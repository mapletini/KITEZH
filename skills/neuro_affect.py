"""
skills/neuro_affect.py — Advanced neuro-chemical emotional simulation for K.A.I.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

# Supported regulation families: suppression mutes outward expression, reappraisal
# actively reframes the event, and rumination prolongs the negative loop.
EmotionRegulationStrategy = Literal["suppression", "reappraisal", "rumination"]

# Cortisol above this level counts as sustained strain and starts depressing mood baselines.
STRESS_THRESHOLD = 0.6
# Cortisol below this level allows slow restoration of allostatic load and baseline drift.
RESTFUL_CORTISOL_THRESHOLD = 0.35
# Drift bounds keep adaptive baselines degraded but still recoverable over time.
MIN_BASELINE_DRIFT = 0.55
MAX_BASELINE_DRIFT = 1.05
# Burnout can cap confidence, but never below this floor.
MIN_SEROTONIN_CAP = 0.35


@dataclass
class Neurotransmitters:
    # Levels range from 0.0 to 1.0
    dopamine: float = 0.5      # Drives Pleasure (Reward/Success)
    noradrenaline: float = 0.2 # Drives Arousal (Alertness/Urgency)
    serotonin: float = 0.8     # Drives Dominance (Confidence/Control)
    cortisol: float = 0.1      # Drives Stress (Reduces Pleasure, spikes Arousal)
    oxytocin: float = 0.15     # Drives social bonding / trust buffering


class NeuroChemicalEngine:
    PLUTCHIK_EMOTIONS = {
        "joy": np.array([0.85, 0.65, 0.65]),
        "trust": np.array([0.65, 0.25, 0.60]),
        "fear": np.array([-0.75, 0.85, 0.10]),
        "surprise": np.array([0.10, 0.95, 0.25]),
        "sadness": np.array([-0.85, 0.20, 0.15]),
        "disgust": np.array([-0.80, 0.35, 0.55]),
        "anger": np.array([-0.70, 0.85, 0.85]),
        "anticipation": np.array([0.35, 0.70, 0.70]),
        "awe": np.array([0.15, 0.90, 0.20]),
        "submission": np.array([-0.20, 0.45, 0.10]),
        "remorse": np.array([-0.55, 0.35, 0.25]),
        "love": np.array([0.90, 0.45, 0.55]),
    }

    def __init__(self) -> None:
        self.homeostatic_baselines = Neurotransmitters(
            dopamine=0.5,
            noradrenaline=0.2,
            serotonin=0.8,
            cortisol=0.1,
            oxytocin=0.15,
        )
        self.baselines = Neurotransmitters(**asdict(self.homeostatic_baselines))
        self.chemicals = Neurotransmitters(**asdict(self.homeostatic_baselines))
        self.baseline_drift: dict[str, float] = {
            "dopamine": 1.0,
            "noradrenaline": 1.0,
            "serotonin": 1.0,
            "cortisol": 1.0,
            "oxytocin": 1.0,
        }
        self.user_bonds: dict[str, float] = {}
        self.active_user_id: str | None = None
        self.conflict: float = 0.0
        self.allostatic_load: float = 0.0
        self.suppression_pressure: float = 0.0
        self.rumination: float = 0.0
        self.expression_clamp: float = 1.0
        self.last_update = time.time()
        self._refresh_baselines()

    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
        return max(lo, min(hi, value))

    def set_active_user(self, user_id: str | None) -> None:
        self.active_user_id = user_id or None
        self._refresh_baselines()

    def get_social_bond(self, user_id: str | None = None) -> float:
        if not user_id:
            return 0.0
        return self.user_bonds.get(user_id, 0.0)

    def get_serotonin_cap(self) -> float:
        return max(MIN_SEROTONIN_CAP, 1.0 - (self.allostatic_load * 0.5))

    def _refresh_baselines(self) -> None:
        active_bond = self.get_social_bond(self.active_user_id)
        self.baselines = Neurotransmitters(
            dopamine=self._clamp(self.homeostatic_baselines.dopamine * self.baseline_drift["dopamine"]),
            noradrenaline=self._clamp(
                self.homeostatic_baselines.noradrenaline * self.baseline_drift["noradrenaline"]
            ),
            serotonin=self._clamp(self.homeostatic_baselines.serotonin * self.baseline_drift["serotonin"]),
            cortisol=self._clamp(self.homeostatic_baselines.cortisol * self.baseline_drift["cortisol"]),
            oxytocin=self._clamp(max(self.homeostatic_baselines.oxytocin, active_bond * 0.6)),
        )

    def _current_baseline_pad(self) -> np.ndarray:
        bond = self.get_social_bond(self.active_user_id)
        baseline_pleasure = (self.baselines.dopamine * 1.4) - (self.baselines.cortisol * 1.45)
        baseline_pleasure += (self.baselines.oxytocin * 0.3) + (bond * 0.2)
        baseline_arousal = self.baselines.noradrenaline * 0.75 + self.baselines.cortisol * 0.25
        baseline_dominance = self.baselines.serotonin - (self.baselines.cortisol * 0.5) + (bond * 0.12)
        return np.array(
            [
                max(-1.0, min(1.0, baseline_pleasure)),
                self._clamp(baseline_arousal),
                self._clamp(baseline_dominance),
            ]
        )

    def _apply_conflict_response(self, positive_signal: float, negative_signal: float) -> None:
        if positive_signal > 0 and negative_signal > 0:
            conflict_intensity = self._clamp((positive_signal + negative_signal) / 2)
            self.conflict = self._clamp(max(self.conflict * 0.5, conflict_intensity * 0.7))
            self.chemicals.noradrenaline = self._clamp(
                self.chemicals.noradrenaline + (self.conflict * 0.18)
            )
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol + (self.conflict * 0.15))
        else:
            self.conflict = max(0.0, self.conflict - 0.08)

    def _apply_allostatic_adaptation(self, positive_signal: float, recovery_signal: float) -> None:
        if self.chemicals.cortisol > STRESS_THRESHOLD:
            strain = 0.03 + (self.conflict * 0.04)
            self.allostatic_load = self._clamp(self.allostatic_load + strain)
            self.baseline_drift["dopamine"] = max(
                MIN_BASELINE_DRIFT,
                self.baseline_drift["dopamine"] - (0.015 + self.conflict * 0.01),
            )
            self.baseline_drift["serotonin"] = max(
                MIN_BASELINE_DRIFT,
                self.baseline_drift["serotonin"] - (0.02 + self.conflict * 0.01),
            )
        else:
            restoration = (positive_signal * 0.02) + (recovery_signal * 0.04)
            self.allostatic_load = max(0.0, self.allostatic_load - restoration)
            self.baseline_drift["dopamine"] = min(
                MAX_BASELINE_DRIFT,
                self.baseline_drift["dopamine"] + restoration,
            )
            self.baseline_drift["serotonin"] = min(
                MAX_BASELINE_DRIFT,
                self.baseline_drift["serotonin"] + (restoration * 0.8),
            )

    def apply_stimulus(
        self,
        reward: float = 0.0,
        threat: float = 0.0,
        success: float = 0.0,
        uncertainty: float = 0.0,
        frustration: float = 0.0,
        recovery: float = 0.0,
        user_id: str | None = None,
    ) -> None:
        """
        Applies a set of emotional stimuli, updating neurochemical levels.

        Parameters
        ----------
        reward      : positive outcome — spikes dopamine, reduces cortisol.
        threat      : perceived danger / rule violation — spikes cortisol and noradrenaline.
        success     : task completion — boosts serotonin and dopamine.
        uncertainty : ambiguous or confusing input — lowers serotonin, raises noradrenaline.
        frustration : blocked goal or repeated failure — raises cortisol, drops dopamine.
        recovery    : calming / resolution event — reduces cortisol and noradrenaline.
        user_id     : optional interaction partner for oxytocin bonding and trust buffering.
        """
        self.set_active_user(user_id or self.active_user_id)
        positive_signal = reward + success + recovery
        negative_signal = threat + uncertainty + frustration
        bond_strength = self.get_social_bond(self.active_user_id)
        threat_buffer = 1.0 - (bond_strength * 0.35)
        uncertainty_buffer = 1.0 - (bond_strength * 0.15)

        if reward > 0:
            self.chemicals.dopamine = self._clamp(self.chemicals.dopamine + reward)
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol - (reward / 2))

        if threat > 0:
            effective_threat = threat * threat_buffer
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol + effective_threat)
            self.chemicals.noradrenaline = self._clamp(
                self.chemicals.noradrenaline + (effective_threat * 1.5)
            )

        if success > 0:
            self.chemicals.serotonin = self._clamp(
                min(self.get_serotonin_cap(), self.chemicals.serotonin + success)
            )
            self.chemicals.dopamine = self._clamp(self.chemicals.dopamine + (success / 2))

        if uncertainty > 0:
            effective_uncertainty = uncertainty * uncertainty_buffer
            self.chemicals.serotonin = self._clamp(
                self.chemicals.serotonin - (effective_uncertainty * 0.8)
            )
            self.chemicals.noradrenaline = self._clamp(
                self.chemicals.noradrenaline + (effective_uncertainty * 0.6)
            )

        if frustration > 0:
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol + (frustration * 0.9))
            self.chemicals.dopamine = self._clamp(self.chemicals.dopamine - (frustration * 0.5))

        if recovery > 0:
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol - (recovery * 0.8))
            self.chemicals.noradrenaline = self._clamp(
                self.chemicals.noradrenaline - (recovery * 0.6)
            )
            self.chemicals.serotonin = self._clamp(
                min(self.get_serotonin_cap(), self.chemicals.serotonin + (recovery * 0.3))
            )

        if self.active_user_id and positive_signal > 0:
            bond_gain = positive_signal * 0.12
            self.user_bonds[self.active_user_id] = self._clamp(
                self.get_social_bond(self.active_user_id) + bond_gain
            )
            self.chemicals.oxytocin = self._clamp(
                max(self.chemicals.oxytocin, self.get_social_bond(self.active_user_id) * 0.8)
                + (bond_gain * 0.5)
            )
        elif self.active_user_id and negative_signal > 0:
            self.user_bonds[self.active_user_id] = max(
                0.0,
                self.get_social_bond(self.active_user_id) - (negative_signal * 0.03),
            )

        self._apply_conflict_response(positive_signal, negative_signal)
        self._apply_allostatic_adaptation(positive_signal, recovery)
        self.chemicals.serotonin = min(self.chemicals.serotonin, self.get_serotonin_cap())
        self._refresh_baselines()

        logger.info(
            "K.A.I. stimulus — reward:%.2f threat:%.2f success:%.2f "
            "uncertainty:%.2f frustration:%.2f recovery:%.2f user:%s conflict:%.2f load:%.2f",
            reward,
            threat,
            success,
            uncertainty,
            frustration,
            recovery,
            self.active_user_id,
            self.conflict,
            self.allostatic_load,
        )

    def regulate(
        self,
        strategy: EmotionRegulationStrategy,
        strength: float = 1.0,
        trigger: str | None = None,
    ) -> dict[str, float | str]:
        strength = self._clamp(strength)

        if strategy == "suppression":
            self.expression_clamp = max(0.45, self.expression_clamp - (0.25 * strength))
            self.suppression_pressure = self._clamp(
                self.suppression_pressure + (0.3 * strength)
            )
            self.chemicals.cortisol = self._clamp(
                self.chemicals.cortisol + (0.05 * self.suppression_pressure)
            )
            self.chemicals.noradrenaline = self._clamp(
                self.chemicals.noradrenaline + (0.04 * self.suppression_pressure)
            )
        elif strategy == "reappraisal":
            self.suppression_pressure = max(0.0, self.suppression_pressure - (0.15 * strength))
            self.rumination = max(0.0, self.rumination - (0.1 * strength))
            self.expression_clamp = min(1.0, self.expression_clamp + (0.2 * strength))
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol - (0.2 * strength))
            self.chemicals.serotonin = self._clamp(
                min(self.get_serotonin_cap(), self.chemicals.serotonin + (0.12 * strength))
            )
            self.chemicals.dopamine = self._clamp(self.chemicals.dopamine + (0.06 * strength))
        elif strategy == "rumination":
            self.rumination = self._clamp(self.rumination + (0.28 * strength))
            self.expression_clamp = min(1.0, self.expression_clamp + (0.05 * strength))
            self.chemicals.cortisol = self._clamp(self.chemicals.cortisol + (0.18 * strength))
            self.chemicals.noradrenaline = self._clamp(
                self.chemicals.noradrenaline + (0.12 * strength)
            )
            self.chemicals.dopamine = self._clamp(self.chemicals.dopamine - (0.08 * strength))
        else:
            raise ValueError(f"Unsupported regulation strategy: {strategy}")

        self.chemicals.serotonin = min(self.chemicals.serotonin, self.get_serotonin_cap())
        self._refresh_baselines()
        logger.info(
            "K.A.I. regulation — strategy:%s trigger:%s clamp:%.2f pressure:%.2f rumination:%.2f",
            strategy,
            trigger,
            self.expression_clamp,
            self.suppression_pressure,
            self.rumination,
        )
        return {
            "strategy": strategy,
            "expression_clamp": self.expression_clamp,
            "suppression_pressure": self.suppression_pressure,
            "rumination": self.rumination,
            "allostatic_load": self.allostatic_load,
        }

    def _metabolize_chemicals(self, elapsed_seconds: float) -> None:
        """
        Chemicals slowly wash away over time, returning to the adaptive baseline.
        """
        decay_rate = max(0.0, 0.005 * elapsed_seconds)
        self._refresh_baselines()

        for chem in ["dopamine", "noradrenaline", "serotonin", "cortisol", "oxytocin"]:
            current = getattr(self.chemicals, chem)
            baseline = getattr(self.baselines, chem)
            adjusted_decay = decay_rate

            if chem == "oxytocin":
                adjusted_decay *= 0.25
            elif chem in {"cortisol", "noradrenaline"} and self.rumination > 0:
                adjusted_decay *= max(0.1, 1.0 - self.rumination)

            if current > baseline:
                new_val = max(baseline, current - adjusted_decay)
            else:
                new_val = min(baseline, current + adjusted_decay)

            if chem == "serotonin":
                new_val = min(new_val, self.get_serotonin_cap())

            setattr(self.chemicals, chem, new_val)

        if self.chemicals.cortisol < RESTFUL_CORTISOL_THRESHOLD:
            restorative = 0.002 * elapsed_seconds
            self.allostatic_load = max(0.0, self.allostatic_load - restorative)
            self.baseline_drift["dopamine"] = min(
                MAX_BASELINE_DRIFT,
                self.baseline_drift["dopamine"] + (restorative * 0.35),
            )
            self.baseline_drift["serotonin"] = min(
                MAX_BASELINE_DRIFT,
                self.baseline_drift["serotonin"] + (restorative * 0.45),
            )

        self.conflict = max(0.0, self.conflict - (elapsed_seconds * 0.02))
        self.suppression_pressure = max(0.0, self.suppression_pressure - (elapsed_seconds * 0.01))
        self.rumination = max(0.0, self.rumination - (elapsed_seconds * 0.005))
        self.expression_clamp = min(1.0, self.expression_clamp + (elapsed_seconds * 0.02))

        bond_decay = elapsed_seconds * 0.0005
        if bond_decay > 0:
            for user_id, strength in list(self.user_bonds.items()):
                new_strength = max(0.0, strength - bond_decay)
                if new_strength == 0.0:
                    self.user_bonds.pop(user_id, None)
                else:
                    self.user_bonds[user_id] = new_strength

        self._refresh_baselines()

    def _current_pad_vector(self) -> np.ndarray:
        c = self.chemicals
        bond = self.get_social_bond(self.active_user_id)

        pleasure = (c.dopamine * 1.4) - (c.cortisol * 1.45)
        pleasure += (c.oxytocin * 0.35) + (bond * 0.2)
        pleasure -= self.suppression_pressure * 0.08
        pleasure = max(-1.0, min(1.0, pleasure))

        arousal = c.noradrenaline * 0.75 + c.cortisol * 0.25
        arousal += (self.conflict * 0.2) + (self.rumination * 0.1)
        arousal = self._clamp(arousal)

        dominance = c.serotonin - (c.cortisol * 0.5) + (bond * 0.12)
        dominance -= self.conflict * 0.08
        dominance = self._clamp(dominance)

        expressed_pad = np.array([pleasure, arousal, dominance])
        baseline_pad = self._current_baseline_pad()
        return baseline_pad + ((expressed_pad - baseline_pad) * self.expression_clamp)

    def get_pad_coordinates(self) -> np.ndarray:
        """
        Converts the raw chemical soup into 3D PAD coordinates so the rest of K.A.I.'s
        brain can understand it.
        """
        now = time.time()
        self._metabolize_chemicals(now - self.last_update)
        self.last_update = now
        return self._current_pad_vector()

    def classify_emotion(self, pad: np.ndarray | None = None) -> str:
        if pad is None:
            pad = self.get_pad_coordinates()

        closest_label = "neutral"
        min_dist = float("inf")
        for label, centroid in self.PLUTCHIK_EMOTIONS.items():
            distance = float(np.linalg.norm(pad - centroid))
            if distance < min_dist:
                min_dist = distance
                closest_label = label
        return closest_label

    def emotion_snapshot(self, pad: np.ndarray | None = None) -> dict[str, float | str | list[float]]:
        if pad is None:
            pad = self.get_pad_coordinates()

        intensity = self.emotional_intensity(pad=pad)
        label = self.classify_emotion(pad=pad)
        return {
            "label": label,
            "pleasure": float(pad[0]),
            "arousal": float(pad[1]),
            "dominance": float(pad[2]),
            "pad": [float(v) for v in pad],
            "intensity": float(intensity),
            "conflict": float(self.conflict),
            "allostatic_load": float(self.allostatic_load),
            "bond_strength": float(self.get_social_bond(self.active_user_id)),
            "expression_clamp": float(self.expression_clamp),
        }

    def emotional_intensity(self, pad: np.ndarray | None = None) -> float:
        """
        Returns a 0.0–1.0 score indicating how far the current emotional state is from
        the adaptive resting baseline.
        """
        if pad is None:
            pad = self.get_pad_coordinates()

        baseline_pad = self._current_baseline_pad()
        distance = float(np.linalg.norm(pad - baseline_pad))
        return min(1.0, distance / 1.73)
