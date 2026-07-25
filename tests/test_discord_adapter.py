"""Tests for skills/discord_adapter.py."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.discord_adapter import DiscordAdapter, DiscordRuntimeConfig, OutboundAction


class TestDiscordAdapter(unittest.TestCase):
    def _make_adapter(self, tmp_workspace: str) -> DiscordAdapter:
        runtime = DiscordRuntimeConfig(
            enabled=True,
            bot_token="token",
            operator_user_id="123",
            recent_messages_limit=50,
            max_send_attempts=3,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
            inbound_poll_seconds=0.1,
            inbound_channel_ids=("chan-1",),
            bot_user_id="999",
            gateway_enabled=False,
            voice_enabled=True,
            voice_autojoin_on_mention=True,
            voice_single_active_channel=True,
            voice_buffer_seconds=30,
            audit_retention_days=30,
            signing_secret="secret",
        )
        with patch("config.WORKSPACE_PATH", tmp_workspace):
            return DiscordAdapter(runtime)

    def test_issue_and_validate_approval_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            token = adapter.issue_approval_token(action_type="delete_message", actor="admin")
            ok, msg = adapter.validate_approval_token(token, expected_action="delete_message")
            self.assertTrue(ok)
            self.assertEqual(msg, "ok")

    def test_approval_token_one_time_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            token = adapter.issue_approval_token(action_type="timeout_member", actor="admin")
            ok1, _ = adapter.validate_approval_token(token, expected_action="timeout_member")
            ok2, msg2 = adapter.validate_approval_token(token, expected_action="timeout_member")
            self.assertTrue(ok1)
            self.assertFalse(ok2)
            self.assertIn("used", msg2)

    def test_enqueue_action_disabled(self) -> None:
        runtime = DiscordRuntimeConfig(
            enabled=False,
            bot_token="",
            operator_user_id="",
            recent_messages_limit=50,
            max_send_attempts=3,
            retry_base_seconds=0.01,
            retry_max_seconds=0.02,
            inbound_poll_seconds=0.1,
            inbound_channel_ids=(),
            bot_user_id="",
            gateway_enabled=False,
            voice_enabled=False,
            voice_autojoin_on_mention=False,
            voice_single_active_channel=True,
            voice_buffer_seconds=30,
            audit_retention_days=30,
            signing_secret="secret",
        )
        with tempfile.TemporaryDirectory() as tmp, patch("config.WORKSPACE_PATH", tmp):
            adapter = DiscordAdapter(runtime)
            out = adapter.enqueue_action(OutboundAction("send_message", {"channel_id": "1", "content": "x"}))
        self.assertFalse(out["ok"])

    def test_send_message_enqueues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            out = adapter.send_message("123", "hello")
            self.assertTrue(out["ok"])
            self.assertTrue(out["queued"])

    def test_poll_inbound_once_emits_mentions_bot_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            events: list[dict[str, object]] = []
            mentions: list[dict[str, object]] = []
            adapter.register_inbound_handler(events.append)
            adapter.register_mention_handler(mentions.append)

            with patch.object(
                adapter,
                "fetch_recent_messages",
                return_value={
                    "ok": True,
                    "data": [
                        {
                            "id": "101",
                            "channel_id": "chan-1",
                            "guild_id": "guild-1",
                            "content": "hello <@999>",
                            "author": {"id": "42", "username": "Alice", "bot": False},
                            "mentions": [{"id": "999"}],
                        }
                    ],
                },
            ):
                emitted = adapter.poll_inbound_once()

        self.assertEqual(emitted, 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(len(mentions), 1)
        self.assertTrue(events[0]["mentions_bot"])
        self.assertTrue(events[0]["voice_autojoin_requested"])

    def test_poll_inbound_once_skips_duplicate_message_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            events: list[dict[str, object]] = []
            adapter.register_inbound_handler(events.append)

            payload = {
                "ok": True,
                "data": [
                    {
                        "id": "202",
                        "channel_id": "chan-1",
                        "guild_id": "guild-1",
                        "content": "ping <@999>",
                        "author": {"id": "77", "username": "Bob", "bot": False},
                        "mentions": [{"id": "999"}],
                    }
                ],
            }
            with patch.object(adapter, "fetch_recent_messages", return_value=payload):
                first = adapter.poll_inbound_once()
                second = adapter.poll_inbound_once()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(events), 1)

    def test_voice_state_update_tracks_and_clears_membership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            joined = adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"}
            )
            current = adapter.get_user_voice_channel("g1", "u1")
            left = adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "u1", "channel_id": None}
            )
            cleared = adapter.get_user_voice_channel("g1", "u1")

        self.assertTrue(joined)
        self.assertEqual(current, "vc1")
        self.assertTrue(left)
        self.assertIsNone(cleared)

    def test_request_voice_autojoin_resolves_channel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")

        self.assertTrue(out["ok"])
        self.assertEqual(out["channel_id"], "vc1")

    def test_request_voice_autojoin_honors_single_active_guardrail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u2", "channel_id": "vc2"})
            first = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")
            second = adapter.request_voice_autojoin(guild_id="g1", user_id="u2")

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("single-active", second["reason"])


if __name__ == "__main__":
    unittest.main()
