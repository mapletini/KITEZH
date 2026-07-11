import unittest
from unittest.mock import patch

from fastapi import HTTPException

import web_ui


class TestWebUiSecurity(unittest.TestCase):
    def test_require_key_rejects_default_secret(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "changeme"):
            with self.assertRaises(HTTPException) as ctx:
                web_ui._require_key("changeme")
        self.assertEqual(ctx.exception.status_code, 503)

    def test_require_key_rejects_wrong_key(self) -> None:
        with patch.object(web_ui.config, "AI_KEY", "correct-key"):
            with self.assertRaises(HTTPException) as ctx:
                web_ui._require_key("wrong-key")
        self.assertEqual(ctx.exception.status_code, 403)

