"""Unit tests for tools/rag/store.py (ADR 0008, ADR 0009).

No database or external services involved -- these exercise pure filesystem
behavior against a temporary `settings.DOCUMENTS_DIR` (via pytest-django's
`settings` fixture), never the real managed store.
"""
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from tools.rag import store


class TestDocumentDir:
    def test_returns_path_under_documents_dir(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        result = store.document_dir(42)

        assert result == tmp_path / "documents" / "42"

    def test_does_not_create_the_directory(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        result = store.document_dir(7)

        assert not result.exists()


class TestSidecarPath:
    def test_the_sidecar_path_is_under_the_documents_own_directory(self, settings, tmp_path):
        """C-18. `"extract.json"` was written out at five production sites
        and `/ "work"` at three, all of them under a directory `store.py`
        already owns. The store decides its own layout."""
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        assert store.sidecar_path(7) == store.document_dir(7) / "extract.json"

    def test_does_not_create_anything(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        result = store.sidecar_path(7)

        assert not result.exists()
        assert not result.parent.exists()


class TestSidecarPathForDir:
    def test_agrees_with_sidecar_path_for_the_same_directory(self, settings, tmp_path):
        """C-18 review: `sidecar_path(doc_id)` delegates to
        `sidecar_path_for_dir(document_dir(doc_id))` -- same filename,
        computed once. `tools.rag.ingest._source_documents` is the one
        caller of the directory-keyed form directly, resolving its sidecar
        from an already-resolved stored-file path rather than a `doc_id`
        (see that function's own docstring)."""
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        assert store.sidecar_path(7) == store.sidecar_path_for_dir(store.document_dir(7))

    def test_returns_the_sidecar_under_an_arbitrary_directory(self, tmp_path):
        assert store.sidecar_path_for_dir(tmp_path) == tmp_path / "extract.json"

    def test_does_not_create_anything(self, tmp_path):
        result = store.sidecar_path_for_dir(tmp_path / "nonexistent")

        assert not result.exists()
        assert not result.parent.exists()


class TestWorkDir:
    def test_the_work_dir_is_under_the_documents_own_directory(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        assert store.work_dir(7) == store.document_dir(7) / "work"

    def test_does_not_create_anything(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        result = store.work_dir(7)

        assert not result.exists()
        assert not result.parent.exists()


class TestStoreFile:
    def test_copies_file_into_document_dir_and_returns_stored_path(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "incoming" / "notes.txt"
        src.parent.mkdir(parents=True)
        src.write_text("hello farabunker")

        stored_path = store.store_file(str(src), doc_id=5)

        expected = documents_dir / "5" / "notes.txt"
        assert stored_path == str(expected)
        assert expected.is_file()
        assert expected.read_text() == "hello farabunker"

    def test_leaves_original_source_file_untouched(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        src = tmp_path / "source.txt"
        src.write_text("original content")

        store.store_file(str(src), doc_id=1)

        assert src.is_file()
        assert src.read_text() == "original content"

    def test_creates_document_dir_if_missing(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir
        assert not documents_dir.exists()

        src = tmp_path / "file.md"
        src.write_text("# doc")

        store.store_file(str(src), doc_id=9)

        assert (documents_dir / "9").is_dir()

    def test_keeps_original_basename(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        src = tmp_path / "Weird Name (v2).PDF"
        src.write_bytes(b"%PDF-1.4 fake")

        stored_path = store.store_file(str(src), doc_id=3)

        assert Path(stored_path).name == "Weird Name (v2).PDF"


class TestMoveFile:
    def test_moves_file_into_document_dir_and_returns_stored_path(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "inbox" / "clip.mp4"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"fake video bytes")

        stored_path = store.move_file(str(src), doc_id=5)

        expected = documents_dir / "5" / "clip.mp4"
        assert stored_path == str(expected)
        assert expected.is_file()
        assert expected.read_bytes() == b"fake video bytes"

    def test_source_file_is_gone_after_the_move(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        src = tmp_path / "inbox" / "clip.mp3"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"fake audio bytes")

        store.move_file(str(src), doc_id=1)

        assert not src.exists()

    def test_creates_document_dir_if_missing(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir
        assert not documents_dir.exists()

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"bytes")

        store.move_file(str(src), doc_id=9)

        assert (documents_dir / "9").is_dir()

    def test_layout_matches_store_file(self, settings, tmp_path):
        """`move_file` and `store_file` write to the SAME layout -- only
        whether the source survives differs."""
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        copied_src = tmp_path / "a.txt"
        copied_src.write_text("a")
        moved_src = tmp_path / "b.txt"
        moved_src.write_text("b")

        copied_path = store.store_file(str(copied_src), doc_id=1)
        moved_path = store.move_file(str(moved_src), doc_id=2)

        assert Path(copied_path).parent.parent == Path(moved_path).parent.parent
        assert Path(copied_path) == documents_dir / "1" / "a.txt"
        assert Path(moved_path) == documents_dir / "2" / "b.txt"


class TestRemoveDocumentFiles:
    def test_deletes_the_document_directory_tree(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        doc_dir = documents_dir / "11"
        doc_dir.mkdir(parents=True)
        (doc_dir / "file.txt").write_text("content")

        store.remove_document_files(11)

        assert not doc_dir.exists()

    def test_safe_when_directory_does_not_exist(self, settings, tmp_path):
        settings.DOCUMENTS_DIR = tmp_path / "documents"

        # Should not raise even though nothing was ever stored for doc 99.
        store.remove_document_files(99)

    def test_does_not_touch_sibling_document_directories(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        keep_dir = documents_dir / "1"
        remove_dir = documents_dir / "2"
        keep_dir.mkdir(parents=True)
        remove_dir.mkdir(parents=True)
        (keep_dir / "keep.txt").write_text("keep me")

        store.remove_document_files(2)

        assert keep_dir.exists()
        assert not remove_dir.exists()


@pytest.mark.parametrize("module", ["ingest", "media", "models", "views"])
def test_no_production_module_spells_the_store_layout_by_hand(module):
    """C-18. Eight production sites re-spelled a path `store.py` owns --
    `"extract.json"` at five, `/ "work"` at three. All eight are now
    repointed to `store.sidecar_path`/`store.sidecar_path_for_dir`/
    `store.work_dir`. `tools.rag.ingest._source_documents` -- the one
    caller that resolves its sidecar from an already-resolved stored-file
    path rather than a `doc_id` -- goes through
    `store.sidecar_path_for_dir(path.parent)` instead of `store.sidecar_path`,
    but still through the store, not a bare literal.

    Test modules are exempt: a test naming the file it asserts about is
    documenting the layout, which is a legitimate thing for a test to do."""
    text = (Path(django_settings.BASE_DIR) / f"tools/rag/{module}.py").read_text()
    assert '/ "work"' not in text
    assert '"extract.json"' not in text
