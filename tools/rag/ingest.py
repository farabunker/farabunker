"""
Ingestion pipeline (ADR 0005, ADR 0009; media-into-RAG plan T2/T7/T8,
ADR 0014).

  - detect file type (prose: pdf/txt/md/docx vs. tabular: csv/xlsx) via
    `tools.rag.readers.medium_for` -- video/audio (T7) and image (T8)
    join the accepted set the moment the "media" feature is enabled
    (`settings.FARABUNKER_FEATURES`): `supported_exts()` below is a
    FUNCTION, not a frozen module-level constant, so a flag flip
    (including a test's `override_settings`) takes effect on the very
    next call, and
    `_doc_type_for_medium` gates its own video/audio/image branch on the
    same flag. A SCANNED (no text layer) PDF is not a medium of its own --
    `medium_for` still calls every ".pdf" "prose", unconditionally, exactly
    like a text-layer one; `_needs_vision_extraction` (T8) is the separate,
    content-based auto-detect that decides whether a given "prose" ".pdf"
    should route through vision extraction instead of the ordinary text
    parse -- see that function's own docstring.

  - STAGE (`stage_document`): copy/move the source file into the managed
    store (tools.rag.store) and assign a Category (explicit, or derived
    from the drop-folder layout by the command/watch layer -- ADR 0009);
    dedup on `(original_path, file_hash)`; the Document row is created (or
    reused, for a changed re-drop of the same original_path) with
    `status=PENDING` -- staging never itself parses/chunks/embeds anything,
    it only makes the file and its row exist and be queryable. REUSING an
    EXISTING row is an authorization decision, not only a bookkeeping one
    (B-2, round-3 hardening): the CLI (`actor=None`) and the watcher
    (`actor=SERVICE_PRINCIPAL` -- NOT `None`, round-1 review finding 1)
    both keep "same path, new bytes = re-index in place" semantics
    unchanged, but the browser-upload door's `actor` must be the row's
    own uploader or someone `tools.rag.access.may_administer_document`
    already admits, or the re-stage raises `StageRefused` instead of
    silently overwriting another principal's document -- see
    `stage_document`'s own `actor` paragraph.

  - RUN (`run_ingest_for`): re-verifies the staged copy's hash against what
    staging recorded, then PENDING -> PROCESSING -> READY:
      - prose: parse -> chunk into LlamaIndex nodes -> embed (via
        models.contracts.gateway) -> write to the pgvector store
        (tools.rag.index.get_vector_store); every chunk node's metadata
        carries a normalized (lowercased) `category` key: the lowercased
        category name, or the literal "uncategorized". A video/audio medium
        (T7) or one `_needs_vision_extraction` recognizes -- image, or a
        scanned PDF (T8) -- transcribes/extracts to a sidecar FIRST (see
        below), then parses/chunks/embeds that sidecar's text exactly like
        any other prose.
      - tabular: parse rows -> write tools.rag.models.DocumentRow rows +
        record the inferred schema on tools.rag.models.Document.tabular_schema
    `run_ingest_for` itself never writes FAILED on its own exceptions --
    both of its callers (`ingest_path` below and the queue job handler,
    `tools.rag.jobs.run_ingest`) wrap it in `run_ingest_or_fail`, the one
    shared place that catches, writes `status=FAILED` +
    `status_detail=str(exc)`, and re-raises -- see that function's
    docstring for why a PROCESSING row must never be left stranded there.

  - `ingest_path` (the CLI's own entry point, `manage.py ingest`) composes
    STAGE (copy, `move=False`) and RUN (via `run_ingest_or_fail`) inline,
    synchronously -- the same net effect as before T2, just built from the
    two halves above. A RUN-half failure here still propagates out of
    `ingest_path` (the CLI command's own per-file try/except reports it),
    but the Document row is now FAILED, not stranded at PROCESSING, before
    that happens.

  - `enqueue_ingest` (the watcher/upload entry point) instead STAGES
    (`move=True` -- an inbox drop's only copy becomes the managed-store
    copy) and hands the RUN half to the execution queue as a `rag.ingest`
    job (`tools.rag.jobs.run_ingest`, `models.contracts.queue`) -- staged-
    at-enqueue: the Document row (and its managed-store file) exist and are
    queryable the instant a file is dropped/uploaded, before the queue ever
    claims the job. `enqueue_reingest` is the retry-button sibling: same
    queueing tail, no (re-)staging -- the Document and its store copy
    already exist.

  - re-ingesting a changed file (same `original_path`, new `file_hash`)
    deletes its prior rows/chunks/stored files first, then reuses the same
    Document row (idempotent re-index)

  - run out-of-band: called from a management command, a folder watcher, or
    the execution queue's worker -- never from inside a synchronous
    request/response cycle

  - a media Document's `work/` directory (T7's `tools.rag.media.
    transcribe_to_sidecar`, T8's `tools.rag.media.extract_to_sidecar`)
    survives ONLY a crashed worker mid-job (the one case `models.queue.
    claim._sweep_orphans` can requeue with a surviving `checkpoint_state`
    for the driver to actually resume from) -- `run_ingest_or_fail` purges
    it unconditionally on every OTHER failure it catches, since a manual
    Retry always starts a fresh job with `checkpoint_state=None` and would
    just overwrite it anyway; see that function's own docstring for the
    full reasoning.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from django.conf import settings
from django.db import transaction
from llama_index.core import Document as LlamaDocument
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.postgres import PGVectorStore
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from foundation.files import SymlinkRefused, create_locked_file, sha256_file
from foundation.format import human_bytes
from identity.access import owner_fields
from identity.contracts.principals import SERVICE_PRINCIPAL, payload_fields
from models.contracts import gateway
from models.contracts.jobkinds import NULL_JOB_CONTEXT, JobContext
from models.contracts.queue import QueueQuotaExceeded, QueueUnavailable, enqueue
from models.contracts.roles import RAG_EMBED_ROLE
from tools.rag import access
from tools.rag import index as rag_index
from tools.rag import media, store, transcode
from tools.rag.categories import get_or_create_category
from tools.rag.models import UNCATEGORIZED, Document, DocumentRow, RagSettings
from tools.rag import readers
from tools.rag.readers import (
    AV_EXTS,
    IMAGE_EXTS,
    PROSE_EXTS,
    TABULAR_EXTS,
    medium_for,
    read_prose_documents,
    read_tabular_dataframe,
)
from tools.rag.sidecar import read_sidecar

logger = logging.getLogger(__name__)

# The splitter's own chunk budget, in TOKENS -- `_ingest_prose` below passes
# this explicitly to `SentenceSplitter(chunk_size=CHUNK_TOKENS)` rather than
# leaving it at the library's own default (which happens to also be 1024,
# but an unstated default a future `llama-index-core` upgrade could silently
# change out from under this codebase). W4 (ADR 0014 §14) reuses this exact
# constant for a SECOND purpose besides splitting: the operator-tunable
# `RagSettings.retrieval_top_k` context-window check
# (`tools.rag.views._retrieval_top_k_update`) estimates how many
# tokens `top_k` retrieved chunks will cost a synthesis prompt as
# `top_k * CHUNK_TOKENS` -- an upper bound (a real chunk is USUALLY smaller
# than the splitter's own budget, never larger), not a live per-chunk token
# count, since no tokenizer is wired to that check.
CHUNK_TOKENS = 1024

# A conservative, deliberately-generous token reserve for everything in a
# synthesis prompt BESIDES the retrieved chunks themselves -- the question,
# the synthesis prompt's own scaffolding text, and the model's own answer --
# consumed by the SAME `RagSettings.retrieval_top_k` context-window check
# `CHUNK_TOKENS` feeds (see that constant's own docstring). Not derived from
# a live token count; a round number chosen so the check's rejection only
# fires when a chosen `top_k` genuinely cannot fit alongside it, never as a
# false positive on a borderline-real prompt.
RESPONSE_RESERVE = 2048


def supported_exts() -> frozenset[str]:
    """Extensions `stage_document` can actually reach right now: prose |
    tabular unconditionally, plus `AV_EXTS` (video/audio, T7) and
    `IMAGE_EXTS` (T8) once "media" is in `settings.FARABUNKER_FEATURES` --
    the exact same gate, the same mechanism T7 already established for
    AV_EXTS, extended to images rather than reinvented.

    A FUNCTION, not a frozen module-level constant computed once at import
    -- `settings.FARABUNKER_FEATURES` can differ per test
    (`django.test.override_settings`) and the watcher/upload-form/CLI
    callers below all need to see a flag flip take effect on their very
    next call, not only after a process restart. Called fresh at every
    call site (`_IngestEventHandler._track`, `manage.py ingest`,
    `tools.rag.views.document_upload`, the upload form's own caller) --
    never cached.
    """
    exts = PROSE_EXTS | TABULAR_EXTS
    if "media" in settings.FARABUNKER_FEATURES:
        exts = exts | AV_EXTS | IMAGE_EXTS
    return frozenset(exts)


# Document.media_type's source of truth for every extension `stage_document`
# can reach -- prose|tabular unconditionally, AV_EXTS (T7) and IMAGE_EXTS
# (T8) once "media" is enabled (see `supported_exts()` above) -- an explicit
# map rather than `mimetypes.guess_type` (whose registry, and therefore its
# answer for an extension like .md, varies by OS/Python build) so this stays
# deterministic across every deployment this platform runs on.
_MEDIA_TYPE_BY_EXT: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# The honest, distinct `Document.status_detail` strings a queue-enqueue
# failure gets (module docstring / `_enqueue_ingest_job`) -- split the same
# way `tools.rag.views._QUEUE_UNAVAILABLE_MESSAGE`/
# `_QUEUE_ENQUEUE_FAILED_MESSAGE` are: "the jobs tables aren't there yet"
# (an unmigrated window) reads differently to an operator than "something
# else went wrong enqueuing," and conflating either with a quota refusal
# would name the wrong fix.
_QUEUE_UNAVAILABLE_DETAIL = (
    "The queue isn't ready — run database migrations, then retry this document."
)
_QUEUE_ENQUEUE_FAILED_DETAIL = "Couldn't add this document to the queue — retry it from the document library."
# C-7 review, fix round 1: `QueueQuotaExceeded`'s own third, honest
# reading -- this caller's own queue is simply full, not down and not
# broken. Marks `doc` FAILED through `_fail_document` the same as the
# two above (fix round 2: round 1 left `doc` at PENDING instead, which
# hid this detail and had no Retry form -- withdrawn, see
# `_enqueue_ingest_job`'s own docstring).
_QUEUE_QUOTA_EXCEEDED_DETAIL = (
    "Your queue is full — wait for one of your own jobs to finish, then retry this document."
)

# The honest READY `status_detail` (W1, D4) for a mixed PDF that fell back to
# its text layer alone because no `rag.extract` (vision) model is bound --
# `run_ingest_for`'s own `media.ModelRoleUnavailable` catch, `.pdf` only
# (see that function's docstring). Names the exact fix (bind a model, then
# Re-ingest -- this row is READY, so `documents.html`'s Retry form renders
# the "Re-ingest" label for it, W1 review MAJOR 1) rather than leaving an
# operator to guess why some pages are missing from a document that
# otherwise shows READY.
_TEXT_ONLY_FALLBACK_NOTE = (
    "Some pages had no text layer and were skipped — bind a vision model "
    "(Inference → rag.extract) and Re-ingest to extract them."
)


class MediaDurationExceededError(ValueError):
    """`stage_document`'s specific rejection for an over-`RagSettings.
    max_media_seconds` video/audio file (T7 review m2) -- a `ValueError`
    subclass, so every existing `except ValueError` call site (the CLI,
    `manage.py ingest`) still catches it exactly as before, while a caller
    that wants to treat "over the duration cap" differently from every
    other staging rejection (`tools.rag.views.document_upload` surfacing
    `str(exc)` verbatim like its size-cap branch does; `_IngestEventHandler.
    poll_once` leaving the file in `pending` instead of popping it) can
    catch this name specifically, ahead of a bare `except ValueError`/
    `except Exception`.
    """


def _check_media_duration(path: Path, medium: str) -> str | None:
    """Duration-cap enforcement for `RagSettings.max_media_seconds`
    (media-into-RAG plan T4/T5/T7; see that field's own docstring).

    Called FIRST in `stage_document` -- before `_doc_type_for_medium(medium)`
    -- so it runs on every staging attempt, but only ever DOES anything for
    `medium in ("video", "audio")` while "media" is enabled
    (`settings.FARABUNKER_FEATURES`): both a non-AV medium and an AV medium
    with the flag off return `None` immediately, exactly matching this
    function's pre-T7 always-`None` behavior for those cases (image stays
    unconditionally `None` too -- T8, not T7's business).

    Probing a video/audio file's duration here (at STAGE time, on a
    watcher/web request thread) is intentional, not an oversight: `ffprobe`
    reads container METADATA, not the media payload, so even a multi-GB
    source file probes in well under a second -- this is the primary
    enforcement point, rejecting an over-cap file before a `Document` row
    or a queue job ever exists for it (`transcribe_to_sidecar` in
    `tools.rag.media` re-checks the SAME cap defensively, at RUN time, for
    the narrower window where the cap changed between staging and a queued
    job actually running -- see that function's own docstring).

    Degrades honestly, never guesses: if `ffprobe` isn't on PATH, this logs
    a warning and returns `None` (the cap silently isn't enforced for this
    upload, same as before `ffprobe` existed on this host) rather than
    failing the whole staging attempt over a missing dev-dependency. Any
    OTHER `transcode.probe_duration` failure (a corrupt file, a real
    `ffprobe` error) is NOT caught here -- it propagates as `transcode`'s
    own `RuntimeError`, since a file `ffprobe` cannot even read is a
    genuine problem `stage_document`'s caller needs to see, not something
    to silently wave through.

    Returns the rejection message (`tools.rag.media.duration_cap_message`)
    for `stage_document` to raise as a `MediaDurationExceededError` (a
    `ValueError` subclass, T7 review m2) when the probed duration exceeds
    `RagSettings.get_solo().max_media_seconds`; `None` otherwise.
    """
    if medium not in ("video", "audio"):
        return None
    if "media" not in settings.FARABUNKER_FEATURES:
        return None
    if not transcode.ffprobe_available():
        logger.warning(
            "ingest: ffprobe is not installed; skipping the media duration cap check for %s "
            "-- RagSettings.max_media_seconds will not be enforced for this file",
            path,
        )
        return None

    # Worst case (T7 review m2): `transcode.PROBE_TIMEOUT_SECONDS` (30s) --
    # this call runs synchronously on whichever thread called
    # `stage_document` (a watcher poll tick, or an upload request), so a
    # slow/network-mounted source file can block it for up to that long,
    # not just the "well under a second" common case the paragraph above
    # describes.
    duration = transcode.probe_duration(path)
    cap = RagSettings.get_solo().max_media_seconds
    if duration <= cap:
        return None
    return media.duration_cap_message(path.name, duration, cap)


class DocumentPageCapExceededError(ValueError):
    """`stage_document`'s specific rejection for a vision-extraction-bound
    document (a scanned PDF -- an image is always exactly 1 page, always
    within any sane cap) over `RagSettings.max_document_pages` (T8 review
    minor 4) -- the exact `MediaDurationExceededError` pattern, one field
    over: a `ValueError` subclass so every existing `except ValueError`
    call site (the CLI, `manage.py ingest`) still catches it exactly as
    before, while a caller that wants to treat "over the page cap"
    differently from every other staging rejection can catch this name
    specifically, ahead of a bare `except ValueError`/`except Exception`.
    """


def _check_document_pages(path: Path, medium: str, *, probe_out: dict | None = None,
                           max_examined: int | None = None) -> str | None:
    """Document-page-count cap enforcement for `RagSettings.
    max_document_pages` (T8 review minor 4; see that field's own
    docstring) -- the vision-extraction sibling of `_check_media_duration`
    above: same "reject before an expensive pipeline runs" rationale, same
    degrade-honestly-never-guess contract.

    CALLED FROM `run_ingest_for`, NOT `stage_document` (B-5, round-3
    hardening H28 -- moved here from stage time; that function's own
    docstring, "DOES NOT DECIDE THE PAGE CAP", has the full story). A
    `Document` row and its managed-store copy already exist by the time
    this runs -- a rejection here leaves the row behind, marked FAILED
    with this function's own message as `status_detail`
    (`run_ingest_or_fail`), the SAME place `_check_media_duration`'s own
    defensive run-time re-check (`tools.rag.media.transcribe_to_sidecar`)
    already reports to, rather than the "nothing to clean up, nothing ever
    created" shape a STAGE-time rejection had. The trade is deliberate:
    this scan is UNBOUNDED (an all-text PDF costs a full pass, `readers.
    pdf_textless_pages`'s own docstring), and `run_ingest_for` runs off
    the request thread (a queue worker, or the CLI's own synchronous
    process) -- never inline in an HTTP request -- so it may take as long
    as an honest answer needs, at the cost of an operator seeing "queued"
    rather than an immediate rejection, and the real refusal landing
    slightly later, on the document's own FAILED row.

    Unlike duration, every vision-extraction document has a knowable page
    count cheaply, up front, with no external tool involved: an image is
    ALWAYS exactly 1 page (`tools.rag.media.extract_to_sidecar`'s
    single-shot branch, one segment), and a scanned or mixed PDF's page
    count (for THIS cap's purposes) is the number of TEXTLESS pages --
    `tools.rag.readers.pdf_textless_pages` -- not the document's total
    page count: the cap exists to bound per-page VISION-MODEL work, and an
    ordinary text page never reaches the model at all (W1). `medium ==
    "image"` is checked FIRST and unconditionally (no flag gate, no probing
    needed -- `medium_for` already made that call off the extension alone);
    a `medium == "prose"` `.pdf` is only scanned at all while `"media" in
    settings.FARABUNKER_FEATURES` -- an ordinary text-layer PDF, or any PDF
    with the flag off, is NEVER subject to this cap at all.

    ONE SCAN, TWO USES (W1 review D3): this used to call
    `_needs_vision_extraction` (a probe) and then `pdf_page_count` (a second,
    independent pass) inside the same `try` -- two `pypdf` passes over the
    same file. `readers.pdf_textless_pages(path, limit=cap + 1)` is called
    exactly ONCE here: an empty result means "not vision-bound, not capped"
    (an ordinary text PDF, `_needs_vision_extraction`'s own truth), and a
    non-empty one supplies `page_count` too, via `len()` -- the routing
    decision and the cap count come from the SAME list. `RagSettings.
    get_solo()` is likewise read exactly ONCE per call now (W1 review MINOR
    7): the branch that needs `cap` reads it and holds it for the final
    comparison below, rather than reading it again there unconditionally
    -- the prior version paid a second `get_solo()` (a real
    `get_or_create` query, not a cached lookup) on every staged PDF/image
    purely to re-derive a value already in hand.
    `limit=cap + 1` is what makes `len(textless) > cap` decidable without
    scanning the tail of a huge document on a watcher thread or an upload
    request; when the document passes (`len <= cap`), the list returned is
    complete, so `tools.rag.media.extract_to_sidecar` can trust the same
    bound (see that function's own docstring). `limit=cap + 1` bounds only
    the over-cap case -- see "The scan cost, stated plainly", ADR 0014 §9,
    for the real (unbounded) cost this pays for an ordinary all-text PDF.

    GUARDED (T8 review MAJOR 1's own lesson, applied here proactively
    rather than reproduced as a third probe-crash incident):
    `pdf_textless_pages` shells out to `pypdf`, which can itself raise on a
    corrupt/truncated/encrypted PDF (`pypdf.errors.PdfStreamError` and
    friends) -- any such failure here degrades to a logged warning and
    `None` (the cap silently isn't enforced for THIS file, the exact
    `_check_media_duration`-missing-`ffprobe` degrade above) rather than
    blocking the run on a probe failure that has nothing to do with the
    page-count question being asked; `run_ingest_for`'s own subsequent
    real parse of the SAME file will surface the actual corruption
    honestly, as a proper FAILED row, not as an opaque crash here.

    Returns the rejection message (`tools.rag.media.page_cap_message`)
    for the caller to raise as a `DocumentPageCapExceededError` when the
    page count exceeds `RagSettings.get_solo().max_document_pages`; `None`
    for every other case (not a vision-extraction document at all, within
    the cap, or a guarded probe failure).

    `probe_out` (C-08): an optional dict this function WRITES the scan's
    own result into, under the key "textless", when it actually runs the
    `limit=cap + 1` pass. That pass already answers the question
    `_needs_vision_extraction` asks with `limit=1` -- "is there at least
    one textless page" -- and `readers.pdf_textless_pages`'s own docstring
    records that an all-text PDF costs a FULL scan at any positive limit,
    so handing the answer forward turns two full `extract_text()` passes
    per upload into one.

    AN OUT-PARAMETER RATHER THAN A WIDER RETURN, deliberately: this
    function's return value is compared directly against `None` and
    against a message throughout `TestCheckDocumentPages`, and widening it
    would make a one-line performance fix into a fifty-site test rewrite.
    A caller that passes nothing sees no change at all.

    THE KEY IS ABSENT, NOT `None`, when the branch did not run (not a
    `.pdf`, not prose, or the "media" flag is off), and also absent when
    the scan itself raised (the guarded except below) OR was truncated
    (immediately below) -- nobody successfully looked, or the look was
    inconclusive. Every reader must treat an absent key as "nobody has
    looked", never as "no textless pages" -- the difference between those
    two is the whole finding.

    `max_examined` (H28 review round 1, finding 1), OPTIONAL: forwarded
    straight through to `readers.pdf_textless_pages` as its own
    `max_examined` -- see that function's own docstring for the bound
    itself (`readers.PDF_SCAN_MAX_PAGES_EXAMINED`) and who passes what.
    When the scan is truncated (examined `max_examined` pages without
    concluding), this function FAILS CLOSED: it returns `media.
    page_scan_ceiling_message`'s refusal -- distinct wording from the
    ordinary over-cap message above, naming the ceiling and the page count
    actually examined, since "truncated" means the real answer (over cap,
    or not) is genuinely unknown, not "probably fine". `probe_out` is left
    untouched in this case (the key stays ABSENT, per the paragraph
    above) -- a partial scan's list is not a trustworthy routing answer
    either.
    """
    try:
        if medium == "image":
            page_count = 1
            cap = RagSettings.get_solo().max_document_pages
        elif medium == "prose" and path.suffix.lower() == ".pdf" and "media" in settings.FARABUNKER_FEATURES:
            cap = RagSettings.get_solo().max_document_pages
            textless, truncated = readers.pdf_textless_pages(path, limit=cap + 1, max_examined=max_examined)
            if truncated:
                return media.page_scan_ceiling_message(path.name, max_examined)
            if probe_out is not None:
                probe_out["textless"] = textless
            if not textless:
                return None  # ordinary text PDF -- not vision-bound, not capped
            page_count = len(textless)
        else:
            return None
    except Exception:
        logger.warning(
            "ingest: failed to determine %s's page count while checking the document page "
            "cap -- skipping the check for this file; a real parse failure will surface "
            "honestly once ingestion actually runs",
            path,
            exc_info=True,
        )
        return None

    if page_count <= cap:
        return None
    return media.page_cap_message(path.name, page_count, cap)


# W3 (ADR 0014 §18): promoted to `foundation/files.py::sha256_file` -- pure
# `hashlib` + `pathlib`, shared with `foundation.ops`'s backup manifest hashing.
# A one-line delegation, not a call-site migration: both call sites below
# (`stage_document`, `run_ingest_for`) keep calling `_sha256(...)` unchanged.
_sha256 = sha256_file


def _doc_type_for_medium(medium: str) -> str:
    """The successor to the retired `_doc_type_for`: map a
    `tools.rag.readers.medium_for` result to `Document.DocType`.

    "prose" and "tabular" always map to their own `DocType`. "video"/"audio"
    (T7) and "image" (T8) all map to `DocType.PROSE` too -- a transcript, or
    a vision-extracted page/image, IS prose text, the same doc_type prose
    ingest already knows how to chunk/embed -- but ONLY while "media" is
    enabled (`settings.FARABUNKER_FEATURES`); with the flag off, this raises
    `ValueError`, since nothing downstream of `stage_document` knows how to
    run/queue that medium. `Document.media_type` (set separately, from the
    file's own extension -- see `_media_type_for`) is what records that the
    SOURCE was video/audio/image; `doc_type` only records the SHAPE of what
    it produces. A scanned PDF (no text layer, T8) is NOT a medium of its
    own here -- `medium_for` still calls it "prose" off the `.pdf`
    extension alone, and it maps to `DocType.PROSE` via the ordinary first
    branch above, unconditionally, exactly like a text-layer PDF; the
    "route this one through vision extraction instead" decision is made
    later, at JOB START, by `_needs_vision_extraction` under
    `run_ingest_for` -- never here, and (since H28/B-5) never in
    `_enqueue_ingest_job` either, which decides only the payload's
    `medium` token and does so off the extension alone.
    """
    if medium == "prose":
        return Document.DocType.PROSE
    if medium == "tabular":
        return Document.DocType.TABULAR
    if medium in ("video", "audio", "image") and "media" in settings.FARABUNKER_FEATURES:
        return Document.DocType.PROSE
    raise ValueError(
        f"{medium.capitalize()} files aren't accepted yet -- enable the \"media\" feature to "
        "ingest this file type."
    )


def _media_type_for(ext: str) -> str:
    """`Document.media_type` for `ext` (e.g. ".pdf" -> "application/pdf",
    ".mp4" -> "video/mp4", ".png" -> "image/png", T8) -- see
    `_MEDIA_TYPE_BY_EXT`. "" for any extension not in that map -- unreachable
    for any extension `stage_document` would ever actually stage
    (`_doc_type_for_medium` already rejects every medium it hasn't
    validated first), kept as an honest default rather than a `KeyError`
    for a caller that hands this function an extension directly."""
    return _MEDIA_TYPE_BY_EXT.get(ext, "")


def _needs_vision_extraction(stored_path: Path, medium: str, *, textless=None) -> bool:
    """True when `stored_path` (already known to be `medium`, via
    `tools.rag.readers.medium_for`) should route through
    `tools.rag.media.extract_to_sidecar` rather than the ordinary
    prose/tabular path -- T8's SINGLE detection function.

    `run_ingest_for`'s ONLY caller now (H28 review round 1, finding 3):
    `_enqueue_ingest_job` used to share this function too (it called this
    to decide whether to declare `payload["medium"] = "pdf-scanned"`), but
    B-5/H28 (round-3 hardening) moved that decision off the request/
    watcher thread entirely -- `_enqueue_ingest_job` now declares
    `"pdf-scanned"` PESSIMISTICALLY, for every `.pdf` while "media" is on,
    off the EXTENSION alone (see that function's own docstring), never by
    calling this content-based detector. This function is reached exactly
    once per RUN, at job start, from `run_ingest_for`.

    Two cases:

    - `medium == "image"`: always -- `readers.medium_for` already
      distinguished this from "prose" purely off the extension
      (`IMAGE_EXTS`), no further probing needed.
    - `medium == "prose"` AND the extension is `.pdf` AND `"media" in
      settings.FARABUNKER_FEATURES` AND `tools.rag.readers.
      pdf_textless_pages(stored_path, limit=1)` is non-empty -- the
      MIXED-OR-SCANNED-PDF AUTO-DETECT (W1, ADR 0014 §18): `medium_for`
      cannot distinguish a scanned/mixed/image-only PDF from an ordinary
      text-layer one (all are ".pdf" -> "prose"), so this is the one place
      that content-based distinction is actually made, by calling the
      readers.py scan directly. ANY textless page routes the WHOLE document
      through `extract_to_sidecar` (W1 decision D2) -- no threshold, no
      "route if >N% textless" guess: a stray blank separator page costs one
      vision call and is then absent from the index (`extract_to_sidecar`'s
      own blank-page skip), which is correct, and a page with real text
      never needed vision in the first place (`tools.rag.ingest.
      _source_documents` merges the text pages back in at read time, D3).
      `limit=1` exits at the FIRST textless page -- cheaper than the old
      5-page probe for a scanned/mixed PDF, more expensive for an all-text
      one (now scanned in full where the retired probe returned at page 1;
      see "The scan cost, stated plainly", ADR 0014 §9). Re-derived FRESH at
      every call site rather than persisted on the Document row -- the same
      "never trust a stale snapshot, the file on disk is the truth" shape
      `run_ingest_for`'s own sha re-verification already uses.

    Gated on `"media" in settings.FARABUNKER_FEATURES` for the PDF case
    (never for "image" -- reaching this function with `medium == "image"`
    at all already implies the flag was on, since `medium_for` itself
    doesn't gate on it but `_doc_type_for_medium`/`supported_exts()` do,
    upstream, before an image file is ever staged): vision extraction
    (`RAG_EXTRACT_ROLE`) is only ever registered while "media" is enabled
    (`tools/rag/apps.py`), so a scanned PDF staged/reingested with the
    flag off gets the SAME pre-T8 treatment it always has (`readers.
    _read_pdf` finds no text, logs, and the document ends up with zero
    chunks) rather than trying to resolve a role that was never
    registered. Every .pdf is always accepted at staging regardless of the
    flag (PROSE_EXTS is unconditional, unlike AV_EXTS/IMAGE_EXTS) -- only
    the ROUTING decision this function makes is flag-gated, never
    acceptance itself.

    CROSS-PROCESS FLAG DIVERGENCE WINDOW (T8 review minor 6, named not
    fixed -- operator misconfiguration, not a bug this function can close
    on its own): `_enqueue_ingest_job` (the web/watcher process) and
    `run_ingest_for` (the worker process, `models.queue.worker.Worker`)
    each call this function independently, each reading `settings.
    FARABUNKER_FEATURES` fresh off its OWN process's environment at the
    moment it happens to run -- there is no single, atomic "the flag was
    X at enqueue time" fact shared between them the way `payload["medium"]`
    is for other decisions. Two ways that can bite an operator who flips
    "media" while a scanned PDF is mid-queue, both self-inflicted
    misconfiguration rather than a race this codebase is responsible for
    preventing:

    - OFF (enqueue) -> ON (run): `_enqueue_ingest_job` declared
      `payload["medium"] = "prose"` (flag was off, so this function
      returned `False`) and `plan_ingest` therefore reserved only
      `rag.embed`, no `rag.extract` -- but by the time the job actually
      runs, the flag is on, so `run_ingest_for`'s OWN call to this
      function (off the file, not the payload) now returns `True` and
      routes through `extract_to_sidecar` anyway. The job runs a real
      vision-extraction workload the scheduler never admitted room for --
      `models/queue/scheduler.py`'s own admission math under-accounts this
      job's actual footprint for its entire run.
    - ON (enqueue) -> OFF (run): the inverse -- `rag.extract` WAS reserved
      at enqueue time, but `run_ingest_for` now sees the flag off, so this
      function returns `False` and the scanned PDF falls through to the
      ordinary `readers.read_prose_documents` path, which finds no text
      (it's scanned) and produces a Document with zero chunks -- silently,
      not a FAILED row, since nothing here actually errors.

    Both are read as "the operator changed their mind mid-flight" facts,
    not failure modes to special-case: a queued job's own `payload`
    already snapshots enough (`medium`) for an operator or a future
    dashboard to notice the mismatch after the fact; making the flag
    itself part of that snapshot, or re-validating it at claim time, is
    real future work this docstring flags, not something this task builds.

    `textless` (C-08, corrected H28 review round 1 finding 3): the scan
    result a caller already paid for, or `None` for "nobody has looked".
    `run_ingest_for`'s own call THREADS a real value through now, rather
    than passing `None` -- `_check_document_pages`'s own job-start scan
    (the SAME `readers.pdf_textless_pages` pass this function would
    otherwise run itself) writes its result into a `probe_out` dict that
    `run_ingest_for` reads right back out and hands to this parameter, so
    the two questions (is this document over the page cap; does it need
    vision extraction) share ONE scan per run, not two -- see
    `_check_document_pages`'s own "ONE SCAN, TWO USES" paragraph.
    `None` is still what this function sees for an IMAGE (`_check_
    document_pages`'s image branch never populates `probe_out` -- no scan
    to share, this function's own `medium == "image"` case below answers
    without one) and for a GUARDED corrupt-PDF scan failure -- this
    function's own un-guarded fallback scan below is what runs in exactly
    the latter case, surfacing the corruption for real (T8 review MAJOR 1).
    """
    if textless is not None:
        return bool(textless)
    if medium == "image":
        return True
    if medium != "prose" or stored_path.suffix.lower() != ".pdf":
        return False
    if "media" not in settings.FARABUNKER_FEATURES:
        return False
    # H28 review round 1, finding 4: `pdf_textless_pages` always returns a
    # `(pages, truncated)` pair now -- unpacked, never truthiness-tested as
    # a bare tuple (`bool((tuple))` is truthy regardless of `pages`, the
    # footgun that function's own docstring names).
    #
    # BOUNDED, like every other job-start scan (round-3 final wave). This
    # call was the last unbounded `pdf_textless_pages` walk left, and it
    # runs in exactly one case: `_check_document_pages`' own guarded scan
    # RAISED, so nobody successfully looked -- the corrupt or otherwise
    # unparseable PDF. A file already known to be misbehaving was the
    # only one still getting an unbounded walk of a worker thread.
    # `truncated` stays irrelevant to the ANSWER: an examined-out scan
    # found no textless page within the ceiling, which routes the
    # document down the ordinary prose path -- the same answer the
    # unbounded walk gave for any document whose first textless page sits
    # past the ceiling, and a document this deep into a corrupt-file
    # fallback is already headed for an honest parse failure downstream.
    pages, _truncated = readers.pdf_textless_pages(
        stored_path, limit=1, max_examined=readers.PDF_SCAN_MAX_PAGES_EXAMINED
    )
    return bool(pages)


def _delete_existing_data(doc: Document) -> None:
    """Remove a Document's prior chunks (pgvector), rows (DocumentRow), and
    stored files (managed store, ADR 0009), in preparation for a fresh
    ingest of a changed file. Safe to call even if the document previously
    had no chunks/rows in one of the two stores (e.g. it flipped from
    tabular to prose, or vice versa)."""
    rag_index.delete_chunks_for_document(doc.id)

    deleted, _ = doc.rows.all().delete()
    if deleted:
        logger.info("ingest: deleted %d prior DocumentRow(s) for Document %s", deleted, doc.id)

    store.remove_document_files(doc.id)


def _category_label(doc: Document) -> str:
    """The normalized (lowercased) category key ingest writes into chunk
    metadata for `doc.category`: the lowercased category name if set, else the
    literal "uncategorized" (ADR 0009 -- retrieval's category filter lowercases
    the requested value and matches on this exact key)."""
    return doc.category.name.lower() if doc.category_id else UNCATEGORIZED.lower()


def _ingest_prose(
    doc: Document,
    path: Path,
    *,
    llama_docs: list[LlamaDocument] | None = None,
    vector_store: PGVectorStore | None = None,
    embed_model=None,
) -> None:
    """Parse `path` (the *stored* copy), chunk into nodes, embed, and write
    to pgvector. Every chunk node's metadata carries `category` (ADR 0009),
    which retrieval filters on.

    `llama_docs`: already-loaded source documents, when the caller has them
    in hand -- `run_ingest_for` passes what `_source_documents` returned,
    so the file is never read twice on the queued path. `None` (every other
    caller: `ingest_prose`/`reencode_all`) now dispatches through
    `_source_documents(doc, path)` itself (T7) rather than always calling
    `read_prose_documents` directly -- this is what makes `reencode_all`
    (`tools.rag.services.reencode_all`) work correctly for a media
    Document: `_source_documents` finds that document's existing
    `extract.json` sidecar and routes through `tools.rag.media.
    documents_from_extract` instead, so re-encoding a transcribed video/
    audio document re-chunks its ALREADY-transcribed text (no transcriber
    call, timestamps intact) rather than trying to `read_prose_documents`
    raw media bytes as if they were a text file.

    `vector_store`: an optional pre-built pgvector store to reuse, threaded
    through to `rag_index.get_index()` (see `ingest_prose`).

    `embed_model`: an already-built LlamaIndex embedding model to thread
    through to `rag_index.get_index()` instead of resolving `rag.embed`
    again -- for a bulk caller (`tools.rag.services.reencode_all`) that
    already resolved the role once (for its own dimension check) and builds
    one embedder for the whole run, the same one-resolve-per-run shape
    `vector_store` already gets. `None` (the default, every other caller)
    builds one fresh via `models.contracts.gateway.get_embed_model` (the
    ordinary role-resolve-then-build path -- this caller has no
    already-resolved `ResolvedModel` of its own to hand `get_embed_model_for`
    the way `reencode_all` does)."""
    if llama_docs is None:
        llama_docs = _source_documents(doc, path)
    if not llama_docs:
        logger.warning("ingest: no text extracted from %s; nothing to index", path)
        return

    category_label = _category_label(doc)

    for llama_doc in llama_docs:
        llama_doc.metadata.update({"file_name": path.name, "source_path": str(path)})
        media.apply_chunk_metadata_exclusions(llama_doc)

    splitter = SentenceSplitter(chunk_size=CHUNK_TOKENS)
    nodes = splitter.get_nodes_from_documents(llama_docs)
    for node in nodes:
        node.metadata["file_id"] = str(doc.id)
        node.metadata["file_name"] = path.name
        node.metadata["source_path"] = str(path)
        node.metadata["category"] = category_label
        media.apply_chunk_metadata_exclusions(node)

    if embed_model is None:
        embed_model = gateway.get_embed_model(RAG_EMBED_ROLE)
    index = rag_index.get_index(vector_store, embed_model=embed_model)
    # Idempotent embed (T7 review M3b): delete this Document's existing
    # chunks BEFORE inserting the new ones, unconditionally -- closes a
    # PRE-EXISTING hole (not media-specific) where re-running ingest against
    # a Document that was already embedded (`tools.rag.views.document_reingest`/
    # `enqueue_reingest`, which re-queues WITHOUT calling `_delete_existing_data`
    # the way a changed-file re-STAGE does) inserted a second copy of every
    # chunk instead of replacing the first. Defensive/cheap when there is
    # nothing to delete yet (a genuinely first-time ingest, or the
    # `reencode_all` rebuild path, which already dropped the whole table) --
    # `rag_index.delete_chunks_for_document` degrades to a no-op rather than
    # raising when the table doesn't exist. `reencode_all`'s own same-dim
    # path still calls this itself too (once per document, sharing this
    # same `vector_store`); the two calls are redundant with each other on
    # that path, not with each other's correctness -- a second delete finds
    # nothing left to delete.
    rag_index.delete_chunks_for_document(doc.id, vector_store=vector_store)
    index.insert_nodes(nodes)
    # THE LABELS ARE NOT THE INDEXING LIBRARY'S BUSINESS. It rewrites the
    # chunk rows wholesale, so a re-ingest of an already-labelled document
    # would silently drop the `entitlements` key without this. Logs
    # rather than raises: a failed re-stamp must not fail an ingest.
    #
    # ROUND 12 REVIEW MINOR 3: THE REPAIR PATH THIS PARAGRAPH USED TO
    # NAME ("the document-label page repairs it") IS NOW CLOSED FOR A
    # CONVERSATION-SCOPED DOCUMENT -- the per-document label route that
    # used to refuse one BY NAME was deleted (C-36);
    # `services.documents_targeted_for_labelling` excludes a chat-scoped
    # row from the bulk writer's target query instead, so the exclusion
    # is closed by QUERY now rather than by a 403 sentence -- same
    # outcome, no route that can be pointed at one of these rows at all.
    # WORSE, THE FAILURE MODE ITSELF CHANGED SHAPE for this
    # `scope`: a universal document that silently misses its
    # `entitlements` key reads as UNLABELLED (a narrower, honest-if-
    # wrong state); a conversation-scoped document that silently misses
    # its `conversation` key reads as an ordinary UNLABELLED UNIVERSAL
    # one instead (`tools.rag.retrieval._visibility_filters`'s own
    # `conversation IS_EMPTY` leg matches an absent key) -- a private
    # chat document would join the UNIVERSAL corpus, the opposite
    # direction from "quietly less available". `tools.rag.views.
    # document_reingest` (a Re-ingest, NOT a re-visit of the label page)
    # is the one repair path still open to a chat-scoped document, and
    # -- UPDATED, whole-branch review A-2/B-2, this paragraph's own
    # first cut was wrong about who could reach it -- its gate is now
    # `tools.rag.access.may_administer_document`, WIDER than `may_label_
    # document` alone: it ALSO admits the document's own UPLOADER
    # (`owner_kind`/`owner_key` matching the acting principal), not only
    # an administrator or an entitlement owner. A chat-scoped document
    # has neither of the latter two by construction, so before that
    # widening this repair path was admin-only -- open to everyone BUT
    # the one person who actually owns the document it is repairing.
    from tools.rag.labels import restamp_document_chunks
    restamp_document_chunks(doc.id)
    logger.info("ingest: indexed %d chunk(s) from %s (Document %s)", len(nodes), path, doc.id)


def ingest_prose(
    doc: Document, path: Path, *, vector_store: PGVectorStore | None = None, embed_model=None
) -> None:
    """Public entry point for (re-)encoding a single prose Document's chunks
    without going through the full ingest pipeline (no dedup / no
    managed-store copy) -- for a caller that already has the Document and
    its retained managed-store copy in hand, notably
    `tools.rag.services.reencode_all`'s bulk re-encode loop. A thin
    wrapper around `_ingest_prose` so external callers don't have to reach
    into a private module name.

    `vector_store`/`embed_model`: see `_ingest_prose`.
    """
    _ingest_prose(doc, path, vector_store=vector_store, embed_model=embed_model)


def _ingest_tabular(doc: Document, path: Path) -> None:
    """Parse `path` into rows, record its schema, and write DocumentRows.
    Tabular data is never embedded (ADR 0005) — it's queried via SQL."""
    # `read_tabular_dataframe` (B-6, round-3 hardening H34) refuses a
    # `.xlsx` whose declared uncompressed size is unreasonable, and a
    # `.csv` over a row-count ceiling, BEFORE building `df` below -- see
    # that function's own docstring and `readers.assert_archive_is_sane`
    # for the two ceilings; this call site owns none of that logic, only
    # what happens to an already-sane frame.
    df = read_tabular_dataframe(path)

    schema = {col: str(dtype) for col, dtype in zip(df.columns, df.dtypes)}
    # Round-trip through pandas' own JSON encoder so numpy scalars / NaN /
    # Timestamps become plain JSON-safe Python values before hitting the
    # Django JSONField.
    records = json.loads(df.to_json(orient="records", date_format="iso"))

    doc.tabular_schema = schema
    doc.save(update_fields=["tabular_schema", "updated_at"])

    rows = [DocumentRow(document=doc, row_index=i, data=record) for i, record in enumerate(records)]
    DocumentRow.objects.bulk_create(rows, batch_size=500)
    logger.info("ingest: wrote %d row(s) from %s (Document %s)", len(rows), path, doc.id)


def category_from_subfolder(file_path: Path, root: Path) -> str | None:
    """Derive a category name from the immediate subfolder of `file_path`
    under `root` (ADR 0009), e.g. `<root>/medical/x.pdf` -> "medical",
    `<root>/medical/sub/x.pdf` -> "medical" (only the first path segment
    counts). Returns None when `file_path` sits directly in `root` (no
    subfolder) or isn't under `root` at all -- callers should treat that as
    "no category" (Uncategorized)."""
    try:
        rel = file_path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) <= 1:
        return None
    return parts[0]


def _source_documents(doc: Document, path: Path) -> list[LlamaDocument]:
    """Dispatch to the text `run_ingest_for`/`_ingest_prose` should
    chunk/embed for a prose `doc`: the extraction sidecar
    (`DOCUMENTS_DIR/<id>/extract.json`, `tools.rag.media.transcribe_to_sidecar`'s
    own output, T7) when present, routed through
    `tools.rag.media.documents_from_extract` -- else the ordinary
    `read_prose_documents` parse of the stored file itself.

    This is the ONE dispatch point both the queued run path and
    `reencode_all` share: `run_ingest_for` calls `transcribe_to_sidecar`
    FIRST for a video/audio medium (writing the sidecar), then reaches this
    function, which now finds that fresh sidecar on disk; `_ingest_prose`'s
    own `llama_docs=None` default (every OTHER caller, notably
    `tools.rag.services.reencode_all`'s bulk loop) calls this function
    directly, so re-encoding an already-transcribed media Document finds
    its EXISTING sidecar and re-chunks the already-transcribed text --
    no transcriber call, timestamps intact.

    `store.sidecar_path_for_dir(path.parent)` (C-18) -- not
    `store.sidecar_path(doc.id)`, though the two agree in every real run
    (`store.store_file`/`move_file` always place the stored file directly
    under `store.document_dir(doc.id)`, so `path.parent` -- what this
    function actually receives -- and `document_dir(doc.id)` are the same
    directory there): the directory-keyed helper resolves the sidecar from
    the SAME argument this function's prose fallback
    (`read_prose_documents(path)`) already uses, not a second, independent
    key that only happens to agree with it in production, while still
    spelling `extract.json` in exactly one place (`store.py`) rather than
    here. `TestSourceDocuments` below exercises this function directly with
    a bare `MagicMock(id=...)` `doc` and a real sidecar written next to
    `path` in `tmp_path`, never touching `settings.DOCUMENTS_DIR` -- those
    tests call the real `store.sidecar_path_for_dir` (they never mock
    `store`) and get back exactly the path they wrote to.

    COMPLETENESS GATE (T7 review M4a): a sidecar is only ever dispatched
    through `documents_from_extract` when its `produced_at` is SET -- a
    genuinely finished transcription/extraction. An in-progress sidecar
    (`produced_at=None`, mid-transcription -- or one left behind by a run
    that died before finishing) must never be silently indexed as if it
    were the whole document: `transcribe_to_sidecar`'s own normal call
    order already guarantees `run_ingest_for`'s queued path never reaches
    this function with an incomplete sidecar (it calls
    `transcribe_to_sidecar` to completion FIRST, and a raise there
    propagates before ever reaching here) -- this gate exists for the OTHER
    caller, `_ingest_prose`'s own `llama_docs=None` default, reached
    directly by `tools.rag.services.reencode_all`'s bulk loop, which does
    NOT filter by `Document.status` on its own (`tools.rag.services.
    reencode_all` skips a not-yet-finished media Document itself before
    ever calling this far, M4b -- this is the belt to that suspenders, for
    any doc that slips past it some other way). Raises `RuntimeError`
    rather than falling through to `read_prose_documents(path)`: `path` is
    the raw video/audio file for a media Document, which
    `read_prose_documents` cannot read at all (it would raise its own,
    less specific "Unsupported prose extension" `ValueError`) -- the
    dedicated message here names the ACTUAL problem (an incomplete
    extraction) instead.

    THE MERGE POINT (W1, D3): for a `.pdf` whose sidecar carries a
    `rasterized_pages` list -- the PLANNED set of textless pages
    `tools.rag.media.extract_to_sidecar` intended to rasterize, written
    identically on every checkpoint write and the completion write (see
    that key's own docstring, `_extraction_sidecar_payload`) -- this
    function reads the document's OWN text layer too
    (`read_prose_documents`) and merges it with the sidecar's extracted
    pages, in page order: the sidecar is never the whole story for a mixed
    PDF, only the pages that had no text layer of their own. The `.pdf`
    suffix gate keeps a video/audio/image sidecar (no page-keyed segments,
    no `rasterized_pages` key at all) out of this branch entirely -- it
    falls through to `media.documents_from_extract(sidecar)` alone, exactly
    today's behavior. A PRE-W1 sidecar has no `rasterized_pages` key at all
    (`isinstance(rasterized, list)` is `False`) -- also today's behavior
    exactly, no silent re-interpretation of old data: those documents stay
    text-only until an operator re-ingests them (ADR 0014 §18's W1 remediation
    note). The `skip` filter (pages the sidecar claims it rasterized) is
    belt-and-braces -- a rasterized page has no text layer by construction,
    so `_read_pdf` never emits one for it -- but a duplicate page would
    double-index, so it's filtered defensively rather than assumed away.
    """
    sidecar_path = store.sidecar_path_for_dir(path.parent)
    if sidecar_path.exists():
        sidecar = read_sidecar(sidecar_path)
        if sidecar is None:
            # C-46: `read_sidecar` returns None for anything that is not
            # a well-formed JSON object -- unreadable, truncated, or a
            # list where an object belongs. Raising here (rather than
            # treating a corrupt sidecar as an ABSENT one) keeps this
            # path's honest failure honest: `run_ingest_or_fail`'s broad
            # except writes `str(exc)` into `status_detail`, and an
            # operator reading the library page needs a sentence, not a
            # `JSONDecodeError` repr.
            raise RuntimeError(
                f"The extraction sidecar for this document is unreadable or corrupt "
                f"({sidecar_path.name}). Re-ingest the document to rebuild it."
            )
        if not sidecar.get("produced_at"):
            raise RuntimeError(
                f"Document {doc.id} has an incomplete extraction sidecar ({sidecar_path}) -- its "
                "transcription/extraction never finished (or is still in progress) -- re-run "
                "ingestion for this document before it can be indexed."
            )
        extracted = media.documents_from_extract(sidecar)
        rasterized = sidecar.get("rasterized_pages")
        if path.suffix.lower() == ".pdf" and isinstance(rasterized, list):
            skip = set(rasterized)
            text_docs = [d for d in read_prose_documents(path) if d.metadata.get("page") not in skip]
            if not text_docs:
                return extracted  # fully scanned -- nothing to merge
            return sorted(text_docs + extracted, key=lambda d: d.metadata.get("page") or 0)
        return extracted
    # `read_prose_documents` -> `_read_docx` (B-6, round-3 hardening H34)
    # refuses a `.docx` whose declared uncompressed size is unreasonable
    # before ever handing it to `python-docx` -- see
    # `readers.assert_archive_is_sane`. Nothing PDF/txt/md-specific
    # changes here; the archive check applies only to the `.docx` branch
    # inside `readers.py`.
    return read_prose_documents(path)


class StageRefused(Exception):
    """`stage_document` declines to stage `path` at all -- for TWO
    distinct reasons, both surfaced to the caller as one honest sentence
    rather than a silent skip:

    - B-2: a re-stage this principal may not perform (a takeover of
      another principal's row at an already-staged path).
    - B-3 (round-3 hardening): the resolved path is outside the
      directory (or directories) this call's `actor` is trusted to
      resolve into -- see the containment check at this function's own
      call site for which door gets which roots.

    Distinct from the cap and duration refusals beside it: those are
    about the FILE's own content/size, these are about WHO is asking and
    WHERE the path resolved to. A member who genuinely re-uploads their
    own work needs to know the difference between "refused" and
    "unchanged".
    """


def _may_restage(actor, document) -> bool:
    """Whether `actor` may reuse `document`'s row for changed bytes at the
    same `original_path` (B-2's re-stage branch, below).

    `document`'s own uploader (`tools.rag.access.is_owner`), or anyone
    `tools.rag.access.may_administer_document` already lets delete or
    re-ingest this row -- the SAME authority boundary this column already
    draws for "who may act on a document", reused here rather than a
    second, narrower one (a bare `is_admin` check) that would happen to
    agree with it on every case this task's tests exercise but diverge
    the moment a document carries an entitlement `may_administer_document`
    already honours (round-3 orchestrator addendum: "one predicate, one
    home").

    `is_owner` is checked separately, not folded into
    `may_administer_document` itself: that predicate's own uploader
    widening is deliberately narrower (`Document.Scope.CONVERSATION`
    only, its own docstring's reasoning) than what a re-stage needs --
    the uploader of an ordinary UNIVERSAL/contained document must be able
    to re-stage their own upload too.
    """
    return access.is_owner(actor, document) or access.may_administer_document(actor, document)


def _acts_for_the_box(actor) -> bool:
    """Whether `actor` is not a principal at all, but the box acting on
    its own behalf (B-2 round-1 review finding 1) -- and so exempt from
    the re-stage authorization check entirely, both `_may_restage`'s own
    decision and the entitlement-clearing branch beside it in
    `stage_document`.

    TWO SHAPES, NOT ONE:

    - `None` -- the CLI (`manage.py ingest`, `ingest_path` below) and the
      notes-consolidation job (`tools.rag.jobs`'s own `stage_document`
      call, keyed deterministically on `NOTES_DIR/<conversation_id>.md`).
      Neither has a request or an operator behind it.
    - `SERVICE_PRINCIPAL` (`identity.contracts.principals.
      SERVICE_PRINCIPAL`) -- THE WATCHER'S REAL ACTOR. `_IngestEventHandler.
      poll_once`'s own `enqueue_ingest` call (below) stamps this, not
      `None`: an earlier cut of this fix conflated "`actor is None`" with
      "the watch folder" and would have refused the watcher's own
      in-place re-index of a file a browser upload happened to land at
      first -- an operator's drop-folder workflow silently breaking the
      moment somebody else uploaded the same filename through the
      library page. `SERVICE_PRINCIPAL` is ONE shared constant
      (`identity/contracts/principals.py`'s own docstring: "the watcher,
      `manage.py ingest`, `manage.py ask` and `manage.py agent_turn` all
      act as it"), so this checks by VALUE (`==`, not `is`) -- a frozen
      dataclass, so equality is structural.

    Neither shape is "somebody" the file/entitlement ownership questions
    below can be asked about, so neither is asked. NOT the same gate the
    containment check (B-3, round-3 hardening) uses -- that one is
    narrower, and deliberately does not exempt `SERVICE_PRINCIPAL`; see
    the containment check's own comment at its call site for why.

    STILL TWO SHAPES, NOT THREE (round-3 hardening H32/C-3, round 2):
    the notes-consolidation job's note now carries the conversation's
    own `owner_kind`/`owner_key` (`tools.rag.jobs.run_consolidate` step
    4, stamped directly on the row AFTER this function returns, never
    through this function's `actor` parameter) -- but that is
    ATTRIBUTION, not OWNERSHIP for re-stage purposes. The job's call
    into `stage_document` still passes `actor=None`, so it is still the
    SAME shape the CLI is here, and a re-consolidation still takes the
    unconditional in-place branch this predicate exists to grant --
    never the authorized-takeover path `_may_restage`/`access.is_owner`
    gate for a real principal. A note is attributed for search and audit
    purposes; it is not "owned" the way a browser-upload document is.
    """
    return actor is None or actor == SERVICE_PRINCIPAL


def stage_document(path: str, category: str | None = None, *, move: bool,
                   workstream_id=None, document_scope=Document.Scope.UNIVERSAL,
                   actor=None) -> tuple[Document, bool]:
    """STAGE half of ingest (module docstring): resolve `path`, dedup on
    `(original_path, file_hash)`, and get its Document row + managed-store
    copy in place -- but do NOT parse/chunk/embed anything. Returns
    `(doc, changed)`: `changed=False` means `path`'s content is unchanged
    since the last time it was staged (the existing Document is returned
    as-is, nothing was copied/moved, nothing needs to run); `changed=True`
    means a NEW or UPDATED Document row now exists at `status=PENDING`,
    ready for `run_ingest_for`.

    `move`: `store.move_file` (the source at `path` is gone afterwards) vs
    `store.store_file` (a copy; `path` is untouched) -- see `store.move_file`'s
    own docstring for which callers want which. Both still run inside one
    `transaction.atomic()` block, matching the pre-T2 `ingest_path`'s own
    shape: the Document row and its managed-store copy come into existence
    together or not at all.

    Raises `FileNotFoundError` if `path` isn't a file, or `ValueError` for
    an extension `tools.rag.readers.medium_for` doesn't recognize at all,
    or one it recognizes as a video/audio/image medium while the "media"
    feature is disabled (`_doc_type_for_medium`), or `MediaDurationExceededError`
    (a `ValueError` subclass, T7 review m2) for a video/audio file over
    `RagSettings.max_media_seconds`.

    DOES NOT DECIDE THE PAGE CAP (B-5, round-3 hardening H28). Before this
    task, this function also ran `_check_document_pages` here and raised
    `DocumentPageCapExceededError` for a scanned/mixed PDF or image over
    `RagSettings.max_document_pages` -- the SAME `readers.pdf_textless_pages`
    scan `_needs_vision_extraction`'s own routing decision needs, which is
    the whole reason that check used to live here: one scan, at stage time,
    serving both questions (C-08). The problem is that "stage time" IS the
    calling thread -- a watcher poll tick, or, for the browser upload door,
    the HTTP request itself -- and `readers.pdf_textless_pages`'s own
    docstring is explicit that an ordinary ALL-TEXT PDF (no textless page to
    short-circuit on) costs a FULL `pypdf.extract_text()` pass over every
    page, with no ceiling on pages EXAMINED (only `limit` on pages
    COLLECTED, which does nothing for this exact case). A large all-text
    PDF therefore pinned an upload request -- uncancellable, no timeout --
    for as long as the scan took; see `tools.rag.readers.pdf_textless_pages`'s
    own docstring for the bound this task added instead (`max_examined`),
    and ADR 0014 §9's dated amendment for the measured cost this was
    closing. BOTH the page-cap decision AND the routing scan that used to
    share it now happen together, ONCE, at the top of `run_ingest_for` --
    off the request thread entirely, in the worker process (or the CLI's
    own synchronous process, which is not a web request either) -- see that
    function's own docstring for exactly where. This function now does
    nothing PDF-content-aware at all; it stages the row and the file and
    nothing else.

    `workstream_id` IS CONTAINMENT (spec §9.2, owner decision 4).
    Keyword-only, defaulting to `None`, which is what keeps the watcher,
    `manage.py ingest`, the `rag.ingest` tool runner and the library
    page's own upload form producing universal documents with not a
    single caller edit.

    IT IS APPLIED AT CREATION AND NOT RE-READ ON A RE-STAGE. A
    document's home is set when it is first staged, and a later content
    change re-ingests it IN PLACE -- so the re-stage branch below leaves
    `workstream` exactly as it found it. Re-homing is not an action this
    product has (§21.2, §22's move-to-stream), and a re-stage that
    silently moved a document would be one.

    `document_scope` (round 12, owner ruling), the SAME "applied at
    creation, never re-read on a re-stage" rule as `workstream_id` just
    above, for the identical reason: a document's SCOPE, like its HOME,
    is set once and does not silently change under a later re-upload of
    the same path. Defaults to `Document.Scope.UNIVERSAL` -- every
    caller before this round, unedited. MUTUALLY EXCLUSIVE WITH
    `workstream_id`: a conversation-scoped document is never contained
    (`tools.rag.views.document_upload`'s own placement resolution never
    sets both), enforced here defensively rather than merely assumed.

    `actor`, OPTIONAL (round 12): stamps `Document.owner_kind`/
    `owner_key` (`identity.access.owner_fields`) onto a NEWLY-CREATED
    row -- the SAME creation-only rule, for the SAME reason. `None` --
    every caller before this round -- leaves both blank here, exactly as
    before this parameter existed.

    THIS FUNCTION IS NO LONGER THE ONLY WRITER OF THOSE TWO COLUMNS
    (round-3 hardening H32/C-3, round 2): `manage.py ingest` still passes
    `actor=None` and its rows stay blank, but `tools.rag.jobs.
    run_consolidate` ALSO passes `actor=None` here (the notes job keeps
    the CLI's own re-stage exemption below, and `NOTES_DIR` is never a
    root the B-3 containment check for a real `actor` would admit
    regardless) and then stamps `owner_kind`/`owner_key` itself, directly
    on the row, immediately after this call returns -- from the
    CONVERSATION's own owner, not from any `actor` this function ever
    sees. `tools.rag.access.is_owner`'s own docstring has the full
    account of that second writer.

    `actor` IS ALSO THE RE-STAGE GATE (B-2, round-3 hardening; the
    `None`/`SERVICE_PRINCIPAL` split below is round-1 review finding 1).
    A changed file at a path already staged is only reused IN PLACE
    unconditionally when `_acts_for_the_box(actor)` -- `actor is None`
    (the CLI, `ingest_path` below, and the notes-consolidation job) or
    `actor == SERVICE_PRINCIPAL` (the WATCHER'S REAL ACTOR,
    `_IngestEventHandler.poll_once`'s own call, below) -- neither is a
    principal, so "same path, new bytes = re-index in place" applies
    unconditionally to both, EVEN over a row somebody else uploaded
    through the browser door. Every OTHER `actor` -- the browser upload
    door's own caller, `stage_and_enqueue_one`, always passes a real one
    -- must be an `actor` `_may_restage` admits (its own uploader, or
    anyone `tools.rag.access.may_administer_document` already lets
    delete or re-ingest it), or the re-stage raises `StageRefused`
    instead of a silent takeover: `<inbox>/<category>/<basename>`
    carries no per-user prefix, so one member's filename collides with
    another's by construction, and reusing that row would otherwise
    destroy the first uploader's chunks and stored file and then
    restamp THEIR labels onto the second uploader's bytes. An
    AUTHORIZED takeover that is NOT by the row's own uploader (an
    administrator, or an entitlement OWNER) also clears the row's
    containment (`workstream`/`scope` back to UNIVERSAL,
    `DocumentAttachment` rows dropped) and re-stamps `owner_fields
    (actor)`, so the new bytes never inherit the victim's wall,
    conversation attachments, or attribution -- see the re-stage
    branch's own comment for why this runs before `_delete_existing_
    data`/`restamp_document_chunks`.
    """
    if document_scope == Document.Scope.CONVERSATION and workstream_id is not None:
        raise ValueError(
            "stage_document: a conversation-scoped document cannot also be contained "
            "in a workstream"
        )
    file_path = Path(path).resolve()
    # B-3 (round-3 hardening), on its own terms -- NOT `_acts_for_the_box`'s
    # inverse (review round 1: that function ALSO exempts `SERVICE_PRINCIPAL`,
    # this check deliberately does not). `actor is None` is the CLI
    # (`ingest_path` below) and the notes-consolidation job
    # (`tools.rag.jobs`'s own call) -- an operator or an internal job
    # naming its OWN file on purpose, exempt. Every OTHER actor is
    # checked, `SERVICE_PRINCIPAL` (the watcher's real actor) included --
    # but not against the same roots: the watcher's own root is the
    # INBOX ONLY (round-1 review ruling). The audit's own attack points a
    # symlink planted in the inbox at a file already living in the
    # managed store or the chat-staging directory; admitting either of
    # those for the watcher would still let that attack through, so a
    # resolved path under either one is REFUSED for `SERVICE_PRINCIPAL`
    # specifically, not merely "not the inbox, so unrecognized". Every
    # other non-`None` actor is a browser-upload door, which keeps its
    # own two roots (the inbox, the chat-staging directory) --
    # `store.assert_inside_platform_dirs` unchanged in name, narrowed in
    # scope (see its own docstring).
    if actor == SERVICE_PRINCIPAL:
        try:
            store.assert_inside_inbox(file_path)
        except ValueError as exc:
            raise StageRefused(str(exc)) from exc
    elif actor is not None:
        try:
            store.assert_inside_platform_dirs(file_path)
        except ValueError as exc:
            raise StageRefused(str(exc)) from exc
    if not file_path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    ext = file_path.suffix.lower()
    medium = medium_for(ext)
    duration_rejection = _check_media_duration(file_path, medium)
    if duration_rejection:
        raise MediaDurationExceededError(duration_rejection)
    # B-5 (round-3 hardening H28): the page-cap decision -- and the
    # `readers.pdf_textless_pages` scan it shares with the vision-routing
    # decision -- used to run here too. Both now happen together at the
    # top of `run_ingest_for`, off this (possibly request-serving) thread;
    # see that function's own docstring and this function's "DOES NOT
    # DECIDE THE PAGE CAP" paragraph above.
    doc_type = _doc_type_for_medium(medium)
    file_hash = _sha256(file_path)
    media_type = _media_type_for(ext)
    original_path = str(file_path)

    with transaction.atomic():
        existing = Document.objects.filter(original_path=original_path).first()

        # NOT AN AUTHORIZATION DECISION, and not covered by `_may_restage`
        # below: a byte-identical re-upload at a path somebody else
        # already staged is indistinguishable, from `actor`'s own point
        # of view, from correctly guessing that exact content already
        # exists in the library at that path -- an EXISTENCE ORACLE
        # (round-1 review, minor), not a takeover, since nothing about
        # the row is ever touched or returned to `actor` beyond the same
        # `(document_id, changed=False)` pair every caller already
        # tolerates for their OWN unchanged re-uploads. Left open
        # deliberately, not silently: closing it is a different, narrower
        # finding (whether `stage_document` should refuse to even
        # CONFIRM another principal's row exists) than B-2's "reusing a
        # row destroys and restamps it", which is what this task closes.
        if existing is not None and existing.file_hash == file_hash:
            logger.info(
                "ingest: %s unchanged since last staging (Document %s); skipping", original_path, existing.id
            )
            return existing, False

        category_obj = get_or_create_category(category)

        if existing is not None:
            # B-2: THE DEDUP KEY IS A PATH EVERY PRINCIPAL SHARES. `<inbox>/
            # <category>/<basename>` has no per-user prefix, so one member's
            # filename collides with another's by construction -- and this
            # branch reuses the row, destroys its chunks and stored file, and
            # then `restamp_document_chunks` stamps the ORIGINAL owner's
            # labels and workstream back onto the new bytes. Reusing a row is
            # therefore an authorization decision, not a bookkeeping one. It
            # fires only when accounts are on: `identity.access.is_admin`
            # (which `may_administer_document` reads through `may_label_
            # document`) answers `True` for every principal on an open box
            # -- there is nobody to refuse on one -- so the refusal below
            # only ever bites once the box has real, distinguishable
            # principals to refuse between.
            #
            # `_acts_for_the_box(actor)` -- `actor is None` (the CLI,
            # `ingest_path` below, and the notes-consolidation job) or
            # `actor == SERVICE_PRINCIPAL` (THE WATCHER'S REAL ACTOR,
            # `_IngestEventHandler.poll_once`'s own call, below) -- is
            # deliberately exempt: neither is a principal, and "same path,
            # new bytes = re-index in place" is that pair's correct
            # behaviour (round-3 report, B-2 "Proposed action") EVEN over a
            # row a browser upload staged first (round-1 review finding 1:
            # an earlier cut of this fix wrongly treated `actor is None` as
            # "the watch folder" and would have refused the watcher's own
            # in-place re-index the moment somebody else's upload landed at
            # the same path). Only the browser-upload door's `actor` (always
            # a real principal, never `None` or `SERVICE_PRINCIPAL`) is
            # subject to the checks below.
            if not _acts_for_the_box(actor):
                if not _may_restage(actor, existing):
                    raise StageRefused(
                        f"{Path(original_path).name} is already in the library under this "
                        "category and belongs to somebody else. Rename your file, or ask "
                        "an administrator to replace theirs."
                    )
                if not access.is_owner(actor, existing):
                    # An AUTHORIZED takeover that is NOT by the row's own
                    # uploader (an administrator, or an entitlement OWNER --
                    # `may_administer_document`'s own gate, never a mere
                    # entitlement HOLDER) is still not the same principal,
                    # so it must not let the new bytes inherit anything that
                    # read as an attribution or a wall:
                    #
                    #   - entitlement labels: `_delete_existing_data` below
                    #     drops the chunks and the stored file, and this
                    #     drops the label rows `restamp_document_chunks`
                    #     would otherwise restore onto bytes from a
                    #     different author (round-3 addendum's own B-2
                    #     "label-survival half").
                    #   - containment: `workstream_id`/`document_scope` are
                    #     otherwise "applied at creation, never re-read on a
                    #     re-stage" (this function's own docstring) -- a
                    #     rule written for the SAME principal re-uploading
                    #     their own work, not for a takeover. A takeover
                    #     clears the wall instead of silently carrying the
                    #     victim's workstream over to a new owner.
                    #   - `DocumentAttachment` rows: a conversation's claim
                    #     on the victim's bytes must not silently become a
                    #     claim on the new uploader's bytes under the same
                    #     row id.
                    #   - `owner_kind`/`owner_key`: re-stamped to the acting
                    #     principal (`identity.access.owner_fields`), the
                    #     SAME stamp a brand-new row gets, so the row's
                    #     recorded uploader is never a lie after a takeover.
                    #
                    # ALL FOUR run BEFORE `_delete_existing_data` (which
                    # only touches chunks/rows/the stored file, not any of
                    # these) and before the queued RUN half can ever call
                    # `restamp_document_chunks` -- there is no window where
                    # a re-stamp could read the stale labels/containment.
                    existing.entitlement_labels.all().delete()
                    existing.workstream = None
                    existing.scope = Document.Scope.UNIVERSAL
                    existing.attachments.all().delete()
                    for _field, _value in owner_fields(actor).items():
                        setattr(existing, _field, _value)
            logger.info("ingest: re-staging changed file %s (Document %s)", original_path, existing.id)
            _delete_existing_data(existing)
            doc = existing
            doc.title = file_path.name
            doc.file_hash = file_hash
            doc.doc_type = doc_type
            doc.tabular_schema = None
            doc.category = category_obj
            doc.media_type = media_type
            doc.status = Document.Status.PENDING
            doc.status_detail = ""
            doc.save()
        else:
            # Save first so the Document has an id -- store.store_file()/
            # move_file() need it to name the managed-store directory.
            # source_path is a required field; it's overwritten with the
            # real stored path immediately below.
            doc = Document.objects.create(
                title=file_path.name,
                source_path=original_path,
                original_path=original_path,
                file_hash=file_hash,
                doc_type=doc_type,
                category=category_obj,
                media_type=media_type,
                status=Document.Status.PENDING,
                workstream_id=workstream_id,
                scope=document_scope,
                **(owner_fields(actor) if actor is not None else {}),
            )

        stored_path = store.move_file(original_path, doc.id) if move else store.store_file(original_path, doc.id)
        doc.source_path = stored_path
        doc.save(update_fields=["source_path", "updated_at"])

    return doc, True


def run_ingest_for(doc: Document, expected_sha256: str, ctx: JobContext | None = None, *,
                    page_scan_max_examined: int | None = readers.PDF_SCAN_MAX_PAGES_EXAMINED) -> dict:
    """RUN half of ingest (module docstring) for a Document already staged
    by `stage_document`: PENDING/queued -> PROCESSING -> parse/chunk/embed
    (prose, including transcribed video/audio -- T7) or parse/rows
    (tabular) -> READY.

    `page_scan_max_examined` (H28 review round 1, finding 1) -- forwarded
    to `_check_document_pages` as its own `max_examined`, which forwards it
    to `readers.pdf_textless_pages`; see that function's own docstring for
    the bound and its justification. DEFAULTS to
    `readers.PDF_SCAN_MAX_PAGES_EXAMINED` -- this function's real callers
    are `tools.rag.jobs.run_ingest` (the queue worker, via
    `run_ingest_or_fail`) and this same wrapper's OTHER caller,
    `ingest_path` (the CLI, `manage.py ingest`) -- and the two want
    different answers. The queue worker shares a finite pool with every
    other job in flight; it keeps the default (bounded, fails closed
    honestly rather than pinning a worker slot indefinitely on a
    pathological upload). `ingest_path` passes `None` explicitly (see that
    function's own docstring) -- an operator running it from a terminal
    has already chosen to wait as long as one file honestly takes, on a
    process nothing else is scheduled against.

    `expected_sha256`: the hash recorded when `doc` was staged, re-verified
    against a fresh hash of the STORED copy right before parsing -- catches
    the (rare, but real once staging and running are split across a queue
    wait) case of the managed-store file having changed on disk since it
    was staged, rather than silently ingesting different bytes than what
    was staged. Raises `RuntimeError` (an honest, operator-facing message)
    on a mismatch.

    `ctx` (T7, `models.contracts.jobkinds.JobContext`): threaded straight
    through to `tools.rag.media.transcribe_to_sidecar` (video/audio) or
    `tools.rag.media.extract_to_sidecar` (image / scanned PDF, T8),
    where it carries progress reporting and checkpoint/resume state for
    that (potentially long-running, multi-slice/multi-page) extraction.
    `None` (the CLI's `ingest_path`, which has no `InferenceJob` row behind
    it) is swapped for `models.contracts.jobkinds.NULL_JOB_CONTEXT` at the
    one call site that actually needs a `ctx` -- everything else in this
    function ignores `ctx` entirely, so a prose/tabular run never has to
    care whether it was given one.

    THE PAGE CAP IS DECIDED HERE (B-5, round-3 hardening H28), for a
    non-AV PROSE medium (an image, or any `.pdf`): `_check_document_pages`
    runs first, and raises `DocumentPageCapExceededError` for one over
    `RagSettings.max_document_pages` -- moved here from `stage_document`,
    which used to make this same decision inline on whatever thread staged
    the file (a watcher poll, or an HTTP upload request); see that
    function's own "DOES NOT DECIDE THE PAGE CAP" paragraph for why. This
    function's own callers (the queue worker, and the CLI's `ingest_path`)
    are never a web request, so this is the right place for a scan that
    can cost a full pass over an all-text PDF's every page. The SAME scan
    answers the routing question just below (`_needs_vision_extraction`'s
    `textless=` parameter) -- see the code's own comment for how the two
    share it.

    For a video/audio medium, or a medium `_needs_vision_extraction`
    recognizes (image, or a scanned/mixed PDF -- the auto-detect;
    `tools.rag.readers.medium_for` on the stored file's own extension --
    `doc.doc_type` is `PROSE` for all of these, see `_doc_type_for_medium`,
    so it alone can't distinguish any of these cases): `transcribe_to_
    sidecar`/`extract_to_sidecar` runs FIRST, writing `extract.json` and
    `doc.duration_seconds`/`doc.extraction` (transcription) or just
    `doc.extraction` (extraction, T8 -- a page/image has no duration) --
    THEN `_source_documents` (below) finds that fresh sidecar and routes
    through `tools.rag.media.documents_from_extract`, merged with the
    document's own text layer for a mixed PDF (W1, `_source_documents`'s own
    docstring), exactly the same dispatch a later `reencode_all` re-encode
    of this same Document reaches on its own. A raise from either driver
    (an unresolved/unhealthy role, a `GenerationRejected`/transport failure
    from the engine, an over-cap duration for AV) propagates straight out
    of this function, same as every other exception here -- see this
    function's own "does NOT write FAILED" paragraph below -- WITH ONE
    EXCEPTION, the text-only fallback immediately below.

    TEXT-ONLY FALLBACK (W1 decision D4): a mixed PDF must never fail for
    want of a vision model. `media.extract_to_sidecar` raises `media.
    ModelRoleUnavailable` (a `RuntimeError` subclass) when `rag.extract`
    isn't bound/healthy -- caught here, and ONLY for a `.pdf`
    (`stored_path.suffix.lower() == ".pdf"`; anything else, notably an
    IMAGE, re-raises IMMEDIATELY, bare, preserving the original traceback
    and its `model_unavailable_message` copy -- an image has no text layer
    to fall back to, and `_source_documents` -> `read_prose_documents` would
    raise its OWN, less honest "Unsupported prose extension" `ValueError`
    if this fallback were ever allowed to reach it). For a `.pdf`, this logs
    a warning and sets `role_unavailable = exc` (the caught exception
    itself -- truthy, so it reads exactly like a boolean flag everywhere
    below, but see the DEVIATION note next paragraph), then falls through
    to `_source_documents` as normal: with no fresh sidecar on disk, that
    call finds either an EXISTING (pre-W1 or prior-run) sidecar or none at
    all, and either way returns whatever the document's own text layer
    parses to. If that's EMPTY (a fully-scanned PDF -- nothing to fall back
    TO), this re-raises the ORIGINAL `ModelRoleUnavailable` rather than
    silently writing an empty-but-READY Document -- today's honest failure,
    unchanged. Otherwise the text pages ingest normally and the READY write
    below carries `_TEXT_ONLY_FALLBACK_NOTE` in `status_detail` instead of
    the usual empty string, naming the exact fix (bind `rag.extract`, then
    Re-ingest) -- see `documents.html`'s Ready-chip line, which is the only
    place that note is ever shown.

    INCOMPLETE PRIOR SIDECAR (W1 review MINOR 3): this fallback's own
    "falls through to `_source_documents` as normal" story below assumes
    whatever sidecar `_source_documents` finds on disk is either absent or
    genuinely finished. An INCOMPLETE one -- `produced_at` unset, left
    behind by an earlier run that crashed mid-extraction rather than ever
    reaching this catch -- is a third, narrower case: `_source_documents`'s
    own completeness gate raises `RuntimeError` on it BEFORE this function
    ever gets a chance to fall back to the text layer, so that Document
    still ends up FAILED (via `run_ingest_or_fail`) rather than landing on
    a text-only READY. The text-only fallback below only ever applies when
    `_source_documents` finds no sidecar, or a genuinely finished one.

    DEVIATION from the design note's literal pseudocode (recorded, not
    silently "improved"): the design describes `role_unavailable` as a
    plain `bool`, re-raised below with a bare `raise`. A bare `raise` is
    only legal while an `except` block is still active on the call stack --
    by the time `_source_documents` runs, this function's own `except
    media.ModelRoleUnavailable` suite has already exited normally, so
    Python has no "currently handled exception" left for a bare `raise` to
    reproduce (it raises `RuntimeError: No active exception to re-raise`
    instead). `role_unavailable` therefore holds the caught exception
    OBJECT, and the fully-scanned-PDF branch does `raise role_unavailable`
    explicitly -- reproducing the same exception type/message/traceback the
    bare `raise` was meant to preserve, just spelled so it is valid this far
    outside the `except` suite. Every other use of `role_unavailable`
    (truthiness checks, the `status_detail` line) is unaffected -- an
    exception instance is truthy exactly like `True`.

    Deliberately does NOT write `Document.status = FAILED` on its own
    exceptions -- it raises straight through, including this function's own
    sha mismatch. Callers use `run_ingest_or_fail` (below), not this
    function directly, unless they have their own reason to handle the
    FAILED write themselves -- see that wrapper's docstring for why a bare
    call to this function must never be left uncaught.
    """
    doc.status = Document.Status.PROCESSING
    doc.save(update_fields=["status", "updated_at"])

    stored_path = Path(doc.source_path)
    actual_sha256 = _sha256(stored_path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"ingest: the stored copy of Document {doc.id} ({stored_path}) no longer matches "
            f"the hash recorded when it was staged (expected {expected_sha256}, found "
            f"{actual_sha256}) -- refusing to ingest a file that changed on disk after staging."
        )

    # `role_unavailable` lives ABOVE the doc_type/medium switch, never
    # between an `if` and its `elif` -- it must be defined regardless of
    # which branch (or none) actually sets it, so the fallback-note check
    # after the switch never sees an undefined name (W1 review N1).
    role_unavailable = None

    if doc.doc_type == Document.DocType.PROSE:
        effective_ctx = ctx if ctx is not None else NULL_JOB_CONTEXT
        medium = medium_for(stored_path.suffix.lower())
        if medium in ("video", "audio"):
            media.transcribe_to_sidecar(doc, effective_ctx)
            needs_vision = False
        else:
            # B-5 (round-3 hardening H28): the page-cap decision -- and the
            # `readers.pdf_textless_pages` scan the routing decision just
            # below shares with it (`_check_document_pages`'s own "ONE
            # SCAN, TWO USES" paragraph) -- both happen HERE now, at the
            # top of a RUN, never at STAGE/ENQUEUE time: this function's
            # own callers are the queue worker and the CLI's synchronous
            # process (`ingest_path`), never a web request, so this is the
            # first point in the pipeline allowed to spend unbounded CPU on
            # an all-text PDF's full scan. `stage_document`'s "DOES NOT
            # DECIDE THE PAGE CAP" paragraph and `_enqueue_ingest_job`'s
            # own docstring have the full story of what moved and why.
            # `probe_out["textless"]`, once `_check_document_pages` has
            # run, IS the exact list `_needs_vision_extraction` needs for
            # its own routing question -- threaded through as `textless=`
            # so this function pays `readers.pdf_textless_pages` AT MOST
            # ONCE per run, not twice -- except a corrupt/unreadable PDF,
            # the ONE case where it is deliberately paid TWICE: `_check_
            # document_pages`'s own GUARDED except swallows the first
            # attempt's failure (leaving `probe_out` empty -- "nobody
            # successfully looked"), so `_needs_vision_extraction` below
            # runs its own UN-guarded second attempt, and THAT is what
            # surfaces the corruption honestly (T8 review MAJOR 1) instead
            # of silently skipping the cap check and then silently
            # ingesting nothing. `page_scan_max_examined` (H28 review
            # round 1, finding 1) bounds the first attempt -- see this
            # function's own docstring for who passes what.
            probe_out: dict = {}
            page_rejection = _check_document_pages(
                stored_path, medium, probe_out=probe_out, max_examined=page_scan_max_examined
            )
            if page_rejection:
                raise DocumentPageCapExceededError(page_rejection)
            needs_vision = _needs_vision_extraction(stored_path, medium, textless=probe_out.get("textless"))
        if needs_vision:
            try:
                media.extract_to_sidecar(doc, effective_ctx)
            except media.ModelRoleUnavailable as exc:
                if stored_path.suffix.lower() != ".pdf":
                    raise  # an IMAGE has no text layer to fall back to -- bare, preserves traceback
                logger.warning(
                    "ingest: Document %s has pages with no text layer but no vision model is "
                    "available -- ingesting its text layer only",
                    doc.id,
                )
                # `role_unavailable` holds the caught exception itself, not a plain `True`: a
                # bare `raise` below (the "nothing to fall back to" case) is only legal INSIDE
                # an active `except` block -- by the time `_source_documents` returns, this
                # `except` suite has already exited normally, so Python has no "currently
                # handled exception" left to re-raise bare. Holding the exception instance
                # (still truthy, so every `if role_unavailable:` check below reads exactly like
                # the boolean flag the design describes) lets `raise role_unavailable` reproduce
                # the ORIGINAL failure -- same type, same `model_unavailable_message` text, same
                # underlying traceback (exception objects carry their own `__traceback__`) --
                # for the one path that needs to re-raise it after this block has closed.
                role_unavailable = exc
        llama_docs = _source_documents(doc, stored_path)
        if role_unavailable and not llama_docs:
            raise role_unavailable  # a fully-scanned PDF has nothing to fall back to
        _ingest_prose(doc, stored_path, llama_docs=llama_docs)
    else:
        _ingest_tabular(doc, stored_path)

    doc.status = Document.Status.READY
    doc.status_detail = _TEXT_ONLY_FALLBACK_NOTE if role_unavailable else ""
    doc.save(update_fields=["status", "status_detail", "updated_at"])

    return {"document_id": doc.id, "title": doc.title}


def _fail_document(doc: Document, detail: str) -> None:
    """Mark `doc` FAILED with `detail`, unconditionally, on the row the
    caller already holds (C-31). The two sites this replaces --
    `run_ingest_or_fail`'s except branch and `_enqueue_ingest_job`'s
    failure tail below -- each wrote the identical three lines.

    NOT `tools.rag.jobs._fail_stranded_rows`: that one is a conditional
    queryset `.update()` guarding against a still-live worker (it only
    resolves rows a live attempt hasn't already claimed). This one is an
    unconditional single-row `.save()` on a `Document` the caller already
    holds in memory, with no live-worker race to guard against. They are
    not the same helper and must not be merged.
    """
    doc.status = Document.Status.FAILED
    doc.status_detail = detail
    doc.save(update_fields=["status", "status_detail", "updated_at"])


def run_ingest_or_fail(doc: Document, expected_sha256: str, ctx: JobContext | None = None, *,
                        page_scan_max_examined: int | None = readers.PDF_SCAN_MAX_PAGES_EXAMINED) -> dict:
    """`run_ingest_for(doc, expected_sha256, ctx)`, but catching ANY exception to
    write `doc.status = Document.Status.FAILED` + `doc.status_detail =
    str(exc)` before re-raising the SAME exception -- the one shared place
    both of `run_ingest_for`'s real callers use, rather than each
    reimplementing this try/except by hand: `ingest_path` (the synchronous
    CLI path) and `tools.rag.jobs.run_ingest` (the queued path, where the
    re-raised string also becomes the job row's own `error` column, so the
    job and the Document agree on one wording for one failure).

    This write is NOT optional for either caller. `run_ingest_for` itself
    has no re-entrancy guard -- nothing stops two calls from running
    against the same Document concurrently -- which is exactly why
    `tools.rag.views.document_reingest` refuses to re-queue a
    PENDING/PROCESSING row (racing a job that might still be running would
    corrupt its output, not just waste work). That refusal means
    PROCESSING has exactly ONE way out: whichever caller put `doc` into
    PROCESSING (via `run_ingest_for`) must itself resolve it to READY or
    FAILED. A caller that let an exception propagate past this point
    without writing FAILED would stall the row at PROCESSING forever --
    invisible to `document_reingest` (refused as "already being
    processed"), and NOT rescued by a subsequent `ingest_path`/
    `enqueue_ingest` call on the same source file either: staging's own
    dedup-on-hash (`stage_document`) sees the unchanged `file_hash` and
    returns `changed=False` without ever touching `run_ingest_for` again.
    The only way out of that trap would be hand-editing the source file's
    bytes to force a new hash -- not a real recovery path. Every caller of
    `run_ingest_for` MUST go through this wrapper (or reproduce its
    behavior exactly) so that never happens.

    WORK-DIR PURGE on failure (T7 round-3 review ADJUDICATED, module
    docstring): a media Document's `work/` directory
    (`tools.rag.media.transcribe_to_sidecar`'s extracted WAV + slices) is
    removed here UNCONDITIONALLY, on every exception this wrapper catches
    -- not conditionally on `ctx.attempt`, a previous cut's mistake. Traced
    through both paths that could ever reach this except branch:

    - An operator's manual Retry (`tools.rag.views.document_reingest` ->
      `enqueue_reingest`) always creates a BRAND-NEW job with a fresh
      `ctx.checkpoint_state=None` -- `transcribe_to_sidecar`'s own
      `reuse_prior_extraction` is therefore always `False` on a retry,
      REGARDLESS of what `work/` still holds, and extraction/slicing reruns
      from scratch, silently overwriting whatever was there. A retained
      `work/` dir never speeds up a manual retry at all.
    - The one case that COULD resume from a retained `work/` dir -- a
      requeued attempt with a SURVIVING `checkpoint_state`, from
      `models.queue.claim._sweep_orphans` forgiving a crashed worker --
      never reaches this except branch in the first place: the worker
      process died mid-job, so nothing here ever ran to catch anything.
      This wrapper's own except branch, by construction, only ever executes
      for a job that finished its own attempt (successfully caught its own
      exception) -- exactly the case where no future resume of THIS job
      will ever use `work/` again.

    So `work/` retention was never actually protecting a real resume path;
    it only leaked disk. Removed here, best-effort
    (`shutil.rmtree(..., ignore_errors=True)`), the same way
    `transcribe_to_sidecar`'s own clean-finish removal is -- a leftover
    directory is not itself a failure worth raising over. The one scenario
    where `work/` genuinely survives to be resumed from is the crash case
    above, which this function's own code never runs for.

    `page_scan_max_examined`: passed straight through to `run_ingest_for`
    -- see that function's own docstring for the default and who overrides
    it.
    """
    try:
        return run_ingest_for(doc, expected_sha256, ctx, page_scan_max_examined=page_scan_max_examined)
    except Exception as exc:
        _fail_document(doc, str(exc))
        work_dir = store.work_dir(doc.id)
        shutil.rmtree(work_dir, ignore_errors=True)
        raise


def _enqueue_ingest_job(doc: Document, actor) -> int | None:
    """Build the `rag.ingest` payload from an already-staged `doc` and
    enqueue it -- the shared tail of `enqueue_ingest` and
    `enqueue_reingest`. On ANY failure between here and a successful
    `enqueue()` call -- marks `doc` FAILED with an honest
    `status_detail`, logs, and returns `None` -- NEVER raises: both callers
    run at a boundary (a watcher thread, an HTTP request that already told
    the browser "queued") that must not crash on a down queue. The
    Document row is the permanent record of the failure; an operator sees
    it (the library's Failed chip + Retry button) and can retry once the
    queue is back.

    `QueueQuotaExceeded` DOES mark `doc` FAILED too (fix round 2 -- the
    round 1 "leave `doc.status` untouched, only `status_detail`" ruling
    is WITHDRAWN): FAILED is the one state `tools.rag.views`'s library
    page already renders WITH its `status_detail` and a Retry form, and
    `document_reingest` already admits it for another attempt. PENDING
    (what round 1 left the row at) hides the detail from that page
    entirely, offers no Retry form, and `document_reingest` itself
    refuses a PENDING/PROCESSING row as "already being processed" -- so a
    quota-refused upload under the round-1 behavior was invisible and
    unretriable from the UI, worse than the FAILED row every OTHER
    enqueue failure already gets. `_QUEUE_QUOTA_EXCEEDED_DETAIL` is the
    detail; the distinction from `QueueUnavailable`/a genuine failure is
    in the STRING an operator reads, not in the STATE the row lands in.

    `payload["medium"]` IS `tools.rag.readers.medium_for` ON THE STORED
    FILE'S EXTENSION -- PLUS ONE PESSIMISTIC OVERRIDE, NEVER A SCAN (B-5,
    round-3 hardening H28; corrected in review round 1, finding 2). Before
    H28, a `.pdf` this function found to be scanned/image-only got the
    literal string `"pdf-scanned"` here instead of "prose" -- a
    CONTENT-based override, decided by calling `_needs_vision_extraction`
    (which, absent a scan another caller already paid for, runs its own
    `readers.pdf_textless_pages` pass) right here, on THIS function's own
    caller's thread: a watcher poll tick, or, for `enqueue_ingest`, the
    HTTP upload request itself. `readers.pdf_textless_pages`'s own
    docstring is explicit that an all-text PDF costs a FULL scan at any
    positive `limit` -- so this override paid the exact same unbounded,
    request-thread cost `stage_document`'s own former page-cap check did
    (see that function's "DOES NOT DECIDE THE PAGE CAP" paragraph), just
    for the ROUTING question instead of the CAP question.

    H28's first cut removed the override entirely -- `payload["medium"]`
    was always the bare extension-only answer, "prose" for every `.pdf`.
    Review round 1 found that too permissive: `tools.rag.jobs.plan_ingest`'s
    admission accounting then reserved `rag.embed` ONLY, never
    `rag.extract`, for EVERY `.pdf` at enqueue time -- not just the rare
    flag-flipped-mid-flight case the "T8 CROSS-PROCESS FLAG DIVERGENCE
    WINDOW" paragraph on `_needs_vision_extraction` already accepted, but
    the ORDINARY case, on every genuinely-scanned PDF, permanently. The
    scheduler's own `models/queue/scheduler.py` rule 2(b) ("an unmeasured
    footprint is treated as effectively exclusive") does NOT backstop
    this the way H28's own first-cut docs claimed: rule 2(b) only serves
    as a backstop when EVERY declared role is unmeasured -- `rag.embed`
    frequently reports a real footprint once an operator has configured
    one, and a job admitted on `rag.embed` alone, that then also runs
    `rag.extract` at run time, is exactly the under-accounted-concurrency
    hole `plan_ingest`'s own module docstring warns against (ADR 0014 §9's
    amendment, corrected in the same round).

    THE FIX: still no scan, but no longer purely extension-only either --
    `medium` becomes the literal string `"pdf-scanned"` for EVERY `.pdf`
    while `"media" in settings.FARABUNKER_FEATURES`, off the EXTENSION
    ALONE (no `readers.pdf_textless_pages` call, no file content read at
    all) -- PESSIMISTIC, not auto-detected: an ordinary text-layer PDF now
    reserves `rag.extract` capacity at enqueue time it will never use, in
    exchange for a genuinely-scanned one never being under-provisioned.
    `run_ingest_for` still independently re-derives the REAL routing
    answer at run time (content-based, via `_needs_vision_extraction`) --
    this function's payload only ever feeds `plan_ingest`'s admission
    math, never actual routing; see that function's own docstring for why
    a stale/divergent enqueue-time snapshot there is already an accepted,
    named risk (the flag-flip case), now simply not compounded by an
    additional, avoidable under-reservation on top of it.
    """
    try:
        stored_path = Path(doc.source_path)
        medium = medium_for(stored_path.suffix.lower())
        if medium == "prose" and stored_path.suffix.lower() == ".pdf" \
                and "media" in settings.FARABUNKER_FEATURES:
            # H28 review round 1, finding 2: PESSIMISTIC, off the
            # extension alone -- see this function's own docstring. Every
            # `.pdf` reserves vision-extraction capacity while the flag is
            # on, whether it turns out to need it or not.
            medium = "pdf-scanned"

        payload = {
            "document_id": doc.id,
            "sha256": doc.file_hash,
            "medium": medium,
            "title": doc.title,
            # WHO ASKED -- the acting principal, stamped into every job
            # payload (the acting rule). `actor` is
            # REQUIRED on every caller of this function; see
            # `enqueue_ingest`'s own docstring for why a default here
            # would be a fail-open default.
            **payload_fields(actor),
        }
        return enqueue("rag.ingest", payload)
    except QueueQuotaExceeded as exc:
        # C-7 review, fix round 2: caught BEFORE the broad `except
        # Exception` below, same as round 1 -- but now falls through to
        # the shared `_fail_document(doc, detail)` tail like every other
        # branch here (round 1 skipped it; see this function's own
        # docstring for why that was withdrawn). Only the STRING differs
        # from the other two failure readings.
        logger.info(
            "ingest: queue quota exceeded enqueuing rag.ingest for Document %s: %s",
            doc.id, exc,
        )
        detail = _QUEUE_QUOTA_EXCEEDED_DETAIL
    except QueueUnavailable:
        logger.exception("ingest: queue unavailable enqueuing rag.ingest for Document %s", doc.id)
        detail = _QUEUE_UNAVAILABLE_DETAIL
    except Exception:  # noqa: BLE001 -- log detail, degrade to an honest FAILED row
        logger.exception("ingest: failed to enqueue rag.ingest for Document %s", doc.id)
        detail = _QUEUE_ENQUEUE_FAILED_DETAIL

    _fail_document(doc, detail)
    return None


def document_at_path(original_path) -> Document | None:
    """The `Document` staged at `original_path`, or None.

    THE STAGING QUESTION, NOT A VISIBILITY ONE. `document_upload` asks it
    twice -- once to tell "already staged, unchanged" from "already
    staged, changed", and once, in its `FileNotFoundError` branch, to tell
    "the watcher won the race" from "staging genuinely failed". It lives
    here rather than in the view because `tools/rag/views.py` may not
    touch `Document.objects` at all (`foundation/ops/tests/
    test_column_boundaries.py`), and because both callers are asking about
    the INBOX, which is this module's subject.
    """
    return Document.objects.filter(original_path=str(original_path)).first()


def enqueue_ingest(path: str, category: str | None = None, *, move: bool, actor,
                   workstream_id=None, document_scope=Document.Scope.UNIVERSAL) -> int | None:
    """Stage `path` (`stage_document`) and enqueue its `rag.ingest` queue
    job -- the watcher/upload entry point (module docstring). Returns the
    new job id, or `None` when either (a) the file is unchanged since its
    last staging (`stage_document` returns `changed=False` -- nothing new
    to queue), or (b) the row was staged but the queue itself rejected the
    enqueue (`_enqueue_ingest_job` already marked it FAILED).

    `actor` is REQUIRED and keyword-only, deliberately (the
    acting rule): this function builds the payload the runtime
    later acts as, and a default of "the box" would silently attribute an
    uploaded document's ingest to nobody. The upload view supplies
    `identity.request.principal_for_request(request)`; the watcher
    (`poll_once`, below) supplies `identity.contracts.principals.
    SERVICE_PRINCIPAL`.

    `move`: forwarded to `stage_document` -- see that function's docstring.

    `workstream_id` is forwarded to `stage_document` -- see that
    function's docstring for what it does and what it deliberately does
    not do on a re-stage.

    `document_scope` (round 12) is forwarded too, along with `actor`
    itself -- `stage_document`'s own new `actor=` parameter stamps
    ownership onto a NEWLY-CREATED row (`identity.access.owner_fields`),
    reusing the SAME acting-rule value this function already requires
    rather than asking every caller for it a second time.

    Never raises past staging's own `ValueError` (an unsupported/media
    extension -- a caller bug, since every caller filters by
    `supported_exts()` first), `StageRefused` (B-2: `actor` may not
    take over another principal's row at this path), or `FileNotFoundError`
    -- an enqueue failure specifically is swallowed (see
    `_enqueue_ingest_job`), never a staging failure, since staging failing
    means there IS no Document row yet for a status write to land on.
    `FileNotFoundError` in particular is not always a caller bug: a
    `move=True` caller can legitimately race another mover of the SAME
    source file (`tools.rag.views.document_upload` racing the
    `watch_folder` service polling the same inbox is the one real instance
    of this today -- see that view's own docstring for how it handles
    losing that race).

    NO PDF SCAN AT ALL, ON THIS THREAD (B-5, round-3 hardening H28). This
    function used to own a fresh `probe_out` dict for the duration of one
    call (C-08), threading it through both `stage_document` (which wrote
    a `"textless"` key into it) and `_enqueue_ingest_job` (which read that
    key back) so an all-text PDF was `pdf_textless_pages`-scanned once per
    upload, not twice. Neither callee scans the file's content anymore --
    see each one's own docstring -- so there is nothing left to thread
    between them, and `probe_out` is gone.
    """
    doc, changed = stage_document(path, category, move=move, workstream_id=workstream_id,
                                  document_scope=document_scope, actor=actor)
    if not changed:
        return None
    return _enqueue_ingest_job(doc, actor)


@dataclass(frozen=True)
class StageOutcome:
    """What happened to ONE uploaded file (C-14).

    Six kinds, not the three the two callers each collapse to, because
    they collapse them DIFFERENTLY and this type exists so neither has to
    change: the browser path flashes an oversize file and counts it
    nowhere, while the chat path folds it into `failed` with the cap in
    the string. Handing back the distinction lets each keep its own
    reporting; folding it here would silently move one of them.

    `document` is set for every kind that produced or found a row --
    `queued`, `unchanged`, and the `failed` branch that staged a row
    before the enqueue declined it -- because the chat path attaches
    every one of those to its turn.
    """
    name: str
    kind: Literal["queued", "unchanged", "rejected", "oversize", "refused", "failed"]
    document: Document | None = None
    reason: str = ""   # "refused" only -- "oversize" has no exception to
                        # quote, and both callers already hold their own
                        # `human_cap` to build their own sentence around.
                        # Usually the exception's own message verbatim
                        # (`MediaDurationExceededError`/
                        # `DocumentPageCapExceededError`/`StageRefused`,
                        # for either of ITS two reasons -- B-2's takeover
                        # refusal or B-3's containment refusal); the one
                        # exception is `SymlinkRefused` (B-3's write-door
                        # refusal), whose own message names the low-level
                        # `ELOOP` mechanics, not the uploader-facing
                        # sentence its own `except` clause builds instead.
    is_tabular: bool = False
    raced: bool = False  # a watcher won the race; counted queued by both callers


def stage_and_enqueue_one(
    upload, target_dir: Path, *, category: str | None, actor,
    workstream_id: int | None, document_scope, max_upload_bytes: int,
    upload_exts: frozenset[str], log_label: str,
) -> StageOutcome:
    """Write `upload` into `target_dir`, decide whether it is new, and
    enqueue it for ingest -- the per-file half both upload doors share.

    THE TWO DOORS ARE `views.document_upload` (the library's browser
    form) and `services.stage_turn_attachments` (the chat composer's
    attach). `services.py`'s own comment already recorded that the second
    "mirrors the first almost verbatim"; this is where the verbatim part
    now lives. What it does NOT do is any bookkeeping: no flash, no
    label, no `_attach`, no summary counter, no return type. Each caller
    switches on `StageOutcome.kind` and keeps every one of those exactly
    as it had them.

    `log_label` is the caller's own name for itself in the log lines --
    an operator reading "document_upload" and one reading
    "stage_turn_attachments" are looking at different surfaces.

    `StageOutcome.document` is resolved UNCONDITIONALLY on every kind that
    can carry one (`queued`, `unchanged`, the staged-but-declined `failed`
    branch) -- one extra `SELECT` per file even on a call whose caller
    will not end up using it (`document_upload` only reaches for it when
    `wanted_labels` is truthy) -- because the OTHER door always needs it:
    `stage_turn_attachments` attaches every one of those to its turn, so
    resolving it here once, unconditionally, is simpler and cheaper than
    handing back a lazy handle or making every caller re-derive it.

    HASH BEFORE ENQUEUE, and that ordering is load-bearing: `enqueue_ingest`
    moves the file, so the unchanged-comparison has to read it first. The
    `FileNotFoundError` branch below exists because the watcher can win
    that race, and a file the watcher already staged is queued, not lost.
    """
    name = os.path.basename(upload.name)
    ext = os.path.splitext(name)[1].lower()
    if ext not in upload_exts:
        return StageOutcome(name=name, kind="rejected")
    if upload.size > max_upload_bytes:
        return StageOutcome(name=name, kind="oversize")

    dest = target_dir / name
    # H13 review round 1, finding 7: created at 0600 directly -- the ONE
    # write site both upload doors (`views.document_upload`,
    # `services.stage_turn_attachments`) funnel through, see this
    # function's own docstring.
    try:
        with create_locked_file(dest) as out:
            for chunk in upload.chunks():
                out.write(chunk)
    except SymlinkRefused as exc:
        # B-3 (round-3 hardening): `dest` was ALREADY a symlink -- planted
        # by whoever else can write into `target_dir` (the inbox for
        # `document_upload`, the chat-staging directory for
        # `stage_turn_attachments` -- BOTH doors funnel through this one
        # write site, so the message below names neither by name) -- and
        # `create_locked_file`'s own `O_NOFOLLOW` refused to open, and
        # therefore never truncated, whatever it points at. Deliberately
        # does NOT `dest.unlink()` the way the refusal branches below
        # clean up their own stray copy: this call never created `dest`,
        # so removing it would delete an entry this door does not own.
        logger.error("%s: refusing to write %s -- %s", log_label, name, exc)
        return StageOutcome(
            name=name, kind="refused",
            reason=f"{name} could not be uploaded -- that name is already taken by a link at "
                   "the destination. Rename your upload, or ask an administrator to clear it.",
        )

    try:
        # W1 review MAJOR 1 ("the re-upload lie"): `enqueue_ingest` returns
        # `None` for either of two reasons (its own docstring) -- `dest`'s
        # content is byte-identical to what is already staged at this
        # exact path (nothing to queue, an honest "unchanged" outcome), or
        # staging succeeded but the queue itself rejected the enqueue (a
        # genuine failure). Telling them apart needs a hash taken BEFORE
        # calling `enqueue_ingest`: a CHANGED file gets MOVED out of `dest`
        # by `stage_document` before this function ever sees the return
        # value, so hashing `dest` only in the `None` branch below would
        # raise `FileNotFoundError` for exactly the "changed" case this
        # check exists to rule out. This read lives INSIDE the
        # `try`/`except FileNotFoundError` too -- the watcher can win its
        # race against THIS read just as easily as against `enqueue_ingest`
        # itself.
        existing = document_at_path(dest)
        dest_unchanged = existing is not None and existing.file_hash == _sha256(dest)

        job_id = enqueue_ingest(
            str(dest), category, move=True, actor=actor,
            workstream_id=workstream_id, document_scope=document_scope)
    except FileNotFoundError:
        # Lost the race against the watcher (see docstring). Verify a
        # Document row actually exists at this original path before
        # counting it queued, so a GENUINE staging failure is never
        # silently counted as a success.
        raced_document = document_at_path(dest)
        if raced_document is not None:
            logger.info(
                "%s: %s was already staged (the watcher likely won a race); "
                "counting it as queued", log_label, name,
            )
            return StageOutcome(name=name, kind="queued", document=raced_document, raced=True)
        logger.error(
            "%s: %s's source vanished but no Document row exists at %s -- "
            "not counting it as queued", log_label, name, dest,
        )
        return StageOutcome(name=name, kind="failed")
    except MediaDurationExceededError as exc:
        # `exc`'s own message already names the limit and how to fix it --
        # shown verbatim by whichever caller wants it. Same stray-temp-copy
        # cleanup as the oversize branch's callers apply themselves:
        # `stage_document` raises BEFORE it ever moves `dest` into the
        # managed store, so the full-size copy this function wrote above
        # is left stray in `target_dir` otherwise.
        dest.unlink(missing_ok=True)
        return StageOutcome(name=name, kind="refused", reason=str(exc))
    except DocumentPageCapExceededError as exc:
        # The page-cap sibling of the duration-cap branch just above --
        # same by-name catch, same message-verbatim-and-not-generic
        # treatment, same stray-copy cleanup. B-5 (round-3 hardening H28):
        # `enqueue_ingest` no longer raises this from THIS call in ordinary
        # operation -- the page-cap decision moved to job start (`tools.
        # rag.ingest.run_ingest_for`'s own docstring), so an over-cap PDF
        # now stages/enqueues successfully and fails later, as a FAILED
        # Document row, not here. This branch stays for the same reason
        # the type itself stays a distinct `ValueError` subclass: a
        # by-name catch costs nothing to keep, and a future caller of
        # `enqueue_ingest` that DOES decide something inline is still
        # given the honest, non-generic treatment its own message earns.
        dest.unlink(missing_ok=True)
        return StageOutcome(name=name, kind="refused", reason=str(exc))
    except StageRefused as exc:
        # B-2: `actor` may not take over ANOTHER principal's row at this
        # path -- the file/duration caps' own by-name catch and
        # message-verbatim treatment, one authorization exception over.
        # `stage_document` raises this BEFORE `_delete_existing_data`
        # ever runs, so the victim's row is untouched; the unlink here is
        # not only cleanup, it is load-bearing: `dest` sits inside the
        # WATCHED inbox, and the watcher's own `SERVICE_PRINCIPAL` actor
        # is exempt from this same check (`_acts_for_the_box`) -- refused
        # bytes left behind would be picked up and re-indexed in place on
        # the very next poll tick, silently doing what this branch just
        # refused to do.
        dest.unlink(missing_ok=True)
        return StageOutcome(name=name, kind="refused", reason=str(exc))
    except Exception:  # noqa: BLE001 -- log detail, skip this file, let the caller's loop continue
        logger.exception("%s: failed to queue %s for ingest", log_label, name)
        return StageOutcome(name=name, kind="failed")

    if job_id is not None:
        return StageOutcome(
            name=name, kind="queued", document=document_at_path(dest),
            is_tabular=ext in TABULAR_EXTS,
        )
    if dest_unchanged:
        # `stage_document` (via `enqueue_ingest` returning `None`) never
        # touches `dest` at all for an unchanged re-upload, so the
        # full-size temp copy written above is otherwise left stray.
        dest.unlink(missing_ok=True)
        return StageOutcome(name=name, kind="unchanged", document=existing)
    # Staged but the enqueue itself was rejected (`_enqueue_ingest_job`
    # already wrote an honest FAILED row + status_detail for it) -- not
    # "unchanged", a genuine failure to queue.
    return StageOutcome(name=name, kind="failed", document=document_at_path(dest))


def enqueue_reingest(doc: Document, *, actor) -> int | None:
    """Re-queue an already-staged `doc` (its Document row and managed-store
    copy already exist) for another `rag.ingest` pass -- the retry-button
    entry point (`tools.rag.views.document_reingest`), for a FAILED (or
    a repeat of a READY) document. Unlike `enqueue_ingest`, there is no
    file to stage/move/dedup here: this re-hashes the RETAINED store copy
    (never the original upload/watch-folder location, which may no longer
    exist -- ADR 0009's "the box owns its data") for the payload's sha, and
    resets `doc` to `status=PENDING` (clearing any prior `status_detail`)
    before enqueuing, so the library UI reads "queued again" rather than
    the stale FAILED/READY chip for the window before the job actually
    starts.

    `actor` is REQUIRED and keyword-only, the same rule and the same
    reason `enqueue_ingest`'s own carries (the acting rule)
    -- its one caller, `tools.rag.views.document_reingest`, supplies
    `identity.request.principal_for_request(request)`.

    Raises `FileNotFoundError` if the store copy is missing -- a
    data-integrity problem distinct from "the queue is down", left to the
    caller (the view) to turn into its own operator-facing message, since
    there's no Document-row failure state that fits it (the row isn't
    "queued and failed", it has nothing left to ingest FROM). Otherwise see
    `_enqueue_ingest_job` for the queue-failure/`None` case.
    """
    stored_path = Path(doc.source_path)
    if not stored_path.is_file():
        raise FileNotFoundError(f"Stored copy for Document {doc.id} is missing: {stored_path}")

    doc.file_hash = _sha256(stored_path)
    doc.status = Document.Status.PENDING
    doc.status_detail = ""
    doc.save(update_fields=["file_hash", "status", "status_detail", "updated_at"])

    return _enqueue_ingest_job(doc, actor)


def ingest_path(path: str, category: str | None = None) -> Document:
    """Ingest a single file at `path` (prose or tabular) and return the
    created/updated Document row -- the CLI's own entry point
    (`manage.py ingest`), composing STAGE (a copy, `move=False`) and RUN
    inline, synchronously (module docstring).

    Idempotent: dedup/re-ingest is keyed on `original_path` (the path
    ingest was given), since `Document.source_path` now points at the
    managed-store copy (ADR 0009), not the original location. If the file's
    content hasn't changed since the last ingest (same SHA-256), the
    existing Document is returned unchanged. If it has changed, prior
    chunks/rows/stored files are deleted and the *same* Document row is
    re-populated (its id, and therefore its store directory, stays stable).

    `category`: an optional category name. When given (non-blank after
    stripping), the Category is get-or-created and assigned; otherwise the
    Document's category is left/set to None ("Uncategorized").

    A RUN-half failure is written to the Document row as
    `status=FAILED`/`status_detail=str(exc)` (via `run_ingest_or_fail`) and
    then RE-RAISED, so `manage.py ingest`'s own per-file try/except still
    reports it exactly as before -- but the row itself is never left
    stranded at PROCESSING (see `run_ingest_or_fail`'s docstring for why
    that matters: nothing else can ever move a PROCESSING row forward).

    `page_scan_max_examined=None` (H28 review round 1, finding 1) --
    explicitly UNBOUNDED, unlike the queued job's own default: this is the
    CLI, a synchronous, operator-invoked process with no other job sharing
    its worker slot -- an operator who runs `manage.py ingest` on a huge
    all-text PDF has already chosen to wait as long as an honest page-cap
    answer takes.
    """
    doc, changed = stage_document(path, category, move=False)
    if not changed:
        return doc
    run_ingest_or_fail(doc, doc.file_hash, page_scan_max_examined=None)
    return doc


# --- watch_folder: enqueue-on-quiescence (module docstring) -----------------

# Internal cadence, not an operator-facing tuning knob (matches
# `models/queue/worker.py`'s own precedent for this kind of constant): how
# long a tracked file's size must sit unchanged before the quiescence
# thread treats a drop as "finished writing" and enqueues it. Long enough
# that an ordinary multi-second copy/upload into the watched folder doesn't
# get enqueued mid-write; short enough that a genuinely-finished drop isn't
# left sitting for long.
STABLE_AFTER_SECONDS = 5

# How often the quiescence thread wakes to re-check `pending` -- also
# internal cadence, not a knob.
_POLL_INTERVAL_SECONDS = 1.0


def _is_regular_file_no_follow(path: Path) -> bool:
    """Whether `path` is a REGULAR file, decided from a single no-follow
    `stat` rather than `Path.is_file()` (B-3, round-3 hardening).

    `Path.is_file()` FOLLOWS symlinks -- the inbox is a host directory
    this platform does not own, so a link planted there names a file
    somewhere else entirely, and `_track`/`poll_once` below used to treat
    that target's size as the watched file's own. Mirrors the refusal
    shape `tools.vision.maintenance._list_dir` already uses for its own
    host-directory listing (`entry.is_file(follow_symlinks=False)`) --
    one `stat(..., follow_symlinks=False)` call rather than `is_file()`'s
    own. `S_ISREG` alone already rules a symlink out (the type bits
    `st_mode` carries are mutually exclusive -- an entry cannot be both a
    regular file and a symlink) and rules out a FIFO/socket/device node
    dropped in the inbox the same way, rather than leaving either for a
    downstream reader to choke on.

    Returns `False` (never raises) for a path that has already vanished
    by the time this stats it -- exactly the `is_file()` contract both
    call sites already relied on.
    """
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode)


class _IngestEventHandler(FileSystemEventHandler):
    """watchdog handler for `watch_folder`: records every created/modified
    file's `(size, monotonic-timestamp)` into `pending` -- it never
    enqueues anything itself. `watch_folder`'s own quiescence thread
    (`poll_once`, called on a timer) is what actually decides a file has
    stopped changing and calls `enqueue_ingest`.

    Derives each file's category from its immediate subfolder under the
    watched `root` (ADR 0009) -- e.g. `<root>/medical/x.pdf` gets category
    "medical"; a file dropped directly in `root` gets Uncategorized.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        # path -> (size last seen, monotonic time it was last seen AT that
        # size). Popped (never left to grow unbounded -- the pre-T2 debounce
        # dict's own nit) by `poll_once` once a file is actually enqueued,
        # or once it's found to have vanished.
        self.pending: dict[str, tuple[int, float]] = {}
        # path -> size already logged as over `RagSettings.max_upload_bytes`
        # (T4) -- suppresses re-logging the SAME size every poll tick while
        # an oversized file sits untouched in the inbox (mirrors `pending`'s
        # own dict shape, same "small seen-set" idea). Cleared whenever that
        # path's observed size changes (poll_once's size-change branch) or
        # the file vanishes, so a genuinely new size -- shrunk under the
        # cap, grown further over it, or a replaced file -- always gets its
        # own honest log line rather than being silently swallowed by a
        # stale entry.
        self._logged_oversized: dict[str, int] = {}
        # path -> size already logged as over `RagSettings.max_media_seconds`
        # (T7 review m2) -- the duration-cap sibling of `_logged_oversized`
        # above, same shape and same clearing rules, keyed on SIZE (not a
        # probed duration) because size is what `poll_once` is already
        # tracking for quiescence -- reusing it avoids a second `ffprobe`
        # call purely to decide whether to re-log.
        self._logged_over_duration: dict[str, int] = {}

    def _track(self, src_path: str) -> None:
        path = Path(src_path)
        if not _is_regular_file_no_follow(path):
            if path.is_symlink():
                # B-3 review round 1 minor: named, not silent -- an
                # operator watching the log sees exactly which drop was
                # refused and why, the same courtesy the unsupported-
                # extension branch just below already gives.
                logger.info("watch_folder: skipping %s -- a symlink, not a regular file", src_path)
            return
        ext = path.suffix.lower()
        if ext not in supported_exts():
            logger.info("watch_folder: ignoring %s (unsupported extension %r)", src_path, ext)
            return
        try:
            size = path.stat().st_size
        except OSError:
            return
        self.pending[src_path] = (size, time.monotonic())

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._track(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._track(event.src_path)

    def poll_once(self) -> None:
        """One size-quiescence pass over `pending`: re-stat every tracked
        file. A size that moved since it was last checked refreshes that
        entry's `(size, now)` (the clock resets); a size that's held
        unchanged for >= `STABLE_AFTER_SECONDS` is checked against
        `RagSettings.max_upload_bytes` (T4, read ONCE per `poll_once` call --
        see the local `cap` variable below -- not once per pending file)
        before anything else happens to it:

        - over the cap: left in `pending` untouched (NOT popped, NOT
          enqueued) so a future size change (a shrink, or the operator
          replacing the file) gets re-evaluated on a later tick -- logged
          once per distinct oversized size (`_logged_oversized`), never
          silently and never every poll.
        - at or under the cap: popped and enqueued
          (`enqueue_ingest(..., move=True)`, wrapped in a never-kill-the-
          watcher try/except -- matches the pre-T2 debounce handler's own
          "log and keep watching" contract), exactly as before T4.

        A video/audio file that clears the BYTE cap above but is over
        `RagSettings.max_media_seconds` (T7 review m2) raises
        `tools.rag.ingest.MediaDurationExceededError` from `enqueue_ingest`
        itself (staging probes the duration) -- caught here and given the
        SAME "leave it, log once per distinct state" treatment as the
        byte-cap branch above, not the generic except-and-forget-it below:
        left in `pending` (not popped), logged once per distinct
        `(path, size)` pairing (`_logged_over_duration`). Once that verdict
        is known for a given `(path, size)`, the enqueue attempt itself is
        SKIPPED on every later tick at that same size (T7 round-3 review
        MINOR) -- probing duration means shelling out to `ffprobe`, unlike
        the byte cap's free `stat()` comparison, so re-attempting a known-
        over-cap file every second forever was a real, unbounded subprocess
        cost. Raising the cap on a PARKED file therefore takes effect on
        that file's next size change or re-drop, not the next poll tick --
        the same two remedies `duration_cap_message`'s own copy already
        names ("raise the cap ... or trim the file").

        A vanished file (deleted/moved away before it ever stabilized) is
        dropped silently, per the `is_file` guard.

        Factored out as its own method -- called on a timer by the daemon
        thread `watch_folder` starts -- rather than inlined in a bare
        `while True` loop, so a test can drive exactly one deterministic
        pass instead of waiting on a real background thread's real sleep.
        """
        now = time.monotonic()
        # One RagSettings read per poll_once call (T4 review MINOR), not one
        # per pending file -- poll_once already runs on a 1-second timer
        # (_POLL_INTERVAL_SECONDS), so a per-file read would be N DB round
        # trips a second for N pending files, for a value that cannot have
        # changed mid-pass.
        cap = RagSettings.get_solo().max_upload_bytes
        for src_path in list(self.pending):
            size, last_changed = self.pending[src_path]
            path = Path(src_path)
            if not _is_regular_file_no_follow(path):
                # B-3: also catches a race where the file `_track` saw was
                # replaced by a symlink before this tick -- same refusal,
                # same "vanished" bookkeeping as an outright deletion.
                self.pending.pop(src_path, None)
                self._logged_oversized.pop(src_path, None)
                self._logged_over_duration.pop(src_path, None)
                continue
            try:
                current_size = path.stat().st_size
            except OSError:
                self.pending.pop(src_path, None)
                self._logged_oversized.pop(src_path, None)
                self._logged_over_duration.pop(src_path, None)
                continue

            if current_size != size:
                self.pending[src_path] = (current_size, now)
                self._logged_oversized.pop(src_path, None)
                self._logged_over_duration.pop(src_path, None)
                continue
            if now - last_changed < STABLE_AFTER_SECONDS:
                continue

            if current_size > cap:
                if self._logged_oversized.get(src_path) != current_size:
                    self._logged_oversized[src_path] = current_size
                    logger.error(
                        "watch_folder: %s is %s, over the %s upload cap "
                        "(RagSettings.max_upload_bytes) -- left in the inbox, not queued; "
                        "raise the cap in RAG settings, or replace the file with a smaller one",
                        src_path, human_bytes(current_size), human_bytes(cap),
                    )
                continue

            if self._logged_over_duration.get(src_path) == current_size:
                # T7 round-3 review MINOR (probe-reproduced: a parked
                # over-duration file forked `ffprobe` once per poll tick
                # forever -- 86k/day). The verdict for THIS file at THIS
                # exact size is already known -- skip the enqueue attempt
                # (and the `ffprobe` subprocess inside `stage_document`'s
                # duration check) entirely rather than re-probing every
                # tick. Takes effect again the moment the file's SIZE
                # changes (the size-change branch above re-evaluates from
                # scratch) or it's removed and re-dropped -- an operator
                # who raises `RagSettings.max_media_seconds` while a file
                # sits parked at an unchanged size needs to touch/re-drop
                # it to force a fresh probe, matching what
                # `tools.rag.media.duration_cap_message`'s own copy
                # already tells them ("raise the cap ... or trim the
                # file" -- either action changes the file, which is what
                # actually clears this).
                continue

            category = category_from_subfolder(path, self._root)
            try:
                # THE ONE SERVICE PRINCIPAL every shell/background path
                # acts as (spec section 5.3 item 8) -- the watcher has no
                # request and no operator behind it, so it stamps its own
                # actor rather than inheriting one.
                enqueue_ingest(src_path, category, move=True, actor=SERVICE_PRINCIPAL)
            except MediaDurationExceededError as exc:
                # Same treatment as the byte-cap branch above (T7 review
                # m2): NOT popped (a cap raise, or the operator swapping in
                # a shorter file, gets picked up on a later poll), and NOT
                # a bare traceback -- `exc`'s own message
                # (`tools.rag.media.duration_cap_message`) already names
                # the limit and the fix. Keyed on (path, size), like
                # `_logged_oversized` -- see `_logged_over_duration`'s own
                # comment for why size, not a probed duration.
                if self._logged_over_duration.get(src_path) != current_size:
                    self._logged_over_duration[src_path] = current_size
                    logger.error("watch_folder: %s -- %s", src_path, exc)
                continue
            except Exception:
                logger.exception("watch_folder: failed to enqueue %s", src_path)

            self.pending.pop(src_path, None)
            # Clear any stale over-cap log-suppression entry (T4 review
            # MINOR): the operator may have raised the cap since this path
            # was last logged as oversized at this exact size -- without
            # this pop, a LATER re-drop of a file at that same stale size
            # would wrongly stay suppressed even though it's a fresh event.
            self._logged_oversized.pop(src_path, None)
            self._logged_over_duration.pop(src_path, None)


def watch_folder(path: str) -> None:
    """Watch `path` (recursively) for new/changed files and enqueue each
    one for `rag.ingest` once it stops changing (size-quiescence, see
    `_IngestEventHandler.poll_once`), auto-assigning each file's category
    from its immediate drop-subfolder under `path` (ADR 0009; see
    `category_from_subfolder`). Runs until interrupted (Ctrl+C) — intended
    for use from a long-lived management command (`manage.py ingest_watch`).
    """
    folder = Path(path).resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    handler = _IngestEventHandler(folder)
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=True)
    observer.start()

    stop_event = threading.Event()

    def _quiescence_loop() -> None:
        while not stop_event.is_set():
            time.sleep(_POLL_INTERVAL_SECONDS)
            handler.poll_once()

    quiescence_thread = threading.Thread(target=_quiescence_loop, name="ingest-quiescence", daemon=True)
    quiescence_thread.start()

    logger.info("watch_folder: watching %s for changes", folder)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("watch_folder: stopping (interrupted)")
    finally:
        stop_event.set()
        observer.stop()
        observer.join()
