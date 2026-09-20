"""Unit tests for the additive engine-contract types (spec §4.2).

MOVED FROM `tools/vision/tests/test_engine_base.py` and
`tools/rag/tests/test_engine_base.py` (C-58), merged into one module.
These are two halves of one contract surface -- image generation and
setup on one side (`Asset`, `JobStatus`, `SetupGuide`, `ImageGenerator`),
transcription on the other (`TranscriptSegment`, `TranscriptResult`,
`Transcriber`, `build_transcriber`) -- that were split across two
consumer columns only because each column tested the half it used.
Neither half imports anything from `tools.vision` or `tools.rag`, so a
contract's own tests belong beside the contract, where the next consumer
finds them.
"""
from __future__ import annotations

import pytest

from models.contracts.engines.base import (
    JOB_STATES,
    SETUP_PLATFORM_LABELS,
    SETUP_PLATFORMS,
    Asset,
    GenerationRejected,
    ImageGenerator,
    InferenceEngine,
    JobStatus,
    SetupGuide,
    SetupStep,
    Transcriber,
    TranscriptResult,
    TranscriptSegment,
)
from models.contracts.engines.ollama import OllamaEngine


class TestAsset:
    def test_size_is_optional(self):
        asset = Asset(kind="lora", asset_id="sub\\detail.safetensors")
        assert asset.size is None
        assert asset.asset_id == "sub\\detail.safetensors"

    def test_is_frozen(self):
        with pytest.raises(Exception):
            Asset(kind="vae", asset_id="v.safetensors").kind = "lora"


class TestJobStatus:
    def test_defaults(self):
        state = JobStatus(state="queued")
        assert state.error is None
        assert state.progress is None

    def test_every_state_the_platform_understands(self):
        assert JOB_STATES == ("queued", "running", "done", "failed", "lost")


class TestOptionalMembersDegradeGracefully:
    """Downstream reads every optional member via `getattr(engine, name,
    None)`, so an adapter that predates them (Ollama) is untouched."""

    def test_ollama_has_no_image_generation_members(self):
        engine = OllamaEngine()
        assert getattr(engine, "build_image_generator", None) is None
        assert getattr(engine, "supported_operations", None) is None
        assert getattr(engine, "list_assets", None) is None
        assert getattr(engine, "list_choices", None) is None


class TestSetupTypes:
    """An engine declares its OWN install guide (the /setup/ page renders
    whatever it is handed and names no engine of its own)."""

    def test_a_step_may_carry_no_command(self):
        step = SetupStep(title="Download the portable build", body="Unzip it anywhere.")
        assert step.command is None

    def test_the_three_platform_keys_and_their_labels(self):
        assert SETUP_PLATFORMS == ("macos", "windows", "linux")
        assert SETUP_PLATFORM_LABELS == {"macos": "macOS", "windows": "Windows", "linux": "Linux"}

    def test_a_guide_carries_everything_the_page_renders(self):
        guide = SetupGuide(
            summary="s",
            platforms={"macos": (SetupStep("t", "b", "c"),)},
            network_note="n",
            models_note="m",
            verify_url_path="/health",
        )
        assert guide.platforms["macos"][0].command == "c"
        assert guide.verify_url_path == "/health"


class TestSetupMembersAreOptional:
    """Read via `getattr` like every other optional member, so an adapter
    that declares neither still renders a minimal card on /setup/."""

    def test_an_engine_without_them_degrades_to_empty(self):
        class _Bare:
            name = "bare"

        engine = _Bare()
        assert getattr(engine, "setup_guide", None) is None
        assert getattr(engine, "serves_capabilities", ()) == ()


class TestProtocolsAreImportable:
    def test_image_generator_declares_the_three_methods(self):
        for name in ("submit", "status", "fetch_outputs"):
            assert hasattr(ImageGenerator, name)

    def test_inference_engine_declares_the_optional_members(self):
        for name in (
            "supported_operations", "list_assets", "list_choices",
            "build_image_generator", "setup_guide", "serves_capabilities",
            "ignored_params",
        ):
            assert hasattr(InferenceEngine, name)

    def test_generation_rejected_is_a_runtime_error(self):
        assert issubclass(GenerationRejected, RuntimeError)


class TestIgnoredParamsIsAnOptionalMember:
    """The negative twin of `param_defaults` (ADR 0012 D-EDIT-13). Read
    downstream via `getattr`, so an adapter that has none is untouched --
    the same rule every other optional member follows."""

    def test_the_protocol_declares_it_with_the_param_defaults_signature(self):
        import inspect

        signature = inspect.signature(InferenceEngine.ignored_params)
        assert list(signature.parameters) == ["self", "operation_key", "config"]
        assert signature.parameters["config"].default is None
        assert "cannot honour" in (InferenceEngine.ignored_params.__doc__ or "")

    def test_an_adapter_without_one_reports_nothing_through_getattr(self):
        assert getattr(OllamaEngine(), "ignored_params", None) is None


# --- transcription half (media-into-RAG plan, T1; ADR 0014) ----------------
#
# `TranscriptSegment`, `TranscriptResult`, `Transcriber`, and
# `InferenceEngine.build_transcriber` -- the seam the classes above already
# cover for `ImageGenerator`. No DB, no Django models involved.


class TestTranscriptSegment:
    def test_is_frozen(self):
        segment = TranscriptSegment(start=0.0, end=1.0, text="hi")
        with pytest.raises(Exception):
            segment.text = "changed"


class TestTranscriptResult:
    def test_defaults(self):
        result = TranscriptResult()
        assert result.segments == ()
        assert result.language is None
        assert result.duration is None
        assert result.text == ""

    def test_is_frozen(self):
        result = TranscriptResult()
        with pytest.raises(Exception):
            result.language = "en"

    def test_text_joins_segments_with_newlines(self):
        result = TranscriptResult(
            segments=(
                TranscriptSegment(start=0.0, end=1.0, text="Hello"),
                TranscriptSegment(start=1.0, end=2.0, text="world"),
            )
        )
        assert result.text == "Hello\nworld"

    def test_text_skips_empty_segments(self):
        result = TranscriptResult(
            segments=(
                TranscriptSegment(start=0.0, end=1.0, text="Hello"),
                TranscriptSegment(start=1.0, end=1.0, text=""),
                TranscriptSegment(start=1.0, end=2.0, text="world"),
            )
        )
        assert result.text == "Hello\nworld"

    def test_offset_shifts_every_segments_start_and_end(self):
        result = TranscriptResult(
            segments=(
                TranscriptSegment(start=0.0, end=1.0, text="a"),
                TranscriptSegment(start=1.0, end=2.5, text="b"),
            )
        )

        shifted = result.offset(10.0)

        assert [s.start for s in shifted.segments] == [10.0, 11.0]
        assert [s.end for s in shifted.segments] == [11.0, 12.5]
        assert [s.text for s in shifted.segments] == ["a", "b"]

    def test_offset_preserves_language_and_duration(self):
        result = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=1.0, text="a"),),
            language="en",
            duration=42.0,
        )

        shifted = result.offset(5.0)

        assert shifted.language == "en"
        assert shifted.duration == 42.0

    def test_offset_does_not_mutate_the_original(self):
        original = TranscriptResult(segments=(TranscriptSegment(start=0.0, end=1.0, text="a"),))

        original.offset(10.0)

        assert original.segments[0].start == 0.0


class TestTranscriberProtocol:
    def test_declares_transcribe(self):
        assert hasattr(Transcriber, "transcribe")


class TestBuildTranscriberIsOptional:
    """Read via `getattr(engine, "build_transcriber", None)`, exactly like
    `build_image_generator` -- an adapter that predates it (Ollama)
    degrades to "can't build one" rather than `AttributeError`."""

    def test_ollama_has_no_build_transcriber(self):
        engine = OllamaEngine()
        assert getattr(engine, "build_transcriber", None) is None

    def test_inference_engine_declares_build_transcriber(self):
        assert hasattr(InferenceEngine, "build_transcriber")
