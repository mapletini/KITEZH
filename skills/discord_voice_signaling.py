"""skills/discord_voice_signaling.py - Discord voice websocket signaling runtime.

This runtime is intentionally minimal and focuses on forwarding inbound voice
gateway payloads (especially SESSION_DESCRIPTION / op=4) into the existing
gateway->adapter->transport chain.
"""

from __future__ import annotations

import json
import logging
import random
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import config

logger = logging.getLogger(__name__)

try:
    from websockets.sync.client import connect as _ws_connect
except Exception:  # pragma: no cover - optional dependency path
    _ws_connect = None


@dataclass(frozen=True)
class DiscordVoiceSignalingConfig:
    enabled: bool
    bot_user_id: str
    reconnect_base_seconds: float
    reconnect_max_seconds: float
    idle_poll_seconds: float


class DiscordVoiceSignalingRuntime:
    """Consume voice websocket frames and forward session descriptions."""

    def __init__(self, runtime: DiscordVoiceSignalingConfig, gateway: Any, voice_transport: Any) -> None:
        self._runtime = runtime
        self._gateway = gateway
        self._voice_transport = voice_transport
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._ready = False
        self._availability_reason = ""
        self._last_endpoint = ""
        self._last_mode = ""
        self._last_ssrc = 0
        self._metrics: dict[str, int] = {
            "connect_attempts": 0,
            "connect_successes": 0,
            "reconnects": 0,
            "payloads_seen": 0,
            "session_descriptions_forwarded": 0,
            "select_protocol_sent": 0,
            "speaking_sent": 0,
            "heartbeats_sent": 0,
            "heartbeat_acks": 0,
        }

    @classmethod
    def from_config(cls, gateway: Any, voice_transport: Any) -> "DiscordVoiceSignalingRuntime":
        enabled = bool(
            config.DISCORD_ENABLED
            and config.DISCORD_VOICE_ENABLED
            and config.DISCORD_VOICE_SIGNALING_ENABLED
            and config.DISCORD_BOT_TOKEN
            and config.DISCORD_BOT_USER_ID
        )
        return cls(
            DiscordVoiceSignalingConfig(
                enabled=enabled,
                bot_user_id=config.DISCORD_BOT_USER_ID,
                reconnect_base_seconds=config.DISCORD_VOICE_SIGNALING_RECONNECT_BASE_SECONDS,
                reconnect_max_seconds=config.DISCORD_VOICE_SIGNALING_RECONNECT_MAX_SECONDS,
                idle_poll_seconds=config.DISCORD_VOICE_SIGNALING_IDLE_POLL_SECONDS,
            ),
            gateway,
            voice_transport,
        )

    def is_enabled(self) -> bool:
        return self._runtime.enabled

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._runtime.enabled,
            "active": bool(self._worker and self._worker.is_alive()),
            "ready": self._ready,
            "availability_reason": self._availability_reason,
            "last_endpoint": self._last_endpoint,
            "last_mode": self._last_mode,
            "last_ssrc": self._last_ssrc,
            "metrics": dict(self._metrics),
        }

    def start(self) -> None:
        if not self.is_enabled():
            self._availability_reason = "voice signaling disabled or missing config"
            return
        if _ws_connect is None:
            self._availability_reason = "websockets package unavailable"
            logger.warning("Discord voice signaling disabled: %s", self._availability_reason)
            return
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run, name="discord-voice-signaling", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
        self._ready = False

    def ingest_payload(self, payload: dict[str, Any]) -> bool:
        """Forward a single voice-gateway payload through the existing gateway path."""
        if not hasattr(self._gateway, "process_voice_gateway_payload"):
            return False
        try:
            return bool(self._gateway.process_voice_gateway_payload(payload))
        except Exception:
            return False

    def _run(self) -> None:
        attempts = 0
        while not self._stop_event.is_set():
            active = self._get_active_voice_session()
            if active is None:
                self._ready = False
                self._stop_event.wait(timeout=max(0.2, self._runtime.idle_poll_seconds))
                continue
            try:
                self._metrics["connect_attempts"] += 1
                self._connect_and_consume(active)
                attempts = 0
            except Exception as exc:
                self._ready = False
                attempts += 1
                self._metrics["reconnects"] += 1
                self._availability_reason = str(exc)
                delay = min(
                    self._runtime.reconnect_base_seconds * (2 ** (attempts - 1)) + random.uniform(0, 0.4),
                    self._runtime.reconnect_max_seconds,
                )
                logger.warning("Discord voice signaling disconnected; reconnecting in %.2fs (%s)", delay, exc)
                self._stop_event.wait(timeout=max(0.2, delay))

    def _connect_and_consume(self, active: dict[str, Any]) -> None:
        endpoint = str(active.get("voice_endpoint", "")).strip()
        if not endpoint:
            raise RuntimeError("voice endpoint is not available")
        ws_url = self._build_voice_ws_url(endpoint)
        self._last_endpoint = endpoint
        self._availability_reason = ""
        self._ready = False

        with _ws_connect(ws_url, open_timeout=10, close_timeout=2) as ws:
            hello_raw = ws.recv(timeout=15)
            hello = self._parse_payload(hello_raw)
            if int(hello.get("op", -1)) != 8:
                raise RuntimeError("Discord voice hello was not received")
            heartbeat_ms = int((hello.get("d") or {}).get("heartbeat_interval", 45000))
            ws.send(json.dumps(self._build_identify_payload(active), separators=(",", ":")))
            self._ready = True
            self._metrics["connect_successes"] += 1

            next_heartbeat = time.monotonic() + (heartbeat_ms / 1000.0)
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_heartbeat:
                    self._send_heartbeat(ws)
                    next_heartbeat = now + (heartbeat_ms / 1000.0)

                raw = ws.recv(timeout=1)
                if raw is None:
                    continue
                payload = self._parse_payload(raw)
                self._handle_inbound_payload(ws=ws, payload=payload, active=active)

    def _handle_inbound_payload(self, *, ws: Any, payload: dict[str, Any], active: dict[str, Any]) -> None:
        self._metrics["payloads_seen"] += 1
        op = int(payload.get("op", -1))
        data = payload.get("d")

        if op == 6:
            self._metrics["heartbeat_acks"] += 1
            return

        if op == 2 and isinstance(data, dict):
            self._on_voice_ready(ws=ws, active=active, ready=data)
            return

        if op == 4 and isinstance(data, dict):
            if self.ingest_payload(payload):
                self._metrics["session_descriptions_forwarded"] += 1
                self._send_speaking(ws=ws, speaking=1, delay=0, ssrc=self._last_ssrc)
            return

        # Pass through unknown payloads so future events can still be consumed upstream.
        self.ingest_payload(payload)

    def _on_voice_ready(self, *, ws: Any, active: dict[str, Any], ready: dict[str, Any]) -> None:
        endpoint = str(active.get("voice_endpoint", "")).strip()
        mode = self._choose_encryption_mode(ready, active)
        ssrc = int(ready.get("ssrc") or 0)
        discovered_ip, discovered_port = self._perform_udp_ip_discovery(
            endpoint=endpoint,
            port=int(ready.get("port") or config.DISCORD_VOICE_UDP_DEFAULT_PORT),
            ssrc=ssrc,
        )
        payload = self._build_select_protocol_payload(
            address=discovered_ip,
            port=discovered_port,
            mode=mode,
        )
        ws.send(json.dumps(payload, separators=(",", ":")))
        self._metrics["select_protocol_sent"] += 1
        self._last_mode = mode
        self._last_ssrc = ssrc

    def _send_heartbeat(self, ws: Any) -> None:
        heartbeat = {"op": 3, "d": int(time.time() * 1000)}
        ws.send(json.dumps(heartbeat, separators=(",", ":")))
        self._metrics["heartbeats_sent"] += 1

    def _send_speaking(self, *, ws: Any, speaking: int, delay: int, ssrc: int) -> None:
        if int(ssrc) <= 0:
            return
        payload = {
            "op": 5,
            "d": {
                "speaking": int(speaking),
                "delay": int(delay),
                "ssrc": int(ssrc),
            },
        }
        ws.send(json.dumps(payload, separators=(",", ":")))
        self._metrics["speaking_sent"] += 1

    def _get_active_voice_session(self) -> dict[str, Any] | None:
        if self._voice_transport is None or not hasattr(self._voice_transport, "status"):
            return None
        try:
            status = self._voice_transport.status()
        except Exception:
            return None
        if not isinstance(status, dict):
            return None
        active = status.get("active")
        if not isinstance(active, dict):
            return None

        guild_id = str(active.get("guild_id", "")).strip()
        session_id = str(active.get("gateway_session_id", "")).strip()
        token = str(active.get("voice_token", "")).strip()
        endpoint = str(active.get("voice_endpoint", "")).strip()
        if not guild_id or not session_id or not token or not endpoint:
            return None
        return active

    def _build_identify_payload(self, active: dict[str, Any]) -> dict[str, Any]:
        return {
            "op": 0,
            "d": {
                "server_id": str(active.get("guild_id", "")).strip(),
                "user_id": self._runtime.bot_user_id,
                "session_id": str(active.get("gateway_session_id", "")).strip(),
                "token": str(active.get("voice_token", "")).strip(),
            },
        }

    @staticmethod
    def _build_select_protocol_payload(*, address: str, port: int, mode: str) -> dict[str, Any]:
        return {
            "op": 1,
            "d": {
                "protocol": "udp",
                "data": {
                    "address": str(address).strip(),
                    "port": int(port),
                    "mode": str(mode).strip() or "xsalsa20_poly1305",
                },
            },
        }

    @staticmethod
    def _choose_encryption_mode(ready: dict[str, Any], active: dict[str, Any]) -> str:
        requested = str(active.get("encryption_mode", "")).strip() or "xsalsa20_poly1305"
        advertised = ready.get("modes")
        if not isinstance(advertised, list) or not advertised:
            return requested
        if requested in advertised:
            return requested
        return str(advertised[0]).strip() or requested

    @staticmethod
    def _perform_udp_ip_discovery(*, endpoint: str, port: int, ssrc: int) -> tuple[str, int]:
        host = (endpoint or "").strip()
        if ":" in host:
            host = host.split(":", 1)[0]
        if not host:
            return "127.0.0.1", int(port)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(float(config.DISCORD_VOICE_UDP_CONNECT_TIMEOUT_SECONDS))
            sock.connect((host, int(port)))
            local_ip, local_port = sock.getsockname()[:2]
            if not local_ip:
                local_ip = "127.0.0.1"
            if int(local_port) <= 0:
                local_port = int(port)
            return str(local_ip), int(local_port)
        except Exception:
            return "127.0.0.1", int(port)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    @staticmethod
    def _build_voice_ws_url(endpoint: str) -> str:
        raw = endpoint.strip()
        if not raw:
            raise ValueError("voice endpoint is empty")
        if "://" not in raw:
            raw = f"wss://{raw}"
        parsed = urlsplit(raw)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("v", "4")
        rebuilt = parsed._replace(query=urlencode(query))
        return urlunsplit(rebuilt)

    @staticmethod
    def _parse_payload(raw: Any) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if not isinstance(raw, str):
            raise RuntimeError("Unexpected voice gateway frame type")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("Unexpected voice gateway payload shape")
        return parsed