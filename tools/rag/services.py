"""
Document-level service operations for the RAG module (ADR 0009).

These are the higher-level operations built on top of tools/rag/store.py
and the pgvector store that other waves (the library UI's delete endpoint,
in particular) call into, rather than reimplementing document teardown
themselves.
"""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from django.conf import settings

from agents.contracts.attachments import (
    AttachmentUploadResult, BAD_PLACEMENT_MESSAGE, placement_is_valid,
)
from foundation.files import create_owner_only_dir
from models.contracts import gateway
from models.contracts.bindings import resolve
from models.contracts.roles import RAG_EMBED_ROLE
from identity.access import owner_fields
from tools.rag import index as rag_index
from tools.rag import ingest, store
from tools.rag.models import AskRecord, Document, DocumentAttachment, RagSettings
from tools.rag.readers import pdf_textless_pages

logger = logging.getLogger(__name__)


def delete_document(document) -> None:
    """Fully remove `document`: its vector chunks, its stored files, and
    the `Document` row itself (its `DocumentRow`s cascade via the FK).

    Vector-chunk deletion goes through the shared
    `rag_index.delete_chunks_for_document` helper -- the same one ingest.py
    uses for re-ingest cleanup -- which is defensive about a missing/
    unreachable store so it never blocks deleting the document.

    NOT TRANSACTIONAL WITH THE ROW (round 12 review, minor 2 -- recorded
    honestly rather than reordered): `store.remove_document_files` is a
    FILESYSTEM operation, and a filesystem delete cannot be rolled back
    by a database transaction. A caller that runs this inside its own
    `transaction.atomic()` (`tools.rag.access.delete_attachments`, the
    round-12 chat-scope cascade, is the one today) and then hits a LATER
    failure in that SAME transaction -- another document's own deletion
    raising, or the surrounding `conversation.delete()` itself failing
    -- rolls the `Document` ROW back (it reappears), while the FILES
    this call already removed stay gone: a resurrected row pointing at
    bytes that no longer exist. `ORDER OF OPERATIONS IS DELIBERATE THE
    OTHER WAY` (`test_services.py::TestDeleteDocument::
    test_order_of_operations_files_then_row_deleted`, unmoved by this
    round): files are removed WHILE the row still exists, keyed by
    `document.id`, which is why this is a comment and not a reorder --
    deferring file removal to `transaction.on_commit` would fix the
    inconsistency window this docstring names, but at the cost of that
    existing, deliberate invariant and of every caller's ability to
    assert file removal synchronously (this module's own test suite
    does, throughout); a Minor-severity finding is not reason enough to
    destabilize both on a call graph as small as this one's single
    caller. Left as a named, accepted gap rather than a silent one.
    """
    rag_index.delete_chunks_for_document(document.id)
    store.remove_document_files(document.id)
    document.delete()


# A-3: THE CATEGORY BRANCH'S BOUND. The id branch gets one for free --
# the framework refuses a POST with more than 1000 fields -- and the
# category branch, which takes PRECEDENCE, had none at all: one member
# who owns a single entitlement (the minimum `document_labels_bulk`'s own
# offered-set guard requires) can POST a removal against the largest
# shelf on the box and drive one unbounded loop of label writes and
# audit rows. Refused with a named message telling the operator to
# select rows instead, in the same honest-rejection register the page
# and duration caps (`tools.rag.ingest.DocumentPageCapExceededError`/
# `MediaDurationExceededError`) already use.
MAX_BULK_LABEL_TARGETS = 1000


class BulkLabelTargetSetTooLargeError(ValueError):
    """`documents_targeted_for_labelling`'s specific rejection for a
    `category` shelf with more than `MAX_BULK_LABEL_TARGETS` rows on it
    (A-3 residue, round-3 hardening H33) -- a `ValueError` subclass, the
    exact `DocumentPageCapExceededError`/`MediaDurationExceededError`
    pattern one column over, so `tools.rag.views.document_labels_bulk`
    can catch this name specifically, ahead of a bare `except
    ValueError`/`except Exception`, and turn it into the same flash-and-
    redirect shape its other designed refusals already use.
    """


def documents_targeted_for_labelling(*, ids=None, category: str = ""):
    """Raw `Document` rows an ADMINISTRATION action -- `tools.rag.views.
    document_labels_bulk`'s target resolution -- names, by id list or by
    category.

    UNFILTERED BY VISIBILITY, deliberately: the same choice `document_
    delete`/`document_reingest` already make via their own
    `get_object_or_404(Document, ...)` (decision 9, "administration, not
    reading" -- spec section 11.3). `tools.rag.access.may_label_document`
    is the ONLY gate on what a bulk apply/remove actually changes, checked
    PER DOCUMENT by the caller. Resolving the candidate set through
    `tools.rag.access.listable_documents` first would make a document
    under an entitlement the caller does not hold silently DISAPPEAR from
    the set instead of being skipped and counted -- which is the one
    thing the bulk route promises an operator selecting a page of rows.

    LIVES HERE, not in `tools/rag/views.py`: `foundation/ops/tests/
    test_column_boundaries.py::test_rag_views_reads_documents_through_
    the_access_module` refuses ANY literal `Document.objects` in that
    file's source, on purpose -- a raw read of the whole table is the one
    thing a page rendering document ROWS must never do by accident. This
    function is the one sanctioned exception, named for what it is
    (administration, not a reading surface) and kept out of that file
    entirely so the guard stays a true zero rather than growing an
    exception list.

    `category`, when given, wins over `ids` -- the same precedence
    `document_labels_bulk` reads its POST body with. An unknown category
    name (stale form, hand-made request) simply matches no row, which is
    the correct reading of it: no `Category` existence check is needed
    for an administration action that already checks every row's own
    permission.

    BOUNDED AT `MAX_BULK_LABEL_TARGETS` (A-3 residue, round-3 hardening
    H33): the `ids` branch already gets a cap for free -- the framework
    refuses a POST carrying more than 1000 fields, so `len(ids)` can
    never exceed that -- but `category` names an arbitrary-size shelf by
    a single string field, which the framework's cap does nothing for.
    Raises `BulkLabelTargetSetTooLargeError` when the named shelf has
    more than `MAX_BULK_LABEL_TARGETS` rows on it, counted BEFORE the
    queryset is returned so the caller never even sees an oversize one
    to iterate.

    EXCLUDES CONVERSATION-SCOPED DOCUMENTS (round 12, owner ruling):
    labels are corpus machinery, and a chat-scoped document has no
    corpus, so there is nothing here for a label to mean -- excluded at
    THIS query, not merely refused per-row by `may_label_document`
    (which also gates delete/re-ingest, and a chat-scoped document's
    re-ingest must still work; see that predicate's own docstring), so
    a chat-scoped row is never even a CANDIDATE the bulk route counts
    as "skipped" -- it is simply never offered, matching the door's own
    checkboxes (`tools.rag.access.listable_documents`'s row-list already
    excludes it from anyone but its uploader/an admin; the per-document
    route that used to refuse it by name is gone (C-36), so this query is
    now the only exclusion there is).
    """
    # C-05: the bulk-label loop reads every target's labels. Prefetching
    # them here is what lets `labels.document_label_ids` answer from the
    # cache instead of issuing one `values_list` per row -- the same
    # prefetch `DocumentsListView` applies for the same reason.
    if category:
        queryset = Document.objects.filter(
            category__name=category,
        ).exclude(scope=Document.Scope.CONVERSATION)
        # A-3: the one bound this branch had none of. Counted here, before
        # the prefetching return below, so an oversize shelf is refused
        # by name rather than handed back as a queryset for the view to
        # iterate.
        count = queryset.count()
        if count > MAX_BULK_LABEL_TARGETS:
            raise BulkLabelTargetSetTooLargeError(
                f'"{category}" has {count} documents, over the '
                f"{MAX_BULK_LABEL_TARGETS}-document bulk-label limit — "
                "select rows instead."
            )
        return queryset.prefetch_related("entitlement_labels")
    return Document.objects.filter(
        pk__in=list(ids or []),
    ).exclude(scope=Document.Scope.CONVERSATION).prefetch_related("entitlement_labels")


def reencode_all() -> dict:
    """Rebuild vector chunks for every prose Document from its retained
    managed-store copy -- the `rag.embed` role's rematerialize callback
    (registered in `tools/rag/apps.py`, run by
    `models.registry.drift.run_rematerialize`) when an operator repoints
    the embedding binding at a different model/dimension.

    Tabular documents are skipped entirely -- they're never embedded (ADR
    0005), so there's nothing of theirs to re-encode. A NOT-YET-READY
    media-sourced prose Document (video/audio/image, or a scanned PDF,
    T7/T8) is ALSO skipped (T7 review M4b; T8 review M3 corrected the
    semantics to "not READY", never "READY but `extraction` unset" -- see
    the per-document loop's own comment for why that distinction is load-
    bearing, not cosmetic) -- but unlike tabular this is a genuine "not
    yet," not "never": it will be picked up on its own once its own ingest
    completes. A READY prose document, of ANY media type, is never skipped
    by this guard -- see the loop comment for why that's always safe.

    Fails fast, before touching a single document, when the currently-
    resolved `rag.embed` binding doesn't know its own embedding dimension
    (ADR 0010, D1/E2): `embed_dim=None` means a DB-bound connection with no
    recorded dimension, or an explicit `EMBED_MODEL` environment
    override set without its `EMBED_DIM` -- an incomplete choice either way,
    with nothing safe to guess. Re-encoding all N documents
    against an unknown/guessed width would burn every embedding call only
    to fail at the first pgvector insert, so this is checked up front
    instead.

    Two-severity behavior (ADR 0010): before the per-document loop, the
    live chunk table's vector column width (`rag_index.live_embed_dim`) is
    compared against the resolved target dimension. When both are known and
    they differ, per-document deletes cannot work -- every insert would
    fail on a pgvector dimension mismatch against the old column width -- so
    the whole table is dropped up front (`rag_index.drop_chunk_table`) and
    recreated at the new width by the first re-ingest. A same-dim run (or an
    unknown live width -- nothing prose has ever been ingested) keeps the
    per-document delete-then-re-ingest path.

    W5 (ADR 0014 §18) adds a SECOND, independent rebuild trigger asking the
    same question about SHAPE rather than width: `rag_index.
    live_store_shape()` (hybrid_search / use_jsonb) compared against
    `rag_index.desired_store_shape()` (today's `RagSettings.hybrid_search`
    toggle). Either mismatch alone forces the same drop-and-rebuild path --
    a dimension change and a shape change are never both silently
    attributed to the wrong one; the log line names which mismatch actually
    fired. `live_store_shape() is None` (nothing ingested yet) is
    explicitly NOT a shape change, the same way an unknown live embed_dim
    isn't a dimension change -- there's nothing to drop, and the next
    `rag_index.get_vector_store()` call creates the table already in the
    desired shape.

    The vector store is built exactly once for the whole run
    (`rag_index.get_vector_store()`) and threaded through every
    `delete_chunks_for_document`/re-ingest call below, rather than each
    document re-resolving the binding and constructing a fresh
    `PGVectorStore` (which re-runs schema/extension/table-existence checks)
    -- this loop is bulk work over one binding by construction (audit E1).

    For each prose Document: delete its existing chunks
    (`rag_index.delete_chunks_for_document`; skipped on the rebuild path
    -- the table was just dropped), then re-ingest from its retained
    managed-store copy (`ingest.ingest_prose`, reading `doc.source_path`).
    `ingest` is imported locally to avoid an import cycle (ingest.py
    imports this module's sibling `index`/`store`, and ultimately the app
    graph, at module load time).

    A single document's failure (e.g. its stored copy is missing) is
    logged and the loop continues -- but a run that could not re-encode
    every document raises at the end rather than reporting success:
    callers (`models.registry.drift.run_rematerialize`) must not stamp
    the run as a completed materialization when data was left behind.

    Returns:
        `{"documents": n, "reencoded": n}` on success -- n = prose
        documents seen, every one successfully re-ingested. A partial run
        never returns; see Raises.

    Raises:
        ValueError: the resolved `rag.embed` binding has no known embedding
            dimension. Raised before any document is touched.
        RuntimeError: one or more documents failed to re-encode (the
            message names the counts; per-document details are in the log).
    """
    from tools.rag import ingest

    target = resolve(RAG_EMBED_ROLE)
    target_dim = target.embed_dim
    if target_dim is None:
        raise ValueError(
            f"reencode_all: the resolved rag.embed connection {target.model_id!r} "
            f"({target.engine}) has no known embedding dimension; set one for "
            "this connection in the model console (Inference > rag.embed) "
            "before re-encoding."
        )

    live_dim = rag_index.live_embed_dim()
    dim_changed = live_dim is not None and live_dim != target_dim
    # W5 (ADR 0014 §18): the SAME "does the live table still match what's
    # wanted" question as the dimension check above, asked about SHAPE
    # (hybrid_search / use_jsonb) instead of width. `live_store_shape()`
    # returning `None` (nothing ingested yet) is explicitly NOT a shape
    # change -- there's no table to drop, and the very next
    # `rag_index.get_vector_store()` call below creates one already in the
    # desired shape (review S8) -- so `None` is excluded from this
    # comparison the same way `live_dim is None` excludes an unknown width
    # from `dim_changed` above. An install that has never touched
    # `hybrid_search` always has `live == desired` (`use_jsonb` is tied to
    # the same toggle -- see `rag_index.desired_store_shape`'s own
    # docstring), so this can only ever fire for an operator who flipped
    # the toggle and hasn't re-encoded since (review M2b).
    live_shape = rag_index.live_store_shape()
    # W5 review m5: one local, not two `desired_store_shape()` calls --
    # the second (inside the `shape_changed` log line below) used to
    # re-read `RagSettings.get_solo()` a second time for a value already
    # in hand, a needless extra query with nothing to gain from re-reading
    # it (the toggle cannot change mid-function).
    desired_shape = rag_index.desired_store_shape()
    shape_changed = live_shape is not None and live_shape != desired_shape
    rebuild = dim_changed or shape_changed
    if rebuild:
        if dim_changed:
            logger.info(
                "reencode_all: embed_dim changed (%s -> %s); dropping chunk table for full rebuild",
                live_dim,
                target_dim,
            )
        if shape_changed:
            logger.info(
                "reencode_all: chunk table shape changed (live=%s, desired=%s); "
                "dropping chunk table for full rebuild",
                live_shape,
                desired_shape,
            )
        rag_index.drop_chunk_table()

    vector_store = rag_index.get_vector_store()
    # Built once from the `target` already resolved above (for the dimension
    # check), rather than each re-ingested document resolving `rag.embed`
    # and building a fresh embedder -- the same one-resolve-per-run shape
    # `vector_store` already gets (audit E1).
    embed_model = gateway.get_embed_model_for(target)

    documents = 0
    reencoded = 0

    for doc in Document.objects.filter(doc_type=Document.DocType.PROSE):
        # T8 review MAJOR 3 -- corrected semantics: a READY prose Document
        # is NEVER skipped by this guard, regardless of media type or
        # `extraction`. By CONSTRUCTION, a READY document is always safe
        # to re-chunk: `run_ingest_for` only ever reaches READY after
        # `_ingest_prose` succeeds, which means `_source_documents` either
        # found a COMPLETE (`produced_at`-set) extraction sidecar to
        # dispatch through `tools.rag.media.documents_from_extract` (its
        # own completeness gate, M4a, RAISES on an incomplete one -- caught
        # by `run_ingest_or_fail` and turned into FAILED, never READY), or
        # found no sidecar at all and read the file straight off disk via
        # `tools.rag.readers.read_prose_documents` -- an ordinary text
        # document. Both are correct to re-encode unconditionally.
        #
        # The PRIOR version of this guard also skipped a READY document
        # whenever `doc.extraction` was unset -- which is true of EVERY
        # ordinary, non-media prose document this app has ever ingested
        # (only `media.py`'s two drivers ever write that field at all), so
        # every plain text/markdown/docx/PDF document was being probed (or
        # worse) on every single `reencode_all()` run -- the OPPOSITE of
        # the "probed live, milliseconds, only for the rare incomplete
        # case" perf story this comment used to tell. Worse than the perf
        # cost: a READY text-layer PDF whose first few probed pages happen
        # to lack a text layer (a scanned cover page, a figures-only page
        # ahead of real text) probed `False` and was SILENTLY SKIPPED from
        # the rebuild -- on the REBUILD path, its chunks had already been
        # dropped table-wide (`rag_index.drop_chunk_table()`, above) and
        # were then simply never reinserted, with `reencode_all()`
        # reporting a clean `{"documents": n, "reencoded": n}` success
        # despite quietly losing that document's entire index presence.
        # SILENT INDEX DATA LOSS, not a mere perf issue -- the reason this
        # guard now gates on `status != READY` alone, never on
        # `extraction`.
        #
        # For a document that IS still processing (T7 review M4b, video/
        # audio/image, extended T8 for scanned PDFs): skipped here BEFORE
        # it's ever touched, logged once, and NOT counted toward
        # `documents`/`reencoded` at all -- it isn't a failure of this run,
        # it's simply not this run's business yet (without this skip, one
        # such Document would raise INSIDE the try/except below like any
        # other failure, which -- on the REBUILD path -- happens AFTER
        # `rag_index.drop_chunk_table()` already ran, so the eventual
        # `reencoded < documents` raise at the end would have left the
        # table dropped and only partially repopulated; one bad video/pdf
        # must never poison the whole rematerialize).
        if doc.status != Document.Status.READY:
            is_unfinished_media = doc.media_type.startswith(("video/", "audio/", "image/"))
            if not is_unfinished_media and doc.media_type == "application/pdf":
                # A SCANNED OR MIXED PDF (T8, W1) is the one case
                # `media_type` alone can't name -- every PDF, scanned,
                # mixed, or ordinary text, is `"application/pdf"` -- so
                # THIS is the one branch that probes the stored file itself
                # (`tools.rag.readers.pdf_textless_pages`, `limit=1`, W1
                # review S5: bounded to the FIRST textless page -- cheaper
                # than the retired 5-page probe for a genuinely scan-
                # bearing not-READY PDF, the case this guard exists for;
                # NOT strictly cheaper than that probe for a not-READY
                # ALL-TEXT PDF, now scanned in full where the old probe
                # returned at page 1 -- "The scan cost, stated plainly",
                # ADR 0014 §9), and only for a not-yet-READY PDF, never for
                # a READY one (see above) or a non-PDF -- bounded blast
                # radius, a rare state, only during a rematerialize.
                #
                # T8 review MAJOR 2 (probe-reproduced): the probe itself
                # can raise on a corrupt/truncated/encrypted PDF (`pypdf.
                # errors.PdfStreamError` and friends) -- on the REBUILD
                # path, an UNCAUGHT raise here would escape this loop
                # entirely AFTER `drop_chunk_table()` already ran, aborting
                # the whole rematerialize with the table dropped and only
                # partially repopulated -- the exact M4b poisoning this
                # guard exists to prevent, just from a probe failure
                # instead of a transcription one. Caught and logged,
                # FAILING OPEN to `is_unfinished_media = False` -- this
                # document is NOT skipped by this guard; it falls through
                # to the ordinary per-document try/except below, where the
                # SAME corrupt file fails the SAME way any other bad
                # document does (logged, not counted toward `reencoded`,
                # loop continues) rather than a raw exception aborting the
                # entire run mid-loop.
                #
                # `stored_path.is_file() and ...` (W1 review D4): kept
                # verbatim from before this item -- a not-READY Document
                # whose managed-store file is simply missing (deleted out
                # from under it, or never finished staging) must still be
                # skipped, not probed (there's nothing to probe), so this
                # short-circuit stays the LEFT operand of the `and`.
                stored_path = Path(doc.source_path)
                try:
                    # H28 review round 1, finding 4: `pdf_textless_pages`
                    # always returns a `(pages, truncated)` pair now --
                    # `[0]` reaches `pages`, never the truthiness of the
                    # tuple itself (which is truthy regardless of content,
                    # the exact footgun that function's own docstring
                    # names). No `max_examined` passed here -- unbounded,
                    # `truncated` is always `False`.
                    is_unfinished_media = stored_path.is_file() and bool(
                        pdf_textless_pages(stored_path, limit=1)[0]
                    )
                except Exception:
                    logger.exception(
                        "reencode_all: failed to probe Document %s (%s) for a text layer -- "
                        "not skipping it; its own re-encode attempt below will report the "
                        "real error",
                        doc.id,
                        doc.title,
                    )
                    is_unfinished_media = False

            if is_unfinished_media:
                logger.info(
                    "reencode_all: skipping Document %s (%s) -- its transcription/extraction "
                    "hasn't finished yet (status=%s); it will be re-encoded once its own "
                    "ingest completes",
                    doc.id,
                    doc.title,
                    doc.status,
                )
                continue

        documents += 1
        try:
            if not rebuild:
                rag_index.delete_chunks_for_document(doc.id, vector_store=vector_store)
            ingest.ingest_prose(doc, Path(doc.source_path), vector_store=vector_store, embed_model=embed_model)
            reencoded += 1
        except Exception:
            logger.exception("reencode_all: failed to re-encode Document %s", doc.id)

    if reencoded < documents:
        raise RuntimeError(
            f"reencode_all: only {reencoded} of {documents} prose documents "
            "re-encoded; see logs for per-document failures"
        )

    return {"documents": documents, "reencoded": reencoded}


def _citation_snapshot(citations: list[dict]) -> list[dict]:
    """Reduce each retrieval citation dict (`tools.rag.retrieval._vector_
    citations`'s full shape -- source/document_id/title/chunk_id/row_index/
    source_path/score/snippet/page/start_seconds/end_seconds/locator/
    locator_text) down to the fields the Ask/History pages themselves
    surface per citation -- `{"file": <title>, "score": <score>,
    "locator": <display string>, "locator_text": <connector-prefixed
    string>, "page": ..., "start_seconds": ..., "end_seconds": ...}` --
    for `AskRecord.citations`. A snapshot of what was SHOWN, not the
    full internal citation shape, which may change independently of what
    history needs to remember.

    T10: `locator`/`page`/`start_seconds`/`end_seconds` are ADDITIVE keys
    onto the pre-existing `file`/`score` pair -- an `AskRecord` written
    before this change has neither, and `history.html`'s citation renderer
    reads them with Django's own missing-key-is-blank template lookup, so
    an old record renders exactly as it always did (plain "file.md", no
    locator suffix) rather than erroring or growing a stray locator from
    nothing.

    T10 review MINOR 4: `locator_text` (`tools.rag.retrieval.
    locator_text_for`'s connector-prefixed `" at 12:40"`/`", p. 3"`/`""`)
    is the SAME kind of additive key -- an `AskRecord` written before this
    change (or one written by this exact `_citation_snapshot` a moment
    before `locator_text` existed) has no such key, and `history.html`
    reads it the same missing-key-is-blank way, so it renders plain (no
    locator suffix at all, not even the old un-prefixed `locator` value)
    rather than erroring -- the one documented exception to "old snapshots
    keep rendering exactly as before": a pre-T10-review record that DID
    have a bare `locator` used to render it inline via the template's own
    connector `{% if %}`; now that the template only reads `locator_text`,
    such a record renders WITHOUT that suffix until it's asked again and
    re-recorded. `locator`/`page`/`start_seconds`/`end_seconds` are kept
    in the snapshot regardless -- callers that want the raw values still
    have them; only the two templates' OWN rendering moved onto the one
    pre-built `locator_text` field."""
    return [
        {
            "file": citation.get("title"),
            "score": citation.get("score"),
            "locator": citation.get("locator") or "",
            "locator_text": citation.get("locator_text") or "",
            "page": citation.get("page"),
            "start_seconds": citation.get("start_seconds"),
            "end_seconds": citation.get("end_seconds"),
        }
        for citation in citations
    ]


def record_ask(
    *,
    question: str,
    category: str | None,
    connection_name: str,
    model_id: str,
    answer: str,
    citations: list[dict],
    actor,
) -> None:
    """Persist one successfully-answered Ask-page question as an
    `AskRecord`, then prune down to `RagSettings.get_solo().history_limit`
    rows.

    Called by `tools.rag.jobs.run_ask` only after a `rag.ask` job
    successfully answers its question (T6: `AskView` itself only
    enqueues -- it never answers a question or writes history directly
    anymore); the caller wraps this in its own try/except (a history-write
    failure must never break answering) -- this function does not swallow
    its own errors, matching every other function in this module
    (`delete_document`/`reencode_all` let their own failures propagate; the
    never-break-the-job guarantee is enforced at the caller's boundary, not
    duplicated here).

    `actor` -- the `identity.contracts.principals.Principal` this row is
    stamped as (`identity.access.owner_fields`). REQUIRED, not defaulted:
    a default would silently write a row no filter can reason about the
    moment a caller forgot it. `run_ask` runs on the worker, with no
    request behind it, so it reads `actor` off the job's own payload
    (`identity.contracts.principals.principal_from_payload`) -- the actor
    the enqueuing page stamped there.
    """
    AskRecord.objects.create(
        question=question,
        category=category or "",
        connection_name=connection_name,
        model_id=model_id,
        answer=answer,
        citations=_citation_snapshot(citations),
        **owner_fields(actor),
    )
    _prune_ask_records(RagSettings.get_solo().history_limit)


def _prune_ask_records(limit: int) -> None:
    """Delete every `AskRecord` beyond the newest `limit` rows.

    One query to find the cutoff pk (a `LIMIT 1 OFFSET limit` slice, newest
    first) plus one bulk delete -- never loads the rows being kept or the
    rows being deleted into Python. Ordered by `-pk` rather than
    `-created_at`: both are monotonically increasing on insert, but pk has
    no possible tie (two rows can share a `created_at` timestamp; they
    can't share a pk), so it's the one unambiguous "insertion order" this
    table has.
    """
    cutoff = AskRecord.objects.order_by("-pk").values_list("pk", flat=True)[limit : limit + 1]
    cutoff_pk = next(iter(cutoff), None)
    if cutoff_pk is not None:
        AskRecord.objects.filter(pk__lte=cutoff_pk).delete()


def _attach(document, conversation_id, turn_id) -> None:
    """Record `document` as attached to `conversation_id` AND `turn_id`
    (round 13, message-bound attachments) -- `get_or_create`, ALWAYS, the
    identical idempotency rule the retired `tools.rag.views._attach`
    (round 11 review I-5) carried, moved here because that view no
    longer has a caller for it at all (round 13 retires the chat door's
    separate-POST path entirely).

    `turn_id` MOVES TO THE NEWEST TURN on a re-attach (ROUND-13 REVIEW
    FIX, MINOR 4) -- an EXISTING row (a re-upload of bytes already
    attached to this SAME conversation) used to keep whichever turn
    FIRST attached it, documented as a "deliberate, minor, named
    simplification"; the review's own re-read of requirement 3 ("an
    attachment is visually part of the message it was sent with")
    disagreed, since a stale `turn_id` puts the chip on an OLDER
    message than the one the operator just watched carry the file --
    precisely the property requirement 3 asks for, not a simplification
    of it. `DocumentAttachment.turn_id`'s own docstring is updated to
    match.

    THE INVARIANT, ENFORCED HERE (round 12: "a CONVERSATION-scoped
    document carries EXACTLY ONE attachment row"), unchanged by the
    move: `document_scope` is set only at a document's own creation, so
    it cannot itself prevent a SECOND conversation from reaching this
    function with a DIFFERENT `conversation_id` for the identical
    path-deduped document -- refused here, silently (provenance is
    never a gate), same as before.
    """
    if document.scope == Document.Scope.CONVERSATION and DocumentAttachment.objects.filter(
            document=document).exclude(conversation_id=conversation_id).exists():
        logger.warning(
            "stage_turn_attachments: Document %s is conversation-scoped to a different "
            "conversation already; not attaching it to %s too",
            document.pk, conversation_id,
        )
        return
    attachment, created = DocumentAttachment.objects.get_or_create(
        document=document, conversation_id=conversation_id,
        defaults={"turn_id": turn_id},
    )
    # ROUND-13 REVIEW FIX, MINOR 4: `get_or_create` only WRITES `turn_id`
    # via `defaults=` when it creates a fresh row -- an already-existing
    # row (a re-upload of bytes already attached to this conversation)
    # needs its OWN explicit move, here, to the turn that just re-
    # carried it. `!= turn_id` guards a needless UPDATE on the ordinary
    # "created just now" path (where `attachment.turn_id` already equals
    # `turn_id`, having been set by `defaults=` above) and on a legacy
    # row being re-attached by a caller passing its own already-current
    # `turn_id` back -- an UPDATE that would write the identical value
    # is not wrong, only wasted.
    if not created and attachment.turn_id != turn_id:
        attachment.turn_id = turn_id
        attachment.save(update_fields=["turn_id"])


def stage_turn_attachments(principal, *, conversation_id, turn_id, files, workstream_id=None,
                           placement: str = "", remember_placement: bool = False,
                           actor) -> AttachmentUploadResult:
    """THE ONLY STAGING PATH FOR A CHAT-CARRIED FILE (round 13, message-
    bound attachments) -- the registered `agents.contracts.attachments`
    uploader, resolved by `agents.attachments.stage_turn_attachments`,
    called from `agents.chat.service.start_turn` inside the SAME
    transaction that creates the user's own `Turn` row. Nothing uploads
    or ingests until a message is actually submitted (requirement 1):
    there is no other entry point into this function, and choosing files
    in the browser -- with no submit -- reaches no view at all.

    MIRRORS `tools.rag.views.document_upload`'s OWN pre-round-13 chat-
    door loop almost verbatim (that loop is RETIRED from that view --
    see its own docstring), reusing `tools.rag.ingest.enqueue_ingest`/
    `stage_document` exactly as it always did (the "round-12 staging
    machinery" the brief names) rather than reimplementing staging here.
    What is genuinely NEW: `target_dir` is `settings.CHAT_STAGING_DIR`
    (never the watched inbox -- round 12 fix-verify A-1/B-4, unchanged
    by this round), there are no `entitlements`/category fields (the
    chat door never offered either), and every attachment binds to
    `turn_id` as well as `conversation_id` (`_attach`, above).

    `placement` is VALIDATED HERE, defensively, even though `agents.chat.
    service.start_turn` already validates it before ever reaching this
    seam -- never trust one layer alone, the same posture `stage_
    document` takes refusing a scope+workstream combination that should
    already be structurally impossible by the time it gets there.

    PER-FILE OUTCOMES, never a single all-or-nothing failure: an
    unsupported extension is skipped (not staged); an oversized file
    (`RagSettings.max_upload_bytes`) is skipped with its own named
    reason; a duration/page-cap rejection
    (`ingest.MediaDurationExceededError`/`DocumentPageCapExceededError`)
    is its own named reason; a watcher race
    (`FileNotFoundError` -- the SAME race `document_upload`'s own
    docstring has always documented, since this staging directory,
    like the inbox, is written to by this request and nothing else
    concurrently reaches it under ordinary operation, but a slow
    request racing ITSELF via a retried submit is not impossible) is
    counted "queued" only once a Document row is confirmed to actually
    exist at that original path; any OTHER exception is logged and that
    one file is skipped, without aborting the rest of the loop.
    """
    # ROUND-13 REVIEW FIX, NIT 1: `BAD_PLACEMENT_MESSAGE`, not a second
    # hand-typed copy of the sentence -- see its own docstring for why
    # this used to be able to drift from `agents.chat.service._BAD_
    # PLACEMENT`'s identical refusal. CONSOLIDATION WAVE (audit B, S12)
    # extends that to the BRANCH itself: `placement_is_valid` is the one
    # place "resolved workstream -> CHAT_PLACEMENTS_IN_STREAM, else
    # CHAT_PLACEMENTS_LOOSE" is decided, so this defensive re-check (this
    # function's own reason for existing: never trust one layer alone)
    # tests the SAME rule `agents.chat.service.start_turn` already did,
    # not a second copy of it.
    if not placement_is_valid(placement, in_stream=workstream_id is not None):
        return AttachmentUploadResult(ok=False, status=400, error=BAD_PLACEMENT_MESSAGE)

    if placement == "conversation":
        document_scope = Document.Scope.CONVERSATION
        effective_workstream_id = None
    elif placement == "contained":
        document_scope = Document.Scope.UNIVERSAL
        effective_workstream_id = workstream_id
    else:  # "universal"
        document_scope = Document.Scope.UNIVERSAL
        effective_workstream_id = None

    if remember_placement and placement in ("contained", "universal") and workstream_id is not None:
        from agents.workstreams import set_upload_placement_default
        set_upload_placement_default(principal, workstream_id, placement)

    target_dir = (Path(settings.CHAT_STAGING_DIR) / f"chat-{conversation_id}").resolve()
    create_owner_only_dir(target_dir)  # S14/B-8

    from foundation.format import human_bytes

    max_upload_bytes = RagSettings.get_solo().max_upload_bytes
    human_cap = human_bytes(max_upload_bytes)
    upload_exts = ingest.supported_exts()

    queued = 0
    unchanged: list[str] = []
    rejected: list[str] = []
    failed: list[str] = []
    tabular: list[str] = []
    # I-2 (round-13 review fix): every document id whose bytes THIS
    # call physically moved into the managed store -- never the
    # "unchanged" branch (nothing moved, an existing copy was reused)
    # and never the "raced" branch (another request's own move, not
    # this one's -- see the loop below). This is what lets a caller
    # whose SURROUNDING transaction later fails for an unrelated reason
    # (`agents.chat.service.start_turn`'s own enqueue) name exactly
    # which managed-store directories to remove, since a filesystem
    # move has no savepoint to undo it (`AttachmentUploadResult.
    # staged_document_ids`'s own docstring has the full reasoning).
    staged_document_ids: list[int] = []

    # C-14: the per-file write/hash-compare/enqueue body now lives in
    # `ingest.stage_and_enqueue_one`, shared with `views.document_upload`'s
    # identical loop (that function's own docstring has the full "two
    # doors, one body" reasoning). This loop keeps only its own
    # bookkeeping: the `_attach` calls and `staged_document_ids` --
    # `document_upload`'s loop keeps its own (the flashes and label
    # application) instead.
    for upload in files:
        outcome = ingest.stage_and_enqueue_one(
            upload, target_dir,
            category=None, actor=actor, workstream_id=effective_workstream_id,
            document_scope=document_scope, max_upload_bytes=max_upload_bytes,
            upload_exts=upload_exts, log_label="stage_turn_attachments",
        )
        if outcome.kind == "rejected":
            rejected.append(outcome.name)
        elif outcome.kind == "oversize":
            failed.append(f"{outcome.name} (larger than the {human_cap} limit)")
        elif outcome.kind == "refused":
            failed.append(f"{outcome.name} ({outcome.reason})")
        elif outcome.kind == "queued":
            queued += 1
            if outcome.is_tabular:
                tabular.append(outcome.name)
            if outcome.document is not None:
                _attach(outcome.document, conversation_id, turn_id)
                if not outcome.raced:
                    # I-2: only the bytes THIS call physically moved --
                    # never the raced branch, whose move belongs to
                    # whichever request actually won the race (see
                    # `AttachmentUploadResult.staged_document_ids`'s own
                    # docstring).
                    staged_document_ids.append(outcome.document.id)
        elif outcome.kind == "unchanged":
            unchanged.append(outcome.name)
            if outcome.document is not None:
                _attach(outcome.document, conversation_id, turn_id)
        else:
            failed.append(outcome.name)
            if outcome.document is not None:
                _attach(outcome.document, conversation_id, turn_id)
                # This branch is reached when `stage_document` DID move
                # the file and create/update its row -- only the
                # SEPARATE async ingest-queue enqueue afterwards failed
                # (`enqueue_ingest`'s own docstring, outcome (b)). The
                # bytes are still at risk on this same transaction's
                # rollback, exactly as the `job_id is not None` branch
                # above.
                staged_document_ids.append(outcome.document.id)

    # ROUND 13'S OWN CLEANUP (whole-branch review Minor 3's own fix,
    # carried forward): every accepted/unchanged file above is MOVED or
    # unlinked out of `target_dir`, so by here the per-conversation
    # directory is normally empty. Best-effort only -- see
    # `tools.rag.views.document_upload`'s own identical rmdir comment
    # for the full reasoning (this is the same cleanup, one call site
    # over, now that ALL chat staging lands here rather than in that
    # view).
    with contextlib.suppress(OSError):
        target_dir.rmdir()

    return AttachmentUploadResult(
        ok=True, queued=queued, unchanged=tuple(unchanged), rejected=tuple(rejected),
        failed=tuple(failed), tabular=tuple(tabular),
        staged_document_ids=tuple(staged_document_ids),
    )
