# Contributing

Thanks for looking. This is a small, opinionated codebase, and most of what it
asks of a change is written down rather than assumed.

**`CLAUDE.md` is the binding document.** It sets out how work is scoped, how
tests are written, and what "deep module" means here, with the reasoning behind
each rule, so review can point at a rule rather than at a preference. It is
addressed to Claude Code because that is what writes most of the code here, but
the rules are the project's, not the tool's, and they apply to everyone.

This file does not restate those rules. It covers the one thing `CLAUDE.md`
cannot: what the path looks like from outside the repository, where you have no
write access and someone else presses merge. A second document repeating the
first is how `docs/code-walkthrough.md` died — precise enough to go stale, and
duplicating something already correct elsewhere. If you find this file
contradicting `CLAUDE.md`, `CLAUDE.md` wins and the contradiction is a bug worth
reporting.

| Question | Where it is answered |
| --- | --- |
| How do I run it? | `README.md` |
| How does it work? | `docs/how-it-works.md` |
| What is the WebSocket protocol? | `backend/app/main.py`'s module docstring |
| How should I scope, test and shape a change? | `CLAUDE.md` |
| What should I work on? | GitHub issues |

## Before you write anything

**Check the issues first.** They are the backlog, each written with enough detail
to pick up cold and labelled by how soon it will hurt:

```bash
gh issue list --label p1     # will bite in ordinary use
gh issue list --label p2     # real, but survivable
gh issue list --label p3     # worth doing, not urgent
```

One label is not a priority: `not-a-task` means the issue cannot be sliced
vertically as it stands, so it is parked rather than queued. Say what would make
it one before you start on it.

Issues drift, because they are written by hand and the code moves. Read the files
an issue cites and confirm the behaviour it describes still happens before you
fix it. If it has already been fixed, or the fix has to look different now, say
so in a comment — that is a useful contribution on its own.

For anything not already filed, **open an issue before the pull request**. Not
ceremony: the discussion is where a change gets scoped down to something that can
merge on its own, and it is much cheaper to have before the code exists. A one
line bug report is fine. So is "I think X is wrong, here is why".

## The shape of a change

One issue, one branch, one pull request. A branch carrying two unrelated fixes
cannot be reviewed or reverted as either of them.

Every change should be a **vertical slice**: shippable end to end on its own, with
nothing else needing to land alongside it. Three questions settle whether yours
is one — can it merge alone without breaking `main`, can a reviewer tell from the
diff whether it worked, and does finishing it change something observable? Any
*no* and it wants splitting. `CLAUDE.md` has the long version, including why a
change spanning `browser.py`, `agent.py`, the WebSocket contract and the preview
pane can still be a single slice while "add types to the backend" is not.

**No passengers.** Unrelated cleanup, a drive-by rename or a neighbouring bug you
spotted along the way is a separate issue, however tempting it is to fix in
passing. This is the most common reason a change gets sent back.

## Tests

**Strict TDD is the default here, not an aspiration**: write the failing test
first, watch it fail *for the right reason*, then write the least code that
passes it. A test you did not watch fail is not a guard — if you are fixing
something the suite missed, break your fix deliberately, confirm the new test
goes red, restore it, and say so in the pull request. "Confirmed failing before
the fix" is the sentence a reviewer trusts.

The exemptions are a **closed list**, and a short one: `CLAUDE.md` has it, and
that is the only copy on purpose, because widening the list *is* an edit to that
file. "Obvious" one-liners are not on it. If you think your change belongs
outside the list, say why in the pull request rather than deciding quietly.

Test at the level the defect lives, not the level that is easiest to reach — a
unit test of a private helper is usually the wrong altitude, and `CLAUDE.md` has
the worked example. Backend tests fake both the LLM and the browser, so no test
needs Ollama or Chromium; follow that. `CLAUDE.md` covers the rest, including
`browse_eval.py`, the opt-in harness for what the fakes cannot tell you.

## Green before done

```bash
./test.sh     # both suites
./lint.sh     # ruff + eslint; --fix applies what it can
```

Both must pass. `ruff format` is authoritative for Python layout, so hand
wrapping will just be undone.

**Anything with a visible surface is verified in the running app**, not reasoned
about. Overflowing images, unreadable contrast, focus rings killed by
`outline: none` — every one of those is invisible in the diff and obvious in a
screenshot. No runner looks at the page, so a green CI run is the cheap half of
this rule, not the rule. `./dev.sh` gets you both halves running.

## Opening the pull request

You will not have write access, so fork the repository and open the pull request
from a branch on your fork. Branch naming follows the repo's own:
`fix/<issue>-<slug>` for a filed issue (`fix/3-ws-origin`), and
`feature/<slug>` for anything else.

Put `Closes #<issue>` in the description so merging closes it, and say what the
issue was and **why the fix takes the shape it does** — the shape is the part a
reviewer cannot reconstruct from the diff. Include the doc updates your change
implies; `CLAUDE.md`, `docs/how-it-works.md`, the protocol docstring in `main.py`
and this file are part of the branch, not follow-up work.

Then two things gate it, both enforced by the repository rather than by custom:

- **`green`** — the CI job in `.github/workflows/ci.yml`, running `./lint.sh`,
  `./test.sh` and `npm run build` on a clean machine. It is a **required check**:
  a red run cannot be merged by anyone, including the repository owner. Rerun it
  by pushing a fix, and keep your branch up to date with `main`, which the rule
  also requires.
- **A review from the code owner** — `.github/CODEOWNERS`. Pull requests from
  contributors need one approving review before they can merge, and the owner
  presses merge; there is no path around this and you should not expect one. An
  approval covers the commits it was given on, so pushing after one lands
  dismisses it — a fix made during review needs a fresh look, not just a fresh
  green run.

Conversations on the pull request must be resolved before it merges, so reply to
review comments rather than silently pushing over them.

## What to expect

Expect review to be specific, and to be about the change rather than about you.
The point of having the rules written down is that a comment can name the one a
diff misses instead of trading tastes. Pushing back is legitimate, especially
when a rule genuinely does not fit your case — make that argument in the thread
rather than working around it quietly. The rules are not immovable either: the
exemption list above says as much about itself, and changing a rule means
changing `CLAUDE.md`, which is a pull request like any other.

Small, boring and complete beats large and impressive. A thin fix with a test
that was watched failing will land faster than a broad improvement that a
reviewer has to take on trust.

## License

Contributions are accepted under the [MIT License](LICENSE), the same terms the
project is released under.
