# Display Monitor Setup

This guide runs Kai's local face renderer on a monitor physically attached to the server.

For full-screen browser kiosk mode, use `/monitor`. That route follows Kai's live
scene selection from display state and can switch between face/UI/custom URL.

## Headless Server Recommendation (NVIDIA-friendly)

When the machine is "headless" (no desktop session), the most reliable monitor path is
an Xorg kiosk process that opens `/monitor` on vt1.

Install runtime packages:

```bash
sudo apt update
sudo apt install -y xinit xserver-xorg chromium-browser x11-xserver-utils xdg-utils dbus-x11
```

If Chromium is installed as a snap and exits with a cgroup error like
`not a snap cgroup for tag snap.chromium.chromium`, use a non-snap browser for kiosk mode:

```bash
sudo apt install -y epiphany-browser
```

Launch manually:

```bash
cd /home/mini/KITEZH
chmod +x scripts/run_monitor_kiosk.sh
./scripts/run_monitor_kiosk.sh
```

Create `/etc/systemd/system/kitezh-monitor-kiosk.service`:

```ini
[Unit]
Description=Kitezh Monitor Kiosk (Xorg + Chromium)
After=network.target kitezh.service
Requires=kitezh.service

[Service]
Type=simple
User=mini
Group=mini
WorkingDirectory=/home/mini/KITEZH
Environment=KITEZH_WORKSPACE=/home/mini/KITEZH/workspace
Environment=KITEZH_MONITOR_URL=http://127.0.0.1:7860/monitor
Environment=KITEZH_MONITOR_BROWSER=epiphany-browser
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
Environment=KITEZH_MONITOR_LOG=/tmp/kitezh-monitor-kiosk.log
ExecStart=/home/mini/KITEZH/scripts/run_monitor_kiosk.sh
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kitezh-monitor-kiosk
sudo systemctl status kitezh-monitor-kiosk --no-pager
```

Tail kiosk-specific logs:

```bash
tail -f /tmp/kitezh-monitor-kiosk.log
```

Verify the active service environment:

```bash
systemctl show kitezh-monitor-kiosk -p Environment
```

## 1) Install face dependency

```bash
cd /home/mini/KITEZH
. .venv/bin/activate
pip install pygame
```

## 2) Manual smoke test

```bash
cd /home/mini/KITEZH
chmod +x scripts/run_display_face.sh
./scripts/run_display_face.sh
```

Expected behavior:
- fullscreen face appears on the attached display
- it reacts as `workspace/kai_display_state.json` changes
- `Esc` exits

The renderer now auto-tries several SDL backends (`fbcon`, `kmsdrm`, `x11`, `wayland`) if one is unavailable.

If the screen stays black, try a different SDL backend:

```bash
KITEZH_DISPLAY_VIDEO_DRIVER=fbcon ./scripts/run_display_face.sh
```

For desktop sessions:

```bash
KITEZH_DISPLAY_VIDEO_DRIVER=x11 ./scripts/run_display_face.sh
```

If every SDL backend reports `not available`, use the no-SDL terminal renderer:

```bash
chmod +x scripts/run_display_terminal.sh
./scripts/run_display_terminal.sh
```

## 3) Run at boot (separate service)

Keep web service and monitor face in separate systemd units.

Create `/etc/systemd/system/kitezh-face.service`:

```ini
[Unit]
Description=Kitezh Monitor Face
After=network.target kitezh.service
Requires=kitezh.service

[Service]
Type=simple
User=mini
Group=mini
WorkingDirectory=/home/mini/KITEZH
Environment=KITEZH_WORKSPACE=/home/mini/KITEZH/workspace
# Optional: pin a preferred backend; omit to let auto-fallback choose.
# Environment=KITEZH_DISPLAY_VIDEO_DRIVER=fbcon
ExecStart=/home/mini/KITEZH/scripts/run_display_face.sh
Restart=always
RestartSec=3

# Prevent FD starvation under long uptimes.
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kitezh-face
sudo systemctl status kitezh-face --no-pager
```

### SDL-free fallback service (recommended on minimal server installs)

Create `/etc/systemd/system/kitezh-face-tty.service`:

```ini
[Unit]
Description=Kitezh Terminal Face (TTY)
After=network.target kitezh.service
Requires=kitezh.service

[Service]
Type=simple
User=mini
Group=mini
WorkingDirectory=/home/mini/KITEZH
Environment=KITEZH_WORKSPACE=/home/mini/KITEZH/workspace
ExecStart=/home/mini/KITEZH/scripts/run_display_terminal.sh
Restart=always
RestartSec=2
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kitezh-face-tty
sudo systemctl status kitezh-face-tty --no-pager
```

For rendering on the physical monitor console, run it on `tty1`:

```bash
sudo systemctl edit kitezh-face-tty
```

Add:

```ini
[Service]
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
```

## 4) Verify display state feed

The face process reads:
- `workspace/kai_display_state.json`

Quick inspect:

```bash
jq '.version, .mode, .emotion.label, .emotion.intensity, .narrative' /home/mini/KITEZH/workspace/kai_display_state.json
```

## 5) Kai-controlled monitor scenes

Kai (or an admin on LAN) can switch the active monitor scene:

```bash
# Face scene
curl -sS -X POST http://127.0.0.1:7860/api/kai/display/scene \
  -H 'Content-Type: application/json' \
  -d '{"mode":"face"}' | jq

# Kai editable workspace UI scene
curl -sS -X POST http://127.0.0.1:7860/api/kai/display/scene \
  -H 'Content-Type: application/json' \
  -d '{"mode":"ui"}' | jq

# Local path scene
curl -sS -X POST http://127.0.0.1:7860/api/kai/display/scene \
  -H 'Content-Type: application/json' \
  -d '{"mode":"url","url":"/face"}' | jq
```

External absolute URLs are blocked by default. To allow them:

```env
KITEZH_DISPLAY_ALLOW_EXTERNAL_URLS=1
```

## 6) Troubleshooting

- `pygame is not installed`
  - install with `pip install pygame` inside Kitezh venv

- `No available video device`
  - switch `KITEZH_DISPLAY_VIDEO_DRIVER` between `kmsdrm`, `fbcon`, and `x11`
  - if all fail, use `scripts/run_display_terminal.sh` (no SDL required)

- Face runs but does not update
  - confirm `kitezh.service` is publishing new state (`version` increases)

- Permission errors
  - ensure service user `mini` can read/write `/home/mini/KITEZH/workspace`

- SSH sessions with no local monitor
  - expected behavior; framebuffer face needs a real attached display
