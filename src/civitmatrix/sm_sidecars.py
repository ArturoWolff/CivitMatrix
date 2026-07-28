from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from civitmatrix.logging_io import utc_now
from civitmatrix.preview_media import find_preview_path

# Strip embedded data:image / base64 blobs from HTML descriptions (never write into sidecars).
_DATA_IMG_TAG_RE = re.compile(
    r"""<img\b[^>]*\bsrc\s*=\s*["']?data:image[^"'>\s]*["']?[^>]*/?\s*>""",
    re.IGNORECASE,
)
_DATA_IMAGE_URI_RE = re.compile(r"data:image[^\s\"'>]+", re.IGNORECASE)


def strip_data_images_from_html(text: str) -> str:
    """Remove data:image URIs and <img> tags that use them; keep normal HTML text/links."""
    if not text:
        return text
    out = _DATA_IMG_TAG_RE.sub("", text)
    return _DATA_IMAGE_URI_RE.sub("", out)


def civit_model_source_url(
    base_url: str, model_id: Any, version_id: Any
) -> str | None:
    if model_id is None or version_id is None:
        return None
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/models/{model_id}?modelVersionId={version_id}"


def build_cm_info(
    model: dict[str, Any],
    version: dict[str, Any],
    file_info: dict[str, Any],
    local_stem: str,
    out_dir: Path,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    """Build a Stability Matrix–compatible .cm-info.json payload."""
    creator = model.get("creator") or {}
    stats = model.get("stats") or {}
    hashes = file_info.get("hashes") or {}
    meta = file_info.get("metadata") or {}
    tags = model.get("tags") or []
    tag_names = [t if isinstance(t, str) else t.get("name") for t in tags]
    tag_names = [t for t in tag_names if t]

    preview = find_preview_path(out_dir, local_stem)
    source_url = civit_model_source_url(base_url, model.get("id"), version.get("id"))
    return {
        "ModelId": model.get("id"),
        "ModelName": model.get("name"),
        "ModelDescription": model.get("description"),
        "Nsfw": bool(model.get("nsfw")),
        "Tags": tag_names,
        "ModelType": model.get("type") or "LORA",
        "VersionId": version.get("id"),
        "VersionName": version.get("name"),
        "VersionDescription": version.get("description"),
        "AuthorUsername": creator.get("username"),
        "BaseModel": version.get("baseModel"),
        "RemoteFileName": file_info.get("name"),
        "RemoteFileId": file_info.get("id"),
        "FileMetadata": {
            "fp": meta.get("fp"),
            "size": meta.get("size"),
            "format": meta.get("format") or "SafeTensor",
        },
        "ImportedAt": utc_now(),
        "Hashes": {
            "SHA256": hashes.get("SHA256"),
            "CRC32": hashes.get("CRC32"),
            "BLAKE3": hashes.get("BLAKE3"),
            "AutoV2": hashes.get("AutoV2"),
        },
        "TrainedWords": version.get("trainedWords") or [],
        "Stats": {
            "favoriteCount": stats.get("favoriteCount", 0),
            "commentCount": stats.get("commentCount", 0),
            "thumbsUpCount": stats.get("thumbsUpCount", 0),
            "downloadCount": stats.get("downloadCount", 0),
            "ratingCount": stats.get("ratingCount", 0),
            "rating": stats.get("rating", 0),
        },
        "UserTitle": None,
        "ThumbnailImageUrl": str(preview) if preview is not None else None,
        "InferenceDefaults": None,
        "Source": 0,
        "SourceUrl": source_url,
    }


def build_swarm_json(
    model: dict[str, Any],
    version: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any] | None:
    """Build SwarmUI ModelSpec sidecar. Returns None if URL cannot be built."""
    url = civit_model_source_url(base_url, model.get("id"), version.get("id"))
    if url is None:
        return None

    model_name = (model.get("name") or "").strip()
    version_name = (version.get("name") or "").strip()
    title = f"{model_name} - {version_name}" if version_name else model_name

    desc_parts: list[str] = [
        f'From <a href="{url}" target="_blank">{url}</a>\n',
    ]
    v_desc = strip_data_images_from_html(version.get("description") or "")
    m_desc = strip_data_images_from_html(model.get("description") or "")
    # Prefer version description; append model description when both exist and differ
    if v_desc:
        desc_parts.append(v_desc if v_desc.endswith("\n") else v_desc + "\n")
    if m_desc and m_desc != v_desc:
        desc_parts.append(m_desc if m_desc.endswith("\n") else m_desc + "\n")

    creator = model.get("creator") or {}
    trained = version.get("trainedWords") or []
    trigger = ", ".join(str(t) for t in trained if t)

    tags = model.get("tags") or []
    tag_names = [t if isinstance(t, str) else t.get("name") for t in tags]
    tag_names = [t for t in tag_names if t]
    tags_joined = ", ".join(str(t) for t in tag_names)

    date = version.get("publishedAt") or utc_now()

    return {
        "modelspec.title": title,
        "modelspec.description": "".join(desc_parts),
        "modelspec.date": date,
        "modelspec.author": creator.get("username") or "",
        "modelspec.trigger_phrase": trigger,
        "modelspec.tags": tags_joined,
    }


CATEGORY_TAGS = {
    "character",
    "characters",
    "style",
    "styles",
    "concept",
    "concepts",
    "clothing",
    "clothes",
    "costume",
}


def sort_hints_from_tags(tags: list[Any]) -> dict[str, Any]:
    names = [t if isinstance(t, str) else t.get("name") for t in tags]
    names = [t for t in names if t]
    return {
        "tagSet": names,
        "suggestedBuckets": [
            t for t in names if isinstance(t, str) and t.lower() in CATEGORY_TAGS
        ],
    }
