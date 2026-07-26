"""Free-disk checks and CivitAI file size helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


GiB = 1024**3


def floor_bytes_from_gib(gib: float) -> int:
    if gib <= 0:
        return 0
    return int(gib * GiB)


def disk_status(path: Path) -> dict[str, int]:
    usage = shutil.disk_usage(path)
    return {"free": int(usage.free), "total": int(usage.total), "used": int(usage.used)}


def below_floor(path: Path, floor_bytes: int) -> bool:
    if floor_bytes <= 0:
        return False
    return disk_status(path)["free"] < floor_bytes


def file_size_bytes(file_info: dict[str, Any] | None) -> int | None:
    if not file_info:
        return None
    size = file_info.get("size")
    if size is not None:
        try:
            n = int(size)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    size_kb = file_info.get("sizeKB")
    if size_kb is not None:
        try:
            n = int(float(size_kb) * 1024)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    return None


def format_bytes(n: int | None) -> str:
    if n is None:
        return "?"
    n = int(n)
    if n < 1024:
        return f"{n} B"
    for unit, div in (("KiB", 1024), ("MiB", 1024**2), ("GiB", 1024**3), ("TiB", 1024**4)):
        if n < div * 1024 or unit == "TiB":
            return f"{n / div:.1f} {unit}"
    return f"{n} B"
