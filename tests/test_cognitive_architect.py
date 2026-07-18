import unittest
from unittest.mock import patch

from skills.cognitive_architect import LLMCognitiveBridge
from skills.neuro_affect import NeuroChemicalEngine


class StubMemory:
    def __init__(self, recent=None):
        self.recent = recent or []
        self.narrative = "Kai is present."

    def synthesize_personality_context(self) -> str:
        return "Kai remembers enough to form intentions."

    def search_by_resonance(self, *args, **kwargs):
        return list(self.recent)

    def summarize_human_state(self, user_id=None) -> str:
        return "Kai feels a pull toward connection."

    def reflect_on_state(self, emotion_snapshot, desires=None, intentions=None, user_id=None) -> str:
        self.narrative = "Kai feels reflective."
        return self.narrative

    def get_self_narrative(self) -> str:
        return self.narrative


class TestLLMCognitiveBridge(unittest.TestCase):
    def test_synchronize_attachment_feeds_predicted_mood_into_neuro(self) -> None:
        neuro = NeuroChemicalEngine()
        bridge = LLMCognitiveBridge(StubMemory(), neuro)
        before_dopamine = neuro.chemicals.dopamine

        with patch.object(
            bridge,
            "_query_brain",
            return_value={
                "predicted_mood": "joyful and excited",
                "recommended_tone": "warm",
                "tempo_multiplier": 1.1,
            },
        ):
            bridge.synchronize_attachment({"user_id": "friend-1", "events": ["smile"]})

        self.assertGreater(neuro.chemicals.dopamine, before_dopamine)
        self.assertGreater(neuro.get_social_bond("friend-1"), 0.0)

    def test_deliberate_primes_negative_memories_back_into_neuro(self) -> None:
        neuro = NeuroChemicalEngine()
        neuro.apply_stimulus(threat=0.7, frustration=0.2)
        bridge = LLMCognitiveBridge(
            StubMemory(
                recent=[
                    {
                        "complex_label": "concerned_alert",
                        "content": "a warped memory of a worrying exchange",
                        "distortion_score": 0.8,
                    }
                ]
            ),
            neuro,
        )
        before_cortisol = neuro.chemicals.cortisol

        with patch.object(bridge, "_query_brain", return_value={"desires": [], "intentions": []}), patch.object(
            bridge,
            "_select_regulation_strategy",
            return_value=None,
        ):
            bridge.deliberate()

        self.assertGreater(neuro.chemicals.cortisol, before_cortisol)

    def test_refresh_self_narrative_updates_bridge_cache(self) -> None:
        neuro = NeuroChemicalEngine()
        bridge = LLMCognitiveBridge(StubMemory(), neuro)
        narrative = bridge.refresh_self_narrative("friend-1")
        self.assertEqual(narrative, "Kai feels reflective.")
        self.assertEqual(bridge.last_narrative, "Kai feels reflective.")


if __name__ == "__main__":
    unittest.main()
