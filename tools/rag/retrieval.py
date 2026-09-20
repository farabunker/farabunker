"""
Query / retrieval pipeline (ADR 0005, ADR 0009).

Phase 1 answers each question with semantic (vector) search over the
pgvector chunks (tools.rag.index.get_index()); the LLM (via the Inference
Gateway) synthesizes an answer and it is returned with citations extracted
from the source nodes' metadata.

Conversation memory is NOT this module's business. It was, briefly and
write-only, through a `session_id` parameter that wrote ChatSession/
ChatMessage rows nothing ever read; both tables were dropped in P2
(agents plan, spec section 7.6). Multi-turn memory now lives in
`agents.models.Turn`, which records the tool call, its data, its
artifacts, and its delegation depth -- none of which a two-column
message table could hold. The RETRIEVAL SEAM is unchanged and is still
the one ADR 0010:277-280 names: an agent invoking RAG as a tool calls
`answer_question`, it does not grow a parallel one.

Category scoping (ADR 0009, WAVE 3a; case-insensitivity hardening as a
follow-up): `answer_question` takes an optional `category: str | None`. When
given a non-empty string, semantic search is restricted to chunks whose node
metadata `category` key equals that value, lowercased to match the
normalized chunk key ingest writes onto every chunk -- the lowercased
`Category.name`, or the literal "uncategorized" for uncategorized documents
-- via a `MetadataFilter(key="category", value=category.lower())` passed to
`index.as_retriever(filters=...)` (W4 review NIT: this used to also be
passed to `index.as_query_engine(filters=...)`, before that kwarg was
found inert and dropped -- see `answer_question`'s own W4 paragraph).
`None`/empty means "search all categories" (today's behavior, no filter
applied).

Phase 2 (deferred): a hybrid path that also routes numeric/aggregate
questions to text-to-SQL over the tabular DocumentRow/Document tables.
Reliable automatic routing with a small local model is still unsolved (the
LLM selector emits malformed JSON; the embedding selector mis-routes prose
questions), so that scaffolding is not carried in the tree today -- see
ADR 0005 for the target design. Recover the earlier RouterQueryEngine +
NLSQLTableQueryEngine spike from git history when Phase 2 begins.

W4 (ADR 0014 §14): retrieval is no longer a fixed-top_k, no-floor exact
scan. `RagSettings.retrieval_top_k` (operator-tunable, replacing the old
fixed `VECTOR_SIMILARITY_TOP_K` module constant) sets how many chunks are
retrieved; `RagSettings.retrieval_score_floor` (default `0.0`, off) drops
any retrieved chunk scoring below it BEFORE synthesis and citations even
see it -- so a question like "who wrote to President Roosevelt about
uranium in 1939?" that used to hand the LLM two strong scanned-letter
matches ALONGSIDE three unrelated, weakly-scored film-transcript chunks can
now have an operator-set floor exclude the noise outright. When every
retrieved chunk scores below a non-zero floor, `answer_question` returns an
honest no-match answer (`tools.rag.messages.score_floor_message`) with
zero citations WITHOUT ever calling the LLM -- both fetched once per call
via `RagSettings.get_solo()`, the module's existing settings-singleton
pattern (see `tools.rag.services.record_ask`'s own `RagSettings.get_solo()`
call for the same shape). `_vector_citations` also now dedups: a document
cited at the SAME locator (page/timestamp span) more than once -- typically
two adjacent chunks from one span, or a duplicate re-ingest -- collapses to
one citation entry keeping the higher score, while genuinely distinct spans
of the same document (a film cited at 3:08 and again at 7:00) stay separate.
A chunk with no locator at all (the plain-prose case -- most documents in
this store) is NEVER collapsed with another chunk of the same document just
because both lack a locator (W4 review MAJOR 1 -- see `_dedup_citations`'s
own docstring for why `(document_id, "")` was the wrong key for that case).

W5 (ADR 0014 §18): hybrid keyword+vector search, an off-by-default
`RagSettings.hybrid_search` toggle. The store's own `hybrid_search` field
(read off the vector store `answer_question` builds -- `tools.rag.index.
get_vector_store()`'s own shape rule, never `RagSettings` a second time,
since the store always describes the LIVE chunk table, not today's toggle)
decides whether `vector_store_query_mode="hybrid"`/`sparse_top_k=
retrieval_top_k` are threaded onto `index.as_retriever(...)`. HYBRID mode
does NO fusion: the installed store (llama-index-vector-stores-postgres
0.8.1) runs the dense query and a Postgres `tsvector`/`ts_rank` keyword
query independently, concatenates dense + sparse, and dedups by node_id
(dense wins ties) -- up to `retrieval_top_k + sparse_top_k` results, never
weighted by an `alpha` (unsupported by this store; never passed). W4's
score floor is NOT applied while the live store is hybrid (`score_floor >
0.0 and not hybrid`, below) -- a dense cosine similarity (0..1) and a raw
`ts_rank` (~0.01-0.1) are incompatible scales with no normalization
anywhere in the store, so any floor an operator would plausibly set would
silently delete every keyword-only hit, the exact result hybrid exists to
surface. `_dedup_citations` (above) gets MORE load-bearing under hybrid,
never less: the store's own node_id dedup is not a document/locator dedup,
and hybrid returns more chunks, so more same-document-same-span pairs
reach `_vector_citations`.

W6 (ADR 0014 §18): the retrieve-then-filter half of `answer_question`
(embed model, vector store/index, retriever construction -- top_k,
hybrid-if-the-live-store-is-hybrid, category filter -- and the retrieve
call itself) is factored out as `retrieve_nodes`, below, so the new
retrieval-only search page (`tools.rag.views.SearchView`) can run the
EXACT same retrieval `answer_question` does without duplicating any of
it. The score floor is deliberately a SEPARATE function, `apply_score_
floor` -- not folded into `retrieve_nodes` itself -- because
`answer_question` needs to tell "nothing was retrieved at all" (the
pre-existing `NO_DOCUMENTS_MESSAGE` case) apart from "something was
retrieved but every hit scored below the floor" (the `score_floor_
message` case, which skips the LLM call entirely); both cases end in an
empty node list, so the distinction can only be made by looking at the
RAW (pre-floor) list `retrieve_nodes` returns, before `apply_score_floor`
narrows it. The search page has no such distinction to preserve -- it
always calls both functions back to back and shows one honest empty state
either way (see `SearchView`'s own docstring) -- but sharing the same two
functions, rather than the search page re-deriving its own floor logic,
is what keeps the two surfaces from silently disagreeing on what "the
floor" means.
"""
from __future__ import annotations

from typing import Any

from django.urls import NoReverseMatch, reverse
from llama_index.core import QueryBundle
from llama_index.core.vector_stores.types import (
    FilterCondition,
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from foundation.format import format_timecode, single_line
from models.contracts import gateway
from models.contracts.bindings import ResolvedModel
from tools.rag.access import DocumentVisibility
from tools.rag.categories import normalize_category_name
from tools.rag import index as index_module
from tools.rag.index import get_index
from tools.rag.messages import score_floor_message
from tools.rag.models import RagSettings

# LlamaIndex's internal placeholder when the query engine had no context to
# synthesize an answer from (empty/no matching chunks). It leaks to users
# verbatim if not translated -- see the friendly replacement below.
_EMPTY_RESPONSE_PLACEHOLDER = "Empty Response"

# Shown instead of an empty/placeholder answer -- e.g. before any documents
# have been ingested, semantic search has nothing to retrieve and the LLM
# synthesis step is skipped, so `str(response)` comes back blank or as
# LlamaIndex's internal "Empty Response" placeholder.
NO_DOCUMENTS_MESSAGE = "No documents have been ingested yet — add some in the Document library."

# F6 (walk-fix batch): the SAME empty-corpus case, but for a turn INSIDE a
# workstream (`visibility.stream is not None`). "add some in the Document
# library" is a dishonest answer there -- documents may well exist, just
# outside this stream's wall/containment (spec §6.1's corpus formula) --
# and naming what those are would be exactly the leak §6.1's outer
# intersection exists to prevent. This sentence says only that the search
# stayed inside the stream's own walls, never what lies past them.
NO_DOCUMENTS_IN_STREAM_MESSAGE = (
    "Nothing in this workstream's scope matched — material outside its walls is "
    "not searched.")


def locator_for(node_metadata: dict) -> str:
    """The human-facing locator string for one citation (T10): `format_
    timecode(start_seconds)` (e.g. `"12:40"`) for an audio/video chunk,
    `f"p. {page}"` for a page-keyed chunk (a PDF page or a vision-extracted
    scan), `""` for a plain prose chunk with neither key -- the exact three
    keys `tools.rag.media.apply_chunk_metadata_exclusions` already
    excludes from embed/LLM-visible text (T7/T8), read back here for
    DISPLAY only.

    `start_seconds` wins when both are somehow present (never happens by
    construction -- `tools.rag.media.documents_from_extract` writes
    EITHER `{"start_seconds", "end_seconds"}` OR `{"page"}` per chunk,
    never both -- but a defined tie-break is cheaper than an assert a
    citation-rendering path should never need).

    Public name (W6 review NIT): promoted from `_locator_for` so
    `tools.rag.views` (the search page, W6, and `_vector_citations`'s own
    per-node dict builder before it) can import a real public name instead
    of reaching for an underscore-prefixed "private" one across a module
    boundary."""
    start_seconds = node_metadata.get("start_seconds")
    if start_seconds is not None:
        return format_timecode(start_seconds)
    page = node_metadata.get("page")
    if page is not None:
        return f"p. {page}"
    return ""


def locator_text_for(node_metadata: dict, locator: str) -> str:
    """The citation TITLE's connector + `locator` (T10 review MINOR 4):
    `" at 12:40"` for a timestamp locator, `", p. 3"` for a page locator,
    `""` when there's nothing to locate (`locator` itself is `""`) -- the
    ONE place the "at ..." vs ", ..." connector rule now lives, replacing
    the SAME rule that used to be duplicated in `rag/ask.html`'s own JS
    (`renderLocatorSpan`) and `rag/history.html`'s template conditional --
    two copies that had to independently agree on "page locator gets a
    comma, timestamp locator gets ' at '" with no shared source of truth.

    Takes the ALREADY-COMPUTED `locator` (from `locator_for` above)
    rather than re-deriving the timecode/page text itself -- the only new
    decision this function makes is the CONNECTOR, chosen off whether
    `node_metadata["start_seconds"]` is set (T10 re-review MINOR 3:
    mirroring `locator_for`'s own priority -- `start_seconds` checked
    FIRST, `page` second -- rather than checking `page` first as this
    function used to. The two functions's docstrings both claim they
    "can't disagree about which kind of locator this is" on the grounds
    that `start_seconds`/`page` are mutually exclusive by construction,
    but a metadata dict that somehow carried BOTH keys (never written
    that way today, but nothing here enforces it) used to make this
    function pick the PAGE branch while `locator_for` had already picked
    the timestamp text for `locator` itself -- a locator string
    (`"12:40"`) glued to the wrong connector (`", 12:40"` instead of
    `" at 12:40"`). Branching on the same key, in the same priority
    order, as `locator_for` makes that disagreement structurally
    impossible rather than merely usually-true) -- so the timecode-
    formatting logic itself is never duplicated between the two.

    Callers append this straight after a citation's title (`title +
    locator_text`) -- `tools.rag.retrieval._vector_citations` persists
    it onto the live citation dict, `tools.rag.services.
    _citation_snapshot` carries it into `AskRecord.citations`, and both
    `rag/ask.html`'s JS and `rag/history.html`'s template now render
    `title + locator_text` verbatim instead of rebuilding the connector
    themselves.

    Public name (W6 review NIT): promoted from `_locator_text_for`, same
    rationale as `locator_for` above."""
    if not locator:
        return ""
    if node_metadata.get("start_seconds") is not None:
        return f" at {locator}"
    return f", {locator}"


def _score_or_neg_inf(citation: dict) -> float:
    """`citation["score"]`, or `float("-inf")` for a citation with no score
    at all (`None` -- never produced by `_vector_citations` today, every
    vector citation carries a real similarity score, but not something the
    dedup/sort logic below should crash on). A plain `citation.get("score")
    or float("-inf")` would be WRONG here: `0.0` is a real, falsy-but-valid
    score (a citation that scored exactly zero similarity), and `0.0 or x`
    evaluates to `x` -- this checks `is not None` explicitly so a genuine
    `0.0` sorts as `0.0`, not as the lowest-possible sentinel."""
    score = citation.get("score")
    return score if score is not None else float("-inf")


def _dedup_citations(citations: list[dict]) -> list[dict]:
    """Collapse `citations` (one entry per retrieved chunk, as built below)
    so a document is cited at most once per DISTINCT, SHARED locator span
    (W4, ADR 0014 §14; W4 review MAJOR 1): two chunks from the SAME
    document at the SAME non-empty `locator_text` -- typically adjacent
    chunks from one page/timestamp span, or a duplicate re-ingest --
    collapse into ONE citation entry, keeping whichever of the two scored
    higher. Two chunks from the same document at DIFFERENT locators (a
    film cited at 3:08 and again at 7:00) stay separate -- they are
    genuinely distinct spans of the source, not noise.

    A citation with NO locator at all (`locator_text == ""`, the
    plain-prose case -- most documents in this store have no page/timestamp
    metadata) is NEVER collapsed with another chunk of the same document.
    `(document_id, "")` used to be treated as a valid, sharable dedup key --
    which meant EVERY chunk of EVERY prose document collapsed onto its
    first chunk (they all share the same `(document_id, "")` key), silently
    dropping every other grounding snippet from a plain-text answer's
    citation list. The key now requires a real, non-empty `locator_text` to
    collapse at all; a chunk with none of its own falls back to
    `("chunk", chunk_id)` -- `chunk_id` (the source node's own id) is
    unique per chunk by construction, so this key can never accidentally
    collapse two DIFFERENT chunks, only ever match a chunk against itself.
    `document_id is None` (no citation dict should reach here without one,
    but nothing upstream guarantees it) gets the same unlocated-fallback
    treatment, for the same reason -- `(None, locator_text)` would collapse
    every undocumented chunk in the whole result set onto one entry.

    Order: highest score first. `_vector_citations`'s input (the vector
    store's own source nodes) already comes back score-descending, but this
    re-sorts explicitly rather than depending on that upstream ordering
    surviving a first-wins dedup unchanged -- a citation list is a
    relevance ranking read top-to-bottom, so the KEPT (max-score) instance
    of a collapsed pair must land in ITS OWN score-sorted position, not
    the position its first-seen, lower-scoring duplicate happened to
    occupy.
    """
    best: dict[tuple[Any, Any], dict] = {}
    for citation in citations:
        document_id = citation.get("document_id")
        locator_text = citation.get("locator_text")
        if document_id is not None and locator_text:
            key = (document_id, locator_text)
        else:
            key = ("chunk", citation.get("chunk_id"))
        existing = best.get(key)
        if existing is None or _score_or_neg_inf(citation) > _score_or_neg_inf(existing):
            best[key] = citation
    return sorted(best.values(), key=_score_or_neg_inf, reverse=True)


def _vector_citations(response: Any) -> list[dict]:
    """Build citation entries from vector-search source nodes.

    Node metadata is written by ingest (tools/rag/ingest.py::_ingest_prose):
    `file_id` (str(Document.id)), `file_name`, `source_path`, plus (T7/T8,
    media/PDF chunks only) `page`/`start_seconds`/`end_seconds` --
    `tools.rag.media.documents_from_extract`'s own per-chunk metadata,
    carried straight through the splitter (`_ingest_prose` never strips
    these keys, only excludes them from embed/LLM-visible text).

    C-5 (round-3 hardening): `source_path` -- a HOST FILESYSTEM PATH -- is
    read from node metadata above but the CONTRACT NARROWED here: the
    returned citation dict no longer carries it. Every earlier caller of
    `answer_question` (spec section 5's `rag.ask` tool, the Ask page, this
    module's own management command) is on the wrong side of a trust
    boundary to see a server path -- `tools/rag/tools.py::run_ask` used to
    hand the dict back unfiltered with a comment naming exactly this risk
    and declining to act on it. Dropped at THIS seam instead of trusted to
    every renderer: `tools.rag.management.commands.ask` is the one caller
    that legitimately wants it, and it reads `Document.source_path` off
    the row by `document_id` instead (an operator on the box already has
    filesystem access; a renderer serving an unauthenticated or remote
    reader does not). `node_metadata` itself is untouched -- only the
    citation dict built from it narrowed.

    `page`/`start_seconds`/`end_seconds` (T10): the raw locator values,
    `None` for a plain prose chunk that has neither. `locator` (T10): the
    bare DISPLAY string built from them by `locator_for` -- `""` when
    there's nothing to locate within the document. `locator_text` (T10
    review MINOR 4): `locator`'s CONNECTOR-PREFIXED form (`_locator_text_
    for`) -- `" at 12:40"` / `", p. 3"` / `""` -- so a template/JS caller
    can render `title + locator_text` ("file.mp4 at 12:40" / "report.pdf,
    p. 3" / plain "notes.md") from ONE field without re-deriving either
    the AV-vs-page distinction OR the connector rule itself; `locator`
    stays alongside it (unchanged, additive) for any caller that still
    wants the bare value.

    W4 (ADR 0014 §14; W4 review MAJOR 1): the returned list is DEDUPED via
    `_dedup_citations` before it's handed back -- one entry per document per
    DISTINCT, SHARED locator span, the higher-scoring chunk kept when two
    collapse together, score-descending order. This used to emit one
    citation per source NODE (chunk) unconditionally, so an ask against a
    single video could return e.g. five citations all reading the same
    "clip.mp4 at 2:15" -- correct per-chunk, misleading as a citation list
    (five lines with no signal they're one source/span). A chunk with no
    locator at all (a plain-prose document) is never collapsed with another
    chunk of the same document -- see `_dedup_citations`'s own docstring.
    Snapshot shape is unchanged either way (`tools.rag.services.
    _citation_snapshot` reduces whatever this returns the same way it
    always has).
    """
    citations = []
    for node_with_score in getattr(response, "source_nodes", None) or []:
        node = node_with_score.node
        node_metadata = node.metadata or {}
        locator = locator_for(node_metadata)
        citations.append(
            {
                "source": "vector",
                "document_id": node_metadata.get("file_id"),
                "title": node_metadata.get("file_name"),
                "chunk_id": node.node_id,
                "row_index": None,
                "score": node_with_score.score,
                "snippet": node.get_content()[:500],
                "page": node_metadata.get("page"),
                "start_seconds": node_metadata.get("start_seconds"),
                "end_seconds": node_metadata.get("end_seconds"),
                "locator": locator,
                "locator_text": locator_text_for(node_metadata, locator),
            }
        )
    return _dedup_citations(citations)


# W6: the chunk-text snippet shown per result is capped to roughly this
# many characters (plus an ellipsis) -- long enough to show real context,
# short enough that one card can't dominate the page.
_SEARCH_SNIPPET_CAP = 1200


def _truncated_snippet(text: str) -> str:
    """`text`, capped to `_SEARCH_SNIPPET_CAP` characters with a trailing
    ellipsis when it's longer -- the search page's chunk-text preview.
    Plain text in, plain text out: escaping is the template's job
    (Django's default autoescape), never done here, so this stays a pure
    string operation with no HTML-safety opinion of its own."""
    if len(text) <= _SEARCH_SNIPPET_CAP:
        return text
    return text[:_SEARCH_SNIPPET_CAP] + "…"


def _document_url_for(document_id) -> str | None:
    """`reverse("rag-document-file", args=[document_id])`, or `None` when
    `document_id` can't reverse (W6 review MINOR 3): the URL pattern binds
    `<int:doc_id>` (`tools/rag/urls.py`), so a `document_id` that isn't a
    plain non-negative integer string -- a stray non-numeric `file_id` in a
    chunk's stored metadata, however that got there -- raises
    `NoReverseMatch` from a plain `{% url %}` tag, a 500 on an otherwise
    healthy search result. `_search_result_for` calls this instead of
    reversing inline so `search.html` only ever renders a link when one
    reverses cleanly, and falls back to plain unlinked text otherwise --
    the SAME "guard, don't crash the page over a display nicety" posture
    every other never-500 seam in this module takes."""
    if document_id is None:
        return None
    try:
        return reverse("rag-document-file", args=[document_id])
    except (NoReverseMatch, ValueError):
        return None


# S10: a search result's title is the uploaded FILENAME verbatim (via the
# `file_name` chunk metadata), and Django's upload-name sanitizer keeps
# embedded newlines. 120 characters, matching the attachment path's own
# cap -- the same filenames, shown in two places, capped the same way.
_MAX_TITLE_LEN = 120


def _search_result_for(node_with_score, *, hybrid: bool) -> dict:
    """Build one search-result card's data from a retrieved
    `NodeWithScore` (W6) -- declared beside `_vector_citations`, the
    sibling it mirrors, reusing this module's OWN `locator_for`/
    `locator_text_for` helpers rather than a third copy of the
    page/timestamp connector rule (see those functions' own docstrings
    for why a second copy would be the exact anti-pattern they were
    extracted to end).

    `score_label`: "cosine similarity" when the live store is dense-only
    (`hybrid=False`) -- every result in that mode came from the one dense
    query, so that attribution is always true. Under hybrid (W5), the
    installed store concatenates a dense hit list with a keyword
    (`ts_rank`) one with no per-result tag saying which arm a given node
    came from (see this module's own W5 docstring) -- rather than guess,
    every result gets the neutral "relevance score" label in that mode.

    `document_url` (W6 review MINOR 3): the guarded `_document_url_for`
    result -- `None` when `document_id` doesn't reverse cleanly (a
    non-integer `file_id` in stored chunk metadata). `search.html` only
    renders the title as a link when this is truthy, falling back to plain
    text otherwise, so a bad locator degrades the link, never the page.

    `title` (S10): the `file_name` metadata run through
    `foundation.format.single_line` -- the SAME helper the attachment
    block uses, because it is the same uploaded filename and Django's
    sanitizer keeps newlines in it. Sanitised BEFORE the `(untitled)`
    fallback, so a name that is entirely control characters reads
    "(untitled)" rather than as an empty title.
    """
    node = node_with_score.node
    node_metadata = node.metadata or {}
    locator = locator_for(node_metadata)
    score = node_with_score.score
    document_id = node_metadata.get("file_id")
    return {
        "document_id": document_id,
        "document_url": _document_url_for(document_id),
        # SANITISED BEFORE THE FALLBACK, deliberately: a name that is
        # entirely control characters collapses to "" and must read
        # "(untitled)", not render as an empty title.
        "title": single_line(node_metadata.get("file_name") or "", max_len=_MAX_TITLE_LEN) or "(untitled)",
        "locator_text": locator_text_for(node_metadata, locator),
        "score": score,
        "score_display": f"{score:.3f}" if score is not None else "n/a",
        "score_label": "relevance score" if hybrid else "cosine similarity",
        "snippet": _truncated_snippet(node.get_content()),
    }


def _visibility_filters(category, visibility: DocumentVisibility):
    """The `MetadataFilters` for this question -- the category clause and
    the visibility group, AND-combined.

    FACTORED OUT of `retrieve_nodes` so it can be asserted directly
    rather than through a live vector store: this is the filter that
    decides what a person may see, and a filter only testable end to end
    is a filter tested rarely.

    The installed Postgres store recurses through nested
    `MetadataFilters` (`_recursively_apply_filters`), so the nesting is
    supported rather than assumed -- and BOTH halves of a hybrid query
    apply the same filters (each calls `_apply_filters_and_limit`), so a
    hybrid search cannot leak past the visibility clause while the dense
    search obeys it.

    THIS FUNCTION NO LONGER RETURNS `None`. The `workstream` clause
    below is appended in EVERY branch, so `clauses` is never empty and
    every retrieval now carries a filter. Harmless at the store (an AND
    of one group is the group), and it retires the "no category,
    unrestricted principal" fast path deliberately rather than by
    accident. The `if not clauses: return None` line is kept as a
    structural impossibility rather than deleted, so a future branch
    that forgot to append still degrades to "no filter" the way the
    store expects rather than raising -- and
    `test_the_filter_is_never_none_any_more` pins that today's code
    cannot reach it.
    """
    clauses = []
    if category:
        clauses.append(MetadataFilter(
            key="category", value=normalize_category_name(category).lower(),
            operator=FilterOperator.EQ))

    # THE ENTITLEMENT GATE, built but held back rather than appended to
    # `clauses` directly (round 12 review I-3): a first cut appended it
    # here as its own top-level AND item, which meant EVERY leg below --
    # the conversation EQ leg included -- still had to pass it. Composed
    # with the corpus formula into `gated` instead, below, so the
    # conversation leg can be OR'd in OUTSIDE that composition and
    # genuinely exempt from it. `None` when `visibility.unrestricted`
    # (nothing to gate).
    entitlement_gate = None
    if not visibility.unrestricted:
        allowed = []
        if visibility.entitlement_ids:
            # `ANY` renders as
            # `metadata_::jsonb->'entitlements' ?| array['3','7']` -- a
            # JSON *string* array test, which is why the stamped ids are
            # decimal STRINGS (`tools/rag/labels.py`).
            allowed.append(MetadataFilter(
                key="entitlements",
                value=sorted(str(i) for i in visibility.entitlement_ids),
                operator=FilterOperator.ANY))
        if visibility.unlabelled_allowed:
            # `IS_EMPTY` renders as `metadata_->>'entitlements' IS NULL`,
            # true exactly when the key is ABSENT -- which is what an
            # unlabelled chunk looks like, and what every chunk in every
            # existing store already looks like. No back-fill needed.
            allowed.append(MetadataFilter(key="entitlements", value="",
                                          operator=FilterOperator.IS_EMPTY))
        entitlement_gate = MetadataFilters(filters=allowed, condition=FilterCondition.OR)

    # CONTAINMENT AND THE WALL, at the one filter point (spec §6.2).
    #
    # OUTSIDE THE `if not visibility.unrestricted` BRANCH ABOVE:
    # containment is a CORPUS rule, not an entitlement rule, so it
    # applies in the open posture and to an administrator with
    # `admin_sees_content` on, exactly as it applies to everybody else.
    #
    # `workstream` is stamped on a contained document's chunks by the one
    # cache writer (`tools/rag/labels.py`, spec §8.3). ABSENT on every
    # chunk in every existing store, which is what `IS_EMPTY` matches --
    # so no back-fill.
    #
    # THE VALUE IS THE STREAM PK AS A DECIMAL STRING, matching the
    # `entitlements` convention documented above for the same reason:
    # `ANY` and `EQ` render as JSON *string* comparisons over
    # `metadata_`, so an integer stamped into the metadata would never
    # match `str(workstream_id)` here.
    if visibility.stream is None:
        corpus = MetadataFilter(key="workstream", value="",
                                operator=FilterOperator.IS_EMPTY)
    else:
        legs = [
            # contained: this stream's own documents
            MetadataFilter(key="workstream", value=str(visibility.stream.workstream_id),
                           operator=FilterOperator.EQ),
        ]
        if visibility.stream.pinned_file_ids:
            # AND-ed WITH `workstream IS_EMPTY`: a pin names a document,
            # not a document-as-of-when-it-was-pinned, and
            # `set_document_workstream` deletes the `WorkstreamPin` row
            # the day a caller re-homes a pinned document -- but that is
            # a delete racing this read, not a guarantee this leg may
            # lean on by itself. Requiring the UNIVERSAL stamp here is
            # what makes the filter enforce the invariant rather than
            # merely assume it: a document re-homed INTO some other
            # stream is stamped with THAT stream's id, so this leg no
            # longer matches its chunks even if a stale pin row still
            # named it.
            legs.append(MetadataFilters(filters=[
                MetadataFilter(
                    key="file_id",
                    value=sorted(str(i) for i in visibility.stream.pinned_file_ids),
                    operator=FilterOperator.ANY),
                MetadataFilter(key="workstream", value="",
                               operator=FilterOperator.IS_EMPTY),
            ], condition=FilterCondition.AND))
        # ROUND 17 (owner: "a bool in settings to use all rag documents
        # ... default it on"): `include_universal is False` drops the
        # WHOLE universal leg below -- wall-narrowed or not -- from
        # `legs` entirely, the identical "drop the leg, not merely
        # narrow it further" rule `tools.rag.workstreams.stream_
        # documents`'s own docstring states for its document-level
        # mirror of this exact formula. The contained leg above and the
        # pinned leg below are UNCHANGED either way.
        if visibility.stream.include_universal:
            universal = [MetadataFilter(key="workstream", value="",
                                        operator=FilterOperator.IS_EMPTY)]
            if visibility.stream.wall:
                # A NON-EMPTY WALL EXCLUDES UNLABELLED UNIVERSAL DOCUMENTS
                # (author decision 5): a document under no entitlement is
                # under none of the wall's. AND-ed into the universal leg
                # only -- the wall narrows what the library contributes and
                # does not un-admit what somebody deliberately contained or
                # pinned.
                universal.append(MetadataFilter(
                    key="entitlements",
                    value=sorted(str(i) for i in visibility.stream.wall),
                    operator=FilterOperator.ANY))
            legs.append(MetadataFilters(filters=universal, condition=FilterCondition.AND))
        corpus = MetadataFilters(filters=legs, condition=FilterCondition.OR)

    # ROUND 12 AMENDMENT (owner ruling, verbatim: "if I submit a
    # document but have scope for chat, then it should only be used in
    # that chat... if I change the scope to workstream, it should be
    # made available via the rag framework") -- LAYERED ON TOP of the
    # corpus formula above, deliberately, rather than woven into each of
    # its legs: every leg above (`stream is None`'s single filter, and
    # the contained/pinned/universal legs) still means exactly what its
    # own comment says, and this wrapping is the ONE place a reader
    # needs to look to find the conversation-scope rule, rather than a
    # fourth `AND conversation IS_EMPTY` copy-pasted into every leg.
    #
    # `tools.rag.labels.restamp_document_chunks` stamps a `conversation`
    # metadata key (the attaching conversation's UUID, as a decimal--no,
    # a plain string, matching the `workstream`/`entitlements`
    # convention above) onto a `Document.scope == "conversation"`
    # document's own chunks, and REMOVES the key otherwise -- ABSENT on
    # every chunk in every existing store (no back-fill needed, the
    # identical shape `workstream`'s own comment already documents).
    #
    # `IS_EMPTY` first, ALWAYS: a chat-scoped document's chunks would
    # otherwise match the ordinary corpus formula above (its `workstream`
    # is ALSO always empty, since it is never contained -- `document_
    # upload`'s own placement logic never sets both), so EVERY leg above
    # must lose to a chat-scoped chunk unless this turn's own
    # conversation is the one it is scoped to.
    #
    # `visibility.conversation_id` OR-ed in UNCONDITIONALLY, no wall, no
    # entitlement clause: the owner's own ruling is that a chat-scoped
    # document is ALWAYS part of its own conversation's corpus, which is
    # precisely "guaranteed availability" -- the identical phrase round
    # 11 review R-1's own follow-up note named as future scope for
    # WALLED-UNIVERSAL documents, delivered here instead for
    # CONVERSATION-scoped ones specifically, by owner ruling rather than
    # by this round's own initiative.
    corpus = MetadataFilters(filters=[
        corpus,
        MetadataFilter(key="conversation", value="", operator=FilterOperator.IS_EMPTY),
    ], condition=FilterCondition.AND)

    # THE GATED FORMULA: entitlement gate AND corpus, when there is a
    # gate to apply (a member on a labelled library); just the corpus
    # formula alone when `visibility.unrestricted`.
    gated = (
        MetadataFilters(filters=[entitlement_gate, corpus], condition=FilterCondition.AND)
        if entitlement_gate is not None else corpus
    )

    if visibility.conversation_id:
        # ROUND 12 REVIEW I-3 (RULING): the conversation leg is EXEMPT
        # from the entitlement gate, OR'd in at THIS OUTER level rather
        # than nested inside `gated` -- a first cut OR'd it only inside
        # the corpus formula, which still lost to `entitlement_gate`'s
        # own AND on a LOCKED library (`unlabelled_allowed=False`, no
        # entitlement held): a chat-scoped chunk is NEVER labelled at
        # all, so it carries no `entitlements` key for `ANY` to match,
        # and `IS_EMPTY` was never offered either -- the document was
        # unretrievable in its OWN conversation on exactly the posture
        # the owner's ruling most needs to survive. A chat-scoped
        # document is governed by OWNERSHIP (`tools.rag.access.
        # readable_documents`' own chat-scope clause), never by labels
        # -- this is that same model, delivered at the chunk-filter
        # level: "guaranteed availability" (round 11 review R-1's own
        # phrase) means what it says, on every posture, for a principal
        # acting in that conversation. THE CITATION LINK STILL 404s FOR
        # A NON-UPLOADER SHARE RECIPIENT (`rag-document-file`, unaffected
        # by this leg) -- retrieved CONTENT and file ACCESS are
        # deliberately different questions (round 12 review minor 4);
        # this leg only ever answers the first.
        gated = MetadataFilters(filters=[
            gated,
            MetadataFilter(key="conversation", value=str(visibility.conversation_id),
                           operator=FilterOperator.EQ),
        ], condition=FilterCondition.OR)
    clauses.append(gated)

    if not clauses:
        return None
    return MetadataFilters(filters=clauses, condition=FilterCondition.AND)


def retrieve_nodes(
    question: str,
    category: str | None,
    settings_row: RagSettings,
    *,
    embed_resolved: ResolvedModel,
    visibility: DocumentVisibility,
    request_timeout: float | None = None,
):
    """Run the shared retrieve step (W6, ADR 0014 §18): resolve the
    category filter, build the embed model + vector store + index, then
    run `index.as_retriever(...).retrieve(...)` with `settings_row.
    retrieval_top_k` (+ the hybrid kwargs, W5, when the LIVE store is
    hybrid). Used by BOTH `answer_question` (below) and `tools.rag.
    views.SearchView` (W6's retrieval-only search page) -- the exact same
    retriever construction, never a second copy that could silently drift
    from Ask's (top_k, category filter, and hybrid-if-live are all
    identical between the two surfaces by construction, not by
    convention).

    Returns `(nodes, hybrid, index)`:

    - `nodes` is the RAW list `retriever.retrieve(...)` came back with,
      BEFORE the score floor is applied (see `apply_score_floor`, its
      deliberately-separate sibling, and this module's own W6 docstring
      paragraph for why the floor isn't folded in here).
    - `hybrid` is the live store's own `hybrid_search` field (`modules.
      rag.index.get_vector_store()`'s shape rule), read here once so
      neither caller has to construct a second `PGVectorStore` just to
      learn it -- `apply_score_floor` needs it (the floor is suspended
      while the live store is hybrid, W5), and the search page needs it
      too (to attribute a result's score honestly -- "cosine similarity"
      only when every result came from a dense-only query).
    - `index` is the SAME `VectorStoreIndex` this function just built --
      returned (rather than rebuilt a second time) so `answer_question`
      can synthesize from it (`index.as_query_engine(llm=...).
      synthesize(...)`) without a second `get_index` call. The search page
      (which never synthesizes) simply discards it.

    `settings_row`: the caller's own `RagSettings.get_solo()` row (never
    re-fetched here) -- `answer_question` already reads it once for BOTH
    `retrieval_top_k`/`retrieval_score_floor`, and the search page fetches
    its own single row the same way, matching this module's existing "one
    settings read per call" convention (see `answer_question`'s own W4
    paragraph).

    `embed_resolved`: an already-resolved `ResolvedModel` for `rag.embed`
    -- built into an embed model via `models.contracts.gateway.
    get_embed_model_for`, never the global LlamaIndex `Settings` (see
    `answer_question`'s own docstring for why callers always resolve
    their own models rather than trusting a role-path default).

    `visibility` (IA-2 T11): a `tools.rag.access.DocumentVisibility` --
    REQUIRED and keyword-only, never defaulted. This is THE ONE FILTER
    POINT for what a principal may retrieve: every caller builds its own
    `DocumentVisibility` (via `tools.rag.access.document_visibility`) from
    whatever subject it has -- the acting principal on a request, a job
    payload's actor, a tool call's `ctx.principal` -- and hands it here,
    so no runner grows its own second copy of the visibility rule. See
    `_visibility_filters`, below, for the filter this builds, and the
    early-return branch just inside this function for the one case that
    filter can never express (a principal who may see nothing at all).

    `request_timeout` (final review I-3, fix round 3), optional and
    keyword-only: threaded straight into BOTH `get_embed_model_for` calls
    below, unchanged (`None`) for every caller that does not pass one --
    the page-submitted search page (`tools.rag.views.SearchView`) and the
    standalone `rag.ask` job path (`tools.rag.jobs.run_ask`) never do.
    `tools.rag.tools.run_search`/`run_ask` -- the IN-TURN tool runners,
    called synchronously inside an agent turn from `agents.runtime.
    loop.run_loop` -- pass the turn's own remaining budget
    (`ctx.budget.deadline_monotonic - time.monotonic()`, the same
    "derive from what's left, never a fixed ceiling" pattern `tools.
    vision.tools`'s own generate runner already uses), so the ollama
    embedder's own hidden 300s default (`models.contracts.engines.
    ollama.build_embedder`) cannot compete with a turn's own,
    operator-editable response timeout the same way the chat LLM's
    already cannot (B2/I-3, one-timeout task).
    """
    if visibility.sees_nothing:
        # A signed-in principal with no entitlements on a LOCKED library.
        # The honest answer is nothing, and it must be an EARLY RETURN
        # rather than an empty filter list -- `MetadataFilters(filters=[])`
        # means "no filter", which means EVERYTHING. This is the single
        # most dangerous line in the phase and it has its own test.
        #
        # The index is still built and returned: `answer_question`
        # synthesizes from it even with zero nodes. The query EMBEDDING is
        # skipped, because it happens inside `retriever.retrieve`, which
        # is what we skip.
        embed_model = gateway.get_embed_model_for(
            embed_resolved, request_timeout=request_timeout)
        with index_module.disposing_vector_store() as vector_store:
            index = get_index(vector_store, embed_model=embed_model)
            return [], vector_store.hybrid_search, index

    filters = _visibility_filters(category, visibility)

    embed_model = gateway.get_embed_model_for(embed_resolved, request_timeout=request_timeout)
    # W5 (ADR 0014 §18): the store is built explicitly, once, so its own
    # `hybrid_search` field (the LIVE table's shape, not `RagSettings.
    # hybrid_search` read a second time) can be read below -- `get_index`
    # already accepts a pre-built store positionally.
    # C-06: `disposing_vector_store` disposes the store's SQLAlchemy engine
    # when this block exits, including on an exception from the retrieve
    # call below -- both branches return from inside the `with` so the
    # pool is disposed after the query, not before it.
    with index_module.disposing_vector_store() as vector_store:
        index = get_index(vector_store, embed_model=embed_model)
        hybrid = vector_store.hybrid_search

        query_bundle = QueryBundle(question)
        retriever_kwargs = {"similarity_top_k": settings_row.retrieval_top_k, "filters": filters}
        if hybrid:
            # W5 composition rule 3: `_hybrid_query` (llama-index-vector-
            # stores-postgres 0.8.1) runs the dense and sparse (Postgres
            # `tsvector`/`ts_rank`, NOT BM25 or a sparse embedding model)
            # queries independently and concatenates them, so `sparse_top_k`
            # is its own budget, not a slice of `similarity_top_k` -- set equal
            # to it (`sparse_top_k = retrieval_top_k`) rather than splitting a
            # fixed total, which would halve dense recall for every question to
            # preserve a number's old meaning instead of stating its new one.
            # `alpha` is NEVER passed -- the installed store logs a warning and
            # ignores it; there is no fusion here to weight.
            retriever_kwargs["vector_store_query_mode"] = "hybrid"
            retriever_kwargs["sparse_top_k"] = settings_row.retrieval_top_k
        retriever = index.as_retriever(**retriever_kwargs)
        nodes = retriever.retrieve(query_bundle)
        return nodes, hybrid, index


def apply_score_floor(nodes: list, score_floor: float, *, hybrid: bool) -> list:
    """Drop any node in `nodes` scoring below `score_floor` -- the W4 (ADR
    0014 §14) score-floor filter, factored out of `answer_question` (W6)
    so `retrieve_nodes`'s raw result can be filtered identically by both
    `answer_question` and `tools.rag.views.SearchView`.

    `score_floor <= 0.0` (the default, "off") returns `nodes` unchanged.
    `hybrid=True` ALSO returns `nodes` unchanged regardless of
    `score_floor` (W5): a hybrid result list interleaves dense cosine
    similarity (0..1) with the sparse arm's raw `ts_rank` (typically
    ~0.01-0.1) -- two incompatible, unnormalized scales -- so applying a
    floor to that list would silently delete every keyword-only hit, the
    exact result hybrid exists to surface.

    A node with no score at all (never produced by a real vector search
    today, same caveat `_score_or_neg_inf` documents for a citation dict's
    "score") is treated as unable to clear ANY positive floor --
    `float("-inf")`, the same "missing score sorts/filters as the worst
    possible score" convention `_score_or_neg_inf` uses, rather than a
    competing "treat missing as 0.0" one.
    """
    if score_floor > 0.0 and not hybrid:
        return [node for node in nodes if (node.score if node.score is not None else float("-inf")) >= score_floor]
    return nodes


def answer_question(
    question: str,
    *,
    category: str | None = None,
    answer_resolved: ResolvedModel,
    embed_resolved: ResolvedModel,
    visibility: DocumentVisibility,
    request_timeout: float | None = None,
) -> dict:
    """Answer `question`.

    If `category` is a non-empty string, semantic search is scoped to chunks
    whose node metadata `category` equals it (lowercased to match the normalized
    chunk key); `None`/empty searches all categories (ADR 0009).

    `visibility` (IA-2 T11): a `tools.rag.access.DocumentVisibility` --
    REQUIRED and keyword-only, NEVER defaulted (a default of "see
    everything" would be a fail-open default). Passed straight through to
    `retrieve_nodes`, below -- this function never builds a filter of its
    own; `retrieve_nodes` is the one filter point.

    `answer_resolved`/`embed_resolved`: already-resolved `ResolvedModel`s the
    caller supplies for THIS call -- there is no implicit role-path default
    anymore (T5, the execution-queue refactor): every caller (AskView, the
    `ask` management command, a queued `rag.ask` job) resolves both roles
    itself first (its own pre-check, or the CLI's own `resolve()` calls) and
    passes the result straight through, so there is exactly one place per
    call that decides which models answer -- never a global mutated
    mid-flight by a concurrent request/job. Built into an LLM/embed model via
    `models.contracts.gateway.get_llm_for`/`get_embed_model_for`; the LLM goes
    straight to `index.as_query_engine(llm=...)`, the embed model to
    `tools.rag.index.get_index(embed_model=...)` -- neither ever touches
    the global LlamaIndex `Settings`.

    W4 (ADR 0014 §14): `RagSettings.retrieval_top_k`/`retrieval_score_floor`
    are fetched ONCE per call, via `RagSettings.get_solo()` (the module's
    existing settings-singleton pattern), rather than re-read mid-flight --
    an ask must see one consistent pair of knobs even if an operator saves a
    change to either while this call is in progress. Retrieval and synthesis
    are DELIBERATELY split into two steps (`index.as_retriever(...).retrieve()`
    then, only when synthesis is actually needed, `index.as_query_engine
    (llm=...).synthesize()`) rather than the single `query_engine.query()`
    call this used to be -- the split is what lets the floor be applied to
    the retrieved nodes BEFORE synthesis/citations ever see them, and lets
    the all-below-floor case return without EVER calling
    `gateway.get_llm_for` (no LLM is built, let alone called, for a question
    that already, provably, has nothing worth asking it).

    Returns a dict shaped like:
        {
            "answer": str,
            "citations": [
                {"document_id": ..., "title": str, "chunk_id": str | None,
                 "row_index": int | None, "snippet": str | None, ...},
                ...
            ],
        }
    Each citation references a prose chunk (`chunk_id` set) from the vector
    search (ADR 0005).

    `answer` is never blank or LlamaIndex's internal "Empty Response"
    placeholder (produced when there's no context to synthesize from, e.g.
    no documents ingested yet) -- both are translated to `NO_DOCUMENTS_MESSAGE`
    so the API and the Ask page always agree on a plain, actionable sentence.
    W4 adds a second, distinct honest-empty case: every node RETRIEVED but
    ALL of them scoring below a non-zero `retrieval_score_floor` -- that one
    gets `tools.rag.messages.score_floor_message`'s own copy instead
    (documents exist and were found; none was a good enough match), never
    conflated with "no documents ingested yet".

    W6 (ADR 0014 §18): the retrieve step itself is `retrieve_nodes`
    (above) -- the SAME function `tools.rag.views.SearchView` calls for
    the retrieval-only search page, so the two surfaces can never silently
    diverge on top_k/hybrid/category-filter behavior. The score floor is
    `apply_score_floor` (also above), called separately from `retrieve_
    nodes` for one reason specific to THIS function: telling "nothing was
    retrieved at all" apart from "something was retrieved but every hit
    scored below the floor" needs the RAW (pre-floor) `nodes` list, which
    `retrieve_nodes` still returns.

    `request_timeout` (final review I-3, fix round 3), optional and
    keyword-only: passed straight through to `retrieve_nodes` below (the
    embedder side) and to `gateway.get_llm_for(answer_resolved, ...)`
    (the answer LLM side) -- see `retrieve_nodes`'s own docstring for the
    full "in-turn only" reasoning. `None` (every caller except `tools.
    rag.tools.run_ask`, the in-turn `rag.ask` tool runner) changes
    nothing.
    """
    settings_row = RagSettings.get_solo()
    score_floor = settings_row.retrieval_score_floor

    nodes, hybrid, index = retrieve_nodes(
        question, category, settings_row, embed_resolved=embed_resolved, visibility=visibility,
        request_timeout=request_timeout,
    )
    surviving_nodes = apply_score_floor(nodes, score_floor, hybrid=hybrid)

    if nodes and score_floor > 0.0 and not surviving_nodes:
        # W4: every retrieved node scored below the floor. An honest
        # no-match answer -- NOT an error, NOT "no documents ingested" (both
        # are false: documents exist and were found; none matched well
        # enough) -- with zero citations, and no LLM ever built or called.
        answer = score_floor_message(score_floor)
        citations: list[dict] = []
    else:
        query_bundle = QueryBundle(question)
        llm = gateway.get_llm_for(answer_resolved, request_timeout=request_timeout)
        # `similarity_top_k`/`filters` are deliberately NOT passed here (W4
        # review NIT): `.synthesize(query_bundle, surviving_nodes)` below
        # synthesizes from the EXPLICIT node list already retrieved above --
        # it never calls back into this query engine's own internal
        # retriever, so kwargs that only configure THAT retriever are inert
        # dead weight on this call, not a second, redundant enforcement of
        # the same top_k/filter.
        query_engine = index.as_query_engine(llm=llm)
        response = query_engine.synthesize(query_bundle, surviving_nodes)
        answer = str(response).strip()
        if not answer or answer == _EMPTY_RESPONSE_PLACEHOLDER:
            # F6: stream-scoped copy when this turn carries a stream
            # (`visibility.stream is not None`), the loose copy otherwise
            # -- unchanged for every existing caller, none of which pass a
            # stream today outside a workstream turn.
            answer = (NO_DOCUMENTS_IN_STREAM_MESSAGE if visibility.stream is not None
                      else NO_DOCUMENTS_MESSAGE)
        citations = _vector_citations(response)

    return {"answer": answer, "citations": citations}
