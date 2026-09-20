"""The workstream contracts leaf — a frozen value and a registry, no Django."""
from __future__ import annotations

import pytest

from agents.contracts.tests._helpers import isolated_panel_registry  # noqa: F401
from agents.contracts.workstreams import (
    WorkstreamPanel, WorkstreamScope, all_workstream_panels, register_workstream_panel,
)


def test_a_scope_carries_the_stream_half_and_an_empty_pin_set_by_default():
    """`pinned_file_ids` is THE ONE FIELD `agents/` does not fill (spec
    §6.2, author decision 6): the value crosses the seam with the pins
    empty and `tools/rag/workstreams.py::scope_with_pins` fills them."""
    scope = WorkstreamScope(
        workstream_id=7, wall=frozenset({3}), default_upload_placement="", may_upload=True)
    assert scope.workstream_id == 7
    assert scope.wall == frozenset({3})
    assert scope.default_upload_placement == ""
    assert scope.may_upload is True
    assert scope.pinned_file_ids == frozenset()


def test_a_scope_is_frozen():
    scope = WorkstreamScope(workstream_id=1, wall=frozenset(),
                            default_upload_placement="", may_upload=False)
    with pytest.raises(Exception):
        scope.wall = frozenset({9})


def test_a_panel_registers_and_lists_in_registration_order(isolated_panel_registry):
    register_workstream_panel(WorkstreamPanel("a.one", "One", "pkg.mod.one", "t/one.html"))
    register_workstream_panel(WorkstreamPanel("b.two", "Two", "pkg.mod.two", "t/two.html"))
    assert [p.key for p in all_workstream_panels()] == ["a.one", "b.two"]


def test_registering_the_same_key_twice_replaces_rather_than_duplicates(isolated_panel_registry):
    """Idempotent, like every sibling registry in this codebase — an
    `AppConfig.ready()` can run twice in one process."""
    register_workstream_panel(WorkstreamPanel("a.one", "One", "pkg.mod.one", "t/one.html"))
    register_workstream_panel(WorkstreamPanel("a.one", "One again", "pkg.mod.one", "t/one.html"))
    assert [(p.key, p.label) for p in all_workstream_panels()] == [("a.one", "One again")]


@pytest.mark.parametrize("key,label,provider,template,fragment", [
    ("", "One", "pkg.mod.one", "t/one.html", "non-blank key"),
    ("a.one", "", "pkg.mod.one", "t/one.html", "non-blank label"),
    ("a.one", "One", "notdotted", "t/one.html", "dotted path"),
    ("a.one", "One", "pkg.mod.one", "not-a-template", "template path"),
])
def test_a_malformed_panel_is_refused_at_construction(key, label, provider, template,
                                                      fragment):
    """Construction error, not a render-time `ImportError` on somebody's
    stream page — the same `__post_init__` shape `EntitlementCascade`
    already carries."""
    with pytest.raises(ValueError) as exc:
        WorkstreamPanel(key, label, provider, template)
    assert fragment in str(exc.value)
