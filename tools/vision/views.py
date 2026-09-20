"""
Vision module HTTP surface (spec §4.7) -- the `/vision/` page.

The page holds NO generation logic: every action goes through
`tools.vision.services`, so the chatbot tool that calls the same functions
later behaves identically. It also never learns which engine is bound --
only the platform types (`Operation`, `PreflightResult`, `GenerationJob`)
cross this line.
"""
from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from identity.access import is_admin, sees_all_content
from identity.contracts.principals import payload_fields
from identity.request import principal_for_request
from models.registry.bindings import (
    MODEL_FORBIDDEN_MESSAGE, model_access_for, picker_options, resolve_connection,
    resolve_connection_named, role_primary,
)
from foundation.format import format_timecode
from foundation.http import mark_private
from models.contracts.bindings import ResolvedModel
from models.contracts.jobkinds import get_job_kind
from models.contracts.operations import (
    Operation, ParamError, get_operation, operations_for, validate_params,
)
from models.contracts.queue import QueueUnavailable, enqueue, get_job
# C-7/H39 reaches this route too. A queue that refuses because this
# account already holds the operator's whole standing-job allowance is
# saying something different from a queue that cannot be reached, and
# `generate` below answers the two differently.
from models.contracts.queue import QueueQuotaExceeded
from models.contracts.roles import IMAGE_GENERATION_CAPABILITY, VISION_GENERATE_ROLE
from models.queue.visibility import (
    may_read_job_content as may_read_queue_job_content,
    may_see_job_id,
    visible_jobs as visible_queue_jobs,
)
from tools.vision import services
from tools.vision.jobs import JOB_KIND
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.visibility import may_read_job, visible_jobs

logger = logging.getLogger(__name__)


def page_operations(resolved=None) -> list[Operation]:
    """Every operation this page can serve for the SELECTED model, in
    registration order. The first is the default a bare `/vision/` lands on.

    The page reads the REGISTRY, narrowed by the engine
    (`services.operations_for_model`) -- it names no operation of its own,
    so registering a mode in `VisionConfig.ready()` is the whole of making
    it appear here, and a model that cannot run one is the whole of making
    it disappear.

    A model that supports NOTHING returns `[]` here, not the full registry
    -- `resolve_page_operation` still falls back to the registered default
    for that case rather than crashing on an empty list, but that fallback
    is silent about WHY: `context["operations_supported"]` (an empty list
    from here) is what drives the page's own honest banner, in
    `CreatePageView`/`_create_page_response`/`_picker_failure_response`
    (owner ruling 2026-08-24(c)).
    """
    return services.operations_for_model(resolved)


def resolve_page_operation(key: str | None, available: list[Operation] | None = None) -> Operation:
    """The `Operation` a request names, or the default when it names none.

    A key that is not a registered image-generation operation AT ALL is a
    plain `Http404`: a stale link or a hand-typed URL must land on a 404,
    never on a form built from a schema this page cannot run.

    A key that IS registered but that the SELECTED model has no graph for is
    a different thing, and a 404 would be a lie about the platform: the
    operator switched models and their mode went away with it. That falls
    back to the selected model's first mode, and the chooser shows what that
    model does offer.

    A selected model that supports NOTHING falls back to the named (or
    default) registered operation, so the page still renders and its banner
    can explain -- an empty page explains nothing.
    """
    registered = operations_for(IMAGE_GENERATION_CAPABILITY)
    if available is None:
        # No opinion was ever asked -- a caller that has not narrowed at
        # all (a future caller, or a test calling this directly). The full
        # registry answers.
        supported = registered
    elif not available:
        # A REAL fact, not "no opinion": the selected model's engine
        # reported it supports nothing. The plan's own fallback still
        # applies here (the named/default registered operation) so this
        # function never crashes on an empty list; the page's own
        # `operations_supported` banner (owner ruling 2026-08-24(c)) is
        # what makes that fallback honest to the operator instead of a
        # silent lie that the default mode will work.
        supported = registered
    else:
        supported = available
    if not key:
        return supported[0]
    named = next((operation for operation in registered if operation.key == key), None)
    if named is None:
        raise Http404(f"Unknown image-generation operation {key!r}.")
    if named.key not in {operation.key for operation in supported}:
        return supported[0]
    return named


# Final-review finding 4: moved to `services.UNREGISTERED_CONNECTION_
# MESSAGE` so `jobs.run_generate`'s claim-time re-check reads the exact
# same sentence this page's picker refusal does -- one copy, imported
# below, not re-declared here.
_UNREGISTERED_CONNECTION_MESSAGE = services.UNREGISTERED_CONNECTION_MESSAGE


def _connection_picker_options(principal, selected: str) -> list[dict] | None:
    """The form's `Model` `<select>` options, in picker order, or `None`
    when there is nothing to pick from.

    A thin wrapper over `models.registry.bindings.picker_options`
    (final-review finding 8 -- lifted out of this function and its
    near-identical `tools.rag.views` copy), fixed to the image-generation
    capability and `vision.generate`. See that function's own docstring
    for the full option-building rule (labels, the primary/`selected`
    preselection, the `None`-when-empty case). `principal` is REQUIRED --
    a connection this principal may not use is dropped from the options
    (`models.registry.access.ModelAccess`, IA-2 T14).
    """
    return picker_options(IMAGE_GENERATION_CAPABILITY, VISION_GENERATE_ROLE, selected,
                          access=model_access_for(principal))


# How many recent jobs the create page shows as cards.
RECENT_JOBS = 8

# Outputs shown per gallery page.
GALLERY_PAGE_SIZE = 24

# The two honest failures a page that talks to the queue can hit, worded
# the way `tools/rag/views.py` words the same two.
_QUEUE_UNAVAILABLE_MESSAGE = "The queue isn't ready yet — run database migrations, then try again."
_QUEUE_ENQUEUE_FAILED_MESSAGE = "Couldn't add your generation to the queue — nothing was queued."

# How a queue state reads on a card. Written here rather than taken from
# the queue's own vocabulary because these are sentences for an operator
# watching one submission, not the queue page's status column.
_QUEUE_STATE_LABELS = {
    "queued": "Queued",
    "running": "Starting…",
    "succeeded": "Finished",
    "failed": "Failed",
    "cancelled": "Cancelled",
}
_QUEUE_TERMINAL_STATES = ("succeeded", "failed", "cancelled")

# Which chip modifier a queue state wears. NOT a third spelling of `state`
# (the card still renders `state_label` and branches on `terminal`): this
# is the presentation class, derived once here so the template carries no
# state vocabulary of its own -- and it collapses five queue words onto
# the four the shared chip actually has.
_QUEUE_STATE_CLASSES = {
    "queued": "queued",
    "running": "running",
    "succeeded": "done",
    "failed": "failed",
    "cancelled": "failed",
}


def _queue_card_context(queue_job_id: int, job_status, principal) -> dict:
    """What `_queued_card.html` needs for one queued submission.

    IA-1: before ANY `GenerationJob` row exists, this is the ONLY card
    for the job, and its `summary` is the prompt (`jobs.summarize_
    generate`) -- content. `content_hidden` (`models.queue.visibility.
    may_read_job_content`) decides whether it may be shown; when it may
    not, both the LABEL and the ERROR text are replaced with the same
    "Content hidden." constant `models/queue/views.py`'s own queue page
    uses -- hiding the answer/state and leaving the prompt underneath it
    would not be hiding anything.

    The payload itself is read through the sanctioned `models.queue.
    visibility.visible_jobs` seam (never `models.queue.models` directly
    -- `tools/` may reach only `.visibility` under import-law rule 2),
    keyed on `queue_job_id` (the `InferenceJob` pk this card polls).
    """
    state = getattr(job_status, "state", "queued")
    if sees_all_content(principal):
        hidden = False
    else:
        row = visible_queue_jobs(principal).filter(pk=queue_job_id).first()
        hidden = not may_read_queue_job_content(principal, row.payload if row else None)
    # The job KIND was all this card could say while the queue's single-job
    # read carried nothing but state and position. It now carries the
    # kind's own `summary` -- for `vision.generate` that is the operator's
    # prompt (`jobs.summarize_generate`) -- so the card names the work from
    # the first tick, on the XHR path, the no-JS redirect path, and every
    # poll, all from one source. `getattr` because this seam passes the
    # BACKEND's object through unchanged and this module must not assume a
    # console-side shape.
    summary = "" if hidden else (getattr(job_status, "summary", "") or "").strip()
    # Elapsed since SUBMISSION (fix round 2026-08-25): before this row's
    # `GenerationJob` exists at all, `job_status.created_at` (the
    # `InferenceJob`'s own enqueue stamp) is the only clock there is --
    # this platform-side wait was previously invisible on the placeholder.
    # LIVE (computed against `now` on every poll, exactly like the real
    # card's own `durations` while non-terminal): the queue job is still
    # waiting/running here, so the number must keep moving. `getattr`
    # again -- `created_at` is not optional on `JobStatus`, but this
    # function must not assume a console-side shape it does not own.
    submitted_at = getattr(job_status, "created_at", None)
    elapsed_display = (
        format_timecode(max(0.0, (timezone.now() - submitted_at).total_seconds()))
        if submitted_at is not None
        else ""
    )
    return {
        "queue_job_id": queue_job_id,
        "poll_url": reverse("vision-queue-status", args=[queue_job_id]),
        "label": "Content hidden." if hidden else (summary or get_job_kind(JOB_KIND).label),
        "content_hidden": hidden,
        "state_label": _QUEUE_STATE_LABELS.get(state, state),
        "state_class": _QUEUE_STATE_CLASSES.get(state, "queued"),
        "position": getattr(job_status, "position", None),
        "error": "" if hidden else (getattr(job_status, "error", "") or ""),
        "terminal": state in _QUEUE_TERMINAL_STATES,
        "elapsed_display": elapsed_display,
    }


def _generation_from_result(job_status, principal) -> GenerationJob | None:
    """The generation a FINISHED queue job's result names, if any -- and
    that this `principal` may see.

    The queue job's own `result` is the fallback for a generation whose
    row is not correlated yet -- one submitted before the column existed,
    or deleted and re-created. The `queue_job_id` lookup that runs first
    lives in `queue_job_status`. A malformed id in a result is treated as
    no job at all rather than a 500 -- a result is data, not a promise.
    """
    result = getattr(job_status, "result", None) or {}
    raw = result.get("job_id")
    if not raw:
        return None
    try:
        return visible_jobs(principal).filter(pk=raw).first()
    except (ValueError, ValidationError):
        return None


def queue_job_status(request, queue_job_id: int):
    """GET /vision/queue/<id>/ -- the queued placeholder's poll target.

    Answers with the REAL job card as soon as the queue job has produced a
    generation (correlated by `GenerationJob.queue_job_id`, looked up
    BEFORE the queue is consulted at all), and with the placeholder until
    then. The card the page is holding is replaced
    either way, so a submission goes queued -> running -> images with no
    gap and no second card.

    404 for a queue job the queue does not know (it may have aged out of
    the retention limit) OR that this principal may not see
    (`models.queue.visibility.may_see_job_id` -- IA-1) -- the page's
    script replaces the card with "This generation was removed." either
    way, so a member polling somebody else's queue job cannot tell it
    apart from one that genuinely aged out. 503 for a queue that cannot
    be read at all, the same unmigrated-window tolerance `AskView` gives
    its own enqueue.
    """
    # THE GENERATION FIRST, the queue second. A queue row is pruned to
    # `JobSettings.retention_limit` on every enqueue, so a succeeded queue
    # job -- including one that succeeded with `timed_out: true` while its
    # generation kept running -- can vanish from under a card that is
    # still polling. Asking the queue first would answer 404, and the
    # page's script would replace a live generation's card with "This
    # generation was removed." The generation is the durable record; only
    # when there is no generation AND no queue row is there nothing to
    # show.
    #
    # `queue_job_id` is not unique -- a worker-retry re-run of the same
    # queue row calls `run_generate` again and stamps a SECOND
    # `GenerationJob` with the same id (jobs.py's ctx-stamp). `.first()`
    # relies on `GenerationJob.Meta.ordering` (`-created_at`) to return the
    # newest of them, which is deliberate: the latest run is the one this
    # card should show.
    #
    # `visible_jobs(principal)`, not the bare manager: a `GenerationJob`
    # this principal may not read must not answer here either, even
    # though it is found by `queue_job_id` rather than by its own pk.
    principal = principal_for_request(request)
    job = visible_jobs(principal).filter(queue_job_id=queue_job_id).first()
    if job is not None:
        return _render_card(request, services.refresh_job(job))

    if not may_see_job_id(principal, queue_job_id):
        raise Http404(f"Queue job {queue_job_id} is no longer in the queue.")

    try:
        job_status = get_job(queue_job_id)
    except QueueUnavailable:
        return render(
            request, "vision/_unavailable.html",
            {"message": _QUEUE_UNAVAILABLE_MESSAGE}, status=503,
        )
    if job_status is None:
        raise Http404(f"Queue job {queue_job_id} is no longer in the queue.")

    job = _generation_from_result(job_status, principal)
    if job is not None:
        return _render_card(request, services.refresh_job(job))
    return render(
        request, "vision/_queued_card.html",
        {"card": _queue_card_context(queue_job_id, job_status, principal)},
    )


def _queued_placeholder(request) -> dict | None:
    """The `?queued=<id>` card for the create page -- the no-JS path's
    answer to "where did my submission go".

    `None` for a missing, malformed, or unknown id, `None` once the
    generation row exists (the Recent list is already showing the real
    card, and a second card for one submission would misreport how much
    is queued), and `None` for a queue job this principal may not see
    (`models.queue.visibility.may_see_job_id`, IA-1) -- a stale/foreign
    id leaves a normal page behind, the same as any other one.
    """
    raw = request.GET.get("queued", "").strip()
    if not raw.isdecimal():
        return None
    queue_job_id = int(raw)
    principal = principal_for_request(request)
    if visible_jobs(principal).filter(queue_job_id=queue_job_id).exists():
        return None
    if not may_see_job_id(principal, queue_job_id):
        return None
    try:
        job_status = get_job(queue_job_id)
    except QueueUnavailable:
        return None
    if job_status is None:
        return None
    return _queue_card_context(queue_job_id, job_status, principal)


def _recent_jobs(principal) -> list[GenerationJob]:
    """The cards the create page shows, in one place.

    Both the GET path and the re-render-around-an-invalid-form path need
    the same list with the same prefetches; a job's inputs are rendered on
    its card, so they are prefetched here rather than fetched per card.

    `visible_jobs(principal)`, not the bare manager (IA-1): a generated
    image is content, and the create page's Recent list must narrow
    exactly like the gallery does.
    """
    return list(
        visible_jobs(principal).prefetch_related("outputs", "inputs")[:RECENT_JOBS]
    )


def _strip_fingerprint(jobs: list[GenerationJob]) -> str:
    """A cheap, deterministic fingerprint of one Recent-list answer (fix
    round 2026-09-16, review finding 1).

    The discovery poll (create.html) compares THIS -- never rendered HTML
    text -- to decide whether anything changed. A body-text compare was
    the original shape here, and it was wrong: `_jobs_strip.html` renders
    with different incidental whitespace depending on whether it is
    INCLUDED inline (create.html) or rendered STANDALONE (`jobs_strip`),
    so a text compare "changed" on the very first tick even when nothing
    had -- forcing an unconditional swap that detached the cards `watch()`
    had just attached at load and left their orphaned poll loops running
    against nodes no longer in the document.

    `id`, `status`, and the number of outputs are the only facts that can
    change a card's rendered SHAPE in a way this poll needs to notice --
    a prompt or a param never does. `len(job.outputs.all())`, not
    `.count()`: `_recent_jobs` already prefetches `outputs`, so the
    `list` this function is always called with has that queryset cached,
    and `.count()` would issue a fresh one per job instead of reading it.
    """
    return "|".join(f"{job.id}:{job.status}:{len(job.outputs.all())}" for job in jobs)


def jobs_strip(request):
    """GET /vision/jobs/strip/ -- the create page's Recent region, as a
    standalone fragment (T1, 2026-09-16).

    Discovery target for create.html's own inline script: every OTHER
    poll on that page only refreshes a card already in the DOM (each
    non-terminal card's own `data-job-poll`, and the queued placeholder's
    `vision-queue-status`) -- nothing polled for a NEW job appearing at
    all, so a generation queued by a chat turn or another tab never
    showed up on an already-open create page until a manual reload. The
    page's script hits this on a fixed cadence and compares the fingerprint
    below to decide whether to swap; this view only ever renders the
    current truth and has no opinion about change detection.

    Renders the SAME cards the create page's own Recent region shows,
    from the SAME builder (`_recent_jobs`) through the SAME include
    (`vision/_jobs_strip.html`, factored out of create.html for exactly
    this -- one loop, two readers, so the page and this fragment can
    never drift on who counts as "recent"; IA-1's `visible_jobs(
    principal)` narrows both alike).

    `selected_connection`/`input_targets` are resolved exactly like
    `_render_card` already resolves them for a single card's own poll:
    `picked_connection` reads the same `?connection=` query key every
    other link and poll on this page carries, so a card discovered here
    polls under, and its "Use in ..." links carry, the SAME pick the
    page was rendered under rather than reverting to the role binding.

    `X-Strip-Fingerprint` (fix round 2026-09-16, review finding 1): the
    SAME `_strip_fingerprint(jobs)` value `_create_page_context` stamps
    onto the create page's own `#jobs-strip` wrapper (`data-strip-fp`),
    so the discovery poll can compare header-to-attribute rather than
    fetched text to rendered DOM -- two renders of one template that are
    never byte-identical to begin with (inline include vs. standalone).
    """
    principal = principal_for_request(request)
    picked, raw_connection, usable = picked_connection(request)
    selected = raw_connection if usable else ""
    check = services.preflight(picked)
    jobs = _recent_jobs(principal)
    response = render(
        request,
        "vision/_jobs_strip.html",
        {
            "jobs": jobs,
            "input_targets": input_targets(check.resolved),
            "selected_connection": selected,
        },
    )
    response["X-Strip-Fingerprint"] = _strip_fingerprint(jobs)
    return response


def _reuse_job(request) -> GenerationJob | None:
    """The job named by `?reuse=<uuid>`, or `None` for a missing, malformed,
    unknown, or INVISIBLE id -- a stale or foreign link should land the
    operator on a normal empty form, never on an error page and never on
    somebody else's parameters.
    """
    raw = request.GET.get("reuse", "").strip()
    if not raw:
        return None
    principal = principal_for_request(request)
    try:
        return visible_jobs(principal).filter(pk=raw).first()
    except (ValueError, ValidationError):
        return None


def _reuse_initial(job: GenerationJob | None) -> dict:
    """Form initial values from a previous job.

    Only keys the operation still declares survive (`build_form` filters
    unknown ones), so a removed parameter in an older job can't poison the
    form.
    """
    return dict(job.params) if job else {}


def _carried_get_initial(request, union) -> dict:
    """Direct `<key>=<value>` GET params as the LOWEST-precedence source of
    `initial` for the constant form -- a DIFFERENT mechanism from `?reuse=
    <job-id>` (`_reuse_initial`, above): that one names a job whose STORED
    params are read back from the database, never a per-field query key.

    This is what makes the Model/Mode picker's own carry-forward work
    (owner: "when i click on buttons it refreshes the page and clears
    current inputs"): create.html's inline script mirrors the operator's
    typed values into hidden fields on the picker form before it submits,
    so the GET reload this produces comes back with those same `key=value`
    pairs in the query string, and this is what turns them back into
    `initial` on the way in.

    Only a key `union` -- every registered operation's params,
    de-duplicated -- actually declares survives; an unrelated query key
    (`?bogus=1`, or `reuse`/`operation`/`connection`/an `input_*` stored
    reference, none of which name a param) is silently ignored. A FILE
    param's key is excluded outright: a query string cannot carry a
    file's bytes, and `forms._finish_form` already refuses to prefill a
    file field's `initial` for exactly that reason -- excluded here too
    so this path never depends on that guard to stay correct. A
    MULTIPLE-valued param (`param.multiple` -- the LoRAs chip group) is
    excluded too, matching the carry-forward script's OWN refusal
    (create.html) to carry one: a `?loras=x` in the query string is one
    value from a field whose real answer is a SET, and a single GET
    param cannot tell "this one LoRA" apart from "these are the only
    LoRAs" -- silently prefilling one checked box would misrepresent a
    selection this mechanism never actually saw.

    NEVER trusted past this: a value from here only ever becomes
    `field.initial` on the UNBOUND constant form the page renders
    (`forms.build_constant_form` validates nothing -- see its own
    docstring). The submission that actually runs validates against
    `forms.build_form`, live options and all, exactly as every other
    submission always has.
    """
    initial = {}
    for param in union:
        if param.kind == "file" or param.multiple:
            continue
        raw = request.GET.get(param.key)
        if raw is not None:
            initial[param.key] = raw
    return initial


def _page_form(
    operation: Operation, check, *, data=None, files=None, initial=None, stored_keys=frozenset()
):
    """The page's ONE constant form (`forms.build_constant_form`): the
    union of every registered operation's params, live-optioned and
    live-ignored for `check.resolved`.

    `CreatePageView.get_context_data` (a GET) and `_create_page_response`
    (a re-render around an invalid, refused, or not-ready POST) both need
    this same assembly -- one builder, so the two can never drift on
    which `services.union_params`/`live_options`/`live_ignored` calls
    build it. Each caller still resolves its own `initial`/`stored_keys`
    first (a GET's `initial` carries the engine's opening values and any
    reuse; a re-render passes none) and its own `data`/`files` (a GET
    passes neither; a re-render binds both to the same POST so typed
    values AND attached uploads come back -- see `forms.build_constant_
    form`'s own docstring for why `files` matters there).
    """
    from tools.vision.forms import build_constant_form

    return build_constant_form(
        # The REGISTRY, not a narrowed list: the field list is constant
        # (ADR 0012 D-EDIT-13), so a narrowing model changes which fields
        # are ENABLED and never which exist. The union is computed HERE
        # and handed over -- `forms` owns no service-layer import
        # (resolution 3).
        services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY)),
        operation,
        services.live_options(operation, check.resolved),
        services.live_ignored(operation, check.resolved),
        initial=initial,
        stored_keys=stored_keys,
        data=data,
        files=files,
    )


def _create_page_context(request, operation: Operation, check, form) -> dict:
    """The create page's own 13 context keys, from the four facts every
    render of it needs: the request (for the picker/stored-input query
    string), the resolved `operation`, the `preflight` result, and the
    bound `form`.

    Final-review finding 8: `CreatePageView.get_context_data` (a GET) and
    `_create_page_response` (a re-render around an invalid or refused
    submission) built this same 13-key dict independently -- one builder,
    so the two render paths for `vision/create.html` can never drift on
    what it needs. Each caller still resolves its OWN `operation`/`check`/
    `form` first (a GET's operation may come from `?operation=` or a
    reused job; a re-render's form is already bound and invalid) -- this
    is only the shared assembly once those four are in hand.
    """
    refs = stored_input_refs(request, operation)
    principal = principal_for_request(request)
    # A GET's or a re-render's unusable pk falls back to the role binding
    # in silence: a stale link should leave a normal page behind, never
    # an error.
    _picked, raw_connection, usable = picked_connection(request)
    selected = raw_connection if usable else ""
    # THREE readings of one answer, from one call. `operation_states`
    # already asks `operations_for_model` for the whole registry, so the
    # narrowed list and the supports-nothing fact are readings of that
    # same answer rather than a second identical question. (`get_context_
    # data` above still asks once of its own, to resolve WHICH operation
    # the request names before this runs -- that one is a different
    # question at a different moment, and collapsing the two is not this
    # task's business.)
    states = services.operation_states(check.resolved)
    available = [state.operation for state in states if state.supported]
    # Computed once, read twice below (`jobs`/`jobs_strip_fp`) -- the
    # fingerprint is derived FROM this exact list, never a second
    # `_recent_jobs(principal)` call that could theoretically answer
    # differently between the two reads.
    jobs = _recent_jobs(principal)
    return {
        "operation": operation,
        "operations": available,
        # A REAL fact about the selected model's engine, not "no opinion" --
        # `operation_states`/`operations_for_model` already tell the two
        # apart. Owner ruling 2026-08-24(c): this is what makes
        # `resolve_page_operation`'s silent supports-nothing fallback
        # honest to the operator instead of a page that looks normal and
        # then fails at the engine.
        "operations_supported": bool(available),
        # Every REGISTERED operation, each marked supported or not for the
        # selected model, with the reason (ADR 0012 D-EDIT-13). The
        # Operation select renders this; `operations` above is the same
        # answer narrowed to what this model can actually run, which is
        # what `input_targets` and the honest banner read.
        "operation_states": states,
        "preflight": check,
        "form": form,
        "connection_options": _connection_picker_options(principal, selected),
        "selected_connection": selected,
        "stored_inputs": _stored_input_context(refs, operation, principal, selected),
        "jobs": jobs,
        # The fingerprint `create.html` stamps onto `#jobs-strip` as
        # `data-strip-fp` (fix round 2026-09-16, review finding 1): the
        # discovery poll's own starting point, so its FIRST comparison is
        # against a value this SAME render produced -- never against a
        # rendered-DOM-vs-fetched-text guess that would "change" on every
        # tick regardless of whether anything actually did.
        "jobs_strip_fp": _strip_fingerprint(jobs),
        "input_targets": input_targets(check.resolved),
        "queued_card": _queued_placeholder(request),
        "may_generate": _may_generate(principal),
        # THE SAME SENTENCE the POST refusal answers with, threaded
        # through so `create.html`'s own `{% else %}` never restates a
        # second spelling of it (finding 6, T13 review).
        "generate_forbidden_message": _GENERATE_FORBIDDEN_MESSAGE,
    }


class CreatePageView(TemplateView):
    """GET /vision/ -- the prompt form, the preflight banner, recent jobs."""

    template_name = "vision/create.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The path alias first (`op/<key>/`), then the canonical query
        # parameter the Operation select produces -- a GET form can only
        # ever emit a query key, never a path segment (spec section 4).
        operation_key = kwargs.get("operation_key") or self.request.GET.get("operation")
        reuse_job = _reuse_job(self.request)

        picked, _raw_connection, _usable = picked_connection(self.request)
        check = services.preflight(picked)
        available = page_operations(check.resolved)

        if not operation_key and reuse_job is not None and get_operation(reuse_job.operation):
            # The URL names no operation of its own -- honor the reused
            # job's operation instead of silently falling back to the
            # default. A URL-named operation always wins over this; an
            # unregistered `job.operation` (feature since switched off,
            # operation renamed) also falls back to the default, honestly.
            operation_key = reuse_job.operation
        operation = resolve_page_operation(operation_key, available)

        form = _page_form(
            operation,
            check,
            initial={
                # LOWEST precedence first, each later source overriding it
                # on a shared key: a carried GET value is what the operator
                # just typed before switching, a live default is the
                # picked model's own opening value, and a reused job's own
                # params (ADR 0012 D-EDIT-7) still win over both.
                **_carried_get_initial(
                    self.request, services.union_params(operations_for(IMAGE_GENERATION_CAPABILITY))
                ),
                **services.live_defaults(operation, check.resolved),
                **_reuse_initial(reuse_job),
            },
            stored_keys=frozenset(
                item["param_key"]
                for item in _stored_input_context(
                    stored_input_refs(self.request, operation), operation,
                    principal_for_request(self.request),
                )
            ),
        )
        context.update(_create_page_context(self.request, operation, check, form))
        return context


def input_targets(resolved: ResolvedModel | None = None) -> list[dict]:
    """Every mode an existing image can be fed into, for the SELECTED
    model (`resolved`) -- or the full registry with none selected, exactly
    as before. Each target pre-fills the operation's FIRST file param
    (`Operation.file_params()`): inpaint's image, leaving its mask for the
    operator to choose.

    Narrowed to SUPPORTED operations on purpose, unlike the Operation
    select beside it: the select shows an unsupported mode disabled
    because the operator is standing there choosing, while a "Use in ..."
    link that lands on a mode this model cannot run is simply a dead end.

    `img2img` and `edit` are two entries again, under their own schema
    labels (ADR 0012 D-EDIT-13 retires the merged one) -- a resolved model
    narrows to at most one of them anyway (D-EDIT-5), and the page that
    can genuinely carry both (nothing selected, nothing bound) should say
    so rather than pick one silently.
    """
    targets = []
    for operation in page_operations(resolved):
        params = operation.file_params()
        if not params:
            continue
        targets.append(
            {
                "label": operation.label,
                "param_key": params[0].key,
                "url": _create_url(operation),
            }
        )
    return targets


def stored_input_refs(request, operation: Operation) -> dict[str, str]:
    """`{param key: reference}` for every file param this request answers
    with an image already in the store.

    Read from the query string on a GET (the gallery's link) and from the
    POST on a submission (the hidden field the page carries back), so the
    two halves of the flow agree without the page inventing a session.
    """
    source = request.POST if request.method == "POST" else request.GET
    refs = {}
    for param in operation.file_params():
        raw = (source.get(f"input_{param.key}") or "").strip()
        if raw:
            refs[param.key] = raw
    return refs


def _remove_stored_input_url(
    operation: Operation, connection: str, refs: dict[str, str], drop_key: str
) -> str:
    """The href for one stored-input block's "Remove" link (owner: "when I
    select use image, there is no way to undo it unless I change the
    url") -- the create page's own URL (`_create_url`, the same helper
    every other link on this page builds `?operation=`/`&connection=`
    from) with `input_<drop_key>` gone and every OTHER live `input_*`
    reference kept.

    Built from `operation`/`connection`/`refs` -- what the page already
    knows it is showing -- rather than copied off `request.GET`: a POST
    re-render (`_create_page_response`) has no query string of its own
    (the browser's address bar still shows the GET that served the page,
    since a form POST is not a navigation), so reconstructing gives the
    same answer on both render paths that call `_stored_input_context`
    instead of a broken link on one of them.

    JS-off is the only path this link is FOR (create.html's script
    removes the block in place without a reload); clicking it re-renders
    the page with one fewer `input_*` param, which is the fallback the
    brief calls for, not a bug.
    """
    url = _create_url(operation, connection)
    for key, reference in refs.items():
        if key == drop_key:
            continue
        url += f"&input_{key}={quote(reference, safe='')}"
    return url


def _stored_input_context(
    refs: dict[str, str], operation: Operation, principal, connection: str = ""
) -> list[dict]:
    """What the create page needs to SHOW a carried reference: the param's
    own label, the reference to post back, a URL to preview it, and a URL
    to remove it (`remove_url`, Cancel-a-stored-hand-off).

    A reference that no longer resolves (its job was deleted between the
    gallery link and this render), OR one `principal` may not see, is
    dropped silently -- the operator gets a normal empty file field, which
    is exactly what a stale OR foreign link should leave behind (IA-1
    security fix: this used to render a preview for ANY live reference,
    which was an existence oracle over another principal's images).

    `connection` defaults to `""` (no model picked) -- the one caller that
    only wants the `param_key`s (`CreatePageView.get_context_data`,
    building `stored_keys`) never renders `remove_url` and need not
    resolve it first.
    """
    shown = []
    for param in operation.file_params():
        reference = refs.get(param.key)
        if not reference:
            continue
        try:
            kind, pk = services.parse_input_reference(reference)
        except ValueError:
            logger.debug("Ignoring malformed stored input %r", reference, exc_info=True)
            continue
        # THIS PAGE SHOWS VISION'S OWN TWO KINDS (chat image artifacts,
        # 2026-09-16). `url_name` below has exactly two branches, and the
        # create page is not the door an attached document comes in
        # through -- a chat turn is. Dropped silently, the same way a
        # stale or foreign reference already is (the operator gets a
        # normal empty file field), rather than given a third preview
        # branch nothing asks for.
        if kind not in ("output", "input"):
            logger.debug("Ignoring non-vision stored input kind %r", kind)
            continue
        if not services.stored_input_exists(reference, principal):
            logger.debug("Ignoring stored input %r that no longer resolves", reference)
            continue
        url_name = "vision-output-file" if kind == "output" else "vision-input-file"
        shown.append(
            {
                "param_key": param.key,
                "label": param.label,
                "reference": reference,
                "url": reverse(url_name, args=[pk]),
                "remove_url": _remove_stored_input_url(operation, connection, refs, param.key),
            }
        )
    return shown


def gallery(request):
    """GET /vision/gallery/ -- every generated output, newest first.

    The gallery carries no picker of its own (`_output_actions.html` never
    sees a `selected_connection` here, matching every other bare caller),
    but its "Use in ..." links still must not lie about what the currently
    BOUND model can run (B2, final review): `input_targets` is narrowed by
    `services.preflight(None).resolved` -- the role binding, the same fact
    the create page's own default (nothing picked) view narrows by -- so a
    role bound to a flux2 connection never offers an img2img link the
    engine would refuse.

    IA-1: narrowed to `visible_jobs(principal)` -- a generated image is
    content, and this is the same manager the create page's Recent list
    reads (`_recent_jobs`), so the two listings can never disagree about
    who may see what.

    Select mode (owner ask, 2026-09-02) is a plain GET state, not a
    second view: `?select=1` renders a checkbox per figure inside ONE
    bulk-delete form and drops the per-figure action row; `?select=all`
    is the same page with every box on it pre-checked (`select_all`) --
    still per-page, so it never implies "every page". Zero JS either
    way; `page=` composes with both.

    `?job=<uuid>` (2026-09-03 fix batch, item B) narrows the listing to
    one `GenerationJob`'s outputs -- the href contract the chat column's
    cards jump here with: `{% url 'vision-gallery' %}?job=<uuid>
    #job-<uuid>`. Resolved through `visible_jobs(principal)`, the same
    IA-1 manager every other lookup here uses, so a foreign job is
    simply invisible, never a 404 that would confirm it exists.
    `.first()` forces the lookup eagerly (rather than composing a lazy
    `job__in=` subquery) so a MALFORMED uuid string's `ValueError` (from
    Django's own UUID adapter, raised on evaluation) is caught right
    here, the identical pattern `_generation_from_result` above already
    uses -- and both a malformed and an unknown/foreign id land on the
    exact same branch below: the ordinary empty-gallery state, 200, no
    oracle distinguishing one from the other.
    """
    principal = principal_for_request(request)
    outputs = (
        GeneratedOutput.objects.filter(job__in=visible_jobs(principal))
        .select_related("job").order_by("-job__created_at", "index")
    )
    job_filter = request.GET.get("job", "")
    if job_filter:
        try:
            target_job = visible_jobs(principal).filter(pk=job_filter).first()
        except (ValueError, ValidationError):
            target_job = None
        outputs = outputs.filter(job=target_job) if target_job else outputs.none()
    page = Paginator(outputs, GALLERY_PAGE_SIZE).get_page(request.GET.get("page"))
    resolved = services.preflight(None).resolved
    select_param = request.GET.get("select")
    select_mode = select_param in ("1", "all")
    return render(
        request,
        "vision/gallery.html",
        {
            "page_obj": page,
            "input_targets": input_targets(resolved),
            "select_mode": select_mode,
            "select_all": select_param == "all",
            "job_filter": job_filter,
        },
    )


def _is_xhr(request) -> bool:
    """True for the page's own fetch() calls. Everything works without JS;
    this only decides whether to answer with a fragment or a redirect."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


_GENERATE_FORBIDDEN_MESSAGE = (
    "Image generation needs an entitlement this account does not hold. "
    "Ask an administrator to grant it."
)

# THE MODEL HALF of the AND-composition (IA-2 T14, spec section 9.5):
# `_may_generate` answers the TOOL half (may this principal call
# `vision.generate` at all); a picked connection this principal may not
# USE, because it is in a set an entitlement narrows and this account
# holds none reaching it, is refused with the SAME 403 shape and a
# different, honest sentence -- not the 503 a picked model that is
# actually gone gets. The sentence itself is `models.registry.bindings.
# MODEL_FORBIDDEN_MESSAGE` (whole-branch review item 3): this file used
# to carry its own private copy, byte-identical to `tools/rag/views.py`'s
# and a differently-named one in `agents/runtime/preflight.py`.


def _may_generate(principal) -> bool:
    """Whether `principal` may run an image generation AT ALL.

    ONE PREDICATE, TWO CALLERS -- `generate` (the POST) and
    `_create_page_context` (the render) -- so the page cannot offer a form
    whose submission answers 403.

    `agents.entitlements.tool_access_for` is a NAMED CROSS-COLUMN SEAM,
    the same standing `models.registry.bindings` and
    `models.queue.visibility` already have: this column registers
    `vision.generate` as a tool through `agents.contracts.tools`
    (`tools/vision/apps.py`), so "may this principal call it" is a
    question the agents column owns the answer to, and asking it twice in
    two shapes is how two surfaces come to disagree.
    """
    from agents.entitlements import tool_access_for
    from tools.vision.tools import VISION_GENERATE_TOOL_KEY

    return tool_access_for(principal).allows(VISION_GENERATE_TOOL_KEY)


def _render_card(request, job: GenerationJob) -> HttpResponse:
    """The one place `_job_card.html` is ever rendered -- both inline (the
    create page's Recent list) and standalone (`job_status`,
    `queue_job_status`, i.e. every poll).

    B2 (final review): this used to build the card's context with a bare
    `input_targets()` -- the full registry in registration order, which
    resolves "Use in Image to image" to `img2img` even for a model that
    only supports `edit` -- and no `selected_connection` at all, silently
    dropping the `&connection=` fix d270d48 gave the create page's own
    render the moment a card was refreshed by a poll. `picked_connection`
    reads the SAME `?connection=` query key `_output_actions.html`'s own
    links (and the create page's chooser) already carry, so a poll request
    that names the pick it was rendered under gets the same narrowing and
    the same carried pick a fresh page load would; a poll that names
    nothing falls back to `preflight(None)`
    -- the role binding -- exactly like `gallery()`, which is still an
    improvement over the old bare-registry default.
    """
    picked, raw_connection, usable = picked_connection(request)
    selected = raw_connection if usable else ""
    check = services.preflight(picked)
    return render(
        request,
        "vision/_job_card.html",
        {
            "job": job,
            "input_targets": input_targets(check.resolved),
            "selected_connection": selected,
        },
    )


def _queue_status(queue_job_id: int):
    """The queue's own read of a job, or `None` when the queue cannot be
    read at all. A card that cannot name its job is a smaller failure than
    a 503 for a submission the queue already accepted."""
    try:
        return get_job(queue_job_id)
    except QueueUnavailable:
        return None


@require_POST
def generate(request):
    """POST /vision/generate/ -- validate, then QUEUE one generation.

    The page enqueues `vision.generate` (`models.contracts.queue.enqueue`)
    rather than calling `services.submit_job` itself, exactly as
    `tools.rag.views.AskView` enqueues `rag.ask`. One submission
    path for the page, an agent, and a management command means the
    scheduler's memory admission sees every generation there is -- while
    the page submitted directly, it could admit a language model into VRAM
    with a generation already running -- and it means a tool cannot get
    behaviour the page does not have.

    What still happens HERE, before anything is queued:

    - the FORM validates (the live layer: real option lists, widget
      bounds) and `validate_params` validates (the schema floor), so an
      invalid submission is a 400 with per-field messages immediately and
      nothing is queued. The worker validates again inside `submit_job`;
      that is not waste but the rule every queued job follows -- a job
      never trusts an enqueue-time decision as its run-time truth.
    - `preflight()` runs, so an unbound role or an unreachable engine is
      the same honest 503 banner it has always been, and `plan_generate`
      is called (at enqueue time) by a caller that has already checked.
    - every file becomes a REFERENCE: an upload is staged into the managed
      store (`services.stage_upload`) and a carried "use this image"
      reference is resolved once to prove it is live. A queue payload is
      JSON; nothing but strings go into it.

    Answers with the QUEUED placeholder card (202, XHR) or a redirect
    carrying `?queued=<id>` (no JS) -- either way the operator sees a card
    in Recent immediately, and it becomes the real job card the moment the
    worker creates the generation row.
    """
    principal = principal_for_request(request)
    if not _may_generate(principal):
        # THE FIRST THING, before a model is picked, a form is built, or a
        # file is staged: a refusal that had already written bytes into the
        # managed store would leave the box holding an upload for a job
        # that never existed.
        #
        # 403, not 503: this is not an unavailable service, it is an
        # action this account may not take. Honest copy, and the same
        # in-view gate shape `tools/rag/views.py::document_delete` uses.
        message = _GENERATE_FORBIDDEN_MESSAGE
        if _is_xhr(request):
            return render(request, "vision/_forbidden.html",
                          {"message": message}, status=403)
        return HttpResponseForbidden(message)

    picked, raw_connection, usable = picked_connection(request)
    if not usable:
        # TWO CAUSES, TWO STATUSES -- same split `agents/runtime/
        # preflight.py` makes for the chat path. A connection registered
        # but not usable by THIS principal (`ModelAccess`) is a 403 (an
        # action this account may not take); one that is genuinely gone
        # (deleted, or never matched the capability) keeps the 503.
        # Asked of the ACCESS VALUE, never by matching on the caught
        # exception's message.
        if raw_connection.isdecimal() and not model_access_for(principal).allows(
                int(raw_connection)):
            message = MODEL_FORBIDDEN_MESSAGE
            if _is_xhr(request):
                return render(request, "vision/_forbidden.html",
                              {"message": message}, status=403)
            return HttpResponseForbidden(message)
        # The operator explicitly chose a model that is gone. Running a
        # different one would be worse than saying no, so nothing is
        # queued and nothing is staged.
        return _picker_failure_response(request)

    check = services.preflight(picked)
    operation = resolve_page_operation(
        request.POST.get("operation"), page_operations(check.resolved)
    )
    refs = stored_input_refs(request, operation)
    # ONE read of this model's ignored-param map per submission: the form
    # needs it to relax `required`, the fill below needs it to know what
    # to supply, and asking the engine adapter twice for the same answer
    # is waste the page can see (`live_ignored` is called per operation).
    ignored = services.live_ignored(operation, check.resolved)
    form = build_form_for(
        request, operation, check, stored_keys=frozenset(refs), ignored=ignored
    )

    if not form.is_valid():
        return _invalid_form_response(request, operation, check, form)

    if not check.ready:
        if _is_xhr(request):
            return render(
                request, "vision/_unavailable.html", {"message": check.message}, status=503
            )
        return _create_page_response(request, operation, check, form, status=503)

    # An attached file always wins over a carried reference: the operator
    # picking a new file is them changing their mind, and the form still
    # rendered the field for exactly that. This is the only layer that can
    # see both candidates, so the rule lives here and the resolver never
    # sees a conflict.
    #
    # An ignored file key is filtered out HERE too, not only blanked out
    # of `merged`/`params` below: `ignored` is a param this model's graph
    # cannot honour (the widget rendered disabled), and a value the graph
    # never reads has no business being staged or named in `inputs`
    # either (ADR 0012 D-EDIT-13) -- the same rule that governs every
    # other ignored param, applied to files. Filtering the SOURCE dicts
    # means the staging loop further down never sees them at all.
    file_keys = operation.file_param_keys()
    uploads = {
        key: value for key, value in request.FILES.items()
        if key in file_keys and key not in ignored
    }
    carried = {key: ref for key, ref in refs.items() if key not in uploads and key not in ignored}

    try:
        files = dict(uploads)
        # Resolved to prove the reference is live before anything is
        # queued -- a `StoredFile` is a lazy handle, so this costs a row
        # read and no file read. A dead reference must fail on the page,
        # where the operator can fix it, not on a worker minutes later.
        files.update(services.resolve_inputs(operation, carried, principal))
        # `build_form_for` only relaxes `required` for an ignored key; the
        # FIELD -- and a hand-crafted POST that re-enables a disabled
        # control in devtools -- can still hand back a value here. The
        # page rendered that control disabled, so the operator was never
        # allowed to answer it, and a value the graph never read has no
        # business in the job record (ADR 0012 D-EDIT-13). Re-derive
        # rather than trust the submission: blank every ignored key
        # before the fill below decides what belongs there.
        merged = {**form.cleaned_data, **files}
        for key in ignored:
            if key in file_keys:
                # `uploads`/`carried` above already exclude every ignored
                # file key, so `files` never carries one for a well-behaved
                # submission -- but `form.cleaned_data` is Django's OWN
                # read of `request.FILES`/POST, independent of that
                # filtering, and a hand-crafted POST that re-enables a
                # disabled control in devtools can still put a live
                # `UploadedFile` there. This pop is the second, cheaper
                # line of defense against exactly that: `merged[key]`
                # would be that object, never text, and stamping the
                # string `""` over it would hand `fill_engine_blanks`/
                # `validate_params` a value that LOOKS like an answer (a
                # param present with a blank string) instead of an
                # absence. Drop the key so it reads as unanswered:
                # `raw.get(key)` returns `None`, the same "blank"
                # `validate_params` already gives a missing key, without
                # lying about the value's shape in between.
                merged.pop(key, None)
            elif key in merged:
                merged[key] = ""
        # Back-fill what the page's own DISABLED widgets never submitted
        # (ADR 0012 D-EDIT-13) BEFORE the schema floor sees the dict --
        # `validate_params` refuses a blank `"choice"` regardless of
        # `required`, and an ignored field is one the operator was never
        # allowed to answer.
        params = validate_params(
            operation,
            services.fill_engine_blanks(
                operation, merged, check.resolved,
                keys=set(ignored),
            ),
        )
    except services.InputReferenceError as exc:
        form.add_error(exc.param_key if exc.param_key in form.fields else None, str(exc))
        return _invalid_form_response(request, operation, check, form)
    except ParamError as exc:
        for key, message in exc.errors.items():
            form.add_error(key if key in form.fields else None, message)
        return _invalid_form_response(request, operation, check, form)

    # A file is named ONCE, in `inputs` -- `submit_job` merges the resolved
    # files back into the params it validates on the worker and derives the
    # basename label itself, so a file key in `params` here would be a
    # second, staler name for the same thing.
    payload_params = {key: value for key, value in params.items() if key not in file_keys}

    services.prune_staged_inputs()
    staged: list[str] = []
    inputs = dict(carried)
    for key, uploaded in uploads.items():
        reference = services.stage_upload(key, uploaded)
        staged.append(reference)
        inputs[key] = reference

    payload = {
        "operation": operation.key, "params": payload_params, "inputs": inputs,
        # WHO ASKED -- the acting principal, stamped into every job
        # payload (the acting rule).
        **payload_fields(principal),
    }
    if raw_connection:
        # The pk, as a string -- never a resolved model, never a name. The
        # worker re-resolves it fresh at claim time (`jobs._resolve_model`),
        # so an edited connection is honoured and a deleted one fails
        # loudly instead of silently running something else.
        payload["connection"] = raw_connection

    try:
        queue_job_id = enqueue(JOB_KIND, payload)
    except QueueUnavailable:
        services.discard_staged_inputs(staged)
        return _queue_failure_response(request, operation, check, form, _QUEUE_UNAVAILABLE_MESSAGE)
    except QueueQuotaExceeded as exc:
        # NOT A QUEUE FAILURE (C-7/H39). The queue is healthy and has
        # simply said this account already holds as many queued-or-running
        # jobs as the operator's cap allows -- 429, not the 503 its two
        # neighbours return. The banner is the exception's OWN sentence,
        # verbatim: it names the two counts and nothing else, and the one
        # place that knows what the cap is should not have that sentence
        # paraphrased here.
        #
        # THE STAGED INPUTS STAY, and that is the difference from every
        # other refusal on this path. The neighbours discard because
        # nothing will ever claim those files; this is the try-again-in-a-
        # minute case, and making an operator re-attach the images they
        # just uploaded would be the platform charging them for its own
        # queue depth. `services.prune_staged_inputs()` above sweeps them
        # on a later submission if the retry never comes.
        if _is_xhr(request):
            return render(request, "vision/_unavailable.html", {"message": str(exc)}, status=429)
        form.add_error(None, str(exc))
        return _create_page_response(request, operation, check, form, status=429)
    except Exception:  # noqa: BLE001 -- log detail, then degrade to a clean 503
        logger.exception("Failed to enqueue a %s job", JOB_KIND)
        services.discard_staged_inputs(staged)
        return _queue_failure_response(request, operation, check, form, _QUEUE_ENQUEUE_FAILED_MESSAGE)

    if _is_xhr(request):
        return render(
            request,
            "vision/_queued_card.html",
            {
                "card": _queue_card_context(
                    queue_job_id, _queue_status(queue_job_id), principal
                )
            },
            status=202,
        )
    return redirect(f"{_create_url(operation, raw_connection)}&queued={queue_job_id}")


def _create_url(operation: Operation, connection: str = "") -> str:
    """The create page's URL for `operation`, carrying the picked model.

    ONE shape for every generated link: `/vision/?operation=<key>`, plus
    `&connection=<pk>` when a model is picked. The pick and the mode both
    live in the query string, not a session, so a link is a complete
    description of what the page will show -- which is what makes the
    Operation select, the redirect after a submission, and a bookmark all
    agree.

    The query form is canonical because the Operation select is a control
    inside the picker's GET form, and a GET form can only ever produce a
    query parameter (ADR 0012 D-EDIT-13). `op/<key>/` stays routed to the
    same view and renders identically, so every previously issued link
    keeps working -- it is simply not what anything emits any more.
    """
    url = f"{reverse('vision-create')}?operation={operation.key}"
    return f"{url}&connection={connection}" if connection else url


def _queue_failure_response(request, operation: Operation, check, form, message: str):
    """The 503 for a submission that could not be queued -- the same two
    shapes an unavailable engine gets, so the page has one way of saying
    "not now": the small banner fragment for XHR, the whole page for a
    plain POST."""
    if _is_xhr(request):
        return render(request, "vision/_unavailable.html", {"message": message}, status=503)
    form.add_error(None, message)
    return _create_page_response(request, operation, check, form, status=503)


def _picker_failure_response(request):
    """The 503 for a submission whose picked model no longer resolves.

    This fires at the very top of `generate()`, BEFORE `operation`/`check`/
    `form` exist, so it cannot reuse `_queue_failure_response`'s "attach the
    error to the form already on hand" trick directly -- there is no form
    yet. It builds the same fallback the page itself would (no operator
    choice survives a picked model that is gone, so there is nothing worth
    re-rendering FROM) and reports the refusal the same way every other
    submission failure does: the small banner fragment for XHR, the whole
    page -- nav, chrome, and the message as a form-level error -- for a
    plain POST, never a bare unstyled fragment (the same rule
    `test_non_xhr_unbound_503_renders_the_whole_page_not_a_bare_fragment`
    already pins for the unbound-role case).
    """
    if _is_xhr(request):
        return render(
            request, "vision/_unavailable.html",
            {"message": _UNREGISTERED_CONNECTION_MESSAGE}, status=503,
        )
    check = services.preflight()
    operation = resolve_page_operation(
        request.POST.get("operation"), page_operations(check.resolved)
    )
    refs = stored_input_refs(request, operation)
    form = build_form_for(request, operation, check, stored_keys=frozenset(refs))
    # Bound, so `add_error` below has a `cleaned_data` to check against --
    # the same thing `generate()`'s own `form.is_valid()` a few lines up
    # its normal path already does before `_queue_failure_response` calls
    # this same method. Whether the rest of the submission would have
    # validated is irrelevant here; the refusal is about the model, not
    # the fields.
    form.is_valid()
    form.add_error(None, _UNREGISTERED_CONNECTION_MESSAGE)
    return _create_page_response(request, operation, check, form, status=503)


def picked_connection(request) -> tuple[ResolvedModel | None, str, bool]:
    """`(resolved, raw_pk, usable)` for the model this request picked.

    Read from the query string on a GET (the picker's own reload, and every
    chooser link) and from the POST on a submission, so the two halves of
    the flow agree without the page inventing a session -- exactly how
    `stored_input_refs` already reads a carried image reference.

    A blank or absent value is `(None, "", True)`: use the role binding,
    which is what every link, bookmark, and existing caller sends and is
    precisely today's behaviour. A pk that no longer resolves is
    `(None, raw, False)` -- the CALLER decides what that means. The PAGE's
    GET callers (`CreatePageView`, `_create_page_response`) treat it as
    "nothing picked": a stale link must leave a normal page behind, never
    an error. `generate`'s submission path and `vision_operations` both
    read `usable` as a refusal instead (`_picker_failure_response`,
    finding-3's 404) -- an operator or a tool that explicitly named a model
    that is gone gets told so, rather than a silent answer for a different
    one.

    `ProgrammingError`/`OperationalError` (tables not migrated yet, or the
    database unreachable) are tolerated the same way
    `models.registry.bindings._bound_connection` already tolerates them
    for the role-binding path -- not a new guard, the same one.
    """
    from django.db import OperationalError, ProgrammingError

    source = request.POST if request.method == "POST" else request.GET
    raw = (source.get("connection") or "").strip()
    if not raw:
        return None, "", True
    access = model_access_for(principal_for_request(request))
    try:
        return (resolve_connection(int(raw), IMAGE_GENERATION_CAPABILITY, access=access),
                raw, True)
    except (TypeError, ValueError, ProgrammingError, OperationalError):
        logger.debug("Ignoring unusable image connection %r", raw, exc_info=True)
        return None, raw, False


def vision_operations(request):
    """GET /vision/operations/ -- `services.operation_catalog()` as JSON
    (ADR 0012's payload-contract section).

    `?connection=<pk>` answers for THAT model rather than the role binding,
    the same pk the generation form posts -- so a tool reads the schema for
    the model it is about to use. An absent or blank pk answers for the
    role binding instead, exactly like a bare page load.

    `connection` in the response body names WHICH model actually answered
    -- `{"id": pk, "name": str}`, or `{"id": None, "name": None}` when
    nothing is bound and nothing was picked -- so a tool reading this
    schema knows what it describes, the same honesty D-EDIT-4 already
    gives a finished job's own record.

    Final-review finding 3: a pk that names no usable image-generation
    connection used to fall back to the role binding's catalog in
    silence -- a caller that named a model explicitly got an answer for a
    DIFFERENT one with nothing marking the substitution. It is now refused
    outright: 404, a JSON `{"error": str}` body, the same message
    `_picker_failure_response` gives an operator who submits with a picked
    model that is gone.
    """
    picked, raw, usable = picked_connection(request)
    if raw and not usable:
        return JsonResponse({"error": _UNREGISTERED_CONNECTION_MESSAGE}, status=404)

    if raw:
        try:
            access = model_access_for(principal_for_request(request))
            _resolved, name = resolve_connection_named(int(raw), IMAGE_GENERATION_CAPABILITY,
                                                        access=access)
        except ValueError:
            # Vanishingly rare: the connection was removed between
            # `picked_connection`'s own check, above, and this one --
            # `usable` already proved it was there for this request.
            name = None
        connection = {"id": int(raw), "name": name}
    else:
        name, pk = role_primary(VISION_GENERATE_ROLE)
        connection = {"id": pk, "name": name or None}

    return JsonResponse(
        {"connection": connection, "operations": services.operation_catalog(picked)}
    )


def job_status(request, job_id):
    """GET /vision/jobs/<uuid>/ -- refresh the job, then render its card.

    `?format=json` returns `services.job_json` instead: the same facts, for
    the future chatbot tool and for tests that shouldn't parse HTML.

    IA-1: looked up through `visible_jobs(principal)`, never the bare
    manager -- 404, not 403, for a job this principal may not read (a
    403 on a row-addressed URL confirms the row exists).
    """
    principal = principal_for_request(request)
    job = get_object_or_404(visible_jobs(principal), pk=job_id)
    job = services.refresh_job(job)
    if request.GET.get("format") == "json":
        return JsonResponse(services.job_json(job))
    return _render_card(request, job)


def _validated_next_url(request) -> str | None:
    """The `next` POST field, honoured only when it is a same-origin,
    same-scheme URL (`url_has_allowed_host_and_scheme`) -- shared by
    `job_delete` and `jobs_delete_selected` so a rogue value can never
    redirect the operator off-site from either delete route. `None`
    (not a fallback URL) when there is no usable `next`: each caller
    picks its own default, because the two routes don't share one.
    """
    next_url = request.POST.get("next", "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None


@require_POST
def job_delete(request, job_id):
    """POST /vision/jobs/<uuid>/delete/ -- remove the job and its files.

    An optional `next` field (the gallery's delete form sets it) sends a
    non-XHR delete back to wherever it was posted from instead of the
    default create page -- validated same-origin only
    (`_validated_next_url`), so a rogue value can't redirect the operator
    off-site; it silently falls back to the create page instead of
    erroring.

    IA-1: deleting is not on the operator's administer list either --
    same `visible_jobs(principal)` lookup as `job_status`, so a principal
    who cannot read a job cannot delete it, and both routes 404 alike.
    """
    principal = principal_for_request(request)
    job = get_object_or_404(visible_jobs(principal), pk=job_id)
    services.delete_job(job)
    if _is_xhr(request):
        return HttpResponse(status=204)
    next_url = _validated_next_url(request)
    return redirect(next_url) if next_url else redirect("vision-create")


@require_POST
def jobs_delete_selected(request):
    """POST /vision/jobs/delete-selected/ -- the gallery's select-mode
    bulk delete: every job checked in ONE submit, instead of the
    per-figure two-step control repeated by hand (owner ask,
    2026-09-02).

    `jobs` is `request.POST.getlist("jobs")` -- the gallery's select-mode
    grid names every figure's checkbox `jobs`, and two outputs of the
    same batch job render two checkboxes carrying the SAME job id, so
    this dedupes via a `set` before doing anything.

    Looked up through `visible_jobs(principal)`, same as `job_delete`
    (IA-1). Unlike `job_delete`, an id this principal may not see -- or
    one that is not even a valid UUID -- is silently DROPPED, not 404'd:
    a bulk action addresses many rows behind one submit, so there is no
    single "which one" a 404 could confirm or deny the existence of the
    way a row-addressed URL can; skipping just means it doesn't count
    toward the total the success message reports.

    A batch job's sibling outputs are deleted with it (`services.
    delete_job` deletes the whole `GenerationJob`, cascading its
    `GeneratedOutput` rows and its directory) -- exactly what selecting
    only one figure of a multi-image batch does today via the per-figure
    control, so bulk delete changes no existing single-job semantics.

    Empty selection is not an error: "Nothing selected." and back to
    where the form was, same as a no-op single delete would be if one
    existed.
    """
    principal = principal_for_request(request)
    next_url = _validated_next_url(request)
    raw_ids = {value for value in request.POST.getlist("jobs") if value}
    if not raw_ids:
        messages.info(request, "Nothing selected.")
        return redirect(next_url) if next_url else redirect("vision-gallery")
    valid_ids = []
    for raw in raw_ids:
        try:
            valid_ids.append(uuid.UUID(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    jobs = list(visible_jobs(principal).filter(pk__in=valid_ids))
    for job in jobs:
        services.delete_job(job)
    noun = "generation" if len(jobs) == 1 else "generations"
    messages.success(request, f"Deleted {len(jobs)} {noun}.")
    return redirect(next_url) if next_url else redirect("vision-gallery")


# THE CLOSED SET `_serve_stored_file` WILL NAME IN A `Content-Type`
# HEADER. `media_type.startswith("image/")` was not an allowlist:
# `image/svg+xml` is an `image/*` type a browser renders as a DOCUMENT,
# script elements and all, in the origin that fetched it -- stored XSS on
# any row whose media type came from the uploading client, which before
# `store.media_type_for_upload` was every row. Every raster type this
# platform can produce or accept is listed; anything else (SVG included,
# and any future type nobody has thought about yet) falls through to
# `application/octet-stream`, which downloads instead of rendering.
_SERVABLE_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "image/bmp", "image/tiff", "image/avif",
})


def _serve_stored_file(request, path: str, media_type: str, missing: str) -> FileResponse:
    """Stream one file out of the managed store.

    Shared by `output_file` and `input_file`: both serve strictly by
    primary key (the request never supplies a filesystem path, so there is
    no path-traversal surface), both honour `?download=1`, and both must
    give the same honest 404 when a job's directory has been deleted out
    from under the row. Written once so the two can never drift.

    Unauthenticated in this Phase-1 skeleton, the same known gap
    `/inference/` and `/rag/`'s file view carry (spec §9).
    """
    if not path or not os.path.isfile(path):
        raise Http404(missing)
    download = request.GET.get("download", "").lower() in ("1", "true")
    # `media_type` is whatever was recorded at store time -- clamp it to
    # `_SERVABLE_IMAGE_TYPES`, a closed allowlist, before it reaches a
    # browser's Content-Type header. `image/*` is not itself safe to
    # allow: `image/svg+xml` is a document type, script elements and all,
    # so a prefix test let a row that (however it got there) carries it
    # be served as markup instead of a download. Any type outside the
    # allowlist -- SVG included -- falls through to
    # `application/octet-stream`.
    base_type = (media_type or "").split(";", 1)[0].strip().lower()
    servable = base_type in _SERVABLE_IMAGE_TYPES
    safe_media_type = base_type if servable else "application/octet-stream"
    response = FileResponse(
        open(path, "rb"),
        # B-1: a type outside the allowlist is served as an ATTACHMENT
        # whatever `?download=` says. `application/octet-stream` already
        # makes every browser this platform targets download rather than
        # render, so this is the second lock on the same door -- cheap,
        # and it means the disposition header states the intent instead of
        # leaving it to the media type alone.
        as_attachment=download or not servable,
        filename=os.path.basename(path),
        content_type=safe_media_type,
    )
    # STEWARD ADDITION (a). The site-wide middleware sets this today, and
    # this route is the one place it is load-bearing -- a stored row whose
    # media type came from anywhere but `media_type_for_upload`. Setting it
    # here makes the guarantee local to the route that needs it, so a
    # middleware change cannot silently remove it from this one.
    response["X-Content-Type-Options"] = "nosniff"
    # B-8 (round-3 hardening): both routes this helper serves (a
    # generation's output, a job's stored input) are entitlement-gated
    # content -- marked `Cache-Control: private, no-store, max-age=0`
    # with `Cookie` added to `Vary`, so neither lingers in a shared
    # browser's disk cache or a LAN caching proxy after the session that
    # fetched it is gone.
    return mark_private(response)


def output_file(request, output_id: int):
    """GET /vision/outputs/<id>/file/ -- stream one generated file.

    IA-1: the owner is resolved through `output.job` (`may_read_job`) --
    one generation has one owner, and a per-output owner would be a
    second answer to the same question. 404, not 403, when it says no.
    """
    output = get_object_or_404(GeneratedOutput, pk=output_id)
    principal = principal_for_request(request)
    if not may_read_job(principal, output.job):
        raise Http404(f"Output {output_id} does not exist.")
    return _serve_stored_file(
        request,
        output.path,
        output.media_type,
        f"The file for output {output_id} is no longer on disk "
        "(the job's directory may have been deleted).",
    )


def input_file(request, input_id: int):
    """GET /vision/inputs/<id>/file/ -- stream one stored input.

    The counterpart to `output_file`: an operator looking at an img2img
    job needs to see the picture it started from, and a caller reading
    `?format=json` needs a URL for it. The bytes are the platform's own
    copy (spec §5), not the engine's.

    IA-1: same `may_read_job` check as `output_file`, through
    `job_input.job`, for an ordinary job input. A STAGED upload
    (`job_input.job_id is None`) has no job to resolve an owner through
    -- `tools.vision.models.JobInput` carries no owner column of its own,
    and stamping one is a fifth migration out of scope here -- so on a
    box with accounts (`accounts_on()`) it is served to `is_admin` only;
    a member gets the same 404 an invisible row gets everywhere else.
    On an OPEN box `is_admin` is always True, so nothing changes there --
    this is a real, named limitation (`tools/vision/README.md`), not a
    gap silently widened: "staged-upload preview is admin-only until
    IA-2 stamps owners on inputs."
    """
    job_input = get_object_or_404(JobInput, pk=input_id)
    principal = principal_for_request(request)
    if job_input.job_id is not None:
        if not may_read_job(principal, job_input.job):
            raise Http404(f"Input {input_id} does not exist.")
    # `is_admin(OPEN_PRINCIPAL)` is always True (its own open-branch-
    # first rule), so this changes nothing on a box with no accounts.
    elif not is_admin(principal):
        raise Http404(f"Input {input_id} does not exist.")
    return _serve_stored_file(
        request,
        job_input.path,
        job_input.media_type,
        f"The file for input {input_id} is no longer on disk "
        "(the job's directory may have been deleted).",
    )


def build_form_for(request, operation: Operation, check, stored_keys=frozenset(), ignored=None):
    """The bound form for a submission, with the engine's live options and
    any file param a stored image already answers.

    A param this model's graph cannot honour (`services.live_ignored`) is
    relaxed to optional here, and only here: the PAGE is what rendered
    that field disabled (ADR 0012 D-EDIT-13), so the browser sent nothing
    for it, and refusing the submission would blame the operator for a
    control they were never allowed to touch. `forms.build_form` itself
    stays untouched -- a caller with no page (the chatbot tool) never
    disabled anything and must keep the schema's own answer.
    `services.fill_engine_blanks` supplies the value before
    `validate_params`, which is still the floor.

    `ignored` is that same map, when the caller already has it: `generate`
    needs it twice in one POST (here, and for the fill), and reading it
    twice would ask the engine adapter the same question twice per
    submission. `None` means "work it out", which is what
    `_picker_failure_response` -- the one caller that has no use for it
    afterwards -- passes.
    """
    from tools.vision.forms import build_form

    form = build_form(
        operation,
        services.live_options(operation, check.resolved),
        data=request.POST,
        files=request.FILES or None,
        stored_keys=stored_keys,
    )
    if ignored is None:
        ignored = services.live_ignored(operation, check.resolved)
    for key in ignored:
        if key in form.fields:
            form.fields[key].required = False
    return form


def _create_page_response(request, operation: Operation, check, form, status: int):
    """Re-render the create page around an invalid or refused submission.

    `form` is the BOUND per-operation form that actually validated
    (`build_form_for`); the page renders the CONSTANT one (ADR 0012
    D-EDIT-13), so this builds that form bound to the same POST -- the
    operator's typing AND attached uploads come back (`files=request.
    FILES`, so a genuinely-uploaded `init_image`/`mask_image` does not
    render "This field is required." under itself) -- and copies the
    real form's errors across, attaching one to `None` when the union has
    no such field. The same trick `_picker_failure_response` already uses
    for its own non-field refusal, and `{{ f.errors }}` in the template
    needs no change.
    """
    refs = stored_input_refs(request, operation)
    page_form = _page_form(
        operation,
        check,
        stored_keys=frozenset(refs),
        data=request.POST,
        files=request.FILES or None,
    )
    # Bound, so `add_error` below has a `cleaned_data` to check against --
    # which also means this form has ALREADY produced its own message for
    # every required field the POST left empty. Copying the operation
    # form's identical message on top would show the operator "This field
    # is required." twice under one input, so only what the union did not
    # already say is copied across.
    page_form.is_valid()
    for key, field_messages in form.errors.items():
        target = key if key in page_form.fields else None
        existing = page_form.errors.get(target or "__all__", [])
        for message in field_messages:
            if message not in existing:
                page_form.add_error(target, message)
    return render(
        request,
        "vision/create.html",
        _create_page_context(request, operation, check, page_form),
        status=status,
    )


def _invalid_form_response(request, operation: Operation, check, form, status: int = 400):
    """The 400 for a form that failed validation (schema or live-choice).

    XHR gets just the errors (`_form_errors.html`) -- the caller's submit
    handler renders it into `#form-errors`, a slot the recent-jobs list
    never shares, so a rejected submission can never look like a new job. A
    plain POST gets the whole page back, form and all, exactly as before.
    """
    if _is_xhr(request):
        return render(request, "vision/_form_errors.html", {"form": form}, status=status)
    return _create_page_response(request, operation, check, form, status=status)
