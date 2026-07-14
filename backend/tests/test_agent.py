"""Tests for the agent loop. The LLM and the browser are both faked, so these run
without Ollama or a real Chromium."""
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


def _scripted(replies):
    """Return a fake chat_json that hands back the given replies in order."""
    queue = list(replies)

    async def fake_chat(messages):
        return queue.pop(0)

    return fake_chat


# --- _format_state ---------------------------------------------------------
def test_format_state_lists_elements():
    s = _format_state("https://x.com", [{"index": 0, "tag": "a", "type": "", "label": "Home"}])
    assert "Current URL: https://x.com" in s
    assert "[0]" in s
    assert "Home" in s


def test_format_state_handles_no_elements():
    assert "(none detected)" in _format_state("about:blank", [])


# --- run_agent -------------------------------------------------------------
async def test_run_agent_executes_actions_then_finishes(monkeypatch):
    monkeypatch.setattr(agent, "chat_json", _scripted([
        {"thought": "go", "action": "go_to_url", "url": "example.com"},
        {"thought": "type", "action": "input_text", "index": 0, "text": "hello"},
        {"thought": "done", "action": "done", "answer": "All set"},
    ]))
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


async def test_run_agent_surfaces_llm_error(monkeypatch):
    async def boom(messages):
        raise LLMError("no ollama")

    monkeypatch.setattr(agent, "chat_json", boom)
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)
    assert any(e["type"] == "error" for e in events)


async def test_run_agent_can_dismiss_dialog(monkeypatch):
    monkeypatch.setattr(agent, "chat_json", _scripted([
        {"action": "dismiss_dialog"},
        {"action": "done", "answer": "ok"},
    ]))
    events = []

    async def emit(e):
        events.append(e)

    browser = FakeBrowser()
    await run_agent("x", browser, emit)
    assert ("dismiss_overlays",) in browser.calls
    dismiss = [e for e in events if e["type"] == "action" and e["text"] == "dismiss_dialog"]
    assert dismiss and "cookie/consent" in dismiss[0]["detail"]


async def test_run_agent_handles_unknown_action(monkeypatch):
    monkeypatch.setattr(agent, "chat_json", _scripted([
        {"action": "frobnicate"},
        {"action": "done", "answer": "ok"},
    ]))
    events = []

    async def emit(e):
        events.append(e)

    await run_agent("x", FakeBrowser(), emit)
    action_events = [e for e in events if e["type"] == "action"]
    assert any("Unknown action" in e.get("detail", "") for e in action_events)
