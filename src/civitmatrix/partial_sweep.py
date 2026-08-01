"""Purge stale download temps in an output directory before a run."""

from __future__ import annotations

from pathlib import Path


def iter_stale_partials(
    out_dir: Path,
    *,
    keep_weight_partials: bool = True,
) -> list[Path]:
    """
    Non-recursive temps in out_dir.
    By default keeps weight ``*.partial`` temps (``.safetensors.partial``,
    ``.gguf.partial``, ``.sft.partial``) for HTTP Range resume; still collects
    preview download temps and other ``*.partial`` junk.
    """
    if not out_dir.is_dir():
        return []
    _weight_partial_suffixes = (
        ".safetensors.partial",
        ".gguf.partial",
        ".sft.partial",
    )
    found: list[Path] = []
    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if keep_weight_partials and name.endswith(_weight_partial_suffixes):
            continue
        if ".preview.download" in name:
            found.append(p)
            continue
        if name.endswith(".partial"):
            found.append(p)
    return sorted(found)


def purge_stale_partials(
    out_dir: Path,
    *,
    keep_weight_partials: bool = True,
) -> list[Path]:
    """Delete stale temps; return paths that were removed."""
    removed: list[Path] = []
    for p in iter_stale_partials(out_dir, keep_weight_partials=keep_weight_partials):
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            continue
    return removed
