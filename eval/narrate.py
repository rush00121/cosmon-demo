"""Turn a canonical Run into an engineer-readable story: phases with outcomes, the agent's rationale, and eval
findings pinned to the step they point at. Used by the per-run Markdown timeline and the Langfuse exporter.

Phases (derived only from tool order, so any harness works):
  setup (open_part / recall) -> baseline (first mesh..solve) -> trial <param>=<value> (set..solve) ... ->
  save -> report -> memory.  A revert starts a new trial; a failed step ends its phase with the error.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .schema import Run, Step

SF_TARGET = 2.0


@dataclass
class Phase:
    kind: str                       # setup | baseline | trial | save | report | memory
    title: str
    steps: list[Step] = field(default_factory=list)
    outcome: str = ""
    ok: bool = True
    findings: list[dict] = field(default_factory=list)


def rationale_by_step(run: Run) -> dict[int, str]:
    """Agent text emitted right before each tool call, from Claude Code's stream next to the harness trace."""
    stream = Path(run.meta.get("trace_path", "")).with_name("llm.stream.jsonl")
    if not stream.exists():
        return {}
    texts, out, i = [], {}, 0
    tool_steps = [s for s in run.steps]
    for line in stream.read_text().splitlines():
        e = json.loads(line)
        if e.get("type") != "assistant":
            continue
        for b in e["message"].get("content", []):
            if b.get("type") == "text" and b.get("text", "").strip():
                texts.append(b["text"].strip())
            elif b.get("type") == "tool_use":
                if texts and i < len(tool_steps):
                    out[tool_steps[i].seq] = " ".join(texts)
                texts, i = [], i + 1
    if texts and tool_steps:
        out[-1] = " ".join(texts)  # final answer after the last tool call
    return out


def _sf(s: Step):
    return s.obs.get("sf")


def derived_intents(run: Run, sensible=(0.25, 4.0)) -> dict[int, str]:
    """Why each call was needed, inferred from workflow state alone (works for traces with no stated intent)."""
    base, load = run.base_params, run.default_load or {}
    cons = run.constraints or {}
    params, out = dict(base), {}
    last_change, last_sf, prev = None, {}, None   # last_sf: param -> (value, sf) from its latest solve
    baseline_done = False
    for s in run.steps:
        a, why = s.args, ""
        if prev is not None and not prev.ok and prev.tool == s.tool:
            why = f"retry {s.tool} after {prev.error_type}"
        elif s.tool == "recall_lessons":
            why = "load lessons from earlier runs before planning"
        elif s.tool == "open_part":
            why = "load part: default load, current parameters, manufacturing constraints"
        elif s.tool == "mesh":
            why = "mesh original geometry for the baseline" if not baseline_done else \
                f"re-mesh: {last_change[0]} changed to {last_change[1]} (a change invalidates the mesh)" \
                if last_change else "re-mesh after revert"
        elif s.tool == "apply_load":
            same = (a.get("face_id"), a.get("force_N")) == (load.get("face_id"), load.get("force_N"))
            why = f"apply the part's default load {load.get('force_N')} N on {load.get('face_id')}" if same \
                else f"apply a NON-default load {a.get('force_N')} N on {a.get('face_id')} (default {load})"
        elif s.tool == "solve":
            why = "measure baseline SF (original parameters)" if not baseline_done else \
                f"measure SF with {last_change[0]}={last_change[1]}" if last_change else "measure SF"
        elif s.tool == "get_safety_factor":
            why = "re-read SF of the last solve"
        elif s.tool == "set_parameter":
            name, v = a.get("name"), a.get("value")
            rule, b = cons.get(name, {}), base.get(name)
            if prev is not None and prev.tool == "mesh" and not prev.ok:
                why = f"adjust {name} to {v}: mesher rejected the previous value"
            elif "max" in rule and v == rule["max"]:
                why = f"probe {name} at its constraint max {v} ({rule.get('why', '')}): can it reach SF 2.0 at all?"
            elif "min" in rule and v == rule["min"]:
                why = f"probe {name} at its constraint min {v} ({rule.get('why', '')}): can it reach SF 2.0 at all?"
            elif b and v in (round(b * sensible[1], 6), round(b * sensible[0], 6)):
                why = f"probe {name} at the {v / b:g}x sensible bound (no stated limit)"
            elif name in last_sf:
                pv, psf = last_sf[name]
                why = f"refine {name}: {pv} gave SF {psf}; try {v} (smaller change)" if psf >= SF_TARGET \
                    else f"refine {name}: {pv} gave SF {psf} < 2.0; try {v}"
            else:
                why = f"try {name} {b} → {v}"
        elif s.tool == "revert_parameters":
            changed = {k: v for k, v in params.items() if base.get(k) != v}
            why = f"discard {changed} before the next trial" if changed else "restore original parameters"
        elif s.tool == "save_part":
            ch = {k: v for k, v in params.items() if base.get(k) != v}
            why = f"persist the fix {ch}: last solve SF {last_sf.get(next(iter(ch), None), (None, None))[1]}"
        elif s.tool == "report_result":
            resub = prev is not None and prev.tool == "report_result" and not prev.ok
            why = f"final answer: {a.get('status')}" + (" (resubmitted after rejection)" if resub else "")
        elif s.tool == "record_lessons":
            why = "store reusable lessons for future runs"
        out[s.seq] = why
        if s.ok:
            if s.tool == "set_parameter":
                params[a.get("name")] = a.get("value")
                last_change = (a.get("name"), a.get("value"))
            elif s.tool in ("revert_parameters", "open_part"):
                params, last_change = dict(base), None
            elif s.tool == "solve":
                baseline_done = True
                if last_change:
                    last_sf[last_change[0]] = (last_change[1], _sf(s))
        prev = s
    return out


def _result_brief(s: Step) -> str:
    r = s.result if isinstance(s.result, dict) else {}
    if not s.ok:
        return f"✗ {s.error_type}: {str(r.get('error', ''))[:110]}"
    if s.tool == "solve":
        return f"SF {r.get('safety_factor')} · {r.get('max_stress_MPa')} MPa / {r.get('yield_MPa')} yield · " \
               f"converged={r.get('converged')}"
    if s.tool == "mesh":
        return f"{r.get('elements')} elements, min quality {round(r.get('min_quality') or 0, 3)}"
    if s.tool == "open_part":
        return f"rev {r.get('revision')}, latest={r.get('is_latest_revision', r.get('is_latest'))}"
    if s.tool == "recall_lessons":
        return f"{len(r.get('lessons', []))} lesson(s)" + (f" · {r['note']}" if r.get("note") else "")
    if s.tool == "record_lessons":
        return f"recorded {r.get('recorded')}" + (f" · {r['note']}" if r.get("note") else "")
    if s.tool == "revert_parameters":
        return "parameters reset, phase → open"
    if s.tool == "set_parameter":
        return "ok, phase → open"
    if s.tool == "save_part":
        return f"saved {r.get('saved')}"
    if s.tool == "report_result":
        return "accepted"
    return "ok"


def _step_label(s: Step) -> str:
    a = s.args
    if s.tool == "set_parameter":
        label = f"set_parameter {a.get('name')}={a.get('value')}"
    elif s.tool == "apply_load":
        label = f"apply_load {a.get('force_N')} N {a.get('direction', '-Z')} on {a.get('face_id')}"
    elif s.tool == "open_part":
        label = f"open_part {a.get('name')}"
    else:
        label = s.tool
    if not s.ok:
        return f"{label} ✗ {s.error_type}"
    if s.tool == "solve" and _sf(s) is not None:
        return f"{label} → SF {_sf(s)}"
    if s.tool == "report_result":
        return f"{label} → {a.get('status')}"
    return label


def phases(run: Run) -> list[Phase]:
    out: list[Phase] = []
    cur: Phase | None = None
    baseline_done = False

    def start(kind, title):
        nonlocal cur
        cur = Phase(kind, title)
        out.append(cur)

    for s in run.steps:
        if s.tool in ("open_part", "recall_lessons") and not baseline_done:
            if not cur or cur.kind != "setup":
                start("setup", "setup")
        elif s.tool in ("mesh", "apply_load", "solve", "get_safety_factor") and not baseline_done:
            if not cur or cur.kind != "baseline":
                start("baseline", "baseline (original parameters, default load)")
        elif s.tool in ("set_parameter", "revert_parameters"):
            if s.tool == "revert_parameters" or not cur or cur.kind != "trial" or \
                    any(x.tool == "solve" for x in cur.steps):
                start("trial", "trial")
        elif s.tool == "save_part":
            start("save", "save")
        elif s.tool == "report_result":
            if not cur or cur.kind != "report":
                start("report", "report")
        elif s.tool == "record_lessons":
            start("memory", "record lessons")
        elif cur is None:
            start("setup", "setup")
        cur.steps.append(s)
        if s.tool == "solve" and s.ok and cur.kind == "baseline":
            baseline_done = True

    base = run.base_params
    for p in out:
        sets = [s for s in p.steps if s.tool == "set_parameter"]
        solves = [s for s in p.steps if s.tool == "solve" and s.ok]
        errors = [s for s in p.steps if not s.ok]
        if p.kind == "trial":
            if sets:
                last = sets[-1].args
                p.title = f"trial {last.get('name')}: {base.get(last.get('name'))} → {last.get('value')}"
            elif any(s.tool == "revert_parameters" for s in p.steps):
                p.title = "revert to original parameters"
        if solves:
            sf = _sf(solves[-1])
            verdict = "✓ meets target" if sf is not None and sf >= SF_TARGET else "✗ below target"
            p.outcome = f"SF {sf} {verdict} ({SF_TARGET})"
        if errors:
            p.ok = not (errors and not solves)
            p.outcome = (p.outcome + "; " if p.outcome else "") + \
                "errors: " + ", ".join(f"{e.tool} {e.error_type}" for e in errors)
        if p.kind == "save":
            p.outcome = "saved" if all(s.ok for s in p.steps) else p.outcome
        if p.kind == "report":
            accepted = [s for s in p.steps if s.ok]
            rejected = [s for s in p.steps if not s.ok]
            p.outcome = (f"{len(rejected)} report(s) rejected, then " if rejected else "") + \
                (f"accepted: {accepted[-1].args.get('status')}" if accepted else "no report accepted")
        if p.kind == "setup":
            opened = next((s for s in p.steps if s.tool == "open_part" and s.ok), None)
            if opened and isinstance(opened.result, dict):
                load = opened.result.get("default_load") or {}
                cons = opened.result.get("manufacturing_constraints")
                p.outcome = (f"default load {load.get('force_N')} N {load.get('direction')} on {load.get('face_id')}; "
                             f"params {opened.result.get('parameters')}; constraints {cons}")
    return out


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
    status = (r.get("status") or "no report").upper()
    parts = [status]
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
    why = rationale_by_step(run)
    usage = run.meta.get("usage") or {}
    L = [f"# {run.part} — `{run.run_id}`", "", f"**{headline(run, row)}**", "",
         f"{len(run.steps)} tool calls · {sum(s.tool == 'solve' for s in run.steps)} solves · "
         f"{usage.get('num_turns')} turns · ${usage.get('total_cost_usd') or 0:.3f} · "
         f"{sum(s.result_bytes for s in run.steps)} B of tool output read by the model", ""]
    if (run.report or {}).get("reason"):
        L += [f"> **Agent's reason:** {run.report['reason']}", ""]
    for f in run_level:
        L.append(f"- ⚠ **{f['check']}** ({f['severity']} {f['status']}): {f['message']}")
    derived = derived_intents(run)
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
            agent_why = s.why or why.get(s.seq, "")
            f = flagged.get(s.seq)
            ev = f"⚠ {f['check']} ({f['status']})" if f else ""
            cell = lambda x: str(x).replace("|", "\\|").replace("\n", " ")
            L.append(f"| {s.seq} | {n}. {p.kind} | `{cell(_step_label(s))}` | {cell(agent_why)} | "
                     f"{cell(derived.get(s.seq, ''))} | {cell(_result_brief(s))} | {ev} |")
    flagged_all = [f for p in ps for f in p.findings]
    if flagged_all:
        L += ["", "## Eval findings on specific calls", ""]
        L += [f"- **{f['check']}** ({f['severity']} {f['status']}) at steps {f['evidence']}: {f['message']}"
              for f in flagged_all]
    L.append("")
    if why.get(-1):
        L += ["## Final answer", "", why[-1], ""]
    return "\n".join(L)


def write_timelines(runs_rows: list[tuple[Run, dict | None]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for run, row in runs_rows:
        name = Path(run.meta.get("trace_path", run.run_id)).parent.name or run.run_id
        p = out_dir / f"{name}.md"
        p.write_text(markdown_timeline(run, row))
        paths.append(p)
    return paths
