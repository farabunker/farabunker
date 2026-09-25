# ADR 0020 — Deletion and retention: one ticket, one registry, delete means delete

**Status:** Accepted
**Date:** 2026-09-21

Written against `deletion-semantics` with the personal-posture conversation slice landed, the
`models/queue` half of the same slice not yet started. Its argument lives in
[`docs/superpowers/specs/2026-09-21-deletion-semantics-design.md`](../superpowers/specs/2026-09-21-deletion-semantics-design.md),
cited where a decision's reasoning is there rather than here. **Where the spec and the code
differ, the code is what this ADR records**, and every claim below is checkable against the
tree it describes.

## Context

Before this phase, "delete" meant something different on every surface that had one: a
conversation delete was a hard, synchronous delete already; a document had no delete route a
person could reach at all; an Ask record was read-only history; a generated image deleted
outright, the instant the button was clicked, with no second thought possible. Nothing that
looked like a mistake could be undone, and nothing that promised to keep something briefly
actually kept a promise anybody could check.

The owner asked for one thing: a person can delete something, see it sitting in one place with
the date it will be gone, put it back before that date, or destroy it immediately if they are
sure. A maintainer installs the platform and sets up nothing — the whole feature works on its
shipped defaults — and the design is held to a stated cost/benefit test throughout (§3.0 of the
spec, restated at decision 8 below): as few options as the feature can honestly have, every one
of them with a working default.

Four facts already in the tree shaped the whole design:

1. **The import law runs one way.** `identity/` may import nothing from `agents/`, `tools/` or
   `models/` (rule 4). Content lives in those columns; the ticket bookkeeping cannot.
2. **A single item can touch four columns at once.** A conversation owns turns, tool-call
   records, chat-scoped documents and the images it generated. `agents/` may not import
   `models.queue` (rule 2), so nothing initiated inside `agents/` could ever also clear a
   conversation's queue rows.
3. **This repository writes no Django signals**, anywhere. A cascade that ran off a `post_delete`
   signal would be invisible at the call site that triggered it.
4. **A filesystem delete has no rollback.** Any design that could delete rows and files in the
   same operation had to decide, explicitly, which goes first.

## Decision

### 1. Delete means delete

There is no soft state a person can be shown by mistake and no partial deletion that leaves
bytes behind under a row that claims they are gone. A delete writes a ticket and nothing else;
a purge destroys the content for real — rows, chunks, the managed store directory, everything a
retention handler can reach — and leaves only a content-free audit event. The promise on the
Deleted page ("Purge on 21 October 2026") is a promise the box keeps without anybody opening a
page or running a cron job: the bounded sweep that runs at the end of every delete purges any
other ticket that has already fallen due, so a box used at all stays clean on its own.

### 2. One ticket table, not a soft-delete column on every content model

`identity.DeletionTicket` is one row per deleted item — `kind`, `key`, the actor, an optional
label, the purge date, and three hold columns written by nothing yet (decision 8) — rather than
a `deleted_at` column added to `Conversation`, `Document`, `AskRecord` and `GenerationJob`
individually. Three reasons, in order of how concrete they are:

- **One migration instead of four.** A column per content model is a schema change in every
  column that holds content, landing on whatever cadence each column's own work allows; one
  ticket table is `identity.0004_deletionticket`, alone.
- **A cliff, an actor, a label and a hold are facts about the DELETION, not about the
  conversation.** A `deleted_at` timestamp on `Conversation` answers "when", nothing else; the
  actor who deleted it, whether it is on hold and why, and the label frozen at delete time for a
  Deleted-page row that must still read correctly after the row itself is gone, all need
  somewhere to live that is not the content row. A ticket is that somewhere, once.
- **The Deleted page is one query over one table.** Four soft-delete columns in three columns
  `identity/` may not import would make the page's own query either four queries stitched
  together in a column that cannot know about three of the four tables, or a second table doing
  what the ticket table already does. The ticket table is that answer, built once.

**The one-table decision stands, and a fourth column is written to it now: `parent`, a nullable
self-reference, indexed like every foreign key.** A conversation's generated images are content
of their own, on their own table, with their own visibility rule, and a delete that hid the chat
while leaving them in the gallery would be a box whose "delete" and whose "destroy" disagreed. The
link is a COLUMN on the same ticket table, not a second one, for the same reason the table itself
is one: a child ticket — written for an item that went with another item's delete — is an
ordinary deleted item that happens to have arrived with another, not a different kind of row. "The
three hold columns written by nothing" above stays true of those three; `parent` is the fourth
column, and it is written, at delete time, by `delete_content` alone. What it buys: an item that
arrived with another can be put back with it, restored and destroyed on the same click as the item
it came with — and one deleted on its own is never moved by somebody else's delete, keeping its
own date and its own standing regardless of what else is deleted around it. What it costs: one
nullable self-reference, indexed, added to the feature's single migration (`identity.0004`, edited
in place rather than followed by a second one, because that migration has never run anywhere but
test and preview databases).

**A deleted item's dependents get tickets of their own.** The `parent` column above records the
link; `RetentionHandler.children` is the mechanism that decides who gets one — a resolver, not a
purge, because each generation gets a deletion of its own rather than a silent destruction on the
chat's own date. It is one OPTIONAL dotted-path resolver, `(key: str) ->
list[tuple[str, str]]`, asked exactly ONCE, inside `delete_content`'s own transaction, before a
single row is touched — it only reads, because by the time a purge runs, the answer has already
become rows. Each pair it returns is written as an ordinary ticket, linked back to the parent via
`parent`, with the same `purge_on` date. When the parent's own ticket is the one purged,
`purge_ticket` runs the parent's own registered handlers first and destroys each child afterward,
so a child's bytes are never removed before the item that named it has finished its own row work —
the filesystem-last rule, one level deeper. (A child the unconditional sweep reaches on its own,
before its parent, is an ordinary due ticket and purges on its own — `identity/retention.py::sweep`
already documents that case; the guarantee above is about the family purging together, not about
every possible order the sweep can visit two tickets in.) Two things follow that a person can
observe: restoring the chat restores its pictures with it, and a picture restored on its own — or
deleted on its own before the chat ever was — survives the chat's later permanent delete, because
somebody already said to keep it, or was already shown a date of its own for it, and `parent` is
followed forward only, never backward, to find that out.

### 3. The retention namespace lives on the existing cascade registry, not a second one

`identity/contracts/cascades.py` already held `EntitlementCascade` — the registry an entitlement
delete resolves to answer "this entitlement is going away, what does your column have to do
about it". Purging a deleted item is the same question at a different trigger: "this item is
going away, what does your column have to do about it". `RetentionHandler` is a second dataclass
and a second dict in the same module, not a second module, because a reader who has already
learned one registry's shape — pure, dotted-path handler strings, `AppConfig.ready()`
self-registration, never swallows — should not have to learn a second one for a sibling
question.

**One handler, one mode — deliberately unlike `EntitlementCascade`'s two.** An entitlement
cascade needs a `commit=False` counting pass because deleting an entitlement is irreversible the
instant an administrator confirms it: the count on the confirmation screen IS the confirmation.
A deletion has a better confirmation than any number a dry-run pass could print — the Deleted
page itself, where the item sits named and restorable for as many days as the retention setting
says. So a retention handler's signature is `(key: str) -> int`, one mode, and the integer it
returns has exactly one reader: the content-free `removed={label: count}` detail on the purge's
own audit event.

Two named order bands, `ORDER_ROWS` and `ORDER_FILES`, are the one dependency this registry
tracks: every `ORDER_ROWS` handler runs before any `ORDER_FILES` handler, because a filesystem
delete has no rollback and a row handler that raised after files were gone would leave a
resurrected row pointing at nothing. A handler needing both orders its own reads before its own
writes internally, which keeps the rule to one field with two values.

### 4. Content and audit are split, and the toggle suppresses no event in either position

Every deletion, restore and purge writes an `AuditEvent` — `content.deleted`, `content.restored`,
`content.purged` — through the one writer, `identity/audit.py::record`, with `target_label`
carrying the item's own title only when `IdentitySettings.audit_detail` is on. Off (the shipped
default), the event still says a conversation with this id was deleted by this actor at this
time; it says so with the label blank, never by not being written. An audit trail with a switch
that turns rows off on demand is not an audit trail, so the toggle changes what one field
contains, never whether an event exists.

### 5. The retention policy is centralised on `IdentitySettings` — an owner ruling

The three fields — `retention_days` (default 30, may be 0 for no grace period),
`queue_retention_days` (default 1; blank means no age limit) and `audit_detail` (default off) —
live on the one row that already carries the box's posture, in one "Retention" settings section,
validated by one writer, changed through one audit action
(`identity.retention_policy_changed`). The author's first draft put `queue_retention_days` on
`JobSettings`, beside the queue's own prune; the owner overrode it in plain words: one retention
policy belongs in one place, and two settings pages means two places to look and a real chance
the content cliff changes while the queue silently keeps its own.

`models/queue` reads that number rather than owning it, across the sanctioned
`identity.access` seam (`IDENTITY_PERMITTED`'s allowlist), never `identity.models`. The read is
**non-creating** — it must not call `IdentitySettings.get_solo()`, whose `get_or_create` would
let a worker process materialise the identity singleton — and **tolerant**, wrapped in the same
`ProgrammingError`/`OperationalError` forgiveness `enqueue()` already gives its own
`JobSettings.get_solo()` read, because an exception on the worker's tick path is read as a
crash. `models/queue` gains no new column for this: it asks identity the number, once per prune,
and does not keep a copy.

### 6. Backups are their own layer

A backup taken before a delete still contains what was deleted, and this platform will not
offer a way to reach into an existing backup set and remove it: rewriting a database dump in
place makes every backup's integrity unverifiable, and selectively deleting from a file copy
leaves a set that no longer restores to a coherent box. Deleted content leaves a box's backups
the same way it always would have — as rotation ages the set out — and an operator with a
retention obligation sets that rotation independently of the deletion date. `docs/OPERATIONS.md`
records this in full under "Deleted content and your backups".

### 7. The named residue

Four things a deletion on this box does not reach today, each accepted rather than hidden:

- **`rag.ask` queue rows keyed to no Ask record.** The queue payload carries the question text
  and the actor, never the id of the record `record_ask` writes on success — there is nothing
  to key a purge handler on, and matching by text would be a guess. Those rows age out through
  the queue's own limits, not through a purge.
- **A generation whose tool turn was never written.** A job row created, then the conversation
  turn that would have carried its reference dies before it is written (a crash, a kill, a
  cancelled turn) — the job is reachable only by its own owner, in the gallery, where the
  `vision_job` kind reaches it directly.
- **Engine-side files beyond `delete_job`'s best-effort sweep.** `/engine/input` and
  `/engine/output` are bind mounts tracked by no row at all; the sweep never raises, so a
  missing mount or a permission error there cannot fail a purge, and the Engine files admin page
  remains the operator's manual door for whatever the sweep did not reach.
- **The queue row of a purged conversation, for now.** A conversation's `agent.turn` and
  `vision.generate` queue rows are not removed by today's purge, because `models/queue` has no
  retention handler yet (decision 5, and see below) — the row survives with the person's literal
  message or prompt still in its payload until the queue's own age limit clears it. This is
  worth stating precisely: `foundation/ops/tests/test_deletion_coverage.py`'s `_COVERED` dict
  lists `jobs.InferenceJob` under the `conversation` kind, and that entry passes today, but only
  because *some* handler is registered for that kind — the gate asserts a kind has a resolving
  handler, not that every listed model's rows are the ones that handler reaches. Two tests in
  `identity/tests/test_deletion_demo.py`,
  `test_the_queue_row_survives_until_the_queue_half_lands` and
  `test_the_generations_queue_row_survives_until_the_queue_half_lands`, assert this state
  against real rows rather than leaving it ambiguous, and are the two tests the queue half
  flips.

### 8. The owner's cost/benefit principle, what it cut, and the one thing it added

*(Not the author's — an owner ruling of 2026-09-21, recorded here so a reader of this decision
list sees the whole shape of the design's choices.)* In the owner's own words: keep a good
cost/benefit for every addition, and do not add so many options that a maintainer is paralysed
setting them up. Held as a test the whole design had to pass: a maintainer installs this and has
to set up nothing, and there are as few options as the feature can honestly have.

Three mechanisms were cut:

- **An optional longer retention for tool-call records.** The owner asked for it, then withdrew
  it — three nullable ticket fields, a second sweep condition, a fourth setting and a page state
  nobody could explain in one sentence, for a records obligation nobody has today. Tool-call
  words are scrubbed inline with their conversation's purge, always, with no deferral.
- **A dry-run count mode on retention handlers.** No `commit` flag, no count shown before a
  confirmation, no per-item count line on the Deleted page. The confirmation a deletion gets is
  the page itself, where the item sits restorable — a better confirmation than a count, and one
  that costs no second code path per handler.
- **Most of the enterprise posture's behaviour.** A named, deferred slice: the Hold control, the
  `content.held` audit action and an owner-set cliff floor. Its FIELDS ship now — the three hold
  columns on the ticket, still written by nothing, and the sweep's due-condition already
  excludes a held ticket — so the slice needs no later migration against a live ticket table.
  One control, and only one, was pulled out of this deferral by a later owner ruling
  (2026-09-22): nobody destroys content early on the organisation posture, for anybody, before
  its date — restore is unchanged. Until the rest is built, the spec, the help text and the
  Deleted page say exactly that, and no more, rather than implying a guarantee that is not
  built.

One thing was added: **the deletion-coverage gate**
(`foundation/ops/tests/test_deletion_coverage.py`). It walks every model the app registry
reports, finds every one carrying the `owner_kind`/`owner_key` ownership pair, and fails the
build when one of them is in neither a `_COVERED` list (mapped to the ticket kind whose handler
reaches it) nor an `_EXEMPT` list (one reasoned sentence). In plain words: a future feature that
stores content somewhere new must wire it into deletion, or say in one line why a deletion never
reaches it, or the build fails. That is the one piece of complexity the ruling added, and it is
the kind the principle favours — a cost paid once, by whoever adds the model, that no maintainer
ever configures.

## Where the landed tree differs from the spec

- **The queue half — `models/queue/retention.py`, the `_prune_finished_jobs` age condition and
  `models/contracts/queue.py::forget_jobs` — has not been built.** The spec's kind-`conversation`
  table names a `ROWS`-band handler, `models.queue.retention.forget_conversation`, cancelling
  and deleting a conversation's queue rows; no such module or registration exists in the tree
  today, which is exactly the residue named in decision 7. It is sequenced after the queue
  steward's own reshaping of `models/queue/backend.py` and `models/queue/views.py` lands on
  `dev`, for textual conflict avoidance rather than a migration-ordering constraint — the
  centralised policy leaves `models/queue` with no migration of its own to sequence.
- **`RetentionRefused`'s one documented raiser does not exist yet.**
  `identity/contracts/retention.py` names `models.queue.retention.forget_conversation` as the
  handler that raises it when a worker still holds one of a conversation's jobs, and
  `identity/retention.py::sweep` already catches it and logs it as a warning rather than an
  error — but with that handler unbuilt, nothing in the tree raises it today. The mechanism is
  wired and its catch is exercised by a test double; its real trigger lands with the queue half.
- **The Deleted page's URL is `/identity/deleted/`, not `/settings/deleted/`.** The spec's
  §3.13 describes the second; `identity/urls.py` mounts the three routes under the column's own
  `/identity/` prefix, consistent with every other identity settings page in the tree.

## Consequences

- **A box that is installed and never configured deletes correctly and purges on a 30-day
  cliff — with nothing filled in, nothing scheduled, and no posture decided.** The queue
  retention field ships its own default (a day) too, recorded from day one so the queue half
  needs no later migration against a live settings row, but nothing in the landed tree reads it
  yet — see "Where the landed tree differs from the spec", above.
- **A person's own deleted items are one page, one query, in every posture that has accounts.**
  Restoring is deleting the ticket; nothing about the item was ever touched, so there is nothing
  to put back but the ticket itself.
- **A future column that stores content joins the deletion registry or the build tells it so.**
  The coverage gate is the one piece of complexity this design added on purpose, and it is
  enforced rather than merely documented.
- **The queue row of a purged conversation is a known gap until the queue half lands.** An
  operator reading an `InferenceJob` payload for a conversation somebody deleted and purged today
  will find the person's literal message still there; `manage.py purge_deleted` and the queue's
  own age-based cleanup do not reach it yet, and this is the one place "delete means delete"
  does not yet hold end to end.
- **Backups are not, and will not become, a second delete surface.** An operator's retention
  obligation is met by rotating backups on a schedule independent of the deletion cliff, stated
  plainly rather than implied.
- **The enterprise posture ships three hold columns nothing writes.** A box running that
  posture today restores exactly as a personal-posture box does, and refuses to purge anything
  early, for anybody — that refusal reads the posture, not those columns; the sweep's own
  due-condition already excludes a held ticket, so the columns are read, just never set. The
  Hold control and the operator-set cliff floor, which are what would write them, are a named,
  deferred slice, not a silent gap.

## See also

- [`docs/superpowers/specs/2026-09-21-deletion-semantics-design.md`](../superpowers/specs/2026-09-21-deletion-semantics-design.md)
  — the binding design: the ticket shape and its hold columns in full (§3.2), the per-kind
  handler tables (§3.6), the conversation-born image job mechanism in full (§3.7), the queue's
  age cliff and the non-creating, tolerant seam that reads it (§3.11), the two-slice delivery
  split (§9), and the owner's cost/benefit ruling in the author's own numbered decisions (§11.8).
- [ADR 0016](0016-identity-and-entitlements.md) — the import law's rule 4, the closed
  `IDENTITY_PERMITTED` seam allowlist this feature's fifth seam joins, `IdentitySettings` as a
  database row rather than an environment variable, and the audit table's own append-only guard.
- [ADR 0015](0015-agent-layer-and-tool-contract.md) — the five-column shape and the import law
  this feature's registries answer to.
- `identity/README.md` §9 — "Deletion semantics: the fifth seam", the column-level account of
  everything this ADR records, with the three settings fields' exact copy and the Deleted page's
  three routes.
- `agents/README.md`, `tools/rag/README.md`, `tools/vision/README.md` — each column's own
  retention handler, in its own words.
- `docs/EXTENDING.md` — "Registering a retention handler", the recipe a future column follows to
  join the registry this ADR records the shape of.
- `docs/OPERATIONS.md` — "Deleted content and your backups", and `manage.py purge_deleted` for
  an operator who wants the cliff enforced on a schedule.
