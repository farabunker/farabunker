"""The queue's rows and its contents, and the difference between them.

A NAMED SEAM under import-law rule 2, alongside
`models.registry.bindings`: `tools/rag/views.py` and
`tools/vision/views.py` must ask "may this caller see queue job N" for
their two poll routes, and `models/contracts/queue.py::get_job` returns
a `JobStatus` that deliberately carries no `payload`. Widening
`JobStatus` instead would put another principal's prompt text on the
seam, which is the opposite of what this module is for. Recorded in
`models/README.md` and `foundation/README.md`, and pinned as a closed
set in `foundation/ops/tests/test_import_law.py`.

ROWS ARE `is_admin`; CONTENTS ARE `sees_all_content`. A queue row's
kind, owner, state, progress, priority and timings are what an
administrator needs to run the box -- to see that something is stuck,
and to cancel it -- and none of them is somebody's words. Its payload
and its answer text are.
"""
from __future__ import annotations

from identity.access import is_admin, sees_all_content
from identity.contracts.principals import ACTOR_KEY_KEY, ACTOR_KIND_KEY
from models.queue.models import QUEUED, RUNNING, InferenceJob


def _is_actor(principal, payload) -> bool:
    """Whether `principal` is the actor recorded in `payload`.

    ONE PREDICATE, THREE CALLERS -- the queryset filter, the row-list
    filter and the content check -- so the page and the cancel route
    cannot come to disagree about who owns a job.

    A non-dict payload answers False rather than raising:
    `InferenceJob.payload` is a `JSONField`, so a bare string or a list
    survives it as easily as a dict, and the queue page is a never-500
    surface.
    """
    if not isinstance(payload, dict):
        return False
    return (payload.get(ACTOR_KIND_KEY) == principal.kind
            and payload.get(ACTOR_KEY_KEY) == principal.key)


def visible_jobs(principal):
    """The queue rows this principal may see, as a QUERYSET -- for
    `jobs-queue-cancel` and the two poll routes, which have an id.

    FAIL CLOSED FOR MEMBERS, OPEN FOR ADMINISTRATORS. A job whose
    payload carries neither key -- every job enqueued before this phase
    -- is visible to `is_admin` and to nobody else. A member sees
    nothing they did not cause.

    A JSON filter, not an index: the queue table is retention-pruned and
    page-limited already, so the scan is bounded. If a future box makes
    it hot, the fix is an expression index on `(payload->>'actor_key')`
    -- named here so it is a follow-up rather than a redesign.
    """
    qs = InferenceJob.objects.all()
    if is_admin(principal):
        return qs
    return qs.filter(**{f"payload__{ACTOR_KIND_KEY}": principal.kind,
                        f"payload__{ACTOR_KEY_KEY}": principal.key})


def visible_rows(principal, rows, *, settings_row=None):
    """The subset of already-fetched `QueueRow`s this principal may see.

    The queue PAGE renders `models.queue.backend.queue_snapshot()`,
    which fetches every row in ONE query and partitions it in Python. A
    queryset filter here would undo that; this filters the rows the page
    already holds, using the same predicate the queryset uses.

    `settings_row`: an already-fetched `IdentitySettings` row, OPTIONAL
    and keyword-only -- every caller in the tests leaves it out, and
    this reads the singleton itself through `is_admin()`, exactly as
    before. `models.queue.views.QueueView` calls this once per snapshot
    LIST (running/waiting/finished) and passes the row it already has
    (`IdentityGateMiddleware`'s own `request.identity_settings_row`) so
    three calls in one request cost one read, not three -- the same
    reason `identity.access.is_admin`/`sees_all_content` take their own
    `settings_row=` keyword.
    """
    if is_admin(principal, settings_row=settings_row):
        return list(rows)
    return [row for row in rows if _is_actor(principal, row.payload)]


def may_see_job_id(principal, job_id: int) -> bool:
    """Whether `job_id` is a row this principal may see, by id.

    For the two POLL routes (`rag-ask-status`, `vision-queue-status`),
    which reach the queue through `models.contracts.queue.get_job` and
    get a `JobStatus` back -- a shape that carries no payload, so the
    actor keys are not on it and the row has to be asked for
    separately.
    """
    if is_admin(principal):
        return True
    return visible_jobs(principal).filter(pk=job_id).exists()


def may_read_job_content(principal, payload, *, settings_row=None) -> bool:
    """Whether the PAYLOAD and the ANSWER/RESULT text of a row may be
    rendered to this principal: they own it, or `sees_all_content`.

    The second half of the rows-versus-content split. `_present_row`
    calls it once per row and, when it is False, renders the row with
    its operational fields and an explicit "content hidden" marker in
    place of the payload -- NOT A BLANK, which would read as a job that
    carried nothing.

    `settings_row`: same optional, keyword-only, already-fetched row
    `visible_rows` takes, for the same reason -- `_present_row` calls
    this ONCE PER ROW, and a queue page with N rows must not turn into
    N reads of the settings singleton.
    """
    content_ok = sees_all_content(principal, settings_row=settings_row)
    return content_ok or _is_actor(principal, payload)


def live_job_for(kind: str, **payload_match) -> int | None:
    """The id of a QUEUED-or-RUNNING job of `kind` whose payload matches
    every `payload_match` pair, or `None`.

    THE IN-FLIGHT QUESTION, asked from another column. `models.contracts.
    queue` cannot answer it: that module is a pure leaf that deliberately
    does not import `models.queue.models`, and `get_job` needs an id the
    asker does not have. This module already holds `InferenceJob` and
    already filters on `payload__<key>`, and import-law rule 2 already
    names it the one submodule of `models.queue` another column may
    reach -- so the answer has one home rather than a new seam.

    NOT A VISIBILITY ANSWER, and the difference is worth stating: every
    other function here narrows rows to a PRINCIPAL. This one takes no
    principal and answers a question about the QUEUE, for a caller that
    has already run its own permission check on the row the job is about
    (`agents.chat.views.workstreams.workstream_consolidate` resolves the
    conversation through `visible_conversations` and the stream through
    `may_manage_workstream` before it asks). It lives here because this
    module is the sanctioned door into `models.queue`, not because it is
    about who may see what.

    ADVISORY, NOT A LOCK. A second submission can race between this read
    and its own enqueue; `uniq_notes_per_conversation` is the database's
    own answer to that, and this reader is what turns the common case
    into an honest 409 instead of a duplicate job.
    """
    rows = InferenceJob.objects.filter(kind=kind, state__in=(QUEUED, RUNNING))
    for key, value in payload_match.items():
        rows = rows.filter(**{f"payload__{key}": value})
    return rows.order_by("-id").values_list("id", flat=True).first()
