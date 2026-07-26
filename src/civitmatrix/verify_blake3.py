"""Post-download BLAKE3 verification against CivitAI file hashes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from civitmatrix.hash_blake3 import file_blake3_hex

VerifyStatus = Literal["ok", "fail", "skipped"]


def verify_weight_blake3(
    weight_path: Path,
    remote_blake3: str | None,
    *,
    skip: bool = False,
) -> tuple[VerifyStatus, str | None, str | None]:
    """
    Returns (status, local_hex_or_none, detail_reason).
    - ok: local matches remote
    - fail: mismatch
    - skipped: skip flag or missing remote hash
    """
    if skip:
        return "skipped", None, "flag"
    if not remote_blake3:
        return "skipped", None, "no_remote_hash"
    try:
        local = file_blake3_hex(weight_path)
    except OSError as e:
        return "fail", None, f"hash_error:{e}"
    if local.upper() != str(remote_blake3).upper():
        return "fail", local, "mismatch"
    return "ok", local, None


def remote_blake3_from_file_info(file_info: dict[str, Any] | None) -> str | None:
    if not file_info:
        return None
    h = (file_info.get("hashes") or {}).get("BLAKE3")
    return str(h) if h else None


def version_matches_local_hash(
    version_payload: dict[str, Any] | None,
    expected_version_id: int,
) -> bool:
    """True when by-hash lookup resolves to the version we intended to download."""
    if not version_payload:
        return False
    try:
        return int(version_payload.get("id") or 0) == int(expected_version_id)
    except (TypeError, ValueError):
        return False
