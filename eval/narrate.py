"""Turn a canonical Run into an engineer-readable story: phases with outcomes, why each call was made, and eval
findings pinned to the step they cite. Used by the per-run Markdown timeline and the Langfuse exporter.

Everything tool-specific is data (the tables below + agent.workflow.ROLE); the code only classifies the
generic situation of a step (baseline vs trial, at a bound, after an error) and fills a template.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent.workflow import ROLE, SENSIBLE_RANGE, SF_TARGET

from .schema import Run, Step

# ---- presentation tables ---------------------------------------------------------------------------------
# How a call is labelled: template over its args; suffixes added generically (error, observed SF, status).
LABEL = {
    "set_parameter": "set_parameter {name}={value}",
    "apply_load": "apply_load {force_N} N {direction} on {face_id}",
    "open_part": "open_part {name}",
    "report_result": "report_result → {status}",
}

# One-line result summary: template over the result dict (missing keys render empty).
BRIEF = {
    "solve": "SF {safety_factor} · {max_stress_MPa} MPa / {yield_MPa} yield · converged={converged}",
    "mesh": "{elements} elements, min quality {min_quality}",
    "open_part": "rev {revision}, latest={is_latest_revision}",
    "recall_lessons": "{n_lessons} lesson(s) {note}",
    "record_lessons": "recorded {recorded} {note}",
    "revert_parameters": "parameters reset, phase → open",
    "set_parameter": "ok, phase → open",
    "save_part": "saved {saved}",
    "report_result": "accepted",
}

# Why a call was needed, by (tool, situation). Situations are generic (see _situation); {placeholders} come
# from the step's args and the workflow state at that point.
INTENT = {
    ("*", "retry"): "retry {tool} after {prev_error}",
    ("recall_lessons", "*"): "load lessons from earlier runs before planning",
    ("open_part", "*"): "load part: default load, current parameters, manufacturing constraints",
    ("mesh", "baseline"): "mesh original geometry for the baseline",
    ("mesh", "trial"): "re-mesh: {param} changed to {value} (a change invalidates the mesh)",
    ("mesh", "reverted"): "re-mesh after revert",
    ("apply_load", "default"): "apply the part's default load {d_force} N on {d_face}",
    ("apply_load", "non_default"): "apply a NON-default load {force_N} N on {face_id} (default {d_force} N on {d_face})",
    ("solve", "baseline"): "measure baseline SF (original parameters)",
    ("solve", "trial"): "measure SF with {param}={value}",
    ("solve", "reverted"): "measure SF after revert",
    ("get_safety_factor", "*"): "re-read SF of the last solve",
    ("get_parameters", "*"): "read current parameter values",
    ("set_parameter", "after_rejection"): "adjust {name} to {value}: the SDK rejected the previous value",
    ("set_parameter", "at_max"): "probe {name} at its constraint max {value} ({rule_why}): can it reach the target?",
    ("set_parameter", "at_min"): "probe {name} at its constraint min {value} ({rule_why}): can it reach the target?",
    ("set_parameter", "at_sensible"): "probe {name} at the {ratio}x sensible bound (no stated limit)",
    ("set_parameter", "refine_pass"): "refine {name}: {prev_value} gave SF {prev_sf} (passes); try smaller change {value}",
    ("set_parameter", "refine_fail"): "refine {name}: {prev_value} gave SF {prev_sf} (short); try {value}",
    ("set_parameter", "first_try"): "try {name} {base_value} → {value}",
    ("revert_parameters", "changed"): "discard {changed} before the next trial",
    ("revert_parameters", "clean"): "restore original parameters",
    ("save_part", "*"): "persist the fix {changed}: last solve SF {last_sf}",
    ("report_result", "resubmit"): "final answer: {status} (resubmitted after rejection)",
    ("report_result", "*"): "final answer: {status}",
    ("record_lessons", "*"): "store reusable lessons for future runs",
}


class _Blank(dict):
    def __missing__(self, key):
        return ""


def _fill(template: str, values: dict) -> str:
    return " ".join(template.format_map(_Blank({k: "" if v is None else v for k, v in values.items()})).split())


@dataclass
class Phase:
    kind: str                       # setup | baseline | trial | save | report | memory
    title: str
    steps: list[Step] = field(default_factory=list)
    outcome: str = ""
    ok: bool = True
    findings: list[dict] = field(default_factory=list)


def rationale_by_step(run: Run) -> dict[int, str]:
    """Agent text emitted before each tool call (Claude Code stream next to the harness trace), paired with
    steps by tool_use id: only calls that got a result count, so a dropped call cannot shift the pairing."""
    stream = Path(run.meta.get("trace_path", "")).with_name("llm.stream.jsonl")
    if not stream.exists():
        return {}
    events = [json.loads(l) for l in stream.read_text().splitlines() if l.strip()]
    answered = {b.get("tool_use_id") for e in events if e.get("type") == "user"
                for b in (e["message"].get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_result"}
    texts, uses = [], []
    for e in events:
        if e.get("type") != "assistant":
            continue
        for b in e["message"].get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                texts.append(b["text"].strip())
            elif b.get("type") == "tool_use" and b.get("id") in answered:
                uses.append(" ".join(texts))
                texts = []
    out = {s.seq: t for s, t in zip(run.steps, uses) if t}
    if texts:
        out[-1] = " ".join(texts)  # final answer after the last tool call
    return out


def _sf(s: Step):
    return s.obs.get("sf")


def _step_label(s: Step) -> str:
    label = _fill(LABEL.get(s.tool, s.tool), s.args) if s.tool in LABEL else s.tool
    if not s.ok:
        return f"{label} ✗ {s.error_type}"
    return f"{label} → SF {_sf(s)}" if _sf(s) is not None and s.tool == "solve" else label


def _result_brief(s: Step) -> str:
    r = s.result if isinstance(s.result, dict) else {}
    if not s.ok:
        return f"✗ {s.error_type}: {str(r.get('error', ''))[:110]}"
    values = {**r, "n_lessons": len(r.get("lessons", [])),
              **{k: round(v, 3) for k, v in r.items() if isinstance(v, float)}}
    return _fill(BRIEF.get(s.tool, "ok"), values)


def _bound_kind(name, value, base_value, rule) -> tuple[str | None, dict]:
    """Is `value` a constraint limit or a sensible-range bound for this parameter?"""
    for kind in ("max", "min"):
        if kind in rule and value == rule[kind]:
            return f"at_{kind}", {"rule_why": rule.get("why", "")}
    if base_value:
        ratio = round(value / base_value, 6)
        if ratio in {round(x, 6) for x in SENSIBLE_RANGE}:
            return "at_sensible", {"ratio": f"{ratio:g}"}
    return None, {}


def derived_intents(run: Run) -> dict[int, str]:
    """Why each call was needed, inferred from workflow state alone (works for traces with no stated intent)."""
    base, d = run.base_params, run.default_load or {}
    cons = run.constraints or {}
    params, change, tried, prev, baseline_done, out = dict(base), None, {}, None, False, {}
    for s in run.steps:
        a = s.args
        ctx = {**a, "tool": s.tool, "d_force": d.get("force_N"), "d_face": d.get("face_id"),
               "param": (change or (None, None))[0], "value": a.get("value", (change or (None, None))[1]),
               "changed": {k: v for k, v in params.items() if base.get(k) != v}}
        stage = "baseline" if not baseline_done else "trial" if change else "reverted"
        situation = "*"
        if prev is not None and not prev.ok and prev.tool == s.tool:
            situation, ctx["prev_error"] = "retry", prev.error_type
        elif s.tool in ("mesh", "solve"):
            situation = stage
        elif s.tool == "apply_load":
            same = (a.get("face_id"), a.get("force_N")) == (d.get("face_id"), d.get("force_N"))
            situation = "default" if same else "non_default"
        elif s.tool == "set_parameter":
            name, value = a.get("name"), a.get("value")
            ctx["base_value"] = base.get(name)
            bound, extra = _bound_kind(name, value, base.get(name), cons.get(name, {}))
            ctx.update(extra)
            if prev is not None and not prev.ok and prev.tool in ("mesh", "set_parameter"):
                situation = "after_rejection"
            elif bound:
                situation = bound
            elif name in tried:
                ctx["prev_value"], ctx["prev_sf"] = tried[name]
                situation = "refine_pass" if (tried[name][1] or 0) >= SF_TARGET else "refine_fail"
            else:
                situation = "first_try"
        elif s.tool == "revert_parameters":
            situation = "changed" if ctx["changed"] else "clean"
        elif s.tool == "save_part":
            ctx["last_sf"] = tried.get(next(iter(ctx["changed"]), None), (None, None))[1]
        elif s.tool == "report_result":
            situation = "resubmit" if prev is not None and prev.tool == s.tool and not prev.ok else "*"
        template = INTENT.get((s.tool, situation)) or INTENT.get(("*", situation)) or INTENT.get((s.tool, "*"), "")
        out[s.seq] = _fill(template, ctx)
        if s.ok:
            if s.tool == "set_parameter":
                params[a.get("name")] = a.get("value")
                change = (a.get("name"), a.get("value"))
            elif ROLE.get(s.tool) == "trial" or s.tool == "open_part":   # revert / reopen clear changes
                params, change = dict(base), None
            elif s.tool == "solve":
                baseline_done = True
                if change:
                    tried[change[0]] = (change[1], _sf(s))
        prev = s
    return out


# ---- phases ------------------------------------------------------------------------------------------------
def phases(run: Run) -> list[Phase]:
    """Group steps by agent.workflow.ROLE: setup -> baseline (work before the first solve) -> trials -> ..."""
    out: list[Phase] = []
    baseline_done = False
    for s in run.steps:
        role = ROLE.get(s.tool, "work")
        cur = out[-1] if out else None
        if role == "work":
            kind = "baseline" if not baseline_done else (cur.kind if cur else "trial")
        elif role == "trial":
            kind = "trial"
        else:
            kind = role
        new_trial = kind == "trial" and role == "trial" and cur is not None and cur.kind == "trial" and (
            s.tool == "revert_parameters" or any(x.tool == "solve" for x in cur.steps))
        if cur is None or cur.kind != kind or new_trial or kind in ("save", "memory"):
            cur = Phase(kind, kind)
            out.append(cur)
        cur.steps.append(s)
        if s.tool == "solve" and s.ok and kind == "baseline":
            baseline_done = True
    for p in out:
        _summarize(p, run)
    return out


PHASE_TITLE = {"setup": "setup", "baseline": "baseline (original parameters, default load)", "save": "save",
               "report": "report", "memory": "record lessons"}


def _summarize(p: Phase, run: Run) -> None:
    sets = [s for s in p.steps if s.tool == "set_parameter" and s.ok] or \
           [s for s in p.steps if s.tool == "set_parameter"]
    solves = [s for s in p.steps if s.tool == "solve" and s.ok]
    errors = [s for s in p.steps if not s.ok]
    p.title = PHASE_TITLE.get(p.kind, p.kind)
    if p.kind == "trial":
        p.title = (f"trial {sets[-1].args.get('name')}: {run.base_params.get(sets[-1].args.get('name'))} → "
                   f"{sets[-1].args.get('value')}") if sets else "revert to original parameters"
    outcome = []
    if solves:
        sf = _sf(solves[-1])
        outcome.append(f"SF {sf} {'✓ meets' if sf is not None and sf >= SF_TARGET else '✗ below'} "
                       f"target ({SF_TARGET})")
    if p.kind == "setup":
        opened = next((s for s in p.steps if s.tool == "open_part" and s.ok), None)
        if opened and isinstance(opened.result, dict):
            r, d = opened.result, opened.result.get("default_load") or {}
            outcome.append(f"default load {d.get('force_N')} N {d.get('direction')} on {d.get('face_id')}; "
                           f"params {r.get('parameters')}; constraints {r.get('manufacturing_constraints')}")
    accepted = [s for s in p.steps if s.ok and s.tool == "report_result"]
    if p.kind == "report":
        rejected = len(p.steps) - len(accepted)
        outcome.append((f"{rejected} report(s) rejected, then " if rejected else "") +
                       (f"accepted: {accepted[-1].args.get('status')}" if accepted else "no report accepted"))
    elif errors:
        outcome.append("errors: " + ", ".join(f"{e.tool} {e.error_type}" for e in errors))
    p.ok = not errors or bool(solves) or bool(accepted)   # an error the phase recovered from is not a failure
    p.outcome = "; ".join(outcome)


def attach_findings(ps: list[Phase], row: dict | None) -> list[dict]:
    """Pin each non-passing check to the phase containing its first evidence step; the rest are run-level."""
    if not row:
        return []
    run_level = []
    for name, c in row["checks"].items():
        if c["status"] not in ("fail", "warn"):
            continue
        f = {"check": name, **c}
        target = next((p for p in ps for s in p.steps if s.seq in set(c["evidence"])), None)
        (target.findings if target else run_level).append(f)
    return run_level


def headline(run: Run, row: dict | None) -> str:
    r = run.report or {}
    parts = [(r.get("status") or "no report").upper()]
    if r.get("status") == "fixed":
        base = run.base_params.get(r.get("parameter"))
        parts.append(f"{r.get('parameter')} {base}→{r.get('new_value')}, SF {r.get('baseline_sf')}→{r.get('final_sf')}")
    elif r:
        parts.append(f"baseline SF {r.get('baseline_sf')}")
    if run.oracle:
        parts.append("oracle re-solve agrees" if run.oracle.get("ok") else
                     "no file to re-solve" if not run.oracle.get("saved_exists") else "ORACLE DISAGREES")
    if row:
        parts.append(f"eval {row['verdict']} ({row['score']})" +
                     (f", failed: {', '.join(row['failed'])}" if row["failed"] else ""))
    return " · ".join(parts)


def markdown_timeline(run: Run, row: dict | None) -> str:
    ps = phases(run)
    run_level = attach_findings(ps, row)
    why, derived = rationale_by_step(run), derived_intents(run)
    usage = run.meta.get("usage") or {}
    cell = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
    L = [f"# {run.part} — `{run.run_id}`", "", f"**{headline(run, row)}**", "",
         f"{len(run.steps)} tool calls · {sum(s.tool == 'solve' for s in run.steps)} solves · "
         f"{usage.get('num_turns')} turns · ${usage.get('total_cost_usd') or 0:.3f} · "
         f"{sum(s.result_bytes for s in run.steps)} B of tool output read by the model", ""]
    if (run.report or {}).get("reason"):
        L += [f"> **Agent's reason:** {run.report['reason']}", ""]
    L += [f"- ⚠ **{f['check']}** ({f['severity']} {f['status']}): {f['message']}" for f in run_level]
    L += ["", "## Phases", ""]
    for n, p in enumerate(ps, 1):
        mark = "✗" if not p.ok or any(f["status"] == "fail" for f in p.findings) else "⚠" if p.findings else "✓"
        L.append(f"{n}. {mark} **{p.title}**" + (f" — {p.outcome}" if p.outcome else ""))
    L += ["", "## Every tool call", "",
          "_why (agent)_ = intent the agent stated with the call; _why (context)_ = derived from workflow state "
          "by the eval, so the two can be cross-checked.", "",
          "| # | phase | call | why (agent) | why (context) | result | eval |", "|---|---|---|---|---|---|---|"]
    for n, p in enumerate(ps, 1):
        flagged = {seq: f for f in p.findings for seq in f["evidence"]}
        for s in p.steps:
            f = flagged.get(s.seq)
            flag = f"⚠ {f['check']} ({f['status']})" if f else ""
            L.append(f"| {s.seq} | {n}. {p.kind} | `{cell(_step_label(s))}` | {cell(s.why or why.get(s.seq, ''))} | "
                     f"{cell(derived.get(s.seq, ''))} | {cell(_result_brief(s))} | {flag} |")
    flagged_all = [f for p in ps for f in p.findings]
    if flagged_all:
        L += ["", "## Eval findings on specific calls", ""]
        L += [f"- **{f['check']}** ({f['severity']} {f['status']}) at steps {f['evidence']}: {f['message']}"
              for f in flagged_all]
    if why.get(-1):
        L += ["", "## Final answer", "", why[-1]]
    return "\n".join(L) + "\n"


def write_timelines(runs_rows: list[tuple[Run, dict | None]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for run, row in runs_rows:
        name = Path(run.meta.get("trace_path", run.run_id)).parent.name or run.run_id
        p = out_dir / f"{name}.md"
        p.write_text(markdown_timeline(run, row))
        paths.append(p)
    return paths
