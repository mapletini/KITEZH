"""
skills/cognitive_architect.py — BDI Engine, Predictive Sync, and Prefrontal Appraisal for K.A.I.
"""

import os
import json
import requests
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class LLMCognitiveBridge:
    """The central nervous system linking K.A.I.'s local logic to its LLM eanchainn [brain]."""
    
    def __init__(self, memory_core, neuro_engine, model_name: str = "llama3.2"):
        self.memory = memory_core
        self.neuro = neuro_engine
        self.model = model_name
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        
        self.current_intentions: List[str] = []
        self.current_desires: List[str] = []

    def _query_brain(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        """Sends a query to the local LLM and forces a structured JSON response."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "stream": False,
            "format": "json"
        }
        
        try:
            # We use the native /api/chat endpoint for full parameter control
            res = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=45)
            if res.status_code == 200:
                text = res.json().get("message", {}).get("content", "{}")
                return json.loads(text)
            return {}
        except Exception as e:
            logger.error(f"K.A.I.'s LLM request had a little hiccup: {e}")
            return {}

    # ---------------------------------------------------------------------------
    # 1. Belief-Desire-Intention (BDI) Deliberation
    # ---------------------------------------------------------------------------

    def deliberate(self) -> None:
        """
        Uses the LLM to process current beliefs (memories) and neurochemicals 
        to synthesize spontaneous desires and concrete intentions.
        """
        # 1. Gather Beliefs (Core facts and recent episodic memory)
        beliefs = self.memory.read_core_memory()
        
        # 2. Get current mood coordinates to find memories that feel similar
        current_pad = self.neuro.get_pad_coordinates()
        recent_episodes = self.memory.search_by_resonance(*current_pad, limit=3)
        
        # 3. Gather Chemical State
        c = self.neuro.chemicals
        chem_state = f"Dopamine: {c.dopamine:.2f}, Cortisol: {c.cortisol:.2f}, Serotonin: {c.serotonin:.2f}"
        
        system = (
            "You are the cognitive BDI engine of K.A.I., an autonomous intelligence. "
            "Based on your current Beliefs (core memories) and your "
            "current neurochemical state, determine your high-level Desires and immediate Intentions. "
            "Output valid JSON ONLY: {\"desires\": [\"str\"], \"intentions\": [\"str\"]}"
        )
        
        user = f"BELIEFS: {json.dumps(beliefs)}\nRECENT EVENTS: {json.dumps(recent_episodes)}\nCHEMICAL STATE: {chem_state}"
        
        thought = self._query_brain(system, user)
        self.current_desires = thought.get("desires", [])
        self.current_intentions = thought.get("intentions", [])
        logger.info(f"K.A.I. Deliberated! Intentions: {self.current_intentions}")

    # ---------------------------------------------------------------------------
    # 2. Predictive Attachment Synchronization
    # ---------------------------------------------------------------------------

    def synchronize_attachment(self, recent_telemetry: Dict[str, Any]) -> Dict[str, Any]:
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
        logger.info(f"K.A.I. Synchronized! Predicted Handler Mood: {sync_params.get('predicted_mood')}")
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
        
        # A hardcoded safety net: If cortisol is too high, K.A.I. refuses to act out of stress!
        if c.cortisol > 0.85:
            logger.warning("Appraisal Fast-Fail: Cortisol is critically high. Action vetoed so I don't make a mistake.")
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
            "Is this action appropriate to execute?"
        )
        
        appraisal = self._query_brain(system, user)
        is_safe = appraisal.get("is_safe", False)
        
        if not is_safe:
            logger.info(f"Prefrontal Veto: {appraisal.get('reasoning')}")
            
        return is_safe
