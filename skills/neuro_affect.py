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

    def apply_stimulus(self, reward: float = 0.0, threat: float = 0.0, success: float = 0.0):
        """
        When a nì [thing] happens to K.A.I., it triggers a chemical release!
        """
        # A good obair [work] creates a dopamine spike!
        if reward > 0:
            self.chemicals.dopamine = min(1.0, self.chemicals.dopamine + reward)
            self.chemicals.cortisol = max(0.0, self.chemicals.cortisol - (reward / 2))
            
        # A threat or rule violation spikes cortisol and noradrenaline!
        if threat > 0:
            self.chemicals.cortisol = min(1.0, self.chemicals.cortisol + threat)
            self.chemicals.noradrenaline = min(1.0, self.chemicals.noradrenaline + (threat * 1.5))
            
        # Successfully completing a task boosts serotonin (confidence)!
        if success > 0:
            self.chemicals.serotonin = min(1.0, self.chemicals.serotonin + success)
            self.chemicals.dopamine = min(1.0, self.chemicals.dopamine + (success / 2))

        logger.info(f"K.A.I.'s eanchainn [brain] received stimulus! Reward:{reward}, Threat:{threat}")

    def _metabolize_chemicals(self, elapsed_seconds: float):
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
        coordinates so the rest of K.A.I.'s brain can understand it!
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
