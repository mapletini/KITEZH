"""Tests for skills/tool_executor.py."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestMakeToolExecutor(unittest.TestCase):
    """Tests for the make_tool_executor factory and individual tool handlers."""

    def setUp(self) -> None:
        # Use a real temp dir as the workspace so filesystem tools actually work.
        self._tmp = tempfile.TemporaryDirectory()
        self._workspace = self._tmp.name

        # Patch config.WORKSPACE_PATH so WorkspaceReader/Writer use our temp dir.
        self._patcher = patch("config.WORKSPACE_PATH", self._workspace)
        self._patcher.start()

        from skills.tool_executor import make_tool_executor
        self.executor = make_tool_executor()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmp.cleanup()

    # ── write_workspace_file ─────────────────────────────────────────────────

    def test_write_workspace_file_creates_file(self) -> None:
        result = self.executor("write_workspace_file", {"path": "hello.txt", "content": "world"})
        self.assertIn("hello.txt", result)
        self.assertIn("written", result.lower())
        self.assertEqual(Path(self._workspace, "hello.txt").read_text(), "world")

    def test_write_workspace_file_missing_path_returns_error(self) -> None:
        result = self.executor("write_workspace_file", {"content": "oops"})
        self.assertIn("Error", result)

    def test_write_workspace_file_escaping_sandbox_returns_error(self) -> None:
        result = self.executor("write_workspace_file", {"path": "../../etc/passwd", "content": "x"})
        self.assertIn("Error", result)

    # ── read_workspace_file ──────────────────────────────────────────────────

    def test_read_workspace_file_returns_content(self) -> None:
        Path(self._workspace, "note.txt").write_text("hello")
        result = self.executor("read_workspace_file", {"path": "note.txt"})
        self.assertEqual(result, "hello")

    def test_read_workspace_file_missing_file_returns_error(self) -> None:
        result = self.executor("read_workspace_file", {"path": "nonexistent.txt"})
        self.assertIn("Error", result)

    def test_read_workspace_file_missing_path_arg_returns_error(self) -> None:
        result = self.executor("read_workspace_file", {})
        self.assertIn("Error", result)

    def test_read_workspace_file_escaping_sandbox_returns_error(self) -> None:
        result = self.executor("read_workspace_file", {"path": "../../etc/passwd"})
        self.assertIn("Error", result)

    # ── list_workspace_files ─────────────────────────────────────────────────

    def test_list_workspace_files_returns_paths(self) -> None:
        Path(self._workspace, "a.txt").write_text("a")
        Path(self._workspace, "b.txt").write_text("b")
        result = self.executor("list_workspace_files", {})
        self.assertIn("a.txt", result)
        self.assertIn("b.txt", result)

    def test_list_workspace_files_empty_workspace_says_no_files(self) -> None:
        result = self.executor("list_workspace_files", {})
        self.assertEqual(result, "No files found.")

    # ── recall_memories ──────────────────────────────────────────────────────

    def test_recall_memories_without_memory_returns_unavailable(self) -> None:
        result = self.executor("recall_memories", {})
        self.assertIn("unavailable", result.lower())

    def test_recall_memories_with_mock_memory(self) -> None:
        mock_memory = MagicMock()
        mock_memory.search_by_resonance.return_value = [
            {
                "category": "test",
                "complex_label": "calm_analytical",
                "fidelity": 0.9,
                "content": "A nice memory.",
            }
        ]
        mock_neuro = MagicMock()
        mock_neuro.get_pad_coordinates.return_value = (0.1, 0.2, 0.7)

        from skills.tool_executor import make_tool_executor
        executor = make_tool_executor(memory=mock_memory, neuro=mock_neuro)
        result = executor("recall_memories", {"limit": 3})
        self.assertIn("A nice memory.", result)

    def test_recall_memories_empty_returns_message(self) -> None:
        mock_memory = MagicMock()
        mock_memory.search_by_resonance.return_value = []
        from skills.tool_executor import make_tool_executor
        executor = make_tool_executor(memory=mock_memory)
        result = executor("recall_memories", {})
        self.assertIn("No relevant memories", result)

    # ── store_note ───────────────────────────────────────────────────────────

    def test_store_note_saves_file(self) -> None:
        result = self.executor("store_note", {"content": "Remember this.", "filename": "reminder.txt"})
        self.assertIn("notes/reminder.txt", result)
        note_path = Path(self._workspace, "notes", "reminder.txt")
        self.assertTrue(note_path.exists())
        self.assertEqual(note_path.read_text(), "Remember this.")

    def test_store_note_auto_filename(self) -> None:
        result = self.executor("store_note", {"content": "Auto-named note."})
        self.assertIn("notes/note_", result)

    def test_store_note_missing_content_returns_error(self) -> None:
        result = self.executor("store_note", {})
        self.assertIn("Error", result)

    # ── runtime awareness / device tools ─────────────────────────────────────

    def test_get_runtime_status_returns_json(self) -> None:
        from skills.tool_executor import make_tool_executor

        executor = make_tool_executor(
            awareness_provider=lambda: {"interface": "web", "tools_available": ["get_runtime_status"]}
        )
        result = executor("get_runtime_status", {})
        self.assertIn('"interface": "web"', result)

    def test_list_cameras_returns_status_and_cameras(self) -> None:
        tapo_hub = MagicMock()
        tapo_hub.status.return_value = {"available": True, "camera_count": 1}
        tapo_hub.list_cameras.return_value = [{"name": "front_door"}]

        from skills.tool_executor import make_tool_executor

        executor = make_tool_executor(tapo_hub=tapo_hub)
        result = executor("list_cameras", {})
        self.assertIn("front_door", result)
        self.assertIn('"camera_count": 1', result)

    def test_get_display_state_returns_json(self) -> None:
        display_bridge = MagicMock()
        display_bridge.latest.return_value = {"mode": "idle", "version": 2}

        from skills.tool_executor import make_tool_executor

        executor = make_tool_executor(display_bridge=display_bridge)
        result = executor("get_display_state", {})
        self.assertIn('"mode": "idle"', result)

    def test_capture_camera_snapshot_requires_name(self) -> None:
        tapo_hub = MagicMock()

        from skills.tool_executor import make_tool_executor

        executor = make_tool_executor(tapo_hub=tapo_hub)
        result = executor("capture_camera_snapshot", {})
        self.assertIn("camera_name", result)

    # ── unknown tool ─────────────────────────────────────────────────────────

    def test_unknown_tool_returns_error_message(self) -> None:
        result = self.executor("launch_missiles", {})
        self.assertIn("Unknown tool", result)


class TestToolDefinitions(unittest.TestCase):
    def test_all_definitions_have_required_fields(self) -> None:
        from skills.tool_executor import TOOL_DEFINITIONS
        for tool in TOOL_DEFINITIONS:
            self.assertEqual(tool["type"], "function")
            fn = tool["function"]
            self.assertIn("name", fn)
            self.assertIn("description", fn)
            self.assertIn("parameters", fn)

    def test_nine_tools_defined(self) -> None:
        from skills.tool_executor import TOOL_DEFINITIONS
        self.assertEqual(len(TOOL_DEFINITIONS), 9)


if __name__ == "__main__":
    unittest.main()
