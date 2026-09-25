"""
The `vision.generate` job kind (ADR 0013, docs/adr/0013-inference-execution-
queue.md) -- the vision module's enrollment against the execution queue's
job-kind registry (`models.contracts.jobkinds`), mirroring `tools/rag/jobs.py`'s
`rag.ask` (that module's docstring is the fuller reference for the registry's own
shape and the enqueue/claim split below). Registered in
`tools/vision/apps.py::ready()` alongside the `vision.generate` role and
every registered operation (`TXT2IMG`/`IMG2IMG`/`INPAINT`/`UPSCALE`/`EDIT`),
inside the SAME feature gate.

`payload` shape: `{"operation": str, "params": dict, "inputs": dict,
"connection": str | None}` -- `operation` is a registered
`models.contracts.operations.Operation` key, `params` is that operation's
`validate_params` OUTPUT (the submitting surface -- the create page, an
agent, a management command -- validates before building the payload;
`run_generate` still validates again inside `submit_job` at claim time,
for defense in depth, never trusting an enqueue-time decision as its
run-time truth), the optional `inputs` maps a `"file"` param's key
(`Operation.file_param_keys()`) to a stored-image REFERENCE:
`"output:<GeneratedOutput id>"` or `"input:<JobInput id>"`, and the optional
`connection` is a per-generation `ModelConnection` pk (as a STRING, matching
the same field and the same meaning `rag.ask`'s payload already carries)
that stands in for the `vision.generate` role binding for this one job --
see `_resolve_model`, below. The payload therefore stays plain JSON -- no
upload object, no path, no bytes -- while a queued img2img/inpaint/upscale
job still runs on a real image, because `tools.vision.services.
resolve_inputs` -- the SAME resolver the create page calls -- re-reads the
platform's own copy and hands `submit_job` the same upload-shaped objects a
browser post produces. A reference naming a param the operation does not
declare as a file param is REFUSED, never dropped: silently running the
generation without it would run a generation nobody asked for, so
`run_generate` lets `resolve_inputs`'s `InputReferenceError` (a
`ValueError`) propagate uncaught, exactly like any other bad-payload
failure this handler does not catch.

Two resolution passes, same split as `rag.ask`:
- `plan_generate` (enqueue time) resolves `vision.generate` ONCE, the exact
  same call `tools.vision.services.preflight`/`submit_job` make
  (`models.contracts.bindings.resolve`), to build the one `ModelRef` the
  scheduler admits/claims against. Per `models/queue/scheduler.py`'s
  provenance contract, `footprint_bytes` stays `None` here -- claim-time
  code fills it in fresh, never from this snapshot. `exclusive=True`
  always: the ComfyUI adapter (`models.contracts.engines.comfyui`) now
  reports a measured `loaded_footprint` -- a post-run delta against the
  pre-run baseline -- and can `unload` a model via `/free`, but this job
  kind does not yet spend either seam. `plan_generate` still builds its
  `ModelRef` with no footprint snapshot, so every `vision.generate` job's
  model is `footprint_bytes=None` at claim time regardless -- which
  `scheduler.py`'s rule 2(b) already treats as "effectively exclusive" on
  its own. Declaring `exclusive=True` explicitly here is the honest,
  written-down version of what rule 2(b) would otherwise leave implicit,
  and is the ruled-safe default until a follow-up task wires this job
  kind's own resolution up to the adapter's measured footprint (not a
  quiet behavior change to smuggle in here).
- `run_generate` (claim time, on the worker) DOES re-resolve
  `vision.generate` itself (T9 fix-round Q1) -- via `_resolve_model`, the
  same payload-aware resolution `plan_generate` uses, called fresh
  immediately before `submit_job`, mirroring `rag.ask`'s `run_ask`, which
  duplicates its own resolve-then-health-check because `answer_question`
  takes already-resolved models directly: the reason is the same one --
  the picked connection may have been edited, or the role rebound, between
  enqueue and claim, and `plan_generate`'s enqueue-time snapshot must never
  be trusted as run-time truth. A `ValueError` out of `_resolve_model` (an
  unbound role, or a picked pk that no longer resolves / no longer answers
  `image-generation` -- the same fold `run_ask`'s own `_precheck` performs
  for its equivalent case) is caught and re-raised as `services.
  VisionUnavailable("unbound", services.role_unbound_message())`, so the
  queue card shows the SAME failure vocabulary (`state`, `failure_kind ==
  ROLE_UNBOUND`) and the SAME operator-facing sentence `submit_job`'s own
  internal `preflight()` produces for an unbound role -- one failure
  regardless of which resolution step actually failed. Once resolution
  succeeds, `submit_job(..., resolved=picked)` still performs the fresh
  HEALTH check (`preflight(resolved)` -> `_health_check`) -- that half of
  the re-check is untouched. `models` (the claimed job's `ModelRef`
  snapshot) stays unused by this handler precisely BECAUSE `_resolve_model`
  is the fresh resolution -- never the enqueue-time snapshot -- kept only
  because `models.contracts.jobkinds.JobKind.handler`'s signature requires
  it, the same shape `run_ask` accepts and ignores for its own reason.

Enqueue-time planner failures (a `ValueError` from `resolve()`, i.e. an
unbound `vision.generate` role) are NOT caught here, mirroring `rag.ask`:
`models.queue.backend.enqueue` calls the planner directly and lets a raise
propagate straight out of `enqueue()` uncaught -- correct because a caller
enqueuing a `vision.generate` job is expected to have already preflighted
the role itself (the page does, via `services.preflight()`), so a planner
failure here only ever fires on a genuine race or a caller that skipped its
own pre-check, either of which should surface loudly rather than silently
queuing a job certain to fail.

Module boundary: imports `models.contracts.*`, `tools.vision.*`, and exactly
one console function (`models.registry.bindings.resolve_connection_named`),
the same single console import surface `tools/rag/jobs.py` uses for the
same reason: a per-job connection override is a pk this module must resolve,
and `models.registry.bindings` is the ONE place a §5 module reaches for it
-- never `models.registry.models` directly.
"""
from __future__ import annotations

import logging

from django.utils.text import Truncator

from identity.contracts.principals import principal_from_payload
from models.registry.bindings import model_access_for, resolve_connection_named
from models.contracts.bindings import ResolvedModel, resolve
from models.contracts.jobkinds import JobContext, ModelRef
from models.contracts.operations import get_operation
from models.contracts.roles import IMAGE_GENERATION_CAPABILITY, RAG_EXTRACT_ROLE, VISION_GENERATE_ROLE
from tools.vision import services
from tools.vision.models import GenerationJob

logger = logging.getLogger(__name__)

# Wall-clock budget `run_generate` gives one generation to finish before
# handing back whatever state it is in. A plain module constant, not a
# Django setting -- matching `models/queue/worker.py`'s own "internal
# cadence, not an operator knob" line for its tuning constants: no
# `Operation` declares an expected runtime today (batch size and step count
# both affect it, and neither is a queue concern), so there is nothing yet
# to make this a per-operation value.
#
# This is a RUNAWAY BACKSTOP, not a liveness mechanism -- worker liveness is
# already covered independently, by `models/queue/worker.py`'s own heartbeat
# writer (`_maybe_heartbeat`, on its own cadence, orphan-swept by staleness),
# which keeps running regardless of how long this wait blocks. The bound
# below exists only to eventually hand back a wedged/hung engine rather than
# block a worker thread forever, so it must cover the SLOWEST real
# generation this hardware runs, with real margin -- not the fastest.
# Measured on the reference hardware (2026-08-24/25 live runs, see
# `tools/vision/README.md`'s "Known limits"): the edit-only family at 20
# steps ~= 15 minutes (~43 s/step), the other family's ordinary,
# non-distilled build at 20 steps ~= 35 minutes of
# sampling alone, plus up to ~25 minutes of cold load on top -- roughly an
# hour worst case observed. The former 600 s (10 minute) bound was an order
# of magnitude too small: it fired mid-run (queue job 32, at sampler step
# 13/20), which does not stop the generation (see the `timed_out` field's
# own docstring, below) but does release this job kind's exclusive model
# slot while ComfyUI is still sampling and the model is still resident --
# ADR 0012's known gap. Set to 4 hours: comfortably past every measured run
# with margin for slower hardware, while `timed_out` remains the honest
# backstop for a genuinely wedged engine.
GENERATE_WAIT_TIMEOUT_SECONDS = 4 * 3600.0

# This module's job-kind key, named once: `apps.py` registers it, the page
# enqueues under it, and the queued card reads its label from the registry.
JOB_KIND = "vision.generate"

# What the queue page CALLS the phase a generation is in, per
# `GenerationJob.Status`. Short, kind-owned words, exactly what
# `JobContext.report_progress`'s `label` is for -- the numbers beside them
# are elapsed seconds, which is the only quantity ComfyUI's HTTP surface
# genuinely lets us count (no per-step fraction exists there: history is
# written at task_done, and per-node progress is WebSocket-only).
_PROGRESS_LABELS = {
    GenerationJob.Status.QUEUED: "waiting on the image engine",
    GenerationJob.Status.RUNNING: "generating",
}


def _resolve_model(payload: dict) -> tuple[ResolvedModel, str]:
    """`(resolved, connection_name)` for the model this job runs on.

    An explicit `payload["connection"]` -- the per-generation picker's
    chosen `ModelConnection` pk, carried as a STRING so the payload stays
    plain JSON -- resolves through `models.registry.bindings.
    resolve_connection_named` (the pk-addressed twin of the role lookup, and
    the ONE override seam this platform has; there is no second mechanism).
    Anything else, including a blank field, resolves the `vision.generate`
    role, which is exactly what every job enqueued before the picker existed
    carries.

    A pk that no longer names a usable image-generation connection raises
    `ValueError`, uncaught: the operator asked for a model that is gone, and
    quietly substituting the role binding would run a generation on a model
    nobody chose. The same `ValueError` now covers a pk this payload's
    actor may not USE (`models.registry.access.ModelAccess`, IA-2 T14) --
    built from `principal_from_payload(payload)`, the same source
    `tools.rag.jobs` uses for `rag.ask`.
    """
    connection = payload.get("connection")
    if connection not in (None, ""):
        access = model_access_for(principal_from_payload(payload))
        return resolve_connection_named(int(connection), IMAGE_GENERATION_CAPABILITY,
                                        access=access)
    return resolve(VISION_GENERATE_ROLE), ""


def plan_generate(payload: dict) -> tuple[list[ModelRef], bool]:
    """Resolve the models a `vision.generate` job needs, at ENQUEUE time.

    TWO `ModelRef`s when `rag.extract` resolves, ONE when it does not --
    the same tolerant, multi-role shape `tools.rag.jobs.plan_ingest` uses
    for its own image/pdf-scanned branch (`rag.extract` + `rag.embed`),
    followed here rather than invented fresh:

    - `vision.generate` for the model this job will actually run on --
      the payload's picked connection when it names one, else the current
      `vision.generate` binding (`_resolve_model`). ALWAYS declared.
    - `rag.extract` -- the SAME extraction role `tools.rag.media.
      extract_to_sidecar` already resolves for its own derived-description
      seam, and the one `run_generate` (below) uses to describe this job's
      own output once generation succeeds (`services.describe_output`).
      Resolved TOLERANTLY: `resolve(RAG_EXTRACT_ROLE)` wrapped in
      `try/except ValueError`, logged once at INFO and simply left out on
      an unbound role -- describing is optional (requirement 3's
      non-fatal contract), so a box with no vision-capable chat model
      bound must still be able to generate images, exactly as it does
      today. `plan_ingest`'s own extract branch takes the identical
      fallback for the identical reason (that function's own docstring,
      "W1 DECISION D4").

    Both refs are declared `synchronous=True` (the field's own default,
    left unset here) rather than `False`: `run_generate`'s handler is the
    thing that drives BOTH models itself, in-process, as part of doing
    this one job's work -- unconditionally for `vision.generate`,
    conditionally (on a successful, output-bearing generation) for
    `rag.extract` -- the same "this job's own handler is the thing that
    calls it" test `tools.rag.jobs.plan_ingest` applies to ITS multi-role
    branches (extract+embed, transcribe+embed), never the
    `synchronous=False` `agents.runtime.jobs.plan_turn` gives a role a
    TOOL may or may not use without this job's own handler ever driving
    it. Declaring `rag.extract` here does NOT ask the scheduler to hold
    both models resident at once, and it cannot ask that: `ModelRef` has
    no notion of ordering or phase, only membership in the job's declared
    set (`models/contracts/jobkinds.py::ModelRef.synchronous`'s own
    docstring -- it "changes neither protection, nor the swept endpoint
    set, nor the budget arithmetic", only the barrier's wait decision).
    `run_generate` runs the two calls SEQUENTIALLY -- generation fully
    finished, then (and only then) the describe call -- but does NOT
    release the image model first (ruling 1: ComfyUI's `/free` has no
    per-model form, so releasing would cost the NEXT generation a cold
    load far larger than the problem it would solve; see that function's
    own comment). Both models may therefore be resident at once for the
    duration of the describe call -- an accepted cost, not an oversight
    -- which is exactly why declaring `rag.extract` here still matters:
    it tells the queue honestly that this job's memory footprint can
    include both, rather than letting an unmeasured second model go
    unprotected and uncounted.

    `footprint_bytes` stays `None` on every ref, per `models/queue/
    scheduler.py`'s provenance contract: claim-time code fills it in
    fresh, never from this snapshot. `exclusive=True` always -- see the
    module docstring; unaffected by whether `rag.extract` resolves.
    Raises `ValueError` for an unbound `vision.generate` role or an
    unusable picked pk, uncaught -- exactly as before this task; only the
    OPTIONAL second ref's own resolution is tolerant.
    """
    resolved, connection_name = _resolve_model(payload)
    model_refs = [
        ModelRef(
            role=VISION_GENERATE_ROLE,
            engine=resolved.engine,
            endpoint=resolved.endpoint,
            model_id=resolved.model_id,
            connection_name=connection_name,
        )
    ]
    try:
        extract_resolved = resolve(RAG_EXTRACT_ROLE)
    except ValueError:
        logger.info(
            "plan_generate: no rag.extract binding resolved -- reserving vision.generate "
            "only; run_generate's own description step will simply be skipped for this job"
        )
    else:
        model_refs.append(
            ModelRef(
                role=RAG_EXTRACT_ROLE,
                engine=extract_resolved.engine,
                endpoint=extract_resolved.endpoint,
                model_id=extract_resolved.model_id,
            )
        )
    return model_refs, True


def run_generate(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:  # noqa: ARG001 - see docstring
    """Run a `vision.generate` job: submit, wait, report.

    `models` (the job's claim-time `ModelRef` snapshot) is unused -- see
    module docstring: this handler re-resolves fresh itself
    (`_resolve_model`, immediately below) rather than trusting it.

    `ctx` (T3, `models.contracts.jobkinds.JobContext`): `ctx.job_id` is
    stamped onto the `GenerationJob` for correlation once `submit_job`
    returns (see the comment at that call site). `ctx.report_progress` is
    wired through `services.wait_for`'s `on_poll` seam (added 2026-08-25
    for exactly this): every poll of the wait loop reports the elapsed
    seconds and the phase word for the generation's current status, so the
    Queue page shows a live line for a running generation instead of
    nothing. `total` is always `None` -- ComfyUI's HTTP surface reports no
    fraction of a generation (history lands only at `task_done`; per-node
    progress is WebSocket-only), and the engine's own queue position, which
    it CAN report, travels on `JobStatus.queue_position` to the job card
    instead, because a position is not a fraction.

    1. Resolves any `"inputs"` references into stored files
       (`services.resolve_inputs`) -- the JSON-safe way a queued job
       carries an image.
    2. `_resolve_model(payload)` -- the fresh, claim-time re-resolve (T9
       fix-round Q1). A `ValueError` here (unbound role, or a picked pk
       gone bad) is caught HERE and re-raised as `services.
       VisionUnavailable("unbound", services.role_unbound_message())`.
       `services.submit_job(operation, params, files=..., resolved=picked)`
       -- validates, creates the `GenerationJob` row, and submits it to the
       engine, health-checking the ALREADY-resolved binding fresh
       (`preflight(resolved)`); it raises `VisionUnavailable("unreachable",
       ...)` itself if THAT check fails, and `ParamError` for bad params.
       Both `VisionUnavailable` cases (raised here for "unbound", raised
       inside `submit_job` for "unreachable") carry operator-facing text
       and are left to propagate -- `models.queue.worker.Worker._execute`
       catches a raising handler and stores `str(exc)` verbatim on the job
       row's `error` column, exactly like `run_ask`'s re-check failure.
    3. `services.wait_for(job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS,
       on_poll=report)` -- polls until the generation reaches a terminal
       state or the timeout elapses, per `wait_for`'s own semantics: a job
       still `queued`/`running` at the timeout is returned exactly as-is,
       not forced into a state it has not actually reached. `on_poll`
       reports progress on every tick (see `report`, defined below).

    Returns `{"job_id": str, "status": str, "timed_out": bool,
    "output_ids": [int, ...], "output_urls": [str, ...],
    "durations": dict[str, float | None]}`.
    `status` is the `GenerationJob.Status` value at the moment `wait_for`
    gave up -- `"done"`, `"failed"` (the engine's own words are on the job
    row's `error` column, not duplicated here), or still `"queued"`/
    `"running"` if the timeout elapsed first. This queue job itself always
    SUCCEEDS once it reaches this return -- an engine-side generation
    failure is a normal, expected `GenerationJob` outcome (exactly as
    `services.submit_job`'s own docstring establishes for an engine
    rejection: "does not raise... it simply failed immediately"), reported
    honestly through `status`/`output_ids`, never smuggled into raising
    this job kind's own handler. `timed_out` is `True` when the wall-clock
    budget elapsed with the generation still `queued`/`running`. The queue
    job still SUCCEEDS: waiting is what timed out, not generating -- the
    engine is still working, and the job's own card polls it to
    completion. `output_ids` and `output_urls` name the same files in the
    same order.
    """
    operation_key = payload["operation"]
    params = payload.get("params") or {}
    operation = get_operation(operation_key)
    # WHO ASKED -- resolved once and reused below for `submit_job`'s own
    # `actor` too, so the claim-time reference resolution and the row this
    # job writes never disagree about who is acting.
    actor = principal_from_payload(payload)
    # An unknown operation key is left to `submit_job`, which raises the
    # one operator-facing "Unknown operation" message -- a second copy of
    # that check here would be a second place to keep in sync.
    #
    # `actor`, not an open pass: IA-1 security fix -- an `inputs` reference
    # is re-resolved fresh at claim time (same as the model below), and it
    # must answer for THIS job's actor exactly as it did at enqueue time,
    # never for whoever happens to run the worker.
    files = (
        services.resolve_inputs(operation, payload.get("inputs") or {}, actor)
        if operation is not None
        else {}
    )

    # Re-resolved FRESH at claim time, never trusted from the enqueue-time
    # plan: the picked connection may have been edited, and the role may
    # have been rebound, between enqueue and claim. A `ValueError` here
    # means one of two genuinely different facts, and final-review finding
    # 4 is that they must not be told apart -- `_resolve_model` never
    # consults the role at all when `payload["connection"]` names one, so
    # a `ValueError` in that branch is ALWAYS about the picked pk, never
    # about the role (which may be bound, or may even be the exact
    # connection the pk used to name):
    #
    # - the payload named NO connection (the role path): mirrors `run_ask`'s
    #   own `_precheck`, folded into the same `VisionUnavailable("unbound",
    #   ...)` `submit_job` itself would have raised had resolution
    #   succeeded and only the health check failed -- `services.
    #   role_unbound_message()` so the operator sees the SAME sentence the
    #   page's own preflight shows.
    # - the payload NAMED a connection that no longer resolves / no longer
    #   answers `image-generation`: `VisionUnavailable("connection_
    #   unavailable", ...)`, `services.UNREGISTERED_CONNECTION_MESSAGE` --
    #   the SAME sentence the page's own picker refusal
    #   (`views._picker_failure_response`) and the schema endpoint's 404
    #   (`views.vision_operations`) give, not the honestly-wrong "No model
    #   assigned" a bound role would make a lie.
    try:
        picked, _name = _resolve_model(payload)
    except ValueError as exc:
        if payload.get("connection") not in (None, ""):
            raise services.VisionUnavailable(
                "connection_unavailable", services.UNREGISTERED_CONNECTION_MESSAGE
            ) from exc
        raise services.VisionUnavailable("unbound", services.role_unbound_message()) from exc
    job = services.submit_job(
        operation_key, params, files=files or None, resolved=picked,
        actor=actor,
    )
    # Correlate the generation with the queue row that asked for it,
    # while it is still running: without this the `GenerationJob`'s id is
    # revealed only in this handler's own result, at the very end, so
    # nothing could link a running queue job to the images it was
    # producing -- no `/queue/` -> `/vision/` link, and the page's queued
    # card could not become the real card until the whole generation had
    # finished. `ctx.job_id` is the claimed row's id exactly as the worker
    # resolved it at claim time (`models.contracts.jobkinds.JobContext`).
    #
    # Stamped HERE rather than passed into `submit_job`: the service layer
    # stays free of a queue-shaped argument every direct caller would have
    # to pass `None` for, and the millisecond window this opens is one the
    # poll endpoint already tolerates (it renders the placeholder for one
    # more tick when the row is not correlated yet).
    #
    # `services.correlate_queue_job`, not a bare `GenerationJob.objects`
    # write: `foundation/ops/tests/test_column_boundaries.py`'s IA-1 gate
    # makes `services.py` (creates) and `visibility.py` (reads) the only
    # two modules under `tools/vision` that may touch that manager.
    services.correlate_queue_job(job, ctx.job_id)

    def report(polled: GenerationJob, elapsed: float) -> None:
        """One `wait_for` tick -> one progress report. Called on EVERY poll;
        the worker throttles the actual database write itself
        (`models.queue.worker.PROGRESS_INTERVAL_SECONDS`), which is exactly
        why `JobContext`'s docstring invites a handler to call this from a
        loop without reasoning about write cost.

        `total=None` on purpose: nothing in ComfyUI's HTTP surface reports
        how much of a generation is done, and the wall-clock budget is our
        patience, not the work -- either as a denominator would draw a bar
        that means nothing. `_progress_text_and_percent` renders a
        total-less report as bare `mm:ss` plus the label, with no bar.

        A TERMINAL tick reports nothing at all: `wait_for` calls back after
        every refresh, including the one that found the job done or failed,
        and there is no honest label for that tick -- `_PROGRESS_LABELS`
        has no word for a finished job, so it would fall through to
        "generating" and describe the job as still working at the exact
        moment it stopped. The queue's own terminal writeback is what has
        the last word about a finished job.
        """
        if polled.is_terminal:
            return
        ctx.report_progress(
            int(elapsed),
            total=None,
            unit="seconds",
            label=_PROGRESS_LABELS.get(polled.status, "generating"),
        )

    job = services.wait_for(
        job, timeout=GENERATE_WAIT_TIMEOUT_SECONDS, on_poll=report
    )

    # Describe the job's own output, STRICTLY AFTER generation has fully
    # finished -- OWNER RULING: the two model calls are a SEQUENTIAL
    # CHAIN, never overlapped or started eagerly for speed.
    # `services.describe_if_ready` is the ONE gate BOTH callers of
    # `describe_output` share (vision-describes-its-own-output task,
    # ruling 3) -- requirement 4 (only a `DONE` job with an output, never
    # failed/refused/still-running), the tolerant `rag.extract` resolve
    # (an unbound role means nothing runs, no extra latency, on a box
    # that never bound it -- which is every box before an operator opts
    # in, and every test in this module that does not bind it), and the
    # call to `describe_output` itself all live there now, not duplicated
    # per call site.
    #
    # NO RELEASE OF THE IMAGE MODEL, AND DELIBERATELY SO -- an
    # unconditional release was built, measured by tracing (not running),
    # and REMOVED (ruling 1). `ComfyUIEngine.unload`'s own docstring is
    # explicit that ComfyUI's `/free` has no per-model form: `POST /free`
    # with `unload_models` set calls `unload_all_models()`, which frees
    # EVERY model at that endpoint, not just the one this job ran.
    # Releasing it would therefore make the NEXT generation at that
    # endpoint pay a full cold load -- this file's own `GENERATE_WAIT_
    # TIMEOUT_SECONDS` comment records that as "up to ~25 minutes" on the
    # reference hardware -- to avoid a few seconds of two models resident
    # at once. Orders of magnitude worse than the problem it would have
    # solved, and it would fire on any box where `rag.extract` is
    # actually bound, which is exactly the box this feature is FOR. Do
    # not add it back without a per-model free (a different engine, or a
    # future ComfyUI capability) to release against -- see this task's
    # own report for the full finding.
    services.describe_if_ready(job)

    # One owner for what a job looks like as data: the outputs' serving
    # URLs are `services.job_json`'s, not a second copy of `reverse()`
    # here that could name a different route.
    representation = services.job_json(job)
    return {
        "job_id": representation["id"],
        "status": representation["status"],
        # Explicit, because the alternative is unreadable: a SUCCEEDED
        # queue job whose `status` is "queued" means "we stopped waiting",
        # and nothing but this flag says so. The generation itself is
        # unaffected -- it is still running on the engine, and the job's
        # own card keeps polling it to completion.
        "timed_out": not job.is_terminal,
        "output_ids": [output["id"] for output in representation["outputs"]],
        "output_urls": [output["url"] for output in representation["outputs"]],
        # What it cost, from the same single owner of that arithmetic.
        "durations": representation["durations"],
    }


def summarize_generate(payload: dict) -> str:
    """One-line, operator-facing row summary for the job queue's listing.

    Final-review finding 6: calls `GenerationJob.headline_text` -- the
    SAME fallback chain `GenerationJob.headline` reads for a finished
    row -- rather than a second copy of it that only ever knew about
    `prompt` and silently missed `edit`'s own `instruction` param. Only
    the truncation is this function's own: `django.utils.text.Truncator.
    chars`, the same engine `summarize_ask` truncates a question with and
    `|truncatechars:120` uses on a card -- a queued row's summary must
    fit the queue listing exactly as a finished job's `headline` fits a
    card, even though `headline_text` itself does not truncate (a job
    card renders the RAW headline through its own template filter).
    """
    operation_key = payload.get("operation", "")
    params = payload.get("params") or {}
    return Truncator(GenerationJob.headline_text(operation_key, params)).chars(120)
