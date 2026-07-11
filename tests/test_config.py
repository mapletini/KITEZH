import os
import unittest
from unittest.mock import patch

import config


class TestEnvHelper(unittest.TestCase):
    def test_env_prefers_primary_key(self) -> None:
        with patch.dict(os.environ, {"PRIMARY": "one", "ALIAS": "two"}, clear=False):
            value = config._env("PRIMARY", "ALIAS", default="fallback")
        self.assertEqual(value, "one")

    def test_env_falls_back_to_alias_order(self) -> None:
        with patch.dict(os.environ, {"A1": "", "A2": "second"}, clear=False):
            value = config._env("P", "A1", "A2", default="fallback")
        self.assertEqual(value, "second")

    def test_env_skips_whitespace_only_values(self) -> None:
        with patch.dict(os.environ, {"PRIMARY": "   "}, clear=False):
            value = config._env("PRIMARY", default="fallback")
        self.assertEqual(value, "fallback")

    def test_env_uses_default_when_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            value = config._env("MISSING", "OTHER", default="fallback")
        self.assertEqual(value, "fallback")

