"""
config.py — Centralized configuration for the Kitezh intelligence engine.

All environment-sensitive values are read from environment variables with
safe fallback defaults so the engine can be started without a .env file
during local development.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def _strip_env_quotes(value: str) -> str:
    """Remove matching single/double quotes around a value."""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _load_env_file(path: str | None = None, *, override: bool = False) -> int:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Returns the number of keys applied to the process environment.
    """
    env_path = Path(path) if path else Path(__file__).with_name(".env")
    if not env_path.exists() or not env_path.is_file():
        return 0

    applied = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _strip_env_quotes(value)
        applied += 1
    return applied


def reload_runtime_config(*, env_path: str | None = None, override: bool = True):
    """Reload .env values and refresh this module's computed config constants.

    Returns the reloaded ``config`` module object.
    """
    _load_env_file(env_path, override=override)
    return importlib.reload(sys.modules[__name__])


# Load local .env once at startup so config constants can pick up file-backed
# settings without requiring shell-level environment export commands.
_load_env_file(override=False)


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


def _env_csv(primary: str, *aliases: str) -> tuple[str, ...]:
    raw = _env(primary, *aliases, default="")
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _env_float(primary: str, *aliases: str, default: float, min_value: float | None = None) -> float:
    raw = _env(primary, *aliases, default=str(default)).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if min_value is not None and value < min_value:
        return min_value
    return value


BASELINE_AFFECT_MODES: tuple[str, ...] = ("neutral", "warm", "reserved", "anxious")


def normalize_baseline_affect_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in BASELINE_AFFECT_MODES:
        return mode
    return "neutral"


#: Startup affect/tone baseline for K.A.I. before adaptive drift takes over.
#: Allowed values: neutral | warm | reserved | anxious
BASELINE_AFFECT_MODE: str = normalize_baseline_affect_mode(
    _env("KITEZH_BASELINE_AFFECT_MODE", default="neutral")
)


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


def runtime_security_warnings() -> list[str]:
    """Return startup security warnings for clearly unsafe runtime settings."""
    warnings: list[str] = []
    if AI_KEY.strip() in INSECURE_AI_KEYS:
        warnings.append("KITEZH_AI_KEY is using an insecure default value")
    if COMMAND_SIGNING_SECRET.strip() in INSECURE_SIGNING_SECRETS:
        warnings.append("KITEZH_COMMAND_SIGNING_SECRET is using an insecure default value")
    if DISCORD_ENABLED and not DISCORD_BOT_TOKEN.strip():
        warnings.append("Discord runtime enabled without KITEZH_DISCORD_BOT_TOKEN")
    if DISCORD_ENABLED and DISCORD_VOICE_ENABLED and not DISCORD_BOT_USER_ID.strip():
        warnings.append("Discord voice enabled without KITEZH_DISCORD_BOT_USER_ID")
    return warnings

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
# Capability pull connector
# ---------------------------------------------------------------------------

#: Enable external capability-pull connectors (optional).
CAPABILITY_PULL_ENABLED: bool = _env_flag("KITEZH_CAPABILITY_PULL_ENABLED", default=False)

#: Base URL for capability pull APIs such as knowledge retrieval.
CAPABILITY_PULL_BASE_URL: str = _env("KITEZH_CAPABILITY_PULL_URL", default="")

#: Auth token for capability pull requests.
CAPABILITY_PULL_TOKEN: str = _env("KITEZH_CAPABILITY_PULL_TOKEN", default="")

#: Endpoint path for knowledge retrieval capability lookups.
CAPABILITY_KNOWLEDGE_PATH: str = _env("KITEZH_CAPABILITY_KNOWLEDGE_PATH", default="/api/knowledge/retrieve")

# ---------------------------------------------------------------------------
# Puppy-trap
# ---------------------------------------------------------------------------

#: Discord user ID that triggers the friendly puppy-trap protocol.
DISCORD_PUPPY_ID: str = _env(
    "KITEZH_PUPPY_ID",
    "DISCORD_PUPPY_ID",
    default="",
)

#: Enable Discord adapter lifecycle.
DISCORD_ENABLED: bool = _env_flag("KITEZH_DISCORD_ENABLED", default=False)

#: Discord bot token for API and gateway operations.
DISCORD_BOT_TOKEN: str = _env("KITEZH_DISCORD_BOT_TOKEN", default="")

#: Bot user ID for reliable mention detection in inbound message events.
DISCORD_BOT_USER_ID: str = _env("KITEZH_DISCORD_BOT_USER_ID", default="")

#: Operator Discord user ID used for runtime alerts (voice disable, etc).
DISCORD_OPERATOR_USER_ID: str = _env("KITEZH_DISCORD_OPERATOR_USER_ID", default="")

#: Number of messages pulled by default for context fetch operations.
DISCORD_RECENT_MESSAGES_LIMIT: int = int(os.environ.get("KITEZH_DISCORD_RECENT_MESSAGES_LIMIT", "50"))

#: Channels to poll for inbound message processing when gateway transport is not active.
DISCORD_INBOUND_CHANNEL_IDS: tuple[str, ...] = _env_csv("KITEZH_DISCORD_INBOUND_CHANNEL_IDS")

#: Poll cadence (seconds) for inbound message processing.
DISCORD_INBOUND_POLL_SECONDS: float = float(os.environ.get("KITEZH_DISCORD_INBOUND_POLL_SECONDS", "2.0"))

#: Enable Discord gateway runtime ingestion (recommended over REST polling).
DISCORD_GATEWAY_ENABLED: bool = _env_flag("KITEZH_DISCORD_GATEWAY_ENABLED", default=False)

#: Gateway intents bitmask for inbound message events.
#: Default 37383 = GUILDS(1) | GUILD_MEMBERS(2) | GUILD_MODERATION(4) | GUILD_MESSAGES(512)
#: | DIRECT_MESSAGES(4096) | MESSAGE_CONTENT(32768).
#: GUILD_MEMBERS is a privileged intent — enable it in the Discord Developer Portal
#: (Bot → Privileged Gateway Intents) before using join/leave logging or newcomer roles.
DISCORD_GATEWAY_INTENTS: int = int(os.environ.get("KITEZH_DISCORD_GATEWAY_INTENTS", "37383"))

#: Reconnect backoff base delay for gateway runtime.
DISCORD_GATEWAY_RECONNECT_BASE_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_GATEWAY_RECONNECT_BASE_SECONDS", "2.0")
)

#: Reconnect backoff max delay for gateway runtime.
DISCORD_GATEWAY_RECONNECT_MAX_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_GATEWAY_RECONNECT_MAX_SECONDS", "30.0")
)

#: Maximum Discord outbound send attempts before dropping and logging.
DISCORD_SEND_MAX_ATTEMPTS: int = int(os.environ.get("KITEZH_DISCORD_SEND_MAX_ATTEMPTS", "3"))

#: Base retry delay (seconds) for outbound queue backoff.
DISCORD_SEND_RETRY_BASE_SECONDS: float = float(os.environ.get("KITEZH_DISCORD_SEND_RETRY_BASE_SECONDS", "2.0"))

#: Cap delay (seconds) for outbound queue backoff.
DISCORD_SEND_RETRY_MAX_SECONDS: float = float(os.environ.get("KITEZH_DISCORD_SEND_RETRY_MAX_SECONDS", "12.0"))

#: Keep local Discord action audit records for this many days.
DISCORD_AUDIT_RETENTION_DAYS: int = int(os.environ.get("KITEZH_DISCORD_AUDIT_RETENTION_DAYS", "30"))

# ---------------------------------------------------------------------------
# Discord event-logging channels
# ---------------------------------------------------------------------------

#: Discord channel ID for posting moderation audit log entries (bans, kicks, timeouts, etc.).
DISCORD_LOG_CHANNEL_MODERATION: str = _env("KITEZH_DISCORD_LOG_CHANNEL_MODERATION", default="")

#: Discord channel ID for general server event logs (joins + leaves).
DISCORD_LOG_CHANNEL_GENERAL: str = _env("KITEZH_DISCORD_LOG_CHANNEL_GENERAL", default="")

#: Discord channel ID for member-join events only.
DISCORD_LOG_CHANNEL_JOINS: str = _env("KITEZH_DISCORD_LOG_CHANNEL_JOINS", default="")

#: Discord channel ID for member-leave events only.
DISCORD_LOG_CHANNEL_LEAVES: str = _env("KITEZH_DISCORD_LOG_CHANNEL_LEAVES", default="")

#: Discord role ID automatically assigned to every new member on join (newcomer role).
DISCORD_NEWCOMER_ROLE_ID: str = _env("KITEZH_DISCORD_NEWCOMER_ROLE_ID", default="")

#: Discord role ID whose holders may use moderator commands (kick, timeout, warn).
DISCORD_MOD_ROLE_ID: str = "1531687553747648532"

#: Discord role ID whose holders may use all moderation commands including ban.
DISCORD_ADMIN_ROLE_ID: str = "1531688174966018224"

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

#: Path to the ``llama-server`` binary.  Falls back to searching PATH.
LLAMACPP_SERVER_BIN: str = os.environ.get("KITEZH_LLAMACPP_SERVER_BIN", "llama-server")

#: Optional directory prepended to LD_LIBRARY_PATH for managed llama-server.
#: Useful for systemd services where shared libraries are outside the linker cache.
LLAMACPP_SERVER_LIB_DIR: str = os.environ.get("KITEZH_LLAMACPP_SERVER_LIB_DIR", "")

#: Absolute path to the ``.gguf`` model file to load when auto-starting llama-server.
#: Required when KITEZH_LLAMACPP_AUTOSTART=1 or --llama-server is passed.
LLAMACPP_MODEL_PATH: str = os.environ.get("KITEZH_LLAMACPP_MODEL_PATH", "")

#: Auto-start llama-server when using the ``llamacpp`` backend.
#: Set to 1 to enable; requires KITEZH_LLAMACPP_MODEL_PATH to be set.
LLAMACPP_AUTOSTART: bool = _env_flag("KITEZH_LLAMACPP_AUTOSTART", default=False)

#: Context-window size passed to llama-server via ``--ctx-size``.
LLAMACPP_SERVER_N_CTX: int = int(os.environ.get("KITEZH_LLAMACPP_N_CTX", "4096"))

#: GPU layers to offload to the accelerator via ``--n-gpu-layers`` (0 = CPU only).
LLAMACPP_SERVER_N_GPU_LAYERS: int = int(os.environ.get("KITEZH_LLAMACPP_N_GPU_LAYERS", "0"))

#: Auto-start the web chat server alongside the main engine (CLI / init-file mode).
#: Set to 1 to enable without passing --with-serve.
WEB_AUTOSTART: bool = _env_flag("KITEZH_WEB_AUTOSTART", default=False)

# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

#: TCP port the built-in web chat server listens on.
WEB_PORT: int = int(os.environ.get("KITEZH_WEB_PORT", "7860"))

#: Prefer the local llama.cpp agentic tool loop in web chat, even when
#: KITEZH_REMOTE_ENABLED=1. When enabled, the remote bridge is used as a
#: fallback path if local agentic execution fails.
WEB_LOCAL_AGENTIC_FIRST: bool = _env_flag("KITEZH_WEB_LOCAL_AGENTIC_FIRST", default=True)

#: Shared JSON file storing the latest visual/display state for local and remote faces.
DISPLAY_STATE_PATH: str = os.environ.get(
    "KITEZH_DISPLAY_STATE_PATH",
    os.path.join(WORKSPACE_PATH, "kai_display_state.json"),
)

#: Idle progression cadence in seconds for autonomous mood drift and display refresh.
AUTONOMY_INTERVAL_SECONDS: float = float(os.environ.get("KITEZH_AUTONOMY_INTERVAL", "15.0"))

#: Number of dream-consolidation cycles between memory reflection sessions (CLI autonomy daemon).
#: Set to 1 to reflect every consolidation run; higher values reduce LLM load.
REFLECTION_CYCLE_INTERVAL: int = int(os.environ.get("KITEZH_REFLECTION_INTERVAL", "1"))

#: Number of dream-consolidation cycles between curiosity-loop runs (CLI autonomy daemon).
CURIOSITY_CYCLE_INTERVAL: int = int(os.environ.get("KITEZH_CURIOSITY_INTERVAL", "2"))

#: Interval in seconds between memory reflection sessions in the web autonomy daemon.
REFLECTION_INTERVAL_SECONDS: float = float(os.environ.get("KITEZH_REFLECTION_INTERVAL_SECS", "3600"))

#: Interval in seconds between curiosity-loop runs in the web autonomy daemon.
CURIOSITY_INTERVAL_SECONDS: float = float(os.environ.get("KITEZH_CURIOSITY_INTERVAL_SECS", "7200"))

#: Interval used by local terminal/framebuffer face processes when polling the display state file.
#: Clamped to >=0.05 to avoid accidental negative/zero-thrash values crashing face loops.
DISPLAY_REFRESH_SECONDS: float = _env_float(
    "KITEZH_DISPLAY_REFRESH_SECONDS",
    default=1.0,
    min_value=0.05,
)

#: SDL video backend used by the optional framebuffer face process.
#: Defaults to ``kmsdrm`` for Linux/Ubuntu Server framebuffer deployments; other
#: environments may need to override this via KITEZH_DISPLAY_VIDEO_DRIVER.
DISPLAY_VIDEO_DRIVER: str = os.environ.get("KITEZH_DISPLAY_VIDEO_DRIVER", "kmsdrm")

#: Allow monitor scene routing to absolute external URLs.
#: Disabled by default so Kai cannot redirect local kiosk displays off-box.
DISPLAY_ALLOW_EXTERNAL_URLS: bool = _env_flag("KITEZH_DISPLAY_ALLOW_EXTERNAL_URLS", default=False)

#: Grace period for the CLI autonomy thread to stop during shutdown.
AUTONOMY_SHUTDOWN_TIMEOUT_SECONDS: float = float(
    os.environ.get("KITEZH_AUTONOMY_SHUTDOWN_TIMEOUT", "1.0")
)

#: Enable stitched reply playback through the audio splicer pipeline.
AUDIO_SPLICER_ENABLED: bool = _env_flag("KITEZH_AUDIO_SPLICER_ENABLED", default=False)

#: Library directory containing reusable wav clips for the audio splicer.
AUDIO_LIBRARY_PATH: str = os.environ.get(
    "KITEZH_AUDIO_LIBRARY_PATH",
    os.path.join(WORKSPACE_PATH, "audio_library"),
)

#: Enable Discord voice call features when dependencies are present.
DISCORD_VOICE_ENABLED: bool = _env_flag("KITEZH_DISCORD_VOICE_ENABLED", default=False)

#: Enable dedicated Discord voice websocket signaling runtime.
DISCORD_VOICE_SIGNALING_ENABLED: bool = _env_flag(
    "KITEZH_DISCORD_VOICE_SIGNALING_ENABLED",
    default=True,
)

#: Base reconnect backoff (seconds) for voice signaling websocket runtime.
DISCORD_VOICE_SIGNALING_RECONNECT_BASE_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_VOICE_SIGNALING_RECONNECT_BASE_SECONDS", "2.0")
)

#: Max reconnect backoff (seconds) for voice signaling websocket runtime.
DISCORD_VOICE_SIGNALING_RECONNECT_MAX_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_VOICE_SIGNALING_RECONNECT_MAX_SECONDS", "30.0")
)

#: Idle poll cadence (seconds) while waiting for an active voice session to signal.
DISCORD_VOICE_SIGNALING_IDLE_POLL_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_VOICE_SIGNALING_IDLE_POLL_SECONDS", "0.5")
)

#: Auto-join voice channels when K.A.I. is mentioned in text chat.
DISCORD_VOICE_AUTOJOIN_ON_MENTION: bool = _env_flag("KITEZH_DISCORD_VOICE_AUTOJOIN_ON_MENTION", default=True)

#: Guardrail: allow one active voice channel at a time.
DISCORD_VOICE_SINGLE_ACTIVE_CHANNEL: bool = _env_flag("KITEZH_DISCORD_VOICE_SINGLE_ACTIVE_CHANNEL", default=True)

#: Join requested voice channels with self mute enabled.
DISCORD_VOICE_SELF_MUTE: bool = _env_flag("KITEZH_DISCORD_VOICE_SELF_MUTE", default=False)

#: Join requested voice channels with self deaf enabled.
DISCORD_VOICE_SELF_DEAF: bool = _env_flag("KITEZH_DISCORD_VOICE_SELF_DEAF", default=False)

#: Default UDP port used for Discord voice media sockets when endpoint lacks explicit port.
DISCORD_VOICE_UDP_DEFAULT_PORT: int = int(os.environ.get("KITEZH_DISCORD_VOICE_UDP_DEFAULT_PORT", "50000"))

#: Socket connect timeout in seconds for Discord voice UDP setup.
DISCORD_VOICE_UDP_CONNECT_TIMEOUT_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_VOICE_UDP_CONNECT_TIMEOUT_SECONDS", "3.0")
)

#: Socket send timeout in seconds for Discord voice UDP packets.
DISCORD_VOICE_UDP_SEND_TIMEOUT_SECONDS: float = float(
    os.environ.get("KITEZH_DISCORD_VOICE_UDP_SEND_TIMEOUT_SECONDS", "1.0")
)

#: RTP payload type used for outgoing PCM packets in the lightweight UDP transport.
DISCORD_VOICE_RTP_PAYLOAD_TYPE: int = int(os.environ.get("KITEZH_DISCORD_VOICE_RTP_PAYLOAD_TYPE", "120"))

#: Voice payload encryption mode for UDP transport.
DISCORD_VOICE_ENCRYPTION_MODE: str = os.environ.get(
    "KITEZH_DISCORD_VOICE_ENCRYPTION_MODE",
    "xsalsa20_poly1305",
).strip() or "xsalsa20_poly1305"

#: Rolling in-memory raw audio buffer length in seconds.
DISCORD_VOICE_BUFFER_SECONDS: int = int(os.environ.get("KITEZH_DISCORD_VOICE_BUFFER_SECONDS", "30"))

#: Voice decoder ingest frame duration in milliseconds.
DISCORD_VOICE_FRAME_MS: int = int(os.environ.get("KITEZH_DISCORD_VOICE_FRAME_MS", "20"))

#: Voice decoder incremental decode cadence in milliseconds.
DISCORD_VOICE_DECODE_INTERVAL_MS: int = int(os.environ.get("KITEZH_DISCORD_VOICE_DECODE_INTERVAL_MS", "120"))

#: Endpointing silence window in milliseconds.
DISCORD_VOICE_ENDPOINT_SILENCE_MS: int = int(os.environ.get("KITEZH_DISCORD_VOICE_ENDPOINT_SILENCE_MS", "400"))

#: Confidence threshold that triggers barge-in while TTS is playing.
DISCORD_VOICE_BARGEIN_CONFIDENCE: float = float(os.environ.get("KITEZH_DISCORD_VOICE_BARGEIN_CONFIDENCE", "0.65"))

#: Degraded-mode trigger latency threshold in milliseconds.
DISCORD_VOICE_DECODE_WARN_MS: int = int(os.environ.get("KITEZH_DISCORD_VOICE_DECODE_WARN_MS", "900"))

#: Enable the custom local STT engine integration.
CUSTOM_STT_ENABLED: bool = _env_flag("KITEZH_CUSTOM_STT_ENABLED", default=False)

#: Optional path to custom STT model/artifacts.
CUSTOM_STT_MODEL_PATH: str = _env("KITEZH_CUSTOM_STT_MODEL_PATH", default="")

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
