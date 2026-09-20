# tools/rag/ — Offline RAG (first module)

**Drop documents in → the system becomes a queryable, cited knowledge source, fully offline.**

This is Farabunker's first module and the Phase 1 walking skeleton. It rebuilds a proven
*agentic hybrid RAG* pattern natively in Python — **no n8n, no cloud services**.

## What it's for

The same module, under different [posture profiles](../../docs/ARCHITECTURE.md#2-security-posture-the-offline-spectrum):

- **Resilience knowledge base** — load how-to references (engineering, medical, repair,
  agriculture…) and query them as long as the machine powers on. `airgap`.
- **Sensitive business data** — internal knowledge that must never touch the internet.
  `airgap` or `isolated-lan`.

## Design in brief

Built with **LlamaIndex** on **Django + Postgres/pgvector + Ollama** (see
[ADR 0004](../../docs/adr/0004-application-stack.md) and
[ADR 0005](../../docs/adr/0005-rag-module-architecture.md)). It talks only to core
capabilities (`inference:chat`, `inference:embeddings`, `vector:read/write`,
`storage:documents`) and is `network: none`.

**Ingest** (out-of-band — from a watch folder, a browser upload, or `manage.py ingest`;
the first two *stage* the file and hand the work to the execution queue as a
`rag.ingest` job, ADR 0013/ADR 0014):

```
file ─▶ into managed store (data/documents/<id>/) ─▶ assign category ─▶ detect medium
        ├─ prose (PDF/txt/md/docx) ────────────────────────▶ chunk ─▶ embed ─▶ pgvector
        ├─ video/audio ─▶ transcribe (host engine) ─▶ extract.json ─▶ chunk ─▶ embed ─▶ pgvector
        ├─ image / scanned or mixed PDF ─▶ vision extract ─▶ extract.json ─▶ chunk ─▶ embed ─▶ pgvector
        └─ tabular (CSV/XLSX) ─────────────────────────────▶ rows ─▶ Postgres table (+ schema)
                                                        (+ document metadata for all branches)
```

`extract.json` and a driver's scratch `work/` directory both live under the same
per-document store directory as the source file; `tools/rag/store.py`'s `sidecar_path`,
`sidecar_path_for_dir` (the same lookup keyed on a directory rather than a document id,
for the one caller that already has an on-disk path), and `work_dir` helpers are the one
place that layout is decided.

Tabular data is kept as **SQL rows, not embeddings**, so aggregate/numeric questions stay
accurate — the key accuracy choice. It is, however, **not searchable today**: the
text-to-SQL router remains Phase 2 work (see below). This is said plainly everywhere an
operator would notice a tabular file (W2, ADR 0014 §18): the Document library's "Table"
source badge carries an honest sub-line ("Stored — rows are not searchable yet."), the Ask
page shows a one-line note counting how many table files aren't searched whenever the
library holds any, and a tabular upload's own success flash names the same fact
per-file.

The media branches (video, audio, images, scanned/mixed PDFs) are gated by the `"media"` flag
in `FARABUNKER_FEATURES`, which controls **new intake only** — already-ingested media keeps
working with the flag off. A **mixed** PDF (some pages have a text layer, some don't) routes
through vision extraction for its textless pages only — its ordinary text pages are read
normally and merged back in at index time; with no vision model bound it still ingests its
text layer and says so on its Ready chip. See [ADR 0014](../../docs/adr/0014-media-ingestion.md)
§18 for the mechanism, and its Re-ingest note below for a PDF ingested before this shipped.

**Operator note (pre-W1 mixed PDFs):** a mixed PDF ingested before per-page routing shipped
(2026-08-24) has no extraction sidecar at all and no amount of re-encoding will create one —
its scanned pages are still missing from the index. Press **Re-ingest** in the document
library to re-ingest it (this actually runs extraction; a bare re-encode does not) — the
document is already READY, so that's the label the library's Retry form shows for it.

**Managed store & categories** (ADR 0009): every ingested file ends up at
`data/documents/<document_id>/<basename>` — the box owns a durable copy, independent of the
watch-folder/upload path it came from. Two verbs, and the caller chooses:
`manage.py ingest` **copies** (never move an operator's own file), while the watch folder
and the browser upload **move** it out of the inbox, so a file is never stored twice
(ADR 0014 §5). `Document.original_path` is what dedup/re-ingest keys on, not the (now
store-owned) `source_path`.

**The watch inbox is a host directory this platform does not own (B-3, round-3
hardening).** `manage.py ingest_watch` polls it, but the box has no exclusive claim on it the
way it does on the managed store — on a single-owner box with no file share, writing into it
is operator trust; the moment it is exposed as a drop folder (a network share, a shared
volume) that trust boundary is gone, and this platform treats it accordingly. The watcher
therefore indexes **regular files only**: a symlink dropped in the inbox is skipped outright,
never followed — `tools.rag.ingest._is_regular_file_no_follow` stats each entry with
`follow_symlinks=False` rather than `Path.is_file()` (which follows), mirroring the same
refusal `tools/vision/maintenance.py`'s own host-directory listing already applies. A resolved
path reaching `stage_document` from the watcher or a browser upload that still lands outside
every directory this platform owns (`tools.rag.store.assert_inside_platform_dirs`: the inbox,
the managed store, the chat-attachment staging directory) is refused as a second, independent
check — `manage.py ingest`'s own door is exempt, since an operator naming their own file on the
command line is trusted to name one outside all three on purpose. Both write doors this module
owns (the library upload, and the watcher's own move into the managed store) open their
destination with `foundation.files.create_locked_file`, which now also refuses to write
**through** a pre-planted symlink at that destination (`O_NOFOLLOW`) rather than truncating
whatever it points at.

**Dedup is `(original_path, file_hash)`, but reusing the row for changed bytes is an
authorization decision, not only a bookkeeping one** (round-3 hardening, finding B-2, with a
round-1 review correction below): `data/inbox/<category>/<basename>` carries no per-user
prefix, so one member's filename can collide with another's by construction. Three doors can
re-stage a changed file at an already-staged `original_path`, and two of them are **not a
principal at all** — `manage.py ingest` and the notes-consolidation job (`actor=None`), and
**the watch folder itself, whose real actor is `SERVICE_PRINCIPAL`, not `None`** — both keep
the pre-existing **"same path, new bytes = re-index in place"** semantics unconditionally, even
over a row a browser upload staged first (there is nobody for either to authorize against). The
browser upload door is the one principal-bearing door: it checks the acting principal against
`tools.rag.access.is_owner`/`may_administer_document` first (this refusal only ever fires once
accounts are on — every principal reads as an administrator on an open box, so there is nobody
to refuse) and raises `tools.rag.ingest.StageRefused` — shown to the uploader as a flash, never
a silent overwrite — for anyone but the row's own uploader or an administrator/entitlement
**owner** (never a mere entitlement holder) who may already delete or re-ingest it. A takeover
that IS authorized but is NOT by the row's own uploader also clears the row's entitlement
labels, resets its containment (`workstream`/`scope` back to Universal, its
`DocumentAttachment` rows dropped), and re-stamps `owner_kind`/`owner_key` to the acting
principal, before re-ingesting — otherwise `restamp_document_chunks` would restore the previous
uploader's labels, and the row would keep the previous owner's wall and attribution, on the new
uploader's bytes.

Each `Document` gets an optional `Category` (null = "Uncategorized"). Assignment:

- `manage.py ingest <path> --category NAME` — explicit override for everything ingested by
  that call.
- `manage.py ingest <directory>` (no `--category`) — each **top-level subfolder name** under
  `<directory>` becomes the category for files within it; files directly in `<directory>` are
  Uncategorized.
- `manage.py ingest_watch <root>` — same subfolder rule, applied live as files land under
  `<root>` (e.g. `<root>/medical/x.pdf` → category "medical").

Every chunk node written to pgvector carries a `category` string in its metadata: the
**lowercased** category name, or the literal `"uncategorized"` — this is what retrieval's
category filter matches on.

**Category identity is case- and whitespace-insensitive** — "Medical", "medical", and
" medical " all denote the same shelf, never three separate ones. `tools/rag/categories.py`
is the single place that enforces this:

- `normalize_category_name(raw)` strips/collapses whitespace.
- `get_or_create_category(raw)` resolves a raw name to a `Category` row, reusing any
  case-insensitive match (`name__iexact`) instead of creating a duplicate; new rows keep the
  caller's casing as the row's "pretty" display name, but existing rows are never re-cased.
- Ingest (`_category_label` in `tools/rag/ingest.py`) writes the **lowercased** category
  name (or `"uncategorized"`) onto every chunk's `category` metadata key, and retrieval
  (`answer_question`) lowercases the requested `category` filter value before matching — so a
  scoped question works regardless of the casing typed in the UI or sent to the API.

**Query** (`tools/rag/retrieval.py::answer_question`) — Phase 1 semantic (vector) search
over the pgvector chunks, with optional category scoping:

```
question (+ optional category) ─▶ semantic search (prose, pgvector; filtered by
                                    node metadata "category" if given)
                                 ─▶ synthesize (LLM via Inference Gateway) ─▶ answer + citations
```

`answer_question(question, category=None)` restricts retrieval to chunks
whose `category` node metadata (written by ingest, ADR 0009) equals the given value; `None`
(the default) searches all categories. Conversation memory is not this function's
business -- it lives in `agents.models.Turn` (P2, spec section 7.6). The pgvector store
(`tools.rag.index.get_vector_store`) is built fresh on every call by design (a worker
process may drop and recreate the chunk table between requests) and disposed via
`tools.rag.index.disposing_vector_store()` when the call ends, since the underlying
package's own `close()` is async and unreachable from this synchronous path.

Before `answer_question` runs (or, on the search page, before retrieval runs at all),
`AskView`'s pre-check, `SearchView`'s own embed-role check, and the queued `rag.ask` job's
pre-run re-check share one dedup-by-endpoint health-check probe (`tools.rag.messages.
unreachable_endpoints`) -- but each caller still renders its own message copy, since the
web path, the search path, and the job path intentionally say different things about an
unbound or unreachable role.

**`POST /rag/ask/` (`AskView`) — `priority` is never honoured, for anyone.** The request body
is `{"question", "connection"}`; a `priority` key is still ACCEPTED and still parses as a
whole, positive number for every caller (a malformed value is still an honest 400), but it is
not part of the shape this endpoint offers, because it is then **always dropped** — anonymous,
member, or administrator alike — and the job kind's own default takes over instead: an ignored
field is documented, not silently ignored. Queue priority is queue policy:
`models.contracts.queue.resolve_client_priority` (C-4) is the one place it is derived, and
`AskView` is its only caller today. An administrator who wants `rag.ask` jobs to run at a
different priority uses the Queue page's own settings form instead — that changes the job
kind's default for every job of that kind, audited, rather than one request's own unaudited
say-so. The 202 response still reports the **resolved** priority (`JobStatus.priority`), which
is what a caller may legitimately read back regardless of what it asked for.

**Document library** (`GET /rag/documents/`, `tools/rag/views.py::DocumentsView` +
`tools/rag/templates/rag/documents.html`) — server-rendered, query-param driven, works with
JS disabled:

```
sidebar (browse)                    main pane (search / list)
------------------                  ---------------------------------
All            <total>              [ search titles…            ] [Search]
Medical        <count>
Engineering    <count>              title · category (when shown) · type · download · delete
Reference &..  <count>              ... (DOCUMENTS_PAGE_SIZE=25 per page)
Business       <count>
Uncategorized  <count>              ‹ Prev   Page X of Y   Next ›
```

Three GET params drive everything, each a plain bookmarkable/shareable URL:

- `?category=<name>` — filter the list to that category (`?category=Uncategorized` for
  `Document.category IS NULL`). Selecting a shelf in the sidebar sets this.
- `?q=<term>` — case-insensitive title search (`title__icontains`) across **all** documents,
  regardless of category; each result shows its category. `q` always wins over `category` for
  filtering (a simultaneously-passed `category` is preserved in links but ignored for the
  query), so searching never requires leaving your current shelf first.
- `?page=<n>` — 1-indexed page into the current result set, `DOCUMENTS_PAGE_SIZE` (25) per
  page (`django.core.paginator.Paginator`). Pagination links preserve whatever `q`/`category`
  are active.

The sidebar counts (`All`, each `Category`, `Uncategorized`) are computed with an annotated
`Count` per request, so they always reflect the current library size — no separate cache to
invalidate. Per-document controls: for a `row.readable` document, the title links to
`/rag/documents/<id>/file/`, `?download=1` forces a download, and Delete POSTs to
`/rag/documents/<id>/delete/` (`services.delete_document`, ADR 0009 teardown). A row this
principal may only ADMINISTER, not read — an administrator with `admin_sees_content` off,
over a document labelled with an entitlement they do not hold — renders the title as plain
text and offers no Download/Transcript link at all (IA-2 walkthrough finding W-2: the title
itself used to keep linking at the file route after the fix wave that gated the other two).

**Browser upload** — `POST /rag/documents/upload/` (`tools/rag/views.py::document_upload`)
does not ingest synchronously. It streams each uploaded file into the watch inbox
(`INGEST_INBOX_DIR`, i.e. `data/inbox/`), under a per-category subfolder derived from the
`new_category` text field (falling back to the `category` select, or the inbox root for
"Uncategorized"/blank) — and then **enqueues it directly**, from that request:

```
browser upload ─▶ POST /rag/documents/upload/ ─▶ data/inbox/<category>/<filename>
                                                              │
                                    stage: hash ─▶ dedup ─▶ Document row (PENDING)
                                           ─▶ MOVE into data/documents/<id>/
                                                              │
                                                  enqueue `rag.ingest` job
                                                              │
                                                      worker service ─▶ run half
```

The job id exists immediately and is watchable at `/queue/`; the upload request returns
"Queued N file(s)" rather than waiting on ingest. This is **watcher-independent** — the
`watcher` container still polls the same inbox out-of-band (for files dropped there by
other means), and a double-enqueue is impossible by construction, since staging moves the
file and dedups on `(original_path, file_hash)`. A re-upload byte-identical to what's
already staged at that path queues nothing and is called out honestly as "unchanged"
(W1 review MAJOR 1), never folded into the "Queued N file(s)" count.

**The per-file write/hash-compare/enqueue body (C-14) is `tools.rag.ingest.
stage_and_enqueue_one`**, shared with the chat door's `stage_turn_attachments`
below — the write-to-disk, hash-before-enqueue, and exception ladder (watcher-race,
size/duration caps) used to be two ~90-line copies of the identical loop; now
there is one, returning a `StageOutcome` each caller switches on. `document_upload`
itself keeps only what is genuinely its own: the flash messages for each outcome, and
applying the uploader's chosen labels (`set_document_labels`) to a successfully-queued
document.

**The page cap is a JOB-START decision, not an upload-time one (B-5, round-3
hardening H28).** Before this fix, an uploaded scanned/mixed PDF (or image) over
`RagSettings.max_document_pages` was rejected inline, on the upload request itself —
which meant scanning the file's text layer synchronously on that request thread to
decide. `readers.pdf_textless_pages`'s own docstring records the cost that scan pays
for an ordinary ALL-TEXT PDF (the common case, not the scanned one the cap is even
about): a full `pypdf.extract_text()` pass over every page, since proving "nothing is
textless" means looking at all of them — no timeout, no ceiling on pages examined, one
web worker pinned for as long as the file took. The upload view no longer inspects a
PDF's content at all: every accepted file is staged and queued (an operator sees
"Queued N file(s)", same as any other upload), and the page-cap verdict — using the
SAME operator-facing message as before — is made at the top of the `rag.ingest` job's
own run, off the request thread (the queue worker, or the CLI's synchronous
`manage.py ingest`). The queue worker's own scan is BOUNDED (round 1 review, H28):
`readers.PDF_SCAN_MAX_PAGES_EXAMINED` pages examined, not the "as long as it honestly
needs" the CLI still gets (an operator running a terminal command has already chosen to
wait) — a document the scan cannot resolve within that ceiling is refused honestly,
naming the ceiling and the page count examined, distinct from the ordinary over-cap
message. An over-cap document
therefore now shows up as an ordinary FAILED row, with the cap named in its
`status_detail`, rather than as an immediate upload-time rejection — Retry after
raising the cap works exactly as it does for any other FAILED document.

**A rendered PDF page also has a stated pixel ceiling, next to the page-count cap
above (B-4, round-3 hardening H29; review round 1 findings 1–2).** `RagSettings.
max_document_pages` bounds *how many* textless pages a scanned/mixed PDF may have;
`transcode.MAX_RENDER_PIXELS` (25,000,000 — 25 megapixels) bounds *how large a single
page's rendered bitmap may be*, since neither `pypdfium2` nor this module ever checked
a PDF page's own declared point size (`MediaBox`) against anything before rendering it
— a hand-built page can claim whatever size it likes, and the format's own 14,400-point
(200in) page-size ceiling goes unenforced by the renderer. `transcode.rasterize_pdf_page`
now reads a page's declared size first and chooses a GRADUATED render scale — `min(the
shipped fixed multiplier, sqrt(MAX_RENDER_PIXELS / declared area))` — that shrinks
continuously as the declared page grows, rather than a step function: a very large but
legitimate page (an architectural drawing, a poster) still renders, just at a smaller
scale, landing at (up to rounding) exactly the pixel ceiling instead of the unbounded
allocation the old fixed scale would have asked for. Only once that chosen scale falls
below `transcode.RENDER_SCALE_FLOOR` (0.25× — an already-illegible ~18 DPI) does
`rasterize_pdf_page` refuse outright — `transcode.RenderAreaExceededError`, naming the
page's own declared size, the ceiling, and the floor — before ever calling into
`pypdfium2`; the audit's own 200000×200000-point reproduction is comfortably past that
floor. The same `MAX_RENDER_PIXELS` also becomes Pillow's own `Image.MAX_IMAGE_PIXELS`
(set explicitly at the `_require_pillow` choke point both rasterization and upload
normalization pass through, rather than left at Pillow's own ~89.5-megapixel default),
so an arbitrary uploaded image (`transcode.normalize_image`) is held to the identical
ceiling via Pillow's own decompression-bomb guard. See `docs/OPERATIONS.md`'s media
limits section for the worst-case memory figure behind the 25-megapixel number.

**An archive-backed document also has a stated uncompressed-size ceiling (B-6,
round-3 hardening H34), independent of `RagSettings.max_upload_bytes` above.** That
upload cap is a **compressed**-size cap: it compares `upload.size`, the bytes the
browser actually sent, against its ceiling (2 GiB by default) — it says nothing about
what those bytes expand to once parsed. `.docx` and `.xlsx` are both ZIP containers,
and both `python-docx` and pandas' `openpyxl` engine (before this task, opened without
read-only streaming) materialize a member's FULL uncompressed size in memory — an
ordinary, unremarkable-looking compression ratio easily reaches 100:1 for prose XML
and 1000:1+ for a repetitive worksheet, so a small upload can still expand to
gigabytes. `readers.assert_archive_is_sane` refuses BEFORE either parser ever sees the
file, reading only the ZIP's own central directory (`zipfile.ZipInfo.file_size`/
`compress_size` — the declared sizes, never decompressed to check) against two
ceilings, either one enough to refuse: `readers.MAX_UNCOMPRESSED_RATIO` (200:1, per
archive member, every member, both formats) and `readers.MAX_UNCOMPRESSED_BYTES` (200
MiB, the summed total across the members the parser actually opens — catches a large,
only mildly-compressible archive the ratio check alone would miss). That sum is the
WHOLE archive for a `.docx` (python-docx builds the entire package, embedded media
included) and excludes `xl/media/`/`xl/drawings/` for an `.xlsx`, which openpyxl's
read-only mode never opens — `assert_archive_is_sane`'s `unopened_prefixes` argument,
narrowed in the round-3 final wave after the whole-archive sum refused a legitimate
workbook full of photographs. A `.csv` is not an archive — there is nothing compressed to compare — but
an unbounded row count is its own way to turn a small file into an outsized in-memory
cost (`pandas.read_csv` builds one Python object per cell, and `_ingest_tabular` writes
one `DocumentRow` per row), so it gets its own bound instead: `readers.MAX_CSV_ROWS`
(1,000,000), checked with a cheap streamed line count before `pandas.read_csv` ever
runs. All three refusals raise `readers.ArchiveExpansionExceededError` (a `ValueError`
subclass, naming the ceiling it hit), which surfaces exactly like any other read
failure — an ordinary FAILED document row, `status_detail` naming the reason, Retry
available after the source file is fixed. See `docs/OPERATIONS.md`'s media/document
limits section for the same account from the operator's side.

**Redirect target: the library, unconditionally, always.** Round 9 through round 12 gave
this view an optional `next` POST field (`_upload_next_url`) so the chat attach door could
land back on the conversation it was opened from instead of the library. ROUND 13 (message-
bound attachments) RETIRES that field along with the rest of the chat door's own separate-POST
path (below): the chat door no longer POSTs here at all, so there is no caller left that ever
sent `next`, and `document_upload` redirects to `rag-documents` unconditionally — exactly what
its own two remaining callers (the library page's own form, the workstream Documents panel's
form) have always wanted, and exactly what every caller got before round 9 ever added the
field. `_upload_next_url` itself is deleted, not merely unused.

**The chat door's own separate-POST path is RETIRED (round 13, message-bound attachments;
owner feedback: "we shouldn't process unless the message is actually submitted with the
file").** Through round 12, `agents/chat/templates/chat/_attach_files.html` was its OWN
`<form>`, posting straight here — with a `conversation` field, three validation checks, and
the `next` coupling above — the INSTANT "Add files" was pressed, independent of whether any
message was ever sent. That whole path (the `conversation` field, `is_chat_door`,
`_CHAT_PLACEMENTS_IN_STREAM`/`_LOOSE`, the provenance-write block) is GONE from this view.
The chat door's own file input and placement chooser now live INSIDE the chat turn form
itself (`agents/chat/README.md`'s own "attach door" section has the front-end shape); a
chat-carried file is staged by `tools.rag.services.stage_turn_attachments`, called from
`agents.chat.service.start_turn` at TURN-CREATE time — never by this view, for anybody, under
any field name. `document_upload` now serves only the library page's own upload form and the
workstream Documents panel's (`rag/panels/documents.html`), exactly as it did before round 12
ever introduced the chat case.

A document the WATCHER ingests arrives UNLABELLED and follows the library posture until
somebody labels it. Per-inbox default labels are deferred (spec section 21); the
mitigation is bulk labelling (`POST /rag/documents/labels/`,
`views.py::document_labels_bulk`) — select what arrived, or a whole category, and add
or remove one entitlement across all of it in one action.

Unsupported extensions are skipped with an error message; the accepted set is
`tools/rag/ingest.py::supported_exts()` — `readers.py`'s `PROSE_EXTS |
TABULAR_EXTS`, plus `MEDIA_EXTS` while the `"media"` feature flag is on. Uploads are also
bounded by operator-editable caps (size, media duration, page count) enforced before
expensive work starts.

**Status and retry.** A `Document` carries a `status` (`PENDING`/`PROCESSING`/`READY`/
`FAILED`) and an honest `status_detail`, rendered in the library. Every way an ingest can
fail — including a job cancelled from the queue or a worker that died twice — leaves the
row `FAILED` with a reason and a working Retry
(`POST /rag/documents/<id>/reingest/`).

**Category management** — the library sidebar's manageable shelves (real categories, not
"All"/"Uncategorized") support rename and delete in place:

- `POST /rag/categories/<id>/rename/` (`category_rename`) — sets a new name, rejecting a blank
  name or one that collides case-insensitively (`name__iexact`) with another existing shelf.
- `POST /rag/categories/<id>/delete/` (`category_delete`) — deletes the `Category` row; its
  documents fall back to Uncategorized via `Document.category`'s `SET_NULL` (ADR 0009). Note
  this does not touch already-written chunk metadata, so previously ingested chunks keep the
  old category string until re-ingested.

A hybrid router that also dispatches numeric/tabular questions to text-to-SQL was spiked
(`RouterQueryEngine` + an `NLSQLTableQueryEngine` over the `DocumentRow`/`Document` tables) but
**deferred to Phase 2** — reliable automatic routing with a small local model is unsolved (the
LLM selector emits malformed JSON; the embedding selector mis-routes prose questions). To keep
the live path lean, that scaffolding is **not carried in the tree today**; recover it from git
history (it lived in `tools/rag/sql.py` + `retrieval.py`) when Phase 2 begins. This hybrid
routing, and full-document fetch, are the Phase 2 work — see
[ADR 0005](../../docs/adr/0005-rag-module-architecture.md) for the target design.

Every LLM/embedding call goes through the **Inference Gateway**, so the model/engine is
swappable by config (Ollama today, something better tomorrow) without touching this module.

## Entitlement labels (IA-2)

**`DocumentEntitlement`** joins a `Document` to an `identity.Entitlement`
(who labelled it, when). A document may carry zero, one, or several
labels; **the library posture decides what an unlabelled one means** —
`identity.contracts.postures.LIBRARY_OPEN` (the default) lets everyone
signed in read it, `LIBRARY_LOCKED` restricts it to an administrator
with the content setting on (`sees_all_content`) — never a per-document
default.

**`tools/rag/access.py`** is the one place a principal's whole library
rule is computed: `document_visibility(principal)` returns a
`DocumentVisibility` — the frozen answer to "what may this principal
see", carrying the entitlement axis, the workstream/conversation scope
axis and the owner axis together. The field list is on the dataclass in
`tools/rag/access.py`; it has grown twice and enumerating it here is how
this paragraph went stale. It is built in at most two queries, once per
request/turn and threaded down, never re-derived inside a runner.
`readable_documents(principal)` gates CONTENT — the bytes, the
transcript, every chunk retrieval can return; `listable_documents(principal)`
gates ROWS and is wider for an administrator (`is_admin`, not
`sees_all_content`) because labelling a sensitive document must be
possible for someone not cleared to read it yet; `may_label_document(principal,
document)` is `is_admin` or an OWNER of one of the document's current
labels — an unlabelled document has no owner to delegate from, so only
an administrator may act on one.

`DocumentVisibility.permits(document, …)` is the **one-row** counterpart to
`readable_documents()`'s queryset: the same question, asked about a document
already in hand, and the reason a renderer can decide whether to show a
download link without running a second query. The two enumerate each other's
axes on purpose — the 2026-09 hygiene sweep found and fixed a real bug
exactly there (`permits()` did not apply the conversation-scope axis, so a
chat-scoped document belonging to somebody else's conversation came back
permitted). Adding an axis to one and not the other is the failure mode both
docstrings are written to make visible.

**The one filter point.** `tools/rag/retrieval.py::retrieve_nodes` is the
only place a vector-store filter is built, and its `visibility` keyword
argument is **required, never defaulted** — a default of "see everything"
would be a fail-open default. `answer_question` takes the same required
`visibility` and passes it straight through rather than building a
filter of its own. `DocumentVisibility.sees_nothing` (unrestricted is
false, no entitlements held, unlabelled not allowed) is checked as an
EARLY RETURN before a filter is ever built, because an empty filter
list, passed to the vector store's own filter type, means "no filter" —
which means everything — the single most dangerous line in the phase if
it were ever reached by mistake.

**One writing surface on the page, one route underneath.** The library
table's Labels column is read-only chips. Every label a person changes from
that page goes through the single **"With selected…"** card above the table:
one set of chip checkboxes, one scope select (the rows you tick, or every
document on a shelf), and two verbs. Its semantics are ADD or REMOVE, never
"set to exactly this" — `rag-document-labels-bulk` computes `current | wanted`
or `current - wanted` per document, so labelling forty inbox arrivals with one
label never strips whatever else they carry, and the card's own copy says so.
`rag-document-labels-bulk` is flat in the number of targets: the permission
check hoists `is_admin`/owned-entitlement lookups out of the per-document
loop, and its target queryset is prefetched with `entitlement_labels` so
`document_label_ids` answers from that cache — via `.all()`, which consults a
prefetch, rather than `.values_list()`, which never does — instead of issuing
a fresh query per document.

**Both scopes are bounded.** The ticked-rows scope is capped implicitly at
1000 by the framework's own field limit on a POST body; the shelf scope
names an arbitrary-size category by a single string field, which that limit
does nothing for, so `services.documents_targeted_for_labelling` enforces
its own explicit `MAX_BULK_LABEL_TARGETS` (1000, matching the other scope's
implicit ceiling) before ever handing back a queryset for the shelf. A shelf
over the cap is refused with a flash naming the problem and telling the
operator to select rows instead — nothing is written — the same
honest-rejection shape the upload route's size/duration/page caps use.

There is no per-document label route. There was one — SET semantics, the
only way to express an exact set — and nothing ever posted to it, so it
is gone: the owner's UI ruling is that the page offers chips and one bulk
writer, and a second route with the opposite destructiveness was a
standing invitation to post to the wrong one.

**The chunk-metadata cache: one writer, three callers, two keys.**
`tools/rag/labels.py::restamp_document_chunks(doc_id, *, raising=False)`
is the only function that writes chunk `metadata_`, and it now carries
**two independent optional keys**: `entitlements` (unchanged — a COPY of
what `DocumentEntitlement` says) and `workstream` (new — a COPY of
`Document.workstream_id`, stamped as the **decimal string** `str(id)`,
matching `entitlements`' own string-array convention: the store's `ANY`
and `EQ` operators render as JSON *string* comparisons over `metadata_`,
so an integer would never match a `str(workstream_id)` filter). Both keys
are kept for the same reason — the retriever filters on chunk metadata
and has no way to express a join back to either table, so a visibility
or containment change costs one `UPDATE`, not a re-encode.

Each key is **present or absent**, never present-and-empty: a document
with no labels has no `entitlements` key, and a universal (uncontained)
document has no `workstream` key, exactly what `IS_EMPTY` (`metadata_->>
'k' IS NULL`) matches and exactly what every chunk already looks like
before this key existed — no back-fill needed. Writing `""` instead of
removing the key would make `IS_EMPTY` fail to match it, silently hiding
every re-stamped universal document from every loose turn, Ask, and
Search. The two keys are independent, so the write branches on **all
four combinations** (labelled/unlabelled × contained/universal), and the
no-op guard that skips the `UPDATE` when nothing would change is a
**disjunction** across both keys' presence — a single-key guard carried
forward unchanged would silently skip a real containment write the
moment the second key existed.

It is called from exactly three places: `tools/rag/ingest.py`, right
after `index.insert_nodes(nodes)` (so re-ingesting an already-labelled or
already-contained document restores both), with `raising=False` — a
failed re-stamp must not fail an ingest, and logs instead; every label
add/remove (`set_document_labels`, `unlabel_all_for_entitlement`); and
every containment change (`tools.rag.workstreams.set_document_
workstream`) — the latter two with `raising=True`, because a label or a
containment that appears saved and is not enforced is worse than one
that refuses to save, so the write rolls back with it.

**`manage.py relabel_chunks [--document <id>]`** re-runs the stamp with
`raising=True` outside any of those three paths — the repair for a
store that was briefly unreachable during ingest, or an `Entitlement`
row removed straight from a shell rather than through
`identity.services.delete_entitlement`'s cascade. Cheap: no embedding,
no engine, one `UPDATE` per document.

**`tools/rag/labels.py::entitlement_ids_for(pks) -> frozenset[int]`**
(WS-2) generalises `document_label_ids` from one document to a
set — the UNION of entitlement ids labelling any of `pks`, in one query,
reading `DocumentEntitlement` directly (never the chunk-metadata cache:
the cache is a copy, the table is the fact). Registered from
`tools/rag/apps.py::ready()` as `agents.contracts.artifacts.
ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for")` — a
dotted-path string, so `agents/runtime/taint.py::stamp_turn_taint`
resolves it at stamp time without `agents/` ever importing `tools/`. It
is the only registration this column makes into that registry today: a
turn that returns a `document:<id>` reference to a labelled document
taints the conversation (and its stream, if any) with that document's
entitlements. The empty set for an empty input, without a query.

**A document's bytes, reachable from another column — `access.artifact_file_for`
(chat image artifacts, 2026-09-16).** `tools/rag/apps.py::ready()` registers
this function as the `document` kind's file resolver
(`agents.contracts.artifacts.register_artifact_file_resolver`), so a tool in
another column that takes a file input can resolve `document:<id>` without
importing this one — the peer columns may not import each other at all, and
`agents/` may import neither. The signature is `(pk, principal) ->
ArtifactFile(path, name, media_type)`, and `readable_document` is the gate:
the *same* two-step containment resolution `/rag/documents/<id>/file/` applies
to the same bytes, so a reference can never reach a file that route would 404
on. A row that does not exist, has no file on disk, or is not readable by that
principal raises `LookupError` — **one** exception for all three, deliberately,
so an invisible row cannot be told apart from a missing one (the same
existence-oracle rule the vision column already applies to its own kinds).

**Five more keys on every attachment row — `is_image`, `reference`, `caption`,
`caption_state`, `readable` (chat image artifacts, 2026-09-16; `caption_state`/`readable`
preview UAT, 2026-09-17).** `access.attached_documents` — the registered attachments
provider both the conversation page and a running turn's own prompt read through — now
also says whether a row is an image (`media_type` prefix), what its artifact reference
string is (`mint_artifact("document", id, title)`, minted **here** because the agents
column may not import this one to mint its own, and for **every** row rather than only
images: a prose attachment is equally nameable by a future tool with a file input), and,
for an image whose extraction has finished, a one-line caption. The caption is read
**model-free** out of the extraction sidecar (`<DOCUMENTS_DIR>/<id>/extract.json`'s
`segments`, via `sidecar.read_sidecar`) — never `Document.extraction`, which is a
snapshot of *which* model produced the text and has never held the text itself —
collapsed to one line and capped at 600 characters with a trailing ellipsis
(`access.image_caption_for`). Turn building never calls a model and never waits on
ingest — the owner addendum of 2026-09-08 — and every one of these five values is a read
of something already on disk or in the row.

**Why a caption is blank is now a STATE, not a bare `""`.** `caption_state` names which
of five things is true — `"pending"` (no finished sidecar yet, genuinely still working),
`"ready"` (there is a caption), `"empty"` (extraction finished and found nothing to say),
`"undescribed"` (it finished before this box described images at all, so nobody has
actually looked), or `"n/a"` (not an image — there was never a description to wait for).
Several genuinely different facts used to arrive as the same blank string and the caller
rendered all of them as "still processing", so a finished extraction that found nothing
read as running forever. Image extraction asks for a short description **and** any
legible text as of 2026-09-17 (`extract.DESCRIPTION_PROMPT`, images only — a scanned
PDF's page loop still asks for its words alone), and the description lands as the
sidecar's first segment, so it is embedded for retrieval as well as shown.
**Existing images are not re-extracted automatically.** `extract.json` is keyed on the
source file hash and an unchanged file's sidecar is ordinarily reused as-is — so an
image ingested before this keeps whatever caption its OCR gave it and reads
`undescribed` if that was nothing, until it is **re-ingested from the library**.
Re-ingesting is the one case the reuse check deliberately does NOT take at face value:
`extract_to_sidecar`'s image call refuses to reuse a hash-matching sidecar that lacks
`"described"` (review fix round 2 — an earlier cut of this reuse check did not make this
exception, so "re-ingest to describe it" was a dead end even when an operator did exactly
that), so a re-ingested `undescribed` image is always re-extracted and comes back
`ready` or `empty`, never `undescribed` again. A PDF's page sidecar never carries
`"described"` at all and is unaffected — reused exactly as before, every time.

`readable` is whether THIS principal may read the row's BYTES — the same
`readable_documents` predicate `/rag/documents/<id>/file/` enforces, `True` for every
ordinary attachment and, for a chat-scoped one, `True` only for its uploader. `caption`
is blanked in this providing column for a `readable=False` row, so the text never
crosses into `agents/` at all.

## Workstreams

**Containment and pinning are two different relationships, not one.**
`Document.workstream` (nullable, `PROTECT`) is CONTAINMENT: null means the
document lives in the universal library, exactly where every document
lived before this column existed and needs no back-fill; set means the
document exists **only** inside that stream's corpus — absent from
`readable_documents`' default, from the library page's member listing,
from Ask and Search, and from every other stream, regardless of
entitlements. `WorkstreamPin` (`workstream`, `document`, unique together)
is PINNING: an association layered on top of a universal document, never
a copy and never a move — the document stays in the library, stays
universal, and stays readable everywhere its labels already allowed. A
universal document may be pinned into any number of streams at once
(`doc.workstream_pins`); a contained document has exactly one home and no
pins — **a contained document may never be pinned**, not into another
stream and not into its own. That rule is not a database constraint (a
`CheckConstraint` can't reference another table, and the condition here
spans `WorkstreamPin` and the joined `Document` row); it is enforced in
exactly one place, `tools/rag/workstreams.py::pin_document`, which
refuses by name.

Both containment foreign keys — `Document.workstream` and
`WorkstreamPin.workstream` — reference `"agents.Workstream"` by **string**,
never an import: `tools/rag` must not import `agents.models`, the same
constraint `DocumentEntitlement.entitlement` already lives under for
`identity.Entitlement`. `WorkstreamPin` itself lives in `tools/rag`, not
`agents`, because its `document` half is a real foreign key with real
referential integrity while its `workstream` half is a string reference —
the column that owns the FK owns the join table. `PROTECT` guards both
containment foreign keys (`Document.workstream` and, on the `agents` side,
`Conversation.workstream`): a stream still holding a document (or a
conversation) refuses to delete; an emptied one deletes cleanly.
`WorkstreamPin` itself is pure association and cascades on either half —
losing the stream or the document destroys nothing else.

**`agents.models.Workstream.include_universal` (bool, default True,
round 17) is a stream-wide gate on the universal leg itself**, owner
feedback verbatim: "a bool in settings to use all rag documents ...
default it on." `False` drops `(universal ∩ wall)` from the corpus
formula entirely — wall-narrowed or not — at both `stream_documents`
(here) and `tools.rag.retrieval._visibility_filters`; containment and
pinning, the two relationships above, are unaffected either way.

**A third relationship: `Document.scope` (chat-only, round 12).**
`Document.scope` (`Document.Scope.CONVERSATION` vs. the default
`UNIVERSAL`) is neither containment nor pinning — it is a separate
rag-framework opt-out, not a workstream concept at all. A chat-scoped
document belongs to exactly one conversation (via `DocumentAttachment`,
below), is never returned by `readable_documents(principal,
workstream_id=<real id>)` (round-12-review I-2), is refused BY NAME at
the workstream pin picker (`tools.rag.workstreams.pin_document`, checked
first, before the cap — round-12 whole-branch review B-1) and at the
library page's own row-level Pin button (`documents.html`), and never
enters `agents.workstreams.stream_documents`'s own corpus formula or any
retrieval query. It exists so a chat participant can hand the model a
file for THIS conversation only, without it silently joining the
searchable library — owner ruling, verbatim, round 12: "if I submit a
document but have scope for chat, then it should only be used in that
chat... if I change the scope to workstream, it should be made
available via the rag framework." The chat attach door
(`agents/chat/README.md`'s own section on it) offers this as one option
in its own inline three-way chooser ("This chat only" — the default,
pre-checked — "This workstream only", "The universal library"), distinct
from this column's own `rag/_placement_choice.html` used by the
workstream Documents panel.

**Staging: `CHAT_STAGING_DIR`, a sibling of the inbox, never inside it.**
A chat-scoped upload stages under `settings.CHAT_STAGING_DIR /
f"chat-{conversation_id}"` (`tools.rag.services.stage_turn_attachments`
— moved here from `tools/rag/views.py::document_upload` in round 13,
message-bound attachments, when that view's own chat-door branch
retired; the DIRECTORY and its own watcher-safety reasoning are
unchanged, only which function writes to it), not under
`INGEST_INBOX_DIR` the way every other upload path (with or without a
workstream) still does. `CHAT_STAGING_DIR` sits alongside
`INGEST_INBOX_DIR` and `NOTES_DIR` under `DATA_DIR` in
`config/settings.py`, following `NOTES_DIR`'s own precedent and its own
reasoning verbatim: `watch_folder`'s `Observer.schedule` is registered on
the inbox path alone (`manage.py ingest_watch <path>`), so a directory
outside that subtree is structurally unreachable to it. Before this
(round-12 whole-branch review A-1/B-4), a chat-scoped upload staged
INSIDE the inbox and raced the watcher's own quiescence poll: if the
watcher won, `category_from_subfolder` read the first path segment under
the inbox and minted a document with a category literally named
`chat-<uuid>`, ingested as an ordinary universal document — scope
silently downgraded to `UNIVERSAL`, no chat-scope check ever run. Moving
the staging directory outside the watched subtree closes the race
structurally, not by timing.

**The per-file body itself is `tools.rag.ingest.stage_and_enqueue_one` (C-14,
task 23), the SAME function `document_upload`'s own browser-upload loop calls** —
`services.py`'s own comment used to say this loop "mirrors `document_upload`'s own
pre-round-13 chat-door loop almost verbatim"; that verbatim body now lives in one
place, in `tools/rag`, and both doors switch on its `StageOutcome.kind` for their own
bookkeeping. `stage_turn_attachments` keeps only what is genuinely its own:
`DocumentAttachment` rows (`_attach`) for every outcome that produced or found a
document, `staged_document_ids` (only the ids THIS call's own bytes physically
moved — never a re-attached "unchanged" document, never a watcher-won race), and the
`AttachmentUploadResult` tuple itself.

**`DocumentAttachment`, the join table between a document and a
conversation (round 11 review I-5).** Multiple conversations may attach
the same universal document (each gets its own `DocumentAttachment`
row); a chat-scoped (`Document.Scope.CONVERSATION`) document carries
EXACTLY one, enforced at the write path (`tools.rag.services._attach` —
moved from the now-retired `tools/rag/views.py::_attach` in round 13)
rather than by a database constraint. `turn_id` (round 13, nullable,
migration `rag/0020`): WHICH turn's own submission carried this file —
`None` for a legacy row predating this column, or one the write path
could not attribute to a turn — set once, at creation, and NOT updated
on a later re-attach of the same bytes to the same conversation (a
deliberate, minor, named simplification: the chip stays on whichever
turn FIRST attached it — see the column's own docstring). Read through
a single provider, `tools.rag.access.attached_documents`, registered
once and consumed both by the chat UI's PER-TURN chips (round 13
retires the conversation-level strip this provider used to feed —
`agents/chat/README.md`'s own section has the display side) and by the
live prompt's own attachments block (`agents/runtime/prompt.py`) — one
resolver, so the two surfaces can never disagree about what is
attached; each returned dict also carries `"turn_id"` and `"may_detach"`
(round 13) alongside the round-11/12 keys. Deleting a conversation
(`agents.visibility.delete_conversation`) removes its
`DocumentAttachment` rows through the same cleanup slot the provider
registry uses for reads, inside its own savepoint (round 11 fix-2
Important N-1); for a chat-scoped document specifically, that cascade
deletes the DOCUMENT itself — chunks, managed-store files, and the row —
since nothing else can ever reference it; for a universal or contained
document merely attached to that conversation, only the attachment row
dies and the document lives on.

**The uploader administers their own chat-scoped document (round 12
whole-branch review A-2/B-2).** `tools.rag.access.
may_administer_document` is a strict superset of the pre-existing
`may_label_document`: the same admin/label-authority rule, plus one
OR-clause — `document.scope == CONVERSATION and document.owner_kind ==
principal.kind and document.owner_key == principal.key` — admitting the
uploader of their own chat-scoped document to Delete and Re-ingest
(never to labelling, which stays admin-only and unchanged). Enforced
identically at render (`documents.html`'s row-level actions,
`DocumentsListView`'s `may_administer` key) and at the POST gates
(`document_delete`, `document_reingest`), both of which now read the row
BEFORE refusing rather than short-circuiting on a principal-only check —
chat-scope ownership is a row fact, not something a cheap pre-filter can
answer, so a truly nonexistent id now 404s the same way a real id you
may not touch 403s (a narrower enumeration signal than the strict
"always 403 first" the two gates used before this round, accepted as the
cost of correctly supporting row-specific administration).

**Post-send remove, a NARROWER, DIFFERENT predicate (round 13,
requirement E; owner feedback: "I also need the ability to remove an
attached file").** `tools.rag.access.may_detach_attachment` is
UPLOADER-ONLY — `document.owner_kind == principal.kind and document.
owner_key == principal.key`, no admin OR-clause at all, and no
chat-scope restriction either (it governs universal/contained
attachments too, unlike `may_administer_document`'s own chat-scope-only
widening). Deliberately narrower than the predicate just above: an
administrator who needs to remove someone else's attachment already has
the library's own Delete (`may_administer_document`, admin-widened) for
a chat-scoped row, or the ordinary label/manage tools for a universal
one — this predicate exists only so the chat UI's own chip ✕ can be
offered to exactly the one principal who put the file there, nobody
else, ever. `tools.rag.access.detach_attachment` (the registered
`agents.contracts.attachments` detacher, resolved by `agents.
attachments.detach_attachment`, `agents.chat.views.turns.
attachment_detach`'s one caller) is ROW-ADDRESSED and NEVER-500:
a `doc_id` that does not exist, or one not attached to the named
conversation, is 404 either way; a real attachment this principal did
not upload is 403 — the platform's SECOND row-addressed 403 (identity
route class "O"; `identity/tests/test_route_matrix.py`'s own
`chat-workstream` exception is the first), pinned by a dedicated test
in that same module rather than the generic sweep, which never builds
a row that would trigger it. The ACTION itself follows the SAME
chat-scoped-vs-not split `delete_attachments` (above) already uses:
chat-scoped → full delete (`tools.rag.services.delete_document`, the
round-12 cascade machinery, reused rather than reimplemented);
universal/contained → unlink only, the `DocumentAttachment` row alone.

**Upload placement is a conscious choice, never a silent default**
(spec §9, owner decision 5). An upload inside a stream with no
`default_upload_placement` set must name `placement` (`"universal"` or
`"contained"`) or the view answers 400 naming the field — the one place a
server-side fallback would silently undo an owner's decision, so there is
none. `tools.rag.ingest.stage_document`/`enqueue_ingest` gain a
keyword-only `workstream_id`, defaulting to `None`, so it is applied only
at creation and never re-read on a re-stage: a document's home is set once
and a later content change re-ingests it in place.

**Known, narrow, correctness bug (spec §24 concern 4): the watcher always
wins universal, never leaked.** `document_upload` resolves placement and
forwards `workstream_id` to `enqueue_ingest`, but the deployed
`watch_folder` service polls the SAME inbox out-of-band and calls
`enqueue_ingest` with no `workstream_id` at all — it has no HTTP request,
no principal, and no stream to ask about. If the watcher's own quiescence
check (a file unchanged for >= `STABLE_AFTER_SECONDS = 5`) fires and stages
the document before this view's own upload path gets there, that document
is staged **universal**, silently overriding whatever placement the
uploader would have chosen. This is a correctness bug, not a security one:
the failure mode is a document landing in the broader universal library
rather than one escaping containment it was supposed to have — the
opposite direction of a leak. It is not fixed here. The shape of a real
fix is a placement claim staged ahead of the bytes (a short-lived
`(original_path, workstream_id)` row the watcher's own staging call could
consult before defaulting to `None`), which is a real design surface of
its own and deliberately out of scope for this task.

**`Document.origin`** (`upload` default, `notes`) records what **put** the
row in the library, not what produced its text — that is `extraction`'s
job (the JSON snapshot of the model/method that transcribed or extracted
it, e.g. `{"method": "transcription", ...}`). A consolidated note is
still written through `extraction` with `method="distillation"` (which
`extraction_summary` already renders as "Processed"); `origin` separately
records that the row arrived by consolidation rather than by an upload or
watch-folder ingest. Keeping the two columns apart means neither one has
to grow a case for a concept it isn't about.

**Consolidation (spec §10) distils one stream conversation into its
contained "notes" `Document`** — one note PER CONVERSATION, keyed by
`Document.notes_conversation_id` (a `UUIDField` by value, never a FK: the
same `tools/rag`-may-not-import-`agents.models` constraint that already
governs `Document.workstream`), enforced at the database by
`uniq_notes_per_conversation` (a partial unique index, `NULL` unconstrained
— every non-note row has one). Registered as the `rag.consolidate` job
kind by `tools/rag/apps.py::ready()` (not by `agents`: the handler needs a
model call, an `agents` transcript reached through the `agents.workstreams`
seam, and a document write plus a re-ingest, and only this column can reach
both ends), with `plan_consolidate`/`run_consolidate`/
`summarize_consolidate`/`on_consolidate_terminal` in `tools/rag/jobs.py`.
`tools.rag.distil.distil_conversation` is the one model call (the
`DISTILLATION_PROMPT` constant, and `CONSOLIDATION_MAX_TURNS = 400` — NOT
`agents.limits.HISTORY_TURNS = 20`, which is a live turn's own prompt
budget). **The transcript reaches that call fenced, the same way a tool
result does** (round-3 hardening H32/C-3, folded into H5's own S2 fix,
`docs/adr/0017-workstreams.md`'s dated amendment): the selector
`agents.workstreams.transcript_for` returns every root-depth completed
turn, TOOL turns included, so a turn's own text can carry retrieved
document content — `distil_conversation` wraps the joined transcript
through `foundation.fence.carrying_block`, the SAME wrap function
`agents/runtime/prompt.py`'s own S2/C-1 fences call (H32 review round 1,
IMPORTANT 3: one fence, one home applies to the wrap itself, not only to
the two primitives it is built from), neutralizing fence-like lines and
nesting the body inside a per-call random BEGIN/END marker, with each
turn's role bracketed (`[role]`) rather than colon-joined. THE BRACKET
ALONE PREVENTS NOTHING — a turn's own text can still contain a literal
`[assistant]` line, and nothing strips it; **the fence is the defence**:
a forged line, bracket included, still lands strictly inside the one
real marker pair as ordinary DATA, never as a boundary of its own. The
note file is written under `settings.NOTES_DIR`, deliberately
**not** `INGEST_INBOX_DIR` (the watcher must never be pointed at it, or a
note would be staged twice — once by the job, once by the watcher as a
universal document with a service principal for an actor). Re-consolidation
OVERWRITES the same row: `ingest.stage_document` dedups on
`original_path`, so a second consolidation finds the existing note, sees a
changed `file_hash`, and re-ingests in place — same id, same store
directory, every old `document:<id>` reference still resolves. The note
INHERITS the conversation's taint tags (`agents.workstreams.
taint_ids_for_conversation`) with no laundering: `run_consolidate` is
deliberately exempt from `set_document_labels`' own labelling-authority
rule, because nobody is *choosing* the labels here, they are copied from
material the conversation already carried. **The note is also ATTRIBUTED
to the conversation's own owner** (round-3 hardening H32/C-3, round 2):
`Document.owner_kind`/`owner_key` are stamped directly onto the row, in
the same write as `title`/`origin`, from `agents.workstreams.
owner_fields_for_conversation` — the CONVERSATION's own two columns, not
the job's acting principal (ruling C lets the STREAM owner consolidate a
RECIPIENT's own thread, and it is the recipient's ownership the note
carries, never the button-presser's). ATTRIBUTION ONLY: `stage_document`
still runs with `actor=None` for this job (`NOTES_DIR` is platform-owned,
not a browser-upload root, and this job is exempt from re-stage
authorization exactly like `manage.py ingest` — `tools.rag.ingest.
_acts_for_the_box`'s own docstring), and the note's `scope` stays
`Document.Scope.UNIVERSAL`, workstream-contained, never `Document.Scope.
CONVERSATION` (mutually exclusive with containment, and the opposite of
this section's own "retrievable by that stream's next turn"). Consolidation is OWNER-ONLY in
v1, including for a recipient's own thread inside a shared stream (ruling
F) — a recipient gets a plain 404 from `POST /chat/w/<pk>/consolidate/`
(`chat-workstream-consolidate`) and never sees the Consolidate button or a
staleness hint (`agents.workstreams.staleness_for`) on the stream page. A
re-consolidation whose re-ingest fails leaves the note row at `FAILED` —
not lost, just its content — and the stream page's Documents panel offers
**Re-consolidate** directly on it, the one repair path a non-admin stream
owner can reach for a contained document (the library's own
`document_reingest` route is not reachable for a contained row at all).

**`tools/rag/workstreams.py` is the only module that writes
`WorkstreamPin`**, and — with `tools/rag/access.py`, which reads it for the
visibility value it builds — one of only two that read it at all
(`foundation/ops/tests/test_column_boundaries.py::
test_the_pin_table_has_exactly_two_sanctioned_readers` pins the set).
Every other module that needs to know what is in a stream asks one of
those two a question instead of touching `WorkstreamPin.objects` itself —
the same reasoning, and the same test shape, as the `Document.objects`
gate above it. `pin_document`/`unpin_document` are also the only writers
of the `workstream.document_pinned`/`workstream.document_unpinned` audit
rows.

**`MAX_PINS_PER_STREAM = 200`** is a cap, not a paginator — the same
reasoning `SIDEBAR_LIMIT = 30` carries elsewhere in this column. A pin
set becomes an `ANY(...)` array of ids in every query the stream runs
(`stream_documents`, the panel's `pinnable` list), so an uncapped set is
unbounded work per turn; a single-operator working set is not an archive,
so refusing the 201st pin by name — naming the limit in the refusal
sentence — is preferred to teaching the pin picker to paginate.

`tools/rag/workstreams.py::panel` is `rag.documents`, this column's
section of the stream page, registered from `RagConfig.ready()`
(`tools/rag/apps.py`) as a `WorkstreamPanel` — a dotted-path string
(`"tools.rag.workstreams.panel"`), resolved at render time by
`agents/workstreams.py::panels_for`, never a live import, because
`agents/` may not import `tools/` at all. **Registered in the same
commit as the handler**: `panels_for` resolves every registered path with
`import_string` and never swallows the resulting `ImportError`, so a
registration landing before its module (or in a later commit) would make
every stream page raise — the same-commit rule this column's
entitlement-cascade and job-kind registrations already follow.

## Tools

`tools/rag/tools.py` registers three `agents.contracts.tools.ToolSpec`s in
`RagConfig.ready()`, unconditionally (not gated behind any feature flag —
plain document work exists for every deployment). Each runner is a thin
wrapper: it calls the *same* service function the corresponding page
already calls, never a parallel copy of that logic.

| Tool key     | `roles`                        | `mutates` | Calls |
|---|---|---|---|
| `rag.search` | `("rag.embed",)`                | `False`   | `retrieval.retrieve_nodes` + `retrieval.apply_score_floor` — the no-LLM path `SearchView` uses |
| `rag.ask`    | `("rag.answer", "rag.embed")`   | `False`   | `retrieval.answer_question` — the same seam the queued `rag.ask` job calls |
| `rag.ingest` | `("rag.embed",)`                | `True`    | `ingest.enqueue_reingest` — the retry button's own entry point |

`rag.ingest` is `mutates=True` because it replaces a document's existing
chunks — a change to state that already exists, not new work product.
It is registered, documented, and tested like the other two, but
`grantable_tools()` excludes it: a mutating tool cannot be granted to an
agent. Identity & Auth's second half (IA-2) does not lift this —
ADR 0010's 2026-08-23 amendment (`0010:266-276`) opened the question and
its 2026-08-30 amendment closes it for this phase: the gate stays in
force until a real policy decision names which entitlement a
settings-mutating tool requires by default. It also takes a
`document_id` and nothing else — no file path at all — so a model calling
it has zero filesystem reach, and it enqueues and returns immediately;
it never waits on the job it queued.

`rag.ingest` applies `may_administer_document` — the same predicate the re-ingest button applies —
and answers a refusal with the *identical* message it answers a missing row with. The caller is a
model, and a model can be steered by a document it just retrieved, so a refusal that read
differently from a not-found would be an existence-and-title oracle over the whole library. It is
also declared `mutates=True` (ADR 0010:266-276), which means no agent can currently be saved with
it and no turn is ever offered it; the predicate is here so that fence can be lifted without this
runner shipping open.

Uploading through `POST /rag/documents/upload/` is itself gated once
accounts are on, at the door rather than in a turn: `tools/rag/
views.py::_may_upload` checks `agents.entitlements.tool_access_for(principal)
.allows(RAG_INGEST_TOOL_KEY)` — the SAME entitlement label this table's
`rag.ingest` row carries — deliberately the mirror of `tools/vision/
views.py::_may_generate`'s shape (one predicate, two callers: the page
and the POST). A member with no grant of it sees the library with no
upload form at all, and a POST anyway answers 403. Being `mutates=True`
already keeps `rag.ingest` off every agent's tool list; this is the
separate, direct-UI-surface question of whether a PERSON reaches the
form at all (decision 17/spec §9.3), and it is answered the same way at
`/chat/tools/`, where `rag.ingest` is listed marked **page-only** since
no agent can ever be granted it.

`rag.search`'s `score_floor` and `hybrid` flag always come from
`RagSettings`, never from tool arguments — they are operator policy (ADR
0014 §14), not something a model call should be able to loosen.

## Status

Phase 0 (design) complete — see the ADRs above. Phase 1 is implemented end to end: the data
model, managed document store, and categories (ADR 0009); ingest
(`tools/rag/ingest.py`, this module's `manage.py ingest` / `ingest_watch` commands, the
browser upload, and the `rag.ingest` job kind) — into-store, dedup/re-ingest, prose
chunk+embed, tabular rows+schema, and category assignment; retrieval
(`tools/rag/retrieval.py::answer_question`) doing semantic search with optional category
scoping; and the ask page (`GET /rag/`) plus the sidebar+search+pagination document library
(`GET /rag/documents/`, described above).

Media ingestion ([ADR 0014](../../docs/adr/0014-media-ingestion.md)) added video/audio
transcription and vision text extraction for images and scanned PDFs, an `extract.json`
sidecar per document (its path decided by `tools.rag.store.sidecar_path`), a transcript
page (`GET /rag/documents/<id>/transcript/`), HTTP
Range support on the file endpoint (`GET /rag/documents/<id>/file/`, which the library
and citation links open in a new tab) so a browser or player can stream and scrub the
original, and locator-bearing citations — *"file.mp4 at 12:40"*,
*"report.pdf, p. 3"*.

Both routes (B-8, round-3 hardening) answer `Cache-Control: private, no-store, max-age=0`
with `Cookie` added to `Vary` — including the file endpoint's 206 Range slice, which is a
second, non-`FileResponse` path and would otherwise drift from the plain response above it
— via `foundation.http.mark_private`, the same shared helper `tools/vision` calls for its
own file routes. See `docs/OPERATIONS.md` §"Private content never gets cached" for the
finding this closes.

W4 (ADR 0014 §14) made retrieval operator-tunable: `RagSettings.retrieval_top_k`
(1–50, default 5, `GET /rag/settings/`'s Retrieval card) replaces the old fixed
`similarity_top_k`, and `RagSettings.retrieval_score_floor` (default `0.0` = off, entered
in the Library form's 0–1 input bounds) drops any retrieved chunk scoring below it *before*
synthesis and citations see it — when every retrieved chunk falls below a non-zero floor,
`answer_question` skips the LLM call entirely and returns an honest "nothing matched" answer
with zero citations, never a 500. The floor is the store's **raw cosine similarity** score
(verified against pgvector: `1 - cosine_distance`), not a normalized confidence — a useful
threshold depends on which embedding model is bound to ingestion/retrieval, there's no
universal value, so pick one empirically against your own corpus/model rather than assuming
e.g. `0.7` means the same thing across embedding models. Saved values are rounded to 2
decimal places (matching the form's own `step="0.01"`), so an untouched re-save of whatever
the settings page renders back never drifts the stored value.

Saving `retrieval_top_k` is rejected when it can't fit the bound `rag.answer` model's own
context window (`top_k * tools.rag.ingest.CHUNK_TOKENS + tools.rag.ingest.RESPONSE_RESERVE`
against `ResolvedModel.config["context_window"]`, when known); an unknown window is
accepted. This check is **save-time only** — if `rag.answer` is later rebound to a model
with a smaller context window, the check does not re-run, and `answer_question` does not
clamp `top_k` at ask time either; a stale top_k that no longer fits its now-current model
surfaces as a real synthesis-time failure, not a silent truncation.

`_vector_citations` also now **dedups**, but only when there's a real, SHARED locator to
dedup on: a document cited more than once at the *same* non-empty locator (page/timestamp
span) collapses to one citation keeping the higher score, while distinct spans of the same
document (a film cited at 3:08 and again at 7:00) stay separate. A chunk with **no locator
at all** — plain-text documents, which have no page/timestamp metadata — is never collapsed
with another chunk of the same document; collapsing on `(document_id, "")` used to mean every
chunk of every prose document silently dropped all but its first citation.

**A citation dict's keys** (`_vector_citations`, `tools/rag/retrieval.py`): `source`,
`document_id`, `title`, `chunk_id`, `row_index`, `score`, `snippet`, `page`, `start_seconds`,
`end_seconds`, `locator`, `locator_text`. **The host filesystem path is deliberately not one
of them** (finding C-5, round-3 hardening): every citation passes through this one function on
its way into an `rag.ask` tool result, a stored chat turn, and the Ask page, and a host path
has no business reaching any of those — an unauthenticated reader on an open box included.
The one caller that legitimately wants it, `manage.py ask` (an operator already at the
machine's own filesystem), reads `Document.source_path` off the row by `document_id` instead.
Node metadata still carries `source_path` internally (ingest writes it there); only the
*citation* built from it narrowed. Ask tool turns stored before this fix keep the key in
their saved `Turn.data` — nothing migrates old rows — but no renderer reads it, so an old row
is not a live leak, only a past one already closed by this fix.

W5 (ADR 0014 §18) added **hybrid keyword + vector search**, an operator toggle
(`RagSettings.hybrid_search`, `GET /rag/settings/`'s Retrieval card, default **off**). Enabling
it does **not** change anything about what's actually searched by itself — the chunk table's
own shape (whether it has the generated `tsvector` column hybrid search needs) always wins
over the toggle for any table that already exists, so flip the toggle, then **Re-encode the
index** (Models → rag.embed → Re-encode) to actually rebuild the table in the new shape;
until that rebuild finishes, search stays semantic-only exactly as it was. The rebuild drops
and repopulates the whole chunk table — the same drop-and-re-embed cost a dimension-changing
model swap already pays — adding a `text_search_tsv` column (`GENERATED ALWAYS AS
to_tsvector('english', text) STORED`) and a GIN index over it; `"english"` is the only
dictionary offered (a Postgres-builtin, no download, matching this platform's offline-first
posture) — stemming is English-only, non-English text still matches on exact tokens.

Once the rebuild completes, a query concatenates a dense (semantic) hit list with a keyword
hit list (Postgres `ts_rank` over the `tsvector` column, **not** BM25 and **not** a sparse
embedding model), dedups by chunk, and returns up to **2×top_k** results — there is **no
fusion/weighting** between the two lists (the installed store doesn't support one; an
`alpha` parameter would only log a warning and be ignored). Because a dense cosine similarity
and a raw `ts_rank` sit on incompatible scales with no normalization, **after the rebuild, the
score floor is no longer applied** — applying it to a mixed list would silently delete every
keyword-only hit, exactly the result hybrid exists to surface. (Flipping the toggle alone does
not do this — see above: the floor stays live until the re-encode actually rebuilds the table.)
Enabling hybrid re-checks the context-window fit `retrieval_top_k` already gets (see above),
against **double** the stored `top_k` — `sparse_top_k` equals `top_k`, so a hybrid query can
return twice as many chunks.

The exact-scan truth from before still holds: there is still no approximate-nearest-neighbour
index anywhere in this store; hybrid adds a *second* exact (GIN-backed) scan per query, not an
ANN one — it's a **recall** change, not a latency one.

Still open, and honest about it: **tabular data is stored but not searchable** — ADR 0005's
hybrid text-to-SQL routing remains Phase 2 work (W2, above, made the UI say so plainly
everywhere it matters; the underlying limitation itself is unchanged).

**Search page** (W6, ADR 0014 §18): `GET /rag/search/?q=…&category=…` — a retrieval-only
page, no LLM, no queue, no `AskRecord`. It's the score floor's visible surface: a plain way
to see what semantic (or hybrid) search actually retrieves for a query, without spending an
LLM call to find out. It reuses the EXACT SAME retrieval `answer_question` runs —
`tools.rag.retrieval.retrieve_nodes` (top_k, hybrid-if-the-live-store-is-hybrid, category
filter) and `apply_score_floor` (the W4 floor, suspended under hybrid same as Ask) are both
factored out of `answer_question` and shared by the two callers, so the two pages can never
silently disagree on what counts as a match. Each result shows the document title (linking to
the file), its locator (page/timestamp, when the chunk has one), the raw score, and an
honestly-attributed label — "cosine similarity" when the live store is dense-only (every
result really did come from that one query), or the neutral "relevance score" under hybrid
(the installed store concatenates dense + keyword hits with no per-result tag saying which
arm a hit came from, so a confident per-node label would be a guess) — plus the matched
chunk text (escaped, whitespace-preserved, capped ~1200 characters). An empty query renders
just the form; no results shows an honest empty state, naming the exact floor value only when
something was actually retrieved and a floor is set and live (W6 review MAJOR 1: an empty or
no-match library/category with a floor configured still retrieves nothing, so it gets the
plain "nothing matched" copy instead — naming a floor that wasn't even the reason for the
empty result would be dishonest). Retrieval runs SYNCHRONOUSLY in the request — deliberately bypassing the
execution queue, since a search is one embedding call plus one SQL query with no LLM in it,
and the embed model is already resident for every other RAG surface on this install; an
unresolvable `rag.embed` binding or a runtime embedding failure both degrade to inline copy,
200, never a 500 or 503.
