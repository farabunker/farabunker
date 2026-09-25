"""The demo, end to end, in the personal posture.

ONE TEST CLASS, ONE STORY: a conversation with a turn, a chat-scoped
document, a generated image and an Ask record; delete it; check it is
invisible and dated; delete it permanently; check that after the
redirect NO SURFACE ON THIS BOX renders any part of it.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from agents.models import Conversation, ToolInvocation, Turn
from agents.visibility import visible_conversations
from identity import audit
from identity.contracts import retention as copy
from identity.contracts.actions import CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_conversation, make_document, make_generation, make_output,
    make_queue_job, make_turn, make_user, posture, sign_in, user_principal,
)
from tools.rag import index as rag_index
from tools.rag.access import readable_documents, visible_ask_records
from tools.rag.models import AskRecord, Document, DocumentAttachment
from tools.vision import store as vision_store

pytestmark = pytest.mark.django_db

InferenceJob = apps.get_model("jobs.InferenceJob")


def index_chunks_for(document) -> None:
    """Write one real chunk row for `document`, keyed on `file_id` --
    the same metadata key `tools.rag.index.delete_chunks_for_document`
    filters on when it tears a document's chunks down.

    THERE IS NO PRODUCTION "WRITE ONE CHUNK" HELPER in
    `tools/rag/index.py`: chunks are written only by LlamaIndex's own
    ingestion pipeline, which needs a working `rag.embed` binding this
    test does not configure (this repository's whole test suite avoids
    that -- see `tools/rag/tests/test_ingest.py`'s own module
    docstring: "these tests never embed or call a real LLM"). This
    writes the row directly instead, the same minimal shape
    `tools/rag/tests/test_labels.py::_seed_chunks` uses for the
    identical reason.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {rag_index.LIVE_TABLE_NAME} "
            f"(id bigserial primary key, metadata_ jsonb)")
        cursor.execute(
            f"INSERT INTO {rag_index.LIVE_TABLE_NAME} (metadata_) VALUES (%s)",
            [json.dumps({"file_id": str(document.pk)})])


def chunk_count_for(doc_id) -> int:
    """How many chunks carry `doc_id` under the same `file_id` key --
    there is no production count helper in `tools/rag/index.py` either,
    so this reads the table directly, the way `index_chunks_for` above
    writes to it."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT COUNT(*) FROM {rag_index.LIVE_TABLE_NAME} "
            f"WHERE metadata_->>'file_id' = %s", [str(doc_id)])
        return cursor.fetchone()[0]


class _FakeChunkStore:
    """Stands in for `tools.rag.index.get_vector_store()`'s real
    `PGVectorStore`, for the one call this test drives for real through
    the purge route: `tools.rag.services.delete_document`'s own
    `rag_index.delete_chunks_for_document(document.id)`.

    A genuine `PGVectorStore` opens ITS OWN SQLAlchemy connection,
    separate from Django's -- `tools/rag/tests/
    test_index_hybrid_integration.py`'s own docstring explains why that
    needs `django_db(transaction=True)` and an explicit teardown (a
    write on one connection is invisible to a delete on the other until
    both commit). This module runs under the ordinary
    (transaction-rolled-back) `django_db` marker every other test file
    here uses, so this keeps the write `index_chunks_for` does above and
    this real delete call on the ONE connection the rest of the test
    already uses -- no second commit path, no manual teardown, and the
    production `delete_chunks_for_document` function itself still runs
    unmocked, exactly as the real purge route calls it.
    """

    def delete_nodes(self, filters) -> None:
        file_id = filters.filters[0].value
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM {rag_index.LIVE_TABLE_NAME} "
                f"WHERE metadata_->>'file_id' = %s", [file_id])


@pytest.fixture
def world(tmp_path, settings, monkeypatch):
    """A conversation carrying every kind of content this box stores.

    EVERY ROW COMES FROM AN EXISTING BUILDER -- `identity/tests/
    _helpers.py`'s, which resolve through `apps.get_model` precisely
    because this module spans every column. No new builder, no
    `conftest.py`.

    `monkeypatch` (an addition beyond the row builders): it binds
    `tools.rag.index.get_vector_store` to `_FakeChunkStore` above, for
    the reason that class's own docstring gives.
    """
    settings.NOTES_DIR = tmp_path
    settings.GENERATED_DIR = tmp_path / "generated"
    monkeypatch.setattr(rag_index, "get_vector_store", lambda: _FakeChunkStore())

    user = make_user()
    owner = {"owner_kind": "user", "owner_key": str(user.pk)}

    conversation = make_conversation(title="A thread", **owner)

    # The generated image, its output row, and the queue row that made it.
    # A REAL FILE, written through `tools.vision.store.store_output` --
    # the same function `services.generate`'s own write path calls --
    # so its path has the exact `<GENERATED_DIR>/<job_id>/<index>-<name>`
    # shape `store.remove_job_files` (called by `services.delete_job`)
    # removes by `shutil.rmtree`-ing the job's whole directory. `path=
    # "/dev/null"` (`make_output`'s own default) would pass every
    # assertion the demo made before -- there was no file to check.
    generation_queue_job = make_queue_job(
        kind="vision.generate", state="succeeded", priority=200,
        payload={"operation": "txt2img", "params": {"prompt": "a lighthouse"}})
    job = make_generation(queue_job_id=generation_queue_job.pk, **owner)
    output_path = vision_store.store_output(
        job.pk, 0, "lighthouse.png", b"not a real PNG, just bytes on disk")
    output = make_output(job=job, path=output_path)

    # The tool record whose words the purge scrubs, and the tool turn
    # that reaches BOTH channels: an artifact reference AND a generation
    # id in `data`.
    invocation = ToolInvocation.objects.create(
        principal_kind="user", principal_key=str(user.pk),
        tool_key="vision__generate", args={"prompt": "a lighthouse"},
        text="Generated 1 image.", error="", outcome="ok")
    make_turn(conversation=conversation, index=0, role="user",
              text="draw me a lighthouse")
    make_turn(conversation=conversation, index=1, role="tool",
              text="Generated 1 image.",
              artifacts=[f"output:{output.pk}"],
              data={"id": str(job.pk), "status": "succeeded"},
              invocation_id=invocation.pk)

    # The chat-scoped document, its attachment claim, and its chunks.
    document = make_document(scope="conversation", **owner)
    DocumentAttachment.objects.create(document=document,
                                      conversation_id=conversation.id)
    index_chunks_for(document)

    # The staging note the consolidation path writes.
    note_path = tmp_path / f"{conversation.id}.md"
    note_path.write_text("a consolidated stream", encoding="utf-8")
    Document.objects.filter(pk=document.pk).update(
        notes_conversation_id=conversation.id)

    # The queue row carrying the person's literal message.
    turn_queue_job = make_queue_job(
        kind="agent.turn", state="succeeded", priority=100,
        payload={"conversation": str(conversation.id), "turn": 1,
                 "agent": "assistant", "text": "draw me a lighthouse"})

    ask = AskRecord.objects.create(
        question="what is in the lighthouse report",
        answer="a lighthouse", citations=[], connection_name="a connection",
        model_id="an-identifier", **owner)

    return SimpleNamespace(
        user=user,
        conversation=conversation,
        invocation=invocation,
        document=document,
        job=job,
        output=output,
        output_path=output_path,
        ask=ask,
        note_path=note_path,
        turn_queue_job=turn_queue_job,
        generation_queue_job=generation_queue_job,
        chunk_count_for_document=lambda: chunk_count_for(document.pk),
    )


class TestTheDemo:
    def test_step_2_a_delete_hides_it_everywhere_and_prints_a_date(self, client, world):
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            principal = user_principal(world.user)
            assert list(visible_conversations(principal)) == []
            assert world.document not in readable_documents(principal)

            body = client.get(reverse("identity-deleted")).content.decode()
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            assert copy.purge_on_line(ticket.purge_on) in body
            assert ticket.purge_on == timezone.localdate() + datetime.timedelta(days=30)

            # NOTHING HAS BEEN PURGED YET: the rows are all still there --
            # every fixture item, not just the conversation and its turns.
            assert Conversation.objects.filter(pk=world.conversation.pk).exists()
            assert Turn.objects.filter(conversation_id=world.conversation.pk).exists()
            assert DocumentAttachment.objects.filter(
                document=world.document, conversation_id=world.conversation.id).exists()
            invocation = ToolInvocation.objects.get(pk=world.invocation.pk)
            assert invocation.args and invocation.text
            if "vision" in settings.FARABUNKER_FEATURES:
                assert apps.get_model("vision.GenerationJob").objects.filter(
                    pk=world.job.pk).exists()
                assert Path(world.output_path).exists()
            assert world.chunk_count_for_document() == 1

    def test_step_2c_the_image_is_hidden_listed_and_unfetchable(self, client, world):
        """THE WHOLE POINT OF THE CHILD TICKET: the picture goes when
        the chat goes, it is NAMED on the Deleted page with its own
        date, and its direct URL stops answering -- rather than sitting
        in the gallery until a date nobody was shown."""
        if "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the image column is not installed in this flag state")
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            principal = user_principal(world.user)

            from tools.vision.visibility import visible_jobs
            assert list(visible_jobs(principal)) == []
            assert client.get(reverse("vision-output-file",
                                      args=[world.output.pk])).status_code == 404

            child = DeletionTicket.objects.get(kind=copy.KIND_VISION_JOB,
                                               key=str(world.job.pk))
            parent = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            assert child.parent_id == parent.pk
            assert child.purge_on == parent.purge_on
            body = client.get(reverse("identity-deleted")).content.decode()
            assert copy.KIND_LABELS[copy.KIND_VISION_JOB] in body
            # The row is still there and the bytes are still on disk --
            # deleted is not destroyed.
            assert Path(world.output_path).exists()

    def test_step_2d_restoring_the_chat_restores_its_image(self, client, world):
        if "vision" not in settings.FARABUNKER_FEATURES:
            pytest.skip("the image column is not installed in this flag state")
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-restore", args=[ticket.pk]))

            from tools.vision.visibility import visible_jobs
            principal = user_principal(world.user)
            assert [job.pk for job in visible_jobs(principal)] == [world.job.pk]
            assert not DeletionTicket.objects.filter(
                kind=copy.KIND_VISION_JOB).exists()
            assert client.get(reverse("vision-output-file",
                                      args=[world.output.pk])).status_code == 200

    def test_step_2b_restore_brings_it_back_before_step_3_deletes_it_again(
            self, client, world):
        """THE RESTORE DOOR step 2's own notice promises: the same
        thread deleted, restored, and deleted again -- one demo, not
        two -- so step 3 (permanent delete) still starts from a freshly
        deleted ticket, exactly as it always has."""
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            principal = user_principal(world.user)
            assert list(visible_conversations(principal)) == []

            body = client.get(reverse("identity-deleted")).content.decode()
            assert world.conversation.title in body
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)

            client.post(reverse("identity-deleted-restore", args=[ticket.pk]))

            assert world.conversation in list(visible_conversations(principal))
            assert not DeletionTicket.objects.filter(pk=ticket.pk).exists()
            restored = audit.by_action([CONTENT_RESTORED])
            own = [e for e in restored
                   if e.target_type == copy.KIND_CONVERSATION
                   and e.target_key == str(world.conversation.pk)]
            assert len(own) == 1
            if "vision" in settings.FARABUNKER_FEATURES:
                # THE IMAGE CAME BACK TOO -- asserted, not filtered away:
                # that is the whole point of the child ticket.
                assert [e.target_key for e in restored
                        if e.target_type == copy.KIND_VISION_JOB] == [
                            str(world.job.pk)]

            # DELETE AGAIN -- a fresh ticket, so the rest of the demo
            # (step 3's permanent delete) starts from the same state it
            # always has.
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            assert list(visible_conversations(principal)) == []
            assert DeletionTicket.objects.filter(kind=copy.KIND_CONVERSATION).count() == 1

    def test_step_3_delete_permanently_leaves_nothing_on_this_box(self, client, world):
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))

            principal = user_principal(world.user)
            assert list(visible_conversations(principal)) == []
            assert not Conversation.objects.filter(pk=world.conversation.pk).exists()
            assert not Turn.objects.filter(conversation_id=world.conversation.pk).exists()
            # The generated image, its files and its rows -- reached
            # through its OWN child ticket, written when the conversation
            # was deleted, and purged as this parent's own click reaches
            # it -- only while `tools/vision/apps.py::ready()` registers
            # the resolver in the first place: with "vision" off nothing
            # is registered, no child ticket is written, and the row is
            # never destroyed by a conversation purge, so this assertion
            # would pin a promise the box never made in that flag state.
            if "vision" in settings.FARABUNKER_FEATURES:
                assert not apps.get_model("vision.GenerationJob").objects.filter(
                    pk=world.job.pk).exists()
                assert not Path(world.output_path).exists()
            # The chat-scoped document, its attachment claim, its chunks
            # and its bytes.
            assert not apps.get_model("rag.Document").objects.filter(
                pk=world.document.pk).exists()
            assert not DocumentAttachment.objects.filter(
                document_id=world.document.pk).exists()
            assert world.chunk_count_for_document() == 0
            # The staging note file.
            assert not world.note_path.exists()
            # The tool-call words are blanked and the SHELL SURVIVES.
            invocation = ToolInvocation.objects.get(pk=world.invocation.pk)
            assert invocation.args == {} and invocation.text == ""
            assert invocation.tool_key and invocation.outcome
            # And no ticket is left for that conversation.
            assert not DeletionTicket.objects.filter(
                kind=copy.KIND_CONVERSATION).exists()
            # NO TICKET OF ANY KIND SURVIVES THE CHAT'S PERMANENT
            # DELETE: the image's own child ticket goes with it.
            assert not DeletionTicket.objects.exists()

    def test_the_queue_row_survives_until_the_queue_half_lands(self, client, world):
        """QUEUE HALF NOT LANDED, and this test says so rather than
        leaving the state ambiguous. `models/queue/retention.py` does not
        exist yet, so nothing is registered for the `conversation` kind
        in the ROWS band and the `agent.turn` row keeps the person's
        literal message in `payload["text"]`.

        TASK 19 RENAMES THIS to `test_the_queue_row_is_gone` and asserts
        `.count() == 0`. Nothing else in this module changes.
        """
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            assert InferenceJob.objects.filter(
                payload__conversation=str(world.conversation.pk)).count() == 1

    def test_the_generations_queue_row_survives_until_the_queue_half_lands(
            self, client, world):
        """QUEUE HALF NOT LANDED. `models.contracts.queue.forget_jobs`
        does not exist yet, so the `vision.generate` row -- which carries
        the prompt in `payload["params"]` -- outlives its job.

        TASK 19 RENAMES THIS to `test_the_generations_queue_row_is_gone`
        and asserts `.count() == 0`.
        """
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            assert InferenceJob.objects.filter(
                pk=world.generation_queue_job.pk).exists()

    def test_step_4_the_ask_record_is_not_deletable_until_slice_two(self, client, world):
        """SLICE 2 BUILDS THE ROUTE. Until then this asserts the record
        is still there and still listed -- the honest state -- and
        task 21 rewrites it to post the delete and assert its absence
        from `HistoryView`'s context.
        """
        with posture("personal"):
            sign_in(client, world.user)
            assert world.ask in visible_ask_records(user_principal(world.user))

    def test_step_5_the_purged_event_is_content_free_with_the_toggle_off(
            self, client, world):
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            # THE CONVERSATION'S OWN EVENT, narrowed: in the "vision"
            # flag state the image writes a `content.purged` event of
            # its own too, and this test's subject is the conversation's.
            event = AuditEvent.objects.filter(
                action=CONTENT_PURGED, target_type=copy.KIND_CONVERSATION).get()
            assert event.target_type == copy.KIND_CONVERSATION
            assert event.target_key == str(world.conversation.pk)
            assert event.target_label == ""
            assert event.actor_key == str(world.user.pk)
            assert all(isinstance(count, int)
                       for count in event.detail["removed"].values())

    def test_flipping_the_toggle_labels_the_next_item_only(self, client, world):
        """Step 5's second half: no event is suppressed in either
        position, and the label follows the setting IN FORCE AT THE
        MOMENT OF THE DELETE -- not retroactively."""
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))

            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()

            second = make_conversation(
                title="A second thread", owner_kind="user",
                owner_key=str(world.user.pk))
            client.post(reverse("chat-conversation-delete", args=[second.id]))

        events = {e.target_key: e.target_label for e
                  in AuditEvent.objects.filter(action=CONTENT_DELETED)}
        assert events[str(world.conversation.id)] == ""
        assert events[str(second.id)] == "A second thread"
