# Latest-only version prune (replace on update)

Date: 2026-07-26  
Status: approved for implementation planning  
Scope: `anima-lora-batch/` first → promote to `civitmatrix/`

## Goals

- Keep **only the latest matching version** of each model on disk.  
- When a creator publishes a newer version, download + verify it, then **remove older local versions** of the same `ModelId`.  
- On every normal run (default), when a model is processed and we already have the latest, still **prune leftover older locals** for that `ModelId`.  

## Non-goals

- Downloading or retaining historical versions.  
- Startup global sweep that deletes without API/listing context (approach 2 rejected).  
- Pruning before verify succeeds.  
- Deleting stems that lack a usable `ModelId` in `.cm-info.json`.  
- Cross-base-model cleanup beyond whatever version the runner already selects (Anima / `--base-model` pick stays unchanged).  

## Decision summary

| Topic | Choice |
|-------|--------|
| When to delete | Only after the kept version is known good: successful verify (including stale-meta accept), or skip because latest is already present |
| Default | Prune on every run (**on**) |
| Escape hatch | `--keep-old-versions` disables prune |
| Match key | Same `.cm-info.json` `ModelId`; delete stems whose `VersionId` ≠ chosen latest `versionId` |
| Approach | Prune-at-touch (process each model → prune siblings) |

## Behavior

### Version selection (unchanged)

- Anima runner: newest Anima `versionId`.  
- CivitMatrix: existing `pick_matching_version` / `--no-match-base-version` rules.  

### Skip rules (unchanged order)

1. `skip_hash` if chosen file BLAKE3 already local.  
2. `skip_version` if chosen `VersionId` already local.  
3. Else download → verify (retry / stale-meta as today).  

### Prune trigger

After a successful outcome for model `M` with chosen `versionId` `V`:

- Outcomes that prune: `skip_hash`, `skip_version`, `ok` (post-verify).  
- Outcomes that **do not** prune: `verify_fail`, `forbidden`, `cancelled`, download errors, `no_files`, etc.

For every local stem whose `.cm-info.json` has `ModelId == M.id` and `VersionId != V`:

- Delete weight (`.safetensors`), `.cm-info.json`, and preview files for that stem (reuse existing preview path helpers).  
- Remove those blake3 / version / stem entries from the in-memory local index.  
- Emit `prune_old_version` (include `modelId`, removed `versionId`, `localStem`, and aggregate count on the run).  

Do **not** delete the stem that corresponds to the kept version `V`.

### Filename / stem

- Prefer reusing a clean stem for the new file as today (`unique_stem`).  
- Prune is by `ModelId`/`VersionId`, not by filename equality — old `name-v{oldId}` leftovers are removed after the new file is verified (or after skip if latest already present).  

### Concurrency

- Prune under the same index lock used for reservations, or an equivalent critical section, so two workers cannot delete each other’s kept file.  
- Only one worker processes a given model at a time (existing pool semantics); prune is scoped to that model’s `ModelId`.  

### CLI / env

- Default: prune enabled.  
- `--keep-old-versions`: disable prune for that run.  
- Optional env mirror only if the project already mirrors similar flags; otherwise CLI-only is enough.  

### Observability

- `events.jsonl`: one event per removed stem (`prune_old_version`) and/or a summary field on job counts (`pruned`).  
- Human log line when anything is deleted (model id + stem + old version id).  

## Error handling

- If unlink of an old file fails: log warning, continue other deletes; do not fail the successful download/skip.  
- Missing sidecar / missing `ModelId`: leave that stem alone.  
- Corrupt `.cm-info.json`: leave that stem alone.  

## Testing

- Unit tests for “find prune candidates by ModelId/VersionId” and “delete stem bundle” with temp dirs.  
- Case: two stems same `ModelId`, different `VersionId` → after keep `V_new`, only `V_new` remains.  
- Case: `--keep-old-versions` → both remain.  
- Case: verify_fail → old stem untouched.  

## Rollout

1. Implement + prove in `anima-lora-batch/`.  
2. Promote modules/CLI wiring to `civitmatrix/`.  
3. Short GUIDE note: latest-only default + `--keep-old-versions`.  
4. Commit + push `civitmatrix` `main`.  

## Out of scope follow-ups

- Interactive “which version to keep” UI.  
- Pruning by BLAKE3 alone without `ModelId`.  
- Automatic deletion of models removed from the remote catalog.  
