"""Throttle download_progress events and CLI \\r lines."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable

from civitmatrix.disk_guard import format_bytes

EmitFn = Callable[[str, dict[str, Any]], None]

_cli_lock = threading.Lock()
MIN_EVENT_BYTES = 8 * 1024 * 1024  # 8 MiB
CLI_INTERVAL_S = 0.5


def progress_event_threshold(total: int | None) -> int:
    if total and total > 0:
        return max(total // 20, MIN_EVENT_BYTES)  # 5%
    return MIN_EVENT_BYTES


class DownloadProgress:
    def __init__(
        self,
        *,
        path: str,
        label: str = "",
        emit: EmitFn | None = None,
        cli: bool = True,
    ) -> None:
        self.path = path
        self.label = label or path
        self._emit = emit
        self.cli = cli
        self.bytes = 0
        self.total: int | None = None
        self._last_event_at = 0
        self._last_cli_at = 0.0
        self._t0 = time.monotonic()

    def set_total(self, total: int | None) -> None:
        if total is not None and total > 0:
            self.total = int(total)

    def seed_bytes(self, n: int) -> None:
        """Set starting byte count (e.g. Range resume offset) without emitting."""
        if n > 0:
            self.bytes = int(n)
            self._last_event_at = int(n)

    def add(self, n: int) -> None:
        if n <= 0:
            return
        self.bytes += n
        now = time.monotonic()
        threshold = progress_event_threshold(self.total)
        if self.bytes - self._last_event_at >= threshold:
            self._last_event_at = self.bytes
            self._fire_event()
        if self.cli and (now - self._last_cli_at) >= CLI_INTERVAL_S:
            self._last_cli_at = now
            self._print_cli()

    def finish(self) -> None:
        self._fire_event(final=True)
        if self.cli:
            self._print_cli(final=True)

    def _pct(self) -> float | None:
        if self.total and self.total > 0:
            return min(100.0, 100.0 * self.bytes / self.total)
        return None

    def _speed(self) -> float | None:
        dt = time.monotonic() - self._t0
        if dt <= 0:
            return None
        return self.bytes / dt

    def _fire_event(self, *, final: bool = False) -> None:
        if self._emit is None:
            return
        fields: dict[str, Any] = {
            "path": self.path,
            "bytes": self.bytes,
            "total": self.total,
            "pct": self._pct(),
            "speedBps": self._speed(),
        }
        if final:
            fields["final"] = True
        self._emit("download_progress", fields)

    def _print_cli(self, *, final: bool = False) -> None:
        pct = self._pct()
        pct_s = f"{pct:5.1f}%" if pct is not None else "  ?.?%"
        speed = self._speed()
        speed_s = f"{format_bytes(int(speed))}/s" if speed else "?/s"
        line = (
            f"\rDL {self.label[:40]:<40} {format_bytes(self.bytes)}"
            f" / {format_bytes(self.total)} {pct_s} {speed_s}   "
        )
        with _cli_lock:
            if final:
                sys.stderr.write(line + "\n")
            else:
                sys.stderr.write(line)
            sys.stderr.flush()
