"""Every page-facing row of spec section 10.1, made visible.

Most of the underlying behaviour shipped in Tasks 9-10; this module
pins its SURFACE -- the honest sentence, the right status code, the
right fragment, and the promise that a GET never refuses to show what
already happened. NO `FARABUNKER_FEATURES` OVERRIDE ANYWHERE (see
`test_mount.py`'s own docstring) -- every test here does an HTTP
request or a `reverse()`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils.html import escape

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    bound_chat_role, fake_queue_down, fake_turn_queue, make_agent, make_conversation,
    make_turn,
)
from agents.contracts.tools import ToolResult, ToolSpec, register_tool
from agents.runtime import loop as loop_module
from agents.runtime.jobs import on_turn_terminal
from agents.runtime.preflight import preflight_turn
from agents.runtime.tests._helpers import isolated_tool_registry  # noqa: F401
from agents.models import Turn
from identity.contracts.principals import OPEN_PRINCIPAL

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _stub_runner(args: dict, ctx) -> ToolResult:
    """A module-level runner `ToolSpec.runner` can name by dotted path.
    Never called by anything here -- every test that registers it only
    cares whether the model was OFFERED it, which `preflight_turn`
    decides before any runner runs."""
    return ToolResult(text="stub ran", data={})


class TestUnboundChatRole:
    """Spec section 10.1's first row. Two different responses for one
    condition, and that is the point: reading is not queueing."""

    def test_the_thread_still_renders_200_with_a_banner_naming_the_role(self, client):
        agent = make_agent(slug="general", llm_role="nothing.bound.here")
        conversation = make_conversation(agent=agent)
        expected = preflight_turn(agent, "", actor=OPEN_PRINCIPAL).message
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        assert response.status_code == 200
        body = response.content.decode()
        # HTML-escaped (the message's own `!r` quoting), so compared
        # against the same escaping the template applies -- the POST's
        # own JSON body below compares the raw sentence instead.
        assert escape(expected) in body
        assert "nothing.bound.here" in body
        assert reverse("inference-console") in body
        assert reverse("setup-index") in body

    def test_the_post_is_503_with_the_same_sentence(self, client):
        agent = make_agent(slug="general", llm_role="nothing.bound.here")
        conversation = make_conversation(agent=agent)
        expected = preflight_turn(agent, "", actor=OPEN_PRINCIPAL).message
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert response.json()["error"] == expected
        assert Turn.objects.filter(conversation=conversation).count() == 0


class TestModelCannotCallTools:
    """Spec section 10.1's second row. The banner names the ROLE, never
    a model -- `preflight_turn`'s own contract, ADR 0010's third
    amendment."""

    def _agent_needing_tools(self, monkeypatch):
        register_tool(ToolSpec(
            key="stub.safe", label="S", description="d",
            runner="agents.chat.tests.test_errors._stub_runner",
        ))
        monkeypatch.setattr(loop_module, "supports_tool_calling", lambda r: False)
        return make_agent(slug="general", tool_keys=["stub.safe"])

    def test_the_thread_still_renders_200_naming_the_role_not_a_model(
        self, client, bound_chat_role, monkeypatch,
    ):
        agent = self._agent_needing_tools(monkeypatch)
        conversation = make_conversation(agent=agent)
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        assert response.status_code == 200
        body = response.content.decode()
        assert agent.llm_role in body
        assert bound_chat_role.model_id not in body

    def test_the_post_is_503_with_the_same_sentence(
        self, client, bound_chat_role, monkeypatch,
    ):
        agent = self._agent_needing_tools(monkeypatch)
        conversation = make_conversation(agent=agent)
        expected = preflight_turn(agent, "", actor=OPEN_PRINCIPAL).message
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert response.json()["error"] == expected
        assert bound_chat_role.model_id not in response.json()["error"]


class TestQueueUnavailable:
    """Spec section 10.1's fourth row. A queue outage refuses a NEW
    turn; it does not hide a conversation's history."""

    def test_the_thread_still_renders_while_the_queue_is_down(
        self, client, bound_chat_role, fake_queue_down,
    ):
        conversation = make_conversation()
        response = client.get(reverse("chat-conversation", args=[conversation.id]))
        assert response.status_code == 200

    def test_the_post_is_503_with_the_migration_copy(
        self, client, bound_chat_role, fake_queue_down,
    ):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert "migrat" in response.json()["error"].lower()


class TestAToolDroppedAsUnregistered:
    """Spec section 8.3 step 3's tolerant half, surfaced. A granted
    tool that is not registered on this install is a NOTE, not a
    refusal -- and the note survives the no-JS redirect because the
    thread's own GET recomputes the SAME `preflight_turn` call."""

    def test_xhr_gets_202_with_the_note(self, client, bound_chat_role, fake_turn_queue):
        agent = make_agent(slug="general", tool_keys=["not.registered"])
        conversation = make_conversation(agent=agent)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 202
        assert any("not.registered" in note for note in response.json()["notes"])

    def test_the_note_appears_on_the_page_after_the_redirect(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        agent = make_agent(slug="general", tool_keys=["not.registered"])
        conversation = make_conversation(agent=agent)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"},
        )
        assert response.status_code == 302
        body = client.get(response["Location"]).content.decode()
        assert "not.registered" in body
        assert "not available on this install" in body


class TestAFailedTurn:
    """Spec section 10.1's `run_turn` row. `Turn.error` shown verbatim,
    never a traceback, plus a way back to the model console."""

    def test_the_card_shows_the_error_verbatim_and_a_setup_link(self, client):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="the worker stopped responding")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "the worker stopped responding" in body
        assert reverse("inference-console") in body
        assert "Traceback" not in body
        assert 'File "' not in body


class TestACancelledTurn:
    """The real hook, driven, not a hand-written row: `on_turn_terminal`
    (`agents/runtime/jobs.py`) writes the sentence, and this test proves
    the page shows exactly what the hook wrote -- a real end-to-end pin
    between the two."""

    def test_the_card_shows_the_sentence_on_turn_terminal_wrote(self, client):
        conversation = make_conversation()
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.QUEUED, queue_job_id=1)
        on_turn_terminal({"turn": turn.pk}, "cancelled")
        turn.refresh_from_db()
        assert turn.state == Turn.State.CANCELLED
        assert turn.error  # the hook actually wrote something
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert turn.error in body
        assert "Traceback" not in body


class TestXHRvsNonXHR:
    """The counterpart of `tools/vision/views.py:836`'s own named test.
    A 400 is the ONE refusal `turn_create` answers with a fragment
    (Task 11) rather than JSON -- every other refusal (409/503) is
    unchanged."""

    def test_xhr_400_returns_the_form_errors_fragment_not_json(self, client):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "   "}, **XHR,
        )
        assert response.status_code == 400
        body = response.content.decode()
        assert "<html" not in body
        assert "blank" in body.lower()
        with pytest.raises(ValueError):
            response.json()

    def test_non_xhr_400_returns_the_whole_page_not_a_bare_fragment(self, client):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "   "},
        )
        assert response.status_code == 400
        body = response.content.decode()
        assert "<html" in body
        assert "blank" in body.lower()

    def test_the_chat_form_error_banner_carries_no_class_nothing_styles(self):
        """C-50. `form-errors` had no CSS rule anywhere and was read by no
        test and no script -- a token that looks like a hook and is not one."""
        text = (Path(settings.BASE_DIR) / "agents/chat/templates/chat/_form_errors.html").read_text()
        assert "form-errors" not in text


class TestTheXHRContentTypeContract:
    """`conversation.html`'s own submit handler decides HTML-fragment
    vs JSON by reading `Content-Type` (review round 1) -- pinned here
    server-side, so a future change on EITHER side (the view starts
    answering a 400 as JSON, or a 409/503 starts answering as HTML)
    fails a test instead of silently reaching the browser as a
    fabricated "could not reach farabunker"."""

    def test_the_400_fragment_is_html(self, client):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "   "}, **XHR,
        )
        assert response.status_code == 400
        assert response["Content-Type"].startswith("text/html")

    def test_the_409_body_is_json(self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        client.post(reverse("chat-turn", args=[conversation.id]), {"text": "first"}, **XHR)
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "second"}, **XHR,
        )
        assert response.status_code == 409
        assert response["Content-Type"].startswith("application/json")

    def test_the_503_body_is_json(self, client, bound_chat_role, fake_queue_down):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 503
        assert response["Content-Type"].startswith("application/json")

    def test_the_202_body_is_json(self, client, bound_chat_role, fake_turn_queue):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "hello"}, **XHR,
        )
        assert response.status_code == 202
        assert response["Content-Type"].startswith("application/json")


class TestNeverATraceback:
    """Never a fabricated answer, never a leaked internal. Swept across
    every error surface this module exercises."""

    def test_no_surface_ever_shows_a_traceback_or_a_source_line(
        self, client, bound_chat_role, fake_queue_down,
    ):
        conversation = make_conversation()
        make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                  state=Turn.State.FAILED, error="boom")
        turn = make_turn(conversation=conversation, role=Turn.Role.ASSISTANT,
                         state=Turn.State.QUEUED, queue_job_id=99)
        on_turn_terminal({"turn": turn.pk}, "cancelled")

        bodies = [
            client.post(
                reverse("chat-turn", args=[conversation.id]), {"text": "  "}, **XHR,
            ).content.decode(),
            client.post(
                reverse("chat-turn", args=[conversation.id]), {"text": "hi"}, **XHR,
            ).content.decode(),
            client.get(
                reverse("chat-conversation", args=[conversation.id])
            ).content.decode(),
        ]
        for body in bodies:
            assert "Traceback" not in body
            assert 'File "' not in body
