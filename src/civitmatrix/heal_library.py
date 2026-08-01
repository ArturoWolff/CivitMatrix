"""Consolidate local model folder: complete sidecars, purge orphans, fix bad weights."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.index_health import index_diagnostics, load_cm_info
from civitmatrix.indexer import (
    WEIGHT_EXTENSIONS,
    iter_cm_info_paths,
    iter_weight_paths,
    relative_pair_stem,
    weight_path_for_stem,
    weight_suffix_from_name,
)
from civitmatrix.preview_media import finalize_preview_file, find_preview_path, pick_preview_url
from civitmatrix.verify_blake3 import remote_blake3_from_file_info, verify_weight_blake3

LogFn = Callable[[str], None]
BuildCmFn = Callable[..., dict[str, Any]]


def _sidecar_incomplete(cm: dict[str, Any] | None) -> bool:
    if cm is None:
        return True
    if cm.get("ModelId") is None or cm.get("VersionId") is None:
        return True
    if not (cm.get("Hashes") or {}).get("BLAKE3"):
        return True
    # SourceUrl is required for Swarm/SM “Installed” links; fill via heal.
    if not cm.get("SourceUrl"):
        return True
    return False


def _remote_unavailable(cm: dict[str, Any] | None) -> bool:
    if not cm:
        return False
    meta = cm.get("CivitMatrix")
    return isinstance(meta, dict) and bool(meta.get("remoteUnavailable"))


def _hash_mismatch_kept(cm: dict[str, Any] | None) -> bool:
    """CDN download works but bytes never match published BLAKE3 — stop redownload thrash."""
    if not cm:
        return False
    meta = cm.get("CivitMatrix")
    return isinstance(meta, dict) and bool(meta.get("hashMismatchKept"))


def _mark_hash_mismatch_kept(
    out_dir: Path,
    stem: str,
    cm: dict[str, Any] | None,
    *,
    model: dict[str, Any] | None,
    version: dict[str, Any] | None,
    file_info: dict[str, Any] | None,
    build_cm_info: BuildCmFn,
    base_url: str,
    local_blake3: str,
    published_blake3: str | None,
    dry_run: bool,
    write_swarm: bool,
    log: LogFn,
) -> None:
    """Keep weight; record stale-remote-meta so heal does not redownload forever."""
    from civitmatrix.logging_io import utc_now
    from civitmatrix.sm_sidecars import civit_model_source_url

    if dry_run:
        return
    info_path = out_dir / f"{stem}.cm-info.json"
    payload: dict[str, Any] = dict(cm) if isinstance(cm, dict) else {}
    fi = dict(file_info) if isinstance(file_info, dict) else {}
    published = dict(fi.get("hashes") or {})
    hashes = dict(published)
    hashes["BLAKE3"] = str(local_blake3).upper()
    fi = {**fi, "hashes": hashes}
    if model is not None and version is not None and fi:
        try:
            payload = build_cm_info(
                model, version, fi, stem, out_dir, base_url=base_url
            )
        except Exception as e:
            log(f"HEAL hash-mismatch sidecar rebuild failed stem={stem}: {e!r}")
    mid = payload.get("ModelId") or (model or {}).get("id")
    vid = payload.get("VersionId") or (version or {}).get("id")
    if not payload.get("SourceUrl"):
        payload["SourceUrl"] = civit_model_source_url(base_url, mid, vid)
    # Local hash is authoritative for the file on disk.
    h = dict(payload.get("Hashes") or {})
    h["BLAKE3"] = str(local_blake3).upper()
    payload["Hashes"] = h
    weight = weight_path_for_stem(out_dir, stem)
    meta = dict(payload.get("CivitMatrix") or {})
    meta["hashMismatchKept"] = True
    meta["staleRemoteMeta"] = True
    meta["localBlake3"] = str(local_blake3).upper()
    meta["publishedBlake3"] = (
        str(published_blake3).upper() if published_blake3 else None
    )
    meta["publishedHashes"] = {
        k: published.get(k) for k in ("BLAKE3", "SHA256", "CRC32", "AutoV2")
    }
    meta["hashMismatchKeptAt"] = utc_now()
    meta["hashMismatchKeptReason"] = (
        "CDN download bytes do not match API-published BLAKE3; "
        "version API is live; kept complete downloaded weight"
    )
    if weight is not None and weight.is_file():
        meta["downloadSizeBytes"] = weight.stat().st_size
    if fi.get("sizeKB") is not None:
        meta["apiSizeKB"] = fi.get("sizeKB")
    payload["CivitMatrix"] = meta
    _write_sidecar(info_path, payload, dry_run=False)
    if write_swarm and model is not None and version is not None:
        from civitmatrix.sm_sidecars import build_swarm_json

        swarm = build_swarm_json(model, version, base_url=base_url)
        if swarm is not None:
            _write_sidecar(out_dir / f"{stem}.swarm.json", swarm, dry_run=False)


def _mark_remote_unavailable(
    out_dir: Path,
    stem: str,
    cm: dict[str, Any] | None,
    *,
    model: dict[str, Any] | None,
    version: dict[str, Any] | None,
    file_info: dict[str, Any] | None,
    build_cm_info: BuildCmFn,
    base_url: str,
    reason: str,
    dry_run: bool,
    write_swarm: bool,
    log: LogFn,
) -> None:
    """Keep the weight; write SourceUrl + flag so heal stops retrying forever."""
    from civitmatrix.logging_io import utc_now
    from civitmatrix.sm_sidecars import civit_model_source_url

    if dry_run:
        return
    info_path = out_dir / f"{stem}.cm-info.json"
    payload: dict[str, Any] = dict(cm) if isinstance(cm, dict) else {}
    if model is not None and version is not None and file_info is not None:
        try:
            payload = build_cm_info(
                model, version, file_info, stem, out_dir, base_url=base_url
            )
        except Exception as e:
            log(f"HEAL remote-gone sidecar rebuild failed stem={stem}: {e!r}")
    mid = payload.get("ModelId") or (model or {}).get("id")
    vid = payload.get("VersionId") or (version or {}).get("id")
    if not payload.get("SourceUrl"):
        payload["SourceUrl"] = civit_model_source_url(base_url, mid, vid)
    meta = dict(payload.get("CivitMatrix") or {})
    meta["remoteUnavailable"] = True
    meta["remoteUnavailableReason"] = reason
    meta["remoteUnavailableAt"] = utc_now()
    payload["CivitMatrix"] = meta
    _write_sidecar(info_path, payload, dry_run=False)
    if write_swarm and model is not None and version is not None:
        from civitmatrix.sm_sidecars import build_swarm_json

        swarm = build_swarm_json(model, version, base_url=base_url)
        if swarm is not None:
            _write_sidecar(out_dir / f"{stem}.swarm.json", swarm, dry_run=False)


def _bump_redownload_result(bump: Callable[[str], None], status: str) -> None:
    if status == "ok":
        bump("heal_redownloaded")
    elif status == "gated":
        bump("heal_gated")
    elif status == "gone":
        bump("heal_remote_gone")
    elif status == "hash_mismatch_kept":
        bump("heal_hash_mismatch_kept")
    else:
        bump("heal_redownload_failed")


def _pick_file_for_hash(version: dict[str, Any], blake3: str) -> dict[str, Any] | None:
    files = version.get("files") or []
    target = blake3.upper()
    for f in files:
        h = (f.get("hashes") or {}).get("BLAKE3")
        if h and str(h).upper() == target:
            return f
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


def _model_from_version_payload(version: dict[str, Any]) -> dict[str, Any]:
    nested = version.get("model") or {}
    return {
        "id": version.get("modelId") or nested.get("id"),
        "name": nested.get("name"),
        "description": nested.get("description"),
        "nsfw": nested.get("nsfw"),
        "type": nested.get("type") or "LORA",
        "tags": nested.get("tags") or [],
        "creator": nested.get("creator") or {},
        "stats": nested.get("stats") or {},
    }


def _write_sidecar(path: Path, payload: dict[str, Any], *, dry_run: bool) -> None:
    if dry_run:
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_cm_and_swarm(
    out_dir: Path,
    stem: str,
    model: dict[str, Any],
    version: dict[str, Any],
    file_info: dict[str, Any],
    *,
    build_cm_info: BuildCmFn,
    base_url: str,
    preview: Path | None,
    dry_run: bool,
    write_swarm: bool = False,
) -> None:
    from civitmatrix.sm_sidecars import build_swarm_json

    payload = build_cm_info(
        model, version, file_info, stem, out_dir, base_url=base_url
    )
    if preview is not None:
        payload["ThumbnailImageUrl"] = str(preview)
    _write_sidecar(out_dir / f"{stem}.cm-info.json", payload, dry_run=dry_run)
    if not write_swarm:
        return
    swarm = build_swarm_json(model, version, base_url=base_url)
    if swarm is not None:
        _write_sidecar(out_dir / f"{stem}.swarm.json", swarm, dry_run=dry_run)


def _ensure_preview(
    client: Any,
    version: dict[str, Any],
    out_dir: Path,
    stem: str,
    *,
    dry_run: bool,
    log: LogFn,
) -> Path | None:
    existing = find_preview_path(out_dir, stem)
    if existing is not None:
        return existing
    url = pick_preview_url(version.get("images") or [])
    if not url:
        return None
    if dry_run:
        return None
    tmp = out_dir / f"{stem}.preview.download"
    try:
        client.download(url, tmp)
        return finalize_preview_file(tmp, out_dir, stem)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        log(f"WARN heal preview failed stem={stem}: {e}")
        return None


def _clear_sibling_weights(out_dir: Path, stem: str, *, keep: Path) -> None:
    """Remove other known weight extensions for the same stem after a replace."""
    keep_resolved = keep.resolve()
    for ext in WEIGHT_EXTENSIONS:
        p = out_dir / f"{stem}{ext}"
        try:
            if p.is_file() and p.resolve() != keep_resolved:
                p.unlink(missing_ok=True)
        except OSError:
            continue


def _delete_weight_bundle(out_dir: Path, stem: str, *, dry_run: bool) -> list[str]:
    removed: list[str] = []
    candidates = [
        out_dir / f"{stem}.cm-info.json",
        out_dir / f"{stem}.swarm.json",
        *out_dir.glob(f"{stem}.preview.*"),
    ]
    for ext in WEIGHT_EXTENSIONS:
        candidates.append(out_dir / f"{stem}{ext}")
    wp = weight_path_for_stem(out_dir, stem)
    if wp is not None:
        candidates.append(wp)
    for p in candidates:
        if p.is_file() and not p.name.endswith(".partial"):
            removed.append(p.name)
            if not dry_run:
                p.unlink(missing_ok=True)
    return removed


def heal_library(
    *,
    client: Any,
    out_dir: Path,
    build_cm_info: BuildCmFn,
    log: LogFn,
    job: Any | None = None,
    dry_run: bool = False,
    purge_orphans: bool = False,
    refresh_sidecars: bool = False,
    write_swarm: bool = False,
    cancel_check: Callable[[], bool] | None = None,
    pause_wait: Callable[[], bool] | None = None,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    diag = index_diagnostics(out_dir)
    if job:
        job.set_phase("healing")
        job.emit(
            "heal_start",
            dryRun=dry_run,
            purgeOrphans=purge_orphans,
            refreshSidecars=refresh_sidecars,
            writeSwarm=write_swarm,
            diagnostics={
                k: (len(v) if isinstance(v, list) else v) for k, v in diag.items()
            },
        )

    # Recursive so SM category subfolders heal; keys are relative pair stems.
    weights = {
        relative_pair_stem(out_dir, p): p
        for p in iter_weight_paths(out_dir, recursive=True)
    }
    infos = {
        relative_pair_stem(out_dir, p, cm_info=True): p
        for p in iter_cm_info_paths(out_dir, recursive=True)
    }
    stems = sorted(set(weights) | set(infos))

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1
        if job:
            job.set_count(key, counts[key])
            job.set_count("processed", sum(counts.values()))

    for stem in stems:
        if cancel_check and cancel_check():
            bump("cancelled")
            break
        if pause_wait and pause_wait():
            bump("cancelled")
            break

        if job:
            job.set_current({"id": None, "name": stem})

        weight = weights.get(stem)
        info_path = infos.get(stem)
        cm = load_cm_info(info_path) if info_path else None

        if weight is not None:
            try:
                size = weight.stat().st_size
            except OSError:
                size = 0
            if size <= 0:
                log(f"HEAL bad weight (empty) stem={stem}")
                version_id = cm.get("VersionId") if cm else None
                removed = _delete_weight_bundle(out_dir, stem, dry_run=dry_run)
                bump("heal_bad_weight")
                if job:
                    job.emit("heal_bad_weight", stem=stem, removed=removed)
                if version_id and not dry_run:
                    status = _redownload_version(
                        client,
                        out_dir,
                        stem,
                        int(version_id),
                        build_cm_info=build_cm_info,
                        log=log,
                        dry_run=dry_run,
                        write_swarm=write_swarm,
                        existing_cm=cm,
                    )
                    _bump_redownload_result(bump, status)
                continue

        if weight is None and info_path is not None:
            version_id = cm.get("VersionId") if cm else None
            if purge_orphans:
                log(f"HEAL purge orphan sidecar stem={stem}")
                removed = _delete_weight_bundle(out_dir, stem, dry_run=dry_run)
                bump("heal_purged_orphan")
                if job:
                    job.emit("heal_purged_orphan", stem=stem, removed=removed)
                continue
            if version_id:
                log(f"HEAL re-download missing weight stem={stem} ver={version_id}")
                if dry_run:
                    bump("heal_would_redownload")
                else:
                    status = _redownload_version(
                        client,
                        out_dir,
                        stem,
                        int(version_id),
                        build_cm_info=build_cm_info,
                        log=log,
                        dry_run=dry_run,
                        write_swarm=write_swarm,
                        existing_cm=cm,
                    )
                    _bump_redownload_result(bump, status)
                continue
            log(f"HEAL orphan sidecar (no VersionId) stem={stem}")
            bump("heal_orphan_unresolved")
            if job:
                job.emit("heal_orphan_unresolved", stem=stem)
            continue

        assert weight is not None
        incomplete = _sidecar_incomplete(cm)
        refreshing = bool(
            refresh_sidecars
            and not incomplete
            and cm
            and cm.get("ModelId") is not None
            and cm.get("VersionId") is not None
        )
        if not incomplete and not refreshing:
            bump("heal_ok")
            continue

        try:
            local_hash = file_blake3_hex(weight)
        except OSError as e:
            log(f"HEAL hash failed stem={stem}: {e}")
            bump("heal_hash_failed")
            continue

        recorded = ((cm or {}).get("Hashes") or {}).get("BLAKE3")
        if recorded and str(recorded).upper() != str(local_hash).upper():
            version_id = cm.get("VersionId") if cm else None
            if _remote_unavailable(cm):
                log(
                    f"HEAL hash mismatch stem={stem} — remote unavailable; keeping local"
                )
                bump("heal_remote_gone_kept")
                if job:
                    job.emit(
                        "heal_remote_gone_kept",
                        stem=stem,
                        recorded=str(recorded).upper(),
                        local=str(local_hash).upper(),
                    )
                continue
            if _hash_mismatch_kept(cm):
                log(
                    f"HEAL hash mismatch stem={stem} — stale remote meta; "
                    "keeping local (hashMismatchKept)"
                )
                bump("heal_hash_mismatch_kept")
                if job:
                    job.emit(
                        "heal_hash_mismatch_kept",
                        stem=stem,
                        recorded=str(recorded).upper(),
                        local=str(local_hash).upper(),
                    )
                continue
            log(f"HEAL hash mismatch stem={stem} — re-downloading to fix")
            bump("heal_hash_mismatch")
            if job:
                job.emit(
                    "heal_hash_mismatch",
                    stem=stem,
                    recorded=str(recorded).upper(),
                    local=str(local_hash).upper(),
                )
            if not version_id:
                bump("heal_hash_mismatch_unresolved")
                continue
            if dry_run:
                bump("heal_would_redownload")
                continue
            status = _redownload_version(
                client,
                out_dir,
                stem,
                int(version_id),
                build_cm_info=build_cm_info,
                log=log,
                dry_run=dry_run,
                write_swarm=write_swarm,
                existing_cm=cm,
            )
            _bump_redownload_result(bump, status)
            continue

        version: dict[str, Any] | None = None
        if refreshing and cm and cm.get("VersionId") is not None:
            try:
                version = client.get_json(
                    f"{client.base_url}/api/v1/model-versions/{int(cm['VersionId'])}"
                )
                time.sleep(0.15)
            except Exception as e:
                log(f"HEAL refresh version fetch failed stem={stem}: {e}")
        if version is None:
            try:
                version = client.get_version_by_hash(local_hash)
            except Exception as e:
                log(f"HEAL by-hash error stem={stem}: {e}")

        if version is None and cm and cm.get("VersionId") is not None:
            try:
                version = client.get_json(
                    f"{client.base_url}/api/v1/model-versions/{int(cm['VersionId'])}"
                )
                time.sleep(0.15)
                log(f"HEAL fallback VersionId={cm.get('VersionId')} stem={stem}")
            except Exception as e:
                log(f"HEAL version fallback failed stem={stem}: {e}")

        if version is None:
            log(f"HEAL unresolved (hash not on CivitAI) stem={stem}")
            bump("heal_unresolved")
            if job:
                job.emit("heal_unresolved", stem=stem, blake3=local_hash)
            continue

        file_info = _pick_file_for_hash(version, local_hash)
        if file_info is None:
            bump("heal_unresolved")
            continue

        remote_h = remote_blake3_from_file_info(file_info)
        if remote_h and str(remote_h).upper() != str(local_hash).upper():
            version_id = version.get("id") or (cm or {}).get("VersionId")
            if _remote_unavailable(cm):
                log(
                    f"HEAL remote BLAKE3 mismatch stem={stem} — "
                    "remote unavailable; keeping local"
                )
                bump("heal_remote_gone_kept")
                continue
            if _hash_mismatch_kept(cm):
                log(
                    f"HEAL remote BLAKE3 mismatch stem={stem} — "
                    "stale remote meta; keeping local (hashMismatchKept)"
                )
                bump("heal_hash_mismatch_kept")
                continue
            log(
                f"HEAL remote BLAKE3 mismatch stem={stem} — re-downloading to fix"
            )
            bump("heal_hash_mismatch")
            if not version_id:
                bump("heal_hash_mismatch_unresolved")
                continue
            if dry_run:
                bump("heal_would_redownload")
                continue
            status = _redownload_version(
                client,
                out_dir,
                stem,
                int(version_id),
                build_cm_info=build_cm_info,
                log=log,
                dry_run=dry_run,
                write_swarm=write_swarm,
                existing_cm=cm,
            )
            _bump_redownload_result(bump, status)
            continue

        # Sidecars already good and weight matches remote — nothing to rewrite
        if refreshing and cm and cm.get("SourceUrl"):
            swarm_path = out_dir / f"{stem}.swarm.json"
            if (not write_swarm) or swarm_path.is_file():
                bump("heal_sidecars_fresh")
                time.sleep(0.1)
                continue

        model_id = version.get("modelId") or (cm or {}).get("ModelId")
        model: dict[str, Any] | None = None
        if model_id is not None:
            try:
                model = client.get_model(int(model_id))
                time.sleep(0.15)
            except Exception as e:
                log(f"WARN heal get_model failed id={model_id}: {e}")
        if model is None:
            model = _model_from_version_payload(version)

        hashes = dict(file_info.get("hashes") or {})
        # Always record the computed local BLAKE3 (API may omit it)
        hashes["BLAKE3"] = local_hash
        file_info = {**file_info, "hashes": hashes}

        preview = _ensure_preview(
            client, version, out_dir, stem, dry_run=dry_run, log=log
        )
        try:
            _write_cm_and_swarm(
                out_dir,
                stem,
                model,
                version,
                file_info,
                build_cm_info=build_cm_info,
                base_url=client.base_url,
                preview=preview,
                dry_run=dry_run,
                write_swarm=write_swarm,
            )
        except Exception as e:
            log(f"HEAL sidecar write failed stem={stem} (keeping weight): {e!r}")
            bump("heal_sidecar_failed")
            continue
        if refreshing:
            log(
                f"HEAL refreshed sidecars stem={stem} model={model.get('id')} "
                f"ver={version.get('id')}"
                + (" (dry-run)" if dry_run else "")
            )
            bump("heal_sidecars_refreshed")
            if job:
                job.emit(
                    "heal_sidecars_refreshed",
                    stem=stem,
                    modelId=model.get("id"),
                    versionId=version.get("id"),
                    blake3=local_hash,
                    writeSwarm=write_swarm,
                    dryRun=dry_run,
                )
        else:
            log(
                f"HEAL repaired stem={stem} model={model.get('id')} ver={version.get('id')}"
                + (" (dry-run)" if dry_run else "")
            )
            bump("heal_repaired")
            if job:
                job.emit(
                    "heal_repaired",
                    stem=stem,
                    modelId=model.get("id"),
                    versionId=version.get("id"),
                    blake3=local_hash,
                    dryRun=dry_run,
                )
        time.sleep(0.1)

    if job:
        job.emit("heal_done", counts=dict(counts), dryRun=dry_run)
    return counts


def _redownload_version(
    client: Any,
    out_dir: Path,
    stem: str,
    version_id: int,
    *,
    build_cm_info: BuildCmFn,
    log: LogFn,
    dry_run: bool,
    write_swarm: bool = False,
    existing_cm: dict[str, Any] | None = None,
) -> str:
    """
    Re-download a version into ``stem``. Returns status:
    ``ok`` | ``gated`` | ``gone`` | ``hash_mismatch_kept`` | ``failed``.
    Never deletes an existing weight on failure.
    """
    from civitmatrix.redact import redact_secrets

    try:
        version = client.get_json(f"{client.base_url}/api/v1/model-versions/{version_id}")
    except Exception as e:
        msg = redact_secrets(str(e))
        log(f"HEAL redownload version fetch failed ver={version_id}: {msg}")
        if "404" in msg:
            _mark_remote_unavailable(
                out_dir,
                stem,
                existing_cm,
                model=None,
                version=None,
                file_info=None,
                build_cm_info=build_cm_info,
                base_url=client.base_url,
                reason="version_404",
                dry_run=dry_run,
                write_swarm=False,
                log=log,
            )
            return "gone"
        return "failed"

    files = version.get("files") or []
    file_info = None
    for f in files:
        if str(f.get("name", "")).lower().endswith(".safetensors") or (
            f.get("metadata") or {}
        ).get("format") == "SafeTensor":
            file_info = f
            if f.get("primary"):
                break
    if file_info is None and files:
        file_info = files[0]
    if file_info is None:
        return "failed"

    download_url = file_info.get("downloadUrl") or (
        f"{client.base_url}/api/download/models/{version_id}"
    )
    remote_name = file_info.get("name") or f"model-{version_id}.safetensors"
    ext = weight_suffix_from_name(str(remote_name))
    weight_path = out_dir / f"{stem}{ext}"
    if dry_run:
        return "ok"
    # Download beside the existing weight; only replace after BLAKE3 verify.
    staging = out_dir / f"{stem}{ext}.heal-new"
    staging.unlink(missing_ok=True)
    try:
        client.download(download_url, staging)
    except PermissionError as e:
        log(f"HEAL redownload gated stem={stem}: {redact_secrets(str(e))}")
        staging.unlink(missing_ok=True)
        return "gated"
    except FileNotFoundError as e:
        log(f"HEAL redownload gone stem={stem}: {redact_secrets(str(e))}")
        staging.unlink(missing_ok=True)
        model = _model_from_version_payload(version)
        _mark_remote_unavailable(
            out_dir,
            stem,
            existing_cm,
            model=model,
            version=version,
            file_info=file_info,
            build_cm_info=build_cm_info,
            base_url=client.base_url,
            reason="download_404",
            dry_run=False,
            write_swarm=write_swarm,
            log=log,
        )
        return "gone"
    except Exception as e:
        log(f"HEAL redownload failed stem={stem}: {redact_secrets(str(e))}")
        staging.unlink(missing_ok=True)
        # Never delete the existing weight because a redownload failed.
        return "failed"

    remote_h = remote_blake3_from_file_info(file_info)
    v_status, local_hash, v_reason = verify_weight_blake3(
        staging, remote_h, skip=False
    )

    model_id = version.get("modelId")
    if model_id is not None:
        try:
            model = client.get_model(int(model_id))
        except Exception:
            model = _model_from_version_payload(version)
    else:
        model = _model_from_version_payload(version)

    if v_status == "fail":
        # Never delete the existing weight. If the CDN gave a complete file
        # whose BLAKE3 never matches published meta, keep the new bytes and
        # mark hashMismatchKept so heal stops thrashing.
        size_kb = file_info.get("sizeKB")
        staging_ok = False
        if staging.is_file():
            sz = staging.stat().st_size
            if size_kb is not None:
                try:
                    expected = int(float(size_kb) * 1024)
                    staging_ok = abs(sz - expected) <= 4096 or sz >= expected * 0.99
                except (TypeError, ValueError):
                    staging_ok = sz > 0
            else:
                staging_ok = sz > 0
        if staging_ok and local_hash:
            log(
                f"HEAL verify fail stem={stem} reason={v_reason} — "
                "keeping complete download (stale remote meta)"
            )
            staging.replace(weight_path)
            _clear_sibling_weights(out_dir, stem, keep=weight_path)
            preview = _ensure_preview(
                client, version, out_dir, stem, dry_run=False, log=log
            )
            _ = preview
            _mark_hash_mismatch_kept(
                out_dir,
                stem,
                existing_cm,
                model=model,
                version=version,
                file_info=file_info,
                build_cm_info=build_cm_info,
                base_url=client.base_url,
                local_blake3=str(local_hash).upper(),
                published_blake3=remote_h,
                dry_run=False,
                write_swarm=write_swarm,
                log=log,
            )
            return "hash_mismatch_kept"
        log(f"HEAL verify fail stem={stem} reason={v_reason}")
        staging.unlink(missing_ok=True)
        return "failed"

    staging.replace(weight_path)
    _clear_sibling_weights(out_dir, stem, keep=weight_path)

    if not local_hash:
        try:
            local_hash = file_blake3_hex(weight_path)
        except OSError:
            local_hash = remote_h
    if local_hash:
        hashes = dict(file_info.get("hashes") or {})
        hashes["BLAKE3"] = str(local_hash).upper()
        file_info = {**file_info, "hashes": hashes}

    preview = _ensure_preview(client, version, out_dir, stem, dry_run=False, log=log)
    try:
        _write_cm_and_swarm(
            out_dir,
            stem,
            model,
            version,
            file_info,
            build_cm_info=build_cm_info,
            base_url=client.base_url,
            preview=preview,
            dry_run=False,
            write_swarm=write_swarm,
        )
    except Exception as e:
        # Weight already verified — never delete it because sidecar write failed.
        log(f"HEAL sidecar write failed stem={stem} (keeping weight): {e!r}")
        return "failed"
    return "ok"
