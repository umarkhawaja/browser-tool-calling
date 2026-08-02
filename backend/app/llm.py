"""Minimal async client for a local Ollama model."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from typing import Any, Callable

import httpx

from app.config import MODEL, OLLAMA_URL

# Streaming lets us bound the *gap between chunks* rather than the whole call,
# so a long answer never trips a deadline just for being long.
TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)

# Called with each text delta as it arrives, so the UI can render tokens live.
OnToken = Callable[[str], Awaitable[None]]


class LLMError(RuntimeError):
    """Raised when Ollama is unreachable or returns something unusable."""


async def _stream_chat(
    payload: dict[str, Any], on_token: OnToken | None
) -> dict[str, Any]:
    """POST to /api/chat with stream=True and reassemble the NDJSON chunks.

    Returns a single assistant message dict — the same shape a non-streaming
    call returns — so callers stay unaware that the response arrived in pieces.
    """
    content: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    try:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT) as client,
            client.stream(
                "POST", f"{OLLAMA_URL}/api/chat", json={**payload, "stream": True}
            ) as resp,
        ):
            if resp.status_code >= 400:
                await resp.aread()  # a streamed body must be read before raising
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate a stray keep-alive / partial line
                if chunk.get("error"):
                    raise LLMError(f"Ollama error: {chunk['error']}")

                message = chunk.get("message") or {}
                delta = message.get("content") or ""
                if delta:
                    content.append(delta)
                    if on_token is not None:
                        await on_token(delta)
                # Ollama emits each tool call complete in a single chunk
                # (arguments already a dict), so collecting them is enough —
                # there are no partial argument fragments to stitch.
                if message.get("tool_calls"):
                    tool_calls.extend(message["tool_calls"])
    except httpx.HTTPError as e:
        raise LLMError(f"Could not reach Ollama at {OLLAMA_URL}: {e}") from e

    assembled: dict[str, Any] = {"role": "assistant", "content": "".join(content)}
    if tool_calls:  # omit the key entirely when empty; it is fed back to Ollama
        assembled["tool_calls"] = tool_calls
    return assembled


async def chat_json(
    messages: list[dict[str, str]], model: str | None = None
) -> dict[str, Any]:
    """Ask the model for one reply and return it parsed as JSON.

    `format="json"` makes Ollama emit strictly valid JSON, so the router's
    decision protocol stays robust without any hand-written parsing. This one
    stays non-streaming: a half-built JSON object is of no use to anyone.
    `model` lets the caller pick a smaller one for cheap classification work.
    """
    payload = {
        "model": model or MODEL,
        "messages": messages,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Could not reach Ollama at {OLLAMA_URL}: {e}") from e

    content = resp.json()["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model did not return valid JSON: {content!r}") from e


async def chat_text(
    messages: list[dict[str, Any]], on_token: OnToken | None = None
) -> str:
    """Stream a plain-language reply and return the finished text."""
    message = await _stream_chat(
        {"model": MODEL, "messages": messages, "options": {"temperature": 0.3}},
        on_token,
    )
    return message["content"].strip()


async def chat_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_token: OnToken | None = None,
) -> dict[str, Any]:
    """Ask the model for its next step using native tool calling.

    Returns the assistant message dict, which contains `content` and, when the
    model wants to act, a `tool_calls` list. `num_ctx` is raised because the
    conversation accumulates page listings + tool results every step.
    """
    return await _stream_chat(
        {
            "model": MODEL,
            "messages": messages,
            "tools": tools,
            "options": {"temperature": 0.1, "num_ctx": 8192},
        },
        on_token,
    )
