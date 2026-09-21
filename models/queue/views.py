"""
The Queue page (T7): the operator's window into the execution queue
(ADR 0013). Server-rendered, ZERO JavaScript, offline-first -- one
`QueueView` (GET) plus two `@require_POST` FBVs (`queue_settings_update`,
`queue_job_cancel`), mirroring `tools/rag/views.py`'s `HistoryView`/
`library_settings_update` grammar exactly: plain forms, `django.contrib.
messages`, redirect-after-POST, never-500 validation. No polling, no
meta-refresh -- an explicit `<a href=".">Refresh</a>` link is the entire
"update this page" mechanism (see the template's own comment header).

`_human_size` (below) is imported from `models.registry.views` rather
than reimplemented: it is platform code already (not a `tools/*` app), so
`models.queue` importing `models.registry` crosses no column-privacy
boundary this codebase polices -- `models/queue/claim.py` already imports
`models.registry.bindings.footprint_for` and `models.registry.
discovery.norm_endpoint`/`norm_tag` for the exact same reason. `_human_size`
itself keeps its own `None`-for-falsy-footprint contract (a UI judgment
call, not a formatting one), so it still lives next to its one other
caller rather than being inlined here a second time.

T10 update: the byte-count-to-string LADDER underneath `_human_size` (and
this module's own `_bytes_to_gb_value`/GB<->bytes conversions) now lives in
`foundation/format.py` (`human_bytes`/`gb_to_bytes`/`bytes_to_gb`) -- the shared,
non-UI home this docstring used to say didn't exist yet. `_human_size`
delegates to it rather than carrying its own copy of the ladder; this
module imports `foundation.format` directly for its own GB<->bytes round trip
(the memory-budget form) rather than duplicating that arithmetic a second
time, the same reasoning that keeps `_human_size` itself a single import
rather than a second copy.
"""
from __future__ import annotations

import math
from datetime import timedelta

from django.contrib import messages
from django.db import OperationalError, ProgrammingError, transaction
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from identity import audit
from identity.contracts import actions
from identity.request import principal_for_request, settings_row_for
from models.registry.views import _human_size
from models.queue.backend import QueueRow, cancel_job, queue_snapshot, summarize_job
from models.queue.models import JobSettings
from foundation.format import bytes_to_gb, format_timecode, gb_to_bytes
from foundation.settings_area import settings_redirect
from foundation.settings_bounds import (
    BIGINT_FIELD_MAX, POSITIVE_INT_FIELD_MAX, exceeds_field_ceiling,
)
from models.contracts.jobkinds import get_job_kind, resolve_dotted_path
from models.contracts.queue import QueueUnavailable
from models.queue.visibility import may_read_job_content, may_see_job_id, visible_rows

# The worker-down hint's staleness threshold -- a module constant (not a
# `JobSettings` field: this is a UI judgment call about how long "nothing
# started" reads as suspicious, not an operator-tunable policy knob) --
# derived from row timestamps only, per the task's own instruction.
WORKER_DOWN_THRESHOLD_MINUTES = 10

# Both settings look like DB-unavailable in the exact same way `_guarded`
# (models/queue/backend.py) already translates for every *other* backend
# call -- `JobSettings.get_solo()` itself is a plain Django model method,
# never wrapped by that decorator, so this view catches the same two
# exception types directly around it rather than adding an unused
# indirection through backend.py for a single call site.
_DB_UNAVAILABLE = (OperationalError, ProgrammingError, QueueUnavailable)


def _bytes_to_gb_value(num_bytes: int | None) -> str:
    """`num_bytes` -> the string an operator's GB input field should show,
    one decimal place, or `""` for `None` (unset -- the field renders
    blank, matching the "blank = unset" grammar the form itself accepts).
    `foundation.format.bytes_to_gb` (T10) is the SAME base `models.registry.
    views._human_size` and `connection_add`'s `footprint_gb` round-trip
    already use, so a value an operator typed (e.g. 8.5) reads back
    identically -- this is the "budget GB<->bytes round-trip renders back
    identically" requirement, satisfied at exactly one arithmetic site."""
    if num_bytes is None:
        return ""
    return f"{bytes_to_gb(num_bytes):.1f}"


def _job_settings_row() -> JobSettings | None:
    """The one `JobSettings` row, or `None` on a box whose settings table
    is not reachable yet.

    ONE READ SITE FOR BOTH PAGES (F1, Coherence Wave C). The Queue page
    renders these four values read-only and the Job execution page
    edits them, so both need the identical never-500 degradation: an
    unmigrated or DB-down box falls back to `JobSettings`'s own
    documented defaults rather than 500ing a page whose whole purpose is
    to tell an operator what to fix. `get_solo()` only -- never
    `objects.first()` -- per `docs/EXTENDING.md`'s read idiom."""
    try:
        return JobSettings.get_solo()
    except _DB_UNAVAILABLE:
        return None


def _job_settings_context(settings_row: JobSettings | None) -> dict:
    """The five `JobSettings` values, in the shape both pages render.

    `settings_row is None` is the degraded box (`_job_settings_row`
    above): every value falls back to the model's own `*_DEFAULT`
    constants -- the same numbers `get_solo()` would have created --
    and `memory_budget_bytes` stays `None`, which is the SEQUENTIAL-MODE
    value, not a missing one (a blank budget means "run one job at a
    time", and both pages' copy says exactly that).

    C-7 (round-3 hardening, fix round 1): `max_queued_per_principal`
    renders as "" when it is `None` -- the form's own "no cap"
    placeholder, the same "" convention `memory_budget_gb_value` above
    already uses for its own null-means-unset field, because Django's
    template engine renders a bare `None` context value as the literal
    string "None", not an empty string."""
    if settings_row is None:
        return {
            "memory_budget_bytes": None,
            "memory_budget_human": None,
            "memory_budget_gb_value": "",
            "max_concurrent_jobs": JobSettings.MAX_CONCURRENT_JOBS_DEFAULT,
            "retention_limit": JobSettings.RETENTION_LIMIT_DEFAULT,
            "default_priority": JobSettings.DEFAULT_PRIORITY_DEFAULT,
            "max_queued_per_principal": "",
            "response_timeout_seconds": JobSettings.RESPONSE_TIMEOUT_SECONDS_DEFAULT,
        }
    return {
        "memory_budget_bytes": settings_row.memory_budget_bytes,
        "memory_budget_human": _human_size(settings_row.memory_budget_bytes),
        "memory_budget_gb_value": _bytes_to_gb_value(settings_row.memory_budget_bytes),
        "max_concurrent_jobs": settings_row.max_concurrent_jobs,
        "retention_limit": settings_row.retention_limit,
        "default_priority": settings_row.default_priority,
        "max_queued_per_principal": (
            "" if settings_row.max_queued_per_principal is None
            else settings_row.max_queued_per_principal
        ),
        "response_timeout_seconds": settings_row.response_timeout_seconds,
    }


def _model_names(model_refs: list[dict]) -> list[str]:
    """Model names for one row, straight from the job's `model_refs`
    snapshot -- `connection_name` when it was captured, else the bare
    `model_id`. Data, not copy: never a hardcoded model name anywhere in
    this template, per the task's own binding convention."""
    return [ref.get("connection_name") or ref.get("model_id", "") for ref in model_refs]


def _format_progress_count(value: int | float, unit: str) -> str:
    """One side of a `progress_text` fraction (a `done` or a `total`) --
    `seconds` renders via `foundation.format.format_timecode` (T10 review MINOR
    1: this used to be a LOCAL `_format_seconds_mm_ss` helper with its own
    deliberate no-hour-rollover decision, `3661` -> `"61:01"` -- a second,
    disagreeing timecode format for the SAME kind of duration
    `tools.rag.retrieval.locator_for`'s citations already rendered via
    `format_timecode` as `"1:01:01"`; an operator watching one video's
    progress cross the hour mark in the Queue while its citation already
    reads `"1:01:01"` in Ask history saw two different clocks for the same
    number. `format_timecode` is the one shared, `None`/negative/
    non-finite-safe formatter every other duration in this codebase
    already renders through -- `done`/`total` here are always
    non-negative ints off `InferenceJob.progress` (never `None`: `done`
    defaults to `0`, and `total` is only ever passed through this
    function when `_progress_text_and_percent`'s own `if total:` guard
    has already proven it truthy), so this call is a plain drop-in, not a
    behavior change beyond the intentional hour-rollover fix itself).
    `pages`/`items` (and anything else a future kind names) render as a
    plain integer, since a fractional page/item count is never
    meaningful."""
    if unit == "seconds":
        return format_timecode(value)
    return str(int(value))


def _progress_text_and_percent(progress: dict | None) -> tuple[str | None, int | None]:
    """`(progress_text, progress_percent)` for one row's stored `progress`
    dict (`InferenceJob.progress` -- `{"done", "total", "unit", "label"}`,
    or `None` for a job that has never reported one).

    `progress_text` is `"{done} of {total} {label}"` when `total` is
    truthy AND `total`'s own formatted text is non-blank, else
    `"{done} {label}"` -- a `None`/`0` total means the job genuinely
    doesn't know its own total yet (see `InferenceJob.progress`'s own
    field comment: a FABRICATED total is worse than none), so the text
    honestly drops the "of ..." half rather than inventing one. A blank
    formatted total is the SAME situation in disguise: `format_timecode`
    (via `_format_progress_count`, `unit="seconds"`) now returns `""`
    rather than raising for a `total` too large to represent as a float
    (`foundation.format.format_timecode`'s own OverflowError-safety) -- without
    this check, a `total=10**400` used to render the dishonest `"0:01 of
    "` (a fabricated-looking dangling "of" with nothing after it) instead
    of raising, because no exception ever reached `_present_row`'s guard.
    Treating a blank formatted total exactly like a falsy total closes
    that hole the same "no fabricated total" way.

    `progress_percent` is `int(done / total * 100)`, clamped to `[0, 100]`
    (a handler's own `done` could in principle overshoot `total` by one
    unit at the very last report before it flips a job to a terminal
    state -- clamping means the bar never visually overflows for that),
    ONLY when `total` is truthy AND non-blank-formatted -- `None`
    otherwise, NEVER a guessed width for an unknown total (the same "no
    fabricated bar" rule `progress_text` follows, just for the numeric
    half a template would use to size a bar div). Without this same
    check, `total=done=10**400` (both unformattable, but equal in
    magnitude) divided cleanly to `1.0` with no `OverflowError` at all,
    producing a fully-fabricated 100% bar under the blank `" of "` text
    above."""
    if not progress:
        return None, None

    done = progress.get("done", 0)
    total = progress.get("total")
    unit = progress.get("unit", "")
    label = progress.get("label", "")

    done_fmt = _format_progress_count(done, unit)
    total_fmt = _format_progress_count(total, unit) if total else ""
    has_total = bool(total) and bool(total_fmt)
    text = f"{done_fmt} of {total_fmt}" if has_total else done_fmt
    if label:
        text = f"{text} {label}"

    percent = None
    if has_total:
        percent = max(0, min(100, int(done / total * 100)))

    return text, percent


def _present_row(row: QueueRow, principal, settings_row=None) -> dict:
    """One `QueueRow` -> the plain dict the template actually renders --
    all display-only derivations (summary/label, model names, human-
    readable footprint) computed HERE, server-side, never in the template
    (the task's own binding instruction).

    Exclusivity display is deliberately narrow: `declared` (the row's own
    stored `exclusive` flag) and `unmeasured` (`footprint_bytes is None`)
    are shown, but `effectively_exclusive()`'s third reason -- oversize
    relative to the CURRENT memory budget (`models/queue/scheduler.py`
    rule 2d) -- is not re-derived here. Reproducing that reason correctly
    would mean rebuilding this job's dedup'd footprint total (rule 4)
    against the live `JobSettings.memory_budget_bytes` inside the view,
    a second, independent copy of arithmetic `plan_admissions` already
    owns -- one that could silently disagree with the scheduler's actual
    admission decision the moment either copy changed alone. `declared`
    and `unmeasured` are stable, storage-level facts that need no such
    recomputation; "runs alone because it's currently oversize" is not
    shown rather than risk being shown wrong.

    `row.progress` (T3) is KIND-OWNED, opaque JSON -- a FAILED job's is
    preserved verbatim (see `Worker._execute`'s own docstring on the
    success/failure writeback asymmetry), so it can be stale relative to
    whatever shape a CURRENT handler writes. `_progress_text_and_percent`
    is therefore called guarded, never trusted -- the same per-row
    degradation `summarize_job` above already applies to a broken
    summarizer: a malformed `progress` dict (a non-numeric `"total"`, say)
    degrades to no progress line for THIS ONE row, rather than taking the
    whole Queue page down for every row on it.

    T10 re-review MINOR 1: the guard also catches `OverflowError` and
    `AttributeError`, not just `TypeError`/`ValueError`/`ZeroDivisionError`.
    `OverflowError` is `_format_progress_count`'s own `format_timecode`
    call raising on an arbitrary-precision `total`/`done` too large for
    `float()` (`foundation.format.format_timecode` catches this itself as of
    the same review pass, but a handler could still hand `_format_
    progress_count`'s OTHER branch, `str(int(value))`, a value that
    overflows there instead -- `int()` doesn't raise on a huge int, but a
    non-numeric one reaching `int(value)` does, see below). `AttributeError`
    covers `row.progress` itself being a non-dict truthy JSON value (a
    bare string or list survives `InferenceJob.progress`'s `JSONField`
    just as easily as a dict) -- `_progress_text_and_percent`'s own `if
    not progress:` guard only screens out FALSY values (`None`/`{}`), so
    `"a non-empty string"` or `[1, 2]` sail past it into `progress.get(...)`,
    an uncaught `AttributeError` before this fix.

    IA-1: `principal` decides whether this row's CONTENT (its `summary`
    and `error`) may be shown, via `models.queue.visibility.
    may_read_job_content` -- the row itself (kind, state, timings, cancel
    control) is already known visible to `principal` by the time this is
    called (`visible_rows` filters the list first). A withheld row's
    `summary` is replaced with a constant, never left blank -- a blank
    would read as a job that carried nothing. `error` is withheld to
    empty string instead, the same rule `tools/vision/views.py`'s
    `_queue_view` already applies: a failure MESSAGE is content (it can
    quote payload back), so it follows `summary` behind the same gate
    rather than leaking through as the one field this function forgot to
    check.

    `settings_row` -- `QueueView`'s own already-fetched `IdentitySettings`
    row, threaded through to `may_read_job_content` so a page with N
    rows costs one settings read, not N (`identity.access.
    sees_all_content`'s own `settings_row=` docstring).

    T13 (spec §3.6, R3-2 ruling): `hold_off_until` reads `row.not_before`
    straight off the already-loaded row -- no query, no script -- but
    only while it is still in the FUTURE. A past `not_before` is a
    hold-off that has already expired; rendering it would read as a
    delay still in force, which it no longer is, so it is withheld to
    `None` (the template's `{% if %}` then renders nothing) exactly like
    a job that was never held off at all.
    """
    content_visible = may_read_job_content(principal, row.payload, settings_row=settings_row)
    summary, kind_label = summarize_job(row.kind, row.payload)
    if not content_visible:
        summary = "Content hidden."
    try:
        progress_text, progress_percent = _progress_text_and_percent(row.progress)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, AttributeError):
        progress_text, progress_percent = None, None
    # THE HOLD-OFF READING (spec §3.6, R3-2 ruling). `not_before` is only
    # interesting while it is still in the FUTURE: a past value is just a
    # hold-off that has expired, and rendering it would read as a delay
    # that is still in force. Display only -- this row was already loaded
    # by `queue_snapshot`, so the reading costs no query and needs no
    # script.
    hold_off_until = (
        row.not_before if row.not_before and row.not_before > timezone.now() else None
    )
    return {
        "id": row.id,
        "kind": row.kind,
        "kind_label": kind_label,
        "summary": summary,
        "content_hidden": not content_visible,
        "model_names": _model_names(row.model_refs),
        "footprint_human": _human_size(row.footprint_bytes),
        "runs_alone": row.exclusive or row.footprint_bytes is None,
        "priority": row.priority,
        "state": row.state,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error": "" if not content_visible else row.error,
        "progress_text": progress_text,
        "progress_percent": progress_percent,
        "hold_off_until": hold_off_until,
    }


class QueueView(TemplateView):
    """GET /queue/ -- the queue page.

    The settings READING (`_job_settings_row`, which owns the
    `get_solo()` try/except) and the rows area (`queue_snapshot()`) are
    fetched in two INDEPENDENT try/excepts, not one shared one -- both tables come from the same migration in
    practice, so they are almost always available or unavailable
    together, but the task's own wording ("the rows area shows... --
    settings sections may also degrade gracefully") treats them as two
    separate degradation surfaces, and coupling them into one try would
    needlessly hide a working settings section behind an unrelated rows-
    area failure (or vice versa) the one time they DO diverge. Either
    failure still never 500s: a missing settings row falls back to
    `JobSettings`'s own documented defaults (the same values `get_solo()`
    would have created), and a missing/unreachable jobs table sets
    `queue_unavailable` for the template's migrations message.

    F1 (Coherence Wave C): the four values are DISPLAYED here and EDITED
    on `JobSettingsView` below -- the registered "Job execution" settings
    page. This page keeps the reading (a budget is only meaningful beside
    what is running against it right now) and links to that one for the
    forms; both read the same two helpers above, so the numbers on the
    two pages cannot drift or degrade differently.
    """

    template_name = "jobs/queue.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["now"] = timezone.now()

        context.update(_job_settings_context(_job_settings_row()))

        try:
            snapshot = queue_snapshot()
        except QueueUnavailable:
            context["queue_unavailable"] = True
            context["finished_count"] = 0
            return context

        context["queue_unavailable"] = False
        # `identity_settings_row`: `IdentityGateMiddleware.process_view`'s
        # OWN already-fetched `IdentitySettings` row, stashed on the
        # request -- reused here (via `identity.request.settings_row_for`,
        # the one named spelling of this fallback -- round-2 FIX-NOW 5),
        # and threaded through every `visible_rows`/`_present_row` call
        # below, so a page with N queue rows across three lists costs the
        # ONE identity-settings read the middleware already paid, not one
        # per list plus one per row (`identity/tests/test_middleware.py::
        # TestTheSingleRowRead` pins the request-wide total).
        identity_settings_row = settings_row_for(self.request)
        principal = principal_for_request(self.request, settings_row=identity_settings_row)
        running_rows = [
            _present_row(row, principal, identity_settings_row)
            for row in visible_rows(principal, snapshot.running, settings_row=identity_settings_row)
        ]
        # POSITION IS THE ROW'S TRUE PLACE IN THE FULL QUEUE, enumerated
        # BEFORE filtering to what this principal may see -- a member's
        # 7th job must still show "#7", the same "how many are ahead"
        # number `rag-ask-status`'s own `position` already exposes for
        # one job at a time. Enumerating the FILTERED list instead would
        # renumber a member's own row from #7 to #1 the moment six
        # invisible rows ahead of it were dropped -- a different, wrong
        # answer to "how long is my wait".
        visible_waiting_ids = {
            row.id for row in
            visible_rows(principal, snapshot.waiting, settings_row=identity_settings_row)
        }
        waiting_rows = [
            {**_present_row(row, principal, identity_settings_row), "position": position}
            for position, row in enumerate(snapshot.waiting, start=1)
            if row.id in visible_waiting_ids
        ]
        finished_rows = [
            _present_row(row, principal, identity_settings_row)
            for row in visible_rows(principal, snapshot.finished, settings_row=identity_settings_row)
        ]

        context["running_rows"] = running_rows
        context["waiting_rows"] = waiting_rows
        context["finished_rows"] = finished_rows
        # THE VISIBLE COUNT, not `snapshot.finished_count` (box-wide,
        # every finished job whoever caused it) -- a member whose own
        # finished jobs never appear in the list must not see a nonzero
        # "N finished" either; the number and the rows beneath it come
        # off the same filtered list, the same "count and rows must
        # agree" rule `tools.rag.access.visible_ask_records`'s own
        # `HistoryView` caller follows.
        context["finished_count"] = len(finished_rows)
        context["has_any_jobs"] = bool(running_rows or waiting_rows or finished_rows)

        context["running_known_human"] = _human_size(snapshot.running_known_bytes)
        context["running_has_unknown"] = snapshot.running_has_unknown

        context["show_worker_down_hint"] = (
            not running_rows
            and snapshot.oldest_waiting_since is not None
            and (timezone.now() - snapshot.oldest_waiting_since)
            > timedelta(minutes=WORKER_DOWN_THRESHOLD_MINUTES)
        )
        return context


class JobSettingsView(TemplateView):
    """GET /queue/settings/ -- "Job execution", the registered settings
    page that owns every `JobSettings` control (F1, Coherence Wave C).

    THE FORMS USED TO SIT ON THE QUEUE PAGE, which is an activity
    surface: an operator looking for the memory budget had to open a
    page of job rows to find it, and the settings sidebar -- the one
    place this box says "configuration lives here" -- did not list it at
    all. The settings surface audit's F1 finding. The four controls moved
    here unchanged: the same two forms, the same one dispatched POST
    endpoint (`queue_settings_update` below), the same validation, the
    same copy.

    OWNED BY THE `models.queue` COLUMN, because the column that READS a
    settings row owns the page that writes it (`docs/EXTENDING.md`,
    "Where a setting lives" (a)) -- `models.queue.worker` reads
    `memory_budget_bytes`/`max_concurrent_jobs` and `models.queue.
    backend` reads `default_priority`/`retention_limit`; no other column
    reads any of the four. It extends
    `_settings.html` rather than `jobs/base.html` for the same reason
    `rag/settings.html` does: this is policy, not a reading surface.

    NOT FLAG-GATED. `JobSettings` is core -- every queued job on every
    box obeys these four values, with or without any
    `FARABUNKER_FEATURES` token -- so the sidebar entry carries no
    `Entry.feature` and this page is always mounted.

    Never-500 on a degraded box, exactly as `QueueView` above is: the
    shared `_job_settings_row`/`_job_settings_context` helpers fall back
    to `JobSettings`'s own defaults rather than raising, so an operator
    on a part-migrated box still gets the page (and its forms) instead
    of a stack trace. Costs the ONE settings read and nothing else --
    no queue snapshot, no job rows: this page is about configuration,
    and the live "what is running against this budget right now" reading
    stays on the Queue page, which already pays for a snapshot.
    """

    template_name = "jobs/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_job_settings_context(_job_settings_row()))
        return context


@require_POST
def queue_settings_update(request):
    """POST /queue/settings/update/ -- ONE endpoint for EVERY settings
    form the Job execution page renders (budget + max concurrent jobs;
    retention limit + default priority + per-principal cap; response
    timeout, added by the one-timeout task, 2026-09-17), matching the
    task's explicit "same POST handler" instruction for each. A hidden
    `form` field (`"budget"` / `"retention"` / `"timeout"`) says which one
    posted -- each form only ever submits its OWN fields, so this
    dispatches cleanly without any cross-form field collision.

    THIS IS THE SANCTIONED SETTINGS WRITE TOPOLOGY (S2, Coherence Wave
    C): one dispatched POST endpoint per settings page, never one URL
    per field -- `tools.rag.views.library_settings_update` was converged
    onto this exact shape from seven per-field endpoints, and
    `docs/EXTENDING.md`'s backend recipe now states it as the rule.

    An unrecognized/missing `form` value FLASHES AND REDIRECTS (F1,
    Coherence Wave C -- it used to be a silent no-op redirect): nothing
    identifies what to validate, so nothing is saved, and an operator
    whose tampered-with form saved nothing is told so rather than left
    to read an unchanged page as success. Never a 500, and never a raw
    400 either (the F7 convention).

    F1 (Coherence Wave C): the two forms moved from the Queue page to
    `JobSettingsView` above, so this redirects to `jobs-settings` -- the
    page whose forms posted -- rather than back to `jobs-queue`. Its path
    moved one segment down (`settings/` -> `settings/update/`) because
    the page now owns `/queue/settings/`, and its url name moved with it
    (`jobs-queue-settings` -> `jobs-settings-update`, I1, Wave C review):
    `docs/EXTENDING.md` now names this endpoint and `tools.rag.views.
    library_settings_update` together as the shape to copy, so the two
    had to stop being named differently.

    Never-500 validation grammar throughout, matching `tools.rag.views.
    library_settings_update`/`models.registry.views.connection_add`:
    every field is validated BEFORE anything is saved, so a bad field in
    either form leaves `JobSettings` completely untouched, not half
    updated.

    BOTH WAYS BACK GO THROUGH `foundation.settings_area.
    settings_redirect` (persistence round): a save made with the
    settings assistant panel open lands back on a page that still has it
    open, because the form's own `action` carried the flag in. A save
    without it redirects exactly where it always did.

    Not wrapped against an unmigrated/DB-down `JobSettings.get_solo()` --
    an unreachable table 500s here, same as `tools.rag.views`' own
    settings handlers do for `RagSettings.get_solo()`; that is documented parity with an
    existing precedent, not an oversight new to this view.
    """
    form = request.POST.get("form", "").strip()
    if form not in ("budget", "retention", "timeout"):
        messages.error(request, f"{form!r} is not a recognised settings form.")
        return settings_redirect(request, "jobs-settings")

    settings_row = JobSettings.get_solo()
    if form == "budget":
        _update_budget_and_concurrency(request, settings_row)
    elif form == "retention":
        _update_retention_and_priority(request, settings_row)
    else:
        _update_response_timeout(request, settings_row)

    return settings_redirect(request, "jobs-settings")


def _update_budget_and_concurrency(request, settings_row: JobSettings) -> None:
    """Validates and saves `memory_budget_bytes`/`max_concurrent_jobs`
    together, or saves neither. Blank `budget_gb` means "unset" (sequential
    mode) -- a clean, valid input, not an error; a non-blank value must
    parse as a positive float. `max_concurrent_jobs` must parse as a
    positive whole number. Stored via `foundation.format.gb_to_bytes` -- the SAME
    base `_bytes_to_gb_value` (this module) and `_human_size` (`console.
    inference.views`) both use, so a typed value reads back identically.

    S1 (Coherence Wave B): both fields now carry the same
    `foundation.settings_bounds` ceiling check `tools.rag.views`'
    `_upload_cap_update` already applies to its own GB field --
    `memory_budget_bytes` is a `BigIntegerField` and `max_concurrent_jobs`
    a `PositiveIntegerField`, the same two column types that motivated
    the original fix. `math.isfinite(budget_gb)` guards `gb_to_bytes`
    the same way `_upload_cap_update` guards it (an operator-typed
    `inf` parses via `float()` without raising, and `round(inf *
    1024**3)` is an uncaught `OverflowError` otherwise)."""
    budget_gb_raw = request.POST.get("budget_gb", "").strip()
    max_concurrent_raw = request.POST.get("max_concurrent_jobs", "").strip()

    budget_bytes: int | None = None
    if budget_gb_raw:
        try:
            budget_gb = float(budget_gb_raw)
        except ValueError:
            messages.error(request, "Memory budget must be a number of GB.")
            return
        if not math.isfinite(budget_gb):
            messages.error(request, "Memory budget must be a number of GB.")
            return
        if budget_gb <= 0:
            messages.error(request, "Memory budget must be greater than zero.")
            return
        try:
            budget_bytes = gb_to_bytes(budget_gb)
        except OverflowError:
            # T10 review MINOR 5's failure class: `budget_gb` itself is
            # finite (it passed `math.isfinite` above), but `budget_gb *
            # 1024**3` can still overflow to `inf` as a float (e.g.
            # `1e308`), and `round(inf)` raises. Same guard
            # `tools.rag.views._upload_cap_update` applies to its
            # own GB field.
            messages.error(request, "Memory budget is too large.")
            return
        if exceeds_field_ceiling(budget_bytes, max_stored=BIGINT_FIELD_MAX):
            messages.error(request, "Memory budget is too large.")
            return

    try:
        max_concurrent = int(max_concurrent_raw)
    except ValueError:
        messages.error(request, "Max concurrent jobs must be a whole number.")
        return
    if max_concurrent <= 0:
        messages.error(request, "Max concurrent jobs must be a positive number.")
        return
    if exceeds_field_ceiling(max_concurrent, max_stored=POSITIVE_INT_FIELD_MAX):
        messages.error(request, "Max concurrent jobs is too large.")
        return

    settings_row.memory_budget_bytes = budget_bytes
    settings_row.max_concurrent_jobs = max_concurrent
    with transaction.atomic():
        settings_row.save(update_fields=["memory_budget_bytes", "max_concurrent_jobs"])
        # S3 (Coherence Wave B): `QUEUE_SETTINGS_UPDATED`, one action per
        # settings DOMAIN like `LIBRARY_SETTINGS_UPDATED` on the `tools.
        # rag` side -- `detail` names both fields since this form saves
        # them together.
        audit.record(
            principal_for_request(request), actions.QUEUE_SETTINGS_UPDATED,
            target_type="jobsettings", target_key=settings_row.pk,
            memory_budget_bytes=budget_bytes, max_concurrent_jobs=max_concurrent,
        )
    if budget_bytes is None:
        messages.info(request, "Memory budget cleared. Jobs run one at a time.")
    else:
        messages.info(
            request,
            f"Memory budget set to {_human_size(budget_bytes)}; "
            f"max concurrent jobs set to {max_concurrent}.",
        )


def _update_retention_and_priority(request, settings_row: JobSettings) -> None:
    """Validates and saves `retention_limit`/`default_priority`/
    `max_queued_per_principal` together, or saves none of the three --
    same never-500 shape as the budget form above, copy grammar mirroring
    `tools.rag.views.history_settings_update`'s `history_limit` validation
    exactly (whole number / positive number) for the first two.

    S1 (Coherence Wave B): both `PositiveIntegerField`s now carry the
    same `foundation.settings_bounds` ceiling check `history_settings_
    update` itself now applies to `history_limit`.

    `max_queued_per_principal` (C-7, round-3 hardening, fix round 1) is
    the fourth hand-parsed field this view adds -- BLANK MEANS NULL MEANS
    NO CAP, the same "an unset value is honestly unknown, not silently
    assumed" convention `budget_gb` above already gives `memory_budget_
    bytes`, not the "must be present" convention `retention_limit`/
    `default_priority` themselves follow. A non-blank value that doesn't
    parse as a whole number, or parses to zero or less, refuses the WHOLE
    form the same way every other bad field on this page does -- nothing
    saved, not even the two fields that were fine."""
    retention_raw = request.POST.get("retention_limit", "").strip()
    priority_raw = request.POST.get("default_priority", "").strip()
    quota_raw = request.POST.get("max_queued_per_principal", "").strip()

    try:
        retention_limit = int(retention_raw)
    except ValueError:
        messages.error(request, "Retention limit must be a whole number.")
        return
    if retention_limit <= 0:
        messages.error(request, "Retention limit must be a positive number.")
        return
    if exceeds_field_ceiling(retention_limit, max_stored=POSITIVE_INT_FIELD_MAX):
        messages.error(request, "Retention limit is too large.")
        return

    try:
        default_priority = int(priority_raw)
    except ValueError:
        messages.error(request, "Default priority must be a whole number.")
        return
    if default_priority <= 0:
        messages.error(request, "Default priority must be a positive number.")
        return
    if exceeds_field_ceiling(default_priority, max_stored=POSITIVE_INT_FIELD_MAX):
        messages.error(request, "Default priority is too large.")
        return

    max_queued_per_principal: int | None = None
    if quota_raw:
        try:
            max_queued_per_principal = int(quota_raw)
        except ValueError:
            messages.error(request, "Per-principal queue cap must be a whole number.")
            return
        if max_queued_per_principal <= 0:
            messages.error(request, "Per-principal queue cap must be a positive number.")
            return
        # THE SAME CEILING GUARD ITS TWO SIBLINGS ABOVE CARRY (C-7
        # review, fix round 2): the column is a `PositiveIntegerField`,
        # so a value past `POSITIVE_INT_FIELD_MAX` is a database-level
        # error at `save()` time -- a 500 on a settings form. Refuse it
        # here, in the form's own voice, exactly as `retention_limit`
        # and `default_priority` already do.
        if exceeds_field_ceiling(max_queued_per_principal, max_stored=POSITIVE_INT_FIELD_MAX):
            messages.error(request, "Per-principal queue cap is too large.")
            return

    settings_row.retention_limit = retention_limit
    settings_row.default_priority = default_priority
    settings_row.max_queued_per_principal = max_queued_per_principal
    with transaction.atomic():
        settings_row.save(update_fields=[
            "retention_limit", "default_priority", "max_queued_per_principal",
        ])
        # S3 (Coherence Wave B): same `QUEUE_SETTINGS_UPDATED` action the
        # budget/concurrency form above uses. C-7 (round-3 hardening)
        # adds the per-principal cap to the same record -- one audit row
        # per form submission, carrying every field the form saved.
        audit.record(
            principal_for_request(request), actions.QUEUE_SETTINGS_UPDATED,
            target_type="jobsettings", target_key=settings_row.pk,
            retention_limit=retention_limit, default_priority=default_priority,
            max_queued_per_principal=max_queued_per_principal,
        )
    cap_text = "no cap" if max_queued_per_principal is None else str(max_queued_per_principal)
    messages.info(
        request,
        f"Retention limit set to {retention_limit}; default priority set to {default_priority}; "
        f"per-principal queue cap set to {cap_text}.",
    )


def _update_response_timeout(request, settings_row: JobSettings) -> None:
    """Validates and saves `response_timeout_seconds` alone -- its own
    form, its own section (one-timeout task, 2026-09-17): a chat/agent
    turn's own wall-clock limit is a different KIND of policy from the
    budget/concurrency and retention/priority/cap groups above (it bounds
    one turn's own response time, not what the queue keeps or runs
    together), so it gets its own dispatched `form` value rather than
    being folded into either sibling group.

    Bounds are 60..7200 seconds (`JobSettings.RESPONSE_TIMEOUT_SECONDS_
    MIN`/`_MAX`) -- a REAL range limit, not merely a column-overflow
    guard (fix round 1, MINOR 1): `exceeds_field_ceiling` is called
    with the FIELD'S OWN max (`RESPONSE_TIMEOUT_SECONDS_MAX`), not the
    bare `PositiveIntegerField` column ceiling, matching the convention
    `foundation/settings_bounds.py` documents and
    `tools/rag/views.py::_ragsettings_field_update` practises for
    `retrieval_top_k`/`retrieval_score_floor` -- ONE ceiling check using
    the field's own real range, never a column-overflow guard PLUS a
    second, separate range check for the identical class of input."""
    raw = request.POST.get("response_timeout_seconds", "").strip()
    try:
        value = int(raw)
    except ValueError:
        messages.error(request, "Response timeout must be a whole number of seconds.")
        return
    if value < JobSettings.RESPONSE_TIMEOUT_SECONDS_MIN:
        messages.error(
            request,
            f"Response timeout must be at least {JobSettings.RESPONSE_TIMEOUT_SECONDS_MIN} "
            "seconds.",
        )
        return
    if exceeds_field_ceiling(value, max_stored=JobSettings.RESPONSE_TIMEOUT_SECONDS_MAX):
        messages.error(
            request,
            f"Response timeout can't be more than {JobSettings.RESPONSE_TIMEOUT_SECONDS_MAX} "
            "seconds.",
        )
        return

    settings_row.response_timeout_seconds = value
    with transaction.atomic():
        settings_row.save(update_fields=["response_timeout_seconds"])
        audit.record(
            principal_for_request(request), actions.QUEUE_SETTINGS_UPDATED,
            target_type="jobsettings", target_key=settings_row.pk,
            response_timeout_seconds=value,
        )
    messages.info(request, f"Response timeout set to {value} seconds.")


@require_POST
def queue_job_cancel(request, job_id: int):
    """POST /queue/<job_id>/cancel/ -- `models.queue.backend.cancel_job`'s
    four outcomes, each mapped to the task's exact operator-facing copy.
    Wrapped against `QueueUnavailable` for the same never-500 reason every
    other view in this module is: an operator clicking Cancel during an
    unmigrated/DB-down window gets a message, not a stack trace.

    IA-1: `may_see_job_id` is checked FIRST, before `cancel_job` ever
    runs -- a member cancelling a job they may not see gets the plain
    404 `get_object_or_404` would give, never the shape of `cancel_job`'s
    own "no longer in the queue" message, which would let them learn a
    job exists by the refusal's wording alone. Cancelling is
    ADMINISTRATION (`may_see_job_id` is `is_admin`-or-owner), not
    reading -- there is no posture or setting in which an operator
    cannot clear their own queue.
    """
    principal = principal_for_request(request)
    if not may_see_job_id(principal, job_id):
        raise Http404(f"Queue job {job_id} does not exist.")
    try:
        outcome = cancel_job(job_id)
    except QueueUnavailable:
        messages.error(request, "The queue isn't ready yet — run database migrations.")
        return redirect("jobs-queue")

    if outcome == "cancelled":
        messages.info(request, "Cancelled.")
    elif outcome == "already_running":
        messages.info(request, "That job had already started — it will run to completion.")
    elif outcome == "already_finished":
        messages.info(request, "That job had already finished.")
    else:
        messages.error(request, "That job is no longer in the queue.")
    return redirect("jobs-queue")
