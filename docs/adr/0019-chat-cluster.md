# ADR 0019 — The chat cluster: an honest context meter, an audience column, and branching

**Status:** Accepted
**Date:** 2026-09-22

Written against `chat-cluster` at **`4b8606d`**, with its fourteen implementation tasks
landed; Task 14's fix round and the whole-branch review follow, and the branch's closing
documentation pass re-stamps this SHA to the branch tip it merges from. Its argument lives in
[`docs/superpowers/specs/2026-09-21-chat-cluster-design.md`](../superpowers/specs/2026-09-21-chat-cluster-design.md),
whose §16 records the owner's ruling on all eight of its open flags; the plan that executed it
is [`docs/superpowers/plans/2026-09-21-chat-cluster.md`](../superpowers/plans/2026-09-21-chat-cluster.md),
which carries a dated amendment listing every reviewed deviation. **Where the spec and the
code differ, the code is what this ADR records** — including the corrections the fourteen
review rounds forced onto the tree after the design was written — and every claim below is
checkable against the tree it describes.

## Context

Three requests landed together, and they share no code but one branch:

1. Show how much of the model's context a conversation is using.
2. Make the agents screen a real utility that members, not only administrators, can reach.
3. Let somebody edit an earlier message and carry on from there.

Each of the three ran into a fact already in the tree that decided its shape. The first met
[ADR 0010](0010-model-management-framework.md)'s own incident write-up, which forbids asking a
model server what a model's maximum context is. The second met
[ADR 0016](0016-identity-and-entitlements.md)'s entitlement gate, which already refuses a
label the actor may not label with, and `Agent.resident`, which already meant something else.
The third met [ADR 0017](0017-workstreams.md)'s taint rows, which are additive-only by
design.

## Decision

### 1. The operative window is the honest denominator — ADR 0010's column gains a READ direction

The meter divides by what the engine will actually be asked to allocate:
`models/contracts/bindings.py::effective_context_window` returns the operator's own
per-connection `context_window` when there is one, the engine adapter's bounded default
otherwise, and a declared "unknown" when the bound engine declares no default here — or when
the stored value is not a positive integer, the degradation the function's own docstring calls
out. **Not** a model's architecture maximum, and **not** a probe.

This amends [ADR 0010](0010-model-management-framework.md)'s 2026-08-22 context-window
amendment with a direction it did not have. That column was written to be **sent** — the
adapter spreads it into the engine call so a client can never again make the engine resolve
its own maximum. It is now also **read, for display**, through one pure function that writes
nothing, sends nothing, and never touches the network.

Two ways of being wrong were available and both are refused:

- **Showing the architecture maximum** would mislead in the dangerous direction. If the engine
  is asked for a bounded number, that bounded number is what truncates the conversation, and
  a reader told they have more room than the engine was asked for would act on it.
- **Probing for it** is what ADR 0010's incident is about. A display probe is still a probe:
  one per page render, against a machine that may be asleep.

The engine-to-default map is a lookup (`_ENGINE_DEFAULT_WINDOWS`), so a future adapter adds
itself to it. An engine absent from the map answers "unknown" rather than borrowing another
engine's number, and the page renders a ceiling-free line.

**It deliberately answers differently from the retrieval page's own reader.**
`tools/rag/views.py`'s `_resolved_answer_context_window` returns "unknown" where this one
returns the adapter's default, because that one decides whether to run a fit check at all —
skipping a check on a guessed number is safe, and refusing a legitimate top-k for a reason
the operator never configured is not. This one must display something.
`models/registry/README.md` records the reconciliation so the next reader finds it before
concluding one of the two is a bug.

### 2. The meter measures what is SENT, not what the conversation holds

The platform replays a fixed number of recent turns (`agents/limits.py::HISTORY_TURNS`), so a
long conversation is already shortened before it is sent. Reporting the whole conversation's
size as "what this chat is using" would have been false, and would have been false in the
direction that makes the number useless: it would keep climbing while the thing it claims to
measure had stopped.

So `agents/usage.py::context_usage` measures the replayed slice, and the line says so —
`truncation_clause` names how many of how many messages are no longer sent. That clause is
the first place this platform tells a reader their conversation is being shortened before it
reaches the model.

The estimate is characters over four. There is no tokenizer on this box, adding one would be
a new dependency for a status line, and asking the engine to tokenize would be a network call
per page render on a surface whose whole design avoids per-render engine traffic. It is
**disclosed, never presented as exact**, by a `<details>` on the page.

It is also a deliberate **under-count**, with six named exclusions in `agents/usage.py`'s
module docstring. Under-counting is the right direction to be wrong in for a "should I start
a new conversation" signal *only if the reader is told*, so the disclosure names the two
exclusions large enough to change a decision: what a tool was asked and answered, and files
attached to a message.

**One counter, not two.** `estimate_tokens` is the only token arithmetic on this platform, at
the column root rather than inside `agents/chat` so a future management command or MCP edge
can ask the same question without being a view. Conversation compaction, when it is built,
consumes this function rather than growing a second, drifting one —
`agents/limits.py::HISTORY_TURNS`' own docstring warned against exactly that.

### 3. `box_wide` is the audience column; `resident` stays the origin marker

`Agent.resident` records that a row **started life as a shipped default**. It is an origin
marker, not a lock, and it has a second reader: `agents/visibility.py::resident_agent_tool_keys`
warns the tool-label page that labelling a tool would weaken the shell path for the shipped
agents that declare it.

Overloading it to also mean "everybody on this box may see this" would have made that warning
fire for rows that were never shipped defaults, and would have made the new agent form's
audience control lie about provenance — an operator ticking "everyone on this box" would have
been stamping their own row as platform-shipped.

So audience got its own boolean, `Agent.box_wide`, and `visible_agents` /
`installed_agent_slugs` swapped one leg of their ownership OR for it. Migration
`agents/migrations/0012_agent_box_wide.py` set `box_wide = resident` for every existing row,
so **nobody's access changed on the day it landed**. `Flow` gained no such column and needed
none: `visible_flows` still reads `resident`, and the asymmetry is recorded in
`agents/README.md` rather than papered over with a column nothing writes.

`agents/defaults.py::install_default` stamps `box_wide=True` **unconditionally**, including on
`--reset`, and deliberately not on the installing principal's authority: that command runs as
the open principal, for whom `is_admin` answers True on an open box and False on an
accounts-on one, so stamping by authority would have left the canonical shell install working
on an open box and silently breaking on the posture where a shipped default most needs to
reach everybody.

### 4. Audience is TWO independent controls, never one exclusive choice

**Reach** writes `box_wide` and touches no labels at all. It is a plain field, offered to
administrators only, and the POST re-checks that rather than trusting the render.

**Entitlement labels** go through the path that already exists —
`agents/chat/service.py::parse_entitlement_diff` → `agents/labels.py::set_agent_labels` — as
an **add/remove diff over what is there now**, never a whole submitted set. That gate refuses
any entitlement the actor may not label with, and because both panes are built from what the
actor may label with, a label an administrator set is never in `choices`, therefore never in
either pane, therefore never in `submitted`. It survives both directions untouched.

The two **compose**: `visible_agents` is `(owned | box_wide | shared) AND label_permitted_q`.
A box-wide agent narrowed to a department is the useful fourth row of that truth table, not a
contradiction.

**There is deliberately no single audience writer.** One control writing both would either
clobber a label its actor may not touch or refuse an edit it should allow. Two controls, two
POST paths on one route, told apart by a named `action` field — and the panel is a **sibling**
of the field form, never nested, because the shared transfer panel renders its own `<form>`
and nested forms are illegal HTML.

### 5. One agent form, two mounts, one edit route

`/chat/agents/` lists the agents a principal may edit; `/settings/agents/` lists every agent
on the box, for an administrator. **One edit route serves both** — `chat-agent-edit`, class O,
rendering one builder (`agents/chat/agentform.py::agent_form_context`) through one fragment
(`chat/_agent_form.html`). The two mounts differ only in which rows they list and who may open
them. Nothing about editing an agent is written twice, and there is no second write endpoint
anywhere in the settings area.

`chat-agent-edit` is class **O**, not S, and that is the whole feature: an S route refuses a
non-administrator at the middleware, which is exactly the person these pages exist for. The
view turns `agents/visibility.py::may_manage_agent` into the house 404, the shape every other
row-addressed route in this column carries.

It is also the one class-O route whose administrator answer does **not** move with the
administrator-content toggle. `may_manage_agent` short-circuits on `is_admin` alone, because
managing an agent is administering box inventory rather than reading somebody's content — an
administrator who could not open the row could not switch a runaway agent off.
`identity/tests/test_route_matrix.py` names that single cell in `_ADMIN_ALWAYS_ADMITTED_O`,
and `identity/access.py::sees_all_content`'s own docstring now records the distinction rather
than leaving two true-looking statements side by side.

**`/settings/agents/` is not a `/chat/` route**, so it has its own URLconf in the owning column
(`agents/chat/agent_admin_urls.py`), mounted from `config/urls.py` beside `/settings/` — the
same shape the settings assistant already set. The cost is one test module: the column's
never-500 sweep derives itself from that column's own URLconf and cannot see a route mounted
elsewhere, so this route's never-500 proof lives in its own test module.
`docs/EXTENDING.md` records both halves.

### 6. The restriction count is asked only when accounts are on — hidden, never zeroed

Both list pages show how many entitlements restrict a row. Both gate that read on
`accounts_on()` **alone** — not on a posture constant — because with accounts off a label
restricts nobody: `visible_agents` returns at its `sees_all_content` short-circuit before the
label clause is ever evaluated.

Without the gate the fold's own meaning inverts. The number is "how many labels this reader
cannot manage"; with accounts off the reader can manage all of them, so the count becomes
every label on the row, printed as a restriction to the one operator who could change any of
them.

The column is **hidden, never zeroed**. A *rendered* `0` would say "none" where the truth is
"not asked"; `0` stays the honest internal value, and the templates drop the cell rather than
print it — the chat list drops the chip, and the settings list drops the header cell, the data
cell and one from the empty row's `colspan`, single-sourced from one flag.

**The count is a bare number, never a name.** Naming the entitlements would be the
entitlement non-disclosure gate, which the route matrix sweeps these routes for. It is folded
in the view from two batch reads shared by both sections through one closure, so twenty-five
rows cost what one row costs.

### 7. `chat_capable_roles()` is the role vocabulary, and it lives below both columns

An agent's `llm_role` is answered in two places: the **form** decides what to offer, and the
**writer** decides what to accept. `agents/visibility.py` may not import `agents.chat` (the
import law), so a single filter reachable from both has to live below both — and
`models/contracts/roles.py` is that place, exactly as it already is for the role keys
themselves.

`chat_capable_roles()` returns `((key, label), ...)` — plain data, not the module's dataclass,
because the form renders pairs into a `<select>` and the writer only needs the keys. Two
spellings of "which roles are chat roles" would be a form that offers what the writer rejects,
which is the render-versus-gate bug in its other direction.

### 8. Editing a past prompt BRANCHES; it does not rewind

Editing an earlier message creates a **new conversation** holding everything before it, with
the edited message as its newest turn. The original is untouched, unrenumbered, untruncated.

A rewind cannot honestly un-taint. Taint rows are additive-only by design
([ADR 0017](0017-workstreams.md)), and deleting the turn that brought labelled material in
would either leave a dangling reference or perform exactly the laundering the platform
forbids — edit the tainted turn, the tag disappears, the share comes back. A rewind also
orphans audit rows and attachments, and in a conversation shared at a level that permits
posting it would destroy somebody else's work with no undo and no trace.

The cost of branching is sidebar clutter, and it is paid with **provenance** rather than by
destroying history: `Conversation.branched_from` (`SET_NULL`) and
`Conversation.branched_at_index`, added by `agents/migrations/0013_conversation_branch.py`,
make "where did this thread come from" a fact you can query rather than a string you can only
read. The asymmetry decided it: branching when a rewind was wanted costs reversible clutter;
rewinding when history was wanted destroys data irreversibly.

**A share recipient may not branch**, not even on their own message. A branch is a copy, so it
answers to the copy predicate (`may_manage_conversation`) and not the wider `may_post_to`: a
recipient minting a durable conversation they own, which survives revocation of the share, is
a different decision from letting them post. What they are refused is a copy.

**Attachments do not follow a branch in v1**, and the form says so before the button. Carrying
them would mean a fifth registered attachment seam — a copier — in the documents column.
Files attached to the *edited* message work normally: they are staged by the same reader the
composer posts into.

**No audit row, and no new action in the closed vocabulary.** A branch is a copy the principal
already had the right to make, and the parent is untouched — the same reasoning
`duplicate_conversation` already carries. Create, duplicate, delete and rename write none
either. Adding a `conversation.branch` action later is an additive change to the writer plus
that vocabulary's own count pin.

**Where the work splits, and why it has to.** `agents/chat` imports `agents/visibility`, one
way, so `branch_conversation` cannot start a turn and cannot redirect. It writes the branch
and returns it; the `turn_edit` view validates the text against the same character constant a
new message uses **before** calling it — so a refused edit writes nothing at all — then starts
the turn through the one existing turn-start service and redirects.

**The half-state is accepted and stated, not papered over.** If that start refuses afterwards
— an unbound role, an unreachable engine, an unmigrated queue — the branch exists with its
copied history and no answer, which the thread page already renders honestly with its existing
banner. The alternative was deleting a conversation the operator can already see in their
sidebar.

### 9. The in-flight clause is conversation-wide, and the control is HIDDEN rather than refused

One non-terminal turn anywhere in the conversation blocks every edit in it. That is a
conversation-level fact, so it is asked once per render rather than once per message.

Because it is conversation-wide, the control is **hidden** for the whole thread while an
answer is running, not merely refused — a button whose own POST answers 404 is a page that
lies. The POST refuses anyway: hiding a control is never the gate.

This is also what makes the poller cheap. The queued and running ticks pay nothing for the
disclosure, because the predicate is provably false while a turn of that conversation is in
flight, and on those two paths the turn being polled **is** such a turn. Only the `done` tick
computes it.

### 10. One row predicate, `is_editable_turn_row`, spelled once

Editability splits in two and each half lives once:

- `agents/visibility.py::is_editable_turn_row(turn)` — the per-turn half: a finished,
  root-depth user row. No principal, no query, which is why the card may ask it once per
  rendered message.
- `may_edit_any_turn(principal, conversation, …)` — the conversation half: the manage
  predicate plus "nothing in flight".

`may_edit_turn` composes both, plus its own one clause — that the turn really belongs to this
conversation, since both ids arrive from the URL separately.

So the page's answer and the POST's answer are built from the same two functions and cannot
drift. That matters in both directions: a card copy grown wider would render a disclosure
whose own POST answers 404, and one grown narrower would silently hide an available control.
The first spelling to land had the row rule written twice — once in the predicate, once in the
card builder — with no test that could notice them disagreeing; the agreement pin that
replaced it could not have been written before the extraction, because there was no single
object to move.

### 11. One copier under two public writers — and the `author_id` fence it fixed

`branch_conversation` and `duplicate_conversation` are siblings: same gate, same constants,
same rules about what an audit row and an attachment belong to — one bounded by an index and
stamped with provenance, the other not. Both copied an eleven-field turn row and a taint block
**byte for byte**, in two places.

The field list now lives once, in `_copy_turns_into` / `_copy_taint_into`: two public writers,
one private copier, each public function keeping its own gate and its own create. Both copy in
one transaction with one bulk write, and the cost is pinned **flat in the number of turns
copied** by an equality between a short thread and a long one — a budget of the form "under
N" stays green on a per-turn write for every thread a test would bother to build.

**This is a behaviour change, not only a refactor.** `duplicate_conversation` did not copy
`Turn.author_id` until this branch — the field had fallen out unnoticed, and the fix had to be
typed twice, which is what argued for the extraction. It is operator-visible: a duplicate
carries no shares, and the prompt builder treats an author-less turn in an unshared thread as
the reader's own, so a duplicate of a conversation somebody else had posted into used to
replay that person's words to the model **unfenced**. It no longer does. The fence is a
boolean, never a rendered name, and no caller asserted the old outcome.

### 12. The poller carries the picker's selection across the `done`-tick swap

The `done` tick supplies the same card keys a reload does, so a swapped exchange renders the
edit disclosure exactly as a reload would — the invariant four docstrings in
`agents/chat/views/turns.py` each state in their own words, and the one round-13 lesson about
stale strips this branch invoked for the meter and then nearly argued against for the
disclosure.

One value cannot travel on the wire: the picker's own selection lives in the thread page's
query string and never reaches the poll endpoint. A no-JS page **load** never lost the pick —
the server renders it into the field — so the polled swap was the only path that did, and its
branch's first turn would then have answered from a different binding.

It is carried across **on the page** instead: four lines in the existing poller copy the
composer's own server-rendered hidden field into each swapped block. One field on the page
into another field on the same page — not a client-invented number, not prose, not
`innerHTML`, nothing sent to the server. The page's pinned script-tag counts are unchanged,
and no new `<script>` exists anywhere in this branch.

## Named gaps and deferred work

Each of these is a decision to stop, not an oversight, and each names the hook it would land
on.

**G1 — the agent library's Owner column prints a principal key, not a name.**
`/settings/agents/` renders the raw `owner_kind:owner_key` pair. It is administrator-only and
it is correct, but it is not what an operator wants to read. Closing it is a small
cross-column addition — an ids-to-names reader in `identity/`, shaped like the existing
`labelling_entitlements` pair-returning helper — because **no display-name reader exists
there today**. It is not a one-liner in a template, which is why it is recorded here rather
than fixed in passing.

**G2 — a member can own a box-wide row and cannot edit it.** The install route predates these
pages: it is reachable by any signed-in principal and stamps the installing principal as the
owner, so a member can own a row everybody on the box can use and that `may_manage_agent`
refuses them. The owner ruled this route **stays open and is made legible** rather than
closed: those rows get their own read-only section on the member's own list, with a declared
sentence saying an administrator can change it and **no** edit link — a link would be a link
to a 404, which is the defect the section exists to prevent. Which sentence a row gets is
`may_manage_agent`'s answer for **that row**, never the section's, so an administrator reading
the same section gets the link and a different declared sentence.

**G3 — `/settings/assistant/` is outside the zero-query mount sweep, deliberately.** That
sweep proves a mount reads no identity table on an open box. The two agent pages joined it
once their entitlement read was gated; the settings assistant's mount has not been brought
under it, and that absence is now **named in the sweep module itself** so it reads as a
decision rather than an oversight. Closing it means giving that mount the same gate, or
recording why its read is not a permission check.

**G4 — a pre-existing poll-path surcharge for in-stream conversations.** Every poll tick of a
conversation inside a workstream pays about five authorization queries more than a loose one,
from the attachment-freshness path's own scope resolution. It predates this branch — measured
directly at the branch point — and it contradicts the spec review's "nothing extra" budget for
this surface. The branch's own pins now assert the *shape* (no lazy agent or workstream read
on either side) rather than encoding the surcharge as a passing number, so an audit of
poll-path budgets has something honest to start from.

**G5 — agents have no name uniqueness.** Two agents may carry the same `name`, differing only
by derived slug; both list pages render names. **Carried for v1, not ruled:** slug-only
uniqueness stands by default because `Agent` never had a per-owner name constraint, and
Task 6's report asked for an owner decision before the list pages landed — none was taken, so
this is an open question rather than a settled one. `Workstream`'s per-owner name constraint
is the shape a fix would take.

**G6 — the field writer's refusal is keyed under `name`.** `update_agent` returns a
field-keyed error dict and has no form-wide key, so its "not yours" refusal is keyed to
`name`. Today the view 404s before reaching it, so nothing renders it under the name input; a
surface that ever does should render it form-level.

**G7 — the two access pages' panel-dict builder is now typed at a third consumer.** The
row-to-panel mapping written in the access view has a third caller in the agent form. It is
small and it is not duplicated logic, but a fourth consumer is the signal to move it into the
shared service module beside the rest of the transfer-panel plumbing.

**G8 — `.access-summary` was not promoted with the rest.** The transfer panel's own `.transfer-*`
rules moved from the settings shell to the page shell when the agent editor became the first
consumer outside the settings area (a **move**, pinned by a "nothing left behind" assertion,
never a copy). `.access-summary` is the two access pages' own `<summary>` class, not the
fragment's, and both its consumers still share the settings shell — so it stayed. The agent
editor writes a plain `<summary>`. If that disclosure should look like the access pages', the
honest move is to promote that rule the same way, not to copy it.

**G9 — a fifth spelling of the conversation-title default.** The page-level default for an
untitled conversation is the agent's name; the list-level default is a literal. The provenance
banner follows the page-level one, which is locally consistent and defensible, and there is no
display helper to reuse today. Worth one shared helper before a sixth spelling appears.

**G10 — the thread page's test module is a permanent member of the split-threshold exemption
list.** It crossed the repo's per-module size gate during this branch and was exempted rather
than split, because splitting it is materially larger than any one task's file list. It now
holds two large meter classes. The next task to add to it should ask whether the split is
finally due. Exemption lists get their exempted code removed, not grown.

**Not gaps, but boundaries this branch deliberately did not cross:** granting tools from the
agent form, per-person agent sharing, a branch that carries its attachments, an audit action
for a branch, and conversation compaction.

## Consequences

- **One token estimator exists on this platform.** Compaction will consume it rather than
  growing a second, drifting counter. The meter is compaction's foundation, not a sibling of
  it: the truncation clause is already the sentence that says a conversation is being
  shortened.
- **The agent form is the first create/edit surface for agents.** Before it, an agent was a
  row an operator edited out of band or adopted from the shipped catalogue. Tool granting and
  per-person agent sharing remain deliberately out of scope.
- **Two migrations, both additive, neither visible on landing day.** `agents/0012` sets the
  new audience column from the old origin marker for every existing row; `agents/0013` adds
  two nullable provenance columns and back-fills nothing, because every existing conversation
  was started rather than branched. `docs/OPERATIONS.md` carries the deploy note.
- **A share recipient cannot edit-and-branch in a conversation shared with them.** This is the
  one refusal in the branch with a stated cost to a real reader, and it is the copy they are
  refused, not the conversation.
- **The administrator-content toggle now has a documented exception.** Managing an agent
  ignores it; listing agents does not. Both halves are stated in the predicate's own docstring
  and pinned by a named cell in the route matrix, so the next reader meets a rule rather than
  two contradictions.
- **The shared transfer panel's CSS now lives in the page shell.** Any consumer anywhere in
  the tree gets it styled, and the CSS-ownership gate keeps it in exactly one place.

## See also

- [ADR 0010](0010-model-management-framework.md) — the context-window column, its incident,
  and the 2026-09-22 amendment recording the read direction this ADR adds.
- [ADR 0015](0015-agent-layer-and-tool-contract.md) — the agent layer, the turn, and the
  shipped-defaults catalogue an installed agent comes from.
- [ADR 0016](0016-identity-and-entitlements.md) — entitlements, labels, the add/remove diff
  gate this branch reuses, and the closed audit vocabulary.
- [ADR 0017](0017-workstreams.md) — taint, and why it cannot be un-taken.
- [`agents/README.md`](../../agents/README.md) — the audience column beside the origin marker,
  the agent writers, and branch provenance.
- [`agents/chat/README.md`](../../agents/chat/README.md) — the context line, the agent pages
  and their routes, and editing a past prompt.
- [`docs/EXTENDING.md`](../EXTENDING.md) — adding a settings page, including a route whose view
  lives in another column.
