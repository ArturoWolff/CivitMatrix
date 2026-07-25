#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -e .

mkdir -p logs downloads/Lora

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.example to .env and set CIVITAI_API_KEY" >&2
  echo "  cp .env.example .env" >&2
  exit 1
fi

exec python -m civitmatrix "$@"
