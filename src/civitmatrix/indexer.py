from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SAFE_NAME_RE = re.compile(r"[^\w.\-@()+\[\] ]+", re.UNICODE)
WS_RE = re.compile(r"\s+")


def sanitize_stem(name: str, max_len: int = 120) -> str:
    name = name.strip().removesuffix(".safetensors")
    name = SAFE_NAME_RE.sub("_", name)
    name = WS_RE.sub(" ", name).strip(" ._")
    if not name:
        name = "model"
    return name[:max_len]


def load_local_index(out_dir: Path) -> tuple[set[str], set[int], set[str]]:
    """
    Return (blake3_upper, version_ids, existing_stems_lower).

    Skip sets only include *complete* installs: non-empty ``*.safetensors`` plus a
    matching ``*.cm-info.json`` that has both VersionId and Hashes.BLAKE3.
    Orphan info/weight (or incomplete sidecars) still reserve stems for naming
    but never count as already-installed — so the next run will re-fetch/heal
    instead of faking Stability Matrix Installed.
    """
    blake3s: set[str] = set()
    version_ids: set[int] = set()
    stems: set[str] = set()
    if not out_dir.is_dir():
        return blake3s, version_ids, stems

    weights = {p.stem: p for p in out_dir.glob("*.safetensors")}
    for stem in weights:
        stems.add(stem.lower())

    for p in out_dir.glob("*.cm-info.json"):
        stem = p.name[: -len(".cm-info.json")]
        stems.add(stem.lower())
        wp = weights.get(stem)
        if wp is None:
            continue
        try:
            if wp.stat().st_size <= 0:
                continue
        except OSError:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        h = (data.get("Hashes") or {}).get("BLAKE3")
        vid = data.get("VersionId")
        if not h or vid is None:
            continue
        try:
            version_ids.add(int(vid))
        except (TypeError, ValueError):
            continue
        blake3s.add(str(h).upper())
    return blake3s, version_ids, stems


def unique_stem(preferred: str, version_id: int, existing: set[str]) -> str:
    base = sanitize_stem(preferred)
    candidate = base
    if candidate.lower() not in existing:
        return candidate
    candidate = f"{base}-v{version_id}"
    if candidate.lower() not in existing:
        return candidate
    n = 2
    while f"{candidate}-{n}".lower() in existing:
        n += 1
    return f"{candidate}-{n}"


def pick_primary_file(version: dict[str, Any]) -> dict[str, Any] | None:
    files = version.get("files") or []
    safetensors = [
        f
        for f in files
        if str(f.get("name", "")).lower().endswith(".safetensors")
        or (f.get("metadata") or {}).get("format") == "SafeTensor"
    ]
    pool = safetensors or files
    if not pool:
        return None
    for f in pool:
        if f.get("primary"):
            return f
    return pool[0]


def pick_matching_version(
    model: dict[str, Any],
    base_model: str,
    *,
    match_base_version: bool = True,
) -> dict[str, Any] | None:
    versions = model.get("modelVersions") or []
    if not versions:
        return None
    want = (base_model or "").strip()
    if match_base_version and want and want.lower() not in {"all", "*", "any"}:
        matched = [v for v in versions if (v.get("baseModel") or "") == want]
        if not matched:
            return None
        matched.sort(key=lambda v: int(v.get("id") or 0), reverse=True)
        return matched[0]
    versions = list(versions)
    versions.sort(key=lambda v: int(v.get("id") or 0), reverse=True)
    return versions[0]
