"""Decide whether a user message is plain chat or needs the browser.

A small classifier model reads the message and answers with one word. It
replaced a keyword/domain regex, which was wrong in both directions: it could
only see English, and it matched on substrings, so "how are you today?" tripped
on "today" and opened a browser while "busca las últimas noticias" looked like
small talk. Meaning is the thing being judged here, and that is a model's job.

Classifying and answering are two separate calls. The classifier returns only a
mode, which keeps it short, cheap and unable to fabricate; `chat_reply` then
streams the actual sentence back as plain text.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import ROUTER_MODEL
from app.llm import OnToken, chat_json, chat_text

ROUTER_PROMPT = """You are the router for a local web-browsing assistant. Decide \
whether answering the user's message requires opening a web browser right now.

Reply with a SINGLE JSON object and nothing else:
  {"mode": "browse"}   the answer depends on the live web
  {"mode": "chat"}     you can handle it in conversation, with no browser

Choose "browse" when the message:
- names a site, URL or domain, or asks to open, visit or search one
- asks for anything current: news, prices, weather, scores, schedules, availability
- asks you to act on a site: search, fill a form, sign in, buy, book, download
- asks about any real-world event or result — a match, an election, a release —
  no matter how recently it happened
- ties anything outside this conversation to a time near now: today, tonight,
  this week, recently, latest, currently
- asks for facts about a specific real-world thing that you cannot be certain of

Choose "chat" when the message:
- is a greeting, thanks, apology or small talk
- is about you: what you are, what you can do, how you work
- is about the conversation itself, or refers only to what was already said
- is self-contained and needs no outside facts: rewrite this, explain a concept,
  do some arithmetic, translate this sentence

Summarising, listing and explaining are not modes of their own — the MATERIAL
decides, never the verb. Summarising what was already said in this conversation
is chat; summarising, listing or explaining anything that lives out in the world
is browse, whether or not a specific site is named.

You are not being asked whether you happen to know the answer. Assume your own
knowledge is stale: if the answer could have changed since you were trained, or
depends on a real-world fact you cannot verify from this conversation alone,
that is "browse" even when an answer feels within reach.

The message may be in ANY language. Judge it by MEANING, never by keywords: a \
message can be chat while containing the word "news", and browse while \
containing no recognisable English at all.

NEVER answer the message. Only classify it. When genuinely unsure, choose "browse"."""

# Few-shot turns beat a wall of rules for a small model. Several of these exist
# specifically to pin down where the old keyword matcher went wrong — "today",
# "summarise", "find", "book", "open" and a bare email address all used to force
# a web search on their own.
_EXAMPLES = [
    ("hi", "chat"),
    ("what can you do?", "chat"),
    ("how are you today?", "chat"),
    ("can you summarise what you just told me?", "chat"),
    ("recap what we have discussed so far", "chat"),
    ("I need to find myself a new hobby", "chat"),
    ("lets book a time to talk tomorrow", "chat"),
    ("my email is john.doe@gmail.com", "chat"),
    ("wie geht es dir?", "chat"),
    ("¿qué puedes hacer?", "chat"),
    ("top stories on Hacker News", "browse"),
    # The mirror image of the two "summarise" chat examples above: same verb,
    # but the material is out on the web, so it needs the browser.
    ("give me a summary of the BBC front page", "browse"),
    ("what happened in the election yesterday?", "browse"),
    ("open github.com/anthropics and read the pinned repo", "browse"),
    ("what's the weather in Karachi right now?", "browse"),
    ("busca las últimas noticias de tecnología", "browse"),
    ("今日のトップニュースは？", "browse"),
    ("أخبار اليوم عن الذكاء الاصطناعي", "browse"),
    ("کیا آج سونے کی قیمت بڑھی ہے؟", "browse"),
]

# The router is good but not perfect, so this prompt has to hold the line when a
# message that really needed the web lands here anyway. A soft "don't fabricate"
# was not enough: the model would narrate a browsing session it never ran, or
# quote a Bitcoin price it invented outright.
CHAT_PROMPT = """You are a local web-browsing assistant. You can browse the web \
on the user's behalf — search, read pages, click through sites, fill in forms — \
and the user can also take control of the browser themselves at any time.

Reply in one or two short, friendly sentences, in the SAME LANGUAGE the user \
wrote in.

Hard rules, most important first:
1. You have NOT browsed. Never write as though you had — no "let me check", no
   "I'm seeing", no "(searching...)", and never describe a page you did not open.
2. Never state a fact about the outside world: no prices, dates, news, results,
   version numbers, and nobody's current job or title. Your training data is
   stale and you cannot tell which parts of it have gone out of date.
3. If answering would need any of that, say so in one sentence and offer to look
   it up. That is a complete and helpful answer on its own — never soften it by
   adding a guess alongside.
4. Never invent links or quotes, and never use placeholders like "[insert ...]".

You may talk freely about yourself, what you can do, and anything already said
in this conversation."""


def _classifier_messages(message: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = [{"role": "system", "content": ROUTER_PROMPT}]
    for text, mode in _EXAMPLES:
        msgs.append({"role": "user", "content": text})
        msgs.append({"role": "assistant", "content": json.dumps({"mode": mode})})
    msgs.append({"role": "user", "content": message})
    return msgs


async def route(message: str) -> dict[str, Any]:
    """Return {"mode": "chat"|"browse"}. Anything unrecognised means browse.

    Defaulting to browse is deliberate: the failure it avoids is the model
    inventing an answer it should have looked up, which is far worse than
    opening a browser that turned out not to be needed.
    """
    reply = await chat_json(_classifier_messages(message), model=ROUTER_MODEL)
    mode = str(reply.get("mode", "")).strip().lower()
    return {"mode": mode if mode in ("chat", "browse") else "browse"}


async def chat_reply(message: str, on_token: OnToken | None = None) -> str:
    """Stream a short conversational reply and return the finished text."""
    return await chat_text(
        [
            {"role": "system", "content": CHAT_PROMPT},
            {"role": "user", "content": message},
        ],
        on_token,
    )
