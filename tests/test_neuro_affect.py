"""
tests/test_neuro_affect.py — Unit tests for the upgraded NeuroChemicalEngine.

Covers:
* apply_stimulus: all types (reward, threat, success, uncertainty, frustration, recovery)
* adaptive baselines, allostatic load, conflict, oxytocin, and regulation
* get_pad_coordinates bounds checking
* emotional_intensity at baseline and after high stimuli
* _metabolize_chemicals returns to baseline
* emotional_intensity accepts a pre-computed pad argument
"""

import time
import unittest

import numpy as np

from skills.neuro_affect import NeuroChemicalEngine, Neurotransmitters


class TestNeuroChemicalEngine(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = NeuroChemicalEngine()

    # ------------------------------------------------------------------
    # apply_stimulus — original types
    # ------------------------------------------------------------------

    def test_reward_raises_dopamine(self) -> None:
        before = self.engine.chemicals.dopamine
        self.engine.apply_stimulus(reward=0.3)
        self.assertGreater(self.engine.chemicals.dopamine, before)

    def test_reward_lowers_cortisol(self) -> None:
        self.engine.chemicals.cortisol = 0.5
        self.engine.apply_stimulus(reward=0.3)
        self.assertLess(self.engine.chemicals.cortisol, 0.5)

    def test_threat_raises_cortisol(self) -> None:
        before = self.engine.chemicals.cortisol
        self.engine.apply_stimulus(threat=0.3)
        self.assertGreater(self.engine.chemicals.cortisol, before)

    def test_threat_raises_noradrenaline(self) -> None:
        before = self.engine.chemicals.noradrenaline
        self.engine.apply_stimulus(threat=0.3)
        self.assertGreater(self.engine.chemicals.noradrenaline, before)

    def test_success_raises_serotonin(self) -> None:
        before = self.engine.chemicals.serotonin
        self.engine.apply_stimulus(success=0.2)
        self.assertGreater(self.engine.chemicals.serotonin, before)

    def test_success_raises_dopamine(self) -> None:
        before = self.engine.chemicals.dopamine
        self.engine.apply_stimulus(success=0.2)
        self.assertGreater(self.engine.chemicals.dopamine, before)

    # ------------------------------------------------------------------
    # apply_stimulus — new types
    # ------------------------------------------------------------------

    def test_uncertainty_lowers_serotonin(self) -> None:
        before = self.engine.chemicals.serotonin
        self.engine.apply_stimulus(uncertainty=0.3)
        self.assertLess(self.engine.chemicals.serotonin, before)

    def test_uncertainty_raises_noradrenaline(self) -> None:
        before = self.engine.chemicals.noradrenaline
        self.engine.apply_stimulus(uncertainty=0.3)
        self.assertGreater(self.engine.chemicals.noradrenaline, before)

    def test_frustration_raises_cortisol(self) -> None:
        before = self.engine.chemicals.cortisol
        self.engine.apply_stimulus(frustration=0.3)
        self.assertGreater(self.engine.chemicals.cortisol, before)

    def test_frustration_lowers_dopamine(self) -> None:
        self.engine.chemicals.dopamine = 0.8
        self.engine.apply_stimulus(frustration=0.3)
        self.assertLess(self.engine.chemicals.dopamine, 0.8)

    def test_recovery_lowers_cortisol(self) -> None:
        self.engine.chemicals.cortisol = 0.7
        self.engine.apply_stimulus(recovery=0.3)
        self.assertLess(self.engine.chemicals.cortisol, 0.7)

    def test_recovery_lowers_noradrenaline(self) -> None:
        self.engine.chemicals.noradrenaline = 0.8
        self.engine.apply_stimulus(recovery=0.3)
        self.assertLess(self.engine.chemicals.noradrenaline, 0.8)

    def test_recovery_raises_serotonin(self) -> None:
        self.engine.chemicals.serotonin = 0.4
        self.engine.apply_stimulus(recovery=0.3)
        self.assertGreater(self.engine.chemicals.serotonin, 0.4)

    # ------------------------------------------------------------------
    # Adaptive mood complexity
    # ------------------------------------------------------------------

    def test_sustained_stress_drifts_dopamine_and_serotonin_baselines_down(self) -> None:
        initial_dopamine = self.engine.baselines.dopamine
        initial_serotonin = self.engine.baselines.serotonin
        for _ in range(6):
            self.engine.apply_stimulus(threat=0.7)
        self.assertLess(self.engine.baselines.dopamine, initial_dopamine)
        self.assertLess(self.engine.baselines.serotonin, initial_serotonin)
        self.assertGreater(self.engine.allostatic_load, 0.0)

    def test_positive_interactions_restore_drifted_baselines(self) -> None:
        for _ in range(6):
            self.engine.apply_stimulus(threat=0.7)
        stressed_baseline = self.engine.baselines.dopamine
        self.engine.chemicals.cortisol = 0.2
        for _ in range(6):
            self.engine.apply_stimulus(reward=0.4, success=0.2, recovery=0.3, user_id="friend")
        self.assertGreater(self.engine.baselines.dopamine, stressed_baseline)

    def test_allostatic_load_caps_serotonin_recovery(self) -> None:
        for _ in range(8):
            self.engine.apply_stimulus(threat=0.8, frustration=0.3)
        self.engine.chemicals.serotonin = 0.2
        self.engine.apply_stimulus(success=1.0, recovery=1.0)
        self.assertLessEqual(self.engine.chemicals.serotonin, self.engine.get_serotonin_cap())
        self.assertLess(self.engine.get_serotonin_cap(), 1.0)

    def test_conflict_state_rises_with_opposing_stimuli(self) -> None:
        before_cortisol = self.engine.chemicals.cortisol
        before_noradrenaline = self.engine.chemicals.noradrenaline
        self.engine.apply_stimulus(reward=0.5, threat=0.5)
        self.assertGreater(self.engine.conflict, 0.0)
        self.assertGreater(self.engine.chemicals.cortisol, before_cortisol)
        self.assertGreater(self.engine.chemicals.noradrenaline, before_noradrenaline)

    def test_positive_user_interaction_builds_oxytocin_bond(self) -> None:
        before = self.engine.chemicals.oxytocin
        self.engine.apply_stimulus(reward=0.4, success=0.3, user_id="friend")
        self.assertGreater(self.engine.chemicals.oxytocin, before)
        self.assertGreater(self.engine.get_social_bond("friend"), 0.0)

    def test_known_user_threat_is_softened_by_bond(self) -> None:
        bonded = NeuroChemicalEngine()
        stranger = NeuroChemicalEngine()
        for _ in range(5):
            bonded.apply_stimulus(reward=0.5, success=0.2, user_id="friend")
        bonded_start = bonded.chemicals.cortisol
        stranger_start = stranger.chemicals.cortisol
        bonded.apply_stimulus(threat=0.4, user_id="friend")
        stranger.apply_stimulus(threat=0.4, user_id="stranger")
        self.assertLess(
            bonded.chemicals.cortisol - bonded_start,
            stranger.chemicals.cortisol - stranger_start,
        )

    def test_regulate_suppression_clamps_expression(self) -> None:
        self.engine.regulate("suppression")
        self.assertLess(self.engine.expression_clamp, 1.0)
        self.assertGreater(self.engine.suppression_pressure, 0.0)

    def test_regulate_rumination_raises_cortisol(self) -> None:
        before = self.engine.chemicals.cortisol
        self.engine.regulate("rumination")
        self.assertGreater(self.engine.chemicals.cortisol, before)

    def test_classify_emotion_returns_plutchik_label(self) -> None:
        label = self.engine.classify_emotion(np.array([0.9, 0.6, 0.7]))
        self.assertIn(label, self.engine.PLUTCHIK_EMOTIONS)

    def test_emotion_snapshot_includes_new_state_fields(self) -> None:
        snapshot = self.engine.emotion_snapshot()
        for key in ("label", "conflict", "allostatic_load", "bond_strength", "expression_clamp"):
            self.assertIn(key, snapshot)

    # ------------------------------------------------------------------
    # Clamping: chemicals never exceed [0, 1]
    # ------------------------------------------------------------------

    def test_chemicals_never_exceed_one(self) -> None:
        for _ in range(10):
            self.engine.apply_stimulus(
                reward=1.0,
                threat=1.0,
                success=1.0,
                uncertainty=1.0,
                frustration=1.0,
                recovery=1.0,
            )
        for chem in ["dopamine", "noradrenaline", "serotonin", "cortisol", "oxytocin"]:
            val = getattr(self.engine.chemicals, chem)
            self.assertLessEqual(val, 1.0, f"{chem} exceeded 1.0")

    def test_chemicals_never_below_zero(self) -> None:
        for _ in range(10):
            self.engine.apply_stimulus(threat=1.0, frustration=1.0, recovery=1.0)
        for chem in ["dopamine", "noradrenaline", "serotonin", "cortisol", "oxytocin"]:
            val = getattr(self.engine.chemicals, chem)
            self.assertGreaterEqual(val, 0.0, f"{chem} went below 0.0")

    # ------------------------------------------------------------------
    # get_pad_coordinates
    # ------------------------------------------------------------------

    def test_pad_pleasure_in_range(self) -> None:
        pad = self.engine.get_pad_coordinates()
        self.assertGreaterEqual(pad[0], -1.0)
        self.assertLessEqual(pad[0], 1.0)

    def test_pad_arousal_in_range(self) -> None:
        pad = self.engine.get_pad_coordinates()
        self.assertGreaterEqual(pad[1], 0.0)
        self.assertLessEqual(pad[1], 1.0)

    def test_pad_dominance_in_range(self) -> None:
        pad = self.engine.get_pad_coordinates()
        self.assertGreaterEqual(pad[2], 0.0)
        self.assertLessEqual(pad[2], 1.0)

    def test_pad_returns_numpy_array(self) -> None:
        pad = self.engine.get_pad_coordinates()
        self.assertIsInstance(pad, np.ndarray)
        self.assertEqual(pad.shape, (3,))

    # ------------------------------------------------------------------
    # emotional_intensity
    # ------------------------------------------------------------------

    def test_emotional_intensity_at_baseline_is_low(self) -> None:
        intensity = self.engine.emotional_intensity()
        self.assertGreaterEqual(intensity, 0.0)
        self.assertLessEqual(intensity, 0.3)

    def test_emotional_intensity_high_after_threat(self) -> None:
        self.engine.apply_stimulus(threat=0.8)
        intensity = self.engine.emotional_intensity()
        self.assertGreater(intensity, 0.1)

    def test_emotional_intensity_in_range(self) -> None:
        self.engine.apply_stimulus(threat=1.0, frustration=1.0)
        intensity = self.engine.emotional_intensity()
        self.assertGreaterEqual(intensity, 0.0)
        self.assertLessEqual(intensity, 1.0)

    def test_emotional_intensity_accepts_precomputed_pad(self) -> None:
        pad = self.engine.get_pad_coordinates()
        intensity_from_method = self.engine.emotional_intensity()
        intensity_from_pad = self.engine.emotional_intensity(pad=pad)
        self.assertAlmostEqual(intensity_from_method, intensity_from_pad, places=3)

    # ------------------------------------------------------------------
    # Metabolize / homeostasis
    # ------------------------------------------------------------------

    def test_metabolize_moves_toward_baseline(self) -> None:
        self.engine.chemicals.dopamine = 1.0
        self.engine.chemicals.cortisol = 0.9
        self.engine._metabolize_chemicals(60.0)
        self.assertLess(self.engine.chemicals.dopamine, 1.0)
        self.assertLess(self.engine.chemicals.cortisol, 0.9)

    def test_metabolize_does_not_overshoot_baseline(self) -> None:
        self.engine.chemicals.dopamine = 0.0
        self.engine._metabolize_chemicals(10.0)
        self.assertLessEqual(self.engine.chemicals.dopamine, self.engine.baselines.dopamine)


if __name__ == "__main__":
    unittest.main()
