"""Unit tests for models/queue/worker.py -- the worker loop (T4).

`test.echo` (and its raising/slow siblings) are registered ONLY from this
file's own module-level functions, via the same snapshot/restore registry
idiom `models/queue/tests/test_backend.py` uses -- never from any
production `ready()`. Engines are mocked at the registry level
(`models.contracts.engines.register`/`ENGINES`), matching house convention
("mock at the HTTP layer, not by patching methods away" -- here there is
no HTTP at all, `FakeEngine` stands in for the whole adapter).

`@pytest.mark.django_db` at class level; no `conftest.py`. Every test that
builds a `Worker()` uses the `worker` fixture, which shuts its thread pool
down afterward so tests don't leak background threads.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import OperationalError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import models.queue.worker as worker_module
from models.registry.models import ModelConnection
from models.queue.claim import claim_and_admit
from models.queue.models import FAILED, QUEUED, RUNNING, SUCCEEDED, InferenceJob, JobSettings
from models.queue.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported
from models.queue.worker import Worker
from models.contracts import jobkinds
from models.contracts.engines import ENGINES, register
from models.contracts.engines.base import InstalledModel
from models.contracts.jobkinds import JobKind, register_job_kind

MODULE = "models.queue.tests.test_worker"
GB = 1024**3


# --- test job kinds' planner/handler/summarizer -----------------------------


def plan_no_models(payload):
    return ([], False)


def echo_handler(payload, models, ctx):
    return {"echo": payload}


def raising_handler(payload, models, ctx):
    raise ValueError("boom")


def summarize_noop(payload):
    return "test job"


_slow_release = threading.Event()


def slow_handler(payload, models, ctx):
    _slow_release.wait(timeout=10)
    return {"echo": payload}


_reclaim_attempt_a_release = threading.Event()
_reclaim_attempt_b_release = threading.Event()


def reclaim_collision_handler(payload, models, ctx):
    """`TestReclaimedAttemptTokenCollision`'s handler: blocks on its own
    dedicated `Event`, keyed on `ctx.attempt` (attempt A is the row's
    first claim, `ctx.attempt == 0`; attempt B is `ctx.attempt == 1` --
    `_sweep_orphans` bumps `attempts` as PART OF the requeue that makes
    this same row claimable again). TWO independent events (not one, and
    not "B returns immediately") so a test can deterministically inspect
    the worker's in-memory bookkeeping right after attempt A's `finally`
    runs while attempt B is STILL genuinely in flight -- if B returned
    immediately, whether B's own (token-matching, legitimate) cleanup had
    already run by the time an assertion executes would be a race, not a
    deterministic property of the fix under test."""
    if ctx.attempt == 0:
        _reclaim_attempt_a_release.wait(timeout=10)
        return {"echo": "attempt-a-finished-late"}
    _reclaim_attempt_b_release.wait(timeout=10)
    return {"echo": "attempt-b"}


def checkpoint_then_raise_handler(payload, models, ctx):
    """T3: reports progress, checkpoints, THEN raises -- proves a failed
    job's terminal writeback leaves both columns exactly as the handler
    last wrote them (see `Worker._execute`'s own docstring on the
    success/failure asymmetry)."""
    ctx.report_progress(1, 2, unit="items", label="things")
    ctx.checkpoint({"offset": 1})
    raise ValueError("boom-after-checkpoint")


def progress_and_checkpoint_handler(payload, models, ctx):
    """T3: reports progress and checkpoints, then succeeds -- proves the
    success writeback clears both columns."""
    ctx.report_progress(5, 10, unit="items")
    ctx.checkpoint({"offset": 5})
    return {"echo": payload}


# Module-level, cleared at the top of each test that uses it: records every
# `ctx.checkpoint_state`/`ctx.attempt` a real, worker-launched execution of
# `resume_recording_handler` observed -- proves a RESUMED attempt (one
# claimed with a non-null `checkpoint` column already on its row) receives
# the prior attempt's own saved state AND the bumped `attempts` value
# through its `JobContext`, not just that the columns round-trip through
# the DB untouched.
_resume_observations: list = []
_resume_attempt_observations: list = []


def resume_recording_handler(payload, models, ctx):
    _resume_observations.append(ctx.checkpoint_state)
    _resume_attempt_observations.append(ctx.attempt)
    prior_offset = (ctx.checkpoint_state or {}).get("offset", 0)
    ctx.checkpoint({"offset": prior_offset + 1})
    return {"echo": payload}


reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")


def _register(key, *, handler=f"{MODULE}.echo_handler"):
    register_job_kind(
        JobKind(
            key=key,
            label=key,
            planner=f"{MODULE}.plan_no_models",
            handler=handler,
            summarizer=f"{MODULE}.summarize_noop",
        )
    )


def _ref(
    *,
    engine="ollama",
    endpoint="http://ollama.local:11434",
    model_id="llama3.1:8b",
    role="test.role",
    connection_name="",
    footprint_bytes=None,
) -> dict:
    return {
        "role": role,
        "engine": engine,
        "endpoint": endpoint,
        "model_id": model_id,
        "connection_name": connection_name,
        "footprint_bytes": footprint_bytes,
    }


def _job(*, kind="test.echo", priority=100, state=QUEUED, model_refs=None, **extra) -> InferenceJob:
    return InferenceJob.objects.create(
        kind=kind, priority=priority, state=state,
        model_refs=model_refs if model_refs is not None else [], **extra,
    )


def _set_budget(*, memory_budget_bytes=None, max_concurrent_jobs=4):
    settings = JobSettings.get_solo()
    settings.memory_budget_bytes = memory_budget_bytes
    settings.max_concurrent_jobs = max_concurrent_jobs
    settings.save()


@pytest.fixture
def worker():
    w = Worker(worker_id="test-worker")
    yield w
    w._executor.shutdown(wait=True)


class FakeEngine:
    """Minimal `InferenceEngine` stub implementing the FULL optional seam
    (`loaded_footprint`/`unload`) -- registered directly into
    `models.contracts.engines.ENGINES`, restored by the `register_engine`
    fixture."""

    well_known_ports: tuple[int, ...] = ()

    def __init__(self, name: str, installed: list | None = None, footprints: dict | None = None):
        self.name = name
        self._installed = installed or []
        self._footprints = footprints or {}
        self.unload_calls: list[tuple[str, str]] = []
        self.unload_returns = True
        # Optional test hook, called with (endpoint, model_id) after every
        # unload() -- lets a test observe worker-side state (e.g. a
        # heartbeat write) that happens BETWEEN successive unload calls,
        # not just the aggregate list of calls at the end.
        self.on_unload = None

    def is_healthy(self, endpoint, timeout=None):
        return True

    def list_installed(self, endpoint):
        return self._installed

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError

    def loaded_footprint(self, endpoint, model_id):
        return self._footprints.get(model_id)

    def unload(self, endpoint, model_id):
        self.unload_calls.append((endpoint, model_id))
        if self.on_unload is not None:
            self.on_unload(endpoint, model_id)
        return self.unload_returns


class FakeEngineNoOptionalMethods:
    """Same shape, minus `loaded_footprint`/`unload` -- an adapter
    predating that optional seam, proving the worker degrades
    (`getattr(..., None)`) instead of raising."""

    well_known_ports: tuple[int, ...] = ()

    def __init__(self, name: str, installed: list | None = None):
        self.name = name
        self._installed = installed or []

    def is_healthy(self, endpoint, timeout=None):
        return True

    def list_installed(self, endpoint):
        return self._installed

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError


class FakeEngineNoListInstalled:
    """`FakeEngine` minus `list_installed` -- an adapter that cannot be
    asked what is resident. `_residency_snapshot` must warn once per
    engine+method and contribute no endpoint, so neither eviction pass
    can touch it."""

    well_known_ports: tuple[int, ...] = ()

    def __init__(self, name: str):
        self.name = name
        self.unload_calls: list[tuple[str, str]] = []

    def is_healthy(self, endpoint, timeout=None):
        return True

    def unload(self, endpoint, model_id):
        self.unload_calls.append((endpoint, model_id))
        return True

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError


class FakeEngineListInstalledRaises(FakeEngineNoListInstalled):
    """`list_installed` blows up. Eviction must never block a launch, so
    the endpoint is skipped, the failure is logged, and every OTHER
    endpoint is still probed and still evicted from."""

    def list_installed(self, endpoint):
        raise RuntimeError("engine exploded")


@pytest.fixture
def register_engine():
    added: list[str] = []

    def _do(engine):
        register(engine)
        added.append(engine.name)
        return engine

    yield _do
    for name in added:
        ENGINES.pop(name, None)


# --- job execution -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestJobExecution:
    """`transaction=True`: the handler actually runs on a separate pool
    thread with its OWN DB connection/session -- plain `@pytest.mark.
    django_db`'s implicit per-test transaction (held open on the MAIN
    thread's connection) would make the created row invisible to that
    other session entirely (default READ COMMITTED isolation never sees
    another session's uncommitted work), the same reason `models/queue/
    tests/test_claim.py`'s `TestConcurrency`/`TestSkipLocked` need it."""
    def test_echo_job_succeeds_and_stores_result(self, worker):
        _register("test.echo")
        _set_budget(memory_budget_bytes=None)
        job = _job(payload={"x": 1})

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == SUCCEEDED
        assert job.result == {"echo": {"x": 1}}
        assert job.finished_at is not None

    def test_measurement_raising_does_not_undo_a_finished_writeback(self, worker, monkeypatch):
        """M2 (review finding, T4): terminal writeback happens BEFORE
        opportunistic measurement, and `_execute` wraps the measurement
        call in its own try/except -- a measurement that raises must not
        throw away an already-succeeded job's result, nor leave the row
        stuck `running` for the orphan sweep to rediscover 120s later."""
        _register("test.echo")
        _set_budget(memory_budget_bytes=None)
        job = _job(payload={"x": 1})

        def _raise(*args, **kwargs):
            raise RuntimeError("measurement blew up")

        monkeypatch.setattr(worker, "_measure_and_record", _raise)

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == SUCCEEDED
        assert job.result == {"echo": {"x": 1}}

    def test_raising_handler_fails_with_operator_message_no_traceback_loop_survives(self, worker):
        _register("test.raise", handler=f"{MODULE}.raising_handler")
        _register("test.echo")
        _set_budget(memory_budget_bytes=None)
        job = _job(kind="test.raise")

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == FAILED
        assert job.error == "boom"
        assert "Traceback" not in job.error

        # Never-500: the worker's own loop must survive a raising handler
        # and keep claiming/running subsequent jobs normally.
        job2 = _job(kind="test.echo")
        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job2.refresh_from_db()
        assert job2.state == SUCCEEDED


# --- third stranding path: handler-never-started on_terminal (T9.5 M5) ------


@pytest.mark.django_db(transaction=True)
class TestHandlerNeverStartedFiresOnTerminal:
    """T9.5 review M5: `get_job_kind`/`resolve_dotted_path`/`ModelRef(**ref)`
    can all fail inside `_execute`'s try, BEFORE the handler is ever
    called -- an unregistered kind, a bad `handler` dotted path, a
    malformed model-ref dict. That failure writes the job row `FAILED`
    exactly like a raising handler does, but unlike a raising handler, the
    handler's own terminal writeback (the ONE place a kind like
    `rag.ingest` normally reaches its Document row on failure) never ran
    at all. `_execute` must fire the kind's registered `on_terminal` hook
    (state="failed") in that case -- and must NOT fire it when the
    handler DID start and raised on its own, since that handler's own
    writeback (or deliberate lack of one) is already the complete,
    correct account of that failure.

    Uses the REAL `rag.ingest` kind's registered hook
    (`tools.rag.jobs.on_ingest_terminal`) re-registered under a locally
    controlled `handler`/`planner` -- `reset_registry` (autouse) clears
    the production registry for the duration of every test in this file,
    so this is the same "register what this test needs" convention every
    other test here already follows, just under the same key production
    code uses so the real hook's own Document-status contract applies."""

    def _stage_doc(self):
        from tools.rag.models import Document

        return Document.objects.create(
            title="stranded.txt",
            source_path="/tmp/irrelevant/stranded.txt",
            original_path="/tmp/irrelevant/stranded.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            status=Document.Status.PENDING,
        )

    def test_bad_handler_dotted_path_fails_the_job_and_fires_on_terminal(self, worker):
        from tools.rag.models import Document

        register_job_kind(
            JobKind(
                key="rag.ingest",
                label="rag.ingest",
                planner=f"{MODULE}.plan_no_models",
                handler="tools.rag.jobs.this_handler_does_not_exist_at_all",
                summarizer=f"{MODULE}.summarize_noop",
                on_terminal="tools.rag.jobs.on_ingest_terminal",
            )
        )
        _set_budget(memory_budget_bytes=None)
        doc = self._stage_doc()
        job = _job(
            kind="rag.ingest",
            payload={"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
        )

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == FAILED

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == (
            "The worker running this job stopped responding; it was not retried again."
        )

    def test_handler_that_raises_after_starting_does_not_fire_on_terminal(self, worker, monkeypatch):
        from tools.rag.models import Document

        calls = []
        monkeypatch.setattr(
            worker_module, "invoke_on_terminal", lambda *args, **kwargs: calls.append((args, kwargs))
        )
        register_job_kind(
            JobKind(
                key="rag.ingest",
                label="rag.ingest",
                planner=f"{MODULE}.plan_no_models",
                handler=f"{MODULE}.raising_handler",
                summarizer=f"{MODULE}.summarize_noop",
                on_terminal="tools.rag.jobs.on_ingest_terminal",
            )
        )
        _set_budget(memory_budget_bytes=None)
        doc = self._stage_doc()
        job = _job(
            kind="rag.ingest",
            payload={"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
        )

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == FAILED
        assert job.error == "boom"

        # The hook must not have been scheduled at all -- the handler
        # STARTED (it's the one that raised), so its own writeback (here,
        # deliberately none -- `raising_handler` never touches the
        # Document) is the complete account of the failure.
        assert calls == []
        doc.refresh_from_db()
        assert doc.status == Document.Status.PENDING
        assert doc.status_detail == ""


# --- progress/checkpoint (T3), via a real worker-launched handler ------------


@pytest.mark.django_db(transaction=True)
class TestJobContextExecution:
    """`transaction=True` -- same reason `TestJobExecution` needs it: the
    handler runs on a real pool thread, its own DB session."""

    def test_success_clears_progress_and_checkpoint(self, worker):
        _register("test.progress_ckpt", handler=f"{MODULE}.progress_and_checkpoint_handler")
        _set_budget(memory_budget_bytes=None)
        job = _job(kind="test.progress_ckpt")

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == SUCCEEDED
        # The handler DID report/checkpoint mid-run -- a succeeded job's
        # stale bar/resume state is noise, so the terminal writeback
        # clears both (see `Worker._execute`'s own docstring).
        assert job.progress is None
        assert job.checkpoint is None

    def test_failed_handler_keeps_its_last_progress_and_checkpoint(self, worker):
        """The asymmetry's other half: a FAILED job's terminal writeback
        does NOT clear either column -- both stay exactly as the handler
        last wrote them (diagnostic value, and a future manual-retry path
        could resume from the same checkpoint)."""
        _register("test.ckpt_then_raise", handler=f"{MODULE}.checkpoint_then_raise_handler")
        _set_budget(memory_budget_bytes=None)
        job = _job(kind="test.ckpt_then_raise")

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == FAILED
        assert job.progress == {"done": 1, "total": 2, "unit": "items", "label": "things"}
        assert job.checkpoint == {"offset": 1}

    def test_resumed_attempt_receives_the_prior_checkpoint_state(self, worker, monkeypatch):
        """Drives the resume path for real: this job's row is seeded
        looking exactly like what `_sweep_orphans` would leave behind for
        a worker that checkpointed `{"offset": 1}` and then died before
        finishing (`state=RUNNING`, stale `heartbeat_at`, a dead worker
        id) -- `checkpoint`/`progress` PRESERVED, matching production
        (see `claim.py`'s `_sweep_orphans`, which never touches either
        column). ONE `tick()` call is enough to exercise sweep-then-
        reclaim-then-launch end to end: `claim_and_admit`'s orphan sweep
        and its candidate scan run inside the SAME transaction (see that
        function's own docstring, step 2), so a row this sweep just
        requeued is immediately visible to that same round's admission --
        no second external `tick()` call is needed to observe a genuinely
        RESUMED, worker-launched execution reading back the preserved
        state through its own `JobContext.checkpoint_state`.

        Also pins the descriptor -> `ctx` mapping for `attempt`
        (adversarial-review MINOR 2): the row starts at `attempts=0`, but
        `_sweep_orphans` bumps it to `1` as PART OF the same requeue that
        preserves the checkpoint -- so the resumed execution's own
        `ctx.attempt` must observe `1`, not the row's original `0`,
        exactly the concrete mapping `JobContext`'s own docstring now
        states (`0` on a first run, `1` after surviving one orphan
        requeue)."""
        _resume_observations.clear()
        _resume_attempt_observations.clear()
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        _register("test.resume", handler=f"{MODULE}.resume_recording_handler")
        _set_budget(memory_budget_bytes=None)
        job = _job(
            kind="test.resume", state=RUNNING, claimed_by="dead-worker",
            claim_token=uuid.uuid4(), heartbeat_at=timezone.now() - timedelta(seconds=1),
            attempts=0, progress={"done": 1, "total": 2, "unit": "items", "label": ""},
            checkpoint={"offset": 1},
        )

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        assert _resume_observations == [{"offset": 1}]
        assert _resume_attempt_observations == [1]
        job.refresh_from_db()
        assert job.state == SUCCEEDED
        assert job.attempts == 1  # bumped by the orphan sweep, exactly once
        assert job.checkpoint is None  # succeeded -- terminal writeback clears it


@pytest.mark.django_db
class TestJobContextWriters:
    """`Worker._build_job_context` (T3) -- the token-conditional progress/
    checkpoint writers `_execute` hands a claimed job's handler, exercised
    DIRECTLY (no thread, no `tick()`) since both writers are synchronous,
    ordinary DB calls with nothing background about them -- the same
    reasoning `TestHeartbeat`'s own class docstring gives for calling
    `_maybe_heartbeat` directly rather than through `tick()`."""

    def _descriptor(self, job: InferenceJob, **overrides) -> dict:
        fields = dict(
            id=job.pk, kind="test.echo", payload={}, model_refs=[],
            claim_token=job.claim_token, exclusive=False,
            checkpoint=job.checkpoint, attempts=job.attempts,
        )
        fields.update(overrides)
        return fields

    def test_progress_write_is_token_conditional_foreign_token_updates_nothing(self, worker):
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx = worker._build_job_context(self._descriptor(job, claim_token=uuid.uuid4()))

        ctx.report_progress(1, 10, unit="items")

        job.refresh_from_db()
        assert job.progress is None

    def test_checkpoint_write_is_token_conditional_foreign_token_updates_nothing(self, worker):
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx = worker._build_job_context(self._descriptor(job, claim_token=uuid.uuid4()))

        ctx.checkpoint({"offset": 1})

        job.refresh_from_db()
        assert job.checkpoint is None

    def test_progress_write_with_matching_token_lands(self, worker):
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx = worker._build_job_context(self._descriptor(job))

        ctx.report_progress(3, 10, unit="items", label="things")

        job.refresh_from_db()
        assert job.progress == {"done": 3, "total": 10, "unit": "items", "label": "things"}

    def test_progress_writes_throttled_two_rapid_calls_yield_one_update(self, worker):
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx = worker._build_job_context(self._descriptor(job))

        ctx.report_progress(1, 10, unit="items")
        ctx.report_progress(2, 10, unit="items")  # within PROGRESS_INTERVAL_SECONDS -- dropped

        job.refresh_from_db()
        assert job.progress == {"done": 1, "total": 10, "unit": "items", "label": ""}

    def test_progress_write_lands_after_throttle_window_elapses(self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "PROGRESS_INTERVAL_SECONDS", 0)
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx = worker._build_job_context(self._descriptor(job))

        ctx.report_progress(1, 10, unit="items")
        ctx.report_progress(2, 10, unit="items")  # window is 0 -- both land

        job.refresh_from_db()
        assert job.progress == {"done": 2, "total": 10, "unit": "items", "label": ""}

    def test_checkpoint_writes_are_unthrottled_each_call_lands_immediately(self, worker):
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx = worker._build_job_context(self._descriptor(job))

        ctx.checkpoint({"offset": 1})
        job.refresh_from_db()
        assert job.checkpoint == {"offset": 1}  # first write landed, no throttle window to wait out

        ctx.checkpoint({"offset": 2})
        job.refresh_from_db()
        assert job.checkpoint == {"offset": 2}  # second write landed too, immediately

    def test_writers_never_touch_heartbeat_at(self, worker):
        """The single-writer invariant (`Worker._maybe_heartbeat`'s own
        docstring): NEITHER writer this method builds may ever mention
        `heartbeat_at` -- proven here by seeding a deliberately stale
        value and asserting it is untouched by either write."""
        job = _job(state=RUNNING, claim_token=uuid.uuid4())
        stale_heartbeat = timezone.now() - timedelta(seconds=999)
        InferenceJob.objects.filter(pk=job.pk).update(heartbeat_at=stale_heartbeat)
        ctx = worker._build_job_context(self._descriptor(job))

        ctx.report_progress(1, 10, unit="items")
        ctx.checkpoint({"offset": 1})

        job.refresh_from_db()
        assert job.heartbeat_at == stale_heartbeat

    def test_response_timeout_seconds_is_stamped_from_job_settings(self, worker):
        """One-timeout task (2026-09-17): `_build_job_context` is the ONE
        place this worker's queue-storage read (`JobSettings.get_solo()`)
        crosses into the `JobContext` `agents/` may not read that table
        for itself (import law)."""
        settings = JobSettings.get_solo()
        settings.response_timeout_seconds = 222
        settings.save()
        job = _job(state=RUNNING, claim_token=uuid.uuid4())

        ctx = worker._build_job_context(self._descriptor(job))

        assert ctx.response_timeout_seconds == 222.0
        assert isinstance(ctx.response_timeout_seconds, float)

    def test_response_timeout_seconds_falls_back_to_the_model_default(self, worker):
        """No `JobSettings` row seeded -- `get_solo()` creates one with
        its own documented default rather than this method guessing.
        Deletes any pre-existing row FIRST: a `transaction=True` sibling
        test elsewhere in this module can leave one committed, since
        those tests bypass the ordinary per-test transaction rollback."""
        JobSettings.objects.all().delete()
        job = _job(state=RUNNING, claim_token=uuid.uuid4())

        ctx = worker._build_job_context(self._descriptor(job))

        assert ctx.response_timeout_seconds == float(
            JobSettings.RESPONSE_TIMEOUT_SECONDS_DEFAULT
        )

    def test_a_raising_settings_read_degrades_to_none_rather_than_raising(
        self, worker, monkeypatch,
    ):
        """B1 (fix round 1): `_build_job_context` is `_execute`'s ONE
        caller of it, and that call sits OUTSIDE `_execute`'s own
        `try`/`finally` -- so anything this method lets escape would
        strand the job RUNNING forever (see the integration-level pin
        below). The settings read is guarded: a raising `get_solo()`
        degrades to the documented `None` fallback, never propagates."""
        def _raise(*args, **kwargs):
            raise RuntimeError("db blip")

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_raise))
        job = _job(state=RUNNING, claim_token=uuid.uuid4())

        ctx = worker._build_job_context(self._descriptor(job))

        assert ctx.response_timeout_seconds is None

    @pytest.mark.django_db(transaction=True)
    def test_a_raising_settings_read_cannot_strand_the_job_running(
        self, worker, monkeypatch,
    ):
        """B1's own stranding scenario, at the integration level: if the
        settings read escaped `_build_job_context` uncaught, it would
        raise OUTSIDE `_execute`'s `try:`, so no FAILED writeback would
        run, this attempt's claim token would never be popped from
        `_active_tokens`, and the orphan sweep could never reclaim the
        row -- permanently RUNNING. With the guard in place, the job
        still reaches a terminal state exactly as if the setting had
        read cleanly.

        Raises on the SECOND `get_solo()` call, not the first: `tick()`
        itself makes ONE `JobSettings` read of its own before ever
        claiming a job (`tick()`'s own docstring, "ONE `JobSettings` READ
        PER TICK", pinned by `TestTheSingleSettingsReadPerTick`) -- that
        first call must keep succeeding, or this test would be exercising
        a `tick()`-level failure instead of `_build_job_context`'s own."""
        _register("test.echo")
        job = _job(payload={"x": 1})
        real_get_solo = JobSettings.get_solo
        calls = {"n": 0}

        def _flaky_get_solo(cls):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("db blip")
            return real_get_solo()

        monkeypatch.setattr(JobSettings, "get_solo", classmethod(_flaky_get_solo))

        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == SUCCEEDED
        assert job.result == {"echo": {"x": 1}}

    def test_two_contexts_throttle_progress_independently(self, worker):
        """`PROGRESS_INTERVAL_SECONDS`'s own comment promises this: throttle
        state lives in a closure `_build_job_context` builds fresh per
        call, NOT on a shared/global clock -- so job A's report a moment
        ago must not throttle job B's very FIRST report, even though both
        run on the same worker at the same time."""
        job_a = _job(state=RUNNING, claim_token=uuid.uuid4())
        job_b = _job(state=RUNNING, claim_token=uuid.uuid4())
        ctx_a = worker._build_job_context(self._descriptor(job_a))
        ctx_b = worker._build_job_context(self._descriptor(job_b))

        ctx_a.report_progress(1, 10, unit="items")
        ctx_a.report_progress(2, 10, unit="items")  # throttled -- A's own window
        ctx_b.report_progress(5, 10, unit="items")  # B's first call -- must still land

        job_a.refresh_from_db()
        job_b.refresh_from_db()
        assert job_a.progress == {"done": 1, "total": 10, "unit": "items", "label": ""}
        assert job_b.progress == {"done": 5, "total": 10, "unit": "items", "label": ""}


@pytest.mark.django_db(transaction=True)
class TestStaleWriteback:
    def test_mismatched_claim_token_writeback_is_discarded(self, worker):
        """Launched via `_launch` (a real pool thread), not called
        in-process -- see `TestJobExecution`'s docstring for why a
        different-session write needs `transaction=True` to be visible at
        all here."""
        _register("test.echo")
        real_token = uuid.uuid4()
        job = _job(state=RUNNING, claimed_by=worker.worker_id, claim_token=real_token)
        descriptor = {
            "id": job.pk,
            "kind": "test.echo",
            "payload": {},
            "model_refs": [],
            "claim_token": uuid.uuid4(),  # deliberately mismatched
            "exclusive": False,
        }

        worker._launch(descriptor)
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.claim_token == real_token


@pytest.mark.django_db(transaction=True)
class TestRunJobsCommand:
    def test_once_flag_calls_tick_exactly_once_and_claims_the_job(self, monkeypatch):
        """`--once`'s actual, documented contract (see run_jobs.py's own
        docstring) is "call Worker.tick() exactly once, then return" --
        asserted STRUCTURALLY here (a monkeypatched, counting wrapper
        around `Worker.tick`), not by inspecting the claimed job's
        eventual terminal state. That state check flaked under system
        load (review finding, T4 final round): the handler itself runs
        asynchronously on a pool thread this command never waits on, so
        whether it has reached `succeeded` by the time an assertion runs
        depends on scheduling, not on anything `--once` actually
        promises. Claiming itself -- leaving `queued`, `claim_token` set
        -- happens SYNCHRONOUSLY inside `tick()`, before anything is
        handed to a background thread at all, so asserting on THAT
        remains deterministic regardless of how the async handler race
        resolves."""
        _register("test.echo")
        _set_budget(memory_budget_bytes=None)
        job = _job()

        tick_call_count = 0
        original_tick = Worker.tick

        def counting_tick(self):
            nonlocal tick_call_count
            tick_call_count += 1
            return original_tick(self)

        monkeypatch.setattr(Worker, "tick", counting_tick)

        call_command("run_jobs", "--once")

        assert tick_call_count == 1
        job.refresh_from_db()
        assert job.state != QUEUED
        assert job.claim_token is not None


@pytest.mark.django_db(transaction=True)
class TestStopRequestedDuringTick:
    """MINOR 1 (review finding, T4): `tick()` re-checks `self._stopping`
    between claim+evict and launch -- a stop requested while
    `claim_and_admit` was busy committing this round's admissions must
    hand the freshly-claimed batch straight back to the queue instead of
    launching it. There is nothing to `_drain_inflight` for a job that was
    never actually handed to a thread."""

    def test_stopping_before_launch_requeues_the_batch_instead(self, worker):
        _register("test.echo")
        _set_budget(memory_budget_bytes=None)
        job = _job()

        worker._stopping.set()
        worker.tick()

        assert worker._futures == {}  # nothing was ever launched
        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.claim_token is None
        assert job.claimed_by == ""
        assert job.started_at is None
        assert job.heartbeat_at is None


@pytest.mark.django_db
class TestHeartbeat:
    """`_maybe_heartbeat` called directly (never through `tick()`, which
    calls `close_old_connections()` and would need `transaction=True`
    instead) -- plain `django_db` is genuinely fine for these, no
    background thread and no `close_old_connections()` call involved."""

    def test_throttled_within_heartbeat_seconds(self, worker):
        job = _job(kind="test.echo", state=RUNNING, claim_token=uuid.uuid4())
        worker._active_tokens[job.pk] = job.claim_token
        worker._last_heartbeat_monotonic = time.monotonic()  # "just heartbeated"

        old_time = timezone.now() - timedelta(seconds=5)
        InferenceJob.objects.filter(pk=job.pk).update(heartbeat_at=old_time)

        worker._maybe_heartbeat()

        job.refresh_from_db()
        assert job.heartbeat_at == old_time  # throttled -- no write yet

    def test_writes_after_throttle_window_elapses(self, worker):
        job = _job(kind="test.echo", state=RUNNING, claim_token=uuid.uuid4())
        worker._active_tokens[job.pk] = job.claim_token
        worker._last_heartbeat_monotonic = (
            time.monotonic() - (worker_module.HEARTBEAT_SECONDS + 1)
        )

        old_time = timezone.now() - timedelta(seconds=999)
        InferenceJob.objects.filter(pk=job.pk).update(heartbeat_at=old_time)

        worker._maybe_heartbeat()

        job.refresh_from_db()
        assert job.heartbeat_at > old_time

    def test_only_refreshes_rows_matching_this_process_tokens_not_foreign_tokens(self, worker):
        """MINOR 5 (review finding, T4): the heartbeat UPDATE is
        conditioned on `claim_token__in=tokens` alone -- a RUNNING row
        under a token this process does NOT hold in `_active_tokens` must
        not be refreshed, even if its `claimed_by` string happens to equal
        this worker's own id (the exact redundant fact `claimed_by` must
        never be trusted for -- see `_maybe_heartbeat`'s docstring)."""
        mine = _job(
            kind="test.echo", state=RUNNING, claimed_by=worker.worker_id, claim_token=uuid.uuid4(),
        )
        foreign = _job(
            kind="test.echo", state=RUNNING, claimed_by=worker.worker_id, claim_token=uuid.uuid4(),
        )
        worker._active_tokens[mine.pk] = mine.claim_token
        # foreign.pk is deliberately NOT in worker._active_tokens.

        old_time = timezone.now() - timedelta(seconds=999)
        InferenceJob.objects.filter(pk__in=[mine.pk, foreign.pk]).update(heartbeat_at=old_time)

        worker._maybe_heartbeat()

        mine.refresh_from_db()
        foreign.refresh_from_db()
        assert mine.heartbeat_at > old_time
        assert foreign.heartbeat_at == old_time  # untouched -- not this worker's own token

    def test_heartbeat_prevents_orphan_sweep(self, worker):
        """A fresh heartbeat write must save a job from the orphan sweep
        (`models.queue.claim._sweep_orphans`) even under an aggressively
        small staleness threshold -- proving the two mechanisms actually
        agree on what "fresh" means."""
        from models.queue.claim import _sweep_orphans

        job = _job(
            kind="test.echo", state=RUNNING, claimed_by=worker.worker_id, claim_token=uuid.uuid4(),
        )
        worker._active_tokens[job.pk] = job.claim_token
        InferenceJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=5)
        )
        worker._last_heartbeat_monotonic = None  # force the throttle to allow a write

        worker._maybe_heartbeat()
        _sweep_orphans(stale_after_seconds=1)

        job.refresh_from_db()
        assert job.state == RUNNING  # the fresh heartbeat saved it from the sweep

    def test_constructing_a_worker_starts_no_thread(self, worker):
        """`--once` and every test in this suite construct a Worker; none
        of them may leak a daemon thread."""
        assert worker._heartbeat_thread is None

    @pytest.mark.django_db(transaction=True)
    def test_the_thread_refreshes_a_row_while_the_tick_thread_is_blocked(self, worker, monkeypatch):
        """The Q8 shape: the tick thread is stuck inside a synchronous
        eviction pass (or a starved process during a long cold load) and
        writes nothing, and the sweep orphans a healthy job. The dedicated
        thread is what keeps that row alive."""
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
        job = _job(state=RUNNING)
        token = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(
            claim_token=token, heartbeat_at=timezone.now() - timedelta(seconds=60),
        )
        with worker._active_lock:
            worker._active_tokens[job.pk] = token

        worker._start_heartbeat_thread()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                job.refresh_from_db()
                if job.heartbeat_at > timezone.now() - timedelta(seconds=5):
                    break
                time.sleep(0.05)
        finally:
            worker._stopping.set()
            worker._heartbeat_thread.join(timeout=5)

        job.refresh_from_db()
        assert job.heartbeat_at > timezone.now() - timedelta(seconds=5)

    def test_the_thread_closes_its_own_connections_each_iteration(self, worker, monkeypatch):
        """Nothing else ever closes or health-checks this thread's own
        connection -- Django's are thread-local."""
        calls = []
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
        monkeypatch.setattr(worker_module, "close_old_connections", lambda: calls.append(1))

        worker._start_heartbeat_thread()
        try:
            time.sleep(0.2)
        finally:
            worker._stopping.set()
            worker._heartbeat_thread.join(timeout=5)

        assert calls

    def test_a_transient_write_error_does_not_kill_the_thread(self, worker, monkeypatch, caplog):
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)
        failures = {"count": 0}

        def _boom():
            failures["count"] += 1
            if failures["count"] <= 2:
                raise OperationalError("connection lost")

        monkeypatch.setattr(worker, "_maybe_heartbeat", _boom)

        with caplog.at_level("WARNING", logger="models.queue.worker"):
            worker._start_heartbeat_thread()
            try:
                time.sleep(0.3)
            finally:
                worker._stopping.set()
                worker._heartbeat_thread.join(timeout=5)

        assert failures["count"] > 2, "the thread stopped at the first error"
        assert any("heartbeat" in r.getMessage() for r in caplog.records)

    def test_the_thread_says_so_loudly_if_it_ever_exits(self, worker, monkeypatch, caplog):
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.05)

        with caplog.at_level("INFO", logger="models.queue.worker"):
            worker._start_heartbeat_thread()
            try:
                time.sleep(0.15)
            finally:
                worker._stopping.set()
                worker._heartbeat_thread.join(timeout=5)

        assert any("heartbeat thread" in r.getMessage() for r in caplog.records)

    @pytest.mark.django_db(transaction=True)
    def test_the_throttle_is_read_and_written_under_the_lock(self, worker):
        """BEHAVIOUR, not source order. An earlier draft asserted that
        `with self._active_lock` appeared before `_last_heartbeat_
        monotonic` in the method's SOURCE -- which this same step's
        instruction to name that attribute in the docstring would have
        flipped, since a docstring IS part of the source.

        Instead: hold the lock from this thread, and prove the writer
        cannot get past its own throttle read while it is held. If the
        read-modify-write sat outside the lock, the call would return
        having written, and the two threads could interleave it."""
        job = _job(state=RUNNING)
        token = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(claim_token=token, heartbeat_at=None)
        with worker._active_lock:
            worker._active_tokens[job.pk] = token
            blocked = threading.Thread(target=worker._maybe_heartbeat, daemon=True)
            blocked.start()
            blocked.join(timeout=0.5)
            still_blocked = blocked.is_alive()
        blocked.join(timeout=5)

        assert still_blocked, "_maybe_heartbeat ran its throttle outside the lock"


# --- orphan sweep, via the worker's own wiring -------------------------------


@pytest.mark.django_db(transaction=True)
class TestOrphanSweepViaTick:
    """`transaction=True`: `Worker.tick()` calls `close_old_connections()`,
    which unconditionally closes the connection when called from inside an
    ambient `atomic()` block (autocommit is off inside one, which
    `close_if_unusable_or_obsolete()` reads as "stale", closing it right
    out from under the very next query) -- exactly what plain
    `@pytest.mark.django_db` wraps every test body in. Fine in production
    (a worker tick is never itself nested inside someone else's
    transaction) but fatal under the plain marker's implicit wrapper; any
    test that calls `tick()` needs `transaction=True`, same as
    `TestJobExecution` above."""

    def test_first_orphan_is_requeued_via_tick(self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        _set_budget(memory_budget_bytes=10 * GB, max_concurrent_jobs=4)
        # An unrelated, healthy, exclusive running job blocks admission
        # entirely this round (rule 3, scheduler.py) so the requeue is
        # observable rather than the requeued row being a valid candidate
        # and immediately re-admitted (and launched onto the pool) within
        # this same tick -- see models/queue/tests/test_claim.py's
        # identical fix for the full explanation. (This class still needs
        # `transaction=True` regardless -- see the class docstring: it is
        # `tick()`'s own `close_old_connections()` call that requires it,
        # not anything about background threads.)
        _job(state=RUNNING, claimed_by="alive", claim_token=uuid.uuid4(),
             exclusive=True, heartbeat_at=timezone.now())

        job = _job(
            state=RUNNING, claimed_by="dead-worker", claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=1), attempts=0,
        )

        worker.tick()

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.attempts == 1
        assert job.claim_token is None

    def test_second_orphan_fails_with_exact_error_via_tick(self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        job = _job(
            state=RUNNING, claimed_by="dead-worker", claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=1), attempts=1,
        )

        worker.tick()

        job.refresh_from_db()
        assert job.state == FAILED
        assert job.error == (
            "the worker running this job stopped responding twice; it was not retried again"
        )

    def test_first_orphan_requeue_preserves_progress_and_checkpoint(self, worker, monkeypatch):
        """T3: `_sweep_orphans`' requeue UPDATE deliberately omits
        `progress`/`checkpoint` from its field list -- their PRESERVATION
        across this requeue IS the resume mechanism (see that function's
        own comment). Same blocking-exclusive-job setup as
        `test_first_orphan_is_requeued_via_tick` above, so the requeue is
        observable rather than immediately re-claimed this same tick."""
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        _set_budget(memory_budget_bytes=10 * GB, max_concurrent_jobs=4)
        _job(state=RUNNING, claimed_by="alive", claim_token=uuid.uuid4(),
             exclusive=True, heartbeat_at=timezone.now())

        job = _job(
            state=RUNNING, claimed_by="dead-worker", claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=1), attempts=0,
            progress={"done": 3, "total": 10, "unit": "pages", "label": ""},
            checkpoint={"offset": 3},
        )

        worker.tick()

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.progress == {"done": 3, "total": 10, "unit": "pages", "label": ""}
        assert job.checkpoint == {"offset": 3}

    def test_second_orphan_failure_preserves_progress_and_checkpoint(self, worker, monkeypatch):
        """The permanently-`failed` branch preserves both too -- diagnostic
        value for a job that will never run again, matching the same
        success/failure asymmetry `Worker._execute`'s terminal writeback
        documents (failure keeps both, success clears both)."""
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        job = _job(
            state=RUNNING, claimed_by="dead-worker", claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=1), attempts=1,
            progress={"done": 7, "total": 10, "unit": "pages", "label": ""},
            checkpoint={"offset": 7},
        )

        worker.tick()

        job.refresh_from_db()
        assert job.state == FAILED
        assert job.progress == {"done": 7, "total": 10, "unit": "pages", "label": ""}
        assert job.checkpoint == {"offset": 7}


# --- SIGTERM drain ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestDrain:
    """`transaction=True`: `_launch` submits `_execute` to a REAL pool
    thread with its own DB session -- under plain `@pytest.mark.django_db`
    the job row created here would never be committed, so that thread's
    eventual writeback would match zero rows for the WRONG reason (the row
    is invisible to its session entirely) rather than the reason this
    class actually means to prove (the row is visible, but no longer
    `running` under this token -- see the first test's trailing comment)."""

    def test_inflight_job_is_requeued_claim_token_cleared_attempts_unchanged(self, worker):
        _slow_release.clear()
        _register("test.slow", handler=f"{MODULE}.slow_handler")
        job = _job(kind="test.slow", state=RUNNING, claimed_by=worker.worker_id,
                    claim_token=uuid.uuid4(), attempts=0)
        descriptor = {
            "id": job.pk, "kind": "test.slow", "payload": {}, "model_refs": [],
            "claim_token": job.claim_token, "exclusive": False,
        }
        # `tick()` registers a claimed batch's tokens BEFORE `_launch` is
        # ever called (final review round, T4) -- reproduced manually here
        # since this test drives `_launch` directly, bypassing `tick()`.
        worker._active_tokens[job.pk] = job.claim_token
        worker._launch(descriptor)

        requeued = worker._drain_inflight(timeout=0.2)

        assert requeued == [job.pk]
        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.claim_token is None
        assert job.attempts == 0  # a drained job is not a crashed job

        # Let the background thread finish so it doesn't leak past the
        # test. Its own writeback (`_execute`'s conditional UPDATE, keyed
        # on the OLD claim_token) now genuinely finds the row it's looking
        # for has already moved on -- `state` is `queued`, not `running`,
        # and `claim_token` is `None`, not the token this thread still
        # holds -- so it matches zero rows and logs "stale writeback
        # discarded", proving the SAME mechanism `TestStaleWriteback`
        # exercises directly, this time as a consequence of a real drain.
        _slow_release.set()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)
        job.refresh_from_db()
        assert job.state == QUEUED  # unchanged by the late, discarded writeback

    def test_drain_preserves_progress_and_checkpoint(self, worker):
        """T3: `_drain_inflight`'s requeue UPDATE deliberately omits
        `progress`/`checkpoint` from its field list -- a voluntarily
        drained job's checkpoint must survive exactly like an orphaned
        one's does (`TestOrphanSweepViaTick`'s own preservation tests),
        so whichever worker next claims this row can resume from it."""
        _slow_release.clear()
        _register("test.slow", handler=f"{MODULE}.slow_handler")
        job = _job(
            kind="test.slow", state=RUNNING, claimed_by=worker.worker_id,
            claim_token=uuid.uuid4(), attempts=0,
            progress={"done": 1, "total": 2, "unit": "items", "label": ""},
            checkpoint={"offset": 1},
        )
        descriptor = {
            "id": job.pk, "kind": "test.slow", "payload": {}, "model_refs": [],
            "claim_token": job.claim_token, "exclusive": False,
        }
        worker._active_tokens[job.pk] = job.claim_token
        worker._launch(descriptor)

        requeued = worker._drain_inflight(timeout=0.2)

        assert requeued == [job.pk]
        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.progress == {"done": 1, "total": 2, "unit": "items", "label": ""}
        assert job.checkpoint == {"offset": 1}

        _slow_release.set()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

    def test_drain_with_nothing_inflight_returns_empty(self, worker):
        assert worker._drain_inflight(timeout=0.1) == []

    def test_drain_does_not_touch_a_row_whose_token_was_reclaimed_by_another_worker(self, worker):
        """M1 (review finding, T4): this worker's OWN snapshot of a job's
        token (taken under `_active_lock` alongside its future) must be
        what the requeue UPDATE is conditioned on -- if some OTHER worker
        has already reclaimed the row under a DIFFERENT token by the time
        the drain runs (the exact trace `_drain_inflight`'s docstring
        walks through: this worker's own tick loop stalled long enough for
        another worker's orphan sweep to requeue-and-reclaim the row while
        this worker's pool thread was still, unaware, running the job
        under the OLD token), the drain must not touch that row at all --
        not requeue it, not clear its NEW claim_token, nothing."""
        _slow_release.clear()
        _register("test.slow", handler=f"{MODULE}.slow_handler")
        original_token = uuid.uuid4()
        job = _job(
            kind="test.slow", state=RUNNING, claimed_by=worker.worker_id,
            claim_token=original_token, attempts=0,
        )
        descriptor = {
            "id": job.pk, "kind": "test.slow", "payload": {}, "model_refs": [],
            "claim_token": original_token, "exclusive": False,
        }
        # `tick()` registers a claimed batch's tokens BEFORE `_launch` is
        # ever called (final review round, T4) -- reproduced manually here
        # since this test drives `_launch` directly, bypassing `tick()`.
        # Without this, `_drain_inflight`'s snapshot would see no token at
        # all for this job (treated as "already finished, skip"), passing
        # this test for the wrong reason rather than genuinely exercising
        # the stale-token comparison it means to prove.
        worker._active_tokens[job.pk] = original_token
        worker._launch(descriptor)

        # Simulate another worker's orphan sweep reclaiming this same row
        # under a fresh token WHILE this worker's own (unaware) pool
        # thread is still running it under `original_token`.
        reclaiming_token = uuid.uuid4()
        InferenceJob.objects.filter(pk=job.pk).update(
            claimed_by="other-worker", claim_token=reclaiming_token,
        )

        requeued = worker._drain_inflight(timeout=0.2)

        assert requeued == []  # nothing touched -- the token no longer matches
        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.claim_token == reclaiming_token
        assert job.claimed_by == "other-worker"

        _slow_release.set()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)


# --- reclaimed-attempt token/future collision (2026-08-25 defect fix) -------


@pytest.mark.django_db(transaction=True)
class TestReclaimedAttemptTokenCollision:
    """Regression test for the defect (peer-session report, InferenceJob
    22, reproduced on their preview 2026-08-25 04:50-05:10 UTC):
    `_execute`'s `finally` used to pop `self._active_tokens[job_id]`
    UNCONDITIONALLY. Sequence: attempt A runs long (its handler still
    alive) -> its heartbeat goes stale (e.g. process starvation during a
    cold model load) -> `_sweep_orphans` requeues the row -> this SAME
    worker's very next `tick()` re-claims it as attempt B, a brand-new
    token -> attempt A's thread eventually reaches its own `finally` and,
    pre-fix, popped `self._active_tokens[job_id]` regardless of whose
    token was actually stored there -> attempt B's token vanished ->
    `_maybe_heartbeat` never heartbeats B again -> B gets orphaned a
    SECOND time and permanently failed while its handler is still
    genuinely running and writing progress.

    `transaction=True`: both attempts run on real pool threads with their
    own DB sessions (same reason `TestJobExecution`/`TestOrphanSweepViaTick`/
    `TestDrain` above all need it -- see `TestJobExecution`'s own class
    docstring)."""

    def test_attempt_a_finishing_late_does_not_evict_attempt_bs_bookkeeping(
        self, worker, monkeypatch,
    ):
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        _reclaim_attempt_a_release.clear()
        _reclaim_attempt_b_release.clear()
        _register("test.reclaim", handler=f"{MODULE}.reclaim_collision_handler")
        job = _job(kind="test.reclaim")

        # Tick 1: claim + launch attempt A (attempts=0) -- it blocks.
        worker.tick()
        job.refresh_from_db()
        assert job.state == RUNNING
        token_a = job.claim_token
        assert token_a is not None
        future_a = worker._futures[(job.pk, token_a)]

        # Age the heartbeat so the NEXT tick's orphan sweep -- run by this
        # SAME worker -- treats attempt A as dead (STALE_AFTER_SECONDS is
        # patched to 0 above, so any past heartbeat_at now qualifies).
        InferenceJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=1)
        )

        # Tick 2: `_sweep_orphans` requeues the stale row (attempts bumped
        # 0 -> 1) and, in the SAME transaction, `claim_and_admit` re-admits
        # it -- launched here as attempt B under a brand-new token, while
        # attempt A's thread is still blocked on
        # `_reclaim_attempt_a_release`, unaware.
        worker.tick()
        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.attempts == 1
        token_b = job.claim_token
        assert token_b is not None
        assert token_b != token_a
        assert worker._active_tokens[job.pk] == token_b
        future_b = worker._futures[(job.pk, token_b)]
        assert future_b is not future_a
        # Per-attempt keying (2026-08-25 second defect fix): A's own entry
        # is untouched by B's `_launch` call -- both attempts' futures are
        # simultaneously reachable, under their own distinct keys.
        assert worker._futures[(job.pk, token_a)] is future_a

        # Release attempt A and wait for ITS OWN future specifically --
        # deterministic: `Future.done()` only becomes `True` after
        # `_execute`'s `finally` has already run to completion. Attempt B
        # is still blocked on its own event throughout this wait.
        _reclaim_attempt_a_release.set()
        concurrent.futures.wait([future_a], timeout=5)

        # The fix under test: attempt A's late `finally` must NOT have
        # evicted attempt B's still-active bookkeeping.
        assert worker._active_tokens.get(job.pk) == token_b
        assert worker._futures.get((job.pk, token_b)) is future_b
        # A's own entry, keyed separately, is STILL present and reachable
        # too -- `_execute`'s `finally` never touches `self._futures` at
        # all (see its own comment); only `_prune_finished_futures` (not
        # called in this test) would remove it. This is exactly what makes
        # A's escaped-exception safety net available even for a superseded
        # attempt -- see `TestSupersededAttemptExceptionNet` below.
        assert worker._futures.get((job.pk, token_a)) is future_a

        # Attempt A's own writeback is token-conditional (ADR 0013) and
        # must have matched zero rows -- the row must still look exactly
        # like B's in-progress claim, not A's completion.
        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.claim_token == token_b
        assert job.result is None

        # A subsequent heartbeat write must still be able to refresh this
        # row under B's token -- proving B's token is genuinely still
        # "active" in `self._active_tokens`, not merely present by
        # coincidence.
        InferenceJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=999)
        )
        worker._last_heartbeat_monotonic = None
        worker._maybe_heartbeat()
        job.refresh_from_db()
        assert job.heartbeat_at > timezone.now() - timedelta(seconds=5)

        # Let attempt B complete normally.
        _reclaim_attempt_b_release.set()
        concurrent.futures.wait([future_b], timeout=5)
        job.refresh_from_db()
        assert job.state == SUCCEEDED
        assert job.result == {"echo": "attempt-b"}
        assert job.claim_token == token_b
        assert job.pk not in worker._active_tokens

    def test_normal_single_attempt_still_cleans_both_dicts(self, worker):
        """The fix's token check must not regress the ordinary,
        never-reclaimed path: a job's only attempt still cleans up after
        itself completely -- `self._active_tokens` immediately (`_execute`'s
        own `finally`), `self._futures` once `_prune_finished_futures`
        next runs (its own, unrelated cleanup path -- see the comment next
        to `_execute`'s `finally` for why `_execute` itself must not touch
        `self._futures` at all)."""
        _register("test.echo")
        _set_budget(memory_budget_bytes=None)
        job = _job(payload={"x": 1})

        worker.tick()
        job.refresh_from_db()
        token = job.claim_token
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)

        assert job.pk not in worker._active_tokens
        worker._prune_finished_futures()
        assert (job.pk, token) not in worker._futures


# --- per-attempt `_futures` keying (2026-08-25 second defect fix) -----------
#
# `self._futures` used to be keyed by bare `job_id`, so a reclaimed
# attempt's own `_launch` call silently overwrote a still-live superseded
# attempt's `Future` -- with nothing left referencing it, an exception that
# escaped `_execute`'s own never-raise guard was lost forever
# (`_prune_finished_futures` never saw it), and `_shutdown`'s
# `executor.shutdown(wait=True)` branch could block on that same untracked
# thread with no bound at all, defeating `SHUTDOWN_GRACE_SECONDS`. Keying by
# `(job_id, claim_token)` instead means every attempt this process ever
# launches keeps its own entry until `_prune_finished_futures` removes it.
# `TestReclaimedAttemptTokenCollision` above already proves both attempts'
# entries coexist; the classes below prove the two things that coexistence
# was actually FOR: the exception net now covers a superseded attempt too,
# and `_shutdown`/`_drain_inflight` correctly account for one that is still
# running past the grace period.


class RaisingForAttemptZeroWorker(Worker):
    """Models "an exception escaped `_execute`'s own guard entirely" (the
    exact case `_prune_finished_futures` exists to surface) for ATTEMPT A
    specifically (`ctx.attempt == 0`, i.e. `descriptor["attempts"] == 0`)
    -- attempt B (`attempts == 1`, the reclaim) is left alone so it can
    complete normally and prove the fix doesn't touch it."""

    def _execute(self, descriptor):
        super()._execute(descriptor)
        if descriptor.get("attempts", 0) == 0:
            raise RuntimeError("escaped-guard-attempt-0")


@pytest.mark.django_db(transaction=True)
class TestSupersededAttemptExceptionNet:
    """Regression coverage for finding 1(a): a SUPERSEDED attempt whose
    handler raises past `_execute`'s own guard must still be surfaced by
    `_prune_finished_futures`, not silently lost the moment `_launch`
    registers the reclaiming attempt's own entry. Reuses
    `reclaim_collision_handler`/its two release `Event`s from
    `TestReclaimedAttemptTokenCollision` above so attempt A (blocked on
    `_reclaim_attempt_a_release`) and attempt B (blocked on
    `_reclaim_attempt_b_release`) can both be alive at once, released
    independently."""

    def test_prune_surfaces_a_superseded_attempts_escaped_exception(
        self, monkeypatch, caplog,
    ):
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        _reclaim_attempt_a_release.clear()
        _reclaim_attempt_b_release.clear()
        _register("test.reclaim", handler=f"{MODULE}.reclaim_collision_handler")
        job = _job(kind="test.reclaim")

        w = RaisingForAttemptZeroWorker(worker_id="test-worker")
        try:
            w.tick()  # attempt A (attempts=0) -- blocks
            job.refresh_from_db()
            token_a = job.claim_token
            future_a = w._futures[(job.pk, token_a)]

            InferenceJob.objects.filter(pk=job.pk).update(
                heartbeat_at=timezone.now() - timedelta(seconds=1)
            )

            w.tick()  # attempt B reclaims (attempts=1) -- also blocks
            job.refresh_from_db()
            token_b = job.claim_token
            assert token_b != token_a
            future_b = w._futures[(job.pk, token_b)]

            # Release A only. Its own handler call succeeds, but the
            # subclass then raises PAST `_execute`'s never-raise guard.
            _reclaim_attempt_a_release.set()
            concurrent.futures.wait([future_a], timeout=5)
            assert future_a.exception() is not None
            assert str(future_a.exception()) == "escaped-guard-attempt-0"
            assert not future_b.done()  # B is still genuinely running

            with caplog.at_level(logging.ERROR):
                w._prune_finished_futures()

            assert f"job {job.pk}'s future raised past _execute's own" in caplog.text
            assert "escaped-guard-attempt-0" in caplog.text  # traceback text

            # A's entry is now pruned; B's -- still not done -- survives.
            assert (job.pk, token_a) not in w._futures
            assert w._futures.get((job.pk, token_b)) is future_b

            _reclaim_attempt_b_release.set()
            concurrent.futures.wait([future_b], timeout=5)
            job.refresh_from_db()
            assert job.state == SUCCEEDED
            assert job.claim_token == token_b
        finally:
            _reclaim_attempt_a_release.set()
            _reclaim_attempt_b_release.set()
            w._executor.shutdown(wait=True)


@pytest.mark.django_db(transaction=True)
class TestShutdownAccountsForSupersededAttempt:
    """Regression coverage for finding 1(b)/1(c): `_drain_inflight` and
    `_shutdown` must both treat a SUPERSEDED attempt that is still running
    past the grace period as genuinely in-flight, not merely whatever
    `job_id`'s CURRENT slot happens to hold."""

    def _two_live_attempts(self, worker, monkeypatch):
        monkeypatch.setattr(worker_module, "STALE_AFTER_SECONDS", 0)
        _reclaim_attempt_a_release.clear()
        _reclaim_attempt_b_release.clear()
        _register("test.reclaim", handler=f"{MODULE}.reclaim_collision_handler")
        job = _job(kind="test.reclaim")

        worker.tick()  # attempt A -- blocks
        job.refresh_from_db()
        token_a = job.claim_token
        InferenceJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=1)
        )
        worker.tick()  # attempt B reclaims -- also blocks
        job.refresh_from_db()
        token_b = job.claim_token
        assert token_b != token_a
        return job, token_a, token_b

    def test_drain_with_a_stale_and_b_live_requeues_only_under_bs_token(
        self, worker, monkeypatch,
    ):
        """Reviewer's TestProbe3Drain shape: both A and B are still
        blocked when the grace period elapses. Only B's own row is
        requeued (its token still matches); A's late writes, now
        token-conditional against a row that has moved on, are no-ops."""
        job, token_a, token_b = self._two_live_attempts(worker, monkeypatch)
        future_a = worker._futures[(job.pk, token_a)]
        future_b = worker._futures[(job.pk, token_b)]

        requeued = worker._drain_inflight(0.2)
        assert requeued == [job.pk]
        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.claim_token is None
        assert job.attempts == 1  # drain does not bump

        assert not future_a.done()
        assert not future_b.done()

        # Every write A can still make is token-conditional on token_a and
        # now matches zero rows.
        ctx_a = worker._build_job_context(
            {"id": job.pk, "claim_token": token_a, "attempts": 0, "checkpoint": None}
        )
        ctx_a.report_progress(1, 2, unit="u")
        ctx_a.checkpoint({"offset": 99})
        job.refresh_from_db()
        assert job.progress is None
        assert job.checkpoint is None

        _reclaim_attempt_a_release.set()
        _reclaim_attempt_b_release.set()
        concurrent.futures.wait([future_a, future_b], timeout=5)
        job.refresh_from_db()
        # Both attempts' terminal writebacks are ALSO token-conditional --
        # B's own (token_b) now matches zero rows too, since the drain
        # already cleared `claim_token` to NULL. The row stays exactly as
        # the drain's requeue left it.
        assert job.state == QUEUED
        assert job.result is None
        assert job.claim_token is None

    def test_shutdown_takes_the_bounded_exit_path_not_wait_true(
        self, worker, monkeypatch,
    ):
        """Reviewer's TestProbe3Drain shape (the second half): A is stale
        and still genuinely running past the grace period while B has
        already finished. Before the fix, A was untracked in
        `self._futures` entirely (overwritten by B's `_launch`), so
        `_drain_inflight` never waited on it, `_shutdown` saw nothing
        abandoned, and took the `executor.shutdown(wait=True)` branch --
        which then blocks on A's untracked thread with no bound at all.
        Asserted here by patching `os._exit` (so the test process itself
        survives) and a short grace period, then confirming `_shutdown`
        returns promptly (it does not block on A) via the patched
        `os._exit` having been called -- NOT via `executor.shutdown
        (wait=True)`, which is what would hang."""
        monkeypatch.setattr(worker_module, "SHUTDOWN_GRACE_SECONDS", 0.2)
        job, token_a, token_b = self._two_live_attempts(worker, monkeypatch)
        future_a = worker._futures[(job.pk, token_a)]
        future_b = worker._futures[(job.pk, token_b)]

        # B finishes; A remains blocked (stale, superseded, unsignaled).
        _reclaim_attempt_b_release.set()
        concurrent.futures.wait([future_b], timeout=5)
        assert future_b.done()
        assert not future_a.done()

        exit_calls: list[int] = []
        monkeypatch.setattr(worker_module.os, "_exit", exit_calls.append)

        shutdown_returned = threading.Event()

        def _run_shutdown():
            worker._shutdown(crashed=False)
            shutdown_returned.set()

        t = threading.Thread(target=_run_shutdown, daemon=True)
        t.start()
        # Bounded by the (patched, short) grace period -- must NOT hang
        # waiting for A the way `executor.shutdown(wait=True)` would.
        assert shutdown_returned.wait(5), (
            "worker._shutdown blocked -- it took the wait=True path and is "
            "joining A's still-running, untracked thread"
        )
        t.join(timeout=5)

        # The bounded os._exit path was chosen, not the clean wait=True one.
        assert exit_calls == [0]  # crashed=False -> exit code 0

        _reclaim_attempt_a_release.set()
        concurrent.futures.wait([future_a], timeout=5)


# --- opportunistic footprint measurement -------------------------------------


@pytest.mark.django_db
class TestMeasurement:
    def test_measured_footprint_is_recorded_via_the_new_bindings_helper(self, register_engine):
        """The `FakeEngine`'s own residency map is keyed by the TAGGED
        form (`"m:latest"`) -- exactly how a real engine's `/api/ps`-style
        report names a loaded model -- while the job's ref and the
        `ModelConnection` row both carry the bare form (`"m"`). This can
        only pass if `_measure_and_record` normalizes `model_id` through
        `norm_tag()` before calling `loaded_footprint` (M4, review finding,
        T4); a fixture keyed identically to the ref (the pre-review shape)
        could never have caught a missing normalization."""
        connection = ModelConnection.objects.create(
            name="conn", engine="fakeengine", endpoint="http://fake:1", model_id="m",
        )
        register_engine(FakeEngine("fakeengine", footprints={"m:latest": 3 * GB}))
        worker = Worker(worker_id="w")
        try:
            worker._measure_and_record([_ref(engine="fakeengine", endpoint="http://fake:1", model_id="m")])
        finally:
            worker._executor.shutdown(wait=False)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes == 3 * GB
        assert connection.measured_footprint_at is not None

    def test_record_measured_footprint_receives_the_raw_untagged_model_id(self, register_engine, monkeypatch):
        """The opposite half of M4: `record_measured_footprint` itself
        must be called with the RAW `model_id` (`"m"`), never the
        tag-normalized form (`"m:latest"`) -- it matches `ModelConnection.
        model_id` EXACTLY, mirroring `footprint_for`'s own exact match
        rule, so normalizing here would make it miss the very row it is
        meant to update."""
        recorded = []
        monkeypatch.setattr(
            worker_module, "record_measured_footprint",
            lambda engine, endpoint, model_id, size: recorded.append((engine, endpoint, model_id, size)),
        )
        register_engine(FakeEngine("fakeengine", footprints={"m:latest": 3 * GB}))
        worker = Worker(worker_id="w")
        try:
            worker._measure_and_record([_ref(engine="fakeengine", endpoint="http://fake:1", model_id="m")])
        finally:
            worker._executor.shutdown(wait=False)

        assert recorded == [("fakeengine", "http://fake:1", "m", 3 * GB)]

    def test_no_measurement_reported_is_not_recorded(self, register_engine):
        connection = ModelConnection.objects.create(
            name="conn", engine="fakeengine", endpoint="http://fake:1", model_id="m",
        )
        register_engine(FakeEngine("fakeengine", footprints={}))
        worker = Worker(worker_id="w")
        try:
            worker._measure_and_record([_ref(engine="fakeengine", endpoint="http://fake:1", model_id="m")])
        finally:
            worker._executor.shutdown(wait=False)

        connection.refresh_from_db()
        assert connection.measured_footprint_bytes is None

    def test_engine_without_loaded_footprint_does_not_crash(self, register_engine):
        register_engine(FakeEngineNoOptionalMethods("fakeengine_bare"))
        worker = Worker(worker_id="w")
        try:
            worker._measure_and_record([_ref(engine="fakeengine_bare", endpoint="http://fake:1", model_id="m")])
        finally:
            worker._executor.shutdown(wait=False)
        # no exception -- that is the whole assertion


# --- pre-launch eviction ------------------------------------------------------


@pytest.mark.django_db
class TestEviction:
    def test_unneeded_resident_model_is_unloaded_when_over_budget(self, register_engine):
        installed = [
            InstalledModel(model_id="needed-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-model", loaded=True, loaded_size=9 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        _set_budget(memory_budget_bytes=5 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        assert ("http://fake:1", "unneeded-model") in engine.unload_calls
        assert ("http://fake:1", "needed-model") not in engine.unload_calls

    def test_no_eviction_when_within_budget(self, register_engine):
        installed = [
            InstalledModel(model_id="needed-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-model", loaded=True, loaded_size=1 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        _set_budget(memory_budget_bytes=100 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        assert engine.unload_calls == []

    def test_no_budget_set_skips_eviction_entirely(self, register_engine):
        installed = [InstalledModel(model_id="unneeded-model", loaded=True, loaded_size=9 * GB)]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        _set_budget(memory_budget_bytes=None)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=None)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        assert engine.unload_calls == []

    def test_engine_without_unload_does_not_crash(self, register_engine):
        installed = [InstalledModel(model_id="unneeded-model", loaded=True, loaded_size=9 * GB)]
        register_engine(FakeEngineNoOptionalMethods("fakeengine_bare", installed=installed))
        _set_budget(memory_budget_bytes=1 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine_bare", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])  # must not raise
        finally:
            worker._executor.shutdown(wait=False)

    def test_exclusive_admitted_job_evicts_everything_else_at_its_endpoint(self, register_engine):
        installed = [
            InstalledModel(model_id="other-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="exclusive-model", loaded=True, loaded_size=1 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        # Huge budget -- NOT over budget -- proves the exclusive rule
        # evicts unconditionally, independent of the budget arithmetic.
        _set_budget(memory_budget_bytes=1000 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="exclusive-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )
        claimed = [
            {
                "id": 999, "kind": "test.echo", "payload": {},
                "model_refs": [_ref(engine="fakeengine", endpoint="http://fake:1", model_id="exclusive-model", footprint_bytes=1 * GB)],
                "claim_token": uuid.uuid4(), "exclusive": True,
            }
        ]

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan(claimed)
        finally:
            worker._executor.shutdown(wait=False)

        assert ("http://fake:1", "other-model") in engine.unload_calls
        assert ("http://fake:1", "exclusive-model") not in engine.unload_calls

    def test_exclusive_eviction_is_uncapped_even_past_max_unloads_per_tick(self, register_engine):
        """NEW MAJOR (review finding, T4 round 3): the pre-fix code
        checked `MAX_UNLOADS_PER_TICK` BEFORE the exclusivity branch, so
        the shared budget-driven cap applied to exclusive-job eviction
        too. Reviewer's trace: an exclusive job is admitted with several
        unneeded models still resident at its endpoint; the cap stops
        eviction partway through, the job launches anyway with unneeded
        models still resident, and it can NEVER recover -- `tick()` only
        calls `_evict_to_match_plan` on a tick that admits something, and
        scheduler rule 3 (models.queue.scheduler) blocks every other
        admission for as long as the exclusive job runs, so there is no
        later tick that would ever revisit the question.

        With more than `MAX_UNLOADS_PER_TICK` unneeded models resident at
        an exclusive job's own endpoint, ALL of them must be unloaded in
        this one pass -- exclusive-endpoint eviction has no cap at all."""
        installed = [
            InstalledModel(model_id="exclusive-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-a", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-b", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-c", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-d", loaded=True, loaded_size=1 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        _set_budget(memory_budget_bytes=1000 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="exclusive-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )
        claimed = [
            {
                "id": 999, "kind": "test.echo", "payload": {},
                "model_refs": [_ref(engine="fakeengine", endpoint="http://fake:1", model_id="exclusive-model", footprint_bytes=1 * GB)],
                "claim_token": uuid.uuid4(), "exclusive": True,
            }
        ]
        assert len(installed) - 1 > worker_module.MAX_UNLOADS_PER_TICK  # the whole point of this test

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan(claimed)
        finally:
            worker._executor.shutdown(wait=False)

        for model_id in ("unneeded-a", "unneeded-b", "unneeded-c", "unneeded-d"):
            assert ("http://fake:1", model_id) in engine.unload_calls
        assert ("http://fake:1", "exclusive-model") not in engine.unload_calls

    def test_unload_attempts_capped_per_tick(self, register_engine):
        """M3 (review finding, T4): with more unneeded resident models
        than `MAX_UNLOADS_PER_TICK`, this function must never issue more
        than the cap's worth of `unload()` calls in one pass -- bounding
        this function's own worst-case wall-clock time so it cannot, by
        itself, starve this worker's heartbeat long enough for another
        worker's orphan sweep to reclaim jobs this one still legitimately
        holds (see `STALE_AFTER_SECONDS`'s comment). Whatever eviction the
        cap leaves undone is simply picked up on a later tick."""
        installed = [
            InstalledModel(model_id="needed-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-a", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-b", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-c", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-d", loaded=True, loaded_size=1 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        # Tight budget -- every unneeded model is a legitimate eviction
        # candidate on budget grounds alone, so the cap (not the budget
        # check) is what limits how many actually get unloaded this tick.
        _set_budget(memory_budget_bytes=1 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        assert len(engine.unload_calls) == worker_module.MAX_UNLOADS_PER_TICK
        assert ("http://fake:1", "needed-model") not in engine.unload_calls

    def test_unload_returning_false_is_logged_and_loop_proceeds(self, register_engine):
        installed = [
            InstalledModel(model_id="unneeded-a", loaded=True, loaded_size=9 * GB),
            InstalledModel(model_id="unneeded-b", loaded=True, loaded_size=9 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        engine.unload_returns = False
        _set_budget(memory_budget_bytes=1 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])  # must not raise even though unload() always refuses
        finally:
            worker._executor.shutdown(wait=False)

        # Both attempts still happen (capped at MAX_UNLOADS_PER_TICK, not
        # short-circuited by the first refusal) -- `unload()` returning
        # `False` is logged and the loop proceeds to the next candidate.
        assert len(engine.unload_calls) == worker_module.MAX_UNLOADS_PER_TICK

    def test_trailing_slash_endpoint_normalized_consistently(self, register_engine):
        """A running job's ref endpoint carrying a trailing slash must
        still be recognized as the SAME endpoint as the bare form
        `list_installed`/`unload` are called with -- `needed_keys`,
        `endpoints`, and the actual engine calls all key off
        `norm_endpoint()` consistently.

        MINOR A (review finding, T4 round 3): the budget must actually be
        TIGHT (mirroring `test_unneeded_resident_model_is_unloaded_when_
        over_budget`) so eviction genuinely runs -- the previous version of
        this test used a 100 GB budget that was never exceeded, so it
        passed identically whether or not `norm_endpoint()` was ever
        applied to `needed_keys` at all (mutation-proof check: removing
        the normalization there still made it pass, since no unload was
        ever attempted in the first place)."""
        installed = [
            InstalledModel(model_id="needed-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-model", loaded=True, loaded_size=9 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        _set_budget(memory_budget_bytes=5 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1/", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        # Eviction genuinely runs (over budget: 1 + 9 > 5) -- "needed-model"
        # is correctly recognized as needed despite the trailing-slash vs.
        # bare endpoint mismatch and is never evicted, while the actually
        # unneeded model is.
        assert ("http://fake:1", "unneeded-model") in engine.unload_calls
        assert ("http://fake:1", "needed-model") not in engine.unload_calls

    def test_heartbeat_advances_during_uncapped_exclusive_eviction_pass(self, register_engine, monkeypatch):
        """Final review round, T4: `tick()` now registers the claimed
        batch's tokens in `self._active_tokens` BEFORE calling
        `_evict_to_match_plan` at all, and pass 1's own model loop calls
        `self._maybe_heartbeat()` after every unload attempt -- together
        these are what make an UNCAPPED exclusive-endpoint eviction pass
        safe at any length (previously the pass's own docstring justified
        this with "the row was freshly stamped moments before", which was
        never actually protection on its own for a pass that could run
        arbitrarily long).

        Proven directly here: with the heartbeat throttle knocked down to
        0 (so every call genuinely writes, not just the first) and several
        unneeded models resident at the exclusive job's endpoint, a hook
        on `FakeEngine.unload` captures the job's `heartbeat_at` after each
        unload call -- it must be non-null and monotonically advancing,
        proving the heartbeat is refreshed DURING the pass, not merely
        once before or after it."""
        monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0)

        installed = [
            InstalledModel(model_id="exclusive-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-a", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-b", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-c", loaded=True, loaded_size=1 * GB),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        _set_budget(memory_budget_bytes=1000 * GB, max_concurrent_jobs=4)
        job = _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="exclusive-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )
        # Deliberately stale, so any write during the pass is unmistakable.
        InferenceJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=1000)
        )
        claimed = [
            {
                "id": job.pk, "kind": "test.echo", "payload": {},
                "model_refs": [_ref(engine="fakeengine", endpoint="http://fake:1", model_id="exclusive-model", footprint_bytes=1 * GB)],
                "claim_token": job.claim_token, "exclusive": True,
            }
        ]

        worker = Worker(worker_id="w2")
        # tick() registers this itself, in production, before ever calling
        # _evict_to_match_plan -- reproduced here since this test calls
        # the eviction function directly, bypassing tick().
        worker._active_tokens[job.pk] = job.claim_token

        observed_heartbeats: list = []

        def record_heartbeat(endpoint, model_id):
            job.refresh_from_db()
            observed_heartbeats.append(job.heartbeat_at)

        engine.on_unload = record_heartbeat

        try:
            worker._evict_to_match_plan(claimed)
        finally:
            worker._executor.shutdown(wait=False)

        assert len(observed_heartbeats) == 3  # one per unneeded model unloaded
        assert all(h is not None for h in observed_heartbeats)
        assert observed_heartbeats == sorted(observed_heartbeats)  # monotonically advancing

        # The hook fires INSIDE unload(), before THAT SAME iteration's own
        # trailing `self._maybe_heartbeat()` call -- so the first
        # observation reflects the stale seed (no write in this pass has
        # landed yet), while every observation AFTER the first reflects a
        # write already made during a PRIOR iteration. That is exactly the
        # property under test: the heartbeat advances DURING the pass
        # (visible well before the pass finishes), not only once at the
        # very end.
        first, *rest = observed_heartbeats
        assert first < timezone.now() - timedelta(seconds=500)  # still the stale seed
        fresh_cutoff = timezone.now() - timedelta(seconds=30)
        assert all(h > fresh_cutoff for h in rest)

        # And the FINAL write (after the last unload's own trailing
        # heartbeat call, which the hook never observes) has also landed.
        job.refresh_from_db()
        assert job.heartbeat_at > fresh_cutoff

    def test_no_running_job_at_all_is_an_early_return(self, register_engine):
        """PHASE 1's second early return. `claim_and_admit` persists this
        tick's admissions as `running` before this is called, so an empty
        `running_refs` means there is genuinely nothing to plan against
        -- the engine must not be probed at all. Pinned before the split
        because the return moves into a helper that answers `None`."""
        # a budget IS set, and an engine IS registered whose list_installed
        # records its calls; no InferenceJob is in RUNNING state.
        installed = [InstalledModel(model_id="unneeded-model", loaded=True, loaded_size=9 * GB)]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        list_installed_calls: list = []
        original_list_installed = engine.list_installed

        def recording_list_installed(endpoint):
            list_installed_calls.append(endpoint)
            return original_list_installed(endpoint)

        engine.list_installed = recording_list_installed
        _set_budget(memory_budget_bytes=1 * GB, max_concurrent_jobs=4)

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        # assert the recorded list_installed calls == []
        assert list_installed_calls == []
        assert engine.unload_calls == []

    def test_an_engine_without_list_installed_is_warned_once_and_skipped(self, register_engine, caplog):
        """PHASE 2's OTHER degradation. `test_engine_without_unload_does_
        not_crash` covers the `unload` half; this covers the probe half --
        an engine lacking `list_installed` is logged once per
        engine+method (`_warn_missing_method_once`) and contributes no
        endpoint to `installed_by_endpoint`, so neither pass can touch
        it. Two calls, one warning."""
        engine = register_engine(FakeEngineNoListInstalled("fakeengine_no_list_installed"))
        _set_budget(memory_budget_bytes=1 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine_no_list_installed", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            with caplog.at_level(logging.INFO):
                worker._evict_to_match_plan([])
                worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        warnings = [
            record for record in caplog.records
            if "fakeengine_no_list_installed" in record.getMessage()
            and "list_installed" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert engine.unload_calls == []

    def test_a_list_installed_that_raises_skips_that_endpoint_and_never_propagates(self, register_engine):
        """PHASE 2's `except Exception` -- "eviction must never block a
        launch". The endpoint is absent from `installed_by_endpoint`,
        every OTHER endpoint is still probed and still evicted from, and
        `_evict_to_match_plan` returns normally."""
        raising_engine = register_engine(FakeEngineListInstalledRaises("raising_engine"))
        installed = [
            InstalledModel(model_id="needed-model-b", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unneeded-model-b", loaded=True, loaded_size=9 * GB),
        ]
        healthy_engine = register_engine(FakeEngine("healthy_engine", installed=installed))
        _set_budget(memory_budget_bytes=5 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[
                _ref(engine="raising_engine", endpoint="http://fake:1", model_id="needed-model-a", footprint_bytes=1 * GB),
                _ref(engine="healthy_engine", endpoint="http://fake:2", model_id="needed-model-b", footprint_bytes=1 * GB),
            ],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])  # must not raise
        finally:
            worker._executor.shutdown(wait=False)

        assert raising_engine.unload_calls == []
        assert ("http://fake:2", "unneeded-model-b") in healthy_engine.unload_calls
        assert ("http://fake:2", "needed-model-b") not in healthy_engine.unload_calls

    def test_a_resident_model_with_no_reported_size_counts_as_zero_bytes(self, register_engine):
        """PHASE 2's documented UNDER-count, argued for in a long inline
        comment: `list_installed` reporting a loaded model with no
        `loaded_size` contributes 0 to `actual_resident_bytes`, so the
        budget verdict can come out UNDER where a real accounting would
        be over. Deliberate, not a bug -- the scheduler's rule 2b already
        makes an unknown-footprint RUNNING job run alone. Pinned here so
        the split cannot quietly 'fix' it."""
        installed = [
            InstalledModel(model_id="needed-model", loaded=True, loaded_size=1 * GB),
            InstalledModel(model_id="unsized-model", loaded=True, loaded_size=None),
        ]
        engine = register_engine(FakeEngine("fakeengine", installed=installed))
        # A budget that would be exceeded if the unsized model's real
        # (unknown, but certainly nonzero) footprint counted for anything
        # at all -- the verdict must still come out UNDER, because a
        # model with no reported size contributes exactly 0 bytes.
        _set_budget(memory_budget_bytes=2 * GB, max_concurrent_jobs=4)
        _job(
            state=RUNNING,
            model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1", model_id="needed-model", footprint_bytes=1 * GB)],
            claimed_by="w", claim_token=uuid.uuid4(),
        )

        worker = Worker(worker_id="w2")
        try:
            worker._evict_to_match_plan([])
        finally:
            worker._executor.shutdown(wait=False)

        # Budget-driven eviction never even triggers: only the sized,
        # needed model (1 GB) counts toward actual_resident_bytes, which
        # is comfortably under the 2 GB budget -- so "unsized-model", a
        # genuine (not needed) eviction candidate, is never touched, not
        # because it was deemed needed but because it was never weighed
        # at all.
        assert engine.unload_calls == []

# --- S6: one settings read per tick ------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestTheSingleSettingsReadPerTick:
    """S6 (settings-backend audit, closed by the final whole-delta
    review's I2 ruling): ONE `JobSettings` read per tick, threaded --
    not one in `claim_and_admit` and a second in `_evict_to_match_plan`
    moments later, off the same 0.5s loop.

    THE PATTERN IS `identity.request.settings_row_for`'s, copied rather
    than invented: one read at the top of the request (here, the top of
    the tick), threaded into every consumer through a `settings_row=`
    keyword whose absence falls back to the consumer's own `get_solo()`.
    The fallback is what keeps `claim_and_admit`/`_evict_to_match_plan`
    callable on their own -- which every other test in this file and in
    `test_claim.py` does -- while the loop that calls both pays for one
    read instead of two.

    COUNTED AGAINST `jobs_jobsettings` SPECIFICALLY rather than the
    tick's total query count, the same way
    `identity/tests/test_middleware.py::TestTheSingleRowRead` counts
    `identity_identitysettings`: this pin must not drift every time the
    claim transaction's own unrelated queries change.

    `Worker(...)` is constructed OUTSIDE the capture: the constructor
    reads the row once itself, deliberately and permanently (thread-pool
    size, which cannot be resized mid-run -- see `__init__`'s own
    comment), and that read is startup's, not the tick's.
    """

    @staticmethod
    def _settings_reads(context) -> int:
        return sum(
            1 for q in context.captured_queries
            if "jobs_jobsettings" in q["sql"]
        )

    def test_a_tick_that_admits_a_job_reads_the_row_once(self, register_engine):
        """The tick that pays BOTH reads in the un-threaded version: a
        job is admitted (so `_evict_to_match_plan` runs at all) and a
        budget is set (so it gets past its own first line). Two reads
        before S6, one after."""
        _register("test.echo")
        register_engine(FakeEngine("fakeengine"))
        _set_budget(memory_budget_bytes=100 * GB, max_concurrent_jobs=4)
        _job(model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1",
                              model_id="m", footprint_bytes=1 * GB)])

        worker = Worker(worker_id="one-read")
        try:
            with CaptureQueriesContext(connection) as ctx:
                worker.tick()
        finally:
            worker._executor.shutdown(wait=True)

        assert self._settings_reads(ctx) == 1

    def test_the_pin_is_not_vacuous_a_second_read_inside_the_tick_is_red(
        self, register_engine, monkeypatch,
    ):
        """NON-VACUITY, by mutation rather than by assertion (no repo file
        is touched): put ONE extra `get_solo()` back inside the tick --
        exactly the read S6 removed -- and the count above goes to two.
        Without this, a pin reading `== 1` would stay green if the
        threading were quietly reverted to a single un-threaded consumer,
        or if `_evict_to_match_plan` stopped being reached at all."""
        _register("test.echo")
        register_engine(FakeEngine("fakeengine"))
        _set_budget(memory_budget_bytes=100 * GB, max_concurrent_jobs=4)
        _job(model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1",
                              model_id="m", footprint_bytes=1 * GB)])

        real_evict = Worker._evict_to_match_plan

        def evict_with_its_own_read(self, claimed, *, settings_row=None):
            JobSettings.get_solo()  # the read the threading removed
            return real_evict(self, claimed, settings_row=settings_row)

        monkeypatch.setattr(Worker, "_evict_to_match_plan", evict_with_its_own_read)

        worker = Worker(worker_id="two-reads")
        try:
            with CaptureQueriesContext(connection) as ctx:
                worker.tick()
        finally:
            worker._executor.shutdown(wait=True)

        assert self._settings_reads(ctx) == 2

    def test_each_consumer_still_reads_for_itself_when_called_alone(self, register_engine):
        """The fallback half of the pattern, and the reason every other
        test in this file and in `test_claim.py` still works: called
        WITHOUT a `settings_row`, each consumer fetches its own. A
        threading change that made the keyword mandatory would be a
        behaviour change to two public entry points, not a query-count
        optimisation."""
        _register("test.echo")
        register_engine(FakeEngine("fakeengine"))
        _set_budget(memory_budget_bytes=100 * GB, max_concurrent_jobs=4)
        _job(model_refs=[_ref(engine="fakeengine", endpoint="http://fake:1",
                              model_id="m", footprint_bytes=1 * GB)])

        with CaptureQueriesContext(connection) as claim_ctx:
            claimed = claim_and_admit("solo", stale_after_seconds=120)
        assert len(claimed) == 1
        assert self._settings_reads(claim_ctx) == 1

        worker = Worker(worker_id="solo-evict")
        try:
            with CaptureQueriesContext(connection) as evict_ctx:
                worker._evict_to_match_plan(claimed)
        finally:
            worker._executor.shutdown(wait=False)
        assert self._settings_reads(evict_ctx) == 1
