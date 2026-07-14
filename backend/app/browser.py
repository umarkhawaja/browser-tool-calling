"""Playwright browser wrapper.

Launches a *visible* browser and exposes a small set of actions the agent can
call. It can also produce a screenshot (base64 PNG) and a numbered list of the
page's interactive elements, so the model always knows what it can act on.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any, Awaitable, Callable, Dict, List, Optional

from playwright.async_api import Browser, Page, async_playwright

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
    out.push({ index: i, tag: el.tagName.toLowerCase(),
               type: el.getAttribute('type') || '', label });
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


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._cdp = None
        self._frame_listener = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # headless=True -> no OS window pops up; the UI shows a live screencast.
        self.browser = await self._pw.chromium.launch(headless=True)
        ctx = await self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.page = await ctx.new_page()
        self._cdp = await ctx.new_cdp_session(self.page)
        await self.page.goto("about:blank")

    async def stop(self) -> None:
        if self.browser:
            await self.browser.close()
        if self._pw:
            await self._pw.stop()

    # --- actions -----------------------------------------------------------
    async def go_to_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self.page.wait_for_timeout(1000)  # let consent banners render
        dismissed = await self.dismiss_overlays()
        msg = f"Navigated to {self.page.url}"
        return f"{msg}; {dismissed}" if dismissed else msg

    async def dismiss_overlays(self) -> Optional[str]:
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

    # --- observation -------------------------------------------------------
    async def elements(self) -> List[Dict[str, Any]]:
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
    async def start_screencast(self, on_frame: Callable[[str], Awaitable[None]]) -> None:
        """Stream live JPEG frames of the page to `on_frame(base64_str)`.

        Uses Chrome DevTools' screencast, which pushes a frame on every visual
        change without interfering with the agent's page actions.
        """
        async def handle(params: Dict[str, Any]) -> None:
            try:
                await on_frame(params["data"])
            except Exception:
                pass  # e.g. the socket went away; just drop the frame
            try:
                await self._cdp.send(
                    "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
                )
            except Exception:
                pass

        self._frame_listener = lambda params: asyncio.create_task(handle(params))
        self._cdp.on("Page.screencastFrame", self._frame_listener)
        await self._cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 60, "maxWidth": 1280, "maxHeight": 800},
        )

    async def stop_screencast(self) -> None:
        try:
            await self._cdp.send("Page.stopScreencast")
        except Exception:
            pass
        if self._frame_listener is not None:
            self._cdp.remove_listener("Page.screencastFrame", self._frame_listener)
            self._frame_listener = None
