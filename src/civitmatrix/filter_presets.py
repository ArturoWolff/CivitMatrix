"""Save / load / list named filter presets under logs/filter-presets/."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PRESET_DIRNAME = "filter-presets"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Keys stored in preset JSON (camelCase, aligned with UI / job manifest).
PRESET_KEYS = (
    "type",
    "baseModel",
    "sort",
    "nsfw",
    "format",
    "checkpointType",
    "updatedFrom",
    "updatedTo",
    "category",
    "users",
    "usersDeny",
    "tagInclude",
    "tagExclude",
    "minDownloads",
    "minLikes",
    "baseOnly",
    "maxNsfwLevel",
)


class FilterPresetError(ValueError):
    """Invalid preset name or payload."""


def presets_dir(logs_dir: Path) -> Path:
    return Path(logs_dir) / PRESET_DIRNAME


def validate_preset_name(name: str) -> str:
    n = str(name or "").strip()
    if not n or not _SAFE_NAME.match(n):
        raise FilterPresetError(
            "preset name must be 1–64 chars: letters, digits, . _ - (start alnum)"
        )
    return n


def preset_path(logs_dir: Path, name: str) -> Path:
    safe = validate_preset_name(name)
    return presets_dir(logs_dir) / f"{safe}.json"


def list_filter_presets(logs_dir: Path) -> list[str]:
    root = presets_dir(logs_dir)
    if not root.is_dir():
        return []
    names: list[str] = []
    for p in sorted(root.glob("*.json")):
        if p.is_file():
            names.append(p.stem)
    return names


def load_filter_preset(logs_dir: Path, name: str) -> dict[str, Any]:
    path = preset_path(logs_dir, name)
    if not path.is_file():
        raise FilterPresetError(f"filter preset not found: {name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise FilterPresetError(f"failed to read preset {name}: {e}") from e
    if not isinstance(data, dict):
        raise FilterPresetError(f"preset {name} must be a JSON object")
    out: dict[str, Any] = {"name": validate_preset_name(name)}
    for key in PRESET_KEYS:
        if key in data:
            out[key] = data[key]
    return out


def save_filter_preset(logs_dir: Path, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    safe = validate_preset_name(name)
    if not isinstance(payload, dict):
        raise FilterPresetError("preset payload must be a dict")
    out: dict[str, Any] = {"name": safe}
    for key in PRESET_KEYS:
        if key in payload:
            out[key] = payload[key]
    root = presets_dir(logs_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{safe}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return out


def apply_preset_defaults(
    preset: dict[str, Any] | None,
    *,
    base: dict[str, Any],
) -> dict[str, Any]:
    """Merge preset under base (base wins for keys already present and non-empty)."""
    if not preset:
        return dict(base)
    merged = dict(base)
    for key in PRESET_KEYS:
        if key not in preset:
            continue
        cur = merged.get(key)
        empty = cur is None or cur == "" or cur == [] or cur == "All"
        if empty or key not in merged:
            merged[key] = preset[key]
    return merged
