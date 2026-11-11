"""Oracle: independently re-solve a saved part in a fresh session and check every claim in it.

Trusts nothing the agent said: re-opens the part from the SDK, re-applies the saved parameters and the
part's *default* load, solves, and compares.  Usage: python -m checks.verify_output outputs/<part>.json
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from agent.constraints import Constraints
from agent.partlab_client import DEFAULT_PARTS, PartLab, make_workdir
from agent.workflow import SF_TARGET, SF_TOL


def verify(saved_path: Path, constraints: Constraints | None = None, parts_dir: Path = DEFAULT_PARTS) -> dict:
    constraints = constraints or Constraints()
    saved = json.loads(Path(saved_path).read_text())
    part, params = saved["name"], saved["parameters"]
    out: dict = {"part": part, "problems": []}
    with tempfile.TemporaryDirectory() as d:
        lab = PartLab(make_workdir(Path(d), parts_dir), faults={})
        opened = lab.run("open-part", part)
        if not opened.ok:
            return {**out, "problems": [f"cannot open part: {opened.error}"]}
        base = lab.run("get-parameters").result
        changed = {k: v for k, v in params.items() if base.get(k) != v}
        load = opened.result["default_load"]
        steps = [("set-parameter", k, v) for k, v in changed.items()] + [
            ("mesh",), ("apply-load", f"--direction={load['direction']}", load["face_id"], load["force_N"]),
            ("solve",)]
        for step in steps:
            env = lab.run(*step)
            if not env.ok:
                return {**out, "problems": [f"{step[0]} failed: {env.error}"]}
        sf = env.result["safety_factor"]
    claimed = (saved.get("solve_result") or {}).get("safety_factor")  # None if saved without a solve
    out.update(resolved_sf=sf, claimed_sf=claimed, changed=changed)
    p = out["problems"]
    if sf < SF_TARGET:
        p.append(f"re-solved SF {sf} < {SF_TARGET}")
    if claimed is None or abs(claimed - sf) > SF_TOL:
        p.append(f"saved SF {claimed} != re-solved {sf}")
    if len(changed) != 1:
        p.append(f"{len(changed)} parameters changed (need exactly 1): {changed}")
    p += [v for k, val in changed.items() if (v := constraints.check(part, k, val))]
    applied = saved.get("applied_load") or {}
    if (applied.get("face_id"), applied.get("force_N")) != (load["face_id"], load["force_N"]):
        p.append(f"saved load {applied} is not the part's default load {load}")
    out["ok"] = not p
    return out


if __name__ == "__main__":
    results = [verify(Path(a)) for a in sys.argv[1:]]
    print(json.dumps(results, indent=2))
    sys.exit(0 if results and all(r.get("ok") for r in results) else 1)
