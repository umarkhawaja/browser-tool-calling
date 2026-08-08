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

### Step 1 — the tool table

`TOOLS` in `agent.py` is the one place an action is written down. Each row
carries both halves of a capability — the schema the model is shown, and the
handler that runs it:

```python
_tool(
    "click",
    "Click an interactive element by its index from the current page listing.",
    {"index": {"type": "integer", "description": "Element index"}},
    ["index"],
    run=lambda browser, args: browser.click(int(args["index"])),
)
```

`_tool()` expands that into Ollama's function-schema nesting, so the table reads
as a list of what the agent can do rather than four levels of dict. There is one
row per action: `go_to_url`, `click`, `input_text`, `press_enter`, `scroll`,
`extract_text`, `dismiss_dialog`.

Keeping the handler beside the schema is what makes the two impossible to drift
apart, and it puts each tool's argument coercion — `int(args["index"])`, the
default for a missing `direction` — next to the schema that declared the
argument in the first place.

### Step 2 — the call

`llm.chat_tools(messages, SCHEMAS)` POSTs to Ollama with the `tools` field and
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

There is no dispatch layer to speak of, which is the point. `_run_tool_call`
looks the name up in the table and runs whatever that row carries:

```python
tool = _BY_NAME.get(name)
result = await tool.run(browser, args) if tool else f"Unknown tool {name!r}."
```

An invented tool name comes back as a *result the model can read* rather than an
exception, so it can correct itself on the next turn.

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
    ┌────────────────────────────────────────────────────┐
    │ 1. ask:   chat_tools(fit_to_context(messages), …)   │◄── llama3.1
    │ 2. call:  message.tool_calls → {name, arguments}    │
    │ 3. act:   TOOLS[name].run(browser, args)            │──► Chromium
    │ 4. feed:  role:"tool" result + new page state       │
    └────────────────────────┬───────────────────────────┘
                            │ repeat until a plain-text answer (no tool_calls)
```

### Why it does not simply remember everything

Growing forever is not an option: the model's context window (`num_ctx`, 8192
tokens) is finite, and each turn adds a page listing plus as much as 4000
characters of extracted text. Ollama enforces the window by discarding messages
from the **front**, and it says nothing when it does — so the first things lost
are the system prompt and the task itself. The agent then keeps browsing,
fluently, having forgotten what it was asked. That failure is invisible in the
trace, which is what makes it worth the machinery.

So `fit_to_context` chooses what to forget, and forgets from the right end. The
system prompt and the task are pinned. The newest turn is kept exactly as it is,
because its listing is what the model is about to click on. Every older turn
collapses to a single line — the result's first sentence, without its page
listing — and only then does whatever budget remains buy recent turns back to
full text. Old listings are the bulk, and they are the safest thing to lose:
their indices are refused by the browser anyway, so all they can do is tempt the
model into a stale click.

That order was not the first guess. Keeping the last three turns whole *first*
sounds right and measures badly: on a page as heavy as Hacker News three
verbatim turns fill the window between them, so a 15-step run arrived at step 15
with no trace of steps 1–12 at all — free to repeat work it had already done. A
summary line costs a twentieth of the turn it stands for, so every step buys one
before any step buys its full text back.

The full transcript stays in memory. Trimming applies only to the copy handed to
the model, and is redone every step, so the window of full detail slides along
with the run.

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

**The number belongs to the element, not to its place in the list.** An element
that already has one keeps it, however many times the page is re-read; only new
elements take a number, from a counter that never goes backwards.

That matters because every old listing is still sitting in the model's
transcript. If each reading renumbered from `[0]`, an index the model read three
steps ago would still resolve — to whatever happened to be element 8 *now*. That
is the nastiest kind of bug: a wrong click that looks perfectly correct in the
trace. Because numbers stay with their elements, a number from a page the agent
has left matches nothing, and `BrowserSession` refuses it with a message the
model reads next to a fresh listing:

> Element [8] is not on the page as it is now. Use an index from the listing
> below — it is the only one that still applies.

The other half is just as important: re-reading a page that has *not* moved
leaves every number where it was. An earlier version of this renumbered on every
reading, and llama3.1 reacted by inventing small indices and spending its entire
step budget being refused.

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
                    ──▶  browser.user_input(event)
                    ──▶  page.mouse.click(640, 400)        ──▶  Chromium
```

The socket hands that dict straight to `BrowserSession.user_input`, which owns the
whole input vocabulary: `GESTURES` in `browser.py` holds one row per gesture —
how to perform it, and whether it can move the DOM. A row answering *yes* is what
makes the socket re-read the page afterwards, so the URL bar and the element
overlay stay honest without a hover costing an `evaluate()` on every pixel.

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
