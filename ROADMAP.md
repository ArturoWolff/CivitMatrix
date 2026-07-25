# Roadmap

CivitMatrix starts as a sharp CLI. Here’s where it’s headed.

## v0.1 — shipped

- [x] Cross-platform CLI (`python -m civitmatrix`, `run.sh`, `run.ps1`)
- [x] Stability Matrix–native sidecars (`.cm-info.json` + preview)
- [x] BLAKE3 / version skip + resume
- [x] `failed.jsonl` + `manifest.jsonl` (with `sortHints`)
- [x] Configurable base model, type, sort, NSFW, concurrency
- [x] Showcase defaults for Anima LoRAs

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

- [ ] Local desktop or lightweight web UI  
- [ ] Queue, progress bars, pause / resume  
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
