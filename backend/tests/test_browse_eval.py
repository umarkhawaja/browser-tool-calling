"""Tests for the parts of `tools/browse_eval.py` that need neither Ollama nor
Chromium: the fixture site, and the grading of a finished run.

The eval script itself is deliberately live — that is the whole point of it — but
two of its halves can be pinned down here. Grading decides whether a run's events
count as a pass, and the fixture site has to actually say the things the cases ask
for. Without the second one a typo in the HTML looks exactly like a model failure
in the live run, which is the most expensive kind of red there is.
"""

import re

import httpx

from app import agent
from app.agent import run_agent
from app.browser import CONSENT_LABELS, StaleIndex
from tools import browse_eval
from tools.browse_eval import (
    ACCEPT_LABEL,
    CASES,
    ENTRY_PATH,
    REJECT_LABEL,
    SITE_PATHS,
    Case,
    grade,
    says,
    serve,
)

# A stand-in for a real row of `CASES`, so these tests keep asserting on grading
# rather than on whatever the fixture site currently charges for a kite.
CASE = Case(
    name="price",
    task="Go to {base} and tell me the price of the Nimbus 3000.",
    expects=("42",),
    rejects=("99",),
)


def _answered(text):
    """The events of a run that took one action and then answered."""
    return [
        {"type": "action", "text": "extract_text", "detail": "Products ..."},
        {"type": "answer", "text": text},
    ]


# --- grading ---------------------------------------------------------------
def test_an_answer_carrying_the_fact_passes():
    outcome = grade(CASE, _answered("The Nimbus 3000 costs £42."))
    assert outcome.passed
    assert outcome.reason == ""
    assert outcome.answer == "The Nimbus 3000 costs £42."


def test_a_missing_fact_fails_and_names_what_was_wanted():
    outcome = grade(CASE, _answered("I could not find a price for that."))
    assert not outcome.passed
    assert "42" in outcome.reason


def test_a_decoy_fails_the_run_even_when_the_fact_is_there():
    # Reading the other product's page and quoting both prices is not answering
    # the question, and a bare "is 42 in there?" would call it a pass.
    outcome = grade(CASE, _answered("The Nimbus 3000 is £42, or possibly £99."))
    assert not outcome.passed
    assert "99" in outcome.reason


def test_a_number_the_fact_is_merely_a_digit_of_is_not_the_fact():
    # "£4200" is not "£42". A plain substring test calls a misread number a pass,
    # which is the exact failure — a confident wrong answer — the eval exists for.
    outcome = grade(CASE, _answered("The Nimbus 3000 costs £4200."))
    assert not outcome.passed
    assert "42" in outcome.reason


def test_a_number_the_decoy_is_merely_a_digit_of_is_not_the_decoy():
    # The mirror image: failing a correct answer because some unrelated number
    # happens to contain the decoy's digits would make the eval untrustworthy in
    # the direction that costs the most time.
    outcome = grade(CASE, _answered("The Nimbus 3000 costs £42 (SKU 4990)."))
    assert outcome.passed, outcome.reason


async def test_a_browser_that_will_not_start_is_a_failed_case_not_a_crash(monkeypatch):
    # The likeliest crash in a fresh checkout is `playwright install chromium`
    # never having been run, and it happens in `start()` — so if that call sits
    # outside the guard, the first case takes the whole script down with a raw
    # traceback, the remaining cases never run, and nothing is graded.
    async def will_not_start(self):
        raise RuntimeError("Executable doesn't exist at .../chrome-mac/Chromium")

    monkeypatch.setattr(browse_eval.BrowserSession, "start", will_not_start)

    outcome = await browse_eval.run_case(CASE, "http://127.0.0.1:1")
    assert not outcome.passed
    assert "Executable doesn't exist" in outcome.reason


def test_a_url_the_model_echoed_back_is_not_a_fact():
    # The fixture site binds to a free port, so its URLs carry a random five-digit
    # number that would satisfy or violate a numeric expectation at random.
    outcome = grade(
        CASE, _answered("The Nimbus 3000 costs £42 (http://127.0.0.1:53199/products).")
    )
    assert outcome.passed, outcome.reason


def test_a_failed_run_is_reported_rather_than_raised():
    outcome = grade(CASE, [{"type": "error", "text": "Ollama is unreachable"}])
    assert not outcome.passed
    assert "Ollama is unreachable" in outcome.reason


def test_a_run_that_never_answered_fails():
    outcome = grade(CASE, [])
    assert not outcome.passed
    assert outcome.reason
    assert outcome.answer == ""


def test_the_actions_and_the_refusals_are_reported():
    # How a run got there is the diagnosis: a pass that burned four refusals on
    # stale indices is a different result from a clean one.
    events = [
        {"type": "action", "text": "go_to_url", "detail": "Navigated to /"},
        {"type": "action", "text": "click", "detail": "Action failed: Element [8] is"},
        {"type": "action", "text": "extract_text", "detail": "Price: £42"},
        {"type": "answer", "text": "£42"},
    ]
    outcome = grade(CASE, events)
    assert outcome.actions == ("go_to_url", "click", "extract_text")
    assert len(outcome.refusals) == 1
    assert "Element [8]" in outcome.refusals[0]


async def test_a_refusal_the_real_loop_emits_is_counted_as_one(monkeypatch):
    """The refusal count is read off a prefix `agent.py` writes, not a field.

    The test above hand-writes that prefix, so it agrees with `grade` by
    construction and would go on agreeing after someone reworded the message in
    `agent.py` — leaving every live run reporting `0 refused` forever, which the
    docs call the interesting half of the output. This is the same claim asked
    of the real loop instead: a browser whose click raises, driven through
    `run_agent`, must come back counted.
    """

    class RefusingBrowser:
        async def restart_numbering(self):
            pass

        async def elements(self):
            return [{"index": 8, "tag": "a", "type": "", "label": "Nimbus 3000"}]

        async def screenshot_b64(self):
            return ""

        def url(self):
            return "http://127.0.0.1:1/products"

        async def click(self, index):
            raise StaleIndex(f"Element [{index}] is not on the page any more.")

    replies = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "click", "arguments": {"index": 8}}}],
        },
        {"role": "assistant", "content": "The Nimbus 3000 costs £42."},
    ]

    async def fake_chat_tools(messages, tools, on_token=None):
        return replies.pop(0)

    monkeypatch.setattr(agent, "chat_tools", fake_chat_tools)

    events = []

    async def emit(event):
        events.append(event)

    await run_agent("price?", RefusingBrowser(), emit)

    outcome = grade(CASE, events)
    assert outcome.passed, outcome.reason  # the answer was still right...
    assert len(outcome.refusals) == 1, (  # ...but the run is not a clean one
        f"the loop's failure message no longer starts with what `grade` looks "
        f"for; actions were {outcome.actions}"
    )
    assert "Element [8]" in outcome.refusals[0]


# --- the fixture site ------------------------------------------------------
_TAG = re.compile(r"<[^>]*>")


def _readable(html):
    """The page as the agent meets it: text, with the markup taken out.

    The agent reads rendered text, never source, so the guards below have to as
    well. Matching raw HTML counts an inline `z-index:42` as a price on the
    landing page — a styling change no agent can see would fail the suite — and,
    the other way round, would accept a fact that exists only in an attribute.
    """
    return _TAG.sub(" ", html)


def test_every_page_of_the_fixture_site_serves():
    with serve() as base:
        for path in SITE_PATHS:
            response = httpx.get(base + path, timeout=5)
            assert response.status_code == 200, path
            assert "<h1>" in response.text, path


def test_a_path_the_site_does_not_have_is_a_404():
    with serve() as base:
        assert httpx.get(base + "/nowhere", timeout=5).status_code == 404


def test_every_case_asks_for_something_the_site_actually_says():
    with serve() as base:
        pages = (httpx.get(base + p, timeout=5).text for p in SITE_PATHS)
        site = " ".join(_readable(page) for page in pages)
    for case in CASES:
        # Asked through `says`, the same predicate that will grade the answer.
        # A second way of asking is how the two drift apart: a fixture repriced
        # to £420 satisfies a plain `"42" in site` while every live run fails.
        for wanted in case.expects:
            assert says(site, wanted), f"{case.name}: {wanted!r} is nowhere"
        # A decoy that is not on the site tests nothing: no run could quote it.
        for decoy in case.rejects:
            assert says(site, decoy), f"{case.name}: decoy {decoy!r} is nowhere"


def test_loosening_the_consent_match_to_a_substring_would_wreck_the_landing_page():
    """The consent wall is only a guard if a wrong match is *visible*.

    `browser.py` matches `CONSENT_LABELS` exactly so it never hits "I do not
    agree"; nothing in the suite holds it to that, so the live eval is where it
    would show — but only if the fixture's two buttons are picked against the
    real label list rather than at random. Three things have to hold at once:
    exact matching opens the wall, substring matching hits the reject button
    instead, and it does so *before* any label matches accept, since
    `dismiss_overlays` clicks the first label that hits and stops.
    """
    labels = [label.lower() for label in CONSENT_LABELS]
    accept, reject = ACCEPT_LABEL.lower(), REJECT_LABEL.lower()

    assert accept in labels, f"{ACCEPT_LABEL!r} would never open the wall"
    assert reject not in labels, f"{REJECT_LABEL!r} is itself an accept label"

    hits_reject = [i for i, label in enumerate(labels) if label in reject]
    hits_accept = [i for i, label in enumerate(labels) if label in accept]
    assert hits_reject, f"no label reaches {REJECT_LABEL!r} even as a substring"
    assert min(hits_reject) < min(hits_accept), (
        f"a substring match would reach {ACCEPT_LABEL!r} first, so loosening "
        f"{REJECT_LABEL!r} out of exactness would pass the eval unnoticed"
    )


def test_no_fact_can_be_read_from_the_landing_page():
    # Every task starts at the entry page. An answer already legible there would
    # be reachable without a click, a form or an extract — so the case would pass
    # while measuring nothing.
    with serve() as base:
        entry = _readable(httpx.get(base + ENTRY_PATH, timeout=5).text)
    for case in CASES:
        for wanted in case.expects:
            assert not says(entry, wanted), f"{case.name}: {wanted!r} is free"
