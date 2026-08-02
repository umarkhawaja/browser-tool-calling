"""The agent loop, driven by the model's native tool calling.

Given a user task, it repeatedly:
  1. asks the model what to do next (it may return a tool call),
  2. executes that tool in the browser,
  3. feeds the result — plus the resulting page (URL + numbered elements) — back
     as a tool message,
  4. streams a screenshot + narration to the frontend,
until the model stops calling tools and replies with a plain-text answer (or we
hit the step limit).

The loop can be paused between steps so a human can drive the browser directly —
to get past a login or a CAPTCHA — and then hand control back.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, Callable

from app.browser import BrowserSession
from app.config import MAX_STEPS
from app.llm import LLMError, chat_tools

SYSTEM_PROMPT = """You are a web-browsing agent that controls a real web browser \
to accomplish the user's task.

Use the provided tools to browse. Every tool result gives you the current page: \
its URL and a numbered list of interactive elements (links, buttons, inputs). \
Click or type using those indices.

Guidelines:
- To search or fill a field: input_text into it, then press_enter (or click a button).
- To read a page's text, headings, or article body, call extract_text.
- Cookie/consent dialogs are accepted automatically after you navigate. If a click
  keeps failing or a popup is covering the page, call dismiss_dialog once, then continue.
- Take as few steps as possible.
- Write your final answer in the SAME LANGUAGE the user used, even though the
  pages you read may be in another one.
- NEVER write a tool call as text or JSON in your message. Either call a tool
  through the tool interface, or, when you have enough to answer, STOP calling
  tools and reply with the final answer as plain language."""


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    """One tool in Ollama's function-schema format.

    The nesting is fixed boilerplate; only the four values here ever vary, so
    the list below reads as a table of what the agent can do.
    """
    parameters: dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        parameters["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


# The tools the model may call. `_execute` below dispatches on these names.
TOOLS: list[dict[str, Any]] = [
    _tool(
        "go_to_url",
        "Navigate the browser to a URL.",
        {"url": {"type": "string", "description": "The URL to open"}},
        ["url"],
    ),
    _tool(
        "click",
        "Click an interactive element by its index from the current page listing.",
        {"index": {"type": "integer", "description": "Element index"}},
        ["index"],
    ),
    _tool(
        "input_text",
        "Type text into an input element by its index.",
        {"index": {"type": "integer"}, "text": {"type": "string"}},
        ["index", "text"],
    ),
    _tool("press_enter", "Press the Enter key, e.g. to submit a search."),
    _tool(
        "scroll",
        "Scroll the page up or down.",
        {"direction": {"type": "string", "enum": ["up", "down"]}},
        ["direction"],
    ),
    _tool("extract_text", "Return the visible text of the current page."),
    _tool("dismiss_dialog", "Dismiss a cookie/consent popup that is blocking the page."),
]

# A callback the loop uses to push events to the WebSocket. Each event is a dict.
Emit = Callable[[dict[str, Any]], Awaitable[None]]


# How many elements the model is shown. Anything past this is unaddressable —
# it cannot be clicked, because the model never learns it exists.
MODEL_ELEMENT_LIMIT = 60


def _format_state(url: str, elements: list[dict[str, Any]]) -> str:
    """Render the current page as the text block we feed to the model."""
    lines = [f"Current URL: {url}", "Interactive elements:"]
    if not elements:
        lines.append("  (none detected)")
    for el in elements[:MODEL_ELEMENT_LIMIT]:
        tag = f"<{el['tag']}{(' type=' + el['type']) if el['type'] else ''}>"
        lines.append(f"  [{el['index']}] {tag} {el['label']}")
    return "\n".join(lines)


async def observe(browser: BrowserSession, emit: Emit | None = None) -> str:
    """Read the page once, for both of its readers.

    The model gets a numbered text listing. The UI, when `emit` is given, gets
    exactly the same elements with their rectangles, so it can draw what the
    model is reasoning over onto the live preview.

    Both are cut at the same limit on purpose: a preview showing more elements
    than the model was given would be a picture of a page it cannot actually
    act on. `total` is reported separately so the UI can say what was left out.
    """
    url, elements = browser.url(), await browser.elements()
    if emit is not None:
        await emit(
            {
                "type": "page",
                "url": url,
                "elements": elements[:MODEL_ELEMENT_LIMIT],
                "total": len(elements),
            }
        )
    return _format_state(url, elements)


async def _execute(browser: BrowserSession, name: str, args: dict[str, Any]) -> str:
    """Run one tool call and return a human-readable result."""
    if name == "go_to_url":
        return await browser.go_to_url(args["url"])
    if name == "click":
        return await browser.click(int(args["index"]))
    if name == "input_text":
        return await browser.input_text(int(args["index"]), args.get("text", ""))
    if name == "press_enter":
        return await browser.press_enter()
    if name == "scroll":
        return await browser.scroll(args.get("direction", "down"))
    if name == "extract_text":
        return await browser.extract_text()
    if name == "dismiss_dialog":
        return await browser.dismiss_overlays() or "No dialog found."
    return f"Unknown tool {name!r}."


def _parse_args(raw: Any) -> dict[str, Any]:
    """Ollama returns arguments as a dict, but tolerate a JSON string too."""
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw or {}


_TOOLISH_KEYS = {"name", "parameters", "arguments", "action", "tool", "tool_name"}


def _looks_like_tool_json(content: str) -> bool:
    """True if the model wrote a tool call as text instead of calling it.

    Small models sometimes emit a JSON blob like {"name": "extract_text", ...}
    in the message body with no real tool_calls; we must not treat that as the
    final answer.
    """
    text = content.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return False
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and bool(_TOOLISH_KEYS & set(obj))


NUDGE = (
    "Do not write tool calls as text. Use the provided tools through the tool "
    "interface, or give a plain-language final answer. To read the page, use "
    "extract_text."
)

RESUMED = (
    "The human took control of the browser and may have changed the page. "
    "Continue the task from what you see now."
)


async def _wait_for_human(
    gate: asyncio.Event, browser: BrowserSession, emit: Emit
) -> dict[str, Any]:
    """Block until the human hands the browser back, then re-read the page.

    Whatever the model last saw is stale by then — they may have navigated or
    filled something in — so it resumes from a fresh observation, not memory.
    """
    await emit({"type": "status", "text": "paused"})
    await gate.wait()
    await emit({"type": "status", "text": "running"})
    return {"role": "user", "content": f"{RESUMED}\n\n{await observe(browser, emit)}"}


async def _run_tool_call(
    call: dict[str, Any], browser: BrowserSession, emit: Emit
) -> dict[str, Any]:
    """Execute one tool call and build the message reporting it back."""
    function = call.get("function", {})
    name = function.get("name", "")
    try:
        result = await _execute(browser, name, _parse_args(function.get("arguments")))
    except Exception as e:  # surface any browser failure back to the model
        result = f"Action failed: {e}"

    await emit({"type": "action", "text": name, "detail": result[:300]})
    await emit({"type": "screenshot", "data": await browser.screenshot_b64()})
    return {
        "role": "tool",
        "tool_name": name,
        "content": f"{result}\n\n{await observe(browser, emit)}",
    }


async def run_agent(
    task: str,
    browser: BrowserSession,
    emit: Emit,
    gate: asyncio.Event | None = None,
) -> None:
    """Drive the browser until the task is answered.

    `gate`, when given, is held set while the agent may run; clearing it pauses
    the loop at the next step boundary (a tool call already in flight finishes
    first). Stop still works while paused — cancellation raises inside the wait.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Task: {task}\n\n{await observe(browser, emit)}"},
    ]
    await emit({"type": "screenshot", "data": await browser.screenshot_b64()})

    async def on_token(delta: str) -> None:
        await emit({"type": "token", "text": delta})

    for _ in range(MAX_STEPS):
        if gate is not None and not gate.is_set():
            messages.append(await _wait_for_human(gate, browser, emit))

        try:
            message = await chat_tools(messages, TOOLS, on_token)
        except LLMError as e:
            await emit({"type": "error", "text": str(e)})
            return
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()

        if not tool_calls:
            # No tool call means the model is answering — unless it fumbled one
            # into the message body, in which case nudge it and try again.
            if _looks_like_tool_json(content):
                # Those tokens already streamed to the UI as if they were an
                # answer; drop them rather than leaving the blob on screen.
                await emit({"type": "token_reset"})
                messages.append({"role": "user", "content": NUDGE})
                continue
            await emit({"type": "answer", "text": content or "(no answer)"})
            return

        if content:  # any reasoning the model included alongside its tool call
            await emit({"type": "thought", "text": content})

        for call in tool_calls:
            messages.append(await _run_tool_call(call, browser, emit))

    await emit({"type": "answer", "text": "Reached the step limit without finishing."})
