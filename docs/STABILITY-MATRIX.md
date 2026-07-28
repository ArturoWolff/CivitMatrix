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

On startup CivitMatrix scans the output directory for:

- existing `.safetensors` stems  
- BLAKE3 + `VersionId` from `.cm-info.json`  

Already-present hashes/versions are skipped — safe to re-run after interruptions.

## After downloading

1. Open Stability Matrix  
2. Refresh / rebuild the model index (or restart)  
3. Browse CivitAI in SM — matching cards should show green **Installed**

## Future integration (roadmap)

- One-click “open SM models folder” helpers in the GUI  
- Optional post-run index refresh notes  
- Update-only mode using installed version ids  

See [ROADMAP.md](../ROADMAP.md).
