# Display Monitor Setup

This guide runs Kai's local face renderer on a monitor physically attached to the server.

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

## 5) Troubleshooting

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
