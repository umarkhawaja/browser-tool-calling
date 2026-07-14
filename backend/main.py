"""FastAPI app exposing a single WebSocket the React frontend talks to.

Protocol
--------
Client -> server:  {"task": "book me a table ..."}
Server -> client:  a stream of events, each a JSON object with a "type":
    {"type": "screenshot", "data": "<base64 png>"}
    {"type": "thought",    "text": "..."}
    {"type": "action",     "text": "click", "detail": "..."}
    {"type": "answer",     "text": "..."}
    {"type": "error",      "text": "..."}
    {"type": "status",     "text": "idle" | "running"}
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from agent import run_agent
from browser import BrowserSession

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One shared, visible browser for the app. Started lazily on first connection.
_browser: BrowserSession | None = None
_browser_lock = asyncio.Lock()


async def get_browser() -> BrowserSession:
    global _browser
    async with _browser_lock:
        if _browser is None:
            _browser = BrowserSession()
            await _browser.start()
        return _browser


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _browser is not None:
        await _browser.stop()


@app.websocket("/ws")
async def ws(sock: WebSocket) -> None:
    await sock.accept()
    browser = await get_browser()

    async def emit(event: dict) -> None:
        await sock.send_json(event)

    try:
        while True:
            msg = await sock.receive_json()
            task = (msg.get("task") or "").strip()
            if not task:
                continue
            await emit({"type": "status", "text": "running"})
            try:
                await run_agent(task, browser, emit)
            except Exception as e:  # never let one bad run kill the socket
                await emit({"type": "error", "text": f"Agent crashed: {e}"})
            await emit({"type": "status", "text": "idle"})
    except WebSocketDisconnect:
        pass
