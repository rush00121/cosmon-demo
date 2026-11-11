"""Genericity + robustness stress test: run the real agent on cases nobody hand-tuned for, judge with oracles.

Cases: un-named real parts, synthetic parts (seeded perturbations of real parts under new names, some with
synthetic constraints), injected CLI faults, and a starved solve budget. Each agent outcome is judged against
the brute-force ground truth (status) and the re-solve oracle (saved file), never against hard-coded answers.
Usage: .venv/bin/python -m checks.stress [--seed 7] [--only NAME] [--jobs 3]
"""
from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.constraints import DEFAULT_PATH, Constraints
from agent.partlab_client import DEFAULT_PARTS, ROOT
from agent.run import parse_args, run
from checks.ground_truth import ground_truth

STRESS_DIR = ROOT / "stress"


def make_synthetic(seed: int, n: int) -> tuple[Path, Path, list[str]]:
    """Write a parts dir = real parts + n perturbed, renamed parts; and a constraints file covering some."""
    rng = random.Random(seed)
    parts_dir = STRESS_DIR / f"parts_seed{seed}"
    parts_dir.mkdir(parents=True, exist_ok=True)
    templates = sorted(DEFAULT_PARTS.glob("*.json"))
    for t in templates:
        (parts_dir / t.name).write_text(t.read_text())
    constraints = json.loads(DEFAULT_PATH.read_text())
    names = []
    for i in range(n):
        part = json.loads(rng.choice(templates).read_text())
        name = f"synth{seed}_{i}"
        part.update(name=name, is_latest=True, supersedes=[])
        part["physics"]["K_stress"] = round(part["physics"]["K_stress"] * rng.uniform(0.5, 2.0), 1)
        part["default_load"]["force_N"] = round(part["default_load"]["force_N"] * rng.uniform(0.6, 1.6))
        part["material"]["yield_MPa"] = round(part["material"]["yield_MPa"] * rng.uniform(0.8, 1.3))
        for k in part["parameters"]:
            part["parameters"][k] = round(part["parameters"][k] * rng.uniform(0.7, 1.4), 2)
        if i % 2 == 0:  # every other synthetic part gets a tight made-up constraint
            k = rng.choice(sorted(part["parameters"]))
            base = part["parameters"][k]
            rule = {"max": round(base * 1.3, 2)} if rng.random() < 0.5 else {"min": round(base * 0.8, 2)}
            constraints[name] = {k: {**rule, "why": "synthetic stress constraint"}}
        (parts_dir / f"{name}.json").write_text(json.dumps(part, indent=2))
        names.append(name)
    cpath = STRESS_DIR / f"constraints_seed{seed}.json"
    cpath.write_text(json.dumps(constraints, indent=2))
    return parts_dir, cpath, names


def cases(seed: int) -> list[dict]:
    parts_dir, cpath, synth = make_synthetic(seed, 4)
    common = ["--parts-dir", str(parts_dir), "--constraints", str(cpath)]
    out = [{"name": p, "part": p, "args": common, "kind": "unnamed_real"}
           for p in ("housing_v1", "bracket_v3_thick", "bracket_v1")]
    out += [{"name": s, "part": s, "args": common, "kind": "synthetic"} for s in synth]
    out += [{"name": "fault_mesh_fail", "part": "bracket_v3", "args": common + ["--faults", "mesh:fail"],
             "kind": "fault"},
            {"name": "fault_solve_timeout", "part": "bracket_v3", "args": common + ["--faults", "solve:timeout"],
             "kind": "fault"},
            {"name": "budget_1_solve", "part": "bracket_v3", "args": common + ["--max-solves", "1"],
             "kind": "budget", "expect": "failed"}]
    for c in out:
        c.update(parts_dir=parts_dir, constraints=cpath)
    return out


def run_case(c: dict) -> dict:
    outputs = STRESS_DIR / "outputs" / c["name"]
    summary = run(parse_args(["--part", c["part"], "--outputs-dir", str(outputs), "--tag", f"stress-{c['name']}",
                              *c["args"]]))
    events = [json.loads(l) for l in (ROOT / summary["trace"]).read_text().splitlines()]
    end = next((e for e in reversed(events) if e["type"] == "session_state"), {})
    report = end.get("report") or {}
    truth = ground_truth(c["part"], c["parts_dir"], Constraints(c["constraints"]))
    expected = c.get("expect", truth["expected_status"])
    got = report.get("status", "no_report")
    oracle = summary["oracle"]
    verdict = "PASS"
    if got != expected:
        verdict = "FAIL"
    if got == "fixed" and not oracle.get("ok"):
        verdict = "FAIL"
    return {"case": c["name"], "kind": c["kind"], "expected": expected, "got": got,
            "baseline_sf": report.get("baseline_sf"), "final_sf": report.get("final_sf"),
            "change": f"{report.get('parameter')}={report.get('new_value')}" if got == "fixed" else "",
            "oracle_ok": oracle.get("ok"), "solves": len(end.get("solves", [])), "calls": end.get("calls"),
            "rejections": sum(e["type"] == "guard_reject" for e in events),
            "self_corrections": sum(e["type"] == "self_correction_requested" for e in events),
            "verdict": verdict, "trace": summary["trace"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=3)
    a = ap.parse_args()
    todo = [c for c in cases(a.seed) if not a.only or a.only in c["name"]]
    with ThreadPoolExecutor(a.jobs) as pool:
        rows = list(pool.map(run_case, todo))
    STRESS_DIR.mkdir(exist_ok=True)
    report = STRESS_DIR / f"report_seed{a.seed}.json"
    if a.only and report.exists():  # partial rerun: replace only the rerun cases
        fresh = {r["case"] for r in rows}
        rows = [r for r in json.loads(report.read_text()) if r["case"] not in fresh] + rows
    report.write_text(json.dumps(rows, indent=2))
    cols = ["case", "kind", "expected", "got", "baseline_sf", "final_sf", "change", "oracle_ok", "solves",
            "rejections", "self_corrections", "verdict"]
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join(str(r[c]) for c in cols))
    print(f"\n{sum(r['verdict'] == 'PASS' for r in rows)}/{len(rows)} PASS")


if __name__ == "__main__":
    main()
