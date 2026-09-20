"""Document labels, and the one writer of the chunk-metadata cache.

TABLES ARE TRUTH. `DocumentEntitlement` decides who may read a document,
and `Document.workstream` decides what stream contains it. The
`entitlements` and `workstream` keys in the live chunk table's `metadata_`
column are each a COPY of one of those facts, kept because the retriever
filters on chunk metadata and has no way to express a join back to either
table. A visibility or containment change must therefore cost ONE
`UPDATE`, not a re-encode.

ONE WRITER. `restamp_document_chunks` is the only function that writes
those keys, and it is called from exactly three places: `tools/rag/
ingest.py` immediately after `index.insert_nodes(nodes)` (so a re-ingest
of an already-labelled or already-contained document RESTORES both
rather than silently dropping them -- the indexing library rewrites the
row, and neither is its business), every label add/remove, and every
containment change (`tools.rag.workstreams.set_document_workstream`) --
the latter two inside the same transaction as the row write they
accompany.

The alternative -- stamping node metadata at ingest and SQL-updating on
change -- would produce TWO SHAPES of one fact: the ingest-time one lands
inside the store's own serialised node blob as well as the filterable
column, the SQL one only in the column. Nothing filters on the blob, so
the difference is invisible until somebody debugs it.

THE KEY IS NEVER NODE METADATA. The indexing library prepends metadata
keys onto a node's text before embedding unless they are excluded -- the
pollution `tools/rag/media.py::apply_chunk_metadata_exclusions` exists to
fix. `entitlements` sidesteps the question by never being node metadata
at all: it is written into the `metadata_` column AFTER insert.
"""
from __future__ import annotations

import json
import logging

from django.db import connection, transaction

from identity import audit
from identity.contracts import actions
from tools.rag import index as rag_index
from tools.rag.models import Document, DocumentAttachment, DocumentEntitlement

logger = logging.getLogger(__name__)


def document_label_ids(document) -> frozenset[int]:
    """The entitlement ids labelling `document` -- FROM THE TABLE, never
    from the chunk-metadata cache. The cache is a copy; these rows are the
    fact, and that distinction is what this function is for.

    `.all()`, not `.values_list()` (C-05): both read the same table, but
    `.all()` consults a `prefetch_related("entitlement_labels")` cache when
    the caller set one up and falls back to its own query when nobody did,
    while `.values_list()` always issues a fresh query and silently makes a
    caller's prefetch useless. The bulk-label loop
    (`views.document_labels_bulk`) prefetches; single-row callers do not,
    and pay exactly what they paid before.
    """
    return frozenset(label.entitlement_id for label in document.entitlement_labels.all())


def entitlement_ids_for(pks) -> frozenset[int]:
    """The UNION of the entitlement ids labelling `pks` -- ONE QUERY.

    Registered from `tools/rag/apps.py` as `ArtifactLabels("document",
    ...)`, a DOTTED-PATH STRING, so `agents/runtime/taint.py` resolves it
    at stamp time without `agents/` importing `tools/` (import-law rule
    3).

    `document_label_ids` generalised from one document to a set: that
    function answers "which labels does THIS row carry" for a caller
    holding the row, and this one answers "which labels do ANY of these
    rows carry" for a caller holding only ids. Both read the TABLE, never
    the chunk-metadata cache, for the reason this module's own docstring
    gives: the cache is a copy, the table is the fact.

    The empty set for an empty input, without a query (author decision
    14).
    """
    pks = {int(p) for p in pks}
    if not pks:
        return frozenset()
    return frozenset(
        DocumentEntitlement.objects.filter(document_id__in=pks)
        .values_list("entitlement_id", flat=True))


def _execute_stamp(sql: str, params: list) -> None:
    """The one cursor. Factored out so a test can make the store fail
    without also making the table-existence check fail."""
    with connection.cursor() as cursor:
        cursor.execute(sql, params)


def restamp_document_chunks(doc_id, *, raising: bool = False, shape=None) -> None:
    """Rewrite `doc_id`'s chunks' `entitlements`, `workstream` AND
    `conversation` keys from the tables. ONE SQL STATEMENT.

    `raising=False` (the INGEST-TIME caller) logs and returns: a failed
    re-stamp must not fail an ingest, and the label page can repair it.
    `raising=True` (the LABEL-CHANGE caller, and the CONTAINMENT-CHANGE
    caller `tools.rag.workstreams.set_document_workstream`) raises, so the
    write rolls back with it -- a label or a containment that appears to
    be saved and is not enforced is worse than one that refuses to save.

    NOT RENAMED, despite gaining a THIRD key (round 12: `conversation`,
    the owner's chat-scope ruling): it is still the ONE WRITER of chunk
    metadata, and its callers are still doors onto one statement. Two
    writers would produce two shapes of one fact.

    IT TAKES AN ID, NOT A ROW, so it has nowhere to read
    `Document.workstream_id`/`.scope` from -- so it reads them, itself,
    in one more small query beside the labels. A `workstream_id=`
    parameter was the alternative and is worse: every caller would have
    to supply it correctly, and the one that got it wrong would stamp a
    containment the table disagrees with. THE TABLE IS THE FACT AND THE
    METADATA IS A CACHE, and a cache writer that reads the fact itself
    cannot cache the wrong thing.

    `conversation` (round 12) is read from `DocumentAttachment`, not
    from `Document` itself -- the OWNER decision 6 (b): a
    `Document.scope == "conversation"` row carries EXACTLY ONE
    attachment row (the write-path invariant, `tools.rag.views._attach`'s
    own docstring), so `.first()` over that document's attachments is
    the SAME single row every other conversation-scope reader (`tools.
    rag.access.attached_documents`, the retrieval leg) already assumes
    exists. A UNIVERSAL document's own attachment rows (round 11's
    provenance-only kind) are IGNORED here -- `conversation` is stamped
    ONLY for a chat-SCOPED document, never for an ordinary one that
    merely happens to have been attached somewhere too.

    THREE OPTIONAL KEYS NEED SIX BRANCHES, NOT THREE. Each key is
    present-or-absent independently, and `IS_EMPTY` matches only an
    ABSENT key, because `metadata_->>'k'` yields SQL `NULL` for a missing
    key and `''` for an empty string. Writing `""` for "no stream" (or
    "no conversation") would make every re-stamped universal document
    invisible to every loose turn, to Ask and to Search: silently,
    totally, and only on rows that had been re-stamped. So this SETS A
    KEY OR REMOVES IT, never writes an empty value.

    A NO-OP when the chunk table does not exist (nothing prose has ever
    been ingested) -- the same defensive posture
    `tools.rag.index.delete_chunks_for_document` documents.

    `shape` (C-09): the `index.live_store_shape()` answer, resolved by the
    caller. `None` -- the default, and what every single-document caller
    passes -- means "resolve it yourself", which is exactly what this
    function always did. A caller with a LOOP (the entitlement-delete
    cascade below, `manage.py relabel_chunks`) resolves it once and hands
    it in: one database's column layout cannot change between two
    iterations of one loop, and paying a `pg_attribute` lookup per
    document to re-ask is the only thing that changes here.

    NOT A MODULE-LEVEL MEMO, deliberately. `index.get_vector_store`'s own
    comment records why a memo was rejected there: the web process must be
    able to see a table a worker process dropped. A parameter has no such
    hazard -- there is nothing to go stale, because there is nothing kept.
    """
    shape = rag_index.live_store_shape() if shape is None else shape
    if shape is None:
        return
    # The live `metadata_` column is `json`, not `jsonb`, on any box that
    # has never enabled hybrid search. BOTH filter operators cast
    # explicitly and work on either type; only this UPDATE's ASSIGNMENT
    # needs the branch.
    cast = "" if shape["jsonb"] else "::json"
    table = rag_index.LIVE_TABLE_NAME   # a module constant, never caller input
    ids = sorted(str(i) for i in _label_ids_for(doc_id))
    ws_id, scope = Document.objects.filter(pk=doc_id).values_list(
        "workstream_id", "scope").first() or (None, Document.Scope.UNIVERSAL)
    conversation_id = None
    if scope == Document.Scope.CONVERSATION:
        conversation_id = DocumentAttachment.objects.filter(
            document_id=doc_id).values_list("conversation_id", flat=True).first()

    sets: list[tuple[str, str]] = []
    removes: list[str] = []
    if ids:
        sets.append(("entitlements", json.dumps(ids)))
    else:
        removes.append("entitlements")
    if ws_id:
        sets.append(("workstream", json.dumps(str(ws_id))))
    else:
        removes.append("workstream")
    if conversation_id:
        sets.append(("conversation", json.dumps(str(conversation_id))))
    else:
        removes.append("conversation")

    # ONE STATEMENT: nested `jsonb_set(..., create_missing=true)` for each
    # key in `sets`, chained `- 'key'` for each in `removes`.
    expression = "metadata_::jsonb"
    params: list = []
    for key, value in sets:
        expression = f"jsonb_set({expression}, '{{{key}}}', %s::jsonb, true)"
        params.append(value)
    for key in removes:
        expression = f"({expression} - '{key}')"

    # THE GUARD IS A DISJUNCTION, and it is not decoration. The
    # single-key version of it (`AND metadata_->>'entitlements' IS NOT
    # NULL`) exists because without it this UPDATE rewrote every chunk
    # row on every ingest of every never-labelled document -- a dead
    # tuple and a WAL record per chunk, on the box's most common case
    # (review finding 3). Carried forward unchanged, it would silently
    # reintroduce that regression the moment another optional key
    # appeared, and it would ALSO skip a real containment/conversation
    # write. The disjunction says "at least one key would actually
    # change".
    sql = (f"UPDATE {table} SET metadata_ = ({expression}){cast} "
           f"WHERE metadata_->>'file_id' = %s "
           f"AND (metadata_->>'entitlements' IS NOT NULL "
           f"     OR metadata_->>'workstream' IS NOT NULL "
           f"     OR metadata_->>'conversation' IS NOT NULL "
           f"     OR %s)")
    params += [str(doc_id), bool(sets)]

    try:
        _execute_stamp(sql, params)
    except Exception:
        if raising:
            raise
        logger.warning("rag: could not re-stamp chunk metadata for Document %s", doc_id,
                       exc_info=True)


def _label_ids_for(doc_id) -> frozenset[int]:
    return frozenset(
        DocumentEntitlement.objects.filter(document_id=doc_id)
        .values_list("entitlement_id", flat=True))


def set_document_labels(actor, document, entitlement_ids, *, labelled_by=None) -> None:
    """Make `document`'s labels exactly `entitlement_ids`, and re-stamp
    its chunks, IN ONE TRANSACTION.

    WRITES THE DIFFERENCE, not the whole set, so an audit trail records
    what changed rather than what was resubmitted.

    THE CALLER CHECKS THE PREDICATE (`tools.rag.access.may_label_document`
    plus the "owner of every entitlement being added or removed" rule in
    the view). This function is the write, not the guard.

    `labelled_by` is an `identity` `User` INSTANCE, or `None` -- mirrors
    `agents.labels.set_tool_labels`'s ruling exactly: this module never
    imports `identity.models` to get one; the caller passes it and this
    function only assigns it to the FK of every row the add loop below
    creates. Provenance also lives in the audit rows written below; this
    column fills only when the caller holds a real `User` (the label page
    will; a shell or the entitlement-delete cascade does not).
    """
    wanted = {int(i) for i in entitlement_ids}
    with transaction.atomic():
        current = set(_label_ids_for(document.id))
        for entitlement_id in sorted(wanted - current):
            DocumentEntitlement.objects.create(document=document,
                                               entitlement_id=entitlement_id,
                                               labelled_by=labelled_by)
            audit.record(actor, actions.DOCUMENT_LABELLED, target_type="document",
                         target_key=document.id, target_label=document.title,
                         entitlement_id=entitlement_id)
        for entitlement_id in sorted(current - wanted):
            DocumentEntitlement.objects.filter(
                document=document, entitlement_id=entitlement_id).delete()
            audit.record(actor, actions.DOCUMENT_UNLABELLED, target_type="document",
                         target_key=document.id, target_label=document.title,
                         entitlement_id=entitlement_id)
        if wanted != current:
            restamp_document_chunks(document.id, raising=True)


def unlabel_all_for_entitlement(entitlement_id: int, *, commit: bool) -> int:
    """This column's answer to "an entitlement is being deleted".

    Registered from `tools/rag/apps.py::ready()` as a DOTTED-PATH STRING,
    so `identity/` runs it without importing `tools/` (import-law rule 4).

    `commit=True` removes every label naming `entitlement_id` and
    RE-STAMPS each affected document, inside the caller's transaction
    (`identity.services.delete_entitlement`'s). Without the re-stamp the
    tables would say "unlabelled" while the chunks still said "Finance",
    and retrieval would keep hiding a document nothing protects any more.

    It runs BEFORE `entitlement.delete()`, so the re-stamp sees the label
    already gone. The database's own `CASCADE` on the foreign key stays
    as the net: a shell that deletes the row directly still leaves no
    orphan labels, only a stale cache, and `manage.py relabel_chunks` is
    the documented repair.
    """
    doc_ids = list(
        DocumentEntitlement.objects.filter(entitlement_id=entitlement_id)
        .values_list("document_id", flat=True))
    if not commit:
        return len(doc_ids)
    DocumentEntitlement.objects.filter(entitlement_id=entitlement_id).delete()
    shape = rag_index.live_store_shape()   # C-09: once for the whole cascade
    for doc_id in doc_ids:
        restamp_document_chunks(doc_id, raising=True, shape=shape)
    return len(doc_ids)
