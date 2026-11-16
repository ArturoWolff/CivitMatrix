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


def file_identity_matches_hash_lookup(version_payload, identity, actual_hash) -> bool:
    """By-hash version membership alone never proves which file was served."""
    from civitmatrix.indexer import cm_install_identity

    if identity is None or not isinstance(version_payload, dict):
        return False
    try:
        if (int(version_payload.get('id') or 0) != identity[1]
                or int(version_payload.get('modelId') or 0) != identity[0]
                or version_payload.get('baseModel') != identity[3]):
            return False
        publisher = version_payload.get('model') or {}
        creator = (publisher.get('creator') or {}).get('username')
        if creator and str(creator).strip().casefold() != identity[2]:
            return False
        if publisher.get('type') and publisher['type'] != identity[4]:
            return False
        for remote in version_payload.get('files') or []:
            metadata = remote.get('metadata') or {}
            candidate = cm_install_identity({
                'ModelId': version_payload['modelId'], 'VersionId': version_payload['id'],
                'AuthorUsername': identity[2], 'BaseModel': version_payload['baseModel'],
                'ModelType': identity[4], 'RemoteFileId': remote.get('id'),
                'RemoteFileName': remote.get('name'),
                'FileMetadata': {'format': metadata.get('format'),
                                 'fp': metadata.get('fp'), 'size': metadata.get('size')}})
            if candidate == identity:
                # A multi-file version response does not identify which variant
                # matched the lookup unless this file explicitly carries the hash.
                if len(version_payload.get('files') or []) == 1:
                    return True
                digest = (remote.get('hashes') or {}).get('BLAKE3')
                return bool(digest and str(digest).upper() == str(actual_hash).upper())
    except (TypeError, ValueError, KeyError):
        return False
    return False


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
