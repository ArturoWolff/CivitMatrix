# CivitMatrix User Guide

Cross-platform setup, personalization, and Stability Matrix paths.

## Requirements

- Python **3.10+**
- A [CivitAI API key](https://civitai.com/user/account)
- Disk space (large base-model sweeps can be terabytes)
- Optional: [Stability Matrix](https://github.com/LykosAI/StabilityMatrix) installed

## Install

### Option A — wrappers (recommended)

```bash
git clone https://github.com/ArturoWolff/CivitMatrix.git
cd CivitMatrix
cp .env.example .env
# edit .env
```

| OS | Command |
|----|---------|
| Linux / macOS | `chmod +x run.sh && ./run.sh --help` |
| Windows | `.\run.ps1 --help` |

Wrappers create `.venv`, install the package editable, and run `python -m civitmatrix`.

### Option B — manual venv

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e .
python -m civitmatrix --help
```

## Point at Stability Matrix

Set `LORA_DIR` (or `--out`) to the folder SM already indexes.

### Typical locations

| Platform | Example |
|----------|---------|
| Portable / Linux | `/path/to/StabilityMatrix/Data/Models/Lora` |
| Windows portable | `C:\StabilityMatrix\Data\Models\Lora` |
| macOS | `~/Library/Application Support/...` or your portable `Data/Models/Lora` |

For checkpoints use `Data/Models/StableDiffusion` (and `--type Checkpoint`).  
For LoRA, keep `Data/Models/Lora`.

After a run: **refresh the model index in SM** (or restart the app). Green **Installed** appears when BLAKE3 matches.

## Personalization cheatsheet

```env
CIVITAI_API_KEY=           # required
CIVITAI_BASE_URL=https://civitai.red
LORA_DIR=./downloads/Lora
BASE_MODEL=Anima
MODEL_TYPE=LORA
SORT=Highest Rated
MAX_CONCURRENT=2
NSFW=true
MATCH_BASE_VERSION=true
KEEP_PARTIALS=false
RESUME_PARTIALS=true
SKIP_VERIFY=false
```

CLI overrides env for a single run:

```bash
./run.sh --cli --base-model Pony --type LORA --sort Newest --concurrency 3 --limit 50
./run.sh --cli --no-nsfw --dry-run
./run.sh --cli --no-match-base-version   # take newest version regardless of base
```

## Recommended workflow

1. `--dry-run --limit 20` — sanity check listing + skips  
2. Point `LORA_DIR` at SM  
3. Full run (or `--limit` while testing)  
4. Watch `logs/failed.jsonl` for gated files  
5. `./run.sh --cli --retry-failed` later  
6. Refresh SM index  

## Control plane (long runs)

While a batch is running, open a **second terminal** in the same repo folder:

| Command | Effect |
|---------|--------|
| `./run.sh --status` | Print phase, counts, current model, flags, lock |
| `./run.sh --status --json` | Same data as JSON (for scripts / UI) |
| `./run.sh --pause` | Finish the current download, then wait |
| `./run.sh --resume` | Clear pause and continue |
| `./run.sh --cancel` | Cooperative stop after in-flight work |
| `Ctrl+C` (in the runner) | Same as `--cancel`; second Ctrl+C force-exits |

These commands do **not** need `CIVITAI_API_KEY`. They read/write under `logs/`:

- `job.json` — live status (`phase`: `running` · `paused` · `done` · `cancelled` · `error`; legacy `listing` / `downloading` still recognized)
- `events.jsonl` — append-only event stream (`stream_start`, `listing_progress`, …)
- `cancel.request` / `pause.request` — request flags
- Output folder gets `.civitmatrix.lock` so two writers don’t collide

Batch runs **stream** models as API pages arrive (phase `running`, `streamMode: true` in `job.json`). Workers stay bounded (`concurrency * 2` in flight) so large catalogs are not held in RAM. Skip / Range resume / BLAKE3 verify still apply per model — restarting a run does not re-download already verified files (local index is recursive under `--out`, so SM category subfolders still skip).

### `--status` exit codes

| Code | Meaning |
|------|---------|
| 0 | `done` |
| 1 | missing / unreadable `job.json` |
| 2 | `error` |
| 4 | `cancelled` |
| 5 | `paused` |
| 6 | active (`starting` / `running` / `listing` / `downloading` / `healing`) |

### Runner exit codes

| Code | Meaning |
|------|---------|
| 0 | success |
| 2 | missing API key |
| 3 | another run holds the output-folder lock |
| 4 | cooperative cancel |
| 5 | disk / preflight failure (free space below `--disk-floor-gib`) |
| 130 | forced exit (second Ctrl+C) |

Note: runner exit **5** (disk) is unrelated to `--status` exit **5** (paused).

### Disk floor

Default free-space floor is **2 GiB** on the output directory (`--disk-floor-gib` / `DISK_FLOOR_GIB`; `0` disables hard checks). At start the runner emits `disk_status` and aborts with exit 5 if below floor. Mid-run downloads recheck and may emit `disk_full` / stop with exit 5. Soft `disk_warn` when a known file size exceeds free space but the floor is still met.

### Download rate limit

Cap total download bandwidth (weights, heal redownloads, previews) with a **global** shared limiter:

```bash
# .env
DOWNLOAD_RATE_LIMIT_MBS=5

# or one-shot
./run.sh --cli --download-rate-limit 5
./run.sh --cli --download-rate-limit 0   # unlimited
```

Units are **MiB/s** (1024² bytes). With `--concurrency 2`, both workers share the same budget (not 5+5).

### Byte progress

Weight downloads print a throttled `\r` progress line on stderr and emit `download_progress` events about every **max(5%, 8 MiB)**. `job.json` `current` may include `bytes` / `total` / `pct` for `--status`.

### Fail ↔ event ids

Each failure writes the same `eventId` on the `fail` (or `verify_fail`) event and the `failed.jsonl` row so the UI can join them. Old rows without `eventId` remain valid.

### Fish shell note

`source .venv/bin/activate` is bash-only. Prefer:

```fish
source .venv/bin/activate.fish
# or
./run.sh --status
```

## Preview files

Previews are saved with an extension that matches **file content** (magic bytes), not always `.jpeg`.  
Still images prefer the API image URL; video previews become `.preview.mp4`.

## Crash leftovers & Range resume

Downloads write to `*.safetensors.partial` then rename into place. If the process dies mid-file, the next run **keeps** that partial and sends `Range: bytes={offset}-` to continue (event `download_resume`). If the server ignores Range (HTTP 200) or returns 416, the client restarts a full download.

```bash
./run.sh --cli                      # resume weight partials by default
./run.sh --cli --no-resume-partials # delete/ignore partials; full re-get
./run.sh --cli --keep-partials      # also keep preview download temps on start
```

On start, preview `*.preview.download*` temps are still purged (`partial_purged`); weight partials are left alone for resume.

## Listing cache (opt-in)

By default every run fetches a **fresh** catalog from the CivitAI API — no cache read or write. Opt in when repeat runs should skip listing HTTP for the same filters.

| Flag | Behavior |
|------|----------|
| *(default)* | Fresh list from API; do not read or write `logs/listing-cache/` |
| `--use-listing-cache` | Reuse a matching **complete** page cache, or fetch from API and build one while listing |
| `--refresh-listing` | Always fetch from API (ignores any existing cache) |
| `--use-listing-cache --refresh-listing` | Fetch from API and overwrite the cache for current filters |

Cache files live under `logs/listing-cache/` as `<key>.meta.json` + `<key>.jsonl` (one API page per line). There is no TTL — refresh on demand with `--refresh-listing` or delete the pair.

**Freshness:** only caches with `complete: true` are reused. A run stopped early by `--limit` or cancel leaves `complete: false`, so the next `--use-listing-cache` run still hits the API. A cache **hit** still respects `--limit` (only the first N models are processed). `--retry-failed` never uses the listing cache.

```bash
./run.sh --cli --dry-run --use-listing-cache          # build cache while listing (no limit → complete)
./run.sh --cli --dry-run --use-listing-cache          # second run: cache hit, no listing HTTP
./run.sh --cli --dry-run --use-listing-cache --refresh-listing   # force fresh list + rewrite cache
./run.sh --cli --dry-run --limit 5 --use-listing-cache           # partial cache; next run still re-lists
```

Events: `listing_cache_hit`, `listing_cache_miss`, `listing_cache_write` in `logs/events.jsonl`.

## Post-download BLAKE3 verify

After each weight download (including Range resume), CivitMatrix hashes the file and compares to CivitAI’s `BLAKE3` **before** writing `.cm-info.json`.

| Result | What happens |
|--------|----------------|
| Match | `verify_ok` → write sidecars as usual |
| Mismatch | delete weight; `verify_fail` (retryable); no sidecar |
| No remote hash | `verify_skipped` (`no_remote_hash`); file kept |
| `--skip-verify` | `verify_skipped` (`flag`); file kept |
| Stale API BLAKE3 but `by-hash(local)` matches version | `verify_ok_stale_meta`; file kept |

```bash
./run.sh --cli                # verify on (default)
./run.sh --cli --skip-verify        # opt out
```

## Latest-only versions (default)

CivitMatrix keeps **one version per model**: the newest matching base-model version.

- After `skip_hash` / `skip_version` / successful download+verify, older local stems with the same `ModelId` (weight + `.cm-info.json` + `.swarm.json` + preview) are deleted.
- Event: `prune_old_version`; job count: `pruned`.
- Opt out: `./run.sh --cli --keep-old-versions`

## Update-only + SM library tools

Keep an existing folder current without downloading brand-new models:

```bash
./run.sh --cli --update-only
./run.sh --cli --update-only --dry-run --limit 100
```

Skips `skip_not_installed` / `skip_uptodate`; downloads only when the remote picked version is newer than the max local `VersionId` for that `ModelId`, then prunes older versions (unless `--keep-old-versions`).

After a successful batch or heal, CivitMatrix logs an SM refresh reminder and emits `sm_refresh_hint`.

Offline library helpers (no API key):

```bash
./run.sh --cli --sm-parity                 # exit 1 if SourceUrl/hash/field issues
./run.sh --cli --import-sm-manifest        # append cm-info rows into logs/manifest.jsonl
```

Details: [STABILITY-MATRIX.md](STABILITY-MATRIX.md).

## Local Win95 UI (default)

`./run.sh` opens a localhost UI (`127.0.0.1:7860`) with three views:

| View | Purpose |
|------|---------|
| **Main** | Filters → **Populate** preview → **search bar** (name / id / creator, client-side) → per-row **latest / pick…** versions → **Start**. “Download all matching filters” = full catalog (ignores preview cap). Uncheck it to download only checked rows + version picks. Optional **Update only** (`--update-only`). |
| **Directories** | Models root + per-type folders (**Browse…**), API key (masked), base URL, disk floor → Save (atomic `.env`; syncs `LORA_DIR`). On **Windows**, Browse results and path fields use backslash separators. |
| **Logs** | Job counts; failure browser (retryable/all, search, export txt/jsonl); event console; Help; **Retry failed + resume**; Heal; Categorize dry-run / apply (`POST /api/categorize`) |

Theme: Win95 (default) or Modern stub (CSS variables). Bound to `127.0.0.1` only.

### Windows paths

When picking folders with **Browse…**, the UI normalizes Windows drive paths to backslashes (`C:\StabilityMatrix\Data\Models\Lora`). Forward slashes still work when pasting; Save persists whatever you enter.

Headless / scripts:

```bash
./run.sh --cli --limit 10
./run.sh --cli --retry-failed
```

Tag filters: empty include = all tags; empty exclude = exclude none; both set = must match include and avoid exclude. Format / Category / Users / Users deny / Min downloads & likes / Base only / Max NSFW level / Updated from–to are separate dimensions (AND with tags). See [FILTERS.md](FILTERS.md); presets save to `logs/filter-presets/`.

## Categorize library (`--categorize`)

Sort installed weights into flat bucket folders under `--out` using `.cm-info.json` `Tags` (same vocabulary as `sortHints` / `sort_hints_from_tags`):

| Bucket | Tag hints (any match) |
|--------|------------------------|
| `characters/` | character, characters |
| `clothes/` | clothing, clothes, costume |
| `styles/` | style, styles |
| `concepts/` | concept, concepts |
| `uncategorized/` | no category tag |

Priority when several match: **character → clothes → style → concept**. Each move keeps basename files together (weight + `.cm-info.json` + `.swarm.json` + preview) in the bucket root — not nested further. Already-correct bucket installs are skipped. Recursive scan (same as local index).

```bash
./run.sh --cli --categorize              # dry-run plan + counts (default)
./run.sh --cli --categorize --apply      # perform moves
./run.sh --cli --categorize --out ./downloads/Lora
```

GUI (Logs): **Categorize dry-run** / **Categorize apply** → `POST /api/categorize` with `{apply: false|true}`.

If index counts don’t match (`blake3` / `versions` / `stems`), some files are missing metadata or have orphan sidecars. Heal consolidates the folder:

```bash
./run.sh --cli --heal --dry-run          # report what would change
./run.sh --cli --heal                    # repair incomplete .cm-info.json (+ preview)
./run.sh --cli --heal --purge-orphans    # also delete sidecars with no matching weight
./run.sh --cli --heal --refresh-sidecars --write-swarm
# re-fetch API metadata for complete installs; rewrite SourceUrl + optional .swarm.json
```

**SwarmUI sidecars** are opt-in (default off):

```bash
./run.sh --cli --write-swarm …           # downloads also write *.swarm.json
# or WRITE_SWARM=1 in .env
```

With `--write-swarm`, new `.swarm.json` files include `modelspec.architecture` (mapped from Civit base model + type) so SwarmUI classifies LoRAs correctly (e.g. Anima → `anima/lora`).

To backfill / correct architecture on an existing tree (local `.cm-info.json` only — no API, no weight changes):

```bash
./run.sh --cli --fix-swarm-architecture --dry-run
./run.sh --cli --fix-swarm-architecture
```

GUI: **Write SwarmUI .swarm.json** on Main; **Refresh sidecars on heal** + **Heal library** on Logs.

What it does:

1. Scans `--out` for incomplete sidecars (missing ModelId / VersionId / BLAKE3)
2. Hashes those weights (BLAKE3), looks up `GET /api/v1/model-versions/by-hash/{hash}`  
   (falls back to existing `VersionId` when by-hash 404s — common for some hosts)
3. Rewrites `.cm-info.json` (always writing the computed local BLAKE3; sets `SourceUrl` when ids are known); writes `.swarm.json` only with `--write-swarm`; fetches a preview when missing
4. With `--refresh-sidecars`, also re-fetches and rewrites complete installs that already have ModelId/VersionId
5. **Hash mismatch** (recorded or remote BLAKE3 ≠ file): stage re-download + verify — never delete the existing weight on failure. If CDN bytes never match the published BLAKE3 but the version API is live, heal keeps the complete download, writes sidecars, and sets `CivitMatrix.hashMismatchKept` / `staleRemoteMeta` / `localBlake3` so later heals skip redownload thrash (same idea as `remoteUnavailable` for 404s). `remoteUnavailable` / `hashMismatchKept` stems are left alone.
6. Deletes empty/corrupt weights; re-downloads when a VersionId is known
7. Orphan sidecars (no weight): re-download if VersionId known, else report — or delete with `--purge-orphans`

Normal batch runs also print a richer index line, e.g.  
`Local index: 7156 blake3, 7162 versions, 7163 stems (missingBlake3=7, orphanInfo=2)`.

## Security

- Keep API keys in `.env` only (gitignored)  
- Rotate keys if they leak  
- Details: [SECURITY.md](../SECURITY.md)

## Support

- [Linktree](https://linktr.ee/ArturoWolff)  
- [Ko-fi](https://ko-fi.com/arturowolff)
