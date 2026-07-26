"""Cooperative cancel via logs/cancel.request + SIGINT."""

from __future__ import annotations

import json
import signal
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


CANCEL_NAME = "cancel.request"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CancelGate:
    def __init__(self, logs_dir: Path) -> None:
        self.path = logs_dir / CANCEL_NAME
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._source: str | None = None
        self._sigint_armed = False

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        self._event.clear()
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
        self._event.set()
        return payload

    def is_requested(self) -> bool:
        if self._event.is_set():
            return True
        if self.path.exists():
            self._event.set()
            return True
        return False

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

    def install_sigint(self, run_id_fn: Callable[[], str | None]) -> None:
        def handler(signum: int, frame: Any) -> None:
            if self.is_requested() and self._sigint_armed:
                print(
                    "\nForce exit on second Ctrl+C (in-flight downloads may be partial).",
                    flush=True,
                )
                raise SystemExit(130)
            self.request(run_id=run_id_fn(), source="sigint")
            self._sigint_armed = True
            print(
                "\nCancel requested (Ctrl+C). Finishing in-flight downloads, "
                "then stopping… (Ctrl+C again to force)",
                flush=True,
            )

        signal.signal(signal.SIGINT, handler)


def request_cancel_cli(logs_dir: Path, job_path: Path) -> int:
    """Write cancel.request for an active run. Exit 0 if requested or nothing to cancel."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_id: str | None = None
    phase: str | None = None
    if job_path.exists():
        try:
            data = json.loads(job_path.read_text(encoding="utf-8"))
            run_id = data.get("runId")
            phase = data.get("phase")
        except (OSError, json.JSONDecodeError):
            pass

    if phase in {"done", "error", "cancelled"}:
        print(f"No active run to cancel (job phase={phase}).", flush=True)
        return 0
    if phase is None and not job_path.exists():
        print("No active run to cancel (missing logs/job.json).", flush=True)
        return 0

    gate = CancelGate(logs_dir)
    payload = gate.request(run_id=run_id, source="cli")
    print(
        f"Cancel requested for runId={payload.get('runId')} "
        f"(flag={gate.path}). In-flight downloads will finish first.",
        flush=True,
    )
    return 0
