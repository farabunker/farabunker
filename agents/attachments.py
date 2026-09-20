"""Conversation-attached documents, read through the sanctioned
cross-column seam (`agents.contracts.attachments`) -- never a direct
`tools.rag` import (import-law rule 3).

`attached_documents` HAS TWO QUESTIONS ASKED OF IT, NOT A CALLER COUNT
WORTH NAMING HERE (a structural claim, per the consolidation wave's own
counting-prose fix, R2 -- and the fix this replaces ALSO named a count,
"FOUR CALLERS," which was already wrong the moment this paragraph
landed: the wave's own F3/S9 consolidation, in the SAME commit, cut it
to two): `agents.runtime.prompt.build_messages` (the prompt steering
block) reads its flat, unfiltered return directly; `attachments_for`,
below, is the ONE place that groups it by turn, and every chat surface
that needs THAT grouping (`agents.chat.views.thread.thread_context`,
`agents.chat.views.turns._attachments_by_turn`, `agents.chat.views.
all_conversations._preview_cards`) calls `attachments_for` rather than
re-deriving the grouping itself. Two questions, two functions, every
caller reading from ONE provider call either way.

ROUND 13 (message-bound attachments) ADDS TWO WRITE-SHAPED SEAMS ON THE
SAME TERMS: `stage_turn_attachments` (`agents.chat.service.start_turn`'s
one caller) and `detach_attachment` (`agents.chat.views.turns.
attachment_detach`'s one caller). Both are still resolved by dotted
path, still never raise past this module's own boundary, and still
degrade to an honest refusal rather than an unhandled exception -- the
identical posture the two READ seams above already take, extended to a
WRITE: a broken `tools.rag` uploader/detacher must not 500 a turn
submission or a chip's remove button, it must say so.
"""
from __future__ import annotations

import logging
import os

from django.db import transaction
from django.utils.module_loading import import_string

from agents.contracts.artifacts import file_resolver_for
from agents.contracts.attachments import (
    AttachmentUploadResult, DetachOutcome, attachment_cleanup, attachment_detacher,
    attachment_orphan_cleanup, attachment_provider, attachment_text_provider,
    attachment_uploader,
)

logger = logging.getLogger(__name__)


def attached_documents(principal, conversation, *, stream=None,
                       settings_row=None) -> list[dict]:
    """Every document `tools.rag` has on file as attached to
    `conversation` AND actually reachable through this principal's own
    corpus for THIS conversation -- `[]` when the conversation has none,
    when nothing is registered (`tools.rag` not installed), or when the
    registered provider itself raises.

    `stream`, OPTIONAL and keyword-only (round 11 review I-3): the
    conversation's OWN `agents.contracts.workstreams.WorkstreamScope`
    (`agents.workstreams.workstream_scope`), or `None` for a loose
    conversation -- THREADED IN by the caller, never derived here, so a
    value computed once per render/turn for another purpose (`agents.
    chat.views.thread.thread_context`'s own attach-door scope; `agents.
    runtime.loop._run_turn`'s own tool-access scope) is reused rather
    than re-queried. Passed straight through to the provider, which
    resolves it against the SAME corpus formula retrieval itself narrows
    a stream turn by (`tools.rag.workstreams.stream_documents`, the wall
    included) -- so this function never names a file this conversation's
    own `rag__search`/`rag__ask` could not actually return, on EITHER
    surface that calls it (the strip and the prompt block alike).

    NEVER RAISES (the SAME "one broken provider, not a broken page"
    posture `agents.workstreams.panels_for` already takes for its own
    registry, author decision 11's reasoning applied a second time): a
    `tools.rag` import failure, or a provider that raises at call time
    -- a migration half-applied, a store that is down -- must not take
    the conversation page, or a running turn's own prompt, down with
    it. A failed lookup answers "no attachments" rather than a 500 or
    an unhandled exception mid-turn.

    `settings_row`, OPTIONAL (round 11 review, minor 5): a caller that
    already holds one `IdentitySettings` read for its own render/turn
    threads it in, so the provider does not pay a second read deriving
    the identical visibility facts. `None` -- the default -- lets the
    provider derive it itself, exactly as before this parameter existed.
    """
    provider = attachment_provider()
    if provider is None:
        return []
    try:
        return import_string(provider)(
            principal, conversation_id=conversation.id, stream=stream,
            settings_row=settings_row,
        )
    except Exception:  # noqa: BLE001 -- one broken provider, not a broken page/turn
        logger.exception("attachments: provider %r could not be resolved", provider)
        return []


def attachments_for(principal, conversation, *, stream=None, settings_row=None) -> dict:
    """`{turn_id: [row, ...]}` -- `attached_documents` above, GROUPED BY
    TURN, in the one place every chat surface that renders per-turn
    attachment chips now calls into, rather than three call sites each
    re-running the identical four lines (CONSOLIDATION WAVE: audit A's
    finding F3, audit B's S9 -- the same duplication, named by both
    readers).

    BEFORE THIS FUNCTION EXISTED, `agents.chat.views.thread.
    thread_context`, `agents.chat.views.turns._attachments_by_turn` and
    `agents.chat.views.all_conversations._preview_cards` each resolved
    `workstream_scope(...) if conversation.workstream_id else None`,
    called `attached_documents`, and built a `defaultdict(list)` keyed
    on `row.get("turn_id")` -- the identical four steps, independently.
    `turns.py`'s own FRESHNESS requirement (the poller must re-query so
    a chip's status is live across ticks) is untouched: it is about
    WHEN this function is called, never about whose body runs when it
    is, so `turns.py` keeps calling this on every tick, fresh, exactly
    as it always has.

    THE LEGACY `None`-KEYED BUCKET THIS USED TO FEED IS GONE (the
    consolidation wave's own S10 ruling: the whole `chat/_legacy_
    attachments.html` renderer was removed as production-unreachable --
    no migration on `origin/main` has ever let a `DocumentAttachment`
    row with a `NULL turn_id` exist outside this branch's own dev/
    preview history). A row with no `turn_id` still groups under the
    key `None` here -- nothing filters it out, there is simply no
    reader left that asks for that key -- so this function's own
    contract did not need to change shape to drop that bucket; the
    callers that used to extract it just stopped.

    `stream`/`settings_row`: threaded straight through to `attached_
    documents`, unchanged -- see that function's own docstring for both.
    """
    from collections import defaultdict

    attachments = attached_documents(principal, conversation, stream=stream,
                                     settings_row=settings_row)
    by_turn: dict = defaultdict(list)
    for row in attachments:
        by_turn[row.get("turn_id")].append(row)
    return by_turn


def delete_attachments_for(conversation_id) -> int:
    """Remove every `DocumentAttachment` row naming `conversation_id`
    (round 11 re-review minor 4) -- the delete-time twin of `attached_
    documents` above, resolved through `agents.contracts.attachments.
    attachment_cleanup` the identical way. Returns the count removed,
    or `0` when nothing is registered or the registered cleanup
    provider itself raises.

    THE ONE CALLER: `agents.visibility.delete_conversation`, inside the
    SAME transaction the conversation's own `Share` rows and the
    conversation itself are deleted in -- so a `DocumentAttachment` row
    naming a UUID that no `Conversation` will ever match again (the
    column is a UUID BY VALUE, never a real FK -- `tools/rag` may not
    import `agents.models`) does not silently outlive the conversation
    it claimed to be attached to.

    NEVER RAISES, the identical posture `attached_documents` above
    takes and for the identical reason: a broken `tools.rag` cleanup
    provider must not block a conversation delete the actor already
    confirmed through `may_manage_conversation`. The `Document` rows
    themselves are never this function's concern -- only the CLAIM a
    conversation made on them (round 12: EXCEPT for a conversation-
    scoped document, whose sole attachment row is itself the whole of
    its existence -- the registered provider deletes the Document too
    in that case; see `tools.rag.access.delete_attachments`'s own
    docstring).

    THE SAVEPOINT (round 11 fix-2 verify, Important N-1): this call
    ALWAYS runs inside `agents.visibility.delete_conversation`'s own
    outer `transaction.atomic()` block. A Python exception from the
    provider is caught below regardless -- but a DATABASE-level error
    (the reviewer reproduced `InternalError: current transaction is
    aborted`) does something the `except` clause alone cannot undo:
    Postgres poisons the WHOLE connection for the rest of the
    surrounding transaction, so `conversation.delete()` itself -- run
    AFTER this call returns, still inside the same outer atomic block
    -- would fail too, even though this function's own contract is
    "never raises". `transaction.atomic()`, NESTED (as it is here,
    inside the caller's own outer one) becomes a SAVEPOINT: on an
    exception it rolls back to just before this call and re-raises,
    which is what lets the `except` below catch a now-ordinary Python
    exception with the OUTER connection already restored to a healthy
    state. Round 12's own document-cascade delete (`tools.rag.access.
    delete_attachments`, called through `provider` below) inherits this
    same discipline for the ROWS it touches -- a failure partway
    through deleting several chat-scoped documents rolls their
    `Document`/`DocumentAttachment` ROWS back together, never a partial
    row-level cleanup. NOT TRUE FOR THE FILES (round 12 review, minor
    2): `tools.rag.services.delete_document`'s own docstring records
    why its managed-store FILE removal is not, and cannot be, covered
    by this or any database savepoint -- a filesystem delete has no
    rollback. A rolled-back row can therefore, in the narrow window this
    savepoint exists for, reappear pointing at files already gone.
    """
    provider = attachment_cleanup()
    if provider is None:
        return 0
    try:
        with transaction.atomic():
            return import_string(provider)(conversation_id)
    except Exception:  # noqa: BLE001 -- one broken provider, not a broken delete
        logger.exception("attachments: cleanup provider %r could not be resolved", provider)
        return 0


def stage_turn_attachments(principal, *, conversation_id, turn_id, files, workstream_id=None,
                           placement: str = "", remember_placement: bool = False,
                           actor) -> AttachmentUploadResult:
    """Stage every file in `files`, scope/contain them per `placement`,
    and bind each to BOTH `conversation_id` and `turn_id` (round 13,
    requirement B) -- resolved through `attachment_uploader()`, the ONE
    caller `agents.chat.service.start_turn` has, inside the SAME
    transaction that writes the user's own `Turn` row.

    NEVER RAISES PAST THIS BOUNDARY -- unlike `attached_documents`
    above, a plumbing failure here does NOT degrade to "as if nothing
    was attached": that would silently drop files the operator just
    submitted with a message, the exact "processing but nobody said so"
    complaint round 13 exists to fix. A resolution or provider failure
    instead comes back as `AttachmentUploadResult(ok=False, status=503,
    error=...)`, which `start_turn` turns into the SAME 503 shape a
    queue outage already gets (`QUEUE_UNAVAILABLE`'s own sentence, one
    field over).
    """
    provider = attachment_uploader()
    if provider is None:
        return AttachmentUploadResult(
            ok=False, status=503,
            error="File attachments aren't available on this box right now.",
        )
    try:
        return import_string(provider)(
            principal, conversation_id=conversation_id, turn_id=turn_id, files=files,
            workstream_id=workstream_id, placement=placement,
            remember_placement=remember_placement, actor=actor,
        )
    except Exception:  # noqa: BLE001 -- one broken provider, not a broken turn
        logger.exception("attachments: uploader %r could not be resolved", provider)
        return AttachmentUploadResult(
            ok=False, status=503, error="Couldn't attach your files — try sending again.",
        )


def detach_attachment(principal, *, conversation_id, doc_id) -> DetachOutcome:
    """Remove one attachment (round 13, requirement E's post-send
    remove) -- resolved through `attachment_detacher()`, the ONE caller
    `agents.chat.views.turns.attachment_detach` has.

    NEVER RAISES PAST THIS BOUNDARY, the same posture `stage_turn_
    attachments` just above takes and for the identical reason: a
    plumbing failure here must read as an honest refusal (404, "nothing
    to remove"), never an unhandled exception behind a chip's own ✕
    button.
    """
    provider = attachment_detacher()
    if provider is None:
        return DetachOutcome(False, 404, "Attachments are not available on this box.")
    try:
        return import_string(provider)(
            principal, conversation_id=conversation_id, doc_id=doc_id,
        )
    except Exception:  # noqa: BLE001 -- one broken provider, not a broken remove
        logger.exception("attachments: detacher %r could not be resolved", provider)
        return DetachOutcome(False, 404, "Couldn't remove that attachment — try again.")


def cleanup_staged_documents(document_ids) -> None:
    """Delete the managed-store BYTES for `document_ids` (round-13
    REVIEW FIX, I-2) -- the ONE caller `agents.chat.service.start_turn`
    has, in its own failure path AFTER `stage_turn_attachments` (above)
    already returned `AttachmentUploadResult.staged_document_ids` for a
    turn whose SURROUNDING transaction then rolled back for an
    unrelated reason (a queue outage, an index collision). The DB rows
    those ids named are already gone by the time this runs -- Django's
    own rollback -- but `tools.rag.store.move_file`'s physical move
    is not a database write and no savepoint undid it; this is what
    removes those orphaned bytes rather than leaving them on disk with
    no row left to find them by.

    `()`/empty input is a plain no-op -- `start_turn` calls this
    unconditionally in each `except` branch, so a turn that carried no
    files (or one whose staging moved nothing new) costs nothing beyond
    the empty check.

    NEVER RAISES PAST THIS BOUNDARY, the identical posture `stage_turn_
    attachments`/`detach_attachment` above take: a broken cleanup
    provider must not turn an ALREADY-FAILED turn (a queue outage, say)
    into an unhandled exception on top of the 503 it was already about
    to answer with. Best-effort only -- the orphan this call could not
    remove is a known, logged, lower-severity leftover (`tools.rag.
    store.remove_document_files`'s own docstring: a stray directory,
    not a live, readable Document a principal could reach), never a
    reason to fail louder than the turn's own refusal already does.
    """
    if not document_ids:
        return
    provider = attachment_orphan_cleanup()
    if provider is None:
        return
    try:
        import_string(provider)(document_ids)
    except Exception:  # noqa: BLE001 -- one broken provider, not a broken refusal
        logger.exception(
            "attachments: orphan-cleanup provider %r could not be resolved", provider)


def inline_attachment_text(doc_id) -> str:
    """`doc_id`'s own text, extracted MODEL-FREE (OWNER ADDENDUM,
    2026-09-08) -- resolved through `attachment_text_provider()`, the
    ONE caller `agents.runtime.prompt._carrying_attachments_block` has.

    `""` for EVERY failure mode -- no provider registered, the provider
    raised, or the provider itself honestly found nothing extractable
    (a scanned PDF, tabular, media) -- the SAME "one broken provider,
    not a broken turn" posture `attached_documents` above takes,
    deliberately NOT the write-shaped `stage_turn_attachments`/`detach_
    attachment` posture just above (which return a typed refusal a view
    renders): the carrying turn's own prompt build has no user-facing
    error surface for "one attachment's text could not be read" to
    reach at all -- the honest "still processing" line the caller
    renders for an empty string already covers every one of these cases
    identically, so a typed distinction here would have no consumer.
    """
    provider = attachment_text_provider()
    if provider is None:
        return ""
    try:
        return import_string(provider)(doc_id) or ""
    except Exception:  # noqa: BLE001 -- one broken provider, not a broken turn
        logger.exception("attachments: text provider %r could not be resolved", provider)
        return ""


def attachment_image_path(document_id, principal) -> str | None:
    """The on-disk path for `document_id`'s bytes, when -- and only
    when -- this IS an image AND `principal` may actually read it.
    `None` for every other outcome (Task 3, native image input).

    RESOLVED THROUGH THE ARTIFACT-FILE REGISTRY (`agents.contracts.
    artifacts.file_resolver_for("document")`), NOT `agents.contracts.
    attachments` above: `tools.vision.services._stored_document_input`
    already resolves this SAME `document` kind through this SAME
    registry, and applies the identical `media_type.startswith(
    "image/")` decision this function reuses rather than re-derives --
    a row's own `is_image` (the attachment provider's own snapshot) is
    only a PRE-FILTER a caller uses to decide whether to try at all;
    the artifact's live `media_type` is what actually gates a read.

    THE GATE IS THE RESOLVER'S OWN, NOT A SECOND CHECK HERE: a
    registered resolver's contract (`agents.contracts.artifacts.
    ArtifactFile`'s own docstring) is to raise `LookupError` for a
    missing row, a missing file, OR a principal who may not read it --
    one exception for all three, so a caller here cannot tell them
    apart and cannot leak which one occurred. This is what keeps
    another uploader's chat-scoped bytes out of a caller's hands: the
    same `readable_document` call `tools/rag/access.py::artifact_
    file_for` already gates its own file route with.

    NEVER RAISES (this module's own standing posture): no registered
    resolver, `LookupError`, or any other exception the resolver itself
    raises all fold into `None`, and a broken resolver additionally
    logs -- the identical shape `inline_attachment_text` above takes
    for its own provider.
    """
    resolver = file_resolver_for("document")
    if resolver is None:
        return None
    try:
        artifact = import_string(resolver)(document_id, principal)
    except LookupError:
        return None
    except Exception:  # noqa: BLE001 -- one broken resolver, not a broken turn
        logger.exception("attachments: image resolver %r could not be resolved", resolver)
        return None
    media_type = artifact.media_type or ""
    if not media_type.startswith("image/"):
        return None
    if not artifact.path or not os.path.isfile(artifact.path):
        return None
    return artifact.path
