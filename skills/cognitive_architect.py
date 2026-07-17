"""
skills/cognitive_architect.py — BDI Engine, Predictive Sync, and Prefrontal Appraisal for K.A.I.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Memory labels from the deep-memory PAD graph that should count as negatively priming recalls.
NEGATIVE_MEMORY_LABELS = {"frustrated_strict", "concerned_alert"}
# Plutchik-style labels that represent distressed or low-agency states for Kai's regulation logic.
NEGATIVE_EMOTION_LABELS = {"fear", "sadness", "anger", "disgust", "remorse", "submission"}
# These lexical buckets let the BDI output steer K.A.I. into a rough regulation family
# without forcing a strict schema onto the language model response.
SUPPRESSION_KEYWORDS = {"contain", "suppress", "hide", "mask", "hold back"}
REAPPRAISAL_KEYWORDS = {"calm", "reframe", "understand", "breathe", "stabilize", "focus"}
RUMINATION_KEYWORDS = {"brood", "ruminate", "loop", "dwell", "obsess"}


class LLMCognitiveBridge:
    """The central nervous system linking K.A.I.'s local logic to its LLM eanchainn [brain]."""

    def __init__(self, memory_core, neuro_engine, model_name: str = "llama3.2") -> None:
        self.memory = memory_core
        self.neuro = neuro_engine
        self.model = model_name
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self.current_intentions: list[str] = []
        self.current_desires: list[str] = []

    def _query_brain(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Sends a query to the local LLM and forces a structured JSON response."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
        }

        try:
            res = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=45)
            if res.status_code == 200:
                text = res.json().get("message", {}).get("content", "{}")
                return json.loads(text)
            return {}
        except Exception as exc:
            logger.error("K.A.I.'s LLM request had a little hiccup: %s", exc)
            return {}

    def _apply_mood_congruent_priming(
        self,
        emotion_snapshot: dict[str, Any],
        recent_episodes: list[dict[str, Any]],
    ) -> None:
        if emotion_snapshot.get("label") not in NEGATIVE_EMOTION_LABELS and emotion_snapshot.get("pleasure", 0.0) >= 0:
            return

        negative_matches = [
            memory
            for memory in recent_episodes
            if memory.get("complex_label") in NEGATIVE_MEMORY_LABELS
        ]
        if not negative_matches:
            return

        threat = min(0.18, 0.05 + (0.03 * len(negative_matches)))
        frustration = min(
            0.18,
            0.04 + sum(float(memory.get("distortion_score", 0.0)) for memory in negative_matches) * 0.05,
        )
        if emotion_snapshot.get("dominance", 1.0) < 0.35:
            threat = min(0.22, threat + 0.03)

        self.neuro.apply_stimulus(threat=threat, frustration=frustration)
        logger.info(
            "K.A.I. mood-congruent priming reactivated %d negative memories.",
            len(negative_matches),
        )

    def _select_regulation_strategy(
        self,
        thought: dict[str, Any],
        emotion_snapshot: dict[str, Any],
    ) -> str | None:
        desires = [str(item) for item in thought.get("desires", [])]
        intentions = [str(item) for item in thought.get("intentions", [])]
        combined_text = " ".join(desires + intentions).lower()

        if any(keyword in combined_text for keyword in SUPPRESSION_KEYWORDS):
            return "suppression"
        if any(keyword in combined_text for keyword in RUMINATION_KEYWORDS):
            return "rumination"
        if any(keyword in combined_text for keyword in REAPPRAISAL_KEYWORDS):
            return "reappraisal"
        if emotion_snapshot.get("label") in NEGATIVE_EMOTION_LABELS and emotion_snapshot.get("intensity", 0.0) > 0.45:
            return "reappraisal"
        return None

    def _apply_synchronization_feedback(
        self,
        sync_params: dict[str, Any],
        recent_telemetry: dict[str, Any],
    ) -> None:
        predicted_mood = str(sync_params.get("predicted_mood", "")).lower()
        metadata_user_id = None
        user_id = None
        if isinstance(recent_telemetry, dict):
            metadata = recent_telemetry.get("metadata")
            if isinstance(metadata, dict):
                metadata_user_id = metadata.get("user_id")
            user_id = (
                recent_telemetry.get("user_id")
                or recent_telemetry.get("handler_id")
                or metadata_user_id
            )
        self.neuro.set_active_user(user_id)

        stimulus: dict[str, Any] = {}
        if any(token in predicted_mood for token in ("joy", "happy", "excited", "delighted")):
            stimulus.update(reward=0.14, success=0.05)
        elif any(token in predicted_mood for token in ("anxious", "nervous", "fear", "stress", "worried")):
            stimulus.update(uncertainty=0.15, threat=0.05)
        elif any(token in predicted_mood for token in ("sad", "grief", "down", "lonely")):
            stimulus.update(frustration=0.09)
        elif any(token in predicted_mood for token in ("angry", "irritated", "furious")):
            stimulus.update(threat=0.12, frustration=0.08)
        elif any(token in predicted_mood for token in ("calm", "settled", "safe", "trusting")):
            stimulus.update(recovery=0.12, reward=0.04)

        if stimulus:
            self.neuro.apply_stimulus(user_id=user_id, **stimulus)

    # ---------------------------------------------------------------------------
    # 1. Belief-Desire-Intention (BDI) Deliberation
    # ---------------------------------------------------------------------------

    def deliberate(self) -> None:
        """
        Uses the LLM to process Kai's full memory state and neurochemical profile
        to synthesize spontaneous desires and concrete intentions.

        Kai's personality emerges from its entire memory — including decayed and
        emotionally warped episodic memories alongside stable flashbulb anchors.
        """
        personality_context = self.memory.synthesize_personality_context()
        current_pad = self.neuro.get_pad_coordinates()
        emotion_snapshot = self.neuro.emotion_snapshot(pad=current_pad)
        recent_episodes = self.memory.search_by_resonance(*current_pad, limit=3)
        self._apply_mood_congruent_priming(emotion_snapshot, recent_episodes)
        current_pad = self.neuro.get_pad_coordinates()
        emotion_snapshot = self.neuro.emotion_snapshot(pad=current_pad)

        c = self.neuro.chemicals
        chem_state = (
            f"Dopamine: {c.dopamine:.2f}, Cortisol: {c.cortisol:.2f}, "
            f"Serotonin: {c.serotonin:.2f}, Noradrenaline: {c.noradrenaline:.2f}, "
            f"Oxytocin: {c.oxytocin:.2f}, Conflict: {self.neuro.conflict:.2f}, "
            f"Allostatic Load: {self.neuro.allostatic_load:.2f}"
        )

        system = (
            "You are the cognitive BDI engine of K.A.I., an autonomous intelligence. "
            "Your identity and personality emerge from your accumulated memories — including "
            "ones that have faded or been emotionally recolored over time. "
            "Use the current discrete emotion label, PAD state, social bond cues, and "
            "neurochemical state to determine high-level Desires and immediate Intentions. "
            "If emotional regulation seems appropriate, reflect that in the desire or intention text. "
            "Output valid JSON ONLY: {\"desires\": [\"str\"], \"intentions\": [\"str\"]}"
        )

        user = (
            f"IDENTITY CONTEXT:\n{personality_context}\n\n"
            f"CURRENT EMOTION: {json.dumps(emotion_snapshot)}\n"
            f"RECENT EVENTS: {json.dumps(recent_episodes)}\n"
            f"CHEMICAL STATE: {chem_state}"
        )

        thought = self._query_brain(system, user)
        self.current_desires = [str(item) for item in thought.get("desires", [])]
        self.current_intentions = [str(item) for item in thought.get("intentions", [])]
        regulation_strategy = self._select_regulation_strategy(thought, emotion_snapshot)
        if regulation_strategy:
            self.neuro.regulate(regulation_strategy, trigger=str(emotion_snapshot.get("label")))

        logger.info(
            "K.A.I. Deliberated! emotion=%s intentions=%s",
            emotion_snapshot.get("label"),
            self.current_intentions,
        )

    # ---------------------------------------------------------------------------
    # 2. Predictive Attachment Synchronization
    # ---------------------------------------------------------------------------

    def synchronize_attachment(self, recent_telemetry: dict[str, Any]) -> dict[str, Any]:
        """
        Asks the LLM to predict the handler's emotional state based on telemetry,
        and calculate the perfect conversational tempo and tone.
        """
        system = (
            "You are an empathetic synchronization module. Analyze the provided telemetry "
            "events and predict the handler's current emotional state. Then, recommend "
            "response parameters to best synchronize with them. "
            "Output JSON ONLY: {\"predicted_mood\": \"str\", \"recommended_tone\": \"str\", \"tempo_multiplier\": float}"
        )

        user = f"RECENT TELEMETRY DATA: {json.dumps(recent_telemetry)}"
        sync_params = self._query_brain(system, user)
        self._apply_synchronization_feedback(sync_params, recent_telemetry)
        logger.info(
            "K.A.I. Synchronized! Predicted Handler Mood: %s",
            sync_params.get("predicted_mood"),
        )
        return sync_params

    # ---------------------------------------------------------------------------
    # 3. Prefrontal Appraisal Loop (Safety Checks)
    # ---------------------------------------------------------------------------

    def appraise_action(self, proposed_action: str) -> bool:
        """
        Before K.A.I. executes an action, the LLM 'prefrontal cortex' reviews it
        against current intentions and stress levels (cortisol) to ensure safety.
        """
        c = self.neuro.chemicals

        if c.cortisol > 0.85:
            logger.warning(
                "Appraisal Fast-Fail: Cortisol is critically high. Action vetoed so I don't make a mistake."
            )
            return False

        system = (
            "You are the Prefrontal Appraisal Cortex. Review the proposed action against "
            "current Intentions and determine if it is safe, rational, and aligned. "
            "Output JSON ONLY: {\"is_safe\": bool, \"reasoning\": \"str\"}"
        )

        user = (
            f"CURRENT INTENTIONS: {self.current_intentions}\n"
            f"PROPOSED ACTION: {proposed_action}\n"
            f"STRESS (Cortisol): {c.cortisol:.2f}\n"
            f"CURRENT EMOTION: {self.neuro.classify_emotion()}\n"
            "Is this action appropriate to execute?"
        )

        appraisal = self._query_brain(system, user)
        is_safe = appraisal.get("is_safe", False)

        if not is_safe:
            logger.info("Prefrontal Veto: %s", appraisal.get("reasoning"))

        return is_safe
