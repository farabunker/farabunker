"""What a workstream contains, from this column's side.

THE ONLY MODULE THAT WRITES `WorkstreamPin`, and (with
`tools/rag/access.py`) one of only two that read it -- the same shape and
the same reason the `Document.objects` gate exists: two readers of "what
is in this stream" is how two surfaces come to disagree. Pinned by
`foundation/ops/tests/test_column_boundaries.py`.

IT REACHES `agents/` THROUGH `agents.workstreams` AND `agents.contracts`
AND NOTHING ELSE -- the permitted direction, closed to three names by
`foundation/ops/tests/test_import_law.py::test_tools_reaches_agents_
through_contracts_entitlements_and_workstreams_and_nothing_else`.
"""
from __future__ import annotations

from dataclasses import replace

from agents.contracts.workstreams import WorkstreamScope
from identity import audit
from identity.contracts import actions
from tools.rag.models import Document, WorkstreamPin

# A pin set becomes an `ANY` array of ids in EVERY query this stream
# runs, so an uncapped one is unbounded work per turn. A CAP, NOT A
# PAGINATOR -- the reason `SIDEBAR_LIMIT = 30` is one: this is a
# single-operator box and a working set is not an archive (author
# decision 7).
MAX_PINS_PER_STREAM = 200


def scope_with_pins(scope: WorkstreamScope | None) -> WorkstreamScope | None:
    """The same frozen value with `pinned_file_ids` filled in, or `None`
    straight through if `scope` is `None`.

    THE ONLY FUNCTION THAT FILLS THEM (author decision 6). The stream
    half -- id, wall, upload default, may-upload -- crosses the seam from
    `agents/workstreams.py` with the pins empty, because the pin table is
    a `tools/rag` table and the column that owns it is the column that
    reads it.

    THE NONE-THROUGH-NONE CASE is for `tools/rag/tools.py`'s two callers
    (`run_search`, `run_ask`), where `ctx.stream` is `None` for a
    conversation with no workstream at all -- that call site used to
    spell `scope_with_pins(ctx.stream) if ctx.stream is not None else
    None` itself, twice; the ternary lives here once instead so both
    callers just write `scope_with_pins(ctx.stream)`.

    `dataclasses.replace`, not a fresh construction, so a field added to
    `WorkstreamScope` later travels through here without an edit.
    """
    if scope is None:
        return None
    return replace(scope, pinned_file_ids=frozenset(
        WorkstreamPin.objects.filter(workstream_id=scope.workstream_id)
        .values_list("document_id", flat=True)))


def pin_document(principal, scope: WorkstreamScope, document) -> str | None:
    """Pin `document` into the stream. Returns None on success, or a
    sentence naming the refusal.

    FOUR REFUSALS, all NAMED rather than 404-shaped, because this is a
    button on a page listing rows the caller can already see:

      - the document is CONVERSATION-scoped (round 12 whole-branch
        review B-1, CHECKED FIRST -- before the cap below, so a doomed
        chat-scoped pin attempt never consumes a `MAX_PINS_PER_STREAM`
        slot). `readable_documents(principal)` -- no `workstream_id`,
        the SAME call this function's own readability check below makes
        -- re-admits the UPLOADER's own chat-scoped rows (round 12
        review I-2's own rule), so without this check the readability
        clause below would have let a chat-scoped document's own
        uploader "pin" it: the write would SUCCEED, into a pin that is
        invisible in the stream panel's own pinned list (that surface
        reads through `readable_documents(…, workstream_id=W)`, which
        correctly excludes a chat-scoped row) and inert at retrieval
        either way -- exactly the silently-inert pin owner decision 2's
        own "the reader's own grants are the outermost intersection"
        rule was never meant to produce. A chat-scoped document is not
        part of the RAG framework at all (the owner's own round 12
        ruling), so there is no "workstream" home for it to be pinned
        INTO in the first place.
      - the document is CONTAINED (owner decision 4: one home, never
        two). Not into another stream, and not into its own -- it is
        already in its stream's corpus by containment, and putting it in
        a second stream is the duplication that decision forbids.
      - the cap (`MAX_PINS_PER_STREAM`) is reached.
      - the caller may not read the document (`readable_documents`).

    The third is ALSO a 404 at the route (`rag-workstream-pin` resolves
    the document through `readable_documents`) and is repeated here
    because this function is the CLI-facing one too, and a predicate
    stated in the view alone is a predicate the second caller does not
    get.

    A PIN DOES NOT FOLLOW A DOCUMENT'S LABELS. Pinning is recorded once;
    if the document is later labelled with an entitlement the pinner does
    not hold, the pin row survives and the document simply stops being
    readable by them -- spec §6.1's outer intersection does that, with no
    pin bookkeeping at all. A pin is never a stored permission and
    therefore never goes stale in a way that matters.
    """
    from tools.rag.access import readable_documents

    if document.scope == Document.Scope.CONVERSATION:
        return (f"“{document.title}” is scoped to one chat only, not part of the RAG "
                f"framework, and cannot be pinned into a workstream.")
    if document.workstream_id is not None:
        return (f"“{document.title}” already lives in a workstream. A document has one "
                f"home; pin a universal document instead.")
    if WorkstreamPin.objects.filter(workstream_id=scope.workstream_id).count() \
            >= MAX_PINS_PER_STREAM:
        return (f"This workstream already has {MAX_PINS_PER_STREAM} pinned documents, "
                f"which is the limit. Unpin one first.")
    if not readable_documents(principal).filter(pk=document.pk).exists():
        return "You may not read that document, so it cannot be pinned here."
    WorkstreamPin.objects.get_or_create(
        workstream_id=scope.workstream_id, document=document,
        defaults={"pinned_by_id": int(principal.key) if principal.kind == "user" else None})
    audit.record(principal, actions.DOCUMENT_PINNED, target_type="workstream",
                 target_key=scope.workstream_id, document=document.pk,
                 document_title=document.title)
    return None


def unpin_document(principal, scope: WorkstreamScope, pin_id) -> str | None:
    """Remove one pin FROM THIS STREAM. None if it went, a sentence
    otherwise.

    THE PIN IS CHECKED AGAINST THIS STREAM, or an owner of stream A could
    unpin from stream B by guessing a sequential id -- the same IDOR
    `agents.visibility.revoke_share` guards against, and `isdecimal()`
    for the same reason its docstring gives: `isdigit()` admits
    characters like "²" that `int()` itself rejects.
    """
    if not str(pin_id).isdecimal():
        return "That pin does not belong to this workstream."
    row = WorkstreamPin.objects.filter(
        pk=int(pin_id), workstream_id=scope.workstream_id).select_related("document").first()
    if row is None:
        return "That pin does not belong to this workstream."
    title, document_pk = row.document.title, row.document_id
    row.delete()
    audit.record(principal, actions.DOCUMENT_UNPINNED, target_type="workstream",
                 target_key=scope.workstream_id, document=document_pk, document_title=title)
    return None


def stream_documents(principal, scope: WorkstreamScope, *, settings_row=None):
    """The ORM mirror of spec §6.1's corpus formula, for the panel and
    the pin picker:

        (  universal ∩ wall   (only when the wall is non-empty,
                               and ONLY when include_universal is True)
         ∪ pinned
         ∪ contained-here  )  ∩ readable_by(principal)

    THE READER'S OWN GRANTS ARE THE OUTERMOST INTERSECTION AND NOTHING
    ESCAPES THEM. A pin does not grant, containment does not grant, and a
    stream never grants (author decision 2). The wall sits INSIDE the
    parentheses because it narrows what the universal library
    contributes; the pin and the contained set sit beside it because they
    are deliberate acts that admit specific documents, and a wall -- a
    convenience the owner set for themselves -- does not un-admit what
    somebody deliberately put in.

    A NON-EMPTY WALL EXCLUDES UNLABELLED UNIVERSAL DOCUMENTS (author
    decision 5): a wall says "this stream is about material under these
    entitlements", and a document under no entitlement is under none of
    them. The stream page states that consequence in one line beside the
    wall editor.

    ROUND 17 (owner: "a bool in settings to use all rag documents ...
    default it on"): `scope.include_universal is False` drops the WHOLE
    universal leg -- wall-narrowed or not -- from the `OR`, never merely
    narrows it further. Pinned and contained-here are UNCHANGED either
    way: a pin and a containment are deliberate per-document acts this
    toggle was never asked to undo, exactly as they already survive a
    non-empty wall for the identical reason two paragraphs up.

    `settings_row`, OPTIONAL (round 11 review, minor 5): passed straight
    through to `readable_documents`, which now accepts it -- see that
    function's own docstring.
    """
    from django.db.models import Q

    from tools.rag.access import readable_documents

    corpus = Q(workstream_id=scope.workstream_id)
    if scope.include_universal:
        universal = Q(workstream__isnull=True)
        if scope.wall:
            universal &= Q(entitlement_labels__entitlement_id__in=scope.wall)
        corpus |= universal
    if scope.pinned_file_ids:
        corpus |= Q(pk__in=scope.pinned_file_ids)
    return readable_documents(
        principal, workstream_id=scope.workstream_id,
        settings_row=settings_row).filter(corpus).distinct()


def set_document_workstream(actor, document, workstream_id) -> None:
    """Contain `document` in a stream (or release it, with `None`), and
    re-stamp its chunks, IN ONE TRANSACTION.

    `raising=True`, the third caller of `restamp_document_chunks` beside
    `_ingest_prose` (ingest time, `raising=False`) and
    `set_document_labels` (a label change, `raising=True`). A containment
    change that appears saved and is not enforced is worse than one that
    refuses to save, which is the rule the `raising` flag already
    encodes.

    DELETES THIS DOCUMENT'S OWN `WorkstreamPin` ROWS, in the same
    transaction: a pin names a document AS PINNED INTO some other
    stream, and re-homing the document (into a different stream, or
    back to universal with `None`) means it is no longer the universal
    document that pin was made against. Left standing, that pin would
    keep naming a stream the document no longer belongs to the way it
    did when it was pinned -- `tools.rag.retrieval`'s pinned leg also
    guards against exactly this by requiring the UNIVERSAL stamp, but
    the guard and the delete are both needed: the guard for a read that
    races this write, the delete so no permanently stale row survives
    once the write has landed. `agents.workstreams.
    workstream_entitlement_cascade`'s own "NO CASCADE FOR WorkstreamPin"
    docstring is about ITS cascade (an entitlement delete) and is
    unaffected by this one.

    Audited as `DOCUMENT_CONTAINED`, with the `library.` namespace
    because `DOCUMENT_LABELLED` already takes it and the subject is the
    same row. The audit write is INSIDE the same `transaction.atomic()`
    as the save and the restamp, matching `set_document_labels`'s own
    pattern: a partial commit must never leave a containment change on
    the row with no trail explaining it.
    """
    from django.db import transaction

    from tools.rag.labels import restamp_document_chunks

    with transaction.atomic():
        document.workstream_id = workstream_id
        document.save(update_fields=["workstream", "updated_at"])
        restamp_document_chunks(document.id, raising=True)
        WorkstreamPin.objects.filter(document_id=document.id).delete()
        audit.record(actor, actions.DOCUMENT_CONTAINED, target_type="document",
                     target_key=document.id, target_label=document.title,
                     workstream=workstream_id)


def panel(principal, workstream_id) -> dict:
    """`rag.documents` — this column's stream-page section (spec §15.3
    item 6).

    Registered as a DOTTED PATH from `tools/rag/apps.py`, resolved at
    render time by `agents/workstreams.py::panels_for`, so `agents/`
    never imports this module. Three groups, exactly as §15.3 names them:
    **Contained** (with notes marked by `origin`), **Pinned** (each with
    its pin id, for Unpin), and the **Pin a document** control's capped
    candidate set of readable universal documents.

    `pinnable` IS `{"id", "name", "checked"}` DICTS, NOT `Document` ROWS
    (ROUND 17 consolidation: "Pin a document" became the round-16
    searchable-checkbox dropdown, `agents/chat/templates/chat/_search_
    checklist.html`'s own ONE consumer-agnostic shape -- `checked` is
    always `False` here, since nothing is pre-selected in a single-pick
    chooser, kept anyway for exact parity with the OTHER consumer's rows
    [`agents/chat/views/all_conversations.py::tag_checkboxes`] rather
    than a shape that happens to differ only because this caller never
    needed the field). The SAME candidate SET as before this round --
    still readable universal, still capped, still minus already-pinned
    -- only the shape changed, at the one call site that renders it.
    """
    from agents.workstreams import workstream_visible
    from tools.rag.access import readable_documents

    # E2: a cheap admission check, not the full `WorkstreamScope` --
    # everything below only ever needed the `is None`/`is not None`
    # answer, never the wall `workstream_scope` builds (~6 queries) to
    # give it.
    if not workstream_visible(principal, workstream_id):
        return {"contained": [], "pinned": [], "pinnable": [], "capped": False}
    pins = list(WorkstreamPin.objects.filter(workstream_id=workstream_id)
                .select_related("document").order_by("-pinned_at"))
    readable_here = readable_documents(principal, workstream_id=workstream_id)
    contained = list(readable_here.filter(workstream_id=workstream_id)
                     .order_by("origin", "title"))
    pinned_ids = {p.document_id for p in pins}
    # ONE QUERY FOR THE WHOLE PIN SET, not one per pin. A
    # `.filter(...).exists()` inside the comprehension would be up to
    # `MAX_PINS_PER_STREAM` queries on a page Global Constraint 6 covers
    # -- the same shape `agents/chat/sidebar.py`'s own docstring records
    # as a measured regression (+2 per row, 47 -> 95 at 25 rows).
    readable_pin_ids = set(readable_here.filter(pk__in=pinned_ids)
                           .values_list("pk", flat=True))
    visible_pins = [{"pin": p, "document": p.document} for p in pins
                    if p.document_id in readable_pin_ids]
    # E3: `readable_here.filter(workstream__isnull=True)`, NOT a second
    # `readable_documents(principal)` call -- `readable_here`'s own
    # containment clause is `workstream__isnull=True) | workstream_id=
    # workstream_id)`, so narrowing IT to `workstream__isnull=True` is
    # set-identical to a fresh universal-only `readable_documents(
    # principal)` and builds no second `DocumentVisibility`.
    pinnable = [
        {"id": d.pk, "name": d.title, "checked": False}
        for d in readable_here.filter(workstream__isnull=True)
        .exclude(pk__in=pinned_ids).order_by("title")[:MAX_PINS_PER_STREAM]
    ]
    return {
        "contained": contained,
        "pinned": visible_pins,
        "pinnable": pinnable,
        "capped": len(pins) >= MAX_PINS_PER_STREAM,
    }
