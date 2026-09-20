"""Unit tests for foundation/files.py (W3) -- pure functions, no Django DB, no
fixtures needed.

Collected the same way as any other column's tests: `foundation` is one of
`pytest.ini`'s `testpaths` entries (`tools models foundation agents
scripts`) -- see this repo's own gate commands -- so the bare `pytest -q`
and the reversed `pytest -q scripts agents foundation models tools` both
sweep these tests in like any other, with no special-casing needed.
"""
from __future__ import annotations

import hashlib

import pytest

from foundation.files import _BLOCK_SIZE, sha256_file


class TestSha256File:
    def test_matches_hashlib_directly(self, tmp_path):
        path = tmp_path / "a.txt"
        path.write_bytes(b"hello world")
        assert sha256_file(path) == hashlib.sha256(b"hello world").hexdigest()

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.txt"
        path.write_bytes(b"")
        assert sha256_file(path) == hashlib.sha256(b"").hexdigest()

    def test_accepts_str_path(self, tmp_path):
        path = tmp_path / "b.txt"
        path.write_bytes(b"same bytes")
        assert sha256_file(str(path)) == sha256_file(path)

    def test_larger_than_one_block(self, tmp_path):
        """A file spanning multiple 1 MiB blocks still hashes to the same
        digest as hashing the bytes in one shot -- the chunked read must
        never drop or duplicate a byte at a block boundary."""
        data = b"x" * (_BLOCK_SIZE + 12345)
        path = tmp_path / "big.bin"
        path.write_bytes(data)
        assert sha256_file(path) == hashlib.sha256(data).hexdigest()

    def test_missing_file_raises_oserror(self, tmp_path):
        with pytest.raises(OSError):
            sha256_file(tmp_path / "does-not-exist.txt")
