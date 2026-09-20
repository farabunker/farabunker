"""Running the entitlement axes against a live Django.

`identity/contracts/axes.py` stays PURE -- pinned by
`identity/tests/test_purity.py`, which imports the whole `contracts/`
package with no `DJANGO_SETTINGS_MODULE` set at all -- so it names its
handlers as dotted-path STRINGS. Resolving one needs
`django.utils.module_loading.import_string`, which is Django, which
rule 4 allows; this module is where that resolution lives, exactly as
`identity/cascades.py` is for the delete cascades and
`identity/ownership.py` is for `apps.get_model`.

NEVER SWALLOWS, the same rule `identity/cascades.py` states: an axis
whose handler cannot be imported, or which raises, takes the request
down with it. An entitlements page that quietly rendered a zero for a
column whose handler had been renamed would be a page reassuring an
operator about reach it never measured.
"""
from __future__ import annotations

from django.utils.module_loading import import_string

from identity.contracts.axes import (
    AxisRow, all_entitlement_axes, entitlement_axis,
)


class AxisRefused(ValueError):
    """An axis write this platform will not perform, with an
    operator-readable reason -- the same shape
    `identity.services.ServiceRefused` has, so a view can render the
    message without catching a genuine programming error by accident."""


def axis_counts(spec) -> dict[int, int]:
    """`{entitlement_id: rows on this axis}` for EVERY entitlement, in
    whatever one query `spec.counts` costs."""
    return import_string(spec.counts)()


def axis_rows(spec) -> list[AxisRow]:
    """This axis's whole catalogue, in display order."""
    return list(import_string(spec.rows)())


def axis_active_ids(spec, entitlement_id: int) -> set[str]:
    """Which of `axis_rows(spec)` carry this entitlement. ONE query."""
    return {str(value) for value in import_string(spec.ids_for)(int(entitlement_id))}


def counts_by_axis() -> dict[str, dict[int, int]]:
    """`{axis key: {entitlement_id: count}}` -- ONE call per registered
    axis, whatever the number of entitlements.

    This is the entitlements LIST page's whole reach read: a page that
    asked each entitlement for its own counts would run six queries per
    row, which is the sidebar N+1 this codebase has already paid for
    once.
    """
    return {spec.key: axis_counts(spec) for spec in all_entitlement_axes()}


def axis_labels() -> list[str]:
    """Every registered axis's plural noun, in display order -- the
    entitlements list's reach column headings."""
    return [spec.label for spec in all_entitlement_axes()]


def reach_by_entitlement(entitlement_ids) -> dict[int, list[int]]:
    """`{entitlement_id: [count per axis, in display order]}`.

    FLAT: one query per registered AXIS, whatever the length of
    `entitlement_ids`. The entitlements list is the manage-all-fifty
    screen, and a page that asked each row for its own reach would run
    five queries per row -- the sidebar N+1 this codebase has already
    paid for once, on a page whose whole purpose is to hold fifty rows.

    Every id gets a full-length row, zeros included, so the template
    iterates counts rather than testing for a missing key per cell.
    """
    counts = counts_by_axis()
    axes = all_entitlement_axes()
    return {
        int(entitlement_id): [counts[spec.key].get(int(entitlement_id), 0)
                              for spec in axes]
        for entitlement_id in entitlement_ids
    }


def axis_doors(entitlement_id: int) -> list[dict]:
    """One door per registered axis that names its own column's page:
    the axis's heading, its own sentence, and a URL into that column
    narrowed to this entitlement.

    THE REASON THIS FUNCTION EXISTS RATHER THAN A HAND-WRITTEN SECTION
    (fix round 2, P2-I1). Document labels are counted from this
    direction and edited in the library, so the entitlement page owes
    the operator a way THERE -- and the first version of that was a
    `<section>` in `identity/templates/identity/entitlement.html` plus a
    `reverse("rag-documents")` in `identity/views.py`, which was the one
    place in this whole column naming another column's route. It also
    made the claim twenty lines above it false: a sixth labelled kind
    would have been an edit to a view AND a template, not a
    registration. It is a registration again.

    NEVER SWALLOWS, like every other resolution here: a `link` whose
    handler cannot be imported takes the page down rather than quietly
    rendering an entitlement page with a door missing.

    COMPUTED FOR EVERY CALLER, unlike the panels: an entitlement OWNER
    may label documents (spec section 7.4's one labelled kind that is
    theirs), so the door into the library is as much theirs as an
    administrator's.
    """
    doors = []
    for spec in all_entitlement_axes():
        if not spec.has_door:
            continue
        # RESOLVED EVERY TIME, AND DO NOT CACHE THIS (fix round 2,
        # concern 2, adjudicated). One `import_string` per door per
        # render is a `sys.modules` lookup after the first, which buys
        # nothing worth having -- and what a cache WOULD cost is
        # correctness under test. This registry is a module-level dict
        # that suites POP, RE-REGISTER and MONKEYPATCH:
        # `identity/tests/test_axes.py`'s isolation fixture, the
        # pop-first registration pins in all three columns, and the
        # entitlement page's own "pop the axis and the door disappears"
        # pin. A resolution memoised on first use would answer whichever
        # handler happened to be registered when the first test in the
        # process ran, and the suite's result would depend on collection
        # order -- which is the one failure mode a gate cannot see.
        doors.append({
            "key": spec.key,
            "label": spec.label,
            "hint": spec.hint,
            "url": import_string(spec.link)(int(entitlement_id)),
        })
    return doors


def axis_panels(entitlement_id: int) -> list[dict]:
    """One transfer panel per EDITABLE axis, for one entitlement's own
    page: the catalogue split into the rows that do not carry it
    (`available`) and the rows that do (`active`).

    THE SPLIT IS DONE HERE, not in the template, so the panel fragment
    renders two plain lists and the "which pane does this row belong in"
    rule is written once. Counted here too -- the pane headings print
    `len()` of what they are about to render, so a heading cannot
    disagree with its own list.

    Non-editable axes (document labels today) are absent: their counts
    are already on the page, in the reach panel, which stays the single
    home for counts (spec section 22.34).
    """
    panels = []
    for spec in all_entitlement_axes():
        if not spec.editable:
            continue
        rows = axis_rows(spec)
        active_ids = axis_active_ids(spec, entitlement_id)
        active = [row for row in rows if row.id in active_ids]
        available = [row for row in rows if row.id not in active_ids]
        panels.append({
            "key": spec.key,
            # THE PANEL'S OWN ELEMENT ID, separate from `key` since
            # phase 2: the fragment now serves two consumers whose
            # anchor vocabularies differ (an axis here, a resource row
            # on the access pages), so the id is supplied rather than
            # assembled inside the fragment from a key whose shape it
            # cannot know. `fields` is the same story for the hidden
            # inputs that name this POST's target.
            "anchor": f"axis-{spec.key}",
            "fields": {"action": "axis", "axis": spec.key},
            "label": spec.label,
            "hint": spec.hint,
            "available": available,
            "active": active,
            "available_count": len(available),
            "active_count": len(active),
            # WHAT MAKES THE LEFT PANE'S EMPTY STATE HONEST. An empty
            # available pane means "no rows of this kind exist on this
            # box" on an axis with nothing registered, and "every row is
            # already active" on one where the operator has ticked them
            # all -- two different sentences, and the panel cannot tell
            # them apart from `available` alone.
            "total": len(rows),
        })
    return panels


def apply_axis(spec, entitlement_id: int, *, add, remove, actor, actor_user=None
               ) -> dict[str, int]:
    """Add and/or remove this entitlement on `spec`'s rows, through the
    column's OWN single writer, and answer `{"added": n, "removed": n}`.

    VALIDATED AGAINST THE CATALOGUE FIRST. `spec.rows()` is the only
    definition of what this axis can hold, so an id that is not in it is
    refused before any write -- one `AxisRefused`, naming the offending
    id, rather than a partial batch and a 500 from the column's own
    lookup. That also makes the catalogue the ONE place a resource has
    to appear for it to be editable here: a feature-gated tool absent
    from this install is not silently labellable by a hand-typed form.
    """
    catalogue = {row.id for row in axis_rows(spec)}
    wanted_add = {str(value) for value in add}
    wanted_remove = {str(value) for value in remove}
    stray = sorted((wanted_add | wanted_remove) - catalogue)
    if stray:
        raise AxisRefused(
            f"{stray[0]!r} is not a {spec.label.lower()} row on this install.")
    return import_string(spec.set_for)(
        int(entitlement_id), add=wanted_add, remove=wanted_remove,
        actor=actor, actor_user=actor_user)


def axis_for(key: str):
    """The registered axis called `key`, or `None`. Re-exported from the
    contract so a caller needs one import, not two."""
    return entitlement_axis(key)
