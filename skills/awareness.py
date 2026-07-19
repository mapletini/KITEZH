"""Helpers for formatting runtime awareness context for prompts."""

from __future__ import annotations

from collections.abc import Iterable


def format_awareness_block(lines: Iterable[str]) -> str:
    bullet_lines = "\n".join(f"- {line}" for line in lines)
    return f"Operational awareness:\n{bullet_lines}"
