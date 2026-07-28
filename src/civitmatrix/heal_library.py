"""Consolidate local model folder: complete sidecars, purge orphans, fix bad weights."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.index_health import index_diagnostics, load_cm_info
from civitmatrix.preview_media import finalize_preview_file, find_preview_path, pick_preview_url
from civitmatrix.verify_blake3 import remote_blake3_from_file_info, verify_weight_blake3

LogFn = Callable[[str], None]
BuildCmFn = Callable[
    [dict[str, Any], dict[str, Any], dict[str, Any], str, Path], dict[str, Any]
]


def _sidecar_incomplete(cm: dict[str, Any] | None) -> bool:
    if cm is None:
        return True
    if cm.get("ModelId") is None or cm.get("VersionId") is None:
        return True
    if not (cm.get("Hashes") or {}).get("BLAKE3"):
        return True
    return False


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


def _delete_weight_bundle(out_dir: Path, stem: str, *, dry_run: bool) -> list[str]:
    removed: list[str] = []
    for p in [
        out_dir / f"{stem}.safetensors",
        out_dir / f"{stem}.cm-info.json",
        *out_dir.glob(f"{stem}.preview.*"),
    ]:
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
            diagnostics={
                k: (len(v) if isinstance(v, list) else v) for k, v in diag.items()
            },
        )

    weights = {p.stem: p for p in out_dir.glob("*.safetensors")}
    infos = {
        p.name[: -len(".cm-info.json")]: p for p in out_dir.glob("*.cm-info.json")
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
                    if _redownload_version(
                        client,
                        out_dir,
                        stem,
                        int(version_id),
                        build_cm_info=build_cm_info,
                        log=log,
                        dry_run=dry_run,
                    ):
                        bump("heal_redownloaded")
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
                elif _redownload_version(
                    client,
                    out_dir,
                    stem,
                    int(version_id),
                    build_cm_info=build_cm_info,
                    log=log,
                    dry_run=False,
                ):
                    bump("heal_redownloaded")
                else:
                    bump("heal_redownload_failed")
                continue
            log(f"HEAL orphan sidecar (no VersionId) stem={stem}")
            bump("heal_orphan_unresolved")
            if job:
                job.emit("heal_orphan_unresolved", stem=stem)
            continue

        assert weight is not None
        if not _sidecar_incomplete(cm):
            bump("heal_ok")
            continue

        try:
            local_hash = file_blake3_hex(weight)
        except OSError as e:
            log(f"HEAL hash failed stem={stem}: {e}")
            bump("heal_hash_failed")
            continue

        recorded = ((cm or {}).get("Hashes") or {}).get("BLAKE3")
        if recorded and str(recorded).upper() != local_hash:
            log(f"HEAL hash mismatch stem={stem} — trusting local file")
            bump("heal_hash_mismatch")

        version: dict[str, Any] | None = None
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
            payload = build_cm_info(model, version, file_info, stem, out_dir)
            if preview is not None:
                payload["ThumbnailImageUrl"] = str(preview)
            info_out = out_dir / f"{stem}.cm-info.json"
            _write_sidecar(info_out, payload, dry_run=dry_run)
        except Exception as e:
            log(f"HEAL sidecar write failed stem={stem} (keeping weight): {e!r}")
            bump("heal_sidecar_failed")
            continue
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
) -> bool:
    try:
        version = client.get_json(f"{client.base_url}/api/v1/model-versions/{version_id}")
    except Exception as e:
        log(f"HEAL redownload version fetch failed ver={version_id}: {e}")
        return False

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
        return False

    download_url = file_info.get("downloadUrl") or (
        f"{client.base_url}/api/download/models/{version_id}"
    )
    weight_path = out_dir / f"{stem}.safetensors"
    if dry_run:
        return True
    try:
        client.download(download_url, weight_path)
    except Exception as e:
        log(f"HEAL redownload failed stem={stem}: {e}")
        weight_path.unlink(missing_ok=True)
        return False

    remote_h = remote_blake3_from_file_info(file_info)
    v_status, local_hash, v_reason = verify_weight_blake3(
        weight_path, remote_h, skip=False
    )
    if v_status == "fail":
        log(f"HEAL verify fail stem={stem} reason={v_reason}")
        weight_path.unlink(missing_ok=True)
        return False

    model_id = version.get("modelId")
    if model_id is not None:
        try:
            model = client.get_model(int(model_id))
        except Exception:
            model = _model_from_version_payload(version)
    else:
        model = _model_from_version_payload(version)

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
        payload = build_cm_info(model, version, file_info, stem, out_dir)
        if preview is not None:
            payload["ThumbnailImageUrl"] = str(preview)
        (out_dir / f"{stem}.cm-info.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        # Weight already verified — never delete it because sidecar write failed.
        log(f"HEAL sidecar write failed stem={stem} (keeping weight): {e!r}")
        return False
    return True
