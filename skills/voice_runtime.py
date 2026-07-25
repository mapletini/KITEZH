"""skills/voice_runtime.py - Hybrid streaming speech runtime primitives.

This module provides local building blocks for K.A.I.'s custom speech engine:
- fixed-duration ring buffer for raw PCM
- simple VAD-style speech/silence tracking
- hybrid partial/final transcript orchestration hooks

It is transport-agnostic so Discord voice and future interfaces can share one
speech runtime contract.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DecoderConfig:
    sample_rate: int = 16_000
    frame_ms: int = 20
    decode_interval_ms: int = 120
    endpoint_silence_ms: int = 400
    bargein_confidence: float = 0.65
    partial_stability_required: int = 2
    buffer_seconds: int = 30


@dataclass(frozen=True)
class DecodeEvent:
    kind: str
    text: str
    confidence: float


class RollingAudioBuffer:
    """In-memory ring buffer for raw PCM samples."""

    def __init__(self, *, max_seconds: int, sample_rate: int) -> None:
        max_samples = max(1, int(max_seconds) * int(sample_rate))
        self._samples = deque(maxlen=max_samples)

    def push(self, pcm: list[float]) -> None:
        self._samples.extend(float(x) for x in pcm)

    def snapshot(self) -> list[float]:
        return list(self._samples)


class HybridSpeechRuntime:
    """Hybrid partial-stream and endpoint-finalization speech runtime.

    The runtime itself does not ship a model. A caller provides:
    - partial_decoder(frames) -> (text, confidence)
    - final_decoder(segment) -> (text, confidence)

    This keeps the engine custom while preserving deterministic orchestration.
    """

    def __init__(
        self,
        *,
        config: DecoderConfig,
        partial_decoder: Callable[[list[float]], tuple[str, float]],
        final_decoder: Callable[[list[float]], tuple[str, float]],
    ) -> None:
        self._cfg = config
        self._partial_decoder = partial_decoder
        self._final_decoder = final_decoder
        self._buffer = RollingAudioBuffer(max_seconds=config.buffer_seconds, sample_rate=config.sample_rate)
        self._since_decode_ms = 0
        self._silence_ms = 0
        self._segment: list[float] = []
        self._last_partial = ""
        self._partial_stable_count = 0

    @property
    def buffer(self) -> RollingAudioBuffer:
        return self._buffer

    def ingest_frame(self, *, frame: list[float], speech_confidence: float, tts_active: bool) -> list[DecodeEvent]:
        """Ingest one frame and return zero or more decode events."""
        events: list[DecodeEvent] = []
        frame_ms = self._cfg.frame_ms
        self._buffer.push(frame)

        is_speech = speech_confidence >= 0.5
        if is_speech:
            self._silence_ms = 0
            self._segment.extend(frame)
        else:
            self._silence_ms += frame_ms

        if tts_active and speech_confidence >= self._cfg.bargein_confidence:
            events.append(DecodeEvent(kind="barge_in", text="", confidence=speech_confidence))

        self._since_decode_ms += frame_ms
        if self._since_decode_ms >= self._cfg.decode_interval_ms and self._segment:
            self._since_decode_ms = 0
            text, conf = self._partial_decoder(self._segment)
            if text and text == self._last_partial:
                self._partial_stable_count += 1
            else:
                self._partial_stable_count = 1 if text else 0
                self._last_partial = text
            if text:
                stable = self._partial_stable_count >= self._cfg.partial_stability_required
                kind = "partial_stable" if stable else "partial"
                events.append(DecodeEvent(kind=kind, text=text, confidence=conf))

        if self._segment and self._silence_ms >= self._cfg.endpoint_silence_ms:
            final_text, final_conf = self._final_decoder(self._segment)
            events.append(DecodeEvent(kind="final", text=final_text, confidence=final_conf))
            self._segment = []
            self._silence_ms = 0
            self._since_decode_ms = 0
            self._last_partial = ""
            self._partial_stable_count = 0

        return events
