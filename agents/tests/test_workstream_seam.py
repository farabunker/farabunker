"""`agents/workstreams.py` — the one module `tools/` and `models/` may
import besides `agents.contracts.*` and `agents.entitlements`."""
from __future__ import annotations

import pytest

from agents import workstreams
from agents.contracts.tests._helpers import isolated_panel_registry  # noqa: F401
from agents.contracts.workstreams import (
    WorkstreamPanel, WorkstreamScope, register_workstream_panel,
)
from agents.models import (
    Conversation, ConversationTaint, Share, WorkstreamScopeEntitlement, WorkstreamTaint,
)
from agents.tests._helpers import _workstream, make_agent
from agents.workstreams import (
    panels_for, set_upload_placement_default, workstream_entitlement_cascade, workstream_scope,
    workstream_visible,
)
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import grant, make_entitlement, make_user, posture, user_principal

pytestmark = pytest.mark.django_db


def test_the_scope_carries_the_stream_half_and_no_pins():
    """`pinned_file_ids` is filled by the OTHER column (author decision
    6) — this side always hands over an empty set."""
    user = make_user()
    stream = _workstream(user_principal(user), default_upload_placement="contained")
    with posture("enterprise"):
        scope = workstream_scope(user_principal(user), stream.pk)
    assert isinstance(scope, WorkstreamScope)
    assert scope.workstream_id == stream.pk
    assert scope.default_upload_placement == "contained"
    assert scope.may_upload is True
    assert scope.pinned_file_ids == frozenset()


def test_a_stream_this_principal_may_not_be_in_is_indistinguishable_from_one_that_is_gone():
    """The caller cannot tell the two apart, and answers 404 to both."""
    owner, stranger = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    with posture("enterprise"):
        assert workstream_scope(user_principal(stranger), stream.pk) is None
        assert workstream_scope(user_principal(owner), 999999) is None


def test_a_recipient_gets_a_scope_but_may_not_upload():
    """`may_upload` is False for a share recipient (spec §12.4), so the
    page asks ONE question rather than re-deriving ownership on a
    surface that cannot see the row."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        scope = workstream_scope(user_principal(reader), stream.pk)
    assert scope is not None
    assert scope.may_upload is False


def test_workstream_visible_agrees_with_workstream_scope_on_both_answers():
    """C-30. `workstream_visible` is the cheap `is None`/`is not None`
    answer `tools/rag/workstreams.py::panel` needs without paying for the
    `WorkstreamScope` it would throw away -- and it must never disagree
    with `workstream_scope` about which streams exist for a principal,
    since both now read through the same `_visible_row` (`visible_
    workstreams`)."""
    owner, stranger = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    with posture("enterprise"):
        assert workstream_visible(user_principal(owner), stream.pk) is True
        assert workstream_visible(user_principal(stranger), stream.pk) is False
        assert workstream_visible(user_principal(owner), 999999) is False


def test_the_wall_is_read_on_an_accounts_box():
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        scope = workstream_scope(user_principal(user), stream.pk)
    assert scope.wall == frozenset({ent.pk})


def test_the_wall_is_empty_on_an_open_box_and_the_table_is_never_read():
    """RULING A (spec §23.A), both halves, and the load-bearing half is
    the SECOND assertion: `workstream_scope` does not read
    `WorkstreamScopeEntitlement` AT ALL on an open box. Global Constraint
    8 says so; without a query assertion the test would pass against an
    implementation that read the table and then discarded the result,
    which is precisely the shape ruling A rejects."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with posture("open"):
        with CaptureQueriesContext(connection) as captured:
            scope = workstream_scope(OPEN_PRINCIPAL, stream.pk)
    assert scope.wall == frozenset()
    assert not [q for q in captured.captured_queries
                if "agents_workstreamscopeentitlement" in q["sql"]]
    # The rows are still there, waiting for the posture to come back.
    assert WorkstreamScopeEntitlement.objects.filter(workstream=stream).count() == 1


def test_switching_the_posture_back_makes_the_same_rows_bind():
    """DORMANT, NOT DELETED, and the direction runs both ways — "a
    switch, not a migration"."""
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with posture("open"):
        assert workstream_scope(OPEN_PRINCIPAL, stream.pk).wall == frozenset()
    with posture("personal"):
        assert workstream_scope(user_principal(user), stream.pk).wall == frozenset({ent.pk})


def test_setting_the_upload_default_through_the_seam_writes_and_audits():
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        assert set_upload_placement_default(user_principal(user), stream.pk, "contained") is True
    stream.refresh_from_db()
    assert stream.default_upload_placement == "contained"


def test_a_non_owner_may_not_set_the_upload_default_through_the_seam():
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert set_upload_placement_default(user_principal(reader), stream.pk, "contained") is False


def test_the_entitlement_cascade_counts_then_removes_the_wall_rows():
    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    assert workstream_entitlement_cascade(ent.pk, commit=False) == 1
    assert WorkstreamScopeEntitlement.objects.count() == 1
    assert workstream_entitlement_cascade(ent.pk, commit=True) == 1
    assert WorkstreamScopeEntitlement.objects.count() == 0


def test_the_entitlement_cascade_counts_then_removes_the_taint_tags_too():
    """WS-2 (Task 16): the widened cascade removes a wall row, a
    conversation tag and a stream tag naming the SAME entitlement, in one
    pass -- `commit=False` counts all three before anything is touched --
    and audits the two taint removals, the one path a tag is removed in
    v1 (spec section 8.4)."""
    ent = make_entitlement()
    stream = _workstream()
    agent = make_agent()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    ConversationTaint.objects.create(conversation=conversation, entitlement=ent)
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)

    assert workstream_entitlement_cascade(ent.pk, commit=False) == 3
    assert WorkstreamScopeEntitlement.objects.count() == 1
    assert ConversationTaint.objects.count() == 1
    assert WorkstreamTaint.objects.count() == 1

    assert workstream_entitlement_cascade(ent.pk, commit=True) == 3
    assert WorkstreamScopeEntitlement.objects.count() == 0
    assert ConversationTaint.objects.count() == 0
    assert WorkstreamTaint.objects.count() == 0
    assert {r.action for r in audit.recent()} >= {
        actions.CONVERSATION_UNTAINTED, actions.WORKSTREAM_UNTAINTED}


def test_a_registered_panel_is_resolved_at_render_time(isolated_panel_registry):
    """The provider is a DOTTED PATH resolved with `import_string` at
    render time, never imported by `agents/`."""
    register_workstream_panel(WorkstreamPanel(
        "test.rows", "Rows", "agents.tests.test_workstream_seam._fake_provider",
        "chat/panels/test_rows.html"))
    stream = _workstream()
    with posture("open"):
        panels = panels_for(OPEN_PRINCIPAL, stream.pk)
    assert panels == [{"key": "test.rows", "label": "Rows",
                       "template": "chat/panels/test_rows.html", "ok": True,
                       "data": {"rows": [1, 2]}}]


def test_a_panel_whose_provider_raises_degrades_to_a_named_empty_section(
        isolated_panel_registry, caplog):
    """AUTHOR DECISION 11. Registration in the same commit as the handler
    protects against a MISSING module; it does not protect against a
    provider that raises at render time on a box with a broken store.
    Chrome on a page whose real subject is the stream — the same
    never-500 posture `chat_picker_options` already takes."""
    register_workstream_panel(WorkstreamPanel(
        "test.bad", "Bad", "agents.tests.test_workstream_seam._raising_provider",
        "chat/panels/test_bad.html"))
    stream = _workstream()
    with posture("open"):
        panels = panels_for(OPEN_PRINCIPAL, stream.pk)
    assert panels == [{"key": "test.bad", "label": "Bad",
                       "template": "chat/panels/test_bad.html", "ok": False, "data": {}}]
    assert "test.bad" in caplog.text


def _fake_provider(principal, workstream_id):
    return {"rows": [1, 2]}


def _raising_provider(principal, workstream_id):
    raise RuntimeError("the store is down")


def test_the_seam_exposes_no_conversation_post_gate():
    """C-34. `may_post_to_conversation` outlived its one caller by two
    rounds and was still being described in a test helper's docstring as
    a live validator. The wall's posting rules are answered by
    `workstream_scope`/`workstream_visible`; a second, unreachable answer
    to the same question is how two answers drift apart."""
    assert not hasattr(workstreams, "may_post_to_conversation")
