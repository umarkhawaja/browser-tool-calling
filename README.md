# Local Browser Agent

A small app where a **local LLM** (llama3 via [Ollama](https://ollama.com)) drives a
**real, visible browser** to do things on the web from chat prompts.

- **Backend** — Python, FastAPI + a WebSocket, low-level agent loop, Playwright.
- **Frontend** — React (Vite): a chat side panel + a live preview of the browser.
- The browser is **headed** (a real Chromium window opens), *and* every step is
  streamed as a screenshot into the preview pane.

```
┌────────────┬────────────────────────────┐
│  Chat      │   Live browser preview      │
│  (agent    │   (screenshots streamed     │
│   side     │    from Playwright)         │
│   panel)   │                             │
└────────────┴────────────────────────────┘
        React frontend  ◀── WebSocket ──▶  Python backend ──▶ Playwright + Ollama
```

## Prerequisites

1. **Ollama** running with a model pulled:
   ```bash
   ollama pull llama3.1      # recommended (better at following the JSON protocol)
   # or:  ollama pull llama3
   ```
   Ollama serves on `http://localhost:11434` by default.

2. Python 3.9+ and Node 18+.

## Setup

Backend (already scripted, but for reference):
```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"       # runtime + test deps
./.venv/bin/python -m playwright install chromium
```

Frontend:
```bash
cd frontend
npm install
```

## Run

One command runs both the backend (`:8008`) and the frontend (`:5173`):
```bash
./dev.sh
```

Or start them separately in two terminals:
```bash
cd backend && ./run.sh          # opens the visible browser on first chat
cd frontend && npm run dev
```

Open http://localhost:5173 and ask the agent something, e.g.
*"Go to Hacker News and tell me the top story."*

## Tests

```bash
./test.sh          # runs backend (pytest) + frontend (vitest) concurrently
```

## Configuration

Environment variables read by the backend:

| Var            | Default                   | Meaning                          |
|----------------|---------------------------|----------------------------------|
| `MODEL`        | `llama3.1`                | Ollama model name                |
| `OLLAMA_URL`   | `http://localhost:11434`  | Ollama server URL                |
| `MAX_STEPS`    | `15`                      | Max actions per task             |
| `BACKEND_PORT` | `8008`                    | Port the API/WebSocket listens on |

The frontend targets `ws://localhost:8008/ws`; override with `VITE_WS_URL`.

```bash
MODEL=llama3 BACKEND_PORT=9000 VITE_WS_URL=ws://localhost:9000/ws ./dev.sh
```

## How it works

Each step the agent:
1. reads the current page (URL + a numbered list of interactive elements, tagged
   in the DOM so clicks never rely on guessed CSS selectors),
2. asks the model for **one** next action as strict JSON
   (Ollama's `format: "json"` guarantees valid JSON),
3. executes it in Playwright (`go_to_url`, `click`, `input_text`, `press_enter`,
   `scroll`, `extract_text`, `done`),
4. streams a screenshot + narration to the UI,

…looping until the model calls `done` or it hits the step limit (15).

## Files

```
backend/
  pyproject.toml           deps + tooling config (single source of truth)
  app/
    main.py                FastAPI app + WebSocket protocol
    agent.py               the agent loop + system prompt
    browser.py             Playwright wrapper (visible browser, actions, screenshots)
    llm.py                 Ollama JSON chat client
    config.py              env-based configuration
  tests/                   pytest suite (faked LLM + browser)
frontend/
  src/
    App.jsx                thin layout
    hooks/useAgentSocket.js  WebSocket connection + chat/preview state
    components/ChatPanel.jsx      chat side panel
    components/PreviewWindow.jsx  live browser preview
    index.css              minimal dark UI
dev.sh                     run backend + frontend together
test.sh                    run both test suites
```
