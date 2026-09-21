"""Running the entitlement cascades against a live Django.

`identity/contracts/cascades.py` stays PURE -- pinned by
`identity/tests/test_purity.py`, which imports the whole `contracts/`
package with no `DJANGO_SETTINGS_MODULE` set at all -- so it names
handlers as dotted-path STRINGS. Resolving one needs
`django.utils.module_loading.import_string`, which is Django, which
rule 4 allows; this module is where that resolution lives, exactly as
`identity/ownership.py` is where `apps.get_model` lives.

NEVER SWALLOWS. A cascade whose handler cannot be imported, or which
raises, takes the whole delete down with it -- inside
`identity.services.delete_entitlement`'s `transaction.atomic()`, so
nothing is half-deleted. A delete that reported success while leaving
orphan labels and a stale chunk cache behind is the one failure mode
this registry exists to prevent.

The second half of this module runs the OTHER registry in
`identity/contracts/cascades.py` -- retention handlers, not
entitlement cascades. Same resolution, same never-swallows discipline,
one function instead of a `commit`-flagged pair: see `run_retention`.
"""
from __future__ import annotations

from django.db import transaction
from django.utils.module_loading import import_string

from identity.contracts.cascades import all_entitlement_cascades, retention_handlers


def _run(entitlement_id: int, *, commit: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    for spec in all_entitlement_cascades():
        handler = import_string(spec.handler)
        counts[spec.label] = handler(entitlement_id, commit=commit)
    return counts


def cascade_counts(entitlement_id: int) -> dict[str, int]:
    """`{label: count}` -- what each column WOULD remove. Writes nothing.
    This is what the delete confirmation names."""
    return _run(entitlement_id, commit=False)


def run_cascades(entitlement_id: int) -> dict[str, int]:
    """`{label: count}` -- what each column DID remove, having also
    repaired its own caches. Call inside the delete's transaction."""
    return _run(entitlement_id, commit=True)


def run_retention(kind: str, key: str) -> dict[str, int]:
    """`{label: count}` -- what each column removed for this deleted
    item. Call inside `identity.retention.purge_ticket`'s transaction.

    ONE RUNNER, ONE MODE, AND NO PRIVATE TWIN. `_run` above is split
    from its two public wrappers because it serves both of them with a
    `commit` flag; this one has a single caller and a single mode, so
    the body lives here rather than in a `_run_retention` that would
    exist only to be called once.

    The departure from `EntitlementCascade`'s two-mode shape is
    deliberate: that registry needs `commit=False` because an
    entitlement delete is irreversible the instant it is confirmed, so
    the count IS the confirmation. A deletion's confirmation is the
    Deleted page, where the item sits named and restorable, so there is
    nothing here for a dry-run pass to tell anybody.

    NEVER SWALLOWS, exactly as `_run` does not: a handler that cannot be
    imported, or that raises, takes the whole purge down with it, so
    nothing is half-purged at the row level and the ticket survives to
    be retried.
    """
    counts: dict[str, int] = {}
    for spec in retention_handlers(kind):
        handler = import_string(spec.handler)
        # A NESTED `transaction.atomic()` -- a SAVEPOINT -- around each
        # handler, the `agents.attachments.delete_attachments_for`
        # discipline: NEITHER SWALLOWS, and the savepoint is what makes
        # that safe.
        #
        # `delete_attachments_for` used to catch a broken cleanup
        # provider's failure so it would not block a delete the actor had
        # already confirmed -- but a delete now only writes a ticket, so
        # that reasoning stopped applying once this cascade moved to
        # running at PURGE time instead: it now LOGS and RE-RAISES,
        # exactly like every retention handler here, because a purge that
        # reported success while leaving content behind is exactly the
        # failure this whole feature exists to prevent.
        #
        # The savepoint is still required. Without it, a DATABASE-level
        # error inside a handler poisons the Postgres connection for the
        # rest of `purge_ticket`'s outer transaction, and the audit write
        # and the ticket delete that follow would fail for a reason
        # unrelated to the real one. Rolling back to the savepoint
        # restores the connection, so the real error reaches the view,
        # which renders it as a refusal sentence and leaves the ticket in
        # place for the next sweep.
        with transaction.atomic():
            counts[spec.label] = handler(key)
    return counts
