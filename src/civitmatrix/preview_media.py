"""Detect preview media type from magic bytes and pick a correct .preview.* name."""

from __future__ import annotations

from pathlib import Path


def sniff_preview_suffix(data: bytes) -> str:
    """Return filename suffix including leading dot, e.g. '.preview.mp4'."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        # ISO BMFF: MP4 / MOV / etc.
        return ".preview.mp4"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".preview.png"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return ".preview.webp"
    if data.startswith(b"\xff\xd8\xff"):
        return ".preview.jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".preview.gif"
    # WebM / Matroska
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return ".preview.webm"
    return ".preview.bin"


def pick_preview_url(images: list[dict]) -> str | None:
    """Prefer a still image URL; fall back to first available media URL."""
    if not images:
        return None
    for img in images:
        if (img.get("type") or "image").lower() == "image" and img.get("url"):
            return str(img["url"])
    for img in images:
        if img.get("url"):
            return str(img["url"])
    return None


def find_preview_path(out_dir: Path, stem: str) -> Path | None:
    matches = sorted(out_dir.glob(f"{stem}.preview.*"))
    # Ignore download temps
    matches = [
        p
        for p in matches
        if not p.name.endswith(".partial") and not p.name.endswith(".preview.download")
    ]
    return matches[0] if matches else None


def finalize_preview_file(tmp_path: Path, out_dir: Path, stem: str) -> Path:
    """
    Move a downloaded preview temp file to `{stem}.preview.<ext>` based on content.
    Removes any other `{stem}.preview.*` siblings so stale wrong extensions don't linger.
    """
    data = tmp_path.read_bytes()[:64]
    suffix = sniff_preview_suffix(data)
    final = out_dir / f"{stem}{suffix}"
    for old in out_dir.glob(f"{stem}.preview.*"):
        if old.resolve() == tmp_path.resolve():
            continue
        if old.name.endswith(".partial"):
            continue
        old.unlink(missing_ok=True)
    if final.exists() and final.resolve() != tmp_path.resolve():
        final.unlink()
    tmp_path.replace(final)
    return final
