"""Tests for skills/discord_voice_media.py."""

import unittest

from skills.discord_voice_media import DiscordVoiceMediaTransport


class _FakeSocket:
    def __init__(self) -> None:
        self.connected_to = None
        self.timeout_values = []
        self.sent_packets: list[bytes] = []
        self.closed = False

    def settimeout(self, value):
        self.timeout_values.append(value)

    def connect(self, target):
        self.connected_to = target

    def send(self, payload: bytes) -> int:
        self.sent_packets.append(payload)
        return len(payload)

    def close(self):
        self.closed = True


class _FakeCiphertext:
    def __init__(self, ciphertext: bytes) -> None:
        self.ciphertext = ciphertext


class _FakeSecretBox:
    def __init__(self, key: bytes) -> None:
        self.key = key
        self.last_nonce = b""

    def encrypt(self, payload: bytes, nonce: bytes):
        self.last_nonce = nonce
        return _FakeCiphertext(b"ENC" + payload)


class TestDiscordVoiceMediaTransport(unittest.TestCase):
    def test_start_send_stop_session(self) -> None:
        fake = _FakeSocket()
        media = DiscordVoiceMediaTransport(socket_factory=lambda *_args: fake)

        started = media.start_session(
            guild_id="g1",
            channel_id="vc1",
            endpoint="voice.discord.media:50000",
            token="tok",
            session_id="sess-1",
        )
        sent = media.send_audio_frame(samples=[0.0, 0.5, -0.5], sample_rate=48_000)
        stopped = media.stop_session(reason="done")

        self.assertTrue(started["ok"])
        self.assertTrue(sent["ok"])
        self.assertGreater(sent["bytes"], 12)
        self.assertTrue(stopped["ok"])
        self.assertTrue(fake.closed)

    def test_send_fails_without_session(self) -> None:
        media = DiscordVoiceMediaTransport(socket_factory=lambda *_args: _FakeSocket())
        out = media.send_audio_frame(samples=[0.1], sample_rate=48_000)
        self.assertFalse(out["ok"])

    def test_endpoint_without_port_uses_default(self) -> None:
        fake = _FakeSocket()
        media = DiscordVoiceMediaTransport(socket_factory=lambda *_args: fake)
        started = media.start_session(
            guild_id="g1",
            channel_id="vc1",
            endpoint="voice.discord.media",
            token="tok",
            session_id="sess-1",
        )
        self.assertTrue(started["ok"])
        self.assertEqual(fake.connected_to[0], "voice.discord.media")
        self.assertIsInstance(fake.connected_to[1], int)

    def test_configure_encryption_and_send_uses_ciphertext(self) -> None:
        fake_socket = _FakeSocket()
        boxes: list[_FakeSecretBox] = []

        def _factory(key: bytes):
            box = _FakeSecretBox(key)
            boxes.append(box)
            return box

        media = DiscordVoiceMediaTransport(socket_factory=lambda *_args: fake_socket, secret_box_factory=_factory)
        configured = media.configure_encryption(secret_key=[7] * 32, mode="xsalsa20_poly1305")
        self.assertTrue(configured["ok"])

        media.start_session(
            guild_id="g1",
            channel_id="vc1",
            endpoint="voice.discord.media:50000",
            token="tok",
            session_id="sess-1",
        )
        sent = media.send_audio_frame(samples=[0.1, -0.1], sample_rate=48_000)

        self.assertTrue(sent["ok"])
        packet = fake_socket.sent_packets[-1]
        self.assertTrue(packet[12:].startswith(b"ENC"))
        self.assertEqual(boxes[0].last_nonce[:12], packet[:12])

    def test_configure_encryption_rejects_invalid_key_length(self) -> None:
        media = DiscordVoiceMediaTransport(socket_factory=lambda *_args: _FakeSocket(), secret_box_factory=_FakeSecretBox)
        out = media.configure_encryption(secret_key=[1, 2, 3], mode="xsalsa20_poly1305")
        self.assertFalse(out["ok"])
        self.assertIn("32 bytes", out["reason"])


if __name__ == "__main__":
    unittest.main()
