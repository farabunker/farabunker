"""What `agents/` destroys when a deleted conversation's date arrives.

REGISTERED, NEVER CALLED DIRECTLY. `agents/apps.py::ready()` registers
the dotted path; `identity/cascades.py::run_retention` resolves it
inside `identity.retention.purge_ticket`'s transaction, in a savepoint
of its own. `identity/` never imports this module, and this module never
imports `tools/` or `models.queue` -- both are reached by registration,
which is what makes the whole purge one transaction across four columns
that may not import each other.

THE ROWS BAND, not the files band -- because this handler no longer
removes any bytes at all (see `purge_conversation`'s own docstring
below): its artifact slot became a resolver, and a conversation's
generated images purge under tickets of their own now, not a silent
destruction on the conversation's own date. `tools.rag.retention.
purge_conversation_notes` is also registered for `kind=KIND_CONVERSATION`
(`tools/rag/apps.py`), in the FILES band, and this handler runs AHEAD of
it -- the filesystem-last rule `identity.contracts.cascades` states
exists for exactly this pair: rows go first so that if the later,
byte-removing handler fails, this handler's row changes are still
inside the same outer transaction and roll back with it, with nothing
on disk already gone; and if this handler's own database work is what
fails, nothing on disk has been touched either way. What this handler
must still do first, inside its own run, is collect the invocation ids
before it deletes the turns that carry them: `Turn.invocation` is
`SET_NULL`, so once the turns are gone there is no path left from the
conversation to its tool records at all.
"""
from __future__ import annotations

import logging
import uuid

from django.utils.module_loading import import_string

from agents.contracts.artifacts import artifact_children, parse_artifact
from agents.models import Conversation, Share, ToolInvocation, Turn
from identity.contracts.retention import KIND_VISION_JOB

logger = logging.getLogger(__name__)

# The artifact kinds this column hands to the image column. `document`
# is deliberately absent: a `document:<id>` reference names a `Document`
# row, and the attachment seam already reaches those.
_IMAGE_KINDS = ("output", "input")


def _collect(conversation_id):
    """Everything the purge needs to read BEFORE anything is deleted.

    ONE `values_list` over the conversation's turns. Three things come
    out of it, and the order matters for the third:

    * artifact references parsing as `output:<id>` / `input:<id>` --
      each is one FK hop from its generation job, and several outputs
      share one job, so the mapping dedupes by job on the other side;
    * generation ids: `data["id"]` for any turn whose `data` is a dict
      whose `"id"` parses as a UUID. `agents/runtime/loop.py` writes
      `data=outcome.result.data` verbatim onto a tool turn, and for the
      image tool that data IS `job_json(job)`, whose first key is the
      job id -- so this channel names the job EVEN WHEN IT PRODUCED NO
      OUTPUT AT ALL, which is precisely the failed-job case the
      artifact channel cannot see;
    * `invocation_id` values, non-null. Collected HERE because
      `Turn.invocation` is `SET_NULL`: after the turns are deleted there
      is no path from the conversation to its tool records at all. That
      `SET_NULL` is deliberate (the 2026-08-27 addendum's consequence 3
      -- an audit row is not owned by the conversation table) and is not
      being changed.

    An unparseable artifact reference is DROPPED AND LOGGED, never
    raised -- `agents.shares.shared_keys`' own posture for untrusted
    stored strings: a purge must not be stopped by one bad row.

    THE LOG LINE NAMES NO CONTENT: a deletion path must not
    write the very bytes it is destroying into a log a purge is
    supposed to make disappear, so this logs the TURN's own id and the
    conversation id -- enough for an operator to find the row by hand --
    and the fact that its reference did not parse, never the raw stored
    string itself.
    """
    refs: list[str] = []
    generation_ids: list[str] = []
    invocation_ids: list[int] = []
    rows = Turn.objects.filter(conversation_id=conversation_id).values_list(
        "id", "artifacts", "data", "invocation_id")
    for turn_id, artifacts, data, invocation_id in rows:
        for reference in artifacts or ():
            try:
                kind, _pk = parse_artifact(reference)
            except ValueError:
                logger.warning(
                    "agents.retention: turn %s (conversation %s) carries an "
                    "artifact reference that failed to parse; ignored.",
                    turn_id, conversation_id)
                continue
            if kind in _IMAGE_KINDS:
                refs.append(reference)
        if isinstance(data, dict):
            raw = data.get("id")
            if isinstance(raw, str):
                try:
                    uuid.UUID(raw)
                except (ValueError, AttributeError, TypeError):
                    pass
                else:
                    generation_ids.append(raw)
        if invocation_id is not None:
            invocation_ids.append(invocation_id)
    return refs, generation_ids, invocation_ids


def scrub_tool_records(invocation_ids) -> int:
    """Blank the words on these `ToolInvocation` rows, keep the shell.

    `args`, `text` and `error` are content -- the tool arguments, the
    result the model saw, the failure it reported. Everything else stays:
    principal, agent slug, tool key, outcome, timings, `queue_job_id`.
    That shell IS the machine audit trail
    `identity/contracts/actions.py`'s own docstring points at when it
    explains why tool calls are absent from the `AuditEvent` catalogue
    -- "tool calls already have a better record in
    `agents.models.ToolInvocation`... the first must be kept and the
    second must be prunable". Scrubbing the words and keeping the record
    is that sentence, implemented.

    ONE CLIFF, NO SECOND DATE, NO SETTING. This runs inline, inside the
    purge's own transaction, on the date the Deleted page printed. The
    owner asked for an optional longer retention for these records and
    withdrew it (spec section 1 ruling 1, section 11.8); there is
    nothing here that defers.

    Idempotent by construction: a second run matches the same rows and
    writes the same three empty values.

    A KNOWN, ACCEPTED RESIDUE: this only reaches invocations
    a SURVIVING `Turn` points at, because `_collect` above finds them by
    walking the conversation's own turns. A tool call whose job died
    between its `ToolInvocation` row being written (`agents/runtime/
    invoke.py`) and its tool `Turn` being written (`agents/runtime/
    loop.py`) has no turn and therefore no conversation link at all --
    its `args`/`text` are not reachable by ANY conversation's purge, on
    this or any other kind. That is a known, accepted gap with no
    reaper today, not a bug this function is expected to close.
    """
    ids = list(invocation_ids)
    if not ids:
        return 0
    return ToolInvocation.objects.filter(pk__in=ids).update(
        args={}, text="", error="")


def purge_conversation(key: str) -> int:
    """Destroy one conversation's agents-side content. Returns how many
    rows were removed and records scrubbed.

    IDEMPOTENT: every step is a filtered delete or update, so a re-run
    after a partial failure removes zero rows and returns zero rather
    than raising. A conversation that is already gone -- or a `key` that
    is not a UUID at all -- is not an error: the item may have been hard
    -deleted by an older path while its ticket stood.

    ORDER, and every step's reason:
      1. collect (see `_collect`) -- nothing is deleted yet;
      2. the row deletes: this conversation's `Share` rows, then
         `agents.attachments.delete_attachments_for` (which reaches
         `tools.rag.access.delete_attachments` through the registered
         cleanup seam -- chat-scoped documents die there, universal and
         stream-contained ones lose only their claim; a failure here now
         PROPAGATES rather than degrading to zero), then
         `conversation.delete()`, with `Turn` going by CASCADE;
      3. the tool-record scrub, on the invocation ids from step 1.

    **A conversation's generated images are NOT destroyed here.** Each
    one carries a ticket of its own, written when this conversation was
    deleted (`conversation_children` below), and is destroyed by the
    image column's own handler on that ticket's date -- or, when
    somebody clicks "Delete permanently" on this conversation, by the
    same click, after this function has finished: the purge runs this
    item's row handlers first and destroys its children's bytes
    afterwards. This function therefore reaches no file on disk at all.

    A DELIBERATE, HONEST LEFTOVER: `agents.models.
    WorkstreamTaint.first_conversation` keeps this conversation's id BY
    VALUE after the purge -- content-free (an entitlement id and a
    timestamp is all a taint row ever carries), inert, and not cleaned
    up here. Nothing reads it as a live pointer back to a conversation
    that may no longer exist; it is left deliberately, not missed.
    """
    try:
        conversation_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0

    # `_refs`/`_generation_ids` ARE READ AND DROPPED, DELIBERATELY: this
    # is the SAME collect step `conversation_children` uses (below), not
    # a second copy of the turn walk -- brief-mandated, and a
    # `need_artifacts=` flag to skip the parsing here would be a second
    # thing to keep in sync with that function's own call. This purge
    # only needs the invocation ids.
    _refs, _generation_ids, invocation_ids = _collect(conversation_id)

    removed = 0

    removed += Share.objects.filter(
        target_type=Share.Target.CONVERSATION,
        target_key=str(conversation_id)).delete()[0]

    from agents.attachments import delete_attachments_for

    removed += delete_attachments_for(conversation_id)

    deleted, _by_model = Conversation.objects.filter(pk=conversation_id).delete()
    removed += deleted

    removed += scrub_tool_records(invocation_ids)

    return removed


def conversation_children(key: str) -> list[tuple[str, str]]:
    """The tickets that go with this conversation's own: one per
    generation its turns reached.

    THE SAME COLLECT STEP THE PURGE USES, not a second copy of it --
    both channels, the `output:`/`input:` artifact references and the
    `data["id"]` generation ids that catch a job which failed and minted
    no output at all.

    ASKED AT DELETE TIME, and the conversation's turns are the only
    truth about what it reached. Nothing here writes anything; the
    tickets the answer becomes are what restore and permanent delete
    follow afterwards.

    WITH NOTHING REGISTERED ON THE SLOT -- a box with the image column
    uninstalled -- this answers `[]`, and a chat delete tickets only the
    chat, which is the honest answer on that box.
    """
    try:
        conversation_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return []
    dotted = artifact_children()
    if dotted is None:
        return []
    refs, generation_ids, _invocation_ids = _collect(conversation_id)
    return [(KIND_VISION_JOB, str(job_key))
            for job_key in import_string(dotted)(refs, generation_ids)]
