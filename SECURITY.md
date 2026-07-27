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
  `POST /api/populate`, `/api/run`, `/api/cancel`, `/api/pause`, `/api/resume`, `/api/retry-failed`, `/api/directories`, `/api/browse-dir`.
- The page loads the token via `GET /api/session` (same-origin).
- Request bodies larger than **2 MiB** are rejected (413).
- Populate/run **ignore** any client-supplied `baseUrl`; the API host comes from `.env` / saved Directories settings only.
- Directory Save (token-gated) may update `CIVITAI_API_KEY`, `CIVITAI_BASE_URL`, `LORA_DIR`, and `MODELS_ROOT` in `.env`.

On a shared machine, prefer `./run.sh --cli …` and do not leave the UI running unattended.

## Downloads

Files are written under the configured output directory (`LORA_DIR` / `--out` / Directories paths). Partial downloads use a `.partial` suffix. Latest-only mode may delete older local versions of the same `ModelId` under that folder.

## Reporting issues

If you find a security-sensitive bug (e.g. path traversal writing outside `--out`, session bypass), open a private report via GitHub Security Advisories on this repository, or contact the author through [Linktree](https://linktr.ee/ArturoWolff).
