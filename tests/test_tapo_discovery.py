"""
tests/test_tapo_discovery.py — Unit tests for camera autodiscovery and registry.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from skills.tapo_discovery import (
    CameraRecord,
    _auto_label,
    _build_rtsp_url,
    _model_has_ptz,
    discover_cameras,
    load_camera_registry,
    save_camera_registry,
)


# ---------------------------------------------------------------------------
# _model_has_ptz
# ---------------------------------------------------------------------------

class TestModelHasPtz(unittest.TestCase):
    def test_c310_has_ptz(self):
        self.assertTrue(_model_has_ptz("C310"))

    def test_c325wb_has_ptz(self):
        self.assertTrue(_model_has_ptz("C325WB"))

    def test_c420_has_ptz(self):
        self.assertTrue(_model_has_ptz("C420"))

    def test_c200_no_ptz(self):
        self.assertFalse(_model_has_ptz("C200"))

    def test_c210_no_ptz(self):
        self.assertFalse(_model_has_ptz("C210"))

    def test_unknown_model_no_ptz(self):
        self.assertFalse(_model_has_ptz(""))

    def test_case_insensitive(self):
        self.assertTrue(_model_has_ptz("c310"))
        self.assertFalse(_model_has_ptz("c200"))


# ---------------------------------------------------------------------------
# _auto_label
# ---------------------------------------------------------------------------

class TestAutoLabel(unittest.TestCase):
    def test_uses_nickname_when_set(self):
        self.assertEqual(_auto_label("C310", "192.168.1.5", 1, "Living Room"), "living_room")

    def test_falls_back_to_model_index_when_no_nickname(self):
        self.assertEqual(_auto_label("C310", "192.168.1.5", 2, ""), "c310_2")

    def test_sanitizes_special_chars_in_nickname(self):
        self.assertEqual(_auto_label("C210", "192.168.1.6", 3, "Front Door!"), "front_door")

    def test_unknown_model_falls_back_gracefully(self):
        label = _auto_label("", "192.168.1.7", 4, "")
        self.assertEqual(label, "cam_4")

    def test_whitespace_only_nickname_falls_back(self):
        # After sanitisation, "  " becomes "" → fall back to model_index.
        label = _auto_label("C200", "192.168.1.8", 5, "   ")
        self.assertEqual(label, "c200_5")


# ---------------------------------------------------------------------------
# _build_rtsp_url
# ---------------------------------------------------------------------------

class TestBuildRtspUrl(unittest.TestCase):
    def test_basic_structure(self):
        url = _build_rtsp_url("192.168.1.5", "user", "pass123")
        self.assertTrue(url.startswith("rtsp://"))
        self.assertIn("@192.168.1.5:554/stream1", url)

    def test_credentials_url_encoded(self):
        url = _build_rtsp_url("192.168.1.5", "user@example.com", "p@ss:word")
        # Extract the credential portion (before the host)
        cred_part = url.split("@192.168.1.5")[0][len("rtsp://"):]
        user_part, _, pass_part = cred_part.partition(":")
        # Literal "@" inside user/password fields must be percent-encoded
        self.assertNotIn("@", user_part)
        self.assertNotIn("@", pass_part)


# ---------------------------------------------------------------------------
# Registry persistence
# ---------------------------------------------------------------------------

class TestRegistryPersistence(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()

    def _make_record(self) -> CameraRecord:
        return CameraRecord(
            ip="192.168.1.5",
            name="hallway",
            model="C310",
            rtsp_url="******192.168.1.5:554/stream1",
            has_ptz=True,
            has_speaker=True,
            has_mic=True,
            mac="AA:BB:CC:DD:EE:FF",
            last_seen=1_700_000_000.0,
        )

    def test_roundtrip(self):
        cams = [self._make_record()]
        save_camera_registry(cams, self._tmpdir)
        loaded = load_camera_registry(self._tmpdir)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].name, "hallway")
        self.assertEqual(loaded[0].ip, "192.168.1.5")
        self.assertTrue(loaded[0].has_ptz)
        self.assertEqual(loaded[0].mac, "AA:BB:CC:DD:EE:FF")

    def test_empty_list_persists(self):
        save_camera_registry([], self._tmpdir)
        loaded = load_camera_registry(self._tmpdir)
        self.assertEqual(loaded, [])

    def test_load_returns_empty_when_no_file(self):
        loaded = load_camera_registry(self._tmpdir + "/nonexistent")
        self.assertEqual(loaded, [])

    def test_load_returns_empty_on_corrupt_json(self):
        path = Path(self._tmpdir) / "tapo_cameras.json"
        path.write_text("not json", encoding="utf-8")
        loaded = load_camera_registry(self._tmpdir)
        self.assertEqual(loaded, [])

    def test_multiple_cameras_persist_in_order(self):
        cam1 = self._make_record()
        cam2 = CameraRecord(
            ip="192.168.1.6",
            name="garden",
            model="C200",
            rtsp_url="******192.168.1.6:554/stream1",
            has_ptz=False,
            has_speaker=True,
            has_mic=True,
        )
        save_camera_registry([cam1, cam2], self._tmpdir)
        loaded = load_camera_registry(self._tmpdir)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].name, "hallway")
        self.assertEqual(loaded[1].name, "garden")


# ---------------------------------------------------------------------------
# discover_cameras
# ---------------------------------------------------------------------------

class TestDiscoverCameras(unittest.TestCase):
    def test_returns_empty_when_no_subnet(self):
        self.assertEqual(discover_cameras("", "user", "pass"), [])

    def test_returns_empty_when_no_user(self):
        self.assertEqual(discover_cameras("192.168.1.0/30", "", "pass"), [])

    def test_returns_empty_when_no_password(self):
        self.assertEqual(discover_cameras("192.168.1.0/30", "user", ""), [])

    @patch("skills.tapo_discovery._port_open")
    @patch("skills.tapo_discovery._probe_tapo")
    def test_discovers_one_camera(self, mock_probe, mock_port):
        # Only 192.168.1.2 has port 9999 open; others return False.
        mock_port.side_effect = lambda ip, port: ip == "192.168.1.2" and port == 9999
        mock_probe.return_value = ("Living Room", "C310", "AA:BB:CC:DD:EE:FF")

        cameras = discover_cameras("192.168.1.0/30", "user", "pass")
        self.assertEqual(len(cameras), 1)
        self.assertEqual(cameras[0].ip, "192.168.1.2")
        self.assertEqual(cameras[0].model, "C310")
        self.assertTrue(cameras[0].has_ptz)
        self.assertEqual(cameras[0].name, "living_room")
        self.assertTrue(cameras[0].has_mic)
        self.assertTrue(cameras[0].has_speaker)

    @patch("skills.tapo_discovery._port_open", return_value=False)
    def test_no_open_ports_returns_empty(self, _):
        self.assertEqual(discover_cameras("192.168.1.0/30", "user", "pass"), [])

    @patch("skills.tapo_discovery._port_open")
    @patch("skills.tapo_discovery._probe_tapo", return_value=None)
    def test_probe_failure_skips_ip(self, _probe, mock_port):
        mock_port.return_value = True
        self.assertEqual(discover_cameras("192.168.1.0/30", "user", "pass"), [])

    @patch("skills.tapo_discovery._port_open")
    @patch("skills.tapo_discovery._probe_tapo")
    def test_progress_callback_called(self, mock_probe, mock_port):
        mock_port.side_effect = lambda ip, port: ip == "192.168.1.2" and port == 9999
        mock_probe.return_value = ("Hallway", "C210", "")
        messages: list[str] = []
        discover_cameras("192.168.1.0/30", "u", "p", progress_cb=messages.append)
        self.assertTrue(any("192.168.1.2" in m for m in messages))

    @patch("skills.tapo_discovery._port_open")
    @patch("skills.tapo_discovery._probe_tapo")
    def test_invalid_subnet_returns_empty(self, mock_probe, mock_port):
        cameras = discover_cameras("not_a_cidr", "user", "pass")
        mock_port.assert_not_called()
        mock_probe.assert_not_called()
        self.assertEqual(cameras, [])
