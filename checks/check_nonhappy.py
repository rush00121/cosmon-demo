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


def check_save_requires_pass(tmp):
    t = fresh(tmp, "save")
    t.call("open_part", {"name": ANY_PART})
    solve_baseline(t)
    out = t.call("save_part").shaped
    assert out["error_type"] == "SaveRejected" and not (t.outputs_dir / f"{ANY_PART}.json").exists(), out
    return f"save refused without a verified change ({out['error']})"


def check_single_parameter(tmp):
    t = fresh(tmp, "single")
    t.call("open_part", {"name": ANY_PART})
    p1, p2 = list(t.base_params)[:2]
    assert t.call("set_parameter", {"name": p1, "value": t.base_params[p1] * 1.1}).shaped["ok"]
    out = t.call("set_parameter", {"name": p2, "value": t.base_params[p2] * 1.1}).shaped
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
          check_single_parameter, check_report_contradiction, check_injected_faults, check_budget]


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
