"""Write logs/job.json so a run can be watched from outside the process."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "runId": str(uuid.uuid4()),
            "phase": "starting",
            "counts": {},
            "current": None,
            "startedAt": _utc_now(),
            "updatedAt": _utc_now(),
            "finishedAt": None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    def _write(self) -> None:
        self._data["updatedAt"] = _utc_now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def set_meta(self, **kwargs: Any) -> None:
        with self._lock:
            self._data.update(kwargs)
            self._write()

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._data["phase"] = phase
            if phase in {"done", "error", "cancelled"}:
                self._data["finishedAt"] = _utc_now()
                self._data["current"] = None
            self._write()

    def set_current(self, model: dict[str, Any] | None) -> None:
        with self._lock:
            if model is None:
                self._data["current"] = None
            else:
                self._data["current"] = {
                    "modelId": model.get("id"),
                    "modelName": model.get("name"),
                }
            self._write()

    def bump(self, key: str, amount: int = 1) -> None:
        with self._lock:
            counts = self._data.setdefault("counts", {})
            counts[key] = int(counts.get(key, 0)) + amount
            self._write()

    def set_count(self, key: str, value: int) -> None:
        with self._lock:
            counts = self._data.setdefault("counts", {})
            counts[key] = int(value)
            self._write()
