"""Unit tests for gateway.get_transcriber / get_transcriber_for
(media-into-RAG plan T6).

Uses real DB rows (a registered connection + role binding) so the whole
resolve -> get_engine -> build path runs; no HTTP happens until the
transcriber is actually used -- byte-parallel to
`tools/vision/tests/test_gateway.py`'s `TestGetImageGenerator*` classes.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from models.registry.models import ModelConnection, RoleBinding
from models.contracts.bindings import ResolvedModel
from models.contracts.engines import ENGINES
from models.contracts.engines.whisper import WhisperTranscriber
from models.contracts.gateway import get_transcriber, get_transcriber_for
from models.contracts.roles import RAG_TRANSCRIBE_ROLE


def _clear_bindings():
    RoleBinding.objects.all().delete()
    ModelConnection.objects.all().delete()


@pytest.fixture(autouse=True)
def _reset_bindings(db):
    _clear_bindings()


def _bind(engine: str, model_id: str, endpoint: str, config=None) -> ModelConnection:
    connection = ModelConnection.objects.create(
        name=f"{engine} {model_id}", engine=engine, endpoint=endpoint,
        model_id=model_id, capabilities=["transcription"], config=config,
    )
    RoleBinding.objects.create(role_key=RAG_TRANSCRIBE_ROLE, connection=connection)
    return connection


@pytest.mark.django_db
class TestGetTranscriber:
    def test_builds_the_bound_engine_transcriber(self):
        _bind("whisper", "ggml-base.en.bin", "http://whisper.local:8080")

        transcriber = get_transcriber()

        assert isinstance(transcriber, WhisperTranscriber)
        assert transcriber.model_id == "ggml-base.en.bin"
        assert transcriber.endpoint == "http://whisper.local:8080"

    def test_connection_config_reaches_the_transcriber(self):
        _bind(
            "whisper", "ggml-base.en.bin", "http://whisper.local:8080",
            config={"language": "fr"},
        )

        assert get_transcriber().config == {"language": "fr"}

    def test_unbound_role_raises_the_resolver_error(self):
        with pytest.raises(ValueError, match="No inference binding resolved"):
            get_transcriber()

    def test_engine_that_cannot_transcribe_says_so(self):
        _bind_non_transcribing()

        with pytest.raises(ValueError, match="cannot transcribe audio"):
            get_transcriber()


def _bind_non_transcribing() -> ModelConnection:
    """An ollama connection bound to the transcription role -- reachable
    only if an operator forced a bad binding by hand, since role options are
    normally filtered by capability; exercises the honest refusal anyway."""
    connection = ModelConnection.objects.create(
        name="ollama llama3.1:8b", engine="ollama", endpoint="http://localhost:11434",
        model_id="llama3.1:8b", capabilities=["chat"],
    )
    RoleBinding.objects.create(role_key=RAG_TRANSCRIBE_ROLE, connection=connection)
    return connection


@pytest.mark.django_db
class TestGetTranscriberFor:
    """`get_transcriber_for` is the explicit-binding counterpart to
    `get_transcriber`: it builds from a `ResolvedModel` a caller already
    holds, never from the role registry."""

    def test_builds_from_an_explicit_binding_without_resolving_a_role(self):
        class StubTranscriberEngine:
            name = "stubtranscriber"

            def __init__(self):
                self.transcriber = object()
                self.built = []

            def build_transcriber(self, model_id, endpoint, **cfg):
                self.built.append((model_id, endpoint, dict(cfg)))
                return self.transcriber

        engine = StubTranscriberEngine()
        resolved = ResolvedModel(
            engine="stubtranscriber",
            model_id="recorded.bin",
            endpoint="http://recorded:9999",
            config={"language": "de"},
        )
        with patch.dict(ENGINES, {"stubtranscriber": engine}), patch(
            "models.contracts.gateway.resolve"
        ) as never_resolved:
            transcriber = get_transcriber_for(resolved)

        assert never_resolved.call_count == 0
        assert transcriber is engine.transcriber
        assert engine.built == [("recorded.bin", "http://recorded:9999", {"language": "de"})]

    def test_an_engine_that_cannot_transcribe_says_so(self):
        """Same guarantee as `test_engine_that_cannot_transcribe_says_so`
        above, reached through the explicit-binding path, where the check
        actually lives (mirrors vision's `test_gateway.py` structure)."""

        class TextOnly:
            name = "textonly"

        resolved = ResolvedModel(engine="textonly", model_id="m", endpoint="http://x:1")
        with patch.dict(ENGINES, {"textonly": TextOnly()}):
            with pytest.raises(ValueError, match="cannot transcribe audio"):
                get_transcriber_for(resolved)
