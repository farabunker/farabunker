# ADR 0015 — The agent layer: one tool contract, one turn, one door

**Status:** Accepted
**Date:** 2026-08-29

## Context

Before this build the repository had three top-level packages — `core/`
(pure, importable everywhere), `console/` (operator tooling), and
`modules/` (capability-scoped feature services) — and the boundary law
between them was two sentences in ADR 0010 §2, amended once. RAG and
image generation were each their own island: each owned its own views,
its own job kinds, and its own way of being asked a question, and
nothing existed that could ask both. `chat.converse` was a role name in
ADR 0010's 2026-08-22 amendment and in ROADMAP Phase 1.6 — declared,
never registered, with no code behind it. The two chat tables
(`ChatSession`/`ChatMessage`) had shipped in the RAG module and were
write-only: one production writer, no reader anywhere.

The owner asked for an agent that can use the platform's own
capabilities — search the library, answer from it, look at what models
are installed, generate an image — through **one** contract rather than
one integration per feature, and for `/chat/` to be a permanent product
surface rather than a demo.

Two facts already in the tree constrained every part of the design:

1. **One door.** ADR 0013 §1 made the queue the only way a model runs.
   An agent is a model consumer like any other; it does not get a second
   seam.
2. **Sequential mode is the shipped default.** `JobSettings.
   memory_budget_bytes` ships `null` (ADR 0013 §4), which
   `plan_admissions` reads as "at most one job on the whole machine."
   A running job therefore blocks every admission — which decides, on
   its own, what a tool is allowed to do (§5 below).

This ADR records what four PRs built — P0's regroup, P1's tool contract,
P2's turn runtime, P3's `/chat/` and flows — and is written in the fifth.
It is a record, not a plan: every claim below is checkable against the
tree it describes.

## Decision

### 1. The repository is four columns, and the fourth is `foundation/`

`core/`, `console/`, and `modules/` are gone. Four columns replace them,
each named for what it holds rather than for who operates it:

```
tools/       rag/  vision/  home/                    feature apps that register tools
models/      contracts/  registry/  queue/           engines, bindings, gateway, the queue
agents/      contracts/  runtime/  chat/             the tool contract, the turn, /chat/
foundation/  format.py  files.py  ops/  setup/       shared leaves + two feature-less apps
```

**The fourth column is not called `platform/`.** The owner named it
that; the name is the one thing about it that changed, and this ADR
**re-confirms** ADR 0010 §2's ruling (`0010:128-133`) rather than
reversing it. `platform` is a Python standard-library module name, and
`manage.py` puts the repository root on `sys.path[0]`, so a `platform/`
package at the root wins over the stdlib for every bare
`import platform` in the environment — including the ones inside
installed dependencies, which cannot be edited to guard against it.

One leg of the spec's argument is **narrowed here**, and this is the
only place it is narrowed: the spec offered "a `platform/` package
breaks `manage.py collectstatic`, which the Docker image runs" as a
third proof. The image does not run it — `Dockerfile:32`'s `CMD` is
`migrate` then `uvicorn`, no `RUN` line collects, and no
`compose*.yaml`, `deploy/`, `scripts/`, or `config/` file contains the
word. The shadowing argument stands on its other two legs (stdlib
collision, `sys.path[0]` precedence), which is why the conclusion is
unchanged. ADR 0010 §2's own "Why not `platform/`" never mentioned
`collectstatic`, so there is nothing there to narrow.

**`STATIC_ROOT` is deliberately unset.** `django.contrib.staticfiles` is
installed (`config/settings.py:241`) and `STATIC_URL` is set (`:337`), but
nothing in this repository runs `collectstatic`: the image's `CMD` is
`migrate` + `uvicorn` (`Dockerfile:32`), no `RUN` layer collects, and no
compose file, deploy script, or management command mentions it.
`STATIC_ROOT` is required by `collectstatic` alone; setting it would name
a directory nothing writes to and nothing serves from, which is a setting
that documents an operation the platform does not perform. It is added by
whoever first puts a real static-serving story in front of the app — a
web server, a collected tree, a cache header policy — in that same
change, not before. `foundation/__init__.py:10` still states the
`collectstatic` claim unqualified rather than conditionally, the way
`foundation/README.md:41` now does — it is a `.py` comment and therefore
out of a docs-only phase's budget: not fixed here; it travels with the
next change to that file.

**The import law is three rules**, replacing the two-sentence version:

1. **Pure leaves are universally importable.** `foundation/format.py`,
   `foundation/files.py`, everything under `models/contracts/`, and
   everything under `agents/contracts/`: no Django models, no views, no
   database, no import of any non-pure module. Any column may import
   them, in any direction. This is what `core/` meant, split so the pure
   code sits beside what it serves.
2. **Django apps are column-private**, with exactly **two** named
   exceptions. A `tools/*` **or** `agents/*` app may import
   `models.registry.bindings`, and only that module —
   `models.registry.models` and `models.registry.views` stay off-limits,
   carried over in substance from ADR 0010's dependency-direction
   amendment (`0010:534-537`). The second exception runs the other way:
   `foundation.ops` may import `models.queue.models`, and only for the
   active-work refusal that stops a backup while jobs are RUNNING
   (`foundation/ops/backup.py:51-52`, with the carve-out written out at
   `:164-169`).
3. **Cross-column *work* goes through a seam, never an import.** Three
   seams: the queue (`models.contracts.queue`), the gateway
   (`models.contracts.gateway`), and the tool registry
   (`agents.contracts.tools`), whose `ToolSpec.runner` is a dotted-path
   string resolved at call time. Rule 3's third clause is what keeps
   `agents/` from importing `tools/` at module scope, exactly as
   `AppConfig.ready()` already registers a job kind without importing
   its handler. There is no cycle: `tools/*` imports `agents.contracts`
   (pure, rule 1); `agents/runtime` reaches `tools/*` only through a
   string.

The rules are enforced, not documented:
`foundation/ops/tests/test_import_law.py` names the forbidden modules
and the single allowed exception as literals (`:53`, `:60`), and
`foundation/ops/tests/test_column_boundaries.py` sweeps the registration
and runtime modules.

**The move renamed no database table.** Every `AppConfig` sets `label`
explicitly, so `rag`, `vision`, `inference`, `jobs`, `agents`, `chat`,
`ops`, and `setup` all survive the package move untouched — and
`foundation/ops/tests/test_app_labels.py` pins it against a
hand-typed literal list read off the pre-move tree, deliberately not one
recomputed from `app_label` at test time, because a recomputed pin
compares a value to itself.

### 2. One tool contract, in one pure leaf, is what every feature registers against

`agents/contracts/` is three modules — `tools.py`, `toolschema.py`,
`artifacts.py` — and it is a rule-1 pure leaf. It holds `ToolSpec`,
`ToolResult`, `ToolContext`, `Principal`, `StepBudget`, and
`ToolRefused`, plus the module-level registry: `register_tool`
(`agents/contracts/tools.py:319`) and the read side `all_tools` /
`get_tool` / `grantable_tools` / `granted_tools`. Registration is
idempotent by construction — the same key registered twice overwrites,
the same shape `register_role` and `register_job_kind` already have — so
re-importing a registration module is safe.

**Purity is pinned by a subprocess, not by a docstring.**
`agents/contracts/tests/test_purity.py` imports every module in the
package with no `DJANGO_SETTINGS_MODULE` set at all and fails on any
`django` name leaking into `sys.modules`. A leaf that is only *asserted*
to be pure stops being pure the first time somebody adds a convenient
import; this one goes red instead.

**Nine tools are registered on a vision-enabled install, from four
`AppConfig.ready()` bodies:** `rag.search` / `rag.ask` / `rag.ingest`
(`tools/rag/apps.py:114-116`), `models.status`
(`models/registry/apps.py:60`), `vision.operations` / `vision.generate`
(`tools/vision/apps.py:75-76`), and `agent.library` /
`agent.illustrator` / `flow.run` (`agents/apps.py:85`, `:95`). The count
carries a caveat and the caveat is part of the claim: the two vision
tools register only while the `vision` feature flag is on — the flag
gate is one early return above those two lines, so with the flag off
there is no vision role, no operations, no job kind, and no tools. Seven
is the flag-independent floor.

Every one of those four bodies registers without importing an
implementation module. A `ToolSpec.runner` is a string; each
registration module imports nothing heavier than the pure contracts at
module scope, and every runner does its own service imports lazily
inside the function body. That is what keeps `ready()` DB-free and
light with nine tools registered, and it is the same discipline the job
kinds already followed.

**A mutating tool is registered but not grantable.** `rag.ingest`
declares `mutates=True`, so `grantable_tools()` — a one-line filter on
`spec.mutates` (`agents/contracts/tools.py:353-361`) — leaves it out of
the set an `Agent` row may name. The tool still exists, is documented,
and is tested; it is simply not something a caller may be granted before
there is an identity to grant it to. That function's own docstring cites
ADR 0010:266-276, which is where the rule comes from.

### 3. A tool's wire schema is rendered by adapters, from one builder

`openai_tool_dict` and `mcp_tool_dict` (`agents/contracts/toolschema.py:89`,
`:105`) render the same `ToolSpec` for two different callers: the LLM's
tool-calling API, and MCP's `tools/list`. Both take their parameter
object from the same private `_input_schema`, and `name` is
`wire_name(spec.key)` in both, because neither function-name grammar
admits a `.`. Neither serializes `runner` or `describer` —
implementation, never contract.

**A drift test pins them equal**, parametrized over every registered
spec rather than one hand-written example, so a tool added in a later
phase is covered the day it is registered
(`agents/contracts/tests/test_toolschema.py::TestTheTwoAdaptersCannotDrift`),
with a non-vacuity assertion so an empty registry cannot make it pass by
looking at nothing.

**The MCP adapter shipped in P2, before any MCP edge exists, and that
is the point.** The shape a tool declares must not depend on who is
asking, or an external caller and an internal prompt come to disagree
about what a tool accepts and the disagreement surfaces as a validation
error nobody can reproduce. Writing the second adapter while there is
one builder to share is cheap; writing it after an edge exists is
writing it against something to drift from.

### 4. An artifact is a reference, never bytes

`agents/contracts/artifacts.py` declares three kinds — `output:<id>`,
`input:<id>`, `document:<id>` — with one parser and one reverse map to
the URL name that serves each. A tool call is JSON (ADR 0012:140: "a
future chatbot tool call is JSON, so neither can carry an upload
object"), so bytes cannot travel inside one. An agent therefore
*references* an artifact that already exists and never receives an
upload; a tool that produces a file hands back its reference, and the
chat template reverses the matching URL. Uploading into a conversation
is a chat-view feature, not a tool-contract feature, and is not built
(named gap G12).

### 5. One chat turn is one queue job — and tools run INLINE inside it

**This is a new decision, and it is stated as one.** ADR 0013 nowhere
forbids a job from enqueuing a job and waiting on it; grepping the queue
for such a rule returns nothing. The deadlock is *implied* — by
sequential mode, by "admissible only into a genuinely idle machine"
(`0013:162-163`), and by the no-backfill proof whose stated
justification is "it makes deadlock structurally impossible"
(`0013:99-106`), which a job-waits-on-job breaks. Implied is not
recorded, so it is recorded here.

`agent.turn` is a job kind like any other, registered from
`agents/apps.py:57` with a planner, a handler, a summarizer, and — not
optionally — an `on_terminal` hook, because a turn has a durable
side-effect that predates the job (the placeholder assistant row) and is
exactly the stranded-row condition ADR 0014 §8 built that hook for. It
declares `exclusive=True` honestly, since one turn may load a chat
model, then an embedding model, then an image checkpoint in sequence.

The rule every tool runner obeys, in one sentence:

> A tool runner must never block on a queue job. It may enqueue one and
> return its id; it must never call `get_job` in a loop, and it must
> never call `enqueue` for work whose result it needs.

`rag.ingest` is the fire-and-forget case the rule allows: it enqueues a
re-ingest and returns the queue job id without waiting. In sequential
mode the child simply sits queued until the turn finishes and then runs;
nothing blocks, so nothing deadlocks. The rule is swept for in source,
over `tools/*/tools.py` and the runtime package
(`foundation/ops/tests/test_column_boundaries.py`'s `RUNTIME_MODULES`),
with a per-tool pin beside the runner it applies to
(`tools/rag/tests/test_tools.py:362`). A **view** is not a tool runner:
`turn_status` calls `get_job` once per request and returns, holding no
execution slot, and the sweep's list excludes it explicitly rather than
by accident.

Inline is not a new seam. Every tool calls a function the platform
already called synchronously from inside a job handler — `rag.ask` calls
`answer_question`, which `tools/rag/jobs.py::run_ask` calls;
`vision.generate` calls `services.submit_job`/`wait_for`, which
`tools/vision/jobs.py::run_generate` calls. (The tool runners carry those
same two names in `tools/rag/tools.py` and `tools/vision/tools.py`; the
paths above are the job handlers.) After this build there are two
worker-side callers of each instead of one, both still under the one
door.

**Memory rotation is not weakened.** The turn is a job; it declares
every model it may touch to the planner at enqueue time
(`agents/runtime/jobs.py::plan_turn`, walking the agent's own chat role,
its granted tools' roles, the delegation closure, and its visible flows'
step roles); and because it is exclusive, `Worker._evict_to_match_plan`
evicts to match that plan, uncapped, before the turn launches. Nothing
in the agent layer calls a model outside the queue.

### 6. A delegate spends the ROOT turn's budget, and depth is capped by omission

An agent may be offered to another agent as a tool: `agent.library` and
`agent.illustrator` are `ToolSpec`s built from code
(`agents/resident.py::agent_tool_specs`, over `agents.defaults.
DEFAULT_AGENTS`), never from rows, so `ready()` still touches no
database. All of them share one runner and learn which spec invoked them
from `ToolContext.tool_key`.

Three properties make delegation safe, and the depth cap is only one of
them (`agents/runtime/delegate.py:1-24`):

- **The budget is SHARED, not nested.** A delegate spends from the root
  turn's `StepBudget` — the same mutable object, reached through
  `ToolContext` — so total LLM calls per turn stay bounded by
  `Agent.max_steps` however the delegation tree is shaped. This is the
  real guard.
- **The depth cap is enforced by OMISSION.** `MAX_AGENT_DEPTH = 2`
  (`agents/limits.py`); at the last legal depth the `agent.*` specs are
  simply left out of the delegate's tool list, so the model is never
  offered a tool it would then be refused for using. The `ToolRefused`
  branch is belt-and-braces for a malformed call, not the mechanism.
- **A delegate gets a fresh message list** — its own system prompt plus
  the task string, never the parent's history. A delegate is a
  subroutine with an assignment, not a second participant in the
  conversation; replaying the parent's history would hand it context
  nobody asked it about and make its budget spend unpredictable.

It runs inline, in the same job. It enqueues nothing and waits on
nothing — §5's rule, applied to the runner most tempted to break it.

### 7. A flow is a row, and it runs through the one registered `flow.run`

Owner ruling 1 (2026-08-28): flows are rows, like agents. `Flow` carries
a CI-unique immutable `slug`, `inputs` JSON, `steps` JSON, `enabled`,
and owner columns.

Rows cannot become registered specs, because `AppConfig.ready()` may
touch no database — but that is an argument about the *tool*, not about
the *flow*. **One** generic tool dissolves it: `flow.run`
(`agents/runtime/flowtool.py`) is registered once, with a `flow`
**choice** param whose `choices` are empty at registration and filled
**per turn** from `visible_flows(principal)`
(`flowtool.narrowed_flow_spec`). The model reads an `enum` of real slugs
rather than guessing at a free string, and N flows exist without N
registered specs. `choices=()` is not a placeholder: it is the
documented shape for a choice something else supplies, and the schema
builder emits no `enum` for one, so an un-narrowed spec is honest about
knowing nothing rather than claiming an empty set of flows.

The runner (`agents/runtime/flow.py`) loads the row by slug at call
time and drives every step through the same
`agents.runtime.invoke.invoke_tool` an agent's loop uses — same
validation floor, same audit row, same shared budget, same depth, same
five outcome classes. That is what makes "a flow is the tool contract
with no LLM" a fact about the code rather than a claim about it. A flow
spends no steps, because a step is an LLM call and a flow makes none;
what it does obey is the turn deadline, checked before each step, and a
deadline reached mid-flow is `degraded`, not a failure — the steps that
ran really ran.

The reference grammar is `$input.<key>`, `$steps.<N>.text`,
`$steps.<N>.data.<path>`, and `$steps.<N>.artifacts.<i>`. One regex
checks it at declaration time (`Flow.save()` through
`agents.defaults.validate_flow_json`) and this runner resolves the same
shape at run time against the same JSON, so a reference spelled any
other way was already refused when the row was saved. A reference that
does not resolve raises and names itself; `None` is never silently
substituted, because a silently-empty prompt is the exact failure a flow
exists to prevent.

### 8. Shipped defaults are a catalogue an operator adopts, never a deploy step

Owner ruling 2: **no code path writes a row the operator did not ask
for.** `agents/defaults.py` is pure data plus one row writer:
`DEFAULT_AGENTS` and `DEFAULT_FLOWS` describe what this platform
*offers*, and `install_default(kind, slug, principal)` is the one
create-if-absent, idempotent way a row comes from one of them. A page
that needs a resident it does not have renders an honest empty state
with a CSRF-protected "Add the default X" button that works with
JavaScript off; `manage.py install_defaults [--reset <slug>]` is the
same thing for a person who prefers a shell. `manage.py sync_agents` —
which re-applied every declaration to its row on every deploy that ran
it — is **deleted**: `agents/management/commands/` holds `agent_turn.py`
and `install_defaults.py`, and nothing else.

Owner ruling 3: **an adopted row is the operator's.** The resident
edit-lock is gone from `Agent.save()`; `resident=True` is now an
**origin marker**, not a permission. `slug` stays immutable, because it
is the key three things resolve against. `install_defaults --reset
<slug>` is the one path that ever rewrites an existing row, and it names
its target.

The catalogue is not a registry. A `FlowSpec` is used for exactly two
things — rendering the offer, and validating row JSON through the same
validator `Flow.save()` calls — and for nothing at run time: `flow.run`
loads the **row**.

### 9. Grants attach to a PRINCIPAL, and one function decides availability

`granted_tools(principal, tool_keys)` (`agents/contracts/tools.py:364`)
is the only place in the codebase that answers "may this caller call
this tool". It preserves the caller's order, de-duplicates, and makes
exactly two drops: a key absent from the registry is dropped **and
logged** (normal, not exceptional — a feature-gated tool with its flag
off, or a tool that ships in a later phase), and a key whose spec is
`mutates=True` is dropped outright (§2). Both the enqueue-time planner
and the run-time loop call it, which is what makes the planner's
declared model set and the model's offered tool list the same filtered
set rather than two that agree by convention.

`tool_keys` is an argument **today**, because grants live on the `Agent`
row. When grants move to their own table behind Identity & Auth that
argument disappears and the principal alone decides; the call site and
the return type do not change. That is the entire reason the principal
is the first parameter of a function that does not yet read it.

Retrieval keeps exactly **one** filter point,
`tools/rag/retrieval.py::retrieve_nodes`, and all three of its callers
go through it — `answer_question`, the retrieval-only search page, and
the `rag.search` tool (`tools/rag/tools.py:214`). A visibility scope,
when tenancy lands, is one more argument *there*; it is never a second
copy of the filter inside a runner, which is how two surfaces come to
disagree about what a caller may see.

### 10. The open box has a named principal, an owner column, and one filter per kind

Owner ruling 4: accounts are off, and that is a posture with a name
rather than an absence.

`agents/chat/principal.py::principal_for_request` is the only
request→principal point in the codebase. In open mode it returns
`OPEN_PRINCIPAL` — `Principal("open", "box")`, declared once at
`agents/contracts/tools.py:126` beside the kind it is built from, and
imported (never re-constructed) by `manage.py install_defaults`, so a
row installed from the shell and one installed from the page are owned
identically because the same actor installed them. `"open"` is a real
member of `PRINCIPAL_KINDS`, not a `None` special case: a box with no
accounts has an answer to "who is acting", and that answer is worth
storing.

`settings.ACCOUNTS_REQUIRED` (`config/settings.py:36`) defaults to
False, and what `True` does today is raise `NotImplementedError` naming
the Identity & Auth phase (`agents/chat/principal.py:36-40`). A setting
whose `True` branch quietly returned an unauthenticated principal would
be a security hole wearing a setting's name.

`Agent`, `Flow`, and `Conversation` all carry `owner_kind`/`owner_key` —
a `Principal`'s two fields, stored flat, each with its own index — and
**every create stamps the acting principal**, including one adopted from
the button. A row written with blank owner columns is a row a later
filter cannot reason about, and backfilling one is a migration nobody
has the information to write.

`agents/visibility.py` holds `visible_conversations`, `visible_agents`,
`installed_agent_slugs`, and `visible_flows` — the four functions that
return everything in open mode and are where a real filter goes when
accounts arrive — plus `owner_fields` and the one `create_conversation`
that stamps them, six functions in all. They are asked by the **page**
and by the **runtime**: `flow.run`'s per-turn narrowing asks
`visible_flows` with the acting principal, so flipping this posture
changes what an agent can *run*, not merely what a page lists. A guard
fails the build if any module under `agents/chat` reaches
`Conversation`/`Agent`/`Flow` `.objects` directly. In open mode all of
this returns exactly what a hard-coded version would have returned,
which is precisely why it was worth building now: Identity & Auth edits
six functions instead of auditing a package.

### 11. Every tool call writes its own row, whoever the caller is

`ToolInvocation` (`agents/models.py:302`) is its own table, referenced
*by* `Turn`, never a set of columns *on* it — because an external MCP
`tools/call` has no conversation and no turn and must land in the same
table unchanged. That is only possible because the conversation table
does not own the row.

`agents.runtime.invoke.invoke_tool` writes one row per call, before the
runner runs, with `outcome=ERROR` as a placeholder it overwrites once it
knows better. Two fields decide how a row reads, and `outcome` is only
one of them: `finished_at IS NULL` means **running**, whatever `outcome`
currently says, and every reader goes through
`agents.runtime.audit.invocation_state` rather than reading the column.
Because a row is created before the work, a crashed call would otherwise
leave one open forever — observed live on 2026-08-28, when a delegate's
invocation was still open as the database entered recovery — so
`audit.close_open_invocations` is called from both terminal paths: the
handler's own `except`, and `on_turn_terminal` for the case where the
handler never started. `outcome` is a closed class of five values, not a
message, so a later report — a per-principal rate limit, a failure
dashboard, an MCP error mapping — can be built on it without parsing
prose. P3 added `queue_job_id`, so an invocation that enqueued work
(§5's fire-and-forget case) names what it enqueued.

### 12. `/chat/` answers 202-and-poll, refuses before it writes, and works with no JS

`POST /chat/c/<uuid>/turn/` has two answers and one path. With
JavaScript: `202` and a JSON body carrying the `status_url` the poller
must use — never a URL the script builds itself. Without it: a redirect
to the thread carrying `?pending=<turn_id>`, which the page renders as a
pending turn and the operator refreshes. `/vision/`'s generate view
already answered exactly this way, for exactly this reason.

`GET /chat/turns/<id>/` always returns 200 for a readable turn; queued,
running, done, failed, and cancelled all report their state in the
**body**. Exactly two non-200s exist: 404 for a turn that does not
exist, and 503 for a queue that cannot be read at all. **It reports
`Turn.State` values and never queue states** — the queue says
`succeeded`, a turn says `done` — and it reads the turn row first,
consulting the queue only for a turn still in flight, because a queue
row is pruned on every enqueue and can vanish from under a card that is
still polling. The body builders are a dict keyed by state, and a test
pins its key set equal to `Turn.State`'s, so a sixth state cannot ship
with no body builder behind it.

**A refusal is preflighted before anything is written.**
`agents/runtime/preflight.py::preflight_turn` (`:104`) resolves the
agent's chat model, checks it can do what the agent needs, and writes
nothing. It is the one rule, shared by all three callers: `manage.py
agent_turn` (`:130`), `/chat/`'s turn creation
(`agents/chat/service.py:125`, which turns a refusal into a 503 before
either row is written), and the thread GET
(`agents/chat/views/thread.py:62`, which turns the same refusal into a
banner and still renders 200). Computing it with the same function over
the same `(agent, connection)` pair is also what makes a
granted-but-unregistered tool's note survive the no-JS redirect: the
thread's own GET recomputes it identically.

### 13. `ChatSession`/`ChatMessage` are retired: the seam survives, the parameter does not

Both tables were write-only. The only production writer was
`answer_question`'s `session_id` branch, and the only shipped way to
reach it was a management-command flag; nothing ever read the rows.
They were dropped outright in P2 —
`tools/rag/migrations/0013_retire_chat_tables.py`, a `DeleteModel` pair
rather than a data migration — alongside the `session_id` parameter
itself. Inventing a migration into `agents.Turn` was considered and
rejected: it would fabricate conversations that never had an agent, a
tool call, or a depth, and a fabricated history is worse than none.

Conversation memory is `agents.models.Turn`
(`agents/migrations/0001_initial.py`), which records the tool call, its
arguments, its data, its artifacts, and its delegation depth — none of
which a two-column message table could hold, and all of which belong to
the agent column rather than the RAG one. **The seam survives; the
parameter does not:** `rag.search` calls `retrieve_nodes` and `rag.ask`
calls `answer_question`, exactly as ADR 0010's agent amendment requires.

That amendment already stands inline at `0010:282-293` and is not
restated here; two records of one decision are what an amendment exists
to prevent. The retirement itself is recorded in two places doing two
different jobs. `docs/OPERATIONS.md:23-31` is the **operator-facing**
record: it names the drop, names the migration, and says what a dump
taken before that migration does on restore — the tables come back
harmlessly, the next `manage.py migrate` drops them again, and there is
no data migration into `agents.Turn` for their rows. This decision is
the **architectural** record: why the tables went, and what replaced
them.

### 14. Hosted-API engines are designed-for, and the prior runs against them

A hosted-API engine would need no framework change: an operator
registers a `ModelConnection` with that engine, an endpoint, and a model
id, binds it to a role, and the gateway hands the adapter its `config`.
Nothing in bindings, gateway, roles, or the console would change.

**None of that makes it precedented.** Hosted-API engines are unbroken
ground with a strong prior against them, and ADR 0010 is **not** cited
here as precedent, because there is none: no ADR, and neither
`ARCHITECTURE` nor `DEV`, mentions API keys, credentials, or hosted
engines at all. The standing rules point the other way — "Default-deny
egress" (`docs/ARCHITECTURE.md:62-63`) and "No implicit phone-home. No
telemetry, license checks, model downloads, or crash reports leave the
box" (`:67-68`), two of the rules that hold in **every** posture profile
(`:60-68`), and "the platform itself never pulls a model"
(`0010:182-185`).

Stated as a constraint on any future adapter: it is **forbidden** in the
`airgap` and `isolated-lan` postures and possible only inside a
deliberate, operator-initiated `gated-sync` window; enabling one is an
operator decision recorded in the posture profile, never a default. And
a credential is a **pointer** in `ModelConnection.config`
(`models/registry/models.py:83`), resolved by name from a secrets store,
never the secret itself — because `foundation/ops/backup.py` dumps that
table (`_TABLE_MODELCONNECTION`, `:64`), and a secret in that column is
a live credential in every backup archive.

### 15. A tool and its page ask the engine the same question, through the same seam

The vision track built the R1/R2 data contract, and the agent layer
consumes it unchanged — which is the test of whether a tool is a real
peer of a page or a second implementation of it.

`ignored_params(operation_key, config)` is an optional engine member
(`models/contracts/engines/base.py:402`), declared beside
`loaded_footprint` (`:426`) and `unload` (`:435`) and read the same
defensive way: every caller reaches all of them through `getattr`, so an
adapter that predates a method degrades to "no opinion" rather than
`AttributeError` (`base.py:381-385`, `:418-421`). It reports the params
an operation's schema declares that *this* engine's graph cannot honour,
mapped to the operator-facing reason — the negative twin of
`param_defaults` — so a console can disable a field and say why instead
of accepting a value and silently dropping it.

`services.fill_engine_blanks(operation, params, resolved, keys=None)`
(`tools/vision/services.py:313`) is the **one** filler for engine-owned
blanks, and both surfaces use it: the page
(`tools/vision/views.py:762-766`, passing the ignored keys it just
re-derived) and the tool (`tools/vision/tools.py:403`, through
`_fill_engine_params`). `services.operation_catalog`
(`tools/vision/services.py:485`) is the single truth for which
operations this install can actually run, and `vision.operations`
renders its cue verbatim — `" — not runnable here: "` followed by the
catalog's own `unsupported_reason` (`tools/vision/tools.py:263`) —
rather than composing a second sentence that could disagree with the
page's.

PR #64's hygiene is part of this contract, not incidental to it:
`run_generate` preflights **once** and threads the resulting `resolved`
binding into both the fill and the submit
(`tools/vision/tools.py:489-498`), so the binding is resolved once
rather than three times; what remains is `submit_job`'s own health
round trip, named as G8. And an ignored FILE param is neither staged
nor forwarded: the view's `uploads`/`carried` sets already exclude every
ignored file key, and a second, cheaper line of defence pops such a key
out of Django's own read of `request.FILES` — the case a hand-crafted
POST that re-enables a disabled control creates
(`tools/vision/views.py:735-752`). Tests that register tools do so
inside `isolated_tool_registry` (`tools/vision/tests/_helpers.py:43`),
so a module-global registry cannot survive between tests and make the
suite pass in one collection order and fail in the other.

## Named gaps and deferred work

Recorded as their own section, following ADR 0013 §8's shape rather than
ADR 0014's (whose gaps live inside a numbered decision, `0014:827`),
because these gaps span the whole build rather than one decision in it.

**G1 — `agent.<slug>` stays catalogue-registered while `flow.run` is
row-driven.** The symmetry is available and deliberately not taken: an
`agent.run` with an `agent` choice filled per turn from `visible_agents`
would work exactly as §7's `flow.run` does. It is not built because
delegation is a **grant** decision in a way running a flow is not —
offering every visible agent as a delegate hands one agent another's
capabilities without anybody deciding that it may. It lands with
Identity & Auth, and `agent.<slug>` retires then. A consequence today:
a user-built or row-installed agent is not exposed as a tool at all,
because §6's specs are built from `DEFAULT_AGENTS` in code.

**G2 — flow-as-a-turn is not built.** `agent.turn`'s payload carries
`"mode"` with exactly one legal value, `"chat"`
(`agents/chat/service.py:159`, `agents/management/commands/agent_turn.py:92`).
A key with one possible value and no caller is not forward
compatibility; it is named here so the next phase reads it as a decision
rather than an omission.

**G3 — `/chat/` offers agents, not flows.** A conversation starts with
one agent (`agents/chat/views/conversations.py::conversation_start`),
and a flow is reachable only *through* that agent's `flow.run` call.
The start surface has no flow picker; the index's adopt-a-default button
covers flows, but adopting one is not running one.

**G4 — non-image artifacts are recorded and not rendered.**
`agents/chat/rendering.py` computes a `files` card key on both the turn
card and the tool card (`:123`, `:147`), and no template under
`agents/chat/templates/chat/` reads it. A tool that produces a non-image
artifact writes an honest reference the page silently declines to show.

**G5 — `thread_context` preflights on every thread GET.** For an agent
with granted tools and a bound connection that means a real, bounded
(~5 s) call to the engine's `supports_tool_calling` probe on every page
load, degrading to `None` on any failure. A per-request TTL cache in
front of the probe is backlog, named in `agents/chat/README.md:558-563`
and not built.

**G6 — hosted-API engines are designed-for only** (Decision 14). The
secrets vault itself, the posture gate that would permit egress,
per-connection credential CRUD in the console, rate-limit/quota/billing
handling, and streaming responses are all out of scope.

**G7 — `plan_turn` may over-declare a role.** A tool declaring two roles
where only one resolves leaves that one role in the admission snapshot,
while `agents/runtime/loop.py::_roles_resolve` (`:387`) later drops the
whole tool — so the queue was told about a model no tool will use. It is
the same tolerant shape `plan_ingest` takes for its extract role
(`tools/rag/jobs.py:408-417`), and over-declaring is the safe direction:
the job is admitted with more headroom than it needs, never less.
Narrowing it needs an enqueue-time/run-time reconciliation nothing has
yet had a reason to design.

**G8 (vision) — `services.submit_job` still preflights internally**,
after the tool's single preflight (Decision 15). Passing `resolved`
makes that internal call a health round trip rather than a full
re-resolution, and the duplication stays because the preflight is
`services`' own contract, shared with the page's submission path;
narrowing it is `services`' change to make, not `tools.py`'s
(`tools/vision/tools.py:433-443`).

**G9 (vision) — `build_generate_spec()` is a function rebuilt per call,
by design** (`tools/vision/tools.py:192`, called from
`tools/vision/apps.py:76`). Its params are the union of whatever
operations are registered at the moment it runs, so it cannot be a
module constant evaluated at import time.

**G10 (vision) — ComfyUI can be evicted, never selectively.** `unload`
(`models/contracts/engines/comfyui.py:827`) frees **every** model at the
endpoint, because ComfyUI's API has no per-model free — `POST /free`
with `unload_models` calls `unload_all_models()`. And `loaded_footprint`
(`:731`) is a deliberate over-count: it measures the memory that
disappeared across this adapter's own run, since `/system_stats` reports
the machine and not the model. So the queue *can* evict a ComfyUI
resident under ADR 0013 §4's plan, but only all-or-nothing, and its
footprint number is an admission-shaped answer rather than a checkpoint
size. Recorded alongside it: the seam's own header comment at
`models/contracts/engines/base.py:422` still reads "`OllamaEngine`
implements both", which was true when it was written and is now
incomplete. It is a `.py` comment and therefore out of a docs-only
phase's budget — named here, not fixed here.

**G11 — the tool-registry test helper is duplicated on purpose.**
`tools/rag/tests/_helpers.py`, `models/registry/tests/_helpers.py`,
`agents/contracts/tests/_helpers.py`, and `agents/tests/_helpers.py` each
carry their own `isolated_tool_registry` fixture, the identical shape
`tools/vision/tests/_helpers.py:26-55` defines (a code-quality review
after this ADR's first draft converted the four from an
`snapshot_tools`/`restore_tools` function pair each test module wrapped
in its own hand-written autouse fixture to this same importable
fixture). Sharing one copy across apps would mean, e.g., `tools/rag`
importing from `tools/vision` — a cross-column test-helper import, which
the standing rule forbids outright (`_helpers.py` modules are duplicated
per app, never imported across apps). The duplication is the rule
working, not the rule failing.

**G12 — ten standing gaps are inherited unchanged**, from the design
spec's §14 (`:2389-2416`), and are listed rather than re-litigated: no
agent-builder UI and no flow-builder UI (both need Identity & Auth,
because creating an agent is granting capabilities); no
settings-mutating tool is grantable until then (Decision 2); `/chat/` is
unauthenticated exactly like every other surface on this box, inheriting
that gap without widening it; no streaming, because a turn is a job and
a job returns a result; no cooperative cancel of a *running* turn, only
of a queued one, inherited from ADR 0013; no turn resume across a worker
restart — a drained turn is requeued and re-runs from the top, spending
its budget again; no token-budget management, since `HISTORY_TURNS = 20`
is a fixed cap and a long conversation against a small context window
will be truncated by the engine rather than pretended about;
`rag.ingest` accepts no path, only a `document_id` of a document already
in the store; and no file uploads into a turn (Decision 4). Gap 11 of
that list (`:2417`) — the memory-governance track inside `models/` — is
likewise unchanged: the agent layer consumes the queue's admission and
eviction exactly as every other job kind does.

**G12b — one of those gaps is DISCHARGED, and this ADR says so.** Spec
§14's gap 12 held that whether the model-listing endpoint reports
`capabilities` was an open question resolvable only by a live probe. P1
ran the probe (recorded in the spec's corrections addendum,
`:2544-2554`): the rows **do** carry the field, so nothing there is dead
code, and the per-model endpoint was kept as the source for
`supports_tool_calling` by choice rather than by necessity — it answers
about one named model precisely, where a discovery listing's per-row
shape is not contractual. `supports_tool_calling` was implemented on the
Ollama adapter (`models/contracts/engines/ollama.py:376`), and
`preflight_turn` consumes it on every `/chat/` thread GET today (G5). A
gap that got answered is worth one sentence saying so; leaving it in the
open list would misdescribe the tree.

**G13 — the order after this ADR**, each item depending on the one
before it:

1. **Identity & Auth** — real principals, grants in their own table
   (Decision 9's `tool_keys` argument disappearing), and `/inference/`'s
   mutation surface closed. G1 and four of G12's items close here — the
   two builder UIs, the grantable-mutating-tool gate, and `/chat/`'s
   unauthenticated posture.
2. **MCP edge** — `tools/list` and `tools/call` over HTTP on the
   existing app, over the same registry Decision 2 already holds. Never
   a separate server, and never a protocol in the middle of an internal
   call: an internal caller keeps calling `invoke_tool`. The import
   direction is part of this item — this platform as an MCP *client* of
   other servers, with a per-server allowlist of which of their tools
   may be registered.
3. **Tenancy** — visibility scopes on documents and categories, applied
   at Decision 9's single filter point.

## Consequences

- **There is now exactly one way a feature exposes capability to an
  agent**, and it is the same object an external caller will one day
  read. A feature app registers a `ToolSpec` from its own `ready()`;
  there is no second seam to reach for, and the MCP adapter proves the
  object is not LLM-shaped by accident.
- **`ready()` stays DB-free and light** with nine tools (§2's
  vision-enabled count) and five job kinds registered, because every
  runner is a dotted-path string and every registration module imports
  its services lazily. Adding a tool does not make app startup heavier.
- **A deploy changes what the platform *offers*, never what is
  *installed*.** `manage.py sync_agents` is gone; a deploy that ships a
  new default agent or flow adds a catalogue entry and an offer, and the
  operator's database is untouched until somebody presses a button. An
  edited row survives every subsequent deploy.
- **The agent layer consumes the queue's admission and eviction exactly
  as every other job kind does.** A turn is a job with a planner, a
  handler, a summarizer, an `on_terminal`, progress, and a checkpoint
  column it does not use; it gets no exemption from priority, memory
  accounting, orphan sweeps, or retention. ADR 0013's one-door rule is
  strengthened by §5's inline rule, not weakened by it.
- **`/chat/` is a product surface, not a demo**: a no-JS path, a
  refusal that precedes every write, turn states that never leak queue
  states, and an audit row per tool call that an MCP edge will reuse
  unchanged.
- **Two write-only tables and one public parameter are gone** (§13), and
  the agent column owns conversation memory in a shape that can actually
  hold what a turn does.
- **Accounts are off, and the seams for turning them on are built and
  guarded** (§10). Identity & Auth edits four functions rather than
  auditing a package — and until it lands, `ACCOUNTS_REQUIRED=1` raises
  rather than pretending.

## See also

- `agents/README.md` — the column as a whole: the data model, how a turn
  runs, and the tools this platform registers.
- `agents/contracts/README.md` — the tool contract itself (§2–§4), with
  the purity rule stated at the package it applies to.
- `agents/runtime/README.md` — the turn loop, delegation, flows, and the
  audit trail (§5–§7, §11).
- `agents/chat/README.md` — the `/chat/` surface, its 202-and-poll
  contract, and its own named gaps (§12, G4, G5).
- [ADR 0010](0010-model-management-framework.md) — roles, bindings, and
  the module-boundary law this ADR's §1 restates as three rules; already
  carrying the `session_id` amendment §13 points at (`0010:282-293`),
  and amended at its own foot (2026-08-29) for the import law and the
  `platform/` re-confirmation.
- [ADR 0013](0013-inference-execution-queue.md) — the queue whose one
  door and sequential default constrain §5; amended at its own foot
  (2026-08-29) with the inline-tools rule it never stated.
- [ADR 0012](0012-image-generation-engine-adapter.md) — the JSON tool
  call that makes an artifact a reference (§4, `0012:140`), and
  D-EDIT-12/13's ignored-param contract §15 consumes.
- `docs/superpowers/specs/2026-08-25-agents-and-tools-design.md` — the
  design this build was written against, including the §14 gap list G12
  inherits and the corrections addendum G12b discharges.

## Amendment (2026-08-29) — G4 closes, the artifact vocabulary widens, and G11's rationale is restated

**G4 is closed, same-branch.** The gap G4 names above — a tool's
non-image artifact recorded and never rendered — was closed after this
ADR's own text was drafted: `9942150` ("render files as a Files list of
links") gave `_artifact_files.html` (the same shared-include shape CQ-12
already gave images) a second call site. `_turn_card.html:85` now
includes it with `files=card.files`; `_tool_card.html:24-31` documents
DELIBERATELY not including it a second time — a turn's `artifacts`
column is the union of every tool call the job made, so a document a
tool call cited is also on the eventual answer turn's own `files`, and
rendering it on both cards would show every source document twice. The
`files` card key itself now lives at `agents/chat/rendering.py:179` and
`:278`, not the `:123`/`:147` G4 names above — moved there by
intervening commits, unrelated to this fix. G4's own text is left as
originally written rather than edited in place, following this repo's
own precedent for a dated record (ADR 0013's `console/jobs` →
`models/queue` path correction): an amendment saying so is the honest
fix; a silent rewrite is not. Following G12b's own precedent — a gap
that got answered is worth one sentence saying so.

**The artifact vocabulary widened.** Decision 4's "three kinds —
`output:<id>`, `input:<id>`, `document:<id>` — with one parser and one
reverse map" no longer names the whole shape: a `document` reference may
now also carry a display title, `document:<id>:<quoted-title>`
(`agents/contracts/artifacts.py:76-97`'s `mint_artifact`, read back by
`artifact_title` at `:100-112`; `parse_artifact`'s `_TITLED_KINDS` branch
at `:51-73` peels the title off rather than refusing a third segment;
`output`/`input` are unchanged and still refuse one). `agents/models.py:430`'s
field comment — `# ["output:12", "document:7"]` — is a stale example for
the same reason; a titled reference is now an equally ordinary member of
that list.

**G11's rationale, restated to cover its own list.** G11 justifies all
four cross-column `isolated_tool_registry` copies with one argument:
sharing a copy across apps would mean, e.g., `tools/rag` importing from
`tools/vision` — a cross-column test-helper import the standing rule
forbids outright. That argument does not reach the other two copies,
`agents/tests/_helpers.py:346` and `agents/contracts/tests/_helpers.py:98`,
which live in the SAME column — `agents/contracts/` and `agents/tests/`
are both under `agents/`, not two of the four columns Decision 1 names —
and intra-column importing is already practised elsewhere in this same
build (`agents/chat/tests/test_errors.py:24` and `test_turn_create.py:20`
both import `isolated_tool_registry` from `agents/runtime/tests/
_helpers.py`). The real reason the contracts copy stays separate is
`agents/contracts/tests/_helpers.py:10-14`'s own header: helpers are
duplicated per PACKAGE by convention, not per column, because a
cross-package import would make one package's test scaffolding
load-bearing for another's — `agents/contracts` and bare `agents` are
different packages sharing one column, a narrower boundary the
cross-column argument does not by itself describe. Recorded here, not
fixed: this is not a call to deduplicate, only to say the real reason
once.

## Amendment (2026-08-30) — Identity & Auth IA-1 (Tasks 5, 10, 11): §10 corrected end to end, and G13 item 1 partially closes

Three of this phase's tasks touch §10's text, and they are recorded here
as one amendment rather than three appended notes, because they are one
story: §10 described the seams Identity & Auth would fill in; this is
the record of exactly how IA-1 filled them.

**The setting is deleted, not flipped (Task 5).** §10 recorded `settings.
ACCOUNTS_REQUIRED` (`config/settings.py:36`) as the branch point Identity
& Auth would flip. Identity & Auth (IA-1, Task 5) instead DELETES it:
its `True` branch never worked — `agents/chat/principal.py::
principal_for_request` raised `NotImplementedError` naming this phase —
so there was no working behaviour to preserve by flipping it, only a
documented non-feature to remove. Leaving it alongside a real posture
would have meant two answers to "what posture is this box in" in a box
that runs three processes (web, worker, and a management shell) with
independently supplied environments — exactly the failure mode owner
ruling 4 and `identity/contracts/postures.py` were written to foreclose.

**The posture is `IdentitySettings.posture` — a row, not an
environment variable.** `identity.access.accounts_on()` reads it
(`open` → `False`, everything else → `True`) and is what every access
function in every column tests first. `agents/chat/principal.py` is
also deleted: `identity/request.py::principal_for_request` is now the
one request→principal point named in §10, unchanged in shape — `open`
posture still returns the same shared `OPEN_PRINCIPAL`, and no other
posture ever answers with it — but resolving a signed-in user, or
`ANONYMOUS` for a request with no session, for `personal`/`enterprise`
instead of raising. `foundation/ops/tests/test_no_accounts_required.py`
is a grep gate pinning that the name appears nowhere left to flip.

**§10's "runs as the agent" is reversed, and `visible_flows`' delegate
ruling with it (Task 11).** §10 said `/chat/` runs turns as the chosen
agent's own principal and invents no user identity. That was P2/P3's honest answer on a box
with no accounts: `agents/runtime/bindings.py::principal_for(agent)`
minted `Principal("resident_agent"|"user_agent", agent.slug)`, and
every one of its four callers (`loop.py::_run_turn`, `jobs.py::
_tool_roles`, `delegate.py::run_agent_tool`, `preflight.py::
preflight_turn`) used it. Identity & Auth's acting rule (spec section
5.3) REVERSES this now that a real actor exists: a turn runs as the
USER named in its job payload (Task 10's `actor_kind`/`actor_key`,
read back by `identity.contracts.principals.principal_from_payload`),
and `principal_for` is deleted outright — not moved, not renamed — because
the question it answered ("who does a turn by this agent run as") is
the wrong one once an agent is understood as a tool a person wields
rather than a second person with its own identity. It never raises: a
payload with no actor (every turn enqueued before this phase) or a
hand-edited one with an unrecognized `actor_kind` both still run,
honestly, as `OPEN_PRINCIPAL`.

**No hop widens the acting principal.** `delegate.py::run_agent_tool`
used to mint its OWN principal from the delegate agent it was about to
run, the same way the root loop did. It now inherits `ctx.principal` —
the ROOT caller's — verbatim, and passes it straight through to
`available_tools` and into the nested `run_loop` call. What DOES
change at a delegation hop is `ToolContext.agent_slug` (Task 10):
WHOSE TOOL DECLARATION IS IN FORCE, a separate fact from `principal`
(WHO THIS IS BEING DONE FOR) that the acting rule needs told apart.
`ToolInvocation` — the actual audit table — does NOT persist
`agent_slug` yet; both a root call and a delegate's now stamp the SAME
principal there, and "which agent made this call" survives today only
in `Turn.tool_call["agent"]` (`loop.py`'s own call-record field). Adding
`ToolInvocation.agent_slug` so the audit table itself can answer that
question is named IA-2 follow-up work, not shipped here.

**`agents.visibility.visible_flows`'s 2026-08-28 ruling — recorded at
§10's own text above — is reversed by the same logic.** It said a
delegate sees the flows IT may run, asked with `principal_for(agent)`
for the delegate's own agent. It now says a delegate sees the flows the
ROOT USER may run, asked with the same acting principal `visible_flows`'
three runtime callers (`narrowed_flow_spec`, `flow_row_roles`,
`run_flow`) always received — because a visibility rule that stopped at
"whichever agent is asking" would be a rule an agent could walk around
by delegating. `agents/visibility.py::visible_flows`'s own docstring
carries the reversal in full; ADR 0016 (written after IA-2) records the
same ruling from the grants side.

**§10's "six functions" become five.** `owner_fields` moves out of
`agents/visibility.py` to `identity.access.owner_fields` — the same
function `identity/access.py` had already declared "MOVED here" in
its own docstring since Task 6, now made true. `agents.visibility.
create_conversation` and `agents.defaults.install_default` both import
it from there; `agents/visibility.py` keeps `visible_conversations`,
`visible_agents`, `installed_agent_slugs`, `visible_flows`, and
`create_conversation`.

**The import-law gate's `Principal(` constructor allowlist shrinks to
two.** `foundation/ops/tests/test_import_law.py::
_PRINCIPAL_CONSTRUCTORS` drops `agents/runtime/bindings.py` — not
because the file is gone (`resolve_chat` still lives there), but
because the one `Principal(...)` construction it held was
`principal_for`'s, and that function no longer exists to hold it.
`identity/contracts/principals.py` and `identity/request.py` are the
two files left standing.

**G13 item 1 is *partially* closed, and this amendment is where that is
said out loud rather than left for a reviewer to infer from a diff.**
Real principals (`identity.contracts.principals.Principal`, replacing
the two historical agent-minted kinds for everything new), a
request→principal point in its own column (`identity.request.
principal_for_request`, moved out of `agents/`), and `/inference/`'s
unauthenticated mutation surface (every route in `models/registry/urls.py`
now classified `ADMIN` in `identity/routes.py`) all land in IA-1. **Grants
in their own table, the two builder UIs (agent and flow), and the
grantable-mutating-tool gate do not** — `agents/contracts/tools.py::
granted_tools`'s `tool_keys` argument is still there, still reading grants
off the `Agent` row rather than a table of its own, and `grantable_tools()`
still excludes every `mutates=True` spec unconditionally rather than
consulting a grant. Those three stay **open, in G12**, not quietly
recounted as done: G12's own list (no agent-builder UI, no flow-builder UI,
no settings-mutating tool grantable) is unchanged by this amendment, and
G13 item 2 (the MCP edge) still names "gated behind Identity & Auth" as
work still to do, not work already finished.

## Amendment (2026-08-30) — Identity & Auth IA-2: §9's `tool_keys` correction, and G13 item 1's grants half

**§9's "`tool_keys` disappears" is corrected: the argument survives and its
MEANING changed.** §9 recorded that when grants moved to their own table the
`tool_keys` argument to `agents/contracts/tools.py::granted_tools` would
disappear and "the principal alone decides". Owner decision 13 keeps the
agent's declaration, so the argument stays and what it MEANS is now a
declaration rather than a grant: what this agent WANTS to be able to do.
The grant is `agents.models.ToolEntitlement` plus the acting user's
entitlements, and it arrives as a third argument — a pure
`agents.contracts.tools.ToolAccess` value built once per turn by
`agents/entitlements.py::tool_access_for`. Availability is now the
intersection of three things: the declaration, the registry, and the acting
principal's entitlements. `granted_tools` is still the one function that
computes it, and it now has three drops rather than two.

Both halves of the original sentence cannot hold at once. If an agent still
declares what it wants, that declaration has to reach the one function that
decides availability; removing the argument would mean either the
declaration stops being consulted (so every user's full entitlement set is
offered to every agent) or `granted_tools` reads the `Agent` row itself,
which would make a Django-free rule-1 leaf query the database. The
declaration is the half worth keeping.

**`ToolInvocation.agent_slug` now exists, with its writer.** IA-1's
amendment above named it as IA-2 follow-up because IA-1 shipped exactly four
migrations. `agents/migrations/0003_toolentitlement_and_share.py` adds the
column and `agents/runtime/invoke.py::invoke_tool` writes it from
`ToolContext.agent_slug`, so the audit table itself answers "which agent
made this call" rather than the answer surviving only in
`Turn.tool_call["agent"]`.

**G13 item 1's grants half closes; its two builder UIs and its
grantable-mutating-tool gate do not.** Grants now live in their own table
(`identity.EntitlementGrant`, `agents.ToolEntitlement`,
`tools.rag.DocumentEntitlement`), which is the half IA-1's amendment
explicitly left open. The agent-builder UI, the flow-builder UI, and
`grantable_tools()`'s unconditional exclusion of every `mutates=True` spec
are UNCHANGED and stay open in G12 — ADR 0010's rule that a settings-mutating
tool is registered but not grantable stays in force through IA-2 and is
lifted by a later phase, against a real policy decision about which
entitlement such a tool requires by default.

## Amendment (2026-09-01) — Amended by ADR 0016: the Identity & Auth programme has its own record

The two amendments above are the corrections IA-1 and IA-2 made to THIS
ADR's own text, and they stay as written.
[ADR 0016](0016-identity-and-entitlements.md) is the architectural record
of the programme itself — the fifth column, the postures, entitlements,
the four labelled axes, sharing, the delete-cascade registry and the
guard rails — written after both halves merged. Read G13's item 1 through
it: what closed, what did not (the two builder UIs and the
grantable-mutating-tool gate, still open in G12 above), and where item 2
(the MCP edge) and item 3 (tenancy: the DOCUMENTS half shipped;
categories stay taxonomy) actually stand.

## Amendment (2026-09-05) — Workstreams: `ToolContext` gains `stream`, and a delegate inherits it

**`ToolContext` gains a fifth carried field, `stream: WorkstreamScope | None = None`** — THE
TURN'S STREAM SCOPE, `None` for a loose turn and for every caller with no conversation at all
(an external MCP `tools/call`, still to come). `None` is what keeps every existing runner and
every existing test that builds a bare `ToolContext` working, and the behaviour they get is the
SAFE one: `None` excludes a stream's contained documents rather than including them, the same
default-to-safe shape `tool_access: ToolAccess = UNRESTRICTED_TOOL_ACCESS` and `agent_slug: str =
""` already took above.

**A delegate inherits it, for the identical reason it inherits `principal` and `tool_access`.**
`agents.runtime.delegate.run_agent_tool` builds the sub-agent's own `ToolContext` by carrying the
root turn's `stream` forward unedited — a delegate is not a way around a stream's wall any more
than it is a way around a label (§9's own "an agent is never a way around labels", extended). The
asymmetry this creates is named, not accidental: the delegate's OWN instructions prose does not
travel (author decision 21, this ADR's §9), while its enforcement — the wall, and now the
stream scope that seeds it — does. A sub-agent that could not see the parent's system prompt but
could still read past its parent's wall would be the one direction that asymmetry must not run.

**Where the value comes from, and what it is not.** For a stream turn, `agents/runtime/loop.py::
_run_turn` calls `agents.workstreams.workstream_scope(principal, conversation.workstream_id)`
ONCE, beside `access` — the same place `agent_slug` and `tool_access` are already built once per
turn and threaded down rather than re-derived per tool call — and hands the result to
`ToolContext.stream`. `tools/rag/tools.py`'s `rag.search`/`rag.ask` runners are the readers: each
fills `scope_with_pins(ctx.stream)` into the `DocumentVisibility` it builds, which is what makes
retrieval honour the stream's contained corpus and its pinned set (ADR 0016 §6). `stream` is
carried, never re-derived per call — the same single-filter-point discipline `readable_documents`'s
own `workstream_id` argument already established there.

`stream` is a DIFFERENT, narrower fact than the WALL VALUE `tool_access_for`/`model_access_for`
already take a `wall=` argument for at all four of §6.3's own call sites (`plan_turn`,
`preflight_turn`, `_run_turn`, `chat_picker_options`) — that value is a bare `frozenset` of
entitlement ids, and the four sites do not each read it independently. `agents.runtime.jobs::
plan_turn` reads it via `agents.entitlements.wall_for` (which itself reads `agents.workstreams.
wall_ids`). For the RENDER pair, `agents.chat.views.thread::thread_context` reads it ONCE via
that same `wall_for` and hands the identical value into both `preflight_turn` (its own `wall=`
keyword, `None` by default for the two callers — `agents.chat.service.start_turn` and `manage.py
agent_turn` — that read it themselves via `wall_for` instead, having no render to share it with)
and `chat_picker_options`, neither of which computes it a second time for that render.
`_run_turn` is the fourth, and a later fix (quality-fix batch, E5) changed how IT gets there
without changing the value itself: on the ADMITTED path it now reuses the `.wall` this same
`workstream_scope` call above already read, rather than asking `wall_for` again for the same
table, and it falls back to its own `wall_for` read only on the anomalous path where
`workstream_scope` came back `None` (not admitted, or no stream) but the wall must still bind.
This amendment does not change how the value travels once read, only how often (and at which of
the four sites) the table underneath it is actually asked.
`WorkstreamScope.wall`
carries the same fact for the ONE caller that needs the fuller value alongside it — the retrieval
seam above — so the two never disagree, because both trace back to the one table `wall_ids`
reads.

Recorded in full, alongside the wall, taint, sharing and consolidation mechanisms this field
exists to carry, by the binding spec,
[`docs/superpowers/specs/2026-09-03-workstreams-design.md`](../superpowers/specs/2026-09-03-workstreams-design.md).
ADR 0017, the architectural record of the Workstreams programme itself (the ADR 0016 precedent:
written after both halves merge), is the next piece of work this amendment hands off to.

## Amendment (2026-09-20) — Chat carries a native image: the capability gate, the artifact-file-resolver reuse, and the 2026-09-03 design's supersession

The attachments-integration land added the agent layer's own half of native
image input: `agents.runtime.prompt.native_media_types(resolved)` answers
whether THIS turn's bound model accepts an image directly in a chat message,
and `_native_image_block`/`_carrying_attachments_block` use that answer to
ride a carried image into the prompt as a real `ImageBlock` instead of the
pre-existing "still processing" text line — the SAME carrying message this
ADR's own turn runtime already builds, one more block kind on it, not a
second message or a second path. **The KNOWN LIMIT this gate carries — that
it reads a fixed catalog vocabulary, not a live per-connection capability —
is recorded once, at [ADR 0010](0010-model-management-framework.md)'s own
2026-09-20 amendment, because it is a fact about `CatalogEntry`/
`ResolvedModel`, not about this ADR's turn or tool-contract shape. Read it
there; it is not restated here.**

**The image's own bytes are reached through the artifact-file-resolver
registry, not a new artifact kind.** `agents.attachments.
attachment_image_path(document_id, principal)` resolves the on-disk path
through `agents.contracts.artifacts.file_resolver_for("document")` — the
SAME registered resolver `tools.vision`'s own stored-document input already
resolves the `document` kind through, and the same `readable_document` gate
that keeps another uploader's chat-scoped bytes out of it. This is a second
caller of an existing extensibility point (the resolver registry the artifact
vocabulary amendment above already describes), not a fourth `document`-
adjacent kind added to Decision 4's list — worth stating because it is not
what the 2026-09-03 design proposed.

**That 2026-09-03 design is superseded.** The design this land started from
(`docs/superpowers/specs/2026-09-03-chat-attachments-design.md`) proposed a
new `file:<id>` artifact kind and conversation-scoped `ConversationFile` rows
on the ADR 0009 managed-store shape. Neither shipped: the fold-forward merge
that landed this ADR's amendment retired that machinery in favor of the
"message-bound attachments" shape chat's own round 13 had already built and
shipped on `main` — ahead of, and independent of, this branch's own
2026-09-03 design. It is that 2026-09-03 design this ADR supersedes, not
round 13's work; attachments stay `tools.rag` documents, scoped to a
conversation through the existing attachment-provider seam
(`agents.attachments.attached_documents`/`attachments_for`/
`stage_turn_attachments`/`detach_attachment`), never a new table and never a
new artifact-reference kind. `native_media_types` and
`attachment_image_path` above both read through the `document` kind's own
resolver for exactly this reason.
