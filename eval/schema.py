"""Canonical, harness-neutral trace schema. Adapters convert raw traces into this; checks only see this.

Observations are extracted from tool results through one table (OBSERVERS), so a new tool or a new harness
needs an adapter entry, not new check logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CANONICAL_VERSION = 1


@dataclass
class Step:
    seq: int
    tool: str                      # canonical tool name, harness prefixes stripped (e.g. "solve")
    args: dict
    ok: bool
    error_type: str | None = None
    source: str | None = None      # cli | guard | harness | None (unknown in foreign harnesses)
    result: object = None          # what the model saw
    result_bytes: int = 0          # size of what the model saw (context cost)
    raw_bytes: int | None = None   # size before shaping, if known
    state_after: dict | None = None  # SDK phase/overrides snapshot, if the harness recorded one
    obs: dict = field(default_factory=dict)


@dataclass
class Run:
    run_id: str
    harness: str
    part: str | None
    steps: list[Step]
    report: dict | None = None             # final accepted report_result args (or last attempted)
    report_attempts: list[dict] = field(default_factory=list)
    constraints: dict | None = None        # constraints in force for this part (None = unknown -> resolve)
    meta: dict = field(default_factory=dict)  # model, prompt sha, budgets, memory, faults, kind, usage...
    oracle: dict | None = None             # independent re-solve of the saved output, if available

    def calls(self, tool: str) -> list[Step]:
        return [s for s in self.steps if s.tool == tool]

    @property
    def default_load(self) -> dict | None:
        for s in self.calls("open_part"):
            if s.ok and isinstance(s.result, dict):
                return s.result.get("default_load")
        return None

    @property
    def base_params(self) -> dict:
        for s in self.calls("open_part"):
            if s.ok and isinstance(s.result, dict) and "parameters" in s.result:
                return s.result["parameters"]
        return self.meta.get("base_params") or {}


def _solve_obs(r: dict) -> dict:
    return {"sf": r.get("safety_factor"), "max_stress": r.get("max_stress_MPa"), "converged": r.get("converged")}


OBSERVERS = {
    "solve": _solve_obs,
    "get_safety_factor": _solve_obs,
    "open_part": lambda r: {"is_latest": r.get("is_latest_revision", r.get("is_latest"))},
}


def observe(tool: str, ok: bool, result) -> dict:
    fn = OBSERVERS.get(tool)
    return fn(result) if fn and ok and isinstance(result, dict) else {}
