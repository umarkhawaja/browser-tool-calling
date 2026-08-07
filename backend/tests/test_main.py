"""The socket's front door: who gets to open it at all.

WebSocket handshakes are not subject to the same-origin policy, so without a
check here any page you happen to have open could connect and drive the agent.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from app.main import app

client = TestClient(app)


def _connect(origin: str | None):
    headers = {"Origin": origin} if origin is not None else {}
    return client.websocket_connect("/ws", headers=headers)


def test_a_page_on_another_origin_cannot_open_the_socket():
    evil = "http://evil.example"
    with pytest.raises(WebSocketDenialResponse) as refusal, _connect(evil) as sock:
        sock.receive_json()
    # An HTTP response rather than a close frame means the handshake itself was
    # refused — the socket was never accepted, so nothing was ever dispatched.
    assert refusal.value.status_code == 403


def test_the_dev_frontend_connects():
    with _connect("http://localhost:5173") as sock:
        assert sock.receive_json()["type"] == "hello"


def test_a_client_that_sends_no_origin_connects():
    # Browsers always send Origin on a handshake; its absence means a non-browser
    # client (curl, a script, this test), which is not the threat being closed.
    with _connect(None) as sock:
        assert sock.receive_json()["type"] == "hello"
