# Latest-only version prune — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep only the latest matching version per `ModelId` on disk: prune older local stems after skip-latest or successful verify; default on, `--keep-old-versions` to disable.

**Architecture:** Small pure module (`version_prune.py`) finds/deletes stem bundles by `ModelId`/`VersionId`; `process_one` calls it after `skip_hash` / `skip_version` / `ok`. Local-first in `anima-lora-batch/`, then promote to `civitmatrix/`.

**Tech Stack:** Python 3.10+, pathlib, existing `.cm-info.json` + `preview_media.find_preview_path`, unittest.

## Global Constraints

- Local-first: prove in `../anima-lora-batch/` then promote to `civitmatrix/`.
- Delete only after keep is known good (skip latest or verify ok); never on verify_fail / forbidden / cancel.
- Never delete stems missing usable `ModelId` or with corrupt JSON.
- Default prune **on**; `--keep-old-versions` disables.
- Index updates under `_index_lock`.

---

### Task 1: `version_prune` module + unit tests (anima)

**Files:**
- Create: `/run/media/arturo/Datos2/Models/anima-lora-batch/version_prune.py`
- Create: `/run/media/arturo/Datos2/Models/anima-lora-batch/test_version_prune.py`

**Interfaces:**
- Produces:
  - `iter_model_sidecars(out_dir: Path) -> Iterator[tuple[Path, dict]]`
  - `find_prune_candidates(out_dir: Path, model_id: int, keep_version_id: int) -> list[dict]` each dict: `stem`, `versionId`, `blake3` (optional str), `infoPath`
  - `delete_stem_bundle(out_dir: Path, stem: str) -> list[Path]` paths removed
  - `prune_old_versions(out_dir, model_id, keep_version_id, *, local_blake3, local_versions, local_stems, index_lock) -> list[dict]` pruned candidate dicts; updates sets under lock after successful deletes

- [ ] **Step 1: Write failing tests** in `test_version_prune.py` (temp dir with two stems same ModelId)

```python
# keep_version=200 → deletes stem "old" (vid 100), keeps "new" (vid 200)
# missing ModelId → not a candidate
# keep_old path tested later via flag in wiring (module always prunes when called)
```

- [ ] **Step 2: Run** `cd anima-lora-batch && .venv/bin/python -m unittest test_version_prune -v` → FAIL import/missing

- [ ] **Step 3: Implement** `version_prune.py` using `find_preview_path` for previews; delete `.safetensors`, `.cm-info.json`, all `{stem}.preview.*` (and `.partial` / `.preview.download` for that stem)

- [ ] **Step 4: Re-run tests** → PASS

---

### Task 2: Wire prune into anima `process_one` + CLI

**Files:**
- Modify: `/run/media/arturo/Datos2/Models/anima-lora-batch/download_anima_loras.py`

**Interfaces:**
- Consumes: `prune_old_versions(...)`
- `process_one(..., keep_old_versions: bool = False)`
- After `skip_hash` / `skip_version` returns (refactor early returns so prune runs **outside** failed outcomes but **on** those skips), and after successful `ok` path before return `"ok"`: if not `keep_old_versions` and not `dry_run`, call prune for `model["id"]` / `version_id`; log + `job.emit("prune_old_version", ...)`; `job.bump` count `pruned` if JobState supports counts (use existing count mechanism).

- [ ] **Step 1:** Add `--keep-old-versions` flag; pass through to workers/`process_one`
- [ ] **Step 2:** On skip_hash/skip_version/ok success paths, invoke prune
- [ ] **Step 3:** Smoke: `py_compile` + unittest

---

### Task 3: Promote to civitmatrix + GUIDE + push

**Files:**
- Create: `civitmatrix/src/civitmatrix/version_prune.py` (same logic)
- Create: `civitmatrix/tests/test_version_prune.py`
- Modify: `civitmatrix/src/civitmatrix/downloader.py`, `cli.py`
- Modify: `civitmatrix/docs/GUIDE.md` (latest-only + `--keep-old-versions`)
- Commit plan+spec already on main; commit impl; push

- [ ] **Step 1:** Copy module + tests; wire `keep_old_versions` like anima
- [ ] **Step 2:** GUIDE short note
- [ ] **Step 3:** `unittest` + commit + `git push`

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| Prune after verify ok | 2 |
| Prune on skip_hash/skip_version | 2 |
| No prune on fail | 2 (only call on success paths) |
| ModelId match / skip missing | 1 |
| `--keep-old-versions` | 2, 3 |
| Events + counts | 2 |
| Index update under lock | 1 |
| Local-first → promote → GUIDE → push | 1–3 |
