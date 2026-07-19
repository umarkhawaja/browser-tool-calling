"""Minimal async client for a local Ollama model."""
from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx

from app.config import MODEL, OLLAMA_URL


class LLMError(RuntimeError):
    """Raised when Ollama is unreachable or returns something unusable."""


async def chat_json(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Ask the model for one reply and return it parsed as JSON.

    `format="json"` makes Ollama emit strictly valid JSON, so the agent's
    action protocol stays robust without any hand-written parsing.
    """
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Could not reach Ollama at {OLLAMA_URL}: {e}") from e

    content = resp.json()["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model did not return valid JSON: {content!r}") from e


async def chat_tools(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Ask the model for its next step using native tool calling.

    Returns the raw assistant message dict, which contains `content` and, when
    the model wants to act, a `tool_calls` list. `num_ctx` is raised because the
    conversation accumulates page listings + tool results every step.
    """
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Could not reach Ollama at {OLLAMA_URL}: {e}") from e

    return resp.json()["message"]
