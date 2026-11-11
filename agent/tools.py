"""Tool layer: the LLM-facing surface. Shapes results, enforces guards/budgets, tracks session invariants.

Pure Python, no LLM and no MCP: the MCP server, the deterministic checks and the eval fixtures all drive
`PartLabTools.call()` directly. Nothing here is part-specific; part data comes from the SDK, constraints
from constraints.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .constraints import Constraints
from .memory import LessonStore
from .partlab_client import ROOT, Envelope, PartLab
from .workflow import MUTATING, SENSIBLE_RANGE, SF_TARGET, SF_TOL  # noqa: F401 (re-exported)

MAX_RESULT_BYTES = 2000
# Commands whose side effects change SDK state; the harness snapshots phase after these.

HINTS = {
    "StateError": "Follow the workflow open_part -> mesh -> apply_load -> solve. set_parameter resets to 'open'.",
    "InvalidArgumentError": "Check the argument against the error text (valid names/faces are listed there).",
    "UsageError": "The CLI did not return JSON; the call was malformed. Re-check tool arguments.",
    "TimeoutError": "Transient: retry the same call once; if it fails again, report status 'failed'.",
    "MeshError": "Meshing failed. Retry once; if it persists treat this geometry as infeasible and try another value.",
    "SolveError": "Solve failed. Re-run mesh -> apply_load -> solve once; if it persists report 'failed'.",
    "ConstraintViolation": "Value breaks a manufacturing constraint. Use a value within the stated limit.",
    "MultiParameterChange": "Only one parameter may differ from the original. Call revert_parameters first.",
    "SaveRejected": "Saving requires a fresh solve with SF >= 2.0 and exactly one parameter changed.",
    "ReportRejected": "Your report contradicts what the tools observed. Fix the fields to match observed values.",
    "BudgetExceeded": "Budget exhausted. Call report_result now with status 'failed' (or what you verified).",
    "NoPartOpen": "Call open_part first.",
    "WrongPart": "Analyse only the part you were asked about; fixes must be parameter changes on that part.",
}


@dataclass
class ToolOutcome:
    shaped: dict                      # exactly what the LLM sees
    source: str                       # cli | guard | harness
    envelope: Envelope | None = None  # raw CLI envelope, for the trace only
    events: list[dict] = field(default_factory=list)  # extra trace events (guard_reject, self_correction...)

    def trace_fields(self) -> dict:
        shaped_json = json.dumps(self.shaped, sort_keys=True)
        out = {"source": self.source, "ok": self.shaped.get("ok"), "error_type": self.shaped.get("error_type"),
               "shaped": self.shaped, "shaped_bytes": len(shaped_json)}
        if self.envelope is not None:
            out.update(exit_code=self.envelope.exit_code, raw_bytes=len(self.envelope.raw),
                       raw_sha=hashlib.sha256(self.envelope.raw.encode()).hexdigest()[:16],
                       cli_duration_ms=self.envelope.duration_ms)
        return out


def ok(result) -> dict:
    return {"ok": True, "result": result}


def err(error_type: str, message: str) -> dict:
    return {"ok": False, "error_type": error_type, "error": message, "hint": HINTS.get(error_type, "")}


SCHEMAS: dict[str, dict] = {
    "open_part": {"description": "Open a part by name. Returns revision, is_latest, material and default_load.",
                  "properties": {"name": {"type": "string"}}, "required": ["name"]},
    "get_parameters": {"description": "Current parameter values of the open part (with overrides).",
                       "properties": {}},
    "mesh": {"description": "Mesh the open part (default element size). Required before apply_load.",
             "properties": {}},
    "apply_load": {"description": "Apply a load to a face. Requires a meshed part.",
                   "properties": {"face_id": {"type": "string"}, "force_N": {"type": "number"},
                                  "direction": {"type": "string", "default": "-Z"}},
                   "required": ["face_id", "force_N"]},
    "solve": {"description": "Run the FEA solve. Returns a compact summary (max stress, SF, convergence).",
              "properties": {}},
    "get_safety_factor": {"description": "Safety factor from the most recent solve (whole part).",
                          "properties": {}},
    "set_parameter": {"description": "Change one parameter. Invalidates mesh/load/solve (phase -> open). "
                                     "Only one parameter may differ from the original part.",
                      "properties": {"name": {"type": "string"}, "value": {"type": "number"}},
                      "required": ["name", "value"]},
    "revert_parameters": {"description": "Discard all parameter changes (re-opens the part, phase -> open).",
                          "properties": {}},
    "save_part": {"description": "Save the open part to outputs/<part>.json. Only after a verified fix.",
                  "properties": {}},
    "recall_lessons": {"description": "Short lessons from previous runs (global + this part). Hints only; "
                                      "always verify by solving.", "properties": {}},
    "record_lessons": {"description": "At the end, record up to 3 short reusable lessons from this run.",
                       "properties": {"lessons": {"type": "array", "maxItems": 3, "items": {
                           "type": "object", "properties": {
                               "scope": {"type": "string", "description": "'global' or 'part:<name>'"},
                               "kind": {"type": "string", "enum": ["process", "observation"]},
                               "text": {"type": "string", "maxLength": 200}},
                           "required": ["scope", "kind", "text"]}}},
                       "required": ["lessons"]},
    "report_result": {"description": "Final answer. Values must match what solve returned.",
                      "properties": {
                          "status": {"type": "string",
                                     "enum": ["no_change_needed", "fixed", "no_fix_possible", "failed"]},
                          "baseline_sf": {"type": ["number", "null"]},
                          "final_sf": {"type": ["number", "null"]},
                          "parameter": {"type": ["string", "null"]},
                          "old_value": {"type": ["number", "null"]},
                          "new_value": {"type": ["number", "null"]},
                          "tried": {"type": "array", "items": {"type": "string"},
                                    "description": "One line per candidate tried, e.g. '<param>=<value> -> SF <x>'"},
                          "reason": {"type": "string"}},
                      "required": ["status", "baseline_sf", "final_sf", "reason"]},
}
TERMINAL = {"report_result", "record_lessons"}

# Every tool takes a short stated intent. It is recorded in the trace (engineers see *why* each call was made)
# and stripped before dispatch, so it never reaches the CLI or changes behavior.
WHY = {"type": "string", "maxLength": 120,
       "description": "<=15 words: why you are making this call, e.g. 'probe <param> at its constraint max to test feasibility'"}
for _schema in SCHEMAS.values():
    _schema["properties"] = {"why": WHY, **_schema["properties"]}
    _schema["required"] = ["why", *_schema.get("required", [])]


class PartLabTools:
    def __init__(self, lab: PartLab, constraints: Constraints, outputs_dir: Path = ROOT / "outputs",
                 lessons: LessonStore | None = None, guards: bool = True,
                 max_solves: int = 12, max_calls: int = 60, run_id: str = "adhoc",
                 assigned_part: str | None = None):
        self.lab, self.constraints, self.outputs_dir = lab, constraints, Path(outputs_dir)
        self.lessons, self.guards, self.run_id = lessons, guards, run_id
        self.max_solves, self.max_calls = max_solves, max_calls
        self.assigned_part = assigned_part
        self.part: str | None = None
        self.calls = 0
        self.solve_attempts = 0
        self.report: dict | None = None
        self._reset_part_state()

    def _reset_part_state(self) -> None:
        self.default_load: dict | None = None
        self.base_params: dict = {}
        self.params: dict = {}
        self.solves: list[dict] = []        # every successful solve summary, in order
        self.fresh_solve = False            # True iff a solve succeeded and nothing changed since
        self.saved_path: str | None = None
        self.saved_solve: dict | None = None  # the solve that was current when save_part succeeded

    def load_is_default(self, load: dict | None) -> bool:
        d, l = self.default_load or {}, load or {}
        return (l.get("face_id"), l.get("force_N"), l.get("direction")) == \
               (d.get("face_id"), d.get("force_N"), d.get("direction"))

    # ---- dispatch -------------------------------------------------------------------------------
    def call(self, name: str, args: dict | None = None) -> ToolOutcome:
        args = {k: v for k, v in (args or {}).items() if k != "why"}
        if name not in SCHEMAS:
            return ToolOutcome(err("InvalidArgumentError", f"unknown tool {name}"), "harness")
        if name not in TERMINAL:
            self.calls += 1
            if self.calls > self.max_calls or (name == "solve" and self.solve_attempts >= self.max_solves):
                return ToolOutcome(err("BudgetExceeded", f"limit reached: {self.calls} calls, "
                                                         f"{self.solve_attempts} solve attempts"), "harness")
            if name == "solve":
                self.solve_attempts += 1
        if name not in {"open_part", "recall_lessons", "record_lessons", "report_result"} and not self.part:
            return ToolOutcome(err("NoPartOpen", "no part is open"), "harness")
        try:
            outcome = getattr(self, f"_t_{name}")(**args)
        except TypeError as e:  # bad/missing tool arguments from the model
            return ToolOutcome(err("InvalidArgumentError", str(e)), "harness")
        return self._cap(outcome)

    @staticmethod
    def _cap(outcome: ToolOutcome) -> ToolOutcome:
        """Generic context guard: no tool result may put more than MAX_RESULT_BYTES in front of the model."""
        text = json.dumps(outcome.shaped, default=str)
        if len(text) > MAX_RESULT_BYTES:
            outcome.shaped = {"ok": outcome.shaped.get("ok"), "truncated": True, "bytes": len(text),
                              "preview": text[: MAX_RESULT_BYTES // 2]}
            outcome.events.append({"type": "result_truncated", "bytes": len(text)})
        return outcome

    def _cli(self, *argv) -> tuple[Envelope, dict]:
        env = self.lab.run(*argv)
        shaped = ok(env.result) if env.ok else err(env.error_type or "UnknownError", env.error or "")
        return env, shaped

    def snapshot(self) -> dict:
        """Harness-only view of SDK state (not shown to the LLM); recorded after mutating calls."""
        env = self.lab.run("get-active-part")
        r = env.result if env.ok and isinstance(env.result, dict) else {}
        return {"phase": r.get("phase"), "overrides": r.get("parameter_overrides"),
                "applied_load": r.get("applied_load")}

    def changed_params(self) -> dict:
        return {k: v for k, v in self.params.items() if self.base_params.get(k) != v}

    # ---- SDK tools ------------------------------------------------------------------------------
    def _t_open_part(self, name: str) -> ToolOutcome:
        if self.guards and self.assigned_part and name != self.assigned_part:
            detail = f"assigned part is {self.assigned_part}, not {name}"
            return ToolOutcome(err("WrongPart", detail), "guard",
                               events=[{"type": "guard_reject", "rule": "assigned_part", "detail": detail}])
        env, shaped = self._cli("open-part", name)
        if env.ok:
            if name != self.part:
                self._reset_part_state()
            self.part, self.default_load = name, env.result.get("default_load")
            penv = self.lab.run("get-parameters")
            self.base_params = dict(penv.result) if penv.ok else {}
            self.params = dict(self.base_params)
            self.fresh_solve = False
            cons = self.constraints.describe(name)
            shaped["result"] = {**env.result, "parameters": self.base_params,
                                "manufacturing_constraints": cons or ["none stated"]}
        return ToolOutcome(shaped, "cli", env)

    def _t_get_parameters(self) -> ToolOutcome:
        env, shaped = self._cli("get-parameters")
        return ToolOutcome(shaped, "cli", env)

    def _t_mesh(self) -> ToolOutcome:
        env, shaped = self._cli("mesh")
        if env.ok:
            self.fresh_solve = False
        return ToolOutcome(shaped, "cli", env)

    def _t_apply_load(self, face_id: str, force_N: float, direction: str = "-Z") -> ToolOutcome:
        env, shaped = self._cli("apply-load", f"--direction={direction}", face_id, force_N)
        if env.ok:
            self.fresh_solve = False
        return ToolOutcome(shaped, "cli", env)

    def _t_solve(self) -> ToolOutcome:
        env, shaped = self._cli("solve")
        if env.ok:
            r = env.result
            log = r.get("log", "")
            peak = re.search(r"peak stress .*", log)
            summary = {k: r.get(k) for k in ("max_stress_MPa", "yield_MPa", "safety_factor", "converged",
                                             "iterations")}
            summary.update(peak=peak.group(0) if peak else None, log_bytes=len(log),
                           note="full solver log omitted (not needed for SF)")
            self.solves.append({"sf": r.get("safety_factor"), "max_stress": r.get("max_stress_MPa"),
                                "converged": r.get("converged"), "params": dict(self.params),
                                "load": r.get("load")})
            self.fresh_solve = bool(r.get("converged"))
            shaped = ok(summary)
        return ToolOutcome(shaped, "cli", env)

    def _t_get_safety_factor(self) -> ToolOutcome:
        env, shaped = self._cli("get-safety-factor", "--region=all")
        return ToolOutcome(shaped, "cli", env)

    def _t_set_parameter(self, name: str, value: float) -> ToolOutcome:
        events = []
        if self.guards:
            violation = self.constraints.check(self.part, name, value)
            if violation:
                events.append({"type": "guard_reject", "rule": "constraint", "detail": violation})
                return ToolOutcome(err("ConstraintViolation", violation), "guard", events=events)
            others = {k for k in self.changed_params() if k != name}
            if others:
                detail = f"{sorted(others)} already changed; only one parameter may differ"
                events.append({"type": "guard_reject", "rule": "single_parameter", "detail": detail})
                return ToolOutcome(err("MultiParameterChange", detail), "guard", events=events)
        env, shaped = self._cli("set-parameter", name, value)
        if env.ok:
            self.params[name] = value
            self.fresh_solve = False
        return ToolOutcome(shaped, "cli", env, events)

    def _t_revert_parameters(self) -> ToolOutcome:
        env, shaped = self._cli("open-part", self.part)
        if env.ok:
            self.params = dict(self.base_params)
            self.fresh_solve = False
            shaped = ok({"reverted_to": self.base_params, "phase": "open"})
        return ToolOutcome(shaped, "cli", env)

    def _t_save_part(self) -> ToolOutcome:
        last = self.solves[-1] if self.solves else None
        changed = self.changed_params()
        problems = []
        if not (self.fresh_solve and last):
            problems.append("no fresh converged solve since the last change")
        elif last["sf"] is None or last["sf"] < SF_TARGET:
            problems.append(f"last SF {last['sf']} < {SF_TARGET}")
        if last and not self.load_is_default(last["load"]):
            problems.append(f"last solve used load {last['load']}, not the default {self.default_load}")
        if len(changed) != 1:
            problems.append(f"{len(changed)} parameters differ from original (need exactly 1)")
        problems += [v for k, val in changed.items() if (v := self.constraints.check(self.part, k, val))]
        if self.guards and problems:
            detail = "; ".join(problems)
            return ToolOutcome(err("SaveRejected", detail), "guard",
                               events=[{"type": "guard_reject", "rule": "save_preconditions", "detail": detail}])
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        path = (self.outputs_dir / f"{self.part}.json").resolve()
        env, shaped = self._cli("save-part", path)
        if env.ok:
            self.saved_path, self.saved_solve = str(path), last
            shaped = ok({"saved": f"outputs/{self.part}.json"})
        return ToolOutcome(shaped, "cli", env)

    # ---- memory + terminal tools ----------------------------------------------------------------
    def _t_recall_lessons(self) -> ToolOutcome:
        if not self.lessons:
            return ToolOutcome(ok({"lessons": [], "note": "memory disabled for this run"}), "harness")
        part = self.part or self.assigned_part   # agents usually recall before open_part
        scopes = [f"part:{part}"] if part else []
        return ToolOutcome(ok({"lessons": self.lessons.recall(scopes)}), "harness")

    def _t_record_lessons(self, lessons: list[dict]) -> ToolOutcome:
        if not self.lessons:
            return ToolOutcome(ok({"recorded": 0, "note": "memory disabled for this run"}), "harness")
        ids = self.lessons.add(lessons[:3], source="reflection", run_id=self.run_id)
        return ToolOutcome(ok({"recorded": len(ids)}), "harness")

    def _t_report_result(self, status: str, baseline_sf, final_sf, reason: str, parameter=None,
                         old_value=None, new_value=None, tried=None) -> ToolOutcome:
        report = dict(status=status, baseline_sf=baseline_sf, final_sf=final_sf, parameter=parameter,
                      old_value=old_value, new_value=new_value, tried=tried or [], reason=reason)
        problems = self.check_report(report)
        events = [{"type": "report_check", "problems": problems, "report": report}]
        if problems and self.guards:
            detail = "; ".join(problems)
            return ToolOutcome(err("ReportRejected", detail), "guard",
                               events=events + [{"type": "self_correction_requested", "detail": detail}])
        self.report = report
        return ToolOutcome(ok({"accepted": True}), "harness", events=events)

    def check_report(self, r: dict) -> list[str]:
        """Claims vs observations. Used live (self-correction) and by the eval (as ground truth)."""
        p, sfs = [], [s["sf"] for s in self.solves]
        close = lambda a, b: a is not None and b is not None and abs(a - b) <= SF_TOL
        if r["status"] == "failed":
            return p
        if not sfs:
            return ["no successful solve was observed"]
        if not close(r["baseline_sf"], sfs[0]):
            p.append(f"baseline_sf {r['baseline_sf']} != first observed solve {sfs[0]}")
        first = self.solves[0]
        if first["params"] != self.base_params or not self.load_is_default(first["load"]):
            p.append("first solve was not the baseline (original parameters + default load)")
        if r["status"] == "no_change_needed":
            if sfs[0] < SF_TARGET:
                p.append(f"baseline SF {sfs[0]} < {SF_TARGET}, a change is needed")
            if self.saved_path:
                p.append("part was saved but status says no change")
        if r["status"] == "fixed":
            verified = self.saved_solve or self.solves[-1]   # the saved file reflects the solve at save time
            if not close(r["final_sf"], verified["sf"]):
                p.append(f"final_sf {r['final_sf']} != SF of the saved/verified solve {verified['sf']}")
            if not self.saved_path:
                p.append("status fixed but save_part was never successful")
            if not self.load_is_default(verified["load"]):
                p.append("verified solve did not use the default load")
            diff = {k: v for k, v in verified["params"].items() if self.base_params.get(k) != v}
            if r.get("parameter") not in diff or not close(r.get("new_value"), diff.get(r.get("parameter"))):
                p.append(f"parameter/new_value do not match the solved change {diff}")
        if r["status"] == "no_fix_possible" and self.saved_path:
            p.append("a part was saved but status says no fix possible")
        return p
