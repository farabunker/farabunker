"""Unit tests for tools/rag/media.py -- the whisper transcription
pipeline (media-into-RAG plan T7).

Every external boundary is mocked -- `tools.rag.media.resolve`/
`get_engine`/`get_transcriber_for` (the model-resolution seam, matching
`test_jobs.py`'s own "mock at the HTTP/DB boundary" convention for
`run_ask`'s pre-check) and `tools.rag.media.transcode` (the whole
submodule reference, matching `test_ingest.py`'s `@patch("tools.rag.ingest.rag_index")`
precedent for a bound submodule import) -- these tests never shell out to
`ffmpeg`/`ffprobe` and never hit a real whisper-server. `settings.DOCUMENTS_DIR`
is redirected to a throwaway directory (the `_managed_store` autouse
fixture, mirroring `test_ingest.py`'s own) so `transcribe_to_sidecar`'s real
file I/O (the work dir, the sidecar) never touches the repo-local `data/`
directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import MetadataMode

from models.contracts.bindings import ResolvedModel
from models.contracts.engines.base import GenerationRejected, TranscriptResult, TranscriptSegment
from tools.rag import media, store
from tools.rag.models import Document
from tools.rag.tests._helpers import make_job_ctx

TRANSCRIBE_RESOLVED = ResolvedModel("whisper", "ggml-base.en", "http://localhost:8080")


@pytest.fixture(autouse=True)
def _managed_store(tmp_path, settings):
    """Redirect the managed document store (ADR 0009) to a throwaway
    directory -- see test_ingest.py's identical fixture."""
    store_root = tmp_path / "_managed_store"
    store_root.mkdir()
    settings.DOCUMENTS_DIR = store_root
    return store_root


def _make_doc(**kwargs) -> Document:
    defaults = dict(
        title="clip.mp4",
        source_path="/inbox-irrelevant/clip.mp4",
        original_path="/inbox/clip.mp4",
        file_hash="a" * 64,
        doc_type=Document.DocType.PROSE,
        media_type="video/mp4",
        status=Document.Status.PROCESSING,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


class _FakeTranscriber:
    """A `Transcriber` stand-in: returns `results[i]` for the i-th call,
    recording every `(audio_path, language)` pair it was called with."""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple] = []

    def transcribe(self, audio_path, *, language=None):
        self.calls.append((audio_path, language))
        return self._results[len(self.calls) - 1]


# --- C-31: the shared driver header (_driver_paths) / tail (_finish_driver) --


@pytest.mark.django_db
class TestDriverPaths:
    """`_driver_paths` is the three-line header both `transcribe_to_
    sidecar` and `extract_to_sidecar` used to open with, by hand."""

    def test_returns_stored_sidecar_and_work_paths_from_the_store(self):
        doc = _make_doc(source_path="/inbox-irrelevant/clip.mp4")

        stored_path, sidecar_path, work_dir = media._driver_paths(doc)

        assert stored_path == Path("/inbox-irrelevant/clip.mp4")
        assert sidecar_path == store.sidecar_path(doc.id)
        assert work_dir == store.work_dir(doc.id)

    def test_does_not_touch_the_filesystem(self):
        """Building the three `Path`s must be side-effect-free -- a driver
        that short-circuits on a finished sidecar, or fails role
        resolution, must never have created `work/` just by calling this."""
        doc = _make_doc()

        _, sidecar_path, work_dir = media._driver_paths(doc)

        assert not sidecar_path.exists()
        assert not work_dir.exists()


@pytest.mark.django_db
class TestFinishDriver:
    """`_finish_driver` is the fixed tail both drivers used to run by
    hand, once their own payload dict exists: write the sidecar
    atomically, stamp `doc.extraction`, purge `work/`, return the
    sidecar."""

    def test_writes_sidecar_stamps_extraction_purges_work_dir_and_returns_it(self):
        doc = _make_doc()
        sidecar_path = store.sidecar_path(doc.id)
        work_dir = store.work_dir(doc.id)
        work_dir.mkdir(parents=True)
        (work_dir / "leftover.wav").write_bytes(b"x")
        sidecar = {"version": 1, "method": "whisper", "produced_at": "2026-01-01T00:00:00+00:00"}

        result = media._finish_driver(
            doc, sidecar, method="transcription", resolved=TRANSCRIBE_RESOLVED,
            produced_at="2026-01-01T00:00:00+00:00", sidecar_path=sidecar_path, work_dir=work_dir,
        )

        assert result is sidecar
        assert json.loads(sidecar_path.read_text()) == sidecar
        assert not work_dir.exists()
        doc.refresh_from_db()
        assert doc.extraction == {
            "method": "transcription",
            "engine": TRANSCRIBE_RESOLVED.engine,
            "model_id": TRANSCRIBE_RESOLVED.model_id,
            "connection_name": "",
            "produced_at": "2026-01-01T00:00:00+00:00",
        }

    def test_method_is_the_callers_own_word_not_hardcoded(self):
        """The two drivers pass different `method=` strings
        ("transcription" vs "extraction") -- `_finish_driver` must stamp
        whichever one it was actually given, never one it silently picks
        itself, or a third driver (or a typo) would go unnoticed."""
        doc = _make_doc()
        sidecar_path = store.sidecar_path(doc.id)
        work_dir = store.work_dir(doc.id)
        work_dir.mkdir(parents=True)

        media._finish_driver(
            doc, {"version": 1}, method="extraction", resolved=TRANSCRIBE_RESOLVED,
            produced_at="2026-01-01T00:00:00+00:00", sidecar_path=sidecar_path, work_dir=work_dir,
        )

        doc.refresh_from_db()
        assert doc.extraction["method"] == "extraction"

    def test_missing_work_dir_does_not_raise(self):
        """Best-effort purge (`ignore_errors=True`) -- matches each
        driver's own docstring: a leftover/missing work directory is not
        itself a failure worth raising over once the sidecar and the
        Document row are already durably written."""
        doc = _make_doc()
        sidecar_path = store.sidecar_path(doc.id)
        work_dir = store.work_dir(doc.id)
        # `document_dir` already exists by the time a real driver runs
        # (the source file was copied there by `store.store_file`/
        # `move_file`) -- created here directly so this test exercises
        # only the "work_dir is missing" case, not an unrelated one.
        sidecar_path.parent.mkdir(parents=True)
        assert not work_dir.exists()

        media._finish_driver(
            doc, {"version": 1}, method="transcription", resolved=TRANSCRIBE_RESOLVED,
            produced_at="2026-01-01T00:00:00+00:00", sidecar_path=sidecar_path, work_dir=work_dir,
        )

        assert not work_dir.exists()
        assert sidecar_path.exists()


# --- transcribe_to_sidecar: health re-check (the run_ask _precheck pattern) --


@pytest.mark.django_db
class TestTranscribeToSidecarHealthRecheck:
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", side_effect=ValueError("No inference binding resolved for role 'rag.transcribe'"))
    def test_unbound_role_raises_the_shared_message(self, mock_resolve, mock_get_engine):
        doc = _make_doc()

        with pytest.raises(RuntimeError) as exc_info:
            media.transcribe_to_sidecar(doc, make_job_ctx())

        assert str(exc_info.value) == "The transcription model isn't set up yet — assign one in the model console."
        mock_get_engine.assert_not_called()

    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=TRANSCRIBE_RESOLVED)
    def test_unreachable_role_raises_the_shared_message(self, mock_resolve, mock_get_engine):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = False
        mock_get_engine.return_value = mock_engine
        doc = _make_doc()

        with pytest.raises(RuntimeError) as exc_info:
            media.transcribe_to_sidecar(doc, make_job_ctx())

        assert str(exc_info.value) == "The transcription model is unreachable."
        mock_engine.is_healthy.assert_called_once_with(TRANSCRIBE_RESOLVED.endpoint)


# --- transcribe_to_sidecar: happy path --------------------------------------


@pytest.mark.django_db
@patch("tools.rag.media.transcode")
@patch("tools.rag.media.get_transcriber_for")
@patch("tools.rag.media.get_engine")
@patch("tools.rag.media.resolve", return_value=TRANSCRIBE_RESOLVED)
class TestTranscribeToSidecarHappyPath:
    def _healthy(self, mock_get_engine):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine

    def test_slices_offsets_grouping_sidecar_shape_doc_fields_and_cleanup(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        self._healthy(mock_get_engine)
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.slice_audio.return_value = [Path("/fake/slice-00000.wav"), Path("/fake/slice-00001.wav")]
        mock_transcode.probe_duration.return_value = 550.0

        result0 = TranscriptResult(
            segments=(
                TranscriptSegment(start=0.0, end=2.0, text="hello"),
                TranscriptSegment(start=2.0, end=4.0, text="world"),
            ),
            language="en",
        )
        result1 = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=3.0, text="second slice"),),
            language="en",
        )
        fake_transcriber = _FakeTranscriber([result0, result1])
        mock_get_transcriber_for.return_value = fake_transcriber

        reports: list[dict] = []
        checkpoints: list[dict] = []
        ctx = make_job_ctx(_report=reports.append, _checkpoint=checkpoints.append)

        doc = _make_doc()
        work_dir = store.document_dir(doc.id) / "work"

        sidecar = media.transcribe_to_sidecar(doc, ctx)

        # extract_audio/slice_audio ran (no checkpoint_state -- first run).
        mock_transcode.extract_audio.assert_called_once()
        # T7 review m5: window_seconds is passed EXPLICITLY, from the same
        # local var the offset math below uses -- not left to slice_audio's
        # own default, which could silently diverge from it.
        work_dir_for_slices = store.document_dir(doc.id) / "work"
        mock_transcode.slice_audio.assert_called_once_with(
            work_dir_for_slices / "audio.wav", work_dir_for_slices / "slices", window_seconds=300
        )

        # Offsets: slice 0 unshifted, slice 1 shifted by 1*window_seconds.
        assert sidecar["segments"] == [
            {"start": 0.0, "end": 2.0, "text": "hello"},
            {"start": 2.0, "end": 4.0, "text": "world"},
            {"start": 300.0, "end": 303.0, "text": "second slice"},
        ]
        assert sidecar["version"] == 1
        assert sidecar["method"] == "whisper"
        assert sidecar["source"] == "clip.mp4"
        assert sidecar["duration_seconds"] == 550.0
        assert sidecar["language"] == "en"
        assert sidecar["model"] == {"engine": "whisper", "model_id": "ggml-base.en"}
        assert sidecar["produced_at"] is not None

        # Sidecar written to disk matches the returned dict.
        sidecar_path = store.document_dir(doc.id) / "extract.json"
        assert json.loads(sidecar_path.read_text()) == sidecar

        # Document fields.
        doc.refresh_from_db()
        assert doc.duration_seconds == 550.0
        assert doc.extraction == {
            "method": "transcription",
            "engine": "whisper",
            "model_id": "ggml-base.en",
            "connection_name": "",
            "produced_at": sidecar["produced_at"],
        }

        # Work dir cleaned up on a clean finish.
        assert not work_dir.exists()

        # Both slices transcribed, in order.
        assert len(fake_transcriber.calls) == 2

        # Progress: done/total/unit reported per slice, capped at duration.
        assert reports == [
            {"done": 300.0, "total": 550.0, "unit": "seconds", "label": "transcribed"},
            {"done": 550.0, "total": 550.0, "unit": "seconds", "label": "transcribed"},
        ]
        # Checkpoint stays tiny -- next_slice_index/segments_written only.
        assert checkpoints == [
            {"next_slice_index": 1, "segments_written": 2},
            {"next_slice_index": 2, "segments_written": 3},
        ]

    def test_generation_rejected_propagates(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        self._healthy(mock_get_engine)
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.slice_audio.return_value = [Path("/fake/slice-00000.wav")]
        mock_transcode.probe_duration.return_value = 10.0

        fake_transcriber = MagicMock()
        fake_transcriber.transcribe.side_effect = GenerationRejected("unknown model file")
        mock_get_transcriber_for.return_value = fake_transcriber

        doc = _make_doc()

        with pytest.raises(GenerationRejected, match="unknown model file"):
            media.transcribe_to_sidecar(doc, make_job_ctx())

    def test_over_cap_duration_raises_before_any_transcription(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        self._healthy(mock_get_engine)
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.slice_audio.return_value = [Path("/fake/slice-00000.wav")]
        # RagSettings.MAX_MEDIA_SECONDS_DEFAULT is 7200 -- comfortably over.
        mock_transcode.probe_duration.return_value = 999_999.0

        fake_transcriber = MagicMock()
        mock_get_transcriber_for.return_value = fake_transcriber

        doc = _make_doc()

        with pytest.raises(ValueError, match="over the 7200s media duration limit"):
            media.transcribe_to_sidecar(doc, make_job_ctx())

        fake_transcriber.transcribe.assert_not_called()
        # Duration is still saved -- a measurement, taken before the cap
        # check, useful even though this run fails.
        doc.refresh_from_db()
        assert doc.duration_seconds == 999_999.0


# --- transcribe_to_sidecar: resume (checkpoint mid-way) ---------------------


@pytest.mark.django_db
class TestTranscribeToSidecarResume:
    """T7 review M1/M2: the sidecar and its checkpoint are written in a
    FIXED order every loop iteration (`_write_sidecar_atomic` THEN
    `ctx.checkpoint(...)`), so a crash between those two writes is the
    scenario a resume actually has to reconcile -- not the "everything
    already lines up" case alone."""

    def _seed_work_dir_and_slices(self, doc):
        work_dir = store.document_dir(doc.id) / "work"
        work_dir.mkdir(parents=True)
        (work_dir / "audio.wav").write_bytes(b"wav")
        slices_dir = work_dir / "slices"
        slices_dir.mkdir()
        (slices_dir / "slice-00000.wav").write_bytes(b"s0")
        (slices_dir / "slice-00001.wav").write_bytes(b"s1")
        return work_dir, slices_dir

    def _write_sidecar(self, doc, segments, *, produced_at=None):
        sidecar_path = store.document_dir(doc.id) / "extract.json"
        sidecar_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "method": "whisper",
                    "source": "clip.mp4",
                    "duration_seconds": 550.0,
                    "language": "en",
                    "model": {"engine": "whisper", "model_id": "ggml-base.en"},
                    "produced_at": produced_at,
                    "segments": segments,
                }
            )
        )
        return sidecar_path

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=TRANSCRIBE_RESOLVED)
    def test_resume_with_a_consistent_checkpoint_continues_from_index(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """The checkpoint and the sidecar already agree
        (`segments_written == len(segments)`) -- the ordinary case when a
        job is orphaned and requeued cleanly BETWEEN two loop iterations,
        rather than crashing mid-iteration."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.probe_duration.return_value = 550.0

        doc = _make_doc()
        work_dir, slices_dir = self._seed_work_dir_and_slices(doc)
        self._write_sidecar(doc, [{"start": 0.0, "end": 2.0, "text": "hello"}])

        result1 = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=3.0, text="second slice"),), language="en"
        )
        mock_get_transcriber_for.return_value = _FakeTranscriber([result1])

        ctx = make_job_ctx(checkpoint_state={"next_slice_index": 1, "segments_written": 1})

        sidecar = media.transcribe_to_sidecar(doc, ctx)

        mock_transcode.extract_audio.assert_not_called()
        mock_transcode.slice_audio.assert_not_called()
        assert sidecar["segments"] == [
            {"start": 0.0, "end": 2.0, "text": "hello"},
            {"start": 300.0, "end": 303.0, "text": "second slice"},
        ]

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=TRANSCRIBE_RESOLVED)
    def test_resume_reconciles_sidecar_ahead_of_checkpoint_no_duplication(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """M1 (the core fix): the sidecar write for slice 1 landed, but the
        checkpoint call recording it crashed before completing -- the
        checkpoint the NEXT attempt loads is stale by one slice
        (`segments_written=1`, `next_slice_index=1`) even though the
        sidecar on disk already has 2 segments. Truncates back to the last
        CONFIRMED state and redoes slice 1 -- the phantom, unconfirmed
        segment is discarded and replaced, not duplicated alongside a
        fresh one."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.probe_duration.return_value = 550.0

        doc = _make_doc()
        work_dir, slices_dir = self._seed_work_dir_and_slices(doc)
        # Sidecar already has BOTH slices' segments (slice 1's write landed)...
        self._write_sidecar(
            doc,
            [
                {"start": 0.0, "end": 2.0, "text": "hello"},
                {"start": 300.0, "end": 303.0, "text": "phantom unconfirmed"},
            ],
        )
        # ...but the checkpoint is one slice BEHIND that (its own write for
        # slice 1 never landed).
        ctx = make_job_ctx(checkpoint_state={"next_slice_index": 1, "segments_written": 1})

        result1 = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=3.0, text="second slice redone"),), language="en"
        )
        fake_transcriber = _FakeTranscriber([result1])
        mock_get_transcriber_for.return_value = fake_transcriber

        sidecar = media.transcribe_to_sidecar(doc, ctx)

        mock_transcode.extract_audio.assert_not_called()
        mock_transcode.slice_audio.assert_not_called()
        # Slice 1 was redone exactly once -- not skipped, not duplicated.
        assert len(fake_transcriber.calls) == 1
        assert fake_transcriber.calls[0][0] == slices_dir / "slice-00001.wav"
        # The phantom segment is GONE, replaced by the fresh redo -- exactly
        # two segments total, never three.
        assert sidecar["segments"] == [
            {"start": 0.0, "end": 2.0, "text": "hello"},
            {"start": 300.0, "end": 303.0, "text": "second slice redone"},
        ]

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=TRANSCRIBE_RESOLVED)
    def test_resume_sidecar_behind_checkpoint_fails_loudly(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """M1, the other direction: the checkpoint claims MORE segments
        were confirmed than the sidecar actually holds -- impossible under
        this module's own write order (sidecar always written before its
        checkpoint), so this can only mean the sidecar lost a write some
        other way. There's no honest way to recompute a resume point from a
        segment COUNT alone, so this raises rather than guessing."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.probe_duration.return_value = 550.0

        doc = _make_doc()
        self._seed_work_dir_and_slices(doc)
        self._write_sidecar(doc, [{"start": 0.0, "end": 2.0, "text": "hello"}])
        # Checkpoint claims 2 segments were confirmed; the sidecar has 1.
        ctx = make_job_ctx(checkpoint_state={"next_slice_index": 2, "segments_written": 2})

        with pytest.raises(RuntimeError, match="behind its own checkpoint"):
            media.transcribe_to_sidecar(doc, ctx)

        mock_get_transcriber_for.return_value.transcribe.assert_not_called()

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=TRANSCRIBE_RESOLVED)
    def test_resume_unreadable_sidecar_forces_full_retranscribe(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """M2: a truncated/corrupt `extract.json` on resume must not just
        reset `segments=[]` while trusting a stale `next_slice_index` --
        that would silently drop the front half of the transcript (skip
        slices whose segments no longer exist anywhere). Both reset
        together: a full retranscribe from slice 0, ending in a complete
        final sidecar."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.probe_duration.return_value = 550.0

        doc = _make_doc()
        work_dir, slices_dir = self._seed_work_dir_and_slices(doc)
        sidecar_path = store.document_dir(doc.id) / "extract.json"
        sidecar_path.write_text("{not valid json")  # truncated/corrupt

        # A checkpoint claiming slice 0 already finished -- must NOT be
        # trusted once the sidecar it depends on is unreadable.
        ctx = make_job_ctx(checkpoint_state={"next_slice_index": 2, "segments_written": 1})

        result0 = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=2.0, text="hello redone"),), language="en"
        )
        result1 = TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=3.0, text="second slice redone"),), language="en"
        )
        fake_transcriber = _FakeTranscriber([result0, result1])
        mock_get_transcriber_for.return_value = fake_transcriber

        sidecar = media.transcribe_to_sidecar(doc, ctx)

        # BOTH slices retranscribed -- nothing skipped.
        assert len(fake_transcriber.calls) == 2
        assert sidecar["segments"] == [
            {"start": 0.0, "end": 2.0, "text": "hello redone"},
            {"start": 300.0, "end": 303.0, "text": "second slice redone"},
        ]
        assert sidecar["produced_at"] is not None


# --- _reconcile_sidecar_resume (audit-approved consolidation) ---------------
#
# `_reconcile_resume_state` (whisper slices) and `_reconcile_page_resume_
# state` (T8 vision pages) were merged into this one function, parameterized
# by `index_key`/`unit_noun`. `transcribe_to_sidecar`'s/`extract_to_sidecar`'s
# own resume tests above/below already exercise it indirectly through both
# callers; these tests call it directly, once for each `index_key` shape, so
# the reconciliation logic itself is proven without needing a full driver
# run around it.


class TestReconcileSidecarResume:
    @pytest.mark.parametrize("index_key,unit_noun", [("next_slice_index", "slice"), ("next_page_index", "page")])
    def test_sidecar_ahead_of_checkpoint_truncates_to_confirmed(self, tmp_path, index_key, unit_noun):
        sidecar_path = tmp_path / "extract.json"
        sidecar_path.write_text(
            json.dumps({"segments": [{"text": "a"}, {"text": "b"}], "language": "en"})
        )
        checkpoint_state = {index_key: 1, "segments_written": 1}

        segments, next_index, payload = media._reconcile_sidecar_resume(
            1, sidecar_path, checkpoint_state, index_key=index_key, unit_noun=unit_noun
        )

        assert segments == [{"text": "a"}]
        assert next_index == 1
        assert payload["language"] == "en"

    @pytest.mark.parametrize("index_key,unit_noun", [("next_slice_index", "slice"), ("next_page_index", "page")])
    def test_sidecar_behind_checkpoint_raises(self, tmp_path, index_key, unit_noun):
        sidecar_path = tmp_path / "extract.json"
        sidecar_path.write_text(json.dumps({"segments": [{"text": "a"}]}))
        checkpoint_state = {index_key: 2, "segments_written": 2}

        with pytest.raises(RuntimeError, match="behind its own checkpoint"):
            media._reconcile_sidecar_resume(
                1, sidecar_path, checkpoint_state, index_key=index_key, unit_noun=unit_noun
            )

    @pytest.mark.parametrize("index_key,unit_noun", [("next_slice_index", "slice"), ("next_page_index", "page")])
    def test_unreadable_sidecar_forces_full_redo_and_returns_no_payload(self, tmp_path, index_key, unit_noun):
        sidecar_path = tmp_path / "extract.json"
        sidecar_path.write_text("{not valid json")

        segments, next_index, payload = media._reconcile_sidecar_resume(
            1, sidecar_path, {index_key: 5, "segments_written": 5}, index_key=index_key, unit_noun=unit_noun
        )

        assert segments == []
        assert next_index == 0
        assert payload is None

    @pytest.mark.parametrize("index_key,unit_noun", [("next_slice_index", "slice"), ("next_page_index", "page")])
    def test_sidecar_that_is_a_json_list_is_treated_as_unreadable(self, tmp_path, index_key, unit_noun):
        """T10 re-review MINOR 2: this function used to read the sidecar
        via its own inline `json.loads`/`except (OSError, ValueError)`,
        which caught the read/parse failures above but not a THIRD one --
        valid JSON that isn't an object at the top level (a bare list,
        never produced by this module's own writers, but not provable
        from here). `existing.get("segments")` on such a value was an
        uncaught `AttributeError`. Now reads via `tools.rag.sidecar.
        read_sidecar` (the same fix `_load_finished_sidecar_if_matching`
        already got), which folds the wrong-shape case into the same
        clean "unreadable -- reset to 0" result as a missing/corrupt file."""
        sidecar_path = tmp_path / "extract.json"
        sidecar_path.write_text(json.dumps([]))

        segments, next_index, payload = media._reconcile_sidecar_resume(
            1, sidecar_path, {index_key: 5, "segments_written": 5}, index_key=index_key, unit_noun=unit_noun
        )

        assert segments == []
        assert next_index == 0
        assert payload is None


# --- transcribe_to_sidecar: finished-sidecar short-circuit (T7 review M3a) --


@pytest.mark.django_db
class TestTranscribeToSidecarFinishedShortCircuit:
    """A prior attempt's transcription can complete cleanly (sidecar
    written, `doc.extraction`/`doc.duration_seconds` saved, `work/`
    removed) and the SAME `run_ingest_for` call still fail later in the
    embed phase -- `doc.status` ends up FAILED, but nothing about the
    transcription itself was lost. A retry (a brand-new job,
    `checkpoint_state=None`) must not redo it.

    T7 round-3 review MAJOR (probe-reproduced): the match check used to
    compare a FRESH duration probe of the container against the sidecar's
    own `duration_seconds` (measured on the DECODED WAV) -- container
    duration is quantized to the container's stream/frame-rate metadata,
    which never equals the decoded sample length for any real video
    format, so the short-circuit never actually fired for a video. It now
    compares `source_sha256` (content-addressed, `doc.file_hash`) instead
    -- these tests exercise that identity check directly, including one
    with REALISTIC divergent durations (a source/WAV pair that would have
    failed the old check) to prove duration plays no part in the decision
    at all anymore.
    """

    def _write_finished_sidecar(self, doc, *, source_name="clip.mp4", source_sha256=None, duration=42.0):
        doc_dir = store.document_dir(doc.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = doc_dir / "extract.json"
        sidecar = {
            "version": 1,
            "method": "whisper",
            "source": source_name,
            "source_sha256": source_sha256 if source_sha256 is not None else doc.file_hash,
            "duration_seconds": duration,
            "language": "en",
            "model": {"engine": "whisper", "model_id": "ggml-base.en"},
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"start": 0.0, "end": 2.0, "text": "already transcribed"}],
        }
        sidecar_path.write_text(json.dumps(sidecar))
        return sidecar

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_orphaned_finished_sidecar_short_circuits_no_transcriber_call(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        doc = _make_doc(source_path="/inbox-irrelevant/clip.mp4")
        finished = self._write_finished_sidecar(doc, source_name="clip.mp4")

        result = media.transcribe_to_sidecar(doc, make_job_ctx())

        assert result == finished
        # No model was ever resolved or health-checked, let alone asked to
        # transcribe -- the whole point of the short-circuit. No duration
        # probe either (T7 round-3): the match decision is hash-only now.
        mock_resolve.assert_not_called()
        mock_get_engine.assert_not_called()
        mock_get_transcriber_for.assert_not_called()
        mock_transcode.probe_duration.assert_not_called()

        doc.refresh_from_db()
        assert doc.duration_seconds == 42.0
        assert doc.extraction == {
            "method": "transcription",
            "engine": "whisper",
            "model_id": "ggml-base.en",
            "connection_name": "",
            "produced_at": finished["produced_at"],
        }

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_finished_sidecar_lacking_duration_key_clears_stale_duration(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """T9.5 review M2: `_stamp_extraction` must strictly PRESERVE the
        pre-consolidation short-circuit's behavior -- it always wrote
        `doc.duration_seconds = finished.get("duration_seconds")`
        unconditionally, clearing a stale value to `None` when the
        finished sidecar it's reusing doesn't carry the key (a legacy or
        hand-edited sidecar). The consolidated `_stamp_extraction` must
        gate that write on `method == "transcription"` (always write for
        a transcription short-circuit, value included), never on
        `duration is not None` (which would instead leave a stale prior
        value untouched whenever the sidecar's own value happens to be
        `None`/missing -- a real behavior drift the T9.5 audit caught)."""
        doc = _make_doc(source_path="/inbox-irrelevant/clip.mp4", duration_seconds=123.0)
        doc_dir = store.document_dir(doc.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = doc_dir / "extract.json"
        finished = {
            "version": 1,
            "method": "whisper",
            "source": "clip.mp4",
            "source_sha256": doc.file_hash,
            # No "duration_seconds" key at all -- the exact case that drifted.
            "language": "en",
            "model": {"engine": "whisper", "model_id": "ggml-base.en"},
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"start": 0.0, "end": 2.0, "text": "already transcribed"}],
        }
        sidecar_path.write_text(json.dumps(finished))

        result = media.transcribe_to_sidecar(doc, make_job_ctx())

        assert result == finished
        mock_get_transcriber_for.assert_not_called()

        doc.refresh_from_db()
        assert doc.duration_seconds is None

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_realistic_divergent_durations_still_short_circuit_on_matching_hash(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """The exact scenario that broke the old duration-based check:
        7.307s measured on the source CONTAINER vs. 7.300s recorded in the
        sidecar (measured on the DECODED WAV) -- realistic, small,
        expected divergence for any real video format. Matching
        `source_sha256` alone is enough; the short-circuit fires anyway."""
        doc = _make_doc(source_path="/inbox-irrelevant/clip.mp4")
        finished = self._write_finished_sidecar(doc, source_name="clip.mp4", duration=7.300)
        # A duration probe would report the CONTAINER's own 7.307s if it
        # were still consulted -- it deliberately is not.
        mock_transcode.probe_duration.return_value = 7.307

        result = media.transcribe_to_sidecar(doc, make_job_ctx())

        assert result == finished
        mock_get_transcriber_for.assert_not_called()
        mock_transcode.probe_duration.assert_not_called()

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_source_name_mismatch_does_not_short_circuit(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        doc = _make_doc(source_path="/inbox-irrelevant/different.mp4")
        self._write_finished_sidecar(doc, source_name="clip.mp4")
        mock_resolve.return_value = TRANSCRIBE_RESOLVED
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.slice_audio.return_value = []
        mock_transcode.probe_duration.return_value = 5.0

        media.transcribe_to_sidecar(doc, make_job_ctx())

        mock_get_transcriber_for.assert_called_once()

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_content_hash_mismatch_does_not_short_circuit(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        """T7 round-3: retargeted from a duration-mismatch scenario (which
        no longer means anything -- duration plays no part in the check)
        to a genuinely-different-file scenario: same name, same directory,
        but the CONTENT changed (a re-staged file with a new hash) -- the
        sidecar's `source_sha256` no longer matches `doc.file_hash`."""
        doc = _make_doc(source_path="/inbox-irrelevant/clip.mp4", file_hash="c" * 64)
        self._write_finished_sidecar(doc, source_name="clip.mp4", source_sha256="d" * 64)
        mock_resolve.return_value = TRANSCRIBE_RESOLVED
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.slice_audio.return_value = []
        mock_transcode.probe_duration.return_value = 5.0

        media.transcribe_to_sidecar(doc, make_job_ctx())

        mock_get_transcriber_for.assert_called_once()

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_transcriber_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_incomplete_sidecar_does_not_short_circuit(
        self, mock_resolve, mock_get_engine, mock_get_transcriber_for, mock_transcode
    ):
        doc = _make_doc(source_path="/inbox-irrelevant/clip.mp4")
        doc_dir = store.document_dir(doc.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = doc_dir / "extract.json"
        sidecar_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "method": "whisper",
                    "source": "clip.mp4",
                    "source_sha256": doc.file_hash,
                    "duration_seconds": 42.0,
                    "produced_at": None,  # still in progress
                    "segments": [],
                }
            )
        )
        mock_resolve.return_value = TRANSCRIBE_RESOLVED
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_transcode.slice_audio.return_value = []
        mock_transcode.probe_duration.return_value = 42.0

        media.transcribe_to_sidecar(doc, make_job_ctx())

        mock_get_transcriber_for.assert_called_once()


# --- extract_to_sidecar (T8): vision extraction -----------------------------
#
# Every external boundary mocked the same way T7's transcription tests mock
# theirs: `tools.rag.media.resolve`/`get_engine`/`get_llm_for` (model
# resolution), plus the bound submodule references `tools.rag.media.
# transcode`/`extract`/`readers` (T7's own "@patch a whole submodule"
# convention, extended to the two new submodules this driver calls into) --
# no real rasterization, no real vision call, ever.


def _make_pdf_doc(**kwargs) -> Document:
    defaults = dict(
        title="scan.pdf",
        source_path="/inbox-irrelevant/scan.pdf",
        original_path="/inbox/scan.pdf",
        file_hash="b" * 64,
        doc_type=Document.DocType.PROSE,
        media_type="application/pdf",
        status=Document.Status.PROCESSING,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


def _make_image_doc(**kwargs) -> Document:
    defaults = dict(
        title="photo.jpg",
        source_path="/inbox-irrelevant/photo.jpg",
        original_path="/inbox/photo.jpg",
        file_hash="e" * 64,
        doc_type=Document.DocType.PROSE,
        media_type="image/jpeg",
        status=Document.Status.PROCESSING,
    )
    defaults.update(kwargs)
    return Document.objects.create(**defaults)


EXTRACT_RESOLVED = ResolvedModel("ollama", "llava", "http://localhost:11434")


# --- extract_to_sidecar: health re-check (mirrors transcription's own) ------


@pytest.mark.django_db
class TestExtractToSidecarHealthRecheck:
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", side_effect=ValueError("No inference binding resolved for role 'rag.extract'"))
    def test_unbound_role_raises_the_shared_message(self, mock_resolve, mock_get_engine):
        doc = _make_pdf_doc()

        with pytest.raises(RuntimeError) as exc_info:
            media.extract_to_sidecar(doc, make_job_ctx())

        assert str(exc_info.value) == "The image text extraction model isn't set up yet — assign one in the model console."
        mock_get_engine.assert_not_called()

    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
    def test_unreachable_role_raises_the_shared_message(self, mock_resolve, mock_get_engine):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = False
        mock_get_engine.return_value = mock_engine
        doc = _make_pdf_doc()

        with pytest.raises(RuntimeError) as exc_info:
            media.extract_to_sidecar(doc, make_job_ctx())

        assert str(exc_info.value) == "The image text extraction model is unreachable."
        mock_engine.is_healthy.assert_called_once_with(EXTRACT_RESOLVED.endpoint)

    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", side_effect=ValueError("No inference binding resolved for role 'rag.extract'"))
    def test_resolve_role_or_fail_raises_model_role_unavailable(self, mock_resolve, mock_get_engine):
        """W1: `_resolve_role_or_fail` raises `media.ModelRoleUnavailable`
        now, not a bare `RuntimeError` -- but it IS still a `RuntimeError`
        (a subclass), so `run_ingest_or_fail`/the worker's own writeback,
        which both catch `Exception`, are unaffected."""
        with pytest.raises(media.ModelRoleUnavailable) as exc_info:
            media._resolve_role_or_fail("rag.extract")

        assert isinstance(exc_info.value, RuntimeError)
        mock_get_engine.assert_not_called()


# --- extract_to_sidecar: scanned-PDF happy path ------------------------------


@pytest.mark.django_db
@patch("tools.rag.media.readers")
@patch("tools.rag.media.extract")
@patch("tools.rag.media.transcode")
@patch("tools.rag.media.get_llm_for")
@patch("tools.rag.media.get_engine")
@patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
class TestExtractToSidecarScannedPdfHappyPath:
    def _healthy(self, mock_get_engine):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine

    def test_per_page_segments_progress_checkpoint_sidecar_and_cleanup(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([1, 2], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-1-png", b"page-2-png"]
        mock_llm = MagicMock()
        mock_get_llm_for.return_value = mock_llm
        mock_extract.extract_image_text.side_effect = ["hello page one", "second page text"]

        reports: list[dict] = []
        checkpoints: list[dict] = []
        ctx = make_job_ctx(_report=reports.append, _checkpoint=checkpoints.append)

        doc = _make_pdf_doc()
        work_dir = store.document_dir(doc.id) / "work"

        sidecar = media.extract_to_sidecar(doc, ctx)

        # The LLM is resolved ONCE, threaded through every page's call.
        mock_get_llm_for.assert_called_once_with(EXTRACT_RESOLVED)
        assert mock_extract.extract_image_text.call_count == 2
        for call in mock_extract.extract_image_text.call_args_list:
            assert call.kwargs["llm"] is mock_llm

        assert sidecar["segments"] == [
            {"page": 1, "text": "hello page one"},
            {"page": 2, "text": "second page text"},
        ]
        assert sidecar["version"] == 1
        assert sidecar["method"] == "vision"
        assert sidecar["source"] == "scan.pdf"
        assert sidecar["source_sha256"] == doc.file_hash
        assert sidecar["model"] == {"engine": "ollama", "model_id": "llava"}
        assert sidecar["produced_at"] is not None
        # No duration_seconds/language keys -- neither concept applies.
        assert "duration_seconds" not in sidecar
        assert "language" not in sidecar

        sidecar_path = store.document_dir(doc.id) / "extract.json"
        assert json.loads(sidecar_path.read_text()) == sidecar

        doc.refresh_from_db()
        assert doc.extraction == {
            "method": "extraction",
            "engine": "ollama",
            "model_id": "llava",
            "connection_name": "",
            "produced_at": sidecar["produced_at"],
        }

        assert not work_dir.exists()

        assert reports == [
            {"done": 1, "total": 2, "unit": "pages", "label": "extracted"},
            {"done": 2, "total": 2, "unit": "pages", "label": "extracted"},
        ]
        assert checkpoints == [
            {"next_page_index": 1, "segments_written": 1},
            {"next_page_index": 2, "segments_written": 2},
        ]

    def test_blank_page_is_skipped_and_logged(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers, caplog
    ):
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([1, 2], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-1-png", b"page-2-png"]
        mock_get_llm_for.return_value = MagicMock()
        # Page 1 produces nothing (a blank/whitespace-only response, already
        # stripped to "" by extract_image_text itself); page 2 has real text.
        mock_extract.extract_image_text.side_effect = ["", "real text"]

        doc = _make_pdf_doc()

        import logging
        with caplog.at_level(logging.INFO):
            sidecar = media.extract_to_sidecar(doc, make_job_ctx())

        # Page 1 never became a segment at all -- not an empty one.
        assert sidecar["segments"] == [{"page": 2, "text": "real text"}]
        assert any("no extractable text" in r.message for r in caplog.records)

    def test_over_page_cap_raises_before_a_single_page_is_rasterized(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """T8 review minor 4: `RagSettings.max_document_pages`, the
        DEFENSIVE re-check inside the extraction driver.
        `tools.rag.ingest._check_document_pages` is the primary
        enforcement, and since H28/B-5 it runs at JOB START (never at
        stage time -- B-5 took that scan off the request thread), which
        leaves this one covering the narrow window between the two."""
        from tools.rag.models import RagSettings

        self._healthy(mock_get_engine)
        RagSettings.objects.create(pk=1, max_document_pages=2)
        mock_readers.pdf_textless_pages.return_value = ([1, 2, 3], False)  # 3 textless pages, over the cap of 2
        mock_get_llm_for.return_value = MagicMock()

        doc = _make_pdf_doc()

        with pytest.raises(ValueError, match="over the 2-page document limit"):
            media.extract_to_sidecar(doc, make_job_ctx())

        mock_transcode.rasterize_pdf_page.assert_not_called()
        mock_extract.extract_image_text.assert_not_called()

    def test_generation_rejected_propagates(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([1], False)
        mock_transcode.rasterize_pdf_page.return_value = b"page-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = GenerationRejected("unknown model file")

        doc = _make_pdf_doc()

        with pytest.raises(GenerationRejected, match="unknown model file"):
            media.extract_to_sidecar(doc, make_job_ctx())

    def test_rasterizes_only_textless_pages(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """W1: a mixed PDF's textless list is `[2, 4]` (out of more pages
        than that) -- only THOSE two are rasterized; the ordinary text
        pages (1, 3) are never touched here at all -- their text comes
        from the document's own text layer, merged back in at read time by
        `tools.rag.ingest._source_documents`."""
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([2, 4], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-2-png", b"page-4-png"]
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = ["page two text", "page four text"]

        doc = _make_pdf_doc()

        sidecar = media.extract_to_sidecar(doc, make_job_ctx())

        assert mock_transcode.rasterize_pdf_page.call_args_list == [
            call(Path("/inbox-irrelevant/scan.pdf"), 2),
            call(Path("/inbox-irrelevant/scan.pdf"), 4),
        ]
        assert sidecar["segments"] == [
            {"page": 2, "text": "page two text"},
            {"page": 4, "text": "page four text"},
        ]
        assert sidecar["rasterized_pages"] == [2, 4]

    def test_progress_total_is_the_textless_count(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """The progress bar's total is the TEXTLESS page count, never the
        document's total page count -- a mixed PDF's progress bar reaches
        100% at the amount of vision work this run actually does."""
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([2, 4], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-2-png", b"page-4-png"]
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = ["page two text", "page four text"]

        reports: list[dict] = []
        ctx = make_job_ctx(_report=reports.append)
        doc = _make_pdf_doc()

        media.extract_to_sidecar(doc, ctx)

        assert reports == [
            {"done": 1, "total": 2, "unit": "pages", "label": "extracted"},
            {"done": 2, "total": 2, "unit": "pages", "label": "extracted"},
        ]

    def test_page_cap_counts_rasterized_pages_only(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """The cap compares the TEXTLESS count against `RagSettings.
        max_document_pages`, never a document's total page count -- 2
        textless pages pass a cap of 2, even for a document with far more
        total pages than that."""
        from tools.rag.models import RagSettings

        self._healthy(mock_get_engine)
        RagSettings.objects.create(pk=1, max_document_pages=2)
        mock_readers.pdf_textless_pages.return_value = ([2, 4], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-2-png", b"page-4-png"]
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = ["page two text", "page four text"]

        doc = _make_pdf_doc()

        sidecar = media.extract_to_sidecar(doc, make_job_ctx())

        assert len(sidecar["segments"]) == 2

    def test_writes_the_full_planned_rasterized_pages_on_every_write(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """Review S1: `rasterized_pages` is the PLANNED set, written
        IDENTICALLY on every checkpoint write -- after the FIRST page's
        checkpoint, the sidecar on disk already carries the complete
        `[2, 4]`, not merely `[2]`."""
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([2, 4], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-2-png", b"page-4-png"]
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = ["page two text", "page four text"]

        doc = _make_pdf_doc()
        sidecar_path = store.document_dir(doc.id) / "extract.json"
        seen: dict = {}

        def _checkpoint(payload):
            if payload["next_page_index"] == 1 and "rasterized_pages" not in seen:
                seen["rasterized_pages"] = json.loads(sidecar_path.read_text())["rasterized_pages"]

        ctx = make_job_ctx(_checkpoint=_checkpoint)

        media.extract_to_sidecar(doc, ctx)

        assert seen["rasterized_pages"] == [2, 4]

    def test_with_no_textless_pages_writes_a_finished_empty_sidecar(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """An EMPTY textless list (the file gained a text layer between
        enqueue and this run) writes a finished, zero-segment sidecar and
        returns -- never raises."""
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([], False)
        mock_get_llm_for.return_value = MagicMock()

        doc = _make_pdf_doc()

        sidecar = media.extract_to_sidecar(doc, make_job_ctx())

        assert sidecar["segments"] == []
        assert sidecar["rasterized_pages"] == []
        assert sidecar["produced_at"] is not None
        mock_transcode.rasterize_pdf_page.assert_not_called()
        mock_extract.extract_image_text.assert_not_called()


# --- extract_to_sidecar: scanned-PDF resume (checkpoint mid-document) -------


@pytest.mark.django_db
class TestExtractToSidecarScannedPdfResume:
    def _write_sidecar(self, doc, segments, *, produced_at=None):
        sidecar_path = store.document_dir(doc.id) / "extract.json"
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "method": "vision",
                    "source": "scan.pdf",
                    "source_sha256": doc.file_hash,
                    "model": {"engine": "ollama", "model_id": "llava"},
                    "produced_at": produced_at,
                    "segments": segments,
                }
            )
        )
        return sidecar_path

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
    def test_resume_continues_from_the_checkpointed_page(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_readers.pdf_textless_pages.return_value = ([1, 2], False)
        mock_transcode.rasterize_pdf_page.return_value = b"page-2-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.return_value = "page two text"

        doc = _make_pdf_doc()
        self._write_sidecar(doc, [{"page": 1, "text": "page one text"}])
        ctx = make_job_ctx(checkpoint_state={"next_page_index": 1, "segments_written": 1})

        sidecar = media.extract_to_sidecar(doc, ctx)

        # Page 1 is never re-rasterized -- resume starts at page 2.
        mock_transcode.rasterize_pdf_page.assert_called_once_with(Path("/inbox-irrelevant/scan.pdf"), 2)
        assert sidecar["segments"] == [
            {"page": 1, "text": "page one text"},
            {"page": 2, "text": "page two text"},
        ]

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
    def test_resume_indexes_the_textless_list(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """`next_page_index` indexes into the TEXTLESS LIST, not the
        document's raw page numbers -- `checkpoint_state={"next_page_index":
        1}` over a textless list of `[2, 4]` means "index 0 (page 2) is
        already done", so this resume re-rasterizes page 4 ONLY, never
        page 2 again and never any of the ordinary text pages in between."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_readers.pdf_textless_pages.return_value = ([2, 4], False)
        mock_transcode.rasterize_pdf_page.return_value = b"page-4-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.return_value = "page four text"

        doc = _make_pdf_doc()
        self._write_sidecar(doc, [{"page": 2, "text": "page two text"}])
        ctx = make_job_ctx(checkpoint_state={"next_page_index": 1, "segments_written": 1})

        sidecar = media.extract_to_sidecar(doc, ctx)

        mock_transcode.rasterize_pdf_page.assert_called_once_with(Path("/inbox-irrelevant/scan.pdf"), 4)
        assert sidecar["segments"] == [
            {"page": 2, "text": "page two text"},
            {"page": 4, "text": "page four text"},
        ]

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
    def test_sidecar_ahead_of_checkpoint_is_truncated_and_redone(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """The same crash window `_reconcile_resume_state` (T7) reconciles:
        the sidecar write for page 1 landed, but the checkpoint call
        recording it crashed before completing -- next attempt's checkpoint
        is stale by one page even though the sidecar already has it."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_readers.pdf_textless_pages.return_value = ([1], False)
        mock_transcode.rasterize_pdf_page.return_value = b"page-1-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.return_value = "page one redone"

        doc = _make_pdf_doc()
        self._write_sidecar(doc, [{"page": 1, "text": "phantom unconfirmed"}])
        ctx = make_job_ctx(checkpoint_state={"next_page_index": 0, "segments_written": 0})

        sidecar = media.extract_to_sidecar(doc, ctx)

        assert mock_extract.extract_image_text.call_count == 1
        assert sidecar["segments"] == [{"page": 1, "text": "page one redone"}]

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
    def test_sidecar_behind_checkpoint_fails_loudly(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_readers.pdf_textless_pages.return_value = ([1, 2], False)

        doc = _make_pdf_doc()
        self._write_sidecar(doc, [{"page": 1, "text": "hello"}])
        ctx = make_job_ctx(checkpoint_state={"next_page_index": 2, "segments_written": 2})

        with pytest.raises(RuntimeError, match="behind its own checkpoint"):
            media.extract_to_sidecar(doc, ctx)

        mock_extract.extract_image_text.assert_not_called()

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
    def test_unreadable_sidecar_forces_full_re_extraction(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_readers.pdf_textless_pages.return_value = ([1, 2], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"p1", b"p2"]
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = ["page one redone", "page two redone"]

        doc = _make_pdf_doc()
        doc_dir = store.document_dir(doc.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        (doc_dir / "extract.json").write_text("{not valid json")

        ctx = make_job_ctx(checkpoint_state={"next_page_index": 1, "segments_written": 1})

        sidecar = media.extract_to_sidecar(doc, ctx)

        assert mock_extract.extract_image_text.call_count == 2
        assert sidecar["segments"] == [
            {"page": 1, "text": "page one redone"},
            {"page": 2, "text": "page two redone"},
        ]


# --- extract_to_sidecar: single-image path ----------------------------------


@pytest.mark.django_db
@patch("tools.rag.media.readers")
@patch("tools.rag.media.extract")
@patch("tools.rag.media.transcode")
@patch("tools.rag.media.get_llm_for")
@patch("tools.rag.media.get_engine")
@patch("tools.rag.media.resolve", return_value=EXTRACT_RESOLVED)
class TestExtractToSidecarImagePath:
    def _healthy(self, mock_get_engine):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine

    def test_single_shot_describes_then_transcribes_into_two_segments(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """RE-PINNED (preview UAT, 2026-09-17) from `test_single_shot_
        one_segment_page_one`: an image now gets a DESCRIPTION segment
        first, then its transcription. The description leads because
        `tools.rag.access._caption_from_sidecar` reads in order under a
        600-character cap -- a long OCR dump must never push the one
        thing the chat model most needs out of the caption."""
        self._healthy(mock_get_engine)
        mock_transcode.normalize_image.return_value = b"normalized-png"
        mock_llm = MagicMock()
        mock_get_llm_for.return_value = mock_llm
        mock_extract.describe_image.return_value = "A red bicycle against a brick wall."
        mock_extract.extract_image_text.return_value = "CITY CYCLES"

        doc = _make_image_doc()
        work_dir = store.document_dir(doc.id) / "work"

        sidecar = media.extract_to_sidecar(doc, make_job_ctx())

        mock_readers.pdf_textless_pages.assert_not_called()
        mock_transcode.rasterize_pdf_page.assert_not_called()
        mock_transcode.normalize_image.assert_called_once_with(Path("/inbox-irrelevant/photo.jpg"))
        # ONE resolve, ONE built LLM, threaded through BOTH calls.
        mock_get_llm_for.assert_called_once_with(EXTRACT_RESOLVED)
        assert mock_extract.describe_image.call_args.kwargs["llm"] is mock_llm
        assert mock_extract.extract_image_text.call_args.kwargs["llm"] is mock_llm

        assert sidecar["segments"] == [
            {"page": 1, "kind": "description", "text": "A red bicycle against a brick wall."},
            {"page": 1, "text": "CITY CYCLES"},
        ]
        assert sidecar["described"] is True
        assert sidecar["method"] == "vision"

        doc.refresh_from_db()
        assert doc.extraction["method"] == "extraction"
        assert not work_dir.exists()

    def test_a_textless_image_still_gets_its_description(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """RE-PINNED from `test_blank_image_produces_no_segments` -- THE
        WHOLE POINT OF THIS TASK. A photo with no writing on it used to
        produce an empty sidecar and therefore no caption, ever."""
        self._healthy(mock_get_engine)
        mock_transcode.normalize_image.return_value = b"normalized-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.describe_image.return_value = "A tabby cat asleep on a windowsill."
        mock_extract.extract_image_text.return_value = ""

        sidecar = media.extract_to_sidecar(_make_image_doc(), make_job_ctx())

        assert sidecar["segments"] == [
            {"page": 1, "kind": "description", "text": "A tabby cat asleep on a windowsill."},
        ]
        assert sidecar["described"] is True

    def test_an_image_that_yields_neither_is_a_finished_sidecar_with_no_segments(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """HONEST EMPTINESS, not a raise: a model that refused, or an
        image it could say nothing about, still finishes -- and
        `described: True` is what lets the provider call it `"empty"`
        rather than leaving it looking like a sidecar that predates this
        change (steward condition S2)."""
        self._healthy(mock_get_engine)
        mock_transcode.normalize_image.return_value = b"normalized-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.describe_image.return_value = ""
        mock_extract.extract_image_text.return_value = ""

        sidecar = media.extract_to_sidecar(_make_image_doc(), make_job_ctx())

        assert sidecar["segments"] == []
        assert sidecar["described"] is True
        assert sidecar["produced_at"] is not None

    def test_the_description_is_never_asked_for_a_scanned_pdf_page(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """THE OTHER HALF OF S1, AND THE ONE THAT PROTECTS EVERY EXISTING
        DOCUMENT: a page of a scanned contract needs its words, not a
        sentence about what a page of a contract looks like. No
        `described` key on a PDF sidecar at all -- the same "don't carry
        meaningless keys" rule `_extraction_sidecar_payload` already
        states for `duration_seconds`/`language`."""
        self._healthy(mock_get_engine)
        mock_readers.pdf_textless_pages.return_value = ([1, 2], False)
        mock_transcode.rasterize_pdf_page.side_effect = [b"page-1-png", b"page-2-png"]
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.extract_image_text.side_effect = ["page one", "page two"]

        sidecar = media.extract_to_sidecar(_make_pdf_doc(), make_job_ctx())

        mock_extract.describe_image.assert_not_called()
        assert sidecar["segments"] == [
            {"page": 1, "text": "page one"},
            {"page": 2, "text": "page two"},
        ]
        assert "described" not in sidecar


class TestTheDescriptionSegmentIsBackwardCompatible:
    """STEWARD CONDITION S1. `extract.json` is keyed on the source file
    hash and is NEVER re-produced for an already-ingested document
    (`_load_finished_sidecar_if_matching`), so both shapes are live on a
    box at once, permanently, and every reader must take both."""

    def test_an_old_page_only_sidecar_still_builds_its_documents(self):
        docs = media.documents_from_extract(
            {"segments": [{"page": 1, "text": "already extracted"}]})
        assert [d.text for d in docs] == ["already extracted"]
        assert [d.metadata["page"] for d in docs] == [1]

    def test_an_old_whisper_sidecar_is_completely_untouched(self):
        docs = media.documents_from_extract(
            {"segments": [{"start": 0.0, "end": 2.0, "text": "spoken words"}]})
        assert [d.text for d in docs] == ["spoken words"]
        assert docs[0].metadata["start_seconds"] == 0.0

    def test_a_new_described_sidecar_indexes_the_description_too(self):
        """The description is EMBEDDED, not merely displayed -- which is
        what makes `rag__search` able to find a photo by what is in it,
        and is the reason the description is a segment rather than a
        top-level key nothing reads."""
        docs = media.documents_from_extract({
            "described": True,
            "segments": [
                {"page": 1, "kind": "description", "text": "A red bicycle."},
                {"page": 1, "text": "CITY CYCLES"},
            ],
        })
        assert [d.text for d in docs] == ["A red bicycle.", "CITY CYCLES"]
        assert [d.metadata["page"] for d in docs] == [1, 1]
        assert all("kind" not in d.metadata for d in docs)


# --- extract_to_sidecar: finished-sidecar short-circuit (shared with T7) ---


@pytest.mark.django_db
class TestExtractToSidecarFinishedShortCircuit:
    def _write_finished_sidecar(self, doc, *, source_name="scan.pdf", source_sha256=None,
                                described=None):
        """`described` (review fix round 2, steward finding): `None`
        writes NO `described` key at all -- the exact shape an image
        ingested before the description prompt existed has on disk, and
        therefore the precondition for the reuse-refusal tests below."""
        doc_dir = store.document_dir(doc.id)
        doc_dir.mkdir(parents=True, exist_ok=True)
        sidecar_path = doc_dir / "extract.json"
        sidecar = {
            "version": 1,
            "method": "vision",
            "source": source_name,
            "source_sha256": source_sha256 if source_sha256 is not None else doc.file_hash,
            "model": {"engine": "ollama", "model_id": "llava"},
            "produced_at": "2026-08-24T00:00:00+00:00",
            "segments": [{"page": 1, "text": "already extracted"}],
        }
        if described is not None:
            sidecar["described"] = described
        sidecar_path.write_text(json.dumps(sidecar))
        return sidecar

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_orphaned_finished_sidecar_short_circuits_no_model_call(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        doc = _make_pdf_doc()
        finished = self._write_finished_sidecar(doc)

        result = media.extract_to_sidecar(doc, make_job_ctx())

        assert result == finished
        mock_resolve.assert_not_called()
        mock_get_engine.assert_not_called()
        mock_get_llm_for.assert_not_called()
        mock_extract.extract_image_text.assert_not_called()

        doc.refresh_from_db()
        assert doc.extraction == {
            "method": "extraction",
            "engine": "ollama",
            "model_id": "llava",
            "connection_name": "",
            "produced_at": finished["produced_at"],
        }

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_content_hash_mismatch_does_not_short_circuit(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        doc = _make_pdf_doc(file_hash="c" * 64)
        self._write_finished_sidecar(doc, source_sha256="d" * 64)
        mock_resolve.return_value = EXTRACT_RESOLVED
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_readers.pdf_textless_pages.return_value = ([], False)
        mock_get_llm_for.return_value = MagicMock()

        media.extract_to_sidecar(doc, make_job_ctx())

        mock_get_llm_for.assert_called_once()

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_an_undescribed_image_sidecar_is_not_reused_and_gets_described(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """STEWARD FINDING (binding), review fix round 2. `enqueue_reingest`
        re-hashes the SAME retained file and deletes nothing, so before
        this fix a re-ingest of an `undescribed` image matched THIS exact
        short-circuit on `produced_at`/`source`/`source_sha256` and came
        back with the OLD, undescribed sidecar verbatim -- `describe_image`
        never ran, and "re-ingest to describe it" was a dead end. A
        matching-by-hash sidecar with NO top-level `"described"` key is now
        refused for reuse (image call only) and the driver re-extracts."""
        mock_resolve.return_value = EXTRACT_RESOLVED
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_transcode.normalize_image.return_value = b"normalized-png"
        mock_get_llm_for.return_value = MagicMock()
        mock_extract.describe_image.return_value = "A red bicycle."
        mock_extract.extract_image_text.return_value = "CITY CYCLES"

        doc = _make_image_doc()
        self._write_finished_sidecar(doc, source_name="photo.jpg")  # described=None: no key

        result = media.extract_to_sidecar(doc, make_job_ctx())

        mock_get_llm_for.assert_called_once()
        mock_extract.describe_image.assert_called_once()
        mock_extract.extract_image_text.assert_called_once()
        assert result["segments"] == [
            {"page": 1, "kind": "description", "text": "A red bicycle."},
            {"page": 1, "text": "CITY CYCLES"},
        ]
        assert result["described"] is True

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_a_pdf_sidecar_with_no_described_key_is_still_reused(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """THE OTHER HALF OF S1, AND OF THIS FIX: a scanned-PDF page
        sidecar has NEVER carried `"described"` -- image-only key,
        `_extraction_sidecar_payload`'s own docstring -- so `require_
        described` must not apply to it. Reused exactly as before
        `test_orphaned_finished_sidecar_short_circuits_no_model_call`
        above already pins; this test's own point is that the NEW
        `require_described=not is_pdf` wiring does not regress it."""
        doc = _make_pdf_doc()
        finished = self._write_finished_sidecar(doc)  # described=None -- PDFs never carry it

        result = media.extract_to_sidecar(doc, make_job_ctx())

        assert result == finished
        mock_resolve.assert_not_called()
        mock_get_engine.assert_not_called()
        mock_get_llm_for.assert_not_called()
        mock_extract.extract_image_text.assert_not_called()
        mock_extract.describe_image.assert_not_called()

    @patch("tools.rag.media.readers")
    @patch("tools.rag.media.extract")
    @patch("tools.rag.media.transcode")
    @patch("tools.rag.media.get_llm_for")
    @patch("tools.rag.media.get_engine")
    @patch("tools.rag.media.resolve")
    def test_an_already_described_image_sidecar_is_still_reused(
        self, mock_resolve, mock_get_engine, mock_get_llm_for, mock_transcode, mock_extract, mock_readers
    ):
        """THE THIRD CASE: an image re-ingested a SECOND time (its sidecar
        already carries `"described": true` from the fix above's own first
        re-extraction) must not pay for a THIRD vision pass -- `require_
        described` demands the KEY be present, not that the run be fresh."""
        doc = _make_image_doc()
        finished = self._write_finished_sidecar(
            doc, source_name="photo.jpg", described=True)

        result = media.extract_to_sidecar(doc, make_job_ctx())

        assert result == finished
        mock_resolve.assert_not_called()
        mock_get_engine.assert_not_called()
        mock_get_llm_for.assert_not_called()
        mock_extract.extract_image_text.assert_not_called()
        mock_extract.describe_image.assert_not_called()


# --- group_segments ----------------------------------------------------------


class TestGroupSegments:
    def test_empty_list_returns_empty(self):
        assert media.group_segments([]) == []

    def test_single_segment_is_its_own_group(self):
        segments = [{"start": 0.0, "end": 2.0, "text": "hello"}]
        assert media.group_segments(segments) == [{"start": 0.0, "end": 2.0, "text": "hello"}]

    def test_segments_under_max_chars_combine_into_one_group(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "a"},
            {"start": 1.0, "end": 2.0, "text": "b"},
        ]
        groups = media.group_segments(segments, max_chars=100)
        assert groups == [{"start": 0.0, "end": 2.0, "text": "a b"}]

    def test_boundary_splits_into_two_groups(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "x" * 5},
            {"start": 1.0, "end": 2.0, "text": "y" * 5},
        ]
        # "xxxxx" (5) fits; adding " yyyyy" (1 + 5 = 6) would push the
        # group to 11 > 6, so it closes and a new group starts.
        groups = media.group_segments(segments, max_chars=6)
        assert groups == [
            {"start": 0.0, "end": 1.0, "text": "x" * 5},
            {"start": 1.0, "end": 2.0, "text": "y" * 5},
        ]

    def test_a_single_segment_over_max_chars_still_forms_its_own_group(self):
        long_text = "z" * 50
        segments = [{"start": 0.0, "end": 1.0, "text": long_text}]
        groups = media.group_segments(segments, max_chars=10)
        assert groups == [{"start": 0.0, "end": 1.0, "text": long_text}]


# --- documents_from_extract ---------------------------------------------------


class TestDocumentsFromExtract:
    def test_builds_one_document_per_group_with_timestamp_metadata(self):
        sidecar = {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "hello"},
                {"start": 2.0, "end": 4.0, "text": "world"},
            ]
        }

        docs = media.documents_from_extract(sidecar)

        assert len(docs) == 1
        assert docs[0].text == "hello world"
        assert docs[0].metadata == {"start_seconds": 0.0, "end_seconds": 4.0}

    def test_empty_or_missing_segments_returns_empty_list(self):
        assert media.documents_from_extract({"segments": []}) == []
        assert media.documents_from_extract({}) == []

    def test_exclusion_lists_set_on_every_returned_document(self):
        sidecar = {"segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}

        docs = media.documents_from_extract(sidecar)

        for key in media.CHUNK_METADATA_KEYS:
            assert key in docs[0].excluded_embed_metadata_keys
            assert key in docs[0].excluded_llm_metadata_keys

    def test_page_keyed_segments_build_one_document_per_page_no_grouping(self):
        """T8: vision-extraction sidecars are page-keyed
        (`{"page", "text"}`) rather than timestamp-keyed -- one `Document`
        PER segment, never combined via `group_segments`, so a two-page
        sidecar with SHORT text on each page still yields two `Document`s,
        not one merged group (unlike the timestamp-keyed branch above,
        where two short segments DO combine)."""
        sidecar = {
            "segments": [
                {"page": 1, "text": "hello page one"},
                {"page": 2, "text": "hello page two"},
            ]
        }

        docs = media.documents_from_extract(sidecar)

        assert len(docs) == 2
        assert docs[0].text == "hello page one"
        assert docs[0].metadata == {"page": 1}
        assert docs[1].text == "hello page two"
        assert docs[1].metadata == {"page": 2}

    def test_page_keyed_segments_carry_the_pollution_fix_too(self):
        sidecar = {"segments": [{"page": 1, "text": "hi"}]}

        docs = media.documents_from_extract(sidecar)

        for key in media.CHUNK_METADATA_KEYS:
            assert key in docs[0].excluded_embed_metadata_keys
            assert key in docs[0].excluded_llm_metadata_keys


# --- apply_chunk_metadata_exclusions: the pollution fix ----------------------


class TestApplyChunkMetadataExclusionsPollutionFix:
    def test_embed_and_llm_visible_text_excludes_metadata_after_a_real_split(self):
        """Proves the fix at the NODE level, after a real SentenceSplitter
        pass -- not just that the exclusion lists are set, but that
        LlamaIndex's own `get_content(metadata_mode=...)` actually honors
        them: the rendered text a real embed/LLM call would see carries
        NONE of the excluded metadata as "key: value" lines."""
        llama_doc = media.LlamaDocument(
            text=(
                "Some real prose text, long enough that SentenceSplitter actually treats it as "
                "content worth keeping around for this test to split into at least one node."
            ),
            metadata={
                "file_id": "42",
                "file_name": "notes.pdf",
                "source_path": "/documents/42/notes.pdf",
                "category": "medical",
                "page": 3,
            },
        )
        media.apply_chunk_metadata_exclusions(llama_doc)

        nodes = SentenceSplitter().get_nodes_from_documents([llama_doc])
        assert nodes

        for node in nodes:
            media.apply_chunk_metadata_exclusions(node)
            embed_text = node.get_content(metadata_mode=MetadataMode.EMBED)
            llm_text = node.get_content(metadata_mode=MetadataMode.LLM)
            for needle in ("file_id", "42", "notes.pdf", "source_path", "category", "medical", "page"):
                assert needle not in embed_text
                assert needle not in llm_text
