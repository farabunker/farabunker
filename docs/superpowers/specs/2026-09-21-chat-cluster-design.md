# Chat cluster — design spec

**Date:** 2026-09-21
**Amended (2):** 2026-09-21 — round-2 scoped re-check (0 Major, 3 Minor, 3 Nit; all three
round-1 alternatives ACCEPTED and the redesigned audience mechanism re-probed leak-free on four
escape routes). **R1** the round-1 amendment justified decision 23 three times on a false premise
— `manage.py install_defaults` runs as `OPEN_PRINCIPAL`, not a service principal; the conclusion
survives through `_user_row`'s `kind != "user"` branch and is now scoped to an accounts-on box
(§4.3.2, decision 23, §15 item 2). **R2** the poll path's "exactly one query" was aspirational —
`visible_turn`'s `select_related` is widened so it becomes true (§3.5, §3.6, §8, §9). **R3** the
redesign opened a disclosure gap: a non-admin owner could not be told their agent carries an
administrator's label, and the entitlement non-disclosure gate forbids naming it — §4.3.1 gains
the `name_for_viewer` idiom and a counted sentence. Nits **R4** (§8's fourth queryset), **R5**
(§4.8's stale `set_agent_audience`) and **R6** (§4.3.1's truth table) applied as written. §15
carries the round-2 dispositions.
**Amended:** 2026-09-21 — adversarial review (7 Major, 10 Minor, 4 Nit), applied **in place**
rather than appended, following the identity, workstreams and settings-assistant specs' own
handling of a review: the sections that carry the mechanics are amended where the mechanics
live. **M1** the audience control was a whole-set write through a raw writer, so a non-admin
owner could delete an administrator's label and widen the agent — §4.3 rewritten around the
existing `parse_entitlement_diff` add/remove gate. **M2** branching was gated on `may_post_to`
where duplication is gated on `may_manage_conversation`, letting a share recipient mint a
durable owned copy — §5.3 narrowed, §5.4's false "applies here unchanged" corrected, flag 7
added. **M3** the poll path has no picked connection and no `Preflight`, so a refreshed
denominator would silently differ from the page's — §3.5 rewritten so the window never travels.
**M4** "measures exactly the corpus" was false for tool-call blocks, replay fences and foreign
headers — §3.3 corrected. **M5** `chat-default-install` is class A, so a member can already
create a box-wide row — §4.3 states the interaction and flag 8 asks whether to close it.
**M6** `editable_agents` had no open-posture branch — §4.2 given the column's standard shape.
**M7** `branch_conversation` cannot call `start_turn` (import direction) — §5.4 split between
the visibility function and the view. Minors and nits landed in §3.2, §3.3, §3.4, §3.5, §3.6,
§4.4, §4.5, §5.2, §6, §7, §8, §9, §10, §12 and §13. §15 records the three places this amendment
implements something other than the edit the review proposed, with reasoning.
**Status:** Design. Nothing built. Written against `HEAD = a4d1033` on branch
`worktree-model-management-framework`; every file, function and behaviour cited below was read
in that tree at write time and is cited by anchor (module, function or grep-able phrase),
never by line number.
**Columns touched:** `agents/` (root, `chat/`, `runtime/`), `foundation/settings_area.py`
(one registration), `identity/routes.py` + `identity/contracts/actions.py` (two additive
tables), `models/contracts/bindings.py` (one pure read).
**Consumes, does not reshape:** `identity/access.py`, `identity/contracts/axes.py`,
`models/registry/bindings.py`, `tools/rag` (the attachment seams).
**Lands as:** three plan-sized features, in the owner's order, on one branch. Each is
independently shippable; feature C depends on nothing in A or B.

Three features, in the owner's order:

1. **Context usage in chat** — what the next turn will cost the bound model, as a number and a
   percentage, honestly labelled as an estimate.
2. **An agent-edit utility** — one edit component mounted in two areas, with an audience model
   built out of the entitlement and ownership seams that already exist.
3. **Edit a past prompt and carry on from there** — recommended as a BRANCH, spelled out in
   full, with the fork flagged for the owner.

No model or vendor names appear anywhere in this document. No absolute paths appear anywhere
in this document. There are **zero open questions**: every call is made, the judgement calls
are numbered in §12 and the ones the owner may want to overrule are listed again in §13.

---

## Table of contents

1. [Owner requirements, verbatim](#1-owner-requirements-verbatim)
2. [What exists today](#2-what-exists-today)
3. [Feature A — context usage in chat](#3-feature-a--context-usage-in-chat)
4. [Feature B — the agent-edit utility](#4-feature-b--the-agent-edit-utility)
5. [Feature C — edit a past prompt](#5-feature-c--edit-a-past-prompt)
6. [Routes, postures and gating](#6-routes-postures-and-gating)
7. [Migrations](#7-migrations)
8. [Query budget and script budget](#8-query-budget-and-script-budget)
9. [Tests](#9-tests)
10. [Documentation](#10-documentation)
11. [Phasing and done-when](#11-phasing-and-done-when)
12. [Decisions the author made](#12-decisions-the-author-made)
13. [Flagged for the owner](#13-flagged-for-the-owner)
14. [Out of scope, named](#14-out-of-scope-named)
15. [Review response](#15-review-response)

---

## 1. Owner requirements, verbatim

**A — token tracking.**

> "it should have an understanding of the models max token usage, and how many tokens the
> current chat is using, and then show that in the UI as a value and % of total so the user can
> understand when to compact."

**B — the agents screen as a utility.**

> "needs to be a utility and be called on various areas... the main chat for everyone system
> needs to be available for admin... other agents may be created by users and may not require
> admin access to maintain/create/modify. However, the main chat should be owned by the admin.
> Note that agents may be user specific, entitlement specific (i.e. all users of an entitlement
> may have access to an agent someone creates) or admin settings specific. It needs to be
> constructed so that it's not an entangled mess."

**C — edit a past prompt.** A backlog item, now approved: in a conversation, edit one of your
earlier prompts and carry the conversation on from that point.

---

## 2. What exists today

Stated so nobody builds what is already there, and so every claim below can be checked.

### 2.1 How a prompt is actually assembled

`agents/runtime/prompt.py::build_messages` is the one builder. It emits, in order: a SYSTEM
message (the agent's `system_prompt`, plus the workstream's `instructions` block, plus the
attachments block, plus the clock line — each appended as its own paragraph, and the message is
omitted entirely when all four are empty), then `history_messages(...)`, then an optional user
message, then the carrying turn's own attachment blocks as a final USER message.

`history_messages` replays **the last `HISTORY_TURNS` turns, not a token budget.** The constant
is `20` (`agents/limits.py`), and its own docstring states the reason plainly: the bound
model's `context_window` is an operational bound the platform already owns, and *"a token
counter here would be a second, drifting one"*. Only `state="done"`, `depth=0` turns replay.

**This is the single most important fact for feature A**, and it is the one the owner's
sentence does not yet know: a conversation of two hundred turns does not send two hundred turns.
It sends twenty. A meter that reported "this chat is using 400% of the model's context" would be
false in the direction that matters. §3 measures what is *sent*.

### 2.2 What the platform knows about a model's ceiling

- `models/registry/models.py::ModelConnection.context_window` — an optional, **operator-set**,
  per-connection positive integer. Null/blank means unset.
- `models/registry/bindings.py::resolved_from_connection` threads a set value into
  `ResolvedModel.config["context_window"]`; unset leaves the key absent entirely.
- The chat adapter (`models/contracts/engines/ollama.py::build_llm`) then applies
  `cfg.setdefault("context_window", DEFAULT_CONTEXT_WINDOW)`, `DEFAULT_CONTEXT_WINDOW = 8192`.
- **Nothing anywhere reads an engine-reported context length.** `list_installed` reads
  `details.embedding_length`; `supports_tool_calling` reads `capabilities`. Neither touches the
  architecture-maximum context length, and that is deliberate: ADR 0010's incident write-up
  says the guard exists *"so the adapter can never again let a client probe a model server's
  architecture max on the platform's behalf."*

So the operative window — the number the engine is actually asked to allocate — is
`config["context_window"]` when the operator set one, and the adapter's bounded default
otherwise. **That, not a model's theoretical maximum, is the honest denominator**, and §3.2
takes it.

### 2.3 Agents

`agents/models.py::Agent` carries `slug` (immutable — `save()` refuses a change, because a slug
is a key that `flow.run`, an `agent.<slug>` grant and `install_defaults --reset` all resolve),
`name`, `description`, `system_prompt`, `llm_role`, `tool_keys`, `max_steps`, `resident`,
`enabled`, and the `owner_kind`/`owner_key` pair every owned row in this codebase carries.

`agents/visibility.py::visible_agents` is the one gate:

```
enabled=True AND ( owned | resident=True | shared(Share.Target.AGENT) ) AND label_permitted_q
```

- `owned` — `identity/access.py::owned_rows_q`, plus service-made rows for an admin.
- `resident=True` — *"the shipped defaults… the platform's own offer"*, visible to everybody.
- `Share.Target.AGENT` — the target exists in `agents/models.py::Share` and is read by
  `visible_agents`. **It has no writer and no UI.**
- `label_permitted_q` — unlabelled rows pass; a labelled row passes only for a holder of one of
  its `AgentEntitlement` rows. AND-ed onto the ownership OR, so a label genuinely restricts.

**There is no agent create or edit surface anywhere in the tree.** `grep` for `system_prompt`
finds the model field, `agents/defaults.py`'s catalogue, and the two prompt builders — no form,
no view, no template. Rows are born from `agents.defaults.install_default` (the catalogue, on
consent) and nothing else. Feature B builds the first one.

`/chat/access/` (`agents/chat/views/access.py`, route class **S**) already labels agents and
flows with entitlements, through `agents/labels.py`'s single writers, which diff and audit.
Feature B reuses those writers and never touches a join table directly.

### 2.4 Conversations, turns, and what duplication already does

`agents/visibility.py::duplicate_conversation` copies a conversation's **terminal** turns
(`done`/`failed`/`cancelled`) into a new conversation owned by the acting principal, renumbering
indexes from zero, carrying `workstream` (ruling D — a copy stays in the stream, or the copy
would launder labelled material out of every gate), copying every `ConversationTaint` row, and
deliberately **not** copying `Turn.invocation` (an audit row belongs to one conversation),
`Turn.queue_job_id`, or any `DocumentAttachment` row.

That function is 90% of feature C. §5 is mostly a narrowing of it.

`ConversationTaint` is **additive only** in v1 — rows are created, never deleted except by the
conversation's own cascade and the entitlement cascade. Removal is a named deferral with a real
design question behind it. **This is why feature C cannot be a rewind** (§5.1).

`Turn.invocation` is `SET_NULL`, `Turn.author` is a nullable FK added by C-1 and read by
`prompt.py::_is_foreign_user_turn` to fence another person's words in a replay.

`DocumentAttachment` (`tools/rag/models.py`) is keyed by `document` FK + `conversation_id`
(a UUID **by value** — `tools/rag` may not import `agents.models`) + `turn_id`. The only writer
is `tools.rag.services.stage_turn_attachments`, reached through the sanctioned
`agents/contracts/attachments.py` registry. There are four registered seams (provider, cleanup,
uploader, detacher). **There is no copier.**

### 2.5 The chat page's disciplines

- **Script budget, pinned by count.** `agents/chat/tests/test_thread.py::
  TestTheDragAndDropStaging` asserts `body.count("<script") == 4` with the attach door present
  and `== 3` without. Any new behaviour on this page must live inside an existing script block
  or none at all.
- **The poller** (`chat/conversation.html`, `{% block scripts %}`) polls `chat-turn-status`
  and swaps whole turn-group HTML. Round 13's lesson is recorded in that template: a strip
  outside a turn card that nothing ever refreshed *"could go stale forever once the poller
  stopped touching anything outside a turn card"*.
- **Never-500** (`agents/chat/tests/test_never_500.py`), **route matrix**
  (`identity/tests/test_route_matrix.py`), **column boundaries** (no module under `agents/chat`
  may touch `Agent`/`Conversation`/`Flow` `.objects` directly — everything goes through
  `agents/visibility.py`), **CSS ownership** (chat CSS lives in `chat/base.html`).
- **Query pins on the thread page are equality-under-scale**, not absolute counts:
  `test_thread.py` compares a one-row render against a many-row render. A new *constant* query
  is allowed; a per-turn one is not.
- **The settings area** is one table, `foundation/settings_area.py::SETTINGS_GROUPS`, walked by
  a drift test in `foundation/tests/test_shell.py`.

---

## 3. Feature A — context usage in chat

### 3.1 What the number IS

**An estimate of the characters the next turn's prompt will carry, divided by a disclosed
constant.** It is never presented as a count. Every surface that shows it carries the word
"estimate" or a `~`, and a disclosure explains the method in one sentence.

There is no tokenizer on this box. `requirements.txt` carries no tokenizer package, and adding
one for a status line would be a new dependency for a cosmetic number. Asking the engine to
tokenize would be a network call per page render, on a surface whose whole design avoids
per-render engine traffic. **Characters divided by four**, named as such, is the honest answer.

`agents/usage.py` (new, column root — the same placement rule `agents/visibility.py` and
`agents/labels.py` record: a future management command or MCP edge needs the same answer without
being a view, and modules under `agents/chat` may not reach `Agent`/`Conversation`/`Flow`
`.objects` at all. **`Turn` is not on that list** — `agents/chat/service.py` uses
`Turn.objects.filter/.create/.update` in five places today, and §2.5 names the boundary
correctly; an earlier draft added `Turn` to it and would have forbidden `turn_edit` from doing
what `turn_create` already does (review m5)):

```python
CHARS_PER_TOKEN = 4          # disclosed in the UI; never presented as exact

@dataclass(frozen=True)
class ContextUsage:
    estimated_tokens: int    # the whole prompt estimate
    window: int              # 0 when unknown -- see §3.2
    window_source: str       # "connection" | "engine-default" | "unknown"
    percent: int | None      # None when window == 0
    band: str                # "ok" | "high" | "full" | "unknown"
    replayed_turns: int      # how many turns the next prompt will carry
    total_turns: int         # how many the conversation holds
    truncated: bool          # total_turns > replayed_turns
```

`estimate_tokens(text) -> int` is `ceil(len(text) / CHARS_PER_TOKEN)` and is the **one**
arithmetic in the feature. `context_usage(conversation, agent, *, window, window_source)`
composes it.

### 3.2 Where the ceiling comes from

**Decided: the operative window — what the engine will actually be asked to allocate — and
never an engine probe.**

`models/contracts/bindings.py` (pure, already in `agents/runtime/bindings.py`'s import graph,
and it already imports the chat adapter's module for `OllamaEngine.name`) gains one function:

```python
def effective_context_window(resolved) -> tuple[int, str]:
    """The window this binding will ask its engine to allocate, and where it came from.

    (n, "connection")      -- an operator set `ModelConnection.context_window`.
    (n, "engine-default")  -- the engine adapter's own bounded default applies.
    (0, "unknown")         -- this engine declares no default here; say so.
    """
```

It reads `resolved.config.get("context_window")` first. With no operator value it returns the
adapter's own bounded default **only for an engine that declares one** (today the chat adapter's
`DEFAULT_CONTEXT_WINDOW`), and `(0, "unknown")` otherwise. A future adapter declares its own; the
function grows a lookup, not a special case.

Three things this deliberately does NOT do:

- **It never probes the engine for an architecture maximum.** ADR 0010's own fix sentence forbids
  exactly that, and a display probe is still a probe — one per page render, against a machine
  that may be asleep.
- **It never shows a model's theoretical maximum.** If the engine is asked for 8,192 and the
  model could do far more, then 8,192 is the number that truncates the conversation. Showing the
  larger one would lie in the dangerous direction: the reader would believe they had room they do
  not have.
- **It writes nothing and sends nothing.** The house lesson stands: we never send a
  model-behaviour knob the operator did not set. This is a pure read of a value the platform
  already resolved.

It also `int()`-casts whatever it reads and degrades to `(0, "unknown")` on a value that will
not cast, for the reason `tools/rag/views.py::_resolved_answer_context_window` already records
for its own cast: nothing enforces that a stored `context_window` is an integer, and an
uncastable one reaching an arithmetic comparison is a 500 on a never-500 surface.

**The page pays nothing for it.** `agents/chat/views/thread.py::thread_context` already holds
`check = preflight_turn(...)`, and `Preflight.resolved` is the `ResolvedModel` for this turn's
bound model. The window comes off a value already in hand.

**The branch is `check.resolved is None`, never `check.ok`** (review m1). A refusal does not
imply a missing binding: `agents/runtime/preflight.py`'s `NO_TOOL_CALLING` leg returns
`Preflight(False, …, resolved, answered_by, …)` carrying a real `ResolvedModel`. A turn that
cannot run because the bound model will not call tools still has a known ceiling, and the meter
shows it. Only `resolved is None` — an unbound role, an unregistered pick, a label refusal —
renders the tokens with no ceiling, beside the honest line the page already carries
(`unavailable`).

**One box, two readers of the same fact, and they answer differently on purpose** (review m10).
`tools/rag/views.py::_resolved_answer_context_window` already reads
`resolve(RAG_ANSWER_ROLE).config.get("context_window")` for the Ask page's top-k fit check, and
returns `None` — "unknown" — where `effective_context_window` returns the adapter's bounded
default. The divergence is deliberate and each side keeps its own reading:

- The **fit check** decides whether to run a check at all. Skipping a check on a guessed number
  is safe; running one on a guessed number would refuse a legitimate `k` for a reason the
  operator never configured. "Unknown" is the correct conservative answer there.
- The **meter** must display something, and the adapter's default is what the engine will
  actually be asked for. Displaying "unknown" while the engine truncates at a number we know
  would be the less honest of the two.

They also ask about different bindings — the fit check about `rag.answer`, the meter about this
agent's own chat binding or the picked connection — so they are not two answers to one question.
§10 records the reconciliation in `models/registry/README.md` so the next reader finds both.

### 3.3 What is counted, and what is not

`context_usage` measures **the text bodies of** the corpus `build_messages` will replay —
not, as an earlier draft of this section claimed, *exactly* that corpus (review M4):

- `agent.system_prompt`;
- `conversation.workstream.instructions`, when there is a stream with non-blank instructions
  (the row is already loaded on this render);
- the last `HISTORY_TURNS` `state="done"`, `depth=0` turns' `text`, in **one flat query**:
  `.filter(...).order_by("-index").values_list("role", "text")[:HISTORY_TURNS]` — constant cost
  whatever the conversation's length, which is what keeps the equality-under-scale pins green.

**Five things are excluded, every one of them an under-count, and the disclosure names the two
a reader can act on:**

1. **The tool-call block a tool turn replays.** `history_messages` does not emit `Turn.text` for
   a `role == "tool"` row — it calls `tool_turn_messages(turn)`, which emits **two** messages,
   the first an ASSISTANT `ToolCallBlock` carrying `wire_name(call["tool"])` and the full
   `tool_kwargs` JSON. None of that is in `Turn.text`, and on a tool-heavy conversation it is
   not a rounding error.
2. **The fence a replayed assistant turn carries.** `_replay_assistant_text` re-derives a fenced
   version from `turn.data["appended_tool_result"]` — a DATA header plus BEGIN/END markers —
   rather than replaying the stored text verbatim.
3. **The header on another person's turn.** A foreign USER turn is wrapped in
   `_FOREIGN_TURN_HEADER` before it is replayed.
4. **Attached-file text.** The carrying turn's inlined extracted text and the attachments block
   vary with the acting principal and cost extra queries, and they belong to the turn being sent
   rather than to the conversation's history.
5. **Tool results not yet written.** A turn that calls three tools adds text the estimate cannot
   know before the turn runs.

**The clock line is not counted either, and that is a budget decision** (review m2, m9).
`ChatSettings` is read nowhere on a thread render — `time_aware_now_line()` reads the singleton
inside `build_messages`, at turn time — so counting the clock would cost a **second** new query
on the page and another on every poll tick. It is also the smallest term in the estimate: one
short line, plus an 18-character prefix on the replayed **non-tool** turns only
(`_TIMESTAMPED_ROLES = ("user", "assistant", "system")`; a tool turn is never prefixed, and
`prompt.py`'s docstring says why). Dropped rather than bought, and named here so the omission is
a decision rather than an oversight.

Every exclusion makes the estimate an **under-count**, which is stated. Under-counting is the
right direction to be wrong in for a "when should I compact" signal only if the reader is told;
they are told, and §3.4's disclosure names tool calls explicitly because that is the one
exclusion large enough to change a reader's decision.

**One counter, not two.** `agents/limits.py::HISTORY_TURNS`' docstring warns against exactly the
drift a second counter would cause. `estimate_tokens` is therefore the only arithmetic, and
compaction (out of scope) will call this same function. The runtime stamps nothing on a turn in
v1 — §14 names that as the follow-up if compaction wants an audit trail of what was really sent.

### 3.4 What the reader sees

A single server-rendered line, the last child of `.composer-block` on `chat/conversation.html`,
below the composer and above `chat/_thread_actions.html`:

> Context ~3,400 of 8,192 tokens (41%) · estimate

plus a thin CSS bar whose width is the percentage, plus a `<details>` disclosure — the house's
zero-JS idiom, already used on this surface — reading:

> How this is worked out. Only the last 20 messages of a conversation are sent to the model; a
> longer conversation is already shortened before it is sent. The number is an estimate, about
> four characters to a token, of the words in those messages. It does not count files attached
> to a message, or what a tool was asked and answered. The limit is the one this connection is
> set to use.

**The CSS lives in `conversation.html`'s own `{% block chat_style %}`, not in
`chat/base.html`** (review m6). `foundation/ops/tests/test_css_ownership.py`'s rule is that a
selector's home is the deepest template that is an ancestor of every template using it, and a
page-only rule belongs in that page's own `_style` block — the placement
`chat/_thread_actions.html`'s own comment records for its neighbour row on this very page. The
meter's only consumer is `conversation.html`'s body. (§5.7's `chat/base.html` placement for the
edit disclosure **is** correct by the same rule: `_turn_card.html` is a fragment with more than
one page consumer.)

Variants, each a distinct declared sentence in Python (house rule: user-facing sentences are
declared once, in Python, never typed into a template):

| State | Line |
|---|---|
| Window known, `< 70%` | `Context ~N of M tokens (P%) · estimate` |
| `70–89%` | the same line, band `high` |
| `>= 90%` | the same line, band `full`, plus *"Start a new conversation to keep the model's full attention."* |
| Window unknown (`window_source == "unknown"`) | `Context ~N tokens · estimate · no limit set for this connection` |
| No model bound (`check.resolved is None`) | `Context ~N tokens · estimate` — the existing `unavailable` banner already says why there is no ceiling; the meter does not repeat it |
| `truncated` is True | one extra clause: *"the oldest R of T messages are no longer sent"* |

The `truncated` clause is the owner-visible payoff of §2.1: it is the first place the platform
tells a reader that their long conversation is already being shortened.

**`window_source == "engine-default"`** additionally renders, **for an administrator only**
(render-vs-gate: the sentence names operator configuration), a link to the Models console:
*"No limit is set for this connection; the engine's own default applies."* A non-admin's render
never builds it.

### 3.5 Keeping it live

Rendered on every full page load, and refreshed by the existing poller at the two moments the
replayed corpus actually grows.

**The window never travels, and that is the fix for a real bug** (review M3).
`agents/chat/views/turns.py::turn_status` resolves `visible_turn(principal, turn_id)` and
dispatches on `turn.state`. It holds **no** conversation-level render context: no
`settings_row`, no `wall`, no `ToolAccess`, no `Preflight` — and critically **no picked
connection**, because the picker's selection lives in the thread page's `?connection=` query
string, which `_done_body(turn, request)` never sees. A poll body that computed its own
denominator would (a) have to run `preflight_turn` — a wall read, `model_access_for`,
`resolve_chat` and tool-access reads — on every tick of every open tab, which is the exact
inverse of §3.2's "the page pays nothing for it", and (b) answer with the **agent's role
binding** where the page answered with the **picked connection**, so the ceiling would silently
change mid-thread for anyone using the picker.

So it does neither. The poll body carries **three integers and nothing else**:

```python
"context": {"estimated_tokens": int, "replayed_turns": int, "total_turns": int}
```

computed from the same one flat `values_list` the page runs. The **window, the percentage band
and every sentence stay with the page**, which is the only place that knows the picked
connection.

**No prose is ever composed in JavaScript.** The line is rendered server-side as declared
sentences around two number spans and one pre-rendered, initially-hidden clause:

- `<span id="context-tokens">` — the token count, re-formatted from an integer;
- `<span id="context-percent">` — `round(100 * estimated / window)`, computed in the script from
  the integer it was sent and the `data-window` the **server** wrote onto the container (never a
  number the client supplied); absent entirely when there is no window, in which case the span
  is not rendered and the script leaves it alone;
- the truncation clause, server-rendered once and `hidden` until `total_turns > replayed_turns`,
  then unhidden. It is the one clause that can newly become true mid-session.

The script sets `textContent` on the two spans, toggles `hidden` on the clause, sets `data-band`
on the container, and sets a CSS custom property for the bar width. **No `innerHTML`**, matching
`showCardError`'s own rule for text the page did not build, and **no new `<script>` tag** — four
lines inside the existing poller, so the pinned counts of 4 and 3 are unchanged.

**One query, and it is one only because `visible_turn` is widened to make it so** (review R2).
`agents/visibility.py::visible_turn` returns
`Turn.objects.filter(...).select_related("conversation").first()` — **`conversation` alone**.
`context_usage(conversation, agent, …)` also reads `agent.system_prompt` and
`conversation.workstream.instructions`, neither of which is loaded on this path, so the budget
below would have been one flat read **plus two lazy FK reads on every tick**, and §9's pin would
have failed on its first run. The fix costs nothing anywhere: widen the existing call to

```python
.select_related("conversation", "conversation__agent", "conversation__workstream")
```

— two more JOINs on a query this path already runs, on a nullable FK Django resolves with a LEFT
JOIN. The thread page is unaffected: there both rows are genuinely already in hand. §9's
per-state query pin is what proves the `select_related` stays wide enough, so a later reader who
narrows it turns a test red rather than quietly adding two queries to every poll.

**Two bodies carry the key, not one** (review m7). `agents/chat/service.py::start_turn` writes
the USER turn with `state=Turn.State.DONE` in the same transaction as the QUEUED placeholder, so
the replayed corpus grows at **queue** time, not only at finish. A meter that waited for `done`
would be stale for the whole in-flight window — which is precisely when the reader is deciding
whether to compact, and is the same shape as the round-13 stale-strip lesson this section
invokes. So `_queued_body` and `_done_body` both carry `context`; `_running_body`,
`_failed_body` and `_cancelled_body` do not, because the corpus does not change between queue
and finish and a failed or cancelled turn adds no replayable text.

This is the one deliberate extension of the poller beyond a turn card. A test pins which bodies
carry the key, that the poll path's query delta is the budgeted one (§8), and that the script
count did not change.

### 3.6 Files

| File | Change |
|---|---|
| `agents/usage.py` | NEW. `CHARS_PER_TOKEN`, `estimate_tokens`, `ContextUsage`, `context_usage`, the declared sentences. |
| `models/contracts/bindings.py` | `effective_context_window(resolved)`. |
| `agents/chat/views/thread.py` | `thread_context` adds `"context_usage"`, built from `check.resolved`. |
| `agents/chat/views/turns.py` | `_queued_body` and `_done_body` add `"context"` (three integers). |
| `agents/visibility.py` | `visible_turn`'s `select_related` widened to `("conversation", "conversation__agent", "conversation__workstream")` — what makes §8's "exactly one" true rather than aspirational (review R2). |
| `agents/chat/templates/chat/conversation.html` | the line, the bar, the disclosure, the meter CSS in its own `chat_style` block, four lines in the existing poller. |

---

## 4. Feature B — the agent-edit utility

### 4.1 The shape, in one paragraph

**One writer set at the column root, one form-context builder, one template fragment, one edit
route and one create route — mounted from two list pages.** The list pages differ only in which
rows they list and who may open them. Nothing about editing an agent is written twice, which is
the whole of *"not an entangled mess"*.

```
agents/visibility.py         may_manage_agent / editable_agents / create_agent / update_agent
                             (the column's one owned-row read+write module -- where
                             create_conversation, rename_workstream and set_workstream_scope
                             already live)
agents/chat/agentform.py     agent_form_context(principal, agent=None, posted=None) -> dict
agents/chat/templates/chat/_agent_form.html      the ONE field form (§4.4)
foundation/templates/_transfer_panel.html        REUSED, unchanged -- the label editor (§4.3.1)
agents/chat/templates/chat/agents.html           user-facing list  (route A)
agents/chat/templates/chat/agents_admin.html     settings-area list (route S)
```

The label editor is **not** a new control: it is the transfer panel `/chat/access/` and
`/chat/tools/` already render, fed by `entitlement_panes` and posted through
`parse_entitlement_diff` — a third consumer of a shared fragment, which is reuse rather than a
fourth spelling of "this row carries entitlement ids".

`agents/chat/agentform.py` never touches a manager; it calls the visibility functions and
`identity.access.labelling_entitlements` / `models.contracts.roles.all_roles`, and returns
plain data. That is what makes it reusable: a third mount — a workstream panel, a future MCP
edge — includes the same fragment and posts to the same route.

### 4.2 Ownership: who may edit what

```python
def may_manage_agent(principal, agent, *, settings_row=None) -> bool:
    """Admin, or the row's own owner. A box-wide agent is the administrator's."""
    if is_admin(principal, settings_row=settings_row):
        return True
    if agent.box_wide:
        return False
    return may_read_owned_row(principal, agent)
```

- **Administrators may edit any agent.** That matches `labellable_agents`' own recorded reasoning
  for the sibling page (*class S already means every caller here is an administrator*), and it is
  what "the main chat should be owned by the admin" requires.
- **A user-created agent needs no administrator** — its owner edits it, exactly as the owner
  asked.
- **A box-wide agent is admin-only**, whoever originally created it.
- **Open posture degrades correctly with no branch:** `is_admin` answers True for everybody on a
  box with no accounts, so whoever is at the keyboard edits everything. That is the true
  statement about a household box, not a fallback.
- `may_read_owned_row` is identity's own row-predicate mirror of `owned_rows_q`; nothing here
  restates the shape of the two owner columns.

`editable_agents(principal)` is the user-facing list, and it takes **the column's standard
shape**, not a bare `owned_rows_q` filter (review M6):

```python
def editable_agents(principal, *, settings_row=None):
    qs = Agent.objects.exclude(box_wide=True).order_by("name")
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(owned_rows_q(principal, settings_row=settings_row))
```

Every sibling in `agents/visibility.py` — `visible_conversations`, `visible_agents`,
`visible_workstreams` — opens with exactly that short-circuit, and for exactly this reason:
`identity/access.py::owned_rows_q` has **no** open-posture widening (it is
`Q(owner_kind=…, owner_key=…) | Q(owner_kind="service")`) and it calls `is_admin`, which reads
the `IdentitySettings` singleton. Without the branch, three things go wrong at once on an open
box: a row stamped with a `user` principal (installed during an accounts-on period, or after an
owner reassignment) is **absent from the list while `may_manage_agent` answers `True` for it**,
so the list and the predicate disagree; the "zero permission queries on an open box" rule §6
quotes is false; and §4.5's "this is every non-box-wide agent" is false.

`settings_row=` is threaded, the same bounded edge every other reader in this module takes.

**On an accounts-on box an administrator's `/chat/agents/` shows their own rows only**, not
everybody's — `sees_all_content` is `is_admin AND admin_sees_content`, so with the content
setting off the admin branch does not fire. That is correct and deliberate: `/chat/agents/` is
"the agents I work on"; `/settings/agents/` is the box-wide view, and it is one click away.

The settings list is `labellable_agents()`'s existing unfiltered read, reused rather than
re-spelled.

### 4.3 Audience: the owner's three, mapped onto seams that exist

The owner named three audiences. Each maps to a seam already in the tree, except one, and the
gap is real rather than a preference:

| Owner's words | Mechanism | Exists? |
|---|---|---|
| "user specific" | owned, unlabelled, not box-wide → visible to its owner alone | **Yes** — `owned_rows_q` |
| "entitlement specific… all users of an entitlement may have access" | `AgentEntitlement` rows, written through `agents/labels.py::set_agent_labels` | **Yes** — and it already audits |
| "admin settings specific" / the main chat "for everyone" | every principal sees it | **Only via `resident=True`** |

`resident=True` is documented as an **origin marker, not a lock** — *"it records that this row
started life as a shipped default"* — and it has a second reader:
`agents/visibility.py::resident_agent_tool_keys` filters on it to warn, on the tool-label page,
that labelling a tool weakens the shell path. Overloading it to mean "everybody may see this"
would (a) make that warning fire for agents that were never shipped defaults and (b) make the
form's audience control lie about where a row came from.

**Decision: one new boolean, `Agent.box_wide`, and `visible_agents` swaps its `resident` leg for
it.** The data migration sets `box_wide = resident` for every existing row, so behaviour on the
day it lands is byte-identical. Afterwards:

- `resident` keeps its documented meaning and its one other reader, untouched;
- an administrator can make a shipped default private, or make an agent they wrote available to
  everybody, without either act being a statement about origin;
- `agents/defaults.py::install_default` stamps `box_wide=True` in its `_fields()` — the shipped
  catalogue **is** the platform's offer to everybody — and `--reset` re-stamps it along with
  every other field, consistent with that function's own *"a reset is a fresh adoption"* rule.

This completes the existing access system rather than paralleling it: one more leg on the one
`Q`, edited from one form, with the other two audiences going through the writers that already
exist. It is the only schema-shaped call in feature B and it is **flagged** (§13, flag 2).

### 4.3.1 The audience is TWO controls, not one exclusive radio

An earlier draft of this section offered one radio — *Just me* / *an entitlement* / *Everyone* —
whose chosen value was written as a **whole set** through `agents/labels.py::set_agent_labels`.
**That was a privilege hole, and the review is right** (M1).

`set_agent_labels` is a **raw writer**. It takes `actor` only to stamp the audit row; it never
consults `labelling_entitlements`, and its `remove(entitlement_id)` closure deletes any
`AgentEntitlement` row the diff names. The enforcement the draft attributed to it actually lives
one layer up, in `agents/chat/service.py::parse_entitlement_diff`, which
`agents/chat/views/access.py::_save` calls before writing — and that helper deliberately parses
an **add/remove operation over what is there now**, never a whole submitted set. Its own
docstring gives both reasons: *"a submitted whole set clobbers"*, and *"THE SAME PREDICATE THE
FORM RENDERS FROM … so a stale form or a hand-made request gets the identical honest refusal"*.
`access.py` then computes `wanted = before | submitted` (add) or `before - submitted` (remove).

The hole the whole-set shape opened: a member who owns an agent an administrator labelled with
*Legal* submits "Just me". `wanted = {}`, `set_agent_labels` deletes the *Legal* row, and because
`label_permitted_q` is AND-ed, the agent becomes visible to **everyone who previously could not
see it** — an audience widening performed by a principal holding no right to label with that
entitlement. A test for the **add** direction alone would never have caught it.

**The corrected design: two independent controls, each gated by its own authority.**

**Control 1 — reach.** One boolean, `box_wide`, on the main field form:

```
Reach
  ( ) The people I give it to        [box_wide = False; default for a new agent]
  ( ) Everyone on this box           [box_wide = True; administrators only]
```

Not rendered for a non-admin, and the POST re-checks `is_admin` rather than trusting the render.
It touches **no labels at all**, so it cannot widen anything past a label that is standing.

**Control 2 — entitlement labels.** The **existing shared transfer panel**
(`foundation/templates/_transfer_panel.html`, fed by
`agents/chat/service.py::entitlement_panes(choices, held)`), in its own `<details>`, with its own
submit — byte-identical in shape to what `/chat/access/` and `/chat/tools/` already render, and
posting through the identical path:

1. `parse_entitlement_diff(request, principal, settings_row, redirect_url=back)` → `(op, submitted)`,
   with `submitted <= {pk for pk, _ in labelling_entitlements(principal, settings_row=…)}` or an
   honest refusal;
2. `before = set(agent_label_ids(agent))`;
3. `wanted = before | submitted` if `op == "add"` else `before - submitted`;
4. `set_agent_labels(principal, agent, wanted, labelled_by=user_for_request(request))`.

**Why this closes the hole by construction.** A label the actor may not label with is never in
`choices`, therefore never in either pane, therefore never in `submitted` — so it survives both
directions untouched. `before - submitted` cannot remove it; `before | submitted` cannot add it.
The administrator's *Legal* label stands whatever the member does, and the member's own labels
are theirs to add and remove. This is the gate, and it is `parse_entitlement_diff`, **not**
`set_agent_labels`, which enforces nothing.

**On an open box** `labelling_entitlements` returns `()`, so control 2 renders nothing at all —
there is nothing to label with and nothing to show.

**The same construction that closes the leak opens a disclosure gap, and the gap is required**
(review R3). Because `entitlement_panes` builds *both* panes from `choices`, a member editing
their own agent that an administrator labelled *Legal* sees a panel with **no trace of that
label** — their agent is narrowed to *Legal* holders and the page they are standing on looks
unlabelled. The omission is not a bug to fix by rendering the name: the entitlement
non-disclosure rule is a **gate**.
`identity/tests/test_route_matrix.py::test_no_route_other_than_the_dormant_share_page_names_an_entitlement_to_a_non_holder`
walks every non-S route with a signed-in non-admin and asserts the name of an entitlement they
neither own nor hold appears in exactly one body on the box — the dormant-share 403 — and it
will begin sweeping `chat-agents`, `chat-agent-new` and `chat-agent-edit` the moment those names
enter `ROUTE_RULES`. Naming the label here would turn that test red, correctly.

**So the form says how many, never which**, through the house idiom rather than a new one:
`agents/visibility.py::name_for_viewer(missing_ids, viewer_principal, disclose_all=False)`, where
`missing_ids = agent_label_ids(agent) - {pk for pk, _ in labelling_entitlements(principal, …)}`
— the labels standing on this row that this actor cannot manage. Its default return is
`(named, unnamed_count)`: **named** only for entitlements this viewer already **holds** (no new
information — they hold it, they know its name, and the gate's own subject is an entitlement you
*neither own nor hold*), **counted** for the rest. One declared sentence, below the panel:

> This agent also carries 1 restriction set by an administrator, which you cannot change here.

and, when `named` is non-empty, the same sentence naming those and counting the remainder, in the
shape the dormant-share page already renders. **`disclose_all=True` is forbidden on this
surface** — its one caller is the dormant-share 403, where the reader holds a live `Share` row on
the stream and owner decision 8 asks for the names in so many words. Neither condition holds on
an agent form.

**The list pages carry the bare count only**, never `name_for_viewer`: `/chat/agents/` renders N
rows, and a per-row `held_entitlement_ids` read is the sidebar N+1 all over again. The count comes
from the page's one batch `agent_entitlement_ids()` read minus the page's one
`labelling_entitlements` set — flat, and it discloses nothing at all.

An administrator sees none of this, because `labelling_entitlements` offers them everything, so
`missing_ids` is empty and the sentence does not render.

**The two controls compose, and the form says what the combination means**, because
`visible_agents` is `(owned | box_wide | shared) AND label_permitted_q` and that is a truth
table, not an exclusive choice:

| `box_wide` | labels | Who can use it |
|---|---|---|
| False | none | its owner |
| False | *Legal* | its owner, if they hold *Legal* — the AND applies to an owner too |
| True | none | everybody on this box |
| True | *Legal* | everybody on this box who holds *Legal* |

**An administrator with `admin_sees_content` on sees every enabled row whatever the table says**,
because `visible_agents`' `sees_all_content` short-circuit returns before the label clause is
reached. Stated once here rather than parenthesised onto one row and not the others (review R6),
since it is a property of the short-circuit and not of any particular combination.

The last row is the useful one the earlier "they are exclusive" claim would have made
unreachable: a box-wide agent narrowed to a department.

### 4.3.2 The install route already creates box-wide rows, and that is pre-existing

`identity/routes.py` classifies `chat-default-install` as **"A"** — any signed-in principal —
and `agents/chat/views/defaults.py::default_install` checks `is_admin` **only** for a slug in
`SETTINGS_SURFACE_SLUGS`. An ordinary catalogue slug is installable by any member, and
`install_default` stamps `**owner_fields(principal)`, so the **member** owns the row (review M5).

Two things follow, and neither is introduced by this change:

- **The box-wide row already exists today.** `install_default` writes `resident=True` on create
  for whoever installs it, and `visible_agents`' `resident` leg already makes that row visible to
  everybody on the box. The `box_wide` migration sets `box_wide = resident` and swaps the leg, so
  a member's install produces exactly the row it produces today, reaching exactly the same people.
  The audience "bypass" is a pre-existing property of a class-A install route, not a hole this
  form opens.
- **`may_manage_agent` returns `False` for that member**, because `box_wide` short-circuits ahead
  of the ownership branch. That is the right answer — editing a row everybody on the box can use
  is an administrator's act — but the **list must not lie about it**. `/chat/agents/` therefore
  renders a second, read-only section, *"Agents everyone on this box can use"*, listing the
  box-wide rows this principal owns, with one declared sentence: *"You installed this from the
  shipped catalogue, so it is available to everyone here. An administrator can change it."*
  Without that section a member owns a row that is absent from the list and refused by the
  editor, with nothing anywhere explaining why.

`install_default` keeps stamping `box_wide=True` unconditionally, and **must**, because it is
also the shell path's writer — but the reason is not the one an earlier draft of this paragraph
gave (review R1). `manage.py install_defaults` does **not** run as a service principal. Its own
docstring says it runs as `OPEN_PRINCIPAL`, *"which it IMPORTS, NEVER CONSTRUCTS … ON PURPOSE AND
UNCONDITIONALLY — even once accounts are on"*, and an AST guard keeps it that way (the module is
deliberately absent from `_PRINCIPAL_CONSTRUCTORS` in
`foundation/ops/tests/test_import_law.py`). `OPEN_PRINCIPAL` and `SERVICE_PRINCIPAL` are
different values and the "never an admin" sentence belongs to the other one.

**The conclusion survives, by a different and narrower route.** `identity/access.py::is_admin`
returns `True` immediately when accounts are off; otherwise it calls `_user_row`, which returns
`None` for any `principal.kind != "user"`. `OPEN_PRINCIPAL` is `Principal("open", "box")`, so
`is_admin(OPEN_PRINCIPAL)` is `True` on an open box and **`False` on an accounts-on box**.
Stamping `box_wide=is_admin(principal)` would therefore leave the canonical shell install working
on an open box and **silently break it on an accounts-on one** — the posture where a shipped
default most needs to reach everybody, and the posture whose operator is least likely to be
watching a command's exit code for a visibility change.

**What that breakage would actually look like**, stated precisely because the earlier draft's
"nobody, administrator included, can see it" was right only by accident: the row would be
`box_wide=False`, `owner_kind="open"`. `owned_rows_q`'s second leg is `Q(owner_kind="service")`,
which an `"open"` row does not match either — so an administrator with `admin_sees_content`
**off** would not see it, while one with it **on** takes `visible_agents`' `sees_all_content`
short-circuit and sees everything. Invisible to the people it was installed for, and visible or
not to the administrator depending on an unrelated setting, is a worse failure than the one the
option was meant to fix.

The command's docstring also states, independently, the principle this whole section rests on:
*"a shipped default is the PLATFORM's offer, not the installing operator's private row"*, and it
records that `resident=True` rows are visible to every principal *"regardless of who owns them …
so which principal owns a resident row never decides who can see it."* `box_wide` inherits that
sentence unchanged. Whether to close the pre-existing route hole instead is **flag 8**.

**Per-person sharing of an agent is not built.** `Share.Target.AGENT` already exists and is
already read by `visible_agents`; giving it a writer is a small, separate feature and is named in
§14.

### 4.4 The editable fields

| Field | Control | Rule |
|---|---|---|
| `name` | text, required | ≤ 255; non-blank after strip |
| `description` | textarea | free |
| `system_prompt` | textarea | free; the one field that actually changes behaviour |
| `llm_role` | select | **administrators only** (§12 decision 7); options are `models.contracts.roles.all_roles()` filtered to `capability == "chat"`; a non-admin sees the current role's label as read-only text |
| `max_steps` | number | at least 1, at most a new `MAX_STEPS_CEILING` declared beside `MAX_STEPS_DEFAULT` in `agents/limits.py` — the model field is a bare `PositiveIntegerField` and no constant names a ceiling today, so the form may not invent one inline (review n2). Refused with a sentence outside the range |
| `enabled` | checkbox | the row stays readable by its past conversations either way (`Conversation.agent` is PROTECT) |
| *audience* | §4.3 | |
| `slug` | — | **immutable, never rendered as an input.** `Agent.save()` refuses a change; a form that offered one would be offering a refusal |
| `tool_keys` | — | **not editable in v1** (§14) |

**Slug on create.** The user never types one. `create_agent` derives it: `slugify(name)`,
truncated to 64, uniquified with a numeric suffix, checked case-insensitively against both
`Agent` (the `uniq_agent_slug_ci` constraint) **and** `agents/defaults.py`'s catalogue slugs —
because a row whose slug collides with a catalogue entry would be silently overwritten by
`install_defaults --reset <slug>`. A name that slugifies to nothing (punctuation only) falls
back to `agent-<n>`.

**Editing a `resident=True` row shows one sentence**, because it is true and nothing else says
it: *"This agent was installed from the shipped catalogue. Running `install_defaults --reset` on
it replaces everything below with the shipped text."*

### 4.5 The two mounts

**User-facing** — `/chat/agents/`, route class **A**, listed in the chat rail beside
Workstreams. Lists `editable_agents(principal)`; a "New agent" button; each row links to the
edit route carrying `?next=`. On an open box this is every non-box-wide agent, which is correct.

**Settings-area** — `/settings/agents/`, route class **S**, registered as
`Entry("Agent library", "settings-agents", ADMIN)` in `SETTINGS_GROUPS`' **Setup** group, beside
Models / Library / Chat / Job execution. **"Agent library", not "Agents"** (review n1): the
Access group two rows down already carries **Agent access**
(`chat-agent-entitlements`), and two entries sharing one noun for two different jobs is a nav an
operator has to learn rather than read. Lists every agent with its audience and owner; links to
the same edit route with its own `?next=`. Mounted by `config/urls.py` from
`agents/chat/agent_admin_urls.py` — **exactly the `assistant_urls.py` precedent**, whose own
docstring gives the reason: these are not `/chat/` routes, they are a settings-area surface whose
views happen to read `agents/` rows, and the composition root is the one module that already
imports every column.

The settings view carries its own `is_admin` check **in addition** to the class-S middleware gate,
for the reason `agents/chat/views/assistant.py` records for its own three views: the gate is not
the only way a view function can be reached.

**One edit route serves both.** `chat-agent-edit`, class **O** — admitted at the middleware,
404 in the view for a row `may_manage_agent` refuses, which is the definition of the O class.

**`?next=` follows the house pattern exactly, and the GET is never redirected to** (review m3).
`agents/chat/service.py::validated_next_url` reads `request.POST.get("next", "")` and nothing
else, so a `?next=` arriving on a GET link is **not** validated by it and a naive
`request.GET["next"]` redirect would be an open redirect off-box. So: the list link carries
`?next=`, the GET **echoes it into a hidden `next` field** and does nothing else with it, and the
**POST** validates through `validated_next_url`, falling back to the user-facing list when the
value is absent or off-origin.

### 4.6 Creation

`chat-agent-new`, class **A**. GET renders the same fragment with an empty `agent`; POST calls
`create_agent(principal, ...)`, which stamps `**owner_fields(principal)` (the creator owns it —
`create_conversation`'s own rule), derives the slug, defaults `llm_role` to `CHAT_CONVERSE_ROLE`,
`max_steps` to `MAX_STEPS_DEFAULT`, `tool_keys` to `[]`, `resident=False`, `box_wide=False`, and
writes one audit row.

**An agent with no tools is a useful agent** — a system prompt and a model is exactly what most
people want. That is why v1 can ship without tool granting.

`agents/apps.py::ready()` builds `agent.<slug>` tool specs from **code** (`DEFAULT_AGENTS`),
never from rows, so a user-created agent is not exposed as a delegate tool. That is an existing,
named deferral (`agents/README.md`'s P3-G1 note), restated here so nobody is surprised.

### 4.7 Audit

Two new actions in `identity/contracts/actions.py` (a pure module `agents/labels.py` already
imports): `AGENT_CREATED = "agent.created"`, `AGENT_EDITED = "agent.edited"`, added to the
registry tuple beside the four `agent.*`/`flow.*` labelling actions. `update_agent` writes one
row naming **which fields changed**, never their contents — a system prompt is the operator's
text, and an audit trail is not the place to copy it.

### 4.8 Files

| File | Change |
|---|---|
| `agents/models.py` | `Agent.box_wide` |
| `agents/migrations/00NN_agent_box_wide.py` | column + data migration (`box_wide = resident`) |
| `agents/visibility.py` | `may_manage_agent`, `editable_agents`, `create_agent`, `update_agent`; `visible_agents`/`installed_agent_slugs` swap the `resident` leg for `box_wide`. **No `set_agent_audience`** (review R5): reach is a plain `box_wide` field write on the main form and labels go through `parse_entitlement_diff` → `set_agent_labels`, so a single audience writer is exactly the shape §4.3.1 exists to avoid |
| `agents/defaults.py` | `install_default` stamps `box_wide=True` |
| `agents/limits.py` | `MAX_STEPS_CEILING`, beside `MAX_STEPS_DEFAULT` |
| `agents/chat/agentform.py` | NEW — the field-form context builder, plus `entitlement_panes` for the label editor |
| `agents/chat/views/agents.py` | NEW — list, new, edit (two POST paths: fields, and `parse_entitlement_diff` → `set_agent_labels`) |
| `agents/chat/views/agents_admin.py` | NEW — the settings-area list |
| `agents/chat/agent_admin_urls.py` | NEW — one route, the `assistant_urls.py` shape |
| `agents/chat/urls.py`, `config/urls.py` | three routes, one mount |
| `agents/chat/templates/chat/_agent_form.html`, `agents.html`, `agents_admin.html` | NEW |
| `foundation/settings_area.py` | one `Entry` |
| `identity/routes.py`, `identity/contracts/actions.py` | four route entries, two actions |

---

## 5. Feature C — edit a past prompt

### 5.1 The fork: BRANCH, recommended

**Recommendation: BRANCH.** Editing an earlier prompt creates a **new conversation** holding
everything before the edited message, with the edited message as its newest turn. The original
is untouched.

Six reasons, in order of weight:

1. **A rewind cannot honestly un-taint.** `ConversationTaint` is additive-only by design; its
   removal is a named deferred item with an open design question. A rewind that deleted the turn
   which brought labelled material into a conversation would leave the taint row standing with
   its `first_turn` pointing at a turn that no longer exists — so the answer to *"why did my
   share go dormant"* becomes a dangling integer. The alternative, deleting the taint row, is
   **precisely the laundering** the brief forbids: edit the tainted turn, the tag disappears, the
   share comes back. Branching sidesteps the whole question — the branch inherits the full tag
   set (§5.4) and the original keeps its own.
2. **A rewind destroys audit links.** `Turn.invocation` points at a `ToolInvocation` — an audit
   record of a call that really happened, by a named principal. Deleting the turn does not delete
   the audit row (`SET_NULL`), it orphans it. The column's recorded posture is that an audit row
   belongs to exactly one conversation and is never re-pointed or re-invented.
3. **A rewind would destroy somebody else's work, or need a gate nobody has designed.** A
   `use`-level share recipient may post (`may_post_to`), so a shared conversation holds turns
   written by more than one person. Under rewind, a recipient editing their own earlier message
   would delete every later turn — the owner's included — with no undo and no trace; gating
   rewind on ownership instead means the recipient cannot correct their own words at all, which
   is the same refusal §5.3 already lands on for branching but with no harmless alternative left
   to offer. There is no destructive multi-row path in this column today except
   `delete_conversation`, which is explicit and confirmed. Branching needs no such choice: §5.3
   refuses the recipient, and what they are refused is a *copy*, which costs them nothing they
   had.
4. **It orphans attachments.** `DocumentAttachment` rows are bound to `turn_id`. Deleting turns
   leaves rows whose turn is gone, on a strip and in a prompt block that group by turn.
5. **90% of it already exists and is already reviewed.** `duplicate_conversation` copies terminal
   turns, renumbers indexes, carries the workstream (ruling D), copies taint, and drops
   `invocation`/`queue_job_id` — each with its reasoning written down. Branching is that function
   with an index bound and a new last turn.
6. **It is reversible.** A branch you did not want is one delete away. A rewind you did not want
   is gone.

The cost of branching is **conversation clutter** — one new sidebar row per edit. §5.5 pays for
that with provenance rather than by destroying history.

**The fork is flagged** (§13, flag 3). If the owner chooses REWIND, it is not a variant of this
design: it needs a turn-deletion path, a taint-removal design (a deferred item with an open
question), a `DocumentAttachment` cleanup rule, and a rule for shared conversations. **Cost if
wrong:** shipping BRANCH when the owner wanted REWIND costs sidebar clutter, reversibly.
Shipping REWIND when the owner wanted history kept destroys data irreversibly. The asymmetry is
the recommendation.

### 5.2 What the reader does

On any of their own earlier messages, a `<details>` disclosure — *"Edit and carry on from here"*
— inside the user turn card. It opens a plain form: a textarea pre-filled with the message's
text, an optional file input, and one button, *"Send from here"*.

**The file input is `chat/_attach_files.html` alone, never `chat/_composer.html`** (review m8).
`_composer.html` ends with `{% if may_attach_files %}{% include "chat/_attach_dragdrop.html" %}
{% endif %}` and an unconditional `{% include "chat/_enter_to_send.html" %}` — **two script
blocks**, and this disclosure renders once per eligible user turn, so including the composer
would multiply the page's script count by the number of editable messages. `_attach_files.html`
is script-free: the plain `<input type="file">`, the placement chooser and the labels, with no
drag-and-drop and no enter-to-send. **No script is added**, so the pinned counts of 4 and 3
stand.

Above the textarea, one declared sentence, because it is true and the reader would otherwise
discover it the hard way:

> This starts a new conversation with everything before this message. The original stays as it
> is. Files attached earlier in this conversation are not carried over.

### 5.3 The gate

`may_edit_turn(principal, conversation, turn)` — **all** of:

- **`may_manage_conversation(principal, conversation)`** — owner or `sees_all_content`;
- `turn.role == "user"` and `turn.depth == 0` and `turn.state == "done"`;
- the turn belongs to this conversation;
- **no turn in this conversation is in a non-terminal state** — one flat `.exists()`. Editing
  while an answer is in flight would branch from a conversation whose shape is still changing,
  and the job would write its answer back to the original's row anyway.

**Manage, not post, and the difference is the whole of the finding** (review M2). An earlier
draft gated on `may_post_to`, which is strictly wider: owner, `sees_all_content`, a `use`-level
`Share.Target.CONVERSATION`, **any** `Share.Target.WORKSTREAM` recipient (a workstream share is
`use`-by-construction), and the stream's owner. Under that gate a share recipient could, with one
click on somebody else's message, mint a **new conversation they own** holding the original's
full terminal history — and §5.6's "shares are not carried" makes that worse, not better: the
copy is stamped with the brancher's `owner_fields`, survives revocation of the share that
permitted it, and the original owner can neither see, manage nor delete it.

`agents/visibility.py::duplicate_conversation` refuses exactly that today, on
`may_manage_conversation`, whose docstring is explicit that *"shared to somebody is not
manageable by them"*. A branch **is** a copy, so it answers to the copy predicate. Since
`may_manage_conversation ⊆ may_post_to`, the right to write the new turn comes along with it and
needs no second check.

The cost is real and is **flag 7**: a `use`-share or workstream-share recipient cannot edit and
branch in a conversation shared with them, even on their own message. That is the conservative
call, and the owner may want it widened — but widening it means deciding what a durable owned
copy of somebody else's thread is worth, which is a question, not an oversight.

The disclosure is rendered only where the predicate holds, and the POST re-checks it. A refused
POST is a 404 (class O), never a 500.

### 5.4 What a branch is made of

**Two functions, in two columns, and the import direction is why** (review M7). An earlier draft
numbered `start_turn` and a redirect as steps 3 and 4 *of* `branch_conversation`. That cannot be
built: the dependency runs `agents/chat` → `agents/visibility`, one way
(`agents/chat/service.py` imports `chat_surface_conversations`; `agents/visibility.py`'s own
import list is `agents.models`, `agents.shares` and `identity.*`, and nothing from `agents/chat`).
Calling `start_turn` from `branch_conversation` inverts that into a cycle, and a function-local
import would work mechanically only by putting the queue, preflight, attachment staging and the
`messages` framework behind a module the whole column treats as a leaf. A function that also
redirects is a view, not a visibility function.

So the work splits, the same way `agents/runtime/bindings.py`'s docstring states its own
direction:

- **`agents/visibility.py::branch_conversation(principal, conversation, turn, *, title)`** — a
  sibling of `duplicate_conversation`, sharing its constants and its rules, doing **steps 1–2
  only**, in one transaction, and returning the new conversation or `None`. It knows nothing
  about HTTP, the queue, or where the reader goes next.
- **`agents/chat/views/turns.py::turn_edit`** — the view. It validates the text, calls
  `branch_conversation`, then calls `start_turn`, then redirects: **steps 3–4**.

**`branch_conversation`, steps 1–2:**

1. Refuse unless `may_edit_turn`.
2. In one `transaction.atomic()`:
   - **The conversation.** `agent` and `workstream` carried (ruling D — a branch stays in the
     stream, or one click launders labelled material out of every gate), `**owner_fields(principal)`
     (the brancher owns it — `duplicate_conversation`'s own rule), the same `title`, and the two
     provenance columns of §5.5.
     `duplicate_conversation`'s recorded `admin_sees_content` consequence — an administrator's
     copy is theirs by ownership, and survives the content setting being switched back off —
     **applies here for the same reason it applies there**, now that §5.3 gates on the same
     predicate. An earlier draft claimed it applied "unchanged" while gating on the wider
     `may_post_to`, which was false: that consequence was written for a predicate that already
     required manage rights (review M2).
   - **The turns.** `Turn.objects.filter(conversation=conversation, index__lt=turn.index,
     state__in=_TERMINAL_TURN_STATES).order_by("index")`, bulk-created with indexes renumbered
     from zero. Copied: `role`, `text`, `tool_call`, `data`, `artifacts`, `depth`, `state`,
     `error` — and **`author_id`**, which `duplicate_conversation` does not copy.
     *That omission is a pre-existing gap, not a difference of opinion:* `prompt.py::
     _is_foreign_user_turn` reads `author` to fence another person's words in a replay, so a copy
     that drops it changes how the model is shown a shared conversation's history. **Both
     functions copy it after this change**, in the same commit, with a test, and the commit
     message says it re-pins `duplicate_conversation`'s behaviour (the house rule for a
     deliberate re-pin).
     **Not** copied, for the reasons `duplicate_conversation` records verbatim:
     `invocation`, `queue_job_id`.
   - **The taint.** Every `ConversationTaint` row of the parent, copied verbatim — **including
     tags whose `first_turn` lies after the branch point.** Over-tainting is safe; under-tainting
     is a leak, and the field is a plain integer precisely so it can name the turn that really
     caused it, in the conversation where it really happened. `duplicate_conversation` already
     copies `first_turn` across a conversation boundary the same way.
   - **No `WorkstreamTaint` write.** The branch stays in the parent's stream, and the stream's
     materialised union already holds every one of the parent's tags — it was unioned upward when
     each was stamped. Nothing new enters the stream.
   - **No `DocumentAttachment` rows.** There is no copier seam (§2.4), and inventing a fifth
     registered seam for v1 is not earned. §5.6 says what this costs and §14 names the follow-up.
**`turn_edit`, steps 3–4**, in the view, after `branch_conversation` returns:

3. `agents/chat/service.py::start_turn(new_conversation, edited_text,
   connection=<the picker's current selection>, actor=principal, files=<newly attached>)` — the
   one turn starter, so a branch's first turn gets the identical preflight, attachment staging,
   placement chooser and queue handling any other turn gets.
4. Redirect to the new conversation, carrying `?pending=<turn_id>`, so the no-JS path and the
   poller both behave exactly as they do after an ordinary send.

**Validate before creating.** `turn_edit` checks blank and over-length text against the same
`MAX_TURN_CHARS` constant `start_turn` uses **before it calls `branch_conversation` at all**, so
a refused edit writes nothing.
If `start_turn` still refuses afterwards (an unbound role, an unreachable engine), the branch
exists with its copied history and no answer — a state the thread page already renders honestly,
with its existing banner. That is accepted and stated rather than papered over.

### 5.5 Provenance

`Conversation` gains two nullable columns:

- `branched_from` — self-FK, `on_delete=SET_NULL`, `related_name="branches"`;
- `branched_at_index` — `PositiveIntegerField(null=True)`, the parent's index of the edited turn.

The thread page renders one line at the top of a branch: *"Branched from <title> at message N"*,
the title linking back when the parent is still visible to this principal (through
`visible_conversations` — never a bare pk read). With the parent deleted or invisible, the line
renders without a link and without the title.

**Corrected (2026-09-22, Task 14 review I1).** Read literally, that leaves a hole where the title
was — "Branched from at message N" — which shipped that way until the review caught it. What
ships now fills the gap with a declared, disclosure-free stand-in noun phrase, *"an earlier
conversation"*, so the line always reads as a whole sentence: *"Branched from an earlier
conversation at message N."*

**Amended (2026-09-22, whole-branch review I-2).** *"message N"* above conflated two different
numbers. `branched_at_index` is `Turn.index`, a dense counter over EVERY row in the thread —
assistant answers, tool cards and delegate turns included, and `0` for the very first one — so
the line read *"at message 0"* for a branch off the first message and *"at message 4"* for the
second message of a thread that had used one tool. The column is unchanged (it is provenance,
and queryable, which is what this section asked of it); what ships is a display-side ordinal
computed at render time — the 1-based position of that turn among the PARENT's finished
root-depth user messages, one bounded `.count()` — rendered as *"at your message N"*. Both
fallback paths (parent deleted, parent invisible) carry NO number at all, because an ordinal
counted over a thread the reader cannot open is one nobody can check: there the line is
*"Branched from an earlier conversation."* and nothing more.

**Why a migration rather than a title convention.** Two rows with the same name and no stated
relationship is exactly the sidebar an operator cannot explain to themselves a week later, and a
title suffix is a string nobody can query. Two nullable columns cost one migration and make the
relationship a fact. Flagged as flag 4 — a title-only v1 is a legitimate smaller call.

### 5.6 What a branch honestly loses

| Thing | Behaviour | Why |
|---|---|---|
| Attachments on copied turns | **Not carried.** The form says so before you press the button. | No copier seam; §14 names it |
| `document:<id>` references inside copied tool results | Render as titled links; resolving one from the branch refuses with the standard dead-reference sentence | The document is not attached to the branch |
| Files for the edited message itself | **Work.** The edit form carries the composer's own attach door, and `start_turn` stages them exactly as a normal send does | |
| Non-terminal turns | Not copied | `_TERMINAL_TURN_STATES`, verbatim from `duplicate_conversation` |
| Tool audit rows | Not copied, not re-pointed | An audit row belongs to one conversation |
| Pinned / archived state | Not carried — a branch is new, unpinned, unarchived | Pinning is a deliberate per-row act |
| Shares | **Not carried.** The branch is the brancher's own | A copy that inherited shares would extend somebody's reach without them asking |

### 5.7 Files

| File | Change |
|---|---|
| `agents/models.py` | `Conversation.branched_from`, `branched_at_index` |
| `agents/migrations/00NN_conversation_branch.py` | two nullable columns |
| `agents/visibility.py` | `may_edit_turn`, `branch_conversation`; `duplicate_conversation` copies `author_id` |
| `agents/chat/views/turns.py` | `turn_edit` — validate, `branch_conversation`, `start_turn`, redirect |
| `agents/chat/urls.py`, `identity/routes.py` | `chat-turn-edit`, class O |
| `agents/chat/rendering.py` | user cards carry `may_edit` |
| `agents/chat/templates/chat/_turn_card.html`, `conversation.html` | the disclosure, the provenance line |
| `agents/chat/templates/chat/base.html` | the disclosure's CSS |

---

## 6. Routes, postures and gating

New entries in `identity/routes.py::ROUTE_RULES` — **a name absent from that table is treated as
ADMIN and logged, and `test_route_matrix.py` fails immediately**, which is why every one is
classified here rather than discovered later:

| Name | Class | Reasoning |
|---|---|---|
| `chat-agents` | **A** | A signed-in person's own list; rows are narrowed in the view |
| `chat-agent-new` | **A** | Creation needs no row |
| `chat-agent-edit` | **O** | Row-addressed; 404 unless `may_manage_agent`. Not S — an S route would refuse a non-admin at the middleware, which is exactly the person this feature exists for |
| `settings-agents` | **S** | A page whose whole body is administrator-only listing — the call `chat-settings` and `chat-tool-entitlements` already record |
| `chat-turn-edit` | **O** | Row-addressed; the same class `chat-turn` and `chat-attachment-detach` carry |

**Posture behaviour, stated per posture:**

- **Open (no accounts).** `is_admin` and `sees_all_content` are True for the single principal.
  Every agent is editable, the entitlement transfer panel renders nothing at all
  (`labelling_entitlements` returns `()`), and the reach control offers "Everyone on this box"
  because the reader *is* the administrator. The context meter and the edit-a-prompt flow behave
  identically to any other posture. **`editable_agents` costs zero permission queries here**, and
  only because of the `sees_all_content` short-circuit §4.2 gives it — without that branch
  `owned_rows_q` calls `is_admin`, which reads the identity singleton, and the claim would be
  false (review M6). The list and `may_manage_agent` agree on an open box, which the
  `owned_rows_q`-only shape would not: a row stamped with a `user` principal is owned by nobody
  the open principal matches.
- **Personal / organisation.** `visible_agents`' non-cheap branch applies. A member sees and
  edits their own agents; entitlement options are the ones they own; "Everyone on this box" is
  neither rendered nor accepted. The settings list is admin-only at the gate.
- **Render-vs-gate throughout.** Admin-only data — the full agent list, the owner column, the
  "engine default applies" sentence, the box-wide radio — is **never built** on a non-admin
  render, not merely hidden by a template `{% if %}`.

**Never-500.** `agents/chat/tests/test_never_500.py` derives its route list from
`agents.chat.urls.urlpatterns` and raises `KeyError` for any name with no `_DRIVERS` entry, so
the three `/chat/` routes (`chat-agents`, `chat-agent-new`, `chat-agent-edit`) and
`chat-turn-edit` each need a driver added there, in the same commit. **`settings-agents` is not
in that sweep** (review m4): it is mounted from `agent_admin_urls.py` by `config/urls.py` — the
`assistant_urls.py` precedent — and is therefore outside `agents.chat.urls`, exactly as the three
`/settings/assistant/` routes are. Its never-500 proof lives in its own test module, named in §9.
`identity/tests/test_route_matrix.py` carries its own `test_every_route_has_a_driver` over
`ROUTE_RULES`, so **all five** new names need drivers there too.

Whichever sweep covers them, every new route answers 404 / 403 / a re-rendered form, never a
traceback — including a malformed `pk`, a `next` field pointing off-box, an agent whose
`llm_role` names an unregistered role, and a `turn_id` from another conversation.

---

## 7. Migrations

**Two**, both additive, neither back-filling content:

1. `agents/00NN_agent_box_wide` — `Agent.box_wide` (`BooleanField(default=False)`), plus a data
   migration setting `box_wide = resident` for every existing row. Behaviour on landing is
   byte-identical: `visible_agents` swaps one leg for a column holding the same truth.
2. `agents/00NN_conversation_branch` — `Conversation.branched_from` (self-FK, `SET_NULL`,
   nullable) and `branched_at_index` (nullable).

Feature A stores nothing and needs no migration; `MAX_STEPS_CEILING` (§4.4) is a constant, not a
column.

No container recreate; no new environment variable; no new setting row.

---

## 8. Query budget and script budget

**Scripts.** Zero new `<script>` tags. `test_thread.py`'s pinned counts (4 with the attach door,
3 without) are unchanged, and a test asserts that after every change in this spec. Feature A adds
four lines *inside* the existing poller; features B and C add none at all.

**Queries — the thread page.** Exactly **one** new query: `context_usage`'s flat
`values_list("role", "text")[:HISTORY_TURNS]`. It is constant in the conversation's length, which
is what the equality-under-scale pins in `test_thread.py` actually assert. The window comes from
`check.resolved`, already in hand, and the workstream row is already loaded on this render.
**`ChatSettings` is deliberately not read** — it is not read anywhere on a thread render today
(`time_aware_now_line()` reads the singleton inside `build_messages`, at turn time), so counting
the clock line would cost a second query here and another on every poll tick. §3.3 drops the
clock allowance for exactly that reason (review m2).

**Queries — the poll path.** Exactly **one** new query, the same flat `values_list`, and only on
the `queued` and `done` bodies (§3.5) — and it is one **because** `visible_turn`'s
`select_related` is widened to carry the agent and the workstream on the query it already runs
(§3.5, review R2). Without that widening it would be one query plus two lazy FK reads per tick. `_running_body`, `_failed_body` and `_cancelled_body` are
unchanged, so a long-running turn's every-two-second tick costs nothing new. **No `preflight_turn`
on this path** — no wall read, no `model_access_for`, no `resolve_chat`, no tool-access reads —
because the window never travels (review M3). A test pins `turn_status`' query count for each of
the five states.

**Queries — the agent list pages.** **Four**, not three (review R4): one for the rows, one for
the read-only "everyone on this box" section §4.3.2 adds (`box_wide=True` narrowed by
`owned_rows_q`), one for their label ids (`agents/labels.py::agent_entitlement_ids` reads every
row's labels in a single `values_list` — the discipline `/chat/access/` already documents), and
one for the entitlement names. All four are **flat in the number of agents**, the fourth section
included, and the per-row restriction count is a fold over the batch reads rather than a query of
its own (§4.3.1). Pinned by a test that renders one agent and twenty-five and asserts the same
count — the sidebar N+1 lesson, applied on the way in.

**Queries — the edit form.** One row read, one label read, plus `labelling_entitlements`' own.

**Queries — a branch.** One `.exists()` for the in-flight check, one ordered read of the turns to
copy, one `bulk_create`, one taint read, one taint `bulk_create` — all inside one transaction,
none per-turn.

---

## 9. Tests

Same commits as the code. The four runs (`vision,media` and `vision`, both collection orders)
plus the two posture sweeps, since this touches visibility.

**Feature A** (`agents/tests/test_usage.py`, `agents/chat/tests/test_thread.py`)
- `estimate_tokens` on empty, exact-multiple and remainder inputs; the divisor is read from the
  constant, never retyped.
- `context_usage` counts the system prompt, the stream instructions and the replayed turns — and
  **stops at `HISTORY_TURNS`**: a 25-turn conversation and a 40-turn conversation with the same
  last twenty produce the same estimate, and both report `truncated=True` with the right counts.
- `depth > 0` and non-`done` turns are excluded, matching `history_messages` exactly.
- **A tool turn's replayed tool-call block is not counted** (review M4): a conversation holding
  one tool turn with a large `tool_kwargs` payload estimates strictly below the characters
  `tool_turn_messages` would emit, and the test asserts the under-count **by name** so the
  omission is pinned as documented behaviour rather than discovered later as a bug. The same for
  a replayed assistant turn's fence and a foreign user turn's header.
- The clock line contributes nothing, with `time_aware` on and off (review m2): the estimate is
  identical either way, and `ChatSettings` is not read during the render.
- `effective_context_window`: operator value wins; unset yields the engine default with source
  `"engine-default"`; an engine with no declared default yields `(0, "unknown")` and the meter
  renders `percent=None` with no `%` anywhere in the body; a **non-integer** stored value
  degrades to `(0, "unknown")` rather than raising (review m10).
- A `NO_TOOL_CALLING` refusal — `check.ok` False with a **real** `resolved` — still renders a
  ceiling; only `check.resolved is None` renders the ceiling-free line (review m1).
- Bands at 69/70/89/90 percent.
- The page renders the line, the disclosure and the bar; the truncation clause is present in the
  markup but `hidden` when not truncated, and unhidden when it is; the "engine default applies"
  sentence appears for an administrator and **is absent from a member's response body**.
- `_queued_body` and `_done_body` carry `"context"` with **three integers and no window, no
  percentage and no sentence**; `_running_body`, `_failed_body` and `_cancelled_body` carry no
  `"context"` key at all (review M3, m7).
- `turn_status`' query count is pinned per state, and the `queued`/`done` delta is exactly the one
  flat read §8 budgets — with an explicit assertion that **no binding is resolved** on that path
  (no `preflight_turn`, no `resolve_chat`). **This pin is what proves `visible_turn`'s
  `select_related` is wide enough** (review R2): narrowing it back to `("conversation",)` adds two
  lazy FK reads per tick and turns this test red, rather than quietly costing every open tab two
  queries every two seconds.
- A conversation polled with the picker pointing at a non-default connection: the ceiling in the
  page and the ceiling after a poll tick are the same number (the regression M3 names).
- The script count is still 4 (door present) and 3 (door absent).
- Query-count equality between a short and a long conversation.
- CSS ownership stays green with the meter's selectors in `conversation.html`'s `chat_style`.

**Feature B** (`agents/tests/test_agent_authoring.py`, `agents/chat/tests/test_agent_pages.py`)
- `may_manage_agent`: owner yes; stranger no; admin yes; a `box_wide` row refuses a non-admin
  owner; open posture yes for everybody.
- `create_agent` stamps owner fields, derives a unique slug, refuses a catalogue-colliding slug
  by uniquifying, handles a punctuation-only name, and refuses a blank name.
- `update_agent` **cannot** change the slug — the attempt is ignored, not a 500, and `Agent.save`'s
  own refusal is not reached because the form never offers one.
- **No entitlement name a non-holder can neither own nor hold appears in `/chat/agents/`,
  `/chat/agents/new/` or `/chat/agents/<pk>/`** — asserted **directly**, in this column's own
  tests, rather than left to `identity/tests/test_route_matrix.py`'s cross-cutting sweep to catch
  later (review R3). The positive half beside it: a member whose own agent carries an
  administrator's label sees the counted sentence, with the right count, and a member who *holds*
  one of those entitlements sees it named — the `name_for_viewer(…, disclose_all=False)` split.
  An administrator sees no such sentence at all, `missing_ids` being empty for them.
- The list pages render a bare count and never call `name_for_viewer`, pinned by the same
  flat-query test at 1 and 25 agents (review R3, R4).
- **The audience write, both directions** (review M1). The headline test: an administrator labels
  a member-owned agent with *Legal*; the member — who does not hold *Legal* — opens the editor,
  and the panel offers *Legal* in **neither** pane; a forged `remove=<Legal>` POST is refused by
  `parse_entitlement_diff` with its own sentence and **the `AgentEntitlement` row survives**; a
  forged `add=<Legal>` is refused identically. Then: the member adds and removes an entitlement
  they *do* own, and *Legal* is untouched by both writes.
- A stale form is harmless: `op="remove"` naming an entitlement already gone is a no-op with no
  audit row, and `op="add"` naming one already present likewise — the diff is the point.
- Two concurrent edits to different labels on the same agent do not clobber each other (the
  add/remove shape's own reason for existing).
- The reach control: "Everyone on this box" sets `box_wide`, is **refused for a non-admin POST**
  even with the field forged, and **touches no labels**; setting it on a labelled agent leaves the
  labels standing, and the truth table in §4.3.1 holds for all four rows of
  `visible_agents`.
- **The install interaction** (review M5): a member POSTs `chat-default-install` for an ordinary
  catalogue slug; the row is `box_wide=True` and owned by that member; `may_manage_agent` answers
  `False` for them; `/chat/agents/` renders it in the read-only "everyone on this box" section
  with its declared sentence, so it is never a row that is owned, absent and refused with no
  explanation. Pinned against the pre-migration behaviour: the same POST today produces a
  `resident=True` row that `visible_agents` already shows to everybody.
- `visible_agents` after the migration: a `box_wide` row reaches everybody; a private row reaches
  its owner only; a labelled row reaches only holders; the label AND still restricts a box-wide
  row.
- `installed_agent_slugs` moves with it.
- **`editable_agents` on an open box** (review M6): a row stamped with a `user` principal is in
  the list **and** `may_manage_agent` answers True for it — list and predicate agree — and the
  call costs **zero** permission queries. On an accounts-on box with `admin_sees_content` off, an
  administrator's `/chat/agents/` holds their own rows only, which is pinned as the intended
  answer rather than left to be read as a bug.
- `/settings/agents/` is 403 for a member at the gate and lists every row for an admin;
  `/chat/agents/` lists only the principal's own.
- A GET carrying `?next=<off-box>` **renders** it into the hidden field and redirects nowhere; the
  POST then refuses it through `validated_next_url` and falls back to the list (review m3).
- Query counts flat at 1 and 25 agents.
- `foundation/tests/test_shell.py`'s settings drift test sees the new entry, under the name
  **Agent library** (review n1).
- `identity/tests/test_route_matrix.py` classifies all five new names, and its own
  `test_every_route_has_a_driver` gets drivers for all five.
- `agents/chat/tests/test_never_500.py`'s `_DRIVERS` gains the three `/chat/` agent routes; a new
  `agents/chat/tests/test_settings_agents.py` carries `settings-agents`' own never-500 proof,
  since `config/urls.py` mounts it outside `agents.chat.urls` and the chat sweep cannot see it
  (review m4).
- `max_steps` is refused at 0 and at `MAX_STEPS_CEILING + 1`, and the form reads the constant
  rather than a literal (review n2).

**Feature C** (`agents/tests/test_branch.py`, `agents/chat/tests/test_turn_edit.py`)
- `may_edit_turn`: own user turn yes; an assistant turn no; a `depth>0` turn no; a non-terminal
  turn no; **any** in-flight turn in the conversation blocks every edit.
- **The share answers, pinned explicitly** (review M2): a `view`-share recipient **no**, a
  `use`-share recipient **no**, a workstream-share recipient **no**, the stream's owner **yes**
  (they own it), an administrator with `admin_sees_content` **yes**. A forged POST from each
  refused principal is a 404 and writes nothing — so no share recipient can mint a durable owned
  copy that outlives revocation.
- `branch_conversation` is callable with no HTTP anywhere in reach, and `agents/visibility.py`
  imports nothing from `agents/chat` (review M7) — the import-law gate covers the general rule;
  this pins the specific direction the split exists to preserve.
- `branch_conversation` copies turns strictly before the edited index, renumbered from zero, with
  `author_id` preserved; drops `invocation` and `queue_job_id`; carries the workstream; copies
  every taint row including one whose `first_turn` is after the branch point; writes no
  `WorkstreamTaint`; copies no `DocumentAttachment`; carries no shares; leaves the original
  byte-identical (turn count, indexes, taint rows, pinned/archived state).
- `duplicate_conversation` now copies `author_id` too (the deliberate re-pin).
- A blank or over-length edit writes nothing at all — no conversation row, no turns.
- The POST redirects to the new conversation with `?pending=`, and `turn_edit` — not
  `branch_conversation` — is what calls `start_turn` and what redirects.
- A `start_turn` refusal after a successful branch leaves the branch readable with its banner and
  no answer, never a 500 and never a half-written conversation.
- The edit form includes `chat/_attach_files.html` and **not** `chat/_attach_dragdrop.html` or
  `chat/_enter_to_send.html`; a thread with ten editable messages still renders 4 script tags
  (review m8).
- The edit disclosure renders only where the predicate holds, and the script count is unchanged.
- A branch of a branch works and reports the right parent.
- Deleting the parent leaves the branch readable with an unlinked provenance line.
- Never-500 on a forged `turn_id` from another conversation.

**Gates that must stay green:** import law, column boundaries (no new `agents/chat` module
touches `Agent`/`Conversation`/`Flow` `.objects` — `Turn` is not on that list, review m5), CSS
ownership (the meter's selectors in `conversation.html`'s own `chat_style`, review m6), route
matrix, script budget, no absolute paths, no model names in prose.

---

## 10. Documentation

Same commits as the code.

- `agents/chat/README.md` — a "What the context line means" section (the estimate, the
  twenty-turn window, what is excluded); the agents pages and the two mounts; the edit-and-branch
  flow and exactly what it does not carry.
- `agents/README.md` — `agents/usage.py`'s place in the column; the `box_wide` column's meaning
  and its relationship to `resident`; `branch_conversation` beside `duplicate_conversation`.
- `models/registry/README.md` — that `ModelConnection.context_window` is now **read for display**
  as well as sent; that no engine probe was added; and the reconciliation with
  `tools/rag/views.py::_resolved_answer_context_window`, which answers "unknown" where the meter
  answers with the adapter's default, why the two differ, and that they ask about different
  bindings (review m10).
- `docs/EXTENDING.md` — how a new settings-area page registers (the `Entry` + its own urls module
  mounted by the composition root), since feature B is the second instance of that pattern.
- `docs/adr/` — one ADR when the branch lands, recording three decisions: the operative window as
  the honest denominator (amending ADR 0010's context-window entry with a *read* direction),
  `box_wide` as the audience column distinct from `resident` as the origin marker, and BRANCH over
  REWIND with the taint reasoning.
- No model or vendor names; no absolute paths.

---

## 11. Phasing and done-when

Three phases, the owner's order, each independently mergeable.

**Phase A — context usage.** *Done when:*
1. The four runs green in both flag states and both collection orders.
2. On the branch's own preview stack, in a browser: a fresh conversation shows a small number and
   a low percentage; sending several long messages moves both **without a page reload** (the
   poller path); the disclosure opens and reads correctly.
3. A conversation of more than twenty turns shows the truncation clause, and its estimate stops
   growing once the window is full — **the proof that the meter measures what is sent**.
4. With `ModelConnection.context_window` set on the bound connection, the denominator changes to
   that number on reload; with it cleared, the line reports that the engine's default applies —
   and that sentence is absent from a member's page source.
5. With no chat role bound, the page still renders 200 with the existing banner and a ceiling-free
   line.
6. The page's HTML carries the same number of `<script>` tags as before the change.

**Phase B — the agent-edit utility.** *Done when:*
1. The four runs plus the two posture sweeps are green.
2. In a browser on the preview stack, as an administrator: `/settings/agents/` lists every agent;
   the shipped chat agent opens, its warning about `--reset` is there, its system prompt is
   edited and saved, and the change is visible in a new conversation's answer.
3. As a member: `/chat/agents/` lists only their own; "New agent" creates one; it appears in the
   chat picker; `/settings/agents/` is refused.
4. As a member: setting the audience to an entitlement they own makes the agent appear for a
   second member holding it and not for a third who does not.
5. "Everyone on this box" is offered to the administrator and **not present in the member's page
   source**.
6. On an open box, every agent is editable and the entitlement control does not render.
7. Every existing conversation still opens, and its agent still answers, after the migration.

**Phase C — edit a past prompt.** *Done when:*
1. The four runs plus the two posture sweeps are green.
2. In a browser: an earlier message is edited; a new conversation appears holding everything
   before it plus the edited message; the answer streams into it; the **original is opened again
   and is unchanged**.
3. The branch carries its workstream and its provenance line, and the line links back.
4. A branch of a conversation with a tool call shows the tool cards and no audit link.
5. An edit is not offered while a turn is running, and a forged POST in that state is refused.
6. The script count is unchanged.

Then: current `main` merged into the branch and resolved there, the whole feature walked end to
end by hand, whole-branch review, and the owner's merge word. **No success language before the
last rung.**

---

## 12. Decisions the author made

1. **The denominator is the operative window, never a model's architecture maximum, and no engine
   is probed for it.** Showing a ceiling larger than what the engine is asked to allocate would
   mislead in the dangerous direction, and ADR 0010's own fix sentence forbids the probe.
2. **The meter measures the replayed window, not the whole conversation**, and says so. The owner's
   sentence assumes everything is sent; the platform sends twenty turns. Reporting the
   conversation's whole size as "what this chat is using" would be false.
3. **Characters over four, disclosed**, rather than a new tokenizer dependency or a per-render
   engine call.
4. **One estimator, one call site, no per-turn stamp in v1** — `HISTORY_TURNS`' own docstring
   warns against a second, drifting counter.
5. **The poller updates the meter.** One `textContent` write inside the existing script, no new
   tag. Round 13's stale-strip incident is the precedent for *not* leaving it to the next reload.
6. **`box_wide` is a new column rather than an overload of `resident`.** `resident` is documented
   as an origin marker and has a second reader that would start warning about rows that were never
   shipped defaults.
7. **`llm_role` is administrator-only.** Which model backs an agent is box policy — the same call
   `Chat` and `Job execution` already record for being ADMIN rather than per-person. A non-admin
   owner sees the current role as text.
8. **`tool_keys` is not editable in v1.** Granting tools is a privilege question (the
   `mutates=True` refusal in `Agent.save`, the entitlement checks in the planner), not a form
   field, and an agent with a prompt and a model is already useful.
9. **Administrators may edit any agent**, consistent with `/chat/access/`'s own recorded reasoning
   for listing every row unfiltered.
10. **One edit route, two list pages.** The mounts differ in what they list and who may open them;
    nothing about editing is written twice.
11. **BRANCH over REWIND**, for the six reasons in §5.1, with the asymmetry of being wrong as the
    decider.
12. **A branch inherits the parent's full taint set**, including tags caused after the branch
    point. Over-tainting is safe; under-tainting is a leak.
13. **A branch carries no attachments**, matching `duplicate_conversation` exactly, and the form
    says so before the button.
14. **`branch_conversation` is a sibling of `duplicate_conversation`, not a parameter on it.** The
    two answer different questions (copy the whole thing / carry on from here) and have different
    rules about the last turn and about the index bound. They now share the **same** predicate
    (`may_manage_conversation`, §5.3 as amended), which is an argument for the sibling shape
    rather than against it: one gate, two operations, neither reaching into the other's body.
    Sharing the constants and the reasoning is the right amount of sharing.
15. **`duplicate_conversation` starts copying `author_id`** in the same commit — a pre-existing
    gap that this feature's tests would otherwise have to work around, re-pinned by name in the
    commit message.
16. **Two provenance columns rather than a title suffix.** A relationship nobody can query is not
    a relationship.
17. **The settings mount follows `assistant_urls.py`** — its own module, mounted by the
    composition root — rather than more entries in the chat URLconf, so the URL an operator sees
    matches the area they are in.
18. **No new capability for per-person agent sharing**, even though `Share.Target.AGENT` is read
    already. It is a separate feature with its own UI question.

Added by the 2026-09-21 amendment:

19. **Bands, a colour bar and a "start a new conversation" clause go beyond the owner's "a value
    and % of total"** (review n3). Kept, because a percentage nobody notices crossing 90% does not
    answer "so the user can understand when to compact" — the threshold is the actionable part,
    and the bar is how a percentage is read at a glance. They are additive to what the owner
    asked for, never a substitute: the value and the percentage are both rendered as text.
    Recorded here rather than flagged, because removing them is a one-line change to a declared
    sentence.
20. **The audience is two controls, not one.** A single exclusive radio cannot be written safely:
    the reach boolean answers to the actor's own authority, and a label answers to whether the
    actor may label with *that* entitlement. One control writing both would either clobber labels
    it may not touch (the M1 hole) or refuse edits it should allow.
21. **The window never travels to the poll endpoint.** The poll body carries three integers; the
    page owns the denominator, the bands and every sentence. The alternative the review suggested
    — sending the page's window back — works, but it makes the server render a number the client
    supplied, and it puts prose-bearing state on a path that has no business holding it.
22. **No prose is composed in JavaScript.** Two number spans and one pre-rendered hidden clause,
    so the house rule "user-facing sentences are declared once, in Python" survives a live meter.
23. **`install_default` keeps stamping `box_wide=True` unconditionally**, because the shell path
    runs as `OPEN_PRINCIPAL` (its own docstring, AST-guarded) and `is_admin(OPEN_PRINCIPAL)` is
    `False` on an **accounts-on** box — `_user_row` answers `None` for any `kind != "user"`. So
    stamping by the installer's authority would leave `manage.py install_defaults` working on an
    open box and silently break it in the one posture where a shipped default most needs to reach
    everybody. Not, as an earlier draft of this decision had it, because the command runs as a
    service principal — it does not (review R1).
24. **The clock line is not counted**, to hold the thread page and the poll path to one new query
    each. It is the smallest term in the estimate and the most expensive to buy.

---

## 13. Flagged for the owner

**Flag 1 — the meter measures the last twenty messages, not the whole conversation.**
The platform already shortens a long conversation before sending it. The meter therefore
plateaus, and says so ("the oldest 40 of 60 messages are no longer sent"). If the owner expected
"how big is this whole chat", that is a *different* number and a different product decision —
and it is the one compaction will actually act on. **Cost if wrong:** the meter answers a
question the owner did not ask, and the compaction feature inherits the wrong input. This is the
one flag worth answering before Phase A is built.

**Flag 2 — `Agent.box_wide`, a new column.** The only schema-shaped call in feature B. The
alternative is to overload `resident`, which is documented as an origin marker and would make the
tool-label page warn about rows that were never shipped defaults. **Cost if wrong:** one
migration and one column to remove.

**Flag 3 — BRANCH versus REWIND.** Recommended BRANCH, for §5.1's six reasons. REWIND is not a
variant of this design: it needs a turn-deletion path, a taint-removal design (an existing
deferred item with an open question), an attachment cleanup rule, and a rule for shared
conversations. **Cost if wrong:** BRANCH-when-REWIND-was-wanted costs reversible clutter;
REWIND-when-BRANCH-was-wanted destroys data irreversibly.

**Flag 4 — branch provenance columns.** `branched_from` / `branched_at_index` cost one migration
and make "where did this conversation come from" a queryable fact. A title-suffix-only v1 is a
legitimate smaller call. **Cost if wrong:** two nullable columns nothing reads.

**Flag 5 — `llm_role` administrator-only.** If the owner wants a member to choose which model
backs their own agent, that is one predicate change. **Cost if wrong:** a member cannot change a
field on a form they otherwise own.

**Flag 6 — attachments do not follow a branch.** The honest v1, matching duplication. If the
owner wants them carried, that is a fifth registered attachment seam (a copier) in the RAG column
— a real, separate piece of work. **Cost if wrong:** editing a message that referred to an
earlier upload produces a branch whose model cannot see that file.

Added by the 2026-09-21 amendment:

**Flag 7 — a share recipient cannot edit-and-branch.** §5.3 gates on `may_manage_conversation`,
so a `use`-share or workstream-share recipient cannot edit even their own message in a
conversation shared with them. The conservative call, matching `duplicate_conversation` exactly.
Widening it means accepting that a recipient can mint a conversation **they own** holding the
original's full history, which survives revocation of the share that permitted it and which the
original owner can neither see nor delete. **Cost if wrong:** a collaborator in a shared thread
has to copy-paste their message into a new conversation by hand.

**Flag 8 — a member can already create a box-wide agent, and this spec does not close it.**
`chat-default-install` is class A, and `install_default` writes `resident=True` (after the
migration, `box_wide=True`) whoever installs it — so a member installing a shipped default today
already creates a row everybody on the box can use. The migration preserves that exactly; the
spec names the consequence and makes it legible (§4.3.2) rather than changing it. Closing it
means gating the install route on `is_admin` for every slug, which would take an existing
member-facing button away. **Cost if wrong:** a member can put an agent in front of the whole box
without an administrator — which is today's behaviour, now visible in a form that talks about
audiences.

---

## 14. Out of scope, named

So nobody builds them by accident, and so the follow-ups are recorded rather than rediscovered:

- **Chat compaction.** Feature A is its foundation and nothing more. `context_usage` is the input
  it will consume; no summarizing, no truncation policy, no compaction UI here.
- **Stamping what was really sent onto each turn.** If compaction wants an audit trail of the
  actual prompt size, the loop is the place to record it — it holds the real message list. A
  follow-up, not a second counter.
- **A tokenizer.** No new dependency for a status line.
- **Reading an engine's reported context length.** Deliberately not built (§3.2).
- **Granting tools from the agent form.** A privilege question, not a form field.
- **Exposing a user-created agent as an `agent.<slug>` delegate tool.** An existing named
  deferral: `ready()` builds specs from code and may not touch the database.
- **A writer for `Share.Target.AGENT`** (per-person agent sharing). The target exists and is read;
  giving it a UI is its own feature.
- **An agent marketplace, import/export, or duplication of agents.**
- **Flow editing.** `Flow` carries the same owner columns and the same label seam, and
  `/chat/access/` already labels flows — but flows have a step editor's worth of design behind
  them and are not this change.
- **Copying attachments into a branch** (the fifth attachment seam).
- **Editing an assistant turn**, regenerating a turn in place, or any other destructive turn edit.
- **A branch tree UI**, grouping branches in the sidebar, or merging branches.
- **Changing `HISTORY_TURNS`.** The meter reports what the constant does; it does not argue with
  it.

---

## 15. Review response

This spec has been through two review rounds. Both are recorded here: round 1's 21 findings and
the three places this author answered them differently, and round 2's verdict on those three plus
its six residuals.

### 15.1 Round 1 — AMEND, 7 Major / 10 Minor / 4 Nit

Every finding applied. Three applied as an **alternative** to the edit the review proposed,
stated below so the re-check could adjudicate rather than re-derive them.

**All 21 findings, and where each landed**

| # | Landed in |
|---|---|
| M1 audience whole-set write | §4.3.1 (rewritten), §4.1, §4.8, §9 |
| M2 branch gated on `may_post_to` | §5.3, §5.4, §9, flag 7 |
| M3 poll path has no connection/budget | §3.5 (rewritten), §8, §9, decisions 21–22 |
| M4 "exactly the corpus" false | §3.3, §3.4 disclosure, §9 |
| M5 `chat-default-install` is class A | §4.3.2, §9, flag 8, decision 23 |
| M6 `editable_agents` open branch | §4.2, §6, §9 |
| M7 `branch_conversation` → `start_turn` | §5.4 (split), §5.7, §9 |
| m1 `check.ok` vs `check.resolved` | §3.2, §9 |
| m2 `ChatSettings` not read on render | §3.3, §8, decision 24 |
| m3 `validated_next_url` is POST-only | §4.5, §6, §9 |
| m4 never-500 sweep misses `settings-agents` | §6, §9 |
| m5 `Turn` is not on the boundary list | §3.1, §9 |
| m6 meter CSS placement | §3.4, §3.6, §9 |
| m7 corpus grows at queue time | §3.5, §9 |
| m8 composer carries two scripts | §5.2, §9 |
| m9 tool turns are never timestamped | §3.3 (moot — allowance dropped) |
| m10 duplicate context-window reader | §3.2, §10, §9 |
| n1 two settings entries, one noun | §4.5 |
| n2 invented `max_steps` ceiling | §4.4, §4.8, §9 |
| n3 bands/bar/clause unrecorded | decision 19 |
| n4 "three migrations" | §7 |

**Where this amendment did something other than what the review proposed**

1. **M3 — the poll body.** The review proposed that *"the poller sends the page's own
   `window`/`window_source` back (or the page's rendered `data-window` is reused client-side)"*.
   This amendment takes the second half only: the window is **never sent to the server**, and the
   poll body carries three integers with no window, no percentage and no sentence. Sending it
   back would make `turn_status` render a declared sentence around a number the **client**
   supplied — harmless in effect (the response has no other reader) but a shape this repository
   does not otherwise take, and it would put the denominator on a path that cannot verify it.
   Keeping the window entirely page-side also makes the M3 correctness bug unreachable by
   construction rather than by agreement between two call sites. The cost is that the band clause
   and the "no limit set" clause do not refresh mid-session; the truncation clause, the only one
   that can newly become true, is pre-rendered and unhidden instead (§3.5).

2. **M5 — the install route.** The review offered three options: gate the route on `is_admin`
   when `box_wide` would be stamped; stamp `box_wide=is_admin(principal)`; or accept it and say
   why, with a flag. This amendment takes the **third**, and rejects the second on a fact the
   review's own options did not weigh: stamping by the installer's authority breaks the canonical
   shell install. The first option would remove a member-facing button that works today. The
   third is taken **with an addition the review did not ask for**: the `/chat/agents/` read-only
   "everyone on this box" section (§4.3.2), because M5's second half — a member owning a row that
   is absent from their list and refused by the editor — is a real defect even though the audience
   half is pre-existing.

   **Corrected by round 2 (R1).** The round-1 wording of this item, and of §4.3.2 and decision 23,
   justified rejecting option 2 by asserting that `manage.py install_defaults` runs as a
   **service** principal. It does not: it runs as `OPEN_PRINCIPAL`, unconditionally and by AST
   guard, and its own docstring says so. The conclusion holds for a narrower reason —
   `is_admin(OPEN_PRINCIPAL)` is `True` on an open box but `False` on an accounts-on one, because
   `_user_row` answers `None` for any `kind != "user"` — which scopes the breakage to exactly the
   posture where it matters most. All three sites are rewritten to that reasoning. A wrong premise
   under a right conclusion is still a wrong premise, and it would have been copied forward by
   whoever read this next.

3. **M4 — the disclosure.** The review asked for *"one clause covering tool calls"*. The
   disclosure gained that clause, and §3.3 gained **five** named exclusions rather than the three
   the review listed, because the same reading found that the clock allowance (m2/m9) had to go
   too; dropping it removes one exclusion's worth of arithmetic and adds another's worth of
   disclosure. The net is a longer exclusion list and a shorter estimator.

**Nothing was skipped**, and no finding was judged wrong. M1's consequence in particular was
re-derived against the tree before rewriting: `set_agent_labels`' `remove` closure,
`parse_entitlement_diff`'s `wanted <= offered` check, and `access.py::_save`'s
`before | submitted` / `before - submitted` were each read again, and the review's account of all
three is accurate.

### 15.2 Round 2 — AMEND (residual), 0 Major / 3 Minor / 3 Nit

**All three alternatives accepted.** M3's page-side window was judged *"better than what the
review proposed"* — keeping the denominator off the poll path makes the drift bug unreachable
rather than agreed between two call sites. M4's five exclusions and the by-name under-count pin
were judged *"the stronger answer to an honesty finding"*. M5's accept-and-flag was accepted as
the right call with the pre-existence claim **verified in the tree**, and only its stated reason
rejected (R1).

**The audience mechanism was re-probed and found leak-free by construction, not by test**, on
four escape routes: control 2 on a box-wide row (unreachable — both POST paths are on
`chat-agent-edit`, class O, 404 unless `may_manage_agent`, which short-circuits `False` on
`box_wide` for a non-admin); control 1 forged by a non-admin (re-checked at the POST, and it
writes no labels); creation (§4.6 hard-codes `box_wide=False`); and holding-versus-owning
(`labelling_entitlements` filters on `owned_entitlement_ids`, so merely *holding* an entitlement
does not let a member remove it — stronger than the review asked for). The non-exclusive truth
table was confirmed against `visible_agents`' real queryset, row 4 included.

**The six residuals, and where each landed**

| # | Landed in |
|---|---|
| R1 Minor — `install_defaults` runs as `OPEN_PRINCIPAL`, not a service principal | §4.3.2 (rewritten, with the accounts-on scoping and the corrected consequence), decision 23, §15 item 2 above |
| R2 Minor — the poll path's budget was one, the real cost up to three | §3.5 (`visible_turn`'s `select_related` widened), §3.6, §8, §9 |
| R3 Minor — the form cannot tell a non-admin owner their agent carries an administrator's label | §4.3.1 (the non-disclosure gate named, `name_for_viewer` idiom, counted sentence, bare count on the lists), §9 |
| R4 Nit — §8 did not budget §4.3.2's second list section | §8 (four querysets, all flat) |
| R5 Nit — §4.8 still named `set_agent_audience` | §4.8 (deleted, with the reason) |
| R6 Nit — the truth table named the administrator in row 1 only | §4.3.1 (parenthetical dropped, short-circuit stated once below the table) |

**All six applied as written; none disputed.** R3 is the one worth a second look by a plan
author: it is the only residual that adds a *feature* rather than correcting a sentence, and it
adds it because the leak-free construction of §4.3.1 necessarily hides information from the
person who most needs it. The gate is right, the silence was not, and a count is the most that
can be said without turning
`test_no_route_other_than_the_dormant_share_page_names_an_entitlement_to_a_non_holder` red.

## 16. Owner rulings (2026-09-21)

All eight flags in §13 were ruled by the owner as one word — **"accept all recommendations"**
— making the spec's stated position binding in each: the meter measures what is sent, with
the truncation clause (flag 1); `Agent.box_wide` lands as a new column with the
`box_wide = resident` data migration (2); edit-past-prompt is BRANCH (3); the branch
provenance columns land (4); `llm_role` stays administrator-only (5); attachments do not
follow a branch in v1 (6); share recipients cannot edit-and-branch (7); and the pre-existing
member-installs-box-wide route stays open and legible as §4.3.2 describes (8). Plan authors
treat these as settled; none remains open.
