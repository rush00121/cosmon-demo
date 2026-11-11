"""Manufacturing constraints as data. The SDK does not enforce these; the tool layer does.

Sources are injected from outside (one or more JSON files, later files override earlier ones). Keys are part
names or fnmatch patterns ("flange_*"); for a part, all matching keys are merged, more specific keys last
(exact name beats pattern). Per-parameter rule fields: min, max, allowed (list), fixed (bool), why.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("constraints.json")


class Constraints:
    def __init__(self, *paths: Path):
        self.paths = [Path(p) for p in (paths or (DEFAULT_PATH,))]
        self.by_key: dict[str, dict] = {}
        for path in self.paths:
            for key, rules in json.loads(path.read_text()).items():
                if key.startswith("_"):
                    continue
                merged = self.by_key.setdefault(key, {})
                for name, rule in rules.items():
                    merged[name] = {**merged.get(name, {}), **rule}

    def for_part(self, part: str) -> dict:
        keys = [k for k in self.by_key if k == part or fnmatch.fnmatchcase(part, k)]
        out: dict = {}
        for key in sorted(keys, key=lambda k: (k == part, len(k.replace("*", "")))):
            for name, rule in self.by_key[key].items():
                out[name] = {**out.get(name, {}), **rule}
        return out

    def check(self, part: str, name: str, value: float) -> str | None:
        """Return a violation message, or None if allowed."""
        rule = self.for_part(part).get(name)
        if not rule:
            return None
        why = rule.get("why", "constraint")
        if rule.get("fixed"):
            return f"{name} may not be changed ({why})"
        if "allowed" in rule and value not in rule["allowed"]:
            return f"{name}={value} not in allowed values {rule['allowed']} ({why})"
        if "max" in rule and value > rule["max"]:
            return f"{name}={value} exceeds max {rule['max']} ({why})"
        if "min" in rule and value < rule["min"]:
            return f"{name}={value} below min {rule['min']} ({why})"
        return None

    def describe(self, part: str) -> list[str]:
        out = []
        for name, rule in self.for_part(part).items():
            if rule.get("fixed"):
                bounds = ["fixed (may not change)"]
            else:
                bounds = [f"{op} {rule[k]}" for k, op in (("min", ">="), ("max", "<=")) if k in rule]
                if "allowed" in rule:
                    bounds.append(f"one of {rule['allowed']}")
            out.append(f"{name} {' and '.join(bounds)} ({rule.get('why', '')})")
        return out
