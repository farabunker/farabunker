"""Unit tests for document upload and the library/history settings views
in tools/rag/views.py.

One of four modules `tools/rag/tests/test_views.py` split into by feature
area (C-56b): this one, `test_views_ask.py`,
`test_views_documents.py`, and
`test_views_retrieval_settings_and_gating.py`. Unlike the registry
module's split (C-56a), this file's original had exactly one `# ---`
divider, so the cut follows class boundaries rather than dividers.

Covers document upload (including the corrupt-PDF path), category
management, the history view/settings, and the library settings view --
plus two pins: C-17 (the upload door asks `ingest.supported_exts()`
directly rather than carrying its own byte-identical twin) and C-25 (the
upload hash goes through `foundation.files.sha256_file`, not a private
re-implementation).
"""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import translation
from django.utils.html import escape

from foundation.files import sha256_file
from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
from identity.contracts.principals import OPEN_PRINCIPAL
from tools.rag import ingest
from tools.rag import views as rag_views
from tools.rag.ingest import DocumentPageCapExceededError, MediaDurationExceededError
from tools.rag.labels import document_label_ids
from tools.rag.models import AskRecord, Category, Document, DocumentEntitlement, RagSettings
from tools.rag.tests._helpers import (  # noqa: F401 -- `client` is a fixture, discovered by name
    bind_rag_roles, client, make_admin, make_entitlement, make_user, posture, sign_in,
)


@pytest.mark.django_db
class TestDocumentUpload:
    """T2: `document_upload` streams each accepted file into the inbox and
    then calls `tools.rag.ingest.enqueue_ingest(..., move=True)` directly
    -- mocked here (the same "mock at the module boundary" convention as
    every other queue-touching view test in this file) so these tests never
    actually stage/move a file into a real managed store or touch the
    queue; `TestEnqueueIngest`/`TestEnqueueReingest` in test_ingest.py cover
    `enqueue_ingest` itself. Since the mock never moves anything, the saved
    file is still found sitting in the inbox by every assertion below --
    the real (unmocked) `move=True` behavior only takes the file away once
    `enqueue_ingest` actually runs, which these tests never let happen."""

    @pytest.fixture(autouse=True)
    def _inbox(self, tmp_path, settings):
        settings.INGEST_INBOX_DIR = tmp_path / "inbox"
        return settings.INGEST_INBOX_DIR

    @pytest.fixture(autouse=True)
    def _enqueue_ingest(self):
        with patch("tools.rag.views.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.return_value = 42
            yield mock_enqueue

    def _upload(self, client, files, **data):
        return client.post(reverse("rag-document-upload"), data={"files": files, **data})

    def test_saves_file_into_category_subfolder_and_redirects(self, client, _inbox):
        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = self._upload(client, [f], category="Medical")
        assert resp.status_code == 302
        assert (Path(_inbox) / "Medical" / "guide.md").read_bytes() == b"# hello"

    def test_saved_file_is_queued_for_ingest(self, client, _inbox, _enqueue_ingest):
        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        self._upload(client, [f], category="Medical")

        _enqueue_ingest.assert_called_once_with(
            str(_inbox / "Medical" / "guide.md"), "Medical", move=True,
            actor=OPEN_PRINCIPAL, workstream_id=None,
            document_scope=Document.Scope.UNIVERSAL,
        )

    def test_a_signed_in_user_records_their_own_principal_as_the_actor(
        self, client, _inbox, _enqueue_ingest
    ):
        """Task 10, the acting rule part 1: the upload view's actor is
        `identity.request.principal_for_request(request)`, the same
        function every browser-facing enqueue uses."""
        from identity.contracts.postures import POSTURE_PERSONAL
        from tools.rag.tests._helpers import make_user, posture, sign_in

        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        user = make_user()

        with posture(POSTURE_PERSONAL):
            sign_in(client, user)
            self._upload(client, [f], category="Medical")

        _, kwargs = _enqueue_ingest.call_args
        assert (kwargs["actor"].kind, kwargs["actor"].key) == ("user", str(user.pk))

    def test_labels_are_applied_to_each_queued_document(self, client, _inbox, _enqueue_ingest):
        """C-14 (task 23) characterization: the `entitlements` picker at
        the top of `document_upload` applies to every successfully-queued
        file's `Document` row via `set_document_labels` -- no existing
        test in this class exercised that branch before the per-file body
        moved into `ingest.stage_and_enqueue_one`, which returns the
        `Document` this loop labels rather than looking it up itself."""
        finance = make_entitlement(name="Finance")
        admin = make_admin()
        dest = Path(_inbox) / "guide.md"
        document = Document.objects.create(
            title="guide.md", source_path=str(dest), original_path=str(dest),
            file_hash="a" * 64, doc_type=Document.DocType.PROSE,
        )
        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            self._upload(client, [f], category="", entitlements=[str(finance.pk)])

        assert document_label_ids(document) == frozenset({finance.pk})

    def test_a_raced_files_existing_labels_are_never_touched(self, client, _inbox, _enqueue_ingest):
        """Post-merge review Important 1: a watcher-raced file's
        `outcome.document` is a row THIS request did not stage -- the
        pre-extraction code's `continue` inside `except FileNotFoundError`
        meant it never reached the label block at all (that block sat
        AFTER the `try`/`except`, in the `job_id is not None` success
        path only). `set_document_labels` writes the EXACT selected set
        (deletes anything not selected, writes an audit row per change),
        so applying it to a raced document -- as `outcome.kind ==
        "queued"` alone would, since a raced file is ALSO reported
        "queued" -- would silently relabel a document this request has
        no actual claim on. Pinned directly: a raced document already
        labelled Legal, with Finance selected on THIS upload, must keep
        Legal, gain nothing, and cause no audit write at all."""
        from identity.contracts import actions
        from identity.models import AuditEvent

        finance = make_entitlement(name="Finance")
        legal = make_entitlement(name="Legal")
        admin = make_admin()
        raced = Document.objects.create(
            title="raced.txt", source_path=str(Path(_inbox) / "raced.txt"),
            original_path=str(Path(_inbox) / "raced.txt"),
            file_hash="0" * 64, doc_type=Document.DocType.PROSE,
        )
        DocumentEntitlement.objects.create(document=raced, entitlement=legal)
        _enqueue_ingest.side_effect = FileNotFoundError("watcher got there first")
        f = SimpleUploadedFile("raced.txt", b"a", content_type="text/plain")

        with posture(POSTURE_ENTERPRISE):
            sign_in(client, admin)
            self._upload(client, [f], category="", entitlements=[str(finance.pk)])

        assert document_label_ids(raced) == frozenset({legal.pk})
        assert AuditEvent.objects.filter(
            action__in=(actions.DOCUMENT_LABELLED, actions.DOCUMENT_UNLABELLED)).count() == 0

    def test_multiple_files_are_each_queued(self, client, _inbox, _enqueue_ingest):
        files = [
            SimpleUploadedFile("a.txt", b"a", content_type="text/plain"),
            SimpleUploadedFile("b.txt", b"b", content_type="text/plain"),
        ]
        self._upload(client, files, category="")

        assert _enqueue_ingest.call_count == 2

    def test_success_message_points_at_the_queue_page(self, client, _inbox):
        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": "Medical"}, follow=True
        )

        body = resp.content.decode()
        assert "Queued 1 file(s)" in body
        assert "Queue page" in body

    def test_tabular_upload_gets_an_extra_stored_not_searchable_flash(self, client, _inbox):
        """W2 (ADR 0014 §18): a CSV/XLSX upload IS queued (counts toward
        the ordinary "Queued N file(s)" summary) but also gets its own
        honest line naming what that ingest produces -- SQL rows, never
        searchable chunks."""
        f = SimpleUploadedFile("sales.csv", b"a,b\n1,2\n", content_type="text/csv")
        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": "Medical"}, follow=True
        )

        body = resp.content.decode()
        assert "Queued 1 file(s)" in body
        assert "sales.csv stored as a table; not searchable yet." in body

    def test_prose_upload_gets_no_tabular_flash(self, client, _inbox):
        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": "Medical"}, follow=True
        )

        assert "not searchable yet" not in resp.content.decode()

    def test_uncategorized_lands_directly_in_inbox(self, client, _inbox):
        f = SimpleUploadedFile("note.txt", b"hi", content_type="text/plain")
        self._upload(client, [f], category="")
        assert (Path(_inbox) / "note.txt").exists()

    def test_uncategorized_is_enqueued_with_none_category(self, client, _inbox, _enqueue_ingest):
        f = SimpleUploadedFile("note.txt", b"hi", content_type="text/plain")
        self._upload(client, [f], category="")

        _enqueue_ingest.assert_called_once_with(
            str(_inbox / "note.txt"), None, move=True, actor=OPEN_PRINCIPAL, workstream_id=None,
            document_scope=Document.Scope.UNIVERSAL,
        )

    def test_new_category_field_wins(self, client, _inbox):
        f = SimpleUploadedFile("a.txt", b"x", content_type="text/plain")
        self._upload(client, [f], category="Medical", new_category="Foraging")
        assert (Path(_inbox) / "Foraging" / "a.txt").exists()

    def test_unsupported_extension_is_rejected(self, client, _inbox, _enqueue_ingest):
        f = SimpleUploadedFile("evil.exe", b"MZ", content_type="application/octet-stream")
        self._upload(client, [f], category="Medical")
        assert not (Path(_inbox) / "Medical" / "evil.exe").exists()
        _enqueue_ingest.assert_not_called()

    def test_av_extension_is_rejected_with_media_flag_off(self, client, _inbox, _enqueue_ingest, settings):
        # "vision" stays ON even though this test is about "media" -- see
        # tools.rag.tests._helpers's module docstring for why an
        # HTTP-request-issuing test here must never drop it.
        settings.FARABUNKER_FEATURES = frozenset({"vision"})
        f = SimpleUploadedFile("clip.mp4", b"fake video bytes", content_type="video/mp4")
        self._upload(client, [f], category="Medical")
        assert not (Path(_inbox) / "Medical" / "clip.mp4").exists()
        _enqueue_ingest.assert_not_called()

    def test_av_extension_is_accepted_with_media_flag_on(self, client, _inbox, _enqueue_ingest, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        f = SimpleUploadedFile("clip.mp4", b"fake video bytes", content_type="video/mp4")
        self._upload(client, [f], category="Medical")
        assert (Path(_inbox) / "Medical" / "clip.mp4").read_bytes() == b"fake video bytes"
        _enqueue_ingest.assert_called_once_with(
            str(_inbox / "Medical" / "clip.mp4"), "Medical", move=True,
            actor=OPEN_PRINCIPAL, workstream_id=None,
            document_scope=Document.Scope.UNIVERSAL,
        )

    def test_over_duration_cap_surfaces_its_message_verbatim_like_the_size_branch(
        self, client, _inbox, _enqueue_ingest, settings
    ):
        """T7 review m2: `MediaDurationExceededError` is caught distinctly
        from the generic `except Exception` branch, and its own message
        (`tools.rag.media.duration_cap_message`) is shown verbatim --
        not folded into the generic "couldn't queue" copy, and not
        counted in the `failed` list either (same shape as the size-cap
        branch just above it)."""
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        _enqueue_ingest.side_effect = MediaDurationExceededError(
            "clip.mp4 is 99999s long, over the 7200s media duration limit "
            "(RagSettings.max_media_seconds) -- raise the cap in RAG settings, then retry, "
            "or trim the file to fit."
        )
        f = SimpleUploadedFile("clip.mp4", b"fake video bytes", content_type="video/mp4")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": "Medical"}, follow=True
        )

        body = resp.content.decode()
        assert "over the 7200s media duration limit" in body
        assert "Couldn't queue" not in body
        # W1 spot-check MINOR: the full-size temp copy this view wrote into
        # the watched inbox must not survive a rejection -- `stage_document`
        # raises before it ever moves that file into the managed store.
        assert not (Path(_inbox) / "Medical" / "clip.mp4").exists()

    def test_over_page_cap_surfaces_its_message_verbatim_like_the_duration_branch(
        self, client, _inbox, _enqueue_ingest, settings
    ):
        """T10: `DocumentPageCapExceededError` gets the exact same
        by-name-not-generic-Exception treatment as `MediaDurationExceededError`
        just above -- was previously falling through to the generic
        `except Exception` branch (a misleading "Couldn't queue ... try
        uploading again" summary for a cap rejection that isn't fixed by
        retrying)."""
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        _enqueue_ingest.side_effect = DocumentPageCapExceededError(
            "scan.pdf is 600 pages, over the 500-page document limit "
            "(RagSettings.max_document_pages) -- raise the cap in RAG settings, then retry, "
            "or trim the file to fit."
        )
        f = SimpleUploadedFile("scan.pdf", b"fake pdf bytes", content_type="application/pdf")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": "Medical"}, follow=True
        )

        body = resp.content.decode()
        assert "over the 500-page document limit" in body
        assert "Couldn't queue" not in body
        # W1 spot-check MINOR: same stray-temp-copy cleanup as the
        # duration-cap branch above.
        assert not (Path(_inbox) / "Medical" / "scan.pdf").exists()

    def test_watcher_race_is_counted_as_queued_and_the_loop_continues(self, client, _inbox, _enqueue_ingest):
        """T2 review MINOR (T4 hardening): the watcher polls the same inbox
        out-of-band and can win a race on a slow-to-stage file, so the
        LOSING side's `enqueue_ingest` call (this view's) raises
        `FileNotFoundError`. Counted as "queued" only once a Document row
        is actually confirmed to exist at the original path (T4) -- here
        that row is created directly to stand in for the watcher's own
        successful `stage_document` call, since `enqueue_ingest` itself is
        mocked in this test class. Must not 500 the request or drop the
        remaining files in the same upload."""
        files = [
            SimpleUploadedFile("raced.txt", b"a", content_type="text/plain"),
            SimpleUploadedFile("normal.txt", b"b", content_type="text/plain"),
        ]
        Document.objects.create(
            title="raced.txt",
            source_path=str(Path(_inbox) / "raced.txt"),
            original_path=str(Path(_inbox) / "raced.txt"),
            file_hash="0" * 64,
            doc_type=Document.DocType.PROSE,
        )
        _enqueue_ingest.side_effect = [FileNotFoundError("watcher got there first"), 42]

        resp = client.post(
            reverse("rag-document-upload"), data={"files": files, "category": ""}, follow=True
        )

        assert resp.status_code == 200
        assert _enqueue_ingest.call_count == 2
        # Both files staged locally regardless of which side's enqueue won.
        assert (Path(_inbox) / "raced.txt").exists()
        assert (Path(_inbox) / "normal.txt").exists()
        body = resp.content.decode()
        assert "Queued 2 file(s)" in body

    def test_watcher_race_without_a_document_row_is_counted_as_failed(
        self, client, _inbox, _enqueue_ingest
    ):
        """T4 carry-over hardening: a `FileNotFoundError` with NO Document
        row at the original path is a GENUINE staging failure, not a race
        the watcher won -- the prior cut's inference ("must have been the
        watcher") is upgraded to a verification, so this is counted as
        failed, honestly, never silently folded into "queued"."""
        f = SimpleUploadedFile("orphan.txt", b"a", content_type="text/plain")
        _enqueue_ingest.side_effect = FileNotFoundError("vanished for real, no row exists")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": ""}, follow=True
        )

        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Queued" not in body
        assert "orphan.txt" in body
        assert "Couldn" in body  # "Couldn't queue orphan.txt ..." (apostrophe escaped)

    def test_watcher_race_during_the_unchanged_hash_read_is_counted_as_queued(
        self, client, _inbox, _enqueue_ingest
    ):
        """W1 spot-check MAJOR: the `dest_unchanged` hash read used to run
        OUTSIDE the `try`/`except FileNotFoundError` block that handles the
        watcher race against `enqueue_ingest` -- so a watcher win in THAT
        window (between the write loop finishing and this hash read
        starting) raised an unhandled `FileNotFoundError` straight out of
        the view, a 500 mid-multi-upload.

        C-14: that hash read now lives inside `ingest.stage_and_enqueue_one`
        (shared with the chat door), calling `ingest._sha256` -- so THAT is
        the name patched here (C-25: `ingest._sha256` is a one-line alias to
        `foundation.files.sha256_file`, called below only to compute a REAL
        hash to delegate to). The patch unlinks `dest` itself before
        delegating to the real hash, simulating the watcher moving the
        file out from under this exact read; the real helper then raises
        `FileNotFoundError` opening the now-missing file, which the
        surrounding `try` must catch -- same as a race lost inside
        `enqueue_ingest` itself."""
        files = [
            SimpleUploadedFile("raced.txt", b"a", content_type="text/plain"),
            SimpleUploadedFile("normal.txt", b"b", content_type="text/plain"),
        ]
        Document.objects.create(
            title="raced.txt",
            source_path=str(Path(_inbox) / "raced.txt"),
            original_path=str(Path(_inbox) / "raced.txt"),
            file_hash="0" * 64,
            doc_type=Document.DocType.PROSE,
        )

        def _race_then_hash(path):
            if Path(path).name == "raced.txt":
                Path(path).unlink()
            return sha256_file(path)

        with patch("tools.rag.ingest._sha256", side_effect=_race_then_hash):
            resp = client.post(
                reverse("rag-document-upload"), data={"files": files, "category": ""}, follow=True
            )

        assert resp.status_code == 200
        # The mocked `enqueue_ingest` was never reached for raced.txt --
        # the watcher won before this view got that far -- but normal.txt
        # still queues normally, proving the loop kept going.
        assert _enqueue_ingest.call_count == 1
        assert (Path(_inbox) / "normal.txt").exists()
        body = resp.content.decode()
        assert "Queued 2 file(s)" in body

    def test_other_enqueue_failure_is_skipped_with_a_message_and_the_loop_continues(
        self, client, _inbox, _enqueue_ingest
    ):
        files = [
            SimpleUploadedFile("broken.txt", b"a", content_type="text/plain"),
            SimpleUploadedFile("fine.txt", b"b", content_type="text/plain"),
        ]
        _enqueue_ingest.side_effect = [RuntimeError("queue is on fire"), 42]

        resp = client.post(
            reverse("rag-document-upload"), data={"files": files, "category": ""}, follow=True
        )

        assert resp.status_code == 200
        assert _enqueue_ingest.call_count == 2
        body = resp.content.decode()
        assert "Queued 1 file(s)" in body
        assert "broken.txt" in body
        assert "Couldn" in body  # "Couldn't queue ..." (apostrophe escaped in rendered HTML)

    def test_path_traversal_category_is_blocked(self, client, _inbox):
        f = SimpleUploadedFile("a.txt", b"x", content_type="text/plain")
        self._upload(client, [f], category="../../etc")
        # Nothing escapes the inbox.
        assert list(Path(_inbox).rglob("a.txt")) == [] or all(
            str(p).startswith(str(Path(_inbox).resolve())) for p in Path(_inbox).rglob("a.txt")
        )

    def test_sibling_prefix_category_is_blocked(self, client, _inbox, tmp_path):
        # A sibling directory whose name happens to share the inbox path as a
        # string prefix (e.g. "<tmp>/inbox" vs "<tmp>/inbox-evil"). A naive
        # str.startswith() containment check would wrongly treat this sibling
        # as "inside" the inbox; a real path-containment check must not.
        sibling = tmp_path / "inbox-evil"
        sibling.mkdir()

        f = SimpleUploadedFile("a.txt", b"x", content_type="text/plain")
        resp = self._upload(client, [f], new_category="../inbox-evil")

        assert resp.status_code == 302
        # Nothing was written into the sibling, and nothing escaped the inbox.
        assert list(sibling.rglob("a.txt")) == []
        assert list(tmp_path.rglob("a.txt")) == [] or all(
            str(p).startswith(str(Path(_inbox).resolve())) for p in tmp_path.rglob("a.txt")
        )

    def test_no_files_redirects_without_error(self, client):
        resp = client.post(reverse("rag-document-upload"), data={"category": "Medical"})
        assert resp.status_code == 302

    # --- size cap (T4) -------------------------------------------------

    def test_oversized_file_is_rejected_before_writing_a_byte(
        self, client, _inbox, _enqueue_ingest
    ):
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        f = SimpleUploadedFile("huge.txt", b"x" * 20, content_type="text/plain")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": ""}, follow=True
        )

        assert resp.status_code == 200
        assert not (Path(_inbox) / "huge.txt").exists()
        _enqueue_ingest.assert_not_called()
        body = resp.content.decode()
        assert "huge.txt is larger than the 10 B limit" in body
        assert "RAG settings" in body

    def test_undersized_file_is_accepted(self, client, _inbox, _enqueue_ingest):
        RagSettings.objects.create(pk=1, max_upload_bytes=20)
        f = SimpleUploadedFile("small.txt", b"x" * 10, content_type="text/plain")

        self._upload(client, [f], category="")

        assert (Path(_inbox) / "small.txt").exists()
        _enqueue_ingest.assert_called_once()

    def test_exactly_at_the_cap_is_accepted(self, client, _inbox, _enqueue_ingest):
        RagSettings.objects.create(pk=1, max_upload_bytes=20)
        f = SimpleUploadedFile("exact.txt", b"x" * 20, content_type="text/plain")

        self._upload(client, [f], category="")

        assert (Path(_inbox) / "exact.txt").exists()
        _enqueue_ingest.assert_called_once()

    def test_oversized_file_does_not_block_the_rest_of_the_upload(
        self, client, _inbox, _enqueue_ingest
    ):
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        files = [
            SimpleUploadedFile("huge.txt", b"x" * 20, content_type="text/plain"),
            SimpleUploadedFile("fine.txt", b"x" * 5, content_type="text/plain"),
        ]

        resp = client.post(
            reverse("rag-document-upload"), data={"files": files, "category": ""}, follow=True
        )

        assert resp.status_code == 200
        assert not (Path(_inbox) / "huge.txt").exists()
        assert (Path(_inbox) / "fine.txt").exists()
        body = resp.content.decode()
        assert "Queued 1 file(s)" in body
        assert "huge.txt is larger than" in body

    # --- unchanged re-upload honesty (W1 review MAJOR 1) ----------------

    def test_unchanged_reupload_is_not_counted_as_queued(self, client, _inbox, _enqueue_ingest):
        """`enqueue_ingest` returns `None` when the file's content is
        byte-identical to what's already staged at this path -- the prior
        cut still said "Queued 1 file(s)", a lie (nothing was queued). This
        is counted honestly as "unchanged" instead, never in the queued
        count."""
        content = b"same bytes every time"
        Document.objects.create(
            title="note.txt",
            source_path=str(Path(_inbox) / "note.txt"),
            original_path=str(Path(_inbox) / "note.txt"),
            file_hash=hashlib.sha256(content).hexdigest(),
            doc_type=Document.DocType.PROSE,
        )
        _enqueue_ingest.return_value = None
        f = SimpleUploadedFile("note.txt", content, content_type="text/plain")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": ""}, follow=True
        )

        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Queued" not in body
        assert (
            "1 file(s) already in the library and unchanged — "
            "use Re-ingest to process them again." in body
        )
        # W1 spot-check MINOR: an unchanged re-upload's temp copy must not
        # be left stray in the watched inbox -- `enqueue_ingest` returning
        # `None` here means `stage_document` never moved it anywhere.
        assert not (Path(_inbox) / "note.txt").exists()

    def test_changed_reupload_still_queues_normally(self, client, _inbox, _enqueue_ingest):
        """A re-upload with DIFFERENT bytes than the existing row at this
        path is a genuine change -- `enqueue_ingest` (mocked here) returns a
        real job id, and this must still count as "queued", not
        "unchanged"."""
        Document.objects.create(
            title="note.txt",
            source_path=str(Path(_inbox) / "note.txt"),
            original_path=str(Path(_inbox) / "note.txt"),
            file_hash=hashlib.sha256(b"old bytes").hexdigest(),
            doc_type=Document.DocType.PROSE,
        )
        f = SimpleUploadedFile("note.txt", b"new bytes", content_type="text/plain")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": ""}, follow=True
        )

        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Queued 1 file(s)" in body
        assert "unchanged" not in body

    def test_enqueue_returning_none_with_no_hash_match_counts_as_failed_not_unchanged(
        self, client, _inbox, _enqueue_ingest
    ):
        """`enqueue_ingest` also returns `None` when staging succeeded but
        the queue itself rejected the enqueue (`_enqueue_ingest_job`'s own
        FAILED write) -- a genuine failure, distinct from "unchanged". With
        no pre-existing Document row at this path at all, a `None` return
        can't be the "unchanged" case, so it must fall into `failed`."""
        _enqueue_ingest.return_value = None
        f = SimpleUploadedFile("brand_new.txt", b"never seen before", content_type="text/plain")

        resp = client.post(
            reverse("rag-document-upload"), data={"files": [f], "category": ""}, follow=True
        )

        assert resp.status_code == 200
        body = resp.content.decode()
        assert "Queued" not in body
        assert "unchanged" not in body
        assert "brand_new.txt" in body
        assert "Couldn" in body  # "Couldn't queue brand_new.txt ..." (apostrophe escaped)


@pytest.mark.django_db
class TestDocumentUploadCorruptPdf:
    """T8 review MAJOR 1, probe-reproduced, end to end via the REAL
    upload path -- unlike `TestDocumentUpload` above, `tools.rag.views.
    ingest.enqueue_ingest` is NOT mocked here: the whole point is to prove
    a corrupt PDF's probe failure (`_needs_vision_extraction`'s own,
    un-guarded scan) degrades to an honest FAILED row -- never a stranded
    PENDING one, never an unhandled 500. Only the core queue seam
    (`tools.rag.ingest.enqueue`) is mocked, so this never touches the real
    execution queue.

    RETARGETED (H28, round-3 hardening, B-5): before this task, the
    corrupt-PDF probe ran INSIDE `_enqueue_ingest_job`'s own guarded `try`
    (T8 review MAJOR 1), so the failure surfaced immediately, at upload
    time, with `enqueue()` never even reached. `_enqueue_ingest_job` no
    longer inspects a `.pdf`'s content at all -- the SAME probe
    (`_needs_vision_extraction`, un-guarded) now runs fresh at JOB START
    (`run_ingest_for`, via `_check_document_pages`'s own GUARDED page-cap
    scan swallowing the error first, then this function's un-guarded
    `limit=1` re-scan surfacing it for real) -- so the upload itself now
    succeeds (stages and queues like any other file) and the SAME honest
    FAILED row appears once the job actually runs, which this test now
    simulates directly via `ingest.run_ingest_or_fail`, the exact wrapper
    `tools.rag.jobs.run_ingest` (the queue handler) itself calls."""

    @pytest.fixture(autouse=True)
    def _inbox(self, tmp_path, settings):
        settings.INGEST_INBOX_DIR = tmp_path / "inbox"
        # flag ON, per the review -- "vision" stays alongside "media" (this
        # fixture's test issues real HTTP requests): see tools.rag.tests.
        # _helpers's module docstring for why dropping it here would risk
        # poisoning the process-wide URL resolver cache for every other
        # test in the run.
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        return settings.INGEST_INBOX_DIR

    def test_corrupt_pdf_fails_honestly_once_the_job_runs_and_retry_then_works(self, client, _inbox):
        """RENAMED from `..._upload_fails_honestly_and_retry_then_works`
        -- see the class docstring's RETARGETED paragraph."""
        f = SimpleUploadedFile(
            "corrupt.pdf", b"%PDF-1.4 not a real pdf, corrupt garbage bytes", content_type="application/pdf"
        )

        with patch("tools.rag.ingest.enqueue", return_value=42) as mock_enqueue:
            resp = client.post(
                reverse("rag-document-upload"), data={"files": [f], "category": ""}, follow=True
            )

        assert resp.status_code == 200
        # The upload no longer inspects the file's content at all (H28) --
        # it stages and queues successfully, same as any other file.
        mock_enqueue.assert_called_once()

        doc = Document.objects.get(title="corrupt.pdf")
        assert doc.status == Document.Status.PENDING

        # Simulates the queued job actually running -- the exact wrapper
        # tools.rag.jobs.run_ingest calls.
        # `match=` (H28 review round 1, minor) pins the RAW pypdf error --
        # proves this is the real corruption surfacing, not a fallback to
        # the generic "Couldn't add this document to the queue" sentence
        # `_enqueue_ingest_job`'s own `except Exception` would produce.
        with pytest.raises(Exception, match="Stream has ended unexpectedly"):  # noqa: B017,PT011
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail  # an honest, non-blank detail -- never silently stranded

        # Retry must be ALLOWED (not refused as "already being processed")
        # -- a stranded PENDING row is exactly what the old, unguarded
        # probe call produced, and PENDING/PROCESSING is the one status
        # `document_reingest` refuses to touch.
        with patch("tools.rag.ingest.enqueue", return_value=43) as mock_enqueue_retry:
            retry_resp = client.post(
                reverse("rag-document-reingest", args=[doc.id]), follow=True
            )

        assert retry_resp.status_code == 200
        body = retry_resp.content.decode()
        assert "already being processed" not in body
        mock_enqueue_retry.assert_called_once()
        doc.refresh_from_db()
        assert doc.status == Document.Status.PENDING

        # The retry re-runs the SAME real detection against the SAME
        # corrupt file (once ITS OWN job runs), so it fails the SAME
        # honest way again -- not a crash, not silently stuck at PENDING a
        # second time either.
        # `match=` (H28 review round 1, minor) pins the RAW pypdf error --
        # proves this is the real corruption surfacing, not a fallback to
        # the generic "Couldn't add this document to the queue" sentence
        # `_enqueue_ingest_job`'s own `except Exception` would produce.
        with pytest.raises(Exception, match="Stream has ended unexpectedly"):  # noqa: B017,PT011
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail


@pytest.mark.django_db
class TestCategoryManagement:
    # NOTE: the DB carries a case-insensitive UNIQUE constraint on
    # Category.name (uniq_category_name_ci) and the 0004 migration seeds
    # "Medical" / "Engineering" / "Reference & Manuals" / "Business" into
    # every test database. Tests that need *fresh* categories use names that
    # don't collide with those seeds ("RagMed" / "RagMedical"); tests that
    # want a *real* collision reuse the already-seeded "Medical" /
    # "Engineering" rows directly via .get() rather than re-creating them.

    def test_rename_updates_name(self, client):
        c = Category.objects.create(name="RagMed")
        resp = client.post(reverse("rag-category-rename", args=[c.id]), {"name": "RagMedical"})
        assert resp.status_code == 302
        c.refresh_from_db()
        assert c.name == "RagMedical"

    def test_rename_collision_is_rejected(self, client):
        Category.objects.get(name="Medical")  # seeded; the collision target
        c = Category.objects.get(name="Engineering")  # seeded
        client.post(reverse("rag-category-rename", args=[c.id]), {"name": "medical"})
        c.refresh_from_db()
        assert c.name == "Engineering"  # unchanged

    def test_blank_rename_is_rejected(self, client):
        c = Category.objects.get(name="Medical")  # seeded
        client.post(reverse("rag-category-rename", args=[c.id]), {"name": "   "})
        c.refresh_from_db()
        assert c.name == "Medical"

    def test_delete_moves_documents_to_uncategorized(self, client):
        c = Category.objects.get(name="Medical")  # seeded
        doc = Document.objects.create(
            title="x.md", source_path="/s/x.md", file_hash="a" * 64,
            doc_type=Document.DocType.PROSE, category=c,
        )
        resp = client.post(reverse("rag-category-delete", args=[c.id]))
        assert resp.status_code == 302
        doc.refresh_from_db()
        assert doc.category_id is None
        assert not Category.objects.filter(pk=c.id).exists()

    def test_rename_nonexistent_returns_404(self, client):
        resp = client.post(reverse("rag-category-rename", args=[999999]), {"name": "X"})
        assert resp.status_code == 404


@pytest.mark.django_db
class TestHistoryView:
    """GET /rag/history/."""

    def test_get_renders_shared_nav_with_history_marked_current(self, client):
        # Same reason as the documents page's own nav test: this asserts
        # the Ask entry is present and not current, and since UI-1 it is
        # present only when its two roles are bound.
        bind_rag_roles()

        response = client.get(reverse("rag-history"))

        assert response.status_code == 200
        body = response.content.decode()
        assert f'href="{reverse("rag-ask-page")}"' in body
        assert f'href="{reverse("rag-documents")}"' in body
        assert f'href="{reverse("rag-history")}"' in body
        assert f'href="{reverse("settings-index")}"' in body
        assert f'href="{reverse("rag-history")}" class="current"' in body
        assert f'href="{reverse("rag-ask-page")}" class="current"' not in body
        assert f'href="{reverse("rag-documents")}" class="current"' not in body

    def test_empty_state(self, client):
        response = client.get(reverse("rag-history"))

        assert response.status_code == 200
        assert "No questions asked yet." in response.content.decode()

    def test_renders_newest_first_with_disclosure(self, client):
        AskRecord.objects.create(
            question="older question", connection_name="workstation llama",
            model_id="llama3.1:8b", answer="older answer",
            citations=[{"file": "a.md", "score": 0.5}],
        )
        AskRecord.objects.create(
            question="newer question", connection_name="workstation llama",
            model_id="llama3.1:8b", answer="newer answer",
            citations=[{"file": "b.md", "score": 0.75}],
        )

        response = client.get(reverse("rag-history"))

        assert response.status_code == 200
        body = response.content.decode()
        assert body.index("newer question") < body.index("older question")
        assert "<details>" in body
        assert "newer answer" in body
        assert "older answer" in body
        assert "b.md" in body and "0.7500" in body
        # The "N of LIMIT kept" line moved to Library with the
        # retention field it belongs to (UI-1); pinned there instead.

    def test_long_question_is_truncated_in_the_summary(self, client):
        long_question = "x" * 200
        AskRecord.objects.create(
            question=long_question, connection_name="c", model_id="m", answer="a",
        )

        response = client.get(reverse("rag-history"))
        body = response.content.decode()
        assert long_question not in body
        assert ("x" * 119 + "…") in body

    def test_no_citations_shows_placeholder(self, client):
        AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a", citations=[],
        )

        response = client.get(reverse("rag-history"))
        assert "(no citations)" in response.content.decode()

    def test_timestamp_locator_renders_at_format(self, client):
        """T10 review MINOR 4: a media citation renders "clip.mp4 at
        12:40" straight off the server-built `citation.locator_text` --
        the " at " connector, not the page one -- rather than the
        template re-deriving the connector off `citation.page` itself."""
        AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a",
            citations=[
                {
                    "file": "clip.mp4", "score": 0.5, "locator": "12:40", "locator_text": " at 12:40",
                    "page": None, "start_seconds": 760.0,
                }
            ],
        )

        response = client.get(reverse("rag-history"))
        assert "clip.mp4 at 12:40" in response.content.decode()

    def test_page_locator_renders_comma_format(self, client):
        """T10 review MINOR 4: a page citation renders "report.pdf, p. 3"
        straight off the server-built `citation.locator_text` -- the
        comma connector, since it was built from a page (not timestamp)
        locator server-side."""
        AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a",
            citations=[
                {"file": "report.pdf", "score": 0.5, "locator": "p. 3", "locator_text": ", p. 3", "page": 3}
            ],
        )

        response = client.get(reverse("rag-history"))
        assert "report.pdf, p. 3" in response.content.decode()

    def test_pre_t10_citation_shape_renders_plain_no_locator_suffix(self, client):
        """A citation snapshot written before T10 has no `locator`/`page`
        keys at all -- must render exactly as it always did (the bare
        filename, no " at "/", " suffix conjured from nothing) rather than
        erroring on the missing keys."""
        AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a",
            citations=[{"file": "old.md", "score": 0.5}],
        )

        response = client.get(reverse("rag-history"))
        body = response.content.decode()
        assert "old.md" in body
        assert "old.md at" not in body
        assert "old.md," not in body

    def test_pre_locator_text_citation_shape_renders_plain_no_suffix(self, client):
        """T10 review MINOR 4: a citation snapshot written AFTER `locator`/
        `page` existed but BEFORE `locator_text` was added has the OLD
        bare `locator` key but no `locator_text` -- `history.html` now
        renders `locator_text` only, so this must render plain (no " at "/
        ", " suffix built from the old `locator` value) rather than
        erroring on the missing key. The one documented behavior change
        this review finding makes: a record in exactly this shape used to
        show its locator suffix (derived from `locator`/`page` by the old
        template conditional) and now doesn't, until it's asked again and
        re-recorded with `locator_text` populated."""
        AskRecord.objects.create(
            question="q", connection_name="c", model_id="m", answer="a",
            citations=[{"file": "mid.mp4", "score": 0.5, "locator": "12:40", "page": None}],
        )

        response = client.get(reverse("rag-history"))
        body = response.content.decode()
        assert "mid.mp4" in body
        assert "mid.mp4 at" not in body

    def test_zero_citation_record_from_a_score_floor_short_circuit_renders_plainly(self, client):
        """W4: an `AskRecord` written by the score-floor all-below
        short-circuit (`tools.rag.retrieval.answer_question`) has
        `citations=[]` -- exactly like any other zero-citation record, it
        renders the SAME "(no citations)" placeholder, no special case
        needed in the template."""
        AskRecord.objects.create(
            question="who wrote to FDR about uranium?", connection_name="c", model_id="m",
            answer="Nothing in the library scored above the retrieval floor (0.60) for this question.",
            citations=[],
        )

        response = client.get(reverse("rag-history"))
        body = response.content.decode()
        assert "Nothing in the library scored above the retrieval floor (0.60)" in body
        assert "(no citations)" in body

    def test_question_and_answer_are_escaped(self, client):
        """XSS regression: a question or answer containing a raw <script>
        tag is stored verbatim (the DB is not the trust boundary) but must
        never render unescaped -- Django's default autoescaping is the only
        thing standing between operator-entered text and script execution,
        and nothing in the write-then-render path (AskRecord -> history.html)
        may opt out of it with |safe or {% autoescape off %}.

        T6: the row is created directly (AskRecord is written by
        `tools.rag.jobs.run_ask` now, not this view) -- this test's job
        is only the template's own escaping, already proven independent of
        how the row got there."""
        payload = "<script>alert(1)</script>"
        AskRecord.objects.create(
            question=payload, connection_name="c", model_id="m", answer=payload,
        )

        response = client.get(reverse("rag-history"))
        body = response.content.decode()
        assert payload not in body
        assert escape(payload) in body


@pytest.mark.django_db
class TestLibrarySettingsView:
    """GET /rag/settings/ -- "Library" (UI-1; renamed in UI-2).

    EVERY TEST IN THIS CLASS USED TO LIVE IN `TestHistoryView`, unchanged
    but for the URL it GETs: these are the same six settings cards,
    asserting the same fields and the same copy, on the page that now
    carries them. The forms' own validation and copy are unchanged too;
    what moved is the page rendering them, and -- at S2, Coherence Wave C
    -- the one endpoint they all now post to.
    """

    def test_renders_the_media_duration_field_unconditionally(self, client):
        # T7: rendered regardless of the "media" feature flag -- it's a
        # real, editable field now, matching max_upload_gb's own precedent.
        response = client.get(reverse("rag-settings"))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="max_media_minutes"' in body
        assert f'value="{RagSettings.MAX_MEDIA_SECONDS_DEFAULT / 60:.1f}"' in body

    def test_renders_the_document_pages_field_unconditionally(self, client):
        # T8 review minor 4: rendered regardless of the "media" flag,
        # matching max_media_minutes' own precedent above.
        response = client.get(reverse("rag-settings"))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="max_document_pages"' in body
        assert f'value="{RagSettings.MAX_DOCUMENT_PAGES_DEFAULT}"' in body

    def test_renders_retention_form_with_current_limit(self, client):
        RagSettings.objects.create(pk=1, history_limit=42)

        response = client.get(reverse("rag-settings"))
        body = response.content.decode()
        assert 'name="history_limit"' in body
        assert 'value="42"' in body
        assert (
            "Lowering the limit deletes the oldest records on the next question; "
            "deleted history cannot be recovered." in body
        )

    def test_renders_the_count_line_beside_the_retention_field(self, client):
        """"N of LIMIT kept" belongs to the field it explains, so it moved
        here with it. The count comes off `visible_ask_records` -- the
        same visibility-filtered queryset the history page lists from --
        never a bare `AskRecord.objects.count()`."""
        for question in ("one", "two"):
            AskRecord.objects.create(question=question, connection_name="c",
                                     model_id="m", answer="a", citations=[])

        body = client.get(reverse("rag-settings")).content.decode()
        assert "2 of 100 kept" in body

    def test_renders_upload_cap_form_with_current_limit_in_gb(self, client):
        RagSettings.objects.create(pk=1, max_upload_bytes=round(8.5 * 1024**3))

        response = client.get(reverse("rag-settings"))
        body = response.content.decode()
        assert 'name="max_upload_gb"' in body
        assert 'value="8.5"' in body

    def test_renders_the_retrieval_top_k_field_unconditionally(self, client):
        # W4 (ADR 0014 §14): rendered regardless of the "media" flag,
        # matching every other RagSettings field on this page.
        response = client.get(reverse("rag-settings"))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="retrieval_top_k"' in body
        assert f'value="{RagSettings.RETRIEVAL_TOP_K_DEFAULT}"' in body

    def test_renders_the_retrieval_score_floor_field_unconditionally(self, client):
        response = client.get(reverse("rag-settings"))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="retrieval_score_floor"' in body
        # W4 review MINOR 4/5: rendered via `stringformat:"g"|unlocalize`
        # (never `floatformat`, which is locale-aware) so a comma-decimal
        # locale can't corrupt a `type="number"` input's value -- the
        # default `0.0` renders as the bare "0", not "0.00".
        assert 'value="0"' in body

    def test_renders_the_retrieval_score_floor_field_non_localized(self, client):
        """W4 review MINOR 4/5: `stringformat:"g"|unlocalize` must render a
        plain "." decimal point regardless of the active locale -- a
        comma-decimal render would silently break a `type="number"` input
        (the browser refuses to parse "0,6" as a number at all)."""
        RagSettings.objects.create(pk=1, retrieval_score_floor=0.6)

        with translation.override("de"):
            response = client.get(reverse("rag-settings"))

        body = response.content.decode()
        assert 'value="0.6"' in body
        assert 'value="0,6"' not in body

    def test_renders_the_chunk_tokens_value_from_ingest_not_a_hardcoded_literal(self, client):
        """W4 review MINOR 8: the top_k caveat copy used to hardcode "1024
        tokens" as a literal that could silently drift from the real
        `tools.rag.ingest.CHUNK_TOKENS` the fit check itself multiplies
        by -- now interpolated from the same constant."""
        response = client.get(reverse("rag-settings"))

        assert response.status_code == 200
        body = response.content.decode()
        assert f"each chunk costs up to {ingest.CHUNK_TOKENS} tokens" in body

    def test_renders_the_hybrid_search_checkbox_unconditionally(self, client):
        """W5 (ADR 0014 §18): rendered regardless of the "media" flag,
        matching every other RagSettings field on this page. Default OFF,
        so an untouched install renders the checkbox unchecked."""
        response = client.get(reverse("rag-settings"))

        assert response.status_code == 200
        body = response.content.decode()
        assert 'name="hybrid_search"' in body
        assert "checked" not in body.split('name="hybrid_search"')[1].split(">")[0]

    def test_renders_the_hybrid_search_checkbox_checked_when_enabled(self, client):
        RagSettings.objects.create(pk=1, hybrid_search=True)

        response = client.get(reverse("rag-settings"))

        body = response.content.decode()
        assert "checked" in body.split('name="hybrid_search"')[1].split(">")[0]

    def test_renders_the_score_floor_not_applied_while_hybrid_copy(self, client):
        """S6/W5 review m1 (R2 wording wins everywhere): the operator copy
        must say EXACTLY this sentence -- no magnitude heuristics, no
        "applies to semantic matches only" phrasing that would invite
        reasoning about which results were semantic, and no claim that the
        floor stops the moment the TOGGLE flips rather than after the
        re-encode actually rebuilds the table (composition rule 2 keys off
        the LIVE store's shape, not the toggle -- see
        `TestHybridSearchSettingsUpdate.test_enable_flash_names_the_
        rebuild_and_the_suspended_floor`, which pins the identical sentence
        on the flash-message side)."""
        response = client.get(reverse("rag-settings"))

        assert "After the rebuild, the score floor is no longer applied." in response.content.decode()


@pytest.mark.django_db
class TestTheHistoryPageIsPureHistory:
    """UI-1 work item D: the reading surface reads, and nothing else.

    Five settings cards used to sit ABOVE the first row of history. Their
    absence is the claim, so it is the assertion: the library's settings
    endpoint appears nowhere on this page, and neither does a form of any
    kind.
    """

    # S2 (Coherence Wave C): ONE endpoint, where seven per-field URLs
    # used to be. Still a tuple, so the sweep below reads unchanged if a
    # second settings endpoint ever joins it.
    _NAMES = ("rag-settings-update",)

    def test_it_renders_none_of_the_settings_endpoints(self, client):
        body = client.get(reverse("rag-history")).content
        for name in self._NAMES:
            assert reverse(name).encode() not in body

    def test_it_renders_no_form_at_all(self, client):
        """PINNED to the open posture: on an accounts-on box the SHELL
        renders a form of its own (the sign-out POST, ruling R-T8), and
        this assertion is about the page, not about the shell."""
        with posture(POSTURE_OPEN):
            body = client.get(reverse("rag-history")).content.decode()
        assert "<form" not in body

    def test_it_still_renders_its_own_records(self, client):
        AskRecord.objects.create(question="a question", connection_name="c",
                                 model_id="m", answer="an answer", citations=[])
        body = client.get(reverse("rag-history")).content.decode()
        assert "a question" in body
        assert "an answer" in body


# --- C-17: the byte-identical extension twin -----------------------------
#
# `views` used to carry a character-for-character copy of
# `ingest.supported_exts`, and each docstring named the other as its
# twin. Deleted: the upload door now asks `ingest.supported_exts()`
# directly, the same function every other caller already asks.


def test_the_upload_door_asks_ingest_which_extensions_it_accepts():
    """C-17. `views` used to carry a byte-identical twin of
    `ingest.supported_exts`, and each docstring named the other. One
    function decides what this module can ingest; the upload form asks it."""
    assert not hasattr(rag_views, "supported_upload_exts")


# --- C-25: the upload hash goes through foundation.files.sha256_file -----


def test_the_rag_views_module_has_no_private_hash_helper():
    """C-25. `_upload_sha256` re-implemented `foundation.files.sha256_file`
    -- same algorithm, same 1 MiB block -- and justified itself by not
    wanting to reach into `ingest`'s private helper. Importing the
    ORIGINAL answers that without a third copy; `ingest._sha256` has been
    a one-line alias to it all along."""
    assert not hasattr(rag_views, "_upload_sha256")
