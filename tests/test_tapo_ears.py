"""
tests/test_tapo_ears.py — Unit tests for the wakeword listener.
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from skills.tapo_discovery import CameraRecord
from skills.tapo_ears import WakewordListener


def _make_camera(name: str = "hallway") -> CameraRecord:
    return CameraRecord(
        ip="192.168.1.5",
        name=name,
        model="C310",
        rtsp_url="******192.168.1.5:554/stream1",
        has_ptz=True,
        has_speaker=True,
        has_mic=True,
    )


class TestWakewordListenerInit(unittest.TestCase):
    def test_thread_is_daemon(self):
        listener = WakewordListener(_make_camera(), "hey_jarvis", on_detect=lambda c: None)
        self.assertTrue(listener._thread.daemon)

    def test_thread_name_includes_camera(self):
        listener = WakewordListener(_make_camera("front_door"), "hey_jarvis", on_detect=lambda c: None)
        self.assertIn("front_door", listener._thread.name)

    def test_default_threshold(self):
        listener = WakewordListener(_make_camera(), "hey_jarvis", on_detect=lambda c: None)
        self.assertEqual(listener._threshold, 0.5)

    def test_custom_threshold(self):
        listener = WakewordListener(_make_camera(), "hey_jarvis", on_detect=lambda c: None, score_threshold=0.8)
        self.assertEqual(listener._threshold, 0.8)

    def test_stop_event_clear_before_start(self):
        listener = WakewordListener(_make_camera(), "hey_jarvis", on_detect=lambda c: None)
        # stop_event should not be set before start() is called
        self.assertFalse(listener._stop_event.is_set())


class TestWakewordListenerLifecycle(unittest.TestCase):
    def test_stop_sets_event(self):
        listener = WakewordListener(_make_camera(), "hey_jarvis", on_detect=lambda c: None)
        # Patch _run so the thread exits immediately
        with patch.object(WakewordListener, "_run", return_value=None):
            listener.start()
            listener.stop()
        self.assertTrue(listener._stop_event.is_set())

    def test_start_clears_stop_event(self):
        listener = WakewordListener(_make_camera(), "hey_jarvis", on_detect=lambda c: None)
        listener._stop_event.set()  # manually set
        with patch.object(WakewordListener, "_run", return_value=None):
            listener.start()
        self.assertFalse(listener._stop_event.is_set())


class TestWakewordListenerFfmpegCmd(unittest.TestCase):
    def test_ffmpeg_cmd_structure(self):
        cam = _make_camera()
        listener = WakewordListener(cam, "hey_jarvis", on_detect=lambda c: None)
        cmd = listener._build_ffmpeg_cmd()
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-vn", cmd)
        self.assertIn("pcm_s16le", cmd)
        self.assertIn("16000", cmd)
        self.assertIn("s16le", cmd)
        self.assertIn("pipe:1", cmd)
        # RTSP URL must be in the command
        self.assertIn(cam.rtsp_url, cmd)


class TestWakewordListenerOwwImportError(unittest.TestCase):
    def test_disables_gracefully_when_openwakeword_missing(self):
        """When openwakeword is not installed the listener should log an error
        and return without crashing instead of propagating an ImportError."""
        cam = _make_camera()
        detected: list[str] = []

        listener = WakewordListener(cam, "hey_jarvis", on_detect=lambda c: detected.append(c.name))

        with patch.object(WakewordListener, "_load_oww_model", side_effect=RuntimeError("not installed")):
            # _run should catch the RuntimeError and return without firing callback
            listener._run()

        self.assertEqual(detected, [])


class TestWakewordListenerFfmpegMissing(unittest.TestCase):
    def test_exits_cleanly_when_ffmpeg_not_found(self):
        """FileNotFoundError from Popen when ffmpeg is absent should exit _run cleanly."""
        cam = _make_camera()
        listener = WakewordListener(cam, "hey_jarvis", on_detect=lambda c: None)

        fake_oww = MagicMock()
        fake_oww.predict.return_value = {}

        with patch.object(WakewordListener, "_load_oww_model", return_value=fake_oww), \
             patch("skills.tapo_ears.subprocess.Popen", side_effect=FileNotFoundError):
            # Should log error and return without infinite retry
            listener._run()

        # If _run returns we pass; no assertion needed beyond no exception.


class TestWakewordDetectCallback(unittest.TestCase):
    def test_callback_fires_and_resets_model_on_high_score(self):
        """When the OWW model returns a score above threshold the callback fires
        and oww.reset() is called to prevent repeat triggers."""
        import numpy as np

        cam = _make_camera()
        detected: list[str] = []

        listener = WakewordListener(cam, "hey_jarvis", on_detect=lambda c: None, score_threshold=0.5)

        def on_detect(c: CameraRecord) -> None:
            detected.append(c.name)
            # Signal the listener to stop so _run exits without waiting for backoff.
            listener._stop_event.set()

        listener._on_detect = on_detect

        fake_oww = MagicMock()
        call_n = {"n": 0}

        def fake_predict(samples):
            call_n["n"] += 1
            if call_n["n"] == 1:
                return {"hey_jarvis": 0.9}
            return {}

        fake_oww.predict.side_effect = fake_predict
        fake_oww.reset = MagicMock()

        raw_audio = (np.zeros(1280, dtype=np.int16)).tobytes()

        fake_proc = MagicMock()
        # First read full chunk; second returns empty to break inner loop.
        fake_proc.stdout.read.side_effect = [raw_audio, b""]

        with patch.object(WakewordListener, "_load_oww_model", return_value=fake_oww), \
             patch("skills.tapo_ears.subprocess.Popen", return_value=fake_proc):
            listener._run()

        self.assertIn("hallway", detected)
        fake_oww.reset.assert_called_once()

    def test_low_score_does_not_fire_callback(self):
        """Scores below threshold must not trigger the callback."""
        import numpy as np

        cam = _make_camera()
        detected: list[str] = []

        listener = WakewordListener(
            cam, "hey_jarvis", on_detect=lambda c: detected.append(c.name), score_threshold=0.7
        )

        fake_oww = MagicMock()
        fake_oww.predict.return_value = {"hey_jarvis": 0.3}  # below threshold
        fake_oww.reset = MagicMock()

        raw_audio = (np.zeros(1280, dtype=np.int16)).tobytes()
        fake_proc = MagicMock()
        fake_proc.stdout.read.side_effect = [raw_audio, b""]

        # Make backoff wait exit immediately by setting the stop event.
        original_wait = listener._stop_event.wait

        def instant_wait(timeout=None):
            listener._stop_event.set()
            return True

        listener._stop_event.wait = instant_wait  # type: ignore[method-assign]

        with patch.object(WakewordListener, "_load_oww_model", return_value=fake_oww), \
             patch("skills.tapo_ears.subprocess.Popen", return_value=fake_proc):
            listener._run()

        self.assertEqual(detected, [])
        fake_oww.reset.assert_not_called()
