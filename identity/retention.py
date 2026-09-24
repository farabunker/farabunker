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
from django.utils import timezone

from identity import audit
from identity.access import may_read_owned_row, owned_rows_q, sees_all_content
from identity.cascades import run_children, run_retention
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, SOURCE_WEB,
)
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


def may_purge(principal, ticket, *, settings_row=None) -> bool:
    """Whether `principal` may destroy this item's content now.

    The item's OWNER, or a `sees_all_content` principal -- the same
    predicate that already lets them read the content, and the same
    shape `tools.vision.visibility.may_read_job` uses. There is no
    posture branch: in this delivery the enterprise posture behaves
    exactly as personal does, and the refusal that will differ is the
    deferred enterprise slice's (spec section 10.10), not this one's.

    `settings_row`, OPTIONAL, THE SAME SHAPE `visible_tickets` ABOVE
    TAKES: a page building one row per ticket already holds the one
    `IdentitySettings` row for the request and passes it through here,
    so listing many tickets costs one settings read rather than one per
    row.

    TODAY THIS CANNOT REFUSE A TICKET `visible_tickets` ALREADY LISTS --
    the two share the same two predicates, owner or `sees_all_content`,
    so anything visible to `principal` is also purgeable by them. The
    two functions are still separate rather than one boolean reused,
    because they answer different questions with different futures: this
    is the hook the deferred enterprise hold behaviour (spec section
    10.10) refuses THROUGH, once a held ticket can be visible without
    being purgeable.
    """
    if sees_all_content(principal, settings_row=settings_row):
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

    `retention_days = 0` IS SYNCHRONOUS ONLY WHILE THE DUE BACKLOG STAYS
    UNDER `SWEEP_LIMIT`: this call's own bounded sweep orders every due
    ticket by `purge_on, pk` and takes the oldest `SWEEP_LIMIT`, so on a
    box with `SWEEP_LIMIT` or more OTHER tickets already due, the ticket
    this call just wrote may not be in that batch. The item is hidden at
    once regardless -- the exclusion is unconditional -- and purged by
    whichever sweep reaches it next.

    A CHILD TICKET CARRIES NO LABEL: the parent's title is not the
    child's name, and the child's own content is not copied into
    bookkeeping the purge is meant to leave behind. It is stamped with
    the PARENT ITEM's owner columns, deliberately -- those columns
    answer "whose deletion is this", and whoever may restore or purge
    the parent may do so for the whole cascade. AN ITEM ALREADY
    TICKETED KEEPS ITS OWN DATE AND ITS OWN STANDING: `get_or_create` on
    the unique `(kind, key)` returns the existing ticket unchanged,
    neither re-dated nor adopted. NO SEPARATE ZERO-DAY PATH for the
    children either: they are written with the SAME `purge_on` as the
    parent, so they are due exactly when it is, and the unconditional
    prune-on-write sweep below purges them in the same call.
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
            # THE CHILDREN GET THEIR OWN TICKETS, not a mention on the
            # parent's. Same owner, same actor, SAME DATE -- the date is
            # the promise, and two dates for one click would be two
            # promises -- and `parent=ticket`, which is how the restore
            # and the permanent delete below know which tickets this
            # click created.
            # A CHILD THAT ALREADY HAS A TICKET IS LEFT EXACTLY AS IT
            # IS: `get_or_create` on the unique `(kind, key)` returns
            # it, and it is neither re-dated nor adopted. Somebody
            # deleted that item on its own and was shown a date for it;
            # this delete does not get to move it.
            # NO SEPARATE ZERO-DAY PATH: these rows are due exactly when
            # the parent is, so the unconditional prune-on-write sweep
            # below purges them in the same call.
            for child_kind, child_key in run_children(kind, str(key)):
                _child, child_created = DeletionTicket.objects.get_or_create(
                    kind=child_kind, key=child_key,
                    defaults=dict(
                        label="",
                        parent=ticket,
                        purge_on=purge_on,
                        deleted_by_kind=getattr(actor, "kind", ""),
                        deleted_by_key=getattr(actor, "key", ""),
                        owner_kind=getattr(owner, "owner_kind", ""),
                        owner_key=str(getattr(owner, "owner_key", "")),
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
    soft-delete columns. Every ticket that exists is restorable (a
    completed purge leaves none), so this has exactly one refusal to
    make and it is not made in this delivery: a held ticket, once the
    deferred enterprise slice can set a hold.

    A TICKET ALREADY GONE (a raced sweep, a double-click) MUST NOT LOG A
    RESTORE THAT DID NOT HAPPEN: deleting by QUERYSET rather than by
    instance reports how many rows it actually removed, and an event is
    written only when that count is nonzero.

    `settings_row`, OPTIONAL, THE SAME SHAPE `visible_tickets`/`may_purge`
    ABOVE TAKE: a caller that already holds the request's one
    `IdentitySettings` row passes it through here instead of paying a
    second read. `None` -- every caller before this parameter existed --
    reads it here, exactly as before.

    A CHILD RESTORED ON ITS OWN EARLIER IS SIMPLY ABSENT by the time this
    runs -- not an error, and it writes no event of its own here: each
    child is removed and reported on its own, for the reason the parent
    already is. A parent whose own delete reports nothing (already gone)
    had no children left to put back either -- a ticket cannot outlive
    its parent's row.
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    with transaction.atomic():
        kind, key, label = ticket.kind, ticket.key, ticket.label
        # THE CHILDREN COME BACK WITH THE PARENT, and they go first and
        # one at a time. The foreign key would cascade them away with
        # the parent row, but a cascade reports nothing per row, and
        # this function's own rule is that A RESTORE THAT DID NOT
        # HAPPEN IS NEVER LOGGED: a child a competing sweep purged in
        # the meantime must not get a `content.restored` event for an
        # item that was in fact destroyed. Deleting by queryset per
        # child answers that question the same way the parent's own
        # delete below answers it.
        # NO RESOLVER HERE -- the link is what this restore follows, so
        # an item somebody deleted on its own, or one that went with a
        # different parent, is not this restore's business and is left
        # deleted with the date it was shown.
        restored_children = []
        for child in ticket.children.all().order_by("pk"):
            removed_child, _ = DeletionTicket.objects.filter(
                pk=child.pk).delete()
            if removed_child:
                restored_children.append(child)
        removed, _ = DeletionTicket.objects.filter(pk=ticket.pk).delete()
        if not removed:
            return
        audit.record(actor, CONTENT_RESTORED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind)
        for child in restored_children:
            audit.record(actor, CONTENT_RESTORED, target_type=child.kind,
                         target_key=child.key, target_label="",
                         source=source, kind=child.kind)


def _purge_child(actor, ticket, *, source: str, row) -> dict[str, int]:
    """Run ONE child ticket's handlers, delete it, record its own
    content-free event. NO CASCADE OF ITS OWN -- a child is never asked
    for children, so a link that somehow pointed back at its own parent
    could not recurse."""
    removed = run_retention(ticket.kind, ticket.key)
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
    the purge rather than re-raising on the half it already did.

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
    """
    row = settings_row if settings_row is not None else IdentitySettings.get_solo()
    with transaction.atomic():
        current = DeletionTicket.objects.select_for_update().filter(pk=ticket.pk).first()
        if current is None:
            return {}
        # THIS ITEM'S ROWS FIRST, THEN THE CHILDREN, AND THE ORDER IS
        # LOAD-BEARING. It is the filesystem-last rule
        # `identity/contracts/cascades.py` states: a filesystem delete
        # has no rollback, so a row handler that raised AFTER files were
        # removed would leave a resurrected row pointing at bytes that
        # are gone. A child here is a generated image, whose handler is
        # in the FILES band, so every row this click touches is gone
        # before the first byte is.
        # READ UNDER THE SAME LOCK, and read before anything is
        # destroyed: the parent row's delete would cascade these away
        # without ever running their handlers. `order_by("pk")` because
        # the table's own ordering is newest-first, and the order this
        # destroys things in is worth being the order they were written
        # in rather than whichever way a timestamp fell.
        children = list(current.children.select_for_update().order_by("pk"))
        removed = run_retention(current.kind, current.key)
        for child in children:
            child_removed = _purge_child(actor, child, source=source, row=row)
            for child_label, count in child_removed.items():
                removed[child_label] = removed.get(child_label, 0) + count
        kind, key, label = current.kind, current.key, current.label
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
    block the rest of the batch. The failure is logged with its kind and
    key -- structural, never content, the shape `tools/rag/jobs.py` uses
    throughout -- and the ticket stays due for the next pass.

    A `RetentionRefused` IS NOT AN ERROR AND IS CAUGHT FIRST: it is a
    handler saying "not now" for an operator-readable reason -- a
    handler may refuse, for example when a worker still holds one of
    the conversation's jobs -- so it is logged at `logger.warning` --
    one line, no traceback -- and every other exception keeps
    `logger.exception`, which is the failure this batch actually needs
    to be noisy about.
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
        # those is an item somebody was shown a date for.
        addressed = 1 + DeletionTicket.objects.filter(parent_id=ticket.pk).count()
        try:
            purge_ticket(SERVICE_PRINCIPAL, ticket, source=source)
        except RetentionRefused as exc:
            logger.warning(
                "identity.retention: purge refused for %s:%s; it stays due -- %s",
                ticket.kind, ticket.key, exc)
            continue
        except Exception:  # noqa: BLE001 -- one bad ticket, not a bad batch
            logger.exception(
                "identity.retention: purge failed for %s:%s; it stays due",
                ticket.kind, ticket.key)
            continue
        purged += addressed
    return purged
