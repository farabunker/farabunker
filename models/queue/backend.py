"""
The execution queue backend (ADR 0013) -- the module
`settings.INFERENCE_QUEUE_BACKEND` names, and so the module
`models/contracts/queue.py`'s seam (`enqueue`/`get_job`) dispatches to.
Console-internal callers (the future queue page) may import this module
directly rather than going through the core seam, since they need
operations (`cancel_job`) that seam deliberately doesn't expose (see
`models/contracts/queue.py`'s docstring: cancellation is console-internal).

Two failure domains, kept separate on purpose:

- `QueueUnavailable` -- the `jobs_inferencejob`/`jobs_jobsettings` tables
  don't exist yet or the DB is unreachable (the same unmigrated-window
  tolerance `models/registry/bindings.py`'s `_bound_connection` shows for
  `RoleBinding`/`ModelConnection` -- `ProgrammingError`/`OperationalError`).
  Typed, not swallowed to `None`: unlike a binding, which degrading to
  "unbound" is a legitimate state, "no queue" must not look like "no job" --
  a caller asking about a specific job id needs to tell those two apart.
  Defined in `models.contracts.queue` (imported here, not redefined) so a
  `tools/*` caller can catch it without ever importing `models.queue`
  directly -- see that module's docstring for the full rationale. Callers render it
  however fits them (Ask -> 503, the queue page -> an empty state -- both
  future work, not this task's concern).
- Every other `ValueError` (bad kind, bad priority, ...) is a caller bug,
  raised uncaught -- exactly like `models.contracts.jobkinds.get_job_kind`
  and `models.contracts.bindings.resolve()` already do for their own "nothing
  usable here" cases.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.utils import timezone

from identity.contracts.principals import (
    ACTOR_KEY_KEY, ACTOR_KIND_KEY, OPEN_PRINCIPAL, SERVICE_PRINCIPAL, principal_from_payload,
)
from models.contracts.jobkinds import ModelRef, get_job_kind, invoke_on_terminal, resolve_dotted_path
from models.contracts.queue import QueueQuotaExceeded, QueueUnavailable
from models.registry.discovery import norm_endpoint, norm_tag
from models.queue.models import (
    CANCELLED,
    QUEUED,
    RUNNING,
    TERMINAL_STATES,
    InferenceJob,
    JobSettings,
)
from models.queue.scheduler import SchedCandidate, SchedModel, resident_bytes

logger = logging.getLogger(__name__)


def _guarded(fn: Callable) -> Callable:
    """Wrap `fn` so a `ProgrammingError`/`OperationalError` from the ORM
    (table doesn't exist yet, DB unreachable) raises `QueueUnavailable`
    instead -- the one place this translation happens, so every public
    function below gets it for free rather than repeating its own
    try/except (the `models/registry/bindings.py::_bound_connection`
    precedent, generalized to a decorator since this module has more than
    one entry point that needs it)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ProgrammingError, OperationalError) as exc:
            raise QueueUnavailable(f"{fn.__name__}: queue tables unavailable") from exc

    return wrapper


@dataclass(frozen=True)
class JobStatus:
    """The read-only status view `get_job` returns -- console-internal
    shape, not constrained by `models/contracts/queue.py` (which passes
    whatever the backend returns straight through, unchanged).

    `progress` (T3) is `InferenceJob.progress`, passed through unchanged --
    `None` for a job that has never reported one, or whose row was already
    cleared (queued, or finished -- the terminal writeback clears it on
    success, and it stays whatever a failed job's handler last wrote on
    failure; see `models.queue.worker.Worker._execute`'s own docstring for
    the asymmetry). Additive: `tools.rag.views.AskJobStatusView`'s
    running-state poll body is the one caller reading this today.

    `summary` (added 2026-08-25) is the job kind's OWN one-line summary of
    this job, from `summarize_job` below -- a caller waiting on a single
    job (`/vision/`'s queued card) needs to name it, and this seam still
    carries no payload for it to summarize itself."""

    id: int
    kind: str
    state: str
    position: int | None
    priority: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    models: list
    result: dict | None
    error: str
    progress: dict | None
    # The job kind's OWN one-line summary of this job (`summarize_job`
    # above). Not the payload: `QueueRow` carries the payload because a
    # listing page re-summarizes many rows at render time; this seam stays
    # payload-free and hands over the finished string, which is what a
    # single-job caller (the /vision/ queued card) actually needs to name
    # the work while it waits.
    summary: str


def summarize_job(kind: str, payload: dict) -> tuple[str, str]:
    """`(summary, kind_label)` for one job.

    Resolves the registered `JobKind` fresh at call time (never stored --
    matches `models.contracts.jobkinds`' own "dotted path, resolved lazily"
    contract) and calls its `summarizer(payload)`. Degrades to the raw
    `kind` key -- for both the label and the summary -- when the kind is
    unregistered (a kind renamed/removed after jobs referencing it were
    already enqueued, or a kind a test process never registered) or when
    the summarizer itself raises: a broken or incompatible summarizer must
    never take a page down, only lose this one job's summary text.

    Lives HERE rather than in `models.queue.views` (where it began as
    `_summarize`) because `get_job` needs it too: the queue's single-job
    read shape carries no payload by design, so the only honest way for a
    caller to NAME a job is the string the job's own kind produced.
    """
    try:
        kind_spec = get_job_kind(kind)
    except ValueError:
        return kind, kind
    try:
        summary = resolve_dotted_path(kind_spec.summarizer)(payload)
    except Exception:  # noqa: BLE001 -- a broken summarizer loses one line, never a page
        summary = kind_spec.label
    return summary, kind_spec.label


def _serialize_model_refs(model_refs: list[ModelRef]) -> list[dict]:
    """`ModelRef`s -> the JSON shape `InferenceJob.model_refs` stores -- see
    that field's docstring for why this is a snapshot, never a FK.
    `footprint_bytes` rides through unchanged (normally `None` at enqueue
    time; `models.queue.claim.claim_and_admit`'s claim-time admission
    fills it in)."""
    return [
        {
            "role": ref.role,
            "engine": ref.engine,
            "endpoint": ref.endpoint,
            "model_id": ref.model_id,
            "connection_name": ref.connection_name,
            "footprint_bytes": ref.footprint_bytes,
        }
        for ref in model_refs
    ]


def _resolve_priority(kind_spec, explicit: int | None, job_settings=None) -> int:
    """The priority chain (owner-specified, exact): explicit `priority` arg
    if not `None`, else the job kind's own `default_priority` if not
    `None`, else the queue-wide `JobSettings.default_priority`.
    Lower runs first (`InferenceJob.Meta.ordering`).

    `job_settings` (H39 follow-up) is the row `enqueue` already read for
    this call, so the LAST rung costs no query of its own. `None` means
    "nobody has a row to give" -- which for THIS step is not forgivable
    the way it is for the quota and the prune: a priority is a column on
    the row about to be written, not housekeeping around it, so the read
    is made here and its failure travels out through `@_guarded` as
    `QueueUnavailable`, exactly as it did when this function always read
    for itself.

    Positivity is enforced on whichever rung of the chain actually supplies
    the value -- a bad explicit `priority` argument is a caller bug, but a
    registered `JobKind.default_priority` of 0 (or negative) is equally a
    bug, in the job kind's own registration, and must fail loudly at
    enqueue time rather than silently producing an invalid row."""
    if explicit is not None:
        if explicit <= 0:
            raise ValueError(f"priority must be a positive integer, got {explicit!r}")
        return explicit
    if kind_spec.default_priority is not None:
        if kind_spec.default_priority <= 0:
            raise ValueError(
                f"job kind {kind_spec.key!r} has a non-positive default_priority: "
                f"{kind_spec.default_priority!r}"
            )
        return kind_spec.default_priority
    if job_settings is None:
        job_settings = JobSettings.get_solo()
    return job_settings.default_priority


def _enforce_principal_quota(payload: dict, job_settings) -> None:
    """Refuse the enqueue when `payload`'s own actor already holds
    `JobSettings.max_queued_per_principal` QUEUED-or-RUNNING
    jobs (C-7, round-3 hardening). A no-op when the setting is `None`
    (the shipped default -- see that field's own docstring) or when the
    actor is `SERVICE_PRINCIPAL`.

    THE ACTOR COMES FROM THE PAYLOAD ITSELF, never a parameter: every
    enqueuer already stamps `identity.contracts.principals.
    payload_fields(actor)` into its payload (the acting rule), so this
    reads it back with that module's own tolerant `principal_from_
    payload` rather than widening `enqueue`'s signature for every caller
    in the tree to learn a second way to say who is asking.

    `SERVICE_PRINCIPAL` IS EXEMPT, DELIBERATELY: it is the ONE shared
    identity the watcher, the registry's rematerialize callback and
    every `manage.py` shell command act as (`identity.contracts.
    principals`'s own docstring) -- the operator's own work, not a
    principal's, and a box doing a lot of it (a large reingest, a bulk
    re-encode) must never be throttled by a cap sized for one human's
    ordinary use. A payload with no actor keys at all -- every job
    enqueued before this phase existed -- resolves to `OPEN_PRINCIPAL`
    the same tolerant way `models.queue.visibility` already treats it,
    and IS counted: an anonymous caller on an open box is still one
    caller, and `OPEN_PRINCIPAL` is one shared identity every such
    caller's jobs already collapse onto for visibility purposes.

    Counts QUEUED and RUNNING only, matching `visibility.visible_jobs`'s
    own `payload__<key>` JSON filter shape -- a finished job (terminal:
    `SUCCEEDED`/`FAILED`/`CANCELLED`) frees its slot the moment it
    leaves those two states, with no separate bookkeeping to keep in
    sync.

    THE OPEN_PRINCIPAL MATCH IS WIDER THAN AN EXACT ONE (fix round 1,
    C-7 review): an exact `payload__actor_kind="open"` filter, the shape
    every OTHER actor uses, would silently UNDERCOUNT here -- a payload
    with no actor keys at all has no `actor_kind` in its stored JSON to
    match against; it is not literally `"open"`, it is simply absent.
    When THIS call's own actor resolved to `OPEN_PRINCIPAL` (whether
    from an explicit `payload_fields(OPEN_PRINCIPAL)` stamp or from no
    stamp at all -- `principal_from_payload`'s own tolerant fallback
    treats both the same), the count below also matches rows with no
    `actor_kind` key, so every anonymous caller's jobs -- stamped or
    not -- collapse onto the ONE shared count the docstring above
    promises, not two silently-separate ones.

    THE SETTINGS READ IS ITS OWN FORGIVEN FAILURE, same shape as
    `enqueue`'s own prune-failure forgiveness just below (and for the
    identical reason: `jobs_jobsettings` and `jobs_inferencejob` are two
    independent tables that can go unreachable independently, and this
    function runs BEFORE the job row is created -- unlike the prune
    step, which runs after). A `ProgrammingError`/`OperationalError`
    reading `JobSettings` must never turn a job the `InferenceJob` table
    is perfectly able to accept into a `QueueUnavailable` that was never
    really about that table at all; it degrades to "cap not enforced
    this call" instead, logged, never silent. `job_settings` IS THAT
    READ, made once by `enqueue` (H39 follow-up) and handed here as
    `None` when it failed -- the forgiveness is unchanged, it just no
    longer costs this function a query of its own."""
    actor = principal_from_payload(payload)
    if actor.kind == SERVICE_PRINCIPAL.kind:
        return
    if job_settings is None:
        logger.warning(
            "enqueue: skipped the per-principal queue quota check -- "
            "JobSettings itself is unreachable; the job is still queued"
        )
        return
    cap = job_settings.max_queued_per_principal
    if cap is None:
        return
    actor_match = Q(**{f"payload__{ACTOR_KIND_KEY}": actor.kind,
                       f"payload__{ACTOR_KEY_KEY}": actor.key})
    if actor.kind == OPEN_PRINCIPAL.kind and actor.key == OPEN_PRINCIPAL.key:
        # THE MISSING-KEY DISJUNCT IS THE ONLY OPEN RESOLUTION THE
        # PLATFORM'S OWN ENQUEUERS CAN PRODUCE (fix round 2, C-7
        # re-review). Every enqueuer here stamps its payload through
        # `identity.contracts.principals.payload_fields`, which always
        # writes BOTH keys as real strings -- so the one open-resolving
        # row this platform itself can leave behind is a payload with no
        # actor keys at all (every job enqueued before this phase), which
        # is exactly what this disjunct matches.
        #
        # `principal_from_payload`'s OTHER routes to `OPEN_PRINCIPAL` --
        # a payload that is not a dict, a non-string or out-of-vocabulary
        # `actor_kind`, or a kind that IS present beside a missing or
        # non-string key -- are reachable only from a hand-edited or
        # corrupt row. They are ACCEPTED RESIDUALS, deliberately not
        # matched here: widening the filter to chase them would need a
        # second, shapeless `payload__actor_kind` predicate for rows this
        # code never writes, and undercounting a corrupt row costs one
        # queue slot on a box that already has a hand-edited payload in
        # it -- the cheaper of the two mistakes.
        actor_match |= Q(**{f"payload__{ACTOR_KIND_KEY}__isnull": True})
    current = InferenceJob.objects.filter(
        Q(state__in=(QUEUED, RUNNING)) & actor_match
    ).count()
    if current >= cap:
        raise QueueQuotaExceeded(
            f"{current} jobs are already queued or running for this account — "
            f"the limit is {cap}. Wait for one to finish before starting another."
        )


@_guarded
def enqueue(kind: str, payload: dict, *, priority: int | None = None) -> int:
    """Enqueue `payload` under job kind `kind`, returning the new row's pk.

    1. Re-validates `kind` against the core registry (`get_job_kind`) --
       redundant with `models.contracts.queue.enqueue`'s own check when
       reached through that seam, but this module is also reachable
       directly by console-internal callers that skip the seam.
    2. Enforces the per-principal queued-job cap (`_enforce_principal_
       quota`, C-7/H39) -- may raise `QueueQuotaExceeded` before anything
       below runs.
    3. Resolves and calls the kind's `planner` (a `callable(payload) ->
       (list[ModelRef], bool)`) to get the models this job will need and
       whether it must run exclusively.
    4. Resolves `priority` via the chain in `_resolve_priority`.
    5. Creates the row `state=queued`, best-effort prunes finished jobs down
       to `JobSettings.get_solo().retention_limit`, and returns the new pk.

    THE QUOTA CHECK'S OWN ACCEPTED RACE (fix round 1, C-7 review): step 2's
    count and step 5's row insert are two separate autocommit statements,
    not one atomic unit, and no lock is taken between them. A principal
    sitting at `cap - 1` who fires several enqueues concurrently (several
    browser tabs, a double-submitted form) can have every one of them read
    the same `current = cap - 1` count before any of their own inserts
    lands, and all of them pass the check -- overshooting the cap by up to
    the number of genuinely concurrent callers. Accepted, deliberately:
    this is a LOW-severity flood control against one principal filling the
    queue, not a hard capacity guarantee, and serializing every enqueue in
    the tree (every job kind, every principal) behind a lock to close a
    window that at worst lets a handful of extra jobs through is a cost
    this fix does not pay.

    Pruning-failure fix (cross-session-reported defect): the row above is
    created BEFORE `_prune_finished_jobs` runs, so a `ProgrammingError`/
    `OperationalError` from THAT call alone must never turn a job that was
    genuinely enqueued into one this function reports as unqueued -- the
    `@_guarded` decorator on this function translates such an error into
    `QueueUnavailable`, and a caller catching that exception has every
    reason to believe nothing was queued (see this module's own docstring:
    "no queue" must not look like "no job"), while the row it never saw
    keeps existing and will still be claimed and run. Pruning is pure
    housekeeping -- it deletes ALREADY-TERMINAL rows, never touches the
    just-created one -- so its own failure carries no information about
    whether enqueueing worked; it must never be allowed to masquerade as
    one. Caught and logged here, narrowly (the same two exception types
    `@_guarded` itself translates, not a bare `except Exception`, so a
    genuine bug inside `_prune_finished_jobs` still raises loudly rather
    than being silently swallowed alongside the housekeeping-failure case
    this is meant to forgive), and this function still returns `job.pk`
    either way.
    """
    kind_spec = get_job_kind(kind)
    # H39 follow-up: ONE `JobSettings` read per enqueue, threaded to the
    # three steps below that each used to make their own. `None` when the
    # row is unreadable -- the quota and the prune both already forgive
    # that (each says so in its own place); `_resolve_priority` does not,
    # and re-reads so its failure still surfaces as `QueueUnavailable`.
    try:
        job_settings = JobSettings.get_solo()
    except (ProgrammingError, OperationalError):
        job_settings = None
        # round-3 final wave: the hoist (H39 follow-up) used to leave
        # this failure logged nowhere -- the two downstream warnings below
        # (the quota check, the prune) both lost `exc_info=True` when their
        # own `except` blocks were collapsed into this one read, since
        # neither still runs inside the block that actually caught the
        # exception. Logged HERE instead, with the traceback, the one place
        # left that still has it -- the forgiveness itself (`job_settings`
        # staying `None`, the job still queuing) is unchanged.
        logger.warning(
            "enqueue: JobSettings.get_solo() failed -- the quota check and pruning will "
            "both be skipped for this enqueue call; the job is still queued",
            exc_info=True,
        )
    _enforce_principal_quota(payload, job_settings)
    planner = resolve_dotted_path(kind_spec.planner)
    model_refs, exclusive = planner(payload)
    resolved_priority = _resolve_priority(kind_spec, priority, job_settings)

    job = InferenceJob.objects.create(
        kind=kind,
        state=QUEUED,
        priority=resolved_priority,
        payload=payload,
        model_refs=_serialize_model_refs(model_refs),
        exclusive=exclusive,
    )
    # T8 review minor 5 -- UNSTATED PRECONDITION this fix relies on: this
    # function must run OUTSIDE any ambient `transaction.atomic()` block a
    # caller might wrap it in. On Postgres, a `ProgrammingError`/
    # `OperationalError` from the prune query below poisons the CONNECTION's
    # current transaction, not merely the Python call that raised it --
    # catching the exception here does not undo that at the database level;
    # every later command on the SAME transaction (including the eventual
    # COMMIT) still raises "current transaction is aborted" until a
    # rollback. If a caller ever wrapped this whole `enqueue()` call in its
    # own `atomic()` block, the prune failure would poison THAT transaction,
    # and the `job` row created above -- part of the SAME transaction --
    # would be rolled back with it on exit, even though this function
    # returned what looks like a valid, durable pk: the fix's entire
    # guarantee ("a returned pk means a truly queued job") would silently
    # invert into its opposite. No caller does this today (confirmed by
    # reading the call graph: `models.contracts.queue.enqueue` -> this
    # function is always invoked at autocommit/no-ambient-atomic call
    # sites), but this is exactly why running this fix INSIDE a broader
    # transaction is never safe without a `SAVEPOINT` isolating the prune
    # query -- not built here, since no caller needs it; named here so a
    # future caller doesn't wrap this in `atomic()` and quietly reintroduce
    # the exact bug this fix closes.
    if job_settings is None:
        logger.warning(
            "enqueue: skipped pruning finished jobs after enqueueing job %s (kind=%r) -- "
            "JobSettings was unreadable at the top of this call; the new job was queued "
            "successfully regardless",
            job.pk, kind,
        )
    else:
        try:
            _prune_finished_jobs(job_settings.retention_limit)
        except (ProgrammingError, OperationalError):
            logger.warning(
                "enqueue: skipped pruning finished jobs after enqueueing job %s (kind=%r) -- "
                "the prune query itself failed; the new job was queued successfully regardless",
                job.pk, kind, exc_info=True,
            )
    return job.pk


def _prune_finished_jobs(limit: int) -> None:
    """Delete every terminal-state `InferenceJob` beyond the newest `limit`
    of them -- the exact `tools.rag.services._prune_ask_records` grammar
    (one `LIMIT 1 OFFSET limit` cutoff-pk query, newest-terminal-first,
    plus one bulk delete), scoped to `state__in=TERMINAL_STATES` so a
    queued or running row can never be counted toward the limit or
    deleted, no matter how large `limit` is set."""
    cutoff = (
        InferenceJob.objects.filter(state__in=TERMINAL_STATES)
        .order_by("-pk")
        .values_list("pk", flat=True)[limit : limit + 1]
    )
    cutoff_pk = next(iter(cutoff), None)
    if cutoff_pk is not None:
        InferenceJob.objects.filter(state__in=TERMINAL_STATES, pk__lte=cutoff_pk).delete()


@_guarded
def get_job(job_id: int) -> JobStatus | None:
    """The current `JobStatus` for `job_id`, or `None` for an unknown id.

    `position` is 1-based queue position among still-`queued` jobs strictly
    ahead of this one by `(priority, pk)` -- the same tie-break `Meta.
    ordering` uses -- or `None` for any non-queued state (a running/
    terminal job has no "position", it already has a state that says
    more)."""
    try:
        job = InferenceJob.objects.get(pk=job_id)
    except InferenceJob.DoesNotExist:
        return None

    position = None
    if job.state == QUEUED:
        ahead = InferenceJob.objects.filter(state=QUEUED).filter(
            _priority_pk_ahead(job.priority, job.pk)
        )
        position = ahead.count() + 1

    return JobStatus(
        id=job.pk,
        kind=job.kind,
        state=job.state,
        position=position,
        priority=job.priority,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        models=job.model_refs,
        result=job.result,
        error=job.error,
        progress=job.progress,
        summary=summarize_job(job.kind, job.payload)[0],
    )


def _priority_pk_ahead(priority: int, pk: int) -> Q:
    """A Q object matching rows strictly ahead of `(priority, pk)` in the
    `(priority, pk)` queue order -- lower priority first, then lower pk --
    shared by `get_job`'s position count (kept out of that function's body
    since the comparison is a comparator on a 2-tuple, not a single
    field)."""
    return Q(priority__lt=priority) | Q(priority=priority, pk__lt=pk)


@_guarded
def cancel_job(job_id: int) -> str:
    """Cancel `job_id` if it is still queued, returning an outcome string.

    The queued->cancelled transition is a single conditional `UPDATE`
    (`filter(pk=job_id, state=QUEUED).update(...)`) -- the race between a
    worker claiming the job and an operator cancelling it is decided by
    that `UPDATE`'s row count, never a check-then-act read followed by a
    separate write (which could cancel a job the same instant a worker
    claims it).

    Returns one of:
    - "cancelled" -- the row was queued and is now cancelled (the `UPDATE`
      matched exactly one row). Also schedules `kind`'s registered
      `on_terminal` hook (T9.5 audit §5, the stranded-Document fix), if
      any, via `transaction.on_commit` -- "for symmetry" with
      `models.queue.claim._sweep_orphans`' own on_commit scheduling of the
      same hook, even though `cancel_job` itself is not normally called
      inside an ambient transaction (Django runs an `on_commit` callback
      immediately when there is no open atomic block, so this ordinarily
      fires synchronously, right here -- see `models.contracts.jobkinds.
      invoke_on_terminal` for the hook's own tolerant lookup-then-call
      contract, and that function's docstring for why a broken hook can
      never turn this "cancelled" outcome into anything else). `kind`/
      `payload` are read BEFORE the cancelling `UPDATE`, not after (T9.5
      review nit) -- a SELECT run AFTER the `UPDATE` races a concurrent
      `_prune_finished_jobs` (every `enqueue()` runs one): once this job's
      state flips to `CANCELLED` it is eligible for pruning, and a prune
      landing in that window could delete the row before a post-UPDATE
      SELECT re-read it, silently skipping the hook. `kind`/`payload` are
      immutable for a row's whole lifetime (never written again after
      `enqueue()` creates it), so reading them before the `UPDATE` is
      exactly as accurate and closes the window: if the pre-read found no
      row, the `UPDATE` below cannot possibly match it either (a pk is
      never reused after a row is deleted), so `updated` is guaranteed `0`
      in that case and the hook is correctly never scheduled.
    - "unknown" -- no row with this pk at all.
    - "already_running" -- the row exists but is `RUNNING` (a worker won
      the race, or cancel was called after claim).
    - "already_finished" -- the row is already in a terminal state.
    """
    row = InferenceJob.objects.filter(pk=job_id).values("kind", "payload").first()
    updated = InferenceJob.objects.filter(pk=job_id, state=QUEUED).update(
        state=CANCELLED, finished_at=timezone.now()
    )
    if updated:
        if row is not None:
            transaction.on_commit(
                lambda kind=row["kind"], payload=row["payload"]: invoke_on_terminal(
                    kind, payload, "cancelled"
                )
            )
        return "cancelled"

    try:
        job = InferenceJob.objects.get(pk=job_id)
    except InferenceJob.DoesNotExist:
        return "unknown"

    if job.state == RUNNING:
        return "already_running"
    return "already_finished"


@dataclass(frozen=True)
class QueueRow:
    """One row of `QueueSnapshot`'s running/waiting/finished lists -- the
    raw facts the queue page (`models.queue.views`) needs per job. Kept
    console-internal (not `JobStatus` above): `JobStatus` is the seam's
    single-job read shape (`models/contracts/queue.py::get_job`, `position`
    among other things), this is a listing row for a page rendering many
    jobs at once and needs `payload` (to call the job kind's `summarizer`
    at render time) and `priority`/`exclusive`/`model_refs` that `JobStatus`
    doesn't carry at all.

    `footprint_bytes` is `InferenceJob.footprint_bytes` (the derived
    property, `None` for an unsized job) -- carried through unchanged so
    the view never re-derives it from `model_refs` itself.

    `progress` (T3) is `InferenceJob.progress`, passed through unchanged --
    `models.queue.views._present_row` is what turns it into the
    `progress_text`/`progress_percent` the Running section's template
    actually renders; this row stays the raw fact, same division as
    `footprint_bytes`/`footprint_human` above."""

    id: int
    kind: str
    payload: dict
    state: str
    priority: int
    exclusive: bool
    model_refs: list[dict]
    footprint_bytes: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str
    progress: dict | None
    not_before: datetime | None
    passed_over: int


@dataclass(frozen=True)
class QueueSnapshot:
    """Everything the queue page's one GET needs, in one shot -- see
    `queue_snapshot()` below for how each field is derived."""

    running: list[QueueRow]
    waiting: list[QueueRow]
    finished: list[QueueRow]
    finished_count: int
    oldest_waiting_since: datetime | None
    running_known_bytes: int
    running_has_unknown: bool


def _queue_row(job: InferenceJob) -> QueueRow:
    return QueueRow(
        id=job.pk,
        kind=job.kind,
        payload=job.payload,
        state=job.state,
        priority=job.priority,
        exclusive=job.exclusive,
        model_refs=job.model_refs,
        footprint_bytes=job.footprint_bytes,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        progress=job.progress,
        not_before=job.not_before,
        passed_over=job.passed_over,
    )


@_guarded
def queue_snapshot() -> QueueSnapshot:
    """Everything the queue page needs, read as ONE query set
    (`InferenceJob.objects.all()`, partitioned by state in Python) rather
    than one query per section -- three/four separate `.filter()` calls
    would each hit the DB independently for what is, in the end, one
    page render. `@_guarded` (this module's own decorator) turns a missing/
    unreachable table into `QueueUnavailable`, exactly like every other
    public function here.

    - `running`/`waiting`/`finished` are partitioned from the one fetch,
      `waiting` re-sorted by `(priority, id)` (defensive, matching
      `plan_admissions`'s own "never trust the caller's ordering" stance --
      the single fetch already comes back in that order via `Meta.
      ordering`, but a partition-then-filter is cheap insurance against
      that assumption quietly going stale) and `finished` sorted newest-
      first (`-pk`) since nothing about `Meta.ordering` orders terminal
      jobs usefully for an operator.
    - `finished_count` is simply `len(finished)` -- `_prune_finished_jobs`
      (this module, `enqueue`) already keeps the terminal rows in the DB
      capped at `JobSettings.retention_limit` on every enqueue, so there is
      no separate cap to apply here; the count IS the "kept" count the
      retention section's copy wants.
    - `oldest_waiting_since` is the MINIMUM `created_at` across every
      still-queued job -- deliberately NOT `waiting[0].created_at` (the
      head of `(priority, id)` order): a low-priority-number job submitted
      moments ago can sit at position 1 while a much older, higher-
      priority-number job is still waiting behind it. The worker-down hint
      (`models.queue.views`) needs "how long has *something* been waiting",
      not "how long has the head of the queue been waiting" -- those are
      different facts once priority and submission order diverge.
    - `running_known_bytes`/`running_has_unknown`: the queue page's
      "Running now: {used} of {budget}" line needs the exact dedup'd
      resident-memory number the scheduler itself would reason from, not
      a re-derived approximation that could quietly disagree with it --
      so this builds `SchedCandidate`/`SchedModel` from the running jobs'
      STORED `model_refs` (normalized the same way `models.queue.claim.
      _sched_candidate` does: `norm_endpoint`/`norm_tag` on the key) and
      calls `models.queue.scheduler.resident_bytes` on them, the same
      function `plan_admissions` uses. Deliberately does NOT re-resolve
      each ref through `footprint_for()` the way `claim_and_admit` does
      for an actual admission round: a running job's `model_refs` were
      already stamped with a freshly-resolved footprint at the moment it
      was claimed (`claim.claim_and_admit`, step 5), so re-measuring here
      would only add a model-discovery round-trip to every page render for
      a number that is already accurate -- this is a page render, not an
      admission decision. `running_has_unknown` is a separate fact, not
      read out of the dedup fold: `resident_bytes` deliberately treats an
      unknown-footprint model as contributing 0 bytes (see that function's
      own docstring), so "the known sum" and "is any of this actually
      unknown" must be tracked side by side -- reading `running_has_unknown`
      off of `InferenceJob.footprint_bytes` (`None` whenever any one of a
      job's models is unsized) is the direct, un-re-derived fact.
    """
    jobs = list(InferenceJob.objects.all())
    running_jobs = [job for job in jobs if job.state == RUNNING]
    waiting_jobs = sorted(
        (job for job in jobs if job.state == QUEUED), key=lambda job: (job.priority, job.pk)
    )
    finished_jobs = sorted(
        (job for job in jobs if job.state in TERMINAL_STATES),
        key=lambda job: job.pk,
        reverse=True,
    )

    oldest_waiting_since = min((job.created_at for job in waiting_jobs), default=None)

    candidates = [
        SchedCandidate(
            job_id=job.pk,
            priority=job.priority,
            exclusive=job.exclusive,
            models=tuple(
                SchedModel(
                    key=(
                        ref["engine"],
                        norm_endpoint(ref["endpoint"]),
                        norm_tag(ref["model_id"]),
                    ),
                    footprint_bytes=ref.get("footprint_bytes"),
                )
                for ref in job.model_refs
            ),
        )
        for job in running_jobs
    ]
    running_known_bytes = resident_bytes(candidates)
    running_has_unknown = any(job.footprint_bytes is None for job in running_jobs)

    finished = [_queue_row(job) for job in finished_jobs]
    return QueueSnapshot(
        running=[_queue_row(job) for job in running_jobs],
        waiting=[_queue_row(job) for job in waiting_jobs],
        finished=finished,
        finished_count=len(finished),
        oldest_waiting_since=oldest_waiting_since,
        running_known_bytes=running_known_bytes,
        running_has_unknown=running_has_unknown,
    )
