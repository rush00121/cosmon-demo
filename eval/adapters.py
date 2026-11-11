"""Adapters: raw trace -> canonical Run. Add a harness = add a function + a detect rule.

- partlab_harness: our harness.jsonl (tool-level ground truth incl. raw sizes, SDK state, oracle).
- claude_stream:   bare Claude Code `--output-format stream-json` (no harness extras). Proves the checks
                   do not depend on our own trace format.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schema import Run, Step, observe

TOOL_PREFIXES = ("mcp__partlab__",)


def canonical_tool(name: str) -> str:
    for p in TOOL_PREFIXES:
        if name.startswith(p):
            return name[len(p):]
    return name.replace("-", "_")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def partlab_harness(path: Path) -> Run:
    events = _read_jsonl(path)
    start = next((e for e in events if e["type"] == "run_start"), {})
    session = next((e for e in reversed(events) if e["type"] == "session_state"), {})
    end = next((e for e in reversed(events) if e["type"] == "run_end"), {})
    oracle = next((e for e in reversed(events) if e["type"] == "oracle"), None)
    calls = {e["seq"]: e for e in events if e["type"] == "tool_call"}
    steps, attempts = [], []
    for e in events:
        if e["type"] == "tool_result":
            c = calls.get(e["call_seq"], {})
            shaped = e.get("shaped") or {}
            result = shaped.get("result") if shaped.get("ok") else shaped
            steps.append(Step(seq=e["call_seq"], tool=e["tool"], args=c.get("args", {}), ok=bool(e.get("ok")),
                              error_type=e.get("error_type"), source=e.get("source"), result=result,
                              result_bytes=e.get("shaped_bytes", 0), raw_bytes=e.get("raw_bytes"),
                              state_after=e.get("state_after"),
                              obs=observe(e["tool"], bool(e.get("ok")), result),
                              ts=c.get("ts"), duration_ms=e.get("duration_ms")))
        elif e["type"] == "report_check":
            attempts.append({**e["report"], "_problems": e["problems"]})
    meta = {k: start.get(k) for k in ("model", "memory", "guards", "faults", "budgets", "system_prompt_sha",
                                      "constraint_sources", "parts_dir")}
    meta.update(usage={k: end.get(k) for k in ("num_turns", "total_cost_usd", "wall_s")},
                exit_code=end.get("exit_code"), base_params=session.get("base_params"),
                trace_path=str(path))
    return Run(run_id=start.get("run_id") or Path(path).parent.name, harness="partlab_harness",
               part=start.get("part") or session.get("part"), steps=steps,
               report=session.get("report") or (attempts[-1] if attempts else None), report_attempts=attempts,
               constraints=start.get("constraints"), meta=meta, oracle=oracle)


def claude_stream(path: Path) -> Run:
    events = _read_jsonl(path)
    pending, steps, attempts = {}, [], []
    for e in events:
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if b.get("type") == "tool_use":
                pending[b["id"]] = b
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                use = pending.pop(b["tool_use_id"])
                text = "".join(x.get("text", "") for x in b.get("content") or [] if isinstance(x, dict))
                try:
                    shaped = json.loads(text)
                except json.JSONDecodeError:
                    shaped = {"ok": False, "error_type": "Unparseable", "error": text[:200]}
                tool, ok = canonical_tool(use["name"]), bool(shaped.get("ok")) and not b.get("is_error")
                result = shaped.get("result") if ok else shaped
                steps.append(Step(seq=len(steps) + 1, tool=tool, args=use.get("input") or {}, ok=ok,
                                  error_type=shaped.get("error_type"), result=result, result_bytes=len(text),
                                  obs=observe(tool, ok, result)))
                if tool == "report_result":
                    attempts.append(dict(use.get("input") or {}))
    accepted = [s for s in steps if s.tool == "report_result" and s.ok]
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    part = next((s.args.get("name") for s in steps if s.tool == "open_part"), None)
    return Run(run_id=final.get("session_id") or Path(path).parent.name, harness="claude_stream", part=part,
               steps=steps, report=accepted[-1].args if accepted else (attempts[-1] if attempts else None),
               report_attempts=attempts, constraints=None,
               meta={"model": init.get("model"), "trace_path": str(path),
                     "usage": {"num_turns": final.get("num_turns"), "total_cost_usd": final.get("total_cost_usd")}})


ADAPTERS = {"partlab_harness": partlab_harness, "claude_stream": claude_stream}


def detect(path: Path) -> str:
    first = json.loads(Path(path).open().readline())
    if "trace_schema_version" in first:
        return "partlab_harness"
    if first.get("type") == "system":
        return "claude_stream"
    raise ValueError(f"unknown trace format: {path}")


def load(path: Path, adapter: str | None = None) -> Run:
    return ADAPTERS[adapter or detect(path)](Path(path))
