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
git clone https://github.com/ArturoWolff/civitmatrix.git
cd civitmatrix
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
./run.sh --base-model Pony --type LORA --sort Newest --concurrency 3 --limit 50
./run.sh --no-nsfw --dry-run
./run.sh --no-match-base-version   # take newest version regardless of base
```

## Recommended workflow

1. `--dry-run --limit 20` — sanity check listing + skips  
2. Point `LORA_DIR` at SM  
3. Full run (or `--limit` while testing)  
4. Watch `logs/failed.jsonl` for gated files  
5. `./run.sh --retry-failed` later  
6. Refresh SM index  

## Control plane (long runs)

While a batch is running, open a **second terminal** in the same repo folder:

| Command | Effect |
|---------|--------|
| `./run.sh --status` | Print phase, counts, current model, flags, lock |
| `./run.sh --status --json` | Same data as JSON (for scripts / future UI) |
| `./run.sh --pause` | Finish the current download, then wait |
| `./run.sh --resume` | Clear pause and continue |
| `./run.sh --cancel` | Cooperative stop after in-flight work |
| `Ctrl+C` (in the runner) | Same as `--cancel`; second Ctrl+C force-exits |

These commands do **not** need `CIVITAI_API_KEY`. They read/write under `logs/`:

- `job.json` — live status (`phase`: `listing` · `downloading` · `paused` · `done` · `cancelled` · `error`)
- `events.jsonl` — append-only event stream
- `cancel.request` / `pause.request` — request flags
- Output folder gets `.civitmatrix.lock` so two writers don’t collide

### `--status` exit codes

| Code | Meaning |
|------|---------|
| 0 | `done` |
| 1 | missing / unreadable `job.json` |
| 2 | `error` |
| 4 | `cancelled` |
| 5 | `paused` |
| 6 | active (`starting` / `listing` / `downloading`) |

### Runner exit codes

| Code | Meaning |
|------|---------|
| 0 | success |
| 2 | missing API key |
| 3 | another run holds the output-folder lock |
| 4 | cooperative cancel |
| 130 | forced exit (second Ctrl+C) |

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
./run.sh                            # resume weight partials by default
./run.sh --no-resume-partials       # delete/ignore partials; full re-get
./run.sh --keep-partials            # also keep preview download temps on start
```

On start, preview `*.preview.download*` temps are still purged (`partial_purged`); weight partials are left alone for resume.

## Post-download BLAKE3 verify

After each weight download (including Range resume), CivitMatrix hashes the file and compares to CivitAI’s `BLAKE3` **before** writing `.cm-info.json`.

| Result | What happens |
|--------|----------------|
| Match | `verify_ok` → write sidecars as usual |
| Mismatch | delete weight; `verify_fail` (retryable); no sidecar |
| No remote hash | `verify_skipped` (`no_remote_hash`); file kept |
| `--skip-verify` | `verify_skipped` (`flag`); file kept |

```bash
./run.sh                      # verify on (default)
./run.sh --skip-verify        # opt out
```

## Library heal (`--heal`)

If index counts don’t match (`blake3` / `versions` / `stems`), some files are missing metadata or have orphan sidecars. Heal consolidates the folder:

```bash
./run.sh --heal --dry-run          # report what would change
./run.sh --heal                    # repair incomplete .cm-info.json (+ preview)
./run.sh --heal --purge-orphans    # also delete sidecars with no matching weight
```

What it does:

1. Scans `--out` for incomplete sidecars (missing ModelId / VersionId / BLAKE3)
2. Hashes those weights (BLAKE3), looks up `GET /api/v1/model-versions/by-hash/{hash}`  
   (falls back to existing `VersionId` when by-hash 404s — common for some hosts)
3. Rewrites `.cm-info.json` (always writing the computed local BLAKE3) and fetches a preview when missing
4. Deletes empty/corrupt weights; re-downloads when a VersionId is known
5. Orphan sidecars (no weight): re-download if VersionId known, else report — or delete with `--purge-orphans`

Normal batch runs also print a richer index line, e.g.  
`Local index: 7156 blake3, 7162 versions, 7163 stems (missingBlake3=7, orphanInfo=2)`.

## Security

- Keep API keys in `.env` only (gitignored)  
- Rotate keys if they leak  
- Details: [SECURITY.md](../SECURITY.md)

## Support

- [Linktree](https://linktr.ee/ArturoWolff)  
- [Ko-fi](https://ko-fi.com/arturowolff)
