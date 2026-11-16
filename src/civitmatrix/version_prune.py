"""Prune older local versions of the same ModelId (latest-only library)."""

from __future__ import annotations

import json
import re
import stat
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.indexer import (
    WEIGHT_EXTENSIONS,
    cm_install_identity,
    iter_cm_info_paths,
    load_local_index,
    relative_pair_stem,
    weight_path_for_stem,
)
from civitmatrix.preview_media import iter_preview_paths
from civitmatrix.release_policy import proven_supersession_reason


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
        0 if keep_stem and stem == keep_stem else 1,
        0 if canonical else 1,
        len(name),
        stem,
    )


def _verified_identity(out_dir: Path, stem: str, data: dict[str, Any]):
    """Complete publisher/file identity backed by the actual local payload."""
    try:
        if any(part.casefold() in {'my loras', 'myloras'}
               for part in (out_dir / stem).resolve().parts):
            return None
        info_path = out_dir / f"{stem}.cm-info.json"
        info_stat = info_path.lstat()
        if not stat.S_ISREG(info_stat.st_mode) or info_stat.st_nlink != 1:
            return None
        info_bytes = info_path.read_bytes()
        current = json.loads(info_bytes)
        if current != data:
            return None
        identity = cm_install_identity(data)
        if identity is None:
            return None
        hashes = data.get("Hashes")
        recorded = hashes.get("BLAKE3") if isinstance(hashes, dict) else None
        wp = weight_path_for_stem(out_dir, stem)
        if (not recorded or wp is None or wp.is_symlink()
                or (out_dir / f"{stem}.cm-info.json").is_symlink()
                or wp.stat().st_size <= 0):
            return None
        # A bundle sharing its stem with another format is ambiguous.
        if sum((out_dir / f"{stem}{ext}").is_file() for ext in WEIGHT_EXTENSIONS) != 1:
            return None
        weight_stat = wp.lstat()
        if not stat.S_ISREG(weight_stat.st_mode) or weight_stat.st_nlink != 1:
            return None
        actual = file_blake3_hex(wp).upper()
        def unchanged(before, after):
            return (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns) == (
                    after.st_dev, after.st_ino, after.st_mode, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns)
        # Hashing can take minutes. Publisher metadata and payload must both
        # remain the same objects with the same contents throughout the read.
        # Replacement goblin: "Same path though." That is not ID, you little shit.
        if (not unchanged(weight_stat, wp.lstat())
                or not unchanged(info_stat, info_path.lstat())
                or info_path.read_bytes() != info_bytes
                or not unchanged(info_stat, info_path.lstat())):
            return None
        if actual != str(recorded).upper():
            return None
        if wp.suffix.lower() != identity[-1]:
            return None
        return identity, actual
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _verified_current_bundle(out_dir: Path, stem: str):
    try:
        data = json.loads((out_dir / f"{stem}.cm-info.json").read_bytes())
        if not isinstance(data, dict):
            return None
        return _verified_identity(out_dir, stem, data)
    except (OSError, ValueError, TypeError):
        return None


def _verified_pair_unchanged(out_dir: Path, first, second) -> bool:
    """Bind BOTH bundles across both hash reads, including all weight variants.

    Rehashing one bundle can take long enough for the already-checked partner
    to change. Independent successful reads are not a stable deletion boundary.
    """
    def binding(stem):
        paths = [out_dir / f'{stem}.cm-info.json']
        paths.extend(out_dir / f'{stem}{ext}' for ext in WEIGHT_EXTENSIONS)
        result = []
        for path in paths:
            try:
                current = path.lstat()
            except FileNotFoundError:
                result.append(None)
            else:
                result.append((current.st_dev, current.st_ino, current.st_mode,
                               current.st_nlink, current.st_size,
                               current.st_mtime_ns, current.st_ctime_ns))
        return tuple(result)
    try:
        bundles = (first, second)
        before = [binding(stem) for stem, _, _ in bundles]
        if any(expected is None or _verified_identity(out_dir, stem, data) != expected
               for stem, data, expected in bundles):
            return False
        return before == [binding(stem) for stem, _, _ in bundles]
    except (OSError, ValueError, TypeError):
        return False


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
    allow_delete: bool = False,
    sidecars: Iterable[tuple[Path, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Propose exact duplicate bundles; deleting needs explicit authorization.

    Different versions, authors, bases, formats or remote files never qualify.
    Unknown identity or bytes inconsistent with metadata means preserve.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    rows = sidecars if sidecars is not None else iter_model_sidecars(out_dir, recursive=True)
    for info_path, data in rows:
        stem = relative_pair_stem(out_dir, info_path, cm_info=True)
        try:
            mid, vid = int(data.get("ModelId")), int(data.get("VersionId"))
        except (TypeError, ValueError):
            continue
        if model_id is not None and mid != int(model_id):
            continue
        if version_id is not None and vid != int(version_id):
            continue
        # Only hash candidate collision groups, not every unrelated large model.
        identity = cm_install_identity(data)
        if identity is None:
            continue
        key = (identity, json.dumps(data.get("FileMetadata"), sort_keys=True),
               data.get("RemoteFileType"), data.get("FileType"))
        groups.setdefault(key, []).append({"stem": stem, "modelId": mid,
            "versionId": vid, "infoPath": info_path, "data": data, "hasWeight": True})
    candidates: list[dict[str, Any]] = []
    for (identity, _, _, _), items in groups.items():
        vid = identity[1]
        if len(items) < 2:
            continue
        verified: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for item in items:
            result = _verified_identity(out_dir, item["stem"], item["data"])
            if result is not None:
                identity, actual = result
                item["blake3"] = actual
                verified.setdefault((identity, actual), []).append(item)
        for expected, same in verified.items():
            keep = min(same, key=lambda it: _keep_collision_rank(it, version_id=vid, keep_stem=keep_stem))
            for item in same:
                if item is keep:
                    continue
                candidate = {k: v for k, v in item.items() if k != "data"}
                candidate.update(keepStem=keep["stem"], deleted=False)
                if allow_delete and not dry_run:
                    # Recheck survivor immediately before any destructive operation.
                    if not _verified_pair_unchanged(out_dir,
                            (keep['stem'], keep['data'], expected),
                            (item['stem'], item['data'], expected)):
                        continue
                    delete_stem_bundle(out_dir, item["stem"], preserve_temps=True)
                    if (out_dir / f'{item["stem"]}.cm-info.json').exists():
                        continue
                    candidate["deleted"] = True
                    if index_lock is not None and local_stems is not None:
                        with index_lock:
                            local_stems.discard(Path(item["stem"]).name.lower())
                candidates.append(candidate)
    return candidates


def find_prune_candidates(
    out_dir: Path,
    model_id: int,
    keep_version_id: int,
    *,
    base_model: str | None = None,
    keep_stem: str | None = None,
) -> list[dict[str, Any]]:
    """Plan genuinely older releases in a verified survivor's publisher/base scope."""
    rows = list(iter_model_sidecars(out_dir, recursive=True))
    survivors = []
    for path, data in rows:
        if data.get("ModelId") != model_id or data.get("VersionId") != keep_version_id:
            continue
        stem = relative_pair_stem(out_dir, path, cm_info=True)
        if keep_stem and stem != keep_stem:
            continue
        if base_model and data.get("BaseModel") != base_model:
            continue
        verified = _verified_identity(out_dir, stem, data)
        if verified is not None:
            survivors.append((stem, verified, data))
    # An unscoped mixed-base survivor set does not authorize a cleanup.
    scopes = {(v[0], v[2], v[3], v[4], v[7], v[10])
              for _, (v, _), _ in survivors}
    if len(scopes) != 1:
        return []
    scope = next(iter(scopes))
    candidates = []
    for path, data in rows:
        try:
            if int(data.get("ModelId")) != int(model_id) or not 0 < int(data.get("VersionId")) < int(keep_version_id):
                continue
        except (TypeError, ValueError):
            continue
        stem = relative_pair_stem(out_dir, path, cm_info=True)
        matches = [(keep, result, keep_data, reason)
                   for keep, result, keep_data in survivors
                   if (reason := proven_supersession_reason(data, keep_data))]
        if not matches:
            continue
        verified = _verified_identity(out_dir, stem, data)
        if verified is None:
            continue
        identity, actual = verified
        if (identity[0], identity[2], identity[3], identity[4], identity[7], identity[10]) != scope:
            continue
        keep_stem_name, (keep_identity, keep_hash), keep_data, reason = matches[0]
        candidates.append({"stem": stem, "versionId": identity[1], "blake3": actual,
                           "infoPath": path, "identity": identity,
                           "keepStem": keep_stem_name, "keepIdentity": keep_identity,
                           "keepBlake3": keep_hash, "reason": reason,
                           "data": data, "keepData": keep_data})
    return candidates


def delete_stem_bundle(out_dir: Path, stem: str, *, preserve_temps: bool = False) -> list[Path]:
    """Delete bundle; automatic cleanup preserves all in-progress download files."""
    removed: list[Path] = []
    weights = [out_dir / f"{stem}{ext}" for ext in WEIGHT_EXTENSIONS]
    # Removing metadata first can orphan a weight when its unlink is denied.
    # No companion is touched until all actual weight paths were removed.
    # Metadata is the receipt. Do not shred it while the weight is still here.
    paths: list[Path] = list(weights)
    paths.extend([out_dir / f"{stem}.cm-info.json", out_dir / f"{stem}.swarm.json"])
    for ext in WEIGHT_EXTENSIONS:
        if not preserve_temps:
            paths.append(out_dir / f"{stem}{ext}.partial")
            paths.append(out_dir / f"{stem}{ext}.heal-new")
            paths.append(out_dir / f"{stem}{ext}.heal-new.partial")
    if not preserve_temps:
        paths.extend([out_dir / f"{stem}.preview.download",
                      out_dir / f"{stem}.preview.download.partial"])
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
            if p in weights:
                return removed
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
    allow_delete: bool = False,
    base_model: str | None = None,
    keep_stem: str | None = None,
) -> list[dict[str, Any]]:
    """
    Delete older ModelId stems and drop them from the in-memory index.
    Returns the candidate dicts that were pruned (best-effort deletes).
    """
    if not allow_delete:
        return []
    candidates = find_prune_candidates(out_dir, model_id, keep_version_id,
                                     base_model=base_model, keep_stem=keep_stem)
    pruned: list[dict[str, Any]] = []
    for cand in candidates:
        stem = str(cand["stem"])
        if not _verified_pair_unchanged(out_dir,
                (stem, cand['data'], (cand['identity'], cand['blake3'])),
                (cand['keepStem'], cand['keepData'], (cand['keepIdentity'], cand['keepBlake3']))):
            continue
        delete_stem_bundle(out_dir, stem, preserve_temps=True)
        info = out_dir / f"{stem}.cm-info.json"
        if info.exists():
            continue
        pruned.append(cand)
    if pruned:
        remaining_hashes, remaining_versions, remaining_stems = load_local_index(out_dir)
        with index_lock:
            # A hash or basename may still be owned by another retained bundle.
            # Cleanup goblin: delete one bundle, not the whole family's alibi.
            for cand in pruned:
                stem = Path(str(cand["stem"])).name.lower()
                if stem not in remaining_stems:
                    local_stems.discard(stem)
                if cand["versionId"] not in remaining_versions:
                    local_versions.discard(cand["versionId"])
                if cand.get("blake3") not in remaining_hashes:
                    local_blake3.discard(str(cand.get("blake3")).upper())
    return pruned
