from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_print_lock = threading.Lock()
_log_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLogger:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.failed_path = log_dir / "failed.jsonl"
        self.manifest_path = log_dir / "manifest.jsonl"
        self.run_log_path = log_dir / "run.log"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str) -> None:
        line = f"[{utc_now()}] {msg}"
        with _print_lock:
            print(line, flush=True)
        with _log_lock:
            with self.run_log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _log_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def record_failure(
        self,
        model: dict[str, Any] | None,
        reason: str,
        *,
        retryable: bool = True,
        version: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "ts": utc_now(),
            "reason": reason,
            "retryable": retryable,
            "modelId": (model or {}).get("id"),
            "modelName": (model or {}).get("name"),
            "versionId": (version or {}).get("id"),
            "baseModel": (version or {}).get("baseModel"),
            "tags": [
                t if isinstance(t, str) else t.get("name")
                for t in ((model or {}).get("tags") or [])
            ],
            "nsfw": (model or {}).get("nsfw"),
            "nsfwLevel": (model or {}).get("nsfwLevel"),
            "creator": ((model or {}).get("creator") or {}).get("username"),
        }
        if extra:
            row.update(extra)
        self.append_jsonl(self.failed_path, row)
        self.log(f"FAIL model={row.get('modelId')} {row.get('modelName')!r}: {reason}")

    def load_failed_model_ids(self) -> list[int]:
        if not self.failed_path.exists():
            return []
        ids: list[int] = []
        seen: set[int] = set()
        for line in self.failed_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("retryable", True):
                continue
            mid = row.get("modelId")
            if mid is None or mid in seen:
                continue
            seen.add(int(mid))
            ids.append(int(mid))
        return ids
