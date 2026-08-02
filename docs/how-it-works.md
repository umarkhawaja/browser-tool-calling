# How It Works

A walkthrough of the whole system: how the pieces connect, how a message flows
end-to-end, and — the interesting part — **how the agent decides which tool to
call** and how commands are recognized.

## 1. The big picture

Everything talks over **one WebSocket**. The frontend sends `{message}`,
`{stop}`, `{control}` (take/return the browser), `{input}` (your clicks and
keystrokes for the preview) or `{navigate}`; the backend streams back typed
events (`hello`, `screenshot`, `page`, `token`, `token_reset`, `thought`,
`action`, `answer`, `note`, `error`, `status`). The docstring at the top of
`main.py` is the authoritative spec.

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
| [`frontend/src/hooks/useAgentSocket.js`](../frontend/src/hooks/useAgentSocket.js) | Owns the WebSocket: sends messages, collects events, exposes `send()` / `stop()` / `takeControl()` / `sendInput()` |
| [`frontend/src/components/TopBar.jsx`](../frontend/src/components/TopBar.jsx) | Model, step budget, connection state |
| [`frontend/src/components/ChatPanel.jsx`](../frontend/src/components/ChatPanel.jsx) | Transcript, the step trace, and the composer |
| [`frontend/src/components/PreviewWindow.jsx`](../frontend/src/components/PreviewWindow.jsx) | Address bar, live frames, the agent view, and input forwarding when you take control |
| [`backend/app/main.py`](../backend/app/main.py) | FastAPI app + the `/ws` WebSocket handler |
| [`backend/app/router.py`](../backend/app/router.py) | Decides chat vs. browse |
| [`backend/app/agent.py`](../backend/app/agent.py) | The think → act → observe loop + the system prompt |
| [`backend/app/browser.py`](../backend/app/browser.py) | Playwright wrapper: actions, element listing, screencast, cookie handling |
| [`backend/app/llm.py`](../backend/app/llm.py) | Thin Ollama client: `chat_json` (router), `chat_text` and `chat_tools` (streaming, native tool calling) |
| [`backend/app/config.py`](../backend/app/config.py) | Env-based configuration |

## 2. Following one message end-to-end

Say you type *"check top stories on bbc"*:

1. **`useAgentSocket.js`** adds your bubble and sends
   `{"message": "check top stories on bbc"}` over the socket.
2. **`main.py`** `ws()` receives it and calls `route()`.
3. **`router.py`** asks a small classifier model, which returns *browse*. If it
   were "hi", it would return *chat* and `chat_reply()` would stream a sentence
   back, without ever opening a browser.
4. `main.py` starts the browser (and its live preview, if not already running)
   and calls **`run_agent(...)`** in **`agent.py`**.
5. The agent loops: ask llama3 **which tool to call** → run it in **`browser.py`**
   → feed the result + new page state back → repeat, streaming
   `token` / `thought` / `action` / `screenshot` events the whole time.
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

You can see this directly: press **V** in the UI and the preview switches to the
*agent view*, drawing that numbered list back onto the page. It shows exactly the
elements the model was handed and no others — both the listing and the overlay
cut at `MODEL_ELEMENT_LIMIT` (60), so the picture can never imply the agent is
able to click something it was never told about. On a page with more than sixty
interactive elements the remainder is genuinely unreachable, and the status strip
says how many were dropped.

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
*needs* the browser. A small classifier model reads it and returns
`{"mode":"chat"}` or `{"mode":"browse"}` — nothing else. So "hi" → chat reply
(no browser); "top stories on bbc" → browse (runs the agent).

Classifying and answering are two separate calls. The classifier returns *only*
a mode, which keeps it short and cheap and stops it from trying to answer inside
a JSON field; `chat_reply()` then streams the actual sentence back as plain
text. Because classification is such a small job, it can run on a smaller model
than the agent — set `ROUTER_MODEL`.

### Why not just match keywords?

That is what this used to do, and it was wrong in both directions at once:

```
"how are you today?"              → browse   (matched "today")
"I need to find myself a hobby"   → browse   (matched "find ")
"my email is john.doe@gmail.com"  → browse   (matched the domain pattern)
"busca las últimas noticias"      → no match (the list is English-only)
"今日のトップニュースは？"          → no match
```

Worse, a keyword hit *short-circuited* — those first three never reached a model
at all, so nothing downstream could recover. Whether a message needs the web is
a judgement about meaning, in whatever language it was written, and that is a
model's job. The classifier gets few-shot examples in several scripts, and a
handful of them exist purely to nail down the cases above.

`tools/routing_eval.py` scores the classifier against a labelled multilingual
set so prompt changes can be measured rather than guessed at.

### When routing gets it wrong

It still does, occasionally — and a miss towards *chat* is the dangerous one,
because nothing will go and check. So `CHAT_PROMPT` is written as a backstop: it
forbids stating any outside-world fact, and forbids writing as though it had
browsed. That matters more than it sounds. With only a mild "don't fabricate"
instruction, the model answered *"the current price of Bitcoin on Coinbase is
$43,919"* — a number it made up entirely — and elsewhere narrated *"let me just
browse the web real quick... (pausing to search)"* without ever opening a page.
Under the strict rules it says it hasn't looked, and offers to.

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
  Frames fan out to a set of subscribers, each keeping only the newest pending
  frame, and a new subscriber is primed with a snapshot — CDP emits nothing while
  a page sits still, so otherwise you would join to a blank pane.
- **Stop** — a turn is an `asyncio.Task`; `{stop}` calls `.cancel()` on it,
  which raises `CancelledError` inside the loop and unwinds cleanly. It works
  while the agent is paused too.

## 6b. Taking the wheel

The preview is interactive. `{"control": "user"}` clears an `asyncio.Event` that
the agent loop awaits at the top of every iteration, so it pauses at the next
**step boundary** — a tool call already in flight finishes first. While it waits,
`{"input": ...}` events carry your mouse and keyboard straight to the page:

```
click in the <img>  ──▶  scale by (frame width / rendered width)
                    ──▶  {"kind":"click","x":640,"y":400}  ──WebSocket──▶
                    ──▶  page.mouse.click(640, 400)        ──▶  Chromium
```

The frame is captured at the viewport size and then scaled by CSS, so undoing
that scale is the whole coordinate mapping — no scroll offset is involved,
because the screencast captures the viewport and Playwright's mouse coordinates
are viewport-relative too.

Input uses Playwright's `page.mouse` / `page.keyboard` rather than the raw CDP
`Input` domain, which would mean hand-rolling virtual key-code tables. Playwright
key names match `event.key`, so single characters become `type` and everything
else (`Enter`, `Backspace`, `Control+a`) becomes a named press.

When you hand control back, the loop appends a message telling the model the
human may have changed the page, along with a fresh observation — otherwise it
would keep reasoning from a transcript that no longer matches reality.

## 6c. Why the model's text appears as it is typed

Every model call streams (`stream: true`). `llm.py` reassembles Ollama's NDJSON
chunks into the *same* single message dict a non-streaming call returns, so the
agent loop never learns the reply arrived in pieces — it just gets an extra
`on_token` callback that emits `token` events. Ollama sends each tool call whole
in one chunk (arguments already a dict), so there is nothing to stitch there.

The client shows `token` deltas in a provisional bubble and replaces it the
moment the authoritative `answer`/`thought`/`action` arrives, so nothing is ever
rendered twice.

## 7. The honest caveat

Native tool calling gives us typed, structured calls — but the *decision* is
still llama3.1 reasoning over the task plus the element list, so tool selection
is **only as good as the model's judgment**. An 8B local model will sometimes
pick the wrong index, click the wrong link, loop, or even write a tool call as
text (which is why the `_looks_like_tool_json` nudge exists). The guardrails —
typed tool schemas, indexed elements, the nudge, cookie handling, a step cap, and
Stop — exist precisely to keep that fallibility contained. That is the trade-off
for running fully local.

The last guardrail is you. **Take control** turns the model's weakest moments —
a login form, a CAPTCHA, a date picker it cannot parse — from a dead end into a
two-second intervention: do the bit it cannot, hand the page back, and let it
carry on from there.
