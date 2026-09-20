# agents/contracts/ — the tool contract

The stable contract every column's tools are built against: what a tool
IS (`tools.py`), how it renders to an LLM tool-calling schema
(`toolschema.py`), and the artifact-reference vocabulary a tool result's
files travel through (`artifacts.py`).

New to the tool contract? [`docs/EXTENDING.md`](../../docs/EXTENDING.md) is
the walkthrough this README is the reference for.

## The five modules

- **`tools.py`** — `ToolSpec` (one tool an agent may call), `ToolResult`
  (what a runner returns), `ToolContext`/`StepBudget` (what a runner is
  called with — `ToolContext` also carries `tool_key`, which spec is
  running, `supplied_keys`, the caller's raw argument keys before
  `validate_tool_args` filled in a default, and `agent_slug` (Task 10)
  — see "Principals and grants" below for what it is and why it is a
  separate field from `principal`), the
  module-level tool registry (`register_tool`/`all_tools`/`get_tool`/
  `grantable_tools`/`granted_tools`), `describe_tool`, and
  `validate_tool_args`. `ToolRefused` — the no-retry failure class a
  runner raises for a state nothing the model can say will fix — is also
  declared here, the one place that can say which exception means which
  (see "Two failure classes" below). `Principal` is IMPORTED here (from
  `identity.contracts.principals`, never re-exported) for `ToolContext`'s
  type — see "Principals and grants" below for where it actually lives.
- **`toolschema.py`** — `wire_name`/`key_from_wire_name` (a tool's `.`-key
  round-trips through an OpenAI-style function name, which cannot itself
  contain a `.`), `openai_tool_dict` (a `ToolSpec` rendered to the wire
  schema an LLM tool-calling API expects), and `mcp_tool_dict` (the same
  `ToolSpec` rendered to an MCP `tools/list` entry). Both call the one
  private `_input_schema(spec)` builder, so the two can never disagree
  about what a tool accepts — pinned by a test parametrized over every
  registered spec (`test_toolschema.py::TestTheTwoAdaptersCannotDrift`).
  No `FunctionTool`, no `predict_and_call` — this platform builds the
  dict by hand and calls a runner itself.
- **`artifacts.py`** — `ARTIFACT_KINDS`, `parse_artifact`,
  `mint_artifact` (`artifacts.py:76`), `artifact_title` (`:100`), and
  `artifact_url_name`: the `"kind:id"` reference vocabulary a
  `ToolResult.artifacts` entry uses (e.g. `document:7`, `output:12`) so a
  tool result never carries a filesystem path or raw bytes. A `document`
  reference may also carry a display title, minted by `mint_artifact` and
  read back by `artifact_title` — `"kind:id:quoted-title"`
  (`artifacts.py:94-97`); `output`/`input` never carry one.
  Two sibling registries live here too, both keyed by artifact kind and both
  holding a DOTTED PATH resolved at call time rather than an import:
  `register_artifact_labels`/`labels_resolver_for` (which entitlements label
  the rows behind a kind — `agents/runtime/taint.py` reads it) and
  `register_artifact_file_resolver`/`file_resolver_for` (where a kind's BYTES
  are — a tool with a file input reads it, and gets back an `ArtifactFile`
  carrying `path`/`name`/`media_type`). A resolver raises `LookupError` for a
  row that is missing, file-less, or invisible to the asking principal — one
  exception for all three, so an invisible row cannot be told from a missing
  one.
- **`workstreams.py`** — `WorkstreamScope` (a stream turn's frozen id/
  wall/upload-default/pins value, threaded down rather than re-derived
  per tool call) and the `WorkstreamPanel` registry
  (`register_workstream_panel`/`all_workstream_panels`): how a column
  that owns rows the stream page must display announces itself to
  `agents/workstreams.py::panels_for`, `provider` a dotted path resolved
  by `import_string` at render time, never imported here.
- **`attachments.py`** — the single-slot sibling of the panel registry
  above (`register_attachment_provider`/`attachment_provider`): how
  `tools.rag` announces "what has this conversation attached" to
  `agents.attachments.attached_documents`, the one resolver both the
  conversation page's attachments strip and a running turn's own prompt
  read through (round 11, owner feedback).

## Two failure classes, and two more outcomes beside them

A runner's `ToolResult` is not the whole outcome space P2's tool loop has
to handle. Four shapes reach it, and the except-clause ORDERING below is
not incidental -- it is the one place drift would silently turn an honest
refusal into a wasted retry.

1. **`ToolRefused`** (`agents/contracts/tools.py:46`) -- no retry. The
   tool declined honestly: the model-consuming role it needs is unbound,
   or the engine behind it is unreachable. Nothing the model can say
   changes either fact, so section 10.1 gives this zero retries.
2. **`ParamError`** (`models/contracts/operations.py:228`) -- exactly
   ONE retry, with the model shown `.errors`, its per-arg map of key ->
   human-readable reason (section 10.2). The model was told precisely
   what was wrong and can plausibly fix it on the next call.
3. **Any other `ValueError`** -- an honest tool error, no retry. Not
   every failure is repairable by rephrasing an argument; a missing row,
   a missing file in the managed store, or a caller-supplied reference
   nothing can resolve are facts about the world, not about the call's
   shape.
4. **A `ToolResult` whose `.text` reports a degraded state** -- not an
   error at all. The call succeeded; what it has to report is a
   less-than-ideal state (a job that didn't queue, a job still running at
   the turn's deadline). The loop must not treat this as any kind of
   failure -- there is nothing to catch and nothing to retry.

**The except-ORDERING constraint.** `ToolRefused` and `ParamError` are
BOTH `ValueError` subclasses -- `class ToolRefused(ValueError)`
(`agents/contracts/tools.py:46`) and `class ParamError(ValueError)`
(`models/contracts/operations.py:228`). A loop that reaches `except
ValueError` before it reaches `except ToolRefused` / `except ParamError`
never lands in either narrower branch: every refusal and every
schema-repairable error alike falls into the wide clause, and if that
wide clause's policy is "retry", every honest refusal becomes a wasted
retry the tool will refuse identically the second time. **P2's
`invoke_tool` must catch `ToolRefused` and `ParamError` BEFORE it catches
`ValueError`.**

**Which runner produces which outcome today:**

| Outcome | Where |
| --- | --- |
| `ToolRefused` | `rag.search` on an unbound `rag.embed` role (`tools/rag/tools.py:213`); `rag.ask` on either role unbound (`tools/rag/tools.py:280`); `vision.generate` on `VisionUnavailable` -- unbound role or unreachable engine (`tools/vision/tools.py:459`) |
| `ParamError` | every runner's own `validate_tool_args` call (`agents/contracts/tools.py:203`, itself `models.contracts.operations.validate_params`); `rag.search`/`rag.ask`'s unknown-category refusal (`tools/rag/tools.py:122`); `vision.generate`'s `submit_job` validating the picked operation's own params (`tools/vision/services.py:613`) |
| Any other `ValueError` | `rag.ingest` on a missing document row (`tools/rag/tools.py:318`) or a missing copy in the managed store (`tools/rag/tools.py:322`, wrapping `FileNotFoundError`); `vision.generate`'s `InputReferenceError` (declared `tools/vision/services.py:436`, a `ValueError` subclass) raised resolving a dead or malformed image reference and left uncaught at its call site (`tools/vision/tools.py:487`), so it reaches the loop as a plain `ValueError` -- the same "propagate uncaught" posture `tools/vision/jobs.py:33` documents for the queued job handler |
| Honest `ToolResult` | `rag.ingest` when `enqueue_reingest` returns no job id -- the queue is down (`tools/rag/tools.py:331-335`); `vision.generate` when the job is still non-terminal at the turn's deadline (`tools/vision/tools.py:495`) |

## Rule-1 purity, pinned not hoped for

This package is a **rule-1 pure leaf**: no Django, no database, no I/O —
any column may import it, in any direction. That claim is asserted in
every module's own docstring, but a docstring is a hope; `tests/
test_purity.py` is the property. It imports the whole package in a
**subprocess with no `DJANGO_SETTINGS_MODULE` set at all**, so an
accidental Django import fails there with an `ImproperlyConfigured` (or
shows up as a leaked `django` entry in `sys.modules`) rather than quietly
making this package depend on a configured Django project. The carve-out
that made this necessary is real, not hypothetical: `ToolContext.job` is
typed as `models.contracts.jobkinds.JobContext`, and `jobkinds.py:34`
imports `django.utils.module_loading` at module scope — so the import in
`tools.py` sits under `if TYPE_CHECKING:`, guarded by `from __future__
import annotations`, and is never evaluated at runtime.

## `get_tool` raises; the prompt builder does not

`get_tool(key)` raises `ValueError` naming the key if it isn't
registered — a tool call for an unregistered tool is a caller bug, not a
state to degrade gracefully from (matching `get_job_kind`, not
`get_role`). A caller for whom an absent key is the NORMAL case — P2's
prompt builder, dropping an unregistered granted key rather than
crashing — reads the module-level registry directly instead (`_TOOLS.
get(key)`), deliberately bypassing the raising accessor.

## Principals and grants

See [`identity/README.md`](../../identity/README.md) for the column
`Principal` now lives in, and the postures (`open`/`personal`/
`enterprise`) that decide which principal a request resolves to.

A grant attaches to a **principal**, not to an agent. `Principal` is
**no longer defined here**: Identity & Auth (IA-1) moved it, and its
`PRINCIPAL_KINDS` tuple, to `identity/contracts/principals.py` — a
rule-1 pure leaf every column may import, because after IA-1 `Principal`
is the base identity type of the whole platform, not an agents-column
detail. `Principal(kind, key)` is still two strings — `kind` one of
`PRINCIPAL_KINDS` (`"open"`, `"user"`, `"service"`, and the two
historical kinds `"resident_agent"`/`"user_agent"` that nothing mints
after IA-1 but that pre-IA-1 `ToolInvocation` rows still carry), `key`
whatever that kind means (a user's primary key, an `Agent.slug` for the
two historical kinds). `ToolContext.principal` carries it — P1's bare
`agent_key` string is gone, because it could only ever mean "an agent"
and the addendum's external MCP `tools/call` has no agent at all — and
this module imports `Principal` from its real home rather than
re-exporting it, so there is one import path to grep for.

`granted_tools(principal, tool_keys, access=UNRESTRICTED_TOOL_ACCESS)` is
the **one** function that decides which of a caller's declared
`tool_keys` it may actually use — ruling R1's runtime half, now with
IA-2's grant half folded in as a THIRD drop. It drops (and logs at INFO)
a key absent from the registry, drops a key whose spec is
`mutates=True` (ADR 0010's rule, still in force through IA-2), and
drops (and logs at DEBUG) a key `access.allows(key)` refuses — then
preserves the caller's order and collapses duplicates. It reads
`_TOOLS` directly rather than through `get_tool`, which raises on an
absent key where an absent key is normal here (see "`get_tool` raises"
above).

`ToolAccess(required, held, unrestricted)` is the pure value the third
drop reads: `required` maps a tool key to the entitlement ids that
LABEL it (a key absent from the mapping is unlabelled and therefore
callable by anybody signed in), `held` is the entitlement ids the
acting principal holds, and `unrestricted` short-circuits the whole
check for open posture or a principal that sees all content.
`UNRESTRICTED_TOOL_ACCESS` is its zero-argument, all-allowing default,
so `access` defaults to it and every existing call site and test that
does not care keeps working unchanged. `ToolAccess` lives here, in this
rule-1 pure leaf, and therefore CANNOT query `agents.models.
ToolEntitlement` itself — the Django-side builder,
`agents.entitlements.tool_access_for(principal) -> ToolAccess`, reads
that table (through `agents.labels.tool_entitlement_ids`) and the
principal's own holdings (through `identity.access.
held_entitlement_ids`/`sees_all_content`) ONCE PER TURN and hands the
answer down as data. `agents/runtime/{jobs,loop}.py` each build one
per turn (`_run_turn`/`plan_turn`) and thread it through every hop of
the walk; `delegate.py` reuses the root's `ctx.tool_access` rather than
building a second — the acting rule's grant half, alongside
`agent_slug` below.

`tool_keys` **survives as a declaration, not a grant** (IA-2, owner
decision 13 amending ADR 0015 §9, which had said this argument would
disappear once grants moved to their own table). Grants live on
`agents.models.ToolEntitlement` now, joined against the acting
principal's own entitlements — but an agent still declares what it
*wants* to be able to call on `Agent.tool_keys`, and `granted_tools`
still reads both: the declaration decides the CEILING, `access` decides
which of that ceiling this particular caller may actually use.

### `ToolContext.tool_access` — the grant half travels with the call (IA-2 Task 5)

`ToolContext` carries `tool_access: ToolAccess = UNRESTRICTED_TOOL_ACCESS`
alongside `principal` and (below) `agent_slug`: the same `ToolAccess` a
turn's `granted_tools` call was already handed, now also reachable from
INSIDE a runner through its own `ctx`, rather than only at the one point
`available_tools` computes the prompt's tool list. `agents/runtime/
{jobs,loop}.py` build one per turn and stamp it onto every `ToolContext`
at every depth; `delegate.py::run_agent_tool` reuses `ctx.tool_access`
verbatim rather than building a second one from the delegate's own
agent or principal — the acting rule's grant half, exactly mirroring
what `agent_slug` does for the declaration half below.

### `ToolContext.agent_slug` — a second fact, split out of `principal` (Task 10)

The acting rule (spec section 5.3) reverses who a turn runs as: a chat
turn or queue job acts as the **user**, an agent's tool list is
intersected with the user's tool entitlements, a delegate inherits the
ROOT user, and an agent is never a way around labels. Expressing that
needed two facts to travel separately, and before Task 10 there was
nowhere for the second one to live — both were crammed into
`ToolContext.principal`.

- **`principal`** is now always WHO THIS IS BEING DONE FOR — the user (or
  the open/service principal) a turn or job runs as.
- **`agent_slug`** is WHOSE TOOL DECLARATION IS IN FORCE — which `Agent`
  row's granted tools, prompt, and role bindings apply to THIS call.

A top-level turn carries the same value in both loosely (the signed-in
user, running the agent they picked); a DELEGATE (agent-as-tool,
`agents.runtime.delegate.run_agent_tool`) is where the split earns its
keep — the nested call carries the ROOT caller's `principal` (never the
delegate's own identity) together with the delegate's OWN `agent_slug`,
so its tool list is still the union the acting rule requires, and no
agent can widen what it may do merely by delegating to another agent.
Blank for a runner called directly (every test that builds a bare
`ToolContext`) and for a caller with no agent at all (the future MCP
edge).

**This task (T10) only makes the field exist and gives every job payload
an `actor_kind`/`actor_key` pair to travel in** (`identity.contracts.
principals.payload_fields`/`principal_from_payload` — see ADR 0013's
2026-08-30 amendment). Nothing yet POPULATES `agent_slug` on a live call
or reads a payload's actor back to decide who a turn runs as — that is
Task 11's work.

## The invocation log

Every tool call this platform makes — an agent's own loop
(`agents.runtime.invoke.invoke_tool`), an agent-as-tool delegate
(`agents.runtime.delegate.run_agent_tool`, which shares the SAME
`invoke_tool`), and (once the MCP edge lands, 2026-08-27 addendum
consequence 3) an external `tools/call` — writes exactly one
**`ToolInvocation`** row (`agents/models.py`): the calling `Principal`
(flattened to `principal_kind`/`principal_key`), the tool key, the
VALIDATED args, a closed `outcome` class (`ok`/`refused`/`param_error`/
`error`/`degraded`), the runner's own text, and (IA-2 Task 5)
`agent_slug` — WHOSE TOOL DECLARATION WAS IN FORCE, written by BOTH
`invoke_tool` and `invoke_unknown_tool` from `ToolContext.agent_slug`,
blank for a call with no agent at all. A hallucinated wire name still
carries it (fix round 1, review finding 5): the agent's own declaration
was in force even though the name it emitted never resolved to a real
`ToolSpec`, and a hallucinated-tool row is precisely the kind an
operator investigates by agent.

Its own table, referenced BY `Turn` (`Turn.invocation`, `SET_NULL`), never
a set of columns ON `Turn` — `agents.models.Turn`'s own docstring makes
the same point about `Turn.tool_call`. This is deliberate, and it is what
lets an external MCP `tools/call` reuse the row unchanged: that caller has
no conversation and no turn at all, only a principal and a tool key, and
`invoke_tool` never assumes a `Turn` exists. A `Turn` merely *references*
the invocation that produced it; the invocation is the durable audit
record either way.

## `mutates`: registered, but not always grantable

`ToolSpec.mutates` is `True` when a tool changes state that **already
exists** — configuration, a role binding, previously-ingested content
(`rag.ingest` replaces existing chunks). Creating new work product (a
generated image, a queued job) is not `mutates`. `grantable_tools()`
returns only `mutates=False` specs: a mutating tool is registered — so it
is visible, documented, and testable — but not **grantable** to any agent
until Identity & Auth lands (ADR 0010:266-276). Today `rag.ingest` is the
one mutating tool in the registry; `rag.search`, `rag.ask`,
`models.status`, `vision.operations`, and `vision.generate` are all
grantable.

## `describer`: declared, inert, ruling R3

`ToolSpec.describer` is a reserved field — a dotted path to a future
`callable(spec, resolved) -> dict` that would merge per-param LIVE facts
(a role's currently-bound model, an engine's reported options) into
`describe_tool`'s output, for the one constant vision input screen the
spec's section 4.8 designs. **Nothing in P1-P4 calls it.** No `ToolSpec`
registered anywhere sets it, `openai_tool_dict` never consults it, and
`describe_tool`'s output carries no live-facts keys. It exists now only
so that work is a fill-in later, not a contract change. A dotted-path
string, never a live callable, for the same reason `runner` is one.

## A tool runner never blocks on a queue job

A runner may enqueue a queue job and return its id (`rag.ingest` does
exactly this); it must never call `get_job` in a loop, and it must never
`enqueue` work whose result it needs. On a default install
`JobSettings.memory_budget_bytes` is `null`, which
`models/queue/scheduler.py` reads as sequential mode — at most one job on
the whole machine — so a turn that blocked on a job it enqueued would
hold the machine's one execution slot while the job it's waiting for can
never be admitted: a certain deadlock, not a probable one, followed by
the orphan sweep eventually failing that job permanently. Guarded
permanently by `foundation/ops/tests/test_column_boundaries.py::
test_no_tool_runner_blocks_on_a_queue_job`, a source-text scan over every
module that declares tool runners (`tools/rag/tools.py`,
`tools/vision/tools.py`, `models/registry/tools.py`) — `tools.vision.
services.wait_for` is correctly NOT caught by it: it polls the image
engine about a generation already submitted, and never touches the
queue at all.

## Registration stays lazy, and that is load-bearing

Every `ToolSpec.runner` is a dotted-path STRING, resolved lazily by
`models.contracts.jobkinds.resolve_dotted_path` — never a live callable —
so registering a tool never imports the module that implements it.
`AppConfig.ready()` in each owning app imports its `tools.py` to register
specs, and `ready()` promises no DB and no heavy imports at startup; every
runner's real service-layer import therefore happens lazily, INSIDE the
runner's own function body. Guarded permanently by `foundation/ops/tests/
test_column_boundaries.py::test_no_tool_module_imports_its_service_layer_
at_module_scope` (an AST scan of each registration module's module-scope
imports, allowing only `agents.contracts.*`, `models.contracts.*`, and the
standard library) and by `models/registry/tests/test_registry_paths.py`,
which resolves every registered `ToolSpec.runner` through
`resolve_dotted_path` to catch a stale path before a worker does.

Design: `docs/superpowers/specs/2026-08-25-agents-and-tools-design.md`.
