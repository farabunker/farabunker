"""Posting a turn: one shared preflight, two rows, one enqueue, 202 or
redirect. NO FARABUNKER_FEATURES OVERRIDE (see test_mount.py's
docstring) -- every test here does an HTTP POST.
"""
from __future__ import annotations

import logging

import pytest
from django.db import IntegrityError
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    _patch_queue, bound_chat_role, fake_queue_down, fake_turn_queue, make_agent,
    make_conversation, make_user, posture, sign_in, user_principal,
)
from agents.contracts.tools import ToolResult, ToolSpec, register_tool
from agents.models import Share, Turn
from agents.runtime import loop as loop_module
from agents.runtime.tests._helpers import isolated_tool_registry  # noqa: F401
from agents.visibility import create_conversation
from identity.contracts.postures import POSTURE_ENTERPRISE

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _stub_runner(args: dict, ctx) -> ToolResult:
    """A module-level runner `ToolSpec.runner` can name by dotted path
    -- needs no behaviour, since every test that registers it only
    cares whether it is OFFERED, not whether it is called."""
    return ToolResult(text="stub ran", data={})


class TestValidation:
    def test_a_blank_message_is_400_with_a_per_field_message_and_writes_nothing(self, client):
        """Task 11: the XHR 400 is `_form_errors.html`, not JSON (the
        row `agents/chat/tests/test_errors.py::TestXHRvsNonXHR` also
        pins) -- `#turn-errors` is filled from whatever HTML comes
        back, never parsed as `response.json()`."""
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "   "}, **XHR,
        )
        assert response.status_code == 400
        body = response.content.decode()
        assert "<html" not in body
        assert "blank" in body.lower()
        assert Turn.objects.count() == 0


class TestNonXHRRefusals:
    """A plain form POST (no `X-Requested-With` header) that is refused
    must RE-RENDER THE WHOLE THREAD PAGE with the message in
    `#turn-errors`, never a bare fragment and never a 500 -- the
    counterpart of `tools/vision/views.py:836`'s own named test."""

    def test_a_blank_message_re_renders_the_whole_page_with_the_error(self, client):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "   "},
        )
        assert response.status_code == 400
        body = response.content.decode()
        assert "<html" in body
        assert "blank" in body.lower()

    def test_queue_unavailable_re_renders_the_whole_page_with_the_error(
            self, client, bound_chat_role, fake_queue_down):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"},
        )
        assert response.status_code == 503
        body = response.content.decode()
        assert "<html" in body
        assert "run database migrations" in body
        assert Turn.objects.count() == 0


class TestTheHappyPath:
    def test_xhr_gets_202_with_the_documented_body(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 202
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        body = response.json()
        assert body["turn_id"] == placeholder.pk
        assert body["state"] == "queued"
        assert body["position"] == 1
        assert body["priority"] == 100
        assert body["status_url"] == reverse("chat-turn-status", args=[placeholder.pk])

    def test_the_202_body_carries_the_users_own_message_as_html(
            self, client, bound_chat_role, fake_turn_queue):
        """D1 (chat-polish P3.1): the whole bug was that the client's
        own `appendPendingCard()` never showed the user's own bubble,
        only a bare assistant placeholder, and no later poll swap did
        either (`_done_body` rendered only the job's own rows). The 202
        body now carries the same server-rendered group `turn_status`'s
        own queued/running/done bodies do, so the submit handler can
        insert real markup instead of hand-building a placeholder."""
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "what is the paper about?"}, **XHR,
        )
        assert response.status_code == 202
        assert "what is the paper about?" in response.json()["html"]

    def test_the_202_bodys_html_carries_one_shared_poll_block_marker(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        html = response.json()["html"]
        assert html.count(f'data-poll-block="{placeholder.queue_job_id}"') == 2

    def test_no_js_gets_a_redirect_carrying_pending(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"},
        )
        assert response.status_code == 302
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        assert response["Location"] == (
            f"{reverse('chat-conversation', args=[conversation.id])}?pending={placeholder.pk}"
        )

    def test_no_js_preserves_the_picked_connection(
            self, client, bound_chat_role, fake_turn_queue):
        """The picked connection round-trips into the redirect, same as
        `conversation_start`'s own -- `service.conversation_url` is the
        ONE builder both call, so the two redirects cannot disagree."""
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "hello", "connection": str(bound_chat_role.pk)},
        )
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        location = response["Location"]
        assert f"connection={bound_chat_role.pk}" in location
        assert f"pending={placeholder.pk}" in location

    def test_two_rows_in_one_transaction_at_consecutive_indices(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        client.post(reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR)
        turns = list(conversation.turns.order_by("index"))
        assert [(t.role, t.state) for t in turns] == [
            (Turn.Role.USER, Turn.State.DONE),
            (Turn.Role.ASSISTANT, Turn.State.QUEUED),
        ]
        assert turns[1].index == turns[0].index + 1

    def test_the_assistant_turn_carries_the_queue_job_id(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        client.post(reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR)
        placeholder = Turn.objects.get(role=Turn.Role.ASSISTANT)
        assert placeholder.queue_job_id == 1

    def test_a_granted_but_unregistered_tool_is_a_note_not_a_refusal(
            self, client, bound_chat_role, fake_turn_queue):
        agent = make_agent(slug="general", tool_keys=["not.registered"])
        conversation = make_conversation(agent=agent)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 202
        assert any("not.registered" in note for note in response.json()["notes"])


class TestRefusals:
    def test_an_unbound_role_is_503_naming_the_role_and_writes_nothing(self, client):
        agent = make_agent(slug="general", llm_role="nothing.bound.here")
        conversation = make_conversation(agent=agent)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert "nothing.bound.here" in response.json()["error"]
        assert Turn.objects.count() == 0

    def test_a_model_that_cannot_call_tools_is_503_and_writes_nothing(
            self, client, bound_chat_role, monkeypatch):
        register_tool(ToolSpec(key="stub.safe", label="S", description="d",
                               runner="agents.chat.tests.test_turn_create._stub_runner"))
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: False)
        agent = make_agent(slug="general", tool_keys=["stub.safe"])
        conversation = make_conversation(agent=agent)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert "cannot call tools" in response.json()["error"]
        assert Turn.objects.count() == 0

    def test_queue_unavailable_is_503_with_migration_copy_and_rolls_back(
            self, client, bound_chat_role, fake_queue_down):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert "migrat" in response.json()["error"].lower()
        assert Turn.objects.count() == 0

    def test_an_unexpected_enqueue_exception_is_logged_and_503_and_rolls_back(
            self, client, bound_chat_role, monkeypatch, caplog):
        _patch_queue(monkeypatch, raises=RuntimeError("boom"))
        conversation = make_conversation()
        with caplog.at_level(logging.ERROR, logger="agents.chat.service"):
            response = client.post(
                reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
            )
        assert response.status_code == 503
        # `_ENQUEUE_FAILED`, pinned by its exact text -- distinct from
        # `QUEUE_UNAVAILABLE`'s "run database migrations" copy, so this
        # test cannot pass by accidentally matching the wrong refusal.
        assert response.json()["error"] == (
            "Couldn't add your message to the queue — nothing was queued."
        )
        assert Turn.objects.count() == 0
        # `logger.exception(...)` in `service.start_turn`'s broad
        # `except` -- the unexpected failure is LOGGED, not swallowed,
        # even though the operator sees only the clean 503 above.
        assert any(
            "failed to enqueue an agent.turn job" in record.message
            for record in caplog.records
        )

    def test_a_second_turn_while_one_is_in_flight_is_409_and_writes_nothing(
            self, client, bound_chat_role, fake_turn_queue):
        """Two concurrent `agent.turn` jobs on one conversation is not a
        hypothetical: the form is still on the page while the first
        answer is pending, and a double-submit is the ordinary way it
        happens."""
        conversation = make_conversation()
        client.post(reverse("chat-turn", args=[conversation.id]), {"text": "first"}, **XHR)
        before = Turn.objects.count()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "second"}, **XHR,
        )
        assert response.status_code == 409
        # `_IN_FLIGHT`, pinned by its exact text -- this is what makes
        # the test fail honestly if the 409 ever started firing for the
        # wrong reason (e.g. a stray validation error that happened to
        # also return 409).
        assert response.json()["error"] == (
            "This conversation is still working on the previous message. Wait for that "
            "answer before sending another."
        )
        assert Turn.objects.count() == before

    def test_a_genuine_index_collision_is_a_503_not_a_500(
            self, client, bound_chat_role, monkeypatch):
        """`uniq_turn_index` firing is the honest failure
        `agents/models.py::next_index` says it prefers to a silently
        reordered conversation -- caught BY NAME, not by the broad
        `except`, so the message can say what actually happened.

        SIMULATED, not provoked: a real `uniq_turn_index` race needs two
        concurrent writers hitting the same index, which a single-
        threaded test cannot construct. `Turn.objects.create` is patched
        to raise the identical `IntegrityError` a real collision would,
        on the SECOND call (the placeholder), so this test proves the
        by-name `except IntegrityError` clause's OWN behaviour --
        distinct from `_ENQUEUE_FAILED`'s broad-`except` sentence above
        -- without needing a real race.
        """
        conversation = make_conversation()
        original_create = Turn.objects.create
        calls = {"n": 0}

        def _flaky_create(**kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise IntegrityError("duplicate key value violates uniq_turn_index")
            return original_create(**kwargs)

        monkeypatch.setattr(Turn.objects, "create", _flaky_create)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        # `_COLLIDED`, pinned by its exact text -- would fail if the
        # named `except IntegrityError` clause were deleted and this
        # fell through to the broad `except Exception` instead (which
        # reports `_ENQUEUE_FAILED`'s different sentence).
        assert response.json()["error"] == (
            "Another message reached this conversation at the same moment, so nothing "
            "was queued. Send it again."
        )
        assert Turn.objects.count() == 0

    def test_a_disabled_agent_is_503_and_writes_nothing(self, client):
        agent = make_agent(slug="general", enabled=False)
        conversation = make_conversation(agent=agent)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert "no longer enabled" in response.json()["error"]
        assert Turn.objects.count() == 0


class TestMethodAndTransport:
    def test_get_is_not_allowed(self, client):
        conversation = make_conversation()
        assert client.get(
            reverse("chat-turn", args=[conversation.id])
        ).status_code == 405


class TestTitleTruncation:
    """U5: the stored title is cut on a WORD boundary with a trailing
    "…", not a hard `text[:TITLE_MAX]` slice. Both `chat/index.html`'s
    list and `chat/conversation.html`'s own `<title>` render
    `Conversation.title` verbatim, so a mid-word cut with no ellipsis
    ("...searching the libr") reached both of them."""

    def test_truncate_title_leaves_a_short_message_untouched(self):
        from agents.chat.service import truncate_title

        assert truncate_title("hello there") == "hello there"

    def test_truncate_title_cuts_on_a_word_boundary_and_adds_an_ellipsis(self):
        from agents.chat.service import truncate_title

        message = "in three words what is this paper about and why does it matter"
        title = truncate_title(message, limit=20)
        assert title.endswith("…")
        assert len(title) <= 21
        # The kept prefix (minus the ellipsis) is a genuine prefix of the
        # original text, AND it stops right at a space -- the character
        # immediately after it in the source text -- so nothing was cut
        # out of the middle of a word the way a hard `text[:20]` would
        # ("...this pap" from "...this paper").
        kept = title[:-1].rstrip()
        assert message.startswith(kept)
        assert message[len(kept):len(kept) + 1] in (" ", "")

    def test_truncate_title_falls_back_to_a_hard_cut_with_no_whitespace_at_all(self):
        from agents.chat.service import truncate_title

        title = truncate_title("x" * 100, limit=20)
        assert title == "x" * 20 + "…"

    def test_a_message_over_the_limit_stores_a_title_ending_in_an_ellipsis_not_mid_word(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        long_message = (
            "searching the library for every paper that mentions attention "
            "span in humans and how it changes with age"
        )
        client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": long_message}, **XHR,
        )
        conversation.refresh_from_db()
        assert conversation.title.endswith("…")
        assert not conversation.title[:-1].endswith(" ")

    def test_a_short_message_stores_the_title_unchanged(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello there"}, **XHR,
        )
        conversation.refresh_from_db()
        assert conversation.title == "hello there"


class TestThePayloadCarriesTheActor:
    """THE ACTOR TRAVELS IN THE PAYLOAD (Task 10, the acting rule part
    1). The queue has one door (`models.contracts.queue.enqueue`) and
    ADR 0013 freezes its signature, so "who asked" belongs in the
    payload -- which is already the record of what was asked for."""

    def test_a_turn_started_on_the_page_records_the_signed_in_user(
            self, client, monkeypatch, bound_chat_role):
        from identity.contracts.postures import POSTURE_PERSONAL
        from agents.chat.tests._helpers import make_user, posture, sign_in

        captured = {}
        monkeypatch.setattr(
            "agents.chat.service.enqueue",
            lambda kind, payload, **kw: captured.update(payload) or 1,
        )
        user = make_user()
        # IA-1: `resident=True` -- a plain, unowned agent is no longer
        # startable by a member once accounts are on
        # (`agents.visibility.visible_agents`); a resident (shipped
        # default) agent is visible to everybody, which is the only
        # fact this test needs and keeps it about the acting rule, not
        # about ownership.
        agent = make_agent(resident=True)
        with posture(POSTURE_PERSONAL):
            sign_in(client, user)
            client.post(reverse("chat-start"), {"agent": agent.slug, "text": "hi"})
        assert captured["actor_kind"] == "user"
        assert captured["actor_key"] == str(user.pk)

    def test_an_open_box_records_the_open_principal(self, client, monkeypatch, bound_chat_role):
        """Not a blank. Every job on an open box has an honest actor,
        which is what makes `models/queue/visibility.py` able to reason
        about it later -- a payload with no actor at all is the
        pre-IA-1 shape, and it is admin-only."""
        captured = {}
        monkeypatch.setattr(
            "agents.chat.service.enqueue",
            lambda kind, payload, **kw: captured.update(payload) or 1,
        )
        agent = make_agent()
        client.post(reverse("chat-start"), {"agent": agent.slug, "text": "hi"})
        assert (captured["actor_kind"], captured["actor_key"]) == ("open", "box")


class TestAViewShareMayNotPost:
    def test_view_gets_403_and_use_gets_the_ordinary_answer(self, client):
        owner, viewer, poster = make_user(), make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=poster,
                             level=Share.Level.USE)
        url = reverse("chat-turn", args=[conversation.pk])
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            assert client.post(url, {"text": "hello"}).status_code == 403
            other = client.__class__()
            sign_in(other, poster)
            # 503 is the honest answer on a box with no chat role bound;
            # what matters is that it is NOT the 403 the viewer got.
            assert other.post(url, {"text": "hello"}).status_code != 403
