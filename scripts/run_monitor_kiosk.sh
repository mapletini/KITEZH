#!/usr/bin/env bash
set -euo pipefail

# Headless-friendly monitor kiosk launcher.
# Starts a minimal Xorg session on vt1 and opens Kai's monitor stage in Chromium.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WEB_PORT="${KITEZH_WEB_PORT:-7860}"
MONITOR_URL="${KITEZH_MONITOR_URL:-http://127.0.0.1:${WEB_PORT}/monitor}"

if command -v chromium-browser >/dev/null 2>&1; then
  BROWSER_BIN="chromium-browser"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER_BIN="chromium"
elif command -v google-chrome >/dev/null 2>&1; then
  BROWSER_BIN="google-chrome"
else
  echo "error: no Chromium-compatible browser found (chromium-browser/chromium/google-chrome)" >&2
  exit 1
fi

if ! command -v xinit >/dev/null 2>&1; then
  echo "error: xinit not found. install: sudo apt install xinit xserver-xorg" >&2
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
