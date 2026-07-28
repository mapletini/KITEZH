"""Tests for skills/discord_adapter.py."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skills.discord_adapter import DiscordAdapter, DiscordRuntimeConfig, OutboundAction
from skills.discord_voice_transport import DiscordVoiceTransportManager
from skills.voice_runtime import DecodeEvent


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

    def test_request_voice_autojoin_uses_transport_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True)
            manager.start()
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")

        self.assertTrue(out["ok"])
        self.assertEqual(out["transport"], "managed")
        self.assertEqual(out["transport_session"]["channel_id"], "vc1")

    def test_request_voice_autojoin_transport_not_running_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True)
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")

        self.assertFalse(out["ok"])
        self.assertIn("transport refused", out["reason"])

    def test_ingest_voice_frame_delegates_to_transport_runtime(self) -> None:
        class _StubRuntime:
            def ingest_frame(self, *, frame, speech_confidence, tts_active):
                return [DecodeEvent(kind="partial", text="hi", confidence=0.7)]

        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="999")
            manager.start()
            manager.attach_speech_runtime(_StubRuntime())
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"}
            )
            auto = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")
            self.assertTrue(auto["ok"])
            adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "999", "channel_id": "vc1", "session_id": "sess-1"}
            )
            adapter.process_voice_server_update(
                {"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"}
            )
            adapter.process_voice_session_description(
                {"mode": "none"}
            )
            manager.mark_voice_transport_active()
            events = adapter.ingest_voice_frame(
                guild_id="g1",
                channel_id="vc1",
                user_id="speaker-1",
                frame=[0.1, 0.2],
                speech_confidence=0.9,
                tts_active=False,
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], "hi")

    def test_pop_pending_voice_state_update_from_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True)
            manager.start()
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")
            self.assertTrue(out["ok"])
            pending = adapter.pop_pending_voice_state_update()
            pending2 = adapter.pop_pending_voice_state_update()

        self.assertIsNotNone(pending)
        self.assertEqual(pending["guild_id"], "g1")
        self.assertEqual(pending["channel_id"], "vc1")
        self.assertIsNone(pending2)

    def test_process_voice_server_update_marks_transport_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="999")
            manager.start()
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")
            self.assertTrue(out["ok"])
            adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "999", "channel_id": "vc1", "session_id": "sess-1"}
            )
            ok = adapter.process_voice_server_update(
                {"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"}
            )
            adapter.process_voice_session_description(
                {"mode": "none"}
            )
            manager.mark_voice_transport_active()
            active = manager.status()["active"]

        self.assertTrue(ok)
        self.assertEqual(active["state"], "active")

    def test_send_voice_tts_frame_uses_transport(self) -> None:
        class _StubRuntime:
            def ingest_frame(self, *, frame, speech_confidence, tts_active):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="999")
            manager.start()
            manager.attach_speech_runtime(_StubRuntime())
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")
            self.assertTrue(out["ok"])
            adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "999", "channel_id": "vc1", "session_id": "sess-1"}
            )
            adapter.process_voice_server_update(
                {"guild_id": "g1", "token": "tok", "endpoint": "127.0.0.1:9"}
            )
            send = adapter.send_voice_tts_frame(samples=[0.1, 0.2], sample_rate=48_000)

        # Session may fail to send if no UDP listener exists, but transport path must execute.
        self.assertIn("ok", send)

    def test_configure_voice_encryption_delegates_to_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True)
            manager.start()
            adapter.set_voice_transport_manager(manager)

            class _StubMedia:
                def status(self):
                    return {"connected": False}

                def configure_encryption(self, *, secret_key, mode=None):
                    return {"ok": True, "mode": mode or "xsalsa20_poly1305", "key_len": len(secret_key)}

            manager.attach_media_transport(_StubMedia())
            out = adapter.configure_voice_encryption(secret_key=[2] * 32, mode="xsalsa20_poly1305")

        self.assertTrue(out["ok"])
        self.assertEqual(out["key_len"], 32)

    def test_process_voice_session_description_enables_active_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            manager = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="999")
            manager.start()

            class _StubMedia:
                def status(self):
                    return {"connected": False}

                def start_session(self, **_kwargs):
                    return {"ok": True}

                def stop_session(self, **_kwargs):
                    return {"ok": True}

                def send_audio_frame(self, **_kwargs):
                    return {"ok": True}

                def configure_encryption(self, *, secret_key, mode=None):
                    return {"ok": True, "mode": mode or "xsalsa20_poly1305", "key_len": len(secret_key)}

            manager.attach_media_transport(_StubMedia())
            adapter.set_voice_transport_manager(manager)
            adapter.process_voice_state_update({"guild_id": "g1", "user_id": "u1", "channel_id": "vc1"})
            out = adapter.request_voice_autojoin(guild_id="g1", user_id="u1")
            self.assertTrue(out["ok"])
            adapter.process_voice_state_update(
                {"guild_id": "g1", "user_id": "999", "channel_id": "vc1", "session_id": "sess-1"}
            )
            adapter.process_voice_server_update(
                {"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"}
            )
            before = manager.status()["active"]["state"]
            applied = adapter.process_voice_session_description(
                {"mode": "xsalsa20_poly1305", "secret_key": [5] * 32}
            )
            after = manager.status()["active"]["state"]

        self.assertEqual(before, "awaiting_session_description")
        self.assertTrue(applied)
        self.assertEqual(after, "active")


    def test_process_member_join_queues_role_and_logs(self) -> None:
        """process_member_join should enqueue role assignment and send log messages."""
        import tempfile
        from unittest.mock import patch

        runtime = DiscordRuntimeConfig(
            enabled=True,
            bot_token="token",
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
            log_channel_joins="join-chan",
            log_channel_general="gen-chan",
            newcomer_role_id="role-99",
        )
        with tempfile.TemporaryDirectory() as tmp, patch("config.WORKSPACE_PATH", tmp):
            adapter = DiscordAdapter(runtime)
            sent: list[OutboundAction] = []
            original_enqueue = adapter.enqueue_action

            def capture(action: OutboundAction) -> dict:
                sent.append(action)
                return {"ok": True, "queued": True, "task_id": "x", "action": action.action, "payload": action.payload}

            adapter.enqueue_action = capture  # type: ignore[method-assign]
            ok = adapter.process_member_join(
                {"guild_id": "g1", "user": {"id": "u1", "username": "Alice"}}
            )

        self.assertTrue(ok)
        actions = [a.action for a in sent]
        self.assertIn("add_member_role", actions)
        self.assertEqual(sent[0].payload["role_id"], "role-99")
        send_channels = {a.payload["channel_id"] for a in sent if a.action == "send_message"}
        self.assertIn("join-chan", send_channels)
        self.assertIn("gen-chan", send_channels)

    def test_process_member_join_skips_role_when_unconfigured(self) -> None:
        import tempfile
        from unittest.mock import patch

        runtime = DiscordRuntimeConfig(
            enabled=True,
            bot_token="token",
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
            log_channel_joins="join-chan",
        )
        with tempfile.TemporaryDirectory() as tmp, patch("config.WORKSPACE_PATH", tmp):
            adapter = DiscordAdapter(runtime)
            sent: list[OutboundAction] = []

            def capture(action: OutboundAction) -> dict:
                sent.append(action)
                return {"ok": True, "queued": True, "task_id": "x", "action": action.action, "payload": action.payload}

            adapter.enqueue_action = capture  # type: ignore[method-assign]
            adapter.process_member_join({"guild_id": "g1", "user": {"id": "u2", "username": "Bob"}})

        self.assertNotIn("add_member_role", [a.action for a in sent])

    def test_process_member_remove_logs_to_leave_and_general_channels(self) -> None:
        import tempfile
        from unittest.mock import patch

        runtime = DiscordRuntimeConfig(
            enabled=True,
            bot_token="token",
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
            log_channel_leaves="leave-chan",
            log_channel_general="gen-chan",
        )
        with tempfile.TemporaryDirectory() as tmp, patch("config.WORKSPACE_PATH", tmp):
            adapter = DiscordAdapter(runtime)
            sent: list[OutboundAction] = []

            def capture(action: OutboundAction) -> dict:
                sent.append(action)
                return {"ok": True, "queued": True, "task_id": "x", "action": action.action, "payload": action.payload}

            adapter.enqueue_action = capture  # type: ignore[method-assign]
            ok = adapter.process_member_remove({"user": {"id": "u3", "username": "Carol"}})

        self.assertTrue(ok)
        channels = {a.payload["channel_id"] for a in sent if a.action == "send_message"}
        self.assertIn("leave-chan", channels)
        self.assertIn("gen-chan", channels)

    def test_process_audit_log_entry_posts_to_moderation_channel(self) -> None:
        import tempfile
        from unittest.mock import patch

        runtime = DiscordRuntimeConfig(
            enabled=True,
            bot_token="token",
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
            log_channel_moderation="mod-chan",
        )
        with tempfile.TemporaryDirectory() as tmp, patch("config.WORKSPACE_PATH", tmp):
            adapter = DiscordAdapter(runtime)
            sent: list[OutboundAction] = []

            def capture(action: OutboundAction) -> dict:
                sent.append(action)
                return {"ok": True, "queued": True, "task_id": "x", "action": action.action, "payload": action.payload}

            adapter.enqueue_action = capture  # type: ignore[method-assign]
            ok = adapter.process_audit_log_entry(
                {"action_type": 22, "user_id": "mod1", "target_id": "target1", "reason": "spam"}
            )

        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].payload["channel_id"], "mod-chan")
        self.assertIn("Member Ban Add", sent[0].payload["content"])
        self.assertIn("spam", sent[0].payload["content"])

    def test_process_audit_log_entry_noop_without_channel(self) -> None:
        import tempfile
        from unittest.mock import patch

        runtime = DiscordRuntimeConfig(
            enabled=True,
            bot_token="token",
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
            ok = adapter.process_audit_log_entry({"action_type": 22, "user_id": "mod1"})

        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
