"""Measure how well the router classifies chat vs. browse.

Routing quality is a property of the prompt, not of the code around it, so the
unit tests cannot tell you whether a prompt edit helped or hurt. This can: it
runs a labelled set through the real classifier against a live Ollama and prints
the accuracy plus every disagreement.

    cd backend && ./.venv/bin/python tools/routing_eval.py

The set deliberately includes the cases the old keyword regex got wrong — plain
conversation containing "today"/"find"/"book"/"open", and web requests written
in languages the keyword list could not see at all.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ROUTER_MODEL
from app.router import route

# (message, expected mode, group)
CASES = [
    # --- English conversation, including the old regex's false positives ----
    ("hi", "chat", "en-chat"),
    ("what can you do?", "chat", "en-chat"),
    ("how are you today?", "chat", "en-chat"),  # "today"
    ("thanks, that was helpful", "chat", "en-chat"),
    ("can you summarise what you just told me?", "chat", "en-chat"),  # "summar"
    ("I need to find myself a new hobby", "chat", "en-chat"),  # "find "
    ("lets book a time to talk tomorrow", "chat", "en-chat"),  # "book "
    ("open source software is great, isn't it?", "chat", "en-chat"),  # "open "
    ("my email is john.doe@gmail.com", "chat", "en-chat"),  # domain regex
    ("what is your current version?", "chat", "en-chat"),  # "current"
    ("translate 'good morning' into French", "chat", "en-chat"),
    ("what is 17 times 23?", "chat", "en-chat"),
    ("explain how a hash map works", "chat", "en-chat"),
    # --- English web tasks --------------------------------------------------
    ("top stories on Hacker News", "browse", "en-browse"),
    ("go to example.com", "browse", "en-browse"),
    ("what's the weather in London right now?", "browse", "en-browse"),
    ("search for the cheapest flight to Dubai", "browse", "en-browse"),
    ("who won the match last night?", "browse", "en-browse"),
    ("summarise today's tech news", "browse", "en-browse"),
    ("look up the price of bitcoin", "browse", "en-browse"),
    ("find the docs for the Playwright python API", "browse", "en-browse"),
    # --- Non-English conversation -------------------------------------------
    ("wie geht es dir?", "chat", "xx-chat"),
    ("ola, tudo bem?", "chat", "xx-chat"),
    ("¿qué puedes hacer?", "chat", "xx-chat"),
    ("merci beaucoup !", "chat", "xx-chat"),
    ("你好，你是谁？", "chat", "xx-chat"),
    ("आप क्या कर सकते हैं?", "chat", "xx-chat"),
    ("مرحبا كيف حالك؟", "chat", "xx-chat"),
    ("آپ کیا کر سکتے ہیں؟", "chat", "xx-chat"),
    # --- Non-English web tasks ----------------------------------------------
    ("busca las últimas noticias de tecnología", "browse", "xx-browse"),
    ("今日のトップニュースは？", "browse", "xx-browse"),
    ("أخبار اليوم عن الذكاء الاصطناعي", "browse", "xx-browse"),
    ("کیا آج سونے کی قیمت بڑھی ہے؟", "browse", "xx-browse"),
    ("quel temps fait-il à Paris aujourd'hui ?", "browse", "xx-browse"),
    ("قیمت دلار امروز چند است؟", "browse", "xx-browse"),
    ("最新のAIニュースを調べて", "browse", "xx-browse"),
    ("wie ist der aktuelle Bitcoin-Kurs?", "browse", "xx-browse"),
    ("बीबीसी पर आज की मुख्य खबरें", "browse", "xx-browse"),
]


async def main() -> int:
    print(f"router model: {ROUTER_MODEL}   cases: {len(CASES)}\n")
    wrong, groups, elapsed = [], {}, []

    for message, expected, group in CASES:
        t0 = time.time()
        got = (await route(message))["mode"]
        elapsed.append(time.time() - t0)

        hit, total = groups.get(group, (0, 0))
        groups[group] = (hit + (got == expected), total + 1)
        if got != expected:
            wrong.append((group, message, expected, got))

    total_hit = sum(h for h, _ in groups.values())
    for group in sorted(groups):
        hit, n = groups[group]
        bar = "█" * hit + "·" * (n - hit)
        print(f"  {group:<10} {hit}/{n:<3} {bar}")

    print(
        f"\n  overall    {total_hit}/{len(CASES)} ({100 * total_hit / len(CASES):.0f}%)"
    )
    print(
        f"  latency    {sum(elapsed) / len(elapsed):.2f}s avg, {max(elapsed):.2f}s worst"
    )

    if wrong:
        print("\nmisclassified:")
        for group, message, expected, got in wrong:
            print(f"  [{group}] want {expected:<6} got {got:<6} {message}")
    return 0 if not wrong else 1


sys.exit(asyncio.run(main()))
