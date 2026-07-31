"""Prune older local versions of the same ModelId (latest-only library)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

from civitmatrix.indexer import iter_cm_info_paths, relative_pair_stem
from civitmatrix.preview_media import find_preview_path


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
        out_dir / f"{stem}.safetensors",
        out_dir / f"{stem}.safetensors.partial",
        out_dir / f"{stem}.cm-info.json",
        out_dir / f"{stem}.swarm.json",
        out_dir / f"{stem}.preview.download",
        out_dir / f"{stem}.preview.download.partial",
    ]
    preview = find_preview_path(out_dir, stem)
    if preview is not None:
        paths.append(preview)
    for p in out_dir.glob(f"{stem}.preview.*"):
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
