#!/usr/bin/env bash
set -euo pipefail

# Headless-friendly monitor kiosk launcher.
# Starts a minimal Xorg session on vt1 and opens Kai's monitor stage in Chromium.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# systemd services often have a minimal PATH; include snap/bin for chromium.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:${PATH:-}"

WEB_PORT="${KITEZH_WEB_PORT:-7860}"
MONITOR_URL="${KITEZH_MONITOR_URL:-http://127.0.0.1:${WEB_PORT}/monitor}"

for candidate in \
  "chromium-browser" \
  "chromium" \
  "google-chrome" \
  "/snap/bin/chromium"; do
  if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then
    BROWSER_BIN="$candidate"
    break
  fi
done

if [[ -z "${BROWSER_BIN:-}" ]]; then
  echo "error: no Chromium-compatible browser found (chromium-browser/chromium/google-chrome)" >&2
  exit 1
fi

if ! command -v xinit >/dev/null 2>&1; then
  echo "error: xinit not found. install: sudo apt install xinit xserver-xorg" >&2
  exit 1
fi
if ! command -v Xorg >/dev/null 2>&1 && [[ ! -x "/usr/lib/xorg/Xorg" ]]; then
  echo "error: Xorg server not found. install: sudo apt install xserver-xorg" >&2
  exit 1
fi

XSESSION_SCRIPT="$(mktemp)"
trap 'rm -f "$XSESSION_SCRIPT"' EXIT

cat > "$XSESSION_SCRIPT" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
xset -dpms || true
xset s off || true
xset s noblank || true
while true; do
  exec __BROWSER__ \
    --kiosk \
    --start-fullscreen \
    --incognito \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-infobars \
    --autoplay-policy=no-user-gesture-required \
    "__URL__"
  sleep 1
done
EOF

sed -i "s|__BROWSER__|${BROWSER_BIN}|g" "$XSESSION_SCRIPT"
sed -i "s|__URL__|${MONITOR_URL}|g" "$XSESSION_SCRIPT"
chmod +x "$XSESSION_SCRIPT"

# -keeptty helps service-managed sessions keep VT ownership stable.
exec xinit "$XSESSION_SCRIPT" -- :0 vt1 -keeptty -nolisten tcp
