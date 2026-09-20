"""`document_upload`'s own summary line for a quota-refused upload (C-7
review, fix round 2) -- `tools.rag.views.document_upload`'s one-case
branch beside the generic "failed" bucket, `Couldn't queue ... for
ingest -- try uploading again.`.

A NEW MODULE, per the fix-round-2 brief's own suggestion: this view's
setup (a real multipart POST, a real staged file) differs from
`tools/rag/tests/test_ingest_quota.py`'s ingest-level driver, and
round-3's own constraint 26 holds `tools/rag/tests/` as a whole
directory for another session -- adding a new module is the permitted
way to add coverage here, so `test_views_upload_and_settings.py`'s own
`TestDocumentUpload` (which already covers this view's OTHER outcomes:
rejected, oversize, refused, queued, unchanged, and the generic failed
case) is not opened.

UNLIKE that class's own `_enqueue_ingest` fixture (which mocks
`tools.rag.ingest.enqueue_ingest` wholesale, bypassing real staging so
no `Document` row exists for the generic-failure case it tests), this
drives the REAL `stage_document` -> `enqueue_ingest` ->
`_enqueue_ingest_job` chain and mocks only the queue seam
(`tools.rag.ingest.enqueue`) -- so a real `Document` row lands FAILED
with the real `_QUEUE_QUOTA_EXCEEDED_DETAIL` (fix round 2's own ruling
change, `tools/rag/tests/test_ingest_quota.py`), which is exactly the
shape `stage_and_enqueue_one`'s `failed` branch needs to carry a
`document` into `document_upload`'s new one-case branch.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from models.contracts.queue import QueueQuotaExceeded
from tools.rag import ingest

pytestmark = pytest.mark.django_db


class TestC7UploadSurfacesTheQuotaDetailNotTheGenericLine:
    @pytest.fixture(autouse=True)
    def _inbox(self, tmp_path, settings):
        settings.INGEST_INBOX_DIR = tmp_path / "inbox"

    @pytest.fixture(autouse=True)
    def _managed_store(self, tmp_path, settings):
        """Redirect the managed document store (ADR 0009) to a throwaway
        directory -- same fixture `test_ingest.py`'s own module-level
        `_managed_store` carries (read, not imported)."""
        store_root = tmp_path / "_managed_store"
        store_root.mkdir()
        settings.DOCUMENTS_DIR = store_root

    def _quota_error(self):
        return QueueQuotaExceeded(
            "1 jobs are already queued or running for this account — the "
            "limit is 1. Wait for one to finish before starting another."
        )

    def test_a_quota_refused_upload_shows_its_own_detail_not_try_again(self, client):
        f = SimpleUploadedFile("notes.md", b"hello", content_type="text/markdown")

        with patch("tools.rag.ingest.enqueue", side_effect=self._quota_error()):
            response = client.post(
                reverse("rag-document-upload"), data={"files": [f]}, follow=True
            )

        body = response.content.decode()
        # THE ONE-CASE BRANCH (fix round 2): the honest, quota-naming
        # detail this document's own `status_detail` carries, NOT the
        # generic "try uploading again" line -- misleading here, since
        # retrying immediately hits the same cap.
        assert ingest._QUEUE_QUOTA_EXCEEDED_DETAIL in body
        assert "try uploading again" not in body

    def test_a_genuine_enqueue_failure_still_uses_the_generic_line(self, client):
        """The one-case branch must not swallow every `failed` outcome --
        only a quota refusal gets its own line; a genuine enqueue failure
        (any other exception) still falls into the generic summary."""
        f = SimpleUploadedFile("notes.md", b"hello", content_type="text/markdown")

        with patch("tools.rag.ingest.enqueue", side_effect=ValueError("planner blew up")):
            response = client.post(
                reverse("rag-document-upload"), data={"files": [f]}, follow=True
            )

        body = response.content.decode()
        assert "try uploading again" in body
        assert ingest._QUEUE_QUOTA_EXCEEDED_DETAIL not in body
