from __future__ import annotations

import json
import threading
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
    pick_matching_version,
    pick_primary_file,
    unique_stem,
)
from civitmatrix.job_state import JobState
from civitmatrix.logging_io import RunLogger, utc_now
from civitmatrix.preview_media import finalize_preview_file, pick_preview_url
from civitmatrix.sm_sidecars import build_cm_info, build_swarm_json, sort_hints_from_tags
from civitmatrix.verify_blake3 import (
    remote_blake3_from_file_info,
    verify_weight_blake3,
    version_matches_local_hash,
)
from civitmatrix.version_prune import prune_old_versions

_index_lock = threading.Lock()

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
) -> int:
    if not enabled or dry_run:
        return 0
    mid = model.get("id")
    if mid is None:
        return 0
    try:
        model_id = int(mid)
    except (TypeError, ValueError):
        return 0
    pruned = prune_old_versions(
        out_dir,
        model_id,
        version_id,
        local_blake3=local_blake3,
        local_versions=local_versions,
        local_stems=local_stems,
        index_lock=_index_lock,
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
                versionId=cand.get("versionId"),
                keepVersionId=version_id,
                **_model_fields(model, version),
            )
            job.bump("pruned")
    return len(pruned)


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
    force_version: dict[str, Any] | None = None,
    write_swarm: bool = False,
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

    blake3 = (file_info.get("hashes") or {}).get("BLAKE3")
    version_id = int(version["id"])
    skip_reason: str | None = None
    stem = ""
    remote_name = file_info.get("name") or f"model-{version_id}.safetensors"
    with _index_lock:
        if blake3 and blake3.upper() in local_blake3:
            skip_reason = "skip_hash"
        elif version_id in local_versions:
            skip_reason = "skip_version"
        else:
            stem = unique_stem(Path(remote_name).stem, version_id, local_stems)
            local_stems.add(stem.lower())
            local_versions.add(version_id)
            if blake3:
                local_blake3.add(blake3.upper())

    if skip_reason:
        if job:
            fields = dict(_model_fields(model, version))
            if skip_reason == "skip_hash" and blake3:
                fields["blake3"] = blake3
            job.emit(skip_reason, **fields)
        maybe_prune_old_versions(
            enabled=not keep_old_versions,
            dry_run=dry_run,
            out_dir=out_dir,
            model=model,
            version=version,
            version_id=version_id,
            local_blake3=local_blake3,
            local_versions=local_versions,
            local_stems=local_stems,
            logger=logger,
            job=job,
        )
        return skip_reason

    weight_path = out_dir / f"{stem}.safetensors"
    info_path = out_dir / f"{stem}.cm-info.json"
    preview_tmp = out_dir / f"{stem}.preview.download"
    preview_path: Path | None = None

    download_url = file_info.get("downloadUrl") or (
        f"{client.base_url}/api/download/models/{version_id}"
    )

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
                job.emit(
                    event,
                    localStem=stem,
                    **fields,
                    **_model_fields(model, version),
                )

        client.download(
            download_url, weight_path, resume=resume, on_event=_dl_event
        )

        remote_b3 = remote_blake3_from_file_info(file_info) or blake3
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
            if version_matches_local_hash(by_hash, version_id):
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
                with _index_lock:
                    if blake3:
                        local_blake3.discard(str(blake3).upper())
                    local_blake3.add(str(local_hash).upper())
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
            _release_reservation(
                local_blake3, local_versions, local_stems, blake3, version_id, stem
            )
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
        maybe_prune_old_versions(
            enabled=not keep_old_versions,
            dry_run=False,
            out_dir=out_dir,
            model=model,
            version=version,
            version_id=version_id,
            local_blake3=local_blake3,
            local_versions=local_versions,
            local_stems=local_stems,
            logger=logger,
            job=job,
        )
        return "ok"
    except PermissionError as e:
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
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
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
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
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
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
        local_versions.discard(version_id)
        if blake3:
            local_blake3.discard(blake3.upper())


