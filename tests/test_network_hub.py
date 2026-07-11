import unittest
from unittest.mock import Mock, patch

from requests.exceptions import ConnectionError, ReadTimeout

import network_hub


class TestNamespaceRouter(unittest.TestCase):
    def setUp(self) -> None:
        network_hub._ADMIN_USER_IDS.clear()
        network_hub._puppy_cycle_index = 0

    def test_namespace_router_assigns_admin_clearance(self) -> None:
        network_hub.register_admin("admin-1")
        payload = network_hub.namespace_router(
            platform="cli",
            user_id="admin-1",
            display_name="Admin",
            content="hello",
        )
        self.assertEqual(payload.clearance, "admin")
        self.assertFalse(payload.is_puppy)

    def test_namespace_router_assigns_guest_clearance(self) -> None:
        payload = network_hub.namespace_router(
            platform="cli",
            user_id="guest-1",
            display_name="Guest",
            content="hello",
        )
        self.assertEqual(payload.clearance, "guest")
        self.assertFalse(payload.is_puppy)

    def test_namespace_router_applies_puppy_trap(self) -> None:
        with patch.object(network_hub.config, "DISCORD_PUPPY_ID", "puppy"):
            payload = network_hub.namespace_router(
                platform="web",
                user_id="puppy",
                display_name="Pup",
                content="hey",
            )
        self.assertTrue(payload.is_puppy)
        self.assertEqual(payload.metadata["original_content"], "hey")
        self.assertTrue(payload.metadata["puppy_trap"])
        self.assertNotEqual(payload.content, "hey")


class TestBridgeFailures(unittest.TestCase):
    def test_bridge_warns_on_insecure_key(self) -> None:
        with self.assertLogs("network_hub", level="WARNING") as captured:
            network_hub.RemoteMochiiBridge(ai_key="changeme")
        self.assertTrue(any("insecure default AI key" in msg for msg in captured.output))

    def test_query_context_returns_timeout_error(self) -> None:
        bridge = network_hub.RemoteMochiiBridge(base_url="http://localhost:9999", ai_key="test")
        with patch.object(bridge, "_post", side_effect=ReadTimeout()):
            result = bridge.query_context(
                network_hub.UserPayload("cli", "u1", "User", "hello")
            )
        self.assertFalse(result.success)
        self.assertIn("timed out", result.error or "")

    def test_query_context_returns_connection_error(self) -> None:
        bridge = network_hub.RemoteMochiiBridge(base_url="http://localhost:9999", ai_key="test")
        with patch.object(bridge, "_post", side_effect=ConnectionError("down")):
            result = bridge.query_context(
                network_hub.UserPayload("cli", "u1", "User", "hello")
            )
        self.assertFalse(result.success)
        self.assertIn("Could not connect", result.error or "")

    def test_query_context_handles_invalid_json(self) -> None:
        bridge = network_hub.RemoteMochiiBridge(base_url="http://localhost:9999", ai_key="test")
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.side_effect = ValueError("bad json")
        with patch.object(bridge, "_post", return_value=fake_response):
            result = bridge.query_context(
                network_hub.UserPayload("cli", "u1", "User", "hello")
            )
        self.assertFalse(result.success)
        self.assertIn("Invalid JSON response", result.error or "")
