"""
affective_core.py — Cognitive / vocal engine for the Kitezh intelligence engine.

Responsibilities
----------------
* **PADState** — immutable snapshot of the 3-D Pleasure–Arousal–Dominance
  emotional space (all values clamped to ``[-1.0, 1.0]``).
* **AffectiveEngine** — stateful emotional state machine with momentum-based
  drift toward a target PAD vector across discrete time ticks.
* **AudioEnvelopeWrapper** — numpy-backed utility that maps the current PAD
  coordinates onto synthesis-ready audio envelope parameters, laying the
  groundwork for dynamic additive sound synthesis.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Valid range for all PAD axes.
PAD_MIN: float = -1.0
PAD_MAX: float = 1.0

#: Default inertia coefficient (0 = instant snap, 1 = never moves).
DEFAULT_INERTIA: float = 0.85

#: Sample rate assumed by the audio envelope wrapper (Hz).
SAMPLE_RATE: int = 44_100

#: Duration of a single envelope frame in seconds.
FRAME_DURATION: float = 0.02  # 20 ms


# ---------------------------------------------------------------------------
# PAD snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PADState:
    """
    Immutable snapshot of the Pleasure–Arousal–Dominance emotional coordinates.

    All three axes are clamped to ``[-1.0, 1.0]`` on construction.
    """

    pleasure: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "pleasure", _clamp(self.pleasure))
        object.__setattr__(self, "arousal", _clamp(self.arousal))
        object.__setattr__(self, "dominance", _clamp(self.dominance))

    def as_array(self) -> npt.NDArray[np.float64]:
        """Return PAD values as a 1-D numpy array ``[P, A, D]``."""
        return np.array([self.pleasure, self.arousal, self.dominance], dtype=np.float64)

    def distance_to(self, other: "PADState") -> float:
        """Euclidean distance between two PAD states."""
        return float(np.linalg.norm(self.as_array() - other.as_array()))

    def __repr__(self) -> str:
        return (
            f"PADState(P={self.pleasure:+.3f}, "
            f"A={self.arousal:+.3f}, "
            f"D={self.dominance:+.3f})"
        )


# ---------------------------------------------------------------------------
# Affective engine
# ---------------------------------------------------------------------------


class AffectiveEngine:
    """
    Stateful 3-D emotional state machine.

    The engine maintains a *current* PAD state and a *target* PAD state.
    Each call to :meth:`tick` nudges the current state toward the target
    according to a momentum/inertia coefficient — higher inertia means
    slower, smoother drift.

    Example
    -------
    >>> engine = AffectiveEngine()
    >>> engine.set_target(PADState(pleasure=0.8, arousal=0.4, dominance=0.2))
    >>> for _ in range(30):
    ...     engine.tick()
    >>> engine.current_state
    PADState(P=+0.???, A=+0.???, D=+0.???)
    """

    def __init__(
        self,
        initial_state: PADState | None = None,
        inertia: float = DEFAULT_INERTIA,
    ) -> None:
        self._current: PADState = initial_state or PADState()
        self._target: PADState = self._current
        self._inertia: float = max(0.0, min(1.0, inertia))
        self._tick_count: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> PADState:
        """The engine's present PAD coordinates."""
        return self._current

    @property
    def target_state(self) -> PADState:
        """The PAD coordinates the engine is drifting toward."""
        return self._target

    @property
    def inertia(self) -> float:
        """Inertia coefficient in ``[0.0, 1.0]``."""
        return self._inertia

    @inertia.setter
    def inertia(self, value: float) -> None:
        self._inertia = max(0.0, min(1.0, value))

    @property
    def tick_count(self) -> int:
        """Total number of ticks processed since engine creation."""
        return self._tick_count

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_target(self, target: PADState) -> None:
        """Set a new emotional target for the drift to converge on."""
        self._target = target
        logger.debug("AffectiveEngine target updated → %s", target)

    def snap_to(self, state: PADState) -> None:
        """Immediately teleport to *state* without momentum (e.g. on reset)."""
        self._current = state
        self._target = state
        logger.debug("AffectiveEngine snapped to %s", state)

    def tick(self, steps: int = 1) -> PADState:
        """
        Advance the emotional state machine by *steps* ticks.

        Each tick applies exponential momentum smoothing::

            current = current * inertia + target * (1 - inertia)

        Returns the new :class:`PADState` after all steps are applied.
        """
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

        logger.debug(
            "AffectiveEngine tick #%d → %s (Δ=%.4f)",
            self._tick_count,
            self._current,
            self._current.distance_to(self._target),
        )
        return self._current

    def is_settled(self, tolerance: float = 1e-3) -> bool:
        """
        Return *True* when the engine has converged within *tolerance* of its
        target (Euclidean distance in PAD space).
        """
        return self._current.distance_to(self._target) < tolerance

    def apply_impulse(self, delta_p: float, delta_a: float, delta_d: float) -> None:
        """
        Instantaneously shift the *target* PAD state by the supplied deltas.

        Useful for injecting emotional events (e.g. a user compliment nudges
        pleasure up by 0.2) without overwriting the entire target.
        """
        self._target = PADState(
            pleasure=self._target.pleasure + delta_p,
            arousal=self._target.arousal + delta_a,
            dominance=self._target.dominance + delta_d,
        )
        logger.debug("AffectiveEngine impulse → target now %s", self._target)


# ---------------------------------------------------------------------------
# Audio envelope wrapper
# ---------------------------------------------------------------------------


class EnvelopeParameters(NamedTuple):
    """Synthesis-ready parameters derived from the current PAD state."""

    amplitude: float    # master gain in [0.0, 1.0]
    frequency_hz: float # fundamental frequency in Hz
    brightness: float   # spectral brightness / harmonic richness in [0.0, 1.0]
    attack_ms: float    # attack time in milliseconds
    release_ms: float   # release time in milliseconds


class AudioEnvelopeWrapper:
    """
    Maps live PAD coordinates to audio synthesis envelope parameters using
    numpy for efficient numerical computation.

    The mapping is intentionally simple and perceptually motivated:

    * **Pleasure** → amplitude (higher pleasure → louder, warmer tone)
    * **Arousal**  → frequency & brightness (higher arousal → higher pitch,
      richer harmonics, faster attack)
    * **Dominance** → dynamic range / release (higher dominance → shorter
      release, more assertive envelope shape)

    Call :meth:`compute_envelope` to obtain a :class:`EnvelopeParameters`
    snapshot or :meth:`generate_frame` to produce a raw numpy waveform
    frame ready for additive synthesis.
    """

    #: Fundamental frequency range (Hz) mapped from arousal [-1, 1].
    FREQ_LOW: float = 80.0
    FREQ_HIGH: float = 880.0

    #: Attack range in milliseconds mapped from arousal [-1, 1].
    ATTACK_LOW_MS: float = 200.0
    ATTACK_HIGH_MS: float = 5.0

    #: Release range in milliseconds mapped from dominance [-1, 1].
    RELEASE_LOW_MS: float = 2000.0
    RELEASE_HIGH_MS: float = 50.0

    def __init__(
        self,
        engine: AffectiveEngine,
        sample_rate: int = SAMPLE_RATE,
        frame_duration: float = FRAME_DURATION,
    ) -> None:
        self._engine = engine
        self._sample_rate = sample_rate
        self._frame_samples = max(1, int(sample_rate * frame_duration))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compute_envelope(self) -> EnvelopeParameters:
        """
        Derive synthesis parameters from the engine's current PAD state.

        All mappings are linear interpolations over the ``[-1, 1]`` PAD range.
        """
        state = self._engine.current_state

        # Pleasure → amplitude: remap [-1, 1] → [0.05, 1.0]
        amplitude = float(np.interp(state.pleasure, [-1.0, 1.0], [0.05, 1.0]))

        # Arousal → frequency (log-scale feels more natural for pitch)
        t_arousal = (state.arousal + 1.0) / 2.0  # normalize to [0, 1]
        frequency_hz = float(
            self.FREQ_LOW * math.exp(t_arousal * math.log(self.FREQ_HIGH / self.FREQ_LOW))
        )

        # Arousal → brightness (spectral richness)
        brightness = float(np.interp(state.arousal, [-1.0, 1.0], [0.0, 1.0]))

        # Arousal → attack time (higher arousal = faster attack)
        attack_ms = float(
            np.interp(state.arousal, [-1.0, 1.0], [self.ATTACK_LOW_MS, self.ATTACK_HIGH_MS])
        )

        # Dominance → release time (higher dominance = shorter, sharper release)
        release_ms = float(
            np.interp(
                state.dominance, [-1.0, 1.0], [self.RELEASE_LOW_MS, self.RELEASE_HIGH_MS]
            )
        )

        return EnvelopeParameters(
            amplitude=amplitude,
            frequency_hz=frequency_hz,
            brightness=brightness,
            attack_ms=attack_ms,
            release_ms=release_ms,
        )

    def generate_frame(self) -> npt.NDArray[np.float64]:
        """
        Generate a single audio frame as a numpy float64 array.

        The frame is a sum of harmonics (additive synthesis) scaled by the
        envelope derived from the current PAD state.  The number of harmonic
        partials grows with the *brightness* parameter.
        """
        params = self.compute_envelope()
        t: npt.NDArray[np.float64] = np.linspace(
            0, FRAME_DURATION, self._frame_samples, endpoint=False, dtype=np.float64
        )

        max_partials = max(1, int(1 + params.brightness * 15))
        frame = np.zeros(self._frame_samples, dtype=np.float64)

        for k in range(1, max_partials + 1):
            harmonic_freq = params.frequency_hz * k
            # Amplitude falls off with harmonic number (natural timbre model)
            partial_amp = params.amplitude / k
            frame += partial_amp * np.sin(2.0 * np.pi * harmonic_freq * t)

        # Apply a simple trapezoid amplitude envelope over the frame
        frame *= _build_trapezoid_envelope(
            self._frame_samples,
            attack_ms=params.attack_ms,
            release_ms=params.release_ms,
            sample_rate=self._sample_rate,
        )

        return frame

    @property
    def sample_rate(self) -> int:
        """Sample rate in Hz."""
        return self._sample_rate

    @property
    def frame_samples(self) -> int:
        """Number of samples per frame."""
        return self._frame_samples


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = PAD_MIN, hi: float = PAD_MAX) -> float:
    """Clamp *value* to the closed interval ``[lo, hi]``."""
    return max(lo, min(hi, value))


def _build_trapezoid_envelope(
    n_samples: int,
    attack_ms: float,
    release_ms: float,
    sample_rate: int,
) -> npt.NDArray[np.float64]:
    """
    Build a trapezoidal amplitude envelope of length *n_samples*.

    The envelope ramps linearly from 0 → 1 during the attack phase, holds at
    1, then ramps from 1 → 0 during the release phase.  If attack + release
    exceed the frame length, both are scaled proportionally.
    """
    attack_samples = int(sample_rate * attack_ms / 1000.0)
    release_samples = int(sample_rate * release_ms / 1000.0)

    total_ramp = attack_samples + release_samples
    if total_ramp > n_samples and total_ramp > 0:
        scale = n_samples / total_ramp
        attack_samples = int(attack_samples * scale)
        release_samples = int(release_samples * scale)

    envelope = np.ones(n_samples, dtype=np.float64)

    if attack_samples > 0:
        envelope[:attack_samples] = np.linspace(0.0, 1.0, attack_samples)

    if release_samples > 0:
        release_start = n_samples - release_samples
        envelope[release_start:] = np.linspace(1.0, 0.0, release_samples)

    return envelope
