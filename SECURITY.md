# Security

## API keys

- Store your CivitAI API key in a local **`.env`** file (gitignored) or in your shell environment.
- **Never** commit `.env`, paste keys into issues, or embed them in scripts that you push.
- If a key is exposed, revoke it in CivitAI account settings and create a new one.

## What this tool sends

CivitMatrix calls CivitAI’s HTTP API with your Bearer token to list and download models you request. It does not phone home to any other service.

## Downloads

Files are written only under the configured output directory (`LORA_DIR` / `--out`). Partial downloads use a `.partial` suffix and are removed on hard failures.

## Reporting issues

If you find a security-sensitive bug (e.g. path traversal writing outside `--out`), open a private report via GitHub Security Advisories on this repository, or contact the author through [Linktree](https://linktr.ee/ArturoWolff).
