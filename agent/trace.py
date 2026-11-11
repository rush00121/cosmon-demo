"""Harness-side trace: one JSONL event per line, flushed immediately (a crashed run still leaves a trace).

This is the eval's ground truth. Schema is versioned; the eval adapters key off `trace_schema_version`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

TRACE_SCHEMA_VERSION = 1


class Tracer:
    def __init__(self, path: Path, run_id: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        # run.py and the MCP server append to the same file in sequence; continue its numbering.
        self.seq = sum(1 for _ in self.path.open()) if self.path.exists() else 0
        self._f = self.path.open("a")

    def emit(self, type_: str, **fields) -> dict:
        self.seq += 1
        event = {"trace_schema_version": TRACE_SCHEMA_VERSION, "run_id": self.run_id, "seq": self.seq,
                 "ts": round(time.time(), 3), "type": type_, **fields}
        self._f.write(json.dumps(event, default=str) + "\n")
        self._f.flush()
        return event

    def close(self) -> None:
        self._f.close()
