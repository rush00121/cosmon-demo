"""Scale test. Tier 1 (infra, no LLM): N synthetic parts through the scripted agent in parallel processes, then
the full eval (with parallel ground truth) over every trace. Tier 2 (LLM): M real agents at K-way concurrency,
recording latency, cost and subscription rate-limit utilisation from the Claude Code stream.

Usage: .venv/bin/python -m checks.scale infra --parts 1000 --jobs 8
       .venv/bin/python -m checks.scale llm --parts 30 --jobs 10
Writes stress/scale/<tier>_summary.json (traces under stress/scale/<tier>/, git-ignored).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

from agent.partlab_client import ROOT
from checks.stress import make_synthetic

OUT = ROOT / "stress" / "scale"


def _pct(xs: list[float], q: float) -> float | None:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2) if xs else None


def _latency(xs: list[float]) -> dict:
    return {"p50": _pct(xs, 0.5), "p95": _pct(xs, 0.95), "max": round(max(xs), 2) if xs else None,
            "mean": round(statistics.mean(xs), 2) if xs else None}


def _scripted(args) -> dict:
    from checks.scripted_agent import run_scripted
    part, parts_dir, cpath, root = args
    try:
        return run_scripted(part, Path(parts_dir), [Path(cpath)], Path(root))
    except Exception as e:  # one broken run must not sink the batch
        return {"run_id": part, "status": "crash", "error": f"{type(e).__name__}: {e}", "wall_s": 0}


def run_eval_timed(trace_root: Path, out: Path, jobs: int) -> dict:
    from eval.run_eval import main as eval_main
    t = time.monotonic()
    eval_main([str(trace_root), "--ground-truth", "--jobs", str(jobs), "--out", str(out)])
    summary = json.loads((out / "report.json").read_text())["summary"]
    return {"eval_wall_s": round(time.monotonic() - t, 1), "verdicts": summary["verdicts"],
            "per_check_fail": {k: v.get("fail", 0) for k, v in summary["per_check"].items() if v.get("fail")}}


def infra(n: int, jobs: int, seed: int) -> dict:
    root = OUT / "infra"
    parts_dir, cpath, names = make_synthetic(seed, n)
    t = time.monotonic()
    with ProcessPoolExecutor(jobs) as pool:
        rows = list(pool.map(_scripted, [(p, str(parts_dir), str(cpath), str(root)) for p in names]))
    wall = time.monotonic() - t
    statuses = {}
    for r in rows:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    out = {"tier": "infra (scripted policy, no LLM)", "parts": n, "jobs": jobs, "agent_wall_s": round(wall, 1),
           "runs_per_min": round(n / wall * 60, 1), "run_latency_s": _latency([r["wall_s"] for r in rows]),
           "statuses": statuses, "crashes": [r for r in rows if r["status"] == "crash"][:5],
           "total_tool_calls": sum(r.get("calls", 0) for r in rows),
           "oracle_failures": sum(r.get("oracle_ok") is False for r in rows),
           "trace_bytes": sum(p.stat().st_size for p in (root / "traces").rglob("*.jsonl"))}
    out.update(run_eval_timed(root / "traces", root / "eval", jobs))
    return out


def llm(n: int, jobs: int, seed: int) -> dict:
    from agent.run import parse_args, run
    root = OUT / "llm"
    parts_dir, cpath, names = make_synthetic(seed + 1000, n)

    def one(part):
        t = time.monotonic()
        try:
            s = run(parse_args(["--part", part, "--parts-dir", str(parts_dir), "--constraints", str(cpath),
                                "--outputs-dir", str(root / "outputs"), "--tag", "scale",
                                "--trace-root", str(root / "traces")]))
        except Exception as e:
            return {"part": part, "error": f"{type(e).__name__}: {e}", "wall_s": time.monotonic() - t}
        stream = ROOT / Path(s["trace"]).parent / "llm.stream.jsonl"
        events = [json.loads(l) for l in stream.read_text().splitlines() if l.strip()]
        rl = [e["rate_limit_info"] for e in events if e.get("type") == "rate_limit_event"]
        res = next((e for e in reversed(events) if e.get("type") == "result"), {})
        return {"part": part, "exit_code": s["exit_code"], "wall_s": round(time.monotonic() - t, 1),
                "cost": res.get("total_cost_usd"), "turns": res.get("num_turns"),
                "rate_status": sorted({r.get("status") for r in rl}),
                "util": [r["unifiedWindows"]["five_hour"]["utilization"] for r in rl if r.get("unifiedWindows")]}

    t = time.monotonic()
    with ThreadPoolExecutor(jobs) as pool:
        rows = list(pool.map(one, names))
    wall = time.monotonic() - t
    utils = [u for r in rows for u in r.get("util", [])]
    out = {"tier": "llm (real claude -p agents)", "parts": n, "jobs": jobs, "agent_wall_s": round(wall, 1),
           "runs_per_min": round(n / wall * 60, 1), "run_latency_s": _latency([r["wall_s"] for r in rows]),
           "failed_runs": [r for r in rows if r.get("error") or r.get("exit_code")][:5],
           "rate_limited_runs": sum("allowed" not in r.get("rate_status", ["allowed"]) for r in rows),
           "five_hour_utilization": {"min": min(utils, default=None), "max": max(utils, default=None)},
           "total_cost_usd_equiv": round(sum(r.get("cost") or 0 for r in rows), 3),
           "turns": _latency([r["turns"] for r in rows if r.get("turns")])}
    out.update(run_eval_timed(root / "traces", root / "eval", jobs))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", choices=["infra", "llm"])
    ap.add_argument("--parts", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    summary = (infra if a.tier == "infra" else llm)(a.parts, a.jobs, a.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{a.tier}_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
