"""Purge stale download temps in an output directory before a run."""

from __future__ import annotations

from pathlib import Path


def iter_stale_partials(out_dir: Path) -> list[Path]:
    """Non-recursive: *.partial and *.preview.download* in out_dir only."""
    if not out_dir.is_dir():
        return []
    found: list[Path] = []
    for p in out_dir.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if name.endswith(".partial"):
            found.append(p)
            continue
        if ".preview.download" in name:
            found.append(p)
    return sorted(found)


def purge_stale_partials(out_dir: Path) -> list[Path]:
    """Delete stale temps; return paths that were removed."""
    removed: list[Path] = []
    for p in iter_stale_partials(out_dir):
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            continue
    return removed
