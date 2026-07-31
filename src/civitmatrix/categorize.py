"""Auto-sort library weights into category bucket folders."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from civitmatrix.indexer import CM_INFO_SUFFIX, iter_weight_paths
from civitmatrix.sm_sidecars import sort_hints_from_tags

BUCKETS = ("characters", "styles", "concepts", "clothes", "uncategorized")

# Priority: character > clothing > style > concept > uncategorized
_BUCKET_RULES: tuple[tuple[frozenset[str], str, str], ...] = (
    (frozenset({"character", "characters"}), "characters", "character"),
    (frozenset({"clothing", "clothes", "costume"}), "clothes", "clothing"),
    (frozenset({"style", "styles"}), "styles", "style"),
    (frozenset({"concept", "concepts"}), "concepts", "concept"),
)


def bucket_from_tags(tags: list[Any]) -> tuple[str, str]:
    """
    Resolve ``(bucket_dir_name, reason)`` from cm-info / Civit tags.

    Uses ``sort_hints_from_tags`` suggested buckets plus raw tag names.
    """
    hints = sort_hints_from_tags(tags or [])
    suggested = {
        str(t).lower()
        for t in (hints.get("suggestedBuckets") or [])
        if isinstance(t, str) and t
    }
    # Also accept any tag name that matches category vocabulary.
    for t in tags or []:
        name = t if isinstance(t, str) else (t.get("name") if isinstance(t, dict) else None)
        if isinstance(name, str) and name:
            suggested.add(name.lower())
    for tokens, bucket, reason in _BUCKET_RULES:
        if suggested & tokens:
            return bucket, reason
    return "uncategorized", "uncategorized"


def _read_tags(info_path: Path | None) -> list[Any]:
    if info_path is None or not info_path.is_file():
        return []
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    tags = data.get("Tags")
    return tags if isinstance(tags, list) else []


def _bundle_paths(weight: Path) -> list[Path]:
    """Weight + matching sidecars/previews in the same directory (basename stem)."""
    stem = weight.stem
    parent = weight.parent
    paths: list[Path] = [weight]
    info = parent / f"{stem}{CM_INFO_SUFFIX}"
    if info.is_file():
        paths.append(info)
    swarm = parent / f"{stem}.swarm.json"
    if swarm.is_file():
        paths.append(swarm)
    for p in sorted(parent.glob(f"{stem}.preview.*")):
        if not p.is_file():
            continue
        if p.name.endswith(".partial"):
            continue
        paths.append(p)
    return paths


def _rel_dir(out_dir: Path, path: Path) -> str:
    """Posix relative parent of ``path`` under ``out_dir`` (``.`` for root)."""
    rel = path.parent.relative_to(out_dir)
    if rel == Path("."):
        return "."
    return rel.as_posix()


def plan_categorize(out_dir: Path) -> list[dict[str, Any]]:
    """
    Build a move plan for weights under ``out_dir``.

    Each entry: ``{stem, fromDir, toDir, reason, paths}``.
    ``stem`` is the basename stem; destination is ``out_dir / toDir / {basename}``.
    Skips installs already sitting directly in the correct bucket folder.
    """
    out_dir = out_dir.resolve()
    plan: list[dict[str, Any]] = []
    if not out_dir.is_dir():
        return plan

    for weight in iter_weight_paths(out_dir, recursive=True):
        stem = weight.stem
        info = weight.parent / f"{stem}{CM_INFO_SUFFIX}"
        tags = _read_tags(info if info.is_file() else None)
        to_dir, reason = bucket_from_tags(tags)
        from_dir = _rel_dir(out_dir, weight)
        if from_dir == to_dir:
            continue
        paths = _bundle_paths(weight)
        plan.append(
            {
                "stem": stem,
                "fromDir": from_dir,
                "toDir": to_dir,
                "reason": reason,
                "paths": [str(p) for p in paths],
            }
        )
    return plan


def apply_categorize(
    out_dir: Path,
    plan: list[dict[str, Any]],
    *,
    dry_run: bool = True,
) -> dict[str, int]:
    """
    Apply a categorize plan. Default ``dry_run=True`` (no filesystem writes).

    Moves each listed path into ``out_dir / toDir / <basename>`` (flat bucket).
    """
    out_dir = out_dir.resolve()
    counts = {
        "planned": len(plan),
        "moved": 0,
        "skipped": 0,
        "errors": 0,
        "files": 0,
    }
    for entry in plan:
        to_name = str(entry.get("toDir") or "")
        if to_name not in BUCKETS:
            counts["errors"] += 1
            continue
        dest_dir = out_dir / to_name
        path_strs = entry.get("paths") or []
        if not isinstance(path_strs, list) or not path_strs:
            counts["skipped"] += 1
            continue
        if dry_run:
            counts["moved"] += 1
            counts["files"] += len(path_strs)
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            for raw in path_strs:
                src = Path(raw)
                if not src.is_file():
                    continue
                dest = dest_dir / src.name
                if dest.resolve() == src.resolve():
                    continue
                if dest.exists():
                    raise FileExistsError(f"destination exists: {dest}")
                shutil.move(str(src), str(dest))
                counts["files"] += 1
            counts["moved"] += 1
        except OSError:
            counts["errors"] += 1
    return counts
