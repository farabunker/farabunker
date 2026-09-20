"""Unit tests for the S14/B-8 permission discipline in tools/rag/store.py
(`store_file`/`move_file`) -- a NEW module rather than an addition to
test_store.py: `tools/rag/tests/` is a whole directory another session is
editing concurrently (plan constraint 18), so this task adds a new file
instead of touching an existing one.

Same fixture pattern as test_store.py: pure filesystem behavior against a
temporary `settings.DOCUMENTS_DIR`, no database.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from tools.rag import store


class TestStoreFilePermissions:
    def test_document_dir_is_owner_only(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "report.txt"
        src.write_bytes(b"hello")

        store.store_file(str(src), doc_id=1)

        assert stat.S_IMODE((documents_dir / "1").stat().st_mode) == 0o700

    def test_copied_file_is_owner_read_write_only(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "report.txt"
        src.write_bytes(b"hello")

        stored = store.store_file(str(src), doc_id=1)

        assert stat.S_IMODE(Path(stored).stat().st_mode) == 0o600

    def test_copy_tightens_a_world_readable_source(self, settings, tmp_path):
        """`shutil.copy2` carries the SOURCE's mode across -- a
        world-readable source must not leave the copy world-readable."""
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "report.txt"
        src.write_bytes(b"hello")
        os.chmod(src, 0o644)

        stored = store.store_file(str(src), doc_id=1)

        assert stat.S_IMODE(Path(stored).stat().st_mode) == 0o600


class TestMoveFilePermissions:
    def test_document_dir_is_owner_only(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"bytes")

        store.move_file(str(src), doc_id=1)

        assert stat.S_IMODE((documents_dir / "1").stat().st_mode) == 0o700

    def test_moved_file_is_owner_read_write_only(self, settings, tmp_path):
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"bytes")

        stored = store.move_file(str(src), doc_id=1)

        assert stat.S_IMODE(Path(stored).stat().st_mode) == 0o600

    def test_move_tightens_a_world_readable_source(self, settings, tmp_path):
        """A same-filesystem `shutil.move` is a rename -- it carries the
        SOURCE's mode across just like `copy2`."""
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"bytes")
        os.chmod(src, 0o644)

        stored = store.move_file(str(src), doc_id=1)

        assert stat.S_IMODE(Path(stored).stat().st_mode) == 0o600

    def test_existing_wider_document_dir_is_tightened_on_reuse(self, settings, tmp_path):
        """`mode=` on `mkdir` is ignored once `exist_ok=True` finds the
        directory already there -- a second file moved into the same
        document (already created at a wider mode by, say, an earlier
        unpatched run) must still end up 0700."""
        documents_dir = tmp_path / "documents"
        settings.DOCUMENTS_DIR = documents_dir
        doc_dir = documents_dir / "1"
        doc_dir.mkdir(parents=True, mode=0o755)

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"bytes")

        store.move_file(str(src), doc_id=1)

        assert stat.S_IMODE(doc_dir.stat().st_mode) == 0o700
