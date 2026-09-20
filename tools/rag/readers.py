"""
Small helper module for loading source files into LlamaIndex `Document`
objects (prose) or `pandas.DataFrame`s (tabular).

Kept separate from `tools/rag/ingest.py` so the "how do I read *this* file
format" concerns don't clutter the ingest pipeline / idempotency logic.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pandas as pd
from llama_index.core import Document as LlamaDocument

logger = logging.getLogger(__name__)


class ArchiveExpansionExceededError(ValueError):
    """B-6 (round-3 hardening H34): `_read_docx`/`read_tabular_dataframe`'s
    refusal for a ZIP-format document (`.docx`/`.xlsx`) whose declared
    uncompressed size is unreasonable relative to what was actually
    uploaded -- either a single member's compression ratio, or the total
    uncompressed size, over the ceilings below. A `ValueError` subclass,
    the same precedent `tools.rag.ingest.DocumentPageCapExceededError`
    set (that class's own docstring): every existing `except ValueError`
    call site still catches this exactly as before, while a caller that
    wants to treat "archive expands too far" differently from every other
    read failure can catch this name specifically."""

# Single source of truth for which file extensions this module can read and
# how they're routed (prose -> LlamaIndex Documents, tabular -> DataFrame).
# ingest.py imports these to decide doc_type and to filter watch-folder events,
# so "what we support" lives in exactly one place.
PROSE_EXTS = {".pdf", ".txt", ".md", ".docx"}
TABULAR_EXTS = {".csv", ".xlsx"}

# Media-ingestion registry groundwork (media-into-RAG plan, T1;
# ADR 0014) -- same single-source-of-truth treatment as PROSE_EXTS/
# TABULAR_EXTS above, extended to the media mediums `medium_for` routes.
# Wired into `tools.rag.ingest.supported_exts()` behind the "media"
# feature flag -- see that function's own docstring for the gate.
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
AV_EXTS = VIDEO_EXTS | AUDIO_EXTS
MEDIA_EXTS = AV_EXTS | IMAGE_EXTS


def read_prose_documents(path: Path) -> list[LlamaDocument]:
    """Load `path` (.pdf/.txt/.md/.docx) into one or more LlamaIndex
    `Document` objects, ready to be handed to a node parser/splitter.

    PDFs yield one `Document` per non-empty page (with a `page` metadata
    key) so citations can eventually point at a page number; other formats
    yield a single `Document` for the whole file.
    """
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _read_pdf(path)
    if ext in (".txt", ".md"):
        return _read_text(path)
    if ext == ".docx":
        return _read_docx(path)
    raise ValueError(f"Unsupported prose extension: {ext}")


def _read_pdf(path: Path) -> list[LlamaDocument]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    docs: list[LlamaDocument] = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if not text:
            continue
        docs.append(LlamaDocument(text=text, metadata={"page": i + 1}))
    if not docs:
        logger.warning(
            "readers: no extractable text found in PDF %s (a scanned/image-only PDF is routed "
            "through vision extraction by tools.rag.ingest's own detection when the 'media' "
            "feature is enabled, never read here -- this module stays a pure text-layer parser)",
            path,
        )
    return docs


def _read_text(path: Path) -> list[LlamaDocument]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return [LlamaDocument(text=text)]


# B-6 (round-3 hardening H34): both `.docx` and `.xlsx` are ZIP
# containers, and both libraries this module hands them to materialize
# the FULL uncompressed member (python-docx's XML parse; pandas' openpyxl
# engine, before this task, opened without read-only streaming). A
# crafted archive with an unremarkable-looking compressed size can
# therefore expand to gigabytes in memory before either library ever
# gets to say the content itself is malformed -- and the only size gate
# earlier in the pipeline (`RagSettings.max_upload_bytes`) is a
# *compressed*-size cap on the bytes the browser actually sent, which a
# high-ratio archive sails under while its EXPANSION does not (see
# `tools/rag/README.md` and `docs/OPERATIONS.md` for the operator-facing
# account of that distinction).
#
# Two independent ceilings, either one enough to refuse -- picked so an
# ordinary office document (typical XML/text compression, low tens of MB
# even for a large legitimate file) is nowhere near either one, while a
# crafted archive trips at least one of them:
#   - MAX_UNCOMPRESSED_RATIO: uncompressed / compressed, per archive
#     member. 200:1 is already generous headroom over the ~5-20:1 an
#     ordinary prose/tabular XML member reaches, and far below the
#     100:1-1000:1+ a repetitive/crafted member reaches easily (the
#     finding's own numbers).
#   - MAX_UNCOMPRESSED_BYTES: an absolute ceiling on the SUM of every
#     member's declared uncompressed size, regardless of ratio -- catches
#     a large, only mildly-compressible archive the ratio check alone
#     would miss (a low ratio, but still an unreasonable expansion).
MAX_UNCOMPRESSED_RATIO = 200
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MiB

# H34 follow-up (a narrow false positive in the round-3 report): the
# `.xlsx` members openpyxl's `read_only` mode NEVER OPENS. That mode
# streams the worksheet XML and reads the workbook, shared-strings,
# styles and relationship parts; `xl/media/*` (embedded pictures) and
# `xl/drawings/*` (their anchors and shapes) are parts only the drawing
# model needs, and read-only mode builds no drawing model. Summing them
# refused a legitimate workbook carrying a few hundred megabytes of
# photographs for an expansion that never happens.
#
# A PREFIX LIST, NOT AN ALLOW-LIST OF THE PARTS THAT ARE READ: an
# unrecognized member still counts. A part this list has never heard of
# is a part that might be loaded, and the guard must fail towards
# refusing.
XLSX_UNOPENED_MEMBER_PREFIXES = ("xl/media/", "xl/drawings/")


def assert_archive_is_sane(path: Path, *, unopened_prefixes: tuple[str, ...] = ()) -> None:
    """Refuse `path` (a ZIP-format container -- `.docx`/`.xlsx`) whose
    declared uncompressed size is unreasonable, BEFORE any caller hands
    it to a parser that would actually expand it.

    Reads ONLY the central directory (`zipfile.ZipInfo.file_size`/
    `compress_size` -- the declared sizes a ZIP's own index carries, read
    without decompressing a single byte of any member) -- never
    `.read()`/`.extract()`s a member. A guard that decompressed to
    measure would BE the bomb it exists to refuse.

    `unopened_prefixes` NARROWS THE SUM, and only the sum: members whose
    name starts with one of these are left out of the
    `MAX_UNCOMPRESSED_BYTES` total. `.docx` passes NOTHING here on
    purpose -- python-docx builds the whole OPC package (document part,
    styles, relationships, embedded media, ...) to make its in-memory
    model, so every member really is loaded and the whole archive is the
    honest membership to bound. The `.xlsx` caller passes
    `XLSX_UNOPENED_MEMBER_PREFIXES`, because openpyxl's `read_only` mode
    genuinely never opens those parts (see that constant's own comment).

    THE PER-MEMBER RATIO CEILING STAYS WHOLE-ARCHIVE regardless: a member
    declaring 200x its compressed size is a crafted member wherever it
    sits, and a real picture or drawing part -- already compressed
    image bytes, or a few kilobytes of XML -- never approaches it. Only
    the absolute-bytes sum, which is what a large legitimate media part
    actually trips, is narrowed.

    Raises `ArchiveExpansionExceededError` (named in the message) for the
    first member over `MAX_UNCOMPRESSED_RATIO`, or for the summed total
    over `MAX_UNCOMPRESSED_BYTES`.
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()

        for info in infos:
            if info.compress_size and info.file_size / info.compress_size > MAX_UNCOMPRESSED_RATIO:
                raise ArchiveExpansionExceededError(
                    f"{path.name}: member {info.filename!r} compresses too far "
                    f"({info.file_size} declared uncompressed bytes from "
                    f"{info.compress_size} compressed -- over the "
                    f"{MAX_UNCOMPRESSED_RATIO}:1 ratio ceiling) -- refused before parsing"
                )

        total_uncompressed = sum(
            info.file_size for info in infos
            if not info.filename.startswith(unopened_prefixes)
        )
        if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
            raise ArchiveExpansionExceededError(
                f"{path.name}: declares {total_uncompressed} uncompressed bytes total, "
                f"over the {MAX_UNCOMPRESSED_BYTES}-byte ceiling -- refused before parsing"
            )


def _read_docx(path: Path) -> list[LlamaDocument]:
    assert_archive_is_sane(path)
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "python-docx is not installed; .docx ingestion is unavailable in "
            "this environment. Install the `python-docx` package to enable it."
        ) from exc

    d = docx.Document(str(path))
    text = "\n".join(p.text for p in d.paragraphs if p.text.strip())
    return [LlamaDocument(text=text)]


# B-6 (round-3 hardening H34): a `.csv` is not an archive -- there is no
# declared-vs-actual size to compare, since nothing is compressed -- but
# an unbounded row count is its own way to turn a small file into an
# outsized in-memory cost: `pandas.read_csv` builds one Python object per
# cell, and `_ingest_tabular` (tools/rag/ingest.py) round-trips the whole
# frame through `DataFrame.to_json(orient="records")` before writing one
# `DocumentRow` per row -- both costs scale with row count, not file
# bytes. Counting rows first is the CSV analogue of reading a ZIP's
# central directory above: cheap (one streamed pass, O(1) memory, no
# DataFrame built) and paid BEFORE the expensive parse.
MAX_CSV_ROWS = 1_000_000


def _count_csv_rows(path: Path) -> int:
    """Stream `path` line by line and return the number of DATA rows
    (the header line excluded). O(1) memory regardless of file size --
    Python's own file iteration is buffered, not a whole-file read.

    A quoted field embedding a literal newline makes this an
    OVERCOUNT of the CSV's true logical row count (it counts physical
    lines, not logical records) -- the safe direction for a refusal
    bound: it can only refuse a legitimate file early, never admit an
    oversize one by undercounting.
    """
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        # `max(0, ...)`: an EMPTY file has no header line to subtract, and
        # "-1 rows" is not a fact about any file. Nothing downstream
        # refused on it (the ceiling is an upper bound), but this is the
        # number `read_tabular_dataframe` compares, and a count is a
        # count.
        return max(0, sum(1 for _ in f) - 1)


def read_tabular_dataframe(path: Path) -> pd.DataFrame:
    """Load `path` (.csv/.xlsx) into a `pandas.DataFrame`."""
    ext = path.suffix.lower()
    if ext == ".csv":
        rows = _count_csv_rows(path)
        if rows > MAX_CSV_ROWS:
            raise ArchiveExpansionExceededError(
                f"{path.name}: {rows} rows, over the {MAX_CSV_ROWS}-row ceiling -- "
                "refused before parsing"
            )
        return pd.read_csv(path)
    if ext == ".xlsx":
        assert_archive_is_sane(path, unopened_prefixes=XLSX_UNOPENED_MEMBER_PREFIXES)
        # Read worksheet data in openpyxl's streaming mode where pandas
        # allows it (B-6 addendum) -- `read_only=True` avoids building
        # openpyxl's own full in-memory workbook model on top of whatever
        # `pd.read_excel` itself materializes, without changing the
        # `defusedxml`-backed XXE guard `test_readers_xxe.py` pins (that
        # guard runs at the XML-parse layer, both modes go through it).
        return pd.read_excel(path, engine="openpyxl", engine_kwargs={"read_only": True})
    raise ValueError(f"Unsupported tabular extension: {ext}")


def medium_for(ext: str) -> str:
    """Return which medium `ext` (an extension like ".pdf", case
    insensitive) belongs to: "prose", "tabular", "video", "audio", or
    "image".

    The one place `tools.rag.ingest._doc_type_for_medium` (the successor
    to a since-retired `_doc_type_for`/`SUPPORTED_EXTS` pair) reads a medium
    from an extension -- see that function's own docstring for how it maps
    this function's result onto `Document.DocType`. Raises `ValueError` for
    an unrecognized extension.
    """
    ext = ext.lower()
    if ext in PROSE_EXTS:
        return "prose"
    if ext in TABULAR_EXTS:
        return "tabular"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    supported = sorted(PROSE_EXTS | TABULAR_EXTS | MEDIA_EXTS)
    raise ValueError(f"Unsupported file extension: {ext!r} (supported: {supported})")


# H28 review round 1, finding 1: the module constant a JOB-START caller
# (never a request-thread one -- see `pdf_textless_pages`'s own docstring)
# passes as `max_examined` to bound the worst-case scan below. Picked
# against `tools.rag.models.RagSettings.MAX_DOCUMENT_PAGES_DEFAULT` (500,
# a count of TEXTLESS pages, not pages examined): 2,000 is 4x that default,
# generous headroom for a legitimately large all-text document (the common
# case a bound like this must never wrongly refuse) while still bounding a
# single worker's worst-case CPU on one job -- ADR 0014 §9's own
# measurement (~1.56ms/page on a synthetic all-text fixture, 7.82s for
# 5,000 pages) puts 2,000 pages at roughly 3s of CPU, not the minutes an
# unbounded pathological upload could otherwise cost one worker slot.
PDF_SCAN_MAX_PAGES_EXAMINED = 2000


def pdf_textless_pages(
    path: Path, *, limit: int | None = None, max_examined: int | None = None
) -> tuple[list[int], bool]:
    """The per-page scanned/mixed-PDF detector (W1, ADR 0014 §18): the
    1-based page numbers of `path` whose `extract_text()` is blank -- one
    `pypdf` pass, no `Document` objects built (this is a scan, not a
    parse). Replaces the whole-file `pdf_has_extractable_text` probe: a PDF
    with SOME text pages and SOME scanned pages needs page-level truth, not
    one boolean for the whole file, so callers can route only the textless
    pages through vision extraction (`tools.rag.media.extract_to_sidecar`)
    while the text pages still come from `read_prose_documents`/`_read_pdf`
    as always (`tools.rag.ingest._source_documents` is the merge point).

    Returns `(pages, truncated)`, ALWAYS -- a 2-tuple, never a bare list
    (H28 review round 1, finding 4). Before this round, this function
    returned a bare list when `max_examined` was omitted and a `(pages,
    truncated)` pair when it was given -- a footgun: `bool(pdf_textless_
    pages(...))` is truthy for ANY non-empty tuple, `(([], False))`
    included, regardless of whether `pages` itself is empty, which is the
    exact opposite of what every caller actually wants to know. One
    caller (`_needs_vision_extraction`, `tools.rag.ingest`) did exactly
    this. A single, unconditional return shape removes the trap instead of
    documenting around it. Every caller unpacks `pages, truncated = ...`
    now, `max_examined` given or not.

    `limit`: stop the scan as soon as `limit` textless pages have been
    COLLECTED -- read that precisely, not "as soon as `limit` pages have
    been scanned". A PDF with NO textless pages is scanned in full at every
    positive `limit`, `limit=1` included: proving "no page lacks a text
    layer" requires looking at every page, since the textless page this
    call is watching for could be the very last one. This is deliberate,
    not an oversight -- see "The scan cost, stated plainly", ADR 0014 §9:
    an ordinary all-text PDF now costs a full `extract_text()` pass where
    the retired whole-file probe returned at page 1, and there is no
    honest way to bound that cost without silently mis-routing a page
    (assume-has-text reintroduces the exact defect this function exists to
    close; assume-textless rasterizes pages that never needed it).

    `max_examined` (B-5, round-3 hardening H28) bounds pages EXAMINED --
    not merely pages COLLECTED, which `limit` already does and which does
    nothing for the worst case above (an all-text PDF collects nothing, so
    `limit` alone never stops that scan early). `truncated` is `True` when
    the scan stopped because it had examined `max_examined` pages, not
    because it ran out of document or hit `limit`. A caller that gets
    `truncated=True` back has an INCONCLUSIVE answer -- the page cap
    decision cannot honestly be made from it (an all-text PDF and a PDF
    whose first textless page sits at `max_examined + 1` look identical up
    to that point) -- and must refuse honestly (H28 review round 1,
    finding 1) rather than silently guessing either direction; see
    `tools.rag.ingest._check_document_pages`'s own docstring for how the
    ONE caller that passes a real ceiling (`PDF_SCAN_MAX_PAGES_EXAMINED`,
    above) handles that. `max_examined=None` (the default) is UNBOUNDED --
    never truncates, same cost as before this parameter existed. The
    queued job passes the module constant above; the CLI's synchronous
    `ingest_path` passes `None` explicitly and says so (it runs neither on
    a request thread nor inside a shared worker pool, so an operator
    running it from a terminal is already choosing to wait as long as the
    file honestly needs) -- see `run_ingest_for`'s own docstring for both.

    `limit <= 0` (W1 review MINOR 6) returns `([], False)` without scanning
    a single page or even opening `path` -- "collect at most zero" has
    exactly one honest answer, the empty list, collected immediately.
    Before this, the loop below appended the FIRST textless page it found
    (there being nothing to distinguish "0 pages collected" from "collect
    0, then stop" in `len(textless) >= limit`'s own comparison) before
    ever checking the limit -- a genuine, if unlikely, caller bug: no real
    caller passes `limit=0` on purpose, but a computed cap of `0` (e.g. a
    `RagSettings.max_document_pages` of `0`, or a caller's own `cap - 1`
    arithmetic landing on zero) must never silently behave like `limit=1`
    instead. `truncated` is `False` here regardless of `max_examined`,
    since nothing was examined at all, never mind cut off mid-scan.

    Matches `_read_pdf`'s own lazy `from pypdf import PdfReader` below
    (module docstring: "kept separate ... so ... concerns don't clutter"
    -- readers.py stays import-cheap for callers that never touch a PDF).
    """
    if limit is not None and limit <= 0:
        return [], False

    # Lazy, matching `_read_pdf`'s own import above this function -- see
    # this function's closing paragraph.
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    textless: list[int] = []
    truncated = False
    for i, page in enumerate(reader.pages):
        if max_examined is not None and i >= max_examined:
            truncated = True
            break
        text = (page.extract_text() or "").strip()
        if text:
            continue
        textless.append(i + 1)
        if limit is not None and len(textless) >= limit:
            break
    return textless, truncated
