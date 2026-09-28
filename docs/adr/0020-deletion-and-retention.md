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
list[tuple[str, str, str, str]]`, answering `(child_kind, child_key, owner_kind, owner_key)` per
child — wrapped as a `ChildTicket` (decision 16, below) at the one point every answer passes
through on its way to `delete_content` — asked exactly ONCE, inside `delete_content`'s own
transaction, before a single row is touched — it only reads, because by the time a purge runs, the
answer has already become rows. Each child it names is written as an ordinary ticket, linked back
to the parent via `parent`, with the same `purge_on` date and the OWNER THE RESOLVER NAMED, never
the parent's own. When the parent's own ticket is the one purged,
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

### 9. The failed-purge mark: a fifth residue closed, not deferred

A handler's own registered order can put a byte-destroying step ahead of a later step in the
*same* purge (the band rule only orders handlers within one `run_retention` call, not across a
failure that comes after); if that later step then fails for any reason, `purge_ticket`'s
transaction rolls the rows back while the files a `shutil.rmtree` already removed stay removed.
This was a fifth named residue for one fix wave of this branch, pinned as a strict `xfail` while
the owner decided between three ways to close it — pin it and close it in the next slice, close it
now in this branch, or close it in a follow-up PR — and never deferred past that decision: the
owner ruled to close it now, in this branch, at the acknowledged cost of the more expensive path.

**What closes it.** `DeletionTicket.content_unrecoverable`, a boolean folded into the same
migration that created the table (decision 2's own table is not yet on `origin/dev`, so the field
joins it rather than opening a second migration). It is written from exactly the two places a
failed purge is already caught — `identity.retention._purge_due`'s sweep-level catch and
`identity.views.deleted_purge`'s POST-level one — both of which already run after `purge_ticket`'s
own transaction has rolled everything else back, so a plain queryset `.update()` there is the one
write that survives. Knowing WHETHER to write it needed one more seam:
`identity.cascades.run_retention` takes an optional `on_files_band` callback, fired the instant a
files-band handler is about to run, before it runs — not after, because a files-band handler that
destroys bytes and then raises later IN THE SAME CALL (the shape `agents.retention.
purge_conversation` has: delete a document's files, then scrub tool records, both inside one
handler function) must still count as "bytes were at risk" even though the handler itself never
returns cleanly. `purge_ticket` sets a plain Python attribute on the ticket object its own caller
already holds a reference to — not a database write, so nothing rolls it back — and the two catch
sites read it back.

**What the mark does.** `identity.retention.may_restore(ticket)` refuses a marked ticket, beside
`may_purge` rather than inside `restore_content` — the same layer the organisation posture's
early-destroy refusal already lives at. The Deleted page renders no Restore control for a marked
row and prints a sentence instead (`copy.RESTORE_REFUSED_LINE`); the restore POST refuses even
called directly, flashing and redirecting rather than 404ing, because the row is still visible on
the page the click came from. On every posture but one, "Delete permanently" already took no
notice of the mark, because ownership already admitted it; the organisation posture's blanket
early-destroy refusal did not carry the same exception, and a marked ticket there was a dead end —
unrestorable and unpurgeable at once, with the page's own copy promising a button it did not
render. An owner ruling (2026-09-28) closed that: `may_purge` gains one named exception, a marked
ticket, on that posture only — the enforced period exists to protect content the item still has,
and a marked item no longer fully has it, so refusing the button protects nothing and strands the
person instead. An UNMARKED ticket on that posture is unchanged, still refused before its date, and
the sweep still takes every ticket on the date regardless of the mark, which `sweep` never asks
about. `purge_refused_line` carries the matching split: an unmarked ticket keeps its original
sentence ("it can be restored, not destroyed early"), and a marked one — reachable only when a
principal has standing over it but the posture still refuses, which after this ruling is nowhere on
the organisation posture and remains true on no other posture either — gets `RESTORE_REFUSED_LINE`
verbatim instead, since the unmarked sentence is backwards for it in both of its own claims.

**Why this, and not the cheaper reading.** A marked ticket still EXISTS; it is not restorable. The
invariant decision 2 states — no purged-but-pending state, a ticket exists exactly while the item
is restorable — gets its one named exception here, in `identity/models.py`'s own docstring, rather
than being quietly falsified. The promise this closes is stated in full where the gap was found:
`agents/tests/test_retention.py::TestTheFailedPurgeMarkRefusesRestore::
test_restore_after_a_purge_that_fails_once_its_files_are_gone_is_refused` — no longer an `xfail`
pinning a gap, now a passing assertion of the invariant the box keeps.

**Two shapes of the gap a later review found, and what closed each.** The mark is one Python
attribute (`_files_band_reached`) set on the ticket object `purge_ticket`'s caller holds, and it
answers only "did the FAMILY'S files band get entered", never "whose bytes are actually gone" —
that vagueness is what let two real gaps hide behind a green gate.

- **A child's own destroyed bytes left the child's OWN ticket unmarked.** `record_failed_purge`
  wrote only the one ticket its caller passed it — the family's top item — while a child ticket is
  an ORDINARY row on the Deleted page, with its own Restore control, no less real for having
  arrived with a parent. A conversation with two generated images, whose second image's handler
  raises after the first image's file is already gone, used to leave the first image's ticket
  standing, unmarked, offering Restore for a file that no longer exists. `record_failed_purge` now
  writes `content_unrecoverable` on the ticket it was called with and every child THIS ATTEMPT
  ACTUALLY REACHED, in one update — the family's files band was entered, so the reached part of the
  family is marked, on the same "when in doubt, refuse the restore, never risk handing one back"
  principle the single-ticket mark already carried. That is wider than "only the ticket whose own
  bytes are gone" — a sibling whose own handler never even ran can be marked alongside one whose
  bytes truly are — and that is accepted for the same reason `run_retention`'s next paragraph
  accepts its own false positive: a wrong refusal costs a click; a wrong restore hands back an item
  that is not the one the person remembers. IT STOPS AT "REACHED", THOUGH: once a child ticket could
  name an owner other than the clicker's own (decision 11), "every ticket presently linked as a
  child" started catching two children `_purge_child` is never even called for — see decision 13.
- **A refusal could follow bytes already destroyed, and never marked.** `RetentionRefused` is not
  an error — a handler saying "not now" for an operator-readable reason — so it is caught
  separately from an ordinary exception, before `record_failed_purge` was ever called. But a
  child's `RetentionRefused` propagates out of `_purge_child` exactly like any other exception a
  handler raises, so it can arrive after this item's own files-band handler, or an earlier child's,
  already ran; and a single files-band handler can destroy bytes and then refuse in the same call,
  the identical shape the ordinary-exception branch already existed to mark for. Both catch sites
  now call `record_failed_purge` from the refusal branch too — it is self-guarding on whether a
  files-band handler actually ran, so a refusal that never reached one still marks nothing.

**What is still accepted residue, not closed by either fix above.** A files-band handler that
raises BEFORE touching a single byte still marks the ticket, because the callback fires before the
handler runs, not after — deliberately, since "after" cannot see a handler that destroys bytes and
then raises in the same call, which is the shape this whole mechanism exists for. Nothing un-marks
a ticket a false alarm like that leaves behind: on the sweep's own retry path the ticket stays due
and the next attempt, succeeding, deletes the ticket row and the question stops mattering: on the
"Delete permanently" click path a ticket purged before its date is not due, so nothing retries it,
and a transient failure with zero bytes actually touched converts a fully intact conversation into
a delete-only row for the rest of its retention period. A clearing path was considered and set
aside: the only cheap trigger available — "the next purge attempt reaches the files band cleanly"
— is, for the tickets where it would fire at all, the SAME event that deletes the ticket row
anyway, so it clears nothing a person can observe; a trigger that clears the flag independently of
that would have to tell "this retry's own rows-band failure, unrelated to the earlier mark" apart
from "the earlier mark was right and this retry failed the same way", which the current per-attempt
design — each purge restarts from its kind's first handler, with no memory of an earlier attempt —
cannot do without either instrumenting every handler's own partial progress or accepting a real,
if narrow, chance of silently un-marking a ticket that IS missing bytes. Given the choice between a
false refusal and a false restore that this whole feature already resolves the same way everywhere
else, the flag stays over-inclusive and undocumented-until-now, rather than gaining a clearing path
whose failure mode is the one this mechanism exists to prevent.

Separately, and pre-existing rather than introduced by either fix above: `sweep`'s own due query
filters on `purge_on` and `hold_by_kind` only, with no `parent__isnull` clause, so a child ticket is
due IN ITS OWN RIGHT. When a parent's purge fails, the sweep's per-ticket pass continues and can
reach and purge that SAME child independently, on its own due pass — destroying that child's content
for real and deleting its ticket, while the parent's ticket (unmarked, if the parent's own failure
came before its files band) still offers Restore for a family whose pieces are, by then, partly
gone. This wave does not close that window either.

**The sweep's own breadth is wider than a click's, and every argument above was made from the
narrower case.** Each paragraph in this decision that accepts the over-inclusive mark as a
reasonable trade reasons about ONE family, under ONE failed attempt — a person's own "Delete
permanently" click, or one due ticket the sweep happened to fail on. `_purge_due` runs as
`SERVICE_PRINCIPAL` for every due ticket `sweep`'s own query returns in a single pass, up to
`limit`, and `identity.retention.purge_ticket` hands that principal `children = candidate_children`
— every non-held child, regardless of who owns it (decision 13's own `_may_destroy_child` narrowing
does not apply to the service principal at all, by design: see decision 11's reasoning for why the
sweep must reach every child on the box). A failure in one ticket's own pass does not stop the loop
`_purge_due` runs the rest of `due` in: each failing ticket marks its OWN family independently, and
there is no shared state, and no limit, on how many DIFFERENT families one unattended run can mark
this way. A transient condition that outlasts a single ticket's own attempt — a busy mount overnight,
a database blip wider than one savepoint — fails every ticket the pass reaches while it lasts and
marks every one of their families, each marked child losing Restore for the rest of its own
retention period, with the identical "no clearing path" already accepted above for a single family.
A person's own click bounds the blast radius to what that one click touches; the sweep has no such
bound, and it runs unattended, with nobody reading the warning line each failure writes as it
happens — precisely the condition under which this residue reaches furthest. Stated here honestly,
rather than left implied by a decision whose own worked examples all argue from the single-family
case: no clearing mechanism is attempted for this either, for the reason already given above — the
only cheap trigger available clears nothing a person can observe, and a trigger that tried to clear
independently could not tell a stale false alarm from a fresh, real one without either instrumenting
every handler's own partial progress or accepting a real chance of silently un-marking a ticket that
does have bytes missing.

### 10. A duplicate or a branch must not ticket a job another live conversation still shows

`agents.visibility.duplicate_conversation`/`branch_conversation` copy a source conversation's
`artifacts` and `data` VERBATIM onto the new row (`_copy_turns_into`) — a deliberate design choice
(the copy's tool cards render exactly as the original's did), but it means a copy's tool turn names
the EXACT SAME `output:<id>` reference and the exact same generation id the original's own turn
does. `agents.retention.conversation_children` walked only the conversation being deleted, so
deleting a COPY found that shared reference on the copy's own turns and ticketed the job it names —
hiding, and on the copy's own date destroying, a picture a DIFFERENT, live, undeleted conversation
still displays. This needed no sharing and no second principal at all: A duplicating and deleting
A's own conversation reproduced it, discovered only because the investigation asked the resolver's
question literally ("what does THIS conversation's turns name") rather than assuming a copy could
not exist.

**What closes it.** `conversation_children` now excludes, before asking the image column to resolve
anything, any artifact reference or generation id some OTHER conversation's own turns still carry —
querying `Turn` directly (bounded: it runs only when this conversation actually names something, and
the containment clauses narrow before the Python loop) rather than the image column, because the
protecting fact ("another thread still shows this") is visible entirely from the `Turn` table
`agents/` already owns, with no need to resolve a reference to a job at all just to answer it. A
conversation already ticketed does not count as "another live conversation" protecting the job —
its own delete either already ticketed the same job (harmless; tickets are idempotent on `(kind,
key)`) or will, the next time its own `conversation_children` runs — so the exclusion is eventually
consistent, not a permanent leak: once every conversation naming a job is itself deleted, whichever
delete runs last is the one that finally reaches it. Landed on this branch, pre-merge: none of this
machinery is on `origin/dev`, so the defect was real in this branch only, never in production.

### 11. Child tickets are stamped with the owner of the content they describe, not the parent's

Decision 2 above describes the `parent` link and says a resolver names what else is deleted; it does
not say, and this correction says now, whose the resulting ticket is. The code did answer that
question, just wrongly: `delete_content` stamped every child ticket it wrote from the PARENT ITEM's
own `owner_kind`/`owner_key` — the same two columns as the parent's ticket — on the reasoning that
"whoever may restore or purge the parent may do so for the whole cascade." That reasoning holds only
while a conversation's owner and its generated images' owner are always the same principal, which
this platform never actually guaranteed: a workstream share admits a `use`-level recipient to post
into somebody else's conversation and generate an image there, and an administrator with
`admin_sees_content` on may duplicate a member's conversation and delete the copy. In either shape a
second principal's `GenerationJob` sits inside a conversation that is not theirs, and the wrong stamp
followed from that: the actual owner's picture could vanish from their own gallery with no row
anywhere on their own Deleted page saying so, unrestorable by them, and destroyable early by whoever
happened to click delete on the chat — the mirror of "delete means delete" (decision 1), now applied
to somebody who deleted nothing.

**What closes it.** `RetentionHandler.children`'s dotted path widens from `(key: str) -> list[tuple[
str, str]]` to `(key: str) -> list[tuple[str, str, str, str]]` — the owner rides alongside each
`(child_kind, child_key)` pair, because `identity/` cannot look one up itself (rule 4 forbids
importing the column that would know). `identity.cascades.run_children` carries the widened tuple
through its dedupe (which still keys on `(kind, key)` only — a `(kind, key)` pair names one item, so
its owner cannot honestly differ between two resolvers that both name it); `identity.retention.
delete_content` stamps the child ticket's owner columns from the tuple instead of from the parent
item's row; `agents.retention.conversation_children` and `tools.vision.retention.
resolve_artifact_jobs` pass the owner through; and `tools.vision.services.existing_job_ids` — the one
unscoped read of `GenerationJob.objects` a deletion needs to know a candidate id still exists at all
— answers the owner columns alongside each id, a widened read in a file IA-1 already lets touch that
table, not a new site. About six files, no migration: the two owner columns already exist on every
ticket.

**The sub-choice, asked of the owner and answered (2026-09-28): a permanent delete of the parent
skips a child the clicker does not own.** Two ways to close the remaining asymmetry were on the
table once the ticket is correctly stamped — leave "Delete permanently" reaching every child
regardless of whose it is (cheapest, but a row with a Restore button somebody else can destroy first
is a promise with a race in it), or have the clicker's own permanent delete detach a child they do
not own, exactly as a held child is already detached, rather than destroy it. The owner chose the
second: `purge_ticket` compares each candidate child's owner against the ACTING principal — never
against a fixed predicate alone, because `sweep` always purges as `SERVICE_PRINCIPAL`, for whom an
ownership check answers False on every user-owned row, and applying it there would make the sweep
leak every child on the box rather than take everything on its date. So the check is keyed on WHO IS
ACTING: `SERVICE_PRINCIPAL` skips nothing (the sweep still takes everything, on the date, regardless
of ownership — nothing outlives the promise it printed), and any other principal — an explicit click
— skips and detaches a child they do not own, leaving it standing with its own ticket, its own date
and its own Restore, exactly like a held child.

**A related question the investigation asked, and the answer this fix gives by construction, not by
a second rule.** An administrator with `admin_sees_content` on can duplicate a member's conversation
and delete the copy, ticketing the member's images under the admin — the cross-owner shape of
decision 10 above, `duplicate_conversation`'s copy problem restated for a second principal instead of
the same one. Decision 10's own fix already closes it: at the moment the admin deletes the copy, the
ORIGINAL conversation (the member's, untouched) is still live and still names the same job, so the
live-elsewhere exclusion drops it before any owner is even consulted — no ticket is written under
anybody. Tracing the other order (the member's original deleted first, admin's copy still live) is
symmetric: the exclusion protects the job until both conversations naming it are gone, at which point
whichever delete runs last reaches it, correctly stamped with the member's own owner columns by
THIS decision's own stamping fix. One duplicate-path fix and one ownership fix, applied together, close
both the same-owner and the cross-owner shapes of the same underlying defect — the resolver answering
a question ("what does this conversation's own turns still name") that a copy makes ambiguous.

### 12. The check-then-act trap in a managed-store directory removal, and the shape to write instead

Three authors wrote `if directory.exists(): shutil.rmtree(directory)` in this branch independently —
two wrote it (`tools/rag/store.py::remove_document_files` and, in the vision column,
`tools/vision/store.py::remove_job_files` and `remove_staged_input`), one found and fixed it — and
the vision column's own implementer added its second site on the same reasoning, on their own,
before word of the first fix reached them. That convergence is the evidence that this is the
OBVIOUS way to write "remove this directory if it is there," not a lapse by anyone in particular,
which is why the lesson belongs here rather than only in a fix's own commit message.

**The trap.** An existence check before a removal is a check-then-act race: the directory can
vanish — another retry sweep reaching the same item, an operator, a sibling handler — in the gap
between the check answering true and the removal actually running, and the unguarded removal then
raises on a directory the check just said was there. A fixture that deletes the directory BEFORE
calling the function never finds this: the check honestly sees nothing and skips the removal, which
is the branch that already worked. The race is reachable only when the check succeeds and the
removal then fails against nothing — a different test from "already gone," and one that is easy to
skip writing because it reads, at a glance, as redundant with it.

**The shape to write instead — the SHAPE, not the site.** A reader who learns "this construction is
dangerous at `store.py` line 207" learns to be careful at one line. The rule that generalises is:
byte removal tolerates absence AT THE REMOVAL ITSELF, never at a check beforehand.

```
    try:
        shutil.rmtree(dest_dir)
    except FileNotFoundError:
        logger.info("... nothing to remove at %s", dest_dir)
```

No existence check first — the check is what creates the window, so removing it removes the race
rather than narrowing it. `FileNotFoundError` covers both "never existed" and "vanished between the
look and the call," which are the same outcome to a retrying caller. `shutil.rmtree(dest_dir,
ignore_errors=True)` is NOT this idiom, even though it also avoids raising on a missing directory:
it swallows every `OSError`, so a permission error, a busy mount or a genuine I/O failure becomes
silence too, and a purge whose caller depends on "success means the bytes are gone" (the retry
contract `identity/contracts/cascades.py::RetentionHandler` states) reports success while the bytes
remain on disk. The rule under the idiom: **absence is not an error; everything else still is.**

**This branch did not create the defect; it created the first caller for which it matters.** The
tolerant, swallow-everything form (`ignore_errors=True`) was already present twice in the same
package before this branch (`tools/rag/ingest.py` and `tools/rag/media.py`), for best-effort disk
hygiene after an already-failed attempt, where a leaked directory is the only cost of swallowing an
error. So the check-then-act shape at the three sites below is not ignorance of the tolerant idiom —
it is the idiom the author had in mind for a DIFFERENT caller: best-effort cleanup after a failed
ingest got the tolerant form; the purge path, whose caller retries, got the fragile one. The same
construction that is merely imprecise for a best-effort cleanup is actively wrong for a path whose
caller retries on failure — decided by the caller, not the call. Three sites carry the shape:
`tools/rag/store.py` ~207, `tools/vision/store.py` ~174 and ~198. The distinction the investigation
found: **tolerance of ABSENCE and tolerance of a RACE are different properties**, and a clearance
can assert the first (a fixture proving "safe when it never existed") while the second goes
unexamined for months, because the vacuous fixture and the real pin look identical from a test name
alone.

**One check in the same family is a guard, not this race, and stays.** `tools/vision/store.py::
remove_staged_input`'s containment check — that a staged path named by a database row really lives
inside the staging directory before anything is removed — is deliberately left as check-then-act,
with its own docstring saying why: it protects against a row naming a path OUTSIDE the staging area
so a job's own input can never be deletable through this function, not against the directory
disappearing, and it stays a check because nothing about the race this decision fixes applies to it.
That sentence exists precisely because a later wave removing existence checks on sight is exactly
when someone deletes the line above it for looking similar.

**What closed it.** `tools/rag/store.py::remove_document_files`, this branch, `e4ab908`. The vision
column's own fix for `remove_job_files` and `remove_staged_input` landed on a local branch, unpushed
as of this writing — cited here by file:line rather than by SHA until it merges.

### 13. A failed purge's mark must never reach a child the click was forbidden to touch

Decision 11 widened `purge_ticket` to skip and detach a child the clicker does not own, and decision
9's own family-wide mark (`record_failed_purge`'s `Q(pk=ticket.pk) | Q(parent_id=ticket.pk)`) was
written before that skip existed. After decision 11 landed, the two no longer agreed: at the moment
`record_failed_purge` runs, `purge_ticket`'s transaction has already rolled back, so EVERY child
still carries `parent_id` — including a not-owned child and a held child, both of which
`_purge_child` was never called for, and both of which are detached only on a SUCCESSFUL purge that
this failed one never reached. A transient error on the conversation's own files-band handler
therefore marked a child the click was structurally incapable of touching, for a person who clicked
nothing and owns content that was provably still intact. That inverted decision 11's own promise —
"their owner keeps the row, the countdown and the ability to restore" — and, for a not-owned child,
landed on a third party rather than on the clicker whose wrong refusal decision 9 already accepted
the cost of.

**What closes it.** `purge_ticket` stashes the pks it is actually about to hand to `_purge_child` —
`ticket._attempted_child_pks`, a second plain Python attribute beside `_files_band_reached`, set
before either child loop can raise so it survives the rollback the identical way. `record_failed_
purge` filters to `Q(pk=ticket.pk) | Q(pk__in=attempted)` instead of `parent_id`, so a not-owned or
held child's own rollback — always clean, since its handler never ran — is never mistaken for a
bytes-at-risk one. Decision 9's family-wide over-marking is otherwise unchanged: a child the clicker
DOES own, whose own handler never got to run before a sibling's failed, is still marked alongside
one whose bytes are truly gone, for the reason decision 9 already gives.

**What this does not change.** Decision 9's own accepted residue — a files-band handler that raises
before touching a byte still marks the ticket, and nothing un-marks a click-path ticket a false
alarm like that leaves behind — is untouched; this decision narrows WHO can be marked by a given
failure, not WHETHER an owned family can still be over-marked by one.

### 14. A child with a genuinely blank owner belongs to the conversation's own owner

Decision 11 stamps a child ticket from the content's own owner columns, read off the row itself. A
`GenerationJob` written before `tools/vision/migrations/0006_generationjob_owner.py` added those two
columns carries `("", "")` — that migration is a bare `AddField` pair with no backfill — and a blank
pair is un-ownable: `Principal.__post_init__` forbids a blank key outright, so no principal can ever
satisfy `may_read_owned_row(actor, child)`. Decision 11's own permanent-delete gate asked exactly
that predicate and nothing else, so an explicit "Delete permanently" silently skipped a pre-tracking
image on every posture, including the open-box case where the clicker is the only principal there
is — the conversation vanished, the audit event was written, and the picture was not destroyed.

**What closes it.** An owner ruling (2026-09-28): a genuinely blank owner is treated as belonging to
the conversation's own owner, so the permanent delete destroys a pre-tracking image exactly as it
always did before ownership was stamped at all. `identity/retention.py::_may_destroy_child(actor,
current, child)` holds the one extra condition — `child.owner_kind == "" and child.owner_key == ""`
reads `current`'s (the parent ticket's) own owner columns instead of `child`'s — and nothing wider.
It is deliberately NOT `may_purge`'s own `sees_all_content or may_read_owned_row` mirror: that would
hand a content-reading administrator power over another member's genuinely OWNED content, the exact
shape decision 11 exists to deny. A child with real, non-blank owner columns is unaffected by this
decision at all. No migration and no backfill for `0006`'s two columns — the owner considered and
declined one for this branch; a blank pair stays blank on disk, and only the permanent-delete
predicate treats it specially.

### 15. Restore must not resurrect a marked child, or destroy the one record that its bytes are gone

Decision 9 gave `content_unrecoverable` and `identity.retention.may_restore` the job of refusing
Restore for a ticket a failed purge already marked. That refusal was asked in exactly one place —
`identity.views.deleted_restore`, about the ticket the click names — and nowhere else. `restore_
content`'s own child loop, reached whenever the PARENT is restored, filtered only on `hold_by_kind
=""`; it never asked `may_restore`'s own question of a child at all. The sequence this missed: B
owns an image inside A's conversation (decision 11's own cross-owner case); A deletes the
conversation; B clicks "Delete permanently" on their own row — reachable, `may_purge` allows it
off the enterprise posture; the files band destroys bytes and something after it raises;
`record_failed_purge` marks the child, which is still `parent_id`-linked because the detach in
`purge_ticket` (decision 11, decision 13) only runs on a SUCCESSFUL purge. A then restores the
PARENT: `may_restore(parent)` is true (the parent was never attempted), and the unfiltered child
loop deleted the marked ticket along with every ordinary one — handing B's half-destroyed image
back to the gallery as ordinary live content and deleting `content_unrecoverable`, the one column
recording that its bytes were gone. That is the exact outcome decision 9 exists to prevent,
reached through the one door `may_restore` does not guard, because nothing asks it there.

**What closes it.** `restore_content`'s child loop now filters on `hold_by_kind="" AND
content_unrecoverable=False` — the same compound condition `purge_ticket` already uses to decide
which children it may destroy — and the single `exclude()` call that detaches skipped children
negates that exact condition, so a marked child is detached (`parent=None`, before the `CASCADE`)
rather than restored, the same shape decision 9's own held-child handling already has. It keeps its
own ticket, its own mark, and its own eventual purge on its own date; `may_restore` still refuses
Restore on it directly, exactly as before. This is a filter change, not a new predicate: `may_
restore` itself is unchanged, and is still asked in exactly the one place a click can reach a
ticket directly. `identity/README.md` and `identity/models.py`'s own field-level documentation of
`parent` both previously asserted "restoring the parent is harmless regardless of whose [content]
it is" — true before this mark existed, and false the moment content can be partly destroyed before
a restore reaches it; both are corrected to name the one exception.

**What this does not change: a race pre-dating this fix, verified still open.** `restore_content`'s
child read — `current.children.filter(...)` inside the transaction, deleted by queryset per row —
takes no `select_for_update()`, unlike `purge_ticket`'s own read of the same rows. A child's purge
running concurrently with a parent's restore can still land `record_failed_purge`'s mark in the
window between this loop's read and its delete, reviving the exact outcome this decision closes for
the ordinary case. Locking the read (`select_for_update()`) would not close this, and is not
attempted here: `record_failed_purge` writes the mark from OUTSIDE any transaction, deliberately,
after `purge_ticket`'s own transaction has already rolled back and released every lock it held
(decision 9's own "a write outside any transaction, on purpose") — there is no lock this restore
could hold that the mark's own write would still be waiting behind. Closing this window would need
the mark itself to be written differently, inside a transaction restore's own lock could serialise
against, which is a larger change than this decision makes and is left to a later one. Recorded
here so this decision is not read as having closed it.

### 16. The child-ticket contract is a named type, not a blind 4-tuple — corrective, not precautionary

Decision 11 widened a resolver's answer from `(kind, key)` pairs to `(kind, key, owner_kind,
owner_key)` 4-tuples, read positionally at every call site: `identity.cascades.run_children`
unpacks a resolver's return value by position, and `identity.retention.delete_content` unpacked
`run_children`'s own answer by position again, two loop-variable lists apart from the resolver that
first produced the values. This is corrective, not a general hardening of a shape that merely looks
risky — three concrete confusions were found VERIFIED IN THE TREE, not hypothesised:

1. **Elements 3 and 4 are an authorization key, and nothing validated them.** `DeletionTicket.
   owner_kind` is a bare `CharField(max_length=32)` with no `choices`, and `delete_content` wrote
   whatever `run_children` hand it straight through. Transposing the pair at any of the three legs
   between a real row and the write — inside a resolver, inside `run_children`'s dedupe, or at a
   future call site that still unpacks positionally — produced `owner_kind="<uuid>"` with no error
   ever, and the ticket became UN-OWNABLE: invisible on every Deleted page (`visible_tickets`
   filters by owner), skipped-and-detached by every explicit "Delete permanently" click (decision
   11's own `_may_destroy_child`), reachable only by the unconditional sweep. Before decision 11,
   the owner arrived as an OBJECT whose own model guaranteed its shape (`owner` in `delete_content`'s
   signature, read via `getattr`); decision 11 made it arrive as a bare string a peer column
   asserted, with nothing checking it.
2. **A wrong-arity resolver was caught only by accident, at a user's click.** `RetentionHandler.
   __post_init__` validates `kind`, `key`, `label` and that `handler` is a dotted path; `children`
   only gets a dottedness check on the STRING naming the resolver, never on what the resolver
   returns. A resolver that answered a 2-tuple raised "not enough values to unpack" inside `delete_
   content`'s transaction — a real exception, but one that surfaced far from the mistake, at the
   first real delete to reach it, rather than at review time.
3. **The document a future implementer is TOLD to read still described the old contract.**
   `AGENTS.md` names `docs/EXTENDING.md` as the file to read before adding a retention handler,
   "do not reverse-engineer an existing one" — and that file still described `children` as `(key:
   str) -> list[tuple[str, str]]` returning `(kind, key)` pairs, decision 11's own widening never
   having reached it.

**What closes (1) and (3).** A frozen `identity.contracts.cascades.ChildTicket(kind, key,
owner_kind, owner_key)` `NamedTuple`, beside `RetentionHandler` in the same pure module — a DROP-IN
for the shape it replaces (a resolver still returns a plain 4-tuple; `ChildTicket(*that_tuple)` is
the same call the shape already supported), so every existing resolver keeps working unchanged.
`identity.cascades.run_children` wraps each resolver answer in one at the one point every answer
passes through on its way to `delete_content`, which now reads `child.kind`/`child.key`/
`child.owner_kind`/`child.owner_key` by name instead of four positional loop variables. Beside it,
`delete_content` gained `_validate_child_owner`: `owner_kind` must be one of `identity.contracts.
principals.PRINCIPAL_KINDS`, or the pair must be the blank exception decision 14 already carries
(`("", "")`) — anything else raises `ValueError` immediately, before a single child ticket is
written, naming the transposition rather than minting an invisible row. That closes confusion 1.
`docs/EXTENDING.md` is corrected to the 4-tuple contract, including what the owner columns mean and
why a transposed pair now fails loudly — that closes confusion 3.

**Confusion (2) stands.** `identity.cascades.run_children` still unpacks a resolver's answer into
four positional loop variables (`for child_kind, child_key, owner_kind, owner_key in
import_string(spec.children)(key):`), and `RetentionHandler.__post_init__` still gives `children`
nothing but a dottedness check on the STRING naming the resolver — never on what it returns. A
resolver that answers a 2-tuple still raises "not enough values to unpack" at the same line, the
same moment, the same message, as before this decision. Closing it properly would mean calling the
resolver at IMPORT TIME, from inside `__post_init__`, with some fake key, purely to count the
returned tuples' length before any real delete ever runs — a far larger and stranger change than
validating a value already in hand at the write, and not attempted here; left to a later decision if
the residue is ever judged worth it. This decision closes what a wrong-arity resolver's answer can
silently BECOME once caught (1) and what a future implementer is TOLD (3); it does not move WHEN a
wrong-arity resolver is first caught (2) at all.

This was weighed against validating `owner_kind` alone on the existing bare tuple, without a named
type: that would have closed confusion 1 exactly as this does, closed nothing else, and left the
contract just as invisible at every OTHER site that reads a plain 4-tuple — the NamedTuple's real
value is closing 3 and making the contract self-documenting at every read site, not only the one
site that validates it. Either way, confusion 2 was never on the table.

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
- **`resolve_artifact_jobs`'s signature is no longer `(key: str) -> list[str]`.** The frozen spec
  document still prints that shape; decision 11's ownership widening changed it to `list[tuple[str,
  str, str]]` (`(job_id, owner_kind, owner_key)`), and every other resolver `RetentionHandler.
  children` names widened the same way. The spec is left as written rather than edited in place;
  this bullet is the landed tree's own record of the divergence.

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
