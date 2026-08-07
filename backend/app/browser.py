"""Playwright browser wrapper.

Launches a *headless* browser and exposes a small set of actions the agent can
call. It can also produce a screenshot (base64 JPEG), stream a live CDP
screencast, and return a numbered list of the page's interactive elements, so the
model always knows what it can act on.

The same page can be driven by a human: `user_input` takes one raw mouse or
keyboard event from the preview and performs it at viewport coordinates, which is
what makes the frontend's live preview interactive rather than a passive image.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable

from playwright.async_api import Browser, Page, async_playwright

from app.config import BROWSER_LOCALE

# The page is rendered — and screencast — at exactly this size, so a screencast
# frame maps 1:1 onto viewport CSS pixels and the frontend can turn a click on
# the preview image into a click on the page with a single scale factor.
VIEWPORT = {"width": 1280, "height": 800}

# JS that tags every visible interactive element with a stable index and returns
# a compact description. Clicking is then done by that index, so the model never
# has to guess CSS selectors.
COLLECT_JS = """
() => {
  const sel = 'a, button, input, textarea, select, [role=button], [onclick]';
  const out = [];
  let i = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const s = window.getComputedStyle(el);
    if (r.width === 0 || r.height === 0) continue;
    if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') continue;
    el.setAttribute('data-agent-idx', String(i));
    const label = (el.innerText || el.value || el.getAttribute('placeholder') ||
                   el.getAttribute('aria-label') || el.getAttribute('name') || '')
                  .replace(/\\s+/g, ' ').trim().slice(0, 120);
    // The rect is in viewport CSS pixels, the same space a screencast frame
    // covers, so the UI can draw each box straight onto the live preview.
    out.push({ index: i, tag: el.tagName.toLowerCase(),
               type: el.getAttribute('type') || '', label,
               rect: [Math.round(r.x), Math.round(r.y),
                      Math.round(r.width), Math.round(r.height)] });
    i++;
  }
  return out;
}
"""

# Accessible-name labels of "accept" buttons on common consent dialogs
# (OneTrust, Sourcepoint, Google, BBC, …). Matched case-insensitively and
# exactly, so negatives like "I do not agree" are never clicked.
CONSENT_LABELS = (
    "Accept all",
    "Accept all cookies",
    "Accept cookies",
    "I agree",
    "Yes, I agree",
    "I accept",
    "Agree",
    "Allow all",
    "Allow cookies",
    "Got it",
    "Accept",
    "OK",
)


LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]", "0.0.0.0")


def with_scheme(url: str) -> str:
    """Fill in the scheme a person left off.

    https is the right default for the open web, but a dev server on loopback
    almost never speaks it — forcing https there fails with a bare SSL error
    instead of loading the page.
    """
    url = url.strip()
    if url.startswith(("http://", "https://")):
        return url
    host = url.split("/", 1)[0].split(":", 1)[0]
    return f"{'http' if host in LOOPBACK_HOSTS else 'https'}://{url}"


class FrameSink:
    """One screencast subscriber.

    Holds at most one pending frame: a newer frame overwrites an unsent older
    one, so a slow consumer falls behind in *latency* rather than accumulating a
    backlog. Interactive use makes this matter — every mouse move repaints, and
    an unbounded queue would leave the preview trailing the cursor by seconds.
    """

    def __init__(self, on_frame: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._on_frame = on_frame
        self._pending: dict[str, Any] | None = None
        # A strong reference to the in-flight send: a bare create_task() may be
        # garbage-collected mid-flight, which silently drops frames.
        self._task: asyncio.Task | None = None
        self._closed = False

    def offer(self, frame: dict[str, Any]) -> None:
        if self._closed:
            return
        self._pending = frame  # latest wins
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while self._pending is not None and not self._closed:
            frame, self._pending = self._pending, None
            try:
                await self._on_frame(frame)
            except Exception:
                return  # e.g. the socket went away; stop feeding this sink

    def close(self) -> None:
        self._closed = True
        self._pending = None
        if self._task is not None and not self._task.done():
            self._task.cancel()


# --- user-driven input (the interactive preview) -----------------------------
# What performing one gesture does: take the event dict as it arrived from the
# client and drive the page with it.
Perform = Callable[[Page, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class Gesture:
    """One thing a human can do to the page, and what it costs to do it."""

    kind: str
    perform: Perform
    # Whether performing it can move the DOM underneath, so `user_input` can tell
    # its caller when the page is worth re-reading. A hover fires on every few
    # pixels of cursor travel, which is far too often to pay for an evaluate().
    changes_page: bool = True


def _button(name: Any) -> str:
    """The button name arrives off the wire, so it must not reach Playwright raw."""
    return name if name in ("left", "middle", "right") else "left"


# Everything the human can do to the page from the preview, and the only place it
# is written down. Each row owns the coercion its own event shape implies, so
# adding a gesture — a drag, a paste — means adding one row here and teaching the
# frontend to send it. Coordinates are viewport CSS pixels, the same space a
# screencast frame covers, so the frontend only has to undo the <img> scaling.
#
# These deliberately use Playwright's mouse/keyboard API rather than the raw CDP
# Input domain, which would mean hand-rolling virtual key-code tables.
GESTURES: list[Gesture] = [
    Gesture(
        "move",
        lambda page, e: page.mouse.move(e["x"], e["y"]),
        changes_page=False,
    ),
    Gesture(
        "click",
        lambda page, e: page.mouse.click(
            e["x"],
            e["y"],
            button=_button(e.get("button")),
            click_count=max(1, int(e.get("clicks", 1))),
        ),
    ),
    Gesture("scroll", lambda page, e: page.mouse.wheel(e.get("dx", 0), e.get("dy", 0))),
    Gesture("type", lambda page, e: page.keyboard.type(e.get("text", ""))),
    Gesture("key", lambda page, e: page.keyboard.press(e.get("key", ""))),
]

_BY_KIND: dict[str, Gesture] = {gesture.kind: gesture for gesture in GESTURES}


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self._cdp = None
        self._sinks: set[FrameSink] = set()
        self._cdp_listener = None
        self._acks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # headless=True -> no OS window pops up; the UI shows a live screencast
        # that the user can also click and type into (see `user_input`).
        self.browser = await self._pw.chromium.launch(headless=True)
        ctx = await self.browser.new_context(
            viewport=dict(VIEWPORT), locale=BROWSER_LOCALE
        )
        self.page = await ctx.new_page()
        self._cdp = await ctx.new_cdp_session(self.page)
        await self.page.goto("about:blank")

    async def stop(self) -> None:
        for sink in list(self._sinks):
            sink.close()
        self._sinks.clear()
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    # --- actions -----------------------------------------------------------
    async def go_to_url(self, url: str) -> str:
        url = with_scheme(url)
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self.page.wait_for_timeout(1000)  # let consent banners render
        dismissed = await self.dismiss_overlays()
        msg = f"Navigated to {self.page.url}"
        return f"{msg}; {dismissed}" if dismissed else msg

    async def dismiss_overlays(self) -> str | None:
        """Best-effort: accept a cookie/consent dialog so it stops blocking clicks.

        Only clicks buttons whose accessible name exactly matches a known
        "accept" label (case-insensitive), across every frame — so it never
        hits "I do not agree", "Reject", or "Manage options".
        """
        for frame in self.page.frames:
            for label in CONSENT_LABELS:
                try:
                    button = frame.get_by_role("button", name=label, exact=True)
                    if await button.count() and await button.first.is_visible():
                        await button.first.click(timeout=2000)
                        await self.page.wait_for_timeout(400)
                        return f"Accepted a cookie/consent dialog ({label!r})"
                except Exception:
                    continue
        return None

    async def click(self, index: int) -> str:
        loc = self.page.locator(f'[data-agent-idx="{index}"]')
        label = (await loc.inner_text())[:80] if await loc.count() else ""
        try:
            await loc.first.click(timeout=10000)
        except Exception:
            # A late consent/overlay may be intercepting the click; clear it
            # and retry once before giving up.
            if not await self.dismiss_overlays():
                raise
            await loc.first.click(timeout=10000)
        await self.page.wait_for_timeout(800)
        return f"Clicked element [{index}] {label!r}"

    async def input_text(self, index: int, text: str) -> str:
        loc = self.page.locator(f'[data-agent-idx="{index}"]')
        await loc.first.fill(text, timeout=10000)
        return f"Typed {text!r} into element [{index}]"

    async def press_enter(self) -> str:
        await self.page.keyboard.press("Enter")
        await self.page.wait_for_timeout(800)
        return "Pressed Enter"

    async def scroll(self, direction: str = "down") -> str:
        dy = 700 if direction == "down" else -700
        await self.page.mouse.wheel(0, dy)
        await self.page.wait_for_timeout(300)
        return f"Scrolled {direction}"

    async def extract_text(self) -> str:
        text = await self.page.evaluate("() => document.body.innerText")
        return text.strip()[:4000]

    # --- user-driven input (the interactive preview) ------------------------
    async def user_input(self, event: dict[str, Any]) -> bool:
        """Perform one gesture from the preview; say whether the page may have moved.

        The event dict is taken whole: no caller unpacks it, names a kind, or has
        to know which gestures are worth re-reading the page after. A kind no row
        claims does nothing, and by doing nothing has changed nothing.
        """
        gesture = _BY_KIND.get(event.get("kind"))
        if gesture is None:
            return False
        await gesture.perform(self.page, event)
        return gesture.changes_page

    # --- observation -------------------------------------------------------
    async def elements(self) -> list[dict[str, Any]]:
        try:
            return await self.page.evaluate(COLLECT_JS)
        except Exception:
            return []

    async def screenshot_b64(self) -> str:
        img = await self.page.screenshot(type="jpeg", quality=70)
        return base64.b64encode(img).decode("ascii")

    def url(self) -> str:
        return self.page.url if self.page else "about:blank"

    # --- live screencast ---------------------------------------------------
    # Chrome DevTools' screencast pushes a JPEG on every visual change without
    # interfering with page actions. Several clients may watch the same session,
    # so frames fan out to a set of sinks and the CDP stream itself runs only
    # while at least one of them is attached.
    async def add_frame_sink(
        self, on_frame: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> FrameSink:
        """Subscribe to live frames. Each is {"data": b64_jpeg, "meta": {...}}."""
        sink = FrameSink(on_frame)
        self._sinks.add(sink)
        if len(self._sinks) == 1:
            await self._start_screencast()
        # CDP only pushes a frame when something repaints, so a subscriber that
        # joins while the page is idle would see nothing at all until it next
        # changes. Prime it with the current view so the preview is never blank.
        sink.offer(
            {
                "data": await self.screenshot_b64(),
                "meta": {"width": VIEWPORT["width"], "height": VIEWPORT["height"]},
            }
        )
        return sink

    async def remove_frame_sink(self, sink: FrameSink) -> None:
        sink.close()
        self._sinks.discard(sink)
        if not self._sinks:
            await self._stop_screencast()

    def _on_cdp_frame(self, params: dict[str, Any]) -> None:
        # Ack every frame immediately and unconditionally. Chromium throttles
        # and then stops the screencast if acks dry up, so acking must not be
        # coupled to whether any client is keeping up with the frames.
        ack = asyncio.create_task(self._ack(params.get("sessionId")))
        self._acks.add(ack)
        ack.add_done_callback(self._acks.discard)

        meta = params.get("metadata") or {}
        frame = {
            "data": params["data"],
            # Width/height let the frontend map a click on the scaled <img>
            # back to a viewport coordinate without assuming the viewport size.
            "meta": {
                "width": meta.get("deviceWidth", VIEWPORT["width"]),
                "height": meta.get("deviceHeight", VIEWPORT["height"]),
            },
        }
        for sink in list(self._sinks):
            sink.offer(frame)

    async def _ack(self, session_id: Any) -> None:
        with contextlib.suppress(Exception):
            await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})

    async def _start_screencast(self) -> None:
        self._cdp_listener = self._on_cdp_frame
        self._cdp.on("Page.screencastFrame", self._cdp_listener)
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 60,
                "maxWidth": VIEWPORT["width"],
                "maxHeight": VIEWPORT["height"],
            },
        )

    async def _stop_screencast(self) -> None:
        with contextlib.suppress(Exception):
            await self._cdp.send("Page.stopScreencast")
        if self._cdp_listener is not None:
            self._cdp.remove_listener("Page.screencastFrame", self._cdp_listener)
            self._cdp_listener = None
