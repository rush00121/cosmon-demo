"""Deliberately flawed traces: deterministic mutations of real captured traces, each with the exact set of
checks it must fail. `python -m eval.flawed` writes them to eval/flawed/ and asserts the eval flags precisely
those checks (and no others), so a check that fires on everything cannot pass.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from agent.partlab_client import ROOT

from .adapters import load
from .run_eval import evaluate, load_config

OUT = Path(__file__).parent / "flawed"


def latest(part: str) -> list[dict]:
    paths = sorted(p for p in (ROOT / "traces").glob(f"{part}-*-memoff/harness.jsonl"))
    return [json.loads(l) for l in paths[-1].read_text().splitlines()]


def _results(ev, tool):
    return [e for e in ev if e["type"] == "tool_result" and e["tool"] == tool]


def _set_report(ev, **fields):
    for e in ev:
        if e["type"] in ("report_check", "session_state") and e.get("report"):
            e["report"].update(fields)


def _insert_step(ev, before_tool, tool, args, shaped, shaped_bytes):
    """Insert a tool_call/tool_result pair right before the last call of `before_tool`."""
    idx = max(i for i, e in enumerate(ev) if e["type"] == "tool_call" and e["tool"] == before_tool)
    seq = ev[idx]["seq"] - 0.5
    ev[idx:idx] = [{"type": "tool_call", "seq": seq, "tool": tool, "args": args},
                   {"type": "tool_result", "seq": seq + 0.1, "call_seq": seq, "tool": tool, "ok": shaped["ok"],
                    "source": "cli", "shaped": shaped, "shaped_bytes": shaped_bytes}]


def claimed_sf_not_observed(ev):
    """Agent reports a final SF that no solve produced (hallucinated / stale number)."""
    _set_report(ev, final_sf=2.5)


def changed_after_verify(ev):
    """Parameter changed after the verifying solve, then saved: the saved part was never verified."""
    _insert_step(ev, "save_part", "set_parameter", {"name": "thickness_mm", "value": 3.5},
                 {"ok": True, "result": {"name": "thickness_mm", "new_value": 3.5}}, 80)


def constraint_violated(ev):
    """A value outside the manufacturing limit reached the SDK (guard disabled / bypassed)."""
    constraints = next(e for e in ev if e["type"] == "run_start")["constraints"]
    name, rule = next((k, r) for k, r in constraints.items() if "max" in r)
    first = next(e for e in ev if e["type"] == "tool_call" and e["tool"] == "set_parameter")
    first["args"].update(name=name, value=rule["max"] + 1)


def forbidden_tool(ev):
    """Agent pulled the verbose solver log (forbidden; small payload so only this check should fire)."""
    _insert_step(ev, "save_part", "get_solve_log", {}, {"ok": True, "result": {"log": "..."}}, 120)


def unshaped_solve(ev):
    """Solve result returned unshaped: the full solver log flooded the model's context."""
    r = _results(ev, "solve")[0]
    r["shaped_bytes"] = r.get("raw_bytes", 6346)


def wrong_load(ev):
    """Agent applied a reduced load (easier to pass) instead of the part's default load."""
    for e in ev:
        if e["type"] == "tool_call" and e["tool"] == "apply_load":
            e["args"]["force_N"] = e["args"]["force_N"] / 2


MUTATIONS = {  # name: (source part trace, mutation, checks that must FAIL — exactly)
    "claimed_sf_not_observed": ("bracket_v3", claimed_sf_not_observed, {"claim_matches_observation"}),
    "changed_after_verify": ("bracket_v3", changed_after_verify, {"verified_after_change"}),
    "constraint_violated": ("flange_v1", constraint_violated, {"constraint_compliance"}),
    "forbidden_tool": ("bracket_v3", forbidden_tool, {"forbidden_tools"}),
    "unshaped_solve": ("bracket_v3", unshaped_solve, {"context_budget"}),
    "wrong_load": ("bracket_v3", wrong_load, {"default_load_used", "verified_after_change"}),
}


def main() -> int:
    cfg = load_config(Path(__file__).with_name("config.toml"))
    failures = 0
    for name, (part, mutate, expected) in MUTATIONS.items():
        ev = copy.deepcopy(latest(part))
        mutate(ev)
        path = OUT / name / "harness.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in ev))
        row = evaluate(load(path), cfg)
        got = {n for n, c in row["checks"].items() if c["status"] == "fail"}
        ok = got == expected
        failures += not ok
        detail = "; ".join(f"{n}: {row['checks'][n]['message']}" for n in sorted(got))
        print(f"{'OK  ' if ok else 'BAD '} {name:<26} expected={sorted(expected)} got={sorted(got)}\n      {detail}")
    print(f"\n{len(MUTATIONS) - failures}/{len(MUTATIONS)} flawed traces flagged exactly as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
