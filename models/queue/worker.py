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
from datetime import timedelta

from django.db import close_old_connections
from django.db import connection as db_connection
from django.db import transaction
from django.db import OperationalError, ProgrammingError
from django.utils import timezone

from models.registry.bindings import (
    record_engine_reported_footprint,
    record_measured_footprint,
    registered_endpoints,
)
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

# A tick whose WALL-CLOCK delta exceeds its MONOTONIC delta by this many
# seconds did not take that long -- the host (or the container VM) was
# suspended. Monotonic clocks on this platform do not advance across a
# sleep; wall clock does. 60s is far beyond any scheduling delay a 0.5s
# tick loop could accumulate and far below the shortest sleep worth
# noticing. A false positive (a genuinely slow, non-sleep tick that
# somehow drifted this far) costs exactly one skipped orphan sweep --
# deliberately the cheap direction (spec §11): a job that is actually
# dead waits one extra tick to be reclaimed, versus a job that is alive
# being wrongly mass-orphaned.
SLEEP_DETECT_SECONDS = 60

# How long after a detected sleep the orphan sweep is skipped, so every
# live worker's rows can re-stamp themselves before anything judges them.
# Comfortably more than `HEARTBEAT_SECONDS` and less than
# `STALE_AFTER_SECONDS`: long enough for a heartbeat to land, short enough
# that a genuinely dead job is still reclaimed promptly.
SLEEP_GRACE_SECONDS = 30

# How many times, and how long apart, the CONSTRUCTOR waits for a racing
# `migrate` before giving up and sizing the pool from the documented
# default. Deliberately small: a worker that cannot read its settings row
# after this long is better off running at the default cap than blocking a
# compose boot, and the tick loop tolerates the same failure independently.
BOOT_SCHEMA_WAIT_ATTEMPTS = 10
BOOT_SCHEMA_WAIT_SECONDS = 3.0

# The two values each optional engine declaration may carry (see
# `models.contracts.engines.base.InferenceEngine`'s own seam comment).
# Anything else -- absent, misspelled, a value invented by a future
# adapter this worker predates -- reads as the SAFE member of its pair,
# which is the LAST element of each tuple here.
_UNLOAD_SCOPES = ("model", "endpoint")
_RESIDENCY_AUTHORITIES = ("endpoint", "memo")

# How long a barrier-refused (or protection-refused) job waits before it
# may be claimed again, written to `InferenceJob.not_before`. COMFORTABLY
# LONGER THAN ONE UNLOAD TIMEOUT (30s on the engine that polls), so a
# refused job is not re-claimed on every 0.5s tick and the retries the
# refusal bound counts are genuinely spaced.
BARRIER_HOLDOFF_SECONDS = 45

# How many INFORMATIVE barrier refusals a job may accumulate before it is
# failed -- and see the span below, which must ALSO be satisfied. A
# PROTECTION refusal is deliberately not counted here: that wait is
# bounded by the live attempt's own end, and since an agent turn is
# planned exclusive, counting it would fail three consecutive chat turns
# for an ordinary long-running foreign job at a shared endpoint.
MAX_BARRIER_REFUSALS = 3

# ...because counting refusals alone is a trap. An informative `False` is
# exactly what a BUSY engine returns (the adapter withholds True while a
# prompt is still running), so three refusals could elapse in barely more
# than the time three unload calls take. A job is failed only once BOTH
# bounds are met, so "three attempts" can never mean "a second and a half".
MIN_BARRIER_REFUSAL_SPAN_SECONDS = 300


def _total_memory_bytes() -> int:
    """What THIS machine reports as total physical memory. A module-level
    function, not an inline `os.sysconf` call, purely so a test can patch
    the worker's own name instead of the stdlib object every other test in
    the process shares."""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def _resolve_wait_seconds(settings_row: JobSettings, kind: str | None) -> float | None:
    """This job's `JobContext.wait_seconds` (spec §3.5a): the OPERATOR's
    per-kind override (`settings_row.kind_wait_seconds`) when one is
    present, else the KIND's own code-declared `JobKind.
    default_wait_seconds`, else `None` -- never a guess.

    Takes `settings_row` rather than reading `JobSettings.get_solo()`
    itself -- this rides on the SAME row `_build_job_context` already
    fetched for `response_timeout_seconds`; a second read here would be
    the query-count regression `JobSettings.kind_wait_seconds`'s own
    field comment names, and is explicitly not how this is read.

    The operator's key is checked FIRST and, when present, wins WITHOUT
    ever resolving the kind through the registry -- an operator override
    for a kind that has since been unregistered (or was mistyped) still
    reads back exactly as saved; nothing about honouring it depends on
    the kind existing. Only the FALLBACK path (no operator value for this
    kind) needs `get_job_kind`, and its `ValueError` for an unregistered
    kind is tolerated here, never raised -- `_build_job_context`'s own
    caller (`Worker._execute`) sits outside a guard for this method (see
    its docstring), so nothing this function does may strand a job
    RUNNING forever over a kind lookup.

    `kind=None` (`_build_job_context` reads it via `descriptor.get
    ("kind")`, never `descriptor["kind"]`) degrades the same tolerant
    way: some hand-built descriptors in this test suite name no `"kind"`
    at all (they exercise `_build_job_context`'s progress/checkpoint
    writers, not job-kind dispatch), and a real claim descriptor
    (`models.queue.claim.claim_and_admit`) always carries one -- this is
    a test-fixture accommodation, not a production path this function
    expects to take."""
    override = settings_row.kind_wait_seconds.get(kind)
    if override is not None:
        return float(override)
    try:
        default = get_job_kind(kind).default_wait_seconds
    except ValueError:
        return None
    return None if default is None else float(default)


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

        # Model refs per IN-FLIGHT ATTEMPT, keyed IDENTICALLY to
        # `self._futures`. Eviction's protected set needs the KEYS a live
        # attempt holds, and a `Future` does not carry them; the claim
        # descriptor does. The only query-free alternative -- re-reading
        # `InferenceJob.model_refs` for the live job ids -- would put a
        # query on the 0.5s tick path.
        #
        # WRITTEN AND DROPPED IN LOCKSTEP WITH `self._futures`, on EVERY
        # path, and that is not a nicety: an entry here that outlives its
        # attempt PERMANENTLY protects a key and blocks eviction at that
        # endpoint for the life of the process. So the write sits in the
        # SAME statement block as the `_futures` insert at the bottom of
        # `_launch` -- BELOW the duplicate-submit refusal's early return,
        # never at the top of the method -- and `_prune_finished_futures`
        # drops from both maps by the same key.
        #
        # Tick-thread-only, exactly like `_futures` (see
        # `_prune_finished_futures`'s own docstring): no lock, because
        # there is no second writer.
        self._inflight_refs: dict[tuple[int, uuid.UUID], list[dict]] = {}

        # THE AFFINITY SNAPSHOT (spec §3.6): what the last eviction pass
        # believed was resident once its own unloads had been subtracted,
        # cached for the NEXT claim round's ordering preference. Written
        # and read on the tick thread alone, wholesale-replaced every
        # admitting tick, bounded by the resident set -- so no lock and no
        # pruning contract.
        self._resident_keys: frozenset[tuple[str, str, str]] = frozenset()

        # How many `unload()` calls the LAST `_unload_endpoint` invocation
        # issued -- the companion of that method's key-set return, which
        # cannot answer the question (at endpoint scope one call releases
        # every key there). Read by the capped budget pass immediately
        # after each call, on the tick thread alone.
        self._last_unload_calls = 0

        # `(actual resident bytes, admitted marginal bytes)` from the last
        # residency snapshot -- the two figures the pass's own INFO line
        # renders (§3.3g). Beside the snapshot's return rather than in
        # it: they are how the budget verdict was reached, not part of it.
        self._last_snapshot_bytes: tuple[int, int] = (0, 0)

        # The believed-resident keys whose `unload()` call came back
        # `False` during THIS eviction pass -- reset at the top of
        # `_evict_to_match_plan` and read by `_barrier`. Spec §3.3d(4)
        # honours `False` ONLY for a call made against a believed-resident
        # model, and this set is exactly that population: a PRECAUTIONARY
        # call's `False` never lands here, because the adapter cannot tell
        # "nothing freed" from "nothing to free".
        self._unload_refusals: set[tuple[str, str, str]] = set()

        # {job_id: (informative refusal count, first refusal's monotonic
        # reading)} -- the barrier's refusal bound, which is count AND
        # wall clock (see `MAX_BARRIER_REFUSALS` /
        # `MIN_BARRIER_REFUSAL_SPAN_SECONDS`). In-process on purpose: it
        # is the bound on how long THIS worker keeps retrying, and the
        # durable half of the mechanism is `InferenceJob.not_before`,
        # which survives the restart this map does not.
        self._barrier_refusals: dict[int, tuple[int, float]] = {}

        self._last_heartbeat_monotonic: float | None = None

        # Sleep detection (spec §3.4c, Task 7): the wall-clock/monotonic
        # pair from THIS tick, so the next one can compute both deltas and
        # compare them -- `None` until the first tick ever runs, which
        # therefore never trips the check (nothing to compare against
        # yet). `_sweep_skip_until` is a MONOTONIC deadline (not a wall
        # clock one, which is exactly the untrustworthy-after-a-sleep
        # clock this whole mechanism exists to stop trusting): once a
        # sleep is detected it holds `mono + SLEEP_GRACE_SECONDS`, and
        # `tick()` skips the orphan sweep for as long as the current tick's
        # monotonic reading stays below it.
        self._last_tick_wall: float | None = None
        self._last_tick_monotonic: float | None = None
        self._sweep_skip_until: float | None = None

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
        row = self._settings_row_or_wait()
        max_workers = max(
            (row.max_concurrent_jobs if row is not None
             else JobSettings.MAX_CONCURRENT_JOBS_DEFAULT),
            1,
        )
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="jobs-worker")

    def _settings_row_or_wait(self) -> JobSettings | None:
        """The settings row, waiting briefly for a racing `migrate`, or
        `None` once the wait expires.

        ONE INFO LINE, NEVER A TRACEBACK. On a cold compose boot the
        worker and `migrate` start together and this read can genuinely
        lose the race; a traceback there is noise an operator learns to
        ignore, on the one boot where a real error would matter.
        """
        for attempt in range(BOOT_SCHEMA_WAIT_ATTEMPTS):
            try:
                return JobSettings.get_solo()
            except (ProgrammingError, OperationalError):
                if attempt == 0:
                    logger.info(
                        "worker: waiting for the database schema (the queue's tables are "
                        "not there yet -- `migrate` is probably still running)",
                    )
                self._stopping.wait(BOOT_SCHEMA_WAIT_SECONDS)
        return None

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
        self._record_detected_memory()
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
        TestTheSingleSettingsReadPerTick`.

        SLEEP DETECTION (spec §3.4c, Task 7), first: a suspended host (or
        a ballooned container VM) leaves this process's monotonic clock
        barely advanced while wall clock jumped hours -- on wake, EVERY
        running row this or any other worker holds looks stale at once,
        and the very next orphan sweep would reclaim all of them
        regardless of whether they are still genuinely running. This tick
        compares the two deltas since the last tick; a divergence past
        `SLEEP_DETECT_SECONDS` says so honestly in the log, writes a fresh
        heartbeat IMMEDIATELY (off the throttle -- every row this process
        holds needs to re-stamp itself before anything judges it, and the
        next admitter's sweep is not necessarily this process's own), and
        skips the orphan sweep for `SLEEP_GRACE_SECONDS` so every live
        worker gets the same chance."""
        close_old_connections()
        self._prune_finished_futures()
        self._maybe_heartbeat()

        wall, mono = time.time(), time.monotonic()
        if self._last_tick_wall is not None:
            drift = (wall - self._last_tick_wall) - (mono - self._last_tick_monotonic)
            if drift > SLEEP_DETECT_SECONDS:
                logger.warning(
                    "worker %s: the host appears to have slept for about %.0f seconds "
                    "(wall clock moved that much further than the monotonic clock); "
                    "skipping the orphan sweep for %ss so live rows can re-stamp "
                    "themselves before anything judges them",
                    self.worker_id, drift, SLEEP_GRACE_SECONDS,
                )
                self._sweep_skip_until = mono + SLEEP_GRACE_SECONDS
                # A fresh heartbeat IMMEDIATELY, not on the throttle: every
                # row this worker holds looks stale at this instant, and
                # the next admitter's sweep is not necessarily ours.
                with self._active_lock:
                    self._last_heartbeat_monotonic = None
                self._maybe_heartbeat()
        self._last_tick_wall, self._last_tick_monotonic = wall, mono

        sweep = self._sweep_skip_until is None or mono >= self._sweep_skip_until

        try:
            settings_row = JobSettings.get_solo()
        except (ProgrammingError, OperationalError):
            # A tick that raises is treated by `run_forever` as a CRASH:
            # traceback, loop stopped, non-zero exit. On a cold boot that
            # is simply the wrong reading of "migrate has not finished
            # yet", so the first ticks return quietly and the loop survives
            # to try again (spec §3.10).
            logger.info("worker %s: database schema not ready yet; skipping this tick",
                        self.worker_id)
            return

        claimed = claim_and_admit(
            self.worker_id,
            stale_after_seconds=STALE_AFTER_SECONDS,
            settings_row=settings_row,
            sweep_orphans=sweep,
            # THE AFFINITY SNAPSHOT (spec 3.6): what the last eviction
            # pass believed was resident once its own unloads were
            # subtracted. An ordering PREFERENCE and nothing else -- empty
            # on a fresh worker, which simply gives admission plain
            # `(priority, id)` order.
            resident_keys=self._resident_keys,
        )
        if not claimed:
            return

        with self._active_lock:
            for descriptor in claimed:
                self._active_tokens[descriptor["id"]] = descriptor["claim_token"]

        refused = self._evict_to_match_plan(claimed, settings_row=settings_row)
        self._maybe_heartbeat()

        if self._stopping.is_set():
            # A refused descriptor has already been handed back (and its
            # token popped) by the refusal writers -- requeueing it twice
            # would clobber the hold-off just written to it.
            self._requeue_unlaunched([d for d in claimed if d["id"] not in refused])
            return

        for descriptor in claimed:
            if descriptor["id"] in refused:
                continue
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
        this never waits on anything.

        DROPS `self._inflight_refs` BY THE SAME SURVIVING KEY SET, and
        forgetting that second map is not a mere leak: eviction's
        protected set reads it, so an entry left behind here protects its
        key -- and blocks eviction at that endpoint -- for the life of the
        process."""
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
        self._inflight_refs = {
            key: refs for key, refs in self._inflight_refs.items() if key in remaining
        }

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

    def _record_detected_memory(self) -> None:
        """Write what THIS PROCESS's machine reports as total memory onto
        the settings row, once, at startup (spec §3.7).

        WHY THE WORKER AND NOT THE CONSOLE: the console renders in the web
        service and the budget governs the worker service -- separate
        containers -- so memory detected in the web process describes the
        wrong machine, in precisely the way the operator would be misled
        by.

        NOTHING IS APPLIED ON THE OPERATOR'S BEHALF. This is a PREFILL and
        a label, never a budget: the container sees the VM's allocation
        rather than the host's, and a silently derived budget would be
        authoritative and wrong. The settings page renders the number with
        the process and the date attached so an operator can judge it.

        No new dependency: `os.sysconf` answers on both platforms this
        runs on. A platform that does not answer writes NOTHING -- an
        honestly absent number, like the budget itself -- rather than a
        guess."""
        try:
            total = _total_memory_bytes()
        except (AttributeError, ValueError, OSError):
            return
        if total <= 0:
            return
        try:
            JobSettings.objects.filter(pk=1).update(
                detected_memory_bytes=total, detected_memory_at=timezone.now(),
            )
        except (ProgrammingError, OperationalError):
            return

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

        # NO DUPLICATE SUBMIT (spec §3.4d, the unbuilt half of the
        # 2026-08-25 reclaim fix). A job this process is ALREADY executing
        # can be orphaned (its own cold load starved the heartbeat),
        # re-admitted, and launched a second time here: two handlers, one
        # job, one set of models.
        #
        # The naive answer -- requeue the fresh claim -- is wrong in four
        # ways at once: the row would sit `queued` with no token tracked,
        # the live attempt would lose heartbeat protection, re-admission
        # would come round again 0.5s later for the entire length of the
        # cold load this exists for (each tick paying a widened eviction
        # pass), and the live attempt's own token-conditional writeback
        # would finally be discarded, running the job a THIRD time.
        #
        # So the refusal RESTORES the row to the live attempt instead: back
        # to `running` under that attempt's own token, claimed by this
        # worker, freshly heartbeaten. The row is then not a candidate, the
        # live attempt is heartbeat-protected again, and its eventual
        # writeback matches the row it is writing to.
        live_token = self._live_attempt_token(job_id, exclude=claim_token)
        if live_token is not None:
            # NOTE FOR TASK 12: this `return` sits ABOVE the `_futures`
            # insert at the bottom of this method, and Task 12 adds a
            # companion `_inflight_refs` write beside that insert. That
            # write MUST stay below this refusal -- written above it, a
            # refused descriptor would leave an `_inflight_refs` entry with
            # no matching `_futures` key, which `_prune_finished_futures`
            # (it iterates `_futures`) could never drop, and eviction would
            # protect that key for the life of the process.
            logger.warning(
                "worker %s: refusing to submit job %s twice -- an attempt is still in "
                "flight here; restoring the row to it and discarding this claim",
                self.worker_id, job_id,
            )
            if not self._restore_to_live_attempt(descriptor, live_token):
                self._requeue_unlaunched([descriptor])
            return

        # ONE statement block, so the two maps can never disagree about
        # which attempts are live (see `_inflight_refs`'s declaration).
        self._futures[(job_id, claim_token)] = self._executor.submit(self._execute, descriptor)
        self._inflight_refs[(job_id, claim_token)] = descriptor["model_refs"]

    def _live_attempt_token(self, job_id: int, exclude: uuid.UUID) -> uuid.UUID | None:
        """The claim token of an attempt for `job_id` this process still
        has IN FLIGHT (a future that is not `done()`), other than
        `exclude` -- or `None`.

        `self._futures` is keyed per attempt (2026-08-25's second defect
        fix), which is exactly what makes this answerable: a superseded
        attempt and its successor are both present, under their own keys.
        """
        for (tracked_id, token), future in self._futures.items():
            if tracked_id == job_id and token != exclude and not future.done():
                return token
        return None

    def _restore_to_live_attempt(self, descriptor: dict, live_token: uuid.UUID) -> bool:
        """Give the row back to the attempt that is genuinely still
        running it, under THAT attempt's own claim token, and report
        whether the write landed.

        Conditional on the SUPERSEDING token, so this can only ever
        rewrite the row this claim actually holds: if another worker
        legitimately owns it by now, zero rows match and the caller
        requeues the fresh claim the ordinary way instead (the existing,
        documented "stale token, zero rows" shape).
        """
        job_id = descriptor["id"]
        restored = InferenceJob.objects.filter(
            pk=job_id, claim_token=descriptor["claim_token"],
        ).update(
            state=RUNNING, claim_token=live_token, claimed_by=self.worker_id,
            heartbeat_at=timezone.now(),
        )
        if not restored:
            return False
        with self._active_lock:
            self._active_tokens[job_id] = live_token
        return True

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

        THE WAIT CEILING (spec §3.5a) RIDES ON THIS SAME READ. `get_solo()`
        is called ONCE here, and BOTH `response_timeout_seconds` and
        `wait_seconds` are derived off that one row (`_resolve_wait_
        seconds` above never reads `JobSettings` itself) -- see that
        function's own docstring for why a second `get_solo()` for the
        wait map is explicitly not how this is read.

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
        an unreadable settings row degrades BOTH `response_timeout_
        seconds` and `wait_seconds` to that same `None` fallback rather
        than ever propagating out of this method. An unregistered job
        kind (`models.contracts.jobkinds.get_job_kind`'s `ValueError`,
        resolved inside `_resolve_wait_seconds`) is tolerated the same
        way -- `wait_seconds` degrades to `None`, never raises.
        """
        job_id = descriptor["id"]
        claim_token = descriptor["claim_token"]
        try:
            settings_row = JobSettings.get_solo()
            response_timeout_seconds = float(settings_row.response_timeout_seconds)
            # Review F3: resolved INSIDE this same try, not after it --
            # `_resolve_wait_seconds` can still raise on a malformed
            # PERSISTED value (`float("abc")` if a non-numeric string
            # ever lands in the map, `AttributeError` if `kind_wait_
            # seconds` is ever not a dict at all), and this method's own
            # docstring states the rule plainly: anything it lets escape
            # strands the job RUNNING forever, because the call to THIS
            # method sits outside `_execute`'s own guard. Today's only
            # writer (`models.queue.views._update_kind_waits`) cannot
            # produce either shape, but a hand-edited row or a future
            # writer is exactly the "not-yet-migrated column on a
            # mid-deploy box" class of surprise the paragraph below
            # already accepts for `get_solo()` itself -- one degradation
            # path should cover all three values, not two of three.
            wait_seconds = _resolve_wait_seconds(settings_row, descriptor.get("kind"))
        except Exception:  # noqa: BLE001 -- never-500 parity: degrade, never strand the job
            logger.exception(
                "worker: job %s could not read the response timeout; falling back to "
                "agents.limits.TURN_DEADLINE_SECONDS", job_id,
            )
            response_timeout_seconds = None
            wait_seconds = None
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
            wait_seconds=wait_seconds,
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

    @staticmethod
    def _unload_scope(engine_obj) -> str:
        """`"model"` or `"endpoint"` -- what ONE `unload()` call frees at
        this engine, read from its OPTIONAL `unload_scope` declaration
        (`models.contracts.engines.base.InferenceEngine`'s seam).

        ANYTHING ABSENT OR UNRECOGNISED READS AS `"endpoint"`, the safe
        value: assuming the call frees the whole endpoint costs at worst a
        needless reload, while wrongly assuming per-model granularity
        destroys a live cold load that is measured in minutes on this
        hardware.
        """
        declared = getattr(engine_obj, "unload_scope", None)
        return declared if declared in _UNLOAD_SCOPES else "endpoint"

    @staticmethod
    def _residency_authority(engine_obj) -> str:
        """`"endpoint"` or `"memo"` -- how much this engine's residency
        report is worth, read from its OPTIONAL `residency_authority`
        declaration the same defensive way.

        ANYTHING ABSENT OR UNRECOGNISED READS AS `"memo"`, the safe
        value: treating an empty residency answer as merely "this process
        does not remember anything" costs one precautionary barrier call,
        while trusting it as fact launches an exclusive job on top of
        memory nobody ever released.
        """
        declared = getattr(engine_obj, "residency_authority", None)
        return declared if declared in _RESIDENCY_AUTHORITIES else "memo"

    @staticmethod
    def _key(engine_name: str, endpoint: str, model_id: str) -> tuple[str, str, str]:
        """The ONE spelling of an eviction key: engine name, NORMALIZED
        endpoint, NORMALIZED model tag. Every set in this pass is built
        through here, so a trailing slash or a bare-vs-tagged model id
        can never make two spellings of the same model look like two
        different models (the mismatch `models.registry.discovery`'s
        module docstring describes for `discover()`'s own merge)."""
        return (engine_name, norm_endpoint(endpoint), norm_tag(model_id))

    @staticmethod
    def _own_keys_by_job(claimed: list[dict]) -> dict[int, set[tuple[str, str, str]]]:
        """Each admitted-EXCLUSIVE job's OWN keys, by job id. A
        non-exclusive admission contributes nothing: the §3.3(c)
        exception, and the barrier, are both written for the job that was
        entitled to the whole machine, never for an ordinary peer."""
        return {
            descriptor["id"]: {
                Worker._key(ref["engine"], ref["endpoint"], ref["model_id"])
                for ref in descriptor["model_refs"]
            }
            for descriptor in claimed
            if descriptor.get("exclusive")
        }

    def _protected_keys(self) -> set[tuple[str, str, str]]:
        """THE ONE SAFETY SET (spec §3.3c): every model key this process
        must not take out from under live work. Two halves, and each
        covers a case the other cannot see.

        FIRST HALF, every RUNNING job's keys. `claim_and_admit` has
        already persisted THIS tick's admissions as `running` by the time
        any of this runs, so one query covers "running union admitted"
        without merging two collections -- which matters concretely
        because an agent turn is planned EXCLUSIVE, so every chat turn
        runs this pass, and an unprotected definition would unload that
        turn's own warm chat model and cold-load it again on every single
        message.

        SECOND HALF, every attempt still IN FLIGHT in this process
        (`self._inflight_refs`). An attempt the orphan sweep requeued
        while its handler is genuinely still mid-cold-load has a row back
        at `queued` -- invisible to the RUNNING query above -- for exactly
        as long as that cold load takes, which is the window this half
        exists for (Q11).

        No `claimed` parameter, deliberately: everything here comes from
        the RUNNING query and from this process's own map. Both maps are
        read without a lock, because both are tick-thread-only (see
        `_prune_finished_futures`'s docstring).
        """
        keys: set[tuple[str, str, str]] = set()
        for model_refs in InferenceJob.objects.filter(state=RUNNING).values_list(
            "model_refs", flat=True
        ):
            for ref in model_refs:
                keys.add(self._key(ref["engine"], ref["endpoint"], ref["model_id"]))
        for refs in list(self._inflight_refs.values()):
            for ref in refs:
                keys.add(self._key(ref["engine"], ref["endpoint"], ref["model_id"]))
        return keys

    def _eviction_targets(self, claimed: list[dict]) -> tuple[
        set[tuple[str, str]],
        set[tuple[str, str]],
        dict[tuple[str, str], tuple[str, ...]],
    ] | None:
        """PHASE 1 of `_evict_to_match_plan` (which see for the whole
        argument): what the machine is supposed to be holding.

        `(endpoints, own_endpoints, model_ids_by_endpoint)`, or `None`
        when no job is RUNNING -- the caller returns immediately on
        `None`, which is what stops the engine being probed for nothing.

        `endpoints` is the set to SWEEP this tick. It starts as the
        RUNNING jobs' own endpoints and is unioned with
        `models.registry.bindings.registered_endpoints()` -- the one
        notion of "every engine endpoint this box knows about" -- ONLY ON
        A TICK THAT ADMITS AN EXCLUSIVE JOB (spec §3.3e). That bound is
        the whole cost control: only the admission entitled to the whole
        machine pays for the whole machine to be probed, and a
        non-exclusive tick keeps exactly today's reach. A model left warm
        on an IDLE engine was never visited before, which is the literal
        host-crash shape this widening exists for (Q4).

        The widening also makes `over_budget` mean something WIDER on
        those ticks -- more endpoints counted means more resident bytes
        counted. Deliberate: under-counting resident memory is the
        direction that crashes hosts.

        `own_endpoints` is the admitted exclusive job's OWN endpoints,
        derived from THIS tick's batch and never recomputed later.

        `model_ids_by_endpoint` is what each endpoint can be ADDRESSED by,
        from the same `registered_endpoints()` call -- the unload seam
        takes a `model_id`, so a foreign endpoint with no connection row
        yields an empty tuple and cannot be addressed at all (a named
        residual, spec §11, never papered over with a synthetic id).
        """
        running_refs = list(
            InferenceJob.objects.filter(state=RUNNING).values_list("model_refs", flat=True)
        )
        if not running_refs:
            return None

        endpoints: set[tuple[str, str]] = set()
        for model_refs in running_refs:
            for ref in model_refs:
                endpoints.add((ref["engine"], norm_endpoint(ref["endpoint"])))

        own_endpoints: set[tuple[str, str]] = {
            (ref["engine"], norm_endpoint(ref["endpoint"]))
            for descriptor in claimed
            if descriptor.get("exclusive")
            for ref in descriptor["model_refs"]
        }

        model_ids_by_endpoint: dict[tuple[str, str], tuple[str, ...]] = {}
        if any(descriptor.get("exclusive") for descriptor in claimed):
            for engine_name, endpoint, model_ids in registered_endpoints():
                endpoints.add((engine_name, endpoint))
                model_ids_by_endpoint[(engine_name, endpoint)] = model_ids

        return endpoints, own_endpoints, model_ids_by_endpoint

    def _residency_snapshot(
        self,
        endpoints: set[tuple[str, str]],
        claimed: list[dict],
        budget_bytes: int | None,
    ) -> tuple[dict[tuple[str, str], list], set[tuple[str, str, str]], bool]:
        """PHASE 2 of `_evict_to_match_plan` (which see): what the
        machine is ACTUALLY holding, and whether that plus what is about
        to load exceeds the budget.

        `(installed_by_endpoint, believed_resident, over_budget)`. An
        endpoint is in the dict ONLY if its engine resolved, offered
        `list_installed`, and that call returned -- so an engine that
        lacks the method (warned once, per engine+method) or whose call
        raised (logged; eviction must never block a launch) is absent from
        the dict and is therefore untouched by both eviction passes. The
        barrier reads that same absence as "the belief here is worth
        nothing" (§3.3d(3)).

        `believed_resident` is every key this snapshot says is loaded --
        the pre-eviction belief spec §3.6's affinity cache subtracts this
        pass's own releases from.

        `over_budget` is `actual_resident_bytes + admitted_marginal >
        budget_bytes`, and is `False` WHENEVER `budget_bytes` is `None`:
        it is the one verdict in this pass that is arithmetic against a
        number that may not exist. Both halves keep their exact prior
        arithmetic -- see the two inline comments below, which are the
        reasoning for the deliberate under-count and for the MAX fold, and
        are the parts of this function most likely to be 'tidied' into a
        bug."""
        installed_by_endpoint: dict[tuple[str, str], list] = {}
        believed_resident: set[tuple[str, str, str]] = set()
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
                # A SKIP a human would otherwise have to infer from an
                # eviction that simply never happened (§3.3g). INFO, not
                # WARNING: there is nothing an operator does about one
                # engine being briefly unreachable, and the barrier
                # already treats this absence as "the belief here is
                # worth nothing".
                logger.info(
                    "worker: eviction could not list installed models at %s (%s) -- "
                    "that endpoint is skipped this tick",
                    endpoint, engine_name,
                )
                continue
            installed_by_endpoint[(engine_name, endpoint)] = installed
            for model in installed:
                if not model.loaded:
                    continue
                key = self._key(engine_name, endpoint, model.model_id)
                believed_resident.add(key)
                resident_sizes[key] = getattr(model, "loaded_size", None)

                if getattr(model, "loaded_size", None):
                    # RUNG 3 (spec §3.1), from a snapshot already on the
                    # wire -- never a call made for this purpose. Only a
                    # POSITIVE reading is written: a `None` or zero size
                    # writes nothing, never a zero, because a zero would
                    # read back as a real "this model is free" answer.
                    record_engine_reported_footprint(
                        engine_name, endpoint, model.model_id, model.loaded_size,
                    )

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
                key = self._key(ref["engine"], ref["endpoint"], ref["model_id"])
                if key in resident_sizes:
                    continue
                size = ref.get("footprint_bytes") or 0
                admitted_new_keys[key] = max(admitted_new_keys.get(key, 0), size)
        admitted_marginal = sum(admitted_new_keys.values())

        over_budget = (
            budget_bytes is not None
            and actual_resident_bytes + admitted_marginal > budget_bytes
        )
        # The two numbers the pass's own INFO line renders (§3.3g),
        # reported beside the three-element return rather than folded
        # into it -- they are how the verdict was reached, not part of
        # the verdict.
        self._last_snapshot_bytes = (actual_resident_bytes, admitted_marginal)
        return installed_by_endpoint, believed_resident, over_budget

    def _unload_endpoint(
        self, engine_name: str, endpoint: str, installed: list, *,
        protected_keys: set[tuple[str, str, str]],
        own_keys: set[tuple[str, str, str]],
        reason: str,
        limit: int | None = None,
    ) -> set[tuple[str, str, str]]:
        """Unload what may be unloaded at ONE endpoint, and return the set
        of keys that were actually RELEASED.

        THE RETURN IS A KEY SET, NOT A COUNT, for one concrete reason: at
        `"endpoint"` scope a single call frees EVERY believed-resident
        model there, so "what was released" is not "the key that was
        addressed", and spec §3.6's affinity cache has to subtract the
        real set.

        THE PROTECTION RULE, applied per scope (spec §3.3c):

        - `"model"` scope -- skip protected keys one by one; everything
          else at the endpoint is unloaded individually.
        - `"endpoint"` scope -- ONE call frees everything here, so the
          WHOLE endpoint is skipped if any protected key lives at it...
        - ...UNLESS every protected key here belongs to `own_keys`: the
          admitted exclusive job's OWN keys at its OWN endpoint. Freeing
          that endpoint unavoidably takes its own model with it and there
          is no per-model call to make instead, so the barrier proceeds and
          the job pays at worst one reload. `own_keys` is EMPTY for every
          other caller, which is what keeps this exception to the one case
          it is written for.

        `reason` is the log vocabulary's "why" (`not needed at an exclusive
        endpoint` / `over budget` / `precautionary barrier`). DECLARED HERE
        IN COMMIT 1 AND FIRST READ IN COMMIT 3, deliberately: the signature
        is final from the start so no later commit revises it.

        `limit` caps the calls issued here, for the budget-driven pass's
        share of `MAX_UNLOADS_PER_TICK`; `None` is uncapped. How many
        calls were actually issued is reported in
        `self._last_unload_calls`, reset at the top of every invocation
        and read by the capped pass immediately afterwards -- it cannot
        be read off the return value, because a released key is not a
        call (at endpoint scope ONE call releases every key there).

        `self._maybe_heartbeat()` is called after EVERY unload call, not
        once around the loop -- that is what makes an uncapped exclusive
        pass safe, and hoisting it out reintroduces the stale-row window
        this pass was fixed to close.
        """
        self._last_unload_calls = 0
        engine_obj = self._get_engine_or_none(engine_name)
        if engine_obj is None:
            return set()
        unload = getattr(engine_obj, "unload", None)
        if unload is None:
            self._warn_missing_method_once(engine_name, "unload")
            return set()

        resident = [model for model in installed if model.loaded]
        if not resident:
            return set()

        if self._unload_scope(engine_obj) == "endpoint":
            return self._unload_whole_endpoint(
                unload, engine_name, endpoint, resident,
                protected_keys=protected_keys, own_keys=own_keys, reason=reason, limit=limit,
            )

        released: set[tuple[str, str, str]] = set()
        issued = 0
        for model in resident:
            if limit is not None and issued >= limit:
                break
            key = self._key(engine_name, endpoint, model.model_id)
            if key in protected_keys:
                continue
            issued += 1
            self._last_unload_calls += 1
            accepted = unload(endpoint, model.model_id)
            if accepted:
                released.add(key)
            else:
                # An INFORMATIVE refusal: the call was made against a
                # model this snapshot says is resident, so `False` means
                # what it says (§3.3d(4)) and the barrier may honour it.
                # INFO here, not WARNING: the line an operator can act on
                # is `_barrier`'s, which knows whether this refusal
                # actually blocked a launch.
                self._unload_refusals.add(key)
            self._log_unload(engine_name, endpoint, model.model_id, "model", reason, accepted)
            # After EVERY call, never once around the loop -- see this
            # method's docstring. `_maybe_heartbeat`'s own
            # `HEARTBEAT_SECONDS` throttle bounds this to at most one
            # actual UPDATE every 10s however many times it is called.
            self._maybe_heartbeat()
        return released

    def _unload_whole_endpoint(
        self, unload, engine_name: str, endpoint: str, resident: list, *,
        protected_keys: set[tuple[str, str, str]],
        own_keys: set[tuple[str, str, str]],
        reason: str,
        limit: int | None,
    ) -> set[tuple[str, str, str]]:
        """`_unload_endpoint`'s `"endpoint"`-scope half, split out only so
        neither branch has to be read through the other. ONE call, which
        frees everything believed resident here -- so the decision is
        all-or-nothing and `model_id` is addressing, not selection."""
        keys = {self._key(engine_name, endpoint, model.model_id) for model in resident}
        protected_here = keys & protected_keys
        if protected_here and not protected_here <= own_keys:
            logger.warning(
                "worker: eviction skipped the whole endpoint %s (%s) -- one unload there "
                "frees everything, and %s is protected by live work (%s)",
                endpoint, engine_name,
                ", ".join(sorted(key[2] for key in protected_here)),
                reason,
            )
            return set()
        if limit is not None and limit < 1:
            return set()

        addressed = resident[0].model_id
        self._last_unload_calls += 1
        accepted = unload(endpoint, addressed)
        self._maybe_heartbeat()
        self._log_unload(engine_name, endpoint, addressed, "endpoint", reason, accepted)
        if not accepted:
            # Informative for every key here, not only the one addressed:
            # at this scope the call was made against the whole
            # believed-resident set (§3.3d(4)).
            self._unload_refusals |= keys
            return set()
        return keys

    @staticmethod
    def _log_unload(
        engine_name: str, endpoint: str, model_id: str, scope: str, reason: str, accepted: bool,
    ) -> None:
        """ONE INFO LINE PER UNLOAD ATTEMPT, in the stable vocabulary spec
        §3.3(g) fixes: who (engine, endpoint, model), at what granularity
        (`model` / `endpoint`), WHY (`not needed at an exclusive endpoint`
        / `over budget` / `precautionary barrier` -- `_unload_endpoint`'s
        `reason`), and the RESULT (`accepted` / `refused`; `unavailable`
        is the third result and belongs to an engine offering no
        `unload()` at all, said once per engine+method by
        `_warn_missing_method_once`).

        Deliberately not a WARNING even when refused: a successful
        eviction used to be silent and a `False` was the only thing this
        pass ever logged, which is precisely the asymmetry that left an
        operator watching a host fill up unable to tell "nothing needed
        evicting" from "everything was skipped". WARNING is reserved for
        what an operator can act on, and whether a refusal is actionable
        is `_barrier`'s question, not this one's."""
        logger.info(
            "worker: unload %s at %s (%s), scope %s, %s -- %s",
            model_id, endpoint, engine_name, scope, reason,
            "accepted" if accepted else "refused",
        )

    def _evict_exclusive_endpoints(
        self,
        endpoints: set[tuple[str, str]],
        installed_by_endpoint: dict[tuple[str, str], list],
        protected_keys: set[tuple[str, str, str]],
        own_keys: set[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        """PASS 1 of `_evict_to_match_plan` (which see for the full
        argument): on a tick that admits an EXCLUSIVE job, every
        non-protected resident model at every endpoint in the swept set is
        unloaded, full stop. Returns the union of released keys.

        THE SWEPT SET, NOT ONLY THE JOB'S OWN ENDPOINTS (spec §3.3e): a
        model left warm on an idle FOREIGN engine occupies the same
        memory as one at the job's own address, and it was the endpoint
        this pass never visited.

        UNCAPPED, deliberately -- NOT subject to `MAX_UNLOADS_PER_TICK`.
        The safety mechanism is not the cap: `tick()` registers this
        batch's tokens in `self._active_tokens` BEFORE calling the caller
        at all, so `self._maybe_heartbeat()` -- called after every unload
        attempt inside `_unload_endpoint`, not once around it -- genuinely
        refreshes the exclusive job's row DURING this pass. Removing that
        call, or hoisting it out of the loop, reintroduces the stale-row
        window this pass was fixed to close."""
        released: set[tuple[str, str, str]] = set()
        for key in sorted(endpoints):
            installed = installed_by_endpoint.get(key)
            if installed is None:
                continue
            engine_name, endpoint = key
            released |= self._unload_endpoint(
                engine_name, endpoint, installed,
                protected_keys=protected_keys, own_keys=own_keys,
                reason="not needed at an exclusive endpoint",
            )
        return released

    def _evict_for_budget(
        self,
        installed_by_endpoint: dict[tuple[str, str], list],
        already_swept: set[tuple[str, str]],
        protected_keys: set[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        """PASS 2 of `_evict_to_match_plan` (which see): non-exclusive,
        budget-driven eviction, CAPPED at `MAX_UNLOADS_PER_TICK` unload
        calls per call. Returns the union of released keys.

        Called only when phase 2 said `over_budget` -- which is itself
        only ever true when a budget exists, so this is the ONE mechanism
        in the pass that still needs one. The caller makes that decision,
        so this method's own loop no longer re-checks a flag that cannot
        change inside it.

        `remaining` is ONE allowance across the endpoint loop: the cap
        bounds total unload calls, not calls per endpoint. This runs on
        the tick thread, and an unbounded run of slow `unload()` calls
        would eat the margin `STALE_AFTER_SECONDS` assumes. Whatever this
        cap leaves undone is picked up on a later tick that itself admits
        something -- not necessarily the next one.

        `already_swept` is whatever pass 1 covered, skipped here so an
        endpoint it emptied uncapped is not nibbled at again under the
        cap."""
        released: set[tuple[str, str, str]] = set()
        remaining = MAX_UNLOADS_PER_TICK
        for key in sorted(installed_by_endpoint):
            if remaining < 1:
                break
            if key in already_swept:
                continue
            engine_name, endpoint = key
            released |= self._unload_endpoint(
                engine_name, endpoint, installed_by_endpoint[key],
                protected_keys=protected_keys, own_keys=set(),
                reason="over budget", limit=remaining,
            )
            # The allowance is spent per CALL ISSUED, which is what the
            # cap bounds -- not per key RELEASED, which at endpoint scope
            # would charge one call several times over. That is why the
            # count comes back beside the key set rather than in it.
            remaining -= self._last_unload_calls
        if remaining < 1:
            # A SKIP a human would otherwise have to infer (§3.3g): an
            # eviction that stopped short looks identical to one that had
            # nothing left to do.
            logger.info(
                "worker: budget-driven eviction hit its cap of %s unload calls this tick; "
                "whatever is left is picked up on a later admitting tick",
                MAX_UNLOADS_PER_TICK,
            )
        return released

    # --- the exclusive barrier (spec §3.3d) ---------------------------------

    def _protection_refusal(
        self, claimed: list[dict], endpoints: set[tuple[str, str]],
        protected_keys: set[tuple[str, str, str]],
        own_keys_by_job: dict[int, set[tuple[str, str, str]]],
    ) -> set[int]:
        """PART 2 of the barrier (§3.3d(2)): an admitted EXCLUSIVE job is
        not launched this tick if ANY endpoint in the swept set holds a
        protected key that is not its own. Returns the job ids refused,
        having already handed each one back to the queue.

        BEFORE ANY HTTP -- `_evict_to_match_plan`'s FIFTH ORDERING RULE,
        and the ordering is about cost, not taste: `protected_keys` comes
        from database rows and this process's own maps and needs no
        network at all, while evaluating it AFTER the residency snapshot
        would pay a full cross-engine probe on every 0.5s tick for the
        entire life of the protecting attempt -- a cold load measured in
        minutes on this hardware.

        THE §3.3(c) EXCEPTION IS INCLUDED, which is why `own_keys_by_job`
        is a parameter: the admitted job's OWN key at its OWN endpoint
        does not refuse its own launch. Anything else does, at either
        unload scope -- a model-scope endpoint can spare the protected
        model, but the memory it occupies is memory the exclusive job was
        promised and is not going to get this tick.

        NEVER TOUCHES `self._barrier_refusals` (review M6). Spec §3.3d(5)
        counts INFORMATIVE refusals; this wait is bounded by the live
        attempt's own end, and since an agent turn is planned exclusive,
        counting it would fail three consecutive chat turns for an
        ordinary long-running foreign job at a shared endpoint -- a job
        the queue was correctly waiting for.
        """
        refused: set[int] = set()
        for descriptor in claimed:
            own_keys = own_keys_by_job.get(descriptor["id"])
            if own_keys is None:
                continue
            blocking = sorted(
                key for key in protected_keys
                if (key[0], key[1]) in endpoints and key not in own_keys
            )
            if not blocking:
                continue
            engine_name, endpoint, _model_id = blocking[0]
            logger.warning(
                "worker: not launching exclusive job %s this tick -- %s at %s (engine %r) "
                "is protected by live work; the job is queued again for %ss",
                descriptor["id"], ", ".join(key[2] for key in blocking), endpoint,
                engine_name, BARRIER_HOLDOFF_SECONDS,
            )
            self._requeue_refused(descriptor)
            refused.add(descriptor["id"])
        return refused

    def _barrier(
        self, claimed: list[dict], endpoints: set[tuple[str, str]],
        installed_by_endpoint: dict[tuple[str, str], list],
        model_ids_by_endpoint: dict[tuple[str, str], tuple[str, ...]],
        protected_keys: set[tuple[str, str, str]],
        own_keys_by_job: dict[int, set[tuple[str, str, str]]],
    ) -> set[int]:
        """PARTS 3 AND 4 of the barrier: the PRECAUTIONARY calls, and then
        reading what the unload calls actually said. Returns the job ids
        refused by an INFORMATIVE `False`, having already handed each one
        back to the queue (or failed it, once both bounds are met).

        Runs AFTER `_evict_exclusive_endpoints`, whose calls against
        believed-resident models are the ones whose answers count -- they
        arrive here through `self._unload_refusals`.

        A PRECAUTIONARY CALL is issued at an endpoint where the belief is
        worth nothing: either `installed_by_endpoint` has no entry for it
        (the snapshot was unavailable or raised) or the entry has no
        loaded model AND the engine declares -- or defaults to --
        `residency_authority="memo"`. An engine declaring
        `residency_authority="endpoint"` that reports nothing gets NO
        call: it actually knows nothing is resident, so there is nothing
        to barrier and nothing its answer could add, and that narrowing is
        what keeps a 30s no-rise poll off every chat turn.

        A PRECAUTIONARY `False` IS INFO AND DOES NOT REFUSE (§3.3d(4)):
        the adapter cannot tell "nothing freed" from "nothing to free",
        and a cold, empty endpoint is the common case after a restart --
        a rule that refused there would make an exclusive job
        unlaunchable not for one tick but for ever.
        """
        for endpoint_key in sorted(endpoints):
            engine_name, endpoint = endpoint_key
            installed = installed_by_endpoint.get(endpoint_key)
            if installed is not None and any(model.loaded for model in installed):
                continue
            engine_obj = self._get_engine_or_none(engine_name)
            if engine_obj is None:
                continue
            if installed is not None and self._residency_authority(engine_obj) == "endpoint":
                continue
            unload = getattr(engine_obj, "unload", None)
            if unload is None:
                self._warn_missing_method_once(engine_name, "unload")
                continue
            addressed = self._barrier_address(endpoint_key, claimed, model_ids_by_endpoint)
            if addressed is None:
                logger.info(
                    "worker: no precautionary barrier call at %s (%s) -- nothing registered "
                    "there supplies a model id to address the unload with",
                    endpoint, engine_name,
                )
                continue

            # THE PRECAUTIONARY CALL DOES NOT ROUTE THROUGH
            # `_unload_endpoint`, and cannot: there is no
            # believed-resident model to iterate. `_unload_endpoint` is
            # the one place a BELIEVED-RESIDENT model is unloaded; this
            # is the one place a call is made precisely because the
            # belief is worth nothing. Its `False` is INFO and does not
            # refuse (§3.3d(4)).
            accepted = unload(endpoint, addressed)
            self._maybe_heartbeat()
            self._log_unload(
                engine_name, endpoint, addressed, self._unload_scope(engine_obj),
                "precautionary barrier", accepted,
            )

        if not self._unload_refusals:
            # A SUCCESSFUL barrier resets the count -- an engine that
            # released its memory this time has not been failing for
            # three spaced attempts.
            for job_id in own_keys_by_job:
                self._barrier_refusals.pop(job_id, None)
            return set()

        engine_name, endpoint, _model_id = sorted(self._unload_refusals)[0]
        refused: set[int] = set()
        for descriptor in claimed:
            job_id = descriptor["id"]
            if job_id not in own_keys_by_job:
                continue
            logger.warning(
                "worker: not launching exclusive job %s this tick -- engine %r at %s refused "
                "to release %s; the job is queued again for %ss",
                job_id, engine_name, endpoint,
                ", ".join(sorted(key[2] for key in self._unload_refusals)),
                BARRIER_HOLDOFF_SECONDS,
            )
            if self._record_barrier_refusal(job_id):
                count, first = self._barrier_refusals.get(job_id, (MAX_BARRIER_REFUSALS, 0.0))
                self._fail_barrier_refused(
                    descriptor, engine_name, endpoint, time.monotonic() - first,
                )
            else:
                self._requeue_refused(descriptor)
            refused.add(job_id)
        return refused

    @staticmethod
    def _barrier_address(
        endpoint_key: tuple[str, str], claimed: list[dict],
        model_ids_by_endpoint: dict[tuple[str, str], tuple[str, ...]],
    ) -> str | None:
        """SOME model id to address a precautionary unload at this
        endpoint with, or `None` when nothing supplies one.

        The admitted exclusive job's OWN ref wins where the endpoint is
        its own -- that is the model it is about to load, named in the
        spelling the job itself uses. Otherwise the endpoint's registered
        connection ids, from `registered_endpoints()`. A CONFIGURED
        endpoint with no connection row yields neither, and is simply not
        barriered: a named residual (spec §11), never papered over with a
        synthetic id the engine would not recognise.
        """
        for descriptor in claimed:
            if not descriptor.get("exclusive"):
                continue
            for ref in descriptor["model_refs"]:
                if (ref["engine"], norm_endpoint(ref["endpoint"])) == endpoint_key:
                    return ref["model_id"]
        model_ids = model_ids_by_endpoint.get(endpoint_key) or ()
        return model_ids[0] if model_ids else None

    def _record_barrier_refusal(self, job_id: int) -> bool:
        """Count one INFORMATIVE refusal for `job_id`, and answer whether
        the job should now be FAILED -- `True` only once BOTH bounds are
        met: `MAX_BARRIER_REFUSALS` refusals AND
        `MIN_BARRIER_REFUSAL_SPAN_SECONDS` elapsed since the first.

        Both, because counting refusals alone is a trap: an informative
        `False` is exactly what a BUSY engine returns, so three of them
        could elapse in barely more than the time three unload calls
        take, and "three attempts" would mean "a second and a half".
        """
        count, first = self._barrier_refusals.get(job_id, (0, time.monotonic()))
        count += 1
        self._barrier_refusals[job_id] = (count, first)
        return (
            count >= MAX_BARRIER_REFUSALS
            and (time.monotonic() - first) >= MIN_BARRIER_REFUSAL_SPAN_SECONDS
        )

    def _requeue_refused(self, descriptor: dict) -> None:
        """Hand a REFUSED descriptor back to the queue with a hold-off.

        POPS `self._active_tokens[job_id]` UNDER THE LOCK FIRST, before
        the conditional UPDATE, matching `_requeue_unlaunched`'s shape and
        for the reason that method's own docstring gives: a refused
        descriptor never reaches `_launch`, so no `Future` exists for it
        and `_prune_finished_futures` can never clean it up; a lingering
        token would have `_maybe_heartbeat` refreshing a row that is
        `queued` again, for ever.

        `attempts` IS DELIBERATELY UNTOUCHED -- the job never ran. What
        is written is the durable half of the mechanism: `not_before =
        now + BARRIER_HOLDOFF_SECONDS`, which the candidate query honours
        (`models.queue.claim`), so the job is not re-claimed on every
        0.5s tick against an engine that is still holding memory.
        Token-conditional for the same reason every other writeback in
        this module is.
        """
        job_id = descriptor["id"]
        with self._active_lock:
            self._active_tokens.pop(job_id, None)
        InferenceJob.objects.filter(
            pk=job_id, state=RUNNING, claim_token=descriptor["claim_token"],
        ).update(
            state=QUEUED, claimed_by="", claim_token=None,
            started_at=None, heartbeat_at=None,
            not_before=timezone.now() + timedelta(seconds=BARRIER_HOLDOFF_SECONDS),
        )

    def _fail_barrier_refused(
        self, descriptor: dict, engine_name: str, endpoint: str, span_seconds: float,
    ) -> None:
        """Fail a job that has now met BOTH refusal bounds, with an error
        an operator can act on: which engine, at which address, refused to
        release memory, how many attempts, over how long (owner decision
        6 -- an honest job failure, never a silent forever-wait).

        Pops the active token under the lock FIRST, exactly like
        `_requeue_refused`, and schedules the kind's `on_terminal` hook
        with `transaction.on_commit` the way
        `models.queue.claim._sweep_orphans`'s second-orphaning branch
        does -- a feature-app hook is arbitrary code and never runs inline
        on this path.
        """
        job_id = descriptor["id"]
        minutes = max(1, round(span_seconds / 60)) if span_seconds >= 30 else 0
        error = (
            "engine '%s' at %s did not release memory for this exclusive job after "
            "%s attempts over %s minutes; it was not retried again"
            % (engine_name, endpoint, MAX_BARRIER_REFUSALS, minutes)
        )
        with self._active_lock:
            self._active_tokens.pop(job_id, None)
        failed = InferenceJob.objects.filter(
            pk=job_id, state=RUNNING, claim_token=descriptor["claim_token"],
        ).update(state=FAILED, finished_at=timezone.now(), error=error)
        self._barrier_refusals.pop(job_id, None)
        if not failed:
            return
        logger.warning("worker: job %s failed -- %s", job_id, error)
        transaction.on_commit(
            lambda kind=descriptor["kind"], payload=descriptor["payload"]:
            invoke_on_terminal(kind, payload, "failed")
        )

    def _evict_to_match_plan(
        self, claimed: list[dict], *, settings_row: JobSettings | None = None,
    ) -> set[int]:
        """Admission (`models.queue.claim.claim_and_admit`) plans against
        RUNNING jobs' declared footprints; this function makes the
        machine's ACTUAL resident memory match that plan before any newly
        admitted job's handler starts -- called once per tick, right
        after `claim_and_admit`, before any of `claimed` is launched.
        Returns the set of job ids this tick REFUSED to launch -- each
        already handed back to the queue with a hold-off (or failed, once
        both refusal bounds are met); `tick()` simply skips them.

        `settings_row` (S6) is `tick()`'s own already-fetched row,
        threaded in so the tick pays ONE `JobSettings` read rather than
        one here and another inside `claim_and_admit` moments earlier off
        the same 0.5s loop -- the pattern
        `identity.request.settings_row_for` already names, applied to a
        tick instead of a request. `None` falls back to `get_solo()`, so
        this method stays callable on its own (`TestEviction` calls it
        directly throughout) and eviction and admission still read the
        same budget when they are called separately.

        THE BUDGET GATES ONE PASS, NOT THE WHOLE FUNCTION. Phases 1 and 2,
        the protected set, and the exclusive pass all run regardless of
        `memory_budget_bytes`; only the capped, budget-driven pass checks
        it, because it is the one mechanism whose decision is arithmetic
        against a number that may not exist. This is not a tidy-up: "no
        budget set" is the posture the field actually ran in, and a
        whole-function early return there made every mechanism below dead
        code exactly where it was needed.

        1. `_eviction_targets` -- the endpoints to sweep, the admitted
           exclusive job's own endpoints, and what each endpoint can be
           addressed by. On an exclusive-admitting tick the swept set is
           unioned with every registered engine endpoint (spec §3.3e).
        2. `_protected_keys` -- every RUNNING job's keys (this tick's
           admitted batch included, since the claim committed before this
           pass runs) plus every attempt still in flight in this process.
        3. `_residency_snapshot` -- ACTUAL residency at each swept
           endpoint via the engine's OPTIONAL `list_installed` (loaded
           flags), ground truth, never a job row's merely-declared
           footprint; plus the rung-3 footprint harvest and the budget
           verdict.
        4. Pass 1, the exclusive pass, uncapped; then pass 2, the capped
           budget-driven pass, only when the budget says so.

        FIVE ORDERING RULES, and none of them is optional: phase 1
        answering `None` is what stops phase 2 probing an engine for
        nothing; phase 2's `over_budget` is what gates pass 2 and nothing
        else; pass 1 must run before pass 2 so an exclusive endpoint is
        emptied uncapped rather than nibbled at under the cap; the
        barrier's answers can only be read after pass 1 has made the
        calls; and -- THE FIFTH -- the protection check runs BEFORE the
        residency snapshot, because it needs no network and evaluating it
        after would pay a full cross-engine probe on every 0.5s tick for
        the entire life of the protecting attempt.

        Exclusive-endpoint eviction is UNCAPPED -- deliberately NOT
        subject to `MAX_UNLOADS_PER_TICK` (review finding, T4 round 3).
        Reasoning: (a) this function is only ever called from `tick()`'s
        `if not claimed: return` branch, so it never runs at all on a
        tick that admits nothing; (b) once an exclusive job IS admitted,
        `models.queue.scheduler` rule 3 blocks every other admission for
        as long as it runs, so there is no future ADMITTING tick for a
        capped leftover to be "picked up" on; and (c) the swept set is
        derived from THIS tick's own `claimed` batch, never recomputed
        later, so a capped eviction here would leave unneeded models
        permanently resident alongside a job that is supposed to have the
        machine to itself, for the rest of that job's run. It is safe to
        leave uncapped because `tick()` registers this batch's tokens in
        `self._active_tokens` BEFORE calling this function at all, so
        `self._maybe_heartbeat()`, called inside `_unload_endpoint` after
        every unload attempt, genuinely refreshes the exclusive job's row
        DURING an uncapped, potentially long-running pass.

        Non-exclusive, BUDGET-DRIVEN eviction IS capped at
        `MAX_UNLOADS_PER_TICK` `unload()` calls per call to this function
        -- this runs synchronously on the SAME thread `tick()` calls it
        from, and an unbounded loop of slow/wedged `unload()` calls would
        eat directly into the margin `STALE_AFTER_SECONDS` assumes (see
        that constant's comment); `tick()` also writes a fresh heartbeat
        again immediately after this function returns, before launching
        anything, precisely to bound how much of that margin this
        function's own wall-clock time can consume.

        Every step degrades, never raises: an engine lacking
        `list_installed`/`unload` (read via `getattr`, per the engine
        seam's own degradation idiom) is logged ONCE per engine+method and
        left alone -- today's idle-timeout-only behavior for it;
        `unload()` returning `False` is logged and the loop proceeds.

        The guiding principle, in one line: admission plans against
        running jobs; eviction makes the machine match the plan.
        """
        row = settings_row if settings_row is not None else JobSettings.get_solo()
        budget_bytes = row.memory_budget_bytes
        self._unload_refusals = set()

        targets = self._eviction_targets(claimed)
        if targets is None:
            return set()
        endpoints, own_endpoints, model_ids_by_endpoint = targets

        protected_keys = self._protected_keys()
        own_keys_by_job = self._own_keys_by_job(claimed)

        # THE FIFTH ORDERING RULE (§3.3d(2)): protection is evaluated
        # BEFORE phase 2's residency snapshot, and the reason is cost
        # rather than taste -- see `_protection_refusal`'s own docstring.
        # A tick refused here has made no HTTP call at all, which is what
        # keeps a refusal from costing a full cross-engine probe every
        # 0.5s for the whole life of the protecting attempt.
        refused = self._protection_refusal(
            claimed, endpoints, protected_keys, own_keys_by_job,
        )
        if refused:
            # No snapshot was taken, so there is no belief to cache -- and
            # a STALE one is worse than none: this path repeats every tick
            # for the life of the protecting attempt, which the spec
            # measures in minutes, so §3.6's ordering preference would
            # spend all of it preferring keys nothing re-checked.
            self._resident_keys = frozenset()
            return refused

        installed_by_endpoint, believed_resident, over_budget = self._residency_snapshot(
            endpoints, claimed, budget_bytes,
        )

        released: set[tuple[str, str, str]] = set()
        swept_exclusively: set[tuple[str, str]] = set()
        if own_endpoints:
            own_keys: set[tuple[str, str, str]] = set()
            for keys in own_keys_by_job.values():
                own_keys |= keys
            swept_exclusively = endpoints
            released |= self._evict_exclusive_endpoints(
                endpoints, installed_by_endpoint, protected_keys, own_keys,
            )
            refused = self._barrier(
                claimed, endpoints, installed_by_endpoint, model_ids_by_endpoint,
                protected_keys, own_keys_by_job,
            )

        if over_budget:
            released |= self._evict_for_budget(
                installed_by_endpoint, swept_exclusively, protected_keys,
            )

        # The affinity snapshot (spec §3.6), cached for the NEXT claim
        # round: the pre-eviction belief MINUS what this pass actually
        # released. `believed_resident` comes from `_residency_snapshot`;
        # `released` is the union of what each `_unload_endpoint` call
        # returned. Naming a model this same pass then unloaded would make
        # the ordering preference systematically wrong.
        #
        # Wholesale-replaced every admitting tick, bounded by the resident
        # set, written and read on the tick thread alone: no lock and no
        # pruning contract.
        self._resident_keys = frozenset(believed_resident - released)

        # ONE INFO LINE PER TICK THAT EVICTS AT ALL (§3.3g), which is any
        # tick with a trigger: a successful eviction used to be silent, so
        # an operator watching a host fill up could not tell "nothing
        # needed evicting" from "everything was skipped". Budget renders
        # as `unset` rather than `None`, because that is the posture, not
        # a missing value.
        triggers = []
        if own_endpoints:
            triggers.append("exclusive admission")
        if over_budget:
            triggers.append("over budget")
        if triggers:
            resident_bytes, admitted_marginal = self._last_snapshot_bytes
            logger.info(
                "worker: eviction pass (%s): swept %s endpoints, %s resident, "
                "%s admitted marginal, budget %s",
                " and ".join(triggers), len(endpoints), resident_bytes, admitted_marginal,
                budget_bytes if budget_bytes is not None else "unset",
            )
        return refused

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
