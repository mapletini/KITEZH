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
        stripped = value.strip() if value else ""
        if stripped:
            return stripped
    return default


def _env_flag(primary: str, *aliases: str, default: bool) -> bool:
    value = _env(primary, *aliases, default="1" if default else "0").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


#: Base URL of the remote FastAPI / Discord backend.
REMOTE_BASE_URL: str = _env(
    "KITEZH_REMOTE_URL",
    "MOCHII_API_URL",
    default="http://localhost:8000",
)

#: Toggle for the external remote API bridge.
REMOTE_ENABLED: bool = _env_flag(
    "KITEZH_REMOTE_ENABLED",
    "KITEZH_REMOTE_API_ENABLED",
    default=True,
)

#: Secret header value sent with every request to the remote backend.
AI_KEY: str = _env(
    "KITEZH_AI_KEY",
    "AI_BRIDGE_SECRET",
    default="changeme",
)

#: Sentinel values treated as insecure/unconfigured API keys by bridge and web auth checks.
INSECURE_AI_KEYS: tuple[str, ...] = ("", "changeme", "change_me_ai_bridge_secret")

#: Sentinel values treated as insecure/unconfigured command signing secrets.
INSECURE_SIGNING_SECRETS: tuple[str, ...] = (
    "",
    "changeme",
    "change_me_ai_bridge_secret",
    "changeme-signing-secret",
)

#: Secret used to sign command envelopes.
COMMAND_SIGNING_SECRET: str = _env(
    "KITEZH_COMMAND_SIGNING_SECRET",
    "AI_COMMAND_SIGNING_SECRET",
    default="changeme-signing-secret",
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

#: Enable the Letta memory/agent integration (all 5 subsystems).
#: Set KITEZH_LETTA_ENABLED=1 to activate; disabled by default so the engine
#: can run without a Letta server.
LETTA_ENABLED: bool = _env_flag("KITEZH_LETTA_ENABLED", default=False)

#: Base URL for the Letta REST API.
LETTA_BASE_URL: str = os.environ.get("KITEZH_LETTA_URL", "http://localhost:8283")

#: API token for Letta API authentication (leave blank for unauthenticated local servers).
LETTA_TOKEN: str = os.environ.get("KITEZH_LETTA_TOKEN", "")

#: Letta agent ID to use.  When empty and KITEZH_LETTA_ENABLED=1, an agent
#: whose name matches KITEZH_LETTA_AGENT_NAME is found or created automatically.
LETTA_AGENT_ID: str = os.environ.get("KITEZH_LETTA_AGENT_ID", "")

#: Name for the default Letta agent.  Used by ``ensure_agent()`` when no
#: explicit agent ID is configured.  Defaults to "kai".
LETTA_AGENT_NAME: str = os.environ.get("KITEZH_LETTA_AGENT_NAME", "kai")

#: Base URL for the llama.cpp OpenAI-compatible API.
LLAMACPP_BASE_URL: str = os.environ.get("KITEZH_LLAMACPP_URL", "http://localhost:8080")

#: llama.cpp model name to target on /v1/chat/completions.
LLAMACPP_MODEL: str = os.environ.get("KITEZH_LLAMACPP_MODEL", "nous-hermes-2-mixtral-8x7b-dpo-gguf")

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

#: TCP port the built-in web chat server listens on.
WEB_PORT: int = int(os.environ.get("KITEZH_WEB_PORT", "7860"))

#: Shared JSON file storing the latest visual/display state for local and remote faces.
DISPLAY_STATE_PATH: str = os.environ.get(
    "KITEZH_DISPLAY_STATE_PATH",
    os.path.join(WORKSPACE_PATH, "kai_display_state.json"),
)

#: Idle progression cadence in seconds for autonomous mood drift and display refresh.
AUTONOMY_INTERVAL_SECONDS: float = float(os.environ.get("KITEZH_AUTONOMY_INTERVAL", "15.0"))

#: Interval used by local terminal/framebuffer face processes when polling the display state file.
DISPLAY_REFRESH_SECONDS: float = float(os.environ.get("KITEZH_DISPLAY_REFRESH_SECONDS", "1.0"))

#: SDL video backend used by the optional framebuffer face process.
DISPLAY_VIDEO_DRIVER: str = os.environ.get("KITEZH_DISPLAY_VIDEO_DRIVER", "kmsdrm")

#: Grace period for the CLI autonomy thread to stop during shutdown.
AUTONOMY_SHUTDOWN_TIMEOUT_SECONDS: float = float(
    os.environ.get("KITEZH_AUTONOMY_SHUTDOWN_TIMEOUT", "1.0")
)

# ---------------------------------------------------------------------------
# Dual-homing / network roles
# ---------------------------------------------------------------------------

#: Ethernet LAN subnet (CIDR) from which admin-only web routes are accessible.
#: When empty (the default), no IP-based restriction is applied — useful for
#: local development without a split-network setup.
#: Example: "192.168.1.0/24"
LAN_CIDR: str = os.environ.get("KITEZH_LAN_CIDR", "")

#: URL path prefix treated as admin-only on the LAN segment.
#: Any request whose path starts with this prefix is blocked for loopback and
#: non-LAN source IPs when KITEZH_LAN_CIDR is configured.
ADMIN_PATH_PREFIX: str = os.environ.get("KITEZH_ADMIN_PATH_PREFIX", "/api/kai")

# ---------------------------------------------------------------------------
# Tapo camera integration
# ---------------------------------------------------------------------------

#: Subnet to scan for Tapo cameras in CIDR notation, e.g. "192.168.1.0/24".
#: Falls back to KITEZH_LAN_CIDR when unset.  Leave both empty to skip
#: autodiscovery (a cached registry will still be loaded if present).
CAMERA_SUBNET: str = os.environ.get("KITEZH_CAMERA_SUBNET", "") or LAN_CIDR

#: Tapo local account username — the email address used in the Tapo app.
TAPO_USER: str = os.environ.get("KITEZH_TAPO_USER", "")

#: Tapo local device password set on each camera in the Tapo app.
TAPO_PASSWORD: str = os.environ.get("KITEZH_TAPO_PASSWORD", "")

#: Path to a custom openWakeWord .onnx model file, or a bundled model name
#: (e.g. "hey_jarvis").  Leave empty to disable wakeword audio listening.
WAKEWORD_MODEL: str = os.environ.get("KITEZH_WAKEWORD_MODEL", "")

#: Minimum prediction score (0.0–1.0) for a wakeword hit to trigger KAI.
WAKEWORD_THRESHOLD: float = float(os.environ.get("KITEZH_WAKEWORD_THRESHOLD", "0.5"))
