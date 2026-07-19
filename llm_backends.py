"""
llm_backends.py — Local LLM backend helpers for Kitezh.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

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
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"Ollama at '{config.OLLAMA_BASE_URL}' returned invalid JSON."
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
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"Letta at '{config.LETTA_BASE_URL}' returned invalid JSON."
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
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"llama.cpp server at '{config.LLAMACPP_BASE_URL}' returned invalid JSON."
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


def chat_with_tools_llamacpp(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_executor: Callable[[str, dict[str, Any]], str] | None = None,
    model: str | None = None,
    max_tool_iterations: int = 8,
) -> str:
    """
    Agentic chat loop with optional tool calling for the llama.cpp backend.

    Sends a multi-turn conversation to the llama-server's OpenAI-compatible
    ``/v1/chat/completions`` endpoint.  When the model issues tool calls and a
    ``tool_executor`` callable is provided, each tool is executed and its
    result is appended as a ``tool`` role message before re-querying the model.
    The loop repeats until the model returns a plain text response or
    ``max_tool_iterations`` is exhausted.

    Parameters
    ----------
    messages:
        Ordered list of ``{"role": ..., "content": ...}`` dicts representing
        the conversation so far (not including any system message).
    system:
        Optional system prompt prepended to the request messages.
    tools:
        OpenAI-format tool definitions to expose to the model.
    tool_executor:
        Callable ``(name, arguments) → str`` that executes a tool call and
        returns the result as a string.  When *None* the loop stops at the
        first tool-call response.
    model:
        Model name override (default: ``config.LLAMACPP_MODEL``).
    max_tool_iterations:
        Hard cap on tool-call rounds to prevent infinite loops.
    """
    target_model = model or config.LLAMACPP_MODEL
    url = f"{config.LLAMACPP_BASE_URL}/v1/chat/completions"

    # Build the initial message list (system prompt + conversation history)
    full_messages: list[dict[str, Any]] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    payload: dict[str, Any] = {
        "model": target_model,
        "messages": full_messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    logger.info(
        "Starting agentic chat with llama.cpp (model=%s, url=%s, tools=%d)",
        target_model,
        url,
        len(tools) if tools else 0,
    )

    for iteration in range(max_tool_iterations + 1):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Cannot connect to llama.cpp server at '{config.LLAMACPP_BASE_URL}'. "
                "Is llama-server running?"
            ) from exc
        except requests.exceptions.JSONDecodeError as exc:
            raise RuntimeError(
                f"llama.cpp server at '{config.LLAMACPP_BASE_URL}' returned invalid JSON."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"llama.cpp request failed: {exc}") from exc

        choices = data.get("choices", [])
        if not choices:
            return str(data)

        message = choices[0].get("message", {})
        finish_reason = choices[0].get("finish_reason", "stop")
        tool_calls = message.get("tool_calls")

        # No tool calls — return the final text response
        if not tool_calls or finish_reason != "tool_calls":
            return message.get("content", "") or str(data)

        # Tool calls received but no executor or iteration cap hit
        if tool_executor is None or iteration >= max_tool_iterations:
            logger.warning(
                "Tool calls received but %s; returning partial response.",
                "no executor provided" if tool_executor is None else "max iterations reached",
            )
            return message.get("content", "") or "[Tool call not executed]"

        # Append assistant's tool-call turn to the conversation
        payload["messages"].append(message)

        # Execute each tool call and feed results back
        for tc in tool_calls:
            tc_id = tc.get("id", "call_0")
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            fn_args_raw = fn.get("arguments", "{}")
            try:
                fn_args: dict[str, Any] = json.loads(fn_args_raw) if fn_args_raw else {}
            except json.JSONDecodeError:
                fn_args = {}
            logger.info("Executing tool: %s(%s)", fn_name, fn_args)
            try:
                result = tool_executor(fn_name, fn_args)
            except Exception as exc:
                result = f"Tool execution error: {exc}"
                logger.error("Tool '%s' raised: %s", fn_name, exc)
            payload["messages"].append(
                {
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": str(result),
                }
            )

    return "[Max tool iterations reached without final response]"
