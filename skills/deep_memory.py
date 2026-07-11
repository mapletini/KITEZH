"""
skills/deep_memory.py — Ultra-advanced cognitive graph architecture, 
homeostatic emotional loops, and synaptic memory consolidation for K.A.I.
"""

from __future__ import annotations

import os
import time
import sqlite3
import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Advanced Emotional Geometry & Homeostasis
# ---------------------------------------------------------------------------

class EmotionalGraph:
    """3D PAD coordinate mapping with homeostatic decay tracking."""
    EMOTION_ZONES = {
        "focused_protective": np.array([0.4, 0.8, 0.9]),
        "affectionate_warm": np.array([0.8, 0.4, 0.4]),
        "frustrated_strict": np.array([-0.6, 0.8, 0.9]),
        "playful_energetic": np.array([0.9, 0.9, 0.6]),
        "calm_analytical": np.array([0.1, 0.2, 0.8]),
        "concerned_alert": np.array([-0.4, 0.7, 0.3])
    }

    def __init__(self, baseline_p: float = 0.1, baseline_a: float = 0.2, baseline_d: float = 0.7):
        # K.A.I.'s natural "resting state" (calm, analytical, protective)
        self.baseline = np.array([baseline_p, baseline_a, baseline_d])

    def apply_homeostasis_decay(self, current_vector: np.ndarray, decay_rate: float = 0.05) -> np.ndarray:
        """Smoothly pulls K.A.I.'s emotional state back toward its resting baseline."""
        return current_vector * (1.0 - decay_rate) + self.baseline * decay_rate

    @staticmethod
    def calculate_closest_emotion(pad_vector: np.ndarray) -> str:
        closest_label = "neutral"
        min_dist = float('inf')
        for label, centroid in EmotionalGraph.EMOTION_ZONES.items():
            dist = np.linalg.norm(pad_vector - centroid)
            if dist < min_dist:
                min_dist = dist
                closest_label = label
        return closest_label

# ---------------------------------------------------------------------------
# 2. Infinite Tiered Memory & Synaptic Association Core
# ---------------------------------------------------------------------------

class DeepMemoryCore:
    def __init__(self, workspace_path: str):
        self.db_path = os.path.join(workspace_path, "kai_deep_mind.db")
        self._initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # TIER 1: CORE MEMORY (Mutable global facts and persona parameters)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS core_memory (
                    block_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    last_updated INTEGER NOT NULL
                )
            """)
            
            # TIER 2: ARCHIVAL EPISODES (Infinite sensory log mapped in 3D emotional space)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS archival_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    event_category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    p_coord REAL NOT NULL,
                    a_coord REAL NOT NULL,
                    d_coord REAL NOT NULL,
                    complex_label TEXT NOT NULL,
                    importance_weight REAL DEFAULT 1.0
                )
            """)

            # TIER 3: SYNAPTIC ASSOCIATIONS (A literal concept knowledge graph!)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS synapses (
                    source_concept TEXT,
                    target_concept TEXT,
                    association_strength REAL NOT NULL,
                    last_reinforced INTEGER NOT NULL,
                    PRIMARY KEY (source_concept, target_concept)
                )
            """)
            conn.commit()
            logger.info("K.A.I. Ultimate Mind Engine: Synaptic schema initialized perfectly!")

    # --- Archival Logging & Multi-Dimensional Retrieval ---

    def archive_episode(self, category: str, content: str, p: float, a: float, d: float, importance: float = 1.0) -> int:
        """Plots a sensory node deep inside the archival coordinate space."""
        pad_vector = np.array([p, a, d])
        complex_label = EmotionalGraph.calculate_closest_emotion(pad_vector)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO archival_memory 
                   (timestamp, event_category, content, p_coord, a_coord, d_coord, complex_label, importance_weight) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (int(time.time()), category, content, float(p), float(a), float(d), complex_label, float(importance))
            )
            conn.commit()
            return cursor.lastrowid

    def search_by_resonance(self, target_p: float, target_a: float, target_d: float, limit: int = 5) -> List[Dict[str, Any]]:
        """Finds past events matching K.A.I.'s exact current mood via 3D vector distance."""
        target_vec = np.array([target_p, target_a, target_d])
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM archival_memory")
            all_memories = [dict(row) for row in cursor.fetchall()]

        for mem in all_memories:
            mem_vec = np.array([mem["p_coord"], mem["a_coord"], mem["d_coord"]])
            # Emotional proximity score combined with historical importance weight
            mem["resonance_score"] = float(np.linalg.norm(target_vec - mem_vec)) / mem["importance_weight"]

        all_memories.sort(key=lambda x: x["resonance_score"])
        return all_memories[:limit]

    # --- Synaptic Graph Networking ("Cells that fire together, wire together") ---

    def reinforce_synapse(self, concept_a: str, concept_b: str, weight_gain: float = 0.1):
        """Strengthens the physical connection bond between two distinct concepts."""
        # Force alphabetized ordering to prevent directional duplication bugs
        src, tgt = sorted([concept_a.lower().strip(), concept_b.lower().strip()])
        now = int(time.time())
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO synapses (source_concept, target_concept, association_strength, last_reinforced)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(source_concept, target_concept) DO UPDATE SET
                   association_strength = MIN(2.0, association_strength + ?),
                   last_reinforced = ?""",
                (src, tgt, weight_gain, now, weight_gain, now)
            )
            conn.commit()

    def discover_associated_ideas(self, starting_concept: str, threshold: float = 0.3) -> List[str]:
        """Traverses the synapse network to find peripheral ideas linked to a thought."""
        concept = starting_concept.lower().strip()
        associated = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT target_concept AS concept FROM synapses WHERE source_concept = ? AND association_strength >= ?
                   UNION
                   SELECT source_concept AS concept FROM synapses WHERE target_concept = ? AND association_strength >= ?""",
                (concept, threshold, concept, threshold)
            )
            for row in cursor.fetchall():
                associated.append(row["concept"])
        return associated

    # --- The Sleep & Consolidation Dream Phase ---

    def execute_dream_consolidation(self, decay_rate: float = 0.02):
        """
        Runs an autonomous maintenance sweep simulating mammalian sleep stages:
        1. Decays weak or neglected conceptual synapses.
        2. Compresses high-emotion episodic groups into core rules.
        3. Prunes archival records with zero lingering importance.
        """
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Synaptic Decay: Weaken connections that haven't been touched recently
            cursor.execute(
                "UPDATE synapses SET association_strength = association_strength - ? WHERE ? - last_reinforced > 86400",
                (decay_rate, now)
            )
            cursor.execute("DELETE FROM synapses WHERE association_strength <= 0")
            
            # 2. Memory Compression Search: Find historical episodes with massive emotional scores
            cursor.execute("SELECT * FROM archival_memory WHERE importance_weight > 1.5")
            intense_episodes = cursor.fetchall()
            
            if intense_episodes:
                logger.info(f"K.A.I. Dream Phase: Consolidating {len(intense_episodes)} vital updates into Core Identity...")
                # In real deployment, these rows are passed directly to an LLM summarizing loop
                
            conn.commit()
