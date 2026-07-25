"""Write logs/job.json and append logs/events.jsonl for a live run."""

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
        self.events_path = path.parent / "events.jsonl"
        self._lock = threading.Lock()
        self._event_lock = threading.Lock()
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
        self.emit("run_start")

    @property
    def run_id(self) -> str:
        with self._lock:
            return str(self._data["runId"])

    def _write(self) -> None:
        self._data["updatedAt"] = _utc_now()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        tmp.write_text(text + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def emit(self, event: str, **fields: Any) -> None:
        with self._lock:
            run_id = self._data["runId"]
        row: dict[str, Any] = {
            "ts": _utc_now(),
            "runId": run_id,
            "event": event,
        }
        row.update(fields)
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._event_lock:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(line)

    def set_meta(self, **kwargs: Any) -> None:
        with self._lock:
            self._data.update(kwargs)
            self._write()

    def set_phase(self, phase: str) -> None:
        with self._lock:
            prev = self._data.get("phase")
            self._data["phase"] = phase
            if phase in {"done", "error", "cancelled"}:
                self._data["finishedAt"] = _utc_now()
                self._data["current"] = None
            self._write()
        if prev != phase:
            self.emit("phase", phase=phase, previous=prev)

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
