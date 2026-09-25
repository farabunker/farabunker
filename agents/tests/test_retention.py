"""`agents.retention.purge_conversation` -- the agents-side teardown,
plus the two channels that find a conversation's generated images."""
from __future__ import annotations

import uuid

import pytest
from django.conf import settings

from agents.contracts import artifacts as artifacts_module
from agents.contracts.artifacts import register_artifact_children
from agents.contracts.tests._helpers import isolated_attachment_registry  # noqa: F401
from agents.models import Conversation, Share, ToolInvocation, Turn
from agents.retention import conversation_children, purge_conversation, scrub_tool_records
from agents.tests._helpers import (
    make_agent, make_conversation, make_turn, make_user, posture, user_principal,
)
from identity.models import DeletionTicket

pytestmark = pytest.mark.django_db

SEEN: list[tuple[tuple, tuple]] = []


def fake_artifact_children(refs, generation_ids) -> list[str]:
    SEEN.append((tuple(refs), tuple(generation_ids)))
    return sorted(set(refs) | set(generation_ids))


@pytest.fixture
def real_registration():
    """Requested BY NAME, not autoused: opts a test out of `_isolated_
    slot` below. `TestZeroDayEndToEnd`
    wants the box exactly as a real deploy starts it -- whatever `agents/
    apps.py::ready()` and `tools/vision/apps.py::ready()` actually
    registered at Django startup, not this module's `SEEN`-recording
    fake -- so it requests this instead, and `_isolated_slot` checks for
    it in `request.fixturenames` and stands aside."""
    return None


@pytest.fixture(autouse=True)
def _isolated_slot(request):
    """SAVE, SET, RESTORE -- the single artifact-children slot is a
    module-level global with no reset path, exactly like the cascade
    registries `identity/tests/test_cascades.py::_isolated_registry`
    protects, and for the same reason: a fake left in place here would
    be resolved by every later conversation delete in the same pytest
    process -- `identity/tests/test_deletion_demo.py`'s, the Deleted
    page's, the route matrix's owner column -- and the suite runs in
    BOTH collection orders, so ordering luck cannot cover it.

    On a box with "vision" off the saved value is `None`, which restores
    correctly too.

    STANDS ASIDE FOR A TEST THAT REQUESTS `real_registration` (above):
    that one test wants the REAL, app-registered
    slot end to end, not this module's fake.
    """
    if "real_registration" in request.fixturenames:
        yield
        return
    saved = artifacts_module._ARTIFACT_CHILDREN
    SEEN.clear()
    register_artifact_children(f"{__name__}.fake_artifact_children")
    yield
    SEEN.clear()
    artifacts_module._ARTIFACT_CHILDREN = saved


class TestTheRowsGo:
    def test_the_conversation_its_turns_and_its_shares_are_removed(self):
        """`Share.user`/`.group` are XOR (`share_user_xor_group`) -- a
        row naming neither violates the constraint, so this pins a real
        recipient (`user=`) rather than the bare `level="view"` the
        brief's own draft carried."""
        conversation = make_conversation()
        make_turn(conversation=conversation)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk),
                             user=make_user(), level="view")
        removed = purge_conversation(str(conversation.pk))
        assert not Conversation.objects.filter(pk=conversation.pk).exists()
        assert not Turn.objects.filter(conversation_id=conversation.pk).exists()
        assert not Share.objects.filter(target_key=str(conversation.pk)).exists()
        assert removed > 0

    def test_it_is_idempotent_on_a_conversation_that_is_already_gone(self):
        """The contract obligation every retention handler owes: a
        re-run after a mid-purge failure COMPLETES rather than raises."""
        conversation = make_conversation()
        purge_conversation(str(conversation.pk))
        assert purge_conversation(str(conversation.pk)) == 0

    def test_an_unparseable_key_removes_nothing_and_does_not_raise(self):
        assert purge_conversation("not-a-uuid") == 0


class TestTheToolRecordScrub:
    def test_the_words_go_and_the_shell_stays(self):
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="rag__search",
            args={"query": "a secret"}, text="the result the model saw",
            error="", outcome="ok")
        make_turn(conversation=conversation, role="tool",
                  invocation_id=invocation.pk)
        purge_conversation(str(conversation.pk))
        invocation.refresh_from_db()
        assert invocation.args == {}
        assert invocation.text == ""
        assert invocation.error == ""
        # The machine audit trail survives, principal and outcome intact.
        assert invocation.principal_key == "1"
        assert invocation.tool_key == "rag__search"
        assert invocation.outcome == "ok"

    def test_the_ids_are_collected_before_the_turns_are_deleted(self):
        """`Turn.invocation` is SET_NULL, so after the delete there is no
        path from the conversation to its invocations at all. Collecting
        first is what makes the scrub reachable, and this is the test
        that would fail if somebody reordered it. (This pins the
        INVOCATION-ID half of collect-before-delete; the purge never
        touches a generated image at all any more -- see
        `TestThePurgeTouchesNoImages` below.)"""
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="t",
            args={"a": 1}, text="x", outcome="ok")
        make_turn(conversation=conversation, role="tool",
                  invocation_id=invocation.pk)
        purge_conversation(str(conversation.pk))
        invocation.refresh_from_db()
        assert invocation.args == {}

    def test_scrub_of_an_empty_id_list_is_a_no_op(self):
        assert scrub_tool_records([]) == 0


class TestFindingTheGeneratedImages:
    def test_output_and_input_references_are_collected_and_deduped(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", index=0,
                  artifacts=["output:1", "output:2"])
        make_turn(conversation=conversation, role="tool", index=1,
                  artifacts=["output:1", "input:9"])
        conversation_children(str(conversation.pk))
        refs, _ids = SEEN[0]
        assert sorted(set(refs)) == ["input:9", "output:1", "output:2"]

    def test_a_document_reference_is_not_handed_to_the_image_column(self):
        """`document:<id>` artifacts are `Document` rows the attachment
        seam already reaches."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool",
                  artifacts=["document:451", "output:3"])
        conversation_children(str(conversation.pk))
        refs, _ids = SEEN[0]
        assert list(refs) == ["output:3"]

    def test_an_unparseable_reference_is_dropped_and_never_raises(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool",
                  artifacts=["output:12:extra", "", "output:4"])
        assert conversation_children(str(conversation.pk)) == [("vision_job", "output:4")]
        refs, _ids = SEEN[0]
        assert list(refs) == ["output:4"]

    def test_a_generation_id_is_collected_from_a_job_that_FAILED(self):
        """THE STEWARD'S GAP, PINNED AS A TEST. A job that reached the
        engine and failed mints no `GeneratedOutput` at all, so the
        artifact channel finds nothing -- but `run_generate` reaches
        `job_json` on every terminal outcome, so the tool turn carries
        `data["id"]`. This is the only thing that catches it."""
        conversation = make_conversation()
        job_id = str(uuid.uuid4())
        make_turn(conversation=conversation, role="tool", artifacts=[],
                  data={"id": job_id, "status": "failed", "error": "out of memory"})
        conversation_children(str(conversation.pk))
        _refs, ids = SEEN[0]
        assert list(ids) == [job_id]

    def test_a_turn_with_no_data_contributes_nothing(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="assistant", data=None)
        conversation_children(str(conversation.pk))
        _refs, ids = SEEN[0]
        assert list(ids) == []

    def test_data_that_is_not_a_dict_or_whose_id_is_not_a_uuid_is_ignored(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", index=0, data=["a", "list"])
        make_turn(conversation=conversation, role="tool", index=1,
                  data={"id": "not-a-uuid"})
        conversation_children(str(conversation.pk))
        _refs, ids = SEEN[0]
        assert list(ids) == []

    def test_with_no_resolver_registered_there_are_no_children(self):
        """Safe to clear here: `_isolated_slot` restores whatever the
        real `tools/vision/apps.py::ready()` registered."""
        artifacts_module._ARTIFACT_CHILDREN = None
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", artifacts=["output:1"])
        assert conversation_children(str(conversation.pk)) == []


# `TestAttachmentRowsGoAtPurge`, `_raising_db_cleanup` / `TestTheCleanupSavepoint`
# and `TestConversationPurgeCascadesChatScopedDocuments` below are MOVED from
# `agents/chat/tests/test_delete.py`, rewritten against `purge_conversation` directly rather than the
# HTTP delete view: the attachment cascade, the savepoint around a
# broken cleanup provider, and the chat-scoped document cascade all run
# at PURGE now, never at the soft delete that only writes a ticket.


class TestAttachmentRowsGoAtPurge:
    """Round 11 re-review, minor 4: `DocumentAttachment.conversation_id`
    is a UUID BY VALUE, never a real FK (`tools/rag` may not import
    `agents.models`) -- nothing would clean those rows up when the
    conversation they claim to name is purged, unless the purge path
    itself reaches across (`agents.attachments.delete_attachments_for`
    and the `agents.contracts.attachments` cleanup registry, resolving
    `tools.rag.access.delete_attachments`)."""

    def test_purging_a_conversation_removes_its_attachment_rows(self):
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        conversation = make_conversation()
        doc = make_document(title="Notes.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        purge_conversation(str(conversation.id))

        assert not DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()

    def test_a_different_conversations_attachment_row_is_untouched(self):
        """The cleanup is scoped to `conversation_id`, not the whole
        table -- purging one conversation must not touch another's own
        claim on a shared document."""
        from agents.tests._helpers import make_document
        from tools.rag.models import DocumentAttachment

        # ONE shared agent for both conversations: `make_conversation()`
        # defaults to a fresh `make_agent()` per call, which collides on
        # that helper's own fixed "test-agent" slug when called twice in
        # one test.
        agent = make_agent()
        gone = make_conversation(agent=agent)
        stays = make_conversation(agent=agent)
        doc = make_document(title="Shared.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=gone.id)
        DocumentAttachment.objects.create(document=doc, conversation_id=stays.id)

        purge_conversation(str(gone.id))

        assert not DocumentAttachment.objects.filter(conversation_id=gone.id).exists()
        assert DocumentAttachment.objects.filter(conversation_id=stays.id).exists()


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
    """The registered attachment-
    cleanup provider used to run bare inside `delete_conversation`'s own
    outer `transaction.atomic()` -- a Python exception was caught
    (`agents.attachments.delete_attachments_for`'s own `except`), but a
    DATABASE-level error left the WHOLE connection "current transaction
    is aborted" for the rest of that block. `delete_attachments_for`'s
    own nested `transaction.atomic()` (a SAVEPOINT) is what still
    protects a caller from that.

    A database error inside the
    cleanup provider used to be pinned as something the purge SURVIVES
    (the conversation still went). That was right when a broken cleanup
    provider could only run at a conversation-delete click a person had
    already confirmed; it stopped being right the moment this module
    became the retention runner's own registered handler. A swallowed
    failure at PURGE time means the rows the cleanup provider was
    supposed to remove -- and the conversation itself, in the old
    behaviour -- would go, while the DocumentAttachment rows the broken
    provider left behind have no ticket left pointing at them. So this
    now pins the OPPOSITE: the error propagates, and everything survives
    for the next sweep to retry."""

    def test_a_database_error_in_cleanup_propagates_out_of_the_purge(
        self, isolated_attachment_registry,
    ):
        """Driven directly against `purge_conversation`: the savepoint
        still isolates the DATABASE-level error (`delete_attachments_
        for` re-raises an ordinary Python exception, not a poisoned
        connection), but that exception is no longer caught here --
        it reaches this caller."""
        from agents.contracts.attachments import register_attachment_cleanup

        conversation = make_conversation()
        register_attachment_cleanup(
            "agents.tests.test_retention._raising_db_cleanup")

        with pytest.raises(Exception):
            purge_conversation(str(conversation.id))

    def test_driven_through_purge_ticket_everything_survives_and_the_connection_stays_usable(
        self, isolated_attachment_registry,
    ):
        """End to end, through `identity.retention.purge_ticket`'s own
        outer transaction: the Conversation row, its turns, its
        attachment rows AND the DeletionTicket itself are all still
        there afterwards, no `content.purged` event was written, and
        the connection is left usable -- proven with one real ORM write
        made INSIDE the same outer `transaction.atomic()` the failure
        happened in, the same technique `identity/tests/
        test_retention_runner.py::test_the_savepoint_leaves_the_
        connection_usable_after_a_db_error` uses for the runner's own
        savepoint."""
        from django.db import transaction as db_transaction

        from agents.contracts.attachments import register_attachment_cleanup
        from agents.tests._helpers import make_document
        from identity.audit import by_action
        from identity.contracts.actions import CONTENT_PURGED
        from identity.contracts.principals import SERVICE_PRINCIPAL
        from identity.models import DeletionTicket
        from identity.retention import delete_content, purge_ticket
        from tools.rag.models import DocumentAttachment

        register_attachment_cleanup(
            "agents.tests.test_retention._raising_db_cleanup")

        conversation = make_conversation()
        make_turn(conversation=conversation)
        doc = make_document(title="Notes.pdf")
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        ticket = delete_content(
            SERVICE_PRINCIPAL, kind="conversation", key=str(conversation.pk),
            owner=conversation, label="")

        with db_transaction.atomic():
            with pytest.raises(Exception):
                purge_ticket(SERVICE_PRINCIPAL, ticket)
            # THE CONNECTION IS USABLE AGAIN: a real write, inside the
            # SAME outer atomic the failure happened in, is what proves
            # the savepoint did its job rather than merely asserting it.
            assert DeletionTicket.objects.filter(pk=ticket.pk).exists()

        assert Conversation.objects.filter(pk=conversation.pk).exists()
        assert Turn.objects.filter(conversation_id=conversation.pk).exists()
        assert DocumentAttachment.objects.filter(
            conversation_id=conversation.id).exists()
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()
        purged = by_action([CONTENT_PURGED])
        assert not any(
            event.target_type == "conversation"
            and event.target_key == str(conversation.pk)
            for event in purged
        )


class TestThePurgeTouchesNoImages:
    """A generated image is no longer destroyed by the conversation's
    own purge: it has a ticket, a date and a restore of its own, and it
    is purged under that ticket -- after this handler has finished, so
    rows still go before bytes. So this handler never reaches a file at
    all."""

    def test_the_resolver_is_never_called_by_a_purge(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", artifacts=["output:1"])
        purge_conversation(str(conversation.pk))
        assert SEEN == []


class TestTheChildrenArePairs:
    def test_each_job_key_becomes_a_vision_job_pair(self):
        conversation = make_conversation()
        job_id = str(uuid.uuid4())
        make_turn(conversation=conversation, role="tool", artifacts=[],
                  data={"id": job_id, "status": "succeeded"})
        assert conversation_children(str(conversation.pk)) == [("vision_job", job_id)]

    def test_a_key_that_is_not_a_uuid_answers_empty(self):
        assert conversation_children("not-a-uuid") == []


class TestConversationPurgeCascadesChatScopedDocuments:
    """Round 12 (owner ruling, verbatim: "if I submit a document but
    have scope for chat, then it should only be used in that chat" --
    read the other way, a chat-scoped document has no life outside its
    chat either). Purging the conversation deletes the DOCUMENT too
    (vector chunks, managed-store FILES, and the row -- `tools.rag.
    services.delete_document`'s own contract), not merely its
    attachment row -- via the SAME cleanup slot `TestAttachmentRowsGoAtPurge`
    above used.

    `services.delete_document` ITSELF IS MOCKED, not exercised with
    real files on disk: this scopes each test to what round 12 actually
    added -- the CASCADE DECISION, called or not called, for the right
    document -- and trusts `delete_document`'s own file-removal
    correctness to its own test suite (`tools/rag/tests/
    test_views_documents.py::TestDocumentDelete`, which already covers
    it)."""

    def test_a_chat_scoped_documents_delete_document_is_called(self):
        from unittest.mock import patch

        from agents.tests._helpers import make_document
        from tools.rag.models import Document, DocumentAttachment

        conversation = make_conversation()
        doc = make_document(scope=Document.Scope.CONVERSATION)
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        with patch("tools.rag.services.delete_document") as mock_delete:
            purge_conversation(str(conversation.id))

        mock_delete.assert_called_once()
        (called_doc,), _ = mock_delete.call_args
        assert called_doc.pk == doc.pk

    def test_a_universal_documents_attachment_row_is_removed_but_the_document_survives(
        self,
    ):
        from unittest.mock import patch

        from agents.tests._helpers import make_document
        from tools.rag.models import Document, DocumentAttachment

        conversation = make_conversation()
        doc = make_document()  # scope defaults to Document.Scope.UNIVERSAL
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

        with patch("tools.rag.services.delete_document") as mock_delete:
            purge_conversation(str(conversation.id))

        mock_delete.assert_not_called()
        assert not DocumentAttachment.objects.filter(document=doc).exists()
        assert Document.objects.filter(pk=doc.pk).exists()


class TestZeroDayEndToEnd:
    """Until this column's handler
    was REGISTERED (Step 5, `agents/apps.py`), a delete on a zero-day box
    (`retention_days = 0`) purged the TICKET while the rows survived:
    `identity.retention.delete_content`'s own unconditional bounded sweep
    ran `identity.cascades.run_retention`, found no handler at all for
    `kind="conversation"`, deleted the now-empty ticket anyway, and the
    conversation reappeared on every list that excludes ticketed keys.
    This test goes through the REAL path end to end --
    `agents.visibility.delete_conversation`, and the REAL registration
    `agents/apps.py::ready()` makes at Django startup, no manual
    `register_retention_handler` call here -- and asserts nothing is
    left behind.
    """

    def test_a_zero_day_delete_purges_everything_synchronously(
        self, real_registration,
    ):
        from agents.tests._helpers import make_document
        from agents.visibility import delete_conversation
        from identity.audit import by_action
        from identity.contracts.actions import CONTENT_PURGED
        from identity.contracts.retention import KIND_VISION_JOB
        from identity.tests._helpers import make_generation, make_output
        from tools.rag.models import DocumentAttachment
        from tools.vision.models import GenerationJob

        user = make_user()
        principal = user_principal(user)
        with posture("personal") as row:
            row.retention_days = 0
            row.save()

            conversation = make_conversation(owner_kind="user", owner_key=str(user.pk))
            make_turn(conversation=conversation)
            doc = make_document(title="Notes.pdf")
            DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id)

            # A REAL GENERATED IMAGE, reached through the same two
            # channels `TestFindingTheGeneratedImages` pins: the
            # `output:<id>` artifact reference AND the `data["id"]`
            # generation id.
            job = make_generation()
            output = make_output(job=job)
            make_turn(conversation=conversation, role="tool", index=1,
                      artifacts=[f"output:{output.pk}"],
                      data={"id": str(job.pk), "status": "succeeded"})

            delete_conversation(principal, conversation)

            assert not Conversation.objects.filter(pk=conversation.pk).exists()
            assert not Turn.objects.filter(conversation_id=conversation.pk).exists()
            assert not DocumentAttachment.objects.filter(
                conversation_id=conversation.id).exists()
            assert not DeletionTicket.objects.filter(
                kind="conversation", key=str(conversation.pk)).exists()
            if "vision" in settings.FARABUNKER_FEATURES:
                # THE IMAGE'S OWN TICKET AND ROW ARE BOTH GONE TOO: the
                # zero-day sweep that purges the conversation's own
                # ticket reaches its child in the same call. Guarded:
                # with "vision" off nothing is registered on the slot,
                # so `conversation_children` never tickets this image
                # in the first place and its row is not this handler's
                # to destroy.
                assert not DeletionTicket.objects.filter(
                    kind=KIND_VISION_JOB, key=str(job.pk)).exists()
                assert not GenerationJob.objects.filter(pk=job.pk).exists()
            purged = by_action([CONTENT_PURGED])
            assert any(
                event.target_type == "conversation"
                and event.target_key == str(conversation.pk)
                for event in purged
            )
