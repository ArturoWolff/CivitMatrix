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

## Crash leftovers (`*.partial`)

Downloads write to a temp `*.partial` (and preview temps) then rename into place. If the process dies mid-file, those leftovers can sit in `--out`.

On the next run (after acquiring the lock), CivitMatrix **deletes** top-level stale temps in that folder and emits `partial_purged`.

```bash
./run.sh --keep-partials          # skip the sweep
# or KEEP_PARTIALS=true in .env
```

HTTP Range resume of kept partials is a follow-up feature.

## Security

- Keep API keys in `.env` only (gitignored)  
- Rotate keys if they leak  
- Details: [SECURITY.md](../SECURITY.md)

## Support

- [Linktree](https://linktr.ee/ArturoWolff)  
- [Ko-fi](https://ko-fi.com/arturowolff)
