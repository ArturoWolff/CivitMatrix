from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterator

import requests

from civitmatrix import __version__
from civitmatrix.download_progress import DownloadProgress

DownloadEventFn = Callable[[str, dict[str, Any]], None]


class CivitClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "User-Agent": f"civitmatrix/{__version__}",
            }
        )
        self.timeout = timeout

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def iter_models(
        self,
        *,
        base_model: str,
        model_type: str,
        nsfw: bool = True,
        sort: str = "Highest Rated",
        page_limit: int = 100,
        on_page: Callable[..., None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/models"
        params: dict[str, Any] = {
            "baseModels": base_model,
            "types": model_type,
            "nsfw": "true" if nsfw else "false",
            "limit": page_limit,
            "sort": sort,
        }
        page_num = 0
        while True:
            data = self.get_json(url, params=params)
            items = list(data.get("items") or [])
            meta = data.get("metadata") or {}
            next_page = meta.get("nextPage")
            page_num += 1
            if on_page is not None:
                on_page(page=page_num, next_page=next_page, items=items)
            for item in items:
                yield item
            if not next_page:
                break
            url = next_page
            params = None
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
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")

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

                with self.session.get(
                    url,
                    stream=True,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers=headers,
                ) as r:
                    if r.status_code in (401, 403):
                        raise PermissionError(f"HTTP {r.status_code} downloading {url}")
                    if r.status_code == 404:
                        raise FileNotFoundError(f"HTTP 404 downloading {url}")

                    if existing > 0 and r.status_code == 416:
                        # Unsatisfiable — partial is stale/corrupt
                        tmp.unlink(missing_ok=True)
                        emit("download_restart", path=str(tmp), reason="http_416")
                        existing = 0
                        mode = "wb"
                        headers = {}
                        continue

                    if existing > 0 and r.status_code == 200:
                        # Server ignored Range — rewrite from scratch
                        tmp.unlink(missing_ok=True)
                        mode = "wb"
                        emit("download_restart", path=str(tmp), reason="server_ignored_range")

                    if r.status_code not in (200, 206):
                        r.raise_for_status()

                    # Resolve total size for progress (Content-Length / Range)
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
                    cr = r.headers.get("Content-Range")  # bytes start-end/total
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
            except Exception:
                # Keep partial for Range resume on next attempt / run
                if attempt == max_retries:
                    raise
                time.sleep(min(2**attempt, 30))


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0
