"""Deleting, restoring and purging content -- the orchestration.

A NAMED SEAM, the fifth: every column may import this module, and
`foundation/ops/tests/test_import_law.py`'s `IDENTITY_PERMITTED`
allowlist names it beside `identity.contracts`, `identity.access`,
`identity.request` and `identity.audit`. `identity.models`,
`identity.services`, `identity.views`, `identity.forms`,
`identity.middleware` and `identity.testing` stay closed to every other
column, exactly as before.

IT IS A SEAM BECAUSE THE ANSWER IT GIVES IS A PRINCIPAL-SHAPED ONE.
`ticketed_keys(kind)` says "these keys are deleted"; it does not say
"these conversations", and it could not -- `identity/` may not import
`agents/` or `tools/` (rule 4). Each column turns that answer into an
exclusion on a queryset of its own rows, which is the same division of
labour `identity/access.py`'s docstring records for ownership.

THE ORCHESTRATION LIVES HERE, in `identity/`, and that placement is the
whole reason a conversation's QUEUE ROWS can go in the same transaction
as its turns: `agents/` may not import `models.queue` (import-law rule
2), so no delete inside `agents/` could ever reach them. Identity sits
below every column, so it can run all of their handlers -- by dotted
path, resolved at purge time, importing none of them.

`identity/contracts/retention.py` stays PURE and holds the vocabulary;
this module is the live service, the same split `identity/cascades.py`
has from `identity/contracts/cascades.py`.
"""
from __future__ import annotations

import datetime
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from identity import audit
from identity.access import may_read_owned_row, owned_rows_q, sees_all_content
from identity.cascades import run_children, run_retention
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, SOURCE_WEB,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import SERVICE_PRINCIPAL
from identity.contracts.retention import RetentionRefused
from identity.models import DeletionTicket, IdentitySettings

logger = logging.getLogger(__name__)

# HOW MANY TICKETS ONE SWEEP PASS PURGES. A module constant, never a
# literal at a call site: three callers run this sweep (a delete, a page
# GET, and the command) and a number typed three times is a number that
# will differ three ways.
SWEEP_LIMIT = 25


def ticketed_keys(kind: str) -> list[str]:
    """The item keys currently deleted under `kind`, as strings.

    MATERIALISED into a list rather than left as a `Subquery` --
    `agents.shares.shared_keys`' own recorded reasoning, applied
    unchanged: `DeletionTicket.key` is text and the four kinds' tables
    have three different primary-key types, so a subquery would need a
    per-type cast and would be a silent type mismatch waiting to happen.
    Two small queries on a single-box install beat one clever one.

    Bounded by the open tickets on the box, which the cliff and the
    sweep bound in turn.
    """
    return list(
        DeletionTicket.objects.filter(kind=kind).values_list("key", flat=True))


def visible_tickets(principal, *, settings_row=None):
    """The tickets `principal` may see: their own, or everyone's for a
    principal that `sees_all_content` -- which is every principal on an
    open box, where there is nobody for anything to be hidden from."""
    qs = DeletionTicket.objects.all()
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(owned_rows_q(principal, settings_row=settings_row))


def may_restore(ticket) -> bool:
    """Whether Restore may run for this ticket at all.

    NO PRINCIPAL ARGUMENT, unlike `may_purge` beside it -- deliberately:
    every other question about who may act on a ticket is answered
    before a caller reaches this one (`visible_tickets`'s ownership
    filter, `_own_ticket_or_404`'s standing check), and this predicate
    answers a different kind of question, about the ITEM'S CONTENT, not
    about the asker. A ticket `record_failed_purge` marked destroyed some
    of its content before it failed, so Restore would hand back an item
    that is not the one the person remembers deleting -- true for
    whoever is asking, not only for some principals.

    EVERY TICKET WAS RESTORABLE before this mark existed (`restore_content`'s
    own docstring said so). This is the one refusal that changes that,
    and it lives here, beside `may_purge`, rather than inside
    `restore_content` itself -- the same layer the organisation posture's
    early-destroy refusal already lives at: `deleted_purge` asks
    `may_purge` before calling `purge_ticket`, never inside it, and
    `deleted_restore` now asks this before calling `restore_content`, on
    the same principle: WHETHER to act is decided by the view, HOW to act
    stays a plain mutation.
    """
    return not ticket.content_unrecoverable


def record_failed_purge(ticket) -> None:
    """After `purge_ticket` has raised and its own `transaction.atomic()`
    has already rolled back everything else, persist the one fact that
    can still survive: whether THIS attempt got as far as a files-band
    handler before it failed.

    A WRITE OUTSIDE ANY TRANSACTION, ON PURPOSE. Called only from the two
    places that already run after `purge_ticket`'s rollback has finished
    -- `_purge_due`'s own `except Exception` below, and
    `identity.views.deleted_purge`'s matching one -- a plain queryset
    `.update()` here is a fresh, ordinary write, not a continuation of
    the transaction that just failed. Nothing else about a failed purge
    survives that rollback; this is deliberately the one write site that
    does not try to survive it by staying inside the same transaction.

    READS AN ATTRIBUTE `purge_ticket` SETS ON THIS SAME OBJECT, never a
    database column: `ticket` here is the exact instance the caller
    passed into `purge_ticket`, which `run_retention`'s `on_files_band`
    callback marks with a plain Python attribute the moment a files-band
    handler is about to run. Setting a Python attribute is not a database
    write, so it is not rolled back with everything else -- it is the one
    piece of this call's own memory that outlives the failed transaction,
    and this function is the only place that reads it.

    A PURGE THAT NEVER REACHED A FILES-BAND HANDLER LEAVES THE ATTRIBUTE
    UNSET, and this is then a no-op: that rollback is clean, nothing on
    disk was ever touched, and marking it would refuse Restore for a
    ticket that broke no promise -- exactly the case
    `test_a_purge_that_rolled_back_leaves_a_ticket_restore_still_accepts`
    pins, unmarked, unchanged by this function existing.

    MARKS THE WHOLE FAMILY THIS ATTEMPT ACTUALLY ATTEMPTED, NOT ONLY
    `ticket` ITSELF, AND NOT EVERY TICKET STILL LINKED TO IT EITHER:
    `_files_band_reached` is one Python attribute on the object
    `purge_ticket` was called with, set the instant ANY files-band
    handler in the family -- this item's own, or any ATTEMPTED child's
    -- is about to run (`identity.cascades.run_retention`'s
    `on_files_band`), and `purge_ticket` passes the SAME closure into
    every `_purge_child` call. So the flag cannot say WHICH member of
    the family entered its band, only that the family did -- and a
    child whose OWN files-band handler already destroyed real bytes
    rolls back to an ordinary, undestroyed-looking ROW exactly like its
    parent does, on the very same failed transaction. Marking `ticket`
    and every ticket its own `_attempted_child_pks` names (a second
    plain attribute, set below the point in `purge_ticket` where the
    owned/held partition happens, in one write: a child a family
    member's own bytes truly destroyed is never missed, at the cost of
    also marking a sibling whose own handler never ran at all -- the
    SAME direction `run_retention`'s "before, not after" placement
    already accepts for a single ticket (a files-band handler that
    raises before touching anything still marks). Consistent with the
    rest of this feature's own rule: a false refusal to restore costs a
    person an early click; a false restore hands back an item that is
    not the one they remember, and this trades toward the cheaper
    mistake on both counts -- BUT ONLY FOR A CHILD THIS ATTEMPT COULD
    HAVE REACHED. `parent_id=ticket.pk` ALONE would ALSO catch a child
    the clicker was never allowed to purge (owner ruling, 2026-09-28)
    and a held child (spec section 10.10) -- both are still linked by
    `parent_id` at rollback time, since the detach that unlinks them
    only happens on a SUCCESSFUL purge, and `_purge_child` is never
    called for either, so their own rollback is always clean. Marking
    one of those would strip Restore from an item nobody clicked and
    nothing here ever touched; the whole point of the owner ruling was
    that a stranger to a child cannot affect its retention window, and
    a false mark is exactly such an effect. `_attempted_child_pks`
    reaches nothing when `ticket` is itself a child (a child is never
    asked for children, so the set purge_ticket stashes on it is
    always empty), so a direct purge of one child's own row still marks
    only that row, exactly as before this widening.
    """
    if not getattr(ticket, "_files_band_reached", False):
        return
    attempted = getattr(ticket, "_attempted_child_pks", frozenset())
    DeletionTicket.objects.filter(
        Q(pk=ticket.pk) | Q(pk__in=attempted)).update(
        content_unrecoverable=True)


def may_purge(principal, ticket, *, settings_row=None) -> bool:
    """Whether `principal` may destroy this item's content now.

    The item's OWNER, or a `sees_all_content` principal -- the same
    predicate that already lets them read the content, and the same
    shape `tools.vision.visibility.may_read_job` uses. ON THE
    ORGANISATION POSTURE, NEITHER: nobody destroys content before the
    date they were promised (spec section 3.10), and that refusal
    applies before the owner/`sees_all_content` question is even asked.

    `settings_row`, OPTIONAL, THE SAME SHAPE `visible_tickets` ABOVE
    TAKES: a page building one row per ticket already holds the one
    `IdentitySettings` row for the request and passes it through here,
    so listing many tickets costs one settings read rather than one per
    row.

    THIS CAN NOW REFUSE A TICKET `visible_tickets` ALREADY LISTS -- the
    difference the two functions always existed to hold. On every
    posture but enterprise the two still share the same two predicates,
    owner or `sees_all_content`, so anything visible to `principal` is
    also purgeable by them there; on enterprise a ticket stays visible
    (it is still on the page, still restorable) while this refuses. The
    deferred enterprise hold behaviour (spec section 10.10) is a further
    refusal on top of this one, once a held ticket exists to refuse.

    ONE NAMED EXCEPTION TO THE ORGANISATION POSTURE'S REFUSAL, an owner
    ruling (2026-09-28): a ticket `may_restore` already refuses
    (`ticket.content_unrecoverable` -- a purge destroyed some of its
    content and then failed) may be purged early on that posture too.
    The enforced period exists to protect CONTENT that is still there to
    protect; this item's is already partly gone, so the period guarantees
    nothing further for it and refusing the button only strands a person
    with a row they can neither restore nor finish. The audit trail is
    unaffected either way -- every `content.deleted`/`content.purged`
    event this ticket's family writes stands regardless of which button
    is clicked, so nothing about that record is at stake in this
    exception. THE EXCEPTION IS TO THE POSTURE LINE ONLY, never to
    standing: a marked ticket still falls through to the same owner-or-
    `sees_all_content` check every other ticket on every other posture
    already passes through here, so a principal with no standing over the
    ticket is refused exactly as before, marked or not. AN UNMARKED
    TICKET ON THIS POSTURE IS UNCHANGED -- still refused before its date,
    for everybody -- and the sweep still takes every ticket, marked or
    not, on the date regardless of this function, which the sweep never
    asks.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    # THE ORGANISATION POSTURE DESTROYS NOTHING EARLY, for anybody, WITH
    # ONE EXCEPTION: a ticket already marked `content_unrecoverable`
    # (see the docstring above). Not a standing question and not a hold
    # for an UNMARKED ticket: the promised date is the whole policy for
    # it, and a box that let one person shorten it would be a box whose
    # printed date was advice. WHICH postures enforce it, and which one
    # ticket state is exempt from it, are both policy choices made on
    # this line and nowhere else -- the page hides the control because
    # it asks this, the POST refuses because it asks this.
    if row.posture == POSTURE_ENTERPRISE and not ticket.content_unrecoverable:
        return False
    if sees_all_content(principal, settings_row=row):
        return True
    return may_read_owned_row(principal, ticket)


def delete_content(actor, *, kind: str, key, owner, label: str = "",
                   source: str = SOURCE_WEB) -> DeletionTicket:
    """Soft-delete one item: write its ticket, record the event, and run
    a bounded sweep.

    `owner` is THE ITEM ROW, read for its `owner_kind`/`owner_key`
    columns -- never a `Principal`. `identity/contracts/principals.py`
    and `identity/request.py` are the only two files that may CONSTRUCT
    one (AST-pinned in `foundation/ops/tests/test_import_law.py`), and
    every caller here is holding a row, not a principal. Duck-typed on
    those two attributes, exactly as `identity.access.may_read_owned_row
    (principal, row)` already is.

    THAT IS ALSO WHY `identity.access.owner_fields` IS NOT USED HERE: it
    reads `principal.kind`/`principal.key` -- it is the STAMP a row gets
    when a principal creates it. The ticket is not being created by the
    item's owner; it is recording who the item's owner already was, so
    it copies the row's existing columns across. A row with neither
    column (nothing in this codebase, but the seam is public) stamps
    blank, which is what an unowned item honestly is.

    `get_or_create` ON `(kind, key)`, backed by the unique constraint: a
    second delete of the same item returns the first ticket, writes no
    second audit event, and is therefore safe to race.

    `purge_on` IS COMPUTED ONCE, HERE, from the setting in force now,
    and is never recomputed. The page prints that date and the date is a
    promise; moving it later would make the page's own history a lie,
    and moving it earlier would destroy content sooner than the person
    was told.

    THE BOUNDED SWEEP AT THE END OF EVERY DELETE IS PRUNE-ON-WRITE
    (spec section 3.9, "Three callers"), unconditionally -- not only
    when this call's own ticket is due. It is what makes `retention_days
    = 0` a synchronous purge: the ticket this call just wrote is due
    today, so the same sweep purges it and the content is gone before
    the response returns. It is ALSO what keeps a box that is used at
    all clean, with no scheduler and no cron requirement: any other
    ticket that has already fallen due -- created by an earlier delete,
    on the ordinary default policy -- is purged as a side effect of THIS
    unrelated delete, exactly the prune-on-write pattern `tools.rag.
    services.record_ask` and `models.queue.backend.enqueue` already use.
    A box on the shipped 30-day default therefore never needs anybody to
    open the Deleted page, or a cron job, for "Purge on <date>" to stay
    a promise actually kept. The sweep stays BOUNDED (`SWEEP_LIMIT`) and
    purges each due ticket in its own transaction, always as the SERVICE
    principal -- never the principal that triggered this call -- exactly
    as `sweep` below documents.

    THE SWEEP RUNS AFTER THE `with transaction.atomic()` BLOCK ABOVE HAS
    ALREADY CLOSED, deliberately -- each purge wants its own transaction,
    not one shared with the ticket write it follows. That is only an
    OUTER transaction's boundary, not the request's, because this
    codebase sets no `ATOMIC_REQUESTS`
    (`config/settings.py` has no such key): a future `ATOMIC_REQUESTS =
    True` would fold this call and its sweep into one ambient
    request-level transaction, and a purge failing partway through would
    then roll back the very delete that triggered it -- silently making
    the zero-day promise above false. Left unguarded because nothing in
    this codebase sets it, not because the risk does not exist.

    `retention_days = 0` IS SYNCHRONOUS ONLY WHILE THE DUE BACKLOG STAYS
    UNDER `SWEEP_LIMIT`: this call's own bounded sweep orders every due
    ticket by `purge_on, pk` and takes the oldest `SWEEP_LIMIT`, so on a
    box with `SWEEP_LIMIT` or more OTHER tickets already due, the ticket
    this call just wrote may not be in that batch. The item is hidden at
    once regardless -- the exclusion is unconditional -- and purged by
    whichever sweep reaches it next.

    A CHILD TICKET CARRIES NO LABEL: the parent's title is not the
    child's name, and the child's own content is not copied into
    bookkeeping the purge is meant to leave behind. IT IS STAMPED WITH
    THE OWNER OF THE CONTENT IT DESCRIBES, not the parent item's:
    `run_children` answers an owner alongside each `(kind, key)` pair
    precisely so this can copy the CHILD's own owner columns rather than
    `owner`'s -- the parameter above is the PARENT item's row, and using
    it for a child would file that child under whoever owns the parent
    even when a second principal's content sits inside it (a share that
    let them post and generate; an administrator's duplicate). Those
    columns answer "whose deletion is this", which is what
    `visible_tickets` and `may_purge` read for THAT ticket specifically
    -- restoring the parent brings every ORDINARY child back regardless
    of whose it is, but a child `record_failed_purge` already marked is
    the one exception: its bytes are already partly gone, so restoring
    it would hand back damaged content as if it were whole and delete
    the one column that says otherwise (`restore_content`'s own
    docstring says why it is skipped and detached instead). `purge_ticket`
    below reads a child's own owner to decide whether an explicit click
    may destroy it. AN ITEM ALREADY TICKETED KEEPS ITS OWN DATE AND ITS
    OWN STANDING: `get_or_create` on the unique `(kind, key)` returns the
    existing ticket unchanged, neither re-dated nor adopted. NO SEPARATE
    ZERO-DAY PATH for the children either: they are written with the
    SAME `purge_on` as the parent, so they are due exactly when it is,
    and the unconditional prune-on-write sweep below purges them in the
    same call.
    """
    row = IdentitySettings.get_solo()
    purge_on = timezone.localdate() + datetime.timedelta(days=row.retention_days)
    with transaction.atomic():
        ticket, created = DeletionTicket.objects.get_or_create(
            kind=kind, key=str(key),
            defaults=dict(
                label=label[:255],
                purge_on=purge_on,
                deleted_by_kind=getattr(actor, "kind", ""),
                deleted_by_key=getattr(actor, "key", ""),
                owner_kind=getattr(owner, "owner_kind", ""),
                owner_key=str(getattr(owner, "owner_key", "")),
            ),
        )
        if created:
            audit.record(actor, CONTENT_DELETED, target_type=kind,
                         target_key=str(key),
                         target_label=label if row.audit_detail else "",
                         source=source, kind=kind)
            # `parent=ticket` links each child back to this click's own
            # ticket -- how `restore_content` and `purge_ticket` below
            # find exactly the tickets one delete created, and no
            # others (`identity.models.DeletionTicket.parent`).
            for child_kind, child_key, child_owner_kind, child_owner_key in (
                    run_children(kind, str(key))):
                _child, child_created = DeletionTicket.objects.get_or_create(
                    kind=child_kind, key=child_key,
                    defaults=dict(
                        label="",
                        parent=ticket,
                        purge_on=purge_on,
                        deleted_by_kind=getattr(actor, "kind", ""),
                        deleted_by_key=getattr(actor, "key", ""),
                        owner_kind=child_owner_kind,
                        owner_key=child_owner_key,
                    ),
                )
                if child_created:
                    audit.record(actor, CONTENT_DELETED, target_type=child_kind,
                                 target_key=child_key, target_label="",
                                 source=source, kind=child_kind)
    if created:
        sweep()
    return ticket


def restore_content(actor, ticket, *, source: str = SOURCE_WEB,
                    settings_row=None) -> None:
    """Put the item back: delete the ticket, record the event.

    NOTHING ELSE. The item was never modified, so there is nothing to
    put back -- which is the whole return on not adding per-model
    soft-delete columns. THIS FUNCTION MAKES NO REFUSAL OF ITS OWN.
    `may_purge`'s early-destroy refusal has never been asked in here --
    it is asked by its caller, `deleted_purge`, before `purge_ticket` is
    even called -- and `may_restore` is asked by ITS caller,
    `deleted_restore`, the same way, before this function is called.
    Two refusals exist at that layer today: a held ticket, once the
    deferred enterprise slice can set a hold, and a ticket
    `record_failed_purge` marked -- a purge that destroyed some of this
    item's content and then failed. Neither is made here.

    A TICKET ALREADY GONE (a raced sweep, a double-click) MUST NOT LOG A
    RESTORE THAT DID NOT HAPPEN. THE PARENT IS RE-READ UNDER A LOCK
    FIRST -- a miss there is the already-gone case, and returns before
    touching a child. Each CHILD is then removed by QUERYSET rather than
    by instance, so a child a competing purge already reached reports
    zero rows removed and gets no event of its own; the parent, once its
    lock is held, cannot be raced by anything else, so it is safe to
    delete by instance instead.

    THE PARENT IS LOCKED FIRST, THEN THE CHILDREN -- the same order
    `purge_ticket` below takes. The opposite order (children first,
    parent last) is what this function used before children existed;
    keeping it would let a restore and a purge of the same family lock
    rows in opposite orders and deadlock under real concurrency, so both
    functions now agree on which row is locked first.

    `settings_row`, OPTIONAL, THE SAME SHAPE `visible_tickets`/`may_purge`
    ABOVE TAKE: a caller that already holds the request's one
    `IdentitySettings` row passes it through here instead of paying a
    second read. `None` -- every caller before this parameter existed --
    reads it here, exactly as before.

    A CHILD RESTORED ON ITS OWN EARLIER IS SIMPLY ABSENT by the time this
    runs -- not an error, and it writes no event of its own here: each
    child is removed and reported on its own, for the reason above. A
    parent already gone (the lock read finds nothing) had no children
    left to put back either -- a ticket cannot outlive its parent's row.

    A HELD CHILD (`hold_by_kind != ""`) IS NOT RESTORED, mirroring
    `purge_ticket` exactly and for the same reason: a hold is placed on
    the CHILD'S ticket, and restoring the parent is not something that
    should be able to lift it. It is also DETACHED (`parent=None`)
    before the parent ticket is deleted, because `parent` is
    `on_delete=CASCADE` -- skipping it in the loop below is not enough
    on its own, since the parent row's delete would otherwise destroy
    the held ticket anyway, with no event and no trace. Nothing in this
    delivery writes a hold, so this is latent until the deferred
    enterprise slice (spec section 10.10) can set one.

    A CHILD `record_failed_purge` HAS MARKED (`content_unrecoverable`)
    IS NOT RESTORED EITHER, the SAME shape as a held child and for a
    related reason: a purge destroyed some of this child's content and
    then failed, so `may_restore` already refuses Restore on the
    ticket directly -- restoring the PARENT must not be a second door
    to the same outcome `may_restore` exists to prevent (identity/
    retention.py's own docstring for it). Before this clause, the only
    filter here was `hold_by_kind=""`, so a marked child that was still
    parent-linked (detach only happens on a SUCCESSFUL purge) came back
    as ordinary live content and the one column recording its bytes
    were gone was deleted with the row -- exactly what `may_restore`
    was written to stop, reached through the door it did not guard.
    Skipped here, it is DETACHED the same way and for the same CASCADE
    reason as a held child, and keeps its own ticket, its own
    `content_unrecoverable` mark, and its own eventual purge.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    with transaction.atomic():
        current = DeletionTicket.objects.select_for_update().filter(pk=ticket.pk).first()
        if current is None:
            return
        kind, key, label = current.kind, current.key, current.label
        # THE CHILDREN COME BACK WITH THE PARENT, and they go first and
        # one at a time. The foreign key would cascade them away with
        # the parent row, but a cascade reports nothing per row, and
        # this function's own rule is that A RESTORE THAT DID NOT
        # HAPPEN IS NEVER LOGGED: a child a competing sweep purged in
        # the meantime must not get a `content.restored` event for an
        # item that was in fact destroyed. Deleting by queryset per
        # child answers that question the same way the parent's own
        # lock read above answers it for the whole ticket.
        # `hold_by_kind=""` EXCLUDES A HELD CHILD and
        # `content_unrecoverable=False` EXCLUDES A CHILD `record_failed_
        # purge` ALREADY MARKED, matching `purge_ticket`'s own read
        # below: this restore reaches every ordinary child that arrived
        # with the parent, never one somebody has since put a hold on,
        # and never one whose bytes are already partly gone.
        # NO RESOLVER HERE -- the link is what this restore follows, so
        # an item somebody deleted on its own, or one that went with a
        # different parent, is not this restore's business and is left
        # deleted with the date it was shown.
        restored_children = []
        for child in current.children.filter(
                hold_by_kind="", content_unrecoverable=False).order_by("pk"):
            removed_child, _ = DeletionTicket.objects.filter(
                pk=child.pk).delete()
            if removed_child:
                restored_children.append(child)
        # A HELD OR MARKED CHILD IS DETACHED, NOT MERELY SKIPPED ABOVE --
        # see `purge_ticket`'s own matching comment below for why. One
        # `exclude()` call negates the SAME compound condition the loop
        # above filtered on, so exactly the children the loop did not
        # already remove are the ones detached here.
        current.children.exclude(
            hold_by_kind="", content_unrecoverable=False).update(parent=None)
        current.delete()
        audit.record(actor, CONTENT_RESTORED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind)
        for child in restored_children:
            audit.record(actor, CONTENT_RESTORED, target_type=child.kind,
                         target_key=child.key, target_label="",
                         source=source, kind=child.kind)


def _may_destroy_child(actor, current, child) -> bool:
    """Whether `actor`'s permanent delete of `current` (the parent) may
    also destroy `child`'s content -- `may_read_owned_row(actor, child)`,
    with ONE ADDITION for a child whose owner columns are GENUINELY
    BLANK (`("", "")`): every `GenerationJob` written before `tools/
    vision/migrations/0006_generationjob_owner.py` added the two
    columns, which backfilled nothing, so such a row is un-ownable --
    `Principal.__post_init__` forbids a blank key outright, so no
    principal can ever match one and `may_read_owned_row` alone always
    answers `False` for it. Left there, an explicit "Delete permanently"
    would silently skip such a child on every posture, forever (owner
    ruling, 2026-09-28).

    A GENUINELY BLANK CHILD IS TREATED AS BELONGING TO THE CONVERSATION'S
    OWN OWNER -- `current`'s own owner columns, the ones its own ticket
    carries -- so the permanent delete destroys a pre-tracking image
    exactly as it always did before ownership was stamped at all. THIS
    IS DELIBERATELY NOT `may_purge`'s OWN `sees_all_content or may_read_
    owned_row` MIRROR: it never asks whether `actor` reads OTHER
    PEOPLE'S content, only whether `actor` owns THIS CONVERSATION -- a
    content-reading administrator who is not that conversation's owner
    is still refused, exactly as for a child with a real, different
    owner. A child with real (non-blank) owner columns is unaffected:
    `may_read_owned_row(actor, child)` alone decides it, same as before
    this function existed.
    """
    if child.owner_kind == "" and child.owner_key == "":
        return may_read_owned_row(actor, current)
    return may_read_owned_row(actor, child)


def _purge_child(actor, ticket, *, source: str, row, on_files_band=None) -> dict[str, int]:
    """Run ONE child ticket's handlers, delete it, record its own
    content-free event. NO CASCADE OF ITS OWN -- a child is never asked
    for children, so a link that somehow pointed back at its own parent
    could not recurse.

    `on_files_band`, forwarded straight to `run_retention`: `purge_ticket`
    passes the SAME closure here it passes for this item's own handlers,
    so a child's bytes being destroyed marks the same ticket a failure
    later in the click gets caught against -- the parent's, since that is
    the one `_purge_due`/`deleted_purge` are holding.
    """
    removed = run_retention(ticket.kind, ticket.key, on_files_band=on_files_band)
    kind, key, label = ticket.kind, ticket.key, ticket.label
    ticket.delete()
    audit.record(actor, CONTENT_PURGED, target_type=kind, target_key=key,
                 target_label=label if row.audit_detail else "",
                 source=source, kind=kind, removed=removed)
    return removed


def purge_ticket(actor, ticket, *, source: str = SOURCE_WEB,
                 settings_row=None) -> dict[str, int]:
    """Destroy this item's content, then the ticket. Returns
    `{handler label: rows removed}` -- integers, content-free.

    ONE TRANSACTION. The handlers run, the ticket is deleted and the
    event is written together, so the ticket and the content can never
    disagree about whether the item still exists. The runner never
    swallows, so a handler that raises rolls every ROW change back and
    leaves the ticket standing for the next sweep -- and the view
    renders the sentence rather than a traceback.

    FILES ALREADY REMOVED BY AN EARLIER FILES-BAND HANDLER STAY REMOVED.
    Named, not hidden: a filesystem delete has no rollback. That is why
    every handler must be idempotent and why the next sweep completes
    the purge rather than re-raising on the half it already did. IF THE
    RAISE HAPPENS AFTER A FILES-BAND HANDLER ALREADY RAN, this function
    itself writes nothing about it -- it only rolls back and re-raises,
    exactly as before -- but the `ticket` argument now carries a plain
    attribute (`run_retention`'s `on_files_band` callback sets it) that
    survives the raise, and `record_failed_purge` below reads it from the
    two places this call's own exception is caught.

    A TICKET ALREADY GONE IS A SILENT NO-OP, NOT A SECOND EVENT: two
    sweeps can overlap by design (prune-on-write on every delete, the
    cron command, the Deleted page's own GET) and a person can
    double-click "Delete permanently", so this RE-READS the row under a
    lock inside the transaction before running a single handler, and a
    miss returns `{}` with nothing run and nothing written.

    `settings_row`, OPTIONAL, THE SAME SHAPE `restore_content` ABOVE
    TAKES: a caller that already holds the request's one
    `IdentitySettings` row passes it through here instead of paying a
    second read. `None` -- `sweep` below and `manage.py purge_deleted`,
    which purge one ticket per row on their own pass rather than one per
    request -- reads it here, once per ticket, exactly as before.

    THE RETURNED MAP IS THIS CLICK'S WHOLE TOTAL: a child's counts are
    merged in under that handler's own label ("Generated image": 2),
    because what the person clicked destroyed all of it -- the map is
    keyed by HANDLER LABEL, which is what the Deletion log renders, and
    the labels that can meet in one such map are distinct, pinned by
    `foundation/ops/tests/test_deletion_coverage.py::
    test_labels_that_meet_in_one_purge_map_are_distinct` (distinct
    within a kind, and no handler wearing another kind's plain name);
    two labels colliding there would pool their numbers into a line
    nobody could read apart. EVERY TICKET DESTROYED WRITES ITS OWN
    `content.purged` EVENT, the children included, because the Deletion
    log lists tickets -- an image listed with a date of its own is a
    line of its own when that date is spent -- so one click can write
    several content-free events and the parent's is the one that
    carries the whole map. A CHILD RESTORED ON ITS OWN, OR AN ITEM THAT
    ALREADY HAD A TICKET OF ITS OWN WHEN THIS DELETE RAN, has no link to
    this ticket and is therefore not purged with it: somebody put that
    image back, or was shown a different date for it, and this click is
    not that date.

    A HELD CHILD (`hold_by_kind != ""`) IS SKIPPED AND LEFT STANDING with
    its own ticket, exactly as the sweep's own due condition already
    excludes a held ticket reached directly -- this item's own purge
    still runs, and the other, un-held children still go with it. It is
    also DETACHED (`parent=None`) before this item's own ticket is
    deleted, because `parent` is `on_delete=CASCADE`: skipping it in the
    handler loop is not enough on its own, since the row delete below
    would otherwise destroy it anyway at the database level. Nothing in
    this delivery writes a hold, so this is latent until the deferred
    enterprise slice (spec section 10.10) can set one.

    A CHILD THE ACTING PRINCIPAL DOES NOT OWN IS ALSO SKIPPED AND
    DETACHED, the SAME shape as a held child, for an unrelated reason
    (owner ruling, 2026-09-28): a child ticket now names the owner of the
    CONTENT it describes, which can differ from this item's own owner,
    and a permanent delete of the parent must not be how a stranger to
    that child cuts its own retention window short. It keeps its own
    ticket, date and Restore, exactly as a held child does. `_may_
    destroy_child` BELOW IS WHAT ANSWERS "OWN" HERE, not `may_read_
    owned_row` directly: a child whose owner columns are GENUINELY
    BLANK -- every `GenerationJob` written before ownership tracking
    added its two columns, with no backfill -- is un-ownable by any
    principal, so `may_read_owned_row` alone would skip it on every
    posture forever; `_may_destroy_child` treats that one case as
    belonging to THIS conversation's own owner instead (a second owner
    ruling, 2026-09-28), so the click that always destroyed it before
    ownership existed still does. THE ONE CALLER THIS NEVER APPLIES TO
    IS THE SWEEP: `sweep` always purges as `SERVICE_PRINCIPAL`, so this
    check is keyed on the acting principal BEING that constant, never on
    an ownership predicate alone -- such a predicate answers False for
    `SERVICE_PRINCIPAL` on every user-owned row, and would make the
    sweep skip every child on the box rather than take everything on the
    date it promised.

    A CHILD'S OWN `RetentionRefused` IS NOT CAUGHT HERE, unlike the
    sweep's: it propagates out of `_purge_child` exactly like any other
    exception a handler raises, taking the whole click down with it --
    every ticket in the family stays, including this item's own, and
    the person sees the child's refusal sentence for an item they did
    not directly address. `sweep`'s own `RetentionRefused` handling
    (below) is what turns that into a quiet retry when nobody is
    watching; a direct click surfaces it.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    # THIS IS THE OUTERMOST TRANSACTION ON EVERY PRODUCTION CALL PATH --
    # no view or command in this codebase opens one of its own around a
    # purge (no `ATOMIC_REQUESTS`, no `@transaction.atomic` on
    # `deleted_purge`/`sweep`'s three call sites), so a raise here rolls
    # back through a real `connection.rollback()`, and `record_failed_
    # purge`'s later `.update()` is an ordinary, separately-committed
    # write, not a write inside a block Django has already marked for
    # rollback. A FUTURE WRAPPER THAT NESTS THIS IN A FURTHER
    # `transaction.atomic(savepoint=False)` WOULD SILENTLY BREAK THAT:
    # `identity/tests/test_retention_service.py::
    # TestTheMarkOutsideAnyAmbientTransaction` is the one test that
    # removes the ambient block pytest-django's own fixture otherwise
    # supplies and would catch it.
    with transaction.atomic():
        current = DeletionTicket.objects.select_for_update().filter(pk=ticket.pk).first()
        if current is None:
            return {}
        # THIS ITEM'S OWN HANDLERS RUN FIRST, IN THEIR OWN REGISTERED
        # ORDER, BEFORE ANY CHILD'S HANDLER RUNS -- AND THE ORDER IS
        # LOAD-BEARING, THOUGH NOT THE STRONGER CLAIM IT CAN LOOK LIKE.
        # This is NOT "every row this click touches is gone before the
        # first byte is": this item's OWN registered handlers can
        # themselves be ORDER_FILES and destroy bytes (a conversation's
        # do, today), so a child's rows can be deleted after this item's
        # own bytes are already gone. The filesystem-last rule
        # `identity/contracts/cascades.py` states (ORDER_ROWS before
        # ORDER_FILES) holds WITHIN each `run_retention` call -- this
        # item's own call, and independently each child's own call --
        # not ACROSS the cascade. What running this item's handlers
        # first DOES guarantee: a child's bytes are never destroyed
        # before this item's own handlers have finished running.
        # READ UNDER THE SAME LOCK, and read before anything is
        # destroyed: the parent row's delete would cascade these away
        # without ever running their handlers. `order_by("pk")` because
        # the table's own ordering is newest-first, and the order this
        # destroys things in is worth being the order they were written
        # in rather than whichever way a timestamp fell.
        # `hold_by_kind=""` EXCLUDES A HELD CHILD, matching `sweep`'s own
        # due condition below: this item's purge reaches every ordinary
        # child that arrived with it, never one somebody has since put a
        # hold on.
        candidate_children = list(
            current.children.filter(hold_by_kind="")
            .select_for_update().order_by("pk"))
        # AN EXPLICIT CLICK DESTROYS ONLY THE CHILDREN THE CLICKER OWNS;
        # THE SWEEP TAKES EVERYTHING (owner ruling, 2026-09-28). A child
        # ticket now carries the OWNER OF THE CONTENT IT DESCRIBES
        # (`delete_content`'s own comment says why), which can differ
        # from the parent's -- and "whoever may restore or purge the
        # parent may do so for the whole cascade" stopped being true the
        # moment that became possible: a stranger to the CHILD should not
        # be able to cut that child's own retention window short merely
        # by owning the PARENT. `SERVICE_PRINCIPAL` IS THE ONE CALLER
        # THIS MUST NOT APPLY TO: `sweep` always purges as that principal
        # (`_purge_due` below), for whom `may_read_owned_row` answers
        # False on every user-owned row -- a check keyed on ownership
        # alone would make the sweep skip every child on the box and
        # leak the whole feature on its own promised date. So the gate is
        # keyed on WHO IS ACTING, not on a purge predicate: `actor ==
        # SERVICE_PRINCIPAL` is true on exactly the one call path where
        # nobody actually clicked anything, and PRINCIPAL_KINDS' own
        # vocabulary backs the distinction -- "service" names a machine
        # caller, never a person at a keyboard.
        if actor == SERVICE_PRINCIPAL:
            children = candidate_children
            children_not_owned = []
        else:
            # ONE PASS, ONE PREDICATE EVALUATION PER CHILD -- two
            # comprehensions over the same list asking the opposite of
            # the same question would cost `_may_destroy_child` twice
            # per child and, if it ever stopped being pure, could
            # disagree with itself about which list a child belongs in.
            children, children_not_owned = [], []
            for child in candidate_children:
                (children if _may_destroy_child(actor, current, child)
                 else children_not_owned).append(child)
        # A THIRD PLAIN PYTHON ATTRIBUTE ON `ticket`, alongside `_files_
        # band_reached` below and set for the identical rollback-survival
        # reason: `record_failed_purge` runs AFTER this whole call has
        # already unwound, so it cannot ask the database which children
        # this attempt actually reached -- `children_not_owned` and a
        # held child (excluded from `candidate_children` above) are both
        # still linked by `parent_id` at that point, exactly like every
        # child this attempt DID hand to `_purge_child`, because the
        # detach that unlinks either of them only happens on a
        # SUCCESSFUL purge. Recording the pks THIS ATTEMPT ACTUALLY
        # ATTEMPTED, here, before either loop below can raise, is what
        # lets `record_failed_purge` mark only the family a failed
        # attempt could have touched -- never a child the click was
        # forbidden to reach, and never a held one.
        ticket._attempted_child_pks = frozenset(child.pk for child in children)
        # A PLAIN PYTHON ATTRIBUTE ON `ticket` -- THE CALLER'S OWN
        # REFERENCE, NOT `current` -- so it survives a raise that rolls
        # back everything in the transaction above: setting it is not a
        # database write, so there is nothing here for a rollback to
        # undo. `_purge_due`/`identity.views.deleted_purge` hold this
        # exact object and read the attribute back, in their own
        # `except Exception` blocks, after this whole call has already
        # unwound. One flag for the whole click, a child's own bytes
        # included: restoring the parent restores the family, so a
        # child's destroyed bytes break the same promise this item's own
        # would.
        mark_files_band_reached = lambda: setattr(ticket, "_files_band_reached", True)  # noqa: E731
        removed = run_retention(current.kind, current.key,
                                on_files_band=mark_files_band_reached)
        for child in children:
            # A SECOND PLAIN PYTHON ATTRIBUTE ON THE SAME `ticket` OBJECT,
            # the caller's own reference, for the same reason
            # `_files_band_reached` above is one: a raise here rolls back
            # the transaction but not this attribute, so `_purge_due`
            # can still read WHICH ticket actually raised after the
            # unwind, instead of only knowing which family it was in.
            # Set right before the re-raise, never cleared on success, so
            # it always names the child whose own handler failed, not
            # merely the last one this loop reached.
            try:
                child_removed = _purge_child(actor, child, source=source, row=row,
                                             on_files_band=mark_files_band_reached)
            except Exception:
                ticket._failed_ticket = child
                raise
            for child_label, count in child_removed.items():
                removed[child_label] = removed.get(child_label, 0) + count
        kind, key, label = current.kind, current.key, current.label
        # A HELD CHILD IS DETACHED, NOT MERELY SKIPPED ABOVE: `parent`
        # is `on_delete=CASCADE`, so the row delete two lines down would
        # otherwise destroy it anyway, at the database level, without
        # ever reaching the skip above. `parent=None` is exactly what an
        # item deleted on its own already looks like -- a held child
        # left this way keeps its own ticket, still restorable and still
        # purgeable on its own, the same as any other un-linked ticket.
        current.children.exclude(hold_by_kind="").update(parent=None)
        # A CHILD THE CLICKER DOES NOT OWN IS DETACHED THE SAME WAY, for
        # the identical database-level reason: `parent=None` before the
        # row delete below, or the CASCADE would destroy a child this
        # click was never allowed to reach. It keeps its own ticket, its
        # own date and its own Restore -- standing on its own from here,
        # exactly like a held child, though for a different reason and
        # with no hold columns written.
        if children_not_owned:
            DeletionTicket.objects.filter(
                pk__in=[child.pk for child in children_not_owned]).update(parent=None)
        current.delete()
        audit.record(actor, CONTENT_PURGED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind, removed=removed)
    return removed


def sweep(*, limit: int = SWEEP_LIMIT, source: str = SOURCE_WEB) -> int:
    """Purge up to `limit` due tickets. Returns how many ITEMS were
    destroyed -- one per ticket, a parent's children included, not one
    per due ticket this pass started from.

    ONE DUE-CONDITION: the promised date has arrived and nothing holds
    the ticket. There is no second clause, because there is no second
    cliff and no ticket that outlives its content. The hold half is
    always true today -- nothing in this delivery writes a hold -- and it
    is in the query so the deferred enterprise slice is a control and a
    refusal, not a change to this function.

    `limit` BOUNDS HOW MANY DUE TICKETS ONE PASS STARTS FROM, NOT HOW
    MANY ITEMS IT ENDS UP DESTROYING: a pass that reaches a parent with
    children destroys more than `limit` items, and the next pass simply
    finds fewer. A child the sweep reaches before its parent is an
    ordinary due ticket and purges on its own; the parent's own purge
    then finds one child fewer and completes.

    ALWAYS ACTS AS THE SERVICE PRINCIPAL, whoever triggered it. A sweep
    that ran under the acting principal would write "this member purged
    somebody else's conversation" into the audit trail for a cliff
    nobody clicked. The cliff is the box's own act and the event says
    so; only an explicit click carries a real actor. `source` is how the
    trail tells the three callers apart -- `manage.py purge_deleted`
    passes `SOURCE_CLI`, the delete and the page's GET leave the
    default.

    EACH TICKET IN ITS OWN TRANSACTION, so one failing ticket does not
    block the rest of the batch. The failure is logged with the kind and
    key of the ticket whose OWN handler actually raised -- a child's,
    when a child's did, never the parent this pass started from -- so an
    operator reading the line knows which row to go look at; structural,
    never content, the shape `tools/rag/jobs.py` uses throughout. The
    ticket that stays due for the next pass is still the parent this
    pass started from, regardless of which family member's handler
    raised: a due ticket names what the next sweep will retry, and
    retrying starts from the top of the family every time.

    A `RetentionRefused` IS NOT AN ERROR AND IS CAUGHT FIRST: it is a
    handler saying "not now" for an operator-readable reason -- a
    handler may refuse, for example when a worker still holds one of
    the conversation's jobs -- so it is logged at `logger.warning` --
    one line, no traceback -- and every other exception keeps
    `logger.exception`, which is the failure this batch actually needs
    to be noisy about. BOTH BRANCHES CALL `record_failed_purge`, though:
    a refusal USUALLY means nothing was attempted, but not always -- a
    child's `RetentionRefused` propagates out of `_purge_child` exactly
    like any other exception a handler raises (`purge_ticket`'s own
    docstring says so), so it can arrive after THIS item's own files-band
    handler, or an earlier child's, already destroyed real bytes. A
    single files-band handler can also destroy bytes and refuse in the
    SAME call, the identical shape that already justifies calling this
    function from the exception branch below. `record_failed_purge` is
    self-guarding on whether a files-band handler actually ran, so
    calling it from BOTH branches marks nothing that a refusal reached
    before any band began -- it is never wrong to call, only sometimes a
    no-op.
    """
    due = list(
        DeletionTicket.objects
        .filter(purge_on__lte=timezone.localdate(), hold_by_kind="")
        .order_by("purge_on", "pk")[:limit]
    )
    return _purge_due(due, source=source)


def _purge_due(due, *, source: str = SOURCE_WEB) -> int:
    """`sweep`'s own per-ticket pass, factored out so the race it
    guards against can be driven directly in a test without threads:
    two sweep passes -- this delete's own prune-on-write sweep and,
    say, `manage.py purge_deleted`, landing in the same window -- can
    each already be holding the SAME ticket as due before either has
    purged it. A normal SECOND `sweep()` call would not even see a
    ticket the first one had, by then, already purged for real -- its
    own `due` query would simply not include a row that is gone -- so
    the two snapshots have to be handed to this pass directly to
    reproduce what two GENUINELY overlapping passes would each see.

    COUNTS ONLY REAL PURGES. `purge_ticket` answers `{}` both when it
    purged an item with zero registered handlers and when the ticket
    was ALREADY GONE by the time this pass reached it -- the two are
    not distinguishable from that return value alone, so this checks
    the ticket still existed immediately before calling `purge_ticket`,
    and counts a purge only then. `manage.py purge_deleted` reports
    this number back to whoever ran it; counting a no-op this pass
    inherited from a competing one would tell them two items were
    destroyed when only one was.

    THE NUMBER IS ITEMS DESTROYED, not tickets this pass started from: a
    due ticket with children destroys itself plus each of them, and each
    of those is an item somebody was shown a date for.
    """
    purged = 0
    for ticket in due:
        if not DeletionTicket.objects.filter(pk=ticket.pk).exists():
            continue
        # COUNTED BEFORE THE PURGE, because afterwards these rows are
        # gone: one due ticket can destroy its children too, and each of
        # those is an item somebody was shown a date for. `hold_by_kind
        # =""` EXCLUDES A HELD CHILD -- `purge_ticket` itself will not
        # destroy one, so counting it here would report one more item
        # destroyed than the purge actually reaches.
        addressed = 1 + DeletionTicket.objects.filter(
            parent_id=ticket.pk, hold_by_kind="").count()
        try:
            purge_ticket(SERVICE_PRINCIPAL, ticket, source=source)
        except RetentionRefused as exc:
            # `purge_ticket` marks `ticket._failed_ticket` with the CHILD
            # whose own handler actually raised, when that is what
            # happened -- an operator reading this line is diagnosing a
            # family that will not purge, and the ticket that refused is
            # what tells them which row to go look at, not the parent
            # this pass started from. Unset (this item's own handler
            # refused, no child involved) falls back to `ticket` itself.
            failed = getattr(ticket, "_failed_ticket", ticket)
            logger.warning(
                "identity.retention: purge refused for %s:%s; it stays due -- %s",
                failed.kind, failed.key, exc)
            record_failed_purge(ticket)
            continue
        except Exception:  # noqa: BLE001 -- one bad ticket, not a bad batch
            failed = getattr(ticket, "_failed_ticket", ticket)
            logger.exception(
                "identity.retention: purge failed for %s:%s; it stays due",
                failed.kind, failed.key)
            record_failed_purge(ticket)
            continue
        purged += addressed
    return purged
