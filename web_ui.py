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
import json
import logging
import requests
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from itertools import combinations
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Body
from fastapi.responses import HTMLResponse

import config
from skills.cognitive_architect import LLMCognitiveBridge
from skills.deep_memory import DeepMemoryCore
from skills.filesystem import WorkspaceWriter, WorkspaceReader
from skills.neuro_affect import NeuroChemicalEngine

logger = logging.getLogger(__name__)

# Path inside the workspace where K.A.I.'s UI template lives.
UI_TEMPLATE_PATH = "ui/index.html"

# Dedicated SQLite file for the chat log (separate from deep memory).
_CHAT_DB_PATH = Path(config.WORKSPACE_PATH) / "chat_log.db"
_MAX_ARCHIVED_MESSAGE_LENGTH = 200
_DREAM_CONSOLIDATION_INTERVAL_SECONDS = 3600
_DREAM_CONSOLIDATION_INTERACTION_FREQUENCY = 10

# Core cognition state for web mode.
_web_memory = DeepMemoryCore(workspace_path=config.WORKSPACE_PATH)
_web_neuro = NeuroChemicalEngine()
_web_cognitive = LLMCognitiveBridge(_web_memory, _web_neuro)
_web_interaction_count = 0

_CONCEPT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
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
        conn.commit()


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


def _query_kai(user_id: str, display_name: str, content: str) -> str:
    payload = {
        "platform": "web",
        "user_id": user_id,
        "display_name": display_name,
        "content": content,
        "clearance": "guest",
        "is_puppy": False,
        "metadata": {},
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
    else:
        _web_neuro.apply_stimulus(reward=0.1, success=0.2, user_id=user_id)
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

    _web_interaction_count += 1
    if _web_interaction_count % _DREAM_CONSOLIDATION_INTERACTION_FREQUENCY == 0:
        _web_memory.execute_dream_consolidation()


async def _dream_consolidation_daemon() -> None:
    while True:
        await asyncio.sleep(_DREAM_CONSOLIDATION_INTERVAL_SECONDS)
        _web_memory.execute_dream_consolidation()
        logger.info("Background dream consolidation complete.")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

_init_chat_db()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    background_task = asyncio.create_task(_dream_consolidation_daemon())
    try:
        yield
    finally:
        background_task.cancel()
        with suppress(asyncio.CancelledError):
            await background_task


app = FastAPI(title="K.A.I. Chat Interface", docs_url=None, redoc_url=None, lifespan=_lifespan)
manager = ConnectionManager()


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


@app.get("/api/chat/users")
async def online_users() -> dict[str, Any]:
    """Return the list of currently connected users."""
    return {"count": manager.user_count, "users": manager.user_list()}


@app.get("/api/kai/emotion")
async def emotion_state() -> dict[str, Any]:
    """Return K.A.I.'s current emotion snapshot from web-mode neuro state."""
    return {"emotion": _web_neuro.emotion_snapshot()}


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(ws: WebSocket, user_id: str, display_name: str = "Anonymous") -> None:
    """
    Real-time chat endpoint.  Connect with::

        ws://<host>:<port>/ws/<user_id>?display_name=<name>

    On connect the client receives a ``history`` frame containing the last 50
    messages from its own private thread, then live ``message`` frames.
    """
    await manager.connect(user_id, display_name, ws)

    # Send private history to this user only.
    await ws.send_text(json.dumps({"type": "history", "messages": _fetch_messages(user_id, 50)}))

    try:
        while True:
            raw = await ws.receive_text()
            # Strip whitespace; ignore empty frames.
            content = raw.strip()
            if not content:
                continue
            timestamp = int(time.time())
            _save_message(user_id, user_id, display_name, content, channel="user")
            await manager.send_to(user_id, {
                "type": "message",
                "user_id": user_id,
                "display_name": display_name,
                "content": content,
                "timestamp": timestamp,
            })

            kai_reply = _query_kai(user_id, display_name, content)
            kai_timestamp = int(time.time())
            _save_message(user_id, "kai", "K.A.I.", kai_reply, channel="kai")
            await manager.send_to(user_id, {
                "type": "message",
                "user_id": "kai",
                "display_name": "K.A.I.",
                "content": kai_reply,
                "timestamp": kai_timestamp,
            })
            _process_web_cognitive_loop(user_id, display_name, content, kai_reply)
    except WebSocketDisconnect:
        manager.disconnect(user_id)


# ── K.A.I. self-edit endpoints (require x-ai-key) ────────────────────────


def _require_key(x_ai_key: str) -> None:
    if not isinstance(x_ai_key, str):
        raise HTTPException(status_code=400, detail="Bad Request — malformed AI key")
    if not isinstance(config.AI_KEY, str):
        raise HTTPException(status_code=503, detail="AI key not configured on server")
    if config.AI_KEY in config.INSECURE_AI_KEYS:
        raise HTTPException(status_code=503, detail="AI key not configured on server")
    if not secrets.compare_digest(x_ai_key, config.AI_KEY):
        raise HTTPException(status_code=403, detail="Forbidden — invalid AI key")


@app.get("/api/kai/read-ui")
async def read_ui(x_ai_key: str = Header(...)) -> dict[str, str]:
    """K.A.I. reads the full source of its own chat interface."""
    _require_key(x_ai_key)
    reader = WorkspaceReader()
    if not reader.exists(UI_TEMPLATE_PATH):
        raise HTTPException(status_code=404, detail="UI template not found")
    return {"content": reader.read_text(UI_TEMPLATE_PATH)}


@app.post("/api/kai/patch-ui")
async def patch_ui(
    body: dict[str, str] = Body(...),
    x_ai_key: str = Header(...),
) -> dict[str, Any]:
    """
    K.A.I. replaces a substring in its UI.  Body: ``{"old": "...", "new": "..."}``.
    Returns the number of replacements made.
    """
    _require_key(x_ai_key)
    if "old" not in body or "new" not in body:
        raise HTTPException(status_code=422, detail="Body must contain 'old' and 'new' keys")
    writer = WorkspaceWriter()
    count = writer.patch_text(UI_TEMPLATE_PATH, body["old"], body["new"])
    return {"replacements": count}


@app.post("/api/kai/write-ui")
async def write_ui(
    body: dict[str, str] = Body(...),
    x_ai_key: str = Header(...),
) -> dict[str, str]:
    """
    K.A.I. fully rewrites its UI with fresh HTML.  Body: ``{"content": "..."}``.
    """
    _require_key(x_ai_key)
    if "content" not in body:
        raise HTTPException(status_code=422, detail="Body must contain 'content' key")
    writer = WorkspaceWriter()
    writer.write_text(UI_TEMPLATE_PATH, body["content"])
    return {"status": "ok"}


@app.post("/api/kai/seed-belief")
async def seed_belief(
    body: dict[str, str] = Body(...),
    x_ai_key: str = Header(...),
) -> dict[str, str]:
    """Store or update a permanent core memory belief."""
    _require_key(x_ai_key)
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


if __name__ == "__main__":
    start()
