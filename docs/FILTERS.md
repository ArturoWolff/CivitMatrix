# Filters

## Available today (v0.1)

| Filter | How |
|--------|-----|
| Base model | `--base-model` / `BASE_MODEL` |
| Model type | `--type` / `MODEL_TYPE` |
| Sort | `--sort` / `SORT` |
| NSFW on/off | `--nsfw` / `--no-nsfw` / `NSFW` |
| Match base version | `--match-base-version` (newest version whose `baseModel` equals the filter) |
| Limit | `--limit N` |
| Concurrency | `--concurrency` |
| Retry failures | `--retry-failed` |
| Dry run | `--dry-run` |

### Sort values

`Highest Rated` · `Most Downloaded` · `Newest` · `Most Liked` · `Most Discussed` · `Most Collected` · `Most Buzz`

### Model types (common)

`LORA` · `LoCon` · `DoRA` · `Checkpoint` · `VAE` · `TextualInversion` · …

### Important nuance

`baseModels=Anima` (API) means “has **at least one** Anima version,” not “Anima-only.”  
With `--match-base-version` (default), CivitMatrix still downloads the newest **Anima** (or chosen base) version of that model.

## Coming next

Tracked in [ROADMAP.md](../ROADMAP.md):

- Tag include / exclude  
- Minimum download or thumbs-up counts  
- Creator allow / deny lists  
- True base-only (reject multi-base models)  
- Published date range  
- NSFW level thresholds  
- Saved filter presets (import / export)
