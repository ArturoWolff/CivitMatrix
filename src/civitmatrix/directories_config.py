"""Persist per-type output directories + UI globals."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_TYPE_DIRS = {
    "LORA": "Lora",
    "LoCon": "Lora",
    "DoRA": "Lora",
    "Checkpoint": "StableDiffusion",
    "TextualInversion": "Embeddings",
    "VAE": "VAE",
    "Workflows": "Workflows",
    "Controlnet": "ControlNet",
    "Upscaler": "ESRGAN",
    "Hypernetwork": "Hypernetworks",
    "AestheticGradient": "AestheticGradients",
    "MotionModule": "Motion",
    "Poses": "Poses",
    "Wildcards": "Wildcards",
    "Detection": "Detection",
    "TextEncoder": "TextEncoders",
    "UNet": "UNet",
    "LLM": "VLM",
    "Other": "Other",
}


def default_models_root(models_root: Path | None = None) -> Path:
    """Portable default: MODELS_ROOT, else cwd/downloads (or cwd if already Models)."""
    if models_root is not None:
        return Path(models_root).expanduser().resolve()
    env = os.environ.get("MODELS_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if cwd.name.lower() == "models":
        return cwd
    parent = cwd.parent
    if parent.name.lower() == "models":
        return parent
    return (cwd / "downloads").resolve()


def default_directories(models_root: Path | None = None) -> dict[str, Any]:
    root = default_models_root(models_root)
    paths = {k: str(root / v) for k, v in DEFAULT_TYPE_DIRS.items()}
    # Alias common UI labels
    paths["Embedding"] = paths["TextualInversion"]
    return {
        "modelsRoot": str(root),
        "paths": paths,
        "baseUrl": os.environ.get("CIVITAI_BASE_URL", "https://civitai.red"),
        "diskFloorGib": float(os.environ.get("DISK_FLOOR_GIB", "2") or 2),
        "apiKeySet": bool(os.environ.get("CIVITAI_API_KEY", "").strip()),
    }


def load_directories(path: Path, *, models_root: Path | None = None) -> dict[str, Any]:
    base = default_directories(models_root)
    if not path.exists():
        return base
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    if not isinstance(data, dict):
        return base
    paths = dict(base["paths"])
    incoming = data.get("paths") or {}
    if isinstance(incoming, dict):
        for k, v in incoming.items():
            if v:
                paths[str(k)] = str(v)
    # Never echo secrets from disk JSON (apiKey / tokens / etc.)
    secret_keys = {
        "apiKey",
        "api_key",
        "CIVITAI_API_KEY",
        "token",
        "secret",
        "password",
    }
    safe_extra = {
        k: v
        for k, v in data.items()
        if k != "paths" and k not in secret_keys and not str(k).lower().endswith("key")
    }
    out = {
        **base,
        **safe_extra,
        "paths": paths,
        "apiKeySet": bool(os.environ.get("CIVITAI_API_KEY", "").strip()),
    }
    if not out.get("modelsRoot"):
        out["modelsRoot"] = base["modelsRoot"]
    # Force boolean only — never return raw key material
    out.pop("apiKey", None)
    return out


def save_directories(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_directories(path) if path.exists() else default_directories()
    clean = {
        "modelsRoot": data.get("modelsRoot") or existing.get("modelsRoot"),
        "paths": data.get("paths") or {},
        "baseUrl": data.get("baseUrl"),
        "diskFloorGib": data.get("diskFloorGib", 2),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return load_directories(path)


def path_for_type(config: dict[str, Any], model_type: str) -> Path:
    paths = config.get("paths") or {}
    key = (model_type or "").strip()
    if not key or key.lower() in {"all", "*", "any"}:
        root = config.get("modelsRoot") or default_directories().get("modelsRoot")
        return Path(str(root))
    if key not in paths and key == "TextualInversion":
        key = "Embedding"
    if key not in paths and model_type in {"LoCon", "DoRA"}:
        key = "LORA"
    if key not in paths and model_type == "LLM":
        key = "LLM"
    raw = paths.get(key) or paths.get(model_type) or paths.get("LORA")
    if not raw:
        return Path(default_directories()["paths"]["LORA"])
    return Path(str(raw))
