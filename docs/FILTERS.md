# Filters

## Available today

| Filter | How |
|--------|-----|
| Base model | `--base-model` / `BASE_MODEL` |
| Model type | `--type` / `MODEL_TYPE` |
| Sort | `--sort` / `SORT` |
| NSFW on/off | `--nsfw` / `--no-nsfw` / `NSFW` |
| Max NSFW level | `--max-nsfw-level N` (keep `nsfwLevel <= N`; missing level fails closed) |
| Match base version | `--match-base-version` (newest version whose `baseModel` equals the filter) |
| Base only | `--base-only` — every `modelVersions[].baseModel` must equal `--base-model` (drops multi-base) |
| Tag include / exclude | `--tag-include` / `--tag-exclude` (comma lists; empty include = any; empty exclude = none; both AND) |
| Category | `--category` (separate dim, not tags) |
| Users / creators | `--users` (comma allow-list; single user also sent to API) |
| Users deny | `--users-deny` (comma block-list) |
| Min downloads / likes | `--min-downloads` / `--min-likes` (`stats.downloadCount` / `thumbsUpCount`, `likeCount` fallback; `0` = no floor) |
| Updated date range | `--updated-from` / `--updated-to` (inclusive `YYYY-MM-DD` on last-updated day) |
| Format | `--format` SafeTensor / PickleTensor / … / All |
| Filter preset | `--filter-preset NAME` → `logs/filter-presets/NAME.json` |
| Job manifest | `--job-manifest` (UI selection + filters JSON) |
| Limit | `--limit N` |
| Concurrency | `--concurrency` |
| Retry failures | `--retry-failed` |
| Dry run | `--dry-run` |

UI Main view exposes the same filter dimensions (Populate preview + Start / Download all), plus Save/Load presets.

### Sort values

`Highest Rated` · `Most Downloaded` · `Newest` · `Most Liked` · `Most Discussed` · `Most Collected` · `Most Buzz`

### Model types (common)

`LORA` · `LoCon` · `DoRA` · `Checkpoint` · `VAE` · `TextualInversion` · …

### Important nuance

`baseModels=Anima` (API) means “has **at least one** Anima version,” not “Anima-only.”  
With `--match-base-version` (default), CivitMatrix still downloads the newest **Anima** (or chosen base) version of that model.  
Use `--base-only` to drop models that also have other bases.

### Filter presets

JSON files under `logs/filter-presets/<name>.json` (safe names: alnum + `.` `_` `-`).

```bash
# after saving from the UI, or hand-writing JSON:
./run.sh --cli --filter-preset anima-sfw --dry-run --limit 5
```

CLI args / job manifest override preset fields when set.
