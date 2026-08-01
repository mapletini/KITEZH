"""skills/discord_gateway.py - Optional Discord Gateway ingestion runtime.

This runtime handles inbound Discord events over the Gateway websocket and
forwards message_create payloads to ``DiscordAdapter.process_inbound_message``.
It is optional and degrades gracefully when websocket dependencies are missing.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

import config
from skills.discord_adapter import DiscordAdapter

logger = logging.getLogger(__name__)

_FALLBACK_GATEWAY_URL = "wss://gateway.discord.gg"
_BOT_USER_AGENT = "DiscordBot (https://github.com/mapletini/KITEZH, 1.0)"

try:
    from websockets.sync.client import connect as _ws_connect
except Exception:  # pragma: no cover - optional dependency path
    _ws_connect = None


@dataclass(frozen=True)
class DiscordGatewayConfig:
    enabled: bool
    bot_token: str
    intents: int
    reconnect_base_seconds: float
    reconnect_max_seconds: float


class DiscordGatewayRuntime:
    """Gateway event ingester with reconnect and heartbeat handling."""

    def __init__(self, runtime: DiscordGatewayConfig, adapter: DiscordAdapter) -> None:
        self._runtime = runtime
        self._adapter = adapter
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._last_sequence: int | None = None
        self._ready = False
        self._availability_reason = ""
        self._outbound_lock = threading.Lock()
        self._outbound_queue: list[dict[str, Any]] = []

    @classmethod
    def from_config(cls, adapter: DiscordAdapter) -> "DiscordGatewayRuntime":
        return cls(
            DiscordGatewayConfig(
                enabled=config.DISCORD_GATEWAY_ENABLED,
                bot_token=config.DISCORD_BOT_TOKEN,
                intents=config.DISCORD_GATEWAY_INTENTS,
                reconnect_base_seconds=config.DISCORD_GATEWAY_RECONNECT_BASE_SECONDS,
                reconnect_max_seconds=config.DISCORD_GATEWAY_RECONNECT_MAX_SECONDS,
            ),
            adapter,
        )

    def is_enabled(self) -> bool:
        return self._runtime.enabled and bool(self._runtime.bot_token)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._runtime.enabled,
            "configured": bool(self._runtime.bot_token),
            "active": bool(self._worker and self._worker.is_alive()),
            "ready": self._ready,
            "last_sequence": self._last_sequence,
            "availability_reason": self._availability_reason,
            "intents": self._runtime.intents,
            "outbound_queue_depth": len(self._outbound_queue),
        }

    def request_voice_state_update(
        self,
        *,
        guild_id: str,
        channel_id: str | None,
        self_mute: bool = False,
        self_deaf: bool = False,
    ) -> dict[str, Any]:
        if not self.is_enabled():
            return {"ok": False, "reason": "discord gateway disabled or missing token"}
        payload = self._build_voice_state_update_payload(
            guild_id=guild_id,
            channel_id=channel_id,
            self_mute=self_mute,
            self_deaf=self_deaf,
        )
        with self._outbound_lock:
            self._outbound_queue.append(payload)
        return {"ok": True, "queued": True, "op": payload.get("op"), "reason": "voice state update queued"}

    def sync_transport_voice_updates(self) -> int:
        """Move pending transport-originated OP4 updates into the gateway outbound queue."""
        if not hasattr(self._adapter, "pop_pending_voice_state_update"):
            return 0
        moved = 0
        while True:
            pending = self._adapter.pop_pending_voice_state_update()
            if not isinstance(pending, dict):
                break
            payload = self._build_voice_state_update_payload(
                guild_id=str(pending.get("guild_id", "")),
                channel_id=str(pending.get("channel_id", "")) or None,
                self_mute=bool(pending.get("self_mute", False)),
                self_deaf=bool(pending.get("self_deaf", False)),
            )
            with self._outbound_lock:
                self._outbound_queue.append(payload)
            moved += 1
        return moved

    def process_voice_gateway_payload(self, payload: dict[str, Any]) -> bool:
        """Forward voice websocket session-description payloads to the adapter.

        Discord voice websocket emits SESSION_DESCRIPTION on opcode 4 with
        payload shape: {"op": 4, "d": {"mode": "...", "secret_key": [...]}}.
        This helper accepts that frame directly and also supports an event-like
        envelope using t=VOICE_SESSION_DESCRIPTION for testability.
        """
        if not isinstance(payload, dict):
            return False

        description: dict[str, Any] | None = None

        op_value = payload.get("op")
        try:
            op = int(op_value) if op_value is not None else -1
        except Exception:
            op = -1
        data = payload.get("d")
        if op == 4 and isinstance(data, dict):
            description = data
        else:
            event_type = str(payload.get("t", "")).strip().upper()
            if event_type == "VOICE_SESSION_DESCRIPTION" and isinstance(data, dict):
                description = data
            elif "mode" in payload:
                description = payload

        if not isinstance(description, dict):
            return False
        if "mode" not in description:
            return False
        if not hasattr(self._adapter, "process_voice_session_description"):
            return False
        return bool(self._adapter.process_voice_session_description(description))

    def start(self) -> None:
        if not self.is_enabled():
            self._availability_reason = "gateway disabled or token not configured"
            return
        if _ws_connect is None:
            self._availability_reason = "websockets package unavailable"
            logger.warning("Discord gateway disabled: %s", self._availability_reason)
            return
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run, name="discord-gateway", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)

    def _run(self) -> None:
        attempts = 0
        while not self._stop_event.is_set():
            try:
                self._connect_and_consume()
                attempts = 0
            except Exception as exc:
                self._ready = False
                attempts += 1
                self._availability_reason = str(exc)
                delay = min(
                    self._runtime.reconnect_base_seconds * (2 ** (attempts - 1)) + random.uniform(0, 0.4),
                    self._runtime.reconnect_max_seconds,
                )
                logger.warning("Discord gateway disconnected; reconnecting in %.2fs (%s)", delay, exc)
                self._stop_event.wait(timeout=max(0.2, delay))

    def _connect_and_consume(self) -> None:
        gateway = self._fetch_gateway_url()
        ws_url = f"{gateway}?v=10&encoding=json"
        self._availability_reason = ""
        self._ready = False
        logger.info("Connecting Discord gateway...")
        with _ws_connect(ws_url, open_timeout=10, close_timeout=2) as ws:
            hello_raw = ws.recv(timeout=15)
            hello = self._parse_payload(hello_raw)
            if int(hello.get("op", -1)) != 10:
                raise RuntimeError("Discord gateway hello was not received")
            heartbeat_ms = int((hello.get("d") or {}).get("heartbeat_interval", 45000))
            self._send_identify(ws)
            self._ready = True

            next_heartbeat = time.monotonic() + (heartbeat_ms / 1000.0)
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_heartbeat:
                    self._send_heartbeat(ws)
                    next_heartbeat = now + (heartbeat_ms / 1000.0)

                self.sync_transport_voice_updates()
                self._drain_outbound(ws)

                raw = ws.recv(timeout=1)
                if raw is None:
                    continue
                payload = self._parse_payload(raw)
                self._handle_gateway_payload(payload, ws=ws)

    @staticmethod
    def _build_voice_state_update_payload(
        *,
        guild_id: str,
        channel_id: str | None,
        self_mute: bool,
        self_deaf: bool,
    ) -> dict[str, Any]:
        return {
            "op": 4,
            "d": {
                "guild_id": str(guild_id).strip(),
                "channel_id": str(channel_id).strip() if channel_id else None,
                "self_mute": bool(self_mute),
                "self_deaf": bool(self_deaf),
            },
        }

    def _drain_outbound(self, ws: Any) -> None:
        batch: list[dict[str, Any]] = []
        with self._outbound_lock:
            if self._outbound_queue:
                batch = list(self._outbound_queue)
                self._outbound_queue.clear()
        for payload in batch:
            ws.send(json.dumps(payload, separators=(",", ":")))

    def _fetch_gateway_url(self) -> str:
        req = urllib.request.Request(
            "https://discord.com/api/v10/gateway/bot",
            headers={
                "Authorization": f"Bot {self._runtime.bot_token}",
                "User-Agent": _BOT_USER_AGENT,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            if exc.code == 403 and "1010" in body:
                logger.warning(
                    "Discord /gateway/bot blocked with HTTP 403 code 1010; "
                    "falling back to %s",
                    _FALLBACK_GATEWAY_URL,
                )
                return _FALLBACK_GATEWAY_URL
            raise RuntimeError(f"Discord gateway URL fetch failed ({exc.code}): {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Discord gateway URL fetch failed: {exc}") from exc

        url = str(data.get("url", "")).strip()
        if not url:
            raise RuntimeError("Discord gateway URL missing from /gateway/bot response")
        return url

    def _send_identify(self, ws: Any) -> None:
        payload = {
            "op": 2,
            "d": {
                "token": self._runtime.bot_token,
                "intents": self._runtime.intents,
                "properties": {
                    "os": "windows",
                    "browser": "kitezh",
                    "device": "kitezh",
                },
            },
        }
        ws.send(json.dumps(payload, separators=(",", ":")))

    def _send_heartbeat(self, ws: Any) -> None:
        payload = {"op": 1, "d": self._last_sequence}
        ws.send(json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def _parse_payload(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            raise RuntimeError("Unexpected gateway frame type")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected gateway payload shape")
        return parsed

    def _handle_gateway_payload(self, payload: dict[str, Any], ws: Any | None = None) -> None:
        seq = payload.get("s")
        if isinstance(seq, int):
            self._last_sequence = seq

        op = int(payload.get("op", -1))
        if op == 11:  # heartbeat ack
            return
        if op == 7:  # reconnect
            raise RuntimeError("Discord gateway requested reconnect")
        if op == 9:  # invalid session
            raise RuntimeError("Discord gateway invalid session")
        if op == 1:  # server heartbeat request
            if ws is not None:
                self._send_heartbeat(ws)
            return
        if op != 0:
            return

        event_type = str(payload.get("t", ""))
        data = payload.get("d")
        if not isinstance(data, dict):
            return
        if event_type == "MESSAGE_CREATE":
            self._adapter.process_inbound_message(data)
            return
        if event_type == "VOICE_STATE_UPDATE":
            self._adapter.process_voice_state_update(data)
            return
        if event_type == "VOICE_SERVER_UPDATE":
            self._adapter.process_voice_server_update(data)
            return
        if event_type == "VOICE_SESSION_DESCRIPTION":
            if hasattr(self._adapter, "process_voice_session_description"):
                self._adapter.process_voice_session_description(data)
            return
        if event_type == "GUILD_MEMBER_ADD":
            if hasattr(self._adapter, "process_member_join"):
                self._adapter.process_member_join(data)
            return
        if event_type == "GUILD_MEMBER_REMOVE":
            if hasattr(self._adapter, "process_member_remove"):
                self._adapter.process_member_remove(data)
            return
        if event_type == "GUILD_AUDIT_LOG_ENTRY_CREATE":
            if hasattr(self._adapter, "process_audit_log_entry"):
                self._adapter.process_audit_log_entry(data)
