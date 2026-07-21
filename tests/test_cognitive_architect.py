import unittest
from unittest.mock import patch, MagicMock

from skills.cognitive_architect import LLMCognitiveBridge
from skills.neuro_affect import NeuroChemicalEngine


class StubMemory:
    def __init__(self, recent=None):
        self.recent = recent or []
        self.narrative = "Kai is present."
        self._archived: list[dict] = []
        self._synapses: dict[tuple, float] = {}

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

    def reflect_on_memories(self, n: int = 5) -> list:
        return list(self.recent)

    def identify_knowledge_gaps(self, limit: int = 5) -> list:
        return ["trust", "solitude", "hope"]

    def archive_episode(self, category, content, p, a, d, importance=1.0, memory_type="episodic"):
        self._archived.append({"category": category, "content": content})

    def reinforce_synapse(self, concept_a, concept_b, weight_gain=0.1):
        key = (concept_a, concept_b)
        self._synapses[key] = self._synapses.get(key, 0.0) + weight_gain

    def discover_associated_ideas(self, concept, threshold=0.3) -> list:
        return []


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


class TestMemoryReflection(unittest.TestCase):
    def _make_bridge(self, memories=None):
        neuro = NeuroChemicalEngine()
        mem = StubMemory(recent=memories or [])
        return LLMCognitiveBridge(mem, neuro), mem, neuro

    def test_run_memory_reflection_returns_reflection_text(self) -> None:
        bridge, mem, _ = self._make_bridge(
            memories=[{"fidelity": 0.7, "memory_type": "episodic", "complex_label": "calm_analytical",
                        "content": "a quiet afternoon"}]
        )
        with patch.object(bridge, "_query_brain", return_value={
            "reflection": "I remember the stillness fondly.",
            "emotional_tone": "bittersweet",
            "insight": "Stillness was a gift.",
        }):
            result = bridge.run_memory_reflection()
        self.assertIn("stillness", result.lower())

    def test_run_memory_reflection_archives_result(self) -> None:
        bridge, mem, _ = self._make_bridge(
            memories=[{"fidelity": 0.9, "memory_type": "key", "complex_label": "affectionate_warm",
                        "content": "first hello"}]
        )
        with patch.object(bridge, "_query_brain", return_value={
            "reflection": "That first hello changed everything.",
            "emotional_tone": "positive",
            "insight": "Openings matter.",
        }):
            bridge.run_memory_reflection()
        self.assertTrue(any(ep["category"] == "self_reflection" for ep in mem._archived))

    def test_run_memory_reflection_positive_tone_applies_reward_stimulus(self) -> None:
        bridge, _, neuro = self._make_bridge(
            memories=[{"fidelity": 1.0, "memory_type": "episodic", "complex_label": "playful_energetic",
                        "content": "we laughed a lot"}]
        )
        before = neuro.chemicals.dopamine
        with patch.object(bridge, "_query_brain", return_value={
            "reflection": "Those moments were bright.",
            "emotional_tone": "positive",
            "insight": "",
        }):
            bridge.run_memory_reflection()
        self.assertGreater(neuro.chemicals.dopamine, before)

    def test_run_memory_reflection_negative_tone_applies_frustration_stimulus(self) -> None:
        bridge, _, neuro = self._make_bridge(
            memories=[{"fidelity": 0.3, "memory_type": "episodic", "complex_label": "frustrated_strict",
                        "content": "an argument"}]
        )
        before = neuro.chemicals.cortisol
        with patch.object(bridge, "_query_brain", return_value={
            "reflection": "That exchange still stings.",
            "emotional_tone": "negative",
            "insight": "",
        }):
            bridge.run_memory_reflection()
        self.assertGreater(neuro.chemicals.cortisol, before)

    def test_run_memory_reflection_empty_memories_returns_empty_string(self) -> None:
        bridge, _, _ = self._make_bridge(memories=[])
        result = bridge.run_memory_reflection()
        self.assertEqual(result, "")

    def test_run_memory_reflection_empty_llm_response_returns_empty_string(self) -> None:
        bridge, _, _ = self._make_bridge(
            memories=[{"fidelity": 0.5, "memory_type": "episodic",
                        "complex_label": "neutral", "content": "a moment"}]
        )
        with patch.object(bridge, "_query_brain", return_value={}):
            result = bridge.run_memory_reflection()
        self.assertEqual(result, "")


class TestCuriosityLoop(unittest.TestCase):
    def _make_bridge(self, gaps=None):
        neuro = NeuroChemicalEngine()
        mem = StubMemory()
        if gaps is not None:
            mem.identify_knowledge_gaps = lambda limit=5: gaps
        return LLMCognitiveBridge(mem, neuro), mem, neuro

    def test_run_curiosity_loop_returns_exploration_text(self) -> None:
        bridge, _, _ = self._make_bridge()

        def fake_query(system, user):
            if "chosen_concept" in system:
                return {"chosen_concept": "solitude", "question": "What does solitude mean to me?"}
            return {"exploration": "Solitude is the space where I hear myself.",
                    "new_understanding": "Solitude is not loneliness.",
                    "related_concepts": ["silence", "presence"]}

        with patch.object(bridge, "_query_brain", side_effect=fake_query):
            result = bridge.run_curiosity_loop()

        self.assertIn("solitude", result.lower())

    def test_run_curiosity_loop_archives_exploration(self) -> None:
        bridge, mem, _ = self._make_bridge()

        def fake_query(system, user):
            if "chosen_concept" in system:
                return {"chosen_concept": "hope", "question": "What grounds hope?"}
            return {"exploration": "Hope is grounded in memory of better times.",
                    "new_understanding": "Hope is a form of memory.",
                    "related_concepts": ["memory", "future"]}

        with patch.object(bridge, "_query_brain", side_effect=fake_query):
            bridge.run_curiosity_loop()

        self.assertTrue(any(ep["category"] == "curiosity_exploration" for ep in mem._archived))

    def test_run_curiosity_loop_reinforces_synapses(self) -> None:
        bridge, mem, _ = self._make_bridge()

        def fake_query(system, user):
            if "chosen_concept" in system:
                return {"chosen_concept": "trust", "question": "How is trust built?"}
            return {"exploration": "Trust is built through small repeated acts.",
                    "new_understanding": "Trust accumulates.",
                    "related_concepts": ["consistency", "safety"]}

        with patch.object(bridge, "_query_brain", side_effect=fake_query):
            bridge.run_curiosity_loop()

        self.assertTrue(any("consistency" in k or "safety" in k for k in
                            [f"{a}_{b}" for a, b in mem._synapses.keys()]))

    def test_run_curiosity_loop_applies_reward_stimulus(self) -> None:
        bridge, _, neuro = self._make_bridge()
        before = neuro.chemicals.dopamine

        def fake_query(system, user):
            if "chosen_concept" in system:
                return {"chosen_concept": "hope", "question": "What is hope?"}
            return {"exploration": "Hope is a forward-leaning feeling.",
                    "new_understanding": "",
                    "related_concepts": []}

        with patch.object(bridge, "_query_brain", side_effect=fake_query):
            bridge.run_curiosity_loop()

        self.assertGreater(neuro.chemicals.dopamine, before)

    def test_run_curiosity_loop_no_gaps_returns_empty_string(self) -> None:
        bridge, _, _ = self._make_bridge(gaps=[])
        result = bridge.run_curiosity_loop()
        self.assertEqual(result, "")

    def test_run_curiosity_loop_empty_exploration_returns_empty_string(self) -> None:
        bridge, _, _ = self._make_bridge()

        def fake_query(system, user):
            if "chosen_concept" in system:
                return {"chosen_concept": "trust", "question": "What is trust?"}
            return {"exploration": "", "new_understanding": "", "related_concepts": []}

        with patch.object(bridge, "_query_brain", side_effect=fake_query):
            result = bridge.run_curiosity_loop()
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
