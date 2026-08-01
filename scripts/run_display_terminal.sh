#!/usr/bin/env bash
set -euo pipefail

# Start Kai's terminal face renderer (no browser, no SDL/pygame required).
# Recommended display path for headless Linux servers with a monitor on tty1.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
DISPLAY_TTY="${KITEZH_DISPLAY_TTY:-}"

# Under X11, keep output on the pseudo-terminal (e.g. xterm) unless explicitly requested.
if [[ -z "$DISPLAY_TTY" && -z "${DISPLAY:-}" && -w /dev/tty1 ]]; then
  DISPLAY_TTY=/dev/tty1
fi

if [[ -n "$DISPLAY_TTY" ]]; then
  if [[ -w "$DISPLAY_TTY" ]]; then
    exec >"$DISPLAY_TTY" 2>"$DISPLAY_TTY"
  else
    echo "warning: KITEZH_DISPLAY_TTY is set but not writable: $DISPLAY_TTY" >&2
  fi
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "error: python virtualenv not found at $VENV_PY" >&2
  echo "create it first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

export KITEZH_WORKSPACE="${KITEZH_WORKSPACE:-$ROOT_DIR/workspace}"

exec "$VENV_PY" "$ROOT_DIR/main.py" --terminal-face
