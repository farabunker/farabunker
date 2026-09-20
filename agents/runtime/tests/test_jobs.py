"""The planner declares what a turn may load; the hook cleans up after a
turn that never ran; the summarizer never touches the database.
"""
from __future__ import annotations

import pytest

from agents.contracts.tools import ToolSpec, register_tool
from agents.models import ToolInvocation, Turn
from agents.runtime.jobs import on_turn_terminal, plan_turn, summarize_turn
from agents.runtime.tests._helpers import (  # noqa: F401 -- the fixture
    bind_chat_role, bound_chat_role, bound_embed_role, isolated_tool_registry,  # import
    make_agent, make_conversation, make_flow, make_turn,  # IS its registration
)
from models.contracts.roles import CHAT_CONVERSE_ROLE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


def _turn_for(agent):
    """An ASSISTANT placeholder on a conversation THIS agent owns.

    `plan_turn` reads `turn.conversation.agent`, not `payload["agent"]`,
    so a test that built the agent and the turn independently would
    either plan for the wrong agent or trip `uniq_agent_slug_ci` on the
    second `make_agent()` `make_conversation()` creates for itself.
    """
    return make_turn(
        conversation=make_conversation(agent=agent),
        role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
    )


def _payload(turn, **overrides):
    """`"agent"` carries the slug because the real payload does -- but
    it is the SUMMARIZER's only. `plan_turn` and `run_turn` both derive
    the agent from `turn.conversation.agent`, so a payload whose slug
    disagreed with the row would change nothing about what is planned.
    """
    fields = dict(conversation=str(turn.conversation_id), turn=turn.pk,
                  agent=turn.conversation.agent.slug, text="hi",
                  connection=None, mode="chat")
    fields.update(overrides)
    return fields


class TestPlanTurn:
    def test_it_declares_the_agents_own_chat_role(self, bound_chat_role):
        agent = make_agent()
        turn = _turn_for(agent)
        refs, exclusive = plan_turn(_payload(turn))
        assert [r.role for r in refs] == [CHAT_CONVERSE_ROLE]
        assert exclusive is True

    def test_it_unions_the_roles_of_every_granted_tool(self, bound_chat_role, bound_embed_role):
        register_tool(ToolSpec(key="stub.embeds", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok",
                               roles=("rag.embed",)))
        agent = make_agent(tool_keys=["stub.embeds"])
        turn = _turn_for(agent)
        refs, _ = plan_turn(_payload(turn))
        assert {r.role for r in refs} == {CHAT_CONVERSE_ROLE, "rag.embed"}

    def test_a_role_is_declared_once_even_when_two_tools_need_it(self, bound_chat_role,
                                                                bound_embed_role):
        for key in ("stub.a", "stub.b"):
            register_tool(ToolSpec(key=key, label="S", description="d",
                                   runner="agents.runtime.tests._helpers.runner_ok",
                                   roles=("rag.embed",)))
        agent = make_agent(tool_keys=["stub.a", "stub.b"])
        turn = _turn_for(agent)
        refs, _ = plan_turn(_payload(turn))
        assert len(refs) == 2

    def test_a_tool_whose_role_will_not_resolve_is_DROPPED_not_raised(self, bound_chat_role):
        """The same tolerant shape `plan_ingest` uses for its extract
        role (`tools/rag/jobs.py:408-424`), and the same filter the loop
        applies -- so a tool the planner dropped is a tool the model was
        never offered."""
        register_tool(ToolSpec(key="stub.unbound", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok",
                               roles=("nothing.bound.here",)))
        agent = make_agent(tool_keys=["stub.unbound"])
        turn = _turn_for(agent)
        refs, _ = plan_turn(_payload(turn))
        assert [r.role for r in refs] == [CHAT_CONVERSE_ROLE]

    def test_an_unresolvable_CHAT_role_is_a_hard_failure(self):
        """Not tolerant, deliberately: the enqueuing caller preflights
        this and refuses before the planner ever runs. Reaching here
        with no chat model means the preflight was skipped, and a turn
        that queued anyway would fail later and less legibly."""
        agent = make_agent(llm_role="nothing.bound.here")
        turn = _turn_for(agent)
        with pytest.raises(ValueError):
            plan_turn(_payload(turn))

    def test_footprints_are_left_unresolved(self, bound_chat_role):
        """`models/queue/scheduler.py`'s provenance contract: a planner
        never resolves footprints; claim-time code fills them in from a
        FRESH lookup, never from this snapshot."""
        agent = make_agent()
        turn = _turn_for(agent)
        refs, _ = plan_turn(_payload(turn))
        assert all(r.footprint_bytes is None for r in refs)

    def test_a_delegate_agents_tool_roles_are_declared_too(self, bound_chat_role,
                                                           bound_embed_role):
        """Agent-as-tool declares empty `roles` -- a delegate's roles are
        not knowable at registration time, so the PLANNER supplies them
        by walking the closure (spec section 6.1)."""
        register_tool(ToolSpec(key="stub.embeds", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok",
                               roles=("rag.embed",)))
        make_agent(slug="library", tool_keys=["stub.embeds"])
        register_tool(ToolSpec(key="agent.library", label="Ask the library agent",
                               description="d",
                               runner="agents.runtime.delegate.run_agent_tool"))
        root = make_agent(slug="general", tool_keys=["agent.library"])
        refs, _ = plan_turn(_payload(_turn_for(root)))
        assert "rag.embed" in {r.role for r in refs}

    def test_a_delegate_agents_OWN_chat_role_is_declared_too(self, bound_chat_role):
        """IMPORTANT fix: `_tool_roles` used to union each reachable
        agent's TOOL roles but never the delegate's OWN `llm_role` --
        `agent_tool_specs()` promises `plan_turn` supplies a delegate's
        roles at enqueue time, and a delegate's chat role is one of them,
        not just the roles of the tools it was granted."""
        bind_chat_role("chat.other", name="test-chat-other")
        register_tool(ToolSpec(key="agent.library", label="Ask the library agent",
                               description="d",
                               runner="agents.runtime.delegate.run_agent_tool"))
        make_agent(slug="library", llm_role="chat.other")
        root = make_agent(slug="general", tool_keys=["agent.library"])
        refs, _ = plan_turn(_payload(_turn_for(root)))
        assert "chat.other" in {r.role for r in refs}

    def test_an_unbound_delegate_chat_role_is_dropped_not_a_hard_failure(
            self, bound_chat_role, caplog):
        """The SAME tolerant-drop path a granted tool's own unresolvable
        role takes (`test_a_tool_whose_role_will_not_resolve_is_DROPPED_
        not_raised` above) -- a delegate whose OWN chat role does not
        resolve is dropped and logged, never a hard failure that would
        take the whole enqueue down for one bad delegate."""
        register_tool(ToolSpec(key="agent.library", label="Ask the library agent",
                               description="d",
                               runner="agents.runtime.delegate.run_agent_tool"))
        make_agent(slug="library", llm_role="nothing.bound.here")
        root = make_agent(slug="general", tool_keys=["agent.library"])
        with caplog.at_level("INFO", logger="agents.runtime.jobs"):
            refs, _ = plan_turn(_payload(_turn_for(root)))
        assert "nothing.bound.here" not in {r.role for r in refs}
        assert "does not resolve" in caplog.text

    def test_a_delegation_cycle_terminates(self, bound_chat_role):
        """Deduped by slug and bounded by MAX_AGENT_DEPTH. A cycle that
        hung the PLANNER would hang the enqueue, i.e. the request."""
        for slug in ("a", "b"):
            register_tool(ToolSpec(key=f"agent.{slug}", label="S", description="d",
                                   runner="agents.runtime.delegate.run_agent_tool"))
        make_agent(slug="a", tool_keys=["agent.b"])
        make_agent(slug="b", tool_keys=["agent.a"])
        root = make_agent(slug="a2", tool_keys=["agent.a"])
        refs, _ = plan_turn(_payload(_turn_for(root)))
        assert refs


class TestPlanTurnFlows:
    """`flow.run` declares `roles=()` at registration -- a flow's roles
    live in ROWS, so `_tool_roles` supplies them by walking the visible,
    enabled `Flow` rows (`agents.runtime.flowtool.flow_row_roles`),
    exactly as a delegate's own roles are supplied by walking the
    agent-as-tool closure.
    """

    def test_an_agent_granted_flow_run_declares_the_library_briefs_roles(
        self, bound_chat_role, bound_embed_role,
    ):
        """The anti-vacuous half: this agent is granted ONLY `flow.run`
        -- no other tool names `rag.embed`/`rag.answer` -- so the
        assertion cannot pass because some other granted tool declared
        the same roles."""
        from agents.defaults import install_default
        from agents.runtime.flowtool import FLOW_RUN
        from identity.contracts.principals import OPEN_PRINCIPAL
        from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE

        register_tool(FLOW_RUN)
        bind_chat_role(RAG_ANSWER_ROLE, name="test-answer")
        install_default("flow", "library-brief", OPEN_PRINCIPAL)
        agent = make_agent(tool_keys=["flow.run"])
        turn = _turn_for(agent)
        refs, _ = plan_turn(_payload(turn))
        assert {RAG_EMBED_ROLE, RAG_ANSWER_ROLE} <= {r.role for r in refs}

    def test_a_flow_step_naming_an_unregistered_tool_is_skipped_not_raised(
        self, bound_chat_role,
    ):
        """Ruling R1's tolerance: a step naming a feature-gated or
        not-yet-shipped tool is a not-here, not a fault -- the same
        tolerance a granted tool's own unresolvable role already gets."""
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)
        make_flow(slug="ghost-flow", steps=[{"tool": "not.registered.at.all", "args": {}}])
        agent = make_agent(tool_keys=["flow.run"])
        turn = _turn_for(agent)
        refs, _ = plan_turn(_payload(turn))  # must not raise
        assert [r.role for r in refs] == [CHAT_CONVERSE_ROLE]


class TestOnTurnTerminal:
    def test_a_cancelled_job_flips_a_queued_turn_to_cancelled(self):
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED)
        on_turn_terminal({"turn": turn.pk}, "cancelled")
        turn.refresh_from_db()
        assert turn.state == Turn.State.CANCELLED
        assert turn.error

    def test_a_failed_job_flips_a_running_turn_to_failed(self):
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.RUNNING)
        on_turn_terminal({"turn": turn.pk}, "failed")
        turn.refresh_from_db()
        assert turn.state == Turn.State.FAILED

    def test_it_leaves_a_done_turn_alone(self):
        """ONE conditional UPDATE filtered on the states a stranded turn
        can be in -- never a read-then-save. This hook runs AFTER the job
        row's terminal write commits, so a still-alive "stale" worker can
        be writing DONE in the same window; the `state__in` guard makes
        clobbering it impossible."""
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.DONE, text="answered")
        on_turn_terminal({"turn": turn.pk}, "failed")
        turn.refresh_from_db()
        assert turn.state == Turn.State.DONE
        assert turn.text == "answered"

    def test_a_missing_turn_is_a_no_op_not_an_exception(self):
        """`invoke_on_terminal` catches and logs
        (`models/contracts/jobkinds.py:291-320`), but a hook that relies
        on its caller's tolerance is a hook that will one day be called
        by something less tolerant."""
        on_turn_terminal({"turn": 999_999}, "cancelled")

    def test_a_payload_with_no_turn_key_is_a_no_op(self):
        on_turn_terminal({}, "failed")

    def test_on_turn_terminal_closes_the_turns_open_invocation_rows(self):
        """The live 2026-08-28 incident: a delegate's call was open when
        its turn died, and nothing closed it."""
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                         queue_job_id=99)
        open_row = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="rag.search", outcome=ToolInvocation.Outcome.ERROR,
            queue_job_id=99,
        )

        on_turn_terminal({"turn": turn.pk}, "cancelled")

        open_row.refresh_from_db()
        assert open_row.finished_at is not None

    def test_on_turn_terminal_leaves_another_jobs_open_row_alone(self):
        """Anti-vacuous pin: the correlation IS the point. A hook that
        closed every open row on the box would silently kill a concurrent
        turn's live call."""
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                         queue_job_id=99)
        other = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="library",
            tool_key="rag.ask", outcome=ToolInvocation.Outcome.ERROR,
            queue_job_id=100,
        )

        on_turn_terminal({"turn": turn.pk}, "failed")

        other.refresh_from_db()
        assert other.finished_at is None

    def test_it_does_not_fire_when_the_handler_ran_and_raised(self):
        """Proven at the seam, not assumed: `Worker._execute` gates the
        hook on `handler_started` (`models/queue/worker.py:618,623,670`),
        whose own docstring says a handler that ran and raised owns its
        failure account. `run_turn` therefore writes Turn(FAILED) itself
        (Task 8), and if this hook ALSO fired there would be two writers
        for one row's final state."""
        import inspect

        from models.queue import worker

        source = inspect.getsource(worker.Worker._execute)
        assert "handler_started" in source
        assert "if error is not None and not handler_started" in source


class TestSummarizeTurn:
    def test_it_never_touches_the_database(self, django_assert_num_queries):
        """The never-500 philosophy `models/queue/views.py::_summarize`
        already applies to every job kind's summarizer: a listing page
        renders many rows, and a summarizer that queried would turn one
        page render into N."""
        with django_assert_num_queries(0):
            summarize_turn({"agent": "general", "text": "what does the library say?"})

    def test_it_names_the_agent_and_the_question(self):
        out = summarize_turn({"agent": "general", "text": "what does the library say?"})
        assert "general" in out and "library" in out

    def test_it_survives_a_payload_missing_every_key(self):
        assert summarize_turn({})

    def test_a_long_question_is_truncated(self):
        assert len(summarize_turn({"agent": "a", "text": "x" * 500})) < 200


class TestRegistration:
    def test_agent_turn_is_registered_with_all_five_dotted_paths(self):
        from models.contracts.jobkinds import get_job_kind, resolve_dotted_path

        kind = get_job_kind("agent.turn")
        assert kind.default_priority == 100
        assert kind.on_terminal is not None
        for path in (kind.planner, kind.handler, kind.summarizer, kind.on_terminal):
            assert callable(resolve_dotted_path(path))
