"""Client-side model filter helpers (tags, category, users)."""

from __future__ import annotations

from typing import Any, Iterable


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
    if not category or not str(category).strip() or str(category).strip().lower() in {
        "any",
        "*",
        "all",
    }:
        return True
    want = str(category).strip().lower()
    cat = model.get("category") or model.get("modelCategory")
    if cat and str(cat).strip().lower() == want:
        return True
    # CivitAI often encodes category-like labels as tags
    return want in model_tag_set_lower(model)


def matches_users(model: dict[str, Any], users: Iterable[str] | None) -> bool:
    """Empty users → any creator. Non-empty → creator username in list (case-insensitive)."""
    allow = [u.lower() for u in (users or []) if u]
    if not allow:
        return True
    creator = (model.get("creator") or {}).get("username") or ""
    return str(creator).lower() in allow


def matches_format(model: dict[str, Any], fmt: str | None) -> bool:
    """Format dim: SafeTensor / PickleTensor / etc. Empty/any → pass."""
    if not fmt or str(fmt).strip().lower() in {"any", "*", "all", ""}:
        return True
    want = str(fmt).strip().lower()
    for ver in model.get("modelVersions") or []:
        for f in ver.get("files") or []:
            meta = f.get("metadata") or {}
            fmt_val = (meta.get("format") or f.get("format") or "").lower()
            if want in fmt_val or fmt_val == want:
                return True
            name = str(f.get("name") or "").lower()
            if want == "safetensor" and name.endswith(".safetensors"):
                return True
            if want in {"pickletensor", "pickle"} and name.endswith(".pt"):
                return True
    return False


def model_passes_filters(
    model: dict[str, Any],
    *,
    tag_include: Iterable[str] | None = None,
    tag_exclude: Iterable[str] | None = None,
    category: str | None = None,
    users: Iterable[str] | None = None,
    file_format: str | None = None,
) -> bool:
    return (
        matches_tag_filters(model, tag_include=tag_include, tag_exclude=tag_exclude)
        and matches_category(model, category)
        and matches_users(model, users)
        and matches_format(model, file_format)
    )


def summarize_model_for_ui(model: dict[str, Any], *, base_model: str | None = None) -> dict[str, Any]:
    """Compact row for Populate table."""
    versions_out: list[dict[str, Any]] = []
    for ver in model.get("modelVersions") or []:
        if base_model:
            if (ver.get("baseModel") or "") != base_model:
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
