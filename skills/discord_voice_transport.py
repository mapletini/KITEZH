"""skills/discord_voice_transport.py - Discord voice transport session manager.

This module tracks voice session intent/state for Discord voice channels.
It does not implement RTP/media yet; instead it provides a deterministic
join/release lifecycle that can be consumed by runtime orchestration and tests.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

import config


@dataclass
class VoiceSession:
    session_id: str
    guild_id: str
    channel_id: str
    requested_by: str
    state: str
    gateway_session_id: str
    voice_endpoint: str
    voice_token: str
    encryption_mode: str
    encryption_ready: bool
    created_at: int
    updated_at: int


class DiscordVoiceTransportManager:
    """Stateful manager for one active Discord voice session at a time."""

    def __init__(
        self,
        *,
        single_active_channel: bool = True,
        bot_user_id: str = "",
        self_mute: bool = False,
        self_deaf: bool = False,
    ) -> None:
        self._single_active_channel = bool(single_active_channel)
        self._bot_user_id = str(bot_user_id).strip()
        self._self_mute = bool(self_mute)
        self._self_deaf = bool(self_deaf)
        self._running = False
        self._active: VoiceSession | None = None
        self._pending_join: dict[str, Any] | None = None
        self._speech_runtime: Any | None = None
        self._media_transport: Any | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
        self._active = None
        self._pending_join = None

    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "single_active_channel": self._single_active_channel,
            "bot_user_id_configured": bool(self._bot_user_id),
            "speech_runtime_attached": self._speech_runtime is not None,
            "media_transport_attached": self._media_transport is not None,
            "media_transport": self._media_transport.status() if self._media_transport is not None else None,
            "pending_join": dict(self._pending_join) if self._pending_join is not None else None,
            "active": self._serialize(self._active),
        }

    def attach_speech_runtime(self, runtime: Any | None) -> None:
        self._speech_runtime = runtime

    def attach_media_transport(self, media_transport: Any | None) -> None:
        self._media_transport = media_transport

    def configure_media_encryption(self, *, secret_key: bytes | list[int] | str, mode: str | None = None) -> dict[str, Any]:
        if self._media_transport is None or not hasattr(self._media_transport, "configure_encryption"):
            return {"ok": False, "reason": "media transport does not support encryption configuration"}
        return self._media_transport.configure_encryption(secret_key=secret_key, mode=mode)

    def ensure_session(self, *, guild_id: str, channel_id: str, requested_by: str, reason: str) -> dict[str, Any]:
        if not self._running:
            return {"ok": False, "reason": "voice transport manager is not running"}

        guild = str(guild_id).strip()
        channel = str(channel_id).strip()
        actor = str(requested_by).strip() or "unknown"
        if not guild or not channel:
            return {"ok": False, "reason": "guild_id and channel_id are required"}

        now = int(time.time())
        if self._active is not None:
            if self._active.guild_id == guild and self._active.channel_id == channel:
                self._active.updated_at = now
                self._active.requested_by = actor
                if self._active.state in {"released", "failed"}:
                    self._active.state = "join_requested"
                self._pending_join = {
                    "guild_id": guild,
                    "channel_id": channel,
                    "self_mute": self._self_mute,
                    "self_deaf": self._self_deaf,
                }
                return {
                    "ok": True,
                    "reused": True,
                    "session": self._serialize(self._active),
                    "reason": reason,
                }
            if self._single_active_channel:
                return {
                    "ok": False,
                    "reason": (
                        "single-active voice transport guardrail active; "
                        f"existing channel {self._active.channel_id} must be released first"
                    ),
                }

        seed = f"{guild}:{channel}:{now}:{actor}"
        session_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        self._active = VoiceSession(
            session_id=session_id,
            guild_id=guild,
            channel_id=channel,
            requested_by=actor,
            state="join_requested",
            gateway_session_id="",
            voice_endpoint="",
            voice_token="",
            encryption_mode=(config.DISCORD_VOICE_ENCRYPTION_MODE or "xsalsa20_poly1305").strip() or "xsalsa20_poly1305",
            encryption_ready=((config.DISCORD_VOICE_ENCRYPTION_MODE or "xsalsa20_poly1305").strip().lower() == "none"),
            created_at=now,
            updated_at=now,
        )
        self._pending_join = {
            "guild_id": guild,
            "channel_id": channel,
            "self_mute": self._self_mute,
            "self_deaf": self._self_deaf,
        }
        return {
            "ok": True,
            "reused": False,
            "session": self._serialize(self._active),
            "reason": reason,
        }

    def release_session(self, *, guild_id: str, channel_id: str | None = None, reason: str = "") -> dict[str, Any]:
        guild = str(guild_id).strip()
        channel = str(channel_id).strip() if channel_id else ""
        if self._active is None:
            return {"ok": True, "released": False, "reason": "no active voice session"}
        if guild and self._active.guild_id != guild:
            return {"ok": False, "reason": "active session belongs to a different guild"}
        if channel and self._active.channel_id != channel:
            return {"ok": False, "reason": "active session belongs to a different channel"}

        released = self._serialize(self._active)
        if self._media_transport is not None and hasattr(self._media_transport, "stop_session"):
            self._media_transport.stop_session(reason=reason or "released")
        self._active = None
        self._pending_join = None
        return {"ok": True, "released": True, "session": released, "reason": reason or "released"}

    def pop_pending_voice_state_update(self) -> dict[str, Any] | None:
        with self._lock:
            if self._pending_join is None:
                return None
            out = dict(self._pending_join)
            self._pending_join = None
            return out

    def process_voice_state_update(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if self._active is None:
            return False

        guild_id = str(payload.get("guild_id", "")).strip()
        user_id = str(payload.get("user_id", "")).strip()
        raw_channel_id = payload.get("channel_id")
        channel_id = str(raw_channel_id).strip() if raw_channel_id is not None else ""
        session_id = str(payload.get("session_id", "")).strip()
        if not guild_id or guild_id != self._active.guild_id:
            return False

        if self._bot_user_id and user_id and user_id != self._bot_user_id:
            return False

        now = int(time.time())
        if not channel_id:
            self._active.state = "released"
            self._active.updated_at = now
            self._active.gateway_session_id = ""
            self._active.voice_endpoint = ""
            self._active.voice_token = ""
            self._active.encryption_ready = False
            if self._media_transport is not None and hasattr(self._media_transport, "stop_session"):
                self._media_transport.stop_session(reason="voice state left channel")
            return True

        if channel_id != self._active.channel_id:
            return False

        if session_id:
            self._active.gateway_session_id = session_id
        self._active.state = "awaiting_server_update"
        self._active.updated_at = now
        return True

    def process_voice_server_update(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        if self._active is None:
            return False

        guild_id = str(payload.get("guild_id", "")).strip()
        token = str(payload.get("token", "")).strip()
        endpoint = str(payload.get("endpoint", "")).strip()
        if not guild_id or guild_id != self._active.guild_id:
            return False
        if not token or not endpoint:
            return False

        self._active.voice_token = token
        self._active.voice_endpoint = endpoint
        self._active.updated_at = int(time.time())
        if self._active.gateway_session_id:
            self._active.state = "ready_for_udp"
        else:
            self._active.state = "awaiting_session_id"
        return True

    def process_voice_session_description(self, payload: dict[str, Any]) -> bool:
        """Apply voice encryption key/mode from a Discord session description payload."""
        if not isinstance(payload, dict):
            return False
        if self._active is None:
            return False

        mode = str(payload.get("mode") or self._active.encryption_mode or "xsalsa20_poly1305").strip()
        if not mode:
            mode = "xsalsa20_poly1305"

        # Discord voice session description delivers the symmetric key bytes.
        secret_key = payload.get("secret_key")
        self._active.encryption_mode = mode
        if mode.lower() == "none":
            self._active.encryption_ready = True
            if self._active.gateway_session_id and self._active.voice_endpoint:
                self._active.state = "ready_for_udp"
            elif self._active.gateway_session_id:
                self._active.state = "awaiting_server_update"
            elif self._active.voice_endpoint:
                self._active.state = "awaiting_session_id"
            self._active.updated_at = int(time.time())
            return True

        if secret_key is None:
            self._active.encryption_ready = False
            self._active.state = "awaiting_session_description"
            self._active.updated_at = int(time.time())
            return False

        out = self.configure_media_encryption(secret_key=secret_key, mode=mode)
        if not out.get("ok", False):
            self._active.encryption_ready = False
            self._active.state = "awaiting_session_description"
            self._active.updated_at = int(time.time())
            return False

        self._active.encryption_ready = True
        if self._active.gateway_session_id and self._active.voice_endpoint:
            self._active.state = "ready_for_udp"
        elif self._active.gateway_session_id:
            self._active.state = "awaiting_server_update"
        elif self._active.voice_endpoint:
            self._active.state = "awaiting_session_id"
        self._active.updated_at = int(time.time())
        return True

    def mark_voice_transport_active(self) -> bool:
        if self._active is None:
            return False
        if self._active.state not in {"ready_for_udp", "awaiting_server_update", "awaiting_session_id"}:
            return False
        if self._active.encryption_mode.lower() != "none" and not self._active.encryption_ready:
            self._active.state = "awaiting_session_description"
            self._active.updated_at = int(time.time())
            return False
        if self._media_transport is not None and hasattr(self._media_transport, "start_session"):
            out = self._media_transport.start_session(
                guild_id=self._active.guild_id,
                channel_id=self._active.channel_id,
                endpoint=self._active.voice_endpoint,
                token=self._active.voice_token,
                session_id=self._active.gateway_session_id,
            )
            if not out.get("ok", False):
                self._active.state = "failed"
                self._active.updated_at = int(time.time())
                return False
        self._active.state = "active"
        self._active.updated_at = int(time.time())
        return True

    def send_tts_audio_frame(self, *, samples: list[float], sample_rate: int = 48_000) -> dict[str, Any]:
        """Send one synthesized audio frame through the attached media transport."""
        if self._active is None or self._active.state != "active":
            return {"ok": False, "reason": "voice session is not active"}
        if self._media_transport is None or not hasattr(self._media_transport, "send_audio_frame"):
            return {"ok": False, "reason": "media transport is not configured"}
        return self._media_transport.send_audio_frame(samples=samples, sample_rate=sample_rate)

    def ingest_user_audio_frame(
        self,
        *,
        guild_id: str,
        channel_id: str,
        user_id: str,
        frame: list[float],
        speech_confidence: float,
        tts_active: bool,
    ) -> list[dict[str, Any]]:
        """Feed one user audio frame into the attached speech runtime.

        Returns normalized event dictionaries for downstream orchestration.
        """
        if self._speech_runtime is None or self._active is None:
            return []
        if self._active.state != "active":
            return []

        guild = str(guild_id).strip()
        channel = str(channel_id).strip()
        speaker = str(user_id).strip()
        if guild != self._active.guild_id or channel != self._active.channel_id or not speaker:
            return []

        events = self._speech_runtime.ingest_frame(
            frame=frame,
            speech_confidence=float(speech_confidence),
            tts_active=bool(tts_active),
        )
        normalized: list[dict[str, Any]] = []
        for event in events:
            normalized.append(
                {
                    "kind": getattr(event, "kind", "unknown"),
                    "text": getattr(event, "text", ""),
                    "confidence": float(getattr(event, "confidence", 0.0) or 0.0),
                    "guild_id": guild,
                    "channel_id": channel,
                    "user_id": speaker,
                }
            )
        return normalized

    @staticmethod
    def _serialize(session: VoiceSession | None) -> dict[str, Any] | None:
        if session is None:
            return None
        return {
            "session_id": session.session_id,
            "guild_id": session.guild_id,
            "channel_id": session.channel_id,
            "requested_by": session.requested_by,
            "state": session.state,
            "gateway_session_id": session.gateway_session_id,
            "voice_endpoint": session.voice_endpoint,
            "voice_token": session.voice_token,
            "encryption_mode": session.encryption_mode,
            "encryption_ready": session.encryption_ready,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }
