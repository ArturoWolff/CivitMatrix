<p align="center">
  <img src="assets/banner.svg" alt="CivitMatrix" width="100%" />
</p>

<p align="center">
  <strong>Batch-download CivitAI models the Stability Matrix way.</strong><br/>
  Green <em>Installed</em> badges. Connected metadata. Resume-friendly. Cross-platform.
</p>

<p align="center">
  <a href="https://www.python.org/"><img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-2ee6a6?style=for-the-badge&labelColor=0b1220" /></a>
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-3ecbff?style=for-the-badge&labelColor=0b1220" /></a>
  <img alt="Linux macOS Windows" src="https://img.shields.io/badge/os-Linux%20%7C%20macOS%20%7C%20Windows-f4f7fb?style=for-the-badge&labelColor=0b1220" />
  <img alt="Stability Matrix" src="https://img.shields.io/badge/Stability%20Matrix-native%20sidecars-2ee6a6?style=for-the-badge&labelColor=0b1220" />
</p>

<p align="center">
  <a href="https://linktr.ee/ArturoWolff">Linktree</a> ·
  <a href="https://ko-fi.com/arturowolff">Ko-fi</a> ·
  <a href="ROADMAP.md">Roadmap</a> ·
  <a href="docs/GUIDE.md">Guide</a> ·
  <a href="docs/STABILITY-MATRIX.md">SM Compatibility</a>
</p>

---

## Why CivitMatrix?

Stability Matrix’s model browser marks a model **Installed** when the local file’s **BLAKE3** hash matches CivitAI. You don’t have to click download one-by-one in the UI.

CivitMatrix:

1. Lists models from the CivitAI API (any base model / type — Anima LoRAs are the showcase default)
2. Picks the newest version that matches your base model
3. Downloads weights into your SM Models folder
4. Writes **SM-native sidecars**: `.cm-info.json` + preview (extension matches real media type)
5. Skips what you already have; logs failures for later retries
6. Exposes a **live control plane** (`job.json`, cancel / pause / status) for long runs and a future UI

<p align="center">
  <img src="assets/demo-terminal.svg" alt="Terminal demo" width="92%" />
</p>

```mermaid
flowchart LR
  A[CivitAI API] --> B[List + filter]
  B --> C{Already have BLAKE3?}
  C -->|yes| D[Skip]
  C -->|no| E[Download safetensors]
  E --> F[Write .cm-info.json]
  F --> G[Write preview]
  G --> H[SM index refresh]
  H --> I[Green Installed]
```

---

## Quickstart

### 1. Clone

```bash
git clone https://github.com/ArturoWolff/civitmatrix.git
cd civitmatrix
cp .env.example .env
```

### 2. Add your API key

Edit `.env` and set:

```env
CIVITAI_API_KEY=your_key_here
LORA_DIR=/path/to/StabilityMatrix/Data/Models/Lora
```

> Get a key under CivitAI → Account → API Keys. **Never commit `.env`.**

### 3. Run

**Linux / macOS**

```bash
chmod +x run.sh
./run.sh --dry-run --limit 5
./run.sh
```

**Windows (PowerShell)**

```powershell
.\run.ps1 --dry-run --limit 5
.\run.ps1
```

**Any platform (Python module)**

```bash
python3 -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows:     .venv\Scripts\Activate.ps1
pip install -e .
python -m civitmatrix --help
```

Then refresh Stability Matrix’s model index (or restart SM) to see green **Installed** labels.

---

## Personalize

| Knob | Env var | CLI | Default |
|------|---------|-----|---------|
| API key | `CIVITAI_API_KEY` | — | *(required)* |
| API host | `CIVITAI_BASE_URL` | — | `https://civitai.red` |
| Output folder | `LORA_DIR` | `--out` | `./downloads/Lora` |
| Base model | `BASE_MODEL` | `--base-model` | `Anima` |
| Model type | `MODEL_TYPE` | `--type` | `LORA` |
| Sort | `SORT` | `--sort` | `Highest Rated` |
| NSFW | `NSFW` | `--nsfw` / `--no-nsfw` | `true` |
| Match base version | `MATCH_BASE_VERSION` | `--match-base-version` | `true` |
| Concurrency | `MAX_CONCURRENT` | `--concurrency` | `2` |
| Keep preview temps | `KEEP_PARTIALS` | `--keep-partials` | `false` |
| Range-resume weights | `RESUME_PARTIALS` | `--resume-partials` / `--no-resume-partials` | `true` |

Examples:

```bash
# Showcase: all Anima LoRAs (SFW + NSFW)
./run.sh --base-model Anima --type LORA

# Illustrious checkpoints, most downloaded first
./run.sh --base-model Illustrious --type Checkpoint --sort "Most Downloaded"

# Dry-run + retry later
./run.sh --dry-run --limit 20
./run.sh --retry-failed
```

### Control a long run (second terminal)

No API key needed for these:

```bash
./run.sh --status              # human summary of logs/job.json
./run.sh --status --json       # machine-readable snapshot
./run.sh --pause               # finish in-flight file, then wait
./run.sh --resume              # continue
./run.sh --cancel              # cooperative stop (exit 4); Ctrl+C does the same

# Fix incomplete sidecars / orphans in an existing library
./run.sh --heal --dry-run
./run.sh --heal
./run.sh --heal --purge-orphans
```

Full walkthrough: [docs/GUIDE.md](docs/GUIDE.md) · Filters: [docs/FILTERS.md](docs/FILTERS.md)

---

## Stability Matrix compatibility

Downloads land as:

```text
YourModels/Lora/
  my-lora.safetensors
  my-lora.cm-info.json
  my-lora.preview.png    # or .jpeg / .webp / .mp4 — sniffed from content
```

That matches what SM writes when you download from its browser — so hashes index cleanly and connected metadata (triggers, version ids, etc.) stays available.

Deep dive: [docs/STABILITY-MATRIX.md](docs/STABILITY-MATRIX.md)

---

## Logs & control plane

| File | Purpose |
|------|---------|
| `logs/job.json` | Live phase, counts, current model, timestamps |
| `logs/events.jsonl` | Structured diary (`run_start`, `download_*`, `fail`, `paused`, …) |
| `logs/failed.jsonl` | Failures with `retryable` flag — feed `--retry-failed` |
| `logs/manifest.jsonl` | Success rows + `sortHints` for upcoming categorizing |
| `logs/run.log` | Console transcript |
| `logs/cancel.request` / `pause.request` | Flags written by `--cancel` / `--pause` |
| `<out>/.civitmatrix.lock` | One writer per output folder |

On start (after lock), preview download temps are purged; **weight** `*.safetensors.partial` are kept and **HTTP Range-resumed** on the next download (`download_resume` event). Use `--no-resume-partials` to force a full re-get. `--keep-partials` also keeps preview temps.

**`--status` exit codes:** `0` done · `1` missing job · `2` error · `4` cancelled · `5` paused · `6` active  
**Runner exits:** `0` ok · `2` missing API key · `3` lock busy · `4` cancelled · `130` forced second Ctrl+C

---

## Roadmap (teaser)

- **Filters** — tags, min downloads, base-only, creators  
- **Categorizing** — auto-bucket into characters / styles / concepts / clothes  
- **GUI** — local Win95-flavored browser UI polling the control plane  
- **Deeper SM hooks** — index refresh helpers, update-only mode  

See the full plan: [ROADMAP.md](ROADMAP.md)

---

## Support the project

If CivitMatrix saves you hours of clicking:

- **Linktree:** [linktr.ee/ArturoWolff](https://linktr.ee/ArturoWolff)  
- **Ko-fi:** [ko-fi.com/arturowolff](https://ko-fi.com/arturowolff)

---

## Disclaimer

Use CivitAI’s API responsibly and within their terms. Early-access / Buzz-gated / private files may fail — those rows land in `logs/failed.jsonl` for later. This project is not affiliated with CivitAI or LykosAI / Stability Matrix.

## License

[MIT](LICENSE) © Arturo Wolff
