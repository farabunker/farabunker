"""Unit tests for the document-serving/library views in tools/rag/views.py.

One of four modules `tools/rag/tests/test_views.py` split into by feature
area (C-56b): this one, `test_views_ask.py`,
`test_views_upload_and_settings.py`, and
`test_views_retrieval_settings_and_gating.py`. Unlike the registry
module's split (C-56a), this file's original had exactly one `# ---`
divider, so the cut follows class boundaries rather than dividers.

Covers document file/transcript serving (including chat-scoped access and
HTTP range support), the documents library listing, and the
delete/reingest administer actions -- plus C-32's parametrized pin that
each administer refusal names its own verb ("Deleting", "Re-ingesting").
"""
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.urls import reverse

from identity.access import owner_fields
from identity.contracts.postures import POSTURE_ENTERPRISE
from tools.rag.models import Category, Document, DocumentEntitlement
from tools.rag.tests._helpers import (  # noqa: F401 -- `client` is a fixture, discovered by name
    bind_rag_roles, client, grant, make_admin, make_document, make_entitlement, make_user,
    posture, sign_in, user_principal,
)
from tools.rag.views import RANGE_UNSATISFIABLE, _parse_range


@pytest.mark.django_db
class TestDocumentFileView:
    def _make_document(self, path, title="doc.txt", media_type=""):
        return Document.objects.create(
            title=title,
            source_path=str(path),
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            media_type=media_type,
        )

    def _url(self, doc_id):
        return reverse("rag-document-file", args=[doc_id])

    @pytest.mark.parametrize("ext", [".md", ".txt", ".csv"])
    def test_text_like_extensions_served_inline_as_plain_text(self, client, tmp_path, ext):
        content = "hello from farabunker\n"
        path = tmp_path / f"doc{ext}"
        path.write_text(content)
        doc = self._make_document(path, title=f"doc{ext}")

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        body = b"".join(response.streaming_content).decode()
        assert body == content
        assert "attachment" not in response.get("Content-Disposition", "")

    def test_download_param_forces_attachment_disposition(self, client, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("content")
        doc = self._make_document(path)

        response = client.get(self._url(doc.id), {"download": "1"})

        assert response.status_code == 200
        assert "attachment" in response["Content-Disposition"]

    def test_nonexistent_document_id_returns_404(self, client):
        response = client.get(self._url(999999))
        assert response.status_code == 404

    def test_document_whose_file_is_missing_on_disk_returns_404(self, client, tmp_path):
        missing_path = tmp_path / "gone.txt"
        # Deliberately not created on disk.
        doc = self._make_document(missing_path)

        response = client.get(self._url(doc.id))

        assert response.status_code == 404

    def test_served_by_db_id_not_by_path(self, client, tmp_path):
        path = tmp_path / "secret.txt"
        path.write_text("top secret content")
        doc = self._make_document(path)

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        body = b"".join(response.streaming_content).decode()
        assert body == "top secret content"


@pytest.mark.django_db
class TestChatScopedDocumentFileAccess:
    """Round 12 (owner ruling, verbatim: "if I submit a document but
    have scope for chat, then it should only be used in that chat"). A
    conversation-scoped document's FILE stays uploader/`sees_all_
    content`-only -- the conversation page's own strip may show its
    TITLE to any conversation reader (`agents/chat/tests/test_thread.py`
    covers that half), but this route judges by ownership alone,
    exactly as `tools.rag.access.readable_documents`'s own round-12
    clause does."""

    def _make_chat_scoped(self, path, owner, title="chat-doc.txt"):
        return Document.objects.create(
            title=title, source_path=str(path), file_hash="a" * 64,
            doc_type=Document.DocType.PROSE, scope=Document.Scope.CONVERSATION,
            **owner_fields(user_principal(owner)),
        )

    def test_the_uploader_may_read_the_file(self, client, tmp_path):
        path = tmp_path / "mine.txt"
        path.write_text("only for my chat")
        owner = make_user()
        doc = self._make_chat_scoped(path, owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            response = client.get(reverse("rag-document-file", args=[doc.id]))
        assert response.status_code == 200

    def test_a_different_user_gets_a_404_not_a_403(self, client, tmp_path):
        """Never a leak that the row exists at all -- the same 404
        `readable_document`'s every other caller already answers with
        for a document this principal may not reach."""
        path = tmp_path / "mine.txt"
        path.write_text("only for my chat")
        owner, stranger = make_user(), make_user()
        doc = self._make_chat_scoped(path, owner)
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, stranger)
            response = client.get(reverse("rag-document-file", args=[doc.id]))
        assert response.status_code == 404

    def test_sees_all_content_admin_may_read_it(self, client, tmp_path):
        path = tmp_path / "mine.txt"
        path.write_text("only for my chat")
        owner = make_user()
        doc = self._make_chat_scoped(path, owner)
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            response = client.get(reverse("rag-document-file", args=[doc.id]))
        assert response.status_code == 200

    def test_an_admin_with_content_off_still_gets_a_404(self, client, tmp_path):
        """THE ADMINISTER/READ SPLIT, unaffected by chat scope: an
        administrator with `admin_sees_content` off is not
        `sees_all_content`, so this document is exactly as unreadable to
        them as it is to any other non-uploader."""
        path = tmp_path / "mine.txt"
        path.write_text("only for my chat")
        owner = make_user()
        doc = self._make_chat_scoped(path, owner)
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            response = client.get(reverse("rag-document-file", args=[doc.id]))
        assert response.status_code == 404


@pytest.mark.django_db
class TestChatScopedDocumentsInTheLibrary:
    """Round 12 (owner ruling): the library's own ROW LISTING follows
    the identical uploader/`sees_all_content` rule, with a "chat" badge
    marking which rows are scoped -- `tools.rag.access.
    listable_documents`'s own round-12 exclusion."""

    def _make_chat_scoped(self, owner, title="Chat Doc"):
        return Document.objects.create(
            title=title, source_path="/tmp/does-not-matter.txt", file_hash="a" * 64,
            doc_type=Document.DocType.PROSE, scope=Document.Scope.CONVERSATION,
            **owner_fields(user_principal(owner)),
        )

    def test_the_uploader_sees_their_own_row_with_the_chat_badge(self, client):
        owner = make_user()
        self._make_chat_scoped(owner, title="Notes.pdf")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, owner)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Notes.pdf" in body
        assert '<span class="doc-badge" title="Attached to one chat only' in body

    def test_a_different_user_does_not_see_the_row_at_all(self, client):
        owner, stranger = make_user(), make_user()
        self._make_chat_scoped(owner, title="Notes.pdf")
        with posture(POSTURE_ENTERPRISE):
            sign_in(client, stranger)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Notes.pdf" not in body

    def test_sees_all_content_admin_sees_the_row(self, client):
        owner = make_user()
        self._make_chat_scoped(owner, title="Notes.pdf")
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Notes.pdf" in body

    def test_an_admin_with_content_off_does_not_see_someone_elses_chat_scoped_row(
        self, client
    ):
        """The row-widening `is_admin` normally gets (labelling a
        document you cannot read) does not apply here -- a chat-scoped
        document is never labellable at all, so there is nothing for an
        administrator with content off to administer on someone else's."""
        owner = make_user()
        self._make_chat_scoped(owner, title="Notes.pdf")
        admin = make_admin()
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Notes.pdf" not in body

    def test_no_select_checkbox_renders_for_a_chat_scoped_row(self, client):
        """Bulk labelling must not even OFFER a chat-scoped row -- the
        checkbox itself is absent, not merely inert."""
        admin = make_admin()
        doc = self._make_chat_scoped(admin, title="Notes.pdf")
        with posture(POSTURE_ENTERPRISE, admin_sees_content=True):
            sign_in(client, admin)
            body = client.get(reverse("rag-documents")).content.decode()
        assert "Notes.pdf" in body
        assert f'value="{doc.id}"' not in body


class TestParseRange:
    """Unit tests for `_parse_range` (T10 review MAJOR 2 -- RFC 9110
    §14.1.2/§14.2 correctness). A 100-byte file (`file_size=100`, valid
    offsets `0..99`) throughout unless a test says otherwise."""

    def test_end_at_or_past_file_size_clamps_not_rejects(self):
        """`bytes=0-<size+N>` -> 206 spanning the whole file, clamped to
        `file_size - 1` -- never 416 for an end that merely reaches/
        exceeds EOF (a player speculatively asking for `bytes=0-999999`
        on a much smaller file is a common, real pattern)."""
        assert _parse_range("bytes=0-999", 100) == (0, 99)

    def test_start_near_end_with_end_past_file_size_clamps_to_one_byte(self):
        assert _parse_range("bytes=99-999", 100) == (99, 99)

    def test_suffix_range_longer_than_file_serves_whole_file(self):
        """`bytes=-500` on a 100-byte file -- "the last 500 bytes" of a
        file that only HAS 100 -- clamps to the whole file, `(0, 99)`."""
        assert _parse_range("bytes=-500", 100) == (0, 99)

    def test_suffix_range_serves_last_n_bytes(self):
        assert _parse_range("bytes=-10", 100) == (90, 99)

    def test_suffix_range_of_zero_is_ignored(self):
        """`bytes=-0` ("the last zero bytes") is not a request any real
        client means literally -- ignored (`None`), not a zero-length 206
        or a 416."""
        assert _parse_range("bytes=-0", 100) is None

    def test_bare_suffix_dash_with_no_digits_is_ignored(self):
        assert _parse_range("bytes=-", 100) is None

    def test_multi_range_is_ignored(self):
        assert _parse_range("bytes=0-10,20-30", 100) is None

    def test_unknown_unit_is_ignored(self):
        assert _parse_range("items=0-10", 100) is None

    def test_unparseable_syntax_is_ignored(self):
        assert _parse_range("bytes=abc-def", 100) is None

    def test_start_greater_than_end_is_ignored(self):
        assert _parse_range("bytes=5-2", 100) is None

    def test_start_at_file_size_is_unsatisfiable(self):
        assert _parse_range("bytes=100-", 100) is RANGE_UNSATISFIABLE

    def test_start_past_file_size_is_unsatisfiable(self):
        assert _parse_range("bytes=150-200", 100) is RANGE_UNSATISFIABLE

    def test_any_range_on_an_empty_file_is_unsatisfiable(self):
        assert _parse_range("bytes=0-0", 0) is RANGE_UNSATISFIABLE
        assert _parse_range("bytes=0-", 0) is RANGE_UNSATISFIABLE
        assert _parse_range("bytes=-1", 0) is RANGE_UNSATISFIABLE

    def test_open_ended_start_within_bounds_is_satisfiable(self):
        assert _parse_range("bytes=50-", 100) == (50, 99)

    def test_ordinary_bounded_range_within_file_is_satisfiable(self):
        assert _parse_range("bytes=10-19", 100) == (10, 19)


@pytest.mark.django_db
class TestDocumentFileRangeSupport:
    """T10: HTTP Range/206 support for video/audio `media_type` documents
    -- Safari refuses to play an inline `<video>`/`<audio>` element served
    without it. A non-AV document (or an AV one with no `Range` header)
    must render byte-for-byte identically to before this feature existed."""

    def _make_av_document(self, path, media_type="video/mp4"):
        return Document.objects.create(
            title=path.name,
            source_path=str(path),
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            media_type=media_type,
        )

    def _url(self, doc_id):
        return reverse("rag-document-file", args=[doc_id])

    def test_full_get_on_an_av_document_is_unchanged(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == content
        assert response["Accept-Ranges"] == "bytes"

    def test_valid_range_returns_206_with_the_right_slice(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes, offsets 0..99
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=10-19")

        assert response.status_code == 206
        assert b"".join(response.streaming_content) == content[10:20]
        assert response["Content-Range"] == "bytes 10-19/100"
        assert response["Content-Length"] == "10"
        assert response["Accept-Ranges"] == "bytes"

    def test_open_ended_range_returns_to_end_of_file(self, client, tmp_path):
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=90-")

        assert response.status_code == 206
        assert b"".join(response.streaming_content) == content[90:]
        assert response["Content-Range"] == "bytes 90-99/100"

    def test_invalid_range_returns_416(self, client, tmp_path):
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=200-300")

        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */100"

    def test_malformed_range_header_is_ignored_serves_full_200(self, client, tmp_path):
        """T10 review MAJOR 2: a `Range` header this endpoint doesn't
        understand at all is IGNORED (RFC 9110 §14.2's "a server MAY
        ignore the Range header field"), not answered with a 416 -- the
        prior version conflated "I don't understand this header" with "I
        understood it and it's out of bounds"."""
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="not-a-range")

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == content
        assert response["Content-Type"] == "video/mp4"
        assert response["Accept-Ranges"] == "bytes"

    def test_end_past_file_size_clamps_to_206_not_416(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=0-999")

        assert response.status_code == 206
        assert b"".join(response.streaming_content) == content
        assert response["Content-Range"] == "bytes 0-99/100"

    def test_start_near_end_with_huge_end_clamps_to_one_byte(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=99-999")

        assert response.status_code == 206
        assert b"".join(response.streaming_content) == content[99:100]
        assert response["Content-Range"] == "bytes 99-99/100"

    def test_suffix_range_serves_last_n_bytes(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=-10")

        assert response.status_code == 206
        assert b"".join(response.streaming_content) == content[-10:]
        assert response["Content-Range"] == "bytes 90-99/100"

    def test_suffix_range_longer_than_file_serves_whole_file(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=-500")

        assert response.status_code == 206
        assert b"".join(response.streaming_content) == content
        assert response["Content-Range"] == "bytes 0-99/100"

    def test_multi_range_header_is_ignored_serves_full_200(self, client, tmp_path):
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=0-10,20-30")

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == content

    def test_unknown_unit_header_is_ignored_serves_full_200(self, client, tmp_path):
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="items=0-10")

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == content

    def test_start_greater_than_end_is_ignored_serves_full_200(self, client, tmp_path):
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=5-2")

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == content

    def test_start_at_file_size_returns_416(self, client, tmp_path):
        content = b"0123456789" * 10  # 100 bytes, valid offsets 0..99
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=100-")

        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */100"

    def test_range_on_an_empty_file_returns_416(self, client, tmp_path):
        path = tmp_path / "empty.mp4"
        path.write_bytes(b"")
        doc = self._make_av_document(path)

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=0-0")

        assert response.status_code == 416
        assert response["Content-Range"] == "bytes */0"

    def test_non_av_document_ignores_range_header(self, client, tmp_path):
        content = b"plain text content, not media"
        path = tmp_path / "notes.txt"
        path.write_text(content.decode())
        doc = self._make_av_document(path, media_type="text/plain")

        response = client.get(self._url(doc.id), HTTP_RANGE="bytes=0-3")

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == content
        assert "Accept-Ranges" not in response


@pytest.mark.django_db
class TestDocumentTranscriptView:
    """T10: GET /rag/documents/<id>/transcript/ -- a zero-JS listing of a
    document's `extract.json` sidecar. Reads the sidecar straight off
    disk (`settings.DOCUMENTS_DIR`), never through `tools.rag.media`."""

    def _make_document(self, title="clip.mp4", media_type="video/mp4"):
        return Document.objects.create(
            title=title,
            source_path=f"/data/{title}",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            media_type=media_type,
        )

    def _url(self, doc_id):
        return reverse("rag-document-transcript", args=[doc_id])

    def _write_sidecar(self, settings, doc_id, sidecar):
        doc_dir = settings.DOCUMENTS_DIR / str(doc_id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "extract.json").write_text(json.dumps(sidecar))

    def test_timestamp_sidecar_renders_mm_ss_headers(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        self._write_sidecar(
            settings,
            doc.id,
            {"segments": [{"start": 0.0, "end": 2.0, "text": "hello world"}, {"start": 760.0, "end": 762.0, "text": "later on"}]},
        )

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        body = response.content.decode()
        assert "[0:00]" in body
        assert "[12:40]" in body
        assert "hello world" in body
        assert "later on" in body
        # W1 review MINOR 5: video/audio keeps the "Transcript" heading.
        assert "<h1>Transcript</h1>" in body
        assert "Extracted pages" not in body

    def test_page_sidecar_renders_page_headers(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document(title="scan.pdf", media_type="application/pdf")
        self._write_sidecar(
            settings, doc.id, {"segments": [{"page": 1, "text": "first page text"}, {"page": 2, "text": "second page text"}]}
        )

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        body = response.content.decode()
        assert "p. 1" in body
        assert "p. 2" in body
        assert "first page text" in body
        # W1 review MINOR 5: a PDF/image sidecar is page-keyed -- its
        # heading reads "Extracted pages", never "Transcript".
        assert "<h1>Extracted pages</h1>" in body
        assert "<h1>Transcript</h1>" not in body

    def test_no_sidecar_returns_404_not_500(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()

        response = client.get(self._url(doc.id))

        assert response.status_code == 404

    def test_corrupt_sidecar_returns_404_not_500(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_text("{not valid json")

        response = client.get(self._url(doc.id))

        assert response.status_code == 404

    # --- T10 review MAJOR 1: every malformed-sidecar shape the review
    # listed must 404, never 500 (tools.rag.sidecar.read_sidecar/
    # display_segments own the actual hardening; this proves it end to
    # end through the real view). ------------------------------------

    def test_sidecar_json_is_a_list_returns_404_not_500(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_text('["not", "an", "object"]')

        assert client.get(self._url(doc.id)).status_code == 404

    def test_sidecar_json_is_a_string_returns_404_not_500(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_text('"just a string"')

        assert client.get(self._url(doc.id)).status_code == 404

    def test_sidecar_json_is_an_int_returns_404_not_500(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_text("42")

        assert client.get(self._url(doc.id)).status_code == 404

    def test_sidecar_json_is_null_returns_404_not_500(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_text("null")

        assert client.get(self._url(doc.id)).status_code == 404

    def test_segments_value_is_a_list_of_non_dicts_renders_200_no_lines(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        self._write_sidecar(settings, doc.id, {"segments": [1, 2, "three"]})

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        assert response.context["lines"] == []

    def test_segments_value_is_a_dict_renders_200_no_lines(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        self._write_sidecar(settings, doc.id, {"segments": {"0": {"text": "x"}}})

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        assert response.context["lines"] == []

    def test_segments_value_is_a_string_renders_200_no_lines(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        self._write_sidecar(settings, doc.id, {"segments": "not a list"})

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        assert response.context["lines"] == []

    def test_segments_value_is_nested_lists_renders_200_no_lines(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        self._write_sidecar(settings, doc.id, {"segments": [["a"], ["b"]]})

        response = client.get(self._url(doc.id))

        assert response.status_code == 200
        assert response.context["lines"] == []

    def test_non_utf8_sidecar_returns_404_not_500(self, client, tmp_path, settings):
        """T10 review MAJOR 1's specific reproduction: `UnicodeDecodeError`
        is a `ValueError`, not an `OSError` -- the pre-fix view's bare
        `except (FileNotFoundError, OSError, json.JSONDecodeError)` missed
        it entirely and 500'd."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        doc_dir = tmp_path / str(doc.id)
        doc_dir.mkdir(parents=True)
        (doc_dir / "extract.json").write_bytes(b'{"segments": [{"text": "\xff\xfe"}]}')

        response = client.get(self._url(doc.id))

        assert response.status_code == 404

    def test_nonexistent_document_returns_404(self, client):
        response = client.get(self._url(999999))
        assert response.status_code == 404

    def test_text_is_escaped(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document()
        self._write_sidecar(
            settings, doc.id, {"segments": [{"start": 0.0, "end": 1.0, "text": "<script>alert(1)</script>"}]}
        )

        response = client.get(self._url(doc.id))

        body = response.content.decode()
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;" in body


@pytest.mark.django_db
class TestDocumentsView:
    def test_the_library_page_renders_no_placement_chooser(self):
        """C-43. The chooser's include and its hint sat on the library page
        reading a context key (`placement_state`) `DocumentsView` never sets
        -- markup that could not render under any request. The fragment
        itself is live elsewhere (the in-stream upload panel); this pin is
        about THIS page, and about the context key, not the fragment."""
        views_text = (Path(settings.BASE_DIR) / "tools/rag/views.py").read_text()
        assert "placement_state" not in views_text, (
            "DocumentsView now sets placement_state -- the chooser is live again "
            "and this deletion must be reconsidered, not the pin loosened")
        page = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/documents.html").read_text()
        assert "placement_state" not in page
        assert '_placement_choice.html' not in page

    def test_the_placement_fragment_still_has_its_live_consumer(self):
        """The other half: deleting a dead include must not be mistaken for
        deleting the fragment. The in-stream upload panel still renders it."""
        panel = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/panels/documents.html").read_text()
        assert 'include "rag/_placement_choice.html"' in panel

    def test_every_narrow_library_column_is_in_the_width_compaction_list(self):
        """C-51. `td.doc-title` claims `width: 100%`, so every OTHER column
        must claim `width: 1%` or it negotiates against it. The Workstream
        column was added after this rule was written and never joined the
        list, which made it the only column still competing for the title's
        space -- the exact fault the rule exists to prevent. Pinned on the
        template's own text: every class a narrow `<td>` carries must appear
        in the compaction selector."""
        text = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/documents.html").read_text()
        compaction = re.search(r"((?:\s*\.[\w-]+,)+\s*\.[\w-]+)\s*\{\s*width:\s*1%", text)
        assert compaction, "the width-compaction rule itself is gone -- this pin is broken"
        listed = set(re.findall(r"\.([\w-]+)", compaction.group(1)))
        # TWO COLUMNS ARE EXEMPT AND BOTH DECLARE THEIR OWN WIDTH, which is
        # what makes them exempt rather than forgotten: `doc-title` claims
        # `width: 100%` (the whole point of the rule), and `col-select` claims
        # `width: 1.5rem` at `documents.html:457-461` -- a checkbox column
        # sized to its checkbox, not to its content. Anything else that
        # declares no width of its own must be in the compaction list.
        declares_own_width = {"doc-title", "col-select"}
        used = set(re.findall(r'<td class="([\w-]+)"', text)) - declares_own_width
        assert used <= listed, f"narrow columns missing from the compaction list: {sorted(used - listed)}"

    def test_the_tabular_note_span_has_a_rule(self):
        """C-51, second half. `<span class="doc-tabular-note">` is emitted at
        the row level and styled nowhere."""
        text = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/documents.html").read_text()
        assert 'class="doc-tabular-note"' in text, "the span itself is gone -- this pin is broken"
        assert re.search(r"\.doc-tabular-note\s*\{", text)

    def _make_document(self, title, category=None, source_path="/tmp/does-not-matter.txt", **kwargs):
        defaults = dict(
            title=title,
            source_path=source_path,
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            category=category,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_get_renders_shared_nav_with_documents_marked_current(self, client):
        """The Documents page renders the shared shell's app bar, with
        itself marked current. `bind_rag_roles()` is what puts the Ask
        entry on the page at all (UI-1 decision 2) -- this test asserts
        it is present and NOT current, so it has to be available."""
        bind_rag_roles()

        response = client.get(reverse("rag-documents"))

        assert response.status_code == 200
        body = response.content.decode()
        assert f'href="{reverse("rag-ask-page")}"' in body
        assert f'href="{reverse("rag-documents")}"' in body
        assert f'href="{reverse("rag-history")}"' in body
        assert f'href="{reverse("settings-index")}"' in body
        assert f'href="{reverse("rag-documents")}" class="current"' in body
        assert f'href="{reverse("rag-ask-page")}" class="current"' not in body
        assert f'href="{reverse("rag-history")}" class="current"' not in body
        assert f'href="{reverse("settings-index")}" class="current"' not in body

    def test_sidebar_shows_categories_with_counts_plus_all_and_uncategorized(self, client):
        wave3b_med = Category.objects.create(name="Wave3b Medical")
        wave3b_eng = Category.objects.create(name="Wave3b Engineering")

        self._make_document("Med Doc 1", category=wave3b_med)
        self._make_document("Med Doc 2", category=wave3b_med)
        self._make_document("Eng Doc", category=wave3b_eng)
        self._make_document("Loose Doc", category=None)

        response = client.get(reverse("rag-documents"))

        assert response.status_code == 200
        body = response.content.decode()

        # Category names + their per-category counts.
        assert "Wave3b Medical" in body
        assert "Wave3b Engineering" in body
        # All = total docs (4), Uncategorized = 1.
        assert "All" in body

        sidebar = response.context["sidebar"]
        by_name = {entry["name"]: entry for entry in sidebar}
        assert by_name["All"]["count"] == 4
        assert by_name["Wave3b Medical"]["count"] == 2
        assert by_name["Wave3b Engineering"]["count"] == 1
        assert by_name["Uncategorized"]["count"] == 1
        assert by_name["All"]["active"] is True
        assert by_name["Wave3b Medical"]["active"] is False

    def test_default_view_lists_all_documents_paginated(self, client):
        for i in range(26):
            self._make_document(f"Doc {i:02d}")

        response = client.get(reverse("rag-documents"))

        assert response.status_code == 200
        page_obj = response.context["page_obj"]
        assert page_obj.paginator.count == 26
        assert len(page_obj.object_list) == 25
        assert page_obj.has_next() is True

        response_page2 = client.get(reverse("rag-documents"), {"page": 2})
        page_obj_2 = response_page2.context["page_obj"]
        assert len(page_obj_2.object_list) == 1

    def test_category_filter_shows_only_that_categorys_documents(self, client):
        wave3b_med = Category.objects.create(name="Wave3b Medical")
        wave3b_eng = Category.objects.create(name="Wave3b Engineering")

        self._make_document("Med Doc", category=wave3b_med)
        self._make_document("Eng Doc", category=wave3b_eng)

        response = client.get(reverse("rag-documents"), {"category": "Wave3b Medical"})

        assert response.status_code == 200
        titles = [doc.title for doc in response.context["page_obj"].object_list]
        assert titles == ["Med Doc"]
        body = response.content.decode()
        assert "Eng Doc" not in body

    def test_uncategorized_filter_shows_only_null_category_documents(self, client):
        wave3b_med = Category.objects.create(name="Wave3b Medical")

        self._make_document("Med Doc", category=wave3b_med)
        self._make_document("Loose Doc", category=None)

        response = client.get(reverse("rag-documents"), {"category": "Uncategorized"})

        assert response.status_code == 200
        titles = [doc.title for doc in response.context["page_obj"].object_list]
        assert titles == ["Loose Doc"]

    def test_search_matches_titles_across_categories_and_shows_category(self, client):
        wave3b_med = Category.objects.create(name="Wave3b Medical")
        wave3b_eng = Category.objects.create(name="Wave3b Engineering")

        self._make_document("Wave3b Alpha Report", category=wave3b_med)
        self._make_document("Wave3b Alpha Manual", category=wave3b_eng)
        self._make_document("Unrelated Doc", category=wave3b_med)

        response = client.get(reverse("rag-documents"), {"q": "Alpha"})

        assert response.status_code == 200
        titles = sorted(doc.title for doc in response.context["page_obj"].object_list)
        assert titles == ["Wave3b Alpha Manual", "Wave3b Alpha Report"]

        body = response.content.decode()
        assert "Wave3b Alpha Report" in body
        assert "Wave3b Alpha Manual" in body
        assert "Unrelated Doc" not in body
        # Category name shown alongside each search result.
        assert "Wave3b Medical" in body
        assert "Wave3b Engineering" in body

    def test_search_ignores_simultaneously_passed_category(self, client):
        wave3b_med = Category.objects.create(name="Wave3b Medical")
        wave3b_eng = Category.objects.create(name="Wave3b Engineering")

        self._make_document("Wave3b Alpha Report", category=wave3b_med)
        self._make_document("Wave3b Alpha Manual", category=wave3b_eng)

        response = client.get(
            reverse("rag-documents"), {"q": "Alpha", "category": "Wave3b Medical"}
        )

        assert response.status_code == 200
        titles = sorted(doc.title for doc in response.context["page_obj"].object_list)
        assert titles == ["Wave3b Alpha Manual", "Wave3b Alpha Report"]

    def test_pagination_links_preserve_query_and_category(self, client):
        wave3b_med = Category.objects.create(name="Wave3b Medical")
        for i in range(26):
            self._make_document(f"Wave3b Match {i:02d}", category=wave3b_med)

        response = client.get(
            reverse("rag-documents"), {"q": "Match", "category": "Wave3b Medical"}
        )

        assert response.status_code == 200
        body = response.content.decode()
        assert "q=Match" in body
        assert "category=Wave3b+Medical" in body or "category=Wave3b%20Medical" in body

    def test_failed_document_shows_chip_and_retry_form(self, client):
        doc = self._make_document("Broken Doc")
        doc.status = Document.Status.FAILED
        doc.status_detail = "the embedding engine is unreachable"
        doc.save(update_fields=["status", "status_detail"])

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert "Failed" in body
        assert "the embedding engine is unreachable" in body
        assert f'action="{reverse("rag-document-reingest", args=[doc.id])}"' in body
        # W1 review MAJOR 1: a FAILED row keeps the "Retry" label -- only a
        # READY row's form is relabeled "Re-ingest" (see the test below).
        assert '<button type="submit" class="retry-btn">Retry</button>' in body
        assert "Re-ingest" not in body

    def test_ready_document_shows_reingest_form_labeled_reingest(self, client):
        """W1 review MAJOR 1: `document_reingest` (views.py) already accepts
        a READY document (D4's "press Retry on a READY doc" remediation,
        ADR 0014 §18 and README both tell the operator to do exactly that),
        but the library used to gate the whole form on `status == "failed"`
        -- a READY document had no way to re-run ingestion from the UI at
        all. The form now renders for READY too, labeled "Re-ingest" (the
        SAME endpoint as Retry) so the copy is honest about what's already
        finished successfully once."""
        doc = self._make_document("Fine Doc")  # status defaults to READY

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert f'action="{reverse("rag-document-reingest", args=[doc.id])}"' in body
        assert '<button type="submit" class="retry-btn">Re-ingest</button>' in body

    def test_processing_document_shows_chip_without_retry_form(self, client):
        doc = self._make_document("In Flight Doc")
        doc.status = Document.Status.PROCESSING
        doc.save(update_fields=["status"])

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert "Processing…" in body
        assert f'action="{reverse("rag-document-reingest", args=[doc.id])}"' not in body

    def test_pending_document_shows_its_own_chip_distinct_from_processing(self, client):
        """T10: PENDING and PROCESSING previously shared one "Processing…"
        chip -- the status facet is now visible for all four `Document.
        Status` values, so PENDING gets its own "Pending…" wording."""
        doc = self._make_document("Queued Doc")
        doc.status = Document.Status.PENDING
        doc.save(update_fields=["status"])

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert "Pending…" in body
        assert f'action="{reverse("rag-document-reingest", args=[doc.id])}"' not in body

    def test_video_document_shows_video_badge_and_duration(self, client):
        self._make_document("Clip", media_type="video/mp4", duration_seconds=760.0)

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert ">Video<" in body
        assert "12:40" in body

    def test_pdf_document_shows_pdf_badge(self, client):
        self._make_document("Report", media_type="application/pdf")

        response = client.get(reverse("rag-documents"))

        assert ">PDF<" in response.content.decode()

    def test_tabular_document_shows_table_badge_with_honest_sub_line(self, client):
        """W2 (ADR 0014 §18): the "Table" source badge gets an honest
        sub-line -- tabular rows are stored (ADR 0005's `DocumentRow`s) but
        never embedded, so the library must say so plainly rather than
        implying it's searchable like every other row."""
        doc = self._make_document(
            "Sales.csv", doc_type=Document.DocType.TABULAR, media_type="text/csv"
        )

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert ">Table<" in body
        assert "Stored — rows are not searchable yet." in body
        # The Ready chip is untouched by this addition.
        assert ">Ready<" in body
        assert doc.status == Document.Status.READY

    def test_non_tabular_document_shows_no_tabular_sub_line(self, client):
        self._make_document("Notes", doc_type=Document.DocType.PROSE, media_type="text/markdown")

        response = client.get(reverse("rag-documents"))

        assert "not searchable yet" not in response.content.decode()

    @pytest.mark.parametrize("status", [Document.Status.FAILED, Document.Status.PENDING])
    def test_failed_or_pending_tabular_document_gets_no_stored_sub_line(self, client, status):
        """W2/W6 review MINOR 5: a FAILED or still-PENDING tabular row was
        never actually stored -- the "Stored — rows are not searchable
        yet." sub-line must not appear for one, since it isn't true yet
        (PENDING) or never became true (FAILED)."""
        self._make_document(
            "Sales.csv", doc_type=Document.DocType.TABULAR, media_type="text/csv", status=status
        )

        response = client.get(reverse("rag-documents"))

        assert "not searchable yet" not in response.content.decode()

    def test_extraction_summary_renders_as_a_sub_line(self, client):
        self._make_document(
            "Clip",
            media_type="video/mp4",
            extraction={
                "method": "transcription",
                "engine": "whisper",
                "model_id": "whisper-large-v3",
                "connection_name": "",
                "produced_at": "2026-08-20T12:34:56Z",
            },
        )

        response = client.get(reverse("rag-documents"))

        assert "Transcribed by whisper-large-v3 · 2026-08-20" in response.content.decode()

    def test_extraction_summary_with_non_string_produced_at_does_not_500(self, client):
        """T10 re-review MAJOR: a corrupt/adversarial `extraction` snapshot
        with a non-string `produced_at` (an int here) used to raise
        `AttributeError` out of `Document.extraction_summary`'s
        `.split("T", 1)` -- an uncaught 500 for this whole library page,
        since Django templates re-raise property exceptions. The page now
        renders 200 with the model_id verb-line, no date."""
        self._make_document(
            "Clip",
            media_type="video/mp4",
            extraction={
                "method": "transcription",
                "model_id": "whisper-large-v3",
                "produced_at": 20260820,
            },
        )

        response = client.get(reverse("rag-documents"))

        assert response.status_code == 200
        assert "Transcribed by whisper-large-v3 · " in response.content.decode()

    def test_extraction_summary_with_non_string_model_id_does_not_500(self, client):
        """Mirror of the above for `model_id`: a non-string value renders
        the plain verb, page still 200."""
        self._make_document(
            "Clip",
            media_type="video/mp4",
            extraction={
                "method": "extraction",
                "model_id": {"unexpected": "shape"},
                "produced_at": "2026-08-20T12:34:56Z",
            },
        )

        response = client.get(reverse("rag-documents"))

        assert response.status_code == 200
        # A `<span>`, not a `<div>`, since fix round 2: the extraction
        # note is one item on the row's single meta line rather than a
        # block of its own under the title.
        assert '<span class="doc-extraction-summary">Extracted</span>' in response.content.decode()

    def test_transcript_link_shown_only_when_a_sidecar_exists(self, client, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path
        with_sidecar = self._make_document("Clip", media_type="video/mp4")
        (tmp_path / str(with_sidecar.id)).mkdir(parents=True)
        (tmp_path / str(with_sidecar.id) / "extract.json").write_text("{}")
        without_sidecar = self._make_document("Notes", media_type="text/markdown")

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert reverse("rag-document-transcript", args=[with_sidecar.id]) in body
        assert reverse("rag-document-transcript", args=[without_sidecar.id]) not in body

    def test_transcript_link_says_transcript_for_video(self, client, settings, tmp_path):
        """W1 review MINOR 5: `has_transcript` is a bare sidecar stat, so a
        post-W1 ordinary PDF with one textless page now gets a genuine
        (one-page) extraction sidecar despite being an ordinary document in
        every other sense -- calling that link "Transcript" would be a lie.
        Video/audio keeps the "Transcript" label (a time-keyed sidecar)."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document("Clip", media_type="video/mp4")
        (tmp_path / str(doc.id)).mkdir(parents=True)
        (tmp_path / str(doc.id) / "extract.json").write_text("{}")

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert '>Transcript</a>' in body
        assert '>Extracted pages</a>' not in body

    def test_transcript_link_says_extracted_pages_for_pdf(self, client, settings, tmp_path):
        """W1 review MINOR 5: a PDF/image sidecar is page-keyed, not
        time-keyed -- its link reads "Extracted pages", never "Transcript"."""
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document("Scan", media_type="application/pdf")
        (tmp_path / str(doc.id)).mkdir(parents=True)
        (tmp_path / str(doc.id) / "extract.json").write_text("{}")

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert '>Extracted pages</a>' in body
        assert '>Transcript</a>' not in body

    def test_ready_document_shows_a_ready_chip_not_processing_or_failed(self, client):
        """T10: the status facet is visible for all four `Document.Status`
        values -- a READY document previously showed NO chip at all
        (silently "done"); it now shows its own "Ready" chip, the same
        always-visible-status contract Pending/Processing/Failed already
        had."""
        self._make_document("Fine Doc")  # status defaults to READY

        response = client.get(reverse("rag-documents"))

        body = response.content.decode()
        assert "Processing…" not in body
        assert "Failed —" not in body
        assert "doc-status-ready" in body
        assert ">Ready<" in body

    def test_library_renders_upload_form(self, client):
        response = client.get(reverse("rag-documents"))
        body = response.content.decode()
        assert 'action="/rag/documents/upload/"' in body
        assert 'enctype="multipart/form-data"' in body
        assert 'name="files"' in body

    def test_sidebar_entries_carry_id_for_real_categories(self, client):
        c = Category.objects.create(name="Wave3b Medical")
        self._make_document("Doc", category=c)
        response = client.get(reverse("rag-documents"))
        by_name = {e["name"]: e for e in response.context["sidebar"]}
        assert by_name["Wave3b Medical"]["id"] == c.id
        assert by_name["Wave3b Medical"]["manageable"] is True
        assert by_name["All"]["manageable"] is False
        assert by_name["Uncategorized"]["manageable"] is False

    def test_an_admin_with_content_off_sees_the_row_but_no_download_link(self, client):
        """Whole-branch review item 5: `listable_documents` widens the ROW
        to every administrator (`is_admin`, not `sees_all_content`), but
        `rag-document-file` -- the Download link's own route -- is gated
        by `readable_documents`, which 404s for an administrator with the
        content setting off and no grant on the document's entitlement.
        The row must still render (an administrator manages what they
        cannot read), but a link that 404s on click is worse than no link
        -- `row.readable` (`DocumentVisibility.permits`) hides it."""
        finance = make_entitlement(name="Finance")
        document = self._make_document("Confidential Report")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, make_admin())
            response = client.get(reverse("rag-documents"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Confidential Report" in body
        # The CSS class name itself is always in the page's own
        # `<style>` block -- the download HREF is the thing that must
        # not render for this document.
        download_url = reverse("rag-document-file", args=[document.id]) + "?download=1"
        assert download_url not in body

    def test_the_same_admin_gets_the_download_link_once_granted(self, client):
        """Anti-vacuous pin: the link is not simply gone for every
        administrator -- granting the entitlement the document is
        labelled with brings it back."""
        finance = make_entitlement(name="Finance")
        document = self._make_document("Confidential Report")
        DocumentEntitlement.objects.create(document=document, entitlement=finance)
        admin = make_admin()
        grant(finance, user=admin)
        with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
            sign_in(client, admin)
            response = client.get(reverse("rag-documents"))
        assert response.status_code == 200
        download_url = reverse("rag-document-file", args=[document.id]) + "?download=1"
        assert download_url in response.content.decode()


@pytest.mark.django_db
class TestDocumentDelete:
    def _make_document(self, title="doc.txt"):
        return Document.objects.create(
            title=title,
            source_path="/tmp/does-not-matter.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
        )

    @patch("tools.rag.views.services.delete_document")
    def test_deletes_document_and_redirects_to_library(self, mock_delete_document, client):
        doc = self._make_document()

        response = client.post(reverse("rag-document-delete", args=[doc.id]))

        assert response.status_code == 302
        assert response.url == reverse("rag-documents")
        assert mock_delete_document.call_count == 1
        (called_document,), _ = mock_delete_document.call_args
        assert called_document.id == doc.id

    @patch("tools.rag.views.services.delete_document")
    def test_nonexistent_document_id_returns_404(self, mock_delete_document, client):
        response = client.post(reverse("rag-document-delete", args=[999999]))

        assert response.status_code == 404
        mock_delete_document.assert_not_called()


@pytest.mark.django_db
class TestDocumentReingest:
    def _make_document(self, title="doc.txt", status=Document.Status.FAILED, status_detail=""):
        return Document.objects.create(
            title=title,
            source_path="/tmp/does-not-matter.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            status=status,
            status_detail=status_detail,
        )

    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_failed_document_is_requeued_and_redirects(self, mock_enqueue_reingest, client):
        mock_enqueue_reingest.return_value = 42
        doc = self._make_document(status=Document.Status.FAILED)

        response = client.post(reverse("rag-document-reingest", args=[doc.id]))

        assert response.status_code == 302
        assert response.url == reverse("rag-documents")
        (called_doc,), _ = mock_enqueue_reingest.call_args
        assert called_doc.id == doc.id

    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_a_signed_in_user_records_their_own_principal_as_the_actor(
        self, mock_enqueue_reingest, client
    ):
        """Task 10, the acting rule part 1.

        IA-1 (Step 6b): re-ingesting is an ADMINISTRATOR action -- see
        `TestTheLibraryMutationsAreAdministration` -- so the signed-in
        principal this records the actor for must be an admin, or the
        view refuses before `enqueue_reingest` is ever called.
        """
        from identity.contracts.postures import POSTURE_PERSONAL
        from tools.rag.tests._helpers import make_admin, posture, sign_in

        mock_enqueue_reingest.return_value = 42
        doc = self._make_document(status=Document.Status.FAILED)
        admin = make_admin()

        with posture(POSTURE_PERSONAL):
            sign_in(client, admin)
            client.post(reverse("rag-document-reingest", args=[doc.id]))

        _, kwargs = mock_enqueue_reingest.call_args
        assert (kwargs["actor"].kind, kwargs["actor"].key) == ("user", str(admin.pk))

    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_an_open_box_records_the_open_principal_as_the_actor(
        self, mock_enqueue_reingest, client
    ):
        mock_enqueue_reingest.return_value = 42
        doc = self._make_document(status=Document.Status.FAILED)

        client.post(reverse("rag-document-reingest", args=[doc.id]))

        _, kwargs = mock_enqueue_reingest.call_args
        assert (kwargs["actor"].kind, kwargs["actor"].key) == ("open", "box")

    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_ready_document_can_also_be_requeued(self, mock_enqueue_reingest, client):
        mock_enqueue_reingest.return_value = 42
        doc = self._make_document(status=Document.Status.READY)

        response = client.post(reverse("rag-document-reingest", args=[doc.id]))

        assert response.status_code == 302
        mock_enqueue_reingest.assert_called_once()

    @pytest.mark.parametrize("status", [Document.Status.PENDING, Document.Status.PROCESSING])
    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_pending_or_processing_document_is_refused(self, mock_enqueue_reingest, client, status):
        doc = self._make_document(status=status)

        response = client.post(reverse("rag-document-reingest", args=[doc.id]), follow=True)

        assert response.status_code == 200
        assert "already being processed" in response.content.decode()
        mock_enqueue_reingest.assert_not_called()

    def test_nonexistent_document_id_returns_404(self, client):
        response = client.post(reverse("rag-document-reingest", args=[999999]))
        assert response.status_code == 404

    @patch("tools.rag.views.ingest.enqueue_reingest", side_effect=FileNotFoundError("gone"))
    def test_missing_store_file_shows_a_clean_message(self, mock_enqueue_reingest, client):
        doc = self._make_document(status=Document.Status.FAILED)

        response = client.post(reverse("rag-document-reingest", args=[doc.id]), follow=True)

        assert response.status_code == 200
        assert "stored file is missing" in response.content.decode()

    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_queue_rejection_shows_the_documents_own_status_detail(self, mock_enqueue_reingest, client):
        mock_enqueue_reingest.return_value = None
        doc = self._make_document(status=Document.Status.FAILED)

        def _set_detail_and_return_none(document, **kwargs):
            document.status_detail = "Couldn't add this document to the queue — retry it from the document library."
            document.save(update_fields=["status_detail"])
            return None

        mock_enqueue_reingest.side_effect = _set_detail_and_return_none

        response = client.post(reverse("rag-document-reingest", args=[doc.id]), follow=True)

        assert response.status_code == 200
        # Rendered HTML escapes the apostrophe in "Couldn't" -- match the
        # part of the message that survives that unchanged.
        assert "add this document to the queue" in response.content.decode()


# --- C-32: the gate prologues ---------------------------------------------
#
# `document_delete`/`document_reingest` (administer) and `document_file`/
# `document_transcript` (readable) each carried their own gate prologue
# verbatim, comment block included. `_administered_document`/
# `_readable_document_or_404` now do that resolution once each; the
# refusal SENTENCE stays a per-call parameter, because "Deleting" and
# "Re-ingesting" are what the operator reads.


@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "verb"), [
    ("rag-document-delete", "Deleting"),
    ("rag-document-reingest", "Re-ingesting"),
])
def test_each_administer_refusal_names_its_own_verb(client, url_name, verb):
    """C-32. Two handlers share one sentence with two verbs. Sharing
    the prologue must not make them share the verb: the sentence is what
    an operator reads to know what they were refused.

    (A third handler, `document_labels_update`, used to be here with the
    verb "Labelling"; C-36 deleted the route. Its refusal predicate lives
    on in `document_labels_bulk`, which reports refusals as a SKIPPED
    COUNT rather than a 403 sentence, and is tested in
    `tools/rag/tests/test_document_label_page.py::TestBulkLabelling`.)"""
    document = make_document()
    with posture(POSTURE_ENTERPRISE):
        sign_in(client, make_user())
        response = client.post(reverse(url_name, args=[document.pk]))
    assert response.status_code == 403
    assert verb in response.content.decode()
