import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import web_ui


class TestLanAdminGuard(unittest.TestCase):
    def test_guard_disabled_when_no_cidr_configured(self) -> None:
        """When LAN_CIDR is empty the guard is a no-op — all IPs are allowed."""
        with patch.object(web_ui.config, "LAN_CIDR", ""):
            self.assertTrue(web_ui._is_admin_allowed("127.0.0.1"))
            self.assertTrue(web_ui._is_admin_allowed("8.8.8.8"))
            self.assertTrue(web_ui._is_admin_allowed(None))

    def test_loopback_ipv4_is_blocked(self) -> None:
        """cloudflared routes public traffic in via loopback — must be denied."""
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertFalse(web_ui._is_admin_allowed("127.0.0.1"))

    def test_loopback_ipv6_is_blocked(self) -> None:
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertFalse(web_ui._is_admin_allowed("::1"))

    def test_external_ip_is_blocked(self) -> None:
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertFalse(web_ui._is_admin_allowed("8.8.8.8"))

    def test_lan_ip_within_cidr_is_allowed(self) -> None:
        """Direct Ethernet connections from within the configured subnet are allowed."""
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertTrue(web_ui._is_admin_allowed("192.168.1.42"))

    def test_lan_ip_on_subnet_boundary_is_allowed(self) -> None:
        with patch.object(web_ui.config, "LAN_CIDR", "10.0.0.0/8"):
            self.assertTrue(web_ui._is_admin_allowed("10.255.255.1"))

    def test_lan_ip_outside_cidr_is_blocked(self) -> None:
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertFalse(web_ui._is_admin_allowed("192.168.2.1"))

    def test_none_client_is_blocked_when_cidr_set(self) -> None:
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertFalse(web_ui._is_admin_allowed(None))

    def test_invalid_ip_string_is_blocked(self) -> None:
        with patch.object(web_ui.config, "LAN_CIDR", "192.168.1.0/24"):
            self.assertFalse(web_ui._is_admin_allowed("not-an-ip"))

    def test_invalid_cidr_blocks_all_access(self) -> None:
        """A misconfigured CIDR fails safe — all admin requests are denied."""
        with patch.object(web_ui.config, "LAN_CIDR", "not-a-cidr"):
            self.assertFalse(web_ui._is_admin_allowed("192.168.1.1"))


class TestConceptExtraction(unittest.TestCase):
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

    def test_display_state_returns_latest_snapshot(self) -> None:
        with patch.object(web_ui._display_bridge, "latest", return_value={"mode": "idle", "version": 1}):
            result = __import__("asyncio").run(web_ui.display_state())
        self.assertEqual(result, {"mode": "idle", "version": 1})


class TestKaiQuery(unittest.TestCase):
    def test_query_kai_uses_local_backend_when_remote_disabled(self) -> None:
        # Default LLM_BACKEND is "ollama" — should still use send_to_backend(content).
        with patch.object(web_ui.config, "REMOTE_ENABLED", False), \
             patch.object(web_ui.config, "LLM_BACKEND", "ollama"), \
             patch.object(web_ui, "send_to_backend", return_value="local reply") as send_to_backend:
            result = web_ui._query_kai("user-1", "User", "hello")
        self.assertEqual(result, "local reply")
        send_to_backend.assert_called_once_with("hello")

    def test_query_kai_llamacpp_uses_agentic_path(self) -> None:
        with patch.object(web_ui.config, "REMOTE_ENABLED", False), \
             patch.object(web_ui.config, "LLM_BACKEND", "llamacpp"), \
             patch.object(web_ui, "chat_with_tools_llamacpp", return_value="kai reply") as agentic, \
             patch.object(web_ui._web_memory, "synthesize_personality_context", return_value=""), \
             patch.object(web_ui._web_neuro, "emotion_snapshot", return_value={"label": "calm"}):
            result = web_ui._query_kai("user-2", "User", "hi")
        self.assertEqual(result, "kai reply")
        agentic.assert_called_once()
        # Verify that tools and a system prompt are passed.
        call_kwargs = agentic.call_args.kwargs
        self.assertIn("tools", call_kwargs)
        self.assertIn("system", call_kwargs)
        self.assertIn("tool_executor", call_kwargs)

    def test_query_kai_llamacpp_returns_fallback_on_runtime_error(self) -> None:
        with patch.object(web_ui.config, "REMOTE_ENABLED", False), \
             patch.object(web_ui.config, "LLM_BACKEND", "llamacpp"), \
             patch.object(web_ui, "chat_with_tools_llamacpp", side_effect=RuntimeError("offline")), \
             patch.object(web_ui._web_memory, "synthesize_personality_context", return_value=""), \
             patch.object(web_ui._web_neuro, "emotion_snapshot", return_value={"label": "neutral"}):
            result = web_ui._query_kai("user-3", "User", "hello")
        self.assertIn("unavailable", result.lower())


class TestSeedBelief(unittest.TestCase):
    def test_seed_belief_rejects_empty_fields(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            __import__("asyncio").run(
                web_ui.seed_belief({"block_id": " ", "content": "x"})
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_seed_belief_stores_core_memory(self) -> None:
        with patch.object(web_ui._web_memory, "store_core_belief") as store:
            result = __import__("asyncio").run(
                web_ui.seed_belief({"block_id": "identity", "content": "Protect the user."})
            )
        store.assert_called_once_with("identity", "Protect the user.")
        self.assertEqual(result, {"status": "ok"})


class TestPasscodeHashing(unittest.TestCase):
    """Use minimal scrypt parameters so these tests run in any memory environment."""

    def setUp(self) -> None:
        # N=2 (minimum valid) keeps memory usage negligible in tests.
        self._patches = [
            patch.object(web_ui, "_SCRYPT_N", 2),
            patch.object(web_ui, "_SCRYPT_R", 1),
            patch.object(web_ui, "_SCRYPT_P", 1),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()

    def test_hash_returns_salt_dollar_hash_format(self) -> None:
        stored = web_ui._hash_passcode("secret")
        self.assertIn("$", stored)
        salt_hex, hash_hex = stored.split("$", 1)
        # 16 bytes of salt → 32 hex chars
        self.assertEqual(len(salt_hex), 32)
        # 32 bytes of key → 64 hex chars
        self.assertEqual(len(hash_hex), 64)

    def test_correct_passcode_verifies(self) -> None:
        stored = web_ui._hash_passcode("mypassword")
        self.assertTrue(web_ui._verify_passcode("mypassword", stored))

    def test_wrong_passcode_does_not_verify(self) -> None:
        stored = web_ui._hash_passcode("correct")
        self.assertFalse(web_ui._verify_passcode("wrong", stored))

    def test_empty_passcode_hashes_and_verifies(self) -> None:
        stored = web_ui._hash_passcode("")
        self.assertTrue(web_ui._verify_passcode("", stored))

    def test_verify_returns_false_for_malformed_stored_value(self) -> None:
        self.assertFalse(web_ui._verify_passcode("pass", "no-dollar-sign-here"))

    def test_two_hashes_of_same_passcode_differ_due_to_salt(self) -> None:
        h1 = web_ui._hash_passcode("same")
        h2 = web_ui._hash_passcode("same")
        self.assertNotEqual(h1, h2)


class TestPersistentIdentity(unittest.TestCase):
    """Tests for _lookup_or_create_identity using an isolated in-memory DB."""

    def setUp(self) -> None:
        # Redirect the chat DB to a fresh temporary file for each test.
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._patches = [
            patch.object(web_ui, "_CHAT_DB_PATH", Path(self._tmp.name)),
            patch.object(web_ui, "_SCRYPT_N", 2),
            patch.object(web_ui, "_SCRYPT_R", 1),
            patch.object(web_ui, "_SCRYPT_P", 1),
        ]
        for p in self._patches:
            p.start()
        web_ui._init_chat_db()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.close()

    def test_first_call_creates_new_identity_no_passcode(self) -> None:
        tid, ok, err = web_ui._lookup_or_create_identity("Maple", "")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        self.assertTrue(tid.startswith("t_"))

    def test_same_name_returns_same_thread_id(self) -> None:
        tid1, _, _ = web_ui._lookup_or_create_identity("Alice", "")
        tid2, _, _ = web_ui._lookup_or_create_identity("Alice", "")
        self.assertEqual(tid1, tid2)

    def test_name_normalisation_treats_case_and_whitespace_as_equal(self) -> None:
        tid1, _, _ = web_ui._lookup_or_create_identity("  BOB  ", "")
        tid2, _, _ = web_ui._lookup_or_create_identity("bob", "")
        self.assertEqual(tid1, tid2)

    def test_empty_name_is_rejected(self) -> None:
        _, ok, err = web_ui._lookup_or_create_identity("", "")
        self.assertFalse(ok)
        self.assertIn("empty", err.lower())

    def test_whitespace_only_name_is_rejected(self) -> None:
        _, ok, err = web_ui._lookup_or_create_identity("   ", "")
        self.assertFalse(ok)
        self.assertFalse(ok)

    def test_correct_passcode_grants_access_on_return_visit(self) -> None:
        web_ui._lookup_or_create_identity("Kai", "hunter2")
        tid, ok, err = web_ui._lookup_or_create_identity("Kai", "hunter2")
        self.assertTrue(ok)
        self.assertFalse(err)

    def test_wrong_passcode_is_rejected(self) -> None:
        web_ui._lookup_or_create_identity("Kai", "hunter2")
        _, ok, err = web_ui._lookup_or_create_identity("Kai", "wrongpass")
        self.assertFalse(ok)
        self.assertIn("passcode", err.lower())

    def test_no_passcode_set_allows_any_login(self) -> None:
        """When a name is registered without a passcode anyone can use it."""
        web_ui._lookup_or_create_identity("Open", "")
        _, ok, _ = web_ui._lookup_or_create_identity("Open", "anything")
        self.assertTrue(ok)

    def test_different_names_get_different_thread_ids(self) -> None:
        tid1, _, _ = web_ui._lookup_or_create_identity("User1", "")
        tid2, _, _ = web_ui._lookup_or_create_identity("User2", "")
        self.assertNotEqual(tid1, tid2)


class TestAuthJoinEndpoint(unittest.TestCase):
    """Integration tests for the /api/auth/join HTTP endpoint."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._patches = [
            patch.object(web_ui, "_CHAT_DB_PATH", Path(self._tmp.name)),
            patch.object(web_ui, "_SCRYPT_N", 2),
            patch.object(web_ui, "_SCRYPT_R", 1),
            patch.object(web_ui, "_SCRYPT_P", 1),
        ]
        for p in self._patches:
            p.start()
        web_ui._init_chat_db()

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        self._tmp.close()

    def test_valid_join_returns_thread_id(self) -> None:
        result = asyncio.run(web_ui.auth_join({"display_name": "Maple", "passcode": ""}))
        self.assertIn("thread_id", result)
        self.assertTrue(result["thread_id"].startswith("t_"))

    def test_empty_display_name_raises_422(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(web_ui.auth_join({"display_name": "", "passcode": ""}))
        self.assertEqual(ctx.exception.status_code, 422)

    def test_wrong_passcode_raises_401(self) -> None:
        asyncio.run(web_ui.auth_join({"display_name": "Secure", "passcode": "correct"}))
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(web_ui.auth_join({"display_name": "Secure", "passcode": "wrong"}))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_idempotent_join_returns_same_thread_id(self) -> None:
        r1 = asyncio.run(web_ui.auth_join({"display_name": "Same", "passcode": ""}))
        r2 = asyncio.run(web_ui.auth_join({"display_name": "Same", "passcode": ""}))
        self.assertEqual(r1["thread_id"], r2["thread_id"])
