"""Optional viewer: push canonical runs + eval scores to Langfuse. Files stay the source of truth.

Langfuse v4 ingests traces only via OpenTelemetry, so runs go to /api/public/otel/v1/traces as OTLP/JSON
(root span = run, one child span per tool call) and eval checks go to /api/public/scores. Stdlib only.
Idempotent: ids are derived from the trace's directory name, so re-exporting updates instead of duplicating.
Usage: LANGFUSE_HOST=http://127.0.0.1:3000 LANGFUSE_PUBLIC_KEY=pk-lf-... LANGFUSE_SECRET_KEY=sk-lf-... \
       .venv/bin/python -m eval.export_langfuse traces/ [--report eval/out/report.json ...]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.request
import uuid
from pathlib import Path

from .adapters import load
from .run_eval import find_traces
from .schema import Run

NS = uuid.UUID("5f0c6a52-2f7e-4f5c-9a53-7c2d1e0b9a11")
SCORE = {"pass": 1.0, "warn": 0.5, "fail": 0.0}


def _hex(n: int, *parts) -> str:
    return uuid.uuid5(NS, "/".join(map(str, parts))).hex[:n]


def _ns(ts: float | None) -> str:
    return str(int((ts or 0) * 1e9))


def _attrs(d: dict) -> list[dict]:
    out = []
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            val = {"arrayValue": {"values": [{"stringValue": x} for x in v]}}
        elif isinstance(v, (dict, list)):
            val = {"stringValue": json.dumps(v, default=str)}
        else:
            val = {"stringValue": str(v)}
        out.append({"key": k, "value": val})
    return out


def otlp_payload(run: Run, export_id: str, row: dict | None) -> tuple[dict, str]:
    trace_id, root_id = _hex(32, export_id), _hex(16, export_id, "root")
    starts = [s.ts for s in run.steps if s.ts]
    t0, t1 = (min(starts), max(s.ts + (s.duration_ms or 0) / 1000 for s in run.steps if s.ts)) if starts else (0, 0)
    tags = [run.harness, f"status:{(run.report or {}).get('status')}", *([f"verdict:{row['verdict']}"] if row else [])]
    spans = [{"traceId": trace_id, "spanId": root_id, "name": f"partlab:{run.part}", "kind": 1,
              "startTimeUnixNano": _ns(t0), "endTimeUnixNano": _ns(t1),
              "attributes": _attrs({"langfuse.trace.name": f"partlab:{run.part}", "langfuse.session.id": run.part,
                                    "langfuse.trace.tags": tags, "langfuse.trace.input": {"part": run.part},
                                    "langfuse.trace.output": run.report,
                                    "langfuse.trace.metadata.run_id": run.run_id,
                                    "langfuse.trace.metadata.oracle": run.oracle,
                                    "langfuse.trace.metadata.usage": run.meta.get("usage"),
                                    "langfuse.trace.metadata.model": run.meta.get("model")})}]
    for s in run.steps:
        start = s.ts or t0
        spans.append({"traceId": trace_id, "spanId": _hex(16, export_id, s.seq), "parentSpanId": root_id,
                      "name": s.tool, "kind": 1, "startTimeUnixNano": _ns(start),
                      "endTimeUnixNano": _ns(start + (s.duration_ms or 0) / 1000),
                      "status": {"code": 1 if s.ok else 2, "message": s.error_type or ""},
                      "attributes": _attrs({"langfuse.observation.input": s.args,
                                            "langfuse.observation.output": s.result,
                                            "langfuse.observation.level": "DEFAULT" if s.ok else "ERROR",
                                            "langfuse.observation.status_message": s.error_type,
                                            "langfuse.observation.metadata.seq": s.seq,
                                            "langfuse.observation.metadata.source": s.source,
                                            "langfuse.observation.metadata.result_bytes": s.result_bytes,
                                            "langfuse.observation.metadata.raw_bytes": s.raw_bytes,
                                            "langfuse.observation.metadata.state_after": s.state_after})})
    payload = {"resourceSpans": [{"resource": {"attributes": _attrs({"service.name": "partlab-agent"})},
                                  "scopeSpans": [{"scope": {"name": "partlab-eval"}, "spans": spans}]}]}
    return payload, trace_id


def scores_for(trace_id: str, export_id: str, row: dict) -> list[dict]:
    out = [{"id": _hex(32, export_id, "overall"), "traceId": trace_id, "name": "overall", "value": row["score"],
            "comment": f"{row['verdict']}; failed: {row['failed'] or 'none'}"}]
    for name, c in row["checks"].items():
        if c["status"] in SCORE:
            ev = f" (steps {c['evidence']})" if c["evidence"] else ""
            out.append({"id": _hex(32, export_id, name), "traceId": trace_id, "name": name,
                        "value": SCORE[c["status"]], "comment": f"{c['severity']} {c['status']}: {c['message']}{ev}"})
    return out


def post(host: str, path: str, auth: str, body: dict) -> dict:
    req = urllib.request.Request(f"{host.rstrip('/')}{path}", data=json.dumps(body, default=str).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": auth})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--report", type=Path, action="append", help="eval report.json (repeatable)")
    a = ap.parse_args()
    host = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3000")
    auth = "Basic " + base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
    rows = {}
    for rp in a.report or [Path(__file__).parent / "out" / "report.json"]:
        if rp.exists():
            rows.update({Path(r["meta"]["trace_path"]).parent.name: r for r in json.loads(rp.read_text())["runs"]})
    n_traces = n_scores = 0
    for path in find_traces(a.paths, None):
        export_id = path.parent.name
        run = load(path)
        row = rows.get(export_id)
        payload, trace_id = otlp_payload(run, export_id, row)
        post(host, "/api/public/otel/v1/traces", auth, payload)
        n_traces += 1
        for score in scores_for(trace_id, export_id, row) if row else []:
            post(host, "/api/public/scores", auth, score)
            n_scores += 1
        print(f"{export_id}: {len(run.steps)} spans, {len(scores_for(trace_id, export_id, row)) if row else 0} scores")
    print(f"exported {n_traces} traces, {n_scores} scores to {host}")


if __name__ == "__main__":
    main()
