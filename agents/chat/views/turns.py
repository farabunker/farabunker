"""Posting a turn, and polling one.

`turn_create` queues a turn; `turn_status` is the 202's other half --
the poll target the queued turn's `status_url` points at.

THE IMPORT DIRECTION INSIDE `agents/chat/views` IS ONE-WAY: this module
imports `thread.py` (for its 503 re-render) and `service.py` (for
`start_turn`/`conversation_url`); `thread.py` imports neither this
module nor `conversations.py` -- see `views/__init__.py`'s own
docstring.

`turn_status` imports `get_job`/`QueueUnavailable` DIRECTLY from
`models.contracts.queue`, not through `agents.chat.service._job_facts`
-- `_job_facts` exists for `start_turn`'s "a position we cannot read is
a nicety" case and swallows every exception into `None`; this view
needs the opposite: `QueueUnavailable` must propagate so it can become
a 503, and every other failure is this view's own to decide, not a
degrade-to-`None` a different caller chose for a different reason.
"""
from __future__ import annotations

from django.contrib import messages
from django.db import OperationalError
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from identity.access import is_admin
from identity.request import principal_for_request, settings_row_for
from agents.attachments import attachments_for, detach_attachment
from agents.chat.rendering import turn_group_cards
from agents.chat.service import (
    QUEUE_UNAVAILABLE, attachment_problem_sentences, composer_attachment_fields,
    conversation_url, flash_attachment_outcomes, start_turn, validated_next_url,
    visible_conversation_or_404,
)
from agents.chat.views.thread import thread_context
from agents.limits import TURN_TIMEOUT_ADMIN_HINT, TURN_TIMEOUT_ERROR
from agents.models import Turn
from agents.reconcile import reconcile_stranded_turn
from agents.visibility import may_post_to, visible_turn
from agents.workstreams import scope_for_conversation
from models.contracts.queue import QueueUnavailable, get_job


def _is_xhr(request) -> bool:
    """True for the page's own `fetch()` calls. Everything works
    without JS; this only decides whether to answer with JSON or a
    redirect. Copied from `tools/vision/views.py:584-587` -- a
    five-line reading of one header, and a shared copy across a column
    boundary would be an import the law forbids."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@require_POST
def turn_create(request, conversation_id):
    """POST /chat/c/<uuid>/turn/ -- queue one turn.

    TWO ANSWERS, ONE PATH. With JS: 202 and a JSON body carrying the
    `status_url` the poller must use -- never a URL the script builds
    itself. Without JS: a redirect to the thread carrying
    `?pending=<turn_id>`, which the page renders as a pending turn and
    the operator refreshes. `tools/vision/views.py::generate` answers
    exactly this way and for exactly this reason.

    A 400 or a 503 for an XHR renders the message into the page's
    `#turn-errors` slot; a non-XHR one RE-RENDERS THE WHOLE PAGE with
    the banner, never a bare fragment -- the counterpart of
    `tools/vision/views.py:836`'s own named test.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    if not may_post_to(principal, conversation):
        # A `view` share READS the thread; it does not post into it.
        # 403, not 404: the caller can already see this conversation --
        # that is what got them here -- so pretending it does not exist
        # would be a secrecy the surrounding page does not keep.
        return HttpResponseForbidden(
            "This conversation was shared with you to read, not to post in."
        )
    text = request.POST.get("text", "")
    connection = request.POST.get("connection", "")
    # ROUND 13 (message-bound attachments): the SAME multipart POST
    # `#turn-form` now submits -- the file input and the placement
    # radios moved INTO this form. `composer_attachment_fields` is the
    # ONE reader for the three names the ONE shared composer writes
    # (audit 2, F6); its own docstring carries the rest.
    start = start_turn(conversation, text, connection=connection, actor=principal,
                       **composer_attachment_fields(request))

    if not start.ok:
        if _is_xhr(request):
            if start.status == 400:
                # `_form_errors.html`, not JSON -- the ONLY
                # refusal shaped like an invalid submission rather
                # than an unavailable service; `conversation.html`'s
                # own submit handler renders whatever HTML comes back
                # into `#turn-errors`. Every other refusal (409/503)
                # is unchanged: the JS error path already reads
                # `data.error` off a JSON body for those.
                return render(
                    request, "chat/_form_errors.html", {"message": start.error},
                    status=400,
                )
            return JsonResponse({"error": start.error}, status=start.status)
        context = thread_context(request, conversation, selected=connection)
        context["turn_error"] = start.error
        return render(request, "chat/conversation.html", context, status=start.status)

    flash_attachment_outcomes(request, start.attachments)

    if _is_xhr(request):
        return JsonResponse(
            {
                "turn_id": start.turn.pk,
                "state": "queued",
                "position": start.position,
                "priority": start.priority,
                "status_url": reverse("chat-turn-status", args=[start.turn.pk]),
                "notes": list(start.notes),
                # ROUND-13 REVIEW FIX, MINOR 2: "on the ordinary JS path
                # a rejected, oversized or failed file produces no
                # visible feedback until some later full page load...
                # 'my file silently didn't attach' [is] the same family
                # of complaint that opened this round." The POSITIVE
                # outcomes (queued/unchanged/tabular) stay session-flash
                # ONLY -- they are not the complaint, and a "Queued 2
                # file(s)" banner in the SAME danger-toned `#turn-errors`
                # slot a real refusal uses would be misleading about
                # severity. Only the two REFUSAL categories -- the ones
                # `attachment_problem_sentences` (`agents.chat.service`) builds -- ride
                # the JSON body too, so the submit handler can show them
                # immediately instead of waiting for a reload.
                "attachment_problems": attachment_problem_sentences(start.attachments),
                # D1 (chat-polish P3.1): the same block `turn_status`'s
                # own "queued" body renders, so the SUBMIT handler can
                # insert real, server-rendered markup -- the user's own
                # bubble included -- instead of hand-building a bare
                # assistant-only placeholder client-side. That
                # hand-built placeholder was the whole bug: it never
                # carried the user's message, and neither did any later
                # poll swap, because `_done_body` (before this) rendered
                # only the job's own rows.
                "html": _group_html(start.turn, request),
            },
            status=202,
        )
    return redirect(
        conversation_url(conversation, connection=connection, pending=start.turn.pk)
    )


def _attachments_by_turn(turn, request) -> dict:
    """ROUND 13 (message-bound attachments, requirement D's LIVE
    STATUS): the SAME per-turn grouping `agents.chat.views.thread.
    thread_context` builds for a full-page render, rebuilt here for
    `_group_html`'s own poller fragment -- so a poll swap's chips are
    NEVER stale relative to what a reload would show ("a chip must
    never read Processing after the system has answered from the
    file"). Queried FRESH on every call, deliberately: this is what
    makes a chip's status live across poll ticks without a second
    script loop -- the poller ALREADY re-renders this turn's whole
    group on every "queued"/"running" tick and once more on "done"
    (D1's own idempotent swap), so a status that changed between two
    ticks is picked up here for free, the same way the assistant's own
    answer already is.

    CONSOLIDATION WAVE (audit A F3 / audit B S9): the grouping ITSELF
    -- resolve the stream scope, call `attached_documents`, group by
    `turn_id` -- now lives in `agents.attachments.attachments_for`, the
    one place `thread.py` and `all_conversations.py` also call into.
    This function's own FRESHNESS requirement (above) is about WHEN it
    is called, never about whose body runs when it is -- `attachments_
    for` is called fresh, right here, on every poll tick, exactly as
    this function's own inlined body used to be.

    `turn` is either the ASSISTANT placeholder `turn_create`'s 202 body
    renders, or the turn `turn_status` resolved through `visible_turn`
    -- either way, `turn.conversation` is the SAME row `thread_context`
    would have built this from for a GET.
    """
    conversation = turn.conversation
    principal = principal_for_request(request)
    settings_row = settings_row_for(request)
    stream_scope = scope_for_conversation(principal, conversation)
    return attachments_for(principal, conversation, stream=stream_scope,
                           settings_row=settings_row)


def _group_html(turn, request, attachments_by_turn: dict | None = None) -> str:
    """`chat/_turn_block.html`, rendered over `turn_group_cards(turn)`
    -- the user's own message plus every card this turn's job wrote
    (D1, chat-polish P3.1). The ONE render call every body below (and
    `turn_create`'s own 202 body) shares, so the queued, running, and
    done bodies -- and the very first insert on submit -- can never
    render the group differently from one another or from a reload.

    `attachments_by_turn`, OPTIONAL: `_done_body` (below) already needs
    this SAME mapping to decide `"attachments_pending"`, and passing its
    own copy through here spares this call a second, identical query --
    every other caller leaves it unset, which computes it fresh
    (`_attachments_by_turn`).
    """
    if attachments_by_turn is None:
        attachments_by_turn = _attachments_by_turn(turn, request)
    cards = turn_group_cards(turn, attachments_by_turn=attachments_by_turn)
    return render_to_string("chat/_turn_block.html", {"cards": cards}, request=request)


def _carrying_user_turn_id(turn):
    """The USER turn immediately before `turn` (an ASSISTANT row) in the
    SAME conversation -- the identical query `agents.chat.rendering.
    turn_group_cards` and `agents.runtime.loop._run_turn` each already
    run for their own, different reasons, reused here a third time
    (round 13) to know WHICH turn's own attachments this exchange
    should watch for "still processing"."""
    user_turn = (
        turn.conversation.turns.filter(role=Turn.Role.USER, index__lt=turn.index)
        .order_by("-index").first()
    )
    return user_turn.pk if user_turn is not None else None


def _queued_body(turn, request) -> dict:
    """`{"state", "position", "priority", "html"}` -- the queue's own
    view of the still-waiting job (`None` for both when the queue
    cannot place it; see the module docstring on why `QueueUnavailable`
    is not caught here and is left to propagate to `turn_status`'s own
    503), plus the same rendered group `_done_body` carries (D1): the
    script's swap needs the user's own bubble at EVERY poll tick, not
    only the final one, or a requeued poll after an earlier swap would
    have nothing to re-anchor to.

    OWNER DECISION 7: a `None` job here is also the one place a STRANDED
    turn (its job row gone, past `STRANDED_TURN_GRACE_SECONDS`) becomes
    visible -- so before answering "queued" forever, this tries
    `reconcile_stranded_turn`, and re-reads and re-renders honestly when
    it closed something. `agents.reconcile.reconcile_stranded_turn`'s own
    docstring carries the condition and why it is safe to call from a
    polled GET.
    """
    job = get_job(turn.queue_job_id)
    if job is None and reconcile_stranded_turn(turn):
        # The strand is visible HERE, at the one surface that was going
        # to answer "Queued — waiting…" for ever. Re-read and answer
        # with the honest state instead, within this same poll tick --
        # a plain reload takes the same path through the rendered card,
        # so this is not a JS-only repair. `_failed_body` (what this
        # recurses into) never reconciles, so this recurses at most once.
        turn.refresh_from_db()
        return _BODY_BUILDERS[turn.state](turn, request)
    return {
        "state": turn.state,
        "position": job.position if job is not None else None,
        "priority": job.priority if job is not None else None,
        "html": _group_html(turn, request),
    }


def _running_body(turn, request) -> dict:
    """`{"state", "progress", "step", "label", "html"}` -- `progress`
    verbatim, `step`/`label` lifted out of it too (spec section 8.3) so
    the script can show them without knowing the dict's shape. `html`
    is D1's own fix, the same as `_queued_body`'s.

    OWNER DECISION 7: the same stranded-turn reconciliation `_queued_
    body` runs, and for the same reason -- see its own docstring."""
    job = get_job(turn.queue_job_id)
    if job is None and reconcile_stranded_turn(turn):
        turn.refresh_from_db()
        return _BODY_BUILDERS[turn.state](turn, request)
    progress = job.progress if job is not None else None
    return {
        "state": turn.state,
        "progress": progress,
        "step": progress.get("done") if progress else None,
        "label": progress.get("label") if progress else None,
        "html": _group_html(turn, request),
    }


def _done_body(turn, request) -> dict:
    """The whole exchange this job wrote, not one card (M6, extended by
    D1 to include the USER turn that provoked it).

    A finished turn is the USER message, the TOOL cards the loop wrote,
    PLUS the answer, and a poller that swapped in less than that would
    show strictly less than the same thread shows after F5 -- silently,
    and only for the operator who waited rather than reloading.
    `rendering.turn_group_cards` is the one place that assembles this;
    see its own docstring for the "no queue_job_id" guard (N4) it still
    carries forward unchanged.

    `"attachments_pending"` (round 13, requirement D): `True` when the
    CARRYING user turn's own chips still hold a non-terminal status
    (neither "ready" nor "failed") at the moment the ASSISTANT turn
    itself finished -- the exact gap the owner's screenshots showed
    ("it says its still processing" after a grounded answer, because
    nothing had re-rendered the STALE strip since page load). The
    poller (`conversation.html`) reads this key to decide whether to
    keep ticking a few times PAST "done", re-swapping this SAME group
    until ingestion catches up or `MAX_POLL_DURATION_MS` gives up --
    never a second script loop, the existing one just does not stop
    the instant the ANSWER is ready if a FILE is not.
    """
    by_turn = _attachments_by_turn(turn, request)
    carrying = _carrying_user_turn_id(turn)
    pending = any(
        row.get("status") not in ("ready", "failed") for row in by_turn.get(carrying, [])
    )
    return {
        "state": turn.state, "html": _group_html(turn, request, by_turn),
        "attachments_pending": pending,
    }


def _failed_body(turn, request) -> dict:
    """`{"state", "error", "setup_url"}` -- `setup_url` on EVERY
    failure, not only model-shaped ones (`AskJobStatusView`'s own
    rule, `tools/rag/views.py:1177-1185`).

    B3 (fix round 1, one-timeout task): NO `"html"` KEY, deliberately --
    `conversation.html`'s poller (HELD by PR #84) never reads one for a
    FAILED turn (`showCardError` rebuilds the card from `error`/
    `setup_url` alone, unlike the "done"/"queued"/"running" bodies'
    `swapBlock`), so returning server-rendered card markup here would be
    dead weight the held JS would never consume -- verified against the
    installed template rather than assumed.

    THE ADMIN HINT REACHES THE LIVE POLL PATH ANYWAY, as plain text
    appended onto `error` itself: `conversation.html`'s failed branch
    already renders `data.error` verbatim via `textContent` (no markup,
    but no held-file edit needed either), so an admin polling a
    turn that failed on `TURN_TIMEOUT_ERROR` sees the SAME hint
    (`TURN_TIMEOUT_ADMIN_HINT`, minus the link `_turn_card.html`'s own
    full-page rendering carries) without waiting for a reload. A
    non-admin, or a FAILED turn with any other error, gets `turn.error`
    unchanged -- the identical content-withheld gate `_turn_card.html`'s
    own FAILED branch applies, just asked here instead of in a template
    `{% if %}`.
    """
    error = turn.error
    if error == TURN_TIMEOUT_ERROR:
        principal = principal_for_request(request)
        settings_row = settings_row_for(request)
        if is_admin(principal, settings_row=settings_row):
            error = f"{error} {TURN_TIMEOUT_ADMIN_HINT}"
    return {
        "state": turn.state,
        "error": error,
        "setup_url": reverse("inference-console"),
    }


def _cancelled_body(turn, request) -> dict:
    """`{"state", "error"}` -- deviation P3-D9: `on_turn_terminal`
    writes a sentence onto a cancelled turn's row, and dropping it here
    would render a blank card while the row holds the explanation."""
    return {"state": turn.state, "error": turn.error}


_BODY_BUILDERS = {
    Turn.State.QUEUED.value: _queued_body,
    Turn.State.RUNNING.value: _running_body,
    Turn.State.DONE.value: _done_body,
    Turn.State.FAILED.value: _failed_body,
    Turn.State.CANCELLED.value: _cancelled_body,
}


# R7 (audit 2, S22): a POST to this URL is a 405 before any row is
# resolved. NOT PURELY READ-ONLY as of owner decision 7: a GET that
# lands on a stranded turn (`_queued_body`/`_running_body`) performs one
# sanctioned repair write via `reconcile_stranded_turn` -- safe under a
# safe verb because it is idempotent, conditionally guarded, and carries
# no request-supplied data; see that function's own docstring. `require_
# safe` still names the right method pair; only "read-only" no longer
# describes what a GET here can do.
#
# `require_safe`, NOT `require_GET` (audit-2 confirm, observation 7):
# `require_GET` is `require_http_methods(["GET"])` and rejects HEAD,
# which this view served before it declared anything -- Django answers
# HEAD by running the GET path and dropping the body. `require_safe` is
# the GET+HEAD pair, so the declaration excludes the unsafe verbs it was
# added for without newly refusing a request that used to work.
@require_safe
def turn_status(request, turn_id: int) -> JsonResponse:
    """GET /chat/turns/<id>/ -- the thread's poll target.

    ALWAYS 200 FOR A READABLE TURN. Queued, running, done, failed and
    cancelled all report their state in the response BODY, never the
    HTTP status -- this platform's never-500 convention, and
    `tools/rag/views.py::AskJobStatusView:1150-1160`'s rule verbatim.
    Exactly two non-200s exist: 404 for a turn id that does not exist,
    and 503 for a queue that cannot be read at all.

    IT REPORTS `Turn.State` VALUES, NEVER QUEUE STATES (deviation
    P3-D8). The queue says `succeeded`; a turn says `done`. P2's ledger
    records a live 960-second hang from exactly that drift, and the
    only defence against a second one is that this view reads the TURN
    and hands its own `state` through untranslated.

    THE ROW FIRST, THE QUEUE SECOND -- and the queue only for a turn
    still in flight. `tools/vision/views.py::queue_job_status` explains
    why in full: a queue row is pruned to `JobSettings.retention_limit`
    on every enqueue, so a finished job can vanish from under a card
    that is still polling. The turn row is the durable record.

    SECURITY NOTE, INHERITED NOT INTRODUCED: a `Turn` pk is a
    sequential integer, so this URL has the same enumeration exposure
    `GET /rag/ask/jobs/<job_id>/` already has on an unauthenticated box
    (spec section 14 gap 4). IA-1 closes it for who may READ the turn --
    `agents.visibility.visible_turn` resolves it through the
    CONVERSATION, never by bare pk, and answers `None` for an unknown id
    and an invisible one alike, so this view cannot tell them apart
    either -- both are this same 404.

    TWO DIFFERENT 503s, and the poller acts on the difference. The
    terminal one keeps its `setup_url`; the retryable one carries
    `"retryable": true`. The retryable half is BEST-EFFORT and cannot be
    a guard around the body builders alone: principal resolution and
    turn visibility touch the database BEFORE any builder runs, and
    session middleware touches it before this view is entered at all, so
    a truly unavailable database can still produce a non-JSON 5xx that
    only the loop's own counting handles.
    """
    try:
        principal = principal_for_request(request)
        turn = visible_turn(principal, turn_id)
        if turn is None:
            # Http404 is neither of the two exceptions below, so it
            # passes through this guard untouched -- the 404 behaviour
            # is byte-identical to before the guard existed, pinned by
            # `TestTheTwoNon200s::test_an_unknown_turn_is_404`.
            raise Http404(f"Turn {turn_id} does not exist.")
        body = _BODY_BUILDERS[turn.state](turn, request)
    except QueueUnavailable:
        # TERMINAL: the queue is not configured. Keeps its setup link, and
        # the loop stops -- retrying would spin for ever on a box that
        # needs an operator, not another request.
        return JsonResponse(
            {"error": QUEUE_UNAVAILABLE, "setup_url": reverse("inference-console")},
            status=503,
        )
    except OperationalError:
        # RETRYABLE: the database was momentarily unavailable (a restart,
        # a recovery). The loop counts this answer against the same
        # bounded transport ceiling a dropped connection uses, so a
        # database that never comes back cannot make the page tick for
        # ever either.
        return JsonResponse(
            {"error": "The queue is briefly unavailable; this will retry.",
             "retryable": True},
            status=503,
        )
    return JsonResponse(body)


@require_POST
def attachment_detach(request, conversation_id, doc_id):
    """POST /chat/c/<uuid>/attachments/<doc_id>/detach/ -- round 13,
    requirement E's post-send remove ("I also need the ability to
    remove an attached file").

    ROW-ADDRESSED, matching every other per-attachment/per-turn action
    on this surface. TWO GATES, in order: `may_post_to` (the SAME
    predicate `turn_create` itself gates a real POST on -- a `view`-
    level share recipient may read this thread but not mutate it, the
    identical rule that already refuses them a new turn), then `agents.
    attachments.detach_attachment`'s own row-specific answer (uploader-
    only, `tools.rag.access.may_detach_attachment` -- see that
    predicate's own docstring for why no admin override exists for this
    ONE action). `DetachOutcome.status` is rendered verbatim (404 for
    "no such attachment here", 403 for "not yours") -- this view adds
    nothing to that number, the same "the seam already decided" posture
    `turn_create` takes for `start.status`.

    A PLAIN POST, NO XHR VARIANT (unlike `turn_create`): the chip's own
    ✕ is a plain `<form>` inside a turn card, and removing one
    attachment is worth a full page reload -- every OTHER chip on the
    page (including ones the poller has not yet refreshed) comes back
    current for free, which a hand-built partial DOM removal would not
    guarantee. `messages.info`/`.error` on the outcome, then a redirect
    back to the conversation BY DEFAULT, matching `conversation_delete`'s
    own shape -- unless a validated `next` says otherwise.

    `next`, OPTIONAL (ROUND-13 REVIEW FIX, MINOR 2: "the detach ✕
    renders inside /chat/all/'s preview pane, and pressing it leaves
    the browser... removing an attachment while browsing bounces the
    operator into the conversation and loses their search/filter/page
    state"). `agents.chat.service.validated_next_url` -- the SAME
    same-origin guard `conversation_rename`/`set_conversation_archived`
    already apply to their own `next` fields -- honours it when present
    and valid; the ordinary thread-page chip carries none (`chat/
    _attachment_chip.html`'s own `next_url` stays unset there), so its
    behaviour is unchanged.
    """
    principal = principal_for_request(request)
    conversation = visible_conversation_or_404(principal, conversation_id)
    if not may_post_to(principal, conversation):
        return HttpResponseForbidden(
            "This conversation was shared with you to read, not to post in."
        )
    result = detach_attachment(principal, conversation_id=conversation_id, doc_id=doc_id)
    if not result.ok:
        if result.status == 403:
            return HttpResponseForbidden(result.message)
        raise Http404(result.message)
    messages.info(request, result.message or "Removed.")
    return redirect(validated_next_url(request) or conversation_url(conversation))
