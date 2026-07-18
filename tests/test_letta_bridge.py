"""
tests/test_letta_bridge.py — Unit tests for skills/letta_bridge.py

Uses unittest.mock to simulate the Letta HTTP server so no real server is
required.  Validates all five integration use-cases:
  1. Archival memory (store_archival, search_archival)
  2. User profiles   (update_human_block, get_human_block)
  3. Long-context    (document-only; handled by llm_backends — not tested here)
  4. Tool registration (register_skill_tools)
  5. Dream consolidation (send_dream_message)

Also tests:
  * ensure_agent (find existing / create new)
  * is_available (health endpoint)
  * build_letta_bridge returns None when LETTA_ENABLED=False
  * All methods fail gracefully on HTTP errors
"""

import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from skills.letta_bridge import LettaBridge, build_letta_bridge


def _make_bridge(agent_id: str = "agent-123") -> LettaBridge:
    """Return a LettaBridge with a pre-configured agent_id."""
    return LettaBridge(
        base_url="http://localhost:8283",
        token="test-token",
        agent_id=agent_id,
    )


def _mock_response(status: int = 200, json_data=None) -> MagicMock:
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    if status >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestIsAvailable(unittest.TestCase):
    def test_available_when_health_200(self) -> None:
        bridge = _make_bridge()
        bridge._session.get = MagicMock(return_value=_mock_response(200))
        self.assertTrue(bridge.is_available())

    def test_unavailable_when_no_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        self.assertFalse(bridge.is_available())

    def test_unavailable_on_connection_error(self) -> None:
        bridge = _make_bridge()
        bridge._session.get = MagicMock(side_effect=ConnectionError("refused"))
        self.assertFalse(bridge.is_available())

    def test_unavailable_when_server_returns_500(self) -> None:
        bridge = _make_bridge()
        bridge._session.get = MagicMock(return_value=_mock_response(500))
        self.assertFalse(bridge.is_available())


class TestEnsureAgent(unittest.TestCase):
    def test_returns_existing_id_when_already_set(self) -> None:
        bridge = _make_bridge("pre-set-id")
        result = bridge.ensure_agent()
        self.assertEqual(result, "pre-set-id")

    def test_finds_existing_agent_by_name(self) -> None:
        bridge = _make_bridge(agent_id="")
        agents = [{"id": "found-id", "name": "kai"}]
        bridge._session.get = MagicMock(return_value=_mock_response(200, agents))
        result = bridge.ensure_agent(name="kai")
        self.assertEqual(result, "found-id")
        self.assertEqual(bridge.agent_id, "found-id")

    def test_creates_new_agent_when_none_found(self) -> None:
        bridge = _make_bridge(agent_id="")
        # GET /v1/agents returns empty list
        bridge._session.get = MagicMock(return_value=_mock_response(200, []))
        # POST /v1/agents returns new agent
        bridge._session.post = MagicMock(
            return_value=_mock_response(200, {"id": "new-agent"})
        )
        result = bridge.ensure_agent(name="kai")
        self.assertEqual(result, "new-agent")

    def test_returns_empty_string_on_failure(self) -> None:
        bridge = _make_bridge(agent_id="")
        bridge._session.get = MagicMock(side_effect=Exception("network error"))
        bridge._session.post = MagicMock(side_effect=Exception("network error"))
        result = bridge.ensure_agent()
        self.assertEqual(result, "")


class TestStoreArchival(unittest.TestCase):
    def test_store_returns_true_on_success(self) -> None:
        bridge = _make_bridge()
        bridge._session.post = MagicMock(return_value=_mock_response(200, [{"id": "mem-1"}]))
        ok = bridge.store_archival(
            content="A calm moment",
            category="conversation",
            emotion_label="calm_analytical",
            pad=(0.1, 0.2, 0.8),
            memory_type="episodic",
        )
        self.assertTrue(ok)

    def test_store_includes_metadata_header(self) -> None:
        bridge = _make_bridge()
        captured: list[dict] = []

        def fake_post(url, json=None, timeout=None):
            captured.append(json or {})
            return _mock_response(200, [])

        bridge._session.post = fake_post
        bridge.store_archival(
            content="test content",
            emotion_label="calm_analytical",
            pad=(0.1, 0.2, 0.8),
            memory_type="key",
        )
        self.assertTrue(len(captured) > 0)
        text = captured[0].get("text", "")
        self.assertIn("[emotion:calm_analytical]", text)
        self.assertIn("[type:key]", text)
        self.assertIn("test content", text)

    def test_store_returns_false_on_http_error(self) -> None:
        bridge = _make_bridge()
        bridge._session.post = MagicMock(return_value=_mock_response(500))
        ok = bridge.store_archival("content")
        self.assertFalse(ok)

    def test_store_returns_false_without_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        ok = bridge.store_archival("content")
        self.assertFalse(ok)


class TestSearchArchival(unittest.TestCase):
    def test_returns_parsed_results(self) -> None:
        bridge = _make_bridge()
        raw = [
            {"id": "m1", "text": "[category:chat] [emotion:calm]\nFirst memory"},
            {"id": "m2", "text": "[category:chat]\nSecond memory"},
        ]
        bridge._session.get = MagicMock(return_value=_mock_response(200, raw))
        results = bridge.search_archival("calm", limit=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["source"], "letta")
        self.assertIn("First memory", results[0]["content"])

    def test_strips_metadata_header(self) -> None:
        bridge = _make_bridge()
        raw = [{"id": "m1", "text": "[meta:data]\nclean content here"}]
        bridge._session.get = MagicMock(return_value=_mock_response(200, raw))
        results = bridge.search_archival("test")
        self.assertEqual(results[0]["content"], "clean content here")

    def test_returns_empty_on_error(self) -> None:
        bridge = _make_bridge()
        bridge._session.get = MagicMock(side_effect=Exception("timeout"))
        results = bridge.search_archival("query")
        self.assertEqual(results, [])

    def test_returns_empty_without_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        results = bridge.search_archival("query")
        self.assertEqual(results, [])


class TestHumanBlock(unittest.TestCase):
    def test_update_human_block_returns_true_on_success(self) -> None:
        bridge = _make_bridge()
        bridge._session.patch = MagicMock(return_value=_mock_response(200))
        ok = bridge.update_human_block("User is Alice, prefers concise answers.")
        self.assertTrue(ok)

    def test_update_sends_correct_value(self) -> None:
        bridge = _make_bridge()
        captured: list[dict] = []

        def fake_patch(url, json=None, timeout=None):
            captured.append(json or {})
            return _mock_response(200)

        bridge._session.patch = fake_patch
        bridge.update_human_block("Alice profile")
        self.assertEqual(captured[0]["value"], "Alice profile")

    def test_update_returns_false_on_error(self) -> None:
        bridge = _make_bridge()
        bridge._session.patch = MagicMock(return_value=_mock_response(404))
        ok = bridge.update_human_block("summary")
        self.assertFalse(ok)

    def test_update_returns_false_without_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        ok = bridge.update_human_block("summary")
        self.assertFalse(ok)

    def test_get_human_block_returns_value(self) -> None:
        bridge = _make_bridge()
        memory_data = {"memory": {"human": {"value": "Alice from London"}}}
        bridge._session.get = MagicMock(return_value=_mock_response(200, memory_data))
        result = bridge.get_human_block()
        self.assertEqual(result, "Alice from London")

    def test_get_human_block_handles_list_format(self) -> None:
        bridge = _make_bridge()
        memory_data = {"memory": [{"label": "human", "value": "Bob from Tokyo"}]}
        bridge._session.get = MagicMock(return_value=_mock_response(200, memory_data))
        result = bridge.get_human_block()
        self.assertEqual(result, "Bob from Tokyo")

    def test_get_human_block_returns_empty_on_error(self) -> None:
        bridge = _make_bridge()
        bridge._session.get = MagicMock(side_effect=Exception("unreachable"))
        result = bridge.get_human_block()
        self.assertEqual(result, "")

    def test_get_human_block_returns_empty_without_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        result = bridge.get_human_block()
        self.assertEqual(result, "")


class TestRegisterSkillTools(unittest.TestCase):
    def test_registers_and_attaches_tools(self) -> None:
        bridge = _make_bridge()
        # GET /v1/tools → empty list (no existing tools)
        bridge._session.get = MagicMock(return_value=_mock_response(200, []))
        # POST /v1/tools → success for each tool
        bridge._session.post = MagicMock(return_value=_mock_response(200, {"id": "t1"}))
        # PATCH /v1/agents/{id} → success
        bridge._session.patch = MagicMock(return_value=_mock_response(200))

        ok = bridge.register_skill_tools()
        self.assertTrue(ok)

    def test_skips_already_registered_tools(self) -> None:
        bridge = _make_bridge()
        existing = [
            {"name": "kitezh_search_memory"},
            {"name": "kitezh_read_workspace_file"},
            {"name": "kitezh_write_workspace_file"},
        ]
        bridge._session.get = MagicMock(return_value=_mock_response(200, existing))
        post_mock = MagicMock(return_value=_mock_response(200))
        bridge._session.post = post_mock
        bridge._session.patch = MagicMock(return_value=_mock_response(200))

        bridge.register_skill_tools()
        # POST should NOT have been called for tools since all exist
        post_mock.assert_not_called()

    def test_returns_false_without_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        ok = bridge.register_skill_tools()
        self.assertFalse(ok)

    def test_returns_false_when_attach_fails(self) -> None:
        bridge = _make_bridge()
        bridge._session.get = MagicMock(return_value=_mock_response(200, []))
        bridge._session.post = MagicMock(return_value=_mock_response(200, {"id": "t1"}))
        bridge._session.patch = MagicMock(return_value=_mock_response(500))
        ok = bridge.register_skill_tools()
        self.assertFalse(ok)


class TestSendDreamMessage(unittest.TestCase):
    def test_returns_true_on_success(self) -> None:
        bridge = _make_bridge()
        bridge._session.post = MagicMock(return_value=_mock_response(200, {"messages": []}))
        ok = bridge.send_dream_message("=== K.A.I. IDENTITY CONTEXT ===\n...")
        self.assertTrue(ok)

    def test_dream_message_contains_context(self) -> None:
        bridge = _make_bridge()
        captured: list[dict] = []

        def fake_post(url, json=None, timeout=None):
            captured.append(json or {})
            return _mock_response(200)

        bridge._session.post = fake_post
        bridge.send_dream_message("identity context here")
        text = captured[0]["messages"][0]["content"]
        self.assertIn("identity context here", text)

    def test_returns_false_on_error(self) -> None:
        bridge = _make_bridge()
        bridge._session.post = MagicMock(return_value=_mock_response(500))
        ok = bridge.send_dream_message("context")
        self.assertFalse(ok)

    def test_returns_false_without_agent_id(self) -> None:
        bridge = _make_bridge(agent_id="")
        ok = bridge.send_dream_message("context")
        self.assertFalse(ok)


class TestBuildLettaBridge(unittest.TestCase):
    def test_returns_none_when_disabled(self) -> None:
        with patch("config.LETTA_ENABLED", False):
            bridge = build_letta_bridge()
        self.assertIsNone(bridge)

    def test_returns_none_when_server_unreachable(self) -> None:
        with (
            patch("config.LETTA_ENABLED", True),
            patch("config.LETTA_BASE_URL", "http://localhost:8283"),
            patch("config.LETTA_TOKEN", ""),
            patch("config.LETTA_AGENT_ID", "agent-99"),
        ):
            with patch.object(LettaBridge, "ensure_agent", return_value="agent-99"), \
                 patch.object(LettaBridge, "is_available", return_value=False):
                bridge = build_letta_bridge()
        self.assertIsNone(bridge)

    def test_returns_bridge_when_available(self) -> None:
        with (
            patch("config.LETTA_ENABLED", True),
            patch("config.LETTA_BASE_URL", "http://localhost:8283"),
            patch("config.LETTA_TOKEN", ""),
            patch("config.LETTA_AGENT_ID", "agent-99"),
        ):
            with patch.object(LettaBridge, "ensure_agent", return_value="agent-99"), \
                 patch.object(LettaBridge, "is_available", return_value=True), \
                 patch.object(LettaBridge, "register_skill_tools", return_value=True):
                bridge = build_letta_bridge()
        self.assertIsNotNone(bridge)


if __name__ == "__main__":
    unittest.main()
