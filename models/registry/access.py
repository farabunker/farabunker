"""Which registered connections a principal may USE.

A `models/` module, so it may import `identity.access` (import-law rule
2's named seam) and its own set tables -- neither of which
`models/registry/bindings.py`'s callers should have to do for themselves.

RE-EXPORTED FROM `models.registry.bindings`, because `bindings` is the
ONLY submodule of this package `agents/` may import at all
(`foundation/ops/tests/test_import_law.py::
test_agents_reaches_models_registry_through_bindings_and_nothing_else`).
Splitting the implementation out keeps that file about resolution while
still giving every column one door.

IT RUNS ONCE PER REQUEST OR PER TURN, not once per option: one join over
the two set edges and one query over the principal's grants -- the same
shape and the same cost as `agents.entitlements.tool_access_for`.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from identity.access import held_entitlement_ids, sees_all_content

# THE BYTE-IDENTICAL SENTENCE, homed ONCE (whole-branch review item 3):
# `tools/vision/views.py`, `tools/rag/views.py`, and `agents/runtime/
# preflight.py` each used to carry their own private copy of this exact
# string under two different names (`_MODEL_FORBIDDEN_MESSAGE` in the
# first two, `_MODEL_NOT_PERMITTED_MESSAGE` in the third) -- one sentence
# for one fact (a picked connection this principal may not USE, per
# `ModelAccess.allows`), so it lives beside the `ModelAccess` type it
# describes and every caller imports it rather than restating it.
MODEL_FORBIDDEN_MESSAGE = (
    "That model needs an entitlement this account does not hold — pick another."
)


@dataclass(frozen=True)
class ModelAccess:
    """Which registered connections this principal may use, as plain data.

    `required` maps a connection pk -> the entitlement ids that reach it
    THROUGH ANY SET IT BELONGS TO. A pk ABSENT from this mapping is in no
    set, and therefore usable -- which is what "use all image generation"
    means and why a box with no sets is unaffected by any of this.

    FLATTENING THE TWO EDGES INTO ONE MAPPING here is deliberate: the
    sets exist so an ADMINISTRATOR can change many grants at once, and no
    read path benefits from re-walking them.

    IT DEFAULTS TO UNRESTRICTED, exactly as `agents.contracts.tools.
    ToolAccess` does, so `UNRESTRICTED_MODEL_ACCESS` is the zero-argument
    answer and every test that does not care keeps working unchanged.
    """

    required: Mapping[int, frozenset[int]] = field(default_factory=dict)
    held: frozenset[int] = frozenset()
    unrestricted: bool = True

    def allows(self, connection_pk: int) -> bool:
        if self.unrestricted:
            return True
        needed = self.required.get(connection_pk)
        return not needed or bool(needed & self.held)


UNRESTRICTED_MODEL_ACCESS = ModelAccess()


def connection_entitlement_ids() -> dict[int, frozenset[int]]:
    """`{connection pk: {entitlement ids that reach it}}`, for every
    connection in at least one ATTACHED set. ONE QUERY, joining the two
    edges on the set.

    A connection whose every set is unattached is ABSENT from the result
    -- the join finds no attachment for it -- which is exactly the
    "a set with nothing attached restricts nothing" rule, obtained by
    construction rather than by a second branch.
    """
    from models.registry.models import ModelSetMember

    out: dict[int, set[int]] = defaultdict(set)
    rows = ModelSetMember.objects.values_list(
        "connection_id", "model_set__entitlement_attachments__entitlement_id")
    for connection_id, entitlement_id in rows:
        if entitlement_id is not None:
            out[connection_id].add(entitlement_id)
    return {pk: frozenset(ids) for pk, ids in out.items()}


def model_access_for(principal, *, settings_row=None,
                     wall: frozenset[int] = frozenset()) -> ModelAccess:
    """What `principal` may use, as plain data.

    THE OPEN BRANCH IS FIRST: `sees_all_content` tests `accounts_on()`
    before it reads a second table, so an open box builds this without
    touching a set edge or a grant -- the structural half of "an open box
    never runs a permission query".

    `sees_all_content`, NOT `is_admin`: using somebody's model is using,
    not administering, so an administrator with the content setting off
    is filtered exactly like a member. Editing a set IS administering,
    and those pages are class S.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- the same shape `identity.access.sees_all_content`/
    `.held_entitlement_ids` and `tools.rag.access.document_visibility`
    already take, threaded through here to the same two calls rather than
    a second `IdentitySettings.get_solo()` of this function's own. A
    caller building more than one access axis for the same turn or
    request (`agents.runtime.preflight.preflight_turn`,
    `agents.runtime.loop._run_turn`) fetches the row ONCE and passes it
    to every axis, which is what keeps a turn to one singleton read
    rather than one per axis.

    `wall` is the workstream scope of the turn this access is being built
    for -- a PLAIN FROZENSET, empty by default, lawful under every
    existing import sweep and adding no import in either direction. It
    narrows the held half identically to `agents.entitlements.
    tool_access_for`'s, for the identical reason: a wall is a scope
    somebody chose, and a scope whose meaning depended on who was looking
    would not be one. This is the ONE change the workstreams phase makes
    in this column, and `models/README.md` records why a reader of this
    column finds a workstream concept in it at all (spec §6.3, §20).
    """
    if sees_all_content(principal, settings_row=settings_row) and not wall:
        return UNRESTRICTED_MODEL_ACCESS
    held = held_entitlement_ids(principal, settings_row=settings_row)
    return ModelAccess(
        required=connection_entitlement_ids(),
        held=(held & wall) if wall else held,
        unrestricted=False,
    )
