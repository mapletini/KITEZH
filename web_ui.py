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

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Body
from fastapi.responses import HTMLResponse

import config
from skills.filesystem import WorkspaceWriter, WorkspaceReader

logger = logging.getLogger(__name__)

# Path inside the workspace where K.A.I.'s UI template lives.
UI_TEMPLATE_PATH = "ui/index.html"

# Dedicated SQLite file for the chat log (separate from deep memory).
_CHAT_DB_PATH = Path(config.WORKSPACE_PATH) / "chat_log.db"


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
                content      TEXT    NOT NULL
            )
        """)
        conn.commit()


def _save_message(user_id: str, display_name: str, content: str) -> None:
    with _chat_conn() as conn:
        conn.execute(
            "INSERT INTO messages (timestamp, user_id, display_name, content) VALUES (?,?,?,?)",
            (int(time.time()), user_id, display_name, content),
        )
        conn.commit()


def _fetch_messages(limit: int = 100) -> list[dict[str, Any]]:
    with _chat_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------------------
# Multi-user WebSocket connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Tracks all active WebSocket sessions and broadcasts to every one of them."""

    def __init__(self) -> None:
        # user_id → (display_name, WebSocket)
        self._active: dict[str, tuple[str, WebSocket]] = {}

    async def connect(self, user_id: str, display_name: str, ws: WebSocket) -> None:
        await ws.accept()
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

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        dead: list[str] = []
        for uid, (_, ws) in self._active.items():
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(uid)
        for uid in dead:
            self._active.pop(uid, None)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

_init_chat_db()
app = FastAPI(title="K.A.I. Chat Interface", docs_url=None, redoc_url=None)
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
async def chat_log(limit: int = 100) -> dict[str, Any]:
    """Return the most recent *limit* chat messages."""
    return {"messages": _fetch_messages(limit)}


@app.get("/api/chat/users")
async def online_users() -> dict[str, Any]:
    """Return the list of currently connected users."""
    return {"count": manager.user_count, "users": manager.user_list()}


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(ws: WebSocket, user_id: str, display_name: str = "Anonymous") -> None:
    """
    Real-time chat endpoint.  Connect with::

        ws://<host>:<port>/ws/<user_id>?display_name=<name>

    On connect the client receives a ``history`` frame containing the last 50
    messages, then live ``message`` and ``system`` frames.
    """
    await manager.connect(user_id, display_name, ws)

    # Send chat history to the newly connected client.
    await ws.send_text(json.dumps({"type": "history", "messages": _fetch_messages(50)}))

    # Announce the arrival to all users.
    await manager.broadcast({
        "type": "system",
        "content": f"⬡ {display_name} connected",
        "users": manager.user_list(),
    })

    try:
        while True:
            raw = await ws.receive_text()
            # Strip whitespace; ignore empty frames.
            content = raw.strip()
            if not content:
                continue
            _save_message(user_id, display_name, content)
            await manager.broadcast({
                "type": "message",
                "user_id": user_id,
                "display_name": display_name,
                "content": content,
                "timestamp": int(time.time()),
            })
    except WebSocketDisconnect:
        name = manager.disconnect(user_id)
        await manager.broadcast({
            "type": "system",
            "content": f"⬡ {name or display_name} disconnected",
            "users": manager.user_list(),
        })


# ── K.A.I. self-edit endpoints (require x-ai-key) ────────────────────────


def _require_key(x_ai_key: str) -> None:
    if x_ai_key != config.AI_KEY:
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def start(host: str = "0.0.0.0", port: int | None = None) -> None:
    """Boot the Uvicorn server.  Called from main.py --serve."""
    uvicorn.run(app, host=host, port=port or config.WEB_PORT, log_level="info")


if __name__ == "__main__":
    start()
