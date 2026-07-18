"""
skills/tapo_discovery.py — Tapo camera autodiscovery and registry for K.A.I.

Scans the configured LAN subnet for Tapo cameras, authenticates with each
using the pytapo local API, and persists a JSON registry file in the workspace
so KAI remembers her cameras between restarts.

Discovery sequence
------------------
1. Parse KITEZH_CAMERA_SUBNET as a CIDR range.
2. For every host IP, probe TCP ports 9999 (old Tapo protocol) and 443 (new
   HTTPS/KLAP protocol) with a short timeout.
3. On any open port, attempt pytapo authentication to fetch device info
   (nickname, model, MAC).
4. Build a CameraRecord with auto-generated label and capabilities, and save
   the registry to ``<workspace>/tapo_cameras.json``.

The registry is loaded on startup so that KAI can begin listening immediately
without waiting for a full rescan.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import quote

logger = logging.getLogger(__name__)

_REGISTRY_FILENAME = "tapo_cameras.json"
# Ports to probe — Tapo old protocol (9999) and new HTTPS API (443).
_TAPO_PORTS = (9999, 443)
_PROBE_TIMEOUT = 1.5  # seconds per TCP probe


@dataclass
class CameraRecord:
    """Everything KAI needs to know about one Tapo camera."""

    ip: str
    name: str           # snake_case label, e.g. "living_room" or "c310_1"
    model: str          # e.g. "C310", "C210"
    rtsp_url: str       # ******ip:554/stream1  (credentials embedded)
    has_ptz: bool
    has_speaker: bool
    has_mic: bool
    mac: str = ""
    last_seen: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Capability helpers
# ---------------------------------------------------------------------------

# Models definitively known to support PTZ (pan/tilt/zoom).
_PTZ_MODEL_NAMES: frozenset[str] = frozenset(
    {"C310", "C325WB", "C420", "C420WS", "C520WS", "C520"}
)


def _model_has_ptz(model: str) -> bool:
    """Heuristic: known PTZ models plus any Tapo C-series ≥ C300 tier."""
    upper = model.upper().strip()
    if upper in {m.upper() for m in _PTZ_MODEL_NAMES}:
        return True
    m = re.match(r"C(\d+)", upper)
    if m:
        return int(m.group(1)) >= 300
    return False


def _auto_label(model: str, ip: str, index: int, nickname: str) -> str:
    """Build a snake_case label for a camera.

    Prefers the nickname set in the Tapo app; falls back to
    ``<model>_<index>`` (e.g. ``c310_1``).
    """
    if nickname:
        label = re.sub(r"[^a-z0-9_]", "", nickname.lower().replace(" ", "_")).strip("_")
        if label:
            return label
    safe_model = re.sub(r"[^a-z0-9]", "", model.lower()) if model else "cam"
    return f"{safe_model}_{index}"


def _build_rtsp_url(ip: str, user: str, password: str) -> str:
    """Construct stream-1 RTSP URL with URL-encoded credentials."""
    return f"rtsp://{quote(user, safe='')}:{quote(password, safe='')}@{ip}:554/stream1"


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------

def _port_open(ip: str, port: int, timeout: float = _PROBE_TIMEOUT) -> bool:
    """Return True if *port* is open on *ip* (TCP connect probe)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_tapo(ip: str, user: str, password: str) -> tuple[str, str, str] | None:
    """Attempt pytapo authentication and return (nickname, model, mac).

    Returns ``None`` when the IP is not a Tapo camera or credentials are wrong.
    pytapo is an optional runtime dependency; when absent this function logs a
    debug message and returns ``None`` so the rest of discovery can continue.
    """
    try:
        from pytapo import Tapo  # type: ignore
    except ImportError:
        logger.debug("pytapo not installed; cannot probe %s (run: pip install pytapo)", ip)
        return None

    try:
        cam = Tapo(ip, user, password)
        info = cam.getBasicInfo()
        # pytapo nests device info differently across firmware versions.
        device_info: dict = (
            info.get("device_info", {}).get("basic_info")
            or info.get("device_info", {})
            or {}
        )
        nickname: str = device_info.get("device_alias") or device_info.get("alias") or ""
        model: str = device_info.get("device_model") or device_info.get("model") or ""
        mac: str = device_info.get("mac") or ""
        return nickname, model, mac
    except Exception as exc:
        logger.debug("Not a Tapo camera (or auth failed) at %s: %s", ip, exc)
        return None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _iter_subnet_ips(subnet: str):
    """Yield all usable host addresses in a CIDR *subnet* string."""
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except ValueError:
        logger.error("Invalid subnet CIDR '%s'; no cameras will be scanned.", subnet)
        return
    for addr in network.hosts():
        yield str(addr)


def discover_cameras(
    subnet: str,
    tapo_user: str,
    tapo_password: str,
    progress_cb: Callable[[str], None] | None = None,
) -> list[CameraRecord]:
    """Scan *subnet* and return discovered Tapo cameras as CameraRecords.

    For each host IP the function:
    1. Probes TCP ports 9999 and 443.
    2. On any open port, calls :func:`_probe_tapo` to authenticate and pull
       device info.
    3. Builds a :class:`CameraRecord` with an auto-generated label and
       capability flags.

    *progress_cb* receives human-readable progress strings if provided.
    Returns an empty list when prerequisites are missing.
    """
    if not subnet:
        logger.info(
            "TapoDiscovery: no camera subnet configured — "
            "set KITEZH_CAMERA_SUBNET to enable autodiscovery."
        )
        return []

    if not tapo_user or not tapo_password:
        logger.warning(
            "TapoDiscovery: Tapo credentials not set — "
            "set KITEZH_TAPO_USER and KITEZH_TAPO_PASSWORD."
        )
        return []

    cameras: list[CameraRecord] = []
    index = 1

    for ip in _iter_subnet_ips(subnet):
        responding_port: int | None = None
        for port in _TAPO_PORTS:
            if _port_open(ip, port):
                responding_port = port
                break
        if responding_port is None:
            continue

        msg = f"Potential camera at {ip}:{responding_port} — probing with pytapo…"
        if progress_cb:
            progress_cb(msg)
        logger.debug("TapoDiscovery: %s", msg)

        probe = _probe_tapo(ip, tapo_user, tapo_password)
        if probe is None:
            continue

        nickname, model, mac = probe
        name = _auto_label(model, ip, index, nickname)
        rtsp_url = _build_rtsp_url(ip, tapo_user, tapo_password)
        has_ptz = _model_has_ptz(model)

        rec = CameraRecord(
            ip=ip,
            name=name,
            model=model,
            rtsp_url=rtsp_url,
            has_ptz=has_ptz,
            has_speaker=True,   # all current Tapo indoor/outdoor cameras have a speaker
            has_mic=True,       # all current Tapo indoor/outdoor cameras have a mic
            mac=mac,
            last_seen=time.time(),
        )
        cameras.append(rec)
        confirmed = f"Registered camera '{name}' ({model}) @ {ip}"
        if progress_cb:
            progress_cb(confirmed)
        logger.info("TapoDiscovery: %s", confirmed)
        index += 1

    return cameras


# ---------------------------------------------------------------------------
# Registry persistence
# ---------------------------------------------------------------------------

def save_camera_registry(cameras: list[CameraRecord], workspace_path: str) -> None:
    """Write the camera list to ``<workspace_path>/tapo_cameras.json``."""
    path = Path(workspace_path) / _REGISTRY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(c) for c in cameras], indent=2), encoding="utf-8")
    logger.info(
        "TapoDiscovery: registry saved (%d camera(s)) → %s", len(cameras), path
    )


def load_camera_registry(workspace_path: str) -> list[CameraRecord]:
    """Load cameras from ``<workspace_path>/tapo_cameras.json``.

    Returns an empty list when the file does not exist or cannot be parsed.
    """
    path = Path(workspace_path) / _REGISTRY_FILENAME
    if not path.exists():
        return []
    try:
        raw: list[dict] = json.loads(path.read_text(encoding="utf-8"))
        cameras = [CameraRecord(**item) for item in raw]
        logger.info("TapoDiscovery: loaded %d camera(s) from registry.", len(cameras))
        return cameras
    except Exception as exc:
        logger.error("TapoDiscovery: failed to load registry: %s", exc)
        return []
