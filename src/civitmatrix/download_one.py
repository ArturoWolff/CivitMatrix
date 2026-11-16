from __future__ import annotations

import json
import hashlib
import re
import stat
import functools
import math
import threading
import time
from pathlib import Path
from typing import Any

from civitmatrix.client import CivitClient
from civitmatrix.disk_guard import (
    below_floor,
    disk_status,
    file_size_bytes,
    format_bytes,
)
from civitmatrix.indexer import (
    LocalInstallIdentities,
    weight_path_for_stem,
    cm_install_identity,
    cm_source_record,
    load_local_install_identities,
    pick_matching_version,
    pick_primary_file,
    unique_stem,
    update_only_skip_reason,
    is_indexed_weight_name,
    weight_suffix_from_name,
)
from civitmatrix.job_state import JobState
from civitmatrix.logging_io import RunLogger, utc_now
from civitmatrix.preview_media import finalize_preview_file, pick_preview_url
from civitmatrix.sm_sidecars import build_cm_info, build_swarm_json, sort_hints_from_tags
from civitmatrix.verify_blake3 import (
    remote_blake3_from_file_info,
    verify_weight_blake3,
    file_identity_matches_hash_lookup,
)
from civitmatrix.version_prune import collapse_stem_collisions, prune_old_versions

_index_lock = threading.Lock()
_STALE_RECEIPT_TTL_SECONDS = 24 * 60 * 60

# Serialize one model's installs without treating an in-flight reservation as
# an installed file. Different models can still download concurrently.
_model_locks_guard = threading.Lock()
_model_locks: dict[tuple[str, str], tuple[threading.RLock, int]] = {}


def _serialize_model_install(fn):
    @functools.wraps(fn)
    def serialized(client, model, out_dir, *args, **kwargs):
        key = (str(out_dir.resolve()), str(model.get("id")))
        with _model_locks_guard:
            lock, users = _model_locks.get(key, (threading.RLock(), 0))
            _model_locks[key] = (lock, users + 1)
        try:
            with lock:
                return fn(client, model, out_dir, *args, **kwargs)
        finally:
            with _model_locks_guard:
                _, users = _model_locks[key]
                if users == 1:
                    del _model_locks[key]
                else:
                    _model_locks[key] = (lock, users - 1)
    return serialized


def _resolve_versions(
    model: dict[str, Any],
    version_ids: list[Any] | None,
    *,
    base_model: str,
    match_base_version: bool,
) -> list[dict[str, Any]]:
    from civitmatrix.indexer import pick_matching_version

    if not version_ids or version_ids == ["latest"]:
        ver = pick_matching_version(
            model, base_model, match_base_version=match_base_version
        )
        return [ver] if ver else []
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for vid in version_ids:
        if vid in ("latest", None, "Latest"):
            for v in _resolve_versions(
                model, ["latest"], base_model=base_model, match_base_version=match_base_version
            ):
                i = int(v["id"])
                if i not in seen:
                    seen.add(i)
                    out.append(v)
            continue
        for v in model.get("modelVersions") or []:
            try:
                if int(v.get("id")) == int(vid):
                    i = int(vid)
                    if i not in seen:
                        seen.add(i)
                        out.append(v)
                    break
            except (TypeError, ValueError):
                continue
    return out


def _model_fields(model: dict[str, Any], version: dict[str, Any] | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "modelId": model.get("id"),
        "modelName": model.get("name"),
    }
    if version is not None:
        fields["versionId"] = version.get("id")
        fields["baseModel"] = version.get("baseModel")
    return fields


def _remote_validator(client, url):
    """Bounded, credential-safe HEAD probe; only strong validators are usable."""
    try:
        resolved = client._download_url(url)
        if not isinstance(resolved, str):
            resolved = url
        timeout = client.timeout if isinstance(client.timeout, (int, float)) else 10
        with client.download_session.head(resolved, timeout=min(timeout, 10),
                allow_redirects=True, headers={'Accept-Encoding': 'identity'}) as response:
            if response.status_code != 200:
                return None
            etag = response.headers.get('ETag')
            size = response.headers.get('Content-Length')
            if (not isinstance(etag, str) or len(etag) < 2 or not etag.startswith('"')
                    or not etag.endswith('"') or not isinstance(size, str) or not size.isdigit()):
                return None
            return {'etag': etag, 'size': int(size)}
    except Exception:
        return None


def _acceptance_receipt(identity, advertised, actual):
    return {'schema': 1, 'identity': list(identity),
            'advertisedBlake3': str(advertised).upper(),
            'actualBlake3': str(actual).upper(), 'verifiedAt': time.time()}


def _existing_verified_bundle(out_dir, row, identity, advertised, *, verify_bytes=True):
    """Validate only the selected bundle, never every weight in the library."""
    try:
        path = row['path']
        data = json.loads(path.read_text(encoding='utf-8'))
        if cm_install_identity(data) != identity:
            return None
        actual = (data.get('Hashes') or {}).get('BLAKE3')
        if not actual or not advertised:
            return None
        actual = str(actual).upper()
        receipt = data.get('CivitMatrixAcceptance')
        if actual != str(advertised).upper():
            expected = _acceptance_receipt(identity, advertised, actual)
            expected.pop('verifiedAt')
            binding = ({k: v for k, v in receipt.items()
                        if k not in ('remoteValidator', 'verifiedAt')}
                       if isinstance(receipt, dict) else None)
            if binding != expected or type(receipt.get('schema')) is not int:
                return None
            verified_at = receipt.get('verifiedAt')
            if (not isinstance(verified_at, (int, float)) or isinstance(verified_at, bool)
                    or not 0 <= verified_at <= time.time()
                    or not math.isfinite(verified_at)):
                return None
            # Without an observable validator, an immediate silent remote
            # change is impossible to detect. Bound stale-API trust to 24h;
            # expiry forces a fresh download and full payload verification.
            # Ordinary advertised-hash matches retain their trusted fast path.
            if (receipt.get('remoteValidator') is None
                    and time.time() - verified_at >= _STALE_RECEIPT_TTL_SECONDS):
                return None
        weight = weight_path_for_stem(out_dir, row['stem'])
        if (weight is None or weight.is_symlink() or path.is_symlink()
                or weight.suffix.lower() != identity[-1] or weight.stat().st_size <= 0):
            return None
        if not verify_bytes and actual == str(advertised).upper():
            return data, actual
        before = weight.stat()
        status, digest, _ = verify_weight_blake3(weight, actual)
        if status != 'ok' or before != weight.stat() or json.loads(path.read_text()) != data:
            return None
        return data, digest
    except (OSError, ValueError, TypeError):
        return None


def _verified_source_candidate(out_dir, row, key, hashes):
    """Fresh dual-digest proof, not an anonymous acceptance receipt."""
    from civitmatrix.hash_blake3 import file_blake3_hex

    try:
        if not isinstance(hashes, dict) or any(
                not isinstance(hashes.get(k), str) or not re.fullmatch(r'[a-fA-F0-9]{64}', hashes[k])
                for k in ('SHA256', 'BLAKE3')):
            return False
        info = row['path']
        weight = weight_path_for_stem(out_dir, row['stem'])
        if weight is None:
            return False
        # Refuse hardlinks and symlinked parents too. Alias goblin: nice fucking try.
        for path in (info, weight):
            if path.resolve() != path.absolute() or not path.is_relative_to(out_dir):
                return False
            binding = path.lstat()
            if not stat.S_ISREG(binding.st_mode) or binding.st_nlink != 1:
                return False
        weight_before, info_before = weight.stat(), info.stat()
        data = json.loads(info.read_text(encoding='utf-8'))
        if (cm_source_record(data) != key or data.get('AuthorUsername') is not None
                or weight_before.st_size <= 0 or weight.suffix.lower() != Path(key[3]).suffix.lower()):
            return False
        b3 = file_blake3_hex(weight)
        with weight.open('rb') as stream:
            sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        return (b3.upper() == hashes['BLAKE3'].upper()
                and sha.upper() == hashes['SHA256'].upper()
                and weight.stat() == weight_before and info.stat() == info_before
                and weight.lstat() == weight_before and info.lstat() == info_before
                and all(path.resolve() == path.absolute() and path.stat().st_nlink == 1
                        for path in (weight, info)))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def maybe_prune_old_versions(
    *,
    enabled: bool,
    dry_run: bool,
    out_dir: Path,
    model: dict[str, Any],
    version: dict[str, Any],
    version_id: int,
    local_blake3: set[str],
    local_versions: set[int],
    local_stems: set[str],
    logger: RunLogger,
    job: JobState | None,
    keep_stem: str = "",
    allow_deletions: bool = False,
    local_install_identities: LocalInstallIdentities | dict[tuple[Any, ...], Any] | None = None,
    library_cleanup: bool = True,
) -> int:
    if dry_run or not library_cleanup:
        return 0
    mid = model.get("id")
    if mid is None:
        return 0
    try:
        model_id = int(mid)
    except (TypeError, ValueError):
        return 0
    # A verified survivor is mandatory even when callers request cleanup.
    try:
        info = out_dir / f'{keep_stem}.cm-info.json'
        data = json.loads(info.read_text(encoding='utf-8'))
        survivor_identity = cm_install_identity(data)
        digest = (data.get('Hashes') or {}).get('BLAKE3')
        if not keep_stem or survivor_identity is None or survivor_identity[:2] != (model_id, version_id):
            return 0
    except (OSError, ValueError, TypeError):
        return 0
    indexed = (local_install_identities if isinstance(local_install_identities, LocalInstallIdentities)
               else load_local_install_identities(out_dir))
    # Metadata-only opportunity filter: no second weight means no cleanup scan.
    with _index_lock:
        others = [row for ident, rows in indexed.records.items()
                  if ident[0] == model_id and ident[2:5] == survivor_identity[2:5]
                  for row in rows if row['stem'] != keep_stem]
    if not others or not _existing_verified_bundle(
            out_dir, {'path': info, 'stem': keep_stem}, survivor_identity, digest):
        return 0
    n = 0
    if enabled:
        pruned = prune_old_versions(
            out_dir,
            model_id,
            version_id,
            local_blake3=local_blake3,
            local_versions=local_versions,
            local_stems=local_stems,
            index_lock=_index_lock,
            allow_delete=True,
            base_model=version.get("baseModel"),
            keep_stem=keep_stem or None,
        )
        for cand in pruned:
            logger.log(
                f"PRUNE old version model={model_id} ver={cand.get('versionId')} "
                f"stem={cand.get('stem')} keep={version_id}"
            )
            if job:
                job.emit(
                    "prune_old_version",
                    localStem=cand.get("stem"),
                    oldVersionId=cand.get("versionId"),
                    keepVersionId=version_id,
                    **_model_fields(model, version),
                )
                job.bump("pruned")
        n += len(pruned)
        if pruned and local_install_identities is not None:
            remaining = load_local_install_identities(out_dir)
            with _index_lock:
                for identity in list(local_install_identities):
                    if identity[0] == model_id and identity not in remaining:
                        del local_install_identities[identity]
    collapsed = collapse_stem_collisions(
        out_dir,
        model_id,
        local_blake3=local_blake3,
        local_versions=local_versions,
        local_stems=local_stems,
        index_lock=_index_lock,
        keep_stem=keep_stem or None,
        allow_delete=True,
    )
    for cand in collapsed:
        logger.log(
            f"PRUNE duplicate stem model={model_id} ver={cand.get('versionId')} "
            f"stem={cand.get('stem')} keep={cand.get('keepStem')}"
        )
        if job:
            job.emit(
                "prune_duplicate_stem",
                localStem=cand.get("stem"),
                keepStem=cand.get("keepStem"),
                oldVersionId=cand.get("versionId"),
                keepVersionId=version_id,
                **_model_fields(model, version),
            )
            job.bump("pruned")
        n += 1
    if n and isinstance(local_install_identities, LocalInstallIdentities):
        remaining = load_local_install_identities(out_dir)
        with _index_lock:
            # The scan can predate another model's completion. Same-model
            # installs are serialized; replace only this model's snapshot.
            for identity in list(local_install_identities):
                if identity[0] == model_id:
                    del local_install_identities[identity]
            for identity in list(local_install_identities.records):
                if identity[0] == model_id:
                    del local_install_identities.records[identity]
            for key in list(local_install_identities.source_records):
                if key[0] == model_id:
                    del local_install_identities.source_records[key]
            for key, rows in remaining.source_records.items():
                if key[0] == model_id:
                    local_install_identities.source_records[key] = rows
            for identity, digests in remaining.items():
                if identity[0] == model_id:
                    local_install_identities[identity] = digests
                    local_install_identities.records[identity] = remaining.records[identity]
    return n


@_serialize_model_install
def process_one(
    client: CivitClient,
    model: dict[str, Any],
    out_dir: Path,
    local_blake3: set[str],
    local_versions: set[int],
    local_stems: set[str],
    logger: RunLogger,
    *,
    base_model: str,
    match_base_version: bool,
    dry_run: bool,
    job: JobState | None = None,
    resume: bool = True,
    skip_verify: bool = False,
    disk_floor_bytes: int = 0,
    keep_old_versions: bool = False,
    library_cleanup: bool = True,
    force_version: dict[str, Any] | None = None,
    write_swarm: bool = False,
    update_only: bool = False,
    local_max_versions: dict[Any, int] | None = None,
    allow_deletions: bool = False,
    local_install_identities: LocalInstallIdentities | dict[tuple[Any, ...], Any] | None = None,
) -> str:
    version = force_version or pick_matching_version(
        model, base_model, match_base_version=match_base_version
    )
    if not version:
        logger.fail_with_event(
            job,
            model,
            "no_matching_base_version",
            retryable=False,
            extra={"wantedBaseModel": base_model},
            **_model_fields(model),
        )
        return "no_match"

    file_info = pick_primary_file(version)
    if not file_info:
        logger.fail_with_event(
            job,
            model,
            "no_files",
            retryable=False,
            version=version,
            **_model_fields(model, version),
        )
        return "no_files"

    remote_name = file_info.get("name") or f"model-{int(version['id'])}.safetensors"
    if not is_indexed_weight_name(str(remote_name)):
        logger.fail_with_event(
            job,
            model,
            "no_files",
            retryable=False,
            version=version,
            extra={"detail": f"unsupported weight extension: {remote_name}"},
            **_model_fields(model, version),
        )
        return "no_files"

    blake3 = (file_info.get("hashes") or {}).get("BLAKE3")
    advertised_blake3 = blake3
    version_id = int(version["id"])
    skip_reason: str | None = None
    stem = ""
    metadata = file_info.get("metadata") or {}
    candidate = {
        "ModelId": model.get("id"), "VersionId": version_id,
        "BaseModel": version.get("baseModel"),
        "AuthorUsername": (model.get("creator") or {}).get("username"),
        "ModelType": model.get("type") or "LORA",
        "RemoteFileId": file_info.get("id"), "RemoteFileName": file_info.get("name"),
        "FileMetadata": {"format": metadata.get("format") or "SafeTensor",
                         "fp": metadata.get("fp"), "size": metadata.get("size")},
    }
    identity = cm_install_identity(candidate)
    source_key = cm_source_record({**candidate, 'Source': 0,
                                   'VersionId': version.get('id'),
                                   'ModelType': model.get('type'),
                                   'RemoteFileType': file_info.get('type'),
                                   'FileMetadata': metadata})
    installed = (local_install_identities if isinstance(local_install_identities, LocalInstallIdentities)
                 else load_local_install_identities(out_dir))
    publisher_recovered = False
    publisher_cache_present = False
    if identity is None and not skip_verify:
        from civitmatrix.sm_sidecars import preserve_cached_publisher

        with _index_lock:
            sources = [row['data'] for rows in installed.records.values() for row in rows]
        publisher_cache_present = any(
            cm_install_identity({**candidate, 'AuthorUsername': data.get('AuthorUsername')})
            == cm_install_identity(data) for data in sources)
        recovered = preserve_cached_publisher(candidate, sources)
        recovered_identity = cm_install_identity(recovered)
        if recovered_identity is not None:
            with _index_lock:
                rows = list(installed.records.get(recovered_identity, []))
            for row in rows:
                digest = (row['data'].get('Hashes') or {}).get('BLAKE3')
                if _existing_verified_bundle(out_dir, row, recovered_identity, digest):
                    identity = recovered_identity
                    publisher_recovered = True
                    model = {**model, 'creator': {**(model.get('creator') or {}),
                                                 'username': recovered['AuthorUsername']}}
                    break

    def cleanup_survivor(keep_stem):
        if not skip_verify and library_cleanup and not dry_run:
            maybe_prune_old_versions(
                enabled=not keep_old_versions, dry_run=False, out_dir=out_dir,
                model=model, version=version, version_id=version_id,
                local_blake3=local_blake3, local_versions=local_versions,
                local_stems=local_stems, logger=logger, job=job,
                keep_stem=keep_stem, allow_deletions=allow_deletions,
                library_cleanup=library_cleanup, local_install_identities=installed)

    mid_raw = model.get("id")
    try:
        model_id_int: int | None = int(mid_raw) if mid_raw is not None else None
    except (TypeError, ValueError):
        model_id_int = None

    if update_only:
        max_map = local_max_versions if local_max_versions is not None else {}
        uo_skip = update_only_skip_reason(model_id_int, version_id, max_map,
                                         base_model=version.get("baseModel"))
        if uo_skip:
            if job:
                fields = dict(_model_fields(model, version))
                if uo_skip == "skip_uptodate" and model_id_int is not None:
                    fields["localVersionId"] = max_map.get((model_id_int, version.get("baseModel")))
                job.emit(uo_skip, **fields)
            return uo_skip

    existing_row = None
    if (identity is None and not skip_verify and not publisher_cache_present
            and source_key is not None and candidate.get('AuthorUsername') is None):
        with _index_lock:
            source_rows = list(installed.source_records.get(source_key, []))
        # Any named/malformed publisher blocks anonymous fallback, including an
        # ambiguous cache. We don't fix author roulette by hiding the damn wheel.
        if source_rows and all(row['data'].get('AuthorUsername') is None for row in source_rows):
            for row in source_rows:
                if _verified_source_candidate(out_dir, row, source_key, file_info.get('hashes')):
                    if job:
                        job.emit('skip_source', **_model_fields(model, version))
                    return 'skip_source'
    if identity is not None and not skip_verify:
        for row in installed.records.get(identity, []):
            actual = str((row['data'].get('Hashes') or {}).get('BLAKE3') or '').upper()
            proof = row['data'].get('CivitMatrixAcceptance') or {}
            validator = proof.get('remoteValidator') if isinstance(proof, dict) else None
            if validator is not None and validator != _remote_validator(client, file_info.get('downloadUrl')):
                continue
            # Normal advertised-hash skips retain their cheap metadata path.
            # Stale acceptance receipts always rehash the selected payload.
            if _existing_verified_bundle(out_dir, row, identity, blake3,
                                         verify_bytes=publisher_recovered or actual != str(blake3).upper()):
                existing_row = row
                skip_reason = 'skip_hash'
                break
    with _index_lock:
        if not skip_reason:
            stem = unique_stem(Path(remote_name).stem, version_id, local_stems)
            if not dry_run:
                local_stems.add(stem.lower())

    if skip_reason and existing_row is not None:
        cleanup_survivor(existing_row['stem'])
        if job:
            fields = dict(_model_fields(model, version))
            if skip_reason == "skip_hash" and blake3:
                fields["blake3"] = blake3
            job.emit(skip_reason, **fields)
        return skip_reason

    # The version endpoint selects the release primary, which can differ from
    # our preferred SafeTensor or an explicitly identified file variant.
    download_url = file_info.get("downloadUrl")
    if not isinstance(download_url, str) or not download_url.strip():
        if not dry_run:
            _release_reservation(
                local_blake3, local_versions, local_stems, blake3, version_id, stem
            )
        logger.fail_with_event(
            job, model, "missing_file_download_url", retryable=False,
            version=version, **_model_fields(model, version),
        )
        return "no_files"

    ext = weight_suffix_from_name(str(remote_name))
    weight_path = out_dir / f"{stem}{ext}"
    info_path = out_dir / f"{stem}.cm-info.json"
    preview_tmp = out_dir / f"{stem}.preview.download"
    preview_path: Path | None = None

    preview_url = pick_preview_url(version.get("images") or [])

    tags = [
        t if isinstance(t, str) else t.get("name") for t in (model.get("tags") or [])
    ]

    if dry_run:
        logger.log(
            f"DRY-RUN would download model={model['id']} ver={version_id} -> {weight_path.name}"
        )
        if job:
            job.emit(
                "dry_run",
                localStem=stem,
                remoteFileName=remote_name,
                **_model_fields(model, version),
            )
        return "dry_run"

    # Mid-run disk floor
    if disk_floor_bytes > 0 and below_floor(out_dir, disk_floor_bytes):
        st = disk_status(out_dir)
        if job:
            job.emit(
                "disk_full",
                free=st["free"],
                floor=disk_floor_bytes,
                path=str(out_dir),
                **_model_fields(model, version),
            )
            job.set_meta(diskFree=st["free"])
        logger.fail_with_event(
            job,
            model,
            "disk_full",
            retryable=True,
            version=version,
            extra={"free": st["free"], "floor": disk_floor_bytes},
            event_name="fail",
            **_model_fields(model, version),
        )
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
        return "disk_full"

    need = file_size_bytes(file_info)
    if need is not None:
        free = disk_status(out_dir)["free"]
        if need > free and (disk_floor_bytes <= 0 or free >= disk_floor_bytes):
            if job:
                job.emit(
                    "disk_warn",
                    free=free,
                    estimate=need,
                    floor=disk_floor_bytes,
                    **_model_fields(model, version),
                )
            logger.log(
                f"WARN disk: need ~{format_bytes(need)} free={format_bytes(free)} "
                f"for model={model['id']}"
            )

    try:
        weight_committed = False
        logger.log(f"DOWNLOAD model={model['id']} ver={version_id} -> {weight_path.name}")
        if job:
            job.emit(
                "download_start",
                localStem=stem,
                remoteFileName=remote_name,
                resume=resume,
                **_model_fields(model, version),
            )

        def _dl_event(event: str, fields: dict[str, Any]) -> None:
            if event == "download_resume":
                logger.log(
                    f"RESUME model={model['id']} ver={version_id} "
                    f"offset={fields.get('offset')} -> {weight_path.name}"
                )
            if event == "download_progress" and job:
                job.update_current_progress(
                    bytes_done=fields.get("bytes"),
                    total=fields.get("total"),
                    pct=fields.get("pct"),
                )
            if job:
                # Merge model fields first so progress keys win; avoid duplicate kwargs.
                job.emit(
                    event,
                    **{
                        **_model_fields(model, version),
                        **fields,
                        "localStem": stem,
                    },
                )

        remote_before = _remote_validator(client, download_url) if identity is not None else None
        client.download(
            download_url, weight_path, resume=resume, on_event=_dl_event
        )

        remote_b3 = remote_blake3_from_file_info(file_info) or blake3
        download_binding = weight_path.stat()
        v_status, local_hash, v_reason = verify_weight_blake3(
            weight_path, remote_b3, skip=skip_verify
        )

        # CivitAI sometimes publishes stale BLAKE3/SHA256 while CDN serves the
        # current file; by-hash(local) still resolves to this versionId.
        stale_meta_ok = False
        if v_status not in ("ok", "skipped") and local_hash and not skip_verify:
            try:
                by_hash = client.get_version_by_hash(local_hash)
            except Exception:
                by_hash = None
            if file_identity_matches_hash_lookup(by_hash, identity, local_hash):
                stale_meta_ok = True
                v_status = "ok"
                v_reason = "stale_remote_meta"
                logger.log(
                    f"VERIFY OK (stale API hash) model={model['id']} ver={version_id} "
                    f"stem={stem} local={local_hash}"
                )
                if job:
                    job.emit(
                        "verify_ok_stale_meta",
                        localStem=stem,
                        reason="stale_remote_meta",
                        localBlake3=local_hash,
                        remoteBlake3=blake3,
                        **_model_fields(model, version),
                    )
                blake3 = str(local_hash).upper()
                hashes = dict(file_info.get("hashes") or {})
                hashes["BLAKE3"] = blake3
                file_info = {**file_info, "hashes": hashes}

        if v_status not in ("ok", "skipped") and not skip_verify:
            logger.log(
                f"VERIFY FAIL model={model['id']} ver={version_id} "
                f"stem={stem} reason={v_reason} — retrying download once"
            )
            if job:
                job.emit(
                    "verify_retry",
                    localStem=stem,
                    reason=v_reason,
                    localBlake3=local_hash,
                    remoteBlake3=blake3,
                    **_model_fields(model, version),
                )
            weight_path.unlink(missing_ok=True)
            weight_path.with_suffix(weight_path.suffix + ".partial").unlink(missing_ok=True)
            client.download(
                download_url, weight_path, resume=False, on_event=_dl_event
            )
            download_binding = weight_path.stat()
            v_status, local_hash, v_reason = verify_weight_blake3(
                weight_path, remote_b3, skip=False
            )

        if v_status == "ok":
            weight_committed = True
            if job and not stale_meta_ok:
                job.emit(
                    "verify_ok",
                    localStem=stem,
                    blake3=local_hash,
                    **_model_fields(model, version),
                )
        elif v_status == "skipped":
            weight_committed = True
            if job:
                job.emit(
                    "verify_skipped",
                    localStem=stem,
                    reason=v_reason,
                    **_model_fields(model, version),
                )
        else:
            logger.log(
                f"VERIFY FAIL model={model['id']} ver={version_id} "
                f"stem={stem} reason={v_reason} local={local_hash}"
            )
            weight_path.unlink(missing_ok=True)
            preview_tmp.unlink(missing_ok=True)
            logger.fail_with_event(
                job,
                model,
                "verify_fail",
                retryable=True,
                version=version,
                extra={
                    "detail": v_reason,
                    "localBlake3": local_hash,
                    "remoteBlake3": blake3,
                    "localStem": stem,
                },
                event_name="verify_fail",
                localStem=stem,
                localBlake3=local_hash,
                remoteBlake3=blake3,
                **_model_fields(model, version),
            )
            return "verify_fail"

        remote_after = _remote_validator(client, download_url) if stale_meta_ok else None
        receipt = (_acceptance_receipt(identity, advertised_blake3, local_hash)
                   if stale_meta_ok and identity is not None else None)
        if (receipt is not None and remote_before is not None and remote_after is not None
                and remote_after == remote_before
                and remote_after['size'] == weight_path.stat().st_size):
            receipt['remoteValidator'] = remote_after

        # A successful retry must not commit an identical new clone. Establish
        # the acceptance binding on the rehashed existing complete survivor.
        if v_status == 'ok' and identity is not None and local_hash:
            for row in installed.records.get(identity, []):
                survivor_weight = weight_path_for_stem(out_dir, row['stem'])
                if survivor_weight is None:
                    continue
                try:
                    survivor_binding = survivor_weight.stat()
                    sidecar_binding = row['path'].stat()
                except OSError:
                    continue
                survivor = _existing_verified_bundle(out_dir, row, identity, local_hash)
                if survivor is None or survivor[1] != str(local_hash).upper():
                    continue
                # Bind both partners across both hash reads. Never discard new
                # bytes after either path changed while its partner was read.
                try:
                    pair_unchanged = (weight_path.stat() == download_binding
                                      and not weight_path.is_symlink()
                                      and survivor_weight.stat() == survivor_binding
                                      and row['path'].stat() == sidecar_binding)
                except OSError:
                    pair_unchanged = False
                if not pair_unchanged:
                    logger.log(f"WARN preserving changed download/survivor pair stem={stem}")
                    return 'error'
                data, _ = survivor
                if stale_meta_ok:
                    data['CivitMatrixAcceptance'] = receipt
                    row['path'].write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                with _index_lock:
                    installed.add_bundle(identity, str(local_hash).upper(), row['path'], row['stem'], data)
                weight_path.unlink()
                _release_reservation(local_blake3, local_versions, local_stems, blake3, version_id, stem)
                cleanup_survivor(row['stem'])
                return 'ok'

        if preview_url:
            try:
                client.download(
                    preview_url, preview_tmp, resume=False, cli_progress=False
                )
                preview_path = finalize_preview_file(preview_tmp, out_dir, stem)
            except Exception as e:
                preview_tmp.unlink(missing_ok=True)
                logger.log(f"WARN preview failed model={model['id']}: {e}")

        cm = build_cm_info(
            model, version, file_info, stem, out_dir, base_url=client.base_url
        )
        if stale_meta_ok and identity is not None:
            cm['CivitMatrixAcceptance'] = receipt
        if preview_path is not None:
            cm["ThumbnailImageUrl"] = str(preview_path)
        info_path.write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if write_swarm:
            swarm = build_swarm_json(model, version, base_url=client.base_url)
            if swarm is not None:
                swarm_path = out_dir / f"{stem}.swarm.json"
                swarm_path.write_text(
                    json.dumps(swarm, ensure_ascii=False, indent=2), encoding="utf-8"
                )

        with _index_lock:
            installed.add_source_bundle(info_path, stem, cm)
            local_versions.add(version_id)
            if blake3:
                local_blake3.add(str(blake3).upper())
                if identity is not None:
                    installed.add_bundle(identity, str(blake3).upper(), info_path, stem, cm)

        logger.append_jsonl(
            logger.manifest_path,
            {
                "ts": utc_now(),
                "status": "ok",
                "modelId": model.get("id"),
                "modelName": model.get("name"),
                "versionId": version_id,
                "versionName": version.get("name"),
                "baseModel": version.get("baseModel"),
                "blake3": blake3,
                "remoteFileName": remote_name,
                "localStem": stem,
                "weightPath": str(weight_path),
                "infoPath": str(info_path),
                "previewPath": str(preview_path) if preview_path is not None else None,
                "tags": [t for t in tags if t],
                "nsfw": model.get("nsfw"),
                "nsfwLevel": model.get("nsfwLevel"),
                "creator": (model.get("creator") or {}).get("username"),
                "sortHints": sort_hints_from_tags(model.get("tags") or []),
            },
        )
        if job:
            job.emit(
                "download_ok",
                localStem=stem,
                blake3=blake3,
                weightPath=str(weight_path),
                previewPath=str(preview_path) if preview_path is not None else None,
                **_model_fields(model, version),
            )
        if local_max_versions is not None and model_id_int is not None:
            with _index_lock:
                key = (model_id_int, version.get("baseModel"))
                prev = local_max_versions.get(key)
                if prev is None or version_id > prev:
                    local_max_versions[key] = version_id
        if v_status == 'ok':
            cleanup_survivor(stem)
        return "ok"
    except PermissionError as e:
        logger.fail_with_event(
            job,
            model,
            "forbidden_or_early_access",
            retryable=False,
            version=version,
            extra={"detail": str(e), "downloadUrl": download_url},
            detail=str(e),
            **_model_fields(model, version),
        )
        if not weight_committed:
            weight_path.unlink(missing_ok=True)
            preview_tmp.unlink(missing_ok=True)
        else:
            logger.log(
                f"WARN keeping verified weight after forbidden error stem={stem}: {e}"
            )
        return "forbidden"
    except FileNotFoundError as e:
        logger.fail_with_event(
            job,
            model,
            "not_found",
            retryable=False,
            version=version,
            extra={"detail": str(e)},
            detail=str(e),
            **_model_fields(model, version),
        )
        if not weight_committed:
            weight_path.unlink(missing_ok=True)
            preview_tmp.unlink(missing_ok=True)
        else:
            logger.log(
                f"WARN keeping verified weight after not_found error stem={stem}: {e}"
            )
        return "not_found"
    except Exception as e:
        logger.fail_with_event(
            job,
            model,
            "download_error",
            retryable=True,
            version=version,
            extra={"detail": repr(e), "downloadUrl": download_url},
            detail=repr(e),
            **_model_fields(model, version),
        )
        if not weight_committed:
            weight_path.unlink(missing_ok=True)
            info_path.unlink(missing_ok=True)
            preview_tmp.unlink(missing_ok=True)
            if preview_path is not None:
                preview_path.unlink(missing_ok=True)
        else:
            logger.log(
                f"WARN keeping verified weight after error stem={stem}: {e!r}"
            )
        return "error"
    finally:
        # Error logging may block or fail. No other model may reuse this name
        # until every possible path deletion by this worker has finished.
        if not weight_committed:
            _release_reservation(
                local_blake3, local_versions, local_stems, blake3, version_id, stem
            )


def _release_reservation(
    local_blake3: set[str],
    local_versions: set[int],
    local_stems: set[str],
    blake3: str | None,
    version_id: int,
    stem: str,
) -> None:
    with _index_lock:
        local_stems.discard(stem.lower())


