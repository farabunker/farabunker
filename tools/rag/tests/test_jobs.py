"""Unit tests for tools/rag/jobs.py -- the `rag.ask` job kind (T5).

Two levels: `plan_ask`/`run_ask`/`summarize_ask` are unit-tested against
mocked seams (`resolve`/`resolve_connection_named`/`get_engine`/
`answer_question`/`record_ask` -- the same "mock at the HTTP/DB boundary"
convention every other RAG test file in this app uses), and one
end-to-end test (`TestEndToEndViaWorker`) proves the whole path for real:
enqueue a `rag.ask` job through `models.contracts.queue`, run one worker tick,
and check the row + `AskRecord`.
"""
from __future__ import annotations

import concurrent.futures
import uuid
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest
from django.conf import settings

from models.contracts.bindings import ResolvedModel
from models.contracts.jobkinds import ModelRef
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_EXTRACT_ROLE, RAG_TRANSCRIBE_ROLE
from models.contracts.testing import hermetic_engine_endpoints  # noqa: F401 -- autouse fence
from identity.contracts.postures import POSTURE_OPEN
from identity.contracts.principals import OPEN_PRINCIPAL, SERVICE_PRINCIPAL
from tools.rag import ingest, jobs
from tools.rag.access import DocumentVisibility
from tools.rag.messages import UNBOUND, UNREACHABLE, model_unavailable_message
from tools.rag.models import AskRecord, Document
from tools.rag.tests._helpers import make_job_ctx, make_pdf_bytes, posture

# `run_ask`'s two full-happy-path tests below run with no accounts (the
# module's own default, untouched posture) and a payload carrying no actor
# fields, so `principal_from_payload` answers `OPEN_PRINCIPAL` and
# `document_visibility` answers this open-box value (IA-2 T11) -- see
# `tools.rag.tests.test_retrieval_visibility.TestEveryCallerBuildsItsOwnVisibility`
# for the tests that actually exercise a restricted principal.
# `owner_kind`/`owner_key` (C-03) are `OPEN_PRINCIPAL`'s own
# ("open"/"box") -- `document_visibility` threads them from whichever
# principal it is handed, unrestricted branch included.
OPEN_VISIBILITY = DocumentVisibility(True, frozenset(), True,
                                     owner_kind="open", owner_key="box")


# Two DISTINCT endpoints, matching test_views_ask.py's own "both roles
# resolve, different endpoints" convention -- lets a re-check test mark
# exactly ONE role unreachable without the endpoint-dedup logic also
# catching the other.
ANSWER_RESOLVED = ResolvedModel("ollama", "llama3.1:8b", "http://localhost:11434")
EMBED_RESOLVED = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11435", embed_dim=768)


def _resolve_side_effect(role):
    return ANSWER_RESOLVED if role == RAG_ANSWER_ROLE else EMBED_RESOLVED


# --- plan_ask ------------------------------------------------------------


class TestPlanAsk:
    @patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect)
    def test_role_default_resolves_both_roles(self, mock_resolve):
        model_refs, exclusive = jobs.plan_ask({"question": "q"})

        assert exclusive is False
        by_role = {ref.role: ref for ref in model_refs}
        assert by_role[RAG_ANSWER_ROLE].engine == "ollama"
        assert by_role[RAG_ANSWER_ROLE].model_id == "llama3.1:8b"
        assert by_role[RAG_ANSWER_ROLE].endpoint == "http://localhost:11434"
        assert by_role[RAG_ANSWER_ROLE].connection_name == ""
        # Provenance contract (models/queue/scheduler.py): the planner never
        # resolves footprints -- claim-time code fills that in fresh.
        assert by_role[RAG_ANSWER_ROLE].footprint_bytes is None
        assert by_role[RAG_EMBED_ROLE].model_id == "nomic-embed-text"
        assert by_role[RAG_EMBED_ROLE].footprint_bytes is None
        mock_resolve.assert_any_call(RAG_ANSWER_ROLE)
        mock_resolve.assert_any_call(RAG_EMBED_ROLE)

    @pytest.mark.django_db
    @patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect)
    @patch("tools.rag.jobs.resolve_connection_named")
    def test_connection_override_resolves_the_picked_connection(self, mock_resolve_named, mock_resolve):
        """`_resolve_answer` now also builds a `ModelAccess` (IA-2 T14),
        from `principal_from_payload` -- an ordinary DB read
        (`sees_all_content`), hence `django_db` on this one test rather
        than the whole mocked-seam class."""
        override = ResolvedModel("ollama", "qwen3:30b", "http://picked:11434")
        mock_resolve_named.return_value = (override, "picked model")

        model_refs, exclusive = jobs.plan_ask({"question": "q", "connection": "7"})

        mock_resolve_named.assert_called_once_with(7, "chat", access=ANY)
        by_role = {ref.role: ref for ref in model_refs}
        assert by_role[RAG_ANSWER_ROLE].model_id == "qwen3:30b"
        assert by_role[RAG_ANSWER_ROLE].endpoint == "http://picked:11434"
        assert by_role[RAG_ANSWER_ROLE].connection_name == "picked model"
        # rag.embed is never overridable -- still the role path.
        assert by_role[RAG_EMBED_ROLE].model_id == "nomic-embed-text"
        assert exclusive is False

    def test_blank_connection_is_exactly_the_role_path(self):
        with patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect) as mock_resolve, patch(
            "tools.rag.jobs.resolve_connection_named"
        ) as mock_resolve_named:
            model_refs, _ = jobs.plan_ask({"question": "q", "connection": ""})

        mock_resolve_named.assert_not_called()
        by_role = {ref.role: ref for ref in model_refs}
        assert by_role[RAG_ANSWER_ROLE].model_id == "llama3.1:8b"

    @patch(
        "tools.rag.jobs.resolve",
        side_effect=ValueError("No inference binding resolved for role 'rag.answer'"),
    )
    def test_unbound_answer_role_raises(self, mock_resolve):
        with pytest.raises(ValueError, match="rag.answer"):
            jobs.plan_ask({"question": "q"})

    def test_unbound_embed_role_raises(self):
        def resolve_side_effect(role):
            if role == RAG_ANSWER_ROLE:
                return ANSWER_RESOLVED
            raise ValueError("No inference binding resolved for role 'rag.embed'")

        with patch("tools.rag.jobs.resolve", side_effect=resolve_side_effect):
            with pytest.raises(ValueError, match="rag.embed"):
                jobs.plan_ask({"question": "q"})

    @pytest.mark.django_db
    @patch(
        "tools.rag.jobs.resolve_connection_named",
        side_effect=ValueError("No connection resolved for pk 999"),
    )
    def test_unknown_connection_pk_raises(self, mock_resolve_named):
        with pytest.raises(ValueError, match="999"):
            jobs.plan_ask({"question": "q", "connection": "999"})


# --- run_ask ---------------------------------------------------------------


class TestRunAsk:
    """`run_ask` now builds its own `DocumentVisibility` (IA-2 T11) via
    `tools.rag.access.document_visibility(principal_from_payload(payload))`
    before calling `answer_question` -- a read that touches
    `IdentitySettings`, hence the class-level `django_db` marker (the
    three re-check-failure tests below never reach that call, since a
    `RuntimeError` from `_precheck` fires first, but the marker costs them
    nothing)."""

    pytestmark = pytest.mark.django_db

    def _models(self) -> list[ModelRef]:
        """The claimed job's (possibly stale) ModelRef snapshot -- run_ask
        must re-resolve fresh rather than trust this."""
        return [
            ModelRef(
                role=RAG_ANSWER_ROLE, engine="ollama", endpoint="http://stale:1", model_id="stale-model"
            ),
            ModelRef(
                role=RAG_EMBED_ROLE, engine="ollama", endpoint="http://stale:2", model_id="stale-embedder"
            ),
        ]

    @patch("tools.rag.jobs.services.record_ask")
    @patch("tools.rag.jobs.answer_question")
    @patch("tools.rag.jobs.answer_role_primary", return_value=("workstation llama", 1))
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect)
    def test_happy_path_reresolves_fresh_and_records(
        self, mock_resolve, mock_get_engine, mock_primary, mock_answer_question, mock_record_ask
    ):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_answer_question.return_value = {
            "answer": "42",
            "citations": [{"title": "doc", "score": 0.5}],
        }

        payload = {"question": "What is the answer?", "category": "General"}
        # T11 review: `visibility=OPEN_VISIBILITY` below is posture-
        # dependent (the payload carries no actor, so `run_ask` resolves
        # `OPEN_PRINCIPAL`, whose `document_visibility` answer differs by
        # posture) -- pinned explicitly rather than left to this module's
        # untouched default, since this file carries no sweep fixture.
        with posture(POSTURE_OPEN):
            result = jobs.run_ask(payload, self._models(), make_job_ctx())

        # Re-resolved (fresh) models are what actually reach answer_question
        # -- never the stale queued snapshot.
        mock_answer_question.assert_called_once_with(
            "What is the answer?",
            category="General",
            answer_resolved=ANSWER_RESOLVED,
            embed_resolved=EMBED_RESOLVED,
            visibility=OPEN_VISIBILITY,
        )
        mock_record_ask.assert_called_once_with(
            question="What is the answer?",
            category="General",
            connection_name="workstation llama",
            model_id="llama3.1:8b",
            answer="42",
            citations=[{"title": "doc", "score": 0.5}],
            actor=OPEN_PRINCIPAL,
        )
        assert result["answer"] == "42"
        assert result["citations"] == [{"title": "doc", "score": 0.5}]
        assert result["answered_by"] == "workstation llama"
        assert result["summary"] == "What is the answer?"
        assert result.keys() >= {"answer", "citations", "answered_by", "summary"}

    @patch("tools.rag.jobs.services.record_ask")
    @patch("tools.rag.jobs.answer_question")
    @patch("tools.rag.jobs.answer_role_primary")
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.jobs.resolve_connection_named")
    @patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect)
    def test_connection_override_answered_by_names_the_picked_connection_not_the_role_primary(
        self,
        mock_resolve,
        mock_resolve_named,
        mock_get_engine,
        mock_primary,
        mock_answer_question,
        mock_record_ask,
    ):
        override = ResolvedModel("ollama", "qwen3:30b", "http://picked:11434")
        mock_resolve_named.return_value = (override, "picked model")
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_answer_question.return_value = {"answer": "42", "citations": []}

        # T11 review: same posture pin as the happy-path test above, and
        # for the same reason -- `visibility=OPEN_VISIBILITY` below only
        # holds under the open posture.
        with posture(POSTURE_OPEN):
            result = jobs.run_ask({"question": "q", "connection": "7"}, [], make_job_ctx())

        mock_answer_question.assert_called_once_with(
            "q", category=None, answer_resolved=override, embed_resolved=EMBED_RESOLVED,
            visibility=OPEN_VISIBILITY,
        )
        assert result["answered_by"] == "picked model"
        mock_primary.assert_not_called()
        assert mock_record_ask.call_args.kwargs["connection_name"] == "picked model"
        assert mock_record_ask.call_args.kwargs["model_id"] == "qwen3:30b"

    def test_both_roles_unbound_raises_with_the_shared_message(self):
        with patch("tools.rag.jobs.resolve", side_effect=ValueError("nope")):
            with pytest.raises(RuntimeError) as exc_info:
                jobs.run_ask({"question": "q"}, [], make_job_ctx())

        causes = {RAG_ANSWER_ROLE: UNBOUND, RAG_EMBED_ROLE: UNBOUND}
        assert str(exc_info.value) == model_unavailable_message(causes, {})

    def test_embed_unreachable_raises_with_the_shared_message(self):
        """Distinct endpoints (module-level `ANSWER_RESOLVED`/`EMBED_RESOLVED`):
        only the embed endpoint fails health, so only `rag.embed` is named --
        proving the re-check dedups by endpoint like `tools.rag.views._precheck_models`
        does, not by role."""
        mock_engine = MagicMock()
        mock_engine.is_healthy.side_effect = lambda endpoint: endpoint == ANSWER_RESOLVED.endpoint

        with patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect), patch(
            "tools.rag.messages.get_engine", return_value=mock_engine
        ):
            with pytest.raises(RuntimeError) as exc_info:
                jobs.run_ask({"question": "q"}, [], make_job_ctx())

        causes = {RAG_EMBED_ROLE: UNREACHABLE}
        resolved = {RAG_ANSWER_ROLE: ANSWER_RESOLVED, RAG_EMBED_ROLE: EMBED_RESOLVED}
        assert str(exc_info.value) == model_unavailable_message(causes, resolved)

    def test_mixed_causes_raise_with_the_shared_message(self):
        def resolve_side_effect(role):
            if role == RAG_ANSWER_ROLE:
                raise ValueError("nope")
            return EMBED_RESOLVED

        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = False

        with patch("tools.rag.jobs.resolve", side_effect=resolve_side_effect), patch(
            "tools.rag.messages.get_engine", return_value=mock_engine
        ):
            with pytest.raises(RuntimeError) as exc_info:
                jobs.run_ask({"question": "q"}, [], make_job_ctx())

        causes = {RAG_ANSWER_ROLE: UNBOUND, RAG_EMBED_ROLE: UNREACHABLE}
        resolved = {RAG_EMBED_ROLE: EMBED_RESOLVED}
        assert str(exc_info.value) == model_unavailable_message(causes, resolved)

    @patch("tools.rag.jobs.services.record_ask", side_effect=RuntimeError("db is on fire"))
    @patch("tools.rag.jobs.answer_question")
    @patch("tools.rag.jobs.answer_role_primary", return_value=("workstation llama", 1))
    @patch("tools.rag.messages.get_engine")
    @patch("tools.rag.jobs.resolve", side_effect=_resolve_side_effect)
    def test_record_ask_failure_is_swallowed_not_raised(
        self, mock_resolve, mock_get_engine, mock_primary, mock_answer_question, mock_record_ask
    ):
        mock_engine = MagicMock()
        mock_engine.is_healthy.return_value = True
        mock_get_engine.return_value = mock_engine
        mock_answer_question.return_value = {"answer": "42", "citations": []}

        result = jobs.run_ask({"question": "q"}, [], make_job_ctx())

        assert result["answer"] == "42"
        mock_record_ask.assert_called_once()


# --- summarize_ask -----------------------------------------------------


class TestSummarizeAsk:
    def test_short_question_is_returned_unchanged(self):
        assert jobs.summarize_ask({"question": "short question"}) == "short question"

    def test_long_question_is_truncated_to_120_chars(self):
        long_question = "x" * 200

        summary = jobs.summarize_ask({"question": long_question})

        assert len(summary) == 120
        assert summary == ("x" * 119) + "…"

    def test_missing_question_key_returns_empty_string(self):
        assert jobs.summarize_ask({}) == ""


# --- end-to-end: enqueue -> claim -> worker tick -> succeeded -----------


class _FakeInferenceEngine:
    """Minimal `InferenceEngine` stub -- registered under a fake engine name
    so the pre-run re-check's health check touches no real network, matching
    `models/queue/tests/test_worker.py`'s `FakeEngine` convention (mock at
    the adapter/HTTP boundary, not deeper)."""

    name = "test-inference"
    well_known_ports: tuple[int, ...] = ()

    def is_healthy(self, endpoint, timeout=None):
        return True

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused, answer_question is mocked
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused
        raise NotImplementedError


@pytest.mark.django_db(transaction=True)
class TestEndToEndViaWorker:
    """Proves the whole path for real: enqueue a `rag.ask` job through
    `models.contracts.queue` (using the REAL, globally-registered `rag.ask`
    kind -- no test-only stand-in kind, unlike `test_worker.py`'s
    `test.echo`), run one worker tick, and check the row reaches
    `succeeded` with the answer in `result` and an `AskRecord` written.

    `transaction=True`: the job actually runs on a separate pool thread with
    its own DB connection/session -- plain `@pytest.mark.django_db`'s
    implicit per-test transaction (held open on the MAIN thread's
    connection) would make the rows created below invisible to that other
    session entirely (default READ COMMITTED isolation never sees another
    session's uncommitted work) -- see `models/queue/tests/test_worker.py`'s
    `TestJobExecution` docstring for the identical reasoning.

    `answer_question` itself is mocked (the "mocked HTTP inference" seam):
    the retrieval pipeline's own correctness -- explicit LLM/embed-model
    construction, zero global `Settings` reads -- is already proven by
    `test_retrieval.py`; this test's job is queue mechanics (plan -> claim
    -> run -> record -> succeeded), not re-proving retrieval.
    """

    @pytest.fixture(autouse=True)
    def _register_fake_engine(self):
        from models.contracts.engines import ENGINES, register

        register(_FakeInferenceEngine())
        yield
        ENGINES.pop("test-inference", None)

    @patch("tools.rag.jobs.answer_question")
    def test_enqueued_ask_job_succeeds_via_one_worker_tick(self, mock_answer_question):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.models import SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue

        answer_conn = ModelConnection.objects.create(
            name="workstation llama",
            engine="test-inference",
            endpoint="http://fake-inference:1",
            model_id="llama3.1:8b",
            capabilities=["chat"],
        )
        embed_conn = ModelConnection.objects.create(
            name="workstation embed",
            engine="test-inference",
            endpoint="http://fake-inference:1",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_ANSWER_ROLE, connection=answer_conn)
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        mock_answer_question.return_value = {
            "answer": "The sky is blue.",
            "citations": [{"title": "sky.md", "score": 0.9}],
        }

        job_id = enqueue("rag.ask", {"question": "Why is the sky blue?"})

        worker = Worker(worker_id="test-worker")
        try:
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        finally:
            worker._executor.shutdown(wait=True)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == SUCCEEDED
        assert job.result["answer"] == "The sky is blue."
        assert job.result["answered_by"] == "workstation llama"
        assert job.result["citations"] == [{"title": "sky.md", "score": 0.9}]
        assert job.finished_at is not None

        record = AskRecord.objects.get()
        assert record.question == "Why is the sky blue?"
        assert record.answer == "The sky is blue."
        assert record.connection_name == "workstation llama"
        assert record.model_id == "llama3.1:8b"


# --- plan_ingest -------------------------------------------------------------


class TestPlanIngest:
    @patch("tools.rag.jobs.resolve")
    def test_prose_medium_resolves_embed_role_non_exclusive(self, mock_resolve):
        mock_resolve.return_value = EMBED_RESOLVED

        model_refs, exclusive = jobs.plan_ingest({"medium": "prose"})

        assert exclusive is False
        assert len(model_refs) == 1
        assert model_refs[0].role == RAG_EMBED_ROLE
        assert model_refs[0].model_id == "nomic-embed-text"
        assert model_refs[0].endpoint == EMBED_RESOLVED.endpoint
        # Provenance contract (models/queue/scheduler.py): the planner never
        # resolves footprints -- claim-time code fills that in fresh.
        assert model_refs[0].footprint_bytes is None
        mock_resolve.assert_called_once_with(RAG_EMBED_ROLE)

    def test_tabular_medium_is_model_free(self):
        model_refs, exclusive = jobs.plan_ingest({"medium": "tabular"})

        assert model_refs == []
        assert exclusive is False

    @patch("tools.rag.jobs.resolve")
    def test_image_medium_resolves_extract_and_embed_roles_non_exclusive(self, mock_resolve):
        extract_resolved = ResolvedModel("ollama", "llava", "http://localhost:11434")

        def side_effect(role):
            return extract_resolved if role == RAG_EXTRACT_ROLE else EMBED_RESOLVED

        mock_resolve.side_effect = side_effect

        model_refs, exclusive = jobs.plan_ingest({"medium": "image"})

        assert exclusive is False
        assert len(model_refs) == 2
        by_role = {ref.role: ref for ref in model_refs}
        assert by_role[RAG_EXTRACT_ROLE].engine == "ollama"
        assert by_role[RAG_EXTRACT_ROLE].model_id == "llava"
        assert by_role[RAG_EXTRACT_ROLE].footprint_bytes is None
        assert by_role[RAG_EMBED_ROLE].model_id == "nomic-embed-text"
        assert by_role[RAG_EMBED_ROLE].footprint_bytes is None
        mock_resolve.assert_any_call(RAG_EXTRACT_ROLE)
        mock_resolve.assert_any_call(RAG_EMBED_ROLE)

    @patch("tools.rag.jobs.resolve")
    def test_pdf_scanned_medium_resolves_extract_and_embed_roles_non_exclusive(self, mock_resolve):
        """The scanned-PDF auto-detect (`tools.rag.ingest.
        _needs_vision_extraction`) records `medium="pdf-scanned"` in the
        enqueue payload -- this planner treats it identically to "image"."""
        extract_resolved = ResolvedModel("ollama", "llava", "http://localhost:11434")

        def side_effect(role):
            return extract_resolved if role == RAG_EXTRACT_ROLE else EMBED_RESOLVED

        mock_resolve.side_effect = side_effect

        model_refs, exclusive = jobs.plan_ingest({"medium": "pdf-scanned"})

        assert exclusive is False
        by_role = {ref.role: ref for ref in model_refs}
        assert by_role[RAG_EXTRACT_ROLE].model_id == "llava"
        assert by_role[RAG_EMBED_ROLE].model_id == "nomic-embed-text"

    @patch("tools.rag.jobs.resolve")
    def test_plan_ingest_falls_back_to_embed_only_when_the_extract_role_is_unbound(self, mock_resolve):
        """W1 decision D4 (enqueue-time half): D2's routing change means
        nearly every real-world mixed/scanned PDF now trips vision
        extraction -- an unbound `rag.extract` must not fail the ENQUEUE
        (with a misleading "queue unavailable" `status_detail`, review M5);
        this planner falls back to reserving `rag.embed` alone, and the
        honest failure (or the mixed-PDF text-only fallback) happens at
        RUN time instead, via `tools.rag.ingest.run_ingest_for`'s own
        `media.ModelRoleUnavailable` catch."""

        def side_effect(role):
            if role == RAG_EXTRACT_ROLE:
                raise ValueError("No inference binding resolved for role 'rag.extract'")
            return EMBED_RESOLVED

        mock_resolve.side_effect = side_effect

        model_refs, exclusive = jobs.plan_ingest({"medium": "pdf-scanned"})

        assert exclusive is False
        assert len(model_refs) == 1
        assert model_refs[0].role == RAG_EMBED_ROLE
        assert model_refs[0].model_id == "nomic-embed-text"

    @patch("tools.rag.jobs.resolve")
    def test_plan_ingest_falls_back_to_embed_only_for_an_unbound_image_extract_role_too(self, mock_resolve):
        """The fallback applies to `"image"` identically to
        `"pdf-scanned"` -- this planner has no file to re-probe and no
        reason to special-case one medium's enqueue-time resolve over the
        other; `run_ingest_for`'s own `.pdf`-only gate (review N1) is what
        keeps an image's eventual failure honest, not this planner."""

        def side_effect(role):
            if role == RAG_EXTRACT_ROLE:
                raise ValueError("No inference binding resolved for role 'rag.extract'")
            return EMBED_RESOLVED

        mock_resolve.side_effect = side_effect

        model_refs, exclusive = jobs.plan_ingest({"medium": "image"})

        assert exclusive is False
        assert len(model_refs) == 1
        assert model_refs[0].role == RAG_EMBED_ROLE

    def test_unknown_medium_raises(self):
        with pytest.raises(ValueError, match="not a supported ingest medium"):
            jobs.plan_ingest({"medium": "carrier-pigeon"})

    @patch("tools.rag.jobs.resolve", side_effect=ValueError("No inference binding resolved for role 'rag.embed'"))
    def test_unbound_embed_role_raises(self, mock_resolve):
        with pytest.raises(ValueError, match="rag.embed"):
            jobs.plan_ingest({"medium": "prose"})

    @pytest.mark.parametrize("medium", ["video", "audio"])
    @patch("tools.rag.jobs.resolve")
    def test_av_medium_resolves_transcribe_and_embed_roles_non_exclusive(self, mock_resolve, medium):
        transcribe_resolved = ResolvedModel("whisper", "ggml-base.en", "http://localhost:8080")

        def side_effect(role):
            return transcribe_resolved if role == RAG_TRANSCRIBE_ROLE else EMBED_RESOLVED

        mock_resolve.side_effect = side_effect

        model_refs, exclusive = jobs.plan_ingest({"medium": medium})

        assert exclusive is False
        assert len(model_refs) == 2
        by_role = {ref.role: ref for ref in model_refs}
        assert by_role[RAG_TRANSCRIBE_ROLE].engine == "whisper"
        assert by_role[RAG_TRANSCRIBE_ROLE].model_id == "ggml-base.en"
        assert by_role[RAG_TRANSCRIBE_ROLE].footprint_bytes is None
        assert by_role[RAG_EMBED_ROLE].model_id == "nomic-embed-text"
        assert by_role[RAG_EMBED_ROLE].footprint_bytes is None
        mock_resolve.assert_any_call(RAG_TRANSCRIBE_ROLE)
        mock_resolve.assert_any_call(RAG_EMBED_ROLE)

    @patch(
        "tools.rag.jobs.resolve",
        side_effect=ValueError("No inference binding resolved for role 'rag.transcribe'"),
    )
    def test_unbound_transcribe_role_raises(self, mock_resolve):
        with pytest.raises(ValueError, match="rag.transcribe"):
            jobs.plan_ingest({"medium": "video"})

    @patch("tools.rag.jobs.resolve")
    def test_av_medium_resolves_with_the_media_flag_off(self, mock_resolve, settings):
        """T7 review m3: the "media" flag gates NEW staging only -- an
        already-staged video/audio Document's own re-ingest (retry,
        `reencode_all`) keeps working through this planner regardless of
        the flag's current value; only NEW intake stops. Pinned here as a
        planner-level unit test; `TestReingestOnFlagOff` below proves the
        fuller reingest-still-works path end to end."""
        settings.FARABUNKER_FEATURES = frozenset()  # media OFF
        transcribe_resolved = ResolvedModel("whisper", "ggml-base.en", "http://localhost:8080")

        def side_effect(role):
            return transcribe_resolved if role == RAG_TRANSCRIBE_ROLE else EMBED_RESOLVED

        mock_resolve.side_effect = side_effect

        model_refs, exclusive = jobs.plan_ingest({"medium": "video"})

        assert exclusive is False
        assert len(model_refs) == 2


# --- run_ingest ----------------------------------------------------------


@pytest.mark.django_db
class TestRunIngest:
    def _doc(self, **kwargs):
        defaults = dict(
            title="notes.md",
            source_path="/tmp/store/1/notes.md",
            original_path="/tmp/inbox/notes.md",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            status=Document.Status.PENDING,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_missing_document_raises_without_touching_any_row(self):
        with pytest.raises(RuntimeError, match="999999"):
            jobs.run_ingest({"document_id": 999999, "sha256": "a" * 64, "title": "x"}, [], make_job_ctx())

    @patch("tools.rag.jobs.ingest.run_ingest_or_fail")
    def test_happy_path_returns_summary_and_document_id(self, mock_run_ingest_or_fail):
        doc = self._doc()
        mock_run_ingest_or_fail.return_value = {"document_id": doc.id, "title": doc.title}
        ctx = make_job_ctx()

        result = jobs.run_ingest(
            {"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
            [],
            ctx,
        )

        # T7: ctx is now threaded through to run_ingest_or_fail (which
        # forwards it to media.transcribe_to_sidecar for a video/audio
        # medium) -- proven here at the mock boundary; the real wiring is
        # proven by test_ingest.py/test_media.py.
        mock_run_ingest_or_fail.assert_called_once_with(doc, doc.file_hash, ctx)
        assert result == {"summary": f"Ingested {doc.title}", "document_id": doc.id}

    @patch("tools.rag.jobs.ingest.run_ingest_or_fail", side_effect=RuntimeError("parse blew up"))
    def test_failure_propagates_the_same_exception(self, mock_run_ingest_or_fail):
        """The FAILED write itself is `run_ingest_or_fail`'s own job now
        (see `tools.rag.ingest.run_ingest_or_fail`'s docstring, and
        `TestRunIngestOrFail` in test_ingest.py for that write's own
        tests) -- `run_ingest_or_fail` is mocked here, so this only proves
        `jobs.run_ingest` calls it with the right args and lets its
        exception propagate unchanged; `TestRunIngestFailureIntegration`
        below proves the two are actually wired together for real."""
        doc = self._doc()
        ctx = make_job_ctx()

        with pytest.raises(RuntimeError, match="parse blew up"):
            jobs.run_ingest(
                {"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
                [],
                ctx,
            )

        mock_run_ingest_or_fail.assert_called_once_with(doc, doc.file_hash, ctx)


@pytest.mark.django_db
class TestRunIngestFailureIntegration:
    """No mock on `tools.rag.ingest.run_ingest_or_fail`/`run_ingest_for`
    themselves -- proves `jobs.run_ingest` really ends up with a FAILED
    Document row (and the job's own re-raised error matching it) through
    the real shared wrapper, not just that it calls a mock correctly.
    Mocks only the same HTTP/index boundary `test_ingest.py::
    TestRunIngestFor` mocks."""

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_ingest_job_failure_writes_failed_with_the_same_string_as_the_job_error(
        self, mock_gateway, mock_rag_index, tmp_path, settings
    ):
        # Redirect the managed store so stage_document's real copy never
        # touches the repo-local data/ directory (see test_ingest.py's own
        # `_managed_store` autouse fixture -- this file has no such
        # fixture of its own, so it's set explicitly here).
        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        mock_rag_index.get_index.side_effect = RuntimeError("index is down")

        path = tmp_path / "notes.md"
        path.write_text("some content")
        doc, _ = ingest.stage_document(str(path), move=False)

        with pytest.raises(RuntimeError, match="index is down"):
            jobs.run_ingest(
                {"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
                [],
                make_job_ctx(),
            )

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "index is down"


# --- summarize_ingest ----------------------------------------------------


class TestSummarizeIngest:
    def test_short_title_is_returned_unchanged(self):
        assert jobs.summarize_ingest({"title": "notes.md"}) == "notes.md"

    def test_long_title_is_truncated_to_120_chars(self):
        long_title = "x" * 200

        summary = jobs.summarize_ingest({"title": long_title})

        assert len(summary) == 120
        assert summary == ("x" * 119) + "…"

    def test_missing_title_key_returns_empty_string(self):
        assert jobs.summarize_ingest({}) == ""


def test_every_job_preview_truncates_at_the_same_width():
    """C-31, second half. Three call sites truncate a preview to 120
    characters and each docstring points at the other two. One constant,
    one function."""
    assert jobs.PREVIEW_CHARS == 120
    assert jobs._preview("x" * 500).endswith("…")
    assert len(jobs._preview("x" * 500)) <= jobs.PREVIEW_CHARS + 1


# --- on_ingest_terminal (T9.5 audit §5, the stranded-Document fix) --------


@pytest.mark.django_db
class TestOnIngestTerminal:
    """Direct, unit-level tests of `jobs.on_ingest_terminal` itself --
    `TestCancelQueuedRagIngestJob` below and `models/queue/tests/
    test_claim.py::TestOrphanSweep`'s own `rag.ingest`-kind test cover the
    real `cancel_job`/`_sweep_orphans` -> `invoke_on_terminal` wiring end
    to end; these prove this function's own status/detail logic in
    isolation, including the cases neither of those two entry points ever
    actually reaches."""

    def _doc(self, **kwargs):
        defaults = dict(
            title="stranded.txt",
            source_path="/tmp/irrelevant/stranded.txt",
            original_path="/tmp/irrelevant/stranded.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            status=Document.Status.PENDING,
        )
        defaults.update(kwargs)
        return Document.objects.create(**defaults)

    def test_cancelled_state_writes_the_cancelled_copy(self):
        doc = self._doc(status=Document.Status.PENDING)

        jobs.on_ingest_terminal({"document_id": doc.id}, "cancelled")

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "Cancelled from the queue before it finished."

    def test_failed_state_writes_the_stopped_responding_copy(self):
        doc = self._doc(status=Document.Status.PROCESSING)

        jobs.on_ingest_terminal({"document_id": doc.id}, "failed")

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == (
            "The worker running this job stopped responding; it was not retried again."
        )

    def test_pending_and_processing_are_both_eligible(self):
        for status in (Document.Status.PENDING, Document.Status.PROCESSING):
            doc = self._doc(status=status, original_path=f"/tmp/irrelevant/{status}.txt")

            jobs.on_ingest_terminal({"document_id": doc.id}, "cancelled")

            doc.refresh_from_db()
            assert doc.status == Document.Status.FAILED

    @pytest.mark.parametrize("status", [Document.Status.READY, Document.Status.FAILED])
    def test_already_terminal_document_is_left_untouched(self, status):
        doc = self._doc(status=status, status_detail="pre-existing detail")

        jobs.on_ingest_terminal({"document_id": doc.id}, "cancelled")

        doc.refresh_from_db()
        assert doc.status == status
        assert doc.status_detail == "pre-existing detail"

    def test_race_a_still_alive_stale_worker_writing_ready_is_not_clobbered(self):
        """T9.5 review M1: the real race a read-then-save has and a
        conditional `UPDATE` doesn't. `_sweep_orphans` gives up on a
        worker it believes is dead and permanently fails its job -- but
        the worker may not actually be dead, just slow to heartbeat, and
        can still finish its own run and write `doc.status = READY`
        AFTER the sweep's own terminal write but BEFORE (or during) this
        hook's own write. A plain `doc = Document.objects.get(...)` then
        `doc.save()` would read PROCESSING, decide to write FAILED, and
        clobber the concurrently-written READY row -- silently discarding
        a completed ingest. The fix is ONE conditional `UPDATE`
        (`status__in=(PENDING, PROCESSING)`), which can only ever match
        (and only ever write) a row that is STILL PENDING/PROCESSING at
        the instant of the write itself -- a row already flipped to READY,
        whenever that flip happened, is simply not matched. Modeled here
        by setting READY before calling the hook (the observable outcome
        is identical regardless of whether the flip lands a millisecond
        before this call or, if it somehow could, mid-call): the READY
        status and its real `status_detail` must both survive untouched,
        never overwritten with `on_ingest_terminal`'s own FAILED copy."""
        doc = self._doc(status=Document.Status.PROCESSING)
        Document.objects.filter(pk=doc.id).update(
            status=Document.Status.READY, status_detail="ingested successfully"
        )

        jobs.on_ingest_terminal({"document_id": doc.id}, "failed")

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert doc.status_detail == "ingested successfully"

    def test_missing_document_logs_and_returns_without_raising(self, caplog):
        with caplog.at_level("WARNING"):
            jobs.on_ingest_terminal({"document_id": 999999}, "cancelled")

        assert any("no longer exists" in record.message for record in caplog.records)

    def test_already_terminal_document_logs_at_debug_not_warning(self, caplog):
        doc = self._doc(status=Document.Status.READY, status_detail="pre-existing detail")

        with caplog.at_level("DEBUG"):
            jobs.on_ingest_terminal({"document_id": doc.id}, "cancelled")

        assert any(
            record.levelname == "DEBUG" and "already left PENDING/PROCESSING" in record.message
            for record in caplog.records
        )
        assert not any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.django_db
def test_the_two_terminal_handlers_repair_a_stranded_row_the_same_way(caplog):
    """C-31. Both leave a PENDING/PROCESSING row FAILED with a
    state-specific detail, and both log at debug when the row was already
    terminal and at warning when it is gone. The conditional filter is the
    race guard: a still-alive stale worker that wrote READY must not be
    clobbered."""
    ingest_doc = Document.objects.create(
        title="stranded.txt", source_path="/tmp/irrelevant/ingest.txt",
        original_path="/tmp/irrelevant/ingest.txt", file_hash="a" * 64,
        doc_type=Document.DocType.PROSE, status=Document.Status.PENDING,
    )
    conversation_id = uuid.uuid4()
    note_doc = Document.objects.create(
        title="Notes — stranded", source_path="/tmp/irrelevant/note.txt",
        original_path="/tmp/irrelevant/note.txt", file_hash="b" * 64,
        doc_type=Document.DocType.PROSE, status=Document.Status.PENDING,
        origin=Document.Origin.NOTES, notes_conversation_id=conversation_id,
    )

    jobs.on_ingest_terminal({"document_id": ingest_doc.id}, "cancelled")
    jobs.on_consolidate_terminal({"conversation": str(conversation_id)}, "cancelled")

    ingest_doc.refresh_from_db()
    note_doc.refresh_from_db()
    assert ingest_doc.status == Document.Status.FAILED
    assert ingest_doc.status_detail == "Cancelled from the queue before it finished."
    assert note_doc.status == Document.Status.FAILED
    assert note_doc.status_detail == (
        "The consolidation job ended before it could rebuild this note. "
        "Re-consolidate from the workstream page."
    )

    # The race guard: a still-alive stale worker's READY write must survive
    # the handler running after it, for both kinds.
    ready_ingest = Document.objects.create(
        title="ready.txt", source_path="/tmp/irrelevant/ready-ingest.txt",
        original_path="/tmp/irrelevant/ready-ingest.txt", file_hash="c" * 64,
        doc_type=Document.DocType.PROSE, status=Document.Status.READY,
        status_detail="ingested successfully",
    )
    ready_conversation_id = uuid.uuid4()
    ready_note = Document.objects.create(
        title="Notes — ready", source_path="/tmp/irrelevant/ready-note.txt",
        original_path="/tmp/irrelevant/ready-note.txt", file_hash="d" * 64,
        doc_type=Document.DocType.PROSE, status=Document.Status.READY,
        status_detail="", origin=Document.Origin.NOTES,
        notes_conversation_id=ready_conversation_id,
    )

    with caplog.at_level("DEBUG"):
        jobs.on_ingest_terminal({"document_id": ready_ingest.id}, "failed")
        jobs.on_consolidate_terminal({"conversation": str(ready_conversation_id)}, "failed")
        jobs.on_ingest_terminal({"document_id": 999999}, "failed")

    ready_ingest.refresh_from_db()
    ready_note.refresh_from_db()
    assert ready_ingest.status == Document.Status.READY
    assert ready_ingest.status_detail == "ingested successfully"
    assert ready_note.status == Document.Status.READY
    assert ready_note.status_detail == ""

    # Already-terminal rows are logged at debug for both kinds; a row
    # that is simply gone (on_ingest_terminal's extra `.exists()` branch)
    # is logged at warning.
    assert any(
        record.levelname == "DEBUG" and "already left PENDING/PROCESSING" in record.message
        for record in caplog.records
    )
    assert any(
        record.levelname == "DEBUG" and "nothing to clear" in record.message
        for record in caplog.records
    )
    assert any(
        record.levelname == "WARNING" and "no longer exists" in record.message
        for record in caplog.records
    )


# --- cancel_job -> on_terminal, end to end (T9.5 audit §5) ----------------


@pytest.mark.django_db
class TestCancelQueuedRagIngestJob:
    """The real `models.queue.backend.cancel_job` against the REAL,
    globally-registered `rag.ingest` `JobKind` (`tools/rag/apps.py`) --
    proves the whole wire-up, not just `on_ingest_terminal` in isolation."""

    def test_cancelling_a_queued_ingest_job_fails_its_document_and_retry_accepts_it(
        self, tmp_path, settings, django_capture_on_commit_callbacks
    ):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.backend import cancel_job
        from models.contracts.queue import enqueue

        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        embed_conn = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint="http://fake-inference:1",
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        path = tmp_path / "notes.md"
        path.write_text("some prose content")
        doc, changed = ingest.stage_document(str(path), move=False)
        assert changed is True
        assert doc.status == Document.Status.PENDING

        job_id = enqueue(
            "rag.ingest",
            {"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
        )

        with django_capture_on_commit_callbacks(execute=True):
            outcome = cancel_job(job_id)

        assert outcome == "cancelled"

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == "Cancelled from the queue before it finished."

        # Retry accepts it: `document_reingest`'s own refusal is keyed on
        # PENDING/PROCESSING ("already being processed") -- a FAILED
        # Document is exactly the state `enqueue_reingest` is meant to
        # re-queue from.
        new_job_id = ingest.enqueue_reingest(doc, actor=SERVICE_PRINCIPAL)
        assert new_job_id is not None
        doc.refresh_from_db()
        assert doc.status == Document.Status.PENDING


@pytest.mark.django_db
class TestCancelQueuedRagAskJobHasNoDocumentSideEffect:
    """`rag.ask` has no `on_terminal` hook at all (module docstring /
    `tools/rag/apps.py`'s own registration comment) -- cancelling a
    queued one must simply cancel, with no Document (there IS no
    Document) and no crash."""

    def test_cancel_completes_without_a_document_or_a_crash(self, django_capture_on_commit_callbacks):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.backend import cancel_job
        from models.contracts.queue import enqueue

        answer_conn = ModelConnection.objects.create(
            name="answer conn", engine="ollama", endpoint="http://fake-inference:1",
            model_id="llama3.1:8b", capabilities=["chat"],
        )
        embed_conn = ModelConnection.objects.create(
            name="embed conn", engine="ollama", endpoint="http://fake-inference:1",
            model_id="nomic-embed-text", capabilities=["embeddings"], embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_ANSWER_ROLE, connection=answer_conn)
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        job_id = enqueue("rag.ask", {"question": "hello?", "category": None})

        with django_capture_on_commit_callbacks(execute=True):
            outcome = cancel_job(job_id)

        assert outcome == "cancelled"
        assert Document.objects.count() == 0


# --- end-to-end: enqueue -> claim -> worker tick -> succeeded ------------


@pytest.mark.django_db(transaction=True)
class TestEndToEndIngestViaWorker:
    """Mirrors `TestEndToEndViaWorker` (rag.ask) for `rag.ingest`: stage a
    real file, enqueue the real, globally-registered `rag.ingest` kind
    through `models.contracts.queue`, run one worker tick, and check the
    Document reaches READY and the job row reaches SUCCEEDED.

    Unlike `rag.ask`'s end-to-end test, no fake engine registration is
    needed: neither `plan_ingest` nor `run_ingest`/`run_ingest_for` ever
    health-checks a live endpoint (`plan_ingest` only resolves the DB
    binding; ingest has no pre-run re-check the way `rag.ask` does) -- the
    only mocked seams are the same HTTP/index boundary this module's other
    prose-ingest tests mock (`tools.rag.ingest.rag_index`/`gateway`).

    `transaction=True`: see `TestEndToEndViaWorker`'s own docstring -- the
    job runs on a separate pool thread with its own DB connection/session.
    """

    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_enqueued_prose_ingest_succeeds_via_one_worker_tick(
        self, mock_gateway, mock_rag_index, tmp_path, settings, monkeypatch
    ):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.models import SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue
        from tools.rag import ingest as ingest_module

        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        # IA-2 T10 review finding 1: the `restamp_document_chunks(doc.id)`
        # call in `_ingest_prose`, immediately after `insert_nodes`, was
        # UNPINNED -- deleting those two lines left this suite green,
        # while silent unlabelling on re-ingest is that task's core
        # failure mode. A spy on the function-local import target proves
        # the call site is really there: `_ingest_prose`'s `from
        # tools.rag.labels import restamp_document_chunks` resolves at
        # CALL time (the job runs on the worker's own pool thread below),
        # so patching the module attribute here is enough to intercept it.
        restamp_spy = MagicMock()
        monkeypatch.setattr("tools.rag.labels.restamp_document_chunks", restamp_spy)

        embed_conn = ModelConnection.objects.create(
            name="workstation embed",
            engine="ollama",
            endpoint="http://fake-inference:1",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        path = tmp_path / "notes.md"
        path.write_text("some prose content to ingest")
        doc, changed = ingest_module.stage_document(str(path), move=False)
        assert changed is True

        job_id = enqueue(
            "rag.ingest",
            {"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
        )

        worker = Worker(worker_id="test-worker")
        try:
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        finally:
            worker._executor.shutdown(wait=True)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == SUCCEEDED
        assert job.result["document_id"] == doc.id
        assert job.finished_at is not None

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        mock_index.insert_nodes.assert_called_once()
        restamp_spy.assert_called_once_with(doc.id)


class _FakeTranscribingEngine:
    """Minimal `InferenceEngine` stub with `build_transcriber` -- the
    transcription-capable sibling of `_FakeInferenceEngine` above, used by
    `TestEndToEndIngestAVViaWorker` so the pre-run health check and
    transcriber construction touch no real network/whisper-server."""

    name = "test-whisper"
    well_known_ports: tuple[int, ...] = ()

    def is_healthy(self, endpoint, timeout=None):
        return True

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused
        raise NotImplementedError

    def build_transcriber(self, model_id, endpoint, **cfg):
        return _FakeTranscriberForEndToEnd()


class _FakeTranscriberForEndToEnd:
    def transcribe(self, audio_path, *, language=None):
        from models.contracts.engines.base import TranscriptResult, TranscriptSegment

        return TranscriptResult(
            segments=(TranscriptSegment(start=0.0, end=1.0, text="hello from the video"),),
            language="en",
        )


@pytest.mark.django_db(transaction=True)
class TestEndToEndIngestAVViaWorker:
    """The video/audio sibling of `TestEndToEndIngestViaWorker`: stage a
    real (fake-bytes) video file with "media" enabled, enqueue the real
    `rag.ingest` kind, run one worker tick, and check the Document reaches
    READY with a real transcription snapshot -- transcode's own subprocess
    calls (`extract_audio`/`slice_audio`/`probe_duration`) are mocked at the
    bound name (`tools.rag.ingest.transcode` for the stage-time duration
    check, `tools.rag.media.transcode` for the transcription driver
    itself), matching this module's "mocked HTTP inference" convention --
    only the ENGINE boundary (whisper) and the SUBPROCESS boundary
    (ffmpeg/ffprobe) are doubled, nothing in between.
    """

    @pytest.fixture(autouse=True)
    def _register_fake_transcribing_engine(self):
        from models.contracts.engines import ENGINES, register

        register(_FakeTranscribingEngine())
        yield
        ENGINES.pop("test-whisper", None)

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.ingest.transcode")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_enqueued_av_ingest_succeeds_via_one_worker_tick(
        self, mock_gateway, mock_rag_index, mock_stage_transcode, mock_media_transcode, tmp_path, settings
    ):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.models import SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue
        from tools.rag import ingest as ingest_module

        settings.FARABUNKER_FEATURES = frozenset({"media"})
        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        # Stage-time duration probe (tools.rag.ingest._check_media_duration).
        mock_stage_transcode.probe_duration.return_value = 30.0

        # The transcription driver's own transcode seams.
        mock_media_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_media_transcode.slice_audio.return_value = [Path("/fake/slice-00000.wav")]
        mock_media_transcode.probe_duration.return_value = 30.0

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        transcribe_conn = ModelConnection.objects.create(
            name="workstation whisper",
            engine="test-whisper",
            endpoint="http://fake-whisper:1",
            model_id="ggml-base.en",
            capabilities=["transcription"],
        )
        embed_conn = ModelConnection.objects.create(
            name="workstation embed",
            engine="test-whisper",
            endpoint="http://fake-whisper:1",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_TRANSCRIBE_ROLE, connection=transcribe_conn)
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        doc, changed = ingest_module.stage_document(str(path), move=False)
        assert changed is True
        assert doc.doc_type == Document.DocType.PROSE

        job_id = enqueue(
            "rag.ingest",
            {"document_id": doc.id, "sha256": doc.file_hash, "medium": "video", "title": doc.title},
        )

        worker = Worker(worker_id="test-worker")
        try:
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        finally:
            worker._executor.shutdown(wait=True)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == SUCCEEDED

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert doc.duration_seconds == 30.0
        assert doc.extraction["method"] == "transcription"
        assert doc.extraction["model_id"] == "ggml-base.en"
        mock_index.insert_nodes.assert_called_once()

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) == 1
        assert "hello from the video" in nodes[0].get_content()


@pytest.mark.django_db(transaction=True)
class TestReingestOnFlagOff:
    """T7 review m3: an already-staged video/audio Document keeps working
    through a reingest even after "media" is disabled -- the flag gates
    NEW staging only (`tools.rag.ingest._doc_type_for_medium`/
    `supported_exts()`), never an already-staged Document's own re-ingest.
    """

    @pytest.fixture(autouse=True)
    def _register_fake_transcribing_engine(self):
        from models.contracts.engines import ENGINES, register

        register(_FakeTranscribingEngine())
        yield
        ENGINES.pop("test-whisper", None)

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.ingest.transcode")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_reingest_after_media_is_disabled_still_succeeds(
        self, mock_gateway, mock_rag_index, mock_stage_transcode, mock_media_transcode, tmp_path, settings
    ):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.models import SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue
        from tools.rag import ingest as ingest_module

        settings.FARABUNKER_FEATURES = frozenset({"media"})
        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        mock_stage_transcode.probe_duration.return_value = 30.0
        mock_media_transcode.DEFAULT_SLICE_SECONDS = 300
        mock_media_transcode.slice_audio.return_value = [Path("/fake/slice-00000.wav")]
        mock_media_transcode.probe_duration.return_value = 30.0

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        transcribe_conn = ModelConnection.objects.create(
            name="workstation whisper",
            engine="test-whisper",
            endpoint="http://fake-whisper:1",
            model_id="ggml-base.en",
            capabilities=["transcription"],
        )
        embed_conn = ModelConnection.objects.create(
            name="workstation embed",
            engine="test-whisper",
            endpoint="http://fake-whisper:1",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_TRANSCRIBE_ROLE, connection=transcribe_conn)
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        # Stage WHILE the flag is on -- this is the one step the flag is
        # allowed to gate.
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"fake video bytes")
        doc, changed = ingest_module.stage_document(str(path), move=False)
        assert changed is True

        # Now disable "media" -- everything from here on is an
        # already-staged Document's own reingest, which must NOT be
        # affected.
        settings.FARABUNKER_FEATURES = frozenset()

        job_id = enqueue(
            "rag.ingest",
            {"document_id": doc.id, "sha256": doc.file_hash, "medium": "video", "title": doc.title},
        )

        worker = Worker(worker_id="test-worker")
        try:
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        finally:
            worker._executor.shutdown(wait=True)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == SUCCEEDED

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        mock_index.insert_nodes.assert_called_once()


class _FakeVisionLLM:
    """A LlamaIndex LLM stand-in with a real `.chat()` -- returns a fixed
    `ChatResponse` regardless of what messages it was given, so `modules.
    rag.extract.extract_image_text`'s own real (unmocked) call reaches a
    real-shaped response object without ever hitting a network."""

    def chat(self, messages):
        from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole

        return ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, content="hello from the image"))


class _FakeVisionEngine:
    """Minimal `InferenceEngine` stub with a real `build_llm` -- the
    vision-extraction sibling of `_FakeTranscribingEngine` above, used by
    the T8 end-to-end tests below so the pre-run health check and LLM
    construction touch no real network/vision-server."""

    name = "test-vision"
    well_known_ports: tuple[int, ...] = ()

    def is_healthy(self, endpoint, timeout=None):
        return True

    def build_llm(self, model_id, endpoint, **cfg):
        return _FakeVisionLLM()

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused
        raise NotImplementedError


@pytest.mark.django_db(transaction=True)
class TestEndToEndIngestImageViaWorker:
    """The image sibling of `TestEndToEndIngestAVViaWorker` (T8): stage a
    real (fake-bytes) image with "media" enabled, enqueue the real
    `rag.ingest` kind, run one worker tick, and check the Document reaches
    READY with a real extraction snapshot and page-keyed chunks.
    `tools.rag.media.transcode.normalize_image` is mocked (the same
    "mocked SUBPROCESS/library boundary, real ENGINE boundary" convention
    the AV test uses) -- Pillow would otherwise choke on the fake JPEG
    bytes this test writes; `tools.rag.extract.extract_image_text` itself
    is NOT mocked, so the real `ChatMessage`/`ImageBlock` construction and
    the registered fake vision engine's own `.chat()` both run for real.
    """

    @pytest.fixture(autouse=True)
    def _register_fake_vision_engine(self):
        from models.contracts.engines import ENGINES, register

        register(_FakeVisionEngine())
        yield
        ENGINES.pop("test-vision", None)

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_enqueued_image_ingest_succeeds_via_one_worker_tick(
        self, mock_gateway, mock_rag_index, mock_media_transcode, tmp_path, settings
    ):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.models import SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue
        from tools.rag import ingest as ingest_module

        settings.FARABUNKER_FEATURES = frozenset({"media"})
        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        mock_media_transcode.normalize_image.return_value = b"normalized-png-bytes"

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        extract_conn = ModelConnection.objects.create(
            name="workstation vision",
            engine="test-vision",
            endpoint="http://fake-vision:1",
            model_id="llava",
            capabilities=["vision"],
        )
        embed_conn = ModelConnection.objects.create(
            name="workstation embed",
            engine="test-vision",
            endpoint="http://fake-vision:1",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_EXTRACT_ROLE, connection=extract_conn)
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        path = tmp_path / "photo.jpg"
        path.write_bytes(b"fake jpeg bytes")
        doc, changed = ingest_module.stage_document(str(path), move=False)
        assert changed is True
        assert doc.doc_type == Document.DocType.PROSE

        job_id = enqueue(
            "rag.ingest",
            {"document_id": doc.id, "sha256": doc.file_hash, "medium": "image", "title": doc.title},
        )

        worker = Worker(worker_id="test-worker")
        try:
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        finally:
            worker._executor.shutdown(wait=True)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == SUCCEEDED

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert doc.extraction["method"] == "extraction"
        assert doc.extraction["model_id"] == "llava"
        mock_index.insert_nodes.assert_called_once()

        # RE-PINNED (preview UAT, 2026-09-17): the single-image branch now
        # calls the vision LLM TWICE -- `describe_image` then
        # `extract_image_text` -- and this fixture's `_FakeVisionLLM`
        # returns the SAME fixed string for either prompt, so the sidecar
        # now carries a description segment ahead of the transcription
        # segment (`tools.rag.access._caption_from_sidecar` reads
        # description-first, under a cap) and BOTH get embedded --
        # `documents_from_extract` branches on `"page" in segments[0]`
        # and embeds every page-keyed segment, description included, so
        # `rag__search` can find a photo by what is in it. One node is no
        # longer the right count; asserting ONLY the count would have let
        # this test keep passing for a document that quietly stopped
        # being described at all.
        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) == 2
        assert "hello from the image" in nodes[0].get_content()
        assert nodes[0].metadata["page"] == 1
        assert "hello from the image" in nodes[1].get_content()
        assert nodes[1].metadata["page"] == 1


@pytest.mark.django_db(transaction=True)
class TestEndToEndIngestScannedPdfViaWorker:
    """The scanned-PDF sibling: a `.pdf` with NO extractable text layer on
    ANY page (`make_pdf_bytes([None])`, the T1 builder) auto-detects
    (`tools.rag.ingest._needs_vision_extraction`) and routes through vision
    extraction exactly like an image does -- proving the auto-detect end to
    end, not just at the unit level. `tools.rag.media.transcode.
    rasterize_pdf_page` is mocked (rendering the hand-assembled minimal PDF
    for real would be exercising `pypdfium2`'s own rendering, not this
    pipeline's routing); `tools.rag.readers.pdf_textless_pages` is NOT
    mocked -- it runs for real against the real fixture file, which is the
    whole point of this test.

    RETARGETED (H28, round-3 hardening, B-5; corrected round 1 finding 2):
    the auto-detect used to run at STAGE/ENQUEUE time, with
    `payload["medium"]` carrying the literal string `"pdf-scanned"` (W1:
    the same token a merely MIXED PDF gets too, decision D1) as the
    payload-level proof. `_enqueue_ingest_job` no longer inspects a
    `.pdf`'s content at all -- H28's first cut made `payload["medium"]`
    plain `"prose"` for every `.pdf`; review round 1 found that
    under-reserved `rag.extract` for every genuinely-scanned PDF, so
    `_enqueue_ingest_job` now declares `"pdf-scanned"` for every `.pdf`
    PESSIMISTICALLY instead, off the extension alone, while "media" is on
    -- so the payload-level assertion below still reads `"pdf-scanned"`,
    same as before H28, but for a DIFFERENT reason (blanket reservation,
    not a per-file scan result). The REAL, content-based auto-detect now
    runs fresh at JOB START (`run_ingest_for`), and this test's own
    worker-tick/READY/`extraction["method"] == "extraction"` assertions
    below are its proof: they only pass if that run-time detection
    correctly routed this scanned PDF through vision extraction,
    regardless of what the payload says.
    """

    @pytest.fixture(autouse=True)
    def _register_fake_vision_engine(self):
        from models.contracts.engines import ENGINES, register

        register(_FakeVisionEngine())
        yield
        ENGINES.pop("test-vision", None)

    @patch("tools.rag.media.transcode")
    @patch("tools.rag.ingest.rag_index")
    @patch("tools.rag.ingest.gateway")
    def test_enqueued_scanned_pdf_ingest_succeeds_via_one_worker_tick(
        self, mock_gateway, mock_rag_index, mock_media_transcode, tmp_path, settings
    ):
        from models.registry.models import ModelConnection, RoleBinding
        from models.queue.models import SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue
        from tools.rag import ingest as ingest_module

        settings.FARABUNKER_FEATURES = frozenset({"media"})
        settings.DOCUMENTS_DIR = tmp_path / "_managed_store"
        settings.DOCUMENTS_DIR.mkdir()

        mock_media_transcode.rasterize_pdf_page.return_value = b"page-png-bytes"

        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        extract_conn = ModelConnection.objects.create(
            name="workstation vision",
            engine="test-vision",
            endpoint="http://fake-vision:1",
            model_id="llava",
            capabilities=["vision"],
        )
        embed_conn = ModelConnection.objects.create(
            name="workstation embed",
            engine="test-vision",
            endpoint="http://fake-vision:1",
            model_id="nomic-embed-text",
            capabilities=["embeddings"],
            embed_dim=768,
        )
        RoleBinding.objects.create(role_key=RAG_EXTRACT_ROLE, connection=extract_conn)
        RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)

        path = tmp_path / "scan.pdf"
        path.write_bytes(make_pdf_bytes([None]))  # no text layer -- the auto-detect
        doc, changed = ingest_module.stage_document(str(path), move=False)
        assert changed is True
        assert doc.doc_type == Document.DocType.PROSE
        assert doc.media_type == "application/pdf"

        # H28 (round-3 hardening, B-5), corrected round 1 finding 2: the
        # payload still carries "pdf-scanned" for THIS file -- but no
        # longer because a content scan found it to be scanned. Every
        # `.pdf` gets this token pessimistically now, while "media" is on,
        # off the extension alone (the class docstring above has the full
        # story) -- `plan_ingest` reserves `rag.extract` for every `.pdf`
        # sight-unseen. The worker tick below is what proves the REAL,
        # content-based auto-detect still runs correctly, at job start,
        # regardless of what this payload says.
        with patch("tools.rag.ingest.enqueue", wraps=enqueue) as spy_enqueue:
            job_id = ingest_module._enqueue_ingest_job(doc, SERVICE_PRINCIPAL)
        (_, payload), _ = spy_enqueue.call_args
        assert payload["medium"] == "pdf-scanned"

        worker = Worker(worker_id="test-worker")
        try:
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        finally:
            worker._executor.shutdown(wait=True)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == SUCCEEDED

        doc.refresh_from_db()
        assert doc.status == Document.Status.READY
        assert doc.extraction["method"] == "extraction"
        assert doc.extraction["model_id"] == "llava"
        mock_index.insert_nodes.assert_called_once()

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) == 1
        assert "hello from the image" in nodes[0].get_content()
        assert nodes[0].metadata["page"] == 1


def test_the_ingest_planner_does_not_claim_an_engine_lacks_methods_it_has():
    """C-54. `plan_ingest`'s comment asserted that an image-generation
    adapter implements neither `loaded_footprint` nor `unload`. Both have
    existed for some time. A comment that describes a limitation the code
    fixed is worse than no comment: it is read as current."""
    text = (Path(settings.BASE_DIR) / "tools/rag/jobs.py").read_text()
    assert "implements neither" not in text
    assert "NO `loaded_footprint`" not in text
