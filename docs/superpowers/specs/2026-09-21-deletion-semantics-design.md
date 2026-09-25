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
**Columns touched:** `identity/` (the ticket, the whole retention policy, the orchestration, the
page), `agents/` (one contract slot, one retention module), `tools/rag`, `tools/vision`,
`models/queue` (a payload-keyed handler, an age condition on the existing prune, one new
non-creating read across the identity seam — **no migration**) + `models/contracts/queue.py`
(one passthrough), `foundation/` (three registration tables, and one new coverage gate, §7).
**Migrations:** **exactly one**, in `identity/` — the ticket table plus the three retention
policy fields on `IdentitySettings` (§4). `models/queue` gains no column at all.
**Lands as:** two slices on one branch off `origin/dev`, in the brief's order.
**What a maintainer sets up to get this:** nothing. Three optional settings, all with working
defaults, in one section of a page that already exists (§4).
**Open questions:** none. Every call is made; §11 lists the ones the owner may want to overrule.
§11.1 is an owner ruling that already overrode the author's first draft, and §11.8 is the
owner's cost/benefit ruling that cut three mechanisms out of the design (§3, §10).

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
   SCRUBBED inline with their conversation's purge, always. The owner first asked for an
   OPTIONAL LONGER RETENTION for these records — extra days after the item's own purge, off by
   default — and then **withdrew that request on 2026-09-21 under the cost/benefit ruling**
   (§11.8): it would have bought three nullable fields on the ticket, a second due-condition on
   the sweep, a ticket state in which an item is neither restorable nor gone, and a fourth
   setting, in exchange for a retention nobody on any box has asked for yet. There is one
   cliff, and the tool records go over it with the conversation. → §3.6, §3.8; the withdrawn
   option is named in §10.9 so a real need can pick it back up cheaply.
2. **Queue rows for a deleted conversation are DELETED**, in the same request — after the
   queue's own cancel path has closed any row that is not terminal yet (§3.6). The queue also
   gets a natural cliff of its own — finished rows older than the queue's retention (default
   one day) go regardless. That number is `IdentitySettings.queue_retention_days`, one of the
   three fields of the single centralised retention policy (**owner ruling, §11.1**); the queue
   reads it, it does not own it. → §3.6, §3.11, §11.1.
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

### 3.0 The design principle this whole section is held to

**A maintainer installs this and has to set up NOTHING.** Every option has a working default;
there are as few options as the feature can honestly have. That is the owner's ruling of
2026-09-21 (§11.8), in his own words:

> I want to make sure we achieve a good cost/benefit associated with our changes/complexity,
> and that we don't add so many features/options it paralyses the maintainer because they have
> to set up so many options.

Read as a test the rest of this document must pass: a box that is installed and never
configured deletes content correctly, purges it thirty days later, keeps a content-free audit
trail, and shows a Deleted page — with no field filled in, no command scheduled, and no posture
decided. Three optional settings exist (§4) and every one of them is a number somebody might
want to change, not a number somebody must supply. Where this design could have offered a
fourth knob, a second mode or a second cliff, §10 names what was left out and what it would
cost to add if a real need appears.

### 3.1 Shape in one paragraph

Deleting anything writes ONE row in ONE new identity table — the deletion ticket — and an audit
event. Every column's existing visibility function excludes ticketed keys, so the item vanishes
from every surface immediately. The ticket carries the date the content will actually be
destroyed. On that date — or the moment a person clicks "Delete permanently" — the kind's
registered handlers run through the EXISTING cascade registry, the content is gone, the ticket
is gone, and a content-free audit event remains. **A ticket exists exactly while the item is
restorable**: there is no purged-but-pending state, no second cliff and no ticket that outlives
its content. No per-model soft-delete column, no second registry, no scheduler, no cache.

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
| `hold_by_kind` / `hold_by_key` | `CharField`, blank | Set = the cliff is suspended. **The FIELDS ship in this delivery; the BEHAVIOUR that sets them does not** — see the box below. |
| `hold_note` | `TextField`, blank | Why. Operator prose, not content. Same: field now, behaviour later. |

**The hold fields ship empty, and the sweep already honours them.** Nothing in this delivery
writes `hold_by_kind`, `hold_by_key` or `hold_note` — the control that would set them is the
deferred enterprise slice (§10.10). They are created by the one migration anyway, and the
sweep's due-condition already excludes a held ticket (§3.9), for one reason: the alternative is
a second migration later against a table that by then holds live tickets, to add three blank
columns. Three blank columns cost nothing; a migration on live deletion bookkeeping is a risk
with a maintenance window attached. This is the only place in the design where something is
built before it is used, and it is stated here rather than discovered in the model file.

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
- **Restore** = delete the ticket, `record(actor, CONTENT_RESTORED, …)`. Nothing else: the item
  was never modified, so there is nothing to put back. That is the whole return on not adding
  per-model columns. **Every ticket that exists is restorable** — the ticket and the item live
  and die together — so restore has exactly one refusal to make, and it is not made in this
  delivery: a held ticket, once the deferred enterprise slice (§10.10) can set a hold.
- **Purge** = inside one `transaction.atomic()`: run the kind's registered handlers (§3.5),
  delete the ticket, `record(actor, CONTENT_PURGED, removed=counts)`. The counts are the
  per-handler integers the handlers returned: content-free by construction. The ticket row is
  destroyed by the same transaction that destroys the content, so the two can never disagree.

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
    label: str      # "Conversation and turns" — the key in the audit event's `removed` map
    handler: str    # "package.module.function"
    order: int = ORDER_ROWS

register_retention_handler(spec) -> None
retention_handlers(kind) -> list[RetentionHandler]   # sorted by (order, registration index)
```

**Handler signature: `(key: str) -> int`. One mode.** It removes this column's share of the
item and returns how many things it removed. That integer has exactly one consumer: the
content-free `removed={label: count}` detail on the `content.purged` audit event (§3.12).
There is no count-only mode, no `commit` flag, no count shown before a confirmation, and no
per-item count line on the Deleted page.

**Why this departs from `EntitlementCascade`'s two-mode shape**, since that registry is the
one being extended and a reader will notice the difference. `EntitlementCascade` needs
`commit=False` because deleting an entitlement is irreversible the instant it is confirmed —
the count IS the confirmation, the only chance the operator gets to see how much a delete
reaches before it happens. A deletion ticket has a better confirmation than any number: **the
Deleted page itself**, where the item sits, named and restorable, for as many days as
`retention_days` says. A person who wants to know what "Delete permanently" will destroy can
restore the item and look at it. Adding a dry-run mode here would double every handler's
surface, double its tests, and buy a number nobody needs to read — precisely the kind of
complexity the owner's ruling (§11.8, §3.0) told this design to leave out.

What does NOT change with the mode: **every retention handler must still be IDEMPOTENT**, and
the ROWS/FILES order bands below are unchanged. Idempotence carries more weight now, not less
— it is the whole recovery story for a purge that failed part-way.

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

**Failure semantics.** `identity/cascades.py` gains ONE runner, `run_retention(kind, key)`,
built on one private `_run_retention` — `_run`'s shape, with `_run`'s two-mode branch gone. It
**never swallows**: a handler that cannot be imported, or that raises, takes the whole purge down,
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
| ROWS | `queue.conversation_jobs` | `models.queue.retention.forget_conversation` | **Cancels** any non-terminal `InferenceJob` whose payload names this conversation through the queue's own cancel path, then deletes every row it names (§3.7) |
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
4. **The tool-record scrub** (§3.8), inline, on the invocation ids collected in step 1 —
   collected FIRST because `Turn.invocation` is `SET_NULL`, so after step 3 there is no path
   from the conversation to its invocations at all. That `SET_NULL` is deliberate (the
   2026-08-27 addendum's consequence 3: an audit row is not owned by the conversation table)
   and it is not being changed; the scrub empties the content fields and leaves the shell. It
   happens in this transaction, on this date, every time — there is no deferral and no setting
   that moves it (§1 ruling 1, §10.9).

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
register_artifact_children(dotted_path: str) -> None
artifact_children() -> str | None
```

Handler signature `(refs: Sequence[str], generation_ids: Sequence[str]) -> list[str]` — job keys,
which the delete turns into tickets of their own, rather than an `int` handed to a destroyer: each
generation this resolves is given a deletion of its own, not destroyed silently on the
conversation's own date.
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

**A live row is CANCELLED before it is deleted, never deleted out from under a running turn.**
This is a binding condition from the queue steward, and it is a correctness rule, not a
courtesy. `agents/runtime/jobs.py::on_turn_terminal` — the `on_terminal` hook
`models/queue/backend.py::cancel_job` schedules on a successful cancel — is what closes a
stranded placeholder assistant turn; its own docstring names the three handler-never-started
paths it exists for. A purge that simply deleted a queued or running row would skip that hook
entirely, and the turn's poller (`agents/chat/views/turns.py::_queued_body` / `_running_body`,
each calling `models.contracts.queue.get_job`) would see the job vanish mid-poll — which reads
as a database fault, not as a delete. So `forget_conversation`, for every non-terminal row it
matched:

1. calls the queue's EXISTING `models/queue/backend.py::cancel_job` — an intra-column call, so
   no new passthrough on `models/contracts/queue.py` is needed for it. The queued→cancelled
   transition stays that function's single conditional `UPDATE`, and its `on_terminal`
   scheduling stays exactly as it is;
2. deletes the row only once it is terminal. A row that came back `"cancelled"`, `"unknown"` or
   `"already_finished"` is deletable in this same call;
3. **refuses on `"already_running"`.** A worker holds that job; nothing here may delete it.
   `forget_conversation` raises, which — the runner never swallowing (§3.5) — aborts the purge
   with the ticket intact and every row and byte untouched, because this handler is `ORDER_ROWS`
   and registered FIRST for the kind, so it runs before any other handler has done anything.
   The view renders the refusal sentence; the cliff sweep simply retries on its next pass. The
   item stays invisible the whole time (§3.4) — the promise is kept even while the purge waits.

The `on_terminal` hook scheduled in step 1 fires on commit of `purge_ticket`'s outer
`transaction.atomic()` — after the turns are already gone — and `on_turn_terminal` is one
conditional `UPDATE` filtered on the states a stranded turn can be in, so it matches zero rows
and no-ops. That is the existing ordering preserved, not a new one. §5 carries the edge case.

**Rows are DELETED, not scrubbed** — owner ruling 2. The audit's own open question weighed
keeping the row for its timings and `model_refs`; the ruling is that a finished job's
bookkeeping is not worth a table of half-erased rows, and the queue's own cliff removes finished
rows on an age basis anyway (§3.11), so a scrub would only defer the same delete.

### 3.8 The tool-record scrub

`agents/retention.py::scrub_tool_records(invocation_ids)` sets `args={}`, `text=""`,
`error=""` on those `ToolInvocation` rows in one `update()`. The shell — principal, agent slug,
tool key, outcome, timings, `queue_job_id` — stays, because that shell IS the machine audit
trail `identity/contracts/actions.py`'s own docstring points at when it explains why tool calls
are absent from the `AuditEvent` catalogue: *"tool calls already have a better record in
`agents.models.ToolInvocation`… the first must be kept and the second must be prunable."*
Scrubbing the words and keeping the record is that sentence, implemented.

**One cliff, no second date, no setting.** The scrub runs inline in step 4 of the conversation
handler, inside the purge's own transaction, and the ticket is deleted at the end of the same
purge. A conversation's words and its tool records go over the same cliff on the same day,
which is also the date the Deleted page printed.

The owner considered an optional longer retention for these records and withdrew it (§1 ruling
1, §11.8). What that decision buys this design is worth naming, because it is the difference
between a mechanism and a knob: the ticket loses three nullable fields, the sweep loses a
second due-condition, the Deleted page loses a row state it would have had to explain ("purged,
but not finished"), restore loses its only impossible case, and `IdentitySettings` loses a
fourth field. §10.9 records exactly what it would take to add back — three nullable ticket
fields and one sweep condition — if a box ever turns up that needs it.

### 3.9 The sweep

`identity/retention.py::sweep(*, limit=SWEEP_LIMIT)` — `SWEEP_LIMIT = 25`, a module constant,
never a literal at a call site.

```
tickets due = purge_on <= today AND no hold
order by purge_on, pk
[:limit]
```

**ONE due-condition.** A ticket is due when its promised date has arrived and nothing holds it.
There is no second clause, because there is no second cliff (§3.8) and no ticket that outlives
its content (§3.1). The hold half of the condition is written now and is always true now —
nothing in this delivery sets a hold (§3.2) — and it is in the query so that the deferred
enterprise slice (§10.10) is a control and a refusal, not a change to the sweep.

Each is purged in its own transaction, so one failing ticket does not block the rest of the
batch; the failure is logged with its kind and key (structural, never content — the shape
`tools/rag/jobs.py` uses throughout) and the ticket stays due.

**The sweep always acts as the service principal** (`identity/contracts/principals.py`), whoever
triggered it. A sweep that ran under the acting principal would write "this member purged
somebody else's conversation" into the audit trail for a cliff nobody clicked; the cliff is the
box's own act, and the event says so. Only an explicit click — Delete, Restore, Delete
permanently — carries a real actor.

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

**What this delivery actually builds, stated before the table so the table cannot be misread:
enterprise behaves exactly as personal does.** The enterprise column below is what the design
holds, not what ships here. The hold control, the owner-set cliff, the refusal of an early
"Delete permanently" and the held-row copy are a named, deferred slice (§10.10), to be built
when a box runs the enterprise posture. Until then, an enterprise box gets the personal
behaviour: the item's owner may purge it before the cliff, and nothing can be held. **This spec
does not claim a records guarantee it has not built**, and neither may the help text, the ADR
or the Deleted page (§3.13).

| | `open` | `personal` | `enterprise` — **built** | `enterprise` — **deferred (§10.10)** |
|---|---|---|---|---|
| Who may delete an item | whoever may manage it today — `may_manage_conversation`, `may_administer_document`, `may_read_job`, and the item's owner for an Ask record | same | same | same |
| What "Delete" does | ticket + cliff | ticket + cliff | ticket + cliff | unchanged |
| The cliff | `retention_days`, default 30; `0` purges inline | same | same — the one box-wide number | operator-set with an enterprise floor |
| Restore, until the cliff | the item's owner, or `sees_all_content` | same | same | same, unless held |
| "Delete permanently" (purge now) | yes — the item's owner or `sees_all_content`. `is_admin` is True for everybody here, and there is nobody for anything to be hidden from | yes, same rule | **yes, same rule as personal — this is the honest statement of what is built** | **no. Nobody, before the cliff.** Control not rendered; the POST refuses with its own sentence |
| Hold (suspend the cliff) | not offered — no accounts, no owner role to gate it | not offered | **not offered.** The ticket's three hold columns exist and stay empty (§3.2) | an owner-role holder of one of the item's entitlements (`owned_entitlement_ids`) or a superuser |
| Whose tickets a viewer sees | everyone's — `sees_all_content` is True | own, plus everyone's for an administrator with the content setting on | same as personal | same |

The deferred column is the whole reason the ticket is posture-ready rather than
posture-branching: on a box with a records obligation, a user's delete must be a request, not an
erasure, and the person who could override that is the entitlement's owner. Everywhere else —
and everywhere in this delivery — delete means delete. What makes the deferral safe to state
in a table rather than to build now is that the fields are already there (§3.2) and the sweep
already excludes a held ticket (§3.9); the slice is a control, a refusal and one audit action,
against a data model that will not have to move.

### 3.11 The queue's age cliff — identity's number, the queue's prune — and today's orphans

**`JobSettings` gains no field.** The queue's age cliff is `IdentitySettings.
queue_retention_days` — `PositiveIntegerField(null=True, blank=True, default=1)`, the second
of the three fields of the one centralised retention policy (§4, **owner ruling §11.1**). Null means **no age
cliff** (the FIFO `retention_limit` alone), the same "honestly unknown, never silently assumed"
convention `memory_budget_bytes` and `max_queued_per_principal` already document on
`JobSettings`; `1` is the shipped default the brief sets. The queue READS this number; it does
not own it.

`models/queue/backend.py::_prune_finished_jobs` gains an age condition beside its existing
cutoff-pk delete: terminal rows whose `finished_at` is older than the policy's day count go, in
the same bulk delete, still scoped to `state__in=TERMINAL_STATES` so a queued or running row can
never be counted or removed. Today its only call site is `enqueue()`, which runs one prune per
call (verified in that function, whose `cancel_job` docstring also relies on the fact); the
queue steward's reshaping also drives it from the worker tick. The signature takes the days as
a parameter — `_prune_finished_jobs(limit, retention_days)` — so the READ is the caller's, made
once, and this function stays a pure query.

**How `models/queue` reads an identity-owned number, exactly.** Three conditions, each a rule
with a reason:

- **Across the sanctioned seam, never `identity.models`.** `models/` sits below `identity/` in
  the import law (`AGENTS.md`, "The import law": a column imports downward through
  `identity → foundation → models → agents → tools`), so `models/queue` importing identity is
  permitted — `models/queue/visibility.py` already imports `identity.access`, and
  `models/queue/views.py` already imports `identity.audit`, `identity.contracts` and
  `identity.request`. But the permitted surface is an ALLOWLIST:
  `foundation/ops/tests/test_import_law.py`'s `IDENTITY_PERMITTED` is exactly
  `("identity.contracts", "identity.access", "identity.request", "identity.audit")`, and
  `test_no_column_imports_identitys_private_modules`' own docstring says the consequence in
  words: *"it means no column ever imports `identity.models`."* So the queue calls a function on
  `identity/access.py`, and identity does the model read on its own side of the seam — the
  identical shape `identity/access.py::settings_row` was written for, and whose docstring gives
  this exact reason for existing.
- **A NON-CREATING read: the queue must never materialise the identity singleton.**
  `IdentitySettings.get_solo()` is `objects.get_or_create(pk=1)` — it WRITES when the row is
  absent. A worker is the wrong process to create the posture row, and creating it inside the
  claim's advisory-lock transaction is worse. So the new seam is its own function, not
  `settings_row()`:

  ```
  identity/access.py::queue_retention_days() -> int | None
      row = IdentitySettings.objects.filter(pk=1).first()
      return QUEUE_RETENTION_DAYS_DEFAULT if row is None else row.queue_retention_days
  ```

  `.first()`, never `get_or_create`; `QUEUE_RETENTION_DAYS_DEFAULT = 1` lives in
  `identity/contracts/retention.py` (pure, and `identity.contracts` is a permitted seam) so the
  queue can name the same constant its fallback uses. An absent row means a box whose posture
  page has never been opened, and the documented default is the honest answer for it.
- **Tolerant at boot, because an exception here is read as a crash.** The prune runs on the
  worker path, and an exception escaping `tick()` is what `models/queue/worker.py::run_forever`
  records as `crashed=True` — `_shutdown(crashed=True)` ends in `os._exit(1)`. A racing
  `migrate` can leave `identity_identitysettings` absent for a few seconds. So the CALLER wraps
  the seam call in `except (ProgrammingError, OperationalError)` and falls back to
  `QUEUE_RETENTION_DAYS_DEFAULT`, logging the same way, and never re-raises. The precedent is
  merged and in the same function: `enqueue` already wraps its one `JobSettings.get_solo()` in
  exactly those two exception types, logs with `exc_info=True`, and forgives by skipping the
  prune — deliberately those two and not a bare `except Exception`, so a genuine bug still
  raises loudly. The queue steward's branch makes the `JobSettings` reads on the tick path
  tolerant the same way; this read follows that established shape rather than inventing one.
- **One read per prune, never one per row.** The identity read happens at most once per prune
  call, beside the single `JobSettings` read `enqueue` already hoists to the top of the call
  (its own comment: *"ONE `JobSettings` read per enqueue, threaded to the three steps below that
  each used to make their own"*). Never inside a per-job loop, and never inside
  `_prune_finished_jobs` itself. §7 pins this with a query-count test, the same instrument §7
  already uses for `ticketed_keys`.

**The queue settings page does not show this number at all.** Its POST writer gains nothing;
the Queue settings form keeps the fields it has. One retention policy, one place to edit it —
a read-only echo would still be a second place to look, and the simpler choice is the one that
cannot drift. The Queue settings page's help text says where the cliff lives, in one sentence
pointing at the retention section of Identity & security, and that is all it says.

**The bound is validated in the identity settings writer**, not in the queue and not in the
database: `identity/services.py::set_posture` (the one writer for that row, whose refusals are
`ServiceRefused` sentences and whose audit events are `identity/audit.py`'s) validates all
three retention fields, through `foundation/settings_bounds.py::exceeds_field_ceiling` with
`POSITIVE_INT_FIELD_MAX` — the same ceiling helper `models/queue/views.py` uses for
`retention_limit` today — plus the per-field range check (`queue_retention_days`: 1–3650, or
blank for null), with the form field on `identity/forms.py::PostureForm` beside
`session_idle_minutes`, and the refusal raised before `.save()`.

**This is what clears the live box's residue.** The 69 orphaned `agent.turn` payloads are
terminal rows older than a day; the first prune after deploy removes them, along with every
other finished row past the cliff. No one-shot purge command, per owner ruling 3 — a command
that existed only to fix a historical state is a command nobody deletes afterwards.

### 3.12 Audit actions, and the detail toggle

THREE names added to the closed tuple in `identity/contracts/actions.py`, in a `content.`
namespace — naming what changed, not which table, the convention that module's own comments
already argue for:

```
CONTENT_DELETED  = "content.deleted"
CONTENT_RESTORED = "content.restored"
CONTENT_PURGED   = "content.purged"
```

A fourth content action for a hold is NOT added here. The action tuple is closed and
`AuditEvent.save()` raises on anything unlisted, so an unused name would be an action nothing
can write — and the deferred enterprise slice (§10.10) adds its own name in its own commit,
beside the control that writes it. With `identity.retention_policy_changed` (§4) that makes
**four new action names in this delivery**, three of them content actions and one a settings
action.

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
  and **"Purge on 21 October 2026"**. Per row: **Restore**, and **Delete permanently** for the
  item's owner or a `sees_all_content` principal. Every ticket on this tab is restorable, in
  every posture — that is what a ticket means (§3.1) — so there is no second row state to
  render and no per-row count of what a purge would reach (§3.5). **No Hold control is rendered
  in any posture, including enterprise**, and the page says nothing about holds or about a
  purge an owner cannot perform: the enterprise behaviour is deferred (§3.10, §10.10) and the
  page must not imply a guarantee that is not built.
- **Purged** — content-free audit events, `by_action((CONTENT_PURGED, CONTENT_DELETED,
  CONTENT_RESTORED))`, rendered as "Conversation 59608c35-… deleted at 14:32" and visible in
  every posture with the toggle in either position. With `audit_detail` on, the same lines
  carry the labels.

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
  note file is unlinked, its ticket is gone from the Deleted tab, and one content-free event
  stands on the Purged tab.

---

## 4. Data model and migrations

**Exactly one migration.**

**`identity/migrations/0004_deletion_ticket_and_retention_settings.py`** — creates
`DeletionTicket` (§3.2) and adds **exactly three** policy fields to `IdentitySettings`, the
whole retention policy on one row (**owner ruling, §11.1**). **Every one is optional and every
one has a working default, so a maintainer who never opens this page gets correct behaviour**
(§3.0):

| Field | Label a person reads | Type | Default | Bound (in the writer) |
|---|---|---|---|---|
| `retention_days` | **"Keep deleted items for"** | `PositiveIntegerField` | `30` | 0–3650; `0` is legal and means delete immediately |
| `queue_retention_days` | **"Keep finished queue jobs for"** | `PositiveIntegerField(null=True, blank=True)` | `1` | 1–3650, or blank for null = no age cliff (§3.11); `0` is ILLEGAL — blank is how "no cliff" is said |
| `audit_detail` | **"Show item names in the deletion log"** | `BooleanField` | `False` | — |

The labels above are the interface copy, in plain words, declared once in Python like every
other label in this repository (§3.13's house rule). A person reading that page is not asked to
understand a ticket, a cliff, a sweep or a purge; they are asked how long to keep things and
whether to show names.

**Three fields, and no fourth.** The design deliberately stops here. A longer retention for
tool-call records was asked for and withdrawn (§1 ruling 1, §10.9); a per-user override is out
of scope (§10.4); the enterprise cliff floor belongs to the deferred slice (§10.10). Where a
number could be a setting or a constant, this document says which and why, once — see the
queue cliff below.

**The queue cliff stays a setting, on the owner's earlier instruction.** Making
`queue_retention_days` a fixed constant in `identity/contracts/retention.py` instead of a
settings field was considered under the same cost/benefit principle — it would remove one knob
and one bound check — and it is kept as a setting because the owner instructed (§11.1) that
the whole retention policy live in one editable place; should that ever be revisited, it is a
two-line change (drop the field from the form and the writer, read the constant the fallback
already names).

All three are edited in ONE **"Retention"** section of the EXISTING `identity-settings` page
(Identity & security), which is where the posture and `admin_sees_content` already live — no
new settings page, no new group for them — and all three are audited under ONE new action,
`RETENTION_POLICY_CHANGED = "identity.retention_policy_changed"`, with `detail` carrying
`field` and `to`. That follows `LIBRARY_SETTINGS_UPDATED`'s recorded rule — one action per
settings DOMAIN, the literal column in `detail` — rather than the three-way split
`POSTURE_CHANGED` / `LIBRARY_POSTURE_CHANGED` / `ADMIN_CONTENT_ACCESS_CHANGED` uses, because
those three are semantically distinct security postures and these three are one retention
policy expressed as three knobs. That makes **four** new action names in total (§3.12), one of
them a settings action rather than a content one; §11 records the choice.

**`models/queue` gets no migration and no new column.** The queue's age cliff is
`queue_retention_days` above, read across the identity seam at prune time (§3.11). There is no
`models/queue/migrations/0006`, and no `0005`-versus-`0006` sequencing question with the queue
steward's branch, because this feature adds no queue migration to sequence.

**No per-model soft-delete columns, and the reasons are three.** (a) Four tables in three columns
means four migrations and four places to forget an exclusion; the ticket means one table and one
exclusion function. (b) A cliff, an actor, a label and a hold are facts about the DELETION, not
about the conversation — a `deleted_at` column on `Conversation` would need the same companions,
and then all of them again on `Document`. (c) The Deleted page is one query over one
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
| Restore after a partial purge | **A purge that rolled back leaves the ticket, and the ticket is restorable** — no rows were lost, so the item is exactly as it was. **A purge that completed has no ticket**, so there is nothing to restore and nothing to refuse: the Deleted page simply no longer lists the item. Those are the only two outcomes, because the ticket is deleted by the same transaction that destroys the content (§3.3). Files a FILES-band handler already removed before a later handler raised are gone — named in the row above — and the next sweep completes the purge. | §3.3, §3.5 |
| A hold on a ticket past its cliff | The sweep's due-query excludes held tickets outright (§3.9), so a held ticket would sit past its date indefinitely. **Nothing in this delivery can set a hold**, so this case cannot arise yet; it is specified because the columns and the query clause ship now and the control is the deferred enterprise slice. | §3.2, §3.9, §10.10 |
| The cliff is changed after tickets exist | **`purge_on` is NOT recomputed.** It is computed once, at delete time, from the setting in force then. The page printed a date and that date is a promise; silently moving it — in either direction — would make the promise worthless, and moving it EARLIER would destroy content sooner than the person was told. A changed `retention_days` governs future deletes only, and the settings page says so in its help text. | §3.2, §8 |
| A posture switch with tickets pending | Tickets are posture-independent data; nothing is migrated, and in THIS delivery nothing changes at all — every posture behaves the same way (§3.10), so a switch to or from enterprise leaves every pending ticket exactly as it was. `identity/services.py::set_posture` gains no retention branch. When the deferred enterprise slice lands it inherits that property: a hold is a recorded decision, not a posture artefact. | §3.10, §10.10 |
| Deleting a conversation whose documents are shared universally | The documents survive. Only `scope=conversation` documents die with their conversation — the invariant `tools/rag/access.py::delete_attachments` already depends on (exactly one attachment row, for that conversation). A universal or stream-contained document loses only its attachment CLAIM, exactly as today. | §3.4, §3.6 |
| Workstream delete | **Unchanged.** It still refuses while the stream holds conversations (`PROTECT`), still deletes shares and pins, still audits `WORKSTREAM_DELETED`. Streams are not a ticket kind (§10). | §10 |
| A `sees_all_content` principal viewing tickets | Sees every ticket, including other people's, and may restore or permanently delete them in every posture this delivery builds — the same predicate that already lets them read the content. On an open box that is every principal, which is correct: there is nobody for anything to be hidden from. An administrator with the content setting OFF sees only their own, and the labels on the Purged tab stay empty for them regardless of `audit_detail`. | §3.10 |
| **A conversation is permanently deleted while one of its turns is still queued or running** | The SOFT delete always succeeds — the ticket is written, the conversation vanishes from every surface immediately (§3.4), and the in-flight turn's own behaviour is exactly today's: `delete_conversation` never touched queue rows and could not (import-law rule 2), so the job simply runs to its end. The PURGE is where the two states differ. A still-QUEUED row is CANCELLED through `models/queue/backend.py::cancel_job` — preserving its `on_terminal` scheduling, so `agents/runtime/jobs.py::on_turn_terminal` runs its one conditional `UPDATE` on commit and no-ops against turns that are already gone — and then deleted. A RUNNING row is never deleted: `forget_conversation` refuses, the purge aborts with the ticket and every row and byte intact (that handler is `ORDER_ROWS` and runs first), the view shows the refusal sentence, and the next sweep — or the next click — completes it once the worker is done. Deleting a live row would make the job vanish under the turn's own poller (`agents/chat/views/turns.py` calling `get_job` each tick), which reads as a database fault rather than as a delete. | §3.6, §3.7 |
| The identity settings row is unreadable when the queue prunes | Not an error, and never a crash. The prune's read of `queue_retention_days` goes through `identity/access.py`, is NON-CREATING (`.first()`, never `get_solo`'s `get_or_create` — a worker must not materialise the posture singleton), and its caller catches `ProgrammingError`/`OperationalError` and falls back to the documented default of one day. An exception escaping the worker's `tick()` is read by `run_forever` as a crash and ends in `os._exit(1)`; a racing `migrate` must not be able to cause that. The precedent is `enqueue`'s existing tolerance of its own `JobSettings.get_solo()`. | §3.11 |
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
- `restore_content` deletes the ticket and writes `CONTENT_RESTORED`.
- **A completed purge leaves NO ticket** — asserted directly, because it is the invariant the
  whole "a ticket means restorable" reading rests on (§3.1) — and a purge that rolled back
  leaves a ticket that `restore_content` still accepts.
- The registry: registration is idempotent (the sibling registries' rule), handlers sort ROWS
  before FILES regardless of registration order, and a handler registered with a non-dotted path
  raises at registration.
- The runner **never swallows**: a handler that raises aborts the purge, the ticket survives,
  and the connection is usable afterwards — asserted by performing a real query after the
  failure, which is the only way the savepoint's purpose is actually tested.
- Re-running a purge after a mid-purge failure completes it (idempotence, as a contract test
  every column's handler is run against).
- `ticketed_keys` costs one query and its result is a list, not a queryset.
- The sweep: **one due-condition** — bounded at `SWEEP_LIMIT`, picks up a ticket whose
  `purge_on` has arrived, skips a ticket with a hold set (constructed directly in the test,
  since nothing in this delivery writes one), one failing ticket does not stop the batch, and
  `manage.py purge_deleted` writes `source="cli"` events.

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
- **`forget_conversation` and a non-terminal row.** A QUEUED row is cancelled through
  `cancel_job` BEFORE it is deleted — asserted by the `on_terminal` hook having been invoked,
  not merely by the row's absence, since only the hook distinguishes a cancel-then-delete from
  a bare delete. A RUNNING row is **not** deleted: the handler raises, the purge aborts, the
  ticket survives, every other row and file is untouched, and re-running the purge after the
  job reaches a terminal state completes it. An already-terminal row is deleted with no cancel
  attempt.
- `_prune_finished_jobs` with a one-day cliff removes a terminal row finished two days ago,
  keeps one finished an hour ago, and **never** touches a queued or running row however old.
  A null cliff reproduces today's FIFO-only behaviour byte for byte. The days come from
  `IdentitySettings.queue_retention_days`, which the tests SET on that row — there is no
  `JobSettings` field to set.
- **The queue reads the policy from identity, and `JobSettings` is unchanged.** Setting
  `IdentitySettings.queue_retention_days` changes what the prune deletes; `JobSettings` has no
  `retention_days`-style field at all (asserted directly against the model's fields, so a later
  re-introduction fails this test); and `models/queue` reaches identity only through the
  allowlisted seams — a direct `identity.models` import in this column is a violation, which
  `foundation/ops/tests/test_import_law.py::test_no_column_imports_identitys_private_modules`
  already fails on and this column re-asserts for its own files.
- **The read is non-creating, tolerant, and made once.** With no `identity_identitysettings` row
  at all, the prune uses the documented default and **creates no row** (asserted by counting
  `IdentitySettings` rows after the prune — the failure this pins is `get_solo`'s
  `get_or_create` being used from a worker). With the table itself absent — a
  `ProgrammingError`/`OperationalError` raised from the seam — the prune falls back to the
  default, logs, and **does not raise**, so nothing can escape `tick()` and be read as a crash.
  And a **query-count test**: one prune over twenty-five terminal rows makes exactly ONE
  identity read, the same instrument the `ticketed_keys` count test uses.
- `forget_jobs` on the contracts seam dispatches through `INFERENCE_QUEUE_BACKEND` and is a
  no-op for an id that does not exist.

**`identity/` — the retention policy fields.**
- `set_posture` writes all three retention fields, refuses each out-of-range value with its own
  sentence before `.save()` (including `queue_retention_days=0`, which is NOT legal — blank is
  how "no age cliff" is expressed), and writes one `identity.retention_policy_changed` event per
  field CHANGED, with the field named in `detail`. Unchanged fields write no event.
- **A box that configures nothing behaves correctly** (§3.0): with a freshly migrated
  `IdentitySettings` row and no field ever written, a delete tickets with `purge_on` thirty
  days out, the queue cliff is one day, and the audit trail carries no labels — asserted
  against the model's declared defaults, so a later default change has to be deliberate.
- `identity/access.py::queue_retention_days()` returns the row's value, returns the documented
  default when no row exists, and creates nothing in either case.
- `retention_days=0` and `queue_retention_days=None` are both round-trippable through the form
  and the writer — the "zero stays expressible" constraint, now for two fields.

**The deletion-coverage gate — one NEW cross-column guard.**

`foundation/ops/tests/test_deletion_coverage.py`, beside `test_import_law.py` and
`test_column_boundaries.py`, because `foundation.ops` is the app that already reaches across
every column by design and this is a repo-wide structural assertion (that module's own recorded
reason for living there).

**In plain words: a future feature that stores content somewhere new must wire it into
deletion, or the build fails.** This design's weakest point is not any mechanism in it — it is
the year after it merges, when somebody adds a table that holds what a person typed and nobody
remembers that deletion is a registry you have to join. A test is the only form of that
reminder that cannot be forgotten.

The test holds **one closed list**, written from §2.1's inventory: every model carrying user
content, mapped to the ticket kind whose registered handlers reach it —

| Model | Kind whose handlers reach it |
|---|---|
| `agents.Conversation`, `agents.Turn`, `agents.ToolInvocation` | `conversation` |
| `tools.rag.Document`, `tools.rag.DocumentRow`, `tools.rag.DocumentAttachment` | `document` (and `conversation`, for a chat-scoped document) |
| `tools.rag.AskRecord` | `ask` |
| `tools.vision.GenerationJob`, `tools.vision.JobInput`, `tools.vision.GeneratedOutput` | `vision_job` |
| `models.queue.InferenceJob` | `conversation` and `document` (payload-keyed, §3.7) |

and it FAILS on either of two conditions:

- **(a)** a listed model has **no registered retention handler for its kind** —
  `retention_handlers(kind)` is empty, or no registered handler's dotted path resolves, which
  catches a handler deleted, renamed or dropped from an `AppConfig.ready()`;
- **(b)** a model carrying the **owner columns** exists that is in neither the covered list nor
  an explicit, reasoned exemption list. The marker is the `owner_kind` / `owner_key` PAIR —
  verified against `identity/access.py::owner_fields`, which is the one definition of how
  ownership is stamped in this codebase (`return {"owner_kind": principal.kind, "owner_key":
  principal.key}`) and whose docstring says it was moved there so every owned table in every
  column shares ONE definition. The test discovers the pair by walking `apps.get_models()` and
  asking each for both field names, so it sees a new owned table the day it is added, with no
  list to update first.

**The exemption list is explicit, reasoned in one line each, and short.** As written today it
holds the owned tables that are configuration or containers rather than content:
`agents.Agent` and `agents.Flow` (an agent definition and a flow definition are settings a
person authored, not content a person's deletion reaches — they are deleted directly, by their
own pages), and `agents.Workstream` (a container, deliberately not a ticket kind — §10.2, where
the reason is argued). An exemption is a sentence somebody has to write and a reviewer has to
read; that is the point of making it a list rather than a default.

What the gate does NOT do: it does not run a purge, does not touch the database beyond model
introspection, and does not assert that a handler is correct — §7's per-column tests do that.
It asserts that the wiring EXISTS, which is the failure mode that arrives silently.

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
  `identity-deleted-purge` for a ticket the principal has no standing on — 404, the class-O
  shape (§3.13). The enterprise-posture refusal is not tested here because it is not built
  here (§3.10, §10.10); the same POST in the enterprise posture succeeds, exactly as it does in
  personal, and one test asserts that rather than leaving the posture untested.

**The demo, as one end-to-end test** (`identity/tests/test_deletion_demo.py`), personal posture,
in this order, asserting at each step:
1. A conversation with a turn, a chat-scoped document, a generated image and an Ask record.
2. Delete the conversation → absent from `visible_conversations`; present on the Deleted page
   with a `Purge on <date>` line carrying the right date; the `agent.turn` queue row still
   exists (nothing has been purged yet) but the conversation renders nowhere.
3. **Delete permanently** → in the same response cycle: absent from the chat list; **zero**
   `InferenceJob` rows naming that conversation; the generated image's row, files and queue row
   gone; the chat-scoped document, its chunks and its bytes gone; `ToolInvocation.args`/`text`
   empty with the row still present — **scrubbed inline, in this transaction, with no setting
   touched**; the note file gone; and **no `DeletionTicket` row left for that conversation**.
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
| `docs/adr/0019-deletion-and-retention.md` | **The next number** (the record runs to `0018-settings-assistant.md`). The policy decision: delete means delete; one ticket table rather than per-model columns; the retention namespace on the existing cascade registry; the content/audit split; backups as their own layer; the named residue (§10); **and the owner's cost/benefit principle (§3.0) with what it cut — no dry-run mode, no second cliff for tool records, enterprise behaviour deferred while its fields ship** |
| `docs/EXTENDING.md` | A new recipe, **"Registering a retention handler"**, beside "Adding an entitlement axis": the `RetentionHandler` fields, the two order bands and when to use each, the one-mode `(key: str) -> int` signature (and one line on why it is not two-mode like `EntitlementCascade`), the idempotence obligation, the one-line `AppConfig.ready()` registration, the one test a new handler owes (re-run-after-failure), **and the deletion-coverage gate: a new model that holds user content must be added to `foundation/ops/tests/test_deletion_coverage.py`'s covered list with a handler that reaches it, or to its exemption list with a reason — the test fails until one of the two is done** |
| `identity/README.md` | The ticket table **and the fact that a ticket exists only while the item is restorable**, the three-field retention policy on `IdentitySettings` with its plain-words labels, **the new non-creating `identity/access.py::queue_retention_days` seam** (what it returns when there is no row, and why it is not `settings_row()`), the orchestration, the Deleted page, and the sentence that identity answers "which keys are deleted", never "which conversations" |
| `agents/README.md` | `agents/retention.py`, the new artifact-purge slot, and the tool-record scrub's rule — scrubbed inline with the conversation, always |
| `tools/rag/README.md`, `tools/vision/README.md` | Their handlers, and what each one does and does not reach |
| `models/README.md` | The payload-keyed queue handler and its cancel-before-delete rule; the age cliff **and the fact that its number is identity's, read across the `identity.access` seam non-creatingly and tolerantly, with `JobSettings` gaining nothing**; and `forget_jobs` on the contracts seam |
| `foundation/settings_help.py` | The Deleted page's `HelpCard` — what the page is for, what Restore and Delete permanently do, that the purge date is fixed at delete time and a changed setting governs future deletes only, and that backups are a separate layer. It says nothing about holds or records obligations, because none are built (§3.10). **The Queue settings card gains one sentence** saying the finished-job cliff is part of the retention policy on Identity & security, since the number is not editable on the Queue page (§3.11) |
| `foundation/ops/tests/test_deletion_coverage.py` | Not a document, but its module docstring carries the maintenance rationale in the same words the `EXTENDING.md` recipe uses: **this gate exists so a feature that starts storing content somewhere new cannot ship without joining the deletion registry.** The two failure conditions, the `owner_kind`/`owner_key` marker and its source (`identity/access.py::owner_fields`), and the rule that an exemption is a line of prose somebody writes and a reviewer reads (§7) |

No document names a model or a vendor; `test_docs_model_names.py` walks the planning archive
too, so this spec is inside its reach.

---

## 9. Delivery

Branch off `origin/dev`, pull request into `dev`, merged on the owner's word, with the
merge-readiness gate's whole-feature UAT walked by hand on this branch's preview stack.

### Two slices

**Slice 1 — personal-posture hard purge, conversation kind, end to end.**
The identity ticket (including its three hold columns, written by nothing — §3.2), the three
retention policy fields and their one "Retention" settings section, the non-creating
`queue_retention_days` seam, the retention namespace on the cascade registry, the one-mode
runner, the three content audit actions plus the settings action, and the audit reader; the
agents handler (turn/share/attachment rows, the inline tool-record scrub, the artifact
collection and the vision hand-off); the rag notes handler; the vision artifact-purge
registration; the Deleted page with both tabs; the sweep and the management command; **the
deletion-coverage gate (§7)**; and the documentation set (`OPERATIONS.md` backups section, ADR
0019, the column READMEs, the `EXTENDING.md` recipe, the help card). **The queue half is a
separate, late task inside this slice** (below).

**Slice 2 — documents, Ask history and the gallery, routed through the retention service.**
The library delete becomes a ticketing delete; an Ask-history delete route and the affordance on
`HistoryView`'s page (which stops being read-only for the first time, and its docstring says so);
both kinds' rows on the Deleted page; the gallery's own delete and bulk delete routed through the
retention service for the `vision_job` kind. The coverage gate's covered list reaches its full
shape here, and the `document`, `ask` and `vision_job` entries are what make it pass.

**There is no third slice in this delivery.** The enterprise behaviour that used to be one is a
named, deferred slice in §10.10, to be built when a box runs the enterprise posture. The
`audit_detail` toggle is NOT deferred with it — it is one of the three shipped settings (§4)
and lands in slice 1 with the rest of the policy.

### Sequencing constraints

- **The `models/queue` half is still implemented after the queue steward's PR lands — but the
  reason has narrowed to textual adjacency.** There is no longer a MIGRATION-ordering constraint
  with that steward at all: the centralised policy (§11.1) leaves `models/queue` with no
  migration, so there is no `0005`/`0006` sequence to agree on and nothing that has to land in a
  particular order to apply cleanly. What REMAINS is that their PR reshapes the Queue page and
  its settings form, and this feature's queue half edits the same files —
  `models/queue/backend.py`'s prune, `models/queue/views.py`'s settings help text, and the Queue
  page's own copy. Writing ours on top of theirs is textual conflict avoidance, nothing more.
  That half is `models/queue/retention.py` (including the cancel-before-delete rule, §3.7), the
  `_prune_finished_jobs` age condition and its one identity read, the Queue settings help
  sentence, and `forget_jobs` on `models/contracts/queue.py`. It is isolated as its own task (or
  two) at the end of slice 1, and until it lands the conversation purge is complete in every
  column but the queue — a state the task's own tests assert rather than leave ambiguous. The
  identity half — the three fields, the settings section, the writer and its bounds — carries no
  such constraint and lands with the rest of slice 1.
- **The queue steward's four binding conditions are carried in the design, not in the task
  brief**: boot tolerance and no worker-side `get_or_create` (§3.11, §5), one read per prune
  (§3.11, pinned by a query-count test in §7), and terminal-rows-only plus cancel-before-delete
  for `forget_conversation` (§3.6, §3.7, §5, §7). The clearance packet for that steward is the
  diff against `models/queue/backend.py`, `models/queue/views.py` and the new
  `models/queue/retention.py`.
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
| `models/queue/retention.py` (new) | models | `models.queue.*` (including `backend.cancel_job`), `identity.contracts`, `identity.access`, `identity.audit`, `identity.request` | **anything under `agents.` or `tools.`**, and `identity.models` — see the row below |
| `models/queue/backend.py` (amended: the prune's age condition) | models | `identity.access.queue_retention_days`, `identity.contracts.retention` for the default constant — the same direction `models/queue/visibility.py` already takes with `identity.access.is_admin`/`sees_all_content` | **`identity.models`**, which is outside `IDENTITY_PERMITTED`; the row read happens on identity's side of the seam |
| `identity/access.py` (one new non-creating reader) | identity | `identity.models` (intra-column) | `agents`, `tools`, `models` |
| `models/contracts/queue.py` (one passthrough) | models | unchanged | `models.queue.models` |
| `foundation/ops/tests/test_deletion_coverage.py` (new, §7) | foundation | `django.apps`, `identity.contracts.cascades`, `identity.access` — the reach `foundation/ops`' other repo-wide gates already have by design | nothing new; it introspects models, it does not import any column's models module |

**No module disappears** with the cuts of §11.8: `identity/cascades.py` keeps its runner (one
instead of two), `agents/retention.py` keeps its scrub (one argument instead of two), and every
row above stands as written. Every cross-column reach in this feature is either a dotted-path
registration resolved at run time or an existing sanctioned seam. **No new import crosses a boundary in a direction the law
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
4. **Per-user retention overrides.** The whole retention policy — all three fields — is a box
   policy on the `IdentitySettings` row (§11.1). A
   per-person or per-entitlement cliff needs a second table and a precedence rule; not now.
5. **Export before delete.** No "download your data" step. Delete is delete, and an export
   surface is its own feature with its own gating.
6. **`rag.ask` queue rows keyed to an Ask record.** No link exists in either direction (§3.6);
   those rows are cleared by the queue's age cliff and nothing keys them to a purge.
7. **A generation whose tool turn was never written** (§3.7): accepted residue, reachable only
   through the gallery.
8. **`Turn.tool_call`'s replayed args as a separate scrub target.** They die with the turn by
   CASCADE; there is no case where a turn survives its conversation.

The last three were cut by the owner's cost/benefit ruling of 2026-09-21 (§11.8, §3.0), and
each is named with what it would cost to build so a real need can pick it up without
re-deriving the design:

9. **Optional longer retention for tool-call records: three nullable ticket fields and one
   sweep condition if a real need appears.** The owner asked for this and withdrew it (§1
   ruling 1). Concretely, should a box ever need it: `purged_at` (datetime, null),
   `tool_purge_on` (date, null) and `deferred_ids` (JSON list of `ToolInvocation` ids, never
   content) on `DeletionTicket`; a second `OR` clause on the sweep's due-query; a fourth
   settings field; and one honest page state to explain — an item whose content is gone but
   whose bookkeeping is not finished, which is neither restorable nor absent. Today tool
   records are scrubbed inline with their conversation, always (§3.8).
10. **The enterprise posture's BEHAVIOUR — a named, deferred slice, to be built when a box runs
    the enterprise posture.** It is: the Hold control on the Deleted page for an owner-role
    holder of one of the item's entitlements (`owned_entitlement_ids`) or a superuser; the
    refusal of "Delete permanently" before the cliff in that posture, with the control not
    rendered and the POST refusing in its own sentence; an owner-set cliff with an enterprise
    floor; a `CONTENT_HELD` audit action added to the closed tuple in the same commit as the
    control that writes it; and the held-row copy ("On hold — the purge date is suspended").
    **Its FIELDS ship now** — `hold_by_kind`, `hold_by_key`, `hold_note` on the ticket (§3.2) —
    and the sweep's due-condition already excludes a held ticket (§3.9), so the slice needs no
    migration against a live ticket table. Until it is built, **the enterprise posture behaves
    exactly as personal does and this spec, the help text, the ADR and the Deleted page all say
    so** (§3.10, §3.13, §8).
11. **A dry-run count of what a purge would remove.** No handler counts without removing, no
    count is shown before a confirmation, and the Deleted page has no per-item count line
    (§3.5). The confirmation a deletion gets is the Deleted page itself, where the item sits
    named and restorable for as long as `retention_days` says. Adding it back would mean a
    `commit` flag on every retention handler and on the artifact-purge slot, a second code path
    per handler and a count-then-commit agreement test per column — which is what the
    `EntitlementCascade` registry pays for its own confirmation count, and it needs one.

---

## 11. Decisions the author made

Each is a place the brief left a choice, with what was chosen and why. **Two entries are not the
author's.** Decision 1 is an owner ruling of 2026-09-21 that stands in place of the author's
first draft, and decision 8 is the owner's cost/benefit ruling of the same date, which cut three
mechanisms out of this design and added one gate to it — both marked as such. **Decision 5 was
withdrawn** by that second ruling and is kept, empty of a choice, at its own number. The rest
are the author's and are open to the same treatment. Nothing is renumbered.

1. **The retention policy is CENTRALISED on `IdentitySettings`. `JobSettings` gains no field.**
   *(Owner ruling, 2026-09-21 — **overrides the author's first draft**, which put
   `queue_retention_days` on `JobSettings` beside the queue's own writer and prune.)*

   All policy fields live on the existing `IdentitySettings` row, beside the posture. As
   shipped that is THREE — `retention_days` (default 30, `0` allowed), `queue_retention_days`
   (default 1; null = no age cliff, the FIFO `retention_limit` alone) and `audit_detail`
   (default False); the fourth this ruling originally covered, `tool_record_extra_days`, was
   withdrawn by the owner's later cost/benefit ruling (§11.8) and the centralisation principle
   is unaffected by its absence. One "Retention" section on the identity settings page edits
   all three; one audit action (`identity.retention_policy_changed`, with the field named in
   `detail`) records every change; one writer validates every bound.

   **The owner's reason, in plain words:** one retention policy belongs in one place. Two
   settings pages means two places to look, two writers to validate, and a real chance the
   content cliff is changed while the queue silently keeps its own. The extra single-row read
   in the prune step is negligible on a single-box install.

   **What this costs the queue, and why it is affordable.** `models/queue/backend.py::
   _prune_finished_jobs` reads the number at prune time across the `identity.access` seam —
   permitted, because `models/` imports downward to `identity/` (`AGENTS.md`'s import law) and
   `models/queue/visibility.py` already does exactly that. The read is non-creating, tolerant of
   a missing table, and made once per prune, never per row; `identity.models` stays closed to
   this column, as `IDENTITY_PERMITTED` requires. §3.11 specifies all three, §5 carries the
   failure cases and §7 pins them.

   The knock-on consequences are carried through this document rather than left implied: **one**
   migration instead of two (§4), no `0005`/`0006` sequencing with the queue steward (§9), and
   the settings-writer tests living in `identity/` (§7).
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
5. **WITHDRAWN under the owner's cost/benefit ruling (§11.8).** This decision chose WHERE to
   carry a deferred tool-record scrub — on the ticket rather than on `ToolInvocation`. The
   owner then withdrew the deferral itself, so there is nothing to place: tool-call records are
   scrubbed inline with their conversation's purge, always (§1 ruling 1, §3.8). The entry is
   kept at its number, and empty of a choice, because the rest are numbered around it and
   because a decision withdrawn is a different fact from a decision never made. What it would
   cost to reinstate is in §10.9.
6. **The Deleted page is gated `EVERYONE` in a new "Your content" settings group, route class
   A.** Every existing settings entry is `ADMIN` or `ACCOUNTS_ADMIN`; this page is a person's own
   deleted items, so a member must reach it. The consequence on the one public settings page is
   recorded in §3.13 rather than left to be discovered.
7. **Settings writes for the retention policy are audited under one new action
   (`identity.retention_policy_changed`) with the field in `detail`**, following
   `LIBRARY_SETTINGS_UPDATED`'s "one action per settings domain" rule rather than splitting three
   knobs into three actions the way the three security postures are split. That makes four new
   action names, not three; the fourth is a settings action, not a content one (§3.12).
8. **Three mechanisms are CUT and one coverage gate is ADDED — owner ruling, 2026-09-21.**
   *(Not the author's. This is the owner's decision, recorded here in the same place the
   author's are so that a reader of §11 sees the whole shape of the design's choices.)*

   The principle, in the owner's own words:

   > I want to make sure we achieve a good cost/benefit associated with our changes/complexity,
   > and that we don't add so many features/options it paralyses the maintainer because they
   > have to set up so many options.

   Held as a test the whole document must pass (§3.0): **a maintainer installs this and has to
   set up NOTHING; every option has a working default; there are as few options as the feature
   can honestly have.**

   **What it cut, and where each is now recorded:**

   - **The optional longer retention for tool-call records** — asked for by the owner first,
     then withdrawn by him. It would have cost three nullable ticket fields, one extra sweep
     condition, a fourth setting and a page state nobody could explain in one sentence. Tool
     records are scrubbed inline with their conversation, always. → §1 ruling 1, §3.8, §10.9;
     it replaces decision 5 above.
   - **The dry-run count mode on retention handlers** — `(key: str) -> int`, one mode. No
     `commit` flag on a retention handler or on the artifact-purge slot, no count before a
     confirmation, no per-item count line on the Deleted page, and no count-then-commit
     agreement test. The departure from `EntitlementCascade`'s two-mode shape is explained
     where a reader meets it: that registry needs a confirmation count because its delete is
     irreversible on click; a deletion's confirmation is the Deleted page itself, where the item
     sits restorable. → §3.5, §10.11.
   - **The enterprise BEHAVIOUR** — deferred to a named slice, built when a box runs the
     enterprise posture. Its FIELDS ship now, in the one migration, so no later migration is
     needed and the sweep's due-condition already honours a hold. In this delivery enterprise
     behaves as personal does, and the spec, the page, the help card and the ADR say so rather
     than implying a guarantee that is not built. → §3.2, §3.9, §3.10, §3.13, §9, §10.10.

   **What it added: one coverage gate** (`foundation/ops/tests/test_deletion_coverage.py`, §7),
   holding a closed list of every model carrying user content mapped to the ticket kind whose
   handlers reach it, and failing when a listed model has no registered handler for its kind or
   when a model carrying the `owner_kind`/`owner_key` pair is in neither that list nor an
   explicit, reasoned exemption list. In plain words: **a future feature that stores content
   somewhere new must wire it into deletion or the build fails.** That is the one piece of
   complexity this ruling ADDED, and it is the kind the principle favours — a cost paid once,
   by the author, that a maintainer never has to configure and a future contributor cannot
   forget. → §7, §8, §9.
