"""Unit tests for tools/rag/transcode.py -- the pure conversion layer
(media-into-RAG plan T5). Nothing outside this file imports `transcode`
yet -- T7/T8 are its first real callers.

Argv-correctness tests monkeypatch `subprocess.run` and assert the exact
argv list ffmpeg/ffprobe would receive -- this repo's "mock at the seam"
philosophy (elsewhere applied to HTTP calls) applied to the subprocess
boundary instead: never actually shells out, never depends on ffmpeg being
installed, just verifies the exact command this module WOULD run.

Real-ffmpeg tests are separately marked with
`@pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` and generate a
tiny WAV fixture by hand -- writing the 44-byte canonical RIFF/WAVE header
plus a run of silence samples directly (`tools.vision.store.
png_dimensions`'s "read the header yourself" instinct, applied to audio
bytes instead of PNG bytes) -- no audio library dependency for a test
fixture this small.
"""
from __future__ import annotations

import io
import shutil
import struct
import subprocess
import sys

import pytest

from tools.rag import transcode
from tools.rag.tests._helpers import make_pdf_bytes


# --- fixture builders --------------------------------------------------------


def _make_wav_bytes(*, seconds: float = 2.0, rate: int = 8000, channels: int = 1, bits: int = 16) -> bytes:
    """Hand-assemble a minimal valid WAV file: the canonical 44-byte
    RIFF/WAVE/fmt/data header followed by `seconds` of digital silence
    (all-zero PCM samples) -- enough for ffprobe/ffmpeg to treat it as
    real audio, without an audio-authoring library this repo doesn't
    otherwise need (the `png_dimensions` read-it-yourself instinct, see
    module docstring)."""
    n_samples = int(seconds * rate)
    bytes_per_sample = bits // 8
    data = b"\x00" * (n_samples * channels * bytes_per_sample)
    byte_rate = rate * channels * bytes_per_sample
    block_align = channels * bytes_per_sample
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def _fake_completed(argv, *, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode=returncode, stdout=stdout, stderr=stderr)


def _call_with_path(call: str, target, tmp_path):
    """Invoke `transcode.<call>` with fixture arguments sized for that
    function's own signature -- `target` is always the first positional
    argument, and is the file every one of the three functions' failure
    messages must name (`probe_duration(path)`, `extract_audio(src,
    dest_wav)`, `slice_audio(wav, out_dir)`)."""
    func = getattr(transcode, call)
    if call == "probe_duration":
        return func(target)
    return func(target, tmp_path / "out")


# --- C-31: the shared `_run_tool` wrapper's per-caller wording ---------------


@pytest.mark.parametrize(("call", "tool", "doing"), [
    ("probe_duration", "ffprobe", "probing the duration of"),
    ("extract_audio", "ffmpeg", "extracting audio from"),
    ("slice_audio", "ffmpeg", "slicing"),
])
def test_each_tool_failure_names_the_tool_the_action_and_the_file(monkeypatch, tmp_path, call, tool, doing):
    """C-31. Three functions share a subprocess wrapper (`_run_tool`) and
    MUST NOT share its words: "ffprobe failed probing the duration of
    /x.mp4" and "ffmpeg failed slicing /y.wav" send an operator to
    different places."""

    def fake_run(argv, **kwargs):
        return _fake_completed(argv, returncode=1, stderr="boom from the tool")

    monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(transcode.subprocess, "run", fake_run)
    target = tmp_path / "clip.media"

    with pytest.raises(RuntimeError) as exc_info:
        _call_with_path(call, target, tmp_path)

    message = str(exc_info.value)
    assert message.startswith(f"{tool} failed {doing} {target} (exit 1):")
    assert "boom from the tool" in message


@pytest.mark.parametrize(("call", "tool", "timeout_attr"), [
    ("probe_duration", "ffprobe", "PROBE_TIMEOUT_SECONDS"),
    ("extract_audio", "ffmpeg", "EXTRACT_TIMEOUT_SECONDS"),
    ("slice_audio", "ffmpeg", "EXTRACT_TIMEOUT_SECONDS"),
])
def test_each_timeout_names_its_own_timeout_constant(monkeypatch, tmp_path, call, tool, timeout_attr):
    """`PROBE_TIMEOUT_SECONDS` and `EXTRACT_TIMEOUT_SECONDS` are different
    numbers (30 vs 600) and the message says which one elapsed -- pins
    that `slice_audio` shares `extract_audio`'s constant (module
    docstring: segment muxing is a copy, not a decode, so it reuses the
    more generous bound rather than getting a third constant)."""

    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

    monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(transcode.subprocess, "run", fake_run)
    target = tmp_path / "clip.media"
    expected_timeout = getattr(transcode, timeout_attr)

    with pytest.raises(RuntimeError) as exc_info:
        _call_with_path(call, target, tmp_path)

    assert str(exc_info.value).startswith(f"{tool} timed out after {expected_timeout}s")
    # PROBE_TIMEOUT_SECONDS and EXTRACT_TIMEOUT_SECONDS are genuinely
    # different numbers -- a test that only matched "timed out" could not
    # tell a probe_duration message from an extract_audio one.
    assert transcode.PROBE_TIMEOUT_SECONDS != transcode.EXTRACT_TIMEOUT_SECONDS


# --- missing-binary guards ----------------------------------------------------


class TestMissingBinaryGuards:
    """Every entry point that shells out calls `_require_tool`/
    `require_ffmpeg` first -- a missing binary must surface as this
    module's own honest `RuntimeError`, never a bare `FileNotFoundError`
    escaping `subprocess.run`."""

    def test_require_ffmpeg_raises_when_missing(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
            transcode.require_ffmpeg()

    def test_require_ffmpeg_message_names_container_and_bare_host_install(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        with pytest.raises(RuntimeError) as exc_info:
            transcode.require_ffmpeg()
        message = str(exc_info.value)
        assert "Dockerfile" in message
        assert "brew install ffmpeg" in message or "apt-get install ffmpeg" in message

    def test_probe_duration_raises_when_ffprobe_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        with pytest.raises(RuntimeError, match="ffprobe is not installed"):
            transcode.probe_duration(tmp_path / "x.mp4")

    def test_extract_audio_raises_when_ffmpeg_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
            transcode.extract_audio(tmp_path / "x.mp4", tmp_path / "out.wav")

    def test_slice_audio_raises_when_ffmpeg_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(shutil, "which", lambda name: None)

        with pytest.raises(RuntimeError, match="ffmpeg is not installed"):
            transcode.slice_audio(tmp_path / "x.wav", tmp_path / "slices")


# --- probe_duration: argv correctness + output handling, subprocess mocked --


class TestProbeDurationArgv:
    def test_exact_argv_and_parses_stdout_as_float(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _fake_completed(argv, stdout="12.5\n")

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)
        path = tmp_path / "clip.mp4"

        result = transcode.probe_duration(path)

        assert result == 12.5
        assert captured["argv"] == [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(path),
        ]
        assert captured["kwargs"]["timeout"] == transcode.PROBE_TIMEOUT_SECONDS

    def test_nonzero_exit_raises_runtimeerror_with_stderr(self, monkeypatch, tmp_path):
        def fake_run(argv, **kwargs):
            return _fake_completed(argv, returncode=1, stderr="No such file or directory")

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="No such file or directory"):
            transcode.probe_duration(tmp_path / "missing.mp4")

    def test_malformed_stdout_raises_runtimeerror_never_a_guess(self, monkeypatch, tmp_path):
        def fake_run(argv, **kwargs):
            return _fake_completed(argv, stdout="N/A", stderr="")

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="unparseable"):
            transcode.probe_duration(tmp_path / "clip.mp4")

    def test_timeout_raises_runtimeerror(self, monkeypatch, tmp_path):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="timed out"):
            transcode.probe_duration(tmp_path / "clip.mp4")


# --- extract_audio: argv correctness, subprocess mocked ----------------------


class TestExtractAudioArgv:
    def test_exact_argv_16khz_mono_pcm_wav(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _fake_completed(argv)

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)
        src = tmp_path / "in.mp4"
        dest = tmp_path / "out.wav"

        result = transcode.extract_audio(src, dest)

        assert result == dest
        assert captured["argv"] == [
            "ffmpeg",
            "-nostdin",
            "-v", "error",
            "-y",
            "-i", str(src),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            "-f", "wav",
            str(dest),
        ]
        assert captured["kwargs"]["timeout"] == transcode.EXTRACT_TIMEOUT_SECONDS

    def test_nonzero_exit_raises_runtimeerror_with_stderr(self, monkeypatch, tmp_path):
        def fake_run(argv, **kwargs):
            return _fake_completed(argv, returncode=1, stderr="Invalid data found when processing input")

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="Invalid data found"):
            transcode.extract_audio(tmp_path / "bad.mp4", tmp_path / "out.wav")

    def test_timeout_raises_runtimeerror(self, monkeypatch, tmp_path):
        def fake_run(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="timed out"):
            transcode.extract_audio(tmp_path / "in.mp4", tmp_path / "out.wav")


# --- slice_audio: argv correctness, subprocess mocked -------------------------


class TestSliceAudioArgv:
    def test_exact_argv_default_window(self, monkeypatch, tmp_path):
        captured = {}
        out_dir = tmp_path / "slices"

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            # Simulate ffmpeg's segment muxer actually writing output.
            (out_dir / "slice-00000.wav").write_bytes(b"")
            (out_dir / "slice-00001.wav").write_bytes(b"")
            return _fake_completed(argv)

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)
        wav = tmp_path / "in.wav"

        result = transcode.slice_audio(wav, out_dir)

        pattern = out_dir / "slice-%05d.wav"
        assert captured["argv"] == [
            "ffmpeg",
            "-nostdin",
            "-v", "error",
            "-y",
            "-i", str(wav),
            "-f", "segment",
            "-segment_time", str(transcode.DEFAULT_SLICE_SECONDS),
            "-c", "copy",
            str(pattern),
        ]
        assert result == [out_dir / "slice-00000.wav", out_dir / "slice-00001.wav"]

    def test_custom_window_seconds_is_forwarded(self, monkeypatch, tmp_path):
        captured = {}
        out_dir = tmp_path / "slices"

        def fake_run(argv, **kwargs):
            captured["argv"] = argv
            return _fake_completed(argv)

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        transcode.slice_audio(tmp_path / "in.wav", out_dir, window_seconds=60)

        assert "-segment_time" in captured["argv"]
        assert captured["argv"][captured["argv"].index("-segment_time") + 1] == "60"

    def test_nonzero_exit_raises_runtimeerror_with_stderr(self, monkeypatch, tmp_path):
        def fake_run(argv, **kwargs):
            return _fake_completed(argv, returncode=1, stderr="segment muxer error")

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        with pytest.raises(RuntimeError, match="segment muxer error"):
            transcode.slice_audio(tmp_path / "in.wav", tmp_path / "slices")

    def test_returned_paths_are_sorted(self, monkeypatch, tmp_path):
        out_dir = tmp_path / "slices"

        def fake_run(argv, **kwargs):
            (out_dir / "slice-00002.wav").write_bytes(b"")
            (out_dir / "slice-00000.wav").write_bytes(b"")
            (out_dir / "slice-00001.wav").write_bytes(b"")
            return _fake_completed(argv)

        monkeypatch.setattr(transcode.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(transcode.subprocess, "run", fake_run)

        result = transcode.slice_audio(tmp_path / "in.wav", out_dir)

        assert result == [
            out_dir / "slice-00000.wav",
            out_dir / "slice-00001.wav",
            out_dir / "slice-00002.wav",
        ]


# --- real ffmpeg: only when the binary is actually on PATH -------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed on this host")
class TestRealFfmpeg:
    def test_probe_duration_returns_approximately_the_expected_duration(self, tmp_path):
        wav = tmp_path / "silence.wav"
        wav.write_bytes(_make_wav_bytes(seconds=2.0, rate=8000))

        duration = transcode.probe_duration(wav)

        assert abs(duration - 2.0) < 0.1

    def test_extract_audio_produces_16khz_mono_pcm_wav(self, tmp_path):
        # Deliberately a different rate/channel-count source (8kHz stereo)
        # so the assertion below proves normalization happened, not that
        # the input already matched the target format.
        src = tmp_path / "source.wav"
        src.write_bytes(_make_wav_bytes(seconds=1.0, rate=8000, channels=2))
        dest = tmp_path / "extracted.wav"

        result = transcode.extract_audio(src, dest)

        assert result == dest
        header = dest.read_bytes()[:44]
        assert header[:4] == b"RIFF"
        assert header[8:12] == b"WAVE"
        (channels,) = struct.unpack("<H", header[22:24])
        (rate,) = struct.unpack("<I", header[24:28])
        (bits,) = struct.unpack("<H", header[34:36])
        assert channels == 1
        assert rate == 16000
        assert bits == 16

    def test_slice_audio_on_two_second_wav_with_one_second_window_yields_two_ordered_files(self, tmp_path):
        wav = tmp_path / "silence.wav"
        wav.write_bytes(_make_wav_bytes(seconds=2.0, rate=8000))
        out_dir = tmp_path / "slices"

        slices = transcode.slice_audio(wav, out_dir, window_seconds=1)

        assert len(slices) == 2
        assert slices == sorted(slices)
        for path in slices:
            assert path.is_file()
            assert path.read_bytes()[:4] == b"RIFF"


# --- rasterize_pdf_page -------------------------------------------------------


class TestRasterizePdfPage:
    def test_missing_pypdfium2_raises_runtimeerror(self, monkeypatch, tmp_path):
        monkeypatch.setitem(sys.modules, "pypdfium2", None)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(make_pdf_bytes(["hello"]))

        with pytest.raises(RuntimeError, match="pypdfium2 is not installed"):
            transcode.rasterize_pdf_page(pdf, 1)

    def test_missing_pillow_raises_runtimeerror(self, monkeypatch, tmp_path):
        monkeypatch.setitem(sys.modules, "PIL", None)
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(make_pdf_bytes(["hello"]))

        with pytest.raises(RuntimeError, match="Pillow is not installed"):
            transcode.rasterize_pdf_page(pdf, 1)

    def test_renders_a_valid_png(self, tmp_path):
        from PIL import Image

        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(make_pdf_bytes(["hello page one"]))

        png_bytes = transcode.rasterize_pdf_page(pdf, 1)

        assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(png_bytes))
        assert img.format == "PNG"
        width, height = img.size
        assert width > 0
        assert height > 0

    def test_max_edge_is_honored_when_downscaling(self, tmp_path):
        from PIL import Image

        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(make_pdf_bytes(["hello page one"]))

        png_bytes = transcode.rasterize_pdf_page(pdf, 1, max_edge=50)

        img = Image.open(io.BytesIO(png_bytes))
        assert max(img.size) <= 50

    def test_page_number_is_one_based(self, tmp_path):
        pdf = tmp_path / "two-page.pdf"
        pdf.write_bytes(make_pdf_bytes(["page one", "page two"]))

        # Both pages render without error under 1-based numbering.
        first = transcode.rasterize_pdf_page(pdf, 1)
        second = transcode.rasterize_pdf_page(pdf, 2)

        assert first[:8] == b"\x89PNG\r\n\x1a\n"
        assert second[:8] == b"\x89PNG\r\n\x1a\n"

    def test_page_number_zero_raises_value_error(self, tmp_path):
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(make_pdf_bytes(["hello"]))

        with pytest.raises(ValueError, match="out of range"):
            transcode.rasterize_pdf_page(pdf, 0)

    def test_page_number_past_the_end_raises_value_error(self, tmp_path):
        pdf = tmp_path / "x.pdf"
        pdf.write_bytes(make_pdf_bytes(["hello"]))

        with pytest.raises(ValueError, match="out of range"):
            transcode.rasterize_pdf_page(pdf, 2)


# --- normalize_image ----------------------------------------------------------


class TestNormalizeImage:
    def test_missing_pillow_raises_runtimeerror(self, monkeypatch, tmp_path):
        monkeypatch.setitem(sys.modules, "PIL", None)
        src = tmp_path / "x.png"
        src.write_bytes(b"not even a real png -- guard fires before this is read")

        with pytest.raises(RuntimeError, match="Pillow is not installed"):
            transcode.normalize_image(src)

    def test_small_png_round_trips_as_png(self, tmp_path):
        from PIL import Image

        src = tmp_path / "small.png"
        Image.new("RGB", (20, 10), (10, 20, 30)).save(src)

        out_bytes = transcode.normalize_image(src)

        assert out_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(out_bytes))
        assert img.size == (20, 10)
        assert img.mode == "RGB"

    def test_oversized_image_is_downscaled(self, tmp_path):
        from PIL import Image

        src = tmp_path / "big.png"
        Image.new("RGB", (3000, 100), (0, 0, 0)).save(src)

        out_bytes = transcode.normalize_image(src, max_edge=500)

        img = Image.open(io.BytesIO(out_bytes))
        assert max(img.size) <= 500
        # Aspect ratio preserved (30:1) within rounding.
        assert img.size[0] > img.size[1] * 20

    def test_undersized_image_is_never_upscaled(self, tmp_path):
        from PIL import Image

        src = tmp_path / "tiny.png"
        Image.new("RGB", (20, 10), (0, 0, 0)).save(src)

        out_bytes = transcode.normalize_image(src, max_edge=1600)

        img = Image.open(io.BytesIO(out_bytes))
        assert img.size == (20, 10)

    def test_rgba_is_converted_to_rgb(self, tmp_path):
        from PIL import Image

        src = tmp_path / "rgba.png"
        Image.new("RGBA", (20, 10), (10, 20, 30, 128)).save(src)

        out_bytes = transcode.normalize_image(src)

        img = Image.open(io.BytesIO(out_bytes))
        assert img.mode == "RGB"


# --- _downscale_to_png (audit-approved consolidation) ------------------------
#
# `rasterize_pdf_page`'s own `test_max_edge_is_honored_when_downscaling` and
# `normalize_image`'s own `test_oversized_image_is_downscaled`/
# `test_undersized_image_is_never_upscaled` above already exercise this
# helper indirectly, through both public entry points that now share it.
# This one test exercises `_downscale_to_png` directly, so a future
# change to its own downscale/encode logic is caught here without having to
# reason through either caller's own PDF/image-loading machinery.
# `test_returns_valid_png_bytes` below is a smoke test for the same PNG
# encoding already covered by `rasterize_pdf_page`'s own
# `test_renders_a_valid_png` and `normalize_image`'s own
# `test_small_png_round_trips_as_png`.


class TestDownscaleToPng:
    def test_downscales_oversized_image_never_upscales_undersized_one(self, tmp_path):
        from PIL import Image

        oversized = Image.new("RGB", (2000, 1000), (1, 2, 3))
        out_bytes = transcode._downscale_to_png(oversized, 500, Image)
        out_img = Image.open(io.BytesIO(out_bytes))
        assert max(out_img.size) <= 500
        # Aspect ratio preserved (2:1).
        assert out_img.size[0] == out_img.size[1] * 2

        undersized = Image.new("RGB", (20, 10), (1, 2, 3))
        out_bytes = transcode._downscale_to_png(undersized, 1600, Image)
        out_img = Image.open(io.BytesIO(out_bytes))
        assert out_img.size == (20, 10)

    def test_returns_valid_png_bytes(self, tmp_path):
        from PIL import Image

        img = Image.new("RGB", (10, 10), (5, 6, 7))
        out_bytes = transcode._downscale_to_png(img, 1600, Image)

        assert out_bytes[:8] == b"\x89PNG\r\n\x1a\n"


# --- ffprobe_available --------------------------------------------------------


class TestFfprobeAvailable:
    def test_true_when_ffprobe_is_on_path(self, monkeypatch):
        monkeypatch.setattr(transcode.shutil, "which", lambda binary: "/usr/bin/ffprobe")
        assert transcode.ffprobe_available() is True

    def test_false_when_ffprobe_is_missing(self, monkeypatch):
        monkeypatch.setattr(transcode.shutil, "which", lambda binary: None)
        assert transcode.ffprobe_available() is False
