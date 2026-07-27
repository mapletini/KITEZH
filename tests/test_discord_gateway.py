"""Tests for skills/discord_gateway.py."""

import unittest
from unittest.mock import MagicMock, patch

from skills.discord_gateway import DiscordGatewayConfig, DiscordGatewayRuntime


class TestDiscordGatewayRuntime(unittest.TestCase):
    def _make_runtime(self, *, enabled: bool = True, token: str = "token") -> tuple[DiscordGatewayRuntime, MagicMock]:
        adapter = MagicMock()
        runtime = DiscordGatewayRuntime(
            DiscordGatewayConfig(
                enabled=enabled,
                bot_token=token,
                intents=37377,
                reconnect_base_seconds=0.01,
                reconnect_max_seconds=0.02,
            ),
            adapter,
        )
        return runtime, adapter

    def test_message_create_forwarded_to_adapter(self) -> None:
        gateway, adapter = self._make_runtime()
        payload = {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "s": 12,
            "d": {"id": "1", "channel_id": "22", "content": "hello"},
        }

        gateway._handle_gateway_payload(payload)

        adapter.process_inbound_message.assert_called_once_with(payload["d"])
        self.assertEqual(gateway.status()["last_sequence"], 12)

    def test_non_message_event_ignored(self) -> None:
        gateway, adapter = self._make_runtime()
        payload = {"op": 0, "t": "GUILD_CREATE", "s": 3, "d": {"id": "g1"}}

        gateway._handle_gateway_payload(payload)

        adapter.process_inbound_message.assert_not_called()

    def test_voice_state_update_forwarded_to_adapter(self) -> None:
        gateway, adapter = self._make_runtime()
        payload = {
            "op": 0,
            "t": "VOICE_STATE_UPDATE",
            "s": 5,
            "d": {"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"},
        }

        gateway._handle_gateway_payload(payload)

        adapter.process_voice_state_update.assert_called_once_with(payload["d"])

    def test_voice_server_update_forwarded_to_adapter(self) -> None:
        gateway, adapter = self._make_runtime()
        payload = {
            "op": 0,
            "t": "VOICE_SERVER_UPDATE",
            "s": 6,
            "d": {"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"},
        }

        gateway._handle_gateway_payload(payload)

        adapter.process_voice_server_update.assert_called_once_with(payload["d"])

    def test_voice_session_description_event_forwarded_to_adapter(self) -> None:
        gateway, adapter = self._make_runtime()
        payload = {
            "op": 0,
            "t": "VOICE_SESSION_DESCRIPTION",
            "s": 7,
            "d": {"mode": "xsalsa20_poly1305", "secret_key": [1] * 32},
        }

        gateway._handle_gateway_payload(payload)

        adapter.process_voice_session_description.assert_called_once_with(payload["d"])

    def test_process_voice_gateway_payload_forwards_op4_description(self) -> None:
        gateway, adapter = self._make_runtime()
        adapter.process_voice_session_description.return_value = True

        ok = gateway.process_voice_gateway_payload(
            {
                "op": 4,
                "d": {"mode": "xsalsa20_poly1305", "secret_key": [2] * 32},
            }
        )

        self.assertTrue(ok)
        adapter.process_voice_session_description.assert_called_once_with(
            {"mode": "xsalsa20_poly1305", "secret_key": [2] * 32}
        )

    def test_process_voice_gateway_payload_rejects_non_description(self) -> None:
        gateway, adapter = self._make_runtime()

        ok = gateway.process_voice_gateway_payload({"op": 2, "d": {"ssrc": 1234}})

        self.assertFalse(ok)
        adapter.process_voice_session_description.assert_not_called()

    def test_request_voice_state_update_queues_op4_payload(self) -> None:
        gateway, _ = self._make_runtime(enabled=True, token="tok")

        out = gateway.request_voice_state_update(
            guild_id="g1",
            channel_id="vc1",
            self_mute=True,
            self_deaf=False,
        )

        self.assertTrue(out["ok"])
        ws = MagicMock()
        gateway._drain_outbound(ws)
        sent = ws.send.call_args[0][0]
        self.assertIn('"op":4', sent)
        self.assertIn('"guild_id":"g1"', sent)
        self.assertIn('"channel_id":"vc1"', sent)

    def test_sync_transport_voice_updates_moves_pending_payloads(self) -> None:
        gateway, adapter = self._make_runtime(enabled=True, token="tok")
        adapter.pop_pending_voice_state_update.side_effect = [
            {"guild_id": "g1", "channel_id": "vc1", "self_mute": True, "self_deaf": False},
            None,
        ]

        moved = gateway.sync_transport_voice_updates()

        self.assertEqual(moved, 1)
        ws = MagicMock()
        gateway._drain_outbound(ws)
        sent = ws.send.call_args[0][0]
        self.assertIn('"op":4', sent)
        self.assertIn('"guild_id":"g1"', sent)
        self.assertIn('"channel_id":"vc1"', sent)

    def test_start_without_websocket_dependency_is_graceful(self) -> None:
        gateway, _ = self._make_runtime()
        with patch("skills.discord_gateway._ws_connect", None):
            gateway.start()

        status = gateway.status()
        self.assertFalse(status["active"])
        self.assertIn("unavailable", status["availability_reason"])

    def test_start_disabled_is_noop(self) -> None:
        gateway, _ = self._make_runtime(enabled=False)
        gateway.start()
        status = gateway.status()
        self.assertFalse(status["active"])
        self.assertIn("disabled", status["availability_reason"])


if __name__ == "__main__":
    unittest.main()
