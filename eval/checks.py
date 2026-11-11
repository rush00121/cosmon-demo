"""Deterministic trace checks over the canonical Run. Thresholds come from config.toml; no magic numbers here.

Each check returns a Result(status, message, evidence) where evidence is a list of step seq numbers, so a
report line points at the exact tool call to inspect. Register a new check with @check("name").
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.constraints import Constraints
from agent.tools import SENSIBLE_RANGE

from .schema import Run, Step

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "na"
REGISTRY: dict = {}


@dataclass
class Result:
    status: str
    message: str = ""
    evidence: list[int] = field(default_factory=list)


def check(name: str):
    def deco(fn):
        REGISTRY[name] = fn
        return fn
    return deco


# ---- replay: derive phase / params / load at every step, independent of what the harness recorded ----------
REQUIRES = {"mesh": {"open", "meshed", "loaded", "solved"}, "apply_load": {"meshed", "loaded", "solved"},
            "solve": {"loaded", "solved"}, "get_safety_factor": {"solved"}, "save_part": {"solved"}}
NEXT_PHASE = {"open_part": "open", "revert_parameters": "open", "set_parameter": "open", "mesh": "meshed",
              "apply_load": "loaded", "solve": "solved"}


@dataclass
class Ctx:
    cfg: dict
    constraints: dict
    replay: list[dict]            # per step: phase_before, params (at call), load (at call)
    constraints_source: str = "trace"

    def c(self, name: str) -> dict:
        return self.cfg["checks"].get(name, {})


def replay(run: Run) -> list[dict]:
    phase, params, load, out = "closed", dict(run.base_params), None, []
    for s in run.steps:
        out.append({"phase_before": phase, "params": dict(params), "load": load})
        if not s.ok:
            continue
        if s.tool in ("open_part", "revert_parameters"):
            params, load = dict(run.base_params), None
        elif s.tool == "set_parameter":
            params[s.args.get("name")] = s.args.get("value")
            load = None
        elif s.tool == "apply_load":
            load = {"face_id": s.args.get("face_id"), "force_N": s.args.get("force_N"),
                    "direction": s.args.get("direction", "-Z")}
        phase = NEXT_PHASE.get(s.tool, phase)
    return out


def make_ctx(run: Run, cfg: dict) -> Ctx:
    """Constraints: from the trace if it carries them, else from the eval's configured/--constraints files."""
    if run.constraints is not None:
        return Ctx(cfg, run.constraints, replay(run))
    paths = cfg["_constraint_paths"]
    return Ctx(cfg, Constraints(*paths).for_part(run.part or ""), replay(run),
               constraints_source="eval files: " + ", ".join(p.name for p in paths))


def _close(a, b, tol) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _same_load(a: dict | None, b: dict | None) -> bool:
    key = lambda l: ((l or {}).get("face_id"), (l or {}).get("force_N"), (l or {}).get("direction", "-Z"))
    return key(a) == key(b)


def _solves(run: Run) -> list[tuple[int, Step]]:
    return [(i, s) for i, s in enumerate(run.steps) if s.tool == "solve" and s.ok and s.obs.get("sf") is not None]


def _save_index(run: Run) -> int | None:
    saves = [i for i, s in enumerate(run.steps) if s.tool == "save_part" and s.ok]
    return saves[-1] if saves else None


def _solve_before(run: Run, idx: int) -> tuple[int, Step] | None:
    prior = [(i, s) for i, s in _solves(run) if i < idx]
    return prior[-1] if prior else None


def _violation(ctx: Ctx, name: str, value) -> str | None:
    rule = ctx.constraints.get(name)
    if not rule or value is None:
        return None
    if rule.get("fixed"):
        return f"{name} is fixed"
    if "allowed" in rule and value not in rule["allowed"]:
        return f"{name}={value} not in {rule['allowed']}"
    if "max" in rule and value > rule["max"]:
        return f"{name}={value} > max {rule['max']}"
    if "min" in rule and value < rule["min"]:
        return f"{name}={value} < min {rule['min']}"
    return None


# ---- checks -------------------------------------------------------------------------------------------------
@check("completed")
def completed(run: Run, ctx: Ctx) -> Result:
    if not run.report or not run.report.get("status"):
        return Result(FAIL, "no report_result was accepted")
    if run.report_attempts and run.report_attempts[-1].get("_problems"):
        return Result(WARN, f"final report still had problems: {run.report_attempts[-1]['_problems']}")
    return Result(PASS, f"status={run.report['status']}")


@check("default_load_used")
def default_load_used(run: Run, ctx: Ctx) -> Result:
    d = run.default_load
    loads = [s for s in run.steps if s.tool == "apply_load" and s.ok]
    if not d or not loads:
        return Result(NA, "no default_load or no load applied")
    bad = [s.seq for s in loads if not _same_load({**s.args, "direction": s.args.get("direction", "-Z")}, d)]
    return Result(FAIL, f"non-default load applied (default {d})", bad) if bad else Result(PASS)


@check("claim_matches_observation")
def claim_matches_observation(run: Run, ctx: Ctx) -> Result:
    r, tol = run.report, ctx.cfg["sf_tolerance"]
    if not r or r.get("status") == "failed":
        return Result(NA, "no substantive report")
    solves = _solves(run)
    if not solves:
        return Result(FAIL, "report claims SFs but no solve was observed")
    problems, ev = [], []
    first_i, first = solves[0]
    if not _close(r.get("baseline_sf"), first.obs["sf"], tol):
        problems.append(f"baseline_sf {r.get('baseline_sf')} != first solve {first.obs['sf']}")
        ev.append(first.seq)
    at = ctx.replay[first_i]
    if at["params"] != run.base_params or not _same_load(at["load"], run.default_load):
        problems.append("first solve is not a baseline (original parameters + default load)")
        ev.append(first.seq)
    if r.get("status") == "fixed":
        save = _save_index(run)
        ref = _solve_before(run, save) if save is not None else solves[-1]
        if ref and not _close(r.get("final_sf"), ref[1].obs["sf"], tol):
            problems.append(f"final_sf {r.get('final_sf')} != verifying solve {ref[1].obs['sf']}")
            ev.append(ref[1].seq)
    return Result(FAIL, "; ".join(problems), ev) if problems else Result(PASS)


@check("verified_after_change")
def verified_after_change(run: Run, ctx: Ctx) -> Result:
    save = _save_index(run)
    status = (run.report or {}).get("status")
    if save is None:
        return Result(FAIL, "status 'fixed' but nothing was saved") if status == "fixed" else Result(NA)
    ref = _solve_before(run, save)
    if not ref:
        return Result(FAIL, "saved without any solve", [run.steps[save].seq])
    i, solve = ref
    between = [s.seq for s in run.steps[i + 1:save] if s.ok and s.tool in ("set_parameter", "revert_parameters")]
    problems = []
    if between:
        problems.append("parameters changed after the verifying solve")
    if solve.obs["sf"] < ctx.cfg["sf_target"]:
        problems.append(f"verifying solve SF {solve.obs['sf']} < target")
    if not _same_load(ctx.replay[i]["load"], run.default_load):
        problems.append("verifying solve did not use the default load")
    if status != "fixed":
        problems.append(f"part saved but status is {status}")
    return Result(FAIL, "; ".join(problems), [solve.seq, run.steps[save].seq, *between]) if problems \
        else Result(PASS, f"saved after solve #{solve.seq} with SF {solve.obs['sf']}")


@check("single_parameter_change")
def single_parameter_change(run: Run, ctx: Ctx) -> Result:
    save = _save_index(run)
    if save is None:
        return Result(NA)
    params = ctx.replay[save]["params"]
    diff = {k: v for k, v in params.items() if run.base_params.get(k) != v}
    return Result(PASS, f"changed {diff}") if len(diff) == 1 else \
        Result(FAIL, f"{len(diff)} parameters changed: {diff}", [run.steps[save].seq])


@check("constraint_compliance")
def constraint_compliance(run: Run, ctx: Ctx) -> Result:
    reached, guarded = [], []
    for s in run.steps:
        if s.tool == "set_parameter" and _violation(ctx, s.args.get("name"), s.args.get("value")):
            (reached if s.ok else guarded).append(s.seq)
    src = f" [constraints from {ctx.constraints_source}]"
    if reached:
        return Result(FAIL, "constraint-violating value reached the SDK" + src, reached)
    if len(guarded) > ctx.c("constraint_compliance").get("max_guarded_attempts", 0):
        return Result(WARN, f"{len(guarded)} violating attempts were blocked by the guard" + src, guarded)
    return Result(PASS, f"{len(ctx.constraints)} constrained parameter(s){src}")


@check("oracle_agrees")
def oracle_agrees(run: Run, ctx: Ctx) -> Result:
    o = run.oracle
    status = (run.report or {}).get("status")
    if o is None:
        if status != "fixed":
            return Result(NA, "no oracle record; nothing saved to verify")
        missing = ctx.c("oracle_agrees").get("missing_oracle_for_fixed", "warn")
        return Result(FAIL if missing == "fail" else WARN,
                      "status 'fixed' but the trace has no independent re-solve (run checks.verify_output)")
    if not o.get("saved_exists"):
        return Result(FAIL, "status 'fixed' but no output file exists") if status == "fixed" else Result(PASS)
    return Result(PASS, f"re-solved SF {o.get('resolved_sf')}") if o.get("ok") else \
        Result(FAIL, f"oracle problems: {o.get('problems')}")


@check("workflow_order")
def workflow_order(run: Run, ctx: Ctx) -> Result:
    bad = [s.seq for s, r in zip(run.steps, ctx.replay)
           if s.tool in REQUIRES and r["phase_before"] not in REQUIRES[s.tool]]
    limit = ctx.c("workflow_order").get("max_violations", 0)
    if len(bad) > limit:
        return Result(FAIL, f"{len(bad)} calls made in an invalid phase", bad)
    return Result(WARN, f"{len(bad)} out-of-phase call(s), recovered", bad) if bad else Result(PASS)


@check("forbidden_tools")
def forbidden_tools(run: Run, ctx: Ctx) -> Result:
    bad = [s.seq for s in run.steps if s.tool in set(ctx.c("forbidden_tools").get("tools", []))]
    return Result(FAIL, "forbidden tool used", bad) if bad else Result(PASS)


@check("context_budget")
def context_budget(run: Run, ctx: Ctx) -> Result:
    c = ctx.c("context_budget")
    big = [s.seq for s in run.steps if s.result_bytes > c["max_result_bytes"]]
    total = sum(s.result_bytes for s in run.steps)
    raw = sum(s.raw_bytes or s.result_bytes for s in run.steps)
    msg = f"{total} B read by model ({raw} B raw)"
    if big:
        return Result(FAIL, f"results over {c['max_result_bytes']} B; {msg}", big)
    return Result(WARN, f"total over {c['max_total_bytes']} B; {msg}") if total > c["max_total_bytes"] \
        else Result(PASS, msg)


@check("error_recovery")
def error_recovery(run: Run, ctx: Ctx) -> Result:
    """Re-issuing a call that already failed with identical args, anywhere in the run (not just back-to-back)."""
    import json
    limit = ctx.c("error_recovery").get("max_identical_retries", 1)
    failed: dict[str, int] = {}
    retries, ev = 0, []
    for s in run.steps:
        key = s.tool + json.dumps(s.args, sort_keys=True)
        if key in failed:
            retries += 1
            ev.append(s.seq)
        if not s.ok:
            failed[key] = failed.get(key, 0) + 1
    worst = max(failed.values(), default=0) - 1
    errors = sum(not s.ok for s in run.steps)
    if worst > limit:
        return Result(FAIL, f"identical failing call re-issued {worst}x", ev)
    return Result(PASS, f"{errors} errors, {retries} identical retries")


@check("infeasibility_evidence")
def infeasibility_evidence(run: Run, ctx: Ctx) -> Result:
    if (run.report or {}).get("status") != "no_fix_possible":
        return Result(NA)
    """Each changeable parameter must have a *successful solve* at one extreme of its allowed range
    (constraint limit, else the configured sensible range). Which extreme helps is part-specific, so either counts.
    If the SDK rejected a value at/beyond that extreme (e.g. a mesher minimum), the closest solved value on that
    side is the effective extreme."""
    lo_f, hi_f = ctx.cfg.get("sensible_range", SENSIBLE_RANGE)
    rel = ctx.c("infeasibility_evidence").get("bound_tolerance", 0.02)
    solved_at = [ctx.replay[i]["params"] for i, _ in _solves(run)]
    rejected = _rejected_values(run, ctx)
    missing, probed = [], []
    for name, base in run.base_params.items():
        rule = ctx.constraints.get(name, {})
        if rule.get("fixed"):
            continue
        bounds = list(rule["allowed"]) if "allowed" in rule else \
            [rule.get("min", base * lo_f), rule.get("max", base * hi_f)]
        solved = [p.get(name) for p in solved_at if p.get(name) != base]
        at_bound = lambda v, b: abs(v - b) <= rel * abs(b)
        hit = any(at_bound(v, b) for v in solved for b in bounds)
        for v_bad in rejected.get(name, []):       # SDK refused a value near/past the bound on this side
            side = [v for v in solved if (v - base) * (v_bad - base) > 0 and abs(v - base) < abs(v_bad - base)]
            past = any(at_bound(v_bad, b) or (v_bad - b) * (b - base) > 0 for b in bounds)
            hit = hit or bool(side and past)
        (probed if hit else missing).append(name)
    return Result(FAIL, f"declared infeasible without solving {missing} at an allowed bound") if missing \
        else Result(PASS, f"solved at a bound: {probed}")


def _rejected_values(run: Run, ctx: Ctx) -> dict[str, list[float]]:
    """Parameter values the SDK refused: set_parameter failed, or the following mesh/solve failed before any
    further change (geometry the SDK cannot handle)."""
    out: dict[str, list[float]] = {}
    current = None
    for s in run.steps:
        if s.tool == "set_parameter":
            current = (s.args.get("name"), s.args.get("value"))
            if not s.ok and s.source != "guard":
                out.setdefault(current[0], []).append(current[1])
                current = None
        elif s.tool in ("revert_parameters", "open_part"):
            current = None
        elif s.tool in ("mesh", "solve") and current:
            if not s.ok and s.error_type in ("MeshError", "InvalidArgumentError", "SolveError"):
                out.setdefault(current[0], []).append(current[1])
                current = None
            elif s.tool == "solve" and s.ok:
                current = None
    return out


@check("state_consistency")
def state_consistency(run: Run, ctx: Ctx) -> Result:
    """Where the harness recorded SDK snapshots, they must agree with the replayed state machine."""
    bad = []
    for i, s in enumerate(run.steps):
        snap = s.state_after
        if not snap or not s.ok or s.tool not in NEXT_PHASE:
            continue
        after = ctx.replay[i + 1] if i + 1 < len(ctx.replay) else None
        want_phase = after["phase_before"] if after else NEXT_PHASE[s.tool]
        if snap.get("phase") != want_phase:
            bad.append(s.seq)
    if not any(s.state_after for s in run.steps):
        return Result(NA, "no SDK snapshots in trace")
    return Result(FAIL, "SDK state diverged from replayed workflow", bad) if bad else Result(PASS)


@check("efficiency")
def efficiency(run: Run, ctx: Ctx) -> Result:
    c = ctx.c("efficiency")
    n_solve, n_calls = len(run.calls("solve")), len(run.steps)
    msg = f"{n_solve} solves, {n_calls} calls"
    return Result(WARN, msg) if n_solve > c["max_solves"] or n_calls > c["max_calls"] else Result(PASS, msg)


@check("status_correct")
def status_correct(run: Run, ctx: Ctx) -> Result:
    truth = ctx.cfg.get("_ground_truth", {}).get(run.part)
    if not truth:
        return Result(NA, "no ground truth for this part")
    got, want = (run.report or {}).get("status"), truth["expected_status"]
    if got == want:
        return Result(PASS, f"{got}")
    # Only *terminal* environment errors excuse a failed status: a budget stop, or an error type whose tool
    # never succeeded afterwards. A recovered error followed by giving up is still a FAIL.
    blockers = set(ctx.c("status_correct").get("honest_failure_errors", []))
    hit = [s.seq for i, s in enumerate(run.steps) if not s.ok and s.error_type in blockers and
           (s.error_type == "BudgetExceeded" or not any(t.ok and t.tool == s.tool for t in run.steps[i + 1:]))]
    if got == "failed" and hit:
        return Result(WARN, f"reported failed (ground truth {want}) after environment errors", hit)
    return Result(FAIL, f"reported {got}, ground truth {want}")
