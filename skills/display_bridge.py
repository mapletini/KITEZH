from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import config


class DisplayBridge:
    """Thread-safe latest-state publisher shared across display targets."""

    def __init__(self, state_path: str | None = None) -> None:
        self.state_path = Path(state_path or config.DISPLAY_STATE_PATH)
        self._lock = threading.Lock()
        self._version = 0
        self._state: dict[str, Any] = self._default_state()
        self._persist_locked()

    def _default_state(self) -> dict[str, Any]:
        return {
            "timestamp": time.time(),
            "version": 0,
            "mode": "idle",
            "emotion": {"label": "neutral", "pad": [0.0, 0.0, 0.0], "intensity": 0.0},
            "desires": [],
            "intentions": [],
            "narrative": "Kai is quiet but present.",
            "preferences": [],
            "relationship": {},
            "message": "",
        }

    def _persist_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def publish(self, state: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._version += 1
            merged = dict(self._state)
            merged.update(state)
            merged["timestamp"] = time.time()
            merged["version"] = self._version
            self._state = merged
            self._persist_locked()
            return dict(self._state)

    def latest(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)


def load_display_state(state_path: str | None = None) -> dict[str, Any]:
    path = Path(state_path or config.DISPLAY_STATE_PATH)
    default_state = {
        "timestamp": time.time(),
        "version": 0,
        "mode": "idle",
        "emotion": {"label": "neutral", "pad": [0.0, 0.0, 0.0], "intensity": 0.0},
        "desires": [],
        "intentions": [],
        "narrative": "Kai is quiet but present.",
        "preferences": [],
        "relationship": {},
        "message": "",
    }
    if not path.exists():
        return default_state
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_state


def build_display_payload(
    emotion: dict[str, Any],
    *,
    desires: list[str] | None = None,
    intentions: list[str] | None = None,
    narrative: str = "",
    preferences: list[dict[str, Any]] | None = None,
    relationship: dict[str, Any] | None = None,
    mode: str = "active",
    message: str = "",
) -> dict[str, Any]:
    return {
        "mode": mode,
        "emotion": emotion,
        "desires": list(desires or []),
        "intentions": list(intentions or []),
        "narrative": narrative,
        "preferences": list(preferences or []),
        "relationship": dict(relationship or {}),
        "message": message,
    }
