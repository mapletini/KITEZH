#!/usr/bin/env bash
set -euo pipefail

# Start Kai's terminal face renderer (no SDL/pygame required).
# Best fallback for headless Linux servers with a monitor on tty1.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "error: python virtualenv not found at $VENV_PY" >&2
  echo "create it first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export KITEZH_WORKSPACE="${KITEZH_WORKSPACE:-$ROOT_DIR/workspace}"

exec "$VENV_PY" "$ROOT_DIR/main.py" --terminal-face
