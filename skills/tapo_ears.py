"""
skills/tapo_ears.py — Wakeword detection via Tapo camera microphones.

For each camera registered by TapoHub, a background thread opens the RTSP
audio track with an FFmpeg subprocess, feeds raw 16-kHz mono PCM chunks to
an openWakeWord model, and fires a callback whenever the configured trigger
phrase is detected.

Audio pipeline
--------------
  FFmpeg (RTSP) ──raw PCM──► WakewordListener._run() ──► on_detect(camera)

FFmpeg decodes any codec the camera streams; openWakeWord runs inference on
standardised 80-ms chunks.  When the stream drops the listener reconnects
with exponential backoff so KAI stays available.

Dependencies
------------
- ``ffmpeg`` binary on PATH  (handles RTSP and all audio codecs)
- ``openwakeword``  (``pip install openwakeword``)
- ``numpy``         (already in requirements.txt)
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable

import numpy as np

from skills.tapo_discovery import CameraRecord

logger = logging.getLogger(__name__)

# openWakeWord expects 16-kHz mono PCM.
_SAMPLE_RATE = 16_000
_CHUNK_MS = 80
_CHUNK_SAMPLES = int(_SAMPLE_RATE * _CHUNK_MS / 1000)  # 1280 samples per chunk
_CHUNK_BYTES = _CHUNK_SAMPLES * 2                       # int16 → 2 bytes per sample

_BACKOFF_INITIAL = 2.0
_BACKOFF_MAX = 60.0

DetectCallback = Callable[[CameraRecord], None]


class WakewordListener:
    """Background thread that monitors one camera's microphone for the wakeword.

    Parameters
    ----------
    camera:
        The camera to listen on.
    wakeword_model_path:
        Path to a custom ``.onnx`` model file *or* the name of a bundled
        openWakeWord model (e.g. ``"hey_jarvis"``).
    on_detect:
        Callable invoked with the :class:`CameraRecord` on each wakeword hit.
    score_threshold:
        Minimum prediction score (0–1) required to fire *on_detect*.
    """

    def __init__(
        self,
        camera: CameraRecord,
        wakeword_model_path: str,
        on_detect: DetectCallback,
        score_threshold: float = 0.5,
    ) -> None:
        self._camera = camera
        self._model_path = wakeword_model_path
        self._on_detect = on_detect
        self._threshold = score_threshold
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"tapo-ears-{camera.name}",
            daemon=True,
        )

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the listener thread."""
        self._stop_event.clear()
        self._thread.start()
        logger.info("WakewordListener: started for camera '%s'.", self._camera.name)

    def stop(self) -> None:
        """Signal the listener thread to exit cleanly."""
        self._stop_event.set()
        logger.info("WakewordListener: stopping for camera '%s'.", self._camera.name)

    @property
    def camera_ip(self) -> str:
        """IP address of the camera this listener is attached to."""
        return self._camera.ip

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_ffmpeg_cmd(self) -> list[str]:
        """Return the FFmpeg command that pipes raw PCM from the camera."""
        return [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", self._camera.rtsp_url,
            "-vn",                       # drop video track
            "-acodec", "pcm_s16le",
            "-ar", str(_SAMPLE_RATE),
            "-ac", "1",                  # mono
            "-f", "s16le",
            "pipe:1",
        ]

    def _load_oww_model(self):
        """Import and return a configured openWakeWord Model instance."""
        try:
            from openwakeword.model import Model  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "openwakeword is not installed — run: pip install openwakeword"
            ) from exc
        return Model(wakeword_models=[self._model_path], inference_framework="onnx")

    def _run(self) -> None:
        """Main listener loop: open RTSP audio stream → run OWW inference."""
        try:
            oww = self._load_oww_model()
        except RuntimeError as exc:
            logger.error(
                "WakewordListener: %s — listener for '%s' disabled.",
                exc,
                self._camera.name,
            )
            return

        backoff = _BACKOFF_INITIAL
        while not self._stop_event.is_set():
            proc: subprocess.Popen | None = None
            try:
                proc = subprocess.Popen(
                    self._build_ffmpeg_cmd(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                logger.debug(
                    "WakewordListener: FFmpeg stream opened for '%s'.", self._camera.name
                )
                backoff = _BACKOFF_INITIAL  # reset on successful connect

                while not self._stop_event.is_set():
                    raw = proc.stdout.read(_CHUNK_BYTES)
                    if len(raw) < _CHUNK_BYTES:
                        # Stream ended or short read — reconnect.
                        break
                    samples = (
                        np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    )
                    prediction: dict[str, float] = oww.predict(samples)
                    for model_name, score in prediction.items():
                        if score >= self._threshold:
                            logger.info(
                                "WakewordListener: wakeword detected on '%s' "
                                "(model=%s score=%.3f)",
                                self._camera.name,
                                model_name,
                                score,
                            )
                            oww.reset()  # clear state to prevent repeat fires
                            try:
                                self._on_detect(self._camera)
                            except Exception as cb_exc:
                                logger.error(
                                    "WakewordListener: callback error: %s", cb_exc
                                )

            except FileNotFoundError:
                logger.error(
                    "WakewordListener: 'ffmpeg' binary not found in PATH — "
                    "install FFmpeg to enable audio wakeword listening."
                )
                return  # no point retrying without ffmpeg
            except Exception as exc:
                logger.warning(
                    "WakewordListener: stream error on '%s': %s — "
                    "reconnecting in %.1fs",
                    self._camera.name,
                    exc,
                    backoff,
                )
            finally:
                if proc is not None:
                    proc.kill()
                    proc.wait()

            if not self._stop_event.is_set():
                self._stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
