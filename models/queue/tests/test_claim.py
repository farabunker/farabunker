"""Unit tests for models/queue/claim.py -- the claim transaction (T4).

`InferenceJob` rows are created directly here (not through `models.queue.
backend.enqueue`) -- most of this module's tests need nothing from the
job-kind registry beyond what app startup already registered, so building
through `backend`/`models.contracts.jobkinds` would only add unrelated
setup. `claim.py` itself DOES read the registry now, in two places
(`_kind_stale_thresholds` for the orphan sweep's per-kind staleness,
`invoke_on_terminal` for the permanent-fail hook) -- `TestOrphanSweep`'s
`rag.ingest` test exercises both against the REAL app-registered kind
(never cleared: see `reset_registry` below), and `TestKindAwareStaleness`
registers its own fake kinds into a registry it explicitly clears first.
`reset_registry` (`models.contracts.testing.registry_reset_fixture`) is
bound `autouse=False` here, unlike its sibling modules -- only a test that
names it as a parameter gets the clear/restore; every other test in this
file sees the job-kind registry exactly as app startup left it, `rag.
ingest`'s real `on_terminal` hook included. `@pytest.mark.django_db`
(regular, not `transaction=True`) everywhere except `TestConcurrency`,
which needs real, separately-committed transactions across threads --
Django's default per-test transaction wrapper would make a second
thread's connection block forever waiting on the first thread's own
uncommitted work, which is exactly what `transaction=True`
(`TransactionTestCase` semantics) avoids.
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.db import transaction
from django.utils import timezone

from models.registry.models import ModelConnection
from models.queue.claim import CANDIDATE_WINDOW, _sweep_orphans, claim_and_admit
from models.queue.models import QUEUED, RUNNING, InferenceJob, JobSettings
from models.queue.tests._helpers import registry_reset_fixture  # noqa: F401 -- re-exported
from models.contracts import jobkinds
from models.contracts.jobkinds import JobKind, register_job_kind

STALE_AFTER = 120  # arbitrary for tests that don't exercise the sweep itself

MODULE = "models.queue.tests.test_claim"

reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS", autouse=False)


# --- test job kinds' planner/handler/summarizer -----------------------------


def plan_no_models(payload):
    return ([], False)


def echo_handler(payload, models, ctx):
    return {"echo": payload}


def summarize_noop(payload):
    return "test job"


def _ref(
    *,
    engine: str = "ollama",
    endpoint: str = "http://ollama.local:11434",
    model_id: str = "llama3.1:8b",
    role: str = "test.role",
    connection_name: str = "",
    footprint_bytes: int | None = None,
) -> dict:
    return {
        "role": role,
        "engine": engine,
        "endpoint": endpoint,
        "model_id": model_id,
        "connection_name": connection_name,
        "footprint_bytes": footprint_bytes,
    }


def _job(*, kind="test.kind", priority=100, state=QUEUED, model_refs=None, exclusive=False, **extra) -> InferenceJob:
    return InferenceJob.objects.create(
        kind=kind,
        priority=priority,
        state=state,
        model_refs=model_refs if model_refs is not None else [],
        exclusive=exclusive,
        **extra,
    )


def _set_budget(*, memory_budget_bytes=None, max_concurrent_jobs=4):
    settings = JobSettings.get_solo()
    settings.memory_budget_bytes = memory_budget_bytes
    settings.max_concurrent_jobs = max_concurrent_jobs
    settings.save()


def _running_job(*, kind: str, heartbeat_age_seconds: int) -> InferenceJob:
    """One RUNNING row whose heartbeat is `heartbeat_age_seconds` old --
    the only shape the orphan sweep's tests care about."""
    row = _job(kind=kind, state=RUNNING)
    InferenceJob.objects.filter(pk=row.pk).update(
        claim_token=uuid.uuid4(),
        heartbeat_at=timezone.now() - timedelta(seconds=heartbeat_age_seconds),
    )
    row.refresh_from_db()
    return row


@pytest.mark.django_db
class TestClaimAndAdmitBasics:
    def test_admitted_row_marked_running_with_fresh_claim_token(self):
        _set_budget(memory_budget_bytes=None, max_concurrent_jobs=4)
        job = _job()

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert [d["id"] for d in claimed] == [job.pk]
        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.claimed_by == "worker-a"
        assert job.claim_token is not None
        assert job.started_at is not None
        assert job.heartbeat_at is not None
        assert job.attempts == 0
        assert claimed[0]["claim_token"] == job.claim_token

    def test_no_candidates_returns_empty_list(self):
        assert claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER) == []

    def test_descriptor_carries_kind_payload_and_exclusive(self):
        _set_budget(memory_budget_bytes=None)
        job = _job(exclusive=True)
        job.payload = {"q": "hi"}
        job.save()

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert claimed[0]["kind"] == "test.kind"
        assert claimed[0]["payload"] == {"q": "hi"}
        assert claimed[0]["exclusive"] is True


@pytest.mark.django_db
class TestFootprintStamping:
    def test_footprint_resolved_and_stamped_at_claim_time(self):
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", measured_footprint_bytes=5 * 1024**3,
        )
        _set_budget(memory_budget_bytes=100 * 1024**3, max_concurrent_jobs=4)
        job = _job(model_refs=[_ref(footprint_bytes=None)])

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert claimed[0]["model_refs"][0]["footprint_bytes"] == 5 * 1024**3
        job.refresh_from_db()
        assert job.model_refs[0]["footprint_bytes"] == 5 * 1024**3

    def test_stale_stored_snapshot_is_never_used_fresh_value_wins(self):
        """The row's OWN stored `footprint_bytes` (as if stamped by a prior,
        now-stale claim) disagrees with what `footprint_for` resolves right
        now -- the claim transaction must use the FRESH value, never the
        stored one, both for admission and for the re-stamp."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="llama3.1:8b", measured_footprint_bytes=9 * 1024**3,
        )
        _set_budget(memory_budget_bytes=100 * 1024**3, max_concurrent_jobs=4)
        # Stored snapshot claims 1 GB -- stale and wrong; the connection's
        # CURRENT measured footprint is 9 GB.
        job = _job(model_refs=[_ref(footprint_bytes=1 * 1024**3)])

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert claimed[0]["model_refs"][0]["footprint_bytes"] == 9 * 1024**3
        job.refresh_from_db()
        assert job.model_refs[0]["footprint_bytes"] == 9 * 1024**3

    def test_unresolvable_footprint_stamps_none(self):
        """No matching `ModelConnection` row at all -- `footprint_for`
        returns `None`, and the claim stamps `None` right back rather than
        inventing a number."""
        _set_budget(memory_budget_bytes=None)
        job = _job(model_refs=[_ref(model_id="no-such-model")])

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert claimed[0]["model_refs"][0]["footprint_bytes"] is None
        job.refresh_from_db()
        assert job.model_refs[0]["footprint_bytes"] is None

    def test_running_jobs_footprints_also_resolved_fresh_for_planning(self):
        """A RUNNING job's stored snapshot is stale too -- admission must
        weigh it using the current `footprint_for` value, not the row's
        own stamped number, when deciding whether a second, budget-tight
        candidate fits alongside it."""
        ModelConnection.objects.create(
            name="conn", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="running-model", measured_footprint_bytes=8 * 1024**3,
        )
        _set_budget(memory_budget_bytes=10 * 1024**3, max_concurrent_jobs=4)
        # Running job's stored snapshot understates its real (fresh) 8 GB
        # footprint as 1 GB -- if admission trusted this stale number, a
        # second candidate needing 5 GB would look like it fits (1 + 5 <=
        # 10); using the fresh 8 GB, it must not (8 + 5 > 10).
        _job(
            state=RUNNING,
            model_refs=[_ref(model_id="running-model", footprint_bytes=1 * 1024**3)],
            claimed_by="other-worker",
            claim_token=uuid.uuid4(),
        )
        candidate = _job(model_refs=[_ref(model_id="candidate-model", footprint_bytes=None)])
        ModelConnection.objects.create(
            name="conn2", engine="ollama", endpoint="http://ollama.local:11434",
            model_id="candidate-model", measured_footprint_bytes=5 * 1024**3,
        )

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert claimed == []
        candidate.refresh_from_db()
        assert candidate.state == QUEUED


@pytest.mark.django_db
class TestCandidateWindowAndOrdering:
    def test_admits_in_priority_then_id_order(self):
        _set_budget(memory_budget_bytes=None)
        low = _job(priority=200)
        high = _job(priority=50)

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        assert [d["id"] for d in claimed] == [high.pk]
        low.refresh_from_db()
        assert low.state == QUEUED

    def test_candidate_window_is_bounded(self):
        assert CANDIDATE_WINDOW == 200


@pytest.mark.django_db
class TestOrphanSweep:
    def test_first_orphan_is_requeued_with_attempts_one(self):
        from datetime import timedelta

        from django.utils import timezone

        _set_budget(memory_budget_bytes=10 * 1024**3, max_concurrent_jobs=4)
        # An unrelated, HEALTHY, exclusive running job blocks admission
        # entirely this round (rule 3, scheduler.py) -- otherwise the
        # orphan's requeue-then-immediate-re-admission-in-the-same-round
        # (also correct behavior; the requeued row is a perfectly valid
        # candidate the instant it goes back to `queued`) would make its
        # transient `queued` state unobservable from outside the
        # transaction, which is what this test isolates.
        _job(
            state=RUNNING, claimed_by="alive", claim_token=uuid.uuid4(),
            exclusive=True, heartbeat_at=timezone.now(),
        )

        job = _job(
            state=RUNNING,
            claimed_by="dead-worker",
            claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=STALE_AFTER + 1),
            attempts=0,
        )

        claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.attempts == 1
        assert job.claimed_by == ""
        assert job.claim_token is None
        assert job.heartbeat_at is None

    def test_second_orphan_fails_permanently(self):
        from datetime import timedelta

        from django.utils import timezone

        job = _job(
            state=RUNNING,
            claimed_by="dead-worker",
            claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=STALE_AFTER + 1),
            attempts=1,
        )

        claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        job.refresh_from_db()
        assert job.state == "failed"
        assert job.error == (
            "the worker running this job stopped responding twice; it was not retried again"
        )

    def test_second_orphan_of_a_rag_ingest_job_fails_its_stranded_document(
        self, django_capture_on_commit_callbacks
    ):
        """T9.5 audit §5, the stranded-Document fix: a `rag.ingest` job's
        SECOND orphaning (permanently `failed`, never retried -- the
        branch `test_second_orphan_fails_permanently` above already
        proves at the job-row level) must not leave its Document row
        stuck at PENDING/PROCESSING forever -- `rag.ingest`'s registered
        `on_terminal` hook (`tools.rag.jobs.on_ingest_terminal`, the
        REAL kind registered by `tools/rag/apps.py` at Django startup,
        not a fake test kind) flips it to FAILED with an honest, Retry-
        enabling `status_detail`. `django_capture_on_commit_callbacks`:
        the hook is scheduled via `transaction.on_commit` from INSIDE
        `claim_and_admit`'s own `atomic()` block (see that function's
        docstring for why it must never run inside the advisory-lock
        window itself) -- this fires it without needing a real DB commit.

        Does not request `reset_registry` (this module's own fixture is
        bound `autouse=False`, see the module docstring) -- this test's
        whole point is that the REAL `rag.ingest` registration, `on_
        terminal` hook included, is what fires here, not a fake test
        kind standing in for it.
        """
        from datetime import timedelta

        from django.utils import timezone

        from tools.rag.models import Document

        doc = Document.objects.create(
            title="stranded.txt",
            source_path="/tmp/irrelevant/stranded.txt",
            original_path="/tmp/irrelevant/stranded.txt",
            file_hash="a" * 64,
            doc_type=Document.DocType.PROSE,
            status=Document.Status.PROCESSING,
        )
        job = InferenceJob.objects.create(
            kind="rag.ingest",
            priority=200,
            state=RUNNING,
            claimed_by="dead-worker",
            claim_token=uuid.uuid4(),
            heartbeat_at=timezone.now() - timedelta(seconds=STALE_AFTER + 1),
            attempts=1,
            payload={"document_id": doc.id, "sha256": doc.file_hash, "medium": "prose", "title": doc.title},
            model_refs=[],
        )

        with django_capture_on_commit_callbacks(execute=True):
            claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        job.refresh_from_db()
        assert job.state == "failed"

        doc.refresh_from_db()
        assert doc.status == Document.Status.FAILED
        assert doc.status_detail == (
            "The worker running this job stopped responding; it was not retried again."
        )

    def test_fresh_heartbeat_is_not_swept(self):
        job = _job(
            state=RUNNING, claimed_by="alive-worker", claim_token=uuid.uuid4(),
        )
        from django.utils import timezone
        InferenceJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now())

        claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)

        job.refresh_from_db()
        assert job.state == RUNNING

    def test_claimed_but_never_launched_row_is_recovered_by_a_later_sweep(self):
        """The accepted recovery window `claim_and_admit`'s own docstring
        describes: a row this function marks `running` is committed
        BEFORE the calling worker ever gets a chance to actually launch it
        onto its thread pool. If that worker process dies in the gap, the
        row is recovered exactly like any other orphan -- indistinguishably
        -- once its heartbeat goes stale."""
        from datetime import timedelta

        from django.utils import timezone

        _set_budget(memory_budget_bytes=10 * 1024**3, max_concurrent_jobs=4)
        job = _job()

        claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)
        assert [d["id"] for d in claimed] == [job.pk]
        job.refresh_from_db()
        assert job.state == RUNNING

        # The worker that claimed it dies before ever calling `_launch` --
        # nothing ever refreshes the heartbeat again.
        InferenceJob.objects.filter(pk=job.pk).update(
            heartbeat_at=timezone.now() - timedelta(seconds=STALE_AFTER + 1)
        )
        # An unrelated, healthy, exclusive running job blocks re-admission
        # this round so the requeue is directly observable, same
        # technique as `test_first_orphan_is_requeued_with_attempts_one`.
        _job(
            state=RUNNING, claimed_by="alive", claim_token=uuid.uuid4(),
            exclusive=True, heartbeat_at=timezone.now(),
        )

        claim_and_admit("worker-b", stale_after_seconds=STALE_AFTER)

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.attempts == 1


@pytest.mark.django_db(transaction=True)
class TestConcurrency:
    def test_two_threads_racing_claim_every_job_exactly_once(self):
        """Two threads hammer `claim_and_admit` concurrently against the
        same 10 queued jobs -- none of them ever actually finishes (this
        test never runs a worker/handler), so `max_concurrent_jobs` is set
        comfortably above 10: nothing here exercises `plan_admissions`'s
        own cap, only whether two concurrent admitters can ever claim the
        same job twice or deadlock each other. The advisory lock
        serializes whichever thread's round actually admits; every other
        concurrent call in that same window must return promptly (either
        `[]` from losing the lock, or `[]` from winning it with nothing
        left to admit) -- never block, never double-claim."""
        _set_budget(memory_budget_bytes=1000 * 1024**3, max_concurrent_jobs=20)
        for i in range(10):
            ModelConnection.objects.create(
                name=f"conn{i}", engine="ollama", endpoint="http://ollama.local:11434",
                model_id=f"model-{i}", measured_footprint_bytes=1024,
            )
        job_ids = {
            _job(model_refs=[_ref(model_id=f"model-{i}", footprint_bytes=None)]).pk
            for i in range(10)
        }

        results: list[int] = []
        results_lock = threading.Lock()
        errors: list[Exception] = []

        def worker_loop(worker_id: str) -> None:
            try:
                for _ in range(60):
                    with results_lock:
                        if len(results) >= len(job_ids):
                            return
                    claimed = claim_and_admit(worker_id, stale_after_seconds=STALE_AFTER)
                    if claimed:
                        with results_lock:
                            results.extend(d["id"] for d in claimed)
                    else:
                        time.sleep(0.01)
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)
            finally:
                from django.db import connection
                connection.close()

        threads = [
            threading.Thread(target=worker_loop, args=(f"worker-{n}",)) for n in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert not any(t.is_alive() for t in threads)
        assert sorted(results) == sorted(job_ids)
        assert len(results) == len(set(results))  # every job claimed exactly once


@pytest.mark.django_db(transaction=True)
class TestSkipLocked:
    # How long the lock-holder thread is willing to hold its lock before
    # giving up on its own -- a SAFETY NET for the holder, not a timing
    # budget for the assertion below. Costs nothing when the test is
    # passing: the main thread calls `release_event.set()` the instant its
    # own timing/assertion work is done (typically well under a second),
    # so the holder never actually waits anywhere near this long. Generous
    # on purpose, so a slow/loaded machine never makes the HOLDER time out
    # for reasons having nothing to do with what this test actually checks
    # (see `elapsed`'s assertion, below).
    HOLD_SECONDS = 30

    def test_locked_row_is_skipped_not_blocked_on(self):
        """A row a concurrent transaction holds `SELECT ... FOR UPDATE` on
        must be skipped this round (`SKIP LOCKED`), not waited on -- proven
        by this call returning promptly with the OTHER, unlocked candidate
        admitted instead. The STRUCTURAL assertions below (which candidate
        got admitted, which stayed queued) are this test's primary
        meaning; `elapsed`'s assertion is a secondary, deliberately loose
        NON-BLOCKING bound (see its own comment), not a performance
        budget."""
        _set_budget(memory_budget_bytes=None, max_concurrent_jobs=4)
        locked_job = _job(priority=10)
        free_job = _job(priority=20)

        locked_event = threading.Event()
        release_event = threading.Event()

        def hold_lock() -> None:
            try:
                with transaction.atomic():
                    InferenceJob.objects.select_for_update().filter(pk=locked_job.pk).first()
                    locked_event.set()
                    release_event.wait(timeout=self.HOLD_SECONDS)
            finally:
                from django.db import connection
                connection.close()

        holder = threading.Thread(target=hold_lock)
        holder.start()
        assert locked_event.wait(timeout=self.HOLD_SECONDS)

        try:
            started = time.monotonic()
            claimed = claim_and_admit("worker-a", stale_after_seconds=STALE_AFTER)
            elapsed = time.monotonic() - started
        finally:
            release_event.set()
            holder.join(timeout=self.HOLD_SECONDS)

        # A NON-BLOCKING bound, not a performance budget (review finding,
        # T4 round 3): derived from HOLD_SECONDS (the holder's own,
        # generous budget) rather than a fixed small number, specifically
        # so this can never be the assertion that flakes under ordinary
        # system load -- it only fails if claim_and_admit() genuinely
        # blocked for a substantial fraction of as long as the holder was
        # willing to hold the lock, i.e. SKIP LOCKED did not take effect
        # at all.
        assert elapsed < self.HOLD_SECONDS / 3
        # `SKIP LOCKED` excludes `locked_job` from the candidate query
        # entirely (rather than blocking on it), so -- even though
        # `locked_job` has the lower priority number and would normally be
        # the sequential-mode head -- the ONLY candidate this round ever
        # sees is `free_job`, which is admitted in its place. The important
        # assertion is that this call did not BLOCK on the lock.
        assert [d["id"] for d in claimed] == [free_job.pk]
        free_job.refresh_from_db()
        assert free_job.state == RUNNING
        locked_job.refresh_from_db()
        assert locked_job.state == QUEUED


class TestKindAwareStaleness:
    """A global 120s cutoff cannot be right for both a sub-second embed
    and a kind that cold-loads a large model for sixteen minutes. A kind
    declares its own threshold in CODE (it is a property of what the
    work does, like `default_priority`), and the sweep honours it."""

    @pytest.mark.django_db
    def test_a_kind_declaring_a_long_threshold_is_not_swept_early(self, reset_registry):
        register_job_kind(JobKind(
            key="test.slow", label="Slow", planner=f"{MODULE}.plan_no_models",
            handler=f"{MODULE}.echo_handler", summarizer=f"{MODULE}.summarize_noop",
            stale_after_seconds=3600,
        ))
        job = _running_job(kind="test.slow", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120)

        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.attempts == 0

    @pytest.mark.django_db
    def test_a_kind_declaring_nothing_falls_back_to_the_global(self, reset_registry):
        register_job_kind(JobKind(
            key="test.plain", label="Plain", planner=f"{MODULE}.plan_no_models",
            handler=f"{MODULE}.echo_handler", summarizer=f"{MODULE}.summarize_noop",
        ))
        # `max_concurrent_jobs=0` (scheduler.py's own "admits nothing" rule
        # for a non-positive cap) holds this round's admission to zero, so
        # the row the sweep just requeued is observed QUEUED rather than
        # immediately re-admitted back to RUNNING in the same transaction
        # -- the same transient-state visibility `test_first_orphan_is_
        # requeued_with_attempts_one` above notes for its own blocker.
        _set_budget(memory_budget_bytes=None, max_concurrent_jobs=0)
        job = _running_job(kind="test.plain", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120)

        job.refresh_from_db()
        assert job.state == QUEUED
        assert job.attempts == 1

    @pytest.mark.django_db
    def test_an_unregistered_kind_falls_back_to_the_global(self, reset_registry):
        """A row whose kind was deregistered since it was enqueued is
        still swept, on the global threshold -- never left running for ever
        because nothing declares a number for it."""
        _set_budget(memory_budget_bytes=None, max_concurrent_jobs=0)  # see comment above
        job = _running_job(kind="test.gone", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120)

        job.refresh_from_db()
        assert job.state == QUEUED

    @pytest.mark.django_db
    def test_sweep_orphans_false_skips_the_sweep_entirely(self, reset_registry):
        # `max_concurrent_jobs=0` (see the comment on the sibling tests
        # above): without it, "sweep ran, requeued, then re-admitted" and
        # "sweep never ran" both leave the row RUNNING -- indistinguishable
        # on `state` alone. With admission held to zero, a sweep that ran
        # would leave the row QUEUED/attempts=1, so `attempts == 0` below
        # is what actually proves the sweep never fired.
        _set_budget(memory_budget_bytes=None, max_concurrent_jobs=0)
        job = _running_job(kind="test.plain", heartbeat_age_seconds=600)

        claim_and_admit("w", stale_after_seconds=120, sweep_orphans=False)

        job.refresh_from_db()
        assert job.state == RUNNING
        assert job.attempts == 0  # the sweep never ran -- not "ran and re-admitted"

    @pytest.mark.django_db
    def test_the_sweep_reads_the_running_set_once_however_many_thresholds(
            self, reset_registry, django_assert_num_queries):
        """Grouped by distinct threshold into ONE query, not one per kind
        -- pinned so a later kind cannot quietly make the sweep N+1."""
        for index, seconds in enumerate((30, 300, 3000)):
            register_job_kind(JobKind(
                key=f"test.k{index}", label="K", planner=f"{MODULE}.plan_no_models",
                handler=f"{MODULE}.echo_handler", summarizer=f"{MODULE}.summarize_noop",
                stale_after_seconds=seconds,
            ))

        with django_assert_num_queries(1):
            _sweep_orphans(120)
