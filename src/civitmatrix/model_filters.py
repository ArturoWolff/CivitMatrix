"""Client-side model filter helpers (tags, category, users, format, dates)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable


def is_all_filter(value: Any) -> bool:
    """True when the filter means 'no constraint' (show everything)."""
    if value is None:
        return True
    s = str(value).strip().lower()
    return s in {"", "all", "*", "any"}


def model_tag_names(model: dict[str, Any]) -> list[str]:
    tags = model.get("tags") or []
    out: list[str] = []
    for t in tags:
        if isinstance(t, str):
            name = t
        else:
            name = (t or {}).get("name") if isinstance(t, dict) else None
        if name:
            out.append(str(name))
    return out


def model_tag_set_lower(model: dict[str, Any]) -> set[str]:
    return {t.lower() for t in model_tag_names(model)}


def parse_csv_list(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [p.strip() for p in str(raw).split(",") if p.strip()]


def matches_tag_filters(
    model: dict[str, Any],
    *,
    tag_include: Iterable[str] | None = None,
    tag_exclude: Iterable[str] | None = None,
) -> bool:
    """
    empty include → no include constraint
    empty exclude → no exclude constraint
    include set → model must have at least one include tag (case-insensitive)
    exclude set → model must have none of the exclude tags
    """
    include = [t.lower() for t in (tag_include or []) if t]
    exclude = [t.lower() for t in (tag_exclude or []) if t]
    tags = model_tag_set_lower(model)
    if include and not any(t in tags for t in include):
        return False
    if exclude and any(t in tags for t in exclude):
        return False
    return True


def matches_category(model: dict[str, Any], category: str | None) -> bool:
    """Category is a dedicated dim (not tags). Match against model.category or tags."""
    if is_all_filter(category):
        return True
    want = str(category).strip().lower()
    cat = model.get("category") or model.get("modelCategory")
    if cat and str(cat).strip().lower() == want:
        return True
    # CivitAI often encodes category-like labels as tags
    return want in model_tag_set_lower(model)


def matches_users(model: dict[str, Any], users: Iterable[str] | None) -> bool:
    """Empty users → any creator. Non-empty → creator username in list (case-insensitive)."""
    allow = [str(u).lstrip("@").lower() for u in (users or []) if u]
    if not allow:
        return True
    creator = (model.get("creator") or {}).get("username") or ""
    return str(creator).lstrip("@").lower() in allow


_FORMAT_ALIASES: dict[str, set[str]] = {
    "safetensor": {"safetensor", "safetensors"},
    "pickletensor": {"pickletensor", "pickle", "pickle tensor"},
    "pt": {"pt", "pytorch", "torch"},
    "gguf": {"gguf"},
    "onnx": {"onnx"},
    "core ml": {"core ml", "coreml", "mlmodel", "mlpackage"},
    "coreml": {"core ml", "coreml", "mlmodel", "mlpackage"},
    "diffusers": {"diffusers"},
    "other": {"other"},
}

_FORMAT_EXTS: dict[str, tuple[str, ...]] = {
    "safetensor": (".safetensors",),
    "pickletensor": (".pt", ".bin", ".pkl", ".pickle"),
    "pt": (".pt",),
    "gguf": (".gguf",),
    "onnx": (".onnx",),
    "core ml": (".mlmodel", ".mlpackage"),
    "coreml": (".mlmodel", ".mlpackage"),
    "diffusers": (),
    "other": (),
}


def _normalize_format_key(fmt: str) -> str:
    s = str(fmt).strip().lower().replace("_", " ")
    s = " ".join(s.split())
    if s in {"safe tensor", "safe tensors", "safetensors"}:
        return "safetensor"
    if s in {"pickle tensor", "pickle tensors", "pickletensors"}:
        return "pickletensor"
    return s


def matches_format(model: dict[str, Any], fmt: str | None) -> bool:
    """Format dim: SafeTensor / PickleTensor / GGUF / … Empty/All → pass."""
    if is_all_filter(fmt):
        return True
    want = _normalize_format_key(str(fmt))
    aliases = _FORMAT_ALIASES.get(want, {want})
    exts = _FORMAT_EXTS.get(want, ())
    for ver in model.get("modelVersions") or []:
        for f in ver.get("files") or []:
            meta = f.get("metadata") or {}
            fmt_val = _normalize_format_key(meta.get("format") or f.get("format") or "")
            if fmt_val in aliases or any(a in fmt_val for a in aliases if a):
                return True
            name = str(f.get("name") or "").lower()
            if exts and any(name.endswith(ext) for ext in exts):
                return True
            if want == "diffusers" and ("diffusers" in name or "/unet/" in name):
                return True
    return False


def _parse_iso_date(raw: Any) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    except ValueError:
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None


def model_last_updated_date(model: dict[str, Any]) -> date | None:
    """Best-effort last-updated day from model / newest version timestamps."""
    candidates: list[date] = []
    for key in ("lastVersionAt", "updatedAt", "publishedAt", "createdAt"):
        d = _parse_iso_date(model.get(key))
        if d:
            candidates.append(d)
    for ver in model.get("modelVersions") or []:
        for key in ("publishedAt", "updatedAt", "createdAt"):
            d = _parse_iso_date(ver.get(key))
            if d:
                candidates.append(d)
    return max(candidates) if candidates else None


def matches_updated_range(
    model: dict[str, Any],
    *,
    updated_from: str | date | None = None,
    updated_to: str | date | None = None,
) -> bool:
    """Inclusive From/To on last-updated day. Empty bounds → no constraint."""
    start = _parse_iso_date(updated_from)
    end = _parse_iso_date(updated_to)
    if start is None and end is None:
        return True
    got = model_last_updated_date(model)
    if got is None:
        return False
    if start is not None and got < start:
        return False
    if end is not None and got > end:
        return False
    return True


def model_passes_filters(
    model: dict[str, Any],
    *,
    tag_include: Iterable[str] | None = None,
    tag_exclude: Iterable[str] | None = None,
    category: str | None = None,
    users: Iterable[str] | None = None,
    file_format: str | None = None,
    updated_from: str | date | None = None,
    updated_to: str | date | None = None,
) -> bool:
    return (
        matches_tag_filters(model, tag_include=tag_include, tag_exclude=tag_exclude)
        and matches_category(model, category)
        and matches_users(model, users)
        and matches_format(model, file_format)
        and matches_updated_range(model, updated_from=updated_from, updated_to=updated_to)
    )


def summarize_model_for_ui(model: dict[str, Any], *, base_model: str | None = None) -> dict[str, Any]:
    """Compact row for Populate table."""
    versions_out: list[dict[str, Any]] = []
    filter_base = base_model if not is_all_filter(base_model) else None
    for ver in model.get("modelVersions") or []:
        if filter_base:
            if (ver.get("baseModel") or "") != filter_base:
                continue
        files = ver.get("files") or []
        primary = next((f for f in files if f.get("primary")), None) or (
            files[0] if files else {}
        )
        size_kb = primary.get("sizeKB")
        versions_out.append(
            {
                "id": ver.get("id"),
                "name": ver.get("name"),
                "baseModel": ver.get("baseModel"),
                "sizeKB": size_kb,
            }
        )
    if not versions_out and (model.get("modelVersions") or []):
        # fallback: all versions if none matched base
        for ver in model.get("modelVersions") or []:
            files = ver.get("files") or []
            primary = next((f for f in files if f.get("primary")), None) or (
                files[0] if files else {}
            )
            versions_out.append(
                {
                    "id": ver.get("id"),
                    "name": ver.get("name"),
                    "baseModel": ver.get("baseModel"),
                    "sizeKB": primary.get("sizeKB"),
                }
            )
    creator = (model.get("creator") or {}).get("username")
    return {
        "id": model.get("id"),
        "name": model.get("name"),
        "creator": creator,
        "tags": model_tag_names(model)[:20],
        "nsfw": bool(model.get("nsfw")),
        "versions": versions_out,
    }
