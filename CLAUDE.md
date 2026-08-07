# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A local browser agent: a local LLM (llama3.1 via Ollama) drives a headless
Chromium through Playwright to accomplish web tasks typed into a chat panel.
Python/FastAPI backend + React/Vite frontend, connected by a single WebSocket.

`README.md` covers setup/usage; `docs/how-it-works.md` and
`docs/code-walkthrough.md` cover the design in depth and are worth reading
before non-trivial changes. `BACKLOG.md` lists the known gaps with enough detail
to pick any of them up cold — check it before proposing new work; *Working the
backlog* below covers how its entries are written and worked. It is a local
working note, gitignored on purpose: it is a plan rather than a description of
the code, and versioning it would put every reordering into the history.

## Commands

Both suites run against a project-local venv (`backend/.venv`) — always invoke
Python through it, never a bare `python`/`pytest`.

```bash
./dev.sh                       # backend :8008 + frontend :5173 together
cd backend && ./run.sh         # backend only (uvicorn --reload)
cd frontend && npm run dev     # frontend only

./test.sh                      # both suites concurrently
cd backend  && ./.venv/bin/python -m pytest -q
cd backend  && ./.venv/bin/python -m pytest tests/test_agent.py -k dismiss -q   # single test
cd frontend && npm test
cd frontend && npx vitest run src/components/ChatPanel.test.jsx                 # single test file

./lint.sh                      # ruff + eslint; --fix applies what it can
cd backend  && ./.venv/bin/ruff check . && ./.venv/bin/ruff format .
cd frontend && npm run lint
```

Dependency changes: backend deps are pinned in `backend/pyproject.toml`
(the single source of truth, including pytest config); reinstall with
`./.venv/bin/pip install -e ".[dev]"`. Playwright's browser binary is separate:
`./.venv/bin/python -m playwright install chromium`.

Linting is `ruff` (check + format, configured in `pyproject.toml`) and `eslint`
(flat config in `frontend/eslint.config.js`). `ruff format` is authoritative for
Python layout — hand-wrapping will just be undone.

`RUF001`-`RUF003` (ambiguous unicode) are off on purpose: the router's few-shot
examples and the routing eval are written in the scripts and punctuation they
would really arrive in, and flagging those characters is pure noise.

## Working the backlog

Entries are worked **one at a time, one branch each** — that independence is what
makes any of them reviewable, or revertable, on its own.

**Writing one.** Every task must be a *vertical slice*: shippable end to end on
its own branch, with nothing else needing to land alongside it. Touching
`browser.py`, `agent.py`, the WebSocket contract and the preview pane in one task
is fine — that is the full depth of a single behaviour. Touching one layer across
many behaviours ("add types to the backend", "wire up error handling everywhere",
"refactor all the tools") is horizontal, and must be split before it goes in the
list. Three questions settle it: can it merge alone without breaking `main`, can
a reviewer tell from the diff whether it worked, and does finishing it change
something observable? Any *no* and it is not a task yet — restate it as the
smallest change that passes all three and file the rest separately. Prefer
several thin, boring entries over one that has to be coordinated. A pure
restructuring answers the third question at the interface instead; see *Code
shape*.

**Working one.**

- **Branch per task.** Cut a fresh branch from an up-to-date `main` before
  touching anything: `fix/<n>-<slug>` for a backlog number (`fix/2-ws-origin`),
  `feature/<slug>` for anything else. Never start a second task on a branch that
  already carries one, and never work directly on `main`.
- **No passengers.** Do not fold in a neighbouring entry, an unrelated cleanup or
  a drive-by rename, however adjacent. If the work uncovers a second problem,
  file it as a new entry and leave it.
- **Confirm the entry is still real** before writing code — read the cited files
  and check the described behaviour still holds. The backlog is maintained by
  hand and can drift ahead of or behind the code.
- **Finish the entry, not the easy half.** Done means the fix, its tests, and the
  doc updates it implies (`CLAUDE.md`, `docs/`, `main.py`'s protocol docstring)
  are all in the branch, and the `BACKLOG.md` entry is deleted as the branch is
  finished. Since the backlog is untracked it survives the branch switch on its
  own; a fix that leaves its entry standing reads as unfixed next session.
- **Green before done.** `./test.sh` and `./lint.sh` both pass, and anything with
  a visible surface is verified in the running app rather than reasoned about.
- **Commit at task granularity.** One coherent commit (or a short ordered series)
  per branch, saying what the entry was and why the fix takes the shape it does.
- **Stop at the boundary.** If a task genuinely cannot be done without another
  landing first, say so and stop rather than quietly widening the branch.
  Pushing, merging or opening a PR happens only when asked.

## Architecture

**Everything flows over one WebSocket** (`/ws` in `backend/app/main.py`). The
client sends `{"message"}`, `{"stop"}`, `{"control"}`, `{"input"}` or
`{"navigate"}`; the server streams typed events back: `hello`, `screenshot`,
`page`, `token`, `token_reset`, `thought`, `action`, `answer`, `note`, `error`,
`status`. That event union is the contract
between `main.py`'s docstring, `agent.py`'s `emit(...)` calls, and
`useAgentSocket.js`'s `handleEvent` — adding an event type means touching all
three. `main.py`'s module docstring is the authoritative spec; keep it current.

Every send goes through the local `send()` helper in `ws()`, which swallows
send-after-close. A client can vanish mid-run, and a raw `sock.send_json` would
then take the whole turn down with an unhandled `RuntimeError`.

**Streaming.** `chat_tools`/`chat_text` always stream (`stream: true`) and
reassemble Ollama's NDJSON into the same single message dict a non-streaming call
would return, so callers are unaware the response arrived in pieces. The optional
`on_token` callback is what feeds `token` events to the UI. Ollama delivers each
tool call whole in one chunk with `arguments` already a dict — there are no
partial argument fragments to stitch. Only `chat_json` (the router's classifier)
stays non-streaming, since half a JSON object is useless.

The client treats `thought`/`action`/`answer`/`note`/`error` as the authoritative
version of whatever was streaming and clears its token buffer when one lands, so
nothing renders twice. `token_reset` covers the one case with no final event: the
`_looks_like_tool_json` nudge, where the streamed text turns out to be a botched
tool call rather than an answer.

**Routing.** Every message hits `router.py` first, where a small classifier model
returns `chat` or `browse`. Only `browse` starts the agent, so the browser stays
closed during plain conversation. Ambiguity resolves to `browse` by design — the
failure that matters is the model inventing an answer it should have looked up.
Classification and answering are deliberately separate calls: `route()` returns
only a mode, and `chat_reply()` then streams the actual text.

This replaced a keyword/domain regex that was wrong in both directions — it only
saw English, and matched substrings, so `"how are you today?"` hit `"today"` and
forced a browse with no model call at all. **Do not reintroduce a keyword
short-circuit**; whether a message needs the web is a judgement about meaning.

Two things keep the classifier honest, and both need a live Ollama to evaluate:

- `_EXAMPLES` in `router.py` are few-shot turns, several chosen specifically to
  pin down where the old regex failed. Prompt edits should be measured, not
  eyeballed: `./.venv/bin/python tools/routing_eval.py` scores a labelled
  multilingual set and prints every disagreement.
- `CHAT_PROMPT` is the backstop for when routing *does* miss. Its rules are
  forceful for a reason: a softer "don't fabricate" produced invented Bitcoin
  prices and narrated browsing sessions that never happened. Keep it strict.

**The agent loop** (`agent.py:run_agent`) uses Ollama's *native* tool calling,
not hand-parsed JSON. It appends the raw assistant message to `messages`, runs
each `tool_calls` entry through the `TOOLS` table, and feeds back a `tool`
message containing the action result **plus a freshly observed page state**. It
terminates when the model returns no tool calls, or at `MAX_STEPS` (15).

Two guardrails exist because small local models misbehave, and both should be
preserved: `_looks_like_tool_json` catches a tool call written as text in the
message body and nudges the model instead of accepting it as the final answer;
`_parse_args` tolerates arguments arriving as a JSON string rather than a dict.

**Index-based DOM addressing** is the core browser trick (`browser.py`). Before
each model turn, `COLLECT_JS` walks visible interactive elements, stamps each
with `data-agent-idx`, and returns a numbered listing that `_format_state`
renders into the prompt (capped at 60 elements). The model then acts by *index*,
and `click`/`input_text` locate via `[data-agent-idx="N"]` — the model never
guesses CSS selectors. Any change to element collection must keep the tagging
and the listing in sync.

**Consent dialogs** are auto-accepted after every navigation and retried once on
a failed click. `CONSENT_LABELS` are matched by accessible name, **exactly and
case-insensitively**, precisely so "I do not agree" / "Reject" are never clicked
— do not loosen this to substring matching.

Those labels are English-only, which is why the context sets `locale`
(`BROWSER_LOCALE`, default `en-US`). Without it Playwright sends no
`Accept-Language` header at all, sites geolocate by IP, and a consent wall can
arrive in a language the labels cannot match — leaving it stuck on screen. It
also made the agent answer in the *page's* language rather than the user's.

**Human takeover.** The preview pane is interactive: `{"control": "user"}` clears
an `asyncio.Event` that `run_agent` awaits at the top of each loop iteration, so
the agent pauses at the next **step boundary** (a tool call already in flight
finishes first). `{"input": ...}` events then dispatch real mouse/keyboard input
at viewport coordinates via `browser.user_*`. On resume the loop appends a `user`
message telling the model the page may have moved under it and re-observes —
without that it would act on a stale transcript. Input is refused server-side
unless the gate is actually cleared, so a stray click can't land mid-step.

The `user_*` methods use Playwright's `page.mouse`/`page.keyboard` rather than
the raw CDP `Input` domain, which would mean hand-rolling virtual key-code
tables. Playwright's key names match the browser's `event.key`, so the frontend
maps single characters to `type` and everything else to a named `key` press.

**Browser lifecycle:** one shared `BrowserSession`, created lazily on first use
and closed in the FastAPI lifespan. Because it is shared, only one turn executes
at a time — a second task while one is in flight gets a `note` telling the user
to press Stop. Turns run as asyncio tasks so the receive loop stays free to
service Stop and preview input while the model is thinking; Stop cancels the task
and works while paused too.

**Screencast:** frames fan out to a *set* of `FrameSink` subscribers, and the CDP
stream runs only while at least one is attached. Three constraints are easy to
break and worth knowing:

- Frames must be acked immediately and unconditionally — Chromium throttles then
  stops the screencast if acks dry up, so acking cannot depend on any client
  keeping up.
- Each sink keeps at most one pending frame (newest wins). Interactive use
  repaints on every mouse move, and an unbounded queue leaves the preview
  trailing the cursor by seconds.
- CDP only emits on *visual change*, so `add_frame_sink` primes each new
  subscriber with a `screenshot_b64()` snapshot. Without it, a client attaching
  to an idle page sees a blank pane until something happens to move.

The screencast is per-connection and deliberately outlives any single run —
between tasks is exactly when a user wants to click around and log in somewhere.

## Testing approach

Backend tests fake both the LLM and the browser (`FakeBrowser` records calls; a
scripted `chat_tools` returns canned assistant messages), so `pytest` runs with
no Ollama and no Chromium. Follow that pattern — no test should need either.
`asyncio_mode = "auto"` is set, so async tests need no marker. Frontend tests
use vitest + Testing Library under jsdom.

## Configuration

Env vars (`backend/app/config.py`, read once at import): `MODEL`, `OLLAMA_URL`,
`MAX_STEPS`, plus `BACKEND_PORT` for the scripts. The frontend defaults to
`ws://localhost:8008/ws`, overridable with `VITE_WS_URL`. Changing the port
means setting both `BACKEND_PORT` and `VITE_WS_URL`.

## Code shape

**Modules must be deep.** A module earns its place by hiding much more than it
exposes: a small interface over substantial functionality, judged by that ratio
and not by line count. The two worth copying are both described above — `llm.py`,
whose three functions hide streaming, NDJSON reassembly, timeouts and error
shaping, and `add_frame_sink`, whose two methods hide the entire screencast.

The failure to avoid is the shallow layer, which costs a call and an abstraction
and buys nothing:

- **No pass-through methods.** A method whose body is one call to the layer below,
  with the same arguments, is not an abstraction — it is a rename.
- **No pass-through layers.** If a concept has to be spelled out again at each
  level (a name here, an if-branch there, a one-line method below), the layers
  are shallow and the concept belongs in *one* of them.
- **One capability, one place.** Adding a tool, an event type or an action should
  mean editing one table or one module. Where it means editing three, that is the
  design telling you the interface is too wide.
- **Grow an existing module before adding one.** A new file is justified when a
  concept has an interface genuinely smaller than what it hides — not to keep
  files short. Splitting a deep module into two shallow ones is a net loss.

Design new work this way from the start: decide what the caller should *not* have
to know, and put the interface there. Reshaping existing code toward depth is
still one task on one branch, and since behaviour by definition does not change,
its "done when" is stated at the interface — *adding a tool touches one place* —
plus a green suite.

The backend splits by concern, one module each for the socket, the loop, the
browser, routing and the model client. Two pieces are worth knowing:

- `Connection` in `main.py` holds everything scoped to one client — the
  screencast subscription, the in-flight turn, and who currently holds the
  browser. The browser itself is process-wide; everything else is per-socket.
- `TOOLS` in `agent.py` is the whole of the agent's action surface. Each row is
  a `Tool(name, schema, run)` built by `_tool()`, holding both what the model is
  told and what running it does, so **adding a tool means adding one row and
  nothing else**: `SCHEMAS` (what Ollama is sent) and the name lookup are both
  derived from the table, and each row's `run` owns the coercion its own schema
  implies. A name in no row comes back to the model as `Unknown tool 'x'.`
  rather than raising, but two rows *sharing* a name raise at import — that one
  fails silently otherwise, leaving the earlier row dead while Ollama is still
  told the tool exists twice. `test_every_declared_tool_reaches_the_browser` is
  the other guard: it walks `TOOLS` and drives each row through the loop with
  arguments built from that row's own schema, asserting exactly one browser
  call, so a schema with no handler — or one wired to the wrong method — fails
  there instead of at the model.

On the frontend, pure logic lives in `src/lib/` so it can be tested without
rendering: `pageInput.js` (event → protocol mapping, coordinate scaling) and
`transcript.jsx` (prose rendering, folding narration into trace blocks). The
components are thin by comparison, and `usePageInput` carries the effects that
turn a DOM surface into a remote browser.

## Frontend

Two rules hold the interface together, and both are load-bearing rather than
decorative — breaking either makes the UI lie about what the machine is doing:

- **Colour reports state, nothing else.** The console is monochrome; the only
  hues are amber (agent acting), coral (you hold the browser) and mint (settled
  / connected). Adding a fourth accent, or using an existing one for emphasis,
  breaks the reading.
- **Mono is the machine speaking, sans is language.** Trace steps, URLs, element
  indices and metrics are monospaced because they are machine output; your
  messages and the agent's answers are sans because they are prose.

Fonts are bundled via `@fontsource` rather than fetched from a CDN: a product
whose premise is running entirely on your machine should not need the network to
render.

**The agent view** (`AgentView` in `PreviewWindow.jsx`) draws the numbered boxes
the model reasons over on top of the live page. It must show **exactly** the
elements the model was given and no more — `observe()` and `_format_state()` both
cut at `MODEL_ELEMENT_LIMIT` (60) for precisely this reason. Drawing all of them
(an earlier version sent 200) turns the feature into a picture of a page the
agent cannot actually act on. The count of what was left out is surfaced in the
status strip, because elements past the limit are a real and otherwise invisible
cause of failure: Hacker News' front page has ~228, so the agent can only reach
the first seven stories.

Layout uses `min-height: 0` on the grid rows and their children. Without it a
grid/flex child defaults to `min-height: auto`, refuses to shrink, and pushes the
composer off the bottom of the screen.

Verify visually, not by reasoning about CSS — several bugs here (image
overflowing the stage, 1.27:1 contrast on the step durations, focus rings killed
by `outline: none` on the inputs) were invisible in the code and obvious in a
screenshot or a measurement.
