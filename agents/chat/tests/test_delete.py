"""Deleting a conversation, and what survives it.

IT TICKETS; IT DOES NOT ERASE (Task 8). A delete POST no longer removes
the `Conversation` row -- `agents.visibility.delete_conversation` now
calls `identity.retention.delete_content`, which writes one
`DeletionTicket` and a content-free `content.deleted` event. The row,
its turns, its shares, its attachment claims and its chat-scoped
documents all survive the request; they are torn down TOGETHER at purge
time by `agents.retention.purge_conversation` and its sibling handlers
(Task 9), whose own test module (`agents/tests/test_retention.py`) is
where every "the row is gone"/"the cascade ran" assertion this module
used to make now lives. This module's own job narrows to what a SOFT
delete does: the row survives, the thread disappears from every reader
that goes through `visible_conversations`, and exactly one ticket
exists.

`Turn.conversation` is CASCADE (still true, and still exercised only at
purge now); `Turn.invocation` is SET_NULL in the OTHER direction (a
`ToolInvocation` never references a `Turn`, so nothing about deleting a
conversation's turns can reach the audit table at all) -- see
`conversation_delete`'s own docstring (`agents/chat/views/
conversations.py`) for the full asymmetry. This module pins that the
SET_NULL choice is a decision, not a default: the audit rows this
conversation produced survive, with their `outcome` and `principal_key`
intact, and a SOFT delete does not even reach `ToolInvocation` in the
first place.

NO `FARABUNKER_FEATURES` OVERRIDE (see `test_mount.py`'s own
docstring) -- every test here does an HTTP request or a `reverse()`.
"""
from __future__ import annotations

import datetime
import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    make_agent, make_conversation, make_thread, make_turn,
)
from agents.models import Agent, Conversation, ToolInvocation, Turn
from agents.visibility import visible_conversations
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import DeletionTicket, IdentitySettings

pytestmark = pytest.mark.django_db

# `_a_conversation_with_a_turn` IN THE BRIEF IS `make_thread` HERE: the
# brief's own prose names a helper by a descriptive alias, and `make_
# thread` (`agents/chat/tests/_helpers.py`) -- "a conversation with a
# finished USER + ASSISTANT pair" -- is the one existing helper in this
# module's family that already fits it; `TestConversationAndTurnsAreGone.
# test_the_conversation_and_its_turns_are_deleted`, below, already calls
# it for the identical reason. No second helper is added.


class TestConversationAndTurnsAreGone:
    def test_the_conversation_and_its_turns_are_deleted(self, client):
        """RE-PINNED (Task 8): a delete no longer erases the row. The
        conversation and its turns SURVIVE the request; what actually
        happens is that the thread leaves every reader that goes through
        `visible_conversations`, and exactly one `DeletionTicket` now
        names it. `TestTicketedConversationsAreInvisible`
        (`agents/chat/tests/test_visibility.py`) is the dedicated pin for
        the visibility half; this test's own job is the HTTP path's
        outcome: the rows are still there, the ticket exists, and the
        conversation is gone from the one list a person actually reads."""
        conversation = make_thread()
        turn_ids = list(conversation.turns.values_list("pk", flat=True))
        assert turn_ids  # the fixture really wrote turns

        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]),
        )

        assert response.status_code == 302
        assert Conversation.objects.filter(pk=conversation.id).exists()
        assert Turn.objects.filter(pk__in=turn_ids).exists()
        assert DeletionTicket.objects.filter(
            kind="conversation", key=str(conversation.id)).exists()
        assert conversation not in list(visible_conversations(OPEN_PRINCIPAL))

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
        pattern) carries the notice across the redirect.

        RE-PINNED (Task 8): the notice now NAMES THE DELETED PAGE --
        `conversation_delete`'s own docstring has the reason the exact
        sentence depends on the box's retention policy -- so this checks
        for the Deleted page's own name rather than the old flat
        "Conversation deleted." sentence, which a zero-day box's own
        notice does not even contain as a whole line (it says
        "Conversation deleted permanently." instead --
        `TestTheNoticeNamesWhereItWent` below pins both)."""
        conversation = make_conversation()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]),
            follow=True,
        )
        assert response.status_code == 200
        assert "Settings → Deleted" in response.content.decode()


class TestTheAuditSurvives:
    """The pin that makes SET_NULL a decision rather than a default --
    unchanged by Task 8: a SOFT delete does not touch `ToolInvocation`
    either, so this stays green with no edit."""

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
    itself reaches across (`agents.attachments.delete_attachments_for`
    and the `agents.contracts.attachments` cleanup registry, resolving
    `tools.rag.access.delete_attachments`).

    RE-PINNED (Task 8): that reach now happens at PURGE, not at delete.
    `agents/tests/test_retention.py` (Task 9) is where "the row is gone"
    moves to, against `agents.retention.purge_conversation`; this class's
    own job narrows to the SOFT-DELETE half -- the claim survives a
    delete exactly like everything else the conversation owns."""

    def test_deleting_a_conversation_removes_its_attachment_rows(self, client):
        """MOVED (Task 9, `agents/tests/test_retention.py`): "removes"
        is now true at PURGE, through `agents.retention.
        purge_conversation`'s attachment handler -- not at delete. The
        soft-delete half stays here: the row survives the POST."""
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        conversation = make_conversation()
        doc = make_document(title="Notes.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        assert DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()

    def test_the_document_itself_survives(self, client):
        """An attachment is a CLAIM a conversation makes on a document,
        never the document's own existence -- deleting the conversation
        must not delete, or touch, the document another conversation
        (or the universal library itself) may still hold or contain.
        UNCHANGED by Task 8: a soft delete touches neither row, so this
        stays true even more directly than before."""
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        conversation = make_conversation()
        doc = make_document(title="Notes.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        client.post(reverse("chat-conversation-delete", args=[conversation.id]))

        doc.refresh_from_db()
        assert doc.title == "Notes.pdf"

    def test_a_different_conversations_attachment_row_is_untouched(self, client):
        """MOVED (Task 9, `agents/tests/test_retention.py`): the scoped-
        cleanup claim ("deleting one conversation must not touch
        another's own claim") is a PURGE-time claim now. The soft-delete
        half stays here: a delete touches NEITHER conversation's
        attachment row."""
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

        assert DocumentAttachment.objects.filter(conversation_id=gone.id).exists()
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
    """A disclosure, then a real POST -- no JS confirm dialog. UNCHANGED
    by Task 8: the copy changes, the mechanism does not."""

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


# `_raising_db_cleanup` and `TestTheCleanupSavepoint` MOVED to
# `agents/tests/test_retention.py` (Task 9), against `agents.retention.
# purge_conversation`: the registered attachment-cleanup provider no
# longer runs inside `delete_conversation` at all -- a soft delete calls
# `identity.retention.delete_content`, which never touches the
# attachment registry -- so the savepoint this test drives a real
# database error through now belongs to the purge path.


# `TestConversationDeleteCascadesChatScopedDocuments` MOVED to
# `agents/tests/test_retention.py` (Task 9), against `agents.retention.
# purge_conversation`: round 12's document cascade ("if I submit a
# document but have scope for chat, then it should only be used in that
# chat") ran at delete time through the SAME cleanup slot `Attachment
# RowsGoWithTheConversation` above used, and that slot no longer runs at
# delete time either. `TestDeleteWritesATicketAndHidesTheThread`, below,
# is where this module's own soft-delete-time claim about a chat-scoped
# document now lives -- nothing cascades at delete time, whatever a
# document's scope.


class TestDeleteWritesATicketAndHidesTheThread:
    """Delete is now a PROMISE, not an erasure: the rows survive until
    the date the Deleted page prints."""

    def test_the_rows_survive_and_the_thread_vanishes_from_every_surface(self, client):
        conversation = make_thread()
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        assert Conversation.objects.filter(pk=conversation.pk).exists()
        assert Turn.objects.filter(conversation_id=conversation.pk).exists()
        assert list(visible_conversations(OPEN_PRINCIPAL)) == []

    def test_one_ticket_carries_the_title_and_the_promised_date(self, client):
        conversation = make_thread()
        conversation.title = "A thread"
        conversation.save(update_fields=["title"])
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        ticket = DeletionTicket.objects.get()
        assert (ticket.kind, ticket.key) == ("conversation", str(conversation.pk))
        assert ticket.label == "A thread"
        assert ticket.purge_on == timezone.localdate() + datetime.timedelta(days=30)

    def test_the_notice_names_where_the_thread_went(self, client):
        conversation = make_thread()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]), follow=True)
        assert "Deleted" in response.content.decode()

    def test_a_second_delete_of_the_same_thread_is_a_404_not_a_second_ticket(self, client):
        conversation = make_thread()
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        second = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]))
        assert second.status_code == 404
        assert DeletionTicket.objects.count() == 1

    def test_the_notice_names_permanent_deletion_when_the_grace_period_is_zero(
        self, client
    ):
        """CONTROLLER ADDITION (Task 8 review): the notice must not
        promise a restore the box cannot keep. With `retention_days = 0`
        the ticket `delete_content` hands back has ALREADY been purged
        by the time the view runs -- its `purge_on` is today
        (`identity.retention.delete_content`'s own unconditional bounded
        sweep) -- so the notice reads "Conversation deleted
        permanently." instead of naming a restore door that is already
        closed.

        THE SETTINGS WRITE AND THE ASSERTION BOTH LIVE INSIDE THIS
        TEST'S OWN SCOPE, deliberately: `IdentitySettings.get_solo()` is
        not restored by anything here, but each test runs inside its own
        rolled-back `django_db` transaction, so a `retention_days = 0`
        write here never leaks into another test."""
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.save()

        conversation = make_thread()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]), follow=True)

        body = response.content.decode()
        assert "Conversation deleted permanently." in body
        # NOT THE BARE SUBSTRING -- "Settings → Deleted" also appears in
        # the sidebar's own OTHER conversations' delete-confirm copy
        # (`chat/_sidebar_row.html`), unrelated to this notice. The
        # restore sentence this box must not print is the whole one.
        assert "Conversation deleted. You can restore it from Settings → Deleted." not in body
