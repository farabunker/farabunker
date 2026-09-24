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


def run_children(kind: str, key: str) -> list[tuple[str, str]]:
    """The `(kind, key)` pairs that follow this item's own ticket.

    ASKED AT DELETE TIME ONLY -- `identity.retention.delete_content` is
    the one caller. What it answers is written as tickets linked to the
    one this delete created, and restore and purge read that link
    instead of asking again: the parent's rows are what this answer is
    computed from, and by purge time this item's handler is about to
    destroy them.

    NEVER SWALLOWS, exactly as `run_retention` does not: a resolver
    that cannot be imported, or that raises, takes the delete down with
    it rather than silently leaving a child undeleted.

    Deduped, in handler order -- ROWS band before FILES band, stable
    within a band, whatever `retention_handlers` returns. A kind with no
    resolver -- every kind but one, today -- answers `[]`, which is not
    an error.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for spec in retention_handlers(kind):
        if spec.children is None:
            continue
        for child_kind, child_key in import_string(spec.children)(key):
            pair = (str(child_kind), str(child_key))
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
    return pairs
