"""Optional viewer: push runs to Langfuse as readable stories. Files stay the source of truth.

Each run becomes: trace (input = task + constraints, output = one-line verdict + final answer)
  -> phase spans ("3. trial thickness_mm: 3 → 4 — SF 2.198 ✓ meets target")
     -> step spans ("solve → SF 2.198", errors as ERROR level, agent rationale in metadata)
Eval checks become trace scores; a failing check is also scored on the exact step it cites, so it shows up
on that step in the UI. Langfuse v4 ingests traces only via OTLP (/api/public/otel/v1/traces); scores via
/api/public/scores. Stdlib only; ids derived from the trace dir name, so re-exporting updates in place.
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
from .narrate import (_result_brief, _step_label, attach_findings, derived_intents, headline, phases,
                      rationale_by_step)
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
        if v is None or v == "":
            continue
        if isinstance(v, list) and v and all(isinstance(x, str) for x in v):
            val = {"arrayValue": {"values": [{"stringValue": x} for x in v]}}
        elif isinstance(v, (dict, list)):
            val = {"stringValue": json.dumps(v, default=str)}
        else:
            val = {"stringValue": str(v)}
        out.append({"key": k, "value": val})
    return out


def _bounds(steps) -> tuple[float, float]:
    ts = [s.ts for s in steps if s.ts]
    if not ts:
        return 0.0, 0.0
    return min(ts), max(s.ts + (s.duration_ms or 0) / 1000 for s in steps if s.ts)


def otlp_payload(run: Run, export_id: str, row: dict | None) -> tuple[dict, str, dict[int, str]]:
    trace_id, root_id = _hex(32, export_id), _hex(16, export_id, "root")
    ps = phases(run)
    run_findings = attach_findings(ps, row)
    why = rationale_by_step(run)
    derived = derived_intents(run)
    t0, t1 = _bounds(run.steps)
    head = headline(run, row)
    tags = [f"status:{(run.report or {}).get('status')}", *([f"verdict:{row['verdict']}"] if row else []),
            *[f"failed:{n}" for n in (row or {}).get("failed", [])], run.harness]
    spans = [{"traceId": trace_id, "spanId": root_id, "name": f"{run.part}: {head}", "kind": 1,
              "startTimeUnixNano": _ns(t0), "endTimeUnixNano": _ns(t1),
              "attributes": _attrs({
                  "langfuse.trace.name": f"{run.part} · {(run.report or {}).get('status')}",
                  "langfuse.session.id": run.part, "langfuse.trace.tags": tags,
                  "langfuse.trace.input": {"task": f"analyse {run.part}", "default_load": run.default_load,
                                           "parameters": run.base_params, "constraints": run.constraints},
                  "langfuse.trace.output": {"verdict": head, "report": run.report, "final_answer": why.get(-1),
                                            "run_level_findings": run_findings},
                  "langfuse.trace.metadata.run_id": run.run_id,
                  "langfuse.trace.metadata.oracle": run.oracle,
                  "langfuse.trace.metadata.usage": run.meta.get("usage"),
                  "langfuse.trace.metadata.model": run.meta.get("model")})}]
    step_span: dict[int, str] = {}
    for n, p in enumerate(ps, 1):
        pid = _hex(16, export_id, "phase", n)
        ps0, ps1 = _bounds(p.steps)
        bad = not p.ok or any(f["status"] == "fail" for f in p.findings)
        spans.append({"traceId": trace_id, "spanId": pid, "parentSpanId": root_id, "kind": 1,
                      "name": f"{n}. {p.title}" + (f" — {p.outcome}" if p.outcome else ""),
                      "startTimeUnixNano": _ns(ps0), "endTimeUnixNano": _ns(ps1),
                      "status": {"code": 2 if bad else 1},
                      "attributes": _attrs({
                          "langfuse.observation.level": "ERROR" if bad else "WARNING" if p.findings else "DEFAULT",
                          "langfuse.observation.status_message": "; ".join(
                              f"{f['check']}: {f['message']}" for f in p.findings),
                          "langfuse.observation.input": {"phase": p.kind},
                          "langfuse.observation.output": {"outcome": p.outcome,
                                                          "eval_findings": p.findings or None}})})
        for s in p.steps:
            sid = _hex(16, export_id, s.seq)
            step_span[s.seq] = sid
            start = s.ts or ps0
            spans.append({"traceId": trace_id, "spanId": sid, "parentSpanId": pid, "kind": 1,
                          "name": _step_label(s), "startTimeUnixNano": _ns(start),
                          "endTimeUnixNano": _ns(start + (s.duration_ms or 0) / 1000),
                          "status": {"code": 1 if s.ok else 2, "message": s.error_type or ""},
                          "attributes": _attrs({
                              "langfuse.observation.input": {"why (agent)": s.why or why.get(s.seq),
                                                             "why (context)": derived.get(s.seq),
                                                             "args": s.args},
                              "langfuse.observation.output": {"summary": _result_brief(s), "result": s.result},
                              "langfuse.observation.level": "DEFAULT" if s.ok else "ERROR",
                              "langfuse.observation.status_message": (s.result or {}).get("error")
                              if not s.ok and isinstance(s.result, dict) else None,
                              "langfuse.observation.metadata.agent_rationale": why.get(s.seq),
                              "langfuse.observation.metadata.seq": s.seq,
                              "langfuse.observation.metadata.source": s.source,
                              "langfuse.observation.metadata.bytes_read_by_model": s.result_bytes,
                              "langfuse.observation.metadata.raw_bytes": s.raw_bytes,
                              "langfuse.observation.metadata.sdk_state_after": s.state_after})})
    payload = {"resourceSpans": [{"resource": {"attributes": _attrs({"service.name": "partlab-agent"})},
                                  "scopeSpans": [{"scope": {"name": "partlab-eval"}, "spans": spans}]}]}
    return payload, trace_id, step_span


def scores_for(trace_id: str, export_id: str, row: dict, step_span: dict[int, str]) -> list[dict]:
    out = [{"id": _hex(32, export_id, "overall"), "traceId": trace_id, "name": "overall", "value": row["score"],
            "comment": f"{row['verdict']}; failed: {', '.join(row['failed']) or 'none'}"}]
    for name, c in row["checks"].items():
        if c["status"] not in SCORE:
            continue
        ev = f" (steps {c['evidence']})" if c["evidence"] else ""
        out.append({"id": _hex(32, export_id, name), "traceId": trace_id, "name": name,
                    "value": SCORE[c["status"]], "comment": f"{c['severity']} {c['status']}: {c['message']}{ev}"})
        if c["status"] != "pass":
            for seq in c["evidence"][:3]:   # also pin the finding to the step it cites
                if seq in step_span:
                    out.append({"id": _hex(32, export_id, name, seq), "traceId": trace_id,
                                "observationId": step_span[seq], "name": name, "value": SCORE[c["status"]],
                                "comment": f"{c['severity']} {c['status']}: {c['message']}"})
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
        payload, trace_id, step_span = otlp_payload(run, export_id, row)
        post(host, "/api/public/otel/v1/traces", auth, payload)
        scores = scores_for(trace_id, export_id, row, step_span) if row else []
        for score in scores:
            post(host, "/api/public/scores", auth, score)
        n_traces, n_scores = n_traces + 1, n_scores + len(scores)
        print(f"{export_id}: {len(payload['resourceSpans'][0]['scopeSpans'][0]['spans'])} spans, {len(scores)} scores")
    print(f"exported {n_traces} traces, {n_scores} scores to {host}")


if __name__ == "__main__":
    main()
