"""Close the learning loop: turn eval findings into reusable process lessons in memory/lessons.jsonl.

Only generic, check-level lessons are written (never part answers), so memory cannot smuggle in hard-coded
results. Repeated failures of the same check bump `confirmations`, which ranks the lesson higher at recall.
Usage: .venv/bin/python -m eval.learn eval/out/report.json [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.memory import LessonStore

LESSONS = {
    "claim_matches_observation": "Report only SF values copied from the solve result you verified; never round or recompute.",
    "verified_after_change": "Save only immediately after a solve with SF >= 2.0 of the exact value being saved.",
    "default_load_used": "Always apply the default_load from open_part exactly (face, force, direction).",
    "single_parameter_change": "Call revert_parameters before probing a different parameter.",
    "constraint_compliance": "Check each candidate value against the listed manufacturing constraints before set_parameter.",
    "workflow_order": "After set_parameter or revert_parameters, re-run mesh -> apply_load -> solve before reading SF.",
    "forbidden_tools": "Never fetch the solver log or handbook estimates; the solve summary has everything needed.",
    "context_budget": "Request only the summary fields you need; avoid verbose outputs.",
    "error_recovery": "On a tool error, follow its hint; never repeat an identical failing call more than once.",
    "infeasibility_evidence": "Before reporting no_fix_possible, probe every changeable parameter at its most favorable allowed value.",
    "efficiency": "Probe the bound first, then refine with at most two more solves.",
    "status_correct": "Only report no_fix_possible after every parameter's favorable bound has been solved.",
}


def lessons_from_report(report: dict) -> list[dict]:
    out = []
    for run in report["runs"]:
        for name in run["issues"]:
            if name in LESSONS:
                out.append({"scope": "global", "kind": "process", "text": LESSONS[name], "_run": run["run_id"]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("report", type=Path)
    ap.add_argument("--lessons", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    items = lessons_from_report(json.loads(a.report.read_text()))
    store = LessonStore(a.lessons) if a.lessons else LessonStore()
    for item in items:
        print(f"{'(dry) ' if a.dry_run else ''}lesson from {item['_run']}: {item['text']}")
        if not a.dry_run:
            store.add([item], source="eval", run_id=item["_run"])
    print(f"{len(items)} lesson(s){' would be' if a.dry_run else ''} recorded")


if __name__ == "__main__":
    main()
