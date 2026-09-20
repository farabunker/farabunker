"""`preflight_turn`: the ONE place "can this agent take a turn at all"
is answered, for the CLI, the page, and any future caller.

The double is patched at `agents.runtime.preflight.loop_module.
supports_tool_calling` -- the owning module attribute `preflight.py`
looks up at call time -- never at `agents.runtime.bindings.resolve`,
which is code under test here (a real `ModelConnection`/`RoleBinding`
row via `bound_chat_role` resolves it for real).
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolSpec, register_tool
from agents.runtime import loop as loop_module
from agents.runtime.preflight import (
    NO_TOOL_CALLING, UNBOUND, UNREGISTERED_CONNECTION, dropped_tool_notes, preflight_turn,
)
from agents.runtime.tests._helpers import (  # noqa: F401
    bound_chat_role, isolated_tool_registry, make_agent, make_conversation, make_entitlement,
    make_user, posture, user_principal,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


class TestTheHappyPaths:
    def test_a_bound_role_and_a_tool_capable_model_is_ok(self, bound_chat_role, monkeypatch):
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: True)
        agent = make_agent(slug="general", tool_keys=[])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert result.ok is True
        assert result.reason == ""
        assert result.resolved is not None

    def test_supports_tool_calling_returning_none_runs_the_turn(
            self, bound_chat_role, monkeypatch):
        """`None` means the engine does not report the fact at all
        (`models/contracts/engines/base.py:450-458`); the turn is
        attempted rather than refused."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: None)
        agent = make_agent(slug="general", tool_keys=["stub.safe"])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert result.ok is True

    def test_a_no_tools_agent_is_ok_even_when_the_model_cannot_call_tools(
            self, bound_chat_role, monkeypatch):
        """The gate is conditional on the agent actually HOLDING tools --
        `agent_turn._preflight` already read this way and `loop._run_turn`
        matches it."""
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: False)
        agent = make_agent(slug="general", tool_keys=[])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert result.ok is True


class TestTheRefusals:
    def test_an_unbound_role_names_the_role(self):
        agent = make_agent(slug="general", llm_role="nothing.bound.here")
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert result.ok is False
        assert result.reason == UNBOUND
        assert "nothing.bound.here" in result.message

    def test_a_dead_connection_pk_gets_a_different_message_than_unbound(self, bound_chat_role):
        """The operator picked a model that is gone -- a different
        problem from never having picked one -- so the sentence must
        differ, not just the reason code."""
        agent = make_agent(slug="general")
        result = preflight_turn(agent, "999999", actor=OPEN_PRINCIPAL)
        assert result.ok is False
        assert result.reason == UNREGISTERED_CONNECTION
        assert "no longer registered" in result.message
        assert "nothing.bound.here" not in result.message

        unbound_agent = make_agent(slug="unbound-role", llm_role="nothing.bound.here")
        unbound_result = preflight_turn(unbound_agent, "", actor=OPEN_PRINCIPAL)
        assert unbound_result.message != result.message

    def test_an_implausibly_large_connection_id_is_refused_not_a_500(self, bound_chat_role):
        """NEVER-500 edge: `int("9" * 25)` parses fine (Python ints are
        unbounded), but `ModelConnection`'s pk is a `BigAutoField` --
        Postgres's `bigint` range. `_plausible_connection_id` refuses it
        at the boundary rather than depend on Django's own integer-
        lookup `EmptyResultSet` short-circuit (`django/db/models/
        lookups.py`) to keep this off a `DataError` traceback -- an ORM
        implementation detail this module has no business trusting."""
        agent = make_agent(slug="general")
        result = preflight_turn(agent, "9" * 25, actor=OPEN_PRINCIPAL)
        assert result.ok is False
        assert result.reason == UNREGISTERED_CONNECTION
        assert "no longer registered" in result.message

    def test_a_granted_tools_agent_on_a_non_tool_calling_model_is_refused(
            self, bound_chat_role, monkeypatch):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: False)
        agent = make_agent(slug="general", tool_keys=["stub.safe"])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert result.ok is False
        assert result.reason == NO_TOOL_CALLING
        assert "cannot call tools" in result.message


class TestDroppedTools:
    def test_a_granted_but_unregistered_tool_is_named_in_dropped_tools(
            self, bound_chat_role, monkeypatch):
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: True)
        agent = make_agent(slug="general", tool_keys=["not.registered"])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert result.ok is True
        assert result.dropped_tools == ("not.registered",)


class TestDroppedToolNotes:
    """CQ-6: one function, so a POST's 202 body (`agents.chat.service.
    start_turn`) and a GET's thread banner (`agents.chat.views.thread.
    thread_context`) can never disagree about the same `Preflight`."""

    def test_no_dropped_tools_is_no_notes(self, bound_chat_role, monkeypatch):
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: True)
        agent = make_agent(slug="general", tool_keys=[])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        assert dropped_tool_notes(result) == ()

    def test_one_note_per_dropped_key_naming_it(self, bound_chat_role, monkeypatch):
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: True)
        agent = make_agent(slug="general", tool_keys=["not.registered"])
        result = preflight_turn(agent, "", actor=OPEN_PRINCIPAL)
        notes = dropped_tool_notes(result)
        assert len(notes) == 1
        assert "not.registered" in notes[0]
        assert "not available on this install" in notes[0]


class TestAForbiddenModelIsRefusedBeforeTheTurnIsWritten:
    def test_preflight_answers_MODEL_NOT_PERMITTED(self):
        """The directive's "refuses at preflight/enqueue, not mid-run".
        A turn runs as the USER (the acting rule), so a connection the
        actor may not use is refused where every other unrunnable turn is
        refused -- before a `Turn` row exists."""
        from agents.runtime.preflight import MODEL_NOT_PERMITTED, preflight_turn
        from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
        from models.registry.tests._helpers import make_chat_connection
        member = make_user()
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        agent = make_agent(tool_keys=[])
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, str(connection.pk),
                                   actor=user_principal(member))
        assert check.ok is False
        assert check.reason == MODEL_NOT_PERMITTED
        assert "entitlement" in check.message.lower()

    def test_start_turn_answers_403_for_it_and_writes_nothing(self):
        """403, not the 503 every other preflight refusal gets: an
        unavailable service and an action this account may not take are
        different facts, and `TurnStart.status` exists precisely so the
        view does not have to guess."""
        from agents.chat.service import start_turn
        from agents.models import Turn
        from models.registry.models import ModelSet, ModelSetEntitlement, ModelSetMember
        from models.registry.tests._helpers import make_chat_connection
        member = make_user()
        connection = make_chat_connection()
        model_set = ModelSet.objects.create(name="Under test")
        ModelSetMember.objects.create(model_set=model_set, connection=connection)
        ModelSetEntitlement.objects.create(model_set=model_set,
                                           entitlement=make_entitlement(name="Legal"))
        conversation = make_conversation()
        with posture(POSTURE_ENTERPRISE):
            start = start_turn(conversation, "hello", connection=str(connection.pk),
                               actor=user_principal(member))
        assert start.ok is False
        assert start.status == 403
        assert Turn.objects.count() == 0


class TestARestrictedAgentIsRefusedBeforeTheTurnIsWritten:
    def test_preflight_answers_AGENT_NOT_PERMITTED(self):
        from agents.models import AgentEntitlement
        from agents.runtime.preflight import AGENT_NOT_PERMITTED, preflight_turn
        agent = make_agent(tool_keys=[])
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=user_principal(make_user()))
        assert check.ok is False
        assert check.reason == AGENT_NOT_PERMITTED

    def test_start_turn_answers_403_and_writes_nothing(self):
        from agents.chat.service import start_turn
        from agents.models import AgentEntitlement, Turn
        agent = make_agent(tool_keys=[])
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        conversation = make_conversation(agent=agent)
        with posture(POSTURE_ENTERPRISE):
            start = start_turn(conversation, "hello", actor=user_principal(make_user()))
        assert start.status == 403
        assert Turn.objects.count() == 0

    def test_a_service_principal_is_refused_too(self):
        """Spec section 9.6, knock-on 5: `manage.py agent_turn` runs as
        `SERVICE_PRINCIPAL`, and a service principal holds no
        entitlements (grants attach to a user or a group by the XOR
        constraint) -- so a labelled agent is unreachable from the shell
        exactly as it is from a member who lacks the entitlement. Without
        this pin, property (e) has no test left that actually exercises
        this path with a non-user principal: deleting the check inside
        `preflight_turn` would leave every other test in this module
        green."""
        from agents.models import AgentEntitlement
        from agents.runtime.preflight import AGENT_NOT_PERMITTED, preflight_turn
        agent = make_agent(tool_keys=[])
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=SERVICE_PRINCIPAL)
        assert check.ok is False
        assert check.reason == AGENT_NOT_PERMITTED
