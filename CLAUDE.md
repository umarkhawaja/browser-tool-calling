# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## What this is

A local browser agent: a local LLM (llama3.1 via Ollama) drives a headless
Chromium through Playwright to accomplish web tasks typed into a chat panel.
Python/FastAPI backend + React/Vite frontend, connected by a single WebSocket.

`README.md` covers setup/usage; `docs/how-it-works.md` covers the design in depth
— read it before non-trivial changes. It is the only such doc on purpose: a
second one, `docs/code-walkthrough.md`, traced the same subsystems call by call
and had drifted from the signatures it named, which is the failure mode of a doc
precise enough to go stale. Depth belongs in `how-it-works.md`, the wire protocol
in `main.py`'s docstring, and the rules here. **GitHub issues are the backlog**,
each filed with enough
detail to pick up cold and labelled `p1`/`p2`/`p3` by how soon it will hurt;
check them before proposing new work (`gh issue list --label p1`). The old
`BACKLOG.md` is gone: a tracker that closes an item when its PR merges beats a
file someone has to remember to prune.

## Commands

Both suites run against a project-local venv (`backend/.venv`) — never a bare
`python`/`pytest`.

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

Backend deps are pinned in `backend/pyproject.toml`, the single source of truth
for pytest and ruff config too; reinstall with
`./.venv/bin/pip install -e ".[dev]"`. Playwright's browser binary is separate:
`./.venv/bin/python -m playwright install chromium`.

`ruff format` is authoritative for Python layout — hand-wrapping will just be
undone; eslint's flat config is `frontend/eslint.config.js`. `RUF001`-`RUF003`
(ambiguous unicode) are off on purpose: the router's few-shot examples and the
routing eval are written in the scripts and punctuation they would really arrive
in, and flagging those characters is pure noise.

## Working the backlog

Issues are worked **one at a time, one branch each** — that independence is what
makes any of them reviewable, or revertable, on its own.

**Writing one.** Every task is a *vertical slice*: shippable end to end on its
own branch, with nothing else needing to land alongside it. All of `browser.py`,
`agent.py`, the WebSocket contract and the preview pane in one task is fine —
that is the full depth of a single behaviour. One layer across many behaviours
("add types to the backend", "refactor all the tools") is horizontal, and must be
split before it is filed. Three questions settle it: can it merge alone without
breaking `main`, can a reviewer tell from the diff whether it worked, and does
finishing it change something observable? Any *no* and it is not a task yet —
restate it as the smallest change that passes all three and file the rest
separately, or label it `not-a-task` and say what would make it one. Prefer
several thin, boring issues over one that has to be coordinated.

Every issue carries a **Done when:** line stating the observable result. Without
one it is not ready to work, because that line is what tells you the branch is
finished rather than merely plausible. A pure restructuring states it at the
interface instead (*adding a tool touches one place*), plus a green suite.

**Working one.**

- **Branch per issue.** Cut a fresh branch from an up-to-date `main` before
  touching anything: `fix/<issue>-<slug>` (`fix/3-ws-origin` for issue #3),
  `feature/<slug>` for anything else. Never work directly on `main`, and never
  start a second task on a branch that already carries one.
- **No passengers.** No neighbouring issue, unrelated cleanup or drive-by rename,
  however adjacent. A second problem found along the way is a new issue, left
  alone.
- **Confirm the issue is still real** before writing code — read the cited files
  and check the described behaviour still holds. Issues are written by hand and
  drift; say so in a comment when one has.
- **Finish the issue, not the easy half.** The fix, its tests and the doc updates
  it implies (`CLAUDE.md`, `docs/`, `main.py`'s protocol docstring) are all in the
  branch, and the PR says `Closes #<issue>` so merging closes it.
- **Green before done.** `./test.sh` and `./lint.sh` both pass, and anything with
  a visible surface is verified in the running app rather than reasoned about.
  `.github/workflows/ci.yml` runs the first half on a clean runner — those two
  scripts *unchanged*, plus `npm run build` — and reports as `CI / green` on every
  PR and push to `main`. It is **advisory**: on this private repo's free plan
  branch protection and rulesets both answer `403`, so only reading the check
  stops a red merge. And no runner looks at the page, so green is the cheap half
  of the rule, not the rule.
- **Commit at task granularity.** One coherent commit (or a short ordered series)
  per branch, saying what the issue was and why the fix takes the shape it does.
- **Stop at the boundary.** If a task genuinely needs another to land first, say
  so and stop rather than quietly widening the branch. Pushing, merging or
  opening a PR happens only when asked.

## Test-driven development

**Strict TDD is the default, not an aspiration.** The order is not negotiable:

1. **Write the failing test first.** It encodes the *observable* result — the
   issue's **Done when:** line is usually the test.
2. **Watch it fail, for the right reason.** Read the assertion message and check
   it describes the actual defect, not an import error or a typo. **A test you
   did not watch fail is not a guard:** when a change fixes something the suite
   missed,
   break the fix deliberately, confirm the new test goes red, restore it, and say
   so in the PR. "Confirmed failing before the fix" is a claim a reviewer can
   trust, and it catches the tautological assertion that passes against any
   implementation.
3. **Write the least code that passes it.** No speculative generality, no
   adjacent improvements riding along.
4. **Refactor with the test green**, running it as you go.

**Exempt — a closed list, not a judgement call:**

- documentation and comments;
- configuration, dependency pins and scripts with no logic to assert on;
- pure restructurings already covered by the suite, which must be green before
  and after;
- throwaway spikes, which are deleted rather than merged.

Everything else is in scope, **including "obvious" one-liners** — exactly where a
skipped test hides an inverted condition. Widening this list is a change to
CLAUDE.md, not a mid-task decision.

Test at the level the defect lives: a bug in tool dispatch belongs in
`test_agent.py` driving `run_agent`, not in a unit test of a private helper that
will be renamed next month. Backend tests fake both the LLM and the browser
(`FakeBrowser` records calls; a scripted `chat_tools` returns canned assistant
messages), so no test needs Ollama or Chromium — follow that.
`asyncio_mode = "auto"` is set, so async tests need no marker. Frontend tests use
vitest + Testing Library under jsdom.

**What the fakes cannot tell you** lives in `tools/browse_eval.py`, the browse
counterpart to `routing_eval.py`: whole tasks driven through a real headless
Chromium and a live Ollama against a fixture site served on loopback, scored
pass/fail. Fakes agree with the code by construction, so native tool calling,
`data-agent-idx` clicking, consent dismissal and the final answer have only ever
been exercised separately — that is the seam it covers.

```bash
cd backend && ./.venv/bin/python tools/browse_eval.py         # all cases, ~20s
cd backend && ./.venv/bin/python tools/browse_eval.py stock   # one by name
```

It stays opt-in, out of `pytest`, for the reason above. Two of its halves are
*not* live, and are in the suite (`tests/test_browse_eval.py`, which imports it —
hence `pythonpath = ["."]`): grading a finished run's events, and the fixture site
saying what the cases ask for. That second one matters more than it looks. A typo
in the fixture HTML is indistinguishable from a model failure when you are reading
a live run, so each case's expected fact is asserted to be *on* the site, its
decoy too, and neither on the landing page — a task answerable without a click
measures nothing. Add a case by adding one row to `CASES`, and put its fact behind
the interaction the case exists to exercise.

## Architecture

**Everything flows over one WebSocket** (`/ws` in `backend/app/main.py`). The
client sends `{"message"}`, `{"stop"}`, `{"control"}`, `{"input"}` or
`{"navigate"}`; the server streams back `hello`, `screenshot`, `page`, `token`,
`token_reset`, `thought`, `action`, `answer`, `note`, `error`, `status`. That
union is the contract between `main.py`'s docstring — the authoritative spec,
keep it current — `agent.py`'s `emit(...)` calls and `useAgentSocket.js`'s
`handleEvent`; a new event type touches all three. Every send goes through
`send()` in `ws()`, which swallows send-after-close: a client can vanish mid-run,
and a raw `sock.send_json` would take the whole turn down with an unhandled
`RuntimeError`.

**Streaming.** `chat_tools`/`chat_text` always stream (`stream: true`) and
reassemble Ollama's
NDJSON into the single message dict a non-streaming call would return, so callers
never see the pieces; the optional `on_token` callback feeds `token` events to the
UI. Ollama delivers each tool call whole in one chunk with `arguments` already a
dict — no partial fragments to stitch. Only `chat_json` (the router's classifier)
stays non-streaming, since half a JSON object is useless.

The client treats `thought`/`action`/`answer`/`note`/`error` as the authoritative
version of whatever was streaming and clears its token buffer when one lands, so
nothing renders twice. `token_reset` covers the one case with no final event: the
`_looks_like_tool_json` nudge, where the streamed text turns out to be a botched
tool call rather than an answer.

**Routing.** Every message hits `router.py` first, where a small classifier model
returns `chat` or `browse`; only `browse` starts the agent, so the browser stays
closed during plain conversation. Ambiguity resolves to `browse` by design — the
failure that matters is the model inventing an answer it should have looked up.
Classification and answering are deliberately separate calls: `route()` returns a
mode, `chat_reply()` then streams the text.

This replaced a keyword/domain regex wrong in both directions — English-only, and
matching substrings, so `"how are you today?"` hit `"today"` and forced a browse
with no model call at all. **Do not reintroduce a keyword short-circuit**; whether
a message needs the web is a judgement about meaning.

Two things keep the classifier honest, and both need a live Ollama to evaluate:

- `_EXAMPLES` in `router.py` are few-shot turns, several chosen to pin down where
  the old regex failed. Measure prompt edits rather than eyeballing them:
  `./.venv/bin/python tools/routing_eval.py` scores a labelled multilingual set
  and prints every disagreement.
- `CHAT_PROMPT` is the backstop for when routing *does* miss. Keep its rules
  strict: a softer "don't fabricate" produced invented Bitcoin prices and narrated
  browsing sessions that never happened.

**The agent loop** (`agent.py:run_agent`) uses Ollama's *native* tool calling, not
hand-parsed JSON: it appends the raw assistant message to `messages`, runs each
`tool_calls` entry through the `TOOLS` table, and feeds back a `tool` message with
the action result **plus a freshly observed page state**. It terminates when the
model returns no tool calls, or at `MAX_STEPS` (15). Two guardrails exist because
small local models misbehave, and both should be preserved: `_looks_like_tool_json`
catches a tool call written as text in the message body and nudges the model
instead of accepting it as the final answer; `_parse_args` tolerates arguments
arriving as a JSON string rather than a dict.

**The transcript is trimmed on the way out, never in place.** `messages` grows by
a page listing plus up to 4000 characters of `extract_text` every step, and
outgrows `num_ctx` well before `MAX_STEPS`. Ollama enforces that window by
dropping from the *front* and reporting nothing, so the system prompt and the task
are the first casualties and the run continues, competently, having forgotten what
it was asked — a failure that leaves no trace to read. `llm.fit_to_context`
therefore decides what to lose: the opening is pinned, the newest turn is kept
whole (its listing is the one about to be acted on), **every older turn is reduced
to one line first**, and only the budget left over buys recent turns back to full
text (up to `_KEEP_VERBATIM`). That order was measured, not guessed: keeping the
last three turns whole first — the obvious reading — leaves no room for a single
summary on a page as heavy as Hacker News, so a 15-step run reaches the end with
no record of steps 1–12 and repeats them. A line costs a twentieth of a turn; it
is bought first.

It trims in **turns** — an assistant message plus the tool results answering it —
because Ollama rejects a tool result it cannot trace back to a call, and a trimmer
working message by message severs a pair only at some budgets; that is why
`test_trimming_drops_whole_turns_so_no_tool_result_is_orphaned` sweeps the budget
to walk the cut across every boundary. The budget sits in `llm.py` beside
`NUM_CTX`, in characters rather than tokens deliberately: a pessimistic ratio
costs a little history, where a tokenizer would cost a dependency and a round trip
per step. `run_agent` keeps the full list and passes only the fitted view,
recomputed each step, so the verbatim window slides.

**Index-based DOM addressing** is the core browser trick (`browser.py`). Before
each model turn, `COLLECT_JS` walks visible interactive elements, stamps each with
`data-agent-idx`, and returns a numbered listing that `_format_state` renders into
the prompt (capped at `MODEL_ELEMENT_LIMIT`, 60). The model acts by *index* and
`click`/`input_text` locate via `[data-agent-idx="N"]`, so it never guesses CSS
selectors; any change to element collection must keep the tagging and the listing
in sync.

**A number belongs to an element, not to a place in the listing.** An element
already carrying `data-agent-idx` keeps it; only new ones draw from a counter that
never goes back. Old listings sit in the transcript for the whole run, and
numbering each one from zero meant an index the model read three steps ago still
resolved — to whatever happened to be eighth *now*. That is a wrong click that
reads as a correct one in the trace, which is worse than an error.

Both halves are load-bearing. Re-reading an unmoved page leaves the numbers alone,
so the model can act on the listing it was just given — an earlier version
renumbered on every reading, and llama3.1 responded by inventing small indices and
burning its whole step budget on refusals. And `BrowserSession._element`, the
single way in for both `click` and `input_text`, raises `StaleIndex` for any index
the latest reading did not report, including one whose neighbours are still live —
a removed element keeps its attribute and would otherwise still match a selector.
The refusal reaches the model as the tool result, next to a fresh listing, so it
is also the correction.

That is why the agent view's "just clicked" mark rides on the DOM node
(`data-agent-clicked`, set before the click and reported by `COLLECT_JS` as
`clicked`) rather than on a number: the number is gone by the time the overlay
draws. A click that navigates leaves nothing marked, which is the truth — and so
does a new task, since `restart_numbering` takes that mark off the page along with
the numbers rather than leaving the last task's click highlighted.

**Consent dialogs** are auto-accepted after every navigation and retried once on a
failed click. `CONSENT_LABELS` are matched by accessible name, **exactly and
case-insensitively**, precisely so "I do not agree" / "Reject" are never clicked —
do not loosen this to substring matching. Those labels are English-only, which is
why the context sets `locale` (`BROWSER_LOCALE`, default `en-US`): without it
Playwright sends no `Accept-Language` header at all, sites geolocate by IP, and a
consent wall can arrive in a language the labels cannot match — leaving it stuck
on screen, and the agent answering in the *page's* language rather than the user's.

**Human takeover.** The preview pane is interactive: `{"control": "user"}` clears
an `asyncio.Event` that `run_agent` awaits at the top of each loop iteration, so
the agent pauses at the next **step boundary** (a tool call already in flight
finishes first). `{"input": ...}` events then dispatch real mouse/keyboard input at
viewport coordinates via `browser.user_*`, refused server-side unless the gate is
actually cleared so a stray click can't land mid-step. On resume the loop appends
a `user` message telling the model the page may have moved under it and
re-observes — without that it would act on a stale transcript. The `user_*` methods
use Playwright's `page.mouse`/`page.keyboard` rather than the raw CDP `Input`
domain, which would mean hand-rolling virtual key-code tables; Playwright's key
names match the browser's `event.key`, so the frontend maps single characters to
`type` and everything else to a named `key` press.

**Browser lifecycle:** one shared `BrowserSession`, created lazily on first use
and closed in the FastAPI lifespan. Because it is shared, only one turn executes
at a time — a second task while one is in flight gets a `note` telling the user to
press Stop. Turns run as asyncio tasks so the receive loop stays free to service
Stop and preview input while the model is thinking; Stop cancels the task, and
works while paused too.

**Screencast:** frames fan out to a *set* of `FrameSink` subscribers, and the CDP
stream runs only while at least one is attached. Three constraints are easy to
break and worth knowing:

- Frames must be acked immediately and unconditionally — Chromium throttles then
  stops the screencast if acks dry up, so acking cannot depend on any client
  keeping up.
- Each sink keeps at most one pending frame (newest wins). Interactive use
  repaints on every mouse move, and an unbounded queue leaves the preview trailing
  the cursor by seconds.
- CDP only emits on *visual change*, so `add_frame_sink` primes each new
  subscriber with a `screenshot_b64()` snapshot; without it, a client attaching to
  an idle page sees a blank pane until something happens to move.

The screencast is per-connection and deliberately outlives any single run —
between tasks is exactly when a user wants to click around and log in somewhere.

## Configuration

Env vars (`backend/app/config.py`, read once at import): `MODEL`, `OLLAMA_URL`,
`MAX_STEPS`, `ALLOWED_ORIGINS`, plus `BACKEND_PORT` for the scripts. The frontend
defaults to `ws://localhost:8008/ws`, overridable with `VITE_WS_URL` — changing
the port means setting both.

`ALLOWED_ORIGINS` guards the socket itself: a WebSocket handshake is exempt from
the same-origin policy, so `ws()` refuses one carrying an unrecognised `Origin`
before `accept()`, with a 403 rather than a bare close frame so the reason shows
up in a browser console. Its default follows `FRONTEND_PORT`, which keeps
`FRONTEND_PORT=6000 ./dev.sh` working with nothing else set. An absent `Origin` is
allowed: browsers always send one, so its absence means a non-browser client, not
the drive-by page this closes off.

## Code shape

**Modules must be deep** — a small interface over substantial functionality,
judged by that ratio and not by line count. The two worth copying are described
above: `llm.py`, whose three functions hide streaming, NDJSON reassembly, timeouts
and error shaping, and `add_frame_sink`, whose two methods hide the entire
screencast. The failure to avoid is the shallow layer, which costs a call and an
abstraction and buys nothing:

- **No pass-through methods.** A body that is one call to the layer below with the
  same arguments is not an abstraction — it is a rename.
- **No pass-through layers.** If a concept has to be spelled out again at each
  level (a name here, an if-branch there, a one-line method below), the layers are
  shallow and the concept belongs in *one* of them.
- **One capability, one place.** Adding a tool, an event type or an action should
  mean editing one table or one module. Where it means editing three, that is the
  design telling you the interface is too wide.
- **Grow an existing module before adding one.** A new file is justified when a
  concept has an interface genuinely smaller than what it hides — not to keep files
  short. Splitting a deep module into two shallow ones is a net loss.

Design new work this way from the start: decide what the caller should *not* have
to know, and put the interface there.

The backend splits by concern — one module each for the socket, the loop, the
browser, routing and the model client. Two pieces are worth knowing:

- `Connection` in `main.py` holds everything scoped to one client: the screencast
  subscription, the in-flight turn, and who currently holds the browser. The
  browser itself is process-wide; everything else is per-socket.
- `TOOLS` in `agent.py` is the whole of the agent's action surface. Each row is a
  `Tool(name, schema, run)` built by `_tool()`, holding both what the model is told
  and what running it does, so **adding a tool means adding one row and nothing
  else**: `SCHEMAS` and the name lookup are both derived from the table, and each
  row's `run` owns the coercion its own schema implies. A name in no row comes back
  to the model as `Unknown tool 'x'.` rather than raising, but two rows *sharing* a
  name raise at import — that one fails silently otherwise, leaving the earlier row
  dead while Ollama is still told the tool exists twice.
  `test_every_declared_tool_reaches_the_browser` walks `TOOLS` and drives each row
  through the loop with arguments built from that row's own schema, asserting
  exactly one browser call, so a schema with no handler — or one wired to the wrong
  method — fails there instead of at the model.

On the frontend, pure logic lives in `src/lib/` so it can be tested without
rendering: `pageInput.js` (event → protocol mapping, coordinate scaling) and
`transcript.jsx` (prose rendering, folding narration into trace blocks). The
components are thin by comparison, and `usePageInput` carries the effects that turn
a DOM surface into a remote browser.

## Frontend

Two rules hold the interface together, and breaking either makes the UI lie about
what the machine is doing:

- **Colour reports state, nothing else.** The console is monochrome; the only hues
  are amber (agent acting), coral (you hold the browser) and mint (settled /
  connected). A fourth accent, or an existing one used for emphasis, breaks the
  reading.
- **Mono is the machine speaking, sans is language.** Trace steps, URLs, element
  indices and metrics are monospaced because they are machine output; your messages
  and the agent's answers are sans because they are prose.

Fonts are bundled via `@fontsource` rather than fetched from a CDN: a product whose
premise is running entirely on your machine should not need the network to render.

**The agent view** (`AgentView` in `PreviewWindow.jsx`) draws the numbered boxes the
model reasons over on top of the live page, and must show **exactly** the elements
the model was given and no more — which is why `observe()` and `_format_state()`
share the same `MODEL_ELEMENT_LIMIT` cut. Drawing all of them (an earlier version
sent 200) turns the feature into a picture of a page the agent cannot actually act
on. What was left out is counted in the status strip, because elements past the
limit are a real and otherwise invisible cause of failure: Hacker News' front page
has ~228, so the agent can only reach the first seven stories.

Layout uses `min-height: 0` on the grid rows and their children. Without it a
grid/flex child defaults to `min-height: auto`, refuses to shrink, and pushes the
composer off the bottom of the screen.

This is the surface *green before done* means: image overflowing the stage, 1.27:1
contrast on the step durations, focus rings killed by `outline: none` on the
inputs — every one invisible in the code and obvious in a screenshot or a
measurement. Reasoning about the CSS is not verifying it.
