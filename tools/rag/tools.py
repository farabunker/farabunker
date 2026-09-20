"""The RAG tools an agent may call (spec section 5).

Each runner calls the SAME service function the page calls. Not one grows
a parallel seam:

- `rag.search` -> `retrieval.retrieve_nodes` + `retrieval.apply_score_floor`
  -- exactly what `views.SearchView` does (views.py:1506,1531).
- `rag.ask`    -> `retrieval.answer_question` -- exactly what
  `jobs.run_ask` does (jobs.py:244), and the seam ADR 0010:277-280 names.
- `rag.ingest` -> `ingest.enqueue_reingest` -- the retry button's own
  entry point (ingest.py:1229).

MODULE-SCOPE IMPORTS STAY PURE, AND THAT IS LOAD BEARING. `RagConfig.
ready()` imports this module to register the specs below, and `ready()`
must not pull in the service layer (no DB, no heavy imports at startup --
see that method's own docstring). So every runner does its imports
LAZILY, inside the function body, the same way `tools/rag/categories.py:40`
already does. `agents/contracts/tests/test_purity.py`'s sibling guard
pins it.
"""
from __future__ import annotations

import time

from agents.contracts.artifacts import mint_artifact
from agents.contracts.tools import ToolContext, ToolRefused, ToolResult, ToolSpec, validate_tool_args
from models.contracts.operations import Param, ParamError
from models.contracts.roles import RAG_ANSWER_ROLE, RAG_EMBED_ROLE

# `category` is a "text" param, not a "choice", deliberately:
# `validate_params` requires a non-blank value for ANY choice param,
# required or not (operations.py:317-319), and "search every category" is
# the normal case (ADR 0009: None/empty searches all categories). The
# LLM-facing schema is IDENTICAL either way -- `openai_tool_dict` maps
# both kinds to "string" and emits no `enum` for an engine-free choice --
# so nothing is lost, and the runner validates the name against the DB.
_CATEGORY = Param(
    "category", "text", "Category",
    description=(
        "Restrict to one library category by name. Leave it out to search "
        "every category."
    ),
)


RAG_SEARCH = ToolSpec(
    key="rag.search",
    label="Search the library",
    description=(
        "Search the operator's own ingested documents and return the "
        "best-matching passages with their source titles and locators. Use "
        "this when you need evidence from the library but do not need it "
        "summarised into an answer. It calls no language model."
    ),
    params=(
        Param("query", "text", "Query", required=True,
              description="What to look for in the library."),
        _CATEGORY,
        Param("top_k", "int", "Results", default=None, min=1, max=50,
              description=(
                  "How many passages to return. Leave it out to use the "
                  "operator's configured retrieval depth."
              )),
    ),
    roles=(RAG_EMBED_ROLE,),
    runner="tools.rag.tools.run_search",
)

RAG_ASK = ToolSpec(
    key="rag.ask",
    label="Ask the library",
    description=(
        "Ask a question of the operator's own ingested documents and get a "
        "synthesised answer with citations. Use this when a written answer "
        "is wanted rather than raw passages."
    ),
    params=(
        Param("question", "text", "Question", required=True,
              description="The question to answer from the library."),
        _CATEGORY,
    ),
    roles=(RAG_ANSWER_ROLE, RAG_EMBED_ROLE),
    runner="tools.rag.tools.run_ask",
)

# THE KEY, SPELLED ONCE. `tools/rag/views.py` gates the library's own
# upload door on this tool's entitlement (spec section 9.3), and a view
# that retyped the literal would be a second spelling of a key the
# registry matches exactly.
RAG_INGEST_TOOL_KEY = "rag.ingest"

RAG_INGEST = ToolSpec(
    key=RAG_INGEST_TOOL_KEY,
    label="Re-ingest a document",
    description=(
        "Queue an already-stored document to be read and indexed again. "
        "Takes the document's id and nothing else -- it accepts no file "
        "path, so it can only ever act on what the operator already put in "
        "the library."
    ),
    params=(
        Param("document_id", "int", "Document", required=True, min=1,
              description="The id of a document already in the library."),
    ),
    roles=(RAG_EMBED_ROLE,),
    runner="tools.rag.tools.run_ingest",
    # A re-ingest REPLACES existing chunks -- a change to state that
    # already exists, which is exactly what `mutates` means. Registered,
    # documented, and tested, but never grantable until Identity & Auth
    # (ADR 0010:266-276).
    mutates=True,
)


def _resolved_category(raw: str | None):
    """The `Category` name `raw` denotes, or `None` for blank.

    Refuses an unknown name with a `ParamError` naming what exists, so a
    model that guessed a shelf gets the repairable failure section 10.2
    grants one retry for -- never a silent all-categories search that
    answers a question nobody asked.
    """
    from tools.rag.models import Category

    name = (raw or "").strip()
    if not name:
        return None
    match = Category.objects.filter(name__iexact=name).first()
    if match is None:
        existing = sorted(Category.objects.values_list("name", flat=True))
        raise ParamError({
            "category": (
                f"No category named {name!r}. "
                + (f"The library has: {', '.join(existing)}." if existing
                   else "The library has no categories yet.")
            )
        })
    return match.name


def _document_artifacts(dicts) -> tuple[str, ...]:
    """`("document:<id>[:<title>]", ...)` for every entry carrying a
    document id, deduped, first-seen order preserved.

    Guards `doc_id` with `str(doc_id).isdecimal()` before minting the
    reference, mirroring `retrieval._document_url_for`'s own guard
    (retrieval.py:362-379): `agents/contracts/artifacts.py`'s
    `"document:<id>"` vocabulary is only ever resolved back through
    `reverse("rag-document-file", args=[id])`'s `<int:doc_id>` path
    converter, so a stray non-numeric `file_id` in a chunk's stored
    metadata must not mint an artifact reference nothing can resolve.

    R5 (chat-polish P3.1): `mint_artifact` embeds each entry's own
    `title` (`retrieval._search_result_for`/`_vector_citations`'s
    `node_metadata.get("file_name") or "(untitled)"`, always present on
    these dicts) alongside the id, at MINT TIME -- the one moment this
    module can reach the title without `agents/chat/rendering.py`
    (which renders the eventual file link) ever importing anything from
    `tools.*`, a crossing `agents/chat/rendering.py`'s own module
    docstring forbids outright. `agents.contracts.artifacts` is the
    shared, Django-free vocabulary module both this tool column and the
    chat app already depend on (see `agents/chat/rendering.py`'s own
    import of it), so reaching it here crosses no boundary the import
    law polices -- only `agents/*` importing `tools/*` is forbidden, not
    the reverse (`tools/rag/tools.py` and `tools/vision/tools.py`
    already import from `agents.contracts.tools` for their own specs).

    Deduped by the BARE `document:<id>` key, never the titled string --
    two citations for the same document must mint exactly ONE artifact,
    keeping whichever title arrived first, not one copy per distinct
    title spelling.
    """
    seen: dict[str, str] = {}
    for entry in dicts:
        doc_id = entry.get("document_id")
        if not (doc_id and str(doc_id).isdecimal()):
            continue
        key = f"document:{doc_id}"
        if key not in seen:
            seen[key] = mint_artifact("document", int(doc_id), entry.get("title") or "")
    return tuple(seen.values())


def run_search(args: dict, ctx: ToolContext) -> ToolResult:
    """Retrieval only -- the no-LLM path `views.SearchView` uses.

    Follows that view exactly: take `RagSettings.get_solo()`, call
    `retrieve_nodes` (which returns the TRIPLE `(nodes, hybrid, index)`,
    retrieval.py:344-350), then `apply_score_floor`.

    The score floor and the hybrid flag are read from `RagSettings`,
    NEVER from tool arguments: they are operator policy (ADR 0014 §14),
    and a tool param that let a model lower the floor would be a settings
    mutation wearing a search tool's clothes.
    """
    from models.contracts.bindings import resolve
    from tools.rag import retrieval
    from tools.rag.access import document_visibility
    from tools.rag.messages import UNBOUND, model_unavailable_message
    from tools.rag.models import RagSettings
    from tools.rag.workstreams import scope_with_pins

    clean = validate_tool_args(RAG_SEARCH, args)
    category = _resolved_category(clean.get("category"))

    settings_row = RagSettings.get_solo()
    if clean.get("top_k"):
        # In memory only -- never saved. The operator's configured depth
        # is policy; a per-call override is not a settings change.
        settings_row.retrieval_top_k = clean["top_k"]

    try:
        embed_resolved = resolve(RAG_EMBED_ROLE)
    except ValueError:
        # `resolve()`'s bare ValueError means the role is simply unbound
        # (models/contracts/bindings.py:148) -- the same UNBOUND cause and
        # the same shared copy `tools.rag.views.SearchView`'s own
        # `_precheck_embed_role` renders for this exact role, checked
        # alone. Nothing the model can say fixes an unbound role, so this
        # is a ToolRefused (no retry, section 10.1), mirroring `tools/
        # vision/tools.py::run_generate`'s `VisionUnavailable` ->
        # `ToolRefused` translation for the same reason.
        raise ToolRefused(model_unavailable_message({RAG_EMBED_ROLE: UNBOUND}, {})) from None
    # Final review I-3 (fix round 3): the embedder `retrieve_nodes` builds
    # is one of the clients this turn's own budget must bound, exactly
    # like `tools.vision.tools`'s own generate runner already bounds its
    # wait -- derived from what's LEFT of the turn, never a fixed
    # ceiling, so the ollama embedder's own hidden 300s default cannot
    # compete with a longer operator-set response timeout.
    request_timeout = max(1.0, ctx.budget.deadline_monotonic - time.monotonic())
    nodes, hybrid, _index = retrieval.retrieve_nodes(
        clean["query"], category, settings_row, embed_resolved=embed_resolved,
        request_timeout=request_timeout,
        visibility=document_visibility(
            ctx.principal,
            # THE PINS ARE FILLED HERE, on this side of the seam, because
            # the pin table is a `tools/rag` table and
            # `agents/workstreams.py` hands the stream half over with
            # `pinned_file_ids=frozenset()` (author decision 6).
            # `scope_with_pins` passes `None` through unchanged, so this
            # needs no `ctx.stream is not None` guard of its own.
            stream=scope_with_pins(ctx.stream),
            # ROUND 12: `ctx.conversation_id` IS this turn's own
            # conversation -- threaded straight through so a
            # conversation-scoped attachment is guaranteed part of the
            # corpus for THIS turn's own retrieval call, and excluded
            # from every other one (`tools.rag.retrieval.
            # _visibility_filters`'s own "round 12 amendment"). `""` (a
            # tool called with no conversation at all -- the external
            # MCP edge, `ToolContext.conversation_id`'s own docstring)
            # is falsy, so `visibility.conversation_id` stays effectively
            # unset for that caller, exactly as `None` would.
            conversation_id=ctx.conversation_id),
    )
    surviving = retrieval.apply_score_floor(
        nodes, settings_row.retrieval_score_floor, hybrid=hybrid
    )

    # `retrieval._search_result_for` -- the SAME function
    # `views.SearchView` calls, moved beside `_vector_citations` in the
    # commit above so both callers are peers rather than one reaching
    # into the other's view module. Not a fourth copy of the
    # page/timestamp connector rule: a tool that drifted from the search
    # page would be two different answers to one question.
    results = [retrieval._search_result_for(node, hybrid=hybrid) for node in surviving]

    lines = [
        f"{i}. {r['title']}{r['locator_text']} — {r['snippet']}"
        for i, r in enumerate(results, start=1)
    ]
    text = "\n".join(lines) if lines else "Nothing in the library matched that."
    return ToolResult(
        text=text,
        data={"results": results, "hybrid": hybrid},
        artifacts=_document_artifacts(results),
    )


def run_ask(args: dict, ctx: ToolContext) -> ToolResult:
    """The synthesised-answer path -- the exact seam ADR 0010:277-280
    names.

    Both roles are re-resolved fresh at call time, for the reason
    `jobs.run_ask` already documents: a queued job can sit for a while,
    and the binding may have changed under it (ADR 0013:205-213).

    `session_id` is gone. It was retired in P2 along with ChatSession/
    ChatMessage (spec section 7.6); this runner never passed one, which
    is why nothing here changed when it went.
    """
    from models.contracts.bindings import resolve
    from tools.rag import retrieval
    from tools.rag.access import document_visibility
    from tools.rag.messages import UNBOUND, model_unavailable_message
    from tools.rag.workstreams import scope_with_pins

    clean = validate_tool_args(RAG_ASK, args)
    category = _resolved_category(clean.get("category"))

    # Both roles attempted, in this order, before either failure is
    # raised -- the same two-role shape `tools.rag.jobs.run_ask`'s own
    # `_precheck` uses, minus its health check (reachability is a live
    # network fact this tool does not add a check for; an unreachable
    # engine still surfaces honestly, just as a runtime failure from
    # `answer_question` itself rather than a precheck refusal) -- so "both
    # unbound" gets the single generic sentence rather than two separate
    # ones. `resolve()`'s bare ValueError means UNBOUND
    # (models/contracts/bindings.py:148) -- nothing the model can say
    # fixes it, so a ToolRefused (no retry, section 10.1), mirroring
    # `tools/vision/tools.py::run_generate`'s `VisionUnavailable` ->
    # `ToolRefused` translation.
    resolved: dict = {}
    causes: dict[str, str] = {}
    for role in (RAG_ANSWER_ROLE, RAG_EMBED_ROLE):
        try:
            resolved[role] = resolve(role)
        except ValueError:
            causes[role] = UNBOUND
    if causes:
        raise ToolRefused(model_unavailable_message(causes, resolved))

    # Final review I-3 (fix round 3): both clients `answer_question`
    # builds (the retrieval embedder AND the answer LLM) are in-turn
    # clients this turn's own budget must bound -- see `run_search`'s
    # own comment, above, for the identical reasoning.
    request_timeout = max(1.0, ctx.budget.deadline_monotonic - time.monotonic())
    result = retrieval.answer_question(
        clean["question"],
        category=category,
        request_timeout=request_timeout,
        answer_resolved=resolved[RAG_ANSWER_ROLE],
        embed_resolved=resolved[RAG_EMBED_ROLE],
        visibility=document_visibility(
            ctx.principal,
            # THE PINS ARE FILLED HERE, on this side of the seam, because
            # the pin table is a `tools/rag` table and
            # `agents/workstreams.py` hands the stream half over with
            # `pinned_file_ids=frozenset()` (author decision 6).
            # `scope_with_pins` passes `None` through unchanged, so this
            # needs no `ctx.stream is not None` guard of its own.
            stream=scope_with_pins(ctx.stream),
            # ROUND 12: `ctx.conversation_id` IS this turn's own
            # conversation -- threaded straight through so a
            # conversation-scoped attachment is guaranteed part of the
            # corpus for THIS turn's own retrieval call, and excluded
            # from every other one (`tools.rag.retrieval.
            # _visibility_filters`'s own "round 12 amendment"). `""` (a
            # tool called with no conversation at all -- the external
            # MCP edge, `ToolContext.conversation_id`'s own docstring)
            # is falsy, so `visibility.conversation_id` stays effectively
            # unset for that caller, exactly as `None` would.
            conversation_id=ctx.conversation_id),
    )
    citations = result["citations"]
    # C-5 (round-3 hardening): `source_path` -- a host filesystem path --
    # no longer leaves `_vector_citations` (retrieval.py:292-353) at all.
    # This tool still hands the dict back unfiltered, but there is nothing
    # left in it for a renderer to leak: the field was stripped at the ONE
    # seam every citation passes through, rather than trusted to every
    # renderer to remember not to surface it (which is what the comment
    # this replaces used to ask for and none of them actually needed).
    return ToolResult(
        text=result["answer"],
        data={"citations": citations},
        artifacts=_document_artifacts(citations),
    )


def run_ingest(args: dict, ctx: ToolContext) -> ToolResult:
    """Queue an already-stored document for another ingest pass.

    ENQUEUES AND RETURNS. It never waits on the job it queued. On a
    default install `JobSettings.memory_budget_bytes` is null, which
    `models/queue/scheduler.py:374-380` reads as sequential mode -- at
    most one job on the whole machine -- so a turn that blocked on a job
    it enqueued would deadlock with certainty. In sequential mode the
    child simply sits queued until the turn finishes and then runs.
    """
    from tools.rag import ingest
    from tools.rag.access import may_administer_document
    from tools.rag.models import Document

    clean = validate_tool_args(RAG_INGEST, args)
    doc_id = clean["document_id"]

    # S16: EVERY HTTP path to this same action applies this predicate
    # (`tools.rag.views._administered_document`). This runner applied
    # nothing and loaded by bare pk.
    #
    # ONE MESSAGE FOR BOTH OUTCOMES, and that is the point rather than a
    # convenience: a refusal that reads differently from a not-found is
    # an existence-and-title oracle over the whole library -- and the
    # caller here is a MODEL, which a poisoned document can steer (S2).
    # Walking the id space and reporting every title back into the
    # conversation is a more valuable primitive to an attacker than the
    # unauthorised re-ingest itself.
    #
    # UNREACHABLE TODAY, AND THAT IS NOT A REASON TO SKIP IT: `Agent.
    # _validate_tool_keys` refuses to save an agent naming a
    # `mutates=True` key and `granted_tools` drops every such spec at
    # turn time -- but both fences describe themselves as lasting only
    # "until Identity & Auth lands" (ADR 0010), and it has landed. The
    # day someone lifts them, this runner would have shipped open.
    doc = Document.objects.filter(pk=doc_id).first()
    if doc is None or not may_administer_document(ctx.principal, doc):
        raise ValueError(f"There is no document with id {doc_id} in the library.")

    try:
        # WHO ASKED -- the acting principal, stamped into every job
        # payload (the acting rule). `ctx.principal` is
        # WHO THIS TURN IS BEING RUN AS, which is exactly the actor this
        # queued job should be attributed to.
        queue_job_id = ingest.enqueue_reingest(doc, actor=ctx.principal)
    except FileNotFoundError as exc:
        # A data-integrity problem distinct from "the queue is down": the
        # row exists but has nothing left to ingest FROM. Surfaced with
        # its real cause, never flattened into a generic failure.
        raise ValueError(
            f"{doc.title!r} cannot be re-ingested: its copy in the managed "
            f"store is missing ({exc})."
        ) from exc

    if queue_job_id is None:
        return ToolResult(
            text=f"{doc.title!r} was not queued — the execution queue is unavailable.",
            data={"document_id": doc.pk, "queue_job_id": None},
        )
    return ToolResult(
        text=f"{doc.title!r} is queued for re-ingest as job {queue_job_id}.",
        data={"document_id": doc.pk, "queue_job_id": queue_job_id},
    )
