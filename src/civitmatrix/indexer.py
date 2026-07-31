from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SAFE_NAME_RE = re.compile(r"[^\w.\-@()+\[\] ]+", re.UNICODE)
WS_RE = re.compile(r"\s+")
CM_INFO_SUFFIX = ".cm-info.json"


def sanitize_stem(name: str, max_len: int = 120) -> str:
    name = name.strip().removesuffix(".safetensors")
    name = SAFE_NAME_RE.sub("_", name)
    name = WS_RE.sub(" ", name).strip(" ._")
    if not name:
        name = "model"
    return name[:max_len]


def _is_under(out_dir: Path, path: Path) -> bool:
    """True when path's parent chain is under out_dir (no escaping via ..)."""
    try:
        path.relative_to(out_dir)
        return True
    except ValueError:
        return False


def cm_info_basename_stem(path: Path) -> str:
    """Basename stem for ``*.cm-info.json`` (not Path.stem, which keeps ``.cm-info``)."""
    return path.name[: -len(CM_INFO_SUFFIX)]


def relative_pair_stem(out_dir: Path, path: Path, *, cm_info: bool = False) -> str:
    """
    Unique pairing key relative to ``out_dir`` (posix).

    Flat: ``foo``. Nested: ``character/foo``. Use with
    ``out_dir / f"{key}.safetensors"`` / ``.cm-info.json``.
    """
    name = cm_info_basename_stem(path) if cm_info else path.stem
    rel_parent = path.parent.relative_to(out_dir)
    if rel_parent == Path("."):
        return name
    return f"{rel_parent.as_posix()}/{name}"


def iter_weight_paths(out_dir: Path, *, recursive: bool = False) -> list[Path]:
    """List ``*.safetensors`` under ``out_dir`` (flat glob or recursive rglob)."""
    if not out_dir.is_dir():
        return []
    paths = out_dir.rglob("*.safetensors") if recursive else out_dir.glob("*.safetensors")
    return sorted(p for p in paths if p.is_file() and _is_under(out_dir, p))


def iter_cm_info_paths(out_dir: Path, *, recursive: bool = False) -> list[Path]:
    """List ``*.cm-info.json`` under ``out_dir`` (flat glob or recursive rglob)."""
    if not out_dir.is_dir():
        return []
    paths = (
        out_dir.rglob(f"*{CM_INFO_SUFFIX}") if recursive else out_dir.glob(f"*{CM_INFO_SUFFIX}")
    )
    return sorted(
        p
        for p in paths
        if p.is_file() and p.name.endswith(CM_INFO_SUFFIX) and _is_under(out_dir, p)
    )


def load_local_index(
    out_dir: Path, *, recursive: bool = True
) -> tuple[set[str], set[int], set[str]]:
    """
    Return (blake3_upper, version_ids, existing_stems_lower).

    Skip sets only include *complete* installs: non-empty ``*.safetensors`` plus a
    matching ``*.cm-info.json`` that has both VersionId and Hashes.BLAKE3.
    Orphan info/weight (or incomplete sidecars) still reserve stems for naming
    but never count as already-installed — so the next run will re-fetch/heal
    instead of faking Stability Matrix Installed.

    Default ``recursive=True`` so weight + sidecar pairs in subfolders (SM-style
    category layouts) still skip downloads. Flat mode only scans ``out_dir``
    itself. Recursive mode pairs each weight with ``*.cm-info.json`` beside it
    (same directory, same basename stem). The stems set always uses lowercased
    **basename** stems for naming conflicts, same as before.
    """
    blake3s: set[str] = set()
    version_ids: set[int] = set()
    stems: set[str] = set()
    if not out_dir.is_dir():
        return blake3s, version_ids, stems

    # Pair by (parent dir, basename stem) so nested same-name files stay distinct.
    weights: dict[tuple[Path, str], Path] = {}
    for wp in iter_weight_paths(out_dir, recursive=recursive):
        stem = wp.stem
        stems.add(stem.lower())
        weights[(wp.parent, stem)] = wp

    for p in iter_cm_info_paths(out_dir, recursive=recursive):
        stem = cm_info_basename_stem(p)
        stems.add(stem.lower())
        wp = weights.get((p.parent, stem))
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


def load_local_model_max_versions(
    out_dir: Path, *, recursive: bool = True
) -> dict[int, int]:
    """
    Return ModelId → max local VersionId from complete weight + cm-info pairs.

    Same completeness rules as ``load_local_index`` (non-empty weight beside a
    sidecar with both VersionId and Hashes.BLAKE3). Used by ``--update-only``.
    """
    max_by_model: dict[int, int] = {}
    if not out_dir.is_dir():
        return max_by_model

    weights: dict[tuple[Path, str], Path] = {}
    for wp in iter_weight_paths(out_dir, recursive=recursive):
        weights[(wp.parent, wp.stem)] = wp

    for p in iter_cm_info_paths(out_dir, recursive=recursive):
        stem = cm_info_basename_stem(p)
        wp = weights.get((p.parent, stem))
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
        mid = data.get("ModelId")
        vid = data.get("VersionId")
        if not h or mid is None or vid is None:
            continue
        try:
            model_id = int(mid)
            version_id = int(vid)
        except (TypeError, ValueError):
            continue
        prev = max_by_model.get(model_id)
        if prev is None or version_id > prev:
            max_by_model[model_id] = version_id
    return max_by_model


def update_only_skip_reason(
    model_id: int | None,
    remote_version_id: int,
    local_max_versions: dict[int, int],
) -> str | None:
    """
    Decide whether ``--update-only`` should skip this model/version.

    Returns ``skip_not_installed``, ``skip_uptodate``, or None to proceed.
    """
    if model_id is None:
        return "skip_not_installed"
    local_max = local_max_versions.get(int(model_id))
    if local_max is None:
        return "skip_not_installed"
    if int(remote_version_id) <= int(local_max):
        return "skip_uptodate"
    return None


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
