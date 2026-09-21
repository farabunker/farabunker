# agents/ — the central brain

`contracts/` shipped in P1 — see [`contracts/README.md`](contracts/README.md).
`models.py`, `runtime/`, `resident.py`, and `limits.py` shipped in P2 — see
[`runtime/README.md`](runtime/README.md) for the turn machinery itself.
`chat/` shipped in P3 — see [`chat/README.md`](chat/README.md) for the
`/chat/` surface itself.

| Sub-package | Phase | What it is |
|---|---|---|
| `contracts/` | **P1 — shipped** | Pure, Django-free **tool contract**: `ToolSpec`/`ToolResult`/`ToolContext`/`StepBudget`/`Principal` and the module-level tool registry (`tools.py`), the LLM tool-schema renderer plus the MCP wire adapter (`toolschema.py`), and the artifact-reference vocabulary (`artifacts.py`). A **rule-1 pure leaf** — any column may import it, in any direction — pinned by a subprocess with no Django configured at all (`contracts/tests/test_purity.py`), not just asserted in a docstring. Eleven tools are registered against it: `rag.search`/`rag.ask`/`rag.ingest` (`tools/rag/`), `models.status` (`models/registry/`), `vision.operations`/`vision.generate` (`tools/vision/`, gated on the `vision` feature flag), `agent.library`/`agent.illustrator` (agent-as-tool, below), `flow.run` (row-driven, P3 Task 13, below), and `settings.card`/`settings.overview` (`agents/settings_tools.py`, the settings assistant, below). |
| `models.py` + `runtime/` + `resident.py` + `limits.py` | **P2 — shipped** | The Django app itself: `Agent`/`Conversation`/`Turn`/`ToolInvocation` (`models.py`), the `agent.turn` job kind and its bounded loop (`runtime/`), the agent-as-tool derivation (`resident.py`), and the five numbers that bound a turn (`limits.py`, `MAX_TURN_CHARS` added round-3 hardening C-7/H39: a character cap on a turn's own text, checked in `agents.chat.service.start_turn`). See "How a turn runs" below. |
| `models.py`'s `Flow` | **P3 Task 1 — shipped** | Flows are rows (ruling 1), added to the data model in `agents/migrations/0002_flow_owners_and_invocation_job.py` alongside `Agent`/`Flow`'s owner columns and `ToolInvocation.queue_job_id`. See "The data model" below. |
| `defaults.py` + `manage.py install_defaults` | **P3 Task 5 — shipped** | The shipped-defaults CATALOGUE (`DEFAULT_AGENTS`, `DEFAULT_FLOWS`), the shared flow-JSON validator (`validate_flow_json`), and the one row writer (`install_default`). Replaces P2's `manage.py sync_agents`. See "Shipped defaults are a catalogue, not a deploy step" below. |
| `runtime/flowtool.py` | **P3 Task 13 — shipped** | The ONE registered `flow.run` tool and its per-turn narrowing. See "The tools this platform registers" and "Flows" below. |
| `chat/` | **P3 — shipped** | Django app, label `chat`. The permanent chat product at `/chat/`: conversation list, thread, tool cards with thumbnails and citations, the 202-and-poll contract with a no-JS path, and flow rows reached through `flow.run`. See [`chat/README.md`](chat/README.md). |
| `entitlements.py` + `labels.py` + `shares.py` | **Identity & Auth IA-2 — shipped** | `entitlements.py::tool_access_for` builds the pure `ToolAccess` (`agents/contracts/tools.py`) a turn's `granted_tools` call needs; `labels.py` reads/writes `ToolEntitlement`/`AgentEntitlement`/`FlowEntitlement` and supplies this column's two entitlement-delete cascade handlers (registered from `agents/apps.py`); `shares.py` reads/writes `Share` and answers `may_post_to`. See "The acting rule" and "The four visibility bodies get real filters" below. |

- **`agents/usage.py`** — what the next turn's prompt will carry, as an
  estimate, and the declared sentences that say it. At the column root for the
  same reason `visibility.py` and `labels.py` are: a future management command
  or MCP edge needs the same answer without being a view. It holds the one
  token arithmetic on this platform (`estimate_tokens`); chat compaction, when
  it is built, consumes this rather than growing a second, drifting counter —
  which is exactly what `agents/limits.py::HISTORY_TURNS`' own docstring warns
  against. Its estimate is a deliberate **under-count**, with six named
  exclusions in the module docstring and a disclosure on the page that names
  the two a reader can act on.

## The data model

| Model | What it is |
|---|---|
| `Agent` | A system prompt, an LLM role, and granted tool keys. |
| `Flow` | A named, multi-step recipe an agent can run as a single tool call (`flow.run`, Task 13) — the row-driven counterpart to `Agent`, added in P3 Task 1 for the same reason `AppConfig.ready()` cannot register one spec per row. |
| `Conversation` / `Turn` | A thread of turns with one agent. |
| `ToolInvocation` | The audit trail: one row per tool call, regardless of caller. |
| `ChatSettings` | The chat column's operator-policy singleton (round 21) — one row, holding the settings that govern how a conversation's prompt is built. Read through `get_solo()`, the same shape `IdentitySettings`/`RagSettings` use. Edited on the settings area's **Chat** page (`/chat/settings/`). |

`Agent` and `Flow` both carry `owner_kind` / `owner_key` — the acting
`Principal`'s two fields, stored flat (ruling 4b), in the same shape
`Conversation`'s identical pair has carried since P2. `PRINCIPAL_KINDS`
gained a fourth value in P3 Task 1, `"open"` — the principal an
unauthenticated box acts as — so an install with no accounts still has
something honest to stamp on every row it writes, rather than a blank
that a later filter cannot reason about. Identity & Auth's owner ruling
4 stamps every create with the ACTING principal, through
`identity.access.owner_fields` (moved out of `agents.visibility`, Task
11 — see "The acting rule" below).

`Principal` ITSELF MOVED (Identity & Auth, Task 9): it now lives in
`identity/contracts/principals.py`, a rule-1 pure leaf every column may
import, alongside `OPEN_PRINCIPAL`, `SERVICE_PRINCIPAL`, `ANONYMOUS`,
and `principal_from_payload`. `agents/contracts/tools.py` imports it
from there rather than declaring its own copy — never re-exported, so
there is exactly one import path to grep for.

**Residents are operator-owned (ruling 3, 2026-08-28).** P2 gave a
`resident=True` row an edit-lock: `Agent.save()` refused any field
change unless `sync_resident_agents` passed a bypass keyword, and
`delete()` refused outright, because the row was a projection of code
and a drifted row would make the code lie about what was running. That
model is gone. `resident=True` is now an **origin marker** — "this row
started life as a shipped default" — and nothing more: the operator
owns the row from the moment it exists, may edit or delete it like any
other, and `manage.py install_defaults --reset <slug>` (Task 5) is how
the shipped text comes back, a deliberate act with that text in front
of them. The one piece of the old lock that survives is that **`slug`
is immutable** on both `Agent` and `Flow` — it is the key `flow.run`
resolves, the key an `agent.<slug>` grant names, and the key `--reset`
matches on, so renaming one in place would silently repoint every
reference to it. `Conversation.agent`'s `PROTECT` still refuses to
delete an agent with conversations; that guard was never part of the
resident lock and needed no change.

### `box_wide` and `resident` are two different facts

`resident` records **where a row came from** — it started life as a shipped
default. It is an origin marker, not a lock (`Agent`'s own ruling 3), and it has
a second reader: the tool-label page's shell-path warning
(`agents/visibility.py::resident_agent_tool_keys`) names the shipped agents that
declare a tool an operator is about to label.

`box_wide` records **who may use it** — everybody on this box, or only the people
its ownership and its entitlement labels reach. `visible_agents` AND-s the label
clause onto its ownership OR, so a box-wide row narrowed to an entitlement
reaches everybody on this box *who holds it*; the two controls compose rather
than excluding each other.

Migration `0012_agent_box_wide` set `box_wide = resident` for every existing row,
so the swap changed nobody's access on the day it landed. `install_defaults`
stamps `box_wide=True` unconditionally, including on `--reset`: a shipped default
is the platform's offer, not the installing operator's private row.

## Flows

**Ruling 1, deviation P3-D11.** A flow is a ROW, and rows cannot become
registered `ToolSpec`s — `AppConfig.ready()` may touch no database, so
N flows cannot become N registrations. The answer is **one** registered
tool, `flow.run` (`agents/runtime/flowtool.py`), whose `flow` param is
a `"choice"` with `choices=()` at registration and a `runner` that is
the dotted-path string `agents.runtime.flow.run_flow`. `ready()` still
imports no implementation module and touches no database.

Every turn, `agents.runtime.loop.available_tools` narrows that one
spec through `narrowed_flow_spec(spec, principal)`: it fills `flow`'s
`choices` from `agents.visibility.visible_flows(principal)` — the
enabled `Flow` rows this principal may run — and appends one
description line per flow naming its slug and its input keys. With no
visible flow at all, `narrowed_flow_spec` returns `None` and
`flow.run` is **dropped from the tool list entirely**: offering a tool
whose only argument has no legal value would let the model call it,
fail, and burn the turn's one recovery on something that could never
have worked.

At enqueue time, the same row-driven answer is asked a second question:
`agents.runtime.jobs._tool_roles` calls `flowtool.flow_row_roles(
principal)`, which unions the declared `roles` of every registered step
tool across every visible, enabled flow (skipping a step whose tool is
not registered here — ruling R1's tolerance, the same one a granted
tool's own unresolvable role already gets). `flow.run` itself declares
`roles=()`, because a flow's roles live in its steps, not in the one
spec every flow shares — so without this, the queue would never be
told a flow needs (say) the embedding model, and the admission snapshot
would simply be wrong.

**Adding a flow needs no deploy and no restart.** A row saved through
`Flow.objects.create(...)` (or `install_default("flow", <slug>,
principal)`) is visible to `visible_flows` the moment it commits, and
the very next turn's `available_tools` call picks it up — there is no
process to restart and nothing to redeploy, because the registered
spec never changed; only the rows behind it did.

**The shipped flow catalogue** (`agents.defaults.DEFAULT_FLOWS`):

| Slug | What it does |
|---|---|
| `library-brief` | A fixed two-step brief on one topic: `rag.search` the operator's own documents for it, then `rag.ask` to answer it from them with citations — the same two steps, in the same order, every time. Its `category` input is optional; leaving it out searches every category (ADR 0009). |

## How a turn runs

1. **Enqueue** — a caller (today, `manage.py agent_turn`; P3, the `/chat/`
   view) preflights the agent's chat role and tool-calling support, writes
   the USER turn and a placeholder ASSISTANT turn (both `state="queued"`
   for the placeholder), then `enqueue("agent.turn", payload)`.
2. **Plan** — `agents.runtime.jobs.plan_turn` resolves the admission
   snapshot: the agent's chat role, plus the union of every tool role
   reachable through agent-as-tool delegation, walked to `MAX_AGENT_DEPTH`
   and tolerant of an unresolvable tool role (never of an unresolvable
   chat role — the enqueuing caller already preflighted that).
3. **Loop** — `agents.runtime.loop.run_turn` builds the message history
   (`agents.runtime.prompt`), then calls the bound LLM in a bounded loop:
   one tool call per iteration, `calls[0]` taken and the rest discarded
   and reported, until an answer, the step budget, the deadline, two
   failures, or a tool-incapable model ends it.
4. **Tool** — every call goes through `agents.runtime.invoke.invoke_tool`,
   which validates, resolves the runner by dotted-path STRING (never an
   import), classifies the result into one of five outcomes, and writes
   the audit row.
5. **Turn** — a TOOL turn is written for every call (its own row, its own
   `ToolInvocation`); the placeholder ASSISTANT turn is moved to the end
   of the conversation and filled in with the final text and artifacts
   once the loop ends.

## The acting rule (Identity & Auth, Task 11)

See [`identity/README.md`](../identity/README.md) for the column that
made this rule possible: the postures, `is_admin`/`sees_all_content`,
and the four seams (`identity.contracts.*`, `identity.access`,
`identity.request`, `identity.audit`) every other column, this one
included, reaches identity through.

**A turn runs as the USER, never as the agent.** `agents/runtime/
bindings.py::principal_for(agent)` used to answer "who does a turn by
this agent run as" with `Principal("resident_agent"|"user_agent",
agent.slug)` — a principal minted from the AGENT. Identity & Auth's
acting rule (spec section 5.3) makes that the wrong question and
deletes the function outright: `identity.contracts.principals.
principal_from_payload(payload)` reads the ACTOR every job payload now
carries (Task 10's `actor_kind`/`actor_key`), and that — never the
agent — is who the turn runs as. It never raises: a payload with no
actor (every turn enqueued before this phase) or a hand-edited one with
a junk `actor_kind` both still run, honestly, as `OPEN_PRINCIPAL`.

**No hop widens it.** `agents/runtime/delegate.py::run_agent_tool`
inherits `ctx.principal` — the ROOT caller's — verbatim; it never mints
one from the agent it is about to run. An agent is a tool a person
wields, not a second person, so a `general` agent delegating to a
`library` agent never turns "the user" into "the library". What DOES
change at a delegation hop is `ToolContext.agent_slug` (Task 10) —
WHOSE TOOL DECLARATION IS IN FORCE — while `principal` — WHO IT WAS
MADE FOR — stays the same all the way down. `ToolContext.tool_access`
(IA-2 Task 5) makes the SAME point about the grant half: a delegate
reuses `ctx.tool_access` verbatim rather than rebuilding one from its
own agent or principal, which is what makes an agent never a way
around labels on the entitlement side too. `ToolInvocation` now
persists `agent_slug` as well (`agents.runtime.invoke.invoke_tool`'s
own write, from `ToolContext.agent_slug`), so "which agent made this
call" survives both in the audit table AND in `Turn.tool_call["agent"]`
— `loop.py`'s own call-record field, stamped independently from the
same `agent.slug`.

**The enqueue-time and run-time answers can never disagree.**
`agents.runtime.jobs._tool_roles(agent, actor, access)` (was
`_tool_roles(agent)`) walks the agent-as-tool closure asking
`granted_tools` with the SAME `actor` AND the SAME `ToolAccess` at every
hop, and `plan_turn` derives both that `actor` and that `access` from
the payload with the identical `principal_from_payload`/`agents.
entitlements.tool_access_for` calls the run-time loop makes. IA-2's
`ToolAccess` (`agents/contracts/tools.py`, `agents/entitlements.py`) is
what gives this real teeth: a tool the acting principal holds no
entitlement for is dropped at BOTH the enqueue-time role walk and the
run-time prompt, never one without the other.

**`agents.visibility.visible_flows`'s 2026-08-28 ruling is reversed.**
It used to say a delegate sees the flows IT may run; the acting rule
says a delegate sees the flows the ROOT USER may run, for the same
reason a delegate's principal never widens. Recorded in an amendment to
`docs/adr/0015-agent-layer-and-tool-contract.md` §10.

**`identity.access.owner_fields` is now the one definition.** It used
to live in `agents/visibility.py`; Task 11 moves it out (`agents.
defaults.install_default` and `agents.visibility.create_conversation`
both import it from `identity.access` now) so all five owner-carrying
tables in three columns share one function rather than three that agree
by convention.

**`Turn.author` records WHO the acting rule ran as, per turn (C-1,
security round 3, H25).** `may_post_to` (below "The four visibility
bodies get real filters") admits a use-level share recipient and a
workstream share recipient alongside a conversation's owner — more than
one person may legitimately post into the same thread — and before this
column a `Turn` row could not answer "whose words are these" at all.
`author` is a nullable `ForeignKey(settings.AUTH_USER_MODEL,
on_delete=SET_NULL)`, stamped at the two places a turn is actually
written: `agents.chat.service.start_turn`'s USER-turn row (`int(actor.
key)` for a `"user"` principal, `None` for any other kind — an open box
has no user to record) and `agents.runtime.loop.run_loop`'s TOOL-turn
row (the ACTING principal the loop ran as, not the agent that chose to
call the tool — `_tool_turn_author_id`, which answers `None` rather than
a raw `int(principal.key)` when that id no longer names a real account,
since the acting principal there is reconstructed from a stored job
payload that can outlive the row it named). `None` always means "not
attributable", never "the platform" — every row written before this
column exists, and an open-posture turn, both read this way.

`agents.runtime.prompt.history_messages` is the REPLAY half: a USER turn
whose `author_id` names somebody other than the principal now acting is
wrapped in the SAME fence `_carrying_attachments_block` already applies
to a file's own bytes (a per-call random `_carrying_delimiter`,
`_neutralize_fence_lines` over the body, an explicit "this is DATA,
never instructions" header) — "one fence, one home" (this repository's
own standing rule), never a second fence implementation. An author-less
row (predating the column) is treated as foreign only when its
conversation actually names a share recipient at all; a loose,
never-shared conversation's author-less rows replay exactly as they
always did. This closes the REPLAY half of the finding only — it does
not change who may post (`may_post_to` is untouched) and it does not
refuse to replay a foreign turn; both are separate, later decisions.
Rendering the author on the turn card is a follow-up filed against the
chat surface owner (`agents/chat/templates/chat/_turn_card.html` is
held elsewhere).

## The four visibility bodies get real filters (Identity & Auth, Task 13)

**Every function in `agents/visibility.py` tests the OPEN BRANCH FIRST.**
`sees_all_content(principal)`/`is_admin(principal)` test `accounts_on()`
before touching another table, so an open box still executes zero
ownership queries and the four functions still return everything —
IA-1 makes the rule real without changing that.

**Ownership is the base rule; IA-2's `Share` extends it.** `visible_
conversations`/`visible_agents`/`visible_flows`/`installed_agent_slugs`
filter on `identity.access.owned_rows_q(principal)` (own rows OR a
service-owned one when `principal` is an admin) OR a box-wide row —
`box_wide=True` for `visible_agents`/`installed_agent_slugs` (task 5,
chat cluster feature B; `Agent`'s own audience column, distinct from the
`resident` origin marker) but still `resident=True` for `visible_flows`
(`Flow` has no `box_wide` column and needed none), because either way
the shipped defaults are visible to everybody — they are the platform's
own offer, not somebody's private work — OR `Q(pk__in=agents.shares.
shared_keys(target_type, principal))` — a row's owner extended it,
directly or through a group, at either level; `agents.visibility.
may_post_to` is what then tells a `view` share apart from a `use` one.
`agents/shares.py` is the SECOND module permitted to touch `Share.
objects` directly (`foundation/ops/tests/test_column_boundaries.py`'s
`_VISIBILITY_MODULE_EXCLUSION`) — one module per owned manager, so a
guard with an exception never becomes a guard somebody widens.

**`owned_rows_q`'s service branch is the one place the administer/read
split does not apply** (spec section 5.3 item 8; full rule at its
canonical home, `identity/README.md` §5b): a row a SHELL PATH created has
nobody behind it for anything to be private from, so an administrator
sees it on `is_admin` alone, whatever the content-visibility setting
says. The read side (`owned_rows_q`) and the mutate side
(`may_manage_conversation` — one predicate for delete, rename,
duplicate, archive and unarchive alike since UI-3b) agree on this, or an
administrator would see a service-owned conversation on the list and get
a 404 clicking delete.

**`visible_turn(principal, turn_id)` resolves through the conversation,
never by bare pk** — `chat-turn-status` takes a sequential integer,
which is the enumeration exposure the agents spec's gap 4 recorded, and
this closes it: an unknown id and an invisible one both answer `None`,
and the view cannot tell them apart. `agents/chat/views/turns.py::
turn_status` and `agents/chat/views/conversations.py::
conversation_delete` route through `visible_turn`/`delete_conversation`
rather than a bare `get_object_or_404` for exactly this reason.

## Agents and flows carry their own labels too (Identity & Auth, Task 15)

**`AgentEntitlement`/`FlowEntitlement`** (`agents/models.py`, read and
written through `agents/labels.py`) attach an
`identity.Entitlement` directly to an `Agent` or a `Flow` — a second
label kind alongside `ToolEntitlement`, gating not "may this principal
call this tool" but "may this principal use this agent/flow at all".
`agents/labels.py::set_agent_labels`/`set_flow_labels` write them;
`agent_flow_label_cascade` is this label kind's answer to an
entitlement being deleted, registered from `agents/apps.py::ready()`
alongside `tool_labels_cascade`.

**One label clause, used by all three visibility bodies.**
`agents/visibility.py::label_permitted_q(principal)` returns a `Q` that
`visible_agents`, `installed_agent_slugs` and `visible_flows` all filter
through — one clause to keep in agreement rather than three (unlabelled
rows pass; a labelled row matches on holding ANY one of its
entitlements, the same OR-within-AND documents and tools use).
**It composes with the box-wide carve-out rather than being bypassed by
it** (decision 35): the label clause is AND-ed onto the
ownership-OR-box-wide clause (`box_wide=True` for agents,
`resident=True` for flows — see "`box_wide` and `resident` are two
different facts" above), not OR-ed into it, so a shipped agent that has
been labelled is restricted exactly like an operator-created one —
labelling a default is not a case the rule quietly exempts.

**`AGENT_NOT_PERMITTED`** (`agents/runtime/preflight.py`) is the second
reason `Preflight` can pick 403 over 503, joining `MODEL_NOT_PERMITTED`
(models/README.md). `preflight_turn` checks
`Agent.objects.filter(pk=agent.pk).filter(label_permitted_q(actor))`
before anything else, short-circuiting on `sees_all_content(actor)`
first exactly as `visible_agents` itself does. Its message is
deliberate: **"This agent needs an entitlement this account does not
hold. The conversation is still readable; new turns are not."** — an
existing `Conversation` with that agent stays fully readable (nothing
about `visible_conversations` changed), only `chat-turn` (starting a
NEW turn) is refused, so a member is never told a conversation they can
still open has vanished.

**The delegation knock-on.** `agents/runtime/delegate.py::run_agent_tool`
resolves the delegate through `agents.visibility.visible_agents(ctx.
principal)`, never a bare `Agent.objects` lookup — the one hop where "an
agent is never a way around labels" has to be true of the AGENT itself,
not only of its tools, or a restricted agent would be one delegation
away from anybody who knows its slug. An absent agent and a labelled-out
one answer the identical refusal, on purpose: telling them apart would
leak which slugs exist.

## What is audited

Every tool call this platform makes — an agent's own loop, an
agent-as-tool delegate, and (once the MCP edge lands) an external
`tools/call` — writes exactly one **`ToolInvocation`** row: the principal
that called it, the tool key, the validated args, the classified outcome,
and the runner's own text. A `Turn.invocation` merely *references* one; the
row itself is what an operator (or a future rate limit) audits. See
`contracts/README.md`'s "The invocation log" section.

## The four structural rules this column exists to hold

1. **`agents/` never imports `tools/`, at module scope or lazily in a
   function body.** A `ToolSpec`'s `runner` is a dotted-path string
   resolved at call time by `models.contracts.jobkinds.
   resolve_dotted_path` — the same mechanism an `AppConfig.ready()`
   already uses to register a job kind without importing its handler.
   `tools/*` imports `agents.contracts` (pure, rule 1); `agents/*` reaches
   `tools/*` only through a string. There is no cycle. Guarded
   permanently: `models/registry/tests/test_registry_paths.py` resolves
   every registered `ToolSpec.runner` string; `foundation/ops/tests/
   test_column_boundaries.py::test_no_tool_module_imports_its_service_
   layer_at_module_scope` AST-scans every registration module for a
   module-scope import outside `agents.contracts`/`models.contracts`/the
   standard library; and `foundation/ops/tests/test_import_law.py::
   test_no_agents_module_imports_a_tools_package` AST-walks EVERY import
   node (not just module scope) in every production file under `agents/`
   for a `tools.*` import, lazy or not.
2. **A tool runner never blocks on a queue job.** It may enqueue one and
   return its id; it must never call `get_job` in a loop, and it must
   never call `enqueue` for work whose result it needs. On a default
   install `JobSettings.memory_budget_bytes` is `null`, which means
   sequential mode — at most one job on the whole machine — so a job that
   waits on a job it enqueued deadlocks with certainty, not probability.
   Guarded permanently: `foundation/ops/tests/test_column_boundaries.py::
   test_no_tool_runner_blocks_on_a_queue_job` (the tool contract modules)
   and `::test_no_runtime_module_blocks_on_a_queue_job` (the turn runtime
   itself — `agents/management/commands/agent_turn.py` and `agents/chat/
   views/turns.py` are the two deliberate, named exceptions: both poll
   `get_job` from OUTSIDE the queue, holding no execution slot, exactly
   as a browser poller would — the CLI's own polling loop and `turn_
   status`'s one-shot read for the page's own poller, respectively).
3. **`AppConfig.ready()` touches no database.** Role and job-kind
   registration, the agent-as-tool specs built from
   `agents.defaults.DEFAULT_AGENTS`, and the ONE `flow.run` spec
   (`agents.runtime.flowtool.FLOW_RUN`, Task 13) are all in-memory,
   code-driven registration — never a query. `flow.run`'s own `flow`
   param carries `choices=()` at registration for exactly this reason;
   the enabled `Flow` rows only reach it per turn, through
   `narrowed_flow_spec`, which `ready()` never calls. This is why
   installing a shipped default is its own explicit act
   (`agents.defaults.install_default`, `manage.py install_defaults`,
   Task 5) rather than a call from `ready()`.
4. **Nothing writes a row the operator did not ask for (ruling 2,
   2026-08-28 — see "Shipped defaults are a catalogue, not a deploy
   step" below).** `agents.defaults.DEFAULT_AGENTS`/`DEFAULT_FLOWS` are
   the one place a shipped default is authored; `install_default` is
   the only thing that ever turns one into a row, it is create-if-absent
   and idempotent, and the row it writes is freely editable afterwards
   (ruling 3), exactly like any other row.

## Named gap P3-G1

`agent.<slug>` delegate tools (`agent.library`, `agent.illustrator`) are
CATALOGUE-registered: `agents/apps.py::ready()` builds their `ToolSpec`s
from `agents.defaults.DEFAULT_AGENTS` (code, via `agents.resident.
agent_tool_specs()`), never from a row, because rule 3 above forbids
`ready()` from touching the database at all. `flow.run`, by contrast, is
ROW-driven: one generic spec is registered at `ready()` time with
`choices=()`, and `agents.runtime.flowtool.narrowed_flow_spec` fills in
the real, enabled `Flow` rows per turn, well after `ready()` has run.

That is an asymmetry between the platform's two delegation mechanisms,
and it is a DEFERRAL, not an oversight: `ready()` may not touch the
database, so an `agent.<slug>` tool can only exist for an agent whose
spec is available in memory at import time — today, that means an
entry in `DEFAULT_AGENTS`. A row-declared agent that is not one of the
shipped defaults has no way to become callable as `agent.<slug>`,
because there is nothing at `ready()` time for it to register from.

Closing this gap means giving `agent.run` the same treatment `flow.run`
already has: one generic, catalogue-independent spec registered at
`ready()`, narrowed per turn to the ROW-installed agents a principal may
delegate to — the later symmetry item this gap defers to. Until then,
`agent.library` and `agent.illustrator` work because they are shipped
defaults with `as_tool=True`; a user-authored agent cannot be delegated
to as a tool at all.

## Shipped defaults are a catalogue, not a deploy step

**Ruling 2, 2026-08-28.** P2's `manage.py sync_agents` re-applied
`RESIDENT_AGENTS` to its rows on every deploy that ran it — a code
change to the declarations became a database write the next time
anybody remembered to run the command. That is retired. `agents/
defaults.py` now declares a **catalogue**: `DEFAULT_AGENTS` and
`DEFAULT_FLOWS` describe what this platform *offers*, and nothing reads
them into the database on its own.

Two installers, both routing through `agents.defaults.install_default`,
which is **create-if-absent and never overwrites**:

- `manage.py install_defaults` — the CLI equivalent, and the direct
  replacement for `sync_agents`. Idempotent: running it after every
  deploy costs nothing on a box where nothing changed, and it never
  touches a row the operator already has.
- the "Add the default X" button on `/chat/` (`agents/chat/views/
  defaults.py`) — the same call, from the page.

`install_defaults --reset <slug>` is the **one** command that ever
rewrites an existing row: it restores the named default's shipped text,
deliberately, with that text in front of the person who ran it, and it
logs what it replaced. Every other path — a deploy, `AppConfig.ready()`,
a page render — writes nothing; `agents/tests/test_defaults.py::
TestNothingWritesRowsByItself` is the permanent guard, an AST sweep
that fails the build if `install_default` grows a third caller.

An operator who already installed `general` keeps their `general`,
including every edit they made to it, across every future deploy. A
newly shipped default simply becomes something new to install — never
something that appears in their database uninvited.

**The settings assistant** (`settings-helper`, P3 Task 5) joins the
catalogue as a fourth agent, `as_tool=False`. It holds three read-only
tools — `settings.card`, `settings.overview`, `models.status` — and
answers only from what they return; its system prompt carries doctrine,
never a page list, so the index rides `settings.card`'s own schema and
cannot go stale. Its surface is the settings panel, never `/chat/`:
`agents.defaults.SETTINGS_SURFACE_SLUGS` names it as the settings-only
surface (owner ruling 2, spec §7), one line beside the spec it names,
and it is this catalogue entry like any other — no row appears until
somebody installs it.

## Test helpers: `agents/chat/tests/_helpers.py` imports from here, and that's a ruling

P2's rule is that `_helpers.py` modules are duplicated per **app**,
never imported across apps — `agents/runtime/tests/_helpers.py`
importing from `agents/tests/_helpers.py` is the one carve-out, because
that pair is "one app, not two". `agents/chat` (Task 3) is a second
Django app, which would make the letter of that rule demand a sixth
copy of `make_agent`/`make_conversation`/`make_turn`/`bind_chat_role`.

**RULING (2026-08-28): it does not.** The boundary that rule actually
polices is the **column** — `tools/rag` vs `tools/vision` vs
`models/registry` vs `agents` — because that is where one team's test
scaffolding becoming load-bearing for another's is a real hazard.
`agents/chat` is the same column and the same top-level package
directory as `agents/tests/_helpers.py`, and ships in the same commit
series as the module it borrows from. So `agents/chat/tests/
_helpers.py` imports the row builders and `bind_chat_role` from
`agents/tests/_helpers.py` and defines only what is genuinely its own
(the HTTP-layer queue doubles and the thread builders).

**The cross-COLUMN rule is unchanged** — no `agents/chat` test imports
from `tools/*/tests/` or `models/*/tests/`, and `foundation/ops/tests/
test_column_boundaries.py`'s guards keep that honest. A ruling that
lives only in a plan document is a ruling the next author re-litigates;
recording it here is what stops that.

## Workstreams

Two new tables. `Workstream` is a named scoped work area — `name`,
`description`, `instructions` (appended to the agent's system prompt for
every turn born inside it), `default_upload_placement` (`""` = ask
every time, `"universal"`, or `"contained"`), and the usual
`owner_kind`/`owner_key` pair every owned row in this app carries — a
stream is owned by COLUMNS, not a `User` FK, exactly like `Agent`,
`Flow`, and `Conversation` before it. Its name is unique per owner,
case-insensitively (`Lower("name")`, matching `uniq_agent_slug_ci`), not
globally — two owners may each have a stream called "Taxes". No slug
(nothing in code names a stream) and no `enabled` (`archived_at`
already covers "put it away").

`WorkstreamScopeEntitlement` is the stream's WALL: zero rows means no
narrowing (an unwalled stream costs exactly what a loose conversation
costs), and present rows narrow by intersection with the reader's own
grants, never by union. Its reverse accessor is **`scope_entitlements`,
deliberately not `entitlement_labels`** — the four existing label tables
(`ToolEntitlement`, `AgentEntitlement`, `FlowEntitlement`,
`DocumentEntitlement`) all share that name so `agents.visibility.
label_permitted_q` can be one function for any of them; a label says
"holders of this may reach this row," a wall says "narrow this row's
reach to this," and reusing the accessor would let a future
`label_permitted_q` call compile against a stream and silently answer
the wrong question. `set_by`/`set_at` are provenance, written and not
yet read by anything this task ships.

`Conversation.workstream` is a nullable `PROTECT` foreign key to
`Workstream` — null means loose (every conversation's behaviour before
this task, unchanged), a value means the conversation was born in that
stream, **for life**: no route in this column writes the column after
creation. `PROTECT`, not `CASCADE` — deleting a stream that still holds
conversations is refused, not silently emptied out from under whoever
owns them. A dedicated index (`agents_conv_ws`, on `workstream,
-updated_at`) makes a stream's own conversation list one index scan.

`Share.Target` gained a fifth member, `WORKSTREAM`, parsed by the same
`_int_or_none` its integer-pk siblings (`AGENT`, `FLOW`,
`VISION_OUTPUT`) already use — nothing else about `Share` changed.

Migration: `agents/migrations/0006_workstream.py`. No data migration:
`workstream` is nullable and every existing conversation is already
loose.

### The instructions block

A workstream's `instructions` reach the model appended to the agent's
system prompt under a labelled header, as a single system message — never
as a second one. Some engines collapse, reorder, or drop a second system
message; one message with two labelled parts behaves identically on every
engine and is what the operator sees when they read the prompt back.

The block is built by `agents.runtime.prompt._instructions_block(stream)`,
which adds the labelled header constant `_INSTRUCTIONS_HEADER` to the
stream's instructions. `build_messages` appends the block when
`conversation.workstream` is not null AND has non-blank instructions —
the existing rule that "a blank agent prompt emits no system message at
all" is preserved IN ITS OWN TERMS: the message is emitted when there is
SOMETHING TO SAY, and stream instructions are something to say. A stream
with blank instructions and a blank agent prompt still emits nothing.

A delegate (`agents.runtime.delegate.run_agent_tool`) **inherits the
scope but not the prose**: the stream's enforcement (the wall, the
entitlement intersection, the queryset filter) reaches the delegated agent
through `ToolContext.stream`, but the stream's prose instructions do not.
This is by design — an agent that is only reached as a tool is a private
implementation detail of another agent's flow, and its runner should not
read the parent's standing instructions; if the owner ever wants a
delegate to inherit the prose too, spec §24 concern 2 records a
one-line remedy: one call to `_instructions_block` in
`agents/runtime/delegate.py` where `ToolContext.stream` is already available.

### The clock

**Round 21, owner feedback:** *"can we inject the date/time into the
prompt so that its time aware ... which is incorrect and so the ai
should always have time stamps for chat components. This should also be
configurable in the settings but defaulted to on."* A model with no
statement of the present moment falls back on whatever its training
implies "now" is — and reported the operator's own current year as a set
of "future dates", which is the report that opened the round.

Two halves, **one switch** (`ChatSettings.time_aware`, default ON,
edited on `/chat/settings/`):

* **The date line.** `agents.runtime.prompt.time_aware_now_line()`
  appends ONE line to the system message, last, as its own paragraph:
  the day of the week, the date, the time and the UTC offset, from
  `django.utils.timezone.localtime()` — the platform's own `TIME_ZONE`,
  never a client-supplied zone — followed by a sentence saying the line
  is authoritative and that a date at or before it has already happened.
  `TIME_ZONE` reads `FARABUNKER_TIME_ZONE` (default `"UTC"`), so a box
  whose operator is not on UTC can state their own wall clock rather
  than one that is correct but not theirs.
* **The turn timestamps.** Each replayed USER/ASSISTANT/SYSTEM turn
  carries a compact `[YYYY-MM-DD HH:MM] ` prefix taken from its own
  `created_at` column, in the same zone the date line states.

**TOOL turns are never prefixed.** `agents/runtime/loop.py` appends the
pair `tool_turn_messages` returns at the moment a tool runs, unprefixed;
a timestamp added on replay but not on that live append would be exactly
the byte-level divergence `agents/runtime/prompt.py`'s one rule forbids.

**Delegated agents get the date line too** (`agents/runtime/delegate.py`).
There are TWO prompt builders on this platform, not one: `build_messages`
and the two-message list `run_agent_tool` assembles by hand. Both call
`time_aware_now_line()` — one helper, one toggle, no re-typed line — so
the wording, the timezone rule and the operator's switch cannot drift
between them. This is the ONE thing a delegate inherits: every other
exemption on that path (the stream's instructions prose, the attachments
block, the parent's history) is about the *parent's* context, and what
day it is is not the parent's context but a fact about the world. A
delegate's text reaches a user exactly like a root turn's does, so
"always" has to mean there too. The per-turn timestamps have no work to
do on that path — its list is two fresh messages with no replayed
history, and a delegate's own turns live at `depth >= 1` and never
replay into anything.

**Both values are server-generated**, from `timezone` and from a row's
own column — never from a header, a form field or user text — so neither
goes through the fence-neutralization the attachment blocks apply to
third-party bytes, which exists for text this platform did not write.

The "a blank agent prompt emits no system message" rule is preserved in
its own terms, exactly as the instructions block preserved it: the
message is emitted when there is SOMETHING TO SAY, and the date is
something to say. A blank prompt on a time-aware box therefore gets a
system message carrying the date and nothing else; with the setting off
and a blank prompt, still nothing.

### The seam: `agents/workstreams.py`

This is the THIRD `agents` name — beside `agents.contracts.*` and the
already-named seam `agents.entitlements` — that anything outside
`agents/` may import, and the only one `tools/` and `models/` may reach
for workstream questions. The closed three-name set is pinned by
`foundation/ops/tests/test_import_law.py::
test_tools_reaches_agents_through_contracts_entitlements_and_workstreams_and_nothing_else`.
It imports nothing of `tools/` — a second, narrow guard
(`::test_agents_workstreams_imports_no_tools_package`) pins that on top
of the broad sweep, because this is the module whose accidental
widening would be least visible in review.

It imports `agents.visibility`, **never the reverse** — one-way, so the
two agents-side stream modules cannot cycle when spec §12 puts
`stream_access` here and `share_workstream`/`workstream_taint_ids` there.

Its whole public surface:

- `workstream_scope(principal, workstream_id) -> WorkstreamScope | None`
  — the pure scope value, or `None` when this principal may not be in
  that stream or it does not exist (the caller cannot tell the two
  apart, and answers 404 to both). `pinned_file_ids` always comes back
  `frozenset()`: only `tools/rag/workstreams.py::scope_with_pins` fills
  it, because the pin table lives in `tools/rag` (author decision 6,
  spec §6.2).
- `wall_ids(workstream_id) -> frozenset[int]` — the stream's wall.
  PUBLIC, not `_wall_ids`: it is the wall's one canonical reader, called
  by both the scope builder above and, through `agents.entitlements.
  wall_for`, the planner's entitlement intersection in
  `agents/entitlements.py::tool_access_for`/`model_access_for` (the
  `wall=` keyword both take) — so a leading underscore would misdescribe
  it. Ruling A (spec §23.A): on a box where
  `identity.access.accounts_on()`
  is False this returns `frozenset()` **without reading
  `WorkstreamScopeEntitlement` at all** — the same "an open box never
  runs a permission query" posture the other entitlement tables keep,
  and what stops a wall written under `enterprise` from bricking a
  stream after a posture switch back to `open`. The rows survive,
  dormant, and bind again the moment the posture returns.
- `set_upload_placement_default(principal, workstream_id, placement) ->
  bool` — the door onto `agents.visibility.set_workstream_upload_default`
  for a `tools/rag` caller; the predicate and the audit row live with
  the writer, not here.
- `workstream_entitlement_cascade(entitlement_id, *, commit) -> int` —
  this column's third answer to "an entitlement is being deleted": the
  stream's wall rows AND (WS-2) its taint tags, at both levels.
  `commit=False` counts (so a delete confirmation can name the number
  first); `commit=True` removes them, writes `WORKSTREAM_UNTAINTED`/
  `CONVERSATION_UNTAINTED` audit rows for the taint half (so the trail
  shows a tag left, not only that it once arrived), and returns the same
  count. Registered from `agents/apps.py::ready()` as a dotted-path
  string under the key `"agents.workstream_entitlements"`, label
  `"Workstream scopes and taint tags"`.
- `panels_for(principal, workstream_id) -> list[dict]` — every
  registered `WorkstreamPanel`'s data, in registration order. Never
  raises (author decision 11): a provider that raises at render time
  degrades to `{"ok": False, "data": {}}` for that one panel rather than
  a 500 for the whole stream page, the same posture `agents.chat.
  pickers.chat_picker_options` already takes for the model picker.

The panel registry itself (`agents.contracts.workstreams.
WorkstreamPanel` / `register_workstream_panel` / `all_workstream_panels`)
is how a column that owns rows the stream page must display announces
itself, instead of `agents/workstreams.py` importing it: `provider` is a
dotted path, resolved by `import_string` at render time and never
imported here. **Register in the same commit as the handler** — the
resolver never swallows an `ImportError`, so a registration landing
before its module exists would make every stream page raise.

**Its sibling, one slot rather than a keyed registry** (round 11, owner
feedback): `agents.contracts.attachments` / `agents.attachments.
attached_documents` answers "what has this conversation attached" the
identical way — `tools.rag` registers `tools.rag.access.
attached_documents` as a dotted path from `tools/rag/apps.py::ready()`,
`agents.attachments.attached_documents` resolves it at call time and
never raises (the same author-decision-11 posture as `panels_for`
above), and both the conversation page's attachments strip and the
running turn's own prompt (`agents.runtime.prompt.build_messages`) read
through this ONE resolver rather than two ad-hoc queries. One slot, not
a dict keyed by many: there is exactly one kind of "what is attached"
fact, uniquely owned by `tools.rag` — no second column will ever
register a second answer to the same question. A carried image rides
this SAME seam one step further: `agents.attachments.
attachment_image_path` resolves its on-disk path through the artifact-
file registry rather than this provider, and `agents.runtime.prompt.
native_media_types` gates whether it reaches the model as a real
`ImageBlock` at all (ADR 0015's 2026-09-20 amendment).

### Taint: the tables, the stamp, and the extension point

Two more tables, both additive-only in v1 (rows are created, never
deleted, except by CASCADE and by the entitlement-delete cascade above).

`ConversationTaint` (`conversation`, `entitlement`, `first_turn`, `at`;
reverse `taint_tags`) hangs off the **conversation**, not the stream, so
a loose conversation accumulates tags exactly like a stream one — nothing
reads them in v1, which is what makes conversation-level share gating a
later change with no back-fill. `WorkstreamTaint` (`workstream`,
`entitlement`, `first_conversation`, `first_turn`, `at`; reverse
`taint_tags`) is the union of its conversations' tags, **materialised**,
not derived: both share gates read it directly rather than joining every
conversation in the stream on every non-owner view. `first_turn` is a
plain `BigIntegerField`, not a `Turn` FK — it is evidence for "why did
this share go dormant", and a real FK would let deleting a turn delete
the record of what that turn brought in.

`agents/runtime/taint.py::stamp_turn_taint(turn, conversation, artifacts,
*, actor=None) -> frozenset[int]` is **the one writer of DERIVED taint** —
the only function that decides something is newly tainted. It is called
from exactly one place: inside the `transaction.atomic()` that creates a
TOOL TURN in `agents/runtime/loop.py` — the tool turn, never the
assistant turn, and never inside `_finish`. Two other call sites touch
these tables without deriving anything: `duplicate_conversation` (below)
COPIES a conversation's existing tags onto its copy, and
`workstream_entitlement_cascade` (above) DELETES every row naming an
entitlement that is itself being deleted — one carries a fact forward,
the other retracts one that no longer holds, and neither computes a new
answer to "is this newly tainted". `_finish` is
reached only on the success path, while tool turns are created and
committed as they run; stamping there would leave a retrieved document's
material committed and rendered with no taint row the moment the
following assistant call raised. The stamp therefore rides a write that
already happens (`Turn.artifacts`, already deduped and validated),
costing nothing extra beyond one `atomic()` wrapper that did not exist
before this phase. A turn whose `artifacts` name no kind with a
registered resolver — the overwhelmingly common case, a turn that called
no retrieval tool — touches the database not at all.

The extension point is `agents.contracts.artifacts.ArtifactLabels(kind,
resolver)` / `register_artifact_labels` / `labels_resolver_for`, living
beside the artifact vocabulary itself (a rule-1 pure leaf — a dataclass
and a dict, no Django). `resolver` is a dotted path,
`(pks: frozenset[int]) -> frozenset[int]`, resolved at stamp time so
`agents/` never imports `tools/`. `tools/rag/apps.py` registers
`ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for")` —
today's only registration. A kind with no registered resolver
contributes nothing, silently: when a generated image becomes a labelled
thing, `tools/vision/apps.py` registers its own `ArtifactLabels("output",
...)` and every turn that returned one starts tainting, with no change to
`agents/runtime/taint.py`, no change to `_finish`, and no migration —
`agents/tests/test_taint.py` pins the claim with a fake resolver rather
than leaving it a hope.

`agents/visibility.py::duplicate_conversation` copies a conversation's
`ConversationTaint` rows onto its copy, row for row, inside the same
`transaction.atomic()` as the turn copy — **always**, not only for a
stream conversation: a copy that dropped the tags would launder a loose
conversation's labelled material exactly as it would a stream one's.

Migration: `agents/migrations/0007_workstream_taint.py` — the two new
tables and `Conversation.consolidated_through_index` /
`Conversation.consolidated_at` (consolidation's own two columns, added in
the same migration since both are WS-2 additions to `Conversation`, ahead
of the consolidation job that reads and writes them). No data migration:
every column is nullable and every existing row already means "never".

Design: `docs/superpowers/specs/2026-09-03-workstreams-design.md`. The
rest of this app's design: `docs/superpowers/specs/
2026-08-25-agents-and-tools-design.md`.
