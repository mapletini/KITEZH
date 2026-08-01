# Display Monitor Setup

This guide runs Kai's non-browser face renderer on a monitor physically attached to the server.

For a headless machine, the recommended path is a tty1 service that opens Kai's terminal face directly.
It avoids browser/snap/XDG session issues entirely while still reading the shared display state.

## Recommended Headless Setup

Install the minimal runtime dependency set:

```bash
sudo apt update
sudo apt install -y x11-xserver-utils
```

Launch manually:

```bash
cd /home/mini/KITEZH
chmod +x scripts/run_display_terminal.sh
./scripts/run_display_terminal.sh
```

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
Environment=KITEZH_DISPLAY_TTY=/dev/tty1
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

The launcher now binds its output directly to `/dev/tty1` when available, so the face
is visible on the attached monitor without a separate systemd override.

Tail the live terminal face output:

```bash
sudo journalctl -u kitezh-face-tty -f
```

## Optional Legacy Browser Mode

If you still want the browser-based monitor, keep `/monitor` and the kiosk docs below.
This path is now optional and should only be used if you specifically need HTML/CSS rendering.

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

## 5) Kai-controlled screen state

Kai (or an admin on LAN) can switch the screen state used by the terminal face:

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

External absolute URLs are blocked by default. To allow them for future screen routing:

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
