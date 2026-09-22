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
    BLANK_MESSAGE, EDIT_LEAD, QUEUE_UNAVAILABLE, TOO_LONG_MESSAGE,
    attachment_problem_sentences, composer_attach_context, composer_attachment_fields,
    conversation_url, flash_attachment_outcomes, start_turn, validated_next_url,
    visible_conversation_or_404,
)
from agents.chat.views.thread import thread_context
from agents.limits import MAX_TURN_CHARS, TURN_TIMEOUT_ADMIN_HINT, TURN_TIMEOUT_ERROR
from agents.models import Turn
from agents.usage import FULL_CLAUSE, WINDOW_SOURCE_UNBOUND, context_usage
from agents.visibility import (
    branch_conversation, may_edit_any_turn, may_edit_turn, may_post_to, visible_turn,
)
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


@require_POST
def turn_edit(request, conversation_id, turn_id: int):
    """POST `/chat/c/<uuid>/turns/<id>/edit/` -- steps 3 and 4 of a
    branch: validate, branch, start the turn, redirect.

    THE SPLIT IS AN IMPORT-DIRECTION FACT, not a preference (spec review
    M7). `agents/chat` imports `agents/visibility`, ONE WAY, so
    `branch_conversation` cannot call `start_turn` and cannot redirect;
    this view does both, after it returns.

    VALIDATE BEFORE CREATING. The text is checked against the SAME
    `MAX_TURN_CHARS` constant `start_turn` uses, BEFORE
    `branch_conversation` is called at all, so a refused edit writes
    nothing -- no conversation row, no turns, no taint. The two refusal
    sentences are `agents.chat.service`'s own, under their public names,
    so a blank or over-long EDIT says exactly what a blank or over-long
    NEW MESSAGE says.

    ONE START PATH, NOT TWO. The edited text is started through the same
    `start_turn` the composer posts into, with the same
    `composer_attachment_fields` reader for the same three attach-door
    field names -- so files attached to the EDITED message are staged
    exactly as any other send stages them, and a change to how a turn
    starts cannot reach one surface and miss this one.

    IF `start_turn` REFUSES AFTERWARDS (an unbound role, an unreachable
    engine, a queue that is not migrated), the branch exists with its
    copied history and no answer. `start_turn` writes both rows in one
    transaction, so there is no half-written turn to clean up -- and the
    thread page already renders that state honestly with its existing
    banner. Accepted rather than papered over: the alternative would be
    deleting a conversation the operator can already see in their
    sidebar.

    CLASS O: a refused POST is a 404, never a 403 (which would confirm
    the row exists) and never a 500. `may_edit_turn` and
    `branch_conversation` each answer False/None rather than raising, so
    the refusal is this view's to spell -- and it spells it the same way
    for a stranger, a share recipient, an assistant row, a turn from
    another conversation, and a thread with an answer still running.
    """
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    conversation = visible_conversation_or_404(principal, conversation_id)
    turn = visible_turn(principal, turn_id)
    if turn is None or not may_edit_turn(principal, conversation, turn,
                                         settings_row=settings_row):
        raise Http404(f"Turn {turn_id} is not one you may edit.")

    text = (request.POST.get("text") or "").strip()
    if not text or len(text) > MAX_TURN_CHARS:
        messages.error(request, BLANK_MESSAGE if not text else TOO_LONG_MESSAGE)
        return redirect(conversation_url(conversation))

    # The SAME `settings_row` the predicate above used -- the whole
    # reason `branch_conversation` grew the keyword (Task 12's own fix
    # round): asking the predicate and then writing would otherwise cost
    # two singleton reads on one request.
    branch = branch_conversation(principal, conversation, turn,
                                 title=conversation.title, settings_row=settings_row)
    if branch is None:
        raise Http404(f"Turn {turn_id} is not one you may edit.")

    result = start_turn(branch, text, connection=request.POST.get("connection", ""),
                        actor=principal, **composer_attachment_fields(request))
    if not result.ok:
        messages.error(request, result.error)
        return redirect(conversation_url(branch))
    flash_attachment_outcomes(request, result.attachments)
    return redirect(conversation_url(branch, pending=result.turn.pk))


def _attachments_by_turn(turn, request, *, stream=None, principal=None,
                         settings_row=None) -> dict:
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

    `stream`, OPTIONAL (chat cluster, feature C): an already-resolved
    `WorkstreamScope` for this conversation, handed straight to
    `attachments_for(..., stream=...)` -- whose own `stream=` keyword
    exists precisely so a caller holding one does not re-resolve it.
    `_done_body` now holds one (its `_edit_context` needs the identical
    answer a few lines later), so the two share a single resolution
    rather than reading the same stream twice on the same tick. `None`
    -- every other caller -- resolves it here exactly as before.

    `principal` / `settings_row`, OPTIONAL (whole-branch review I-4):
    the request's own, for a caller that has already resolved them. A
    bare `principal_for_request(request)` runs `accounts_on()` ->
    `IdentitySettings.get_solo()`, a REAL query, while
    `settings_row_for(request)` is free (the middleware's stash) --
    so resolving the principal without threading the row is one
    avoidable SELECT per call. `_done_body` resolves both once and
    hands them to all three of its helpers; every other caller leaves
    them out and is unchanged.
    """
    conversation = turn.conversation
    if settings_row is None:
        settings_row = settings_row_for(request)
    if principal is None:
        principal = principal_for_request(request, settings_row=settings_row)
    stream_scope = stream if stream is not None else scope_for_conversation(
        principal, conversation)
    return attachments_for(principal, conversation, stream=stream_scope,
                           settings_row=settings_row)


def _group_html(turn, request, attachments_by_turn: dict | None = None, *,
                may_edit: bool = False, may_attach_files: bool = False,
                attach_workstream=None) -> str:
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

    `may_edit` / `may_attach_files` / `attach_workstream`, OPTIONAL
    (chat cluster, feature C): passed straight through to
    `turn_group_cards`, so the edit disclosure a polled `done` swap
    inserts is the one a reload renders. `_done_body` is the ONE body
    that supplies them (see `_edit_context` for the price and for why
    the queued and running bodies pay none of it); defaulted so
    `_queued_body`, `_running_body` and `turn_create`'s own 202 body
    keep calling this exactly as they do today.

    `edit_lead` IS IN THE RENDER CONTEXT, NOT ON THE CARD, because it is
    ONE sentence for the whole fragment rather than a per-turn value --
    the same object `agents.chat.views.thread.thread_context` puts in a
    full render's context, read from `agents.chat.service` by both. A
    swapped disclosure that opened onto a form saying nothing about what
    the button does would be exactly the reload/poll divergence the four
    docstrings in this module forbid.
    """
    if attachments_by_turn is None:
        attachments_by_turn = _attachments_by_turn(turn, request)
    cards = turn_group_cards(turn, attachments_by_turn=attachments_by_turn,
                             may_edit=may_edit, may_attach_files=may_attach_files,
                             attach_workstream=attach_workstream)
    return render_to_string("chat/_turn_block.html",
                            {"cards": cards, "edit_lead": EDIT_LEAD}, request=request)


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


def _context_body(turn) -> dict:
    """THREE INTEGERS AND NOTHING ELSE -- no window, no percentage, no
    sentence (spec decisions 21-22, review M3).

    This endpoint holds NO conversation-level render context: no
    `settings_row`, no wall, no `ToolAccess`, no `Preflight`, and
    critically NO PICKED CONNECTION, because the picker's selection
    lives in the thread page's `?connection=` query string, which
    nothing on this path ever sees. A body that computed its own
    denominator would have to run `preflight_turn` on every tick of
    every open tab -- the exact inverse of "the page pays nothing for
    it" -- and would answer with the AGENT's role binding where the page
    answered with the PICKED connection, so the ceiling would silently
    change mid-thread for anyone using the picker.

    So the window stays with the page. `window=0` here is not a value
    anybody renders: only `estimated_tokens`, `replayed_turns` and
    `total_turns` are read off the result, and the page's own
    server-written `data-window` is the only denominator that exists.

    PLUS ONE STRING, AND IT IS NOT A FOURTH NUMBER (whole-branch review
    I-3). `full_clause` is `agents.usage.FULL_CLAUSE` itself -- the same
    declared object the thread page renders -- carried so the script can
    put the `full` band's own sentence on the page at the moment the
    band flips, with `textContent` and never `innerHTML`. Spec 3.4's
    table puts that sentence in the `>= 90%` row unconditionally; before
    this, crossing 90% mid-session turned the bar red and left the
    sentence explaining it until the next F5. It is a CONSTANT, not a
    per-conversation computation -- no window, no percentage and no
    decision is made here, exactly as the paragraphs above require --
    and `agents/chat/tests/test_thread_meter.py` pins it against the page's
    own render so the two can never drift into two sentences.
    """
    usage = context_usage(turn.conversation, turn.conversation.agent,
                          window=0, window_source=WINDOW_SOURCE_UNBOUND)
    return {
        "estimated_tokens": usage.estimated_tokens,
        "replayed_turns": usage.replayed_turns,
        "total_turns": usage.total_turns,
        "full_clause": FULL_CLAUSE,
    }


def _queued_body(turn, request) -> dict:
    """`{"state", "position", "priority", "html", "context"}` -- the
    queue's own view of the still-waiting job (`None` for both when the
    queue cannot place it; see the module docstring on why
    `QueueUnavailable` is not caught here and is left to propagate to
    `turn_status`'s own 503), plus the same rendered group `_done_body`
    carries (D1): the script's swap needs the user's own bubble at
    EVERY poll tick, not only the final one, or a requeued poll after
    an earlier swap would have nothing to re-anchor to.
    """
    job = get_job(turn.queue_job_id)
    return {
        "state": turn.state,
        "position": job.position if job is not None else None,
        "priority": job.priority if job is not None else None,
        "html": _group_html(turn, request),
        # TWO BODIES CARRY THE KEY, NOT ONE (spec review m7).
        # `agents.chat.service.start_turn` writes the USER turn DONE in
        # the SAME transaction as the QUEUED placeholder, so the
        # replayed corpus grows at QUEUE time, not at finish -- a meter
        # that waited for `done` would be stale for the whole in-flight
        # window, which is exactly when the reader is deciding whether
        # to compact, and is the same shape as round 13's stale-strip
        # lesson. `_running_body`, `_failed_body` and `_cancelled_body`
        # do NOT carry it: the corpus does not change between queue and
        # finish, and a failed or cancelled turn adds no replayable text.
        "context": _context_body(turn),
    }


def _running_body(turn, request) -> dict:
    """`{"state", "progress", "step", "label", "html"}` -- `progress`
    verbatim, `step`/`label` lifted out of it too (spec section 8.3) so
    the script can show them without knowing the dict's shape. `html`
    is D1's own fix, the same as `_queued_body`'s."""
    job = get_job(turn.queue_job_id)
    progress = job.progress if job is not None else None
    return {
        "state": turn.state,
        "progress": progress,
        "step": progress.get("done") if progress else None,
        "label": progress.get("label") if progress else None,
        "html": _group_html(turn, request),
    }


def _edit_context(turn, request, *, scope=None, principal=None,
                  settings_row=None) -> dict:
    """The three card keys the edit disclosure needs, for the ONE poll
    body that can honestly answer them.

    ON `queued` AND `running` THE ANSWER IS PROVABLY FALSE and this is
    never called: `may_edit_any_turn` refuses while any turn of the
    conversation is non-terminal, and on those two paths the turn being
    polled IS one. So the every-two-seconds tick pays nothing, and the
    price below is once per finished turn.

    THE PRICE, NAMED: `may_manage_conversation` (at most one `_user_row`
    read, memoised on the settings row the middleware already stashed)
    plus one `.exists()`, plus `composer_attach_context`'s own
    `tool_access_for`. Against a body that already runs `get_job`,
    `_attachments_by_turn` and a full `render_to_string`, that is the
    cost of keeping the invariant this module states four times over --
    that a polled swap never shows less than a reload.

    `attach_workstream` RIDES ALONG BECAUSE THE FRAGMENT READS IT:
    `chat/_attach_files.html` gates TWO things on it -- the middle radio
    of the placement chooser ("This workstream only") and the whole
    remember-this-choice block -- so passing the boolean alone would
    render a TWO-value chooser here where a reload renders three plus
    the remember control. That is the defect this function exists to
    prevent, in a smaller shape.

    `principal` / `settings_row`, OPTIONAL (whole-branch review I-4):
    the request's own, threaded by `_done_body` so one tick resolves
    them once. See `_attachments_by_turn` for why a bare
    `principal_for_request(request)` is a real query and this is not.

    `scope`, OPTIONAL: an already-resolved `WorkstreamScope` for this
    conversation. `_done_body` holds one -- `_attachments_by_turn` needs
    the identical scope for `attachments_for(..., stream=...)`, whose
    `stream=` keyword exists precisely so a caller holding one does not
    re-resolve it -- so the two share a single resolution rather than
    reading the same stream twice on the same tick. `None` re-resolves,
    for a caller that has none.

    THE FK IS STILL ONLY DEREFERENCED WHERE `thread_context`
    DEREFERENCES IT (`workstream=... if may_upload else None`,
    `workstream_id=` always), so the stream ROW is never returned to a
    principal who may not upload. Widening a poll body is exactly where
    a disclosure leak would hide; this one does not open it. The row
    itself costs nothing to reach: `visible_turn`'s `select_related`
    already carries `conversation__workstream` for the context meter, so
    it is in hand on this path.
    """
    conversation = turn.conversation
    if settings_row is None:
        settings_row = settings_row_for(request)
    if principal is None:
        principal = principal_for_request(request, settings_row=settings_row)
    if not may_edit_any_turn(principal, conversation, settings_row=settings_row):
        return {"may_edit": False, "may_attach_files": False, "attach_workstream": None}
    if scope is None:
        scope = scope_for_conversation(principal, conversation)
    may_upload = bool(scope is not None and scope.may_upload)
    attach = composer_attach_context(
        principal,
        workstream=conversation.workstream if may_upload else None,
        workstream_id=conversation.workstream_id,
        settings_row=settings_row, may_upload=may_upload,
    )
    return {"may_edit": True, **attach}


def _done_body(turn, request) -> dict:
    """`{"state", "html", "attachments_pending", "context"}` -- the whole
    exchange this job wrote, not one card (M6, extended by D1 to include
    the USER turn that provoked it).

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

    FEATURE C (chat cluster) MAKES THIS THE ONE BODY THAT COMPUTES THE
    EDIT DISCLOSURE'S THREE CARD KEYS, for the same invariant this
    docstring's second paragraph already states: a swapped exchange must
    show what a reload shows, and the just-answered bubble losing its
    "Edit and carry on from here" until F5 would be strictly less. The
    queued and running bodies compute nothing -- `may_edit_any_turn` is
    provably False while a turn of this conversation is in flight, which
    on those paths is the turn being polled -- so the every-two-seconds
    tick is untouched; `_edit_context` carries the full reasoning and
    the named price.
    """
    # ONE PRINCIPAL RESOLUTION PER TICK (whole-branch review I-4), and
    # it is the house pattern rather than a local optimisation: the
    # `settings_row` comes from the middleware's stash for free, and
    # `principal_for_request` then answers WITHOUT the
    # `IdentitySettings.get_solo()` SELECT a bare call pays. This body
    # used to resolve the principal twice bare -- once here, once
    # inside `_attachments_by_turn` -- in the one function whose own
    # comment insists on "ONE SCOPE RESOLUTION PER TICK, not two".
    settings_row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=settings_row)
    # ONE SCOPE RESOLUTION PER TICK, not two. `_attachments_by_turn`
    # resolves this same scope for `attachments_for(..., stream=...)`
    # -- whose `stream=` keyword exists exactly so a caller holding one
    # does not re-resolve it -- and `_edit_context` needs the identical
    # answer a few lines later. `test_the_done_tick_costs_at_most_the_
    # budgeted_extra_reads` is flat either way, which is precisely why
    # this is said here; the ABSOLUTE pin beside it
    # (`test_the_done_tick_costs_exactly_the_reads_it_budgets`) is what
    # actually holds both this and the threading above.
    scope = scope_for_conversation(principal, turn.conversation)
    by_turn = _attachments_by_turn(turn, request, stream=scope,
                                   principal=principal, settings_row=settings_row)
    carrying = _carrying_user_turn_id(turn)
    pending = any(
        row.get("status") not in ("ready", "failed") for row in by_turn.get(carrying, [])
    )
    return {
        "state": turn.state,
        # FEATURE C: the three card keys, computed HERE and nowhere else
        # on the poll path -- see `_edit_context`'s own docstring for why
        # `_queued_body` and `_running_body` pay nothing for them.
        "html": _group_html(turn, request, by_turn,
                            **_edit_context(turn, request, scope=scope,
                                            principal=principal,
                                            settings_row=settings_row)),
        "attachments_pending": pending,
        # TWO BODIES CARRY THE KEY, NOT ONE (spec review m7).
        # `agents.chat.service.start_turn` writes the USER turn DONE in
        # the SAME transaction as the QUEUED placeholder, so the
        # replayed corpus grows at QUEUE time, not at finish -- a meter
        # that waited for `done` would be stale for the whole in-flight
        # window, which is exactly when the reader is deciding whether
        # to compact, and is the same shape as round 13's stale-strip
        # lesson. `_running_body`, `_failed_body` and `_cancelled_body`
        # do NOT carry it: the corpus does not change between queue and
        # finish, and a failed or cancelled turn adds no replayable text.
        "context": _context_body(turn),
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


# R7 (audit 2, S22): read-only, and now declared so. A POST to this URL
# is a 405 before any row is resolved.
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
    """
    principal = principal_for_request(request)
    turn = visible_turn(principal, turn_id)
    if turn is None:
        raise Http404(f"Turn {turn_id} does not exist.")
    try:
        body = _BODY_BUILDERS[turn.state](turn, request)
    except QueueUnavailable:
        return JsonResponse(
            {"error": QUEUE_UNAVAILABLE, "setup_url": reverse("inference-console")},
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
