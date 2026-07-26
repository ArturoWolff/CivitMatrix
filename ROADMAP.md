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

## QoL forever

- [ ] Preflight size estimator (TB warning)  
- [ ] Bandwidth / rate limits  
- [ ] Parallel listing cache  
- [ ] Better Windows path UX  
- [ ] Translations

---

Ideas welcome via GitHub Issues.  
Support: [Ko-fi](https://ko-fi.com/arturowolff) · [Linktree](https://linktr.ee/ArturoWolff)
