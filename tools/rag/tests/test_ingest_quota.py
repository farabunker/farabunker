"""`_enqueue_ingest_job`'s own `QueueQuotaExceeded` handling (C-7 review,
fix rounds 1-2) -- `tools.rag.ingest._enqueue_ingest_job`, the shared
tail of `enqueue_ingest`/`enqueue_reingest`.

A NEW MODULE: round-3's own constraint 26 (round-2's constraint 18)
holds `tools/rag/tests/` as a whole directory for another session, and
explicitly permits a task needing rag tests to add a new module rather
than open an existing one -- `test_ingest.py` already carries the sibling
`QueueUnavailable`/broad-`Exception` coverage this mirrors
(`test_queue_unavailable_marks_document_failed_and_returns_none`,
`test_other_enqueue_failure_marks_document_failed_and_returns_none`) but
is not opened here.

Same driver shape as those two tests (read, not imported): `patch(
"tools.rag.ingest.enqueue", side_effect=...)` around a real
`ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)`
call, which stages a real `Document` row before `_enqueue_ingest_job`
ever runs.

FIX ROUND 2 RULING CHANGE: round 1 left a quota-refused `Document` at
PENDING with only `status_detail` set, deliberately NOT marking it
FAILED. Withdrawn -- FAILED is the state `tools.rag.views`'s library
page renders `status_detail` and a Retry form against, and PENDING is
refused by `document_reingest` as "already being processed". The test
below now asserts FAILED, and (read, not imported, per this module's own
convention) mirrors `test_views_documents.py::TestDocumentReingest.
test_failed_document_is_requeued_and_redirects`'s own
`@patch("tools.rag.views.ingest.enqueue_reingest")` shape to prove a
quota-refused row is accepted for another attempt, not refused.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from django.urls import reverse

from identity.contracts.principals import SERVICE_PRINCIPAL
from models.contracts.queue import QueueQuotaExceeded
from tools.rag import ingest
from tools.rag.models import Document

pytestmark = pytest.mark.django_db


class TestC7IngestAnswersItsOwnQuotaHonestly:
    @pytest.fixture(autouse=True)
    def _managed_store(self, tmp_path, settings):
        """Redirect the managed document store (ADR 0009) to a throwaway
        directory, distinct from `tmp_path`'s own source-file use below --
        same fixture `test_ingest.py`'s own module-level `_managed_store`
        carries for every test in that file (read, not imported)."""
        store_root = tmp_path / "_managed_store"
        store_root.mkdir()
        settings.DOCUMENTS_DIR = store_root

    @pytest.fixture(autouse=True)
    def _stage_from_the_inbox(self, tmp_path, settings):
        """`stage_document`'s containment check
        (`tools.rag.store.assert_inside_inbox`) needs `settings.
        INGEST_INBOX_DIR` to admit `tmp_path` for `SERVICE_PRINCIPAL` --
        same fixture `test_ingest.py::TestEnqueueIngest` already carries
        for its own sibling `QueueUnavailable`/other-failure tests (read,
        not imported, per this module's own docstring)."""
        settings.INGEST_INBOX_DIR = tmp_path

    def _quota_error(self):
        return QueueQuotaExceeded(
            "1 jobs are already queued or running for this account — the "
            "limit is 1. Wait for one to finish before starting another."
        )

    def test_quota_exceeded_marks_the_document_failed_with_the_quota_detail(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("hello")

        with patch("tools.rag.ingest.enqueue", side_effect=self._quota_error()):
            job_id = ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        assert job_id is None
        doc = Document.objects.get(original_path=str(path.resolve()))
        # FIX ROUND 2 RULING (withdraws round 1's "leave it PENDING"):
        # SAME state `QueueUnavailable`/a genuine enqueue failure already
        # leave a document in (`test_ingest.py`'s own
        # `test_queue_unavailable_marks_document_failed_and_returns_
        # none`/`test_other_enqueue_failure_marks_document_failed_and_
        # returns_none`) -- FAILED is the retriable state the library
        # page renders `status_detail` and a Retry form against; only the
        # STRING distinguishes a quota refusal from those two.
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == ingest._QUEUE_QUOTA_EXCEEDED_DETAIL
        assert "queue is full" in doc.status_detail

    def test_the_quota_detail_is_distinct_from_the_other_two_enqueue_failure_strings(self):
        assert ingest._QUEUE_QUOTA_EXCEEDED_DETAIL not in (
            ingest._QUEUE_UNAVAILABLE_DETAIL, ingest._QUEUE_ENQUEUE_FAILED_DETAIL,
        )

    @patch("tools.rag.views.ingest.enqueue_reingest")
    def test_a_quota_refused_document_is_accepted_for_reingest_not_refused(
        self, mock_enqueue_reingest, tmp_path, client
    ):
        """The whole point of marking FAILED instead of PENDING (fix
        round 2): `document_reingest` refuses a PENDING/PROCESSING row as
        "already being processed" (`test_views_documents.py::
        TestDocumentReingest.test_pending_or_processing_document_is_
        refused`, read not imported) -- a quota-refused row must NOT hit
        that refusal."""
        path = tmp_path / "notes.md"
        path.write_text("hello")
        with patch("tools.rag.ingest.enqueue", side_effect=self._quota_error()):
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)
        doc = Document.objects.get(original_path=str(path.resolve()))
        assert doc.status == Document.Status.FAILED
        mock_enqueue_reingest.return_value = 42

        response = client.post(reverse("rag-document-reingest", args=[doc.id]), follow=True)

        assert response.status_code == 200
        body = response.content.decode()
        assert "already being processed" not in body
        mock_enqueue_reingest.assert_called_once()
