"""The bounded loop: every way a turn can end, and each one honest.

The double is patched in at `models.contracts.gateway.get_llm_for` --
the seam `docs/DEV.md:181-186` names -- so the loop's own resolution,
message building, and schema rendering all really run.
"""
from __future__ import annotations

import logging
import time
from unittest.mock import patch

import pytest
from llama_index.core.llms import MessageRole

from agents.chat.rendering import render_answer
from agents.contracts.tools import ToolSpec, register_tool
from agents.models import AgentEntitlement, ToolInvocation, Turn
from agents.runtime.loop import AGENT_NOT_PERMITTED_NOTE, run_turn
from agents.runtime.prompt import history_messages
from agents.runtime.tests._helpers import (  # noqa: F401
    FakeToolLLM, bind_chat_role, isolated_tool_registry, make_agent, make_conversation,
    make_entitlement, make_flow, make_job_ctx, make_turn, make_user, patch_llm, posture,
)
from agents.tests._helpers import make_document
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.roles import CHAT_CONVERSE_ROLE
from tools.rag.models import DocumentAttachment

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]


def _setup(tool_keys=(), *, max_steps=8, script=(("final", "done"),)):
    # A REAL connection and role binding, not a patched `resolve()`:
    # `resolve` is code under test here (M1). `patch_llm` still supplies
    # the LLM itself at the gateway seam, so nothing reaches a network.
    bind_chat_role(CHAT_CONVERSE_ROLE)
    agent = make_agent(slug="general", tool_keys=list(tool_keys), max_steps=max_steps)
    conv = make_conversation(agent=agent)
    make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
    assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                          state=Turn.State.QUEUED)
    payload = {"conversation": str(conv.id), "turn": assistant.pk,
               "agent": agent.slug, "text": "a question",
               "connection": None, "mode": "chat"}
    return agent, conv, assistant, payload, FakeToolLLM(script)


class TestTheSimplePaths:
    def test_a_no_tool_agent_makes_one_llm_call_and_one_assistant_turn(self):
        _a, conv, assistant, payload, llm = _setup()
        with patch_llm(llm):
            result = run_turn(payload, [], make_job_ctx())
        assert len(llm.calls) == 1
        assistant.refresh_from_db()
        assert assistant.state == Turn.State.DONE
        assert assistant.text == "done"
        assert result["steps_used"] == 1
        assert conv.turns.filter(role=Turn.Role.TOOL).count() == 0

    def test_a_no_tool_agent_is_offered_no_tools_at_all(self):
        """`tools=None`, not `tools=[]`: an empty list is a claim that
        there are tools and none apply."""
        _a, _c, _t, payload, llm = _setup()
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert llm.calls[0][1] is None

    def test_one_tool_path_is_llm_tool_llm_and_three_turn_rows(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, assistant, payload, llm = _setup(
            ["stub.safe"],
            script=[("tool", "stub.safe", {}), ("final", "here is the answer")],
        )
        with patch_llm(llm):
            result = run_turn(payload, [], make_job_ctx())
        assert len(llm.calls) == 2
        assert [t.role for t in conv.turns.all()] == [
            Turn.Role.USER, Turn.Role.TOOL, Turn.Role.ASSISTANT,
        ]
        assert result["steps_used"] == 2      # the tool step, then the final step
        assistant.refresh_from_db()
        assert assistant.text == "here is the answer"

    def test_the_assistant_turn_moves_to_the_end_after_tool_turns(self):
        """The placeholder was created before the tool turns existed. It
        is moved so the conversation reads in the order it happened."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, assistant, payload, llm = _setup(
            ["stub.safe"], script=[("tool", "stub.safe", {}), ("final", "x")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        tool_turn = conv.turns.get(role=Turn.Role.TOOL)
        assert assistant.index > tool_turn.index

    def test_the_tool_turn_records_all_five_keys_and_the_invocation(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, _t, payload, llm = _setup(
            ["stub.safe"], script=[("tool", "stub.safe", {}), ("final", "x")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        turn = conv.turns.get(role=Turn.Role.TOOL)
        assert set(turn.tool_call) == {"tool", "args", "agent", "id", "discarded"}
        assert turn.tool_call["tool"] == "stub.safe"
        assert turn.tool_call["agent"] == "general"
        assert turn.tool_call["id"] == ""          # the engine supplies none
        assert turn.invocation_id is not None
        assert turn.invocation.outcome == ToolInvocation.Outcome.OK


class TestArtifactsReachTheModelInTheSameTurn:
    """The loop appends the pair `tool_turn_messages` returns, so the
    artifact line is not a history-only fix: the references are in front
    of the model on its VERY NEXT call, in the turn that made them. That
    is what lets "now edit that image" work without a second user turn.
    """

    def _run_one_tool_turn(self):
        register_tool(ToolSpec(key="stub.files", label="F", description="d",
                               runner="agents.runtime.tests._helpers.runner_artifacts"))
        _a, conv, _t, payload, llm = _setup(
            ["stub.files"], script=[("tool", "stub.files", {}), ("final", "here it is")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        return conv, llm

    def test_the_second_llm_call_sees_the_artifact_line(self):
        _conv, llm = self._run_one_tool_turn()
        tool_messages = [m for m in llm.calls[1][0] if m.role == MessageRole.TOOL]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "it worked\n[artifacts: output:36, output:37]"

    def test_the_stored_turn_text_itself_is_unchanged(self):
        """The line is built at RENDER time from the `artifacts` column,
        not baked into `Turn.text`. The row keeps saying what the tool
        actually said, so the page and the audit trail do not inherit a
        prompt-shaped string."""
        conv, _llm = self._run_one_tool_turn()
        turn = conv.turns.get(role=Turn.Role.TOOL)
        assert turn.text == "it worked"
        assert turn.artifacts == ["output:36", "output:37"]

    def test_a_tool_with_no_artifacts_adds_no_line(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, _t, payload, llm = _setup(
            ["stub.safe"], script=[("tool", "stub.safe", {}), ("final", "x")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        tool_messages = [m for m in llm.calls[1][0] if m.role == MessageRole.TOOL]
        assert tool_messages[0].content == "it worked"


class TestTheToolListIsFiltered:
    def test_an_unregistered_granted_key_never_reaches_the_prompt(self):
        _a, _c, _t, payload, llm = _setup(["not.registered"])
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert llm.calls[0][1] is None

    def test_a_granted_registered_tool_is_offered_under_its_WIRE_name(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, _t, payload, llm = _setup(["stub.safe"])
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        names = [t["function"]["name"] for t in llm.calls[0][1]]
        assert names == ["stub__safe"]

    def test_a_tool_needing_an_unbound_role_never_reaches_the_prompt(self, caplog):
        """`_roles_resolve` is the third of the three gates `Agent`'s own
        docstring names: granted, registered-and-non-mutating, and its
        declared roles actually resolve here. A tool naming a role
        nothing binds is dropped and logged, not offered and left to
        fail mid-turn."""
        register_tool(ToolSpec(key="stub.needs_role", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok",
                               roles=("some.unbound.role",)))
        _a, _c, _t, payload, llm = _setup(["stub.needs_role"])
        with caplog.at_level(logging.INFO):
            with patch_llm(llm):
                run_turn(payload, [], make_job_ctx())
        assert llm.calls[0][1] is None
        assert "does not resolve here" in caplog.text


class TestParallelCallsArePolicy:
    def test_three_calls_in_one_response_run_one_and_record_two_discarded(self):
        """Plain `Ollama.chat` does NOT cap tool calls -- `base.py:400-445`
        passes them through uncapped, and `force_single_tool_call`
        (`base.py:63`) is only reachable through `chat_with_tools`, which
        this design does not use. Taking `calls[0]` is THIS PLATFORM'S
        policy, so it must be pinned here or it is not a rule at all."""
        for key in ("stub.a", "stub.b", "stub.c"):
            register_tool(ToolSpec(key=key, label="S", description="d",
                                   runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, _t, payload, llm = _setup(
            ["stub.a", "stub.b", "stub.c"],
            script=[
                [("tool", "stub.a", {}), ("tool", "stub.b", {}), ("tool", "stub.c", {})],
                ("final", "x"),
            ],
        )
        with patch_llm(llm):
            result = run_turn(payload, [], make_job_ctx())
        turn = conv.turns.get(role=Turn.Role.TOOL)
        assert turn.tool_call["tool"] == "stub.a"
        assert [d["tool"] for d in turn.tool_call["discarded"]] == ["stub.b", "stub.c"]
        assert ToolInvocation.objects.count() == 1      # exactly one tool actually ran
        assert result["steps_used"] == 2                # one tool step, one final step

    def test_the_tool_message_says_which_call_was_executed(self):
        """The discard is recorded, never silent -- in the row AND in
        what the model is told, so its next step is reasoning about what
        really happened."""
        for key in ("stub.a", "stub.b"):
            register_tool(ToolSpec(key=key, label="S", description="d",
                                   runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, _t, payload, llm = _setup(
            ["stub.a", "stub.b"],
            script=[[("tool", "stub.a", {}), ("tool", "stub.b", {})], ("final", "x")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert "stub.b" in conv.turns.get(role=Turn.Role.TOOL).text


class TestResolvedTurnTimeout:
    """`agents.runtime.loop.resolved_turn_timeout` -- the ONE resolver
    both `_run_turn` and `agents.runtime.delegate.run_agent_tool` call
    (B2, fix round 1), unit-tested directly rather than only through
    their own end-to-end effects."""

    def test_uses_the_stamped_value_when_set(self):
        from agents.runtime.loop import resolved_turn_timeout

        ctx = make_job_ctx(response_timeout_seconds=123.0)

        assert resolved_turn_timeout(ctx) == 123.0

    def test_falls_back_to_turn_deadline_seconds_when_none(self):
        from agents.limits import TURN_DEADLINE_SECONDS
        from agents.runtime.loop import resolved_turn_timeout

        ctx = make_job_ctx(response_timeout_seconds=None)

        assert resolved_turn_timeout(ctx) == TURN_DEADLINE_SECONDS

    def test_a_degenerate_stamped_value_is_not_floored_here(self):
        """MINOR 7 (final review, fix round 3) DELIBERATELY does NOT
        floor this function's own return value -- see its docstring.
        `resolved_turn_timeout` must keep returning a `0`/negative value
        verbatim: this suite's OWN deadline-simulation tests
        (`make_job_ctx(response_timeout_seconds=-1.0)`, throughout this
        file) rely on exactly that to build an already-expired
        `StepBudget`. The floor lives ONLY at the `gateway.get_llm_for`
        call sites (`_run_turn`, `agents.runtime.delegate.
        run_agent_tool`), proven by
        `TestTheCompetingTimeoutFloor`, below -- not here, where it
        would (and, in this function's first version, briefly did)
        silently turn "already expired" into "an hour from now"."""
        from agents.runtime.loop import resolved_turn_timeout

        assert resolved_turn_timeout(make_job_ctx(response_timeout_seconds=0.0)) == 0.0
        assert resolved_turn_timeout(make_job_ctx(response_timeout_seconds=-5.0)) == -5.0


class TestTheCompetingTimeoutFloor:
    """MINOR 7 (final review, fix round 3): a degenerate (`<= 0`)
    resolved timeout must never reach a REAL engine client as
    `request_timeout` -- floored at the two `gateway.get_llm_for` call
    sites, never inside `resolved_turn_timeout` itself (see that
    function's own docstring, and `TestResolvedTurnTimeout`'s own
    sibling test, for why)."""

    def test_the_root_loops_llm_is_never_built_with_a_non_positive_timeout(self):
        from agents.limits import RESOLVED_TIMEOUT_FLOOR_SECONDS

        _a, _c, _t, payload, llm = _setup()
        ctx = make_job_ctx(response_timeout_seconds=0.0)

        with (
            patch("models.contracts.gateway.get_llm_for", return_value=llm) as mock_get_llm_for,
            patch("agents.runtime.loop.supports_tool_calling", return_value=None),
        ):
            run_turn(payload, [], ctx)

        _, kwargs = mock_get_llm_for.call_args
        assert kwargs["request_timeout"] == RESOLVED_TIMEOUT_FLOOR_SECONDS

    def test_a_positive_timeout_well_above_the_floor_is_untouched(self):
        _a, _c, _t, payload, llm = _setup()
        ctx = make_job_ctx(response_timeout_seconds=123.0)

        with (
            patch("models.contracts.gateway.get_llm_for", return_value=llm) as mock_get_llm_for,
            patch("agents.runtime.loop.supports_tool_calling", return_value=None),
        ):
            run_turn(payload, [], ctx)

        _, kwargs = mock_get_llm_for.call_args
        assert kwargs["request_timeout"] == 123.0

    def test_the_budgets_own_deadline_is_unaffected_by_the_floor(self):
        """The floor lives ONLY at the `get_llm_for` boundary -- a `0`
        stamped value still produces an ALREADY-EXPIRED budget (the
        honest, graceful ending), never a client-boundary value leaking
        back into the budget's own deadline."""
        _a, _c, assistant, payload, llm = _setup()
        ctx = make_job_ctx(response_timeout_seconds=0.0)

        with patch_llm(llm):
            run_turn(payload, [], ctx)

        assistant.refresh_from_db()
        assert "time limit" in assistant.text
        assert llm.calls == []


class TestHonestEndings:
    def test_budget_exhaustion_produces_an_honest_final_and_spends_max_steps(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, assistant, payload, llm = _setup(
            ["stub.safe"], max_steps=3,
            script=[("tool", "stub.safe", {})] * 5,
        )
        with patch_llm(llm):
            result = run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert result["steps_used"] == 3
        assert "ran out of steps" in assistant.text
        assert assistant.state == Turn.State.DONE     # honest, not failed

    def test_an_exhausted_turn_still_carries_the_last_tool_result(self):
        """Never a fabricated answer, and never a silent truncation: the
        user gets the honest sentence AND whatever was actually found."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, assistant, payload, llm = _setup(
            ["stub.safe"], max_steps=2, script=[("tool", "stub.safe", {})] * 3,
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "it worked" in assistant.text

    def test_an_expired_deadline_ends_the_turn_before_the_first_call(self, monkeypatch):
        _a, _c, assistant, payload, llm = _setup()
        monkeypatch.setattr(
            "agents.runtime.loop.TURN_DEADLINE_SECONDS", -1.0,
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "time limit" in assistant.text
        assert llm.calls == []
        # No tool ever ran, so the "here is what the last tool returned"
        # trailer must not appear -- that would be a fabricated claim.
        assert "last tool returned" not in assistant.text

    def test_a_deadline_crossed_mid_step_ends_before_the_tool_runs(self, monkeypatch):
        """`budget.expired` is checked at the top of EVERY iteration and
        AGAIN immediately before the tool call -- never only at the top.
        Here the model already chose a tool on step 1, but the deadline
        crosses between that LLM call and the tool actually running; the
        turn must end honestly right there, and the tool must never run."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, assistant, payload, llm = _setup(
            ["stub.safe"], script=[("tool", "stub.safe", {})],
        )
        monkeypatch.setattr("agents.runtime.loop.TURN_DEADLINE_SECONDS", 10.0)
        # Three `time.monotonic()` calls happen before a tool would run:
        # (1) the budget's own construction, (2) the top-of-iteration
        # check (not yet expired), (3) the pre-tool-call check (expired).
        clock = iter([0.0, 0.0, 100.0])
        monkeypatch.setattr("agents.runtime.loop.time.monotonic", lambda: next(clock))
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "time limit" in assistant.text
        assert ToolInvocation.objects.count() == 0
        assert conv.turns.filter(role=Turn.Role.TOOL).count() == 0

    def test_ctx_response_timeout_seconds_overrides_the_fallback_constant(self):
        """One-timeout task (2026-09-17): `ctx.response_timeout_seconds`
        -- not `agents.limits.TURN_DEADLINE_SECONDS` -- is the operative
        deadline whenever it is stamped. A deliberately negative (already
        expired) value proves the loop actually built its budget from
        THIS number: `TURN_DEADLINE_SECONDS` stays 900.0 throughout, and
        would never have expired here on its own."""
        _a, _c, assistant, payload, llm = _setup()
        ctx = make_job_ctx(response_timeout_seconds=-1.0)

        with patch_llm(llm):
            run_turn(payload, [], ctx)

        assistant.refresh_from_db()
        assert "time limit" in assistant.text
        assert llm.calls == []

    def test_ctx_response_timeout_seconds_none_falls_back_to_turn_deadline_seconds(
        self, monkeypatch,
    ):
        _a, _c, assistant, payload, llm = _setup()
        monkeypatch.setattr("agents.runtime.loop.TURN_DEADLINE_SECONDS", -1.0)
        ctx = make_job_ctx(response_timeout_seconds=None)

        with patch_llm(llm):
            run_turn(payload, [], ctx)

        assistant.refresh_from_db()
        assert "time limit" in assistant.text
        assert llm.calls == []

    def test_the_llm_is_built_with_the_turns_own_response_timeout(self):
        """The competing-timeout pin, from the loop's own side
        (complementing `models/registry/tests/test_bindings.py`'s
        gateway-level pin): `_run_turn` passes `request_timeout=` into
        `gateway.get_llm_for` using the SAME resolved value the budget
        was built from -- ollama's own hidden `DEFAULT_REQUEST_TIMEOUT`
        never gets a chance to compete."""
        _a, _c, _t, payload, llm = _setup()
        ctx = make_job_ctx(response_timeout_seconds=123.0)

        with (
            patch("models.contracts.gateway.get_llm_for", return_value=llm) as mock_get_llm_for,
            patch("agents.runtime.loop.supports_tool_calling", return_value=None),
        ):
            run_turn(payload, [], ctx)

        _, kwargs = mock_get_llm_for.call_args
        assert kwargs["request_timeout"] == 123.0

    def test_a_timed_out_turn_is_marked_failed_with_the_shared_constant(self):
        """Section 5 of the one-timeout task: a `budget.expired` ending
        (never a `budget.exhausted`/steps one -- see `TestHonestEndings`'s
        own step-exhaustion pin above) marks the turn FAILED and writes
        `TURN_TIMEOUT_ERROR` into `Turn.error`, so `_turn_card.html`'s
        FAILED branch (and its admin-only Settings hint) can key off it."""
        from agents.runtime.loop import TURN_TIMEOUT_ERROR

        _a, _c, assistant, payload, llm = _setup()
        ctx = make_job_ctx(response_timeout_seconds=-1.0)

        with patch_llm(llm):
            run_turn(payload, [], ctx)

        assistant.refresh_from_db()
        assert assistant.state == Turn.State.FAILED
        assert assistant.error == TURN_TIMEOUT_ERROR

    def test_an_in_flight_engine_hang_past_the_deadline_ends_as_the_shared_timeout(self):
        """Final review I-2 (fix round 3): an engine call already IN
        FLIGHT when the deadline passes is the one place `budget.
        expired` cannot be checked before the call itself raises --
        every other check in this loop runs BETWEEN steps. This is the
        incident's own scenario (a chat-model reload under memory
        pressure hanging inside `llm.chat`, raising `httpx.ReadTimeout`
        once the engine's own client timeout -- now equal to the turn's
        value -- elapses) and it must produce the SAME ending as the
        loop's own `budget.expired` breaks: FAILED, `TURN_TIMEOUT_ERROR`,
        and the last tool's own result preserved in `Turn.text` -- never
        the raw, often-empty exception string `run_turn`'s outer
        `except` would otherwise write.

        A REAL, tiny sleep past a REAL, tiny deadline, deliberately,
        rather than mocking `time.monotonic()`: `StepBudget.expired` is
        checked several separate times across one tool round-trip (top
        of loop, before the tool runs, top of loop again), and
        hand-counting every one of them, across two different modules
        that both call it (`agents.runtime.loop`, `agents.contracts.
        tools`), would make this test fail the moment an unrelated line
        anywhere in that path added or removed a call -- for a reason
        that has nothing to do with what this test actually proves. The
        margin is generous (a 0.2s deadline, a 0.3s sleep) so ordinary
        DB/test overhead before the second `llm.chat` call cannot itself
        cross the deadline early; if it somehow did, the pre-existing
        `budget.expired` branch this loop already had would produce the
        identical observable outcome asserted below, which is itself
        proof this fix's ending is indistinguishable from that one, by
        design."""
        import time as time_module

        from agents.limits import TURN_TIMEOUT_ERROR

        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, assistant, payload, llm = _setup(
            ["stub.safe"], script=[("tool", "stub.safe", {})],
        )
        ctx = make_job_ctx(response_timeout_seconds=0.2)
        real_chat = llm.chat
        call_count = {"n": 0}

        def _chat(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return real_chat(*args, **kwargs)
            time_module.sleep(0.3)
            raise RuntimeError("engine hung")

        llm.chat = _chat
        with patch_llm(llm):
            run_turn(payload, [], ctx)

        assistant.refresh_from_db()
        assert assistant.state == Turn.State.FAILED
        assert assistant.error == TURN_TIMEOUT_ERROR
        assert "it worked" in assistant.text
        assert call_count["n"] == 2

    def test_an_in_flight_exception_before_the_deadline_keeps_the_generic_ending(self):
        """The companion case (final review I-2): an engine call that
        raises for a REAL reason, nothing to do with timing, still gets
        the pre-existing generic ending -- `run_turn`'s own `except`
        writes `str(exc)` verbatim, exactly as it always has. Proves the
        new deadline check in `run_loop` RE-RAISES rather than
        swallowing an ordinary failure -- the companion assertion to the
        test above, which proves the opposite branch."""
        _a, _c, assistant, payload, llm = _setup()

        def _chat(*args, **kwargs):
            raise RuntimeError("boom")

        llm.chat = _chat
        with patch_llm(llm), pytest.raises(RuntimeError):
            run_turn(payload, [], make_job_ctx())

        assistant.refresh_from_db()
        assert assistant.state == Turn.State.FAILED
        assert assistant.error == "boom"

    def test_an_empty_response_with_no_tool_call_ends_honestly(self, caplog):
        """A model that emits neither text nor a tool call must never
        produce a blank DONE turn -- that reads as a bug, not an answer.
        """
        _a, _c, assistant, payload, llm = _setup(script=[("final", "")])
        with caplog.at_level(logging.INFO):
            with patch_llm(llm):
                run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert assistant.text == "The model returned no answer."
        assert assistant.state == Turn.State.DONE
        assert "empty response" in caplog.text

    def test_a_bound_model_that_cannot_call_tools_says_so_and_calls_nothing(self, monkeypatch):
        """Section 10.1's row, reached from inside the loop rather than a
        preflight. NO prompt-hacking: never a hand-rolled tool syntax in
        the system prompt, never a "please respond in JSON" nudge, never
        parsing a tool call out of prose (`models/contracts/engines/
        base.py:450-458` says all three outright)."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, assistant, payload, llm = _setup(["stub.safe"])
        monkeypatch.setattr("agents.runtime.loop.supports_tool_calling", lambda r: False)
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "cannot call tools" in assistant.text
        assert llm.calls == []

    def test_an_unreported_tool_capability_attempts_the_turn(self, monkeypatch):
        """`None` means the engine does not report the fact at all
        (`base.py:450-458`). The turn RUNS; a model that emits no tool
        call is honestly indistinguishable from one that chose not to."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, _t, payload, llm = _setup(["stub.safe"])
        monkeypatch.setattr("agents.runtime.loop.supports_tool_calling", lambda r: None)
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert len(llm.calls) == 1


def _replayed_assistant_content(conv, needle):
    """The `.content` of the replayed ASSISTANT message (`agents.runtime.
    prompt.history_messages`) that carries `needle` -- the text a LATER
    turn's own prompt would actually see, as opposed to `Turn.text`
    itself (the stored, human-facing row). Filtered to `MessageRole.
    ASSISTANT` specifically: a TOOL turn's own message (role `TOOL`) can
    carry the identical raw words, already fenced by `_tool_content`, and
    this helper exists to look past that at the FINAL answer message."""
    matches = [m.content for m in history_messages(conv)
              if m.role == MessageRole.ASSISTANT and m.content and needle in m.content]
    assert matches, f"no replayed ASSISTANT message contains {needle!r}"
    return matches[-1]


class TestHonestEndingsFencing:
    """H5 review round 1, IMPORTANT / round 2, finding 1: `_honest_ending`
    and `_two_failures_text` bake raw tool text DIRECTLY into the
    ASSISTANT turn's own persisted `Turn.text`. Round 1 fenced it AT THAT
    WRITE -- which meant the DATA header and the BEGIN/END marker lines
    rendered verbatim in the one place with no render-time step of its
    own, the chat bubble a HUMAN actually reads. Round 2 moves the fence
    to REPLAY time instead: `Turn.text` is unfenced again, exactly as it
    was before H5, and `agents.runtime.prompt.history_messages` fences a
    COPY of it, from `Turn.data["appended_tool_result"]`'s own
    provenance, only when that copy is built for a LATER turn's prompt.
    """

    def test_an_exhausted_turns_last_retrieval_result_is_unfenced_for_a_human_reader(self):
        register_tool(ToolSpec(key="rag.search", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, assistant, payload, llm = _setup(
            ["rag.search"], max_steps=1, script=[("tool", "rag.search", {})] * 2,
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "BEGIN TOOL RESULT" not in assistant.text
        assert "END TOOL RESULT" not in assistant.text
        assert "it worked" in assistant.text
        # And through the actual chat-rendering seam -- the reader never
        # gets fence scaffolding either.
        assert "BEGIN TOOL RESULT" not in render_answer(assistant.text)

    def test_the_same_result_fences_only_when_replayed_into_a_later_prompt(self):
        register_tool(ToolSpec(key="rag.search", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, assistant, payload, llm = _setup(
            ["rag.search"], max_steps=1, script=[("tool", "rag.search", {})] * 2,
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        # H5 review round 3, finding 2: each entry also carries its own
        # `offset`/`length` within `assistant.text` -- computed by
        # `agents.runtime.loop._honest_ending` directly from the fixed
        # template pieces, not re-found by searching.
        entries = assistant.data["appended_tool_result"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["tool"] == "rag.search"
        assert entry["text"] == "it worked"
        offset, length = entry["offset"], entry["length"]
        assert assistant.text[offset:offset + length] == "it worked"
        content = _replayed_assistant_content(conv, "it worked")
        assert "BEGIN TOOL RESULT" in content
        assert "END TOOL RESULT" in content

    def test_a_non_retrieval_last_result_stays_unfenced_on_both_sides(self):
        """Pinned beside its fenced twin above -- `TestHonestEndings::
        test_an_exhausted_turn_still_carries_the_last_tool_result`
        already covers this with `stub.safe`; this is the same shape,
        asserting the ABSENCE of the fence explicitly in BOTH the stored
        text and a replay of it."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, conv, assistant, payload, llm = _setup(
            ["stub.safe"], max_steps=1, script=[("tool", "stub.safe", {})] * 2,
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "BEGIN TOOL RESULT" not in assistant.text
        assert "it worked" in assistant.text
        content = _replayed_assistant_content(conv, "it worked")
        assert "BEGIN TOOL RESULT" not in content

    def test_two_failed_retrieval_calls_stay_unfenced_in_the_stored_text(self):
        """`_two_failures_text` composes its "- {tool}: {text}" lines
        directly from `failure_provenance` (one dict per failing call,
        `_tool_result_provenance`'s own entries) -- STORED raw (round 2,
        finding 1); there is no separate list of pre-joined strings to
        keep in sync any more (round 3, finding 2). Each entry is fenced
        independently only on replay, the next test."""
        register_tool(ToolSpec(key="rag.search", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_value_error"))
        _a, conv, assistant, payload, llm = _setup(
            ["rag.search"],
            script=[("tool", "rag.search", {}), ("tool", "rag.search", {})],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "BEGIN TOOL RESULT" not in assistant.text
        assert "no row matches that reference" in assistant.text
        assert len(assistant.data["appended_tool_result"]) == 2

    def test_two_failed_retrieval_calls_both_fence_independently_on_replay(self):
        """Anti-vacuous pin for `_replay_assistant_text`'s own monotonic-
        cursor reasoning: TWO entries with the IDENTICAL raw text (the
        same tool failing the same way twice) must each get their OWN
        fence, not one double-fenced call and one bare."""
        register_tool(ToolSpec(key="rag.search", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_value_error"))
        _a, conv, assistant, payload, llm = _setup(
            ["rag.search"],
            script=[("tool", "rag.search", {}), ("tool", "rag.search", {})],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        content = _replayed_assistant_content(conv, "no row matches that reference")
        assert content.count("BEGIN TOOL RESULT") == 2
        assert content.count("END TOOL RESULT") == 2

    def test_a_flow_reaching_an_honest_ending_fences_by_its_last_step_only_on_replay(self):
        """H5 review round 1, CRITICAL / round 2, finding 1, exercised
        end to end through the REAL `flow.run` runner (`agents.runtime.
        flow.run_flow`): a flow whose one step is `rag.search` must fence
        the honest ending exactly like a bare `rag.search` call, because
        `flow.run`'s own result text IS that step's text (`agents/
        runtime/flow.py::_finished`) -- but only in a REPLAY of it, never
        in `Turn.text` itself."""
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)
        register_tool(ToolSpec(key="rag.search", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_flow(slug="one-step", steps=[{"tool": "rag.search", "args": {}}])
        _a, conv, assistant, payload, llm = _setup(
            ["flow.run"], max_steps=1,
            script=[("tool", "flow.run", {"flow": "one-step"})] * 2,
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert "BEGIN TOOL RESULT" not in assistant.text
        assert "it worked" in assistant.text
        content = _replayed_assistant_content(conv, "it worked")
        assert "BEGIN TOOL RESULT" in content


class TestFailureAndRecovery:
    def test_a_failing_tool_produces_a_tool_turn_and_exactly_one_recovery(self):
        register_tool(ToolSpec(key="stub.bad", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_value_error"))
        _a, conv, assistant, payload, llm = _setup(
            ["stub.bad"], script=[("tool", "stub.bad", {}), ("final", "recovered")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert assistant.text == "recovered"
        assert conv.turns.filter(role=Turn.Role.TOOL).count() == 1

    def test_a_second_failure_ends_the_turn_naming_both(self):
        register_tool(ToolSpec(key="stub.bad", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_value_error"))
        _a, _c, assistant, payload, llm = _setup(
            ["stub.bad"], script=[("tool", "stub.bad", {}), ("tool", "stub.bad", {})],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert assistant.text.count("stub.bad") >= 2

    def test_a_refused_tool_is_dropped_from_the_rest_of_the_turn(self):
        """"No retry" made literal: the model is not offered a tool it
        will be refused for using -- enforcement by omission, the same
        mechanism section 6.4 uses for the depth cap."""
        register_tool(ToolSpec(key="stub.refuse", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_refused_subclass"))
        _a, _c, _t, payload, llm = _setup(
            ["stub.refuse"], script=[("tool", "stub.refuse", {}), ("final", "ok")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert llm.calls[0][1] is not None            # offered on the first call
        assert llm.calls[1][1] is None                # gone on the second

    def test_an_unknown_wire_name_is_a_tool_error_not_a_crash(self):
        _a, conv, assistant, payload, llm = _setup(
            script=[("tool", "no.such.tool", {}), ("final", "recovered")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert assistant.text == "recovered"
        assert conv.turns.get(role=Turn.Role.TOOL).invocation.tool_key == "no.such.tool"

    def test_every_failed_call_still_spends_a_step(self):
        """A failing loop cannot outrun the budget: with only ONE recovery
        per turn, a second failure ends the turn well before `max_steps`
        (4 here) is reached -- but it still costs exactly the two steps
        both failing calls actually spent, not zero and not a free retry.
        """
        register_tool(ToolSpec(key="stub.bad", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_value_error"))
        _a, _c, assistant, payload, llm = _setup(
            ["stub.bad"], max_steps=4, script=[("tool", "stub.bad", {})] * 6,
        )
        with patch_llm(llm):
            result = run_turn(payload, [], make_job_ctx())
        assert result["steps_used"] == 2
        assistant.refresh_from_db()
        assert assistant.text.count("stub.bad") >= 2

    def test_a_param_error_tool_stays_offered_and_shows_the_reasons(self):
        """The asymmetry (section 10.2): a REFUSED tool is dropped for
        the rest of the turn, but a `param_error` is not -- the model was
        told exactly what was wrong with its arguments and gets a real
        chance to fix them, so the tool stays in the schema and the tool
        message carries the per-arg reasons verbatim."""
        register_tool(ToolSpec(
            key="stub.paramerr", label="S", description="d",
            runner="agents.runtime.tests._helpers.runner_param_error_subclass",
        ))
        _a, conv, _t, payload, llm = _setup(
            ["stub.paramerr"],
            script=[("tool", "stub.paramerr", {}), ("final", "recovered")],
        )
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert llm.calls[1][1] is not None
        names = [t["function"]["name"] for t in llm.calls[1][1]]
        assert "stub__paramerr" in names
        tool_turn = conv.turns.get(role=Turn.Role.TOOL)
        assert "top_k" in tool_turn.text
        assert "50 is too large" in tool_turn.text


class TestTheHandlerWritesItsOwnFailure:
    def test_a_raising_loop_marks_the_turn_failed_and_re_raises(self, monkeypatch):
        """Section 6.2 step 8. `on_terminal` does NOT fire when the
        handler ran and raised -- `Worker._execute` gates it on a
        `handler_started` flag (`models/queue/worker.py:618,623,670`)
        whose docstring says so outright. So the handler must write its
        own failure or nothing ever does."""
        _a, _c, assistant, payload, llm = _setup()
        monkeypatch.setattr(
            "agents.runtime.loop.build_messages",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with patch_llm(llm), pytest.raises(RuntimeError):
            run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert assistant.state == Turn.State.FAILED
        assert assistant.error == "boom"
        assert "Traceback" not in assistant.error

    def test_a_failing_writeback_does_not_mask_the_original_exception(self, monkeypatch,
                                                                       caplog):
        """A DB outage during the FAILED-state writeback itself must not
        replace `boom` with its own error -- that would hide the real
        reason the turn failed behind an unrelated one from the moment
        `run_turn` tried to record it. Only the FAILED-state update
        (from the `except` clause) is made to explode; the earlier
        RUNNING-state update (`_run_turn`'s own first write) must still
        go through normally, proving this isn't just a blanket `update`
        failure the test happens to survive by accident."""
        from django.db.models import QuerySet

        _a, _c, assistant, payload, llm = _setup()
        monkeypatch.setattr(
            "agents.runtime.loop.build_messages",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        real_update = QuerySet.update

        def _update(self, **kwargs):
            if kwargs.get("state") == Turn.State.FAILED:
                raise RuntimeError("db outage during the writeback itself")
            return real_update(self, **kwargs)

        monkeypatch.setattr(QuerySet, "update", _update)
        with patch_llm(llm), caplog.at_level("ERROR", logger="agents.runtime.loop"):
            with pytest.raises(RuntimeError) as exc_info:
                run_turn(payload, [], make_job_ctx())
        assert str(exc_info.value) == "boom"          # the ORIGINAL exception, re-raised
        assert "could not write" in caplog.text.lower()
        assistant.refresh_from_db()
        assert assistant.state == Turn.State.RUNNING  # the writeback never landed

    def test_a_handler_that_raises_closes_its_own_open_invocation_rows(self):
        """`on_turn_terminal` does NOT fire when the handler ran and raised
        (`handler_started`), so this except is the only closer on this
        path -- and the original exception must still propagate.

        The turn's own tool call opens (and, on the happy path, closes)
        its own row normally -- `invoke_tool` is fully synchronous. What
        this pins is the WIRING: an open row stamped with THIS job's id
        -- exactly what a crash mid a delegate's own call would leave
        behind -- gets closed by this except, correlated purely on
        `ctx.job_id`, with no dependency on which tool call produced it.
        """
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        _a, _c, assistant, payload, llm = _setup(
            ["stub.safe"], script=[("tool", "stub.safe", {}), ("final", "unreached")],
        )
        ctx = make_job_ctx(job_id=4242)
        open_row = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="agent.library", outcome=ToolInvocation.Outcome.ERROR,
            queue_job_id=ctx.job_id,
        )
        real_chat = llm.chat

        def _chat(*args, **kwargs):
            if len(llm.calls) == 1:      # the first tool call already opened its row
                raise RuntimeError("boom")
            return real_chat(*args, **kwargs)

        llm.chat = _chat
        with patch_llm(llm), pytest.raises(RuntimeError) as exc_info:
            run_turn(payload, [], ctx)
        assert str(exc_info.value) == "boom"
        open_row.refresh_from_db()
        assert open_row.finished_at is not None
        assistant.refresh_from_db()
        assert assistant.state == Turn.State.FAILED


class TestFlowRunNarrowing:
    """`flow.run` is registered with empty `choices`; the loop narrows
    it per turn from the enabled `Flow` rows this principal may run
    (`agents.runtime.flowtool.narrowed_flow_spec`, called from
    `available_tools`)."""

    def _flow_run_schema(self, tools: list | None) -> dict | None:
        if not tools:
            return None
        return next((t for t in tools if t["function"]["name"] == "flow__run"), None)

    def test_two_enabled_flows_appear_as_an_enum_naming_their_input_keys(self):
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_flow(slug="flow-a", name="Flow A", description="does a",
                 inputs=[{"key": "topic", "label": "Topic"}],
                 steps=[{"tool": "stub.safe", "args": {}}])
        make_flow(slug="flow-b", name="Flow B", description="does b",
                 inputs=[{"key": "query", "label": "Query"}],
                 steps=[{"tool": "stub.safe", "args": {}}])
        _a, _c, _t, payload, llm = _setup(["flow.run"])
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        schema = self._flow_run_schema(llm.calls[0][1])
        assert schema is not None
        flow_param = schema["function"]["parameters"]["properties"]["flow"]
        assert set(flow_param["enum"]) == {"flow-a", "flow-b"}
        description = schema["function"]["description"]
        assert "topic" in description
        assert "query" in description

    def test_no_flow_rows_means_flow_run_is_absent_from_the_tool_list(self):
        """Offering the tool with no legal value for its only argument
        would burn the turn's one recovery on a call that could never
        work -- enforcement by omission, so it is simply never offered."""
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)
        _a, _c, _t, payload, llm = _setup(["flow.run"])
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert llm.calls[0][1] is None

    def test_a_disabled_flow_is_absent_from_the_enum(self):
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_flow(slug="flow-on", steps=[{"tool": "stub.safe", "args": {}}])
        make_flow(slug="flow-off", enabled=False, steps=[{"tool": "stub.safe", "args": {}}])
        _a, _c, _t, payload, llm = _setup(["flow.run"])
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        schema = self._flow_run_schema(llm.calls[0][1])
        flow_param = schema["function"]["parameters"]["properties"]["flow"]
        assert flow_param["enum"] == ["flow-on"]

    def test_a_flow_added_between_two_turns_appears_in_the_second_without_a_restart(self):
        from agents.runtime.flowtool import FLOW_RUN

        register_tool(FLOW_RUN)
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        make_flow(slug="flow-first", steps=[{"tool": "stub.safe", "args": {}}])
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(slug="general", tool_keys=["flow.run"])
        conv = make_conversation(agent=agent)

        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="q1")
        assistant1 = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                               state=Turn.State.QUEUED)
        payload1 = {"conversation": str(conv.id), "turn": assistant1.pk,
                   "agent": agent.slug, "text": "q1", "connection": None, "mode": "chat"}
        llm1 = FakeToolLLM([("final", "a1")])
        with patch_llm(llm1):
            run_turn(payload1, [], make_job_ctx())
        first_enum = set(
            self._flow_run_schema(llm1.calls[0][1])["function"]["parameters"]
            ["properties"]["flow"]["enum"]
        )
        assert first_enum == {"flow-first"}

        make_flow(slug="flow-second", steps=[{"tool": "stub.safe", "args": {}}])

        assistant2 = make_turn(conversation=conv, index=2, role=Turn.Role.ASSISTANT,
                               state=Turn.State.QUEUED)
        payload2 = {"conversation": str(conv.id), "turn": assistant2.pk,
                   "agent": agent.slug, "text": "q2", "connection": None, "mode": "chat"}
        llm2 = FakeToolLLM([("final", "a2")])
        with patch_llm(llm2):
            run_turn(payload2, [], make_job_ctx())
        second_enum = set(
            self._flow_run_schema(llm2.calls[0][1])["function"]["parameters"]
            ["properties"]["flow"]["enum"]
        )
        assert second_enum == {"flow-first", "flow-second"}


class TestVisionGenerateNarrowing:
    """Fix 1b (image-model-trace.md, Follow-up 3), cleared for this
    exact callsite by the agents-column owner: `available_tools`
    narrows `vision.generate` per turn the same way it narrows
    `flow.run` above -- through
    `tools.vision.tools.narrowed_generate_spec`, reached ONLY by a
    dotted-path string (`resolve_dotted_path`), never an import
    (import-law rule 3). This module (a TEST file) may import
    `tools.vision.*` directly to set the scenario up -- the gate that
    forbids it is production-only (`foundation/ops/tests/
    test_import_law.py::test_no_agents_module_imports_a_tools_package`).
    """

    def _generate_schema(self, tools: list | None) -> dict | None:
        if not tools:
            return None
        return next((t for t in tools if t["function"]["name"] == "vision__generate"), None)

    def test_the_operation_enum_narrows_to_what_the_bound_model_supports(self):
        from models.contracts.bindings import ResolvedModel
        from models.contracts.operations import get_operation
        from models.contracts.roles import VISION_GENERATE_ROLE
        from tools.vision import services
        from tools.vision.tools import build_generate_spec

        register_tool(build_generate_spec())
        bind_chat_role(VISION_GENERATE_ROLE, name="test-vision", capability="image-generation",
                       model_id="vision-model")
        _a, _c, _t, payload, llm = _setup(["vision.generate"])

        edit = get_operation("edit")
        resolved = ResolvedModel(
            engine="ollama", model_id="vision-model", endpoint="http://localhost:11434",
        )
        with patch_llm(llm), \
             patch("tools.vision.services.preflight",
                   return_value=services.PreflightResult("ready", resolved, "")), \
             patch("tools.vision.services.operations_for_model", return_value=[edit]), \
             patch("tools.vision.services.live_defaults", return_value={}):
            run_turn(payload, [], make_job_ctx())

        schema = self._generate_schema(llm.calls[0][1])
        assert schema is not None
        operation_param = schema["function"]["parameters"]["properties"]["operation"]
        assert operation_param["enum"] == ["edit"]
        assert "This model runs: edit." in schema["function"]["description"]

    def test_an_engine_with_no_opinion_offers_the_full_union_unnarrowed(self):
        """`services.operations_for_model` returning the full registered
        list (an adapter with no `supported_operations` opinion) leaves
        the spec exactly as registered -- never rewritten to a
        no-op-in-substance copy."""
        from models.contracts.bindings import ResolvedModel
        from models.contracts.operations import all_operations
        from models.contracts.roles import VISION_GENERATE_ROLE
        from tools.vision import services
        from tools.vision.tools import build_generate_spec

        register_tool(build_generate_spec())
        bind_chat_role(VISION_GENERATE_ROLE, name="test-vision", capability="image-generation",
                       model_id="vision-model")
        _a, _c, _t, payload, llm = _setup(["vision.generate"])

        resolved = ResolvedModel(
            engine="ollama", model_id="vision-model", endpoint="http://localhost:11434",
        )
        with patch_llm(llm), \
             patch("tools.vision.services.preflight",
                   return_value=services.PreflightResult("ready", resolved, "")), \
             patch("tools.vision.services.operations_for_model",
                   return_value=all_operations()):
            run_turn(payload, [], make_job_ctx())

        schema = self._generate_schema(llm.calls[0][1])
        assert schema is not None
        operation_param = schema["function"]["parameters"]["properties"]["operation"]
        assert set(operation_param["enum"]) == {
            operation.key for operation in all_operations()
        }
        assert "This model runs:" not in schema["function"]["description"]

    def test_a_narrowing_failure_fails_open_to_the_full_union_and_warns_loudly(self, caplog):
        """Review round 1, finding 2: a SECOND, independent guard around
        the dotted-path call itself (loop.py's own docstring), layered
        on top of `narrowed_generate_spec`'s own internal try/except --
        this one catches the resolved callable failing outright (a bad
        dotted path, or -- as scripted here -- the function raising).
        Fails OPEN to the full union spec with a LOUD warning, never a
        silent swallow: a silent one here would invisibly recreate the
        incident this fix exists for -- a model offered the wrong
        operations with no one able to tell why."""
        from models.contracts.operations import all_operations
        from models.contracts.roles import VISION_GENERATE_ROLE
        from tools.vision.tools import build_generate_spec

        register_tool(build_generate_spec())
        bind_chat_role(VISION_GENERATE_ROLE, name="test-vision", capability="image-generation",
                       model_id="vision-model")
        _a, _c, _t, payload, llm = _setup(["vision.generate"])

        with patch_llm(llm), \
             patch("tools.vision.tools.narrowed_generate_spec",
                   side_effect=RuntimeError("boom")), \
             caplog.at_level(logging.WARNING, logger="agents.runtime.loop"):
            run_turn(payload, [], make_job_ctx())

        schema = self._generate_schema(llm.calls[0][1])
        assert schema is not None
        operation_param = schema["function"]["parameters"]["properties"]["operation"]
        assert set(operation_param["enum"]) == {
            operation.key for operation in all_operations()
        }
        assert any(
            "vision.generate narrowing failed" in record.message for record in caplog.records
        )


class TestProgress:
    def test_progress_is_reported_each_iteration_with_a_named_unit(self):
        reports = []
        ctx = make_job_ctx(_report=reports.append)
        _a, _c, _t, payload, llm = _setup(script=[("final", "x")])
        with patch_llm(llm):
            run_turn(payload, [], ctx)
        assert reports and reports[0]["unit"] == "items"
        assert reports[0]["total"] == 8


class TestTheLabelRecheckIsSymmetric:
    """Whole-branch review, item 4: `_run_turn` rebuilds `access` (the
    tool axis) and `model_access` (the model axis) as defence in depth,
    re-derived here rather than trusted from `plan_turn`'s enqueue-time
    snapshot -- but until this test, nothing re-asked the AGENT-LABEL
    question the same way. An agent labelled AFTER its turn was already
    queued, and BEFORE a worker claimed the job, used to run anyway.
    Same actor, same shape as `agents.runtime.preflight.preflight_turn`'s
    own `AGENT_NOT_PERMITTED` refusal (`agents/runtime/tests/
    test_preflight.py::TestARestrictedAgentIsRefusedBeforeTheTurnIsWritten`).
    """

    def test_a_label_added_after_the_turn_row_exists_refuses_at_run_time(self):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        agent = make_agent(slug="general", tool_keys=["stub.safe"], max_steps=8)
        conv = make_conversation(agent=agent)
        member = make_user()
        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
        # THE TURN ROW ALREADY EXISTS, QUEUED, before the label lands --
        # exactly the seam a run-time re-check exists for (an entitlement,
        # or here a label, changing between enqueue and run).
        assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                              state=Turn.State.QUEUED)
        payload = {"conversation": str(conv.id), "turn": assistant.pk,
                   "agent": agent.slug, "text": "a question", "connection": None,
                   "mode": "chat", "actor_kind": "user", "actor_key": str(member.pk)}
        AgentEntitlement.objects.create(agent=agent,
                                        entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            result = run_turn(payload, [], make_job_ctx())
        assistant.refresh_from_db()
        assert result["text"] == AGENT_NOT_PERMITTED_NOTE
        assert assistant.text == AGENT_NOT_PERMITTED_NOTE
        assert assistant.state == Turn.State.DONE
        assert result["steps_used"] == 0

    def test_an_unlabelled_agent_is_unaffected(self):
        """Anti-vacuous pin: the recheck itself must not refuse a turn
        that was never labelled at all -- the overwhelming majority of
        turns on any install."""
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        bind_chat_role(CHAT_CONVERSE_ROLE)
        agent = make_agent(slug="general", tool_keys=["stub.safe"], max_steps=8)
        conv = make_conversation(agent=agent)
        member = make_user()
        make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
        assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                              state=Turn.State.QUEUED)
        payload = {"conversation": str(conv.id), "turn": assistant.pk,
                   "agent": agent.slug, "text": "a question", "connection": None,
                   "mode": "chat", "actor_kind": "user", "actor_key": str(member.pk)}
        with posture(POSTURE_ENTERPRISE):
            with patch_llm(FakeToolLLM([("final", "done")])):
                result = run_turn(payload, [], make_job_ctx())
        assert result["text"] == "done"


class TestTheAttachmentsBlockReachesARealTurn:
    """NIT 2 (round 11 review): `_run_turn`'s own `stream_scope=`/
    `available=`/`settings_row=` wiring into `build_messages` was
    previously exercised only IMPLICITLY -- a wrong keyword there would
    TypeError every other test in this file (which is why the reviewer
    called it a nit and not more), but nothing actually asserted the
    attachments block reaches a REAL turn's first LLM call end to end.
    This pins that, through the SAME `run_turn` -> `_run_turn` path
    every other test in this file exercises, not a direct `build_
    messages` call."""

    def test_a_real_turn_carries_the_attachments_block_to_the_llm(self):
        _a, conv, _t, payload, llm = _setup()
        doc = make_document(title="Notes.pdf", status="ready")
        DocumentAttachment.objects.create(document=doc, conversation_id=conv.id)
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        system_content = llm.calls[0][0][0].content
        assert "Notes.pdf" in system_content
        assert "attached to this conversation" in system_content


class TestTheCarryingAttachmentsBlockReachesARealTurn:
    """OWNER ADDENDUM (2026-09-08): the SAME end-to-end shape
    `TestTheAttachmentsBlockReachesARealTurn` above pins for round 11's
    own steering block, applied to the addendum's full-text inject --
    `_run_turn` resolves `carrying_turn_id` itself (the user turn
    `_setup()` already creates at index 0, immediately before the
    ASSISTANT placeholder at index 1) and threads it into `build_
    messages`; this proves that wiring survives the REAL `run_turn ->
    _run_turn -> build_messages -> gateway.get_llm_for` path, not only
    a direct `build_messages` call (`agents/runtime/tests/test_prompt.
    py::TestTheCarryingAttachmentsBlock` covers the block's own
    budget/labelling logic in isolation)."""

    def test_the_carrying_turns_attachment_reaches_the_llms_first_call(self, monkeypatch):
        monkeypatch.setattr(
            "agents.attachments.inline_attachment_text",
            lambda doc_id: "the roof needs replacing by spring",
        )
        _a, conv, _assistant, payload, llm = _setup()
        user_turn = Turn.objects.get(conversation=conv, role=Turn.Role.USER)
        doc = make_document(title="Inspection.pdf", status="ready")
        DocumentAttachment.objects.create(document=doc, conversation_id=conv.id,
                                          turn_id=user_turn.pk)
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        # I-1(a) (round-13 review fix): the carrying block rides its OWN
        # trailing USER-role message now, never folded into the system
        # prompt (`agents/runtime/tests/test_prompt.py`'s own identical
        # update has the full reasoning).
        carrying_content = llm.calls[0][0][-1].content
        assert "Attached file: Inspection.pdf" in carrying_content
        assert "the roof needs replacing by spring" in carrying_content

    def test_a_later_turns_own_call_does_not_replay_the_full_text(self, monkeypatch):
        """Ruling 3: a SECOND turn answering after the carrying one must
        see only the reference line (round 11's own steering sentence),
        never the full text a second time."""
        monkeypatch.setattr(
            "agents.attachments.inline_attachment_text",
            lambda doc_id: "the roof needs replacing by spring",
        )
        agent, conv, assistant, payload, llm = _setup()
        user_turn = Turn.objects.get(conversation=conv, role=Turn.Role.USER)
        doc = make_document(title="Inspection.pdf", status="ready")
        DocumentAttachment.objects.create(document=doc, conversation_id=conv.id,
                                          turn_id=user_turn.pk)
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())

        # A SECOND turn, answering after the first -- the carrying turn
        # is now history, not the live one.
        second_user = make_turn(conversation=conv, index=2, role=Turn.Role.USER,
                                text="a follow-up")
        second_assistant = make_turn(conversation=conv, index=3, role=Turn.Role.ASSISTANT,
                                     state=Turn.State.QUEUED)
        second_payload = {"conversation": str(conv.id), "turn": second_assistant.pk,
                          "agent": agent.slug, "text": "a follow-up",
                          "connection": None, "mode": "chat"}
        llm2 = FakeToolLLM((("final", "done"),))
        with patch_llm(llm2):
            run_turn(second_payload, [], make_job_ctx())
        system_content = llm2.calls[0][0][0].content
        assert "the roof needs replacing by spring" not in system_content
        assert "Inspection.pdf" in system_content

    def test_the_acceptance_scenario_attach_and_ask_answers_with_no_rag_tool_call(
            self, monkeypatch):
        """ROUND-13 REVIEW FIX, MINOR 5: "no test pins the addendum's
        own acceptance scenario ('attach + ask in the same message
        answers with NO rag tool call in the trace'); the loop tests
        prove the text reaches the first call, not that the answer
        needs no tool." The gap this test closes: `rag.search`/
        `rag.ask` are GRANTED and REGISTERED (genuinely OFFERED to the
        model, unlike every other test in this class, which never grants
        the agent any tool at all -- so "no call happened" there proves
        nothing about whether one was AVAILABLE to skip), the fake
        model's OWN script answers directly on its first turn with no
        tool step, and the platform's own AUDIT TRAIL (`ToolInvocation`,
        never merely the message text) shows zero rows -- the turn
        completed with a real, available retrieval tool sitting unused
        because the carrying block already gave the model what it
        needed, not because nothing was ever offered.
        """
        register_tool(ToolSpec(key="rag.search", label="Search", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        register_tool(ToolSpec(key="rag.ask", label="Ask", description="d",
                               runner="agents.runtime.tests._helpers.runner_ok"))
        monkeypatch.setattr(
            "agents.attachments.inline_attachment_text",
            lambda doc_id: "the roof needs replacing by spring",
        )
        _a, conv, _assistant, payload, llm = _setup(
            ["rag.search", "rag.ask"], script=(("final", "the roof, got it"),))
        user_turn = Turn.objects.get(conversation=conv, role=Turn.Role.USER)
        doc = make_document(title="Inspection.pdf", status="ready")
        DocumentAttachment.objects.create(document=doc, conversation_id=conv.id,
                                          turn_id=user_turn.pk)
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert len(llm.calls) == 1  # answered on the FIRST call -- no tool round at all
        assert ToolInvocation.objects.count() == 0
        assistant = Turn.objects.get(conversation=conv, role=Turn.Role.ASSISTANT)
        assert assistant.state == Turn.State.DONE
        assert assistant.text == "the roof, got it"


class TestTheResolvedModelReachesBuildMessages:
    """Task 3's own threading pin: `_run_turn` already holds `resolved`
    (`resolve_chat`'s own return, used just above to pick `gateway.
    get_llm_for(resolved)`) and now passes the SAME value into `build_
    messages` too, rather than a second resolution -- the identical
    "wiring reaches a real turn" shape `TestTheAttachmentsBlockReaches
    ARealTurn` above already pins for `principal=`/`stream_scope=`/
    `available=`."""

    def test_resolved_reaches_build_messages(self, monkeypatch):
        from agents.runtime.prompt import build_messages as _real_build_messages

        captured = {}

        def _capture(*args, **kwargs):
            captured.update(kwargs)
            return _real_build_messages(*args, **kwargs)

        monkeypatch.setattr("agents.runtime.loop.build_messages", _capture)
        _a, _conv, _t, payload, llm = _setup()
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        assert "resolved" in captured
        assert captured["resolved"].model_id == "chat-model"  # `_setup`'s own `bind_chat_role`


class TestNativeImageInputReachesARealTurn:
    """Task 3's own end-to-end pin, the identical shape `TestThe
    CarryingAttachmentsBlockReachesARealTurn` above already takes for
    the text-inline path: this proves the gate-open wiring survives the
    REAL `run_turn -> _run_turn -> build_messages` path, not only a
    direct `_carrying_attachments_block` call (`agents/runtime/tests/
    test_prompt_native_images.py::TestNativeImageBlocks` covers the
    block's own bounds/fallback logic in isolation).

    `llava:7b` is a REAL, PERMANENT `models.contracts.catalog` entry
    (`capability="vision"`, `accepts=("image",)`) -- no temporary
    catalog mutation needed here, unlike the isolated-unit tests, since
    Task 3's own gate never reads `capability`, only `accepts`."""

    def test_a_real_turn_attaches_a_native_image_block(self, monkeypatch, tmp_path):
        from llama_index.core.llms import ImageBlock

        path = tmp_path / "photo.png"
        path.write_bytes(b"stand-in image bytes")
        monkeypatch.setattr(
            "agents.attachments.attachment_image_path",
            lambda doc_id, principal: str(path),
        )
        monkeypatch.setattr("agents.attachments.inline_attachment_text", lambda doc_id: "")
        bind_chat_role(CHAT_CONVERSE_ROLE, model_id="llava:7b")
        agent = make_agent(slug="general2", tool_keys=[], max_steps=8)
        conv = make_conversation(agent=agent)
        user_turn = make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="hi")
        assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                              state=Turn.State.QUEUED)
        payload = {"conversation": str(conv.id), "turn": assistant.pk,
                  "agent": agent.slug, "text": "hi", "connection": None, "mode": "chat"}
        doc = make_document(title="photo.png", media_type="image/png", status="ready")
        DocumentAttachment.objects.create(document=doc, conversation_id=conv.id,
                                          turn_id=user_turn.pk)
        llm = FakeToolLLM((("final", "I can see it"),))
        with patch_llm(llm):
            run_turn(payload, [], make_job_ctx())
        carrying_message = llm.calls[0][0][-1]
        images = [b for b in carrying_message.blocks if isinstance(b, ImageBlock)]
        assert len(images) == 1
        assert "attached image provided to you directly" in carrying_message.content
