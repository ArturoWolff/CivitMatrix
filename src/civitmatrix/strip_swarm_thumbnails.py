from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_swarm_thumbnails(out_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    counts = {"scanned": 0, "stripped": 0, "unchanged": 0, "errors": 0}
    if not out_dir.is_dir():
        return counts
    for path in sorted(out_dir.glob("*.swarm.json")):
        counts["scanned"] += 1
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            counts["errors"] += 1
            continue
        if "modelspec.thumbnail" not in data:
            counts["unchanged"] += 1
            continue
        data.pop("modelspec.thumbnail", None)
        counts["stripped"] += 1
        if not dry_run:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return counts
