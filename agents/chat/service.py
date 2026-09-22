"""Starting a turn: preflight, two rows, one enqueue.

Everything two views both need and neither should own lives here rather
than in either view module. This module started as the home
of `conversation_url`: `conversation_start`'s redirect needs it, and
`turn_create`'s no-JS redirect needs the same builder, so it belongs to
neither view -- a helper reached by an import back up the package
(`turns.py` -> `conversations.py`, say) is a cycle waiting for its
second caller (see `views/__init__.py`'s own docstring on the package's
one-way import direction).

This module also declares the three poller constants
(`POLL_INTERVAL_MS`, `MAX_TRANSPORT_RETRIES`, `MAX_POLL_DURATION_MS`),
`TITLE_MAX`, `TurnStart`, and `start_turn` -- the HTTP-FREE turn
starter. It returns a `TurnStart` -- a status number and a sentence --
and the views translate that into a 202, a redirect, or a 503. Two
callers need it (the index's "start a conversation with a message" and
the thread's message form), and a rule that lived in one view and was
called from the other would be the drift this whole design keeps
naming.

It follows `manage.py agent_turn` step for step, because that command
is this page's REFERENCE CLIENT and its exit codes are this page's
states: preflight BEFORE anything is written, the USER turn and the
placeholder ASSISTANT turn in one transaction, `enqueue`, then the
`queue_job_id` stamp.

This module does not import any view module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme, urlencode

from agents.attachments import cleanup_staged_documents, stage_turn_attachments
from agents.contracts.attachments import (
    AttachmentUploadResult, BAD_PLACEMENT_MESSAGE, placement_is_valid,
)
from agents.entitlements import tool_access_for
from agents.limits import MAX_TURN_CHARS
from agents.models import Turn
from agents.runtime.preflight import (
    AGENT_NOT_PERMITTED, MODEL_NOT_PERMITTED, dropped_tool_notes, preflight_turn,
)
from agents.visibility import chat_surface_conversations
from agents.workstreams import workstream_scope
from foundation.settings_area import preserve_assistant_flag
from identity.access import labelling_entitlements
from identity.contracts.principals import payload_fields
from models.contracts.queue import QueueQuotaExceeded, QueueUnavailable, enqueue, get_job

# Round 13's own chat-door tool key, a LITERAL string rather than an
# import for the identical reason `agents/chat/views/workstreams.py`'s
# own `_RAG_INGEST_TOOL_KEY` and `agents/runtime/prompt.py`'s own
# `_RAG_SEARCH_TOOL_KEY`/`_RAG_ASK_TOOL_KEY` all are: `agents/` may not
# import `tools/` at all (import-law rule 3), so `tools.rag.tools.
# RAG_INGEST_TOOL_KEY` is unreachable from here. This module does not
# import `agents.chat.views.workstreams` either -- `service.py` has no
# view-package dependency today, and gaining one just to reach a string
# constant would be the wrong kind of coupling for what this is.
_RAG_INGEST_TOOL_KEY = "rag.ingest"

logger = logging.getLogger(__name__)

TITLE_MAX = 60

# The `Conversation.title` COLUMN's own width, which is a different
# number from `TITLE_MAX` and answers a different question: `TITLE_MAX`
# is how much of a first message makes a readable list entry, this is
# how much the database will accept at all. UI-3b's rename and duplicate
# truncate to THIS -- an operator who types a long title meant to type
# it, and clipping them to 60 would be the page overruling them -- while
# an auto-derived title still stops at `TITLE_MAX`.
#
# A CONSTANT, NOT `Conversation._meta.get_field("title").max_length`: no
# module under `agents/chat` imports `agents.models` (ruling 4c, and
# `views/conversations.py`'s own note). `agents/tests/test_models.py`
# pins the two against each other so this copy cannot drift from the
# column.
TITLE_COLUMN_MAX = 255

# The no-JS thread's poller tuning. Declared HERE, not in
# `views/turns.py` or `views/thread.py`, because `thread_context` hands
# these three numbers to the template and `thread.py` must not import
# `turns.py` (the package's import direction is one-way -- see
# `views/__init__.py`'s own docstring). The poll script and its own
# test both read them from this module. Values match the poller this
# one mirrors, `tools/rag/templates/rag/ask.html:355-357`.
POLL_INTERVAL_MS = 2000
MAX_TRANSPORT_RETRIES = 3
# I3 (fix round 1, one-timeout task): BOUND TO `JobSettings.RESPONSE_
# TIMEOUT_SECONDS_MAX` (7200s, `models/queue/models.py`) -- a NAMED
# NUMBER, not an import: `agents/` may not read `models.queue` (import
# law), so this cannot follow that field's ceiling automatically the way
# the turn's own deadline does (`agents.runtime.loop.resolved_turn_
# timeout`). It is this client-side "give up and tell the reader to
# reload" safety net, a DIFFERENT axis from the turn's own deadline (it
# never ends the turn -- the loop's own check is still the only thing
# that does), but it used to sit at 10 minutes while the operator-
# editable turn timeout could run up to 7200s (two hours) -- every turn
# past 10 minutes, the exact range the ceiling was raised to allow, told
# the reader to reload well before the turn itself could possibly be
# done. Set above the field's own MAX so this net can never fire before
# the real timeout could: an operator who raises `response_timeout_
# seconds` all the way to 7200s still gets a poller that keeps watching
# for the whole of it, with headroom. See `docs/OPERATIONS.md`'s
# response-timeout entry for the operator-facing note on this pairing.
MAX_POLL_DURATION_MS = 7500 * 1000

_BLANK = "Say something: the message cannot be blank."
# C-7 (round-3 hardening, H39): `agents.limits.MAX_TURN_CHARS`, the fifth
# number that bounds a turn -- see that module's own docstring.
_TOO_LONG = f"That message is too long — the limit is {MAX_TURN_CHARS} characters."
# THE SAME TWO SENTENCES, UNDER PUBLIC NAMES (chat cluster, feature C).
# `agents.chat.views.turns.turn_edit` validates the edited text against
# the SAME `MAX_TURN_CHARS` constant `start_turn` uses, BEFORE
# `agents.visibility.branch_conversation` is called at all, so a refused
# edit writes nothing -- no conversation row, no turns, no taint. It
# therefore needs the refusal WORDING before `start_turn` has run, and
# the house rule is one declaration per user-facing sentence: these are
# aliases, not copies, so the blank/too-long message an edit shows and
# the one a new turn shows can never drift apart.
BLANK_MESSAGE = _BLANK
TOO_LONG_MESSAGE = _TOO_LONG
# FEATURE C's declared disclosure lead -- what pressing "Send from here"
# is about to do, said before the button. DECLARED HERE, in the shared
# leaf, rather than in `views/turns.py` where the view that consumes the
# POST lives: `agents/chat/views/thread.py` renders it and may not
# import `views/turns.py` (the package's one-way import direction,
# `agents/chat/views/__init__.py`'s own docstring), and `views/turns.py`
# renders it too on the poller's `done` tick, so the ONE module both
# already import is the only place it can live without a cycle or a
# second copy.
#
# THIS STRING IS THE SENTENCE, and both renderers interpolate it rather
# than retyping any part of it -- which is what makes the reload/poll
# parity a property rather than a coincidence.
EDIT_LEAD = (
    "This starts a new conversation with everything before this message. "
    "The original stays as it is. Files attached earlier in this conversation "
    "are not carried over."
)
# FEATURE C's provenance banner, the same public-declaration rule as
# `EDIT_LEAD` just above and for the same reason: `agents/chat/views/
# thread.py` renders it and may not import `views/turns.py`, so this
# shared leaf both already import is where it lives.
#
# TASK 14 REVIEW FIX (I1/M3): these used to live in `agents/visibility.py`
# -- the brief's own placement -- but that module is the column's
# predicate-and-writer surface (its own string constants, e.g.
# `AGENT_NOT_YOURS`, are refusal sentences its OWN writers return); this
# is a chat PAGE's prose, consumed only by `chat/conversation.html`
# through `thread_context`, exactly the shape `EDIT_LEAD` already lives
# here for. The import direction does not bite: `agents/chat/service.py`
# already imports from `agents.visibility` (`chat_surface_conversations`,
# above), never the reverse, and `thread.py` already imports both
# modules.
#
# `BRANCH_PROVENANCE_UNNAMED` NAMES NOTHING (I1, review Important 1). The
# banner used to be assembled in the template out of `BRANCH_PROVENANCE_
# LEAD` plus an `{% if branched_from %}<a>...</a>{% endif %}` that
# rendered NOTHING on both the deleted-parent and the unreadable-parent
# path -- `branched_from` is `None` either way -- leaving the literal
# rendered sentence "Branched from  at message 3" (HTML collapses the
# double space, so that really is what a reader saw). That is exactly
# the shape AGENTS.md's "user-facing sentences are declared once, in
# Python" exists to forbid: a sentence assembled from fragments plus a
# hole nothing fills. This constant is the hole's own honest filling --
# a whole, grammatical, disclosure-free phrase -- so the template's own
# `{% else %}` (never a bare omission) always has a real noun phrase to
# render, on both fallback paths alike.
BRANCH_PROVENANCE_LEAD = "Branched from"
BRANCH_PROVENANCE_UNNAMED = "an earlier conversation"


def branch_point_ordinal(parent, index: int) -> int:
    """WHICH OF THE PARENT'S OWN MESSAGES the branch left off at, 1-based
    and countable by a reader -- the ordinal of the turn at `index` among
    `parent`'s finished, root-depth USER turns.

    WHY NOT `Turn.index` (whole-branch review I-2). That column is a
    DENSE counter over EVERY row in the thread -- user, assistant, tool
    cards, and delegate turns at `depth >= 1` (`agents/models.py::Turn.
    next_index` returns `0` for the very first one). Nothing on the
    thread page renders it, and a reader's notion of "message" is the
    bubbles they can see, so the banner used to render "at message 0"
    for a branch off the FIRST message and "at message 4" for the second
    message of a thread that had used one tool. The column stays exactly
    what it was -- `branched_at_index` is PROVENANCE, queryable, not
    display -- and this is the display half, computed at render time
    from the parent that is being pointed at.

    ONE QUERY, BOUNDED BY THE PARENT'S OWN LENGTH -- a `.count()` over
    the same `(conversation, index)` index `agents.usage.context_usage`
    already walks, and flat in the BRANCH's length, which is what
    `agents/chat/tests/test_thread.py::test_the_meter_costs_the_same_on_
    a_short_and_a_long_conversation` pins.

    THE FILTER IS `is_editable_turn_row`'S OWN SET plus the role the
    branch point always has: `agents.visibility.may_edit_turn` admits
    only a finished root-depth USER turn, so by construction the answer
    is at least 1. It can still be 0 if the parent's earlier rows were
    deleted after the branch was taken, and `thread_context` renders no
    number at all in that case rather than "at your message 0".
    """
    return parent.turns.filter(role=Turn.Role.USER, depth=0,
                               state=Turn.State.DONE, index__lte=index).count()


def branch_provenance_tail(ordinal: int) -> str:
    """The provenance banner's own closing fragment: where in the parent
    this thread left off ("at your message N"), N being
    `branch_point_ordinal` above -- the number the reader can point at,
    never the raw row index (whole-branch review I-2).

    A FRAGMENT, NAMED AS ONE (review M3) -- `branch_provenance_sentence`
    was this function's name until the fix round above, and the name
    overclaimed: it never returned a whole sentence, only this tail, and
    calling a fragment a sentence is what let the missing-middle defect
    (I1) go unnoticed as long as it did. The parent's own title (or its
    declared stand-in, `BRANCH_PROVENANCE_UNNAMED` above) is the
    TEMPLATE's half, not this function's: whether the title may be NAMED
    is a visibility question `thread_context` answers through `visible_
    conversations`, and this function is handed only a number, never the
    parent row, so it could not leak one if it tried.

    BOTH FALLBACK SENTENCES CARRY NO NUMBER, and that is declared here
    rather than left to be inferred. The ordinal is counted over the
    PARENT's rows, so a parent that was deleted (`branched_from_id is
    None` after `SET_NULL`) or that this reader may not see has no rows
    to count -- and a number counted over nothing, or over a thread the
    reader cannot open to check, is exactly the unverifiable number I-2
    is about. On both paths the banner is the whole, grammatical,
    disclosure-free sentence "Branched from an earlier conversation."
    and nothing more.
    """
    return f"at your message {ordinal}"


# Public (no leading underscore): `agents.chat.views.turns` imports this
# rather than keeping its own copy of the same sentence (CQ-11).
QUEUE_UNAVAILABLE = (
    "The queue isn't ready yet — run database migrations, then try again."
)
_ENQUEUE_FAILED = "Couldn't add your message to the queue — nothing was queued."
_AGENT_DISABLED = (
    "This conversation's agent is no longer enabled, so it cannot take another "
    "turn. Its history stays readable."
)
_IN_FLIGHT = (
    "This conversation is still working on the previous message. Wait for that "
    "answer before sending another."
)
_COLLIDED = (
    "Another message reached this conversation at the same moment, so nothing "
    "was queued. Send it again."
)
_ATTACH_NOT_PERMITTED = (
    "You don't have permission to attach files to this conversation."
)
# ROUND-13 REVIEW FIX, NIT 1: shared with `tools.rag.services.stage_
# turn_attachments`'s own defensive re-check -- see `agents.contracts.
# attachments.BAD_PLACEMENT_MESSAGE`'s own docstring for why this used
# to be two independently-typed copies of the same sentence.
_BAD_PLACEMENT = BAD_PLACEMENT_MESSAGE


class _AttachmentsRefused(Exception):
    """Raised INSIDE `start_turn`'s own `transaction.atomic()` block to
    unwind a turn that already wrote its USER row once `stage_turn_
    attachments` (round 13) comes back `ok=False` -- a registered
    uploader that could not be resolved, or one that raised. Caught by
    `start_turn` itself, immediately outside the `with` block, and
    turned into the matching `TurnStart` refusal; never allowed to
    escape this module. Raising (rather than an early `return` from
    inside the `with`) is what rolls the just-written USER `Turn` row
    back -- a message whose OWN files could not be staged must not sit
    in the conversation as if it had sent successfully with none.
    """

    def __init__(self, result: AttachmentUploadResult) -> None:
        self.result = result


@dataclass(frozen=True)
class TurnStart:
    """What happened, in transport-free terms.

    `status` is an HTTP number because both callers are views and
    inventing a parallel vocabulary for them to translate would buy
    nothing. Everything else here is data the view renders.
    """

    ok: bool
    status: int
    error: str = ""
    setup_url: str = ""
    notes: tuple[str, ...] = ()
    turn: object | None = None
    job_id: int | None = None
    position: int | None = None
    priority: int | None = None
    # ROUND 13 (message-bound attachments): the raw per-file outcome
    # `tools.rag.services.stage_turn_attachments` returned, or `None`
    # when this turn carried no files at all. `agents.chat.views.turns.
    # turn_create` is what turns this into Django flash messages
    # (`messages.info`/`.error`, `tools.rag.views.document_upload`'s
    # own pre-round-13 wording, reused verbatim) -- kept OUT of this
    # dataclass's own rendering, the same "views render, service.py
    # only decides" split every other field here already respects
    # (`error`/`setup_url` are TEXT, not markup, for the identical
    # reason).
    attachments: AttachmentUploadResult | None = None


def start_turn(conversation, text: str, *, connection: str = "", actor,
               files=None, placement: str = "",
               remember_placement: bool = False) -> TurnStart:
    """Queue one turn for `conversation`, or refuse without writing.

    `actor` is REQUIRED and keyword-only, deliberately: this function
    builds the payload the runtime later acts as, and a default would be
    a fail-open default -- the caller that forgot it would silently
    enqueue a turn attributed to nobody, and every visibility rule
    downstream would then be reasoning about a blank.

    `files`/`placement`/`remember_placement`, ALL OPTIONAL and keyword-
    only (round 13, message-bound attachments; owner feedback: "we
    shouldn't process unless the message is actually submitted with the
    file"). `files` -- a list of Django `UploadedFile`s, or `None`/empty
    for a plain text-only turn. ROUND 18 GIVES `agents.chat.views.
    conversations.conversation_start` (this function's OTHER caller) THE
    IDENTICAL FILE INPUT `agents.chat.views.turns.turn_create` already
    had -- `chat/_composer.html` is the one composer both surfaces
    render now, so a start carrying files is no longer a shape this
    function only ever sees from the turn side; a start with `files`
    empty (the common case, and every call before this round) still
    behaves exactly as before. When `files` is non-empty, this function -- not
    `agents.chat.views.turns.turn_create` -- resolves the SAME two facts
    `tools.rag.views.document_upload` used to resolve for the chat door
    before round 13 retired that path: whether `actor` may attach files
    to THIS conversation at all (`tool_access_for`, the `rag.ingest`
    tool -- the identical predicate `agents.chat.views.thread.
    thread_context` computes for the RENDER side, re-derived here for
    the POST gate, the same "recompute, never trust a render-time
    snapshot" rule `preflight_turn` above already follows), and, inside
    a workstream, whether `actor` may upload to THAT stream
    (`workstream_scope(actor, ...).may_upload` -- `None`/`False` means
    the upload is treated as LOOSE, offering only the two-value chooser,
    never a silent contained placement this principal could not
    otherwise reach). `placement` is then validated against the
    resulting two/three-value set BEFORE a single row is written --
    author decision 17's own rule, "the one field a server-side default
    would silently undo an owner's decision", applied here exactly as
    it always was in the retired view.
    """
    message = (text or "").strip()
    if not message:
        return TurnStart(False, 400, _BLANK)
    if len(message) > MAX_TURN_CHARS:
        return TurnStart(False, 400, _TOO_LONG)

    files = [f for f in (files or []) if f]
    resolved_workstream_id = None
    if files:
        if not tool_access_for(actor).allows(_RAG_INGEST_TOOL_KEY):
            return TurnStart(False, 403, _ATTACH_NOT_PERMITTED)
        if conversation.workstream_id:
            scope = workstream_scope(actor, conversation.workstream_id)
            if scope is not None and scope.may_upload:
                resolved_workstream_id = conversation.workstream_id
        # CONSOLIDATION WAVE (audit B, S12): the BRANCH, not merely the
        # refusal sentence, now lives once in the contract -- see
        # `placement_is_valid`'s own docstring.
        if not placement_is_valid(placement, in_stream=resolved_workstream_id is not None):
            return TurnStart(False, 400, _BAD_PLACEMENT)

    agent = conversation.agent
    if not agent.enabled:
        return TurnStart(False, 503, _AGENT_DISABLED,
                         setup_url=reverse("inference-console"))

    # ONE TURN AT A TIME PER CONVERSATION. The form is still on the
    # page while an answer is pending, so a double-submit or an
    # impatient second message is the ordinary way two `agent.turn`
    # jobs end up racing on one thread -- and `Turn.next_index` is a
    # read-then-write whose safety `agents/models.py` justifies
    # PRECISELY by "a turn is enqueued only after the previous one
    # finished". This refusal is what makes that sentence true for the
    # page, as it already is for the synchronous CLI. 409, not 400:
    # nothing about the message is wrong, and the same message will be
    # accepted in a moment.
    if Turn.objects.filter(
        conversation=conversation, role=Turn.Role.ASSISTANT,
        state__in=(Turn.State.QUEUED, Turn.State.RUNNING),
    ).exists():
        return TurnStart(False, 409, _IN_FLIGHT)

    check = preflight_turn(agent, connection, actor=actor, conversation=conversation)
    if not check.ok:
        # 403 for "you may not use that model" or "you may not use that
        # agent", 503 for everything else. An authorization refusal is
        # not an unavailable service, and the view renders `start.status`
        # verbatim, so this is the only place that has to know the
        # difference.
        status = 403 if check.reason in (MODEL_NOT_PERMITTED,
                                         AGENT_NOT_PERMITTED) else 503
        return TurnStart(False, status, check.message,
                         setup_url=reverse("inference-console"))

    notes = dropped_tool_notes(check)

    # BOTH ROWS BEFORE THE ENQUEUE, in one transaction. The placeholder
    # is the durable side-effect that predates the job -- the row
    # `on_turn_terminal` exists to fix up -- and writing it after the
    # enqueue would open a window in which a cancel finds nothing to
    # flip. The `atomic` block is what makes the rollback below real:
    # a failed enqueue must leave no half-written thread.
    upload_result: AttachmentUploadResult | None = None
    try:
        with transaction.atomic():
            _title_if_unset(conversation, message)
            user_turn = Turn.objects.create(
                conversation=conversation, index=Turn.next_index(conversation),
                role=Turn.Role.USER, text=message, state=Turn.State.DONE,
                # C-1 (H25): WHO WROTE THIS. `int(actor.key)` on a "user"
                # principal, matching `agents.visibility.share_conversation`'s
                # own `shared_by_id` stamp; `None` for every other kind
                # (open box, service caller) -- `Turn.author`'s own
                # docstring, "not attributable", never "the platform".
                author_id=int(actor.key) if actor.kind == "user" else None,
            )
            # ROUND 13 (message-bound attachments): staged HERE, inside
            # this SAME transaction, AFTER the user turn exists (so its
            # pk is available to bind attachments to) and BEFORE the
            # agent turn is enqueued (requirement 1: nothing uploads
            # until the message is submitted, and the message that
            # submitted them is THIS one). `stage_turn_attachments`
            # (`agents.attachments`, the sanctioned seam) is the ONLY
            # entry point into `tools.rag.services.stage_turn_
            # attachments` -- `agents/` may not import `tools/` at all.
            if files:
                upload_result = stage_turn_attachments(
                    actor, conversation_id=conversation.id, turn_id=user_turn.pk,
                    files=files, workstream_id=resolved_workstream_id,
                    placement=placement, remember_placement=remember_placement,
                    actor=actor,
                )
                if not upload_result.ok:
                    # Unwinds the USER row just written -- see
                    # `_AttachmentsRefused`'s own docstring.
                    raise _AttachmentsRefused(upload_result)
            placeholder = Turn.objects.create(
                conversation=conversation, index=Turn.next_index(conversation),
                role=Turn.Role.ASSISTANT, state=Turn.State.QUEUED,
            )
            payload = {
                "conversation": str(conversation.id),
                "turn": placeholder.pk,
                "agent": agent.slug,
                "text": message,
                # A pk as a STRING, matching both existing precedents
                # (`tools/rag/jobs.py:9-10`, `tools/vision/jobs.py:11-24`)
                # -- the planner and the handler re-resolve it fresh.
                "connection": str(connection) if connection else None,
                # One legal value. Flow-as-turn is deviation P3-D4.
                "mode": "chat",
                # WHO ASKED. The runtime reads these back with
                # `identity.contracts.principals.principal_from_payload`
                # and runs the turn as that principal -- not as the
                # agent, which is the design the acting rule reverses.
                **payload_fields(actor),
            }
            job_id = enqueue("agent.turn", payload)
    except _AttachmentsRefused as exc:
        # NO CLEANUP HERE (I-2's own scope): `_AttachmentsRefused` is
        # raised only by the placement-refusal branch, BEFORE
        # `stage_turn_attachments` ever moves a byte (`AttachmentUpload
        # Result.staged_document_ids`'s own docstring) -- there is
        # nothing on disk yet for this refusal to have orphaned.
        return TurnStart(False, exc.result.status, exc.result.error)
    except QueueUnavailable:
        # I-2 (round-13 review fix): `stage_turn_attachments`, just
        # above, may already have moved real bytes into the managed
        # store before THIS step raised -- the transaction this `with`
        # block just rolled back undoes every DB row it wrote, but not
        # that filesystem move. `cleanup_staged_documents` removes the
        # orphan directly; a turn that carried no files (`upload_result`
        # still `None`) or whose staging moved nothing new costs one
        # empty-tuple no-op.
        _cleanup_any_staged_documents(upload_result)
        return TurnStart(False, 503, QUEUE_UNAVAILABLE,
                         setup_url=reverse("inference-console"))
    except QueueQuotaExceeded as exc:
        # C-7 (H39): an honest refusal naming the quota, not the generic
        # `_ENQUEUE_FAILED` the broad `except Exception` below would give
        # -- same orphan-cleanup reasoning as `QueueUnavailable` just
        # above (staged files may already be on disk). 429, not 503: this
        # is a refusal about THIS caller's own queue usage, not the queue
        # being unavailable to anyone.
        _cleanup_any_staged_documents(upload_result)
        return TurnStart(False, 429, str(exc))
    except IntegrityError:
        # `uniq_turn_index` firing: two writers reached the same index,
        # which is exactly the honest failure `agents/models.py::
        # next_index` says it prefers to a silently reordered
        # conversation. Caught BY NAME and before the broad clause, so
        # the sentence can say what actually happened instead of
        # blaming the queue for a collision the queue never saw.
        logger.info("chat: turn index collision in conversation %s", conversation.id)
        _cleanup_any_staged_documents(upload_result)  # I-2, same reasoning as above
        return TurnStart(False, 503, _COLLIDED)
    except Exception:  # noqa: BLE001 -- log detail, then degrade to a clean 503
        logger.exception("chat: failed to enqueue an agent.turn job")
        _cleanup_any_staged_documents(upload_result)  # I-2, same reasoning as above
        return TurnStart(False, 503, _ENQUEUE_FAILED,
                         setup_url=reverse("inference-console"))

    # Stamped right after the enqueue, while the job is still running,
    # for the same reason `GenerationJob` gets its stamp right after
    # `submit_job` returns (`tools/vision/jobs.py:323-324`): without it
    # nothing links a running queue job back to the turn it is
    # answering. `agents.runtime.audit.close_open_invocations`
    # also correlates on it.
    Turn.objects.filter(pk=placeholder.pk).update(queue_job_id=job_id)
    # `.update()` writes the column but never touches the in-memory
    # `placeholder` object -- and `turn_create`'s 202 body (D1,
    # chat-polish P3.1) renders `rendering.turn_group_cards(start.turn)`
    # from THIS object, in THIS request, before anything re-queries it.
    # Without this line `placeholder.queue_job_id` stays `None` for the
    # rest of the request, `turn_group_cards` would take the "no job"
    # branch, and the 202 body's own card would carry no `poll_block`
    # marker for the poller's very first swap to key on.
    placeholder.queue_job_id = job_id

    status = _job_facts(job_id)
    return TurnStart(True, 202, notes=notes, turn=placeholder, job_id=job_id,
                     position=getattr(status, "position", None),
                     priority=getattr(status, "priority", None),
                     attachments=upload_result)


def composer_attach_context(principal, *, workstream=None, workstream_id=None,
                            settings_row=None, tool_access=None, may_upload=None) -> dict:
    """`may_attach_files`/`attach_workstream`: the two context keys
    every composer surface needs to gate its own attach door (round
    18).

    THE ONE IMPLEMENTATION, for all THREE surfaces (audit 2, F5). The
    two START surfaces (`agents.chat.views.conversations._index_context`,
    `agents.chat.views.workstreams._working_context`) have no
    `Conversation.workstream_id` to read, so `workstream` -- the stream
    a new conversation would be born into, or `None` for a loose one --
    stands in for it. The CONVERSATION surface
    (`agents.chat.views.thread.thread_context`) used to compute this
    predicate a second time by hand; it calls this now, threading the
    `WorkstreamScope` answer and the `ToolAccess` it already holds
    through `may_upload=`/`tool_access=` below. This docstring used to
    record the opposite -- that sharing would mean "threading an
    already-resolved scope through two call shapes that share no
    caller" -- and `may_upload=` (round-18-confirm R7) is precisely the
    threading that retired it.

    STILL ONE GATE PER LAYER. Sharing the render-time predicate changes
    nothing about the write path: `agents.contracts.attachments.
    placement_is_valid` and `start_turn` each keep their own defensive
    call, for the reason that function's own docstring gives. This is
    the render-time half, and there is one of it.

    `workstream_id`, OPTIONAL: the stream's IDENTITY, when the caller
    has it more cheaply than the row. Only `attach_workstream` ever
    needs the ROW, and only when `may_upload_here` is true -- so a
    caller holding a `Conversation` whose `workstream` FK is not
    `select_related` can pass the id here (free, it is a local column)
    and pass `workstream=` only on the branch that will actually return
    it, instead of dereferencing an FK this render would otherwise
    never touch.

    DERIVED FROM `workstream` WHEN OMITTED, so passing the row alone is
    not a contradiction at all -- the body fills the id in off `.pk` and
    the result is identical to passing both. THE COMBINATION TO AVOID is
    the opposite one: `workstream=None` with a NON-None `workstream_id`
    and `may_upload=True`. That says "there is a stream, and this
    principal may upload into it, and here is no stream" -- the door
    opens (`may_attach_files` is true, since `may_upload_here` satisfies
    the gate) while `attach_workstream` is `None`, so the composer would
    render an attach door naming no scope at all. No caller makes it, and
    the reason is structural rather than vigilance: `thread_context` ties
    BOTH the row and `may_upload=` to its one `may_upload_here`
    predicate, so the row is withheld on exactly the branch where
    `may_upload` is False and the door is shut anyway.

    `tool_access`, OPTIONAL: a caller that already resolved a `ToolAccess`
    for this SAME principal/render for some other reason (`agents.chat.
    views.workstreams._working_context`'s own `may_upload_tool`, owner-
    only) passes it through rather than paying `tool_access_for`'s query
    twice on one render -- the identical threading `agents.chat.views.
    thread.thread_context` already does for `preflight_turn`.

    `may_upload`, OPTIONAL (ROUND-18-CONFIRM R7): a caller that already
    knows the answer skips `workstream_scope`'s own fresh resolution
    (a `visible_workstreams` filter plus a wall read) entirely --
    NOT a threaded-scope shortcut this function's own "every layer
    keeps its own defensive call" reasoning above would forbid, because
    `may_upload` and `may_manage_workstream` are the SAME predicate
    under two names (`agents.workstreams._scope_from_row`: `may_upload=
    may_manage_workstream(principal, row)`) -- `agents.chat.views.
    workstreams._working_context` already computed exactly that
    predicate, as `is_owner`, via `stream_access`'s own owner branch
    (`agents/workstreams.py::stream_access`: `is_owner` is `True` iff
    `may_manage_workstream` is), before this function is ever called.
    Passing it through is not trusting a DIFFERENT layer's math; it is
    recognising the SAME layer already did this exact one.
    """
    rag_tool_access = (tool_access if tool_access is not None
                       else tool_access_for(principal, settings_row=settings_row))
    # IDENTITY GATES THE DOOR; THE ROW IS ONLY EVER RETURNED. Every
    # question below is answered from `workstream_id`, so a caller that
    # withheld the row on the branch where it is not returned still
    # gets the same two values.
    if workstream_id is None and workstream is not None:
        workstream_id = workstream.pk
    may_upload_here = False
    if workstream_id is not None:
        if may_upload is not None:
            may_upload_here = may_upload
        else:
            scope = workstream_scope(principal, workstream_id)
            may_upload_here = bool(scope is not None and scope.may_upload)
    may_attach_files = rag_tool_access.allows(_RAG_INGEST_TOOL_KEY) and (
        workstream_id is None or may_upload_here)
    return {
        "may_attach_files": may_attach_files,
        "attach_workstream": workstream if may_upload_here else None,
    }


def visible_conversation_or_404(principal, conversation_id):
    """The conversation this principal may READ, or a 404 -- the first
    half of every row-addressed chat view, written once (audit 2, S21,
    for the six copies across four modules).

    THE SECOND HALF -- may they CHANGE it -- is the visibility
    function's own answer (`rename_conversation` and friends return
    False/None), which each view turns into the same 404. Both answer
    404 rather than 403: a 403 on a row-addressed URL confirms the row
    exists.

    Takes a principal, not a request: every caller already holds one for
    that second gate.

    ASKS `chat_surface_conversations`, UNCONDITIONALLY (owner ruling 2,
    spec §7.1.1). All nine of this function's call sites are `/chat/`
    views -- which is what this function's own name and the paragraph
    above already say it is -- so all nine answer 404 on a
    settings-surface conversation: render, post, attachment_detach,
    rename, duplicate, pin, unpin, archive, unarchive, delete and SHARE
    (`turns.py`'s two call sites are post and attachment_detach; pin/
    unpin and archive/unarchive each share one `conversations.py` call
    site through a shared helper, which is how nine sites cover eleven
    actions). Lists make that assistant invisible on `/chat/`; only this
    makes it UNREACHABLE, and unreachable is what "settings-only
    surface" claims. The share action
    is the one that matters most: it lives on the page this now 404s, and
    a shared assistant conversation is a deferral whose door is shut
    rather than merely unwalked.

    The settings panel does NOT use this helper -- it reads through
    `visible_conversations(principal).filter(agent__slug=...)`, the
    un-narrowed gate -- so there is no escape-hatch keyword here and
    nothing needs one.

    THE WORKSTREAM RIDES ALONG (context meter, 2026-09-21).
    `agents.visibility.visible_conversations` `select_related`s the
    AGENT only, and `agents.chat.views.thread.thread_context`
    dereferences `conversation.workstream` on its `may_upload_here`
    branch alone -- its own comment says so. So every reader who may not
    upload (a `view`-share recipient; a stream recipient who may not
    manage it) would pay a LAZY FK READ the moment
    `agents.usage.context_usage` asked for the stream's instructions.
    One more LEFT JOIN on a query this path already runs, scoped to the
    row-addressed `/chat/` views and to nothing else -- the same fix
    review R2 made for `visible_turn`'s poll path, applied here. The
    LIST pages are untouched.
    """
    return get_object_or_404(
        chat_surface_conversations(principal).select_related("workstream"),
        pk=conversation_id,
    )


def composer_attachment_fields(request) -> dict:
    """The three fields `chat/_composer.html`'s attach door posts, keyed
    by `start_turn`'s OWN parameter names so a caller splats it through.

    ONE READER FOR THE NAMES ONE FRAGMENT WRITES (audit 2, F6): both
    surfaces that receive that composer's POST read them here, so a
    renamed input cannot keep working on one and silently stop carrying
    files on the other. In this module for the reason `flash_attachment_
    outcomes` is -- the shared leaf both view modules may import.

    RAW FIELDS ONLY, never a gate: `start_turn` decides whether this
    principal may attach and resolves the placement set.
    """
    return {
        "files": request.FILES.getlist("files"),
        "placement": request.POST.get("placement", ""),
        "remember_placement": bool(request.POST.get("remember_placement")),
    }


def flash_attachment_outcomes(request, result) -> None:
    """Translate `agents.contracts.attachments.AttachmentUploadResult`
    into Django flash messages (round 13) -- the SAME wording `tools.
    rag.views.document_upload`'s own pre-round-13 chat-door loop used to
    write directly. PROMOTED HERE FROM `agents.chat.views.turns` (round
    18): `agents.chat.views.conversations.conversation_start` needs the
    identical translation now that a start carrying files goes through
    this SAME `start_turn` -- and `conversations.py` may not import
    `turns.py` (`agents/chat/views/__init__.py`'s own one-way import
    direction) -- so this moves to the shared leaf both may import,
    exactly the reason `validated_next_url` made the identical move in
    ROUND-13 REVIEW FIX, MINOR 2.

    `result` is `None` for a turn that carried no files at all (`start.
    attachments`'s own default) -- a no-op, not a "nothing happened"
    message; a text-only turn has nothing to report here.
    """
    if result is None:
        return
    if result.queued:
        messages.info(request, f"Queued {result.queued} file(s) — track them on the Queue page.")
    if result.unchanged:
        messages.info(
            request,
            f"{len(result.unchanged)} file(s) already in the library and unchanged — "
            "use Re-ingest to process them again.",
        )
    for sentence in attachment_problem_sentences(result):
        messages.error(request, sentence)
    for name in result.tabular:
        messages.info(request, f"{name} stored as a table; not searchable yet.")


def attachment_problem_sentences(result) -> list[str]:
    """The REFUSAL half of `flash_attachment_outcomes`'s own wording --
    `rejected`/`failed`, never `queued`/`unchanged`/`tabular` -- as a
    plain list, so a JSON body and a flash-message loop both say
    EXACTLY the same sentence for the same outcome (ROUND-13 REVIEW
    FIX, MINOR 2; promoted here alongside `flash_attachment_outcomes`
    in round 18, same reason). `result` may be `None` (a turn that
    carried no files at all) -- `[]`, the same "nothing to report"
    answer `flash_attachment_outcomes` gives that case.
    """
    if result is None:
        return []
    sentences = []
    if result.rejected:
        sentences.append(f"Skipped unsupported file(s): {', '.join(result.rejected)}.")
    if result.failed:
        sentences.append(
            f"Couldn't queue {', '.join(result.failed)} for ingest — try uploading again."
        )
    return sentences


def _cleanup_any_staged_documents(upload_result: AttachmentUploadResult | None) -> None:
    """I-2 (round-13 review fix): the one call site all three post-
    staging failure branches above share. `upload_result` is `None` for
    a turn that carried no files at all (`files` was empty, `stage_turn_
    attachments` never ran) -- `cleanup_staged_documents(())` is
    already a no-op for that case, so this wrapper exists only to make
    each `except` clause read as one line rather than three."""
    cleanup_staged_documents(upload_result.staged_document_ids if upload_result else ())


def _title_if_unset(conversation, message: str) -> None:
    """The title, from the first ~60 characters of the first user turn.

    NEVER generated by a model: that would be a second, invisible model
    call per conversation (spec section 7.2).
    """
    if not conversation.title:
        conversation.title = truncate_title(message)
        conversation.save(update_fields=["title"])


def truncate_title(text: str, limit: int = TITLE_MAX) -> str:
    """`text`, cut to at most `limit` characters on a WORD boundary,
    with a trailing "…" when anything was actually cut (chat-polish
    P3.1, U5).

    The conversation LIST (`chat/index.html`) and the thread page's own
    `<title>` (`chat/conversation.html`) both render `Conversation.
    title` verbatim, so a hard `text[:limit]` cut -- this function's
    predecessor -- reached both of them mid-word and with no mark that
    anything was missing ("...searching the libr", "...on a"). Falls
    back to the hard cut when the window holds no whitespace at all (one
    very long "word") rather than returning an empty string.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind(" ")
    if boundary > 0:
        cut = cut[:boundary]
    return cut.rstrip() + "…"


def fit_title(text: str, *, prefix: str = "") -> str:
    """`prefix` + `text`, cut so the result fits the `Conversation.title`
    COLUMN -- the one function UI-3b's rename and duplicate both call.

    IT SPENDS ONE CHARACTER OF THE BUDGET ON THE ELLIPSIS, which
    `truncate_title` itself does not: that function cuts to `limit` and
    THEN appends "…", so its result can be `limit + 1` characters long.
    That has never mattered to its other caller (`TITLE_MAX` is 60
    against a 255-wide column), but a caller truncating TO the column
    would hand the database 256 characters and get a `DataError` on
    save. Hence `TITLE_COLUMN_MAX - 1`, here, once, rather than at each
    call site -- and `truncate_title`'s own contract (and its two pins
    in `agents/chat/tests/test_turn_create.py`) left alone.

    THE PREFIX IS NEVER WHAT GETS CUT. Truncating "Copy of <a very long
    title with no spaces in it>" as one string finds its only word
    boundary immediately after "Copy of" and yields "Copy of…" -- a
    title that says nothing about what was copied. The budget belongs
    to the part that carries the meaning.
    """
    return f"{prefix}{truncate_title(text, TITLE_COLUMN_MAX - 1 - len(prefix))}".strip()


def conversation_url(conversation, *, connection: str = "", pending=None) -> str:
    """THE ONE thread-URL builder.

    `conversations.py` (after a start) and `turns.py` (after a no-JS
    POST) both need "the thread, carrying the pick, and possibly a
    turn to watch", and two builders would be two chances to drop one
    of the two keys. Lives here rather than in either view module
    because both already import this one, and `thread.py` must stay
    importable without either (M1's one-way direction).

    The pick lives in the QUERY STRING, not a session and not a column
    (deviation P3-D7) -- `tools/vision/views.py::_create_url`'s own
    rule: a link is then a complete description of what the page will
    show, so the chooser, the redirect after a submission, and a
    bookmark all agree.
    """
    from urllib.parse import urlencode

    url = reverse("chat-conversation", args=[conversation.id])
    query = {k: v for k, v in (("connection", connection),
                               ("pending", pending)) if v}
    return f"{url}?{urlencode(query)}" if query else url


def validated_next_url(request) -> str | None:
    """The `next` POST field, honoured only when it is a same-origin,
    same-scheme URL -- or `None`.

    MOVED HERE from `views/conversations.py` (ROUND-13 REVIEW FIX,
    MINOR 2): `views/turns.py::attachment_detach` needed the identical
    guard (the review's own finding -- a detach POSTed from `/chat/
    all/`'s own preview pane had no way to return there, only to
    `conversation_url(conversation)`, "bounc[ing] the operator into the
    conversation and los[ing] their search/filter/page state"), and
    `turns.py` may not import `conversations.py` (the package's import
    direction is one-way -- `views/__init__.py`'s own docstring). Lives
    here, the one module both already import, for the identical reason
    `conversation_url` above does.

    THE SIDEBAR/BROWSER IS ON MORE THAN ONE SURFACE, so "where does this
    action land" has more than one right answer: rename from the thread
    you are reading should leave you reading it, rename from another
    thread's sidebar entry should not teleport you into that thread, and
    a detach from `/chat/all/`'s own preview pane should return there,
    filters and all. The form carries the page it was posted from and
    the view returns there, which is the only way to get all of that
    without guessing.

    `url_has_allowed_host_and_scheme` is the same guard
    `tools/vision/views.py::_validated_next_url` puts on its own delete
    forms, and for the same reason: a `next` value is caller-supplied,
    and an unchecked one is an open redirect off this box. `None` (not
    a fallback URL) when there is no usable value -- each caller picks
    its own default, because they do not all share one.
    """
    next_url = request.POST.get("next", "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return None


def validated_next_link(request, next_url: str) -> str | None:
    """A caller-supplied `next`, validated for use as an HREF -- or
    `None`.

    THE SAME GUARD AS `validated_next_url` ABOVE, PLUS ONE, AND FOR A
    DIFFERENT MOMENT. That function reads `request.POST` and answers
    where a completed write should LAND; this one takes a raw value the
    page is about to RENDER as a link, which on this surface arrives on
    a GET (`?next=`) and on a refused POST alike. The two exist
    separately rather than one taking a dictionary, because "which
    dictionary" is not the difference that matters -- the difference is
    that a POST value has already been through a CSRF-protected form
    this box rendered, and a GET value is whatever was in the address
    bar.

    THE EXTRA CHECK IS THAT IT IS A PATH ON THIS BOX. `url_has_allowed_
    host_and_scheme` already refuses another origin, a scheme-relative
    `//host`, and a `javascript:` URL -- but it ADMITS a fully-qualified
    `https://this-host/...`, which is a correct answer for a redirect
    and a needlessly wide one for an href. A link this page writes goes
    to a path on this box or it does not exist, so that is what is
    required, and the narrowing is here rather than in
    `validated_next_url` precisely so no existing redirect's behaviour
    moves.

    `None` -- never a fallback URL -- when there is no usable value, the
    same contract `validated_next_url` states: each caller picks its own
    default, because they do not all share one.
    """
    next_url = (next_url or "").strip()
    if not next_url.startswith("/") or next_url.startswith("//"):
        return None
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return None
    return next_url


# --- the two access pages' shared transfer-panel plumbing (phase 2) --------
#
# `/chat/tools/` and `/chat/access/` are "the same page written twice"
# (`parse_entitlement_diff`'s own ancestor said so), and phase 2 gave
# them a third thing in common: each resource row is a collapsed
# `<details>` whose summary states what the row carries, expanding into
# the shared transfer panel. The summary sentence, the change sentence
# and the return URL are declared ONCE here rather than in two views and
# two templates.

SUMMARY_LEAD_NAMES = 3

# THE EMPTY-CATALOGUE SENTENCE, declared once here rather than typed into
# both access-page templates (the house rule: user-facing sentences live
# in Python). It is the string those two pages already shipped, moved
# verbatim -- a box with no entitlements at all has nothing to label
# with, and the only useful thing to say is where to make one.
NO_ENTITLEMENTS_COPY = "No entitlements exist yet \u2014 create one on the Entitlements page."


def entitlement_summary(total: int, names) -> str:
    """The collapsed row's own line: the count first, then the leading
    names, then how many are not shown.

    THE COUNT IS `total`, NOT `len(names)`, and the difference is the
    honest case: `names` comes from `labelling_entitlements`, which
    offers an administrator everything and an entitlement owner only
    what they own. Both access pages are class S, so every caller who
    reaches them is an administrator and the two are equal in practice
    -- but a summary that silently counted only the nameable ones would
    be the kind of undercount nobody notices until the class loosens.

    Read with no labels at all, the row says so in the page's own
    vocabulary: an unlabelled tool or agent is available to everybody
    signed in, which is exactly what an operator scanning the list needs
    to see without opening anything.
    """
    shown = list(names)[:SUMMARY_LEAD_NAMES]
    if not total:
        return "No entitlements — everyone signed in"
    rest = total - len(shown)
    line = ", ".join(shown)
    if rest > 0:
        line = f"{line} +{rest} more" if line else f"{rest} not shown"
    plural = "" if total == 1 else "s"
    return f"{total} entitlement{plural}: {line}"


def entitlement_change_flash(label: str, before, after) -> str:
    """What a save really changed, not what it was sent.

    The DIFFERENCE, for the same reason `agents/labels.py::_set_labels`
    writes the difference: an operator reads this line to learn what
    happened, and a count of submissions would say "1 added" for a box
    that was already ticked.
    """
    added = len(set(after) - set(before))
    removed = len(set(before) - set(after))
    return f"{label}: {added} added, {removed} removed."


def entitlement_row_url(request, route_name: str, *, row_anchor: str) -> str:
    """Where a per-row entitlement save returns to: the page, with this
    row re-opened, scrolled to it, and the assistant panel still open if
    it was.

    `?open=<anchor>` IS THE ROW'S OPEN STATE, and it is a query
    parameter for the same reason `?assistant=1` is one: no script, no
    cookie, nothing stored on the client. A `<details>` collapses on
    every reload, so a save without this would land the operator on an
    anchor pointing at a closed row -- half a return, on the page whose
    whole purpose is a run of edits.

    `preserve_assistant_flag` MUST STAY THE THING THAT APPENDS THE FLAG,
    and that is the whole of what is load-bearing here (fix round 2,
    P2-M1). It is fragment-aware -- it splits the URL and rebuilds it --
    so the flag lands in the QUERY however this function orders its own
    construction; building the fragment before or after it produces the
    same bytes, which was worth checking rather than asserting. What
    does NOT survive is anybody replacing it with a concatenation: `url
    + "&assistant=1"` on a URL that already carries a `#` buries the
    parameter inside the fragment, where `request.GET` never sees it and
    the panel shuts on every save.

    The route matrix cannot pin that, because its drivers for both
    access pages are GETs and the only POST its sweep makes is an empty
    body that redirects to the page top -- it never sees a `?open=` URL
    at all. `agents/chat/tests/test_tool_entitlements_page.py::
    test_the_save_redirect_composes_open_with_the_assistant_flag`
    asserts the whole `Location` byte for byte instead, and goes red on
    exactly the concatenation above.
    """
    url = f"{reverse(route_name)}?{urlencode({'open': row_anchor})}#{row_anchor}"
    return preserve_assistant_flag(request, url)


def parse_entitlement_diff(request, principal, settings_row, *, redirect_url: str):
    """The transfer panel's `op` plus its `add`/`remove` list, validated
    against what this principal may LABEL WITH -- `(op, set[int])` on
    success, or an `HttpResponse` the caller returns straight through.

    THE ADD/REMOVE TWIN of the whole-set `entitlements` field this
    replaced, and the reasons are phase 1's, unchanged: a submitted
    whole set clobbers (two administrators editing different rows of the
    same resource each ship the other's stale pane back), and one form
    spans both panes, so a submit carries every ticked box in BOTH --
    honouring only the side the pressed button names is what stops a
    stray tick in the other pane from acting.

    THE SAME PREDICATE THE FORM RENDERS FROM
    (`identity.access.labelling_entitlements`), so a stale form or a
    hand-made request gets the identical honest refusal either page
    would give. What differs between the two callers is everything AFTER
    this point -- which model gets labelled, and how -- which is why this
    stops here and returns the validated pair rather than doing the
    write.

    `redirect_url`: the fully-built URL each caller's refusal returns to,
    already carrying that row's own anchor and the assistant flag
    (`entitlement_row_url` above). A refusal lands the operator back on
    the row they were editing, exactly as a success does.
    """
    op = request.POST.get("op", "")
    if op not in ("add", "remove"):
        messages.error(request, f"{op!r} is not a recognised operation.")
        return redirect(redirect_url)
    raw = request.POST.getlist(op)
    if any(not value.isdecimal() for value in raw):
        messages.error(request, "Entitlements are chosen from the list, not typed.")
        return redirect(redirect_url)
    wanted = {int(value) for value in raw}
    offered = {pk for pk, _name in labelling_entitlements(principal, settings_row=settings_row)}
    if not wanted <= offered:
        # THE SAME PREDICATE THE FORM RENDERS FROM. A POST naming an
        # entitlement this page never offered is either a stale form or a
        # hand-made request, and both get the same honest refusal.
        messages.error(request, "That entitlement is not one you may label with.")
        return redirect(redirect_url)
    return op, wanted


def entitlement_panes(choices, held):
    """One resource's two panes: the entitlements it does NOT carry, and
    the ones it does, both in the offered order (`labelling_entitlements`
    sorts by name).

    PLAIN DICTS, not a typed row: `foundation/templates/
    _transfer_panel.html` reads `row.id` / `row.name` / `row.note`, and
    Django resolves those off a dict. A type imported from another
    column to carry two strings across a template boundary would be a
    dependency for nothing.
    """
    available = [{"id": pk, "name": name} for pk, name in choices if pk not in held]
    active = [{"id": pk, "name": name} for pk, name in choices if pk in held]
    return available, active


def _job_facts(job_id):
    """The queue's own view of the job just enqueued, or `None`.

    A position we cannot read is a nicety; the turn is queued either
    way, and a 503 for a failed status read would throw away work the
    queue has already accepted -- `tools/vision/views.py::_queue_status`
    makes the same call for the same reason.
    """
    try:
        return get_job(job_id)
    except Exception:  # noqa: BLE001 -- the enqueue already succeeded
        logger.info("chat: could not read queue job %r right after enqueuing it", job_id)
        return None
