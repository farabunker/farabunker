"""Unit tests for the job-kind registry (models/contracts/jobkinds.py).

Pure in-memory registry -- no DB, no Django models. The module-level
`_JOB_KINDS` dict is reset between tests with an autouse fixture built by
`models.contracts.testing.registry_reset_fixture` (C-59) -- the same
snapshot/restore factory `models/registry/tests/test_roles.py`'s
`reset_registry` (over `roles._ROLES`) and `models/queue/tests/
test_backend.py`/`test_worker.py` (over this same `jobkinds._JOB_KINDS`)
all build their own `reset_registry` from, so tests don't leak state into
each other or into whatever a feature app registers at import time.
"""
from __future__ import annotations

import pytest

from models.contracts import jobkinds
from models.contracts.jobkinds import (
    JobContext,
    JobKind,
    ModelRef,
    all_job_kinds,
    get_job_kind,
    register_job_kind,
    resolve_dotted_path,
)
from models.registry.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported

reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")


def _kind(key="rag.ask", **overrides):
    fields = dict(
        key=key,
        label="RAG Ask",
        planner="tools.rag.jobs.plan_ask",
        handler="tools.rag.jobs.handle_ask",
        summarizer="tools.rag.jobs.summarize_ask",
    )
    fields.update(overrides)
    return JobKind(**fields)


# --- ModelRef / JobKind construction + frozenness ------------------------


class TestModelRef:
    def test_construction_with_defaults(self):
        ref = ModelRef(
            role="rag.answer",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
        )

        assert ref.role == "rag.answer"
        assert ref.engine == "ollama"
        assert ref.endpoint == "http://ollama.local:11434"
        assert ref.model_id == "llama3.1:8b"
        assert ref.connection_name == ""
        assert ref.footprint_bytes is None

    def test_construction_with_all_fields(self):
        ref = ModelRef(
            role="rag.answer",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b",
            connection_name="primary",
            footprint_bytes=4_000_000_000,
        )

        assert ref.connection_name == "primary"
        assert ref.footprint_bytes == 4_000_000_000

    def test_frozen(self):
        ref = ModelRef(role="rag.answer", engine="ollama", endpoint="http://e", model_id="m")

        with pytest.raises(AttributeError):
            ref.model_id = "other"


class TestJobKind:
    def test_construction_with_defaults(self):
        kind = _kind()

        assert kind.key == "rag.ask"
        assert kind.label == "RAG Ask"
        assert kind.planner == "tools.rag.jobs.plan_ask"
        assert kind.handler == "tools.rag.jobs.handle_ask"
        assert kind.summarizer == "tools.rag.jobs.summarize_ask"
        assert kind.default_priority is None
        assert kind.stale_after_seconds is None

    def test_construction_with_explicit_priority(self):
        kind = _kind(default_priority=10)

        assert kind.default_priority == 10

    def test_construction_with_explicit_stale_after_seconds(self):
        kind = _kind(stale_after_seconds=3600)

        assert kind.stale_after_seconds == 3600

    def test_frozen(self):
        kind = _kind()

        with pytest.raises(AttributeError):
            kind.label = "Other"


class TestJobContext:
    """`JobContext` (T3) -- DB-free, worker-injected `_report`/
    `_checkpoint` closures stand in for the real token-conditional writers
    `models/queue/worker.py::Worker._build_job_context` builds in
    production (that method's own tests, `models/queue/tests/
    test_worker.py`, prove the real writers; these prove the class's own
    contract in isolation)."""

    def _ctx(self, **overrides) -> tuple[JobContext, list[dict], list[dict]]:
        reports: list[dict] = []
        checkpoints: list[dict] = []
        fields = dict(
            job_id=7,
            attempt=0,
            checkpoint_state=None,
            _report=reports.append,
            _checkpoint=checkpoints.append,
        )
        fields.update(overrides)
        ctx = JobContext(**fields)
        return ctx, reports, checkpoints

    def test_report_progress_builds_the_stored_dict_shape(self):
        ctx, reports, _ = self._ctx()

        ctx.report_progress(3, 10, unit="pages", label="chunks")

        assert reports == [{"done": 3, "total": 10, "unit": "pages", "label": "chunks"}]

    def test_report_progress_total_none_is_expressible(self):
        """A job that genuinely doesn't know its own total yet must be
        able to say so honestly -- `total=None`, never a guessed number
        (see `InferenceJob.progress`'s own field comment)."""
        ctx, reports, _ = self._ctx()

        ctx.report_progress(3, unit="items")

        assert reports == [{"done": 3, "total": None, "unit": "items", "label": ""}]

    def test_checkpoint_calls_the_injected_writer_with_the_raw_state(self):
        ctx, _, checkpoints = self._ctx()

        ctx.checkpoint({"offset": 42})

        assert checkpoints == [{"offset": 42}]

    def test_frozen(self):
        ctx, _, _ = self._ctx()

        with pytest.raises(AttributeError):
            ctx.job_id = 999

    def test_checkpoint_state_carries_the_prior_attempt_value(self):
        ctx, _, _ = self._ctx(checkpoint_state={"offset": 5})

        assert ctx.checkpoint_state == {"offset": 5}

    def test_attempt_and_job_id_are_plain_read_only_facts(self):
        ctx, _, _ = self._ctx(job_id=42, attempt=2)

        assert ctx.job_id == 42
        assert ctx.attempt == 2

    def test_response_timeout_seconds_defaults_to_none(self):
        """One-timeout task (2026-09-17): optional, defaulting to `None`
        so NULL_JOB_CONTEXT and every existing constructor (this file's
        own `_ctx` included) stay valid without naming the new field."""
        ctx, _, _ = self._ctx()

        assert ctx.response_timeout_seconds is None

    def test_response_timeout_seconds_carries_the_stamped_value(self):
        ctx, _, _ = self._ctx(response_timeout_seconds=123.0)

        assert ctx.response_timeout_seconds == 123.0

    def test_null_job_context_carries_no_response_timeout(self):
        """`NULL_JOB_CONTEXT` (the no-op object for a caller with no real
        queue behind it) predates this field -- it must still construct,
        and honestly reports `None`, never a guessed number."""
        from models.contracts.jobkinds import NULL_JOB_CONTEXT

        assert NULL_JOB_CONTEXT.response_timeout_seconds is None


# --- register/get/all -----------------------------------------------------


class TestRegisterJobKind:
    def test_register_two_kinds_are_both_retrievable(self):
        ask = _kind(key="rag.ask")
        reencode = _kind(key="rag.reencode", planner="tools.rag.jobs.plan_reencode")

        register_job_kind(ask)
        register_job_kind(reencode)

        assert get_job_kind("rag.ask") is ask
        assert get_job_kind("rag.reencode") is reencode

    def test_reregister_same_key_is_idempotent_last_wins(self):
        original = _kind(label="Original")
        updated = _kind(label="Updated")

        register_job_kind(original)
        register_job_kind(updated)

        assert all_job_kinds() == [updated]
        assert get_job_kind("rag.ask") is updated

    def test_all_job_kinds_ordering_is_stable_across_reregistration(self):
        first = _kind(key="rag.ask")
        second = _kind(key="rag.reencode")
        first_updated = _kind(key="rag.ask", label="Ask v2")

        register_job_kind(first)
        register_job_kind(second)
        register_job_kind(first_updated)  # re-registering rag.ask must not move it to the end

        assert all_job_kinds() == [first_updated, second]


class TestGetJobKind:
    def test_unknown_key_raises_value_error_naming_it(self):
        with pytest.raises(ValueError, match="does.not.exist"):
            get_job_kind("does.not.exist")


# --- dotted-path resolution helper -----------------------------------------


class TestResolveDottedPath:
    def test_resolves_a_real_function(self):
        resolved = resolve_dotted_path("models.contracts.jobkinds.get_job_kind")

        assert resolved is get_job_kind

    def test_bad_path_raises_informatively(self):
        with pytest.raises(ImportError):
            resolve_dotted_path("does.not.exist.at_all")
