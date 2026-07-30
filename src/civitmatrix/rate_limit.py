"""Global shared download bandwidth limiter (token bucket)."""

from __future__ import annotations

import threading
import time


class BandwidthLimiter:
    """
    Thread-safe token bucket. ``bytes_per_sec <= 0`` disables limiting.
    Capacity equals one second of budget so short bursts stay smooth.
    """

    def __init__(self, bytes_per_sec: float = 0.0) -> None:
        self._lock = threading.Lock()
        self.set_rate(bytes_per_sec)

    def set_rate(self, bytes_per_sec: float) -> None:
        with self._lock:
            rate = float(bytes_per_sec) if bytes_per_sec and bytes_per_sec > 0 else 0.0
            self._rate = rate
            self._capacity = rate if rate > 0 else 0.0
            self._tokens = self._capacity
            self._updated = time.monotonic()

    @property
    def bytes_per_sec(self) -> float:
        with self._lock:
            return self._rate

    def acquire(self, nbytes: int) -> None:
        if nbytes <= 0:
            return
        while True:
            with self._lock:
                if self._rate <= 0:
                    return
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= nbytes:
                    self._tokens -= nbytes
                    return
                wait = (nbytes - self._tokens) / self._rate
            time.sleep(min(wait, 0.25))


def mib_per_sec_to_bytes(mib_per_sec: float) -> float:
    """Convert MiB/s (1024**2) to bytes/s; ``<= 0`` → unlimited (0)."""
    if mib_per_sec is None:
        return 0.0
    try:
        v = float(mib_per_sec)
    except (TypeError, ValueError):
        return 0.0
    if v <= 0:
        return 0.0
    return v * (1024 ** 2)


def parse_rate_limit_mib(raw: str | None, default: float = 0.0) -> float:
    """Parse env/CLI MiB/s value; empty/invalid → default; ``0`` = unlimited."""
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default
