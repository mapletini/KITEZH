import unittest
from unittest.mock import patch

from fastapi import HTTPException

import web_ui


class TestWebUiSecurity(unittest.TestCase):
    def test_require_key_rejects_malformed_key_type(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "correct-key"):
            with self.assertRaises(HTTPException) as ctx:
                web_ui._require_key(None)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.status_code, 400)

    def test_require_key_rejects_default_secret(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "changeme"), patch.object(
            web_ui.config, "INSECURE_AI_KEYS", ("changeme", "", "change_me_ai_bridge_secret")
        ):
            with self.assertRaises(HTTPException) as ctx:
                web_ui._require_key("changeme")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_require_key_rejects_other_insecure_secret(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "change_me_ai_bridge_secret"), patch.object(
            web_ui.config, "INSECURE_AI_KEYS", ("changeme", "", "change_me_ai_bridge_secret")
        ):
            with self.assertRaises(HTTPException) as ctx:
                web_ui._require_key("change_me_ai_bridge_secret")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_require_key_rejects_wrong_key(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "correct-key"), patch.object(
            web_ui.config, "INSECURE_AI_KEYS", ("changeme", "", "change_me_ai_bridge_secret")
        ):
            with self.assertRaises(HTTPException) as ctx:
                web_ui._require_key("wrong-key")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_require_key_accepts_valid_secure_key(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "correct-key"), patch.object(
            web_ui.config, "INSECURE_AI_KEYS", ("changeme", "", "change_me_ai_bridge_secret")
        ):
            web_ui._require_key("correct-key")

    def test_extract_concepts_filters_stopwords_and_duplicates(self) -> None:
        concepts = web_ui._extract_concepts("The memory memory graph syncs kai memory state quickly.")
        self.assertIn("memory", concepts)
        self.assertIn("graph", concepts)
        self.assertNotIn("the", concepts)
        self.assertEqual(concepts.count("memory"), 1)

    def test_reinforce_message_concepts_builds_pairs(self) -> None:
        with patch.object(web_ui._web_memory, "reinforce_synapse") as reinforce:
            web_ui._reinforce_message_concepts("alpha beta gamma")
        self.assertGreaterEqual(reinforce.call_count, 3)

    def test_emotion_state_returns_snapshot(self) -> None:
        with patch.object(web_ui._web_neuro, "emotion_snapshot", return_value={"label": "joy"}):
            result = __import__("asyncio").run(web_ui.emotion_state())
        self.assertEqual(result, {"emotion": {"label": "joy"}})

    def test_seed_belief_rejects_empty_fields(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "correct-key"), patch.object(
            web_ui.config, "INSECURE_AI_KEYS", ("changeme", "", "change_me_ai_bridge_secret")
        ):
            with self.assertRaises(HTTPException) as ctx:
                __import__("asyncio").run(
                    web_ui.seed_belief({"block_id": " ", "content": "x"}, x_ai_key="correct-key")
                )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_seed_belief_stores_core_memory(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "correct-key"), patch.object(
            web_ui.config, "INSECURE_AI_KEYS", ("changeme", "", "change_me_ai_bridge_secret")
        ), patch.object(web_ui._web_memory, "store_core_belief") as store:
            result = __import__("asyncio").run(
                web_ui.seed_belief({"block_id": "identity", "content": "Protect the user."}, x_ai_key="correct-key")
            )
        store.assert_called_once_with("identity", "Protect the user.")
        self.assertEqual(result, {"status": "ok"})
