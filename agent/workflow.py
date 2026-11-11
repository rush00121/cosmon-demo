"""Single source of truth for the PartLab workflow vocabulary: which phases each tool needs, which phase it
leads to, whether it mutates SDK state, and which story phase it belongs to. The tool layer, eval checks and
the narrative all read this table; none of them keep their own copy.
"""
from __future__ import annotations

from dataclasses import dataclass

SF_TARGET = 2.0
SF_TOL = 1e-3
SENSIBLE_RANGE = (0.25, 4.0)  # multiples of the original value when a parameter has no stated limit

ANY = None  # no phase precondition


@dataclass(frozen=True)
class ToolRule:
    requires: frozenset | None   # SDK phases in which the call is valid (None = any)
    next_phase: str | None       # SDK phase after a successful call (None = unchanged)
    mutating: bool               # changes SDK state -> harness snapshots after it
    role: str                    # story phase: setup | work | trial | save | report | memory


_WORK = frozenset({"open", "meshed", "loaded", "solved"})
RULES: dict[str, ToolRule] = {
    "recall_lessons":    ToolRule(ANY, None, False, "setup"),
    "open_part":         ToolRule(ANY, "open", True, "setup"),
    "get_parameters":    ToolRule(ANY, None, False, "work"),
    "mesh":              ToolRule(_WORK, "meshed", True, "work"),
    "apply_load":        ToolRule(frozenset({"meshed", "loaded", "solved"}), "loaded", True, "work"),
    "solve":             ToolRule(frozenset({"loaded", "solved"}), "solved", True, "work"),
    "get_safety_factor": ToolRule(frozenset({"solved"}), None, False, "work"),
    "set_parameter":     ToolRule(ANY, "open", True, "trial"),
    "revert_parameters": ToolRule(ANY, "open", True, "trial"),
    "save_part":         ToolRule(frozenset({"solved"}), None, True, "save"),
    "report_result":     ToolRule(ANY, None, False, "report"),
    "record_lessons":    ToolRule(ANY, None, False, "memory"),
}

MUTATING = {name for name, r in RULES.items() if r.mutating}
REQUIRES = {name: set(r.requires) for name, r in RULES.items() if r.requires}
NEXT_PHASE = {name: r.next_phase for name, r in RULES.items() if r.next_phase}
ROLE = {name: r.role for name, r in RULES.items()}
