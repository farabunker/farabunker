"""C-2 (security round 3, H31): a flow step is not a bypass around the
tool-entitlement question every other caller asks.

`agents.runtime.flow._run_steps` already re-implemented ONE of the two
filters `agents.contracts.tools.granted_tools` applies to an agent's own
keys -- it refuses a `mutates=True` step (see the "LOAD-BEARING" comment
beside that guard) -- but never asked `ctx.tool_access` whether a step's
tool was ENTITLED at all. `agents/runtime/delegate.py:124`'s hop already
re-applies `ctx.tool_access` (see `agents/runtime/tests/
test_tool_access.py::TestTheDelegationHop`); a flow step walked past it.

The fix lives in `agents.runtime.invoke.invoke_tool` itself, immediately
after the audit row is created and BEFORE the `try` that wraps
`validate_tool_args` -- so no third caller (a flow, a future MCP edge)
can forget to ask. This module proves that from the OUTSIDE: a step
naming a tool the acting principal does not hold ends the flow exactly
the way any other refused step does (`_run_steps`'s existing
`bars_retry` re-raise, already pinned by `test_flow.py::
test_a_refused_step_re_raises_tool_refused_no_retry`), and `invoke_tool`
itself RETURNS a refused outcome rather than raising one -- the
"returns, never raises" contract `agents/runtime/invoke.py:20-30`
documents and that `ToolRefused` being a `ValueError` subclass
(`agents/contracts/tools.py:56`) makes load-bearing: a `raise` placed
inside `invoke_tool`'s own `try` would be silently swallowed by its own
classification chain.

`TestTheShippedFlowEndToEnd` (review round 1, finding 1) drives the same
proof through the actual shipped `library-brief` default and real
`rag.search`/`rag.ask` keys -- the harness shape `agents/tests/
test_defaults.py:436-481`'s own `TestLibraryBriefFlow` already uses to
run `flow.run` end to end -- rather than only the bespoke
`flow-test.entitled` stub the other classes below use for their
unit-level proofs.

New module (not appended to `test_flow.py`), per this task's own
placement ruling: a fixture-light, single-purpose module for one finding
is easier to review in isolation than a further-grown shared file.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolAccess, ToolRefused, get_tool, register_tool, ToolSpec
from agents.models import Flow, ToolEntitlement, ToolInvocation
from agents.runtime.flow import _run_steps
from agents.runtime.invoke import invoke_tool
from agents.runtime.tests._helpers import (  # noqa: F401 -- the import IS the registration
    CALLS, isolated_tool_registry, make_agent, make_tool_ctx,
)
from models.contracts.operations import Param

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

_KEY = "flow-test.entitled"


def _register_entitled() -> ToolSpec:
    spec = ToolSpec(
        key=_KEY, label="An entitled stub",
        description="A stub used to prove the flow entitlement check.",
        params=(), runner="agents.runtime.tests._helpers.runner_ok",
    )
    register_tool(spec)
    return spec


def _make_flow(**overrides) -> Flow:
    fields = dict(
        slug="flow-test-c2",
        name="Test flow",
        description="",
        inputs=[],
        steps=[{"tool": _KEY, "args": {}}],
        resident=False,
        enabled=True,
    )
    fields.update(overrides)
    return Flow.objects.create(**fields)


def _refused() -> ToolAccess:
    """Access that labels `_KEY` with an entitlement the acting
    principal does not hold -- the same construction
    `agents/runtime/tests/test_tool_access.py::_refused` uses for the
    same purpose at the other call sites."""
    return ToolAccess(required={_KEY: frozenset({1})},
                      held=frozenset({2}), unrestricted=False)


def _permitted() -> ToolAccess:
    return ToolAccess(required={_KEY: frozenset({1})},
                      held=frozenset({1}), unrestricted=False)


def _install_library_brief_with_stub_rag() -> None:
    """The harness shape `agents/tests/test_defaults.py:436-481`'s
    `TestLibraryBriefFlow` uses to drive `flow.run` end to end: register
    `FLOW_RUN` and stub `rag.search`/`rag.ask` runners (real keys, not a
    bespoke stand-in), then install the shipped `library-brief` default.

    `resident=True` (every shipped default, `agents.defaults.
    install_default`) makes the installed row visible to ANY principal
    through `agents.visibility.visible_flows`, regardless of who
    installed it -- so `OPEN_PRINCIPAL` here is only the INSTALLING
    principal, never necessarily the one a test later calls `flow.run`
    as.
    """
    from agents.defaults import install_default
    from agents.runtime.flowtool import FLOW_RUN
    from identity.contracts.principals import OPEN_PRINCIPAL

    register_tool(FLOW_RUN)
    for key, required_key in (("rag.search", "query"), ("rag.ask", "question")):
        register_tool(ToolSpec(
            key=key, label=key, description="stub",
            params=(
                Param(required_key, "text", required_key, required=True),
                Param("category", "text", "Category"),
            ),
            runner="agents.runtime.tests._helpers.runner_ok",
        ))
    install_default("flow", "library-brief", OPEN_PRINCIPAL)


class TestInvokeToolItselfReturnsRefusedNeverRaises:
    """Report §6's own reason for moving the check here: the access
    object is already on the context, so asking it at the seam every
    caller passes through is what stops the next caller forgetting."""

    def test_an_unheld_tool_is_refused(self):
        spec = _register_entitled()
        tool_ctx = make_tool_ctx(tool_access=_refused())

        outcome = invoke_tool(spec, {}, tool_ctx)

        assert outcome.outcome == ToolInvocation.Outcome.REFUSED
        assert _KEY in outcome.text

    def test_a_held_tool_still_runs(self):
        spec = _register_entitled()
        tool_ctx = make_tool_ctx(tool_access=_permitted())

        outcome = invoke_tool(spec, {}, tool_ctx)

        assert outcome.outcome == ToolInvocation.Outcome.OK

    def test_the_refusal_is_audited_like_every_other_one(self):
        """It goes through `_finish` on the row created at
        `invoke.py:97`, so a refused call is as findable as a
        successful one -- never a call that skipped the audit trail."""
        spec = _register_entitled()
        tool_ctx = make_tool_ctx(tool_access=_refused())

        outcome = invoke_tool(spec, {}, tool_ctx)

        row = ToolInvocation.objects.get(pk=outcome.invocation_id)
        assert row.outcome == ToolInvocation.Outcome.REFUSED
        assert row.tool_key == _KEY

    def test_the_refused_row_records_the_attempted_args(self):
        """Review round 1, finding 2: the SAME idiom the `ParamError`
        branch uses (`row.args = args if isinstance(args, dict) else
        {}`, before its own `_finish`) -- an operator reading a refused
        row should see WHAT was asked for, not just that it was
        refused. `row.args` still holds the creation-time `{}` until
        something sets it, since this check runs before
        `validate_tool_args` ever sees `args`."""
        spec = _register_entitled()
        tool_ctx = make_tool_ctx(tool_access=_refused())

        outcome = invoke_tool(spec, {"unexpected": "value"}, tool_ctx)

        row = ToolInvocation.objects.get(pk=outcome.invocation_id)
        assert row.args == {"unexpected": "value"}
        assert outcome.args == {"unexpected": "value"}

    def test_the_runner_never_ran(self):
        """The check is BEFORE the `try`, so an unheld tool's runner is
        never entered at all -- the difference between a refusal and a
        failure, and the reason `CALLS` (which `runner_ok` appends to)
        must stay untouched."""
        spec = _register_entitled()
        tool_ctx = make_tool_ctx(tool_access=_refused())
        before = len(CALLS)

        invoke_tool(spec, {}, tool_ctx)

        assert len(CALLS) == before

    def test_the_guard_precedes_validate_tool_args_for_a_spec_with_a_required_param(self):
        """Review round 1, finding 4 (renamed from `test_run_flow_
        offers_a_real_choice_param_and_still_refuses`, which pinned this
        but not the flow -- moved into this class, which is where the
        property it actually proves belongs). A real `ToolSpec` carrying
        a declared, REQUIRED param is still refused before
        `validate_tool_args` ever runs -- the guard does not depend on
        the caller sending no arguments at all."""
        spec = ToolSpec(
            key="flow-test.entitled-with-args", label="entitled, with args",
            description="a flow-test stub",
            params=(Param("topic", "text", "Topic", required=True),),
            runner="agents.runtime.tests._helpers.runner_ok",
        )
        register_tool(spec)
        tool_ctx = make_tool_ctx(tool_access=ToolAccess(
            required={"flow-test.entitled-with-args": frozenset({1})},
            held=frozenset(), unrestricted=False,
        ))

        outcome = invoke_tool(spec, {"topic": "x"}, tool_ctx)

        assert outcome.outcome == ToolInvocation.Outcome.REFUSED


class TestAFlowStepIsNotABypass:
    """The finding itself: a flow's step loop never asked `ctx.
    tool_access` at all. It still doesn't -- the question moved to
    `invoke_tool`, and a flow step goes through `invoke_tool` like every
    other caller, so it inherits the answer without a second check."""

    def test_a_step_naming_an_unheld_tool_ends_the_flow(self):
        """`_run_steps` treats a refused step exactly like any other
        refused step (see `test_flow.py::
        test_a_refused_step_re_raises_tool_refused_no_retry`): `bars_
        retry` is `True` for a `REFUSED` outcome, so the flow re-raises
        `ToolRefused` naming the step -- no retry, same as every other
        refusal a flow can hit."""
        _register_entitled()
        flow = _make_flow()
        ctx = make_tool_ctx(tool_access=_refused())

        with pytest.raises(ToolRefused) as exc:
            _run_steps(flow, {}, ctx)

        assert _KEY in str(exc.value)

    def test_a_step_the_principal_does_hold_still_runs(self):
        _register_entitled()
        flow = _make_flow()
        ctx = make_tool_ctx(tool_access=_permitted())

        result = _run_steps(flow, {}, ctx)

        assert result.text == "it worked"

    def test_the_walls_tool_half_survives_a_flow(self):
        """Inside a walled workstream, `agents.entitlements.
        tool_access_for` narrows `held` to the INTERSECTION of the
        acting principal's own entitlements with the wall (see that
        module's own docstring: "narrows the HELD half by INTERSECTION
        and never by union"). A principal who holds the entitlement
        everywhere ELSE but whose wall excludes it is exactly the shape
        that intersection produces -- and a step must not walk past
        that any more than past a flat refusal.

        UNIT-LEVEL: the `ToolAccess` below is built BY HAND to the same
        shape `tool_access_for`'s own `(held & wall) if wall else held`
        line produces, so this proves a flow respects whatever access
        object it is handed without needing a real `Workstream` row.
        `TestTheShippedFlowEndToEnd::
        test_the_shipped_flow_is_refused_when_a_real_wall_excludes_rag`
        below is the sibling that builds the SAME shape through the
        real composition instead (review round 1, finding 1)."""
        _register_entitled()
        flow = _make_flow()
        # `held={1}` is what the principal holds account-wide; the wall
        # would have narrowed it to `frozenset()` before this `ToolAccess`
        # ever reached the flow.
        walled = ToolAccess(required={_KEY: frozenset({1})},
                            held=frozenset(), unrestricted=False)
        ctx = make_tool_ctx(tool_access=walled)

        with pytest.raises(ToolRefused) as exc:
            _run_steps(flow, {}, ctx)

        assert _KEY in str(exc.value)

    def test_the_mutating_guard_is_untouched(self):
        """The existing refusal keeps its own sentence -- two guards,
        two reasons, two messages. An unrestricted `tool_access` (so the
        entitlement question is not what fires here) still refuses a
        `mutates=True` step with the ORIGINAL "changes state that
        already exists" wording, not the entitlement message."""
        register_tool(ToolSpec(
            key="flow-test.mutator-c2", label="mutator",
            description="a flow-test stub",
            runner="agents.runtime.tests._helpers.runner_ok", mutates=True,
        ))
        flow = _make_flow(steps=[{"tool": "flow-test.mutator-c2", "args": {}}])
        ctx = make_tool_ctx()  # UNRESTRICTED_TOOL_ACCESS by default

        with pytest.raises(ToolRefused) as exc:
            _run_steps(flow, {}, ctx)

        assert "changes state that already exists" in str(exc.value)
        assert "not available to you on this box" not in str(exc.value)


class TestTheShippedFlowEndToEnd:
    """Review round 1, finding 1: the same proof, driven through the
    SHIPPED `library-brief` flow (`agents/defaults.py::DEFAULT_FLOWS`)
    and real `rag.search`/`rag.ask` keys, entered through
    `invoke_tool(get_tool("flow.run"), ...)` exactly the way a model's
    own tool call would reach it -- not `_run_steps` called directly, as
    the unit-level tests above do."""

    def test_the_shipped_flow_is_refused_end_to_end_without_rag_entitlements(self):
        _install_library_brief_with_stub_rag()
        access = ToolAccess(
            required={"rag.search": frozenset({1}), "rag.ask": frozenset({1})},
            held=frozenset(), unrestricted=False,
        )
        tool_ctx = make_tool_ctx(tool_access=access)

        outcome = invoke_tool(
            get_tool("flow.run"),
            {"flow": "library-brief", "input": '{"topic": "attention"}'},
            tool_ctx,
        )

        assert outcome.outcome == ToolInvocation.Outcome.REFUSED
        assert "rag.search" in outcome.text

    def test_the_shipped_flow_is_refused_when_a_real_wall_excludes_rag(self):
        """The wall's tool half, exercised through the REAL composition
        `agents.entitlements.tool_access_for(principal, wall=wall_for(
        conversation))` builds for a genuinely contained conversation --
        not the hand-built `ToolAccess` standing in for it in
        `TestAFlowStepIsNotABypass::
        test_the_walls_tool_half_survives_a_flow` above. The principal
        holds `rag_entitlement` ACCOUNT-WIDE (`grant`); the workstream's
        own `WorkstreamScopeEntitlement` names a DIFFERENT entitlement,
        so `tool_access_for`'s `held & wall` intersection excludes the
        one the two `rag.*` keys are actually labelled with -- the
        honest shape a contained conversation that was never given the
        rag entitlement produces."""
        from agents.entitlements import tool_access_for, wall_for
        from agents.models import Conversation, WorkstreamScopeEntitlement
        from agents.tests._helpers import _workstream
        from identity.testing import grant, make_entitlement, make_user, posture, user_principal

        _install_library_brief_with_stub_rag()
        rag_entitlement = make_entitlement()
        other_entitlement = make_entitlement()
        ToolEntitlement.objects.create(tool_key="rag.search", entitlement=rag_entitlement)
        ToolEntitlement.objects.create(tool_key="rag.ask", entitlement=rag_entitlement)

        user = make_user()
        principal = user_principal(user)
        grant(rag_entitlement, user=user)
        stream = _workstream(principal)
        WorkstreamScopeEntitlement.objects.create(
            workstream=stream, entitlement=other_entitlement)
        agent = make_agent()
        conversation = Conversation.objects.create(agent=agent, workstream=stream)

        with posture("enterprise"):
            wall = wall_for(conversation)
            access = tool_access_for(principal, wall=wall)
            tool_ctx = make_tool_ctx(
                principal=principal, tool_access=access,
                conversation_id=str(conversation.id),
            )

            outcome = invoke_tool(
                get_tool("flow.run"),
                {"flow": "library-brief", "input": '{"topic": "attention"}'},
                tool_ctx,
            )

        assert outcome.outcome == ToolInvocation.Outcome.REFUSED
        assert "rag.search" in outcome.text
