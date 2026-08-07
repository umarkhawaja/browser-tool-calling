"""Tests for the browser wrapper's user-input dispatch and screencast fan-out.

Playwright is faked, so these run without a real Chromium.
"""

import asyncio

from app.browser import BrowserSession, FrameSink, with_scheme


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


class FakePage:
    def __init__(self):
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()


def _session():
    s = BrowserSession()
    s.page = FakePage()
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
