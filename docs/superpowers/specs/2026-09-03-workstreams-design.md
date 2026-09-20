# Workstreams — design spec

**Date:** 2026-09-03
**Amended (2):** 2026-09-03 — round-2 re-check of the amendments below (3 Major, 3 minor, 2 nit).
All 29 round-1 findings landed and nothing verified clean was disturbed; what the round-2 pass
found were three contradictions the *fixes themselves* introduced, two of them already inside
done-when criteria. Recorded as **orchestrator rulings E–G** (§23), also flagged for owner:
**E** — gate two names every missing entitlement (`disclose_all`), gate one and the owner's marker
keep ruling B's default; **F** — consolidation stays owner-only; **G** — containment fences the
corpus, not the bytes, so `sees_all_content` opens a contained document. Sections touched: §6.4,
§8.1, §10.4, §10.5, §12.1, §12.3, §12.4, §13, §15.3, §17.2, §17.3, §17.5, §19.1, §19.2, §22, §23,
§24.
**Amended:** 2026-09-03 — adversarial review (13 Major, 12 minor, 4 nit), applied in place rather
than appended, following the identity spec's own handling of a review: the sections that carry the
mechanics are amended where the mechanics live, and the four findings that needed a *decision* are
recorded in §23 as **orchestrator rulings A–D**, each flagged for owner. Sections touched: §1.1,
§4.2, §4.3, §5.4, §5.5, §6.1–§6.4, §7.1–§7.5, §8.1, §8.3, §8.4, §10.4, §10.5, §12.1–§12.4, §13,
§15.1, §15.3, §16, §17, §18, §19, §20, §23, §24.
**Status:** Design. Nothing built. Written against `HEAD = 10faba2`; the amendments above are
written against `d564348`, this document's own first commit, whose tree is identical to `10faba2`
for every file cited.
**Phase:** the phase after Identity & Auth, on the `/chat/` surface ADR 0015 built and IA-1/IA-2
narrowed (`docs/ROADMAP.md`, Phase 1.6).
**Lands as:** two plan-sized halves — **WS-1** and **WS-2** (§19) — then ADR 0017, written last.

A **Workstream** is a named, scoped work area: a place where conversations are born, documents
are contained or pinned, instructions apply, entitlements narrow, and what the work has touched
is remembered. It is the platform's answer to "everything about this matter, in one place, and
nothing else leaks in or out of it."

This spec designs it against a codebase that already has the four seams it needs and none of the
tables. Conversations exist and are owned, shared and listed (`agents/visibility.py`).
Documents exist and are labelled and filtered at one retrieval point
(`tools/rag/retrieval.py::retrieve_nodes`). Entitlements exist and are answered in integer ids
by one column that may not import the other two (`identity/access.py`). A queue exists and takes
job kinds as dotted-path strings (`models/contracts/jobkinds.py`). Every mechanism below is
threaded through one of those, and the places where this spec's own vocabulary and the tree's
disagree are recorded as numbered author decisions in §23 rather than quietly reconciled.

Two constraints shape everything. The first is the **import law** (ADR 0015 Decision 1, amended
by the identity spec §4.2): `agents/` may not import `tools/` at all, `identity/` may not import
either, and a Workstream touches all three columns. §4 answers that once and the rest of the
document lives inside the answer. The second is **postures**: the same tables must serve a
household box with one principal and no entitlements, and an organisation with many of both.
§13 says what every mechanism does in each.

No model or product names appear anywhere in this document. The repository is going public and
ADR 0010's third amendment forbids the platform from naming a model for the operator.

---

## Table of contents

1. [Context](#1-context)
2. [Owner decisions, restated](#2-owner-decisions-restated)
3. [What a Workstream is](#3-what-a-workstream-is)
4. [Where the tables live, and the import law](#4-where-the-tables-live-and-the-import-law)
5. [Data model](#5-data-model)
6. [The wall, at three seams](#6-the-wall-at-three-seams)
7. [Taint: what the work has touched](#7-taint-what-the-work-has-touched)
8. [Documents: containment, pinning, and the chunk cache](#8-documents-containment-pinning-and-the-chunk-cache)
9. [Upload placement is a conscious choice](#9-upload-placement-is-a-conscious-choice)
10. [Consolidation into stream notes](#10-consolidation-into-stream-notes)
11. [Instructions](#11-instructions)
12. [Sharing a workstream, and the two gates](#12-sharing-a-workstream-and-the-two-gates)
13. [Postures](#13-postures)
14. [Every new route, its class, and its rule](#14-every-new-route-its-class-and-its-rule)
15. [UI](#15-ui)
16. [Audit](#16-audit)
17. [Testing](#17-testing)
18. [Migrations, in order](#18-migrations-in-order)
19. [Phasing: WS-1 and WS-2](#19-phasing-ws-1-and-ws-2)
20. [Documentation](#20-documentation)
21. [Non-goals](#21-non-goals)
22. [Deferred, with the hook each relies on](#22-deferred-with-the-hook-each-relies-on)
23. [Decisions the author made](#23-decisions-the-author-made)
24. [Author concerns](#24-author-concerns)

---

## 1. Context

### 1.1 What exists today

Every mechanism this spec adds threads through a seam that already exists. None of them is new
machinery invented for this phase; the table is here so a reader can see that before the design
asks for anything.

| Seam | Where | What it does today |
|---|---|---|
| request → principal | `identity/request.py::principal_for_request` | the one point; `OPEN_PRINCIPAL` in open posture, `Principal("user", str(pk))` otherwise |
| entitlement ids | `identity/access.py::held_entitlement_ids` / `::owned_entitlement_ids` | `frozenset[int]`; identity answers ids, never rows |
| conversation listing | `agents/visibility.py::visible_conversations` | own rows (`owned_rows_q`) OR a `Share`; `.all()` when `sees_all_content` |
| the sidebar | `agents/chat/sidebar.py::sidebar_context` | one flat list, `SIDEBAR_LIMIT = 30`, active/archived, zero JavaScript |
| the system prompt | `agents/runtime/prompt.py::build_messages` | `agent.system_prompt` (only if non-blank), then history, then optional user text — **no other block** |
| the planner's intersection | `agents/runtime/jobs.py::plan_turn` → `agents/entitlements.py::tool_access_for` | `ToolAccess` built **once per turn** and threaded through `_tool_roles`; re-checked in `loop._run_turn` |
| the one retrieval filter point | `tools/rag/retrieval.py::retrieve_nodes`, filters from `::_visibility_filters` | a category clause AND an entitlement clause, over **chunk metadata** |
| what a turn touched | `agents.Turn.artifacts` (`["document:<id>", …]`; `mint_artifact` may append a `:<title>` suffix, which `parse_artifact` peels off — this spec writes the bare form throughout) and `Turn.data["citations"]` | minted by `tools/rag/tools.py::_document_artifacts`, written onto **each tool turn** at `agents/runtime/loop.py:407-419` and accumulated onto the assistant turn at `:421` → `:438` |
| documents | `tools/rag/models.py::Document` | one row per ingested source file; **no owner column, by design**; labels are its whole access story |
| the chunk-metadata cache | `tools/rag/labels.py::restamp_document_chunks` | ONE SQL `UPDATE` keyed on `metadata_->>'file_id'`, writing the `entitlements` key |
| sharing | `agents/models.py::Share`, `agents/shares.py` | a generic target (`target_type` + `target_key` as text), four target types, one UI (conversations) |
| the queue | `models/contracts/jobkinds.py::JobKind`, `models/contracts/queue.py::enqueue` | five registered kinds; `planner`/`handler`/`summarizer`/`on_terminal` are **dotted-path strings** |
| ingestion | `tools/rag/ingest.py::stage_document` → `::enqueue_ingest` → `::run_ingest_or_fail` | dedup on `(original_path, file_hash)`; a changed hash **reuses the same row** and re-ingests |
| a platform prompt constant | `tools/rag/extract.py::EXTRACTION_PROMPT` | the one precedent: a module constant beside its single-call function, framed as platform behaviour |
| the registries a column plugs into | `identity/contracts/ownership.py`, `identity/contracts/cascades.py`, `models/contracts/jobkinds.py` | three registries of **dotted-path strings** or `app_label.ModelName` strings, filled from each `AppConfig.ready()` |

### 1.2 The five facts that constrain the whole design

**`agents/` may not import `tools/`, and the gate is an AST sweep over every import node.**
`foundation/ops/tests/test_import_law.py::test_no_agents_module_imports_a_tools_package` walks
every tracked non-test file under `agents/` and fails on any `tools`-prefixed import, module
scope or in-function. The reverse is explicitly allowed and already used:
`tools/rag/tools.py` imports `agents.contracts.tools` and `agents.contracts.artifacts`. A
Workstream is a container of conversations **and** documents, so this one-way arrow decides
where its tables live (§4) and it is the single most load-bearing fact in this document.

**`identity/` may not import `agents/` or `tools/` at all, and it answers in ids.**
`identity/access.py`'s own header says it: *identity cannot answer "which documents"*. It
answers `frozenset[int]` and another column turns that into a queryset. So no identity module
can hold a table that FKs a conversation or a document, and the entitlement half of a
Workstream reaches identity the way every other label already does — a string FK to
`"identity.Entitlement"` from the column that owns the labelled thing.

**Retrieval's one filter point filters CHUNK METADATA, not a document queryset.**
`_visibility_filters` builds `MetadataFilters` over the LlamaIndex-managed `data_rag_chunks`
table's `metadata_` JSON, not a `Document.objects` filter. There is no candidate-document list
handed to the scorer. So "the wall applies before scoring" means *a clause in that filter*, and
"a document lives only in this stream" means *a key stamped on its chunks*, cached by the one
writer that already stamps `entitlements` (§8.3). Anything else would be a second filter point,
and the identity spec's §1.2 already recorded why there must not be one.

**What a turn actually retrieved is already recorded, twice, and neither record is a table.**
`Turn.data["citations"]` (from `rag.ask`) / `Turn.data["results"]` (from `rag.search`) hold the
per-tool-turn citation dicts; `Turn.artifacts` holds `"document:<id>"` reference strings minted
from exactly those dicts by `tools/rag/tools.py::_document_artifacts`, deduped on the bare
`document:<id>` key, and accumulated across every tool call in the turn onto the assistant turn
that `agents/runtime/loop.py::_finish` writes. §7.1 records which of the two this spec makes
the source of truth, and why.

**The database is the only durable state the platform trusts, and the worker is a separate
process.** `compose.yaml` starts the web process, the queue worker and the watcher from one
image with independently supplied environments. A consolidation job therefore runs in a process
that shares no memory with the page that asked for it, and everything it needs — the
conversation, the stream, the acting principal — travels in the job payload, as `agent.turn`'s
already does through `identity.contracts.principals.payload_fields`.

---

## 2. Owner decisions, restated

Every row is binding and was taken in the 2026-09-03 brainstorm. The "why" column is the
owner's own reasoning where they gave one, and this spec's where they did not.

| # | Decision | Why | Realized in |
|---|---|---|---|
| 1 | The name is **Workstream** — never "Room", never "Project". A Workstream is a project space; a `Conversation` lives inside **at most one**; a loose conversation keeps today's behaviour, untouched | one word for one concept, and the two products this resembles have taken the other two | §3, §5.1, §5.4 |
| 2 | **Stream-first creation only.** A chat is born in a stream or born loose. There is **no move affordance in v1**, and `Conversation.workstream` is immutable for life | a thread that can change containers is a thread whose taint history is a lie; the import rule that would make a move honest is designed and deferred (§22) | §5.4, §22 |
| 3a | **`scope_entitlements` — the WALL.** An optional subset of the acting user's grants, on the stream. Empty means full scope. It intersects into the existing IA-2 composition at three seams: retrieval (**before scoring**), the turn planner's entitlement intersection, and visibility/sidebar. Enforced server-side at those seams, **never** via prompt text | a wall a model is asked to respect is not a wall | §6 |
| 3b | **`tainted_entitlements` — the TAG SET**, on the conversation and on the stream. It accumulates the union of the entitlement labels of the documents retrieval **actually returned** into each turn, stamped at turn completion; the conversation's tags union upward into the stream in the same transaction. **Additive only** in v1. Every addition is audited with the causing turn | what a conversation has seen is a property of the conversation, and it is what makes a share decision possible later | §7 |
| 4 | **Containment is not pinning.** `Document.workstream` (nullable FK) is containment: null = the universal library as it is today; set = the document exists **only** in that stream's corpus, never in general Ask/Search and never in another stream, regardless of entitlements. **One row, one home, never duplicated.** An `origin` mark distinguishes notes from uploads. A separate **pin** table associates a universal document the user can already read into a stream's working set | duplicating a document to put it in two places is how two copies come to disagree | §5.5, §5.6, §8 |
| 5 | **Upload placement is a conscious choice.** Uploading inside a stream requires an **unpreselected** choice: universal library, or this workstream only. **No silent default.** The same form carries an unchecked box, "Use this choice for all future uploads to this workstream", which sets `Workstream.default_upload_placement` (null = ask every time). With a default active the form shows a visible "placed per stream default — change" note instead of the choice; with none active it hints that a default can be set. Uploads outside any stream — loose chat, the watcher inbox, the CLI — are universal, unchanged, and offer no choice | where a document lives is a decision, and a decision that defaults silently is a decision nobody made | §9 |
| 6 | **Consolidation is manual only**, an action on a stream conversation, run as a **queue job** because it is an LLM call. Output is **one contained note document per conversation** (`"Notes — <title>"`, `origin=notes`), **overwritten and re-ingested** on re-consolidation through the existing chunk/embed path. It never cascades and never touches another conversation's note. The distillation prompt is a **documented platform constant**, on the vision extraction-prompt precedent. A note **inherits its source conversation's taint tags** — no laundering. The stream page shows per-conversation staleness ("N turns since last consolidated") | a summary that quietly loses the labels of what it summarised is a hole with a friendly name | §10 |
| 7 | **Sharing generalises the IA-2 `Share` row to target a workstream** (conversations keep their existing share). A recipient may read and converse in a shared stream; re-sharing, editing the wall and changing pins stay owner-only in v1. **Two gates:** at **share time**, refused if the recipient lacks any tagged entitlement, and the refusal names the missing entitlements **to the sharer**, who holds them and can act; at **read time**, every non-owner read re-checks the tags against the reader's **current** grants, and a failing share goes **dormant** | tags grow and grants are revoked, so a share that was safe when made is not therefore safe now | §12 |
| 8 | **A dormant share is explicit to the invited reader**, naming the missing entitlements — "contains material from entitlements you don't hold: X, Y — ask an administrator, or the owner". A stranger or a revoked recipient gets the standard 404-shaped nothing. The owner's share list marks dormant shares with the reason | an owner override of the 404 house rule, scoped to somebody who already knows the thing exists because they hold a real share row | §12.2, §12.3 |
| 9 | **Instructions.** `Workstream.instructions` is injected as a clearly-labelled block appended to the agent's system prompt for stream turns | one place, one block, visibly the stream's words and not the agent's | §11 |
| 10 | **UI.** The chat sidebar gains a Workstreams section above conversations; entering a stream **scopes** the sidebar (the stream's name, its conversations, an "All chats" exit). The stream page carries instructions, the wall, pinned documents, notes with staleness, the upload default, the share list with dormant markers, and a New-chat composer. **Zero JavaScript** beyond the chat page's one existing sanctioned script | the sidebar is the navigation; a stream that is not in it is a stream nobody uses | §15 |
| 11 | **Taint scope, v1: only entitlement-labelled DOCUMENTS taint.** Images and tool outputs carry no labels today. The stamp must generalise mechanically if that changes | tainting on a thing that has no labels would stamp nothing and cost a query | §7.4, §22 |

---

## 3. What a Workstream is

### 3.1 The hierarchy, in one paragraph

A **Workstream** is a row. A `Conversation` has a nullable `workstream` foreign key: null means
loose — today's behaviour, unchanged in every respect — and set means the conversation was born
inside that stream and will never be in another. A `Document` has a nullable `workstream`
foreign key with a different meaning: null means the universal library, and set means the
document exists **only** in that stream. A **pin** is a third thing entirely: a row associating
a *universal* document with a stream so it appears in that stream's working set without moving.
Nothing is ever duplicated: one document row has one home, and a pin is an association, not a
copy.

### 3.2 The two entitlement sets, and why they are two

The stream carries two sets of entitlements and they are independent — different direction,
different lifecycle, different enforcement.

| | `scope_entitlements` — the WALL | `tainted_entitlements` — the TAG SET |
|---|---|---|
| Set by | the owner, deliberately, on the stream page | the runtime, automatically, at turn completion |
| Means | "narrow this stream to material under these entitlements" | "material under these entitlements has been in this stream" |
| Direction | **restricts** what may come in | **records** what came in |
| Bound | a subset of the setter's own grants; empty = no narrowing | a union of what retrieval returned; empty = nothing labelled has been seen |
| Enforced at | retrieval, the planner, visibility (§6) | share-time and read-time gates (§12) |
| Changes | only by an owner edit | only by a turn, additively, audited |
| If the two disagree | they cannot: the wall constrains what can be retrieved, so the tags are a subset of the wall **when a wall is set and has never been widened**. §7.5 records why the spec does not assert that as an invariant |

The wall is a **self-narrowing**: it can only ever remove entitlements from what the acting user
already holds. It is never a grant. §6.1 states that as a composition law with explicit
parentheses, because the prose form of the corpus rule is ambiguous about exactly this and the
ambiguity is a security question.

### 3.3 What is deliberately NOT a Workstream

- Not a tenant. One box is one organisation (identity spec §1.2); a stream partitions work, not
  machines.
- Not a permission. A stream never grants anything. Every read a stream permits is a read the
  reader's own grants already permitted (§6.1).
- Not an agent. A stream has instructions, not a personality, not tools and not a model binding.
  Per-stream agent defaults and model bindings are deferred (§22).
- Not a folder. A document has one home; a stream is not a place you file copies.

---

## 4. Where the tables live, and the import law

### 4.1 The placement question, answered once

A Workstream touches three columns: it contains conversations (`agents`), it contains and pins
documents (`tools/rag`), and it carries entitlement sets (`identity`). Exactly one placement
survives the import law.

**It cannot live in `identity/`.** Rule 4 forbids identity importing `agents` or `tools`, and
`identity.models` is off-limits to every other column with no exception. A `Workstream` there
would need a fifth sanctioned identity seam and a write path from the agents runtime into
identity's private models; that is two new holes in a law whose value is that it has few.

**It cannot live in `tools/rag`.** The sidebar, the stream page, conversation creation and the
prompt injection are all `agents/` code, and `agents/` may not import `tools/` **at all** —
not lazily, not in a function body. Every one of those four would need a registry indirection.

**It lives in `agents/`.** The direction that is already permitted and already used is
`tools/` → `agents.contracts.*`; the direction that is forbidden is `agents/` → `tools/`.
Putting the stream in `agents/` means the four agents-side mechanisms (sidebar, page,
conversation creation, prompt) reach it by ordinary import, and the one tools-side mechanism
(documents) reaches it by the permitted direction. One indirection remains — the stream page
must display documents, which `agents/` may not read — and §4.2 gives it the same shape the
three existing cross-column registries have.

Author decision 1 (§23) records this and the alternatives.

### 4.2 The two new seams, and what each is for

Two crossings are needed and they run in opposite directions. Each uses the mechanism that
already exists for its direction; neither invents a new law.

**Direction A — `tools/rag` needs to ask about a stream** (may this principal be in it, what is
its wall, where should this upload go). This is the permitted direction, and the answer is a
**named seam module**, exactly as `models.registry.bindings` is the one sanctioned cross-column
import into `models/registry`.

```
agents/workstreams.py     ← NEW. A cross-column seam for streams. May be imported by tools/
                            and models/; it is the SECOND agents module outside
                            agents/contracts/ that may be, beside `agents.entitlements`
                            (already a named seam — tools/vision/views.py:824 says so in
                            those words, and tools/rag/views.py:626 imports it too).
                            Imports agents.models, agents.visibility and identity.access;
                            imports nothing of tools/. NEVER imported BY agents.visibility:
                            the direction is one-way (workstreams → visibility), so the two
                            modules cannot cycle when §12 adds `stream_access` here and
                            `share_workstream` there (m11).
```

Its whole public surface, and nothing more:

```python
def workstream_scope(principal, workstream_id) -> WorkstreamScope | None
    """The pure scope value for `workstream_id`, or None when this
    principal may not be in that stream (or it does not exist — the
    caller cannot tell the two apart, and answers 404 to both)."""

def transcript_for(principal, conversation_id, *, limit: int) -> tuple[dict, ...] | None
    """The most recent `limit` replayable root-depth turns of a stream
    conversation, as pure dicts — `({"role": str, "text": str}, ...)`,
    oldest first — for the consolidation job, or None when the principal
    may not read it.

    NOT `prompt.history_messages`, and `limit` is why. That helper caps
    at `agents.limits.HISTORY_TURNS = 20`, which is a PROMPT budget for a
    live turn; consolidating only the last twenty turns would make
    section 10.4's "the conversation" false without saying so. This is
    its own query, with its own cap, named by its own caller."""

def record_consolidation(conversation_id, *, through_index: int, at) -> None
    """Stamp the conversation's consolidation bookkeeping (§10.5)."""

def taint_ids_for_conversation(conversation_id) -> frozenset[int]
    """The conversation's taint tags, for the note document to inherit
    (§10.4)."""
```

`WorkstreamScope` is a frozen dataclass in `agents/contracts/workstreams.py` — pure, no Django,
importable by anyone under rule 1:

```python
@dataclass(frozen=True)
class WorkstreamScope:
    workstream_id: int
    # Entitlement ids; empty = no narrowing. ALWAYS EMPTY when
    # `identity.access.accounts_on()` is False -- the wall is inert on an
    # open box and its rows are never read there (ruling A, section 13).
    wall: frozenset[int]
    default_upload_placement: str  # "" = ask every time
    may_upload: bool               # False for a share recipient (§12.4)
    # THE FIFTH FIELD IS FILLED BY THE OTHER COLUMN. `agents/workstreams.py`
    # returns this value with `pinned_file_ids=frozenset()`; only
    # `tools/rag/workstreams.py::scope_with_pins` fills it, because the pin
    # table lives in `tools/rag` (author decision 6, §6.2).
    pinned_file_ids: frozenset[int] = frozenset()
```

**Direction B — `agents/chat` needs to display documents.** This crossing is forbidden outright,
so it goes through a registry of dotted-path strings, the shape
`identity/contracts/cascades.py::EntitlementCascade` and `models/contracts/jobkinds.py::JobKind`
both already have:

```python
# agents/contracts/workstreams.py — pure, Django-free
@dataclass(frozen=True)
class WorkstreamPanel:
    key: str      # stable identifier, e.g. "rag.documents"
    label: str    # the page's section heading, e.g. "Documents"
    provider: str # "package.module.function", signature
                  # (principal, workstream_id) -> dict, resolved at
                  # render time by agents/workstreams.py, never imported here

def register_workstream_panel(spec: WorkstreamPanel) -> None
def all_workstream_panels() -> list[WorkstreamPanel]
```

`tools/rag/apps.py::RagConfig.ready()` registers exactly one:
`WorkstreamPanel("rag.documents", "Documents", "tools.rag.workstreams.panel")`. The stream page
renders whatever is registered, in registration order, through one include. A second panel later
— generated images in a stream, say — is a **registration**, not an edit to a view that would
otherwise silently skip it. That sentence is copied from `identity/contracts/cascades.py`'s own
docstring because it is the same argument.

**Registered in the same commit as the handler**, deliberately, for the reason
`tools/rag/apps.py` already records about its cascade: the resolver uses `import_string` and
never swallows, so a registration that landed before its module would make every stream page
raise `ImportError`.

### 4.3 The guards that grow

| Guard | Today | After |
|---|---|---|
| `test_import_law.py::test_no_agents_module_imports_a_tools_package` | every tracked non-test file under `agents/` | **unchanged**, and it is what makes §4.2's Direction B a registry rather than an import. `agents/workstreams.py` and `tools/rag/workstreams.py` are both in its sweep or its counterpart; neither may name the other |
| new: `test_tools_reaches_agents_through_contracts_entitlements_and_workstreams_and_nothing_else` | — | its own AST sweep over `git ls-files -- tools models`, allowing a **three-name closed set** — `agents.contracts.*`, **`agents.entitlements`** and `agents.workstreams` — and forbidding every other `agents.`-prefixed import, in the shape `test_import_law.py`'s `ALLOWED` dict already uses. **`agents.entitlements` is in the set because it is already there:** `tools/vision/views.py:832` and `tools/rag/views.py:626` both import `tool_access_for` today, and `tools/vision/views.py:824` calls it "a NAMED CROSS-COLUMN SEAM" in those words. A two-name allow-list would fail on the first WS-1 commit, before `agents/workstreams.py` exists. Modelled line for line on `test_agents_reaches_models_registry_through_bindings_and_nothing_else`, including its anti-vacuous pin: the test asserts each allowed name really is imported somewhere, so a sweep that stopped matching anything would fail rather than pass |
| new: `test_agents_workstreams_imports_no_tools_package` | — | the narrow, named twin of the sweep above, pinning that the one module other columns may import does not itself reach back across. Redundant with the broad sweep by construction, and kept because this module is the one whose accidental widening would be least visible in review |
| `test_column_boundaries.py`'s chat-`.objects` gate | `Conversation`, `Agent`, `Flow`, `Share` | **`Workstream` is added to `_VISIBILITY_MODELS`**, so no module under `agents/chat` queries streams directly either — every read goes through `agents/visibility.py` |
| new: `test_rag_views_reads_documents_through_the_access_module`'s sibling for pins | — | `WorkstreamPin.objects` outside `tools/rag/workstreams.py` and `tools/rag/access.py` is a violation, the same shape and for the same reason the `Document.objects` gate exists: two readers of "what is in this stream" is how two surfaces come to disagree |
| `identity/tests/test_purity.py` | imports `identity/contracts/` with no settings module | **unchanged**; `agents/contracts/tests/` gains the identical purity assertion for `agents/contracts/workstreams.py` — it is a rule-1 leaf and must import no Django |
| `identity/tests/test_route_matrix.py` | every url_name appears in `ROUTE_RULES` | **unchanged in shape**; it is what forces §14's table to be complete, and it fails naming any route this phase forgets |

---

## 5. Data model

Five new tables and six new columns. Every cross-column foreign key is declared as a **string**
(`"identity.Entitlement"`, `"agents.Workstream"`), never an import — which is exactly what
`DocumentEntitlement.entitlement` already does and what `settings.AUTH_USER_MODEL` exists for.

### 5.1 `agents.Workstream`

```python
class Workstream(models.Model):
    """A named scoped work area.

    OWNER COLUMNS, NOT A USER FK, matching every other owned row in this
    codebase (`Agent`, `Flow`, `Conversation`, `GenerationJob`,
    `AskRecord`): the open posture's single principal is not a `User`
    row, and `owned_rows_q` is the one predicate that already knows how
    to read these two columns in every posture.
    """

    class UploadPlacement(models.TextChoices):
        UNIVERSAL = "universal", "The universal library"
        CONTAINED = "contained", "This workstream only"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # Appended to the agent's system prompt for every turn in this
    # stream, as a clearly-labelled block (section 11). TextField, not a
    # capped CharField: it is a prompt, and a cap is a truncation nobody
    # asked for.
    instructions = models.TextField(blank=True, default="")
    # "" = ASK EVERY TIME (owner decision 5). Not `null=True`: a blank
    # CharField with choices is the shape `owner_kind`/`media_type`
    # already use for "not set", and a nullable choice column would give
    # one column two spellings of empty.
    default_upload_placement = models.CharField(
        max_length=16, choices=UploadPlacement.choices, blank=True, default="")
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True, default=None)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            # CASE-INSENSITIVELY UNIQUE PER OWNER, not globally: two people
            # on one box may each have a stream called "Taxes", and a
            # global unique would make the second person's stream
            # unnameable for a reason no page could explain. `Lower(...)`
            # matches `uniq_agent_slug_ci` and `uniq_category_name_ci`.
            models.UniqueConstraint(Lower("name"), "owner_kind", "owner_key",
                                    name="uniq_workstream_name_ci_per_owner"),
        ]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_ws_owner"),
        ]
```

**No slug.** A stream is addressed by integer pk in a URL and by name on a page. `Agent` and
`Flow` carry slugs because a slug is how a declaration in `agents/defaults.py` and a tool key
(`agent.<slug>`) name a row *in code*; nothing names a stream in code, and an immutable slug on
a user-renamed thing would be a second identity nobody edits.

**`archived_at`, a timestamp**, mirroring `Conversation.archived_at` (migration
`0005_conversation_archived_at`) rather than a boolean, for the reason that column already
records: "when" is strictly more information than "whether" and costs the same column.

**No `enabled`.** `Agent`/`Flow` carry one because a disabled agent must refuse a turn while
staying visible to its operator; a stream has no such state — archived covers "put it away".

### 5.2 `agents.WorkstreamScopeEntitlement` — the wall

```python
class WorkstreamScopeEntitlement(models.Model):
    """One entitlement in a stream's WALL (owner decision 3a).

    NO ROWS = NO NARROWING, which is what makes an unwalled stream cost
    exactly what a loose conversation costs, and what makes every
    existing behaviour the default. Present rows narrow, and they narrow
    by INTERSECTION with the reader's own grants -- never by union
    (section 6.1).

    The same table shape as `DocumentEntitlement`, `ToolEntitlement`,
    `AgentEntitlement`, `FlowEntitlement` and `ModelSetEntitlement`
    before it, deliberately: a sixth spelling of "this row carries
    entitlement ids" would be a sixth thing to keep in agreement.
    """

    workstream = models.ForeignKey(Workstream, on_delete=models.CASCADE,
                                   related_name="scope_entitlements")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="workstream_scopes")
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    set_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workstream", "entitlement"],
                                               name="uniq_workstream_scope")]
        indexes = [models.Index(fields=["entitlement"], name="agents_wsscope_ent")]
```

**The reverse accessor is `scope_entitlements`, NOT `entitlement_labels`.** The four existing
label tables all name theirs `entitlement_labels` precisely so `agents/visibility.py::
label_permitted_q` can be one function for two models — and a wall is not a label. A label says
"holders of this may reach this row"; a wall says "narrow this row's reach to this". Reusing the
accessor would let a future `label_permitted_q` call compile against a stream and answer the
wrong question silently. Author decision 4.

### 5.3 `agents.ConversationTaint` and `agents.WorkstreamTaint` — the tag sets

```python
class ConversationTaint(models.Model):
    """One entitlement whose labelled material retrieval has actually
    returned into this conversation (owner decision 3b).

    ADDITIVE ONLY in v1: rows are created, never deleted, except by the
    conversation's own CASCADE and by the entitlement cascade (section
    8.4). Removal -- "this conversation no longer contains E" -- is a
    deferred item with a real design question behind it (section 22).

    `first_turn` is THE CAUSING TURN, kept as a plain integer, not a
    ForeignKey: it is the evidence a person needs when they ask why a
    share went dormant, and a real FK would make deleting a turn delete
    the record of what that turn brought in.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name="taint_tags")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="conversation_taints")
    first_turn = models.BigIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "entitlement"],
                                               name="uniq_conversation_taint")]
        indexes = [models.Index(fields=["entitlement"], name="agents_convtaint_ent")]


class WorkstreamTaint(models.Model):
    """The union of its conversations' tags, MATERIALISED (owner decision 3b).

    Materialised, not derived, and that is the decision rather than an
    optimisation. The read-time share gate (section 12.2) runs on every
    non-owner view of a shared stream; deriving the union would mean a
    join across every conversation in the stream on every one of those
    reads and -- worse -- the read-time gate would be answering from a
    different query than the share-time gate did, which is how two gates
    come to disagree about one fact. ONE WRITER (section 7.3), in the
    same transaction as the conversation row it follows.
    """

    workstream = models.ForeignKey(Workstream, on_delete=models.CASCADE,
                                   related_name="taint_tags")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="workstream_taints")
    first_conversation = models.UUIDField(null=True, blank=True)
    first_turn = models.BigIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workstream", "entitlement"],
                                               name="uniq_workstream_taint")]
        indexes = [models.Index(fields=["entitlement"], name="agents_wstaint_ent")]
```

**Two tables, not one with a nullable pair of parents.** They answer two questions with
different readers: the conversation's tags are what a note document inherits (§10.4) and what
the per-conversation display shows; the stream's tags are what both share gates read. One table
with `conversation` XOR `workstream` would need the XOR check constraint, two partial uniques
and a branch at every read, to save one migration operation.

**A loose conversation is tainted too.** `ConversationTaint` hangs off `Conversation`, not off
`Workstream`, so a conversation with no stream accumulates tags exactly the same way. Nothing
reads them in v1 — a loose conversation's share is IA-2's and this phase does not change it —
and the rows are what makes conversation-level share gating a later change with no back-fill.
Author decision 9.

### 5.4 `agents.Conversation` gains three columns

```python
    # NEW. Null = loose (today's behaviour, in every respect). Set = born
    # in that stream, FOR LIFE: no route writes this column after
    # creation, and `agents/visibility.py` exposes no setter (owner
    # decision 2). PROTECT, not CASCADE -- deleting a stream that still
    # holds conversations is refused and named (section 8.4).
    workstream = models.ForeignKey("agents.Workstream", null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="conversations")
    # NEW (WS-2). The `Turn.index` this conversation was last consolidated
    # THROUGH, and when. Null = never. Two columns rather than a
    # `Consolidation` row per run: only the LATEST matters -- a
    # re-consolidation overwrites the note (owner decision 6) -- and a
    # history table with one meaningful row is a history table nobody
    # reads.
    consolidated_through_index = models.PositiveIntegerField(null=True, blank=True)
    consolidated_at = models.DateTimeField(null=True, blank=True)
```

`Meta.indexes` gains `models.Index(fields=["workstream", "-updated_at"], name="agents_conv_ws")`
— the scoped sidebar's exact ordering (§15.2), so a stream's conversation list is one index scan
rather than a filter over the owner index.

**"For life" has exactly one route that would have broken it, and it is `duplicate_conversation`.**
`agents/visibility.py:341-403`, reachable from `chat-conversation-duplicate` (class `O`), builds
the copy with `Conversation.objects.create(agent=…, title=…, **owner_fields(principal))` and
`Turn.objects.bulk_create([...])` carrying `text`, `data` and `artifacts` — the quoted document
text and the `document:<id>` references. It names no `workstream`. Unamended, one ⋯-menu click
would produce a **loose** thread holding `E`-labelled material with **zero taint tags**, outside
every gate this phase builds and shareable through `chat-conversation-share`, which §5.3 says
this phase does not change. §12's two gates would be one click from decorative, and an
administrator under `admin_sees_content` could do it to anybody's stream conversation.

**Ruling D (§23.D), and it is spelled out here because this is where duplication is defined:**

```python
        copy = Conversation.objects.create(
            agent=conversation.agent, title=title,
            workstream=conversation.workstream,      # THE COPY STAYS IN THE STREAM
            **owner_fields(principal),
        )
        ...
        # ALWAYS, not only for a stream conversation: a loose thread's tags
        # are recorded too (section 5.3), and a copy that dropped them would
        # launder a loose conversation exactly as it would a stream one.
        ConversationTaint.objects.bulk_create([
            ConversationTaint(conversation=copy, entitlement_id=t.entitlement_id,
                              first_turn=t.first_turn)
            for t in conversation.taint_tags.all()
        ])
```

The copy inherits the source's stream identity, which is consistent with immutability rather than
an exception to it: nothing *moves*, and the new row's `workstream` is stamped once at creation
like every other row's. **v1 offers no loose copy of a stream conversation** — there is no
control for it and no parameter that would produce one. Because the stream is the same, no taint
crosses a boundary; because the tags are copied, no thread ever holds labelled material with no
tags. §17.4 asserts the laundering case directly and §19.1's done-when 6 names it.

### 5.5 `tools.rag.Document` gains three columns

```python
    # NEW. CONTAINMENT (owner decision 4). Null = the universal library,
    # which is every existing row and needs no back-fill. Set = this
    # document exists ONLY in that stream's corpus: absent from
    # `readable_documents`, from the library page, from Ask and Search,
    # and from every other stream, REGARDLESS of entitlements (section
    # 8.1). A STRING reference, so `tools/rag` never imports
    # `agents.models` -- the same mechanism `DocumentEntitlement.
    # entitlement` uses for `identity.Entitlement`.
    workstream = models.ForeignKey("agents.Workstream", null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="documents")

    class Origin(models.TextChoices):
        UPLOAD = "upload", "Uploaded"
        NOTES = "notes", "Consolidated notes"

    # NEW. What PUT this row here -- distinct from `extraction` (what
    # produced its TEXT) and `media_type` (what the source file was).
    # Every existing row is an upload, which is why the default is
    # `UPLOAD` and no data migration is needed.
    origin = models.CharField(max_length=16, choices=Origin.choices,
                              default=Origin.UPLOAD)
    # NEW. The conversation a `notes` document was distilled from -- the
    # key that makes re-consolidation an OVERWRITE rather than a second
    # note (section 10.4). A UUID BY VALUE, never a FK: `tools/rag` may
    # not import `agents.models`, and this is the same by-value reference
    # `Turn.queue_job_id` already is in the other direction.
    notes_conversation_id = models.UUIDField(null=True, blank=True)
```

`Meta` gains:

```python
        constraints = [
            # ONE NOTE PER CONVERSATION (owner decision 6), enforced by the
            # database and not only by the job that writes it: a
            # re-consolidation racing itself would otherwise produce two
            # notes and the stream page would show both.
            models.UniqueConstraint(fields=["notes_conversation_id"],
                                    condition=Q(notes_conversation_id__isnull=False),
                                    name="uniq_notes_per_conversation"),
        ]
        indexes = [models.Index(fields=["workstream"], name="rag_document_ws")]
```

**`origin` is a column, not a value inside `extraction`.** `extraction` is documented as a
snapshot of *what produced this document's text*, and its `method` vocabulary is
`transcription`/`extraction`. A note's text is produced by a model too, so its `extraction`
snapshot is written the same way with `method="distillation"` — and `extraction_summary` already
degrades an unknown method to `"Processed"`, so nothing crashes. `origin` answers a different
question, *what put this row in the library*, and it is the one a page filters on. Author
decision 12.

### 5.6 `tools.rag.WorkstreamPin`

```python
class WorkstreamPin(models.Model):
    """A UNIVERSAL document associated into a stream's working set (owner
    decision 4). An association, never a copy and never a move: the
    document stays universal, stays in the library, and stays readable
    everywhere its labels already allowed.

    IN `tools/rag`, NOT IN `agents`, because the `document` half is a real
    ForeignKey with real referential integrity and the `workstream` half
    is a string reference -- and the column that owns the FK owns the
    join table. The reverse (`agents.WorkstreamPin` with a by-value
    `document_id`) would leave a dangling pin behind every document
    delete, which `delete_document` would then have to clean from a
    column it may not import.
    """

    workstream = models.ForeignKey("agents.Workstream", on_delete=models.CASCADE,
                                   related_name="pins")
    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="workstream_pins")
    pinned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    pinned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pinned_at"]
        constraints = [models.UniqueConstraint(fields=["workstream", "document"],
                                               name="uniq_workstream_pin")]
        indexes = [models.Index(fields=["document"], name="rag_wspin_document")]
```

**A contained document may never be pinned** — not into another stream, and not into its own.
It is already in its stream's corpus by containment, and putting it in a second stream is the
duplication owner decision 4 forbids. That rule is **not** a `CheckConstraint`: the condition
lives on the joined `Document` row and Postgres does not accept a check constraint that
references another table. It is enforced in exactly one place —
`tools/rag/workstreams.py::pin_document`, which refuses by name — and pinned by a test that
tries it. Recorded here rather than left as an absence, so a reader reaching for the constraint
later finds the reason instead of a failing migration. Author decision 13.

### 5.7 `agents.Share` gains a fifth target

```python
    class Target(models.TextChoices):
        CONVERSATION = "conversation", "Conversation"
        AGENT = "agent", "Agent"
        FLOW = "flow", "Flow"
        VISION_OUTPUT = "vision_output", "Generated image"
        WORKSTREAM = "workstream", "Workstream"     # NEW

TARGET_KEY_PARSERS = {
    ...
    Share.Target.WORKSTREAM: _int_or_none,          # NEW
}
```

Nothing else about `Share` changes. `target_key` is already `CharField(max_length=64)` holding a
pk as text, `Share.save()` already refuses a key its parser rejects, and `agents/shares.py::
shared_keys` already drops an unparseable row with a warning. A stream share is one more row in
a table built generic for exactly this. The migration is a `choices`-only `AlterField`, which
Django emits and Postgres executes without touching the column.

**`level` is `use`, or the share is refused.** IA-2's `view`/`use` split means "read the thread"
versus "read and post". A stream share that could not converse would be a reading list, and
owner decision 7 says a recipient reads **and** converses. `share_workstream` (§12.1) therefore
accepts only `Level.USE` and refuses `Level.VIEW` by name, rather than storing a level no reader
honours. Author decision 16.

### 5.8 What gets no column, and why

| Thing | Decision | Why |
|---|---|---|
| the tag set as a JSON list on the stream | **no** — a table (§5.3) | the entitlement cascade must remove one id when an entitlement is deleted, and a JSON list is not something a cascade can join on or a constraint can keep unique |
| `Conversation.workstream_id` denormalised onto `Turn` | **no** | a turn reaches its stream through `turn.conversation.workstream_id`, one join the runtime already makes — `plan_turn`'s `select_related("conversation__agent")` becomes `select_related("conversation__agent", "conversation__workstream")` |
| `Document.pin_count` or any counter | **no** | `WorkstreamPin` is the fact; a counter is a second answer that goes stale |
| `Workstream.owner` as a real `User` FK | **no** — owner columns | the open posture's principal is not a `User` row, and `owned_rows_q` is the one predicate that reads owner columns in every posture |
| `Share.is_dormant` | **no** | dormancy is **computed at read time** against the reader's current grants (§12.2). A stored flag is stale the moment a grant moves, and grants moving is the entire reason the read-time gate exists |
| a `Workstream.category` link | **no** | a category is library-wide taxonomy (ROADMAP, Tenancy: "categories stay taxonomy") and a stream is not a taxonomy node |

---

## 6. The wall, at three seams

### 6.1 The composition law, with its parentheses

The corpus of a turn in stream **W**, for a principal **P**, is:

```
    (  universal_documents
       ∩ wall(W)                     ← only when wall(W) is non-empty
     ∪ pinned(W)
     ∪ contained(W)  )
  ∩ readable_by(P)
```

**The reader's own grants are the outermost intersection and nothing escapes them.** A pin does
not grant, containment does not grant, and a stream never grants. If P cannot read a document
under IA-2's rules, no arrangement of streams makes P able to read it. The wall sits *inside*
the parentheses because it narrows what the universal library contributes; the pin and the
contained set sit beside it because they are deliberate acts that admit specific documents into
the working set, and a wall — which is a convenience the owner set for themselves — does not
un-admit what somebody deliberately put in.

The prose form owner decision 4 gives is `(universal ∩ grants ∩ wall) ∪ pinned ∪ contained`,
which read literally would put `pinned` and `contained` outside the grant intersection. Written
that way, pinning a document into a stream would make it readable by a share recipient who holds
none of its labels, which is exactly the leak §12's two gates exist to prevent — and the same
sentence's own "docs **the user can already read**" says it was not meant. The parenthesisation
above is the reading this spec implements, and it is recorded as **author decision 2** rather
than applied silently.

A loose turn's corpus is unchanged from today: `universal_documents ∩ readable_by(P)`. The one
thing that is new for a loose turn is that `universal_documents` now excludes contained rows
(§8.1) — which is the containment guarantee, and which is why the clause is added outside the
entitlement branch rather than inside it.

**A non-empty wall excludes UNLABELLED universal documents.** A wall says "this stream is about
material under these entitlements"; a document under no entitlement is under none of them. This
is the one place where a wall visibly removes something a person could otherwise see, and it is
the behaviour that makes a wall worth setting. The stream page says so in one line beside the
wall editor. Author decision 5.

**A wall may only name entitlements the setter holds.** `set_workstream_scope` refuses an id
outside `identity.access.held_entitlement_ids(principal)` — not because holding it would grant
anything (it would not; the outer intersection sees to that) but because a wall naming an
entitlement its owner does not hold is a wall that narrows to the empty set and reads as a bug.

**The editor therefore renders `entitlement_names(held_entitlement_ids(principal))`, NOT
`labelling_entitlements(principal)`** — the render-vs-gate pair, with the two halves actually
matching. `labelling_entitlements` answers **owned**, not held (`identity/access.py:328-357`:
`rows.filter(pk__in=owned_entitlement_ids(...))` for a non-admin, and *everything* for an
administrator), and the two diverge in both directions: an entitlement you own but do not hold
would render and then be refused, and one you hold but do not own would never render, making a
wall this section says you may set unsettable. For an administrator it would offer every
entitlement on the box while the gate refused all of them, which is the common shape — an
administrator reads content through `admin_sees_content`, not through grants. §12.1 is already
adding `entitlement_names(ids)` to `identity/access.py`; this is its second caller. §17.3 asserts
that the rendered set and the accepted set are equal for a non-admin who holds one entitlement and
owns a different one.

### 6.2 Seam 1 — retrieval, before scoring

`tools/rag/access.py::DocumentVisibility` gains one field and `_visibility_filters` gains one
clause. Nothing else in the retrieval path changes, and there is still exactly one filter point.

```python
@dataclass(frozen=True)
class DocumentVisibility:
    unrestricted: bool
    entitlement_ids: frozenset[int]
    unlabelled_allowed: bool
    # NEW. None = a LOOSE turn or a non-stream surface: contained
    # documents are excluded and nothing else changes. A `WorkstreamScope`
    # = a stream turn: its wall narrows the universal leg and its
    # contained/pinned legs are OR-ed in (section 6.1).
    stream: WorkstreamScope | None = None
```

**Defaulting to `None` is what keeps every existing call site correct**, exactly as
`ToolAccess`'s `UNRESTRICTED_TOOL_ACCESS` default did in IA-2: `SearchView`, `AskView`, the two
runners and every test that builds a visibility by hand keep working and keep meaning "the
universal library", and the compiler never tells you about the ones you forgot — but the
behaviour they get is the safe one, because `None` **excludes** contained documents rather than
including them.

The clause `_visibility_filters` adds, in full, and **outside the `if not visibility.
unrestricted` branch** — containment is a corpus rule, not an entitlement rule, so it applies in
the open posture and to an administrator with `admin_sees_content` on, exactly as it applies to
everybody else:

```python
    # `workstream` is stamped on a contained document's chunks by the one
    # cache writer (section 8.3). ABSENT on every chunk in every existing
    # store, which is what `IS_EMPTY` matches -- so no back-fill.
    if visibility.stream is None:
        clauses.append(MetadataFilter(key="workstream", value="",
                                      operator=FilterOperator.IS_EMPTY))
    else:
        legs = [
            # contained: this stream's own documents
            MetadataFilter(key="workstream", value=str(visibility.stream.workstream_id),
                           operator=FilterOperator.EQ),
        ]
        if visibility.stream.pinned_file_ids:
            legs.append(MetadataFilter(
                key="file_id",
                value=sorted(str(i) for i in visibility.stream.pinned_file_ids),
                operator=FilterOperator.ANY))
        universal = [MetadataFilter(key="workstream", value="",
                                    operator=FilterOperator.IS_EMPTY)]
        if visibility.stream.wall:
            universal.append(MetadataFilter(
                key="entitlements",
                value=sorted(str(i) for i in visibility.stream.wall),
                operator=FilterOperator.ANY))
        legs.append(MetadataFilters(filters=universal, condition=FilterCondition.AND))
        clauses.append(MetadataFilters(filters=legs, condition=FilterCondition.OR))
```

**`pinned_file_ids` is the one field of `WorkstreamScope` (§4.2) that `agents/` does not fill.**
The value handed across the seam from `agents/workstreams.py` carries the *stream* half — id,
wall, upload default, may-upload — with `pinned_file_ids=frozenset()`;
`tools/rag/workstreams.py::scope_with_pins(scope)` returns the same frozen value with the pins
filled in, and it is the only function that fills them, because the pin table lives in
`tools/rag`. Author decision 6.

**The containment value is the stream pk as a DECIMAL STRING**, matching the `entitlements`
convention `retrieval.py:461-464` documents for the same reason: `ANY` and `EQ` render as JSON
*string* comparisons over `metadata_`, so an integer stamped into the metadata would never match
`str(workstream_id)` here. §8.3's writer stamps the same spelling.

**This clause makes `_visibility_filters`' `None` return unreachable, and that is a documented
path changing.** `retrieval.py:473-475` returns `None` when no clause was built — the "no
category, unrestricted principal" fast path — and a `workstream` clause is appended in **every**
branch, so `clauses` is never empty and every retrieval now carries a filter. Harmless at the
store (an AND of one group is the group), but it retires a branch and it breaks the existing
`test_visibility_filters` assertions that expect `None`. §17.2 names them.

**The nesting is supported, not assumed.** `_visibility_filters`' own docstring already records
that the installed Postgres store recurses through nested `MetadataFilters`
(`_recursively_apply_filters`) and that **both halves of a hybrid query apply the same filters**.
The clause above adds one more level of the same nesting and inherits both properties. The
existing `test_visibility_filters` suite — which asserts the filter object directly rather than
through a live store, for the reason that docstring gives — gains the stream cases.

**`sees_nothing` is unchanged and still returns early.** A principal who may see nothing sees
nothing in a stream too; a stream is not a way around an empty entitlement set. The early return
at `retrieval.py:540` is untouched.

**The pinned leg is capped.** A stream with thousands of pins would render an `ANY` array of
thousands of ids into every query. `pin_document` refuses past `MAX_PINS_PER_STREAM = 200` with
an honest message naming the cap — a number chosen the way `SIDEBAR_LIMIT = 30` was, as an
honest bound on a single-operator box rather than a paginator. Author decision 7.

### 6.3 Seam 2 — the turn planner's entitlement intersection

`agents/entitlements.py::tool_access_for` builds one `ToolAccess` per turn from
`held_entitlement_ids(principal)`. A stream turn narrows the `held` half by the wall, and
nothing else about the function changes:

```python
def tool_access_for(principal, *, settings_row=None, wall: frozenset[int] = frozenset()):
    row = settings_row if settings_row is not None else _identity_settings_row()
    if sees_all_content(principal, settings_row=row) and not wall:
        return UNRESTRICTED_TOOL_ACCESS
    held = held_entitlement_ids(principal, settings_row=row)
    return ToolAccess(required=tool_entitlement_ids(),
                      held=(held & wall) if wall else held,
                      unrestricted=False)
```

**`and not wall` is the whole of the change to the unrestricted branch**, and it is the one
subtle line in this section. Without it, an administrator with `admin_sees_content` on would get
`UNRESTRICTED_TOOL_ACCESS` and the wall would silently do nothing for tools while doing something
for documents. A wall is not a permission check; it is a **scope the owner chose**, and choosing
it means choosing it for yourself as well. Author decision 8.

**`wall` is EMPTY on an open box, and the caller is what makes it so** — orchestrator ruling A
(§23.A). `_wall_for(conversation)` returns `frozenset()` without reading
`WorkstreamScopeEntitlement` at all when `identity.access.accounts_on()` is False, so an open box
takes the `sees_all_content and not wall` branch, gets `UNRESTRICTED_TOOL_ACCESS`, and runs zero
permission queries. An earlier draft of this section asserted the opposite premise — "on an open
box nothing is labelled" — which is **false**: `ToolEntitlement`, `DocumentEntitlement` and the
model-set rows all survive a posture switch to `open`, and `agents/labels.py:20::
tool_entitlement_ids` reads them with no posture branch. Without ruling A, a wall set under
`enterprise` and then switched to `open` would leave `held=frozenset()` against a non-empty
`required`, drop every labelled tool, refuse every labelled model set at preflight, and — because
§15.3 hides the Scope editor on a box whose principal holds no entitlements — leave **no page that
could clear it**. The stream would be bricked on a box with no accounts. Ruling A removes the
possibility rather than adding an escape hatch: the wall rows survive, dormant, and start biting
again the moment the posture returns.

**Four call sites, not two**, and the fourth is the one §17.3 asserts against:

| Function | Where | Narrowed with the wall |
|---|---|---|
| `agents.entitlements.tool_access_for` | `agents/runtime/jobs.py:103` (`plan_turn`) | yes — `wall = _wall_for(turn.conversation)`, one read off the already-`select_related` conversation |
| | `agents/runtime/loop.py:221` (`_run_turn`) | yes — the run-time re-check, same value, same read. The planner decides what to *admit* and the loop decides what to *offer*, and IA-2 already pins that those two agree |
| | **`agents/runtime/preflight.py:246`** (`preflight_turn`) | **yes** — without it §17.3's *"refused at `preflight_turn`, before a turn row is written"* is impossible: the turn row is written and the refusal lands mid-run, which is exactly what `agents/runtime/jobs.py:85-88` records as the wrong order to fail in |
| `models.registry.access.model_access_for` | `agents/runtime/jobs.py:93`, `agents/runtime/loop.py:232`, **`agents/runtime/preflight.py:196`** | yes — the model half narrows identically, for the identical reason |
| | **`agents/chat/pickers.py:42`** (`chat_picker_options`) | **yes** — the render half of the model picker on a stream conversation's page. Its docstring says a connection the principal may not use "is never in the offered options"; unnarrowed it would offer models the walled turn then refuses, breaking the render-vs-gate pair on the surface the wall is most visible on |

**`model_access_for` lives in `models/registry/access.py:92`, so this phase touches a fourth
column.** Narrowing it is one keyword-only `wall: frozenset[int] = frozenset()` parameter — a
plain frozenset, lawful under every existing import sweep, with no new import in either
direction. §19.1 lists the file and §20 lists its README.

The refusal copy is **unchanged**. A tool walled off from a stream is dropped exactly as a tool
the principal lacks the entitlement for is dropped — silently, from `granted_tools`, logged at
`DEBUG` — and a walled-off model is refused with the existing sentence,
*"That model needs an entitlement this account does not hold — pick another."* Owner decision 3a
says the honest copy is the existing copy; §24 records the one place where that sentence is now
slightly less than true and why this spec does not change it.

### 6.4 Seam 3 — visibility and the sidebar

`agents/visibility.py` gains `visible_workstreams(principal)` and one narrowing:

```python
def visible_workstreams(principal):
    """Every workstream this principal may read.

    THE OPEN BRANCH IS FIRST, as in every function in this module.
    `owned_rows_q` is own-rows-plus-service-rows-for-an-admin; a `Share`
    extends it to somebody the owner named. The DORMANCY check is NOT
    here -- a dormant share still LISTS (section 12.2), and hiding it
    would leave a recipient with a stream that vanished and no sentence
    explaining why.
    """
    qs = Workstream.objects.all()
    if sees_all_content(principal):
        return qs
    return qs.filter(
        owned_rows_q(principal)
        | Q(pk__in=shared_keys(Share.Target.WORKSTREAM, principal))
    ).distinct()
```

`visible_conversations` is **unchanged in WS-1**, and §12.4 adds **two** clauses to it in WS-2,
with the share. In WS-1 there are no stream shares, so every conversation in a stream is reached
by its own ownership exactly as before, and `sidebar_context` and the stream page simply filter
`visible_conversations(principal).filter(workstream=...)`. In WS-2 a stream share extends to the
stream's conversations and a stream's **owner** reads every conversation in it (§12.4, ruling C) —
"a share on the container implies a share on the contents", and "an owner is not opaque to their
own space", are two rules that must be written once and read from one place, and §12.4 is that
place.

The **scoped sidebar** (§15.2) is `visible_workstreams` for the section header list, and
`visible_conversations(principal).filter(workstream_id=...)` for the scoped body — one extra
`.filter()` on the queryset that already exists, over the `agents_conv_ws` index.

**`sidebar_context`'s queryset gains `select_related("workstream")`, and this is not an
optimisation — it is the condition of ruling C being affordable.** `may_manage_conversation`
gains a stream-owner branch (§12.4), and `agents/chat/sidebar.py` calls that predicate **once per
listed row** — the caller its own docstring singles out, recording a *measured* regression the
last time a per-row read was added there: *"+2 queries per conversation on `/chat/` and on every
thread page (47 → 95 at 25 rows)"*, fixed by threading `settings_row`. The new branch reads
`conversation.workstream.owner_kind`/`owner_key`, which is a second per-row join unless the
queryset fetches it — so it does, on both sidebar lists (scoped and unscoped) and on the stream
page's own list. §5.8 already adds `select_related("conversation__workstream")` to `plan_turn`
for the runtime's half of the same fact; this is the page's half, and §17.3 pins it with a query
count rather than trusting it.

### 6.5 The wall is never in the prompt

Owner decision 3a is explicit and this section states the consequence so nobody softens it
later: **no sentence about the wall, the stream's scope, or what the model may not see is ever
added to the system prompt.** `build_messages` gains one block (§11) and it carries the stream's
*instructions*, which are the owner's words for the model. The wall is enforced by the three
seams above — a filter clause, an entitlement intersection and a queryset — every one of which
runs in the web or worker process against the database, and none of which the model can talk its
way past. `agents/runtime/loop.py`'s own "NO PROMPT-HACKING, EVER" docstring is the standing
version of this rule; this is that rule applied to a new mechanism.

---

## 7. Taint: what the work has touched

### 7.1 What a turn actually returned, in the real tree

The brief for this design says "citation records = source of truth". There is **no citation
table**. The tree records what a turn retrieved in two places, both on `agents.Turn`:

| Record | Shape | Written where |
|---|---|---|
| `Turn.data["citations"]` / `["results"]` | the full per-citation dicts, `{"document_id", "title", "score", "locator", …}` | on each TOOL turn, `agents/runtime/loop.py:412`, from `ToolResult.data` |
| `Turn.artifacts` | `("document:<id>", …)`, deduped on the bare `document:<id>` | on each TOOL turn, `agents/runtime/loop.py:407-419`, and accumulated onto the assistant turn at `:421` → `:438` |

**`Turn.artifacts` ON THE TOOL TURN is the source of truth for taint**, and the reconciliation is
author decision 3. Three reasons, in order of weight:

1. It is **already guarded**. `_document_artifacts` drops any entry whose `document_id` is not
   decimal, precisely so a stray metadata value cannot mint a reference nothing resolves; the raw
   citation dicts carry no such guard and taint would be reading them unfiltered.
2. It is **already deduped**, on the bare id, keeping one entry per document.
3. It is **a column the tool turn's own row creation already writes** (`loop.py:407-419`), so the
   stamp rides a write that already happens rather than needing a second read of rows the runtime
   just wrote.

**The tool turn, not the assistant turn** — the correction §7.2 explains. An earlier draft stamped
the accumulated `artifacts` on the assistant turn in `_finish`, on the argument that the
accumulation was already the union. It is; but `_finish` is reached only on the **success** path,
and the tool turns commit as they run.

`agents.contracts.artifacts.parse_artifact` is the one parser and taint uses it, so the taint
stamp cannot come to disagree with the chat page's own rendering about what a reference means.
Non-`document` kinds (`output`, `input`) are skipped — §7.4.

**What this misses, named rather than glossed:** a document returned by a *delegate* contributes
only if the delegate's `ToolResult.artifacts` propagate to the parent's tool turn. They do
today (`agents/runtime/delegate.py:196` writes artifacts onto the delegate's own turn and the
delegate's `ToolResult` carries them back), and §17 pins it with an explicit test, because it is
the one path where "the union" could quietly stop being the union.

### 7.2 The stamp, and where it goes

One new function, in `agents/runtime/taint.py`:

```python
def stamp_turn_taint(turn, conversation, artifacts) -> frozenset[int]:
    """Record the entitlement labels of the documents `turn` returned,
    on the conversation and on its stream. Returns what was ADDED.

    Returns the empty set without touching the database when `artifacts`
    holds no `document:` reference -- which is the overwhelmingly common
    case (a turn that called no retrieval tool) and must cost nothing.
    """
```

**It is called on the TOOL TURN, in the same `transaction.atomic()` as the tool turn's own
`Turn.objects.create(...)` at `agents/runtime/loop.py:407-419`** — not on the assistant turn in
`_finish`:

```python
    with transaction.atomic():
        tool_turn = Turn.objects.create(
            conversation=conversation, index=..., role=Turn.Role.TOOL,
            artifacts=list(outcome.result.artifacts) if outcome.result is not None else [],
            ...
        )
        stamp_turn_taint(tool_turn, conversation, tool_turn.artifacts)
```

**Because retrieval and completion are not the same event.** `run_loop`'s caller wraps `_run_turn`
in a `try/except` (`loop.py:127-139`) that, on any exception, flips the assistant turn to `FAILED`
with one `.update()` — and `_finish` is reached only on the **success** path (`loop.py:214`,
`:241`, `:264`). The tool turns, meanwhile, are created and **committed as they run**, each
carrying the retrieved text in `text`/`data` and its `document:` references in `artifacts`. So
"retrieve a document labelled `E`, then the assistant call raises — an engine error, a context
overflow, an orphaned worker" would leave the `E` material committed and rendered in the thread
with **no taint row for it**, and both share gates would then pass a stream that holds `E`
material. A cancellation between tool turns is the same shape. That is the opposite of the
conservative direction §7.5 claims for this mechanism: it is "material is in there and no gate
knows", not "a share you expected does not work".

Stamping on the tool turn is strictly safer, costs the same one query per turn that returned a
`document:` artifact, makes §17.4's "union across two retrievals in one turn" fall out of the loop
rather than depending on the accumulator, and — the thing an earlier draft of this section got
wrong — means **`_finish` grows no transaction at all**. It stays the single `save()` it is
today, and §23's smaller-calls list no longer claims otherwise.

**Where the ids come from.** `agents/` may not import `tools/`, so the runtime cannot ask
`DocumentEntitlement` anything. The lookup goes through the same registry-of-dotted-paths shape
as everything else that crosses this way:

```python
# agents/contracts/artifacts.py -- beside the vocabulary it keys on
@dataclass(frozen=True)
class ArtifactLabels:
    kind: str      # an entry of ARTIFACT_KINDS
    resolver: str  # "package.module.function", signature
                   # (pks: frozenset[int]) -> frozenset[int], returning
                   # the union of the entitlement ids labelling those rows

def register_artifact_labels(spec: ArtifactLabels) -> None
def labels_resolver_for(kind: str) -> str | None
```

`tools/rag/apps.py` registers `ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for")`
— a new one-query function beside `document_label_ids`, which it generalises from one document
to a set. `agents/runtime/taint.py` resolves the dotted path with `import_string` at stamp time.
A kind with no registered resolver contributes nothing, silently: that is what makes owner
decision 11's "generalises mechanically" true, and it is why §7.4 is a scope statement rather
than a code branch.

### 7.3 Union upward, and the audit

Inside the same transaction, in this order:

1. `ConversationTaint` rows for `new_ids - already_on_conversation`, each carrying
   `first_turn=turn.pk`.
2. `WorkstreamTaint` rows for `new_ids - already_on_stream`, each carrying
   `first_conversation=conversation.pk` and `first_turn=turn.pk` — **only when the conversation
   is in a stream**. A loose conversation stops at step 1.
3. One `identity.audit.record` per **newly added** id, at each level.

```python
audit.record(actor, actions.WORKSTREAM_TAINTED,
             target_type="workstream", target_key=workstream.pk,
             target_label=workstream.name, source=SOURCE_WEB,
             entitlement=entitlement_id, entitlement_label=name,
             conversation=str(conversation.pk), turn=turn.pk)
```

**Per newly added id, not per turn**, because the question an operator asks of this trail is
"when did E get into this stream, and what brought it in" — one row per (stream, entitlement)
first-sighting answers it, and a row per turn would bury it under every subsequent turn that
returned the same document. A re-sighting adds no audit row and no table row; `bulk_create(...,
ignore_conflicts=True)` against the unique constraints is what makes the second turn free.

**`source`** is `SOURCE_WEB` for a turn started from `/chat/` and `SOURCE_CLI` for one started by
`manage.py agent_turn`; the payload already carries enough to tell them apart, and the audit
writer already takes `source` as a keyword.

**The actor is the acting principal, read back from the job payload** with
`principal_from_payload` — the acting rule, unchanged. The taint was caused by a person's turn,
not by the box, **and that person is not always the stream's owner**: a share recipient's turn
taints the owner's stream, which is ruling B (§23.B) and is the point of the mechanism rather
than a flaw in it. One audit row per newly added tag, carrying its acting principal, is therefore
the one place "who brought `E` into this stream" is answerable — and it is answerable to whoever
may read the audit trail.

### 7.4 What taints, in v1

**Only `document:` artifacts, because only documents carry entitlement labels.** `output:` and
`input:` are vision's, and `tools/vision` has no label table; a tool's own output text is not a
labelled row at all. Registering a resolver for a kind that has no labels would be a query that
always returns the empty set.

The generalisation owner decision 11 asks for is the `ArtifactLabels` registry itself: when a
generated image becomes a labelled thing, `tools/vision/apps.py` registers
`ArtifactLabels("output", "tools.vision.labels.entitlement_ids_for")` and every turn that
returned one starts tainting, with **no change to `agents/runtime/taint.py`, no change to
`_finish`, and no migration**. That is the whole test of whether the seam was drawn in the right
place, and §17 asserts it with a fake resolver registered in a test.

### 7.5 Additive only, and what that costs

A tag, once on, stays on. Owner decision 3b, and §22 defers removal. Two consequences worth
stating rather than discovering:

**The tag set is not guaranteed to be a subset of the wall, and there are TWO ways they
diverge.** First, a wall can be widened, narrowed or removed after turns have run, and the tags
recorded under the old wall stay. Second — the consequential one — **a share recipient's turn
retrieves under the recipient's own grants**, so a stream can acquire a tag for an entitlement its
owner neither holds nor owns and its wall never named (ruling B, §23.B; §24 concern 6). Both are
correct: the material really was in there. Both mean no code may derive one set from the other or
assert containment between them, and the stream page shows the wall and the tags as two separate
lists under two different headings for exactly this reason.

**A single accidental retrieval is permanent in v1.** Retrieving one document under a restricted
entitlement into a stream taints that stream forever, and every share to somebody without that
entitlement is refused or dormant from then on. This is the conservative direction — the failure
mode is "a share you expected does not work", not "material leaks" — and it is stated in §24 as
an author concern with the deferred remedy (an audited untaint, owner-only, naming what it
forgets) named beside it.

---

## 8. Documents: containment, pinning, and the chunk cache

### 8.1 Containment, at every reader

`Document.workstream` being set must remove the row from **every** surface that means "the
library". There are exactly four such readers and they are all in `tools/rag/access.py` or
downstream of it:

**The rule, once: a contained document's content is reachable only from inside its stream.** Not
"unreachable" — an earlier draft filtered it out of `readable_documents` unconditionally, which
would have made every contained document, notes included, 404 at `rag-document-file` and
`rag-document-transcript` for its own stream's members, because those two routes are gated on
`readable_documents` (`tools/rag/views.py:1104`, `:1195`, each carrying the comment "CLASS L:
content, not a row. 404 outside `readable_documents`"). §10.4 promises a note is openable and
§15.3 renders a **Contained** group of links; both would have been dead links. Containment is
**stream-aware**, not absolute:

| Reader | Change |
|---|---|
| `readable_documents(principal, *, workstream_id=None)` | gains the keyword-only parameter and filters `Q(workstream__isnull=True) | Q(workstream_id=workstream_id)`. `None` — every existing caller, unedited — means the universal library exactly as today, which is the safe default: a caller that forgets the parameter sees no contained document rather than all of them |
| `rag-document-file`, `rag-document-transcript` | resolve a contained document by first calling `agents.workstreams.workstream_scope(principal, document.workstream_id)` and answering **404** when it is `None`, then `readable_documents(principal, workstream_id=…)`. Two resolutions, both 404-shaped, the same pair `rag-workstream-pin` uses (§14) |
| `DocumentVisibility.permits` | gains the **same** containment leg and the same parameter. This is not optional: `permits` is the in-memory mirror of `readable_documents`' filter and its docstring says its whole purpose is that `DocumentsListView` must not "show a link `readable_documents`' own route would 404 on" (`tools/rag/access.py:71-88`). Changing one and not the other reintroduces precisely the failure it was written to prevent — and §15.4's new **Workstream** column makes those links visible to an administrator |
| `listable_documents(principal)` | gains **nothing** in the `is_admin` branch and inherits `readable_documents`' default (`workstream_id=None`) in the other. An administrator lists every document ROW including contained ones, marked with their stream — pruning and re-ingesting the library is administration, and a document an administrator cannot see the existence of is a document nobody can clean up (identity spec §8.2's own reasoning). Listing a row is not opening it: the two content routes above still resolve through the stream |
| `document_visibility(...)` → `_visibility_filters` | the `workstream` clause of §6.2 |
| the stream's own panel | a **new** `stream_documents(principal, scope)` in `tools/rag/workstreams.py`, the ORM mirror of §6.1's formula, used by the panel and by the pin picker |

Two expressions of the rule must exist because the ORM path and the chunk-metadata path answer the
same question for different consumers — pages versus retrieval — and IA-2 already has that shape
(`readable_documents` beside `DocumentVisibility.permits`). §17.2 pins that they agree on a
generated matrix of streams, walls, labels and containment, which is the only way two expressions
of one rule stay in step.

**A contained document is not hidden from its stream's members by containment.** It is hidden
from *everywhere else*. Inside its stream it is subject to the ordinary label rules and to
nothing extra: a contained document labelled `E` is invisible to a stream member who does not
hold `E`, exactly as a universal one would be.

**And containment does not bind `sees_all_content` at the content routes** — ruling G (§23.G).
An administrator with `admin_sees_content` on opens a contained document exactly as they open any
other, because `visible_workstreams`' first branch admits them to every stream and
`workstream_scope` therefore returns a scope rather than `None`. That is the mechanism this
section specifies, and it is the right answer: the paragraph three rows above — *"a document an
administrator cannot see the existence of is a document nobody can clean up"* — argues for the
row, and IA-2 already settled that `sees_all_content` reads the bytes. **The containment fence is
the CORPUS, not the bytes**: §17.2 cell 4 still pins that the same administrator does not
*retrieve* another stream's contained document into this stream's turn, which is a different
question and the one containment exists to answer.

**Deleting a stream.** `Document.workstream` and `Conversation.workstream` are both `PROTECT`.
`delete_workstream` therefore counts first and refuses by name — *"This workstream holds 3
conversations and 7 documents. Delete them first."* — which is the same count-then-name shape
`delete_entitlement` already uses for its cascade. Deleting the contents is a separate,
deliberate act; a stream delete that silently took seven documents with it would be the one
destructive gesture on this surface, hidden behind the least alarming button. The pins go with
the stream (`CASCADE`) because a pin is pure association and its loss destroys nothing.

**"Delete them first", not "delete or re-home them first"**, because re-homing is not an action
this product has: owner decision 2 forbids moving a conversation, §21.2 forbids a document in two
streams, and §14 offers no re-home route. A refusal that names an action the person cannot take
is worse than one that names a chore. Re-homing arrives with move-to-stream (§22).

**And every row the refusal counts is a row the person reading it can act on** — ruling C
(§23.C). A stream's owner reads and manages every conversation in their stream, including ones a
share recipient started there (§12.4), so the count is actionable rather than a wall of rows the
owner can neither open nor delete.

### 8.2 Pinning

`tools/rag/workstreams.py` owns the pin, and it is the only module that writes the table:

```python
def pin_document(principal, scope: WorkstreamScope, document) -> str | None:
    """Pin `document` into the stream. Returns None on success, or a
    sentence naming the refusal.

    THREE REFUSALS, all named rather than 404-shaped, because this is a
    button on a page listing rows the caller can already see:
      - the document is CONTAINED (owner decision 4: one home, never two)
      - the cap (section 6.2) is reached
      - the caller may not read the document (`readable_documents`)
    The third is a 404 at the ROUTE (`rag-workstream-pin` resolves the
    document through `readable_documents`) and is repeated here because
    this function is also the CLI-facing one and a predicate stated in
    the view alone is a predicate the second caller does not get.
    """
```

Unpinning is the same route with an `action` field, keyed on the pin id, exactly as
`chat-conversation-share` revokes: one URL, two actions, so the two predicates cannot drift.

**A pin does not follow a document's labels.** Pinning is recorded once; if the document is later
labelled with an entitlement the pinner does not hold, the pin row survives and the document
simply stops being readable by them — the outer intersection of §6.1 does that, with no pin
bookkeeping at all. A pin is never a stored permission and therefore never goes stale in a way
that matters.

### 8.3 The chunk-metadata cache gains one key

`tools/rag/labels.py::restamp_document_chunks` is the **one writer** of chunk metadata and it
stays one. It gains the `workstream` key alongside `entitlements` — and it also gains a signature:

```python
def restamp_document_chunks(doc_id, *, raising: bool = False) -> None:
```

**takes an id, not a row**, so it has nowhere to read `Document.workstream_id` from. It reads it,
in the one query it already runs for the labels:
`Document.objects.filter(pk=doc_id).values_list("workstream_id", flat=True).first()`. A
`workstream_id=` parameter was the alternative and is worse: three callers would each have to
supply it correctly, and the one that got it wrong would stamp a containment the table disagrees
with. The **table is the fact and the metadata is a cache**, and a cache writer that reads the
fact itself cannot cache the wrong thing.

**Two optional keys need four branches, not two.** Each key is present-or-absent independently —
an unlabelled universal document (both absent), a labelled universal one, an unlabelled contained
one, a labelled contained one — and `IS_EMPTY` matches only an **absent** key, because
`metadata_->>'k'` yields SQL `NULL` for a missing key and `''` for an empty string. Writing `""`
for "no stream" would therefore make every re-stamped universal document invisible to every loose
turn, to Ask and to Search: silently, totally, and only on rows that had been re-stamped. So the
writer **sets a key or removes it**, never writes an empty value:

```python
    sets, removes = [], []
    if ids:      sets.append(("entitlements", json.dumps(ids)))
    else:        removes.append("entitlements")
    if ws_id:    sets.append(("workstream", json.dumps(str(ws_id))))
    else:        removes.append("workstream")
```

composed into ONE statement — nested `jsonb_set(..., create_missing=true)` for each key in
`sets`, chained `- 'key'` for each in `removes`, the existing `::json` cast branch on a
non-`jsonb` store — guarded by the **disjunction** of the no-op guard the existing code carries:

```sql
 WHERE metadata_->>'file_id' = %s
   AND (  -- at least one key would actually change
          metadata_->>'entitlements' IS NOT NULL
       OR metadata_->>'workstream'   IS NOT NULL
       OR %s  -- true when `sets` is non-empty
       )
```

The guard is not decoration. `tools/rag/labels.py:85-107` records why it exists ("review
finding 3"): without it, every ingest of every never-labelled document rewrites every chunk row —
a dead tuple and a WAL record per chunk, on the box's most common case. A single-key guard carried
forward unchanged would silently reintroduce that regression the moment a second optional key
appeared, which is exactly what the disjunction prevents.

**The containment value is the stream pk as a decimal string** (`json.dumps(str(ws_id))`),
matching the `entitlements` convention and §6.2's `EQ` filter, for the reason
`retrieval.py:461-464` documents: these operators render as JSON *string* comparisons.

**No back-fill.** A universal document's chunks carry no `workstream` key at all, which is what
`IS_EMPTY` matches and what every chunk in every existing store already looks like — the identity
spec's §22.20 argument, applied to a second key for the same reason.

**The function is not renamed**, despite gaining a second key: its two existing callers
(`_ingest_prose` at ingest time with `raising=False`, `set_document_labels` on a label change with
`raising=True`) are joined by a third, `set_document_workstream`, with `raising=True` — a
containment change that appears saved and is not enforced is worse than one that refuses to save,
which is the rule the `raising` flag already encodes.

**One writer, three callers, one statement.** Two writers would produce two shapes of one fact;
that sentence is copied from the decision it is repeating.

### 8.4 The entitlement cascade grows one registration

Deleting an entitlement must remove its wall rows and its taint rows, and the mechanism exists:

```python
# agents/apps.py::AgentsConfig.ready()
register_entitlement_cascade(EntitlementCascade(
    key="agents.workstream_entitlements",
    label="Workstream scopes and taint tags",
    handler="agents.workstreams.workstream_entitlement_cascade",
))
```

One registration, one handler, `(entitlement_id: int, *, commit: bool) -> int`, counting in
`commit=False` mode so the delete confirmation names the number first. It removes
`WorkstreamScopeEntitlement`, `ConversationTaint` and `WorkstreamTaint` rows for that
entitlement and returns their total.

**Deleting an entitlement therefore un-taints, and it says so in the trail.** That is the one
path by which a tag is removed in v1, and it is not an exception to "additive only" so much as
the meaning of deleting the entitlement: the label no longer exists, so no document carries it,
so no share can be refused for lacking it. The handler writes one `WORKSTREAM_UNTAINTED` /
`CONVERSATION_UNTAINTED` row per removed tag (§16.1) — without them the `WORKSTREAM_TAINTED` rows
would survive their own tags and `for_target("workstream", pk)` would show what came in and not
that it left, which is a trail that lies by omission about the only removal the product performs. `tools/rag/labels.py::unlabel_all_for_entitlement` already makes the documents
unlabelled in the same transaction, and the two cascades run under one `transaction.atomic()` in
`identity/services.py::delete_entitlement`, so a stream cannot end up tagged with an entitlement
its documents have lost. Author decision 14.

**No cascade for `WorkstreamPin`.** A pin dies with its stream or its document, both by
`CASCADE`, and an entitlement delete does not remove either.

---

## 9. Upload placement is a conscious choice

### 9.1 The three states of the upload form

`tools/rag/views.py::document_upload` is a plain `@require_POST` function view reading raw
`request.POST`/`request.FILES` — there is no `Form` class in `tools/rag` and this spec adds none,
because adding one for this field alone would leave the other four fields un-validated by it and
make the view read from two places. The **form** — the markup in `rag/documents.html` and the
stream page's own copy of it — grows one field, `placement`, whose rendering has three states:

| State | Condition | What the form shows | What the POST carries |
|---|---|---|---|
| **Outside a stream** | no `workstream` in the form's context | nothing new; today's form exactly | no `placement` field |
| **In a stream, no default** | `scope.default_upload_placement == ""` | two radio buttons, **neither preselected**, "The universal library" / "This workstream only"; below them an unchecked box, "Use this choice for all future uploads to this workstream"; and one line of hint text saying a default can be set here | `placement` (required), `remember_placement` (optional) |
| **In a stream, default active** | `scope.default_upload_placement` set | no radios. One line: *"Placed in the universal library per this workstream's default."* / *"…in this workstream only…"* — with **change**, a link to the stream page's upload-default control | `placement` absent; the view reads the default |

**Neither radio is preselected, and the view enforces it.** An absent or unrecognised `placement`
on an in-stream upload is a `400` naming the field, not a fallback to either value. HTML's
`required` on a radio group is the render half; the view is the gate half, and this is the one
field where a fallback would silently undo the whole decision. Author decision 17.

**`remember_placement` sets the default and is audited.** It writes
`Workstream.default_upload_placement` through `agents/workstreams.py::
set_upload_placement_default(principal, workstream_id, placement)` — a seam call, because the
column is on an `agents` row and this is `tools/rag` code — and writes one
`identity.audit.record(actor, actions.WORKSTREAM_UPLOAD_DEFAULT_SET, …)`. Clearing the default
back to "ask every time" is the same call with `""`, from the stream page.

### 9.2 What placement does

```python
    # tools/rag/views.py::document_upload, per file
    ingest.enqueue_ingest(str(dest), category or None, move=True, actor=principal,
                          workstream_id=(scope.workstream_id
                                         if placement == CONTAINED else None))
```

`enqueue_ingest` and `stage_document` each gain one keyword-only `workstream_id=None` parameter,
threaded to the `Document` row at creation and **not re-read on a re-stage**: a document's home
is set when it is first staged and a later content change re-ingests it in place. `origin` is
not a parameter at all here — an upload is `Origin.UPLOAD`, which is the field's default, and
only §10.4 ever writes the other value.

**Everything outside a stream is unchanged, including its actor.** The watcher
(`ingest.watch_folder` → `poll_once`, acting as `SERVICE_PRINCIPAL`), `manage.py ingest`
(`ingest_path`), the `rag.ingest` tool runner and the library page's own upload form all call
the same functions with `workstream_id=None` by default and produce universal documents, exactly
as today. Owner decision 5 says so and the default parameter is what makes it true without a
single caller edit.

**The inbox path is unchanged too.** A stream upload still lands under
`INGEST_INBOX_DIR/<category>/<name>` with the existing `is_relative_to` traversal guard, and is
still `move=True`. Containment is a database fact, not a directory layout, so the watcher racing
the upload view for the same file — the race that view's docstring already documents — behaves
identically. §24 records the one consequence: if the watcher wins the race, the document is
staged **universal**, because the watcher knows nothing about streams.

### 9.3 Labels and placement are independent

The upload form's existing `entitlements` checkboxes are untouched and orthogonal. A contained
document may carry labels (and should, if its content warrants them: containment is organisation,
labels are access). A universal document uploaded from inside a stream carries whatever labels
were ticked and is immediately visible in the library. The two fields are rendered side by side
with one line distinguishing them: *"Placement decides where it lives. Labels decide who may
read it."*

---

## 10. Consolidation into stream notes

### 10.1 The action

One button, on a stream conversation, on the stream page and on the thread page:
**"Consolidate into stream notes"**. `POST /chat/w/<int:pk>/consolidate/` with a
`conversation` field. Manual only — owner decision 6, and §22 defers automatic triggers.

It refuses, by name, when: the conversation is not in a stream (404 — the route resolves through
`visible_conversations(...).filter(workstream_id=pk)`); the caller is not the stream's owner or
`sees_all_content` (404, the row-addressed rule); a consolidation job for that conversation is
already `queued` or `running` (409, the shape `start_turn`'s in-flight refusal already uses); or
the conversation has no completed turns to distil (400, named).

### 10.2 The job kind

```python
# tools/rag/apps.py::RagConfig.ready()
register_job_kind(JobKind(
    key="rag.consolidate",
    label="Consolidate into stream notes",
    planner="tools.rag.jobs.plan_consolidate",
    handler="tools.rag.jobs.run_consolidate",
    summarizer="tools.rag.jobs.summarize_consolidate",
    default_priority=200,
    on_terminal="tools.rag.jobs.on_consolidate_terminal",
))
```

**Registered by `tools/rag`, not by `agents`,** and that is the placement decision this section
turns on. The handler needs three things: the transcript (an `agents` row), a model call, and a
document write plus a re-ingest (`tools/rag`). Only `tools/rag` can reach both ends — through
`agents.workstreams`, the permitted direction (§4.2) — while `agents` may not reach documents at
all. Author decision 18.

**`plan_consolidate` requests TWO roles**, so the job that distils is the job that ingests and
the operator watches one queue row rather than two:

```python
def plan_consolidate(payload: dict) -> tuple[list[ModelRef], bool]:
    # CHAT_CONVERSE_ROLE for the distillation call, RAG_EMBED_ROLE for the
    # re-ingest of the note. `plan_ingest` already returns more than one
    # ref, so this is the established shape rather than a new one.
    ...
    return refs, True     # exclusive, as every model-consuming kind is
```

**`chat.converse`, not a new role.** Owner decision 6 asks for a documented prompt constant, not
a new binding an operator must configure before the feature works. A box that can hold a
conversation can distil one. If a deployment ever wants a small dedicated summariser, that is a
`RoleSpec` registration and a console binding, and §22 records it as the hook.

**`on_terminal`** exists for the same reason `rag.ingest`'s does: the action writes a durable
row before the handler runs — a `queued` marker the page reads to render "consolidating…" and to
refuse a second submission — and a job cancelled while queued, or permanently orphaned, must
clear it. It is one conditional `UPDATE` filtered on the marker still being present, never a
read-then-save, exactly as `on_ingest_terminal` is.

### 10.3 The distillation prompt constant

```python
# tools/rag/distil.py -- ONE transcript in, ONE model call, ONE string out.
#
# The distillation instruction sent with every consolidation -- PLATFORM
# BEHAVIOR, not a model default: this exact wording is what makes "a
# retrievable note, not a chat recap" true of every engine
# `chat.converse` could ever be bound to, regardless of that model's own
# summarising manners. A module constant, not buried inline in
# `distil_conversation` below, so an operator auditing or overriding
# platform behavior has exactly one place to look -- and one place to
# change, a deliberate, NAMED deferral: making this operator-editable (a
# `RagSettings` field, say) is real future work this constant's
# existence flags, not a decision this task makes.
#
# DESIGNED FOR TWO CONSUMERS. Consolidation (section 10) is the first.
# Conversation compaction (section 22) is the second: it will reuse this
# same constant and this same one-call function to replace a live prompt
# history with its summary when a conversation approaches its model's
# context limit. The wording therefore describes the OUTPUT -- a
# self-contained, factual distillation that stands without the
# transcript -- and says nothing about where that output is going.
DISTILLATION_PROMPT = (
    "Distil the conversation below into a self-contained note that will be read on its own, "
    "without the conversation. Keep every decision, fact, figure, name and open question, and "
    "the reasoning that led to each. Drop pleasantries, restatements and turn-taking. Write it "
    "as prose under short headings, in the conversation's own terms. Do not add anything that "
    "was not said, and do not summarise away specifics."
)


def distil_conversation(turns: tuple[dict, ...], *, llm=None) -> str:
```

The shape follows `tools/rag/extract.py::EXTRACTION_PROMPT` exactly: a `SCREAMING_SNAKE` module
constant, a parenthesised implicit-concatenation of string literals (no triple-quote indentation
to strip), immediately above the single function that consumes it, framed as platform behaviour,
carrying its own named operator-editability deferral. That is the one precedent in the tree and
this is the second instance of it.

### 10.4 The note document

`run_consolidate`, in order:

1. `transcript_for(actor, conversation_id, limit=CONSOLIDATION_MAX_TURNS)` through the agents
   seam → pure dicts. `None` → the job fails with a named error rather than distilling something
   the actor may not read.

   **`CONSOLIDATION_MAX_TURNS = 400`**, in `tools/rag/distil.py` beside the prompt it feeds. Not
   `HISTORY_TURNS = 20` — that is a live turn's prompt budget and distilling only the last twenty
   turns would silently make "the conversation" mean something else. Not uncapped either: the
   transcript goes into one model call, and an unbounded one would fail against the bound model's
   context window with no named reason. When the cap bites, the job distils the **most recent**
   400 replayable root-depth turns and the note's first line says so in one sentence — an honest
   partial beats a failure and beats a silent truncation. Distilling a longer conversation in
   chunks is a §22 hook, not v1.
2. `distil_conversation(turns)` → one string.
3. Write it to a **deterministic path**: `<DATA_DIR>/notes/<conversation_id>.md`, with a first
   line naming the source conversation and the date. **Under `DATA_DIR`, deliberately not under
   `INGEST_INBOX_DIR`**: the watcher polls the inbox, and a note written there would be staged
   twice — once by this job and once by the watcher, as a *universal* document with a service
   principal for an actor. `stage_document` takes any path and `move=False` leaves the file in
   place, so a directory the watcher never looks at costs one `settings` constant
   (`NOTES_DIR = DATA_DIR / "notes"`, beside `DOCUMENTS_DIR` and `INGEST_INBOX_DIR`) and closes
   the whole question. Deterministic because `stage_document` dedups on `original_path`:
   the second consolidation writes the same path, `stage_document` finds the existing row, sees a
   changed `file_hash`, calls `_delete_existing_data`, and re-ingests **in place** — the same
   row, the same id, the same store directory, the same `document:<id>` reference in every turn
   that ever cited it. That is owner decision 6's "overwritten and re-ingested" delivered by the
   ingest path exactly as it already works, with no new code for the overwrite case at all.
   Author decision 19.
4. Set on the row, in one transaction: `title = f"Notes — {conversation.title}"`,
   `origin = Origin.NOTES`, `notes_conversation_id = conversation.pk`,
   `workstream = <the stream>`, `extraction = {"method": "distillation", …}` in the existing
   snapshot shape.
5. **Inherit the taint.** `set_document_labels(actor, doc, taint_ids_for_conversation(cid))` —
   the conversation's tags become the note's entitlement labels. Owner decision 6: no laundering.
   `set_document_labels` already writes the difference in one transaction, audits both
   directions and re-stamps the chunks, so this is a call, not a mechanism.
6. `run_ingest_or_fail(doc, sha)` in-process, using the embed model the planner admitted.

**Step 5 is the one that has to be right, and it is deliberately a *widening* only.** A
re-consolidation recomputes the labels from the conversation's current tags, which are additive,
so the note's labels only ever grow. A note can therefore never become *more* readable than the
conversation it came from, which is the property the whole step exists for.

**Step 5 is also an exception to the platform's labelling-authority rule, and it is deliberate.**
`set_document_labels`' own docstring says *"THE CALLER CHECKS THE PREDICATE"* —
`tools.rag.access.may_label_document` plus the view's "owner of every entitlement being added or
removed" rule. `run_consolidate` checks neither: it applies `taint_ids_for_conversation(cid)`,
which under ruling B can contain entitlements the actor neither owns nor holds, and the
`DOCUMENT_LABELLED` audit rows are written in the actor's name. **The exception is the point.**
The authority rule governs a person *choosing* labels for a document; nobody is choosing here —
the labels are copied from what the material already carried, and refusing to copy one because
the actor does not own it is precisely the laundering owner decision 6 forbids. Recorded as
author decision 27 rather than left as a silence, and `run_consolidate` names the exemption in
its own docstring so a reader of `set_document_labels`' callers can see why one of them does not
check.

**Step 3 destroys the previous note before step 6 recreates it, and that window is real.**
`stage_document`, on a changed hash, runs `_delete_existing_data(existing)` inside its
`transaction.atomic()` — `delete_chunks_for_document`, every `DocumentRow`, and
`store.remove_document_files(doc.id)` — then sets `status=PENDING`
(`tools/rag/ingest.py:551-563`, `:852-875`). If step 6 then fails — an embed-model outage, an
orphaned worker — `run_ingest_or_fail` writes `status=FAILED` and re-raises, and the note row
survives with **no chunks, no stored file**: the previous note's content is gone, it retrieves
nothing into the stream, and `rag-document-file` 404s on it. An earlier draft's *"with no new code
for the overwrite case at all"* is exactly what made this invisible; the claim is true and it is
not the whole story.

Three things bound it, and the fourth is the fix:

- **The model call cannot cause it.** Step 2 precedes step 3, so a distillation failure destroys
  nothing — the exposure is the embed step alone.
- **The row is not lost**, only its content: `status=FAILED`, `status_detail` carrying the
  engine's own words, and `notes_conversation_id` still pointing at its conversation.
- **The stream page says so**, rendering the note's `FAILED` chip in the Contained group rather
  than a silent absence.
- **The stream page offers Re-consolidate on a `FAILED` note** — the repair, in one click, from
  the page it happened on. Without it the documented repair is `document_reingest` from the
  library page, which a non-admin stream owner cannot reach for a contained document at all
  (§8.1), so the person whose note was destroyed would have to ask an administrator to fix it.

§17.6 forces the failure and asserts all four; §24 concern 5 records the window.

**`"Notes — "` with an em dash**, and the title is regenerated on every consolidation so a
renamed conversation gets a renamed note. `truncate_title` (`agents/chat/service.py`) is not
reused — it is `agents` code and `title` is a 512-character column here — so the note title is
truncated locally at 512 with the same ellipsis convention.

**Notes are ordinary documents.** They are contained, so they are in the stream's corpus and
nowhere else; they are chunked and embedded by the same path; they are retrievable by the
stream's turns; they are readable at `rag-document-file` by anybody the labels allow **from
inside the stream** (§8.1's stream-aware containment — a note is a contained document like any
other, and its URL 404s from outside); and they
are deletable and re-ingestable from the library page by an administrator. Nothing about a note
is a special case downstream of the job that writes it.

### 10.5 Staleness

The stream page shows, per conversation, **to the stream's owner and to `sees_all_content`, and
to nobody else**, one of:

- *"Not consolidated"* — `consolidated_through_index is None`.
- *"Up to date"* — the conversation's latest `Turn.index` equals `consolidated_through_index`.
- *"N turns since last consolidated"* — the difference.

**Owner-only, because the button it labels is owner-only** (ruling F, §23.F). A staleness hint is
a prompt to press Consolidate; rendering it to a recipient who would get a 404 would be the
render-vs-gate pair broken on the surface it is most visible on, and `staleness_for` is simply not
called for a non-owner — one predicate, checked once for the page rather than once per row.

`N` is a difference of two integers on rows the page already loaded, computed in
`agents/workstreams.py::staleness_for(conversations)` with one aggregate query for the latest
index per conversation. `record_consolidation` (§4.2) writes both columns, called by the job
through the seam.

**Index difference, not turn count.** `_finish` leaves deliberate gaps in `Turn.index` (its own
docstring says so), so `latest_index - consolidated_through_index` can exceed the number of turns
actually added. It is a staleness *hint*, the owner's own word, and an over-estimate of "how much
has happened here" is the right direction for a hint to err in. Author decision 20.

**And it counts turns, so it says one more thing when tags have moved.** A note's labels are
recomputed only on re-consolidation (§10.4 step 5), so a conversation that acquired a tag after
its last consolidation has a note that is **under-labelled relative to its stream**. That is
safe — the newer material is not in the note — but nothing on a turn-counting hint would tell an
owner it had happened. The hint therefore appends *"; 1 tag added since"* when
`ConversationTaint.at > consolidated_at` for any row, off the same batched query, so the one case
where re-consolidating changes *access* rather than *content* is visible on the page that offers
the button.

### 10.6 What consolidation never does

- **It never cascades.** One conversation, one note. Consolidating conversation A does not touch
  conversation B's note, does not regenerate a stream digest, and does not re-ingest anything but
  its own row.
- **It never deletes the conversation.** The thread is untouched and still readable, still
  postable. (This is the line that most distinguishes consolidation from the deferred compaction
  feature of §22, which replaces a live history.)
- **It never runs itself.** No trigger, no schedule, no threshold. §22.
- **It never synthesises across notes.** A stream digest is §22.

---

## 11. Instructions

`agents/runtime/prompt.py::build_messages` gains exactly one block, and the change is four lines:

```python
    messages: list[ChatMessage] = []
    system = agent.system_prompt
    stream = conversation.workstream            # NULL for a loose conversation
    if stream is not None and stream.instructions.strip():
        system = (f"{system}\n\n" if system else "") + _instructions_block(stream)
    if system:
        messages.append(ChatMessage(role=MessageRole.SYSTEM, content=system))
```

```python
_INSTRUCTIONS_HEADER = "Workstream instructions — the operator's standing instructions for this workstream:"

def _instructions_block(stream) -> str:
    return f"{_INSTRUCTIONS_HEADER}\n{stream.instructions.strip()}"
```

**Appended to the agent's system message, not sent as a second system message.** Some engines
collapse, reorder or drop a second system message; one message with two labelled parts behaves
identically on every engine and is what the operator sees when they read the prompt back.

**A blank system prompt with non-blank instructions still emits a system message**, and that is
the behaviour change the guard above encodes. `build_messages`' existing rule — "a blank system
prompt emits no system message at all, because an empty system message is not neutral" — is
preserved in its own terms: the message is emitted when there is *something to say*, and stream
instructions are something to say.

**Clearly labelled**, per owner decision 9, and the label is a constant so a test can assert the
model was told whose words these are. The block never describes the wall, the taint or what the
model may not retrieve (§6.5).

**Delegates do not inherit it.** `agents/runtime/delegate.py` builds its own message list from
the delegate's own system prompt plus the task string, deliberately, and this spec does not
change that. A delegate is a tool the root turn calls; it retrieves through the same seams under
the same wall (the `ToolContext` carries the scope, §12.4's mechanism), so the enforcement
travels even though the prose does not. Author decision 21, and §24's second concern.

---

## 12. Sharing a workstream, and the two gates

### 12.1 Gate one — share time

```python
# agents/visibility.py
def share_workstream(principal, workstream, *, user=None, group=None, level):
    """Share `workstream`. Returns a `SharedWorkstream` on success, or a
    `ShareRefused` naming the missing entitlements.

    UNLIKE `share_conversation`, WHICH RETURNS `None` FOR EVERY REFUSAL.
    That function's silence is right for its three refusals, which are all
    "you may not" or "that is not a valid request". This one has a fourth
    refusal that is neither: the recipient is missing entitlements, and
    naming the ones the SHARER THEMSELVES HOLDS is actionable rather than
    a leak. Owner decision 7, fenced by ruling B (section 23.B) -- the
    sharer does NOT necessarily hold every tag (a previous recipient's
    turn may have brought one in), so the message names what this viewer
    holds and counts the rest.
    """
```

`workstream_taint_ids(workstream) -> frozenset[int]` lives **here, in
`agents/visibility.py`**, beside `share_workstream` — not in `agents/workstreams.py`. The two
agents-side stream modules meet in WS-2 and the direction has to be stated before they do:
**`agents/workstreams.py` imports `agents/visibility.py`, never the reverse** (§4.2). Putting the
taint reader in `workstreams.py` would make `share_workstream` import it and close the cycle;
`stream_access` (§12.2, in `workstreams.py`) reads it from `visibility.py` and the arrow stays
one-way.

The gate itself is two set operations:

```python
    missing = workstream_taint_ids(workstream) - recipient_entitlement_ids(subject)
    if missing:
        return ShareRefused(missing_ids=missing, missing_names=(...))
```

`recipient_entitlement_ids` is a new `identity/access.py` function —
`entitlement_ids_for_subject(*, user=None, group=None) -> frozenset[int]` — because the existing
`held_entitlement_ids` takes a `Principal` and the recipient here is a `User` or a `Group` row,
not the caller. It is the same one join `_grant_ids` already runs, keyed differently.
`entitlement_names(ids) -> tuple[tuple[int, str], ...]` joins it, so a page can render ids as
names without importing `identity.models`. Both are additions to an existing sanctioned seam,
not a new module. Author decision 22.

**A group recipient is checked as the intersection of its members' grants — no.** It is checked
as the **group's own grants**, `EntitlementGrant.objects.filter(group=g)`. A group is the subject
of a grant in this codebase (`grant_user_xor_group`), so "does this group hold E" is a row, not
a computation over membership; and the read-time gate re-checks each actual reader anyway, which
is where a member who personally lacks E is caught. Author decision 23.

**The refusal names only what its VIEWER holds, and counts the rest** — ruling B (§23.B), and it
is one filter applied at the point of rendering:

```python
# agents/visibility.py, module scope, beside `share_workstream` and
# `workstream_taint_ids`. The cap on how many entitlement names one
# message may disclose (section 12.3, fence 3) -- a named constant for the
# reason `MAX_PINS_PER_STREAM`, `CONSOLIDATION_MAX_TURNS`,
# `WORKSTREAM_SIDEBAR_LIMIT` and `SIDEBAR_LIMIT` are: a bound a test
# asserts is a bound the code has to name.
NAME_CAP = 5


def name_for_viewer(missing_ids, viewer_principal, *, disclose_all: bool = False):
    """Render `missing_ids` for whoever is READING this message.

    DEFAULT (`disclose_all=False`) -- NAMED: the ids this viewer holds;
    they can act on those and they already know the name. COUNTED:
    everything else, because an entitlement's NAME is not disclosed to
    somebody who neither owns nor holds it anywhere else on this platform
    (section 12.3), and a refusal message is not the place to start.

    `disclose_all=True` -- every id NAMED, capped at five with "and N
    more". EXACTLY ONE CALLER: the dormant-share page of section 12.3,
    where owner decision 8 asks for the names in so many words and where
    the reader holds a live `Share` row on this stream. See section 12.3.
    """
    held = held_entitlement_ids(viewer_principal)
    if disclose_all:
        return entitlement_names(missing_ids)[:NAME_CAP], ...
    named = entitlement_names(missing_ids & held)
    rest = len(missing_ids - held)
    ...
```

**Where each mode is used — one rule per site, and there are three sites** (ruling E, §23.E):

| Site | Reader | Mode |
|---|---|---|
| **Gate one**, the share-refusal message (§12.1) | the **sharer** | default — names what they hold, counts the rest |
| the owner's **dormant marker** on the share list (§12.2) | the **owner** | default — same rule, same reason: under ruling B the owner may hold no grant for a tag on their own stream, so the marker reads *"Dormant — that account no longer holds **Finance**"* when the owner holds `Finance` and *"…no longer holds 1 entitlement you don't hold"* when they do not |
| **Gate two**, the invited reader's 403 page (§12.3) | the **recipient** | **`disclose_all=True`** — every missing name, capped at five |

**Gate two is the exception and owner decision 8 is why.** Applying the default there would name
**nothing, always**: at gate two the viewer *is* the recipient and `missing = taint_ids -
held(recipient)` by construction, so `missing_ids & held(viewer)` is empty by definition and the
page would read *"and N entitlements you don't hold"* — the precise opposite of the decision's own
words, *"contains material from entitlements you don't hold: X, Y"*. The decision is binding and
the page is the reason it was taken; §12.3's two other fences are what make the disclosure safe
there and not at gate one.

So the share panel says, to a sharer who holds `Finance` but not `Legal`:

> *"Not shared. This workstream contains material from **Finance**, and 1 more entitlement you
> don't hold, and that account holds none of them. Grant what you can, or share a conversation
> instead."*

and, to a sharer who holds both:

> *"Not shared. This workstream contains material from **Finance** and **Legal**, and that
> account holds neither. Grant them, or share a conversation instead."*

The last clause is real advice: a single conversation inside the stream may carry fewer tags than
the stream does, and `chat-conversation-share` already exists. **"and 1 more you don't hold" is
still actionable** — it tells the sharer the refusal is not theirs to resolve and that an
administrator is the next step — without turning a refusal into an entitlement-name oracle.

**Why the sharer may not hold every tag.** An earlier draft asserted they always do ("they are
the stream's tags, and the stream is theirs"). Ruling B makes taint accumulate from **any**
participant's retrieval, because security is the point and authorship is irrelevant: a recipient's
turn, run under the recipient's own grants, can put material into the owner's stream that the
owner holds no grant for. That is deliberate — the alternative is a stream whose tag set
understates what is in it — and §24 concern 6 records what it costs.

### 12.2 Gate two — read time, and dormancy

Every non-owner read of a shared stream re-checks. Tags grow and grants are revoked, so a share
that passed gate one is not therefore passing now.

```python
# agents/workstreams.py
@dataclass(frozen=True)
class StreamAccess:
    """Why this principal may or may not read this stream, right now."""
    ok: bool
    missing: frozenset[int]         # empty when ok
    missing_names: tuple[str, ...]  # for the reader's own error page
    is_owner: bool
    via_share: bool

def stream_access(principal, workstream, *, settings_row=None) -> StreamAccess
```

- Owner, or `sees_all_content` → `ok=True`, no tag check. **The owner is never locked out of
  their own stream by its tags** — the tags were caused by turns *in this stream*, which is the
  owner's space, and a stream whose owner could be shut out of it by a recipient's retrieval
  would be a space nobody could administer. Note what this sentence deliberately no longer says:
  not "they caused them". Under ruling B a recipient's turn taints the owner's stream, so the
  owner may hold no grant for a tag on their own stream (§24 concern 6).
- A share row exists → `missing = taint_ids - held_entitlement_ids(principal)`;
  `ok = not missing`.
- No share row → the caller never reached this function; `visible_workstreams` already excluded
  the row and the view already answered 404.

**Dormancy is computed, never stored** (§5.8). One extra query per stream page view for a
recipient — the taint ids — against an indexed unique constraint, on a page that is already
doing several. The owner's share list computes it per row, which is N queries for N recipients;
`share_list_for(workstream)` batches it into one grants query keyed by subject, the same way
`sidebar_context` batches its per-row predicate.

**A dormant share still LISTS for the recipient**, in the sidebar's Workstreams section, marked.
Hiding it would make a stream the recipient has been reading vanish with no sentence, which is
strictly worse than the honest refusal below — and the recipient already knows it exists.

### 12.3 The 404 house rule, and its one scoped exception

The house rule is absolute and stated in three places already (`identity/routes.py`,
`agents/visibility.py`, the identity spec §11.1): **404, not 403, on a row-addressed URL**,
because a 403 confirms the row exists.

Owner decision 8 makes one scoped exception, and this section fences it.

| Who | What they get |
|---|---|
| A stranger, or somebody whose share was revoked | **404**. The house rule, untouched. `visible_workstreams` never returned the row, and the view never reached the gate |
| **A holder of a real, live `Share` row whose tags outran their grants** | **403** with an explicit page naming the missing entitlements |

The page's words:

> **This workstream is not readable right now.**
> It contains material from entitlements you don't hold: **Finance**, **Legal**.
> Ask an administrator for those entitlements, or ask the workstream's owner.

**The exception is safe because the recipient already knows the stream exists.** Somebody
deliberately shared it with them; it is in their sidebar; it was readable yesterday. What the 403
adds is *why*. It reveals nothing about the stream's contents, its conversations, its documents
or its other recipients.

**It does disclose entitlement NAMES to a non-admin, and that is genuinely new on this platform.**
An earlier draft claimed the platform "already names them on the entitlements page to every
signed-in account"; that is **false**. `identity-entitlements` is class **S**
(`identity/routes.py:224`, `_TIER` maps `"S"` to `ADMIN`), the accounts page that renders
`effective_entitlements` is class `S` too, and the only entitlement names a non-admin sees
anywhere come from `labelling_entitlements`, which filters to `owned_entitlement_ids(principal)`
for a non-admin (`identity/access.py:353-356`). **Today, the name of an entitlement you neither
own nor hold is disclosed to you by no route.** This page is the first, and owner decision 8 asks
for it deliberately, trading a name for actionability. §24 concern 7 records the trade so nobody
downstream mistakes it for a thing the platform already did.

**Three fences, so the disclosure is the smallest one that is still useful:**

1. **Only to a holder of a real `Share` row.** The 403 branch is unreachable otherwise — see the
   table above — so no input a stranger can supply produces a name.
2. **Every missing entitlement is named, and nothing else is** —
   `name_for_viewer(missing, reader, disclose_all=True)` (§12.1), the one caller of that mode.
   The set named is exactly `taint_ids - held(reader)` for **this** stream: not the reader's other
   streams, not the platform's entitlement catalogue, not who else holds them. Ruling E (§23.E)
   settles this against ruling B's default, which would have named **nothing** here — at gate two
   the viewer is the recipient and the missing set is disjoint from what they hold by definition,
   so the count-what-you-don't-hold rule degenerates and owner decision 8's own sentence becomes
   unwritable.
3. **Capped at five names**, with "and N more", so a stream with a long tag list cannot be turned
   into a bulk enumeration by sharing it and letting the grant lapse.

**It is scoped by a row, not by a guess.** The 403 branch is reachable only when a `Share` row
matching this principal exists. There is no input a stranger can supply that reaches it: without
the row, `visible_workstreams` excludes the stream and the view raises `Http404` before
`stream_access` is called. §17 pins that with the negative case as well as the positive one.

The owner's share list carries its own line, per recipient — *"Dormant — that account no longer
holds Finance."* — reporting the same fact to a different reader under a **different rule**: it is
one of the two default-mode sites of §12.1's table, so it names only what the **owner** holds and
counts the rest, while the page above names everything. Ruling E is exactly the removal of the
symmetry an earlier draft assumed here.

### 12.4 What a share reaches, and what a recipient may do

**Reaches** (all read-and-converse, `Level.USE`):

| Row | Through |
|---|---|
| the stream page | `visible_workstreams` (the `Share` clause) then `stream_access` |
| every conversation in the stream | `visible_conversations` gains **two clauses**, and this is the one place the container→contents rule is written. The recipient's: `Q(workstream_id__in=shared_keys(Share.Target.WORKSTREAM, principal))`. The **owner's**: `Q(workstream__in=Workstream.objects.filter(owned_rows_q(principal)))` — ruling C, below |
| new turns in those conversations | `may_post_to` gains the same two clauses. A stream share is `use` by construction (§5.7) |
| new conversations in the stream | `chat-start` accepts a `workstream` a recipient may reach. The row is stamped `**owner_fields(principal)`, so it is owned by the **recipient** — and read by the stream's owner through the owner clause above |
| the stream's documents | §6.1's outer intersection — a recipient reads what their **own** grants allow, and the two gates are what make that set non-empty for the stream's tags |

**Ruling C (§23.C): a stream's owner reads and manages every conversation in their stream,
including ones a recipient started there.** The stream is the owner's space and its contents
cannot be opaque to them. Without the owner clause, `create_conversation`'s
`**owner_fields(principal)` stamp would make a recipient's new thread invisible to the stream's
owner on every surface: the stream page would omit it, §10.1's consolidate (owner-only, resolved
through `visible_conversations`) could never reach it, and §8.1's `PROTECT` delete would refuse
with *"This workstream holds 3 conversations…"* counting rows the owner can neither open nor
delete — an undeletable stream with an unactionable refusal. Meanwhile §7.3 would still union that
thread's taint into the stream, so the owner's future shares would be refused for entitlements
brought in by a conversation the owner cannot read.

So `may_manage_conversation` gains the stream-owner branch alongside its existing ones:
**consolidating and deleting a conversation in a stream is available to its creator and to the
stream's owner**, and to nobody else. `PROTECT`'s count is then always a count of rows the person
reading the refusal can act on (§8.1).

**And the recipient is told, on the page, before they type anything.** The stream page renders one
line above the New-chat composer for a non-owner: *"This is somebody else's workstream — its owner
can read the conversations you start here."* An arrangement this reasonable is still a surprise if
nobody says it, and §15.3 puts it where the surprise would otherwise happen.

**Does not reach**, all owner-only in v1 (owner decision 7):

| Action | Refusal |
|---|---|
| re-sharing | 404 from `share_workstream`'s `_may_share`, the shape `share_conversation` already uses |
| editing the wall | 404 |
| pinning or unpinning | 404, and `WorkstreamScope.may_upload` is `False` so the pin control does not render |
| uploading into the stream | 404, same flag, same render-vs-gate pair |
| editing instructions, the name, the upload default | 404 |
| consolidating **anything**, including a thread they started themselves | 404 — it writes a stream-contained `Document`, labelled from the stream's taint set and ingested on the owner's box, which is a stream mutation and belongs beside the other four (ruling F, §23.F). Ruling C is what makes the **owner** able to consolidate a recipient's thread; it does not make the recipient able to consolidate anything |
| deleting the stream | 404 |
| deleting or renaming a conversation they did not start | 404 — `may_manage_conversation`, with the creator branch and the stream-owner branch and nothing else |

**Every "does not reach" row is a 404 and not a 403**, because they are all row-addressed
mutations of a row the recipient can see — which is precisely the case
`may_manage_conversation`'s docstring already rules on for shared conversations. §12.3's
exception is a **read** refusal on the container, and it does not spread to any of these.

**The turn's scope travels in `ToolContext`.** `agents/contracts/tools.py::ToolContext` gains
`stream: WorkstreamScope | None = None`, defaulting to `None` so every existing runner and every
test that builds a bare context keeps working. `invoke_tool` sets it from the turn's conversation
alongside `tool_key` and `supplied_keys`, in the same `replace()` call; `tools/rag/tools.py`'s
two retrieval runners read it and hand it to `document_visibility`. A **delegate inherits it**,
the same way it inherits `principal` and for the same reason: a delegate is not a way around a
wall any more than it is a way around a label.

---

## 13. Postures

One codebase, one set of tables, and machinery that is invisible until a row says otherwise —
the identity spec's rule, applied mechanism by mechanism. The column that decides is
`IdentitySettings.posture`, read through `identity.access.accounts_on`.

| Mechanism | `open` (one principal, no accounts) | `personal` / `enterprise` |
|---|---|---|
| **The stream itself** — name, instructions, conversations, archive | fully live and fully useful. Organisation and containment are the two things a household box wants most, and neither needs an account | the same |
| **Containment** | **fully live.** A contained document is out of the library, out of Ask and out of Search, for the one principal too. Containment is a **corpus** rule, not a permission rule — §6.2's clause sits outside the `unrestricted` branch precisely so this is true | the same |
| **Pinning** | fully live | the same |
| **Upload placement** | fully live. The choice is about *where a document lives*, which is a question a household box asks as often as an organisation does | the same |
| **Consolidation and notes** | fully live | the same |
| **The wall** | **INERT, by ruling A (§23.A), and the rows survive.** `_wall_for(conversation)` returns `frozenset()` without reading `WorkstreamScopeEntitlement` at all when `accounts_on()` is False, so no seam narrows, no permission query runs, and nothing is refused. The Scope editor is not rendered. Wall rows written under `enterprise` are **dormant, not deleted**, and start binding again the moment the posture returns | live. §6 in full |
| **Taint** | **inert but consistent.** `entitlement_ids_for` returns the empty set for every document, so `stamp_turn_taint` adds nothing and writes nothing beyond the one artifact scan it does in memory | live. §7 in full |
| **Sharing** | **inert but consistent.** There is no second account to share to; the share panel does not render, and `share_subjects` (which already excludes the caller) returns nothing. Both gates are present in code and unreachable in practice | live. §12 in full |
| **`sees_all_content`** | `True` for the one principal, first branch in every function, **zero permission queries** | `is_admin and admin_sees_content` |

**"An open box never runs a permission query" is preserved, and ruling A is what preserves it.**
The three new predicates — `visible_workstreams`, `stream_access`, `tool_access_for`'s wall
narrowing — all short-circuit on `sees_all_content` before touching `EntitlementGrant`,
`WorkstreamScopeEntitlement` or `WorkstreamTaint`, exactly as their IA-2 siblings do. An earlier
draft broke that: `and not wall` (§6.3) is precisely a bypass of the `sees_all_content`
short-circuit, so **deciding** `not wall` would have required reading
`WorkstreamScopeEntitlement` on every stream turn in every posture *before* any short-circuit
could be taken — and, once it said a wall existed, `tool_entitlement_ids()` too. Gating the read
on `accounts_on()` restores the claim exactly.

**On `personal` and `enterprise`, the honest number is one indexed read.** A stream turn reads
`WorkstreamScopeEntitlement` for its stream once, over `uniq_workstream_scope`, and the wall
value is threaded from there through all four call sites of §6.3 rather than re-read at each.
Zero grant queries beyond the ones IA-2 already ran. §19.1's done-when 9 asserts the open-box
zero and this one.

**Dormant, not deleted, and the direction runs both ways.** §3.3 of the identity spec calls a
posture change "a switch, not a migration": the tables exist in every posture and only the
questions asked of them change. `ToolEntitlement`, `DocumentEntitlement` and the model-set rows
already survive a switch to `open` with no posture branch anywhere in their readers, and a wall
behaves the same. A stream configured under `enterprise`, run for a while under `open`, and
switched back finds its wall exactly as it left it.

**There is no posture branch in a visibility function**, and this phase adds none. Ruling A's
branch is in `_wall_for` — a reader of one table, in the runtime — not in `visible_workstreams`,
`stream_access` or any `visible_*` function, whose bodies stay posture-free exactly as IA-2 left
them.
`personal` and `enterprise` behave identically here; they differ only in which admin pages exist,
which is IA-1's rule and not this phase's business.

**A wall written under `enterprise`, run for a while under `open`, and switched back finds itself
exactly as it was left.** The rows are there throughout; only the questions asked of them change.
That is decision 8 of the identity spec — "a switch, not a migration" — and ruling A is what makes
it true in the `open` direction, which is the direction that would otherwise have bricked a
stream. **A wall cannot be *written* on an open box at all** — the Scope editor is not rendered
there (§15.3 item 3) — so the mirror sentence an earlier draft carried, "a wall set on an open box
then switched to `personal` starts biting", describes a state ruling A makes unreachable. A wall
is a table, not a setting, and `accounts_on()` decides whether anybody reads it.

---

## 14. Every new route, its class, and its rule

Seven new routes. Two existing routes grow a field and keep their class. Every name below is
added to `identity/routes.py::ROUTE_RULES` **in the same commit as the route**, because a name
absent from that table is treated as `ADMIN` and `identity/tests/test_route_matrix.py` fails
naming it — which is what keeps that table a live artefact.

Classes are IA-1's six, unchanged: **P** public, **A** authenticated, **O** owned content,
**L** library content, **R** operational rows, **S** superuser.

#### New under `/chat/` — `agents/chat/urls.py`

| Path | Name | Method | Class | Rule |
|---|---|---|---|---|
| `w/` | `chat-workstreams` | GET, POST | **A** | GET lists `visible_workstreams`; POST creates one and stamps the actor through `create_workstream(principal, name)`, which is `create_conversation`'s twin and lives in `agents/visibility.py` for the same reason |
| `w/<int:pk>/` | `chat-workstream` | GET | **O** | resolved through `visible_workstreams` → 404. Then `stream_access` → 200, or the **403 of §12.3** for a share holder whose grants no longer cover the tags. The only route in the platform that answers 403 on a row-addressed URL, and §12.3 fences why |
| `w/<int:pk>/edit/` | `chat-workstream-edit` | POST | **O** | one `action` field: `rename`, `description`, `instructions`, `upload_default`, `archive`, `unarchive`, `delete`. Owner or `sees_all_content` only; a recipient gets 404. `delete` refuses by name while conversations or documents remain (§8.1) |
| `w/<int:pk>/scope/` | `chat-workstream-scope` | POST | **O** | sets the wall to exactly the submitted entitlement ids, each of which must be in `held_entitlement_ids(principal)` (§6.1). Owner only |
| `w/<int:pk>/share/` | `chat-workstream-share` | POST | **O** | share and revoke on one URL keyed on a `share_id` in the body, exactly as `chat-conversation-share` does. Owner or `sees_all_content`; a recipient may not re-share. Gate one (§12.1) runs here and its refusal renders as a message, not a 404 |
| `w/<int:pk>/consolidate/` | `chat-workstream-consolidate` | POST | **O** | takes a `conversation` field resolved through `visible_conversations(...).filter(workstream_id=pk)`. Owner only (§10.1). 409 when one is already in flight |

**One `edit` route with an `action` field, not seven routes.** The five owner-only mutations of a
stream row share one predicate and one 404 rule; seven routes would be seven chances for one of
them to drift, and the drift would show as a control that 404s. That is the ruling
`may_manage_conversation` already made for its own five actions, and `identity-entitlement-edit`
already made for its `_ENTITLEMENT_ACTIONS` tuple. `_WORKSTREAM_ACTIONS` is the same shape.

**No `chat-workstream-delete` route.** Delete is an `action` on `edit`, for the same reason.

#### New under `/rag/` — `tools/rag/urls.py`

| Path | Name | Method | Class | Rule |
|---|---|---|---|---|
| `workstreams/<int:ws_id>/pin/` | `rag-workstream-pin` | POST | **O** | `action` = `pin` or `unpin`. **Two resolutions, both 404-shaped:** the stream through `agents.workstreams.workstream_scope(principal, ws_id)` (which is `None` for a stream the caller may not mutate), and the document through `readable_documents(principal)`. The named refusals of §8.2 — contained, capped — are messages on a row the caller can already see, not 404s |

**It is class O, not L**, and the row it is addressed by is the **stream**, not the document.
`rag-document-labels` is the nearest precedent for a `tools/rag` route whose predicate is not
about reading the document's bytes; this one goes further and is about a row in another column
entirely, which is why the stream half resolves through the seam rather than through anything in
`tools/rag`.

#### Existing routes that grow a field

| Name | Change | Class |
|---|---|---|
| `chat-start` | accepts an optional `workstream` field; the stream must be in `visible_workstreams` and pass `stream_access`, else 400. `create_conversation(principal, agent, workstream=…)` stamps it once and nothing ever writes the column again | **A**, unchanged |
| `rag-document-upload` | accepts optional `workstream` and `placement` fields (§9.1); an in-stream upload with no `placement` and no active default is a **400 naming the field**, never a fallback | **A**, unchanged |
| `rag-documents` | accepts an optional `?pin_into=<int>` which renders a Pin control per universal readable row and a banner naming the stream. No new page: the library page already lists, searches and paginates documents, and a second listing would be a second set of counts to keep in agreement | **R**, unchanged |
| `chat-index` | the sidebar it renders gains the Workstreams section (§15.1). No parameter, no class change | **A**, unchanged |

---

## 15. UI

Zero JavaScript, per owner decision 10. Every control below is a link, a `<details>` element, or
a plain POST form with a CSRF token — the idiom `_sidebar.html` already documents. The
conversation page's one sanctioned inline `<script>` (the message poller and its Enter-to-send
handler, `chat/conversation.html`) is untouched and nothing new depends on it.

### 15.1 The sidebar's Workstreams section

`agents/chat/sidebar.py::sidebar_context` returns three new keys and `chat/_sidebar.html` grows
one block **above** the conversation list:

```
  WORKSTREAMS                                   [ + New ]
    ▸ Q3 planning                     4
    ▸ Rebuild                         12
    …2 more
  ───────────────────────────────────
  CHATS
    Yesterday's thread
    …
```

- `sidebar_workstreams` — `visible_workstreams(principal).filter(archived_at__isnull=True)`,
  capped at `WORKSTREAM_SIDEBAR_LIMIT = 10` with the same honest "…N more" line the conversation
  cap already renders, linking to `chat-workstreams`.
- Each row shows its conversation count and, for a recipient, a **dormant** marker when
  `stream_access` fails (§12.2). Computed in one batched query, not per row, the way
  `may_manage_conversation` was made flat by `settings_row` threading.
- **The section renders even when empty**, as one line — *"No workstreams yet — New"* — because
  a feature that only appears once you have used it is a feature nobody finds.

### 15.2 The scoped sidebar

Entering a stream **scopes** the sidebar: the section collapses to the stream's own name as a
heading, the conversation list becomes that stream's conversations, and an **"All chats"** link
returns to the unscoped view.

Mechanically this is one argument: `sidebar_context(principal, current=…, workstream=…)`, whose
body filters the queryset it already builds by `workstream_id` over the `agents_conv_ws` index.
There is no second template, no second route and no second copy of the shape — which is the
ruling `sidebar_context`'s own docstring already made about the archived list, applied again.

Both stream pages — `chat-workstream` and every `chat-conversation` whose conversation is in a
stream — pass the stream, so the sidebar stays scoped for as long as the person is inside.

### 15.3 The stream page

`chat/workstream.html`, extending `chat/base.html`, in this order:

1. **Header** — the name (inline rename form under a `<details>`), the description, and the
   archive/delete controls.
2. **Instructions** — a `<textarea>` and Save. One line under it: *"Sent with every turn in this
   workstream, labelled as the workstream's instructions."*
3. **Scope** — the wall. Checkboxes rendered from
   **`entitlement_names(held_entitlement_ids(principal))`**, not `labelling_entitlements`, so the
   set offered and the set §6.1's gate accepts are the same set (M10, §6.1). One line beside them:
   *"With a scope set, this workstream retrieves only material under these entitlements — and not
   unlabelled material."* (§6.1's consequence, stated where it bites.) **Hidden entirely when
   `accounts_on()` is False** — ruling A, and the condition is the posture, not "the principal
   holds no entitlements". The distinction matters: the wall is inert on an open box, so a hidden
   editor strands nothing, whereas the old condition would have hidden the editor on a box where
   the wall was still binding.
4. **New chat** — the composer. An agent picker (`visible_agents`) and a message box, posting to
   `chat-start` with the stream's id. For a **non-owner**, one line above it: *"This is somebody
   else's workstream — its owner can read the conversations you start here."* (Ruling C, §12.4.)
5. **Conversations** — the stream's threads, each with its staleness hint (§10.5) and a
   **Consolidate** button. The owner sees every thread in the stream, including ones recipients
   started (ruling C). A **recipient sees the threads they may read and no staleness hints and no
   Consolidate button at all** — consolidation is owner-only (ruling F, §23.F), and a control
   that 404s is worse than one that is absent.
6. **Documents** — rendered from the registered panels (§4.2). For `rag.documents` that is three
   groups: **Contained** (with notes marked by `origin`), **Pinned** (each with Unpin), and a
   **Pin a document** control — a capped `<select>` of readable universal documents plus a
   *"browse the library"* link to `rag-documents?pin_into=<pk>`. A note whose last re-ingest
   **failed** renders its `FAILED` chip and a **Re-consolidate** button, which is the whole repair
   path for §10.4's destroy-then-recreate window — without it the fix lives on the library page,
   which a non-admin cannot reach for a contained document (§8.1, §24 concern 5).
7. **Uploads** — the upload form of §9.1, with its placement control in whichever of the three
   states applies, and the upload-default control beside it.
8. **Tags** — the taint set, as names, with one line: *"Material from these entitlements has been
   retrieved into this workstream. Anyone you share it with must hold all of them."* Rendered
   **only** when non-empty, and never on an open box.
9. **Sharing** — the share panel (`chat/_share_panel.html`'s shape, reused), each recipient
   marked live or **dormant** with its reason (§12.2).

**Sections 3, 8 and 9 are absent on an open box**, and not disabled — an operator running a
household box never sees the words "entitlement", "taint" or "share" on this page. That is
"machinery invisible until a row says otherwise", rendered.

### 15.4 The library page

`rag/documents.html` grows two things and loses none:

- a **Workstream** column on each row, blank for universal documents and naming the stream for
  contained ones — because an administrator lists contained documents (§8.1) and a list that
  shows a row without saying where it lives is a list that invites the wrong delete;
- the `?pin_into=` mode of §14: a banner naming the stream and a Pin button per universal
  readable row.

The category sidebar, counts, search box and bulk-label form are untouched.

---

## 16. Audit

### 16.1 Seventeen new actions

Added to `identity/contracts/actions.py::AUDIT_ACTIONS` **in the same commit as their write
sites**, because `AuditEvent.save()` raises on an unknown action and a constant with no writer
is a promise nobody keeps.

| Constant | Value | Written by | `target_type` |
|---|---|---|---|
| `WORKSTREAM_CREATED` | `workstream.created` | `create_workstream` | `workstream` |
| `WORKSTREAM_RENAMED` | `workstream.renamed` | `chat-workstream-edit` (`rename`) | `workstream` |
| `WORKSTREAM_DELETED` | `workstream.deleted` | `chat-workstream-edit` (`delete`) | `workstream` |
| `WORKSTREAM_ARCHIVED` | `workstream.archived` | `chat-workstream-edit` | `workstream` |
| `WORKSTREAM_UNARCHIVED` | `workstream.unarchived` | `chat-workstream-edit` | `workstream` |
| `WORKSTREAM_INSTRUCTIONS_SET` | `workstream.instructions_set` | `chat-workstream-edit` | `workstream` |
| `WORKSTREAM_SCOPE_ADDED` | `workstream.scope_added` | `set_workstream_scope` | `workstream` |
| `WORKSTREAM_SCOPE_REMOVED` | `workstream.scope_removed` | `set_workstream_scope` | `workstream` |
| `WORKSTREAM_UPLOAD_DEFAULT_SET` | `workstream.upload_default_set` | `set_upload_placement_default` | `workstream` |
| `WORKSTREAM_TAINTED` | `workstream.tainted` | `stamp_turn_taint` | `workstream` |
| `CONVERSATION_TAINTED` | `conversation.tainted` | `stamp_turn_taint` | `conversation` |
| `DOCUMENT_CONTAINED` | `library.document_contained` | `stage_document`, `run_consolidate` | `document` |
| `DOCUMENT_PINNED` / `DOCUMENT_UNPINNED` | `workstream.document_pinned` / `…_unpinned` | `pin_document` / `unpin_document` | `workstream` |
| `WORKSTREAM_CONSOLIDATED` | `workstream.consolidated` | `run_consolidate` | `workstream` |
| `WORKSTREAM_UNTAINTED` / `CONVERSATION_UNTAINTED` | `workstream.untainted` / `conversation.untainted` | `workstream_entitlement_cascade`, in `commit=True` mode | `workstream` / `conversation` |

(Seventeen constants across fifteen rows — pin/unpin and taint/untaint each share a row.)

**The untaint pair exists because the cascade is the one v1 path that removes a tag** (§8.4).
Without it the `WORKSTREAM_TAINTED` rows would outlive their own tags and
`for_target("workstream", pk)` would show everything that came in and nothing that left — a trail
that is complete about additions and silent about the only removal the product performs. It is
also the constant the deferred untaint feature (§22) lands on, already in the vocabulary.

**`SHARE_ADDED` and `SHARE_REVOKED` are reused**, with `target_type="workstream"`. They already
carry the subject in `detail` and the target type in its own column; a second pair of constants
would split one question — "who was this shared with" — across two vocabularies.

**Naming follows the existing convention exactly**: dotted `namespace.verb_phrase`, past tense,
constant name the SCREAMING_SNAKE of the value's tail. `library.document_contained` takes the
`library.` namespace because `DOCUMENT_LABELLED` already does and the subject is the same row.

### 16.2 What each taint row carries

`WORKSTREAM_TAINTED`'s `detail` is the evidence a person needs when a share goes dormant:
`entitlement` (id), `entitlement_label` (name), `conversation` (uuid as string), `turn` (pk).
`CONVERSATION_TAINTED`'s is the same minus `conversation`, which is its target. The actor is the
principal the turn ran as, read back from the payload — not the box, and not the agent.

`identity.audit.for_target("workstream", pk)` therefore answers "what has been in this stream,
when, and what brought it in" in one query, which is exactly the question §12.3's error page
makes somebody ask.

### 16.3 Deliberately not audited

- **A refused share.** Gate one writes nothing and returns a sentence; auditing a refusal would
  fill the trail with rows about things that did not happen. The stream's taint rows already
  record why the refusal was correct.
- **A dormant read.** The read-time gate runs on every non-owner page view; auditing it would
  turn a page view into a write.
- **Retrieval itself.** `ToolInvocation` already records every tool call with its principal;
  taint records the *consequence*, which is the durable fact.
- **Reading a stream page, or listing streams.** Consistent with §13.3 of the identity spec:
  reads are not audited on this platform.

---

## 17. Testing

The suite extends the patterns that exist rather than adding a strategy. Every item below names
the existing test module it grows or the pattern it copies.

### 17.1 The route matrix

`identity/tests/test_route_matrix.py` gains the seven new names, asserted in **all three
postures** against **all four principals** and **both settings of `admin_sees_content`** — the
existing sweep, extended, plus two rows it has never had before:

- **`chat-workstream` answers 403, not 404**, for a signed-in account holding a live `Share` row
  on a stream whose tags outran its grants (§12.3), and **404** for the same account with the
  share row deleted. Asserted as a pair, because the exception is only safe if the negative case
  holds.
- **The anonymous-POST row** for each new POST route through a client built with
  `enforce_csrf_checks=True`, the anti-vacuous pin the matrix already uses.

### 17.2 The composition law

One generated matrix, in `tools/rag/tests/test_workstream_corpus.py`, over the cross product of:
document placement (universal / contained-here / contained-elsewhere / pinned), document labels
(none / E1 / E2), the reader's grants (none / E1 / both), the stream's wall (empty / E1 / E2),
and the surface (stream retrieval / loose retrieval / library page / Ask / Search). Each cell
asserts **both** expressions of the rule — the ORM one (`stream_documents` /
`readable_documents`) and the chunk-metadata one (`_visibility_filters`, asserted as a filter
object, not through a live store, for the reason that function's docstring gives) — and asserts
they **agree**. Two expressions of one rule that are never compared are two rules.

The eight cells that matter most, called out so a reviewer can find them:

1. A contained document is invisible in the library, in Ask, in Search **and in another stream**,
   for a reader who holds every one of its labels.
2. A pinned document is visible in its stream **and** in the library, and stops being visible in
   both the moment the reader loses its label.
3. A non-empty wall hides an **unlabelled** universal document (§6.1) and does **not** hide a
   contained or pinned one.
4. `sees_all_content` does not defeat containment: an administrator with the content setting on
   still does not retrieve another stream's contained document into *this* stream's turn.
5. **A contained document opens from inside its stream and 404s from outside it** (M1, §8.1):
   `rag-document-file` and `rag-document-transcript` both succeed for a stream member reaching
   them from the stream page, and both answer **404** at the same URL for a signed-in account
   that is not in the stream — and for an administrator with `admin_sees_content`
   **off**, whose `listable_documents` row is a row and not a key to the bytes. An administrator
   with the content setting **on** gets **200** (ruling G, §23.G): `workstream_scope` returns a
   scope rather than `None` for a principal `sees_all_content` admits to every stream, so the
   mechanism produces a 200 and the earlier draft of this cell asserted a 404 the mechanism
   cannot produce. Containment fences the **corpus**, not the bytes — cell 4 is the corpus half
   and it still holds for that same administrator.
6. **`permits` and `readable_documents` agree about containment**, asserted directly rather than
   through the page — the property `permits` exists for, and the one a one-sided change breaks
   invisibly.
7. **A universal document that is RE-LABELLED is still returned by a loose turn** (M11, §8.3).
   This is the cell that catches an empty-string `workstream` value: `IS_EMPTY` renders
   `metadata_->>'workstream' IS NULL`, which `""` does not satisfy, so a writer that stamped an
   empty value instead of removing the key would pass every other cell in this matrix and fail
   only here.
8. **The no-op guard still fires**: re-stamping an unlabelled, universal document updates **zero**
   chunk rows, asserted with a row count. Without the disjunction of §8.3 this silently
   reintroduces the regression "review finding 3" removed.

**Existing tests this section changes rather than adds to.** §6.2's `workstream` clause is
appended in every branch, so `_visibility_filters` never returns `None` again and the
`test_visibility_filters` cases that assert `None` for "no category, unrestricted principal" must
be rewritten to assert the one-clause filter instead (m12). They are named in the plan so the
change reads as intended rather than as a broken test somebody repaired.

### 17.3 The wall at the other two seams

- **Planner**: a tool labelled `E2` is dropped from a turn in a stream walled to `E1`, for a
  principal holding both — asserted at `plan_turn` **and** at `loop._run_turn`, because IA-2's
  own tests pin that those two agree and a wall that narrowed one and not the other would be a
  tool offered and then refused mid-run.
- **Model**: a connection in a set attached only to `E2` is refused for a turn in an `E1` stream,
  at `preflight_turn`, **before a turn row is written** — the existing `MODEL_NOT_PERMITTED`
  path, with the existing copy.
- **The picker agrees with the planner** (§6.3): `chat_picker_options` on a walled stream's
  conversation page **omits** a connection the walled turn would refuse, and offers every one it
  would accept. The render-vs-gate pair, asserted as a pair, on the surface the wall is most
  visible on.
- **All four call sites narrow.** One parametrised test over `plan_turn`, `_run_turn`,
  `preflight_turn` and `chat_picker_options`, because §6.3's whole correction was that an earlier
  draft named two of them.
- **The `and not wall` line** (§6.3): an administrator with `admin_sees_content` on, in a walled
  stream **on an accounts-on box**, gets a **narrowed** `ToolAccess` and not
  `UNRESTRICTED_TOOL_ACCESS`. One test, named after the line, because it is the one place a
  plausible refactor would silently undo the wall.
- **Ruling A, both halves.** On an **open** box a stream carrying wall rows gets
  `UNRESTRICTED_TOOL_ACCESS`, starts its turns, and refuses nothing — the anti-bricking case;
  and switching that same box to `personal` makes the same rows bind, with no edit and no
  migration, which is the dormant-not-deleted case.
- **The rendered wall set equals the accepted wall set** (M10) for a non-admin who **holds** one
  entitlement and **owns** a different one: the editor offers exactly the held one, and
  `set_workstream_scope` accepts exactly the held one. The single test that catches
  `labelling_entitlements` being used here.
- **Query counts**: a stream page and a stream turn on an **open** box run zero queries against
  `EntitlementGrant`, `WorkstreamScopeEntitlement` and `WorkstreamTaint`; the same stream turn on
  a `personal` box runs **one** `WorkstreamScopeEntitlement` read and no more, whatever the number
  of seams that consume the value (§13).
- **Ruling C costs no queries per row** (r4, §6.4): a sidebar of **N stream conversations** runs
  the **same number of queries** as a sidebar of N loose ones, asserted at N=1 and N=25 — the
  exact shape and the exact numbers `test_sidebar.py` already pins for the `settings_row` fix,
  extended to the `select_related("workstream")` this ruling makes necessary. The same assertion
  on the stream page's own list. Written as a **pinned count**, not an inequality, because the
  regression this guards against was found by measurement and would be invisible to a
  correctness test.

### 17.4 Taint

- A turn whose retrieval returned a document labelled `E` leaves exactly one `ConversationTaint`
  row, one `WorkstreamTaint` row and two audit rows; **the second such turn leaves none**.
- A turn that called no retrieval tool performs **zero** extra queries — the early return of
  §7.2, asserted with a query count.
- **A turn that retrieves `E` and then FAILS still taints** (M5): force the assistant call to
  raise after the tool turn commits, and assert the conversation and the stream both carry `E`
  while the assistant turn is `FAILED`. The test that would have passed against the earlier
  `_finish` placement and would have been asserting the wrong thing.
- **A turn cancelled between two tool turns** taints from the first (the same property, by a
  different route).
- **The union across two retrievals in one turn** is complete, and **a delegate's documents are
  in it** (§7.1's named risk).
- **Duplication does not launder** (ruling D, §5.4): duplicating a stream conversation produces a
  copy whose `workstream` is the source's and whose `ConversationTaint` rows match the source's,
  row for row — asserted for an ordinary owner **and** for an administrator under
  `admin_sees_content`, since `may_manage_conversation`'s first branch admits them. A loose
  conversation's duplicate carries its tags too.
- **The generalisation** (§7.4): a fake `ArtifactLabels("output", …)` registered in a test makes
  a turn that returned `output:12` taint, with **no change to any runtime module**.

### 17.5 Sharing

- Gate one refuses and **names the missing entitlements the sharer holds, and counts the rest**;
  both sentences of §12.1 asserted verbatim, for a sharer who holds all the tags and for one who
  holds some (ruling B).
- Gate two: a share that passed, then a revoked grant, then a **403** to the recipient and a
  **dormant marker with a reason** on the owner's list — and the stream **still listed** in the
  recipient's sidebar (§12.2).
- **The 403 page's BODY, not only its status** (M7, ruling E): it names **every** missing
  entitlement for the stream — the `disclose_all=True` mode — capped at five with "and N more",
  and names nothing belonging to any other stream. Asserted against a reader holding **none** of
  them, which is the only state the page is reachable in and the state ruling B's default would
  have rendered as a bare count.
- **The other two rendering sites take the DEFAULT mode** (§12.1's table): gate one's refusal and
  the owner's dormant marker each name what *their own* viewer holds and count the rest —
  asserted for a viewer who holds all the tags and for one who holds none, so the two modes are
  pinned apart rather than assumed distinct.
- Paired with a sweep asserting that **no route other than the dormant-share page** renders an
  entitlement name to a principal who neither owns nor holds it — the property §12.3 says this
  page is the first to break, pinned so it stays the only one.
- A tag added *after* a share was made makes it dormant with no write anywhere: dormancy is
  computed (§5.8).
- **Ruling C, both directions** (§12.4): a recipient starts a conversation in a shared stream and
  the **stream's owner reads it, consolidates it and deletes it**; the recipient does **not**
  read a thread another recipient started, and does not manage the owner's. Plus the consequence:
  `delete_workstream`'s count matches the number of conversations the owner can actually open.
- **Ruling F, the negative** (§12.4): a recipient posting to `chat-workstream-consolidate` gets
  **404** — for the owner's threads *and for their own* — and the Consolidate button does not
  render for them anywhere, the render-vs-gate pair. Asserted beside ruling C's positive, because
  the two are one sentence apart and an implementer reading only one of them would build the
  other wrongly.
- Every row of §12.4's "does not reach" table, one negative test each.
- A group share checked against the **group's** grants, and a member of that group who personally
  lacks the entitlement refused at read time (§12.1's two halves).

### 17.6 Consolidation

- The note is **one** document, `origin=notes`, contained, labelled with the conversation's tags.
- Re-consolidating **overwrites the same row** — same pk, same store directory — and re-ingests:
  the chunk count changes and the `document:<id>` reference in an old turn still resolves.
- **A re-consolidation whose re-ingest FAILS** (M13, §10.4): force `run_ingest_or_fail` to raise,
  then assert the row survives at `status=FAILED` with `status_detail` set, that the stream page
  renders the failed chip, that a **Re-consolidate** button is offered on it, and that pressing it
  restores a readable note — the whole repair path, without the library page and without an
  administrator.
- Consolidating conversation A leaves conversation B's note **byte-identical**.
- `uniq_notes_per_conversation` refuses a second note, asserted at the database.
- A cancelled-while-queued job clears the in-flight marker through `on_terminal`, asserted the
  way `on_ingest_terminal`'s test does.
- **The cap** (m5): a conversation longer than `CONSOLIDATION_MAX_TURNS` distils its most recent
  N turns and says so in the note's first line, rather than failing or silently truncating.
- The distillation prompt is the constant, asserted the way
  `tools/rag/tests/test_extract.py:45` asserts `EXTRACTION_PROMPT`.

### 17.7 The import law

Three new guards (§4.3), each with the anti-vacuous pin its siblings carry, plus the purity
assertion for `agents/contracts/workstreams.py`. Written **before** the modules they police, so
the first commit that would violate one fails — which is exactly why the `tools/`→`agents/`
allow-list must be the **three-name** set of §4.3 from the first line it is written: a two-name
version fails on `agents.entitlements`, which two production files already import, before
`agents/workstreams.py` exists at all.

### 17.8 Postures

The full suite runs under `FARABUNKER_TEST_POSTURE=personal` and `=enterprise`, as it already
does, and §13's table is asserted rather than described: for each mechanism, one test that it is
live in every posture, or one test that it is inert-and-consistent in `open`.

### 17.9 How to run it

Unchanged: `pytest` from the repository root against the branch's own preview database, per
`docs/DEV.md` and the parallel-session isolation rule. No new fixtures beyond a
`_workstream(...)` helper in `agents/tests/_helpers.py` and its `tools/rag` twin.

---

## 18. Migrations, in order

### WS-1

| # | Migration | Contents |
|---|---|---|
| 1 | `agents/migrations/0006_workstream.py` | `Workstream`, `WorkstreamScopeEntitlement`, `Conversation.workstream` + index `agents_conv_ws`, and the `choices`-only `AlterField` on `Share.target_type` (§5.7). Depends on `("agents", "0005_conversation_archived_at")`, `("identity", "0003_entitlement_and_grant")` and `swappable_dependency(settings.AUTH_USER_MODEL)` |
| 2 | `tools/rag/migrations/0016_document_workstream_and_pins.py` | `Document.workstream`, `Document.origin`, `WorkstreamPin` — **and `Document`'s first `Meta` ever**: the model has none today (`tools/rag/models.py:50ff`), so this migration carries `AlterModelOptions` plus `AddIndex(rag_document_ws)` rather than only `AddField`. Depends on `("rag", "0015_documententitlement")`, `("agents", "0006_workstream")` and `swappable_dependency(settings.AUTH_USER_MODEL)` |

### WS-2

| # | Migration | Contents |
|---|---|---|
| 3 | `agents/migrations/0007_workstream_taint.py` | `ConversationTaint`, `WorkstreamTaint`, `Conversation.consolidated_through_index`, `Conversation.consolidated_at`. Depends on `("agents", "0006_workstream")`, `("identity", "0003_entitlement_and_grant")` |
| 4 | `tools/rag/migrations/0017_document_notes.py` | `Document.notes_conversation_id` and `AddConstraint(uniq_notes_per_conversation)` — onto the `Meta` migration 2 created. Depends on `("rag", "0016_document_workstream_and_pins")` |

**Four migrations, and migration 2 is the repository's first `tools/` → `agents/` migration
dependency.** IA-2 introduced cross-app dependencies (`rag` → `identity`, `agents` → `identity`,
`inference` → `identity`), all of them pointing at the base column. This one points sideways, in
the direction the import law already permits, and ADR 0017 records it the way ADR 0016 recorded
its predecessor. The app labels are `agents` and `rag` (from `AgentsConfig` and `RagConfig`), not
the package paths, and both `makemigrations` and the dependency tuples name the labels.

**No data migration anywhere, and that is the load-bearing property of every column added here.**
Every new column is nullable or has a default that describes the existing rows correctly:
`workstream` null means "the universal library", which every existing document is; `origin`
defaults to `upload`, which every existing document is; `consolidated_*` null means "never", which
every existing conversation is. The chunk store needs no back-fill either, for the reason §8.3
gives: an absent `workstream` key is exactly what `IS_EMPTY` matches and exactly what every chunk
already looks like.

**No dependency change.** `requirements.txt` pins `Django>=5.1,<6.0`, which already covers the
`CheckConstraint(condition=…)` and `UniqueConstraint(Lower(…), …)` spellings used above.

**Gates on the migration set**, both halves: `manage.py migrate --plan` against a restored
production backup shows exactly these operations and no others; `manage.py makemigrations --check
--dry-run` exits 0 after each half; and the `PROTECT` behaviour of migration 1 and 2's two FKs is
exercised in both directions (a stream with a conversation refuses; an emptied one deletes).

---

## 19. Phasing: WS-1 and WS-2

Two plans, two PRs, each independently mergeable, deployable and green. The split is along the
line where the feature stops being *organisation* and starts being *disclosure*: WS-1 is worth
shipping to a single-operator box on its own, and WS-2 is what an organisation needs.

### 19.1 WS-1 — the stream, the wall, containment

**Content.** `Workstream`, `WorkstreamScopeEntitlement`, `WorkstreamPin`;
`Conversation.workstream` and `Document.workstream`/`origin`; `agents/workstreams.py` and
`agents/contracts/workstreams.py` with the panel registry; `tools/rag/workstreams.py` and its
registered panel; `visible_workstreams`, `create_workstream`, `stream_access` (the owner branch
only — its tag branch arrives with the tags); the wall at all three seams (§6) — including
**`models/registry/access.py::model_access_for`**, which gains a `wall: frozenset[int] =
frozenset()` parameter and is the one file this phase touches in a fourth column, and
**`agents/chat/pickers.py::chat_picker_options`**, which is the wall's render half;
`agents/runtime/preflight.py`'s two narrowed calls; `_visibility_filters`' `workstream` clause,
`readable_documents`/`DocumentVisibility.permits`' `workstream_id=` parameter (§8.1) and
`restamp_document_chunks`' second key and four-way branch (§8.3);
`duplicate_conversation`'s `workstream=` carry (ruling D); the upload
placement choice and the stream default (§9); instructions in `build_messages` (§11); the
sidebar's Workstreams section and the scoped sidebar; the stream page without its Tags and
Sharing sections; **five** of the seven new routes (`chat-workstream-share` and
`chat-workstream-consolidate` are WS-2); the entitlement cascade registration for the wall rows;
migrations 1–2; the three import-law guards.

**Done when:**

1. A stream is created from the sidebar, a chat is started **inside** it, and that chat appears
   in the stream's list and in the scoped sidebar and **not** in the unscoped one's stream
   section — verified in a browser, not only in tests.
2. A document uploaded from inside a stream with **This workstream only** is present in that
   stream's document panel and its turns' retrieval, and **absent** from the library page, the
   Ask page, Search, and a second stream — for an account holding every one of its labels.
2b. **That same document OPENS from inside its stream and 404s from outside it.** Clicking it in
   the stream's Documents panel serves the file and the transcript; the same `rag-document-file`
   URL answers **404** to a signed-in account outside the stream, and **404** to an administrator
   with `admin_sees_content` **off** who reached the row through the library listing — a listed
   row is not a key to the bytes. With the content setting **on**, that administrator gets
   **200**, exactly as they do for every other document (ruling G, §23.G, and §8.1's own
   admin-cleanup argument).
3. The **same upload with Universal** is in the library and in the stream's corpus both.
4. An in-stream upload with **neither radio picked** is refused with a 400 naming the field, and
   ticking "use this choice for all future uploads" makes the next upload show the
   "placed per stream default — change" line instead of the radios.
5. A stream walled to `E1` retrieves nothing labelled `E2` and nothing **unlabelled** from the
   universal library, still retrieves its own contained documents and its pins, and a tool
   labelled `E2` is absent from its turns — the last asserted at the planner **and** the loop.
6. **No route produces a loose copy of stream work, and duplication launders nothing**
   (ruling D). `Conversation.workstream` is written once at creation and by no route afterwards;
   **duplicating** a stream conversation produces a copy **in the same stream** carrying the same
   taint tags, row for row — verified from the ⋯ menu in a browser, and verified again as an
   administrator under `admin_sees_content` against somebody else's stream conversation. The
   earlier phrasing of this criterion — "none of the five menu actions moves a thread" — was true
   and materially misleading: duplicate does not *move* a thread, it would have *forked it out*.
7. Deleting a stream that still holds conversations or documents is refused with a sentence
   naming both counts.
8. The stream's instructions appear in the turn's system message under their labelled header,
   and an agent with a blank system prompt still gets one.
9. An **open** box (ruling A): streams, containment, pinning, uploads and instructions all work;
   the Scope, Tags and Sharing sections do not render; the query-count test shows **zero**
   permission queries on the stream page and the stream turn; and a stream **carrying wall rows
   written under `enterprise`** starts its turns, offers every model in the picker and refuses
   nothing. Switching that box to `personal` makes the same rows bind — one
   `WorkstreamScopeEntitlement` read per stream turn, no edit, no migration — and switching back
   makes them dormant again.
10. Both posture sweeps green; the route matrix green in all three postures for the five new
    routes; `makemigrations --check` clean.
11. The ladder to fresh pixels.

### 19.2 WS-2 — taint, sharing, consolidation

**Content.** `ConversationTaint`, `WorkstreamTaint` and `Conversation.consolidated_*`;
`Document.notes_conversation_id`; `agents/runtime/taint.py`, the `ArtifactLabels` registry and
`tools.rag.labels.entitlement_ids_for`; the tool turn's stamp inside the `transaction.atomic()`
it already has (§7.2 — **`_finish` is NOT touched**, and an earlier draft of this list said it
was); `stream_access`'s tag branch and
the §12.3 403 page; `share_workstream`/`revoke_workstream_share` and the share panel with dormant
markers; `identity/access.py`'s `entitlement_ids_for_subject` and `entitlement_names`;
`visible_conversations`' and `may_post_to`'s stream-share clause; `tools/rag/distil.py` with
`DISTILLATION_PROMPT`; the `rag.consolidate` job kind with its planner, handler, summarizer and
`on_terminal`; `record_consolidation`, `taint_ids_for_conversation` and `staleness_for`;
`chat-workstream-share` and `chat-workstream-consolidate`; the taint half of the entitlement
cascade; the seventeen audit actions; `name_for_viewer` (§12.1) and the two `identity/access.py`
additions; `visible_conversations`' and `may_post_to`'s **two** new clauses and
`may_manage_conversation`'s stream-owner branch (ruling C) and the `select_related("workstream")`
that keeps it flat (§6.4); `NOTES_DIR = DATA_DIR / "notes"` in `config/settings.py`;
migrations 3–4.

**Done when:**

1. A turn whose retrieval returned a document labelled `E` leaves one conversation tag, one
   stream tag and two audit rows naming the causing turn; the next such turn leaves none and
   costs no extra queries.
2. A turn that retrieved nothing writes nothing and runs the same number of queries it ran before
   this phase.
2b. **A turn that retrieves `E` and then FAILS still tags the conversation and the stream** (§7.2).
   Forced in a test and confirmed on the box: kill the engine mid-turn after a retrieval, and the
   thread shows the failed turn *and* the stream shows the tag. The share gates never see a stream
   whose material outran its tags.
3. Sharing that stream to an account without `E` is **refused**; the message names `E` when the
   sharer holds `E` and says *"and 1 more entitlement you don't hold"* when they do not
   (ruling B, §12.1) — both sentences seen in a browser. Granting `E` and retrying succeeds; the
   recipient can then read the stream, open its conversations and post a turn into one.
3b. **A recipient's turn tags the owner's stream** (ruling B), the tag is audited with the
   **recipient** as its acting principal, and the owner — who may hold no grant for it — is not
   locked out of their own stream by it (§12.2).
3b2. **Ruling C is flat** (r4): `/chat/` scoped to a stream with **25** conversations issues the
   same number of queries as `/chat/` with 25 loose ones, and the same on the stream page —
   pinned as an equality, since `may_manage_conversation`'s new branch is called once per row and
   `agents/chat/sidebar.py`'s own docstring records the last per-row read costing 47 → 95 queries
   at 25 rows.
3c. **Rulings C and F, in a browser:** the recipient starts a new conversation in the shared
   stream; the **owner** sees it on the stream page, consolidates it, and deletes it. The
   **negative, asserted in the same pass**: the recipient sees no Consolidate button on any
   thread — theirs included — no staleness hints at all, cannot manage the owner's threads, and
   gets a 404 posting to `chat-workstream-consolidate` directly. And `delete_workstream`'s
   refusal counts only rows the owner can open.
4. Revoking `E` from that recipient makes the share **dormant**: the stream is still listed in
   their sidebar, opening it gives the **403 page naming `E`**, and the owner's share list says
   *"Dormant — that account no longer holds E."* Deleting the share row makes the same URL a
   **404** — the pair asserted together (§12.3).
5. Every row of §12.4's "does not reach" table refuses with a 404 for a recipient, and the
   controls for them do not render.
6. Consolidating a stream conversation produces **one** contained note document titled
   `Notes — <title>`, `origin=notes`, labelled with the conversation's tags, retrievable by that
   stream's next turn and by no other stream.
7. Re-consolidating **overwrites the same row** — same pk, same store directory — and an old
   turn's `document:<id>` link still resolves; conversation B's note is byte-identical
   throughout.
7b. **A re-consolidation whose re-ingest fails is visible and repairable from the stream page**
   (§10.4, §24 concern 5): the note shows a `FAILED` chip, the **Re-consolidate** button is
   offered on it, and pressing it restores a readable note — no library page, no administrator.
8. The staleness hint reads "Not consolidated", then "Up to date", then "N turns since last
   consolidated" after another turn.
9. Cancelling a queued consolidation from the queue page clears the in-flight marker and the
   button is offered again.
10. Deleting an entitlement removes its wall rows and its taint rows, **writes one
    `WORKSTREAM_UNTAINTED`/`CONVERSATION_UNTAINTED` row per removed tag**, the delete confirmation
    names their count first, and a share refused for that entitlement now succeeds.
11. The **generalisation test** (§7.4) passes with a fake resolver and no runtime edit.
12. The full route matrix — three postures, four principals, both `admin_sees_content` settings —
    green, including the 403/404 pair **and the 403 page's body**: it names entitlements only
    under §12.3's three fences, and the platform-wide sweep confirms no other route discloses an
    entitlement name to a principal who neither owns nor holds it (§17.5).
13. The ladder to fresh pixels.

### 19.3 ADR 0017

Written last, after both halves are merged, **against the SHA the amendments were written
against — `d564348` — and against whatever `HEAD` is when it is drafted, stated in its own
header** the way ADR 0016 states its. Recording: the Workstream as an architectural
concept; the placement decision and the two cross-column seams (§4); the first `tools/` →
`agents/` migration dependency; the corpus composition law (§6.1) as the platform's standing
answer to "what does a scoped area contain"; the taint mechanism and its `ArtifactLabels`
extension point; the one scoped exception to the 404 house rule (§12.3), which is a rule
ADR 0016 stated absolutely and this phase qualifies; and this phase's own named gaps (§24).

---

## 20. Documentation

| Document | Change |
|---|---|
| `docs/ROADMAP.md` | a Workstreams entry under Phase 1.6, marked shipped in two halves when both land, with the deferred list of §22 named as such rather than left implicit |
| `docs/adr/0017-workstreams.md` | new, per §19.3 |
| `docs/adr/0016-identity-and-entitlements.md` | an amendment noting that the 404 house rule now has one scoped exception and where it is fenced (§12.3), and that `Share` has a fifth target |
| `docs/adr/0015-agent-layer-and-tool-contract.md` | an amendment noting `ToolContext.stream` and that a delegate inherits it |
| `agents/README.md` | the Workstream section: the two tables, the seam module and what may import it, the panel registry, and the taint stamp's place in the turn |
| `tools/rag/README.md` | containment versus pinning; the `workstream` chunk-metadata key and its one writer; `origin`; the note document and its overwrite path |
| `identity/README.md` | the two new `identity.access` functions (`entitlement_ids_for_subject`, `entitlement_names`), the **seventeen** new audit actions, the note that a workstream is **not** an identity concept even though it carries entitlement sets, and the one route that now names an entitlement to a non-admin (§12.3) |
| `models/README.md` | `model_access_for`'s new `wall` parameter — the one change this phase makes in the `models/` column, and the reason a reader of that column finds a workstream concept in it at all (§6.3) |
| `docs/OPERATIONS.md` | backups now contain contained documents that no non-admin library page lists; the new `DATA_DIR/notes/` directory and why the watcher must never be pointed at it; and the fact that deleting an entitlement un-taints (§8.4) |
| `docs/EXTENDING.md` | how a column registers a workstream panel, and how a labelled artifact kind joins the taint stamp |

---

## 21. Non-goals

Named so they read as scoped-out rather than forgotten.

1. **Nested workstreams.** A stream has no parent and no children. Hierarchy is a second
   containment rule and every query in §6.1 would grow a recursive term.
2. **A document in two streams.** Owner decision 4. One row, one home. The answer to "it belongs
   to both" is a universal document pinned into both.
3. **Moving a conversation between streams.** Owner decision 2, and deferred with a designed
   shape in §22 — not merely unbuilt, but deliberately absent from v1 because the honest version
   needs a taint-import rule.
4. **A stream as a permission.** A stream never widens what anybody may read (§6.1). If it did,
   it would be a second grant mechanism beside entitlements, which is already a non-goal of the
   identity spec.
5. **Prompt-level enforcement of anything.** §6.5.
6. **A per-stream model or agent binding.** §22.
7. **Automatic consolidation.** Owner decision 6 says manual only; §22 defers the triggers.
8. **A stream digest, or retrieval over full transcripts.** §22, both.
9. **Multi-tenancy.** One box is one organisation; a stream partitions work, not machines.
10. **A feature flag.** `FARABUNKER_FEATURES` has exactly one job — gate a feature app's role
    registration and its URL mount — and Workstreams is neither a feature app nor a new mount.
    It ships on, like IA-1 and IA-2 did, and its invisibility on a box that never creates one is
    a single empty sidebar section. Author decision 10.
11. **Untainting.** §22, and §24's third concern.
12. **A separate notes surface.** A note is an ordinary `Document` (§10.4). There is no notes
    page, no notes model and no notes URL.

---

## 22. Deferred, with the hook each relies on

| Item | The hook it lands on |
|---|---|
| **Full-transcript RAG** — retrieving over conversation turns rather than over distilled notes | `Turn` rows already exist and are already ordered; the missing piece is a chunk source that is not a file on disk, which every current ingest path assumes (`stage_document` takes a `path`). The note document is the v1 answer and the hook is `Document.origin`: a second origin value is where transcript-derived chunks would land |
| **A stream digest** synthesised from its notes | `Document.origin == notes` and `Document.workstream` are exactly the query a digest job would run; `rag.consolidate` is the job-kind shape it would copy, and `DISTILLATION_PROMPT`'s file is where its own constant would live |
| **Automatic consolidation triggers** | `Conversation.consolidated_through_index` and `staleness_for` already compute the number a threshold would compare against; the trigger is a check in `_finish` and an `enqueue`, and the reason it is not here is that an LLM call nobody asked for is a surprise on somebody's queue |
| **Move-to-stream**, with taint import on arrival | `Conversation.workstream` is already nullable and already indexed. **The designed shape, recorded so the deferral is a decision and not a gap:** a move is permitted only into a stream whose taint set is a **superset** of the conversation's, or it imports the conversation's tags into the destination in the same transaction and audits each with the move as the cause. The reason it is not in v1 is that the same rule must also decide what happens to the conversation's *contained* documents, and "the note follows, the uploads do not" is a rule that wants a real use case before it is written |
| **Taint removal / untaint** | `ConversationTaint` and `WorkstreamTaint` are rows with a `first_turn`, so an untaint has something to name, and `WORKSTREAM_UNTAINTED`/`CONVERSATION_UNTAINTED` are already in the audit vocabulary (§16.1), written today by the entitlement cascade. What is missing is an owner-only route and a decision about whether removing a stream tag that a conversation still carries is coherent — which it is not, so the real shape is "untaint the conversation, then recompute the stream", and recompute is the one operation §5.3 deliberately does not have |
| **Recipient-initiated consolidation** | ruling F (§23.F) keeps it owner-only in v1 because it writes a stream-contained document, which is a stream mutation and belongs with the other three owner decision 7 names. The hook is `may_manage_conversation`'s branch structure: a recipient already *manages* the threads they started, so the change is one predicate in `chat-workstream-consolidate` plus a decision about whose taint set the note inherits when the consolidator is not the stream's owner — which is the question that makes it a design item rather than a line |
| **Re-share, and recipient mutation rights** | `Share.level`'s vocabulary and `_may_share`'s predicate. A third level (`manage`) is the shape, and it is deferred because two levels are already enough to get wrong |
| **Stream templates** — a new stream pre-loaded with instructions, a wall and pins | `create_workstream` is one function and `agents/defaults.py`'s catalogue-not-deploy-step pattern (`DEFAULT_AGENTS`, `manage.py install_defaults`) is the shape a template catalogue would copy exactly |
| **Per-stream agent defaults and model bindings** | `Workstream` is a row with room for a `default_agent` FK and an `llm_role` override; `resolve_chat` and `preflight_turn` are the two seams that would read them. Deferred because a stream that silently changes which model answers is a surprise, and the per-turn picker already exists |
| **Admin-provisioned organisation streams** — a stream owned by the box, granted by entitlement rather than shared row by row | `owner_kind`/`owner_key` already admit a service-owned row, and `visible_workstreams` would gain an entitlement clause shaped exactly like `label_permitted_q`. Deferred because it is a different sharing model, not a bigger one |
| **Entitlement-labelled non-document artifacts** (generated images, tool outputs) joining the taint stamp | `ArtifactLabels` (§7.2). One registration in the owning column's `AppConfig.ready()`, and **no change to any runtime module** — pinned by a test today (§17.4) so the claim is not a hope |
| **Conversation compaction (context-limit management)** | `tools/rag/distil.py`'s `DISTILLATION_PROMPT` and `distil_conversation`, which are written for two consumers from the start (§10.3). A future chat-runtime feature — **not** Workstreams, and not this spec — that tracks a conversation's token usage against its bound model's context limit and compacts automatically near the ceiling, or on a manual trigger. It differs from consolidation in what it does with the result: consolidation produces a **retrievable note and leaves the chat intact**, while compaction **replaces the live prompt history** with the summary so the conversation can continue. Its other hooks are `prompt.history_messages`' `before_index` parameter and `agents/limits.py::HISTORY_TURNS`, which is today's crude stand-in for a context budget |
| **Operator-editable prompt constants** | the named deferral `EXTRACTION_PROMPT` already carries and `DISTILLATION_PROMPT` repeats: a `RagSettings` field, decided once for both rather than twice |

**Tracked separately by the owner, and deliberately not designed here:** editing a past prompt
and restarting a conversation from that point. It is a chat-runtime feature with its own shape
(it rewrites history rather than summarising it), it is on the owner's own list, and naming it
here is only so a reader does not mistake its absence for an oversight.

---

## 23. Decisions the author made

Everything the brainstorm left open, decided and recorded. **Twenty-seven numbered author
decisions, plus seven lettered orchestrator rulings.** Ten carry a **flagged for owner** marker —
§23.2, §23.5, §23.8 and all seven rulings — being the places where this spec and something said in
the brainstorm do not sit flush, or where a consequence follows that nobody stated out loud.

**The seven rulings (A–G) were taken by the orchestrator on the 2026-09-03 adversarial review**,
each answering a finding that needed a decision rather than a correction: **A–D** from its first
pass, and **E–G** from the round-2 re-check, which found that three of the first pass's own fixes
had created internal contradictions — two of them already embedded in done-when criteria, so they
would have surfaced as unsignable acceptance rather than as latent bugs. They are listed first,
lettered rather than numbered, so a reader can see at a glance which calls came from outside the
authoring of this document and which the author made. Every one of them is **flagged for owner**:
they change behaviour the owner has not yet seen described.

### Ruling A — the wall is INERT when the posture is `open`

**Flagged for owner.** `_wall_for(conversation)` returns `frozenset()` without reading
`WorkstreamScopeEntitlement` when `identity.access.accounts_on()` is False. The Scope editor is
not rendered on an open box. Wall rows written under `enterprise` are kept **dormant, not
deleted**, across a switch to `open`, and bind again the moment the posture returns.

**The finding it answers.** §6.3's `and not wall` is a deliberate bypass of the `sees_all_content`
short-circuit, and an earlier draft justified its safety on an open box with "on an open box
nothing is labelled" — which is **false**. `ToolEntitlement`, `DocumentEntitlement` and the
model-set rows all survive a posture switch to `open`, and `agents/labels.py:20::
tool_entitlement_ids` reads them with no posture branch. So an open box with any wall row would
have produced `ToolAccess(required=<non-empty>, held=frozenset(), unrestricted=False)`: every
labelled tool dropped, every labelled model set refused at preflight, the stream's turns unable
to start — and, because §15.3 hid the Scope editor whenever the principal held no entitlements
(always, on an open box), **no page that could clear it**. A bricked stream on a box with no
accounts, refused with a sentence §24 concern 1 already calls the wrong reason.

**Why inert rather than an escape hatch.** Rendering the editor "whenever wall rows exist" would
have unbricked it and left a security control that binds where the platform has no principals to
bind — the wall means "narrow to these entitlements", and where entitlements are off the sentence
has no referent. Making it inert also **restores two claims this document makes elsewhere**:
§13's "an open box never runs a permission query" (deciding `not wall` would otherwise require
reading the wall table on every stream turn in every posture) and §19.1's done-when 9. §13, §6.3
and §15.3 item 3 carry the mechanics; §19.1 done-when 9 and §17.3 assert both directions of the
switch.

### Ruling B — taint accumulates from ANY participant, and the MESSAGES are what change

**Flagged for owner.** A share recipient's retrieval, run under the recipient's own grants, tags
the owner's stream exactly as the owner's own would. Security is the point; authorship is
irrelevant. What changes instead is the messaging premise: every refusal and dormancy message
names only the tagged entitlements **the message's own viewer holds** and summarises the rest as
a count — *"…and 1 more you don't hold"*. The audit rows record the acting principal per tag, so
"who brought this in" has an answer for whoever may read the trail.

**The finding it answers.** Three of this document's load-bearing sentences were false in the
share case, and all three are now rewritten: §12.2's *"the owner is never locked out… they caused
them"* (the owner did not cause a recipient's tag); §12.1's *"the SHARER HOLDS THEM"* (the sharer
may hold none of them); and §7.5's claim that a widened wall is the only way the wall and the tag
set diverge. The path is inherited rather than invented — IA-2's `share_conversation` +
`may_post_to` already let a `use` recipient's retrieval into the owner's thread — but Workstreams
is the phase that builds a gate whose whole purpose is "who may see what has been in here", and
building it in one direction only would have left the other unstated.

**Why not narrow the recipient to `held(recipient) ∩ held(owner)`.** That was the cheap
alternative and it is the wrong shape: it would make a share *reduce* what a recipient may read
below what their own grants allow, on a surface that never explains why, and it would make the
owner's grant set a silent second wall nobody set. The material a recipient retrieves is material
they are entitled to; the honest answer is to record that it is now in the stream and to gate
onward disclosure on it, which is what taint already does. §24 concern 6 records what remains:
the owner can read it.

### Ruling C — a stream's owner reads and manages every conversation in their stream

**Flagged for owner.** Including conversations a share recipient started there. The stream is the
owner's space and its contents cannot be opaque to them, so `visible_conversations` and
`may_post_to` gain an owner clause beside the recipient clause, and `may_manage_conversation`
gains a stream-owner branch — consolidate and delete included. Recipients are told, on the stream
page above the composer: *"This is somebody else's workstream — its owner can read the
conversations you start here."*

**This widens what the OWNER may do and nothing about what a recipient may do.** Consolidation
stays owner-only (ruling F, §23.F); §10.1's and §14's owner-or-`sees_all_content` predicate is
unchanged, and what ruling C changes is only which conversations that predicate can now find.

**The finding it answers.** `create_conversation` stamps `**owner_fields(principal)`, so a
recipient's new thread is owned by the **recipient**, and the owner's two legs in
`visible_conversations` (`owned_rows_q`, and shares *they* hold) match neither. The owner would
never have seen it: absent from the stream page, unreachable by §10.1's owner-only consolidate,
and counted by §8.1's `PROTECT` refusal as a row the owner could neither open nor delete — an
undeletable stream with an unactionable message — while §7.3 still unioned its taint upward and
refused the owner's future shares for it.

**Why disclose rather than restrict.** The alternative was to keep the thread private to its
creator and exempt it from the stream's taint, which would make a stream's tag set understate
what is in it — the same failure ruling B rejects, by a different route. A visible sentence on the
page is a smaller cost than a gate that lies.

### Ruling D — duplication copies the taint and stays in the stream

**Flagged for owner.** `duplicate_conversation` carries `workstream=conversation.workstream` and
copies the source's `ConversationTaint` rows, **always** — for loose conversations as well as
stream ones. v1 offers no loose copy of a stream conversation.

**The finding it answers.** `agents/visibility.py:341-403` builds the copy with no `workstream`
and no taint rows while copying `text`, `data` and `artifacts` — the quoted document text and the
`document:<id>` references. One ⋯-menu click would have produced a loose thread holding labelled
material with zero tags, outside every gate this phase builds and shareable through
`chat-conversation-share`, which §5.3 says this phase does not change. §12's two gates would have
been one click from decorative, and an administrator under `admin_sees_content` could have done
it to anybody's stream conversation. §19.1's done-when 6 said "none of the five menu actions moves
a thread", which was true and materially misleading: duplicate does not move a thread, it forks
it out.

**Why copy rather than refuse.** Refusing duplication of a stream conversation was the offered
alternative and it removes a useful action to fix a bookkeeping omission. Carrying the stream
identity is consistent with immutability rather than an exception to it — nothing moves, and the
new row's `workstream` is stamped once at creation like every other row's — and copying the tags
means no thread anywhere ever holds labelled material with no tags, which is a property worth
having for loose conversations too.

### Ruling E — gate two names every missing entitlement; gate one and the owner's marker do not

**Flagged for owner.** `name_for_viewer` (§12.1) grows a `disclose_all` switch with exactly one
caller. **Gate one** (the share refusal, read by the *sharer*) and the **owner's dormant marker**
(read by the *owner*) use the default: name what this viewer holds, count the rest — ruling B,
unchanged. **Gate two** (the dormant-share 403, read by the *invited recipient*) passes
`disclose_all=True` and names every missing entitlement, capped at five.

**The finding it answers.** Applying ruling B's default at gate two names **nothing, always**: the
viewer there *is* the recipient, `missing = taint_ids - held(recipient)` by construction, so
`missing_ids & held(viewer)` is empty by definition. The page would have read *"and N entitlements
you don't hold"* — the exact inverse of owner decision 8, whose own words are *"contains material
from entitlements you don't hold: X, Y"*. The first amendment half-noticed this and diverged four
ways: §12.3's page text named them, its fence 2 conceded the emptiness and then invented an
**undefined second rule** ("the names of those tied to them by a live share") that no function
implemented, §17.5 tested that undefined rule, and §19.2's done-when 4 required naming. An
acceptance criterion and its function contradicting each other is unsignable, and the failure
direction is the bad one: an implementer building from §12.1 would have silently dropped owner
decision 8.

**Why the owner's earlier decision governs here specifically.** Gate two is fenced by the thing
gate one is not — a **live `Share` row on this stream** — which is precisely the scoping owner
decision 8 relied on when they asked for the reader to be told *"what entitlement mismatch is
preventing them from seeing it"*. The invited reader already knows the stream exists; somebody
gave it to them. Ruling B's conservatism is right where the reader has no such standing (a sharer
enumerating a recipient's gaps) and wrong where the whole point is to tell somebody why a door
they were handed a key to no longer opens. **One rule per site, three sites, stated in one table**
(§12.1) — and the undefined second rule is deleted, not reworded.

### Ruling F — consolidation stays owner-only

**Flagged for owner.** A share recipient may not consolidate anything, including a thread they
started themselves. Owner decision 7 is unamended: recipients read and converse. §12.4's row is
restored to the mutation list, `chat-workstream-consolidate` stays owner-or-`sees_all_content`
(§10.1, §14), and **staleness hints render for the owner only** (§10.5, §15.3) — a hint is a
prompt to press a button, and prompting somebody toward a 404 is the render-vs-gate pair broken
on the most visible surface there is.

**The finding it answers.** The first amendment's §12.4 row granted a recipient the right to
consolidate "the threads they started", which is a **stream mutation**: it writes a contained
`Document` into somebody else's stream, labelled from that stream's taint set, ingested on the
owner's box. §10.1, §14 and ruling C's own text all still said owner-only, so the spec answered
one access-control question three times, two ways. That was drift, not a decision: ruling C exists
to make the **owner** able to reach a recipient's thread — because a stream cannot be opaque to
the person who owns it — and nothing about it implies the reverse.

**Why conservative.** Owner decision 7 lists re-sharing, wall edits and pin changes as owner-only;
consolidation is the fourth stream mutation and nobody ruled it out of that set. A recipient whose
thread wants a note asks the owner, who can now do it (ruling C). The recipient-initiated variant
is recorded in §22 as deferred, with the hook it lands on, rather than shipped by accident.

### Ruling G — containment fences the corpus, not the bytes

**Flagged for owner.** An administrator with `admin_sees_content` **on** opens a contained
document at `rag-document-file`/`rag-document-transcript` with a **200**, exactly as they open any
other document. §17.2 cell 5 and §19.1 done-when 2b are rewritten to assert that; the 404
assertions for non-admin outsiders, and for an administrator with the setting **off**, stand.

**The finding it answers.** The first amendment's test cell and done-when required a **404** that
§8.1's own mechanism cannot produce: `visible_workstreams`' first branch admits `sees_all_content`
to every stream, `stream_access` answers `ok=True` with no tag check, so `workstream_scope`
returns a scope rather than `None` and `readable_documents` takes its `unrestricted` branch. The
criterion was unpassable without inventing a second, undocumented contract for `workstream_scope`
(a stream an administrator may *administer* but not be *in*), which would then have had to be
checked against every other caller.

**And the mechanism is the right one, not merely the actual one.** It agrees with §8.1's own
admin-cleanup argument three paragraphs above the cell (*"a document an administrator cannot see
the existence of is a document nobody can clean up"*) and with IA-2's settled rule that
`sees_all_content` reads bytes. §17.2 **cell 4** is the property containment actually owns and it
is untouched: the same administrator still does not *retrieve* another stream's contained document
into this stream's turn. Corpus and bytes are two questions, and only the first is containment's.

### The twenty-seven author decisions

1. **The `Workstream` tables live in `agents/`, not in `identity/` and not in a sixth column.**
   §4.1. The import law permits `tools/` → `agents.contracts` and forbids `agents/` → `tools/`
   outright, so `agents/` is the only column from which the sidebar, the stream page, the prompt
   injection and conversation creation can all reach a stream by ordinary import. The two
   rejected alternatives, recorded rather than dropped: `identity/` would need a fifth sanctioned
   seam **and** a write path from the runtime into identity's private models; a **sixth top-level
   column** would need every one of those crossings anyway plus a new entry in the import law's
   own table. The one crossing this placement cannot make — the stream page displaying documents
   — is the one the platform already has three registries for.

2. **The corpus formula is `((universal ∩ wall) ∪ pinned ∪ contained) ∩ readable_by(P)`.** §6.1.
   **Flagged for owner.** The brainstorm's own words were
   `(universal ∩ grants ∩ wall) ∪ pinned ∪ contained`, which read strictly would put pins and
   contained documents **outside** the reader's grants — making a pin a grant, and making §12's
   two gates decorative. The same sentence says pins are "docs the user can already read", so
   the two halves cannot both hold and the security-preserving one is the one worth keeping. The
   wall stays inside the parentheses, where it narrows the universal leg only, because a wall is
   a convenience its owner set and a pin is a deliberate admission.

3. **`Turn.artifacts` on the TOOL TURN is the taint source of truth**, not "citation records"
   and not the assistant turn. §7.1, §7.2. There is no citation table in the tree; there is
   `Turn.data["citations"]` per tool turn and `Turn.artifacts` on the tool turn, accumulated onto
   the assistant turn. `artifacts` is already deduped and already guarded against a non-decimal
   id, and the tool turn's own `create()` already writes it — so the stamp rides a write that
   already happens. **On the tool turn, because a tool turn commits and an assistant turn may
   never arrive:** `_finish` runs only on the success path, so stamping there would leave
   retrieved material committed in a thread with no tag on any turn that failed after retrieval.
   A consequence worth stating: `_finish` therefore grows **no** transaction, and this document's
   first draft said it did.

4. **The wall's reverse accessor is `scope_entitlements`, never `entitlement_labels`.** §5.2. The
   four existing label tables share that name so `label_permitted_q` can be one function for two
   models; a wall answers a different question, and sharing the name would let a future call
   compile against a stream and answer wrongly in silence.

5. **A non-empty wall excludes UNLABELLED universal documents.** §6.1. **Flagged for owner.**
   Nobody said this out loud and it follows from "narrow this stream to material under these
   entitlements": a document under no entitlement is under none of them. The alternative — a wall
   that lets unlabelled material through — would make a wall almost useless on a box whose
   library is mostly unlabelled, which is every box before somebody labels it. The stream page
   states the consequence in one line beside the wall editor rather than leaving it to be
   discovered.

6. **`WorkstreamScope.pinned_file_ids` is filled by `tools/rag`, not by `agents`.** §6.2. The
   stream half of the value (id, wall, upload default, may-upload) crosses the seam from
   `agents/workstreams.py`; `tools/rag/workstreams.py::scope_with_pins` returns the same frozen
   value with the pins added, and is the only function that does. The pin table is a `tools/rag`
   table and the column that owns it is the column that reads it.

7. **`MAX_PINS_PER_STREAM = 200`**, refused with an honest message naming the cap. §6.2. A pin
   set becomes an `ANY` array in every query the stream runs, so it is unbounded work per turn.
   A cap, not a paginator, for the reason `SIDEBAR_LIMIT = 30` is one: this is a single-operator
   box and a working set is not an archive.

8. **`tool_access_for` returns `UNRESTRICTED_TOOL_ACCESS` only when there is no wall.** §6.3.
   **Flagged for owner.** The consequence is that setting a wall narrows the tools available to
   the person who set it, including an administrator with `admin_sees_content` on. A wall is a
   scope somebody chose, not a permission check on somebody else, so choosing it means choosing
   it for yourself. The alternative — a wall that binds documents but not tools for privileged
   readers — would make the wall's meaning depend on who is looking, which is exactly what a
   scope must not do. **Bounded by ruling A**: "every principal on an open box" is no longer part
   of this decision, because on an open box the wall is not read at all.

9. **A LOOSE conversation is tainted too.** §5.3, §7.3. `ConversationTaint` hangs off the
   conversation, not the stream, so the stamp has no "am I in a stream" branch and a loose
   conversation accumulates tags nothing reads in v1. That is what makes conversation-level share
   gating a later change with no back-fill, and it costs one row per first-sighting on threads
   that retrieve labelled material.

10. **No feature flag.** §21.10. `FARABUNKER_FEATURES` has exactly one documented job and this is
    not it. Workstreams ships on, and its invisibility on a box that never creates one is one
    empty sidebar section.

11. **One `chat-workstream-edit` route with an `action` field, not seven routes.** §14. Seven
    owner-only mutations of one row share one predicate and one 404 rule; seven routes are seven
    chances for one to drift, and the drift shows as a control that 404s. `_WORKSTREAM_ACTIONS`
    is `identity-entitlement-edit`'s `_ENTITLEMENT_ACTIONS` and `may_manage_conversation`'s
    one-predicate-for-five ruling, applied a third time.

12. **`origin` is its own column, not a `method` value inside `extraction`.** §5.5. `extraction`
    snapshots *what produced the text*; `origin` records *what put the row in the library*. The
    note's `extraction` is still written, with `method="distillation"`, which
    `extraction_summary` already degrades to `"Processed"` for an unknown method.

13. **The "a contained document may not be pinned" rule is a function refusal, not a check
    constraint.** §5.6. The condition lives on a joined table and Postgres will not accept it.
    One enforcement point, one named refusal, one test that tries it — recorded here so the
    absence reads as a decision rather than an omission.

14. **Deleting an entitlement un-taints.** §8.4. One `EntitlementCascade` registration removes
    the wall rows and the taint rows; `unlabel_all_for_entitlement` makes the documents
    unlabelled in the same `transaction.atomic()`, so a stream can never be tagged with an
    entitlement its documents have lost. It is the one path by which a tag disappears in v1, and
    it is not an exception to "additive only" so much as what deleting the label *means*.

15. **Both containment foreign keys are `PROTECT`, and the stream delete counts first.** §5.4,
    §5.5, §8.1. `CASCADE` would make one button delete somebody's documents and conversations,
    which is the most destructive gesture on this surface hiding behind the least alarming
    control. `delete_workstream` names both counts and refuses, which is `delete_entitlement`'s
    own count-then-name shape.

16. **A workstream share is `Level.USE`, or it is refused by name.** §5.7. Owner decision 7 says
    a recipient reads **and** converses; storing a `view` level no reader honours would be a
    column value with two meanings.

17. **Neither placement radio is preselected, and the view enforces it with a 400.** §9.1. HTML's
    `required` is the render half and the view is the gate half. This is the one field where a
    server-side fallback would silently undo an owner decision, so there is none.

18. **`rag.consolidate` is registered by `tools/rag`, not by `agents`.** §10.2. The handler needs
    the transcript (an `agents` row), a model call, and a document write plus a re-ingest. Only
    `tools/rag` can reach both ends, through the permitted direction; `agents/` cannot reach
    documents at all.

19. **The note's path is deterministic, so re-consolidation is an overwrite the existing ingest
    path already performs.** §10.4, which is where the path itself is stated and argued —
    `<DATA_DIR>/notes/<conversation_id>.md`, under `NOTES_DIR` and deliberately **not** under
    `INGEST_INBOX_DIR`, so the watcher cannot stage a note a second time as a universal document.
    An earlier version of this item carried the inbox spelling, which §10.4 argues at length is
    the wrong one; the path lives in one place now and this item cites it. The mechanism is
    unchanged and independent of the directory: `stage_document` dedups on `original_path`, sees
    a changed hash, calls `_delete_existing_data` and re-ingests in place — same row, same id,
    same store directory, and every old `document:<id>` reference still resolves. No new overwrite
    code exists anywhere, which is also what makes §10.4's destroy-then-recreate window invisible
    unless it is named — and §24 concern 5 names it.

20. **Staleness is an index difference, and it is a hint.** §10.5. `_finish` leaves deliberate
    gaps in `Turn.index`, so `latest - consolidated_through` can exceed the number of turns
    actually added. An over-estimate of "how much has happened" is the right direction for a hint
    to err in, and the alternative is a `COUNT(*)` per conversation per page render.

21. **A delegate does not inherit the stream's instructions prose, and does inherit its scope.**
    §11, §12.4. `delegate.py` deliberately builds a fresh message list, and this spec does not
    change that; the enforcement travels through `ToolContext.stream` regardless, so a delegate
    is not a way around a wall even though it is not told about one. §24's second concern records
    what this costs.

22. **Two new functions on `identity/access.py`, not a new identity module.**
    `entitlement_ids_for_subject(*, user=None, group=None)` and `entitlement_names(ids)` — §12.1.
    The existing `held_entitlement_ids` takes a `Principal` and gate one's subject is a `User` or
    `Group` row. Both are the join `_grant_ids` already runs, keyed differently, on a seam every
    column may already import; a new module would be a fifth sanctioned identity import for two
    functions.

23. **A group recipient is checked against the GROUP's own grants, not its members'.** §12.1. A
    group is the subject of a grant in this codebase (`grant_user_xor_group`), so "does this group
    hold `E`" is a row rather than a computation over membership — and a member who personally
    lacks `E` is caught by the read-time gate, which checks each actual reader.

24. **Distillation resolves `chat.converse`; no new role is registered.** §10.2. Owner decision 6
    asks for a documented prompt constant, not a binding an operator must configure before the
    feature works. A box that can hold a conversation can distil one, and a dedicated summariser
    role is a `RoleSpec` registration away if a deployment ever wants it (§22).

25. **The pin picker is `?pin_into=` on the existing library page, not a new page.** §14, §15.4.
    The library page already lists, searches, paginates and permission-filters documents; a second
    listing would be a second set of counts and a second filter to keep in agreement with
    `readable_documents`.

26. **`WorkstreamTaint` is materialised, and the taint tables are two.** §5.3. The read-time gate
    runs on every non-owner page view; deriving the union per read would be a join across every
    conversation in the stream **and** would let the two gates answer from two different queries.
    Two tables rather than one XOR-constrained table, because they have different readers and one
    table would buy a check constraint, two partial uniques and a branch at every read to save one
    migration operation.

27. **`run_consolidate` is exempt from the labelling-authority rule, and the exemption is the
    point.** §10.4 step 5. `set_document_labels`' docstring says *"THE CALLER CHECKS THE
    PREDICATE"* — `may_label_document` plus the view's "owner of every entitlement being added or
    removed" rule. The consolidation handler checks neither: it applies
    `taint_ids_for_conversation(cid)`, which under ruling B can contain entitlements the actor
    neither owns nor holds, and writes `DOCUMENT_LABELLED` rows in the actor's name. The authority
    rule governs a person **choosing** labels for a document; nobody is choosing here — the labels
    are copied from what the material already carried, and refusing to copy one because the actor
    does not own it is precisely the laundering owner decision 6 forbids. `run_consolidate` names
    the exemption in its own docstring, so a reader working through `set_document_labels`' callers
    can see why one of them does not check.

Smaller calls recorded in place rather than listed again: no slug on `Workstream` (§5.1);
`archived_at` as a timestamp, matching `Conversation`'s (§5.1); `WORKSTREAM_SIDEBAR_LIMIT = 10`
with the same honest "…N more" line the conversation cap already renders, and the section
rendering even when empty (§15.1); reusing `SHARE_ADDED`/`SHARE_REVOKED` rather than minting a
workstream pair (§16.1) while minting the untaint pair, because the cascade is the one path that
removes a tag (§16.1); `_finish` keeping the single `save()` it has today (§7.2, and the
correction in item 3); the instructions block appended to the agent's system message rather than sent as a second system
message (§11); one audit row per newly added tag rather than per turn (§16.2); and no data
migration anywhere, which is a property every column above was chosen to have (§18).

---

## 24. Author concerns

Seven — four from the first draft, three added by the 2026-09-03 review. Each is implemented **as
the owner decided or as the ruling directs**; each is recorded here with its evidence and the
smallest remedy that would close it.

**Concern 1 — the honest-refusal copy is now slightly less than honest for a walled tool, and
this spec does not change it.** Owner decision 3a says a tool gated by a walled-off entitlement
refuses "with existing honest copy". For a **model**, that copy is
*"That model needs an entitlement this account does not hold — pick another."* Inside a walled
stream, a person may hold the entitlement perfectly well and still be refused, because the wall —
which they themselves set — narrowed it away. The sentence is then wrong about the reason while
right about the outcome. For a **tool** there is no sentence at all: `granted_tools` drops it
silently, which is correct on a labelled box and correct here too, so only the model path shows.

Implemented as written, because the alternative is a second sentence and a second reason code
threaded from `tool_access_for` through `preflight_turn` to `start_turn`, for a case whose fix is
one click away on a page the person is already on. The narrow remedy, if it is ever wanted: a
`MODEL_OUTSIDE_STREAM_SCOPE` reason beside `MODEL_NOT_PERMITTED`, with
*"That model is outside this workstream's scope."* One constant, one branch, no table.

**Concern 2 — a delegate obeys the wall and is never told about it, and that asymmetry will read
as a bug the first time somebody hits it.** §11 and §23.21: `ToolContext.stream` travels, so a
delegate's retrieval is walled and its contained-document access is correct; but
`delegate.py` builds its own message list from the delegate's own system prompt, so the stream's
*instructions* do not reach it. A delegate in a stream about Q3 planning therefore retrieves the
right documents and does not know it is in a stream. Implemented as written, because the delegate
deliberately does not inherit the parent's prompt or history and changing that is an agent-layer
decision, not a workstream one. The remedy is one line in `delegate.py` if the owner wants it —
append `_instructions_block(stream)` to the delegate's system prompt too — and it is a one-line
change precisely because §11 put the block behind a named function.

**Concern 3 — additive-only taint makes one accidental retrieval permanent, and there is no way
back short of deleting the entitlement.** §7.5. Retrieving a single document labelled `Legal`
into a stream tags that stream forever; every share to somebody without `Legal` is refused or
dormant from then on, and the only removal path in v1 is deleting the `Legal` entitlement
entirely (§8.4), which is not a remedy — it is a much larger act. On a busy box with a broad
library this will happen, and the person it happens to will have no page that explains what to do.
The direction is the safe one — the failure mode is "a share you expected does not work", never
"material leaks" — which is why it is implemented as the owner decided. Three mitigations are in
the spec: the audit trail names the causing turn (§16.2), so *why* is always answerable; the
stream page shows the tags as a list, so the state is visible; and §22 records the untaint's
designed shape, including the reason it is genuinely harder than it looks (removing a stream tag
that a conversation still carries is incoherent, so the real operation is "untaint the
conversation, then recompute the stream", and recompute is exactly what §23.26's materialised
table does not have).

**Concern 4 — the watcher can win the race for a stream upload and produce a universal
document.** §9.2. `document_upload` writes into `INGEST_INBOX_DIR` and the watcher service polls
that same directory; the view's own docstring already documents this race and how it handles
losing it. The watcher knows nothing about streams and calls `enqueue_ingest` with no
`workstream_id`, so a file the watcher stages first becomes a **universal** document even though
the person who uploaded it chose "this workstream only" — a placement decision silently
inverted, which is the one outcome owner decision 5 exists to prevent. It is a narrow window
(the file must be quiescent for `STABLE_AFTER_SECONDS = 5` before `poll_once` acts, and the view
enqueues immediately) and the consequence is a document in the library rather than a document
leaked to somebody, so it is a correctness bug and not a security one.

Implemented as written rather than fixed here, because the clean fix touches the watcher's
directory contract and that is its own change: a stream upload would stage into
`INGEST_INBOX_DIR/_streams/<pk>/<category>/`, which `category_from_subfolder` would have to learn
to read as a placement **and** a category rather than only a category, and `watch_folder` would
have to learn a reserved prefix. §10.4 sidesteps the same hazard for notes by writing them under
`DATA_DIR/notes/` — a directory the watcher never polls — and the same move is available here if
the owner wants the race closed: stage a stream upload outside the inbox entirely, with
`move=False`, and let the job own the file. That is one settings constant and one branch in
`document_upload`, and the reason it is not in WS-1 is that it changes the upload view's
lose-the-race handling, which has its own tests and its own docstring, for a window measured in
the five seconds `STABLE_AFTER_SECONDS` already imposes.

**Concern 5 — a re-consolidation destroys the previous note before the new one exists, and a
failure in between leaves it contentless.** §10.4 step 3. `stage_document`, on a changed hash,
runs `_delete_existing_data(existing)` — chunks, `DocumentRow`s and the stored file
(`tools/rag/ingest.py:551-563`) — and sets `PENDING`, inside its own transaction. If step 6's
`run_ingest_or_fail` then raises (an embed-model outage, an orphaned worker), it writes
`status=FAILED` and re-raises, and the note row survives with no chunks and no file: the previous
note's content is **gone**, it retrieves nothing into the stream, and `rag-document-file` 404s on
it. The first draft of §10.4 said this path needed "no new code for the overwrite case at all",
which is true and is exactly what made the window invisible.

The exposure is genuinely narrow — step 2 (the model call) precedes step 3, so a distillation
failure destroys nothing, and the embed step is all that is left inside it — and stating that is
most of the fix. The rest of the fix is the repair path: the stream page renders a `FAILED` note's
chip and offers **Re-consolidate** on it (§15.3 item 6). Without that button the documented repair
is `document_reingest` from the library page, which a non-admin stream owner cannot reach for a
contained document at all (§8.1) — so the person whose note was destroyed would have to ask an
administrator to fix it. Implemented as written, with §17.6 forcing the failure and asserting all
four properties. The structural fix, if it is ever wanted: stage into a second path and swap only
on success, which costs a second `Document` row's worth of bookkeeping for a window the retry
already closes.

**Concern 6 — a share recipient's retrieval puts material into the owner's stream, and the owner
may read it.** Ruling B (§23.B), stated here as the cost that ruling accepts. Owner O shares
stream W to recipient R; R holds `E3` and O holds neither `E3` nor a grant on it; R posts a turn,
retrieval quotes `E3`-labelled text into a committed tool turn, and the taint stamp adds `E3` to
W with R as its actor. O then reads that thread verbatim — it is a conversation in O's own stream,
and ruling C makes it visible to O deliberately — and may distil it into a contained note. **`E3`
material has reached somebody holding no `E3` grant, through a share the owner themselves made.**

Three things bound it and none of them closes it. It is **inherited, not invented**: IA-2's
`share_conversation` + `may_post_to` (`agents/visibility.py:404-418`) already let a `use`
recipient's retrieval into the owner's thread, and this phase does not widen that path — it makes
it visible for the first time, by recording what came in. The disclosure requires the **owner's
own deliberate share** to a recipient they chose. And the taint trail names R as the acting
principal per tag (§16.2), so the crossing is auditable rather than silent.

The narrow fix, if the owner wants it later, is one frozenset intersection at the seam the wall
already threads: for a turn in a stream reached **by share rather than by ownership**, narrow
`DocumentVisibility.entitlement_ids` to `held(principal) ∩ held(stream owner)`, computed from the
owner columns `agents/workstreams.py` already reads, inert in every posture with no accounts. It
is cheap and it is not free: it makes a share *reduce* what a recipient may read below their own
grants, on a surface with nowhere to explain why, and it makes the owner's grant set a second,
invisible wall nobody set. Ruling B chose visibility over restriction; this concern is the record
of what that choice costs.

**Concern 7 — §12.3's 403 page is the first surface on this platform that names an entitlement to
somebody who neither owns nor holds it.** Owner decision 8 asks for the names, so the disclosure
is deliberate; what was wrong was the *fence*. An earlier draft justified it with "the platform
already names them on the entitlements page to every signed-in account", which is false:
`identity-entitlements` is class **S** (`identity/routes.py:224`), the accounts page rendering
`effective_entitlements` is class `S`, and the only entitlement names a non-admin sees anywhere
come from `labelling_entitlements`, filtered to `owned_entitlement_ids` for a non-admin
(`identity/access.py:353-356`). Nobody downstream could have seen what the disclosure actually
cost, because the document said it cost nothing.

Implemented as the owner decided, with the three fences §12.3 now states — a live `Share` row is
required to reach the branch at all, ruling E puts this page in `name_for_viewer`'s
`disclose_all` mode so owner decision 8's own sentence is writable, and the list caps at five — and with §17.5 asserting the page's **body** and sweeping the
rest of the route surface to confirm this stays the only one. The remedy that would close it
entirely is to name nothing and say only *"this workstream contains material you are not cleared
for — ask an administrator"*, which is what the house rule would produce and what owner decision 8
explicitly rejected as mysterious rather than actionable. Recorded so the trade is visible rather
than assumed.
