"""
skills/deep_memory.py — Ultra-advanced cognitive graph architecture, 
homeostatic emotional loops, exponential time-decay, synaptic memory consolidation,
emotional reconsolidation (warp), and flashbulb key-memory formation for K.A.I.

Memory model
------------
* **Episodic memories** — decay in salience over time; their emotional coordinates
  (PAD) drift toward the current mood on every recall (reconsolidation).  Fidelity
  erodes gradually, letting Kai's personality be shaped by accumulated distortions.
* **Key (flashbulb) memories** — formed during high emotional intensity or when
  importance exceeds KEY_MEMORY_IMPORTANCE_THRESHOLD.  They do not decay and are
  immune to emotional warping; they anchor Kai's identity.
* **Core beliefs** — stable, explicitly written facts (never expire).
"""

from __future__ import annotations

import os
import time
import math
import sqlite3
import logging
import numpy as np
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# importance_weight at or above this value automatically makes a memory a key memory
KEY_MEMORY_IMPORTANCE_THRESHOLD: float = 2.0

# Per-recall PAD drift rate for episodic reconsolidation (0 = no warp, 1 = instant overwrite)
WARP_RATE: float = 0.04

# Per-consolidation-run fidelity decay for episodic memories
FIDELITY_DECAY_RATE: float = 0.02

# Episodic fidelity never drops below this floor (memories become unreliable, not gone)
MIN_FIDELITY: float = 0.10

# Number of leading characters used to deduplicate Letta results against local results
_LETTA_DEDUP_PREFIX_LEN: int = 100

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
        # K.A.I.'s natural "resting state"
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
# 2. Tiered Memory & Synaptic Association Core
# ---------------------------------------------------------------------------

class DeepMemoryCore:
    def __init__(self, workspace_path: str = ".", letta_bridge=None) -> None:
        self.db_path = os.path.join(workspace_path, "kai_deep_mind.db")
        self._letta = letta_bridge
        self._initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_schema(self) -> None:
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

            # TIER 2: ARCHIVAL EPISODES (Episodic log mapped in 3D emotional space)
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

            # TIER 3: SYNAPTIC ASSOCIATIONS (Concept knowledge graph)
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

            # Migrate schema: add columns introduced in the memory-model upgrade.
            self._migrate_archival_schema(cursor, conn)

        logger.info("K.A.I. Ultimate Mind Engine initialized.")

    def _migrate_archival_schema(self, cursor: sqlite3.Cursor, conn: sqlite3.Connection) -> None:
        """Add new columns to archival_memory for existing databases."""
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(archival_memory)").fetchall()}
        additions: Dict[str, str] = {
            # 'episodic' or 'key' — key memories never decay or warp
            "memory_type":      "TEXT NOT NULL DEFAULT 'episodic'",
            # Immutable snapshot of the original content at creation time
            "original_content": "TEXT",
            # 1.0 = pristine recall, decays toward MIN_FIDELITY over time for episodic
            "fidelity":         "REAL NOT NULL DEFAULT 1.0",
            # Cumulative measure of how far the memory has drifted emotionally
            "distortion_score": "REAL NOT NULL DEFAULT 0.0",
            # How many times this memory has been recalled and reconsolidated
            "recall_count":     "INTEGER NOT NULL DEFAULT 0",
            # Timestamp of most recent recall
            "last_recalled":    "INTEGER",
            # PAD state at most recent recall (used for progressive warp tracking)
            "last_recall_p":    "REAL",
            "last_recall_a":    "REAL",
            "last_recall_d":    "REAL",
        }
        for col, typedef in additions.items():
            if col not in existing:
                # col and typedef come exclusively from the controlled additions dict above;
                # this assertion ensures no external input can reach the string-format path.
                assert col in {
                    "memory_type", "original_content", "fidelity", "distortion_score",
                    "recall_count", "last_recalled", "last_recall_p", "last_recall_a", "last_recall_d",
                }, f"Unexpected column name in migration: {col!r}"
                cursor.execute(f"ALTER TABLE archival_memory ADD COLUMN {col} {typedef}")
        conn.commit()

    # ---------------------------------------------------------------------------
    # Writing & Archiving Memories
    # ---------------------------------------------------------------------------

    def store_core_belief(self, block_id: str, content: str) -> None:
        """Saves a permanent rule or fact that never decays."""
        now = int(time.time())
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO core_memory (block_id, content, last_updated)
                   VALUES (?, ?, ?)
                   ON CONFLICT(block_id) DO UPDATE SET content = ?, last_updated = ?""",
                (block_id, content, now, content, now)
            )
            conn.commit()

    def archive_episode(
        self,
        category: str,
        content: str,
        p: float,
        a: float,
        d: float,
        importance: float = 1.0,
        memory_type: str = "episodic",
    ) -> int:
        """
        Archives an episode in 3D emotional space.

        Pass ``memory_type='key'`` (or set ``importance >= KEY_MEMORY_IMPORTANCE_THRESHOLD``)
        to create a flashbulb key memory that never decays or warps.
        """
        pad_vector = np.array([p, a, d])
        complex_label = EmotionalGraph.calculate_closest_emotion(pad_vector)

        # Auto-promote to key memory when emotional weight is high enough
        if importance >= KEY_MEMORY_IMPORTANCE_THRESHOLD:
            memory_type = "key"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO archival_memory
                   (timestamp, event_category, content, original_content,
                    p_coord, a_coord, d_coord, complex_label, importance_weight,
                    memory_type, fidelity, distortion_score, recall_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, 0)""",
                (
                    int(time.time()), category, content, content,
                    float(p), float(a), float(d), complex_label, float(importance),
                    memory_type,
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid

        if memory_type == "key":
            logger.info("K.A.I. Flashbulb! Key memory formed: '%s'", content[:80])

        # Mirror to Letta archival store (non-blocking best-effort)
        if self._letta is not None:
            self._letta.store_archival(
                content=content,
                category=category,
                emotion_label=complex_label,
                pad=(float(p), float(a), float(d)),
                memory_type=memory_type,
                fidelity=1.0,
            )

        return row_id

    # ---------------------------------------------------------------------------
    # Emotional Reconsolidation (Warp)
    # ---------------------------------------------------------------------------

    def _apply_emotional_warp(
        self, memory_id: int, current_pad: Tuple[float, float, float]
    ) -> None:
        """
        Gently drifts an episodic memory's PAD coordinates toward the current emotional
        state, simulating reconsolidation bias.  The original_content is never modified.
        Key memories are fully immune.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT * FROM archival_memory WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                return
            if row["memory_type"] == "key":
                return

            mem_pad = np.array([row["p_coord"], row["a_coord"], row["d_coord"]])
            curr_pad = np.array(current_pad)

            # Bounded drift toward current emotional state
            warped_pad = mem_pad + WARP_RATE * (curr_pad - mem_pad)
            warped_pad = np.clip(warped_pad, -1.0, 1.0)

            new_label = EmotionalGraph.calculate_closest_emotion(warped_pad)
            new_distortion = min(1.0, row["distortion_score"] + WARP_RATE)
            new_fidelity = max(MIN_FIDELITY, row["fidelity"] - WARP_RATE * 0.5)
            now = int(time.time())

            cursor.execute(
                """UPDATE archival_memory SET
                   p_coord = ?, a_coord = ?, d_coord = ?, complex_label = ?,
                   distortion_score = ?, fidelity = ?,
                   recall_count = recall_count + 1, last_recalled = ?,
                   last_recall_p = ?, last_recall_a = ?, last_recall_d = ?
                   WHERE id = ?""",
                (
                    float(warped_pad[0]), float(warped_pad[1]), float(warped_pad[2]),
                    new_label, new_distortion, new_fidelity, now,
                    float(current_pad[0]), float(current_pad[1]), float(current_pad[2]),
                    memory_id,
                ),
            )
            conn.commit()

        logger.debug(
            "Memory %d reconsolidated: label '%s'→'%s', fidelity %.2f, distortion %.2f",
            memory_id, row["complex_label"], new_label, new_fidelity, new_distortion,
        )

    # ---------------------------------------------------------------------------
    # Salience, Decay & Retrieval
    # ---------------------------------------------------------------------------

    def read_core_memory(self) -> List[str]:
        """Fetches all permanent core beliefs."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT content FROM core_memory ORDER BY last_updated ASC")
            return [row["content"] for row in cursor.fetchall()]

    def _calculate_salience(
        self,
        record: sqlite3.Row,
        current_pad: Tuple[float, float, float],
        current_time: float,
    ) -> float:
        """
        Cognitive salience = temporal score + emotional resonance bonus.

        Key memories bypass temporal decay so they are always accessible.
        Episodic memories additionally scale by current fidelity.
        """
        memory_type = record["memory_type"] if "memory_type" in record.keys() else "episodic"
        fidelity = record["fidelity"] if "fidelity" in record.keys() else 1.0

        if memory_type == "key":
            # Key memories have full, permanent base salience
            temporal_score = float(record["importance_weight"])
        else:
            time_elapsed = current_time - record["timestamp"]
            decay_rate = 0.00005
            base_importance = float(record["importance_weight"]) * fidelity
            temporal_score = base_importance * math.exp(-decay_rate * time_elapsed)

        # Emotional resonance bonus: memories formed in a similar mood surface more easily
        mem_pad = np.array([record["p_coord"], record["a_coord"], record["d_coord"]])
        curr_pad = np.array(current_pad)
        pad_distance = float(np.linalg.norm(mem_pad - curr_pad))
        resonance_bonus = max(0.0, 1.0 - (pad_distance / 2.0))

        return temporal_score + resonance_bonus

    def search_by_resonance(
        self,
        target_p: float,
        target_a: float,
        target_d: float,
        limit: int = 5,
        warp_on_recall: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Returns the top ``limit`` memories ranked by salience.

        Episodic memories undergo emotional reconsolidation on each recall
        (``warp_on_recall=True`` by default), gradually tinting them with the
        mood Kai was in when it remembered them.
        """
        current_time = time.time()
        target_vec = (target_p, target_a, target_d)
        scored: List[Tuple[float, Dict[str, Any]]] = []

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM archival_memory")
            rows = cursor.fetchall()

        for row in rows:
            salience = self._calculate_salience(row, target_vec, current_time)
            if salience > 0.1:
                scored.append((salience, dict(row)))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:limit]

        if warp_on_recall:
            for _, mem in top:
                if mem.get("memory_type", "episodic") == "episodic":
                    self._apply_emotional_warp(mem["id"], target_vec)

        result: List[Dict[str, Any]] = []
        for salience, mem in top:
            result.append({
                "id": mem["id"],
                "content": mem["content"],
                "original_content": mem.get("original_content") or mem["content"],
                "category": mem["event_category"],
                "complex_label": mem["complex_label"],
                "memory_type": mem.get("memory_type", "episodic"),
                "fidelity": mem.get("fidelity", 1.0),
                "distortion_score": mem.get("distortion_score", 0.0),
                "is_warped": (mem.get("distortion_score", 0.0) > 0.1),
                "salience": salience,
                "age_seconds": current_time - mem["timestamp"],
            })

        # Augment with Letta semantic results (best-effort; never displaces local results)
        if self._letta is not None:
            emotion_label = EmotionalGraph.calculate_closest_emotion(
                np.array([target_p, target_a, target_d])
            )
            letta_hits = self._letta.search_archival(emotion_label, limit=limit)
            seen_contents = {r["content"][:_LETTA_DEDUP_PREFIX_LEN] for r in result}
            for hit in letta_hits:
                if hit["content"][:_LETTA_DEDUP_PREFIX_LEN] not in seen_contents:
                    result.append(hit)
                    seen_contents.add(hit["content"][:_LETTA_DEDUP_PREFIX_LEN])

        return result

    # ---------------------------------------------------------------------------
    # Personality Synthesis
    # ---------------------------------------------------------------------------

    def synthesize_personality_context(self) -> str:
        """
        Builds a structured personality context from Kai's entire memory state.

        Key (flashbulb) memories act as identity anchors that never change.
        Episodic memories — including decayed and emotionally warped ones — add
        tonal coloring, biases, and lived history that shape Kai's personality.
        The returned string is designed to be injected directly into LLM prompts.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            key_rows = cursor.execute(
                "SELECT * FROM archival_memory WHERE memory_type = 'key' ORDER BY timestamp ASC"
            ).fetchall()
            episodic_rows = cursor.execute(
                """SELECT * FROM archival_memory WHERE memory_type = 'episodic'
                   ORDER BY importance_weight DESC, timestamp DESC LIMIT 10"""
            ).fetchall()
            core_rows = cursor.execute(
                "SELECT content FROM core_memory ORDER BY last_updated ASC"
            ).fetchall()

        lines = ["=== K.A.I. IDENTITY CONTEXT ==="]

        if core_rows:
            lines.append("\n[Core Beliefs — Permanent Rules]")
            for row in core_rows:
                lines.append(f"  • {row['content']}")

        if key_rows:
            lines.append("\n[Flashbulb Memories — Identity Anchors]")
            for row in key_rows:
                lines.append(
                    f"  ★ [{row['event_category']} / {row['complex_label']}] {row['content']}"
                )

        if episodic_rows:
            lines.append("\n[Episodic Memory — Emotional History]")
            for row in episodic_rows:
                fidelity = row["fidelity"] if "fidelity" in row.keys() else 1.0
                distortion = row["distortion_score"] if "distortion_score" in row.keys() else 0.0
                label = row["complex_label"]
                tag = f"[{fidelity:.0%} fidelity"
                if distortion > 0.1:
                    tag += f", emotionally recolored: {label}"
                tag += "]"
                lines.append(f"  ~ [{row['event_category']}] {row['content']} {tag}")

        # Append Letta semantic archival context when available
        if self._letta is not None:
            letta_hits = self._letta.search_archival("personality identity memories", limit=5)
            if letta_hits:
                lines.append("\n[Letta Archival Context — Semantic Recall]")
                for hit in letta_hits:
                    lines.append(f"  ◈ {hit['content']}")

        return "\n".join(lines)

    # ---------------------------------------------------------------------------
    # Synaptic Graph Networking & Dream Phase
    # ---------------------------------------------------------------------------

    def reinforce_synapse(self, concept_a: str, concept_b: str, weight_gain: float = 0.1) -> None:
        """Strengthens the association bond between two concepts."""
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
                (src, tgt, weight_gain, now, weight_gain, now),
            )
            conn.commit()

    def discover_associated_ideas(self, starting_concept: str, threshold: float = 0.3) -> List[str]:
        """Traverses the synapse network to find peripheral ideas linked to a concept."""
        concept = starting_concept.lower().strip()
        associated: List[str] = []
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT target_concept AS concept FROM synapses
                   WHERE source_concept = ? AND association_strength >= ?
                   UNION
                   SELECT source_concept AS concept FROM synapses
                   WHERE target_concept = ? AND association_strength >= ?""",
                (concept, threshold, concept, threshold),
            )
            for row in cursor.fetchall():
                associated.append(row["concept"])
        return associated

    def execute_dream_consolidation(self, decay_rate: float = 0.02) -> None:
        """
        Autonomous maintenance sweep simulating mammalian sleep-stage consolidation.

        1. Weakens synaptic connections that haven't been reinforced recently.
        2. Decays fidelity of old episodic memories (key memories are immune).
        3. Logs high-importance episodes that may need promotion.
        """
        now = int(time.time())
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Synaptic decay: weaken unused connections
            cursor.execute(
                "UPDATE synapses SET association_strength = association_strength - ?"
                " WHERE ? - last_reinforced > 86400",
                (decay_rate, now),
            )
            cursor.execute("DELETE FROM synapses WHERE association_strength <= 0")

            # 2. Fidelity decay for episodic memories older than one hour
            cursor.execute(
                """UPDATE archival_memory
                   SET fidelity = MAX(?, fidelity - ?)
                   WHERE memory_type = 'episodic' AND ? - timestamp > 3600""",
                (MIN_FIDELITY, FIDELITY_DECAY_RATE, now),
            )

            # 3. Log intense episodes (candidates for manual key-memory promotion)
            intense_episodes = cursor.execute(
                "SELECT * FROM archival_memory WHERE importance_weight > 1.5"
            ).fetchall()

            if intense_episodes:
                logger.info(
                    "K.A.I. Dream Phase: %d intense episodes in memory, "
                    "some may shape Core Identity.",
                    len(intense_episodes),
                )

            conn.commit()

