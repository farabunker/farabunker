"""
Job-kind registry.

A "job kind" is a named, model-consuming unit of work a feature app declares
against the execution queue (ADR 0013) -- e.g. "rag.ask". Feature apps
register their kinds at import time; the queue seam (`models/contracts/
queue.py`) reads `get_job_kind()` to validate an `enqueue()` call, and the
queue's worker reads `all_job_kinds()`/`get_job_kind()` to dispatch a
claimed job to its `planner`/`handler`/`summarizer` without either side
importing the other -- the same registry/lookup shape as
`models.contracts.roles.RoleSpec`, deliberately: this is that registry's twin,
one rung down the stack (a role names a model-consuming *purpose*; a job
kind names a model-consuming *unit of work* that gets queued, claimed, and
run against models resolved for it at enqueue time).

`planner`, `handler`, and `summarizer` are dotted-path strings, never live
callables, for the same reason `RoleSpec.rematerialize` is one: importing
them at registration time would force every feature app's planner/handler
module (and whatever those modules import) to load merely to declare a job
kind exists, and would tie the registering process to the worker process's
import graph. A dotted path defers that cost to the moment something
actually needs to call the function -- the worker resolves `handler` only
when it runs a claimed job, `queue.py` never resolves any of the three at
all. Kept pure and in-memory on purpose, matching `roles.py`: no Django, no
DB, no dependency on `tools/` or `models/registry/` -- `models/contracts/`
stays the one place both sides can register/read a shared definition from.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelRef:
    """The admission snapshot for one model a job kind's planner asked for.

    Captured at enqueue time from whatever `resolve()`s at that moment
    (`models.contracts.bindings.resolve`) so a job's model choice is pinned to
    what was live when it was queued, not re-resolved at claim time.
    `footprint_bytes` is left `None` here -- it is filled in later by
    console-side code at claim time (memory admission needs the model's
    on-disk/VRAM size, which isn't known to the pure planner call).
    """

    role: str
    engine: str
    endpoint: str
    model_id: str
    connection_name: str = ""
    footprint_bytes: int | None = None


@dataclass(frozen=True)
class JobContext:
    """The third argument every job-kind `handler` is called with (T3,
    lifting ADR 0013 §8's "per-endpoint budgets and progress reporting are
    named extension seams, not built" deferral) -- a handler's own window
    onto per-job progress display and checkpoint/resume state, with no DB
    access of its own.

    `job_id`/`attempt` are read-only facts about this run, taken from the
    claim descriptor (`models.queue.claim.claim_and_admit`'s return shape)
    exactly as the worker resolved it at claim time -- never re-queried.
    `attempt` is `InferenceJob.attempts` verbatim: `0` on a job's first
    run (it has never been orphaned), `1` once it has survived exactly one
    orphan requeue (`models.queue.claim._sweep_orphans` stamps `attempts=1`
    on the first orphaning; a SECOND orphaning fails the job permanently
    instead of requeuing it again -- see that function's own docstring --
    so `1` is also this field's practical ceiling). `checkpoint_state` is
    whatever the PRIOR attempt's own `checkpoint()` call last saved --
    `None` on a job's first attempt (`attempt == 0`), or on any attempt
    whose predecessor never called `checkpoint()` at all; a handler that
    knows how to resume reads this at the top of its own run, a handler
    that doesn't understand checkpointing simply never looks at it, same
    as any other field here.

    `_report`/`_checkpoint` are the two write paths, INJECTED by
    `models.queue.worker.Worker._execute` as plain closures -- this class
    is otherwise completely DB-free (no import of `models.queue.models`
    anywhere in this module), matching `models/contracts/` staying the one
    place neither side of the queue seam needs the other's storage layer to
    read a shared definition from (see this module's own docstring).
    Never call `_report`/`_checkpoint` directly -- go through
    `report_progress`/`checkpoint` below, which is what builds the actual
    JSON shape each writer expects.

    A handler MAY ignore this argument entirely -- exactly like it may
    already ignore `models` (see `JobKind.handler`'s own docstring) --
    and every job kind that predates this task still runs unmodified in
    that sense; only its call signature grew a third parameter. Calling
    `report_progress` often is fine: the WORKER throttles how often that
    call actually reaches the database
    (`models.queue.worker.PROGRESS_INTERVAL_SECONDS`), not this class, so
    a handler is free to call it on every loop iteration without
    reasoning about write cost itself. `checkpoint`, by contrast, is
    UNTHROTTLED -- every call is a real write -- so a handler should call
    it only at natural resume boundaries (after committing one durable
    unit of work), not on every iteration of a tight inner loop.

    Rejected alternative: a module-level reporter keyed by a thread-local
    (so a handler could call a bare `report_progress(...)` without
    threading `ctx` through every inner call) was rejected for the same
    ambient-global reason `models/contracts/gateway.py`'s
    `configure_settings()` was deleted (ADR 0010's 2026-08-23 amendment) --
    a shared, implicitly-read piece of per-thread state is still one more
    place for a job's own identity to silently leak into or out of, and
    the explicit-injection shape this class already uses costs nothing
    extra to thread through by hand.
    """

    job_id: int
    attempt: int
    checkpoint_state: dict | None
    _report: Callable[[dict], None]
    _checkpoint: Callable[[dict], None]
    # THE ONE-TIMEOUT SEAM (2026-09-17, ADR 0013's dated amendment): the
    # operator-editable `models.queue.models.JobSettings.
    # response_timeout_seconds`, stamped here by `models.queue.worker.
    # Worker._build_job_context` -- the ONE place this value crosses from
    # queue storage into `agents/`, which may not import `models.queue`
    # at all (import law). `agents.runtime.loop._run_turn` reads it back
    # to build BOTH the turn's own step budget deadline AND the chat
    # engine's own request timeout (`models.contracts.gateway.
    # get_llm_for`'s `request_timeout` kwarg) from the SAME number, so
    # the two timeouts that used to compete (the engine's own hidden
    # default, this platform's own turn deadline) are one. `None` --
    # never a number -- is the honest value for a caller with no queue
    # settings row behind it (`NULL_JOB_CONTEXT` below, a claim
    # descriptor from a version that predates this field): `agents.
    # limits.TURN_DEADLINE_SECONDS` is the fallback a reader substitutes
    # for `None`, not something this class guesses on its own behalf.
    response_timeout_seconds: float | None = None
    # THE KIND'S WAIT CEILING (spec §3.5a), stamped by
    # `models.queue.worker.Worker._build_job_context` off the SAME
    # settings row `response_timeout_seconds` above rides on -- never a
    # second read. `None` means this kind declares no ceiling and the
    # operator set none: a handler that does not wait on an engine simply
    # never looks at it, exactly as it may ignore `models`.
    wait_seconds: float | None = None

    def report_progress(
        self, done: int | float, total: int | float | None = None, *, unit: str, label: str = "",
    ) -> None:
        """Report display-only progress: `done` units of `total` (or
        `total=None` when the total genuinely isn't known yet -- never
        fabricate one, see this class's docstring). `unit` is one of
        `"seconds"`, `"pages"`, `"items"` (`models/queue/models.py`'s
        `InferenceJob.progress` field comment names the same three);
        `label` is a short, kind-owned word for what's being counted
        (e.g. `"chunks"`), shown alongside the numbers, blank by
        default. Builds the exact dict `InferenceJob.progress` stores and
        hands it to the injected `_report` writer -- throttled on the
        WORKER side, not here (see docstring)."""
        self._report({"done": done, "total": total, "unit": unit, "label": label})

    def checkpoint(self, state: dict) -> None:
        """Save `state` as this job's resume point, unthrottled -- every
        call is a real write. `state` is entirely kind-owned: this class,
        the worker, and the storage column it lands in
        (`InferenceJob.checkpoint`) never interpret its shape, only pass
        it through to the NEXT attempt's `checkpoint_state`."""
        self._checkpoint(state)


# A no-op `JobContext` for a caller with no real queue behind it --
# `tools.rag.ingest.ingest_path` (the CLI's synchronous entry point,
# which has no `InferenceJob` row to report progress against or checkpoint
# into) is the one caller today. `report_progress`/`checkpoint` become
# no-ops, `checkpoint_state` stays `None` (so a handler like `tools.rag.
# media.transcribe_to_sidecar` never mistakes a CLI run for a resumed
# one). A Null Object, not a `ctx: JobContext | None` threaded through
# every call site with a None-guard at each one -- a handler built around
# this class is written as if `ctx` is always real, which is simpler to
# read and to test than sprinkling `if ctx is not None` through its own
# control flow. Immutable/stateless, so one shared instance is safe to
# reuse across every CLI-driven call rather than constructing a fresh one
# per call. Lives here, beside `JobContext` itself, rather than in
# `tools.rag.media` (its original, T7-era home) -- `tools.rag.ingest`
# needs it too (see `run_ingest_for`'s own `ctx if ctx is not None else
# NULL_JOB_CONTEXT` swap), and `models/contracts/` is the one place neither
# `tools/rag/media.py` nor `tools/rag/ingest.py` needs the other's
# import graph to reach a shared, feature-agnostic Null Object.
NULL_JOB_CONTEXT = JobContext(
    job_id=0,
    attempt=0,
    checkpoint_state=None,
    _report=lambda progress: None,
    _checkpoint=lambda state: None,
)


@dataclass(frozen=True)
class JobKind:
    """A model-consuming unit of work a feature app declares against the queue.

    `planner`, `handler`, and `summarizer` are dotted-path strings to
    callables, resolved lazily by whoever needs to call them (never by this
    registry) -- see the module docstring for why.

    Fields:
        key: Unique job-kind key (e.g. "rag.ask").
        label: Operator-facing label, feature-authored.
        planner: Dotted path to `callable(payload: dict) ->
            tuple[list[ModelRef], bool]` -- the model refs the job needs plus
            an exclusive-run flag, called at enqueue time.
        handler: Dotted path to `callable(payload: dict, models:
            list[ModelRef], ctx: JobContext) -> dict` -- runs on the worker
            once the job is claimed, returns the result JSON. `ctx` (T3)
            is this job's `JobContext` -- progress display and checkpoint/
            resume, built and injected by `models.queue.worker.
            Worker._execute`; a handler may ignore it entirely, same as it
            may already ignore `models` (see `JobContext`'s own
            docstring).

            THE RULE THAT COMES WITH `ctx.wait_seconds` (spec §3.5b), and
            it is not optional: a handler whose wait ceiling expires MUST
            NOT write a terminal outcome while its own engine still
            reports the work running. The exclusive slot is released the
            instant the job row goes terminal, so a handler that gives up
            waiting and reports success (or failure) hands the machine to
            the next admission while its own engine is still sampling.
            Such a handler must either keep holding (continuing to report
            progress, which keeps the row alive under the worker's
            heartbeat) or cancel the engine-side work and confirm the
            engine is terminal, and only then return. The queue enforces
            the OTHER side of this from spec §3.3(d): the barrier catches
            a still-working engine before the next EXCLUSIVE job launches,
            because the barrier-polling adapter withholds `True` while a
            prompt is still running -- but the exclusive->non-exclusive
            case and the cross-engine case stay unenforceable (spec §11),
            which is exactly why this rule has to be published here
            rather than left implicit.
        summarizer: Dotted path to `callable(payload: dict) -> str` -- a
            one-line row summary for operator-facing job listings.
        default_priority: This kind's rung of the priority chain, or `None`
            to use the global default.
        on_terminal: Dotted path to `callable(payload: dict, state: str) ->
            None` (T9.5 audit §5, the stranded-Document fix), or `None`
            (most kinds) for "nothing to do". Called for a job that reaches
            a TERMINAL state WITHOUT ever running its own `handler` -- the
            THREE paths a handler's own success/failure writeback never
            covers, because the handler never started:
            - cancelled while still queued (`models.queue.backend.
              cancel_job`, `state="cancelled"`);
            - permanently failed by a second orphaning
              (`models.queue.claim._sweep_orphans`, `state="failed"`);
            - the handler itself never got to run because resolving it
              failed first -- an unregistered job kind, a bad `handler`
              dotted path, or a malformed model-ref dict
              (`models.queue.worker.Worker._execute`'s `handler_started`
              guard, `state="failed"`).
            Invoked (via `invoke_on_terminal` below) AFTER the job row's
            own terminal write COMMITS (all three call sites schedule it
            with `transaction.on_commit`, not inline) -- `_sweep_orphans`
            runs inside `claim_and_admit`'s advisory-lock transaction, and
            this hook is arbitrary feature-app code that must never run
            inside that lock's window. Exceptions from the hook are caught
            and logged by `invoke_on_terminal`, never propagated -- a
            broken hook must never break cancel, the orphan sweep, or the
            worker's own failure writeback (the same never-500 philosophy
            `models.queue.views._summarize` already applies to a job
            kind's summarizer). A kind whose own handler is the only thing
            that can ever leave a stranded side-effect behind (nothing
            external to fix up once a job never ran at all) correctly
            leaves this `None`.
        stale_after_seconds: How long this kind's running job may go without
            a heartbeat before the orphan sweep reclaims it, or `None` to
            use the worker's own global cutoff. CODE-DECLARED, never
            operator-editable: a kind's staleness is a property of what the
            work DOES -- a sub-second embed and a job that cold-loads a
            large model for minutes cannot share one number -- in the same
            spirit as `default_priority` above. Read by
            `models.queue.claim._sweep_orphans`, which resolves the whole
            kind->threshold map itself; nothing else branches on it.
        default_wait_seconds: How long this kind's handler may wait on its
            ENGINE before it gives up, or `None` for "this kind does not
            wait". CODE-DECLARED like `stale_after_seconds`; the OPERATOR
            may override it per kind on the job-execution settings page
            (`models.queue.models.JobSettings.kind_wait_seconds`), and the
            worker stamps whichever wins onto `JobContext.wait_seconds`.

            THE RULE THAT COMES WITH IT, and it is not optional: a kind
            whose wait ceiling expires MUST NOT write a terminal outcome
            while its engine still reports the work running. `handler`'s
            own docstring above states it in full -- what such a handler
            must do instead, and where the queue's own enforcement stops.
    """

    key: str
    label: str
    planner: str
    handler: str
    summarizer: str
    default_priority: int | None = None
    on_terminal: str | None = None
    stale_after_seconds: int | None = None
    default_wait_seconds: int | None = None


_JOB_KINDS: dict[str, JobKind] = {}


def register_job_kind(kind: JobKind) -> None:
    """Register `kind` under its `.key`, replacing any existing entry.

    Idempotent: registering the same key again simply overwrites the prior
    entry, so re-importing a module that calls this at import time is safe
    (matches `models.contracts.roles.register_role`).
    """
    _JOB_KINDS[kind.key] = kind


def all_job_kinds() -> list[JobKind]:
    """Return all registered job kinds, in registration order."""
    return list(_JOB_KINDS.values())


def get_job_kind(key: str) -> JobKind:
    """Return the registered job kind for `key`.

    Raises `ValueError` if no job kind is registered under that key (matches
    `models.contracts.engines.get_engine`'s "always resolves or raises"
    idiom -- unlike `roles.get_role`, a job kind is never optional: an
    `enqueue()` call for an unregistered kind is a caller bug, not a state
    to degrade gracefully from).
    """
    try:
        return _JOB_KINDS[key]
    except KeyError:
        raise ValueError(f"Unknown job kind: {key!r}") from None


def resolve_dotted_path(path: str) -> Callable:
    """Resolve a dotted-path string to the callable it names.

    Thin, shared name for the one resolution mechanism this codebase already
    uses for a dotted-path callable field (`models.contracts.bindings.resolve`'s
    provider dispatch, `models.registry.drift.run_rematerialize`'s
    `rematerialize` resolution): Django's `import_string`. The intended
    caller is console-side worker code resolving a `JobKind`'s `planner`/
    `handler`/`summarizer` (not built in this task); it is not used by
    `models/contracts/queue.py`, whose `INFERENCE_QUEUE_BACKEND` names a
    *module* rather than a single callable and so resolves via
    `importlib.import_module` instead. Does not cache -- a caller wanting
    fresh-per-call semantics gets that for free by simply not caching this
    function's return value across calls, same as those two precedents.
    """
    return import_string(path)


def invoke_on_terminal(kind: str, payload: dict, state: str) -> None:
    """Call `kind`'s registered `JobKind.on_terminal` hook (if any) for a
    job that just reached a terminal state WITHOUT its own `handler` ever
    running (T9.5 audit §5) -- the shared call this module offers so
    `models.queue.backend.cancel_job`, `models.queue.claim.
    _sweep_orphans`'s permanent-fail branch, and `models.queue.worker.
    Worker._execute`'s handler-never-started branch invoke the hook
    identically, rather than each reimplementing this same tolerant
    lookup-then-call by hand. `state` is `"cancelled"` or `"failed"`,
    passed straight through to the hook -- see `JobKind.on_terminal`'s own
    docstring for the full contract, including WHY all three call sites
    schedule this via `transaction.on_commit` rather than calling it
    inline (not this function's concern: it has no idea whether it's being
    called from inside a transaction).

    Tolerant end to end, the same degrade-and-log philosophy `models.queue.
    views._summarize` already applies to a job kind's summarizer:
    - `kind` unregistered (hot-swapped/removed since the job was enqueued,
      or a test process that never registered it) -- logged, skipped.
    - `on_terminal is None` (every kind except `rag.ingest` today -- no
      stranded side-effect to fix up) -- a silent no-op, the ordinary case.
    - The hook itself raises -- caught and logged here, NEVER propagated:
      a broken hook must never break `cancel_job`'s own "cancelled" return,
      `_sweep_orphans`' own sweep completion, or `Worker._execute`'s own
      failure writeback.
    """
    try:
        kind_spec = get_job_kind(kind)
    except ValueError:
        logger.warning("jobkinds: on_terminal skipped -- unregistered job kind %r", kind)
        return

    if kind_spec.on_terminal is None:
        return

    try:
        hook = resolve_dotted_path(kind_spec.on_terminal)
        hook(payload, state)
    except Exception:  # noqa: BLE001 -- a broken hook must never break cancel/sweep
        logger.exception(
            "jobkinds: on_terminal hook %r raised for job kind %r (state=%r)",
            kind_spec.on_terminal, kind, state,
        )
