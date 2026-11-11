"""Cross-run lesson store: append/merge JSONL in git. Reads are bounded so memory never floods context."""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "memory" / "lessons.jsonl"
MAX_TEXT = 200


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", text.lower()).strip()


class LessonStore:
    def __init__(self, path: Path = DEFAULT_PATH, k: int = 5, max_bytes: int = 1500):
        self.path = Path(path)
        self.k, self.max_bytes = k, max_bytes

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def _save(self, lessons: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("".join(json.dumps(l, sort_keys=True) + "\n" for l in lessons))

    def recall(self, scopes: list[str]) -> list[dict]:
        """Top-k lessons for these scopes (plus global), most-confirmed first, capped at max_bytes."""
        wanted = {"global", *scopes}
        hits = sorted((l for l in self._load() if l["scope"] in wanted),
                      key=lambda l: (l.get("confirmations", 1), l.get("created", 0)), reverse=True)
        out, size = [], 0
        for l in hits[: self.k]:
            item = {"scope": l["scope"], "kind": l["kind"], "text": l["text"],
                    "confirmations": l.get("confirmations", 1)}
            size += len(json.dumps(item))
            if size > self.max_bytes:
                break
            out.append(item)
        return out

    def add(self, items: list[dict], source: str, run_id: str) -> list[str]:
        """Merge lessons; duplicates (same scope + normalized text) bump confirmations instead of appending."""
        lessons = self._load()
        index = {(l["scope"], _norm(l["text"])): l for l in lessons}
        ids = []
        for item in items:
            text = str(item["text"])[:MAX_TEXT]
            key = (item.get("scope", "global"), _norm(text))
            if key in index:
                l = index[key]
                l["confirmations"] = l.get("confirmations", 1) + 1
                l["evidence_run_ids"] = sorted({*l.get("evidence_run_ids", []), run_id})
            else:
                l = {"id": uuid.uuid4().hex[:8], "scope": key[0], "kind": item.get("kind", "process"),
                     "text": text, "source": source, "evidence_run_ids": [run_id],
                     "created": int(time.time()), "confirmations": 1}
                lessons.append(l)
                index[key] = l
            ids.append(l["id"])
        self._save(lessons)
        return ids
