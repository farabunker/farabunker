"""Unit tests for models/registry/jobs.py -- the `rag.reencode` job kind
(T8): the SECOND job kind registered against the execution queue (after
`rag.ask`), proving the kind registry generalizes beyond the RAG module --
this one is registered from `models.registry.apps`, not `tools/rag`.

Three levels, matching `tools/rag/tests/test_jobs.py`'s own shape:
`plan_reencode`/`run_reencode`/`summarize_reencode` are unit-tested against
mocked seams (`resolve`/`run_rematerialize` -- the same "mock at the
module boundary" convention every other test file in this app uses), a
`TestRegistration` class checks the kind is actually registered (via the
REAL, globally-registered kind -- `models.registry.apps.InferenceConfig.
ready()` already ran at Django startup, no re-registration needed here),
and two end-to-end classes prove the queue-level story for real: enqueue
through `models.contracts.queue`, run the real scheduler/claim/worker, and
check what actually ran and in what order.

`@pytest.mark.django_db` at class level (repo convention); no
conftest.py.
"""
from __future__ import annotations

import concurrent.futures
from unittest.mock import patch

import pytest

from models.registry import jobs
from models.registry.tests._helpers import make_job_ctx
from models.contracts.bindings import ResolvedModel
from models.contracts.jobkinds import get_job_kind
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE

GB = 1024**3

RESOLVED = ResolvedModel("ollama", "nomic-embed-text", "http://localhost:11434", embed_dim=768)


# --- plan_reencode -----------------------------------------------------


class TestPlanReencode:
    @patch("models.registry.jobs.resolve", return_value=RESOLVED)
    def test_resolves_named_role_and_declares_exclusive(self, mock_resolve):
        model_refs, exclusive = jobs.plan_reencode({"role_key": "rag.embed"})

        # Exclusive=True is the whole point of this kind -- see the module
        # docstring: a re-encode drops/rebuilds the chunk table asks read,
        # so it must own the machine.
        assert exclusive is True
        assert len(model_refs) == 1
        ref = model_refs[0]
        assert ref.role == "rag.embed"
        assert ref.engine == "ollama"
        assert ref.endpoint == "http://localhost:11434"
        assert ref.model_id == "nomic-embed-text"
        # Provenance contract (models/queue/scheduler.py): the planner
        # never resolves footprints -- claim-time code fills that in fresh.
        assert ref.footprint_bytes is None
        mock_resolve.assert_called_once_with("rag.embed")

    @patch("models.registry.jobs.resolve", return_value=RESOLVED)
    def test_generic_over_role_key_not_hard_coded_to_rag_embed(self, mock_resolve):
        """The payload's `role_key` names whatever role is being
        re-encoded -- this kind is not hard-coded to `rag.embed`, even
        though that's its only caller today (future-proofing already
        present in `run_rematerialize`, which this planner rides)."""
        model_refs, exclusive = jobs.plan_reencode({"role_key": "some.other.role"})

        assert model_refs[0].role == "some.other.role"
        assert exclusive is True
        mock_resolve.assert_called_once_with("some.other.role")

    @patch(
        "models.registry.jobs.resolve",
        side_effect=ValueError("No inference binding resolved for role 'rag.embed'"),
    )
    def test_unbound_role_raises(self, mock_resolve):
        with pytest.raises(ValueError, match="rag.embed"):
            jobs.plan_reencode({"role_key": "rag.embed"})


# --- run_reencode --------------------------------------------------------


class TestRunReencode:
    @patch("models.registry.jobs.run_rematerialize")
    def test_happy_path_returns_summary_via_format_tally(self, mock_rematerialize):
        mock_rematerialize.return_value = {"documents": 5, "reencoded": 5}

        result = jobs.run_reencode({"role_key": "rag.embed"}, [], make_job_ctx())

        mock_rematerialize.assert_called_once_with("rag.embed")
        # The exact tally shape `models.registry.drift.format_tally`
        # produces for a dict result -- the same rendering the old
        # synchronous view used to fold into its green message.
        assert result == {"summary": "documents=5, reencoded=5"}

    @patch(
        "models.registry.jobs.run_rematerialize",
        side_effect=RuntimeError("reencode_all: only 1 of 3 prose documents re-encoded"),
    )
    def test_runtime_error_propagates_with_its_original_copy(self, mock_rematerialize):
        """A partial-reencode RuntimeError must reach the job row's `error`
        column with the SAME operator-facing copy the old synchronous
        view used to show as a red message -- `models.queue.worker.
        Worker._execute` stores `str(exc)` verbatim, so the handler must
        not swallow or reword it."""
        with pytest.raises(RuntimeError, match="only 1 of 3 prose documents"):
            jobs.run_reencode({"role_key": "rag.embed"}, [], make_job_ctx())

    @patch(
        "models.registry.jobs.run_rematerialize",
        side_effect=ValueError("Role 'rag.answer' has no rematerialize callback configured"),
    )
    def test_value_error_propagates_with_its_original_copy(self, mock_rematerialize):
        with pytest.raises(ValueError, match="no rematerialize callback"):
            jobs.run_reencode({"role_key": "rag.answer"}, [], make_job_ctx())


# --- summarize_reencode --------------------------------------------------


class TestSummarizeReencode:
    def test_names_the_role_key(self):
        assert jobs.summarize_reencode({"role_key": "rag.embed"}) == "Re-encode rag.embed"

    def test_missing_role_key_still_renders(self):
        assert jobs.summarize_reencode({}) == "Re-encode "


# --- registration ----------------------------------------------------------


class TestRegistration:
    def test_rag_reencode_is_registered_with_the_expected_shape(self):
        """`models.registry.apps.InferenceConfig.ready()` already ran at
        Django startup -- this checks the REAL registry, not a
        re-registration, proving the kind is actually wired up in
        production, not only reachable if some test file happens to
        register it."""
        kind = get_job_kind("rag.reencode")

        assert kind.label == "Re-encode embeddings"
        assert kind.planner == "models.registry.jobs.plan_reencode"
        assert kind.handler == "models.registry.jobs.run_reencode"
        assert kind.summarizer == "models.registry.jobs.summarize_reencode"
        # Background maintenance work queues behind interactive asks
        # (owner's per-kind priority ruling) -- see the priority-proof
        # test below for the behavioral consequence of this number.
        assert kind.default_priority == 200


# --- shared fixtures for the end-to-end tests below -------------------------


class _FakeInferenceEngine:
    """Minimal `InferenceEngine` stub, registered under a fake engine name
    so `rag.ask`'s pre-run health-check re-check touches no real network --
    the same convention `tools/rag/tests/test_jobs.py`'s own
    `_FakeInferenceEngine` uses. No `loaded_footprint`/`unload`/
    `list_installed` -- the worker's eviction/measurement code reads those
    via `getattr(..., None)` and degrades to a no-op when absent, exactly
    like `models/queue/tests/test_worker.py`'s bare-bones fakes do."""

    name = "test-inference"
    well_known_ports: tuple[int, ...] = ()

    def is_healthy(self, endpoint, timeout=None):
        return True

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused, mocked out
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused
        raise NotImplementedError


@pytest.fixture
def _register_fake_engine():
    from models.contracts.engines import ENGINES, register

    register(_FakeInferenceEngine())
    yield
    ENGINES.pop("test-inference", None)


def _bind_both_rag_roles(*, footprint_bytes=1 * GB):
    """Register + bind chat/embeddings connections for BOTH `rag.answer`
    and `rag.embed`, each carrying a KNOWN `footprint_override_bytes` --
    so neither job's models are "unmeasured" (`models/queue/scheduler.py`
    rule 2b) and any exclusivity this test observes is attributable ONLY
    to `rag.reencode`'s own `exclusive=True` declaration (rule 2a), not to
    an unmeasured-model side effect."""
    from models.registry.models import ModelConnection, RoleBinding

    answer_conn = ModelConnection.objects.create(
        name="workstation llama",
        engine="test-inference",
        endpoint="http://fake-inference:1",
        model_id="llama3.1:8b",
        capabilities=["chat"],
        footprint_override_bytes=footprint_bytes,
    )
    embed_conn = ModelConnection.objects.create(
        name="workstation embed",
        engine="test-inference",
        endpoint="http://fake-inference:1",
        model_id="nomic-embed-text",
        capabilities=["embeddings"],
        embed_dim=768,
        footprint_override_bytes=footprint_bytes,
    )
    RoleBinding.objects.create(role_key=RAG_ANSWER_ROLE, connection=answer_conn)
    RoleBinding.objects.create(role_key=RAG_EMBED_ROLE, connection=embed_conn)


# --- end-to-end: cross-kind exclusivity (the T8 kind-registry proof) -------


@pytest.mark.django_db(transaction=True)
class TestCrossKindExclusivityViaWorker:
    """The cross-kind proof: enqueue a `rag.reencode` job AND a `rag.ask`
    job, with a real memory budget configured (so any exclusivity
    observed is `rag.reencode`'s own declared `exclusive=True`, never an
    incidental sequential-mode/unmeasured-model artifact), then run the
    REAL scheduler/claim/worker (`models.queue.claim.claim_and_admit`,
    `models.queue.worker.Worker`) -- only `models.registry.jobs.
    run_rematerialize` and `tools.rag.jobs.answer_question` are mocked
    (the "mock the actual work + HTTP" seam), matching `tools/rag/
    tests/test_jobs.py`'s own `TestEndToEndViaWorker` convention.

    `rag.reencode` is enqueued with an explicit `priority=1` (lower than
    its own `default_priority=200`) so it is admitted FIRST despite
    `rag.ask` using its own default (100) -- isolating the property this
    test is actually about (declared exclusivity blocks a fitting,
    concurrent candidate) from the priority-ordering property the
    separate `TestPriorityOrdering` class below proves; mixing the two
    into one test would leave it ambiguous which rule produced the
    observed blocking.

    `transaction=True`: jobs run on separate pool threads with their own
    DB connections -- see `tools/rag/tests/test_jobs.py`'s
    `TestEndToEndViaWorker` docstring for why plain `@pytest.mark.
    django_db`'s implicit per-test transaction would make these rows
    invisible to those other sessions entirely.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, _register_fake_engine):
        from models.queue.models import JobSettings

        _bind_both_rag_roles(footprint_bytes=1 * GB)
        settings = JobSettings.get_solo()
        # A budget with plenty of room for both 1 GB models at once --
        # if `rag.reencode` were NOT declared exclusive, `rag.ask` would
        # fit comfortably alongside it. Proves the blocking below is the
        # exclusivity rule, not budget exhaustion.
        settings.memory_budget_bytes = 10 * GB
        settings.max_concurrent_jobs = 4
        settings.save()

    @patch("tools.rag.jobs.answer_question")
    @patch("models.registry.jobs.run_rematerialize")
    def test_reencode_runs_alone_then_ask_runs_after(
        self, mock_rematerialize, mock_answer_question
    ):
        from models.queue.models import QUEUED, RUNNING, SUCCEEDED, InferenceJob
        from models.queue.worker import Worker
        from models.contracts.queue import enqueue

        mock_rematerialize.return_value = {"documents": 3, "reencoded": 3}
        mock_answer_question.return_value = {"answer": "The sky is blue.", "citations": []}

        reencode_id = enqueue("rag.reencode", {"role_key": "rag.embed"}, priority=1)
        ask_id = enqueue("rag.ask", {"question": "Why is the sky blue?"})

        worker = Worker(worker_id="test-worker")
        try:
            # Tick 1: only the exclusive re-encode is admitted -- the ask
            # job, though it would fit the budget on paper, waits (rule 5:
            # an effectively-exclusive candidate is admissible only into
            # an idle machine, and is the only thing admitted that round).
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)

            reencode_job = InferenceJob.objects.get(pk=reencode_id)
            ask_job = InferenceJob.objects.get(pk=ask_id)
            assert reencode_job.state == SUCCEEDED
            assert reencode_job.result == {"summary": "documents=3, reencoded=3"}
            assert ask_job.state == QUEUED

            # Tick 2: the machine is idle again -- the ask job is admitted
            # and runs to completion.
            worker.tick()
            concurrent.futures.wait(list(worker._futures.values()), timeout=5)

            ask_job.refresh_from_db()
            assert ask_job.state == SUCCEEDED
            assert ask_job.result["answer"] == "The sky is blue."
        finally:
            worker._executor.shutdown(wait=True)

        mock_rematerialize.assert_called_once_with("rag.embed")
        assert reencode_job.state != RUNNING  # sanity: never left half-finished


# --- priority ordering: default-priority ask beats a queued reencode ------


@pytest.mark.django_db(transaction=True)
class TestPriorityOrdering:
    """`rag.ask` registers no `default_priority` of its own (falls through
    to `JobSettings.default_priority`, 100 out of the box);
    `rag.reencode` registers `default_priority=200` (T8). Enqueuing BOTH
    at their own defaults -- `rag.reencode` FIRST, `rag.ask` after --
    proves the priority NUMBER decides claim order, not enqueue order:
    the ask (100) is admitted this round even though the reencode (200)
    has the lower job id.

    Exercises the real `models.queue.claim.claim_and_admit` directly
    (rather than a full `Worker.tick()`) -- claiming is the whole point
    of this test; nothing about actually running either job's handler
    adds anything a `patch`-heavy `Worker` run wouldn't already cost.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, _register_fake_engine):
        from models.queue.models import JobSettings

        _bind_both_rag_roles(footprint_bytes=1 * GB)
        settings = JobSettings.get_solo()
        settings.memory_budget_bytes = 10 * GB
        settings.max_concurrent_jobs = 4
        settings.save()

    def test_default_priority_ask_enqueued_after_a_reencode_still_claims_first(self):
        from models.queue.claim import claim_and_admit
        from models.queue.models import QUEUED, RUNNING, InferenceJob
        from models.contracts.queue import enqueue

        reencode_id = enqueue("rag.reencode", {"role_key": "rag.embed"})
        ask_id = enqueue("rag.ask", {"question": "Why is the sky blue?"})

        reencode_row = InferenceJob.objects.get(pk=reencode_id)
        ask_row = InferenceJob.objects.get(pk=ask_id)
        assert reencode_row.priority == 200
        assert ask_row.priority == 100

        claimed = claim_and_admit("test-worker", stale_after_seconds=600)

        claimed_ids = {descriptor["id"] for descriptor in claimed}
        assert claimed_ids == {ask_id}

        reencode_row.refresh_from_db()
        ask_row.refresh_from_db()
        assert reencode_row.state == QUEUED
        assert ask_row.state == RUNNING
