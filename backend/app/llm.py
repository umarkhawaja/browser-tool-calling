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

# How much context the agent's turns are given. Everything below exists because
# Ollama enforces this by truncating from the *front*, and says nothing when it
# does — so an overflowing run loses its system prompt and its task first, and
# carries on looking healthy while having forgotten what it was asked.
NUM_CTX = 8192

# Room for what a request carries besides the transcript: the tool schemas, and
# the reply the model still has to fit. Both come out of the same window.
_RESERVED_TOKENS = 1024

# Characters per token, deliberately pessimistic. Real text runs nearer four,
# but a transcript of URLs, element labels and JSON tokenises far worse, and
# guessing high here only costs a little history.
_CHARS_PER_TOKEN = 3

# The budget `fit_to_context` holds a transcript to, measured over the JSON that
# actually goes on the wire.
CONTEXT_BUDGET_CHARS = (NUM_CTX - _RESERVED_TOKENS) * _CHARS_PER_TOKEN


class LLMError(RuntimeError):
    """Raised when Ollama is unreachable or returns something unusable."""


# How many of the newest turns are kept word for word, budget permitting.
# Anything older is worth a line, not a page: the agent is told that only the
# most recent listing's indices are live, so an old one is bulk it cannot act on.
_KEEP_VERBATIM = 3

_ELIDED = "…(earlier result and page listing dropped to fit the context window)"

# What survives elision, and the length below which eliding would cost more in
# marker than it saves in text.
_SUMMARY_CHARS = 120
_ELIDE_ABOVE = 240


def _wire_size(messages: list[dict[str, Any]]) -> int:
    """What a transcript costs, measured over the JSON actually sent."""
    return len(json.dumps(messages, default=str))


def _elide(message: dict[str, Any]) -> dict[str, Any]:
    """Reduce one stale observation to a line saying what it was.

    The assistant's own messages are left alone: they are short, and they are
    the reasoning everything else hangs off. What goes is what was *observed* —
    page listings whose indices are refused by now, and extracted text that has
    already been reasoned about — which is all of the bulk.
    """
    content = message.get("content") or ""
    if message.get("role") == "assistant" or len(content) <= _ELIDE_ABOVE:
        return message
    opening = content.strip().split("\n", 1)[0][:_SUMMARY_CHARS]
    return {**message, "content": f"{opening}\n{_ELIDED}"}


def _turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group the transcript into units that are safe to drop whole.

    A tool result Ollama cannot trace back to a tool call is rejected, so each
    turn is one message plus the tool results answering it, and trimming works
    in turns rather than messages.
    """
    turns: list[list[dict[str, Any]]] = []
    for message in messages:
        if message.get("role") == "tool" and turns:
            turns[-1].append(message)
        else:
            turns.append([message])
    return turns


def _split(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    """Separate the pinned opening — system prompt and task — from the rest."""
    pinned = 0
    while pinned < len(messages) and messages[pinned].get("role") == "system":
        pinned += 1
    pinned = min(pinned + 1, len(messages))  # and the task itself
    return list(messages[:pinned]), _turns(messages[pinned:])


def _flatten(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [message for turn in turns for message in turn]


def fit_to_context(
    messages: list[dict[str, Any]], budget: int = CONTEXT_BUDGET_CHARS
) -> list[dict[str, Any]]:
    """Return `messages` cut down to something that fits the model's window.

    Left to itself Ollama enforces `num_ctx` by dropping from the front and
    saying nothing, so the first casualties are the system prompt and the task —
    and the run continues, competently following instructions it can no longer
    read. This decides what to lose instead, and loses the right end.

    The opening is pinned, and the newest turn is never elided or dropped: its
    listing is the one the model is about to act on. Everything older starts as
    a single line, and only then is what remains of the budget spent restoring
    recent turns to full text.

    That order is the whole design. Taking the recent turns whole *first* is the
    obvious reading of "keep the last N", and it is wrong: on a real page three
    verbatim turns fill the window between them, and every earlier step falls
    off the transcript entirely rather than costing a line. The model is then
    free to repeat work it has already done, having no record that it did.

    The caller keeps its full transcript; this is only the view sent to the
    model, recomputed each turn so the verbatim window slides forward with it.
    """
    if _wire_size(messages) <= budget:
        return list(messages)

    head, turns = _split(messages)
    if not turns:
        return list(messages)

    def assembled(chosen: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        return head + _flatten(chosen)

    # The floor: the newest turn whole, every older one down to a line.
    kept = [[_elide(m) for m in turn] for turn in turns[:-1]] + [turns[-1]]

    # If even that overflows, the oldest breadcrumbs are what go. `turns` is
    # popped alongside so the two stay aligned for the restoring pass below.
    while len(kept) > 1 and _wire_size(assembled(kept)) > budget:
        kept.pop(0)
        turns.pop(0)

    # Whatever is left over buys back full text, newest first.
    for depth in range(2, min(_KEEP_VERBATIM, len(kept)) + 1):
        candidate = list(kept)
        candidate[-depth] = turns[-depth]
        if _wire_size(assembled(candidate)) > budget:
            break
        kept = candidate

    return assembled(kept)


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
    conversation accumulates page listings + tool results every step; keeping it
    inside that window is `fit_to_context`, which the caller applies.
    """
    return await _stream_chat(
        {
            "model": MODEL,
            "messages": messages,
            "tools": tools,
            "options": {"temperature": 0.1, "num_ctx": NUM_CTX},
        },
        on_token,
    )
