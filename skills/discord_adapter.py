"""skills/discord_adapter.py - Discord runtime adapter with queue/audit/token gating.

Implements a lightweight Discord REST adapter without hard dependency on a
Discord SDK. Outbound actions are queued and processed by a worker thread with
retry/backoff. Privileged actions are gated by one-time approval tokens that
are both HMAC-signed and persisted in SQLite for auditability.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import random
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

import config
from skills.custom_stt_engine import CustomSpeechEngine

logger = logging.getLogger(__name__)

_REST_BASE = "https://discord.com/api/v10"
_MAX_DISCORD_MESSAGE_CHARS = 2000
_BOT_USER_AGENT = "DiscordBot (https://github.com/mapletini/KITEZH, 1.0)"

# Regex for extracting a user ID from a Discord mention like <@123> or <@!123>.
_MENTION_RE = re.compile(r"<@!?(\d+)>")

# Human-readable labels for Discord audit log action types that represent
# moderation events (see https://discord.com/developers/docs/resources/audit-log).
_AUDIT_ACTION_NAMES: dict[int, str] = {
    20: "Member Kick",
    21: "Member Prune",
    22: "Member Ban Add",
    23: "Member Ban Remove",
    24: "Member Update",
    25: "Member Role Update",
    26: "Member Move",
    27: "Member Disconnect",
    28: "Bot Add",
}

# Audit events where target_id is expected to be a user/member ID.
_AUDIT_USER_TARGET_ACTIONS: set[int] = {
    20,  # Member Kick
    22,  # Member Ban Add
    23,  # Member Ban Remove
    24,  # Member Update
    25,  # Member Role Update
    26,  # Member Move
    27,  # Member Disconnect
}


def _display_name(user: dict[str, Any]) -> str:
    """Return the best available display name from a Discord user object."""
    return str(user.get("global_name") or user.get("username") or "Unknown User")


def _snowflake_or_empty(value: Any) -> str:
    """Return a valid Discord snowflake string or an empty string."""
    text = str(value or "").strip()
    return text if text.isdigit() else ""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True)
class DiscordRuntimeConfig:
    enabled: bool
    bot_token: str
    operator_user_id: str
    recent_messages_limit: int
    max_send_attempts: int
    retry_base_seconds: float
    retry_max_seconds: float
    inbound_poll_seconds: float
    inbound_channel_ids: tuple[str, ...]
    bot_user_id: str
    gateway_enabled: bool
    voice_enabled: bool
    voice_autojoin_on_mention: bool
    voice_single_active_channel: bool
    voice_buffer_seconds: int
    audit_retention_days: int
    signing_secret: str
    log_channel_moderation: str = ""
    log_channel_general: str = ""
    log_channel_joins: str = ""
    log_channel_leaves: str = ""
    newcomer_role_id: str = ""
    ignored_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutboundAction:
    action: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class QueuedAction:
    task_id: str
    action: OutboundAction
    attempts: int = 0


class DiscordAdapter:
    """Thin runtime adapter boundary for Discord operations.

    The implementation is intentionally minimal in this first slice so the
    rest of the codebase can depend on a stable interface while voice and full
    Discord transport are implemented incrementally.
    """

    def __init__(self, runtime: DiscordRuntimeConfig) -> None:
        self._runtime = runtime
        self._queue: Queue[QueuedAction] = Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._inbound_worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seen_message_ids: set[str] = set()
        self._inbound_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._mention_handlers: list[Callable[[dict[str, Any]], None]] = []
        self._voice_presence_by_user: dict[tuple[str, str], str] = {}
        self._active_voice_guild_id = ""
        self._active_voice_channel_id = ""
        self._voice_transport: Any | None = None
        self._voice_runtime_available = True
        self._voice_disable_reason = ""
        self._db_path = Path(config.WORKSPACE_PATH) / "discord_runtime.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # Register built-in mod command handler so it runs for all inbound messages.
        self.register_inbound_handler(self._handle_mod_command)

    @classmethod
    def from_config(cls) -> "DiscordAdapter":
        return cls(
            DiscordRuntimeConfig(
                enabled=config.DISCORD_ENABLED,
                bot_token=config.DISCORD_BOT_TOKEN,
                operator_user_id=config.DISCORD_OPERATOR_USER_ID,
                recent_messages_limit=config.DISCORD_RECENT_MESSAGES_LIMIT,
                max_send_attempts=config.DISCORD_SEND_MAX_ATTEMPTS,
                retry_base_seconds=config.DISCORD_SEND_RETRY_BASE_SECONDS,
                retry_max_seconds=config.DISCORD_SEND_RETRY_MAX_SECONDS,
                inbound_poll_seconds=config.DISCORD_INBOUND_POLL_SECONDS,
                inbound_channel_ids=config.DISCORD_INBOUND_CHANNEL_IDS,
                bot_user_id=config.DISCORD_BOT_USER_ID,
                ignored_user_ids=config.DISCORD_IGNORED_USER_IDS,
                gateway_enabled=config.DISCORD_GATEWAY_ENABLED,
                voice_enabled=config.DISCORD_VOICE_ENABLED,
                voice_autojoin_on_mention=config.DISCORD_VOICE_AUTOJOIN_ON_MENTION,
                voice_single_active_channel=config.DISCORD_VOICE_SINGLE_ACTIVE_CHANNEL,
                voice_buffer_seconds=config.DISCORD_VOICE_BUFFER_SECONDS,
                audit_retention_days=config.DISCORD_AUDIT_RETENTION_DAYS,
                signing_secret=config.COMMAND_SIGNING_SECRET,
                log_channel_moderation=config.DISCORD_LOG_CHANNEL_MODERATION,
                log_channel_general=config.DISCORD_LOG_CHANNEL_GENERAL,
                log_channel_joins=config.DISCORD_LOG_CHANNEL_JOINS,
                log_channel_leaves=config.DISCORD_LOG_CHANNEL_LEAVES,
                newcomer_role_id=config.DISCORD_NEWCOMER_ROLE_ID,
            )
        )

    def is_enabled(self) -> bool:
        return self._runtime.enabled and bool(self._runtime.bot_token)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._runtime.enabled,
            "configured": bool(self._runtime.bot_token),
            "recent_messages_limit": self._runtime.recent_messages_limit,
            "inbound_poll_seconds": self._runtime.inbound_poll_seconds,
            "inbound_channel_ids": list(self._runtime.inbound_channel_ids),
            "inbound_active": bool(self._inbound_worker and self._inbound_worker.is_alive()),
            "gateway_enabled": self._runtime.gateway_enabled,
            "max_send_attempts": self._runtime.max_send_attempts,
            "voice_enabled": self._runtime.voice_enabled,
            "voice_runtime_available": self._voice_runtime_available,
            "voice_disable_reason": self._voice_disable_reason,
            "voice_autojoin_on_mention": self._runtime.voice_autojoin_on_mention,
            "voice_single_active_channel": self._runtime.voice_single_active_channel,
            "voice_buffer_seconds": self._runtime.voice_buffer_seconds,
            "voice_frame_ms": config.DISCORD_VOICE_FRAME_MS,
            "voice_decode_interval_ms": config.DISCORD_VOICE_DECODE_INTERVAL_MS,
            "voice_endpoint_silence_ms": config.DISCORD_VOICE_ENDPOINT_SILENCE_MS,
            "voice_bargein_confidence": config.DISCORD_VOICE_BARGEIN_CONFIDENCE,
            "voice_decode_warn_ms": config.DISCORD_VOICE_DECODE_WARN_MS,
            "active_voice_guild_id": self._active_voice_guild_id,
            "active_voice_channel_id": self._active_voice_channel_id,
            "tracked_voice_members": len(self._voice_presence_by_user),
            "voice_transport": self._voice_transport.status() if self._voice_transport is not None else None,
            "queue_size": self._queue.qsize(),
            "audit_retention_days": self._runtime.audit_retention_days,
        }

    def start(self) -> None:
        if not self.is_enabled():
            return
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run_worker, name="discord-outbound", daemon=True)
        self._worker.start()
        if self._runtime.inbound_channel_ids and not self._runtime.gateway_enabled:
            self._inbound_worker = threading.Thread(
                target=self._run_inbound_worker,
                name="discord-inbound",
                daemon=True,
            )
            self._inbound_worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
        if self._inbound_worker:
            self._inbound_worker.join(timeout=2.0)
        self._cleanup_expired_approvals()
        self._cleanup_old_audit_rows()

    def register_inbound_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._inbound_handlers.append(handler)

    def register_mention_handler(self, handler: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            self._mention_handlers.append(handler)

    def set_voice_transport_manager(self, manager: Any | None) -> None:
        self._voice_transport = manager

    def poll_inbound_once(self) -> int:
        """Poll configured channels once and emit normalized message events."""
        if not self._runtime.inbound_channel_ids:
            return 0
        emitted = 0
        for channel_id in self._runtime.inbound_channel_ids:
            emitted += self._poll_channel(channel_id)
        return emitted

    def validate_voice_runtime(self) -> dict[str, Any]:
        """Validate that voice runtime dependencies are available."""
        if not self._runtime.voice_enabled:
            self._voice_runtime_available = False
            self._voice_disable_reason = "voice disabled by configuration"
            return {"ok": True, "enabled": False, "reason": self._voice_disable_reason}

        if not self.is_enabled():
            self._voice_runtime_available = False
            self._voice_disable_reason = "discord adapter is not enabled/configured"
            return {"ok": False, "enabled": True, "reason": self._voice_disable_reason}

        stt_health = CustomSpeechEngine.from_config().health()
        if not stt_health.ready:
            self._voice_runtime_available = False
            self._voice_disable_reason = stt_health.reason
            return {"ok": False, "enabled": True, "reason": stt_health.reason}

        self._voice_runtime_available = True
        self._voice_disable_reason = ""
        return {"ok": True, "enabled": True, "reason": "ok"}

    def disable_voice(self, reason: str) -> None:
        self._voice_runtime_available = False
        self._voice_disable_reason = reason

    def process_voice_state_update(self, payload: dict[str, Any]) -> bool:
        """Track user->voice channel mapping from Discord VOICE_STATE_UPDATE events."""
        if not isinstance(payload, dict):
            return False
        guild_id = str(payload.get("guild_id", "")).strip()
        user_id = str(payload.get("user_id", "")).strip()
        raw_channel_id = payload.get("channel_id")
        channel_id = str(raw_channel_id).strip() if raw_channel_id is not None else ""
        if not guild_id or not user_id:
            return False

        key = (guild_id, user_id)
        delegated = False
        if self._voice_transport is not None and hasattr(self._voice_transport, "process_voice_state_update"):
            try:
                delegated = bool(self._voice_transport.process_voice_state_update(payload))
            except Exception:
                delegated = False
        with self._lock:
            if channel_id:
                self._voice_presence_by_user[key] = channel_id
            else:
                self._voice_presence_by_user.pop(key, None)
                if self._active_voice_guild_id == guild_id and not any(
                    gid == guild_id and cid == self._active_voice_channel_id
                    for (gid, _uid), cid in self._voice_presence_by_user.items()
                ):
                    self._active_voice_guild_id = ""
                    self._active_voice_channel_id = ""
        return True

    def process_voice_server_update(self, payload: dict[str, Any]) -> bool:
        """Forward VOICE_SERVER_UPDATE payloads to voice transport signaling state."""
        if self._voice_transport is None or not hasattr(self._voice_transport, "process_voice_server_update"):
            return False
        try:
            ok = bool(self._voice_transport.process_voice_server_update(payload))
            if ok and hasattr(self._voice_transport, "mark_voice_transport_active"):
                self._voice_transport.mark_voice_transport_active()
            return ok
        except Exception:
            return False

    def process_voice_session_description(self, payload: dict[str, Any]) -> bool:
        """Forward voice session description (mode + secret_key) to transport and retry activation."""
        if self._voice_transport is None or not hasattr(self._voice_transport, "process_voice_session_description"):
            return False
        try:
            ok = bool(self._voice_transport.process_voice_session_description(payload))
            if ok and hasattr(self._voice_transport, "mark_voice_transport_active"):
                self._voice_transport.mark_voice_transport_active()
            return ok
        except Exception:
            return False

    def pop_pending_voice_state_update(self) -> dict[str, Any] | None:
        """Return one pending transport-originated OP4 voice state update, if any."""
        if self._voice_transport is None or not hasattr(self._voice_transport, "pop_pending_voice_state_update"):
            return None
        try:
            pending = self._voice_transport.pop_pending_voice_state_update()
            return pending if isinstance(pending, dict) else None
        except Exception:
            return None

    def ingest_voice_frame(
        self,
        *,
        guild_id: str,
        channel_id: str,
        user_id: str,
        frame: list[float],
        speech_confidence: float,
        tts_active: bool,
    ) -> list[dict[str, Any]]:
        """Pass an inbound voice frame into transport/runtime decoding when configured."""
        if self._voice_transport is None or not hasattr(self._voice_transport, "ingest_user_audio_frame"):
            return []
        try:
            return list(
                self._voice_transport.ingest_user_audio_frame(
                    guild_id=guild_id,
                    channel_id=channel_id,
                    user_id=user_id,
                    frame=frame,
                    speech_confidence=speech_confidence,
                    tts_active=tts_active,
                )
            )
        except Exception:
            return []

    def send_voice_tts_frame(self, *, samples: list[float], sample_rate: int = 48_000) -> dict[str, Any]:
        """Send one synthesized voice frame through the active transport session."""
        if self._voice_transport is None or not hasattr(self._voice_transport, "send_tts_audio_frame"):
            return {"ok": False, "reason": "voice transport is not configured"}
        try:
            return dict(self._voice_transport.send_tts_audio_frame(samples=samples, sample_rate=sample_rate))
        except Exception as exc:
            return {"ok": False, "reason": f"voice transport send failed: {exc}"}

    def configure_voice_encryption(self, *, secret_key: bytes | list[int] | str, mode: str | None = None) -> dict[str, Any]:
        """Configure media payload encryption for active/future voice sessions."""
        if self._voice_transport is None or not hasattr(self._voice_transport, "configure_media_encryption"):
            return {"ok": False, "reason": "voice transport does not support encryption"}
        try:
            return dict(self._voice_transport.configure_media_encryption(secret_key=secret_key, mode=mode))
        except Exception as exc:
            return {"ok": False, "reason": f"voice encryption configuration failed: {exc}"}

    def get_user_voice_channel(self, guild_id: str, user_id: str) -> str | None:
        key = (str(guild_id).strip(), str(user_id).strip())
        if not key[0] or not key[1]:
            return None
        with self._lock:
            return self._voice_presence_by_user.get(key)

    def request_voice_autojoin(self, *, guild_id: str, user_id: str) -> dict[str, Any]:
        """Resolve and reserve a voice channel join target for a mention-triggered request."""
        if not self._runtime.voice_enabled:
            return {"ok": False, "reason": "voice disabled by configuration"}
        if not self._voice_runtime_available:
            return {"ok": False, "reason": self._voice_disable_reason or "voice runtime unavailable"}

        guild = str(guild_id).strip()
        user = str(user_id).strip()
        channel_id = self.get_user_voice_channel(guild, user)
        if not channel_id:
            return {"ok": False, "reason": "user is not currently in a tracked voice channel"}

        with self._lock:
            if (
                self._runtime.voice_single_active_channel
                and self._active_voice_channel_id
                and self._active_voice_channel_id != channel_id
            ):
                return {
                    "ok": False,
                    "reason": (
                        "single-active voice guardrail active; existing channel "
                        f"{self._active_voice_channel_id} must be released first"
                    ),
                }
            self._active_voice_guild_id = guild
            self._active_voice_channel_id = channel_id

        result: dict[str, Any] = {
            "ok": True,
            "guild_id": guild,
            "channel_id": channel_id,
            "transport": "pending",
            "reason": "voice join target resolved",
        }
        if self._voice_transport is not None:
            transport_out = self._voice_transport.ensure_session(
                guild_id=guild,
                channel_id=channel_id,
                requested_by=user,
                reason="mention_autojoin",
            )
            if not transport_out.get("ok", False):
                return {
                    "ok": False,
                    "reason": f"voice transport refused session: {transport_out.get('reason', 'unknown reason')}",
                }
            result["transport"] = "managed"
            result["transport_session"] = transport_out.get("session")
        return result

    def issue_approval_token(self, *, action_type: str, actor: str, ttl_seconds: int = 300) -> str:
        now = int(time.time())
        nonce = hashlib.sha256(f"{actor}:{action_type}:{now}:{random.random()}".encode("utf-8")).hexdigest()[:24]
        payload = {
            "nonce": nonce,
            "action_type": action_type,
            "actor": actor,
            "issued_at": now,
            "expires_at": now + max(30, int(ttl_seconds)),
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        sig = hmac.new(self._runtime.signing_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
        token = f"{_b64url(payload_bytes)}.{sig}"
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO approvals (nonce, action_type, actor, issued_at, expires_at, used_at)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (nonce, action_type, actor, payload["issued_at"], payload["expires_at"]),
            )
            conn.commit()
        return token

    def validate_approval_token(self, token: str, *, expected_action: str) -> tuple[bool, str]:
        try:
            encoded_payload, sig = token.split(".", 1)
            payload_bytes = _b64url_decode(encoded_payload)
            expected_sig = hmac.new(
                self._runtime.signing_secret.encode("utf-8"), payload_bytes, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                return False, "invalid signature"
            payload = json.loads(payload_bytes.decode("utf-8"))
            nonce = str(payload.get("nonce", ""))
            action_type = str(payload.get("action_type", ""))
            expires_at = int(payload.get("expires_at", 0) or 0)
            now = int(time.time())
            if action_type != expected_action:
                return False, "action mismatch"
            if now > expires_at:
                return False, "token expired"
            with self._db() as conn:
                row = conn.execute(
                    "SELECT used_at, expires_at, action_type FROM approvals WHERE nonce = ?",
                    (nonce,),
                ).fetchone()
                if row is None:
                    return False, "approval not found"
                if int(row["used_at"] or 0) > 0:
                    return False, "token already used"
                if int(row["expires_at"] or 0) < now:
                    return False, "approval expired"
                if str(row["action_type"]) != expected_action:
                    return False, "approval action mismatch"
                conn.execute("UPDATE approvals SET used_at = ? WHERE nonce = ?", (now, nonce))
                conn.commit()
            return True, "ok"
        except Exception as exc:
            return False, f"invalid token ({exc})"

    def enqueue_action(self, action: OutboundAction) -> dict[str, Any]:
        """Queue an outbound Discord action for worker delivery."""
        if not self.is_enabled():
            return {
                "ok": False,
                "reason": "Discord adapter disabled or missing bot token.",
            }
        task_id = hashlib.sha256(f"{action.action}:{time.time_ns()}".encode("utf-8")).hexdigest()[:16]
        self._queue.put(QueuedAction(task_id=task_id, action=action, attempts=0))
        return {
            "ok": True,
            "queued": True,
            "task_id": task_id,
            "action": action.action,
            "payload": action.payload,
        }

    # ------------------------------------------------------------------
    # Direct action helpers used by tool executor
    # ------------------------------------------------------------------

    def send_message(self, channel_id: str, content: str) -> dict[str, Any]:
        return self.enqueue_action(OutboundAction("send_message", {"channel_id": channel_id, "content": content}))

    def reply_message(self, channel_id: str, message_id: str, content: str) -> dict[str, Any]:
        return self.enqueue_action(
            OutboundAction(
                "reply_message",
                {
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "content": content,
                },
            )
        )

    def send_typing(self, channel_id: str) -> dict[str, Any]:
        return self.enqueue_action(OutboundAction("typing", {"channel_id": channel_id}))

    def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> dict[str, Any]:
        return self.enqueue_action(
            OutboundAction(
                "add_reaction",
                {
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "emoji": emoji,
                },
            )
        )

    def fetch_recent_messages(self, channel_id: str, limit: int | None = None) -> dict[str, Any]:
        self._ensure_enabled()
        lim = max(1, min(100, int(limit or self._runtime.recent_messages_limit)))
        qs = urllib.parse.urlencode({"limit": lim})
        path = f"/channels/{channel_id}/messages?{qs}"
        return self._request("GET", path)

    def get_member_profile(self, guild_id: str, user_id: str) -> dict[str, Any]:
        self._ensure_enabled()
        return self._request("GET", f"/guilds/{guild_id}/members/{user_id}")

    def timeout_member(
        self,
        *,
        guild_id: str,
        user_id: str,
        until_iso8601: str,
        reason: str,
        approval_token: str,
    ) -> dict[str, Any]:
        ok, msg = self.validate_approval_token(approval_token, expected_action="timeout_member")
        if not ok:
            return {"ok": False, "reason": f"approval denied: {msg}"}
        payload = {
            "communication_disabled_until": until_iso8601,
            "reason": reason,
        }
        return self._request("PATCH", f"/guilds/{guild_id}/members/{user_id}", payload)

    def delete_message(
        self,
        *,
        channel_id: str,
        message_id: str,
        reason: str,
        approval_token: str,
    ) -> dict[str, Any]:
        ok, msg = self.validate_approval_token(approval_token, expected_action="delete_message")
        if not ok:
            return {"ok": False, "reason": f"approval denied: {msg}"}
        return self._request("DELETE", f"/channels/{channel_id}/messages/{message_id}", {"reason": reason})

    def notify_operator(self, message: str) -> dict[str, Any]:
        self._ensure_enabled()
        if not self._runtime.operator_user_id:
            return {"ok": False, "reason": "DISCORD_OPERATOR_USER_ID not configured."}
        dm = self._request(
            "POST",
            "/users/@me/channels",
            {"recipient_id": self._runtime.operator_user_id},
        )
        dm_channel_id = str(dm.get("id", ""))
        if not dm_channel_id:
            return {"ok": False, "reason": "failed to create operator DM channel"}
        return self._request("POST", f"/channels/{dm_channel_id}/messages", {"content": message})

    def add_member_role(self, guild_id: str, user_id: str, role_id: str) -> dict[str, Any]:
        """Queue a role-add for a guild member (PUT /guilds/{guild_id}/members/{user_id}/roles/{role_id})."""
        return self.enqueue_action(
            OutboundAction("add_member_role", {"guild_id": guild_id, "user_id": user_id, "role_id": role_id})
        )

    def process_member_join(self, payload: dict[str, Any]) -> bool:
        """Handle a GUILD_MEMBER_ADD event: assign newcomer role and post to log channels."""
        if not isinstance(payload, dict):
            return False
        guild_id = str(payload.get("guild_id", "")).strip()
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user_id = str(user.get("id", "")).strip()
        username = _display_name(user)
        if not guild_id or not user_id:
            return False
        if user_id in self._runtime.ignored_user_ids:
            return False

        if self._runtime.newcomer_role_id:
            self.add_member_role(guild_id, user_id, self._runtime.newcomer_role_id)

        msg = f"\U0001f44b **{username}** (`{user_id}`) joined the server."
        if self._runtime.log_channel_joins:
            self.send_message(self._runtime.log_channel_joins, msg)
        if self._runtime.log_channel_general:
            self.send_message(self._runtime.log_channel_general, msg)
        return True

    def process_member_remove(self, payload: dict[str, Any]) -> bool:
        """Handle a GUILD_MEMBER_REMOVE event: post to leave and general log channels."""
        if not isinstance(payload, dict):
            return False
        user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
        user_id = str(user.get("id", "")).strip()
        username = _display_name(user)
        if not user_id:
            return False
        if user_id in self._runtime.ignored_user_ids:
            return False

        msg = f"\U0001f6aa **{username}** (`{user_id}`) left the server."
        if self._runtime.log_channel_leaves:
            self.send_message(self._runtime.log_channel_leaves, msg)
        if self._runtime.log_channel_general:
            self.send_message(self._runtime.log_channel_general, msg)
        return True

    def process_audit_log_entry(self, payload: dict[str, Any]) -> bool:
        """Handle a GUILD_AUDIT_LOG_ENTRY_CREATE event: post to moderation log channel."""
        if not self._runtime.log_channel_moderation:
            return False
        if not isinstance(payload, dict):
            return False
        action_type = int(payload.get("action_type", 0) or 0)
        action_name = _AUDIT_ACTION_NAMES.get(action_type, f"Action #{action_type}")
        moderator_id = _snowflake_or_empty(payload.get("user_id"))
        target_id = _snowflake_or_empty(payload.get("target_id"))
        reason = str(payload.get("reason") or "").strip() or "No reason provided"
        if target_id and target_id in self._runtime.ignored_user_ids:
            return False

        parts = [f"\U0001f528 **{action_name}**"]
        if moderator_id:
            parts.append(f"By: <@{moderator_id}>")
        if target_id and action_type in _AUDIT_USER_TARGET_ACTIONS:
            parts.append(f"Target: <@{target_id}>")
        elif target_id:
            parts.append(f"Target ID: {target_id}")
        parts.append(f"Reason: {reason}")
        self.send_message(self._runtime.log_channel_moderation, " | ".join(parts))
        return True

    # ------------------------------------------------------------------
    # Internal queue + transport
    # ------------------------------------------------------------------

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                queued = self._queue.get(timeout=0.25)
            except Empty:
                continue

            attempts = queued.attempts + 1
            t0 = time.perf_counter()
            ok = False
            failure = ""
            try:
                self._dispatch(queued.action)
                ok = True
            except Exception as exc:
                failure = str(exc)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            self._audit(queued.task_id, queued.action.action, queued.action.payload, ok, failure, attempts, latency_ms)

            if ok:
                continue

            if attempts >= self._runtime.max_send_attempts:
                logger.warning(
                    "Discord action dropped after %d attempts: %s (%s)",
                    attempts,
                    queued.action.action,
                    failure or "unknown error",
                )
                continue

            delay = min(
                self._runtime.retry_base_seconds * (2 ** (attempts - 1)) + random.uniform(0, 0.3),
                self._runtime.retry_max_seconds,
            )
            self._stop_event.wait(timeout=delay)
            self._queue.put(QueuedAction(task_id=queued.task_id, action=queued.action, attempts=attempts))

    def _run_inbound_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_inbound_once()
            except Exception as exc:
                logger.debug("Discord inbound poll failed: %s", exc)
            self._stop_event.wait(timeout=max(0.2, float(self._runtime.inbound_poll_seconds)))

    def _poll_channel(self, channel_id: str) -> int:
        response = self.fetch_recent_messages(channel_id, limit=self._runtime.recent_messages_limit)
        raw_messages = response.get("data", response)
        if not isinstance(raw_messages, list):
            return 0

        events: list[dict[str, Any]] = []
        for message in sorted(raw_messages, key=lambda item: int(str(item.get("id", "0") or "0"))):
            if not isinstance(message, dict):
                continue
            message_id = str(message.get("id", "")).strip()
            if not message_id:
                continue
            if self.process_inbound_message(message, channel_id=channel_id):
                events.append({"message_id": message_id})

        return len(events)

    def process_inbound_message(self, message: dict[str, Any], *, channel_id: str | None = None) -> bool:
        """Normalize and dispatch a raw Discord message_create payload once."""
        if not isinstance(message, dict):
            return False
        message_id = str(message.get("id", "")).strip()
        if not message_id or self._already_seen_message(message_id):
            return False
        derived_channel = channel_id or str(message.get("channel_id", "")).strip()
        if not derived_channel:
            return False
        event = self._normalize_message_event(derived_channel, message)
        if event is None:
            return False
        self._emit_inbound_event(event)
        return True

    def _already_seen_message(self, message_id: str) -> bool:
        with self._lock:
            if message_id in self._seen_message_ids:
                return True
            self._seen_message_ids.add(message_id)
            if len(self._seen_message_ids) > 3000:
                # Keep memory bounded with a soft-reset. IDs are monotonic snowflakes,
                # so occasional reset only risks very rare duplicate processing.
                self._seen_message_ids = {message_id}
            return False

    def _normalize_message_event(self, channel_id: str, message: dict[str, Any]) -> dict[str, Any] | None:
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        author_id = str(author.get("id", "")).strip()
        author_name = _display_name(author)
        is_bot = bool(author.get("bot"))
        if self._runtime.bot_user_id and author_id == self._runtime.bot_user_id:
            is_bot = True
        if is_bot:
            return None

        content = str(message.get("content", ""))
        mentions = message.get("mentions") if isinstance(message.get("mentions"), list) else []
        mention_ids = {
            str(item.get("id", "")).strip()
            for item in mentions
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }
        bot_id = self._runtime.bot_user_id.strip()
        mentions_bot = bool(
            bot_id
            and (
                bot_id in mention_ids
                or f"<@{bot_id}>" in content
                or f"<@!{bot_id}>" in content
            )
        )
        voice_autojoin_requested = bool(
            mentions_bot
            and self._runtime.voice_enabled
            and self._runtime.voice_autojoin_on_mention
            and self._voice_runtime_available
        )
        guild_id = str(message.get("guild_id", "")).strip()
        member = message.get("member") if isinstance(message.get("member"), dict) else {}
        member_roles: list[str] = [str(r) for r in member.get("roles", []) if r]
        return {
            "event_type": "message_create",
            "message_id": str(message.get("id", "")),
            "channel_id": channel_id,
            "guild_id": guild_id,
            "author_id": author_id,
            "author_name": author_name,
            "content": content,
            "mentions_bot": mentions_bot,
            "voice_autojoin_requested": voice_autojoin_requested,
            "is_direct_message": not bool(guild_id),
            "member_roles": member_roles,
            "received_at": int(time.time()),
            "raw": message,
        }

    def _emit_inbound_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            inbound_handlers = list(self._inbound_handlers)
            mention_handlers = list(self._mention_handlers)

        for handler in inbound_handlers:
            with suppress(Exception):
                handler(event)

        if not event.get("mentions_bot"):
            return
        for handler in mention_handlers:
            with suppress(Exception):
                handler(event)

    def _enqueue_reply(self, channel_id: str, message_id: str, content: str) -> None:
        """Queue a reply (or plain message) into the outbound worker."""
        if message_id:
            self.enqueue_action(
                OutboundAction(
                    "reply_message",
                    {"channel_id": channel_id, "message_id": message_id, "content": content},
                )
            )
        elif channel_id:
            self.enqueue_action(OutboundAction("send_message", {"channel_id": channel_id, "content": content}))

    @staticmethod
    def _moderation_help_text() -> str:
        return (
            "🛡️ Moderation commands: `!kick @user [reason]`, `!warn @user [reason]`, `"
            "`!timeout @user <minutes> [reason]`, `!ban @user [reason]` (admin only)."
        )

    def _handle_mod_command(self, event: dict[str, Any]) -> None:
        """Dispatch !ban / !kick / !timeout / !warn commands for authorised members."""
        if str(event.get("event_type", "")) != "message_create":
            return
        content = str(event.get("content", "")).strip()
        if not content.startswith("!"):
            return

        member_roles: list[str] = event.get("member_roles", [])
        is_mod = (
            config.DISCORD_MOD_ROLE_ID in member_roles
            or config.DISCORD_ADMIN_ROLE_ID in member_roles
        )
        is_admin = config.DISCORD_ADMIN_ROLE_ID in member_roles

        if not is_mod:
            return

        channel_id = str(event.get("channel_id", "")).strip()
        guild_id = str(event.get("guild_id", "")).strip()
        message_id = str(event.get("message_id", "")).strip()

        if content.lower().startswith("!help"):
            self._enqueue_reply(channel_id, message_id, self._moderation_help_text())
            return

        # ---- !ban -------------------------------------------------------
        if content.lower().startswith("!ban"):
            if not is_admin:
                self._enqueue_reply(channel_id, message_id, "❌ You need the admin role to use `!ban`.")
                return
            parts = content.split(None, 2)
            if len(parts) < 2:
                self._enqueue_reply(channel_id, message_id, "Usage: `!ban @user [reason]`")
                return
            target_id = self._extract_mention(parts[1])
            if not target_id:
                self._enqueue_reply(channel_id, message_id, "❌ Please mention a valid user.")
                return
            reason = parts[2].strip() if len(parts) > 2 else "No reason provided"
            self.enqueue_action(
                OutboundAction("ban_member", {"guild_id": guild_id, "user_id": target_id, "reason": reason})
            )
            self._enqueue_reply(channel_id, message_id, f"✅ Banned <@{target_id}>. Reason: {reason}")
            return

        # ---- !kick ------------------------------------------------------
        if content.lower().startswith("!kick"):
            parts = content.split(None, 2)
            if len(parts) < 2:
                self._enqueue_reply(channel_id, message_id, "Usage: `!kick @user [reason]`")
                return
            target_id = self._extract_mention(parts[1])
            if not target_id:
                self._enqueue_reply(channel_id, message_id, "❌ Please mention a valid user.")
                return
            reason = parts[2].strip() if len(parts) > 2 else "No reason provided"
            self.enqueue_action(
                OutboundAction("kick_member", {"guild_id": guild_id, "user_id": target_id, "reason": reason})
            )
            self._enqueue_reply(channel_id, message_id, f"✅ Kicked <@{target_id}>. Reason: {reason}")
            return

        # ---- !timeout ---------------------------------------------------
        if content.lower().startswith("!timeout"):
            parts = content.split(None, 3)
            if len(parts) < 3:
                self._enqueue_reply(channel_id, message_id, "Usage: `!timeout @user <minutes> [reason]`")
                return
            target_id = self._extract_mention(parts[1])
            if not target_id:
                self._enqueue_reply(channel_id, message_id, "❌ Please mention a valid user.")
                return
            try:
                minutes = int(parts[2])
            except ValueError:
                self._enqueue_reply(channel_id, message_id, "❌ Duration must be a number of minutes.")
                return
            reason = parts[3].strip() if len(parts) > 3 else "No reason provided"
            until = (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            ).isoformat()
            self.enqueue_action(
                OutboundAction(
                    "timeout_member_direct",
                    {"guild_id": guild_id, "user_id": target_id, "until_iso8601": until, "reason": reason},
                )
            )
            self._enqueue_reply(
                channel_id, message_id,
                f"✅ Timed out <@{target_id}> for {minutes} minute(s). Reason: {reason}",
            )
            return

        # ---- !warn ------------------------------------------------------
        if content.lower().startswith("!warn"):
            parts = content.split(None, 2)
            if len(parts) < 2:
                self._enqueue_reply(channel_id, message_id, "Usage: `!warn @user [reason]`")
                return
            target_id = self._extract_mention(parts[1])
            if not target_id:
                self._enqueue_reply(channel_id, message_id, "❌ Please mention a valid user.")
                return
            reason = parts[2].strip() if len(parts) > 2 else "You have been warned."
            self._enqueue_reply(
                channel_id, message_id,
                f"⚠️ <@{target_id}> has been warned. Reason: {reason}",
            )
            return

    @staticmethod
    def _extract_mention(text: str) -> str | None:
        """Return the user ID from a Discord mention string, or None if not a mention."""
        m = _MENTION_RE.search(text)
        return m.group(1) if m else None

    def _dispatch(self, action: OutboundAction) -> dict[str, Any]:
        name = action.action
        payload = action.payload
        if name == "send_message":
            channel_id = str(payload["channel_id"])
            chunks = self._message_chunks(payload.get("content", ""))
            result: dict[str, Any] = {"ok": True}
            for chunk in chunks:
                result = self._request("POST", f"/channels/{channel_id}/messages", {"content": chunk})
            return result
        if name == "reply_message":
            channel_id = str(payload["channel_id"])
            message_id = str(payload["message_id"])
            chunks = self._message_chunks(payload.get("content", ""))
            first_chunk = chunks[0]
            remaining_chunks = chunks[1:]
            try:
                result = self._request(
                    "POST",
                    f"/channels/{channel_id}/messages",
                    {
                        "content": first_chunk,
                        "message_reference": {"message_id": message_id},
                    },
                )
            except RuntimeError as exc:
                # Some contexts reject message_reference (permissions/unknown message);
                # fall back to a plain send so the user still gets a response.
                logger.warning("Discord reply_message failed; falling back to send_message: %s", exc)
                result = self._request(
                    "POST",
                    f"/channels/{channel_id}/messages",
                    {"content": first_chunk},
                )
            for chunk in remaining_chunks:
                result = self._request("POST", f"/channels/{channel_id}/messages", {"content": chunk})
            return result
        if name == "typing":
            return self._request("POST", f"/channels/{payload['channel_id']}/typing")
        if name == "add_reaction":
            emoji = urllib.parse.quote(str(payload["emoji"]), safe="")
            return self._request(
                "PUT",
                f"/channels/{payload['channel_id']}/messages/{payload['message_id']}/reactions/{emoji}/@me",
            )
        if name == "add_member_role":
            return self._request(
                "PUT",
                f"/guilds/{payload['guild_id']}/members/{payload['user_id']}/roles/{payload['role_id']}",
            )
        if name == "ban_member":
            return self._request(
                "PUT",
                f"/guilds/{payload['guild_id']}/bans/{payload['user_id']}",
                {"delete_message_seconds": 0},
                audit_reason=payload.get("reason", ""),
            )
        if name == "kick_member":
            return self._request(
                "DELETE",
                f"/guilds/{payload['guild_id']}/members/{payload['user_id']}",
                audit_reason=payload.get("reason", ""),
            )
        if name == "timeout_member_direct":
            return self._request(
                "PATCH",
                f"/guilds/{payload['guild_id']}/members/{payload['user_id']}",
                {"communication_disabled_until": payload["until_iso8601"]},
                audit_reason=payload.get("reason", ""),
            )
        raise RuntimeError(f"Unsupported outbound action '{name}'.")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, *, audit_reason: str | None = None) -> dict[str, Any]:
        self._ensure_enabled()
        url = f"{_REST_BASE}{path}"
        headers = {
            "Authorization": f"Bot {self._runtime.bot_token}",
            "Content-Type": "application/json",
            "User-Agent": _BOT_USER_AGENT,
        }
        if audit_reason:
            headers["X-Audit-Log-Reason"] = urllib.parse.quote(audit_reason, safe="")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8") if resp.length != 0 else "{}"
                if not raw:
                    return {"ok": True}
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {"ok": True, "data": parsed}
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            raise RuntimeError(f"Discord HTTP {exc.code}: {msg}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Discord request failed: {exc}") from exc

    @staticmethod
    def _message_chunks(content: Any) -> list[str]:
        """Split outbound content into Discord-safe message chunks (<=2000 chars)."""
        text = str(content or "").strip()
        if not text:
            return ["(empty response)"]
        if len(text) <= _MAX_DISCORD_MESSAGE_CHARS:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= _MAX_DISCORD_MESSAGE_CHARS:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n", 0, _MAX_DISCORD_MESSAGE_CHARS + 1)
            if cut <= 0:
                cut = remaining.rfind(" ", 0, _MAX_DISCORD_MESSAGE_CHARS + 1)
            if cut <= 0:
                cut = _MAX_DISCORD_MESSAGE_CHARS
            piece = remaining[:cut].strip()
            if piece:
                chunks.append(piece)
            remaining = remaining[cut:].lstrip()
        return chunks or [text[:_MAX_DISCORD_MESSAGE_CHARS]]

    def _ensure_enabled(self) -> None:
        if not self.is_enabled():
            raise RuntimeError("Discord adapter disabled or missing bot token.")

    # ------------------------------------------------------------------
    # Audit + approvals storage
    # ------------------------------------------------------------------

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    action_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    error_text TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL,
                    latency_ms REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    nonce TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.commit()

    def _audit(
        self,
        task_id: str,
        action_name: str,
        payload: dict[str, Any],
        ok: bool,
        error_text: str,
        attempts: int,
        latency_ms: float,
    ) -> None:
        with suppress(Exception):
            with self._db() as conn:
                conn.execute(
                    """
                    INSERT INTO outbound_audit (
                        ts, task_id, action_name, payload_json, ok, error_text, attempts, latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(time.time()),
                        task_id,
                        action_name,
                        json.dumps(payload, ensure_ascii=False),
                        1 if ok else 0,
                        error_text,
                        attempts,
                        float(latency_ms),
                    ),
                )
                conn.commit()

    def _cleanup_old_audit_rows(self) -> None:
        cutoff = int(time.time()) - (max(1, self._runtime.audit_retention_days) * 86400)
        with suppress(Exception):
            with self._db() as conn:
                conn.execute("DELETE FROM outbound_audit WHERE ts < ?", (cutoff,))
                conn.commit()

    def _cleanup_expired_approvals(self) -> None:
        now = int(time.time())
        with suppress(Exception):
            with self._db() as conn:
                conn.execute("DELETE FROM approvals WHERE expires_at < ?", (now,))
                conn.commit()
