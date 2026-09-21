"""
The execution queue's worker process (ADR 0013, T4) -- polls
`models.queue.claim.claim_and_admit` for newly admitted jobs, runs each in a
thread pool, and is the ONE writer of a running job's heartbeat.

This closes the two gaps `tools.rag.ingest.watch_folder`
(`tools/rag/ingest.py:289-311`) leaves open -- that function is the
precedent for "a long-lived management-command loop" in this codebase, and
a queue worker is the same shape (a container process meant to run
unattended, indefinitely), but it was never meant to survive the demands
that shape puts on a worker specifically:

- No SIGTERM handling: `watch_folder`'s `while True: time.sleep(1)` loop
  only ever exits on `KeyboardInterrupt` (SIGINT). `docker compose stop`/
  `down` sends SIGTERM, which that loop does not catch at all -- the
  container's `stop_grace_period` elapses with nothing having noticed, and
  Docker SIGKILLs the process. For an ingest watcher that mostly loses a
  little responsiveness; for a worker mid-job it would abandon an
  in-flight `InferenceJob` row still marked `running` with no chance to
  hand it back to the queue. This worker installs SIGTERM/SIGINT handlers
  that ONLY set a stopping flag (`Worker._handle_signal`) and drains
  in-flight jobs for up to `SHUTDOWN_GRACE_SECONDS` before exiting -- see
  `Worker._drain_inflight`/`Worker._shutdown`.
- No DB connection lifecycle: `watch_folder`'s loop never calls
  `close_old_connections()`, so a long-lived process holds one Django DB
  connection open indefinitely, silently reusing it past `CONN_MAX_AGE` or
  straight through a DB restart (see `config/settings.py`'s
  `CONN_HEALTH_CHECKS`, added alongside this task for exactly this reason).
  This worker calls `close_old_connections()` at the top of every tick, and
  each job-execution thread closes ITS OWN connection in a `finally` when
  the job finishes (Django's per-thread connection model means a thread
  that keeps running after its one job is done would otherwise leave that
  connection open indefinitely too), and the dedicated heartbeat thread
  (`_heartbeat_forever`) calls `close_old_connections()` at the top of each
  of its own iterations, for the same reason.

Never-500 philosophy, worker edition: one job's handler raising must never
take the worker process down or stop it claiming the next job -- every
per-job step below happens inside `Worker._execute`'s own `try/except`,
never the tick loop's.
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor

from django.db import close_old_connections
from django.db import connection as db_connection
from django.db import transaction
from django.utils import timezone

from models.registry.bindings import record_measured_footprint
from models.registry.discovery import norm_endpoint, norm_tag
from models.queue.claim import claim_and_admit
from models.queue.models import FAILED, QUEUED, RUNNING, SUCCEEDED, InferenceJob, JobSettings
from models.contracts.engines import get_engine
from models.contracts.jobkinds import JobContext, ModelRef, get_job_kind, invoke_on_terminal, resolve_dotted_path

logger = logging.getLogger(__name__)

# --- Internal tuning constants -------------------------------------------
#
# Deliberately NOT operator knobs -- `models/queue/models.py::JobSettings`'s
# docstring draws exactly this line: `memory_budget_bytes`,
# `max_concurrent_jobs`, `default_priority`, and `retention_limit` are the
# operator-editable, queue-WIDE settings (one DB row, editable without a
# deploy). Everything below is this WORKER PROCESS's own internal cadence
# -- how often it polls, how often it writes a heartbeat, how stale is too
# stale, how long it waits during a drain -- which is an implementation
# detail of one worker implementation, not a policy decision an operator
# should be tuning per deployment. Plain module attributes (not Django
# settings) on purpose: a test that needs a faster cadence monkeypatches
# the attribute directly, with no `override_settings` machinery for a knob
# that was never meant to be settings-shaped in the first place.

POLL_INTERVAL_SECONDS = 0.5

# How often the ONE heartbeat UPDATE (see `Worker._maybe_heartbeat`) is
# actually written -- throttled independently of `POLL_INTERVAL_SECONDS`
# (the claim-scan cadence) so a busy worker doesn't pay for a heartbeat
# write on every 0.5s tick when every 10s is plenty to keep a job looking
# alive well within `STALE_AFTER_SECONDS`.
HEARTBEAT_SECONDS = 10

# Must be MANY multiples of `HEARTBEAT_SECONDS`: a running job's row going
# stale by this many seconds means several consecutive heartbeat writes
# were missed outright -- the worker process itself died (crashed, was
# SIGKILLed, lost its DB connection permanently), not merely a slow tick.
# 120s is 12 missed heartbeats at the default cadence, comfortably beyond
# any GC pause or lock wait a live worker could plausibly stall on for --
# INCLUDING this tick's own pre-launch eviction, which is synchronous HTTP
# on this same thread and would otherwise eat into that margin: one
# endpoint's `list_installed` (two Ollama calls at `DISCOVERY_TIMEOUT`,
# ~10s worst case) plus up to `MAX_UNLOADS_PER_TICK` `unload()` calls (30s
# each, Ollama's `UNLOAD_TIMEOUT`) is bounded well under 120s for any
# realistic endpoint count, and `Worker.tick()` writes a FRESH heartbeat
# again immediately after eviction finishes (before launching anything),
# so the staleness clock restarts from that point rather than accumulating
# across ticks -- see `_evict_to_match_plan`'s docstring and the second
# `_maybe_heartbeat()` call in `tick()`.
STALE_AFTER_SECONDS = 120

# How often a handler's `ctx.report_progress(...)` call (T3, `core.
# inference.jobkinds.JobContext`) actually reaches the database, throttled
# independently of `HEARTBEAT_SECONDS` -- a handler is meant to be free to
# call `report_progress` as often as it likes (on every loop iteration, if
# that's natural for it) without reasoning about write cost itself; this
# constant is what makes that promise cheap rather than a busy handler
# hammering the DB with one UPDATE per iteration. Per-`JobContext` state
# (a closure the worker builds fresh in `_execute`, one per job attempt --
# see that method), NOT a shared/global clock: two jobs running
# concurrently on this same worker throttle independently of each other.
# `checkpoint()` writes are deliberately NOT throttled by anything here --
# see `JobContext.checkpoint`'s own docstring for why. One accepted
# consequence of the throttle itself: a FAILED job's preserved `progress`
# (see `_execute`'s own docstring on the success/failure writeback
# asymmetry) can lag the handler's true last `report_progress` call by up
# to this many seconds -- the very last report before a raise is the one
# most likely to have been dropped by the throttle window it landed in.
PROGRESS_INTERVAL_SECONDS = 5

# Wall-clock budget `Worker._drain_inflight` gives in-flight jobs to finish
# on their own once a stop has been requested, before giving up on whatever
# is still running. `compose.yaml`'s `worker` service sets
# `stop_grace_period: 30s` -- comfortably longer than this, so Docker's own
# SIGKILL is never the thing that actually ends this process; this
# worker's own drain-then-exit always gets there first.
SHUTDOWN_GRACE_SECONDS = 20

# Hard cap on how many `unload()` calls `_evict_to_match_plan` will issue
# per tick AGAINST NON-EXCLUSIVE, BUDGET-DRIVEN eviction ONLY -- an
# admitted-exclusive job's OWN endpoints are evicted separately and
# UNCAPPED (see `_evict_to_match_plan`'s docstring for why: there is no
# "later tick" to catch up on for that case). Each `unload()` can block
# for up to Ollama's own `UNLOAD_TIMEOUT` (30s) on a wedged/busy engine; an
# uncapped loop over N unneeded resident models would make this tick's own
# worst case `N * 30s` -- unbounded, and (per `STALE_AFTER_SECONDS`'s
# comment above) capable of starving this worker's OWN heartbeat long
# enough for another worker's orphan sweep to reclaim jobs this one still
# legitimately holds. 2 * UNLOAD_TIMEOUT (60s) leaves comfortable margin
# under `STALE_AFTER_SECONDS` (120s) even stacked with a `list_installed`
# round. Whatever budget-driven eviction this cap leaves undone is picked
# up on a LATER tick, but ONLY one that itself admits something --
# `_evict_to_match_plan` is called exclusively from `tick()`'s
# `if not claimed: return` branch, so a tick that admits nothing never
# calls it at all; the leftover eviction is not abandoned forever, but it
# is not guaranteed to be revisited on the very next tick either. Eviction
# is a best-effort catch-up against the admission plan, never a one-shot
# guarantee.
MAX_UNLOADS_PER_TICK = 2


class Worker:
    """One worker process: claims admitted jobs and runs them in a thread
    pool. `worker_id` defaults to `f"{socket.gethostname()}:{os.getpid()}"`
    -- the same free-text identifier stamped into `InferenceJob.claimed_by`,
    readable in logs/DB rows, never parsed back apart by this class."""

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._stopping = threading.Event()
        self._active_lock = threading.Lock()
        # job_id -> claim_token, for every job this process currently has
        # in flight -- the heartbeat UPDATE's `claim_token IN (...)` set.
        #
        # INVARIANT (2026-08-25 defect fix, see ADR 0013's dated amendment):
        # this dict is keyed by `job_id` alone, but a job_id can be
        # RE-CLAIMED by this SAME process while an OLDER attempt for that
        # same job_id is still finishing on its own pool thread -- the
        # orphan sweep (`models.queue.claim._sweep_orphans`) requeues a
        # job whose heartbeat went stale (e.g. a slow tick starved this
        # process's own heartbeat writer), and the very next `tick()` can
        # re-admit and re-launch that same job_id under a brand-new
        # `claim_token` while the STALE attempt's handler is still running
        # (it has no way to know it was orphaned mid-flight). `tick()`
        # registers the new attempt's token here BEFORE launching it, so
        # from that moment on this dict holds the NEW attempt's token, not
        # the old one.
        #
        # `_execute`'s `finally` MUST pop `self._active_tokens[job_id]`
        # ONLY when the stored value still equals the token ITS OWN attempt
        # was launched with -- an unconditional `.pop(job_id, None)` would
        # let a late-finishing OLD attempt evict the NEW attempt's token
        # out from under it, silently breaking `_maybe_heartbeat` for the
        # (still genuinely running) new attempt until the orphan sweep
        # wrongly orphans it a second time.
        self._active_tokens: dict[int, uuid.UUID] = {}

        # (job_id, claim_token) -> Future, for EVERY attempt this process
        # has ever launched and not yet pruned -- deliberately NOT keyed by
        # bare `job_id` (2026-08-25 SECOND defect fix, ADR 0013's dated
        # amendment: the first fix above, keying `_active_tokens` by
        # `job_id` and popping it token-conditionally, protected only the
        # HEARTBEAT bookkeeping. `_futures` was still keyed by bare
        # `job_id`, and `_launch` UNCONDITIONALLY OVERWROTE whatever entry
        # was already there -- so a re-claimed attempt's `_launch` call
        # silently dropped the superseded attempt's own `Future` on the
        # floor, with nothing left referencing it). Keying by the pair
        # means `_launch` INSERTS a brand-new entry for every attempt
        # instead of clobbering one (a reclaimed attempt's `claim_token` is
        # always freshly minted by `claim_and_admit`, so this key can never
        # collide with an earlier attempt's own); a superseded (stale,
        # orphaned-and-reclaimed) attempt's `Future` therefore stays
        # reachable, under its OWN key, for as long as it actually takes to
        # finish. Concretely, this dict now backs THREE things it
        # previously could not, for a superseded attempt specifically:
        #
        #   - `_prune_finished_futures` still sees it once `.done()` is
        #     true and still surfaces an exception that escaped `_execute`'s
        #     own never-raise guard entirely (see that method's docstring)
        #     -- for EVERY attempt, not only whichever one currently holds
        #     `job_id`'s slot in `self._active_tokens`. Before this fix
        #     such an exception was simply lost the moment `_launch`
        #     overwrote the entry -- nothing ever inspected the orphaned
        #     `Future` again.
        #   - `_drain_inflight` snapshots and waits on every attempt
        #     (current AND superseded) still tracked here; each attempt's
        #     requeue write is conditioned on ITS OWN token, read straight
        #     from this dict's key (no lookup into `self._active_tokens`
        #     needed), and simply no-ops once the row no longer matches
        #     that token -- the same "stale token, zero rows" shape every
        #     other writeback in this module already uses.
        #   - `_shutdown` sees a still-running superseded attempt as
        #     genuinely in flight (its `Future` is not yet `.done()`), so
        #     the grace-period-then-`os._exit` path applies to it too,
        #     instead of `executor.shutdown(wait=True)` blocking on an
        #     untracked thread with no bound at all -- see `_shutdown`'s
        #     own docstring for the trace this closes.
        #
        # `_execute` must still never pop its own entry here (see that
        # method's `finally` comment) -- cleanup stays `_prune_finished_
        # futures`'s job alone. With a unique key per attempt there is no
        # longer even a theoretical collision to guard against here (unlike
        # `self._active_tokens`, which stays job_id-keyed and genuinely
        # needs the token-conditional pop documented above).
        self._futures: dict[tuple[int, uuid.UUID], Future] = {}
        self._last_heartbeat_monotonic: float | None = None

        # The dedicated heartbeat thread (Q8, spec §3.4a). `None` until
        # `run_forever` starts it -- NEVER started by this constructor: a
        # single-tick diagnostic run (`manage.py run_jobs --once`) and
        # every test in this suite build a `Worker` and must spawn nothing.
        self._heartbeat_thread: threading.Thread | None = None

        # Pool size is read ONCE, here, at worker startup -- never re-read
        # per tick. `JobSettings.max_concurrent_jobs` is an operator-
        # editable knob; the ADMISSION side (`claim_and_admit`, called
        # fresh every tick) honors a mid-run change to it immediately --
        # the very next claim round simply admits up to the new cap. This
        # pool's own size cannot follow suit the same way:
        # `ThreadPoolExecutor` has no resize operation, and rebuilding one
        # mid-run would either abandon whatever is in flight or require
        # reimplementing the same drain machinery SIGTERM handling already
        # provides, just to react to a settings edit instead of a signal.
        # An operator who changes `max_concurrent_jobs` and wants the
        # WORKER POOL itself (not just admission) to reflect the new value
        # needs to restart the worker process -- a real, narrow limitation,
        # documented here rather than silently discovered later.
        max_workers = max(JobSettings.get_solo().max_concurrent_jobs, 1)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="jobs-worker")

    # --- main loop -----------------------------------------------------

    def run_forever(self) -> None:
        """Install signal handlers and loop `tick()` until a stop is
        requested (SIGTERM/SIGINT) or `tick()` itself raises, then drain --
        see `_shutdown`.

        An exception escaping `tick()` is logged HERE with its full
        traceback (never silently swallowed) and stops the loop -- `tick()`
        already isolates every per-job failure inside `_execute`'s own
        try/except (never-500), so anything that reaches this level is a
        bug in the tick machinery itself (claim/evict/launch), not a bad
        job; `_shutdown` is told this was a crash so its exit code reflects
        that (see `_shutdown`'s docstring) rather than reporting the same
        clean `0` a deliberate, signal-initiated stop would."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        self._start_heartbeat_thread()
        logger.info("worker %s: starting", self.worker_id)
        crashed = False
        try:
            while not self._stopping.is_set():
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - log fully, then stop; never silently swallow
                    logger.exception("worker %s: tick() raised -- stopping", self.worker_id)
                    crashed = True
                    break
                self._stopping.wait(POLL_INTERVAL_SECONDS)
        finally:
            self._shutdown(crashed=crashed)

    def _handle_signal(self, signum, frame) -> None:  # noqa: ARG002 - signal handler signature
        """ONLY sets the stopping flag -- no work happens inside a signal
        handler (it runs in an arbitrary interrupted context; touching the
        DB or the thread pool from here would be unsafe). The main loop
        notices the flag on its own next check and does all the actual
        draining from ordinary code."""
        logger.info("worker %s: received signal %s, stopping", self.worker_id, signum)
        self._stopping.set()

    def tick(self) -> None:
        """One iteration: refresh this process's DB connection, maybe
        write a heartbeat, claim+admit (which sweeps orphans and admits
        new jobs in one transaction -- see `models.queue.claim.
        claim_and_admit`), REGISTER the freshly-claimed batch's tokens
        (moved here from `_launch` specifically so
        this batch is heartbeat-protected DURING eviction, not only once
        launched), evict to match the plan, refresh the heartbeat again
        (eviction is synchronous HTTP and can take real wall-clock time --
        see `STALE_AFTER_SECONDS`'s comment), then launch every newly
        admitted job onto the thread pool -- UNLESS a stop was requested
        while we were busy claiming/evicting, in which case the whole
        freshly-claimed batch is handed straight back to the queue instead
        of launched (`_requeue_unlaunched`): a batch this tick decided to
        admit is not yet running anywhere, so there is nothing to drain for
        it, only a plain requeue. Never raises for a reason internal to
        one job -- `_execute` (run on the pool, not here) owns that
        boundary.

        ONE `JobSettings` READ PER TICK (S6), threaded. `claim_and_admit`
        needs `memory_budget_bytes` and `max_concurrent_jobs`;
        `_evict_to_match_plan` needs `memory_budget_bytes` again moments
        later. Both used to fetch the row themselves, so a worker paid
        two reads every `POLL_INTERVAL_SECONDS` -- the tick reads it once
        here and hands the SAME row to both, the shape
        `identity.request.settings_row_for` already names for a request.
        Both callees keep their own `get_solo()` fallback for when they
        are called on their own, so this is a query-count change and not
        a contract change. Read BEFORE the claim transaction opens, which
        is also why it is cheap: one uncontended select outside the
        advisory lock, not another statement inside the round every other
        admitter is waiting on. An operator's edit lands on the very next
        tick either way. Pinned by `models/queue/tests/test_worker.py::
        TestTheSingleSettingsReadPerTick`."""
        close_old_connections()
        self._prune_finished_futures()
        self._maybe_heartbeat()

        settings_row = JobSettings.get_solo()
        claimed = claim_and_admit(
            self.worker_id,
            stale_after_seconds=STALE_AFTER_SECONDS,
            settings_row=settings_row,
        )
        if not claimed:
            return

        with self._active_lock:
            for descriptor in claimed:
                self._active_tokens[descriptor["id"]] = descriptor["claim_token"]

        self._evict_to_match_plan(claimed, settings_row=settings_row)
        self._maybe_heartbeat()

        if self._stopping.is_set():
            self._requeue_unlaunched(claimed)
            return

        for descriptor in claimed:
            self._launch(descriptor)

    def _requeue_unlaunched(self, claimed: list[dict]) -> None:
        """Hand every descriptor in `claimed` straight back to the queue
        without ever launching it -- only reachable when `self._stopping`
        was set between claim+evict and launch (`tick()`). Pops each
        descriptor's token from `self._active_tokens` first (`tick()` now
        registers them right after claiming, before eviction, so they are
        no longer "active" once handed back to the queue instead of a
        thread -- otherwise a stale token would linger there forever,
        `_maybe_heartbeat` would keep trying to refresh a row that is
        `queued` again, and `_prune_finished_futures` would never clean it
        up since no `Future` was ever created for it). Token-conditional
        (`state=running AND claim_token=<this batch's token>`) for the
        same reason `_drain_inflight`'s requeue is: cheap defense in
        depth, even though nothing else should have had a chance to touch
        a row this tick admitted moments ago and this process has not yet
        handed to any thread. Clears `claimed_by`/`started_at`/
        `heartbeat_at` along with `claim_token`, matching `_sweep_orphans`'
        requeue shape exactly -- the two requeue paths (crash-detected and
        voluntary-drain) must leave a requeued row looking identical, not
        just agree on `state`/`claim_token`."""
        with self._active_lock:
            for descriptor in claimed:
                self._active_tokens.pop(descriptor["id"], None)
        for descriptor in claimed:
            # `progress`/`checkpoint` are deliberately absent from this
            # field list (T3) -- a batch handed back here never even
            # reached a handler this tick, so there is nothing new to
            # preserve, but the principle is the same one every OTHER
            # requeue path in this module follows: whatever either column
            # already held (from a PRIOR attempt) survives untouched,
            # because that preservation IS the resume mechanism.
            InferenceJob.objects.filter(
                pk=descriptor["id"], state=RUNNING, claim_token=descriptor["claim_token"],
            ).update(
                state=QUEUED, claimed_by="", claim_token=None,
                started_at=None, heartbeat_at=None,
            )

    def _prune_finished_futures(self) -> None:
        """Drop completed entries from `self._futures` -- otherwise a
        long-running worker accumulates one dict entry per job ATTEMPT
        forever. Safe to do at the top of a tick: `self._futures` is
        mutated only here and in `_launch`, both called only from `tick()`
        -- itself only ever running on this one main-loop thread, so there
        is no concurrent writer to race. `_drain_inflight` only ever
        reads it, and only after the tick loop has already stopped calling
        this method at all (see `_shutdown`).

        Keyed by `(job_id, claim_token)` (2026-08-25 second defect fix, see
        `self._futures`'s own declaration comment in `__init__`): a
        superseded attempt and its successor can both be present here at
        once, each under its OWN key, so this loop surfaces an escaped
        exception for EVERY attempt independently -- not only whichever one
        currently occupies `job_id`'s slot in `self._active_tokens`.

        Also surfaces a failing future (review finding, T4 round 3):
        `_execute` is DESIGNED to never raise (every step of it is its own
        guarded try/except -- see its docstring), so `future.result(...)`
        raising here means something truly unexpected escaped that
        guard entirely (a bug, not a job outcome). Without this check that
        exception would simply vanish into the `Future` object forever --
        nothing would ever log it, and the only visible symptom would be
        the job's row looking mysteriously "stuck running" until the
        orphan sweep eventually requeues it, with no explanation of why.
        `timeout=0` is non-blocking -- every future checked here is
        already `done()`, so `.result()` returns (or re-raises) instantly;
        this never waits on anything."""
        remaining: dict[tuple[int, uuid.UUID], Future] = {}
        for key, future in self._futures.items():
            job_id, _claim_token = key
            if not future.done():
                remaining[key] = future
                continue
            try:
                future.result(timeout=0)
            # noqa: BLE001 - log-and-continue; this is bookkeeping, not job
            # handling. Deliberately `Exception`, not `BaseException`: a
            # `CancelledError` (a `BaseException` in 3.8+) would escape
            # this catch entirely -- acceptable today because nothing in
            # this codebase ever calls `future.cancel()` (no `cancel()`,
            # no `cancel_futures=True` anywhere `_executor.shutdown` is
            # called); revisit this if `cancel_futures` is ever introduced
            # on a shutdown path.
            except Exception:
                logger.exception(
                    "worker: job %s's future raised past _execute's own "
                    "guard -- this is a bug (_execute is designed to never "
                    "raise); its row is left however _execute's own "
                    "writeback (or lack of one) already left it",
                    job_id,
                )
        self._futures = remaining

    # --- heartbeat -------------------------------------------------------

    def _maybe_heartbeat(self) -> None:
        """ONE UPDATE, throttled to `HEARTBEAT_SECONDS`: every row this
        worker currently has running gets a fresh `heartbeat_at`. This is
        the ONLY place any thread writes `heartbeat_at` -- job-execution
        threads (`_execute`) never touch it, so there is exactly one
        writer and no risk of a job thread's own slow write racing this
        one. It IS, however, now called from two threads -- the tick
        thread's own calls in `tick()`, and the dedicated heartbeat thread
        (`_heartbeat_forever`, Q8, spec §3.4a) -- so the throttle's own
        read-modify-write (`_last_heartbeat_monotonic`) has to be atomic
        across both, or two calls landing close together could both read
        "not throttled yet" and both go on to write. `self._active_lock`
        already exists for `self._active_tokens`; this method holds it for
        the throttle check too, so a call from either thread either wins
        outright (advances `_last_heartbeat_monotonic` and proceeds) or
        loses and returns immediately -- never both proceeding at once.

        Filtered on `claim_token__in=tokens` alone -- NOT also
        `claimed_by=self.worker_id`. `InferenceJob.claim_token`'s
        docstring is explicit that `claim_token` is "the actual claim
        mechanism" while `claimed_by` is bookkeeping alongside it, "never
        read back to decide anything about the job itself". A token this
        process holds in `self._active_tokens` is authoritative on its own
        -- adding `claimed_by` as a second, redundant predicate would just
        be a second, independently-maintained fact that could in principle
        drift from the token (it never should, but the token is the one
        fact this class is supposed to trust for a decision like this).
        """
        with self._active_lock:
            now = time.monotonic()
            if (
                self._last_heartbeat_monotonic is not None
                and now - self._last_heartbeat_monotonic < HEARTBEAT_SECONDS
            ):
                return
            self._last_heartbeat_monotonic = now
            tokens = list(self._active_tokens.values())
        if not tokens:
            return

        InferenceJob.objects.filter(
            state=RUNNING, claim_token__in=tokens,
        ).update(heartbeat_at=timezone.now())

    def _start_heartbeat_thread(self) -> None:
        """Start the dedicated heartbeat thread. Called by `run_forever`
        only -- see `__init__`'s own note on why not the constructor."""
        if self._heartbeat_thread is not None:
            return
        thread = threading.Thread(
            target=self._heartbeat_forever, name="jobs-heartbeat", daemon=True,
        )
        self._heartbeat_thread = thread
        thread.start()

    def _heartbeat_forever(self) -> None:
        """The heartbeat's own thread (Q8, spec §3.4a).

        WHY IT EXISTS: the heartbeat used to be written from the tick
        thread alone, so a tick that BLOCKS -- a synchronous eviction pass,
        or a whole process starved during a cold load measured in minutes
        -- stopped the heartbeat too, and the orphan sweep reclaimed a job
        that was perfectly healthy. The cross-engine sweep this track adds
        makes that worse before it makes it better: `_residency_snapshot`
        contains no heartbeat call at all and each `list_installed` can
        cost a full discovery timeout.

        THE CONNECTION STORY IS THE WHOLE POINT, and getting it wrong
        re-creates the bug it fixes:

        - `close_old_connections()` at the TOP of every iteration. Django
          connections are thread-local; nothing else in this process would
          ever close or health-check the one this thread opens, so without
          this it would hold a single connection open for ever and sail
          straight through a database restart.
        - the write is WRAPPED. A transient database error is logged and
          retried on the next iteration rather than killing the thread.
        - an exit is LOUD. A silently dead heartbeat writer mass-orphans
          every healthy job this worker holds, which is precisely the
          failure Q8 exists to remove.

        Waits on `self._stopping` rather than sleeping, so a SIGTERM ends
        this thread promptly instead of after one more full interval, and
        polls at half the heartbeat cadence so the writer's own throttle
        (`HEARTBEAT_SECONDS`, still shared with the tick thread's calls)
        cannot stretch the effective interval to twice the constant.

        The tick's own `_maybe_heartbeat()` calls REMAIN, harmlessly
        throttled -- that is what keeps `--once` exactly as protected as
        it is today, with no thread running at all.
        """
        logger.info("worker %s: heartbeat thread started", self.worker_id)
        try:
            # HALF the heartbeat cadence, with NO FLOOR. A floor (an
            # earlier draft had `max(1.0, ...)`) makes a test that
            # monkeypatches `HEARTBEAT_SECONDS` down to fractions of a
            # second never execute this loop body at all, so the thread's
            # own behaviour becomes unprovable. Polling at half the cadence
            # is what stops the writer's own throttle -- still shared with
            # the tick thread's calls -- from stretching the effective
            # interval to twice the constant.
            while not self._stopping.wait(HEARTBEAT_SECONDS / 2):
                try:
                    close_old_connections()
                    self._maybe_heartbeat()
                except Exception:  # noqa: BLE001 - log and retry next iteration; never die
                    logger.warning(
                        "worker %s: heartbeat write failed; retrying next iteration",
                        self.worker_id, exc_info=True,
                    )
        finally:
            logger.info(
                "worker %s: heartbeat thread exiting -- every running row this worker "
                "holds now depends on the tick thread alone", self.worker_id,
            )

    # --- launching claimed jobs -------------------------------------------

    def _launch(self, descriptor: dict) -> None:
        """Submit `descriptor` to the pool. Does NOT register the token in
        `self._active_tokens` -- `tick()` already did that, for every
        descriptor in this batch, right after `claim_and_admit` returned
        (see `tick()`'s own docstring), specifically so the batch is heartbeat-
        protected through `_evict_to_match_plan` too, not only from the
        moment it is actually launched.

        Keyed by `(job_id, claim_token)` in `self._futures` (2026-08-25
        second defect fix) -- an INSERT, never an overwrite: a reclaimed
        attempt's `claim_token` is always freshly minted by
        `claim_and_admit`, so this can never collide with -- and can never
        silently replace -- a still-tracked entry for an earlier,
        superseded attempt of the same `job_id`. See `self._futures`'s own
        declaration comment in `__init__` for what this fixes."""
        job_id = descriptor["id"]
        claim_token = descriptor["claim_token"]
        self._futures[(job_id, claim_token)] = self._executor.submit(self._execute, descriptor)

    def _build_job_context(self, descriptor: dict) -> JobContext:
        """Build the `models.contracts.jobkinds.JobContext` `_execute` passes
        as a claimed job's third handler argument -- the ONE place this
        worker constructs one, so the two writers below and the throttle
        state they share are built together, per call, from the same
        `job_id`/`claim_token` closure.

        Both writers are TOKEN-CONDITIONAL single-row UPDATEs, the same
        "stale token, zero rows, silently ignored" shape every other
        writeback in this module already uses (`_execute`'s own terminal
        UPDATE, `_requeue_unlaunched`, `_drain_inflight`) -- a job this
        worker no longer legitimately holds (drained, orphaned, reclaimed)
        must not have its progress/checkpoint columns written by a stale
        handler thread that hasn't noticed yet. Each writer touches ONLY
        its own column (`progress` or `checkpoint`) -- NEVER
        `heartbeat_at`. `_maybe_heartbeat` is this worker's ONE writer of
        that column, by design (see its own docstring); a progress/
        checkpoint write racing a heartbeat write on some OTHER thread
        would be harmless today (both are `state=RUNNING AND
        claim_token=...` conditional UPDATEs touching disjoint columns),
        but the single-writer rule for `heartbeat_at` specifically is
        policed in this codebase regardless of whether a given violation
        would actually collide -- these writers simply never mention that
        column at all, full stop.

        Progress writes are THROTTLED (`PROGRESS_INTERVAL_SECONDS`);
        checkpoint writes are NOT. The throttle's own state
        (`last_progress_monotonic`) lives in a one-element list, a plain
        mutable CELL captured by `_report`'s closure -- NOT an attribute
        of `ctx` itself, since `JobContext` is frozen and, more to the
        point, is meant to stay pure display/reporting surface with no
        worker-internal bookkeeping leaking into it (see that class's own
        docstring). `[None]` mirrors `_maybe_heartbeat`'s own "`None` means
        never written yet, so the first call always goes through"
        convention exactly.

        THE ONE-TIMEOUT SEAM (2026-09-17): `response_timeout_seconds` is
        stamped here, as a plain `float` or `None`, from `JobSettings.
        get_solo().response_timeout_seconds` -- this worker already
        legally reads queue storage, and `agents/` may not (import law),
        so this is the ONE place the operator's own setting crosses into
        the `JobContext` `agents.runtime.loop` reads it back from. One
        extra read per job execution, not per tick.

        GUARDED, NOT TRUSTED (fix round 1, B1): `_execute` -- THIS
        method's one caller -- is contracted to never raise past its own
        `try`/`finally` (see that method's own docstring); the call to
        THIS method sits OUTSIDE that guard, so anything this method lets
        escape strands the job RUNNING forever (the claim token's slot in
        `_active_tokens` is never released, `_maybe_heartbeat` keeps the
        row alive for the orphan sweep, and the pool thread's own DB
        connection leaks). `get_solo()` never raises `DoesNotExist` (it
        get-or-creates the row), but it can still raise anything else a
        DB call can (a connection blip, a not-yet-migrated column on a
        mid-deploy box) -- exactly the window `response_timeout_seconds
        =None`'s documented fallback (`agents.limits.TURN_DEADLINE_
        SECONDS`) exists for. So the read is wrapped here, not trusted:
        an unreadable settings row degrades to that same `None` fallback
        rather than ever propagating out of this method.
        """
        job_id = descriptor["id"]
        claim_token = descriptor["claim_token"]
        try:
            response_timeout_seconds = float(JobSettings.get_solo().response_timeout_seconds)
        except Exception:  # noqa: BLE001 -- never-500 parity: degrade, never strand the job
            logger.exception(
                "worker: job %s could not read the response timeout; falling back to "
                "agents.limits.TURN_DEADLINE_SECONDS", job_id,
            )
            response_timeout_seconds = None
        last_progress_monotonic: list[float | None] = [None]

        def _report(progress: dict) -> None:
            now = time.monotonic()
            if (
                last_progress_monotonic[0] is not None
                and now - last_progress_monotonic[0] < PROGRESS_INTERVAL_SECONDS
            ):
                return
            last_progress_monotonic[0] = now
            InferenceJob.objects.filter(
                pk=job_id, state=RUNNING, claim_token=claim_token,
            ).update(progress=progress)

        def _checkpoint(state: dict) -> None:
            InferenceJob.objects.filter(
                pk=job_id, state=RUNNING, claim_token=claim_token,
            ).update(checkpoint=state)

        return JobContext(
            job_id=job_id,
            attempt=descriptor.get("attempts", 0),
            checkpoint_state=descriptor.get("checkpoint"),
            _report=_report,
            _checkpoint=_checkpoint,
            response_timeout_seconds=response_timeout_seconds,
        )

    def _execute(self, descriptor: dict) -> None:
        """Runs on a pool thread: resolve and call the job kind's
        `handler`, THEN a CONDITIONAL terminal writeback keyed on
        `claim_token` (a stale token -- this job was already drained/
        orphaned/reclaimed by someone else -- updates zero rows and is
        logged, nothing else), THEN opportunistic post-execution
        measurement. `finally` closes this thread's own DB connection --
        see module docstring's second gap.

        Writeback happens BEFORE measurement, deliberately (review
        finding, T4): opportunistic work must never precede or gate the
        essential one. `_measure_and_record` is guarded by its own
        try/except here in ADDITION to being internally never-raising
        (belt and suspenders) -- a DB hiccup or engine error during
        measurement must never throw away an already-finished job's
        result or leave its row stuck `running` for another
        `STALE_AFTER_SECONDS` before the orphan sweep re-runs it from
        scratch.

        Never-500: a raising handler is caught HERE, logged with its
        traceback, and turned into a `failed` row with an operator-
        readable `str(exc)` message (never a traceback) -- one bad job
        must never kill this thread's ability to report an outcome, let
        alone the worker process itself.

        Terminal writeback asymmetry (T3, `progress`/`checkpoint`): on
        SUCCESS both columns are cleared (`progress=None,
        checkpoint=None`) -- a succeeded job's stale progress bar or
        resume state is noise, never read again by anything (a fresh
        re-run of the same kind starts a brand-new job row with its own
        `checkpoint_state=None`). On FAILURE both are deliberately left
        exactly as the handler last wrote them -- diagnostic value (an
        operator watching the Queue page can see how far a failed job
        got), and a future manual-retry path could resume from the same
        checkpoint rather than starting over.

        Third stranding path (T9.5 review M5): `get_job_kind`/
        `resolve_dotted_path`/`ModelRef(**ref)` above can themselves raise
        BEFORE the handler is ever called -- an unregistered kind, a bad
        dotted path, a malformed model-ref dict. That failure is caught by
        the SAME `except Exception` as a raising handler and produces the
        SAME `failed` job row, but the handler's own terminal writeback
        never ran (it never started), so after the FAILED job-row write
        above, this method also schedules `kind`'s registered
        `on_terminal` hook (`models.contracts.jobkinds.invoke_on_terminal`,
        state=`"failed"`), via `transaction.on_commit`, exactly like
        `models.queue.backend.cancel_job`/`models.queue.claim.
        _sweep_orphans` already do for the other two stranding paths --
        see `JobKind.on_terminal`'s own docstring for the shared contract.
        Gated on a `handler_started` flag set immediately before the
        handler call: when the handler DID run and raised, its own
        writeback (or deliberate lack of one) is the correct and complete
        account of that failure, and this hook must NOT also fire for it.
        """
        job_id = descriptor["id"]
        claim_token = descriptor["claim_token"]
        ctx = self._build_job_context(descriptor)
        try:
            result = None
            error: Exception | None = None
            handler_started = False
            try:
                kind = get_job_kind(descriptor["kind"])
                handler = resolve_dotted_path(kind.handler)
                models = [ModelRef(**ref) for ref in descriptor["model_refs"]]
                handler_started = True
                result = handler(descriptor["payload"], models, ctx)
            except Exception as exc:  # noqa: BLE001 - never-500: isolate one job's failure
                logger.exception("worker: job %s handler raised", job_id)
                error = exc

            if error is None:
                updated = InferenceJob.objects.filter(
                    pk=job_id, state=RUNNING, claim_token=claim_token,
                ).update(
                    state=SUCCEEDED, result=result, finished_at=timezone.now(),
                    progress=None, checkpoint=None,
                )
            else:
                updated = InferenceJob.objects.filter(
                    pk=job_id, state=RUNNING, claim_token=claim_token,
                ).update(state=FAILED, error=str(error), finished_at=timezone.now())

            if not updated:
                logger.warning("worker: stale writeback discarded for job %s", job_id)

            # Third stranding path (T9.5 review M5): `get_job_kind`/
            # `resolve_dotted_path`/`ModelRef(**ref)` above can all fail
            # BEFORE the handler is ever invoked -- an unregistered kind, a
            # bad dotted path, a malformed model-ref dict. That failure
            # lands here as an ordinary `error`, and the FAILED writeback
            # above covers the job row, but the handler's own terminal
            # writeback (the ONE place a kind like `rag.ingest` normally
            # reaches its Document row on failure) never ran -- so without
            # this call, a job that fails to even START its handler leaves
            # the same kind of stranded Document behind that `on_terminal`
            # already fixes for cancel/orphan-sweep (`models.contracts.
            # jobkinds.JobKind.on_terminal`'s own docstring). Only fires
            # when the handler never started (`handler_started` is False);
            # when the handler DID run and raised, its own writeback (or
            # deliberate lack of one) is the correct and complete account
            # of that failure -- calling the hook there too would be a
            # second, possibly conflicting writer. Scheduled via
            # `transaction.on_commit`, same discipline `models.queue.
            # backend.cancel_job`/`models.queue.claim._sweep_orphans` use
            # for this exact hook (their own docstrings explain why --
            # `on_commit` fires immediately here since `_execute` runs
            # outside any ambient transaction, but scheduling it this way
            # costs nothing and stays consistent if that ever changes).
            # `invoke_on_terminal` itself is fully tolerant (unregistered
            # kind, no hook registered, or the hook raising are all caught
            # and logged there) -- nothing further to guard here.
            if error is not None and not handler_started:
                transaction.on_commit(
                    lambda kind=descriptor["kind"], payload=descriptor["payload"]: invoke_on_terminal(
                        kind, payload, "failed"
                    )
                )

            try:
                self._measure_and_record(descriptor["model_refs"])
            except Exception:  # noqa: BLE001 - opportunistic; must never undo a finished writeback
                logger.exception(
                    "worker: post-execution measurement raised for job %s", job_id
                )
        finally:
            # Token-conditional pop (2026-08-25 defect fix, see the
            # invariant documented next to `self._active_tokens`'s
            # declaration in `__init__`, and ADR 0013's dated amendment):
            # this attempt's own `claim_token` must still be the one
            # recorded for `job_id` -- if this process has ALREADY
            # re-claimed `job_id` under a newer token (this attempt was
            # orphaned and reclaimed while still running), `.get(job_id)`
            # will hold that NEWER token, not `claim_token`, and this
            # attempt must leave it alone: popping unconditionally here
            # would evict the new attempt's own token, silently breaking
            # `_maybe_heartbeat` for a job that is still genuinely running
            # under this same process.
            #
            # `self._futures[(job_id, claim_token)]` is deliberately NOT
            # touched here (not even token-conditionally) -- it must stay
            # exactly as `_launch` last left it, popped ONLY by
            # `_prune_finished_futures`'s own `future.done()` check.
            # Popping it here too would beat `_prune_finished_futures` to
            # it: that method's whole reason to exist (T4 round 3 review
            # finding) is calling `future.result(timeout=0)` on an entry
            # still present in `self._futures` to surface an exception
            # that escaped `_execute`'s own guard entirely -- since
            # Python's `finally` always runs before such an exception
            # actually propagates out to the `Future`, an unconditional
            # self-removal here would make that entry vanish from
            # `self._futures` before `_prune_finished_futures` ever gets a
            # chance to inspect it, silently defeating the exact safety
            # net it exists to provide -- including, since the 2026-08-25
            # second defect fix, for a SUPERSEDED attempt specifically:
            # `self._futures` is now keyed by `(job_id, claim_token)`, so
            # THIS attempt's own entry is unique to it regardless of
            # whether a newer attempt has since been launched for the same
            # `job_id` (see that dict's own declaration comment in
            # `__init__`). There is no collision with a newer attempt's
            # entry to avoid here anymore -- only the same "leave it for
            # `_prune_finished_futures`" rule as ever, now honored for
            # every attempt independently rather than only the current one.
            with self._active_lock:
                if self._active_tokens.get(job_id) == claim_token:
                    self._active_tokens.pop(job_id, None)
            db_connection.close()

    def _measure_and_record(self, model_refs: list[dict]) -> None:
        """Opportunistic post-execution measurement: for each DISTINCT
        `(engine, endpoint, model_id)` this job declared, ask the engine
        (via the OPTIONAL `loaded_footprint`, read with `getattr` per
        `models.contracts.engines.base.InferenceEngine`'s degradation idiom)
        how much memory that model occupies right now, and persist it via
        `models.registry.bindings.record_measured_footprint` (same
        silent-no-op-on-ambiguous-match rule as `footprint_for`). Genuinely
        never raises -- not merely "usually doesn't": every subscript
        access into one `ref` dict, every engine call, and the record call
        itself are each guarded, so one malformed ref or one misbehaving
        engine degrades to "skip this one ref/measurement", never escapes
        to the caller (`_execute`, which also wraps this call defensively
        -- belt and suspenders, see its own docstring).

        `loaded_footprint` is called with `norm_tag(model_id)` -- an
        engine's `/api/ps`-style report always names a loaded model with
        its explicit tag (`an-embedding-model:latest`), while a job's own
        ref (and the `ModelConnection` row it will be recorded against)
        may carry the bare form (`an-embedding-model`); passing the raw,
        un-normalized `model_id` here would silently never match a live
        model reported under its tagged spelling (verified against a real
        Ollama `/api/ps` response), the exact bare/tagged mismatch
        `models.registry.discovery`'s module docstring already describes
        for `discover()`'s own merge. `record_measured_footprint`, in
        contrast, is called with the RAW `model_id` -- it matches
        `ModelConnection.model_id` EXACTLY (mirroring `footprint_for`'s own
        exact, never-tag-normalized match rule), so normalizing here would
        make it MISS the very row it is meant to update.
        """
        seen: set[tuple[str, str, str]] = set()
        for ref in model_refs:
            try:
                engine_name = ref["engine"]
                endpoint = ref["endpoint"]
                model_id = ref["model_id"]
            except (TypeError, KeyError):
                logger.warning("worker: malformed model ref skipped during measurement: %r", ref)
                continue

            key = (engine_name, endpoint, model_id)
            if key in seen:
                continue
            seen.add(key)

            try:
                engine_obj = get_engine(engine_name)
            except ValueError:
                continue

            loaded_footprint = getattr(engine_obj, "loaded_footprint", None)
            if loaded_footprint is None:
                continue

            try:
                size = loaded_footprint(endpoint, norm_tag(model_id))
            except Exception:  # noqa: BLE001 - a measurement must never fail the job
                logger.warning(
                    "worker: loaded_footprint measurement raised for %s at %s",
                    model_id, endpoint,
                )
                continue

            if not size:
                continue

            try:
                record_measured_footprint(engine_name, endpoint, model_id, size)
            except Exception:  # noqa: BLE001 - the record call itself must never raise either
                logger.exception(
                    "worker: record_measured_footprint raised for %s at %s", model_id, endpoint
                )

    # --- pre-launch eviction -----------------------------------------------

    def _eviction_targets(
        self, claimed: list[dict]
    ) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]], set[tuple[str, str]]] | None:
        """PHASE 1 of `_evict_to_match_plan` (which see for the whole
        argument): what the machine is supposed to be holding.

        `(needed_keys, endpoints, exclusive_endpoints)`, or `None` when
        no job is RUNNING -- the caller returns immediately on `None`,
        which is what stops the engine being probed for nothing.

        `needed_keys`/`endpoints` come from RUNNING jobs' `model_refs`.
        By the time this runs, `claim_and_admit` has already persisted
        THIS tick's admissions as `running`, so one query covers
        "running union admitted" without merging two collections.
        `exclusive_endpoints` comes from `claimed` instead -- an
        admitted-and-exclusive job's own endpoints, derived from THIS
        tick's batch and never recomputed later."""
        running_refs = list(
            InferenceJob.objects.filter(state=RUNNING).values_list("model_refs", flat=True)
        )
        if not running_refs:
            return None

        needed_keys: set[tuple[str, str, str]] = set()
        endpoints: set[tuple[str, str]] = set()
        for model_refs in running_refs:
            for ref in model_refs:
                key = (ref["engine"], norm_endpoint(ref["endpoint"]), norm_tag(ref["model_id"]))
                needed_keys.add(key)
                endpoints.add((key[0], key[1]))

        exclusive_endpoints: set[tuple[str, str]] = {
            (ref["engine"], norm_endpoint(ref["endpoint"]))
            for descriptor in claimed
            if descriptor.get("exclusive")
            for ref in descriptor["model_refs"]
        }
        return needed_keys, endpoints, exclusive_endpoints

    def _residency_snapshot(
        self,
        endpoints: set[tuple[str, str]],
        claimed: list[dict],
        budget_bytes: int,
    ) -> tuple[dict[tuple[str, str], list], bool]:
        """PHASE 2 of `_evict_to_match_plan` (which see): what the
        machine is ACTUALLY holding, and whether that plus what is about
        to load exceeds the budget.

        `(installed_by_endpoint, over_budget)`. An endpoint is in the
        dict ONLY if its engine resolved, offered `list_installed`, and
        that call returned -- so an engine that lacks the method (warned
        once, per engine+method) or whose call raised (logged; eviction
        must never block a launch) is absent from the dict and is
        therefore untouched by both eviction passes.

        `over_budget` is `actual_resident_bytes + admitted_marginal >
        budget_bytes`. Both halves keep their exact prior arithmetic --
        see the two inline comments below, which are the reasoning for
        the deliberate under-count and for the MAX fold, and are the
        parts of this function most likely to be 'tidied' into a bug."""
        installed_by_endpoint: dict[tuple[str, str], list] = {}
        resident_sizes: dict[tuple[str, str, str], int | None] = {}
        for engine_name, endpoint in endpoints:
            engine_obj = self._get_engine_or_none(engine_name)
            if engine_obj is None:
                continue
            list_installed = getattr(engine_obj, "list_installed", None)
            if list_installed is None:
                self._warn_missing_method_once(engine_name, "list_installed")
                continue
            try:
                installed = list_installed(endpoint)
            except Exception:  # noqa: BLE001 - eviction must never block a launch
                logger.warning(
                    "worker: eviction could not list installed models at %s (%s)",
                    endpoint, engine_name,
                )
                continue
            installed_by_endpoint[(engine_name, endpoint)] = installed
            for model in installed:
                if not model.loaded:
                    continue
                key = (engine_name, norm_endpoint(endpoint), norm_tag(model.model_id))
                resident_sizes[key] = getattr(model, "loaded_size", None)

        # `resident_sizes[key]` is `None` for a model `list_installed`
        # reports as loaded but with no size attached (an adapter/engine
        # that doesn't report one) -- summing only the known values below
        # means such a model contributes 0 bytes here, UNDER-counting real
        # resident memory rather than over-counting it. Accepted, not
        # fixed: this is a best-effort, opportunistic pass (never a
        # safety-critical fence -- see this function's own docstring), and
        # the scheduler's rule 2b (`models.queue.scheduler
        # .effectively_exclusive`) already makes any RUNNING job with an
        # unknown-footprint model run alone, so the ordinary case where
        # this asymmetry could matter (concurrently admitting alongside an
        # unmeasured-size model) is already excluded by admission itself,
        # not by this function. Worst case here is one skipped eviction
        # this tick; the size becomes known (or the next tick's own budget
        # check re-evaluates it) soon enough that this is not a persistent
        # gap.
        actual_resident_bytes = sum(size for size in resident_sizes.values() if size is not None)

        # Dedup'd by key, MAX-folded across every admitted job's own
        # reading -- the same fold `models.queue.scheduler`'s rule 4 uses
        # for resident-set accounting, so two admitted jobs sharing one
        # not-yet-resident model are charged for it ONCE, at its largest
        # known size, not once each (which would double-count the very
        # memory pressure this arithmetic exists to estimate).
        admitted_new_keys: dict[tuple[str, str, str], int] = {}
        for descriptor in claimed:
            for ref in descriptor["model_refs"]:
                key = (ref["engine"], norm_endpoint(ref["endpoint"]), norm_tag(ref["model_id"]))
                if key in resident_sizes:
                    continue
                size = ref.get("footprint_bytes") or 0
                admitted_new_keys[key] = max(admitted_new_keys.get(key, 0), size)
        admitted_marginal = sum(admitted_new_keys.values())
        over_budget = actual_resident_bytes + admitted_marginal > budget_bytes
        return installed_by_endpoint, over_budget

    def _evict_exclusive_endpoints(
        self,
        exclusive_endpoints: set[tuple[str, str]],
        installed_by_endpoint: dict[tuple[str, str], list],
        needed_keys: set[tuple[str, str, str]],
    ) -> None:
        """PASS 1 of `_evict_to_match_plan` (which see for the full
        argument): every non-needed resident model at an endpoint an
        admitted-EXCLUSIVE job owns is unloaded, full stop.

        UNCAPPED, deliberately -- NOT subject to `MAX_UNLOADS_PER_TICK`.
        The safety mechanism is not the cap: `tick()` registers this
        batch's tokens in `self._active_tokens` BEFORE calling the
        caller at all, so `self._maybe_heartbeat()` -- called after every
        unload attempt in the loop below, not once around it --
        genuinely refreshes the exclusive job's row DURING this pass.
        Removing that call, or hoisting it out of the loop, reintroduces
        the stale-row window this pass was fixed to close."""
        # Pass 1: exclusive-endpoint eviction -- UNCAPPED (see this
        # function's docstring for why). Every non-needed resident model
        # at an endpoint an admitted-exclusive job owns is unloaded,
        # full stop.
        for engine_name, endpoint in exclusive_endpoints:
            installed = installed_by_endpoint.get((engine_name, endpoint))
            if installed is None:
                continue
            engine_obj = self._get_engine_or_none(engine_name)
            if engine_obj is None:
                continue
            unload = getattr(engine_obj, "unload", None)
            if unload is None:
                self._warn_missing_method_once(engine_name, "unload")
                continue

            for model in installed:
                if not model.loaded:
                    continue
                key = (engine_name, endpoint, norm_tag(model.model_id))
                if key in needed_keys:
                    continue
                if not unload(endpoint, model.model_id):
                    logger.warning(
                        "worker: eviction unload refused for %s at %s (%s)",
                        model.model_id, endpoint, engine_name,
                    )
                # Uncapped pass -- refresh the heartbeat after every
                # unload attempt, not just once before/after the whole
                # pass. `_maybe_heartbeat`'s own `HEARTBEAT_SECONDS`
                # throttle bounds this to at most one actual UPDATE every
                # 10s regardless of how many times it's called here (still
                # single-writer, still this same tick thread) -- this is
                # what makes an uncapped exclusive pass of any length safe
                # (see this function's docstring, point (c)).
                self._maybe_heartbeat()

    def _evict_for_budget(
        self,
        installed_by_endpoint: dict[tuple[str, str], list],
        exclusive_endpoints: set[tuple[str, str]],
        needed_keys: set[tuple[str, str, str]],
    ) -> None:
        """PASS 2 of `_evict_to_match_plan` (which see): non-exclusive,
        budget-driven eviction, CAPPED at `MAX_UNLOADS_PER_TICK` unload
        calls per call.

        Called only when phase 2 said `over_budget` -- the caller makes
        that decision, so this method's own loop no longer re-checks a
        flag that cannot change inside it.

        `unloads_this_tick` is ONE counter across the endpoint loop and
        the model loop, with a `break` in each: the cap bounds total
        unload calls, not calls per endpoint. This runs on the heartbeat
        thread, and an unbounded run of slow `unload()` calls would eat
        the margin `STALE_AFTER_SECONDS` assumes. Whatever this cap
        leaves undone is picked up on a later tick that itself admits
        something -- not necessarily the next one."""
        # Pass 2: non-exclusive, budget-driven eviction -- capped at
        # MAX_UNLOADS_PER_TICK (see that constant's comment). Exclusive
        # endpoints are skipped here -- pass 1 above already handled them,
        # uncapped.
        unloads_this_tick = 0
        for (engine_name, endpoint), installed in installed_by_endpoint.items():
            if (engine_name, endpoint) in exclusive_endpoints:
                continue
            if unloads_this_tick >= MAX_UNLOADS_PER_TICK:
                break
            engine_obj = self._get_engine_or_none(engine_name)
            if engine_obj is None:
                continue
            unload = getattr(engine_obj, "unload", None)
            if unload is None:
                self._warn_missing_method_once(engine_name, "unload")
                continue

            for model in installed:
                if unloads_this_tick >= MAX_UNLOADS_PER_TICK:
                    break
                if not model.loaded:
                    continue
                key = (engine_name, endpoint, norm_tag(model.model_id))
                if key in needed_keys:
                    continue
                unloads_this_tick += 1
                if not unload(endpoint, model.model_id):
                    logger.warning(
                        "worker: eviction unload refused for %s at %s (%s)",
                        model.model_id, endpoint, engine_name,
                    )

    def _evict_to_match_plan(
        self, claimed: list[dict], *, settings_row: JobSettings | None = None,
    ) -> None:
        """Admission (`models.queue.claim.claim_and_admit`) plans against
        RUNNING jobs' declared footprints; this function makes the
        machine's ACTUAL resident memory match that plan before any newly
        admitted job's handler starts -- called once per tick, right
        after `claim_and_admit`, before any of `claimed` is launched.

        `settings_row` (S6) is `tick()`'s own already-fetched row,
        threaded in so the tick pays ONE `JobSettings` read rather than
        one here and another inside `claim_and_admit` moments earlier off
        the same 0.5s loop -- the pattern
        `identity.request.settings_row_for` already names, applied to a
        tick instead of a request. `None` falls back to `get_solo()`, so
        this method stays callable on its own (`TestEviction` calls it
        directly throughout) and eviction and admission still read the
        same budget when they are called separately.

        A no-op in sequential mode (`memory_budget_bytes is None`): with no
        budget, admission never runs more than one job at a time, so there
        is nothing to evict for. Otherwise:

        1. `needed_keys`/`endpoints` -- every model key (and its engine,
           endpoint) belonging to a currently-`running` job. By the time
           this runs, `claim_and_admit` has already persisted this tick's
           admissions as `running`, so one query covers "running ∪
           admitted" without needing to merge two separate collections.
        2. For each needed endpoint, read ACTUAL residency via the
           engine's OPTIONAL `list_installed` (loaded flags) -- ground
           truth, never a job row's merely-declared footprint.
        3. Any RESIDENT model at a needed endpoint whose key is NOT in
           `needed_keys` is a candidate for eviction. It is evicted
           unconditionally if it sits at an endpoint an admitted-and-
           EXCLUSIVE job owns (that job needs the machine, at its
           endpoints, to itself); otherwise only if keeping it around
           would exceed budget: `actual_resident_bytes + admitted_marginal
           > budget_bytes`, where `admitted_marginal` is this tick's
           admitted jobs' own footprint total for keys not already
           actually resident (the memory they are ABOUT to consume once
           their handler starts, which `list_installed` cannot see yet),
           dedup'd/MAX-folded by key the same way `models.queue.scheduler`
           dedups a resident set (rule 4).

        Exclusive-endpoint eviction (an admitted-EXCLUSIVE job's own
        endpoints) is UNCAPPED -- deliberately NOT subject to
        `MAX_UNLOADS_PER_TICK` (review finding, T4 round 3). Reasoning:
        (a) this function is only ever called from `tick()`'s
        `if not claimed: return` branch, so it never runs at all on a
        tick that admits nothing; (b) once an exclusive job IS admitted,
        `models.queue.scheduler` rule 3 blocks every other admission for
        as long as it runs, so there is no future ADMITTING tick for a
        capped leftover to be "picked up" on; and (c) `exclusive_endpoints`
        is derived from THIS tick's own `claimed` batch, never
        recomputed later, so a capped eviction here would leave unneeded
        models permanently resident alongside a job that is supposed to
        have its endpoints entirely to itself, for the rest of that job's
        run. This is safe to leave uncapped for the REAL reason (an earlier
        version of this docstring claimed the row's "freshly stamped"
        heartbeat alone was protection enough, which was never actually
        true on its own): `tick()`
        registers this batch's tokens in `self._active_tokens` BEFORE
        calling this function at all (moved there specifically for this),
        so `self._maybe_heartbeat()`, called again inside this pass's own
        model loop below, genuinely refreshes the exclusive job's row
        DURING an uncapped, potentially long-running pass -- not just once,
        before or after it. The loop is additionally bounded in practice by
        however many models can physically be loaded at one endpoint (a
        handful, not an adversarial N), but that is a secondary comfort,
        not the actual safety mechanism.

        Non-exclusive, BUDGET-DRIVEN eviction (every other endpoint) IS
        capped at `MAX_UNLOADS_PER_TICK` `unload()` calls per call to this
        function -- this runs synchronously on the SAME thread `tick()`
        calls it from (the heartbeat thread), and an unbounded loop of
        slow/wedged `unload()` calls would eat directly into the margin
        `STALE_AFTER_SECONDS` assumes (see that constant's comment);
        `tick()` also writes a fresh heartbeat again immediately after
        this function returns, before launching anything, precisely to
        bound how much of that margin this function's own wall-clock time
        can consume. Whatever budget-driven eviction this cap leaves
        undone is picked up on a LATER tick -- but only one that itself
        admits something (see `MAX_UNLOADS_PER_TICK`'s own comment); it is
        not guaranteed to be the very next one.

        Every step degrades, never raises: an engine lacking
        `list_installed`/`unload` (read via `getattr`, per the engine
        seam's own degradation idiom) is logged ONCE per engine+method and
        left alone -- today's idle-timeout-only behavior for it;
        `unload()` returning `False` is logged and the loop proceeds.

        The guiding principle, in one line: admission plans against
        running jobs; eviction makes the machine match the plan.

        FOUR PHASES, FOUR METHODS (C-48a). This function is the ORDER; each
        phase's own reasoning lives on its own method's docstring. Nothing
        about the sequence is optional: phase 1 answering `None` is what stops
        phase 2 probing an engine for nothing, phase 2's `over_budget` is what
        gates pass 2 and nothing else, and pass 1 must run before pass 2 so an
        exclusive endpoint is emptied uncapped rather than nibbled at under
        the cap.
        """
        row = settings_row if settings_row is not None else JobSettings.get_solo()
        budget_bytes = row.memory_budget_bytes
        if budget_bytes is None:
            return

        targets = self._eviction_targets(claimed)
        if targets is None:
            return
        needed_keys, endpoints, exclusive_endpoints = targets

        installed_by_endpoint, over_budget = self._residency_snapshot(
            endpoints, claimed, budget_bytes)

        self._evict_exclusive_endpoints(exclusive_endpoints, installed_by_endpoint, needed_keys)

        if over_budget:
            self._evict_for_budget(installed_by_endpoint, exclusive_endpoints, needed_keys)

    _warned_missing_methods: set[tuple[str, str]] = set()

    @classmethod
    def _warn_missing_method_once(cls, engine_name: str, method: str) -> None:
        key = (engine_name, method)
        if key in cls._warned_missing_methods:
            return
        cls._warned_missing_methods.add(key)
        logger.info(
            "worker: engine %r has no %s() -- eviction degrades to today's "
            "idle-timeout behavior for it", engine_name, method,
        )

    @staticmethod
    def _get_engine_or_none(engine_name: str):
        try:
            return get_engine(engine_name)
        except ValueError:
            return None

    # --- shutdown / drain --------------------------------------------------

    def _drain_inflight(self, timeout: float) -> list[int]:
        """Wait up to `timeout` seconds for every currently in-flight job
        ATTEMPT to finish on its own; any still running after that is
        requeued (state=queued, claim_token=NULL) and its id returned.

        Snapshots `self._futures.items()` under `_active_lock` -- but the
        lock is NOT what keeps `self._futures` itself consistent here
        (2026-08-25 second defect fix; a prior version of this docstring
        implied otherwise). `self._futures` is mutated only by `_launch`
        and `_prune_finished_futures`, both called only from `tick()`,
        which runs on ONE thread -- and this method's only real caller,
        `_shutdown`, runs from `run_forever`'s `finally` AFTER that same
        thread has already stopped ticking for good. By the time this
        method runs there is no concurrent mutator of `self._futures` left
        at all; single-thread discipline is the actual guarantee. The lock
        is vestigial; the lock acquisition is kept as cheap defense in
        depth (this method no longer reads `_active_tokens`, and
        `_futures` is mutated only by single-threaded callers).

        TOKEN-CONDITIONAL (review finding, T4): the requeue UPDATE is
        `filter(pk=job_id, state=RUNNING, claim_token=token)`, where
        `token` is THIS ATTEMPT's own token, read straight from its
        `self._futures` key (2026-08-25 second defect fix: `self._futures`
        is keyed by `(job_id, claim_token)` -- see that dict's own
        declaration comment in `__init__` -- so every attempt this process
        ever launched, current or superseded, carries its own token right
        alongside its own `Future`; no separate lookup into
        `self._active_tokens` is needed, or even possible for a superseded
        attempt, whose token `self._active_tokens` no longer holds at
        all). NEVER a bare `filter(pk=job_id, state=RUNNING)`. Trace this
        closes: worker A's main tick loop stalls for over
        `STALE_AFTER_SECONDS` (e.g. `_evict_to_match_plan` wedged on a
        slow/uncapped engine call, before `MAX_UNLOADS_PER_TICK` existed)
        while job 7 is still legitimately running on one of A's pool
        threads -- A's own heartbeat writer runs on that SAME stalled main
        thread, so job 7's row goes stale from every OTHER worker's point
        of view even though it is not actually abandoned. Worker B's
        orphan sweep (correctly, from what it can see) requeues-and-
        immediately-reclaims job 7 under a NEW token T2, while A's
        original pool thread is still, unaware, running the OLD job under
        T1. If A's stall then ends and it receives SIGTERM, a bare
        `state=RUNNING` requeue (the pre-review code) would match B's row
        (still `RUNNING`, just now under T2) and clobber B's legitimate,
        in-progress claim out from under it -- B's own heartbeat writes
        would then start no-op'ing (its token no longer matches anything
        `RUNNING`), the row would falsely look orphaned a SECOND time and
        get permanently `failed`, and whatever A's still-running thread
        eventually does with job 7 becomes a THIRD, completely
        unsupervised execution. Conditioning on A's own token (T1) means
        this UPDATE matches zero rows the instant somebody else (B) has
        already reclaimed the row -- exactly the same protection every
        OTHER terminal write in this module already has (`_execute`'s
        writeback, `_requeue_unlaunched`'s requeue).

        `claim_token=NULL` neuters that job's eventual (if it ever
        actually returns) late writeback -- `_execute`'s conditional
        UPDATE is keyed on the exact claim_token it was launched with, so
        once this clears it, that thread's writeback will match zero rows
        and log "stale writeback discarded", same as any other stale
        writeback. `claimed_by`/`started_at`/`heartbeat_at` are cleared
        too, matching `_sweep_orphans`' requeue shape exactly -- the
        crash-detected and voluntary-drain requeue paths must leave an
        identical-looking row behind, not merely agree on `state`/
        `claim_token`.

        `attempts` is deliberately left UNCHANGED here -- unlike the
        orphan sweep (`claim._sweep_orphans`), which bumps `attempts` on a
        requeue because that job's worker went silent/died without any
        sign of an orderly shutdown. A DRAINED job is not a CRASHED job:
        this worker knows exactly what happened to it (an operator asked
        this process to stop) and is handing it back to the queue in good
        faith, not spending one of its retry attempts on a worker that
        behaved correctly.

        Returns only the ids this call actually requeued (a matching,
        successful UPDATE) -- NOT every `job_id` with a still-not-`done()`
        future. A superseded attempt's own future can still be genuinely
        unfinished past `timeout` with nothing here for this method to
        requeue at all (its row already moved on to a newer attempt, so
        its token-conditional UPDATE matches zero rows); `_shutdown` (the
        real caller) checks `self._futures` itself, separately, to decide
        whether anything is still running -- see that method's own
        docstring for why the two checks must not be conflated.

        Contains no `os._exit` / process-exit logic itself -- that lives
        in `_shutdown`, the only real caller outside tests -- so this
        method is safe to call directly from a test with a short timeout
        and assert on.
        """
        with self._active_lock:
            futures_snapshot = list(self._futures.items())
        if not futures_snapshot:
            return []

        _done, not_done = concurrent.futures.wait(
            [future for _, future in futures_snapshot], timeout=timeout
        )

        requeued: list[int] = []
        for (job_id, token), future in futures_snapshot:
            if future not in not_done:
                continue
            # `progress`/`checkpoint` are deliberately absent from this
            # field list (T3) -- exactly like `_requeue_unlaunched`'s own
            # requeue: a drained job's PRESERVED checkpoint is what a
            # later attempt's `JobContext.checkpoint_state` resumes from,
            # not something this voluntary, orderly requeue path should
            # ever discard.
            updated = InferenceJob.objects.filter(
                pk=job_id, state=RUNNING, claim_token=token,
            ).update(
                state=QUEUED, claimed_by="", claim_token=None,
                started_at=None, heartbeat_at=None,
            )
            if updated:
                requeued.append(job_id)
                logger.warning(
                    "worker %s: job %s still running after %ss grace -- requeued "
                    "(claim_token cleared; attempts unchanged, a drained job is "
                    "not a crashed job)",
                    self.worker_id, job_id, timeout,
                )
        return requeued

    def _shutdown(self, *, crashed: bool = False) -> None:
        """Called once, from `run_forever`'s `finally`, after either the
        stop flag is set (an ordinary signal-initiated stop) or `tick()`
        raised (`crashed=True`). Drains for `SHUTDOWN_GRACE_SECONDS`; if
        everything finished on its own AND this was not a crash, shuts the
        pool down normally (`wait=True`) and returns -- the only path that
        lets the interpreter exit on its own, with exit code 0.

        Every other path calls `os._exit`, with a DELIBERATE exit code
        (review finding, T4): `os._exit(1)` whenever `crashed=True`,
        regardless of whether anything is still running -- an exception
        escaping `tick()` is a real failure an orchestrator (docker's
        restart policy, a process supervisor) needs to see as one, not a
        deceptively clean `0` just because whatever jobs happened to be in
        flight finished draining anyway. `os._exit(0)` is for the
        remaining case: an ORDERLY, signal-initiated stop that still had
        to abandon something past the grace period -- the worker did
        exactly what was asked (stop), it simply couldn't wait forever for
        one slow handler; that is not an error condition.

        "Still running" is checked HERE, directly against
        `self._futures`, NOT by treating `_drain_inflight`'s own return
        value as the complete account of what's still in flight
        (2026-08-25 second defect fix). A SUPERSEDED attempt's `Future`
        can still be genuinely un-`done()` past the grace period with
        nothing for `_drain_inflight` to requeue at all -- its row already
        moved on to a newer attempt (a different `claim_token`, or a
        terminal `state`), so its own token-conditional UPDATE matches
        zero rows and it never appears in that method's return value, even
        though its pool thread is unambiguously still running. Before this
        fix, such an attempt was not tracked in `self._futures` AT ALL
        (keyed by bare `job_id`, silently overwritten by the reclaiming
        attempt's own `_launch` call -- see that dict's declaration
        comment in `__init__`), so `_drain_inflight`'s `wait()` never even
        looked for it, its return value came back with nothing to report,
        and this method took the `executor.shutdown(wait=True)` branch
        below -- which then BLOCKED on that same untracked thread,
        unbounded, for however long its handler actually took to finish:
        exactly the "SIGKILL after the grace period elapses anyway"
        failure this module exists to avoid (see the next paragraph).
        Keying `self._futures` by `(job_id, claim_token)` means every
        attempt this process ever launched stays in this dict, under its
        own key, until `_prune_finished_futures` removes it -- so this
        direct check now sees a superseded attempt's thread too, and takes
        the bounded `os._exit` path for it instead.

        Why `os._exit` at all, not a plain return, whenever something is
        still running: `ThreadPoolExecutor` registers an `atexit` hook
        that JOINS every one of its worker threads before the interpreter
        is allowed to actually exit -- if a handler thread is still
        running past the grace period (wedged on a slow model call, or a
        superseded attempt nobody signaled), a normal return here would
        leave this process hanging at interpreter shutdown for however
        long that handler takes to return, which could be unbounded.
        Python provides no supported way to forcibly stop a thread, so
        once a still-current job has already been requeued above (its
        claim_token cleared, its eventual writeback already neutered) --
        or, for a superseded attempt, was never this worker's row to
        requeue in the first place -- abandoning that thread and exiting
        immediately is the only way to keep this process's OWN shutdown
        bounded by `SHUTDOWN_GRACE_SECONDS` rather than "eventually". This
        is the one place this worker deliberately skips graceful
        interpreter teardown.
        """
        requeued = self._drain_inflight(SHUTDOWN_GRACE_SECONDS)

        # `self._stopping` is already set on the ordinary, signal-initiated
        # path (`_handle_signal`) -- `.set()` here is what covers the
        # `crashed=True` path, where `tick()` raised without a signal ever
        # arriving. Only after it is set does the heartbeat thread's own
        # `_stopping.wait()` loop (`_heartbeat_forever`) return, so the
        # join below is what actually observes it exit. A short timeout,
        # not `None`: this is a daemon thread, so a join that times out
        # here can never hold the process open -- the thread simply gets
        # abandoned along with everything else `os._exit` below abandons.
        self._stopping.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2.0)

        # Lock acquisition is vestigial here too, kept as cheap defense in depth.
        with self._active_lock:
            still_running = [
                job_id for (job_id, _token), future in self._futures.items()
                if not future.done()
            ]

        if not still_running and not crashed:
            self._executor.shutdown(wait=True)
            logger.info("worker %s: drained cleanly, exiting", self.worker_id)
            return

        if still_running:
            logger.warning(
                "worker %s: exiting with %d job attempt(s) still running past "
                "the %ss grace period (requeued: %s)",
                self.worker_id, len(still_running), SHUTDOWN_GRACE_SECONDS, requeued,
            )
        os._exit(1 if crashed else 0)
