"""Unit tests for models/queue/backend.py -- the `INFERENCE_QUEUE_BACKEND`
module `models/contracts/queue.py` dispatches to.

`@pytest.mark.django_db` at class level (repo convention); no conftest.py.
Test job kinds are registered against the real core registry
(`models.contracts.jobkinds`) with the snapshot/restore fixture idiom
`models/registry/tests/test_jobkinds.py` uses, since `enqueue()` (both
this module's own and `models.contracts.queue`'s) validates against it.
Planner/handler/summarizer callables are plain module-level functions here,
addressed by dotted path -- the same shape any feature app's `jobs.py`
would use.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import OperationalError, ProgrammingError, connection

from models.queue import backend
from models.queue.models import CANCELLED, FAILED, QUEUED, RUNNING, SUCCEEDED, InferenceJob, JobSettings
from models.queue.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported
from models.contracts import jobkinds, queue
from models.contracts.jobkinds import JobKind, ModelRef, register_job_kind

MODULE = "models.queue.tests.test_backend"


# --- test job kinds' planner/handler/summarizer ----------------------------


def plan_no_models(payload):
    return ([], False)


def plan_one_model(payload):
    refs = [
        ModelRef(
            role="test.role",
            engine="ollama",
            endpoint="http://ollama.local:11434",
            model_id="test-model",
            connection_name="test conn",
        )
    ]
    return (refs, True)


def handle_noop(payload, models, ctx):  # pragma: no cover - not exercised this task
    return {}


def summarize_noop(payload):  # pragma: no cover - not exercised this task
    return "test job"


def summarize_prompt(payload):
    """Reads the payload, so a job's `summary` can be told apart from its
    kind's label."""
    return payload.get("prompt", "")


def summarize_boom(payload):
    raise RuntimeError("this summarizer is broken")


# --- on_terminal test hooks (T9.5, cancel_job's own invocation) -------------

_on_terminal_calls: list[tuple[dict, str]] = []


def hook_records_calls(payload, state):
    _on_terminal_calls.append((payload, state))


def hook_that_raises(payload, state):
    raise RuntimeError("on_terminal hook boom")


@pytest.fixture(autouse=True)
def _clear_on_terminal_calls():
    _on_terminal_calls.clear()
    yield
    _on_terminal_calls.clear()


reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")


def _register(
    key, *, planner=f"{MODULE}.plan_no_models", summarizer=f"{MODULE}.summarize_noop",
    default_priority=None, on_terminal=None,
):
    register_job_kind(
        JobKind(
            key=key,
            label=key,
            planner=planner,
            handler=f"{MODULE}.handle_noop",
            summarizer=summarizer,
            default_priority=default_priority,
            on_terminal=on_terminal,
        )
    )


# --- enqueue: model/exclusive serialization --------------------------------


@pytest.mark.django_db
class TestEnqueueSerialization:
    def test_creates_queued_row_returns_pk(self):
        _register("test.no_models")

        job_id = backend.enqueue("test.no_models", {"x": 1}, priority=5)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == QUEUED
        assert job.payload == {"x": 1}
        assert job.model_refs == []
        assert job.exclusive is False

    def test_planner_models_and_exclusive_are_stored(self):
        _register("test.one_model", planner=f"{MODULE}.plan_one_model")

        job_id = backend.enqueue("test.one_model", {}, priority=5)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.exclusive is True
        assert job.model_refs == [
            {
                "role": "test.role",
                "engine": "ollama",
                "endpoint": "http://ollama.local:11434",
                "model_id": "test-model",
                "connection_name": "test conn",
                "footprint_bytes": None,
                # RE-PINNED 2026-09-24: the snapshot carries
                # `ModelRef.synchronous` too, so the worker can tell an
                # endpoint this job's handler loads at from one a tool
                # may use. `True` here is the dataclass default, which is
                # also how every reader treats the key's ABSENCE.
                "synchronous": True,
            }
        ]


# --- priority chain ---------------------------------------------------------


@pytest.mark.django_db
class TestPriorityChain:
    def test_explicit_priority_wins(self):
        _register("test.prio", default_priority=42)

        job_id = backend.enqueue("test.prio", {}, priority=7)

        assert InferenceJob.objects.get(pk=job_id).priority == 7

    def test_kind_default_wins_over_global_default(self):
        _register("test.prio", default_priority=42)

        job_id = backend.enqueue("test.prio", {}, priority=None)

        assert InferenceJob.objects.get(pk=job_id).priority == 42

    def test_global_default_used_when_both_absent(self):
        _register("test.prio", default_priority=None)

        job_id = backend.enqueue("test.prio", {}, priority=None)

        assert InferenceJob.objects.get(pk=job_id).priority == JobSettings.get_solo().default_priority

    def test_explicit_priority_must_be_positive(self):
        _register("test.prio")

        with pytest.raises(ValueError):
            backend.enqueue("test.prio", {}, priority=0)

        with pytest.raises(ValueError):
            backend.enqueue("test.prio", {}, priority=-1)

    def test_kind_default_priority_must_be_positive(self):
        """A registered `JobKind` with a non-positive `default_priority` is
        a bug in that kind's own registration, not a value to silently
        carry through to the row -- it must fail loudly at enqueue time,
        same as a bad explicit `priority` argument does."""
        _register("test.prio", default_priority=0)

        with pytest.raises(ValueError, match="test.prio"):
            backend.enqueue("test.prio", {}, priority=None)


# --- prune -------------------------------------------------------------------


@pytest.mark.django_db
class TestPruneFinishedJobs:
    def test_prunes_oldest_terminal_beyond_retention_never_touches_active(self):
        settings = JobSettings.get_solo()
        settings.retention_limit = 3
        settings.save()

        terminal_states = [SUCCEEDED, FAILED, SUCCEEDED, CANCELLED, FAILED]
        terminal_ids = [
            InferenceJob.objects.create(kind="test.terminal", priority=100, state=state).pk
            for state in terminal_states
        ]
        queued_ids = [
            InferenceJob.objects.create(kind="test.queued", priority=100, state=QUEUED).pk
            for _ in range(2)
        ]
        running_id = InferenceJob.objects.create(
            kind="test.running", priority=100, state=RUNNING
        ).pk

        _register("test.prune")
        backend.enqueue("test.prune", {}, priority=100)

        remaining_terminal = set(
            InferenceJob.objects.filter(pk__in=terminal_ids).values_list("pk", flat=True)
        )
        # Newest 3 (highest pks) of the 5 terminal rows survive.
        assert remaining_terminal == set(terminal_ids[-3:])

        # Queued/running rows are untouched regardless of retention_limit.
        assert InferenceJob.objects.filter(pk__in=queued_ids).count() == 2
        assert InferenceJob.objects.filter(pk=running_id, state=RUNNING).exists()

    def test_retention_at_or_above_terminal_count_deletes_nothing(self):
        """The empty-slice/`cutoff_pk is None` path: when the terminal-row
        count doesn't exceed `retention_limit`, the `LIMIT 1 OFFSET limit`
        cutoff query comes back empty and nothing is deleted."""
        settings = JobSettings.get_solo()
        settings.retention_limit = 5
        settings.save()

        terminal_ids = [
            InferenceJob.objects.create(kind="test.terminal", priority=100, state=SUCCEEDED).pk
            for _ in range(5)
        ]

        _register("test.prune_noop")
        backend.enqueue("test.prune_noop", {}, priority=100)

        assert InferenceJob.objects.filter(pk__in=terminal_ids).count() == 5

    def test_enqueue_still_returns_pk_when_prune_raises(self):
        """The queued row is already committed by the time
        `_prune_finished_jobs` runs -- a prune-time `OperationalError`
        must not make `enqueue` raise (and so make a caller believe
        nothing was queued) when the job row plainly exists."""
        _register("test.prune_fails")

        with patch(
            "models.queue.backend._prune_finished_jobs",
            side_effect=OperationalError("could not connect"),
        ):
            job_id = backend.enqueue("test.prune_fails", {}, priority=100)

        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == QUEUED


# --- enqueue survives a prune-time database error (cross-session-reported --
# defect: the job row is created BEFORE `_prune_finished_jobs` runs, so a
# failure in that housekeeping call alone must never make `enqueue()` look
# like it failed to queue anything) -------------------------------------------


@pytest.mark.django_db
class TestEnqueueSurvivesPruneFailure:
    @pytest.mark.parametrize("exc_cls", [ProgrammingError, OperationalError])
    def test_enqueue_still_returns_the_pk_and_the_row_is_queued(self, exc_cls, caplog):
        _register("test.prune_fails")

        with patch(
            "models.queue.backend._prune_finished_jobs", side_effect=exc_cls("boom")
        ), caplog.at_level("WARNING"):
            job_id = backend.enqueue("test.prune_fails", {"x": 1}, priority=5)

        # The pk is real, and the row exists, queued -- a prune-time error
        # must never be reported as "nothing was queued".
        assert isinstance(job_id, int)
        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == QUEUED
        assert job.payload == {"x": 1}

        # The skipped prune is named in a warning, not swallowed silently.
        assert any(
            "skipped pruning" in record.message and str(job_id) in record.message
            for record in caplog.records
        )

    def test_a_genuine_prune_bug_still_raises(self):
        """Only `ProgrammingError`/`OperationalError` (the same two
        `@_guarded` itself translates) are forgiven -- any other exception
        out of `_prune_finished_jobs` is a real bug and must still surface,
        not be silently folded into "prune skipped, job queued"."""
        _register("test.prune_other_bug")

        with patch(
            "models.queue.backend._prune_finished_jobs", side_effect=ValueError("not a db error")
        ):
            with pytest.raises(ValueError, match="not a db error"):
                backend.enqueue("test.prune_other_bug", {}, priority=5)

    @pytest.mark.django_db(transaction=True)
    def test_survives_a_genuinely_broken_connection_not_a_mocked_one(self):
        """T8 review minor 5: every OTHER test in this class mocks
        `_prune_finished_jobs`'s own Python-level exception -- that proves
        the try/except's CONTROL FLOW is correct, but never touches a real
        database connection, so it can't prove the fix survives what
        actually happens on Postgres when a query inside a transaction
        fails: the CONNECTION's current transaction is poisoned, not just
        the Python call that raised. `jobs_jobsettings` (the table
        `JobSettings.get_solo()`, called INSIDE this function's own
        try/except, reads) is renamed away for real here -- a genuine
        `ProgrammingError` (relation does not exist), not a mock -- and
        renamed back in `finally` regardless of outcome.

        `transaction=True` (not the default per-test-wrapped-in-one-
        transaction pytest-django gives every other test in this file):
        this test needs the REAL autocommit-per-statement behavior
        `enqueue()` itself relies on in production (see this fix's own
        "no ambient atomic()" precondition, documented at the try/except
        in backend.py) -- running it inside pytest-django's own default
        wrapping transaction would have the poisoned-transaction problem
        this fix's precondition warns about happen to THIS TEST's own
        transaction, corrupting every assertion after the renamed-table
        query, not just the one under test.
        """
        _register("test.prune_real_db_error")

        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE jobs_jobsettings RENAME TO jobs_jobsettings_renamed_away")
        try:
            job_id = backend.enqueue("test.prune_real_db_error", {"x": 1}, priority=5)
        finally:
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE jobs_jobsettings_renamed_away RENAME TO jobs_jobsettings")

        # The pk is real and the row genuinely exists, queued -- proven
        # with a fresh query against a connection that just had a real
        # ProgrammingError on it, not an assumption.
        assert isinstance(job_id, int)
        job = InferenceJob.objects.get(pk=job_id)
        assert job.state == QUEUED
        assert job.payload == {"x": 1}


# --- get_job: round trip + position -----------------------------------------


@pytest.mark.django_db
class TestGetJob:
    def test_unknown_id_returns_none(self):
        assert backend.get_job(999999) is None

    @pytest.mark.parametrize("state", [QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED])
    def test_round_trips_each_state(self, state):
        job = InferenceJob.objects.create(
            kind="test.state",
            priority=50,
            state=state,
            payload={"q": "hi"},
            result={"a": 1} if state == SUCCEEDED else None,
            error="boom" if state == FAILED else "",
        )

        status = backend.get_job(job.pk)

        assert status.id == job.pk
        assert status.kind == "test.state"
        assert status.state == state
        assert status.priority == 50
        if state == QUEUED:
            assert status.position is not None
        else:
            assert status.position is None

    def test_position_follows_priority_then_pk(self):
        low_prio_first = InferenceJob.objects.create(kind="k", priority=10, state=QUEUED)
        low_prio_second = InferenceJob.objects.create(kind="k", priority=10, state=QUEUED)
        high_prio = InferenceJob.objects.create(kind="k", priority=1, state=QUEUED)

        # Order should be: high_prio (prio 1), low_prio_first, low_prio_second
        assert backend.get_job(high_prio.pk).position == 1
        assert backend.get_job(low_prio_first.pk).position == 2
        assert backend.get_job(low_prio_second.pk).position == 3

    def test_progress_passes_through_unchanged(self):
        """T3: `JobStatus.progress` is `InferenceJob.progress`, carried
        through verbatim -- no re-derivation, no reshaping."""
        job = InferenceJob.objects.create(
            kind="test.state", priority=50, state=RUNNING,
            progress={"done": 3, "total": 10, "unit": "items", "label": "chunks"},
        )

        status = backend.get_job(job.pk)

        assert status.progress == {"done": 3, "total": 10, "unit": "items", "label": "chunks"}

    def test_progress_is_none_when_never_reported(self):
        job = InferenceJob.objects.create(kind="test.state", priority=50, state=QUEUED)

        status = backend.get_job(job.pk)

        assert status.progress is None


@pytest.mark.django_db
class TestJobStatusSummary:
    """A single-job read must be able to NAME the job. The queue still
    carries no payload across this seam -- what crosses is the string the
    job kind's OWN registered summarizer produced, which is the same thing
    the Queue page has always rendered from `QueueRow.payload`."""

    def test_get_job_carries_the_kinds_own_summary(self):
        _register("test.summary", summarizer=f"{MODULE}.summarize_prompt")
        job = InferenceJob.objects.create(
            kind="test.summary", priority=50, state=QUEUED,
            payload={"prompt": "a lighthouse"},
        )

        assert backend.get_job(job.pk).summary == "a lighthouse"

    def test_an_unregistered_kind_degrades_to_its_key(self):
        job = InferenceJob.objects.create(
            kind="gone.kind", priority=50, state=QUEUED, payload={}
        )

        assert backend.get_job(job.pk).summary == "gone.kind"

    def test_a_raising_summarizer_degrades_to_the_kind_label(self):
        _register("test.broken", summarizer=f"{MODULE}.summarize_boom")
        job = InferenceJob.objects.create(
            kind="test.broken", priority=50, state=QUEUED, payload={}
        )

        # `_register` uses the key as the label, so this is the label path.
        assert backend.get_job(job.pk).summary == "test.broken"

    def test_a_malformed_payload_degrades_the_summary_without_raising(self):
        """Orchestrator-sanctioned addition (mid-task addendum): the
        per-row degradation guarantee `summarize_job` inherited from the
        old `_summarize` must hold at THIS call site too -- a malformed
        `payload` (here a list, breaking `summarize_prompt`'s `.get()`
        call) must degrade the summary, never take `get_job`/the poll path
        down with it. `payload` is a non-nullable `JSONField(default=dict)`
        (`models/queue/models.py`), so `[]` -- not `None` -- is the
        DB-legal way to store a JSON value that isn't a dict."""
        _register("test.malformed", summarizer=f"{MODULE}.summarize_prompt")
        job = InferenceJob.objects.create(
            kind="test.malformed", priority=50, state=QUEUED, payload=[],
        )

        status = backend.get_job(job.pk)

        assert status is not None
        assert status.summary == "test.malformed"


# --- queue_snapshot / QueueRow ------------------------------------------------


@pytest.mark.django_db
class TestQueueSnapshotProgress:
    """T3: `QueueRow.progress` is `InferenceJob.progress`, carried through
    unchanged by `_queue_row` -- `models/queue/tests/test_views.py`'s
    `TestProgressDisplay` proves the end-to-end rendering; this is the
    narrower, view-independent passthrough fact."""

    def test_running_row_progress_passes_through_unchanged(self):
        InferenceJob.objects.create(
            kind="test.state", priority=50, state=RUNNING,
            progress={"done": 3, "total": 10, "unit": "items", "label": "chunks"},
        )

        snapshot = backend.queue_snapshot()

        assert len(snapshot.running) == 1
        assert snapshot.running[0].progress == {
            "done": 3, "total": 10, "unit": "items", "label": "chunks",
        }

    def test_row_with_no_progress_reported_is_none(self):
        InferenceJob.objects.create(kind="test.state", priority=50, state=RUNNING)

        snapshot = backend.queue_snapshot()

        assert snapshot.running[0].progress is None


# --- cancel_job --------------------------------------------------------------


@pytest.mark.django_db
class TestCancelJob:
    def test_queued_job_is_cancelled(self):
        job = InferenceJob.objects.create(kind="k", priority=1, state=QUEUED)

        outcome = backend.cancel_job(job.pk)

        assert outcome == "cancelled"
        job.refresh_from_db()
        assert job.state == CANCELLED
        assert job.finished_at is not None

    def test_running_job_reports_already_running(self):
        job = InferenceJob.objects.create(kind="k", priority=1, state=RUNNING)

        assert backend.cancel_job(job.pk) == "already_running"
        job.refresh_from_db()
        assert job.state == RUNNING

    def test_terminal_job_reports_already_finished(self):
        job = InferenceJob.objects.create(kind="k", priority=1, state=SUCCEEDED)

        assert backend.cancel_job(job.pk) == "already_finished"

    def test_unknown_job_reports_unknown(self):
        assert backend.cancel_job(999999) == "unknown"


# --- cancel_job: on_terminal hook invocation (T9.5 audit §5) ----------------


@pytest.mark.django_db
class TestCancelJobOnTerminalHook:
    """`cancel_job`'s own responsibility for `JobKind.on_terminal` --
    `tools/rag/tests/test_jobs.py`'s `TestOnIngestTerminal`/
    `TestCancelRealRagIngestJob` cover the real `rag.ingest` hook end to
    end; these tests prove the generic invocation mechanism itself: any
    registered kind's hook is called, a broken one never breaks the
    "cancelled" outcome, and a kind with no hook at all is unaffected --
    all via `django_capture_on_commit_callbacks`, which fires a test's
    `transaction.on_commit` callbacks without needing a real DB commit
    (`cancel_job` schedules the hook that way "for symmetry" with
    `models.queue.claim._sweep_orphans`, even though it isn't normally
    called inside an ambient transaction of its own -- see that function's
    docstring)."""

    def test_hook_is_invoked_with_the_jobs_own_payload_and_cancelled_state(
        self, django_capture_on_commit_callbacks
    ):
        _register("test.with_hook", on_terminal=f"{MODULE}.hook_records_calls")
        job = InferenceJob.objects.create(
            kind="test.with_hook", priority=1, state=QUEUED, payload={"document_id": 42}
        )

        with django_capture_on_commit_callbacks(execute=True):
            outcome = backend.cancel_job(job.pk)

        assert outcome == "cancelled"
        assert _on_terminal_calls == [({"document_id": 42}, "cancelled")]

    def test_broken_hook_does_not_break_the_cancelled_outcome(self, caplog, django_capture_on_commit_callbacks):
        _register("test.with_broken_hook", on_terminal=f"{MODULE}.hook_that_raises")
        job = InferenceJob.objects.create(kind="test.with_broken_hook", priority=1, state=QUEUED)

        with caplog.at_level("ERROR"):
            with django_capture_on_commit_callbacks(execute=True):
                outcome = backend.cancel_job(job.pk)

        assert outcome == "cancelled"
        job.refresh_from_db()
        assert job.state == CANCELLED
        assert any("on_terminal hook" in record.message for record in caplog.records)

    def test_kind_without_on_terminal_is_unaffected(self, django_capture_on_commit_callbacks):
        _register("test.no_hook")  # on_terminal defaults to None

        job = InferenceJob.objects.create(kind="test.no_hook", priority=1, state=QUEUED)

        with django_capture_on_commit_callbacks(execute=True):
            outcome = backend.cancel_job(job.pk)

        assert outcome == "cancelled"
        assert _on_terminal_calls == []

    def test_unregistered_kind_is_unaffected(self, django_capture_on_commit_callbacks):
        """A row whose `kind` was never registered at all (or was
        deregistered since being enqueued) -- `invoke_on_terminal` logs and
        skips rather than raising `get_job_kind`'s own `ValueError`."""
        job = InferenceJob.objects.create(kind="totally.unregistered", priority=1, state=QUEUED)

        with django_capture_on_commit_callbacks(execute=True):
            outcome = backend.cancel_job(job.pk)

        assert outcome == "cancelled"

    def test_already_running_job_never_invokes_the_hook(self, django_capture_on_commit_callbacks):
        _register("test.with_hook", on_terminal=f"{MODULE}.hook_records_calls")
        job = InferenceJob.objects.create(kind="test.with_hook", priority=1, state=RUNNING)

        with django_capture_on_commit_callbacks(execute=True):
            outcome = backend.cancel_job(job.pk)

        assert outcome == "already_running"
        assert _on_terminal_calls == []


# --- QueueUnavailable --------------------------------------------------------


@pytest.mark.django_db
class TestQueueUnavailable:
    def test_backend_queue_unavailable_is_the_core_queue_seams_class(self):
        """T6: `QueueUnavailable` is now DEFINED in `models.contracts.queue`
        (not `models.queue.backend`) precisely so a `tools/*` caller can
        catch it without ever importing `models.queue` -- `backend` imports
        (never redefines) it, so `backend.QueueUnavailable` must be the
        EXACT SAME class object as `models.contracts.queue.QueueUnavailable`,
        not a lookalike a `raises`/`isinstance` check would still pass by
        accident."""
        assert backend.QueueUnavailable is queue.QueueUnavailable

    def test_enqueue_raises_typed_exception_on_table_missing(self):
        _register("test.unavailable")

        with patch("models.queue.backend.InferenceJob.objects") as mock_objects:
            mock_objects.create.side_effect = ProgrammingError(
                'relation "jobs_inferencejob" does not exist'
            )

            with pytest.raises(backend.QueueUnavailable):
                backend.enqueue("test.unavailable", {})

    def test_get_job_raises_typed_exception_on_operational_error(self):
        with patch("models.queue.backend.InferenceJob.objects") as mock_objects:
            mock_objects.get.side_effect = OperationalError("could not connect")

            with pytest.raises(backend.QueueUnavailable):
                backend.get_job(1)


# --- dispatch integration via models.contracts.queue --------------------------


@pytest.mark.django_db
class TestDispatchIntegration:
    def test_core_queue_seam_reaches_this_backend(self):
        """`models.contracts.queue.enqueue` with the real, settings-configured
        default backend (`models.queue.backend`) must reach this module and
        create a row -- proves the dotted-path wiring end to end, not just
        that this module works when called directly."""
        _register("test.dispatch")

        job_id = queue.enqueue("test.dispatch", {"y": 2})

        job = InferenceJob.objects.get(pk=job_id)
        assert job.kind == "test.dispatch"
        assert job.payload == {"y": 2}
        assert job.state == QUEUED
