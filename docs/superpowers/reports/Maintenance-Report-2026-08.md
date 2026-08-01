# Maintenance Report - August 2026

**Run:** 2026-08-01 (schedule: 00:01 CST / cron `1 6 1 * *` → 06:01 UTC on the 1st)  
**Repo baseline:** [ArturoWolff/CivitMatrix](https://github.com/ArturoWolff/CivitMatrix) `main` @ `2518edc` (CivitMatrix **1.0.0**)  
**Scope:** public docs, changelogs, release notes, and APIs for SwarmUI, ComfyUI, Stability Matrix, and CivitAI — models, formats, tags, sidecars, download/auth, and related behavior that could affect CivitMatrix.

No critical breakage of current LoRA/SM download paths was found. This report is documentation-only.

---

## SwarmUI

### Sources

- Releases: [mcmonkeyprojects/SwarmUI/releases](https://github.com/mcmonkeyprojects/SwarmUI/releases)
- Latest tagged release: [0.9.8-Beta](https://github.com/mcmonkeyprojects/SwarmUI/releases/tag/0.9.8-Beta) (2026-02-06); master commits continue through 2026-08-01 (e.g. Mage Flow support)
- Model docs: [docs/Model Support.md](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/docs/Model%20Support.md)
- Video models: [docs/Video Model Support.md](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/docs/Video%20Model%20Support.md)
- ModelSpec: [Stability-AI/ModelSpec](https://github.com/Stability-AI/ModelSpec)

### Notable updates

- **0.9.8-Beta** added first-class support for Flux.2 (Dev / Klein 4B & 9B), Z-Image, Anima, Hunyuan Image 2.1, Hunyuan Video 1.5, Qwen Image Edit Plus, LTX-2 (video+audio), plus broader audio/video as first-class media types.
- Native formats remain `.safetensors` (+ `.sft`, `.engine`, `.gguf`); legacy `.ckpt`/`.pt`/`.pth`/`.bin` still accepted. Quantization paths (GGUF, Nunchaku) are emphasized in docs.
- Sidecar contract unchanged and still documented: Swarm prefers ModelSpec in-file, can import “matrix jsons”, and **falls back to `{stem}.swarm.json`** for other formats. Trigger-phrase UX improved (copy button; optional `UseSecondaryTriggerPhraseSources` to reduce tag spam).
- Metadata edits can now often avoid rewriting the weight file in place — sidecar-only metadata remains the lean path CivitMatrix already uses.

### Relevance to CivitMatrix

- Opt-in `--write-swarm` / `WRITE_SWARM` writing lean `modelspec.*` keys (no `modelspec.thumbnail`) still matches Swarm’s documented sidecar fallback.
- New architectures expand what users may put under SM/Swarm shared trees; CivitMatrix does not need Swarm HTTP APIs for current behavior.

---

## ComfyUI

### Sources

- Releases: [Comfy-Org/ComfyUI/releases](https://github.com/Comfy-Org/ComfyUI/releases) — latest reviewed: [v0.29.2](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.29.2) (2026-07-31), [v0.29.0](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.29.0) (2026-07-29)
- Changelog: [docs.comfy.org/changelog](https://docs.comfy.org/changelog)
- Folder layout (master): [`folder_paths.py`](https://github.com/Comfy-Org/ComfyUI/blob/master/folder_paths.py)

### Notable updates

- Rapid model expansion through mid/late 2026: Anima / Anima TE LoRAs, Flux.2 / Ideogram 4 / Lens / PixelDiT / JoyImageEdit / Gemma4 / int8 and fp8 safetensors handling, SeedVR2, LTXV/Wan LoRA key maps, video/audio nodes, partner API nodes.
- Supported weight extensions still center on `.safetensors` (plus legacy torch formats). **GGUF is not** in Comfy’s default `supported_pt_extensions` set in `folder_paths.py` (GGUF typically needs custom loaders / SM routing elsewhere).
- Canonical diffusion UNet folder is **`models/diffusion_models`**, with legacy `models/unet` still listed as an alias path. Related shared folders include `text_encoders` (alias `clip`), `clip_vision`, `model_patches`, `audio_encoders`, `background_removal`, `detection`, etc.

### Relevance to CivitMatrix

- CivitMatrix has **no direct ComfyUI integration**; overlap is shared weights + SM’s Comfy shared-folder mapping.
- Folder-name drift (`unet` → `diffusion_models`, SM `DiffusionModels`) matters when users download UNet-only / Flux-family checkpoints through CivitMatrix into an SM tree used by Comfy.

---

## Stability Matrix

### Sources

- Releases: [LykosAI/StabilityMatrix/releases](https://github.com/LykosAI/StabilityMatrix/releases) — latest stable reviewed: [v2.16.1](https://github.com/LykosAI/StabilityMatrix/releases/tag/v2.16.1) (2026-06-16), [v2.16.0](https://github.com/LykosAI/StabilityMatrix/releases/tag/v2.16.0) (2026-06-09)
- Changelog: [CHANGELOG.md](https://github.com/LykosAI/StabilityMatrix/blob/main/CHANGELOG.md)
- Connected metadata: [`ConnectedModelInfo.cs`](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Models/ConnectedModelInfo.cs), [`ConnectedModelSource.cs`](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Models/ConnectedModelSource.cs), [`SharedFolderType.cs`](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Models/SharedFolderType.cs)
- Comfy package mapping: [`ComfyUI.cs`](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Models/Packages/ComfyUI.cs)

### Notable updates

- **v2.16.x** centers Inference/Image Lab on modern multi-file stacks (Z-Image, Anima, Flux.2 Klein): automatic text-encoder/VAE pairing, **misplaced-model warnings** with one-click move into the correct shared folder, Anima treated like Z-Image (standalone in **DiffusionModels** + separate TE/VAE).
- Checkpoint classification improvements: UNet-only / Wan / HiDream / Z-Image / Hunyuan3D / Diffusers-format Flux → **DiffusionModels**; **GGUF checkpoints go to DiffusionModels**, not `StableDiffusion`.
- `.cm-info.json` contract remains the Installed/connected-metadata path. `ConnectedModelInfo` still requires `ModelId`/`VersionId`/`Hashes` for Civit; **`SourceUrl`** is now also documented for sources without integer ids (e.g. CivArchive). `ConnectedModelSource` enum: `Civitai`, `OpenModelDb`, `CivArchive`, `Other` (CivitMatrix writes `Source: 0` ≈ Civitai).
- Shared folder rename (historical, still the live layout): **`Unet` → `DiffusionModels`**. Folder reference maps Comfy `unet` → SM `DiffusionModels`.
- CivitAI browser/auth fixes earlier in the 2.15 line (account 401 / API changes); not a change to on-disk cm-info shape.

### Relevance to CivitMatrix

- Writing `.cm-info.json` with BLAKE3 + `SourceUrl` remains the correct Installed contract; SM still indexes by BLAKE3 after refresh.
- Default type→folder map in `directories_config.DEFAULT_TYPE_DIRS` still sends **`UNet` → `UNet/`**, while current SM expects **`DiffusionModels/`**. That is the highest-impact SM layout drift for modern checkpoints.
- Doc drift: [`docs/STABILITY-MATRIX.md`](../../STABILITY-MATRIX.md) still implies `.swarm.json` is always written; code defaults Swarm sidecars **off** unless `--write-swarm`.

---

## CivitAI

### Sources

- Developer site: [developer.civitai.com/site](https://developer.civitai.com/site/)
- Reference: [developer.civitai.com/site/reference](https://developer.civitai.com/site/reference/)
- Auth: [Authentication guide](https://developer.civitai.com/site/guide/authentication)
- Pagination: [Pagination guide](https://developer.civitai.com/site/guide/pagination)
- CLI (download/auth policy): [CLI guide](https://developer.civitai.com/site/guide/cli)
- Education download guide (older wording): [Civitai’s Guide to Downloading via API](https://education.civitai.com/civitais-guide-to-downloading-via-api/)
- Live enums probed 2026-08-01: `GET https://civitai.com/api/v1/enums` and `GET https://civitai.red/api/v1/enums`

### Notable updates / current contract

- Public Site API remains under `/api/v1/` (`models`, `model-versions`, `by-hash`, `tags`, `enums`, download via `/api/download/models/{versionId}`, plus newer surfaces: vault, permissions, collections, articles, `POST /model-versions/by-hash`, mini versions).
- **Auth:** Bearer preferred; `?token=` still supported for download-tool compatibility. Official CLI docs state **every model-file download requires a token** (even small public files → `401`). Region / “green” domain gating can silently SFW-filter listings.
- **Pagination:** Prefer cursors for deep catalogs. **`page * limit` must not exceed 1000** or the API returns **`429`** (“use cursors instead”). Cursor metadata exposes `nextCursor`; `nextPage` may already embed a cursor URL.
- **Live `ModelType` enum** (`.com` and `.red` agree): includes CivitMatrix’s set **plus** `CLIPVision`, `VisionLanguage`, `CLIP` (not in `TYPE_CHOICES`).
- **Active base models** now include many 2026 families CivitMatrix users already filter ad hoc (`Anima`, `Flux.2*`, `ZImageTurbo`/`ZImageBase`, `Pony V7`, `Krea 2`, `Lens`, `MageFlow`, extensive `Wan Video 2.x`, `LTXV*`, etc.). UI/CLI still accept free-text `--base-model`; hardcoded dropdown lists can lag.

### Relevance to CivitMatrix

- Download auth pattern (Bearer on API session; `?token=` only on same-origin download URLs; no Bearer on CDN) still matches current developer guidance and SECURITY.md.
- Client follows `metadata.nextPage` with a fixed 0.35s delay and **does not handle HTTP 429 / Retry-After**. Deep `--type All` / large catalog sweeps can hit the page×limit ceiling unless `nextPage` already switched to cursors.
- Enum/type drift: missing `CLIPVision` / `VisionLanguage` / `CLIP` in CLI/UI type lists; base-model vocabulary has grown far beyond FILTERS examples.
- FILTERS.md still lists sort `Most Buzz`; confirm against live Meilisearch/sort labels when touching sort UX.

---

## Impact on CivitMatrix

| Area | Current CivitMatrix behavior | Upstream status | Impact |
|------|------------------------------|-----------------|--------|
| SM Installed badge | Weight BLAKE3 + `.cm-info.json` (`ModelId`, `VersionId`, `Hashes.BLAKE3`, `SourceUrl`) | Still the SM connected-metadata contract | **Aligned** — no critical break |
| Swarm sidecars | Opt-in lean `.swarm.json` ModelSpec keys | Still documented fallback; Swarm imports matrix jsons | **Aligned** when `--write-swarm` used |
| SM folder `UNet` | `DEFAULT_TYPE_DIRS["UNet"] = "UNet"` | SM folder is **`DiffusionModels`** (Comfy: `diffusion_models`) | **Medium** — UNet-only / Flux-family / GGUF-style checkpoints may land in the wrong SM folder and trigger SM “misplaced model” warnings |
| Always `.safetensors` output name | `pick_primary_file` prefers SafeTensor but dest stem uses `.safetensors` | GGUF / other formats increasingly common; SM routes GGUF → DiffusionModels | **Medium** for non-SafeTensor downloads |
| Civit type enums | `TYPE_CHOICES` lacks `CLIPVision`, `VisionLanguage`, `CLIP` | Present on live `/api/v1/enums` | **Low–medium** — filter/UI incompleteness |
| Deep pagination | Follow `nextPage`; no 429 handling | Docs: page×limit > 1000 → 429; prefer cursors | **Medium** for full-catalog / high-page runs |
| Download auth | Token required path already implemented | CLI: all downloads need token | **Aligned** (users without `CIVITAI_API_KEY` will 401) |
| ComfyUI | No integration | New folders/models only via SM shared tree | **None direct** |
| Docs | STABILITY-MATRIX.md implies Swarm always written | Code default off | **Low** — doc accuracy |

Primary LoRA → `Models/Lora` + cm-info path used by most CivitMatrix workflows remains compatible with SM 2.16.x and SwarmUI 0.9.8+/master.

---

## Suggested follow-ups

1. **SM folder map:** Change default `UNet` output directory from `UNet` → `DiffusionModels` (and document Comfy `diffusion_models` alias). Consider optional migration note for existing `Models/UNet` trees.
2. **Format-aware destinations:** When primary file is GGUF (or other non-SafeTensor), preserve real extension and/or route checkpoints to `DiffusionModels` consistent with SM’s download classification.
3. **Civit enums sync:** Add `CLIPVision`, `VisionLanguage`, and `CLIP` to `TYPE_CHOICES` / UI; optionally refresh base-model suggestions from `/api/v1/enums` (`ActiveBaseModel`) instead of static lists.
4. **Pagination resilience:** Prefer/explicitly use cursor pagination for deep listing; handle HTTP `429` with backoff / switch-to-cursor messaging (official page×limit=1000 rule).
5. **Docs polish:** Align `docs/STABILITY-MATRIX.md` and README with opt-in `--write-swarm`; refresh FILTERS sort/type notes against live API labels.
6. **No app code in this PR** — none of the above is a confirmed production outage for the default LoRA workflow; track as roadmap/issues unless a critical breakage is confirmed in a later run.

---

## Primary link index

| Ecosystem | Primary links |
|-----------|----------------|
| SwarmUI | [Releases](https://github.com/mcmonkeyprojects/SwarmUI/releases) · [0.9.8-Beta](https://github.com/mcmonkeyprojects/SwarmUI/releases/tag/0.9.8-Beta) · [Model Support](https://github.com/mcmonkeyprojects/SwarmUI/blob/master/docs/Model%20Support.md) |
| ComfyUI | [Releases](https://github.com/Comfy-Org/ComfyUI/releases) · [Changelog](https://docs.comfy.org/changelog) · [folder_paths.py](https://github.com/Comfy-Org/ComfyUI/blob/master/folder_paths.py) |
| Stability Matrix | [Releases](https://github.com/LykosAI/StabilityMatrix/releases) · [CHANGELOG](https://github.com/LykosAI/StabilityMatrix/blob/main/CHANGELOG.md) · [ConnectedModelInfo](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Models/ConnectedModelInfo.cs) · [SharedFolderType](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Models/SharedFolderType.cs) |
| CivitAI | [Site API](https://developer.civitai.com/site/) · [Reference](https://developer.civitai.com/site/reference/) · [Auth](https://developer.civitai.com/site/guide/authentication) · [Pagination](https://developer.civitai.com/site/guide/pagination) · [CLI](https://developer.civitai.com/site/guide/cli) |
