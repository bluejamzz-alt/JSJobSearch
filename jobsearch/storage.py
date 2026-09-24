"""Remembers which postings were already sent so each digest only has new ones."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import GradedJob

log = logging.getLogger(__name__)


class SeenStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict = {"jobs": {}, "updated_at": None}
        self.load()

    def load(self) -> None:
        if self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8")) or self._data
                self._data.setdefault("jobs", {})
            except json.JSONDecodeError:
                log.warning("Seen file %s is corrupt; starting fresh", self.path)
                self._data = {"jobs": {}, "updated_at": None}

    def save(self) -> None:
        self._data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")

    def is_seen(self, uid: str) -> bool:
        return uid in self._data["jobs"]

    def mark(self, graded: GradedJob) -> None:
        j = graded.job
        entry = self._data["jobs"].get(j.uid) or {
            "first_seen": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        entry.update({"title": j.title, "company": j.company, "url": j.url, "tier": graded.tier,
                      "score": graded.score_pct})
        self._data["jobs"][j.uid] = entry

    def reset(self) -> None:
        self._data = {"jobs": {}, "updated_at": None}

    def __len__(self) -> int:
        return len(self._data["jobs"])
