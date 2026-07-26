from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import requests

from civitmatrix import __version__


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
    ) -> Iterator[dict[str, Any]]:
        url = f"{self.base_url}/api/v1/models"
        params: dict[str, Any] = {
            "baseModels": base_model,
            "types": model_type,
            "nsfw": "true" if nsfw else "false",
            "limit": page_limit,
            "sort": sort,
        }
        while True:
            data = self.get_json(url, params=params)
            for item in data.get("items") or []:
                yield item
            meta = data.get("metadata") or {}
            next_page = meta.get("nextPage")
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

    def download(self, url: str, dest: Path, max_retries: int = 5) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".partial")
        for attempt in range(1, max_retries + 1):
            try:
                with self.session.get(
                    url, stream=True, timeout=self.timeout, allow_redirects=True
                ) as r:
                    if r.status_code in (401, 403):
                        raise PermissionError(f"HTTP {r.status_code} downloading {url}")
                    if r.status_code == 404:
                        raise FileNotFoundError(f"HTTP 404 downloading {url}")
                    r.raise_for_status()
                    with tmp.open("wb") as f:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                tmp.replace(dest)
                return
            except (PermissionError, FileNotFoundError):
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                raise
            except Exception:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                if attempt == max_retries:
                    raise
                time.sleep(min(2**attempt, 30))
