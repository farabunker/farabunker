"""Unit tests for the B-8 cache-header hardening (round-3, cache half):
`document_file` (both the plain `FileResponse` path and the 206 Range
path) and `document_transcript` must answer `Cache-Control: private,
no-store, max-age=0` with `Cookie` added to `Vary`, via the shared
`foundation.http.mark_private` helper.

A NEW module (Global Constraint 26 / this plan's constraint 26):
`tools/rag/tests/` is a whole directory another session edits, so a task
needing rag tests adds a new module rather than appending to an existing
one.
"""
import json

import pytest
from django.urls import reverse

from tools.rag.models import Document
from tools.rag.tests._helpers import client  # noqa: F401 -- fixture, discovered by name

_PRIVATE_NO_STORE = "private, no-store, max-age=0"


@pytest.mark.django_db
class TestDocumentFileAndTranscriptAnswerPrivateNoStore:
    def _make_document(self, path, title="doc.txt", media_type=""):
        return Document.objects.create(
            title=title,
            source_path=str(path),
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            media_type=media_type,
        )

    def _file_url(self, doc_id):
        return reverse("rag-document-file", args=[doc_id])

    def _transcript_url(self, doc_id):
        return reverse("rag-document-transcript", args=[doc_id])

    def test_document_file_plain_get_carries_the_headers(self, client, tmp_path):
        path = tmp_path / "doc.txt"
        path.write_text("hello")
        doc = self._make_document(path)

        response = client.get(self._file_url(doc.id))

        assert response.status_code == 200
        assert response["Cache-Control"] == _PRIVATE_NO_STORE
        assert "Cookie" in response["Vary"]

    def test_document_file_range_request_carries_them_too(self, client, tmp_path):
        """`document_file` has a second, non-`FileResponse` path for
        Range requests (the 206 `StreamingHttpResponse` branch) -- the
        helper must be on both, or the two drift (B-8)."""
        content = b"0123456789" * 10
        path = tmp_path / "clip.mp4"
        path.write_bytes(content)
        doc = Document.objects.create(
            title=path.name,
            source_path=str(path),
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            media_type="video/mp4",
        )

        response = client.get(self._file_url(doc.id), HTTP_RANGE="bytes=0-3")

        assert response.status_code == 206
        assert response["Cache-Control"] == _PRIVATE_NO_STORE
        assert "Cookie" in response["Vary"]

    def test_document_transcript_carries_the_headers(self, client, tmp_path, settings):
        settings.DOCUMENTS_DIR = tmp_path
        doc = self._make_document(tmp_path / "clip.mp4", title="clip.mp4", media_type="video/mp4")
        doc_dir = settings.DOCUMENTS_DIR / str(doc.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "extract.json").write_text(
            json.dumps({"segments": [{"start": 0.0, "end": 2.0, "text": "hello world"}]})
        )

        response = client.get(self._transcript_url(doc.id))

        assert response.status_code == 200
        assert response["Cache-Control"] == _PRIVATE_NO_STORE
        assert "Cookie" in response["Vary"]

    def test_an_ordinary_page_is_untouched(self, client):
        """This is about the entitlement-gated content routes, not a
        site-wide header -- the Ask page (no document bytes, no
        entitlement gate) must not gain `no-store`."""
        response = client.get(reverse("rag-ask-page"))
        assert "no-store" not in response.get("Cache-Control", "")
