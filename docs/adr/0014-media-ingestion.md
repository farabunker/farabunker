# ADR 0014 — Media ingestion: video, audio, images, and scanned PDFs

**Status:** Accepted
**Date:** 2026-08-24

Builds on [ADR 0005](0005-rag-module-architecture.md) (the RAG module, whose
"OCR for scanned PDFs… voice, and non-document sources" deferral this ADR
lifts), [ADR 0009](0009-document-store-and-categories.md) (the managed
document store), [ADR 0010](0010-model-management-framework.md) (roles,
capabilities, bindings), [ADR 0012](0012-image-generation-engine-adapter.md)
(the first non-RAG engine adapter, whose native-host and feature-flag
patterns this build reuses), and [ADR 0013](0013-inference-execution-queue.md)
(the execution queue, whose per-job progress deferral this ADR lifts).

## Context

Before this build, RAG ingestion accepted only text-extractable formats:
`.pdf`/`.txt`/`.md`/`.docx` as prose, `.csv`/`.xlsx` as tabular
(`modules/rag/readers.py`'s `PROSE_EXTS`/`TABULAR_EXTS`). A scanned PDF —
one with no text layer — parsed to zero pages and produced zero chunks,
silently. Video, audio, and images were not accepted at all. ADR 0005's
Scope section deferred exactly this:

> **Deferred (later phases):** reranking, multi-hop/iterative retrieval,
> **OCR for scanned PDFs**, per-document access control…, incremental/
> streaming re-index at scale, **voice, and non-document sources**.

The owner's requirement for this build, quoted as written (one product name
elided per this project's docs rule against naming models):

> further enhance the rag process to take in video files (take them in and
> transcribe them using [a speech-to-text engine] or something similar),
> PDF's, Word Documents, and PDF/Images with text (i.e. using a model to
> injest and capture text). the goal is for us to expand the mediums… we
> must organize the code in an effective way and not duplicate anything, we
> must be thoughful of the structure and how we execute.

ADR 0013's queue is what made this affordable. Transcribing an hour of
video and running one vision call per page of a scanned document are both
minutes-to-hours of model work; neither can run inline on a request thread
or on the watcher's own poll loop, and both need priority, memory
accounting, and — new here — visible progress and the ability to survive a
deploy. Exploration also surfaced three pre-existing hazards this build
fixes on the way past, because at video sizes they stop being cosmetic:
every ingested file was stored twice forever (the inbox was never swept),
unsupported inbox files vanished with no log line at all, and ingestion ran
inline on the watcher thread.

This ADR records the media-ingestion build (tasks T0–T10): the module
layering it is organized by, a third engine adapter, one new job kind, the
sidecar artifact that makes media re-encodes cheap, the progress/checkpoint
contract now shared by every job kind in the platform, and the named limits
it ships with.

## Decision

### 1. The layering law

The owner's "organize the code effectively, don't duplicate anything" ask is
enforced structurally, not by convention. `modules/rag/` gained four leaf
modules and one pipeline module, and the dependency arrow only ever points
one direction:

```
{readers, transcode, extract, sidecar}  ←  media  ←  ingest  ←  jobs
```

Each leaf is a genuine leaf, and each is restricted differently:

- **`readers.py`** — plain parse only. Never a model, a subprocess, the
  database, or the queue. It gained the media extension sets
  (`VIDEO_EXTS`/`AUDIO_EXTS`/`IMAGE_EXTS`/`AV_EXTS`/`MEDIA_EXTS`),
  `medium_for(ext)`, and `pdf_textless_pages` — the per-page scanned/mixed-
  PDF detector, a promotion of an observation `_read_pdf` was already
  making and throwing away. (`pdf_page_count`/`pdf_has_extractable_text`,
  the original whole-file pair this shipped with, were retired by W1 —
  see §17/§18 below — in favor of the one page-level scan.)
- **`transcode.py`** — deterministic local conversion via an external tool
  (`ffmpeg`/`ffprobe` on PATH) or a wheel (`pypdfium2`, `Pillow`). Bytes in,
  bytes out: `probe_duration`, `extract_audio` (to 16 kHz mono PCM WAV —
  normalized *here*, so every container format takes one path downstream),
  `slice_audio`, `rasterize_pdf_page`. Never a model, never the database,
  never the queue. Every shell-out is `subprocess.run` with an argv **list**,
  never `shell=True`; a missing binary surfaces as an honest `RuntimeError`
  naming the tool, following `readers._read_docx`'s soft-dependency guard.
- **`extract.py`** — the single vision call: one image in, one model call,
  one string out.
- **`sidecar.py`** — the model-free `extract.json` reader. Alone among the
  four it is imported from *both* halves of the pipeline (the worker half
  and `modules/rag/views.py`), because reading an already-finished sidecar
  is a cheap local file read with no worker-only dependency — no ffmpeg, no
  transcriber client, no model resolution.

`media.py` is the model-consuming half those four feed: the transcription
loop, the vision-extraction loop, segment grouping, the sidecar writers, and
`documents_from_extract`. The law is about **calls, not importability**: no
model-consuming function of `media` is ever called outside a worker/run path
— `transcribe_to_sidecar` and `extract_to_sidecar` have exactly one caller
each, `ingest.py`'s run half, reached from `jobs.py`, the queue's own entry
point — because an HTTP request thread must not block on a multi-minute
transcription. The module itself is importable anywhere, and is: `ingest.py`
imports it at module level, and `views.py` imports `ingest`. The stage half
uses that on purpose, for two model-free helpers —
`media.duration_cap_message` and `media.page_cap_message` — so a watcher or
upload rejection of an over-cap file reads in exactly the words the run
half's own defensive re-check would have used. `ingest.py` sits above
`media`, and `jobs.py` above that. The four leaves must never import
`media`.

This is stated in each module's own docstring, not only here, so a
contributor reads the law at the file they are editing rather than in a doc
they may not open.

### 2. whisper.cpp is a native host service, not a container dependency

`core/inference/engines/whisper.py` is the platform's third engine adapter,
and it follows ADR 0012's ComfyUI template exactly: the server runs
**natively on the host** (so it can reach the GPU), the containers reach it
over `host.docker.internal`, and `WHISPER_BASE_URL` is the only default —
a *location*, which ADR 0010's third amendment already carves out as the one
kind of default the platform is allowed to bake in. Python-speech-recognition-
in-the-container was rejected outright: the native-host rule exists for
GPU/model engines, and this is one.

Three properties of this engine forced honest answers rather than convenient
ones:

- **`list_installed` returns `[]`, always.** The server loads exactly one
  model file, named on its own command line when the process starts, and
  exposes no route that reports which file that was (verified live against
  a real, running server: a bare `GET /` and `POST /inference` are the only
  routes found, and neither names it). The platform does not guess a model
  id on the operator's behalf — the same "never fabricate" stance the
  image-generation adapter's own `list_installed` takes. The adapter's
  `install_cmd_template` documents the real mechanism instead: **the start
  command is the model choice**, one model per server process; a second
  model means a second server on another port, registered as its own
  connection.
- **Health is a body sniff, not a bare 200.** The conventional port for this
  server is commonly squatted by other local dev servers, so "something
  answered" is not evidence. `is_healthy` requires a 200 *and* a marker
  substring within the first 4096 characters of the decoded response body
  (`engine_http.HEALTH_MARKER_SEARCH_BYTES`, applied to the decoded text).
  The read itself is bounded, not just the sniff window: it stops at that
  same 4096-byte prefix rather than reading the rest of the body, and a
  status page *longer* than the prefix is still sniffed on what was read
  rather than refused outright (`engine_http.get_bounded(...,
  truncate=True)`) — a resource bound as much as a check either way.
- **Footprint is unknown, so jobs run alone.** The adapter implements
  neither `loaded_footprint` nor `unload`, because the server reports
  neither. ADR 0013 §4's rule — an unmeasured footprint is treated as
  effectively exclusive — therefore governs every transcription job today:
  it is admitted only into an idle machine and nothing runs beside it. The
  documented recovery path is the operator's: setting
  `ModelConnection.footprint_override_bytes` in the console supplies the
  number the engine cannot, and the scheduler starts reasoning about the
  job normally.

The `Transcriber` protocol (`core/inference/engines/base.py`, beside
`ImageGenerator`) is one method — `transcribe(audio_path, *, language=None)
-> TranscriptResult` — over frozen dataclasses that carry the one arithmetic
operation every slicing caller would otherwise re-derive (`TranscriptResult
.offset(seconds)`). The request sends only a response-format field, plus a
language when the operator supplied one on the connection: no model-behavior
knobs, the lesson ADR 0010's context-window amendment already records. The
model id is carried on the object for the platform's own record-keeping and
is **never sent** — the server's own start flag is the truth.
`gateway.get_transcriber`/`get_transcriber_for` mirror the image-generation
accessors, including the written cannot-transcribe error.

`"transcription"` joins `CAPABILITIES`; `rag.transcribe` (capability
`transcription`) and `rag.extract` (capability `vision` — which ADR 0012 D3
had already reserved for image *input*, and which this is the first role to
actually use) are registered by `modules/rag/apps.py`. Both declare
`rematerialize=None`: re-transcribing is a re-ingest of the source file, not
a role rematerialization in place.

### 3. The vision-extraction path reuses the model layer as it stands

Reading text off an image needed **zero** framework changes. `extract.py`
builds one `ChatMessage` with two blocks — the prompt, then the image — and
sends it through the existing `get_llm_for` seam; the installed LlamaIndex
adapter carries the image block natively. One image, one call, one string.

`EXTRACTION_PROMPT` is a **module constant, and platform behavior — not a
model default.** The exact wording is what makes "transcribe the visible
text, verbatim, no commentary" true of *any* model the role could be bound
to, regardless of that model's own out-of-the-box manners; a vision-capable
chat model asked with no instruction at all may narrate an image instead of
transcribing it. It lives as one named constant rather than inline in the
call so an operator auditing platform behavior has exactly one place to
look. Making it operator-editable is a named deferral (§18), not a decision
made here.

**Model output is stored verbatim.** Nothing judges, filters, or rewrites
it — the same content-agnostic stance ADR 0012 states plainly for image
generation, in its Context's third property and again in its own "The
content-agnostic stance, stated plainly" section. The one exception is
not a judgment: a whitespace-only result means the page had no text, so that
page contributes no segment and the skip is logged — the precedent
`_read_pdf` already set for an empty page.

A scanned PDF is **not a medium of its own.** `medium_for` calls every
`.pdf` "prose", unconditionally. `_needs_vision_extraction` is a separate,
content-based auto-detect that decides whether a given prose `.pdf` routes
through vision extraction instead of the ordinary text parse — so the
routing follows what is actually inside the file, not what its extension
claims.

### 4. One job kind, `rag.ingest`, with per-medium planner branches

Every ingest is one job kind, not five. The payload is
`{"document_id", "sha256", "medium", "title"}` — deliberately **no path**:
`Document.source_path` is the single source of that fact, and carrying the
title and medium keeps the queue-page summarizer free of database access.

`plan_ingest` branches on `medium` and declares, honestly, what each shape
actually consumes:

| medium | model refs | why |
|---|---|---|
| `prose` | `rag.embed` | it embeds — truthfully |
| `tabular` | *(none)* | tabular ingest genuinely never embeds (ADR 0005) |
| `image`, `pdf-scanned` | `rag.extract` + `rag.embed` | extract text, then chunk and embed it like any other prose |
| `video`, `audio` | `rag.transcribe` + `rag.embed` | transcribe, then chunk and embed the transcript |

**`pdf-scanned` is assigned PESSIMISTICALLY at enqueue time, not by content
(H28 review round 1, finding 2 — dated amendment below).** Every `.pdf`
gets this token while `"media"` is enabled, off its extension alone —
never a per-file scan. The content-based decision (does this PDF actually
have a textless page) is made separately, later, at `run_ingest_for`'s own
job-start dispatch — see the amendment for why the two now differ on
purpose.

The empty tuple for `tabular` is honest but not free: ADR 0013 §4's rule
treats "declares no models at all" as indistinguishable from a planner bug
and therefore effectively exclusive, so a queued CSV ingest still runs alone
for its own brief duration. That is accepted here rather than papered over
with a dishonest `exclusive=True` or a fabricated model ref; a dedicated
zero-footprint job class is a named fast-follow (§18).

**Transcription declares `exclusive=False`, and this is a deliberate
divergence from `vision.generate`'s `exclusive=True`** — worth recording,
because the two look inconsistent and are not. Today they behave
identically: neither engine reports a footprint, so ADR 0013 §4's rule 2(b)
makes both run alone regardless of what their planners say. The difference
is what happens when that changes. `vision.generate` hardcodes `True`, so a
future measured footprint would still not buy it any concurrency without a
planner edit. `rag.ingest` declares what the job *is* — ordinary, not
machine-hogging work — so the moment a footprint becomes knowable (an
operator's `footprint_override_bytes` today; an engine that learns to report
one tomorrow), a transcription job starts running alongside other work with
zero code change. A real measurement then **buys** concurrency the job was
always honestly declared to want.

`default_priority=200` — the same 200-point handicap behind interactive Ask
traffic that `rag.reencode` already carries (ADR 0013 §2). Ingest is
background work; it queues behind a person waiting on an answer.

The kind is registered **unconditionally**, not behind the media feature
flag: plain prose and tabular ingestion moved onto the queue for every
deployment, whether or not media mediums are enabled.

### 5. Staging happens at enqueue — hash, dedup, MOVE, a row, and a Retry

`modules/rag/ingest.py` split into a **stage** half and a **run** half.

Staging (`stage_document`, called by the watcher and by upload) hashes the
file, dedups on `(original_path, file_hash)` — unchanged semantics, so a
re-drop of an unchanged file still creates no job — creates the `Document`
row at `status=PENDING`, and **moves** the file into the managed store
(`store.move_file`, an atomic rename on the same volume). The move is the
fix for the double-storage bug: before this, every ingested file lived
forever in both the inbox and the store, which is merely wasteful for a PDF
and fatal for a library of video. The store keeps two named verbs and the
caller chooses: `manage.py ingest` still **copies**, because moving an
operator's own file out from under them would be wrong.

Staging never parses, chunks, or embeds anything. It only makes the file and
its row exist and be queryable — which is the point: a dropped file appears
in the library, with an honest status, the instant it lands, rather than
after a pipeline that may take an hour.

The run half (`run_ingest_for`) writes PROCESSING first and re-verifies the
stored copy's SHA-256 against what staging recorded immediately *after* that
write, before any real work — so a file that changed on disk after staging
raises out of a row already marked PROCESSING, which the wrapper below
resolves to FAILED, rather than out of a PENDING row nothing would then have
moved.

`Document.status`/`status_detail` are never derived from job rows (a job row
is retention-pruned and would erase the answer). They are written in exactly
five places in `ingest.py`, plus §8's hook, each owning one transition:

- `stage_document` writes PENDING — the row exists and is queryable before
  anything heavy starts.
- `run_ingest_for` writes PROCESSING on entry and READY on a clean finish,
  and writes nothing on failure: it lets its own exceptions, its SHA
  mismatch included, propagate untouched.
- `run_ingest_or_fail` — the one shared wrapper both the CLI and the queue
  handler call, so both agree on the failure shape — writes *only* FAILED,
  with the exception's own text as `status_detail`, and re-raises. It is
  what guarantees a PROCESSING row is never stranded.
- `_enqueue_ingest_job` writes FAILED when the *enqueue* itself fails (the
  queue unavailable, e.g. migrations not yet run), with an honest detail
  rather than silently dropping the file.
- `enqueue_reingest` writes PENDING again on a retry, clearing any prior
  `status_detail` so the library does not show a stale FAILED chip while the
  new job waits.

§8's `on_terminal` hook covers the paths where the handler never ran at all;
it is the only writer outside `ingest.py`, and it moves a row still sitting
at PENDING or PROCESSING to FAILED via one conditional `UPDATE`, so it can
never clobber a status the handler already wrote.

Every one of those failure states is reachable by the operator:
`POST /rag/documents/<id>/reingest/` re-queues the run half against the
already-staged file. That endpoint is what closes every failure loop in this
build — the reason §8 exists at all is that one class of failure was
invisible to it.

**The watcher's 2-second debounce was replaced by a size-quiescence check**
(`STABLE_AFTER_SECONDS = 5`). A multi-gigabyte copy fires modification
events for minutes; a debounce would have moved a half-written file into the
store and hashed it. The watcher now re-stats each tracked file and only
acts once its size has held still. Its silent-drop of unsupported extensions
was also fixed — it logs.

Upload streams each file into the inbox and then enqueues **directly**, from
the request, rather than leaving it for the watcher to notice: the operator
gets a job immediately and a message pointing at the Queue page.
Double-enqueue is impossible by construction (move + dedup); the watcher
winning the race is detected and counted correctly rather than inferred.
`FILE_UPLOAD_TEMP_DIR` now points inside the host-mounted data directory —
multi-gigabyte uploads were transiting the container's writable layer, an
ADR 0006 §3 violation that was one line to close.

The CLI stays a direct, synchronous path — the same bypass class ADR 0013 §8
records for `manage.py ask`/`manage.py reencode`, which ingest now joins —
and that is what lets it bulk-backfill media inline.

### 6. `extract.json` — one sidecar file, one shared schema core

Every extraction — transcription or vision — writes a single
`extract.json` in that document's own store directory
(`DOCUMENTS_DIR/<id>/`), around one shared core of keys every writer emits:
`version`, `method`, `source`, `source_sha256`, a `model` snapshot (engine +
model id, a snapshot rather than a live reference, the same reasoning
`AskRecord` uses), `produced_at`, and `segments` — `{start, end, text}` for a
transcription, `{page, text}` for a vision extraction. The transcription
writer (`media._sidecar_payload`) adds two keys on top of that core,
`duration_seconds` and `language`; the vision writer
(`media._extraction_sidecar_payload`) deliberately omits both, because
neither concept applies to a page or an image and carrying them as permanent
`None`s would leave every future reader guessing whether they ever mean
anything. Nothing has to branch on which writer produced a file:
`media._load_finished_sidecar_if_matching`, the reuse check both writers
share, reads only `produced_at`, `source`, and `source_sha256` — core keys
both shapes always have.

It is a sidecar rather than a table for three reasons, in the order they
decided it: it is derived-of-the-file, and the store is where a file's
things live; the writers are the same code path for both mediums, so one
schema avoids two tables that would drift; and — the operational one — it
is what makes an embeddings re-encode of a media document cheap.
`ingest._source_documents` dispatches on it: if a sidecar exists,
`media.documents_from_extract` re-chunks the already-extracted text, model-
free, with timestamps intact. Without it, `reencode_all` would try to read
raw video bytes as if they were a text file. Re-encoding a library of
transcribed video after an embeddings rebind therefore costs embeddings
only — it never re-runs a transcription.

The short-circuit is **keyed on the source file's hash**:
`_load_finished_sidecar_if_matching` reuses a sidecar only when it carries a
`produced_at`, names this file, and records the same `source_sha256`. A
changed file re-stages, which deletes the old sidecar with the rest of that
document's data, so a genuinely new file always re-extracts.

**One optional key, added by W1: `rasterized_pages`.** A `.pdf`-only,
vision-sidecar-only addition (never emitted for a transcription or an
image sidecar) — the full list of TEXTLESS page numbers a per-page PDF
extraction run intends to process, written identically on every
checkpoint write and the completion write. It is the PLANNED set, never
"pages done so far" — worth stating precisely, because that alone is
**not** what makes it safe for `ingest._source_documents` to read as a
*skip* set (that function's own completeness gate already rejects an
in-progress sidecar, `produced_at` unset, before `rasterized_pages` is
ever looked at, so this key in practice is only ever read off a
genuinely FINISHED sidecar). The real property "written identically on
every write" buys: every writer of the key, at every point in the run,
agrees on what it says, so a reader can never land on a partial or stale
answer, whichever write it happens to see. `_source_documents` reads it
to decide which of the document's own text-layer pages to merge back in
alongside the sidecar's extracted ones — a mixed PDF's sidecar was never
meant to be the whole story, only the pages that had no text layer of
their own (§18 below). A sidecar written before W1 has no such key at
all, so a pre-W1 mixed PDF's re-encode behaves exactly as it always did
(text-only) until the document is re-ingested.

**Its named limitation, stated rather than discovered later: re-extraction
after a *model* upgrade requires a re-ingest of the source file.** Because
the key is the file's hash and not the model's identity, binding a better
speech-to-text or vision model and pressing Re-ingest (the document is
already READY at this point) will reuse the old sidecar and skip the model
entirely. There is no `--force` flag today. The
workaround is to re-drop the file (or delete the document and re-add it);
the flag is a named gap (§18).

### 7. Progress and checkpoint — ADR 0013 §8's deferral, lifted

ADR 0013 §8 named per-job progress reporting as an extension seam it did not
build. This build needed it — "still transcribing" for forty minutes is not
an operator experience — so it is built here, for every job kind at once.

`InferenceJob` gained **two** columns, kept separate on purpose so a display
write can never clobber resume state:

- `progress` — `{"done", "total", "unit", "label"}`. A dict, not a percent,
  because `total: null` must be expressible: the platform never fabricates a
  denominator, and the Queue page renders a bar only when the total is
  genuinely known — text alone otherwise, never a guessed width.
- `checkpoint` — opaque, kind-owned execution state. Neither the worker nor
  the column ever interprets its shape.

**Every job kind's handler signature became `handler(payload, models, ctx)`.**
This is a real break, taken once, with four handlers in existence, rather
than repeatedly later. `ctx` is a `core.inference.jobkinds.JobContext`: a
frozen dataclass carrying `job_id`, `attempt`, `checkpoint_state`, and two
methods, `report_progress(done, total=None, *, unit, label="")` and
`checkpoint(state)`. It holds **no database access of its own** — the two
write paths are plain closures injected by the worker, so `core/` still
imports neither side's storage layer. A handler may ignore `ctx` entirely,
exactly as it may already ignore `models`; prose and tabular ingests never
look at it.

The writes are **token-conditional single-row UPDATEs**, each touching only
its own column, issued from the worker's pool thread. That is what preserves
ADR 0013 §7's single-writer invariant: a heartbeat write and a progress
write are both `pk=… AND state=running AND claim_token=…` conditional
updates on disjoint columns, so a stale worker cannot write progress for a
job it no longer holds, and neither write can clobber the other. Progress
writes are throttled by the *worker* (`PROGRESS_INTERVAL_SECONDS = 5`), not
by the context object, so a handler may call `report_progress` on every loop
iteration without reasoning about write cost. `checkpoint` is deliberately
**unthrottled** — every call is a real write — so a handler calls it only at
a genuine resume boundary.

A thread-local ambient reporter (so a handler could call a bare
`report_progress(...)` without threading `ctx` through) was **rejected**,
for the same reason `configure_settings()` was deleted (ADR 0010's
2026-08-23 amendment): implicitly-read shared state is one more place for a
job's identity to leak.

Requeue paths — drain, orphan sweep, unlaunched — never touch either column.
That *is* the resume mechanism: a long transcription survives arbitrarily
many deploys, resuming each time, because a drain leaves attempts unchanged.
A crash still burns the job's one attempt, which is ADR 0013's existing and
correct policy.

**Resume reconciliation is explicit about the one inconsistency a crash can
produce.** The extraction loops write the sidecar and *then* the checkpoint,
in that fixed order, so a crash between the two leaves the sidecar one unit
ahead of the last confirmed checkpoint. `_reconcile_sidecar_resume` — shared
by the per-slice and per-page loops — resolves that with three rules and no
guessing:

- **Sidecar unreadable** (missing, corrupt, truncated, or valid JSON that
  isn't an object): its segments are unrecoverable, so the resume index is
  reset to zero and the whole extraction redone. Slow, but correct — the
  alternative, keeping a stale index, would skip units whose text no longer
  exists anywhere.
- **Sidecar ahead of its checkpoint** (more segments than the checkpoint
  confirmed): truncate to the confirmed count and let the loop redo that
  unit honestly, rather than reprocess it and end up with two copies.
- **Sidecar behind its checkpoint**: impossible under this module's own
  write order, so it means something external truncated the file. There is
  no honest way to recompute a resume point from a segment count alone, so
  this **fails loud** with a `RuntimeError` naming the inconsistency and
  telling the operator to Retry from the library, rather than guessing.

Working files (extracted audio, slices) live under
`DOCUMENTS_DIR/<id>/work/` and are retained until success, so a resume skips
the ffmpeg pass. That directory survives only a crashed worker mid-job — the
one case the orphan sweep can requeue with a usable checkpoint; every other
failure purges it, because a manual Retry always starts a fresh job with no
checkpoint and would overwrite it anyway. The store's existing recursive
teardown cleans it for free.

### 8. `on_terminal`, and the stranded-Document invariant

The staging split of §5 created a structural hole. A `rag.ingest` job has a
durable side-effect that **predates the job itself** — the Document row,
already at PENDING before the job was ever enqueued. Three paths reach a
terminal job state without the handler ever running, so none of them reach
`run_ingest_or_fail`'s FAILED write:

1. **cancelled while still queued** (the Queue page's Cancel button);
2. **permanently failed by a second orphan sweep** (a worker died twice);
3. **the handler never started** — an unregistered kind, a bad dotted path,
   or a malformed model-ref dict.

In all three the Document sat at PENDING or PROCESSING forever — and the
Retry endpoint *refuses* a PENDING/PROCESSING row ("already being
processed"), so the document was invisible to the one mechanism that could
have rescued it.

`JobKind` therefore gained an optional `on_terminal` dotted path, registry-
shaped exactly like `planner`/`handler`/`summarizer`, invoked through
`jobkinds.invoke_on_terminal` from all three call sites. `rag.ingest`
registers one; every other kind today correctly leaves it `None`, because
none of them has an external row to strand.

Three properties of the hook, each load-bearing:

- **It runs after the job row's terminal write commits**, scheduled with
  `transaction.on_commit` at all three sites — the orphan sweep runs inside
  the claim transaction's advisory lock, and arbitrary feature-app code must
  never execute inside that window.
- **It writes one conditional UPDATE**, never a read-then-save. Because it
  runs after the commit, a "stale" worker that had not actually died can
  finish and write READY between a read and a write; the
  `status__in=(PENDING, PROCESSING)` guard makes that race impossible, since
  it only ever matches a row still in those states at the instant of the
  write. A zero-row result is told apart honestly: the document is gone
  (logged as a warning), or it already reached a terminal state on its own
  (logged at debug, nothing to fix).
- **It can never break the caller.** Exceptions are caught and logged by
  `invoke_on_terminal`, never propagated back into cancel, the sweep, or the
  worker's own failure writeback — the same never-break-the-page philosophy
  the Queue view already applies to a kind's summarizer.

The invariant this restores, stated plainly: **a `Document` never stays at
PENDING or PROCESSING once the job responsible for it has finished, however
it finished.** Retry is always reachable.

### 9. Caps: upload bytes, media seconds, document pages

Three operator-editable bounds live on `RagSettings`, in ADR 0013 §4's
"operational bound" category — the platform enforcing a limit on its own
resource use, so they ship *with* values and are visibly changeable, unlike
a hardware fact the platform refuses to guess:

- `max_upload_bytes` — checked in the upload view **before a single byte is
  written**, with a rejection message that names the limit and offers the
  inbox as the alternative for a genuinely large file; and by the watcher at
  quiescence, which leaves an over-limit file untouched in the inbox and
  logs it once per `(path, size)` rather than on every poll tick.
- `max_media_seconds` — probed at **stage** time with `ffprobe`, which reads
  container metadata rather than the media payload, so even a multi-gigabyte
  file answers in well under a second. This is the primary enforcement
  point: it rejects before a Document row or a job exists. The transcription
  driver re-checks the same cap defensively at run time, for the narrow
  window where the cap changed between staging and the job running.
- `max_document_pages` — the vision-extraction sibling, at the same stage-
  time position. An image is always exactly one page; **the page cap counts
  rasterized pages only** (W1): for a scanned or mixed PDF, the count is the
  number of TEXTLESS pages (`readers.pdf_textless_pages`), not the
  document's total page count — an ordinary text page never reaches a
  vision model at all, so it never counts against a cap that exists to
  bound per-page MODEL work. An ordinary text-layer PDF (zero textless
  pages) is never subject to it at all.

**A missing `ffprobe` degrades honestly and visibly: the check is skipped
and a warning is logged**, naming the file and saying the cap will not be
enforced for it — rather than failing an otherwise fine upload over a
missing development dependency, and rather than passing silently. The same
shape covers a probe failure while scanning a PDF for textless pages: skip
the check, log it, and let the real parse surface the actual corruption
later as an honest FAILED row instead of an opaque staging crash. A
genuinely unreadable media file is *not* swallowed — that propagates,
because a file the prober cannot read at all is a real problem the
operator needs to see.

**The scan cost, stated plainly (W1).** `readers.pdf_textless_pages`
replaced a whole-file boolean probe (`pdf_has_extractable_text`, retired)
that returned at the FIRST page carrying text — one page, milliseconds,
for an ordinary text PDF. The per-page replacement must instead reach the
END of the document to conclude "no page lacks a text layer": cost is
proportional to *pages before the first textless page* — cheap for a
scanned or mixed PDF, worst-case the WHOLE document for an all-text one.
No `max_pages` scan bound exists to cap that cost: a bound would have to
assign some meaning to the unscanned tail, and both available meanings are
wrong — "assume it has text" silently drops scanned pages (the exact
defect W1 exists to close, reintroduced with a page number attached), and
"assume it is textless" rasterizes pages that never needed it. The scan
stays complete; the cost is named and measured instead of hidden.

**Two scans per ingest of a text-only PDF** (C-08). `ingest._check_document_pages`
(STAGE, a watcher poll tick or an upload request thread) runs the one
`limit=cap + 1` pass and hands its own result forward, in-process, to
`ingest._enqueue_ingest_job`'s call to `_needs_vision_extraction` (ENQUEUE,
the same thread, moments later, nothing touching the file in between) —
so STAGE and ENQUEUE share ONE scan, not two. `ingest.run_ingest_for`'s
own call to `_needs_vision_extraction` (RUN, the worker, a *different*
process, potentially long after enqueue) still re-derives independently
rather than trusting that snapshot — a stale answer there would mean
routing a file that changed on disk since it was staged, which this
codebase never does silently. **Three when the document is actually
routed to extraction** (a scanned or mixed PDF): `media.extract_to_sidecar`'s
own `limit=cap + 1` scan joins the STAGE and RUN scans above, reusing the
bound but re-deriving independently rather than trusting either earlier
snapshot. **Plus `services.reencode_all`'s `limit=1` probe per not-READY
PDF** encountered during a rematerialize (§17) — a separate, bounded cost
paid only in that path, not additive with the per-ingest counts above.

**Measured**, per the verification requirement this item carries: a full
`pdf_textless_pages(limit=cap + 1)` pass (`cap` at its default of 500,
`limit=501`) on `attention.pdf`, the largest PDF available in this
project's own local `data/documents/` at the time of measurement (2.2 MB,
15 pages, all-text — an academic paper, worst case for this cost since no
textless page short-circuits the scan) took **~0.57s averaged over 5 runs**
(0.53s–0.62s) on the development machine. Proportionally that is roughly
35–40ms per page of `pypdf.extract_text()` on this document — noticeable
on an upload request for a genuinely large (hundreds-of-pages) all-text
PDF, not yet a problem at this scale. No larger real PDF was available
locally to measure against the owner's actual worst case; if the owner's
live library holds a substantially larger all-text PDF, this number should
be re-measured against it before the cost is called settled.

An over-cap file that got as far as staging becomes a FAILED Document whose
detail says which limit it hit and that raising the limit and retrying is
the fix.

### 10. Citations carry a locator

A citation now names *where* in a document its text came from: `"file.mp4 at
12:40"` for media, `"report.pdf, p. 3"` for a page — and the page key,
present in the parser since the beginning, finally surfaces.

`retrieval.locator_for` derives the locator from chunk metadata
(`start_seconds` first, then `page`), and **`locator_text_for` is the
single source for the connector rule** — `" at "` before a timestamp, `", "`
before a page. That rule previously existed in two independent copies, in
the Ask page's JavaScript and the History template, with nothing keeping
them in agreement. Both now render `title + locator_text` verbatim. The
raw values are kept alongside the rendered string, for a future deep link.

`core/format.py` is new, and is where `format_timecode` lives — four modules
across a module boundary call it (`modules/rag/retrieval.py` for a citation's
timestamp locator, `modules/rag/sidecar.py` for the transcript page's
`[mm:ss]` headings, `modules/rag/models.py` for the library table's duration
column, and `console/jobs/views.py` for the Queue page's own progress
rendering), which is precisely the condition for a shared leaf in `core/`.
It renders `m:ss` under an hour and **rolls to `h:mm:ss` at an hour**, truncating rather than rounding, and returns `""` for `None`, a
negative, or a non-finite value — because there is no honest timecode for
"unknown", and a raise there would turn a bad timestamp into a 500 on a
rendering path. The module also absorbed three separate copies of the
byte-ladder formatter and three copies of the GB↔bytes conversion, so a
value an operator types round-trips identically everywhere by construction.

### 11. Range/206 on the document file endpoint

Safari refuses to play an inline `<video>` served without range support, and
Django's own `FileResponse` implements none, so `document_file` grew about
25 lines of RFC 9110 handling for audio/video documents. The policy, stated
because the easy version of this is subtly wrong in two directions:

- A satisfiable single byte range is served as **206** with `Content-Range`.
  Both shapes an `HTMLMediaElement` actually sends are supported — the
  open-ended `bytes=N-` and the closed `bytes=N-M` — plus the suffix form
  `bytes=-N`.
- **An end offset at or past EOF is clamped, never rejected.** Players
  speculatively request `bytes=0-<huge>`; a 416 there breaks playback for no
  reason.
- **416 is reserved for the genuinely unsatisfiable case** — a start offset
  at or past EOF — answered with `Content-Range: bytes */<size>`.
- **A range this endpoint does not support is ignored, not refused.** A
  multi-range request, an unknown unit, unparseable syntax, `start > end`,
  or the degenerate suffix forms all fall through to a plain 200, per RFC
  9110 §14.2's "a server MAY ignore the Range header field" — because "I
  don't understand this" and "I understood it and it's out of bounds" are
  different answers and must not share one.

`Accept-Ranges: bytes` is set on every branch for an A/V document, including
the plain 200 path.

### 12. `ffmpeg` joins the one shared image — and what that cost

`ffmpeg` is installed by `apt` into the single shared image, in the same
`RUN` layer as `libpq5`. It is the same category as `libpq5`: codec tooling,
no weights, no runtime downloads. Host-side installation was rejected — the
native-host rule (§2) exists for GPU/model engines, and this is pure CPU
decode. Per-service images were rejected because same-image-different-
command is an ADR 0006 §6 invariant, and both the watcher (duration probe at
stage time) and the worker (audio extraction and slicing) need the binary.
No `ffmpeg-python` wrapper dependency: every invocation is a short,
hand-written argv list.

**The cost, measured rather than estimated** — two independent
measurements on the project's own images (arm64, Debian trixie,
`--no-install-recommends`), taken 2026-08-24:

| Measurement | Method | Result |
|---|---|---|
| Image layer growth | `docker image history` on the pre-media image vs. the media image, comparing the one `apt-get install` layer | **4.2 MB → 422 MB** (`libpq5` alone → `libpq5 ffmpeg`) — **+418 MB** |
| Installed package closure | `dpkg-query -W -f='${Package}\t${Installed-Size}\n'` in both containers, diffed; sizes of packages present only in the media image, summed | **196 new packages, 417,195 KB ≈ 407 MB** (the `ffmpeg` package itself is 2,836 KB ≈ 2.8 MB; the remainder is its codec/library dependency closure) |

Both numbers describe the same thing from two directions and agree to within
rounding. **The honest figure to quote is roughly 410–420 MB, and ~99% of it
is the dependency closure, not the `ffmpeg` binary.** That is a large,
real cost for an appliance image, accepted deliberately: it buys every
container format the platform accepts, on every service that needs one, with
no second image to keep in sync.

### 13. The licensing asymmetry is deliberate

This platform is dual-licensed with a copyleft core (ADR 0002/0003), so a
dependency's license is a business decision, not a preference. The rule this
build applied, and the reason two dependencies with different licenses were
treated differently:

- **In-process code must be permissively licensed.** PDF rasterization uses
  `pypdfium2` + `Pillow` — both permissive. **`pymupdf` was rejected on AGPL
  grounds**, despite being the more capable library, because it would be
  linked into the platform's own process and its license would reach the
  dual-licensing model.
- **A subprocess tool is an arm's-length packaging question.** `ffmpeg` is
  invoked as a separate process over an argv list; it is a tool the image
  ships, not a library the code links. Its licensing is a *distribution*
  question for the appliance image, which is a different question from
  what is compiled into the product.

Recorded here so a future contributor does not read the pair as an
inconsistency and "fix" one of them.

### 14. The `media` feature flag gates NEW intake only

`"media"` joins `FARABUNKER_FEATURES` alongside `"vision"`, using ADR 0012
D9's pattern. It gates: registration of the two new roles, the media
extensions in `supported_exts()`, the scanned-PDF auto-detect, and the
duration cap.

It deliberately does **not** gate the run half. A Document already staged as
video or audio keeps working through the planner and the run half regardless
of the flag's current value — so turning `media` off never strands
already-ingested content, never breaks `reencode_all`, and never blocks a
Retry. **Only new intake stops.**

`supported_exts()` is a function rather than a frozen module constant, so
a flag change (including a test's override) takes effect on the very
next call.

**The cross-process divergence window is real and is documented rather than
papered over.** The watcher/web process reads the flag when it stages; the
worker process reads it again, independently, when the job runs. There is no
atomic "the flag was X at enqueue time" fact shared between them. Flipping
the flag while a scanned PDF is mid-queue produces one of two outcomes, both
read as "the operator changed their mind mid-flight" rather than as races
the codebase must prevent:

- **off at enqueue → on at run**: only the embed role was reserved, but the
  job now routes through vision extraction anyway — so it runs a real
  extraction workload the scheduler never admitted room for, and the
  admission math under-accounts that job for its whole run.
- **on at enqueue → off at run**: the extraction role *was* reserved, but
  the scanned PDF now falls through to the ordinary text parse, finds no
  text, and produces a document with zero chunks — silently, since nothing
  errors.

Making the flag part of the payload snapshot, or re-validating it at claim
time, is real future work this ADR flags rather than builds.

### 15. `doc_type` is the shape of the content; `media_type` is the source

`Document.doc_type` stays `PROSE`/`TABULAR`, and every medium in this build
that produces searchable text is `PROSE`. `media_type` (a MIME string, the
same column shape ADR 0012 D6 established for generation records) is what
the **source file** was.

This is not a redundancy. `doc_type` answers "what shape is the queryable
content" — which is what `reencode_all`'s prose filter and the retrieval
path both need. A `MEDIA` value in `doc_type` would silently exclude every
transcribed video from re-encoding, because that filter would stop matching.
The library's Source badge, duration line, and Transcript link derive from
the other fields instead: `Document.source_kind` reads `media_type`'s MIME
prefix first ("Video"/"Audio"/"Image"/"PDF") and falls back to `doc_type` for
the "Table" case, which `media_type` alone cannot name (it is ambiguous
between `text/csv` and the xlsx spreadsheet MIME type); `duration_display`
renders `duration_seconds`; and `has_transcript`, which gates the Transcript
link, is a filesystem stat of that document's own `extract.json` rather than
a database flag.

Alongside them: `status`/`status_detail` (§5), `duration_seconds`, and
`extraction` — a five-key snapshot of *how* this document's text was
produced (method, engine, model id, connection, timestamp), a snapshot
rather than a foreign key for the same reason `AskRecord` snapshots its own:
the binding it describes can change afterwards, and the record should not.
`status` defaults to `READY`, so the migration needed no data backfill —
every pre-existing document was, by definition, already ingested.

### 16. The metadata-pollution fix is forward-improving

A pre-existing bug, found on the way through and fixed for **every** document
type rather than only for media: no exclusion lists were set anywhere, so
LlamaIndex's default metadata mode was prepending `"file_id: 42\nfile_name:
notes.pdf\nsource_path: …"` onto every chunk's own text *before embedding it
and before sending it to the LLM*. Every prose chunk this platform has ever
indexed carried that noise.

`media.apply_chunk_metadata_exclusions` sets both exclusion lists over the
full fixed key vocabulary, applied to the `Document` before splitting and
re-asserted on each node after — belt-and-suspenders on purpose: the
installed splitter *does* propagate the parent's exclusions, and this
codebase does not want to depend on that being every splitter's behavior
forever.

**The fix is forward-improving only.** Chunks written before it keep their
old, polluted metadata, so a library upgraded onto this build is honestly
mixed — old chunks polluted, new chunks clean — until an operator runs one
re-encode (Inference console → the embeddings role → re-encode). **A
re-encode is recommended, not required**: retrieval keeps working either
way, it just works better afterwards.

### 17. A related correction: a READY document is never skipped by a re-encode

`reencode_all`'s skip guard is now reached **only** when `status != READY`.
A READY document is never probed and never skipped. The prior version
re-probed every PDF for a text layer and skipped it when the probe came back
empty — which, for a READY text-layer PDF whose first probed pages happen to
be image-only (a cover page, a scanned appendix at the front), dropped its
chunks on every single re-encode and never reinserted them.

Inside the not-READY branch the skip stays narrow, because "not READY" alone
does not mean "mid-extraction". A not-READY document is skipped only when
its `media_type` starts with `video/`, `audio/`, or `image/`, or when it is
an `application/pdf` whose per-page probe (`readers.pdf_textless_pages`,
called with `limit=1` — the one file probe left, and reached only for a
not-READY PDF) comes back NON-EMPTY, i.e. the PDF still has at least one
page with no text layer of its own. Every other not-READY document
falls through to the ordinary per-document `try`/`except` below — and so
does a PDF whose probe *raises* on a corrupt or encrypted file: that is
caught and fails open to not-skipping, so one bad file fails like any other
bad document instead of aborting a rebuild that has already dropped the
chunk table.

The READY half holds by construction: a document only reaches READY after
its chunking succeeded, which means its text — from a parse, a transcript,
or an extraction — already exists and is safe to re-chunk. A document the
not-READY branch does skip is logged, and will be re-encoded once its own
ingest finishes.

### 18. What retrieval actually does today — and the named gaps

**Retrieval is an exact scan, not an approximate one.** No ANN index exists
anywhere in this codebase: the vector store is created without any
index-building parameters, and no migration or SQL statement creates an
`hnsw` or `ivfflat` index on the chunk table. pgvector without an index
performs an exact sequential scan of every chunk on every question. At the
scale this platform runs at that is correct and simple; it is recorded here
so a future performance conversation starts from the fact rather than an
assumption, and so "add an index" is a measured decision rather than a
reflex. `similarity_top_k` is now `RagSettings.retrieval_top_k`, an operator
knob (W4); hybrid search (W5, below) adds a *second* exact scan — Postgres's
own GIN-backed keyword search — never an ANN one.

The gaps this build ships with, named so they read as scoped-out rather than
forgotten:

- **A zero-footprint job class.** A genuinely model-free job (tabular
  ingest) is treated as effectively exclusive by ADR 0013 §4's rule. An
  ADR 0013 amendment, not solved here (§4).
- **Per-page PDF routing — shipped (2026-08-24), item W1.** A PDF with any
  textless page now routes through vision extraction (no threshold — a
  stray blank separator page costs one vision call and is then absent from
  the index, which is correct) and reserves `rag.extract`; the document's
  own text pages are read and merged back in at index time
  (`ingest._source_documents`), never re-sent through vision, and the page
  cap counts rasterized (textless) pages only (§9). With no vision model
  bound, the document ingests its text layer alone and says so honestly in
  its Ready chip (`status_detail`) rather than failing the whole document
  or the enqueue outright (`media.ModelRoleUnavailable`, a `.pdf`-only
  fallback — an image still fails honestly, having no text layer to fall
  back to). **Operator remediation for a PRE-W1 mixed PDF:** it was
  ingested with no sidecar at all (`reencode_all` never runs extraction, so
  no rebuild will ever create one) and its scanned pages are still missing
  from the index — press **Re-ingest** in the library (`document_reingest`
  → `enqueue_reingest` — this row is READY, so the library's Retry form
  renders the "Re-ingest" label for it, W1 review MAJOR 1), which re-derives
  the medium and actually runs extraction this time. This is not automated (finding
  every mixed PDF already in the library would mean scanning every stored
  PDF — the same unbounded cost §9 names); a library hint flagging
  "may have unextracted pages" is a reasonable follow-up, not built here.
- **Hybrid keyword + vector search — shipped (2026-08-25), item W5.**
  `RagSettings.hybrid_search` (default **off**) is a single toggle; there is
  no `indexed_metadata_keys` (evaluated and cut — the `file_id` btree would
  be dead on arrival against `_build_filter_clause`'s `::float` cast, and
  the `category` one is unverified without an EXPLAIN spent on a rebuild
  not yet justified).
  **One shape rule, not three independent flags:** `modules.rag.index.
  get_vector_store()` always builds a store that describes the chunk table
  that *actually exists* (`live_store_shape()`, one `pg_attribute` lookup —
  `None` only when nothing has been ingested yet, which is *not* a rebuild
  trigger) and falls back to the toggle's `desired_store_shape()` only for
  a table that doesn't exist. `use_jsonb` rides the same toggle as
  `hybrid_search` rather than being its own knob, so an install that never
  touches this toggle can never be surprise-rebuilt by an unrelated
  embeddings-drift rematerialize. A toggle flip therefore changes **nothing**
  about what's queried until an operator re-encodes (Inference → rag.embed
  → Re-encode), which is what makes the toggle's own flash copy literally
  true. `reencode_all` gained a second, independent rebuild trigger
  (`shape_changed`, alongside the existing dimension-mismatch one) so a
  rebuild-then-forget install still gets picked up on the next
  rematerialize.
  **No fusion.** The installed store (`llama-index-vector-stores-postgres`
  0.8.1) runs the dense (embedding) query and a Postgres keyword query
  (`tsvector`/`ts_rank`, **not** BM25, **not** a sparse embedding model)
  independently, concatenates them, and dedups by chunk — up to
  `top_k + sparse_top_k` (`sparse_top_k = top_k`) results, never a weighted
  blend; `alpha` is unsupported by this store and is never passed. Because
  a dense cosine similarity and a raw `ts_rank` sit on incompatible,
  unnormalized scales, **W4's score floor is not applied while the live
  store is hybrid** — applying it to a mixed list would silently delete
  every keyword-only hit, the exact result hybrid exists to surface.
  Enabling the toggle re-checks the context-window fit check `top_k`
  already gets, against **double** the stored `top_k` (a hybrid query can
  return twice as many chunks). `text_search_config` is pinned to
  `"english"` — the offline-first posture: a Postgres-builtin dictionary,
  no download, no operator setup — stemming is English-only, non-English
  text still matches on exact tokens. The exact-scan truth above is
  unchanged: hybrid adds a *second* exact (GIN-backed) scan, not an ANN
  one. **Pre-W1 mixed PDFs are not repaired by this rebuild** — a rebuild
  re-embeds from each Document's retained text, it never re-runs
  extraction, so the same operator Re-ingest remediation the W1 bullet
  above names still applies to them.
- **A retrieval-only search page — shipped (2026-08-25), item W6.**
  `GET /rag/search/?q=…&category=…` (`rag-search`) is the score floor's
  visible surface: no LLM, no queue, no `AskRecord`. `modules.rag.
  retrieval.answer_question`'s own retrieve-then-filter step was split
  into two shared functions — `retrieve_nodes` (embed model, vector
  store/index, retriever construction: top_k, hybrid-if-live, category
  filter) and `apply_score_floor` (the W4 floor, suspended under hybrid
  same as W5) — used identically by `answer_question` and the search
  page, so the two surfaces can never silently disagree on what counts as
  a match. Each result shows the document title (linked to the file), its
  locator when the chunk has one (page/timestamp, the same `locator_for`/
  `locator_text_for` connector rule Ask/History already use), the raw
  score, and an honestly-attributed label — "cosine similarity" only when
  the live store is dense-only (every result really did come from that
  one query); under hybrid, the installed store concatenates dense +
  keyword hits with no per-result tag naming which arm produced a given
  hit, so guessing per node would be dishonest — every result gets the
  neutral "relevance score" label instead. The matched chunk text is
  escaped, whitespace-preserved, and capped to ~1200 characters. Retrieval
  runs SYNCHRONOUSLY in the request, a deliberate divergence from every
  queued job kind in this build: a search is one embedding call plus one
  SQL query, holds no LLM, and the embed model it needs is already
  resident for every other RAG surface on this install — queuing it would
  add latency and a poll loop for work that costs less than the health
  check `AskView`'s own pre-check already pays inline. The `rag.embed`
  role is pre-checked alone (never paired with `rag.answer`, since this
  page has no answer-role opinion); unbound/unreachable renders the
  existing `model_unavailable_message` copy inline, and a runtime
  embedding-call exception surviving that precheck is caught and rendered
  the same way — both 200, never a 500 or 503 (this page has no queue to
  fail into). A query longer than 2000 characters is rejected before any
  model call — a NEW bound specific to this page (Ask has no analogous
  cap today), justified by retrieval running synchronously here with no
  queue between an unbounded query string and the request thread.
- **A `--queue` flag for the CLI.** `manage.py ingest` still calls the run
  half directly, joining the recorded bypass class ADR 0013 §8 names — that
  bullet names `manage.py ask` and `manage.py reencode`, not ingest, and the
  `--queue` flag it calls a named fast-follow is still not built.
- **No `should_stop()` cooperative cancel.** Cancel reaches a job only while
  it is still QUEUED: `console.jobs.backend.cancel_job` is one conditional
  `UPDATE` filtered on `state=QUEUED`, so a job a worker has already claimed
  is left running and the operator is told exactly that — the outcome comes
  back as `"already_running"` and the Queue page says "That job had already
  started — it will run to completion." A running transcription or
  extraction has nothing to poll that would ask it to stop, and that seam is
  the gap. Work is still not lost to a worker *shutdown*, which is a
  different path: `Worker._drain_inflight` requeues an in-flight job and it
  resumes from its last checkpoint, so drain loss is bounded by the
  checkpoint cadence — judged good enough for v1.
- **Fixed 300-second slices, no overlap.** Deduplicating overlapped
  transcribed text would be a guess, so a slice boundary landing mid-word is
  an accepted and documented cost. Silence-aware slicing is the upgrade
  path, and the window is already a parameter.
- **Model-upgrade re-extraction needs a re-ingest** (§6), because the
  sidecar is keyed on the file's hash. A `--force` re-ingest is the missing
  piece.
- **Tabular data is stored but not searchable — shipped (2026-08-25), item
  W2.** ADR 0005's text-to-SQL routing remains deferred (no retrieval code
  path changed); the UI now says so plainly everywhere an operator would
  notice a tabular file: the Document library's "Table" source badge
  carries an honest sub-line ("Stored — rows are not searchable yet."),
  the Ask page shows a one-line note ("N table file(s) in the library
  aren't searched — tables are stored but not part of retrieval yet.")
  server-rendered ONLY when the library holds at least one tabular
  Document, and a tabular upload's own success flash names the same fact
  per-file ("{name} stored as a table; not searchable yet.").
- **No backup/restore procedure — shipped (2026-08-25), item W3.** See
  `docs/OPERATIONS.md`. `manage.py backup <dest> [--dump PATH]` (`console.ops`,
  a new platform app owned by no feature module) copies `DOCUMENTS_DIR` +
  `GENERATED_DIR` and folds in a `pg_dump --format=custom` file the operator
  hands it; `manage.py restore <src>` verifies and mirrors the files back,
  then verifies and prints the `pg_restore` command. Three decisions worth
  recording: (a) **the split is structural, not a choice** — `pg_dump`/
  `pg_restore` live only in the `db` image (`pgvector/pgvector:pg16`), never
  in `web`/`worker`/`watcher` (`python:3.12-slim` + `libpq5` + `ffmpeg`), so
  the command owns the file half and the manifest, prints the DB half's
  commands rather than running them, and refuses to claim
  `manifest.complete` without a dump; (b) **`dumpdata`/`loaddata` was
  rejected** — it cannot carry the pgvector chunk table's column type
  sanely, gives no transactional consistency, has FK-ordering hazards, and
  is orders of magnitude slower than `pg_dump -Fc` on a real chunk table;
  (c) **the residual window**: `web` keeps serving between the `pg_dump` and
  the file walk, so an upload lands as an orphan file (harmless) and a
  delete leaves a dangling database row (a 404'ing `document_file` link) —
  `web` is stopped too, alongside `worker`/`watcher`, for a strictly
  consistent backup and for the whole restore (it holds pooled connections
  up to `conn_max_age=600`, is `restart: unless-stopped`, and its own
  auth/session tables are inside the dump).
- **Operator-editable extraction prompt** (§3), **speaker diarization**, and
  **per-segment deep links into a player** are all out of scope.

### The approved product wave

Six items are approved to follow this build, in this order, with **no dates
committed**:

| # | Item |
|---|---|
| W1 | Per-page PDF routing — partition a mixed text/scan PDF and vision-extract only the pages that need it — **shipped (2026-08-24)**; see §18 and `modules/rag/README.md` |
| W2 | Tabular honesty — the library and Ask copy say plainly that tabular data is stored but not searchable — **shipped (2026-08-25)**; see §18 |
| W3 | A backup/restore story — a documented procedure and a `manage.py` pair, leading with originals-plus-dump reproducibility — **shipped (2026-08-25)**; see `docs/OPERATIONS.md` |
| W4 | A retrieval score floor, an operator-tunable `top_k`, and citation dedup — **shipped (2026-08-24)**; see `modules/rag/README.md`'s retrieval paragraph |
| W5 | Hybrid keyword + vector search — **shipped (2026-08-25)**; see §18 and `modules/rag/README.md`'s retrieval paragraph |
| W6 | A retrieval-only search page — no LLM, no queue; the score floor's visible surface — **shipped (2026-08-25)**; see §18 and `modules/rag/README.md`'s "Search page" section |

Recorded keep-outs, so they are not re-litigated: reranking; an ANN index
before measurement (§18); reviving text-to-SQL retry; multi-hop retrieval;
PPTX/EML/URL ingestion; a tunable chunk size; a mobile redesign; queue
dashboards; and multi-turn chat, which belongs to Phase 1.6.

## Consequences

- **RAG ingests four new mediums**, and ADR 0005's OCR/voice/non-document
  deferral is closed. A scanned PDF that used to produce zero chunks
  silently now produces cited, page-located text.
- **Every ingest is a queued job.** The watcher and the upload view are
  enqueuers now, not pipelines — the watcher thread no longer runs ingestion
  inline, and an upload request returns as soon as the file is staged.
- **Every job kind in the platform can report progress and resume**, because
  the handler signature changed once for all of them (§7). This is the
  contract every future kind is written against; there is no un-`ctx`'d
  variant left to write.
- **The image grew by roughly 410–420 MB** (§12) and now requires a
  **rebuild**, not a restart, to deploy — `ffmpeg` is baked in, so the
  bind-mounted code path that normally suffices does not carry it.
- **One Python dependency was added** (`pypdfium2`) and one was promoted
  from transitive to first-party (`Pillow`, pulled in by other packages
  already, now pinned in `requirements.txt` because
  `modules/rag/transcode.py` imports it directly). Both are permissive by
  deliberate selection, and `pymupdf` was rejected on AGPL grounds (§13).
- **A new host service joins the operator's setup**: the transcription
  server, installed and started per its own `/setup/` page — which, per
  ADR 0012's engine-declared-setup-guide ruling, is generated from the
  adapter itself and needed no template change to appear.
- **An upgraded library is honestly mixed** until one re-encode runs (§16),
  and a re-encode of transcribed media is cheap because the sidecar makes it
  model-free (§6).
- **Turning `media` off is safe for existing content and unsafe to do
  mid-queue** (§14) — the second is documented, not prevented.
- The stranded-Document class of failure is closed by construction (§8), and
  every failure state a document can reach is reachable by Retry.

## See also

- `modules/rag/transcode.py`, `modules/rag/media.py`, `modules/rag/sidecar.py`
  — the layering law (§1) is restated in each of their own module
  docstrings, at the file a contributor is editing.
- `core/inference/jobkinds.py` — `JobContext` and `JobKind.on_terminal`, each
  documented in depth beside the code (§7, §8).
- `core/inference/engines/whisper.py` — the adapter, including which of its
  HTTP assumptions were verified against a live server and which remain
  speculative for other builds of that server (§2).
- [ADR 0005](0005-rag-module-architecture.md) — the RAG module, amended below
  where its OCR/voice deferral is lifted.
- [ADR 0006](0006-containerization-and-isolation.md) §6 — the shared image,
  amended below where `ffmpeg` joins it.
- [ADR 0013](0013-inference-execution-queue.md) §8 — the queue's deferred
  progress seam, amended below where this build lifts it.
- [ADR 0012](0012-image-generation-engine-adapter.md) — the native-host
  engine pattern, the engine-declared `SetupGuide`, the feature-flag rule
  (D9), and the content-agnostic stance this build follows.

## Amendment (2026-09-14) — B-5, round-3 hardening (H28): the page cap moves off the request thread

§9's "same stage-time position" claim for `max_document_pages` no longer holds. It
described the STAGE-time page-cap check as sharing `max_media_seconds`'s primary
enforcement placement — before a `Document` row exists, on whatever thread staged the
file. For the duration cap that placement is fine: `ffprobe` reads container metadata,
not the payload, so even a multi-gigabyte file answers in well under a second. The page
cap's own scan has no equivalent shortcut — "The scan cost, stated plainly" above
already named the real cost (a full `pypdf.extract_text()` pass over every page of an
ordinary all-text PDF, the common case, not merely the scanned one this cap is about) —
and a security review round found that cost unbounded and running INLINE, on the HTTP
upload request itself: nothing capped pages EXAMINED (only pages COLLECTED, via
`limit`, which does nothing when nothing is ever collected), and nothing offloaded the
work. Reproduced against this branch's own reader: a hand-built 5,000-page all-text PDF
returned zero textless pages after 7.82s of CPU, on the same thread that owed the
browser a response. The framework's own request-body/file-count cap (a related,
independent fix, S9) bounds the bytes of that POST; it does nothing about the CPU this
paragraph is about.

**The fix.** The page-cap decision — `tools.rag.ingest._check_document_pages` — no
longer runs from `stage_document` at all. Staging now does exactly what its own module
docstring always said it should: make the row and the file exist, nothing else. The
SAME check (same operator-facing message, `tools.rag.media.page_cap_message`, verbatim)
now runs at the very top of a `rag.ingest` job's RUN half (`run_ingest_for`) — the queue
worker, or the CLI's own synchronous process, never a web request — the exact placement
§9's own text already gave `max_media_seconds`'s DEFENSIVE run-time re-check
(`tools.rag.media.transcribe_to_sidecar`). The vision-routing decision that used to
share this same scan at enqueue time (`_needs_vision_extraction`, called from
`tools.rag.ingest._enqueue_ingest_job` — itself also reachable from the request thread,
on both the initial upload and a Retry) is REMOVED from there entirely, for the same
reason: an all-text PDF costs the same full scan to answer "does this need vision
extraction" as it does to answer "is this over the page cap" (the SAME
`readers.pdf_textless_pages` call, W1's own "one scan, two uses" design). Both
questions now share one scan, once, inside `run_ingest_for` — `_enqueue_ingest_job`'s
payload simply carries `medium_for`'s extension-only answer ("prose" for every `.pdf`,
scanned or not); `run_ingest_for` already independently re-derives the real routing
answer regardless of what the payload says (the CROSS-PROCESS FLAG DIVERGENCE WINDOW
paragraph this ADR's code already documents), so actual ingestion is unaffected. What
changes is `tools.rag.jobs.plan_ingest`'s admission accounting: it reserves `rag.embed`
only, never `rag.extract`, for a `.pdf` at enqueue time now, regardless of whether it
turns out to be scanned — the same accepted-risk shape that divergence paragraph
already named for an operator flipping the "media" flag mid-queue, just no longer
confined to that one edge case, and backstopped the same way (`models/queue/
scheduler.py` rule 2(b): an unmeasured footprint is treated as effectively exclusive).

**What an operator sees differently.** An uploaded scanned/mixed PDF (or image) over
the cap used to be refused inline, at upload time, with nothing written to the
database. It is now accepted like any other upload ("Queued N file(s)"), and fails
shortly after, as an ordinary FAILED document row — the cap named in `status_detail`,
the same message as before, Retry-after-raising-the-cap working exactly as it does for
any other FAILED row. This is the SAME trade the duration cap's own defensive re-check
already made peace with: the primary/only enforcement point moved off a thread that
owes somebody an immediate answer.

**The scan itself gained a bound it did not have.** `readers.pdf_textless_pages` took a
new keyword, `max_examined`, bounding pages EXAMINED rather than merely pages
COLLECTED — returning `(pages, truncated)` instead of a bare list when given. No caller
in this codebase passes it: the queued job and the CLI's own process are both allowed
to take as long as an honest answer needs, and after the change above there is no
request-thread caller left to need a bounded one. It exists so a FUTURE caller that
genuinely must answer inline has an honest way to give up rather than reintroducing
this finding — see that function's own docstring.

**"Two scans per ingest of a text-only PDF" is retired.** §9's own "Two scans per
ingest of a text-only PDF (C-08)" paragraph described STAGE and ENQUEUE sharing one
scan via an in-process hand-off (`probe_out`). Neither STAGE nor ENQUEUE scans at all
anymore, so there is nothing left to hand off — that plumbing (the `probe_out`
parameter on `stage_document`/`_enqueue_ingest_job`) is removed. The RUN half's own
scan (now doing double duty for the cap and the routing decision, in one call) and
`media.extract_to_sidecar`'s own defensive re-check (unchanged by this task) are what
remain — a text-only PDF is now scanned once per RUN attempt, not twice per upload.

## Amendment (2026-09-14) — H28 review round 1: the ceiling is applied, and the vision role is reserved pessimistically

Three claims in the amendment just above did not survive review and are corrected here
— the previous amendment is left as written above (an accurate record of what H28's
first cut actually shipped), not silently edited.

**Claim: "No caller in this codebase passes [`max_examined`]."** No longer true.
`readers.PDF_SCAN_MAX_PAGES_EXAMINED` (a new module constant, `2000` — four times
`RagSettings.MAX_DOCUMENT_PAGES_DEFAULT`'s own `500`, generous headroom for a
legitimately large all-text document while still bounding one worker's worst-case CPU
at roughly 3s, against this ADR's own §9 measurement of ~1.56ms/page) is now the
DEFAULT `max_examined` `run_ingest_for` passes to `_check_document_pages` — the queued
job gets it automatically; the CLI's `ingest_path` explicitly overrides it to `None`
(unbounded — an operator running a terminal command has already chosen to wait). When
the scan is truncated (the ceiling is reached before the cap decision can be made
honestly either way), `_check_document_pages` now FAILS CLOSED: it refuses the document
with `media.page_scan_ceiling_message`, naming the ceiling and the page count examined
— distinct wording from the ordinary over-cap refusal, since "inconclusive" and "over
cap" are different facts an operator needs told apart (the fix is different: trim/split
the file, or accept the cost of a full scan by raising the cap, versus simply raising
the cap).

**Claim: "`_enqueue_ingest_job`'s payload simply carries `medium_for`'s extension-only
answer ('prose' for every `.pdf`, scanned or not)."** No longer true. Review round 1
found this too permissive: with `payload["medium"]` always `"prose"`, `plan_ingest`
reserved `rag.embed` ONLY, never `rag.extract`, for EVERY `.pdf` at enqueue time — not
merely the rare flag-flipped-mid-flight case this ADR's own code already accepted as a
named risk (§4, `_needs_vision_extraction`'s "CROSS-PROCESS FLAG DIVERGENCE WINDOW"),
but the ordinary case, on every genuinely-scanned PDF, permanently. `_enqueue_ingest_job`
now declares `"pdf-scanned"` for every `.pdf` while `"media"` is enabled — PESSIMISTIC,
off the extension alone, still no scan — so `plan_ingest` always reserves both roles
for a `.pdf`; an ordinary text-layer PDF reserves capacity it will not use, in exchange
for a genuinely-scanned one never being under-provisioned. See §4's own table note,
above.

**Claim: "backstopped the same way (`models/queue/scheduler.py` rule 2(b): an
unmeasured footprint is treated as effectively exclusive)."** This was wrong on its own
terms, independent of the fix above: rule 2(b) only serves as a backstop when EVERY
role a job declares is unmeasured. `rag.embed` — declared by the `"prose"` branch on
every ingest, including every one of these under-reserved PDFs — frequently reports a
real footprint once an operator has bound a real embedding model, which is the
ordinary, intended operating condition, not an edge case. A job admitted on a measured
`rag.embed` alone, that then also runs `rag.extract` at run time, is precisely the
under-accounted-concurrency hole this ADR's own module docstring (§4) warns
`plan_ingest`'s honesty contract exists to avoid — rule 2(b) does not reach it. The fix
above (reserve pessimistically) is what actually closes this, not the scheduler rule.

## Amendment (2026-09-17) — preview UAT: a second prompt, for images only

`EXTRACTION_PROMPT` ends *"If the image contains no text, output nothing"* —
correct for a page of a scanned contract, and exactly wrong for a photo. A
textless image produced an empty sidecar, so a chat attachment never got a
description and the attachments block said "description not ready yet"
forever; the chat model read that and told the operator to keep waiting for
work that had already finished. `DESCRIPTION_PROMPT` is its sibling constant,
under the same platform-behaviour rule the paragraph above states, and
`describe_image` its sibling call. **Images only** — a scanned PDF's page loop
never asks for one, and the transcription prompt and its stored text are
unchanged, byte for byte, so no existing document's OCR or citations move.

Two calls rather than one combined prompt, deliberately: a single call
returning both would need a delimiter the model must obey and a parser for its
output, against this ADR's own **model output is stored verbatim** rule. The
cost is one extra call per *image*, never per page.

The sidecar grows two backward-compatible things, both image-only: a first
segment `{"page": 1, "kind": "description", "text": …}` before the
transcription segment, and a top-level `"described": true`. Every existing
reader consumes both unchanged — `documents_from_extract` branches on `"page"
in segments[0]` and therefore **embeds the description** (so retrieval can find
a photo by what is in it), and a sidecar without the keys behaves exactly as it
always did. `extract.json` is keyed on the source file hash and is never
re-produced for an already-ingested document, so both shapes are live on a box
at once, permanently, by design.

`"described"` exists to keep one distinction honest: a finished sidecar with no
segments **and** the marker means "we looked and there was nothing to say"; one
with no segments and **no** marker means "this was extracted before
descriptions existed". Those are different sentences in front of an operator,
and without the marker they are indistinguishable. **No automatic re-ingest** —
existing images keep whatever their OCR produced, and re-ingesting one from the
library is what gives it a description.

## Amendment (2026-09-17) — review fix round 2: the remedy above did not work

The amendment above promised that re-ingesting an `"undescribed"` image from the
library gives it a description. It did not: `enqueue_reingest` re-hashes the
SAME retained file and deletes nothing, so `_load_finished_sidecar_if_matching`
(reuse-decision helper, `tools/rag/media.py`) matched on `produced_at`/
`source`/`source_sha256` exactly as it does for any unchanged file, and the
OLD, undescribed sidecar came back verbatim — `describe_image` never ran, and
the remedy sentence pointed nowhere.

The fix is narrow and lives one layer up, in `_reuse_finished_sidecar` (the
short-circuit HEAD both drivers share, not the hash-matching helper itself):
`extract_to_sidecar`'s image call now passes `require_described=True`, so a
matching-by-hash sidecar that lacks the top-level `"described"` marker is
treated as NOT reusable — the driver falls through and re-extracts, describing
the image for the first time and rewriting the sidecar with `"described":
true`. A PDF's page sidecar (`require_described=False`, the unchanged default,
and whisper's own transcription reuse never had this parameter at all) is
still reused exactly as before: neither ever carries `"described"`, and
requiring it there would make every PDF re-ingest redo a full, pointless
vision pass. The paragraph above's own "`extract.json`... is never re-produced
for an already-ingested document" is therefore no longer exactly true for this
ONE case by design — it is precisely the case this amendment exists to make an
exception for.
