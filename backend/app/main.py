"""FastAPI app exposing a single WebSocket the React frontend talks to.

Protocol
--------
Client -> server:
    {"message": "..."}   a chat message (the router decides chat vs browse)
    {"stop": true}       cancel the current browser run

Server -> client:  a stream of events, each a JSON object with a "type":
    {"type": "screenshot", "data": "<base64 png>"}
    {"type": "thought",    "text": "..."}
    {"type": "action",     "text": "click", "detail": "..."}
    {"type": "answer",     "text": "..."}   chat reply or agent's final answer
    {"type": "note",       "text": "..."}   system notice (stopped / busy)
    {"type": "error",      "text": "..."}
    {"type": "status",     "text": "idle" | "running"}
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.agent import run_agent
from app.browser import BrowserSession
from app.llm import LLMError
from app.router import route

# One shared, visible browser for the app, started lazily on first use.
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


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    run: asyncio.Task | None = None  # the in-flight browser run, if any

    async def do_browse(task: str) -> None:
        await sock.send_json({"type": "status", "text": "running"})
        try:
            browser = await get_browser()  # opens the window on first browse only
            await run_agent(task, browser, sock.send_json)
        except asyncio.CancelledError:
            await sock.send_json({"type": "note", "text": "Stopped."})
        except Exception as e:  # never let one bad run kill the socket
            await sock.send_json({"type": "error", "text": f"Agent crashed: {e}"})
        finally:
            await sock.send_json({"type": "status", "text": "idle"})

    try:
        while True:
            msg = await sock.receive_json()

            # Stop the current run.
            if msg.get("stop"):
                if run and not run.done():
                    run.cancel()
                continue

            text = (msg.get("message") or "").strip()
            if not text:
                continue

            # Plain chat, or drive the browser?
            try:
                decision = await route(text)
            except LLMError as e:
                await sock.send_json({"type": "error", "text": str(e)})
                continue

            if decision["mode"] == "chat":
                await sock.send_json({"type": "answer", "text": decision["reply"]})
                continue

            # A browse task: only one run at a time (single shared browser).
            if run and not run.done():
                await sock.send_json(
                    {"type": "note",
                     "text": "Still working on the previous task — press Stop to interrupt."}
                )
                continue
            run = asyncio.create_task(do_browse(text))
    except WebSocketDisconnect:
        if run and not run.done():
            run.cancel()
