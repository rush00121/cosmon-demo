"""Brute-force ground truth (no LLM): is any single-parameter change within bounds able to reach SF >= 2.0?

Sweeps each parameter over a grid between its allowed bounds (constraint limits, else the prompt's
0.25x..4x sensible range) through the real CLI. Used to judge `no_fix_possible` claims independently.
Usage: python -m checks.ground_truth <part> [--parts-dir DIR] [--constraints FILE]
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from agent.constraints import DEFAULT_PATH, Constraints
from agent.partlab_client import DEFAULT_PARTS, PartLab, make_workdir
from agent.tools import SF_TARGET

SENSIBLE = (0.25, 4.0)   # must match the range stated in the system prompt
GRID = 9


def candidates(base: float, rule: dict) -> list[float]:
    if rule.get("fixed"):
        return []
    if "allowed" in rule:
        return list(rule["allowed"])
    lo, hi = max(rule.get("min", base * SENSIBLE[0]), 1e-6), rule.get("max", base * SENSIBLE[1])
    return [round(lo + (hi - lo) * i / (GRID - 1), 4) for i in range(GRID)]


def solve_with(lab: PartLab, part: str, overrides: dict) -> float | None:
    opened = lab.run("open-part", part).result  # re-open = clean overrides
    for k, v in overrides.items():
        lab.run("set-parameter", k, v)
    d = opened["default_load"]
    for step in (("mesh",), ("apply-load", f"--direction={d['direction']}", d["face_id"], d["force_N"]), ("solve",)):
        env = lab.run(*step)
        if not env.ok:
            return None
    return env.result["safety_factor"]


def ground_truth(part: str, parts_dir: Path = DEFAULT_PARTS, constraints: Constraints | None = None) -> dict:
    constraints = constraints or Constraints()
    with tempfile.TemporaryDirectory() as d:
        lab = PartLab(make_workdir(Path(d), parts_dir), faults={})
        if not lab.run("open-part", part).ok:
            return {"part": part, "error": "cannot open"}
        base = lab.run("get-parameters").result
        baseline = solve_with(lab, part, {})
        best = {}
        for name, val in base.items():
            grid = candidates(val, constraints.for_part(part).get(name, {}))
            sfs = [(v, solve_with(lab, part, {name: v})) for v in grid]
            sfs = [(v, s) for v, s in sfs if s is not None]
            best[name] = max(sfs, key=lambda x: x[1]) if sfs else None
    feasible = baseline is not None and baseline < SF_TARGET and any(b and b[1] >= SF_TARGET for b in best.values())
    expected = ("no_change_needed" if baseline is not None and baseline >= SF_TARGET
                else "fixed" if feasible else "no_fix_possible")
    return {"part": part, "baseline_sf": baseline, "best_per_param": best, "expected_status": expected}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("part")
    p.add_argument("--parts-dir", type=Path, default=DEFAULT_PARTS)
    p.add_argument("--constraints", type=Path, action="append")
    a = p.parse_args()
    print(json.dumps(ground_truth(a.part, a.parts_dir, Constraints(*(a.constraints or [DEFAULT_PATH]))), indent=2))
