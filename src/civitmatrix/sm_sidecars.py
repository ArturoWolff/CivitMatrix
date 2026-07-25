from __future__ import annotations

from pathlib import Path
from typing import Any

from civitmatrix.logging_io import utc_now
from civitmatrix.preview_media import find_preview_path


def build_cm_info(
    model: dict[str, Any],
    version: dict[str, Any],
    file_info: dict[str, Any],
    local_stem: str,
    out_dir: Path,
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
        "SourceUrl": None,
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
