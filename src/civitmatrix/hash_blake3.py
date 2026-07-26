"""Streaming BLAKE3 hex digest (uppercase) for large weight files."""

from __future__ import annotations

from pathlib import Path

import blake3


def file_blake3_hex(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = blake3.blake3()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().upper()
