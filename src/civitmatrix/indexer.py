from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SAFE_NAME_RE = re.compile(r"[^\w.\-@()+\[\] ]+", re.UNICODE)
WS_RE = re.compile(r"\s+")
CM_INFO_SUFFIX = ".cm-info.json"

# Indexed / resolvable weight extensions (order = preference for lookup).
WEIGHT_EXTENSIONS = (".safetensors", ".gguf", ".sft")
KNOWN_WEIGHT_SUFFIXES = frozenset(WEIGHT_EXTENSIONS)


def sanitize_stem(name: str, max_len: int = 120) -> str:
    name = name.strip()
    lower = name.lower()
    for suf in WEIGHT_EXTENSIONS:
        if lower.endswith(suf):
            name = name[: -len(suf)]
            break
    name = SAFE_NAME_RE.sub("_", name)
    name = WS_RE.sub(" ", name).strip(" ._")
    if not name:
        name = "model"
    return name[:max_len]


def is_indexed_weight_name(remote_name: str) -> bool:
    """True when the remote file uses a suffix ``iter_weight_paths`` can see."""
    suffix = Path(remote_name or "").suffix.lower()
    return suffix in KNOWN_WEIGHT_SUFFIXES


def weight_suffix_from_name(remote_name: str) -> str:
    """
    Destination extension from a remote file name.

    Prefer known weight suffixes; otherwise keep a short alphanumeric suffix
    that looks like an extension; default ``.safetensors``.
    """
    suffix = Path(remote_name or "").suffix
    if not suffix:
        return ".safetensors"
    lower = suffix.lower()
    if lower in KNOWN_WEIGHT_SUFFIXES:
        return lower
    body = suffix[1:] if suffix.startswith(".") else suffix
    if body and len(suffix) <= 12 and body.replace("_", "").isalnum():
        return lower if suffix.startswith(".") else f".{lower}"
    return ".safetensors"


def weight_path_for_stem(out_dir: Path, stem: str) -> Path | None:
    """
    Resolve the weight file beside ``stem`` (may be nested, e.g. ``char/foo``).

    Prefers ``.safetensors``, then ``.gguf``, then ``.sft``.
    """
    for ext in WEIGHT_EXTENSIONS:
        p = out_dir / f"{stem}{ext}"
        if p.is_file():
            return p
    return None


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
    ``weight_path_for_stem`` / ``.cm-info.json``.
    """
    name = cm_info_basename_stem(path) if cm_info else path.stem
    rel_parent = path.parent.relative_to(out_dir)
    if rel_parent == Path("."):
        return name
    return f"{rel_parent.as_posix()}/{name}"


def iter_library_files(out_dir: Path, *, recursive: bool = False):
    """Walk physical directories once, following aliases without following cycles.

    Keep paths relative to the requested root so sibling weight/sidecar pairing
    works for an external directory reached through a library symlink.
    """
    seen: set[tuple[int, int]] = set()
    pending = [out_dir]
    while pending:
        directory = pending.pop()
        try:
            stat = directory.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity in seen:
                continue
            seen.add(identity)
            with os.scandir(directory) as scan:
                entries = sorted(scan, key=lambda e: e.name.lower())
        except OSError:
            continue
        children = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=True):
                    if recursive:
                        children.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=True):
                    yield Path(entry.path)
            except OSError:
                continue
        pending.extend(reversed(children))


def iter_weight_paths(out_dir: Path, *, recursive: bool = False) -> list[Path]:
    """List known weight files under ``out_dir`` (flat or recursive).

    Scans ``WEIGHT_EXTENSIONS`` (``.safetensors``, ``.gguf``, ``.sft``). When
    several extensions share a stem, the earlier extension wins so skip/heal
    pairing matches ``weight_path_for_stem``.
    """
    if not out_dir.is_dir():
        return []
    by_key: dict[tuple[Path, str], Path] = {}
    files = list(iter_library_files(out_dir, recursive=recursive))
    for ext in WEIGHT_EXTENSIONS:
        for p in files:
            if not p.is_file() or not _is_under(out_dir, p):
                continue
            if p.suffix.lower() != ext:
                continue
            key = (p.parent, p.stem)
            if key not in by_key:
                by_key[key] = p
    return sorted(by_key.values(), key=lambda p: str(p).lower())


def iter_cm_info_paths(out_dir: Path, *, recursive: bool = False) -> list[Path]:
    """List ``*.cm-info.json`` under ``out_dir`` (flat glob or recursive rglob)."""
    if not out_dir.is_dir():
        return []
    paths = iter_library_files(out_dir, recursive=recursive)
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

    Skip sets only include *complete* installs: non-empty weight
    (``.safetensors`` / ``.gguf`` / ``.sft``) plus a matching ``*.cm-info.json`` that has
    both VersionId and Hashes.BLAKE3.
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


def cm_install_identity(data: dict[str, Any]) -> tuple[Any, ...] | None:
    """Publisher/file identity for non-destructive skip decisions.

    This deliberately does not infer an author or a file variant from a hash.
    Deletion callers additionally verify the actual payload before cleanup.
    """
    try:
        mid, vid, fid = (int(data[k]) for k in ("ModelId", "VersionId", "RemoteFileId"))
        if min(mid, vid, fid) <= 0:
            return None
        author, base, kind, name = (data.get(k) for k in
            ("AuthorUsername", "BaseModel", "ModelType", "RemoteFileName"))
        if any(not isinstance(v, str) or not v.strip() for v in (author, base, kind, name)):
            return None
        for field in ('RemoteFileType', 'FileType'):
            value = data.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                return None
        metadata = data.get("FileMetadata")
        if (not isinstance(metadata, dict)
                or not isinstance(metadata.get("format"), str)
                or not metadata["format"].strip()
                or any(metadata.get(k) is not None and not isinstance(metadata[k], str)
                       for k in ("fp", "size"))):
            return None
        return (mid, vid, author.strip().casefold(), base.strip(), kind.strip(),
                fid, name, str(metadata["format"]).casefold(),
                metadata.get("fp"), metadata.get("size"), Path(name).suffix.lower())
    except (KeyError, TypeError, ValueError):
        return None


def cm_source_record(data: dict[str, Any]) -> tuple[Any, ...] | None:
    """Exact publication candidate key, never installed/deletion authority."""
    if not isinstance(data, dict) or type(data.get('Source')) is not int or data['Source'] != 0:
        return None
    # Optional type evidence may be absent/null, but never training data in a
    # model-shaped trench coat. Validate before any candidate dictionary lookup.
    for field in ('RemoteFileType', 'FileType'):
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or value != 'Model'):
            return None
    ids = []
    for field in ('ModelId', 'VersionId', 'RemoteFileId'):
        value = data.get(field)
        if not (type(value) is int or isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value)):
            return None
        if int(value) <= 0:
            return None
        ids.append(int(value))
    fields = [data.get(k) for k in ('RemoteFileName', 'BaseModel', 'ModelType')]
    meta = data.get('FileMetadata')
    # Unknown variant knobs are not decoration; same bytes are no damn proof
    # that a different publication was requested. Only the API's literal False
    # endpoint default is inert; 0 and "false" don't get a fake moustache.
    if not isinstance(meta, dict) or set(meta) - {'format', 'fp', 'size', 'isRequired'}:
        return None
    if 'isRequired' in meta and meta['isRequired'] is not False:
        return None
    fields.append(meta.get('format'))
    if any(not isinstance(v, str) or not v.strip() for v in fields):
        return None
    # Missing/null optional variant evidence stays unknown, not a guessed bf16.
    # Keep the slots: unknown versus known must still be different damn keys.
    optional = [meta.get(k) for k in ('fp', 'size')]
    if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in optional):
        return None
    fields.extend(optional)
    name = str(fields[0])
    if Path(name).name != name or '/' in name or '\\' in name or not is_indexed_weight_name(name):
        return None
    return (*ids, *fields)


class LocalInstallIdentities(dict):
    """All payload revisions and their cheaply indexed complete bundle records."""

    def __init__(self):
        super().__init__()
        self.records: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        # Candidates are not receipts. No publisher-shaped fake moustache here.
        self.source_records: dict[tuple[Any, ...], list[dict[str, Any]]] = {}

    def add_source_bundle(self, path, stem, data):
        key = cm_source_record(data)
        if key is not None:
            rows = self.source_records.setdefault(key, [])
            rows[:] = [row for row in rows if row['path'] != path]
            rows.append({'path': path, 'stem': stem, 'data': data})

    def add_bundle(self, identity, digest, path, stem, data):
        self.setdefault(identity, set()).add(digest)
        rows = self.records.setdefault(identity, [])
        rows[:] = [row for row in rows if row['path'] != path]
        rows.append({'path': path, 'stem': stem, 'data': data})


def load_local_install_identities(out_dir: Path) -> LocalInstallIdentities:
    """Index every complete revision without hashing payloads at startup."""
    identities = LocalInstallIdentities()
    for path in iter_cm_info_paths(out_dir, recursive=True):
        stem = relative_pair_stem(out_dir, path, cm_info=True)
        weight = weight_path_for_stem(out_dir, stem)
        try:
            if weight is None or weight.stat().st_size <= 0:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            identities.add_source_bundle(path, stem, data)
            identity = cm_install_identity(data)
            hashes = data.get("Hashes")
            digest = hashes.get("BLAKE3") if isinstance(hashes, dict) else None
            if identity is not None and digest:
                identities.add_bundle(identity, str(digest).upper(), path, stem, data)
        except (OSError, ValueError, TypeError):
            continue
    return identities


def load_local_model_max_versions(
    out_dir: Path, *, recursive: bool = True, by_base: bool = False
) -> dict[Any, int]:
    """
    Return ModelId → max local VersionId from complete weight + cm-info pairs.

    Same completeness rules as ``load_local_index`` (non-empty weight beside a
    sidecar with both VersionId and Hashes.BLAKE3). Used by ``--update-only``.
    """
    # The unscoped form is retained for consumers displaying legacy summaries.
    # Download decisions always request by_base=True, including "All" runs.
    max_by_model: dict[Any, int] = {}
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
        base = data.get("BaseModel")
        if by_base and (not isinstance(base, str) or not base.strip()):
            continue
        key = (model_id, base.strip()) if by_base else model_id
        prev = max_by_model.get(key)
        if prev is None or version_id > prev:
            max_by_model[key] = version_id
    return max_by_model


def update_only_skip_reason(
    model_id: int | None,
    remote_version_id: int,
    local_max_versions: dict[Any, int],
    *,
    base_model: str | None = None,
) -> str | None:
    """
    Decide whether ``--update-only`` should skip this model/version.

    Returns ``skip_not_installed``, ``skip_uptodate``, or None to proceed.
    """
    if model_id is None:
        return "skip_not_installed"
    key = (int(model_id), base_model.strip()) if base_model else int(model_id)
    local_max = local_max_versions.get(key)
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
