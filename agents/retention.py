"""What `agents/` destroys when a deleted conversation's date arrives.

REGISTERED, NEVER CALLED DIRECTLY. `agents/apps.py::ready()` registers
the dotted path; `identity/cascades.py::run_retention` resolves it
inside `identity.retention.purge_ticket`'s transaction, in a savepoint
of its own. `identity/` never imports this module, and this module never
imports `tools/` or `models.queue` -- both are reached by registration,
which is what makes the whole purge one transaction across four columns
that may not import each other.

THE FILES BAND, not the rows band, and deliberately: this handler must
READ the conversation's turns (their artifacts and their tool payloads)
before it deletes them, AND it reaches bytes on disk through the
registered artifact purge. A handler that must do both registers in the
FILES band and orders its own reads before its own writes internally --
which keeps the registry's ordering rule to one field with two values
instead of a general dependency graph nothing else needs.
"""
from __future__ import annotations

import logging
import uuid

from agents.contracts.artifacts import artifact_purge, parse_artifact
from agents.models import Conversation, Share, ToolInvocation, Turn

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
    """
    refs: list[str] = []
    generation_ids: list[str] = []
    invocation_ids: list[int] = []
    rows = Turn.objects.filter(conversation_id=conversation_id).values_list(
        "artifacts", "data", "invocation_id")
    for artifacts, data, invocation_id in rows:
        for reference in artifacts or ():
            try:
                kind, _pk = parse_artifact(reference)
            except ValueError:
                logger.warning(
                    "agents.retention: conversation %s carries an unusable "
                    "artifact reference %r; ignored.", conversation_id, reference)
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
    """
    ids = list(invocation_ids)
    if not ids:
        return 0
    return ToolInvocation.objects.filter(pk__in=ids).update(
        args={}, text="", error="")


def purge_conversation(key: str) -> int:
    """Destroy one conversation's agents-side content. Returns how many
    rows and images it removed.

    IDEMPOTENT: every step is a filtered delete or update, so a re-run
    after a partial failure removes zero rows and returns zero rather
    than raising. A conversation that is already gone -- or a `key` that
    is not a UUID at all -- is not an error: the item may have been hard
    -deleted by an older path while its ticket stood.

    ORDER, and every step's reason:
      1. collect (see `_collect`) -- nothing is deleted yet;
      2. hand the references and ids to the registered artifact purge --
         called whenever a handler IS registered, even with two empty
         lists, so the registered handler decides for itself whether
         there is anything to do rather than this column silently
         deciding on its behalf. It maps them to jobs, dedupes by job,
         and deletes each job with its files and its queue row;
      3. the row deletes: this conversation's `Share` rows, then
         `agents.attachments.delete_attachments_for` (which reaches
         `tools.rag.access.delete_attachments` through the registered
         cleanup seam -- chat-scoped documents die there, universal and
         stream-contained ones lose only their claim), then
         `conversation.delete()`, with `Turn` going by CASCADE;
      4. the tool-record scrub, on the ids from step 1.
    """
    try:
        conversation_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0

    refs, generation_ids, invocation_ids = _collect(conversation_id)

    removed = 0
    purge = artifact_purge()
    if purge is not None:
        from django.utils.module_loading import import_string
        removed += import_string(purge)(refs, generation_ids)

    removed += Share.objects.filter(
        target_type=Share.Target.CONVERSATION,
        target_key=str(conversation_id)).delete()[0]

    from agents.attachments import delete_attachments_for

    removed += delete_attachments_for(conversation_id)

    deleted, _by_model = Conversation.objects.filter(pk=conversation_id).delete()
    removed += deleted

    removed += scrub_tool_records(invocation_ids)
    return removed
