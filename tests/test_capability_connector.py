"""Tests for skills/capability_connector.py."""

import importlib.util
import unittest
from unittest.mock import Mock, patch

from skills.capability_connector import (
    CapabilityAuthError,
    CapabilityConnectionError,
    CapabilityConnector,
    CapabilityDisabledError,
    CapabilityResponseError,
    CapabilityTimeoutError,
)


@unittest.skipUnless(importlib.util.find_spec("requests") is not None, "requests package is required")
class TestCapabilityConnector(unittest.TestCase):
    def test_lookup_knowledge_requires_enabled(self) -> None:
        connector = CapabilityConnector(
            enabled=False,
            base_url="",
            token="",
            knowledge_path="/api/knowledge/retrieve",
            timeout=10.0,
        )
        with self.assertRaises(CapabilityDisabledError):
            connector.lookup_knowledge(query="hello")

    def test_lookup_knowledge_normalizes_results(self) -> None:
        connector = CapabilityConnector(
            enabled=True,
            base_url="http://localhost:9999",
            token="abc",
            knowledge_path="/api/knowledge/retrieve",
            timeout=10.0,
        )
        fake = Mock()
        fake.status_code = 200
        fake.raise_for_status.return_value = None
        fake.json.return_value = {
            "results": [
                {"title": "Doc", "content": "Text", "score": 0.91, "source": "test"}
            ]
        }
        with patch.object(connector._session, "post", return_value=fake):
            result = connector.lookup_knowledge(query="kai", top_k=3)
        self.assertEqual(result["query"], "kai")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["title"], "Doc")

    def test_lookup_knowledge_auth_error(self) -> None:
        connector = CapabilityConnector(
            enabled=True,
            base_url="http://localhost:9999",
            token="abc",
            knowledge_path="/api/knowledge/retrieve",
            timeout=10.0,
        )
        fake = Mock()
        fake.status_code = 401
        with patch.object(connector._session, "post", return_value=fake):
            with self.assertRaises(CapabilityAuthError):
                connector.lookup_knowledge(query="kai")

    def test_lookup_knowledge_timeout_error(self) -> None:
        connector = CapabilityConnector(
            enabled=True,
            base_url="http://localhost:9999",
            token="abc",
            knowledge_path="/api/knowledge/retrieve",
            timeout=10.0,
        )
        read_timeout_exc = __import__("requests").exceptions.ReadTimeout()
        with patch.object(connector._session, "post", side_effect=read_timeout_exc):
            with self.assertRaises(CapabilityTimeoutError):
                connector.lookup_knowledge(query="kai")

    def test_lookup_knowledge_connection_error(self) -> None:
        connector = CapabilityConnector(
            enabled=True,
            base_url="http://localhost:9999",
            token="abc",
            knowledge_path="/api/knowledge/retrieve",
            timeout=10.0,
        )
        conn_exc = __import__("requests").exceptions.ConnectionError("down")
        with patch.object(connector._session, "post", side_effect=conn_exc):
            with self.assertRaises(CapabilityConnectionError):
                connector.lookup_knowledge(query="kai")

    def test_lookup_knowledge_invalid_json(self) -> None:
        connector = CapabilityConnector(
            enabled=True,
            base_url="http://localhost:9999",
            token="abc",
            knowledge_path="/api/knowledge/retrieve",
            timeout=10.0,
        )
        fake = Mock()
        fake.status_code = 200
        fake.raise_for_status.return_value = None
        fake.json.side_effect = ValueError("bad")
        with patch.object(connector._session, "post", return_value=fake):
            with self.assertRaises(CapabilityResponseError):
                connector.lookup_knowledge(query="kai")


if __name__ == "__main__":
    unittest.main()
