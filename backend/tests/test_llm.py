"""Tests for the Ollama chat client. httpx is mocked with respx, so these run
without a live Ollama server."""

import json

import httpx
import pytest
import respx

from app.config import OLLAMA_URL
from app.llm import LLMError, chat_json, chat_tools


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def _ndjson(*chunks: dict) -> httpx.Response:
    """Ollama's streaming wire format: one JSON object per line."""
    lines = [json.dumps(c) for c in chunks]
    return httpx.Response(200, text="\n".join(lines) + "\n")


@respx.mock
async def test_parses_valid_json():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_reply('{"action": "done", "answer": "hi"}')
    )
    out = await chat_json([{"role": "user", "content": "x"}])
    assert out == {"action": "done", "answer": "hi"}


@respx.mock
async def test_invalid_json_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply("not json at all"))
    with pytest.raises(LLMError):
        await chat_json([{"role": "user", "content": "x"}])


@respx.mock
async def test_connection_failure_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(LLMError):
        await chat_json([{"role": "user", "content": "x"}])


# --- chat_tools: streaming reassembly --------------------------------------
async def _collect(messages=None, tools=None):
    seen = []

    async def on_token(delta):
        seen.append(delta)

    msg = await chat_tools(messages or [], tools or [], on_token)
    return msg, seen


@respx.mock
async def test_stream_reassembles_text_and_reports_tokens():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ndjson(
            {"message": {"content": "The top "}, "done": False},
            {"message": {"content": "story is Rust."}, "done": False},
            {"message": {"content": ""}, "done": True},
        )
    )
    msg, seen = await _collect()
    assert msg["content"] == "The top story is Rust."
    assert seen == ["The top ", "story is Rust."]
    # No tool call was made, so the key is absent rather than an empty list —
    # this message gets fed straight back to Ollama on the next turn.
    assert "tool_calls" not in msg


@respx.mock
async def test_stream_collects_tool_calls():
    # Ollama delivers each tool call whole, in its own chunk.
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ndjson(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "go_to_url",
                                "arguments": {"url": "https://x.com"},
                            }
                        }
                    ],
                },
                "done": False,
            },
            {"message": {"content": ""}, "done": True},
        )
    )
    msg, seen = await _collect()
    assert msg["tool_calls"][0]["function"]["name"] == "go_to_url"
    assert msg["tool_calls"][0]["function"]["arguments"] == {"url": "https://x.com"}
    assert seen == []  # nothing to show the user on a pure tool-call turn


@respx.mock
async def test_stream_tolerates_blank_and_malformed_lines():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=httpx.Response(
            200,
            text='{"message": {"content": "ok"}, "done": false}\n'
            "\n"
            "not json\n"
            '{"message": {"content": ""}, "done": true}\n',
        )
    )
    msg, _ = await _collect()
    assert msg["content"] == "ok"


@respx.mock
async def test_stream_error_line_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_ndjson({"error": "model not found"})
    )
    with pytest.raises(LLMError, match="model not found"):
        await chat_tools([], [])


@respx.mock
async def test_stream_http_error_raises_llmerror():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(LLMError):
        await chat_tools([], [])
