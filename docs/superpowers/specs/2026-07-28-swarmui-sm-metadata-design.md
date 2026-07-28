# SwarmUI + Stability Matrix metadata sidecars

**Date:** 2026-07-28  
**Status:** Implemented 2026-07-28  
**Repo:** CivitMatrix

## Problem

SwarmUI shows a Civit model page link when LoRAs have Swarm-compatible metadata (local examples use `.swarm.json` with a URL inside `modelspec.description`). CivitMatrix currently writes Stability Matrix `.cm-info.json` with `SourceUrl: null` and does not write `.swarm.json`, so fresh downloads lack that link in SwarmUI.

## Goals

- On every successful model install (download + heal repair), write metadata that makes **both** Stability Matrix and SwarmUI happy.
- Expose a clickable Civit page URL derived from `ModelId` + `VersionId`.
- Keep disk lean: **never** embed base64 thumbnails; keep preview as a separate image file (existing `.preview.*` behavior).
- Use `CIVITAI_BASE_URL` (e.g. `https://civitai.red`) as the link host.

## Non-goals (this pass)

- Backfilling ~11k existing LoRAs (optional follow-up: heal/scan pass).
- Writing ModelSpec into safetensors headers.
- Changing preview download behavior beyond “no base64 in JSON”.

## URL format

```text
{base}/models/{modelId}?modelVersionId={versionId}
```

Example: `https://civitai.red/models/1681403?modelVersionId=2920941`

Skip URL fields when `modelId` or `versionId` is missing (private/local models).

## Artifact 1: `.cm-info.json` (Stability Matrix)

Keep existing payload from `build_cm_info`. Changes:

- Set `SourceUrl` to the URL above when ids exist (today it is always `null`).
- Do not add base64 fields.

## Artifact 2: `{stem}.swarm.json` (SwarmUI)

Write beside `{stem}.safetensors`, matching existing Swarm sidecars in the user’s Lora folder:

| Key | Value |
|-----|--------|
| `modelspec.title` | `"{ModelName} - {VersionName}"` (omit empty version suffix) |
| `modelspec.description` | `From <a href="{url}" target="_blank">{url}</a>\n` + model/version description text (may include HTML from Civit; no thumbnail data URIs) |
| `modelspec.date` | Version `publishedAt` if present, else import time (ISO-8601) |
| `modelspec.author` | Creator username |
| `modelspec.trigger_phrase` | `trainedWords` joined with `, ` |
| `modelspec.tags` | Tags joined with `, ` |

**Explicitly omit** `modelspec.thumbnail` (and any other base64 / data-URI fields).

## When to write

1. **Download success** in `process_one` after verified weight + `.cm-info.json` write.
2. **Heal repair / redownload** whenever cm-info is rewritten with known ids.

Failure policy: if `.swarm.json` write fails after a verified weight, **keep the weight** (same rule as cm-info sidecar failures). Log a warning; do not delete the model.

## One-shot cleanup (optional, small)

Strip `modelspec.thumbnail` from any existing `*.swarm.json` under the configured Lora/out dir (currently ~4 files) to reclaim space. Do not touch `.safetensors` or previews.

## Tests

- Unit: URL builder from base + modelId + versionId.
- Unit: `build_swarm_json` / cm-info `SourceUrl` populated; no `thumbnail` / `data:image` keys.
- Unit: missing ids → no SourceUrl / no swarm file required (or empty skip).

## Success criteria

- Fresh CivitMatrix download of a public Civit LoRA produces:
  - `.safetensors` + `.cm-info.json` with non-null `SourceUrl`
  - `.swarm.json` with the same URL in `modelspec.description`
  - preview file only as separate media (if available)
- SwarmUI can open the model page from that metadata; SM still sees Installed via existing hash/version fields.
