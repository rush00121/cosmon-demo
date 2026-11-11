"""Eval harness: raw traces -> canonical runs -> checks -> scored JSON + Markdown report.

Usage: .venv/bin/python -m eval.run_eval traces/ [more paths] [--adapter claude_stream] [--ground-truth]
       [--config eval/config.toml] [--out eval/out]
A path may be a trace file, a run dir (harness.jsonl preferred, else llm.stream.jsonl) or a dir of run dirs.
"""
from __future__ import annotations

import argparse
import json
import tomllib
from collections import defaultdict
from pathlib import Path

from agent.partlab_client import DEFAULT_PARTS, ROOT

from . import checks as C
from .adapters import load
from .schema import Run

TRACE_NAMES = {"partlab_harness": "harness.jsonl", "claude_stream": "llm.stream.jsonl"}


def find_traces(paths: list[Path], adapter: str | None) -> list[Path]:
    names = [TRACE_NAMES[adapter]] if adapter else list(TRACE_NAMES.values())
    out = []
    for p in paths:
        if p.is_file():
            out.append(p)
            continue
        dirs = [p] if any((p / n).exists() for n in names) else sorted(d for d in p.iterdir() if d.is_dir())
        for d in dirs:
            hit = next((d / n for n in names if (d / n).exists()), None)
            if hit:
                out.append(hit)
    return out


def load_config(path: Path) -> dict:
    cfg = tomllib.loads(path.read_text())
    cfg["_constraint_paths"] = [ROOT / p for p in cfg.get("constraint_files", [])]
    return cfg


def evaluate(run: Run, cfg: dict) -> dict:
    ctx = C.make_ctx(run, cfg)
    weights = cfg["severity_weights"]
    results, earned, possible = {}, 0.0, 0.0
    for name, spec in cfg["checks"].items():
        if spec.get("enabled", True) is False or name not in C.REGISTRY:
            continue
        try:
            r = C.REGISTRY[name](run, ctx)
        except Exception as e:  # a broken check must not hide the other results
            r = C.Result(C.FAIL, f"check crashed: {type(e).__name__}: {e}")
        sev = spec.get("severity", "minor")
        results[name] = {"status": r.status, "severity": sev, "message": r.message, "evidence": r.evidence}
        if r.status != C.NA:
            w = weights[sev]
            possible += w
            earned += w * {C.PASS: 1.0, C.WARN: 0.5, C.FAIL: 0.0}[r.status]
    critical_fail = [n for n, r in results.items() if r["status"] == C.FAIL and r["severity"] == "critical"]
    any_issue = [n for n, r in results.items() if r["status"] in (C.FAIL, C.WARN)]
    verdict = "FAIL" if critical_fail else "WARN" if any_issue else "PASS"
    return {"run_id": run.run_id, "harness": run.harness, "part": run.part,
            "status": (run.report or {}).get("status"), "verdict": verdict,
            "score": round(earned / possible, 3) if possible else None, "failed": critical_fail,
            "issues": any_issue, "checks": results,
            "meta": {k: run.meta.get(k) for k in ("model", "memory", "guards", "faults", "system_prompt_sha",
                                                  "usage", "trace_path")}}


def aggregate(rows: list[dict]) -> dict:
    per_check = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for name, c in r["checks"].items():
            per_check[name][c["status"]] += 1
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        for key in ("part", "harness"):
            by[key][str(r[key])].append(r["verdict"] == "PASS")
        by["prompt"][str(r["meta"]["system_prompt_sha"])].append(r["verdict"] == "PASS")
    return {"runs": len(rows), "verdicts": {v: sum(r["verdict"] == v for r in rows) for v in ("PASS", "WARN", "FAIL")},
            "per_check": {k: dict(v) for k, v in per_check.items()},
            "pass_rate_by": {k: {g: f"{sum(v)}/{len(v)}" for g, v in groups.items()} for k, groups in by.items()}}


def markdown(rows: list[dict], agg: dict) -> str:
    L = ["# PartLab agent eval report", "",
         f"**{agg['runs']} runs** — PASS {agg['verdicts']['PASS']}, WARN {agg['verdicts']['WARN']}, "
         f"FAIL {agg['verdicts']['FAIL']}. Any failed *critical* check fails a run; score = severity-weighted "
         "pass fraction (warn = half).", "", "## Runs", "",
         "| run | part | status | verdict | score | failed / issues |", "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["verdict"] != "FAIL", r["score"] or 0)):
        issues = ", ".join(f"**{i}**" if i in r["failed"] else i for i in r["issues"]) or "—"
        L.append(f"| `{r['run_id']}` | {r['part']} | {r['status']} | {r['verdict']} | {r['score']} | {issues} |")
    L += ["", "## Check pass rates", "", "| check | pass | warn | fail | n/a |", "|---|---|---|---|---|"]
    for name, c in agg["per_check"].items():
        L.append(f"| {name} | {c.get('pass', 0)} | {c.get('warn', 0)} | {c.get('fail', 0)} | {c.get('na', 0)} |")
    L += ["", "## Findings (what to look at)", ""]
    for r in rows:
        for name in r["issues"]:
            c = r["checks"][name]
            ev = f" — steps {c['evidence']}" if c["evidence"] else ""
            L.append(f"- `{r['run_id']}` **{name}** ({c['severity']}, {c['status']}): {c['message']}{ev}")
    if not any(r["issues"] for r in rows):
        L.append("- none")
    L += ["", "## Pass rate by group", ""]
    for key, groups in agg["pass_rate_by"].items():
        L.append(f"- **{key}**: " + ", ".join(f"{g}: {v}" for g, v in groups.items()))
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--adapter", choices=list(TRACE_NAMES))
    ap.add_argument("--config", type=Path, default=Path(__file__).with_name("config.toml"))
    ap.add_argument("--ground-truth", action="store_true", help="brute-force expected status per part (slow)")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    runs = [load(p, a.adapter) for p in find_traces(a.paths, a.adapter)]
    if a.ground_truth:
        from agent.constraints import Constraints
        from checks.ground_truth import ground_truth
        cfg["_ground_truth"] = {}
        for run in runs:
            if run.part and run.part not in cfg["_ground_truth"]:
                parts = Path(run.meta.get("parts_dir") or DEFAULT_PARTS)
                srcs = run.meta.get("constraint_sources") or cfg["_constraint_paths"]
                cfg["_ground_truth"][run.part] = ground_truth(run.part, parts, Constraints(*map(Path, srcs)))
    rows = [evaluate(r, cfg) for r in runs]
    agg = aggregate(rows)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.json").write_text(json.dumps({"summary": agg, "runs": rows}, indent=2))
    (a.out / "report.md").write_text(markdown(rows, agg))
    print(markdown(rows, agg))
    return 1 if agg["verdicts"]["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
