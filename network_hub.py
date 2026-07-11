"""
network_hub.py — Complete cryptographic client gateway and API surface mapping for K.A.I.
"""

from __future__ import annotations

import os
import hmac
import uuid
import json
import time
import hashlib
import requests
import logging
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)

class RemoteMochiiBridge:
    def __init__(self):
        # Read parameters from local configuration env
        self.api_url = os.getenv("MOCHII_API_URL", "https://your-remote-backend.com")
        self.bridge_secret = os.getenv("AI_BRIDGE_SECRET", "change_me_ai_bridge_secret")
        self.signing_secret = os.getenv("AI_COMMAND_SIGNING_SECRET", self.bridge_secret)
        
        self.headers = {
            "x-ai-key": self.bridge_secret,
            "Content-Type": "application/json"
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    # ---------------------------------------------------------------------------
    # Cryptographic Envelope Signing Engine
    # ---------------------------------------------------------------------------

    def sign_and_prepare_envelope(self, action_type: str, rules_version: str, action_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Structures a standard action payload and generates a strict,
        replay-protected canonical HMAC-SHA256 envelope signature.
        """
        now = int(time.time())
        
        payload = {
            "action_type": action_type,
            "rules_version_used": rules_version,
            "command_nonce": str(uuid.uuid4()),
            "command_issued_at": now,
            "command_expires_at": now + 300,  # Max 300-second TTL enforcement
            "actor": "K.A.I. Core System",
            "rationale_summary": "Autonomous script evaluation pass.",
            **action_params
        }

        # Serialize to compact, sorted-key JSON (no spaces)
        canonical_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        # Compute HMAC-SHA256 hex digest
        signature = hmac.new(
            self.signing_secret.encode("utf-8"),
            canonical_json.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        payload["command_signature"] = signature
        return payload

    # ---------------------------------------------------------------------------
    # Core Context & Ingest Operations
    # ---------------------------------------------------------------------------

    def fetch_unified_context(self) -> Optional[Dict[str, Any]]:
        """Queries the combined convenience gateway for state hydration."""
        try:
            response = self.session.get(f"{self.api_url}/api/ai/context", timeout=10)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Context call failed: {str(e)}")
            return None

    def ingest_snapshot(self, device_id: str, snapshot: Dict[str, Any], usage_stats: Dict[str, Any], app_stats: Dict[str, Any]) -> bool:
        """Pushes an updated device metric snapshot up to the server datastore."""
        payload = {
            "device_id": device_id,
            "captured_at": int(time.time()),
            "snapshot": snapshot,
            "usage_stats": usage_stats,
            "app_stats": app_stats
        }
        try:
            response = self.session.post(f"{self.api_url}/api/ai/ingest", json=payload, timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Ingest operation failed: {str(e)}")
            return False

    # ---------------------------------------------------------------------------
    # Telemetry Analytics
    # ---------------------------------------------------------------------------

    def fetch_telemetry_summary(self, hours: int = 24) -> Optional[Dict[str, Any]]:
        """Retrieves a summarized breakdown of puppy telemetry events."""
        try:
            res = self.session.get(f"{self.api_url}/api/ai/telemetry/summary", params={"hours": hours}, timeout=10)
            return res.json() if res.status_code == 200 else None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Telemetry fetch failed: {str(e)}")
            return None

    def fetch_raw_telemetry(self, hours: int = 24, limit: int = 200, offset: int = 0, event_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Accesses paginated raw telemetry data events."""
        params = {"hours": hours, "limit": limit, "offset": offset}
        if event_type:
            params["event_type"] = event_type
        try:
            res = self.session.get(f"{self.api_url}/api/ai/telemetry/raw", params=params, timeout=10)
            return res.json() if res.status_code == 200 else None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Raw telemetry fetch failed: {str(e)}")
            return None

    # ---------------------------------------------------------------------------
    # Rules Synced Feed Operations
    # ---------------------------------------------------------------------------

    def fetch_rules_changes(self, since_timestamp: int) -> Optional[Dict[str, Any]]:
        """Queries for rule status deltas since a checkpoint mark."""
        try:
            res = self.session.get(f"{self.api_url}/api/ai/rules/changes", params={"since": since_timestamp}, timeout=10)
            return res.json() if res.status_code == 200 else None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Rules delta check failed: {str(e)}")
            return None

    def fetch_rules_status(self) -> Optional[Dict[str, Any]]:
        """Checks rules health and synchronizer freshness status."""
        try:
            res = self.session.get(f"{self.api_url}/api/ai/rules/status", timeout=10)
            return res.json() if res.status_code == 200 else None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Rules health check failed: {str(e)}")
            return None

    def force_rules_resync(self) -> bool:
        """Forces an immediate server re-poll against the source channel."""
        try:
            res = self.session.post(f"{self.api_url}/api/ai/rules/resync", timeout=15)
            return res.status_code == 200
        except Exception as e:
            logger.error(f"K.A.I. Bridge: Rules force sync failed: {str(e)}")
            return False

    # ---------------------------------------------------------------------------
    # App-Action Approval Management
    # ---------------------------------------------------------------------------

    def fetch_app_approvals(self, status: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        """Gathers a listing of per-app package execution clearance models."""
        params = {"status": status} if status else {}
        try:
            res = self.session.get(f"{self.api_url}/api/ai/app-approvals", params=params, timeout=10)
            return res.json() if res.status_code == 200 else None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: App approvals fetch failed: {str(e)}")
            return None

    def request_app_approval(self, package_name: str) -> Optional[Dict[str, Any]]:
        """Dispatches a websocket authorization notice targeting an app package."""
        try:
            res = self.session.post(f"{self.api_url}/api/ai/app-approval/request", json={"package_name": package_name}, timeout=10)
            return res.json() if res.status_code == 200 else None
        except Exception as e:
            logger.error(f"K.A.I. Bridge: App approval request failed: {str(e)}")
            return None

    # ---------------------------------------------------------------------------
    # Command Execution Routes
    # ---------------------------------------------------------------------------

    def execute_action(self, action_type: str, rules_version: str, action_params: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches a signed command envelope across the gateway network."""
        envelope = self.sign_and_prepare_envelope(action_type, rules_version, action_params)
        
        try:
            # 1. Run dry-run simulation first
            sim_url = f"{self.api_url}/api/ai/action/simulate"
            sim_res = self.session.post(sim_url, json=envelope, timeout=10)
            
            if sim_res.status_code != 200 or not sim_res.json().get("allowed", False):
                return {"status": "aborted", "reason": f"Simulation rejected: {sim_res.text}"}

            # 2. Fire actual live command
            exec_url = f"{self.api_url}/api/ai/action"
            response = self.session.post(exec_url, json=envelope, timeout=10)
            return {"status": "executed", "code": response.status_code, "payload": response.json()}
            
        except Exception as e:
            return {"status": "failed", "error": str(e)}
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


# ---------------------------------------------------------------------------
# Remote client
# ---------------------------------------------------------------------------


class RemoteMochiiBridge:
    """
    HTTP client for the remote Mochii FastAPI/Discord backend.

    Every request includes the mandatory ``x-ai-key`` header so the backend
    can authenticate the engine without exposing credentials in the URL.
    """

    def __init__(
        self,
        base_url: str = config.REMOTE_BASE_URL,
        ai_key: str = config.AI_KEY,
        timeout: float = config.REQUEST_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-ai-key": ai_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def query_context(self, payload: UserPayload) -> ContextResponse:
        """
        POST *payload* to the remote ``/api/ai/context`` endpoint.

        Returns a :class:`ContextResponse` — never raises; all remote
        errors are captured and surfaced inside the response object.
        """
        url = f"{self._base_url}/api/ai/context"
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
            response = self._session.post(url, json=body, timeout=self._timeout)
            response.raise_for_status()
            return ContextResponse(success=True, data=response.json())
        except ReadTimeout:
            msg = f"Request to {url} timed out after {self._timeout}s"
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

    def health_check(self) -> bool:
        """Return *True* if the remote backend responds to a GET on ``/health``."""
        try:
            resp = self._session.get(
                f"{self._base_url}/health", timeout=self._timeout
            )
            return resp.status_code < 400
        except RequestException:
            return False

    def close(self) -> None:
        """Release the underlying connection pool."""
        self._session.close()

    def __enter__(self) -> "RemoteMochiiBridge":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Namespace router
# ---------------------------------------------------------------------------

#: User IDs that receive admin-level clearance.  Override via subclassing or
#: by populating this set at startup from a config source.
_ADMIN_USER_IDS: set[str] = set()


def register_admin(user_id: str) -> None:
    """Mark *user_id* as an admin at runtime."""
    _ADMIN_USER_IDS.add(user_id)


def namespace_router(
    platform: str,
    user_id: str,
    display_name: str,
    content: str,
    extra_metadata: dict[str, Any] | None = None,
) -> UserPayload:
    """
    Wrap raw incoming message fields into a :class:`UserPayload` and assign a
    clearance level.

    Clearance rules
    ~~~~~~~~~~~~~~~
    * ``"admin"`` — *user_id* is listed in ``_ADMIN_USER_IDS``.
    * ``"guest"`` — everyone else.

    The puppy-trap flag is also set here so that downstream consumers can
    branch without needing to re-inspect the configuration themselves.
    """
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


# ---------------------------------------------------------------------------
# Puppy trap
# ---------------------------------------------------------------------------

#: Friendly messages cycled through when the puppy trap fires.
_PUPPY_RESPONSES: tuple[str, ...] = (
    "You're doing amazing! Keep being curious! 🐾",
    "Every question you ask makes you smarter — great job! 🌟",
    "Woof! You've got this! 🐶",
    "You're such a good learner! Let's explore together! 🎉",
    "Paws up — you're awesome! 🐾✨",
)

_puppy_cycle_index: int = 0


def puppy_trap(payload: UserPayload) -> UserPayload:
    """
    Intercept a payload whose *user_id* matches ``DISCORD_PUPPY_ID``.

    The original content is archived in ``payload.metadata["original_content"]``
    and replaced with a friendly, encouraging message.  The payload is also
    tagged so the bridge can route it to a simplified, safe processing path.
    """
    global _puppy_cycle_index

    logger.info(
        "puppy_trap activated for user %s (%s)",
        payload.user_id,
        payload.display_name,
    )

    friendly_response = _PUPPY_RESPONSES[_puppy_cycle_index % len(_PUPPY_RESPONSES)]
    _puppy_cycle_index += 1

    payload.metadata["original_content"] = payload.content
    payload.metadata["puppy_trap"] = True
    payload.metadata["friendly_response"] = friendly_response
    payload.content = friendly_response
    return payload
