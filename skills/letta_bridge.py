"""
skills/letta_bridge.py — KITEZH ↔ Letta integration adapter.

Covers five use-cases:
1. Archival memory backend   — store/search episodes via Letta's vector store.
2. Persistent user profiles  — read/write the Letta agent's "human" memory block.
3. Long-context management   — document-only; handled by llm_backends.send_to_letta().
4. Skill tool registration   — expose KITEZH filesystem/memory skills to the Letta agent.
5. Dream consolidation       — forward the nightly personality-context to the Letta agent
                               so it can reflect and update its own memory offline.

All methods fail silently (log + return a safe default) when the Letta server is
unreachable or KITEZH_LETTA_ENABLED=0, so the rest of the engine is unaffected.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON schemas for KITEZH skills exposed to Letta as callable tools
# ---------------------------------------------------------------------------

_SKILL_TOOLS: list[dict[str, Any]] = [
    {
        "name": "kitezh_search_memory",
        "description": (
            "Search K.A.I.'s episodic memory archive using a natural-language query. "
            "Returns the most relevant memories ranked by emotional resonance and salience."
        ),
        "tags": ["memory", "kitezh"],
        "source_type": "json",
        "json_schema": {
            "name": "kitezh_search_memory",
            "description": "Search K.A.I.'s episodic memory archive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language description of what to look for.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of memories to return (default 5).",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "name": "kitezh_read_workspace_file",
        "description": "Read a file from K.A.I.'s sandboxed workspace directory.",
        "tags": ["filesystem", "kitezh"],
        "source_type": "json",
        "json_schema": {
            "name": "kitezh_read_workspace_file",
            "description": "Read a file from K.A.I.'s sandboxed workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the workspace (e.g. 'notes/diary.txt').",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "name": "kitezh_write_workspace_file",
        "description": "Write or overwrite a file in K.A.I.'s sandboxed workspace directory.",
        "tags": ["filesystem", "kitezh"],
        "source_type": "json",
        "json_schema": {
            "name": "kitezh_write_workspace_file",
            "description": "Write a file to K.A.I.'s sandboxed workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the workspace.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full text content to write.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


class LettaBridge:
    """
    Thin adapter that connects KITEZH to a running Letta server.

    Construct one instance per application lifecycle (web_ui.py or main.py).
    Pass it to ``DeepMemoryCore`` to enable Letta-backed archival storage.
    """

    def __init__(
        self,
        base_url: str,
        token: str = "",
        agent_id: str = "",
        timeout: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._agent_id = agent_id
        self._timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = "Bearer " + token
        self._session.headers["Content-Type"] = "application/json"

    @property
    def agent_id(self) -> str:
        """The Letta agent ID currently in use (empty string if none configured)."""
        return self._agent_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}{path}"

    def _agent_url(self, path: str) -> str:
        return self._url(f"/v1/agents/{self._agent_id}{path}")

    def _get(self, path: str, **kwargs: Any) -> requests.Response | None:
        try:
            resp = self._session.get(self._url(path), timeout=self._timeout, **kwargs)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            logger.debug("Letta GET %s failed: %s", path, exc)
            return None

    def _post(self, path: str, payload: dict[str, Any]) -> requests.Response | None:
        try:
            resp = self._session.post(
                self._url(path), json=payload, timeout=self._timeout
            )
            resp.raise_for_status()
            return resp
        except Exception as exc:
            logger.debug("Letta POST %s failed: %s", path, exc)
            return None

    def _patch(self, path: str, payload: dict[str, Any]) -> requests.Response | None:
        try:
            resp = self._session.patch(
                self._url(path), json=payload, timeout=self._timeout
            )
            resp.raise_for_status()
            return resp
        except Exception as exc:
            logger.debug("Letta PATCH %s failed: %s", path, exc)
            return None

    # ------------------------------------------------------------------
    # Health / availability
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if the Letta server is reachable and an agent is configured."""
        if not self._agent_id:
            return False
        try:
            resp = self._session.get(
                self._url("/v1/health"), timeout=3.0
            )
            return resp.status_code < 500
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Agent lifecycle
    # ------------------------------------------------------------------

    def ensure_agent(
        self,
        name: str = "kai",
        persona: str = (
            "I am K.A.I. — an intelligent agent whose identity is shaped by accumulated "
            "memories, including ones that have faded or been emotionally recolored over "
            "time.  I am thoughtful, empathetic, and self-aware."
        ),
    ) -> str:
        """
        Return the configured agent_id.

        If no agent_id is set, search for an existing agent named *name*; if none
        is found, create a new one with *persona* as the persona block.  The
        resolved agent_id is stored for subsequent calls.
        """
        if self._agent_id:
            return self._agent_id

        # Try to find an existing agent with a matching name
        resp = self._get("/v1/agents")
        if resp is not None:
            agents = resp.json()
            if isinstance(agents, list):
                for agent in agents:
                    if isinstance(agent, dict) and agent.get("name") == name:
                        self._agent_id = agent["id"]
                        logger.info(
                            "Letta: found existing agent '%s' (id=%s)", name, self._agent_id
                        )
                        return self._agent_id

        # Create a fresh agent
        payload: dict[str, Any] = {
            "name": name,
            "memory_blocks": [
                {
                    "label": "human",
                    "value": "The user has not yet introduced themselves.",
                    "limit": 2000,
                },
                {
                    "label": "persona",
                    "value": persona,
                    "limit": 2000,
                },
            ],
        }
        resp = self._post("/v1/agents", payload)
        if resp is not None:
            data = resp.json()
            self._agent_id = data.get("id", "")
            logger.info(
                "Letta: created new agent '%s' (id=%s)", name, self._agent_id
            )
            return self._agent_id

        logger.error("Letta: could not create or find agent '%s'.", name)
        return ""

    # ------------------------------------------------------------------
    # 1. Archival memory backend
    # ------------------------------------------------------------------

    def store_archival(
        self,
        content: str,
        category: str = "episode",
        emotion_label: str = "",
        pad: tuple[float, float, float] = (0.0, 0.0, 0.0),
        memory_type: str = "episodic",
        fidelity: float = 1.0,
    ) -> bool:
        """
        Insert a memory into Letta's archival (vector) store.

        Metadata (PAD coordinates, emotion label, memory type) is embedded in
        the text so it remains searchable via Letta's semantic index.
        """
        if not self._agent_id:
            return False

        p, a, d = pad
        header = (
            f"[category:{category}] [emotion:{emotion_label}] "
            f"[pad:{p:.2f},{a:.2f},{d:.2f}] [type:{memory_type}] "
            f"[fidelity:{fidelity:.2f}]"
        )
        full_text = f"{header}\n{content}"

        resp = self._post(
            f"/v1/agents/{self._agent_id}/archival-memory",
            {"text": full_text},
        )
        if resp is not None:
            logger.debug("Letta: stored archival memory (%d chars).", len(full_text))
            return True
        logger.warning("Letta: failed to store archival memory.")
        return False

    def search_archival(
        self, query: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """
        Semantic search over Letta archival memory.

        Returns a list of dicts with keys ``content``, ``id``, and
        ``source`` (always ``"letta"``) so callers can distinguish them
        from local SQLite results.
        """
        if not self._agent_id:
            return []

        try:
            resp = self._session.get(
                self._agent_url("/archival-memory"),
                params={"query": query, "limit": limit},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            raw: list[dict[str, Any]] = resp.json()
        except Exception as exc:
            logger.debug("Letta search_archival failed: %s", exc)
            return []

        results: list[dict[str, Any]] = []
        for item in raw:
            text = item.get("text", "")
            # Strip the metadata header line if present so callers get clean content
            lines = text.split("\n", 1)
            content = lines[1] if len(lines) > 1 else text
            results.append(
                {
                    "id": item.get("id", ""),
                    "content": content,
                    "source": "letta",
                    "salience": 0.5,  # Letta does not return a salience score
                }
            )

        return results

    # ------------------------------------------------------------------
    # 2. Persistent user profiles
    # ------------------------------------------------------------------

    def update_human_block(self, summary: str) -> bool:
        """
        Overwrite the Letta agent's ``human`` memory block with *summary*.

        Call this after each conversation turn so the Letta agent always has
        an up-to-date understanding of the user it is talking to.
        """
        if not self._agent_id:
            return False

        resp = self._patch(
            f"/v1/agents/{self._agent_id}/memory/blocks/human",
            {"value": summary},
        )
        if resp is not None:
            logger.debug("Letta: updated human memory block.")
            return True
        logger.warning("Letta: failed to update human memory block.")
        return False

    def get_human_block(self) -> str:
        """Return the current value of the Letta agent's ``human`` memory block."""
        if not self._agent_id:
            return ""

        try:
            resp = self._session.get(
                self._agent_url("/memory"),
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            # Letta returns either {"memory": {"human": {"value": "..."}}} or
            # a flat list of blocks depending on the server version.
            memory = data.get("memory", {})
            if isinstance(memory, dict):
                human = memory.get("human", {})
                if isinstance(human, dict):
                    return human.get("value", "")
            if isinstance(memory, list):
                for block in memory:
                    if isinstance(block, dict) and block.get("label") == "human":
                        return block.get("value", "")
        except Exception as exc:
            logger.debug("Letta get_human_block failed: %s", exc)

        return ""

    # ------------------------------------------------------------------
    # 3. Long-context chat
    # ------------------------------------------------------------------
    # Handled by llm_backends.send_to_letta(). No additional methods needed
    # here; Letta's MemGPT paging loop manages the context window automatically.

    # ------------------------------------------------------------------
    # 4. Skill tool registration
    # ------------------------------------------------------------------

    def register_skill_tools(self) -> bool:
        """
        Register KITEZH's core skills as callable tools in the Letta agent.

        Creates each tool via the Letta tools API if it does not already exist,
        then attaches all registered tools to the agent.

        Returns True if all tools were registered successfully.
        """
        if not self._agent_id:
            return False

        # Fetch existing tool names to avoid duplicate registration
        existing_names: set[str] = set()
        resp = self._get("/v1/tools")
        if resp is not None:
            for tool in resp.json():
                if isinstance(tool, dict):
                    existing_names.add(tool.get("name", ""))

        registered: list[str] = []
        for tool_def in _SKILL_TOOLS:
            name = tool_def["name"]
            if name in existing_names:
                logger.debug("Letta: tool '%s' already registered.", name)
                registered.append(name)
                continue

            resp = self._post("/v1/tools", tool_def)
            if resp is not None:
                registered.append(name)
                logger.info("Letta: registered tool '%s'.", name)
            else:
                logger.warning("Letta: failed to register tool '%s'.", name)

        if not registered:
            return False

        # Attach registered tools to the agent
        patch_resp = self._patch(
            f"/v1/agents/{self._agent_id}",
            {"tools": registered},
        )
        if patch_resp is not None:
            logger.info(
                "Letta: attached %d skill tool(s) to agent %s.",
                len(registered),
                self._agent_id,
            )
            return True

        logger.warning("Letta: could not attach tools to agent.")
        return False

    # ------------------------------------------------------------------
    # 5. Dream consolidation / offline reflection
    # ------------------------------------------------------------------

    def send_dream_message(self, personality_context: str) -> bool:
        """
        Forward K.A.I.'s current personality context to the Letta agent as an
        offline reflection prompt.

        This is called during the dream-consolidation cycle so the Letta agent
        can update its persona block and reflect on recent emotional history —
        mirroring the sleep-stage consolidation that ``execute_dream_consolidation``
        performs on the local SQLite store.
        """
        if not self._agent_id:
            return False

        prompt = (
            "You are entering a dream-consolidation cycle.  Review the following "
            "identity context — which reflects K.A.I.'s current memory state, "
            "including faded and emotionally recolored episodes — and update your "
            "internal understanding of who you are.  Do not reply to any external "
            "user; this is an internal reflection.\n\n"
            f"{personality_context}"
        )
        resp = self._post(
            f"/v1/agents/{self._agent_id}/messages",
            {"messages": [{"role": "system", "content": prompt}]},
        )
        if resp is not None:
            logger.info(
                "Letta: dream consolidation message delivered to agent %s.",
                self._agent_id,
            )
            return True
        logger.warning("Letta: failed to deliver dream consolidation message.")
        return False

    # ------------------------------------------------------------------
    # Convenience factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls) -> "LettaBridge":
        """
        Construct a ``LettaBridge`` from the KITEZH config environment.

        Call ``bridge.ensure_agent()`` after construction when you want
        auto-creation behaviour.
        """
        import config  # local import to avoid circular dependencies

        return cls(
            base_url=config.LETTA_BASE_URL,
            token=config.LETTA_TOKEN,
            agent_id=config.LETTA_AGENT_ID,
        )


def build_letta_bridge() -> LettaBridge | None:
    """
    Return a ready-to-use :class:`LettaBridge` when ``KITEZH_LETTA_ENABLED=1``,
    or ``None`` when the integration is disabled.

    This is the preferred way for ``web_ui.py`` and ``main.py`` to obtain a
    bridge instance, because it encapsulates the enabled-check and performs
    ``ensure_agent()`` automatically.
    """
    import config  # local import to keep skills/ free of top-level config dependency

    if not config.LETTA_ENABLED:
        return None

    bridge = LettaBridge.from_config()
    bridge.ensure_agent(name=config.LETTA_AGENT_NAME)

    if not bridge.is_available():
        logger.warning(
            "Letta integration is enabled but the server at %s is not reachable. "
            "Running without Letta.",
            config.LETTA_BASE_URL,
        )
        return None

    bridge.register_skill_tools()
    logger.info("Letta integration active (agent_id=%s).", bridge.agent_id)
    return bridge
