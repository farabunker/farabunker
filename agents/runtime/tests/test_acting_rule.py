"""The acting rule, end to end.

A turn runs as the USER. A delegate carries the SAME principal and a
DIFFERENT `agent_slug`. There is no hop at which the acting principal
widens -- which is the whole of "an agent is never a way around
labels".

`runner_capture` below is a MODULE-LEVEL runner, referenced by its
dotted path exactly as every other test runner in this package is
(`agents.runtime.tests._helpers.runner_ok`): a `ToolSpec.runner` is a
string resolved at call time, so a closure defined inside a test would
never be reachable. It appends the live `ToolContext` to `CAPTURED`,
which is the only way to see what the loop actually built.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolResult, ToolSpec, register_tool
from agents.models import ToolEntitlement, Turn
from agents.runtime.loop import run_turn
from agents.runtime.tests._helpers import (  # noqa: F401 -- the import IS the registration
    FakeToolLLM, bind_chat_role, isolated_tool_registry, make_agent, make_conversation,
    make_job_ctx, make_turn, patch_llm,
)
from agents.tests._helpers import grant, make_entitlement, make_user, posture, user_principal
from agents.resident import agent_tool_specs
from identity.contracts.postures import POSTURE_ENTERPRISE
from identity.contracts.principals import OPEN_PRINCIPAL, Principal, SERVICE_PRINCIPAL
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

CAPTURED: list = []


def runner_capture(args: dict, ctx) -> ToolResult:
    """Records the live `ToolContext` and answers. The ONLY way to see
    what the loop built -- `invoke_tool` constructs it and hands it
    straight to the runner, so there is no other seam to read it at."""
    CAPTURED.append(ctx)
    return ToolResult(text="captured")


@pytest.fixture(autouse=True)
def _clear_captured():
    CAPTURED.clear()
    yield
    CAPTURED.clear()


def _setup(actor_fields: dict, *, tool_keys=("stub.capture",), slug="general"):
    """A conversation, a queued assistant turn, and the payload the
    queue would carry -- with `actor_fields` merged in, so each test
    below differs by exactly the two keys it is about."""
    register_tool(ToolSpec(key="stub.capture", label="C", description="d",
                           runner="agents.runtime.tests.test_acting_rule.runner_capture"))
    bind_chat_role(CHAT_CONVERSE_ROLE)
    agent = make_agent(slug=slug, tool_keys=list(tool_keys), max_steps=8)
    conv = make_conversation(agent=agent)
    make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
    assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                          state=Turn.State.QUEUED)
    payload = {
        "conversation": str(conv.id), "turn": assistant.pk, "agent": agent.slug,
        "text": "a question", "connection": None, "mode": "chat",
        **actor_fields,
    }
    return agent, conv, payload


class TestARootTurn:
    def test_it_runs_as_the_actor_from_the_payload_not_as_the_agent(self):
        """THE REVERSAL, stated as an assertion. Before this, a turn ran
        as `Principal("resident_agent"|"user_agent", agent.slug)` --
        `agents/runtime/bindings.py::principal_for`, now deleted."""
        agent, _conv, payload = _setup({"actor_kind": "user", "actor_key": "7"})
        script = [("tool", "stub.capture", {}), ("final", "done")]
        with patch_llm(FakeToolLLM(script)):
            run_turn(payload, [], make_job_ctx())
        assert CAPTURED[0].principal == Principal("user", "7")
        assert CAPTURED[0].agent_slug == agent.slug

    def test_a_cli_turn_runs_as_the_one_service_principal(self):
        _agent, _conv, payload = _setup({"actor_kind": "service", "actor_key": "local"})
        with patch_llm(FakeToolLLM([("tool", "stub.capture", {}), ("final", "done")])):
            run_turn(payload, [], make_job_ctx())
        assert CAPTURED[0].principal == SERVICE_PRINCIPAL

    def test_a_payload_with_no_actor_runs_as_the_open_principal(self):
        """Every turn enqueued before this phase. Never a crash, and
        never a blank principal that no filter can reason about --
        `principal_from_payload` answers `OPEN_PRINCIPAL` and says
        why."""
        _agent, _conv, payload = _setup({})
        with patch_llm(FakeToolLLM([("tool", "stub.capture", {}), ("final", "done")])):
            run_turn(payload, [], make_job_ctx())
        assert CAPTURED[0].principal == OPEN_PRINCIPAL

    def test_a_payload_with_a_junk_actor_kind_does_not_fail_the_job(self):
        """A hand-edited or corrupt row must not turn into a failed
        turn: the worker would retry it forever."""
        _agent, _conv, payload = _setup({"actor_kind": "wizard", "actor_key": "x"})
        with patch_llm(FakeToolLLM([("tool", "stub.capture", {}), ("final", "done")])):
            result = run_turn(payload, [], make_job_ctx())
        assert result["text"] == "done"
        assert CAPTURED[0].principal == OPEN_PRINCIPAL


class TestADelegate:
    def test_it_inherits_the_root_actor_and_carries_its_own_slug(self):
        """NO HOP WIDENS THE ACTING PRINCIPAL. The delegate's own slug
        travels in `agent_slug`, so the trail still records WHICH agent
        made the call -- the fact an operator most wants when reading
        it -- while the principal stays the person who asked."""
        for spec in agent_tool_specs():
            register_tool(spec)
        register_tool(ToolSpec(key="stub.capture", label="C", description="d",
                               runner="agents.runtime.tests.test_acting_rule.runner_capture"))
        bind_chat_role(CHAT_CONVERSE_ROLE)
        make_agent(slug="library", tool_keys=["stub.capture"],
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
            ("tool", "stub.capture", {}),                                       # delegate
            ("final", "the library found it"),                                  # delegate
            ("final", "here is your answer"),                                   # root
        ]
        with patch_llm(FakeToolLLM(script)):
            run_turn(payload, [], make_job_ctx())

        # ONE capture, from inside the DELEGATE's own loop.
        assert len(CAPTURED) == 1
        assert CAPTURED[0].principal == Principal("user", "7")
        assert CAPTURED[0].agent_slug == "library"
        assert CAPTURED[0].depth == 1

    def test_the_delegates_tool_list_is_computed_from_the_root_actor(self, monkeypatch):
        """`available_tools` is called with the ROOT actor, not with a
        principal minted for the sub-agent. Asserted at the CALL, not
        only at its result, because in IA-1 every tool access is
        unrestricted and the two would produce the same list -- so the
        argument is the only thing that can be wrong yet."""
        import agents.runtime.delegate as delegate_module

        seen: list = []
        real = delegate_module.available_tools

        def spy(principal, agent, access):
            seen.append((principal, agent.slug))
            return real(principal, agent, access)

        monkeypatch.setattr(delegate_module, "available_tools", spy)

        for spec in agent_tool_specs():
            register_tool(spec)
        bind_chat_role(CHAT_CONVERSE_ROLE)
        make_agent(slug="library", tool_keys=[])
        general = make_agent(slug="general", tool_keys=["agent.library"], max_steps=8)
        conv = make_conversation(agent=general)
        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask")
        assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                              state=Turn.State.QUEUED)
        payload = {"conversation": str(conv.id), "turn": assistant.pk,
                   "agent": general.slug, "text": "ask", "connection": None,
                   "mode": "chat", "actor_kind": "user", "actor_key": "7"}
        script = [("tool", "agent.library", {"task": "go"}), ("final", "sub done"),
                  ("final", "root done")]
        with patch_llm(FakeToolLLM(script)):
            run_turn(payload, [], make_job_ctx())

        assert seen == [(Principal("user", "7"), "library")]


class TestTheRunTimeRederivation:
    def test_the_run_time_availability_call_uses_the_payload_actor(self, monkeypatch):
        """`plan_turn` computes roles at ENQUEUE time and `_run_turn`
        re-derives from the actor at RUN time, so somebody whose access
        narrowed between the two gets the narrower answer -- free,
        because that read has to happen at run time anyway.

        In IA-1 every tool access is unrestricted, so this asserts the
        SHAPE -- the run-time call really is made with the payload's
        actor -- rather than a narrowing. IA-2 gives it teeth by making
        the two answers differ."""
        import agents.runtime.loop as loop_module

        seen: list = []
        real = loop_module.granted_tools
        monkeypatch.setattr(
            loop_module, "granted_tools",
            lambda principal, keys, access: (
                seen.append(principal) or real(principal, keys, access)
            ),
        )
        _agent, _conv, payload = _setup({"actor_kind": "user", "actor_key": "7"})
        with patch_llm(FakeToolLLM([("final", "done")])):
            run_turn(payload, [], make_job_ctx())
        assert seen and all(p == Principal("user", "7") for p in seen)

    def test_the_planner_walks_the_agent_tree_as_the_actor(self, monkeypatch):
        """`_tool_roles(agent, actor)`: the enqueue-time role walk uses
        the same principal the run will, so the queue's admission
        snapshot cannot be computed against a wider tool set than the
        turn is actually offered."""
        import agents.runtime.jobs as jobs_module

        seen: list = []
        real = jobs_module.granted_tools
        monkeypatch.setattr(
            jobs_module, "granted_tools",
            lambda principal, keys, access: (
                seen.append(principal) or real(principal, keys, access)
            ),
        )
        _agent, _conv, payload = _setup({"actor_kind": "user", "actor_key": "7"})
        jobs_module.plan_turn(payload)
        assert seen and all(p == Principal("user", "7") for p in seen)


class TestPrincipalForIsGone:
    def test_the_bindings_module_no_longer_answers_who_an_agent_acts_as(self):
        """It has no callers left, and a function that answers the
        WRONG QUESTION is worse than no function: somebody would call
        it again."""
        import agents.runtime.bindings as bindings
        assert not hasattr(bindings, "principal_for")


class TestTwoLevelDelegationEnforcesTheRootAccess:
    """Review finding 1 (IA-2 T5 fix round 1) -- HIGHEST VALUE: an agent
    is never a way around labels, proven at DEPTH 2, not just depth 1.

    At depth 1, `delegate.py::run_agent_tool`'s own `available_tools(
    principal, agent, ctx.tool_access)` call already filters off the
    ROOT's `ctx.tool_access` -- threaded there from `loop.py`'s own
    `_run_turn`/`run_loop`, untouched by this finding -- so a
    single-hop test cannot tell "access threaded through `run_loop`
    too" from "access threaded through only the direct `available_
    tools` call". Only a GRANDCHILD -- a tool call the CHILD's own
    NESTED `run_loop` makes -- proves `run_loop`'s `access=` keyword
    (`delegate.py`'s `run_loop(..., access=ctx.tool_access)`) is not a
    dead argument: delete it and the CHILD's own loop defaults to
    `UNRESTRICTED_TOOL_ACCESS`, so every `ToolContext` it builds for
    ITS OWN tool calls -- including the one that starts the
    grandchild -- hands the grandchild a `tool_access` that was never
    the root's, reopening the exact bypass this task closes.

    THREE REAL AGENTS, THREE REAL HOPS, no monkeypatching of
    `available_tools`/`run_loop` themselves: `general` (root) delegates
    to `library` (child), which delegates to `illustrator` (grandchild),
    which alone holds the LABELLED leaf tool (`stub.capture`). The
    `FakeToolLLM` script ATTEMPTS the labelled call regardless of what
    is actually offered (it is a scripted double, not a real model), so
    whether `runner_capture` ever actually RUNS is entirely a function
    of whether the grandchild's own `available` dict really carried the
    root's restriction -- exactly the fact the deleted argument would
    break.
    """

    def _three_level_setup(self, actor_fields: dict):
        for spec in agent_tool_specs():
            register_tool(spec)
        register_tool(ToolSpec(key="stub.capture", label="C", description="d",
                               runner="agents.runtime.tests.test_acting_rule.runner_capture"))
        bind_chat_role(CHAT_CONVERSE_ROLE)
        # `resident=True, box_wide=True` on the two DELEGATE hops:
        # `library` and `illustrator` are the platform's real shipped
        # agent-as-tool slugs (`agent_tool_specs()`), which ship both
        # flags together in production (`agents.defaults.install_
        # default`'s `_fields()`). Task 15's `run_agent_tool` resolves a
        # delegate through `visible_agents(ctx.principal)`, which (like
        # today's picker) requires OWNED, BOX-WIDE (task 5, chat cluster
        # feature B -- formerly RESIDENT), or SHARED -- an unowned,
        # non-box-wide row is invisible to a plain member regardless of
        # any label, so leaving these `box_wide=False` would make this
        # fixture fail closed for a reason that has nothing to do with
        # the entitlement this test class is actually about.
        make_agent(slug="illustrator", tool_keys=["stub.capture"],
                   resident=True, box_wide=True)
        make_agent(slug="library", tool_keys=["agent.illustrator"],
                   resident=True, box_wide=True)
        general = make_agent(slug="general", tool_keys=["agent.library"], max_steps=10)
        conv = make_conversation(agent=general)
        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask")
        assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                              state=Turn.State.QUEUED)
        payload = {
            "conversation": str(conv.id), "turn": assistant.pk, "agent": general.slug,
            "text": "ask", "connection": None, "mode": "chat", **actor_fields,
        }
        # ONE `FakeToolLLM`, shared by every hop (`patch_llm` patches the
        # gateway globally) -- its script is therefore the GLOBAL call
        # order across all three nested loops, not three separate
        # scripts: root's tool call, then (once that nested call starts)
        # the child's, then (once THAT nested call starts) the
        # grandchild's tool call and its own final answer, then the
        # child's final answer, then the root's.
        script = [
            ("tool", "agent.library", {"task": "go"}),          # root -> child
            ("tool", "agent.illustrator", {"task": "deeper"}),  # child -> grandchild
            ("tool", "stub.capture", {}),                       # grandchild's own attempt
            ("final", "grandchild done"),
            ("final", "child done"),
            ("final", "root done"),
        ]
        return payload, script

    def test_an_unentitled_member_never_reaches_the_grandchilds_labelled_tool(self):
        member = make_user()
        legal = make_entitlement(name="Legal")
        ToolEntitlement.objects.create(tool_key="stub.capture", entitlement=legal)
        payload, script = self._three_level_setup(
            {"actor_kind": "user", "actor_key": str(member.pk)})
        with posture(POSTURE_ENTERPRISE):
            with patch_llm(FakeToolLLM(script)):
                run_turn(payload, [], make_job_ctx())
        # THE ONE ASSERTION THAT MATTERS: the grandchild's own registered,
        # role-resolvable runner never ran. Deleting `delegate.py`'s
        # `access=ctx.tool_access` pass-through into `run_loop` makes
        # this list non-empty -- `stub.capture` becomes wrongly
        # available at the grandchild hop and the FakeToolLLM's scripted
        # attempt actually reaches `runner_capture`.
        assert CAPTURED == []

    def test_an_entitlement_holder_reaches_it_two_hops_down(self):
        member = make_user()
        legal = make_entitlement(name="Legal")
        grant(legal, user=member)
        ToolEntitlement.objects.create(tool_key="stub.capture", entitlement=legal)
        payload, script = self._three_level_setup(
            {"actor_kind": "user", "actor_key": str(member.pk)})
        with posture(POSTURE_ENTERPRISE):
            with patch_llm(FakeToolLLM(script)):
                run_turn(payload, [], make_job_ctx())
        # ANTI-VACUOUS: the mechanism actually GRANTS it for a holder,
        # so the negative test above is failing closed for the right
        # reason rather than because nothing here ever works.
        assert len(CAPTURED) == 1
        assert CAPTURED[0].depth == 2
        assert CAPTURED[0].agent_slug == "illustrator"
        assert CAPTURED[0].principal == user_principal(member)
