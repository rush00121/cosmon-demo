"""Deterministic, LLM-free stand-in agent for infrastructure scale tests.

Drives the *same* tool layer, guards, trace writer and oracle as the real agent, with a generic policy and no
part knowledge: baseline -> probe each changeable parameter at both allowed bounds -> bisect toward the
original value for the smallest passing change -> verify, save, report. It exists to measure how the harness
and eval behave at hundreds/thousands of runs; it is not the submitted agent (traces are tagged backend=scripted).
"""
from __future__ import annotations

import time
from pathlib import Path

from agent.constraints import Constraints
from agent.mcp_server import emit_session_state, traced_call
from agent.partlab_client import PartLab, make_workdir
from agent.tools import PartLabTools
from agent.trace import Tracer
from agent.workflow import SENSIBLE_RANGE, SF_TARGET
from checks.verify_output import verify

BISECT_STEPS = 3


def run_scripted(part: str, parts_dir: Path, constraint_files: list[Path], out_root: Path,
                 max_solves: int = 40, max_calls: int = 200) -> dict:
    run_id = f"{part}-scripted"
    trace_dir, workdir, outputs = out_root / "traces" / run_id, out_root / "runs" / run_id, out_root / "outputs"
    if (trace_dir / "harness.jsonl").exists():
        (trace_dir / "harness.jsonl").unlink()
    cons = Constraints(*constraint_files)
    tracer = Tracer(trace_dir / "harness.jsonl", run_id)
    tracer.emit("run_start", part=part, backend="scripted", model=None, memory="off", guards=True, faults="",
                parts_dir=str(parts_dir), constraints=cons.for_part(part),
                constraint_sources=[str(p) for p in constraint_files],
                budgets={"max_solves": max_solves, "max_calls": max_calls}, system_prompt_sha="scripted")
    tools = PartLabTools(PartLab(make_workdir(workdir, parts_dir), faults={}), cons, outputs_dir=outputs,
                         max_solves=max_solves, max_calls=max_calls, run_id=run_id, assigned_part=part)
    start = time.monotonic()

    def call(tool, why, **args):
        return traced_call(tools, tracer, tool, {"why": why, **args})

    def evaluate(label: str) -> float | None:
        d = tools.default_load
        if not call("mesh", f"mesh {label}")["ok"] and not call("mesh", "retry mesh once")["ok"]:
            return None
        call("apply_load", "apply default load", face_id=d["face_id"], force_N=d["force_N"], direction=d["direction"])
        r = call("solve", f"measure SF {label}")
        return r["result"]["safety_factor"] if r["ok"] else None

    report = None
    if call("open_part", "load part", name=part)["ok"]:
        base, rules = dict(tools.base_params), cons.for_part(part)
        baseline = evaluate("baseline")
        tried = []
        if baseline is not None and baseline >= SF_TARGET:
            report = dict(status="no_change_needed", baseline_sf=baseline, final_sf=baseline)
        elif baseline is not None:
            for name, b in base.items():
                rule = rules.get(name, {})
                if rule.get("fixed"):
                    continue
                bounds = list(rule["allowed"]) if "allowed" in rule else \
                    [rule.get("max", round(b * SENSIBLE_RANGE[1], 4)), rule.get("min", round(b * SENSIBLE_RANGE[0], 4))]
                for bound in bounds:
                    call("revert_parameters", "clean slate before probe")
                    if not call("set_parameter", f"probe {name} at bound {bound}", name=name, value=bound)["ok"]:
                        continue
                    sf = evaluate(f"{name}={bound}")
                    tried.append(f"{name}={bound} -> SF {sf}")
                    if sf is None or sf < SF_TARGET:
                        continue
                    lo, hi, best = b, bound, (bound, sf)       # bisect toward the original value
                    for _ in range(BISECT_STEPS):
                        mid = round((lo + hi) / 2, 4)
                        call("set_parameter", f"bisect {name} toward original", name=name, value=mid)
                        msf = evaluate(f"{name}={mid}")
                        tried.append(f"{name}={mid} -> SF {msf}")
                        if msf is not None and msf >= SF_TARGET:
                            hi, best = mid, (mid, msf)
                        else:
                            lo = mid
                    call("set_parameter", "set chosen value for verification", name=name, value=best[0])
                    final = evaluate(f"verify {name}={best[0]}")
                    if final is not None and final >= SF_TARGET and call("save_part", "persist verified fix")["ok"]:
                        report = dict(status="fixed", baseline_sf=baseline, final_sf=final, parameter=name,
                                      old_value=b, new_value=best[0])
                    break
                if report:
                    break
            if not report:
                call("revert_parameters", "restore original parameters")
                report = dict(status="no_fix_possible", baseline_sf=baseline, final_sf=baseline)
    report = report or dict(status="failed", baseline_sf=None, final_sf=None)
    call("report_result", "final answer", tried=[], reason="scripted policy", **report)
    emit_session_state(tools, tracer)
    tracer.close()
    saved = outputs / f"{part}.json"
    oracle = verify(saved, cons, parts_dir) if tools.saved_path else {"saved": False}
    tracer = Tracer(trace_dir / "harness.jsonl", run_id)
    tracer.emit("oracle", saved_exists=bool(tools.saved_path), **oracle)
    tracer.emit("run_end", exit_code=0, wall_s=round(time.monotonic() - start, 2), num_turns=None,
                usage=None, total_cost_usd=0.0)
    tracer.close()
    return {"run_id": run_id, "status": report["status"], "wall_s": round(time.monotonic() - start, 2),
            "calls": tools.calls, "solves": tools.solve_attempts, "oracle_ok": oracle.get("ok")}
