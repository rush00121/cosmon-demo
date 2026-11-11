"""Manufacturing constraints as data. The SDK does not enforce these; the tool layer does."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("constraints.json")


class Constraints:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        data = json.loads(self.path.read_text())
        self.by_part: dict[str, dict] = {k: v for k, v in data.items() if not k.startswith("_")}

    def for_part(self, part: str) -> dict:
        return self.by_part.get(part, {})

    def check(self, part: str, name: str, value: float) -> str | None:
        """Return a violation message, or None if allowed."""
        rule = self.for_part(part).get(name)
        if not rule:
            return None
        if "max" in rule and value > rule["max"]:
            return f"{name}={value} exceeds max {rule['max']} ({rule.get('why', 'constraint')})"
        if "min" in rule and value < rule["min"]:
            return f"{name}={value} below min {rule['min']} ({rule.get('why', 'constraint')})"
        return None

    def describe(self, part: str) -> list[str]:
        out = []
        for name, rule in self.for_part(part).items():
            bounds = [f"{op} {rule[k]}" for k, op in (("min", ">="), ("max", "<=")) if k in rule]
            out.append(f"{name} {' and '.join(bounds)} ({rule.get('why', '')})")
        return out
