"""Tests for the Ollama JSON chat client. httpx is mocked with respx, so these
run without a live Ollama server."""
import httpx
import pytest
import respx

from app.config import OLLAMA_URL
from app.llm import LLMError, chat_json


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


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
