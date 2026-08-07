"""All runtime configuration, read once from the environment."""

import os

# Ollama server and the local model the agent talks to.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MODEL", "llama3.1")

# The router only ever answers "chat or browse?", so it can run on a smaller,
# faster model than the agent. Defaults to the same one, so nothing extra needs
# pulling; set it to e.g. llama3.2:1b to shave latency off every message.
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", MODEL)

# Locale the browser presents to sites. Without one Playwright sends no
# Accept-Language header at all, so sites fall back to geolocating by IP and can
# serve a language nobody asked for — which also leaves the English-only consent
# labels in browser.py unable to match, so the cookie wall never gets dismissed.
BROWSER_LOCALE = os.environ.get("BROWSER_LOCALE", "en-US")

# Safety cap on how many actions the agent may take for a single task.
MAX_STEPS = int(os.environ.get("MAX_STEPS", "15"))

# Origins allowed to open the WebSocket, comma-separated. A handshake is not
# subject to the same-origin policy — a browser sends `Origin` and then connects
# anyway — so without this list any page you happen to have open could drive the
# agent. The default is the dev frontend, and it follows FRONTEND_PORT so that
# `FRONTEND_PORT=6000 ./dev.sh` needs nothing else set.
_DEV_ORIGINS = ",".join(
    f"http://{host}:{os.environ.get('FRONTEND_PORT', '5173')}"
    for host in ("localhost", "127.0.0.1")
)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", _DEV_ORIGINS).split(",")
    if origin.strip()
]
