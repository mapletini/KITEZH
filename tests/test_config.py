import os
import tempfile
import unittest
from pathlib import Path
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


class TestEnvFlagHelper(unittest.TestCase):
    def test_env_flag_accepts_true_values(self) -> None:
        with patch.dict(os.environ, {"FLAG": "on"}, clear=False):
            value = config._env_flag("FLAG", default=False)
        self.assertTrue(value)

    def test_env_flag_accepts_false_values(self) -> None:
        with patch.dict(os.environ, {"FLAG": "0"}, clear=False):
            value = config._env_flag("FLAG", default=True)
        self.assertFalse(value)

    def test_env_flag_uses_default_for_unknown_values(self) -> None:
        with patch.dict(os.environ, {"FLAG": "maybe"}, clear=False):
            value = config._env_flag("FLAG", default=True)
        self.assertTrue(value)


class TestRuntimeSecurityWarnings(unittest.TestCase):
    def test_warns_on_insecure_defaults(self) -> None:
        with patch.object(config, "AI_KEY", "changeme"), \
             patch.object(config, "COMMAND_SIGNING_SECRET", "changeme-signing-secret"), \
             patch.object(config, "DISCORD_ENABLED", True), \
             patch.object(config, "DISCORD_BOT_TOKEN", ""), \
             patch.object(config, "DISCORD_VOICE_ENABLED", True), \
             patch.object(config, "DISCORD_BOT_USER_ID", ""):
            warnings = config.runtime_security_warnings()

        self.assertGreaterEqual(len(warnings), 4)

    def test_no_warnings_when_configured(self) -> None:
        with patch.object(config, "AI_KEY", "secure-token"), \
             patch.object(config, "COMMAND_SIGNING_SECRET", "secure-signing-secret"), \
             patch.object(config, "DISCORD_ENABLED", True), \
             patch.object(config, "DISCORD_BOT_TOKEN", "bot-token"), \
             patch.object(config, "DISCORD_VOICE_ENABLED", True), \
             patch.object(config, "DISCORD_BOT_USER_ID", "123"):
            warnings = config.runtime_security_warnings()

        self.assertEqual(warnings, [])


class TestDotenvLoading(unittest.TestCase):
    def test_load_env_file_reads_key_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                """
# comment
KITEZH_TEST_ALPHA=one
export KITEZH_TEST_BETA="two"
KITEZH_TEST_GAMMA='three'
""".strip(),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = config._load_env_file(str(env_path), override=False)
                self.assertEqual(loaded, 3)
                self.assertEqual(os.environ.get("KITEZH_TEST_ALPHA"), "one")
                self.assertEqual(os.environ.get("KITEZH_TEST_BETA"), "two")
                self.assertEqual(os.environ.get("KITEZH_TEST_GAMMA"), "three")

    def test_load_env_file_does_not_override_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("KITEZH_TEST_ALPHA=new-value", encoding="utf-8")
            with patch.dict(os.environ, {"KITEZH_TEST_ALPHA": "existing"}, clear=True):
                loaded = config._load_env_file(str(env_path), override=False)
                self.assertEqual(loaded, 0)
                self.assertEqual(os.environ.get("KITEZH_TEST_ALPHA"), "existing")

    def test_reload_runtime_config_applies_file_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("KITEZH_TEST_RELOAD=applied", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                reloaded = config.reload_runtime_config(env_path=str(env_path), override=True)
                self.assertIs(reloaded, config)
                self.assertEqual(os.environ.get("KITEZH_TEST_RELOAD"), "applied")
