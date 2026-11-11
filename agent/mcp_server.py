"""MCP stdio server exposing PartLabTools to `claude -p`. Thin: dispatch + trace, no decision logic.

Configured entirely by env vars set by run.py (one server process per agent run).
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .constraints import Constraints
from .memory import LessonStore
from .partlab_client import PartLab
from .tools import MUTATING, SCHEMAS, PartLabTools
from .trace import Tracer


def build() -> tuple[PartLabTools, Tracer]:
    env = os.environ
    run_id = env["PARTLAB_RUN_ID"]
    tracer = Tracer(Path(env["PARTLAB_TRACE"]), run_id)
    lessons = LessonStore(Path(env["PARTLAB_LESSONS"])) if env.get("PARTLAB_LESSONS") else None
    tools = PartLabTools(PartLab(Path(env["PARTLAB_WORKDIR"])), Constraints(*map(Path, env["PARTLAB_CONSTRAINTS"].split(os.pathsep))),
                         outputs_dir=Path(env["PARTLAB_OUTPUTS"]), lessons=lessons,
                         guards=env.get("PARTLAB_GUARDS", "1") == "1",
                         max_solves=int(env.get("PARTLAB_MAX_SOLVES", 12)),
                         max_calls=int(env.get("PARTLAB_MAX_CALLS", 60)), run_id=run_id,
                         assigned_part=env.get("PARTLAB_PART"))
    return tools, tracer


def traced_call(tools: PartLabTools, tracer: Tracer, name: str, args: dict) -> dict:
    """The one place a tool call becomes trace events. Used by the MCP server and the offline checks."""
    call = tracer.emit("tool_call", tool=name, args=args)
    start = time.monotonic()
    outcome = tools.call(name, args)
    fields = outcome.trace_fields()
    if name in MUTATING and tools.part:
        fields["state_after"] = tools.snapshot()
    tracer.emit("tool_result", call_seq=call["seq"], tool=name, duration_ms=int((time.monotonic() - start) * 1000),
                **fields)
    for ev in outcome.events:
        tracer.emit(ev.pop("type"), call_seq=call["seq"], tool=name, **ev)
    if name in STATE_CHANGING:
        emit_session_state(tools, tracer)
    return outcome.shaped


# The client may kill this process without a clean shutdown, so final state is written as it changes
# (latest `session_state` event wins), not only on exit.
STATE_CHANGING = {"save_part", "report_result", "record_lessons"}


def emit_session_state(tools: PartLabTools, tracer: Tracer) -> None:
    tracer.emit("session_state", report=tools.report, solves=tools.solves, calls=tools.calls,
                saved_path=tools.saved_path, base_params=tools.base_params, part=tools.part,
                default_load=tools.default_load)


def main() -> None:
    tools, tracer = build()

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=n, description=s["description"],
                       input_schema={"type": "object", "properties": s["properties"],
                                     "required": s.get("required", [])})
            for n, s in SCHEMAS.items()])

    async def call_tool(ctx, params) -> types.CallToolResult:
        shaped = traced_call(tools, tracer, params.name, dict(params.arguments or {}))
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(shaped))],
                                    is_error=not shaped.get("ok", False))

    server = Server("partlab", on_list_tools=list_tools, on_call_tool=call_tool)

    async def run() -> None:
        async with stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        anyio.run(run)
    finally:
        emit_session_state(tools, tracer)
        tracer.close()


if __name__ == "__main__":
    main()
