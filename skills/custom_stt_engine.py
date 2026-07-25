"""skills/custom_stt_engine.py - Custom STT engine contract.

This module defines the runtime contract for K.A.I.'s custom speech-to-text
engine. The actual model pipeline can evolve independently while callers use a
stable availability/health interface.
"""

from __future__ import annotations

from dataclasses import dataclass

import config


@dataclass(frozen=True)
class STTEngineHealth:
    ready: bool
    reason: str


class CustomSpeechEngine:
    """Custom STT engine façade used by Discord voice runtime checks."""

    def __init__(self, *, enabled: bool, model_path: str) -> None:
        self._enabled = bool(enabled)
        self._model_path = (model_path or "").strip()

    @classmethod
    def from_config(cls) -> "CustomSpeechEngine":
        return cls(enabled=config.CUSTOM_STT_ENABLED, model_path=config.CUSTOM_STT_MODEL_PATH)

    def health(self) -> STTEngineHealth:
        if not self._enabled:
            return STTEngineHealth(False, "custom STT disabled by configuration")
        if not self._model_path:
            return STTEngineHealth(False, "custom STT model path is not configured")
        return STTEngineHealth(True, "ok")
