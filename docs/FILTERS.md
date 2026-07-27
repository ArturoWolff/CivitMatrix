# Filters

## Available today (v0.2)

| Filter | How |
|--------|-----|
| Base model | `--base-model` / `BASE_MODEL` |
| Model type | `--type` / `MODEL_TYPE` |
| Sort | `--sort` / `SORT` |
| NSFW on/off | `--nsfw` / `--no-nsfw` / `NSFW` |
| Match base version | `--match-base-version` (newest version whose `baseModel` equals the filter) |
| Tag include / exclude | `--tag-include` / `--tag-exclude` (comma lists; empty include = any; empty exclude = none; both AND) |
| Category | `--category` (separate dim, not tags) |
| Users / creators | `--users` (comma usernames; single user also sent to API) |
| Format | `--format` SafeTensor / PickleTensor / any |
| Job manifest | `--job-manifest` (UI selection + filters JSON) |
| Limit | `--limit N` |
| Concurrency | `--concurrency` |
| Retry failures | `--retry-failed` |
| Dry run | `--dry-run` |

UI Main view exposes the same filter dimensions (Populate preview + Start / Download all).

### Sort values

`Highest Rated` · `Most Downloaded` · `Newest` · `Most Liked` · `Most Discussed` · `Most Collected` · `Most Buzz`

### Model types (common)

`LORA` · `LoCon` · `DoRA` · `Checkpoint` · `VAE` · `TextualInversion` · …

### Important nuance

`baseModels=Anima` (API) means “has **at least one** Anima version,” not “Anima-only.”  
With `--match-base-version` (default), CivitMatrix still downloads the newest **Anima** (or chosen base) version of that model.

## Coming next

Tracked in [ROADMAP.md](../ROADMAP.md):

| Filter | Notes |
|--------|--------|
| Min downloads / likes | Not shipped |
| True base-only | Exclude multi-base models |
| Published date range | Not shipped |
| NSFW level threshold | Beyond on/off |
| Filter presets JSON | Save/load |
