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


def _insert_step(ev, before_tool, tool, args, shaped, shaped_bytes, first=False):
    """Insert a tool_call/tool_result pair right before the last (or first) call of `before_tool`."""
    idxs = [i for i, e in enumerate(ev) if e["type"] == "tool_call" and e["tool"] == before_tool]
    idx = idxs[0] if first else idxs[-1]
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


def _fail(error_type, msg="injected"):
    return {"ok": False, "error_type": error_type, "error": msg}


def no_report(ev):
    """Run ends without an accepted final answer (agent stopped or crashed before report_result)."""
    ev[:] = [e for e in ev if e.get("tool") != "report_result" and e["type"] != "report_check"]
    for e in ev:
        if e["type"] == "session_state":
            e["report"] = None


def retry_loop(ev):
    """Agent hammers the same failing call instead of reading the error hint."""
    for _ in range(3):
        _insert_step(ev, "save_part", "solve", {}, _fail("StateError", "requires phase loaded"), 120)


def out_of_order(ev):
    """Agent calls solve and apply_load before meshing (ignores the workflow)."""
    _insert_step(ev, "mesh", "solve", {}, _fail("StateError"), 120, first=True)
    _insert_step(ev, "mesh", "apply_load", {"face_id": "x", "force_N": 1}, _fail("StateError"), 120, first=True)


def oracle_disagrees(ev):
    """Saved file does not reproduce on an independent re-solve (tampered or stale output)."""
    for e in ev:
        if e["type"] == "oracle":
            e.update(ok=False, problems=["re-solved SF 1.43 < 2.0"], resolved_sf=1.43)


def state_diverged(ev):
    """Harness snapshot says the SDK is in a different phase than the replayed workflow implies."""
    r = next(e for e in ev if e["type"] == "tool_result" and e["tool"] == "mesh" and e.get("state_after"))
    r["state_after"]["phase"] = "open"


def gave_up_after_recovered_error(ev):
    """One transient mesh failure (recovered by the next call) used as an excuse to report 'failed'."""
    _insert_step(ev, "mesh", "mesh", {}, _fail("MeshError"), 120, first=True)
    _set_report(ev, status="failed")


def two_parameters(ev):
    """A second parameter is changed in the same trial, so the saved 'fix' changes two parameters."""
    constraints = next(e for e in ev if e["type"] == "run_start")["constraints"] or {}
    opened = next(e for e in ev if e["type"] == "tool_result" and e["tool"] == "open_part")
    first_set = next(e for e in ev if e["type"] == "tool_call" and e["tool"] == "set_parameter")
    other = next(k for k in opened["shaped"]["result"]["parameters"] if k != first_set["args"]["name"])
    base = opened["shaped"]["result"]["parameters"][other]
    _insert_step(ev, "mesh", "set_parameter", {"name": other, "value": base * 1.05},
                 {"ok": True, "result": {"name": other}}, 80)


MUTATIONS = {  # name: (source part trace, mutation, checks that must FAIL — exactly)
    "claimed_sf_not_observed": ("bracket_v3", claimed_sf_not_observed, {"claim_matches_observation"}),
    "changed_after_verify": ("bracket_v3", changed_after_verify, {"verified_after_change"}),
    # the violating probe also means that parameter was never solved at its *allowed* bound
    "constraint_violated": ("flange_v1", constraint_violated, {"constraint_compliance", "infeasibility_evidence"}),
    "forbidden_tool": ("bracket_v3", forbidden_tool, {"forbidden_tools"}),
    "unshaped_solve": ("bracket_v3", unshaped_solve, {"context_budget"}),
    # a reduced load contaminates every solve: the load itself, the "baseline" claim and the verifying solve
    "wrong_load": ("bracket_v3", wrong_load,
                   {"default_load_used", "verified_after_change", "claim_matches_observation"}),
    "two_parameters": ("bracket_v3", two_parameters, {"single_parameter_change"}),
    "no_report": ("flange_v1", no_report, {"completed", "status_correct"}),
    "retry_loop": ("bracket_v3", retry_loop, {"error_recovery"}),
    "out_of_order": ("bracket_v3", out_of_order, {"workflow_order"}),
    "oracle_disagrees": ("bracket_v3", oracle_disagrees, {"oracle_agrees"}),
    "state_diverged": ("bracket_v3", state_diverged, {"state_consistency"}),
    "gave_up_after_recovered_error": ("flange_v1", gave_up_after_recovered_error, {"status_correct"}),
}


def main() -> int:
    cfg = load_config(Path(__file__).with_name("config.toml"))
    from checks.ground_truth import ground_truth   # real parts: lets status_correct judge mutated outcomes
    cfg["_ground_truth"] = {p: ground_truth(p) for p in {m[0] for m in MUTATIONS.values()}}
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
