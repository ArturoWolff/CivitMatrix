from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from civitmatrix.cancel_control import CancelGate
from civitmatrix.catalog import iter_filtered_models
from civitmatrix.client import CivitClient
from civitmatrix.disk_guard import (
    below_floor,
    disk_status,
    floor_bytes_from_gib,
    format_bytes,
)
from civitmatrix.download_one import (
    _index_lock,
    _model_fields,
    _resolve_versions,
    process_one,
)
from civitmatrix.heal_library import heal_library
from civitmatrix.index_health import format_index_log_line, index_diagnostics
from civitmatrix.indexer import load_local_index
from civitmatrix.job_state import JobState
from civitmatrix.listing_cache import (
    ListingCacheWriter,
    cache_paths,
    iter_cached_models,
    make_cache_key,
    probe_cache,
)
from civitmatrix.logging_io import RunLogger, utc_now
from civitmatrix.sm_sidecars import build_cm_info
from civitmatrix.model_filters import model_passes_filters
from civitmatrix.partial_sweep import purge_stale_partials
from civitmatrix.pause_control import PauseGate
from civitmatrix.run_lock import RunLock, RunLockError
from civitmatrix.stream_run import run_streaming_pool

# Re-exports for stable imports
__all__ = ["process_one", "run_batch", "run_heal", "_resolve_versions"]

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
    resume: bool = True,
    skip_verify: bool = False,
    use_listing_cache: bool = False,
    refresh_listing: bool = False,
    disk_floor_gib: float = 2.0,
    keep_old_versions: bool = False,
    tag_include: list[str] | None = None,
    tag_exclude: list[str] | None = None,
    category: str = "",
    users: list[str] | None = None,
    file_format: str = "",
    checkpoint_type: str = "All",
    updated_from: str = "",
    updated_to: str = "",
    selection_map: dict[int, list] | None = None,
    write_swarm: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = logger.job_path.parent
    cancel = CancelGate(logs_dir)
    pause = PauseGate(logs_dir)
    cancel.clear()  # drop stale flags from a previous run
    pause.clear()

    disk_floor_bytes = floor_bytes_from_gib(disk_floor_gib)

    job = JobState(logger.job_path)
    job.set_meta(
        dryRun=bool(dry_run),
        baseModel=base_model,
        modelType=model_type,
        sort=sort,
        outDir=str(out_dir),
        resumePartials=bool(resume),
        skipVerify=bool(skip_verify),
        streamMode=True,
        listingCache=bool(use_listing_cache),
        refreshListing=bool(refresh_listing),
        listingCacheHit=False,
        diskFloorGib=float(disk_floor_gib),
        writeSwarm=bool(write_swarm),
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
        logger.log("Keeping all temps including preview downloads (--keep-partials)")
    else:
        # Keep *.safetensors.partial for Range resume; purge preview junk only
        removed = purge_stale_partials(out_dir, keep_weight_partials=True)
        job.set_count("partialsPurged", len(removed))
        sample = [p.name for p in removed[:20]]
        job.emit("partial_purged", count=len(removed), sample=sample)
        if removed:
            logger.log(
                f"Purged {len(removed)} stale preview/temp file(s) "
                "(weight *.safetensors.partial kept for resume)"
            )

    local_blake3, local_versions, local_stems = load_local_index(out_dir)
    diag = index_diagnostics(out_dir)
    logger.log(f"{format_index_log_line(diag)} under {out_dir}")

    st0 = disk_status(out_dir)
    job.set_meta(diskFree=st0["free"])
    job.emit(
        "disk_status",
        free=st0["free"],
        total=st0["total"],
        floor=disk_floor_bytes,
        path=str(out_dir),
    )
    logger.log(
        f"Disk free={format_bytes(st0['free'])} / total={format_bytes(st0['total'])} "
        f"floor={format_bytes(disk_floor_bytes) if disk_floor_bytes else 'off'}"
    )
    if disk_floor_bytes > 0 and st0["free"] < disk_floor_bytes:
        job.emit(
            "disk_full",
            free=st0["free"],
            floor=disk_floor_bytes,
            path=str(out_dir),
            fatal=True,
        )
        job.set_phase("error")
        logger.log(
            f"ERROR: free disk {format_bytes(st0['free'])} below floor "
            f"{format_bytes(disk_floor_bytes)} — aborting (exit 5)"
        )
        cancel.clear()
        pause.clear()
        lock.release()
        job.emit("lock_released", lockPath=str(lock.path))
        return 5

    try:
        job.set_phase("running")
        job.emit("stream_start", concurrency=concurrency, dryRun=dry_run)
        logger.log(
            f"Streaming {model_type} base={base_model!r} "
            f"(concurrency={concurrency}, dry_run={dry_run}, limit={limit or 'all'})"
        )

        cancel_logged = False
        on_pause_run, on_resume_run = _pause_hooks("running")
        counts_lock = threading.Lock()

        def worker(model: dict[str, Any]) -> str:
            nonlocal cancel_logged
            if pause.wait_if_paused(
                cancel,
                resume_phase="running",
                on_pause=on_pause_run,
                on_resume=on_resume_run,
            ):
                with counts_lock:
                    if not cancel_logged:
                        job.emit(
                            "cancel_requested",
                            source=cancel.source,
                            phase="running",
                        )
                        cancel_logged = True
                logger.fail_with_event(
                    job,
                    model,
                    "cancelled",
                    retryable=True,
                    **_model_fields(model),
                )
                return "cancelled"
            if cancel.is_requested():
                with counts_lock:
                    if not cancel_logged:
                        job.emit(
                            "cancel_requested",
                            source=cancel.source,
                            phase="running",
                        )
                        cancel_logged = True
                logger.fail_with_event(
                    job,
                    model,
                    "cancelled",
                    retryable=True,
                    **_model_fields(model),
                )
                return "cancelled"
            job.set_current(model)
            try:
                mid = int(model["id"])
            except (KeyError, TypeError, ValueError):
                mid = -1
            sel = selection_map or {}
            version_specs = sel.get(mid, ["latest"]) if sel else ["latest"]
            versions = _resolve_versions(
                model,
                version_specs,
                base_model=base_model,
                match_base_version=match_base_version,
            )
            if not versions:
                logger.fail_with_event(
                    job,
                    model,
                    "no_matching_base_version",
                    retryable=False,
                    **_model_fields(model),
                )
                return "no_match"
            multi = len(versions) > 1
            last = "ok"
            for ver in versions:
                last = process_one(
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
                    resume=resume,
                    skip_verify=skip_verify,
                    disk_floor_bytes=disk_floor_bytes,
                    keep_old_versions=keep_old_versions or multi,
                    force_version=ver,
                    write_swarm=write_swarm,
                )
                if last in {"cancelled", "disk_full"}:
                    return last
            return last

        def on_listed(n: int, model: dict[str, Any]) -> None:
            job.set_count("listed", n)
            if n % 50 == 0:
                job.emit("listing_progress", listed=n)
                logger.log(f"Listed {n} models…")

        def on_worker_crash(model: dict[str, Any], err: BaseException) -> None:
            logger.fail_with_event(
                job,
                model,
                "worker_crash",
                retryable=True,
                extra={"detail": repr(err)},
                detail=repr(err),
                modelId=model.get("id"),
                modelName=model.get("name"),
            )

        def on_result(status: str, snapshot: dict[str, int]) -> None:
            for key, value in snapshot.items():
                job.set_count(key, value)
            job.set_count("processed", sum(snapshot.values()))

        def should_stop() -> bool:
            nonlocal cancel_logged
            if cancel.is_requested():
                with counts_lock:
                    if not cancel_logged:
                        job.emit(
                            "cancel_requested",
                            source=cancel.source,
                            phase="running",
                        )
                        cancel_logged = True
                return True
            return pause.wait_if_paused(
                cancel,
                resume_phase="running",
                on_pause=on_pause_run,
                on_resume=on_resume_run,
            )

        listing_state: dict[str, Any] = {
            "writer": None,
            "exhausted": False,
            "cache_hit": False,
        }

        model_iter = _iter_models_for_run(
            client,
            logger,
            job,
            listing_state,
            retry_failed=retry_failed,
            base_model=base_model,
            model_type=model_type,
            nsfw=nsfw,
            sort=sort,
            use_listing_cache=use_listing_cache,
            refresh_listing=refresh_listing,
            tag_include=tag_include or [],
            tag_exclude=tag_exclude or [],
            category=category,
            users=users or [],
            file_format=file_format,
            checkpoint_type=checkpoint_type,
            updated_from=updated_from,
            updated_to=updated_to,
            selection_map=selection_map,
        )

        counts: dict[str, int] = {}
        cancelled = False
        listed = 0
        try:
            counts, cancelled, listed = run_streaming_pool(
                model_iter,
                worker=worker,
                concurrency=concurrency,
                limit=limit,
                should_stop=should_stop,
                on_listed=on_listed,
                on_worker_crash=on_worker_crash,
                on_result=on_result,
            )
        finally:
            writer = listing_state["writer"]
            if writer is not None:
                limited = bool(limit) and listed >= limit
                complete = (
                    bool(listing_state["exhausted"])
                    and not cancelled
                    and not limited
                )
                meta = writer.finalize(complete=complete)
                job.emit(
                    "listing_cache_write",
                    key=writer.key,
                    path=str(writer.jsonl_path),
                    complete=complete,
                    pages=meta.get("pages"),
                    items=meta.get("items"),
                )
                logger.log(
                    f"Listing cache write complete={complete} "
                    f"pages={meta.get('pages')} items={meta.get('items')}"
                )

        job.set_count("listed", listed)
        if not cancelled:
            job.emit("listing_done", listed=listed)

        if cancelled or counts.get("cancelled"):
            job.emit("run_cancelled", counts=dict(counts), listed=listed)
            job.set_phase("cancelled")
            cancel.clear()
            pause.clear()
            logger.log(f"Cancelled. Counts: {json.dumps(counts, sort_keys=True)}")
            logger.log(f"Job status -> {logger.job_path}")
            return 4

        if counts.get("disk_full"):
            job.emit("run_disk_full", counts=dict(counts), listed=listed)
            job.set_phase("error")
            cancel.clear()
            pause.clear()
            logger.log(f"Stopped: disk below floor. Counts: {json.dumps(counts, sort_keys=True)}")
            return 5

        job.emit("run_done", counts=dict(counts), listed=listed)
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


def _iter_models_for_run(
    client: CivitClient,
    logger: RunLogger,
    job: JobState,
    listing_state: dict[str, Any],
    *,
    retry_failed: bool,
    base_model: str,
    model_type: str,
    nsfw: bool,
    sort: str,
    use_listing_cache: bool = False,
    refresh_listing: bool = False,
    tag_include: list[str] | None = None,
    tag_exclude: list[str] | None = None,
    category: str = "",
    users: list[str] | None = None,
    file_format: str = "",
    checkpoint_type: str = "All",
    updated_from: str = "",
    updated_to: str = "",
    selection_map: dict[int, list] | None = None,
) -> Iterator[dict[str, Any]]:
    logs_dir = logger.job_path.parent
    tag_include = tag_include or []
    tag_exclude = tag_exclude or []
    users = users or []

    def _passes(model: dict[str, Any]) -> bool:
        return model_passes_filters(
            model,
            tag_include=tag_include,
            tag_exclude=tag_exclude,
            category=category,
            users=users,
            file_format=file_format,
            updated_from=updated_from or None,
            updated_to=updated_to or None,
        )

    if retry_failed:
        ids = logger.load_failed_model_ids()
        logger.log(f"Retry mode: {len(ids)} unique retryable modelIds")
        for mid in ids:
            try:
                yield client.get_model(mid)
            except Exception as e:
                logger.record_failure(
                    {"id": mid, "name": None},
                    "retry_fetch_failed",
                    extra={"detail": repr(e)},
                )
            time.sleep(0.2)
        listing_state["exhausted"] = True
        return

    if selection_map:
        logger.log(f"Selection mode: {len(selection_map)} modelId(s)")
        for mid in selection_map:
            try:
                model = client.get_model(mid)
            except Exception as e:
                logger.record_failure(
                    {"id": mid, "name": None},
                    "selection_fetch_failed",
                    extra={"detail": repr(e)},
                )
                time.sleep(0.2)
                continue
            if _passes(model):
                yield model
            time.sleep(0.15)
        listing_state["exhausted"] = True
        return

    key_fields = {
        "baseUrl": client.base_url.rstrip("/"),
        "baseModel": base_model,
        "modelType": model_type,
        "sort": sort,
        "nsfw": bool(nsfw),
    }
    key = make_cache_key(
        base_url=key_fields["baseUrl"],
        base_model=key_fields["baseModel"],
        model_type=key_fields["modelType"],
        sort=key_fields["sort"],
        nsfw=key_fields["nsfw"],
    )
    use_cache = bool(use_listing_cache)
    refresh = bool(refresh_listing)

    if use_cache and not refresh:
        reason, meta = probe_cache(
            logs_dir,
            base_url=key_fields["baseUrl"],
            base_model=key_fields["baseModel"],
            model_type=key_fields["modelType"],
            sort=key_fields["sort"],
            nsfw=key_fields["nsfw"],
        )
        if reason == "ok" and meta is not None:
            _, jsonl = cache_paths(logs_dir, key)
            listing_state["cache_hit"] = True
            job.set_meta(listingCacheHit=True)
            job.emit(
                "listing_cache_hit",
                key=key,
                items=meta.get("items"),
                pages=meta.get("pages"),
                path=str(jsonl),
            )
            logger.log(f"Listing cache hit ({meta.get('items')} items) → {jsonl}")
            for model in iter_cached_models(jsonl):
                if _passes(model):
                    yield model
            listing_state["exhausted"] = True
            return
        job.emit("listing_cache_miss", key=key, reason=reason)
        logger.log(f"Listing cache miss ({reason}); fetching from API")
    elif refresh:
        job.emit("listing_cache_miss", key=key, reason="refresh")

    logger.log(
        f"Listing {model_type} for base={base_model!r} sort={sort!r} "
        f"from {client.base_url} …"
    )
    writer = None
    if use_cache:
        writer = ListingCacheWriter(logs_dir, key=key, key_fields=key_fields)
        writer.begin()
        listing_state["writer"] = writer

    def on_page(*, page: int, next_page: str | None, items: list) -> None:
        if writer is not None:
            writer.append_page(page=page, next_page=next_page, items=items)

    username = users[0] if len(users) == 1 else None
    for model in iter_filtered_models(
        client,
        base_model=base_model,
        model_type=model_type,
        nsfw=nsfw,
        sort=sort,
        tag_include=tag_include,
        tag_exclude=tag_exclude,
        category=category,
        users=users,
        file_format=file_format,
        checkpoint_type=checkpoint_type,
        updated_from=updated_from or None,
        updated_to=updated_to or None,
        username=username,
        on_page=on_page,
    ):
        yield model
    listing_state["exhausted"] = True


def run_heal(
    *,
    client: CivitClient,
    out_dir: Path,
    logger: RunLogger,
    dry_run: bool,
    purge_orphans: bool,
    keep_partials: bool = False,
    refresh_sidecars: bool = False,
    write_swarm: bool = False,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = logger.job_path.parent
    cancel = CancelGate(logs_dir)
    pause = PauseGate(logs_dir)
    cancel.clear()
    pause.clear()

    job = JobState(logger.job_path)
    job.set_meta(
        dryRun=bool(dry_run),
        outDir=str(out_dir),
        mode="heal",
        refreshSidecars=bool(refresh_sidecars),
        writeSwarm=bool(write_swarm),
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

    try:
        if keep_partials:
            job.emit("partial_sweep_skipped", reason="keep_partials")
        else:
            removed = purge_stale_partials(out_dir, keep_weight_partials=True)
            job.set_count("partialsPurged", len(removed))
            job.emit(
                "partial_purged",
                count=len(removed),
                sample=[p.name for p in removed[:20]],
            )
            if removed:
                logger.log(
                    f"Purged {len(removed)} stale preview/temp file(s) "
                    "(weight *.safetensors.partial kept for resume)"
                )

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
            refresh_sidecars=refresh_sidecars,
            write_swarm=write_swarm,
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
