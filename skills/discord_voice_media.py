"""skills/discord_voice_media.py - Lightweight Discord voice UDP media transport.

Implements a minimal UDP socket transport with RTP-like framing for outgoing
PCM packets. This provides concrete socket I/O primitives while full Discord
voice encryption/secret-key negotiation remains a future layer.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass
from typing import Any, Callable

import config

try:
    from nacl.secret import SecretBox as _SecretBox
except Exception:  # pragma: no cover - optional dependency path
    _SecretBox = None


@dataclass
class VoiceMediaSession:
    guild_id: str
    channel_id: str
    endpoint_host: str
    endpoint_port: int
    token: str
    session_id: str
    started_at: int
    packets_sent: int = 0


class DiscordVoiceMediaTransport:
    """Concrete UDP transport for voice packet output."""

    def __init__(
        self,
        *,
        socket_factory: Callable[[int, int], socket.socket] | None = None,
        secret_box_factory: Callable[[bytes], Any] | None = None,
    ) -> None:
        self._socket_factory = socket_factory or socket.socket
        self._secret_box_factory = secret_box_factory or _SecretBox
        self._sock: socket.socket | None = None
        self._session: VoiceMediaSession | None = None
        self._seq = 0
        self._timestamp = 0
        self._encryption_mode = ""
        self._secret_box: Any | None = None

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._sock is not None and self._session is not None,
            "encryption_enabled": self._secret_box is not None,
            "encryption_mode": self._encryption_mode,
            "session": self._serialize_session(),
        }

    def configure_encryption(self, *, secret_key: bytes | list[int] | str, mode: str | None = None) -> dict[str, Any]:
        mode_value = (mode or config.DISCORD_VOICE_ENCRYPTION_MODE or "").strip() or "xsalsa20_poly1305"
        if mode_value.lower() == "none":
            self._secret_box = None
            self._encryption_mode = "none"
            return {"ok": True, "mode": self._encryption_mode}
        if mode_value != "xsalsa20_poly1305":
            return {"ok": False, "reason": f"unsupported encryption mode '{mode_value}'"}
        if self._secret_box_factory is None:
            return {"ok": False, "reason": "PyNaCl is unavailable; cannot configure voice encryption"}

        key_bytes = self._normalize_secret_key(secret_key)
        if len(key_bytes) != 32:
            return {"ok": False, "reason": "voice secret key must be exactly 32 bytes"}
        try:
            self._secret_box = self._secret_box_factory(key_bytes)
        except Exception as exc:
            return {"ok": False, "reason": f"failed to initialize encryption: {exc}"}

        self._encryption_mode = mode_value
        return {"ok": True, "mode": self._encryption_mode}

    def start_session(
        self,
        *,
        guild_id: str,
        channel_id: str,
        endpoint: str,
        token: str,
        session_id: str,
    ) -> dict[str, Any]:
        host, port = self._parse_endpoint(endpoint)
        if not host:
            return {"ok": False, "reason": "voice endpoint host is missing"}

        self.stop_session(reason="replacing prior session")

        sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(float(config.DISCORD_VOICE_UDP_CONNECT_TIMEOUT_SECONDS))
        try:
            sock.connect((host, port))
            sock.settimeout(float(config.DISCORD_VOICE_UDP_SEND_TIMEOUT_SECONDS))
        except OSError as exc:
            try:
                sock.close()
            except OSError:
                pass
            return {"ok": False, "reason": f"udp connect failed: {exc}"}

        self._sock = sock
        self._session = VoiceMediaSession(
            guild_id=str(guild_id).strip(),
            channel_id=str(channel_id).strip(),
            endpoint_host=host,
            endpoint_port=port,
            token=str(token).strip(),
            session_id=str(session_id).strip(),
            started_at=int(time.time()),
        )
        self._seq = 0
        self._timestamp = 0
        return {"ok": True, "reason": "udp media session started", "session": self._serialize_session()}

    def stop_session(self, *, reason: str = "stopped") -> dict[str, Any]:
        prev = self._serialize_session()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._session = None
        self._seq = 0
        self._timestamp = 0
        return {"ok": True, "reason": reason, "session": prev}

    def send_audio_frame(self, *, samples: list[float], sample_rate: int = 48_000) -> dict[str, Any]:
        if self._sock is None or self._session is None:
            return {"ok": False, "reason": "voice media session is not active"}
        if not samples:
            return {"ok": False, "reason": "audio frame is empty"}

        payload = self._float_to_pcm16(samples)
        header = self._build_rtp_header()
        encoded_payload = self._encode_payload(payload, header)
        packet = header + encoded_payload
        try:
            sent = self._sock.send(packet)
        except OSError as exc:
            return {"ok": False, "reason": f"udp send failed: {exc}"}

        self._session.packets_sent += 1
        self._seq = (self._seq + 1) % 65536
        self._timestamp = (self._timestamp + int((len(samples) / max(1, sample_rate)) * 48_000)) % (2**32)
        return {"ok": True, "bytes": sent, "packets_sent": self._session.packets_sent}

    @staticmethod
    def _parse_endpoint(endpoint: str) -> tuple[str, int]:
        raw = (endpoint or "").strip()
        if not raw:
            return "", int(config.DISCORD_VOICE_UDP_DEFAULT_PORT)
        if ":" in raw:
            host, port_text = raw.rsplit(":", 1)
            host = host.strip()
            try:
                port = int(port_text.strip())
            except ValueError:
                port = int(config.DISCORD_VOICE_UDP_DEFAULT_PORT)
            return host, max(1, min(65535, port))
        return raw, int(config.DISCORD_VOICE_UDP_DEFAULT_PORT)

    @staticmethod
    def _float_to_pcm16(samples: list[float]) -> bytes:
        ints = []
        for sample in samples:
            clamped = max(-1.0, min(1.0, float(sample)))
            ints.append(int(clamped * 32767.0))
        return struct.pack("<" + "h" * len(ints), *ints)

    def _build_rtp_header(self) -> bytes:
        version = 2
        padding = 0
        extension = 0
        csrc_count = 0
        marker = 0
        payload_type = int(config.DISCORD_VOICE_RTP_PAYLOAD_TYPE) & 0x7F
        b0 = (version << 6) | (padding << 5) | (extension << 4) | csrc_count
        b1 = (marker << 7) | payload_type
        return struct.pack(
            ">BBHII",
            b0,
            b1,
            self._seq,
            self._timestamp,
            1,  # placeholder SSRC until full Discord voice keying is implemented
        )

    def _encode_payload(self, payload: bytes, header: bytes) -> bytes:
        if self._secret_box is None:
            return payload
        # Discord xsalsa20_poly1305 uses the 12-byte RTP header + 12 zero bytes as nonce.
        nonce = header + (b"\x00" * 12)
        encrypted = self._secret_box.encrypt(payload, nonce)
        cipher = getattr(encrypted, "ciphertext", encrypted)
        return bytes(cipher)

    @staticmethod
    def _normalize_secret_key(secret_key: bytes | list[int] | str) -> bytes:
        if isinstance(secret_key, bytes):
            return secret_key
        if isinstance(secret_key, str):
            try:
                return bytes.fromhex(secret_key)
            except ValueError:
                return secret_key.encode("utf-8")
        if isinstance(secret_key, list):
            return bytes(int(x) & 0xFF for x in secret_key)
        return b""

    def _serialize_session(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        return {
            "guild_id": self._session.guild_id,
            "channel_id": self._session.channel_id,
            "endpoint_host": self._session.endpoint_host,
            "endpoint_port": self._session.endpoint_port,
            "session_id": self._session.session_id,
            "started_at": self._session.started_at,
            "packets_sent": self._session.packets_sent,
        }
