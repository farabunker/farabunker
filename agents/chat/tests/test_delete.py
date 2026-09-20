"""Deleting a conversation, and what survives it.

`Turn.conversation` is CASCADE (the turns go); `Turn.invocation` is
SET_NULL in the OTHER direction (a `ToolInvocation` never references a
`Turn`, so nothing about deleting a conversation's turns can reach the
audit table at all) -- see `conversation_delete`'s own docstring
(`agents/chat/views/conversations.py`) for the full asymmetry. This
module pins that the SET_NULL choice is a decision, not a default: the
audit rows this conversation produced survive, with their `outcome` and
`principal_key` intact.

NO `FARABUNKER_FEATURES` OVERRIDE (see `test_mount.py`'s own
docstring) -- every test here does an HTTP request or a `reverse()`.
"""
from __future__ import annotations

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    make_agent, make_conversation, make_thread, make_turn,
)
from agents.contracts.tests._helpers import isolated_attachment_registry  # noqa: F401
from agents.models import Agent, Conversation, ToolInvocation, Turn

pytestmark = pytest.mark.django_db


class TestConversationAndTurnsAreGone:
    def test_the_conversation_and_its_turns_are_deleted(self, client):
        conversation = make_thread()
        turn_ids = list(conversation.turns.values_list("pk", flat=True))
        assert turn_ids  # the fixture really wrote turns

        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]),
        )

        assert response.status_code == 302
        assert not Conversation.objects.filter(pk=conversation.id).exists()
        assert not Turn.objects.filter(pk__in=turn_ids).exists()

    def test_the_redirect_lands_on_the_index(self, client):
        conversation = make_conversation()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]),
        )
        assert response["Location"] == reverse("chat-index")

    def test_the_index_shows_a_deleted_notice(self, client):
        """D4: the list used to re-render with the row simply gone --
        no confirmation that the click did anything. `django.contrib.
        messages` (already installed platform-wide, `tools/rag`'s own
        pattern) carries "Conversation deleted." across the redirect."""
        conversation = make_conversation()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]),
            follow=True,
        )
        assert response.status_code == 200
        assert "Conversation deleted." in response.content.decode()


class TestTheAuditSurvives:
    """The pin that makes SET_NULL a decision rather than a default."""

    def test_the_tool_invocation_row_survives_with_outcome_and_principal_key(
        self, client,
    ):
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="rag.search", outcome=ToolInvocation.Outcome.OK,
            text="two results", finished_at=timezone.now(),
        )
        make_turn(
            conversation=conversation, role=Turn.Role.TOOL, text="two results",
            state=Turn.State.DONE, invocation=invocation,
            tool_call={"tool": "rag.search", "args": {"query": "x"}, "agent": "general",
                       "id": "", "discarded": []},
        )

        client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        invocation.refresh_from_db()
        assert invocation.outcome == ToolInvocation.Outcome.OK
        assert invocation.principal_key == "general"
        assert invocation.text == "two results"

    def test_a_failed_invocations_error_also_survives(self, client):
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="resident_agent", principal_key="general",
            tool_key="rag.search", outcome=ToolInvocation.Outcome.ERROR,
            error="the engine timed out", finished_at=timezone.now(),
        )
        make_turn(
            conversation=conversation, role=Turn.Role.TOOL, text="",
            state=Turn.State.DONE, invocation=invocation,
            tool_call={"tool": "rag.search", "args": {}, "agent": "general",
                       "id": "", "discarded": []},
        )

        client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        invocation.refresh_from_db()
        assert invocation.outcome == ToolInvocation.Outcome.ERROR
        assert invocation.error == "the engine timed out"


class TestTheAgentIsUntouched:
    def test_the_agent_row_survives_the_conversations_delete(self, client):
        agent = make_agent(slug="general")
        conversation = make_conversation(agent=agent)
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        assert Agent.objects.filter(pk=agent.pk).exists()


class TestAttachmentRowsGoWithTheConversation:
    """Round 11 re-review, minor 4: `DocumentAttachment.conversation_id`
    is a UUID BY VALUE, never a real FK (`tools/rag` may not import
    `agents.models`) -- nothing would clean those rows up when the
    conversation they claim to name is deleted, unless the delete path
    itself reaches across (`agents.visibility.delete_conversation`,
    through `agents.attachments.delete_attachments_for` and the
    `agents.contracts.attachments` cleanup registry, resolving `tools.
    rag.access.delete_attachments`)."""

    def test_deleting_a_conversation_removes_its_attachment_rows(self, client):
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        conversation = make_conversation()
        doc = make_document(title="Notes.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        assert not DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()

    def test_the_document_itself_survives(self, client):
        """An attachment is a CLAIM a conversation makes on a document,
        never the document's own existence -- deleting the conversation
        must not delete, or touch, the document another conversation
        (or the universal library itself) may still hold or contain."""
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        conversation = make_conversation()
        doc = make_document(title="Notes.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        doc.refresh_from_db()
        assert doc.title == "Notes.pdf"

    def test_a_different_conversations_attachment_row_is_untouched(self, client):
        """The cleanup is scoped to `conversation_id`, not the whole
        table -- deleting one conversation must not touch another's own
        claim on a shared document."""
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        # ONE shared agent for both conversations: `make_conversation()`
        # defaults to a fresh `make_agent()` per call, which collides on
        # that helper's own fixed "test-agent" slug when called twice in
        # one test (see `test_thread.py`'s own query-cost pin comment
        # for the identical fix, same reason).
        agent = make_agent()
        gone = make_conversation(agent=agent)
        stays = make_conversation(agent=agent)
        doc = make_document(title="Shared.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=gone.id)
        DocumentAttachment.objects.create(document=doc, conversation_id=stays.id)

        client.post(reverse("chat-conversation-delete", args=[gone.id]))

        assert not DocumentAttachment.objects.filter(conversation_id=gone.id).exists()
        assert DocumentAttachment.objects.filter(conversation_id=stays.id).exists()


class TestMethodAndTransport:
    def test_a_get_is_405(self, client):
        conversation = make_conversation()
        response = client.get(
            reverse("chat-conversation-delete", args=[conversation.id]),
        )
        assert response.status_code == 405
        assert Conversation.objects.filter(pk=conversation.id).exists()

    def test_an_unknown_id_is_404_not_a_500(self, client):
        response = client.post(
            reverse("chat-conversation-delete", args=[uuid.uuid4()]),
        )
        assert response.status_code == 404


class TestTheDeleteControlOnThePage:
    """A disclosure, then a real POST -- no JS confirm dialog."""

    def test_the_page_carries_a_details_confirm_not_a_js_dialog(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "<details" in body
        assert (
            f'action="{reverse("chat-conversation-delete", args=[conversation.id])}"'
            in body
        )
        assert "confirm(" not in body


def _raising_db_cleanup(conversation_id) -> int:
    """A cleanup provider that fails with a REAL database-level error,
    not a plain Python exception -- reproducing the fix-2 verify's own
    repro (`InternalError: current transaction is aborted`). Module
    level, not a closure: `import_string` needs a resolvable dotted
    path to reach it."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute("SELECT 1/0")  # Postgres: DataError, mid-transaction
    return 0  # pragma: no cover - unreachable, the execute() above raises


class TestTheCleanupSavepoint:
    """Round 11 fix-2 verify, Important N-1: the registered attachment-
    cleanup provider used to run bare inside `delete_conversation`'s own
    outer `transaction.atomic()` -- a Python exception was caught
    (`agents.attachments.delete_attachments_for`'s own `except`), but a
    DATABASE-level error left the WHOLE connection "current transaction
    is aborted" for the rest of that block, which meant `conversation.
    delete()` -- run straight after, in the SAME transaction -- failed
    too, even though the cleanup function's own contract is "never
    raises". Fixed with a SAVEPOINT (`delete_attachments_for`'s own
    `transaction.atomic()`, nested): this test drives a REAL database
    error through the full HTTP delete path and pins that the
    conversation still goes."""

    def test_a_database_error_in_cleanup_does_not_block_the_delete(
        self, client, isolated_attachment_registry
    ):
        from agents.contracts.attachments import register_attachment_cleanup

        conversation = make_conversation()
        register_attachment_cleanup(
            "agents.chat.tests.test_delete._raising_db_cleanup")

        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]),
        )

        assert response.status_code == 302
        assert not Conversation.objects.filter(pk=conversation.id).exists()


class TestConversationDeleteCascadesChatScopedDocuments:
    """Round 12 (owner ruling, verbatim: "if I submit a document but
    have scope for chat, then it should only be used in that chat" --
    read the other way, a chat-scoped document has no life outside its
    chat either). Deleting the conversation deletes the DOCUMENT too
    (vector chunks, managed-store FILES, and the row -- `tools.rag.
    services.delete_document`'s own contract), not merely its
    attachment row -- via the SAME cleanup slot round-11 fix-2 built.

    `services.delete_document` ITSELF IS MOCKED, not exercised with
    real files on disk: this scopes each test to what round 12 actually
    added -- the CASCADE DECISION, called or not called, for the right
    document -- and trusts `delete_document`'s own file-removal
    correctness to its own test suite (`tools/rag/tests/
    test_views_documents.py::TestDocumentDelete`, which already covers
    it)."""

    def test_a_chat_scoped_documents_delete_document_is_called(self, client):
        from unittest.mock import patch

        from agents.tests._helpers import make_document
        from tools.rag.models import Document, DocumentAttachment

        conversation = make_conversation()
        doc = make_document(scope=Document.Scope.CONVERSATION)
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        with patch("tools.rag.services.delete_document") as mock_delete:
            client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        mock_delete.assert_called_once()
        (called_doc,), _ = mock_delete.call_args
        assert called_doc.pk == doc.pk

    def test_a_universal_documents_attachment_row_is_removed_but_the_document_survives(
        self, client
    ):
        from unittest.mock import patch

        from agents.tests._helpers import make_document
        from tools.rag.models import Document, DocumentAttachment

        conversation = make_conversation()
        doc = make_document()  # scope defaults to Document.Scope.UNIVERSAL
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        with patch("tools.rag.services.delete_document") as mock_delete:
            client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        mock_delete.assert_not_called()
        assert not DocumentAttachment.objects.filter(document=doc).exists()
        assert Document.objects.filter(pk=doc.pk).exists()
