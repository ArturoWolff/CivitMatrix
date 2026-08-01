from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from civitmatrix import __version__
from civitmatrix.download_progress import DownloadProgress
from civitmatrix.http_policy import OriginMismatch, assert_same_origin
from civitmatrix.rate_limit import BandwidthLimiter
from civitmatrix.redact import redact_secrets

DownloadEventFn = Callable[[str, dict[str, Any]], None]


class CivitClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: int = 120,
        *,
        rate_limit_bytes_per_sec: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Shared across concurrent download workers on this client instance.
        self.rate_limiter = BandwidthLimiter(rate_limit_bytes_per_sec)
        ua = f"civitmatrix/{__version__}"
        # API session carries Bearer for list/metadata only.
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": ua,
            }
        )
        # Download session never sends Authorization (CDN / third-party hosts).
        # Same-origin download URLs get ``?token=`` instead (see ``_download_url``).
        self.download_session = requests.Session()
        self.download_session.headers.update({"User-Agent": ua})

    def _download_url(self, url: str) -> str:
        """Attach API token for same-origin download endpoints only (not CDN)."""
        from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

        try:
            assert_same_origin(url, self.base_url)
        except OriginMismatch:
            return url
        if not self.api_key:
            return url
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if "token" in qs:
            return url
        qs["token"] = [self.api_key]
        return urlunparse(parsed._replace(query=urlencode(qs, doseq=True)))

    def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        max_retries: int = 5,
    ) -> dict[str, Any]:
        """GET JSON with retries on HTTP 429 (honors Retry-After when present)."""
        last: Any = None
        for attempt in range(max_retries + 1):
            r = self.session.get(url, params=params, timeout=self.timeout)
            last = r
            if r.status_code == 429 and attempt < max_retries:
                delay = _retry_after_seconds(r, attempt)
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()
        assert last is not None
        last.raise_for_status()
        return last.json()

    def iter_models(
        self,
        *,
        base_model: str | None = None,
        model_type: str | None = None,
        nsfw: bool = True,
        sort: str = "Highest Rated",
        page_limit: int = 100,
        username: str | None = None,
        tag: str | None = None,
        checkpoint_type: str | None = None,
        on_page: Callable[..., None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        from civitmatrix.model_filters import is_all_filter

        models_url = f"{self.base_url}/api/v1/models"
        url = models_url
        params: dict[str, Any] = {
            "nsfw": "true" if nsfw else "false",
            "limit": page_limit,
            "sort": sort,
        }
        if not is_all_filter(base_model):
            params["baseModels"] = str(base_model).strip()
        if not is_all_filter(model_type):
            params["types"] = str(model_type).strip()
        if not is_all_filter(checkpoint_type):
            params["checkpointType"] = str(checkpoint_type).strip()
        if username:
            params["username"] = username
        if tag:
            params["tag"] = tag
        base_params = dict(params)
        page_num = 0
        while True:
            data = self.get_json(url, params=params)
            items = list(data.get("items") or [])
            meta = data.get("metadata") or {}
            next_page = meta.get("nextPage")
            next_cursor = meta.get("nextCursor")
            page_num += 1
            if on_page is not None:
                on_page(page=page_num, next_page=next_page, items=items)
            for item in items:
                yield item
            if next_page:
                assert_same_origin(str(next_page), self.base_url)
                url = str(next_page)
                params = None
            elif next_cursor:
                url = models_url
                params = {**base_params, "cursor": str(next_cursor)}
            else:
                break
            time.sleep(0.35)

    def get_model(self, model_id: int) -> dict[str, Any]:
        return self.get_json(f"{self.base_url}/api/v1/models/{model_id}")

    def get_version_by_hash(self, file_hash: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/api/v1/model-versions/by-hash/{file_hash}"
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else None

    def download(
        self,
        url: str,
        dest: Path,
        max_retries: int = 5,
        *,
        resume: bool = True,
        on_event: DownloadEventFn | None = None,
        cli_progress: bool = True,
    ) -> None:
        """
        Download to dest via ``dest.partial``.
        When resume=True and a partial exists, send HTTP Range and append on 206.
        Network errors keep the partial for a later retry; auth/404 clear it.
        Uses a session without Authorization so redirects cannot leak the API key.
        Same-origin ``/api/download/...`` URLs receive ``?token=`` instead.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")
        url = self._download_url(url)

        def emit(event: str, **fields: Any) -> None:
            if on_event:
                on_event(event, fields)

        if not resume and tmp.exists():
            tmp.unlink(missing_ok=True)
            emit("download_restart", path=str(tmp), reason="no_resume")

        for attempt in range(1, max_retries + 1):
            try:
                existing = _file_size(tmp) if resume else 0
                headers: dict[str, str] = {}
                mode = "wb"
                if existing > 0:
                    headers["Range"] = f"bytes={existing}-"
                    mode = "ab"
                    emit("download_resume", path=str(tmp), offset=existing, attempt=attempt)

                with self.download_session.get(
                    url,
                    stream=True,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers=headers,
                ) as r:
                    if r.status_code in (401, 403):
                        raise PermissionError(
                            f"HTTP {r.status_code} downloading {redact_secrets(url)}"
                        )
                    if r.status_code == 404:
                        raise FileNotFoundError(
                            f"HTTP 404 downloading {redact_secrets(url)}"
                        )

                    if existing > 0 and r.status_code == 416:
                        tmp.unlink(missing_ok=True)
                        emit("download_restart", path=str(tmp), reason="http_416")
                        existing = 0
                        mode = "wb"
                        headers = {}
                        continue

                    if existing > 0 and r.status_code == 200:
                        tmp.unlink(missing_ok=True)
                        mode = "wb"
                        emit("download_restart", path=str(tmp), reason="server_ignored_range")

                    if r.status_code not in (200, 206):
                        r.raise_for_status()

                    start_offset = existing if mode == "ab" else 0
                    content_len = r.headers.get("Content-Length")
                    total: int | None = None
                    if content_len:
                        try:
                            cl = int(content_len)
                            if r.status_code == 206 or start_offset > 0:
                                total = start_offset + cl
                            else:
                                total = cl
                        except ValueError:
                            total = None
                    cr = r.headers.get("Content-Range")
                    if cr and "/" in cr:
                        try:
                            overall = cr.rsplit("/", 1)[-1]
                            if overall != "*":
                                total = int(overall)
                        except ValueError:
                            pass

                    prog = DownloadProgress(
                        path=str(tmp),
                        label=dest.name,
                        emit=lambda ev, fields: emit(ev, **fields),
                        cli=cli_progress,
                    )
                    if start_offset > 0:
                        prog.seed_bytes(start_offset)
                    prog.set_total(total)

                    with tmp.open(mode) as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                self.rate_limiter.acquire(len(chunk))
                                f.write(chunk)
                                prog.add(len(chunk))
                    prog.finish()

                tmp.replace(dest)
                emit(
                    "download_complete",
                    path=str(dest),
                    resumed=existing > 0 and mode == "ab",
                    bytes=_file_size(dest),
                )
                return
            except (PermissionError, FileNotFoundError):
                tmp.unlink(missing_ok=True)
                raise
            except OriginMismatch:
                raise
            except Exception:
                if attempt == max_retries:
                    raise
                time.sleep(min(2**attempt, 30))


def _retry_after_seconds(response: Any, attempt: int) -> float:
    """Parse Retry-After (seconds) or fall back to exponential backoff."""
    raw = response.headers.get("Retry-After") if response is not None else None
    if raw is not None:
        try:
            return max(0.0, float(str(raw).strip()))
        except ValueError:
            pass
    return float(min(2**attempt, 60))


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0
