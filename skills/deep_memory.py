"""
skills/deep_memory.py — Full tiered cognitive memory and 3D emotional graphing for K.A.I.
"""

from __future__ import annotations

import os
import time
import json
import sqlite3
import logging
import numpy as np
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 3D Emotional Geometry
# ---------------------------------------------------------------------------

class EmotionalGraph:
    """
    Maps the literal 3D PAD space to complex, nuanced emotional states.
    P = Pleasure (-1 to 1)
    A = Arousal (0 to 1)
    D = Dominance (0 to 1)
    """
    EMOTION_ZONES = {
        "focused_protective": np.array([0.4, 0.8, 0.9]),
        "affectionate_warm": np.array([0.8, 0.4, 0.4]),
        "frustrated_strict": np.array([-0.6, 0.8, 0.9]),
        "playful_energetic": np.array([0.9, 0.9, 0.6]),
        "calm_analytical": np.array([0.1, 0.2, 0.8]),
        "concerned_alert": np.array([-0.4, 0.7, 0.3])
    }

    @staticmethod
    def calculate_closest_emotion(pad_vector: np.ndarray) -> str:
        """Finds the human-readable label for a specific 3D coordinate."""
        closest_label = "neutral"
        min_dist = float('inf')
        
        for label, centroid in EmotionalGraph.EMOTION_ZONES.items():
            dist = np.linalg.norm(pad_vector - centroid)
            if dist < min_dist:
                min_dist = dist
                closest_label = label
                
        return closest_label

# ---------------------------------------------------------------------------
# Tiered Memory Core
# ---------------------------------------------------------------------------

class DeepMemoryCore:
    """
    A fully realized, three-tier memory architecture.
    1. Working Buffer: Immediate context (handled by the LLM context window).
    2. Core Memory: Mutable, permanent facts (identity, rules, relationships).
    3. Archival Memory: Infinite episodic log, searchable by 3D emotional distance.
    """
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
            
            # CORE MEMORY: Semantic blocks that define reality
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS core_memory (
                    block_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    last_updated INTEGER NOT NULL
                )
            """)
            
            # ARCHIVAL MEMORY: Episodic events with 3D math coordinates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS archival_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    event_category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    p_coord REAL NOT NULL,
                    a_coord REAL NOT NULL,
                    d_coord REAL NOT NULL,
                    complex_label TEXT NOT NULL
                )
            """)
            conn.commit()

    # --- Core Memory (The Semantic Self) ---

    def update_core_block(self, block_id: str, new_content: str) -> None:
        """K.A.I. can rewrite its fundamental understanding of the world."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "REPLACE INTO core_memory (block_id, content, last_updated) VALUES (?, ?, ?)",
                (block_id, new_content, int(time.time()))
            )
            conn.commit()

    def read_core_memory(self) -> Dict[str, str]:
        """Pulls the entire personality and fact foundation into the active prompt."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT block_id, content FROM core_memory")
            return {row["block_id"]: row["content"] for row in cursor.fetchall()}

    # --- Archival Memory (The Emotional Graph) ---

    def archive_episode(self, category: str, content: str, p: float, a: float, d: float) -> int:
        """Saves a memory exactly where it belongs in the 3D emotional cube."""
        pad_vector = np.array([p, a, d])
        complex_label = EmotionalGraph.calculate_closest_emotion(pad_vector)
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO archival_memory 
                   (timestamp, event_category, content, p_coord, a_coord, d_coord, complex_label) 
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (int(time.time()), category, content, float(p), float(a), float(d), complex_label)
            )
            conn.commit()
            return cursor.lastrowid

    def search_by_emotional_resonance(self, target_p: float, target_a: float, target_d: float, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Pulls past memories that feel exactly like K.A.I.'s current mood!
        Uses raw numpy Euclidean distance calculation across the 3D space.
        """
        target_vec = np.array([target_p, target_a, target_d])
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM archival_memory")
            all_memories = [dict(row) for row in cursor.fetchall()]

        # Score all memories by how close they are in 3D space
        for mem in all_memories:
            mem_vec = np.array([mem["p_coord"], mem["a_coord"], mem["d_coord"]])
            mem["emotional_distance"] = float(np.linalg.norm(target_vec - mem_vec))

        # Sort by closest emotional match
        all_memories.sort(key=lambda x: x["emotional_distance"])
        return all_memories[:limit]
      
