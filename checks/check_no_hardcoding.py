"""Static check: agent and eval logic must not embed part names or observed results.

Scans every string and number literal (docstrings excluded) in agent/ and eval/ logic for (a) any part name
known to the SDK and (b) any SF / stress value that appears in a captured trace. Data files (constraints.json,
config.toml) and test fixtures (eval/flawed.py) are allowed to name parts.  Exit 0 iff clean.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from agent.partlab_client import DEFAULT_PARTS, ROOT

SCAN = ["agent", "eval"]
ALLOWED_FILES = {"flawed.py"}   # fixtures that mutate named traces on purpose


def literals(tree: ast.AST):
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and id(n) not in docstrings:
            yield n


def observed_numbers() -> set[float]:
    nums = set()
    for p in (ROOT / "traces").glob("*/harness.jsonl"):
        for line in p.read_text().splitlines():
            e = json.loads(line)
            r = (e.get("shaped") or {}).get("result") if e.get("tool") == "solve" else None
            if isinstance(r, dict):
                nums |= {r.get("safety_factor"), r.get("max_stress_MPa")}
    return {n for n in nums if isinstance(n, float)}


def main() -> int:
    specs = [json.loads(p.read_text()) for p in DEFAULT_PARTS.glob("*.json")]
    parts = {s["name"] for s in specs}
    param_names = {k for s in specs for k in s.get("parameters", {})}
    numbers = observed_numbers()
    hits = []
    for d in SCAN:
        for f in sorted((ROOT / d).glob("*.py")):
            if f.name in ALLOWED_FILES:
                continue
            for n in literals(ast.parse(f.read_text())):
                if isinstance(n.value, str) and any(p in n.value for p in parts):
                    hits.append(f"{f.relative_to(ROOT)}:{n.lineno} part name in literal: {n.value[:60]!r}")
                elif d == "agent" and isinstance(n.value, str) and any(p in n.value for p in param_names):
                    hits.append(f"{f.relative_to(ROOT)}:{n.lineno} parameter name in agent literal: {n.value[:60]!r}")
                elif isinstance(n.value, float) and n.value in numbers:
                    hits.append(f"{f.relative_to(ROOT)}:{n.lineno} observed result value {n.value}")
    for h in hits:
        print("HARDCODED", h)
    print(f"scanned {SCAN} against {len(parts)} part names, {len(param_names)} parameter names and {len(numbers)} observed values: "
          f"{'CLEAN' if not hits else f'{len(hits)} hit(s)'}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
