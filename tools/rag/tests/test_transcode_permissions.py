"""Regression test for H13 review round 1, finding 10:
`tools.rag.transcode.slice_audio`'s `out_dir` -- a subdirectory of a
document's own scratch `work_dir` -- now goes through
`foundation.files.create_owner_only_dir` instead of a bare `mkdir`.

A NEW module -- `tools/rag/tests/` is a whole directory another session
edits concurrently (plan constraint 18) -- rather than an addition to
`test_transcode.py`. Same "fake `subprocess.run`, real everything else"
convention as that file (no real ffmpeg needed).
"""
from __future__ import annotations

import stat

from tools.rag import transcode


def _fake_completed(argv, returncode=0, stdout="", stderr=""):
    import subprocess

    return subprocess.CompletedProcess(argv, returncode=returncode, stdout=stdout, stderr=stderr)


class TestSliceAudioOutDirPermissions:
    def test_out_dir_is_owner_only(self, monkeypatch, tmp_path):
        out_dir = tmp_path / "slices"

        def fake_run(argv, **kwargs):
            (out_dir / "slice-00000.wav").write_bytes(b"")
            return _fake_completed(argv)

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)
        wav = tmp_path / "in.wav"

        transcode.slice_audio(wav, out_dir)

        assert stat.S_IMODE(out_dir.stat().st_mode) == 0o700

    def test_an_already_existing_wider_out_dir_is_tightened(self, monkeypatch, tmp_path):
        import os

        out_dir = tmp_path / "slices"
        out_dir.mkdir(mode=0o755)
        os.chmod(out_dir, 0o755)

        def fake_run(argv, **kwargs):
            return _fake_completed(argv)

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)
        wav = tmp_path / "in.wav"

        transcode.slice_audio(wav, out_dir)

        assert stat.S_IMODE(out_dir.stat().st_mode) == 0o700
