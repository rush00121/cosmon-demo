"""Run the agent on one part: isolated session -> `claude -p` + PartLab MCP server -> oracle -> run_end.

Usage: .venv/bin/python -m agent.run --part bracket_v3 [--memory on|off] [--model sonnet] [--no-guards]
       [--faults mesh:fail] [--parts-dir DIR] [--constraints FILE] [--outputs-dir DIR] [--tag TAG]
Writes traces/<run_id>/harness.jsonl (tool-level ground truth) and llm.stream.jsonl (Claude Code stream-json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from checks.verify_output import verify
from .constraints import DEFAULT_PATH as DEFAULT_CONSTRAINTS, Constraints
from .memory import DEFAULT_PATH as DEFAULT_LESSONS
from .partlab_client import DEFAULT_PARTS, ROOT, make_workdir
from .prompts import SYSTEM_PROMPT, user_prompt
from .tools import SCHEMAS
from .trace import Tracer

PYTHON = ROOT / ".venv" / "bin" / "python"


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--part", required=True)
    p.add_argument("--model", default="sonnet")
    p.add_argument("--memory", choices=["on", "off"], default="off")
    p.add_argument("--no-guards", action="store_true", help="disable tool guards (to produce bad traces)")
    p.add_argument("--faults", default="", help="e.g. mesh:fail,solve:timeout (each fires once)")
    p.add_argument("--parts-dir", type=Path, default=DEFAULT_PARTS)
    p.add_argument("--constraints", type=Path, action="append",
                   help="constraints JSON; repeatable, later files override (default: agent/constraints.json)")
    p.add_argument("--outputs-dir", type=Path, default=ROOT / "outputs")
    p.add_argument("--lessons", type=Path, default=DEFAULT_LESSONS)
    p.add_argument("--max-solves", type=int, default=12)
    p.add_argument("--max-calls", type=int, default=60)
    p.add_argument("--timeout-s", type=int, default=600)
    p.add_argument("--tag", default="")
    a = p.parse_args(argv)
    a.constraints = [c.resolve() for c in (a.constraints or [DEFAULT_CONSTRAINTS])]
    return a


def run(a) -> dict:
    run_id = "-".join(x for x in (a.part, time.strftime("%Y%m%dT%H%M%S"), f"mem{a.memory}", a.tag) if x)
    trace_dir = ROOT / "traces" / run_id
    workdir = make_workdir(ROOT / "runs" / run_id, a.parts_dir)
    harness_path, stream_path = trace_dir / "harness.jsonl", trace_dir / "llm.stream.jsonl"
    saved = a.outputs_dir / f"{a.part}.json"
    if saved.exists():  # a stale file from an earlier run must not count as this run's output
        saved.unlink()

    tracer = Tracer(harness_path, run_id)
    tracer.emit("run_start", part=a.part, backend="claude_cli", model=a.model, memory=a.memory,
                guards=not a.no_guards, faults=a.faults, parts_dir=str(a.parts_dir),
                constraints=Constraints(*a.constraints).for_part(a.part),
                constraint_sources=[str(c) for c in a.constraints],
                budgets={"max_solves": a.max_solves, "max_calls": a.max_calls},
                tools=sorted(SCHEMAS), system_prompt=SYSTEM_PROMPT,
                system_prompt_sha=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12])
    tracer.close()

    server_env = {
        "PYTHONPATH": str(ROOT), "PARTLAB_RUN_ID": run_id, "PARTLAB_TRACE": str(harness_path),
        "PARTLAB_WORKDIR": str(workdir), "PARTLAB_CONSTRAINTS": os.pathsep.join(map(str, a.constraints)),
        "PARTLAB_OUTPUTS": str(a.outputs_dir.resolve()), "PARTLAB_GUARDS": "0" if a.no_guards else "1",
        "PARTLAB_MAX_SOLVES": str(a.max_solves), "PARTLAB_MAX_CALLS": str(a.max_calls),
        "PARTLAB_FAULTS": a.faults, "PARTLAB_PART": a.part,
        **({"PARTLAB_LESSONS": str(a.lessons.resolve())} if a.memory == "on" else {}),
    }
    mcp_cfg = workdir / "mcp.json"
    mcp_cfg.write_text(json.dumps({"mcpServers": {"partlab": {
        "command": str(PYTHON), "args": ["-m", "agent.mcp_server"], "env": server_env}}}))

    cmd = ["claude", "-p", user_prompt(a.part), "--system-prompt", SYSTEM_PROMPT,
           "--mcp-config", str(mcp_cfg), "--strict-mcp-config",
           "--tools", "",                              # no built-in tools: no Bash/Read -> no parts/*.json
           "--allowedTools", "mcp__partlab",           # only our MCP tools, pre-approved
           "--permission-mode", "dontAsk", "--setting-sources", "", "--disable-slash-commands",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose", "--model", a.model]
    start = time.monotonic()
    with stream_path.open("w") as out:
        try:
            proc = subprocess.run(cmd, cwd=workdir, stdout=out, stderr=subprocess.PIPE, text=True,
                                  timeout=a.timeout_s)
            exit_code, stderr = proc.returncode, proc.stderr[-2000:]
        except subprocess.TimeoutExpired:
            exit_code, stderr = -1, "agent timed out"

    result_line = {}
    for line in stream_path.read_text().splitlines():
        if '"type":"result"' in line.replace(" ", ""):
            result_line = json.loads(line)
    oracle = verify(saved, Constraints(*a.constraints), a.parts_dir) if saved.exists() else {"saved": False}

    tracer = Tracer(harness_path, run_id)
    tracer.emit("oracle", saved_exists=saved.exists(), **oracle)
    end = tracer.emit("run_end", exit_code=exit_code, stderr=stderr, wall_s=round(time.monotonic() - start, 1),
                      num_turns=result_line.get("num_turns"), usage=result_line.get("usage"),
                      total_cost_usd=result_line.get("total_cost_usd"), final_text=result_line.get("result"))
    tracer.close()
    return {"run_id": run_id, "trace": str(harness_path.relative_to(ROOT)), "exit_code": exit_code,
            "oracle": oracle, "final_text": end["final_text"]}


if __name__ == "__main__":
    summary = run(parse_args())
    print(json.dumps(summary, indent=2))
    sys.exit(0 if summary["exit_code"] == 0 else 1)
