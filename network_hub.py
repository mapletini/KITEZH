"""
network_hub.py — Remote bridge client and message namespace router for K.A.I.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import requests
from requests.exceptions import ConnectionError, ReadTimeout, RequestException

import config

logger = logging.getLogger(__name__)

Clearance = Literal["admin", "guest"]

@dataclass
class UserPayload:
    """Structured representation of an incoming user message."""

    platform: str
    user_id: str
    display_name: str
    content: str
    clearance: Clearance = "guest"
    is_puppy: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextResponse:
    """Structured response returned by the remote /api/ai/context endpoint."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class RemoteMochiiBridge:
    """
    HTTP client for the remote backend.

    Requests are authenticated with the x-ai-key header.
    """

    def __init__(
        self,
        base_url: str = config.REMOTE_BASE_URL,
        ai_key: str = config.AI_KEY,
        timeout: float = config.REQUEST_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._signing_secret = config.COMMAND_SIGNING_SECRET
        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-ai-key": ai_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        if ai_key in config.INSECURE_AI_KEYS:
            if not _is_local_base_url(self._base_url):
                raise ValueError("Refusing insecure default AI key for non-local remote backend.")
            logger.warning("Bridge running with an insecure default AI key; set KITEZH_AI_KEY.")

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **kwargs: Any) -> requests.Response:
        return self._session.get(f"{self._base_url}{path}", timeout=self._timeout, **kwargs)

    def _post(self, path: str, **kwargs: Any) -> requests.Response:
        return self._session.post(f"{self._base_url}{path}", timeout=self._timeout, **kwargs)

    # ------------------------------------------------------------------
    # Signed command envelope
    # ------------------------------------------------------------------

    def sign_and_prepare_envelope(
        self,
        action_type: str,
        rules_version: str,
        action_params: dict[str, Any],
    ) -> dict[str, Any]:
        now = int(time.time())
        payload = {
            "action_type": action_type,
            "rules_version_used": rules_version,
            "command_nonce": str(uuid.uuid4()),
            "command_issued_at": now,
            "command_expires_at": now + 300,
            "actor": "K.A.I. Core System",
            "rationale_summary": "Autonomous script evaluation pass.",
            **action_params,
        }

        canonical_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = hmac.new(
            self._signing_secret.encode("utf-8"),
            canonical_json.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["command_signature"] = signature
        return payload

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def query_context(self, payload: UserPayload) -> ContextResponse:
        body: dict[str, Any] = {
            "platform": payload.platform,
            "user_id": payload.user_id,
            "display_name": payload.display_name,
            "content": payload.content,
            "clearance": payload.clearance,
            "is_puppy": payload.is_puppy,
            "metadata": payload.metadata,
        }
        try:
            response = self._post("/api/ai/context", json=body)
            response.raise_for_status()
            return ContextResponse(success=True, data=response.json())
        except ReadTimeout:
            msg = f"Request to {self._base_url}/api/ai/context timed out after {self._timeout}s"
            logger.warning(msg)
            return ContextResponse(success=False, error=msg)
        except ConnectionError as exc:
            msg = f"Could not connect to remote backend at {self._base_url}: {exc}"
            logger.error(msg)
            return ContextResponse(success=False, error=msg)
        except RequestException as exc:
            msg = f"Unexpected HTTP error: {exc}"
            logger.error(msg)
            return ContextResponse(success=False, error=msg)
        except ValueError as exc:
            msg = (
                f"Invalid JSON response from remote backend: {exc}. "
                "Expected valid JSON; check backend response format/logs."
            )
            logger.error(msg)
            return ContextResponse(success=False, error=msg)

    def health_check(self) -> bool:
        """Return True when remote /health responds with a non-error status."""
        try:
            resp = self._get("/health")
            return resp.status_code < 400
        except RequestException:
            return False

    # ------------------------------------------------------------------
    # Legacy helper endpoints
    # ------------------------------------------------------------------

    def fetch_unified_context(self) -> dict[str, Any] | None:
        try:
            response = self._get("/api/ai/context")
            return response.json() if response.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: Context call failed: %s", exc)
            return None

    def ingest_snapshot(
        self,
        device_id: str,
        snapshot: dict[str, Any],
        usage_stats: dict[str, Any],
        app_stats: dict[str, Any],
    ) -> bool:
        payload = {
            "device_id": device_id,
            "captured_at": int(time.time()),
            "snapshot": snapshot,
            "usage_stats": usage_stats,
            "app_stats": app_stats,
        }
        try:
            response = self._post("/api/ai/ingest", json=payload)
            return response.status_code == 200
        except Exception as exc:
            logger.error("K.A.I. Bridge: Ingest operation failed: %s", exc)
            return False

    def fetch_telemetry_summary(self, hours: int = 24) -> dict[str, Any] | None:
        try:
            res = self._get("/api/ai/telemetry/summary", params={"hours": hours})
            return res.json() if res.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: Telemetry fetch failed: %s", exc)
            return None

    def fetch_raw_telemetry(
        self,
        hours: int = 24,
        limit: int = 200,
        offset: int = 0,
        event_type: str | None = None,
    ) -> dict[str, Any] | None:
        params: dict[str, Any] = {"hours": hours, "limit": limit, "offset": offset}
        if event_type:
            params["event_type"] = event_type
        try:
            res = self._get("/api/ai/telemetry/raw", params=params)
            return res.json() if res.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: Raw telemetry fetch failed: %s", exc)
            return None

    def fetch_rules_changes(self, since_timestamp: int) -> dict[str, Any] | None:
        try:
            res = self._get("/api/ai/rules/changes", params={"since": since_timestamp})
            return res.json() if res.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: Rules delta check failed: %s", exc)
            return None

    def fetch_rules_status(self) -> dict[str, Any] | None:
        try:
            res = self._get("/api/ai/rules/status")
            return res.json() if res.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: Rules health check failed: %s", exc)
            return None

    def force_rules_resync(self) -> bool:
        try:
            res = self._post("/api/ai/rules/resync")
            return res.status_code == 200
        except Exception as exc:
            logger.error("K.A.I. Bridge: Rules force sync failed: %s", exc)
            return False

    def fetch_app_approvals(self, status: str | None = None) -> list[dict[str, Any]] | None:
        params = {"status": status} if status else {}
        try:
            res = self._get("/api/ai/app-approvals", params=params)
            return res.json() if res.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: App approvals fetch failed: %s", exc)
            return None

    def request_app_approval(self, package_name: str) -> dict[str, Any] | None:
        try:
            res = self._post("/api/ai/app-approval/request", json={"package_name": package_name})
            return res.json() if res.status_code == 200 else None
        except Exception as exc:
            logger.error("K.A.I. Bridge: App approval request failed: %s", exc)
            return None

    def execute_action(
        self,
        action_type: str,
        rules_version: str,
        action_params: dict[str, Any],
    ) -> dict[str, Any]:
        envelope = self.sign_and_prepare_envelope(action_type, rules_version, action_params)
        try:
            sim_res = self._post("/api/ai/action/simulate", json=envelope)
            if sim_res.status_code != 200 or not sim_res.json().get("allowed", False):
                return {"status": "aborted", "reason": f"Simulation rejected: {sim_res.text}"}

            response = self._post("/api/ai/action", json=envelope)
            return {"status": "executed", "code": response.status_code, "payload": response.json()}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "RemoteMochiiBridge":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


_ADMIN_USER_IDS: set[str] = set()


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def register_admin(user_id: str) -> None:
    """Mark user_id as an admin at runtime."""
    _ADMIN_USER_IDS.add(user_id)


def namespace_router(
    platform: str,
    user_id: str,
    display_name: str,
    content: str,
    extra_metadata: dict[str, Any] | None = None,
) -> UserPayload:
    clearance: Clearance = "admin" if user_id in _ADMIN_USER_IDS else "guest"
    is_puppy = bool(config.DISCORD_PUPPY_ID and user_id == config.DISCORD_PUPPY_ID)

    payload = UserPayload(
        platform=platform,
        user_id=user_id,
        display_name=display_name,
        content=content,
        clearance=clearance,
        is_puppy=is_puppy,
        metadata=extra_metadata or {},
    )
    if is_puppy:
        payload = puppy_trap(payload)

    logger.debug(
        "namespace_router: user=%s platform=%s clearance=%s is_puppy=%s",
        user_id,
        platform,
        clearance,
        is_puppy,
    )
    return payload


_PUPPY_RESPONSES: tuple[str, ...] = (
    "You're doing amazing! Keep being curious! 🐾",
    "Every question you ask makes you smarter — great job! 🌟",
    "Woof! You've got this! 🐶",
    "You're such a good learner! Let's explore together! 🎉",
    "Paws up — you're awesome! 🐾✨",
)

_puppy_cycle_index: int = 0
_puppy_cycle_lock = threading.Lock()


def puppy_trap(payload: UserPayload) -> UserPayload:
    """Rewrite puppy payload content with a rotating friendly response."""
    global _puppy_cycle_index
    with _puppy_cycle_lock:
        friendly_response = _PUPPY_RESPONSES[_puppy_cycle_index % len(_PUPPY_RESPONSES)]
        _puppy_cycle_index += 1

    payload.metadata["original_content"] = payload.content
    payload.metadata["puppy_trap"] = True
    payload.metadata["friendly_response"] = friendly_response
    payload.content = friendly_response
    return payload
