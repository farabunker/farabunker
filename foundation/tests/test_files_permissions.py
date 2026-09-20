"""Unit tests for the S14/B-8 permission helpers in foundation/files.py --
`create_owner_only_dir` and `lock_down_file` -- pure functions, no Django DB,
no fixtures needed (same pattern as test_files.py's `TestSha256File`).

New module rather than an addition to test_files.py: these two helpers are
their own concern (directory/file permission discipline shared across every
writer -- backups, the managed document store, chat-attachment staging, the
inbox, vision uploads), separate from `sha256_file`'s hashing concern.
"""
from __future__ import annotations

import os
import stat

import pytest

from foundation.files import create_locked_file, create_owner_only_dir, lock_down_file


class TestCreateOwnerOnlyDir:
    def test_creates_a_missing_directory_at_0700(self, tmp_path):
        target = tmp_path / "fresh"
        assert not target.exists()

        result = create_owner_only_dir(target)

        assert result == target
        assert target.is_dir()
        assert stat.S_IMODE(target.stat().st_mode) == 0o700

    def test_creates_missing_parents_too(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"

        create_owner_only_dir(target)

        assert target.is_dir()
        assert stat.S_IMODE(target.stat().st_mode) == 0o700

    def test_tightens_an_already_existing_wider_directory(self, tmp_path):
        """`mode=` on `mkdir` is ignored once `exist_ok=True` finds the
        directory already there -- the explicit `chmod` is what actually
        guarantees 0700 for a directory some earlier, unpatched run (or
        anything else) left at a wider mode."""
        target = tmp_path / "already-here"
        target.mkdir(mode=0o755)

        create_owner_only_dir(target)

        assert stat.S_IMODE(target.stat().st_mode) == 0o700

    def test_does_not_widen_or_touch_existing_parent_modes(self, tmp_path):
        """Only the leaf directory is tightened -- the shared parents this
        is called with (a document store root, a data volume) are not
        themselves one document's/job's/backup's private data."""
        parent = tmp_path / "parent"
        parent.mkdir(mode=0o755)
        before = stat.S_IMODE(parent.stat().st_mode)

        create_owner_only_dir(parent / "child")

        assert stat.S_IMODE(parent.stat().st_mode) == before

    def test_is_idempotent(self, tmp_path):
        target = tmp_path / "twice"
        create_owner_only_dir(target)
        create_owner_only_dir(target)
        assert stat.S_IMODE(target.stat().st_mode) == 0o700


class TestCreateOwnerOnlyDirOnlyIfCreated:
    """H13 review round 1, finding 4: `only_if_created=True` -- for a
    caller sometimes handed a pre-existing SHARED directory it does not
    itself own."""

    def test_creates_and_tightens_a_missing_leaf(self, tmp_path):
        target = tmp_path / "fresh"

        create_owner_only_dir(target, only_if_created=True)

        assert target.is_dir()
        assert stat.S_IMODE(target.stat().st_mode) == 0o700

    def test_never_chmods_an_already_existing_leaf(self, tmp_path):
        target = tmp_path / "shared-root"
        target.mkdir()
        os.chmod(target, 0o755)

        create_owner_only_dir(target, only_if_created=True)

        assert stat.S_IMODE(target.stat().st_mode) == 0o755

    def test_default_behaviour_is_unchanged_without_the_flag(self, tmp_path):
        target = tmp_path / "owned-leaf"
        target.mkdir()
        os.chmod(target, 0o755)

        create_owner_only_dir(target)

        assert stat.S_IMODE(target.stat().st_mode) == 0o700


class TestLockDownFile:
    def test_tightens_a_freshly_written_file_to_0600(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_bytes(b"hello")

        result = lock_down_file(path)

        assert result == path
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_tightens_a_world_readable_file(self, tmp_path):
        path = tmp_path / "world.txt"
        path.write_bytes(b"hello")
        path.chmod(0o644)

        lock_down_file(path)

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            lock_down_file(tmp_path / "does-not-exist.txt")


class TestCreateLockedFile:
    def test_creates_a_fresh_file_at_0600(self, tmp_path):
        path = tmp_path / "a.bin"

        with create_locked_file(path) as f:
            f.write(b"hello")

        assert path.read_bytes() == b"hello"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_truncates_an_existing_file(self, tmp_path):
        path = tmp_path / "a.bin"
        path.write_bytes(b"old content, longer than the new one")

        with create_locked_file(path) as f:
            f.write(b"new")

        assert path.read_bytes() == b"new"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_usable_as_a_plain_write_handle_without_the_context_manager(self, tmp_path):
        path = tmp_path / "a.bin"
        f = create_locked_file(path)
        try:
            f.write(b"hello")
        finally:
            f.close()

        assert path.read_bytes() == b"hello"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
