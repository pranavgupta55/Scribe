# Claude GUI-Session Playbook (Scribe)

> A living guide for Claude Code sessions making visual / front-end changes to
> the Scribe local web host (`graph/index.html`, `graph/graph.js`, the
> chat / copy / graph views, and anything CSS-touched).
>
> **This is intentionally incomplete.** It exists because GUI bugs are
> niche — each one teaches a specific lesson about how a particular layout,
> animation, or measurement breaks. Append new lessons to the bottom of the
> relevant section when you find one. Don't rewrite history; add.
>
> Last appended: 2026-06-21.

---

## Why this document exists

On 2026-06-20 two sessions tried to fix the same set of Scribe UI defects.

- **Session `f775e01f`** — earlier, longer (~1812 transcript lines). Spent
  several iterations chasing UI symptoms (e.g. "the decomp overlay isn't
  rendering") when the actual root cause was upstream of the DOM (e.g.
  the `qwen3` decomposer was emitting empty output because `think=False`
  hadn't been passed). The user had to send screenshots 10+ times and
  repeat the same complaint two or three times because fixes were pushed
  blind and didn't actually solve the problem.

- **Session `4d98dae7`** — later, shorter (~443 transcript lines). Same
  class of work, fewer cycles. Differences traced to four habits: ADRs
  before edits, reading the relevant code region first, running
  `serve.sh` for live verification, and asking the user explicit
  questions when intent was ambiguous (`/grill-me`).

The contrast is the entire reason for this document. The patterns below
are extracted from both sessions — what to repeat, what to avoid.

---

## The non-negotiable: live verification before push

GUI work is not testable by inspection. CSS, transforms, flex math, and
DOM measurements all interact at runtime; a change that "looks right" in
a diff regularly does something else on screen. Concrete failure mode
from session `4d98dae7`:

```
ADR 0004 D1 — clamp the chat shim's translateX to ±slack.
Math checked, code compiled, pushed without running serve.sh.
Result (next turn, with screenshot from user): chat-inner pinned to
chat-main's LEFT edge instead of its centre. The clamp was clamping to
the BOUND, not to 0. A 30-second visual check would have caught it.
```

ADR 0005 then explicitly listed "run `bash serve.sh` and visually
verify" as the final step in its Implementation Plan, treating it as a
hard gate rather than a courtesy.

**Rule:** if you touched anything that affects rendering, you run
`serve.sh` before committing.

### Restarting the server

`server.py` is plain `ThreadingHTTPServer`, no hot reload. Python file
changes don't propagate without restart. HTML/JS/CSS *do* propagate on
browser refresh (they're served as static files), but Chrome caches
aggressively — always hard-refresh (`cmd-shift-R`).

If port 8765 is occupied by a stale process:

```sh
lsof -ti:8765 | xargs kill
```

If you can't kill (permission, another user, sandbox), tell the user
and let them. Don't try `kill -9` on an arbitrary PID without consent —
the Claude Code sandbox will deny this for safety, and rightly.

### What to actually look at

Open each view that your change touched and exercise the dynamic state
that *caused* the bug. Static screenshots from the user are not enough;
many bugs only show up under interaction (e.g. carousel transitions,
sidebar collapse, resize). A non-exhaustive list of dynamic states
worth probing in this repo:

- **Chat view** — empty state vs. with messages; dev-panel open + right
  sidebar open; dev-panel open + right sidebar collapsed; both
  collapsed; very long messages; multi-paragraph code blocks;
  thinking-state spinner.
- **Copy view** — first turn; second turn (carousel up/down navigation
  matters here, see below); ▲ / ▼ pagination; queries that decompose
  into 1 / 2 / 5 sub-queries; high source-count queries (60+ sources);
  zero-new-sources turns; very narrow viewports.
- **Graph view** — empty graph; full graph; controls open vs. collapsed;
  legend open vs. collapsed; node selected vs. deselected.

If you can't reach a state, ask the user to drive — don't pretend you
verified it.

---

## Diagnose before fixing

The biggest source of wasted iterations in `f775e01f` was treating a
visual symptom as the bug. A few recurring traps in this codebase:

### "The overlay/widget isn't rendering"

Two equally-likely causes:

1. The DOM/CSS path is wrong (typo, z-index, display:none).
2. The data feeding the render is empty (server returned no sources,
   the decomposer emitted nothing, a flag like `think=False` was
   dropped).

Always inspect the actual JSON or event stream first. The `/api/chat`
endpoint sends a `debug` SSE event with the exact wire payload — open
DevTools' Network tab, find the streaming response, and read it.
The Dev panel in the chat view (the left column) is also designed to
show this: system prompt, query decomposition, retrieved RAG context,
final assembled wire payload, raw model response. If a section says
`(empty)`, the bug is upstream of the DOM.

### "Element X is in the wrong position"

Probably one of three things, in decreasing order of frequency:

1. **A transform is applied** (`translate`, `scale`) and `getBoundingClientRect()`
   is reading the post-transform size. See "Measurement: `offsetWidth` vs.
   `getBoundingClientRect()`" below — this caused the
   carousel-bento-shrinks-after-nav bug in ADR 0005 D3.
2. **A flex / grid item is being clamped by `min-width` or `max-width`**
   that you forgot was there. Always read the *current* CSS for the
   element before editing, even if you wrote the previous version.
3. **A JS shim is setting an inline `style.transform`** that survives
   across renders. ADR 0004 D1's shim left a stale `translateX(-290px)`
   on `.chat-inner` because the function was always re-applying it
   rather than clearing first. ADR 0005 D1 fixed this by stubbing the
   shim to a no-op that *clears* inline transforms.

### "Numbers don't match what I calculated"

This is almost always a clamp / cap / min interfering with your math.
If you write a formula like `shift = (rightW - devW) / 2`, walk through
the formula with concrete viewport numbers (e.g. dev=620, right=0,
chatMain=1300, inner=720) and check that the result *visually* puts
the element where you want. Don't just check that the formula type-checks.

---

## Specific Scribe gotchas

These are concrete pitfalls observed in this repo. Each one is worth
remembering because the obvious-looking fix is wrong.

### Measurement: `offsetWidth` vs. `getBoundingClientRect()`

`Element.getBoundingClientRect()` returns the *rendered* size — including
any CSS `transform`. `Element.offsetWidth` and `Element.offsetHeight`
return the *layout-box* size, ignoring transforms.

In the copy view, `.copy-turn` carries a `transition: transform 0.32s`
that animates `translate` + `scale` whenever the user navigates ▲ / ▼.
If `layoutBentoFor()` measures the column's width mid-transition with
`getBoundingClientRect()`, it gets the scaled value, computes a smaller
`cellSize`, and renders shrunken cells until something else (e.g. a
browser zoom firing a fresh `ResizeObserver`) triggers a re-measurement.

**Rule:** when measuring anything inside an element that has a `transition`
on a transform property, use `offsetWidth` / `offsetHeight`. Reserve
`getBoundingClientRect()` for cases where you genuinely need post-transform
coordinates (e.g. hit-testing).

### Centering inside flex columns: don't reinvent it

ADR 0002 D2 introduced `centerChatToViewport()`, a JS shim that
applied `translateX((rightW - devW) / 2)` to `.chat-inner` and
`.composer-inner` to keep them at the *viewport* midline regardless of
sidebar widths. It worked when both sidebars were similar widths and
broke spectacularly when they weren't — the inner got shoved under the
dev-panel. ADR 0004 D1's "clamp" attempt was off-by-bound and made
things worse. ADR 0005 D1 deleted the shim entirely; natural CSS
`margin: 0 auto` inside `#chat-main { flex: 1 }` does the right thing.

**Rule:** if natural CSS centering already does what you want, don't
override it with JS. The user's mental model of "centered in the
leftover space" is what flex centering provides. JS shims for layout
are a code smell.

### Inline `style.*` lingers across re-renders

Most JS code paths in this repo set inline styles (`el.style.transform =
'translateX(...)'`). These persist on the DOM until something removes
them. If you refactor a function to *not* set an inline style anymore,
also clear any inline style that earlier versions might have left.

Pattern to use: a "no-op clear" function. ADR 0005 D1's
`centerChatToViewport` survives as `el.style.transform = ''` for every
target — it does nothing if no prior code set the transform; clears it
if something did.

### The carousel intentionally shows neighbours

The copy view's `.copy-turn.off` adjacent turns are *meant* to be
visible as faded slivers above and below the focused turn — that's the
"new pushes old upwards" affordance. Don't mistake this for an
overflow bug. (Confirmed in session `4d98dae7` after the user
explicitly called it out.)

### Topbar is invisible but reserved

`#topbar` is `background: transparent; border-bottom: none` (ADR 0003).
The 48 px reserve is held open by `body { padding-top: 48px }`. The
buttons inside (`#dev-toggle`, `#status-pill`, `#view-toggle`, etc.)
keep their own backgrounds, so the bar reads as floating chips on a
seamless canvas. If you add a new control to the topbar, give it a
background and a border so it remains visible — don't expect to inherit
chrome from the bar.

### `chat-history` ≠ "the prompt the model sees"

`chatHistory` (client) is an array of `{role, content, sources?}` —
nothing more. The actual wire payload to the model is constructed
server-side in `build_chat_entries()` + `assemble_wire()`
(`server.py:614`). If the Dev panel's "Full user prompt (as sent)"
section seems to undercount tokens, you are almost certainly displaying
the wrong intermediate (`build_prompt()` is one *entry's* text, not the
full conversation). See commit `92cd8a9` for the fix.

### Dev panel data is server-driven

Everything in the chat-view dev panel comes from a single SSE event of
`type: "debug"`. The fields it includes (`system`, `sub_queries`,
`context`, `prompt`, `system_tokens`, `history_tokens`, `history_msgs`)
are the *only* place the client should be sourcing dev-panel state. If
you want a new field shown, add it to the server's debug event payload
and read it on the client. Don't compute it locally — you'll drift.

---

## How to communicate with the user (and how not to)

The two sessions diverged sharply on communication quality. Patterns
that worked, with examples.

### Be explicit about what you're talking about

In `4d98dae7`, the user explicitly redirected:

> "for one when you ask questions describe what you're talking about
> better since I don't know what 'B2' is."

The assistant had been referring to bugs by an internal numbering
scheme (B1–B9) without describing them. Even though those labels were
clear to the assistant after a long planning step, they were opaque to
the user. Fix: every question or option in `AskUserQuestion` should
describe the *thing in the UI* (e.g. "the column between NEW SOURCES
and ALL SOURCES with the numbered question cards") rather than an
internal label.

This is the same instinct as not narrating tool calls or thinking
state — the user only sees the rendered output, not your scratchpad.

### Quote the user's exact words back when checking understanding

When the user gave a feedback list, the assistant restated each item
in the user's own phrasing in the `/grill-me` round. The user
confirmed quickly because there was nothing to translate. If you
paraphrase, you risk silently changing the requirement.

### Ask for screenshots when intent is ambiguous

In `f775e01f`, the assistant several times tried to fix layout
problems from prose descriptions alone — and missed by a wide margin.
In `4d98dae7`, the user attached screenshots up-front; the few
ambiguous moments (e.g. "the decomp panels are still not centered" —
plural "panels" was ambiguous: cards inside the strip, or the strip
itself?) would have been clarified faster with a screenshot.

**Heuristic:** if the user's text references a visual property
("centered," "overlap," "uneven spacing"), and you don't have a
screenshot showing that state, ask. The cost of asking is one round-
trip; the cost of guessing wrong is two or three.

### Cite line numbers and file paths

When discussing a bug, reference the exact code:
`graph/graph.js:1044 (centerChatToViewport)`. The user has the codebase
open and can jump straight there. The earlier session used vague
references like "the chat scroll function" — slower for the user to
parse, easier to mis-target on fix.

### When you're terse, the user appreciates it

`/caveman` mode was active for most of `4d98dae7` and produced visibly
faster turns. The user explicitly asked for it. Don't *default* to
caveman — wait for the signal — but recognize that this user prefers
short fragmented technical prose over polite full sentences. The CLAUDE.md
in `~/.claude/` confirms: "Keep responses concise. No trailing
summaries or 'here's what I did' recaps at end of responses."

---

## Tools and process

### ADRs are scope-control devices

Each ADR (0003, 0004, 0005) in this session bundled a coherent set of
changes, documented the *why* and the alternatives considered, and
gated implementation on user acceptance. The benefits:

- **Scope is explicit.** "Here are the seven things I plan to change.
  Confirm or redirect." Reduces drift mid-implementation.
- **Reversibility is documented.** ADR 0005 explicitly supersedes
  parts of ADR 0002 and ADR 0004. A future engineer can read the
  history of *why* the chat shim was added, clamped, then dropped —
  without re-inventing the analysis.
- **Build Log captures what actually happened.** If implementation
  diverged from the plan, that's a separate paragraph at the bottom,
  not a retroactive edit to the decision.

When the user says "/adr", treat it as a contract: write the ADR
*before* the edits, and don't ship anything that isn't in the plan.

### `/grill-me` is a confirmation device, not a brainstorm

The `/grill-me` skill is for the model to ask the user pointed
multiple-choice questions before committing to an approach. Best used
when you've narrowed to 2–4 plausible options and need the human's
judgement call. Not useful for "what should I build?" — that's
upstream of grilling.

Format observed to work well: `AskUserQuestion` with up to four
questions per call, each with 2–4 mutually-exclusive options plus a
clear "Recommended" tag on your best guess. Include code snippets or
ASCII previews in the `preview` field when the option is a layout
choice the user might want to compare visually.

### TaskCreate + TaskUpdate

For multi-step UI work (audit → ADR → implement → verify → push), use
TaskCreate for each major step and update status as you go. Even when
work is sequential, the user benefits from seeing the queue. Skip for
single-edit fixes — three trivial tasks add more noise than signal.

### `git` discipline

- One commit per logical change. ADR 0005's commit (`c95f00c`)
  bundled seven small changes that all addressed the same defect list
  — keeping them together makes `git log` readable.
- Reference the ADR in the commit message. `5d318b3`, `454f879`,
  `c95f00c`, `92cd8a9` all open with "ADR-XXXX:".
- Push immediately after the commit when work is small and verified.
- Don't squash multiple ADRs into one commit.

### Specific tool habits

- **Read before Edit.** Always read the surrounding code (the function
  containing your edit, the CSS rule block, the call site) before
  changing it. The `Read` tool isn't free, but reading the wrong line
  is more expensive than reading 50 unnecessary ones.
- **`grep` for symbols.** When tracking down where a function or class
  is used, `grep -n 'symbolName' file.js` is faster than scrolling.
- **Don't `cat` / `head` / `tail`.** Use `Read` for files (line-numbered
  output is helpful when editing). Use `Bash` `wc -l` to size up.

---

## Anti-patterns to avoid (with citations)

1. **Pushing without running the server.** ADR 0004 D1 (clamp). The
   commit shipped a math bug that the user had to catch on the next
   turn with a fresh screenshot. ADR 0005 then promoted live
   verification to a mandatory checkpoint.

2. **Inventing internal jargon (`B1`, `B2`) without translation.**
   `4d98dae7` turn 27 — user explicitly redirected.

3. **Fixing a symptom without understanding the cause.** `f775e01f`'s
   overlay-not-rendering loop — the bug was upstream (qwen3 needs
   `think=False`); three iterations of CSS tweaks didn't help.

4. **Setting inline styles without clearing.** ADR 0004's
   `centerChatToViewport()` always reapplied, never cleared; the user
   then saw stale offsets from a prior state.

5. **Trusting `getBoundingClientRect()` on transformed elements.** ADR
   0005 D3 — the carousel bento shrunk because the col was measured
   mid-transition.

6. **Asking yes/no questions when the user has a clear preference for
   choices.** The user wants to compare options side-by-side, not give
   permission. Multi-option `AskUserQuestion` outperforms.

7. **Letting one ADR cover unrelated changes.** ADR 0005 covers seven
   defects because they were all caught in one user feedback cycle.
   That's fine. What's *not* fine is bundling unrelated decisions
   ("topbar invisibility + new chat history backend") into one ADR;
   they should supersede or coexist as separate records.

8. **Editing an accepted ADR.** They're immutable. Supersede with a
   new ADR instead. (Already in ADR 0002, ADR 0004, ADR 0005 chain.)

9. **Skipping the user-validation checkpoint.** "Looks good!" is the
   gate. Don't move to the next task without it.

---

## What's still open / lessons we haven't learned yet

This list grows. Items live here until they're either resolved or
written up as a fuller lesson above.

- **Visual rendering by the assistant.** Neither session had a
  reliable way for the assistant to *see* the rendered output of its
  own changes. Future improvement: a headless screenshot script
  (`puppeteer`, `playwright`) wired to a Claude Code skill that lets
  the assistant capture the post-build state and compare against a
  baseline. Until that exists, the user is the screenshot pipeline.

- **Carousel transition timing.** ADR 0005 D3 fixed the measurement
  by switching to `offsetWidth`, but didn't address the broader
  pattern of "code that runs while a CSS `transition` is in flight."
  If you find yourself needing to react after a transition completes,
  use a `transitionend` listener — not double `requestAnimationFrame`.

- **Outer-gutter symmetry in copy view.** The user flagged that the
  right-side nav arrows occupy part of the 80 px gutter while the
  left side is empty, creating visual asymmetry. Not yet fixed
  (deferred in ADR 0005). Worth a future ADR.

- **Token-budget visibility.** The conv-tokens counter (commit
  `92cd8a9`) is the first piece of context-window visibility. As the
  history grows we may want a "compaction in progress" indicator when
  Tier 1 / Tier 2 truncation fires server-side. Mention in dev panel?
  TBD.

---

## How to extend this document

When you finish a Scribe GUI session, ask:

1. **Did anything take more than two iterations to land?** If yes, add
   it to "Anti-patterns to avoid" or to the "Specific Scribe gotchas"
   section, depending on whether the lesson is process or layout.

2. **Did you find a repo-specific layout/rendering quirk?** Add it to
   "Specific Scribe gotchas". Cite a file:line so future sessions can
   navigate to it.

3. **Did the user say something that should be a standing rule?**
   ("Always X before Y", "prefer A to B for this codebase".) Quote
   them, attribute to the date, and put it under "How to communicate
   with the user".

Don't rewrite existing entries — append. The history is the value;
later sessions need to see what was tried and rejected, not a sanitized
current state. Keep entries short, name files and ADRs, and write so
the next assistant — with zero prior context — can use the lesson.

---

## Appendix: References

- ADR 0001 — RAG chat architecture
- ADR 0002 — UI polish + gibberish gate (UI parts now superseded)
- ADR 0003 — Topbar invisibility
- ADR 0004 — Visual-bug batch round 1 (chat clamp superseded by 0005)
- ADR 0005 — Visual-bug batch round 2 (current shipped state)
- `ARCHITECTURE.md` — wire-level architecture, RAG flow
- `UBIQUITOUS_LANGUAGE.md` — terminology in this repo

Session transcripts (local, not in repo):
- `~/.claude/projects/-Users-pranavgupta/f775e01f-83e8-4e4d-afbe-a45452569fe9.jsonl`
- `~/.claude/projects/-Users-pranavgupta/4d98dae7-4872-4cf8-bdda-8a648d6b8208.jsonl`
