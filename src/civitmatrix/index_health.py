"""Diagnose local model index gaps (stems vs blake3 vs versions)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from civitmatrix.indexer import (
    iter_cm_info_paths,
    iter_weight_paths,
    relative_pair_stem,
)


def load_cm_info(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def index_diagnostics(out_dir: Path, *, recursive: bool = True) -> dict[str, Any]:
    """Summarize why blake3 / version / stem counts may diverge."""
    if out_dir.is_dir():
        weights = {
            relative_pair_stem(out_dir, p): p
            for p in iter_weight_paths(out_dir, recursive=recursive)
        }
        infos = {
            relative_pair_stem(out_dir, p, cm_info=True): p
            for p in iter_cm_info_paths(out_dir, recursive=recursive)
        }
    else:
        weights = {}
        infos = {}

    blake3s: set[str] = set()
    versions: set[int] = set()
    missing_blake3: list[str] = []
    missing_version: list[str] = []
    missing_model: list[str] = []
    weight_no_info: list[str] = []
    info_no_weight: list[str] = []
    empty_weights: list[str] = []

    for stem, wp in sorted(weights.items()):
        try:
            if wp.stat().st_size <= 0:
                empty_weights.append(stem)
        except OSError:
            empty_weights.append(stem)
        if stem not in infos:
            weight_no_info.append(stem)

    for stem, ip in sorted(infos.items()):
        if stem not in weights:
            info_no_weight.append(stem)
        cm = load_cm_info(ip)
        if cm is None:
            missing_blake3.append(stem)
            missing_version.append(stem)
            missing_model.append(stem)
            continue
        h = (cm.get("Hashes") or {}).get("BLAKE3")
        if h:
            blake3s.add(str(h).upper())
        else:
            missing_blake3.append(stem)
        vid = cm.get("VersionId")
        if vid is not None:
            try:
                versions.add(int(vid))
            except (TypeError, ValueError):
                missing_version.append(stem)
        else:
            missing_version.append(stem)
        if cm.get("ModelId") is None:
            missing_model.append(stem)

    return {
        "stems": len(set(weights) | set(infos)),
        "weights": len(weights),
        "infos": len(infos),
        "blake3": len(blake3s),
        "versions": len(versions),
        "missingBlake3": missing_blake3,
        "missingVersionId": missing_version,
        "missingModelId": missing_model,
        "weightNoInfo": weight_no_info,
        "infoNoWeight": info_no_weight,
        "emptyWeights": empty_weights,
    }


def format_index_log_line(diag: dict[str, Any]) -> str:
    extras = []
    for key, label in (
        ("missingBlake3", "missingBlake3"),
        ("missingVersionId", "missingVersionId"),
        ("weightNoInfo", "weightNoInfo"),
        ("infoNoWeight", "orphanInfo"),
        ("emptyWeights", "emptyWeights"),
    ):
        n = len(diag.get(key) or [])
        if n:
            extras.append(f"{label}={n}")
    base = (
        f"Local index: {diag['blake3']} blake3, {diag['versions']} versions, "
        f"{diag['stems']} stems"
    )
    if extras:
        return f"{base} ({', '.join(extras)})"
    return base
