"""Tests for the chat/browse router. httpx is mocked with respx."""
import httpx
import respx

from app.config import OLLAMA_URL
from app.router import route


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


@respx.mock
async def test_routes_conversation_to_chat():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_reply('{"mode": "chat", "reply": "hi there"}')
    )
    assert await route("hello") == {"mode": "chat", "reply": "hi there"}


@respx.mock
async def test_routes_task_to_browse():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": "browse"}'))
    out = await route("find the top Hacker News story")
    assert out["mode"] == "browse"


@respx.mock
async def test_unknown_mode_defaults_to_browse():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": "???"}'))
    assert (await route("do a thing"))["mode"] == "browse"


# The keyword/domain guardrail short-circuits to browse without any LLM call.
async def test_keyword_forces_browse():
    out = await route("Give me a summary of top stories on hackernoon")
    assert out["mode"] == "browse"


async def test_domain_forces_browse():
    out = await route("open example.com and read it")
    assert out["mode"] == "browse"
