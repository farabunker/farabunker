"""`agents.retention.purge_conversation` -- the agents-side teardown,
plus the two channels that find a conversation's generated images."""
from __future__ import annotations

import datetime
import uuid

import pytest
from django.conf import settings
from django.utils import timezone

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


def fake_artifact_children(refs, generation_ids) -> list[tuple[str, str, str]]:
    SEEN.append((tuple(refs), tuple(generation_ids)))
    return [(key, "", "") for key in sorted(set(refs) | set(generation_ids))]


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
        assert conversation_children(str(conversation.pk)) == [("vision_job", "output:4", "", "")]
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


class TestADuplicateDoesNotTicketTheOriginalsImages:
    """PATH C (cross-owner investigation §2, "the same-owner correctness
    bug inside Path B"): `duplicate_conversation`/`branch_conversation`
    copy `artifacts` and `data` VERBATIM (`agents/visibility.py::
    _copy_turns_into`), so a copy's tool turn names the exact SAME
    `output:<id>` reference and the exact same `data["id"]` the
    original's own turn does. IF THIS FIXTURE WERE FED TO THE UNFIXED
    RESOLVER, it would find that reference on the copy's own turns and
    ticket the job -- exactly the same job a DIFFERENT, live,
    undeleted conversation still shows. This needs no second principal
    and no sharing: one person, one conversation, one copy of it."""

    def test_a_reference_shared_with_a_still_live_conversation_is_dropped(self):
        # ONE SHARED AGENT: `make_conversation()` defaults to a fresh
        # `make_agent()` per call, which collides on that helper's own
        # fixed "test-agent" slug when called twice in one test.
        agent = make_agent()
        original = make_conversation(agent=agent)
        make_turn(conversation=original, role="tool", index=0,
                  artifacts=["output:1"], data={"id": "job-1", "status": "succeeded"})
        # THE COPY: same artifact reference, same generation id, on a
        # SEPARATE conversation row -- exactly what `_copy_turns_into`
        # produces, without going through the whole `duplicate_
        # conversation` gate for this unit-level pin.
        duplicate = make_conversation(agent=agent)
        make_turn(conversation=duplicate, role="tool", index=0,
                  artifacts=["output:1"], data={"id": "job-1", "status": "succeeded"})

        assert conversation_children(str(duplicate.pk)) == []
        # THE ASSERTION THAT WOULD FLIP IF THE BEHAVIOUR REGRESSED: an
        # unfixed resolver hands the resolver BOTH the reference and the
        # generation id, and `fake_artifact_children` would answer
        # `["job-1", "output:1"]`, not the empty pair this fixture must
        # see for the ticket count above to hold.
        refs, ids = SEEN[0]
        assert refs == () and ids == ()

    def test_a_reference_named_by_no_other_conversation_still_tickets(self):
        """THE FIX MUST NOT OVER-EXCLUDE: a reference only THIS
        conversation's turns carry is unaffected, so an ordinary,
        un-shared delete still tickets its own images exactly as
        before."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", artifacts=["output:9"])
        assert conversation_children(str(conversation.pk)) == [("vision_job", "output:9", "", "")]

    def test_once_the_other_conversation_is_itself_deleted_the_reference_tickets(self):
        """EVENTUALLY CONSISTENT, NOT PERMANENTLY SUPPRESSED: a
        conversation already ticketed (on its own way out) does not
        count as "another live conversation" -- once the original is
        itself deleted, the duplicate's own delete finally reaches the
        job nothing else is still showing."""
        agent = make_agent()
        original = make_conversation(agent=agent)
        make_turn(conversation=original, role="tool", artifacts=["output:5"])
        duplicate = make_conversation(agent=agent)
        make_turn(conversation=duplicate, role="tool", artifacts=["output:5"])

        DeletionTicket.objects.create(
            kind="conversation", key=str(original.pk),
            purge_on=timezone.localdate() + datetime.timedelta(days=30))

        assert conversation_children(str(duplicate.pk)) == [("vision_job", "output:5", "", "")]

    def test_branching_and_deleting_the_branch_leaves_the_originals_image_untouched(
        self, real_registration,
    ):
        """END TO END, through the REAL registrations `agents/apps.py::
        ready()` and `tools/vision/apps.py::ready()` make at Django
        startup -- the brief's own named pin: branch a conversation,
        delete the branch, and the original's image is untouched and
        still fetchable."""
        from agents.visibility import branch_conversation, delete_conversation
        from identity.contracts.retention import KIND_VISION_JOB
        from identity.tests._helpers import make_generation, make_output
        from tools.vision.visibility import may_read_job

        user = make_user()
        principal = user_principal(user)
        with posture("personal"):
            original = make_conversation(owner_kind="user", owner_key=str(user.pk))
            job = make_generation(owner_kind="user", owner_key=str(user.pk))
            output = make_output(job=job)
            make_turn(conversation=original, role="user", index=0,
                      text="draw me a lighthouse")
            make_turn(conversation=original, role="tool", index=1,
                      artifacts=[f"output:{output.pk}"],
                      data={"id": str(job.pk), "status": "succeeded"})
            branch_point = make_turn(conversation=original, role="user", index=2,
                                     text="thanks")

            branch = branch_conversation(principal, original, branch_point,
                                         title="A branch")
            assert branch is not None

            deleted = delete_conversation(principal, branch)
            assert deleted is not None

            if "vision" in settings.FARABUNKER_FEATURES:
                assert not DeletionTicket.objects.filter(
                    kind=KIND_VISION_JOB, key=str(job.pk)).exists()
            assert may_read_job(principal, job) is True


class TestTheChildTicketCarriesTheRealOwnerEndToEnd:
    """Every cross-owner assertion elsewhere in this feature runs
    against an IDENTITY-LEVEL FAKE resolver (`identity/tests/
    test_retention_service.py::cross_owner_children`, `identity/tests/
    test_deleted_page.py::_cross_owner_image_child`); every test that
    runs the REAL vision pipeline
    (`test_branching_and_deleting_the_branch_leaves_the_originals_image_
    untouched` above) is same-owner. So the one seam this feature widens
    across three columns -- `existing_job_ids` -> `resolve_artifact_jobs`
    (`tools/vision/retention.py`) -> `conversation_children`
    (`agents/retention.py`) -> `identity.retention.delete_content` -- is
    proved a leg at a time and never as a whole: a tuple-order slip
    anywhere in that chain (`(owner_key, owner_kind)` transposed, say)
    would be caught by each leg's own fixture and by nothing that runs
    them together. This drives the whole chain, with two real users and
    the real, app-registered handlers."""

    def test_a_real_cross_owner_image_is_ticketed_under_its_own_owner(
        self, real_registration,
    ):
        from agents.visibility import delete_conversation
        from identity.contracts.retention import KIND_VISION_JOB
        from identity.tests._helpers import make_generation, make_output

        a, b = make_user(), make_user()
        principal = user_principal(a)
        with posture("personal"):
            conversation = make_conversation(owner_kind="user", owner_key=str(a.pk))
            # THE IMAGE BELONGS TO B, NOT TO A -- a workstream share or
            # an administrator's duplicate that let a second principal's
            # content sit inside A's own conversation. If stamping ever
            # regressed to reading the PARENT's owner columns instead of
            # the content's own, `child.owner_key` below would read
            # `str(a.pk)`, not `str(b.pk)`, and the final `!=` against
            # the parent ticket's own owner would fail too.
            job = make_generation(owner_kind="user", owner_key=str(b.pk))
            output = make_output(job=job)
            make_turn(conversation=conversation, role="user", index=0,
                      text="draw me a lighthouse")
            make_turn(conversation=conversation, role="tool", index=1,
                      artifacts=[f"output:{output.pk}"],
                      data={"id": str(job.pk), "status": "succeeded"})

            parent = delete_conversation(principal, conversation)
            assert parent is not None

            if "vision" in settings.FARABUNKER_FEATURES:
                child = DeletionTicket.objects.get(
                    kind=KIND_VISION_JOB, key=str(job.pk))
                assert (child.owner_kind, child.owner_key) == ("user", str(b.pk))
                assert (child.owner_kind, child.owner_key) != (
                    parent.owner_kind, parent.owner_key)


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
        assert conversation_children(str(conversation.pk)) == [("vision_job", job_id, "", "")]

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


class TestOrderingAgainstRag:
    """Both handlers registered for `KIND_CONVERSATION` sit in the FILES
    band: `agents.conversation` (`agents/apps.py`) removes bytes through
    the attachment seam (a chat's own attached documents), and
    `rag.conversation_notes` (`tools/rag/apps.py`) removes the
    consolidated notes file and its ingested copy. A band is stable by
    registration order, and `tools.rag` precedes `agents` in
    `INSTALLED_APPS`, so `rag.conversation_notes` runs FIRST.

    THAT ORDER IS WHAT KEEPS A DISK FAILURE SAFE. `rag.conversation_
    notes`'s one raising path (a non-`FileNotFoundError` `OSError` on
    `NOTES_DIR` -- a read-only volume, permissions, a full disk --
    deliberately not swallowed) aborts the purge before `agents.
    conversation` ever runs, so the bytes THAT handler would have
    removed are never touched, and `identity.retention.purge_ticket`'s
    rollback restores exactly what stood before the call. Reversed, the
    conversation handler would destroy a chat's attached documents --
    files and pgvector chunks both -- before `rag.conversation_notes`
    raised, and the rollback would restore the `Document` rows while
    their bytes stayed gone: a resurrected row pointing at nothing.

    Both pins below run against the REAL registrations `agents/apps.py
    ::ready()` and `tools/rag/apps.py::ready()` made at Django startup,
    not a synthetic registry -- `identity/tests/test_retention_contracts.py`
    and `identity/tests/test_retention_runner.py` already pin the
    GENERAL rows-before-files mechanism; this is the one place that pins
    these two columns' own order against each other."""

    def test_rags_notes_handler_is_ordered_ahead_of_the_conversation_handler(
        self, real_registration,
    ):
        from identity.contracts.cascades import retention_handlers
        from identity.contracts.retention import KIND_CONVERSATION

        keys = [spec.key for spec in retention_handlers(KIND_CONVERSATION)]
        assert "agents.conversation" in keys
        assert "rag.conversation_notes" in keys
        assert keys.index("rag.conversation_notes") < keys.index("agents.conversation")

    def test_a_raising_rag_handler_leaves_the_conversations_rows_and_bytes_untouched(
        self, real_registration, monkeypatch, tmp_path, settings,
    ):
        """End to end through the real runner, `identity.cascades.
        run_retention`, wrapped the same way `identity.retention.
        purge_ticket` itself wraps every call -- one `transaction.
        atomic()` directly around the run, nothing catching between it
        and a raise, so a handler that fails rolls the whole block back
        before the exception ever reaches a caller. `rag.conversation_
        notes` runs FIRST and raises immediately; because the runner
        never swallows, that exception propagates out of the
        `transaction.atomic()` wrapping the call, which rolls back
        everything inside it. Proven by a spy on the conversation
        handler, not merely by the rows surviving: it must never be
        called at all. And proven on disk, not only in the database --
        this fixture gives the conversation a real chat-scoped document
        with a file in the managed store, the one shape no earlier
        fixture in this module carried, so the file's survival is
        actually exercised rather than vacuously true because nothing
        was ever there to lose."""
        import agents.retention as agents_retention_module
        import tools.rag.retention as rag_retention_module
        from django.db import transaction as db_transaction

        from agents.tests._helpers import make_document
        from identity.cascades import run_retention
        from identity.contracts.retention import KIND_CONVERSATION
        from tools.rag import store
        from tools.rag.models import DocumentAttachment

        settings.DOCUMENTS_DIR = tmp_path

        conversation = make_conversation()
        make_turn(conversation=conversation)

        document = make_document(title="Notes.pdf", scope="conversation")
        DocumentAttachment.objects.create(
            document=document, conversation_id=conversation.id)
        doc_dir = store.document_dir(document.pk)
        doc_dir.mkdir(parents=True)
        (doc_dir / "Notes.pdf").write_text("content", encoding="utf-8")

        calls: list[str] = []
        real_purge = agents_retention_module.purge_conversation

        def _spy_purge(key: str) -> int:
            calls.append("agents")
            return real_purge(key)

        def _raise(key: str) -> int:
            calls.append("rag")
            raise RuntimeError("disk is unavailable")

        monkeypatch.setattr(agents_retention_module, "purge_conversation", _spy_purge)
        monkeypatch.setattr(rag_retention_module, "purge_conversation_notes", _raise)

        with pytest.raises(RuntimeError, match="disk is unavailable"):
            with db_transaction.atomic():
                run_retention(KIND_CONVERSATION, str(conversation.pk))

        assert calls == ["rag"]
        assert Conversation.objects.filter(pk=conversation.pk).exists()
        assert Turn.objects.filter(conversation_id=conversation.pk).exists()
        assert DocumentAttachment.objects.filter(
            document=document, conversation_id=conversation.id).exists()
        # SURVIVAL ONLY, NOT SELF-GUARDING: this line alone would still
        # pass if the fixture ever stopped being at risk (a renamed
        # scope value, a filter change, an unregistered provider). What
        # guards it today is a sibling in this file that pins the same
        # fixture shape against the cascade actually firing --
        # `test_a_chat_scoped_documents_delete_document_is_called`
        # (`agents/tests/test_retention.py:416`).
        assert doc_dir.exists()


class TestTheFailedPurgeMarkRefusesRestore:
    """`TestOrderingAgainstRag` above closes the most likely way a purge
    breaks its promise to the person who deleted something: a handler
    that raises BEFORE any byte moves. It cannot close every way, because
    the band rule it relies on only orders handlers WITHIN one
    `run_retention` call (`identity/retention.py:433-443` says so in its
    own words) -- and this item's OWN handler can itself destroy bytes
    and then a LATER step of the SAME purge can still fail.

    Here, that later step is `agents.retention.scrub_tool_records`: it
    runs after `delete_attachments_for` has already removed a chat-scoped
    document's files (`agents/retention.py:220,225`), so a failure there
    rolls `identity.retention.purge_ticket`'s transaction back -- the
    conversation and the document ROW both reappear -- while the file a
    `shutil.rmtree` already removed does not. What used to happen next: a
    Deleted page that still offered Restore for the conversation, and a
    Restore that handed back a conversation whose attached document would
    not open. Now the ticket itself remembers a files-band handler ran
    before the failure (`identity.cascades.run_retention`'s `on_files_band`
    callback, read back by `identity.retention.record_failed_purge` in
    `_purge_due`'s own `except Exception`), and `identity.retention.
    may_restore` refuses Restore for it -- the promise holds because
    Restore is no longer offered, not because the document came back."""

    def test_restore_after_a_purge_that_fails_once_its_files_are_gone_is_refused(
        self, real_registration, monkeypatch, tmp_path, settings,
    ):
        import agents.retention as agents_retention_module
        from tools.rag import store

        from agents.tests._helpers import make_document
        from identity.contracts.retention import KIND_CONVERSATION
        from identity.retention import delete_content, may_restore
        from tools.rag.models import DocumentAttachment

        settings.DOCUMENTS_DIR = tmp_path

        user = make_user()
        actor = user_principal(user)

        def _raise(invocation_ids):
            raise RuntimeError("scrub failed after the bytes were already gone")

        monkeypatch.setattr(agents_retention_module, "scrub_tool_records", _raise)

        with posture("personal") as row:
            # `retention_days = 0` makes the ticket due at once, so
            # `delete_content`'s own bounded sweep -- the SAME sweep an
            # ordinary delete always triggers, not a synthetic call --
            # purges it before this call returns. The raise above lands
            # AFTER `delete_attachments_for` has removed the document's
            # files and BEFORE the ticket itself is deleted, so
            # `identity.retention._purge_due`'s own swallow (identical to
            # a production sweep) logs it, rolls the row changes back,
            # and leaves the ticket standing due -- exactly as it would
            # on a real box.
            row.retention_days = 0
            row.save()

            conversation = make_conversation(
                owner_kind="user", owner_key=str(user.pk))
            make_turn(conversation=conversation)

            document = make_document(title="Notes.pdf", scope="conversation")
            DocumentAttachment.objects.create(
                document=document, conversation_id=conversation.id)
            doc_dir = store.document_dir(document.pk)
            doc_dir.mkdir(parents=True)
            (doc_dir / "Notes.pdf").write_text("content", encoding="utf-8")

            ticket = delete_content(
                actor, kind=KIND_CONVERSATION, key=str(conversation.pk),
                owner=conversation)

        # The files are really gone -- not a fixture mistake, the premise
        # of the whole scenario.
        assert not doc_dir.exists()

        still_stands = DeletionTicket.objects.filter(pk=ticket.pk).exists()
        if not still_stands:
            return  # the promise holds trivially: nothing left to offer

        ticket.refresh_from_db()
        assert ticket.content_unrecoverable is True, (
            "the purge destroyed the document's bytes and then failed, "
            "but left no mark behind"
        )
        assert may_restore(ticket) is False, (
            "the ticket stands, its document's bytes are gone, and "
            "Restore is still offered for it"
        )
