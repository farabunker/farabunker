"""`agents/entitlements.py::tool_access_for` -- the Django-side builder
for the pure `ToolAccess` value.

ONCE PER TURN, not once per tool: one query over `ToolEntitlement` (a
small table) and one over the principal's grants.
"""
from __future__ import annotations

import pytest

from agents.entitlements import tool_access_for
from agents.models import ToolEntitlement
from agents.tests._helpers import (
    grant, make_admin, make_entitlement, make_user, posture, reset_settings,
    seed_sweep_posture, user_principal,
)
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _settings():
    reset_settings()
    seed_sweep_posture()
    yield
    reset_settings()


class TestToolAccessFor:
    def test_open_posture_is_unrestricted_and_runs_no_permission_query(
            self, django_assert_num_queries):
        """The open box must not pay for the enterprise's machinery. One
        primary-key read of the settings singleton, and nothing else.

        DEVIATION FROM THE BRIEF (see task-5-report.md): pinned to
        `POSTURE_OPEN` explicitly -- `_settings`' own `seed_sweep_posture`
        call means this module also runs under
        `FARABUNKER_TEST_POSTURE=enterprise`, and a test that pins its
        own posture always wins that sweep (`identity.testing.
        seed_sweep_posture`'s own docstring), the same precedence every
        other open-posture pin in the tree already relies on (e.g.
        `identity/tests/test_zero_queries.py`)."""
        with posture(POSTURE_OPEN):
            with django_assert_num_queries(1):
                access = tool_access_for(OPEN_PRINCIPAL)
        assert access.unrestricted is True
        assert access.required == {}

    def test_a_member_gets_the_labels_and_their_own_holdings(self):
        member = make_user()
        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        grant(finance, user=member)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            access = tool_access_for(user_principal(member))
        assert access.unrestricted is False
        assert access.required == {"rag.search": frozenset({legal.pk})}
        assert access.held == frozenset({finance.pk})
        assert access.allows("rag.search") is False
        assert access.allows("rag.ask") is True

    def test_an_admin_with_the_content_setting_off_is_still_restricted(self):
        """`sees_all_content` is what unrestricts a tool list, and it is
        `is_admin AND admin_sees_content`. Administering is not reading,
        and calling somebody's labelled tool would be reading."""
        admin = make_admin()
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            assert tool_access_for(user_principal(admin)).allows("rag.search") is False
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            assert tool_access_for(user_principal(admin)).allows("rag.search") is True

    def test_a_service_principal_gets_unlabelled_tools_only(self):
        """Grants attach to a user or a group, by the XOR constraint, and
        service-account tokens are IA-3 -- so the watcher and the CLI get
        unlabelled tools only, permanently, for now. Labelling a tool a
        shell path uses makes it silently unavailable there, which is why
        the tool-label form warns about exactly that."""
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        with posture(POSTURE_ENTERPRISE):
            access = tool_access_for(SERVICE_PRINCIPAL)
        assert access.allows("rag.search") is False
        assert access.allows("rag.ask") is True

    def test_preflight_reports_a_labelled_tool_as_UNENTITLED_not_as_dropped(self):
        """THE TWO CAUSES STAY APART, and the copy is why. A key that is
        not registered on this install is reported to the operator as
        "not available on this install" -- true, and useful. A key the
        acting principal simply lacks an entitlement for is NOT that, and
        saying so would be a false sentence naming a tool the entitlement
        was meant to keep out of their way. `unentitled_tools` renders
        nothing at all: enforcement by omission, the same mechanism
        `available_tools` already uses for the depth cap, and the same
        reason `granted_tools` logs this drop at DEBUG."""
        from agents.models import ToolEntitlement
        from agents.runtime.preflight import dropped_tool_notes, preflight_turn
        from agents.tests._helpers import bind_chat_role, make_agent
        from models.contracts.roles import CHAT_CONVERSE_ROLE
        member = make_user()
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(tool_keys=["rag.search"])
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=user_principal(member))
        assert check.unentitled_tools == ("rag.search",)
        assert check.dropped_tools == ()
        assert dropped_tool_notes(check) == ()


class TestPreflightGatesOnGrantedNotRegistered:
    """Review finding 2 (IA-2 T5 fix round 1): `preflight_turn`'s
    `NO_TOOL_CALLING` refusal is gated on `granted` (post-entitlement),
    not `registered` (pre-entitlement) -- a principal who ends up with
    NO entitled tool at all is never going to be offered one regardless
    of what the bound model can do, so refusing them over a capability
    that will never be exercised would be dishonest. Both tests bind a
    real chat role and patch `supports_tool_calling` to `False` so the
    gate WOULD fire if it were still reading `registered`; the only
    variable between them is whether the actor holds the entitlement.
    """

    def test_an_unentitled_member_is_effectively_tool_less_so_no_refusal(self, monkeypatch):
        from agents.runtime import loop as loop_module
        from agents.runtime.preflight import preflight_turn
        from agents.tests._helpers import bind_chat_role, make_agent
        from models.contracts.roles import CHAT_CONVERSE_ROLE
        member = make_user()
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        bind_chat_role(CHAT_CONVERSE_ROLE)
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: False)
        agent = make_agent(tool_keys=["rag.search"])
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=user_principal(member))
        assert check.unentitled_tools == ("rag.search",)
        assert check.ok is True

    def test_an_entitlement_holder_still_gets_the_honest_refusal(self, monkeypatch):
        from agents.runtime import loop as loop_module
        from agents.runtime.preflight import NO_TOOL_CALLING, preflight_turn
        from agents.tests._helpers import bind_chat_role, make_agent
        from models.contracts.roles import CHAT_CONVERSE_ROLE
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=legal)
        bind_chat_role(CHAT_CONVERSE_ROLE)
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: False)
        agent = make_agent(tool_keys=["rag.search"])
        with posture(POSTURE_ENTERPRISE):
            check = preflight_turn(agent, None, actor=user_principal(member))
        assert check.unentitled_tools == ()
        assert check.ok is False
        assert check.reason == NO_TOOL_CALLING
