"""CLI --status: read logs/job.json (+ flags/lock) with stable exit codes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from civitmatrix.cancel_control import CANCEL_NAME
from civitmatrix.pause_control import PAUSE_NAME
from civitmatrix.run_lock import LOCK_NAME, pid_alive

# --status exit codes
EXIT_DONE = 0
EXIT_MISSING = 1
EXIT_ERROR = 2
EXIT_CANCELLED = 4
EXIT_PAUSED = 5
EXIT_ACTIVE = 6

# Runner exit codes (documented for scripts / future UI)
# 0 = success, 2 = missing API key, 3 = lock denied, 4 = cancelled, 130 = forced SIGINT


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _lock_snapshot(out_dir: str | None) -> dict[str, Any]:
    if not out_dir:
        return {"present": False, "alive": False, "path": None, "pid": None, "runId": None}
    path = Path(out_dir) / LOCK_NAME
    if not path.exists():
        return {"present": False, "alive": False, "path": str(path), "pid": None, "runId": None}
    info = _read_json(path) or {}
    pid = int(info.get("pid") or 0)
    return {
        "present": True,
        "alive": pid_alive(pid),
        "path": str(path),
        "pid": pid or None,
        "runId": info.get("runId"),
    }


def build_status_snapshot(logs_dir: Path, job_path: Path) -> dict[str, Any] | None:
    job = _read_json(job_path)
    if job is None:
        return None
    out_dir = job.get("outDir") or job.get("lockPath")
    if isinstance(out_dir, str) and out_dir.endswith(LOCK_NAME):
        out_dir = str(Path(out_dir).parent)
    flags = {
        "pauseRequest": (logs_dir / PAUSE_NAME).exists(),
        "cancelRequest": (logs_dir / CANCEL_NAME).exists(),
    }
    return {
        **job,
        "flags": flags,
        "lock": _lock_snapshot(out_dir if isinstance(out_dir, str) else None),
    }


def status_exit_code(snapshot: dict[str, Any] | None) -> int:
    if snapshot is None:
        return EXIT_MISSING
    phase = str(snapshot.get("phase") or "")
    if phase == "done":
        return EXIT_DONE
    if phase == "error":
        return EXIT_ERROR
    if phase == "cancelled":
        return EXIT_CANCELLED
    if phase == "paused":
        return EXIT_PAUSED
    if phase in {"starting", "listing", "downloading"}:
        return EXIT_ACTIVE
    if snapshot.get("finishedAt"):
        return EXIT_ERROR
    return EXIT_ACTIVE


def format_status_human(snapshot: dict[str, Any]) -> str:
    counts = snapshot.get("counts") or {}
    if isinstance(counts, dict) and counts:
        counts_s = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    else:
        counts_s = "(none)"
    current = snapshot.get("current")
    if isinstance(current, dict) and current:
        current_s = f"{current.get('modelName')} (id={current.get('modelId')})"
    else:
        current_s = "(none)"
    flags = snapshot.get("flags") or {}
    lock = snapshot.get("lock") or {}
    if lock.get("present"):
        alive = "alive" if lock.get("alive") else "stale"
        lock_s = f"held pid={lock.get('pid')} ({alive})"
    else:
        lock_s = "none"
    lines = [
        f"phase: {snapshot.get('phase')}",
        f"runId: {snapshot.get('runId')}",
        f"outDir: {snapshot.get('outDir')}",
        f"current: {current_s}",
        f"counts: {counts_s}",
        f"startedAt: {snapshot.get('startedAt')}",
        f"updatedAt: {snapshot.get('updatedAt')}",
        f"finishedAt: {snapshot.get('finishedAt')}",
        (
            f"flags: pause.request="
            f"{'yes' if flags.get('pauseRequest') else 'no'} "
            f"cancel.request="
            f"{'yes' if flags.get('cancelRequest') else 'no'}"
        ),
        f"lock: {lock_s}",
    ]
    return "\n".join(lines)


def print_status_cli(logs_dir: Path, job_path: Path, *, as_json: bool) -> int:
    snapshot = build_status_snapshot(logs_dir, job_path)
    code = status_exit_code(snapshot)
    if snapshot is None:
        msg = {"error": "missing or unreadable job.json", "path": str(job_path)}
        if as_json:
            print(json.dumps(msg, indent=2), flush=True)
        else:
            print(f"No job status ({msg['error']}: {job_path})", flush=True)
        return code
    if as_json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2), flush=True)
    else:
        print(format_status_human(snapshot), flush=True)
    return code
