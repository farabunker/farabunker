# Deletion semantics — design spec

**Date:** 2026-09-21
**Status:** Design. Nothing built. Written against branch `audit-deletion-paths` = `origin/dev`
`edf23e0`; every file, function and behaviour cited below was read in that tree at write time.
**Citation convention:** by ANCHOR — module, class, function, or a grep-able phrase — never by
line number, per `AGENTS.md`'s subagent-driven-development rule ("cite code by anchor or grep
pattern rather than line number"), which exists because a whole-branch review re-verifies every
citation against a tree that has moved. The read-only audit
(`.superpowers/deletion-audit-2026-09-21.md`) carries the same facts with line numbers at the
SHA above; it is the evidence, this is the design.
**Sources:** the owner-approved brief (`.superpowers/deletion-design-brief.md`) is binding; the
audit is evidence. Where the two disagree, the brief wins.
**Columns touched:** `identity/` (the ticket, the orchestration, the page), `agents/` (one
contract slot, one retention module), `tools/rag`, `tools/vision`, `models/queue` +
`models/contracts/queue.py` (one passthrough), `foundation/` (three registration tables).
**Lands as:** three slices on one branch off `origin/dev`, in the brief's order.
**Open questions:** none. Every call is made; §11 lists the ones the owner may want to overrule.

No model or vendor names appear in this document. No absolute paths appear in this document;
`<repo>` stands for the checkout root.

---

## Table of contents

1. [The owner's requirement](#1-the-owners-requirement)
2. [What exists today](#2-what-exists-today)
3. [The design](#3-the-design)
4. [Data model and migrations](#4-data-model-and-migrations)
5. [Error handling and edge cases](#5-error-handling-and-edge-cases)
6. [Backups are their own layer](#6-backups-are-their-own-layer)
7. [Tests](#7-tests)
8. [Documentation](#8-documentation)
9. [Delivery](#9-delivery)
10. [Out of scope, named](#10-out-of-scope-named)
11. [Decisions the author made](#11-decisions-the-author-made)

---

## 1. The owner's requirement

Verbatim intent, from the brief:

> Delete means delete. A deleted item's CONTENT (text, embeddings, chunks, sidecars, generated
> files, queue payloads, ask history, tool-call records, staging notes) is purged; an
> append-only AUDIT layer keeps content-free EVENTS (who deleted what kind and key, when).
> Posture keys the policy. Every posture shows a Deleted page with purge-on dates and restore
> until the cliff: a checkable promise, never silent deletion. Backups are their own layer.

Four owner rulings bind the design and are implemented where named:

1. **Tool-call records** (`agents.models.ToolInvocation`'s `args` / `text` / `error`) are
   SCRUBBED at the item's retention cliff by default; a setting allows a LONGER retention for
   them (extra days after the item's purge; default 0). → §3.6, §3.8.
2. **Queue rows for a deleted conversation are DELETED**, in the same request. The queue also
   gets a natural cliff of its own — finished rows older than the queue's retention (default
   one day) go regardless. → §3.6, §3.11.
3. **Existing residue** is handled by that queue cliff; no one-shot purge command. Ask history
   rows simply become deletable. → §3.11, §9.
4. **Audit keeps what an audit needs.** Deletion events are ALWAYS written and ALWAYS visible
   (kind, key, actor, timestamp); an audit-detail TOGGLE, default OFF, adds the labels an
   auditor would want. Never content. → §3.12.

### The three outcomes

**O1 — Delete means delete.** After "Delete permanently" on a conversation, no surface on this
box renders any part of it: not the chat list, not the Queue page, not Ask history, not the
gallery, not a search result. Not after a refresh, not after a poll tick, not a day later.

**O2 — A checkable promise.** A deleted item appears on a Deleted page in every posture, with
the date it will be purged and a Restore control that works until that date. Nothing is deleted
silently and nothing is deleted at a time the person was not told about.

**O3 — An honest audit.** What remains after a purge is a content-free event: who deleted what
kind and which key, and when. It is append-only, it is visible on the Deleted page's second
tab, and it never contains the deleted words.

---

## 2. What exists today

Stated so nothing here is built twice, and so §3 can name what it reuses.

### 2.1 Where content lives

"Content" is anything a person typed, uploaded, or that was derived from it.

| Column | Row | The content fields | Files |
|---|---|---|---|
| `agents` | `Conversation` | `title` | — |
| `agents` | `Turn` | `text`, `tool_call` (the validated args, replayed verbatim into later prompts), `data`, `artifacts`, `error` | — |
| `agents` | `ToolInvocation` | `args`, `text` (the tool result the model saw), `error` | — |
| `tools/rag` | `Document` | `title`, `extraction`, `tabular_schema`, `status_detail`, the two paths | managed store dir + `extract.json` |
| `tools/rag` | `DocumentRow` | `data` — every tabular cell | — |
| `tools/rag` | `AskRecord` | `question`, `answer`, `citations` | — |
| `tools/rag` | pgvector `data_rag_chunks` | chunk text + metadata | — |
| `tools/rag` | staging note | — | `<data>/notes/<conversation-uuid>.md` |
| `tools/vision` | `GenerationJob` | `params` (**the prompt**), `engine_payload`, `error` | managed dir |
| `tools/vision` | `JobInput` / `GeneratedOutput` | `path` | yes |
| `models/queue` | `InferenceJob` | `payload` (`text` for a turn, `question` for an ask, `params` for a generation), `result` (the full assistant reply), `error`, `progress.label`, `checkpoint` | — |

Three of those tables carry a cross-column key **by value, never as a foreign key**, because
the import law forbids the FK: `Turn.queue_job_id`, `ToolInvocation.queue_job_id` and
`GenerationJob.queue_job_id` (each field's own docstring says so), `DocumentAttachment.
conversation_id` and `Document.notes_conversation_id` (both UUIDs by value, because `tools/rag`
may not import `agents.models`). Every seam in §3 is shaped by that fact.

### 2.2 What delete reaches today

| Surface | Code | Removes | Leaves | Audit |
|---|---|---|---|---|
| Chat → delete a conversation | `agents/chat/views/conversations.py::conversation_delete` → `agents/visibility.py::delete_conversation` | `Share` rows; `DocumentAttachment` claims and chat-scoped `Document`s through the registered seam; `Conversation` + `Turn` (CASCADE) | **queue rows**; **`ToolInvocation` content**; the notes file; conversation-born generation jobs | **none** |
| Library → delete a document | `tools/rag/views.py::document_delete` → `tools/rag/services.py::delete_document` | chunks, store dir, row (`DocumentRow` CASCADE) | `rag.ingest` / `rag.ask` queue payloads; `AskRecord.citations` still names it | **none** |
| Gallery → delete a job | `tools/vision/services.py::delete_job` | row + children (CASCADE), managed dir, **best-effort engine-side sweep** (`store.remove_engine_files`, which never raises) | `vision.generate` queue payload — the prompt | **none** |
| Workstream delete | `agents/visibility.py::delete_workstream` | shares, the row, pins | refuses while non-empty (`PROTECT`) | **yes** (`WORKSTREAM_DELETED`) |
| Ask history | — | — | **everything** | — |
| Queue page | `models/queue/views.py` — cancel only | — | — | — |

Two known leaks, both confirmed on the live box by the audit's read-only check:

- **(a) A deleted conversation is still readable on the Queue page.** `agents/chat/service.py`
  puts `"text": message` into the `agent.turn` payload; `agents/runtime/jobs.py::summarize_turn`
  renders `f"{agent}: {text}"` from the payload alone, with no database read;
  `models/queue/views.py` calls it per row. `delete_conversation` never touches `InferenceJob`
  and **cannot** — `agents/` may not import `models.queue` (import-law rule 2, pinned by
  `foundation/ops/tests/test_import_law.py`'s `FORBIDDEN_MODULES`). The only eviction is the
  FIFO `models/queue/backend.py::_prune_finished_jobs` at `retention_limit=100`. The one
  existing mitigation is visibility, not deletion: `models/queue/visibility.py::
  may_read_job_content` hides the summary from a non-actor — and the person who deleted their
  own conversation IS the actor, so they see their own deleted words. Live numbers: **78
  `agent.turn` rows, 69 of them naming a conversation that no longer exists, 69 of those (100%)
  carrying non-empty `payload["text"]`.**
- **(b) Ask history has no deletion path at all.** `tools/rag/views.py::HistoryView`'s own
  docstring: *"This is a reading surface, and it reads."* No delete route exists in
  `tools/rag/urls.py`. The only removal is FIFO pruning on insert
  (`tools/rag/services.py::_prune_ask_records`, bound by `RagSettings.history_limit`, default
  100). Live: **13 `AskRecord` rows, undeletable.**

The audit **refuted** a third suspected leak: orphaned pgvector chunks.
`tools/rag/index.py::delete_chunks_for_document` filters on `metadata_->>'file_id'`, which is
the `Document` pk; live orphan count by that key is **0**. (A naive query on the metadata's
`doc_id`/`document_id` — LlamaIndex node UUIDs — falsely reports 32 of 32.) Chunks are verified
at purge, not re-implemented.

Live audit trail at the time of the check: **3 `AuditEvent` rows, not one of them a deletion.**

### 2.3 The machinery this design reuses rather than reinvents

**The cascade registry.** `identity/contracts/cascades.py` is pure — `EntitlementCascade(key,
label, handler)` where `handler` is a DOTTED-PATH STRING, resolved at delete time by
`identity/cascades.py::_run` with `import_string`, because `identity/` may not import `agents/`
or `tools/`. One handler, two modes: `commit=False` counts, `commit=True` removes and repairs.
Columns self-register from `AppConfig.ready()` — `agents/apps.py` registers three, `tools/rag/
apps.py` one. The runner **never swallows**, and the caller
(`identity/services.py::delete_entitlement`) runs the cascades BEFORE the row delete, all inside
one `transaction.atomic()`. Its module docstring states the reason in terms that transfer
without change: *"A delete that reported success while leaving orphan labels and a stale chunk
cache behind is the one failure mode this registry exists to prevent."*

**The savepoint precedent.** `agents/attachments.py::delete_attachments_for` resolves a
registered dotted path inside a NESTED `transaction.atomic()`. Its docstring is the reason:
a database-level error inside a provider poisons the whole Postgres connection for the rest of
the surrounding transaction, so even a caught Python exception would take the outer delete down
with it; a nested atomic block is a SAVEPOINT, and rolling back to it restores the connection.
The same docstring records the limit of that guarantee: *"NOT TRUE FOR THE FILES… a filesystem
delete has no rollback."*

**The audit seam.** `identity/audit.py` is the only module in the codebase permitted ANY
`AuditEvent.objects` attribute access — not `.create`, not `.filter` — pinned by an AST guard in
`foundation/ops/tests/test_column_boundaries.py` that bans the whole manager, because `.update()`
and `.delete()` never call `save()`. `identity/contracts/actions.py` is a closed tuple;
`AuditEvent.save()` raises on an unlisted action. The catalogue already carries
`ENTITLEMENT_DELETED`, `GROUP_DELETED`, `WORKSTREAM_DELETED`, `ENGINE_FILE_DELETED` — and no
`content.*` action at all. **This is the deletion-events layer the requirement asks for,
already built.**

**The visibility functions.** One choke point per column, each already taking an optional
pre-fetched `IdentitySettings` row: `agents/visibility.py::visible_conversations`,
`tools/rag/access.py::readable_documents` / `::attached_documents` / `::visible_ask_records`,
`tools/vision/visibility.py::visible_jobs`. Each opens with a `sees_all_content` branch that
returns early — a fact §3.4 has to design around rather than past.

**The posture and entitlement vocabulary.** `identity/contracts/postures.py` names `open`,
`personal`, `enterprise`; `identity/access.py::posture` reads it off the `IdentitySettings`
singleton (a database row, never an environment variable, because web, worker and watcher share
only the database). `is_admin` is True for everybody on an open box. `sees_all_content` has
deliberately NO posture branch. `owned_entitlement_ids` is the owner role — the natural gate for
an owner-only hold. `owned_rows_q` / `owner_fields` are the one definition of ownership across
five tables in three columns.

**The settings-area registration, three places that must agree** — `foundation/settings_area.py`
`SETTINGS_GROUPS`, the sidebar `foundation/templates/_settings.html` (same order, same gates as
template `{% if %}`s; a context processor is forbidden by a query-count pin), and a `HelpCard` in
`foundation/settings_help.py` whose `gate` is compared for EQUALITY against the `Entry.gate` —
plus `_NAMES` in `foundation/tests/test_page_names.py` and a route class in `identity/routes.py`.
Field range limits live in the writer, never as a database constraint
(`foundation/settings_bounds.py`).

---

## 3. The design

### 3.1 Shape in one paragraph

Deleting anything writes ONE row in ONE new identity table — the deletion ticket — and an audit
event. Every column's existing visibility function excludes ticketed keys, so the item vanishes
from every surface immediately. The ticket carries the date the content will actually be
destroyed. On that date — or the moment a person clicks "Delete permanently", where the posture
allows it — the kind's registered handlers run through the EXISTING cascade registry, the
content is gone, the ticket is gone, and a content-free audit event remains. No per-model
soft-delete column, no second registry, no scheduler, no cache.

### 3.2 The deletion ticket

`identity/models.py::DeletionTicket`, and `identity/contracts/retention.py` (pure) for the kind
vocabulary — a closed tuple, validated in `DeletionTicket.save()` exactly as
`AuditEvent.save()` validates its action against `AUDIT_ACTIONS`, and for the identical reason:
a typo'd kind is a construction error at the call site, not a category that silently splits a
page in two.

| Field | Type | Why |
|---|---|---|
| `kind` | `CharField(32)`, closed vocabulary | `conversation`, `document`, `ask`, `vision_job`. Not `workstream` — §10. |
| `key` | `CharField(200)` | The item's pk AS TEXT. The four kinds have three pk types (UUID, UUID, int, int); one text column is the `agents.models.Share::target_key` precedent, and §3.4 handles the type join the same way `agents/shares.py::shared_keys` does. |
| `owner_kind` / `owner_key` | `CharField(32)` / `CharField(200)` | The ITEM's owner, stamped at create from the item's own owner columns. Same two columns, same widths, same blank default as the five tables that already carry them. |
| `deleted_by_kind` / `deleted_by_key` | `CharField(32)` / `CharField(200)` | The ACTOR, which is not always the owner (an administrator deletes somebody's row). A principal is two strings; no FK to `User`, for `AuditEvent`'s own recorded reason. |
| `label` | `CharField(255)`, blank | The item's title AT DELETE TIME, for the Deleted page only. Content, and treated as such: never copied into an audit event unless `audit_detail` is on (§3.12), and destroyed with the ticket at purge. |
| `deleted_at` | `DateTimeField(auto_now_add=True)` | — |
| `purge_on` | `DateField`, `db_index=True` | A DATE, not a datetime: "Purge on 21 October 2026" is the promise the page prints, and a date is what a person can check. Computed once at create (§3.3) and never recomputed (§5). |
| `hold_by_kind` / `hold_by_key` | `CharField`, blank | Set = the cliff is suspended. Enterprise only (§3.10). Slice 3. |
| `hold_note` | `TextField`, blank | Why. Operator prose, not content. |
| `purged_at` | `DateTimeField`, null | Set = the content is already gone and only the deferred tool-record scrub is outstanding (§3.8). A ticket with this set can never be restored. |
| `tool_purge_on` | `DateField`, null | `purge_on + tool_record_extra_days`, when that setting is non-zero. Null = nothing deferred. |
| `deferred_ids` | `JSONField`, default `list` | The `ToolInvocation` ids whose scrub is deferred to `tool_purge_on` (§3.8). Empty in the default configuration. **Ids, never content** — the same discipline the audit trail keeps. |

**Uniqueness.** `UniqueConstraint(fields=["kind", "key"], name="uniq_deletion_ticket")`. One
ticket per item, so a second delete of the same item is a no-op rather than a duplicate row, and
`get_or_create` is the writer. It is also the index §3.4's exclusion reads.

**Indexes.** The unique constraint covers `(kind, key)`. Two more:
`Index(["owner_kind", "owner_key"], name="identity_ticket_owner")` — the Deleted page's own
query, the same shape as `agents_conv_owner` — and `purge_on`'s own `db_index`, which is the
sweep's ordering (§3.9). `Meta.ordering = ["-deleted_at"]`.

### 3.3 Soft delete, restore, purge

`identity/retention.py` — the live service, beside `identity/cascades.py` and for the same
reason (`identity/contracts/retention.py` stays pure, pinned by `identity/tests/test_purity.py`,
which imports the whole `contracts/` package with no `DJANGO_SETTINGS_MODULE` set at all).

```
delete_content(actor, *, kind, key, owner, label="", source=SOURCE_WEB) -> DeletionTicket
restore_content(actor, ticket, *, source=SOURCE_WEB) -> None
purge_ticket(actor, ticket, *, source=SOURCE_WEB) -> dict[str, int]
sweep(*, limit=SWEEP_LIMIT) -> int           # always acts as the service principal, §3.9
ticketed_keys(kind) -> list[str]
```

- **Soft delete** = `get_or_create` the ticket with `purge_on = today + IdentitySettings.
  get_solo().retention_days`, `record(actor, CONTENT_DELETED, …)`, then run the bounded sweep
  (§3.9). When `retention_days == 0`, `purge_on` is today, the sweep the same call runs picks
  the ticket up, and the content is gone before the request returns. **Zero stays expressible**
  — the audit's constraint 9 — and it is the posture-independent way to say "no grace period".
- **Restore** = refuse when `purged_at` is set or a hold forbids it, delete the ticket,
  `record(actor, CONTENT_RESTORED, …)`. Nothing else: the item was never modified, so there is
  nothing to put back. That is the whole return on not adding per-model columns.
- **Purge** = inside one `transaction.atomic()`: run the kind's registered handlers with
  `commit=True` (§3.5), then either delete the ticket or — when a tool-record deferral is
  outstanding — stamp `purged_at` and leave it for the sweep (§3.8), then
  `record(actor, CONTENT_PURGED, removed=counts)`. The counts are per-handler integers:
  content-free by construction.

**Refusals** raise `identity.services.ServiceRefused`, the sentence-carrying exception this
column already uses, so every view keeps its never-500 shape.

### 3.4 Visibility exclusion, per column

The mechanism is one function — `identity/retention.py::ticketed_keys(kind)` — returning a
MATERIALISED `list[str]`, not a `Subquery`. That is `agents/shares.py::shared_keys`'s own
recorded reasoning, applied unchanged: the ticket's `key` is text and the target tables have
different primary-key types, so a subquery would need a per-type cast and would be a silent type
mismatch waiting to happen; two small queries on a single-box install beat one clever one. The
list is bounded by the open tickets on the box, which the cliff and the sweep bound in turn.

**The exclusion is applied to the base queryset, BEFORE each function's `sees_all_content`
branch.** This is the single most important mechanical detail in this section. Every one of the
four functions opens with an early return for a principal who sees all content — and
`sees_all_content` is True for EVERY principal on an open box, which is the posture the live box
is in. An exclusion bolted onto the restricted leg alone would leave deleted items fully visible
in the exact posture most boxes run. So:

```
qs = Conversation.objects.select_related("agent").exclude(
    pk__in=ticketed_keys(KIND_CONVERSATION))
if sees_all_content(principal, settings_row=settings_row):
    return qs
```

| Column | Function | Exclusion added |
|---|---|---|
| `agents` | `agents/visibility.py::visible_conversations` | `exclude(pk__in=ticketed_keys("conversation"))` on the base queryset |
| `tools/rag` | `tools/rag/access.py::readable_documents` | `exclude(pk__in=ticketed_keys("document"))`, plus the chat-scoped clause below |
| `tools/rag` | `tools/rag/access.py::listable_documents` | the same two exclusions — the library LIST is a separate function from the CONTENT read, and a row that vanished from one and not the other would be a half-delete |
| `tools/rag` | `tools/rag/access.py::attached_documents` | rows whose `conversation_id` is a ticketed conversation key are dropped |
| `tools/rag` | `tools/rag/access.py::visible_ask_records` | `exclude(pk__in=ticketed_keys("ask"))` |
| `tools/vision` | `tools/vision/visibility.py::visible_jobs` | `exclude(pk__in=ticketed_keys("vision_job"))` |

**Chat-scoped documents follow their conversation.** A `Document` with `scope=conversation` has
exactly one `DocumentAttachment`, for one conversation (`tools/rag/access.py::delete_attachments`
states and depends on that invariant). So `readable_documents` and `listable_documents` also
exclude documents whose attachment names a ticketed conversation — one more materialised list,
flat in the number of tickets, not in the number of documents. Universal and stream-contained
documents are UNTOUCHED: an attachment is a claim a conversation makes on a document, never the
document's own existence, and deleting a conversation must not hide a document another
conversation still holds a claim on. §5 carries the edge case.

**Not excluded, deliberately:** `agents/visibility.py::may_manage_conversation` and its
siblings. A ticketed row is unreachable because `visible_conversation_or_404` resolves through
`visible_conversations`, which now excludes it; adding a second check in the predicate would be
a second place for the rule to live.

### 3.5 The retention namespace on the existing cascade registry

`identity/contracts/cascades.py` gains a second dataclass and a second registry dict beside
`EntitlementCascade` — the same module, the same file, the same purity, the same dotted-path
discipline. Not a second registry module: the brief's instruction is explicit, and the audit's
constraint 2 says the same thing ("Reuse this — do not invent a second registry").

```
@dataclass(frozen=True)
class RetentionHandler:
    kind: str       # which ticket kind this answers for
    key: str        # "agents.conversation", stable identifier
    label: str      # "Conversation and turns" — what the count line prints
    handler: str    # "package.module.function"
    order: int = ORDER_ROWS

register_retention_handler(spec) -> None
retention_handlers(kind) -> list[RetentionHandler]   # sorted by (order, registration index)
```

Handler signature: `(key: str, *, commit: bool) -> int`. Identical in shape to
`EntitlementCascade`'s, and identical in meaning: `commit=False` counts what this column WOULD
remove (the Deleted page's per-item count line, and the confirmation copy), `commit=True`
removes it and returns the same count. One handler, two modes, for the reason the existing
dataclass docstring already gives — two registrations would be two things to keep in agreement
about what "affected" means.

**Ordering, as two named bands** (constants in the same pure module):

- `ORDER_ROWS = 100` (the default) — the handler touches database rows only.
- `ORDER_FILES = 200` — the handler removes bytes from disk, directly or through a function
  that does.

The runner runs every `ORDER_ROWS` handler before any `ORDER_FILES` handler, stable within a
band by registration order. **This is the filesystem-last rule, and its whole purpose is the
one thing a database transaction cannot undo.** `tools/rag/services.py::delete_document`'s own
docstring records it: a filesystem delete cannot be rolled back, so a row handler that raises
AFTER files were removed would leave a resurrected row pointing at bytes that are gone. Running
row handlers first means the common failure — a database error — aborts the purge with nothing
on disk touched. The converse case is accepted and named: **a file handler that raises after
some bytes are gone leaves the rows standing, the ticket standing, and the item still hidden;
the next sweep retries.** Every retention handler must therefore be IDEMPOTENT — re-running it
on a partially-purged item must complete rather than raise — and that is a contract obligation
stated in `RetentionHandler`'s docstring and in `docs/EXTENDING.md` (§8), not an assumption.

A handler that must both read the item's rows and remove its bytes (the conversation handler
does: it reads `Turn.artifacts` before it deletes the turns) registers in the FILES band and
does its own reads before its own writes, internally. That keeps the registry's ordering rule to
one field with two values instead of a general dependency graph nothing else needs.

**Failure semantics.** `identity/cascades.py` gains `retention_counts(kind, key)` and
`run_retention(kind, key)`, sharing one `_run_retention` — exactly `_run`'s shape. It **never
swallows**: a handler that cannot be imported, or that raises, takes the whole purge down,
inside `purge_ticket`'s `transaction.atomic()`, so nothing is half-purged at the row level and
the ticket survives to be retried. Each handler is invoked inside a NESTED `transaction.atomic()`
— the `agents/attachments.py::delete_attachments_for` savepoint discipline — but with the
opposite catch policy, and the difference is the point:

> `delete_attachments_for` catches, because a broken cleanup provider must not block a delete
> the actor already confirmed. A retention handler's exception is NOT caught, because a purge
> that reported success while leaving content behind is exactly the failure this whole feature
> exists to prevent. The savepoint is still required: without it, a database-level error inside
> a handler poisons the Postgres connection for the rest of the outer transaction, and the
> audit write and the ticket update that follow would fail for a reason unrelated to the real
> one. The savepoint restores the connection so the real error reaches the view, which renders
> it as a refusal sentence and leaves the ticket in place.

### 3.6 The per-kind handlers

Every one is a thin wrapper over a delete that already exists, except the four small new pieces
the brief names. Registration is one call in each column's `AppConfig.ready()`, beside the
`register_entitlement_cascade` calls already there.

#### kind `conversation`

| Order | Key | Dotted path | What it does |
|---|---|---|---|
| ROWS | `queue.conversation_jobs` | `models.queue.retention.forget_conversation` | Deletes every `InferenceJob` whose payload names this conversation (§3.7) |
| FILES | `agents.conversation` | `agents.retention.purge_conversation` | The whole of the agents-side purge, in order (below) |
| FILES | `rag.conversation_notes` | `tools.rag.retention.purge_conversation_notes` | The staging note file and the note `Document` |

`agents/retention.py::purge_conversation` is `delete_conversation`'s hard path, moved, plus
three additions. In order, inside the runner's savepoint:

1. **Collect, before anything is deleted.** One `values_list` over the conversation's turns
   yields `artifacts`, `data` and `invocation_id`. From it:
   - artifact references parsing as `output:<id>` / `input:<id>` through
     `agents/contracts/artifacts.py::parse_artifact` (unparseable entries dropped and logged,
     the `shared_keys` posture);
   - generation ids: `data["id"]` for any turn whose `data` is a dict whose `"id"` parses as a
     UUID (§3.7 explains why this is the channel that catches a failed job, and why the agents
     column deliberately does not know the image tool's key);
   - `invocation_id` values, non-null, for step 4.
2. **Hand the artifact references and generation ids to the registered artifact purge** — one
   new single slot on `agents/contracts/artifacts.py` (§3.7), resolved with the same savepoint
   discipline. `tools/vision` registers it; it maps refs and ids to jobs, dedupes by job, and
   calls `tools/vision/services.py::delete_job` per job.
3. **The existing row deletes:** `Share` rows for this conversation; `agents/attachments.py::
   delete_attachments_for` (which reaches `tools/rag/access.py::delete_attachments` through the
   registered cleanup seam — chat-scoped documents are deleted outright there, universal and
   contained ones keep only their claim removed); then `conversation.delete()`, and `Turn` goes
   by CASCADE.
4. **The tool-record scrub** (§3.8), on the invocation ids collected in step 1 — collected
   FIRST because `Turn.invocation` is `SET_NULL`, so after step 3 there is no path from the
   conversation to its invocations at all. That `SET_NULL` is deliberate (the 2026-08-27
   addendum's consequence 3: an audit row is not owned by the conversation table) and it is not
   being changed; the scrub empties the content fields and leaves the shell.

`tools/rag/retention.py::purge_conversation_notes` removes `<data>/notes/<conversation-uuid>.md`
— the deterministic path `tools/rag/jobs.py` writes — and deletes any `Document` whose
`notes_conversation_id` is this conversation, through `services.delete_document`, which already
tears down chunks, store directory and row together. Missing file is not an error (§5).

#### kind `document`

| Order | Key | Dotted path | What it does |
|---|---|---|---|
| ROWS | `queue.document_jobs` | `models.queue.retention.forget_document` | `rag.ingest` rows naming this `document_id` |
| FILES | `rag.document` | `tools.rag.retention.purge_document` | `tools/rag/services.py::delete_document`, unchanged: chunks by `file_id`, the managed store directory, the row, `DocumentRow` and `DocumentAttachment` by CASCADE |

The chunk delete is **verified, not assumed**: the audit's refutation stands only because
`delete_chunks_for_document` keys on `metadata_->>'file_id'`, which is the `Document` pk, and a
test asserts a zero chunk count for that key after a purge (§7).

#### kind `ask`

| Order | Key | Dotted path | What it does |
|---|---|---|---|
| ROWS | `rag.ask_record` | `tools.rag.retention.purge_ask` | `AskRecord.objects.filter(pk=…).delete()` |

No queue handler. A `rag.ask` payload carries the question text and the actor, and **no
reference to the `AskRecord` the handler later writes** (`tools/rag/views.py` builds it; the row
is created by `tools/rag/services.py::record_ask` on success). There is no id to key on, and
matching on question text would be a guess. Those rows are left to the queue's own age cliff
(§3.11) and that residue is named in §10 rather than papered over.

#### kind `vision_job`

| Order | Key | Dotted path | What it does |
|---|---|---|---|
| FILES | `vision.job` | `tools.vision.retention.purge_job` | `tools/vision/services.py::delete_job` — row, `JobInput`/`GeneratedOutput` by CASCADE, the managed directory, and its own best-effort engine-side sweep — plus the queue row by id (§3.7) |

`delete_job` already best-effort-sweeps the engine's own `/engine/output` and `/engine/input`
bind mounts through `store.remove_engine_files`, which never raises, so a missing bind mount or
a permission error cannot turn a purge into a failure. **Said once, here**, and not restated at
every call site: those directories are tracked by no row at all
(`tools/vision/maintenance.py`), so the sweep is the only reach this platform has into them and
the Engine files page remains the operator's manual door.

### 3.7 Conversation-born image jobs, in full

This is the one place where finding the content is harder than deleting it, so the mechanism is
spelled out rather than summarised.

**Two channels, because one of them has a hole.**

1. **Artifact references.** `tools/vision/tools.py::run_generate` returns
   `ToolResult(text=…, data=payload, artifacts=artifacts)` where
   `artifacts = tuple(f"output:{output['id']}" for output in payload.get("outputs", ()))`.
   `agents/runtime/loop.py` writes `artifacts=list(outcome.result.artifacts)` onto the tool
   `Turn` verbatim. `output:<id>` is a `GeneratedOutput` pk and `input:<id>` a `JobInput` pk;
   each is one FK hop from its job, and several outputs share one job, so the mapping dedupes
   by job.
2. **Generation ids recorded in the turn's tool result.** The same line of
   `agents/runtime/loop.py` writes `data=(outcome.result.data if outcome.result is not None
   else None)`. For this tool that data IS `tools/vision/services.py::job_json(job)`, whose
   first key is `"id": str(job.id)`. **`Turn.data["id"]` therefore names the generation job
   even when the job produced no output at all.**

**What a FAILED chat-created job leaves behind, precisely, and whether it is caught.** A job
that reached the engine and failed has its prompt in `GenerationJob.params` and its reason in
`.error`; it mints **no** `GeneratedOutput`, so channel 1 finds nothing — the gap the vision
steward flagged. Channel 2 catches it: `run_generate` reaches `job_json` on every terminal
outcome, including `failed` (its own comment describes speaking a failed job's error as what
"turns 'finished as failed.' into a recoverable answer"), and the tool turn is written with that
payload as `data`. **A failed chat-created job IS caught, by its generation id.** The same is
true of a job still running when the turn's budget expired: the tool returns
`"Generation <uuid> is still running"` with the same `data`.

**The one case that is not caught, named as accepted residue.** A generation whose tool turn was
never written at all — the job was created by `services.start_generation`, and the turn died
between that return and `agents/runtime/loop.py`'s tool-turn create (a crash, a worker kill, a
cancelled turn). The job row then exists with the prompt in `params` and no reference to it
anywhere in the conversation, so neither channel sees it. It remains visible to its own owner in
the gallery, where the `vision_job` kind (slice 2) deletes it with the engine-side sweep. It is
not reachable from the conversation's purge, and this spec does not claim otherwise. A refusal
BEFORE the job exists (`VisionUnavailable` → `ToolRefused`) leaves nothing at all, which is why
that path needs no handling.

**The seam.** `agents/contracts/artifacts.py` gains ONE slot beside the existing
`register_artifact_file_resolver` / `file_resolver_for` pair:

```
register_artifact_purge(dotted_path: str) -> None
artifact_purge() -> str | None
```

Handler signature `(refs: Sequence[str], generation_ids: Sequence[str], *, commit: bool) -> int`.
A single slot, not a per-kind dict, matching `agents/contracts/attachments.py::
register_attachment_cleanup`'s own single-slot shape for the same situation: the agents column
computes values and one tool column knows what they mean. `tools/vision/apps.py` registers it;
`tools/rag` does not need it, because `document:<id>` artifacts are `Document` rows the
attachment seam already reaches.

**Queue rows for a generation.** A `vision.generate` payload carries `operation`, `params` and
`inputs` — and no generation id, because the `GenerationJob` is created by the handler at run
time. The link exists in the other direction: `GenerationJob.queue_job_id`. So the vision purge
handler collects those ids from the jobs it is about to delete and hands them to
`models/contracts/queue.py::forget_jobs(job_ids)` — ONE new passthrough on the dispatch seam
every column already uses for `enqueue`/`get_job`, dispatched through
`settings.INFERENCE_QUEUE_BACKEND` exactly as those two are. This is not an import-law exception:
rule 2 forbids `models.queue.models` to `tools/`, `agents/` and `foundation/` (pinned by
`FORBIDDEN_MODULES` in `foundation/ops/tests/test_import_law.py`, with one carve-out for
`foundation/ops/backup.py`), and `models.contracts.queue` is the sanctioned door whose own
docstring explains that it exists precisely so a tool column never reaches into `models.queue`.

**The queue's payload-keyed handler**, `models/queue/retention.py`, with no `agents` import of
any kind — a small table of what a payload field means, owned by the column that owns the
payload column:

| Ticket kind | Job kinds | Payload field | Cast |
|---|---|---|---|
| `conversation` | `agent.turn`, `rag.consolidate` | `conversation` | `str` |
| `document` | `rag.ingest` | `document_id` | `int` |

Both are JSON field lookups (`payload__conversation=…`), one query per job kind, and both
payload shapes are read from their producers: `agents/chat/service.py` for `agent.turn`,
`agents/chat/views/workstreams.py` for `rag.consolidate` (which carries the conversation id AND
the conversation's `title`), `tools/rag/ingest.py` for `rag.ingest`.

**Rows are DELETED, not scrubbed** — owner ruling 2. The audit's own open question weighed
keeping the row for its timings and `model_refs`; the ruling is that a finished job's
bookkeeping is not worth a table of half-erased rows, and the queue's own cliff removes finished
rows on an age basis anyway (§3.11), so a scrub would only defer the same delete.

### 3.8 The tool-record scrub, and the extra-days rule

`agents/retention.py::scrub_tool_records(invocation_ids, *, commit)` sets `args={}`, `text=""`,
`error=""` on those `ToolInvocation` rows in one `update()`. The shell — principal, agent slug,
tool key, outcome, timings, `queue_job_id` — stays, because that shell IS the machine audit
trail `identity/contracts/actions.py`'s own docstring points at when it explains why tool calls
are absent from the `AuditEvent` catalogue: *"tool calls already have a better record in
`agents.models.ToolInvocation`… the first must be kept and the second must be prunable."*
Scrubbing the words and keeping the record is that sentence, implemented.

**The extra-days rule, without a new column on `ToolInvocation`.** When
`IdentitySettings.tool_record_extra_days` is 0 — the default, and the demo path — the scrub runs
inline in step 4 of the conversation handler and the ticket is deleted at the end of the purge.
When it is non-zero, the purge instead:

- runs every other handler as normal (the conversation's own content is gone on its promised
  date — the extra days buy time for the tool records, never for the conversation);
- stamps the ticket `purged_at=now`, `tool_purge_on = purge_on + tool_record_extra_days`, and
  `deferred_ids = [<invocation ids>]` — ids only, never the words they point at;
- is picked up again by the sweep on `tool_purge_on`, which runs the scrub and deletes the
  ticket.

A ticket in that state is **not** restorable (§5) and is not listed on the Deleted page's first
tab — its content is already gone; it is bookkeeping the sweep will finish. Ids are not content.

This is why the ticket carries `purged_at` and `tool_purge_on` at all, and it is the reason a
second table was rejected: one row, two dates, one sweep.

### 3.9 The sweep

`identity/retention.py::sweep(*, limit=SWEEP_LIMIT)` — `SWEEP_LIMIT = 25`, a module constant,
never a literal at a call site.

```
tickets due = (purged_at IS NULL AND purge_on <= today AND no hold)
              OR (purged_at IS NOT NULL AND tool_purge_on <= today)
order by purge_on, pk
[:limit]
```

Each is purged in its own transaction, so one failing ticket does not block the rest of the
batch; the failure is logged with its kind and key (structural, never content — the shape
`tools/rag/jobs.py` uses throughout) and the ticket stays due.

**The sweep always acts as the service principal** (`identity/contracts/principals.py`), whoever
triggered it. A sweep that ran under the acting principal would write "this member purged
somebody else's conversation" into the audit trail for a cliff nobody clicked; the cliff is the
box's own act, and the event says so. Only an explicit click — Delete, Restore, Delete
permanently, Hold — carries a real actor.

**Three callers, no scheduler and no new job kind:**

1. **Prune-on-write** — at the end of `delete_content`. This is what makes `retention_days = 0`
   a synchronous purge, and it means a box that is used at all keeps itself clean.
2. **Prune-on-read** — on the Deleted page's GET, before the list is built, so the page can
   never show a row whose promised date has passed.
3. **`manage.py purge_deleted [--limit N]`** — an `identity/management/commands/` command for an
   operator's cron, with `source=SOURCE_CLI` on its audit events and
   `identity.contracts.principals`' service principal as the actor.

The pattern is the one `tools/rag/services.py::record_ask` and `models/queue/backend.py::
enqueue` already use — prune on write, bounded — and the reason it is enough is the same: a box
where nothing is ever deleted has nothing to purge.

### 3.10 Posture policy

`identity/access.py::posture()` keys the table; `is_admin`, `sees_all_content` and
`owned_entitlement_ids` are the predicates, unchanged.

| | `open` | `personal` | `enterprise` |
|---|---|---|---|
| Who may delete an item | whoever may manage it today — `may_manage_conversation`, `may_administer_document`, `may_read_job`, and the item's owner for an Ask record | same | same |
| What "Delete" does | ticket + cliff | ticket + cliff | ticket + cliff |
| The cliff | `retention_days`, default 30; `0` purges inline | same | operator-set on the settings page; the enterprise box's own number |
| Restore, until the cliff | the item's owner, or `sees_all_content` | same | same |
| "Delete permanently" (purge now) | yes — the item's owner or `sees_all_content`. `is_admin` is True for everybody here, and there is nobody for anything to be hidden from | yes, same rule | **no. Nobody, before the cliff.** The control is not rendered and the POST refuses with its own sentence |
| Hold (suspend the cliff) | not offered — no accounts, no owner role to gate it | not offered | an owner-role holder of one of the item's entitlements (`owned_entitlement_ids`) or a superuser |
| Whose tickets a viewer sees | everyone's — `sees_all_content` is True | own, plus everyone's for an administrator with the content setting on | same as personal |

The enterprise row is the whole reason the policy is posture-keyed: on a box with a records
obligation, a user's delete must be a request, not an erasure, and the person who could override
that is the entitlement's owner. Everywhere else, delete means delete.

### 3.11 The queue's own cliff, and today's orphans

`models/queue/models.py::JobSettings` gains `retention_days` —
`PositiveIntegerField(null=True, blank=True, default=1)`. Null means **no age cliff** (the FIFO
`retention_limit` alone), the same "honestly unknown, never silently assumed" convention
`memory_budget_bytes` and `max_queued_per_principal` already document on that model; `1` is the
shipped default the brief sets.

`models/queue/backend.py::_prune_finished_jobs` gains an age condition beside its existing
cutoff-pk delete: terminal rows whose `finished_at` is older than `retention_days` go, in the
same bulk delete, still scoped to `state__in=TERMINAL_STATES` so a queued or running row can
never be counted or removed. It already runs on every `enqueue()`, so no new call site exists.

**This is what clears the live box's residue.** The 69 orphaned `agent.turn` payloads are
terminal rows older than a day; the first `enqueue()` after deploy removes them, along with
every other finished row past the cliff. No one-shot purge command, per owner ruling 3 — a
command that existed only to fix a historical state is a command nobody deletes afterwards.

The bound lives in the writer, not the database: `models/queue/views.py`'s settings POST
validates `retention_days` the way it already validates `retention_limit`, through
`foundation/settings_bounds.py::exceeds_field_ceiling` with `POSITIVE_INT_FIELD_MAX`, plus a
range check (1–3650, or blank for null), and refuses with its own copy before `.save()`.

### 3.12 Audit actions, and the detail toggle

Four names added to the closed tuple in `identity/contracts/actions.py`, in a `content.`
namespace — naming what changed, not which table, the convention that module's own comments
already argue for:

```
CONTENT_DELETED  = "content.deleted"
CONTENT_RESTORED = "content.restored"
CONTENT_PURGED   = "content.purged"
CONTENT_HELD     = "content.held"
```

Written through `identity/audit.py::record()`, the only permitted writer, with
`target_type=<ticket kind>` and `target_key=<item key>`. `detail` carries the kind and, on a
purge, `removed={label: count}` — integers, content-free.

**The toggle reads exactly as the owner ruled.** `IdentitySettings.audit_detail`, default False.
OFF: `target_label=""` — the event says a conversation with this id was deleted by this actor at
this time, and that event is written and rendered regardless. ON: `target_label` carries the
item's title, the field that already holds titles and usernames today. Nothing else changes; no
event is suppressed by the toggle in either direction, because an audit trail with a switch that
turns rows off is not an audit trail.

`identity/audit.py` gains ONE reader, `by_action(actions, limit=100)`, beside `recent` and
`for_target` — here, not in the page, for the reason the module's own docstring gives: a page
that had to name `AuditEvent.objects` would need an exception to the AST guard, and a guard with
an exception is a guard somebody widens.

### 3.13 The Deleted page

One page, in the settings area, all kinds. Route name `identity-deleted`, URL
`/settings/deleted/`, view in `identity/views.py` beside the other identity settings pages,
because the ticket table is identity's.

**Registration — the five places that must agree** (§2.3), all in the same commit:

1. `foundation/settings_area.py::SETTINGS_GROUPS` — a new group `("Your content", (Entry("Deleted",
   "identity-deleted", EVERYONE),))`, placed after `Setup` and before `Access`.
2. `foundation/templates/_settings.html` — the same group, the same order, ungated like
   `Install guides`.
3. `foundation/settings_help.py` — a `HelpCard` with `gate=EVERYONE`, compared for equality
   against the `Entry.gate` by `foundation/tests/test_settings_help.py`.
4. `identity/routes.py::ROUTE_RULES` — class **A**: the page lists the viewer's own tickets and
   addresses no row in its URL, the identical shape `chat-all` carries. Two POST routes beside
   it, `identity-deleted-restore` and `identity-deleted-purge`, class **O**: row-addressed
   mutations of owned content, refused with 404 for a principal with no standing, the shape
   `chat-conversation-delete` already has.
5. `foundation/tests/test_page_names.py::_NAMES`.

`EVERYONE`, not `ADMIN`: this page is a person's own deleted items, and on a box with accounts a
member is exactly who needs it. The one consequence, recorded rather than discovered: the
sidebar renders on the single PUBLIC page in the settings area (`setup-index`, class P), so an
anonymous visitor sees the entry and is redirected to sign in when they click it — the same
thing every app-bar link on that page already does (`foundation/templates/_shell.html` renders
Chat, Ask, Document library, Ask history and Queue unconditionally).

**Two tabs.**

- **Deleted** — open tickets, newest first: what it was (kind and label), who deleted it, when,
  and **"Purge on 21 October 2026"**. Per row: **Restore**, and **Delete permanently** where the
  posture and the principal allow it (§3.10). Enterprise adds **Hold** for an owner-role holder,
  and a held row reads "On hold — the purge date is suspended" instead of a date.
- **Purged** — content-free audit events, `by_action((CONTENT_PURGED, CONTENT_DELETED,
  CONTENT_RESTORED, CONTENT_HELD))`, rendered as "Conversation 59608c35-… deleted at 14:32" and
  visible in every posture with the toggle in either position. With `audit_detail` on, the same
  lines carry the labels.

**Copy is plain, and declared once in Python** (the house rule): "Deleted", "Restore", "Delete
permanently", "Purge on <date>". The word *purge* appears in code, in this spec, and in the date
line's verb — and nowhere else in the interface; there is no "purge queue", no "retention
cliff", no "ticket" in anything a person reads.

### 3.14 Synchronous completeness

The demo requirement, stated as the mechanism that delivers it:

- **"Delete permanently" purges inline, in the request that handled the click.** The POST view
  calls `purge_ticket`, which runs every registered handler inside one `transaction.atomic()`
  and returns before the redirect. There is no queue job, no worker hop, no `on_commit` hook.
- **Queue rows go in that same transaction.** `models.queue.retention.forget_conversation` is a
  registered handler like any other, invoked by the same runner inside the same atomic block —
  which is exactly what the import law made impossible from `agents/`, and exactly why the
  orchestration lives in `identity/`.
- **No cache to go stale.** Nothing in this design caches a ticket, a visibility answer or a
  settings row across requests. `IdentitySettings`' own docstring states the rule this inherits:
  *"a cache would be a second truth with a staleness window."*
- **Therefore**, after the redirect: the conversation is absent from the chat list and the
  conversations browser (`visible_conversations`), absent from the Queue page (the rows are
  gone, not merely hidden by `may_read_job_content`), its chat-scoped documents and their chunks
  and bytes are gone, its generated images and their files are gone, its Ask-history rows —
  where the person deleted those too — are gone, its tool-call words are blanked, its staging
  note file is unlinked, and one content-free event stands on the Purged tab.

---

## 4. Data model and migrations

**Exactly two migrations.**

1. **`identity/migrations/0004_deletion_ticket_and_retention_settings.py`** — creates
   `DeletionTicket` (§3.2) and adds three fields to `IdentitySettings`:

   | Field | Type | Default | Bound (in the writer) |
   |---|---|---|---|
   | `retention_days` | `PositiveIntegerField` | `30` | 0–3650; `0` is legal and means purge on delete |
   | `tool_record_extra_days` | `PositiveIntegerField` | `0` | 0–3650 |
   | `audit_detail` | `BooleanField` | `False` | — |

   All three are edited on `identity-settings` (Identity & security), which is where the posture
   and `admin_sees_content` already live, and all three are audited under ONE new action,
   `RETENTION_POLICY_CHANGED = "identity.retention_policy_changed"`, with `detail` carrying
   `field` and `to`. That follows `LIBRARY_SETTINGS_UPDATED`'s recorded rule — one action per
   settings DOMAIN, the literal column in `detail` — rather than the three-way split
   `POSTURE_CHANGED` / `LIBRARY_POSTURE_CHANGED` / `ADMIN_CONTENT_ACCESS_CHANGED` uses, because
   those three are semantically distinct security postures and these three are one retention
   policy expressed as three knobs. That makes five new action names in total, one of them a
   settings action rather than a content one; §11 records the choice.

   Queue retention is **not** on this row (§11): the queue owns its own cliff.

2. **`models/queue/migrations/0006_jobsettings_retention_days.py`** — one nullable field (§3.11).
   `0005` is the queue steward's, whose PR lands first (§9).

**No per-model soft-delete columns, and the reasons are three.** (a) Four tables in three columns
means four migrations and four places to forget an exclusion; the ticket means one table and one
exclusion function. (b) A cliff, a hold, an actor and a deferred second cliff are facts about the
DELETION, not about the conversation — a `deleted_at` column on `Conversation` would need four
companions, and then the same five on `Document`. (c) The Deleted page is one query over one
table; with per-model columns it is a union over four querysets in three columns that
`identity/` may not import. The cost is the join §3.4 pays — a bounded materialised key list per
visibility call — and that cost is stated, budgeted and tested rather than hidden.

**No `PROTECT` flip anywhere.** `Conversation.agent`, `Conversation.workstream` and
`Document.workstream` keep the `PROTECT` their authors recorded reasons for; nothing in this
design deletes through them.

---

## 5. Error handling and edge cases

| Case | Answer | Where |
|---|---|---|
| The file is already gone (note, store dir, generated dir) | Not an error. Every file removal is best-effort and idempotent: `store.remove_document_files`, `store.remove_job_files` and `store.remove_engine_files` already are, and `purge_conversation_notes` uses `missing_ok`. A purge that failed because somebody had already cleaned up would be a purge nobody could finish. | §3.5, §3.6 |
| A handler raises mid-purge | The runner does not swallow. The savepoint restores the connection, the outer `transaction.atomic()` rolls back every ROW change, the ticket survives, and the view renders the refusal sentence. Files already removed by an earlier FILES-band handler stay removed — named, not hidden — which is why handlers must be idempotent and why the next sweep completes the purge. | §3.5 |
| Restore after a partial purge | **Impossible, and refused explicitly.** Once `purged_at` is set the content is gone and there is nothing to restore; `restore_content` raises with "This item has already been deleted permanently and cannot be restored." The ticket remains only until the deferred tool-record scrub completes. A purge that rolled back before setting `purged_at` leaves the ticket restorable, which is correct: no rows were lost. | §3.3, §3.8 |
| A hold on a ticket past its cliff | The hold wins. The sweep's due-query excludes held tickets outright, so a held ticket sits past its date indefinitely and the page reads "On hold" instead of a date. Clearing the hold makes it due on the next sweep — which the page's own prune-on-read runs. | §3.9, §3.10 |
| The cliff is changed after tickets exist | **`purge_on` is NOT recomputed.** It is computed once, at delete time, from the setting in force then. The page printed a date and that date is a promise; silently moving it — in either direction — would make the promise worthless, and moving it EARLIER would destroy content sooner than the person was told. A changed `retention_days` governs future deletes only, and the settings page says so in its help text. | §3.2, §8 |
| A posture switch with tickets pending | Tickets are posture-independent data; nothing is migrated. Switching TO enterprise withdraws "Delete permanently" from every pending ticket and offers Hold; switching AWAY from enterprise leaves existing holds standing (a hold is a recorded decision, not a posture artefact) and lets an administrator clear one. `identity/services.py::set_posture` gains no retention branch at all. | §3.10 |
| Deleting a conversation whose documents are shared universally | The documents survive. Only `scope=conversation` documents die with their conversation — the invariant `tools/rag/access.py::delete_attachments` already depends on (exactly one attachment row, for that conversation). A universal or stream-contained document loses only its attachment CLAIM, exactly as today. | §3.4, §3.6 |
| Workstream delete | **Unchanged.** It still refuses while the stream holds conversations (`PROTECT`), still deletes shares and pins, still audits `WORKSTREAM_DELETED`. Streams are not a ticket kind (§10). | §10 |
| A `sees_all_content` principal viewing tickets | Sees every ticket, including other people's, and may restore or permanently delete them where the posture allows — the same predicate that already lets them read the content. On an open box that is every principal, which is correct: there is nobody for anything to be hidden from. An administrator with the content setting OFF sees only their own, and the labels on the Purged tab stay empty for them regardless of `audit_detail`. | §3.10 |
| Two deletes of the same item race | `get_or_create` on `(kind, key)`, backed by the unique constraint: the second is a no-op returning the first ticket, and writes no second audit event. | §3.2 |
| The item was hard-deleted by an older path while a ticket stood | Every handler is a filtered delete or update, so it removes zero rows and returns zero. The purge completes, the ticket goes, the audit event stands. | §3.5 |

---

## 6. Backups are their own layer

`docs/OPERATIONS.md` gains one section, **"Deleted content and your backups"**, placed after
"What a backup contains". Its content, as it must read:

- A backup is a **copy of content that no delete path on this box reaches.** The retention cliff
  governs the live box. It does not, and cannot, reach into a backup set that was written before
  the delete.
- **There is no retroactive purge of existing backup sets**, and this platform will not offer
  one: rewriting a `pg_dump` in place would make every backup's integrity unverifiable, and
  selectively deleting from a file copy would leave a set that no longer restores to a coherent
  box.
- Deleted content therefore leaves your backups **as rotation ages them out**, on whatever
  schedule you keep. An operator with a retention obligation sets the backup rotation to match
  the deletion cliff; the two numbers are independent and this platform will not pretend
  otherwise.
- **Preview stacks are not a backup layer** and are not covered by the cliff either. A preview
  stack (`compose.preview.yaml`, `data/preview/`) is a full parallel copy with its own database;
  deleting something on the live box does not touch it. Tear a preview stack down when you are
  done with it.
- What a deletion leaves in a *new* backup taken after the purge: the content-free
  `AuditEvent` rows, which are in the dump like every other identity row, and nothing else.

The section names no absolute path and adds no new backup behaviour: `foundation/ops/backup.py`
is untouched by this feature.

---

## 7. Tests

Same commits as the code (`AGENTS.md` non-negotiable 1). The gate is the four pytest runs across
the `FARABUNKER_FEATURES` matrix in both collection orders, **plus the two posture-sweep runs**,
because this is identity, posture and visibility work in every column it touches.

**`identity/` — the ticket, the registry, the service.**
- `DeletionTicket` refuses an unknown `kind` at `save()`, the way `AuditEvent` refuses an
  unknown action; the unique constraint makes a second ticket for the same `(kind, key)` a
  `get_or_create` no-op with no second audit event.
- `delete_content` with `retention_days=30` writes a ticket with `purge_on` 30 days out and
  purges nothing; with `retention_days=0` the content is gone before the call returns.
- `restore_content` deletes the ticket and writes `CONTENT_RESTORED`; it refuses a ticket with
  `purged_at` set, with that exact sentence.
- The registry: registration is idempotent (the sibling registries' rule), handlers sort ROWS
  before FILES regardless of registration order, and a handler registered with a non-dotted path
  raises at registration.
- The runner **never swallows**: a handler that raises aborts the purge, the ticket survives,
  and the connection is usable afterwards — asserted by performing a real query after the
  failure, which is the only way the savepoint's purpose is actually tested.
- Re-running a purge after a mid-purge failure completes it (idempotence, as a contract test
  every column's handler is run against).
- `ticketed_keys` costs one query and its result is a list, not a queryset.
- The sweep: bounded at `SWEEP_LIMIT`; skips held tickets; picks up `tool_purge_on`; one failing
  ticket does not stop the batch; `manage.py purge_deleted` writes `source="cli"` events.

**`agents/`.**
- `purge_conversation` removes shares, attachments, the conversation and its turns; collects
  `invocation_id`s BEFORE the delete and blanks `args`/`text`/`error` while leaving the shell
  (principal, tool key, outcome, timings) intact.
- Artifact collection: `output:`/`input:` refs are parsed and deduped; an unparseable ref is
  dropped and logged, never raised; `data["id"]` is collected from a tool turn **whose job
  FAILED and which therefore has no artifacts at all** — the steward's gap, pinned as a test
  rather than as a comment; a turn with `data=None` contributes nothing.
- `visible_conversations` excludes a ticketed conversation **for `sees_all_content` too** —
  asserted in the open posture, where that branch is every principal.
- Query-count equality between one conversation and twenty-five, with and without tickets.
- `agents/visibility.py` imports nothing from `models.queue`; the import-law gate covers the
  general rule, this pins the direction this feature could have broken.

**`tools/rag`.**
- `purge_document` leaves **zero** rows in `data_rag_chunks` for that `file_id` — the audit's
  refutation, re-verified rather than assumed — and zero files in the managed store.
- `purge_ask` deletes the record; `visible_ask_records` excludes a ticketed one.
- `readable_documents` / `listable_documents` / `attached_documents` exclude ticketed documents,
  and hide a chat-scoped document whose conversation is ticketed while leaving a universal one
  attached to that same conversation fully readable.
- `purge_conversation_notes` unlinks the note file, deletes the note `Document`, and is a no-op
  when the file is already gone.

**`tools/vision`.**
- `visible_jobs` excludes a ticketed job.
- The registered artifact purge maps `output:<id>` and `input:<id>` to their jobs through the FK,
  dedupes two outputs of one job to one delete, accepts a bare generation id, ignores a UUID that
  matches no job, and calls `delete_job` — so the managed directory goes and the engine-side
  sweep runs.
- The queue rows for those jobs are gone afterwards, via `forget_jobs`.

**`models/queue`.**
- `forget_conversation` deletes `agent.turn` AND `rag.consolidate` rows naming that conversation
  and touches no other row; `forget_document` the same for `rag.ingest`.
- `models/queue/retention.py` imports nothing from `agents` — asserted directly, in this
  column's own tests, not left to the repo-wide gate.
- `_prune_finished_jobs` with `retention_days=1` removes a terminal row finished two days ago,
  keeps one finished an hour ago, and **never** touches a queued or running row however old.
  `retention_days=None` reproduces today's FIFO-only behaviour byte for byte.
- `forget_jobs` on the contracts seam dispatches through `INFERENCE_QUEUE_BACKEND` and is a
  no-op for an id that does not exist.

**Guards that must stay green, each asserted after the change.**
- `foundation/ops/tests/test_column_boundaries.py`'s AST audit guard: `identity/audit.py` is
  still the ONLY module performing any `AuditEvent.objects` access. The new `by_action` reader
  is inside it precisely so this stays true, and a test asserts the new views and the new
  service reach the trail only through `identity.audit`.
- `foundation/ops/tests/test_import_law.py`: no new file imports `models.queue.models`.
- Settings drift: `foundation/tests/test_settings_area.py`, `test_shell.py` (the rendered
  sidebar's first link still matches where `/settings/` redirects the same viewer),
  `test_settings_help.py` (equal gates, and the card's anchors exist on the page it names),
  `foundation/tests/test_page_names.py`, and `identity/tests/test_route_matrix.py` classifying
  all three new names with drivers for each.
- Never-500 on a forged ticket id, a ticket belonging to somebody else, and a POST to
  `identity-deleted-purge` in the enterprise posture.

**The demo, as one end-to-end test** (`identity/tests/test_deletion_demo.py`), personal posture,
in this order, asserting at each step:
1. A conversation with a turn, a chat-scoped document, a generated image and an Ask record.
2. Delete the conversation → absent from `visible_conversations`; present on the Deleted page
   with a `Purge on <date>` line carrying the right date; the `agent.turn` queue row still
   exists (nothing has been purged yet) but the conversation renders nowhere.
3. **Delete permanently** → in the same response cycle: absent from the chat list; **zero**
   `InferenceJob` rows naming that conversation; the generated image's row, files and queue row
   gone; the chat-scoped document, its chunks and its bytes gone; `ToolInvocation.args`/`text`
   empty with the row still present; the note file gone.
4. The Ask record is deleted on its own and is absent from `HistoryView`'s context.
5. A `content.purged` event is on the Purged tab with the kind, the key, the actor and the time
   — **with `audit_detail` OFF**, and with `target_label` empty; flipping the toggle and
   deleting a second item shows the label on that one only.

---

## 8. Documentation

Same commit as the code it describes, per non-negotiable 1.

| Document | What it gains |
|---|---|
| `docs/OPERATIONS.md` | The "Deleted content and your backups" section (§6), and one line in the retention discussion pointing at `manage.py purge_deleted` for an operator who wants a cron rather than relying on prune-on-write |
| `docs/adr/0019-deletion-and-retention.md` | **The next number** (the record runs to `0018-settings-assistant.md`). The policy decision: delete means delete; one ticket table rather than per-model columns; the retention namespace on the existing cascade registry; posture-keyed policy; the content/audit split; backups as their own layer; the named residue (§10) |
| `docs/EXTENDING.md` | A new recipe, **"Registering a retention handler"**, beside "Adding an entitlement axis": the `RetentionHandler` fields, the two order bands and when to use each, the `(key, *, commit) -> int` signature, the idempotence obligation, the one-line `AppConfig.ready()` registration, and the two tests a new handler owes (count-then-commit agreement, and re-run-after-failure) |
| `identity/README.md` | The ticket table, the orchestration, the Deleted page, and the sentence that identity answers "which keys are deleted", never "which conversations" |
| `agents/README.md` | `agents/retention.py`, the new artifact-purge slot, and the tool-record scrub's rule |
| `tools/rag/README.md`, `tools/vision/README.md` | Their handlers, and what each one does and does not reach |
| `models/README.md` | The payload-keyed queue handler, the age cliff, and `forget_jobs` on the contracts seam |
| `foundation/settings_help.py` | The Deleted page's `HelpCard` — what the page is for, what Restore and Delete permanently do, that the purge date is fixed at delete time and a changed setting governs future deletes only, and that backups are a separate layer |

No document names a model or a vendor; `test_docs_model_names.py` walks the planning archive
too, so this spec is inside its reach.

---

## 9. Delivery

Branch off `origin/dev`, pull request into `dev`, merged on the owner's word, with the
merge-readiness gate's whole-feature UAT walked by hand on this branch's preview stack.

### Slices, exactly as the brief has them

**Slice 1 — personal-posture hard purge, conversation kind, end to end.**
The identity ticket, the three settings fields, the retention namespace on the cascade registry,
the runner, the audit actions and the audit reader; the agents handler (turn/share/attachment
rows, the tool-record scrub, the artifact collection and the vision hand-off); the rag notes
handler; the vision artifact-purge registration; the Deleted page with both tabs; the sweep and
the management command; and the documentation set (`OPERATIONS.md` backups section, ADR 0019,
the column READMEs, the `EXTENDING.md` recipe, the help card). **The queue half is a separate,
late task inside this slice** (below).

**Slice 2 — documents and Ask history.**
The library delete becomes a ticketing delete; an Ask-history delete route and the affordance on
`HistoryView`'s page (which stops being read-only for the first time, and its docstring says so);
both kinds' rows on the Deleted page; the gallery's own delete and bulk delete routed through the
retention service for the `vision_job` kind.

**Slice 3 — enterprise.**
Hold, the owner-set cliff, no early purge, and the `audit_detail` labels.

### Sequencing constraints

- **The `models/queue` half lands after the queue steward's PR.** Their migration is `0005`;
  ours becomes `0006`. That half is `models/queue/retention.py`, the `_prune_finished_jobs` age
  condition, `JobSettings.retention_days`, its settings-page writer and bound, and `forget_jobs`
  on `models/contracts/queue.py`. It is isolated as its own task (or two) at the end of slice 1,
  and until it lands the conversation purge is complete in every column but the queue — a state
  the task's own tests assert rather than leave ambiguous.
- **Second lander merges `dev`.** `agents/visibility.py` is under heavy edit on the chat-cluster
  branch, and the settings sidebar is a shared surface; whichever of the two lands second merges
  current `dev` into its branch, resolves there, and re-runs its full gate — the rule
  `AGENTS.md` states and the brief repeats.
- **Clearance packets pre-merge, not pings after.** `tools/vision` has given precision notes and
  is CLEAR; the diff against `tools/vision/visibility.py`, `services.py` and `apps.py` goes to
  that steward before merge. The `agents/` and `tools/rag` diffs go to theirs the same way.
  `tools/rag` is clear today.
- **Announce the settings-area registration** before it merges: `SETTINGS_GROUPS`, the sidebar
  and the help table are three files other sessions also edit.

### Import-law placement of every new module

Downward only, through `identity → foundation → models → agents → tools`:

| New or amended | Column | May import | Must not |
|---|---|---|---|
| `identity/contracts/retention.py` (new, pure) | identity | nothing but the standard library | Django, any project module |
| `identity/contracts/cascades.py` (amended) | identity | unchanged — stays pure | — |
| `identity/cascades.py` (amended) | identity | Django's `import_string` | `agents`, `tools`, `models` |
| `identity/retention.py` (new) | identity | `identity.*`, Django | `agents`, `tools`, `models` |
| `identity/views.py`, `urls.py`, `routes.py`, templates, `management/commands/purge_deleted.py` | identity | `identity.*` | as above |
| `agents/contracts/artifacts.py` (one slot) | agents | stays pure | — |
| `agents/retention.py` (new) | agents | `agents.*`, `identity.*`, `models.contracts.*` | `tools.*`, `models.queue.*` |
| `tools/rag/retention.py`, `tools/vision/retention.py` (new) | tools | `identity.*`, `models.contracts.*`, `agents.contracts.*`, own column | `models.queue.models` (rule 2) |
| `models/queue/retention.py` (new) | models | `models.queue.*`, `identity.*` | **anything under `agents.` or `tools.`** |
| `models/contracts/queue.py` (one passthrough) | models | unchanged | `models.queue.models` |

Every cross-column reach in this feature is either a dotted-path registration resolved at run
time or an existing sanctioned seam. **No new import crosses a boundary in a direction the law
forbids, and no Django signal is used anywhere** — `delete_conversation`'s own docstring already
records why (*"this repository uses no Django signals anywhere"*), and a `post_delete` receiver
would be exactly the wrong mechanism for a delete that must be explicit, counted and audited.

---

## 10. Out of scope, named

1. **Retroactive purge of existing backup sets.** Never offered (§6). Backups age out on
   rotation.
2. **Workstream retention.** `delete_workstream` is unchanged: it still refuses while the stream
   holds conversations, and `workstream` is deliberately not a ticket kind. A stream is a
   container whose contents each have their own cliff; giving the container a second, independent
   cliff is a design question with a real answer nobody has asked for yet.
3. **Engine-side files not tracked by a row**, beyond `delete_job`'s existing best-effort sweep.
   `/engine/input` and `/engine/output` are bind mounts this platform tracks with no row at all;
   the Engine files admin page remains the manual door, and its `ENGINE_FILE_DELETED` audit
   action is untouched.
4. **Per-user retention overrides.** The cliff is a box policy on the `IdentitySettings` row. A
   per-person or per-entitlement cliff needs a second table and a precedence rule; not now.
5. **Export before delete.** No "download your data" step. Delete is delete, and an export
   surface is its own feature with its own gating.
6. **`rag.ask` queue rows keyed to an Ask record.** No link exists in either direction (§3.6);
   those rows are cleared by the queue's age cliff and nothing keys them to a purge.
7. **A generation whose tool turn was never written** (§3.7): accepted residue, reachable only
   through the gallery.
8. **`Turn.tool_call`'s replayed args as a separate scrub target.** They die with the turn by
   CASCADE; there is no case where a turn survives its conversation.

---

## 11. Decisions the author made

Each is a place the brief left a choice, with what was chosen and why.

1. **Queue retention lives on `JobSettings`, not on `IdentitySettings`.** The brief listed a
   `queue_retention_days` field on the identity row AND a `JobSettings.retention_days` in the
   queue paragraph. Two homes for one number is a drift source; `JobSettings` wins because the
   queue's own writer, its own settings page, its own bound and its own `_prune_finished_jobs`
   are all there, and `models/queue/backend.py` reading `IdentitySettings` on every `enqueue()`
   would add a read to the hottest write path in the platform for a number that is queue policy.
   `IdentitySettings` therefore gains three fields, not four.
2. **The registry gets an explicit `order` field with two named bands, rather than relying on
   registration order.** `AppConfig.ready()` order is not a contract, and "filesystem last" is a
   correctness rule about the one operation no transaction can undo. Two values (`ORDER_ROWS`,
   `ORDER_FILES`) are enough because the only true dependency — read the rows before deleting
   them — lives inside one handler, where it is visible.
3. **Vision's queue rows go through one new passthrough on `models/contracts/queue.py`
   (`forget_jobs`), not through the payload-keyed handler.** The brief's payload-keying works for
   `agent.turn`, `rag.consolidate` and `rag.ingest`; a `vision.generate` payload carries no
   generation id, because the job row is created at run time, and the only link is
   `GenerationJob.queue_job_id`. The contracts seam is the door every tool column already uses
   for the queue, so this adds a function, not an exception.
4. **`purge_on` is never recomputed when the retention setting changes.** The date printed on
   the page is the promise; moving it earlier would destroy content sooner than the person was
   told, and moving it later would make the page's own history a lie. The setting governs future
   deletes only, and the help card says so.
5. **The deferred tool-record scrub is carried on the ticket (`purged_at`, `tool_purge_on`,
   `deferred_ids`), not on `ToolInvocation`.** A per-row scrub date would be a fifth column on a
   table this design otherwise only updates, and a third migration. One row with two dates keeps
   the "no per-model columns" rule intact and gives the sweep a single due-query.
6. **The Deleted page is gated `EVERYONE` in a new "Your content" settings group, route class
   A.** Every existing settings entry is `ADMIN` or `ACCOUNTS_ADMIN`; this page is a person's own
   deleted items, so a member must reach it. The consequence on the one public settings page is
   recorded in §3.13 rather than left to be discovered.
7. **Settings writes for the retention policy are audited under one new action
   (`identity.retention_policy_changed`) with the field in `detail`**, following
   `LIBRARY_SETTINGS_UPDATED`'s "one action per settings domain" rule rather than splitting three
   knobs into three actions the way the three security postures are split. That makes five new
   action names, not four; the fifth is a settings action, not a content one.
