"""
affective_core.py — Cognitive / vocal engine for K.A.I.

Responsibilities
----------------
* **PADState** — immutable snapshot of the 3-D Pleasure–Arousal–Dominance emotional space.
* **AffectiveEngine** — stateful momentum-based drift across discrete time ticks.
* **AudioEnvelopeWrapper** — numpy-backed utility generating dynamic formant-filtered 
  additive sound synthesis based directly on PAD coordinates.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PAD_MIN: float = -1.0
PAD_MAX: float = 1.0
DEFAULT_INERTIA: float = 0.85
SAMPLE_RATE: int = 44_100
FRAME_DURATION: float = 0.02

# ---------------------------------------------------------------------------
# PAD snapshot
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = PAD_MIN, hi: float = PAD_MAX) -> float:
    return max(lo, min(hi, value))

@dataclass(frozen=True)
class PADState:
    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "pleasure", _clamp(self.pleasure))
        object.__setattr__(self, "arousal", _clamp(self.arousal))
        object.__setattr__(self, "dominance", _clamp(self.dominance))

    def as_array(self) -> npt.NDArray[np.float64]:
        return np.array([self.pleasure, self.arousal, self.dominance], dtype=np.float64)

    def distance_to(self, other: "PADState") -> float:
        return float(np.linalg.norm(self.as_array() - other.as_array()))

# ---------------------------------------------------------------------------
# Affective engine
# ---------------------------------------------------------------------------

class AffectiveEngine:
    def __init__(self, initial_state: PADState | None = None, inertia: float = DEFAULT_INERTIA) -> None:
        self._current: PADState = initial_state or PADState()
        self._target: PADState = self._current
        self._inertia: float = max(0.0, min(1.0, inertia))
        self._tick_count: int = 0

    @property
    def current_state(self) -> PADState:
        return self._current

    def apply_impulse(self, delta_p: float, delta_a: float, delta_d: float) -> None:
        self._target = PADState(
            pleasure=self._target.pleasure + delta_p,
            arousal=self._target.arousal + delta_a,
            dominance=self._target.dominance + delta_d,
        )

    def tick(self, steps: int = 1) -> PADState:
        for _ in range(max(1, steps)):
            alpha = 1.0 - self._inertia
            curr = self._current.as_array()
            tgt = self._target.as_array()
            new_vec = curr * self._inertia + tgt * alpha
            self._current = PADState(
                pleasure=float(new_vec[0]),
                arousal=float(new_vec[1]),
                dominance=float(new_vec[2]),
            )
            self._tick_count += 1
        return self._current

# ---------------------------------------------------------------------------
# Audio envelope wrapper (Formant Synth Edition)
# ---------------------------------------------------------------------------

class AudioEnvelopeWrapper:
    def __init__(self, engine: AffectiveEngine, sample_rate: int = SAMPLE_RATE) -> None:
        self._engine = engine
        self._sample_rate = sample_rate
        # Formant frequencies for premium cyber lilt [F1, F2]
        self.base_formants = [500.0, 1500.0]

    def generate_frame(self, duration: float = 1.5) -> npt.NDArray[np.float64]:
        """
        Calculates a full float64 additive wave array.
        Maps PAD vectors directly to phase-locked glottal pulse harmonics and formant filters.
        """
        state = self._engine.current_state
        total_samples = int(self._sample_rate * duration)
        t = np.linspace(0, duration, total_samples, endpoint=False, dtype=np.float64)

        # 1. Map Arousal & Pleasure to Fundamental Frequency (Pitch)
        # Remap arousal to a slightly higher baseline for the cyber lilt
        f0 = 130.0 + ((state.arousal + 1) / 2 * 150.0) + (state.pleasure * 30.0)

        # 2. Synthesize phase-locked harmonic stack (The source buzz)
        signal = np.zeros_like(t)
        harmonics = 6
        for i in range(1, harmonics + 1):
            # If pleasure is negative (stress), overtones become un-dampened/metallic
            weight = (1.0 / i) if state.pleasure >= 0 else (1.0 / (i ** 0.6))
            signal += weight * np.sin(2.0 * np.pi * (f0 * i) * t)

        # 3. Shape the formant filters using Dominance metrics
        # High dominance = narrow band, clean resonance. Low dominance = distorted and wide.
        bandwidth = 40.0 + (90.0 * (1.0 - state.dominance))
        f1 = self.base_formants[0] * (1.0 + (0.1 * -state.pleasure))
        f2 = self.base_formants[1] * (1.0 + (0.15 * state.arousal))

        # Apply convolutional resonant bandpass shapes mathematically
        filter_1 = np.exp(-bandwidth * t) * np.sin(2.0 * np.pi * f1 * t)
        filter_2 = np.exp(-bandwidth * t) * np.sin(2.0 * np.pi * f2 * t)
        
        filtered_audio = np.convolve(signal, filter_1, mode='same') + np.convolve(signal, filter_2, mode='same')

        # 4. Apply a sleek trapezoid breathing envelope.
        # Clamp so attack + release <= total_samples (no region overlap).
        envelope = np.ones(total_samples, dtype=np.float64)
        attack = min(int(self._sample_rate * 0.1), total_samples // 2)
        release = min(int(self._sample_rate * 0.15), total_samples - attack)

        envelope[:attack] = np.linspace(0.0, 1.0, attack)
        envelope[-release:] = np.linspace(1.0, 0.0, release)
        
        final_wave = filtered_audio * envelope

        # Avoid audio clipping bugs
        if np.max(np.abs(final_wave)) > 0:
            final_wave = final_wave / np.max(np.abs(final_wave))

        return final_wave
      
