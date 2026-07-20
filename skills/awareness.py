"""Helpers for formatting runtime awareness context for prompts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeAwareness:
    interface: str
    runtime_mode: str
    local_backend: str
    response_path: str
    active_tools: tuple[str, ...] = ()
    tool_calling_active: bool = False
    remote_enabled: bool = False
    letta_enabled: bool = False
    letta_available: bool = False
    letta_role: str = "disabled"
    display_mode: str = "idle"
    display_available: bool = True
    camera_summary: dict[str, Any] = field(default_factory=dict)

    def prompt_lines(self) -> list[str]:
        available_tools = ", ".join(self.active_tools) if self.active_tools else "none"
        unavailable: list[str] = [
            "Source-code editing, git commits, pushes, deployments, and rollbacks are unavailable unless exposed as live tools.",
            "Never infer abilities from memories, old conversations, or aspirational documentation.",
        ]
        if not self.tool_calling_active:
            unavailable.append("No callable tools are available in this runtime.")
        if not self.letta_available:
            unavailable.append("Letta-backed long-term memory services are unavailable right now.")

        camera_count = int(self.camera_summary.get("camera_count", 0) or 0)
        listener_count = int(self.camera_summary.get("wakeword_listener_count", 0) or 0)
        cameras_state = (
            f"{camera_count} camera(s) known, {listener_count} wakeword listener(s) active"
            if camera_count or listener_count
            else "no live camera access"
        )

        lines = [
            f"Interface: {self.interface}",
            f"Runtime mode: {self.runtime_mode}",
            f"Primary response path: {self.response_path}",
            f"Local backend setting: {self.local_backend}",
            f"Callable tools available right now: {available_tools}",
            f"Letta status: {'available' if self.letta_available else 'unavailable'} ({self.letta_role})",
            f"Display channel: {'available' if self.display_available else 'unavailable'} (mode: {self.display_mode})",
            f"Camera/wakeword state: {cameras_state}",
            "If asked about an unavailable action, say clearly that you cannot do it in this runtime.",
        ]
        lines.extend(unavailable)
        return lines

    def as_metadata(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "runtime_mode": self.runtime_mode,
            "response_path": self.response_path,
            "local_backend": self.local_backend,
            "tools_available": list(self.active_tools),
            "tool_calling_active": self.tool_calling_active,
            "remote_enabled": self.remote_enabled,
            "letta_enabled": self.letta_enabled,
            "letta_available": self.letta_available,
            "letta_role": self.letta_role,
            "display_available": self.display_available,
            "display_mode": self.display_mode,
            "camera_summary": dict(self.camera_summary),
        }


def build_runtime_awareness(
    *,
    interface: str,
    runtime_mode: str,
    local_backend: str,
    response_path: str,
    active_tools: Iterable[str] = (),
    remote_enabled: bool = False,
    letta_enabled: bool = False,
    letta_available: bool = False,
    letta_role: str = "disabled",
    display_mode: str = "idle",
    display_available: bool = True,
    camera_summary: dict[str, Any] | None = None,
) -> RuntimeAwareness:
    tool_names = tuple(name for name in active_tools if name)
    return RuntimeAwareness(
        interface=interface,
        runtime_mode=runtime_mode,
        local_backend=local_backend,
        response_path=response_path,
        active_tools=tool_names,
        tool_calling_active=bool(tool_names),
        remote_enabled=remote_enabled,
        letta_enabled=letta_enabled,
        letta_available=letta_available,
        letta_role=letta_role,
        display_mode=display_mode,
        display_available=display_available,
        camera_summary=dict(camera_summary or {}),
    )


def format_awareness_block(lines: Iterable[str]) -> str:
    bullet_lines = "\n".join(f"- {line}" for line in lines)
    return f"Operational awareness:\n{bullet_lines}"


def format_runtime_awareness_block(awareness: RuntimeAwareness) -> str:
    return format_awareness_block(awareness.prompt_lines())
