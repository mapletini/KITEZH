"""
llm_backends.py — Local LLM backend helpers for Kitezh.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

import config

logger = logging.getLogger("kitezh.llm_backends")


def send_to_ollama(prompt: str, model: str | None = None) -> str:
    """Send *prompt* to the Ollama REST API and return the generated text."""
    target_model = model or config.OLLAMA_MODEL
    url = f"{config.OLLAMA_BASE_URL}/api/generate"
    payload: dict[str, Any] = {
        "model": target_model,
        "prompt": prompt,
        "stream": False,
    }

    logger.info("Sending prompt to Ollama (model=%s, url=%s)", target_model, url)
    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "")
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot connect to Ollama at '{config.OLLAMA_BASE_URL}'. "
            "Is the Ollama server running?"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Ollama request failed: {exc}") from exc


def send_to_letta(prompt: str, agent_id: str | None = None) -> str:
    """Send *prompt* to the Letta REST API and return the assistant's reply."""
    target_agent = agent_id or config.LETTA_AGENT_ID
    if not target_agent:
        raise RuntimeError(
            "KITEZH_LETTA_AGENT_ID is not set. "
            "Set it via the environment variable or --agent-id flag."
        )

    url = f"{config.LETTA_BASE_URL}/v1/agents/{target_agent}/messages"
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}]
    }

    logger.info("Sending prompt to Letta (agent=%s, url=%s)", target_agent, url)
    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        messages = data.get("messages", [])
        for msg in messages:
            if msg.get("role") == "assistant":
                return msg.get("content", "")
        return str(data)
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot connect to Letta at '{config.LETTA_BASE_URL}'. "
            "Is the Letta server running?"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Letta request failed: {exc}") from exc


def send_to_llamacpp(prompt: str, model: str | None = None) -> str:
    """Send *prompt* to a llama.cpp OpenAI-compatible endpoint and return text."""
    target_model = model or config.LLAMACPP_MODEL
    url = f"{config.LLAMACPP_BASE_URL}/v1/chat/completions"
    payload: dict[str, Any] = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
    }

    logger.info("Sending prompt to llama.cpp (model=%s, url=%s)", target_model, url)
    try:
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            return str(data)
        message = choices[0].get("message", {})
        return message.get("content", "") or str(data)
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"Cannot connect to llama.cpp server at '{config.LLAMACPP_BASE_URL}'. "
            "Is llama-server running?"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"llama.cpp request failed: {exc}") from exc


def send_to_backend(
    prompt: str,
    backend: str | None = None,
    model: str | None = None,
    agent_id: str | None = None,
) -> str:
    """Send *prompt* to the configured local backend."""
    target_backend = backend or config.LLM_BACKEND
    if target_backend == "ollama":
        return send_to_ollama(prompt, model=model)
    if target_backend == "llamacpp":
        return send_to_llamacpp(prompt, model=model)
    if target_backend == "letta":
        return send_to_letta(prompt, agent_id=agent_id)
    raise RuntimeError(f"Unsupported backend: {target_backend}")
