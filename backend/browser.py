"""Playwright browser wrapper: launches a *visible* browser and exposes a small
set of actions the agent can call. After every action it can produce a fresh
screenshot (base64 PNG) and a numbered list of interactive elements so the model
can decide what to do next.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

from playwright.async_api import Browser, Page, async_playwright

# JS that tags every visible interactive element with a stable index and returns
# a compact description. Clicking is then done by that index, so the model never
# has to guess CSS selectors.
COLLECT_JS = """
() => {
  const sel = 'a, button, input, textarea, select, [role=button], [onclick]';
  const els = Array.from(document.querySelectorAll(sel));
  const out = [];
  let i = 0;
  for (const el of els) {
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


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        # headless=False -> the real browser window is visible on the desktop.
        self.browser = await self._pw.chromium.launch(
            headless=False, args=["--window-size=1280,800"]
        )
        ctx = await self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.page = await ctx.new_page()
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
        return f"Navigated to {self.page.url}"

    async def click(self, index: int) -> str:
        loc = self.page.locator(f'[data-agent-idx="{index}"]')
        label = (await loc.inner_text())[:80] if await loc.count() else ""
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
        png = await self.page.screenshot(type="png")
        return base64.b64encode(png).decode("ascii")

    def url(self) -> str:
        return self.page.url if self.page else "about:blank"
