"""
The execution queue's claim transaction (ADR 0013, T4) --
`claim_and_admit()` is the one function that turns live `InferenceJob` rows
into `models.queue.scheduler.SchedCandidate`/`SchedModel` snapshots per that
module's binding "footprint/key provenance contract" (see `scheduler.py`'s
module docstring), calls `plan_admissions`, and atomically marks the
admitted rows running.

Why an advisory lock, not just `SELECT ... FOR UPDATE` on the candidate
rows: admission has to reason about EVERY running + candidate job's
resident footprint as ONE atomic decision (`plan_admissions`'s
`resident_bytes`/dedup fold) -- two concurrent admitters must never build
competing plans off overlapping snapshots of the same machine. A row-level
lock only protects the rows it locks; it does nothing to stop a second
admitter from reading a *different*, equally-stale view of the running set
concurrently and admitting past `max_concurrent`/`budget_bytes` itself.
`pg_try_advisory_xact_lock` instead makes the whole admission DECISION
single-flight across however many worker processes are polling: whichever
process gets the lock this tick does the entire read-plan-write sequence
alone; every other process that tries `claim_and_admit` at the same moment
gets `False` back immediately (never blocks) and simply returns `[]` --
"another admitter is running right now, poll again next tick", not an
error. The lock is transaction-scoped (`_xact_lock`, not the session-scoped
`pg_advisory_lock`), so it releases automatically when this function's
transaction commits OR rolls back -- including on an uncaught exception --
with no `finally`/`pg_advisory_unlock` needed anywhere.

`SELECT ... FOR UPDATE SKIP LOCKED` on the CANDIDATE query underneath the
advisory lock guards a DIFFERENT race: an operator's `cancel_job` (or a
future requeue path) taking a row-level lock on one specific queued row at
the same moment this transaction is scanning candidates. Without
`SKIP LOCKED` this transaction would BLOCK waiting for that row, stalling
the whole admission round behind one unrelated cancel; with it, that one
row is simply skipped this round (it will be reconsidered next tick, once
whatever is cancelling/touching it has committed) while every other
candidate is still evaluated normally.

`SCHEDULER_LOCK_KEY` -- Postgres advisory locks share ONE global namespace
per database, keyed by a single bigint; there is no separate sub-namespace
per subsystem/table/purpose the way row locks are scoped to a table. A
collision would mean some OTHER piece of code (this app or an extension)
independently took a lock under the exact same integer, which nothing else
in this codebase does today (`pg_advisory_lock`/`pg_try_advisory_lock` do
not appear anywhere else at the time this was written). The key is derived
from a namespaced, human-readable string via a deterministic hash
(`zlib.crc32` -- NEVER Python's built-in `hash()`, which is randomly salted
per process by `PYTHONHASHSEED` and would disagree between the several
worker processes that must all compute the exact same key) so the source
documents WHAT this lock protects instead of presenting an opaque literal
some future reader would have to `grep` blindly for.
"""
from __future__ import annotations

import logging
import uuid
import zlib
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from models.registry.bindings import footprint_for
from models.registry.discovery import norm_endpoint, norm_tag
from models.queue.models import FAILED, QUEUED, RUNNING, InferenceJob, JobSettings
from models.queue.scheduler import SchedCandidate, SchedModel, plan_admissions
from models.contracts.jobkinds import all_job_kinds, invoke_on_terminal

logger = logging.getLogger(__name__)

SCHEDULER_LOCK_KEY = zlib.crc32(b"farabunker.jobs.scheduler.admission")

# The candidate scan is bounded, not "every queued row": a backlog of
# thousands of queued jobs must not make every admission tick pay for an
# unbounded scan when `plan_admissions`'s rule 9 (scheduler.py) only ever
# admits a short prefix of the (priority, id) order in any one round
# anyway, and STOPS at the first non-admissible candidate rather than
# scanning past it. 200 is comfortably larger than any `max_concurrent`
# an operator is expected to configure, so it never truncates a real
# admission decision -- only the pathological backlog case that would
# otherwise cost an unbounded `ORDER BY` scan every tick.
CANDIDATE_WINDOW = 200


def claim_and_admit(
    worker_id: str, *, stale_after_seconds: int, sweep_orphans: bool = True,
    settings_row: JobSettings | None = None,
) -> list[dict]:
    """Claim and admit newly-runnable jobs for `worker_id`, one round.

    `settings_row` (S6) is the caller's OWN already-fetched
    `JobSettings` row, threaded in rather than re-fetched here -- the
    third spelling of "read the singleton once per unit of work", copied
    from `identity.request.settings_row_for` rather than invented. The
    unit of work here is a worker TICK, not a request:
    `models.queue.worker.Worker.tick` calls this function and
    `_evict_to_match_plan` back to back, every `POLL_INTERVAL_SECONDS`,
    and both need the same two numbers. It reads the row once at the top
    of the tick and hands the SAME row to both.

    `None` falls back to `JobSettings.get_solo()` here, exactly as
    `settings_row_for`'s no-row branch does -- that is what keeps this
    function callable on its own (every test in `models/queue/tests/
    test_claim.py` does), and what makes the threading a query-count
    change rather than a change to this function's contract. The two
    values are read off ONE row either way, so admission can never plan
    against a budget and a concurrency cap fetched a moment apart.

    `stale_after_seconds` is the orphan sweep's staleness threshold -- a
    parameter, not a module constant here, because the actual constant
    (`STALE_AFTER_SECONDS`) is owned by `models.queue.worker` (documented
    there as one of this worker process's internal cadence knobs) and this
    module must not import from `worker` (which imports THIS module to call
    `claim_and_admit` -- importing back would be circular). Passing it in
    keeps the orphan sweep itself testable in isolation too, with an
    arbitrarily short threshold, no monkeypatching required.

    `sweep_orphans` (spec §3.4c) lets the caller skip step 2 for ONE round.
    The worker passes `False` for a grace period after it detects that the
    HOST SLEPT -- on wake every running row looks stale at once, because
    wall-clock hours passed while the process's monotonic clock barely
    advanced, and a sweep at that instant mass-orphans healthy work. It is
    a deliberate one-round skip, never a mode: the very next tick sweeps
    normally.

    Returns a list of claimed job descriptors, each `{"id", "kind",
    "payload", "model_refs", "claim_token", "exclusive", "checkpoint",
    "attempts"}` -- `model_refs` carries the FRESHLY resolved footprints
    just stamped onto the row (see step 3 below), ready to build `core.
    inference.jobkinds.ModelRef` objects from; `exclusive` is the row's own
    declared flag, included for the worker's pre-launch eviction (an
    admitted exclusive job evicts everything else at its endpoints,
    unconditionally). `checkpoint`/`attempts` (T3) are the row's own
    stored values, READ here but never written by this function -- step 5's
    claim UPDATE deliberately does not touch either column (see that
    step's own note): a requeued job's checkpoint survives across however
    many claim rounds it takes to actually run, and `attempts` is bumped
    only by `_sweep_orphans`, never by an ordinary claim. The worker
    (`models.queue.worker.Worker._execute`) reads both straight off this
    descriptor to build the claimed job's `models.contracts.jobkinds.
    JobContext` (`checkpoint_state=checkpoint`, `attempt=attempts`).

    One transaction, in order:

    1. `pg_try_advisory_xact_lock(SCHEDULER_LOCK_KEY)` -- not acquired
       means another admitter is mid-round right now; return `[]`
       immediately (never block), the caller polls again next tick.
    2. Orphan sweep (`_sweep_orphans`), inside this SAME transaction,
       before anything below reads the running set -- a job whose worker
       stopped heartbeating must be requeued/failed before this round's
       admission plan is built against the running set, or a genuinely
       dead job would keep occupying a `max_concurrent` slot / resident
       footprint that is not really there anymore.
    3. Load `running` (state=running) and up to `CANDIDATE_WINDOW`
       `candidates` (state=queued, `(priority, id)` order,
       `select_for_update(skip_locked=True)` -- see module docstring for
       why `SKIP LOCKED` here specifically). For EVERY model ref of BOTH
       sets, resolve `footprint_for(engine, endpoint, model_id)` FRESH,
       right now -- never the row's stored/stamped snapshot, which is
       display/history data only (`scheduler.py`'s provenance contract).

       `not_before` (spec §3.3d) is the ONE new admission-side filter this
       track adds -- an extra `WHERE` on this same SELECT, not a second
       statement. A job the worker refused to launch this tick is excluded
       until its hold-off expires, so it cannot be re-claimed on every 0.5s
       tick against an engine that is still holding memory. The no-backfill
       deadlock proof survives because the exclusion is time-bounded and
       SELF-CLEARING -- the job returns to its own head position the moment
       the hold-off passes, and an effectively-exclusive head is still
       admitted alone the instant the machine is idle -- so nothing can
       wait behind it for ever.
    4. `plan_admissions(candidates, running, budget_bytes=<row>.
       memory_budget_bytes, max_concurrent=<row>.max_concurrent_jobs)`,
       `<row>` being the threaded `settings_row` or this function's own
       fallback fetch (see above).
    5. For each admitted id: one conditional `UPDATE` -- state=running,
       claimed_by=worker_id, a FRESH `claim_token` (uuid4, one per job),
       started_at=now, heartbeat_at=now, attempts unchanged, and
       `model_refs` rewritten with the step-3 footprints filled in
       (`footprint_bytes` per entry) -- the "filled at claim" moment
       `InferenceJob.model_refs`'s docstring describes: this stamp is
       display/audit data about what admission believed at the moment it
       admitted this job, and the scheduler itself never reads it back
       (every future round re-resolves fresh again, per the provenance
       contract).
    6. Return descriptors for the caller (the worker) to submit to its
       thread pool.

    Accepted recovery window: a row this function marks `running` (step 5)
    is committed to the DB the instant this transaction commits -- BEFORE
    the calling worker ever gets a chance to actually launch it onto its
    thread pool (`models.queue.worker.Worker._launch`). If that worker
    process dies (crash, SIGKILL) in the narrow gap between this
    transaction committing and the launch happening, the row is left
    looking exactly like any other abandoned job: `running`, with a
    `heartbeat_at` that will never be refreshed. No special handling
    exists for this case, deliberately -- it needs none. The very same
    orphan sweep (`_sweep_orphans`, step 2 above) that recovers a job whose
    WORKER died mid-execution recovers this one too, indistinguishably,
    once `heartbeat_at` goes stale past `stale_after_seconds`: requeued
    with `attempts=1` on the first such sweep, same as any other orphan.
    The accepted cost is up to `stale_after_seconds` (`STALE_AFTER_SECONDS`
    in production, 120s) of the job merely sitting `running`-but-idle
    before another worker's sweep notices and reclaims it -- not a
    correctness gap, a bounded recovery latency.
    """
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [SCHEDULER_LOCK_KEY])
            (acquired,) = cursor.fetchone()
        if not acquired:
            return []

        if sweep_orphans:
            _sweep_orphans(stale_after_seconds)

        running_rows = list(InferenceJob.objects.filter(state=RUNNING))
        now = timezone.now()
        candidate_rows = list(
            InferenceJob.objects.select_for_update(skip_locked=True)
            .filter(state=QUEUED)
            .filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
            .order_by("priority", "id")[:CANDIDATE_WINDOW]
        )

        running_resolved = {row.pk: _resolve_refs(row.model_refs) for row in running_rows}
        candidate_resolved = {row.pk: _resolve_refs(row.model_refs) for row in candidate_rows}

        running = [
            _sched_candidate(row, running_resolved[row.pk]) for row in running_rows
        ]
        candidates = [
            _sched_candidate(row, candidate_resolved[row.pk]) for row in candidate_rows
        ]

        job_settings = settings_row if settings_row is not None else JobSettings.get_solo()
        admitted_ids = plan_admissions(
            candidates,
            running,
            budget_bytes=job_settings.memory_budget_bytes,
            max_concurrent=job_settings.max_concurrent_jobs,
        )

        rows_by_id = {row.pk: row for row in candidate_rows}
        now = timezone.now()
        descriptors: list[dict] = []
        for job_id in admitted_ids:
            row = rows_by_id[job_id]
            resolved = candidate_resolved[job_id]
            stamped_refs = [{**ref, "footprint_bytes": footprint} for ref, footprint in resolved]
            token = uuid.uuid4()

            # Deliberately does NOT touch `progress`/`checkpoint` -- an
            # ordinary claim is not a resume decision, it's admission;
            # whatever a PRIOR attempt last checkpointed (if any) must
            # still be there for the worker to read back into this
            # attempt's `JobContext.checkpoint_state` moments from now.
            InferenceJob.objects.filter(pk=job_id).update(
                state=RUNNING,
                claimed_by=worker_id,
                claim_token=token,
                started_at=now,
                heartbeat_at=now,
                model_refs=stamped_refs,
            )
            descriptors.append(
                {
                    "id": job_id,
                    "kind": row.kind,
                    "payload": row.payload,
                    "model_refs": stamped_refs,
                    "claim_token": token,
                    "exclusive": row.exclusive,
                    "checkpoint": row.checkpoint,
                    "attempts": row.attempts,
                }
            )

        return descriptors


def _resolve_refs(model_refs: list[dict]) -> list[tuple[dict, int | None]]:
    """`(ref, footprint)` pairs for every entry of `model_refs`, each
    footprint resolved FRESH via `footprint_for` right now -- shared by the
    `SchedModel` construction (planning) and the claim-time stamp (step 5)
    so a footprint is computed exactly once per ref per round, never
    re-queried for the write after already being read for the plan."""
    return [
        (ref, footprint_for(ref["engine"], ref["endpoint"], ref["model_id"]))
        for ref in model_refs
    ]


def _sched_candidate(row: InferenceJob, resolved: list[tuple[dict, int | None]]) -> SchedCandidate:
    """Build one `SchedCandidate` for `row` (running or queued -- the
    scheduler treats both shapes identically) from its already-resolved
    `(ref, footprint)` pairs. `key` is normalized per `scheduler.py`'s
    provenance contract: `norm_endpoint()` for the endpoint segment,
    `norm_tag()` for the model_id segment -- built here, the one place
    a `SchedModel.key` is ever constructed for live rows."""
    models = tuple(
        SchedModel(
            key=(ref["engine"], norm_endpoint(ref["endpoint"]), norm_tag(ref["model_id"])),
            footprint_bytes=footprint,
        )
        for ref, footprint in resolved
    )
    return SchedCandidate(job_id=row.pk, priority=row.priority, exclusive=row.exclusive, models=models)


def _kind_stale_thresholds(default_stale_seconds: int) -> dict[str, int]:
    """`{job kind key: staleness threshold}` for every REGISTERED kind,
    with `default_stale_seconds` standing in for any kind that declares
    none.

    Resolved HERE, in the claim module, rather than threaded in from the
    worker: this module already imports the job-kind registry, the sweep
    runs inside this module's own advisory-lock transaction, and the
    worker owns only the global cadence constant it passes in (it may not
    be imported from here -- it imports this module).
    """
    thresholds: dict[str, int] = {}
    for kind in all_job_kinds():
        declared = getattr(kind, "stale_after_seconds", None)
        thresholds[kind.key] = declared if declared else default_stale_seconds
    return thresholds


def _sweep_orphans(default_stale_seconds: int) -> None:
    """Requeue-or-fail every `running` job whose heartbeat has gone stale
    -- now against THAT KIND's own threshold (spec §3.4b) rather than one
    global cutoff, which could never be right for both a sub-second embed
    and a job that cold-loads a large model for minutes.

    ONE QUERY, grouped by DISTINCT threshold (a handful of kinds, bounded
    and pinned by a query-count test) -- never one query per kind. A kind
    the registry does not know (deregistered since the row was enqueued)
    falls to `default_stale_seconds`, so no row is ever left running for
    ever merely because nothing declares a number for it.

    Everything below this point is unchanged: first orphaning requeues
    with `attempts=1`, a second fails permanently and schedules the kind's
    `on_terminal` hook via `transaction.on_commit`, and each row's own
    UPDATE re-checks `state=running AND heartbeat_at < <that row's own
    cutoff>` atomically so a heartbeat landing in the window between read
    and write leaves the job correctly alone.
    """
    now = timezone.now()
    thresholds = _kind_stale_thresholds(default_stale_seconds)

    by_threshold: dict[int, list[str]] = {}
    for kind_key, seconds in thresholds.items():
        by_threshold.setdefault(seconds, []).append(kind_key)

    condition = Q(
        ~Q(kind__in=list(thresholds)),
        heartbeat_at__lt=now - timedelta(seconds=default_stale_seconds),
    )
    for seconds, kind_keys in by_threshold.items():
        condition |= Q(kind__in=kind_keys, heartbeat_at__lt=now - timedelta(seconds=seconds))

    stale = list(InferenceJob.objects.filter(Q(state=RUNNING) & condition))

    for job in stale:
        cutoff = now - timedelta(seconds=thresholds.get(job.kind, default_stale_seconds))
        if job.attempts == 0:
            # `progress`/`checkpoint` are deliberately NOT in this field
            # list (T3) -- their preservation across a requeue IS the
            # resume mechanism: whatever the dead worker's handler last
            # checkpointed is exactly what the NEXT claim's `JobContext.
            # checkpoint_state` must see, and a stale `progress` reading
            # is harmless (the resumed attempt's own first report simply
            # overwrites it).
            updated = InferenceJob.objects.filter(
                pk=job.pk, state=RUNNING, heartbeat_at__lt=cutoff,
            ).update(
                state=QUEUED,
                claimed_by="",
                claim_token=None,
                attempts=1,
                started_at=None,
                heartbeat_at=None,
            )
            if updated:
                logger.warning(
                    "claim: job %s orphaned (worker %r stopped heartbeating) -- requeued, attempts=1",
                    job.pk, job.claimed_by,
                )
        else:
            # A second orphaning is permanently `failed`, not requeued --
            # `progress`/`checkpoint` are left exactly as they last stood
            # (same reason `run_ask`'s failure writeback in `_execute`
            # keeps both: diagnostic value, and a future manual retry
            # could still resume from them).
            updated = InferenceJob.objects.filter(
                pk=job.pk, state=RUNNING, heartbeat_at__lt=cutoff,
            ).update(
                state=FAILED,
                finished_at=timezone.now(),
                error="the worker running this job stopped responding twice; it was not retried again",
            )
            if updated:
                logger.error(
                    "claim: job %s orphaned a second time (worker %r) -- failed permanently",
                    job.pk, job.claimed_by,
                )
                # T9.5 audit §5, the stranded-Document fix: `kind`'s
                # registered `on_terminal` hook (if any), scheduled via
                # `transaction.on_commit` rather than called inline --
                # this whole sweep runs INSIDE `claim_and_admit`'s
                # advisory-lock transaction (module docstring), and a
                # feature-app hook is arbitrary code that must never run
                # inside that lock's window. `on_commit` defers it until
                # that outer transaction actually commits, whichever
                # caller's `atomic()` block that turns out to be -- see
                # `models.contracts.jobkinds.invoke_on_terminal`'s own
                # docstring for the tolerant lookup-then-call contract a
                # broken hook can never break this sweep through.
                transaction.on_commit(
                    lambda kind=job.kind, payload=job.payload: invoke_on_terminal(kind, payload, "failed")
                )
