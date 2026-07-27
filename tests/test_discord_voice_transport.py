"""Tests for skills/discord_voice_transport.py."""

import unittest

from skills.discord_voice_transport import DiscordVoiceTransportManager
from skills.voice_runtime import DecodeEvent


class TestDiscordVoiceTransportManager(unittest.TestCase):
    def test_ensure_session_requires_running(self) -> None:
        mgr = DiscordVoiceTransportManager(single_active_channel=True)
        out = mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="test")
        self.assertFalse(out["ok"])
        self.assertIn("not running", out["reason"])

    def test_ensure_session_creates_and_reuses(self) -> None:
        mgr = DiscordVoiceTransportManager(single_active_channel=True)
        mgr.start()
        first = mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        second = mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u2", reason="mention")

        self.assertTrue(first["ok"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["reused"])

    def test_single_active_guardrail_blocks_second_channel(self) -> None:
        mgr = DiscordVoiceTransportManager(single_active_channel=True)
        mgr.start()
        first = mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        second = mgr.ensure_session(guild_id="g1", channel_id="vc2", requested_by="u2", reason="mention")

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("single-active", second["reason"])

    def test_release_session_clears_active(self) -> None:
        mgr = DiscordVoiceTransportManager(single_active_channel=True)
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        out = mgr.release_session(guild_id="g1", channel_id="vc1", reason="done")

        self.assertTrue(out["ok"])
        self.assertTrue(out["released"])
        self.assertIsNone(mgr.status()["active"])

    def test_pending_voice_state_update_emitted_once(self) -> None:
        mgr = DiscordVoiceTransportManager(single_active_channel=True, self_mute=True, self_deaf=False)
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")

        first = mgr.pop_pending_voice_state_update()
        second = mgr.pop_pending_voice_state_update()

        self.assertEqual(first["guild_id"], "g1")
        self.assertEqual(first["channel_id"], "vc1")
        self.assertTrue(first["self_mute"])
        self.assertFalse(first["self_deaf"])
        self.assertIsNone(second)

    def test_voice_signaling_progression_to_ready_for_udp(self) -> None:
        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        mgr.start()
        created = mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        self.assertEqual(created["session"]["state"], "join_requested")

        state_ok = mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-123",
            }
        )
        server_ok = mgr.process_voice_server_update(
            {
                "guild_id": "g1",
                "token": "tok",
                "endpoint": "voice.discord.media",
            }
        )
        active = mgr.status()["active"]

        self.assertTrue(state_ok)
        self.assertTrue(server_ok)
        self.assertEqual(active["gateway_session_id"], "sess-123")
        self.assertEqual(active["voice_token"], "tok")
        self.assertEqual(active["state"], "ready_for_udp")

    def test_ingest_user_audio_frame_emits_runtime_events(self) -> None:
        class _StubRuntime:
            def ingest_frame(self, *, frame, speech_confidence, tts_active):
                return [DecodeEvent(kind="partial", text="hello", confidence=0.8)]

        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        mgr.start()
        mgr.attach_speech_runtime(_StubRuntime())
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-1",
            }
        )
        mgr.process_voice_server_update({"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"})
        mgr.process_voice_session_description({"mode": "none"})
        mgr.mark_voice_transport_active()

        events = mgr.ingest_user_audio_frame(
            guild_id="g1",
            channel_id="vc1",
            user_id="user-7",
            frame=[0.1, 0.2],
            speech_confidence=0.9,
            tts_active=False,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "partial")
        self.assertEqual(events[0]["text"], "hello")

    def test_ingest_user_audio_frame_ignores_wrong_channel(self) -> None:
        class _StubRuntime:
            def ingest_frame(self, *, frame, speech_confidence, tts_active):
                return [DecodeEvent(kind="partial", text="hello", confidence=0.8)]

        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        mgr.start()
        mgr.attach_speech_runtime(_StubRuntime())
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-1",
            }
        )
        mgr.process_voice_server_update({"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"})
        mgr.process_voice_session_description({"mode": "none"})
        mgr.mark_voice_transport_active()

        events = mgr.ingest_user_audio_frame(
            guild_id="g1",
            channel_id="vc-other",
            user_id="user-7",
            frame=[0.1, 0.2],
            speech_confidence=0.9,
            tts_active=False,
        )

        self.assertEqual(events, [])

    def test_mark_active_starts_media_transport(self) -> None:
        class _StubMedia:
            def __init__(self):
                self.started = 0

            def status(self):
                return {"connected": self.started > 0}

            def start_session(self, **_kwargs):
                self.started += 1
                return {"ok": True}

            def stop_session(self, **_kwargs):
                return {"ok": True}

            def send_audio_frame(self, **_kwargs):
                return {"ok": True, "bytes": 10}

            def configure_encryption(self, *, secret_key, mode=None):
                return {"ok": True, "mode": mode or "xsalsa20_poly1305", "key_len": len(secret_key)}

        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        media = _StubMedia()
        mgr.attach_media_transport(media)
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-1",
            }
        )
        mgr.process_voice_server_update({"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"})
        mgr.process_voice_session_description({"mode": "xsalsa20_poly1305", "secret_key": [3] * 32})

        ok = mgr.mark_voice_transport_active()

        self.assertTrue(ok)
        self.assertEqual(media.started, 1)

    def test_release_session_stops_media_transport(self) -> None:
        class _StubMedia:
            def __init__(self):
                self.stopped = 0

            def status(self):
                return {"connected": False}

            def start_session(self, **_kwargs):
                return {"ok": True}

            def stop_session(self, **_kwargs):
                self.stopped += 1
                return {"ok": True}

            def send_audio_frame(self, **_kwargs):
                return {"ok": True}

        mgr = DiscordVoiceTransportManager(single_active_channel=True)
        media = _StubMedia()
        mgr.attach_media_transport(media)
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")

        out = mgr.release_session(guild_id="g1", channel_id="vc1", reason="done")

        self.assertTrue(out["ok"])
        self.assertEqual(media.stopped, 1)

    def test_send_tts_audio_frame_uses_media_transport(self) -> None:
        class _StubMedia:
            def status(self):
                return {"connected": True}

            def start_session(self, **_kwargs):
                return {"ok": True}

            def stop_session(self, **_kwargs):
                return {"ok": True}

            def send_audio_frame(self, **_kwargs):
                return {"ok": True, "bytes": 42}

            def configure_encryption(self, *, secret_key, mode=None):
                return {"ok": True, "mode": mode or "xsalsa20_poly1305", "key_len": len(secret_key)}

        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        mgr.attach_media_transport(_StubMedia())
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-1",
            }
        )
        mgr.process_voice_server_update({"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"})
        mgr.process_voice_session_description({"mode": "xsalsa20_poly1305", "secret_key": [4] * 32})
        mgr.mark_voice_transport_active()

        out = mgr.send_tts_audio_frame(samples=[0.1, 0.2], sample_rate=48_000)

        self.assertTrue(out["ok"])
        self.assertEqual(out["bytes"], 42)

    def test_configure_media_encryption_delegates_to_transport(self) -> None:
        class _StubMedia:
            def status(self):
                return {"connected": False}

            def configure_encryption(self, *, secret_key, mode=None):
                return {"ok": True, "mode": mode or "xsalsa20_poly1305", "key_len": len(secret_key)}

        mgr = DiscordVoiceTransportManager(single_active_channel=True)
        mgr.attach_media_transport(_StubMedia())
        out = mgr.configure_media_encryption(secret_key=[1] * 32, mode="xsalsa20_poly1305")

        self.assertTrue(out["ok"])
        self.assertEqual(out["mode"], "xsalsa20_poly1305")
        self.assertEqual(out["key_len"], 32)

    def test_mark_active_requires_session_description_when_encryption_enabled(self) -> None:
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

        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        mgr.attach_media_transport(_StubMedia())
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-1",
            }
        )
        mgr.process_voice_server_update({"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"})

        before = mgr.mark_voice_transport_active()
        applied = mgr.process_voice_session_description({"mode": "xsalsa20_poly1305", "secret_key": [1] * 32})
        after = mgr.mark_voice_transport_active()

        self.assertFalse(before)
        self.assertTrue(applied)
        self.assertTrue(after)

    def test_process_session_description_none_mode_allows_activation(self) -> None:
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
                return {"ok": True, "mode": mode or "none"}

        mgr = DiscordVoiceTransportManager(single_active_channel=True, bot_user_id="bot1")
        mgr.attach_media_transport(_StubMedia())
        mgr.start()
        mgr.ensure_session(guild_id="g1", channel_id="vc1", requested_by="u1", reason="mention")
        mgr.process_voice_state_update(
            {
                "guild_id": "g1",
                "user_id": "bot1",
                "channel_id": "vc1",
                "session_id": "sess-1",
            }
        )
        mgr.process_voice_server_update({"guild_id": "g1", "token": "tok", "endpoint": "voice.discord.media"})

        applied = mgr.process_voice_session_description({"mode": "none"})
        active = mgr.mark_voice_transport_active()

        self.assertTrue(applied)
        self.assertTrue(active)


if __name__ == "__main__":
    unittest.main()
