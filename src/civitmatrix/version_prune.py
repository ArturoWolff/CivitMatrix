"""Prune older local versions of the same ModelId (latest-only library)."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Iterator

from civitmatrix.indexer import (
    WEIGHT_EXTENSIONS,
    iter_cm_info_paths,
    relative_pair_stem,
    weight_path_for_stem,
)
from civitmatrix.preview_media import iter_preview_paths


def iter_model_sidecars(
    out_dir: Path, *, recursive: bool = True
) -> Iterator[tuple[Path, dict[str, Any]]]:
    if not out_dir.is_dir():
        return
    for info_path in iter_cm_info_paths(out_dir, recursive=recursive):
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        yield info_path, data


def collision_base_name(stem: str, version_id: int) -> str:
    """
    Basename with unique_stem suffixes stripped.

    ``foo``, ``foo-v123``, and ``foo-v123-2`` all map to ``foo`` when
    ``version_id`` is 123. Distinct filenames of the same version stay distinct.
    """
    name = Path(stem).name
    vid = int(version_id)
    numbered = re.fullmatch(rf"(.+)-v{vid}-(\d+)$", name)
    if numbered:
        return numbered.group(1)
    plain = re.fullmatch(rf"(.+)-v{vid}$", name)
    if plain:
        return plain.group(1)
    return name


def _stem_parent_key(stem: str) -> str:
    parent = Path(stem).parent
    if parent == Path("."):
        return "."
    return parent.as_posix()


def _keep_collision_rank(
    item: dict[str, Any], *, version_id: int, keep_stem: str | None
) -> tuple[Any, ...]:
    stem = str(item["stem"])
    name = Path(stem).name
    canonical = collision_base_name(stem, version_id) == name
    return (
        0 if item.get("hasWeight") else 1,
        0 if canonical else 1,
        0 if keep_stem and stem == keep_stem else 1,
        len(name),
        stem,
    )


def collapse_stem_collisions(
    out_dir: Path,
    model_id: int | None = None,
    version_id: int | None = None,
    *,
    local_blake3: set[str] | None = None,
    local_versions: set[int] | None = None,
    local_stems: set[str] | None = None,
    index_lock: threading.Lock | None = None,
    keep_stem: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """
    Delete unique_stem duplicates of the same ModelId+VersionId in one folder.

    Keeps the canonical basename (no ``-v{id}`` suffix) when it has a weight.
    Does not touch distinct filenames that share a version (multi-file versions).
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    want_model = int(model_id) if model_id is not None else None
    want_version = int(version_id) if version_id is not None else None
    for info_path, data in iter_model_sidecars(out_dir, recursive=True):
        mid = data.get("ModelId")
        vid = data.get("VersionId")
        try:
            mid_i = int(mid)
            vid_i = int(vid)
        except (TypeError, ValueError):
            continue
        if want_model is not None and mid_i != want_model:
            continue
        if want_version is not None and vid_i != want_version:
            continue
        stem = relative_pair_stem(out_dir, info_path, cm_info=True)
        base = collision_base_name(stem, vid_i)
        key = (mid_i, vid_i, _stem_parent_key(stem), base.lower())
        wp = weight_path_for_stem(out_dir, stem)
        has_weight = False
        if wp is not None:
            try:
                has_weight = wp.is_file() and wp.stat().st_size > 0
            except OSError:
                has_weight = False
        blake3 = (data.get("Hashes") or {}).get("BLAKE3")
        groups.setdefault(key, []).append(
            {
                "stem": stem,
                "modelId": mid_i,
                "versionId": vid_i,
                "blake3": str(blake3).upper() if blake3 else None,
                "infoPath": info_path,
                "hasWeight": has_weight,
            }
        )

    removed: list[dict[str, Any]] = []
    for (_mid, vid_i, _parent, _base), items in groups.items():
        if len(items) < 2:
            continue
        keep = min(
            items,
            key=lambda it: _keep_collision_rank(
                it, version_id=int(vid_i), keep_stem=keep_stem
            ),
        )
        keep_stem_name = str(keep["stem"])
        for item in items:
            stem = str(item["stem"])
            if stem == keep_stem_name:
                continue
            item = {**item, "keepStem": keep_stem_name}
            if not dry_run:
                delete_stem_bundle(out_dir, stem)
                leftover = out_dir / f"{stem}.cm-info.json"
                if leftover.exists():
                    continue
            removed.append(item)
            if index_lock is not None and local_stems is not None:
                with index_lock:
                    local_stems.discard(Path(stem).name.lower())
    return removed


def find_prune_candidates(
    out_dir: Path,
    model_id: int,
    keep_version_id: int,
) -> list[dict[str, Any]]:
    """Local stems for model_id whose VersionId is not keep_version_id."""
    want = int(model_id)
    keep = int(keep_version_id)
    out: list[dict[str, Any]] = []
    for info_path, data in iter_model_sidecars(out_dir, recursive=True):
        mid = data.get("ModelId")
        if mid is None:
            continue
        try:
            if int(mid) != want:
                continue
            vid = int(data.get("VersionId"))
        except (TypeError, ValueError):
            continue
        if vid == keep:
            continue
        stem = relative_pair_stem(out_dir, info_path, cm_info=True)
        blake3 = (data.get("Hashes") or {}).get("BLAKE3")
        out.append(
            {
                "stem": stem,
                "versionId": vid,
                "blake3": str(blake3).upper() if blake3 else None,
                "infoPath": info_path,
            }
        )
    return out


def delete_stem_bundle(out_dir: Path, stem: str) -> list[Path]:
    """Delete weight, cm-info, previews, and download temps for stem."""
    removed: list[Path] = []
    paths: list[Path] = [
        out_dir / f"{stem}.cm-info.json",
        out_dir / f"{stem}.swarm.json",
        out_dir / f"{stem}.preview.download",
        out_dir / f"{stem}.preview.download.partial",
    ]
    for ext in WEIGHT_EXTENSIONS:
        paths.append(out_dir / f"{stem}{ext}")
        paths.append(out_dir / f"{stem}{ext}.partial")
    wp = weight_path_for_stem(out_dir, stem)
    if wp is not None and wp not in paths:
        paths.append(wp)
    for p in iter_preview_paths(out_dir, stem):
        if p not in paths:
            paths.append(p)
    for p in paths:
        try:
            if p.exists() or p.is_symlink():
                p.unlink(missing_ok=True)
                removed.append(p)
        except OSError:
            continue
    return removed


def prune_old_versions(
    out_dir: Path,
    model_id: int,
    keep_version_id: int,
    *,
    local_blake3: set[str],
    local_versions: set[int],
    local_stems: set[str],
    index_lock: threading.Lock,
) -> list[dict[str, Any]]:
    """
    Delete older ModelId stems and drop them from the in-memory index.
    Returns the candidate dicts that were pruned (best-effort deletes).
    """
    candidates = find_prune_candidates(out_dir, model_id, keep_version_id)
    pruned: list[dict[str, Any]] = []
    for cand in candidates:
        stem = str(cand["stem"])
        delete_stem_bundle(out_dir, stem)
        info = out_dir / f"{stem}.cm-info.json"
        if info.exists():
            continue
        pruned.append(cand)
        with index_lock:
            local_stems.discard(Path(stem).name.lower())
            try:
                local_versions.discard(int(cand["versionId"]))
            except (TypeError, ValueError, KeyError):
                pass
            b3 = cand.get("blake3")
            if b3:
                local_blake3.discard(str(b3).upper())
    return pruned
