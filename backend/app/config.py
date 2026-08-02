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
