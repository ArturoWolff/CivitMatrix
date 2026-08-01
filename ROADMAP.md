# Roadmap

CivitMatrix starts as a sharp CLI. Here’s where it’s headed.

**Current package version: 1.0.0** (v0.2–v0.5 + QoL rate-limit = 1.0).

## v1.0.0 — shipped

v1.0 packages everything below as the first stable release:

- [x] v0.1 core CLI + SM sidecars + control plane + heal / resume / verify  
- [x] v0.2 filters (tags, stats floors, base-only, NSFW level, presets, …)  
- [x] v0.3 categorize + recursive local index  
- [x] v0.4 Win95 browser UI  
- [x] v0.5 SM helpers (`--update-only`, `--sm-parity`, `--import-sm-manifest`, refresh hints)  
- [x] QoL bandwidth / rate limits (`DOWNLOAD_RATE_LIMIT_MBS` / `--download-rate-limit`)

Deferred post-1.0: Translations.

## v0.1 — shipped

- [x] Cross-platform CLI (`python -m civitmatrix`, `run.sh`, `run.ps1`)
- [x] Stability Matrix–native sidecars (`.cm-info.json` + preview; `SourceUrl` + `.swarm.json` with `modelspec.architecture` for SwarmUI)
- [x] Preview extension from content (JPEG / PNG / WebP / MP4, …)
- [x] BLAKE3 / version skip + resume
- [x] `failed.jsonl` + `manifest.jsonl` (with `sortHints`)
- [x] Configurable base model, type, sort, NSFW, concurrency
- [x] Showcase defaults for Anima LoRAs
- [x] Live control plane: `logs/job.json` + `logs/events.jsonl`
- [x] Exclusive output-dir lock (`.civitmatrix.lock`)
- [x] Cooperative `--cancel` / Ctrl+C (exit 4)
- [x] Cooperative `--pause` / `--resume`
- [x] `--status` / `--status --json` with stable exit codes
- [x] On start: purge stale `*.partial` / preview download temps (`--keep-partials` to skip)
- [x] `--heal` library consolidate (BLAKE3 by-hash sidecars, orphan purge, bad-weight repair; `remoteUnavailable` / `hashMismatchKept` stop redownload thrash)
- [x] Richer local index log (missingBlake3 / orphanInfo / …)
- [x] HTTP Range resume for interrupted weight downloads
- [x] Post-download BLAKE3 verify (`--skip-verify` to opt out)
- [x] Stream process while listing (bounded in-flight pool; phase `running`)
- [x] Disk floor + soft size warn (`--disk-floor-gib`, runner exit 5)
- [x] Byte download progress (CLI + events)
- [x] Global download rate limit (`DOWNLOAD_RATE_LIMIT_MBS` / `--download-rate-limit`)
- [x] `failed.jsonl` ↔ `eventId` on fail events

## v0.2 — Filters

- [x] Tag include / exclude  
- [x] Min downloads / likes  
- [x] Creator allow / deny (`--users` / `--users-deny`)  
- [x] True **base-only** (exclude multi-base models)  
- [x] Date range (`--updated-from` / `--updated-to`) + NSFW level caps (`--max-nsfw-level`)  
- [x] Filter presets (JSON under `logs/filter-presets/`; `--filter-preset` + UI Save/Load)  
- [x] Category + file format dims (CLI + UI)

## v0.3 — Categorizing

- [x] Auto-sort into `characters/` · `styles/` · `concepts/` · `clothes/` (+ `uncategorized/`) via `--categorize`  
- [x] Use cm-info `Tags` + `sort_hints_from_tags` heuristics (priority: character → clothes → style → concept)  
- [x] Recursive local index (subfolder installs still skip / heal / prune / diagnostics)  
- [x] Dry-run move plan by default; `--categorize --apply` writes (weight + sidecars + preview)

## v0.4 — GUI

- [x] Lightweight local **browser** UI (Win95-flavored; no Electron)  
- [x] Poll `job.json` / `events.jsonl` only (control plane already shipped)  
- [x] Main / Directories / Logs; Start / Cancel / Pause / Resume  
- [x] Populate + select models; version latest / multi  
- [x] Filter dims: tags include/exclude, category, users, format, min stats, base-only, NSFW level, presets  
- [x] Per-type output directories + Browse… + portable models root + theme stub + failures list + Help
- [x] Failure browser polish (all/retryable, search, export) + Main table search
- [x] Saved filter presets UX (Load/Save)

## v0.5 — Deeper SM integration

- [x] Post-run “refresh index” guidance / helpers (`sm_refresh_hint` after batch/heal)  
- [x] Update-only mode (newer version than installed) — `--update-only`  
- [x] Parity checks vs SM connected metadata — `--sm-parity`  
- [x] Optional import of existing SM libraries into the manifest — `--import-sm-manifest`

## Crash recovery (next)

- [x] HTTP **Range** resume for `*.safetensors.partial` (`download_resume` / `--no-resume-partials`)  
- [x] Start sweep keeps weight partials; purges preview temps (`partial_purged`)  
- [x] Post-download BLAKE3 verify against CivitAI file hash  
- [x] Safe restart mid-catalog without re-downloading verified files (stream + skip/index)

## QoL forever

- [x] Preflight / free-disk floor (`--disk-floor-gib`, soft size warn, exit 5)  
- [x] Byte-level download progress (CLI + `download_progress` events)  
- [x] `failed.jsonl` linked to control-plane `eventId`  
- [x] Bandwidth / rate limits (`DOWNLOAD_RATE_LIMIT_MBS` / `--download-rate-limit`)  
- [x] Listing cache (opt-in page blobs; `--use-listing-cache` / `--refresh-listing`)  
- [x] Better Windows path UX (Browse/UI path separators)  
- [ ] Translations (deferred post-1.0)

---

Ideas welcome via GitHub Issues.  
Support: [Ko-fi](https://ko-fi.com/arturowolff) · [Linktree](https://linktr.ee/ArturoWolff)
