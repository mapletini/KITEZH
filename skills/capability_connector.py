"""skills/capability_connector.py - Optional external capability pull client.

This module provides a narrow connector API for optional pull-style
capabilities (starting with knowledge retrieval). It is intentionally small
and returns deterministic error categories for tool-level handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import requests
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import HTTPError, ReadTimeout, RequestException
except ImportError:  # pragma: no cover - exercised in minimal test environments
    requests = None

    class RequestsConnectionError(Exception):
        pass

    class HTTPError(Exception):
        pass

    class ReadTimeout(Exception):
        pass

    class RequestException(Exception):
        pass

import config


class CapabilityConnectorError(RuntimeError):
    """Base class for capability connector failures."""


class CapabilityDisabledError(CapabilityConnectorError):
    """Raised when capability pull integration is disabled."""


class CapabilityAuthError(CapabilityConnectorError):
    """Raised when auth is missing or rejected by remote endpoint."""


class CapabilityTimeoutError(CapabilityConnectorError):
    """Raised on timeout while calling a capability endpoint."""


class CapabilityConnectionError(CapabilityConnectorError):
    """Raised when the capability endpoint cannot be reached."""


class CapabilityResponseError(CapabilityConnectorError):
    """Raised for malformed or unexpected response payloads."""


@dataclass(frozen=True)
class KnowledgeResult:
    """Normalized record returned from a knowledge retrieval endpoint."""

    title: str
    content: str
    score: float
    source: str


class CapabilityConnector:
    """HTTP client for optional pull-style external capabilities."""

    def __init__(
        self,
        *,
        enabled: bool,
        base_url: str,
        token: str,
        knowledge_path: str,
        timeout: float,
    ) -> None:
        self._enabled = bool(enabled)
        self._base_url = (base_url or "").rstrip("/")
        self._token = token
        self._knowledge_path = knowledge_path if knowledge_path.startswith("/") else f"/{knowledge_path}"
        self._timeout = float(timeout)
        self._session = requests.Session() if requests is not None else None

        if self._session is not None and self._token:
            self._session.headers.update({"Authorization": f"Bearer {self._token}"})
        if self._session is not None:
            self._session.headers.update(
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )

    @classmethod
    def from_config(cls) -> "CapabilityConnector":
        return cls(
            enabled=config.CAPABILITY_PULL_ENABLED,
            base_url=config.CAPABILITY_PULL_BASE_URL,
            token=config.CAPABILITY_PULL_TOKEN,
            knowledge_path=config.CAPABILITY_KNOWLEDGE_PATH,
            timeout=config.REQUEST_TIMEOUT,
        )

    def is_enabled(self) -> bool:
        return self._enabled and bool(self._base_url)

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CapabilityDisabledError("Capability pull integration is disabled.")
        if not self._base_url:
            raise CapabilityDisabledError("Capability pull base URL is not configured.")

    def lookup_knowledge(
        self,
        *,
        query: str,
        top_k: int = 5,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Retrieve knowledge context from the configured external endpoint."""
        self._require_enabled()
        if self._session is None:
            raise CapabilityConnectorError("Capability connector dependency 'requests' is not installed.")
        payload: dict[str, Any] = {
            "query": query,
            "top_k": max(1, min(25, int(top_k))),
            "context": context or {},
        }
        url = f"{self._base_url}{self._knowledge_path}"

        try:
            response = self._session.post(url, json=payload, timeout=self._timeout)
            if response.status_code in {401, 403}:
                raise CapabilityAuthError("Knowledge retrieval auth failed.")
            response.raise_for_status()
            data = response.json()
        except CapabilityAuthError:
            raise
        except ReadTimeout as exc:
            raise CapabilityTimeoutError("Knowledge retrieval timed out.") from exc
        except RequestsConnectionError as exc:
            raise CapabilityConnectionError("Knowledge retrieval endpoint is unreachable.") from exc
        except HTTPError as exc:
            raise CapabilityConnectorError(f"Knowledge retrieval HTTP error: {exc}") from exc
        except ValueError as exc:
            raise CapabilityResponseError("Knowledge retrieval returned invalid JSON.") from exc
        except RequestException as exc:
            raise CapabilityConnectorError(f"Knowledge retrieval request failed: {exc}") from exc

        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raise CapabilityResponseError("Knowledge retrieval response missing list field 'results'.")

        normalized: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "title": str(item.get("title", "")),
                    "content": str(item.get("content", "")),
                    "score": float(item.get("score", 0.0) or 0.0),
                    "source": str(item.get("source", "external")),
                }
            )

        return {
            "query": query,
            "count": len(normalized),
            "results": normalized,
            "raw": data,
        }

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
