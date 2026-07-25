"""Exclusive run lock on an output directory (cross-platform, no extra deps)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOCK_NAME = ".civitmatrix.lock"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it
        return True
    except OSError:
        return False
    return True


class RunLockError(RuntimeError):
    def __init__(self, message: str, *, lock_info: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.lock_info = lock_info or {}


@dataclass
class RunLock:
    out_dir: Path
    run_id: str
    path: Path
    held: bool = False

    @classmethod
    def acquire(cls, out_dir: Path, run_id: str, *, retries: int = 1) -> RunLock:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / LOCK_NAME
        payload = {
            "pid": os.getpid(),
            "runId": run_id,
            "outDir": str(out_dir.resolve()),
            "acquiredAt": _utc_now(),
        }
        attempt = 0
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))
                finally:
                    os.close(fd)
                return cls(out_dir=out_dir, run_id=run_id, path=path, held=True)
            except FileExistsError:
                info = _read_lock(path)
                owner_pid = int(info.get("pid") or 0)
                if owner_pid and pid_alive(owner_pid):
                    raise RunLockError(
                        f"Another CivitMatrix run is already using this folder "
                        f"(pid={owner_pid}, runId={info.get('runId')}). "
                        f"Lock file: {path}",
                        lock_info=info,
                    )
                # Stale lock — remove and retry
                try:
                    path.unlink(missing_ok=True)
                except OSError as e:
                    raise RunLockError(
                        f"Found a stale lock but could not remove it: {path} ({e})",
                        lock_info=info,
                    ) from e
                attempt += 1
                if attempt > retries:
                    raise RunLockError(
                        f"Could not acquire lock after removing stale file: {path}",
                        lock_info=info,
                    )
                time.sleep(0.05)

    def release(self) -> None:
        if not self.held:
            return
        try:
            info = _read_lock(self.path)
            if int(info.get("pid") or 0) in {0, os.getpid()}:
                self.path.unlink(missing_ok=True)
        except OSError:
            pass
        self.held = False

    def __enter__(self) -> RunLock:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
