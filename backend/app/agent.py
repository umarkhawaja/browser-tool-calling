"""The agent loop, driven by the model's native tool calling.

Given a user task, it repeatedly:
  1. asks the model what to do next (it may return a tool call),
  2. executes that tool in the browser,
  3. feeds the result — plus the resulting page (URL + numbered elements) — back
     as a tool message,
  4. streams a screenshot + narration to the frontend,
until the model stops calling tools and replies with a plain-text answer (or we
hit the step limit).
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List

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
- NEVER write a tool call as text or JSON in your message. Either call a tool
  through the tool interface, or, when you have enough to answer, STOP calling
  tools and reply with the final answer as plain language."""

# The tools the model may call, in Ollama's function-schema format.
TOOLS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "go_to_url",
        "description": "Navigate the browser to a URL.",
        "parameters": {"type": "object",
            "properties": {"url": {"type": "string", "description": "The URL to open"}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "click",
        "description": "Click an interactive element by its index from the current page listing.",
        "parameters": {"type": "object",
            "properties": {"index": {"type": "integer", "description": "Element index"}},
            "required": ["index"]}}},
    {"type": "function", "function": {
        "name": "input_text",
        "description": "Type text into an input element by its index.",
        "parameters": {"type": "object",
            "properties": {"index": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["index", "text"]}}},
    {"type": "function", "function": {
        "name": "press_enter",
        "description": "Press the Enter key, e.g. to submit a search.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "scroll",
        "description": "Scroll the page up or down.",
        "parameters": {"type": "object",
            "properties": {"direction": {"type": "string", "enum": ["up", "down"]}},
            "required": ["direction"]}}},
    {"type": "function", "function": {
        "name": "extract_text",
        "description": "Return the visible text of the current page.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "dismiss_dialog",
        "description": "Dismiss a cookie/consent popup that is blocking the page.",
        "parameters": {"type": "object", "properties": {}}}},
]

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


async def _observe(browser: BrowserSession) -> str:
    return _format_state(browser.url(), await browser.elements())


async def _execute(browser: BrowserSession, name: str, args: Dict[str, Any]) -> str:
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


def _parse_args(raw: Any) -> Dict[str, Any]:
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


async def run_agent(task: str, browser: BrowserSession, emit: Emit) -> None:
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Task: {task}\n\n{await _observe(browser)}"},
    ]
    await emit({"type": "screenshot", "data": await browser.screenshot_b64()})

    for _ in range(MAX_STEPS):
        # 1. Ask the model for its next step.
        try:
            message = await chat_tools(messages, TOOLS)
        except LLMError as e:
            await emit({"type": "error", "text": str(e)})
            return
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        content = (message.get("content") or "").strip()

        # 2. No tool call -> the model is giving its final answer, unless it
        #    fumbled a tool call into the message body — then nudge and retry.
        if not tool_calls:
            if _looks_like_tool_json(content):
                messages.append({
                    "role": "user",
                    "content": ("Do not write tool calls as text. Use the provided "
                                "tools through the tool interface, or give a "
                                "plain-language final answer. To read the page, "
                                "use extract_text."),
                })
                continue
            await emit({"type": "answer", "text": content or "(no answer)"})
            return

        if content:  # any reasoning the model included alongside its tool call
            await emit({"type": "thought", "text": content})

        # 3. Run each tool call, then feed the result + new page state back in.
        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            args = _parse_args(fn.get("arguments"))
            try:
                result = await _execute(browser, name, args)
            except Exception as e:  # surface any browser failure back to the model
                result = f"Action failed: {e}"
            await emit({"type": "action", "text": name, "detail": result[:300]})
            await emit({"type": "screenshot", "data": await browser.screenshot_b64()})
            messages.append({
                "role": "tool",
                "tool_name": name,
                "content": f"{result}\n\n{await _observe(browser)}",
            })

    await emit({"type": "answer", "text": "Reached the step limit without finishing."})
