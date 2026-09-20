"""B-3 (round-3 hardening): `foundation.files.create_locked_file` refuses to
write through a pre-planted symlink instead of following it.

New module, not an addition to `test_files_permissions.py` (this task's own
addendum: new test modules under `foundation/tests`, not appended existing
ones) -- pure functions, no Django DB, same "no fixtures needed" shape as
that file's own `TestCreateLockedFile`.
"""
from __future__ import annotations

import stat

import pytest

from foundation.files import SymlinkRefused, create_locked_file


class TestCreateLockedFileRefusesASymlink:
    def test_a_symlink_at_the_destination_is_refused_not_followed(self, tmp_path):
        target = tmp_path / "outside" / "victim.txt"
        target.parent.mkdir()
        target.write_bytes(b"secret")
        link = tmp_path / "dest.bin"
        link.symlink_to(target)

        with pytest.raises(SymlinkRefused):
            create_locked_file(link)

        assert target.read_bytes() == b"secret"  # untouched, never opened

    def test_the_refusal_names_the_path(self, tmp_path):
        link = tmp_path / "dest.bin"
        link.symlink_to(tmp_path / "elsewhere.txt")

        with pytest.raises(SymlinkRefused, match=str(link)):
            create_locked_file(link)

    def test_symlink_refused_is_an_os_error(self):
        """Every existing broad `except OSError` around a `create_locked_file`
        write (e.g. `foundation.ops.backup._copy_and_hash`) keeps catching
        this without a code change."""
        assert issubclass(SymlinkRefused, OSError)

    def test_an_ordinary_pre_existing_file_is_still_truncated_not_refused(self, tmp_path):
        """The O_TRUNC/O_EXCL trade-off this helper's own docstring records:
        a REGULAR file already at the destination (the ordinary re-stage/
        re-upload case) is still opened and truncated, never refused --
        only a SYMLINK is."""
        path = tmp_path / "dest.bin"
        path.write_bytes(b"old content")

        with create_locked_file(path) as f:
            f.write(b"new")

        assert path.read_bytes() == b"new"

    def test_a_symlink_pointing_nowhere_is_still_refused(self, tmp_path):
        """A dangling symlink is still a symlink -- `O_NOFOLLOW` rejects the
        leaf itself, independent of whether its target exists."""
        link = tmp_path / "dangling.bin"
        link.symlink_to(tmp_path / "does-not-exist.txt")

        with pytest.raises(SymlinkRefused):
            create_locked_file(link)

    def test_a_fresh_file_is_unaffected(self, tmp_path):
        path = tmp_path / "fresh.bin"

        with create_locked_file(path) as f:
            f.write(b"hello")

        assert path.read_bytes() == b"hello"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
