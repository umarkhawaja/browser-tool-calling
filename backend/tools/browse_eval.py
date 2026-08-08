"""Run whole browse tasks through the real stack: real Chromium, live Ollama.

The pytest suite fakes both the model and the browser, which is right for what it
is — but it means nothing ever checks that the pieces fit together. Native tool
calling, the numbered listing, `data-agent-idx` clicking, consent dismissal and
the answer at the end have only ever been exercised separately, against fakes
that agree with them by construction.

This is the equivalent of `routing_eval.py` for a browse run: opt-in, live, and
scored against a fixture site served on loopback, so a failure means the stack
broke rather than the web moved.

    cd backend && ./.venv/bin/python tools/browse_eval.py            # all cases
    cd backend && ./.venv/bin/python tools/browse_eval.py stock      # just one

The site is small on purpose, and every fact sits *behind* the interaction the
case is meant to exercise — a price one click past the index, a stock count only
a filled-in form will produce. A task answerable from the landing page would
measure nothing, which is what `test_no_fact_can_be_read_from_the_landing_page`
holds it to. The index also opens under a consent overlay whose "Reject all"
wrecks the page, so `dismiss_overlays` picking the wrong button is loud rather
than invisible.

Grading is by outcome, not by method: a case passes when the final answer names
the fact and does not name the decoy that lives one page over. How many actions
it took, and how many were refused, is reported alongside — a pass that burned
four refusals on stale indices is a different result from a clean one.

`serve()` and `grade()` are covered by `tests/test_browse_eval.py`; everything
below them needs Ollama and a browser, exactly like the run it is measuring.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import run_agent
from app.browser import BrowserSession
from app.config import MAX_STEPS, MODEL

# --- the fixture site --------------------------------------------------------
# A consent wall the agent has to get past before it can read anything, with the
# negative button a substring match would happily hit. Accepting removes it;
# rejecting replaces the page, so a wrong click cannot pass for a right one.
CONSENT = """
<div id=consent style="position:fixed;inset:0;background:#111;color:#eee;
     padding:3rem;font:16px sans-serif;z-index:9">
  <p>Cloud Kit uses cookies.</p>
  <button onclick="document.getElementById('consent').remove()">Accept all</button>
  <button onclick="document.body.textContent='Cookies rejected.'">Reject all</button>
</div>
"""


def _html(title: str, body: str) -> str:
    return (
        f"<!doctype html><html lang=en><head><meta charset=utf-8>"
        f"<title>{title}</title></head><body>{body}</body></html>"
    )


ENTRY_PATH = "/"

PAGES = {
    ENTRY_PATH: _html(
        "Cloud Kit",
        CONSENT
        + """
        <h1>Cloud Kit</h1>
        <p>Kit for the well-equipped sky.</p>
        <ul>
          <li><a href="/products">Products</a></li>
          <li><a href="/stock">Stock check</a></li>
          <li><a href="/about">About us</a></li>
        </ul>
        """,
    ),
    "/products": _html(
        "Products",
        """
        <h1>Products</h1>
        <p>Pick a model to see what it costs.</p>
        <ul>
          <li><a href="/products/nimbus">Nimbus 3000</a></li>
          <li><a href="/products/cirrus">Cirrus Two</a></li>
        </ul>
        <p><a href="/">Home</a></p>
        """,
    ),
    "/products/nimbus": _html(
        "Nimbus 3000",
        """
        <h1>Nimbus 3000</h1>
        <p>Price: £42</p>
        <p>Made in Bristol.</p>
        <p><a href="/products">Back to products</a></p>
        """,
    ),
    "/products/cirrus": _html(
        "Cirrus Two",
        """
        <h1>Cirrus Two</h1>
        <p>Price: £99</p>
        <p>Made in Dundee.</p>
        <p><a href="/products">Back to products</a></p>
        """,
    ),
    "/about": _html(
        "About us",
        """
        <h1>About us</h1>
        <p>Cloud Kit has sold kites for three generations.</p>
        <p><a href="/">Home</a></p>
        """,
    ),
}

# The one page that answers a query rather than serving a fixed body. The form is
# on the results page too, exactly as a real search page keeps it.
STOCK = _html(
    "Stock check",
    """
    <h1>Stock check</h1>
    <form action="/stock" method="get">
      <input type="text" name="q" placeholder="Product name">
      <button type="submit">Check</button>
    </form>
    RESULT
    <p><a href="/">Home</a></p>
    """,
)

# Every URL the site answers, so the suite can hold each one to serving — a 500
# from the handler would otherwise surface only as a puzzling live failure.
SITE_PATHS = (
    ENTRY_PATH,
    "/products",
    "/products/nimbus",
    "/products/cirrus",
    "/about",
    "/stock",
    "/stock?q=nimbus",
    "/stock?q=cirrus",
)


def _stock_result(query: str) -> str:
    asked = (parse_qs(query).get("q") or [""])[0].strip().lower()
    if not asked:
        return "<p>Enter a product name to check stock.</p>"
    if "nimbus" in asked:
        return "<p>Nimbus 3000: 7 units in stock at the Bristol depot.</p>"
    if "cirrus" in asked:
        return "<p>Cirrus Two: 31 units in stock at the Dundee depot.</p>"
    return f"<p>No product matches {escape(asked)}.</p>"


def _body(path: str, query: str) -> str | None:
    if path == "/stock":
        return STOCK.replace("RESULT", _stock_result(query))
    return PAGES.get(path)


@contextmanager
def serve():
    """Run the fixture site on a free loopback port; yields its base URL.

    Port zero rather than a fixed one: an eval that cannot run because something
    else holds 8000 is a worse failure than any it is looking for. The random
    port is why `grade` strips URLs out of an answer before matching on it.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # the name BaseHTTPRequestHandler dispatches to
            path, _, query = self.path.partition("?")
            body = _body(path, query)
            self.send_response(200 if body is not None else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            payload = (body or _html("Not found", "<h1>Not found</h1>")).encode("utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass  # the agent's own trace is the output; access logs are noise

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


# --- the cases ---------------------------------------------------------------
@dataclass(frozen=True)
class Case:
    """One scripted task, and how to tell whether the run answered it.

    `expects` are the facts the final answer must name — all of them. `rejects`
    are the neighbouring facts it must not: each one lives on a page the task has
    no reason to visit, so quoting it means the run read the wrong thing and
    reported it with a straight face, which a bare "did it say 42?" would pass.
    """

    name: str
    task: str  # formatted with {base}, the fixture site's URL
    expects: tuple = ()
    rejects: tuple = ()


CASES = [
    # Navigate, get past the consent wall, click twice, read the page.
    Case(
        name="price",
        task="Go to {base} and tell me the price of the Nimbus 3000.",
        expects=("42",),
        rejects=("99",),  # the Cirrus Two's price, one page over
    ),
    # The same, but the fact only exists once a form has been filled in and
    # submitted: input_text plus press_enter or a click on Check.
    Case(
        name="stock",
        task="Go to {base} and find out how many Nimbus 3000 are in stock.",
        expects=("7",),
        rejects=("31",),  # what the form says for the other product
    ),
]


# --- grading -----------------------------------------------------------------
@dataclass(frozen=True)
class Outcome:
    passed: bool
    reason: str = ""
    answer: str = ""
    actions: tuple = ()
    refusals: tuple = ()


# A URL is machinery the model was handed, not something it read off a page, and
# the fixture site's URLs carry a random port. Left in, those five digits satisfy
# or violate a numeric expectation at random — "£42" and ":54299" are the same
# characters to a substring match.
_URLISH = re.compile(r"(https?://|127\.0\.0\.1|localhost)\S*", re.IGNORECASE)


def grade(case: Case, events: list[dict[str, Any]]) -> Outcome:
    """Decide whether the events of one run answered the case.

    Takes the emitted events rather than a return value because that is all a run
    produces: `run_agent` reports everything through `emit`, so the same stream
    the UI renders is the record graded here.
    """
    actions = tuple(e["text"] for e in events if e.get("type") == "action")
    refusals = tuple(
        e.get("detail", "")
        for e in events
        if e.get("type") == "action" and e.get("detail", "").startswith("Action failed")
    )
    answers = [e["text"] for e in events if e.get("type") == "answer"]
    errors = [e["text"] for e in events if e.get("type") == "error"]

    if not answers:
        # A run that died is a failed case, not a failed script: whatever went
        # wrong is reported in the same table as a wrong answer.
        reason = f"the run failed: {errors[-1]}" if errors else "the run never answered"
        return Outcome(False, reason, actions=actions, refusals=refusals)

    answer = answers[-1]
    prose = _URLISH.sub(" ", answer).lower()
    missing = [want for want in case.expects if want.lower() not in prose]
    decoys = [decoy for decoy in case.rejects if decoy.lower() in prose]

    problems = []
    if missing:
        problems.append(f"never said {', '.join(repr(m) for m in missing)}")
    if decoys:
        problems.append(f"quoted the decoy {', '.join(repr(d) for d in decoys)}")
    return Outcome(not problems, "; ".join(problems), answer, actions, refusals)


# --- running one case for real -----------------------------------------------
def _oneline(text: str, width: int = 78) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def _trace(event: dict[str, Any]) -> None:
    """Print the run as it happens: a live case takes a minute, and a silent
    minute is indistinguishable from a hang."""
    kind = event.get("type")
    if kind == "action":
        print(f"    · {event['text']:<13} {_oneline(event.get('detail', ''))}")
    elif kind in ("thought", "answer", "error"):
        print(f"    {kind:<15} {_oneline(event.get('text', ''))}")


async def run_case(case: Case, base: str) -> Outcome:
    """Run one case against a real browser and a live model.

    Each case gets its own `BrowserSession`, so it starts from `about:blank`.
    Sharing one would leave the previous case's page — and quite possibly its
    answer — on screen, and a case answerable without browsing has measured
    nothing.
    """
    events: list[dict[str, Any]] = []

    async def emit(event: dict[str, Any]) -> None:
        # Screenshots are a megabyte of base64 each and tokens arrive one word at
        # a time; neither is graded, and the authoritative events cover both.
        if event.get("type") not in ("screenshot", "token", "token_reset"):
            events.append(event)
            _trace(event)

    browser = BrowserSession()
    await browser.start()
    try:
        await run_agent(case.task.format(base=base), browser, emit)
    except Exception as e:  # a crash is this case's result, not the script's end
        events.append({"type": "error", "text": f"{type(e).__name__}: {e}"})
        _trace(events[-1])
    finally:
        await browser.stop()
    return grade(case, events)


def _selected(names: list[str]) -> list[Case]:
    if not names:
        return CASES
    unknown = set(names) - {case.name for case in CASES}
    if unknown:
        known = ", ".join(case.name for case in CASES)
        raise SystemExit(f"unknown case: {', '.join(sorted(unknown))} (have {known})")
    return [case for case in CASES if case.name in names]


async def main(names: list[str]) -> int:
    cases = _selected(names)
    print(f"model: {MODEL}   max steps: {MAX_STEPS}   cases: {len(cases)}\n")

    results = []
    with serve() as base:
        print(f"fixture site: {base}\n")
        for case in cases:
            print(f"  {case.name}  {case.task.format(base=base)}")
            started = time.time()
            outcome = await run_case(case, base)
            elapsed = time.time() - started
            results.append((case, outcome))
            mark = "✓ pass" if outcome.passed else "✗ FAIL"
            print(
                f"    {mark}  {len(outcome.actions)} actions, "
                f"{len(outcome.refusals)} refused, {elapsed:.1f}s\n"
            )

    passed = sum(1 for _, outcome in results if outcome.passed)
    print(f"  overall    {passed}/{len(results)}")

    failed = [(case, outcome) for case, outcome in results if not outcome.passed]
    if failed:
        print("\nfailed:")
        for case, outcome in failed:
            print(f"  [{case.name}] {outcome.reason}")
            print(f"    answer:  {_oneline(outcome.answer) or '(none)'}")
            print(f"    actions: {' '.join(outcome.actions) or '(none)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    # Guarded, unlike routing_eval.py's: the suite imports this module for the
    # fixture site and the grading.
    sys.exit(asyncio.run(main(sys.argv[1:])))
