"""The registry that lets an ENTITLEMENT reach back into every column
that labels something with it.

`identity/` may not import `agents/`, `models/` or `tools/` (import-law
rule 4), and an operator administering fifty entitlements has to be able
to ask each one "which tools, agents, flows and model sets carry you"
-- and change the answer. The columns register a DOTTED-PATH STRING at
`AppConfig.ready()`, exactly as the delete cascades already do, and
`identity/axes.py` resolves it at request time.

Same isolation shape as `identity/tests/test_cascades.py`: the registry
is a module-level dict with no reset path, so a test that registers a
broken handler would otherwise break every later page render in the
same pytest process.
"""
from __future__ import annotations

import pytest

from identity.axes import (
    AxisRefused, apply_axis, axis_active_ids, axis_counts, axis_doors, axis_for,
    axis_panels, axis_rows, counts_by_axis,
)
from identity.contracts import axes as axes_module
from identity.contracts.axes import (
    AxisRow, EntitlementAxis, all_entitlement_axes, entitlement_axis,
    register_entitlement_axis,
)

_HERE = "identity.tests.test_axes"
_CALLS: list[tuple] = []


@pytest.fixture(autouse=True)
def _isolated_registry():
    saved = dict(axes_module._AXES)
    axes_module._AXES.clear()
    _CALLS.clear()
    yield
    axes_module._AXES.clear()
    axes_module._AXES.update(saved)


# --- a fake column, registered by the tests below --------------------------

_WORLD = {"1": {"alpha", "beta"}}


def fake_counts() -> dict[int, int]:
    return {int(key): len(value) for key, value in _WORLD.items()}


def fake_rows() -> list[AxisRow]:
    return [AxisRow("alpha", "Alpha"), AxisRow("beta", "Beta"),
            AxisRow("gamma", "Gamma", note="page only")]


def fake_ids_for(entitlement_id: int) -> set[str]:
    return set(_WORLD.get(str(entitlement_id), set()))


def fake_set_for(entitlement_id, *, add, remove, actor, actor_user) -> dict[str, int]:
    _CALLS.append((entitlement_id, frozenset(add), frozenset(remove), actor, actor_user))
    return {"added": len(add), "removed": len(remove)}


def fake_link(entitlement_id: int) -> str:
    return f"/fakes/?entitlement={entitlement_id}"


def fake_int_ids_for(entitlement_id: int) -> set[int]:
    """A column that answers INTEGER primary keys, the shape agents,
    flows and model sets all have -- the resolver normalises them to the
    strings the panels and the form body use."""
    return {1, 2}


def _editable(key="test.axis", label="Fakes", order=100, hint="") -> EntitlementAxis:
    return EntitlementAxis(
        key=key, label=label, order=order, hint=hint,
        counts=f"{_HERE}.fake_counts",
        rows=f"{_HERE}.fake_rows",
        ids_for=f"{_HERE}.fake_ids_for",
        set_for=f"{_HERE}.fake_set_for",
    )


class TestTheRegistryIsPure:
    def test_an_axis_needs_a_key_a_label_and_a_dotted_counter(self):
        with pytest.raises(ValueError):
            EntitlementAxis(key="", label="Fakes", counts=f"{_HERE}.fake_counts")
        with pytest.raises(ValueError):
            EntitlementAxis(key="test.axis", label="", counts=f"{_HERE}.fake_counts")
        with pytest.raises(ValueError):
            EntitlementAxis(key="test.axis", label="Fakes", counts="not_dotted")

    def test_the_editing_trio_is_all_three_or_none(self):
        """A `rows` with no `set_for` renders a panel whose buttons do
        nothing; a `set_for` with no `rows` has nothing to validate a
        submitted id against. Neither half-axis is offerable, so neither
        is constructible."""
        with pytest.raises(ValueError):
            EntitlementAxis(key="test.axis", label="Fakes",
                            counts=f"{_HERE}.fake_counts", rows=f"{_HERE}.fake_rows")
        with pytest.raises(ValueError):
            EntitlementAxis(key="test.axis", label="Fakes",
                            counts=f"{_HERE}.fake_counts",
                            set_for=f"{_HERE}.fake_set_for")
        counted_only = EntitlementAxis(key="test.axis", label="Fakes",
                                       counts=f"{_HERE}.fake_counts")
        assert counted_only.editable is False
        assert _editable().editable is True

    def test_registration_is_idempotent(self):
        """The same shape every sibling registry has, so an
        `AppConfig.ready()` that runs twice registers one axis."""
        spec = _editable()
        register_entitlement_axis(spec)
        register_entitlement_axis(spec)
        assert all_entitlement_axes() == [spec]

    def test_axes_come_back_in_display_order_not_registration_order(self):
        """`order` is EXPLICIT precisely so that moving an app in
        `INSTALLED_APPS` -- which is what decides `ready()` order --
        cannot silently reshuffle a page's columns."""
        late = _editable(key="test.late", label="Late", order=90)
        early = _editable(key="test.early", label="Early", order=10)
        register_entitlement_axis(late)
        register_entitlement_axis(early)
        assert [spec.key for spec in all_entitlement_axes()] == [
            "test.early", "test.late"]

    def test_a_tie_on_order_keeps_registration_order(self):
        first = _editable(key="test.a", label="A", order=50)
        second = _editable(key="test.b", label="B", order=50)
        register_entitlement_axis(first)
        register_entitlement_axis(second)
        assert [spec.key for spec in all_entitlement_axes()] == ["test.a", "test.b"]

    def test_an_unregistered_key_answers_none_rather_than_raising(self):
        register_entitlement_axis(_editable())
        assert entitlement_axis("test.axis") is not None
        assert entitlement_axis("test.nope") is None
        assert axis_for("test.nope") is None


class TestResolvingThem:
    def test_the_round_trip_reads_rows_active_ids_and_counts(self):
        spec = _editable()
        register_entitlement_axis(spec)
        assert [row.id for row in axis_rows(spec)] == ["alpha", "beta", "gamma"]
        assert axis_active_ids(spec, 1) == {"alpha", "beta"}
        assert axis_active_ids(spec, 2) == set()
        assert axis_counts(spec) == {1: 2}
        assert counts_by_axis() == {"test.axis": {1: 2}}

    def test_active_ids_are_strings_whatever_the_column_returns(self):
        """A column keyed by integer primary keys answers integers; the
        panels compare them against form-body strings. ONE vocabulary,
        normalised at this seam rather than at four call sites.

        `fake_int_ids_for` is a plain module-level helper beside the
        other fakes -- this test used to write its own local function
        into `globals()` so the dotted path would resolve, a side effect
        the isolation fixture (which restores only the registry) never
        undid."""
        spec = EntitlementAxis(
            key="test.ints", label="Ints", counts=f"{_HERE}.fake_counts",
            rows=f"{_HERE}.fake_rows", ids_for=f"{_HERE}.fake_int_ids_for",
            set_for=f"{_HERE}.fake_set_for")
        assert axis_active_ids(spec, 7) == {"1", "2"}

    def test_a_dotted_path_that_does_not_resolve_is_never_swallowed(self):
        """The rule `identity/cascades.py` states for the delete side. A
        page that rendered a zero for a column whose handler had been
        renamed would reassure an operator about reach it never
        measured."""
        spec = EntitlementAxis(key="test.broken", label="Broken",
                               counts=f"{_HERE}.no_such_function")
        register_entitlement_axis(spec)
        with pytest.raises(ImportError):
            axis_counts(spec)
        with pytest.raises(ImportError):
            counts_by_axis()

    def test_panels_split_the_catalogue_into_available_and_active(self):
        register_entitlement_axis(_editable(hint="A hint."))
        panels = axis_panels(1)
        assert len(panels) == 1
        panel = panels[0]
        assert panel["label"] == "Fakes"
        assert panel["hint"] == "A hint."
        assert [row.id for row in panel["active"]] == ["alpha", "beta"]
        assert [row.id for row in panel["available"]] == ["gamma"]
        assert (panel["active_count"], panel["available_count"]) == (2, 1)
        assert panel["total"] == 3

    def test_a_counted_only_axis_gets_no_panel(self):
        """Document labels are counted from this direction but edited
        from the library's own page; the reach panel stays the single
        home for their count."""
        register_entitlement_axis(EntitlementAxis(
            key="test.counted", label="Counted", counts=f"{_HERE}.fake_counts"))
        assert axis_panels(1) == []


class TestTheDoorIntoAColumnsOwnPage:
    """FIX ROUND 2, P2-I1. An axis counted here but edited on its own
    column's page needs somewhere to send an operator, and the first
    version of that was a hand-written section plus a
    `reverse("rag-documents")` inside `identity/views.py` -- the one
    place in this column naming another column's route, and a direct
    contradiction of what this registry exists to claim. A door is a
    REGISTRATION now.
    """

    def test_a_link_must_be_a_dotted_path(self):
        with pytest.raises(ValueError):
            EntitlementAxis(key="test.axis", label="Fakes",
                            counts=f"{_HERE}.fake_counts", link="not_dotted")

    def test_an_axis_without_a_link_has_no_door(self):
        counted = EntitlementAxis(key="test.axis", label="Fakes",
                                  counts=f"{_HERE}.fake_counts")
        assert counted.has_door is False
        register_entitlement_axis(counted)
        assert axis_doors(7) == []

    def test_a_door_carries_the_columns_own_heading_sentence_and_url(self):
        """THE COLUMN OWNS THE WORDS. `identity/` composes a door
        without knowing what is behind it."""
        register_entitlement_axis(EntitlementAxis(
            key="test.axis", label="Fakes", hint="Edited elsewhere.",
            counts=f"{_HERE}.fake_counts", link=f"{_HERE}.fake_link"))
        assert axis_doors(7) == [{
            "key": "test.axis",
            "label": "Fakes",
            "hint": "Edited elsewhere.",
            "url": "/fakes/?entitlement=7",
        }]

    def test_an_editable_axis_may_carry_a_door_as_well(self):
        """Not gated on counted-only: an axis with a panel that also
        names its column's page gets both, and neither knows about the
        other."""
        spec = _editable()
        register_entitlement_axis(EntitlementAxis(
            key=spec.key, label=spec.label, counts=spec.counts, rows=spec.rows,
            ids_for=spec.ids_for, set_for=spec.set_for, link=f"{_HERE}.fake_link"))
        assert [door["key"] for door in axis_doors(1)] == ["test.axis"]
        assert [panel["key"] for panel in axis_panels(1)] == ["test.axis"]

    def test_a_link_that_does_not_resolve_is_never_swallowed(self):
        """An entitlement page with a door silently missing is the
        failure this resolver's whole no-swallow rule exists to
        prevent."""
        register_entitlement_axis(EntitlementAxis(
            key="test.broken", label="Broken", counts=f"{_HERE}.fake_counts",
            link=f"{_HERE}.no_such_function"))
        with pytest.raises(ImportError):
            axis_doors(1)


class TestApplyingAWrite:
    def test_add_and_remove_reach_the_columns_own_writer(self):
        spec = _editable()
        register_entitlement_axis(spec)
        summary = apply_axis(spec, 1, add={"gamma"}, remove={"alpha"},
                             actor="principal", actor_user="user")
        assert summary == {"added": 1, "removed": 1}
        assert _CALLS == [(1, frozenset({"gamma"}), frozenset({"alpha"}),
                           "principal", "user")]

    def test_an_id_outside_the_catalogue_is_refused_before_any_write(self):
        """The catalogue IS the validator -- a feature-gated row absent
        from this install must not be labellable by a hand-typed form."""
        spec = _editable()
        register_entitlement_axis(spec)
        with pytest.raises(AxisRefused) as refusal:
            apply_axis(spec, 1, add={"alpha", "made-up"}, remove=set(),
                       actor="principal")
        assert "made-up" in str(refusal.value)
        assert _CALLS == []

    def test_a_stray_id_on_the_remove_side_is_refused_too(self):
        spec = _editable()
        register_entitlement_axis(spec)
        with pytest.raises(AxisRefused):
            apply_axis(spec, 1, add=set(), remove={"made-up"}, actor="principal")
        assert _CALLS == []
