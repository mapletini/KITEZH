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
        }

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

                raw = ws.recv(timeout=1)
                if raw is None:
                    continue
                payload = self._parse_payload(raw)
                self._handle_gateway_payload(payload, ws=ws)

    def _fetch_gateway_url(self) -> str:
        req = urllib.request.Request(
            "https://discord.com/api/v10/gateway/bot",
            headers={"Authorization": f"Bot {self._runtime.bot_token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
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
