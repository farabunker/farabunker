"""Round 12 (owner ruling, verbatim: "if I submit a document but have
scope for chat, then it should only be used in that chat. If, however,
I change the scope to workstream, it should be made available via the
rag framework") -- the corpus-behavior tests the brief's own minimum
matrix names, gathered in ONE dedicated file.

ROUND 12 REVIEW I-4: an earlier commit's `test_upload_attachment.py`
docstring already pointed readers here as "round 12's own file for
chat-scope-specific behaviour" before this file existed. This is that
file, not a repoint -- the brief's missing corpus tests (retrievable in
its own chat / absent from another conversation / absent from a stream
corpus / absent from Search+Ask), the `_attach` invariant guard, and
the locked-library exemption (round 12 review I-3) all live here.

`_matches`, below, is a deliberately tiny, pure-Python evaluator of the
SAME `MetadataFilters` shape the live Postgres store applies (just
enough of EQ/IS_EMPTY/ANY and AND/OR) -- it is what turns "the filter
OBJECT has the expected shape" (the review's own complaint about the
coverage that existed before this file) into "this exact chunk's
metadata WOULD or WOULD NOT be returned", without a live vector store.
"""
from __future__ import annotations

import uuid

import pytest
from llama_index.core.vector_stores.types import (
    FilterCondition, FilterOperator, MetadataFilter,
)

from agents.contracts.workstreams import WorkstreamScope
from identity.contracts.principals import OPEN_PRINCIPAL
from tools.rag import retrieval
from tools.rag.access import DocumentVisibility, attached_documents, readable_documents
from tools.rag.models import Document, DocumentAttachment
from tools.rag.tests._helpers import _workstream, make_document, make_user, posture

pytestmark = pytest.mark.django_db


def _matches(filters, metadata: dict) -> bool:
    """Whether a chunk carrying `metadata` would pass `filters` (a
    `retrieval._visibility_filters(...)` return value) -- see this
    module's own docstring for why this exists."""
    if isinstance(filters, MetadataFilter):
        value = metadata.get(filters.key)
        if filters.operator == FilterOperator.EQ:
            return value == filters.value
        if filters.operator == FilterOperator.IS_EMPTY:
            return value is None
        if filters.operator == FilterOperator.ANY:
            if value is None:
                return False
            held = value if isinstance(value, list) else [value]
            wanted = filters.value if isinstance(filters.value, list) else [filters.value]
            return bool(set(held) & set(wanted))
        raise NotImplementedError(f"test evaluator: unhandled operator {filters.operator!r}")
    combine = all if filters.condition == FilterCondition.AND else any
    return combine(_matches(child, metadata) for child in filters.filters)


def _chat_scoped(conversation_id, **overrides):
    doc = make_document(scope=Document.Scope.CONVERSATION, **overrides)
    DocumentAttachment.objects.create(document=doc, conversation_id=conversation_id)
    return doc


class TestTheChunkFilterCorpus:
    """`tools.rag.retrieval._visibility_filters`, exercised through
    `_matches` -- the brief's own minimum matrix, proven against REAL
    chunk-metadata shapes `tools.rag.labels.restamp_document_chunks`
    actually writes (`tools/rag/tests/test_labels.py::
    TestTheConversationKey` pins that writer; this pins the READER)."""

    def test_a_chat_scoped_chunk_is_retrievable_in_its_own_chat(self):
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True, conversation_id="conv-a")
        filters = retrieval._visibility_filters(None, v)
        assert _matches(filters, {"conversation": "conv-a"})

    def test_a_chat_scoped_chunk_is_absent_from_another_conversations_corpus(self):
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True, conversation_id="conv-b")
        filters = retrieval._visibility_filters(None, v)
        assert not _matches(filters, {"conversation": "conv-a"})

    def test_a_chat_scoped_chunk_is_absent_from_search_and_ask(self):
        """Search/Ask/`manage.py ask` never set `conversation_id`
        (`""` is falsy, same as `None` here) -- the ONE surface every
        chat-scoped chunk can never reach."""
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True)
        filters = retrieval._visibility_filters(None, v)
        assert not _matches(filters, {"conversation": "conv-a"})

    def test_a_chat_scoped_chunk_is_absent_from_a_stream_corpus_even_with_a_wall_open(self):
        """Attached from INSIDE a stream conversation (a chat-scoped
        document is never contained, so its chunk carries NO
        `workstream` key regardless of where it was attached from) --
        a DIFFERENT turn in that SAME stream, with no matching
        `conversation_id`, must not retrieve it even when the wall
        would otherwise admit an ordinary unlabelled universal chunk."""
        stream = WorkstreamScope(workstream_id=7, wall=frozenset(),
                                 default_upload_placement="", may_upload=False)
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True, stream=stream)
        filters = retrieval._visibility_filters(None, v)
        assert not _matches(filters, {"conversation": "conv-a"})
        # Sanity: the SAME filters DO admit an ordinary unlabelled
        # universal chunk -- proving the exclusion above is about the
        # `conversation` key, not a filter that rejects everything.
        assert _matches(filters, {})

    def test_i3_the_conversation_leg_is_exempt_from_a_locked_librarys_entitlement_gate(self):
        """ROUND 12 REVIEW I-3 (RULING): a member who holds SOME
        entitlement (so NOT `sees_nothing` -- that early-return is its
        own, separate, UNCHANGED case, pinned below) but not the one
        this library's `unlabelled_allowed=False` posture would need --
        their OWN chat-scoped attachment must still be retrievable in
        their own conversation. An ORDINARY unlabelled universal chunk
        stays excluded on the identical visibility, proving the
        exemption is scoped to the conversation leg alone."""
        v = DocumentVisibility(unrestricted=False, entitlement_ids=frozenset({42}),
                               unlabelled_allowed=False, conversation_id="conv-a")
        filters = retrieval._visibility_filters(None, v)
        assert _matches(filters, {"conversation": "conv-a"})
        assert not _matches(filters, {})
        assert not _matches(filters, {"conversation": "conv-b"})

    def test_the_conversation_leg_never_bypasses_a_category_filter(self):
        """Category is query-shaping, not a security boundary -- I-3's
        exemption is scoped to the ENTITLEMENT gate specifically, never
        to a category the caller themselves asked to narrow by."""
        v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                               unlabelled_allowed=True, conversation_id="conv-a")
        filters = retrieval._visibility_filters("medical", v)
        assert not _matches(filters, {"conversation": "conv-a", "category": "legal"})
        assert _matches(filters, {"conversation": "conv-a", "category": "medical"})


class TestTheAttachedDocumentsCorpus:
    """`tools.rag.access.attached_documents`/`readable_documents` --
    the SAME brief matrix, at the business-logic level the strip and
    the prompt actually read through."""

    def test_retrievable_in_its_own_chat_via_attached_documents(self):
        conversation_id = uuid.uuid4()
        doc = _chat_scoped(conversation_id)
        with posture("open"):
            rows = attached_documents(OPEN_PRINCIPAL, conversation_id=conversation_id)
        assert [r["id"] for r in rows] == [doc.pk]
        assert rows[0]["in_corpus"] is True

    def test_absent_from_another_conversations_attached_documents(self):
        _chat_scoped(uuid.uuid4())
        with posture("open"):
            rows = attached_documents(OPEN_PRINCIPAL, conversation_id=uuid.uuid4())
        assert rows == []

    def test_absent_from_a_streams_own_readable_documents_even_when_the_uploader_asks(self):
        """`readable_documents`' own chat-scope re-admission is
        `workstream_id is None` ONLY (round 12 review I-2's own fix,
        pinned again here from the attachment's own angle): the
        uploader's chat-scoped document must not appear in ANY specific
        stream's own listing, even though they may see it on the plain
        library read."""
        user = make_user()
        from identity.access import owner_fields
        from tools.rag.tests._helpers import user_principal
        doc = make_document(scope=Document.Scope.CONVERSATION,
                            **owner_fields(user_principal(user)))
        DocumentAttachment.objects.create(document=doc, conversation_id=uuid.uuid4())
        stream = _workstream()
        with posture("enterprise"):
            principal = user_principal(user)
            in_the_stream = readable_documents(principal, workstream_id=stream.pk)
            in_the_library = readable_documents(principal, workstream_id=None)
        assert doc not in in_the_stream
        assert doc in in_the_library


class TestTheAttachInvariantGuard:
    """`tools.rag.views._attach`'s own write-path guard (round 12
    review I-4): a conversation-scoped document carries EXACTLY ONE
    attachment row -- a SECOND conversation calling `_attach` on an
    already chat-scoped document (an edge case the round-12 review I-1
    fix's conversation-distinct path makes hard to reach through a real
    upload, but the guard is a defensive backstop, not a guarantee the
    path alone provides) must not create a second claim."""

    def test_a_second_conversation_cannot_attach_an_already_chat_scoped_document(self, caplog):
        # ROUND 13: `_attach` moved from `tools.rag.views` (retired --
        # that view no longer writes `DocumentAttachment` at all) to
        # `tools.rag.services`, the new staging path's own home. The
        # guard itself is byte-identical.
        from tools.rag.services import _attach

        first, second = uuid.uuid4(), uuid.uuid4()
        doc = _chat_scoped(first)
        with caplog.at_level("WARNING"):
            _attach(doc, second, None)
        assert DocumentAttachment.objects.filter(document=doc).count() == 1
        assert DocumentAttachment.objects.get(document=doc).conversation_id == first
        assert "conversation-scoped" in caplog.text

    def test_the_same_conversation_re_attaching_is_still_a_no_op_not_a_duplicate(self):
        from tools.rag.services import _attach

        conversation_id = uuid.uuid4()
        doc = _chat_scoped(conversation_id)
        _attach(doc, conversation_id, None)
        assert DocumentAttachment.objects.filter(document=doc).count() == 1


class TestReAttachingMovesTheChipToTheNewestTurn:
    """ROUND-13 REVIEW FIX, MINOR 4: "`_attach` keeps the FIRST turn's
    `turn_id` when the same bytes are re-sent in a later message, so
    the chip renders on an older message than the one the operator
    just attached it to -- documented as a 'minor, named
    simplification', but it is precisely the 'visually part of the
    message it was sent with' property requirement 3 asks for."
    `DocumentAttachment`'s own docstring is updated to match this
    class's own claim, not the retired one."""

    def test_a_re_upload_from_a_later_turn_moves_turn_id_forward(self):
        from tools.rag.services import _attach

        conversation_id = uuid.uuid4()
        doc = make_document()
        _attach(doc, conversation_id, 1)
        attachment = DocumentAttachment.objects.get(document=doc,
                                                     conversation_id=conversation_id)
        assert attachment.turn_id == 1

        _attach(doc, conversation_id, 2)  # the SAME bytes, a LATER turn
        attachment.refresh_from_db()
        assert attachment.turn_id == 2
        # STILL EXACTLY ONE ROW -- a moved chip, never a second claim.
        assert DocumentAttachment.objects.filter(document=doc).count() == 1

    def test_re_attaching_with_the_identical_turn_id_costs_no_write(self):
        """The `!= turn_id` guard's own non-vacuous pin: re-attaching
        with the SAME `turn_id` the row already carries (the ordinary
        "created just now" path, `defaults=` having just set it) must
        not perform a needless second `UPDATE`."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from tools.rag.services import _attach

        conversation_id = uuid.uuid4()
        doc = make_document()
        _attach(doc, conversation_id, 1)
        with CaptureQueriesContext(connection) as ctx:
            _attach(doc, conversation_id, 1)  # identical turn_id, again
        update_queries = [q for q in ctx.captured_queries if "UPDATE" in q["sql"]]
        assert update_queries == []

    def test_a_legacy_none_turn_id_re_attach_stays_none_not_overwritten_by_none(self):
        """`_attach(doc, conversation_id, None)` twice (`TestTheAttach
        InvariantGuard`'s own existing pin, just above) must keep
        costing no write either -- `None != None` is `False`, so the
        guard's own condition already covers this, pinned here
        explicitly rather than left to be merely implied by the OTHER
        test's own passing."""
        from tools.rag.services import _attach

        conversation_id = uuid.uuid4()
        doc = make_document()
        _attach(doc, conversation_id, None)
        _attach(doc, conversation_id, None)
        attachment = DocumentAttachment.objects.get(document=doc,
                                                     conversation_id=conversation_id)
        assert attachment.turn_id is None


class TestContentVersusLinkAsymmetry:
    """Round 12 review minor 4: a `use`-level conversation share
    recipient's OWN turn retrieves the owner's chat-scoped document and
    gets its CONTENT (`attached_documents`' chat-scope branch is
    unconditional on principal identity, `in_corpus=True` for anyone who
    is not `sees_nothing`) -- while `rag-document-file` still 404s for
    them (uploader/`sees_all_content`-only). Deliberately asymmetric,
    the SAME rule the brief's own citation-link precedent anticipated
    for the LINK half; this pins that the CONTENT half really does go
    the other way, which nothing recorded before this round."""

    def test_a_use_level_recipient_sees_content_retrievable_but_the_file_route_404s(
        self, client
    ):
        from identity.access import owner_fields
        from tools.rag.tests._helpers import user_principal

        owner, recipient = make_user(), make_user()
        conversation_id = uuid.uuid4()
        doc = make_document(title="Private.pdf",
                            **owner_fields(user_principal(owner)))
        doc.scope = Document.Scope.CONVERSATION
        doc.save(update_fields=["scope", "updated_at"])
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation_id)

        from django.urls import reverse
        from tools.rag.tests._helpers import sign_in

        with posture("enterprise"):
            recipient_principal = user_principal(recipient)
            rows = attached_documents(recipient_principal, conversation_id=conversation_id)
            assert rows and rows[0]["in_corpus"] is True

            sign_in(client, recipient)
            response = client.get(reverse("rag-document-file", args=[doc.id]))
        assert response.status_code == 404


class TestChatStagingStaysOutsideTheWatchedInbox:
    """Round 12 fix verify A-1/B-4 (the reviewer's own probe): chat-
    scoped uploads used to stage under `INGEST_INBOX_DIR/[category/]
    chat-<uuid>/`, squarely inside the folder watcher's own recursive
    Observer tree (`tools.rag.ingest.watch_folder`) -- so a watcher
    race could re-ingest the file as an ordinary UNIVERSAL document
    under a junk `chat-<uuid>` category, silently discarding "This chat
    only" through the race branch. Fixed by moving staging to
    `settings.CHAT_STAGING_DIR`, a SIBLING of the inbox under
    `DATA_DIR` -- never a descendant of it."""

    def test_chat_staging_dir_is_not_nested_under_the_watched_inbox(self):
        from pathlib import Path

        from django.conf import settings

        inbox = Path(settings.INGEST_INBOX_DIR).resolve()
        chat_dir = Path(settings.CHAT_STAGING_DIR).resolve()
        assert chat_dir != inbox
        assert not chat_dir.is_relative_to(inbox)
        assert not inbox.is_relative_to(chat_dir)

    def test_a_chat_scoped_uploads_file_never_lands_inside_the_watched_inbox(self):
        # ROUND 13: staging moved from `tools.rag.views.document_upload`
        # (retired -- the chat door's own separate-POST path is gone)
        # to `tools.rag.services.stage_turn_attachments`, called from
        # `agents.chat.service.start_turn` at turn-create time. This
        # test exercises that SERVICE function directly, the same
        # "regression guard for the staging path, not the HTTP plumbing
        # around it" shape the ordinary (universal) watcher-race test
        # already used before this round -- the full HTTP round trip
        # through a real turn is covered separately, in `agents/chat/
        # tests/test_turn_attachments.py`.
        from pathlib import Path

        from django.conf import settings
        from django.core.files.uploadedfile import SimpleUploadedFile

        from identity.access import owner_fields
        from identity.testing import make_user, user_principal
        from tools.rag.services import stage_turn_attachments
        from tools.rag.tests._helpers import make_conversation

        user = make_user()
        conversation = make_conversation(**owner_fields(user_principal(user)))
        principal = user_principal(user)
        result = stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=None,
            files=[SimpleUploadedFile("chat-only.md", b"private to this chat")],
            placement="conversation", actor=principal,
        )
        assert result.ok
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.CONVERSATION
        inbox = Path(settings.INGEST_INBOX_DIR).resolve()
        assert not Path(doc.original_path).resolve().is_relative_to(inbox)

    def test_the_watcher_race_branch_never_downgrades_a_chat_scoped_documents_scope(self):
        """The exact race the review probed, pinned as a regression
        guard: even inside `stage_turn_attachments`' own
        `FileNotFoundError` ("the watcher likely won") branch, a
        chat-scoped upload's resulting document keeps
        `scope == "conversation"` -- never the silent downgrade to
        `"universal"` the bug produced."""
        from unittest.mock import patch

        from django.core.files.uploadedfile import SimpleUploadedFile

        from identity.access import owner_fields
        from identity.testing import make_user, user_principal
        from tools.rag.services import stage_turn_attachments
        from tools.rag.tests._helpers import make_conversation

        user = make_user()
        conversation = make_conversation(**owner_fields(user_principal(user)))
        principal = user_principal(user)
        # First, a REAL chat-scoped upload -- creates the row this
        # test's own patched second upload will find already staged,
        # the SAME two-step shape the ordinary (universal) watcher-race
        # test already uses.
        stage_turn_attachments(
            principal, conversation_id=conversation.id, turn_id=None,
            files=[SimpleUploadedFile("chat-only.md", b"private to this chat")],
            placement="conversation", actor=principal,
        )
        with patch("tools.rag.ingest.enqueue_ingest", side_effect=FileNotFoundError):
            stage_turn_attachments(
                principal, conversation_id=conversation.id, turn_id=None,
                files=[SimpleUploadedFile("chat-only.md", b"private to this chat")],
                placement="conversation", actor=principal,
            )
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.CONVERSATION
        assert DocumentAttachment.objects.filter(
            document=doc, conversation_id=conversation.id).exists()


class TestTheUploaderMayAdministerTheirOwnChatScopedDocument:
    """Round 12 whole-branch review A-2/B-2: `may_label_document` alone
    left a chat-scoped document's own uploader with no Delete and no
    Re-ingest at all (never labelled, so never an entitlement owner, and
    not necessarily an admin) -- undelivering the brief's own stated
    reason for showing the row ("find/delete path"). `tools.rag.access.
    may_administer_document` widens exactly Delete/Re-ingest, render AND
    gate, both directions."""

    def _chat_scoped_for(self, owner, title="Private.pdf"):
        from identity.access import owner_fields
        from tools.rag.tests._helpers import user_principal

        doc = make_document(title=title, **owner_fields(user_principal(owner)))
        doc.scope = Document.Scope.CONVERSATION
        doc.save(update_fields=["scope", "updated_at"])
        return doc

    def test_the_uploader_sees_delete_and_reingest_buttons(self, client):
        from django.urls import reverse
        from tools.rag.tests._helpers import posture, sign_in

        owner = make_user()
        doc = self._chat_scoped_for(owner)
        with posture("enterprise"):
            sign_in(client, owner)
            body = client.get(reverse("rag-documents")).content.decode()
        assert f'action="{reverse("rag-document-delete", args=[doc.id])}"' in body
        assert f'action="{reverse("rag-document-reingest", args=[doc.id])}"' in body

    def test_a_second_non_admin_user_sees_neither_button(self, client):
        from django.urls import reverse
        from tools.rag.tests._helpers import posture, sign_in

        owner, stranger = make_user(), make_user()
        doc = self._chat_scoped_for(owner)
        # `sees_all_content` off, no admin, no entitlement -- but a
        # sees_all_content admin WOULD see the row (round 12 review
        # I-2's own rule for listable_documents); this stranger is
        # deliberately neither, so the row itself is invisible to them
        # and neither button can render at all.
        with posture("enterprise"):
            sign_in(client, stranger)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Private.pdf" not in body
        assert f'action="{reverse("rag-document-delete", args=[doc.id])}"' not in body

    def test_the_uploader_may_delete_their_own_chat_scoped_document(self, client):
        from django.urls import reverse
        from tools.rag.tests._helpers import posture, sign_in

        owner = make_user()
        doc = self._chat_scoped_for(owner)
        with posture("enterprise"):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-delete", args=[doc.id]))
        assert response.status_code == 302
        assert not Document.objects.filter(pk=doc.pk).exists()

    def test_a_second_non_admin_user_cannot_delete_it(self, client):
        from django.urls import reverse
        from tools.rag.tests._helpers import posture, sign_in

        owner, stranger = make_user(), make_user()
        doc = self._chat_scoped_for(owner)
        with posture("enterprise"):
            sign_in(client, stranger)
            response = client.post(reverse("rag-document-delete", args=[doc.id]))
        assert response.status_code in (403, 404)
        assert Document.objects.filter(pk=doc.pk).exists()

    def test_the_uploader_may_reingest_their_own_chat_scoped_document(self, client):
        from django.urls import reverse
        from tools.rag.tests._helpers import posture, sign_in

        owner = make_user()
        doc = self._chat_scoped_for(owner)
        doc.status = Document.Status.FAILED
        doc.save(update_fields=["status", "updated_at"])
        with posture("enterprise"):
            sign_in(client, owner)
            response = client.post(reverse("rag-document-reingest", args=[doc.id]))
        # 302 (never 403) is the whole proof here -- `document_reingest`
        # redirects either way (success or a caught enqueue failure),
        # so the status code alone is what distinguishes "the gate let
        # me try" from "the gate refused me outright".
        assert response.status_code == 302

    def test_a_second_non_admin_user_cannot_reingest_it(self, client):
        from django.urls import reverse
        from tools.rag.tests._helpers import posture, sign_in

        owner, stranger = make_user(), make_user()
        doc = self._chat_scoped_for(owner)
        doc.status = Document.Status.FAILED
        doc.save(update_fields=["status", "updated_at"])
        with posture("enterprise"):
            sign_in(client, stranger)
            response = client.post(reverse("rag-document-reingest", args=[doc.id]))
        assert response.status_code in (403, 404)
