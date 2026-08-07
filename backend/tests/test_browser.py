"""Tests for the browser wrapper's user-input dispatch and screencast fan-out.

Playwright is faked, so these run without a real Chromium.
"""

import asyncio

import pytest

from app.browser import BrowserSession, FrameSink, StaleIndex, with_scheme


class FakeMouse:
    def __init__(self):
        self.calls = []

    async def move(self, x, y):
        self.calls.append(("move", x, y))

    async def click(self, x, y, button="left", click_count=1):
        self.calls.append(("click", x, y, button, click_count))

    async def wheel(self, dx, dy):
        self.calls.append(("wheel", dx, dy))


class FakeKeyboard:
    def __init__(self):
        self.calls = []

    async def type(self, text):
        self.calls.append(("type", text))

    async def press(self, key):
        self.calls.append(("press", key))


class FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector
        self.first = self

    async def count(self):
        return 1

    async def inner_text(self):
        return "Some element"

    async def click(self, timeout=None):
        self._page.clicked.append(self._selector)

    async def fill(self, text, timeout=None):
        self._page.filled.append((self._selector, text))

    async def evaluate(self, script):
        self._page.marked.append(self._selector)


class FakePage:
    """`readings` is the indices each successive `elements()` call reports.

    Spelled out rather than derived, so these tests say which numbering they are
    describing instead of re-deriving it the way the page script does.
    """

    def __init__(self, readings=()):
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.clicked = []
        self.filled = []
        self.marked = []
        self.bases = []
        self.forgot = []
        self._readings = list(readings)

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def evaluate(self, script, arg=None):
        if arg is None:  # the only argument-less pass is the one that forgets
            self.forgot.append(script)
            return None
        self.bases.append(arg)
        found = self._readings.pop(0) if self._readings else []
        return {
            "next": max(found, default=(arg or 0) - 1) + 1,
            "elements": [{"index": i, "tag": "a"} for i in found],
        }

    async def wait_for_timeout(self, ms):
        pass


def _session(readings=()):
    s = BrowserSession()
    s.page = FakePage(readings)
    return s


# --- user input ------------------------------------------------------------
# These drive `user_input` with the protocol dict the socket receives, because
# that is the only entry point the rest of the app has.
async def test_every_gesture_reaches_playwright():
    s = _session()
    await s.user_input({"kind": "move", "x": 10, "y": 20})
    await s.user_input({"kind": "click", "x": 30, "y": 40})
    await s.user_input({"kind": "scroll", "dx": 0, "dy": 120})
    await s.user_input({"kind": "type", "text": "hello"})
    await s.user_input({"kind": "key", "key": "Enter"})

    assert s.page.mouse.calls == [
        ("move", 10, 20),
        ("click", 30, 40, "left", 1),
        ("wheel", 0, 120),
    ]
    assert s.page.keyboard.calls == [("type", "hello"), ("press", "Enter")]


async def test_a_click_passes_button_and_count():
    s = _session()
    await s.user_input({"kind": "click", "x": 1, "y": 2, "button": "right", "clicks": 2})
    assert s.page.mouse.calls == [("click", 1, 2, "right", 2)]


async def test_a_click_rejects_an_unknown_button():
    # The button name comes off the wire, so it must not reach Playwright raw.
    s = _session()
    await s.user_input({"kind": "click", "x": 1, "y": 2, "button": "nonsense"})
    assert s.page.mouse.calls == [("click", 1, 2, "left", 1)]


async def test_a_click_floors_the_click_count_at_one():
    s = _session()
    await s.user_input({"kind": "click", "x": 1, "y": 2, "clicks": 0})
    assert s.page.mouse.calls == [("click", 1, 2, "left", 1)]


async def test_gestures_report_whether_the_page_may_have_moved():
    # The caller re-reads the page on a true, and a mouse move is far too
    # frequent to pay for that — so only a move may report false.
    s = _session()
    assert await s.user_input({"kind": "move", "x": 1, "y": 2}) is False
    assert await s.user_input({"kind": "click", "x": 1, "y": 2}) is True
    assert await s.user_input({"kind": "scroll", "dx": 0, "dy": 1}) is True
    assert await s.user_input({"kind": "type", "text": "a"}) is True
    assert await s.user_input({"kind": "key", "key": "Enter"}) is True


async def test_an_unknown_gesture_does_nothing():
    s = _session()
    assert await s.user_input({"kind": "drag", "x": 1, "y": 2}) is False
    assert await s.user_input({}) is False
    assert s.page.mouse.calls == []
    assert s.page.keyboard.calls == []


async def test_a_kind_that_is_not_even_a_string_is_just_unknown():
    # `kind` is whatever the client put on the wire. A list or a dict is not a
    # gesture any row claims, and looking one up in a dict raises rather than
    # missing — so it has to be ruled out before the lookup, not after.
    s = _session()
    for kind in ([], {}, 7, None, True):
        assert await s.user_input({"kind": kind}) is False
    assert s.page.mouse.calls == []
    assert s.page.keyboard.calls == []


# --- element addressing ----------------------------------------------------
# Old listings stay in the model's transcript for the whole run, so the danger is
# not an index that fails to resolve — it is one that resolves to the wrong thing
# and reads as a correct click in the trace.
async def test_an_element_keeps_its_number_while_it_is_still_there():
    # Re-reading a page that has not moved must not move the numbers with it.
    # A model that is told [1] twice and refused the second time has been given
    # a listing it cannot trust, and it burns its step budget re-reading.
    s = _session(readings=[[0, 1, 2], [0, 1, 2]])
    await s.elements()
    await s.elements()
    await s.click(1)
    assert s.page.clicked == ['[data-agent-idx="1"]']


async def test_a_number_from_a_page_that_has_moved_on_is_refused_not_clicked():
    s = _session(readings=[[0, 1, 2], [3, 4, 5]])
    stale = (await s.elements())[1]["index"]
    await s.elements()  # a new page: nothing carries the old numbers

    with pytest.raises(StaleIndex) as refusal:
        await s.click(stale)

    assert s.page.clicked == [], "a stale index must not reach the page"
    # The model reads this as the tool result, so it has to say what to do next.
    assert "listing below" in str(refusal.value)


async def test_an_element_that_left_the_page_is_refused_even_mid_range():
    # [1] closed a menu behind it; [0] and [2] kept their numbers. Being between
    # two live numbers must not make a gone element addressable.
    s = _session(readings=[[0, 1, 2], [0, 2]])
    await s.elements()
    await s.elements()
    with pytest.raises(StaleIndex):
        await s.click(1)
    assert s.page.clicked == []


async def test_typing_into_a_stale_index_is_refused_too():
    s = _session(readings=[[0, 1, 2], [3, 4, 5]])
    stale = (await s.elements())[1]["index"]
    await s.elements()

    with pytest.raises(StaleIndex):
        await s.input_text(stale, "hello")
    assert s.page.filled == []


async def test_a_failed_reading_invalidates_every_index():
    # elements() swallows an evaluate() failure and reports nothing found. If the
    # previous numbers stayed addressable, the model would keep acting on them.
    s = _session(readings=[[0, 1, 2]])
    live = (await s.elements())[0]["index"]

    async def boom(script, arg=None):
        raise RuntimeError("page went away")

    s.page.evaluate = boom
    assert await s.elements() == []
    with pytest.raises(StaleIndex):
        await s.click(live)


async def test_numbering_starts_over_for_a_new_task():
    # A number only has to be unique for as long as something quoting it can
    # still be acted on, and a task begins with an empty transcript. Letting the
    # count climb across tasks put [341] [342] [343] on a three-element page, and
    # llama3.1 answered that by inventing [1] and spending its budget refused.
    s = _session(readings=[[0, 1, 2], [3, 4]])
    await s.elements()
    await s.elements()
    assert s.page.bases == [0, 3], "numbers climb within a task"

    await s.restart_numbering()
    assert len(s.page.forgot) == 1, "elements must lose the numbers they carry"
    await s.elements()
    assert s.page.bases == [0, 3, 0]


async def test_restarting_forgets_the_click_mark_along_with_the_numbers():
    # The mark says "the agent just went for this one", and a task that has not
    # run yet has gone for nothing — so the agent view must not open on the
    # previous task's click. The mark rides on the node, so only the page can be
    # asked to drop it; what the script then does is checked in real Chromium.
    s = _session(readings=[[0, 1, 2]])
    await s.elements()
    await s.click(1)
    await s.restart_numbering()
    assert "data-agent-clicked" in s.page.forgot[0]


async def test_nothing_is_addressable_between_restarting_and_re_reading():
    s = _session(readings=[[0, 1, 2]])
    await s.elements()
    await s.restart_numbering()
    with pytest.raises(StaleIndex):
        await s.click(1)


async def test_a_click_marks_its_target_in_the_page():
    # The agent view highlights whatever the agent just clicked. It cannot rely
    # on the number to find it again — a click that navigates renumbers the lot —
    # so the click leaves a mark on the node itself for the listing to report.
    s = _session(readings=[[0, 1, 2]])
    await s.elements()
    await s.click(1)
    assert s.page.marked == ['[data-agent-idx="1"]']


# --- screencast backpressure ----------------------------------------------
async def test_frame_sink_drops_stale_frames_while_one_is_in_flight():
    sent = []
    release = asyncio.Event()

    async def slow(frame):
        await release.wait()
        sent.append(frame["data"])

    sink = FrameSink(slow)
    sink.offer({"data": "1"})  # starts sending, then blocks
    await asyncio.sleep(0)
    sink.offer({"data": "2"})  # these two queue up behind it...
    sink.offer({"data": "3"})
    sink.offer({"data": "4"})  # ...and only the newest survives

    release.set()
    await asyncio.sleep(0.05)
    assert sent == ["1", "4"]


async def test_frame_sink_sends_every_frame_when_it_keeps_up():
    sent = []

    async def fast(frame):
        sent.append(frame["data"])

    sink = FrameSink(fast)
    for n in "123":
        sink.offer({"data": n})
        await asyncio.sleep(0.01)
    assert sent == ["1", "2", "3"]


async def test_frame_sink_stops_after_close():
    sent = []

    async def fast(frame):
        sent.append(frame["data"])

    sink = FrameSink(fast)
    sink.close()
    sink.offer({"data": "1"})
    await asyncio.sleep(0.01)
    assert sent == []


async def test_new_subscriber_is_primed_with_the_current_view():
    # CDP only pushes on repaint, so without priming a client that attaches to
    # an idle page would see a blank pane until something happened to change.
    s = _session()
    s.screenshot_b64 = lambda: _immediately("SNAP")
    s._start_screencast = lambda: _immediately(None)

    got = []

    async def on_frame(frame):
        got.append(frame)

    await s.add_frame_sink(on_frame)
    await asyncio.sleep(0.01)
    assert got and got[0]["data"] == "SNAP"
    assert got[0]["meta"] == {"width": 1280, "height": 800}


async def _immediately(value):
    return value


async def test_a_failing_sink_does_not_disturb_the_others():
    # One client's socket dying must not stop the other client's stream.
    good = []

    async def broken(frame):
        raise RuntimeError("socket gone")

    async def working(frame):
        good.append(frame["data"])

    s = _session()
    s._sinks = {FrameSink(broken), FrameSink(working)}
    for sink in s._sinks:
        sink.offer({"data": "1"})
    await asyncio.sleep(0.01)
    assert good == ["1"]


# --- scheme inference ------------------------------------------------------
def test_https_is_the_default_for_the_open_web():
    assert with_scheme("example.com") == "https://example.com"
    assert with_scheme("news.ycombinator.com/news") == "https://news.ycombinator.com/news"


def test_an_explicit_scheme_is_left_alone():
    assert with_scheme("http://example.com") == "http://example.com"
    assert with_scheme("https://example.com") == "https://example.com"


def test_loopback_gets_http():
    # A dev server on localhost almost never speaks https, and forcing it
    # fails with a bare SSL error rather than loading anything.
    assert with_scheme("localhost:8020/t.html") == "http://localhost:8020/t.html"
    assert with_scheme("127.0.0.1:5173") == "http://127.0.0.1:5173"
    assert with_scheme("localhost") == "http://localhost"


def test_a_hostname_merely_containing_localhost_still_gets_https():
    assert with_scheme("localhost.example.com") == "https://localhost.example.com"


def test_surrounding_whitespace_is_ignored():
    assert with_scheme("  example.com  ") == "https://example.com"
