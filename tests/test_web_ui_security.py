import unittest
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
