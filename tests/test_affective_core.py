import unittest

import numpy as np

from affective_core import AffectiveEngine, AudioEnvelopeWrapper, PADState


class TestAffectiveCore(unittest.TestCase):
    def test_pad_state_clamps_values(self) -> None:
        state = PADState(pleasure=2.0, arousal=-2.0, dominance=0.5)
        self.assertEqual(state.pleasure, 1.0)
        self.assertEqual(state.arousal, -1.0)
        self.assertEqual(state.dominance, 0.5)

    def test_engine_tick_moves_toward_target(self) -> None:
        engine = AffectiveEngine(initial_state=PADState(0.0, 0.0, 0.0), inertia=0.5)
        engine.apply_impulse(1.0, 0.0, 0.0)
        next_state = engine.tick()
        self.assertGreater(next_state.pleasure, 0.0)
        self.assertLess(next_state.pleasure, 1.0)


class TestAudioEnvelopeWrapper(unittest.TestCase):
    def _make_wrapper(self) -> AudioEnvelopeWrapper:
        engine = AffectiveEngine(initial_state=PADState(0.2, 0.1, 0.0), inertia=0.85)
        return AudioEnvelopeWrapper(engine)

    def test_generate_frame_warmup_duration_no_crash(self) -> None:
        """Regression: generate_frame(duration=0.1) must not raise ValueError.

        With the original unguarded attack/release constants (4410 and 6615 samples
        for a 44100 Hz frame), assigning envelope[-6615:] = linspace(1, 0, 6615)
        onto a 4410-element array raised:
            ValueError: could not broadcast input array from shape (6615,) into
                        shape (4410,)
        The fix clamps attack and release so their sum never exceeds total_samples.
        """
        wrapper = self._make_wrapper()
        frame = wrapper.generate_frame(duration=0.1)
        self.assertEqual(len(frame), int(44100 * 0.1))

    def test_generate_frame_very_short_duration_no_crash(self) -> None:
        """generate_frame with duration < attack constant must not raise."""
        wrapper = self._make_wrapper()
        # 0.05 s → total_samples=2205, well below the unclamped attack of 4410
        frame = wrapper.generate_frame(duration=0.05)
        self.assertEqual(len(frame), int(44100 * 0.05))

    def test_generate_frame_normal_duration_correct_length(self) -> None:
        wrapper = self._make_wrapper()
        frame = wrapper.generate_frame(duration=1.5)
        self.assertEqual(len(frame), int(44100 * 1.5))

    def test_generate_frame_returns_normalized_array(self) -> None:
        """Output should be in [-1.0, 1.0] (normalised unless all-zero)."""
        wrapper = self._make_wrapper()
        frame = wrapper.generate_frame(duration=0.5)
        self.assertIsInstance(frame, np.ndarray)
        self.assertLessEqual(float(np.max(np.abs(frame))), 1.0 + 1e-9)

