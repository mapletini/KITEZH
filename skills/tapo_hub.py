"""
skills/tapo_hub.py — KAI's Tapo camera orchestrator.

TapoHub owns the full camera lifecycle and is the only interface KAI uses to
interact with the physical cameras.  No actions are exposed to end users.

Startup sequence
----------------
1. Load the cached registry immediately (fast path, no blocking).
2. Start wakeword listeners for any camera with a mic.
3. If KITEZH_CAMERA_SUBNET is configured, launch a background discovery
   thread that rescans the subnet, updates the registry, and starts
   listeners for any newly found cameras.

KAI-facing API
--------------
- ``list_cameras()``         — summarise known cameras (no credentials)
- ``capture_snapshot(name)`` — grab a live JPEG from a named camera
- ``refresh_cameras()``      — trigger a blocking rediscovery on demand
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

import config
from skills.tapo_discovery import (
    CameraRecord,
    discover_cameras,
    load_camera_registry,
    save_camera_registry,
)
from skills.tapo_ears import WakewordListener
from skills.vision import RTSPCameraBridge

if TYPE_CHECKING:
    from skills.neuro_affect import NeuroChemicalEngine

logger = logging.getLogger(__name__)


class TapoHub:
    """Manages Tapo cameras end-to-end for KAI.

    Parameters
    ----------
    neuro:
        Optional :class:`NeuroChemicalEngine` instance.  When provided,
        wakeword events inject a mild alertness stimulus into KAI's
        neurochemical state.
    """

    def __init__(self, neuro: "NeuroChemicalEngine | None" = None) -> None:
        self._neuro = neuro
        self._cameras: list[CameraRecord] = []
        self._listeners: list[WakewordListener] = []
        self._lock = threading.Lock()
        self._vision = RTSPCameraBridge(workspace_path=config.WORKSPACE_PATH)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load cached registry, start listeners, then rescan in background."""
        if not config.TAPO_PASSWORD:
            logger.info(
                "TapoHub: Tapo credentials not configured — "
                "set KITEZH_TAPO_USER and KITEZH_TAPO_PASSWORD to enable cameras."
            )
            return

        # Fast path: use cached registry so listeners start immediately.
        cached = load_camera_registry(config.WORKSPACE_PATH)
        if cached:
            with self._lock:
                self._cameras = cached
            self._start_listeners(cached)

        # Background discovery refreshes the registry without blocking startup.
        if config.CAMERA_SUBNET:
            t = threading.Thread(
                target=self._discover_and_refresh,
                name="tapo-discovery",
                daemon=True,
            )
            t.start()
        elif not cached:
            logger.info(
                "TapoHub: no registry found and KITEZH_CAMERA_SUBNET is not set — "
                "cameras unavailable until a subnet is configured."
            )

    def stop(self) -> None:
        """Stop all active wakeword listeners."""
        with self._lock:
            for listener in self._listeners:
                listener.stop()
            self._listeners.clear()
        logger.info("TapoHub: all listeners stopped.")

    # ------------------------------------------------------------------
    # KAI-facing actions
    # ------------------------------------------------------------------

    def list_cameras(self) -> list[dict]:
        """Return a summary of known cameras (credentials are never included)."""
        with self._lock:
            return [
                {
                    "name": c.name,
                    "model": c.model,
                    "ip": c.ip,
                    "has_ptz": c.has_ptz,
                    "has_mic": c.has_mic,
                    "has_speaker": c.has_speaker,
                }
                for c in self._cameras
            ]

    def capture_snapshot(self, camera_name: str) -> str | None:
        """Grab a live JPEG frame from *camera_name*.  Returns file path or None."""
        cam = self._get_camera(camera_name)
        if cam is None:
            logger.warning("TapoHub: unknown camera '%s'.", camera_name)
            return None
        return self._vision.capture_snapshot(cam.rtsp_url, cam.name)

    def refresh_cameras(self) -> list[CameraRecord]:
        """Block until discovery completes and return the updated camera list.

        Useful when KAI wants to ensure the registry is current before acting
        on a camera by name.
        """
        if not config.CAMERA_SUBNET:
            logger.info("TapoHub: KITEZH_CAMERA_SUBNET not set; skipping refresh.")
            with self._lock:
                return list(self._cameras)
        return self._discover_and_refresh()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_camera(self, name: str) -> CameraRecord | None:
        with self._lock:
            for cam in self._cameras:
                if cam.name == name:
                    return cam
        return None

    def _start_listeners(self, cameras: list[CameraRecord]) -> None:
        """Start wakeword listeners for cameras that have a mic."""
        if not config.WAKEWORD_MODEL:
            logger.info(
                "TapoHub: KITEZH_WAKEWORD_MODEL not set — "
                "audio wakeword listening disabled."
            )
            return

        with self._lock:
            already_listening = {
                listener.camera_ip for listener in self._listeners
            }

        new_listeners: list[WakewordListener] = []
        for cam in cameras:
            if not cam.has_mic:
                continue
            if cam.ip in already_listening:
                continue
            listener = WakewordListener(
                camera=cam,
                wakeword_model_path=config.WAKEWORD_MODEL,
                on_detect=self._on_wakeword,
                score_threshold=config.WAKEWORD_THRESHOLD,
            )
            listener.start()
            new_listeners.append(listener)

        if new_listeners:
            with self._lock:
                self._listeners.extend(new_listeners)
            logger.info(
                "TapoHub: %d wakeword listener(s) now active (total).",
                len(self._listeners),
            )

    def _discover_and_refresh(self) -> list[CameraRecord]:
        """Run a full subnet scan, update registry, and adjust listeners."""
        logger.info(
            "TapoHub: starting camera discovery on subnet %s…", config.CAMERA_SUBNET
        )
        discovered = discover_cameras(
            subnet=config.CAMERA_SUBNET,
            tapo_user=config.TAPO_USER,
            tapo_password=config.TAPO_PASSWORD,
            progress_cb=lambda msg: logger.info("TapoHub: %s", msg),
        )

        if discovered:
            save_camera_registry(discovered, config.WORKSPACE_PATH)
            with self._lock:
                self._cameras = discovered
            # Start listeners for any cameras not yet covered.
            self._start_listeners(discovered)
            logger.info(
                "TapoHub: discovery complete — %d camera(s) registered.",
                len(discovered),
            )
        else:
            logger.warning("TapoHub: discovery found no Tapo cameras on %s.", config.CAMERA_SUBNET)

        with self._lock:
            return list(self._cameras)

    def _on_wakeword(self, camera: CameraRecord) -> None:
        """Callback fired by a WakewordListener when KAI's name is called.

        Injects a mild alertness stimulus into the neurochemical engine so
        KAI becomes attentive to the environment.
        """
        logger.info(
            "TapoHub: wakeword detected via camera '%s' (%s) — KAI is attending.",
            camera.name,
            camera.ip,
        )
        if self._neuro is not None:
            # uncertainty → noradrenaline spike (alertness); small reward for
            # being called signals a positive social event.
            self._neuro.apply_stimulus(
                uncertainty=0.15,
                reward=0.05,
                user_id="environment",
            )
