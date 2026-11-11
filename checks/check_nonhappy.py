"""Deterministic, no-LLM checks of non-happy paths through the real CLI + tool layer.

Each check runs in its own isolated workdir and prints PROVED/FAILED with what it demonstrates.
Exit code 0 iff all pass.  Usage: .venv/bin/python -m checks.check_nonhappy
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from agent.constraints import Constraints
from agent.partlab_client import PartLab, make_workdir
from agent.tools import PartLabTools

CONSTRAINED_PART = "flange_v1"   # any part with an entry in constraints.json works
ANY_PART = "bracket_v3"


def fresh(tmp: Path, name: str, faults=None, **kw) -> PartLabTools:
    wd = make_workdir(tmp / name)
    return PartLabTools(PartLab(wd, faults=faults or {}), Constraints(), outputs_dir=wd / "outputs", **kw)


def solve_baseline(t: PartLabTools) -> None:
    load = t.default_load
    for name, args in [("mesh", {}), ("apply_load", {"face_id": load["face_id"], "force_N": load["force_N"],
                                                     "direction": load["direction"]}), ("solve", {})]:
        assert t.call(name, args).shaped["ok"], name


def check_constraint_guard(tmp):
    t = fresh(tmp, "constraint")
    t.call("open_part", {"name": CONSTRAINED_PART})
    name, rule = next(iter(t.constraints.for_part(CONSTRAINED_PART).items()))
    bad = rule["max"] + 1 if "max" in rule else rule["min"] - 1
    out = t.call("set_parameter", {"name": name, "value": bad}).shaped
    sdk = t.lab.run("get-active-part").result["parameter_overrides"]
    assert out["error_type"] == "ConstraintViolation" and not sdk, (out, sdk)
    return f"{name}={bad} rejected before reaching the SDK (SDK overrides still {sdk})"


def check_invalid_transition(tmp):
    t = fresh(tmp, "transition")
    t.call("open_part", {"name": ANY_PART})
    out = t.call("solve").shaped
    assert out["error_type"] == "StateError" and out["hint"], out
    return "solve before mesh -> StateError surfaced to the agent with a workflow hint"


def check_usage_error(tmp):
    t = fresh(tmp, "usage")
    env = t.lab.run("open-part", ANY_PART, "extra-arg")
    env2 = t.lab.run("not-a-command")
    assert not env.ok and not env2.ok and env2.error_type == "UsageError", (env, env2)
    return f"bad CLI usage becomes a structured envelope ({env.error_type}, {env2.error_type}), never a crash"


def tiny_change(t: PartLabTools, name: str) -> float:
    """A 1% nudge that stays inside any constraint (so only the rule under test can fire)."""
    for v in (t.base_params[name] * 1.01, t.base_params[name] * 0.99):
        if not t.constraints.check(t.part, name, v):
            return v
    raise AssertionError(f"no in-constraint nudge for {name}")


def check_save_requires_pass(tmp):
    t = fresh(tmp, "save")
    t.call("open_part", {"name": ANY_PART})
    solve_baseline(t)
    if t.solves[-1]["sf"] >= 2.0:
        return f"SKIPPED: {ANY_PART} already passes; pick a failing part for this check"
    name = next(iter(t.base_params))
    t.call("set_parameter", {"name": name, "value": tiny_change(t, name)})
    solve_baseline(t)
    assert t.solves[-1]["sf"] < 2.0, "nudge unexpectedly fixed the part"
    out = t.call("save_part").shaped
    assert out["error_type"] == "SaveRejected" and "SF" in out["error"], out
    assert not (t.outputs_dir / f"{ANY_PART}.json").exists()
    return f"single-param change with SF still < 2 cannot be saved ({out['error']})"


def check_wrong_part(tmp):
    t = fresh(tmp, "wrongpart", assigned_part=ANY_PART)
    out = t.call("open_part", {"name": ANY_PART + "_other"}).shaped
    assert out["error_type"] == "WrongPart" and t.part is None, out
    return "opening a different part than assigned is refused (no part-swap 'fixes')"


def check_non_default_load(tmp):
    t = fresh(tmp, "load")
    t.call("open_part", {"name": ANY_PART})
    d = t.default_load
    t.call("mesh")
    t.call("apply_load", {"face_id": d["face_id"], "force_N": d["force_N"] / 10, "direction": d["direction"]})
    t.call("solve")
    sf = t.solves[-1]["sf"]
    rep = t.check_report({"status": "no_change_needed", "baseline_sf": sf, "final_sf": sf, "reason": "x"})
    assert any("baseline" in p for p in rep), rep
    return f"a solve under a reduced load (SF {sf}) is not accepted as the baseline"


def check_single_parameter(tmp):
    t = fresh(tmp, "single")
    t.call("open_part", {"name": ANY_PART})
    if len(t.base_params) < 2:
        return "SKIPPED: part has fewer than two parameters"
    p1, p2 = list(t.base_params)[:2]
    assert t.call("set_parameter", {"name": p1, "value": tiny_change(t, p1)}).shaped["ok"]
    out = t.call("set_parameter", {"name": p2, "value": tiny_change(t, p2)}).shaped
    reverted = t.call("revert_parameters").shaped
    sdk = t.lab.run("get-active-part").result["parameter_overrides"]
    assert out["error_type"] == "MultiParameterChange" and reverted["ok"] and not sdk, (out, sdk)
    return "second parameter blocked; revert_parameters really clears SDK overrides"


def check_report_contradiction(tmp):
    t = fresh(tmp, "report")
    t.call("open_part", {"name": ANY_PART})
    solve_baseline(t)
    real = t.solves[-1]["sf"]
    out = t.call("report_result", {"status": "fixed", "baseline_sf": real, "final_sf": real + 1,
                                   "parameter": "thickness_mm", "new_value": 9, "reason": "x"}).shaped
    assert out["error_type"] == "ReportRejected" and t.report is None, out
    return f"fabricated report rejected live -> agent must self-correct ({out['error'][:80]}...)"


def check_injected_faults(tmp):
    t = fresh(tmp, "faults", faults={"mesh": "fail", "solve": "garbage"})
    t.call("open_part", {"name": ANY_PART})
    first = t.call("mesh").shaped
    retry = t.call("mesh").shaped
    load = t.default_load
    t.call("apply_load", {"face_id": load["face_id"], "force_N": load["force_N"]})
    bad = t.call("solve").shaped
    good = t.call("solve").shaped
    assert first["error_type"] == "MeshError" and retry["ok"] and bad["error_type"] == "UsageError" and good["ok"]
    return "injected mesh failure and garbage solve output are recoverable by a plain retry"


def check_budget(tmp):
    t = fresh(tmp, "budget", max_solves=1)
    t.call("open_part", {"name": ANY_PART})
    solve_baseline(t)
    out = t.call("solve").shaped
    final = t.call("report_result", {"status": "failed", "baseline_sf": None, "final_sf": None,
                                     "reason": "budget"}).shaped
    assert out["error_type"] == "BudgetExceeded" and final["ok"], (out, final)
    return "solve budget enforced; a graceful 'failed' report is still accepted"


CHECKS = [check_constraint_guard, check_invalid_transition, check_usage_error, check_save_requires_pass,
          check_wrong_part, check_non_default_load, check_single_parameter, check_report_contradiction,
          check_injected_faults, check_budget]


def main() -> int:
    failed = 0
    with tempfile.TemporaryDirectory() as d:
        for check in CHECKS:
            try:
                print(f"PROVED  {check.__name__}: {check(Path(d))}")
            except Exception as e:  # report every check, don't stop at the first
                failed += 1
                print(f"FAILED  {check.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
