# Stability Matrix Compatibility

CivitMatrix is built so downloads look like they came from Stability Matrix itself.

## How the green Installed badge works

SM maintains a local model index of file **BLAKE3** hashes. When the CivitAI browser card’s file hash is in that set, you see **Installed** (or update-available if an older version is present).

You do **not** need to download through SM’s UI. Dropping the correct weight file into the Models tree and refreshing the index is enough for the badge.

## Folder map

```text
StabilityMatrix/Data/Models/
  Lora/                 ← --type LORA (default)
  StableDiffusion/      ← --type Checkpoint
  DiffusionModels/      ← --type UNet (Comfy: models/diffusion_models)
  ClipVision/           ← --type CLIPVision
  TextEncoders/         ← --type CLIP / TextEncoder
  VLM/                  ← --type VisionLanguage / LLM
  VAE/
  ...
```

Point `--out` / `LORA_DIR` at the matching folder for the type you download.

**UNet → DiffusionModels:** the default type path for API type `UNet` is SM’s `DiffusionModels` folder (ComfyUI `diffusion_models`). Existing trees under `Models/UNet` are **not** moved automatically. If you already saved paths in `logs/directories.json`, those overrides keep the old folder until you reset/edit them in the UI Directories view.

## What CivitMatrix writes

For each model version:

| File | Role |
|------|------|
| `{stem}.safetensors` / `{stem}.gguf` | Weights (hash identity; extension matches the remote primary file) |
| `{stem}.cm-info.json` | Connected metadata (model/version ids, triggers, hashes, tags, `SourceUrl`) |
| `{stem}.swarm.json` | **Opt-in** SwarmUI ModelSpec (`--write-swarm` / `WRITE_SWARM`); Civit link + `modelspec.architecture`; no base64 thumbnails |
| `{stem}.preview.*` | Preview / thumbnail — extension matches content (`.jpeg`, `.png`, `.webp`, `.mp4`, …) |

The `.cm-info.json` shape mirrors SM’s connected-metadata format (`ModelId`, `VersionId`, `Hashes.BLAKE3`, `TrainedWords`, `SourceUrl`, etc.).

## Skip / resume behavior

On startup CivitMatrix scans the output directory **recursively** for:

- existing weight stems (``.safetensors`` / ``.gguf``; basename reserved for naming)
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

## SwarmUI `modelspec.architecture`

New downloads (with `--write-swarm`) set `modelspec.architecture` from Civit `baseModel` + model `type` (e.g. Anima LoRA → `anima/lora`, SDXL → `stable-diffusion-xl-v1-base/lora`). Swarm reads this from `.swarm.json` and uses it to classify the model.

To fix an existing library **without redownloading weights**:

```bash
./run.sh --cli --fix-swarm-architecture --dry-run   # counts only
./run.sh --cli --fix-swarm-architecture             # create/update *.swarm.json
./run.sh --cli --fix-swarm-architecture --out /path/to/Models/Lora
```

Reads local `.cm-info.json` `BaseModel` / `ModelType` only (no API). Overwrites wrong architecture values; creates a lean `.swarm.json` when missing. Never touches `.safetensors` or previews. Unknown bases are skipped.

## Related CLI

See also `--heal` / `--refresh-sidecars` in [GUIDE.md](GUIDE.md). Roadmap: [ROADMAP.md](../ROADMAP.md).
