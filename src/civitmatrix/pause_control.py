"""Cooperative pause via logs/pause.request (+ CLI resume deletes it)."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from civitmatrix.cancel_control import CancelGate


PAUSE_NAME = "pause.request"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PauseGate:
    def __init__(self, logs_dir: Path) -> None:
        self.path = logs_dir / PAUSE_NAME
        self._lock = threading.Lock()
        self._source: str | None = None
        # Serialize enter/exit so concurrent workers don't spam phase transitions
        self._wait_lock = threading.Lock()

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        with self._lock:
            self._source = None

    def request(self, *, run_id: str | None, source: str) -> dict[str, Any]:
        payload = {
            "runId": run_id,
            "requestedAt": _utc_now(),
            "source": source,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        with self._lock:
            self._source = source
        return payload

    def is_requested(self) -> bool:
        # Must re-read file: resume deletes it from another process
        return self.path.exists()

    @property
    def source(self) -> str | None:
        with self._lock:
            if self._source:
                return self._source
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return str(data.get("source") or "manual")
        except (OSError, json.JSONDecodeError):
            return "manual"

    def wait_if_paused(
        self,
        cancel: CancelGate,
        *,
        resume_phase: str,
        on_pause: Callable[[], None] | None = None,
        on_resume: Callable[[], None] | None = None,
        poll_s: float = 0.5,
    ) -> bool:
        """
        Block while pause.request exists.
        Returns True if cancel was requested (caller should stop).
        """
        if cancel.is_requested():
            return True
        if not self.is_requested():
            return False

        with self._wait_lock:
            if cancel.is_requested():
                return True
            if not self.is_requested():
                return False
            if on_pause:
                on_pause()
            while self.is_requested():
                if cancel.is_requested():
                    return True
                time.sleep(poll_s)
            if on_resume:
                on_resume()
            return cancel.is_requested()


def _read_job(job_path: Path) -> tuple[str | None, str | None]:
    if not job_path.exists():
        return None, None
    try:
        data = json.loads(job_path.read_text(encoding="utf-8"))
        return data.get("runId"), data.get("phase")
    except (OSError, json.JSONDecodeError):
        return None, None


def request_pause_cli(logs_dir: Path, job_path: Path) -> int:
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_id, phase = _read_job(job_path)
    if phase in {"done", "error", "cancelled"}:
        print(f"No active run to pause (job phase={phase}).", flush=True)
        return 0
    if phase is None and not job_path.exists():
        print("No active run to pause (missing logs/job.json).", flush=True)
        return 0

    gate = PauseGate(logs_dir)
    payload = gate.request(run_id=run_id, source="cli")
    print(
        f"Pause requested for runId={payload.get('runId')} "
        f"(flag={gate.path}). In-flight downloads will finish, then the run waits.",
        flush=True,
    )
    return 0


def request_resume_cli(logs_dir: Path, job_path: Path) -> int:
    logs_dir.mkdir(parents=True, exist_ok=True)
    gate = PauseGate(logs_dir)
    if not gate.is_requested():
        _, phase = _read_job(job_path)
        if phase == "paused":
            print(
                "Job is paused but pause.request is missing — nothing to clear. "
                "If stuck, restart the run.",
                flush=True,
            )
        else:
            print("No pause.request to clear (run is not paused).", flush=True)
        return 0
    gate.clear()
    print(f"Resume requested (cleared {gate.path}).", flush=True)
    return 0
