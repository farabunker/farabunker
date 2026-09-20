"""What the work has touched, recorded at the moment it touches it.

THE ONE WRITER OF DERIVED TAINT. `stamp_turn_taint` is the only function
that DERIVES a tag from a turn's own retrieved material, and it is
called from exactly one place: inside the `transaction.atomic()` that
creates a TOOL TURN in `agents/runtime/loop.py`. Two other call sites
touch these tables without deriving anything new: `agents/visibility.py
::duplicate_conversation` COPIES a conversation's existing
`ConversationTaint` rows onto its copy, row for row, and `agents/
workstreams.py::workstream_entitlement_cascade` DELETES every row naming
an entitlement that is itself being deleted. Neither decides that
something is newly tainted -- one carries a fact forward, the other
retracts one that no longer holds -- so `stamp_turn_taint` stays the one
place that answer is computed.

THE TOOL TURN, NOT THE ASSISTANT TURN, and that is the correction spec
§7.2 exists to make. `run_loop`'s caller wraps `_run_turn` in a
`try/except` that, on any exception, flips the assistant turn to FAILED
with one `.update()` -- and `_finish` is reached only on the SUCCESS
path. The tool turns, meanwhile, are created and COMMITTED as they run,
each carrying the retrieved text in `text`/`data` and its `document:`
references in `artifacts`. So "retrieve a document labelled E, then the
assistant call raises" would leave the E material committed and rendered
in the thread with NO taint row for it, and both share gates would then
pass a stream that holds E material. That is the opposite of the
conservative direction this mechanism claims: it is "material is in
there and no gate knows", not "a share you expected does not work".

`_finish` THEREFORE GROWS NO TRANSACTION AT ALL. It stays the single
`save()` it is today.
"""
from __future__ import annotations

import logging

from django.utils.module_loading import import_string

from agents.contracts.artifacts import labels_resolver_for, parse_artifact
from agents.models import ConversationTaint, WorkstreamTaint
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL

logger = logging.getLogger(__name__)


def _references_by_kind(artifacts) -> dict[str, set[int]]:
    """`{kind: {pk, ...}}` for every parseable reference in `artifacts`.

    `parse_artifact` is THE ONE PARSER and taint uses it, so the taint
    stamp cannot come to disagree with the chat page's own rendering
    about what a reference means -- including `mint_artifact`'s optional
    `:<title>` suffix, which that parser peels off. A malformed
    reference is SKIPPED rather than raising: this runs inside a turn's
    own transaction, and one bad string must never fail a turn.
    """
    out: dict[str, set[int]] = {}
    for reference in artifacts or ():
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            continue
        out.setdefault(kind, set()).add(pk)
    return out


def stamp_turn_taint(turn, conversation, artifacts, *, actor=None) -> frozenset[int]:
    """Record the entitlement labels of the rows `turn` returned, on the
    conversation and on its stream. Returns what was ADDED.

    Returns the empty set WITHOUT TOUCHING THE DATABASE when `artifacts`
    holds no reference of a kind that has a registered labels resolver --
    which is the overwhelmingly common case (a turn that called no
    retrieval tool) and must cost nothing. `agents/tests/test_taint.py`
    pins that with a query count.

    `actor` is the ACTING PRINCIPAL, read back from the job payload by
    the caller -- the acting rule, unchanged. The taint was caused by a
    person's turn, not by the box, AND THAT PERSON IS NOT ALWAYS THE
    STREAM'S OWNER: a share recipient's turn taints the owner's stream,
    which is ruling B (spec §23.B) and is the point of the mechanism
    rather than a flaw in it. One audit row per newly added tag, carrying
    its acting principal, is therefore the one place "who brought E into
    this stream" is answerable.
    """
    references = _references_by_kind(artifacts)
    if not references:
        return frozenset()

    ids: set[int] = set()
    for kind, pks in references.items():
        path = labels_resolver_for(kind)
        if path is None:
            # A KIND WITH NO RESOLVER CONTRIBUTES NOTHING, SILENTLY --
            # `output:` and `input:` today, because `tools/vision` has no
            # label table and a tool's own output text is not a labelled
            # row at all. Registering a resolver for a kind that has no
            # labels would be a query that always returns the empty set.
            continue
        try:
            ids |= import_string(path)(frozenset(pks))
        except Exception:  # noqa: BLE001 -- a broken resolver, not a broken turn
            logger.exception("taint: resolver %r for kind %r failed", path, kind)
    if not ids:
        return frozenset()

    actor = actor if actor is not None else OPEN_PRINCIPAL
    added_conversation = _add_conversation_tags(conversation, ids, turn, actor)
    added_stream = frozenset()
    if conversation.workstream_id:
        added_stream = _add_workstream_tags(conversation, ids, turn, actor)
    return added_conversation | added_stream


def _add_tags(*, rows_model, existing_ids, wanted_ids, build_row, audit_row) -> frozenset[int]:
    """`bulk_create` the tags in `wanted_ids - existing_ids`, ignoring
    conflicts, and audit each one that was genuinely new (C-29).

    **MUST BE CALLED INSIDE THE CALLER'S `transaction.atomic()`** and
    deliberately opens none of its own: `agents/runtime/loop.py` wraps the
    tool `Turn` row and this stamp in ONE block, because the tag rows and
    the turn that caused them must land together or not at all. A nested
    block here would be a second failure boundary inside that one.

    `existing_ids` is READ BY THE CALLER, not by this helper -- the two
    callers below query a different relation (`conversation.taint_tags`
    vs `stream.taint_tags`) for it, and `fresh` (what actually gets
    written and audited) is `wanted_ids - existing_ids`, computed here so
    both callers keep the exact same read-diff-bulk_create-audit order
    the original two functions had.

    `build_row(entitlement_id)` and `audit_row(entitlement_id)` are the
    caller's own vocabulary: the two levels write different models and
    different actions, and the workstream row carries a `conversation`
    field the conversation row does not.
    """
    fresh = sorted(set(wanted_ids) - set(existing_ids))
    if not fresh:
        return frozenset()
    rows_model.objects.bulk_create([build_row(i) for i in fresh], ignore_conflicts=True)
    for entitlement_id in fresh:
        # AUDITED INSIDE THE SAME TRANSACTION as the tag rows (author
        # decision 13): `identity.audit.record` never swallows, and an
        # audit row that survived a rolled-back tag row would name a
        # tainting that did not happen.
        audit_row(entitlement_id)
    return frozenset(fresh)


def _add_conversation_tags(conversation, ids, turn, actor) -> frozenset[int]:
    """Step 1 of spec §7.3.

    THE READ COMES FIRST, AND IT IS NOT AN OVERSIGHT.
    `bulk_create(ignore_conflicts=True)` cannot report which rows landed,
    and `fresh` is what drives the audit rows -- one per (conversation,
    entitlement) FIRST SIGHTING, which is the whole point of §16.2. So
    the read is what makes the trail exactly one row per first sighting,
    and `ignore_conflicts` is the RACE GUARD against two turns stamping
    the same tag at once, not an optimisation that removes the read."""
    existing = set(conversation.taint_tags.filter(entitlement_id__in=ids)
                   .values_list("entitlement_id", flat=True))
    return _add_tags(
        rows_model=ConversationTaint,
        existing_ids=existing,
        wanted_ids=ids,
        build_row=lambda i: ConversationTaint(
            conversation=conversation, entitlement_id=i, first_turn=turn.pk),
        audit_row=lambda i: audit.record(
            actor, actions.CONVERSATION_TAINTED, target_type="conversation",
            target_key=conversation.pk, target_label=conversation.title,
            entitlement=i, turn=turn.pk),
    )


def _add_workstream_tags(conversation, ids, turn, actor) -> frozenset[int]:
    """Step 2: the union upward, IN THE SAME TRANSACTION as the
    conversation rows it follows -- only when the conversation is in a
    stream. A loose conversation stops at step 1.

    Same read-then-`ignore_conflicts` shape as step 1, and for the same
    reason: the read drives the audit rows, the flag guards the race."""
    stream = conversation.workstream
    existing = set(stream.taint_tags.filter(entitlement_id__in=ids)
                   .values_list("entitlement_id", flat=True))
    return _add_tags(
        rows_model=WorkstreamTaint,
        existing_ids=existing,
        wanted_ids=ids,
        build_row=lambda i: WorkstreamTaint(
            workstream=stream, entitlement_id=i,
            first_conversation=conversation.pk, first_turn=turn.pk),
        audit_row=lambda i: audit.record(
            actor, actions.WORKSTREAM_TAINTED, target_type="workstream",
            target_key=stream.pk, target_label=stream.name,
            entitlement=i, conversation=str(conversation.pk), turn=turn.pk),
    )
