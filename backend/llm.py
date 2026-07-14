"""Thin async client for a local Ollama model (llama3 by default)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MODEL", "llama3.1")


class LLMError(RuntimeError):
    pass


async def chat_json(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Send a chat request to Ollama and return the assistant reply parsed as JSON.

    We use Ollama's `format: "json"` so the model is constrained to emit valid
    JSON, which makes the agent's action protocol robust without any parsing
    gymnastics on our side.
    """
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach Ollama at {OLLAMA_URL}: {e}") from e

    content = resp.json()["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model did not return valid JSON: {content!r}") from e
