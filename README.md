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
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium
```

Frontend:
```bash
cd frontend
npm install
```

## Run

Two terminals:

```bash
# 1) backend  (opens the visible browser on first chat)
cd backend && ./run.sh
```
```bash
# 2) frontend
cd frontend && npm run dev
```

Open http://localhost:5173 and ask the agent something, e.g.
*"Go to Hacker News and tell me the top story."*

## Configuration

Environment variables read by the backend:

| Var          | Default                   | Meaning                     |
|--------------|---------------------------|-----------------------------|
| `MODEL`      | `llama3.1`                | Ollama model name           |
| `OLLAMA_URL` | `http://localhost:11434`  | Ollama server URL           |

```bash
MODEL=llama3 ./run.sh
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
  main.py       FastAPI app + WebSocket protocol
  agent.py      the agent loop + system prompt
  browser.py    Playwright wrapper (visible browser, actions, screenshots)
  llm.py        Ollama JSON chat client
frontend/
  src/App.jsx                    WebSocket state + layout
  src/components/ChatPanel.jsx   chat side panel
  src/components/PreviewWindow.jsx  live browser preview
  src/styles.css                 minimal dark UI
```
