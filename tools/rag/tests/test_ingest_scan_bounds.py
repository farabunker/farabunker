"""Regression tests for B-5 (round-3 hardening, H28): the stage-time PDF
text scan used to run synchronously, inside the HTTP request (or a watcher
poll tick), with no bound on pages EXAMINED -- only on pages COLLECTED,
which does nothing for an ordinary all-text PDF (the common case), since
proving "no page lacks a text layer" means looking at every page
(`tools.rag.readers.pdf_textless_pages`'s own docstring).

Constraint 26 (this plan's own restatement of round-2 constraint 18):
`tools/rag/tests/` is a whole directory another session edits too, so a
task needing rag tests here adds a NEW module rather than appending to any
existing one -- this file.

Round 1 review found the first cut incomplete on two counts, both covered
below alongside the original two:

  - the ONE surviving scan (the page-cap decision `_check_document_pages`
    used to make at STAGE time, C-08) no longer runs on the thread that
    stages/enqueues a document at all -- neither `stage_document` nor
    `tools.rag.ingest._enqueue_ingest_job` touches `readers.
    pdf_textless_pages` anymore. Both the page-cap decision and the
    vision-routing decision that used to share that one scan now happen
    together, once, at the top of a `rag.ingest` RUN (`run_ingest_for`) --
    the queue worker, or the CLI's own synchronous process, never a web
    request.
  - `readers.pdf_textless_pages` gained a `max_examined` keyword that
    bounds pages EXAMINED. Round 1: this bound now has a real caller --
    `readers.PDF_SCAN_MAX_PAGES_EXAMINED`, a named module constant, is the
    DEFAULT the queue worker's own job-start check applies; a scan that
    cannot conclude within it makes the job refuse HONESTLY (fail-closed),
    naming the ceiling and the page count examined. The CLI's `ingest_path`
    explicitly opts out (`None`, unbounded).
  - `_enqueue_ingest_job`'s payload is no longer purely `medium_for`'s
    extension-only answer either (round 1, finding 2): while "media" is
    on, EVERY `.pdf` gets the pessimistic `"pdf-scanned"` token -- still no
    content scan, but no longer indistinguishable from an ordinary text
    PDF for `tools.rag.jobs.plan_ingest`'s own admission accounting.
  - `readers.pdf_textless_pages` ALWAYS returns `(pages, truncated)` now
    (round 1, finding 4) -- never a bare list -- removing a
    truthiness-of-tuple footgun a polymorphic return shape had.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
# `pypdf._page` (underscore-prefixed -- a private submodule, not part of
# pypdf's own public API) is reached into ONLY so `patch.object(PageObject,
# "extract_text", ...)` can prove the scan actually STOPPED at a given page
# count (by counting real calls) rather than merely trusting the returned
# list -- the same test-only shape `test_readers.py`'s own
# `TestPdfTextlessPages` class already uses, for the identical reason.
from pypdf._page import PageObject

from identity.contracts.principals import SERVICE_PRINCIPAL
from tools.rag import ingest, jobs, readers
from tools.rag.models import Document, RagSettings
from tools.rag.tests._helpers import client, make_pdf_bytes  # noqa: F401 -- `client` is a fixture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _managed_store(tmp_path, settings):
    """Redirect the managed document store (ADR 0009) to a throwaway
    directory -- the same fixture `test_ingest.py`'s own module carries,
    duplicated here rather than imported across modules (this package's
    own convention: see `tools.rag.tests._helpers.isolated_tool_registry`'s
    docstring for why a shared fixture stays put rather than crossing test
    modules)."""
    store_root = tmp_path / "_managed_store"
    store_root.mkdir()
    settings.DOCUMENTS_DIR = store_root
    return store_root


def _all_text_pdf(tmp_path, *, name: str = "all-text.pdf", pages: int) -> Path:
    """An N-page PDF with a real text layer on EVERY page -- the worst
    case `readers.pdf_textless_pages`'s own docstring names: no textless
    page ever short-circuits the scan, so it must walk the whole file to
    conclude "nothing is textless"."""
    path = tmp_path / name
    path.write_bytes(make_pdf_bytes([f"page {i}" for i in range(pages)]))
    return path


def _over_cap_pdf(tmp_path, *, name: str = "scan.pdf", textless_pages: int = 3) -> Path:
    """A PDF with `textless_pages` pages, none of them carrying a text
    layer -- paired with a `RagSettings.max_document_pages` cap smaller
    than `textless_pages` by whichever test uses this."""
    path = tmp_path / name
    path.write_bytes(make_pdf_bytes([None] * textless_pages))
    return path


class TestB5TheScanDoesNotRunOnTheRequestThread:
    """Neither `stage_document` nor the upload view's whole request cycle
    calls `readers.pdf_textless_pages` anymore -- the finding's own
    reproduction (a hand-built 5,000-page all-text PDF, 7.82s of CPU on
    the worktree's own reader) is a cost this suite does not need to pay
    to prove the fix: `mock.patch` on the scan itself proves it is never
    reached at all, regardless of how expensive a real run would be."""

    def test_staging_does_not_call_the_textless_scan(self, tmp_path, settings):
        """Staging's job is to make the row and the file exist."""
        # THE VISION-FLAG RULE (`tools.rag.tests._helpers`'s own module
        # docstring): this file also has a test that touches the Django
        # test Client/`reverse()`, so `test_flag_hygiene.py`'s own gate
        # requires "vision" in every override here, even one that (like
        # this test) never itself resolves a URL.
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        path = _all_text_pdf(tmp_path, pages=50)

        with patch.object(ingest.readers, "pdf_textless_pages") as scan:
            doc, changed = ingest.stage_document(str(path), move=False, actor=None)

        scan.assert_not_called()
        assert changed is True
        assert doc.status == Document.Status.PENDING

    def test_the_queued_job_makes_the_page_cap_decision(self, tmp_path, settings):
        """The page-cap refusal that used to happen at `stage_document`
        time now happens at the top of a `rag.ingest` RUN
        (`ingest.run_ingest_for`, reached the same way the queue worker
        reaches it -- via `run_ingest_or_fail`, `tools.rag.jobs.
        run_ingest`'s own call) -- never at stage/enqueue time, and the
        cap it names is the SAME operator sentence
        (`tools.rag.media.page_cap_message`) it always was."""
        # THE VISION-FLAG RULE (`tools.rag.tests._helpers`'s own module
        # docstring): this file also has a test that touches the Django
        # test Client/`reverse()`, so `test_flag_hygiene.py`'s own gate
        # requires "vision" in every override here, even one that (like
        # this test) never itself resolves a URL.
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        RagSettings.objects.create(pk=1, max_document_pages=2)
        path = _over_cap_pdf(tmp_path, textless_pages=3)  # 3 textless pages, cap is 2

        # Staging itself succeeds -- it no longer looks at the cap at all.
        doc, changed = ingest.stage_document(str(path), move=False, actor=None)
        assert changed is True
        assert doc.status == Document.Status.PENDING

        with pytest.raises(ingest.DocumentPageCapExceededError, match="over the 2-page document limit"):
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert "page" in doc.status_detail

    def test_the_upload_view_returns_before_any_scan_could_have_run(self, client, tmp_path, settings):
        # The VISION-FLAG RULE (`tools.rag.tests._helpers`'s own module
        # docstring): a real HTTP request/`reverse()` call needs "vision"
        # kept in the overridden flag set, even though this test's own
        # point is about "media".
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        settings.INGEST_INBOX_DIR = tmp_path / "inbox"
        upload = SimpleUploadedFile(
            "big.pdf", make_pdf_bytes([f"page {i}" for i in range(30)]), content_type="application/pdf"
        )

        with patch.object(ingest.readers, "pdf_textless_pages") as scan, \
                patch("tools.rag.ingest.enqueue", return_value=99):
            client.post(reverse("rag-document-upload"), data={"files": [upload]})

        scan.assert_not_called()


class TestB5AdmissionIsReservedPessimistically:
    """Round 1, finding 2: `_enqueue_ingest_job` declares `"pdf-scanned"`
    for EVERY `.pdf` while "media" is on -- off the extension alone, no
    scan -- so `tools.rag.jobs.plan_ingest` always reserves `rag.extract`
    capacity for a `.pdf`, never under-provisioning a genuinely-scanned
    one just because the enqueue-time payload can no longer afford to look
    at the file's content."""

    def test_pdf_enqueue_declares_the_pessimistic_medium_with_media_on(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        settings.INGEST_INBOX_DIR = tmp_path  # SERVICE_PRINCIPAL's own containment root
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))  # an ORDINARY text PDF

        with patch.object(ingest.readers, "pdf_textless_pages") as scan, \
                patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        scan.assert_not_called()  # still no content scan -- extension + flag only
        (_, payload), _ = mock_enqueue.call_args
        assert payload["medium"] == "pdf-scanned"

    def test_pdf_enqueue_declares_plain_prose_with_media_off(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision"})  # "media" pinned OFF
        settings.INGEST_INBOX_DIR = tmp_path  # SERVICE_PRINCIPAL's own containment root
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        (_, payload), _ = mock_enqueue.call_args
        assert payload["medium"] == "prose"


class TestB5TheScanIsBoundedByPagesExamined:
    """`readers.pdf_textless_pages(path, *, max_examined=...)` -- pages
    EXAMINED, not merely pages COLLECTED (`limit`'s own job, which does
    nothing for an all-text PDF: nothing is ever collected, so nothing
    ever reaches `limit`)."""

    def test_max_examined_stops_the_walk(self, tmp_path):
        # 200 pages, not the finding's own 5,000 -- a real `pypdf` pass
        # over either proves the same thing; the smaller fixture keeps
        # this suite fast. `all_text_pdf` (the brief's own name) is
        # `_all_text_pdf` here, this module's private helper above.
        pdf = _all_text_pdf(tmp_path, pages=200)

        original_extract_text = PageObject.extract_text
        with patch.object(
            PageObject, "extract_text", autospec=True, side_effect=original_extract_text
        ) as mock_extract:
            found, truncated = readers.pdf_textless_pages(pdf, limit=10, max_examined=100)

        assert truncated is True
        assert found == []
        # The load-bearing half: the walk actually STOPPED at 100 pages
        # examined, not merely "the returned list happens to be capped".
        assert mock_extract.call_count == 100

    def test_an_unbounded_call_still_walks_the_whole_file(self, tmp_path):
        """The shell command (`ingest_path`) and the queued job's own
        DEFAULT may take as long as they need; `max_examined=None`
        (omitted) never truncates. `pdf_textless_pages` ALWAYS returns the
        `(pages, truncated)` pair now (round 1, finding 4) -- `truncated`
        is simply `False` here, not a bare list."""
        page_texts = [f"page {i}" for i in range(20)] + [None]  # textless page sits LAST
        pdf = tmp_path / "textless-at-the-end.pdf"
        pdf.write_bytes(make_pdf_bytes(page_texts))

        result = readers.pdf_textless_pages(pdf, limit=10)

        assert result == ([21], False)  # found it -- the walk did not stop early

    def test_limit_zero_with_max_examined_is_the_pair_shape_too(self, tmp_path):
        """`limit <= 0` short-circuits before opening the file at all
        (W1 review MINOR 6) -- still returns `([], False)`, the same pair
        shape, when `max_examined` is given (round 1, finding 4's own
        explicit ask)."""
        path = tmp_path / "does-not-exist.pdf"  # never opened -- proves it

        assert readers.pdf_textless_pages(path, limit=0, max_examined=100) == ([], False)
        assert readers.pdf_textless_pages(path, limit=-1, max_examined=100) == ([], False)

    def test_the_not_truncated_case_with_max_examined_given(self, tmp_path):
        """`max_examined` given, but the scan concludes (finds `limit`
        textless pages, or exhausts the document) well before reaching it
        -- `truncated` is `False`, the ordinary, expected outcome for any
        real-sized document under a sane ceiling."""
        pdf = tmp_path / "three-textless.pdf"
        pdf.write_bytes(make_pdf_bytes([None, None, None]))

        found, truncated = readers.pdf_textless_pages(pdf, limit=10, max_examined=100)

        assert found == [1, 2, 3]
        assert truncated is False


class TestB5TheJobRefusesHonestlyWhenTheScanCannotConclude:
    """Round 1, finding 1: `PDF_SCAN_MAX_PAGES_EXAMINED` has a real caller
    now -- `run_ingest_for`'s own default applies it to `_check_document_
    pages`'s job-start scan. A document the scan cannot resolve within
    that ceiling is refused HONESTLY (fail-closed): `_check_document_
    pages` returns `media.page_scan_ceiling_message`'s wording (naming the
    ceiling and the page count examined), never a silent guess either
    direction."""

    def test_the_5000_page_all_text_shape_short_circuits_and_refuses(self, tmp_path, settings):
        """The finding's own reproduction shape: a large all-text PDF
        (no textless page to ever short-circuit on) -- proves the JOB
        (not just `readers.pdf_textless_pages` in isolation, `Test
        B5TheScanIsBoundedByPagesExamined` above already covers that)
        stops at the ceiling and refuses, rather than either scanning the
        full 5,000 pages or silently admitting an unresolved document."""
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        path = _all_text_pdf(tmp_path, name="huge.pdf", pages=5000)
        doc, changed = ingest.stage_document(str(path), move=False, actor=None)
        assert changed is True

        original_extract_text = PageObject.extract_text
        with patch.object(
            PageObject, "extract_text", autospec=True, side_effect=original_extract_text
        ) as mock_extract:
            with pytest.raises(ingest.DocumentPageCapExceededError) as exc_info:
                ingest.run_ingest_or_fail(doc, doc.file_hash)

        # The load-bearing half: the scan actually STOPPED at the ceiling
        # -- it never walked anywhere near the full 5,000 pages.
        assert mock_extract.call_count == readers.PDF_SCAN_MAX_PAGES_EXAMINED
        message = str(exc_info.value)
        assert str(readers.PDF_SCAN_MAX_PAGES_EXAMINED) in message
        assert "could not be checked" in message

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert str(readers.PDF_SCAN_MAX_PAGES_EXAMINED) in doc.status_detail

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_media_off_the_job_start_cap_site_never_scans_at_all(
        self, mock_gateway, mock_rag_index, tmp_path, settings
    ):
        """The gate (matching `test_ingest.py::TestEnqueueIngest::test_
        with_media_off_nothing_changes`'s own precedent, at THIS call site
        instead): with "media" off, `_check_document_pages` returns
        before ever reaching `readers.pdf_textless_pages` -- a `.pdf`'s
        page count is never this cap's business at all while the flag is
        off, ceiling included. `rag_index`/`gateway` mocked because, with
        no cap and no vision routing in the way, this document actually
        reaches the ordinary prose embed path."""
        mock_rag_index.get_index.return_value = MagicMock()
        settings.FARABUNKER_FEATURES = frozenset({"vision"})  # "media" pinned OFF
        path = _all_text_pdf(tmp_path, pages=5)
        doc, changed = ingest.stage_document(str(path), move=False, actor=None)
        assert changed is True

        with patch.object(ingest.readers, "pdf_textless_pages") as scan:
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        scan.assert_not_called()
        doc.refresh_from_db()
        assert doc.status == Document.Status.READY


class TestB5TheCorruptPdfFallbackScanIsBoundedToo:
    """The one `pdf_textless_pages` call left unbounded after round 1:
    `_needs_vision_extraction`'s own fallback scan.

    It runs in exactly one case -- `_check_document_pages`' guarded
    job-start scan RAISED, so no result was threaded through
    `probe_out` and nobody has looked. That is the corrupt or otherwise
    unparseable PDF: the file already known to be misbehaving was the
    only one still getting an unbounded walk on a worker thread. The
    ceiling the rest of the job-start path uses applies here too."""

    def test_the_fallback_scan_passes_the_module_ceiling(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"not really a pdf at all")

        with patch.object(ingest.readers, "pdf_textless_pages",
                          return_value=([], False)) as scan:
            assert ingest._needs_vision_extraction(path, "prose") is False

        assert scan.call_args.kwargs["max_examined"] == readers.PDF_SCAN_MAX_PAGES_EXAMINED

    def test_the_fallback_scan_actually_stops_at_the_ceiling(self, tmp_path, settings, monkeypatch):
        """The behavioural half, with the ceiling lowered so the fixture
        stays small: an all-text PDF gives the fallback no textless page
        to short-circuit on, so an unbounded walk reads every page."""
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        monkeypatch.setattr(readers, "PDF_SCAN_MAX_PAGES_EXAMINED", 3)
        path = _all_text_pdf(tmp_path, name="fallback.pdf", pages=10)

        original_extract_text = PageObject.extract_text
        with patch.object(
            PageObject, "extract_text", autospec=True, side_effect=original_extract_text
        ) as mock_extract:
            assert ingest._needs_vision_extraction(path, "prose") is False

        assert mock_extract.call_count == 3

    def test_an_examined_out_scan_still_routes_the_document_as_prose(self, tmp_path, settings):
        """`truncated` stays irrelevant to the answer (this function's own
        docstring): an examined-out scan -- the ceiling reached with no
        textless page found within it -- is the SAME "no textless page"
        answer as a scan that walked the whole file, so it routes down the
        ordinary prose path too, not vision. Pinning the documented choice
        directly, rather than only exercising it as a side effect of the
        ceiling being reached (the two tests above)."""
        settings.FARABUNKER_FEATURES = frozenset({"vision", "media"})
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"not really a pdf at all")

        with patch.object(ingest.readers, "pdf_textless_pages", return_value=([], True)):
            assert ingest._needs_vision_extraction(path, "prose") is False
