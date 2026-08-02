"""Tests for the chat/browse router. httpx is mocked with respx.

These cover the plumbing — how a classifier reply is interpreted. Whether the
model actually classifies well is a property of the prompt, not of this code,
and is measured by the routing eval (see docs) against a live Ollama.
"""

import json

import httpx
import respx

from app.config import OLLAMA_URL, ROUTER_MODEL
from app.router import _classifier_messages, chat_reply, route


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": content}})


def _stream(*deltas: str) -> httpx.Response:
    """An NDJSON stream of content deltas, the way Ollama sends them."""
    lines = [json.dumps({"message": {"content": d}, "done": False}) for d in deltas]
    lines.append(json.dumps({"message": {"content": ""}, "done": True}))
    return httpx.Response(200, text="\n".join(lines) + "\n")


# --- route -----------------------------------------------------------------
@respx.mock
async def test_routes_conversation_to_chat():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": "chat"}'))
    assert await route("hello") == {"mode": "chat"}


@respx.mock
async def test_routes_task_to_browse():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": "browse"}'))
    assert (await route("find the top Hacker News story"))["mode"] == "browse"


@respx.mock
async def test_unknown_mode_defaults_to_browse():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": "???"}'))
    assert (await route("do a thing"))["mode"] == "browse"


@respx.mock
async def test_missing_mode_defaults_to_browse():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"other": 1}'))
    assert (await route("do a thing"))["mode"] == "browse"


@respx.mock
async def test_mode_is_normalised():
    # Small models like to shout, and to pad with whitespace.
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": "  CHAT "}'))
    assert (await route("hello"))["mode"] == "chat"


@respx.mock
async def test_non_string_mode_defaults_to_browse():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_reply('{"mode": ["chat"]}'))
    assert (await route("hello"))["mode"] == "browse"


@respx.mock
async def test_every_message_reaches_the_classifier():
    # There is no keyword short-circuit any more: the old regex forced a browse
    # on "today"/"find"/"open" without ever consulting the model.
    calls = respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_reply('{"mode": "chat"}')
    )
    assert (await route("how are you today?"))["mode"] == "chat"
    assert calls.call_count == 1


@respx.mock
async def test_classifier_uses_the_router_model():
    route_call = respx.post(f"{OLLAMA_URL}/api/chat").mock(
        return_value=_reply('{"mode": "chat"}')
    )
    await route("hi")
    assert json.loads(route_call.calls[0].request.content)["model"] == ROUTER_MODEL


# --- the prompt the classifier actually sees --------------------------------
def test_classifier_prompt_is_few_shot_and_ends_with_the_message():
    msgs = _classifier_messages("¿qué hora es?")
    assert msgs[0]["role"] == "system"
    assert msgs[-1] == {"role": "user", "content": "¿qué hora es?"}
    # Examples are paired user/assistant turns, all answering with a bare mode.
    assert len(msgs) > 3 and len(msgs) % 2 == 0
    for m in msgs[2:-1:2]:
        assert json.loads(m["content"])["mode"] in ("chat", "browse")


def test_classifier_examples_cover_several_languages():
    joined = " ".join(m["content"] for m in _classifier_messages("x"))
    # Latin-script non-English, plus three non-Latin scripts.
    assert "wie geht es dir?" in joined
    assert any(ord(c) > 0x0590 for c in joined), "no non-Latin script examples"


# --- chat_reply ------------------------------------------------------------
@respx.mock
async def test_chat_reply_streams_and_returns_full_text():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_stream("Hi", " there", "!"))
    seen = []

    async def on_token(delta):
        seen.append(delta)

    assert await chat_reply("hello", on_token) == "Hi there!"
    assert seen == ["Hi", " there", "!"]


@respx.mock
async def test_chat_reply_works_without_a_token_callback():
    respx.post(f"{OLLAMA_URL}/api/chat").mock(return_value=_stream("yes"))
    assert await chat_reply("hello") == "yes"
