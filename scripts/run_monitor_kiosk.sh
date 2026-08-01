#!/usr/bin/env bash
set -euo pipefail

# Headless-friendly monitor kiosk launcher.
# Starts a minimal Xorg session on vt1 and opens Kai's monitor stage in Chromium.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# systemd services often have a minimal PATH; include snap/bin for chromium.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:${PATH:-}"

WEB_PORT="${KITEZH_WEB_PORT:-7860}"
MONITOR_URL="${KITEZH_MONITOR_URL:-http://127.0.0.1:${WEB_PORT}/monitor}"
LOG_FILE="${KITEZH_MONITOR_LOG:-/tmp/kitezh-monitor-kiosk.log}"
PREFERRED_BROWSER="${KITEZH_MONITOR_BROWSER:-}"

# Ensure a runtime directory exists when launched as a system service.
USER_ID="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${USER_ID}}"
if [[ ! -d "$XDG_RUNTIME_DIR" ]]; then
  mkdir -p "$XDG_RUNTIME_DIR"
  chmod 700 "$XDG_RUNTIME_DIR" || true
fi

for candidate in \
  "$PREFERRED_BROWSER" \
  "/snap/bin/chromium" \
  "chromium-browser" \
  "chromium" \
  "firefox" \
  "google-chrome"; do
  [[ -n "$candidate" ]] || continue
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
set -uo pipefail
if command -v xset >/dev/null 2>&1; then
  xset -dpms || true
  xset s off || true
  xset s noblank || true
fi
unset DBUS_SESSION_BUS_ADDRESS || true
browser_base="$(basename "__BROWSER__")"
while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] launching browser: __BROWSER__ __URL__" >&2
  if [[ "$browser_base" == firefox* ]]; then
    __BROWSER__ --kiosk "__URL__"
  else
    if command -v dbus-run-session >/dev/null 2>&1; then
      dbus-run-session -- __BROWSER__ \
        --kiosk \
        --start-fullscreen \
        --incognito \
        --no-first-run \
        --disable-gpu \
        --no-sandbox \
        --disable-dev-shm-usage \
        --ozone-platform=x11 \
        --disable-session-crashed-bubble \
        --disable-infobars \
        --user-data-dir=/tmp/kitezh-kiosk-profile \
        --autoplay-policy=no-user-gesture-required \
        "__URL__"
    else
      __BROWSER__ \
        --kiosk \
        --start-fullscreen \
        --incognito \
        --no-first-run \
        --disable-gpu \
        --no-sandbox \
        --disable-dev-shm-usage \
        --ozone-platform=x11 \
        --disable-session-crashed-bubble \
        --disable-infobars \
        --user-data-dir=/tmp/kitezh-kiosk-profile \
        --autoplay-policy=no-user-gesture-required \
        "__URL__"
    fi
  fi
  rc=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] browser exited with code ${rc}; retrying..." >&2
  sleep 1
done
EOF

sed -i "s|__BROWSER__|${BROWSER_BIN}|g" "$XSESSION_SCRIPT"
sed -i "s|__URL__|${MONITOR_URL}|g" "$XSESSION_SCRIPT"
chmod +x "$XSESSION_SCRIPT"

# -keeptty helps service-managed sessions keep VT ownership stable.
echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting monitor kiosk for ${MONITOR_URL}" | tee -a "$LOG_FILE"
while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] launching xinit" | tee -a "$LOG_FILE"
  xinit "$XSESSION_SCRIPT" -- :0 vt1 -keeptty -nolisten tcp >> "$LOG_FILE" 2>&1 || true
  rc=$?
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] xinit exited with code ${rc}; restarting..." | tee -a "$LOG_FILE"
  sleep 2
done
