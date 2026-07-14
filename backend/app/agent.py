"""The agent loop.

Given a user task, it repeatedly:
  1. shows the model the current page (URL + numbered interactive elements),
  2. asks it for ONE next action as JSON,
  3. executes that action in the browser,
  4. streams a screenshot + narration to the frontend,
until the model calls `done` or we hit the step limit.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List

from app.browser import BrowserSession
from app.config import MAX_STEPS
from app.llm import LLMError, chat_json

SYSTEM_PROMPT = """You are a web-browsing agent. You control a real web browser to \
accomplish the user's task.

On every turn you receive the current page: its URL and a numbered list of \
interactive elements (links, buttons, inputs). You must reply with a SINGLE JSON \
object choosing exactly ONE action. Do not add any text outside the JSON.

Available actions (JSON shapes):
  {"thought": "...", "action": "go_to_url", "url": "https://..."}
  {"thought": "...", "action": "click", "index": <int>}
  {"thought": "...", "action": "input_text", "index": <int>, "text": "..."}
  {"thought": "...", "action": "press_enter"}
  {"thought": "...", "action": "scroll", "direction": "down"|"up"}
  {"thought": "...", "action": "extract_text"}
  {"thought": "...", "action": "dismiss_dialog"}
  {"thought": "...", "action": "done", "answer": "final answer to the user"}

Rules:
- "thought" is a short explanation of why you chose this action.
- Only use element indices that appear in the current page listing.
- To search or fill a field: input_text into it, then press_enter (or click a button).
- Cookie/consent dialogs are accepted automatically after you navigate. If a
  click keeps failing or a popup is covering the page, use "dismiss_dialog"
  once, then continue.
- When you have enough information to answer, use the "done" action.
- Prefer as few steps as possible.
"""

# A callback the loop uses to push events to the WebSocket. Each event is a dict.
Emit = Callable[[Dict[str, Any]], Awaitable[None]]


def _format_state(url: str, elements: List[Dict[str, Any]]) -> str:
    """Render the current page as the text block we feed to the model."""
    lines = [f"Current URL: {url}", "Interactive elements:"]
    if not elements:
        lines.append("  (none detected)")
    for el in elements[:60]:
        tag = f"<{el['tag']}{(' type=' + el['type']) if el['type'] else ''}>"
        lines.append(f"  [{el['index']}] {tag} {el['label']}")
    return "\n".join(lines)


async def _execute(browser: BrowserSession, reply: Dict[str, Any]) -> str:
    """Run the single action the model chose and return a human-readable result."""
    action = reply.get("action", "")
    if action == "go_to_url":
        return await browser.go_to_url(reply["url"])
    if action == "click":
        return await browser.click(int(reply["index"]))
    if action == "input_text":
        return await browser.input_text(int(reply["index"]), reply.get("text", ""))
    if action == "press_enter":
        return await browser.press_enter()
    if action == "scroll":
        return await browser.scroll(reply.get("direction", "down"))
    if action == "extract_text":
        return await browser.extract_text()
    if action == "dismiss_dialog":
        return await browser.dismiss_overlays() or "No dialog found."
    return f"Unknown action {action!r}."


async def run_agent(task: str, browser: BrowserSession, emit: Emit) -> None:
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Task: {task}"},
    ]

    for _ in range(MAX_STEPS):
        # 1. Observe the current page and show it to the model + the user.
        state = _format_state(browser.url(), await browser.elements())
        await emit({"type": "screenshot", "data": await browser.screenshot_b64()})
        messages.append({"role": "user", "content": state})

        # 2. Ask the model for the next action.
        try:
            reply = await chat_json(messages)
        except LLMError as e:
            await emit({"type": "error", "text": str(e)})
            return
        messages.append({"role": "assistant", "content": json.dumps(reply)})

        if reply.get("thought"):
            await emit({"type": "thought", "text": reply["thought"]})

        # 3. The model can finish at any point.
        if reply.get("action") == "done":
            await emit({"type": "answer", "text": reply.get("answer", "(no answer)")})
            return

        # 4. Otherwise execute the action and feed the result back in.
        try:
            result = await _execute(browser, reply)
        except Exception as e:  # surface any browser failure back to the model
            result = f"Action failed: {e}"
        await emit({"type": "action", "text": reply.get("action", ""), "detail": result[:300]})
        messages.append({"role": "user", "content": f"Result: {result}"})

    await emit({"type": "answer", "text": "Reached the step limit without finishing."})
