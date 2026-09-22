# Extending farabunker

## Adding a tool in three steps

A tool is how a feature offers itself to an agent — one entry an LLM (or a
future MCP caller) can call, backed by a plain Python function. There are
three steps, three files, and no plugin system: a tool is code in this
repository, registered by an app you install.

The worked example below is `models.status` (`models/registry/tools.py`) —
the smallest real tool in the repository: no params, no roles, one lazy
import, one `ToolResult`. The three Python blocks under Step 1 (the spec,
the runner, and its lazy imports) are quoted directly from that file, so if
you diff this page against the tree you will find them identical; the
`Param` pair, the `register_tool` line, and the bash gate block are each
quoted from elsewhere, named at their own point below. No model names
appear anywhere below; `models.status` reports role *assignments* and names
no model itself, which is part of why it is the right example.

## Step 1 — the spec and the runner, in your column's `tools.py`

### The spec

```python
MODELS_STATUS = ToolSpec(
    key="models.status",
    label="Model assignments",
    description=(
        "Report which model this box currently has assigned to each of its "
        "model-consuming roles, and which roles have nothing assigned. It "
        "reads the registry only: it does not contact any engine, so it "
        "cannot say whether an engine is actually running."
    ),
    params=(),
    roles=(),
    runner="models.registry.tools.run_status",
)
```

(`models/registry/tools.py:37-49`.)

- **`key`** — the wire key, dotted, `<column>.<verb>`. `models.status`'s
  column is `models` and its verb is `status`.
- **`label`** — what a card shows, operator-facing.
- **`description`** — **written for the model, not for a human.** This is
  the text the LLM reads to decide whether and when to call the tool at
  all, not documentation for a person reading this file.
- **`params`** — empty here (`models.status` takes no arguments). A tool
  that does take arguments declares them as a tuple of `Param`s. Two
  shapes cover almost everything, built from `models/contracts/
  operations.py:41`'s `Param` and already exercised by
  `agents/contracts/tests/test_tools.py::TestToolParamKinds`:

  ```python
  Param("query", "text", "Query", required=True)
  Param("top_k", "int", "Results", default=None, min=1, max=50)
  ```

  The kinds a `Param` may declare are named at
  `agents/contracts/tools.py:53` (`TOOL_PARAM_KINDS`):
  `text`, `int`, `float`, `choice`, `seed`, `asset`. `"file"` is
  deliberately excluded — the rationale is written at
  `agents/contracts/tools.py:49-52`: a tool call is JSON, so an image
  input has to be an artifact *reference*, never an upload object.
- **`roles`** — empty here (`models.status` consumes no model itself). A
  tool that needs a model declares the role key so the turn's planner can
  declare it to the queue's admission snapshot.
- **`runner`** — a **dotted-path string**, not a function object. This
  matters: `agents/runtime` reaches every tool through a string and never
  imports `tools/*` directly — that is import-law rule 3.
- **`mutates`** — not shown above because it defaults to `False`.
  `True` means the tool changes state that already exists; it is still
  registered (so it's visible, documented, and testable) but **not
  grantable** to any agent until Identity & Auth lands.

### The runner

```python
def run_status(args: dict, ctx: ToolContext) -> ToolResult:
    """Every registered role and whatever currently answers it.

    Reports all three of `role_primary`'s honest states without
    collapsing any of them (bindings.py:192-221): a bound connection
    (name plus pk), an explicit environment override (a name, no pk --
    there is no registry row to carry one), or genuinely unassigned.
    """
```

(`models/registry/tools.py:52-59`.) The signature is fixed for every
runner: `(args: dict, ctx: ToolContext) -> ToolResult`.

**The service imports are INSIDE the function body**, not at module scope:

```python
    from models.contracts.roles import all_roles
    from models.registry.bindings import role_primary

    validate_tool_args(MODELS_STATUS, args)   # declares no params: any arg is a bug
```

(`models/registry/tools.py:60-61`, then `validate_tool_args` at `:63` as
the first real statement after them — in that order, because that is the
order the file has.) The reason: `AppConfig.ready()` imports this module to
reach the spec, so a module-scope service import would put database and
heavy-import work into every `manage.py` invocation. A guard enforces this
permanently: `foundation/ops/tests/test_column_boundaries.py:507`.

### Which exception to raise

| Situation | Raise | What the agent sees |
|---|---|---|
| The caller's arguments are wrong | `ParamError` (`models/contracts/operations.py:228`) — `validate_tool_args` raises it for you | `param_error`; the one failure class worth handing back for a retry |
| Nothing the model can say would fix it (a role is unbound, the caller is not allowed) | `ToolRefused` (`agents/contracts/tools.py:56`) | `refused`; no retry |
| Anything else went wrong | let it raise, or raise `ValueError` | `error`; logged, classified, never a traceback in the UI |

## Step 2 — one line in `AppConfig.ready()`

```python
register_tool(MODELS_STATUS)
```

(`models/registry/apps.py:60`.) `ready()` lives under two rules: **no
database access** and **no heavy imports** — exactly why step 1's service
imports are lazy. A flag-gated feature registers inside its own flag check
instead, the way `tools/vision/apps.py:75-76` does.

## Step 3 — two guard lists, in the same commit

Both lists live in `foundation/ops/tests/test_column_boundaries.py`, and
both are edited **in the same commit that creates the module** — the
file's own comment at `:182-184` says so:

- **`TOOL_MODULES`** (`:185-191`) — every module that declares tool
  runners. Swept for the never-block-on-a-queue-job rule
  (`test_no_tool_runner_blocks_on_a_queue_job`, `:359`).
- **`_REGISTRATION_MODULES`** (`:219-235`) — the modules `AppConfig.
  ready()` imports to register specs. It **overlaps** `TOOL_MODULES`
  rather than being a subset of it: `agents/runtime/flowtool.py` is in
  this list only. Swept for the no-service-import-at-module-scope rule
  (`:507`). It is an explicit positive list, not a derived one, and
  `:531`'s `test_the_registration_modules_list_is_not_silently_empty` is
  what keeps it honest.

**On "bump the floor":** there is **no per-tool registration floor to
bump**. What exists are two anti-vacuous floors, both `>=`, both
deliberately loose:

- `models/registry/tests/test_registry_paths.py:80` —
  `assert len(paths) >= 26`, whose docstring (`:59-78`) states the rule:
  "a phase that adds a registration should not have to edit this line,
  but it must move up when a phase adds six."
- `agents/contracts/tests/test_toolschema.py:203` —
  `assert len(all_tools()) >= 4`, the flag-independent tools.

One new tool moves neither. The test that *will* fail if step 3 is
skipped is `test_every_registered_runner_lives_in_a_swept_module`
(`foundation/ops/tests/test_column_boundaries.py:456`) — its own docstring
says why: `TOOL_MODULES` is a hand-maintained list, so a new tool module
nobody added to it would silently escape the sweep. Recognize that failure
when you see it.

(`agents/contracts/tests/test_tools.py:184`'s
`test_it_delegates_to_the_platforms_one_floor` is **not** a registration
floor — it pins that `validate_tool_args` delegates to the platform's one
validation floor. Named here so it is never mistaken for a counter.)

## What you get for free

| You get | Mechanism |
|---|---|
| Grants — an agent row naming your key in `tool_keys` may call it | `agents/models.py::Agent.tool_keys`, decided by `agents/contracts/tools.py::granted_tools` |
| Not grantable if it writes | `agents/contracts/tools.py::grantable_tools` (a filter on `spec.mutates`) |
| Both wire schemas, from one builder | `agents/contracts/toolschema.py:89` (`openai_tool_dict`) and `:105` (`mcp_tool_dict`), sharing `:53` (`_input_schema`) |
| Argument validation, one floor | `agents/contracts/tools.py::validate_tool_args` delegating to the platform's `validate_params` |
| An audit row per call, whoever called | `agents/runtime/invoke.py:95` (`invoke_tool`) writing `agents/models.py::ToolInvocation.Outcome`'s five outcome classes (`ok`, `refused`, `param_error`, `error`, `degraded`) |
| A card in `/chat/` with its arguments, thumbnails, and citations | `agents/chat/rendering.py::tool_card`, rendered by `agents/chat/templates/chat/_tool_card.html` |
| Usable as a flow step, with `$input.<key>` / `$steps.<N>.…` references | `agents/runtime/flow.py:67` (`resolve_ref`), `:149` (`resolve_args`), `:258` (`_run_steps`) |
| Budget and depth bounds, enforced around you | `agents/limits.py:20` (`MAX_STEPS_DEFAULT`), `:29` (`TURN_DEADLINE_SECONDS`), `:43` (`MAX_AGENT_DEPTH`) |
| An honest ending on every failure path | `agents/runtime/invoke.py:95-157` — it never raises for a tool failure; it classifies |

## Granting it to an agent

A tool is not callable just because it is registered; it is callable
because an `Agent` row names it in `tool_keys` (`agents/models.py:126`). Edit
the row (`manage.py shell`, or the row itself), or add the key to the
shipped catalogue in `agents/defaults.py` and adopt it with `manage.py
install_defaults`. **`--reset <slug>` restores the shipped text of one
default and discards local edits to that row**
(`agents/management/commands/install_defaults.py:53`, `:77-91`) — the one
destructive flag on this page.

## Registering a workstream panel

The stream page (`chat-workstream`) renders whatever section a column has
registered, in registration order, through one include — a second panel
later (generated images in a stream, say) is a **registration**, never an
edit to `agents/chat/views/workstreams.py`, which may not import your
column's models at all.

One call in your `AppConfig.ready()`, in the SAME COMMIT as the handler it
names (the resolver below never swallows an import error, so a
registration that landed before its module would make every stream page
raise):

```python
from agents.contracts.workstreams import WorkstreamPanel, register_workstream_panel

register_workstream_panel(WorkstreamPanel(
    "rag.documents", "Documents", "tools.rag.workstreams.panel",
    "rag/panels/documents.html"))
```

Quoted directly from `tools/rag/apps.py` — the real, shipped registration
for the stream page's Documents section. The four fields:

- **`key`** — a stable identifier (`"rag.documents"`), namespaced by your
  column so two panels never collide.
- **`label`** — the page's own section heading (`"Documents"`).
- **`provider`** — a dotted path, `"package.module.function"`, resolved by
  `import_string` at RENDER time and never imported at registration time.
  Its signature is `(principal, workstream_id) -> dict`: whatever plain
  data your template needs, built however your column likes.
- **`template`** — the template path your provider's own `data` is
  `{% include %}`d into. It lives in YOUR column's template directory, for
  the same reason the provider is your column's own function: the markup
  for another column's rows is written by that column.

**A provider that raises never breaks the page.** `agents/workstreams.py::
panels_for` catches any exception per panel and renders that one section as
its own heading plus one honest sentence instead — a store that is down or
a migration half-applied degrades one panel, not the whole stream.

## Joining the taint stamp

A stream's TAINT is the union of the entitlement labels its own retrievals
have actually touched (spec §7). If your column's tool runner can return an
artifact reference (`"yourkind:<id>"`, the same shape `document:<id>` and
`output:<id>` already use) and rows behind that kind can carry entitlement
labels, one registration is the whole of what makes those returns start
tainting — **no change to any runtime module, and no migration**:

```python
from agents.contracts.artifacts import ArtifactLabels, register_artifact_labels

register_artifact_labels(
    ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for"))
```

Quoted from `tools/rag/apps.py`, again in `AppConfig.ready()` and again in
the same commit as the resolver function it names. `resolver` is a dotted
path with the signature `(pks: frozenset[int]) -> frozenset[int]`,
returning the UNION of the entitlement ids labelling those rows — your
column's own label table, read however it already reads it for every other
purpose.

**A kind with no registration contributes nothing, silently.** `agents/
runtime/taint.py::stamp_turn_taint` looks up a resolver for every artifact
kind a turn returned and skips a kind with none — which is why `output:`
and `input:` (generated images, today) taint nothing: `tools/vision` has no
label table yet. The day it does, one `ArtifactLabels("output", ...)`
registration is the entire change; `agents/tests/test_taint.py` pins the
claim against a fake resolver so it stays true rather than aspirational.

## Registering an artifact file resolver

An artifact reference (`output:<id>`, `input:<id>`, `document:<id>`) is a
JSON-safe *string* — never a path, never bytes. A tool that takes a **file
input** eventually needs the bytes behind one, and the reference it was handed
may name a kind another column owns. Two columns under `tools/` may not import
each other at all, and `agents/` may import neither, so the answer is the same
shape every other cross-column seam here takes: the owning column registers a
**dotted path**, and the consuming column resolves it at call time.

### If you OWN a kind — register where its bytes are

One line in your `AppConfig.ready()`, in the **same commit** as the function it
names:

```python
from agents.contracts.artifacts import register_artifact_file_resolver

register_artifact_file_resolver("document", "tools.rag.access.artifact_file_for")
```

Quoted from `tools/rag/apps.py`. Your resolver's signature is
`(pk: int, principal) -> ArtifactFile`:

```python
from agents.contracts.artifacts import ArtifactFile

def artifact_file_for(pk: int, principal) -> ArtifactFile:
    row = readable_row(principal, pk)          # YOUR column's own visibility gate
    if row is None:
        raise LookupError(f"document:{pk} does not name a readable document.")
    source = Path(row.source_path or "")
    if not source.is_file():
        raise LookupError(f"document:{pk} has no file on disk.")
    return ArtifactFile(path=str(source), name=source.name,
                        media_type=row.media_type or "")
```

`ArtifactFile` carries three fields and nothing else: `path` (absolute,
filesystem, your column's own), `name` (a **safe basename**, never a path — the
consuming column joins it onto its own directory), and `media_type` (a MIME
string, or `""` when you do not know — `""`, never `None`, because every
consumer asks `.startswith("image/")` on it).

**Raise `LookupError` — one exception for three conditions.** No such row, no
file on disk, **and** not visible to `principal` must all answer identically.
A distinguishable refusal is an existence oracle over another principal's
files: a member could learn that row 412 exists by naming it and reading which
sentence came back. This is the same rule the vision column has always applied
to its own kinds.

### If you CONSUME one — resolve it, never import it

```python
from agents.contracts.artifacts import file_resolver_for
from models.contracts.jobkinds import resolve_dotted_path

resolver = file_resolver_for(kind)
if resolver is None:
    raise ValueError(f"{kind}:{pk} does not name a stored image.")
try:
    artifact = resolve_dotted_path(resolver)(pk, principal)
except LookupError:
    raise ValueError(f"{kind}:{pk} does not name a stored image.") from None
```

Quoted in substance from `tools/vision/services.py::_stored_document_input`.
Three rules the working example keeps, and a new consumer should too:

- **No registered resolver is a dead reference, not a crash.** The owning
  column is simply not installed on this box, so the reference cannot be live
  — and from the consumer's side that is indistinguishable from a row that is
  gone. Reuse the sentence you already have for a dead reference rather than
  inventing a second one.
- **Check the type you actually need, and refuse by name.** A resolved file
  that is not the medium your tool takes gets its own honest sentence
  (`document:12 is application/pdf, not an image.`) — a plain `ValueError`, so
  the tool loop turns it into a refusal the model can read and recover from
  next turn.
- **Echo the bare `kind:<id>` form in every refusal.** A `document` reference
  may carry a percent-encoded display title, which is somebody's uploaded
  filename. An operator-facing sentence built out of an uploaded filename is
  how a filename becomes prose a human reads.

**A kind with no registration resolves to nothing, silently** — the same
posture the taint registry above takes, for the same reason. `output`/`input`
have no registration today because the vision column still resolves its own
kinds in-column; registering them is a zero-cost follow-up that changes no
caller.

## Adding an entitlement axis

An entitlement is one object seen from two directions. From a RESOURCE you
ask "which entitlements label this document, tool or agent" — the per-column
label pages answer that. From the ENTITLEMENT you ask "which resources carry
me", and change the answer: `identity-entitlements` lists one reach column
per kind, and `identity-entitlement-edit` renders one two-pane transfer
panel per editable kind.

`identity/` may not import your column (import-law rule 4), so the second
direction is a **registration**, exactly like a cascade or a workstream
panel — never an edit to `identity/views.py`, which has no way to name your
join table.

One call in your `AppConfig.ready()`, in the SAME COMMIT as the module it
names (`identity/axes.py` resolves with `import_string` and never swallows,
so a registration that landed before its module would make every
entitlement page raise):

```python
from identity.contracts.axes import EntitlementAxis, register_entitlement_axis

register_entitlement_axis(EntitlementAxis(
    key="agents.tools", label="Tools", order=10,
    counts="agents.axes.tool_counts",
    rows="agents.axes.tool_rows",
    ids_for="agents.axes.tool_ids_for",
    set_for="agents.axes.set_tool_axis",
))
```

Quoted from `agents/apps.py` — the real, shipped registration for the Tools
axis. The fields:

- **`key`** — a stable identifier, namespaced by your column. It is also the
  `id` of the panel's own section (`#axis-agents.tools`), which a save
  redirects back to.
- **`label`** — the PLURAL noun both projections print: a column heading on
  the list, a section heading on one entitlement's page.
- **`counts`** — a dotted path, `() -> dict[int, int]`:
  `{entitlement_id: rows}` for EVERY entitlement, in **one query**. This one
  is required. The list page renders fifty rows off one call per axis; a
  per-entitlement counter would be an N+1 on the page whose whole job is to
  hold fifty rows, and `identity/tests/test_entitlement_pages.py` pins the
  query count equal at one row and at thirty.
- **`order`** — display order across all axes, low first. Explicit rather
  than inherited from `INSTALLED_APPS`, so moving an app in the settings
  file cannot reshuffle a page's columns.
- **`hint`** — optional, one sentence under the section heading (and under a
  door's, below).
- **`link`** — optional, a dotted path `(entitlement_id) -> str` answering the
  URL of **your own page**, narrowed to that entitlement. Register it and the
  entitlement page renders a **door** for your axis: your `label` as the
  heading, your `hint` as the sentence, and that link. This is how a kind that
  is counted here but edited on its own page — document labels, today — sends
  an operator where the editing happens, and it is why `identity/` names no
  column's route: the URL comes back from your function, resolved at render
  time, never `reverse()`d there. `tools/rag/axes.py::document_link` is the
  shipped one, three lines long.

Then the **editing trio — all three or none**. Register only `counts` and
your kind is counted from this direction but edited from its own page
(`tools/rag/apps.py` does exactly that for document labels, and
`tools/rag/axes.py` says why). Register all three and it gets a transfer
panel:

- **`rows`** — `() -> list[AxisRow]`, your whole catalogue in display order.
  `AxisRow(id, name, note="")`; `id` is a string even when the underlying
  key is an integer pk, because it arrives back from a form body either way.
  **This list is also the validator**: an id not in it is refused before any
  write, so a feature-gated row absent from this install is not labellable
  by a hand-typed form.
- **`ids_for`** — `(entitlement_id) -> set[str]`, which rows carry it, in
  one query. Return integers if that is what your table holds; the resolver
  normalises them to strings.
- **`set_for`** — `(entitlement_id, *, add, remove, actor, actor_user) ->
  {"added": n, "removed": n}`.

**`set_for` takes ADD and REMOVE, never a whole wanted set**, and it must
write through your column's EXISTING single writer rather than touching the
join table itself. Both halves of that are load-bearing:

- A whole-set write clobbers: two administrators editing different rows of
  the same axis each ship the other's stale pane back, and the second save
  wins. A difference cannot.
- Your single writer (`agents.labels.set_tool_labels`,
  `models.registry.labels.attach`, …) is where the audit rows are written,
  and `/chat/access/` reads that trail. A second write path is a second
  place to forget it.

Those writers take one RESOURCE's full entitlement set, so the transpose is
one call per resource that really changes — see `agents/axes.py::_apply`,
which is the shape all three of that column's axes share.

**A batch is N transactions, not one.** Each single writer opens its own
`transaction.atomic()` around one resource, so twenty ticked rows are twenty
transactions in a stable sorted order, and a crash part-way leaves a partial
change with an honest audit trail for the part that landed. Do not try to
wrap the batch in one transaction from your `set_for`: a single transaction
spanning the batch means bypassing the writers that audit, which is the one
thing this seam exists to prevent. Say so in your own docstring, as
`agents/axes.py` does, so the next reader does not assume atomicity the
platform never promised.

**What you get for free:** a searchable reach column on the entitlements
list, a two-pane transfer panel with per-pane filtering that works with
JavaScript off, catalogue validation, the flash summary, the anchor
redirect, the administrator-only gate
(`identity.services.set_entitlement_axis`), and — with `link` — a door into
your own page. No route, no view, no template and no migration: that claim is
enforced rather than merely stated
(`foundation/ops/tests/test_import_law.py::test_identity_names_no_other_columns_route`).

**What you do not get:** an entitlement OWNER cannot use the panels. Axis
editing is administrator-only — an owner's three capabilities are granting,
revoking and labelling documents — so do not register an axis expecting
owners to reach it.

**And you do not get the RESOURCE-major page for free.** The registry answers
"which of your rows carry this entitlement"; the other direction — "which
entitlements label this row" — is your column's own page, because only your
column knows what a row of yours looks like. What you can reuse there is the
same component: `{% include "_transfer_panel.html" %}` with panes of
entitlements instead of panes of resources, which is exactly what
`/chat/tools/`, `/chat/access/` and the agent editor at `/chat/agents/<pk>/`
do (`agents/chat/views/tools.py` for the view side,
`agents/chat/service.py::parse_entitlement_diff` for the POST — which is the
gate, while the `set_*_labels` writers below it are raw and enforce nothing).
The fragment's own CSS lives in `foundation/templates/_shell.html`, so a
consumer anywhere in the tree gets it styled; it was promoted there from
`_settings.html` by the first consumer outside the settings area, and
`foundation/ops/tests/test_css_ownership.py` is what keeps it in one place.
Registering an axis and never writing that page is a legitimate choice —
`tools/rag` does it in reverse, editing documents on its own page and
registering counts only.

The registration is pinned the same anti-vacuous way cascades are: pop the
key, prove it is gone, run `ready()` again, prove it came back
(`agents/tests/test_axes.py`). Cross-reference: the delete side of the same
seam is `identity/contracts/cascades.py`, and your column almost certainly
wants both.

## Adding a settings page

Five steps get a control onto a page and into the sidebar; a further set,
below, gets the value it edits stored, read and written coherently. Four of
the first five have a test that goes red if you skip them; the fifth is the
one that does not, which is why it is written out at length. Read "Where a
setting lives" first — it decides which of the backend steps even apply.

**The most recent page built from this recipe end to end is "Job
execution"** (`models/queue/views.py::JobSettingsView`, `models/queue/
templates/jobs/settings.html`, F1 / Coherence Wave C) — a settings page
for a singleton that already existed, whose controls had been embedded in
an activity page. Read it beside the steps below if a step reads
abstract: it is one worked instance of every one of them.

### Where a setting lives

Before adding a field anywhere, decide which storage tier it belongs to.
This is folklore today, argued independently in two model docstrings and
one view docstring rather than written down once — restated here as the
single place to find all three, which does not replace those docstrings;
keep reading them too. (`tools.rag.models.RagSettings` and
`models.queue.models.JobSettings` carry their own docstring arguments for
the singleton *pattern*; the placement reasoning below is
`identity/models.py`'s, `agents/models.py`'s and
`agents/chat/views/settings.py`'s.)

  (a) **A settings row belongs to the column that reads it.** Don't hang a
      new field on a neighbouring singleton because it is convenient. The
      worked case is `agents.models.ChatSettings`, which exists as its own
      model rather than a column on `IdentitySettings` (posture, sessions,
      admin content access) or `tools.rag.models.RagSettings` (retention,
      upload caps, retrieval) precisely because neither of those pages is
      about how a conversation's prompt is assembled — "a settings row is
      read by the code that owns the subject" (`agents/models.py:959-965`,
      restated `agents/chat/views/settings.py:4-10`).

  (b) **Anything web, worker and watcher must agree on is a DB row, never
      an environment variable.** `compose.yaml` starts those three
      processes with independently supplied environments, so an env var
      set on one process can be unset on another — and the worker is
      where turns actually run. The database is the one thing all three
      provably share (`identity/models.py:51-60`, restated
      `agents/models.py:951-958`). It is also the recorded reason this
      box's earlier accounts-on/off env var was deleted in favour of the
      `IdentitySettings.posture` row it was replaced by
      (`docs/adr/0016-identity-and-entitlements.md`).

  (c) **A value that must be validated against database facts is a DB
      row.** An environment variable cannot be refused; a write that
      checks a database fact first can be. `identity/models.py:57-60`
      states the case that motivated the rule: switching away from the
      open posture is refused unless an active superuser exists, which
      only a database write — never an env var — can enforce.

  (d) **A value that varies PER REGISTERED CONNECTION is a
      `ModelConnection.config` key — and it must be added to the list
      below in the same commit.** This is the fourth storage tier, and
      the one nothing used to tell an author about (S7,
      settings-backend audit). It is not a singleton row and not an
      environment variable: it is a JSON blob on each connection, seeded
      into `models.contracts.bindings.ResolvedModel.config` by
      `models/registry/bindings.py::resolved_from_connection` and
      splatted into the engine adapter's builder as `**config`
      (`models/contracts/gateway.py`). Use it when the value is a fact
      about ONE operator-registered model — the companion files a
      multi-file checkpoint family needs, a per-endpoint timeout — and
      not a policy the whole box shares. A dedicated column always wins:
      `context_window` is a real field on `ModelConnection`, and a key of
      the same name in the JSON is shadowed, never merged.

   It is **schemaless**. There is no validation, no migration, no help
      card and no form field generated for a new key — which is exactly
      why it is the path of least resistance for the next per-engine
      knob, and exactly why it needs this list. The rule is the whole
      mechanism: **a new key that any code reads must appear in the table
      below**, and a key that no code reads any more must leave it.

<!-- CONNECTION-CONFIG-KEYS:BEGIN -->

| Key | Read by |
|---|---|
| `client_kwargs` | the chat/embedding adapter, passed through to its HTTP client (a timeout, TLS settings) |
| `context_window` | the chat adapter's prompt budget — normally supplied by `ModelConnection.context_window`, the dedicated column, which shadows any JSON key of this name |
| `family` | which graph template an image adapter builds, and whether a bind-time note warns that companions are missing |
| `language` | the transcription adapter's language hint, when the caller passes none |
| `request_timeout` | how long the chat and transcription adapters wait on one call |
| `text_encoder` | the companion text-encoder file a multi-file family loads; a blank one fails the job with an operator-facing sentence |
| `vae` | the companion VAE file, same rule as `text_encoder` |
| `variant` | which build of a declared family is wired, and that family's own parameter defaults |

<!-- CONNECTION-CONFIG-KEYS:END -->

   *Add a key and skip the table, or leave a key here that nothing
      reads →* `foundation/ops/tests/test_connection_config_keys.py`
      catches both directions. It sweeps every production module for the
      spellings a config key is actually read by and compares that set
      against this table, so the list cannot drift from the code in
      either direction. It does **not** validate values: runtime schema
      validation for this tier is a recorded owner-call (ADR 0018, G13),
      deliberately not built.

None of this is new invention. Rules (a)-(c) are the reasoning two model
docstrings and one view docstring already carry, each written the day its
own singleton was added; rule (d) is the tier those three never mention.
Recording
it once here is what keeps the next one from re-deriving it, or skipping it
because the column a new value seems to belong to looks close enough to an
existing one.

### 1. The page

A view and a template extending `foundation/templates/_settings.html`,
writing its body into `{% block settings_content %}` and overriding its
own `side_current_*` block. A sub-page marks its *parent's* entry — model
sets marks Models, an entitlement's own page marks Entitlements — because
the sidebar names sections, not URLs.

### 2. The route rule

An entry in `identity/routes.py::ROUTE_RULES`, plus a `_DRIVERS` entry in
`identity/tests/test_route_matrix.py`.

*Skip it →* `test_every_route_has_a_driver` fails, and an unclassified
name is treated as admin **and logged**.

### 3. The sidebar entry — in BOTH tables

An `Entry(label, url_name, gate)` in
`foundation/settings_area.py::SETTINGS_GROUPS`, **and** the matching
`<a>` in `foundation/templates/_settings.html`'s sidebar, in the same
group and the same position, with its own `{% block side_current_… %}`
for the page to mark itself with. Two readers, one truth: the table is
what `/settings/` redirects by, the template is what a viewer clicks,
and a context processor is not an option (it would run on every page in
the box and cost a query the console's own count pin forbids) — see
`foundation/settings_area.py`'s own "ONE TABLE, TWO READERS".

*Skip the `Entry` →* `foundation/tests/test_page_names.py::
test_names_covers_every_settings_sidebar_entry` names your route (and
the `_NAMES` row it wants is step "Two more that are easy to get wrong",
below).

*Skip the template half, or put it in a different place, or let its link
text drift from `Entry.label` →* `foundation/tests/test_shell.py::
TestTheSettingsSidebar::test_the_sidebar_is_the_table_and_lands_where_
settings_sends_you`. It compares the WHOLE ordered list of rendered
`(href, label)` pairs against `visible_entries(...)`, across four
principal/posture cells and both feature states — so this half is
guarded as tightly as the `Entry` itself, in position, href and label
alike. What it does NOT guard is the `{% block side_current_… %}` name:
a page whose block name no other template mentions simply never marks
itself current, silently.

### 4. The help card

A `HelpCard` in `foundation/settings_help.py`, one `HelpField` per
control. **Four rules, because none of them is testable:**

**(a) Describe the controls THIS page renders, and no others.** The drift
tests only ask whether the anchor exists on the page the card names, so a
card that claims a neighbouring page's control passes every one of them
and misroutes every answer about it. The worked case is the one the
settings assistant is judged by: `library_posture` is an
`IdentitySettings` column edited only on **Identity & security**, so the
`identity-settings` card carries it and the `rag-settings` card must not
— that page owns the library's numeric limits, none of which is a
posture. A `rag-settings` card that claimed it would send every "make the
library admin-only" answer to the wrong page, with a green suite.

**(b) If a control only renders in some postures, say so in its
`meaning`.** The anchor assertion renders **one** posture and cannot see
the others. The same field is the worked case:
`identity/templates/identity/settings.html` branches on
`posture == "personal"` only, so library posture is a real select on an
open **and** an enterprise box and a hidden input with an explanation on
a personal one. A card that described it flatly would be wrong in one
posture out of three and right-looking in every test run. Deep links are
unaffected either way — the `.field` wrapper that carries the anchor
renders in all three postures and only its contents branch — so this is a
rule about what the card **says**, never about whether the link lands.

**(c) Prefer a multi-word or otherwise distinctive `title` where the
obvious human name is a common English word.** A card's `title` is a live
text matcher over every assistant answer's prose, not only a label: the
settings assistant auto-links the FIRST occurrence of a card's own
`title` or `route_name` spelling wherever it appears in a rendered answer
(`agents.chat.context_processors::_linkify_named_pages`), plus a
page-level strip link besides (`_named_page_links`). A **one-word**,
generic title ("Models", "Library", "Chat", "Accounts", "Groups",
"Entitlements") reads as ordinary English the way a multi-word one
("Identity & security", "Tool access") almost never does. The Q7
tightening (`_requires_exact_case`) bounds the damage — a one-word title
links only on the registry's own exact capitalisation, or
case-insensitively when its own card was fetched this turn — but does
**not** eliminate it: an exact-case occurrence of a one-word title
anywhere in any answer still links, whatever the sentence is actually
about. This is precisely the battery finding that motivated the rule: an
answer about library ACCESS POSTURE auto-linked the stray word "library"
to the numeric-limits page, because that page's own title happens to be
that word. Nothing forbids a one-word `title` — none of the existing
cards were renamed over this — but a new card is the one place
this cost is still cheap to avoid by choice, before the matcher has to
narrow further to cover it.

**(d) If a control only takes effect while a `FARABUNKER_FEATURES` token
is on, say so in its `meaning`.** A field can be honest about what a
control does and still mislead an administrator on a box that has the
relevant feature off — the worked case is `rag-settings`'s "Maximum media
duration" and "Maximum document pages" fields, which describe caps that
have nothing to enforce until "media" is enabled — **for two different
reasons, and the rule is not about either one**: the media cap's own
extensions are not even accepted for upload with the flag off
(`tools/rag/views.py::supported_upload_exts` only adds `AV_EXTS`/
`IMAGE_EXTS` under the flag), while the page cap's are — `PROSE_EXTS`
is accepted unconditionally — and it is the vision-extraction branch the
cap sits on that is gated (`tools/rag/ingest.py::_check_document_pages`).
What the rule asks for is the same in both cases: say so in the
`meaning`. This is a narrower case of
rule (b) above — a flag is just another condition a control's real effect
depends on — stated separately because a flag's absence is easy to miss:
nothing about the page itself changes shape the way a posture branch
does, so there is no rendered branch to remind a reader the caveat is
missing.

*Skip the card →* `test_every_settings_entry_has_a_card_and_every_card_has_an_entry`
(`foundation/tests/test_settings_help.py`), naming your route. *Get its
contents wrong, or its title's word-collision risk, or leave out a flag's
effect on it →* **no test catches you; these four rules are the only
guard.**

*Add a model field with no `HelpField` at all, or fold it into an
existing field so it shares that field's anchor →* (S5, Coherence Wave
B) `foundation/tests/test_settings_help.py::TestTheModelFieldCoverage`
DOES catch this one, on the four settings singletons specifically
(`RagSettings`, `JobSettings`, `IdentitySettings`, `ChatSettings`): every
concrete field on each must be either a real, individually-anchored
`HelpField` on its own page's card, or named in that test's own
`_NAMED_EXCLUSIONS` with a reason. This is the guard the surface audit's
F2/S5 finding named directly — `RagSettings`' `retrieval_top_k`/
`retrieval_score_floor`/`hybrid_search` were bundled into one "Retrieval"
field sharing one anchor, which the anchor-existence check above is
satisfied by just as happily as by three separate ones, and this is the
guard that would have caught it. It does NOT check rules (a)–(d) above —
a field CAN be present, correctly anchored, and still describe the wrong
page, the wrong posture, or omit a flag caveat, with this guard green.

### 5. The anchors

A stable `id=` on each section a field names, **in every branch of the
template**. The id names the control's own section wrapper, not a label
and not an input, and it is stable vocabulary rather than a slugified
heading — renaming a heading must not silently break a link. Where a page
has no section wrapper to name — a `{% for %}` loop with no container, or
a single control (a link, a button) with nothing wrapping it either —
use an empty `<span id="…"></span>` immediately before it: that is the
tree's own anchor precedent, at
`models/registry/templates/inference/console.html`, which carries both
shapes -- section-less loops, and `#model-sets`, beside a single
"Manage model sets" link with nothing else wrapping it.

**Check the branch you are NOT looking at.** A page with mutually
exclusive branches carries the id in each; and an id inside a
conditional that a fresh box does not satisfy is an id the anchor
assertion will not find. If a section only sometimes renders, anchor the
unconditional point above it instead.

*Skip it →* `test_every_anchor_a_card_cites_exists_on_the_rendered_page`,
naming the card and the anchor.

### The backend half

The five steps above get a control onto a page. They say nothing about
where the value they edit is stored, read or written — because until now
nothing did. This is the concrete recipe for adding one field to an
existing settings singleton, worked against the two most recent real
additions (`RagSettings.retrieval_score_floor`/`hybrid_search`). Read "Where a
setting lives" above first: if the new value fails any of its three rules,
it does not belong here at all.

**Steps, in the order you will actually do them:**

1. **The model field**, plus its `*_DEFAULT` class constant (and
   `*_MIN`/`*_MAX` if the value is bounded) on the singleton's model class.
   No guard — nothing fails if you forget the default and rely on Django's
   own field default instead, but every existing singleton states its
   default as a named constant so the view and the help card can cite the
   same number rather than repeating a literal.

2. **The migration.** `manage.py makemigrations --check` fails if you
   forget it — the one step in this whole recipe with a guard that is
   specific to it.

2b. **The write endpoint — and there is exactly ONE per settings page.**
   A settings page exposes a single dispatched POST endpoint; per-field
   URLs are retired. The form carries a hidden input naming which of the
   page's forms submitted, the endpoint maps that value to the handler
   that validates and saves it, and an unrecognised value is a flash and
   a redirect (never a 500, never a raw 400, and never a silent no-op —
   an unchanged page reads as success). Both settings pages that write a
   numeric singleton are this shape and are worth copying from:
   `models/queue/views.py::queue_settings_update` (a hidden `form` field
   naming one of two forms, each saving its own pair of fields) and
   `tools/rag/views.py::library_settings_update` (a hidden `field` input
   naming one of seven fields, keyed by the POST field name the form
   already submits, so a form cannot dispatch to a handler that reads a
   field it did not send).

   **So a new field usually needs no new route at all** — it joins the
   dispatch table its page already has. That is the point of the rule:
   the seven per-field URLs `tools/rag` used to carry meant three touch
   points (a path, a `ROUTE_RULES` class, a `_DRIVERS` row) per field
   added, and the route matrix grew a near-identical block each time
   (S2, Coherence Wave C, which collapsed them).

   **A save has two halves, and both are yours to write.** The form
   gives the assistant panel's open flag: every `action` on a settings
   page appends `{{ assistant.open_query }}`, exactly as every sidebar
   link in `foundation/templates/_settings.html` and the app bar's
   `Settings` entry in `_shell.html` do — a new page inherits the chrome's
   own links from the shell and writes the suffix on each of its own
   forms. The view gives it back: the redirect goes through
   `foundation/settings_area.py::settings_redirect`, never a bare
   `redirect(...)`, which re-appends the flag when the submitting request
   carried one and is a plain `redirect` when it did not. Miss either half
   and the panel shuts on every save.

   **The unsaved-input guard is the one thing you inherit and cannot
   forget.** `foundation/templates/_settings.html` carries a single
   `beforeunload` scan for the whole area, so a new settings form is
   guarded the moment it renders: navigating away with a control whose
   value differs from the one the server rendered prompts first. There is
   nothing to opt into and, deliberately, no opt-out pattern — the scan
   compares against `defaultValue`/`defaultChecked`/`defaultSelected` at
   unload time, so a field the operator reverted by hand is clean again,
   and hidden inputs, buttons and disabled controls are never counted. A
   SAVE never prompts about the form it is saving: the same script
   records the submitting form and skips it. What that does mean for you
   is that a form whose values your own script rewrites after render will
   read as dirty — which is usually correct, and is why the models
   console's endpoint autofill only fires on an engine change the
   operator made.

   **One rule it puts on your own scripts: a settings-page script that
   cancels a submit must cancel it on the FORM, never in a listener
   delegated to `document`.** The guard reads `defaultPrevented` at the
   document bubble — after every handler bound to the form itself, but
   before any document listener registered later, and your own
   `{% block scripts %}` always renders after the guard does. A cancelled submit the guard does
   not see leaves that form exempt for the rest of the page's life, which
   silently restores the edit-loss the guard exists to close. Bind to the
   form, the way `chat/_assistant_panel.html` does. For the same reason a
   settings-area `<form>` must not carry `target`: it fires `submit` and
   never unloads the page, so the exemption is never spent. Both halves
   fail the build in `foundation/tests/test_shell.py::
   TestTheGuardsOrderingInvariant`, which derives the settings area from
   `_settings.html` itself rather than from a list somebody has to
   remember to extend.

   Both halves are swept by `identity/tests/test_route_matrix.py::
   TestEverySettingsSaveKeepsTheAssistantPanelOpen`: one test renders
   every page in `card_routes()` with the panel open and reads the form
   actions off the HTML; the other drives every POST endpoint whose
   redirect lands in the settings area. Both derive what to check from
   the page and the `_DRIVERS` row you are adding anyway, so a new
   settings page that forgets either half fails there — and the handful of
   forms that legitimately do not carry the flag (the panel's own three,
   sign-out, and the one endpoint that cannot honour it) are named in
   `_UNFLAGGED_ACTIONS` with their reasons rather than quietly skipped.

   **If you are adding a settings PAGE**, its one endpoint does need
   those three loud-guard touch points, which "2. The route rule" above
   already covers: a URL entry (`test_route_matrix.py` fails with a
   `KeyError` naming itself if the `_DRIVERS` row is missing), a
   `ROUTE_RULES` class in `identity/routes.py`
   (`test_every_route_has_a_driver` fails and an unclassified name is
   treated as admin, and logged), and the `_DRIVERS` row itself — and
   that row must be NON-VACUOUS: drive it with a real dispatch value and
   that value's own payload, or every caller takes the refusal branch and
   the matrix pins nothing. Adding one field to an existing settings
   singleton touches fourteen places end to end, and these three are the
   only ones that fail loudly — every other one of the fourteen is
   memorised, which is why this recipe exists at all. Restated here
   rather than only above, because a new POST endpoint is
   exactly the moment this recipe's own steps and the five UI steps
   above collide.

3. **The validation idiom.** The parse-and-refuse shape is still not
   unified — each singleton hand-rolls its own (`tools/rag/views.py`'s
   `_ragsettings_field_update`, `identity/forms.py`'s `Form` fields) —
   but the upper-bound half of it now IS: a new integer settings field's
   ceiling check goes through `foundation.settings_bounds.
   exceeds_field_ceiling`, passing `BIGINT_FIELD_MAX` for a
   `BigIntegerField` or `POSITIVE_INT_FIELD_MAX` for a
   `PositiveIntegerField` (or a narrower, field-specific ceiling if the
   value has a real meaningful range smaller than the column's, e.g.
   `RagSettings.RETRIEVAL_TOP_K_MAX`) as `max_stored`. Reject non-numeric
   and out-of-range input the way every existing handler already does;
   `foundation.settings_bounds` only closes the specific gap a settings
   integer with no upper bound is: a real, already-seen bug class (an
   operator-typed value large enough to overflow the column, T10 review
   MINOR 5, generalised at S1, Coherence Wave B). No guard beyond that
   one check.

4. **The read idiom.** `Model.get_solo()` only — **never**
   `Model.objects.filter(...)` or `.first()`, which would be a second door
   around every write-time guard the singleton has. If the same request
   reads the row more than once, thread it as a plain argument rather than
   calling `get_solo()` again; `identity/request.py::settings_row_for` is
   the named version of this pattern (fetch once in middleware, thread an
   optional `settings_row=` parameter to every consumer) and is worth
   copying outright for a column with more than one or two read sites. No
   guard — nothing fails if a second read site is added, though a query-
   count test is the cheap way to pin one if the column is read somewhere
   hot (`tools/rag/tests/test_ingest.py`'s `mock_get_solo.call_count`
   pins are the worked example).

5. **Caching — almost certainly none.** Settings singletons are never
   cached: a cache would be a second truth with a staleness window, and
   for anything security- or policy-relevant that staleness window is
   exactly the interval in which the box is wrong
   (`identity/models.py:62-66`). If what you are adding is *per-row*
   config rather than a singleton field (a `ModelConnection` column, for
   example), the caching pattern to follow instead is
   `models/registry/availability.py`'s: a TTL for other processes, a
   generation counter snapshotted before the read and compared after, and
   invalidation on `on_commit` rather than `post_save`. No guard either
   way — this is a design choice a reviewer has to catch.

6. **The audit decision.** Decide, explicitly, whether this write gets an
   `AuditEvent` row, and record the decision in the view's own docstring
   rather than leaving it silent. The house default is that operator-
   policy singleton/config writes ARE audited; an omission is a recorded
   decision, not an absence. `agents/chat/views/settings.py:25-30` is the
   worked example: `ChatSettings.time_aware` is deliberately unaudited,
   and the docstring states why ("operator policy about prompt text... not
   a security posture") rather than leaving a reader to guess whether the
   omission was a choice or a gap. No guard — nothing today ties a
   settings write to an audit-coverage assertion, so this step lives or
   dies on the docstring actually being written.

7. **`settings.overview`.** If the new field is on `agents.models.
   ChatSettings` (which `agents/` imports directly) or on
   `identity.models.IdentitySettings` (which it reaches through the
   `identity.access` seam — `agents/` may **not** import
   `identity.models`; the permitted identity seams are exactly
   `identity.contracts`, `identity.access`, `identity.request` and
   `identity.audit`, pinned by `IDENTITY_PERMITTED` in
   `foundation/ops/tests/test_import_law.py`, and `run_overview` reaches
   the row via `from identity.access import is_admin, settings_row`) —
   add it to `agents/settings_tools.py`'s
   `REPORTED_SETTINGS_FIELDS`, its data dict, its text lines and its tool
   description, the same information the help card states, restated for
   the tool that reports current values rather than explains controls.
   If the new field is on `tools.rag.models.RagSettings` or `models.
   queue.models.JobSettings` instead, `agents/` may not IMPORT that
   table directly (import-law rule 3, zero exceptions for `tools/`;
   `models.queue.models` is a FORBIDDEN_MODULE to every column but one
   narrow backup carve-out) — but that is narrower than "impossible to
   report" (corrected at I1, Coherence Wave B review, after a prior
   version of this step claimed it CANNOT be added at all). The lawful
   shape, if the owner ever wants coverage, is a registered read-only
   dotted-path provider — the SAME pattern `agents/contracts/
   workstreams.py`'s `register_workstream_panel`/`all_workstream_panels`
   already uses for `agents/` to reach `tools/rag` data without
   importing it, cited by `foundation/ops/tests/test_import_law.
   py:918`'s own doctrine ("the correct answer there is always a
   registry, never an import"). Building that registry is a parked
   owner decision, not part of this recipe — until it exists, add the
   field to `UNREPORTED_SETTINGS_FIELDS` instead, with a reason (S4,
   Coherence Wave B). Those unreported fields are also NAMED IN THE
   TOOL'S OWN OUTPUT, grouped under the PAGE they are edited on
   (`run_overview`'s `unreported` dict) — so moving a field to a
   different page means re-keying that group, which nothing checks: the
   COUNT beside each label is derived, the label and the prose are hand-
   maintained (N2, Coherence Wave B; re-keyed at F1, Coherence Wave C,
   when four fields moved from Queue to Job execution). GUARDED, unlike
   every other step here:
   `agents/tests/test_settings_tools.py::TestTheOverviewFieldCoverage`
   introspects all four singleton models directly (a test file, exempt
   from the import-law sweep) and fails if a concrete field lands in
   neither `REPORTED_SETTINGS_FIELDS` nor `UNREPORTED_SETTINGS_FIELDS`.

8. **The help card field.** Covered above, under "4. The help card" — it
   is listed again here only so this recipe reads as one list end to end.
   No guard beyond the anchor-existence check step 5 (of the first five)
   already states.

**Honestly: two of these eight steps have a guard of their own** — the
migration (step 2), and `settings.overview`'s field-coverage sweep (step
7, S4/Coherence Wave B). Step 2b, when it applies, borrows the three loud
guards "2. The route rule" already states, for the exact same reason
step 8 borrows the anchor-existence guard, at one remove; the other five
of the eight have nothing. Writing the recipe down does not add a guard
to most of them; it only means "memorised" now means "written somewhere,"
not "known only to whoever added the last field."

### Two more that are easy to get wrong and cheap to state

- The page's `<h1>` and `<title>` must agree with the sidebar label
  (`foundation/tests/test_page_names.py`).
- A leaf page overriding `{% block extra_style %}` writes
  `{{ block.super }}` **first**, or it silently drops `_settings.html`'s
  shared rules.

## Testing it

The four-run gate lives in one place, `docs/DEV.md`'s "The verification ladder", §8 rung 1 —
run it from there rather than a second copy here. Export `DATABASE_URL` for your own
database first: pick a name nobody else is using — never point a run at a bare
`test_farabunker`; see that rung for the full rule.

Three guards fail loudly if a step above was missed:

- `test_every_registered_runner_lives_in_a_swept_module` (`:456`) — step 3
  forgotten.
- `test_no_tool_module_imports_its_service_layer_at_module_scope` (`:507`)
  — step 1's lazy import forgotten.
- `test_no_tool_runner_blocks_on_a_queue_job` (`:359`) — a runner that
  waits on a queue job, the rule ADR 0015 Decision 5 states.

Add one line of your own: write the runner's own test the way every
existing one does — patch the service function it calls and assert the
call.

## What is not possible yet

- **No plugin system or entry points.** A tool is code in this repository,
  registered by an app you install.
- **No MCP import.** This platform is not yet a client of other MCP
  servers — that is the MCP-edge phase, and the export half is not built
  either.
- **No row-driven tools.** A `Flow` is a row, an `Agent` is a row, a
  *tool* is not — `ready()` may not read the database, which is why
  `flow.run` is one tool with per-turn choices rather than one tool per
  row.
- **No grants UI.** `tool_keys` is edited out of band until Identity &
  Auth.

The shape these gaps close in is recorded in ADR 0015's named-gaps
section; none of them changes the three steps above.

## Two tools already in the tree

Beyond `models.status` above: `settings.card` (`agents/settings_tools.py`) explains one
settings page — platform-authored help text, ungated by design (spec §5.3) — and
`settings.overview` reports this box's own current configuration to an administrator only,
refusing a non-admin **in its own runner**, not only at the surface.

## See also

- [`agents/contracts/README.md`](../agents/contracts/README.md) — the
  tool contract's own reference.
- [ADR 0015](adr/0015-agent-layer-and-tool-contract.md) — what the tool
  contract is, and why; amended for `ToolContext.stream`.
- [ADR 0016](adr/0016-identity-and-entitlements.md) — entitlements, labels
  and the taint mechanism the "Joining the taint stamp" section above
  registers into.
- `docs/superpowers/specs/2026-09-03-workstreams-design.md` — the binding
  spec for workstream panels (§4.2) and the taint stamp (§7).
- `docs/DEV.md`'s "The verification ladder" section.
- `models/registry/tools.py` itself.
