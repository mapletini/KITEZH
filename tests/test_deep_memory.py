"""
tests/test_deep_memory.py — Unit tests for the upgraded DeepMemoryCore.

Covers:
* Schema initialisation and migration (new columns appear on existing DBs)
* archive_episode (episodic and key)
* Auto-promotion to key when importance >= KEY_MEMORY_IMPORTANCE_THRESHOLD
* Key memories are immune to fidelity decay and emotional warp
* Episodic fidelity decays during dream consolidation
* Emotional reconsolidation shifts PAD coords on recall
* search_by_resonance returns results and respects salience threshold
* Key memories always have salience above the cutoff
* synthesize_personality_context includes all three memory tiers
"""

import os
import tempfile
import unittest

from skills.deep_memory import (
    DeepMemoryCore,
    EmotionalGraph,
    FIDELITY_DECAY_RATE,
    KEY_MEMORY_IMPORTANCE_THRESHOLD,
    MIN_FIDELITY,
    WARP_RATE,
)


class TestEmotionalGraph(unittest.TestCase):
    def test_calculate_closest_emotion_returns_known_label(self) -> None:
        # A vector very close to "calm_analytical" centroid [0.1, 0.2, 0.8]
        label = EmotionalGraph.calculate_closest_emotion([0.1, 0.2, 0.8])
        self.assertEqual(label, "calm_analytical")

    def test_calculate_closest_emotion_returns_string(self) -> None:
        import numpy as np
        label = EmotionalGraph.calculate_closest_emotion(np.array([0.0, 0.0, 0.0]))
        self.assertIsInstance(label, str)


class TestDeepMemoryCore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.mem = DeepMemoryCore(workspace_path=self._tmp)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    # Schema / migration
    # ------------------------------------------------------------------

    def test_schema_creates_tables(self) -> None:
        import sqlite3
        conn = sqlite3.connect(self.mem.db_path)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        self.assertIn("archival_memory", tables)
        self.assertIn("core_memory", tables)
        self.assertIn("synapses", tables)

    def test_migration_adds_new_columns(self) -> None:
        import sqlite3
        conn = sqlite3.connect(self.mem.db_path)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(archival_memory)").fetchall()}
        conn.close()
        for col in ("memory_type", "original_content", "fidelity", "distortion_score",
                    "recall_count", "last_recalled", "last_recall_p", "last_recall_a", "last_recall_d"):
            self.assertIn(col, columns, f"Missing column: {col}")

    # ------------------------------------------------------------------
    # archive_episode
    # ------------------------------------------------------------------

    def test_archive_episode_returns_id(self) -> None:
        row_id = self.mem.archive_episode("test", "hello", p=0.1, a=0.2, d=0.8)
        self.assertIsInstance(row_id, int)
        self.assertGreater(row_id, 0)

    def test_archive_episode_default_type_is_episodic(self) -> None:
        import sqlite3
        self.mem.archive_episode("test", "episodic content", p=0.1, a=0.2, d=0.8)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row["memory_type"], "episodic")

    def test_archive_episode_explicit_key_type(self) -> None:
        import sqlite3
        self.mem.archive_episode("important", "a pivotal moment", p=0.9, a=0.9, d=0.6,
                                  memory_type="key")
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row["memory_type"], "key")

    def test_archive_episode_auto_promotes_high_importance_to_key(self) -> None:
        import sqlite3
        self.mem.archive_episode("big event", "very important",
                                  p=0.5, a=0.5, d=0.5,
                                  importance=KEY_MEMORY_IMPORTANCE_THRESHOLD)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row["memory_type"], "key")

    def test_archive_episode_stores_original_content(self) -> None:
        import sqlite3
        self.mem.archive_episode("test", "original text", p=0.1, a=0.2, d=0.8)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT original_content FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertEqual(row["original_content"], "original text")

    def test_archive_episode_fidelity_starts_at_one(self) -> None:
        import sqlite3
        self.mem.archive_episode("test", "hello", p=0.1, a=0.2, d=0.8)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT fidelity FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertAlmostEqual(row["fidelity"], 1.0)

    # ------------------------------------------------------------------
    # Key memory: no fidelity decay and no emotional warp
    # ------------------------------------------------------------------

    def test_key_memory_immune_to_fidelity_decay(self) -> None:
        import sqlite3
        self.mem.archive_episode("anchor", "a flashbulb event", p=0.8, a=0.9, d=0.6,
                                  memory_type="key")
        # Force time to appear old by backdating the timestamp
        conn = sqlite3.connect(self.mem.db_path)
        import time
        old_ts = int(time.time()) - 7200  # 2 hours ago
        conn.execute("UPDATE archival_memory SET timestamp = ?", (old_ts,))
        conn.commit()
        conn.close()

        self.mem.execute_dream_consolidation()

        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT fidelity FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertAlmostEqual(row["fidelity"], 1.0, msg="Key memory fidelity must never decay")

    def test_key_memory_immune_to_emotional_warp(self) -> None:
        import sqlite3
        self.mem.archive_episode("anchor", "a flashbulb event", p=0.8, a=0.9, d=0.6,
                                  memory_type="key")
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        before = dict(conn.execute("SELECT p_coord, a_coord, d_coord FROM archival_memory").fetchone())
        conn.close()

        # Warp toward the opposite end of the emotional space
        row_id = conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row_id = conn.execute("SELECT id FROM archival_memory LIMIT 1").fetchone()["id"]
        conn.close()

        for _ in range(20):
            self.mem._apply_emotional_warp(row_id, (-1.0, -1.0, -1.0))

        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        after = dict(conn.execute("SELECT p_coord, a_coord, d_coord FROM archival_memory").fetchone())
        conn.close()

        self.assertAlmostEqual(before["p_coord"], after["p_coord"],
                               msg="Key memory PAD must not change")

    # ------------------------------------------------------------------
    # Episodic decay and warp
    # ------------------------------------------------------------------

    def test_episodic_fidelity_decays_on_consolidation(self) -> None:
        import sqlite3
        import time
        self.mem.archive_episode("daily", "mundane chat", p=0.1, a=0.2, d=0.8)
        old_ts = int(time.time()) - 7200
        conn = sqlite3.connect(self.mem.db_path)
        conn.execute("UPDATE archival_memory SET timestamp = ?", (old_ts,))
        conn.commit()
        conn.close()

        self.mem.execute_dream_consolidation()

        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT fidelity FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertLess(row["fidelity"], 1.0, "Episodic fidelity should have decayed")
        self.assertGreaterEqual(row["fidelity"], MIN_FIDELITY, "Fidelity must not drop below MIN_FIDELITY")

    def test_episodic_warp_shifts_pad_coords(self) -> None:
        import sqlite3
        self.mem.archive_episode("calm", "quiet moment", p=0.1, a=0.1, d=0.8)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row_id = conn.execute("SELECT id FROM archival_memory LIMIT 1").fetchone()["id"]
        before_p = conn.execute("SELECT p_coord FROM archival_memory LIMIT 1").fetchone()["p_coord"]
        conn.close()

        # Apply warp pulling strongly toward high-pleasure state
        for _ in range(10):
            self.mem._apply_emotional_warp(row_id, (1.0, 0.9, 0.6))

        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        after_p = conn.execute("SELECT p_coord FROM archival_memory LIMIT 1").fetchone()["p_coord"]
        conn.close()

        self.assertGreater(after_p, before_p, "Pleasure coord should drift toward warp target")

    def test_episodic_distortion_score_increases_on_warp(self) -> None:
        import sqlite3
        self.mem.archive_episode("test", "neutral text", p=0.0, a=0.0, d=0.0)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row_id = conn.execute("SELECT id FROM archival_memory LIMIT 1").fetchone()["id"]
        conn.close()

        self.mem._apply_emotional_warp(row_id, (0.9, 0.9, 0.9))

        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT distortion_score FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertGreater(row["distortion_score"], 0.0)

    def test_fidelity_never_drops_below_min(self) -> None:
        import sqlite3
        self.mem.archive_episode("ancient", "very old memory", p=0.0, a=0.0, d=0.0)
        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row_id = conn.execute("SELECT id FROM archival_memory LIMIT 1").fetchone()["id"]
        conn.close()

        for _ in range(1000):
            self.mem._apply_emotional_warp(row_id, (0.9, 0.9, 0.9))

        conn = sqlite3.connect(self.mem.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT fidelity FROM archival_memory LIMIT 1").fetchone()
        conn.close()
        self.assertGreaterEqual(row["fidelity"], MIN_FIDELITY)

    # ------------------------------------------------------------------
    # search_by_resonance
    # ------------------------------------------------------------------

    def test_search_by_resonance_returns_results(self) -> None:
        for i in range(3):
            self.mem.archive_episode("test", f"memory {i}", p=0.1 * i, a=0.2, d=0.8)
        results = self.mem.search_by_resonance(0.1, 0.2, 0.8, warp_on_recall=False)
        self.assertGreater(len(results), 0)

    def test_search_by_resonance_returns_required_fields(self) -> None:
        self.mem.archive_episode("test", "sample", p=0.1, a=0.2, d=0.8)
        results = self.mem.search_by_resonance(0.1, 0.2, 0.8, warp_on_recall=False)
        self.assertGreater(len(results), 0)
        r = results[0]
        for key in ("id", "content", "salience", "memory_type", "fidelity", "distortion_score"):
            self.assertIn(key, r, f"Missing key: {key}")

    def test_search_key_memory_appears_in_results(self) -> None:
        self.mem.archive_episode("anchor", "key event", p=0.8, a=0.9, d=0.6, memory_type="key")
        results = self.mem.search_by_resonance(-0.5, 0.0, 0.0, limit=5, warp_on_recall=False)
        types = [r["memory_type"] for r in results]
        self.assertIn("key", types, "Key memory must appear in search results regardless of emotional distance")

    def test_search_by_resonance_respects_limit(self) -> None:
        for i in range(10):
            self.mem.archive_episode("test", f"item {i}", p=0.1, a=0.2, d=0.8)
        results = self.mem.search_by_resonance(0.1, 0.2, 0.8, limit=3, warp_on_recall=False)
        self.assertLessEqual(len(results), 3)

    # ------------------------------------------------------------------
    # synthesize_personality_context
    # ------------------------------------------------------------------

    def test_synthesize_includes_core_beliefs(self) -> None:
        self.mem.store_core_belief("rule_1", "Always protect the user.")
        ctx = self.mem.synthesize_personality_context()
        self.assertIn("Always protect the user.", ctx)

    def test_synthesize_includes_key_memories(self) -> None:
        self.mem.archive_episode("anchor", "first contact with the user", p=0.8, a=0.9, d=0.6,
                                  memory_type="key")
        ctx = self.mem.synthesize_personality_context()
        self.assertIn("first contact with the user", ctx)
        self.assertIn("★", ctx)  # Key memory marker

    def test_synthesize_includes_episodic_memories(self) -> None:
        self.mem.archive_episode("chat", "routine conversation", p=0.1, a=0.2, d=0.8)
        ctx = self.mem.synthesize_personality_context()
        self.assertIn("routine conversation", ctx)
        self.assertIn("~", ctx)  # Episodic memory marker

    def test_synthesize_returns_string(self) -> None:
        ctx = self.mem.synthesize_personality_context()
        self.assertIsInstance(ctx, str)
        self.assertGreater(len(ctx), 0)

    # ------------------------------------------------------------------
    # Core beliefs
    # ------------------------------------------------------------------

    def test_store_and_read_core_belief(self) -> None:
        self.mem.store_core_belief("kb_1", "I am K.A.I.")
        beliefs = self.mem.read_core_memory()
        self.assertIn("I am K.A.I.", beliefs)

    def test_update_core_belief(self) -> None:
        self.mem.store_core_belief("kb_1", "initial belief")
        self.mem.store_core_belief("kb_1", "updated belief")
        beliefs = self.mem.read_core_memory()
        self.assertIn("updated belief", beliefs)
        self.assertNotIn("initial belief", beliefs)

    # ------------------------------------------------------------------
    # Synaptic graph
    # ------------------------------------------------------------------

    def test_reinforce_and_discover_synapse(self) -> None:
        self.mem.reinforce_synapse("trust", "safety", weight_gain=0.5)
        associated = self.mem.discover_associated_ideas("trust")
        self.assertIn("safety", associated)

    def test_synapse_below_threshold_not_discovered(self) -> None:
        self.mem.reinforce_synapse("trust", "caution", weight_gain=0.1)
        associated = self.mem.discover_associated_ideas("trust", threshold=0.5)
        self.assertNotIn("caution", associated)


if __name__ == "__main__":
    unittest.main()
