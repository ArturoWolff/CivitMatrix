from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from civitmatrix.indexer import cm_info_basename_stem, iter_cm_info_paths
from civitmatrix.sm_sidecars import build_swarm_json, swarm_architecture_for


def _base_url_from_source(source_url: str | None) -> str:
    if not source_url or not isinstance(source_url, str):
        return ""
    parsed = urlparse(source_url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _model_version_from_cm(
    cm: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    tags = cm.get("Tags") or []
    model: dict[str, Any] = {
        "id": cm.get("ModelId"),
        "name": cm.get("ModelName") or "",
        "type": cm.get("ModelType") or "LORA",
        "description": cm.get("ModelDescription") or "",
        "tags": tags,
        "creator": {"username": cm.get("AuthorUsername") or ""},
    }
    version: dict[str, Any] = {
        "id": cm.get("VersionId"),
        "name": cm.get("VersionName") or "",
        "baseModel": cm.get("BaseModel"),
        "trainedWords": cm.get("TrainedWords") or [],
        "description": cm.get("VersionDescription") or "",
    }
    return model, version


def _minimal_swarm_from_cm(cm: dict[str, Any], architecture: str) -> dict[str, Any]:
    model_name = (cm.get("ModelName") or "").strip()
    version_name = (cm.get("VersionName") or "").strip()
    title = f"{model_name} - {version_name}" if version_name else model_name
    if not title:
        title = "model"
    trained = cm.get("TrainedWords") or []
    trigger = ", ".join(str(t) for t in trained if t)
    tags = cm.get("Tags") or []
    tag_names = [t if isinstance(t, str) else t.get("name") for t in tags]
    tag_names = [t for t in tag_names if t]
    payload: dict[str, Any] = {
        "modelspec.title": title,
        "modelspec.author": cm.get("AuthorUsername") or "",
        "modelspec.trigger_phrase": trigger,
        "modelspec.tags": ", ".join(str(t) for t in tag_names),
        "modelspec.architecture": architecture,
    }
    source = cm.get("SourceUrl")
    if source:
        payload["modelspec.description"] = (
            f'From <a href="{source}" target="_blank">{source}</a>\n'
        )
    return payload


def fix_swarm_architecture(out_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    """
    Set ``modelspec.architecture`` on ``*.swarm.json`` from local ``*.cm-info.json``.

    Never touches ``.safetensors`` or previews. No API calls.
    """
    counts = {
        "scanned": 0,
        "updated": 0,
        "created": 0,
        "skipped_unknown": 0,
        "skipped_unchanged": 0,
        "errors": 0,
    }
    if not out_dir.is_dir():
        return counts

    for info_path in iter_cm_info_paths(out_dir, recursive=True):
        counts["scanned"] += 1
        stem = cm_info_basename_stem(info_path)
        parent = info_path.parent
        swarm_path = parent / f"{stem}.swarm.json"
        try:
            parsed: Any = json.loads(info_path.read_text(encoding="utf-8"))
        except Exception:
            counts["errors"] += 1
            continue
        if not isinstance(parsed, dict):
            counts["errors"] += 1
            continue
        cm: dict[str, Any] = parsed
        arch = swarm_architecture_for(cm.get("BaseModel"), cm.get("ModelType"))
        if not arch:
            counts["skipped_unknown"] += 1
            continue

        existing: dict[str, Any] | None = None
        if swarm_path.is_file():
            try:
                raw_swarm: Any = json.loads(swarm_path.read_text(encoding="utf-8"))
            except Exception:
                counts["errors"] += 1
                continue
            if not isinstance(raw_swarm, dict):
                counts["errors"] += 1
                continue
            existing = raw_swarm
            if existing.get("modelspec.architecture") == arch:
                counts["skipped_unchanged"] += 1
                continue
            existing["modelspec.architecture"] = arch
            counts["updated"] += 1
            if not dry_run:
                swarm_path.write_text(
                    json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            continue

        # Missing .swarm.json — create from cm-info (no API).
        model, version = _model_version_from_cm(cm)
        base_url = _base_url_from_source(cm.get("SourceUrl"))
        payload = build_swarm_json(model, version, base_url=base_url)
        if payload is None:
            payload = _minimal_swarm_from_cm(cm, arch)
        else:
            payload["modelspec.architecture"] = arch
        counts["created"] += 1
        if not dry_run:
            swarm_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    return counts
