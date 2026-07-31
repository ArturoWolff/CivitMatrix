# Stability Matrix Compatibility

CivitMatrix is built so downloads look like they came from Stability Matrix itself.

## How the green Installed badge works

SM maintains a local model index of file **BLAKE3** hashes. When the CivitAI browser card’s file hash is in that set, you see **Installed** (or update-available if an older version is present).

You do **not** need to download through SM’s UI. Dropping the correct weight file into the Models tree and refreshing the index is enough for the badge.

## What CivitMatrix writes

For each model version:

| File | Role |
|------|------|
| `{stem}.safetensors` | Weights (hash identity) |
| `{stem}.cm-info.json` | Connected metadata (model/version ids, triggers, hashes, tags, `SourceUrl`) |
| `{stem}.swarm.json` | SwarmUI ModelSpec sidecar (Civit link in description; no base64 thumbnails) |
| `{stem}.preview.*` | Preview / thumbnail — extension matches content (`.jpeg`, `.png`, `.webp`, `.mp4`, …) |

The `.cm-info.json` shape mirrors SM’s connected-metadata format (`ModelId`, `VersionId`, `Hashes.BLAKE3`, `TrainedWords`, `SourceUrl`, etc.).

## Folder map

```text
StabilityMatrix/Data/Models/
  Lora/                 ← --type LORA (default)
  StableDiffusion/      ← --type Checkpoint
  VAE/
  ...
```

Point `--out` / `LORA_DIR` at the matching folder for the type you download.

## Skip / resume behavior

On startup CivitMatrix scans the output directory **recursively** for:

- existing `.safetensors` stems (basename reserved for naming)
- BLAKE3 + `VersionId` from complete pairs (weight + matching `.cm-info.json` in the **same** directory)

Already-present hashes/versions are skipped — including installs under SM category subfolders — safe to re-run after interruptions.

## After downloading

1. Open Stability Matrix  
2. Refresh / rebuild the model index (or restart)  
3. Browse CivitAI in SM — matching cards should show green **Installed**

Successful batch and heal runs log this reminder and emit a control-plane event `sm_refresh_hint` in `logs/events.jsonl`.

## Update-only mode

Re-scan the catalog but only pull **newer** versions of models you already have:

```bash
./run.sh --cli --update-only
./run.sh --cli --update-only --dry-run --limit 50
```

CivitMatrix builds `ModelId → max local VersionId` from recursive complete pairs (weight + `.cm-info.json`). For each listed model:

| Local state | Action |
|-------------|--------|
| ModelId not installed | `skip_not_installed` |
| Remote picked version ≤ local max | `skip_uptodate` |
| Remote picked version newer | download + prune older versions (unless `--keep-old-versions`) |

Job meta includes `updateOnly: true`.

## Parity check

Audit an existing SM library tree without hitting the API:

```bash
./run.sh --cli --sm-parity
./run.sh --cli --sm-parity --out /path/to/Models/Lora
```

Reports missing `SourceUrl`, BLAKE3 mismatches (local file vs recorded), missing required fields, orphan sidecars / weights. Exit **0** if clean, **1** if any issues (summary + sample printed).

## Import SM library → manifest

Seed `logs/manifest.jsonl` from recursive `.cm-info.json` (useful when the folder was filled by SM or another tool):

```bash
./run.sh --cli --import-sm-manifest
```

Rows include `modelId`, `versionId`, `blake3`, `localStem`, and `sortHints` from Tags. Duplicate `versionId`s already in the manifest are skipped.

## Related CLI

See also `--heal` / `--refresh-sidecars` in [GUIDE.md](GUIDE.md). Roadmap: [ROADMAP.md](../ROADMAP.md).
