"""Unit tests for tools/rag/ingest.py (ADR 0008, ADR 0009).

All external services (the Inference Gateway / Ollama, and the pgvector
store) are mocked -- these tests never embed or call a real LLM. DB-backed
tests run against the real Postgres+pgvector test database that
pytest-django creates.

`settings.DOCUMENTS_DIR` is redirected to a throwaway directory for every
test (see the `_managed_store` autouse fixture below) so ingest's copy into
the managed store (tools.rag.store) never touches the real repo-local
`data/` directory.

Category names used here are deliberately distinct from the four seeded
defaults (Medical, Engineering, Reference & Manuals, Business) so these
tests don't collide with/depend on that data migration.
"""
import hashlib
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from django.conf import settings as dj_settings
from django.core.files.uploadedfile import SimpleUploadedFile

from identity.contracts.principals import SERVICE_PRINCIPAL, Principal
from models.contracts.bindings import ResolvedModel
from models.contracts.queue import QueueUnavailable
from tools.rag import ingest, media, readers, store
from tools.rag.models import Category, Document, DocumentRow, RagSettings
from tools.rag.tests._helpers import make_job_ctx, make_pdf_bytes


@pytest.fixture(autouse=True)
def _managed_store(tmp_path, settings):
    """Redirect the managed document store (ADR 0009) to a throwaway
    directory, distinct from any `tmp_path` a test uses for its own source
    files, so store.store_file()'s copies never land in the real repo."""
    store_root = tmp_path / "_managed_store"
    store_root.mkdir()
    settings.DOCUMENTS_DIR = store_root
    return store_root


# --- inbox setting (no I/O, no DB) ------------------------------------------


class TestInboxSetting:
    def test_inbox_dir_is_under_data_dir(self):
        from django.conf import settings
        assert settings.INGEST_INBOX_DIR == settings.DATA_DIR / "inbox"


class TestFileUploadTempDirSetting:
    def test_temp_dir_is_under_data_dir_and_exists(self):
        from django.conf import settings
        assert settings.FILE_UPLOAD_TEMP_DIR == settings.DATA_DIR / "tmp"
        assert settings.FILE_UPLOAD_TEMP_DIR.is_dir()


# --- doc_type detection (no I/O, no DB) ------------------------------------


class TestDocTypeForMedium:
    """`_doc_type_for_medium` -- the successor to the retired
    `_doc_type_for`, now working off `tools.rag.readers.medium_for`'s own
    result rather than re-deriving a doc type from an extension itself
    (that unsupported-extension error path is `medium_for`'s own to test --
    see `test_readers.py::TestMediumFor` -- not duplicated here)."""

    def test_prose_medium_maps_to_prose(self):
        assert ingest._doc_type_for_medium("prose") == Document.DocType.PROSE

    def test_tabular_medium_maps_to_tabular(self):
        assert ingest._doc_type_for_medium("tabular") == Document.DocType.TABULAR

    @pytest.mark.parametrize("medium", ["video", "audio", "image"])
    def test_media_mediums_raise_naming_the_media_flag_with_flag_off(self, medium, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        with pytest.raises(ValueError, match='enable the "media" feature'):
            ingest._doc_type_for_medium(medium)

    @pytest.mark.parametrize("medium", ["video", "audio", "image"])
    def test_av_and_image_mediums_map_to_prose_once_media_is_enabled(self, medium, settings):
        # T8: "image" joins "video"/"audio" behind the same flag gate.
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        assert ingest._doc_type_for_medium(medium) == Document.DocType.PROSE


class TestMediaTypeFor:
    def test_known_extensions_map_to_a_mime_type(self):
        assert ingest._media_type_for(".pdf") == "application/pdf"
        assert ingest._media_type_for(".csv") == "text/csv"

    def test_av_extensions_map_to_a_mime_type(self):
        # T7: video/audio extensions joined _MEDIA_TYPE_BY_EXT.
        assert ingest._media_type_for(".mp4") == "video/mp4"
        assert ingest._media_type_for(".wav") == "audio/wav"

    def test_image_extensions_map_to_a_mime_type(self):
        # T8: image extensions joined _MEDIA_TYPE_BY_EXT.
        assert ingest._media_type_for(".png") == "image/png"
        assert ingest._media_type_for(".jpg") == "image/jpeg"
        assert ingest._media_type_for(".jpeg") == "image/jpeg"

    def test_unmapped_extension_is_blank(self):
        assert ingest._media_type_for(".xyz") == ""


class TestNeedsVisionExtraction:
    """`_needs_vision_extraction` -- T8's scanned-PDF/image auto-detect.

    ONE CALLER since H28/B-5: `run_ingest_for`, at job start, for the
    actual routing. `_enqueue_ingest_job` used to call it too, for the
    payload's `medium`, and that is the content scan B-5 took off the
    request thread -- its token is pessimistic and extension-only now."""

    def test_image_medium_is_always_true(self, tmp_path, settings):
        # medium_for already told us this is an image -- no probing needed,
        # and no flag gate either (see the function's own docstring).
        # Pinned OFF explicitly to prove that claim -- True even with
        # "media" off, since reaching this call with medium=="image" at
        # all already implies the flag was on further upstream.
        settings.FARABUNKER_FEATURES = frozenset()
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"fake jpeg bytes")
        assert ingest._needs_vision_extraction(path, "image") is True

    def test_tabular_and_prose_non_pdf_are_always_false(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        assert ingest._needs_vision_extraction(tmp_path / "data.csv", "tabular") is False
        assert ingest._needs_vision_extraction(tmp_path / "notes.md", "prose") is False

    def test_text_layer_pdf_is_false_with_flag_on(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))

        assert ingest._needs_vision_extraction(path, "prose") is False

    def test_scanned_pdf_is_true_with_flag_on(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))  # no text layer at all

        assert ingest._needs_vision_extraction(path, "prose") is True

    def test_scanned_pdf_is_false_with_flag_off(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))

        assert ingest._needs_vision_extraction(path, "prose") is False

    def test_needs_vision_extraction_true_for_a_mixed_text_and_scan_pdf(self, tmp_path, settings):
        """W1 decision D2: ANY textless page routes the WHOLE document
        through vision extraction -- before this item, a PDF whose first
        probed pages had real text (this fixture's page 1) answered
        `False` and silently dropped every scanned page after it."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["real text", None]))

        assert ingest._needs_vision_extraction(path, "prose") is True

    def test_needs_vision_extraction_stops_at_the_first_textless_page(self, tmp_path, settings):
        """`limit=1` is passed through to `readers.pdf_textless_pages` --
        pinned by patching the module-level function and asserting the
        kwargs, not by re-deriving the result. `max_examined` joined them
        in the round-3 final wave: this fallback was the last unbounded
        scan in the pipeline (see `tools.rag.tests.test_ingest_scan_
        bounds.TestB5TheCorruptPdfFallbackScanIsBoundedToo` for why it
        runs only for an unparseable PDF)."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))

        # H28 review round 1, finding 4: `pdf_textless_pages` always
        # returns a `(pages, truncated)` pair now, never a bare list.
        with patch.object(ingest.readers, "pdf_textless_pages", return_value=([1], False)) as mock_textless:
            assert ingest._needs_vision_extraction(path, "prose") is True

        mock_textless.assert_called_once_with(
            path, limit=1, max_examined=readers.PDF_SCAN_MAX_PAGES_EXAMINED
        )


@pytest.mark.django_db
class TestCheckDocumentPages:
    """`_check_document_pages` -- T8 review minor 4's `RagSettings.
    max_document_pages` enforcement, the page-count sibling of
    `TestCheckMediaDuration` below. Marked `django_db` at class level
    (matching `TestCheckMediaDurationFlagOn`'s own precedent) since the
    over/under/at-cap tests read `RagSettings.get_solo()`."""

    def test_non_pdf_prose_and_tabular_are_never_checked(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        assert ingest._check_document_pages(tmp_path / "notes.md", "prose") is None
        assert ingest._check_document_pages(tmp_path / "data.csv", "tabular") is None

    def test_image_is_always_one_page_never_over_the_default_cap(self, tmp_path, settings):
        # No probing, no flag gate -- an image is trivially page=1.
        settings.FARABUNKER_FEATURES = frozenset()
        assert ingest._check_document_pages(tmp_path / "photo.jpg", "image") is None

    def test_text_layer_pdf_is_never_checked_with_flag_on(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))

        assert ingest._check_document_pages(path, "prose") is None

    def test_scanned_pdf_stays_uncapped_with_flag_off(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))

        assert ingest._check_document_pages(path, "prose") is None

    def test_scanned_pdf_under_cap_returns_none(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=3)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None]))  # 2 textless pages, cap is 3

        assert ingest._check_document_pages(path, "prose") is None

    def test_scanned_pdf_over_cap_returns_the_rejection_message(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=2)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None, None]))  # 3 textless pages, cap is 2

        message = ingest._check_document_pages(path, "prose")

        assert message is not None
        assert "needs 3 page(s) extracted by a vision model" in message
        assert "over the 2-page document limit" in message
        assert "RagSettings.max_document_pages" in message

    def test_scanned_pdf_exactly_at_cap_returns_none(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=2)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None]))  # exactly 2 textless pages

        assert ingest._check_document_pages(path, "prose") is None

    def test_corrupt_pdf_probe_failure_degrades_to_none_with_a_log(self, tmp_path, settings, caplog):
        """T8 review MAJOR 1's own lesson, applied proactively: a corrupt
        PDF's probe (`readers.pdf_textless_pages`) can itself raise --
        this must degrade to `None` (the cap silently isn't enforced for
        this file), never propagate and block staging."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"%PDF-1.4 not a real pdf, corrupt garbage bytes")

        with caplog.at_level("WARNING"):
            message = ingest._check_document_pages(path, "prose")

        assert message is None
        assert "failed to determine" in caplog.text

    def test_check_document_pages_counts_only_textless_pages(self, tmp_path, settings):
        """W1 decision D3: the cap counts TEXTLESS pages only, not the
        document's total page count -- a mixed PDF with 3 text pages and 2
        textless ones, cap 2, is accepted (2 <= 2), not rejected as a
        5-page document over a 2-page cap."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=2)
        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["t", "t", "t", None, None]))

        assert ingest._check_document_pages(path, "prose") is None

    def test_check_document_pages_scans_only_up_to_the_cap(self, tmp_path, settings):
        """`readers.pdf_textless_pages` is called with `limit=cap + 1` --
        review S4's bounded scan, decidable without walking the tail of a
        huge document."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=2)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None, None]))

        # H28 review round 1, finding 4: always a `(pages, truncated)`
        # pair now. `max_examined=None` in the expected call below --
        # round 1 finding 1's new keyword, `_check_document_pages`'s own
        # default when its caller (here, none) doesn't override it.
        with patch.object(
            ingest.readers, "pdf_textless_pages", return_value=([1, 2, 3], False)
        ) as mock_textless:
            ingest._check_document_pages(path, "prose")

        mock_textless.assert_called_once_with(path, limit=3, max_examined=None)

    def test_check_document_pages_scans_the_pdf_once(self, tmp_path, settings):
        """W1 review D3: the retired shape called `_needs_vision_extraction`
        (a probe) and then `pdf_page_count` (a second pass) inside the same
        `try` -- two `pypdf` passes over one file. The restructured
        function computes the textless list exactly ONCE."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=5)
        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["t", None]))

        # H28 review round 1, finding 4: always a `(pages, truncated)`
        # pair now.
        with patch.object(ingest.readers, "pdf_textless_pages", return_value=([2], False)) as mock_textless:
            ingest._check_document_pages(path, "prose")

        assert mock_textless.call_count == 1

    def test_check_document_pages_reads_rag_settings_once_for_a_scanned_pdf(self, tmp_path, settings):
        """W1 review MINOR 7: `RagSettings.get_solo()` used to be read
        twice per staged PDF -- once to derive `cap` for the `limit=cap + 1`
        scan, and again, unconditionally, just to re-derive the same `cap`
        for the final over-the-cap comparison. `get_solo()` is a real
        `get_or_create` query, not a cached lookup, so this is now read
        exactly once."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=5)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None]))

        with patch.object(ingest.RagSettings, "get_solo", wraps=RagSettings.get_solo) as mock_get_solo:
            ingest._check_document_pages(path, "prose")

        assert mock_get_solo.call_count == 1

    def test_check_document_pages_reads_rag_settings_once_for_an_image(self, tmp_path, settings):
        RagSettings.objects.create(pk=1, max_document_pages=5)

        with patch.object(ingest.RagSettings, "get_solo", wraps=RagSettings.get_solo) as mock_get_solo:
            ingest._check_document_pages(tmp_path / "photo.jpg", "image")

        assert mock_get_solo.call_count == 1

    def test_check_document_pages_never_reads_rag_settings_for_non_capped_media(self, tmp_path, settings):
        """A plain prose file or a tabular one is never subject to this
        cap at all -- it must not pay even one `get_solo()` query."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})

        with patch.object(ingest.RagSettings, "get_solo", wraps=RagSettings.get_solo) as mock_get_solo:
            ingest._check_document_pages(tmp_path / "notes.md", "prose")
            ingest._check_document_pages(tmp_path / "data.csv", "tabular")

        assert mock_get_solo.call_count == 0


class TestCheckMediaDuration:
    """`_check_media_duration`: T4's stub always returned `None`
    regardless of medium -- still true today with the "media" feature off
    (the default in tests, `config/settings.py`) or for a non-AV medium
    (prose/tabular/image) regardless of the flag. T7 wires REAL enforcement
    for video/audio once "media" is enabled -- see the flag-on class below."""

    @pytest.mark.parametrize("medium", ["prose", "tabular", "video", "audio", "image"])
    def test_always_returns_none_with_flag_off(self, tmp_path, medium, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        path = tmp_path / "whatever.bin"
        path.write_bytes(b"x")
        assert ingest._check_media_duration(path, medium) is None

    def test_nonexistent_path_still_returns_none_with_flag_off(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        # No probing happens at all with the flag off -- a path that
        # doesn't even exist is still a no-op, not an error.
        assert ingest._check_media_duration(tmp_path / "gone.mp4", "video") is None

    @pytest.mark.parametrize("medium", ["prose", "tabular", "image"])
    def test_non_av_mediums_still_return_none_with_flag_on(self, tmp_path, medium, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "whatever.bin"
        path.write_bytes(b"x")
        assert ingest._check_media_duration(path, medium) is None


@pytest.mark.django_db
class TestCheckMediaDurationFlagOn:
    """T7: real enforcement for `medium in ("video", "audio")` once "media"
    is enabled -- `transcode.probe_duration` is mocked at the bound name
    (`tools.rag.ingest.transcode`), matching this file's own
    `@patch("tools.rag.ingest.rag_index")` convention for a bound
    submodule import."""

    @pytest.mark.parametrize("medium", ["video", "audio"])
    @patch("tools.rag.ingest.transcode")
    def test_under_cap_returns_none(self, mock_transcode, medium, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_transcode.probe_duration.return_value = 60.0
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"x")

        assert ingest._check_media_duration(path, medium) is None
        mock_transcode.probe_duration.assert_called_once_with(path)

    @patch("tools.rag.ingest.transcode")
    def test_over_cap_returns_the_rejection_message(self, mock_transcode, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_transcode.probe_duration.return_value = 99999.0
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"x")

        message = ingest._check_media_duration(path, "video")

        assert message is not None
        assert "over the 7200s media duration limit" in message
        assert "RagSettings.max_media_seconds" in message

    @patch("tools.rag.ingest.transcode")
    def test_exactly_at_cap_returns_none(self, mock_transcode, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_transcode.probe_duration.return_value = float(RagSettings.MAX_MEDIA_SECONDS_DEFAULT)
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"x")

        assert ingest._check_media_duration(path, "audio") is None

    @patch("tools.rag.ingest.transcode")
    def test_missing_ffprobe_degrades_to_none_with_a_log(self, mock_transcode, tmp_path, settings, caplog):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_transcode.ffprobe_available.return_value = False
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"x")

        with caplog.at_level("WARNING"):
            message = ingest._check_media_duration(path, "video")

        assert message is None
        mock_transcode.probe_duration.assert_not_called()
        assert "ffprobe is not installed" in caplog.text


# --- hashing -----------------------------------------------------------


class TestSha256:
    def test_hash_is_stable_and_correct(self, tmp_path):
        path = tmp_path / "sample.txt"
        content = b"hello farabunker\n" * 100
        path.write_bytes(content)

        expected = hashlib.sha256(content).hexdigest()
        assert ingest._sha256(path) == expected
        # stable across repeated calls
        assert ingest._sha256(path) == ingest._sha256(path)

    def test_hash_changes_when_content_changes(self, tmp_path):
        path = tmp_path / "sample.txt"
        path.write_bytes(b"version one")
        hash1 = ingest._sha256(path)

        path.write_bytes(b"version two")
        hash2 = ingest._sha256(path)

        assert hash1 != hash2


# --- category_from_subfolder (no I/O, no DB) --------------------------------


class TestCategoryFromSubfolder:
    def test_immediate_subfolder_is_the_category(self, tmp_path):
        root = tmp_path / "watch"
        file_path = root / "medical" / "x.pdf"
        assert ingest.category_from_subfolder(file_path, root) == "medical"

    def test_nested_subfolder_uses_only_first_segment(self, tmp_path):
        root = tmp_path / "watch"
        file_path = root / "medical" / "sub" / "x.pdf"
        assert ingest.category_from_subfolder(file_path, root) == "medical"

    def test_file_directly_in_root_has_no_category(self, tmp_path):
        root = tmp_path / "watch"
        file_path = root / "x.pdf"
        assert ingest.category_from_subfolder(file_path, root) is None

    def test_file_outside_root_has_no_category(self, tmp_path):
        root = tmp_path / "watch"
        other = tmp_path / "elsewhere" / "x.pdf"
        assert ingest.category_from_subfolder(other, root) is None


# --- managed store copy (ADR 0009) -----------------------------------------


@pytest.mark.django_db
class TestManagedStoreCopy:
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_file_is_copied_into_store_and_original_untouched(self, mock_gateway, mock_rag_index, tmp_path):
        mock_rag_index.get_index.return_value = MagicMock()

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        original = source_dir / "notes.md"
        original.write_text("original content")

        doc = ingest.ingest_path(str(original))

        assert doc.original_path == str(original.resolve())
        # Stored under DOCUMENTS_DIR/<id>/, not the original location.
        stored_path = Path(doc.source_path)
        assert stored_path.parent == dj_settings.DOCUMENTS_DIR / str(doc.id)
        assert stored_path.exists()
        assert stored_path.read_text() == "original content"
        assert stored_path != original.resolve()

        # Original file is untouched (copy, not move).
        assert original.exists()
        assert original.read_text() == "original content"


# --- category assignment (ADR 0009) -----------------------------------------


@pytest.mark.django_db
class TestCategoryAssignment:
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_explicit_category_creates_and_assigns(self, mock_gateway, mock_rag_index, tmp_path):
        mock_rag_index.get_index.return_value = MagicMock()

        path = tmp_path / "notes.md"
        path.write_text("some prose")

        doc = ingest.ingest_path(str(path), category="Wave2Alpha")

        assert doc.category is not None
        assert doc.category.name == "Wave2Alpha"
        assert Category.objects.filter(name="Wave2Alpha").count() == 1

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_category_reused_case_insensitively(self, mock_gateway, mock_rag_index, tmp_path):
        from tools.rag.models import Category
        mock_rag_index.get_index.return_value = MagicMock()
        seeded = Category.objects.create(name="Fieldwork")

        path = tmp_path / "notes.md"
        path.write_text("some prose")
        doc = ingest.ingest_path(str(path), category="fieldwork")

        assert doc.category_id == seeded.id
        assert Category.objects.filter(name__iexact="fieldwork").count() == 1

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_explicit_category_reuses_existing_category(self, mock_gateway, mock_rag_index, tmp_path):
        mock_rag_index.get_index.return_value = MagicMock()
        existing = Category.objects.create(name="Wave2Beta")

        path = tmp_path / "notes.md"
        path.write_text("some prose")

        doc = ingest.ingest_path(str(path), category="Wave2Beta")

        assert doc.category_id == existing.id
        assert Category.objects.filter(name="Wave2Beta").count() == 1

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_no_category_leaves_document_uncategorized(self, mock_gateway, mock_rag_index, tmp_path):
        mock_rag_index.get_index.return_value = MagicMock()

        path = tmp_path / "notes.md"
        path.write_text("some prose")

        doc = ingest.ingest_path(str(path))

        assert doc.category is None

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_blank_category_is_ignored(self, mock_gateway, mock_rag_index, tmp_path):
        mock_rag_index.get_index.return_value = MagicMock()

        path = tmp_path / "notes.md"
        path.write_text("some prose")

        doc = ingest.ingest_path(str(path), category="   ")

        assert doc.category is None


# --- prose ingest branch + chunk metadata -----------------------------------


@pytest.mark.django_db
class TestIngestProse:
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_prose_document_created_and_nodes_indexed_with_metadata(
        self, mock_gateway, mock_rag_index, tmp_path
    ):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text("# Title\n\nSome prose content to be chunked and embedded.\n")

        doc = ingest.ingest_path(str(path))

        assert doc.doc_type == Document.DocType.PROSE
        assert doc.title == "notes.md"

        mock_gateway.get_embed_model.assert_called_once_with("rag.embed")
        mock_rag_index.get_index.assert_called_once_with(
            None, embed_model=mock_gateway.get_embed_model.return_value
        )
        mock_index.insert_nodes.assert_called_once()

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            assert node.metadata["file_id"] == str(doc.id)
            assert node.metadata["file_name"] == "notes.md"
            # source_path metadata now points at the *stored* copy, not the
            # original location ingest was given (ADR 0009).
            assert node.metadata["source_path"] == doc.source_path
            assert node.metadata["source_path"] != str(path.resolve())

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_chunk_metadata_category_is_lowercased(self, mock_gateway, mock_rag_index, tmp_path):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text("Some prose content.")

        ingest.ingest_path(str(path), category="Field Guide")

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            assert node.metadata["category"] == "field guide"

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_chunk_metadata_carries_explicit_category(self, mock_gateway, mock_rag_index, tmp_path):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text("Some prose content.")

        ingest.ingest_path(str(path), category="Wave2Gamma")

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            assert node.metadata["category"] == "wave2gamma"

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_chunk_metadata_uses_uncategorized_literal_when_no_category(
        self, mock_gateway, mock_rag_index, tmp_path
    ):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text("Some prose content.")

        ingest.ingest_path(str(path))

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            assert node.metadata["category"] == "uncategorized"

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_pollution_fix_applies_to_plain_prose_documents_too(self, mock_gateway, mock_rag_index, tmp_path):
        """T7's pollution fix (`tools.rag.media.apply_chunk_metadata_exclusions`)
        is wired into `_ingest_prose` for EVERY document, not just media --
        proven here at the node level, after the real `SentenceSplitter`
        pass `_ingest_prose` itself runs: the embed/LLM-visible content
        carries none of the excluded metadata as "key: value" lines."""
        from llama_index.core.schema import MetadataMode

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text(
            "Some real prose content, long enough that the splitter treats it as one node "
            "worth keeping around for this pollution-fix test to check."
        )

        doc = ingest.ingest_path(str(path), category="Field Guide")

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            for key in ("file_id", "file_name", "source_path", "category"):
                assert key in node.excluded_embed_metadata_keys
                assert key in node.excluded_llm_metadata_keys
            embed_text = node.get_content(metadata_mode=MetadataMode.EMBED)
            llm_text = node.get_content(metadata_mode=MetadataMode.LLM)
            for needle in (str(doc.id), "notes.md", "field guide", doc.source_path):
                assert needle not in embed_text
                assert needle not in llm_text

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_pollution_fix_covers_the_page_key_from_a_text_layer_pdf(
        self, mock_gateway, mock_rag_index, tmp_path
    ):
        """T10 metadata-exclusion follow-through, path 1 of 3 (the prose
        reader): `tools.rag.readers._read_pdf` writes `metadata={"page":
        i + 1}` onto each per-page `LlamaDocument` -- proven here that
        `page` specifically (not just file_id/file_name/source_path/
        category, `test_pollution_fix_applies_to_plain_prose_documents_
        too`'s own coverage) ends up excluded at the NODE level too, after
        a real `_ingest_prose` run through the real `SentenceSplitter`."""
        from llama_index.core.schema import MetadataMode

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "report.pdf"
        path.write_bytes(
            make_pdf_bytes(
                [
                    "The first sheet holds real, extractable prose long enough for this test.",
                    "The second sheet also holds real, extractable prose for this same test.",
                ]
            )
        )

        ingest.ingest_path(str(path))

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            assert "page" in node.excluded_embed_metadata_keys
            assert "page" in node.excluded_llm_metadata_keys
            # "page: N" is how an UNEXCLUDED metadata key would render
            # (LlamaIndex's default "{key}: {value}" template) -- absent
            # here proves the exclusion actually took effect on the
            # rendered content, not just on the (separately checked)
            # exclusion lists above. The source text itself deliberately
            # avoids the word "page" so this check can't false-negative.
            embed_text = node.get_content(metadata_mode=MetadataMode.EMBED)
            llm_text = node.get_content(metadata_mode=MetadataMode.LLM)
            assert "page:" not in embed_text
            assert "page:" not in llm_text

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_idempotent_embed_deletes_before_inserting_every_call(self, mock_gateway, mock_rag_index, tmp_path):
        """T7 review M3b: `_ingest_prose` deletes this Document's existing
        chunks BEFORE inserting the new ones on EVERY call -- closes a
        pre-existing hole where re-running ingest against an already-
        embedded Document (`enqueue_reingest`, which does not go through
        `_delete_existing_data`) inserted a second copy instead of
        replacing the first. Proven two ways: the delete-then-insert order
        (a shared recorder pins it, matching `test_services.py`'s own
        `test_deletes_existing_chunks_before_reingesting` convention), and
        a real double-run leaving exactly ONE `insert_nodes` call's worth
        of chunks live (never two)."""
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        calls: list[tuple] = []
        mock_rag_index.delete_chunks_for_document.side_effect = (
            lambda doc_id, vector_store=None: calls.append(("delete", doc_id))
        )
        mock_index.insert_nodes.side_effect = lambda nodes: calls.append(("insert", len(nodes)))

        path = tmp_path / "notes.md"
        path.write_text("Some prose content, ingested once, then reingested to prove idempotency.")

        doc = ingest.ingest_path(str(path))

        assert calls == [("delete", doc.id), ("insert", calls[-1][1])]

        # A second run against the SAME (unchanged) stored copy -- the
        # `enqueue_reingest` shape, re-running the RUN half directly rather
        # than through `stage_document`'s own re-stage dedup (which would
        # otherwise short-circuit on the unchanged hash before ever
        # reaching `_ingest_prose` again).
        calls.clear()
        ingest.run_ingest_or_fail(doc, doc.file_hash)

        assert calls == [("delete", doc.id), ("insert", calls[-1][1])]
        assert mock_index.insert_nodes.call_count == 2
        assert mock_rag_index.delete_chunks_for_document.call_count == 2


# --- Settings-sentinel regression guard (T5) --------------------------
#
# The ingest twin of test_retrieval.py's `TestSettingsNeverRead`: proves the
# prose-ingest path never reads the global `llama_index.core.Settings`
# either -- see that module's test for the full rationale (a mutable global
# is a race the moment more than one request/job embeds concurrently with a
# different resolved model).


class _RaisingSettingsSentinel:
    """Stands in for `llama_index.core.Settings` -- reading `.llm` or
    `.embed_model` raises immediately, proving neither was ever touched."""

    @property
    def llm(self):
        raise AssertionError("global Settings.llm must never be read on the execution path")

    @property
    def embed_model(self):
        raise AssertionError("global Settings.embed_model must never be read on the execution path")


@pytest.mark.django_db
class TestSettingsNeverRead:
    @patch("llama_index.core.Settings", new=_RaisingSettingsSentinel())
    @patch("models.contracts.gateway.get_engine")
    @patch("tools.rag.ingest.rag_index")
    def test_prose_ingest_never_reads_global_settings(
        self, mock_rag_index, mock_get_engine, tmp_path, settings
    ):
        """`rag_index`/`get_engine` are mocked (index/HTTP-adapter
        boundaries, per house convention), so `gateway.get_embed_model`
        builds via a fake engine rather than touching a real network -- with
        that stood in, ingest completing without the sentinel's
        `AssertionError` firing proves it never reads `Settings` itself, and
        the explicit `embed_model=` assertion proves why it doesn't have
        to."""
        settings.EMBED_MODEL = "nomic-embed-text"
        settings.EMBED_DIM = 768
        settings.OLLAMA_BASE_URL = "http://localhost:11434"
        from models.registry.models import RoleBinding

        RoleBinding.objects.all().delete()

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index
        mock_engine = MagicMock()
        mock_engine.build_embedder.return_value = MagicMock(name="fake-embedder")
        mock_get_engine.return_value = mock_engine

        path = tmp_path / "notes.md"
        path.write_text("Some prose content.")

        doc = ingest.ingest_path(str(path))

        assert doc.doc_type == Document.DocType.PROSE
        mock_index.insert_nodes.assert_called_once()
        _, kwargs = mock_rag_index.get_index.call_args
        assert kwargs["embed_model"] is mock_engine.build_embedder.return_value


# --- tabular ingest branch -----------------------------------------------


@pytest.mark.django_db
class TestIngestTabular:
    def _csv_path(self, tmp_path, rows=None):
        rows = rows or ["1,north,10", "2,south,20"]
        path = tmp_path / "sales.csv"
        path.write_text("id,region,amount\n" + "\n".join(rows) + "\n")
        return path

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_tabular_document_created_with_schema_and_rows(self, mock_gateway, mock_rag_index, tmp_path):
        path = self._csv_path(tmp_path)

        doc = ingest.ingest_path(str(path))

        assert doc.doc_type == Document.DocType.TABULAR
        assert doc.title == "sales.csv"
        assert set(doc.tabular_schema.keys()) == {"id", "region", "amount"}

        rows = list(DocumentRow.objects.filter(document=doc).order_by("row_index"))
        assert len(rows) == 2
        assert rows[0].row_index == 0
        assert rows[0].data == {"id": 1, "region": "north", "amount": 10}
        assert rows[1].data == {"id": 2, "region": "south", "amount": 20}

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_tabular_ingest_never_touches_vector_index(self, mock_gateway, mock_rag_index, tmp_path):
        path = self._csv_path(tmp_path)

        ingest.ingest_path(str(path))

        mock_gateway.get_embed_model.assert_not_called()
        mock_rag_index.get_index.assert_not_called()


# --- idempotent re-ingest ---------------------------------------------------


@pytest.mark.django_db
class TestIdempotentReingest:
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_unchanged_file_is_skipped_on_second_ingest(self, mock_gateway, mock_rag_index, tmp_path):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "doc.txt"
        path.write_text("stable content")

        doc1 = ingest.ingest_path(str(path))
        assert mock_index.insert_nodes.call_count == 1

        doc2 = ingest.ingest_path(str(path))

        assert doc2.id == doc1.id
        assert Document.objects.count() == 1
        # No re-insert happened for the unchanged file.
        assert mock_index.insert_nodes.call_count == 1

    @patch("tools.rag.ingest.store")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_changed_file_deletes_prior_data_and_reingests(
        self, mock_gateway, mock_rag_index, mock_store, tmp_path
    ):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index
        # store.store_file must still actually copy the file for the ingest
        # pipeline (read_prose_documents etc.) to have something to read.
        mock_store.store_file.side_effect = lambda src, doc_id: src
        # C-18: _source_documents now resolves its sidecar through
        # store.sidecar_path_for_dir(path.parent) -- delegate to the real
        # (side-effect-free) computation rather than let the wholesale
        # mock return a truthy MagicMock and misroute prose ingest through
        # the sidecar branch.
        mock_store.sidecar_path_for_dir.side_effect = lambda d: Path(d) / "extract.json"

        path = tmp_path / "doc.txt"
        path.write_text("version one")
        doc1 = ingest.ingest_path(str(path))
        first_hash = doc1.file_hash

        # Changing the content changes the hash and should trigger a
        # delete-then-reingest of the same Document row.
        path.write_text("version two, totally different content")
        doc2 = ingest.ingest_path(str(path))

        assert doc2.id == doc1.id
        assert doc2.file_hash != first_hash
        assert Document.objects.count() == 1

        # Re-ingest cleanup delegates vector-chunk teardown to the shared
        # helper (index.delete_chunks_for_document), keyed on the doc id --
        # THREE calls now (T7 review M3b), not one: `_ingest_prose`'s own
        # idempotent-embed guard (`doc.id, vector_store=None`) now runs on
        # EVERY `_ingest_prose` call (both `ingest_path` calls above, not
        # just a changed re-stage), plus `_delete_existing_data`'s own
        # re-stage cleanup (positional-only `doc.id`) on the second one.
        calls = mock_rag_index.delete_chunks_for_document.call_args_list
        assert calls.count(call(doc1.id, vector_store=None)) == 2
        assert calls.count(call(doc1.id)) == 1
        assert len(calls) == 3
        mock_store.remove_document_files.assert_called_once_with(doc1.id)
        assert mock_index.insert_nodes.call_count == 2

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_changed_tabular_file_replaces_rows(self, mock_gateway, mock_rag_index, tmp_path):
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n2,20\n")
        doc1 = ingest.ingest_path(str(path))
        assert DocumentRow.objects.filter(document=doc1).count() == 2

        path.write_text("id,amount\n1,10\n2,20\n3,30\n")
        doc2 = ingest.ingest_path(str(path))

        assert doc2.id == doc1.id
        rows = list(DocumentRow.objects.filter(document=doc2).order_by("row_index"))
        assert len(rows) == 3
        assert rows[-1].data == {"id": 3, "amount": 30}

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_reingest_with_new_category_reassigns_it(self, mock_gateway, mock_rag_index, tmp_path):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "doc.txt"
        path.write_text("version one")
        doc1 = ingest.ingest_path(str(path), category="Wave2Delta")
        assert doc1.category.name == "Wave2Delta"

        path.write_text("version two, different content")
        doc2 = ingest.ingest_path(str(path), category="Wave2Epsilon")

        assert doc2.id == doc1.id
        assert doc2.category.name == "Wave2Epsilon"


# --- stage_document (T2: stage/run split) -----------------------------------


@pytest.mark.django_db
class TestStageDocument:
    def test_new_file_is_staged_pending_with_media_type(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("hello")

        doc, changed = ingest.stage_document(str(path), move=False)

        assert changed is True
        assert doc.status == Document.Status.PENDING
        assert doc.status_detail == ""
        assert doc.media_type == "text/markdown"
        assert doc.doc_type == Document.DocType.PROSE
        assert Path(doc.source_path).read_text() == "hello"
        # copy semantics (move=False): original untouched.
        assert path.exists()

    def test_move_semantics_remove_the_original(self, tmp_path):
        source_dir = tmp_path / "inbox"
        source_dir.mkdir()
        path = source_dir / "notes.md"
        path.write_text("hello")

        doc, changed = ingest.stage_document(str(path), move=True)

        assert changed is True
        assert not path.exists()
        assert Path(doc.source_path).read_text() == "hello"

    def test_unchanged_hash_returns_existing_doc_and_false(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("stable content")

        doc1, changed1 = ingest.stage_document(str(path), move=False)
        assert changed1 is True

        doc2, changed2 = ingest.stage_document(str(path), move=False)
        assert changed2 is False
        assert doc2.id == doc1.id

    def test_changed_content_restages_pending_and_clears_prior_failure(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("version one")
        doc1, _ = ingest.stage_document(str(path), move=False)
        doc1.status = Document.Status.FAILED
        doc1.status_detail = "boom"
        doc1.save(update_fields=["status", "status_detail"])

        path.write_text("version two, totally different content")
        doc2, changed = ingest.stage_document(str(path), move=False)

        assert changed is True
        assert doc2.id == doc1.id
        assert doc2.status == Document.Status.PENDING
        assert doc2.status_detail == ""

    def test_media_extension_raises_naming_the_media_flag_with_flag_off(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")

        with pytest.raises(ValueError, match='enable the "media" feature'):
            ingest.stage_document(str(path), move=False)

        assert Document.objects.count() == 0

    @patch("tools.rag.ingest.transcode")
    def test_av_extension_stages_as_prose_once_media_is_enabled(self, mock_transcode, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_transcode.probe_duration.return_value = 60.0
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")

        doc, changed = ingest.stage_document(str(path), move=False)

        assert changed is True
        assert doc.doc_type == Document.DocType.PROSE
        assert doc.media_type == "video/mp4"
        assert doc.status == Document.Status.PENDING

    @patch("tools.rag.ingest.transcode")
    def test_av_extension_over_duration_cap_is_rejected_at_stage(self, mock_transcode, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_transcode.probe_duration.return_value = 99999.0
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")

        with pytest.raises(ValueError, match="over the 7200s media duration limit"):
            ingest.stage_document(str(path), move=False)

        assert Document.objects.count() == 0

    def test_scanned_pdf_over_page_cap_is_rejected_at_job_start(self, tmp_path, settings):
        """RENAMED from `..._at_stage` (H28, round-3 hardening, B-5): the
        page-cap verdict moved off `stage_document` to the top of a
        `rag.ingest` job's own run (`run_ingest_for`) -- staging this file
        now always succeeds, and the SAME refusal (same exception type,
        same operator-facing message) is asserted after the job runs,
        via the same `run_ingest_or_fail` wrapper the queue handler uses."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=2)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None, None]))  # 3 pages, cap is 2

        doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is True
        assert Document.objects.count() == 1

        with pytest.raises(ingest.DocumentPageCapExceededError, match="over the 2-page document limit"):
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert "over the 2-page document limit" in doc.status_detail

    def test_scanned_pdf_under_page_cap_stages_normally(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        RagSettings.objects.create(pk=1, max_document_pages=5)
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None]))  # 2 pages, cap is 5

        doc, changed = ingest.stage_document(str(path), move=False)

        assert changed is True
        assert doc.status == Document.Status.PENDING

    def test_unsupported_extension_raises(self, tmp_path):
        path = tmp_path / "evil.exe"
        path.write_bytes(b"MZ")

        with pytest.raises(ValueError, match="Unsupported file extension"):
            ingest.stage_document(str(path), move=False)


# --- _source_documents (T2: sidecar-aware dispatcher) ------------------------


class TestSourceDocuments:
    def test_no_sidecar_falls_back_to_read_prose_documents(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("hello world")
        doc = MagicMock(id=1)

        docs = ingest._source_documents(doc, path)

        assert len(docs) == 1
        assert docs[0].text == "hello world"

    def test_sidecar_present_dispatches_through_documents_from_extract(self, tmp_path):
        # T7: the sidecar branch is now real -- routes through
        # tools.rag.media.documents_from_extract instead of raising.
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        sidecar = {
            "version": 1,
            "method": "whisper",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"start": 0.0, "end": 1.5, "text": "hello world"}],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        docs = ingest._source_documents(doc, path)

        assert len(docs) == 1
        assert docs[0].text == "hello world"
        assert docs[0].metadata["start_seconds"] == 0.0
        assert docs[0].metadata["end_seconds"] == 1.5

    def test_vision_sidecar_present_dispatches_page_keyed_documents(self, tmp_path):
        # T8: a scanned-PDF/image sidecar routes through the same
        # documents_from_extract dispatch, page-keyed instead of
        # timestamp-keyed.
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"fake pdf bytes")
        sidecar = {
            "version": 1,
            "method": "vision",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 1, "text": "hello page one"}],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        docs = ingest._source_documents(doc, path)

        assert len(docs) == 1
        assert docs[0].text == "hello page one"
        assert docs[0].metadata["page"] == 1

    def test_incomplete_sidecar_raises_instead_of_indexing_a_partial_transcript(self, tmp_path):
        """T7 review M4a: a sidecar with `produced_at` unset (still
        in-progress, or left behind by a run that died before finishing)
        must never be silently indexed as if it were the whole document --
        and must NOT fall through to `read_prose_documents(path)` either
        (which would raise its own, less specific "Unsupported prose
        extension" error for a raw video file)."""
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        sidecar = {
            "version": 1,
            "method": "whisper",
            "produced_at": None,
            "segments": [{"start": 0.0, "end": 1.5, "text": "hello world"}],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        with pytest.raises(RuntimeError, match="incomplete extraction sidecar"):
            ingest._source_documents(doc, path)

    def test_missing_produced_at_key_also_raises(self, tmp_path):
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        (tmp_path / "extract.json").write_text(json.dumps({"segments": []}))
        doc = MagicMock(id=1)

        with pytest.raises(RuntimeError, match="incomplete extraction sidecar"):
            ingest._source_documents(doc, path)

    def test_source_documents_merges_text_and_extracted_pages_in_page_order(self, tmp_path):
        """W1, D3: a mixed PDF's sidecar carries only the TEXTLESS pages --
        `_source_documents` reads the document's own text layer too and
        merges the two, in page order, keyed on the sidecar's content for
        any page it claims."""
        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["page one text", None, "page three text"]))
        sidecar = {
            "version": 1,
            "method": "vision",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 2, "text": "vision-extracted page two"}],
            "rasterized_pages": [2],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        docs = ingest._source_documents(doc, path)

        assert [d.metadata["page"] for d in docs] == [1, 2, 3]
        by_page = {d.metadata["page"]: d.text for d in docs}
        assert by_page[2] == "vision-extracted page two"
        assert by_page[1] == "page one text"
        assert by_page[3] == "page three text"

    def test_source_documents_does_not_merge_a_pre_w1_sidecar(self, tmp_path):
        """A sidecar written before W1 has no `rasterized_pages` key at all
        -- `isinstance(rasterized, list)` is `False`, so this must behave
        exactly like today: only the sidecar's own extracted pages,
        `read_prose_documents` never even called."""
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"not a real pdf -- read_prose_documents must never be called on it")
        sidecar = {
            "version": 1,
            "method": "vision",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 1, "text": "hello page one"}],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        with patch("tools.rag.ingest.read_prose_documents") as mock_read_prose:
            docs = ingest._source_documents(doc, path)

        mock_read_prose.assert_not_called()
        assert len(docs) == 1
        assert docs[0].text == "hello page one"

    def test_source_documents_does_not_merge_for_a_video_sidecar(self, tmp_path):
        """The `.pdf` suffix gate: a `rasterized_pages` key (however it got
        there) on a non-`.pdf` sidecar must never trigger the merge --
        `read_prose_documents` cannot even parse raw video bytes."""
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        sidecar = {
            "version": 1,
            "method": "vision",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 1, "text": "hello page one"}],
            "rasterized_pages": [1],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        with patch("tools.rag.ingest.read_prose_documents") as mock_read_prose:
            docs = ingest._source_documents(doc, path)

        mock_read_prose.assert_not_called()
        assert len(docs) == 1
        assert docs[0].text == "hello page one"

    def test_source_documents_ignores_a_rasterized_page_also_present_in_the_text_layer(self, tmp_path):
        """Belt-and-braces `skip` filter: a page the sidecar claims it
        rasterized is dropped from the text layer even if that page
        happens to carry real text too (should never happen by
        construction, but a duplicate page would double-index)."""
        path = tmp_path / "two-page.pdf"
        path.write_bytes(make_pdf_bytes(["page one text", "page two text"]))
        sidecar = {
            "version": 1,
            "method": "vision",
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 1, "text": "vision-extracted page one"}],
            "rasterized_pages": [1],
        }
        (tmp_path / "extract.json").write_text(json.dumps(sidecar))
        doc = MagicMock(id=1)

        docs = ingest._source_documents(doc, path)

        assert [d.metadata["page"] for d in docs] == [1, 2]
        by_page = {d.metadata["page"]: d.text for d in docs}
        assert by_page[1] == "vision-extracted page one"
        assert by_page[2] == "page two text"


# --- run_ingest_for (T2: stage/run split) -----------------------------------


@pytest.mark.django_db
class TestRunIngestFor:
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_prose_happy_path_sets_ready(self, mock_gateway, mock_rag_index, tmp_path):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text("some prose content")
        doc, _ = ingest.stage_document(str(path), move=False)
        assert doc.status == Document.Status.PENDING

        result = ingest.run_ingest_for(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert doc.status_detail == ""
        assert result == {"document_id": doc.id, "title": doc.title}
        mock_index.insert_nodes.assert_called_once()

    def test_tabular_happy_path_sets_ready(self, tmp_path):
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n")
        doc, _ = ingest.stage_document(str(path), move=False)

        ingest.run_ingest_for(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert DocumentRow.objects.filter(document=doc).count() == 1

    def test_sets_processing_before_running(self, tmp_path):
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n")
        doc, _ = ingest.stage_document(str(path), move=False)

        seen_status = {}
        original_ingest_tabular = ingest._ingest_tabular

        def _spy(doc_arg, path_arg):
            seen_status["status"] = doc_arg.status
            return original_ingest_tabular(doc_arg, path_arg)

        with patch("tools.rag.ingest._ingest_tabular", side_effect=_spy):
            ingest.run_ingest_for(doc, doc.file_hash)

        assert seen_status["status"] == Document.Status.PROCESSING

    def test_sha_mismatch_raises_and_never_writes_failed(self, tmp_path):
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(RuntimeError, match="no longer matches"):
            ingest.run_ingest_for(doc, "0" * 64)

        doc.refresh_from_db()
        # run_ingest_for itself never writes FAILED -- only the queue
        # handler (tools.rag.jobs.run_ingest) does; see its docstring.
        assert doc.status == Document.Status.PROCESSING

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_exception_during_ingest_propagates_without_writing_failed(
        self, mock_gateway, mock_rag_index, tmp_path
    ):
        mock_rag_index.get_index.side_effect = RuntimeError("index is down")

        path = tmp_path / "notes.md"
        path.write_text("some content")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(RuntimeError, match="index is down"):
            ingest.run_ingest_for(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.PROCESSING


# --- run_ingest_for text-only fallback (W1 decision D4) ---------------------


@pytest.mark.django_db
class TestRunIngestForTextOnlyFallback:
    """A mixed PDF must never fail outright for want of a vision model
    (W1 decision D4): `run_ingest_for` catches `media.ModelRoleUnavailable`
    -- raised by `media.extract_to_sidecar` -- for a `.pdf` ONLY, and falls
    back to ingesting the document's own text layer alone."""

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    @patch("tools.rag.ingest.media.extract_to_sidecar")
    def test_run_ingest_for_falls_back_to_text_only_when_the_extract_role_is_unavailable(
        self, mock_extract, mock_gateway, mock_rag_index, tmp_path, settings
    ):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_extract.side_effect = media.ModelRoleUnavailable("no rag.extract bound")
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["real text", None]))
        doc, _ = ingest.stage_document(str(path), move=False)

        result = ingest.run_ingest_for(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert doc.status_detail == ingest._TEXT_ONLY_FALLBACK_NOTE
        assert result == {"document_id": doc.id, "title": doc.title}
        mock_index.insert_nodes.assert_called_once()

    @patch("tools.rag.ingest.media.extract_to_sidecar")
    def test_run_ingest_for_still_fails_a_fully_scanned_pdf_with_no_vision_model(
        self, mock_extract, tmp_path, settings
    ):
        """No text layer to fall back TO -- the original
        `ModelRoleUnavailable` propagates, same as today's honest
        failure."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        original_exc = media.ModelRoleUnavailable("no rag.extract bound")
        mock_extract.side_effect = original_exc

        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None, None]))
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(media.ModelRoleUnavailable) as exc_info:
            ingest.run_ingest_for(doc, doc.file_hash)

        assert exc_info.value is original_exc
        doc.refresh_from_db()
        # run_ingest_for itself never writes FAILED -- see its own docstring.
        assert doc.status == Document.Status.PROCESSING

    @patch("tools.rag.ingest.media.extract_to_sidecar")
    def test_run_ingest_for_still_fails_an_image_with_no_vision_model(self, mock_extract, tmp_path, settings):
        """W1 review N1: the `.pdf` gate. `_needs_vision_extraction`
        returns `True` unconditionally for an image, so an ungated fallback
        would swallow an image's role failure too and then hit
        `_source_documents` -> `read_prose_documents("x.png")`, which
        raises its OWN, less honest "Unsupported prose extension"
        `ValueError` -- turning today's honest `model_unavailable_message`
        into a FAILED row with a parser error instead. Pin the ORIGINAL,
        honest message survives."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_extract.side_effect = media.ModelRoleUnavailable("rag.extract isn't set up yet")

        path = tmp_path / "photo.png"
        path.write_bytes(b"fake png bytes")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(media.ModelRoleUnavailable, match="rag.extract isn't set up yet"):
            ingest.run_ingest_for(doc, doc.file_hash)

    @patch("tools.rag.ingest.media.extract_to_sidecar")
    def test_incomplete_prior_sidecar_raises_instead_of_falling_back(self, mock_extract, tmp_path, settings):
        """W1 review MINOR 3: the fallback's own "falls through to
        `_source_documents` as normal" story assumes whatever sidecar is on
        disk is either absent or genuinely finished. An INCOMPLETE one --
        `produced_at` unset, left behind by an earlier attempt that crashed
        mid-extraction rather than ever reaching this catch -- hits
        `_source_documents`'s own completeness gate FIRST: that raises
        `RuntimeError` before this function ever gets a chance to fall back
        to the text layer, so the Document still fails rather than landing
        on a text-only READY."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_extract.side_effect = media.ModelRoleUnavailable("no rag.extract bound")

        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["real text", None]))
        doc, _ = ingest.stage_document(str(path), move=False)

        stored_path = Path(doc.source_path)
        sidecar_path = stored_path.parent / "extract.json"
        sidecar_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "method": "vision",
                    "source": stored_path.name,
                    "source_sha256": doc.file_hash,
                    "model": {"engine": "x", "model_id": "y"},
                    "produced_at": None,  # incomplete -- an earlier attempt died mid-run
                    "segments": [],
                }
            )
        )

        with pytest.raises(RuntimeError, match="incomplete extraction sidecar"):
            ingest.run_ingest_for(doc, doc.file_hash)

        doc.refresh_from_db()
        # run_ingest_for itself never writes FAILED -- see its own docstring.
        assert doc.status == Document.Status.PROCESSING


# --- run_ingest_or_fail (T2 review: shared FAILED-writing wrapper) ----------


@pytest.mark.django_db
class TestRunIngestOrFail:
    """`run_ingest_for` itself never writes FAILED (see its own docstring)
    -- `run_ingest_or_fail` is the ONE place that does, and both of
    `run_ingest_for`'s real callers (`ingest_path` and
    `tools.rag.jobs.run_ingest`) must go through it so a failure never
    strands a Document at PROCESSING (review MAJOR finding: PROCESSING has
    no other way out -- `document_reingest` refuses it, and re-running
    `ingest_path`/`enqueue_ingest` on the same source hits
    `stage_document`'s own unchanged-hash dedup and never reaches
    `run_ingest_for` again)."""

    def test_happy_path_returns_run_ingest_fors_own_result(self, tmp_path):
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n")
        doc, _ = ingest.stage_document(str(path), move=False)

        result = ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert result == {"document_id": doc.id, "title": doc.title}

    def test_sha_mismatch_marks_failed_with_the_same_string_and_reraises(self, tmp_path):
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(RuntimeError, match="no longer matches") as exc_info:
            ingest.run_ingest_or_fail(doc, "0" * 64)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == str(exc_info.value)

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_ingest_exception_marks_failed_with_the_same_string_and_reraises(
        self, mock_gateway, mock_rag_index, tmp_path
    ):
        mock_rag_index.get_index.side_effect = RuntimeError("index is down")

        path = tmp_path / "notes.md"
        path.write_text("some content")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(RuntimeError, match="index is down"):
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "index is down"

    def test_a_corrupt_extraction_sidecar_fails_the_document_with_a_sentence(self, tmp_path):
        """C-46. `_source_documents` read the sidecar with a raw
        `json.loads`, so a truncated or non-object file surfaced on the
        library page as a `JSONDecodeError` repr. `sidecar.read_sidecar` is
        the hardened reader this module already ships; the failure it enables
        is a sentence naming the fix."""
        path = tmp_path / "notes.md"
        path.write_text("hello world")
        doc, _ = ingest.stage_document(str(path), move=False)

        stored_path = Path(doc.source_path)
        sidecar_path = stored_path.parent / "extract.json"
        sidecar_path.write_text("{not valid json")

        with pytest.raises(RuntimeError, match="unreadable or corrupt"):
            ingest.run_ingest_or_fail(doc, doc.file_hash)

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert "unreadable or corrupt" in doc.status_detail
        assert "JSONDecodeError" not in doc.status_detail

    def test_a_failed_document_cannot_be_silently_re_ingested(self, tmp_path):
        """Regression pin for the review's MAJOR finding: once a document
        is FAILED, `stage_document` on the SAME unchanged source is a
        silent no-op (`changed=False`) -- proving why every caller of
        `run_ingest_for` must resolve PROCESSING to FAILED itself, since
        nothing downstream will ever do it for them."""
        path = tmp_path / "sales.csv"
        path.write_text("id,amount\n1,10\n")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(RuntimeError):
            ingest.run_ingest_or_fail(doc, "0" * 64)
        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED

        restaged_doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is False
        assert restaged_doc.id == doc.id
        # stage_document leaves it exactly as FAILED -- but that's not a
        # dead end: tools.rag.views.document_reingest / ingest.
        # enqueue_reingest re-queue a FAILED document directly from its
        # retained managed-store copy, bypassing stage_document's own
        # unchanged-hash dedup entirely (dedup-bypass by design). Retry is
        # a working path; it just isn't THIS path.
        assert restaged_doc.status == Document.Status.FAILED


@pytest.mark.django_db
class TestFailDocument:
    """C-31. `_fail_document(doc, detail)` is the one place `run_ingest_or_
    fail`'s except branch and `_enqueue_ingest_job`'s failure tail each
    used to write the identical three lines by hand."""

    def test_marks_the_row_failed_with_the_given_detail(self, tmp_path):
        dest = tmp_path / "notes.md"
        doc = Document.objects.create(
            title="notes.md", source_path=str(dest), original_path=str(dest),
            file_hash="0" * 64, doc_type=Document.DocType.PROSE,
            status=Document.Status.PROCESSING,
        )

        ingest._fail_document(doc, "a specific operator-facing detail")

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "a specific operator-facing detail"

    def test_saves_only_the_three_named_fields(self, tmp_path):
        """Regression pin: an unconditional single-row `.save()` on the
        row the caller already holds -- if a future edit widened
        `update_fields` it would risk clobbering an unrelated concurrent
        write to the same row; this pins the field list stays exactly
        `status`/`status_detail`/`updated_at`."""
        dest = tmp_path / "notes.md"
        doc = Document.objects.create(
            title="notes.md", source_path=str(dest), original_path=str(dest),
            file_hash="0" * 64, doc_type=Document.DocType.PROSE,
            status=Document.Status.PROCESSING,
        )

        with patch.object(Document, "save", autospec=True) as mock_save:
            ingest._fail_document(doc, "boom")

        mock_save.assert_called_once_with(doc, update_fields=["status", "status_detail", "updated_at"])

    def test_is_not_the_same_helper_as_fail_stranded_rows(self):
        """`tools.rag.jobs._fail_stranded_rows` is a conditional queryset
        `.update()` guarding against a still-live worker; `_fail_document`
        is an unconditional single-row `.save()` on a row the caller
        already holds. They must stay two distinct functions -- this pins
        that `ingest` never re-exports `jobs._fail_stranded_rows` under
        this name, or vice versa."""
        from tools.rag import jobs

        assert ingest._fail_document is not jobs._fail_stranded_rows


@pytest.mark.django_db
class TestRunIngestOrFailWorkDirRetention:
    """T7 round-3 review ADJUDICATED: a media Document's `work/` dir is
    purged UNCONDITIONALLY whenever `run_ingest_or_fail`'s except branch
    runs -- regardless of `ctx.attempt`. A previous cut gated this on
    `ctx.attempt >= 1`, reasoning that `attempt == 0` might still resume
    automatically; traced through both paths that could reach this except
    branch, that never actually held: a manual Retry always creates a
    brand-new job with `checkpoint_state=None` (overwriting `work/`, never
    speeding anything up regardless of what attempt the FAILED job was on),
    and the one case that could resume from a retained `work/` dir -- a
    crashed worker, forgiven by `models.queue.claim._sweep_orphans` --
    never reaches this except branch at all (the process died, so nothing
    here ever ran to catch anything). See `run_ingest_or_fail`'s own
    docstring for the full reasoning."""

    def _stage_av_doc(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        with patch("tools.rag.ingest.transcode") as mock_transcode:
            mock_transcode.probe_duration.return_value = 60.0  # under cap at STAGE time
            doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is True
        return doc

    def _fail_transcription_over_cap(self, doc, ctx):
        """Drives `run_ingest_or_fail` to a real failure that happens
        AFTER `transcribe_to_sidecar` has already created `work/` --
        the over-duration-cap `ValueError` (probed on the WAV, well after
        `work_dir.mkdir()` runs)."""
        with (
            patch("tools.rag.media.resolve") as mock_resolve,
            patch("tools.rag.media.get_engine") as mock_get_engine,
            patch("tools.rag.media.get_transcriber_for"),
            patch("tools.rag.media.transcode") as mock_media_transcode,
        ):
            mock_resolve.return_value = ResolvedModel("whisper", "ggml-base.en", "http://localhost:8080")
            mock_engine = MagicMock()
            mock_engine.is_healthy.return_value = True
            mock_get_engine.return_value = mock_engine
            mock_media_transcode.DEFAULT_SLICE_SECONDS = 300
            mock_media_transcode.probe_duration.return_value = 99_999.0

            with pytest.raises(ValueError, match="media duration limit"):
                ingest.run_ingest_or_fail(doc, doc.file_hash, ctx)

    def test_purged_at_the_attempt_ceiling(self, tmp_path, settings):
        doc = self._stage_av_doc(tmp_path, settings)

        self._fail_transcription_over_cap(doc, make_job_ctx(attempt=1))

        work_dir = store.document_dir(doc.id) / "work"
        assert not work_dir.exists()

    def test_purged_on_the_first_failure_too(self, tmp_path, settings):
        """T7 round-3: inverted -- `attempt == 0` is NOT special. A manual
        Retry after this failure would start a brand-new job with
        `checkpoint_state=None` regardless, so retaining `work/` here would
        never have sped anything up."""
        doc = self._stage_av_doc(tmp_path, settings)

        self._fail_transcription_over_cap(doc, make_job_ctx(attempt=0))

        work_dir = store.document_dir(doc.id) / "work"
        assert not work_dir.exists()

    def test_purged_when_ctx_is_none_too(self, tmp_path, settings):
        """T7 round-3: inverted -- the CLI path (`ingest_path`, no `ctx` at
        all) purges too, same as every other path through this except
        branch."""
        doc = self._stage_av_doc(tmp_path, settings)

        self._fail_transcription_over_cap(doc, None)

        work_dir = store.document_dir(doc.id) / "work"
        assert not work_dir.exists()


@pytest.mark.django_db
class TestNullJobContextSmokeTest:
    """T7 review m7: `models.contracts.jobkinds.NULL_JOB_CONTEXT` is a real
    drop-in for a caller with no queue behind it -- `ingest_path` (the
    CLI's synchronous entry point) passes NO `ctx` at all, so
    `run_ingest_for`'s AV branch swaps in `NULL_JOB_CONTEXT` at the one
    call site that needs one.
    Proven end to end here (a real `.mp4` through `ingest_path`, mocked
    only at the transcode/transcriber/embed boundaries) rather than only
    at the unit level, since the whole point is that NOTHING about the
    driver's own control flow needs to special-case "no real ctx"."""

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    @patch("tools.rag.ingest.transcode")
    def test_cli_ingest_of_an_av_file_succeeds_end_to_end(
        self,
        mock_stage_transcode,
        mock_resolve,
        mock_get_engine,
        mock_get_transcriber_for,
        mock_media_transcode,
        mock_gateway,
        mock_rag_index,
        tmp_path,
        settings,
    ):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        mock_stage_transcode.probe_duration.return_value = 30.0

        mock_resolve.return_value = ResolvedModel("whisper", "ggml-base.en", "http://localhost:8080")
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_media_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_media_transcode.slice_audio.return_value = [Path("/fake/slice-00000.wav")]
        mock_media_transcode.probe_duration.return_value = 30.0

        from models.contracts.engines.base import TranscriptResult, TranscriptSegment

        fake_transcriber = MagicMock()
        fake_transcriber.transcribe.return_value = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=2.0, text="hello from the CLI"),), language="en"
        )
        mock_get_transcriber_for.return_value = fake_transcriber

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")

        doc = ingest.ingest_path(str(path))

        assert doc.status == Document.Status.READY
        assert doc.extraction["method"] == "transcription"
        mock_index.insert_nodes.assert_called_once()
        (nodes,), _ = mock_index.insert_nodes.call_args
        assert "hello from the CLI" in nodes[0].get_content()

        # No leftover work/ dir on a clean finish, exactly as the queued
        # path leaves.
        work_dir = store.document_dir(doc.id) / "work"
        assert not work_dir.exists()


# --- ingest_path failure handling (T2 review: MAJOR) -------------------------


@pytest.mark.django_db
class TestIngestPathFailureHandling:
    """`ingest_path` (the CLI's synchronous entry point) now routes its RUN
    half through `run_ingest_or_fail`, exactly like `tools.rag.jobs.
    run_ingest` does for the queued path -- so a `manage.py ingest` failure
    leaves the row FAILED (with the raised exception's own text), not
    stranded at PROCESSING, while still re-raising so the CLI command's own
    per-file try/except reports it unchanged."""

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_run_failure_marks_document_failed_and_reraises_the_same_string(
        self, mock_gateway, mock_rag_index, tmp_path
    ):
        mock_rag_index.get_index.side_effect = RuntimeError("index is down")

        path = tmp_path / "notes.md"
        path.write_text("some content")

        with pytest.raises(RuntimeError, match="index is down"):
            ingest.ingest_path(str(path))

        doc = Document.objects.get(original_path=str(path.resolve()))
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "index is down"


# --- enqueue_ingest (T2: watcher/upload entry point) -------------------------


@pytest.mark.django_db
class TestEnqueueIngest:
    @pytest.fixture(autouse=True)
    def _stage_from_the_inbox(self, tmp_path, settings):
        """B-3 fixture fix (round-3 hardening; coordinator ruling waives
        constraint 26 for this collision): every test below writes its own
        source file directly under `tmp_path` and stages it with a
        non-`None` actor (`SERVICE_PRINCIPAL` or a real `Principal`) --
        `stage_document`'s new containment check
        (`tools.rag.store.assert_inside_platform_dirs`) now needs
        `settings.INGEST_INBOX_DIR` to admit that path for any of them to
        reach the real `stage_document` body they're testing."""
        settings.INGEST_INBOX_DIR = tmp_path

    def test_unchanged_file_returns_none_without_enqueueing(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("stable")
        ingest.stage_document(str(path), move=False)

        with patch("tools.rag.ingest.enqueue") as mock_enqueue:
            job_id = ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        assert job_id is None
        mock_enqueue.assert_not_called()

    def test_changed_file_enqueues_rag_ingest_with_expected_payload(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("hello")

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            job_id = ingest.enqueue_ingest(str(path), category="Wave2Zeta", move=False, actor=SERVICE_PRINCIPAL)

        assert job_id == 99
        (kind, payload), _ = mock_enqueue.call_args
        assert kind == "rag.ingest"
        doc = Document.objects.get(original_path=str(path.resolve()))
        assert payload == {
            "document_id": doc.id,
            "sha256": doc.file_hash,
            "medium": "prose",
            "title": "notes.md",
            "actor_kind": "service",
            "actor_key": "local",
        }

    def test_move_true_moves_the_file(self, tmp_path):
        source_dir = tmp_path / "inbox"
        source_dir.mkdir()
        path = source_dir / "notes.md"
        path.write_text("hello")

        with patch("tools.rag.ingest.enqueue", return_value=1):
            ingest.enqueue_ingest(str(path), move=True, actor=SERVICE_PRINCIPAL)

        assert not path.exists()

    def test_scanned_pdf_is_routed_through_vision_extraction_at_job_start(self, tmp_path, settings):
        """RENAMED from `..._enqueues_with_pdf_scanned_medium` (H28,
        round-3 hardening, B-5): `_enqueue_ingest_job` no longer inspects
        a `.pdf`'s content at all. Its `payload["medium"]` is decided off
        the extension alone -- and, since review round 1 finding 2, that
        is the pessimistic "pdf-scanned" for EVERY `.pdf` while "media"
        is on, this one and an ordinary text-layer one alike (see
        `test_text_layer_pdf_also_enqueues_with_the_pessimistic_medium`
        right below, which pins that for the ordinary case). What this
        test pins instead: the scanned/image-only auto-detect still runs,
        correctly, just at job start (`run_ingest_for`) rather than at
        enqueue time -- proven by `tools.rag.media.extract_to_sidecar`
        actually being reached."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))
        doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is True

        with patch("tools.rag.ingest.media.extract_to_sidecar") as mock_extract:
            ingest.run_ingest_for(doc, doc.file_hash)

        mock_extract.assert_called_once()

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_mixed_pdf_is_routed_through_vision_extraction_at_job_start(
        self, mock_gateway, mock_rag_index, tmp_path, settings
    ):
        """RENAMED from `..._enqueues_with_pdf_scanned_medium` (H28,
        round-3 hardening, B-5) -- the sibling rename just above, for a
        MIXED pdf (some text pages, some textless) instead of a fully
        scanned one. W1 decision D1 (ANY textless page routes the WHOLE
        document through vision extraction, not just its own pages) still
        holds; it is proven at job start now, not enqueue time. `rag_index`/
        `gateway` are mocked because this file's real text page DOES reach
        `_ingest_prose`'s embed path once `extract_to_sidecar` (mocked,
        writes no sidecar) is done -- the same reason `TestRunIngestFor`'s
        own `test_prose_happy_path_sets_ready` mocks them."""
        mock_rag_index.get_index.return_value = MagicMock()
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "mixed.pdf"
        path.write_bytes(make_pdf_bytes(["real text", None]))
        doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is True

        with patch("tools.rag.ingest.media.extract_to_sidecar") as mock_extract:
            ingest.run_ingest_for(doc, doc.file_hash)

        mock_extract.assert_called_once()

    def test_text_layer_pdf_also_enqueues_with_the_pessimistic_medium(self, tmp_path, settings):
        """RENAMED from `..._enqueues_with_plain_prose_medium` (H28 review
        round 1, finding 2): an ORDINARY text-layer PDF used to enqueue as
        plain "prose", distinct from a scanned one -- that distinction
        required a content scan `_enqueue_ingest_job` no longer makes.
        While "media" is on, EVERY `.pdf` (this ordinary one included)
        gets "pdf-scanned" now, pessimistically, off the extension alone
        -- see `TestB5AdmissionIsReservedPessimistically` in `tools.rag.
        tests.test_ingest_scan_bounds` for the fuller regression coverage;
        this test's own job is just to confirm THIS specific file (a
        real, unambiguous text-only PDF) is no exception to that blanket
        rule."""
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        (_, payload), _ = mock_enqueue.call_args
        assert payload["medium"] == "pdf-scanned"

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_an_all_text_pdf_is_scanned_once_at_job_start(self, mock_gateway, mock_rag_index, tmp_path, settings):
        """RENAMED from `..._scanned_once_during_upload` (H28, round-3
        hardening, B-5): C-08's own "one scan, two uses" guarantee no
        longer lives in `enqueue_ingest` at all -- neither `stage_document`
        nor `_enqueue_ingest_job` scans a PDF's content anymore (see
        `tools.rag.tests.test_ingest_scan_bounds` for that regression).
        The guarantee now lives inside `run_ingest_for`, at job start: ONE
        `pdf_textless_pages` pass answers BOTH the page-cap decision and
        the vision-routing decision for the SAME all-text PDF -- `readers.
        py`'s own docstring still records that an all-text PDF costs a
        FULL scan at any positive limit, which is exactly why paying it
        only once, per job attempt, still matters."""
        mock_rag_index.get_index.return_value = MagicMock()
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))
        doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is True

        with patch.object(ingest.readers, "pdf_textless_pages",
                           wraps=ingest.readers.pdf_textless_pages) as scan:
            ingest.run_ingest_for(doc, doc.file_hash)

        assert scan.call_count == 1

    def test_with_media_off_nothing_changes(self, tmp_path, settings):
        """The gate. With the flag off neither scan runs at all, and this
        fix must not introduce one."""
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        path = tmp_path / "doc.pdf"
        path.write_bytes(make_pdf_bytes(["real extractable text"]))

        with patch("tools.rag.ingest.enqueue", return_value=99), \
             patch.object(ingest.readers, "pdf_textless_pages") as scan:
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        assert scan.call_count == 0

    def test_scanned_pdf_stays_plain_prose_with_flag_off(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        (_, payload), _ = mock_enqueue.call_args
        assert payload["medium"] == "prose"

    def test_image_file_enqueues_with_image_medium(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        path = tmp_path / "photo.jpg"
        path.write_bytes(b"fake jpeg bytes")

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        (_, payload), _ = mock_enqueue.call_args
        assert payload["medium"] == "image"

    def test_queue_unavailable_marks_document_failed_and_returns_none(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("hello")

        with patch("tools.rag.ingest.enqueue", side_effect=QueueUnavailable("nope")):
            job_id = ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        assert job_id is None
        doc = Document.objects.get(original_path=str(path.resolve()))
        assert doc.status == Document.Status.FAILED
        assert "migrations" in doc.status_detail

    def test_other_enqueue_failure_marks_document_failed_and_returns_none(self, tmp_path):
        path = tmp_path / "notes.md"
        path.write_text("hello")

        with patch("tools.rag.ingest.enqueue", side_effect=ValueError("planner blew up")):
            job_id = ingest.enqueue_ingest(str(path), move=False, actor=SERVICE_PRINCIPAL)

        assert job_id is None
        doc = Document.objects.get(original_path=str(path.resolve()))
        assert doc.status == Document.Status.FAILED
        assert "queue" in doc.status_detail.lower()
        assert "migrations" not in doc.status_detail

    def test_the_callers_own_actor_travels_in_the_payload(self, tmp_path):
        """Task 10, the acting rule part 1: `actor` is REQUIRED (no
        default -- a default of "the box" would silently attribute an
        uploaded document's ingest to nobody) and whatever the caller
        passes lands verbatim in the payload. The upload view passes
        `identity.request.principal_for_request(request)`; this test
        proves the plumbing with an arbitrary principal rather than
        re-deriving one from an HTTP request."""
        path = tmp_path / "notes.md"
        path.write_text("hello")

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            ingest.enqueue_ingest(str(path), move=False, actor=Principal("user", "9"))

        (_, payload), _ = mock_enqueue.call_args
        assert (payload["actor_kind"], payload["actor_key"]) == ("user", "9")


# --- stage_and_enqueue_one (C-14: the shared per-file upload body) ----------


@pytest.mark.django_db
class TestStageAndEnqueueOne:
    """Direct, function-level pin of `ingest.stage_and_enqueue_one`
    (task 23) -- the per-file body `views.document_upload` and
    `services.stage_turn_attachments` now both call, switching on
    `StageOutcome.kind` for their own bookkeeping. The two callers' own
    test suites (`test_views_upload_and_settings.py::TestDocumentUpload`,
    `test_services.py::TestStageTurnAttachmentsPerFileOutcomes`,
    `test_chat_scoped_documents.py`,
    `agents/chat/tests/test_turn_attachments.py`) already prove every
    OBSERVABLE surface end to end; this class pins the function's own
    six-way contract directly, one `kind` at a time."""

    @pytest.fixture(autouse=True)
    def _stage_from_the_inbox(self, tmp_path, settings):
        """B-3 fixture fix -- same reasoning as `TestEnqueueIngest`'s own
        fixture of this name: `target_dir` below is `tmp_path` itself, so
        `settings.INGEST_INBOX_DIR` must admit it for the tests that let
        the real `stage_document` run (the ones that only mock the
        innermost `tools.rag.ingest.enqueue` seam, not `enqueue_ingest`
        itself) to reach it."""
        settings.INGEST_INBOX_DIR = tmp_path

    def _kwargs(self, tmp_path, **overrides):
        max_upload_bytes = overrides.pop("max_upload_bytes", 10_000)
        defaults = dict(
            category=None, actor=SERVICE_PRINCIPAL, workstream_id=None,
            document_scope=Document.Scope.UNIVERSAL, max_upload_bytes=max_upload_bytes,
            upload_exts=ingest.supported_exts(), log_label="test",
        )
        defaults.update(overrides)
        return tmp_path, defaults

    def test_an_unsupported_extension_is_rejected(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)
        upload = SimpleUploadedFile("evil.exe", b"MZ")

        outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(name="evil.exe", kind="rejected")
        assert not (target_dir / "evil.exe").exists()

    def test_an_oversized_file_is_oversize_with_no_reason(self, tmp_path):
        """Post-merge review ruling: `stage_and_enqueue_one` no longer
        takes a `human_cap` parameter at all -- neither caller ever read
        `outcome.reason` for `"oversize"` (both build their own sentence
        from their own already-held `human_cap`), so there is no message
        to build here and `reason` stays at its `""` default. Only
        `"refused"` (an actual exception) fills it."""
        target_dir, kwargs = self._kwargs(tmp_path, max_upload_bytes=10)
        upload = SimpleUploadedFile("huge.txt", b"x" * 20)

        outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(name="huge.txt", kind="oversize")
        assert not (target_dir / "huge.txt").exists()

    def test_a_prose_upload_is_queued_with_its_document_and_is_not_tabular(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)
        upload = SimpleUploadedFile("notes.md", b"hello")

        with patch("tools.rag.ingest.enqueue", return_value=99):
            outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome.kind == "queued"
        assert outcome.is_tabular is False
        assert outcome.raced is False
        doc = Document.objects.get()
        assert outcome.document == doc

    def test_a_csv_upload_is_queued_and_reported_tabular(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)
        upload = SimpleUploadedFile("numbers.csv", b"a,b\n1,2")

        with patch("tools.rag.ingest.enqueue", return_value=99):
            outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome.kind == "queued"
        assert outcome.is_tabular is True

    def test_a_byte_identical_reupload_is_unchanged_and_carries_the_existing_document(
        self, tmp_path,
    ):
        target_dir, kwargs = self._kwargs(tmp_path)
        with patch("tools.rag.ingest.enqueue", return_value=99):
            ingest.stage_and_enqueue_one(
                SimpleUploadedFile("dup.md", b"same bytes"), target_dir, **kwargs)
        first = Document.objects.get()

        outcome = ingest.stage_and_enqueue_one(
            SimpleUploadedFile("dup.md", b"same bytes"), target_dir, **kwargs)

        assert outcome.kind == "unchanged"
        assert outcome.document == first
        assert not (target_dir / "dup.md").exists()  # the stray temp copy is unlinked

    def test_a_duration_cap_rejection_is_refused_with_the_exceptions_own_message(
        self, tmp_path, settings,
    ):
        # This calls `stage_and_enqueue_one` directly -- no Django test
        # client, no URL resolution -- so the VISION-FLAG RULE (this
        # module's own docstring) does not apply here; `.mp4` just needs
        # "media" in the flag set to clear the extension check at all and
        # reach the mocked exception below.
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        target_dir, kwargs = self._kwargs(tmp_path)
        upload = SimpleUploadedFile("clip.mp4", b"fake video bytes")

        with patch(
            "tools.rag.ingest.enqueue_ingest",
            side_effect=ingest.MediaDurationExceededError("clip.mp4 is over the duration cap"),
        ):
            outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(
            name="clip.mp4", kind="refused", reason="clip.mp4 is over the duration cap")
        assert not (target_dir / "clip.mp4").exists()

    def test_a_page_cap_rejection_is_refused_with_the_exceptions_own_message(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)
        upload = SimpleUploadedFile("scan.pdf", b"fake pdf bytes")

        with patch(
            "tools.rag.ingest.enqueue_ingest",
            side_effect=ingest.DocumentPageCapExceededError("scan.pdf is over the page cap"),
        ):
            outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(
            name="scan.pdf", kind="refused", reason="scan.pdf is over the page cap")
        assert not (target_dir / "scan.pdf").exists()

    def test_a_generic_enqueue_failure_is_failed_with_no_document(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)
        upload = SimpleUploadedFile("broken.txt", b"a")

        with patch("tools.rag.ingest.enqueue_ingest", side_effect=RuntimeError("boom")):
            outcome = ingest.stage_and_enqueue_one(upload, target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(name="broken.txt", kind="failed")

    def test_a_watcher_race_with_a_confirmed_row_is_queued_and_raced(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)
        dest = target_dir / "raced.txt"
        raced_doc = Document.objects.create(
            title="raced.txt", source_path=str(dest), original_path=str(dest),
            file_hash="0" * 64, doc_type=Document.DocType.PROSE,
        )

        with patch("tools.rag.ingest.enqueue_ingest", side_effect=FileNotFoundError):
            outcome = ingest.stage_and_enqueue_one(
                SimpleUploadedFile("raced.txt", b"a"), target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(
            name="raced.txt", kind="queued", document=raced_doc, raced=True)

    def test_a_watcher_race_with_no_confirmed_row_is_failed_not_queued(self, tmp_path):
        target_dir, kwargs = self._kwargs(tmp_path)

        with patch("tools.rag.ingest.enqueue_ingest", side_effect=FileNotFoundError):
            outcome = ingest.stage_and_enqueue_one(
                SimpleUploadedFile("orphan.txt", b"a"), target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(name="orphan.txt", kind="failed")

    def test_staged_but_not_enqueued_is_failed_with_the_staged_document(self, tmp_path):
        """The `else` branch: `job_id is None` and `dest_unchanged` is
        False -- `stage_document` created/updated the row, only the
        separate enqueue declined it. Simulated directly (rather than
        through a real queue failure) by pre-creating a `Document` at
        `dest`'s path with a HASH THAT DOES NOT MATCH `dest`'s real
        bytes (so `dest_unchanged` is False, not True) and mocking
        `enqueue_ingest` to return `None`."""
        target_dir, kwargs = self._kwargs(tmp_path)
        dest = target_dir / "half_staged.txt"
        existing = Document.objects.create(
            title="half_staged.txt", source_path=str(dest), original_path=str(dest),
            file_hash="0" * 64, doc_type=Document.DocType.PROSE,
        )

        with patch("tools.rag.ingest.enqueue_ingest", return_value=None):
            outcome = ingest.stage_and_enqueue_one(
                SimpleUploadedFile("half_staged.txt", b"new bytes"), target_dir, **kwargs)

        assert outcome == ingest.StageOutcome(
            name="half_staged.txt", kind="failed", document=existing)


# --- enqueue_reingest (T2: retry-button entry point) -------------------------


@pytest.mark.django_db
class TestEnqueueReingest:
    def _failed_doc(self, tmp_path, content=b"hello"):
        path = tmp_path / "notes.md"
        path.write_bytes(content)
        doc, _ = ingest.stage_document(str(path), move=False)
        doc.status = Document.Status.FAILED
        doc.status_detail = "boom"
        doc.save(update_fields=["status", "status_detail"])
        return doc

    def test_missing_store_copy_raises(self, tmp_path):
        doc = self._failed_doc(tmp_path)
        Path(doc.source_path).unlink()

        with pytest.raises(FileNotFoundError):
            ingest.enqueue_reingest(doc, actor=SERVICE_PRINCIPAL)

    def test_success_resets_status_and_enqueues(self, tmp_path):
        doc = self._failed_doc(tmp_path)

        with patch("tools.rag.ingest.enqueue", return_value=7) as mock_enqueue:
            job_id = ingest.enqueue_reingest(doc, actor=SERVICE_PRINCIPAL)

        assert job_id == 7
        doc.refresh_from_db()
        assert doc.status == Document.Status.PENDING
        assert doc.status_detail == ""
        (kind, payload), _ = mock_enqueue.call_args
        assert kind == "rag.ingest"
        assert payload["document_id"] == doc.id
        assert payload["sha256"] == doc.file_hash
        assert (payload["actor_kind"], payload["actor_key"]) == ("service", "local")

    def test_enqueue_failure_marks_failed_again(self, tmp_path):
        doc = self._failed_doc(tmp_path)

        with patch("tools.rag.ingest.enqueue", side_effect=RuntimeError("boom")):
            job_id = ingest.enqueue_reingest(doc, actor=SERVICE_PRINCIPAL)

        assert job_id is None
        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED


# --- watch_folder: _IngestEventHandler tracking (T2: size-quiescence) -------


class TestIngestEventHandlerTracking:
    def test_supported_file_is_tracked_on_create(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("x")

        handler.on_created(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) in handler.pending

    def test_tracked_on_modified_too(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("x")

        handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) in handler.pending

    def test_unsupported_extension_is_logged_and_not_tracked(self, tmp_path, caplog):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "evil.exe"
        f.write_bytes(b"MZ")

        with caplog.at_level(logging.INFO):
            handler.on_created(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) not in handler.pending
        assert any("unsupported extension" in r.message for r in caplog.records)

    def test_directory_events_are_ignored(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        handler.on_created(SimpleNamespace(is_directory=True, src_path=str(tmp_path / "sub")))
        assert handler.pending == {}

    def test_vanished_file_is_not_tracked(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        missing = tmp_path / "gone.txt"
        handler.on_modified(SimpleNamespace(is_directory=False, src_path=str(missing)))
        assert str(missing) not in handler.pending

    def test_av_extension_is_ignored_with_flag_off(self, tmp_path, caplog, settings):
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")

        with caplog.at_level(logging.INFO):
            handler.on_created(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) not in handler.pending
        assert any("unsupported extension" in r.message for r in caplog.records)

    def test_av_extension_is_tracked_with_flag_on(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")

        handler.on_created(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) in handler.pending

    def test_image_extension_is_ignored_with_flag_off(self, tmp_path, caplog, settings):
        # T8: IMAGE_EXTS join the flag the same way AV_EXTS already did.
        settings.FARABUNKER_FEATURES = frozenset()  # pinned OFF -- do not trust the ambient default
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        with caplog.at_level(logging.INFO):
            handler.on_created(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) not in handler.pending
        assert any("unsupported extension" in r.message for r in caplog.records)

    def test_image_extension_is_tracked_with_flag_on(self, tmp_path, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        handler.on_created(SimpleNamespace(is_directory=False, src_path=str(f)))

        assert str(f) in handler.pending


class TestSupportedExts:
    """`supported_exts()` -- a FUNCTION (T7), so a flag flip is visible on
    the very next call, never cached from an earlier import."""

    def test_media_off_excludes_av_and_image_extensions(self, settings):
        settings.FARABUNKER_FEATURES = frozenset()
        exts = ingest.supported_exts()
        assert ".mp4" not in exts
        assert ".mp3" not in exts
        assert ".png" not in exts
        assert ".jpg" not in exts
        assert ".pdf" in exts
        assert ".csv" in exts

    def test_media_on_includes_av_extensions(self, settings):
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        exts = ingest.supported_exts()
        assert ".mp4" in exts
        assert ".mov" in exts
        assert ".mkv" in exts
        assert ".webm" in exts
        assert ".mp3" in exts
        assert ".wav" in exts
        assert ".m4a" in exts

    def test_media_on_includes_image_extensions(self, settings):
        # T8: IMAGE_EXTS join the accepted set behind the same flag AV_EXTS
        # already uses.
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        exts = ingest.supported_exts()
        assert ".png" in exts
        assert ".jpg" in exts
        assert ".jpeg" in exts


# --- watch_folder: poll_once (T2: size-quiescence enqueue) -------------------


@pytest.mark.django_db
class TestIngestEventHandlerPollOnce:
    """`@pytest.mark.django_db` at class level (T4): `poll_once` now reads
    `RagSettings.get_solo().max_upload_bytes` once a tracked file reaches
    quiescence (every test below that drives a stable file through), so
    every test in this class needs DB access even where the size cap
    itself is never exercised."""

    @pytest.fixture(autouse=True)
    def _stage_from_the_inbox(self, tmp_path, settings):
        """B-3 fixture fix -- same reasoning as `TestEnqueueIngest`'s own
        fixture of this name: every `handler = ingest._IngestEventHandler(
        tmp_path)` below watches `tmp_path` itself, so `settings.
        INGEST_INBOX_DIR` must admit it for the two tests here that let the
        real `enqueue_ingest`/`stage_document` run unmocked to reach them
        -- every other test in this class mocks `enqueue_ingest` directly
        and never reaches the new check at all."""
        settings.INGEST_INBOX_DIR = tmp_path

    def test_size_change_refreshes_the_clock_without_enqueueing(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("hello")  # actual size: 5
        old_time = time.monotonic() - 100
        handler.pending[str(f)] = (1, old_time)  # stale recorded size

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_not_called()
        new_size, new_time = handler.pending[str(f)]
        assert new_size == f.stat().st_size
        assert new_time > old_time

    def test_stable_file_is_enqueued_exactly_once_and_evicted(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("stable content")
        size = f.stat().st_size
        # Recorded "last changed" far enough in the past to already be stable.
        handler.pending[str(f)] = (size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_called_once_with(str(f), None, move=True, actor=SERVICE_PRINCIPAL)
        assert str(f) not in handler.pending

    def test_the_watchers_own_actor_reaches_the_real_payload(self, tmp_path):
        """Task 10, the acting rule part 1: the watcher has no request and
        no operator behind it, so it stamps THE ONE SERVICE PRINCIPAL
        (spec section 5.3 item 8) itself, rather than inheriting one.
        `enqueue_ingest` is NOT mocked here (unlike the sibling test just
        above): only the innermost `enqueue()` seam is, so this proves the
        actor really reaches the `rag.ingest` payload, not just the call
        `poll_once` makes to `enqueue_ingest`."""
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("stable content")
        size = f.stat().st_size
        handler.pending[str(f)] = (size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1)

        with patch("tools.rag.ingest.enqueue", return_value=99) as mock_enqueue:
            handler.poll_once()

        (_, payload), _ = mock_enqueue.call_args
        assert (payload["actor_kind"], payload["actor_key"]) == ("service", "local")

    def test_corrupt_pdf_via_watcher_fails_honestly_not_stranded_at_pending(self, tmp_path, settings):
        """T8 review MAJOR 1, probe-reproduced, end to end via the REAL
        watcher path -- `enqueue_ingest` is NOT mocked here (unlike every
        other test in this class): the whole point is to prove the real
        `stage_document` -> `_enqueue_ingest_job` -> `_needs_vision_
        extraction` chain degrades a corrupt PDF's probe failure to an
        honest FAILED row, reachable by an operator's Retry button --
        never stranded at PENDING (the old, unguarded probe's actual
        failure mode: an uncaught raise there propagated out of
        `enqueue_ingest`, straight past `poll_once`'s own try/except into
        the generic `except Exception` branch, which pops the file from
        `pending` -- so the watcher itself never re-attempts either way,
        but the Document row used to be left with NOTHING useful, PENDING
        forever, invisible to Retry).

        Deliberately does NOT round-trip through the HTTP `document_
        reingest` view here (unlike `TestDocumentUploadCorruptPdf` in
        test_views_upload_and_settings.py, which already proves that end
        to end): combining
        an `settings.FARABUNKER_FEATURES` override with an actual URL
        resolution/HTTP request risks permanently poisoning the process-
        wide URL resolver cache if this happens to be the FIRST request
        Django ever resolves in a given test run (`config/urls.py` builds
        its `vision/` mount conditionally, once, at import time -- see
        `tools.vision.tests.test_config.TestVisionUrlMount.test_not_
        mounted_when_the_feature_is_off`'s own docstring for the full
        mechanism) -- asserting on `doc.status`/`doc.status_detail`
        directly proves the exact same claim (FAILED, not stranded at
        PENDING, `document_reingest`'s own refusal condition is
        `status in (PENDING, PROCESSING)`) without that risk.
        """
        settings.FARABUNKER_FEATURES = frozenset({"media"})
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "corrupt.pdf"
        f.write_bytes(b"%PDF-1.4 not a real pdf, corrupt garbage bytes")
        size = f.stat().st_size
        handler.pending[str(f)] = (size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1)

        handler.poll_once()  # the REAL enqueue_ingest -- no mock at all

        doc = Document.objects.get(original_path=str(f.resolve()))
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail  # an honest, non-blank detail -- never silently stranded
        # document_reingest's own refusal condition (never reached here).
        assert doc.status not in (Document.Status.PENDING, Document.Status.PROCESSING)

    def test_not_yet_stable_is_neither_enqueued_nor_evicted(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("stable content")
        size = f.stat().st_size
        handler.pending[str(f)] = (size, time.monotonic())  # just now -- not stable yet

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_not_called()
        assert str(f) in handler.pending

    def test_category_derived_from_immediate_subfolder(self, tmp_path):
        sub = tmp_path / "medical"
        sub.mkdir()
        handler = ingest._IngestEventHandler(tmp_path)
        f = sub / "a.txt"
        f.write_text("stable content")
        handler.pending[str(f)] = (f.stat().st_size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_called_once_with(str(f), "medical", move=True, actor=SERVICE_PRINCIPAL)

    def test_vanished_file_is_dropped_silently(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        missing_path = str(tmp_path / "gone.txt")
        handler.pending[missing_path] = (10, time.monotonic() - 100)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_not_called()
        assert missing_path not in handler.pending

    def test_enqueue_exception_is_logged_and_does_not_raise(self, tmp_path, caplog):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "a.txt"
        f.write_text("stable content")
        handler.pending[str(f)] = (f.stat().st_size, time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1)

        with patch("tools.rag.ingest.enqueue_ingest", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.ERROR):
                handler.poll_once()  # must not raise

        assert str(f) not in handler.pending


# --- watch_folder: poll_once size-cap enforcement (T4) -----------------------


@pytest.mark.django_db
class TestIngestEventHandlerPollOnceSizeCap:
    """`RagSettings.max_upload_bytes` enforcement at the watcher door (T4):
    an over-cap stable file is left in `pending` (not enqueued, not
    evicted), logged once per distinct oversized size."""

    def _stable(self, handler, path):
        handler.pending[str(path)] = (
            path.stat().st_size,
            time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1,
        )

    def test_over_cap_stable_file_is_not_enqueued_and_stays_pending(self, tmp_path):
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_not_called()
        assert str(f) in handler.pending

    def test_over_cap_logs_exactly_one_line(self, tmp_path, caplog):
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest"):
            with caplog.at_level(logging.ERROR):
                handler.poll_once()

        over_cap_records = [r for r in caplog.records if "upload cap" in r.message]
        assert len(over_cap_records) == 1
        assert str(f) in over_cap_records[0].message

    def test_over_cap_at_the_same_size_does_not_repeat_the_log_on_a_later_poll(self, tmp_path, caplog):
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest"):
            with caplog.at_level(logging.ERROR):
                handler.poll_once()
                # Still stable, still the same size -- re-stabilize the
                # entry (poll_once never advances its own clock) and poll
                # again.
                self._stable(handler, f)
                handler.poll_once()

        over_cap_records = [r for r in caplog.records if "upload cap" in r.message]
        assert len(over_cap_records) == 1

    def test_over_cap_re_logs_when_the_size_changes(self, tmp_path, caplog):
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest"):
            with caplog.at_level(logging.ERROR):
                handler.poll_once()  # first oversized log, at 20 bytes

                f.write_bytes(b"x" * 30)  # still oversized, but a NEW size
                handler.poll_once()  # picked up as a size change, clock resets
                self._stable(handler, f)
                handler.poll_once()  # now stable again at the new size

        over_cap_records = [r for r in caplog.records if "upload cap" in r.message]
        assert len(over_cap_records) == 2

    def test_at_or_under_cap_is_enqueued_normally(self, tmp_path):
        RagSettings.objects.create(pk=1, max_upload_bytes=20)
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "exact.txt"
        f.write_bytes(b"x" * 20)  # exactly at the cap -- must be accepted
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_called_once_with(str(f), None, move=True, actor=SERVICE_PRINCIPAL)
        assert str(f) not in handler.pending

    def test_default_cap_from_a_fresh_settings_row_is_generous_enough_for_a_small_file(self, tmp_path):
        # No RagSettings row created -- get_solo()'s default applies.
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "small.txt"
        f.write_bytes(b"x" * 10)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_called_once_with(str(f), None, move=True, actor=SERVICE_PRINCIPAL)

    def test_get_solo_is_called_once_per_poll_once_call_not_once_per_pending_file(self, tmp_path):
        """T4 review MINOR: `poll_once` reads `RagSettings.max_upload_bytes`
        ONCE per invocation, not once per pending file it evaluates --
        `poll_once` already runs on a 1-second timer
        (`_POLL_INTERVAL_SECONDS`), so a per-file read would be N DB round
        trips a second for N pending files, for a value that cannot change
        mid-pass."""
        handler = ingest._IngestEventHandler(tmp_path)
        for i in range(3):
            f = tmp_path / f"f{i}.txt"
            f.write_text("stable content")
            self._stable(handler, f)

        settings_row = SimpleNamespace(max_upload_bytes=1024**4)
        with patch("tools.rag.ingest.RagSettings.get_solo", return_value=settings_row) as mock_get_solo:
            with patch("tools.rag.ingest.enqueue_ingest"):
                handler.poll_once()

        assert mock_get_solo.call_count == 1

    def test_logged_oversized_entry_clears_once_a_raised_cap_lets_it_enqueue(self, tmp_path):
        """T4 review MINOR (stale-entry corner): an operator raising the
        cap after a file was logged as oversized must not leave a stale
        `_logged_oversized` entry behind once that file is finally
        enqueued -- otherwise a LATER re-drop of a file at that exact
        stale size would wrongly stay suppressed even though it's a fresh
        event."""
        RagSettings.objects.create(pk=1, max_upload_bytes=10)
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()  # oversized -- logged, left pending

        mock_enqueue.assert_not_called()
        assert str(f) in handler._logged_oversized

        settings_row = RagSettings.get_solo()
        settings_row.max_upload_bytes = 100
        settings_row.save(update_fields=["max_upload_bytes"])

        self._stable(handler, f)  # re-stabilize at the SAME (unchanged) size
        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()

        mock_enqueue.assert_called_once_with(str(f), None, move=True, actor=SERVICE_PRINCIPAL)
        assert str(f) not in handler.pending
        assert str(f) not in handler._logged_oversized


@pytest.mark.django_db
class TestIngestEventHandlerPollOnceDurationCap:
    """T7 review m2: `MediaDurationExceededError` from `enqueue_ingest`
    gets the SAME "leave it, log once per distinct (path, size)" treatment
    as the byte-cap branch above -- not a bare pop-and-traceback."""

    def _stable(self, handler, path):
        handler.pending[str(path)] = (
            path.stat().st_size,
            time.monotonic() - ingest.STABLE_AFTER_SECONDS - 1,
        )

    def test_over_duration_cap_is_left_in_pending_not_popped(self, tmp_path):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = ingest.MediaDurationExceededError(
                "clip.mp4 is over the media duration limit"
            )
            handler.poll_once()

        assert str(f) in handler.pending

    def test_over_duration_cap_logs_exactly_one_line_naming_the_file(self, tmp_path, caplog):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = ingest.MediaDurationExceededError(
                "clip.mp4 is over the media duration limit"
            )
            with caplog.at_level(logging.ERROR):
                handler.poll_once()

        over_cap_records = [r for r in caplog.records if "media duration limit" in r.message]
        assert len(over_cap_records) == 1
        assert str(f) in over_cap_records[0].message

    def test_over_duration_cap_at_the_same_size_does_not_repeat_the_log(self, tmp_path, caplog):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = ingest.MediaDurationExceededError(
                "clip.mp4 is over the media duration limit"
            )
            with caplog.at_level(logging.ERROR):
                handler.poll_once()
                # Still stable, still the same size -- re-stabilize and
                # poll again, same as the byte-cap test's own convention.
                self._stable(handler, f)
                handler.poll_once()

        over_cap_records = [r for r in caplog.records if "media duration limit" in r.message]
        assert len(over_cap_records) == 1

    def test_over_duration_cap_re_logs_when_the_size_changes(self, tmp_path, caplog):
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = ingest.MediaDurationExceededError(
                "clip.mp4 is over the media duration limit"
            )
            with caplog.at_level(logging.ERROR):
                handler.poll_once()  # first over-duration log, at 20 bytes

                f.write_bytes(b"x" * 30)  # still over-duration, but a NEW size
                handler.poll_once()  # picked up as a size change, clock resets
                self._stable(handler, f)
                handler.poll_once()  # now stable again at the new size

        over_cap_records = [r for r in caplog.records if "media duration limit" in r.message]
        assert len(over_cap_records) == 2

    def test_generic_enqueue_failure_still_pops_as_before(self, tmp_path):
        """The generic `except Exception` branch's own "pop and log a
        traceback" behavior is UNCHANGED -- only `MediaDurationExceededError`
        gets the new "leave it in pending" treatment."""
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = RuntimeError("boom")
            handler.poll_once()

        assert str(f) not in handler.pending

    def test_stable_file_is_probed_at_most_once_across_many_polls(self, tmp_path):
        """T7 round-3 review MINOR (probe-reproduced): a parked
        over-duration file used to fork `ffprobe` (via `enqueue_ingest` ->
        `stage_document`'s duration check) once per poll tick FOREVER --
        86k/day at the default 1s poll interval. Once the verdict for a
        given `(path, size)` is known, later polls at that same size must
        skip the enqueue attempt entirely, not just re-suppress the log
        line."""
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = ingest.MediaDurationExceededError("over the media duration limit")
            for _ in range(5):
                handler.poll_once()

        assert mock_enqueue.call_count == 1
        assert str(f) in handler.pending

    def test_vanished_file_clears_the_over_duration_log_suppression_entry(self, tmp_path):
        """T7 round-3 review MINOR: the reintroduced T4 finding --
        `_logged_over_duration` must be popped in the vanish/OSError
        branches, mirroring `_logged_oversized`'s own pops exactly, so a
        LATER file re-dropped at the same path/size isn't wrongly
        suppressed by a stale entry from a file that no longer exists."""
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)
        handler._logged_over_duration[str(f)] = 20  # a prior poll already logged it

        f.unlink()
        handler.poll_once()

        assert str(f) not in handler._logged_over_duration
        assert str(f) not in handler.pending

    def test_size_change_clears_the_over_duration_log_suppression_entry(self, tmp_path):
        """T9.5 audit fix (one-line defect): the size-change branch already
        popped `_logged_oversized` but left a stale `_logged_over_duration`
        entry behind -- so a file that changed size and later changed BACK
        to a previously-logged size stayed wrongly suppressed instead of
        getting a fresh over-duration evaluation. `_IngestEventHandler.
        __init__`'s own docstring claims the two dicts share "the same
        clearing rules" -- this proves that claim is now true."""
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        handler.pending[str(f)] = (20, time.monotonic())
        handler._logged_over_duration[str(f)] = 20  # a prior poll already logged it at size 20

        f.write_bytes(b"x" * 30)  # size changes -- not yet stable
        handler.poll_once()

        assert str(f) not in handler._logged_over_duration

    def test_logged_over_duration_entry_clears_once_the_file_is_finally_enqueued(self, tmp_path):
        """Mirrors the byte-cap suppression-clearing test above: once the
        file finally enqueues successfully, its stale `_logged_over_duration`
        entry must not linger and wrongly suppress a later, genuinely fresh
        over-duration event at that same size.

        T7 round-3: re-polling at the exact SAME size is now throttled
        (the probe-suppression fix) -- it would never even attempt
        `enqueue_ingest` again at that size, so this drives the retry via a
        SIZE CHANGE instead (the operator replacing the file), the real way
        a parked over-duration file gets re-evaluated."""
        handler = ingest._IngestEventHandler(tmp_path)
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x" * 20)
        self._stable(handler, f)

        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            mock_enqueue.side_effect = ingest.MediaDurationExceededError("over the media duration limit")
            handler.poll_once()

        assert str(f) in handler._logged_over_duration

        f.write_bytes(b"x" * 30)  # a new size -- picked up as a size change
        handler.poll_once()
        self._stable(handler, f)
        with patch("tools.rag.ingest.enqueue_ingest") as mock_enqueue:
            handler.poll_once()  # now stable at the new size -- succeeds

        mock_enqueue.assert_called_once_with(str(f), None, move=True, actor=SERVICE_PRINCIPAL)
        assert str(f) not in handler.pending
        assert str(f) not in handler._logged_over_duration
