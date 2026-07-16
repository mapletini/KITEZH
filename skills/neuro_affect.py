"""
skills/neuro_affect.py — Advanced neuro-chemical emotional simulation for K.A.I.
"""

import time
import logging
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Neurotransmitters:
    # Levels range from 0.0 to 1.0
    dopamine: float = 0.5      # Drives Pleasure (Reward/Success)
    noradrenaline: float = 0.2 # Drives Arousal (Alertness/Urgency)
    serotonin: float = 0.8     # Drives Dominance (Confidence/Control)
    cortisol: float = 0.1      # Drives Stress (Reduces Pleasure, spikes Arousal)

class NeuroChemicalEngine:
    def __init__(self):
        self.chemicals = Neurotransmitters()
        self.last_update = time.time()
        
        # The natural, calm baseline K.A.I. always tries to return to!
        self.baselines = Neurotransmitters(
            dopamine=0.5,
            noradrenaline=0.2,
            serotonin=0.8,
            cortisol=0.1
        )

    def apply_stimulus(
        self,
        reward: float = 0.0,
        threat: float = 0.0,
        success: float = 0.0,
        uncertainty: float = 0.0,
        frustration: float = 0.0,
        recovery: float = 0.0,
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
        """
        if reward > 0:
            self.chemicals.dopamine = min(1.0, self.chemicals.dopamine + reward)
            self.chemicals.cortisol = max(0.0, self.chemicals.cortisol - (reward / 2))

        if threat > 0:
            self.chemicals.cortisol = min(1.0, self.chemicals.cortisol + threat)
            self.chemicals.noradrenaline = min(1.0, self.chemicals.noradrenaline + (threat * 1.5))

        if success > 0:
            self.chemicals.serotonin = min(1.0, self.chemicals.serotonin + success)
            self.chemicals.dopamine = min(1.0, self.chemicals.dopamine + (success / 2))

        if uncertainty > 0:
            # Uncertainty erodes confidence and raises alertness
            self.chemicals.serotonin = max(0.0, self.chemicals.serotonin - (uncertainty * 0.8))
            self.chemicals.noradrenaline = min(1.0, self.chemicals.noradrenaline + (uncertainty * 0.6))

        if frustration > 0:
            # Frustration: rising stress and fading reward sense
            self.chemicals.cortisol = min(1.0, self.chemicals.cortisol + (frustration * 0.9))
            self.chemicals.dopamine = max(0.0, self.chemicals.dopamine - (frustration * 0.5))

        if recovery > 0:
            # Recovery: cortisol and noradrenaline settle back toward calm
            self.chemicals.cortisol = max(0.0, self.chemicals.cortisol - (recovery * 0.8))
            self.chemicals.noradrenaline = max(0.0, self.chemicals.noradrenaline - (recovery * 0.6))
            self.chemicals.serotonin = min(1.0, self.chemicals.serotonin + (recovery * 0.3))

        logger.info(
            "K.A.I. stimulus — reward:%.2f threat:%.2f success:%.2f "
            "uncertainty:%.2f frustration:%.2f recovery:%.2f",
            reward, threat, success, uncertainty, frustration, recovery,
        )

    def _metabolize_chemicals(self, elapsed_seconds: float) -> None:
        """
        Chemicals slowly wash away over time, returning to the safe baseline.
        """
        decay_rate = 0.005 * elapsed_seconds 
        
        for chem in ["dopamine", "noradrenaline", "serotonin", "cortisol"]:
            current = getattr(self.chemicals, chem)
            baseline = getattr(self.baselines, chem)
            
            # Smoothly drift toward the baseline
            if current > baseline:
                new_val = max(baseline, current - decay_rate)
            else:
                new_val = min(baseline, current + decay_rate)
                
            setattr(self.chemicals, chem, new_val)

    def get_pad_coordinates(self) -> np.ndarray:
        """
        Converts the raw chemical soup into 3D PAD (Pleasure, Arousal, Dominance) 
        coordinates so the rest of K.A.I.'s brain can understand it.
        """
        now = time.time()
        self._metabolize_chemicals(now - self.last_update)
        self.last_update = now

        c = self.chemicals
        
        # Pleasure (-1 to 1): High dopamine makes it positive, high cortisol drags it negative.
        pleasure = (c.dopamine * 1.5) - (c.cortisol * 1.5)
        pleasure = max(-1.0, min(1.0, pleasure))
        
        # Arousal (0 to 1): Driven heavily by noradrenaline, with a tiny bump from stress.
        arousal = c.noradrenaline * 0.8 + c.cortisol * 0.2
        arousal = max(0.0, min(1.0, arousal))
        
        # Dominance (0 to 1): Driven by serotonin (confidence) vs cortisol (fear/helplessness).
        dominance = c.serotonin - (c.cortisol * 0.5)
        dominance = max(0.0, min(1.0, dominance))

        return np.array([pleasure, arousal, dominance])

    def emotional_intensity(self, pad: np.ndarray | None = None) -> float:
        """
        Returns a 0.0–1.0 score indicating how far the current emotional state is from
        the calm resting baseline.

        A high score means K.A.I. is experiencing a strong emotion; events at this
        intensity are candidates for flashbulb (key) memory formation.

        Parameters
        ----------
        pad : optional pre-computed PAD array.  If None, ``get_pad_coordinates()`` is
              called to obtain the current state, which also advances chemical metabolism.
        """
        if pad is None:
            pad = self.get_pad_coordinates()

        # Compute baseline PAD from the resting chemical levels
        b = self.baselines
        baseline_pleasure   = max(-1.0, min(1.0, (b.dopamine * 1.5) - (b.cortisol * 1.5)))
        baseline_arousal    = max(0.0, min(1.0, b.noradrenaline * 0.8 + b.cortisol * 0.2))
        baseline_dominance  = max(0.0, min(1.0, b.serotonin - (b.cortisol * 0.5)))
        baseline_pad = np.array([baseline_pleasure, baseline_arousal, baseline_dominance])

        distance = float(np.linalg.norm(pad - baseline_pad))
        # Max possible Euclidean distance in the PAD cube ≈ sqrt(3) * 2 ≈ 3.46;
        # normalise against sqrt(3) ≈ 1.73 to keep the scale intuitive (0–1 for typical swings)
        return min(1.0, distance / 1.73)

