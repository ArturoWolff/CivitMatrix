# Security

## API keys

- Store your CivitAI API key in a local **`.env`** file (gitignored) or in your shell environment.
- **Never** commit `.env`, paste keys into issues, or embed them in scripts that you push.
- If a key is exposed, revoke it in CivitAI account settings and create a new one.

## What this tool sends

CivitMatrix calls your configured CivitAI-compatible HTTP API (`CIVITAI_BASE_URL`) with a Bearer token for **listing and metadata** only. Weight downloads use a separate HTTP session **without** the Authorization header so CDN redirects cannot leak the key.

Listing pagination (`nextPage`) must stay on the same origin as `CIVITAI_BASE_URL`; off-origin URLs are rejected.

It does not phone home to any unrelated service.

## Local Win95 UI (127.0.0.1)

The UI binds **only** to `127.0.0.1`. That is not multi-user authentication.

- On start, the server writes a random token to `logs/.ui-session` (mode `0600`, gitignored).
- Mutating routes require header `X-CivitMatrix-Token`:  
  `POST /api/populate`, `/api/run`, `/api/cancel`, `/api/pause`, `/api/resume`, `/api/retry-failed`, `/api/heal`, `/api/categorize`, `/api/directories`, `/api/browse-dir`, `/api/filter-presets`.
- The page loads the token via `GET /api/session` (same-origin).
- Request bodies larger than **2 MiB** are rejected (413).
- Populate/run **ignore** any client-supplied `baseUrl`; the API host comes from `.env` / saved Directories settings only.
- Directory Save (token-gated) may update `CIVITAI_API_KEY`, `CIVITAI_BASE_URL`, `LORA_DIR`, and `MODELS_ROOT` in `.env`.

On a shared machine, prefer `./run.sh --cli …` and do not leave the UI running unattended.

## Downloads

Files are written under the configured output directory (`LORA_DIR` / `--out` / Directories paths). Partial downloads use a `.partial` suffix. HTTP resume verifies byte ranges and response/final lengths before committing a destination.

Automatic library cleanup is enabled by default: verified clones and proven superseded same-item revisions are removed only with a verified survivor. Distinct releases are not called duplicates, and a larger version ID alone does not prove supersession. Collection characters, alternate variants and unknown/conflicting provenance are preserved. Duplicate deletion verifies actual payload hashes plus author/model/version/file/base/format identity. `--keep-old-versions` opts into archiving, and `--no-library-cleanup` disables automatic model cleanup. Broader orphan/temp removal and damaged-weight replacement remain separately gated by `--allow-deletions`; heal never substitutes an unrelated release file for a recorded file ID.

Kernel-held leases coordinate identical, parent/child and directory-alias scopes among same-user CivitMatrix writers. A private per-user registry coordinates overlapping scopes; output and shared-log directory locks protect job admission. Lock JSON is status information, not ownership proof, and lock files remain after release. Do not remove a lock file while a writer owns it. All writers must use the same registry location (`CIVITMATRIX_LOCK_REGISTRY` can override it for isolated testing).

## Reporting issues

If you find a security-sensitive bug (e.g. path traversal writing outside `--out`, session bypass), open a private report via GitHub Security Advisories on this repository, or contact the author through [Linktree](https://linktr.ee/ArturoWolff).
