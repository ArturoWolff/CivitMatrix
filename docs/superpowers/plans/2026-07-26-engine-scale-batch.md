# Engine Scale Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or implement inline. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship disk floor checks (exit 5), throttled byte download progress, and shared `eventId` on fail/failed.jsonl.

**Architecture:** Small `disk_guard` + download progress helpers; extend `client.download` and fail paths; wire CLI `--disk-floor-gib`. Prove in anima, promote to civitmatrix, docs, push.

**Tech Stack:** Python 3.10+, stdlib `shutil`/`uuid`/`time`, existing JobState/`on_event`.

**Spec:** `docs/superpowers/specs/2026-07-26-engine-scale-batch-design.md`

## Global Constraints

- Default disk floor 2 GiB; `0` disables hard checks; CLI `--disk-floor-gib` / env `DISK_FLOOR_GIB`
- Runner exit 5 = disk/preflight; exit 4 = user cancel; `--status` exit 5 = paused (document)
- Progress events every max(5% of total, 8 MiB); CLI ~1–2/s `\r`
- Same `eventId` on `fail`/`verify_fail` events and `failed.jsonl` rows
- Anima first then civitmatrix; commit + push when done

---

### Task 1: disk_guard + unit tests (both trees)

- Create `disk_guard.py` with `GiB`, `floor_bytes_from_gib`, `disk_status`, `below_floor`, `file_size_bytes`, `format_bytes`
- Tests for size parse + floor compare

### Task 2: download progress in client.download

- Track bytes; resolve total from Content-Length / Range; emit `download_progress` on threshold; optional CLI `\r` via helper

### Task 3: eventId fail helper + call sites

- `record_failure(..., event_id=)` + helper that emits + records with one id
- Update all fail/verify_fail paths

### Task 4: Wire run_batch / anima main

- Start disk_status; hard stop exit 5; mid-run before weight; `--disk-floor-gib`
- Progress → job.current bytes/total/pct

### Task 5: Docs + smoke + commit + push

- README/GUIDE/ROADMAP/todo; smoke floor abort; push
