"""Tests for the agent loop. The LLM and the browser are both faked, so these run
without Ollama or a real Chromium."""

import asyncio
import contextlib

from app import agent
from app.agent import _format_state, run_agent
from app.llm import LLMError


class FakeBrowser:
    """Records every action the agent invokes; no real browser involved."""

    def __init__(self):
        self.calls = []
        self._url = "about:blank"

    async def elements(self):
        return [{"index": 0, "tag": "input", "type": "text", "label": "Search"}]

    async def screenshot_b64(self):
        return "AAAA"

    def url(self):
        return self._url

    async def go_to_url(self, url):
        self.calls.append(("go_to_url", url))
        self._url = url if url.startswith("http") else "https://" + url
        return f"Navigated to {self._url}"

    async def click(self, index):
        self.calls.append(("click", index))
        return f"Clicked {index}"

    async def input_text(self, index, text):
        self.calls.append(("input_text", index, text))
        return "typed"

    async def press_enter(self):
        self.calls.append(("press_enter",))
        return "enter"

    async def scroll(self, direction="down"):
        self.calls.append(("scroll", direction))
        return "scrolled"

    async def extract_text(self):
        self.calls.append(("extract_text",))
        return "page text"

    async def dismiss_overlays(self):
        self.calls.append(("dismiss_overlays",))
        return "Accepted a cookie/consent dialog ('I agree')"


def _scripted(replies, seen=None):
    """Return a fake chat_tools that hands back the given messages in order.

    `seen`, if given, collects the transcript each call was made with, so tests
    can assert on what the model was actually told.
    """
    queue = list(replies)

    async def fake_chat(messages, tools, on_token=None):
        if seen is not None:
            seen.append(list(messages))
        reply = queue.pop(0)
        if on_token is not None and reply.get("content"):
            await on_token(reply["content"])  # mimic streaming the text out
        return reply

    return fake_chat


def _tool_call(name, args, content=""):
    """An assistant message that calls one tool (native tool-calling shape)."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [{"function": {"name": name, "arguments": args}}],
    }


def _final(text):
    """An assistant message with no tool call -> the final answer."""
    return {"role": "assistant", "content": text}


# --- _format_state ---------------------------------------------------------
def test_format_state_lists_elements():
    element = {"index": 0, "tag": "a", "type": "", "label": "Home"}
    s = _format_state("https://x.com", [element])
    assert "Current URL: https://x.com" in s
    assert "[0]" in s
    assert "Home" in s


def test_format_state_handles_no_elements():
    assert "(none detected)" in _format_state("about:blank", [])


# --- run_agent -------------------------------------------------------------
async def test_run_agent_executes_tools_then_finishes(monkeypatch):
    monkeypatch.setattr(
        agent,
        "chat_tools",
        _scripted(
            [
                _tool_call("go_to_url", {"url": "example.com"}),
                _tool_call("input_text", {"index": 0, "text": "hello"}),
                _final("All set"),
            ]
        ),
    )
    events = []

    async def emit(e):
        events.append(e)

    browser = FakeBrowser()
    await run_agent("do a thing", browser, emit)

    assert ("go_to_url", "example.com") in browser.calls
    assert ("input_text", 0, "hello") in browser.calls

    kinds = [e["type"] for e in events]
    assert "screenshot" in kinds
    answers = [e for e in events if e["type"] == "answer"]
    assert answers and answers[0]["text"] == "All set"


async def test_run_agent_parses_stringified_arguments(monkeypatch):
    # Some models return tool arguments as a JSON string instead of an object.
    monkeypatch.setattr(
        agent,
        "chat_tools",
        _scripted(
            [
                _tool_call("click", '{"index": 3}'),
                _final("done"),
            ]
        ),
    )
    events = []

    async def emit(e):
        events.append(e)

    browser = FakeBrowser()
    await run_agent("x", browser, emit)
    assert ("click", 3) in browser.calls


async def test_run_agent_nudges_on_toolcall_written_as_text(monkeypatch):
    # First reply fumbles a tool call into the message body (no tool_calls);
    # the loop should nudge and continue rather than answer with the JSON.
    monkeypatch.setattr(
        agent,
        "chat_tools",
        _scripted(
            [
                _final('{"name": "get_element_text", "parameters": {"index": 0}}'),
                _final("The heading is Example Domain"),
            ]
        ),
    )
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)
    answers = [e for e in events if e["type"] == "answer"]
    assert len(answers) == 1
    assert answers[0]["text"] == "The heading is Example Domain"


async def test_run_agent_surfaces_llm_error(monkeypatch):
    async def boom(messages, tools, on_token=None):
        raise LLMError("no ollama")

    monkeypatch.setattr(agent, "chat_tools", boom)
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)
    assert any(e["type"] == "error" for e in events)


async def test_run_agent_can_dismiss_dialog(monkeypatch):
    monkeypatch.setattr(
        agent,
        "chat_tools",
        _scripted(
            [
                _tool_call("dismiss_dialog", {}),
                _final("ok"),
            ]
        ),
    )
    events = []

    async def emit(e):
        events.append(e)

    browser = FakeBrowser()
    await run_agent("x", browser, emit)
    assert ("dismiss_overlays",) in browser.calls
    dismiss = [
        e for e in events if e["type"] == "action" and e["text"] == "dismiss_dialog"
    ]
    assert dismiss and "cookie/consent" in dismiss[0]["detail"]


async def test_run_agent_handles_unknown_tool(monkeypatch):
    monkeypatch.setattr(
        agent,
        "chat_tools",
        _scripted(
            [
                _tool_call("frobnicate", {}),
                _final("ok"),
            ]
        ),
    )
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)
    action_events = [e for e in events if e["type"] == "action"]
    assert any("Unknown tool" in e.get("detail", "") for e in action_events)


# --- streaming -------------------------------------------------------------
async def test_run_agent_streams_tokens_before_the_answer(monkeypatch):
    monkeypatch.setattr(agent, "chat_tools", _scripted([_final("All set")]))
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)

    kinds = [e["type"] for e in events]
    assert kinds.index("token") < kinds.index("answer")
    assert "".join(e["text"] for e in events if e["type"] == "token") == "All set"


async def test_nudged_tool_json_tokens_are_discarded(monkeypatch):
    # The fumbled JSON streams to the UI before we know it isn't an answer, so
    # the loop must tell the client to throw those tokens away.
    monkeypatch.setattr(
        agent,
        "chat_tools",
        _scripted(
            [
                _final('{"name": "get_element_text", "parameters": {"index": 0}}'),
                _final("The heading is Example Domain"),
            ]
        ),
    )
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)
    kinds = [e["type"] for e in events]
    assert "token_reset" in kinds
    assert kinds.index("token_reset") < kinds.index("answer")


# --- pause gate ------------------------------------------------------------
async def test_run_agent_waits_while_the_human_holds_control(monkeypatch):
    seen = []
    monkeypatch.setattr(agent, "chat_tools", _scripted([_final("done")], seen))
    events = []

    async def emit(e):
        events.append(e)

    gate = asyncio.Event()  # cleared -> the human has taken the browser
    task = asyncio.create_task(run_agent("x", FakeBrowser(), emit, gate))
    await asyncio.sleep(0.05)

    assert {"type": "status", "text": "paused"} in events
    assert seen == [], "the model must not be consulted while paused"

    gate.set()
    await task

    assert any(e["type"] == "answer" for e in events)
    # On resume the model is told the page may have moved under it.
    resumed = seen[-1][-1]
    assert resumed["role"] == "user"
    assert "took control" in resumed["content"]


async def test_run_agent_runs_straight_through_when_gate_is_set(monkeypatch):
    monkeypatch.setattr(agent, "chat_tools", _scripted([_final("done")]))
    events = []

    async def emit(e):
        events.append(e)

    gate = asyncio.Event()
    gate.set()
    await run_agent("x", FakeBrowser(), emit, gate)

    assert not any(e.get("text") == "paused" for e in events)
    assert any(e["type"] == "answer" for e in events)


async def test_stop_works_while_paused(monkeypatch):
    monkeypatch.setattr(agent, "chat_tools", _scripted([_final("done")]))

    async def emit(e):
        pass

    gate = asyncio.Event()  # never set
    task = asyncio.create_task(run_agent("x", FakeBrowser(), emit, gate))
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
