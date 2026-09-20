"""
The model-consuming half of media ingestion (media-into-RAG plan T7/T8;
ADR 0014).

The layering law (see `tools.rag.transcode`'s own docstring for the first
half of it): `{readers, transcode, extract, sidecar} <- media <- ingest <-
jobs`. `transcode.py` (T5) is pure bytes-to-bytes conversion via an external
tool -- never a model, never the database, never the execution queue;
`extract.py` (T8) is the one-image-one-call vision request itself;
`sidecar.py` (T10 review MAJOR 1) is the model-free `extract.json` reader --
THIS module's own `_load_finished_sidecar_if_matching` reads a sidecar via
it too, the one place `sidecar.read_sidecar` is called from BOTH halves of
the pipeline (see that module's own docstring for why). THIS module is the
model-consuming half those feed: everything here either calls a resolved
inference role (whisper transcription, T7, via `extract.py`'s sibling
`transcode.py` output; vision text extraction, T8, this module owns the
loop/progress/checkpoint/sidecar driver around `extract.py`'s single call,
the same division `transcribe_to_sidecar` has with `get_transcriber_for`) or
reads/writes a `Document` row. `readers.py`/`transcode.py`/`extract.py`/
`sidecar.py` must never import this module -- the dependency arrow only
ever points this direction.

Imported ONLY from the worker/run half of the pipeline --
`tools/rag/jobs.py` (the `rag.ingest` handler, `run_ingest`) and
`tools/rag/ingest.py`'s own RUN half (`run_ingest_for`, and its
`_source_documents` dispatcher). NEVER from `tools/rag/views.py`
(HTTP-request threads must not block on a multi-minute transcription),
NEVER from `tools/rag/readers.py`/`tools/rag/transcode.py` (the
layering law above), and never from a synchronous request/response cycle
anywhere in this app -- exactly the same "runs out-of-band" contract
`tools.rag.ingest`'s own module docstring states for RUN-half code.

Heterogeneous-index note (T7 review m4): `apply_chunk_metadata_exclusions`'s
pollution fix only applies to chunks written AFTER it landed -- an upgrade
onto this fix leaves any already-ingested chunk carrying its old,
unexcluded metadata pollution, so a mixed index (old chunks polluted, new
chunks clean) is the honest state right after upgrade until an operator
re-encodes (Inference console -> `rag.embed` -> re-encode) once. Recorded
as a migration note in ADR 0014 section 16: the fix is forward-improving,
so a re-encode is RECOMMENDED, not required -- retrieval keeps working
either way.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from django.utils import timezone
from llama_index.core import Document as LlamaDocument

from foundation.files import create_owner_only_dir, lock_down_file
from models.contracts.bindings import ResolvedModel, resolve
from models.contracts.engines import get_engine
from models.contracts.gateway import get_llm_for, get_transcriber_for
from models.contracts.jobkinds import JobContext
from models.contracts.roles import RAG_EXTRACT_ROLE, RAG_TRANSCRIBE_ROLE
from tools.rag import extract, readers, store, transcode
from tools.rag.messages import UNBOUND, UNREACHABLE, model_unavailable_message
from tools.rag.models import Document, RagSettings
from tools.rag.sidecar import read_sidecar

logger = logging.getLogger(__name__)

# Greedy-group segments into windows of at most this many characters before
# turning them into a LlamaIndex `Document` (see `group_segments`/
# `documents_from_extract` below). 900, not `SentenceSplitter`'s own default
# `chunk_size` of 1024 TOKENS: characters undercount tokens for ordinary
# English prose (~4 chars/token), so a 900-character group comfortably
# survives the splitter as ONE chunk (no mid-group split) with real margin,
# rather than a value tuned to land exactly on the boundary. At typical
# spoken-word pace this is also roughly a minute of audio per group -- a
# citation that points at "start_seconds..end_seconds" for one chunk names a
# span a listener can actually scrub to and verify, not a single instant or
# an entire multi-hour file.
SEGMENT_GROUP_MAX_CHARS = 900

# The full metadata-key vocabulary any chunk node in this pipeline's prose
# path may carry, across every document type this platform ingests: prose
# (file_id/file_name/source_path/category; `page` from `readers._read_pdf`),
# and media (`start_seconds`/`end_seconds`, T7). Tabular data is never
# embedded (ADR 0005), so it never reaches `apply_chunk_metadata_exclusions`
# at all. T8's own image-OCR path is expected to reuse `page` (already
# listed here for that reason) rather than add a new key.
CHUNK_METADATA_KEYS = (
    "file_id",
    "file_name",
    "source_path",
    "category",
    "page",
    "start_seconds",
    "end_seconds",
)


def apply_chunk_metadata_exclusions(node_or_doc) -> None:
    """Exclude every key in `CHUNK_METADATA_KEYS` from both embed-time and
    LLM-time rendering of `node_or_doc` (a LlamaIndex `Document`, or a
    `TextNode`/`BaseNode` a splitter produced from one).

    THE POLLUTION FIX (pre-existing bug, fixed here for every document type,
    not only media): without this, LlamaIndex's default `metadata_mode`
    prepends every metadata key as a literal `"key: value"` line onto a
    node's own text before it is embedded or sent to an LLM. `file_id`/
    `file_name`/`source_path`/`category`/`page`/`start_seconds`/
    `end_seconds` are RETRIEVAL metadata -- ADR 0009's category filter, a
    citation's page/timestamp span -- never content that should shape a
    chunk's embedding vector or show up inside an answer's own context.
    Left unexcluded, every prose chunk this platform has ever indexed has
    been embedding (and feeding the LLM) a few lines of "file_id: 42
    file_name: notes.pdf ..." noise ahead of its real text.

    Called on the `Document` BEFORE splitting (`tools.rag.ingest._ingest_prose`)
    AND again on each `TextNode` AFTER splitting -- belt-and-suspenders, not
    strictly redundant: verified against the installed `llama-index-core`,
    `SentenceSplitter.get_nodes_from_documents` DOES propagate a parent
    `Document`'s `excluded_embed_metadata_keys`/`excluded_llm_metadata_keys`
    onto the nodes it produces, so the pre-split call alone already covers
    every key in `CHUNK_METADATA_KEYS` (this function always applies the
    FULL fixed vocabulary, regardless of what's actually in `.metadata` at
    the time -- see below). This codebase does not want to depend on that
    being every splitter's behavior forever, though, so the post-split call
    on each node is kept as an explicit guarantee -- cheap (set union is
    idempotent when the keys are already inherited) and correct regardless
    of which splitter runs or how a future LlamaIndex version behaves.

    Mutates `node_or_doc.excluded_embed_metadata_keys`/
    `.excluded_llm_metadata_keys` in place -- both default to `[]` on a
    fresh `Document`/`TextNode`. Extends (via set union) rather than
    replaces, so a caller that already excluded some other key of its own
    first never loses it. Naming a key that isn't actually present in
    `node_or_doc.metadata` is harmless (LlamaIndex's exclusion lists are
    just names to skip when present) -- so one shared, over-inclusive list
    is simpler, and safer against drift, than a bespoke per-medium subset
    that `ingest.py`/`media.py` would otherwise have to keep hand-in-sync.
    """
    node_or_doc.excluded_embed_metadata_keys = list(
        set(node_or_doc.excluded_embed_metadata_keys) | set(CHUNK_METADATA_KEYS)
    )
    node_or_doc.excluded_llm_metadata_keys = list(
        set(node_or_doc.excluded_llm_metadata_keys) | set(CHUNK_METADATA_KEYS)
    )


def duration_cap_message(name: str, duration: float, cap: int) -> str:
    """The shared "over the media duration cap" `ValueError` copy -- used by
    both `tools.rag.ingest._check_media_duration` (the STAGE-time check,
    the common case: this rejects an over-long upload/watch-folder drop
    before a queue job is ever created) and `transcribe_to_sidecar`'s own
    defensive re-check below (RUN time -- guards the window between staging
    and a queued job actually running, e.g. an operator lowering
    `RagSettings.max_media_seconds` in between). One function so the two
    checks can never drift into two different wordings for the same fact.
    """
    return (
        f"{name} is {duration:.0f}s long, over the {cap}s media duration limit "
        "(RagSettings.max_media_seconds) -- raise the cap in RAG settings, then retry, "
        "or trim the file to fit."
    )


def page_cap_message(name: str, page_count: int, cap: int) -> str:
    """The shared "over the document page cap" `ValueError` copy (T8
    review minor 4) -- the exact `duration_cap_message` pattern, one field
    over: used by both `tools.rag.ingest._check_document_pages` (the
    JOB-START check, the common case: B-5/H28, round-3 hardening, moved
    this off STAGE time -- this rejects an over-cap scanned PDF once a
    `rag.ingest` job actually runs, never inline in an upload/watcher
    thread) and `extract_to_sidecar`'s own defensive re-check below (a
    SECOND run-time re-check, guarding the narrower window between THAT
    job start and this driver actually running, e.g. an operator lowering
    `RagSettings.max_document_pages` in between the two). One function so
    every check can never drift into a different wording for the same
    fact.

    `page_count` (W1): the number of PAGES ACTUALLY RASTERIZED-OR-DUE-FOR-
    RASTERIZATION -- textless pages only, since W1 the cap counts only the
    per-page VISION-MODEL work a document requires, never its total page
    count (an ordinary text page in a mixed PDF never reaches the model at
    all). One wording covers both shapes (a genuinely all-scanned PDF,
    where this equals the total page count, and a mixed one, where it
    doesn't) without a caller-side branch: "{name} needs {n} page(s)
    extracted by a vision model, over the {cap}-page document limit...".
    """
    return (
        f"{name} needs {page_count} page(s) extracted by a vision model, over the {cap}-page "
        "document limit (RagSettings.max_document_pages) -- raise the cap in RAG settings, "
        "then retry, or trim the document to fit."
    )


def page_scan_ceiling_message(name: str, examined: int) -> str:
    """The "the scan could not conclude within its own ceiling" `ValueError`
    copy (H28 review round 1, finding 1) -- DISTINCT from `page_cap_message`
    above, on purpose: that message means "I finished counting, and the
    count is over the cap"; this one means "I could not finish counting at
    all, within `readers.PDF_SCAN_MAX_PAGES_EXAMINED` pages examined, so I
    genuinely do not know whether this document is over
    `RagSettings.max_document_pages` or not". Conflating the two would tell
    an operator "raise the cap" for a problem raising the cap does not fix
    (a colossal all-text PDF whose textless-page COUNT may be zero) --
    naming the ceiling and how many pages were actually looked at, instead,
    points at the actual, honest limitation: this file is too large for
    this platform to make the page-cap decision on at all, in the time one
    worker is willing to spend finding out. FAIL-CLOSED, not fail-open:
    `tools.rag.ingest._check_document_pages` refuses the document rather
    than guessing "probably fine" (silently under-cap) or "probably over"
    (silently rejecting a legitimate all-text document) -- see that
    function's own docstring for the caller-side decision this message
    reports for.
    """
    return (
        f"{name} could not be checked against the document page limit "
        f"(RagSettings.max_document_pages) -- the scan did not conclude within "
        f"{examined} page(s) examined. This document is likely too large to check safely; "
        "trim it, split it, or raise RagSettings.max_document_pages and re-ingest to accept "
        "the cost of a full scan."
    )


class ModelRoleUnavailable(RuntimeError):
    """`_resolve_role_or_fail`'s own exception (W1, ADR 0014 §18) -- a
    `RuntimeError` SUBCLASS, deliberately, so every existing `except
    Exception` / bare re-raise path (`run_ingest_or_fail`, the queue
    worker's own writeback) is byte-for-byte unchanged and keeps catching
    this exactly as it always caught the bare `RuntimeError` this replaces.
    The only thing that changes is that ONE new call site --
    `tools.rag.ingest.run_ingest_for`'s text-only fallback (D4) -- can
    now tell "no vision model available" apart from every OTHER
    `RuntimeError` this pipeline can raise (a sha mismatch, an incomplete
    sidecar, an engine transport failure) and route around it for a `.pdf`
    specifically, instead of failing the whole document outright."""


def _resolve_role_or_fail(role: str) -> ResolvedModel:
    """Fresh resolve + health re-check of `role`, at the moment work is
    actually about to run -- the same resolve-then-health-check shape
    `tools.rag.jobs.run_ask`'s own `_precheck` uses for `rag.answer`/
    `rag.embed` (module docstring: "the run_ask _precheck pattern"), one
    role instead of two. Shared by `transcribe_to_sidecar`
    (`RAG_TRANSCRIBE_ROLE`) and `extract_to_sidecar` (`RAG_EXTRACT_ROLE`,
    T8) -- consolidated from two near-identical functions
    (`_resolve_transcribe`/`_resolve_extract`) that differed only in which
    role constant they closed over.

    Raises `ModelRoleUnavailable` (a `RuntimeError` subclass, W1) carrying
    `tools.rag.messages.model_unavailable_message`'s copy -- the SAME
    operator-facing sentence shape the Ask page's 503 and `run_ask`'s own
    re-check failure use. `role` must have an entry in `messages.
    _ROLE_LABELS` (both `RAG_TRANSCRIBE_ROLE: "transcription"` and
    `RAG_EXTRACT_ROLE: "image text extraction"` do) so the single-role
    branch of `model_unavailable_message` reads as "the transcription model
    isn't set up yet" / "... is unreachable" rather than a KeyError -- see
    that module's own docstring for why this formatting is shared, not
    reimplemented here.
    """
    try:
        resolved = resolve(role)
    except ValueError:
        logger.debug("media: no inference binding resolved for role %r", role, exc_info=True)
        raise ModelRoleUnavailable(model_unavailable_message({role: UNBOUND}, {})) from None

    if not get_engine(resolved.engine).is_healthy(resolved.endpoint):
        raise ModelRoleUnavailable(model_unavailable_message({role: UNREACHABLE}, {role: resolved}))
    return resolved


def _write_sidecar_atomic(path: Path, data: dict) -> None:
    """Write `data` as JSON to `path`, atomically -- write to a sibling
    `.tmp` file, then `Path.replace` (a single rename) over the real path.
    Every reader of `path` (`tools.rag.ingest._source_documents`, and this
    module's own resume-read below) therefore only ever sees either the
    prior complete write or the new complete write, never a partially
    written file -- matching `models.queue.worker.py`'s own atomic-write
    convention for job state, applied here to the sidecar file instead of a
    DB row.
    """
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    tmp_path.replace(path)
    # H13 review round 1, finding 10: `path` lives inside a document's own
    # already-owned directory (`tools.rag.store.sidecar_path`) -- `replace`
    # is a rename, it does not change `tmp_path`'s own (OS-default) mode.
    lock_down_file(path)


def _sidecar_payload(
    stored_path: Path,
    source_sha256: str,
    duration: float,
    language: str | None,
    resolved: ResolvedModel,
    segments: list[dict],
    *,
    produced_at: str | None,
) -> dict:
    """Build the extraction sidecar's schema v1 dict (module docstring):
    `version`/`method`/`source`/`source_sha256`/`duration_seconds`/
    `language`/`model` (a snapshot, matching `Document.extraction`'s own
    snapshot rationale -- never a live reference)/`produced_at`/`segments`.

    `source_sha256` (T7 round-3 review MAJOR): `doc.file_hash` at the
    moment this sidecar was written -- the identity signal
    `_load_finished_sidecar_if_matching` compares against on a later run,
    in place of a duration probe (see that function's own docstring for
    why a duration comparison was wrong). Free to record here: by the time
    `transcribe_to_sidecar` runs, `run_ingest_for` has ALREADY re-verified
    `doc.file_hash` against a fresh hash of the stored file (its own sha
    mismatch check, before ever dispatching to this branch) -- so this is
    zero extra I/O, just carrying a value already known to be fresh.

    Called TWICE by `transcribe_to_sidecar`: once per checkpoint, mid-loop,
    with `produced_at=None` (still in progress -- the sidecar IS the
    resumable accumulator, see that function's docstring) and once at the
    very end with a real `produced_at` timestamp -- the same shape either
    way, so a reader never has to special-case an in-progress sidecar's
    structure, only whether `produced_at` is set.
    """
    return {
        "version": 1,
        "method": "whisper",
        "source": stored_path.name,
        "source_sha256": source_sha256,
        "duration_seconds": duration,
        "language": language,
        "model": {"engine": resolved.engine, "model_id": resolved.model_id},
        "produced_at": produced_at,
        "segments": list(segments),
    }


def _extraction_sidecar_payload(
    stored_path: Path,
    source_sha256: str,
    resolved: ResolvedModel,
    segments: list[dict],
    *,
    produced_at: str | None,
    rasterized_pages: list[int] | None = None,
    described: bool | None = None,
) -> dict:
    """Build the VISION EXTRACTION sidecar's schema v1 dict (T8) -- the
    `extract_to_sidecar` sibling of `_sidecar_payload` (transcription):
    `version`/`method`/`source`/`source_sha256`/`model`/`produced_at`/
    `segments`. No `duration_seconds`/`language` keys -- neither concept
    applies to a page/image extraction (whisper-specific fields of
    `_sidecar_payload`, kept out here rather than carried through as
    meaningless `None`s that every future reader of this sidecar shape
    would have to remember never mean anything).

    `rasterized_pages` (W1, ADR 0014 §6): ONE new key, a `.pdf`-only
    addition -- emitted only when not `None` (an image/AV sidecar keeps
    today's shape exactly, the same "don't carry meaningless `None`s" rule
    the paragraph above already states for `duration_seconds`/`language`).
    **It is the PLANNED set, not "pages done so far"**: the full list of
    textless page numbers this run INTENDS to process
    (`readers.pdf_textless_pages`'s own output), written IDENTICALLY on
    every checkpoint write and the completion write -- never grown
    incrementally as pages finish. W1 review MINOR 4: this is NOT what
    makes it safe for `tools.rag.ingest._source_documents` to read one
    as a *skip* set -- that function's own completeness gate rejects an
    in-progress sidecar (`produced_at` unset) before `rasterized_pages` is
    ever looked at, so `_source_documents` in practice never reads this
    key off anything but a genuinely FINISHED sidecar. The real property
    "written identically on every write" buys is simpler and holds
    regardless of that gate: every writer of this key, at every point in
    the run, agrees on what it says, so whichever write a reader happens
    to land on -- finished or not -- sees the SAME planned set, never a
    partial or stale one; a reader never has to ask "as of which write"
    the list reflects.

    `method: "vision"` -- the sidecar's own ALGORITHM-level label (mirrors
    `_sidecar_payload`'s `"whisper"`), deliberately NOT the same string
    `Document.extraction["method"]` gets. Those two are different strings
    ON PURPOSE: `Document.extraction_summary`'s verb map
    (`tools.rag.models.Document`) only knows `"transcription"|"extraction"`
    -- pre-existing vocabulary this task does not change -- so
    `extract_to_sidecar` stamps `doc.extraction["method"] = "extraction"`
    (the UI-facing category, "Extracted by ...") while THIS sidecar's own
    top-level `method` names the actual algorithm that produced it
    (`"vision"`, matching `"whisper"`'s own role at this same layer) --
    exactly the same split `_sidecar_payload`/`transcribe_to_sidecar`
    already draw between `"whisper"` (here) and `"transcription"` (the
    Document snapshot).

    `segments`: `[{"page": int, "text": str}, ...]`, one entry per
    successfully-extracted page/image, in page order -- a page/image that
    produced no text is never appended at all (`extract_to_sidecar`'s own
    skip+log, the `tools.rag.readers._read_pdf` blank-page precedent),
    never stored as an empty segment. ON THE IMAGE PATH ONLY (preview
    UAT, 2026-09-17) a segment may carry a THIRD, optional key,
    `"kind": "description"`, marking the one segment that holds the
    image's own description rather than text read off it -- always
    first, and always page 1. Additive by construction: every reader
    branches on `"page" in segment`/`segments[0]` and ignores the extra
    key, so a segment without it (every scanned-PDF page, and every
    sidecar written before this) is read exactly as it always was.

    `source_sha256`: identical role to `_sidecar_payload`'s own parameter
    of the same name -- see that function's docstring; `_load_finished_
    sidecar_if_matching` reads it the same way regardless of which of
    these two builders produced the sidecar on disk.

    `described` (preview UAT, 2026-09-17): ONE more key, IMAGE-ONLY --
    emitted only when not `None`, exactly as `rasterized_pages` above is
    `.pdf`-only, and for the identical "don't carry meaningless `None`s"
    reason this docstring already states for `duration_seconds`/
    `language`. It records that the DESCRIPTION STEP RAN, which is a
    different fact from whether it produced anything: a sidecar with no
    segments AND this marker means "we looked and there was nothing to
    say", while a sidecar with no segments and NO marker means "this was
    extracted before descriptions existed at all". Those two deserve
    different sentences in front of an operator (`tools.rag.access.
    image_caption`'s `"empty"` versus `"undescribed"`), and without this
    key they are indistinguishable -- which is exactly the steward
    condition (S2) this key exists for.
    """
    payload = {
        "version": 1,
        "method": "vision",
        "source": stored_path.name,
        "source_sha256": source_sha256,
        "model": {"engine": resolved.engine, "model_id": resolved.model_id},
        "produced_at": produced_at,
        "segments": list(segments),
    }
    if rasterized_pages is not None:
        payload["rasterized_pages"] = list(rasterized_pages)
    if described is not None:
        payload["described"] = bool(described)
    return payload


def _load_finished_sidecar_if_matching(sidecar_path: Path, stored_path: Path, file_hash: str) -> dict | None:
    """If `sidecar_path` already holds a FINISHED transcription/extraction
    (`produced_at` set) for `stored_path`'s CURRENT content, return it
    as-is; otherwise `None` (T7 review M3a -- "orphaned success" guard).
    Called by both `transcribe_to_sidecar` and `extract_to_sidecar` (T8) via
    their shared `_reuse_finished_sidecar` wrapper: this function only ever
    reads `produced_at`/`source`/`source_sha256`, keys both `_sidecar_payload`
    (transcription) and `_extraction_sidecar_payload` (vision) write, so it
    is generic over which of the two ever wrote `sidecar_path` -- no
    branch on `sidecar["method"]` needed here at all.

    A prior attempt's transcription can complete cleanly (sidecar written,
    `doc.extraction`/`doc.duration_seconds` saved, `work/` removed) and the
    SAME `run_ingest_for` call still fail later, in the embed phase (a
    transient embed-role outage, say) -- `doc.status` ends up FAILED, but
    the transcription itself was never lost. Without this check, a retry
    (`enqueue_reingest`: a brand-new job, `checkpoint_state=None`) would
    re-run the FULL extract/slice/transcribe pipeline from scratch even
    though nothing about the transcription needs redoing -- turning a retry
    that should cost seconds (just re-embedding) into one that costs
    minutes again, and making `work/`'s removal on the prior clean finish
    look like a mistake in hindsight when it wasn't.

    "Matching": the sidecar is readable, `produced_at` is set (a genuinely
    FINISHED sidecar -- an in-progress one always has `produced_at=None`,
    the same completeness rule `tools.rag.ingest._source_documents`
    enforces before ever indexing one, M4), its recorded `source` equals
    `stored_path.name`, and its recorded `source_sha256` equals `file_hash`.

    T7 round-3 review MAJOR (probe-reproduced): this used to compare a
    FRESH `transcode.probe_duration(stored_path)` against the sidecar's own
    `duration_seconds` -- which never matched for a single video format.
    `duration_seconds` is the DECODED AUDIO length (measured on the
    extracted WAV, after `transcode.extract_audio`); a fresh probe of
    `stored_path` measures the CONTAINER's own duration (mp4/mov/mkv/webm),
    which is quantized to the container's stream/frame-rate metadata, not
    the decoded sample count -- the two differ by design, not by bug, for
    every container format, so this check NEVER short-circuited a video at
    all. `file_hash` (`doc.file_hash`, content-addressed) is the correct
    identity signal: it doesn't drift between the container and its
    decoded audio, and it costs NOTHING extra to check here -- by the time
    `transcribe_to_sidecar` runs, `run_ingest_for` has ALREADY re-verified
    `doc.file_hash` against a fresh hash of the stored file (its own sha
    mismatch check), so this comparison is against a value already known
    fresh, not a new hash computed over a multi-GB file.

    Returns `None` (falls through to an ordinary transcription run) on any
    mismatch or read/parse failure -- never guesses that stale-looking data
    is still good.

    HASH/SOURCE/`produced_at` ONLY -- deliberately unaware of `"described"`
    (review fix round 2, steward finding). This function's own contract is
    "does this sidecar still describe `stored_path`'s CURRENT bytes", a
    content-identity question; "did the description step ALSO run" is a
    separate, method-specific question `_reuse_finished_sidecar` below
    answers with its own `require_described` argument, layered on TOP of
    whatever this function returns -- keeping this function generic over
    transcription and extraction sidecars alike, exactly as it always was.

    Reads via `tools.rag.sidecar.read_sidecar` (T10 review MAJOR 1
    follow-up) rather than its own inline `json.loads`/`except (OSError,
    ValueError)` -- a pure win here: `read_sidecar` catches the exact same
    two exception types this function already did (so no behavior change
    for every well-formed-or-unreadable sidecar this has ever seen), PLUS
    the shape check this function never had -- a sidecar that somehow
    decoded to a JSON list/string/number (never produced by
    `_sidecar_payload`/`_extraction_sidecar_payload`, but this function
    has no way to prove that from here) used to make the very next line,
    `existing.get("produced_at")`, an uncaught `AttributeError` instead of
    the clean "nothing to reuse" `None` every other failure mode already
    got.
    """
    existing = read_sidecar(sidecar_path)
    if existing is None:
        return None
    if not existing.get("produced_at"):
        return None
    if existing.get("source") != stored_path.name:
        return None
    if existing.get("source_sha256") != file_hash:
        return None
    return existing


def _stamp_extraction(
    doc: Document,
    *,
    method: str,
    engine: str | None,
    model_id: str | None,
    produced_at: str | None,
    duration: float | None = None,
    write_duration: bool = False,
) -> None:
    """Write `doc.extraction` (the five-key `{method, engine, model_id,
    connection_name, produced_at}` snapshot dict `transcribe_to_sidecar`/
    `extract_to_sidecar` each stamp) and save it -- the ONE place this dict
    is built, replacing four longhand copies of it (each driver's
    short-circuit head and completion tail). `connection_name` is always
    `""` here -- neither driver ever resolves through a picked-connection
    override, so there is never a name to record beyond the empty default
    every non-override `ModelRef`/extraction snapshot already uses
    elsewhere in this app.

    `write_duration` (T9.5 review M2): when `True`, also sets
    `doc.duration_seconds = duration` in the SAME save call -- used ONLY
    by the short-circuit head (`_reuse_finished_sidecar`) for
    `method="transcription"`, gated on METHOD, not on `duration`'s own
    value. This matters because a finished sidecar is the sole source of
    truth for duration once it's being reused, and must be applied even
    when the sidecar happens to lack the key: `duration=None` then WRITES
    `None`, clearing any stale prior value -- exactly matching the
    pre-consolidation short-circuit's own unconditional `doc.
    duration_seconds = finished.get("duration_seconds")` write. Gating on
    `duration is not None` instead (the T9.5 audit's finding) would
    silently drift into "leave the stale value untouched whenever the
    sidecar doesn't carry the key," which is a real, different behavior.

    `write_duration=False` (the default) leaves `duration_seconds`
    untouched entirely -- not even named in `update_fields` -- matching
    both `transcribe_to_sidecar`'s own completion tail (which already
    saved `duration_seconds` separately, earlier, before the cap check
    ran, so re-saving it here would be redundant, not wrong, but this
    keeps that tail's `update_fields` minimal) and `extract_to_sidecar`'s
    two call sites (a page/image extraction has no duration concept at
    all, see `_extraction_sidecar_payload`'s own docstring).
    """
    doc.extraction = {
        "method": method,
        "engine": engine,
        "model_id": model_id,
        "connection_name": "",
        "produced_at": produced_at,
    }
    update_fields = ["extraction", "updated_at"]
    if write_duration:
        doc.duration_seconds = duration
        update_fields.append("duration_seconds")
    doc.save(update_fields=update_fields)


def _reuse_finished_sidecar(
    doc: Document, sidecar_path: Path, stored_path: Path, *, method: str,
    require_described: bool = False,
) -> dict | None:
    """The short-circuit HEAD both `transcribe_to_sidecar` (T7,
    `method="transcription"`) and `extract_to_sidecar` (T8,
    `method="extraction"`) share: if `sidecar_path` already holds a
    FINISHED transcription/extraction matching `stored_path`'s CURRENT
    content (`_load_finished_sidecar_if_matching`), stamp `doc.extraction`
    (and, for `method="transcription"`, `doc.duration_seconds`) from it via
    `_stamp_extraction` and return the sidecar dict; `None` (nothing to
    reuse -- the caller proceeds to resolve a model and run) otherwise.

    `method` doubles as the sidecar's own algorithm-neutral log noun here
    ("transcription"/"extraction") -- the exact two strings each driver's
    own former inline log line already used ("already has a finished
    {method} sidecar ... skipping re-{method}"), so this consolidation
    changes no operator-visible wording.

    `require_described` (review fix round 2, steward finding, preview UAT
    2026-09-17): `extract_to_sidecar`'s IMAGE call passes `True` for
    exactly the reason its own docstring at that call site gives --
    `"undescribed"` (`tools.rag.access.image_caption`'s own state for a
    sidecar that predates the description prompt) is supposed to be
    REPAIRABLE by re-ingesting the image, and without this it never was:
    `enqueue_reingest` (`tools.rag.ingest.py`) does not delete the old
    sidecar or touch the source bytes, so a fresh `rag.ingest` job hashes
    an IDENTICAL file and this exact short-circuit matched every time --
    the old, undescribed sidecar came back verbatim, `describe_image`
    never ran, and the remedy sentence this whole state's line points at
    ("re-ingest to describe it") was a dead end. `False` (the default,
    and every PDF/whisper caller) is UNCHANGED behaviour -- a PDF's page
    sidecar and a scanned-page's own text never carry `"described"` at
    all (image-only key, `_extraction_sidecar_payload`'s own docstring),
    so requiring it there would make EVERY PDF re-ingest pointlessly
    redo its full vision pass.
    """
    finished = _load_finished_sidecar_if_matching(sidecar_path, stored_path, doc.file_hash)
    if finished is None:
        return None
    if require_described and not finished.get("described"):
        # FALL THROUGH, do not reuse: an old image sidecar that predates
        # the description prompt matches on hash/source/produced_at but
        # is missing the one thing `require_described` exists to demand.
        # Logged at the SAME level and shape as the ordinary "nothing to
        # reuse" path below reaches via `extract_to_sidecar` re-running
        # from scratch -- no separate log line needed here, since the
        # caller's own driver already logs its own progress once it
        # actually runs.
        return None
    logger.info(
        "media: Document %s already has a finished %s sidecar (%s) matching its current "
        "source file -- skipping re-%s",
        doc.id, method, sidecar_path, method,
    )
    duration = finished.get("duration_seconds") if method == "transcription" else None
    _stamp_extraction(
        doc,
        method=method,
        engine=finished.get("model", {}).get("engine"),
        model_id=finished.get("model", {}).get("model_id"),
        produced_at=finished.get("produced_at"),
        duration=duration,
        write_duration=(method == "transcription"),
    )
    return finished


def _reconcile_sidecar_resume(
    doc_id: int, sidecar_path: Path, checkpoint_state: dict, *, index_key: str, unit_noun: str
) -> tuple[list[dict], int, dict | None]:
    """Reconcile a resumed attempt's checkpoint against the sidecar it left
    behind, and return `(segments, next_index, existing_payload)` for a
    per-unit resume loop to continue from (T7 review M1/M2) -- shared by
    `transcribe_to_sidecar`'s per-SLICE loop (`index_key="next_slice_index"`,
    `unit_noun="slice"`) and `extract_to_sidecar`'s per-PAGE loop (T8,
    `index_key="next_page_index"`, `unit_noun="page"`). Consolidated from
    two near-identical functions (`_reconcile_resume_state`/`_reconcile_
    page_resume_state`) that differed only in which checkpoint key names
    the next unit and which word their log/error copy uses for one unit of
    work -- "at most one segment per page" (a page either produced text or
    didn't) is a simpler shape than whisper's "a slice can produce several
    segments at once", but the reconciliation logic below is unaffected by
    that difference, since it only ever compares COUNTS, never assumes a
    fixed segments-per-unit ratio.

    The sidecar and its checkpoint are written in a FIXED order every
    iteration of the caller's loop -- `_write_sidecar_atomic` THEN
    `ctx.checkpoint(...)` -- so a crash between those two writes leaves the
    SIDECAR one unit AHEAD of the last CONFIRMED checkpoint: the sidecar
    already holds the segment(s) from the unit that was in flight when the
    prior attempt died, but the checkpoint call recording that unit as done
    never completed. `checkpoint_state["segments_written"]` names exactly
    how many segments were confirmed as of that last successful checkpoint
    call, which is what lets this function tell the difference between
    "the sidecar is honestly ahead" (expected, recoverable) and "something
    is actually wrong" (not recoverable by guessing):

    - Sidecar unreadable (missing/corrupt/truncated `extract.json`, despite
      any per-unit work files themselves still being on disk): the
      segments it held are unrecoverable. Logs the corruption and returns
      `next_index=0`/`existing_payload=None` (a full redo -- slow but
      correct) rather than keeping a stale index that would skip units
      whose segments no longer exist anywhere (M2).
    - `len(segments) > segments_written` (the sidecar is AHEAD of its own
      checkpoint -- the exact inconsistency the crash above produces):
      truncates `segments` back to `segments_written`, discarding the
      not-yet-confirmed tail, and lets the loop's own `next_index` (still
      pointing at that same unit, since ITS checkpoint call never landed
      either) redo it honestly -- rather than reprocessing that unit and
      ending up with two copies of its segments (M1).
    - `len(segments) < segments_written` (the sidecar is BEHIND its own
      checkpoint): impossible under this module's own write order above,
      so this can only mean the sidecar lost a write some other way after
      the fact (an external truncation, a filesystem issue). There is no
      honest way to recompute a resume point from a segment COUNT alone --
      this checkpoint does not track segments-per-unit -- so this raises
      `RuntimeError` naming the inconsistency rather than guessing which
      unit to redo (M1, the other direction).

    `existing_payload` is the whole parsed sidecar dict on a SUCCESSFUL
    read (`None` only on the unreadable-sidecar path) -- so a caller that
    needs another field off it (`transcribe_to_sidecar`'s own `language`)
    reads it straight off this return value instead of re-parsing the
    file a second time.
    """
    # The operator-facing verb for "redo this whole pipeline from scratch"
    # differs by which caller this is -- "retranscribing"/"re-transcribe"
    # for whisper's per-slice loop, "re-extracting"/"re-extract" for the
    # vision per-page loop -- the exact two verbs each former standalone
    # function (`_reconcile_resume_state`/`_reconcile_page_resume_state`)
    # used before this consolidation. Derived from `unit_noun` rather than
    # a generic "re-run" so this consolidation changes no operator-visible
    # wording (T9.5 review M-nit).
    verb_gerund = "retranscribing" if unit_noun == "slice" else "re-extracting"
    verb_imperative = "re-transcribe" if unit_noun == "slice" else "re-extract"

    # T10 re-review MINOR 2: reads via `tools.rag.sidecar.read_sidecar`
    # (the same fix `_load_finished_sidecar_if_matching` above already
    # got in the prior T10 pass) rather than its own inline `json.loads`/
    # `except (OSError, ValueError)`. That inline form caught the same
    # read/parse failures `read_sidecar` does, but not the THIRD one
    # `read_sidecar` also guards: a sidecar that decoded to valid JSON
    # that isn't an object at the top level (a bare list/string/number --
    # never produced by this module's own writers, but not provable from
    # here) used to sail past the `except` untouched and make the very
    # next line, `existing.get("segments")`, an uncaught `AttributeError`
    # instead of the same clean "nothing to resume from, start over at 0"
    # every other unreadable-sidecar case already gets.
    existing = read_sidecar(sidecar_path)
    if existing is None:
        logger.warning(
            "media: extract.json for Document %s is unreadable on resume (missing/corrupt/"
            "truncated) -- discarding it and %s from the beginning",
            doc_id, verb_gerund,
        )
        return [], 0, None

    segments = list(existing.get("segments") or [])
    next_index = int(checkpoint_state.get(index_key, 0))
    segments_written = int(checkpoint_state.get("segments_written", len(segments)))

    if len(segments) > segments_written:
        segments = segments[:segments_written]
    elif len(segments) < segments_written:
        raise RuntimeError(
            f"media: extract.json for Document {doc_id} has {len(segments)} segment(s) but its "
            f"checkpoint recorded {segments_written} as of the last confirmed {unit_noun} -- the "
            "sidecar is behind its own checkpoint, which should never happen under this "
            f"module's write order; refusing to guess which {unit_noun} to resume from -- retry "
            f"this document from the library; it will {verb_imperative} from the beginning."
        )

    return segments, next_index, existing


def _driver_paths(doc: Document) -> tuple[Path, Path, Path]:
    """`(stored_path, sidecar_path, work_dir)` for a media driver (C-31).
    Both `transcribe_to_sidecar` and `extract_to_sidecar` open with these
    same lines -- `stored_path` from the Document row, `sidecar_path` and
    `work_dir` from the managed store (`store.sidecar_path`/`store.
    work_dir`, ADR 0009/C-18).

    Computing `work_dir` here, even though a driver may short-circuit
    (`_reuse_finished_sidecar`) before ever using it, costs nothing: this
    only builds the `Path` object, it never creates the directory on disk
    -- each driver's own `create_owner_only_dir(work_dir)` call (H13
    review round 1, finding 10: this document's own scratch directory,
    owned exclusively, so the default aggressive retightening applies)
    stays exactly where it was, AFTER the short-circuit and model
    resolution, so a short-circuited or a role-resolution-failed run still
    never touches the filesystem for `work/`.
    """
    stored_path = Path(doc.source_path)
    sidecar_path = store.sidecar_path(doc.id)
    work_dir = store.work_dir(doc.id)
    return stored_path, sidecar_path, work_dir


def _finish_driver(
    doc: Document,
    sidecar: dict,
    *,
    method: str,
    resolved: ResolvedModel,
    produced_at: str,
    sidecar_path: Path,
    work_dir: Path,
) -> dict:
    """Write the sidecar atomically, stamp the extraction, drop the work
    directory, and hand the sidecar back (C-31) -- the fixed tail both
    drivers run once their OWN payload is finished.

    The PAYLOAD is built by the caller, not here -- `_sidecar_payload`
    (transcription) and `_extraction_sidecar_payload` (extraction) take
    different arguments, the latter carrying a `rasterized_pages` count
    the former has no notion of, and `method`'s value (`"transcription"`
    vs `"extraction"`) has to already be baked into `sidecar` before this
    function ever sees it. What IS shared, verbatim, in both drivers: once
    that finished dict exists, write it atomically, stamp `doc.extraction`
    with the same `method`/`resolved`/`produced_at`, best-effort purge
    `work_dir` (a leftover directory is not itself a failure worth raising
    over -- see each driver's own docstring), and return the sidecar.
    """
    _write_sidecar_atomic(sidecar_path, sidecar)

    _stamp_extraction(doc, method=method, engine=resolved.engine, model_id=resolved.model_id, produced_at=produced_at)

    shutil.rmtree(work_dir, ignore_errors=True)

    return sidecar


def transcribe_to_sidecar(doc: Document, ctx: JobContext) -> dict:
    """The transcription driver (module docstring / plan architecture):
    extract_audio -> slice_audio -> per-slice transcribe + offset +
    checkpoint -> grouped segments -> `extract.json` sidecar. Returns the
    finished sidecar dict (the same shape `_sidecar_payload` builds).

    Short-circuits FIRST, before resolving any model, if `extract.json`
    already holds a FINISHED transcription matching the current source file
    (`_reuse_finished_sidecar`, T7 review M3a) -- returns it as-is, touching
    no transcriber and no work directory at all. Only once that check finds
    nothing to reuse does this function resolve `RAG_TRANSCRIBE_ROLE` and
    health-check it (`_resolve_role_or_fail`) -- a `RuntimeError` from THAT
    point on means no transcription work has started yet, nothing to clean
    up.

    Work directory: `<DOCUMENTS_DIR>/<doc.id>/work/` (`store.document_dir`),
    holding the extracted WAV and its slices for the DURATION of this run --
    removed only on a clean finish (see the bottom of this function; a
    PERMANENT job failure's own removal is `tools.rag.ingest.
    run_ingest_or_fail`'s responsibility, not this function's -- see that
    function's own docstring for when). This is deliberately NOT the
    CHECKPOINT'S storage: the checkpoint (`ctx.checkpoint`) stays tiny
    (`{"next_slice_index", "segments_written"}`) and the SIDECAR
    (`extract.json`, in the document's own directory, one level up from
    `work/`) is the accumulator -- appended to and written atomically after
    every slice (`_write_sidecar_atomic`). A resumed attempt
    (`ctx.checkpoint_state is not None`) that finds the prior attempt's WAV
    + slice files still on disk skips extraction/slicing entirely (both are
    pure, deterministic functions of the same source file -- redoing them
    would only cost time, but skipping them is free and the whole point of
    checkpointing a multi-minute ffmpeg pass); it also reconciles whatever
    the sidecar and the checkpoint each say against each other
    (`_reconcile_sidecar_resume`, M1/M2) rather than trusting either alone. If
    the prior WAV/slices are missing for any reason (a resumed attempt on a
    fresh host, a cleaned-up work dir), extraction runs again from scratch
    and the loop starts at slice 0 -- degrading to a slower, but still
    correct, full re-run rather than raising.

    Duration cap: probed via `transcode.probe_duration` on the extracted
    WAV, stored on `doc.duration_seconds` immediately (useful even if this
    run later fails), then checked against `RagSettings.max_media_seconds`
    -- a DEFENSIVE re-check (`duration_cap_message`; module docstring), not
    the primary enforcement point (`tools.rag.ingest._check_media_duration`
    already rejects an over-cap file at STAGE time, before a job is ever
    queued) -- this catches the window between staging and this job
    actually running, e.g. an operator lowering the cap in between.

    Per slice: `transcriber.transcribe(slice)` (a `GenerationRejected` or a
    raw transport error from THIS call propagates unchanged -- the job fails
    honestly with the engine's own words, exactly like every other job
    handler in this platform lets an engine's own refusal/failure surface
    rather than rewriting it), `.offset(i * window_seconds)` to re-express
    that slice's segment timestamps in the SOURCE's own timeline (see
    `TranscriptResult.offset`'s own docstring), append to the running
    `segments` list, report progress
    (`ctx.report_progress(done_seconds, duration, unit="seconds",
    label="transcribed")`), write the sidecar (accumulated so far, still
    `produced_at=None`), then checkpoint -- in that exact order, which is
    what `_reconcile_sidecar_resume` above relies on.

    On completion: writes the FINAL sidecar (`produced_at` set) and
    snapshots `doc.extraction` (`_stamp_extraction`, `method="transcription"`,
    `resolved`'s engine/model_id, `produced_at`, `duration=None`) --
    `duration_seconds` was ALREADY saved earlier (before the cap check), so
    `_stamp_extraction`'s own `duration` parameter is deliberately left at
    its default here rather than saving it a second time. Then removes
    `work/` (best-effort -- `ignore_errors=True`, since a
    leftover work directory is not itself a failure worth raising over once
    the real result -- the sidecar and the Document row -- is already
    durably written), and returns the sidecar dict.
    """
    stored_path, sidecar_path, work_dir = _driver_paths(doc)

    finished = _reuse_finished_sidecar(doc, sidecar_path, stored_path, method="transcription")
    if finished is not None:
        return finished

    resolved = _resolve_role_or_fail(RAG_TRANSCRIBE_ROLE)
    transcriber = get_transcriber_for(resolved)

    # H13 review round 1, finding 10: `work_dir` is this document's own
    # scratch directory (`tools.rag.store.work_dir`) -- owned exclusively,
    # same as the document's own store directory.
    create_owner_only_dir(work_dir)
    wav_path = work_dir / "audio.wav"
    slices_dir = work_dir / "slices"

    # The one local var offsets AND slicing both key off of -- computed
    # here, before either, and passed EXPLICITLY to `slice_audio` below
    # (T7 review m5) so the two can never silently diverge (e.g. a test
    # mocking `DEFAULT_SLICE_SECONDS` to one value while `slice_audio`
    # itself defaulted to another).
    window_seconds = transcode.DEFAULT_SLICE_SECONDS

    resuming = ctx.checkpoint_state is not None
    reuse_prior_extraction = (
        resuming
        and wav_path.exists()
        and slices_dir.is_dir()
        and any(slices_dir.glob("slice-*.wav"))
    )
    if reuse_prior_extraction:
        slices = sorted(slices_dir.glob("slice-*.wav"))
    else:
        transcode.extract_audio(stored_path, wav_path)
        slices = transcode.slice_audio(wav_path, slices_dir, window_seconds=window_seconds)

    duration = transcode.probe_duration(wav_path)
    doc.duration_seconds = duration
    doc.save(update_fields=["duration_seconds", "updated_at"])

    cap = RagSettings.get_solo().max_media_seconds
    if duration > cap:
        raise ValueError(duration_cap_message(stored_path.name, duration, cap))

    if reuse_prior_extraction:
        segments, next_slice_index, existing_payload = _reconcile_sidecar_resume(
            doc.id, sidecar_path, ctx.checkpoint_state or {}, index_key="next_slice_index", unit_noun="slice"
        )
        language = existing_payload.get("language") if existing_payload is not None else None
    else:
        segments, next_slice_index, language = [], 0, None

    for i, slice_path in enumerate(slices):
        if i < next_slice_index:
            continue

        result = transcriber.transcribe(slice_path).offset(i * window_seconds)
        for segment in result.segments:
            segments.append({"start": segment.start, "end": segment.end, "text": segment.text})
        if result.language:
            language = result.language

        done_seconds = min((i + 1) * window_seconds, duration)
        ctx.report_progress(done_seconds, duration, unit="seconds", label="transcribed")

        _write_sidecar_atomic(
            sidecar_path,
            _sidecar_payload(stored_path, doc.file_hash, duration, language, resolved, segments, produced_at=None),
        )
        ctx.checkpoint({"next_slice_index": i + 1, "segments_written": len(segments)})

    produced_at = timezone.now().isoformat()
    sidecar = _sidecar_payload(
        stored_path, doc.file_hash, duration, language, resolved, segments, produced_at=produced_at
    )
    return _finish_driver(
        doc, sidecar, method="transcription", resolved=resolved, produced_at=produced_at,
        sidecar_path=sidecar_path, work_dir=work_dir,
    )


def extract_to_sidecar(doc: Document, ctx: JobContext) -> dict:
    """The vision-extraction driver (T8; `transcribe_to_sidecar` above is
    this function's own structural template -- same short-circuit, same
    work-dir/removal contract, same fixed sidecar-then-checkpoint write
    order): a scanned PDF's pages, or a single image, become one
    `extract.json` sidecar. Returns the finished sidecar dict.

    Short-circuits FIRST, before resolving any model (`_reuse_finished_
    sidecar`, shared with transcription -- see that helper's own
    docstring) if `extract.json` already holds a FINISHED extraction
    matching the current source file -- touches no model, no work
    directory. Only past that check does this function resolve
    `RAG_EXTRACT_ROLE` and health-check it (`_resolve_role_or_fail`); a
    `RuntimeError` from that point on means no extraction work has
    started, nothing to clean up.

    Work directory: `<DOCUMENTS_DIR>/<doc.id>/work/` (`store.document_dir`),
    holding ONE rendered page/image PNG at a time -- unlike `transcribe_to_
    sidecar`'s WAV+slices (an expensive ffmpeg pass worth preserving across
    a resume), rasterizing a single PDF page (`transcode.rasterize_pdf_page`)
    is cheap enough that a resumed attempt simply re-renders whichever page
    it resumes from, rather than checking for and reusing a prior render.
    Removed on a clean finish (best-effort); a crash mid-run leaves it for
    a checkpoint-carrying resume, the exact same `tools.rag.ingest.
    run_ingest_or_fail` unconditional-purge-on-caught-failure contract
    `transcribe_to_sidecar` documents.

    Page cap (W1): `RagSettings.max_document_pages` (T8 review minor 4) is
    checked against the scanned/mixed PDF's TEXTLESS page count -- the
    pages this run actually intends to rasterize, `tools.rag.readers.
    pdf_textless_pages(stored_path, limit=cap + 1)`, reusing the exact
    SAME BOUND (`limit=cap + 1`) `tools.rag.ingest._check_document_pages`
    already used at JOB START (B-5/H28, round-3 hardening -- moved off
    STAGE time; that function's own docstring), re-deriving the RESULT
    independently rather than trusting that earlier scan's own list (ADR
    0014 §9's amendment: "Three [scans] when the document is actually
    routed to extraction") -- before a single page is rasterized
    (`page_cap_message`). This is a SECOND, DEFENSIVE re-check, not the
    primary enforcement point (`_check_document_pages`, at job start,
    already rejects an over-cap document before this driver ever runs) --
    the exact `max_media_seconds`/`transcribe_to_sidecar` precedent, one
    field over. An image never reaches this check at all -- it is always
    exactly 1 page, trivially under any sane cap.

    Two distinct medium shapes, branched on `stored_path`'s own extension
    (`.pdf` vs. an image extension) -- this function is the one place that
    decides which, so neither caller (`tools.rag.ingest.run_ingest_for`)
    nor `tools.rag.jobs.plan_ingest` has to duplicate the check:

    - Scanned/mixed PDF (W1): ONLY the TEXTLESS pages
      (`readers.pdf_textless_pages`'s own list -- the ordinary text pages a
      mixed PDF also has are never rasterized at all; `tools.rag.ingest.
      _source_documents` merges them back in at read time from the
      document's own text layer), processed in LIST order from `ctx.
      checkpoint_state`'s own resume index (`_reconcile_sidecar_resume(...,
      index_key="next_page_index", unit_noun="page")`, the exact function
      `transcribe_to_sidecar` uses for slices, one parameter pair over --
      `next_page_index` here indexes into the TEXTLESS LIST, not the
      document's raw page numbers). Per textless page: `transcode.
      rasterize_pdf_page` renders it (by its real page number,
      `textless[i]`) to a temp PNG under `work/`, `tools.rag.extract.
      extract_image_text` (given the SAME resolved `llm` -- built ONCE
      before this loop starts, via `models.contracts.gateway.get_llm_for
      (resolved)`, never re-resolved per page) transcribes it. A blank/
      whitespace-only result is SKIPPED, not stored as an empty segment
      (logged at INFO -- the `tools.rag.readers._read_pdf` blank-page
      precedent). Progress: `ctx.report_progress(done=<textless-list
      position>, total=<textless page count>, unit="pages",
      label="extracted")` -- the progress bar's total is the TEXTLESS
      count, not the document's page count, so a mixed PDF's progress bar
      actually reaches 100% at the amount of vision work this run does.
      Sidecar write + checkpoint EVERY textless page, in the fixed order
      `_reconcile_sidecar_resume` relies on -- regardless of whether that
      page's extraction produced a segment, exactly like `transcribe_to_
      sidecar`'s own per-slice write records "this unit is done"
      independently of how many segments it added. Every sidecar write
      carries `rasterized_pages=textless` -- the FULL planned list, not a
      running prefix (see `_extraction_sidecar_payload`'s own docstring) --
      so it is already complete after the FIRST page's write. An EMPTY
      textless list (the file gained a text layer between enqueue and this
      run) skips the loop entirely and falls through to the completion
      write below with zero segments -- a finished sidecar, never a raise;
      `_source_documents` then merges 0 extracted pages with the whole text
      layer, which is exactly right.
    - Single image: one `transcode.normalize_image` call, one
      `extract_image_text` call, one segment `{"page": 1, "text": ...}` if
      it produced any text at all -- no loop, no per-unit checkpoint (there
      is exactly one unit of work; a crash before it finishes has nothing
      partial to resume FROM, so a requeue just starts over, cheaply).

    On completion: writes the FINAL sidecar (`produced_at` set,
    `_extraction_sidecar_payload`), snapshots `doc.extraction`
    (`_stamp_extraction`, `method="extraction"` -- see
    `_extraction_sidecar_payload`'s own docstring for why this differs from
    the sidecar's own `method="vision"`). Removes `work/` (best-effort).
    Returns the sidecar dict.
    """
    stored_path, sidecar_path, work_dir = _driver_paths(doc)

    # Purely extension-shaped: multi-page rasterize-loop vs. single-shot
    # normalize -- this function does not itself re-decide "is this PDF
    # actually scanned" (that content-based call, `tools.rag.ingest.
    # _needs_vision_extraction`, is what routed the caller here in the
    # first place; a caller that ever mis-routes a text-layer PDF here
    # would still get a correct-but-wasteful vision pass over it, not a
    # crash -- this branch is agnostic to WHY a .pdf ended up here).
    #
    # COMPUTED BEFORE THE REUSE CHECK (review fix round 2, steward
    # finding): `_reuse_finished_sidecar` needs to know, for THIS
    # document, whether a matching-but-`"described"`-less sidecar is
    # still good enough to reuse -- true for a PDF (no description step
    # ever applies to one), false for an image (see that call's own
    # `require_described` argument below).
    is_pdf = stored_path.suffix.lower() == ".pdf"

    finished = _reuse_finished_sidecar(
        doc, sidecar_path, stored_path, method="extraction", require_described=not is_pdf,
    )
    if finished is not None:
        return finished

    resolved = _resolve_role_or_fail(RAG_EXTRACT_ROLE)
    llm = get_llm_for(resolved)

    # H13 review round 1, finding 10: same reasoning as the transcription
    # driver above -- this document's own scratch directory.
    create_owner_only_dir(work_dir)

    if is_pdf:
        # W1: the cap counts TEXTLESS pages only (the vision-model work
        # this run actually needs), not the document's total page count --
        # `limit=cap + 1` is what makes `len(textless) > cap` decidable
        # without scanning the tail of a huge document (module docstring,
        # `page_cap_message`'s own docstring).
        cap = RagSettings.get_solo().max_document_pages
        # H28 review round 1, finding 4: `pdf_textless_pages` always
        # returns a `(pages, truncated)` pair now -- unpacked here, never
        # `len()`-ed as if the tuple itself were the list (that would
        # silently read `len()` as 2, always, regardless of how many
        # pages are actually textless). No `max_examined` passed -- this
        # re-check is unbounded, same as before; `truncated` is unused.
        textless, _truncated = readers.pdf_textless_pages(stored_path, limit=cap + 1)

        # T8 review minor 4: `RagSettings.max_document_pages` -- a SECOND,
        # DEFENSIVE re-check (`page_cap_message`; module docstring), not
        # the primary enforcement point (`tools.rag.ingest.
        # _check_document_pages` already rejects an over-cap scanned PDF
        # at JOB START, before this driver ever runs -- B-5/H28, round-3
        # hardening, moved that off STAGE time) -- this catches the
        # narrower window between THAT check and this driver actually
        # running, e.g. an operator lowering the cap in between (the exact
        # `transcribe_to_sidecar`/`max_media_seconds` precedent, one field
        # over). Checked BEFORE any resume-reconciliation work, and before
        # a single page is rasterized -- an over-cap document is rejected
        # outright, never partially processed first.
        if len(textless) > cap:
            raise ValueError(page_cap_message(stored_path.name, len(textless), cap))

        resuming = ctx.checkpoint_state is not None
        if resuming:
            segments, next_page_index, _existing_payload = _reconcile_sidecar_resume(
                doc.id, sidecar_path, ctx.checkpoint_state or {}, index_key="next_page_index", unit_noun="page"
            )
        else:
            segments, next_page_index = [], 0

        temp_png = work_dir / "page.png"
        for i, page_number in enumerate(textless):
            if i < next_page_index:
                continue

            # B-4 (round-3 hardening H29): a page whose declared point size
            # would render past `transcode.MAX_RENDER_PIXELS` raises
            # `transcode.RenderAreaExceededError` here -- not caught
            # specially, the SAME "let it propagate" contract every other
            # refusal in this loop already has (the page cap above,
            # `extract.extract_image_text` below): `run_ingest_or_fail`
            # (this driver's only caller) catches it as any other
            # exception, writes `status=FAILED`/`status_detail=str(exc)`,
            # and its message names the page and the ceiling.
            temp_png.write_bytes(transcode.rasterize_pdf_page(stored_path, page_number))
            lock_down_file(temp_png)  # H13 review round 1, finding 10
            text = extract.extract_image_text(temp_png, llm=llm)
            if text:
                segments.append({"page": page_number, "text": text})
            else:
                logger.info(
                    "media: page %d of Document %s produced no extractable text -- skipping",
                    page_number, doc.id,
                )

            ctx.report_progress(i + 1, len(textless), unit="pages", label="extracted")

            _write_sidecar_atomic(
                sidecar_path,
                _extraction_sidecar_payload(
                    stored_path, doc.file_hash, resolved, segments, produced_at=None, rasterized_pages=textless
                ),
            )
            ctx.checkpoint({"next_page_index": i + 1, "segments_written": len(segments)})
    else:
        temp_png = work_dir / "image.png"
        temp_png.write_bytes(transcode.normalize_image(stored_path))
        lock_down_file(temp_png)  # H13 review round 1, finding 10
        # PREVIEW UAT (2026-09-17): DESCRIBE, THEN TRANSCRIBE -- two
        # calls, on the SAME already-built `llm`, for an image only.
        # `EXTRACTION_PROMPT` ends "if the image contains no text,
        # output nothing", which is right for a page of a scanned
        # contract and exactly wrong for a photo: a textless image used
        # to produce an empty sidecar, no caption, and an attachments
        # line that read "description not ready yet" forever.
        #
        # DESCRIPTION FIRST IN THE LIST, and that order is load-bearing:
        # `tools.rag.access._caption_from_sidecar` reads segments in
        # order under a 600-character cap, so a long transcription must
        # never push the one thing the chat model most needs out of the
        # caption.
        #
        # ONE EXTRA CALL PER IMAGE, NEVER PER PDF PAGE (an image is one
        # page by definition), and the transcription call below is
        # byte-identical to what it has always been -- which is what
        # makes "no scanned PDF's OCR changed" provable rather than
        # asserted.
        description = extract.describe_image(temp_png, llm=llm)
        text = extract.extract_image_text(temp_png, llm=llm)
        segments = []
        if description:
            # `kind`, an EXTRA key on the existing `{"page", "text"}`
            # shape -- never a new segment shape and never a new
            # top-level key. Every reader branches on `"page" in
            # segment`/`segments[0]` and simply does not look at this
            # one, so `documents_from_extract` embeds the description
            # (which is what lets a search find a photo by what is in
            # it), `display_segments` renders it, and an OLD sidecar
            # without the key behaves exactly as it always did.
            segments.append({"page": 1, "kind": "description", "text": description})
        if text:
            segments.append({"page": 1, "text": text})
        if not description and not text:
            logger.info(
                "media: Document %s produced neither a description nor extractable text",
                doc.id,
            )
        ctx.report_progress(1, 1, unit="pages", label="extracted")

    produced_at = timezone.now().isoformat()
    sidecar = _extraction_sidecar_payload(
        stored_path,
        doc.file_hash,
        resolved,
        segments,
        produced_at=produced_at,
        rasterized_pages=textless if is_pdf else None,
        described=None if is_pdf else True,
    )
    return _finish_driver(
        doc, sidecar, method="extraction", resolved=resolved, produced_at=produced_at,
        sidecar_path=sidecar_path, work_dir=work_dir,
    )


def group_segments(segments: list[dict], max_chars: int = SEGMENT_GROUP_MAX_CHARS) -> list[dict]:
    """Greedily group `segments` (each `{"start", "end", "text"}`, the
    sidecar's own shape) into windows of combined `{"start", "end", "text"}`
    -- `start` from the first segment in a group, `end` from the last,
    `text` the group's segment texts joined with a single space.

    Greedy, left-to-right, never reordering: walk `segments` in order,
    appending each one's text to the current group; when adding the NEXT
    segment would push the group's combined character count over
    `max_chars`, the current group is closed (appended to the result) and a
    new one starts with that segment. A single segment whose own text
    already exceeds `max_chars` still becomes its own one-segment group
    (this function only ever decides BETWEEN segments, never splits one
    segment's text) -- the boundary is thereby some multiple of whole
    segments, never a mid-sentence cut. An empty `segments` list returns
    `[]`.

    Timestamp-keyed segments (whisper) ONLY -- `documents_from_extract`
    (below) is the one caller, and dispatches to this function purely for
    `{"start", "end", "text"}` segments. T8's page-keyed vision-extraction
    segments (`{"page", "text"}`) deliberately do NOT run through this
    function at all (verified/decided, not just anticipated by this
    docstring the way an earlier draft of it once put it): `extract_to_
    sidecar` already emits AT MOST ONE segment per page, so there is
    nothing to greedily combine the way many-per-slice whisper segments
    need combining, and grouping several pages together would blur a
    citation from page-PRECISE (`{"page": n}`, matching `tools.rag.
    readers._read_pdf`'s own per-page `Document` convention exactly) down
    to a page-RANGE -- a WORSE citation, not a more efficient chunk. See
    `documents_from_extract`'s own docstring for exactly how page segments
    are handled instead (one `Document` per segment, no grouping step).
    """
    groups: list[dict] = []
    bucket: list[str] = []
    bucket_start = None
    bucket_end = None
    bucket_chars = 0

    def flush() -> None:
        if bucket:
            groups.append({"start": bucket_start, "end": bucket_end, "text": " ".join(bucket)})

    for segment in segments:
        text = segment.get("text", "")
        added_chars = len(text) + (1 if bucket else 0)  # +1 for the joining space
        if bucket and bucket_chars + added_chars > max_chars:
            flush()
            bucket = []
            bucket_chars = 0
            bucket_start = None

        if not bucket:
            bucket_start = segment.get("start")
        bucket.append(text)
        bucket_end = segment.get("end")
        bucket_chars += len(text) + (1 if len(bucket) > 1 else 0)

    flush()
    return groups


def documents_from_extract(sidecar: dict) -> list[LlamaDocument]:
    """Build LlamaIndex `Document`s from a finished extraction `sidecar`
    dict (`transcribe_to_sidecar`'s/`extract_to_sidecar`'s own return
    shape, or the same shape read back off `extract.json` by `tools.rag.
    ingest._source_documents`) -- MODEL-FREE: this function calls no
    inference role, only pure grouping/formatting, so it is safe to call
    from `reencode_all`'s bulk loop (`tools.rag.services.reencode_all`)
    with no transcriber/extractor involved at all.

    Branches on the FIRST segment's own keys (an empty/missing
    `sidecar["segments"]` short-circuits to `[]` before this check even
    runs, matching `group_segments`'s own empty-input behavior --
    `tools.rag.ingest._ingest_prose` already logs and no-ops on an empty
    `llama_docs` list, so this needs no special case of its own):

    - `"start"` in the first segment (whisper, T7): one `Document` per
      `group_segments(sidecar["segments"])` group, with `text` the group's
      joined text and `metadata={"start_seconds": ..., "end_seconds": ...}`
      -- the citation span T7's plan names ("a citation span ~1 min",
      `SEGMENT_GROUP_MAX_CHARS`'s own docstring).
    - `"page"` in the first segment (vision extraction, T8): one `Document`
      PER SEGMENT, no grouping (`group_segments`'s own docstring explains
      why grouping is deliberately skipped for this shape), with
      `metadata={"page": segment["page"]}` -- the exact metadata key
      `tools.rag.readers._read_pdf` already uses for an ordinary
      text-layer PDF's own per-page `Document`s, so a citation behaves
      IDENTICALLY regardless of whether a given PDF went through the
      text-extraction path or the vision-extraction path.

    Every returned `Document` has `apply_chunk_metadata_exclusions` already
    applied (the pollution fix -- see that function's docstring):
    `start_seconds`/`end_seconds`/`page` are retrieval metadata, never text
    this platform embeds or feeds an LLM as if it were the transcript's/
    page's own words.
    """
    segments = sidecar.get("segments") or []
    if not segments:
        return []

    docs: list[LlamaDocument] = []
    if "page" in segments[0]:
        for segment in segments:
            llama_doc = LlamaDocument(text=segment["text"], metadata={"page": segment["page"]})
            apply_chunk_metadata_exclusions(llama_doc)
            docs.append(llama_doc)
        return docs

    for group in group_segments(segments):
        llama_doc = LlamaDocument(
            text=group["text"],
            metadata={"start_seconds": group["start"], "end_seconds": group["end"]},
        )
        apply_chunk_metadata_exclusions(llama_doc)
        docs.append(llama_doc)
    return docs
