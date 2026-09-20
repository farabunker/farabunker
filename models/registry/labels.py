"""Model sets: creating them, editing their two edges, and giving up
their attachments when an entitlement dies.

EVERY WRITE HERE IS ADMINISTRATOR-ONLY (both pages are class S), unlike
document labels: an entitlement owner's three capabilities are grants and
documents, and a model connection is box inventory rather than somebody's
library shelf. The CALLER checks the predicate; this module is the write.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction

from identity import audit
from identity.contracts import actions
from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember


class SetRefused(ValueError):
    """A set write this platform will not perform, with an
    operator-readable reason -- the same shape
    `identity.services.ServiceRefused` has, so a view can render the
    message without catching a genuine programming error by accident."""


def connection_set_ids(connection) -> frozenset[int]:
    """The set ids `connection` belongs to -- from the TABLE."""
    return frozenset(
        connection.set_memberships.values_list("model_set_id", flat=True))


def set_memberships_for(actor, connection, set_ids, *, added_by=None) -> None:
    """Make `connection`'s memberships exactly `set_ids`.

    WRITES THE DIFFERENCE, so the audit trail records what changed rather
    than what was resubmitted. This is the edge an operator edits when a
    NEW MODEL ARRIVES, which is why it lives beside the connection on the
    console rather than on the sets page.

    `added_by` is an `identity` `User` INSTANCE, or `None` -- this
    module never imports `identity.models` to get one (import law rule
    4: `models/registry/` is importable-from, not an importer of
    `identity`); the caller passes it and this function only assigns it
    to the FK, the same shape `agents/labels.py::set_tool_labels` uses
    for `labelled_by`. Provenance also lives in the `MODELSET_MEMBER_
    ADDED` audit row written below; this column fills only when the
    caller holds a real `User` (a view reached through `identity.
    request.user_for_request` will; the shell/`SERVICE_PRINCIPAL` path
    does not).
    """
    wanted = {int(i) for i in set_ids}
    with transaction.atomic():
        current = set(connection_set_ids(connection))
        for set_id in sorted(wanted - current):
            ModelSetMember.objects.create(model_set_id=set_id, connection=connection,
                                          added_by=added_by)
            audit.record(actor, actions.MODELSET_MEMBER_ADDED, target_type="model_set",
                         target_key=set_id, target_label=connection.name,
                         connection_id=connection.pk)
        for set_id in sorted(current - wanted):
            ModelSetMember.objects.filter(model_set_id=set_id,
                                          connection=connection).delete()
            audit.record(actor, actions.MODELSET_MEMBER_REMOVED, target_type="model_set",
                         target_key=set_id, target_label=connection.name,
                         connection_id=connection.pk)


def create_set(actor, *, name: str, description: str = "") -> ModelSet:
    clean = (name or "").strip()
    if not clean:
        raise SetRefused("A model set needs a name.")
    try:
        with transaction.atomic():
            model_set = ModelSet.objects.create(name=clean,
                                                description=description.strip())
            audit.record(actor, actions.MODELSET_CREATED, target_type="model_set",
                         target_key=model_set.pk, target_label=clean)
    except IntegrityError as exc:
        raise SetRefused(
            f"A model set called {clean!r} already exists. Names are compared without "
            f"regard to case."
        ) from exc
    return model_set


def rename_set(actor, model_set, name: str) -> None:
    clean = (name or "").strip()
    if not clean:
        raise SetRefused("A model set needs a name.")
    if clean == model_set.name:
        return
    was = model_set.name
    try:
        with transaction.atomic():
            model_set.name = clean
            model_set.save(update_fields=["name"])
            audit.record(actor, actions.MODELSET_RENAMED, target_type="model_set",
                         target_key=model_set.pk, target_label=clean,
                         **{"from": was, "to": clean})
    except IntegrityError as exc:
        raise SetRefused(f"A model set called {clean!r} already exists.") from exc


def set_delete_counts(model_set) -> dict[str, int]:
    """What deleting `model_set` takes with it. THE CONFIRMATION NAMES
    THESE FIRST, for the reason an entitlement delete does: removing the
    last set a model belongs to WIDENS access to it."""
    return {"Models": model_set.members.count(),
            "Entitlements": model_set.entitlement_attachments.count()}


def delete_set(actor, model_set) -> dict[str, int]:
    """Both edges go with it, by `CASCADE`, and one audit row names both
    counts."""
    counts = set_delete_counts(model_set)
    label, pk = model_set.name, model_set.pk
    with transaction.atomic():
        model_set.delete()
        audit.record(actor, actions.MODELSET_DELETED, target_type="model_set",
                     target_key=pk, target_label=label, removed=counts)
    return counts


def attach(actor, model_set, entitlement_id: int, *, attached_by=None) -> None:
    """Attach one entitlement. IDEMPOTENT: attaching twice is not a
    second event.

    `attached_by` -- an `identity` `User` instance, or `None` -- is the
    same provenance shape `set_memberships_for`'s own `added_by` above
    documents; see that docstring."""
    if model_set.entitlement_attachments.filter(entitlement_id=entitlement_id).exists():
        return
    with transaction.atomic():
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement_id=entitlement_id,
                                           attached_by=attached_by)
        audit.record(actor, actions.MODELSET_ATTACHED, target_type="model_set",
                     target_key=model_set.pk, target_label=model_set.name,
                     entitlement_id=entitlement_id)


def detach(actor, model_set, entitlement_id: int) -> None:
    """The same in reverse, equally idempotent."""
    rows = model_set.entitlement_attachments.filter(entitlement_id=entitlement_id)
    if not rows.exists():
        return
    with transaction.atomic():
        rows.delete()
        audit.record(actor, actions.MODELSET_DETACHED, target_type="model_set",
                     target_key=model_set.pk, target_label=model_set.name,
                     entitlement_id=entitlement_id)


def model_set_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's answer to "this entitlement is going away".

    Registered from `models/registry/apps.py::ready()` as a DOTTED-PATH
    STRING, so `identity/` runs it without importing `models/`
    (import-law rule 4). It detaches the ENTITLEMENT from every set; the
    sets and their memberships survive, because a set is a grouping of
    models and has meaning without any entitlement attached.
    """
    rows = ModelSetEntitlement.objects.filter(entitlement_id=entitlement_id)
    count = rows.count()
    if commit:
        rows.delete()
    return count
