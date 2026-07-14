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

from browser import BrowserSession
from llm import LLMError, chat_json

MAX_STEPS = 15

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
  {"thought": "...", "action": "done", "answer": "final answer to the user"}

Rules:
- "thought" is a short explanation of why you chose this action.
- Only use element indices that appear in the current page listing.
- To search or fill a field: input_text into it, then press_enter (or click a button).
- When you have enough information to answer, use the "done" action.
- Prefer as few steps as possible.
"""

# A callback the loop uses to push events to the WebSocket. Each event is a dict.
Emit = Callable[[Dict[str, Any]], Awaitable[None]]


def _format_state(url: str, elements: List[Dict[str, Any]]) -> str:
    lines = [f"Current URL: {url}", "Interactive elements:"]
    if not elements:
        lines.append("  (none detected)")
    for el in elements[:60]:
        t = f"<{el['tag']}{(' type=' + el['type']) if el['type'] else ''}>"
        lines.append(f"  [{el['index']}] {t} {el['label']}")
    return "\n".join(lines)


async def run_agent(task: str, browser: BrowserSession, emit: Emit) -> None:
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Task: {task}"},
    ]

    for step in range(1, MAX_STEPS + 1):
        # 1. Observe current page and show it to the model + the user.
        elements = await browser.elements()
        state = _format_state(browser.url(), elements)
        await emit({"type": "screenshot", "data": await browser.screenshot_b64()})
        messages.append({"role": "user", "content": state})

        # 2. Ask the model for the next action.
        try:
            reply = await chat_json(messages)
        except LLMError as e:
            await emit({"type": "error", "text": str(e)})
            return
        messages.append({"role": "assistant", "content": json.dumps(reply)})

        action = reply.get("action", "")
        thought = reply.get("thought", "")
        if thought:
            await emit({"type": "thought", "text": thought})

        # 3. Execute the chosen action.
        try:
            if action == "done":
                await emit({"type": "answer", "text": reply.get("answer", "(no answer)")})
                return
            elif action == "go_to_url":
                result = await browser.go_to_url(reply["url"])
            elif action == "click":
                result = await browser.click(int(reply["index"]))
            elif action == "input_text":
                result = await browser.input_text(int(reply["index"]), reply.get("text", ""))
            elif action == "press_enter":
                result = await browser.press_enter()
            elif action == "scroll":
                result = await browser.scroll(reply.get("direction", "down"))
            elif action == "extract_text":
                result = await browser.extract_text()
            else:
                result = f"Unknown action {action!r}."
        except Exception as e:  # surface any browser failure back to the model
            result = f"Action failed: {e}"

        await emit({"type": "action", "text": f"{action}", "detail": result[:300]})
        messages.append({"role": "user", "content": f"Result: {result}"})

    await emit({"type": "answer", "text": "Reached the step limit without finishing."})
