"""Round 13 (message-bound attachments; owner feedback: "we shouldn't
process unless the message is actually submitted with the file. also I
need the ability to remove an attached file. ... After submitting the
document wasn't attached to that message (at least not visually) ...
i also asked about it and it referenced it, but it says its still
processing").

FOUR REQUIREMENTS, FOUR CLASSES BELOW (the brief's own numbering):
1. `TestStagingIsMessageBound` -- nothing uploads/ingests until a turn
   actually submits the files.
2. `TestOnePostFlow` -- the turn-create POST itself stages/creates the
   documents, scope matrix included, plus the JS-off equivalence.
3. `TestChipsOnTheTurnBubble` -- chips render on the carrying turn's
   own card, both on a full reload and through the poller's fragment.
4. `TestDetach` -- the post-send remove matrix (uploader unlink vs.
   delete by scope, stranger, gate parity, never-500).

`tools/rag/tests/test_chat_scoped_documents.py` and `tools/rag/tests/
test_services.py`-shaped coverage stay THAT column's job (the staging
service's own per-file loop, watcher-race, scope semantics); this file
is the AGENTS-SIDE integration -- the turn-create POST, the rendered
page, the detach route -- none of which `tools/rag`'s own tests can
reach (`agents/` may not import `tools/`, so the reverse crossing
belongs here).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from agents.chat.tests._helpers import (   # noqa: F401 -- the import IS the registration
    bound_chat_role, fake_queue_down, fake_turn_queue, make_agent, make_conversation, make_user,
    posture, sign_in, user_principal,
)
from agents.models import Share, Turn
from agents.runtime.tests._helpers import isolated_tool_registry  # noqa: F401
from agents.tests._helpers import _workstream, make_document
from agents.visibility import create_conversation
from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from tools.rag.models import Document, DocumentAttachment

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _post(client, conversation, *, text="hello", files=None, placement="conversation",
         workstream=None, remember_placement=False, xhr=True, **extra):
    data = {"text": text, "placement": placement}
    if files is not None:
        data["files"] = files
    if workstream is not None:
        data["workstream"] = str(workstream.pk)
    if remember_placement:
        data["remember_placement"] = "1"
    data.update(extra)
    kwargs = XHR if xhr else {}
    return client.post(reverse("chat-turn", args=[conversation.id]), data, **kwargs)


class TestStagingIsMessageBound:
    """Requirement 1: choosing files stages them in the browser only --
    server-side, there is no endpoint that turns a bare file selection
    into a Document/DocumentAttachment without a turn. This is a
    NEGATIVE claim about the SURFACE (no such route exists), pinned two
    ways: the retired chat-door fields on the library's own upload
    route are inert, and NOTHING reaches `tools.rag.services.stage_
    turn_attachments` except through `chat-turn`."""

    def test_the_librarys_own_upload_route_ignores_a_conversation_field(self, client):
        """The retired path, honestly dead: `rag-document-upload` still
        exists (the library/workstream doors still use it), but a
        `conversation` field on it creates no attachment any more --
        `tools/rag/views.py::document_upload`'s own docstring."""
        user = make_user()
        conversation = make_conversation()
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, user)
            client.post(reverse("rag-document-upload"), {
                "files": SimpleUploadedFile("sneaky.md", b"hi"),
                "conversation": str(conversation.id),
                "placement": "universal",
            })
        assert DocumentAttachment.objects.count() == 0

    def test_a_get_to_the_conversation_page_creates_nothing(self, client):
        conversation = make_conversation()
        client.get(reverse("chat-conversation", args=[conversation.id]))
        assert Document.objects.count() == 0
        assert Turn.objects.count() == 0


class TestOnePostFlow:
    """Requirement B: the turn-create POST itself creates the user
    turn, stages the file(s), binds each attachment to BOTH the
    conversation and that turn, and enqueues ingestion -- one request,
    scope semantics exactly as round 12."""

    def test_a_message_with_a_file_creates_one_turn_one_document_one_attachment(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        conversation = make_conversation()
        response = _post(client, conversation, text="here's the report",
                         files=SimpleUploadedFile("report.md", b"quarterly numbers"))
        assert response.status_code == 202
        user_turn = Turn.objects.get(role=Turn.Role.USER)
        assert user_turn.text == "here's the report"
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.CONVERSATION
        attachment = DocumentAttachment.objects.get()
        assert attachment.document_id == doc.id
        assert attachment.conversation_id == conversation.id
        assert attachment.turn_id == user_turn.pk

    def test_a_message_with_no_files_behaves_exactly_as_before(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """THE REGRESSION GUARD: a plain text message, no `files` field
        at all, must not require a `placement` or otherwise change
        shape -- most messages carry no attachment."""
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]), {"text": "just text"}, **XHR,
        )
        assert response.status_code == 202
        assert Document.objects.count() == 0
        assert DocumentAttachment.objects.count() == 0

    def test_placement_universal_produces_an_ordinary_universal_document(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        conversation = make_conversation()
        response = _post(client, conversation, files=SimpleUploadedFile("note.md", b"x"),
                         placement="universal")
        assert response.status_code == 202
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.UNIVERSAL
        assert doc.workstream_id is None

    def test_placement_contained_requires_a_workstream_the_principal_may_upload_to(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        owner = make_user()
        stream = _workstream(principal=user_principal(owner))
        conversation = make_conversation(workstream=stream)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = _post(client, conversation, files=SimpleUploadedFile("plan.md", b"x"),
                             placement="contained", workstream=stream)
        assert response.status_code == 202
        doc = Document.objects.get()
        assert doc.scope == Document.Scope.UNIVERSAL
        assert doc.workstream_id == stream.pk

    def test_an_unrecognised_placement_refuses_the_whole_turn_400(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """Author decision 17's own rule, unchanged by the move: a
        malformed `placement` is refused BY NAME, never a silent
        fallback -- and refusing the WHOLE turn (not merely dropping
        the file) means no half-sent message with a silently-missing
        attachment either."""
        conversation = make_conversation()
        response = _post(client, conversation, text="hi",
                         files=SimpleUploadedFile("x.md", b"x"), placement="not-a-real-value")
        assert response.status_code == 400
        assert Turn.objects.count() == 0
        assert Document.objects.count() == 0

    def test_a_principal_without_rag_ingest_cannot_attach_files_at_all(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """Render-vs-gate, the POST half: even a hand-crafted request
        carrying files is refused wholesale for a principal the render
        side would never have shown the door to."""
        from agents.models import ToolEntitlement
        from identity.testing import make_entitlement

        owner = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        ToolEntitlement.objects.create(tool_key="rag.ingest",
                                       entitlement=make_entitlement(name="Legal"))
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = _post(client, conversation, files=SimpleUploadedFile("x.md", b"x"))
        assert response.status_code == 403
        assert Turn.objects.count() == 0

    def test_the_no_js_path_behaves_identically_to_the_xhr_one(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """JS-OFF EQUIVALENCE: the SAME plain multipart POST, no
        `X-Requested-With` header -- a redirect instead of a 202 JSON
        body, but the identical turn/document/attachment rows."""
        conversation = make_conversation()
        response = _post(client, conversation, text="plain post",
                         files=SimpleUploadedFile("plain.md", b"y"), xhr=False)
        assert response.status_code == 302
        user_turn = Turn.objects.get(role=Turn.Role.USER)
        doc = Document.objects.get()
        attachment = DocumentAttachment.objects.get()
        assert attachment.document_id == doc.id
        assert attachment.turn_id == user_turn.pk
        assert doc.scope == Document.Scope.CONVERSATION


class TestRollbackLeavesNoOrphanFile:
    """Round-13 REVIEW FIX, I-2: "Every rollback of a carrying turn
    leaks the uploaded bytes into the managed store, permanently."
    `agents.chat.service.start_turn`'s own `transaction.atomic()` rolls
    back every DB row a later step's failure touches, but `tools.rag.
    store.move_file` (reached through `stage_turn_attachments`, ABOVE
    the failing step) is a filesystem move no savepoint can undo -- the
    reviewer's own queue-outage probe, reproduced here: `Document.
    objects.count() == 0` AND the SPECIFIC managed-store directory this
    request's own staging created is gone afterward.

    THE SPECIFIC DIRECTORY, NOT A WHOLE-TREE SNAPSHOT DIFF: `settings.
    DOCUMENTS_DIR` is the box's own real, persistent data directory
    (ADR 0009), never an isolated per-test tmp path -- on a shared
    devbox where more than one test run can be exercising this SAME
    directory tree at once (a genuine, documented hazard on this
    project -- other sessions' OWN attachment tests create and remove
    their own document directories throughout), a "list the whole tree
    before and after" diff can catch an unrelated directory that
    appeared or vanished in the same window and misreport it as THIS
    request's own leak (or non-leak). Spying on `agents.chat.service.
    cleanup_staged_documents` (patched at the point `agents.chat.
    service` itself imports it) captures the EXACT id this request's
    own staging produced, with zero risk of ever being confused by
    another id.
    """

    def _spy_on_cleanup(self, monkeypatch):
        import agents.chat.service as service_module

        calls = []
        real = service_module.cleanup_staged_documents

        def _spy(document_ids):
            calls.append(tuple(document_ids))
            return real(document_ids)

        monkeypatch.setattr(service_module, "cleanup_staged_documents", _spy)
        return calls

    def test_a_queue_outage_after_staging_leaves_no_document_row_and_no_stray_file(
        self, client, bound_chat_role, fake_queue_down, monkeypatch,
    ):
        from django.conf import settings

        calls = self._spy_on_cleanup(monkeypatch)
        conversation = make_conversation()
        response = _post(client, conversation, text="here's the report",
                         files=SimpleUploadedFile("report.md", b"quarterly numbers"))
        assert response.status_code == 503
        # The user's own turn is rolled back too -- a message whose
        # files could not ultimately be queued must not sit in the
        # thread as if it had sent (the SAME claim `_AttachmentsRefused`
        # `s own docstring makes for the OTHER refusal path).
        assert Turn.objects.count() == 0
        assert Document.objects.count() == 0
        assert DocumentAttachment.objects.count() == 0
        # THE PROBE ITSELF: the DB row being gone is necessary but not
        # sufficient -- the bug this pins is specifically that the
        # BYTES survive the row. Non-vacuous (`assert calls == [(...)]`
        # with a real id inside, never `calls == [()]`): staging really
        # did move something THIS request is responsible for cleaning
        # up, not merely "the cleanup hook fired with nothing to do".
        assert len(calls) == 1 and len(calls[0]) == 1
        staged_id = calls[0][0]
        assert not (Path(settings.DOCUMENTS_DIR) / str(staged_id)).exists()

    def test_a_generic_enqueue_failure_leaves_no_stray_file_either(
        self, client, bound_chat_role, monkeypatch,
    ):
        """The SAME leak, the OTHER exception branch (`start_turn`'s
        broad `except Exception` -- an unexpected failure, not
        specifically a `QueueUnavailable`) -- both branches call the
        identical `_cleanup_any_staged_documents`, so both need their
        own pin rather than trusting one to stand in for the other."""
        from django.conf import settings

        from agents.chat.tests._helpers import _patch_queue

        _patch_queue(monkeypatch, raises=RuntimeError("unexpected"))
        calls = self._spy_on_cleanup(monkeypatch)
        conversation = make_conversation()
        response = _post(client, conversation, text="here's the report",
                         files=SimpleUploadedFile("plan.md", b"quarterly numbers"))
        assert response.status_code == 503
        assert Document.objects.count() == 0
        assert len(calls) == 1 and len(calls[0]) == 1
        staged_id = calls[0][0]
        assert not (Path(settings.DOCUMENTS_DIR) / str(staged_id)).exists()

    def test_an_ordinary_successful_turn_is_unaffected_by_the_cleanup_call(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """NON-REGRESSION: `_cleanup_any_staged_documents` is wired only
        into the FAILURE branches -- an ordinary, successful staging
        keeps its Document row and its managed-store file exactly as
        every other test in this file already proves; this test exists
        so a future edit that accidentally called the cleanup on the
        SUCCESS path too would be caught immediately rather than by a
        vague, unrelated failure elsewhere."""
        conversation = make_conversation()
        response = _post(client, conversation, text="here's the report",
                         files=SimpleUploadedFile("keeper.md", b"quarterly numbers"))
        assert response.status_code == 202
        doc = Document.objects.get()
        assert Path(doc.source_path).exists()


class TestChatPathCaps:
    """ROUND-13 REVIEW FIX, I-4's own second finding: "the new staging
    path's own caps (size/extension/duration/page) have no test on the
    chat path at all -- they travelled in code, not in coverage." The
    caps THEMSELVES are `stage_document`'s own (`tools/rag/tests/
    test_ingest.py`) and the library door's own `tools/rag/tests/
    test_views_upload_and_settings.py::TestDocumentUpload` already proves
    them enforced THERE -- this class proves the SAME four caps fire
    through the CHAT door specifically (`agents.chat.service.start_turn` ->
    `stage_turn_attachments`), which is a genuinely different call path
    (`tools.rag.views.document_upload`'s own chat branch is RETIRED,
    per this file's own `TestStagingIsMessageBound`) that could
    silently stop enforcing any one of them without a single existing
    test noticing.
    """

    def test_an_unsupported_extension_is_rejected_not_staged(
            self, client, bound_chat_role, fake_turn_queue):
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("evil.exe", b"MZ"))
        assert response.status_code == 202
        assert Document.objects.count() == 0
        assert DocumentAttachment.objects.count() == 0

    def test_an_oversized_file_is_rejected_before_a_document_row_exists(
            self, client, bound_chat_role, fake_turn_queue):
        from tools.rag.models import RagSettings

        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("huge.txt", b"x" * 100))
        assert response.status_code == 202
        assert Document.objects.count() == 0
        assert DocumentAttachment.objects.count() == 0

    def test_an_over_duration_media_file_is_rejected_with_no_stray_file(
            self, client, bound_chat_role, fake_turn_queue, monkeypatch, settings):
        """The chat door's own twin of `tools/rag/tests/
        test_views_upload_and_settings.py::TestDocumentUpload::
        test_over_duration_cap_surfaces_its_message_verbatim_like_the_
        size_branch` -- `MediaDurationExceededError`
        caught BY NAME (`stage_turn_attachments`'s own `except ingest.
        MediaDurationExceededError` branch), never falling through to
        the generic per-file `except Exception`, and the staged temp
        copy is unlinked rather than left behind either way."""
        from tools.rag import ingest as rag_ingest

        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        monkeypatch.setattr(
            "tools.rag.services.ingest.enqueue_ingest",
            lambda *a, **k: (_ for _ in ()).throw(
                rag_ingest.MediaDurationExceededError("clip.mp4 is over the duration cap")),
        )
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("clip.mp4", b"fake video bytes"))
        assert response.status_code == 202
        assert Document.objects.count() == 0
        stray = list(Path(settings.CHAT_STAGING_DIR).rglob("clip.mp4"))
        assert stray == [], f"orphaned staging file(s): {stray}"

    def test_an_over_page_document_is_rejected_with_no_stray_file(
            self, client, bound_chat_role, fake_turn_queue, monkeypatch, settings):
        """The chat door's own twin of `TestDocumentUpload::test_over_
        page_cap_surfaces_its_message_verbatim_like_the_duration_
        branch` -- `DocumentPageCapExceededError` caught by name, the
        identical shape one exception type over."""
        from tools.rag import ingest as rag_ingest

        monkeypatch.setattr(
            "tools.rag.services.ingest.enqueue_ingest",
            lambda *a, **k: (_ for _ in ()).throw(
                rag_ingest.DocumentPageCapExceededError("scan.pdf is over the page cap")),
        )
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("scan.pdf", b"fake pdf bytes"))
        assert response.status_code == 202
        assert Document.objects.count() == 0
        stray = list(Path(settings.CHAT_STAGING_DIR).rglob("scan.pdf"))
        assert stray == [], f"orphaned staging file(s): {stray}"


class TestChipsOnTheTurnBubble:
    """Requirement 3: an attachment is visually part of the message it
    was sent with, on both the full-page render AND the poller's own
    swapped fragment (requirement 4's live-status half)."""

    def test_the_users_own_turn_card_carries_the_chip(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        conversation = make_conversation()
        _post(client, conversation, text="see attached",
             files=SimpleUploadedFile("agenda.md", b"z"))
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])
        ).content.decode()
        assert "see attached" in body
        assert "agenda.md" in body
        assert '<div class="turn-attachments">' in body

    def test_a_ready_chip_never_regresses_to_processing_on_the_poller_fragment(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """The owner's exact complaint: "it says its still processing"
        after the file was already done. `turn_status`'s own body reads
        the document's CURRENT status fresh on every call -- flipping it
        to READY between two polls must show READY on the next one,
        never a value cached from the first."""
        conversation = make_conversation()
        response = _post(client, conversation, files=SimpleUploadedFile("x.md", b"x"))
        turn_id = response.json()["turn_id"]
        doc = Document.objects.get()
        doc.status = Document.Status.PROCESSING
        doc.save(update_fields=["status", "updated_at"])
        first = client.get(reverse("chat-turn-status", args=[turn_id]))
        assert "Processing" in first.json()["html"]

        doc.status = Document.Status.READY
        doc.save(update_fields=["status", "updated_at"])
        second = client.get(reverse("chat-turn-status", args=[turn_id]))
        assert "Ready" in second.json()["html"]
        assert "Processing" not in second.json()["html"]

    def test_a_still_processing_attachment_keeps_the_poll_going_past_done(
        self, client, bound_chat_role, fake_turn_queue,
    ):
        """`_done_body`'s own `attachments_pending` key -- the poller's
        own signal to keep ticking a while after the ANSWER itself is
        done, so a slow-to-ingest file's chip is not stuck stale."""
        conversation = make_conversation()
        response = _post(client, conversation, files=SimpleUploadedFile("x.md", b"x"))
        turn_id = response.json()["turn_id"]
        Turn.objects.filter(pk=turn_id).update(state=Turn.State.DONE)
        doc = Document.objects.get()
        doc.status = Document.Status.PROCESSING
        doc.save(update_fields=["status", "updated_at"])
        body = client.get(reverse("chat-turn-status", args=[turn_id])).json()
        assert body["state"] == "done"
        assert body["attachments_pending"] is True

        doc.status = Document.Status.READY
        doc.save(update_fields=["status", "updated_at"])
        body = client.get(reverse("chat-turn-status", args=[turn_id])).json()
        assert body["attachments_pending"] is False


class TestDetach:
    """Requirement E's post-send remove: uploader-only, matrix-classed
    by scope, never-500."""

    def _attached(self, *, owner, conversation, scope=Document.Scope.CONVERSATION, turn=None):
        doc = make_document(title="Mine.pdf", scope=scope, **owner_fields(user_principal(owner)))
        if turn is None:
            turn = Turn.objects.create(
                conversation=conversation, index=Turn.next_index(conversation.id),
                role=Turn.Role.USER, text="attached", state=Turn.State.DONE,
            )
        DocumentAttachment.objects.create(document=doc, conversation_id=conversation.id,
                                          turn_id=turn.pk)
        return doc

    def test_the_uploader_may_unlink_their_own_universal_attachment(self, client):
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        doc = self._attached(owner=owner, conversation=conversation,
                             scope=Document.Scope.UNIVERSAL)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
        assert response.status_code == 302
        assert not DocumentAttachment.objects.filter(document=doc).exists()
        assert Document.objects.filter(pk=doc.id).exists()   # unlink only -- doc survives

    def test_the_uploader_removing_a_chat_scoped_attachment_deletes_the_document(self, client):
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        doc = self._attached(owner=owner, conversation=conversation,
                             scope=Document.Scope.CONVERSATION)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
        assert response.status_code == 302
        assert not Document.objects.filter(pk=doc.id).exists()

    def test_a_total_stranger_gets_404_and_nothing_is_removed(self, client):
        """A stranger with NO relationship to this conversation cannot
        even SEE it (`visible_conversations`, the first gate) -- 404,
        the same "cannot tell apart from nonexistent" answer every
        other row-addressed chat route gives an outsider."""
        owner, stranger = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        doc = self._attached(owner=owner, conversation=conversation)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, stranger)
            response = client.post(
                reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
        assert response.status_code == 404
        assert Document.objects.filter(pk=doc.id).exists()
        assert DocumentAttachment.objects.filter(document=doc).exists()

    def test_a_nonexistent_doc_id_is_404_never_500(self, client):
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-attachment-detach", args=[conversation.id, 999999]))
        assert response.status_code == 404

    def test_a_real_doc_not_attached_to_this_conversation_is_404(self, client):
        owner = make_user()
        agent = make_agent()
        conversation = create_conversation(user_principal(owner), agent)
        other_conversation = create_conversation(user_principal(owner), agent)
        doc = self._attached(owner=owner, conversation=other_conversation)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.post(
                reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
        assert response.status_code == 404

    def test_the_render_side_offers_no_remove_control_to_a_stranger(self, client):
        """RENDER-VS-GATE: a chip's own ✕ form must not even be offered
        to a principal the GATE would refuse -- never a control that
        renders and then 403s on click."""
        owner, stranger = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        self._attached(owner=owner, conversation=conversation)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=stranger,
                             level=Share.Level.VIEW)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, stranger)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert "Mine.pdf" in body
        assert "chat-attachment-detach" not in body

    def test_the_render_side_offers_the_control_to_the_uploader(self, client):
        owner = make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        doc = self._attached(owner=owner, conversation=conversation)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(
                reverse("chat-conversation", args=[conversation.pk])
            ).content.decode()
        assert reverse("chat-attachment-detach", args=[conversation.pk, doc.id]) in body

    def test_a_view_level_share_recipient_who_is_also_the_uploader_still_needs_post_access(
        self, client,
    ):
        """`may_post_to` gates the ROUTE before `may_detach_attachment`
        ever runs -- the SAME rule `turn_create` itself enforces, so a
        downgraded-to-view-only uploader cannot mutate a conversation
        they may only read, even for their own file."""
        owner, uploader = make_user(), make_user()
        conversation = create_conversation(user_principal(owner), make_agent())
        doc = self._attached(owner=uploader, conversation=conversation)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=uploader,
                             level=Share.Level.VIEW)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, uploader)
            response = client.post(
                reverse("chat-attachment-detach", args=[conversation.id, doc.id]))
        assert response.status_code == 403


class TestTheStagedFilesListClearsAfterSend:
    """ROUND-13 REVIEW FIX, MINOR 1: "After a successful JS submit,
    `form.reset()` empties the file input but nothing re-renders `.
    staged-files`, so the disclosure keeps listing files that are no
    longer staged and will NOT be sent with the next message. ... a UI
    lie about attachment state in the round whose purpose is honest
    attachment state." No headless browser here (this session's own
    convention, `TestEnterToSendOnTheStartBox` in `test_index.py`'s own
    docstring) -- pinned as a SOURCE-TEXT presence check the same shape
    that class already uses for its own JS handler, proving the fix's
    two halves are both still there: the reset call itself, and that
    the staged accumulator is cleared from the exact branch (a
    successful send) that used to leave the list stale.

    ROUND 18 UPDATES THE MECHANISM, NOT THE CLAIM: a synthetic `change`
    event was enough when the staging script's own `renderStagedFiles`
    read straight off `fileInput.files`; round 18's own accumulate
    semantics (`chat/_attach_dragdrop.html`) keep a SEPARATE, closure-
    private `stagedFiles` array a bare reset/change event cannot touch,
    so the page now calls `form._clearStagedAttachments()` -- the one
    hook that script hangs off the form for exactly this -- instead."""

    def test_the_page_clears_staged_attachments_after_reset(self, client):
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "form.reset();" in body
        assert "form._clearStagedAttachments" in body
        # NON-VACUOUS ORDERING: the clear follows the reset, on the
        # SUCCESSFUL-send branch specifically (inside the `if (card)`
        # block, not merely somewhere else on the page).
        reset_at = body.index("form.reset();")
        clear_at = body.index("form._clearStagedAttachments", reset_at)
        assert reset_at < clear_at


class TestAttachmentProblemsReachTheJsPathImmediately:
    """ROUND-13 REVIEW FIX, MINOR 2: "on the ordinary JS path a
    rejected, oversized or failed file produces no visible feedback
    until some later full page load ... the same family of complaint
    that opened this round." `turn_create`'s own JSON body now carries
    `attachment_problems` -- the rejected/failed sentences, and ONLY
    those -- so `conversation.html`'s own submit handler can show them
    immediately (`TestTheStagedFilesListClearsAfterSend`'s sibling
    class, above, pins the SCRIPT side of this same fix by source
    text; this class pins the SERVER side by a real request)."""

    def test_an_unsupported_extension_reaches_the_json_body_as_a_problem(
            self, client, bound_chat_role, fake_turn_queue):
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("evil.exe", b"MZ"))
        assert response.status_code == 202
        body = response.json()
        assert len(body["attachment_problems"]) == 1
        assert "evil.exe" in body["attachment_problems"][0]
        assert "Skipped unsupported" in body["attachment_problems"][0]

    def test_an_oversized_file_reaches_the_json_body_as_a_problem(
            self, client, bound_chat_role, fake_turn_queue):
        from tools.rag.models import RagSettings

        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("huge.txt", b"x" * 100))
        assert response.status_code == 202
        body = response.json()
        assert len(body["attachment_problems"]) == 1
        assert "huge.txt" in body["attachment_problems"][0]

    def test_a_successfully_queued_file_is_absent_from_attachment_problems(
            self, client, bound_chat_role, fake_turn_queue, monkeypatch):
        """NON-VACUOUS OTHER DIRECTION: the POSITIVE outcome stays
        flash-only, on purpose (`turn_create`'s own comment) -- a
        successful attach must not ALSO appear in the "problems" list,
        which would misrepresent it as a refusal.

        `tools.rag.ingest.enqueue` (the underlying queue call `_enqueue_
        ingest_job` makes) mocked to a fixed job id: this test
        environment binds no real `rag.embed` role, so the REAL rag.
        ingest job enqueue fails for every file regardless of staging
        outcome (`TestChatPathCaps`'s own chat-path cap tests never hit
        this branch, since a cap rejection happens before ANY enqueue
        is attempted) -- `stage_document` itself still runs for real
        (creating the Document row and moving the file), only the
        FINAL "hand it to the queue" step is faked to succeed, without
        which this test would exercise the "failed" bucket for a
        reason that has nothing to do with the claim under test."""
        monkeypatch.setattr("tools.rag.ingest.enqueue", lambda *a, **k: 1)
        response = _post(client, make_conversation(), text="here",
                         files=SimpleUploadedFile("report.md", b"quarterly numbers"))
        assert response.status_code == 202
        assert response.json()["attachment_problems"] == []

    def test_a_text_only_turn_carries_an_empty_problems_list_not_a_missing_key(
            self, client, bound_chat_role, fake_turn_queue):
        response = client.post(
            reverse("chat-turn", args=[make_conversation().id]), {"text": "just text"}, **XHR,
        )
        assert response.status_code == 202
        assert response.json()["attachment_problems"] == []

    def test_the_page_renders_attachment_problems_into_turn_errors(self, client):
        """The SCRIPT half, pinned by source text the same shape
        `TestTheStagedFilesListClearsAfterSend` above already uses."""
        conversation = make_conversation()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert "result.data.attachment_problems" in body
        assert "turnErrors.textContent = result.data.attachment_problems.join" in body


class TestAPastedImage:
    """Chat image artifacts (2026-09-16). The browser names a pasted
    image `pasted-image-<YYYYMMDD-HHMMSS>[-<n>].<ext>` and puts it on the
    SAME `files` field a chosen file rides; the server has no idea a
    paste happened and must not need one. This is the proof that nothing
    server-side special-cases the name.

    SETS `FARABUNKER_FEATURES` ITSELF (the matrix runs `vision,media` and
    `vision`): image extensions only reach `stage_document` while
    `"media"` is on, and `"vision"` stays in the set because this test
    goes through the HTTP client (the vision-flag rule)."""

    def test_a_pasted_png_stages_and_attaches_like_any_image(
            self, client, bound_chat_role, fake_turn_queue, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        conversation = make_conversation()
        name = "pasted-image-20260916-101500.png"
        response = _post(client, conversation, text="what is this?",
                         files=SimpleUploadedFile(name, b"\x89PNG\r\n\x1a\nbody"))
        assert response.status_code == 202
        doc = Document.objects.get()
        assert doc.title == name
        assert doc.media_type == "image/png"
        assert doc.scope == Document.Scope.CONVERSATION
        attachment = DocumentAttachment.objects.get()
        assert attachment.document_id == doc.id
        user_turn = Turn.objects.get(role=Turn.Role.USER)
        assert attachment.turn_id == user_turn.pk

    def test_a_second_pasted_image_in_one_message_gets_its_own_row(
            self, client, bound_chat_role, fake_turn_queue, settings):
        """The `-<n>` suffix exists so two images pasted in one second do
        not collide on name+size in the browser's own accumulator; the
        server simply sees two files."""
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        conversation = make_conversation()
        response = client.post(
            reverse("chat-turn", args=[conversation.id]),
            {"text": "these two", "placement": "conversation",
             "files": [
                 SimpleUploadedFile("pasted-image-20260916-101500.png",
                                    b"\x89PNG\r\n\x1a\nfirst"),
                 SimpleUploadedFile("pasted-image-20260916-101500-2.png",
                                    b"\x89PNG\r\n\x1a\nsecond"),
             ]},
            **XHR,
        )
        assert response.status_code == 202
        assert Document.objects.count() == 2
        assert DocumentAttachment.objects.count() == 2

    def test_a_pasted_image_is_refused_honestly_with_the_media_feature_off(
            self, client, bound_chat_role, fake_turn_queue, settings):
        """Unchanged server behaviour: the extension allow-list is what
        refuses it, exactly as it refuses a CHOSEN .png, and the existing
        inline refusal is what the operator sees."""
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        response = _post(client, make_conversation(), text="what is this?",
                         files=SimpleUploadedFile("pasted-image-20260916-101500.png",
                                                  b"\x89PNG\r\n\x1a\nbody"))
        assert response.status_code == 202
        assert Document.objects.count() == 0
        assert DocumentAttachment.objects.count() == 0


class TestTheImageThumbnailOnTheChip:
    """Chat image artifacts (2026-09-16). An attached image shows itself,
    inside its own chip, served by the existing `rag-document-file` view
    — the one route that already gates these bytes with `readable_
    document`, so the thumbnail can never show what the chip's own title
    link could not.

    THE CHIP IS THE HONEST PLACE FOR THIS, not `agents.chat.rendering`: a
    `document:<id>` reference in a TOOL RESULT carries no media type, so
    rendering cannot tell an image document from a prose one by reference
    alone. The chip has the row.

    `media_type` is flipped on the row rather than posting a real PNG:
    this test is about the TEMPLATE, and staging a real image would make
    it depend on `"media"` being in `FARABUNKER_FEATURES` for no coverage
    it does not already have (`TestAPastedImage` owns that path)."""

    def _conversation_with(self, client, *, media_type):
        conversation = make_conversation()
        _post(client, conversation, text="see attached",
              files=SimpleUploadedFile("agenda.md", b"z"))
        Document.objects.update(media_type=media_type)
        return conversation

    def test_an_image_row_renders_a_thumbnail_inside_its_chip(
            self, client, bound_chat_role, fake_turn_queue):
        """Final fix wave, Fix 1: the document file view marks its
        response `Cache-Control: private, no-store`, so the browser
        cannot cache this `<img>` — and the poller re-swaps the card,
        chip included, on every tick while ingest runs. `loading="lazy"
        decoding="async"` is pinned on the SAME element string as the
        `src` it already asserts, so an edit that keeps the `src` right
        but drops the two attributes still fails here."""
        conversation = self._conversation_with(client, media_type="image/png")
        doc = Document.objects.get()
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert '<span class="turn-attachment-thumb">' in body
        assert (f'src="{reverse("rag-document-file", args=[doc.pk])}"'
                ' alt="agenda.md"\n         loading="lazy" decoding="async">') in body

    def test_the_thumbnail_carries_the_title_as_its_alt_text(
            self, client, bound_chat_role, fake_turn_queue):
        conversation = self._conversation_with(client, media_type="image/png")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert 'alt="agenda.md"' in body

    def test_a_prose_row_renders_no_thumbnail(
            self, client, bound_chat_role, fake_turn_queue):
        """ASSERTS THE ELEMENT, NOT THE CLASS NAME (review round 1,
        blocker 3): `.turn-attachment-thumb`'s CSS lives in
        `chat/base.html`'s inline `<style>`, which this page renders on
        EVERY response — so the bare string is always in the body and a
        `"turn-attachment-thumb" not in body` assertion could only ever
        fail for the wrong reason or pass for none."""
        conversation = self._conversation_with(client, media_type="text/markdown")
        body = client.get(
            reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert '<span class="turn-attachment-thumb">' not in body

    def test_the_thumbnail_survives_a_poller_re_render(
            self, client, bound_chat_role, fake_turn_queue):
        """The chip is re-rendered on every tick — a thumbnail that only
        appeared on a full reload would vanish the moment the poller
        swapped the card."""
        conversation = make_conversation()
        response = _post(client, conversation, text="see attached",
                         files=SimpleUploadedFile("agenda.md", b"z"))
        Document.objects.update(media_type="image/png")
        html = client.get(
            reverse("chat-turn-status", args=[response.json()["turn_id"]])).json()["html"]
        assert '<span class="turn-attachment-thumb">' in html

    def test_the_thumbnail_rules_live_in_the_chat_base_template(self, client):
        """CSS OWNERSHIP (`foundation/ops/tests/test_css_ownership.py`):
        a fragment never carries its own `<style>`, and its rules belong
        in the nearest common ancestor of every page that could render
        it — `chat/base.html`, where `.turn-attachment-row` already is."""
        chip = Path("agents/chat/templates/chat/_attachment_chip.html").read_text()
        base = Path("agents/chat/templates/chat/base.html").read_text()
        assert "<style" not in chip
        assert ".turn-attachment-thumb" in base

    def test_an_unreadable_image_row_renders_the_link_but_no_thumbnail(
            self, client, bound_chat_role, fake_turn_queue):
        """PACKET §7, second informational note. For a non-uploader's
        chat-scoped image the `rag-document-file` route 404s, so an
        unconditional `<img>` gives that reader a BROKEN-IMAGE GLYPH --
        on the conversation page and on `/chat/all/`'s preview pane --
        where main showed only a dead link. Gating on the SAME `readable`
        key the caption is gated on gives them the dead link back and
        nothing worse; the chip's own comment already accepts that the
        thumbnail shows nothing the title link could not.

        `posture(POSTURE_ENTERPRISE)` + `sign_in`, NOT the default open
        posture -- the same trap `agents/chat/tests/test_composer.py:
        86-92` documents for the attach-door gate. On an open box
        `sees_all_content` is True for its sole principal, so
        `readable_documents` admits EVERY row regardless of who uploaded
        it, `readable` comes back True, and this test would assert the
        absence of a thumbnail that is correctly present -- failing for
        a reason its own name denies. A second, signed-in principal on a
        posture that actually has principals is the only way to have a
        non-uploader at all.

        A `Share` (VIEW level) is what makes the CONVERSATION itself
        visible to that second principal in the first place -- an
        unowned, unshared conversation 404s for anyone but its own
        creator under `POSTURE_ENTERPRISE` (`visible_conversations`),
        which would otherwise mask the very readability gate this test
        exists to exercise behind an unrelated 404."""
        conversation = self._conversation_with(client, media_type="image/png")
        Document.objects.update(scope=Document.Scope.CONVERSATION,
                                owner_kind="user", owner_key="somebody-else")
        viewer = make_user()
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), user=viewer,
                             level=Share.Level.VIEW)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, viewer)
            body = client.get(
                reverse("chat-conversation", args=[conversation.id])).content.decode()
        assert '<span class="turn-attachment-thumb">' not in body
        # The row itself is still there -- title and status belong to
        # anyone who can see the conversation (the round-12 ruling).
        assert "agenda.md" in body

    def test_a_row_with_no_readable_key_still_renders_its_thumbnail(self):
        """A Django template's dotted lookup on a missing key resolves
        FALSY, which would silently drop every thumbnail the day a
        provider predating the key rendered here -- so the template asks
        `doc.readable is not False`, not `doc.readable`. Pinned by
        source, the way this suite pins template shape."""
        chip = Path("agents/chat/templates/chat/_attachment_chip.html").read_text()
        assert "doc.readable is not False" in chip
