"""Outbound URL policy: keep API pagination on the configured origin."""

from __future__ import annotations

from urllib.parse import urlparse


class OriginMismatch(ValueError):
    """Raised when a URL is not the same origin as the configured API base."""


def origin_tuple(url: str) -> tuple[str, str]:
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    if not netloc:
        raise OriginMismatch(f"URL missing host: {url!r}")
    return scheme, netloc


def assert_same_origin(url: str, base_url: str) -> None:
    """Require ``url`` to share scheme+host with ``base_url``."""
    try:
        got = origin_tuple(url)
        want = origin_tuple(base_url)
    except OriginMismatch:
        raise
    except Exception as e:  # noqa: BLE001
        raise OriginMismatch(f"invalid URL: {url!r}") from e
    if got != want:
        raise OriginMismatch(
            f"refusing off-origin URL {url!r} (expected origin {want[0]}://{want[1]})"
        )
