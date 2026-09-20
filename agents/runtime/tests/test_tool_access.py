"""Done-when 3: an agent is never a way around labels, proven at the
PLANNER, at the LOOP, at the DELEGATION HOP, and on the page that
refuses before it writes.

Every helper here is one `agents/runtime/tests/_helpers.py` already
exports -- `make_agent`, `bind_chat_role`, `isolated_tool_registry` --
and the tool keys are registered by that fixture, so a dropped key is
dropped for the reason under test rather than for being unregistered.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolAccess, ToolContext, ToolSpec, register_tool
from agents.runtime.jobs import _tool_roles
from agents.runtime.loop import available_tools
from agents.runtime.preflight import preflight_turn
from agents.runtime.tests._helpers import (  # noqa: F401 -- the import IS the registration
    bind_chat_role, isolated_tool_registry, make_agent, make_job_ctx,
)
from identity.contracts.principals import OPEN_PRINCIPAL
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

# A registered, non-mutating spec DECLARING A ROLE, so the planner
# assertion below is not vacuous: `_tool_roles` accumulates
# `spec.roles`, and a `roles=()` stub would answer `set()` whether or not
# the access check dropped the key.
LABELLED = ToolSpec(
    key="stub.labelled", label="A labelled stub",
    description="A stub used to prove the access drop.",
    params=(), roles=(CHAT_CONVERSE_ROLE,),
    runner="agents.runtime.tests._helpers.runner_ok",
)


@pytest.fixture(autouse=True)
def _register(isolated_tool_registry):
    # DEVIATION FROM THE BRIEF (see task-5-report.md): depends on
    # `isolated_tool_registry` BY PARAMETER, not only via the module's
    # `pytestmark`, so pytest sets that fixture up FIRST -- its snapshot
    # of `_TOOLS` is taken BEFORE `LABELLED` is registered, and its
    # teardown then actually removes it. An autouse fixture with no such
    # dependency runs before an explicitly-requested one of the same
    # scope (pytest's own autouse-ordering rule), which would put
    # `LABELLED` INSIDE the snapshot `isolated_tool_registry` restores
    # to -- a leak `foundation/ops/tests/test_column_boundaries.py::
    # test_every_registered_runner_lives_in_a_swept_module` catches the
    # moment it runs anywhere after this module in the same session.
    register_tool(LABELLED)


def _refused() -> ToolAccess:
    """Access that labels `stub.labelled` with an entitlement the acting
    principal does not hold."""
    return ToolAccess(required={"stub.labelled": frozenset({1})},
                      held=frozenset({2}), unrestricted=False)


def _permitted() -> ToolAccess:
    return ToolAccess(required={"stub.labelled": frozenset({1})},
                      held=frozenset({1}), unrestricted=False)


class TestThePlanner:
    def test_a_labelled_tool_declares_no_roles_to_the_queue(self):
        """`_tool_roles` walks agent-as-tool to the depth cap with ONE
        acting principal. A tool the acting user may not call must not
        contribute its role to the admission snapshot either -- otherwise
        the box reserves memory for a model the turn will never use, and
        the enqueue-time plan and the run-time prompt stop being the same
        filtered set.

        THE TWO CELLS DIFFER, which is the whole point: `LABELLED`
        declares a real role, so `set()` here means the key was dropped
        rather than that the stub had nothing to contribute."""
        agent = make_agent(tool_keys=["stub.labelled"])
        assert _tool_roles(agent, OPEN_PRINCIPAL, _refused()) == set()

    def test_the_same_tool_declares_its_role_for_a_holder(self):
        agent = make_agent(tool_keys=["stub.labelled"])
        assert _tool_roles(agent, OPEN_PRINCIPAL, _permitted()) == {CHAT_CONVERSE_ROLE}


class TestTheLoop:
    def test_a_labelled_tool_is_not_offered_to_the_model(self):
        agent = make_agent(tool_keys=["stub.labelled"])
        assert available_tools(OPEN_PRINCIPAL, agent, _refused()) == {}

    def test_a_holder_is_offered_it(self):
        # DEVIATION FROM THE BRIEF (see task-5-report.md): `available_tools`
        # (unlike `_tool_roles`) also requires the tool's own declared
        # role to RESOLVE (`_roles_resolve`) before offering it, so
        # `LABELLED`'s `CHAT_CONVERSE_ROLE` must actually be bound here or
        # this test would fail on role resolution rather than proving the
        # access check -- the same class of gap `bind_chat_role` closes
        # for `TestTheDelegationHop`/`TestPreflight` below.
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(tool_keys=["stub.labelled"])
        assert list(available_tools(OPEN_PRINCIPAL, agent, _permitted())) == ["stub.labelled"]


class TestTheDelegationHop:
    def test_a_delegate_reuses_the_root_access_and_never_rebuilds_one(self, monkeypatch):
        """The whole of the acting rule at a hop: WHO this is for never
        changes, and neither does WHAT they may call. A delegate that
        rebuilt access from its own agent -- or from its own principal --
        would be the way around labels this rule exists to close.

        DEVIATION FROM THE BRIEF'S LITERAL CONSTRUCTION (see task-5-
        report.md): the brief's `ToolContext` left `tool_key` blank and
        `budget`/`job` as `None` with `conversation_id=""`. `run_agent_
        tool` reads the delegate's slug from `ctx.tool_key` (never from
        `args`), and checks `ctx.budget.exhausted` before it ever reaches
        `available_tools` -- so a blank `tool_key` or a `None` budget
        both refuse BEFORE the mocked `available_tools` is ever called,
        which would make the assertions below fail on a bare `KeyError`
        rather than prove anything. `tool_key` is set to name the real
        child agent and `budget`/`job` are real objects (exactly the
        brief's own fallback: "if the real `run_agent_tool` in this tree
        reads [budget/job] earlier, pass the module's own `make_job_
        ctx()` and a `StepBudget`"). `conversation_id=""` is kept
        verbatim -- `available_tools` is computed before the
        conversation-id check (this task's own reordering in
        `delegate.py`, since what a delegate may call never depended on
        the conversation it happens to run in), so the mocked
        `available_tools` is still reached FIRST and the refusal this
        call ends in is still the honest "no conversation to run in"
        one, exactly as the docstring below describes.
        """
        from agents.runtime import delegate as delegate_module
        from agents.runtime.tests._helpers import make_budget

        captured = {}

        def _fake_available_tools(principal, agent, access):
            captured["principal"] = principal
            captured["access"] = access
            return {}

        monkeypatch.setattr(delegate_module, "available_tools", _fake_available_tools)
        access = _refused()
        child = make_agent(slug="child", tool_keys=["stub.labelled"])
        bind_chat_role(CHAT_CONVERSE_ROLE)
        ctx = ToolContext(conversation_id="", principal=OPEN_PRINCIPAL, depth=0,
                          budget=make_budget(), job=make_job_ctx(), agent_slug="parent",
                          tool_key="agent.child", tool_access=access)
        with pytest.raises(Exception):
            # The call fails later (no conversation id), which is fine:
            # `available_tools` is reached FIRST, and what it was handed
            # is the whole assertion.
            delegate_module.run_agent_tool({"agent": child.slug, "task": "go"}, ctx)
        assert captured["access"] is access
        assert captured["principal"] is OPEN_PRINCIPAL


class TestPreflight:
    def test_an_open_box_drops_nothing_at_preflight(self):
        """THE REGRESSION PIN: `preflight_turn` derives its own access
        from `tool_access_for(actor)`, and in `open` posture that is
        unrestricted -- so IA-2 changed nothing here for a household box.
        The non-open sibling lives in `agents/tests/test_entitlements.py`,
        where the posture helper is already imported."""
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(tool_keys=["stub.labelled"])
        check = preflight_turn(agent, None, actor=OPEN_PRINCIPAL)
        assert check.dropped_tools == ()
        assert check.unentitled_tools == ()


class TestVisibleAgentSlugsIsFetchedLazily:
    """T15 review finding 4: `available_tools`/`_tool_roles` used to call
    `visible_agent_slugs` UNCONDITIONALLY, once per hop, even for an
    agent that declares no `agent.*` key at all -- the common case for a
    leaf agent. Both now fetch it at most once, and only once an
    `agent.*` key is actually seen; this pins the zero-query case each
    docstring now claims.
    """

    def test_available_tools_runs_no_query_for_an_agent_with_no_agent_dot_key(
            self, django_assert_num_queries):
        agent = make_agent(tool_keys=[])
        with django_assert_num_queries(0):
            assert available_tools(OPEN_PRINCIPAL, agent) == {}

    def test_tool_roles_runs_no_query_for_an_agent_with_no_agent_dot_key(
            self, django_assert_num_queries):
        agent = make_agent(tool_keys=[])
        with django_assert_num_queries(0):
            assert _tool_roles(agent, OPEN_PRINCIPAL, _permitted()) == set()
