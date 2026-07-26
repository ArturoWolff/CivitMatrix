# Engine scale batch — disk guard, byte progress, fail event ids

Date: 2026-07-26  
Status: approved for implementation planning  
Scope: `anima-lora-batch/` first → promote to `civitmatrix/`  
Umbrella for three todo items:

1. Size estimator + free-disk check before/during run  
2. Byte-level download progress (CLI + events)  
3. `failed.jsonl` linked to event ids  

## Goals

- Keep default runs safe on full disks without surprising silent failure.  
- Show live transfer progress in CLI and control plane for future UI.  
- Correlate each failure across `events.jsonl` and `failed.jsonl` with one shared id.  

## Non-goals

- Multi-line TUI / progress bars for concurrent workers  
- Backfilling historical `failed.jsonl` rows  
- Linking success/manifest rows to event ids  
- Separate `--estimate-only` tool  
- Automatic catalog size from full listing when sizes are missing (best-effort only)  
- `--no-progress` flag (v1 always shows progress)  

## Shared conventions

- Control plane remains the source of truth: `logs/job.json` + `logs/events.jsonl`.  
- New modules stay small and stdlib-first (`shutil`, `uuid`, existing `on_event`).  
- Local-first: prove in anima, then promote.  

---

## 1. Size estimator + free-disk check

### Behavior (policy C)

| When | Behavior |
|------|----------|
| Run start | `shutil.disk_usage(out_dir)`; emit `disk_status` with `free`, `total`, `floor`; if `free < floor` → **hard stop** before downloads |
| Soft warn | If a best-effort remaining estimate exists and `estimate > free` but `free >= floor` → emit `disk_warn` / log; continue |
| Mid-run / before weight | Recheck free space; if `free < floor` → emit `disk_full`, stop with runner exit **5** (distinct from user cancel **4**) |
| Estimate | Best-effort from CivitAI file size fields (`sizeKB` / `size` / equivalent) when present; if unknown, skip estimate but still enforce floor |

### Config

- Default floor: **2 GiB**  
- CLI: `--disk-floor-gib` (int/float)  
- Env: `DISK_FLOOR_GIB`  
- Floor `0` disables hard checks (escape hatch); still emit `disk_status` when practical  

### Module

- `disk_guard.py`: `disk_status(path)`, `below_floor(path, floor_bytes)`, `file_size_bytes(file_info) -> int | None`, `format_bytes`

### Events / job meta

- `disk_status` — `{ free, total, floor, path }`  
- `disk_warn` — `{ free, estimate, floor }` when estimate exceeds free  
- `disk_full` — `{ free, floor, path, modelId? }`  
- `job.json`: `diskFloorGib`, optional live `diskFree`  

### Exit codes (runner)

| Code | Meaning |
|------|---------|
| 4 | User cooperative cancel |
| 5 | Disk / preflight failure (below floor) |

Note: `--status` exit **5** means *paused* — different command; document both in GUIDE.

---

## 2. Byte-level download progress

### Behavior (policy B)

- Extend `CivitClient.download` chunk loop (already has `on_event`).  
- Resolve `total` from `Content-Length` when present; with Range resume, prefer `total = resume_offset + remaining` when headers allow; else `total=null` and report bytes so far only.  
- **CLI:** overwrite one progress line ~1–2/s (`\r`); clear/newline on file completion. Show stem, bytes, optional `%`, optional MiB/s.  
- **Events:** `download_progress` every **max(5% of total, 8 MiB)** of new data since last event, and on completion — fields: `modelId?`, `path`, `bytes`, `total`, `pct` (nullable), `speedBps` (optional).  
- Keep existing `download_start` / `download_resume` / `download_done` / fail events.  
- `job.json` `current` may include `bytes`, `total`, `pct` updated on throttled progress so `--status` reflects transfer.  
- Concurrency &gt; 1: **last writer wins** on the single CLI line (prefix short model id when available). No multi-line TUI in v1.  

### Events

- `download_progress` — as above  

---

## 3. `failed.jsonl` ↔ event ids

### Behavior (policy B)

On each failure path that today does `record_failure` + `job.emit("fail", …)`:

1. `eventId = str(uuid.uuid4())`  
2. Emit `fail` **with** `eventId`  
3. Append `failed.jsonl` row **with the same** `eventId`  

Prefer a single helper (e.g. `fail_with_event(...)` or `record_failure(..., event_id=)` paired with emit using the same id) so the two sinks cannot drift.

### Compatibility

- `--retry-failed` selection unchanged (still unique retryable `modelId`s).  
- Old rows without `eventId` remain valid.  
- No backfill.  
- Success/manifest linking: out of scope.  

---

## Implementation shape

| Piece | Responsibility |
|-------|----------------|
| `disk_guard.py` | Disk usage, floor compare, size parse helpers |
| Client `download` | Byte counters + throttled `download_progress` via `on_event` |
| Logging / job helpers | Shared `eventId` on fail emit + failed row |
| CLI | `--disk-floor-gib` |
| Docs | README, GUIDE, ROADMAP; local todo `[x]` when pushed |

Suggested order:

1. Fail `eventId` helper (touches call sites)  
2. Disk guard + start/mid checks  
3. Download byte progress + `job.current` fields  
4. Docs + smoke  

Prove in anima first, then promote to civitmatrix.

## Smoke / acceptance

1. **Disk:** absurdly high floor → abort at start; normal floor → proceed.  
2. **Progress:** real or instrumented download shows `\r` progress and `download_progress` events at thresholds.  
3. **Fail link:** force a known fail; same `eventId` on the `fail` event and the new `failed.jsonl` row.  

## Spec self-review

- Placeholder scan: exit-code conflict with `--status` paused resolved by documenting runner-vs-status namespaces.  
- Progress threshold and floor defaults are explicit.  
- Three todo items covered; non-goals listed.  
- Ambiguity on mid-run exit: pinned to runner exit **5** + `disk_full` (not user cancel 4).  
