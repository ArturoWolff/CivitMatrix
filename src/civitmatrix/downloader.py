from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from civitmatrix.cancel_control import CancelGate
from civitmatrix.client import CivitClient
from civitmatrix.indexer import (
    load_local_index,
    pick_matching_version,
    pick_primary_file,
    unique_stem,
)
from civitmatrix.job_state import JobState
from civitmatrix.logging_io import RunLogger, utc_now
from civitmatrix.heal_library import heal_library
from civitmatrix.index_health import format_index_log_line, index_diagnostics
from civitmatrix.partial_sweep import purge_stale_partials
from civitmatrix.pause_control import PauseGate
from civitmatrix.preview_media import finalize_preview_file, pick_preview_url
from civitmatrix.run_lock import RunLock, RunLockError
from civitmatrix.sm_sidecars import build_cm_info, sort_hints_from_tags

_index_lock = threading.Lock()


def _model_fields(model: dict[str, Any], version: dict[str, Any] | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "modelId": model.get("id"),
        "modelName": model.get("name"),
    }
    if version is not None:
        fields["versionId"] = version.get("id")
        fields["baseModel"] = version.get("baseModel")
    return fields


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
) -> str:
    version = pick_matching_version(
        model, base_model, match_base_version=match_base_version
    )
    if not version:
        logger.record_failure(
            model,
            "no_matching_base_version",
            retryable=False,
            extra={"wantedBaseModel": base_model},
        )
        if job:
            job.emit(
                "fail",
                reason="no_matching_base_version",
                retryable=False,
                **_model_fields(model),
            )
        return "no_match"

    file_info = pick_primary_file(version)
    if not file_info:
        logger.record_failure(model, "no_files", retryable=False, version=version)
        if job:
            job.emit(
                "fail",
                reason="no_files",
                retryable=False,
                **_model_fields(model, version),
            )
        return "no_files"

    blake3 = (file_info.get("hashes") or {}).get("BLAKE3")
    version_id = int(version["id"])
    with _index_lock:
        if blake3 and blake3.upper() in local_blake3:
            if job:
                job.emit("skip_hash", blake3=blake3, **_model_fields(model, version))
            return "skip_hash"
        if version_id in local_versions:
            if job:
                job.emit("skip_version", **_model_fields(model, version))
            return "skip_version"
        remote_name = file_info.get("name") or f"model-{version_id}.safetensors"
        stem = unique_stem(Path(remote_name).stem, version_id, local_stems)
        local_stems.add(stem.lower())
        local_versions.add(version_id)
        if blake3:
            local_blake3.add(blake3.upper())

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

    try:
        logger.log(f"DOWNLOAD model={model['id']} ver={version_id} -> {weight_path.name}")
        if job:
            job.emit(
                "download_start",
                localStem=stem,
                remoteFileName=remote_name,
                **_model_fields(model, version),
            )
        client.download(download_url, weight_path)
        if preview_url:
            try:
                client.download(preview_url, preview_tmp)
                preview_path = finalize_preview_file(preview_tmp, out_dir, stem)
            except Exception as e:
                preview_tmp.unlink(missing_ok=True)
                logger.log(f"WARN preview failed model={model['id']}: {e}")

        cm = build_cm_info(model, version, file_info, stem, out_dir)
        if preview_path is not None:
            cm["ThumbnailImageUrl"] = str(preview_path)
        info_path.write_text(json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8")

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
        return "ok"
    except PermissionError as e:
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
        logger.record_failure(
            model,
            "forbidden_or_early_access",
            retryable=True,
            version=version,
            extra={"detail": str(e), "downloadUrl": download_url},
        )
        if job:
            job.emit(
                "fail",
                reason="forbidden_or_early_access",
                retryable=True,
                detail=str(e),
                **_model_fields(model, version),
            )
        weight_path.unlink(missing_ok=True)
        preview_tmp.unlink(missing_ok=True)
        return "forbidden"
    except FileNotFoundError as e:
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
        logger.record_failure(
            model,
            "not_found",
            retryable=False,
            version=version,
            extra={"detail": str(e)},
        )
        if job:
            job.emit(
                "fail",
                reason="not_found",
                retryable=False,
                detail=str(e),
                **_model_fields(model, version),
            )
        weight_path.unlink(missing_ok=True)
        preview_tmp.unlink(missing_ok=True)
        return "not_found"
    except Exception as e:
        _release_reservation(
            local_blake3, local_versions, local_stems, blake3, version_id, stem
        )
        logger.record_failure(
            model,
            "download_error",
            retryable=True,
            version=version,
            extra={"detail": repr(e), "downloadUrl": download_url},
        )
        if job:
            job.emit(
                "fail",
                reason="download_error",
                retryable=True,
                detail=repr(e),
                **_model_fields(model, version),
            )
        weight_path.unlink(missing_ok=True)
        info_path.unlink(missing_ok=True)
        preview_tmp.unlink(missing_ok=True)
        if preview_path is not None:
            preview_path.unlink(missing_ok=True)
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


def run_batch(
    *,
    client: CivitClient,
    out_dir: Path,
    logger: RunLogger,
    base_model: str,
    model_type: str,
    sort: str,
    nsfw: bool,
    match_base_version: bool,
    concurrency: int,
    dry_run: bool,
    limit: int,
    retry_failed: bool,
    keep_partials: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = logger.job_path.parent
    cancel = CancelGate(logs_dir)
    pause = PauseGate(logs_dir)
    cancel.clear()  # drop stale flags from a previous run
    pause.clear()

    job = JobState(logger.job_path)
    job.set_meta(
        dryRun=bool(dry_run),
        baseModel=base_model,
        modelType=model_type,
        sort=sort,
        outDir=str(out_dir),
    )
    cancel.install_sigint(lambda: job.run_id)

    def _pause_hooks(resume_phase: str) -> tuple[Any, Any]:
        def on_pause() -> None:
            job.emit("pause_requested", source=pause.source, phase=resume_phase)
            job.set_phase("paused")
            job.emit("paused")
            logger.log(f"Paused (phase was {resume_phase}). Waiting for --resume …")

        def on_resume() -> None:
            job.emit("resumed", phase=resume_phase)
            job.set_phase(resume_phase)
            logger.log(f"Resumed → {resume_phase}")

        return on_pause, on_resume

    try:
        lock = RunLock.acquire(out_dir, job.run_id)
    except RunLockError as e:
        job.emit("lock_denied", detail=str(e), **e.lock_info)
        job.set_phase("error")
        logger.log(f"ERROR: {e}")
        return 3

    job.emit("lock_acquired", lockPath=str(lock.path), pid=os.getpid())
    job.set_meta(lockPath=str(lock.path))

    if keep_partials:
        job.emit("partial_sweep_skipped", reason="keep_partials")
        logger.log("Keeping stale partials (--keep-partials / KEEP_PARTIALS)")
    else:
        removed = purge_stale_partials(out_dir)
        job.set_count("partialsPurged", len(removed))
        sample = [p.name for p in removed[:20]]
        job.emit("partial_purged", count=len(removed), sample=sample)
        if removed:
            logger.log(f"Purged {len(removed)} stale partial file(s)")

    local_blake3, local_versions, local_stems = load_local_index(out_dir)
    diag = index_diagnostics(out_dir)
    logger.log(f"{format_index_log_line(diag)} under {out_dir}")

    models: list[dict[str, Any]] = []
    cancelled = False
    try:
        job.set_phase("listing")
        if retry_failed:
            ids = logger.load_failed_model_ids()
            logger.log(f"Retry mode: {len(ids)} unique retryable modelIds")
            import time

            on_pause, on_resume = _pause_hooks("listing")
            for mid in ids:
                if pause.wait_if_paused(
                    cancel, resume_phase="listing", on_pause=on_pause, on_resume=on_resume
                ):
                    cancelled = True
                    break
                if cancel.is_requested():
                    cancelled = True
                    break
                try:
                    models.append(client.get_model(mid))
                except Exception as e:
                    logger.record_failure(
                        {"id": mid, "name": None},
                        "retry_fetch_failed",
                        extra={"detail": repr(e)},
                    )
                time.sleep(0.2)
            job.set_count("listed", len(models))
        else:
            logger.log(
                f"Listing {model_type} for base={base_model!r} sort={sort!r} "
                f"from {client.base_url} …"
            )
            on_pause, on_resume = _pause_hooks("listing")
            for i, model in enumerate(
                client.iter_models(
                    base_model=base_model,
                    model_type=model_type,
                    nsfw=nsfw,
                    sort=sort,
                ),
                1,
            ):
                if pause.wait_if_paused(
                    cancel, resume_phase="listing", on_pause=on_pause, on_resume=on_resume
                ):
                    cancelled = True
                    break
                if cancel.is_requested():
                    cancelled = True
                    break
                models.append(model)
                if i % 50 == 0:
                    job.set_count("listed", i)
                    job.emit("listing_progress", listed=i)
                    logger.log(f"Listed {i} models…")
                if limit and i >= limit:
                    break
            job.set_count("listed", len(models))
            if not cancelled:
                job.emit("listing_done", listed=len(models))

        if cancelled:
            job.emit(
                "cancel_requested",
                source=cancel.source,
                phase="listing",
                listed=len(models),
            )
            job.emit("run_cancelled", counts={}, listed=len(models))
            job.set_phase("cancelled")
            cancel.clear()
            pause.clear()
            logger.log(f"Cancelled during listing after {len(models)} models.")
            return 4

        logger.log(
            f"Processing {len(models)} models (concurrency={concurrency}, dry_run={dry_run})"
        )
        job.set_phase("downloading")
        job.set_count("total", len(models))
        counts: dict[str, int] = {}
        counts_lock = threading.Lock()
        cancel_logged = False
        on_pause_dl, on_resume_dl = _pause_hooks("downloading")

        def worker(model: dict[str, Any]) -> str:
            nonlocal cancel_logged
            if pause.wait_if_paused(
                cancel,
                resume_phase="downloading",
                on_pause=on_pause_dl,
                on_resume=on_resume_dl,
            ):
                with counts_lock:
                    if not cancel_logged:
                        job.emit(
                            "cancel_requested",
                            source=cancel.source,
                            phase="downloading",
                        )
                        cancel_logged = True
                    counts["cancelled"] = counts.get("cancelled", 0) + 1
                    job.set_count("cancelled", counts["cancelled"])
                    job.set_count("processed", sum(counts.values()))
                return "cancelled"
            if cancel.is_requested():
                with counts_lock:
                    if not cancel_logged:
                        job.emit(
                            "cancel_requested",
                            source=cancel.source,
                            phase="downloading",
                        )
                        cancel_logged = True
                    counts["cancelled"] = counts.get("cancelled", 0) + 1
                    job.set_count("cancelled", counts["cancelled"])
                    job.set_count("processed", sum(counts.values()))
                return "cancelled"
            job.set_current(model)
            status = process_one(
                client,
                model,
                out_dir,
                local_blake3,
                local_versions,
                local_stems,
                logger,
                base_model=base_model,
                match_base_version=match_base_version,
                dry_run=dry_run,
                job=job,
            )
            with counts_lock:
                counts[status] = counts.get(status, 0) + 1
                job.set_count(status, counts[status])
                job.set_count("processed", sum(counts.values()))
            return status

        if concurrency <= 1:
            for model in models:
                if cancel.is_requested() and not cancel_logged:
                    job.emit(
                        "cancel_requested",
                        source=cancel.source,
                        phase="downloading",
                    )
                    cancel_logged = True
                    cancelled = True
                    break
                status = worker(model)
                if status == "cancelled":
                    cancelled = True
                    break
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futs = {ex.submit(worker, m): m for m in models}
                for fut in as_completed(futs):
                    try:
                        if fut.result() == "cancelled":
                            cancelled = True
                    except Exception as e:
                        m = futs[fut]
                        logger.record_failure(m, "worker_crash", extra={"detail": repr(e)})
                        job.emit(
                            "fail",
                            reason="worker_crash",
                            retryable=True,
                            detail=repr(e),
                            modelId=m.get("id"),
                            modelName=m.get("name"),
                        )
                        with counts_lock:
                            counts["error"] = counts.get("error", 0) + 1
                            job.set_count("error", counts["error"])
                            job.set_count("processed", sum(counts.values()))

        if cancelled or counts.get("cancelled"):
            job.emit("run_cancelled", counts=dict(counts))
            job.set_phase("cancelled")
            cancel.clear()
            pause.clear()
            logger.log(f"Cancelled. Counts: {json.dumps(counts, sort_keys=True)}")
            logger.log(f"Job status -> {logger.job_path}")
            return 4

        job.emit("run_done", counts=dict(counts))
        job.set_phase("done")
        cancel.clear()
        pause.clear()
        logger.log(f"Done. Counts: {json.dumps(counts, sort_keys=True)}")
        logger.log(f"Failures -> {logger.failed_path}")
        logger.log(f"Manifest -> {logger.manifest_path}")
        logger.log(f"Job status -> {logger.job_path}")
        logger.log(f"Events -> {job.events_path}")
        logger.log(
            "Refresh Stability Matrix model index (or restart SM) to see green Installed labels."
        )
        return 0
    except Exception:
        job.emit("run_error")
        job.set_phase("error")
        raise
    finally:
        lock.release()
        job.emit("lock_released", lockPath=str(lock.path))


def run_heal(
    *,
    client: CivitClient,
    out_dir: Path,
    logger: RunLogger,
    dry_run: bool,
    purge_orphans: bool,
    keep_partials: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = logger.job_path.parent
    cancel = CancelGate(logs_dir)
    pause = PauseGate(logs_dir)
    cancel.clear()
    pause.clear()

    job = JobState(logger.job_path)
    job.set_meta(dryRun=bool(dry_run), outDir=str(out_dir), mode="heal")
    cancel.install_sigint(lambda: job.run_id)

    def _pause_hooks(resume_phase: str) -> tuple[Any, Any]:
        def on_pause() -> None:
            job.emit("pause_requested", source=pause.source, phase=resume_phase)
            job.set_phase("paused")
            job.emit("paused")
            logger.log(f"Paused (phase was {resume_phase}). Waiting for --resume …")

        def on_resume() -> None:
            job.emit("resumed", phase=resume_phase)
            job.set_phase(resume_phase)
            logger.log(f"Resumed → {resume_phase}")

        return on_pause, on_resume

    try:
        lock = RunLock.acquire(out_dir, job.run_id)
    except RunLockError as e:
        job.emit("lock_denied", detail=str(e), **e.lock_info)
        job.set_phase("error")
        logger.log(f"ERROR: {e}")
        return 3

    job.emit("lock_acquired", lockPath=str(lock.path), pid=os.getpid())
    job.set_meta(lockPath=str(lock.path))

    try:
        if keep_partials:
            job.emit("partial_sweep_skipped", reason="keep_partials")
        else:
            removed = purge_stale_partials(out_dir)
            job.set_count("partialsPurged", len(removed))
            job.emit(
                "partial_purged",
                count=len(removed),
                sample=[p.name for p in removed[:20]],
            )
            if removed:
                logger.log(f"Purged {len(removed)} stale partial file(s)")

        diag = index_diagnostics(out_dir)
        logger.log(f"{format_index_log_line(diag)} under {out_dir}")

        on_pause_h, on_resume_h = _pause_hooks("healing")

        def _cancel() -> bool:
            return cancel.is_requested()

        def _pause() -> bool:
            return pause.wait_if_paused(
                cancel,
                resume_phase="healing",
                on_pause=on_pause_h,
                on_resume=on_resume_h,
            )

        counts = heal_library(
            client=client,
            out_dir=out_dir,
            build_cm_info=build_cm_info,
            log=logger.log,
            job=job,
            dry_run=dry_run,
            purge_orphans=purge_orphans,
            cancel_check=_cancel,
            pause_wait=_pause,
        )
        if counts.get("cancelled"):
            job.set_phase("cancelled")
            cancel.clear()
            pause.clear()
            logger.log(f"Heal cancelled. Counts: {json.dumps(counts, sort_keys=True)}")
            return 4

        job.set_phase("done")
        cancel.clear()
        pause.clear()
        diag2 = index_diagnostics(out_dir)
        logger.log(f"Heal done. Counts: {json.dumps(counts, sort_keys=True)}")
        logger.log(f"After heal: {format_index_log_line(diag2)}")
        logger.log("Refresh Stability Matrix model index if Installed badges look stale.")
        return 0
    except Exception:
        job.emit("run_error")
        job.set_phase("error")
        raise
    finally:
        lock.release()
        job.emit("lock_released", lockPath=str(lock.path))
