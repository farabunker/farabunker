"""What one principal may see of the library, in one place.

IA-2 SHIPS THE LIBRARY'S SPLIT. `visible_ask_records` is IA-1's;
`document_visibility`/`DocumentVisibility`, `readable_documents`,
`listable_documents` and `may_label_document` are IA-2's, now that
documents have labels for them to reason about: the first splits ROWS
from CONTENT, the second answers who may put a label on a document at
all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.db.models import F, Q

from agents.contracts.artifacts import ArtifactFile, mint_artifact
from agents.contracts.workstreams import WorkstreamScope
from foundation.format import single_line
from identity.access import (
    held_entitlement_ids, is_admin, may_see_unlabelled, owned_entitlement_ids,
    owned_rows_q, sees_all_content,
)
from tools.rag.models import AskRecord, Document, DocumentAttachment

logger = logging.getLogger(__name__)

# `inline_text_for`'s own read bound (round-13 fix-verify Important):
# a WHOLE-FILE size threshold, checked before `read_prose_documents` is
# ever called, so a file over this size never gets synchronously
# parsed in full just to keep 4,000 characters of it.
#
# THE ARITHMETIC: `agents.runtime.prompt._INLINE_TEXT_PER_FILE_CHAR_
# BUDGET` (4,000 -- literal here, not imported: `tools/rag` and
# `agents.runtime` may not import one another) is what this extraction
# exists to fill. 20 MiB (20 * 1024 * 1024 = 20,971,520 bytes) is
# roughly 5,243x that budget -- generous enough that the addendum's own
# named use case (a report, a letter, a contract, a resume attached to
# a chat message) reads in full every ordinary time, since real prose
# documents carry FAR more raw bytes per character of eventually-kept
# text than a bare 1:1 ratio would allow for (PDF page/font/markup
# overhead alone routinely runs 10-50x a page's own visible character
# count) -- while still keeping the worst case (`RagSettings.max_
# upload_bytes`'s own 2 GiB default ceiling) roughly 100x SMALLER than
# a full parse of the largest file this box will accept at all. Not a
# partial/truncated READ (`inline_text_for`'s own docstring has the
# full reasoning for why a byte-limited read is the wrong shape for a
# PDF specifically) -- a file over this line skips extraction
# entirely and falls back to the SAME honest "still processing" line
# every other "nothing extractable here" case already renders.
_INLINE_READ_SIZE_CAP_BYTES = 20 * 1024 * 1024

# `image_caption_for`'s own character budget -- the one line about an
# image that lands in a turn's SYSTEM message.
#
# 600, and the reasoning is the same fixed-character-budget one
# `agents.runtime.prompt._INLINE_TEXT_PER_FILE_CHAR_BUDGET` (4,000)
# already states for the carrying turn: a CHARACTER count, never a token
# count, because a token count would need a live tokenizer this side of
# the boundary. Smaller than that budget by a factor of nearly seven
# because this is a different job -- an attachment list that may hold a
# dozen rows needs each line to stay a LINE, so the model can read the
# list and pick one; the full extracted text is still reachable, and the
# prompt block says so, through the retrieval tools.
_CAPTION_CHAR_CAP = 600

# The four things that can be true of an attached image's description,
# and the reason this is a STATE rather than "a string, or blank"
# (preview UAT, 2026-09-17). `image_caption_for` answered `""` for
# several genuinely different situations, the prompt rendered all of
# them as "description not ready yet", and a finished extraction that
# found nothing therefore read as still-running forever -- the chat
# model dutifully told the operator to keep waiting for work that was
# already done.
#
# `pending`     -- no sidecar, or one whose `produced_at` is unset. The
#                  ingest job has not finished. Genuinely "wait".
# `ready`       -- there is a caption. Show it.
# `empty`       -- the description step RAN (`sidecar["described"]`) and
#                  produced nothing, and neither did the transcription.
#                  We looked; there is nothing to say.
# `undescribed` -- a FINISHED sidecar with no text and NO `described`
#                  marker: an image ingested before descriptions existed
#                  at all, whose empty sidecar only ever meant "the OCR
#                  prompt found no text". Calling that `empty` would
#                  claim we looked for a description when we did not
#                  (steward condition S2). Nothing is re-extracted
#                  automatically -- `extract.json` is keyed on the file
#                  hash and never re-produced -- so re-ingesting the
#                  image is what moves it out of this state.
# `n/a`      -- not an image at all. A prose or tabular attachment has
#               no description to be waiting for, and answering
#               `pending` for one (as a first cut did) says "still
#               working" about a document that will never be described
#               -- the exact shape of lie this whole state exists to
#               stop telling about images. The prompt renders NOTHING
#               for it: a non-image bullet keeps the tail it has today.
CAPTION_PENDING = "pending"
CAPTION_READY = "ready"
CAPTION_EMPTY = "empty"
CAPTION_UNDESCRIBED = "undescribed"
CAPTION_NOT_APPLICABLE = "n/a"


def visible_ask_records(principal):
    """`/rag/history/`: own records, or all for a principal that
    `sees_all_content`.

    An Ask record holds a QUESTION and an ANSWER, so it is content, not
    an operational row -- which is why an administrator with the content
    setting off sees only their own.

    `owned_rows_q(principal)` also surfaces what a shell path produced,
    to an admin -- full rule and rationale at its canonical home,
    `identity.access.owned_rows_q` (identity/README.md §5b).
    `manage.py ask` writes no `AskRecord` today, but `rag.ask` enqueued
    from a command would, and a row nobody can see is a row nobody can
    prune.
    """
    qs = AskRecord.objects.all()
    if sees_all_content(principal):
        return qs
    return qs.filter(owned_rows_q(principal))


@dataclass(frozen=True)
class DocumentVisibility:
    """What one principal may see of the library, as plain data.

    BUILT ONCE per request or per turn and THREADED DOWN; never
    re-derived inside a runner, which is the drift the single filter
    point exists to prevent.

    `unrestricted` is `sees_all_content` -- so an administrator with the
    content setting OFF is filtered exactly like a member: they read what
    their entitlements and the library posture allow, and nothing else.

    `stream` is NEW. `None` = a LOOSE turn or a non-stream surface:
    contained documents are excluded and nothing else changes. A
    `WorkstreamScope` = a stream turn: its wall narrows the universal leg
    and its contained/pinned legs are OR-ed in (spec §6.1).

    DEFAULTING TO `None` IS WHAT KEEPS EVERY EXISTING CALL SITE CORRECT,
    exactly as `UNRESTRICTED_TOOL_ACCESS` did in IA-2: `SearchView`,
    `AskView`, the two runners and every test that builds a visibility by
    hand keep working and keep meaning "the universal library", and the
    compiler never tells you about the ones you forgot -- but the
    behaviour they get is the SAFE one, because `None` EXCLUDES contained
    documents rather than including them.
    """

    unrestricted: bool
    entitlement_ids: frozenset[int]
    unlabelled_allowed: bool
    stream: "WorkstreamScope | None" = None
    # NEW (round 12, owner ruling verbatim: "if I submit a document but
    # have scope for chat, then it should only be used in that chat").
    # THE TURN'S OWN CONVERSATION, as a string (matching `agents.
    # contracts.tools.ToolContext.conversation_id`'s own shape) -- `None`
    # for every caller before this round and for any non-chat surface
    # (Search, Ask, `manage.py ask`), which is what keeps a chat-scoped
    # document invisible to every one of them (`tools.rag.retrieval.
    # _visibility_filters`'s own "round 12 amendment" comment has the
    # mechanism). Threaded down, never derived: `tools.rag.tools.
    # run_search`/`run_ask` are the two callers that ever supply it,
    # both straight from `ctx.conversation_id`.
    conversation_id: str | None = None
    # C-03: the principal's own identity, for the conversation-scope
    # axis in `permits()`. `None`/`None` is every caller before this
    # round, and means "this visibility cannot answer an ownership
    # question" -- which `permits()` treats as "not the uploader",
    # the safe direction.
    owner_kind: str | None = None
    owner_key: str | None = None

    @property
    def sees_nothing(self) -> bool:
        """A signed-in principal with no entitlements on a LOCKED
        library. `retrieve_nodes` turns this into an EARLY RETURN, because
        an empty vector-store filter list means "no filter", which means
        EVERYTHING."""
        return (not self.unrestricted and not self.entitlement_ids
                and not self.unlabelled_allowed)

    def permits(self, document, *, workstream_id=None) -> bool:
        """Whether this value lets its principal reach `document`'s
        CONTENT -- the in-memory mirror of `readable_documents`' own
        filter, for a caller that already has the row and its labels
        loaded (`tools.rag.views.DocumentsListView`, over a page it has
        already `prefetch_related("entitlement_labels__entitlement")`ed)
        and must not run a fresh query PER ROW just to decide whether to
        show a link `readable_documents`' own route would 404 on.

        `readable_documents` IS THE QUERYSET HALF this method must agree
        with, row for row: every axis it filters on (containment, the
        conversation scope, labels/entitlements) has a twin clause here,
        and an axis added to one without the other is the exact drift
        this docstring exists to flag for the next person to add one
        (C-03 added the conversation-scope axis to this side; it
        already lived in `readable_documents`).

        Reads `document.entitlement_labels.all()`, which costs no query
        at all when the caller has prefetched it -- the whole reason this
        lives here rather than as a second call to `readable_documents`
        (a query) per row.

        `workstream_id` IS THE CONTAINMENT HALF, and it is not optional
        for correctness: this method is the in-memory mirror of
        `readable_documents`' own filter, and a mirror that answers a
        different question about containment is the exact failure it was
        written to prevent. `None` means the universal library, the same
        safe default `readable_documents` takes.

        CONTAINMENT IS CHECKED FIRST, and outside the `unrestricted`
        branch, because it is a CORPUS rule and not an entitlement rule
        (spec §13): it binds on an open box and it binds for an
        administrator with the content setting on.
        """
        if document.workstream_id is not None \
                and document.workstream_id != workstream_id:
            return False
        # C-03: THE CONVERSATION-SCOPE AXIS, the one `readable_documents`
        # has and this predicate did not. A chat-scoped document is never
        # labelled and never contained, so without this branch it fell
        # through to `unlabelled_allowed` and came back permitted for
        # anybody -- the exact answer `readable_documents` refuses at
        # `not_chat_scoped`. Re-admitted for the uploader and for a
        # principal who sees all content, and only outside a workstream
        # scope, matching that function's `:320-321`/`:329-331` clauses
        # clause for clause.
        if document.scope == Document.Scope.CONVERSATION:
            if workstream_id is not None:
                return False
            return self.unrestricted or (
                self.owner_kind is not None
                and document.owner_kind == self.owner_kind
                and document.owner_key == self.owner_key
            )
        if self.unrestricted:
            return True
        labels = list(document.entitlement_labels.all())
        if not labels:
            return self.unlabelled_allowed
        return any(label.entitlement_id in self.entitlement_ids for label in labels)


def document_visibility(principal, *, settings_row=None, stream=None,
                        conversation_id=None) -> DocumentVisibility:
    """`principal`'s whole library rule, in one value and two queries at
    most.

    THE OPEN BRANCH IS FIRST. `sees_all_content` tests `accounts_on()`
    before it reads a second table, so an open box builds this without
    touching a grant row.

    `stream` is a `WorkstreamScope` for a turn inside a workstream, and
    `None` everywhere else. It is carried, never derived: the value is
    built once per turn (`agents/workstreams.py`, filled by
    `tools/rag/workstreams.py::scope_with_pins`) and threaded down, which
    is the drift the single filter point exists to prevent.

    `conversation_id`, OPTIONAL (round 12): passed straight through to
    `DocumentVisibility` -- see that field's own docstring. UNRESTRICTED
    (`sees_all_content`) STILL CARRIES IT: a chat-scoped document is
    "guaranteed available in its own conversation" for EVERY principal
    running a turn there, sees-all-content or not -- the corpus rule is
    not a permission rule, the identical reasoning `_visibility_filters`'
    own containment/wall comment already gives for `stream`.

    `owner_kind`/`owner_key` (C-03): always `principal`'s own, in both
    branches -- what `DocumentVisibility.permits()`'s conversation-scope
    axis compares a chat-scoped document's `owner_kind`/`owner_key`
    against.
    """
    if sees_all_content(principal, settings_row=settings_row):
        return DocumentVisibility(True, frozenset(), True, stream, conversation_id,
                                  principal.kind, principal.key)
    return DocumentVisibility(
        unrestricted=False,
        entitlement_ids=held_entitlement_ids(principal, settings_row=settings_row),
        unlabelled_allowed=may_see_unlabelled(principal, settings_row=settings_row),
        stream=stream,
        conversation_id=conversation_id,
        owner_kind=principal.kind,
        owner_key=principal.key,
    )


def readable_documents(principal, *, workstream_id=None, settings_row=None):
    """Documents whose CONTENT this principal may reach -- the bytes, the
    transcript, and the chunks retrieval may return.

    `DocumentVisibility.permits()` IS THE ROW HALF this queryset must
    agree with, axis for axis (containment, conversation scope, labels/
    entitlements) -- see that method's own docstring, which names this
    function back.

    OR-MATCH on the labels; the library posture decides the unlabelled
    ones. `.distinct()` because a document carrying two labels the
    principal holds would otherwise be returned twice by the join.

    `workstream_id` IS CONTAINMENT (spec §8.1). `None` -- every existing
    caller, unedited -- means the universal library exactly as today,
    which is the SAFE default: a caller that forgets the parameter sees
    no contained document rather than all of them. A value admits THAT
    stream's contained rows and no other stream's.

    `settings_row`, OPTIONAL (round 11 review, minor 5): passed straight
    through to `document_visibility`, which already accepts it -- a
    caller that already holds one `IdentitySettings` read for its whole
    render/turn (`agents.chat.views.thread.thread_context`, `agents.
    runtime.loop._run_turn`) threads it in rather than paying a second
    read here. `None` -- every caller before this round -- reproduces
    exactly what `document_visibility(principal)` already did.

    THE CONTAINMENT CLAUSE IS OUTSIDE THE `unrestricted` BRANCH, so it
    binds in the open posture and for an administrator with
    `admin_sees_content` on -- it is a corpus rule, not a permission
    rule. What it does NOT do is fence the BYTES from an administrator
    who is admitted to the stream: `rag-document-file` resolves the
    stream first (ruling G), and `visible_workstreams` admits
    `sees_all_content` to every stream, so that administrator arrives
    here with the right `workstream_id` and gets a 200.

    CONVERSATION-SCOPED DOCUMENTS (round 12, owner ruling verbatim: "if
    I submit a document but have scope for chat, then it should only be
    used in that chat") are a SEPARATE axis from containment/labels:
    `Document.scope == "conversation"` is readable ONLY by its own
    uploader (`Document.owner_kind`/`owner_key`, stamped once at
    creation by `tools.rag.ingest.stage_document` -- the identical
    `identity.access.owner_fields` shape `AskRecord` already carries)
    and by `sees_all_content` -- entitlement labels never apply to it (a
    chat-scoped document is never labelled at all; labelling is corpus
    machinery, and this document has no corpus, spec: round 12
    amendment). EXCLUDED from `base` outright, then RE-ADMITTED for the
    uploader ONLY when `workstream_id is None` -- a chat-scoped document
    is never part of ANY stream's own corpus (`tools.rag.workstreams.
    stream_documents`'s own universal leg would otherwise re-admit it
    into every stream's pin picker and document panel, which the owner's
    ruling explicitly forbids: "excluded from every other corpus"), so
    the uploader sees their own chat-scoped rows ONLY on the plain
    library read (`workstream_id=None`, `tools.rag.views.DocumentsView`/
    `readable_document`'s own universal branch), never inside a specific
    stream's own listing.
    """
    contained = Q(workstream__isnull=True) | Q(workstream_id=workstream_id)
    not_chat_scoped = ~Q(scope=Document.Scope.CONVERSATION)
    v = document_visibility(principal, settings_row=settings_row)
    if v.unrestricted:
        # ROUND 12 REVIEW I-2: a first cut left this branch untouched,
        # which leaked a chat-scoped document into EVERY stream's own
        # corpus surface for an unrestricted principal (open/personal
        # box, or `sees_all_content`) -- its `workstream_id` is ALWAYS
        # NULL (never contained), so `contained` above already matched
        # it via the universal disjunct regardless of which stream's
        # own listing was asked for, and this branch applied no further
        # exclusion at all. PROBED (the reviewer's own scenario): under
        # `posture("open")`, `stream_documents(...)` for an UNRELATED
        # workstream returned it, and pinning it there "succeeded"
        # (silently inert, since retrieval still excludes the chunks --
        # but a pin picker offering it is a corpus surface, and the
        # owner's ruling is "excluded from every OTHER corpus").
        #
        # SAME SHAPE AS THE LABELLED BRANCH BELOW: excluded from `base`
        # outright, re-admitted ONLY on the plain library read
        # (`workstream_id is None`) -- `sees_all_content` sees EVERY
        # chat-scoped document there (the brief's own "readable by
        # their uploader + `sees_all_content`" rule), not merely the
        # uploader's own, which is the one respect this branch
        # deliberately differs from the labelled one just below.
        base = contained & not_chat_scoped
        if workstream_id is None:
            base = base | Q(scope=Document.Scope.CONVERSATION)
        return Document.objects.filter(base)
    labelled = Q(entitlement_labels__entitlement_id__in=v.entitlement_ids)
    if v.unlabelled_allowed:
        base = contained & (labelled | Q(entitlement_labels__isnull=True))
    else:
        base = contained & labelled
    base = base & not_chat_scoped
    if workstream_id is None:
        base = base | Q(scope=Document.Scope.CONVERSATION,
                        owner_kind=principal.kind, owner_key=principal.key)
    return Document.objects.filter(base).distinct()


def attached_documents(principal, *, conversation_id, stream=None,
                       settings_row=None) -> list[dict]:
    """Every `Document` attached to `conversation_id` (`tools.rag.
    models.DocumentAttachment`, round 11 review I-5's junction table)
    that this principal may READ, as plain dicts each carrying its own
    `in_corpus` flag -- the ONE query pair both the conversation page's
    own attachments strip and the agent's own prompt steering block read
    through the `agents.contracts.attachments` registry (`agents.
    attachments.attached_documents` is the resolver; THIS function is
    what gets registered, `tools/rag/apps.py::ready()`), so neither
    surface builds its own, possibly-diverging filter.

    `stream`: the conversation's own `agents.contracts.workstreams.
    WorkstreamScope` (`None` for a loose conversation), threaded in by
    the CALLER -- `tools/rag` may cross into `agents.workstreams`/
    `agents.entitlements` but not `agents.models` (import-law rule 2's
    closed set), so the `Conversation` row itself is never this
    module's to read.

    `in_corpus`, PER ROW (round 11 RE-review R-1, replacing this
    function's own prior "never carry a second render state" stance --
    that stance was the bug): a document this principal may READ but
    that a walled stream's CORPUS does not admit still gets returned,
    flagged `False`, rather than dropped -- ATTACHED-MINUS-CORPUS, not
    ATTACHED-INTERSECT-CORPUS. R-1's own scenario is the common one, not
    an edge case: the chat attach door offers PLACEMENT but no
    ENTITLEMENT LABELS (`chat/_attach_files.html` posts `files`/
    `workstream`/`placement`/`next`/`conversation`, never
    `entitlements`), so a UNIVERSAL-placement attach into a walled
    stream is ALWAYS unlabelled, and author decision 5 excludes every
    unlabelled universal document from a non-empty wall's corpus --
    meaning a first cut that only ever returned in-corpus rows made
    THAT attach vanish from both the strip and the prompt with nothing
    to explain why, reproducing the owner's original symptom exactly.
    Round 11 review I-3's OWN honesty goal (never claim `rag__search`
    can return what it cannot) is preserved here -- `in_corpus=False`
    rows are named ON BOTH SURFACES as explicitly NOT retrievable,
    never silently offered as if they were.

    TWO BOUNDED QUERIES, NOT A CORPUS CHANGE (the ruling's own
    mechanism): `readable_documents` (ACL + containment, no wall) finds
    every document this principal may read that is ALSO attached here;
    `stream_documents` (`tools.rag.workstreams.stream_documents`, the
    document-level mirror of `tools/rag/retrieval.py`'s own chunk-level
    corpus formula -- contained ∪ pinned ∪ (universal ∩ wall),
    intersected with `readable_documents`) finds the narrower set that
    is ALSO in-corpus. The second is a `pk`-only `values_list`, not a
    second full row fetch, and it runs AT ALL only when `stream` is
    given -- a loose conversation has no wall to narrow by, so every
    attached-and-readable row is trivially in-corpus and the second
    query is skipped outright (`in_corpus_ids is None` below), matching
    this function's query cost before R-1 for the common loose-
    conversation case. Neither query scales per attachment -- GC-6.

    `settings_row`, OPTIONAL (round 11 review, minor 5): threaded
    straight through to both `readable_documents`/`stream_documents`
    calls, both of which accept it for the identical reason -- a caller
    that already holds one `IdentitySettings` read for its own
    render/turn passes it in rather than paying a second.

    NAMED FOLLOW-UP, NOT THIS ROUND'S FIX (round 11 re-review R-1's own
    ruling; PARTIALLY DELIVERED by round 12's own owner ruling instead --
    see the next paragraph): giving an attachment its own leg in the
    retrieval corpus itself -- "guaranteed consultation" regardless of
    the wall -- was named as a spec-level owner decision, not a call
    this function could make unilaterally. WALLED-UNIVERSAL attachments
    still work exactly as R-1 left them (honesty, not reach). But
    CONVERSATION-SCOPED attachments (below) now DO get guaranteed
    availability, because the owner's OWN round 12 ruling delivered
    exactly that for the chat-scope case specifically.

    CONVERSATION-SCOPED ATTACHMENTS (round 12, owner ruling verbatim:
    "if I submit a document but have scope for chat, then it should
    only be used in that chat"), fetched UNCONDITIONALLY and merged in
    -- NOT filtered through `readable_documents` at all, and always
    `in_corpus=True`. TWO REASONS this is its own branch rather than
    riding `readable_documents`' own new chat-scope clause (that clause
    admits the FILE to its uploader/admin ONLY):

    (a) STRIP VISIBILITY vs FILE ACCESS ARE DIFFERENT QUESTIONS for a
    chat-scoped document specifically (round 12 brief's own
    reconciliation of the round 11 "readable-filtered" comment this
    docstring paragraph replaces): the document IS this conversation's
    own content by the owner's ruling, so its TITLE and STATUS belong on
    the strip/prompt for ANY principal who can already see this
    conversation -- both this function's callers are only ever invoked
    for such a principal (`agents.chat.views.thread.thread_context`
    sits behind `ConversationView`'s own visibility gate; the prompt's
    caller IS the turn's own acting principal) -- while the underlying
    BYTES stay uploader/admin-only through `rag-document-file`'s own
    `readable_document` call, unaffected by this branch.

    (b) GUARANTEED IN-CORPUS -- BUT HONESTLY SO (round 12 review I-3): a
    chat-scoped attachment is part of its own conversation's retrieval
    corpus on EVERY posture (`tools.rag.retrieval._visibility_filters`'s
    own "round 12 amendment" leg is exempt from the entitlement gate) --
    no wall arithmetic, no LOCKED-library exception. The ONE case this
    function still must not over-promise: `DocumentVisibility.
    sees_nothing` (a signed-in principal with ZERO entitlements on a
    locked library) short-circuits `retrieve_nodes` to an empty result
    BEFORE `_visibility_filters` is ever called at all -- a coarser,
    EARLIER gate the conversation leg's own exemption cannot reach past,
    by design (the ruling's own required test: "the sees_nothing
    principal still gets nothing"). `in_corpus` for a chat-scoped row is
    therefore `not sees_nothing` for THIS principal, not an unconditional
    `True` -- computed from the SAME `document_visibility` value
    `retrieve_nodes` itself would build, so the strip/prompt can never
    claim a retrieval that principal's own turn would come back empty on.

    MERGED BY `created_at`, not appended after: the combined list still
    reads as ONE chronological attachment history, whichever branch
    supplied each row.
    """
    from tools.rag.workstreams import scope_with_pins, stream_documents

    # ROUND 13 (message-bound attachments): `turn_id` annotated straight
    # off the join, not a second query -- `uniq_document_attachment`
    # guarantees exactly one `attachments` row per (document,
    # conversation_id) pair, so filtering on `attachments__
    # conversation_id=conversation_id` already narrows the join to that
    # ONE row before `F("attachments__turn_id")` ever reads it; there is
    # no fan-out to worry about the way there would be for a document
    # attached to several conversations at once (a different join row
    # entirely, excluded by the same filter).
    #
    # (a)/(b) above -- see this function's own docstring paragraph.
    chat_scoped = list(
        Document.objects.filter(scope=Document.Scope.CONVERSATION,
                                attachments__conversation_id=conversation_id)
        .annotate(turn_id=F("attachments__turn_id"))
    )
    chat_scoped_ids = {d.pk for d in chat_scoped}
    # ONLY PAID WHEN THERE IS SOMETHING CHAT-SCOPED TO ANSWER FOR (the
    # same "second query only when there is something to narrow"
    # discipline the in_corpus_ids computation below already follows) --
    # the common case, no chat-scoped attachment on this conversation,
    # costs nothing extra here.
    chat_scoped_in_corpus = True
    if chat_scoped:
        chat_scoped_in_corpus = not document_visibility(
            principal, settings_row=settings_row).sees_nothing

    # WHICH CHAT-SCOPED ROWS THIS PRINCIPAL MAY ACTUALLY READ (packet
    # finding A, 2026-09-17). The `attached` leg below came OUT of
    # `readable_documents`, so every row in it is readable by
    # construction and needs no check; the chat-scoped leg above is
    # deliberately NOT readable-filtered (this function's own docstring:
    # TITLE and STATUS belong to anyone who can see the conversation,
    # the BYTES stay uploader/admin-only) -- and a CAPTION is content,
    # not a title. ONE bounded `pk__in` query, paid only when there IS
    # something chat-scoped to answer for, the same "second query only
    # when there is something to narrow" discipline `in_corpus_ids`
    # below already follows.
    chat_scoped_readable_ids: set[int] = set()
    if chat_scoped:
        chat_scoped_readable_ids = set(
            readable_documents(principal, settings_row=settings_row)
            .filter(pk__in=chat_scoped_ids).values_list("pk", flat=True)
        )

    scope = scope_with_pins(stream)
    readable_workstream_id = scope.workstream_id if scope is not None else None
    attached = list(readable_documents(
        principal, workstream_id=readable_workstream_id, settings_row=settings_row,
    ).exclude(pk__in=chat_scoped_ids).filter(attachments__conversation_id=conversation_id)
     .annotate(turn_id=F("attachments__turn_id")))

    # THE THIRD QUERY RUNS ONLY WHEN THERE IS SOMETHING TO NARROW
    # (query-cost pin, `test_thread.py::TestTheAttachDoorsQueryDiscipline
    # ::test_a_non_empty_wall_costs_exactly_one_more_grant_read`): `attached`
    # is materialised (`list(...)`, above) FIRST, and the common case for
    # most conversation renders -- no non-chat-scoped attachments at all
    # -- pays no extra entitlement-grant read for a wall check with
    # nothing to check against.
    in_corpus_ids = None
    if scope is not None and attached:
        in_corpus_ids = set(
            stream_documents(principal, scope, settings_row=settings_row)
            .filter(attachments__conversation_id=conversation_id)
            .values_list("pk", flat=True)
        )

    combined = [(d, chat_scoped_in_corpus, True) for d in chat_scoped] + [
        (d, True if in_corpus_ids is None else d.pk in in_corpus_ids, False) for d in attached
    ]
    combined.sort(key=lambda triple: triple[0].created_at)

    # PREVIEW UAT (2026-09-17): the STATE travels with the text, so the
    # prompt can tell "still working" from "we looked and found nothing"
    # from "this predates descriptions" -- facts that all used to arrive
    # as the same `""` and all rendered as "not ready yet". Computed
    # once per row here rather than twice inside the comprehension.
    #
    # BLANKED FOR A ROW THIS PRINCIPAL MAY NOT READ (packet finding A;
    # review fix round 2, non-blocking (ii): BOTH fields now, not text
    # alone). `caption_state` is a fact about the EXTRACTION ("did it
    # run, did it find anything"); once `readable` is `False` that fact
    # is no more this principal's to know than the caption text itself
    # is -- `agents/` receives `"n/a"` here, the same value a genuinely
    # non-image row gets, and neither the text nor the real state ever
    # crosses this seam. `_attachment_line_tail`'s own `readable` check
    # runs FIRST and renders the withheld line unconditionally regardless
    # of `caption_state`, so this blanking changes no rendered output --
    # it closes the seam itself, not merely what the prompt happens to
    # read off it.
    captions = {}
    for d, _in_corpus, is_chat_scoped in combined:
        readable = (d.pk in chat_scoped_readable_ids) if is_chat_scoped else True
        state, text = image_caption(d)
        captions[d.pk] = (
            readable,
            state if readable else CAPTION_NOT_APPLICABLE,
            text if readable else "",
        )

    return [
        {
            "id": d.pk, "title": d.title, "status": d.status, "status_detail": d.status_detail,
            "in_corpus": in_corpus,
            # ROUND 12 REVIEW NIT 2: "chat_scoped", so the strip can tell
            # a chat-scoped attachment apart from an ordinary one -- "the
            # brief made the badge optional" (its own words), but "from
            # inside the chat there is no way to tell which files are
            # chat-only" was the exact fact this round exists to make
            # legible, so the conversation page's own template now uses
            # this key for a small chip.
            "chat_scoped": chat_scoped,
            # ROUND 17 (owner: "a bool in settings to use all rag
            # documents ... default it on"): WHY a non-chat-scoped row
            # is `in_corpus=False`, so the strip/chip can name the
            # actual cause instead of always blaming the wall --
            # `"universal_off"` when this stream's own `include_
            # universal` is False (the whole universal leg dropped,
            # regardless of the wall), `"wall"` otherwise (the pre-
            # round-17 cause, unchanged: an unlabelled or wrongly-
            # labelled universal document against a non-empty wall).
            # `None` whenever there is nothing to explain -- `in_corpus`
            # is True, OR this row is chat-scoped (a DIFFERENT, PRE-
            # EXISTING mechanism -- `sees_nothing`, not the wall/toggle
            # formula this field describes -- left alone here, out of
            # this round's own named scope). COMPUTED, NEVER A QUERY:
            # `scope` is the one `WorkstreamScope` this whole function
            # call already resolved.
            "not_in_corpus_reason": (
                ("universal_off" if scope is not None and not scope.include_universal
                 else "wall")
                if not in_corpus and not chat_scoped else None
            ),
            # ROUND 13 (message-bound attachments): which turn's own
            # bubble this chip renders on (`None` for a legacy/
            # unattributed row -- see `DocumentAttachment.turn_id`'s own
            # docstring), and whether THIS principal specifically may
            # remove it (uploader-only, `may_detach_attachment`, below --
            # computed here, where `principal` and `d` are both already
            # in scope, rather than re-derived by the template).
            "turn_id": d.turn_id,
            "may_detach": may_detach_attachment(principal, d),
            # CHAT IMAGE ARTIFACTS (2026-09-16). Three keys, all read
            # off the row and its already-stored extraction, NEVER from
            # a model call -- the owner addendum's own rule, and the
            # reason `caption` reads a sidecar rather than describing
            # anything itself.
            #
            # `reference` IS MINTED FOR EVERY ROW, not only images: a
            # prose attachment is equally nameable by a future tool with
            # a file input, and minting it HERE is what lets the prompt
            # and the page name it at all -- `agents/` may not import
            # this column to mint its own. WITH the title, which
            # `mint_artifact` percent-encodes, so a hostile filename
            # inside a reference is inert text rather than extra lines.
            "is_image": (d.media_type or "").startswith("image/"),
            "reference": mint_artifact("document", d.pk, d.title),
            "readable": captions[d.pk][0],
            "caption_state": captions[d.pk][1],
            "caption": captions[d.pk][2],
        }
        for d, in_corpus, chat_scoped in combined
    ]


def delete_attachments(conversation_id) -> int:
    """Delete every `DocumentAttachment` row naming `conversation_id`,
    and return the count of ATTACHMENT CLAIMS removed (round 11
    re-review minor 4) -- the ONE function registered as `tools/rag/
    apps.py::ready()`'s cleanup provider (`agents.contracts.attachments.
    register_attachment_cleanup`), called from `agents.visibility.
    delete_conversation` when a conversation is deleted, through the
    resolver `agents.attachments.delete_attachments_for`.

    ROUND 12 SPLITS THIS INTO TWO CASES, by the owner's own ruling ("if
    I submit a document but have scope for chat... it should only be
    used in that chat" -- read the other way, a chat-scoped document
    has no life OUTSIDE its chat either):

    - `Document.scope == "conversation"`: the DOCUMENT ITSELF is
      deleted (`tools.rag.services.delete_document` -- vector chunks,
      the managed-store FILES, and the row), not merely the attachment
      claim. Safe by the invariant this column enforces at write time
      (`tools.rag.views._attach`'s own docstring): a chat-scoped
      document carries EXACTLY ONE attachment row, for THIS
      conversation, so nothing else could still be pointing at it.
      `delete_document` cascades the `DocumentAttachment` row too
      (`DocumentAttachment.document` is `on_delete=CASCADE`) -- no
      separate delete needed for those rows.

    - Every other attachment (universal or stream-contained,
      round 11's original provenance-only kind): UNTOUCHED beyond the
      `DocumentAttachment` row itself. An attachment is a CLAIM a
      conversation makes on a document (`DocumentAttachment`'s own
      docstring), never the document's own existence -- deleting a
      conversation must not delete, or even touch, a document another
      conversation (or the universal library itself) may still hold a
      claim on, or may still simply contain.

    `.delete()` ON THE FILTERED QUERYSET for the second case, not a
    loop: after the chat-scoped documents above are gone (their own
    attachment rows cascaded away with them), this filter names exactly
    the REMAINING rows, deleted in one query.
    """
    from tools.rag import services

    chat_scoped_docs = list(Document.objects.filter(
        scope=Document.Scope.CONVERSATION, attachments__conversation_id=conversation_id))
    for document in chat_scoped_docs:
        services.delete_document(document)

    total, _ = DocumentAttachment.objects.filter(conversation_id=conversation_id).delete()
    return total + len(chat_scoped_docs)


def remove_staged_documents(document_ids) -> None:
    """Delete the managed-store DIRECTORY for each id in `document_ids`
    (round-13 REVIEW FIX, I-2) -- the ONE function registered as
    `tools/rag/apps.py::ready()`'s orphan-cleanup provider (`agents.
    contracts.attachments.register_attachment_orphan_cleanup`), called
    from `agents.attachments.cleanup_staged_documents` when `agents.
    chat.service.start_turn`'s own transaction rolls back AFTER
    `stage_turn_attachments` already moved real bytes into the store.

    NO `Document.objects.filter(...).delete()` HERE, deliberately: by
    the time this runs, the row those ids named was ALREADY rolled back
    by the very transaction failure that triggered this call (Django's
    own `transaction.atomic()` semantics) -- there is nothing left in
    the database to delete. `tools.rag.store.remove_document_files` is
    a pure FILESYSTEM operation, safe to call for an id whose row does
    not exist (its own docstring: "safe to call when the directory
    doesn't exist"), which is exactly the shape this caller needs: best-
    effort removal of bytes that never had a durable row pointing at
    them, never a second database write in an already-failed request.
    """
    from tools.rag import store

    for doc_id in document_ids:
        try:
            store.remove_document_files(doc_id)
        except OSError:
            logger.exception(
                "remove_staged_documents: could not remove managed-store files for "
                "document %s", doc_id,
            )


def containing_workstream_id(doc_id) -> int | None:
    """The stream a document is contained in, or `None` for a universal
    one -- and `None` for a document that does not exist, which the
    caller's own 404 then covers.

    HERE, NOT IN THE VIEW, because `foundation/ops/tests/
    test_column_boundaries.py::test_rag_views_reads_documents_through_
    the_access_module` forbids `tools/rag/views.py` from touching
    `Document.objects` at all, for the reason that test's own docstring
    gives: counts are a reading surface too. This is a one-column
    `values_list`, not a visibility question -- but it is a `Document`
    question, and the gate is flat.
    """
    return Document.objects.filter(pk=doc_id).values_list(
        "workstream_id", flat=True).first()


def readable_document(principal, doc_id):
    """One `Document` this principal may read the CONTENT of, by primary
    key -- or `None` if it does not exist or its content is not theirs.

    THE TWO-STEP CONTAINMENT RESOLUTION (spec §8.1), extracted here
    because it was typed identically at three call sites in `tools/rag/
    views.py` (`document_file`, `document_transcript`, and
    `workstream_pin`, the last in an inverted-but-equivalent form). A
    document CONTAINED somewhere is reachable only from inside ITS OWN
    stream, so the stream is resolved FIRST -- `agents.workstreams.
    workstream_scope` (the seam; a function-local import, matching every
    other `tools/rag` reach into it) answers `None` for a stream this
    principal may not be in, and the caller cannot tell that apart from
    "no such stream" -- and only then is `readable_documents` asked, WITH
    that stream id, so the row is admitted rather than filtered out by
    containment's safe default. Asking plain `readable_documents(
    principal)` (the universal-only default) would refuse a contained
    document before any of its own named refusals (`pin_document`'s
    "already lives in a workstream", say) ever got to run.

    RULING G: an administrator with `admin_sees_content` on is admitted
    to every stream by `visible_workstreams`' first branch, so this pair
    still returns the document for them -- containment fences the
    CORPUS, not the bytes.
    """
    from agents.workstreams import workstream_scope

    row = containing_workstream_id(doc_id)
    if row is not None and workstream_scope(principal, row) is None:
        return None
    return readable_documents(principal, workstream_id=row).filter(pk=doc_id).first()


def artifact_file_for(pk: int, principal) -> ArtifactFile:
    """Where `document:<pk>`'s BYTES are, for a principal who may read
    them -- the registered `agents.contracts.artifacts` file resolver
    for the `document` kind (`tools/rag/apps.py::ready()`), resolved at
    call time by whichever column was handed the reference.

    THE GATE IS `readable_document`, NOT A SECOND FILTER: the same
    two-step containment resolution `rag-document-file` itself applies
    to the same bytes, so a reference can never reach a file that route
    would 404 on -- and a chat-scoped attachment stays uploader/admin
    only here exactly as it is there.

    ONE EXCEPTION FOR THREE CONDITIONS. `LookupError` covers "no such
    row", "the row has no file on disk", AND "this principal may not
    read it" -- the caller must not be able to tell them apart. That is
    the identical rule `tools.vision.services._visible_referenced_row`
    already enforces for its own kinds (IA-1): a distinguishable refusal
    is an existence oracle over another principal's attachments.

    Returns a PURE VALUE, never a `Document` and never a file handle:
    this crosses a column boundary, and the receiving column knows the
    three-field contract without knowing this one's ORM.
    """
    document = readable_document(principal, pk)
    if document is None:
        raise LookupError(f"document:{pk} does not name a readable document.")
    source = Path(document.source_path or "")
    if not source.is_file():
        raise LookupError(f"document:{pk} has no file on disk.")
    return ArtifactFile(
        path=str(source), name=source.name, media_type=document.media_type or "",
    )


def listable_documents(principal):
    """Document ROWS this principal may see -- title, category, labels,
    status, size, ingest state.

    `is_admin`, NOT `sees_all_content`. Labelling, deleting and
    re-ingesting a document are administration, and an administrator MUST
    be able to label a document they are not cleared to read -- otherwise
    the first label on a sensitive document can only be applied by
    somebody who does not need it, which is the wrong way round.

    The ROW is deliberately the whole of what this widens:
    `readable_documents` still gates the bytes, the transcript and every
    chunk retrieval can return, so an administrator with the content
    setting off can put a document in an entitlement without ever seeing
    a word of it. A member's listing is `readable_documents`, so nothing
    widens for them.

    CONVERSATION-SCOPED DOCUMENTS ARE THE ONE EXCEPTION TO THE ROW
    WIDENING (round 12, owner ruling): labels never apply to a chat-
    scoped document (it has no corpus for a label to govern), so there
    is nothing an administrator with content OFF could DO with one they
    do not already own -- the `is_admin` widening's own justification
    ("must be able to label a document they are not cleared to read")
    does not hold for a document that can never be labelled at all. So
    a chat-scoped row belonging to somebody else is excluded even from
    the ADMIN listing, unless `sees_all_content` -- the brief's own
    "chat badge... excluded from other users' listings" line, applied
    to the row-list too, not only to content-reachability.
    """
    if is_admin(principal):
        rows = Document.objects.all()
    else:
        rows = readable_documents(principal)
    if not sees_all_content(principal):
        rows = rows.exclude(
            Q(scope=Document.Scope.CONVERSATION)
            & ~Q(owner_kind=principal.kind, owner_key=principal.key)
        )
    return rows


def is_owner(principal, document) -> bool:
    """Whether `principal` is the uploader of `document` (B-2).

    `Document.owner_kind`/`owner_key` (`tools/rag/models.py:216-217`)
    against the principal's own `kind`/`key` -- the same two-column
    convention `identity.access.owner_fields(principal)` writes those
    columns with when `tools.rag.ingest.stage_document` first creates the
    row for a real `actor`. Not `owner_fields(principal) == {...}`: that
    would allocate a dict on every call for a two-field compare a caller
    may run once per row of a page.

    Blank for a document staged before `actor` existed, and for one
    staged with `actor=None` -- `manage.py ingest` and the
    notes-consolidation job, the two shapes
    `tools.rag.ingest._acts_for_the_box` covers that carry no principal
    at all -- so both read as "nobody uploaded this" rather than
    raising. NOT BLANK FOR THE WATCHER: its real actor is
    `SERVICE_PRINCIPAL`, so `stage_document` stamps
    `owner_kind="service"` on its rows like any other principal's, and
    they read as owned by the box rather than by nobody. No database
    query: both columns are already on `document`.

    NOT BLANK FOR A NOTES-CONSOLIDATION DOCUMENT (round-3 hardening
    H32/C-3, round 2) -- the SECOND writer of these two columns:
    `stage_document` itself still runs `actor=None` for that job (the
    CLI's own exemption, `tools.rag.ingest._acts_for_the_box`, still
    applies to it, and the B-3 containment refusal for a `NOTES_DIR`
    path still precedes any re-stage regardless), but `tools.rag.jobs.
    run_consolidate` stamps `owner_kind`/`owner_key` directly onto the
    row immediately afterward, from the CONVERSATION's own two columns.
    A note therefore reads as OWNED by the conversation's owner through
    this exact predicate, the same as any browser-uploaded row -- the
    single creation-only writer this docstring used to describe is no
    longer the whole story for that one `Document.origin`.

    PUBLIC, not `_is_owner` (round-1 review, minor): every other
    cross-module call into this file from elsewhere in `tools/rag`
    (`document_visibility`, `may_administer_document`, `readable_
    documents`) names a public function, and this one is no different --
    `tools.rag.ingest` calls it by name, from a different file in the
    same app.

    Two readers in `tools.rag.ingest`, both in `stage_document`'s
    re-stage branch: `_may_restage`'s own authorization decision, and the
    entitlement-clearing check beside it (an authorized takeover that is
    NOT by the uploader -- an administrator, or an entitlement OWNER,
    never a mere entitlement HOLDER -- is still not the same principal,
    so it clears labels/containment nobody re-applied and re-stamps
    `owner_fields`).
    """
    return bool(document.owner_kind) and document.owner_kind == principal.kind \
        and document.owner_key == principal.key


def may_label_document(principal, document, *, is_admin_=None, owned=None) -> bool:
    """Whether `principal` may add or remove THIS document's labels, and
    delete or re-ingest it.

    `is_admin`, or an OWNER of one of the document's current
    entitlements. The identity-side spelling of the same predicate is
    `identity.services.may_administer_entitlement`; this column cannot
    import that module (it is column-private), so it asks the two facts
    through the sanctioned seam instead.

    An UNLABELLED document has no entitlement owner, so only an
    administrator may act on it -- which is the honest answer: there is
    nobody with a claim to delegate from.

    `is_admin_` AND `owned` MAY BE PRE-COMPUTED, for a caller answering
    this once per row of a page rather than once for a single document
    (`tools.rag.views.DocumentsListView`, the whole-branch review's own
    fix): both are per-PRINCIPAL facts, identical on every row of one
    render, so a caller that recomputes them per row pays two extra
    identity queries per row for nothing. `None` (every other caller, all
    single-document routes) computes them here exactly as before.

    THE LABEL CHECK ITSELF READS `document.entitlement_labels.all()`,
    never `.filter(...).exists()`: the `.all()` form costs NO extra query
    at all when the caller has prefetched it (`DocumentVisibility.
    permits`'s own docstring gives the identical reason), where a
    `.filter()` call is a fresh query regardless of what the caller
    prefetched.
    """
    if is_admin_ is None:
        is_admin_ = is_admin(principal)
    if is_admin_:
        return True
    if owned is None:
        owned = owned_entitlement_ids(principal)
    if not owned:
        return False
    return any(label.entitlement_id in owned for label in document.entitlement_labels.all())


def may_administer_document(principal, document, *, is_admin_=None, owned=None) -> bool:
    """Whether `principal` may DELETE or RE-INGEST this document --
    `may_label_document`'s own predicate (above), WIDENED by exactly one
    more admitting clause (round 12 whole-branch review A-2/B-2): the
    UPLOADER of THEIR OWN conversation-scoped document.

    A SEPARATE FUNCTION, not a change to `may_label_document` itself,
    because that predicate's own docstring already governs THREE
    actions -- "add or remove THIS document's labels, and delete or
    re-ingest it" -- and a chat-scoped document is deliberately still
    NEVER labellable (round 12's own exclusion -- enforced by `services.
    documents_targeted_for_labelling`'s target query since C-36 deleted
    the per-document route whose explicit 403 used to carry it, not
    touched by this function). Widening `may_label_document` in place
    would have silently reopened labelling for a document that has no
    corpus for a label to govern; this function's own callers
    (`document_delete`, `document_reingest`) are the two administration
    routes labelling was never the gate for in the first place.

    THE BRIEF'S OWN STATED REASON for showing a chat-scoped row at all
    ("find/delete path", round 12's own orchestrator assumption) is what
    this closes: before this function existed, a non-admin uploader saw
    their own row (with the "chat" badge) but no Delete and no Re-ingest
    button, and a direct delete POST answered 403 with the row intact --
    the ONLY way to remove the document was to delete the whole
    conversation.

    `is_admin_`/`owned`, OPTIONAL, forwarded straight through to `may_
    label_document` for the identical pre-computation reason that
    function's own docstring gives.
    """
    if may_label_document(principal, document, is_admin_=is_admin_, owned=owned):
        return True
    return (document.scope == Document.Scope.CONVERSATION
            and document.owner_kind == principal.kind
            and document.owner_key == principal.key)


def may_detach_attachment(principal, document) -> bool:
    """Whether `principal` may remove THIS attachment from a chat turn's
    own chip (round 13, requirement E: "uploader-only ✕ on the chip") --
    UPLOADER-ONLY, deliberately NARROWER than `may_administer_document`
    above: no admin OR-clause, and not limited to a chat-scoped document
    either. `document.owner_kind`/`owner_key` are stamped at CREATION for
    every browser-upload door (`tools.rag.ingest.stage_document`'s own
    "applied at creation, never re-read on a re-stage" rule -- still true
    for that call itself), so this is asking "did THIS principal bring
    this specific file into the library at all", the same fact for a
    chat-scoped, contained, or universal document alike -- the brief
    names no admin override for this one action, and an administrator
    who needs to remove someone else's attachment already has the
    library's own Delete (`may_administer_document`, still
    admin-widened) for a chat-scoped row, or can unlabel/manage a
    universal one through the ordinary label page.

    NOT THE WHOLE STORY FOR A NOTES-CONSOLIDATION DOCUMENT (round-3
    hardening H32/C-3, round 2): that row's `owner_kind`/`owner_key` are
    stamped by a SECOND writer, `tools.rag.jobs.run_consolidate`, AFTER
    `stage_document` returns -- see `is_owner`'s own docstring, above in
    this file, for the full account. A note is never attached to a chat
    turn's own chip (it has no `DocumentAttachment` at all, being
    workstream-contained rather than chat-scoped), so this predicate is
    never actually asked about one in practice; named here only so this
    docstring's own creation-only claim is not read as universal.

    A document uploaded before `owner_kind`/`owner_key` existed (every
    row predating round 12) has both blank, which never equals a real
    principal's `kind`/`key` -- an honest `False` for everyone, uploader
    included, rather than a guess. There is no administration escape
    hatch for THAT row through this predicate; the library's own Delete
    still reaches it for an admin.
    """
    return (document.owner_kind == principal.kind
            and bool(document.owner_key)
            and document.owner_key == principal.key)


def inline_text_for(doc_id) -> str:
    """`doc_id`'s own text, extracted MODEL-FREE and SYNCHRONOUSLY
    (OWNER ADDENDUM, 2026-09-08: "when I add a file to the chat, it
    shouldn't have to do rag to find it... have that as a reference
    within the context") -- the registered `agents.contracts.
    attachments` text provider, resolved by `agents.attachments.
    inline_attachment_text`, called from `agents.runtime.prompt.
    _carrying_attachments_block` at TURN-BUILD time.

    REUSES `tools.rag.readers.read_prose_documents` -- the SAME
    model-free reader `tools.rag.ingest.stage_document`'s own async
    `rag.ingest` job eventually calls for chunking -- NEVER a model
    call: this runs inline inside a turn, before that job may even have
    started (`document.source_path` already exists the moment `stage_
    document` returns, synchronously, well before its `status` reaches
    READY), and it must never wait on that job (ruling 2: "NEVER block
    or delay the turn on ingestion").

    `""` -- extraction is not even ATTEMPTED, never merely "came back
    empty" -- for a medium `read_prose_documents` was never built to
    read: tabular (rows, not prose) and every media type (video/audio/
    image need transcription/vision, both model calls this function
    must not make). For an actual PROSE extension (`tools.rag.readers.
    PROSE_EXTS`), `""` covers three more honestly-indistinguishable
    cases the caller renders identically (an honest "still processing"
    line): the file does not exist yet at `source_path` (staged but the
    managed-store write has not landed -- see `stage_document`'s own
    atomicity), a scanned/textless PDF (every page comes back with no
    text layer -- `read_prose_documents`'s own page-per-`Document`
    shape means "no pages, or every page blank" IS "no extractable
    text", not a distinct condition to detect), or the reader itself
    raising (a corrupt file, an unsupported encoding).

    THE READ ITSELF IS BOUNDED, NOT ONLY THE RESULT (round-13 fix-verify
    Important, uncovered while re-probing the disputed memoization
    nit): `read_prose_documents` has no partial-read mode of its own --
    `_read_text` reads the WHOLE file, `_read_pdf` walks and extracts
    EVERY page -- so calling it unconditionally means a perfectly legal
    upload at `RagSettings.max_upload_bytes`'s own default ceiling (2
    GiB) gets fully parsed into memory, synchronously, inside the
    worker's prompt build, on every carrying turn, just to keep the
    caller's own 4,000-character per-file budget
    (`agents.runtime.prompt._INLINE_TEXT_PER_FILE_CHAR_BUDGET` --
    literal here rather than imported, `tools/rag` and `agents` may not
    import one another either direction) -- a self-inflicted latency/
    memory event on a box whose own operating notes already treat
    unified-memory pressure as a live hazard, and the opposite of the
    addendum's own "NEVER block or delay the turn" spirit. `_INLINE_
    READ_SIZE_CAP_BYTES` (below) is a WHOLE-FILE size check before
    `read_prose_documents` is ever called, for EITHER prose shape --
    not a partial/truncated read (a PDF's page objects are not laid out
    so that "the first N raw bytes" reliably contains its own first N
    readable pages; a byte-limited read would silently favour whichever
    file happens to front-load its text), the review's own explicitly-
    sanctioned alternative ("refuse inline extraction above a size
    threshold and fall back to the honest 'still processing' line,
    which already exists"). A file over the cap returns `""` here,
    RENDERED IDENTICALLY to every other "nothing extractable" case
    above -- the caller's own honest fallback line was always the
    answer for this, never a new one.
    """
    try:
        document = Document.objects.get(pk=doc_id)
    except Document.DoesNotExist:
        return ""
    from tools.rag.readers import PROSE_EXTS, read_prose_documents

    source = Path(document.source_path or "")
    if source.suffix.lower() not in PROSE_EXTS or not source.is_file():
        return ""
    try:
        if source.stat().st_size > _INLINE_READ_SIZE_CAP_BYTES:
            logger.info(
                "inline_text_for: Document %s is over the %s-byte inline read cap; "
                "skipping synchronous extraction (still reachable through rag__search once "
                "async ingest finishes)", doc_id, _INLINE_READ_SIZE_CAP_BYTES,
            )
            return ""
    except OSError:
        # A `stat()` race (the file vanished between `is_file()` and
        # here) is the SAME "nothing to extract" answer every other
        # not-there case above already gives -- never a reason to let
        # `read_prose_documents` try anyway.
        return ""
    try:
        docs = read_prose_documents(source)
    except Exception:  # noqa: BLE001 -- an honest "" is this function's own contract
        logger.exception("inline_text_for: could not extract text from Document %s", doc_id)
        return ""
    return "\n\n".join(d.text for d in docs if d.text).strip()


def image_caption_for(document) -> str:
    """What an attached IMAGE is, in one line -- read MODEL-FREE out of
    the extraction sidecar the `rag.ingest` job already finished writing,
    for the attachments provider's own `caption` key.

    THE OWNER ADDENDUM (2026-09-08, binding) IS WHY THIS READS A FILE
    RATHER THAN CALLING ANYTHING: a turn's prompt is built inline, in the
    worker, and must never call a model or wait on ingest. The
    description an image gets here is text some earlier job wrote; if no
    such job has finished, this answers `""` and the caller renders an
    honest "not ready yet" line.

    THE SOURCE IS THE SIDECAR, NOT `Document.extraction`. That JSONField
    is a five-key SNAPSHOT of WHICH model produced the text (`method`/
    `engine`/`model_id`/`connection_name`/`produced_at`,
    `tools.rag.media._stamp_extraction`) -- it has never held the text
    itself. The text lives in `<DOCUMENTS_DIR>/<id>/extract.json`'s
    `segments` (`tools.rag.store.sidecar_path`), read through
    `tools.rag.sidecar.read_sidecar`, which is a TOTAL function over
    whatever bytes happen to be on disk and never raises -- the same
    reader `tools.rag.views.document_transcript` uses, and the reason a
    corrupt or half-written sidecar degrades to `""` here rather than
    failing a turn.

    `""` -- indistinguishable to the caller, deliberately, because all of
    these mean "there is no description to show yet" -- for: a non-image
    document (a scanned PDF gets a page-keyed sidecar too, but that is a
    TRANSCRIPT, `rag__search`'s job, not a one-line bullet); no sidecar;
    an UNFINISHED sidecar (`produced_at` unset -- the same completeness
    gate `tools.rag.ingest._source_documents` applies before indexing
    one); a sidecar whose `segments` is missing, not a list, or holds no
    text.

    ONE LINE, BOUNDED. Every run of whitespace -- newlines included --
    collapses to a single space before the cap is applied, so extracted
    text can never forge a second line inside a prompt; then
    `foundation.format.single_line` applies `_CAPTION_CHAR_CAP` with the
    platform's own trailing ellipsis. `tools/rag` and `agents.runtime`
    may not import one another, so the caller re-applies its own bound
    too -- belt and braces over text no human vetted.

    ONE `stat()` PER IMAGE ROW, AND THE PARSE IS MEMOIZED (review round
    1, finding 8). `attached_documents` calls this once per attachment
    on every conversation render AND on every poller tick -- the chip
    stack is re-rendered on each one -- so an unmemoized read would
    re-open and re-parse the same finished JSON dozens of times for a
    value that cannot change until the file does. The `stat()` itself
    stays (`Document.has_transcript` already pays exactly one per row on
    a paginated page, with the same reasoning) and it is what makes the
    memo correct rather than stale: `_caption_from_sidecar` below is
    keyed on the path AND its `st_mtime_ns`/`st_size`, so a re-ingest
    that rewrites the sidecar produces a different key and the stale
    entry is simply never asked for again.

    WHY NOT READ IT OFF THE ROW INSTEAD. `Document.extraction` is
    written in exactly ONE place (`tools.rag.media._stamp_extraction`,
    media.py:529) and holds a five-key SNAPSHOT of WHICH model produced
    the text -- `{method, engine, model_id, connection_name,
    produced_at}`. The text itself has never been on the row; putting it
    there would be a migration plus a second source of truth for
    something the sidecar already owns, and it would go stale against
    the file on every re-ingest.

    THE TEXT ALONE. `image_caption` below is the same read returning
    `(state, text)`; this stays as the one-value wrapper every existing
    caller and test already uses, because a rename would churn a dozen
    assertions for no behaviour.
    """
    return image_caption(document)[1]


def image_caption(document) -> tuple[str, str]:
    """`(state, text)` for an attached image's description -- `state` one
    of `CAPTION_PENDING`/`CAPTION_READY`/`CAPTION_EMPTY`/
    `CAPTION_UNDESCRIBED` (see those constants for what each means, and
    why four rather than "a string or blank").

    A NON-IMAGE IS `(CAPTION_NOT_APPLICABLE, "")` and reads nothing off
    disk. NOT `pending`: the provider sets `caption_state` on every row,
    and telling a reader a prose attachment's description is "still
    working" -- about a document that will never have one -- is the
    exact shape of lie this state exists to stop telling about images.
    The prompt renders nothing at all for `n/a`.

    Same single `stat()` and the same memo as before (see
    `image_caption_for`'s own docstring for both);
    `_caption_from_sidecar` returns the pair now.
    """
    from tools.rag import store

    if not (document.media_type or "").startswith("image/"):
        return CAPTION_NOT_APPLICABLE, ""
    path = store.sidecar_path(document.pk)
    try:
        stamp = path.stat()
    except OSError:
        return CAPTION_PENDING, ""
    return _caption_from_sidecar(str(path), stamp.st_mtime_ns, stamp.st_size)


@lru_cache(maxsize=256)
def _caption_from_sidecar(path: str, mtime_ns: int, size: int) -> tuple[str, str]:
    """`image_caption_for`'s read half, memoized on the sidecar's own
    identity (see that function's docstring for why it is memoized at
    all).

    `mtime_ns`/`size` are CACHE KEYS, not values this body reads -- they
    are what makes a rewritten sidecar a different entry rather than a
    stale hit. `maxsize=256` bounds the memo at a few hundred short
    strings; a box with more attached images than that evicts the
    least-recently-used, which costs one re-parse and nothing else.

    TOTAL over whatever bytes are on disk, like its own reader:
    `read_sidecar` never raises (`tools.rag.sidecar`'s own contract),
    and every "nothing usable here" shape answers `(CAPTION_PENDING, "")`
    -- not a dict, or `produced_at` unset (an UNFINISHED sidecar: the
    same completeness gate `tools.rag.ingest._source_documents` applies
    before ever indexing one).

    RETURNS `(state, text)` (preview UAT, 2026-09-17). The DESCRIPTION
    segment wins over the transcription when there is one: an image with
    a lot of writing on it would otherwise spend the whole
    600-character cap on its OCR dump and cut the one sentence that says
    what the picture IS. With no description segment it joins every
    segment's text, which is exactly what this function has always done
    -- so an image ingested before descriptions existed keeps the
    caption its OCR produced, and every test written against that
    behaviour still means what it meant.
    """
    from tools.rag.sidecar import read_sidecar

    sidecar = read_sidecar(Path(path))
    if not sidecar or not sidecar.get("produced_at"):
        return CAPTION_PENDING, ""
    described = bool(sidecar.get("described"))
    segments = sidecar.get("segments")
    if not isinstance(segments, list):
        segments = []
    usable = [s for s in segments if isinstance(s, dict)]

    described_texts = [str(s.get("text", "")) for s in usable
                       if s.get("kind") == "description"]
    chosen = (" ".join(described_texts) if described_texts
              else " ".join(str(s.get("text", "")) for s in usable))

    collapsed = " ".join(chosen.split())
    if collapsed:
        return CAPTION_READY, single_line(collapsed, max_len=_CAPTION_CHAR_CAP)
    return (CAPTION_EMPTY if described else CAPTION_UNDESCRIBED), ""


def detach_attachment(principal, *, conversation_id, doc_id):
    """The registered `agents.contracts.attachments.DetachOutcome`
    handler (round 13, requirement E) -- `tools.rag.apps.py::ready()`
    registers this dotted path as the attachment detacher, resolved by
    `agents.attachments.detach_attachment`, the ONE caller
    (`agents.chat.views.turns.attachment_detach`).

    ROW-ADDRESSED, NEVER-500 (the brief's own words): a `doc_id` that
    does not exist, or one that exists but carries no attachment to
    `conversation_id` at all, is the SAME 404 -- this function cannot
    tell "never existed" from "not attached here" apart from the
    caller's own point of view, and should not try to (the identical
    enumeration-signal reasoning `may_administer_document`'s own gates
    already accept). A real attachment this principal did not upload is
    403 (`may_detach_attachment`, above).

    TWO SCOPES, TWO ACTIONS (round 12's own cascade split, `delete_
    attachments`' docstring, applied to a SINGLE row rather than a whole
    conversation's worth): a chat-scoped document's ONE attachment row
    IS the whole of its existence (the write-time invariant `tools.rag.
    services.stage_turn_attachments` enforces), so removing it removes
    the DOCUMENT itself -- `tools.rag.services.delete_document`, the
    SAME round-12 cascade machinery `delete_attachments` already calls
    for the conversation-delete case, reused here rather than
    reimplemented. A universal or contained document's attachment is
    only ever a CLAIM this conversation makes on it -- unlinking removes
    the `DocumentAttachment` row alone; the document, and any OTHER
    conversation's own claim on it, are untouched.
    """
    from agents.contracts.attachments import DetachOutcome

    try:
        document = Document.objects.get(pk=doc_id)
    except Document.DoesNotExist:
        return DetachOutcome(False, 404, "That attachment no longer exists.")
    if not DocumentAttachment.objects.filter(
            document=document, conversation_id=conversation_id).exists():
        return DetachOutcome(False, 404, "That file is not attached to this conversation.")
    if not may_detach_attachment(principal, document):
        return DetachOutcome(False, 403, "Only the person who attached this file may remove it.")

    if document.scope == Document.Scope.CONVERSATION:
        from tools.rag import services
        services.delete_document(document)
    else:
        DocumentAttachment.objects.filter(
            document=document, conversation_id=conversation_id).delete()
    return DetachOutcome(True, 200, "Removed.")
