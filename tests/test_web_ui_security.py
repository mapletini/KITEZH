import unittest
from unittest.mock import patch

from fastapi import HTTPException

import web_ui


class TestWebUiSecurity(unittest.TestCase):
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
