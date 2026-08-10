# Local Browser Agent

A small app where a **local LLM** (llama3 via [Ollama](https://ollama.com)) drives a
browser to do things on the web from chat prompts.

- **Backend** — Python, FastAPI + a WebSocket, low-level agent loop, Playwright.
- **Frontend** — React (Vite): a chat side panel + a live preview of the browser.
- Chromium runs **headless** (no window pops up); the page is streamed live into
  the in-app preview pane via a CDP screencast.
- The preview is **interactive**: press *Take control* to click, type and scroll
  in the very same page the agent is driving — handy when it gets stuck on a
  login or a cookie wall. The agent pauses while you have it.
- Replies **stream token by token** as the model generates them.
- An **agent view** flips the preview to show the numbered elements the model
  actually reasons over — the fastest way to see why it clicked the wrong thing.

```
┌────────────┬────────────────────────────┐
│  Chat      │   Live browser preview      │
│  (agent    │   (streamed from Playwright │
│   side     │    — and clickable: you can │
│   panel)   │    take over any time)      │
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
cd backend && ./run.sh          # headless browser; preview shows in the UI
cd frontend && npm run dev
```

Open http://localhost:5173 and ask the agent something, e.g.
*"Go to Hacker News and tell me the top story."*

## Tests

```bash
./test.sh          # backend (pytest) + frontend (vitest), concurrently
./lint.sh          # ruff + eslint; pass --fix to apply what can be fixed
```

Those fake both the model and the browser, so they need neither Ollama nor
Chromium. To check the whole stack really fits together — tool calling, clicking
by index, cookie walls, the answer at the end — there is an opt-in eval that
drives real tasks through a real browser against a fixture site on loopback:

```bash
cd backend && ./.venv/bin/python tools/browse_eval.py     # needs Ollama + Chromium
```

```
  price  Go to http://127.0.0.1:61397 and tell me the price of the Nimbus 3000.
    · go_to_url     Navigated to http://127.0.0.1:61397/; Accepted a cookie/consent…
    · click         Clicked element [0] 'Products'
    · click         Clicked element [3] 'Nimbus 3000'
    · extract_text  Nimbus 3000 Price: £42 Made in Bristol. Back to products
    answer          The price of the Nimbus 3000 is £42.
    ✓ pass  4 actions, 0 refused, 12.4s
```

## Configuration

Environment variables read by the backend:

| Var            | Default                   | Meaning                          |
|----------------|---------------------------|----------------------------------|
| `MODEL`        | `llama3.1`                | Ollama model name                |
| `ROUTER_MODEL` | same as `MODEL`           | Model for chat/browse routing    |
| `BROWSER_LOCALE` | `en-US`                 | Locale the browser presents to sites |
| `OLLAMA_URL`   | `http://localhost:11434`  | Ollama server URL                |
| `MAX_STEPS`    | `15`                      | Max actions per task             |
| `BACKEND_PORT` | `8008`                    | Port the API/WebSocket listens on |
| `ALLOWED_ORIGINS` | dev frontend, comma-separated | Origins allowed to open the WebSocket |

Routing only has to answer "chat or browse?", so it can run on a much smaller
model than the agent — `ROUTER_MODEL=llama3.2:1b` shaves latency off every
message you send.

A WebSocket handshake ignores the same-origin policy, so the backend refuses one
carrying an `Origin` it does not recognise — otherwise any page open in any
browser could connect and drive the agent. The default allows the dev frontend
on `localhost` and `127.0.0.1`, following `FRONTEND_PORT`; serving the UI from
anywhere else means listing that origin.

The frontend targets `ws://localhost:8008/ws`; override with `VITE_WS_URL`.

```bash
MODEL=llama3 BACKEND_PORT=9000 VITE_WS_URL=ws://localhost:9000/ws ./dev.sh
```

## How it works

**Chat vs. browse.** Every message first goes through a quick router
([router.py](backend/app/router.py)): a small classifier model decides whether
answering needs the live web. Plain conversation gets a direct text reply, while
anything that needs the web starts the browser agent. So you can just talk to it
in one input box — the browser only opens when a task actually needs it. Press
**Stop** to cancel a run mid-way.

The classifier judges *meaning*, in **any language** — `busca las últimas
noticias` and `今日のトップニュースは？` both open the browser, while "how are you
today?" does not. (An earlier keyword-matching version got all four of those
wrong.) `backend/tools/routing_eval.py` scores it against a labelled
multilingual set if you want to tune the prompt.

When a task runs, the agent uses the model's **native tool calling**
(Ollama's `tools` API): it passes typed function schemas and the model returns a
structured `tool_calls` array. Each step it:
1. asks the model which tool to call, given the current page (URL + a numbered
   list of interactive elements, tagged in the DOM so clicks never rely on
   guessed CSS selectors),
2. executes the chosen tool in Playwright (`go_to_url`, `click`, `input_text`,
   `press_enter`, `scroll`, `extract_text`, `dismiss_dialog`),
3. feeds the result **plus the new page state** back as a `tool` message,
4. streams a screenshot + narration to the UI,

…looping until the model replies with a plain-text answer (no tool call) or it
hits the step limit (15). The model's text streams into the chat as it is
generated rather than appearing all at once.

**Taking over.** The preview is not just a picture. Press **Take control** and
your clicks, keystrokes and scrolling go straight to the agent's page over the
same WebSocket, dispatched by Playwright at viewport coordinates. The agent
pauses at its next step boundary, waits while you work, and when you press
**Give back to agent** it re-reads the page and carries on from what you left.
That is the escape hatch for logins, CAPTCHAs and anything an 8B model fumbles.

> It is a live view of the agent's own Chromium, not an `<iframe>` of the site.
> Most sites refuse to be framed (`X-Frame-Options`), and an iframe would load
> the page in *your* session rather than the agent's — you would be looking at a
> different page from the one it is driving.

**The agent view.** The model never sees the screenshot — it sees a numbered list
like `[8] <a> News`, and clicks by index. Press **V** (or the toggle above the
preview) and the pane draws that list back onto the page: an amber box and index
over every element the model can address, with the one it just clicked marked.

It is the quickest way to understand a wrong click, and it shows a limit that is
otherwise invisible — the model is only given the first 60 elements, so on a
dense page (Hacker News' front page has ~228) everything past the first handful
of stories is simply unreachable. The status strip says how many were left out.

## Files

```
backend/
  pyproject.toml           deps + tooling config (single source of truth)
  app/
    main.py                FastAPI app + WebSocket protocol
    agent.py               the tool-calling agent loop + tool schemas
    browser.py             Playwright wrapper (headless browser, actions, screencast)
    router.py              chat vs. browse routing
    llm.py                 Ollama client (JSON mode + native tool calling)
    config.py              env-based configuration
  tests/                   pytest suite (faked LLM + browser)
frontend/
  src/
    App.jsx                thin layout
    hooks/useAgentSocket.js  WebSocket connection + chat/preview state
    components/TopBar.jsx         identity, model, step budget, link state
    components/ChatPanel.jsx      transcript, step trace, composer
    components/PreviewWindow.jsx  address bar, live page, agent view, handoff
    components/…           TopBar, ChatPanel, PreviewWindow, AgentView, …
    lib/pageInput.js       events → the input protocol; coordinate scaling
    lib/transcript.jsx     prose rendering + folding narration into traces
    hooks/                 the socket, and page input for the preview
    index.css              the console: token system + layout
  eslint.config.js         lint rules
backend/tools/routing_eval.py  scores the chat/browse classifier (needs Ollama)
backend/tools/browse_eval.py   whole tasks vs. a fixture site (needs Ollama + Chromium)
lint.sh                    run both linters
dev.sh                     run backend + frontend together
test.sh                    run both test suites
```

## License

MIT — see [LICENSE](LICENSE).
