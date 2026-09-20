"""
The `rag.ask` job kind (ADR 0013, T5) -- the first real, model-consuming
unit of work registered against the execution queue's job-kind registry
(`models.contracts.jobkinds`). Registered in `tools/rag/apps.py::ready()`
alongside the existing `rag.embed` rematerialize callback.

`payload` shape (the same fields `AskView` already reads off its
request body today):
    {"question": str, "category": str | None, "connection": str | None}
`connection` is an optional `ModelConnection` pk (as a string, matching the
Ask form's `<select>` value / `AskView`'s own parsing) that stands in for
`rag.answer`'s durable binding for this one question, exactly like the
Ask-time model picker override does today -- `rag.embed` is never
overridable (embeddings are index-welded, not a per-question choice).

Two resolution passes, deliberately NOT shared code with each other:
- `plan_ask` (called by `models.contracts.queue.enqueue`/
  `models.queue.backend.enqueue` at ENQUEUE time) resolves both roles once
  to build the `ModelRef`s the scheduler admits/claims against.
  `models/queue/scheduler.py`'s provenance contract applies here verbatim:
  this planner does NOT resolve footprints (`ModelRef.footprint_bytes`
  stays `None`) -- claim-time code fills that in later, from a *fresh*
  `footprint_for()` lookup, never from this snapshot.
- `run_ask` (called by `models.queue.worker.Worker._execute` once the job
  is CLAIMED and actually runs) re-resolves both roles fresh and
  health-checks them again, exactly like `tools.rag.views.AskView.
  _precheck_models` does for the synchronous path -- a queued job can sit
  for a while, and the models it declared at enqueue time may no longer be
  what's live (rebound, or newly unreachable) by the time it actually runs.
  A re-check failure raises `RuntimeError` carrying the SAME operator-facing
  copy the web 503 would show (`tools.rag.messages.model_unavailable_message`
  -- see that module's docstring for why the pure formatting lives there,
  not in `views.py`) -- `models.queue.worker.Worker._execute` catches a
  raising handler and stores `str(exc)` verbatim on the job row's `error`
  column (never a traceback), so this is what an operator watching the
  queue sees.

`run_ask`'s re-check RESOLVE loop is intentionally NOT extracted into a
function shared with `tools.rag.views._precheck_models`/`plan_ask`'s own
resolution: that would either make this worker-side module depend on
`views.py` (this module's HTTP-surface sibling -- exactly what
`tools.rag.messages`'s docstring already argues against for the message
text alone) or turn a three-line resolve loop into a seam more complex than
the duplication it would save -- and the web path and the job path
deliberately say different things about a deleted/renamed connection pk
(see `_precheck`'s own docstring), so the two loops could never fully
converge anyway. The pure, deterministic *formatting* of the final message
is shared (`tools.rag.messages.model_unavailable_message`), and so is the
endpoint-dedup + health-check probe that follows the resolve loop
(`tools.rag.messages.unreachable_endpoints`, C-15) -- only the resolve loop
itself, and the message precision it produces, are duplicated on purpose.

Column-privacy (binding on this file): imports `models.registry.bindings`
only -- the one sanctioned seam a `tools/*` app is allowed to reach into
`models/registry/` through (the pk-addressed override lookup + the role's
primary display name), matching `tools/rag/views.py`'s own import surface.
`models.contracts.*` is imported freely, same as everywhere else in this app.

Enqueue-time planner failures (a `ValueError` from `resolve()`/
`resolve_connection_named`) are NOT caught here: `models.queue.backend.
enqueue` calls the planner directly and lets a raise propagate straight out
of `enqueue()` uncaught (confirmed by reading `backend.py`) -- this is
correct for now because every enqueue-time caller (the web layer, today the
only one) already pre-checks both roles itself before ever calling
`enqueue()`, so a planner failure here would only ever fire on a genuine
race (the binding changed between the pre-check and the enqueue call) or a
caller that skipped the pre-check, either of which SHOULD surface loudly
rather than silently queuing a job certain to fail its own re-check.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import Truncator

from foundation.files import create_owner_only_dir, lock_down_file
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import principal_from_payload
from models.registry.bindings import (
    answer_role_primary, model_access_for, resolve_connection_named,
)
from models.contracts.bindings import ResolvedModel, resolve
from models.contracts.gateway import get_llm_for
from models.contracts.jobkinds import JobContext, ModelRef
from models.contracts.roles import (
    CHAT_CONVERSE_ROLE, RAG_ANSWER_ROLE, RAG_EMBED_ROLE, RAG_EXTRACT_ROLE, RAG_TRANSCRIBE_ROLE,
)
from tools.rag import ingest, services
from tools.rag.access import document_visibility
from tools.rag.distil import CONSOLIDATION_MAX_TURNS, distil_conversation
from tools.rag.messages import (
    PRECHECK_ROLES, UNBOUND, UNREACHABLE, model_unavailable_message, unreachable_endpoints,
)
from tools.rag.models import Document
from tools.rag.retrieval import answer_question

logger = logging.getLogger(__name__)


def _ref(role: str, resolved: ResolvedModel, connection_name: str = "") -> ModelRef:
    """Build one `ModelRef` from an already-resolved model -- the
    four-field copy (`engine`/`endpoint`/`model_id` off `resolved`, plus
    `role`) every planner in this module builds at least once, replacing 7
    longhand `ModelRef(role=..., engine=resolved.engine, endpoint=resolved.
    endpoint, model_id=resolved.model_id, ...)` constructions across
    `plan_ask`/`plan_ingest`. `footprint_bytes` is never passed here --
    it stays this dataclass's own `None` default, per every planner's
    shared provenance contract (module docstring: claim-time code fills it
    in fresh, never a planner-time guess). `connection_name` defaults to
    `""` (the ordinary role path); `plan_ask`'s own answer-role ref is the
    one caller that passes a real one, for the Ask-time model-picker
    override."""
    return ModelRef(
        role=role,
        engine=resolved.engine,
        endpoint=resolved.endpoint,
        model_id=resolved.model_id,
        connection_name=connection_name,
    )


def _resolve_answer(payload: dict) -> tuple[ResolvedModel, str]:
    """Resolve the answer-role model for `payload`: an explicit `connection`
    pk (validated "chat"-capable via
    `models.registry.bindings.resolve_connection_named`) when
    `payload["connection"]` is present and non-blank, else `rag.answer`'s
    own role binding.

    Returns `(resolved, display_name)` -- `display_name` is the picked
    connection's name on the override path, or `""` on the role path (the
    role path's own "answered by" label -- which may be a synthetic
    "<model> (environment override)" string -- is only meaningful once the
    question has actually been answered; see `run_ask`'s own
    `answer_role_primary()` call).

    Raises `ValueError` for an unparseable/unknown/non-chat-capable pk, an
    unbound `rag.answer` role, or a pk this payload's actor may not USE
    (`models.registry.access.ModelAccess`, IA-2 T14, built from
    `principal_from_payload(payload)`) -- a caller bug in production (see
    module docstring: the web layer pre-checks both roles before ever
    enqueuing).
    """
    connection_param = payload.get("connection")
    if connection_param not in (None, ""):
        access = model_access_for(principal_from_payload(payload))
        return resolve_connection_named(int(connection_param), "chat", access=access)
    return resolve(RAG_ANSWER_ROLE), ""


def plan_ask(payload: dict) -> tuple[list[ModelRef], bool]:
    """Resolve the models a `rag.ask` job for `payload` needs, at ENQUEUE
    time. Returns `(model_refs, exclusive)`: both roles' `ModelRef`s
    (`footprint_bytes` left `None` -- claim-time code fills that in fresh,
    per `models/queue/scheduler.py`'s provenance contract) and
    `exclusive=False` (answering one question is cheap, ordinary
    concurrent work, never a machine-to-itself job).
    """
    answer_resolved, answer_name = _resolve_answer(payload)
    embed_resolved = resolve(RAG_EMBED_ROLE)

    model_refs = [
        _ref(RAG_ANSWER_ROLE, answer_resolved, answer_name),
        _ref(RAG_EMBED_ROLE, embed_resolved),
    ]
    return model_refs, False


def _precheck(payload: dict) -> tuple[dict[str, ResolvedModel], dict[str, str], str]:
    """Fresh re-resolve + health-check both roles for `payload`, at RUN
    time. The RESOLVE loop below is a deliberately-duplicated twin of
    `tools.rag.views._precheck_models`'s own (see module docstring
    for why the resolve loop itself isn't shared code); the dedup +
    health-check probe that follows it IS shared with that method (and
    with `SearchView`'s own embed-role check) via `tools.rag.messages.
    unreachable_endpoints` (C-15), using the same cause vocabulary
    (`tools.rag.messages.UNBOUND`/`UNREACHABLE`) either way.

    Returns `(resolved, causes, answer_name)`: `resolved` is every role
    that resolved (a subset on failure, both on success); `causes` maps
    each bad role to why; `answer_name` is the override connection's
    display name when `payload["connection"]` names one, else `""`.

    Precision divergence from the web path (deliberate): if the picked
    `connection` pk was deleted/renamed/de-capabilitated between enqueue
    and this run, `_resolve_answer` raises the SAME `ValueError`
    `resolve(RAG_ANSWER_ROLE)` would for an unbound role, so it's folded
    into the generic `UNBOUND` cause here -- `AskView` instead catches
    that case immediately, before its own `_precheck_models` ever runs, and
    reports the distinct `_UNREGISTERED_CONNECTION_MESSAGE` ("no longer
    registered"). Reproducing that pk-specific special case here would mean
    either importing it from `views.py` or duplicating its wording a third
    time; the generic "chat model isn't set up" copy is still accurate --
    just less pointed -- so this stays the one place the two paths'
    messages can legitimately differ.
    """
    resolved: dict[str, ResolvedModel] = {}
    causes: dict[str, str] = {}
    answer_name = ""

    for role in PRECHECK_ROLES:
        try:
            if role == RAG_ANSWER_ROLE:
                resolved[role], answer_name = _resolve_answer(payload)
            else:
                resolved[role] = resolve(role)
        except ValueError:
            logger.debug("rag.ask re-check: no inference binding resolved for role %r", role, exc_info=True)
            causes[role] = UNBOUND
        except Exception:  # noqa: BLE001 -- log detail, then degrade to the shared 503-equivalent message
            logger.exception("rag.ask re-check: unexpected error resolving inference binding for role %r", role)
            causes[role] = UNBOUND

    for role in unreachable_endpoints(resolved, log_prefix="rag.ask re-check"):
        causes[role] = UNREACHABLE

    return resolved, causes, answer_name


def run_ask(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:  # noqa: ARG001 - see docstring
    """Run a `rag.ask` job: re-check, answer, record, return the result.

    `models` (the job's claim-time `ModelRef` snapshot) is intentionally
    UNUSED for resolution -- required by the job-kind handler signature
    (`models.contracts.jobkinds.JobKind.handler`), but superseded here by a
    fresh re-resolve (`_precheck`) rather than trusted, since the queue wait
    between planning and running may have outdated it (see module
    docstring).

    `ctx` (T3, `models.contracts.jobkinds.JobContext`) is also unused --
    answering one question is a single retrieve-then-generate call with no
    natural intermediate boundary to report progress from or checkpoint at
    (see `JobContext`'s own docstring: a handler may ignore it entirely).

    1. Pre-run re-check: raises `RuntimeError` (the same copy the web 503
       shows) if either role fails to resolve or its endpoint fails health.
    2. Calls `tools.rag.retrieval.answer_question` with the freshly
       resolved models.
    3. Records a `tools.rag.services.record_ask` history row exactly like
       `AskView` does today -- success-only, best-effort (a write
       failure is logged, never allowed to fail the job).
    4. Returns `{"answer", "citations", "answered_by", "summary"}` -- a
       superset of today's 200 response body, plus `summary` (this job's
       one-line row summary, also available standalone via
       `summarize_ask`).
    """
    resolved, causes, answer_name = _precheck(payload)
    if causes:
        raise RuntimeError(model_unavailable_message(causes, resolved))

    question = payload["question"]
    result = answer_question(
        question,
        category=payload.get("category"),
        answer_resolved=resolved[RAG_ANSWER_ROLE],
        embed_resolved=resolved[RAG_EMBED_ROLE],
        visibility=document_visibility(principal_from_payload(payload)),
    )

    connection_param = payload.get("connection")
    answered_by = answer_name if connection_param not in (None, "") else answer_role_primary()[0]

    try:
        services.record_ask(
            question=question,
            category=payload.get("category"),
            connection_name=answered_by,
            model_id=resolved[RAG_ANSWER_ROLE].model_id,
            answer=result["answer"],
            citations=result["citations"],
            actor=principal_from_payload(payload),
        )
    except Exception:  # noqa: BLE001 -- history is best-effort, never fatal to the job
        logger.exception("rag.ask: failed to record Ask history for this question")

    return {
        "answer": result["answer"],
        "citations": result["citations"],
        "answered_by": answered_by,
        "summary": summarize_ask(payload),
    }


# One width for every job preview (C-31). Three call sites truncated to
# 120 characters and each of their docstrings pointed at the other two;
# `Truncator` is Django's, and `.chars` counts characters rather than
# bytes so a multibyte title is not cut mid-glyph.
PREVIEW_CHARS = 120


def _preview(text: str) -> str:
    """`text` truncated to `PREVIEW_CHARS`, for a queue row's own
    one-line summary."""
    return Truncator(text or "").chars(PREVIEW_CHARS)


def summarize_ask(payload: dict) -> str:
    """One-line, operator-facing row summary for the job queue's listing:
    `payload["question"]`, truncated the same way the Ask History page's
    own listing truncates each row's question (`django.utils.text.
    Truncator.chars`, the same engine the template's `|truncatechars:120`
    filter uses) -- so a queued job's row and its eventual `AskRecord` agree
    on one truncation convention rather than inventing a second."""
    return _preview(payload.get("question", ""))


# --- rag.ingest (media-into-RAG plan, T2; ADR 0014) -------------------
#
# The second job kind registered against the execution queue from this
# module -- see the module docstring above for `rag.ask`'s fuller treatment
# of the planner/handler split; the same two-pass shape applies here.
#
# Staging -- the source file's copy/move into the managed store, its
# dedup-on-hash Document row -- happens BEFORE this job is ever enqueued
# (`tools.rag.ingest.enqueue_ingest`/`enqueue_reingest` ->
# `tools.rag.ingest.stage_document`). By the time `plan_ingest`/
# `run_ingest` run, `payload["document_id"]` always names a real,
# already-staged Document row; this kind's own job is purely "do the
# parse/chunk/embed (or parse/rows) work and flip PROCESSING ->
# READY/FAILED" -- never "decide whether/how to stage".
#
# `payload` shape: `{"document_id": int, "sha256": str, "medium": str,
# "title": str}`. `sha256`/`medium`/`title` are enqueue-time snapshots
# (`stage_document`'s own hash, `tools.rag.readers.medium_for(ext)`, and
# the Document's title) rather than re-derived from the row at run/
# summarize time: `sha256` is RE-VERIFIED, not just trusted, by
# `tools.rag.ingest.run_ingest_for` against the stored file at run time
# (a queued job can sit for a while -- see that function's docstring for
# why a fresh check matters); `medium` lets `plan_ingest` branch without a
# DB read; `title` lets `summarize_ingest` render a row summary for a job
# that hasn't run yet (and even a FAILED job's payload still names the file
# it was trying to ingest).


def plan_ingest(payload: dict) -> tuple[list[ModelRef], bool]:
    """Resolve the models a `rag.ingest` job for `payload` needs, at
    ENQUEUE time (see the section comment above and `plan_ask`'s own
    docstring for the shared provenance contract: `footprint_bytes` is
    always left `None` here -- claim-time code fills it in fresh).

    `payload["medium"]` decides the shape:
    - "prose": ingest embeds (via `rag.embed`) -- truthfully. One
      `ModelRef` for the currently-resolved `rag.embed` binding,
      `exclusive=False` (embedding one document is ordinary, cheap,
      concurrent work, same register as `rag.ask`).
    - "tabular": genuinely model-free (ADR 0005 -- tabular ingest never
      embeds) -- `([], False)`. Honest, but not actually free concurrency:
      `models/queue/scheduler.py` rule 2(c) treats an empty `models` tuple
      as effectively exclusive (indistinguishable, at the scheduler's
      level, from a planner bug), so a queued CSV ingest still runs alone
      for its own brief duration. Accepted here as the honest answer rather
      than papered over with a dishonest `exclusive=True` or a fabricated
      model ref -- a dedicated "zero-footprint, genuinely concurrent" job
      class is a named ADR-0013-amendment fast-follow, not something this
      task solves.
    - "video"/"audio" (T7): TWO `ModelRef`s -- `rag.transcribe` (the whisper
      role a video/audio Document's transcription pass resolves,
      `tools.rag.media.transcribe_to_sidecar`) and `rag.embed` (the same
      role the "prose" branch above resolves, since a transcript is chunked
      and embedded exactly like any other prose text once
      `tools.rag.media.documents_from_extract` hands it back). Also
      `exclusive=False` -- a DELIBERATE divergence from
      `tools.vision.jobs.plan_generate`'s own `exclusive=True` for
      `vision.generate`, worth contrasting explicitly: that planner
      hardcodes `exclusive=True` rather than leaning on
      `models/queue/scheduler.py` rule 2(b) ("an unmeasured footprint is
      treated as effectively exclusive") the way this one does -- a
      defensive choice, not evidence about any particular adapter's
      capabilities either way. This planner takes the opposite, equally
      defensible position: `exclusive=False` is still the correct, honest
      declaration of what a `rag.ingest` transcription job actually IS
      (ordinary, not machine-hogging work), and rule 2(b) is the backstop
      that keeps that declaration safe regardless of whether the engine
      adapter resolved for `rag.transcribe` reports a footprint -- if it
      does, the job runs concurrently as declared; if it doesn't, rule
      2(b) still serializes it, with zero change to this function either
      way.
    - "image" / "pdf-scanned" (T8): TWO `ModelRef`s -- `rag.extract` (the
      vision-extraction role an image or a scanned/mixed PDF's extraction
      pass resolves, `tools.rag.media.extract_to_sidecar`) and
      `rag.embed` (the same role the "prose" branch above resolves, since
      extracted text is chunked and embedded exactly like any other prose
      text once `tools.rag.media.documents_from_extract` hands it back).
      "pdf-scanned" is `tools.rag.ingest._enqueue_ingest_job`'s own
      payload-medium override for EVERY ".pdf" while "media" is on
      (H28 review round 1, finding 2 -- PESSIMISTIC, off the extension
      alone, not a per-file auto-detect: see that function's own
      docstring for why an enqueue-time content scan was removed and this
      blanket override is what replaced it). "pdf-scanned" still covers a
      genuinely all-scanned PDF and a merely mixed one identically (W1
      decision D2; ADR 0014 §18 records why no separate token exists) --
      it now ALSO covers an ordinary text-layer PDF, deliberately: this
      planner reserves `rag.extract` capacity it will sometimes not end up
      needing, in exchange for never under-reserving it for a PDF that
      DOES need vision extraction, which the file itself decides, later,
      at `run_ingest_for`'s own job-start detection (`_needs_vision_
      extraction`, content-based, unchanged). `exclusive=False` -- same
      reasoning as "video"/"audio" above:
      `models.contracts.engines`' vision-capable adapters may or may not
      report `loaded_footprint` today, but this planner still declares the
      HONEST thing this job kind is (ordinary, not machine-hogging work)
      rather than copying `vision.generate`'s hardcoded `exclusive=True`.

      W1 DECISION D4 (enqueue-time half): `resolve(RAG_EXTRACT_ROLE)` is
      wrapped in `try/except ValueError` here and falls back to reserving
      `rag.embed` ALONE on an unbound role, logged once. Without this, D2's
      routing change means nearly every real-world PDF (a figures page, a
      blank verso) now trips the vision route -- and `models.queue.backend.
      enqueue` calls this planner SYNCHRONOUSLY at enqueue time
      (`backend.py`), so an unbound `rag.extract` would otherwise raise
      `ValueError` straight out of `enqueue()`, caught by `tools.rag.
      ingest._enqueue_ingest_job`'s bare `except Exception`, and the
      Document would be marked FAILED with the WRONG sentence ("Couldn't
      add this document to the queue" -- the queue was fine, a model
      wasn't bound). Falling back here lets the job actually run:
      `tools.rag.ingest.run_ingest_for`'s OWN fallback (`media.
      ModelRoleUnavailable`, `.pdf`-gated) then either ingests the text
      layer honestly (a mixed PDF) or fails at RUN time with
      `model_unavailable_message`'s own copy (a genuinely all-scanned PDF
      or an image) -- late, but with the TRUE reason, never "queue
      unavailable". Applies to "image" identically, not just "pdf-scanned"
      -- this planner has no file to re-probe and no reason to special-case
      one medium's enqueue-time resolve over the other; `run_ingest_for`'s
      own `.pdf`-only gate (W1 review N1) is what keeps an image's eventual
      failure honest, not this one.
    """
    medium = payload["medium"]
    if medium == "prose":
        resolved = resolve(RAG_EMBED_ROLE)
        return [_ref(RAG_EMBED_ROLE, resolved)], False
    if medium == "tabular":
        return [], False
    if medium in ("image", "pdf-scanned"):
        embed_resolved = resolve(RAG_EMBED_ROLE)
        try:
            extract_resolved = resolve(RAG_EXTRACT_ROLE)
        except ValueError:
            logger.info(
                "plan_ingest: no rag.extract binding resolved for a %r ingest -- reserving "
                "rag.embed only; run_ingest_for's own fallback (a mixed PDF) or honest failure "
                "(a fully-scanned PDF or an image) decides this job's real outcome at run time",
                medium,
            )
            return [_ref(RAG_EMBED_ROLE, embed_resolved)], False
        model_refs = [
            _ref(RAG_EXTRACT_ROLE, extract_resolved),
            _ref(RAG_EMBED_ROLE, embed_resolved),
        ]
        return model_refs, False
    if medium in ("video", "audio"):
        # T7 review m3: deliberately NOT gated on `"media" in settings.
        # FARABUNKER_FEATURES` here. The flag governs NEW staging only
        # (`tools.rag.ingest._doc_type_for_medium`/`_check_media_duration`/
        # `supported_exts()`) --
        # a Document that's ALREADY staged as video/audio (doc_type=PROSE,
        # media_type video/*) keeps working through this planner and
        # `run_ingest_for`'s own AV branch regardless of the flag's current
        # value, on purpose: disabling "media" mid-life must not strand
        # already-ingested content or block routine maintenance
        # (`enqueue_reingest`, `reencode_all`) on it -- only NEW intake
        # stops.
        transcribe_resolved = resolve(RAG_TRANSCRIBE_ROLE)
        embed_resolved = resolve(RAG_EMBED_ROLE)
        model_refs = [
            _ref(RAG_TRANSCRIBE_ROLE, transcribe_resolved),
            _ref(RAG_EMBED_ROLE, embed_resolved),
        ]
        return model_refs, False
    raise ValueError(f"medium={medium!r} is not a supported ingest medium.")


def run_ingest(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:  # noqa: ARG001 - see docstring
    """Run a `rag.ingest` job: load the staged Document, run it, report.

    `models` (the job's claim-time `ModelRef` snapshot) is unused --
    `tools.rag.ingest.run_ingest_for` doesn't need a resolved model
    handed to it: prose ingest resolves/builds its own embedder internally
    (`_ingest_prose`, the same `models.contracts.gateway.get_embed_model`
    path `rag.ask`'s embed role uses, just not re-plumbed through this
    handler's signature) -- kept only because `models.contracts.jobkinds.
    JobKind.handler`'s signature requires it, the same shape `run_ask`/
    `run_generate` accept and ignore for their own reasons.

    `ctx` (T3, `models.contracts.jobkinds.JobContext`) IS threaded through
    now (T7) -- to `tools.rag.ingest.run_ingest_or_fail`/`run_ingest_for`,
    which pass it straight to `tools.rag.media.transcribe_to_sidecar` for
    a video/audio medium, where it carries progress reporting and
    checkpoint/resume state across that transcription's own per-slice loop.
    A prose/tabular run still never looks at it -- `run_ingest_for` only
    reaches for `ctx` on the video/audio branch, so this parameter's
    threading costs a plain prose ingest nothing.

    Raises `RuntimeError` if `payload["document_id"]` no longer names a
    Document (a caller bug or a raced delete between enqueue and claim) --
    there's no row to mark FAILED in that case, so this is the one failure
    mode of this handler that does NOT write a Document status.

    Otherwise: `tools.rag.ingest.run_ingest_or_fail(doc, payload["sha256"], ctx)`
    -- PROCESSING -> parse/chunk/embed or parse/rows -> READY, or, on any
    exception, `doc.status = FAILED` + `status_detail = str(exc)` (written
    by `run_ingest_or_fail` itself -- the SAME shared wrapper
    `tools.rag.ingest.ingest_path`, the CLI's synchronous entry point,
    also goes through, so both callers of `run_ingest_for` agree on this
    one failure-handling shape rather than each reimplementing it) before
    RE-RAISING the same exception, so `models.queue.worker.Worker._execute`
    stores the identical `str(exc)` on the job row's own `error` column:
    the job's error and the Document's `status_detail` are always the same
    string, never two independently-worded accounts of one failure.

    THE PAGE CAP IS ONE OF THOSE EXCEPTIONS NOW (B-5, round-3 hardening
    H28). `tools.rag.ingest._check_document_pages` (`RagSettings.
    max_document_pages`) used to run at STAGE time, before this job ever
    existed -- it now runs at the top of `run_ingest_for`'s own dispatch,
    this handler's first real line of work, the same "queue worker, not a
    web request, so an unbounded scan is safe here" placement `_check_
    media_duration`'s own defensive run-time re-check
    (`tools.rag.media.transcribe_to_sidecar`, ADR 0014 §9) already had for
    the duration cap. An over-cap document now fails HERE, on its first
    (and, absent a raised cap + Retry, only) `rag.ingest` attempt, rather
    than never having reached the queue at all.
    """
    document_id = payload["document_id"]
    try:
        doc = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        raise RuntimeError(f"rag.ingest: Document {document_id} no longer exists -- nothing to ingest.")

    result = ingest.run_ingest_or_fail(doc, payload["sha256"], ctx)

    return {"summary": f"Ingested {result['title']}", "document_id": result["document_id"]}


def summarize_ingest(payload: dict) -> str:
    """One-line, operator-facing row summary for the job queue's listing:
    `payload["title"]`, truncated the same way `summarize_ask` truncates a
    question (`django.utils.text.Truncator.chars(120)`)."""
    return _preview(payload.get("title", ""))


def _fail_stranded_rows(rows, *, detail: str) -> int:
    """Leave any still-unfinished row in `rows` FAILED with `detail`, and
    return how many moved (C-31).

    THE CONDITIONAL FILTER IS THE RACE GUARD, not a nicety: a stale worker
    that is still alive may have written READY between the terminal
    callback firing and this update running, and clobbering that is worse
    than leaving a stranded row. Only PENDING/PROCESSING is repaired.

    THE LOGGING DELIBERATELY STAYS AT EACH CALL SITE. `on_ingest_terminal`
    spends a second `.exists()` query to tell "its own handler writeback
    got there first" (debug) apart from "the row is gone" (warning);
    `on_consolidate_terminal` has one debug line and no such query.
    Sharing that branch would make one path pay a query it does not need
    or make the other lose a distinction an operator reads. What is
    genuinely identical is the UPDATE, and that is all this owns.
    """
    return rows.filter(
        status__in=(Document.Status.PENDING, Document.Status.PROCESSING),
    ).update(status=Document.Status.FAILED, status_detail=detail, updated_at=timezone.now())


def on_ingest_terminal(payload: dict, state: str) -> None:
    """`rag.ingest`'s `models.contracts.jobkinds.JobKind.on_terminal` hook
    (T9.5 audit §5, "best-structured" option) -- the stranded-Document fix.

    A `rag.ingest` job that never reaches `run_ingest`/`run_ingest_or_fail`
    at all -- cancelled while still queued, permanently failed by a SECOND
    orphan sweep (`models.queue.claim._sweep_orphans`), or failed to even
    START its handler (`models.queue.worker.Worker._execute`'s
    `handler_started` guard, T9.5 review M5) -- leaves its Document row
    stuck exactly where `stage_document`/the first orphaning left it
    (PENDING or PROCESSING) forever: `run_ingest_or_fail`'s own FAILED
    write is the ONE place a `rag.ingest` job's failure normally reaches
    the Document row (see that function's docstring), and it never runs
    for any of these three paths, because the handler itself never
    started. `tools.rag.views.document_reingest` refuses to re-queue a
    PENDING/PROCESSING row ("already being processed") -- without this
    hook, a cancelled or twice-orphaned ingest is invisible to Retry
    forever, the exact trap `run_ingest_or_fail`'s own docstring already
    names for an uncaught exception, reached here a different way.

    Called by `models.contracts.jobkinds.invoke_on_terminal`, AFTER the job
    row's own terminal write commits (`models.queue.backend.cancel_job`/
    `models.queue.claim._sweep_orphans`/`models.queue.worker.Worker.
    _execute`, all via `transaction.on_commit`) -- any exception raised
    here is caught and logged by that caller, never propagated back into
    cancel/sweep/the worker's own writeback.

    ONE conditional `UPDATE` (T9.5 review M1) -- `Document.objects.filter(
    pk=document_id, status__in=(PENDING, PROCESSING)).update(...)` -- never
    a read-then-save. A read-then-save has a real window: this hook runs
    AFTER the job row's own terminal write already committed, so nothing
    stops a still-alive "stale" worker (one the orphan sweep gave up on,
    but that hadn't actually died) from finishing its own run and writing
    READY in between this function's read and its write, which a plain
    `doc.save()` would then silently clobber back to FAILED. The
    conditional `UPDATE`'s `status__in` guard makes that impossible: it
    only ever matches (and only ever writes) a row still PENDING/
    PROCESSING at the instant of the write itself, not at some earlier
    read. A `0`-row update means one of two honest things, told apart by
    a follow-up `exists()` check (only run on that path, never on the
    common success path): the Document is gone (a raced delete, or a
    malformed/legacy payload -- logged as a warning, nothing to fix), or
    it already reached READY/FAILED on its own before this hook ran (the
    handler's own terminal writeback, or a genuinely concurrent call to
    this same hook, got there first -- logged at debug, also nothing to
    fix, `status_detail` left exactly as that writeback left it).

    Sets `status=FAILED` with a state-specific, honest `status_detail` --
    Retry (`enqueue_reingest`) becomes available again either way:
    - "cancelled": "Cancelled from the queue before it finished."
    - "failed" (a permanent second-orphaning failure, or a worker failure
      before the handler ever started): "The worker running this job
      stopped responding; it was not retried again."
    """
    document_id = payload.get("document_id")
    detail = (
        "Cancelled from the queue before it finished."
        if state == "cancelled"
        else "The worker running this job stopped responding; it was not retried again."
    )
    updated = _fail_stranded_rows(Document.objects.filter(pk=document_id), detail=detail)

    if updated:
        return

    if Document.objects.filter(pk=document_id).exists():
        logger.debug(
            "rag.ingest on_terminal: Document %s already left PENDING/PROCESSING (its own "
            "handler writeback got there first) -- nothing to fix", document_id
        )
    else:
        logger.warning(
            "rag.ingest on_terminal: Document %s no longer exists -- nothing to fix", document_id
        )


# --- rag.consolidate (Workstreams v1, Task 18; spec §10) -----------------
#
# Distils one stream conversation into its contained "notes" Document --
# ONE contained note document PER CONVERSATION, overwritten and
# re-ingested on re-consolidation, never cascading to any other
# conversation's note. Registered by `tools/rag`, not `agents` (author
# decision 18): the handler needs the transcript (an `agents` row,
# reached through the sanctioned `agents.workstreams` seam), a model
# call, and a document write plus a re-ingest -- only this column can
# reach both ends.


def plan_consolidate(payload: dict) -> tuple[list[ModelRef], bool]:
    """The models a `rag.consolidate` job needs, at ENQUEUE time.

    TWO ROLES, so the job that distils is the job that ingests and the
    operator watches ONE queue row rather than two: `CHAT_CONVERSE_ROLE`
    for the distillation call and `RAG_EMBED_ROLE` for the re-ingest of
    the note. `plan_ingest` already returns more than one ref, so this is
    the established shape rather than a new one.

    `chat.converse`, NOT A NEW ROLE (author decision 24). Owner decision 6
    asks for a documented prompt constant, not a binding an operator must
    configure before the feature works: a box that can hold a
    conversation can distil one. A dedicated summariser role is a
    `RoleSpec` registration and a console binding away if a deployment
    ever wants one (spec §22).

    EXCLUSIVE, as every model-consuming kind is: the distillation call
    and the embed pass both want the machine.
    """
    return (
        [_ref(CHAT_CONVERSE_ROLE, resolve(CHAT_CONVERSE_ROLE)),
         _ref(RAG_EMBED_ROLE, resolve(RAG_EMBED_ROLE))],
        True,
    )


def run_consolidate(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:  # noqa: ARG001 - see docstring
    """Distil one stream conversation into its contained note document.

    SIX STEPS, IN SPEC §10.4'S ORDER, and the order is the design: the
    model call precedes the destroy, so a distillation failure destroys
    nothing (spec §24 concern 5).

    EXEMPT FROM THE LABELLING-AUTHORITY RULE, AND THE EXEMPTION IS THE
    POINT (author decision 27). `tools.rag.labels.set_document_labels`'
    own docstring says *"THE CALLER CHECKS THE PREDICATE"* --
    `may_label_document` plus the view's "owner of every entitlement
    being added or removed" rule. THIS CALLER CHECKS NEITHER. It applies
    `taint_ids_for_conversation(cid)`, which under ruling B can contain
    entitlements the actor neither owns nor holds, and the
    `DOCUMENT_LABELLED` rows are written in the actor's name. The
    authority rule governs a person CHOOSING labels for a document;
    nobody is choosing here -- the labels are copied from what the
    material already carried, and refusing to copy one because the actor
    does not own it is precisely the laundering owner decision 6 forbids.
    Named here so a reader working through `set_document_labels`' callers
    can see why one of them does not check.

    THE NOTE'S FIRST LINE IS THIS FUNCTION'S OWN PROSE, not the model's
    (author decision 17), so a model that ignored an instruction cannot
    make the note lie about its own provenance or about the cap.
    """
    from agents.workstreams import (
        owner_fields_for_conversation, record_consolidation,
        taint_ids_for_conversation, transcript_for,
    )
    from tools.rag.labels import set_document_labels

    actor = principal_from_payload(payload)
    conversation_id = payload["conversation"]

    # 1. THE TRANSCRIPT, through the agents seam. `None` fails the job
    #    with a named error rather than distilling something the actor
    #    may not read.
    turns = transcript_for(actor, conversation_id, limit=CONSOLIDATION_MAX_TURNS)
    if turns is None:
        raise RuntimeError(
            f"Conversation {conversation_id} is not readable by the principal this "
            f"job runs as, so there is nothing to consolidate.")
    if not turns:
        raise RuntimeError(
            f"Conversation {conversation_id} has no completed turns to distil.")

    # 2. ONE MODEL CALL. Before step 3, deliberately. FRESH-RESOLVED, not
    #    the enqueue-time `models` snapshot: `models` (the job's claim-time
    #    `ModelRef` snapshot) is otherwise UNUSED here, for the same reason
    #    `run_ingest`'s own docstring gives -- a queued job can sit for a
    #    while, and `ModelRef` carries only `engine`/`endpoint`/`model_id`,
    #    never the operator's own connection `config` (`context_window`
    #    among it, folded in by `models.registry.bindings.
    #    resolved_from_connection`). Rebuilding a partial `ResolvedModel`
    #    from the stale ref would silently drop that config and leave this
    #    one job -- which feeds up to `CONSOLIDATION_MAX_TURNS` turns into
    #    a SINGLE prompt -- running against the adapter's bare default
    #    context window instead of what the operator actually configured.
    #    `resolve(CHAT_CONVERSE_ROLE)` here is the exact re-check shape
    #    `run_ask`'s own `_precheck` uses (see that function's docstring).
    chat_resolved = resolve(CHAT_CONVERSE_ROLE)
    note_body = distil_conversation(turns, llm=get_llm_for(chat_resolved))

    # 3. A DETERMINISTIC PATH under `NOTES_DIR`, never under
    #    `INGEST_INBOX_DIR` -- the watcher polls the inbox, and a note
    #    written there would be staged twice, once by this job and once
    #    by the watcher as a UNIVERSAL document with a service principal
    #    for an actor (author decision 19).
    #
    #    Deterministic because `stage_document` dedups on
    #    `original_path`: the second consolidation writes the same path,
    #    finds the existing row, sees a changed `file_hash`, calls
    #    `_delete_existing_data`, and re-ingests IN PLACE -- same row,
    #    same id, same store directory, and every old `document:<id>`
    #    reference still resolves.
    # H13 review round 1, finding 10: `NOTES_DIR` is a FLAT shared root --
    # every conversation's note lands directly under it, not in a
    # per-conversation subdirectory -- so it is created if missing but
    # never force-chmod'd if it was already there (`only_if_created`,
    # same rule as `tools.rag.views.document_upload`'s inbox root). The
    # note FILE itself is this call's own, freshly written, and always
    # tightened.
    create_owner_only_dir(settings.NOTES_DIR, only_if_created=True)
    path = settings.NOTES_DIR / f"{conversation_id}.md"
    path.write_text(_note_text(payload, turns, note_body), encoding="utf-8")
    lock_down_file(path)

    # `move=False` leaves the file where this job wrote it; the managed
    # store gets its own copy, exactly as every other ingest does.
    #
    # `actor=None`, DELIBERATELY UNCHANGED (round-3 hardening H32/C-3,
    # round 2): `NOTES_DIR` is platform-owned, not one of the two roots
    # `tools.rag.store.assert_inside_platform_dirs` admits for a real
    # actor (B-3), and this job -- like `manage.py ingest` -- is exempt
    # from both that containment check and the re-stage authorization
    # check (`tools.rag.ingest._acts_for_the_box`'s own docstring).
    # ATTRIBUTION is stamped separately, in step 4 below, never through
    # this call's `actor` parameter.
    doc, _changed = ingest.stage_document(str(path), None, move=False,
                                          workstream_id=payload["workstream"])

    # 4. THE NOTE'S OWN FIELDS, in one transaction. AFTER staging, because
    #    `stage_document`'s re-stage branch overwrites `title` with the
    #    file's name -- so a title set before it would be lost on every
    #    re-consolidation.
    #
    #    ATTRIBUTED TO THE CONVERSATION'S OWNER (round-3 hardening
    #    H32/C-3, round 2): `owner_kind`/`owner_key` are stamped here,
    #    directly, from `owner_fields_for_conversation` -- the
    #    CONVERSATION's own two columns, not `actor` above (under ruling
    #    C the acting principal can be the STREAM owner consolidating a
    #    RECIPIENT's own thread, and it is the recipient's ownership the
    #    note should carry, not the button-presser's). `("", "")` if the
    #    conversation somehow no longer exists by the time this runs --
    #    impossible in practice (`turns` above already confirmed it
    #    exists), kept only so this line never raises on a `None`.
    #    INFORMATIONAL ONLY: this does NOT change who may re-stage the
    #    note (`stage_document`'s `actor` argument two lines above is
    #    unchanged, `None`) -- see `_acts_for_the_box`'s own docstring.
    #
    #    `workstream_id` IS ALSO RESTAMPED HERE, EXPLICITLY (H32 review
    #    round 1, IMPORTANT 2), not only passed to `stage_document` above:
    #    that call's own re-stage branch never touches `existing.
    #    workstream` at all when `_acts_for_the_box(actor)` is true (this
    #    job's own case, unchanged) -- it happens to survive a
    #    re-consolidation today only because nothing else clears it, an
    #    IMPLICIT invariant rather than a stated one. Stating it here,
    #    in the same write as the other explicit fields, makes "the note
    #    carries its workstream" a fact this function asserts on every
    #    run rather than one it merely never breaks.
    owner_kind, owner_key = owner_fields_for_conversation(conversation_id) or ("", "")
    with transaction.atomic():
        doc.title = _note_title(payload["title"])
        doc.origin = Document.Origin.NOTES
        doc.notes_conversation_id = conversation_id
        doc.workstream_id = payload["workstream"]
        doc.owner_kind = owner_kind
        doc.owner_key = owner_key
        doc.extraction = {
            "method": "distillation",
            "engine": chat_resolved.engine,
            "model_id": chat_resolved.model_id,
            # NO CONNECTION OVERRIDE for `chat.converse` (unlike
            # `rag.answer`'s Ask-time picker) -- `plan_consolidate` never
            # offers one, so this is always "".
            "connection_name": "",
            "produced_at": timezone.now().isoformat(),
        }
        doc.save(update_fields=["title", "origin", "notes_conversation_id",
                                "workstream_id", "owner_kind", "owner_key",
                                "extraction", "updated_at"])

    # 5. INHERIT THE TAINT. Owner decision 6: no laundering. A WIDENING
    #    ONLY -- the tags are additive, so the note's labels only ever
    #    grow, which is the property this step exists for: a note can
    #    never become MORE readable than the conversation it came from.
    set_document_labels(actor, doc, taint_ids_for_conversation(conversation_id))

    # 6. RE-INGEST IN PROCESS, using the embed model the planner admitted.
    #    THE DESTROY-THEN-RECREATE WINDOW IS HERE (spec §24 concern 5):
    #    step 3 already ran `_delete_existing_data` on a re-stage, so a
    #    failure now leaves the row at FAILED with no chunks and no
    #    stored file. `run_ingest_or_fail` writes that status and
    #    re-raises; the stream page renders the chip and offers
    #    Re-consolidate, which is the whole repair path.
    try:
        ingest.run_ingest_or_fail(doc, doc.file_hash, ctx)
    except Exception as exc:
        # DEFENCE IN DEPTH, NOT THE THING PROVEN BY THIS KIND'S OWN TEST
        # (`test_a_re_consolidation_whose_re_ingest_FAILS_is_visible_and_
        # repairable` forces a REAL failure inside `run_ingest_for`, so
        # the assertion there is `run_ingest_or_fail`'s OWN FAILED write,
        # not this branch). `run_ingest_or_fail` already writes FAILED +
        # `status_detail` before re-raising, so on every real failure
        # this is a no-op re-check that changes nothing. It exists only
        # for the case THAT wrapper's own write did not happen at all --
        # e.g. a genuine crash inside it before its own try/except runs
        # -- so the row still lands at FAILED rather than staying stuck
        # at PENDING/PROCESSING with no repair path visible on the
        # stream page.
        doc.refresh_from_db(fields=["status"])
        if doc.status in (Document.Status.PENDING, Document.Status.PROCESSING):
            doc.status = Document.Status.FAILED
            doc.status_detail = str(exc)
            doc.save(update_fields=["status", "status_detail", "updated_at"])
        raise

    # REQUIRED, not `.get(...)`: `through_index` is the caller's own
    # resolution of "the index this run consolidates through"
    # (`workstream_consolidate`'s own `latest_completed_turn_index` call,
    # named there as deliberately NOT re-read here -- see that view's own
    # comment), and a missing key would silently stamp
    # `consolidated_through_index=None`, which `staleness_for` reads as
    # "Not consolidated" forever, even after a successful run.
    record_consolidation(conversation_id,
                         through_index=payload["through_index"],
                         at=timezone.now())
    audit.record(actor, actions.WORKSTREAM_CONSOLIDATED, target_type="workstream",
                 target_key=payload["workstream"], target_label=payload.get("stream_name", ""),
                 conversation=str(conversation_id), document=doc.id,
                 turns=len(turns), capped=len(turns) >= CONSOLIDATION_MAX_TURNS)
    return {"document_id": doc.id, "turns": len(turns)}


# `title` is a 512-character column here, and `agents.chat.service.
# truncate_title` is `agents` code this column may not import -- so the
# note title is truncated locally at the column's own width, with the
# same ellipsis convention.
_TITLE_CAP = 512


def _note_title(conversation_title: str) -> str:
    """`"Notes — <title>"`, with an EM DASH, regenerated on every
    consolidation so a renamed conversation gets a renamed note."""
    full = f"Notes — {conversation_title or 'Untitled'}"
    return full if len(full) <= _TITLE_CAP else full[:_TITLE_CAP - 1] + "…"


def _note_text(payload: dict, turns: tuple[dict, ...], body: str) -> str:
    """The note file: this function's own provenance line, then the
    model's distillation.

    THE FIRST LINE IS THE JOB'S PROSE, NOT THE MODEL'S (author decision
    17). Spec §10.4 asks for "a first line naming the source conversation
    and the date", and the cap's honesty sentence -- "an honest partial
    beats a failure and beats a silent truncation" -- has to be true even
    when the model ignored every instruction it was given.
    """
    header = (f"Notes from conversation {payload['conversation']} "
              f"({timezone.now():%Y-%m-%d}).")
    if len(turns) >= CONSOLIDATION_MAX_TURNS:
        header += (f" This note covers the most recent {CONSOLIDATION_MAX_TURNS} turns of a "
                   f"longer conversation.")
    return f"{header}\n\n{body}\n"


def summarize_consolidate(payload: dict) -> str:
    """The queue listing's one-line row summary, truncated the way
    `summarize_ask` and `summarize_ingest` truncate theirs."""
    return _preview(f"Notes — {payload.get('title', '')}")


def on_consolidate_terminal(payload: dict, state: str) -> None:  # noqa: ARG001 - see docstring
    """`rag.consolidate`'s `on_terminal` hook -- the stranded-note fix.

    THE SAME SHAPE AND THE SAME REASON AS `on_ingest_terminal`, narrowed
    to the one row this kind can strand. A job cancelled while still
    queued, permanently failed by a second orphan sweep, or whose handler
    never started leaves NOTHING behind on a FIRST consolidation -- the
    note row does not exist until step 3 runs -- but on a
    RE-consolidation it can leave the PREVIOUS note at `PENDING`, because
    `stage_document` already destroyed its chunks and its stored file and
    set that status inside its own transaction before this job died.
    Without this hook that row is stuck at PENDING forever, and
    `tools.rag.views.document_reingest` refuses to re-queue a
    PENDING/PROCESSING row ("already being processed") -- so the note
    would be unrepairable from either the stream page or the library.

    ONE CONDITIONAL `UPDATE`, never a read-then-save, for exactly the
    reason `on_ingest_terminal`'s own docstring gives at length: this
    runs AFTER the job row's terminal write commits, so a still-alive
    "stale" worker can finish and write READY between a read and a write,
    which a plain `save()` would silently clobber back to FAILED. The
    `status__in` guard makes that impossible.

    `state` is unused: a cancelled job and a twice-orphaned one strand
    the row identically, and the operator-facing words come from
    `status_detail`, not from which of the two happened.
    """
    conversation_id = (payload or {}).get("conversation")
    if not conversation_id:
        logger.warning("rag.consolidate: terminal hook with no conversation in its payload")
        return
    updated = _fail_stranded_rows(
        Document.objects.filter(notes_conversation_id=conversation_id),
        detail="The consolidation job ended before it could rebuild this note. "
               "Re-consolidate from the workstream page.",
    )
    if not updated:
        logger.debug("rag.consolidate: nothing to clear for conversation %s "
                     "(no note yet, or it already reached a terminal status)",
                     conversation_id)
