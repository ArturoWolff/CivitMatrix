# Heal refresh sidecars + optional Swarm writes

**Date:** 2026-07-28  
**Status:** Approved in chat; awaiting user review of this written spec  
**Repo:** CivitMatrix

## Problem

Fresh downloads can write Stability Matrix `.cm-info.json` with `SourceUrl` and optional SwarmUI `.swarm.json`. Existing complete installs (~11k) still have `SourceUrl: null` and almost no `.swarm.json`, and today’s `--heal` skips them as “ok”. Users who do not use SwarmUI should not get `.swarm.json` files at all.

## Goals

- Opt-in **sidecar refresh** on heal: re-fetch model/version from the API and rewrite `.cm-info.json` (always set `SourceUrl` when ids exist).
- Opt-in **Swarm writes** for both heal refresh and new downloads; default **off**.
- GUI toggles for both options so non-CLI users can choose without flags.
- After refresh, a normal catalog run still downloads missing weights (e.g. the deleted ~3.3k) and writes sidecars according to the same Swarm flag.

## Non-goals

- Deleting existing `.swarm.json` when Swarm is disabled (no bulk cleanup unless a later explicit strip command).
- Writing ModelSpec into safetensors headers.
- Changing preview download behavior beyond existing rules (no base64 thumbnails in JSON).

## CLI

| Flag | Default | Meaning |
|------|---------|---------|
| `--write-swarm` / `--no-write-swarm` | off (`WRITE_SWARM` env if set) | When on, write `{stem}.swarm.json` on successful download and on heal paths that rewrite sidecars |
| `--heal --refresh-sidecars` | refresh off | With heal: for each stem that has a weight + cm-info with `ModelId`/`VersionId`, re-fetch from API and rewrite cm-info; write swarm only if `--write-swarm` |

Plain `--heal` (no `--refresh-sidecars`) keeps current incomplete-only repair behavior. When that path rewrites sidecars, it also respects `--write-swarm`.

## Download path

In `process_one`, after verified weight + `.cm-info.json`:

- Always build cm-info with `SourceUrl` via `base_url` (unchanged).
- Call `build_swarm_json` and write `.swarm.json` **only if** write-swarm is enabled.
- Sidecar write failures after verify still keep the weight.

## Heal path

### Without `--refresh-sidecars`

Unchanged skip for complete pairs (`heal_ok`). Incomplete / bad-weight / orphan flows unchanged, except sidecar writes use `_write_cm_and_swarm` with a `write_swarm: bool` gate.

### With `--refresh-sidecars`

For stems with weight + cm-info containing `ModelId` and `VersionId` (and preferably BLAKE3):

1. Re-fetch version (and model when possible) from the API.
2. Verify local BLAKE3 still matches when remote hash is present (same safety as current heal); on hard mismatch follow existing heal mismatch handling.
3. Rewrite `.cm-info.json` with `SourceUrl`.
4. If write-swarm: rewrite `.swarm.json`; if not: leave any existing `.swarm.json` untouched.
5. Count/log distinctly (e.g. `heal_sidecars_refreshed`) so status is clear.
6. Keep polite delay between API calls; honor cancel/pause.

Stems missing ids remain unresolved as today.

## GUI

Add two controls (defaults off), passed into job spawn / CLI args like other options:

1. **Write SwarmUI `.swarm.json` sidecars** → `--write-swarm`
2. **Refresh sidecars on heal** → `--refresh-sidecars` (only meaningful with Heal)

Heal button / heal job must include these flags when set. Catalog/download jobs include `--write-swarm` when set.

## Wiring

- Thread `write_swarm: bool` through `process_one`, `run_batch`, `heal_library`, `_write_cm_and_swarm`, and UI server argv builder.
- Persist toggle defaults in UI session/directories config only if the project already persists similar checkboxes; otherwise session/job manifest is enough for this pass.

## Example run (user workflow)

```bash
./run.sh --cli --heal --refresh-sidecars --write-swarm
./run.sh --cli --base-model Anima --type LORA --sort "Highest Rated" --write-swarm
```

## Tests

- Unit: `_write_cm_and_swarm` / download write path skips `.swarm.json` when `write_swarm=False`; writes when `True`.
- Unit: refresh eligibility — complete + null SourceUrl or missing swarm still refreshes when flag set; plain heal still skips complete.
- Unit: with `write_swarm=False`, refresh does not create swarm file; existing swarm file left in place.
- Smoke: CLI help lists `--write-swarm` and `--refresh-sidecars`.

## Success criteria

- With Swarm off, new downloads never create `.swarm.json`.
- With `--heal --refresh-sidecars --write-swarm`, existing installs get non-null `SourceUrl` and `.swarm.json` after API refresh (ids present).
- Subsequent catalog run downloads missing weights and writes sidecars per the same Swarm setting.
- Weight never deleted because a sidecar rewrite failed.
