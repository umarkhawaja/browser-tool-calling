"""FastAPI app exposing a single WebSocket the React frontend talks to.

Protocol
--------
Client -> server:
    {"message": "..."}      a chat message (the router decides chat vs browse)
    {"stop": true}          cancel the current run
    {"control": "user"}     take the browser; pauses the agent at its next step
    {"control": "agent"}    hand the browser back; the agent re-observes and resumes
    {"navigate": "url"}     open an address yourself, only while you hold control
    {"input": {...}}        one mouse/keyboard gesture for the live preview, only
                            while you hold control. `GESTURES` in `browser.py` is
                            the vocabulary and what each kind carries; nothing
                            here needs to know either.

Server -> client:  a stream of events, each a JSON object with a "type":
    {"type": "hello",       "model": "...", "max_steps": 15}   sent once, on connect
    {"type": "screenshot",  "data": "<base64 jpeg>", "meta": {"width": w, "height": h}}
    {"type": "page",        "url": "...", "elements": [...], "total": n}
                            the page as the model reads it — each element carries
                            its index, tag, label and viewport rect, so the UI can
                            draw the agent's own view over the live preview
    {"type": "token",       "text": "..."}   one delta of the reply being generated
    {"type": "token_reset"}                  discard the tokens streamed so far
    {"type": "thought",     "text": "..."}
    {"type": "action",      "text": "click", "detail": "..."}
    {"type": "answer",      "text": "..."}   chat reply or agent's final answer
    {"type": "note",        "text": "..."}   system notice (stopped / control)
    {"type": "error",       "text": "..."}
    {"type": "status",      "text": "idle" | "running" | "paused"}

Any of thought/action/answer/note/error is the authoritative version of whatever
was streaming, so the client clears its live token buffer when one arrives.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agent import observe, run_agent
from app.browser import BrowserSession, FrameSink
from app.config import MAX_STEPS, MODEL
from app.llm import LLMError
from app.router import chat_reply, route

Event = dict[str, Any]

BUSY_NOTICE = "Still working on the previous task — press Stop to interrupt."

# One shared, headless browser for the app, started lazily on first use.
_browser: BrowserSession | None = None
_browser_lock = asyncio.Lock()


async def get_browser() -> BrowserSession:
    global _browser
    async with _browser_lock:
        if _browser is None:
            _browser = BrowserSession()
            await _browser.start()
        return _browser


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield  # startup is lazy; nothing to do here
    if _browser is not None:
        await _browser.stop()


app = FastAPI(title="Local Browser Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Connection:
    """One client, and everything scoped to it.

    The browser is shared process-wide, but the screencast subscription, the
    in-flight turn and who currently holds the browser all belong to a single
    socket, so they live here rather than in globals.
    """

    def __init__(self, sock: WebSocket) -> None:
        self.sock = sock
        self.browser: BrowserSession | None = None
        self.sink: FrameSink | None = None
        self.turn: asyncio.Task | None = None
        # Set while the agent may run; cleared while the human holds the browser.
        self.agent_may_run = asyncio.Event()
        self.agent_may_run.set()

    @property
    def human_has_control(self) -> bool:
        return not self.agent_may_run.is_set()

    # --- outbound -----------------------------------------------------------
    async def send(self, event: Event) -> None:
        """Emit one event, tolerating a client that has already gone away.

        A closed socket raises from send, and an in-flight run would otherwise
        die with an unhandled error instead of unwinding cleanly.
        """
        with suppress(WebSocketDisconnect, RuntimeError):
            await self.sock.send_json(event)

    async def _send_frame(self, frame: Event) -> None:
        await self.send({"type": "screenshot", **frame})

    async def ensure_browser(self) -> BrowserSession:
        """Start the browser (once) and keep a live preview for this connection.

        The screencast deliberately outlives any single run: between tasks is
        exactly when you want to click around — to log in somewhere, say.
        """
        self.browser = await get_browser()
        if self.sink is None:
            self.sink = await self.browser.add_frame_sink(self._send_frame)
        return self.browser

    async def close(self) -> None:
        if self.turn and not self.turn.done():
            self.turn.cancel()
        if self.browser is not None and self.sink is not None:
            await self.browser.remove_frame_sink(self.sink)

    # --- inbound ------------------------------------------------------------
    async def take_control(self) -> None:
        self.agent_may_run.clear()
        await self.ensure_browser()  # so there is a page to interact with
        await self.send({"type": "note", "text": "You have control of the browser."})
        await observe(self.browser, self.send)  # seed the URL bar + overlay

    async def release_control(self) -> None:
        self.agent_may_run.set()
        await self.send({"type": "note", "text": "Control handed back to the agent."})

    async def dispatch_input(self, event: Event) -> None:
        """Apply one mouse/keyboard event to the page the human is driving."""
        # Stray events are ignored unless the human actually holds the browser,
        # so a mis-click can never land in the middle of an agent step.
        if self.browser is None or not self.human_has_control:
            return
        try:
            may_have_moved = await self.browser.user_input(event)
        except Exception as e:
            await self.send({"type": "error", "text": f"Input failed: {e}"})
            return
        # Re-read the page when the gesture could have changed it, so the URL and
        # the element overlay stay honest.
        if may_have_moved:
            await observe(self.browser, self.send)

    async def navigate(self, url: str) -> None:
        """Let the human type an address — the agent's tools are not theirs."""
        url = url.strip()
        if self.browser is None or not self.human_has_control or not url:
            return
        try:
            await self.send({"type": "note", "text": await self.browser.go_to_url(url)})
        except Exception as e:
            await self.send({"type": "error", "text": f"Could not open {url}: {e}"})
        await observe(self.browser, self.send)

    def start_turn(self, text: str) -> None:
        """Routing calls the model, so a turn runs as a task: the receive loop
        must stay free to service Stop and preview input meanwhile."""
        self.turn = asyncio.create_task(self._run_turn(text))

    @property
    def turn_in_flight(self) -> bool:
        return self.turn is not None and not self.turn.done()

    async def _run_turn(self, text: str) -> None:
        """Route the message, then either reply directly or drive the browser."""
        await self.send({"type": "status", "text": "running"})

        async def on_token(delta: str) -> None:
            await self.send({"type": "token", "text": delta})

        try:
            if (await route(text))["mode"] == "chat":
                reply = await chat_reply(text, on_token)
                await self.send({"type": "answer", "text": reply})
            else:
                browser = await self.ensure_browser()
                await run_agent(text, browser, self.send, self.agent_may_run)
        except asyncio.CancelledError:
            await self.send({"type": "note", "text": "Stopped."})
        except LLMError as e:
            await self.send({"type": "error", "text": str(e)})
        except Exception as e:  # never let one bad run kill the socket
            await self.send({"type": "error", "text": f"Agent crashed: {e}"})
        finally:
            await self.send({"type": "status", "text": "idle"})

    async def handle(self, msg: Event) -> None:
        """Dispatch one client message."""
        if msg.get("stop"):
            if self.turn_in_flight:
                self.turn.cancel()

        elif "control" in msg:
            if msg.get("control") == "user":
                await self.take_control()
            else:
                await self.release_control()

        # Input is awaited inline so events stay in order — a click must land
        # before the keystrokes that follow it.
        elif "input" in msg:
            await self.dispatch_input(msg.get("input") or {})

        elif "navigate" in msg:
            await self.navigate(str(msg.get("navigate") or ""))

        elif text := (msg.get("message") or "").strip():
            # One turn at a time; the browser is shared and cannot be split.
            if self.turn_in_flight:
                await self.send({"type": "note", "text": BUSY_NOTICE})
            else:
                self.start_turn(text)


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    conn = Connection(sock)

    # Tell the client what it is driving, so the UI can label the model and
    # size the step budget instead of hard-coding either.
    await conn.send({"type": "hello", "model": MODEL, "max_steps": MAX_STEPS})

    try:
        while True:
            await conn.handle(await sock.receive_json())
    # A client that vanishes mid-handshake surfaces as RuntimeError from receive
    # rather than WebSocketDisconnect; both just mean "this connection is over".
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await conn.close()
