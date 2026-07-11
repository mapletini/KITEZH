"""
config.py — Centralized configuration for the Kitezh intelligence engine.

All environment-sensitive values are read from environment variables with
safe fallback defaults so the engine can be started without a .env file
during local development.
"""

from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# Remote backend
# ---------------------------------------------------------------------------

def _env(primary: str, *aliases: str, default: str) -> str:
    for key in (primary, *aliases):
        value = os.environ.get(key)
        if value:
            return value
    return default


#: Base URL of the remote FastAPI / Discord backend.
REMOTE_BASE_URL: str = _env(
    "KITEZH_REMOTE_URL",
    "MOCHII_API_URL",
    default="http://localhost:8000",
)

#: Secret header value sent with every request to the remote backend.
AI_KEY: str = _env(
    "KITEZH_AI_KEY",
    "AI_BRIDGE_SECRET",
    default="changeme",
)

#: Secret used to sign command envelopes.
COMMAND_SIGNING_SECRET: str = _env(
    "KITEZH_COMMAND_SIGNING_SECRET",
    "AI_COMMAND_SIGNING_SECRET",
    default=AI_KEY,
)

#: Full URL for the AI context endpoint on the remote backend.
CONTEXT_ENDPOINT: str = f"{REMOTE_BASE_URL}/api/ai/context"

#: HTTP request timeout in seconds.
REQUEST_TIMEOUT: float = float(os.environ.get("KITEZH_TIMEOUT", "10.0"))

# ---------------------------------------------------------------------------
# Puppy-trap
# ---------------------------------------------------------------------------

#: Discord user ID that triggers the friendly puppy-trap protocol.
DISCORD_PUPPY_ID: str = _env(
    "KITEZH_PUPPY_ID",
    "DISCORD_PUPPY_ID",
    default="",
)

# ---------------------------------------------------------------------------
# Local workspace (sandboxed skill execution)
# ---------------------------------------------------------------------------

#: Absolute path to the sandboxed workspace directory used by skills.
WORKSPACE_PATH: str = os.environ.get(
    "KITEZH_WORKSPACE", os.path.join(os.path.dirname(__file__), "workspace")
)

# ---------------------------------------------------------------------------
# LLM backend
# ---------------------------------------------------------------------------

#: Which local LLM backend to use: "ollama" | "letta"
LLM_BACKEND: str = os.environ.get("KITEZH_LLM_BACKEND", "ollama")

#: Base URL for the Ollama REST API.
OLLAMA_BASE_URL: str = os.environ.get("KITEZH_OLLAMA_URL", "http://localhost:11434")

#: Ollama model name to target.
OLLAMA_MODEL: str = os.environ.get("KITEZH_OLLAMA_MODEL", "llama3")

#: Base URL for the Letta REST API.
LETTA_BASE_URL: str = os.environ.get("KITEZH_LETTA_URL", "http://localhost:8283")

#: Letta agent ID to send initialization prompts to.
LETTA_AGENT_ID: str = os.environ.get("KITEZH_LETTA_AGENT_ID", "")

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

#: TCP port the built-in web chat server listens on.
WEB_PORT: int = int(os.environ.get("KITEZH_WEB_PORT", "7860"))
