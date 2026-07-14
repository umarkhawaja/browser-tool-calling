"""Decide whether a user message is plain chat or needs the browser.

A cheap deterministic check catches obvious web requests (a URL, or words like
"search", "latest", "top stories", "summary of"…). Anything else goes to a quick
LLM classifier that is biased toward browsing and forbidden from fabricating.
This keeps the single-input UX while making misroutes rare.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from app.llm import chat_json

# High-signal phrases that almost always mean "use the web".
_BROWSE_TERMS = (
    "search", "google", "look up", "lookup", "find ", "browse", "open ",
    "go to", "navigate", "visit", "latest", "top stor", "news", "headline",
    "trending", "summar", "current", "today", "right now", "live ",
    "price", "buy ", "book ", "order", "cheapest", "weather", "http",
)
_DOMAIN_RE = re.compile(r"\b[\w-]+\.(com|org|net|io|dev|ai|co|news|gov|edu)\b")


def _looks_like_browse(message: str) -> bool:
    text = message.lower()
    if _DOMAIN_RE.search(text):
        return True
    return any(term in text for term in _BROWSE_TERMS)


ROUTER_PROMPT = """You are the router for a local web-browsing assistant.
Decide whether the user's latest message needs live web browsing.

Reply with a SINGLE JSON object, nothing else:
- {"mode": "browse"}                         to look something up or act on the web
- {"mode": "chat", "reply": "<short reply>"}  for greetings, small talk, or questions
                                              about yourself that you can answer directly

Rules:
- Choose "browse" whenever the answer depends on the live web: current events,
  news, prices, or the contents of any specific website.
- Choose "chat" ONLY for greetings, small talk, or questions about who you are
  or what you can do.
- NEVER fabricate facts, summaries, or links, and never use placeholders like
  "[insert ...]". If you cannot answer from your own general knowledge, browse.
- When unsure, choose "browse".

Examples:
  "hi"                              -> {"mode": "chat", "reply": "Hi! Ask me to look something up on the web."}
  "what can you do?"               -> {"mode": "chat", "reply": "I can browse the web for you — search, read pages, fill forms."}
  "top stories on Hacker News"      -> {"mode": "browse"}
  "summary of today's tech news"    -> {"mode": "browse"}
"""


async def route(message: str) -> Dict[str, Any]:
    """Return {"mode": "chat"|"browse", "reply": str}. Defaults to browse."""
    if _looks_like_browse(message):
        return {"mode": "browse", "reply": ""}

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
