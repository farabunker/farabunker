"""This column's entitlement AXIS -- model sets -- read and written
from the entitlement's own side.

`models/registry/labels.py` answers the resource question ("which
entitlements are attached to this set"), which is what the sets page
asks. This module answers the same table's OTHER question ("which sets
carry this entitlement"), which is what the entitlement pages ask -- and
it answers it without `identity/` importing anything here, because
`models/registry/apps.py::ready()` registers a dotted-path descriptor
against `identity.contracts.axes` and `identity/axes.py` resolves it.

EVERY WRITE GOES THROUGH `models/registry/labels.py`'s EXISTING
`attach`/`detach`, which are idempotent and write their own
`model_set.attached`/`model_set.detached` audit rows. Nothing here
touches `ModelSetEntitlement` on the write path.
"""
from __future__ import annotations

from django.db.models import Count

from identity.contracts.axes import AxisRow


def model_set_rows() -> list[AxisRow]:
    """Every model set, name-ordered. The whole inventory: attaching an
    entitlement to a set is administrator-only box policy (both sets
    pages are class S), so there is no narrower catalogue to offer."""
    from models.registry.models import ModelSet

    return [AxisRow(str(pk), name)
            for pk, name in ModelSet.objects.order_by("name").values_list("id", "name")]


def model_set_ids_for(entitlement_id: int) -> set[str]:
    """The set ids this entitlement is attached to. ONE query."""
    from models.registry.models import ModelSetEntitlement

    return {str(pk) for pk in ModelSetEntitlement.objects
            .filter(entitlement_id=entitlement_id)
            .values_list("model_set_id", flat=True)}


def model_set_counts() -> dict[int, int]:
    """`{entitlement_id: attached sets}` for EVERY entitlement, in ONE
    aggregate -- see `agents/axes.py::_counts` for why the whole-table
    shape rather than a per-entitlement count."""
    from models.registry.models import ModelSetEntitlement

    return {
        row["entitlement_id"]: row["n"]
        for row in ModelSetEntitlement.objects.values("entitlement_id")
                                              .annotate(n=Count("id"))
    }


def set_model_set_axis(entitlement_id: int, *, add, remove, actor, actor_user=None
                       ) -> dict[str, int]:
    """Attach/detach this entitlement on the named sets, through the
    column's own idempotent writers.

    THE CURRENT ATTACHMENTS ARE READ ONCE, not once per set: `attach`
    and `detach` are each already a no-op for a row in the state asked
    for, but the summary this returns is a count of what CHANGED, which
    an operator reads as a sentence, so the decision has to be made here
    rather than inferred from two functions that answer nothing.
    """
    from models.registry.labels import attach, detach
    from models.registry.models import ModelSet

    held = model_set_ids_for(entitlement_id)
    wanted_add = {str(value) for value in add} - held
    wanted_remove = {str(value) for value in remove} & held
    touched = wanted_add | wanted_remove
    if not touched:
        return {"added": 0, "removed": 0}
    by_id = {str(row.pk): row
             for row in ModelSet.objects.filter(pk__in=[int(i) for i in touched])}
    added = removed = 0
    for key in sorted(wanted_add):
        row = by_id.get(key)
        # DELETED BETWEEN THE RENDER AND THE SUBMIT is a no-op, not an
        # error: `identity.axes.apply_axis` has already refused every id
        # outside the catalogue, so a miss here is a row that went away.
        if row is None:
            continue
        attach(actor, row, entitlement_id, attached_by=actor_user)
        added += 1
    for key in sorted(wanted_remove):
        row = by_id.get(key)
        if row is None:
            continue
        detach(actor, row, entitlement_id)
        removed += 1
    return {"added": added, "removed": removed}
