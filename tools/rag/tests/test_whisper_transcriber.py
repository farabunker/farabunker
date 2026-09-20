"""Unit tests for models/contracts/engines/whisper.py's `WhisperTranscriber`
and `_parse_transcript` (media-into-RAG plan T6).

HTTP is mocked at the `httpx` layer (`models.contracts.engines.whisper.httpx.post`),
never `WhisperTranscriber.transcribe` itself, so the adapter's real request-
building and response-parsing logic runs. `transcribe()` still opens
`audio_path` for real (to build the multipart upload) -- the file's bytes
are never inspected by the mocked HTTP layer, so an arbitrary small file
written by `tmp_path` stands in for it. NO audio fixtures at this layer:
nothing here decodes, probes, or cares what the bytes actually are -- that
is `tools.rag.transcode`'s job, tested in test_transcode.py, not this
module's.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import httpx
import pytest

from models.contracts.engines.base import GenerationRejected, TranscriptResult, TranscriptSegment
from models.contracts.engines.whisper import (
    DEFAULT_TRANSCRIBE_TIMEOUT,
    WhisperTranscriber,
    _parse_transcript,
)
from tools.rag.tests._helpers import fake_whisper_post

ENDPOINT = "http://whisper.local:8080"


@pytest.fixture
def audio_path(tmp_path):
    """An arbitrary small local file standing in for an audio slice --
    `transcribe()` opens it to build the multipart upload, but the mocked
    HTTP layer never inspects its bytes (see module docstring)."""
    path = tmp_path / "slice-00000.wav"
    path.write_bytes(b"not-decoded-by-anything-in-this-test")
    return path


# --- request shape --------------------------------------------------------


class TestRequestShape:
    def test_multipart_field_is_named_file_with_audio_wav_media_type(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["files"] = files
            return fake_whisper_post(payload={"text": "hello"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "ggml-base.en.bin").transcribe(audio_path)

        field_name, (filename, handle, media_type) = next(iter(captured["files"].items()))
        assert field_name == "file"
        assert filename == audio_path.name
        assert media_type == "audio/wav"

    def test_posts_to_the_inference_path(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["url"] = url
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

        assert captured["url"] == f"{ENDPOINT}/inference"

    def test_response_format_is_always_verbose_json(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

        assert captured["data"]["response_format"] == "verbose_json"

    def test_no_language_key_when_none_supplied_and_none_configured(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

        assert "language" not in captured["data"]

    def test_caller_supplied_language_is_sent(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path, language="es")

        assert captured["data"]["language"] == "es"

    def test_config_language_is_used_when_caller_passes_none(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m", config={"language": "fr"}).transcribe(audio_path)

        assert captured["data"]["language"] == "fr"

    def test_caller_supplied_language_wins_over_config(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m", config={"language": "fr"}).transcribe(
                audio_path, language="es"
            )

        assert captured["data"]["language"] == "es"

    def test_no_temperature_or_beam_size_or_any_other_tunable_is_ever_sent(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(
                ENDPOINT, "m", config={"temperature": 0.5, "beam_size": 5}
            ).transcribe(audio_path)

        assert set(captured["data"]) <= {"response_format", "language"}
        assert "temperature" not in captured["data"]
        assert "beam_size" not in captured["data"]

    def test_model_id_never_appears_in_files_or_data(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["files"] = files
            captured["data"] = data
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "ggml-base.en.bin").transcribe(audio_path)

        assert "model" not in captured["data"]
        assert "ggml-base.en.bin" not in captured["data"].values()
        assert all(
            "ggml-base.en.bin" not in str(value) for value in captured["files"].values()
        )


class TestTimeout:
    def test_defaults_to_default_transcribe_timeout(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["timeout"] = timeout
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

        assert captured["timeout"] == DEFAULT_TRANSCRIBE_TIMEOUT

    def test_config_request_timeout_is_honored(self, audio_path):
        captured = {}

        def _post(url, files=None, data=None, timeout=None):
            captured["timeout"] = timeout
            return fake_whisper_post(payload={"text": "hi"})(url, files, data, timeout)

        with patch("models.contracts.engines.whisper.httpx.post", _post):
            WhisperTranscriber(ENDPOINT, "m", config={"request_timeout": 42.0}).transcribe(
                audio_path
            )

        assert captured["timeout"] == 42.0


class TestErrorHandling:
    def test_non_2xx_with_body_raises_generation_rejected_with_server_words(self, audio_path):
        fake = fake_whisper_post(status_code=400, text="invalid audio format")
        with patch("models.contracts.engines.whisper.httpx.post", fake):
            with pytest.raises(GenerationRejected, match="invalid audio format"):
                WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

    def test_non_2xx_with_no_body_falls_back_to_the_status_code(self, audio_path):
        fake = fake_whisper_post(status_code=500, text="")
        with patch("models.contracts.engines.whisper.httpx.post", fake):
            with pytest.raises(GenerationRejected, match="HTTP 500"):
                WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

    def test_transport_failure_propagates_raw(self, audio_path):
        with patch(
            "models.contracts.engines.whisper.httpx.post",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            with pytest.raises(httpx.ConnectError):
                WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)


class TestTranscribeReturnsParsedResult:
    def test_returns_a_transcript_result_built_from_the_response(self, audio_path):
        payload = {
            "segments": [{"start": 0.0, "end": 1.5, "text": "hello"}],
            "language": "en",
            "duration": 1.5,
        }
        fake = fake_whisper_post(payload=payload)
        with patch("models.contracts.engines.whisper.httpx.post", fake):
            result = WhisperTranscriber(ENDPOINT, "m").transcribe(audio_path)

        assert isinstance(result, TranscriptResult)
        assert result.text == "hello"
        assert result.language == "en"
        assert result.duration == 1.5


# --- _parse_transcript: shape-defensive parsing ---------------------------


class TestParseTranscriptSecondsShape:
    def test_numeric_start_end_segments_parse_directly(self):
        payload = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "one"},
                {"start": 2.0, "end": 4.5, "text": "two"},
            ],
            "language": "en",
            "duration": 4.5,
        }

        result = _parse_transcript(payload)

        assert result.segments == (
            TranscriptSegment(start=0.0, end=2.0, text="one"),
            TranscriptSegment(start=2.0, end=4.5, text="two"),
        )
        assert result.language == "en"
        assert result.duration == 4.5


class TestParseTranscriptOffsetsShape:
    def test_offsets_from_to_in_milliseconds_convert_to_seconds(self):
        payload = {
            "segments": [
                {"offsets": {"from": 0, "to": 1500}, "text": "one"},
                {"offsets": {"from": 1500, "to": 3000}, "text": "two"},
            ],
        }

        result = _parse_transcript(payload)

        assert result.segments == (
            TranscriptSegment(start=0.0, end=1.5, text="one"),
            TranscriptSegment(start=1.5, end=3.0, text="two"),
        )


class TestParseTranscriptSkipsMalformedEntriesButKeepsGoodOnes:
    """T6 review: a malformed INDIVIDUAL segment entry must not discard
    every good segment around it -- one bad entry costs its own segment
    (the `readers._read_pdf` skip-the-bad-page precedent), logged once per
    skipped entry, never the whole response falling back to the all-or-
    nothing `None` that used to send it past this branch entirely."""

    def test_good_garbage_good_keeps_the_two_good_segments_and_logs_once(self, caplog):
        payload = {
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "one"},
                {"weird": "shape"},
                {"start": 1.0, "end": 2.0, "text": "two"},
            ],
        }

        with caplog.at_level(logging.WARNING, logger="models.contracts.engines.whisper"):
            result = _parse_transcript(payload)

        assert result.segments == (
            TranscriptSegment(start=0.0, end=1.0, text="one"),
            TranscriptSegment(start=1.0, end=2.0, text="two"),
        )
        assert len(caplog.records) == 1
        assert "skipping" in caplog.records[0].getMessage().lower()

    def test_good_garbage_good_in_the_offsets_shape_also_keeps_the_two_good_segments(self, caplog):
        payload = {
            "segments": [
                {"offsets": {"from": 0, "to": 1000}, "text": "one"},
                {"offsets": {"from": "not-a-number", "to": 2000}, "text": "bad"},
                {"offsets": {"from": 2000, "to": 3000}, "text": "two"},
            ],
        }

        with caplog.at_level(logging.WARNING, logger="models.contracts.engines.whisper"):
            result = _parse_transcript(payload)

        assert result.segments == (
            TranscriptSegment(start=0.0, end=1.0, text="one"),
            TranscriptSegment(start=2.0, end=3.0, text="two"),
        )


class TestParseTranscriptTextOnlyFallback:
    def test_bare_text_with_no_segments_yields_one_segment(self):
        payload = {"text": "just some words", "duration": 3.0}

        result = _parse_transcript(payload)

        assert result.segments == (TranscriptSegment(start=0.0, end=3.0, text="just some words"),)

    def test_bare_text_with_no_duration_uses_zero_zero(self):
        payload = {"text": "just some words"}

        result = _parse_transcript(payload)

        assert result.segments == (TranscriptSegment(start=0.0, end=0.0, text="just some words"),)

    def test_unparseable_segments_still_fall_back_to_text(self):
        """`segments` present but in a shape neither branch recognizes must
        not discard the transcript text."""
        payload = {"segments": [{"weird": "shape"}], "text": "the real transcript"}

        result = _parse_transcript(payload)

        assert result.text == "the real transcript"


class TestParseTranscriptEmpty:
    def test_neither_segments_nor_text_yields_an_empty_result(self):
        result = _parse_transcript({})

        assert result.segments == ()
        assert result.text == ""
        assert result.language is None
        assert result.duration is None

    def test_blank_text_is_treated_as_no_text(self):
        result = _parse_transcript({"text": "   "})

        assert result.segments == ()

    def test_empty_segments_list_falls_through_to_text(self):
        result = _parse_transcript({"segments": [], "text": "fallback text"})

        assert result.text == "fallback text"


class TestParseTranscriptNeverFabricates:
    def test_language_and_duration_absent_stay_none(self):
        result = _parse_transcript({"segments": [{"start": 0.0, "end": 1.0, "text": "x"}]})

        assert result.language is None
        assert result.duration is None

    def test_non_string_language_is_ignored_not_coerced(self):
        result = _parse_transcript({"text": "x", "language": 123})

        assert result.language is None
