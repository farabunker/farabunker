"""Unit tests for the character cap on a turn's own text (C-7, round-3
hardening, H39) -- `agents.limits.MAX_TURN_CHARS`, checked in
`agents.chat.service.start_turn` beside the existing blank-text refusal
-- and (fix round 1, C-7 review) `chat-workstream-consolidate`'s own
`QueueQuotaExceeded` handling, `agents.chat.views.workstreams.
workstream_consolidate`.

A NEW MODULE, per the H39 addendum: `agents/chat/tests/` is additionally
held (the settings-assistant peer), so this task opens no existing chat
test module -- not `test_turn_create.py` (whose own `TestValidation`
class already covers the blank-text sibling of this refusal), not
`test_thread.py` (held by PR #88). The fix-round-1 brief names this same
module for the consolidate-route quota test too (rather than
`test_never_500.py`, which already exercises this route's other four
conditions but is not opened here).
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    bind_chat_role, bound_chat_role, fake_turn_queue, make_agent, make_conversation, make_thread,
)
from agents.limits import MAX_TURN_CHARS
from agents.models import Turn
from agents.tests._helpers import _workstream
from models.contracts.queue import QueueQuotaExceeded
from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

pytestmark = pytest.mark.django_db

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def post_turn(client, *, text: str, **extra):
    conversation = make_conversation()
    return client.post(reverse("chat-turn", args=[conversation.id]), {"text": text}, **XHR, **extra)


def response_text(response) -> str:
    return response.content.decode()


class TestC7ATurnsTextIsBounded:
    def test_a_turn_longer_than_the_cap_is_a_400_naming_it(self, client):
        response = post_turn(client, text="x" * (MAX_TURN_CHARS + 1))
        assert response.status_code == 400
        assert str(MAX_TURN_CHARS) in response_text(response)
        assert Turn.objects.count() == 0

    def test_a_turn_at_the_cap_is_accepted(self, client, bound_chat_role, fake_turn_queue):
        response = post_turn(client, text="x" * MAX_TURN_CHARS)
        assert response.status_code == 202
        assert Turn.objects.filter(role=Turn.Role.USER).count() == 1


class TestC7BConsolidateAnswersItsOwnQuotaHonestly:
    """`chat-workstream-consolidate` had no `QueueQuotaExceeded` handler
    at all before this fix round -- there is no broad `except Exception`
    on this view, so a principal already at their own queue cap would
    have hit an uncaught exception (a 500), the exact gap review round 1
    flagged. Same double-patch shape `agents/chat/tests/test_never_500.py`
    already uses for this route: `workstream_consolidate` reads `enqueue`
    directly off `models.contracts.queue` into its OWN module namespace
    (`agents.chat.views.workstreams.enqueue`), so the double patches that
    name directly, not `agents.chat.service.enqueue`."""

    def _bind_consolidate_roles(self):
        bind_chat_role(CHAT_CONVERSE_ROLE, name="c7-consolidate-chat")
        bind_chat_role(RAG_EMBED_ROLE, name="c7-consolidate-embed",
                       capability="embeddings", embed_dim=768)

    def test_a_principal_at_their_own_quota_gets_a_429_never_a_500(self, client, monkeypatch):
        self._bind_consolidate_roles()

        def _raise(kind, payload):
            raise QueueQuotaExceeded(
                "1 jobs are already queued or running for this account — the "
                "limit is 1. Wait for one to finish before starting another."
            )

        monkeypatch.setattr("agents.chat.views.workstreams.enqueue", _raise)
        stream = _workstream(name="c7-ws-quota")
        agent = make_agent(slug="c7-consolidate-agent")
        conversation = make_conversation(agent=agent, workstream=stream)
        make_thread(conversation=conversation)

        response = client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                               {"conversation": str(conversation.pk)})

        assert response.status_code == 429
        assert "already queued or running" in response_text(response)
