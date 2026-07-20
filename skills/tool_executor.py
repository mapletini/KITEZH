"""
skills/tool_executor.py — Tool definitions and execution for K.A.I.'s agentic loop.

Defines the workspace and memory tools that Kai can call during a response,
and provides a factory that returns an executor bound to live memory/neuro
instances.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI-compatible tool definitions
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_runtime_status",
            "description": (
                "Report K.A.I.'s current runtime mode, active backend, live subsystems, "
                "and the exact actions available right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workspace_file",
            "description": (
                "Read the text content of a file from Kai's workspace sandbox. "
                "Use this to check notes, code, or other files Kai has saved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace (e.g. 'notes/todo.txt').",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_workspace_file",
            "description": (
                "Write or overwrite a file in Kai's workspace sandbox. "
                "Use this to save notes, drafts, code, or any persistent content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path within the workspace.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text content to write to the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": "List files in Kai's workspace sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter files (default: '**/*').",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memories",
            "description": (
                "Recall Kai's episodic memories ranked by emotional resonance with the "
                "current mood. Returns recent experiences that feel most salient right now."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return (default: 5).",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "store_note",
            "description": (
                "Save a short note or observation to Kai's workspace for later reference. "
                "Useful for recording thoughts, reminders, or information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The note to save.",
                    },
                    "filename": {
                        "type": "string",
                        "description": (
                            "Optional filename inside the notes/ directory "
                            "(default: auto-generated from timestamp)."
                        ),
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cameras",
            "description": (
                "List known Tapo cameras and wakeword availability. "
                "Use this before claiming live camera access."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_camera_snapshot",
            "description": (
                "Capture a live snapshot from a named camera when camera access is available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "camera_name": {
                        "type": "string",
                        "description": "The configured camera name to capture from.",
                    }
                },
                "required": ["camera_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_display_state",
            "description": "Return the latest shared display/face state being published by K.A.I.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool executor factory
# ---------------------------------------------------------------------------


def make_tool_executor(
    memory: Any | None = None,
    neuro: Any | None = None,
    awareness_provider: Callable[[], dict[str, Any]] | None = None,
    tapo_hub: Any | None = None,
    display_bridge: Any | None = None,
) -> Callable[[str, dict[str, Any]], str]:
    """
    Return a tool executor function bound to the given memory and neuro instances.

    Parameters
    ----------
    memory:
        A :class:`~skills.deep_memory.DeepMemoryCore` instance (optional).
        Required for ``recall_memories``; other tools work without it.
    neuro:
        A :class:`~skills.neuro_affect.NeuroChemicalEngine` instance (optional).
        Used to colour memory recall by current emotional state.
    """
    from skills.filesystem import (
        SandboxViolationError,
        WorkspaceError,
        WorkspaceReader,
        WorkspaceWriter,
    )

    def execute_tool(name: str, arguments: dict[str, Any]) -> str:
        logger.info("K.A.I. tool call: %s(%s)", name, arguments)

        # ── get_runtime_status ────────────────────────────────────────────────
        if name == "get_runtime_status":
            if awareness_provider is None:
                return "Runtime awareness unavailable."
            try:
                return json.dumps(awareness_provider(), ensure_ascii=False, indent=2)
            except Exception as exc:
                return f"Error reading runtime status: {exc}"

        # ── read_workspace_file ──────────────────────────────────────────────
        if name == "read_workspace_file":
            path = arguments.get("path", "").strip()
            if not path:
                return "Error: 'path' argument is required."
            reader = WorkspaceReader()
            try:
                return reader.read_text(path)
            except SandboxViolationError:
                return f"Error: path '{path}' escapes the workspace sandbox."
            except WorkspaceError as exc:
                return f"Error reading '{path}': {exc}"

        # ── write_workspace_file ─────────────────────────────────────────────
        if name == "write_workspace_file":
            path = arguments.get("path", "").strip()
            content = arguments.get("content", "")
            if not path:
                return "Error: 'path' argument is required."
            writer = WorkspaceWriter()
            try:
                writer.write_text(path, content)
                return f"File '{path}' written successfully."
            except SandboxViolationError:
                return f"Error: path '{path}' escapes the workspace sandbox."
            except WorkspaceError as exc:
                return f"Error writing '{path}': {exc}"

        # ── list_workspace_files ─────────────────────────────────────────────
        if name == "list_workspace_files":
            pattern = arguments.get("pattern", "**/*") or "**/*"
            reader = WorkspaceReader()
            try:
                files = reader.list_files(pattern)
                if not files:
                    return "No files found."
                return "\n".join(str(f) for f in sorted(files))
            except Exception as exc:
                return f"Error listing files: {exc}"

        # ── recall_memories ──────────────────────────────────────────────────
        if name == "recall_memories":
            if memory is None:
                return "Memory system unavailable."
            limit = max(1, min(20, int(arguments.get("limit", 5))))
            pad = neuro.get_pad_coordinates() if neuro is not None else (0.0, 0.0, 0.0)
            try:
                results = memory.search_by_resonance(*pad, limit=limit)
                if not results:
                    return "No relevant memories found."
                lines: list[str] = []
                for mem in results:
                    fidelity = float(mem.get("fidelity", 1.0))
                    category = mem.get("category", "memory")
                    label = mem.get("complex_label", "unknown")
                    tag = f"[{category} / {label} / {fidelity:.0%} fidelity]"
                    lines.append(f"{tag} {mem['content']}")
                return "\n".join(lines)
            except Exception as exc:
                return f"Error recalling memories: {exc}"

        # ── store_note ───────────────────────────────────────────────────────
        if name == "store_note":
            content = arguments.get("content", "").strip()
            if not content:
                return "Error: 'content' argument is required."
            raw_filename = (arguments.get("filename") or "").strip()
            if raw_filename:
                filename = raw_filename if raw_filename.startswith("notes/") else f"notes/{raw_filename}"
            else:
                filename = f"notes/note_{time.time_ns()}.txt"
            writer = WorkspaceWriter()
            try:
                writer.write_text(filename, content)
                return f"Note saved to '{filename}'."
            except Exception as exc:
                return f"Error saving note: {exc}"

        # ── list_cameras ─────────────────────────────────────────────────────
        if name == "list_cameras":
            if tapo_hub is None:
                return "Camera subsystem unavailable."
            try:
                status = tapo_hub.status()
                cameras = tapo_hub.list_cameras()
                return json.dumps({"status": status, "cameras": cameras}, ensure_ascii=False, indent=2)
            except Exception as exc:
                return f"Error listing cameras: {exc}"

        # ── capture_camera_snapshot ──────────────────────────────────────────
        if name == "capture_camera_snapshot":
            if tapo_hub is None:
                return "Camera subsystem unavailable."
            camera_name = str(arguments.get("camera_name", "")).strip()
            if not camera_name:
                return "Error: 'camera_name' argument is required."
            try:
                snapshot_path = tapo_hub.capture_snapshot(camera_name)
                if not snapshot_path:
                    return f"Unable to capture a snapshot from '{camera_name}'."
                return f"Snapshot saved to '{snapshot_path}'."
            except Exception as exc:
                return f"Error capturing snapshot: {exc}"

        # ── get_display_state ────────────────────────────────────────────────
        if name == "get_display_state":
            if display_bridge is None:
                return "Display subsystem unavailable."
            try:
                return json.dumps(display_bridge.latest(), ensure_ascii=False, indent=2)
            except Exception as exc:
                return f"Error reading display state: {exc}"

        return f"Unknown tool: '{name}'."

    return execute_tool
