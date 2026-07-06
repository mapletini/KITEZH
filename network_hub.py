"""
network_hub.py — Central orchestrator for the Kitezh intelligence engine.

Responsibilities
----------------
* **RemoteMochiiBridge** — thin HTTP client that queries the remote
  FastAPI/Discord backend with strict custom-header validation.
* **namespace_router** — multi-tenant payload wrapper that resolves
  clearance levels (admin / guest) for incoming user messages.
* **puppy_trap** — interceptor layer that detects the configured puppy
  Discord ID and redirects processing to a gentle, encouraging protocol.

All remote I/O is isolated here; no business logic bleeds into this file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import requests
from requests.exceptions import ConnectionError, ReadTimeout, RequestException

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

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
