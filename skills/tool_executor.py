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
from urllib.parse import urlparse
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
    {
        "type": "function",
        "function": {
            "name": "set_display_scene",
            "description": (
                "Set the monitor scene target. Modes: 'face' (emotion orb), "
                "'ui' (Kai's editable workspace UI), or 'url' (custom target)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "Display mode: face | ui | url.",
                    },
                    "url": {
                        "type": "string",
                        "description": "Optional URL/path for mode='url'.",
                    },
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reflect_on_memories",
            "description": (
                "Trigger a private memory reflection session. Kai will review a diverse "
                "batch of its memories — old, faded, and significant — and return a "
                "personal reflection on what they mean right now."
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
            "name": "explore_curiosity",
            "description": (
                "Trigger a self-directed curiosity exploration. Kai will identify a gap "
                "in its knowledge, form a question about it, and reason through an answer "
                "using existing memories and associations."
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
            "name": "retrieve_knowledge_context",
            "description": (
                "Fetch optional external knowledge context via the configured "
                "capability connector when available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Knowledge lookup query.",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of records to request (default: 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_discord_message",
            "description": "Queue a Discord channel message for delivery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string", "description": "Discord channel ID."},
                    "content": {"type": "string", "description": "Message content."},
                },
                "required": ["channel_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reply_discord_message",
            "description": "Queue a reply to a specific Discord message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                    "message_id": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["channel_id", "message_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discord_typing_indicator",
            "description": "Send a typing indicator event to a Discord channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                },
                "required": ["channel_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_discord_reaction",
            "description": "Queue a reaction on a Discord message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                    "message_id": {"type": "string"},
                    "emoji": {"type": "string"},
                },
                "required": ["channel_id", "message_id", "emoji"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_discord_recent_messages",
            "description": "Fetch recent messages from a Discord channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["channel_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_discord_member_profile",
            "description": "Fetch profile details for a Discord guild member.",
            "parameters": {
                "type": "object",
                "properties": {
                    "guild_id": {"type": "string"},
                    "user_id": {"type": "string"},
                },
                "required": ["guild_id", "user_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "timeout_discord_member",
            "description": "Apply a moderation timeout to a Discord member (approval token required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "guild_id": {"type": "string"},
                    "user_id": {"type": "string"},
                    "until_iso8601": {"type": "string"},
                    "reason": {"type": "string"},
                    "approval_token": {"type": "string"},
                },
                "required": ["guild_id", "user_id", "until_iso8601", "approval_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_discord_message",
            "description": "Delete a Discord message (approval token required).",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_id": {"type": "string"},
                    "message_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "approval_token": {"type": "string"},
                },
                "required": ["channel_id", "message_id", "approval_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "issue_discord_approval_token",
            "description": "Issue a one-time approval token for privileged Discord moderation actions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {"type": "string"},
                    "actor": {"type": "string"},
                    "ttl_seconds": {"type": "integer"},
                },
                "required": ["action_type", "actor"],
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
    cognitive_bridge: Any | None = None,
    capability_connector: Any | None = None,
    discord_adapter: Any | None = None,
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
    cognitive_bridge:
        A :class:`~skills.cognitive_architect.LLMCognitiveBridge` instance (optional).
        Required for ``reflect_on_memories`` and ``explore_curiosity``.
    """
    from skills.filesystem import (
        SandboxViolationError,
        WorkspaceError,
        WorkspaceReader,
        WorkspaceWriter,
    )
    import config

    def _normalize_display_scene(mode_raw: Any, url_raw: Any) -> tuple[dict[str, str] | None, str | None]:
        mode = str(mode_raw or "").strip().lower()
        if mode not in {"face", "ui", "url"}:
            return None, "Error: 'mode' must be one of: face, ui, url."

        if mode == "face":
            return {"mode": "face", "url": "/face", "updated_by": "kai"}, None
        if mode == "ui":
            return {"mode": "ui", "url": "/", "updated_by": "kai"}, None

        url_text = str(url_raw or "").strip()
        if not url_text:
            return None, "Error: 'url' is required when mode='url'."

        parsed = urlparse(url_text)
        if parsed.scheme or parsed.netloc:
            if not config.DISPLAY_ALLOW_EXTERNAL_URLS:
                return None, (
                    "Error: external URLs are disabled by config "
                    "(set KITEZH_DISPLAY_ALLOW_EXTERNAL_URLS=1 to enable)."
                )
            if parsed.scheme not in {"http", "https"}:
                return None, "Error: only http/https URLs are allowed for external display scenes."
        elif not url_text.startswith("/"):
            return None, "Error: local scene paths must start with '/'."

        return {"mode": "url", "url": url_text, "updated_by": "kai"}, None

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

        # ── set_display_scene ───────────────────────────────────────────────
        if name == "set_display_scene":
            if display_bridge is None:
                return "Display subsystem unavailable."
            scene, error = _normalize_display_scene(arguments.get("mode"), arguments.get("url"))
            if error:
                return error
            try:
                updated = display_bridge.publish({"screen": scene})
                return json.dumps(
                    {
                        "ok": True,
                        "screen": updated.get("screen", scene),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as exc:
                return f"Error updating display scene: {exc}"

        # ── reflect_on_memories ──────────────────────────────────────────────
        if name == "reflect_on_memories":
            if cognitive_bridge is None:
                return "Cognitive bridge unavailable."
            try:
                reflection = cognitive_bridge.run_memory_reflection()
                if not reflection:
                    return "No reflection was produced (no memories or LLM unavailable)."
                return reflection
            except Exception as exc:
                return f"Error during memory reflection: {exc}"

        # ── explore_curiosity ────────────────────────────────────────────────
        if name == "explore_curiosity":
            if cognitive_bridge is None:
                return "Cognitive bridge unavailable."
            try:
                exploration = cognitive_bridge.run_curiosity_loop()
                if not exploration:
                    return "No curiosity exploration was produced (no gaps or LLM unavailable)."
                return exploration
            except Exception as exc:
                return f"Error during curiosity exploration: {exc}"

        # ── retrieve_knowledge_context ──────────────────────────────────────
        if name == "retrieve_knowledge_context":
            if capability_connector is None:
                return "Capability connector unavailable."
            query = str(arguments.get("query", "")).strip()
            if not query:
                return "Error: 'query' argument is required."
            top_k = int(arguments.get("top_k", 5) or 5)
            try:
                result = capability_connector.lookup_knowledge(query=query, top_k=top_k)
                return json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as exc:
                return f"Knowledge retrieval failed: {exc}"

        # ── Discord action tools ────────────────────────────────────────────
        if name == "send_discord_message":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            channel_id = str(arguments.get("channel_id", "")).strip()
            content = str(arguments.get("content", "")).strip()
            if not channel_id or not content:
                return "Error: 'channel_id' and 'content' are required."
            return json.dumps(discord_adapter.send_message(channel_id, content), ensure_ascii=False, indent=2)

        if name == "reply_discord_message":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            channel_id = str(arguments.get("channel_id", "")).strip()
            message_id = str(arguments.get("message_id", "")).strip()
            content = str(arguments.get("content", "")).strip()
            if not channel_id or not message_id or not content:
                return "Error: 'channel_id', 'message_id', and 'content' are required."
            return json.dumps(
                discord_adapter.reply_message(channel_id, message_id, content),
                ensure_ascii=False,
                indent=2,
            )

        if name == "discord_typing_indicator":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            channel_id = str(arguments.get("channel_id", "")).strip()
            if not channel_id:
                return "Error: 'channel_id' is required."
            return json.dumps(discord_adapter.send_typing(channel_id), ensure_ascii=False, indent=2)

        if name == "add_discord_reaction":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            channel_id = str(arguments.get("channel_id", "")).strip()
            message_id = str(arguments.get("message_id", "")).strip()
            emoji = str(arguments.get("emoji", "")).strip()
            if not channel_id or not message_id or not emoji:
                return "Error: 'channel_id', 'message_id', and 'emoji' are required."
            return json.dumps(
                discord_adapter.add_reaction(channel_id, message_id, emoji),
                ensure_ascii=False,
                indent=2,
            )

        if name == "fetch_discord_recent_messages":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            channel_id = str(arguments.get("channel_id", "")).strip()
            limit = int(arguments.get("limit", 0) or 0)
            if not channel_id:
                return "Error: 'channel_id' is required."
            try:
                return json.dumps(
                    discord_adapter.fetch_recent_messages(channel_id, limit=limit if limit > 0 else None),
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as exc:
                return f"Discord fetch failed: {exc}"

        if name == "get_discord_member_profile":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            guild_id = str(arguments.get("guild_id", "")).strip()
            user_id = str(arguments.get("user_id", "")).strip()
            if not guild_id or not user_id:
                return "Error: 'guild_id' and 'user_id' are required."
            try:
                return json.dumps(discord_adapter.get_member_profile(guild_id, user_id), ensure_ascii=False, indent=2)
            except Exception as exc:
                return f"Discord profile lookup failed: {exc}"

        if name == "timeout_discord_member":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            guild_id = str(arguments.get("guild_id", "")).strip()
            user_id = str(arguments.get("user_id", "")).strip()
            until_iso8601 = str(arguments.get("until_iso8601", "")).strip()
            reason = str(arguments.get("reason", "")).strip()
            approval_token = str(arguments.get("approval_token", "")).strip()
            if not guild_id or not user_id or not until_iso8601 or not approval_token:
                return "Error: 'guild_id', 'user_id', 'until_iso8601', and 'approval_token' are required."
            try:
                return json.dumps(
                    discord_adapter.timeout_member(
                        guild_id=guild_id,
                        user_id=user_id,
                        until_iso8601=until_iso8601,
                        reason=reason,
                        approval_token=approval_token,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as exc:
                return f"Discord timeout action failed: {exc}"

        if name == "delete_discord_message":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            channel_id = str(arguments.get("channel_id", "")).strip()
            message_id = str(arguments.get("message_id", "")).strip()
            reason = str(arguments.get("reason", "")).strip()
            approval_token = str(arguments.get("approval_token", "")).strip()
            if not channel_id or not message_id or not approval_token:
                return "Error: 'channel_id', 'message_id', and 'approval_token' are required."
            try:
                return json.dumps(
                    discord_adapter.delete_message(
                        channel_id=channel_id,
                        message_id=message_id,
                        reason=reason,
                        approval_token=approval_token,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as exc:
                return f"Discord delete action failed: {exc}"

        if name == "issue_discord_approval_token":
            if discord_adapter is None:
                return "Discord adapter unavailable."
            action_type = str(arguments.get("action_type", "")).strip()
            actor = str(arguments.get("actor", "")).strip()
            ttl_seconds = int(arguments.get("ttl_seconds", 300) or 300)
            if not action_type or not actor:
                return "Error: 'action_type' and 'actor' are required."
            try:
                token = discord_adapter.issue_approval_token(
                    action_type=action_type,
                    actor=actor,
                    ttl_seconds=ttl_seconds,
                )
                return json.dumps(
                    {
                        "ok": True,
                        "action_type": action_type,
                        "ttl_seconds": ttl_seconds,
                        "approval_token": token,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            except Exception as exc:
                return f"Approval token issuance failed: {exc}"

        return f"Unknown tool: '{name}'."

    return execute_tool
