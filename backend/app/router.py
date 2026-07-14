"""Decide whether a user message is plain chat or needs the browser.

One quick LLM call classifies each message so the user can just talk to the
agent in a single input box: greetings and questions get a direct reply, while
anything that needs the web spins up the browser agent.
"""
from __future__ import annotations

from typing import Any, Dict

from app.llm import chat_json

ROUTER_PROMPT = """You are the router for a local web-browsing assistant.
Decide whether the user's latest message needs live web browsing.

Reply with a SINGLE JSON object, nothing else:
- Plain conversation, greetings, or questions you can answer directly:
    {"mode": "chat", "reply": "<a short, friendly reply>"}
- Anything needing the web (search, look up, open a site, find, buy, book, check):
    {"mode": "browse"}

Examples:
  "hi"                              -> {"mode": "chat", "reply": "Hi! Ask me to look something up on the web."}
  "who are you?"                    -> {"mode": "chat", "reply": "I'm a local agent that can browse the web for you."}
  "top story on Hacker News"        -> {"mode": "browse"}
  "find flights to Tokyo in May"    -> {"mode": "browse"}
"""


async def route(message: str) -> Dict[str, Any]:
    """Return {"mode": "chat"|"browse", "reply": str}. Defaults to browse."""
    reply = await chat_json(
        [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": message},
        ]
    )
    mode = reply.get("mode")
    if mode not in ("chat", "browse"):
        mode = "browse"
    return {"mode": mode, "reply": reply.get("reply", "")}
