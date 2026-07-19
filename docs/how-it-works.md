# How It Works

A walkthrough of the whole system: how the pieces connect, how a message flows
end-to-end, and — the interesting part — **how the agent decides which tool to
call** and how commands are recognized.

## 1. The big picture

Everything talks over **one WebSocket**. The frontend sends `{message}` or
`{stop}`; the backend streams back typed events (`screenshot`, `thought`,
`action`, `answer`, `note`, `error`, `status`).

```
┌─────────────── FRONTEND (React) ───────────────┐        ┌───────────── BACKEND (Python) ─────────────┐
│  ChatPanel ──type──► useAgentSocket ──WebSocket─┼───────►│  main.py (/ws)                              │
│  PreviewWindow ◄──frames/messages── useAgentSocket◄──────┤    │                                        │
└─────────────────────────────────────────────────┘        │    ├─ router.py   chat? or browse?         │
                                                            │    ├─ agent.py    the think→act loop        │
                                                            │    │    └─ llm.py  ──HTTP──► Ollama (llama3) │
                                                            │    └─ browser.py  ──CDP──► headless Chromium │
                                                            └─────────────────────────────────────────────┘
```

| File | Role |
|------|------|
| [`frontend/src/hooks/useAgentSocket.js`](../frontend/src/hooks/useAgentSocket.js) | Owns the WebSocket: sends messages, collects events, exposes `send()` / `stop()` |
| [`frontend/src/components/ChatPanel.jsx`](../frontend/src/components/ChatPanel.jsx) | Chat side panel (input, message list, Stop button) |
| [`frontend/src/components/PreviewWindow.jsx`](../frontend/src/components/PreviewWindow.jsx) | Renders the live browser frames |
| [`backend/app/main.py`](../backend/app/main.py) | FastAPI app + the `/ws` WebSocket handler |
| [`backend/app/router.py`](../backend/app/router.py) | Decides chat vs. browse |
| [`backend/app/agent.py`](../backend/app/agent.py) | The think → act → observe loop + the system prompt |
| [`backend/app/browser.py`](../backend/app/browser.py) | Playwright wrapper: actions, element listing, screencast, cookie handling |
| [`backend/app/llm.py`](../backend/app/llm.py) | Thin Ollama client: `chat_json` (router) + `chat_tools` (native tool calling) |
| [`backend/app/config.py`](../backend/app/config.py) | Env-based configuration |

## 2. Following one message end-to-end

Say you type *"check top stories on bbc"*:

1. **`useAgentSocket.js`** adds your bubble and sends
   `{"message": "check top stories on bbc"}` over the socket.
2. **`main.py`** `ws()` receives it and calls `route()`.
3. **`router.py`** decides *browse* (the word "bbc" + "top stories" trips the
   guardrail). If it were "hi", it would return a `chat` reply and stop here.
4. `main.py` starts the screencast and calls **`run_agent(...)`** in **`agent.py`**.
5. The agent loops: ask llama3 **which tool to call** → run it in **`browser.py`**
   → feed the result + new page state back → repeat, streaming
   `thought` / `action` / `screenshot` events the whole time.
6. When the model replies with a plain-text answer (no tool call), `main.py`
   sends `status: idle`.

## 3. The core question: how does the agent know which tool to use?

We use the model's **native tool calling**. llama3.1 is fine-tuned to emit
structured function calls, so we hand Ollama a list of typed tool schemas and it
returns *which tool to call* plus its arguments — no prompt-engineered JSON, no
hand-parsing.

### Step 1 — the tool schemas

`TOOLS` in `agent.py` defines every action as an Ollama function schema:

```python
{"type": "function", "function": {
    "name": "click",
    "description": "Click an interactive element by its index from the current page listing.",
    "parameters": {"type": "object",
        "properties": {"index": {"type": "integer"}},
        "required": ["index"]}}}
```

There is one per action: `go_to_url`, `click`, `input_text`, `press_enter`,
`scroll`, `extract_text`, `dismiss_dialog`.

### Step 2 — the call

`llm.chat_tools(messages, TOOLS)` POSTs to Ollama with the `tools` field and
returns the raw assistant message. When the model wants to act, that message
carries a `tool_calls` array:

```json
{"role": "assistant", "content": "",
 "tool_calls": [{"function": {"name": "click", "arguments": {"index": 8}}}]}
```

The model chose the tool **and** filled in typed arguments. That is the model
doing the "recognition" — trained behavior, not string matching.

### Step 3 — what it can act on *right now*

The model isn't guessing indices. Each tool result we feed back includes the
current page from `_format_state()`:

```
Current URL: https://www.bbc.com/
Interactive elements:
  [0] <a> Home
  [8] <a> News
  [12] <button> Sign In
```

Crucial insight: **the model never sees the screenshot.** The preview image is
for the human. The model navigates purely from this text list — it reads
`[8] News` and calls `click(index=8)`.

### Step 4 — the dispatch

`_execute(browser, name, args)` in `agent.py` maps the tool name to a
`BrowserSession` method:

```python
if name == "go_to_url":      return await browser.go_to_url(args["url"])
if name == "click":          return await browser.click(int(args["index"]))
if name == "dismiss_dialog": return await browser.dismiss_overlays() or "No dialog found."
```

### Step 5 — finishing

There is **no `done` tool**. The model finishes by replying with *no tool call* —
just a plain-language answer. `run_agent` sees the empty `tool_calls` and emits
that text as the `answer`.

### Step 6 — a robustness nudge

Small local models sometimes *write* a tool call as JSON text (e.g.
`{"name": "extract_text", ...}`) instead of actually calling it.
`_looks_like_tool_json()` catches that and nudges the model back to the real tool
interface rather than mistaking the JSON for a final answer.

### The feedback loop (this is the "agent" part)

After running each tool, we append a `role: "tool"` message containing the
result **plus the new page state**, so the model's next decision is informed:

```
role: "tool", content: "Clicked element [8] 'News'

Current URL: https://www.bbc.com/news
Interactive elements:
  [0] <a> Home
  ..."
```

This observe → call → execute → feed-back cycle is the classic **ReAct loop**,
running until the model answers in plain text or hits `MAX_STEPS` (15). The
`messages` list grows each turn, so the model remembers what it already tried.

```
    ┌────────────────────────────────────────────────┐
    │ 1. ask:   chat_tools(messages, TOOLS)            │◄── llama3.1
    │ 2. call:  message.tool_calls → {name, arguments} │
    │ 3. act:   _execute(browser, name, args)          │──► Chromium
    │ 4. feed:  role:"tool" result + new page state    │
    └────────────────────────┬───────────────────────┘
                            │ repeat until a plain-text answer (no tool_calls)
```

## 4. How it recognizes commands (chat vs. browse)

Before the agent ever runs, `router.py` decides whether your message even
*needs* the browser. Two stages:

- **Deterministic guardrail** — `_looks_like_browse()` checks for a URL or
  high-signal words (`search`, `latest`, `top stories`, `summary of`, a
  `.com`…). If it matches, it is *browse* immediately, with no model call.
- **LLM classifier** — anything ambiguous goes to llama3 with a prompt that
  returns `{"mode":"chat","reply":"..."}` or `{"mode":"browse"}`, biased toward
  browse and forbidden from fabricating.

So "hi" → chat reply (no browser); "top stories on bbc" → browse (runs the
agent).

## 5. The clever bit: clicking by index, not by guessing selectors

How does `click(index=8)` actually click the right thing? In `browser.py`, the
`COLLECT_JS` snippet runs inside the page and, as it lists each interactive
element, stamps it with an attribute:

```js
el.setAttribute('data-agent-idx', String(i));   // [8] → data-agent-idx="8"
```

Then `click(8)` simply does `page.locator('[data-agent-idx="8"]').click()`. The
number the model sees and the DOM attribute are the same, so the model never has
to invent brittle CSS selectors — it points at a number, and Playwright finds
that exact node.

## 6. The supporting machinery

- **Cookie walls** — `go_to_url` and `click` call `dismiss_overlays()`, which
  clicks an "accept"-type button (matched exactly by accessible name across all
  frames) so consent modals stop intercepting clicks.
- **Live preview** — `browser.py` starts a **CDP screencast**; Chromium pushes
  JPEG frames on every visual change, which `main.py` forwards as `screenshot`
  events. That is why you see the page live without a window popping up.
- **Stop** — a browse run is an `asyncio.Task`; `{stop}` calls `.cancel()` on it,
  which raises `CancelledError` inside the loop and unwinds cleanly.

## 7. The honest caveat

Native tool calling gives us typed, structured calls — but the *decision* is
still llama3.1 reasoning over the task plus the element list, so tool selection
is **only as good as the model's judgment**. An 8B local model will sometimes
pick the wrong index, click the wrong link, loop, or even write a tool call as
text (which is why the `_looks_like_tool_json` nudge exists). The guardrails —
typed tool schemas, indexed elements, the nudge, cookie handling, a step cap, and
Stop — exist precisely to keep that fallibility contained. That is the trade-off
for running fully local.
