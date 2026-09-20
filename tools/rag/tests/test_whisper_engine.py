"""Unit tests for models/contracts/engines/whisper.py's `WhisperEngine` half
(media-into-RAG plan T6).

`TestIsHealthy` mocks at the READER layer
(`models.contracts.engines.whisper.get_bounded`, S11/H11) -- never the
engine's own methods, and never `httpx.get` (which `is_healthy` no longer
calls at all) -- so the adapter's real health-check/marker-sniff logic runs
against bytes a double controls. A test that still patched `httpx.get` here
would silently exercise nothing: the patch is never consulted, and
`get_bounded` would open a REAL connection to whatever host the test names
(review round 1 caught exactly this on two tests that only passed because
`whisper.local` fails to resolve on the box running them). `TestTranscribe`
(transcription) still mocks at the `httpx` layer, since `WhisperTranscriber.
transcribe` calls `httpx.post` directly -- see test_whisper_transcriber.py's
own module docstring. No audio fixtures at this layer: everything is
HTTP-mocked. No DB.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from models.contracts.engines import ENGINES, get_engine
from models.contracts.engines.base import SETUP_PLATFORMS
from models.contracts.engines.whisper import DISCOVERY_TIMEOUT, WhisperEngine
from tools.rag.tests._helpers import fake_whisper_get

ENDPOINT = "http://whisper.local:8080"


class TestRegistrationAndMetadata:
    def test_registered_under_its_own_name(self):
        assert isinstance(get_engine("whisper"), WhisperEngine)
        assert "whisper" in ENGINES

    def test_console_facing_metadata(self):
        engine = WhisperEngine()
        assert engine.name == "whisper"
        assert engine.api_description == "the whisper.cpp server HTTP API"
        assert engine.library_url == "https://huggingface.co/ggerganov/whisper.cpp"
        assert engine.well_known_ports == (8080,)

    def test_it_declares_the_capability_it_serves(self):
        assert WhisperEngine.serves_capabilities == ("transcription",)


class TestInstallCmdTemplate:
    """Unlike every other adapter, the start command IS the model choice --
    `-m <model-file>` -- so there is no bare `<model-name>` placeholder to
    fill in separately; this asserts no model NAME (a real file/model
    identifier) sneaks into the template, only the placeholder shape."""

    def test_the_start_command_names_no_model_only_a_placeholder(self):
        template = WhisperEngine().install_cmd_template
        assert template == "whisper-server -m <model-file> --host 0.0.0.0 --port 8080"
        assert "<model-file>" in template
        assert "--host 0.0.0.0" in template
        assert "--port 8080" in template


class TestIsHealthy:
    def test_true_when_root_answers_200_with_the_marker(self):
        # H11 (S11): `is_healthy` reads through `engine_http.get_bounded`,
        # imported into `whisper.py`'s own namespace as `get_bounded` --
        # not through `httpx.get` any more -- so the double patches that
        # name, not the `httpx` layer, for this class only.
        with patch(
            "models.contracts.engines.whisper.get_bounded",
            return_value=b"<html>whisper.cpp server</html>",
        ):
            assert WhisperEngine().is_healthy(ENDPOINT) is True

    def test_false_on_200_without_the_marker(self):
        """A different local dev server squatting on 8080 must not read as
        whisper.cpp just because it answers 200 -- V1's whole reason for
        existing."""
        fake = fake_whisper_get(status_code=200, body="<html>some other dev server</html>")
        with patch("models.contracts.engines.whisper.get_bounded", fake):
            assert WhisperEngine().is_healthy(ENDPOINT) is False

    def test_marker_check_is_case_insensitive(self):
        with patch(
            "models.contracts.engines.whisper.get_bounded", return_value=b"WHISPER.CPP SERVER"
        ):
            assert WhisperEngine().is_healthy(ENDPOINT) is True

    def test_false_on_non_200_even_with_the_marker_in_body(self):
        fake = fake_whisper_get(status_code=500, body="whisper.cpp server error")
        with patch("models.contracts.engines.whisper.get_bounded", fake):
            assert WhisperEngine().is_healthy(ENDPOINT) is False

    def test_false_on_connect_error(self):
        """A transport failure raised BY THE READER (`get_bounded`, not
        `httpx.get` directly -- `is_healthy` never calls `httpx.get` any
        more) must still read as unhealthy, not propagate."""
        with patch(
            "models.contracts.engines.whisper.get_bounded",
            side_effect=httpx.ConnectError("connection refused"),
        ):
            assert WhisperEngine().is_healthy(ENDPOINT) is False

    def test_empty_marker_disables_the_sniff(self, monkeypatch):
        """Setting `_HEALTH_MARKER` to `""` degrades to a bare 200 check --
        documented escape hatch for an operator whose build changed the
        status page's copy."""
        import models.contracts.engines.whisper as whisper_module

        monkeypatch.setattr(whisper_module, "_HEALTH_MARKER", "")
        with patch(
            "models.contracts.engines.whisper.get_bounded", return_value=b"anything at all"
        ):
            assert WhisperEngine().is_healthy(ENDPOINT) is True

    def test_default_timeout_is_discovery_timeout(self):
        seen = {}

        def _fake_get_bounded(url, *, timeout, params=None, max_bytes=None, client=None, truncate=False):
            seen["timeout"] = timeout
            return b"whisper.cpp server ready"

        with patch("models.contracts.engines.whisper.get_bounded", _fake_get_bounded):
            WhisperEngine().is_healthy(ENDPOINT)
        assert seen["timeout"] == DISCOVERY_TIMEOUT

    def test_caller_supplied_timeout_is_used(self):
        seen = {}

        def _fake_get_bounded(url, *, timeout, params=None, max_bytes=None, client=None, truncate=False):
            seen["timeout"] = timeout
            return b"whisper.cpp server ready"

        with patch("models.contracts.engines.whisper.get_bounded", _fake_get_bounded):
            WhisperEngine().is_healthy(ENDPOINT, timeout=0.5)
        assert seen["timeout"] == 0.5


class TestListInstalled:
    def test_always_returns_an_empty_list_without_any_http_call(self):
        called = []

        def _get(url, timeout=None):
            called.append(url)
            raise AssertionError("list_installed must never call the network")

        with patch("models.contracts.engines.whisper.httpx.get", _get):
            assert WhisperEngine().list_installed(ENDPOINT) == []
        assert called == []

    def test_never_calls_anything_resembling_a_load_endpoint(self):
        """No `/load`-shaped POST is ever made -- there's nothing to load,
        so nothing should even try."""
        with patch("models.contracts.engines.whisper.httpx.post") as mock_post:
            WhisperEngine().list_installed(ENDPOINT)
        mock_post.assert_not_called()


class TestUnsupportedRoles:
    def test_build_llm_refuses(self):
        with pytest.raises(NotImplementedError, match="transcription only"):
            WhisperEngine().build_llm("any-model", ENDPOINT)

    def test_build_embedder_refuses(self):
        with pytest.raises(NotImplementedError, match="transcription only"):
            WhisperEngine().build_embedder("any-model", ENDPOINT)


class TestBuildTranscriber:
    def test_builds_a_transcriber_bound_to_model_and_endpoint(self):
        from models.contracts.engines.whisper import WhisperTranscriber

        transcriber = WhisperEngine().build_transcriber(
            "ggml-base.en.bin", ENDPOINT, language="fr"
        )

        assert isinstance(transcriber, WhisperTranscriber)
        assert transcriber.model_id == "ggml-base.en.bin"
        assert transcriber.endpoint == ENDPOINT
        assert transcriber.config == {"language": "fr"}


class TestSetupGuide:
    """The adapter owns its own install guide; the /setup/ page just renders
    it, same as ComfyUI's (spec §8 / owner requirement 2026-08-23)."""

    def test_covers_every_platform_the_page_renders(self):
        guide = WhisperEngine.setup_guide
        assert set(guide.platforms) == set(SETUP_PLATFORMS)
        assert all(guide.platforms[key] for key in SETUP_PLATFORMS)

    def test_every_platform_says_how_to_start_it_listening(self):
        guide = WhisperEngine.setup_guide
        for key in ("macos", "linux"):
            commands = " ".join(step.command or "" for step in guide.platforms[key])
            assert "--host 0.0.0.0" in commands
        windows = " ".join(step.command or "" for step in guide.platforms["windows"])
        assert "0.0.0.0" in windows or "0.0.0.0" in " ".join(
            step.body for step in guide.platforms["windows"]
        )

    def test_network_note_explains_the_container_hop(self):
        assert "host.docker.internal" in WhisperEngine.setup_guide.network_note

    def test_models_note_explains_one_model_per_process_and_never_downloads(self):
        note = WhisperEngine.setup_guide.models_note
        assert "one" in note.lower() and "model" in note.lower()
        assert "never downloads" in note
        assert "/inference/" in note
        assert "transcription" in note

    def test_verify_path_matches_the_health_check(self):
        from models.contracts.engines.whisper import _HEALTH_PATH

        assert WhisperEngine.setup_guide.verify_url_path == _HEALTH_PATH
        assert WhisperEngine.setup_guide.verify_url_path == "/"

    def test_no_model_names_appear_in_the_guide(self):
        """A specific model identifier (a whisper.cpp size tier like
        "base.en"/"large-v3", or a real filename) must never appear in the
        adapter's own copy -- only the placeholder `<model-file>` shape,
        matching the platform-wide "no model names in code/copy" convention.
        "ggml"/"gguf" alone are FORMAT names (like ComfyUI's guide naming
        ".safetensors"), not a model choice, so those are not tested for."""
        guide = WhisperEngine.setup_guide
        blob = guide.summary + guide.network_note + guide.models_note
        for platform_steps in guide.platforms.values():
            for step in platform_steps:
                blob += step.title + step.body + (step.command or "")
        blob = blob.lower()
        for model_name in ("tiny", "base.en", "small", "medium", "large-v3", ".bin"):
            assert model_name not in blob, f"{model_name!r} (a concrete model choice) leaked into the guide"

    def test_get_a_model_file_step_has_no_command_and_points_at_library_url(self):
        """The one step that names a real operator action ("go get a model
        file yourself") carries `command=None` -- there is no shell command
        for that, matching `SetupStep`'s own "no fake shell line" contract."""
        for platform in ("macos", "linux", "windows"):
            steps = WhisperEngine.setup_guide.platforms[platform]
            model_steps = [s for s in steps if "model file" in s.title.lower()]
            assert model_steps, f"no 'model file' step found for {platform}"
            for step in model_steps:
                assert step.command is None
                assert WhisperEngine.library_url in step.body
