# Opt-in Listing Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in listing cache so default runs stay fresh from the API, while `--use-listing-cache` can reuse a complete page-blob cache and `--refresh-listing` rebuilds it on demand.

**Architecture:** Pure `listing_cache.py` module owns keying, read path, and page-append writer (meta sidecar + JSONL pages). Listing iterators gain an `on_page` callback; the batch runner chooses cache-hit vs API+optional-write before feeding the existing streaming pool. Prove in `anima-lora-batch/` first, then promote into `civitmatrix/`.

**Tech Stack:** Python 3.10+, stdlib `json`/`hashlib`/`pathlib`, existing `requests` client, control plane via `JobState`.

**Spec:** `docs/superpowers/specs/2026-07-26-listing-cache-design.md`

## Global Constraints

- Default (no flags): never read or write `logs/listing-cache/`
- Cache key fields: `baseUrl`, `baseModel`, `modelType`, `sort`, `nsfw`
- Storage: `logs/listing-cache/<key>.meta.json` + `<key>.jsonl` (page blobs)
- Mark `complete: true` only when listing finishes naturally (no cancel, no `--limit` stop)
- `--retry-failed` never uses listing cache
- Local-first: implement and smoke in `/run/media/arturo/Datos2/Models/anima-lora-batch/` before editing `civitmatrix/` package code (except this plan/spec already in repo)
- `anima-lora-batch/` is not a git repo — no commits there; commits happen in `civitmatrix/`
- Do not commit `todo.md` (gitignored)

## File structure

| File | Responsibility |
|------|----------------|
| `anima-lora-batch/listing_cache.py` | Key, paths, probe, iterate models, writer |
| `anima-lora-batch/download_anima_loras.py` | Flags, wire iterator, events, finalize |
| `anima-lora-batch/test_listing_cache.py` | Unit tests for cache module (temp dirs) |
| `civitmatrix/src/civitmatrix/listing_cache.py` | Promoted copy of module |
| `civitmatrix/src/civitmatrix/client.py` | `on_page` on `iter_models` |
| `civitmatrix/src/civitmatrix/downloader.py` | Cache-aware `_iter_models_for_run` + finalize |
| `civitmatrix/src/civitmatrix/cli.py` | `--use-listing-cache`, `--refresh-listing` |
| `civitmatrix/tests/test_listing_cache.py` | Promoted unit tests |
| `civitmatrix/README.md`, `docs/GUIDE.md`, `ROADMAP.md` | User-facing docs |

---

### Task 1: `listing_cache` module + unit tests (anima)

**Files:**
- Create: `/run/media/arturo/Datos2/Models/anima-lora-batch/listing_cache.py`
- Create: `/run/media/arturo/Datos2/Models/anima-lora-batch/test_listing_cache.py`

**Interfaces:**
- Consumes: stdlib only
- Produces:
  - `make_cache_key(*, base_url: str, base_model: str, model_type: str, sort: str, nsfw: bool) -> str` — stable 16-char hex sha256 prefix of canonical JSON
  - `cache_paths(logs_dir: Path, key: str) -> tuple[Path, Path]` — `(meta_path, jsonl_path)` under `logs_dir / "listing-cache"`
  - `probe_cache(logs_dir, *, base_url, base_model, model_type, sort, nsfw) -> tuple[str, dict | None]` — returns `(reason, meta)` where reason is `"ok"` with meta, or `"missing"` / `"incomplete"` / `"mismatch"` / `"corrupt"` with `meta=None`
  - `iter_cached_models(jsonl_path: Path) -> Iterator[dict]` — yield each item from each page line
  - `class ListingCacheWriter` with `begin() -> None`, `append_page(*, page: int, next_page: str | None, items: list) -> None`, `finalize(*, complete: bool) -> dict` (returns final meta for events)

- [ ] **Step 1: Write failing unit tests**

Create `test_listing_cache.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from listing_cache import (
    ListingCacheWriter,
    cache_paths,
    iter_cached_models,
    make_cache_key,
    probe_cache,
)


class ListingCacheTests(unittest.TestCase):
    def test_key_stable_and_sensitive(self):
        a = make_cache_key(
            base_url="https://civitai.red",
            base_model="Anima",
            model_type="LORA",
            sort="Highest Rated",
            nsfw=True,
        )
        b = make_cache_key(
            base_url="https://civitai.red",
            base_model="Anima",
            model_type="LORA",
            sort="Highest Rated",
            nsfw=True,
        )
        c = make_cache_key(
            base_url="https://civitai.red",
            base_model="Anima",
            model_type="LORA",
            sort="Newest",
            nsfw=True,
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 16)

    def test_incomplete_not_ok(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            key = make_cache_key(
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            w = ListingCacheWriter(
                logs,
                key=key,
                key_fields={
                    "baseUrl": "https://x",
                    "baseModel": "Anima",
                    "modelType": "LORA",
                    "sort": "Highest Rated",
                    "nsfw": True,
                },
            )
            w.begin()
            w.append_page(page=1, next_page=None, items=[{"id": 1}])
            w.finalize(complete=False)
            reason, meta = probe_cache(
                logs,
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            self.assertEqual(reason, "incomplete")
            self.assertIsNone(meta)

    def test_complete_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            fields = {
                "baseUrl": "https://x",
                "baseModel": "Anima",
                "modelType": "LORA",
                "sort": "Highest Rated",
                "nsfw": True,
            }
            key = make_cache_key(**{
                "base_url": fields["baseUrl"],
                "base_model": fields["baseModel"],
                "model_type": fields["modelType"],
                "sort": fields["sort"],
                "nsfw": fields["nsfw"],
            })
            w = ListingCacheWriter(logs, key=key, key_fields=fields)
            w.begin()
            w.append_page(page=1, next_page="http://next", items=[{"id": 1}, {"id": 2}])
            w.append_page(page=2, next_page=None, items=[{"id": 3}])
            meta = w.finalize(complete=True)
            self.assertTrue(meta["complete"])
            self.assertEqual(meta["pages"], 2)
            self.assertEqual(meta["items"], 3)
            reason, probed = probe_cache(
                logs,
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            self.assertEqual(reason, "ok")
            self.assertEqual(probed["items"], 3)
            _, jsonl = cache_paths(logs, key)
            ids = [m["id"] for m in iter_cached_models(jsonl)]
            self.assertEqual(ids, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests — expect fail (module missing)**

Run: `cd /run/media/arturo/Datos2/Models/anima-lora-batch && python test_listing_cache.py -v`  
Expected: `ModuleNotFoundError: No module named 'listing_cache'`

- [ ] **Step 3: Implement `listing_cache.py`**

```python
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
        return "ok", meta
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "corrupt", None


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
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd /run/media/arturo/Datos2/Models/anima-lora-batch && python test_listing_cache.py -v`  
Expected: all tests `ok`

- [ ] **Step 5: No git commit** (anima not a repo). Proceed to Task 2.

---

### Task 2: `on_page` callback on anima `CivitClient.iter_anima_loras`

**Files:**
- Modify: `/run/media/arturo/Datos2/Models/anima-lora-batch/download_anima_loras.py` (`iter_anima_loras`)

**Interfaces:**
- Consumes: `ListingCacheWriter.append_page` signature from Task 1
- Produces: `iter_anima_loras(self, page_limit: int = 100, on_page: Callable | None = None)`

- [ ] **Step 1: Update `iter_anima_loras`**

Replace the method body so each successful page invokes `on_page` before yielding items:

```python
def iter_anima_loras(
    self,
    page_limit: int = 100,
    on_page: Any | None = None,
):
    url = f"{self.base_url}/api/v1/models"
    params: dict[str, Any] = {
        "baseModels": "Anima",
        "types": "LORA",
        "nsfw": "true",
        "limit": page_limit,
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
```

- [ ] **Step 2: Sanity import**

Run: `cd /run/media/arturo/Datos2/Models/anima-lora-batch && python -c "from download_anima_loras import CivitClient; import inspect; print('on_page' in inspect.signature(CivitClient.iter_anima_loras).parameters)"`  
Expected: `True`

- [ ] **Step 3: No git commit.** Proceed.

---

### Task 3: Wire CLI flags + cache-aware listing in anima runner

**Files:**
- Modify: `/run/media/arturo/Datos2/Models/anima-lora-batch/download_anima_loras.py`

**Interfaces:**
- Consumes: `listing_cache` API from Task 1; `iter_anima_loras(..., on_page=...)` from Task 2; existing `run_streaming_pool`
- Produces: CLI `--use-listing-cache`, `--refresh-listing`; job meta `listingCache`, `listingCacheHit`, `refreshListing`; events `listing_cache_hit` / `listing_cache_miss` / `listing_cache_write`

- [ ] **Step 1: Add argparse flags** (near other download flags)

```python
parser.add_argument(
    "--use-listing-cache",
    action="store_true",
    help="Opt in: reuse complete listing cache or build one while listing",
)
parser.add_argument(
    "--refresh-listing",
    action="store_true",
    help="Force fresh listing from API (with --use-listing-cache, rewrite cache)",
)
```

- [ ] **Step 2: Extend `job.set_meta` after streamMode**

Include:

```python
listingCache=bool(args.use_listing_cache),
refreshListing=bool(args.refresh_listing),
listingCacheHit=False,
```

- [ ] **Step 3: Replace `model_iter()` inside the streaming `try` with cache-aware logic**

Use fixed Anima key fields matching the client filter:

```python
key_fields = {
    "baseUrl": base_url.rstrip("/"),
    "baseModel": "Anima",
    "modelType": "LORA",
    "sort": "Highest Rated",  # anima client does not pass sort; use this constant for key stability
    "nsfw": True,
}
```

**Note:** Current `iter_anima_loras` does not send `sort`; keep key `sort` as `"Highest Rated"` (API default) so civitmatrix promotion stays consistent. Document in a code comment.

Logic sketch for `model_iter` + finalize:

```python
from listing_cache import (
    ListingCacheWriter,
    cache_paths,
    iter_cached_models,
    make_cache_key,
    probe_cache,
)

listing_state = {
    "writer": None,
    "exhausted": False,
    "cache_hit": False,
}

def model_iter() -> Iterator[dict[str, Any]]:
    if args.retry_failed:
        # existing retry loop unchanged; no cache
        ...
        listing_state["exhausted"] = True
        return

    use_cache = bool(args.use_listing_cache)
    refresh = bool(args.refresh_listing)
    key = make_cache_key(
        base_url=key_fields["baseUrl"],
        base_model=key_fields["baseModel"],
        model_type=key_fields["modelType"],
        sort=key_fields["sort"],
        nsfw=key_fields["nsfw"],
    )

    if use_cache and not refresh:
        reason, meta = probe_cache(
            LOGS,
            base_url=key_fields["baseUrl"],
            base_model=key_fields["baseModel"],
            model_type=key_fields["modelType"],
            sort=key_fields["sort"],
            nsfw=key_fields["nsfw"],
        )
        if reason == "ok" and meta is not None:
            _, jsonl = cache_paths(LOGS, key)
            listing_state["cache_hit"] = True
            job.set_meta(listingCacheHit=True)
            job.emit(
                "listing_cache_hit",
                key=key,
                items=meta.get("items"),
                pages=meta.get("pages"),
                path=str(jsonl),
            )
            log(f"Listing cache hit ({meta.get('items')} items) → {jsonl}")
            for model in iter_cached_models(jsonl):
                yield model
            listing_state["exhausted"] = True
            return
        job.emit("listing_cache_miss", key=key, reason=reason)
        log(f"Listing cache miss ({reason}); fetching from API")
    elif refresh:
        job.emit("listing_cache_miss", key=key, reason="refresh")

    log(f"Listing Anima LoRAs from {base_url} …")
    writer = None
    if use_cache:
        writer = ListingCacheWriter(LOGS, key=key, key_fields=key_fields)
        writer.begin()
        listing_state["writer"] = writer

    def on_page(*, page: int, next_page: str | None, items: list) -> None:
        if writer is not None:
            writer.append_page(page=page, next_page=next_page, items=items)

    for model in client.iter_anima_loras(on_page=on_page):
        yield model
    listing_state["exhausted"] = True
```

After `run_streaming_pool(...)` returns `(counts, cancelled, listed)`:

```python
writer = listing_state["writer"]
if writer is not None:
    limited = bool(args.limit) and listed >= int(args.limit)
    complete = bool(listing_state["exhausted"]) and not cancelled and not limited
    meta = writer.finalize(complete=complete)
    job.emit(
        "listing_cache_write",
        key=writer.key,
        path=str(writer.jsonl_path),
        complete=complete,
        pages=meta.get("pages"),
        items=meta.get("items"),
    )
    log(
        f"Listing cache write complete={complete} "
        f"pages={meta.get('pages')} items={meta.get('items')}"
    )
```

Default path (no `--use-listing-cache`): do not construct a writer; do not call `probe_cache`.

- [ ] **Step 4: Smoke — default does not create cache**

Run: `cd /run/media/arturo/Datos2/Models/anima-lora-batch && ./run.sh --dry-run --limit 3 && test ! -d logs/listing-cache && echo NO_CACHE_OK`  
Expected: dry-run succeeds; `NO_CACHE_OK`

- [ ] **Step 5: Smoke — limited write stays incomplete**

Run:

```bash
cd /run/media/arturo/Datos2/Models/anima-lora-batch
rm -rf logs/listing-cache
./run.sh --dry-run --limit 5 --use-listing-cache
python -c "import json,glob; p=glob.glob('logs/listing-cache/*.meta.json')[0]; print(json.load(open(p))['complete'])"
```

Expected: prints `False`

- [ ] **Step 6: Smoke — full cache build + hit**

Anima catalog is large; building a complete cache takes a long listing. For proof without walking 7k+ models, use a **unit-level complete file** then hit:

```bash
cd /run/media/arturo/Datos2/Models/anima-lora-batch
python <<'PY'
from pathlib import Path
from listing_cache import ListingCacheWriter, make_cache_key
from dotenv import load_dotenv
import os
load_dotenv()
base = os.environ.get("CIVITAI_BASE_URL", "https://civitai.red").rstrip("/")
fields = {"baseUrl": base, "baseModel": "Anima", "modelType": "LORA", "sort": "Highest Rated", "nsfw": True}
key = make_cache_key(base_url=fields["baseUrl"], base_model="Anima", model_type="LORA", sort="Highest Rated", nsfw=True)
# seed minimal complete cache with 3 fake models that will skip_hash or fail gracefully — better: copy real shape from one API page
print("seed key", key)
PY
```

Preferred real smoke (acceptable if slow): run `--use-listing-cache --dry-run` **without** `--limit` until listing finishes (or cancel after documenting incomplete). Faster alternative accepted for this task:

1. Seed complete cache by running writer in a script that fetches **one** page via API and marks complete **only for smoke** with those items (not production path).
2. Then `./run.sh --dry-run --limit 3 --use-listing-cache` and confirm `logs/events.jsonl` contains `listing_cache_hit`.

Run check:

```bash
grep listing_cache_hit logs/events.jsonl | tail -1
```

Expected: a JSON line with `"event": "listing_cache_hit"`

- [ ] **Step 7: Smoke — refresh forces miss reason refresh**

```bash
./run.sh --dry-run --limit 2 --use-listing-cache --refresh-listing
grep listing_cache_miss logs/events.jsonl | tail -1
```

Expected: `"reason": "refresh"` (and writer runs; with `--limit`, finalize `complete=false`)

- [ ] **Step 8: No git commit.** Proceed to promote.

---

### Task 4: Promote module + `on_page` into civitmatrix

**Files:**
- Create: `src/civitmatrix/listing_cache.py` (same as anima module; package-ready)
- Create: `tests/test_listing_cache.py` (same tests; import `from civitmatrix.listing_cache import ...`)
- Modify: `src/civitmatrix/client.py` — add `on_page` to `iter_models`

**Interfaces:**
- Consumes: Task 1 module text
- Produces: `civitmatrix.listing_cache` public helpers; `CivitClient.iter_models(..., on_page=None)`

- [ ] **Step 1: Copy `listing_cache.py` into package**

Path: `/run/media/arturo/Datos2/Models/civitmatrix/src/civitmatrix/listing_cache.py`  
Content: identical to anima `listing_cache.py`.

- [ ] **Step 2: Add unit tests under package**

Path: `/run/media/arturo/Datos2/Models/civitmatrix/tests/test_listing_cache.py`  
Same as anima tests but:

```python
from civitmatrix.listing_cache import (
    ListingCacheWriter,
    cache_paths,
    iter_cached_models,
    make_cache_key,
    probe_cache,
)
```

- [ ] **Step 3: Run tests with PYTHONPATH**

Run: `cd /run/media/arturo/Datos2/Models/civitmatrix && PYTHONPATH=src python -m unittest tests.test_listing_cache -v`  
Expected: all `ok`

- [ ] **Step 4: Add `on_page` to `iter_models` in `client.py`**

```python
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
```

Ensure `Callable` is imported in `client.py` typing imports.

- [ ] **Step 5: Commit**

```bash
cd /run/media/arturo/Datos2/Models/civitmatrix
git add src/civitmatrix/listing_cache.py src/civitmatrix/client.py tests/test_listing_cache.py
git commit -m "$(cat <<'EOF'
Add listing cache module and on_page listing callback.

EOF
)"
```

---

### Task 5: Wire civitmatrix CLI + downloader

**Files:**
- Modify: `src/civitmatrix/cli.py`
- Modify: `src/civitmatrix/downloader.py` (`run_batch`, `_iter_models_for_run`)
- Modify: wherever `run_batch(...)` is called from `cli.py` / `__main__` to pass new flags

**Interfaces:**
- Consumes: `civitmatrix.listing_cache`, `client.iter_models(..., on_page=)`
- Produces: same flag/event/meta behavior as anima; `run_batch(..., use_listing_cache: bool = False, refresh_listing: bool = False)`

- [ ] **Step 1: Add CLI flags** in `cli.py` (near verify/resume flags)

```python
p.add_argument(
    "--use-listing-cache",
    action="store_true",
    help="Opt in: reuse complete listing cache or build one while listing",
)
p.add_argument(
    "--refresh-listing",
    action="store_true",
    help="Force fresh listing from API (with --use-listing-cache, rewrite cache)",
)
```

Pass through into `run_batch` from `main()`.

- [ ] **Step 2: Extend `run_batch` signature**

```python
use_listing_cache: bool = False,
refresh_listing: bool = False,
```

Set job meta:

```python
listingCache=bool(use_listing_cache),
refreshListing=bool(refresh_listing),
listingCacheHit=False,
```

- [ ] **Step 3: Replace `_iter_models_for_run` with a richer helper**

Rename/replace to return an iterator **and** mutate a `listing_state` dict owned by `run_batch` (same pattern as Task 3). Pass `logs_dir=logger.job_path.parent`, real `base_model` / `model_type` / `sort` / `nsfw` / `client.base_url`.

On cache hit emit `listing_cache_hit` and set `listingCacheHit=True`.  
On miss emit `listing_cache_miss` with reason.  
After `run_streaming_pool`, finalize writer with:

```python
limited = bool(limit) and listed >= limit
complete = listing_state["exhausted"] and not cancelled and not limited
```

- [ ] **Step 4: Package smoke**

```bash
cd /run/media/arturo/Datos2/Models/civitmatrix
rm -rf logs/listing-cache
./run.sh --dry-run --limit 3
test ! -d logs/listing-cache && echo NO_CACHE_OK
./run.sh --dry-run --limit 3 --use-listing-cache
python -c "import json,glob; print(json.load(open(glob.glob('logs/listing-cache/*.meta.json')[0]))['complete'])"
```

Expected: `NO_CACHE_OK` then `False`

- [ ] **Step 5: Commit**

```bash
git add src/civitmatrix/cli.py src/civitmatrix/downloader.py
git commit -m "$(cat <<'EOF'
Wire opt-in listing cache into the batch runner CLI.

EOF
)"
```

---

### Task 6: Docs + ROADMAP + push

**Files:**
- Modify: `README.md` (Logs & control plane / CLI mention)
- Modify: `docs/GUIDE.md` (flags + freshness note)
- Modify: `ROADMAP.md` (check listing cache)
- Modify: `todo.md` locally only — mark `[x]` (do not commit)

- [ ] **Step 1: README blurb**

Add under control plane / after stream sentence:

```markdown
**Listing cache (opt-in):** `--use-listing-cache` reuses a complete page cache under `logs/listing-cache/` for the current filters; default runs always re-list. `--refresh-listing` forces a fresh API list (and rewrites the cache when caching is on).
```

- [ ] **Step 2: GUIDE flags table rows**

Document both flags; note default freshness; note `--limit` never marks cache complete.

- [ ] **Step 3: ROADMAP**

Under QoL / crash recovery, mark:

```markdown
- [x] Listing cache (opt-in page blobs; `--use-listing-cache` / `--refresh-listing`)
```

- [ ] **Step 4: Mark local todo**

In `todo.md`: change listing cache line to `[x]`.

- [ ] **Step 5: Ignore cache artifacts in git**

Append to `.gitignore`:

```
logs/listing-cache/
```

- [ ] **Step 6: Commit docs** (push only if user asks)

```bash
git add README.md docs/GUIDE.md ROADMAP.md .gitignore docs/superpowers/plans/2026-07-26-listing-cache.md
git commit -m "$(cat <<'EOF'
Document opt-in listing cache flags and freshness defaults.

EOF
)"
```

Also push earlier commits from Tasks 4–5 if not yet pushed.

---

## Spec coverage check

| Spec requirement | Task |
|------------------|------|
| Default never touches cache | 3, 5 |
| `--use-listing-cache` hit/miss/build | 3, 5 |
| `--refresh-listing` (+ both) | 3, 5 |
| Key fields | 1 |
| meta.json + jsonl pages | 1 |
| complete only on natural end | 3, 5 |
| `--limit` incomplete + still caps hits | 3, 5 (pool limit unchanged) |
| `--retry-failed` no cache | 3, 5 |
| `on_page` streaming write | 2, 4 |
| Events + job meta | 3, 5 |
| Docs / ROADMAP | 6 |
| anima first then promote | 1–3 then 4–6 |

## Placeholder / consistency check

- Event names match spec: `listing_cache_hit`, `listing_cache_miss`, `listing_cache_write`
- Miss reasons: `missing` \| `incomplete` \| `mismatch` \| `corrupt` \| `refresh`
- Anima `sort` key constant `"Highest Rated"` documented (client omits sort param today)
