"""The wall at seams two and three — the turn planner's entitlement
intersection and the model half, at all four call sites."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from agents.chat import pickers
from agents.chat.pickers import chat_picker_options
from agents.entitlements import tool_access_for, wall_for
from agents.models import Conversation, WorkstreamScopeEntitlement
from agents.runtime import jobs
from agents.runtime.tests._helpers import FakeToolLLM
from agents.tests._helpers import (  # noqa: F401 -- bound_chat_role used as a fixture
    _workstream, bound_chat_role, make_agent, make_job_ctx,
)
from identity.contracts.principals import OPEN_PRINCIPAL, payload_fields
from identity.testing import grant, make_admin, make_entitlement, make_user, posture, user_principal
from models.registry.access import model_access_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent():
    """A plain `Agent` row -- this module's own fixture, matching
    `agents/tests/test_workstreams.py`'s own (no shared `agent` fixture
    exists across this app's tests)."""
    return make_agent()


def _llm_answering(text):
    """A minimal fake chat LLM that answers with a final text turn and
    no tool call -- `agents/runtime/tests/_helpers.py::FakeToolLLM`
    already implements exactly the two methods `run_loop` calls
    (`.chat`, `.get_tool_calls_from_response`), so this reuses it rather
    than growing a third copy of the same double."""
    return FakeToolLLM([("final", text)])


def test_a_walled_turn_narrows_the_held_set_by_intersection_never_by_union():
    """`held & wall`, so a wall can only ever REMOVE entitlements from
    what the acting user already holds. It is never a grant."""
    user = make_user()
    e1, e2 = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    grant(e2, user=user)
    with posture("enterprise"):
        access = tool_access_for(user_principal(user), wall=frozenset({e1.pk}))
    assert access.held == frozenset({e1.pk})
    assert access.unrestricted is False


def test_a_wall_naming_an_entitlement_the_user_lacks_narrows_to_empty_not_to_it():
    user = make_user()
    e1, unheld = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    with posture("enterprise"):
        access = tool_access_for(user_principal(user), wall=frozenset({unheld.pk}))
    assert access.held == frozenset()


def test_and_not_wall_is_the_whole_of_the_change_to_the_unrestricted_branch():
    """AUTHOR DECISION 8, named after the line. Without `and not wall`,
    an administrator with `admin_sees_content` on would get
    `UNRESTRICTED_TOOL_ACCESS` and the wall would silently do NOTHING
    for tools while doing something for documents. A wall is not a
    permission check; it is a SCOPE THE OWNER CHOSE, and choosing it
    means choosing it for yourself as well.

    The one place a plausible refactor would silently undo the wall."""
    admin = make_admin()
    ent = make_entitlement()
    with posture("enterprise", admin_sees_content=True):
        unwalled = tool_access_for(user_principal(admin))
        walled = tool_access_for(user_principal(admin), wall=frozenset({ent.pk}))
    assert unwalled.unrestricted is True
    assert walled.unrestricted is False
    assert walled.held == frozenset()


def test_the_model_half_narrows_identically():
    user = make_user()
    e1, e2 = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    grant(e2, user=user)
    with posture("enterprise"):
        access = model_access_for(user_principal(user), wall=frozenset({e1.pk}))
    assert access.held == frozenset({e1.pk})
    assert access.unrestricted is False


def test_wall_for_reads_the_table_on_an_accounts_box(agent):
    user = make_user()
    ent = make_entitlement()
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    with posture("enterprise"):
        assert wall_for(conversation) == frozenset({ent.pk})


def test_wall_for_returns_empty_on_an_open_box_without_reading_the_table(
        agent, django_assert_num_queries):
    """RULING A, THE ANTI-BRICKING HALF. A stream carrying wall rows
    written under `enterprise`, run on an open box, gets
    `UNRESTRICTED_TOOL_ACCESS`, starts its turns and refuses nothing --
    and reads ZERO permission rows to decide it."""
    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    with posture("open"):
        with django_assert_num_queries(1):     # the posture singleton, and nothing else
            wall = wall_for(conversation)
        assert wall == frozenset()
        assert tool_access_for(OPEN_PRINCIPAL, wall=wall).unrestricted is True


def test_a_loose_conversation_has_no_wall_and_costs_no_query(agent, django_assert_num_queries):
    conversation = Conversation.objects.create(agent=agent)
    with posture("enterprise"):
        with django_assert_num_queries(0):
            assert wall_for(conversation) == frozenset()


def test_all_four_call_sites_narrow(monkeypatch, agent, bound_chat_role):
    """§17.3: one parametrised test over `plan_turn`, `_run_turn`,
    `preflight_turn` and `chat_picker_options`, because §6.3's whole
    correction was that an earlier draft named two of them.

    Asserted by RECORDING the `wall=` each site passes, rather than by
    four separate end-to-end refusals: the property under test is that
    every site threads the same value, and four refusals would pass even
    if one site computed its own."""
    seen = []

    def _record(principal, *, settings_row=None, wall=frozenset()):
        seen.append(frozenset(wall))
        return _real_tool_access(principal, settings_row=settings_row, wall=wall)

    from agents import entitlements as ent_module
    from agents.runtime import jobs, loop as loop_module, preflight

    _real_tool_access = ent_module.tool_access_for
    for module in (ent_module, jobs, loop_module, preflight):
        monkeypatch.setattr(module, "tool_access_for", _record, raising=False)

    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = Conversation.objects.get(pk=conversation.pk).turns.create(
        index=0, role="assistant", text="", state="queued", depth=0)
    payload = {"turn": turn.pk, **payload_fields(user_principal(user))}

    with posture("enterprise"):
        jobs.plan_turn(payload)
        preflight.preflight_turn(agent, "", actor=user_principal(user),
                                 conversation=conversation)
        # `_run_turn(payload, models, ctx)` -- it resolves the turn, the
        # conversation and the agent itself from `payload["turn"]`
        # (`agents/runtime/loop.py:171-174`), so nothing is handed to it.
        with patch.object(loop_module.gateway, "get_llm_for",
                          return_value=_llm_answering("hi")):
            loop_module._run_turn(payload, [], make_job_ctx())

    # THREE runtime sites, all narrowed with the SAME non-empty value.
    assert len(seen) == 3
    assert set(seen) == {frozenset({ent.pk})}

    # The fourth site is the RENDER half, and it narrows the model axis
    # rather than the tool axis -- so it is asserted on its own value.
    model_seen = []
    _real_model_access = pickers.model_access_for
    monkeypatch.setattr(
        pickers, "model_access_for",
        lambda p, *, settings_row=None, wall=frozenset(): (
            model_seen.append(frozenset(wall))
            or _real_model_access(p, settings_row=settings_row, wall=wall)))
    with posture("enterprise"):
        chat_picker_options(user_principal(user), wall=wall_for(conversation))
    assert model_seen == [frozenset({ent.pk})]


def test_the_picker_omits_exactly_what_the_walled_turn_would_refuse():
    """THE RENDER-VS-GATE PAIR, asserted AS A PAIR, on the surface the
    wall is most visible on. `chat_picker_options`' own docstring says a
    connection the principal may not use "is never in the offered
    options"; unnarrowed it would offer models the walled turn then
    refuses."""
    user = make_user()
    e1, e2 = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    grant(e2, user=user)
    # THE WORLD IS BUILT HERE. Without it `picker_options` returns an
    # empty list, the loop never runs, and the test passes with zero
    # assertions on the one property this task exists to prove.
    #
    # `models/registry/tests/_helpers.py` has NO model-set builder --
    # the real one is `_set_with` at `models/registry/tests/
    # test_model_sets.py:91`, a module-private function in a test module
    # -- so this is a standalone copy, the convention
    # `tools/rag/tests/test_jobs.py:41-45` documents.
    inside = _connection_in_set_attached_to(e1, name="inside the wall")
    outside = _connection_in_set_attached_to(e2, name="outside the wall")

    with posture("enterprise"):
        offered = chat_picker_options(user_principal(user), wall=frozenset({e1.pk})) or []
        access = model_access_for(user_principal(user), wall=frozenset({e1.pk}))

    # BOTH DIRECTIONS, so the test fails if the wall stops narrowing as
    # readily as if it starts over-narrowing.
    assert {o["value"] for o in offered} == {str(inside.pk)}
    assert access.allows(inside.pk) is True
    assert access.allows(outside.pk) is False


def _connection_in_set_attached_to(entitlement, *, name):
    """One chat-capable `ModelConnection`, in a `ModelSet` attached to
    `entitlement`, bound to the chat role."""
    from models.contracts.roles import CHAT_CONVERSE_ROLE
    from models.registry.models import (
        ModelConnection, ModelSet, ModelSetEntitlement, ModelSetMember, RoleBinding,
    )

    connection = ModelConnection.objects.create(
        name=name, engine="test-inference", endpoint="http://fake-inference:1",
        model_id="a-chat-model", capabilities=["chat"])
    model_set = ModelSet.objects.create(name=f"set for {name}")
    ModelSetMember.objects.create(model_set=model_set, connection=connection)
    ModelSetEntitlement.objects.create(model_set=model_set, entitlement=entitlement)
    RoleBinding.objects.get_or_create(role_key=CHAT_CONVERSE_ROLE,
                                      defaults={"connection": connection})
    return connection


def test_a_stream_turn_on_a_personal_box_reads_the_wall_exactly_once(
        agent, bound_chat_role, django_assert_num_queries):
    """§13's honest number, pinned as an EQUALITY rather than an
    inequality: the wall is read once per stream turn, over
    `uniq_workstream_scope`, WHATEVER the number of seams that consume
    the value -- which is what "threaded, not re-read" means."""
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = conversation.turns.create(index=0, role="assistant", text="",
                                     state="queued", depth=0)

    with posture("personal"):
        with CaptureQueriesContext(connection) as captured:
            jobs.plan_turn({"turn": turn.pk, **payload_fields(user_principal(user))})

    assert _hits(captured, "agents_workstreamscopeentitlement") == 1


def test_an_admitted_stream_turn_reads_the_wall_table_exactly_once_through_run_turn(
        agent, bound_chat_role):
    """E5's own honest number, pinned as an EQUALITY over `agents.runtime.
    loop._run_turn` -- the sibling of the pin just above, which covers
    `jobs.plan_turn` instead.

    Before E5, `_run_turn` computed `wall = wall_for(conversation)` AND
    THEN `workstream_scope(principal, conversation.workstream_id)` (whose
    own `wall_ids` call reads the SAME table again to fill
    `WorkstreamScope.wall`) -- two reads of `WorkstreamScopeEntitlement`
    for one turn, on the ADMITTED path alone. E5 reordered this so
    `workstream_scope` is computed first and `wall` is taken from its own
    `.wall` field on this path, so `wall_for` is never called a second
    time. This is the test that would fail if that reordering ever
    regressed back to reading the table twice.
    """
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = Conversation.objects.get(pk=conversation.pk).turns.create(
        index=0, role="assistant", text="", state="queued", depth=0)
    payload = {"turn": turn.pk, **payload_fields(user_principal(user))}

    from agents.runtime import loop as loop_module

    with posture("enterprise"):
        with patch.object(loop_module.gateway, "get_llm_for",
                          return_value=_llm_answering("hi")):
            with CaptureQueriesContext(connection) as captured:
                loop_module._run_turn(payload, [], make_job_ctx())

    assert _hits(captured, "agents_workstreamscopeentitlement") == 1


def test_a_stream_turn_on_an_open_box_reads_no_permission_table_at_all(agent, bound_chat_role):
    """§19.1 done-when 9, the query half: ZERO queries against
    `EntitlementGrant`, `WorkstreamScopeEntitlement` and (in WS-2)
    `WorkstreamTaint`. Ruling A is what makes this true -- `wall_for`
    short-circuits on `accounts_on()` before touching the table, so
    deciding `not wall` never costs a read."""
    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = conversation.turns.create(index=0, role="assistant", text="",
                                     state="queued", depth=0)

    with posture("open"):
        with CaptureQueriesContext(connection) as captured:
            jobs.plan_turn({"turn": turn.pk})

    for table in ("identity_entitlementgrant", "agents_workstreamscopeentitlement",
                  "agents_workstreamtaint"):
        assert _hits(captured, table) == 0, table


def _hits(captured, table: str) -> int:
    """How many captured statements name `table`.

    ASSERTED ON THE TABLE NAME IN THE SQL, not on a total count, which is
    what makes the pin non-vacuous: a total would move with every
    unrelated query the platform adds, and the property under test is
    about ONE table.
    """
    return sum(1 for q in captured.captured_queries if table in q["sql"])
