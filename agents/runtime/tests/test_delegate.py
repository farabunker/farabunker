"""Agent-as-tool: a nested loop, in the same job, on the same budget.

The budget is the real guard. The depth cap is enforced by OMISSION --
the model is never offered a tool it would be refused for using -- and
the ToolRefused branch is belt-and-braces for a malformed call.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from agents.contracts.tools import ToolRefused, ToolSpec, register_tool
from agents.limits import MAX_AGENT_DEPTH
from agents.models import ChatSettings, Turn
from agents.resident import agent_tool_specs
from agents.runtime.delegate import run_agent_tool
from agents.runtime.prompt import _NOW_PREFIX
from agents.runtime.tests._helpers import (  # noqa: F401 -- the fixture import
    CALLS, FakeToolLLM, bind_chat_role, bound_chat_role, isolated_tool_registry,  # IS the
    make_agent, make_budget, make_conversation, make_entitlement, make_job_ctx,  # registration
    make_tool_ctx, make_turn, make_user, patch_llm, posture, user_principal,
)
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


@pytest.fixture(autouse=True)
def _bind(db):
    """EVERY test in this module runs a delegate, and every delegate
    resolves its own agent's chat role through `resolve_chat` -- which
    raises when nothing is bound. Binding once, autouse, rather than in
    each test: a per-test call is a line seven of the eight tests below
    would forget, and the failure it produces (`ValueError` from
    `resolve`) reads like a bug in the code under test rather than a
    missing fixture."""
    bind_chat_role(CHAT_CONVERSE_ROLE)


def _clock(*, on: bool) -> None:
    """Set `ChatSettings.time_aware` explicitly.

    Round 21 fix round 1 gave the delegate path the clock line too, so
    this module's message-list assertions now depend on a box-level
    switch -- and a test that depends on one sets it, rather than
    inheriting whatever the shipped default happens to be."""
    ChatSettings.objects.update_or_create(pk=1, defaults={"time_aware": on})


class TestTheSpecs:
    def test_one_spec_per_as_tool_resident_and_no_others(self):
        assert {s.key for s in agent_tool_specs()} == {"agent.library", "agent.illustrator"}

    def test_each_declares_a_required_task_param_and_no_roles(self):
        """Empty `roles` is deliberate (spec section 6.4): a delegate's
        roles are not knowable at registration time, so `plan_turn`
        supplies them by walking the closure."""
        for spec in agent_tool_specs():
            assert spec.roles == ()
            assert [p.key for p in spec.params] == ["task"]
            assert spec.params[0].required is True

    def test_they_all_share_one_runner(self):
        """Which is exactly why `ToolContext.tool_key` exists: a runner's
        signature is (args, ctx), so N specs sharing one runner have no
        other way to learn which one invoked them."""
        assert len({s.runner for s in agent_tool_specs()}) == 1

    def test_none_of_them_mutates(self):
        assert not any(s.mutates for s in agent_tool_specs())


class TestTheRunner:
    def test_it_runs_the_named_agents_loop_and_returns_its_text(self):
        make_agent(slug="library", system_prompt="You are the library.")
        conv = make_conversation(agent=make_agent(slug="general"))
        llm = FakeToolLLM([("final", "the library says yes")])
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with patch_llm(llm):
            result = run_agent_tool({"task": "what does it say?"}, ctx)
        assert result.text == "the library says yes"
        assert result.data["agent"] == "library"

    def test_the_delegate_gets_its_OWN_system_prompt_and_the_task(self):
        """A fresh message list, not the parent's history: a delegate is
        a subroutine with an assignment, not a second participant.

        THE CLOCK IS TURNED OFF FOR THIS ONE, the same discipline
        `agents/runtime/tests/test_prompt.py`'s own `_clock_off` fixture
        follows: this assertion is a byte-identity assertion about WHICH
        messages a delegate gets, and the clock line's text changes every
        minute. `TestTheDelegatesClock`, below, is where the clock is
        turned back on and pinned in both states."""
        _clock(on=False)
        make_agent(slug="library", system_prompt="You are the library.")
        conv = make_conversation(agent=make_agent(slug="general"))
        llm = FakeToolLLM([("final", "x")])
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with patch_llm(llm):
            run_agent_tool({"task": "the assignment"}, ctx)
        contents = [m.content for m in llm.calls[0][0]]
        assert contents == ["You are the library.", "the assignment"]

    def test_the_budget_is_SHARED_not_nested(self):
        """The real guard. A delegate spends from the ROOT turn's
        budget, so total LLM calls per turn stay bounded by
        Agent.max_steps however the delegation tree is shaped."""
        make_agent(slug="library")
        conv = make_conversation(agent=make_agent(slug="general"))
        budget = make_budget(steps=5)
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            budget=budget)
        with patch_llm(FakeToolLLM([("final", "x")])):
            run_agent_tool({"task": "t"}, ctx)
        assert budget.steps_left == 4

    def test_the_delegates_turns_are_written_one_level_deeper(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_agent(slug="library", tool_keys=["stub.safe"])
        conv = make_conversation(agent=make_agent(slug="general"))
        llm = FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library", depth=0)
        with patch_llm(llm):
            run_agent_tool({"task": "t"}, ctx)
        assert conv.turns.get(role=Turn.Role.TOOL).depth == 1

    def test_the_delegates_principal_is_the_callers_not_the_delegate(self):
        """THE ACTING RULE (Task 11), REVERSING THIS TEST'S OWN OLD
        NAME AND ASSERTION. Before Task 11 this test asserted the
        opposite -- that a delegate's audit rows carried a principal
        MINTED FROM THE DELEGATE AGENT (`principal_for(agent)`, now
        deleted). The acting rule says an agent is a tool a person
        wields, not a second person: NO HOP WIDENS THE ACTING PRINCIPAL,
        so the audit trail must say who actually ASKED -- `ctx.
        principal`, unchanged -- not which agent's tool declaration
        happened to be in force. `agent_slug` (Task 10) is the field
        that still tells the story of the WRONG kind of fact this test
        used to hang its intent on -- see
        `test_the_delegates_agent_slug_names_the_delegate_not_the_caller`
        for that half."""
        from agents.models import ToolInvocation

        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_agent(slug="library", tool_keys=["stub.safe"], resident=True)
        conv = make_conversation(agent=make_agent(slug="general"))
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with patch_llm(FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])):
            run_agent_tool({"task": "t"}, ctx)
        invocation = ToolInvocation.objects.get()
        assert invocation.principal_key == ctx.principal.key
        assert invocation.principal_kind == ctx.principal.kind

    def test_the_delegates_agent_slug_names_the_delegate_not_the_caller(self):
        """The OTHER half of the acting rule's pair (Task 10/11):
        `agent_slug` is WHOSE TOOL DECLARATION IS IN FORCE, and that
        DOES change at a delegation hop, even though `principal` (above)
        never does.

        Asserted at TWO seams, because they are two different things
        that happen to agree here: `ToolContext.agent_slug` itself
        (`run_loop`'s own stamp, read back via `runner_ok`'s `CALLS`
        recording, the ONLY way to see the live context a runner was
        actually called with) and `Turn.tool_call["agent"]` (`loop.py`'s
        own call-record field, independently built from the SAME
        `agent.slug` at `loop.py:337`). Today the audit trail an
        operator reads (`ToolInvocation`) carries neither -- only
        `principal`, per the acting rule -- so `Turn.tool_call["agent"]`
        is where "which agent made this call" survives to be read at
        all, until IA-2 gives `ToolInvocation` its own `agent_slug`
        column.
        """
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_agent(slug="library", tool_keys=["stub.safe"], resident=True)
        conv = make_conversation(agent=make_agent(slug="general"))
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with patch_llm(FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])):
            run_agent_tool({"task": "t"}, ctx)
        tool_turn = conv.turns.get(role=Turn.Role.TOOL)
        assert tool_turn.tool_call["agent"] == "library"
        _args, captured_ctx = CALLS[-1]
        assert captured_ctx.agent_slug == "library"

    def test_a_tool_incapable_model_gets_the_honest_answer_without_spending_the_budget(
        self, monkeypatch,
    ):
        """RULING (Task 11 review, IMPORTANT 4): the SAME "cannot call
        tools" gate the root loop applies (section 10.1) applies to a
        delegate too -- checked, and answered, BEFORE `run_loop` even
        starts, so a tool-incapable model never spends so much as one
        step of the shared budget on a blind loop it cannot use tools
        in. `monkeypatch.setattr` runs BEFORE `patch_llm` is entered,
        matching `patch_llm`'s own docstring: it only installs the
        `None` default when nothing has already overridden
        `supports_tool_calling`."""
        from agents.runtime import loop as loop_module

        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_agent(slug="library", tool_keys=["stub.safe"])
        conv = make_conversation(agent=make_agent(slug="general"))
        budget = make_budget(steps=5)
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            budget=budget)
        monkeypatch.setattr("agents.runtime.loop.supports_tool_calling", lambda r: False)
        llm = FakeToolLLM([])   # never called -- proof no blind loop was attempted
        with patch_llm(llm):
            result = run_agent_tool({"task": "t"}, ctx)
        assert result.text == loop_module.NO_TOOL_CALLING_NOTE
        assert result.data["steps_used"] == 0
        assert budget.steps_left == 5
        assert llm.calls == []


class TestTheDelegatesOwnTimeout:
    """B2 (one-timeout task, fix round 1): a delegate spends from the
    ROOT turn's SHARED budget (`ctx.job` is the root's own `JobContext`),
    so its own chat client must be built with the SAME `request_timeout`
    -- via `agents.runtime.loop.resolved_turn_timeout`, the identical
    resolver the root loop itself calls -- or it keeps the engine's
    hidden 300s default while spending a budget that may now run up to
    7200s. Mirrors `agents/runtime/tests/test_loop.py::
    test_the_llm_is_built_with_the_turns_own_response_timeout` on the
    delegate side."""

    def test_the_delegates_llm_is_built_with_the_shared_jobs_response_timeout(self):
        make_agent(slug="library")
        conv = make_conversation(agent=make_agent(slug="general"))
        job_ctx = make_job_ctx(response_timeout_seconds=123.0)
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            job=job_ctx)
        llm = FakeToolLLM([("final", "x")])

        with (
            patch("models.contracts.gateway.get_llm_for", return_value=llm) as mock_get_llm_for,
            patch("agents.runtime.loop.supports_tool_calling", return_value=None),
        ):
            run_agent_tool({"task": "t"}, ctx)

        _, kwargs = mock_get_llm_for.call_args
        assert kwargs["request_timeout"] == 123.0

    def test_none_falls_back_to_turn_deadline_seconds(self):
        from agents.limits import TURN_DEADLINE_SECONDS

        make_agent(slug="library")
        conv = make_conversation(agent=make_agent(slug="general"))
        job_ctx = make_job_ctx(response_timeout_seconds=None)
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            job=job_ctx)
        llm = FakeToolLLM([("final", "x")])

        with (
            patch("models.contracts.gateway.get_llm_for", return_value=llm) as mock_get_llm_for,
            patch("agents.runtime.loop.supports_tool_calling", return_value=None),
        ):
            run_agent_tool({"task": "t"}, ctx)

        _, kwargs = mock_get_llm_for.call_args
        assert kwargs["request_timeout"] == TURN_DEADLINE_SECONDS

    def test_a_degenerate_timeout_is_floored_at_the_delegate_call_site(self):
        """Final review MINOR 3 (fix round 3): the delegate's own call site
        mirrors the root loop's -- a degenerate stamped value is floored
        before it reaches the engine client."""
        from agents.limits import RESOLVED_TIMEOUT_FLOOR_SECONDS

        make_agent(slug="library")
        conv = make_conversation(agent=make_agent(slug="general"))
        job_ctx = make_job_ctx(response_timeout_seconds=0.0)
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            job=job_ctx)
        llm = FakeToolLLM([("final", "x")])

        with (
            patch("models.contracts.gateway.get_llm_for", return_value=llm) as mock_get_llm_for,
            patch("agents.runtime.loop.supports_tool_calling", return_value=None),
        ):
            run_agent_tool({"task": "t"}, ctx)

        _, kwargs = mock_get_llm_for.call_args
        assert kwargs["request_timeout"] == RESOLVED_TIMEOUT_FLOOR_SECONDS


class TestHistoryIsolation:
    def test_a_delegates_turns_never_replay_into_the_parents_prompt(self):
        """M6. A delegate's turns live in this same conversation at
        depth >= 1 so an operator can audit exactly what ran -- but they
        are NOT the parent's history. Replaying them would hand the
        parent model the inside of a subroutine it only ever saw the
        return value of, with tool-call blocks naming tools the parent
        may not even hold."""
        from agents.runtime.prompt import history_messages

        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_agent(slug="library", tool_keys=["stub.safe"])
        conv = make_conversation(agent=make_agent(slug="general"))
        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask")
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with patch_llm(FakeToolLLM([("tool", "stub.safe", {}), ("final", "x")])):
            run_agent_tool({"task": "t"}, ctx)


        assert conv.turns.filter(depth=1).exists()          # they WERE written
        assert [m.content for m in history_messages(conv)] == ["ask"]


class TestTheGuards:
    def test_at_the_depth_cap_the_agent_tools_are_OMITTED_from_the_delegate(self):
        """Enforcement by omission. The model is never offered a tool it
        will be refused for using."""
        for spec in agent_tool_specs():
            register_tool(spec)
        make_agent(slug="library", tool_keys=["agent.illustrator"])
        make_agent(slug="illustrator")
        conv = make_conversation(agent=make_agent(slug="general"))
        llm = FakeToolLLM([("final", "x")])
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            depth=MAX_AGENT_DEPTH - 1)
        with patch_llm(llm):
            run_agent_tool({"task": "t"}, ctx)
        assert llm.calls[0][1] is None

    def test_at_the_depth_cap_a_direct_call_is_REFUSED(self):
        make_agent(slug="library")
        conv = make_conversation(agent=make_agent(slug="general"))
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            depth=MAX_AGENT_DEPTH)
        with pytest.raises(ToolRefused):
            run_agent_tool({"task": "t"}, ctx)

    def test_an_unknown_or_disabled_agent_is_REFUSED_not_errored(self):
        """Nothing the model can say makes a disabled agent run, so this
        gets no retry (section 10.1)."""
        make_agent(slug="library", enabled=False)
        conv = make_conversation(agent=make_agent(slug="general"))
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with pytest.raises(ToolRefused):
            run_agent_tool({"task": "t"}, ctx)

    def test_an_exhausted_budget_refuses_before_starting_a_nested_loop(self):
        make_agent(slug="library")
        conv = make_conversation(agent=make_agent(slug="general"))
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            budget=make_budget(steps=0))
        with pytest.raises(ToolRefused):
            run_agent_tool({"task": "t"}, ctx)

    def test_a_blank_tool_key_refuses_rather_than_guessing(self):
        """`ctx.tool_key` is set by `invoke_tool`. A blank one means this
        runner was called some other way, and guessing which agent was
        meant would run the wrong one."""
        conv = make_conversation()
        with pytest.raises(ToolRefused):
            run_agent_tool({"task": "t"}, make_tool_ctx(conversation_id=str(conv.id)))

    def test_a_blank_conversation_id_refuses_rather_than_erroring(self):
        """A blank `ctx.conversation_id` reaching `Conversation.objects.
        get(id="")` would raise `Conversation.DoesNotExist`, classified
        as an unclassified `error` -- nothing the model can say supplies
        a conversation this runner was never given, so this is
        refusal-class like the blank-tool-key and disabled-agent cases
        beside it."""
        make_agent(slug="library")
        ctx = make_tool_ctx(conversation_id="", tool_key="agent.library")
        with pytest.raises(ToolRefused):
            run_agent_tool({"task": "t"}, ctx)

    def test_a_delegation_cycle_terminates_on_the_shared_budget(self):
        """Two agents naming each other. The depth cap stops the tree and
        the shared budget stops the total -- both, not either."""
        for spec in agent_tool_specs():
            register_tool(spec)
        make_agent(slug="library", tool_keys=["agent.illustrator"])
        make_agent(slug="illustrator", tool_keys=["agent.library"])
        conv = make_conversation(agent=make_agent(slug="general"))
        budget = make_budget(steps=6)
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library",
                            budget=budget, depth=0)
        script = [("tool", "agent.illustrator", {"task": "t"})] * 10
        with patch_llm(FakeToolLLM(script)):
            run_agent_tool({"task": "t"}, ctx)
        assert budget.steps_left <= 0        # terminated, and on the shared budget

    def test_an_unbound_delegate_role_is_REFUSED_and_dropped_for_the_rest_of_the_turn(self):
        """RULING (Task 11 review, IMPORTANT 3): an unbound delegate chat
        role is refusal-class, exactly like `tools.rag.tools.run_ask`'s
        and `tools.vision.tools.run_generate`'s own unbound-role ->
        `ToolRefused` translations -- nothing the model can say fixes it.

        Driven through `run_loop`, not `run_agent_tool` directly, so the
        drop-on-refusal mechanism (`agents.runtime.loop`'s
        `available.pop(key, None)` on a `bars_retry` outcome) is actually
        proven: the SECOND attempt at the same tool name reaches
        `invoke_unknown_tool` instead of `run_agent_tool` again, because
        `agent.library` is no longer in `available` after the first
        refusal.
        """
        from agents.contracts.tools import Principal
        from agents.models import ToolInvocation
        from agents.runtime.loop import run_loop

        for spec in agent_tool_specs():
            register_tool(spec)
        # No role binding for this string anywhere -- `resolve()` raises
        # `ValueError`, which `run_agent_tool` translates to `ToolRefused`.
        make_agent(slug="library", llm_role="an-unbound-role-nobody-bound")
        general = make_agent(slug="general", tool_keys=["agent.library"])
        conv = make_conversation(agent=general)
        script = [
            ("tool", "agent.library", {"task": "t"}),
            ("tool", "agent.library", {"task": "t"}),
            ("final", "done"),
        ]
        result = run_loop(
            agent=general,
            conversation=conv,
            messages=[],
            llm=FakeToolLLM(script),
            budget=make_budget(steps=5),
            principal=Principal(kind="resident_agent", key="general"),
            job_ctx=make_job_ctx(),
            depth=0,
            available={s.key: s for s in agent_tool_specs()},
        )
        # TWO invocation rows, not one call refused twice: the first is
        # `run_agent_tool` itself refusing; the second is
        # `invoke_unknown_tool` finding no tool called "agent.library" at
        # all -- which is only possible because the first refusal
        # already popped it out of `available`. A delegate that was NOT
        # dropped would show REFUSED twice, not REFUSED then ERROR.
        invocations = list(ToolInvocation.objects.filter(tool_key="agent.library").order_by("id"))
        assert len(invocations) == 2
        assert invocations[0].outcome == ToolInvocation.Outcome.REFUSED
        assert invocations[1].outcome == ToolInvocation.Outcome.ERROR
        # `.error`, not `.text`: `invoke.py::_finish` leaves `.text` blank
        # on a non-OK/DEGRADED outcome and records the reason in `.error`.
        assert "no tool called" in invocations[1].error.lower()
        assert "Two tool calls failed" in result.text


class TestFullIntegrationThroughRunTurn:
    def test_run_turn_drives_the_parent_loop_through_invoke_tool_into_a_real_delegate(self):
        """MINOR (f), Task 11 review: one end-to-end test through the
        REAL registered `agent.library` spec -- `run_turn` (the
        `agent.turn` job handler) -> its own `run_loop` -> `invoke_tool`
        (dotted-path resolution of
        `agents.runtime.delegate.run_agent_tool`) -> a SECOND, nested
        `run_loop` for the delegate itself. Every other test in this
        module calls `run_agent_tool` or `run_loop` directly; this one
        proves the whole chain the way a real turn actually runs it,
        with tool calls scripted at BOTH levels on the ONE shared
        `FakeToolLLM` -- `patch_llm` patches `get_llm_for` for every
        resolution, root and delegate alike, so one script queue serves
        calls from both loops in the order they actually happen.
        """
        from agents.models import ToolInvocation
        from agents.runtime.loop import run_turn

        for spec in agent_tool_specs():
            register_tool(spec)
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_agent(slug="library", tool_keys=["stub.safe"],
                   system_prompt="You are the library.")
        general = make_agent(slug="general", tool_keys=["agent.library"], max_steps=8)
        conv = make_conversation(agent=general)
        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask the library")
        assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                              state=Turn.State.QUEUED)
        payload = {
            "conversation": str(conv.id), "turn": assistant.pk, "agent": general.slug,
            "text": "ask the library", "connection": None, "mode": "chat",
            "actor_kind": "user", "actor_key": "7",
        }
        script = [
            ("tool", "agent.library", {"task": "what does the library say?"}),  # root
            ("tool", "stub.safe", {}),                                          # delegate
            ("final", "the library found it"),                                 # delegate
            ("final", "here is your answer"),                                  # root
        ]
        with patch_llm(FakeToolLLM(script)):
            result = run_turn(payload, [], make_job_ctx())

        assert result["text"] == "here is your answer"
        # ALL FOUR script entries spent from the ONE shared budget -- the
        # root's own two steps AND the delegate's two, because the
        # budget is shared, not nested (property 1).
        assert result["steps_used"] == 4
        assistant.refresh_from_db()
        assert assistant.text == "here is your answer"
        assert assistant.state == Turn.State.DONE

        # The root's own call to "agent.library" lands at depth 0 (the
        # root turn's own depth); the delegate's own call to "stub.safe"
        # lands one level deeper (M6's depth>=1 audit trail).
        assert conv.turns.get(role=Turn.Role.TOOL, tool_call__tool="agent.library").depth == 0
        assert conv.turns.get(role=Turn.Role.TOOL, tool_call__tool="stub.safe").depth == 1

        # THE ACTING RULE (Task 11): BOTH invocations -- the root's own
        # call to `agent.library` and the delegate's nested call to
        # `stub.safe` -- carry the SAME principal, the payload's actor.
        # Before Task 11 these two rows disagreed (`"general"` for the
        # root call, `"library"` for the delegate's, each minted from
        # the calling agent's own slug by the now-deleted
        # `principal_for`) -- exactly the widening-at-a-hop the acting
        # rule forecloses.
        invocations = {inv.tool_key: inv for inv in ToolInvocation.objects.all()}
        assert set(invocations) == {"agent.library", "stub.safe"}
        assert invocations["agent.library"].principal_key == "7"
        assert invocations["agent.library"].principal_kind == "user"
        assert invocations["stub.safe"].principal_key == "7"
        assert invocations["stub.safe"].principal_kind == "user"


class TestDelegationCannotReachARestrictedAgent:
    def test_run_agent_tool_refuses_an_agent_the_actor_may_not_see(self):
        """The acting rule reaches agents themselves, not only their
        tools: `run_agent_tool` resolved `Agent.objects` directly, so a
        restricted agent was one delegation away from anybody."""
        from agents.contracts.tools import ToolRefused
        from agents.models import AgentEntitlement
        child = make_agent(slug="child", enabled=True)
        AgentEntitlement.objects.create(agent=child,
                                        entitlement=make_entitlement(name="Legal"))
        # `tool_key`, not `args["agent"]`, is how `run_agent_tool` learns
        # which agent it was asked to run -- see its own first line.
        ctx = make_tool_ctx(tool_key="agent.child", principal=user_principal(make_user()))
        with posture(POSTURE_ENTERPRISE), pytest.raises(ToolRefused):
            run_agent_tool({"task": "go"}, ctx)


class TestTheDelegatesClock:
    """ROUND 21 FIX ROUND 1 (I1). The owner's word was "always" -- and a
    delegate produces text a user reads, so a time-blind delegate
    reproduces the original symptom ("2026 must be a future date")
    through the one path nobody was watching.

    THE CLOCK IS THE ONE THING A DELEGATE INHERITS. Every other
    exemption on this path (the stream's instructions prose, the
    attachments block, the parent's history) is about the PARENT'S
    context -- prose it was not addressed by, files it was not handed.
    What day it is is not the parent's context; it is a fact about the
    world this agent is answering in.

    ONE HELPER, ONE TOGGLE: both builders call
    `agents.runtime.prompt.time_aware_now_line`, so the wording, the
    timezone rule and the operator's switch cannot drift between them.
    """

    def _run(self, *, system_prompt="You are the library."):
        make_agent(slug="library", system_prompt=system_prompt)
        conv = make_conversation(agent=make_agent(slug="general"))
        llm = FakeToolLLM([("final", "x")])
        ctx = make_tool_ctx(conversation_id=str(conv.id), tool_key="agent.library")
        with patch_llm(llm):
            run_agent_tool({"task": "what year is it?"}, ctx)
        return [m.content for m in llm.calls[0][0]]

    def test_a_delegate_is_told_the_date(self):
        _clock(on=True)
        contents = self._run()
        assert _NOW_PREFIX in contents[0]
        now = timezone.localtime()
        assert now.strftime("%A") in contents[0]
        assert now.strftime("%d %B %Y") in contents[0]

    def test_the_line_is_the_last_paragraph_of_the_delegates_system_message(self):
        """The same placement `build_messages` gives it: after the
        agent's own prompt, its own paragraph, never glued onto the end
        of the agent's last sentence."""
        _clock(on=True)
        contents = self._run()
        assert contents[0].startswith("You are the library.")
        assert contents[0].split("\n\n")[-1].startswith(_NOW_PREFIX)

    def test_a_delegate_with_a_blank_prompt_still_gets_a_system_message_for_it(self):
        """The "something to say" rule, on this path too: the date IS
        something to say, so a delegate whose agent has no prompt of its
        own still gets one system message carrying the date alone."""
        _clock(on=True)
        contents = self._run(system_prompt="")
        assert contents[0].startswith(_NOW_PREFIX)
        assert contents[1] == "what year is it?"

    def test_the_operators_toggle_governs_this_path_too(self):
        """ONE SWITCH, EVERYWHERE. With it off a delegate's prompt is
        exactly what it was before this fix -- its own system prompt and
        the task, nothing else."""
        _clock(on=False)
        assert self._run() == ["You are the library.", "what year is it?"]

    def test_a_delegate_with_a_blank_prompt_and_the_clock_off_gets_no_system_message(self):
        """The other direction of the "something to say" rule: nothing
        to say, nothing said -- an empty system message is a message,
        and this path must not start emitting one."""
        _clock(on=False)
        assert self._run(system_prompt="") == ["what year is it?"]

    def test_both_builders_read_the_same_helper(self):
        """THE ANTI-DRIFT PIN, and the reason this fix threaded a shared
        function rather than re-typing four lines here: patching the ONE
        helper changes what BOTH paths say. If a future edit re-inlines
        `_now_line()` on either side, this fails."""
        _clock(on=True)
        import agents.runtime.prompt as prompt_module

        original = prompt_module.time_aware_now_line
        prompt_module.time_aware_now_line = lambda: "CLOCK-SENTINEL"
        try:
            contents = self._run()
        finally:
            prompt_module.time_aware_now_line = original
        assert contents[0] == "You are the library.\n\nCLOCK-SENTINEL"
