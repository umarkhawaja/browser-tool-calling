# Code Walkthrough — Frontend ⇄ Backend, Every Call

A precise, presentation-ready trace of the system: every function, what it does,
what it calls, and how a single user message travels from the React input box,
through the WebSocket, into the agent loop and the browser, and back to the UI.

- **Frontend:** React (Vite) — a chat panel and a live browser preview.
- **Backend:** FastAPI + a single WebSocket, a chat/browse router, a ReAct-style
  agent loop, Playwright driving headless Chromium, and a local Ollama model.
- **One connection:** the browser and server talk over **one WebSocket**
  (`ws://localhost:8008/ws`).

---

## 1. Component map

| Layer | File | Responsibility |
|-------|------|----------------|
| UI entry | [`frontend/src/main.jsx`](../frontend/src/main.jsx) | Mounts `<App/>` into the DOM |
| UI shell | [`frontend/src/App.jsx`](../frontend/src/App.jsx) | Wires the socket hook to the two panels |
| Networking | [`frontend/src/hooks/useAgentSocket.js`](../frontend/src/hooks/useAgentSocket.js) | The WebSocket client + all UI state |
| Chat UI | [`frontend/src/components/ChatPanel.jsx`](../frontend/src/components/ChatPanel.jsx) | Input box, message list, Stop button |
| Preview UI | [`frontend/src/components/PreviewWindow.jsx`](../frontend/src/components/PreviewWindow.jsx) | Renders live browser frames |
| Server | [`backend/app/main.py`](../backend/app/main.py) | FastAPI app + `/ws` handler + orchestration |
| Router | [`backend/app/router.py`](../backend/app/router.py) | Chat reply vs. drive-the-browser decision |
| Agent | [`backend/app/agent.py`](../backend/app/agent.py) | The think → act → observe loop |
| Browser | [`backend/app/browser.py`](../backend/app/browser.py) | Playwright actions, element listing, screencast, cookies |
| LLM | [`backend/app/llm.py`](../backend/app/llm.py) | Calls Ollama, returns parsed JSON |
| Config | [`backend/app/config.py`](../backend/app/config.py) | Env-based settings (`MODEL`, `MAX_STEPS`, …) |

---

## 2. The wire protocol (what crosses the WebSocket)

**Client → server** (JSON):

| Message | Meaning |
|---------|---------|
| `{"message": "..."}` | A user chat message |
| `{"stop": true}` | Cancel the running browser task |

**Server → client** (JSON, each has a `type`):

| Event | Fields | UI effect |
|-------|--------|-----------|
| `screenshot` | `data` (base64 JPEG) | Update the preview image |
| `status` | `text`: `running`/`idle` | Toggle Stop button + spinner |
| `thought` | `text` | Muted "thinking" step line |
| `action` | `text`, `detail` | Muted "action" step line |
| `answer` | `text` | Prominent reply bubble |
| `note` | `text` | Centered system notice (Stopped/busy) |
| `error` | `text` | Red error bubble |

---

## 3. End-to-end trace of one message

Example: the user types **"check top stories on bbc"** and presses Enter.

### Phase A — Frontend sends it

1. **`ChatPanel.submit()`** — reads the textarea, calls `onSend(text)` (which is
   the hook's `send`), then clears the box.
   *(Enter triggers this via `onKeyDown`; Shift+Enter inserts a newline instead.)*
2. **`useAgentSocket.send(text)`** — optimistically appends
   `{role:"user", text}` to `messages` (so your bubble shows instantly), then
   calls `rawSend({message: text})`.
3. **`useAgentSocket.rawSend(obj)`** — if the socket is `OPEN`, `ws.send(JSON…)`;
   otherwise it pushes to `queueRef` to flush on reconnect (send-buffering).
   → bytes travel over the WebSocket to the backend.

### Phase B — Backend decides chat vs. browse

4. **`main.ws(sock)`** — its `while True` loop does `await sock.receive_json()`
   and gets `{"message": "check top stories on bbc"}`. It's not a `stop`, so it
   extracts `text` and calls `route(text)`.
5. **`router.route(text)`** — first runs **`router._looks_like_browse(text)`**, a
   deterministic check for a URL or high-signal words ("top stor", "bbc" domain…).
   Here it matches → returns `{"mode": "browse", "reply": ""}` **without calling
   the model**. (For "hi" it would fall through to a quick llama3 classification.)
6. **`main.ws`** — sees `mode == "browse"`, confirms no run is already active,
   then `run = asyncio.create_task(do_browse(text))`. The receive loop keeps
   running, so `stop` and chat still work while the task executes.

### Phase C — The browse task starts

7. **`main.do_browse(task)`** (inner coroutine of `ws`):
   - `sock.send_json({"type":"status","text":"running"})` → UI shows the spinner.
   - `browser = await get_browser()` — **`main.get_browser()`** lazily constructs
     one shared `BrowserSession` and calls **`BrowserSession.start()`** on first
     use (launches headless Chromium, opens a page, attaches a CDP session).
   - `await browser.start_screencast(on_frame)` — begins the live video stream
     (see §5). `on_frame` forwards each frame as a `screenshot` event.
   - `await run_agent(task, browser, sock.send_json)` — hands control to the
     agent loop, passing `sock.send_json` as the `emit` callback.

### Phase D — The agent loop (the heart)

8. **`agent.run_agent(task, browser, emit)`** seeds the conversation:
   ```python
   messages = [
     {"role":"system", "content": SYSTEM_PROMPT},
     {"role":"user",   "content": f"Task: {task}\n\n{await _observe(browser)}"},
   ]
   ```
   `_observe()` = `_format_state(browser.url(), await browser.elements())` — the
   initial page (URL + numbered elements) is seeded right into the first message.
   Then it loops up to `MAX_STEPS` (15). Each iteration:

   1. **Ask the model, with tools** — `message = await chat_tools(messages, TOOLS)`.
      - **`agent.TOOLS`** is the list of Ollama function schemas (one per action:
        `go_to_url`, `click`, `input_text`, `press_enter`, `scroll`,
        `extract_text`, `dismiss_dialog`).
      - **`llm.chat_tools(messages, tools)`** POSTs to Ollama `/api/chat` with the
        `tools` field, `stream:false`, `temperature:0.1`, `num_ctx:8192`, and
        returns the raw assistant message. Raises `LLMError` if Ollama is down.
   2. **Append** that assistant message to `messages`.
   3. **Finish check** — if the message has **no `tool_calls`**, the model is done:
      `emit({"type":"answer", "text": content})` and **return**.
      *(Guard: if the plain text actually looks like a fumbled tool call —
      `_looks_like_tool_json()` — nudge the model to use the real tools and
      continue instead of ending.)*
   4. If there is content alongside the call, `emit` it as a `thought`.
   5. **Run each tool call.** For every `tool_calls[i]`:
      - `name = function.name`, `args = _parse_args(function.arguments)`.
      - `result = await _execute(browser, name, args)`.
      - **`agent._execute(browser, name, args)`** maps the tool name to a method:
        | tool | calls |
        |------|-------|
        | `go_to_url` | `browser.go_to_url(url)` |
        | `click` | `browser.click(index)` |
        | `input_text` | `browser.input_text(index, text)` |
        | `press_enter` | `browser.press_enter()` |
        | `scroll` | `browser.scroll(direction)` |
        | `extract_text` | `browser.extract_text()` |
        | `dismiss_dialog` | `browser.dismiss_overlays()` |
      - `emit` an `action` event + a fresh `screenshot`
        (`browser.screenshot_b64()`), then append a **`role:"tool"`** message
        whose content is the result **plus the new page state** (`_observe`), so
        the model's next decision is informed.
   6. Loop back with the growing `messages` list.

   If the loop exhausts `MAX_STEPS`, it emits an `answer` saying it didn't finish.

### Phase E — Results stream back to the UI (continuously)

9. Every `emit(...)` in the loop is `sock.send_json(...)` → the event crosses the
   WebSocket → **`useAgentSocket.handleEvent(e)`**:
   - `screenshot` → `setScreenshot(e.data)` → **`PreviewWindow`** re-renders the
     `<img src="data:image/jpeg;base64,…">`.
   - `status` → `setRunning(...)` → **`ChatPanel`** swaps Send ⇄ Stop.
   - everything else (`thought`/`action`/`answer`/`note`/`error`) → appended to
     `messages` → **`ChatPanel`** renders it (steps compact, answers prominent).

### Phase F — The task ends

10. When `run_agent` returns (a `done` answer or step-limit), **`do_browse`**'s
    `finally` runs `browser.stop_screencast()` and
    `sock.send_json({"type":"status","text":"idle"})` → the UI drops the spinner
    and restores the Send button.

---

## 4. Sequence diagram (browse request)

```
User      ChatPanel        useAgentSocket        main.ws          router     agent.run_agent   browser        Ollama
 │  Enter    │                    │                  │               │              │              │              │
 │──────────►│ submit()           │                  │               │              │              │              │
 │           │──onSend(text)─────►│ send()           │               │              │              │              │
 │           │                    │─rawSend {message}►│ receive_json  │              │              │              │
 │           │                    │                  │─route(text)──►│ (guardrail)  │              │              │
 │           │                    │                  │◄─{browse}─────│              │              │              │
 │           │                    │                  │─create_task(do_browse)       │              │              │
 │           │                    │◄─status:running──│                              │              │              │
 │           │                    │                  │─start_screencast────────────────────────►│              │
 │           │                    │                  │─run_agent(task, browser, emit)──────────►│              │
 │           │                    │                  │              LOOP:            │              │              │
 │           │                    │                  │              │─chat_tools(messages, TOOLS)─────────────►│
 │           │                    │                  │              │◄──tool_calls:[click{index:8}]────────────│
 │           │                    │◄─action──────────│◄─emit────────│─_execute()──►browser.click(8)             │
 │           │                    │◄─screenshot──────│◄─emit────────│─append role:"tool" (result + new state)   │
 │           │  (frames stream)   │◄─screenshot──────│◄──on_frame (CDP screencast)─│              │              │
 │           │                    │                  │              … repeat …      │              │              │
 │           │                    │                  │              │◄──(no tool_calls) plain answer───────────│
 │           │                    │◄─answer──────────│◄─emit────────│              │              │              │
 │           │                    │◄─status:idle─────│ (finally)    │              │              │              │
```

---

## 5. Two subsystems worth calling out

### Clicking by index (no brittle selectors)
`COLLECT_JS` stamps each listed element with `data-agent-idx="i"`. The model sees
`[8] News`; it calls the `click` tool with `{"index": 8}`;
**`BrowserSession.click(8)`** does `page.locator('[data-agent-idx="8"]').click()`.
The number the model reasons about and the DOM attribute are identical — so the
model points at a number instead of inventing a CSS/XPath selector.

### Live preview via CDP screencast
**`BrowserSession.start_screencast(on_frame)`** issues the DevTools command
`Page.startScreencast` and registers a listener. Chromium then pushes a
`Page.screencastFrame` event on every visual change; the handler calls
`on_frame(data)` (→ a `screenshot` WS event) and acks with
`Page.screencastFrameAck`. That's why the page renders live in the panel with **no
OS window** — it's a JPEG stream, not a visible browser.

### Cookie/consent handling
**`BrowserSession.dismiss_overlays()`** scans every frame for a button whose
accessible name exactly matches an allowlist (`CONSENT_LABELS`: "Accept all", "I
agree", …) and clicks it — never "Reject"/"I do not agree". It's called
automatically inside `go_to_url()` (after load) and as a retry inside `click()`
if a click is intercepted, plus exposed to the model as the `dismiss_dialog`
action.

---

## 6. Concurrency & lifecycle

- **One shared browser.** `main.get_browser()` guards a module-level
  `BrowserSession` with an `asyncio.Lock`; it starts headless Chromium lazily on
  the first browse, so plain chat never opens a browser.
- **Runs are cancellable tasks.** A browse is `asyncio.create_task(do_browse)`.
  `{"stop": true}` calls `run.cancel()`, which raises `CancelledError` inside the
  loop; `do_browse` catches it, emits a `note`, and cleans up in `finally`.
- **The socket stays responsive.** Because the run is a background task, the
  `ws()` receive loop continues handling `stop` and chat messages mid-run.
- **One run at a time.** A second browse while one is active returns a `note`
  ("press Stop to interrupt") — the single shared page can't run two loops.
- **Shutdown.** FastAPI's `lifespan` closes the browser (`BrowserSession.stop()`)
  when the server stops.
- **Reconnect.** On the client, `ws.onclose` retries `connect()` after 1.5s, and
  queued messages flush on `ws.onopen`.

---

## 7. Function reference (quick index)

**Frontend**
- `App()` — calls `useAgentSocket()`, renders `<ChatPanel>` + `<PreviewWindow>`.
- `useAgentSocket()` → `{messages, screenshot, connected, running, send, stop}`.
  - `connect()` — opens the socket, wires `onopen/onclose/onmessage`.
  - `handleEvent(e)` — routes an inbound event to `setScreenshot`/`setRunning`/`setMessages`.
  - `rawSend(obj)` — send now or buffer; `send(text)` — add bubble + send `{message}`; `stop()` — send `{stop}`.
- `ChatPanel({messages,onSend,onStop,connected,running})` — `submit()`, `onKeyDown(e)`, `resize()`, inner `Message({m})`.
- `PreviewWindow({screenshot,running})` — renders the JPEG frame or a placeholder.

**Backend**
- `main.get_browser()` — lazy singleton `BrowserSession`.
- `main.lifespan(app)` — closes the browser on shutdown.
- `main.ws(sock)` — the WebSocket loop; inner `do_browse(task)` orchestrates a run.
- `router.route(message)` → `{mode, reply}`; `router._looks_like_browse(message)` — deterministic guardrail.
- `agent.run_agent(task, browser, emit)` — the tool-calling loop; `agent.TOOLS` — the function schemas; `agent._format_state(url, elements)`; `agent._observe(browser)`; `agent._execute(browser, name, args)`; `agent._parse_args(raw)`; `agent._looks_like_tool_json(content)`.
- `llm.chat_tools(messages, tools)` — Ollama call with `tools` → assistant message with `tool_calls` (raises `LLMError`).
- `llm.chat_json(messages)` — Ollama call in JSON mode → parsed dict; used by the router.
- `browser.BrowserSession`:
  - lifecycle: `start()`, `stop()`
  - actions: `go_to_url(url)`, `click(index)`, `input_text(index,text)`, `press_enter()`, `scroll(direction)`, `extract_text()`, `dismiss_overlays()`
  - observation: `elements()`, `screenshot_b64()`, `url()`
  - live view: `start_screencast(on_frame)`, `stop_screencast()`

---

## 8. Talking points for the meeting

1. **Native tool calling.** We pass Ollama typed function schemas (`agent.TOOLS`)
   via `chat_tools`; llama3.1 returns a structured `tool_calls` array, and
   `_execute` dispatches on the tool name. The model finishes by answering with
   *no* tool call.
2. **The model is "blind" to the image.** It acts on the *text* element list
   (`_format_state`), fed back inside each `role:"tool"` result — not the
   screenshot. The screenshot is purely for the human.
3. **The ReAct loop** — ask → call → execute → feed the result back — is what
   makes it an agent; state accumulates in the `messages` array.
4. **Everything is one WebSocket** with a tiny typed event protocol — easy to
   reason about and extend.
5. **Guardrails contain a small local model's fallibility:** deterministic router,
   typed tool schemas, a nudge when it writes a call as text
   (`_looks_like_tool_json`), index-based clicking, auto-cookie handling, a
   15-step cap, and Stop.
