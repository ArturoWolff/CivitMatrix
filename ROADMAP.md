# Roadmap

CivitMatrix starts as a sharp CLI. Here’s where it’s headed.

## v0.1 — shipped

- [x] Cross-platform CLI (`python -m civitmatrix`, `run.sh`, `run.ps1`)
- [x] Stability Matrix–native sidecars (`.cm-info.json` + preview)
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
- [x] `--heal` library consolidate (BLAKE3 by-hash sidecars, orphan purge, bad-weight repair)
- [x] Richer local index log (missingBlake3 / orphanInfo / …)
- [x] HTTP Range resume for interrupted weight downloads
- [x] Post-download BLAKE3 verify (`--skip-verify` to opt out)
- [x] Stream process while listing (bounded in-flight pool; phase `running`)
- [x] Disk floor + soft size warn (`--disk-floor-gib`, runner exit 5)
- [x] Byte download progress (CLI + events)
- [x] `failed.jsonl` ↔ `eventId` on fail events

## v0.2 — Filters

- [ ] Tag include / exclude  
- [ ] Min downloads / likes  
- [ ] Creator allow / deny  
- [ ] True **base-only** (exclude multi-base models)  
- [ ] Date range + NSFW level caps  
- [ ] Filter presets (JSON import/export)

## v0.3 — Categorizing

- [ ] Auto-sort into `characters/` · `styles/` · `concepts/` · `clothes/`  
- [ ] Use `manifest.jsonl` `sortHints` + tag heuristics  
- [ ] Optional SM subfolder layouts without breaking the index  
- [ ] Dry-run move plan before touching files

## v0.4 — GUI

- [ ] Lightweight local **browser** UI (Win95-flavored; no Electron)  
- [ ] Poll `job.json` / `events.jsonl` only (control plane already shipped)  
- [ ] Queue view + progress; wire Start / Cancel / Pause / Resume  
- [ ] Visual filter builder  
- [ ] Folder picker for SM Models paths  
- [ ] Failure browser with one-click retry

## v0.5 — Deeper SM integration

- [ ] Post-run “refresh index” guidance / helpers  
- [ ] Update-only mode (newer version than installed)  
- [ ] Parity checks vs SM connected metadata  
- [ ] Optional import of existing SM libraries into the manifest

## Crash recovery (next)

- [x] HTTP **Range** resume for `*.safetensors.partial` (`download_resume` / `--no-resume-partials`)  
- [x] Start sweep keeps weight partials; purges preview temps (`partial_purged`)  
- [x] Post-download BLAKE3 verify against CivitAI file hash  
- [x] Safe restart mid-catalog without re-downloading verified files (stream + skip/index)

## QoL forever

- [x] Preflight / free-disk floor (`--disk-floor-gib`, soft size warn, exit 5)  
- [x] Byte-level download progress (CLI + `download_progress` events)  
- [x] `failed.jsonl` linked to control-plane `eventId`  
- [ ] Bandwidth / rate limits  
- [x] Listing cache (opt-in page blobs; `--use-listing-cache` / `--refresh-listing`)  
- [ ] Better Windows path UX  
- [ ] Translations

---

Ideas welcome via GitHub Issues.  
Support: [Ko-fi](https://ko-fi.com/arturowolff) · [Linktree](https://linktr.ee/ArturoWolff)
