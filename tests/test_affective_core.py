import unittest

from affective_core import AffectiveEngine, PADState


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

