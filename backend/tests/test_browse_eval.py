"""Tests for the parts of `tools/browse_eval.py` that need neither Ollama nor
Chromium: the fixture site, and the grading of a finished run.

The eval script itself is deliberately live — that is the whole point of it — but
two of its halves can be pinned down here. Grading decides whether a run's events
count as a pass, and the fixture site has to actually say the things the cases ask
for. Without the second one a typo in the HTML looks exactly like a model failure
in the live run, which is the most expensive kind of red there is.
"""

import httpx

from tools.browse_eval import CASES, ENTRY_PATH, SITE_PATHS, Case, grade, serve

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


# --- the fixture site ------------------------------------------------------
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
        site = " ".join(httpx.get(base + p, timeout=5).text for p in SITE_PATHS).lower()
    for case in CASES:
        for wanted in case.expects:
            assert wanted.lower() in site, f"{case.name}: {wanted!r} is nowhere"
        # A decoy that is not on the site tests nothing: no run could quote it.
        for decoy in case.rejects:
            assert decoy.lower() in site, f"{case.name}: decoy {decoy!r} is nowhere"


def test_no_fact_can_be_read_from_the_landing_page():
    # Every task starts at the entry page. An answer already legible there would
    # be reachable without a click, a form or an extract — so the case would pass
    # while measuring nothing.
    with serve() as base:
        entry = httpx.get(base + ENTRY_PATH, timeout=5).text.lower()
    for case in CASES:
        for wanted in case.expects:
            assert wanted.lower() not in entry, f"{case.name}: {wanted!r} is free"
