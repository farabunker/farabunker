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
from identity.cascades import run_retention
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, SOURCE_WEB,
)
from identity.contracts.principals import SERVICE_PRINCIPAL
from identity.contracts.retention import RETENTION_KINDS
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


def may_purge(principal, ticket) -> bool:
    """Whether `principal` may destroy this item's content now.

    The item's OWNER, or a `sees_all_content` principal -- the same
    predicate that already lets them read the content, and the same
    shape `tools.vision.visibility.may_read_job` uses. There is no
    posture branch: in this delivery the enterprise posture behaves
    exactly as personal does, and the refusal that will differ is the
    deferred enterprise slice's (spec section 10.10), not this one's.
    """
    if sees_all_content(principal):
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

    THE SWEEP AT THE END IS WHAT MAKES `retention_days = 0` SYNCHRONOUS:
    when the setting in force is 0, the ticket this call just wrote is
    due TODAY, so it calls `sweep()` before returning and the content is
    gone before the response returns. ONLY THEN: a delete with any grace
    period must not incidentally purge some unrelated ticket that has
    since fallen due -- that is the sweep's own job, on its own cliff or
    its own click, not a side effect of a caller who asked to delete one
    different item. The prune-on-write pattern `tools.rag.services.
    record_ask` and `models.queue.backend.enqueue` already use still
    applies; it is scoped to the one case where "prune" and "the write
    just made" are the same ticket.
    """
    if kind not in RETENTION_KINDS:
        raise ValueError(f"{kind!r} is not a retention kind.")
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
    if created and purge_on <= timezone.localdate():
        sweep()
    return ticket


def restore_content(actor, ticket, *, source: str = SOURCE_WEB) -> None:
    """Put the item back: delete the ticket, record the event.

    NOTHING ELSE. The item was never modified, so there is nothing to
    put back -- which is the whole return on not adding per-model
    soft-delete columns. Every ticket that exists is restorable (a
    completed purge leaves none), so this has exactly one refusal to
    make and it is not made in this delivery: a held ticket, once the
    deferred enterprise slice can set a hold.
    """
    row = IdentitySettings.get_solo()
    with transaction.atomic():
        kind, key, label = ticket.kind, ticket.key, ticket.label
        ticket.delete()
        audit.record(actor, CONTENT_RESTORED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind)


def purge_ticket(actor, ticket, *, source: str = SOURCE_WEB) -> dict[str, int]:
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
    """
    row = IdentitySettings.get_solo()
    with transaction.atomic():
        removed = run_retention(ticket.kind, ticket.key)
        kind, key, label = ticket.kind, ticket.key, ticket.label
        ticket.delete()
        audit.record(actor, CONTENT_PURGED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind, removed=removed)
    return removed


def sweep(*, limit: int = SWEEP_LIMIT, source: str = SOURCE_WEB) -> int:
    """Purge up to `limit` due tickets. Returns how many were purged.

    ONE DUE-CONDITION: the promised date has arrived and nothing holds
    the ticket. There is no second clause, because there is no second
    cliff and no ticket that outlives its content. The hold half is
    always true today -- nothing in this delivery writes a hold -- and it
    is in the query so the deferred enterprise slice is a control and a
    refusal, not a change to this function.

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
    """
    due = list(
        DeletionTicket.objects
        .filter(purge_on__lte=timezone.localdate(), hold_by_kind="")
        .order_by("purge_on", "pk")[:limit]
    )
    purged = 0
    for ticket in due:
        try:
            purge_ticket(SERVICE_PRINCIPAL, ticket, source=source)
        except Exception:  # noqa: BLE001 -- one bad ticket, not a bad batch
            logger.exception(
                "identity.retention: purge failed for %s:%s; it stays due",
                ticket.kind, ticket.key)
            continue
        purged += 1
    return purged
