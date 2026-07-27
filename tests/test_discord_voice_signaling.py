"""Tests for skills/discord_voice_signaling.py."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from skills.discord_voice_signaling import DiscordVoiceSignalingConfig, DiscordVoiceSignalingRuntime


class TestDiscordVoiceSignalingRuntime(unittest.TestCase):
    def _make_runtime(self, *, enabled: bool = True) -> tuple[DiscordVoiceSignalingRuntime, MagicMock, MagicMock]:
        gateway = MagicMock()
        transport = MagicMock()
        runtime = DiscordVoiceSignalingRuntime(
            DiscordVoiceSignalingConfig(
                enabled=enabled,
                bot_user_id="999",
                reconnect_base_seconds=0.01,
                reconnect_max_seconds=0.02,
                idle_poll_seconds=0.01,
            ),
            gateway,
            transport,
        )
        return runtime, gateway, transport

    def test_ingest_payload_forwards_to_gateway_processor(self) -> None:
        runtime, gateway, _ = self._make_runtime()
        gateway.process_voice_gateway_payload.return_value = True

        ok = runtime.ingest_payload({"op": 4, "d": {"mode": "none"}})

        self.assertTrue(ok)
        gateway.process_voice_gateway_payload.assert_called_once_with({"op": 4, "d": {"mode": "none"}})

    def test_ingest_payload_returns_false_without_gateway_hook(self) -> None:
        runtime, _, transport = self._make_runtime()
        runtime = DiscordVoiceSignalingRuntime(runtime._runtime, object(), transport)

        ok = runtime.ingest_payload({"op": 4, "d": {"mode": "none"}})

        self.assertFalse(ok)

    def test_build_voice_ws_url_adds_scheme_and_version(self) -> None:
        url = DiscordVoiceSignalingRuntime._build_voice_ws_url("voice.discord.media:443")

        self.assertTrue(url.startswith("wss://voice.discord.media:443"))
        self.assertIn("v=4", url)

    def test_build_identify_payload_uses_active_session_fields(self) -> None:
        runtime, _, _ = self._make_runtime()

        payload = runtime._build_identify_payload(
            {
                "guild_id": "g1",
                "gateway_session_id": "sess-1",
                "voice_token": "tok",
            }
        )

        self.assertEqual(payload["op"], 0)
        self.assertEqual(payload["d"]["server_id"], "g1")
        self.assertEqual(payload["d"]["user_id"], "999")
        self.assertEqual(payload["d"]["session_id"], "sess-1")
        self.assertEqual(payload["d"]["token"], "tok")

    def test_parse_payload_rejects_non_dict_shapes(self) -> None:
        with self.assertRaises(RuntimeError):
            DiscordVoiceSignalingRuntime._parse_payload(json.dumps(["not", "dict"]))

    def test_get_active_voice_session_returns_none_without_prereqs(self) -> None:
        runtime, _, transport = self._make_runtime()
        transport.status.return_value = {
            "active": {
                "guild_id": "g1",
                "gateway_session_id": "",
                "voice_token": "tok",
                "voice_endpoint": "voice.discord.media",
            }
        }

        active = runtime._get_active_voice_session()

        self.assertIsNone(active)

    def test_build_select_protocol_payload(self) -> None:
        payload = DiscordVoiceSignalingRuntime._build_select_protocol_payload(
            address="10.0.0.2",
            port=55000,
            mode="xsalsa20_poly1305",
        )

        self.assertEqual(payload["op"], 1)
        self.assertEqual(payload["d"]["protocol"], "udp")
        self.assertEqual(payload["d"]["data"]["address"], "10.0.0.2")
        self.assertEqual(payload["d"]["data"]["port"], 55000)
        self.assertEqual(payload["d"]["data"]["mode"], "xsalsa20_poly1305")

    def test_choose_encryption_mode_prefers_active_mode(self) -> None:
        mode = DiscordVoiceSignalingRuntime._choose_encryption_mode(
            ready={"modes": ["xsalsa20_poly1305", "xsalsa20_poly1305_suffix"]},
            active={"encryption_mode": "xsalsa20_poly1305"},
        )
        self.assertEqual(mode, "xsalsa20_poly1305")

    def test_choose_encryption_mode_falls_back_to_first_advertised(self) -> None:
        mode = DiscordVoiceSignalingRuntime._choose_encryption_mode(
            ready={"modes": ["xsalsa20_poly1305_suffix", "xsalsa20_poly1305_lite"]},
            active={"encryption_mode": "none"},
        )
        self.assertEqual(mode, "xsalsa20_poly1305_suffix")

    def test_handle_inbound_ready_sends_select_protocol(self) -> None:
        runtime, _gateway, _transport = self._make_runtime()
        ws = MagicMock()
        runtime._perform_udp_ip_discovery = MagicMock(return_value=("10.0.0.8", 56000))
        runtime._send_speaking = MagicMock()

        runtime._handle_inbound_payload(
            ws=ws,
            payload={"op": 2, "d": {"port": 50000, "ssrc": 1234, "modes": ["xsalsa20_poly1305"]}},
            active={"voice_endpoint": "voice.discord.media", "encryption_mode": "xsalsa20_poly1305"},
        )

        sent = ws.send.call_args[0][0]
        parsed = json.loads(sent)
        self.assertEqual(parsed["op"], 1)
        self.assertEqual(parsed["d"]["data"]["mode"], "xsalsa20_poly1305")
        self.assertEqual(runtime.status()["last_ssrc"], 1234)
        self.assertEqual(runtime.status()["metrics"]["select_protocol_sent"], 1)

    def test_handle_inbound_session_description_forwards_and_speaks(self) -> None:
        runtime, gateway, _transport = self._make_runtime()
        runtime._last_ssrc = 777
        runtime._send_speaking = MagicMock()
        gateway.process_voice_gateway_payload.return_value = True
        ws = MagicMock()

        runtime._handle_inbound_payload(
            ws=ws,
            payload={"op": 4, "d": {"mode": "xsalsa20_poly1305", "secret_key": [1] * 32}},
            active={"voice_endpoint": "voice.discord.media"},
        )

        runtime._send_speaking.assert_called_once_with(ws=ws, speaking=1, delay=0, ssrc=777)
        self.assertEqual(runtime.status()["metrics"]["session_descriptions_forwarded"], 1)

    def test_handle_inbound_heartbeat_ack_updates_metric(self) -> None:
        runtime, _gateway, _transport = self._make_runtime()
        ws = MagicMock()

        runtime._handle_inbound_payload(ws=ws, payload={"op": 6, "d": {}}, active={})

        self.assertEqual(runtime.status()["metrics"]["heartbeat_acks"], 1)
        ws.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
