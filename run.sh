#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
PY="${ROOT}/.venv/bin/python"
INSTALL_MANIFEST="${ROOT}/.venv/.civitmatrix-install-manifest"
# Editable source changes are immediate; rebuild only when packaging changes or
# the environment is incomplete. Publish the marker only after a successful install.
if ! cmp -s pyproject.toml "$INSTALL_MANIFEST" || \
   ! "$PY" -c 'import civitmatrix, requests, dotenv, blake3' >/dev/null 2>&1; then
  "$PY" -m pip install -q -e .
  cp pyproject.toml "$INSTALL_MANIFEST"
fi

mkdir -p logs downloads/Lora

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and set CIVITAI_API_KEY" >&2
  echo "  cp .env.example .env" >&2
  exit 1
fi

# Default: Win95 UI. Headless: ./run.sh --cli …
if [[ "${1:-}" == "--cli" ]]; then
  shift
  exec "$PY" -m civitmatrix --cli "$@"
fi

# Allow UI flags without forcing CLI mode
if [[ $# -gt 0 ]]; then
  for a in "$@"; do
    case "$a" in
      --ui|--no-open) ;;
      -*)
        exec "$PY" -m civitmatrix --cli "$@"
        ;;
    esac
  done
  exec "$PY" -m civitmatrix --ui "$@"
fi

exec "$PY" -m civitmatrix --ui
