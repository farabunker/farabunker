"""Regression tests for H13 review round 1, finding 10:
`tools.rag.media`'s scratch-work-directory and sidecar writes now go
through the shared `foundation.files` helpers instead of a bare
`mkdir`/`write_bytes`.

A NEW module -- `tools/rag/tests/` is a whole directory another session
edits concurrently (plan constraint 18) -- rather than an addition to
`test_media.py`. Reuses that file's own conventions (module-boundary
mocks for `resolve`/`get_engine`/`get_llm_for`/`transcode`/`extract`/
`readers`, `_managed_store` redirecting `DOCUMENTS_DIR`) so these tests
never shell out to a real engine.
"""
from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.contracts.bindings import ResolvedModel
from tools.rag import media, store
from tools.rag.models import Document
from tools.rag.tests._helpers import make_job_ctx

EXTRACT_RESOLVED = ResolvedModel("ollama", "llava", "http://localhost:11434")


@pytest.fixture(autouse=True)
def _managed_store(tmp_path, settings):
    store_root = tmp_path / "_managed_store"
    store_root.mkdir()
    settings.DOCUMENTS_DIR = store_root
    return store_root


def _make_image_doc(**kwargs) -> Document:
    defaults = dict(
        title="photo.jpg",
        source_path="/inbox-irrelevant/photo.jpg",
        original_path="/inbox/photo.jpg",
        file_hash="e" * 64,
        doc_type=Document.DocType.PROSE,
        media_type="image/jpeg",
        status=Document.Status.PROCESSING,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


class TestWriteSidecarAtomicPermissions:
    def test_the_sidecar_is_owner_read_write_only(self, tmp_path):
        path = tmp_path / "extract.json"

        media._write_sidecar_atomic(path, {"segments": []})

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_re_writing_an_existing_wider_sidecar_still_tightens_it(self, tmp_path):
        import os

        path = tmp_path / "extract.json"
        path.write_text("{}")
        os.chmod(path, 0o644)

        media._write_sidecar_atomic(path, {"segments": []})

        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.django_db
@patch("tools.rag.media.readers")
@patch("tools.rag.media.extract")
@patch("tools.rag.media.transcode")
@patch("tools.rag.media.get_llm_for")
@patch("tools.rag.media.get_engine")
@patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
class TestExtractToSidecarWorkDirPermissions:
    """Mirrors `test_media.py::TestExtractToSidecarImagePath`'s own
    mocking shape exactly -- the image (single-shot) branch is the
    simplest path through `extract_to_sidecar` that still reaches the
    `work_dir`/`temp_png` write this task changed."""

    def test_the_temp_png_is_owner_read_write_only_before_cleanup(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.normalize_image.return_value = b"normalized-png"
        mock_get_llm_for.return_value = MagicMock()

        captured = {}

        def _capture_and_return_text(path, llm=None):
            captured["png_mode"] = stat.S_IMODE(Path(path).stat().st_mode)
            captured["dir_mode"] = stat.S_IMODE(Path(path).parent.stat().st_mode)
            return "photo text"

        # RE-PINNED (preview UAT, 2026-09-17): the single-image branch now
        # calls `describe_image` BEFORE `extract_image_text` -- stubbed
        # here the same way this module already stubs the transcription
        # call, with a plain string return, so this test keeps pinning
        # the file-mode invariant rather than an unconfigured `MagicMock`
        # failing JSON serialization of the sidecar two calls later.
        mock_extract.describe_image.return_value = "a photo"
        mock_extract.extract_image_text.side_effect = _capture_and_return_text

        doc = _make_image_doc()
        media.extract_to_sidecar(doc, make_job_ctx())

        # `_finish_driver` removes `work_dir` on success -- the assertions
        # above had to happen DURING the call, from inside the mock, or
        # there would be nothing left on disk to check.
        assert captured["png_mode"] == 0o600
        assert captured["dir_mode"] == 0o700
        assert not (store.document_dir(doc.id) / "work").exists()
