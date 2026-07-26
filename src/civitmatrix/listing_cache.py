"""Opt-in CivitAI listing page cache (meta sidecar + JSONL pages)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_cache_key(
    *,
    base_url: str,
    base_model: str,
    model_type: str,
    sort: str,
    nsfw: bool,
) -> str:
    payload = {
        "baseUrl": base_url.rstrip("/"),
        "baseModel": base_model,
        "modelType": model_type,
        "sort": sort,
        "nsfw": bool(nsfw),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def cache_paths(logs_dir: Path, key: str) -> tuple[Path, Path]:
    root = logs_dir / "listing-cache"
    return root / f"{key}.meta.json", root / f"{key}.jsonl"


def probe_cache(
    logs_dir: Path,
    *,
    base_url: str,
    base_model: str,
    model_type: str,
    sort: str,
    nsfw: bool,
) -> tuple[str, dict[str, Any] | None]:
    key = make_cache_key(
        base_url=base_url,
        base_model=base_model,
        model_type=model_type,
        sort=sort,
        nsfw=nsfw,
    )
    meta_path, jsonl_path = cache_paths(logs_dir, key)
    if not meta_path.is_file() or not jsonl_path.is_file():
        return "missing", None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return "corrupt", None
        expected = {
            "baseUrl": base_url.rstrip("/"),
            "baseModel": base_model,
            "modelType": model_type,
            "sort": sort,
            "nsfw": bool(nsfw),
        }
        got = meta.get("key") or {}
        if got != expected:
            return "mismatch", None
        if not meta.get("complete"):
            return "incomplete", None
        _validate_jsonl_readable(jsonl_path)
        return "ok", meta
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "corrupt", None


def _validate_jsonl_readable(jsonl_path: Path) -> None:
    """Raise if jsonl cannot be fully parsed as page rows (corrupt / unreadable)."""
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("jsonl row must be a JSON object")
            items = row.get("items")
            if items is not None and not isinstance(items, list):
                raise ValueError("jsonl items must be a list")


def iter_cached_models(jsonl_path: Path) -> Iterator[dict[str, Any]]:
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            for item in row.get("items") or []:
                if isinstance(item, dict):
                    yield item


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


class ListingCacheWriter:
    def __init__(
        self,
        logs_dir: Path,
        *,
        key: str,
        key_fields: dict[str, Any],
    ) -> None:
        self.key = key
        self.key_fields = dict(key_fields)
        self.meta_path, self.jsonl_path = cache_paths(logs_dir, key)
        self.pages = 0
        self.items = 0
        self._fh: Any = None

    def begin(self) -> None:
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        self.jsonl_path.write_text("", encoding="utf-8")
        self.pages = 0
        self.items = 0
        self._write_meta(complete=False)
        self._fh = self.jsonl_path.open("a", encoding="utf-8")

    def append_page(
        self,
        *,
        page: int,
        next_page: str | None,
        items: list[Any],
    ) -> None:
        if self._fh is None:
            raise RuntimeError("ListingCacheWriter.begin() not called")
        row = {"page": page, "nextPage": next_page, "items": items}
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.pages += 1
        self.items += len(items)
        self._write_meta(complete=False)

    def finalize(self, *, complete: bool) -> dict[str, Any]:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return self._write_meta(complete=complete)

    def _write_meta(self, *, complete: bool) -> dict[str, Any]:
        meta = {
            "v": 1,
            "key": self.key_fields,
            "builtAt": _utc_now(),
            "complete": bool(complete),
            "pages": self.pages,
            "items": self.items,
        }
        _atomic_write_json(self.meta_path, meta)
        return meta
