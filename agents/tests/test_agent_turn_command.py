"""The CLI proof surface, and the preflight that keeps a doomed turn out
of the queue.

Vision-flag rule: these tests run a management command and never call
`reverse()` or the test `Client`, so they are not subject to the
keep-vision clause -- but none of them overrides the flag either.
"""
from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from agents.models import Conversation, Turn
from agents.tests._helpers import (  # noqa: F401 -- a fixture is invisible to
    _finished_job, _write_assistant, _write_tool_turn,   # pytest until a test
    bound_chat_role, fake_failed_queue, fake_queue,        # module imports it
    fake_queue_with_tool, fake_running_queue, make_agent,
)

pytestmark = pytest.mark.django_db


class TestPreflight:
    def test_an_unknown_agent_is_refused_before_anything_is_written(self):
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "nope", "hello")
        assert "nope" in str(exc.value)
        assert Conversation.objects.count() == 0

    def test_a_disabled_agent_is_refused(self):
        make_agent(slug="off", enabled=False)
        with pytest.raises(CommandError):
            call_command("agent_turn", "off", "hello")

    def test_a_blank_message_is_refused(self):
        make_agent(slug="general")
        with pytest.raises(CommandError):
            call_command("agent_turn", "general", "   ")

    def test_an_unbound_chat_role_is_refused_and_names_the_role(self, monkeypatch):
        """Section 10.1's "chat role unbound" row. No page exists yet, so
        the command is where it surfaces -- and it surfaces BEFORE the
        enqueue, so a turn that cannot possibly run never becomes a
        queued job somebody has to cancel."""
        make_agent(slug="general", llm_role="nothing.bound.here")
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello")
        assert "nothing.bound.here" in str(exc.value)
        assert Turn.objects.count() == 0

    def test_a_model_that_cannot_call_tools_is_refused_when_the_agent_has_tools(
            self, monkeypatch, bound_chat_role):
        """Section 10.1's "bound model cannot call tools" row. The LOOP
        also handles this (Task 8) as belt-and-braces; refusing here is
        what keeps the honest message in front of the person who typed
        the command rather than buried in a finished turn.

        Patched on `agents.runtime.loop`, not on this command's own
        module: `_preflight` now delegates to `agents.runtime.
        preflight.preflight_turn`, which looks the function up on
        `loop_module` at call time (Task 9). The command no longer
        imports the name at all, so a patch on its own module would no
        longer be on the call path."""
        monkeypatch.setattr(
            "agents.runtime.loop.supports_tool_calling", lambda r: False
        )
        make_agent(slug="general", tool_keys=["rag.search"])
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello")
        assert "cannot call tools" in str(exc.value)

    def test_a_dead_connection_pk_is_refused_with_the_registered_model_copy(
            self, bound_chat_role):
        """DECLARED BEHAVIOUR CHANGE (Task 9): `resolve_chat` raises the
        same `ValueError` for an unbound role and for a picked
        connection that no longer resolves, and this command used to
        report both with one "Could not resolve a chat model" sentence.
        `preflight_turn` splits them -- a picked connection that is gone
        now gets `tools/rag/views.py`'s own copy ("That model is no
        longer registered"), which is a different, and truer, sentence
        than the role-naming one an unbound role still gets."""
        make_agent(slug="general")
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello", connection="999999")
        assert "no longer registered" in str(exc.value)
        assert Turn.objects.count() == 0

    def test_a_no_tool_agent_is_NOT_refused_by_the_tool_calling_gate(
            self, monkeypatch, bound_chat_role, fake_queue):
        """An agent with no tools does not need a tool-capable model, and
        refusing it would be a lie about what it needs."""
        monkeypatch.setattr(
            "agents.runtime.loop.supports_tool_calling", lambda r: False
        )
        make_agent(slug="plain", tool_keys=[])
        call_command("agent_turn", "plain", "hello")
        assert Turn.objects.filter(role=Turn.Role.USER).count() == 1

    def test_an_unavailable_queue_is_refused_with_operator_copy(
            self, monkeypatch, bound_chat_role):
        """`QueueUnavailable` (`models/contracts/queue.py:51`) means "no
        queue", which must never look like "no job"."""
        from models.contracts.queue import QueueUnavailable

        make_agent(slug="general")
        monkeypatch.setattr(
            "agents.management.commands.agent_turn.enqueue",
            lambda *a, **k: (_ for _ in ()).throw(QueueUnavailable("down")),
        )
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello")
        assert "migrate" in str(exc.value).lower() or "queue" in str(exc.value).lower()

    def test_a_malformed_conversation_id_is_refused_not_a_traceback(self, bound_chat_role):
        make_agent(slug="general")
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello", conversation="not-a-uuid")
        assert "not-a-uuid" in str(exc.value)
        assert Conversation.objects.count() == 0

    def test_a_conversation_belonging_to_a_different_agent_is_refused(
            self, bound_chat_role, fake_queue):
        """A mismatch is refused, naming BOTH slugs: the job would
        otherwise run as `conversation.agent` -- the row the handler
        actually reads -- while this command's own preflight validated a
        different agent's model and tools against it."""
        make_agent(slug="general")
        make_agent(slug="other")
        call_command("agent_turn", "general", "hello")
        conv = Conversation.objects.get()
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "other", "hello again", conversation=str(conv.id))
        assert "general" in str(exc.value) and "other" in str(exc.value)

    def test_a_walled_stream_conversation_threads_its_wall_through_preflight(
            self, bound_chat_role, fake_queue, monkeypatch):
        """THE CLI'S OWN WIRING, driven through THE COMMAND CLASS -- not
        `preflight_turn` called directly, which `agents/tests/
        test_wall_seams.py::test_all_four_call_sites_narrow` already
        covers and which cannot see a regression in THIS file's own
        `handle()`/`_preflight` order (Task 10 review finding: no test
        drove this command's own wiring at all). `handle()` resolves an
        EXISTING `--conversation` row before `_preflight` runs (a READ,
        so "before anything is written" still holds -- see
        `_preflight`'s own docstring) so the row it hands to
        `preflight_turn` must be the ONE that answers a non-empty
        `wall_for`, not a copy, not `None`, and not some other row.

        Spies on `preflight_turn` at the point THIS COMMAND calls it,
        rather than mocking it away, so the second turn still runs for
        real end-to-end (`fake_queue`): an edit that stops passing
        `conversation=` at all is caught by the `is not None` assertion
        below, and an edit that passes the WRONG conversation is caught
        by the `wall_for` equality on the ROW THE SPY ACTUALLY SAW,
        re-fetched from the database rather than the local `conv` object
        this test built -- so the assertion only holds if the command's
        own DB round trip really carried the workstream through.
        """
        from agents.entitlements import wall_for
        from agents.models import WorkstreamScopeEntitlement
        from agents.runtime.preflight import preflight_turn as real_preflight_turn
        from agents.tests._helpers import _workstream
        from identity.testing import make_entitlement, posture

        make_agent(slug="general")
        call_command("agent_turn", "general", "first")
        conv = Conversation.objects.get()
        ent = make_entitlement()
        stream = _workstream()
        WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
        conv.workstream = stream
        conv.save(update_fields=["workstream"])

        seen = {}

        def _spy(agent, connection, *, actor, conversation=None):
            seen["conversation"] = conversation
            return real_preflight_turn(agent, connection, actor=actor,
                                       conversation=conversation)

        monkeypatch.setattr("agents.management.commands.agent_turn.preflight_turn", _spy)

        with posture("enterprise"):
            # THE WORLD UNDER TEST REALLY IS WALLED, asserted before the
            # command runs at all -- a `wall_for` that lied about the
            # fixture would make the assertion below pass for the wrong
            # reason.
            assert wall_for(conv) == frozenset({ent.pk})
            call_command("agent_turn", "general", "second", conversation=str(conv.id))

            assert seen["conversation"] is not None
            assert seen["conversation"].pk == conv.pk
            # STILL INSIDE `enterprise`: ruling A forces `wall_for` to
            # `frozenset()` on an open box regardless of what the table
            # holds, so this equality must be read under the SAME
            # posture the command itself ran under, or it would prove
            # nothing.
            assert wall_for(seen["conversation"]) == frozenset({ent.pk})


class TestRowsAndPayload:
    def test_it_writes_a_user_turn_and_the_assistant_row_reaches_done_end_to_end(
            self, bound_chat_role, fake_queue):
        """`fake_queue`'s writeback runs SYNCHRONOUSLY inside `enqueue()`
        (by design -- see its docstring in `_helpers.py`), so by the time
        this command returns the assistant row has already been carried
        from QUEUED to DONE, exactly as a real worker would leave it.
        `test_the_placeholder_exists_BEFORE_the_enqueue` below is what
        actually proves the row is QUEUED at write time, before the
        enqueue call; this test proves the two-row shape survives an
        end-to-end run."""
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        conv = Conversation.objects.get()
        roles = [(t.role, t.state) for t in conv.turns.all()]
        assert roles == [
            (Turn.Role.USER, Turn.State.DONE),
            (Turn.Role.ASSISTANT, Turn.State.DONE),
        ]

    def test_the_placeholder_exists_BEFORE_the_enqueue(self, bound_chat_role, monkeypatch):
        """Otherwise a cancel between enqueue and write finds nothing to
        flip, and the hook that exists for exactly that case cannot fire.
        """
        seen = {}

        def _enqueue(kind, payload, **kwargs):
            seen["turns"] = Turn.objects.count()
            return 1

        monkeypatch.setattr("agents.management.commands.agent_turn.enqueue", _enqueue)
        monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                            lambda job_id: _finished_job())
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        assert seen["turns"] == 2

    def test_the_placeholder_carries_the_queue_job_id_before_any_worker_runs(
            self, bound_chat_role, monkeypatch):
        """Stamped right after `enqueue()` returns, like `GenerationJob`
        (`tools/vision/jobs.py:323-324`) -- a NON-synchronous double, so
        the row must carry it even though nothing has "run" yet."""
        monkeypatch.setattr("agents.management.commands.agent_turn.enqueue",
                            lambda kind, payload, **kw: 7)
        monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                            lambda job_id: _finished_job())
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        assert placeholder.queue_job_id == 7

    def test_the_payload_is_json_safe_and_carries_the_documented_keys(
            self, bound_chat_role, monkeypatch):
        import json

        captured = {}
        monkeypatch.setattr(
            "agents.management.commands.agent_turn.enqueue",
            lambda kind, payload, **kw: (captured.update(kind=kind, payload=payload), 1)[1],
        )
        monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                            lambda job_id: _finished_job())
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        assert captured["kind"] == "agent.turn"
        assert set(captured["payload"]) == {
            "conversation", "turn", "agent", "text", "connection", "mode",
            "actor_kind", "actor_key",
        }
        assert captured["payload"]["mode"] == "chat"
        json.dumps(captured["payload"])          # JSON-safe: no ORM objects, no paths

    def test_a_cli_turn_records_the_one_service_principal(self, bound_chat_role, monkeypatch):
        """Task 10, the acting rule part 1: this command builds its own
        payload (rather than going through `agents.chat.service.
        start_turn`), so it needs its own assertion -- the two dicts are
        two places the same two keys can be forgotten."""
        captured = {}
        monkeypatch.setattr(
            "agents.management.commands.agent_turn.enqueue",
            lambda kind, payload, **kw: captured.update(payload) or 1,
        )
        monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                            lambda job_id: _finished_job())
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        assert (captured["actor_kind"], captured["actor_key"]) == ("service", "local")

    def test_a_conversation_id_continues_an_existing_thread(
            self, bound_chat_role, fake_queue):
        make_agent(slug="general")
        call_command("agent_turn", "general", "first")
        conv = Conversation.objects.get()
        call_command("agent_turn", "general", "second", conversation=str(conv.id))
        assert Conversation.objects.count() == 1
        assert conv.turns.count() == 4

    def test_the_title_comes_from_the_first_user_turn_not_from_a_model(
            self, bound_chat_role, fake_queue):
        """A generated title would be a second, invisible model call per
        conversation."""
        make_agent(slug="general")
        call_command("agent_turn", "general", "what does the library say about attention?")
        assert Conversation.objects.get().title.startswith("what does the library say")

    def test_a_shell_turn_owns_its_conversation_as_the_service_principal(
            self, bound_chat_role, fake_queue):
        """Task 10, the acting rule part 1: a shell path acts as the ONE
        service principal (spec section 5.3 item 8), and it must own what
        it creates as that principal too -- otherwise this one command
        writes two different answers to "who did this" (the row saying
        the box, the job saying the shell). One command, ONE answer: the
        row and the payload carry the same principal."""
        from identity.contracts.principals import SERVICE_PRINCIPAL

        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        conv = Conversation.objects.get()
        assert (conv.owner_kind, conv.owner_key) == (SERVICE_PRINCIPAL.kind, SERVICE_PRINCIPAL.key)


class TestPollingAndOutput:
    def test_it_prints_the_assistant_text(self, bound_chat_role, fake_queue, capsys):
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        assert "the answer" in capsys.readouterr().out

    def test_it_prints_each_tool_call_with_its_outcome(self, bound_chat_role,
                                                       fake_queue_with_tool, capsys):
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        out = capsys.readouterr().out
        assert "rag.search" in out and "ok" in out

    def test_a_second_turn_on_a_continued_conversation_prints_only_its_own_tool_calls(
            self, bound_chat_role, monkeypatch, capsys):
        """`_report` filters by THIS turn's `queue_job_id`, not
        `index__lt` over the whole conversation -- a continued
        conversation's second turn must never print a tool call that
        belongs to the FIRST turn's job. Two enqueues, two distinct job
        ids, two distinct tool keys, so the assertion actually proves
        which filter ran rather than passing by coincidence."""
        job_ids = iter([1, 2])

        def _enqueue(kind, payload, **kwargs):
            job_id = next(job_ids)
            _write_tool_turn(payload, tool_key=f"tool.job{job_id}", queue_job_id=job_id)
            _write_assistant(payload, text=f"answer {job_id}")
            return job_id

        monkeypatch.setattr("agents.management.commands.agent_turn.enqueue", _enqueue)
        monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                            lambda job_id: _finished_job())
        make_agent(slug="general")
        call_command("agent_turn", "general", "first")
        capsys.readouterr()  # discard the first turn's own output
        conv = Conversation.objects.get()
        call_command("agent_turn", "general", "second", conversation=str(conv.id))
        out = capsys.readouterr().out
        assert "tool.job2" in out
        assert "tool.job1" not in out

    def test_a_failed_job_prints_the_error_and_exits_non_zero(self, bound_chat_role,
                                                             fake_failed_queue):
        make_agent(slug="general")
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello")
        assert "boom" in str(exc.value)

    def test_polling_stops_at_the_timeout_and_says_the_job_is_still_running(
            self, bound_chat_role, fake_running_queue):
        """It does NOT cancel the job. The turn may still finish; the
        command simply stops waiting and says where to look."""
        make_agent(slug="general")
        with pytest.raises(CommandError) as exc:
            call_command("agent_turn", "general", "hello", timeout=0)
        assert "still" in str(exc.value).lower()

    def test_a_timed_out_turn_prints_the_error_not_a_stale_success(
            self, bound_chat_role, monkeypatch, capsys):
        """MINOR 5 (final review, fix round 3): a timed-out turn's own
        JOB still reads SUCCEEDED -- the handler ran to completion; it
        only wrote its OWN turn row FAILED (`agents.runtime.loop.
        _finish`, one-timeout task) -- so `_wait`'s own `status.state ==
        FAILED` check (the QUEUE job's state) never fires, and `_report`
        is reached exactly as it is for a real answer. Before this fix
        `_report` printed `turn.text` through `self.style.SUCCESS`
        unconditionally; it must now print `turn.error` instead, for a
        FAILED turn, whatever the job itself says."""
        def _run(payload):
            Turn.objects.filter(pk=payload["turn"]).update(
                state=Turn.State.FAILED,
                error="The response ran out of time before it finished.",
                text="This turn hit its time limit before I reached an answer.",
            )

        monkeypatch.setattr("agents.management.commands.agent_turn.enqueue",
                            lambda kind, payload, **kwargs: (_run(payload), 1)[1])
        monkeypatch.setattr("agents.management.commands.agent_turn.get_job",
                            lambda job_id: _finished_job())
        make_agent(slug="general")
        call_command("agent_turn", "general", "hello")
        out = capsys.readouterr().out
        assert "The response ran out of time before it finished." in out
        assert "This turn hit its time limit" not in out


class TestFixtureVocabulary:
    """`_finished_job`'s default `state` once spelled "done" -- a state
    the real queue vocabulary has never had (queued/running/succeeded/
    failed/cancelled, `models/queue/models.py:15-19`) -- and every
    output/polling test above passed against that wrong literal, because
    the fixtures agreed with the command's own typo instead of with the
    queue. This pins each of the four `_patch_queue`-based fixtures'
    `get_job().state` to the real vocabulary, reached through the
    sanctioned seam (`models.contracts.queue`, never `models.queue.*`
    directly -- import-law rule 2) so a fixture can never quietly drift
    from it again.
    """

    @pytest.mark.parametrize("fixture_name", [
        "fake_queue", "fake_queue_with_tool", "fake_failed_queue", "fake_running_queue",
    ])
    def test_every_fixtures_job_state_is_in_the_real_vocabulary(
            self, fixture_name, request, bound_chat_role):
        from models.contracts.queue import CANCELLED, FAILED, QUEUED, RUNNING, SUCCEEDED
        from agents.management.commands import agent_turn

        request.getfixturevalue(fixture_name)
        status = agent_turn.get_job(1)
        assert status.state in {QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED}
