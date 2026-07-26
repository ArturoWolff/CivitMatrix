# Listing cache (opt-in) — design

Date: 2026-07-26  
Status: approved for implementation planning  
Scope: `anima-lora-batch/` first, then promote to `civitmatrix/`

## Goal

Default runs always fetch a **fresh** catalog from the API (current behavior).  
Users may **opt in** to a listing cache so repeat runs skip listing API calls.  
Refresh is **on demand** only — no TTL, no silent staleness policy beyond “until you refresh.”

## Non-goals

- Automatic background refresh
- Cursor-only resume without page payloads
- Using cache for `--retry-failed` (still fetches individual models)
- Changing skip / Range / BLAKE3 verify behavior

## CLI

| Flags | Behavior |
|-------|----------|
| *(default)* | Fresh list from API; do not read or write cache |
| `--use-listing-cache` | If a matching **complete** cache exists → yield from cache; else fetch from API and write cache |
| `--refresh-listing` | Always fetch from API |
| `--use-listing-cache --refresh-listing` | Fetch from API and overwrite cache |

Optional env later (`LISTING_CACHE=1`); not required for v1.

## Cache key

Hash of:

- `baseUrl`
- `baseModel`
- `modelType`
- `sort`
- `nsfw`

Mismatch → treat as cache miss.

## Storage

Directory: `logs/listing-cache/`

Per key:

- `<key>.meta.json` — `{ "v": 1, "key": { ... }, "builtAt", "complete", "pages", "items" }`
- `<key>.jsonl` — one page object per line:  
  `{ "page": N, "nextPage": "<url>"|null, "items": [ ...model objects ] }`

Sidecar meta avoids rewriting the first JSONL line; `complete` flips atomically via temp + replace on the meta file.

Write flow when building/refreshing:

1. Truncate/create `.jsonl`; write meta with `complete: false`
2. Append page lines as API pages arrive (stream-friendly)
3. Set meta `complete: true` only when listing finished to the natural end (no cancel, no `--limit` stop)

Invalid / unusable if:

- Meta or jsonl missing / unreadable / corrupt JSON
- Meta `complete` is not `true`
- Key fields do not match current run filters
- Run stopped early via `--limit` or cancel while writing → leave `complete: false`

No TTL. User refreshes with `--refresh-listing` or deletes the pair of files.

**`--limit`:** always caps how many models the worker pool processes. A limited run never marks the cache complete. A cache **hit** still respects `--limit` (yield/process only the first N models from cached pages).

## Runtime wiring

New module `listing_cache.py` (private runner + package):

- `cache_key(...)`, `cache_path(logs_dir, key)`
- `try_open_readable(...)` → iterator of models or miss
- `ListingCacheWriter` — begin / append_page / finalize(complete=bool)

`CivitClient.iter_models` (and anima equivalent):

- Optional `on_page(page: dict)` callback invoked after each successful page fetch so the writer can append without buffering the full catalog

`_iter_models_for_run` / anima main loop:

- Cache hit → yield models from page lines; emit `listing_cache_hit`
- Cache miss / refresh → API iterator; if caching, write pages; emit `listing_cache_miss` / `listing_cache_write`
- On cancel or `--limit` before natural end → `finalize(complete=False)`

Streaming worker pool unchanged: still process as models arrive (from cache or API).

## Control plane

`job.json` meta:

- `listingCache`: whether `--use-listing-cache` was set
- `listingCacheHit`: whether this run served models from cache
- `refreshListing`: whether `--refresh-listing` was set

Events:

- `listing_cache_hit` — `{ key, items, pages, path }`
- `listing_cache_miss` — `{ key, reason }` (`missing` | `incomplete` | `mismatch` | `corrupt` | `refresh`)
- `listing_cache_write` — `{ key, path, complete, pages, items }`

## Docs

- README / GUIDE: short subsection under control plane or CLI flags
- ROADMAP: mark listing cache done when shipped
- Local `todo.md`: check when pushed to GitHub

## Testing / smoke

1. Default dry-run — no files created under `logs/listing-cache/`
2. `--use-listing-cache --limit 5` — cache file exists with `complete: false`; second identical run still misses / rebuilds
3. `--use-listing-cache` without limit (or enough to finish a small fixture) — second run emits `listing_cache_hit` and skips listing HTTP
4. `--refresh-listing --use-listing-cache` — listing HTTP again; cache rewritten `complete: true`

## Implementation order

1. `listing_cache.py` + client `on_page` in `anima-lora-batch/`
2. Wire flags into anima CLI; smoke on real Anima listing
3. Promote to `civitmatrix/`; docs; push
