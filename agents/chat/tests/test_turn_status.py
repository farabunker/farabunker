"""`turn_status` -- always 200 for a readable turn.

NO FARABUNKER_FEATURES OVERRIDE (see test_mount.py).
"""
from __future__ import annotations

import json

import pytest
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from agents.chat.views import turns as turns_module
from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    fake_queued_job, fake_running_job, make_admin, make_agent, make_conversation, make_turn,
    make_user, posture, sign_in, user_principal,
)
from agents.models import ToolInvocation, Turn
from agents.runtime.tests.test_jobs import _assistant_turn, _stranded_assistant_turn
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE
from models.contracts.queue import QueueUnavailable

pytestmark = pytest.mark.django_db


def _status(client, turn):
    response = client.get(reverse("chat-turn-status", args=[turn.pk]))
    return response.status_code, json.loads(response.content)


class TestTheStateVocabulary:
    def test_it_reports_turn_states_and_never_queue_states(self, client, monkeypatch):
        """DEVIATION P3-D8, and the reason it is a deviation: the queue
        says "succeeded" and a turn says "done"
        (`models/contracts/queue.py:69-80` vs `Turn.State`). P2's
        ledger records a live 960-second hang from exactly this drift.
        Asserted against `Turn.State.DONE` itself rather than a literal
        typed a second time."""
        conversation = make_conversation()
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text="the answer", state=Turn.State.DONE)
        code, body = _status(client, turn)
        assert code == 200
        assert body["state"] == Turn.State.DONE.value
        assert body["state"] != "succeeded"

    def test_the_five_states_are_the_only_ones_it_can_report(self, client):
        """Anti-vacuous pin: a sixth `Turn.State` added later must fail
        HERE, where the body shapes are decided, rather than silently
        falling through to a done body with no html in it."""
        from agents.chat.views.turns import _BODY_BUILDERS

        assert set(_BODY_BUILDERS) == {s.value for s in Turn.State}


class TestPerState:
    def test_queued_reports_its_place_in_the_line(self, client, fake_queued_job):
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                         queue_job_id=1)
        _code, body = _status(client, turn)
        assert body["state"] == "queued" and body["position"] == 3

    def test_queued_also_carries_the_users_own_message(self, client, fake_queued_job):
        """D1 (chat-polish P3.1): the poller's swap must show what a
        full reload already shows -- the user's own bubble alongside
        the still-queued placeholder, not just the placeholder."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="what is the paper about?", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.QUEUED, queue_job_id=1)
        _code, body = _status(client, turn)
        assert "what is the paper about?" in body["html"]

    def test_running_reports_the_progress_dict_the_job_wrote(self, client,
                                                            fake_running_job):
        """`{"done","total","unit","label"}` -- `report_progress`'s own
        shape, passed through unchanged, exactly as
        `AskJobStatusView`'s running body does."""
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.RUNNING,
                         queue_job_id=1)
        _code, body = _status(client, turn)
        assert body["progress"]["unit"] == "items"
        # `step` and `label` are lifted OUT of the dict as well as
        # passed through in it (spec section 8.3): the script shows
        # them without having to know the progress dict's shape, and
        # `/queue/` keeps rendering the same dict unchanged.
        assert body["step"] == body["progress"]["done"]
        assert body["label"] == "thinking"

    def test_running_also_carries_the_users_own_message(self, client, fake_running_job):
        """D1: the running body's own fragment must contain the user's
        text too, not just the eventual done body's."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="what is the paper about?", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.RUNNING, queue_job_id=1)
        _code, body = _status(client, turn)
        assert "what is the paper about?" in body["html"]

    def test_the_queued_and_running_blocks_share_one_poll_block_marker_with_the_user_card(
        self, client, fake_running_job
    ):
        """The user's card is grouped under the SAME `data-poll-block`
        marker as the running placeholder (D1) -- not its own row's
        `queue_job_id`, which is always `NULL` -- so a later poll tick's
        idempotent swap removes the stale copy of BOTH before inserting
        the fresh pair, never leaving two user bubbles behind."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="what is the paper about?", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.RUNNING, queue_job_id=1)
        _code, body = _status(client, turn)
        assert body["html"].count('data-poll-block="1"') == 2

    def test_done_carries_the_rendered_card(self, client):
        """The SAME `_turn_card.html` the page rendered inline, from
        the SAME `rendering.thread_cards` -- so a polled answer and a
        refreshed one are byte-identical."""
        conversation = make_conversation()
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text="the answer", state=Turn.State.DONE)
        _code, body = _status(client, turn)
        assert "the answer" in body["html"]

    def test_done_carries_the_tool_cards_this_job_wrote_too(self, client):
        """M6. A finished turn is the USER message, the TOOL cards the
        loop wrote, PLUS the answer (D1, chat-polish P3.1, extended
        this pin to include the user's own message: a poller that
        swapped in only the answer -- or only the job's own rows --
        showed strictly less than the same page shows after a
        refresh)."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="the question", state=Turn.State.DONE)
        make_turn(conversation=conversation, role=Turn.Role.TOOL,
                  text="two results", state=Turn.State.DONE, queue_job_id=1,
                  tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                             "id": "", "discarded": []})
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text="the answer", state=Turn.State.DONE, queue_job_id=1)
        _code, body = _status(client, turn)
        assert "two results" in body["html"]
        assert "the answer" in body["html"]
        # D1: the message that provoked it is now IN the block -- the
        # poller's whole reason for existing this task is that a full
        # reload always showed it and the polled swap never did.
        assert "the question" in body["html"]

    def test_the_done_block_carries_the_poll_block_marker_the_script_dedupes_on(
        self, client
    ):
        """Review round 2. Every card in the done block shares ONE
        `data-poll-block` value -- the job's `queue_job_id` -- which is
        the marker `conversation.html`'s poller uses to find and remove
        every stale copy of this job's cards (ones already on the page
        from an earlier full-thread render) before inserting this
        block, rather than replacing only the one pending placeholder
        it was polling and leaving a duplicate behind."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.TOOL,
                  text="two results", state=Turn.State.DONE, queue_job_id=1,
                  tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                             "id": "", "discarded": []})
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text="the answer", state=Turn.State.DONE, queue_job_id=1)
        _code, body = _status(client, turn)
        assert body["html"].count('data-poll-block="1"') == 2

    def test_a_done_turn_with_no_job_stamp_renders_itself_alone(self, client):
        """N4. `thread_cards(conversation, queue_job_id=None)` means
        "the whole thread", so a turn whose stamp never landed would
        swap the ENTIRE conversation into the page in place of one
        card. Silent duplication, not an error -- which is why the
        guard is explicit and why this test exists."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="the question", state=Turn.State.DONE)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text="the answer", state=Turn.State.DONE,
                         queue_job_id=None)
        _code, body = _status(client, turn)
        assert body["html"].count('class="turn ') == 1
        assert "the question" not in body["html"]

    def test_a_delegates_steps_are_nested_in_the_polled_block_too(self, client):
        """Anti-vacuous pin on reusing `thread_cards` rather than
        looping over `turn_card`: the grouper is what nests depth-1
        turns, and a hand-rolled loop in the poll view would render
        them flat -- so a polled thread and a refreshed one would
        disagree about structure while agreeing about text.

        Row shape mirrors `test_rendering.py::
        test_a_delegates_turns_nest_under_the_delegating_tool_turn`: the
        depth-1 delegate step is written BEFORE its parent's own TOOL
        row -- the real order `run_loop` uses, since `invoke_tool` runs
        the whole delegate loop first -- and every row this job wrote
        carries the SAME `queue_job_id` so `_done_body`'s
        `thread_cards(..., queue_job_id=...)` filter picks up all of
        them.
        """
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.USER,
                  text="the question", state=Turn.State.DONE)
        make_turn(
            conversation=conversation, role=Turn.Role.TOOL, depth=1,
            text="the library searched", state=Turn.State.DONE, queue_job_id=1,
            invocation=ToolInvocation.objects.create(
                principal_kind="resident_agent", principal_key="library",
                tool_key="rag.search", outcome=ToolInvocation.Outcome.OK,
                text="the library searched", finished_at=timezone.now(),
            ),
            tool_call={"tool": "rag.search", "args": {}, "agent": "library",
                       "id": "", "discarded": []},
        )
        make_turn(
            conversation=conversation, role=Turn.Role.TOOL, depth=0,
            text="the library said…", state=Turn.State.DONE, queue_job_id=1,
            invocation=ToolInvocation.objects.create(
                principal_kind="resident_agent", principal_key="general",
                tool_key="agent.library", outcome=ToolInvocation.Outcome.OK,
                text="the library said…", finished_at=timezone.now(),
            ),
            tool_call={"tool": "agent.library", "args": {"task": "t"},
                       "agent": "general", "id": "", "discarded": []},
        )
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         text="the answer", state=Turn.State.DONE, queue_job_id=1)

        _code, body = _status(client, turn)
        html = body["html"]

        # The nested marker exists at all, and there is exactly ONE of
        # them -- the depth-1 turn is buffered under its parent, never
        # rendered as its own top-level card.
        assert html.count('<details class="turn-nested">') == 1
        # And it sits INSIDE the parent's own tool card, not before it
        # and not as a sibling: the parent's tool card opens, THEN the
        # nested disclosure opens, THEN the nested turn's own text
        # appears -- the containment a hand-rolled flat loop would get
        # wrong.
        tool_open = html.index('class="tool-card')
        details_open = html.index('<details class="turn-nested">')
        nested_text = html.index("the library searched")
        assert tool_open < details_open < nested_text
        # D1: the user's own message is now part of the block (see
        # `test_done_carries_the_tool_cards_this_job_wrote_too`'s own
        # update), and it comes BEFORE the tool card in the group --
        # `turn_group_cards` prepends it, matching the order a full
        # reload already renders turns in.
        assert "the question" in html
        assert html.index("the question") < tool_open

    def test_failed_carries_the_error_and_a_setup_url_on_every_failure(self, client):
        """`setup_url` on EVERY failure, not only model-shaped ones --
        `AskJobStatusView`'s own rule (`tools/rag/views.py:1177-1185`):
        the simplest honest rule, and it never depends on the stored
        error's exact wording staying stable."""
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.FAILED,
                         error="the worker stopped responding")
        _code, body = _status(client, turn)
        assert body["error"] == "the worker stopped responding"
        assert body["setup_url"] == reverse("inference-console")

    def test_cancelled_says_so(self, client):
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.CANCELLED,
                         error="Cancelled from the queue before it ran.")
        _code, body = _status(client, turn)
        assert body["state"] == "cancelled"


class TestOnTimeoutAdminHintOnThePollPath:
    """B3 (fix round 1, one-timeout task): `conversation.html`'s poller
    (HELD by PR #84) never swaps in `_turn_card.html`'s own admin-hint
    HTML for a FAILED turn -- `showCardError` rebuilds the card from
    `data.error`/`data.setup_url` alone, the same way it does for every
    OTHER failure, and reads no `html` key at all for this state. So
    `_failed_body` delivers the SAME hint a different way: appended, as
    plain text, onto `data.error` itself, which the poller already
    renders verbatim -- an admin watching a timed-out turn live sees the
    hint without waiting for a reload, even though the reload's own
    rendering additionally carries a real link this one cannot."""

    def test_an_admin_sees_the_hint_appended_to_the_polled_error(self, client):
        from agents.runtime.loop import TURN_TIMEOUT_ADMIN_HINT, TURN_TIMEOUT_ERROR

        admin = make_admin()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(admin), agent)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.FAILED, error=TURN_TIMEOUT_ERROR)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            _code, body = _status(client, turn)
        assert body["error"] == f"{TURN_TIMEOUT_ERROR} {TURN_TIMEOUT_ADMIN_HINT}"

    def test_a_member_sees_the_polled_error_unchanged(self, client):
        from agents.runtime.loop import TURN_TIMEOUT_ADMIN_HINT, TURN_TIMEOUT_ERROR

        member = make_user()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(member), agent)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.FAILED, error=TURN_TIMEOUT_ERROR)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, member)
            _code, body = _status(client, turn)
        assert body["error"] == TURN_TIMEOUT_ERROR
        assert TURN_TIMEOUT_ADMIN_HINT not in body["error"]

    def test_an_admin_sees_no_hint_when_the_failure_is_a_different_error(self, client):
        from agents.runtime.loop import TURN_TIMEOUT_ADMIN_HINT

        admin = make_admin()
        agent = make_agent(enabled=True)
        conversation = create_conversation(user_principal(admin), agent)
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.FAILED, error="the worker stopped responding")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            _code, body = _status(client, turn)
        assert body["error"] == "the worker stopped responding"
        assert TURN_TIMEOUT_ADMIN_HINT not in body["error"]


class TestTheTwoNon200s:
    def test_an_unknown_turn_is_404(self, client):
        response = client.get(reverse("chat-turn-status", args=[999999]))
        assert response.status_code == 404

    def test_an_unreadable_queue_is_503_for_a_turn_that_needs_it(self, client,
                                                                monkeypatch):
        """The unmigrated-window tolerance `AskView` gives its own
        enqueue. Only for a turn still in flight: a finished turn needs
        no queue read at all."""
        monkeypatch.setattr(
            "agents.chat.views.turns.get_job",
            lambda job_id: (_ for _ in ()).throw(QueueUnavailable("no tables")),
        )
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                         queue_job_id=1)
        assert client.get(
            reverse("chat-turn-status", args=[turn.pk])
        ).status_code == 503

    def test_a_finished_turn_answers_without_consulting_the_queue_at_all(
        self, client, monkeypatch
    ):
        """THE VISION LESSON (`tools/vision/views.py::queue_job_status`,
        "THE GENERATION FIRST, the queue second"): a queue row is pruned
        to `JobSettings.retention_limit` on every enqueue, so a
        succeeded job can vanish from under a card still polling it.
        The turn row is the durable record and never does."""
        def _boom(job_id):
            raise AssertionError("the queue must not be consulted for a done turn")

        monkeypatch.setattr("agents.chat.views.turns.get_job", _boom)
        turn = make_turn(role=Turn.Role.ASSISTANT, text="a",
                         state=Turn.State.DONE, queue_job_id=1)
        assert _status(client, turn)[0] == 200

    def test_a_queued_turn_whose_job_vanished_still_reports_queued(self, client,
                                                                  monkeypatch):
        """Honest rather than clever: the ROW says queued, so the body
        says queued, with nothing to say about position. The poller's
        own 10-minute ceiling ends the wait."""
        monkeypatch.setattr("agents.chat.views.turns.get_job", lambda job_id: None)
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                         queue_job_id=1)
        _code, body = _status(client, turn)
        assert body["state"] == "queued" and body["position"] is None


class TestReconciliationOnThePollPath:
    """Owner decision 7: a turn whose job row vanished recovers instead of
    sitting at "working" forever. `_queued_body`/`_running_body` are the
    one surface that was going to answer "Queued — waiting…" for ever, so
    that is where the strand becomes visible and gets repaired, within
    the same poll tick that noticed it -- no separate JS-only path, since
    a plain reload takes the same view."""

    def test_a_polled_turn_whose_job_row_is_gone_comes_back_failed(self, client):
        """Turns a permanent "Queued — waiting…" into an honest failed
        card within one poll tick -- with JS on or off, since a plain
        reload takes the same path through the rendered card."""
        turn = _stranded_assistant_turn()
        _code, body = _status(client, turn)
        assert body["state"] == "failed"
        assert "lost this turn" in body["error"]

    def test_a_turn_inside_the_grace_still_reports_queued(self, client):
        """The enqueue-then-commit window: a turn mid-creation genuinely
        has no job row yet, and the poll path must not race it."""
        turn = _assistant_turn(state=Turn.State.QUEUED, queue_job_id=4242, age_seconds=1)
        _code, body = _status(client, turn)
        assert body["state"] == "queued"


class TestTheRetryable503:
    """TWO DIFFERENT 503s. One says "try again", the other says "go
    configure the queue" -- and the poller acts on the difference."""

    def test_a_momentarily_unavailable_database_answers_retryable(self, client,
                                                                  monkeypatch):
        """BEST-EFFORT, and the view's own docstring says so: the guard
        wraps the whole view body because principal resolution and turn
        visibility both touch the database before any body builder runs,
        and session middleware touches it before the view is entered at
        all. Patched at `visible_turn` because that is inside the guard
        and a real recovering database would raise there first."""
        monkeypatch.setattr(
            turns_module, "visible_turn",
            lambda *a, **k: (_ for _ in ()).throw(OperationalError("server closed")),
        )

        response = client.get(reverse("chat-turn-status", args=[1]))

        assert response.status_code == 503
        assert response.json()["retryable"] is True

    def test_the_configuration_503_stays_terminal_and_keeps_its_setup_link(
        self, client, monkeypatch
    ):
        """Two different 503s: one says "try again", the other says "go
        configure the queue". Conflating them would either spin on a
        misconfigured box or give up on a recovering one."""
        monkeypatch.setattr(
            "agents.chat.views.turns.get_job",
            lambda job_id: (_ for _ in ()).throw(QueueUnavailable("no tables")),
        )
        turn = make_turn(role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
                         queue_job_id=1)

        response = client.get(reverse("chat-turn-status", args=[turn.pk]))

        assert response.status_code == 503
        body = response.json()
        assert body.get("retryable") is not True
        assert "setup_url" in body
