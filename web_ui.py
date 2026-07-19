"""
web_ui.py — K.A.I.'s self-editable multi-user web chat interface.

K.A.I. can read and rewrite its own HTML/CSS/JS at runtime via the
/api/kai/read-ui, /api/kai/patch-ui, and /api/kai/write-ui endpoints,
all of which are protected by the x-ai-key header.

Usage
-----
Launch the chat server on the configured port::

    python main.py --serve

Or directly::

    python web_ui.py
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import requests
import re
import secrets
import sqlite3
import time
import unicodedata
from contextlib import asynccontextmanager, suppress
from itertools import combinations
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

import config
from llm_backends import send_to_backend, chat_with_tools_llamacpp
from skills.cognitive_architect import LLMCognitiveBridge
from skills.deep_memory import DeepMemoryCore
from skills.display_bridge import DisplayBridge, build_display_payload
from skills.filesystem import WorkspaceWriter, WorkspaceReader
from skills.letta_bridge import build_letta_bridge
from skills.neuro_affect import NeuroChemicalEngine
from skills.tool_executor import TOOL_DEFINITIONS, make_tool_executor
from skills.awareness import format_awareness_block
try:
    from skills.tapo_hub import TapoHub
except ImportError:
    TapoHub = None

logger = logging.getLogger(__name__)
if TapoHub is None:
    logger.info("Optional TapoHub dependencies are unavailable; camera hub disabled in web mode.")

# Path inside the workspace where K.A.I.'s UI template lives.
UI_TEMPLATE_PATH = "ui/index.html"

# Dedicated SQLite file for the chat log (separate from deep memory).
_CHAT_DB_PATH = Path(config.WORKSPACE_PATH) / "chat_log.db"
_MAX_ARCHIVED_MESSAGE_LENGTH = 200
_DREAM_CONSOLIDATION_INTERVAL_SECONDS = 3600
_DREAM_CONSOLIDATION_INTERACTION_FREQUENCY = 10
# Maximum characters of a user message included in the Letta human-block profile summary.
_LETTA_USER_MESSAGE_PREVIEW = 200

# Letta integration bridge (None when KITEZH_LETTA_ENABLED=0).
_letta_bridge = build_letta_bridge()

# Core cognition state for web mode.
_web_memory = DeepMemoryCore(workspace_path=config.WORKSPACE_PATH, letta_bridge=_letta_bridge)
_web_neuro = NeuroChemicalEngine()
_web_cognitive = LLMCognitiveBridge(_web_memory, _web_neuro)
_display_bridge = DisplayBridge()
_web_interaction_count = 0

# Tapo camera hub — wired to the web-mode neuro engine.
_tapo_hub = TapoHub(neuro=_web_neuro) if TapoHub is not None else None

# Keep concept tokens at 4+ chars to reduce low-signal function words.
_CONCEPT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
_CONCEPT_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "your", "you",
    "are", "not", "but", "was", "were", "will", "would", "can", "could", "should",
    "about", "into", "through", "their", "there", "what", "when", "where", "why",
    "how", "just", "very", "like", "they", "them", "then", "than", "been", "ours",
    # Domain labels that appear in nearly every chat line and add little concept value.
    "ourselves", "kai", "user", "assistant", "reply",
}


# ---------------------------------------------------------------------------
# Chat log (SQLite)
# ---------------------------------------------------------------------------


def _chat_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_CHAT_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_chat_db() -> None:
    _CHAT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _chat_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    INTEGER NOT NULL,
                user_id      TEXT    NOT NULL,
                display_name TEXT    NOT NULL,
                content      TEXT    NOT NULL,
                thread_id    TEXT    NOT NULL DEFAULT '',
                channel      TEXT    NOT NULL DEFAULT 'user'
            )
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(messages)").fetchall()}
        if "thread_id" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT NOT NULL DEFAULT ''")
        if "channel" not in columns:
            conn.execute("ALTER TABLE messages ADD COLUMN channel TEXT NOT NULL DEFAULT 'user'")
        conn.execute("UPDATE messages SET thread_id = user_id WHERE thread_id = ''")

        # Persistent identity table: maps a normalised name key → stable thread.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_identities (
                name_key      TEXT PRIMARY KEY,
                thread_id     TEXT NOT NULL UNIQUE,
                passcode_hash TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.commit()


# scrypt parameters — N=2**15 provides good GPU resistance for a private service.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def _hash_passcode(passcode: str) -> str:
    """Return a storable ``{salt_hex}${hash_hex}`` string using scrypt."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(passcode.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return salt.hex() + "$" + dk.hex()


def _verify_passcode(passcode: str, stored: str) -> bool:
    """Return True if *passcode* matches the stored scrypt hash."""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.scrypt(passcode.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return secrets.compare_digest(dk.hex(), hash_hex)


def _lookup_or_create_identity(
    display_name: str,
    passcode: str,
) -> tuple[str, bool, str]:
    """Return (thread_id, ok, error_message).

    First visit with a given name creates a new stable thread and stores an
    optional passcode hash.  Subsequent visits verify the passcode when one
    was set, or allow free entry when none was set.

    Passcodes are hashed with hashlib.scrypt (salt stored alongside the hash
    as ``{salt_hex}${hash_hex}``) to protect against brute-force and rainbow
    table attacks.
    """
    name_key = unicodedata.normalize("NFKC", display_name).strip().lower()
    if not name_key:
        return "", False, "Handle cannot be empty."

    passcode = passcode or ""
    with _chat_conn() as conn:
        row = conn.execute(
            "SELECT thread_id, passcode_hash FROM user_identities WHERE name_key = ?",
            (name_key,),
        ).fetchone()

        if row is None:
            # New identity — create a stable thread_id with cryptographic entropy.
            thread_id = "t_" + secrets.token_hex(16)
            stored_hash = _hash_passcode(passcode) if passcode else ""
            conn.execute(
                "INSERT INTO user_identities (name_key, thread_id, passcode_hash) VALUES (?,?,?)",
                (name_key, thread_id, stored_hash),
            )
            conn.commit()
            return thread_id, True, ""

        thread_id = row["thread_id"]
        stored_hash = row["passcode_hash"]

        if stored_hash:
            # Account has a passcode — verify.
            if not _verify_passcode(passcode, stored_hash):
                return "", False, "Incorrect passcode."

        return thread_id, True, ""


def _save_message(
    thread_id: str,
    user_id: str,
    display_name: str,
    content: str,
    channel: str = "user",
) -> None:
    with _chat_conn() as conn:
        conn.execute(
            """
            INSERT INTO messages (timestamp, user_id, display_name, content, thread_id, channel)
            VALUES (?,?,?,?,?,?)
            """,
            (int(time.time()), user_id, display_name, content, thread_id, channel),
        )
        conn.commit()


def _fetch_messages(thread_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _chat_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Multi-user WebSocket connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Tracks active WebSocket sessions and routes frames per user session."""

    def __init__(self) -> None:
        # user_id → (display_name, WebSocket)
        self._active: dict[str, tuple[str, WebSocket]] = {}

    async def connect(self, user_id: str, display_name: str, ws: WebSocket) -> None:
        await ws.accept()
        existing = self._active.get(user_id)
        if existing:
            try:
                await existing[1].close(code=1000)
            except Exception:
                pass
        self._active[user_id] = (display_name, ws)
        logger.info("WebSocket connected: %s (%s)", display_name, user_id)

    def disconnect(self, user_id: str) -> str | None:
        entry = self._active.pop(user_id, None)
        return entry[0] if entry else None

    @property
    def user_count(self) -> int:
        return len(self._active)

    def user_list(self) -> list[dict[str, str]]:
        return [{"user_id": uid, "display_name": dn} for uid, (dn, _) in self._active.items()]

    async def send_to(self, user_id: str, message: dict[str, Any]) -> None:
        entry = self._active.get(user_id)
        if not entry:
            return
        payload = json.dumps(message)
        try:
            await entry[1].send_text(payload)
        except Exception:
            self._active.pop(user_id, None)


def _extract_kai_content(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("response", "reply", "message", "content", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        messages = data.get("messages")
        if isinstance(messages, list):
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
        return json.dumps(data, ensure_ascii=False)
    return str(data)


# Number of past DB messages to include as conversation history for the llamacpp backend.
_CONVERSATION_HISTORY_LIMIT = 20
# Map DB channel names to OpenAI role names.
_CHANNEL_TO_ROLE: dict[str, str] = {"user": "user", "kai": "assistant"}


def _tool_names() -> list[str]:
    names: list[str] = []
    for tool in TOOL_DEFINITIONS:
        fn = tool.get("function", {})
        name = fn.get("name")
        if name and isinstance(name, str):
            names.append(name)
    return names


def _active_tool_names() -> list[str]:
    # Only the local llamacpp path uses the agentic tool loop; remote mode and
    # other backends are plain text generation without callable tool execution.
    if config.REMOTE_ENABLED or config.LLM_BACKEND != "llamacpp":
        return []
    return _tool_names()


def _awareness_summary_for_prompt() -> str:
    bridge_mode = "remote bridge" if config.REMOTE_ENABLED else "local backend"
    active_tools = _active_tool_names()
    available_tools = ", ".join(active_tools) if active_tools else "none"
    return format_awareness_block(
        (
            "Interface: web chat",
            f"Runtime mode: {bridge_mode}",
            f"Local backend setting: {config.LLM_BACKEND}",
            f"Callable tools available right now: {available_tools}",
            "Never state that you can access or use tools outside this exact list; if none are available, say so directly.",
        )
    )


def _awareness_metadata() -> dict[str, Any]:
    return {
        "interface": "web_chat",
        "remote_enabled": config.REMOTE_ENABLED,
        "local_backend": config.LLM_BACKEND,
        "tools_available": _active_tool_names(),
    }


def _build_kai_system_prompt(user_id: str | None = None) -> str:
    """Build a rich system prompt from Kai's current cognitive and emotional state."""
    parts: list[str] = [
        "You are K.A.I. (Kitezh Autonomous Intelligence), an emotionally-aware AI with a "
        "rich inner life shaped by your memories, relationships, and emotional state. "
        "Respond as yourself — curious, thoughtful, and genuine. "
        "You may use the provided tools when they help you give a better response. "
        "Never claim you can access tools that are not explicitly available in this runtime.",
    ]
    try:
        personality = _web_memory.synthesize_personality_context()
        if personality:
            parts.append(personality)
    except Exception as exc:
        logger.debug("Could not build personality context: %s", exc)
    try:
        emotion = _web_neuro.emotion_snapshot()
        parts.append(
            f"[Current emotional state: {emotion.get('label', 'neutral')}, "
            f"strongest unmet need: {emotion.get('strongest_need', 'connection')}]"
        )
    except Exception as exc:
        logger.debug("Could not get emotion snapshot: %s", exc)
    parts.append(_awareness_summary_for_prompt())
    return "\n\n".join(parts)


def _build_conversation_history(
    user_id: str, limit: int = _CONVERSATION_HISTORY_LIMIT
) -> list[dict[str, Any]]:
    """Convert recent DB messages into an OpenAI-format message list."""
    db_messages = _fetch_messages(user_id, limit)
    history: list[dict[str, Any]] = []
    for msg in db_messages:
        role = _CHANNEL_TO_ROLE.get(msg.get("channel", "user"), "user")
        msg_content = msg.get("content", "")
        if msg_content:
            history.append({"role": role, "content": msg_content})
    return history


def _query_kai(user_id: str, display_name: str, content: str) -> str:
    if not config.REMOTE_ENABLED:
        # For the llamacpp backend use the full agentic loop with tool calling.
        if config.LLM_BACKEND == "llamacpp":
            try:
                system_prompt = _build_kai_system_prompt(user_id)
                history = _build_conversation_history(user_id)
                history.append({"role": "user", "content": content})
                executor = make_tool_executor(memory=_web_memory, neuro=_web_neuro)
                return chat_with_tools_llamacpp(
                    history,
                    system=system_prompt,
                    tools=TOOL_DEFINITIONS,
                    tool_executor=executor,
                )
            except RuntimeError as exc:
                logger.warning("K.A.I. llamacpp agentic call failed: %s", exc)
                return "K.A.I. llamacpp backend unavailable right now. Check the configured llama-server and try again."
        # Other backends (ollama, letta) use the simple single-prompt path.
        try:
            system_prompt = _build_kai_system_prompt(user_id)
            return send_to_backend(
                content,
                backend=config.LLM_BACKEND,
                system=system_prompt,
            )
        except RuntimeError as exc:
            logger.warning("K.A.I. local backend failed: %s", exc)
            return "K.A.I. local backend unavailable right now. Check the configured LLM server and try again."

    payload = {
        "platform": "web",
        "user_id": user_id,
        "display_name": display_name,
        "content": content,
        "clearance": "guest",
        "is_puppy": False,
        "metadata": {"kai_awareness": _awareness_metadata()},
    }
    try:
        response = requests.post(
            config.CONTEXT_ENDPOINT,
            json=payload,
            timeout=config.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return _extract_kai_content(response.json())
    except Exception as exc:
        logger.warning("K.A.I. context bridge failed: %s", exc)
        return "K.A.I. bridge unavailable right now. Try again in a moment."


def _extract_concepts(text: str, limit: int = 12) -> list[str]:
    concepts: list[str] = []
    seen: set[str] = set()
    for token in _CONCEPT_TOKEN_RE.findall(text.lower()):
        if token in _CONCEPT_STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        concepts.append(token)
        if len(concepts) >= limit:
            break
    return concepts


def _reinforce_message_concepts(text: str) -> None:
    concepts = _extract_concepts(text)
    if len(concepts) < 2:
        return
    for concept_a, concept_b in combinations(concepts, 2):
        _web_memory.reinforce_synapse(concept_a, concept_b, weight_gain=0.02)


def _process_web_cognitive_loop(user_id: str, display_name: str, user_content: str, kai_reply: str) -> None:
    global _web_interaction_count

    _web_neuro.set_active_user(user_id)
    bridge_failed = "bridge unavailable" in kai_reply.lower()
    if bridge_failed:
        _web_neuro.apply_stimulus(uncertainty=0.15, frustration=0.05, user_id=user_id)
        _web_memory.update_relationship(
            user_id,
            display_name=display_name,
            trust_delta=-0.03,
            tension_delta=0.04,
            familiarity_delta=0.01,
        )
        _web_memory.infer_preferences_from_text(user_content, -0.02)
    else:
        _web_neuro.apply_stimulus(reward=0.1, success=0.2, user_id=user_id)
        _web_memory.update_relationship(
            user_id,
            display_name=display_name,
            trust_delta=0.04,
            attachment_delta=0.03,
            tension_delta=-0.02,
            familiarity_delta=0.05,
        )
        _web_memory.infer_preferences_from_text(user_content, 0.03)
        _web_memory.infer_preferences_from_text(kai_reply, 0.02)
        _web_cognitive.synchronize_attachment(
            {
                "user_id": user_id,
                "display_name": display_name,
                "platform": "web",
                "content": user_content,
                "reply": kai_reply,
                "metadata": {},
            }
        )

    pad_coords = _web_neuro.get_pad_coordinates()
    intensity = _web_neuro.emotional_intensity(pad=pad_coords)
    memory_type = "key" if intensity >= 0.6 else "episodic"
    archived_content = (
        f"User({display_name}): {user_content} | Kai: {kai_reply}"
    )[:_MAX_ARCHIVED_MESSAGE_LENGTH]
    _web_memory.archive_episode(
        category="web_conversation",
        content=archived_content,
        p=float(pad_coords[0]),
        a=float(pad_coords[1]),
        d=float(pad_coords[2]),
        importance=1.0 + intensity,
        memory_type=memory_type,
    )

    _reinforce_message_concepts(user_content)
    _reinforce_message_concepts(kai_reply)
    _web_cognitive.deliberate()
    _publish_display_state("active", f"Speaking with {display_name}.")

    # Update Letta's human memory block with a brief user profile summary
    if _letta_bridge is not None:
        human_summary = (
            f"Active user: {display_name} (id={user_id}). "
            f"Most recent message: {user_content[:_LETTA_USER_MESSAGE_PREVIEW]}"
        )
        _letta_bridge.update_human_block(human_summary)

    _web_interaction_count += 1
    if _web_interaction_count % _DREAM_CONSOLIDATION_INTERACTION_FREQUENCY == 0:
        _web_memory.execute_dream_consolidation()
        if _letta_bridge is not None:
            personality_ctx = _web_memory.synthesize_personality_context()
            _letta_bridge.send_dream_message(personality_ctx)
        _publish_display_state("dreaming", "Kai is consolidating its memories.")


def _publish_display_state(mode: str, message: str = "") -> None:
    emotion = _web_neuro.emotion_snapshot()
    payload = build_display_payload(
        emotion,
        desires=_web_cognitive.current_desires,
        intentions=_web_cognitive.current_intentions,
        narrative=_web_memory.get_self_narrative(),
        preferences=_web_memory.get_preferences(limit=3),
        relationship=_web_memory.get_relationship(_web_neuro.active_user_id),
        mode=mode,
        message=message,
    )
    _display_bridge.publish(payload)


def _advance_web_autonomy() -> None:
    snapshot = _web_neuro.advance_autonomous_state(config.AUTONOMY_INTERVAL_SECONDS)
    _web_memory.reflect_on_state(
        snapshot,
        desires=_web_cognitive.current_desires,
        intentions=_web_cognitive.current_intentions,
        user_id=_web_neuro.active_user_id,
    )
    _publish_display_state("idle", "Kai is idly reflecting.")


async def _dream_consolidation_daemon() -> None:
    while True:
        await asyncio.sleep(_DREAM_CONSOLIDATION_INTERVAL_SECONDS)
        try:
            _web_memory.execute_dream_consolidation()
            logger.info("Background dream consolidation complete.")
            if _letta_bridge is not None:
                personality_ctx = _web_memory.synthesize_personality_context()
                _letta_bridge.send_dream_message(personality_ctx)
            _publish_display_state("dreaming", "Kai is consolidating its memories.")
        except Exception as exc:
            logger.exception("Background dream consolidation failed: %s", exc)


async def _autonomy_daemon() -> None:
    while True:
        await asyncio.sleep(config.AUTONOMY_INTERVAL_SECONDS)
        try:
            _advance_web_autonomy()
        except Exception as exc:
            logger.exception("Background autonomy update failed: %s", exc)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

_init_chat_db()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    if _tapo_hub is not None:
        _tapo_hub.start()
    _web_cognitive.refresh_self_narrative()
    _publish_display_state("idle", "Kai is waking up.")
    background_task = asyncio.create_task(_dream_consolidation_daemon())
    autonomy_task = asyncio.create_task(_autonomy_daemon())
    try:
        yield
    finally:
        background_task.cancel()
        autonomy_task.cancel()
        with suppress(asyncio.CancelledError):
            await background_task
        with suppress(asyncio.CancelledError):
            await autonomy_task
        _publish_display_state("idle", "Kai is resting.")
        if _tapo_hub is not None:
            _tapo_hub.stop()


app = FastAPI(title="K.A.I. Chat Interface", docs_url=None, redoc_url=None, lifespan=_lifespan)
manager = ConnectionManager()


# ---------------------------------------------------------------------------
# LAN-only admin guard
# ---------------------------------------------------------------------------


def _is_admin_allowed(client_host: str | None) -> bool:
    """Return True if the source IP is permitted to call admin endpoints.

    When KITEZH_LAN_CIDR is unset the guard is disabled (always returns True),
    preserving the existing behaviour for development setups.

    In production with dual-homing:
    - Public internet traffic arrives via cloudflared → loopback (127.0.0.1) → blocked.
    - Operator LAN traffic arrives directly on the Ethernet NIC → allowed.
    """
    if not config.LAN_CIDR:
        return True  # Guard disabled; allow all (development / unconfigured)
    if not client_host:
        return False
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    if addr.is_loopback:
        # cloudflared tunnels public requests in via loopback — deny
        return False
    try:
        network = ipaddress.ip_network(config.LAN_CIDR, strict=False)
    except ValueError:
        logger.error(
            "KITEZH_LAN_CIDR '%s' is not a valid CIDR; blocking all admin access.",
            config.LAN_CIDR,
        )
        return False
    return addr in network


class LanAdminGuard(BaseHTTPMiddleware):
    """Restrict admin-prefixed routes to direct LAN connections only.

    Public traffic tunnelled through cloudflared arrives from loopback and is
    blocked.  Direct Ethernet connections within the configured LAN CIDR are
    allowed through.  All other paths are unaffected.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path.startswith(config.ADMIN_PATH_PREFIX):
            host = request.client.host if request.client else None
            if not _is_admin_allowed(host):
                logger.warning(
                    "LanAdminGuard: blocked admin request from %s → %s",
                    host,
                    request.url.path,
                )
                return Response(
                    "Forbidden — admin routes require a direct LAN connection.",
                    status_code=403,
                )
        return await call_next(request)


app.add_middleware(LanAdminGuard)


# ── Public endpoints ──────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root() -> HTMLResponse:
    """Serve K.A.I.'s self-editable HTML interface from the workspace."""
    reader = WorkspaceReader()
    if reader.exists(UI_TEMPLATE_PATH):
        return HTMLResponse(reader.read_text(UI_TEMPLATE_PATH))
    return HTMLResponse(
        "<html><body style='background:#0a0a0f;color:#00ff99;font-family:monospace'>"
        "<h2>K.A.I. interface not yet initialized.</h2>"
        "<p>POST to <code>/api/kai/write-ui</code> with the initial HTML.</p>"
        "</body></html>"
    )


@app.get("/api/chat/log")
async def chat_log(user_id: str, limit: int = 100) -> dict[str, Any]:
    """Return the most recent *limit* messages for one private user thread."""
    return {"messages": _fetch_messages(user_id, limit)}


@app.post("/api/auth/join")
async def auth_join(body: dict[str, str] = Body(...)) -> dict[str, Any]:
    """Resolve or create a persistent identity for a display name.

    Body: ``{"display_name": "...", "passcode": "..."}``

    Returns ``{"thread_id": "..."}`` on success, or HTTP 401/422 on error.
    """
    display_name = (body.get("display_name") or "").strip()
    passcode = body.get("passcode") or ""
    if not display_name:
        raise HTTPException(status_code=422, detail="Handle cannot be empty.")
    thread_id, ok, error = _lookup_or_create_identity(display_name, passcode)
    if not ok:
        raise HTTPException(status_code=401, detail=error)
    return {"thread_id": thread_id}


@app.get("/api/chat/users")
async def online_users() -> dict[str, Any]:
    """Return the list of currently connected users."""
    return {"count": manager.user_count, "users": manager.user_list()}


@app.get("/api/kai/emotion")
async def emotion_state() -> dict[str, Any]:
    """Return K.A.I.'s current emotion snapshot from web-mode neuro state."""
    return {"emotion": _web_neuro.emotion_snapshot()}


@app.get("/api/display/state")
async def display_state() -> dict[str, Any]:
    return _display_bridge.latest()


@app.get("/api/display/stream")
async def display_stream(request: Request) -> StreamingResponse:
    async def event_gen():
        last_version = None
        while True:
            if await request.is_disconnected():
                break
            state = _display_bridge.latest()
            version = state.get("version")
            if version != last_version:
                yield f"data: {json.dumps(state, ensure_ascii=False)}\n\n"
                last_version = version
            await asyncio.sleep(config.DISPLAY_REFRESH_SECONDS)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/face", response_class=HTMLResponse, include_in_schema=False)
async def face_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>K.A.I. Face</title>
<style>
body{margin:0;background:#060a12;color:#dff4ff;font-family:system-ui,Segoe UI,sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center}
.wrap{display:flex;flex-direction:column;align-items:center;gap:18px}
.orb{width:38vmin;height:38vmin;border-radius:50%;background:radial-gradient(circle at 50% 45%,#8ce3ff,#102136 65%,#060a12 100%);position:relative;box-shadow:0 0 60px rgba(100,200,255,.25)}
.eye{position:absolute;top:35%;width:14%;height:14%;border-radius:50%;background:#f5fbff}
.eye.left{left:28%}.eye.right{right:28%}
.pupil{position:absolute;inset:28%;border-radius:50%;background:#09111f}
.mouth{position:absolute;left:32%;right:32%;bottom:24%;height:18%;border-bottom:4px solid #f5fbff;border-radius:0 0 120px 120px}
.meta{opacity:.85;text-align:center;max-width:70ch}
</style></head><body>
<div class="wrap"><div class="orb" id="orb"><div class="eye left"><div class="pupil"></div></div><div class="eye right"><div class="pupil"></div></div><div class="mouth" id="mouth"></div></div>
<div class="meta"><h2 id="emotion">Kai</h2><div id="narrative">Waiting…</div></div></div>
<script>
const orb=document.getElementById('orb'), mouth=document.getElementById('mouth');
const emotionEl=document.getElementById('emotion'), narrativeEl=document.getElementById('narrative');
function applyState(state){
  const emotion=state.emotion||{}, pad=emotion.pad||[0,0,0], label=emotion.label||'neutral';
  const colors={joy:['#ffe67a','#17181d'],love:['#ff9dd7','#1b1020'],trust:['#8affd0','#10201b'],fear:['#8cb6ff','#0e1320'],sadness:['#7ea1ff','#0d1220'],anger:['#ff8c8c','#220d0d'],anticipation:['#ffba6d','#24170a']};
  const pair=colors[label]||['#8ce3ff','#102136'];
  orb.style.background=`radial-gradient(circle at 50% 45%,${pair[0]},${pair[1]} 65%,#060a12 100%)`;
  mouth.style.borderBottomLeftRadius=(pad[0] >= 0 ? 120 : 20)+'px';
  mouth.style.borderBottomRightRadius=(pad[0] >= 0 ? 120 : 20)+'px';
  mouth.style.transform=`translateY(${pad[1]*-8}px) scaleY(${pad[0] >= 0 ? 1 : -.5})`;
  emotionEl.textContent=`${label} · need: ${emotion.strongest_need||'connection'}`;
  narrativeEl.textContent=state.narrative||'Kai is quiet but present.';
}
const es=new EventSource('/api/display/stream');
es.onmessage=(evt)=>applyState(JSON.parse(evt.data));
fetch('/api/display/state').then(r=>r.json()).then(applyState);
</script></body></html>"""
    )


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(
    ws: WebSocket,
    user_id: str,
    display_name: str = "Anonymous",
    thread_id: str = "",
) -> None:
    """
    Real-time chat endpoint.  Connect with::

        ws://<host>:<port>/ws/<user_id>?display_name=<name>&thread_id=<tid>

    ``thread_id`` is the stable persistent thread returned by ``/api/auth/join``.
    When omitted the session-scoped ``user_id`` is used as the thread (legacy
    behaviour preserved for backwards compatibility).

    On connect the client receives a ``history`` frame containing the last 50
    messages from its persistent thread, then live ``message`` frames.
    """
    effective_thread = thread_id if thread_id else user_id
    await manager.connect(user_id, display_name, ws)

    # Send private history to this user only.
    await ws.send_text(json.dumps({"type": "history", "messages": _fetch_messages(effective_thread, 50)}))

    try:
        while True:
            raw = await ws.receive_text()
            # Strip whitespace; ignore empty frames.
            content = raw.strip()
            if not content:
                continue
            timestamp = int(time.time())
            _save_message(effective_thread, user_id, display_name, content, channel="user")
            await manager.send_to(user_id, {
                "type": "message",
                "user_id": user_id,
                "display_name": display_name,
                "content": content,
                "timestamp": timestamp,
            })

            kai_reply = _query_kai(effective_thread, display_name, content)
            kai_timestamp = int(time.time())
            _save_message(effective_thread, "kai", "K.A.I.", kai_reply, channel="kai")
            await manager.send_to(user_id, {
                "type": "message",
                "user_id": "kai",
                "display_name": "K.A.I.",
                "content": kai_reply,
                "timestamp": kai_timestamp,
            })
            _process_web_cognitive_loop(effective_thread, display_name, content, kai_reply)
    except WebSocketDisconnect:
        manager.disconnect(user_id)


# ── K.A.I. self-edit endpoints (LAN-only, guarded by LanAdminGuard) ──────────────────────


@app.get("/api/kai/read-ui")
async def read_ui() -> dict[str, str]:
    """K.A.I. reads the full source of its own chat interface."""
    reader = WorkspaceReader()
    if not reader.exists(UI_TEMPLATE_PATH):
        raise HTTPException(status_code=404, detail="UI template not found")
    return {"content": reader.read_text(UI_TEMPLATE_PATH)}


@app.post("/api/kai/patch-ui")
async def patch_ui(
    body: dict[str, str] = Body(...),
) -> dict[str, Any]:
    """
    K.A.I. replaces a substring in its UI.  Body: ``{"old": "...", "new": "..."}``.
    Returns the number of replacements made.
    """
    if "old" not in body or "new" not in body:
        raise HTTPException(status_code=422, detail="Body must contain 'old' and 'new' keys")
    writer = WorkspaceWriter()
    count = writer.patch_text(UI_TEMPLATE_PATH, body["old"], body["new"])
    return {"replacements": count}


@app.post("/api/kai/write-ui")
async def write_ui(
    body: dict[str, str] = Body(...),
) -> dict[str, str]:
    """
    K.A.I. fully rewrites its UI with fresh HTML.  Body: ``{"content": "..."}``.
    """
    if "content" not in body:
        raise HTTPException(status_code=422, detail="Body must contain 'content' key")
    writer = WorkspaceWriter()
    writer.write_text(UI_TEMPLATE_PATH, body["content"])
    return {"status": "ok"}


@app.post("/api/kai/seed-belief")
async def seed_belief(
    body: dict[str, str] = Body(...),
) -> dict[str, str]:
    """Store or update a permanent core memory belief."""
    block_id = body.get("block_id", "").strip()
    content = body.get("content", "").strip()
    if not block_id or not content:
        raise HTTPException(status_code=422, detail="Body must include non-empty 'block_id' and 'content'")
    _web_memory.store_core_belief(block_id, content)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start(host: str = "0.0.0.0", port: int | None = None) -> None:
    """Boot the Uvicorn server.  Called from main.py --serve."""
    uvicorn.run(app, host=host, port=port or config.WEB_PORT, log_level="info")


def start_background(host: str = "0.0.0.0", port: int | None = None) -> None:
    """Start the Uvicorn server in a background daemon thread.

    Returns immediately; the web server keeps running until the main process
    exits.  Used by ``main.py --with-serve`` to run the web UI alongside the
    interactive CLI or init-file mode.
    """
    import threading

    resolved_port = port or config.WEB_PORT

    def _run() -> None:
        uvicorn.run(app, host=host, port=resolved_port, log_level="info")

    thread = threading.Thread(target=_run, name="kai-webui", daemon=True)
    thread.start()
    logger.info("Web chat server started in background on port %d.", resolved_port)


if __name__ == "__main__":
    start()
