#!/usr/bin/env bash
set -euo pipefail

# Start Kai's fullscreen framebuffer face on a directly attached monitor.
# Intended for Linux server environments (kmsdrm/fbcon/x11).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "error: python virtualenv not found at $VENV_PY" >&2
  echo "create it first: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

# Keep this aligned with the main service workspace unless explicitly overridden.
export KITEZH_WORKSPACE="${KITEZH_WORKSPACE:-$ROOT_DIR/workspace}"

# Optional preferred video backend; if omitted, renderer will auto-fallback.
if [[ -n "${KITEZH_DISPLAY_VIDEO_DRIVER:-}" ]]; then
  export KITEZH_DISPLAY_VIDEO_DRIVER
fi

# Optional SDL tuning knobs (set only when provided by caller).
if [[ -n "${KITEZH_SDL_FBDEV:-}" ]]; then
  export SDL_FBDEV="$KITEZH_SDL_FBDEV"
fi
if [[ -n "${KITEZH_SDL_CARD:-}" ]]; then
  export SDL_RENDER_DRIVER="$KITEZH_SDL_CARD"
fi

exec "$VENV_PY" "$ROOT_DIR/main.py" --framebuffer-face
