# Optional Swarm + Heal Sidecar Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or implement inline. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `.swarm.json` opt-in (`--write-swarm`, default off) and add `--heal --refresh-sidecars` to re-fetch and rewrite SM/Swarm metadata for existing installs; expose both in the GUI.

**Architecture:** Thread `write_swarm: bool` through download + heal write helpers. When `refresh_sidecars` is set, heal no longer skips complete pairs with ModelId/VersionId — it re-fetches and rewrites `.cm-info.json`, and writes `.swarm.json` only if `write_swarm`. GUI checkboxes map to these flags; add `/api/heal` spawn.

**Tech Stack:** Python CLI/heal/download, existing UI (index.html / app.js / server.py), unittest.

## Global Constraints

- Default: do **not** write `.swarm.json` unless `--write-swarm` / GUI toggle / `WRITE_SWARM=1`.
- `--heal --refresh-sidecars`: API re-fetch; always rewrite `.cm-info.json` with `SourceUrl` when ids exist.
- With Swarm off during refresh: do **not** delete existing `.swarm.json`.
- Sidecar write failure after verified weight: keep the weight.
- No ModelSpec in safetensors headers; no base64 thumbnails in JSON.

---

## File map

| File | Change |
|------|--------|
| `heal_library.py` | `write_swarm` on `_write_cm_and_swarm`; `refresh_sidecars` branch |
| `download_one.py` | Gate swarm write on `write_swarm` |
| `downloader.py` | Pass flags through `run_batch` / `run_heal` |
| `cli.py` | `--write-swarm`, `--refresh-sidecars` |
| `ui/server.py` | Pass flags on run/retry; add `/api/heal` |
| `ui/static/index.html` + `app.js` | Checkboxes + Heal button |
| `tests/` | Gate + refresh eligibility tests |
| `docs/GUIDE.md` | Short note + example commands |

---

### Task 1: Gate Swarm writes (`write_swarm`)

**Files:** `heal_library.py`, `download_one.py`, `downloader.py`, `cli.py`, `tests/test_write_swarm_gate.py`

- [ ] Add `write_swarm: bool = False` to `_write_cm_and_swarm`; only write swarm when True.
- [ ] Add `write_swarm` to `process_one`; only write swarm when True (currently always writes — change that).
- [ ] Thread through `run_batch` → `process_one` and `run_heal` → `heal_library` → redownload/repair.
- [ ] CLI: `--write-swarm` / `--no-write-swarm` (default off; env `WRITE_SWARM`).
- [ ] Tests: False → no `.swarm.json`; True → file created.
- [ ] Commit: `feat: make .swarm.json writes opt-in via --write-swarm`

### Task 2: `--refresh-sidecars` on heal

**Files:** `heal_library.py`, `downloader.py`, `cli.py`, tests

- [ ] `heal_library(..., refresh_sidecars: bool = False)`.
- [ ] When complete and not refresh → `heal_ok` (unchanged).
- [ ] When complete and refresh and has ModelId+VersionId → fetch version/model, rewrite sidecars, `heal_sidecars_refreshed`.
- [ ] Incomplete path still repairs; respects `write_swarm`.
- [ ] CLI: `--refresh-sidecars` (only with heal; ignored otherwise or harmless).
- [ ] Tests for refresh rewriting SourceUrl / optional swarm.
- [ ] Commit: `feat: heal --refresh-sidecars re-fetches and rewrites metadata`

### Task 3: GUI toggles + heal spawn

**Files:** `ui/static/index.html`, `ui/static/app.js`, `ui/server.py`

- [ ] Checkbox **Write SwarmUI .swarm.json** (default off) on Main near Start; include in run + retry-failed argv.
- [ ] Logs: **Refresh sidecars on heal** + **Heal library** button; POST `/api/heal` with `{refreshSidecars, writeSwarm, …}`.
- [ ] `_start_run` / retry append `--write-swarm` when set; heal spawn `--cli --heal [--refresh-sidecars] [--write-swarm] --out …`.
- [ ] Commit: `feat: GUI toggles for Swarm writes and heal sidecar refresh`

### Task 4: Docs + verify

- [ ] GUIDE + brief ROADMAP/README if needed.
- [ ] Run related unit tests.
- [ ] Mark spec Implemented; commit.

## Spec coverage

| Spec item | Task |
|-----------|------|
| write_swarm default off | 1 |
| refresh-sidecars API rewrite | 2 |
| GUI both toggles | 3 |
| Don’t delete swarm when off | 1–2 |
| Keep weight on sidecar fail | existing |

## User commands after ship

```bash
./run.sh --cli --heal --refresh-sidecars --write-swarm
./run.sh --cli --base-model Anima --type LORA --sort "Highest Rated" --write-swarm
```
