# Chat attachments — design

> **STATUS:** historical: superseded in part; landed as the integration in `2026-09-20-attachments-integration-land.md`.

**Date:** 2026-09-03 · **Status:** approved in brainstorm, awaiting owner spec review
**Base:** main 4abef8a · **Zone:** agents (models, runtime, chat), one contracts extension, one rag tool extension

## Goal

`/chat/` turns accept file attachments — every type the RAG ingest stack
handles (images, PDFs, office docs, audio, video) — and the agent can both
**understand** an attachment (its content reaches the model) and **operate
on** it (hand it to tools: img2img a photo, ingest a PDF into the library
on request).

## Owner rulings (this brainstorm, 2026-09-03)

1. **Both** understanding and tool-operand — not one or the other.
2. **Everything RAG ingests** — not an images-only slice.
3. **Conversation-scoped, on the existing file structures** — attachments
   live with the conversation in the managed-store shape (ADR 0009), never
   auto-ingested into the library; adding to the library stays an explicit
   act. Deleting the conversation deletes its files.
4. **Design the end state; slowness is transient, never an architectural
   input.** Native multimodal is part of the v1 contract (capability-gated),
   not a deferred upgrade. Extraction runs inline in the turn job (the
   simplest end-state design) even though whisper on long video is slow
   today; the mitigations (sidecar cache, honest progress labels) are cheap
   and remain correct at any speed.

## Design

### 1. Data model (`agents/models.py`, one migration)

`ConversationFile`:

- `conversation` FK, `on_delete=CASCADE`, related_name `files`
- `path` (managed store copy), `original_name`, `media_type`, `file_hash`
  (sha256, indexed — the sidecar cache key), `size_bytes`, `created_at`
- owner columns stamped via `identity.ownership` at create (IA-1 binding;
  the route-matrix suite catches misses)

Bytes live at `DATA_DIR / "conversations" / <conversation.id> / <file.pk>
/ <basename>` — the `DOCUMENTS_DIR/<doc_id>/<basename>` shape (`Conversation.id` is already a UUID pk) (`tools/rag/
store.py`, ADR 0009), under a new `CONVERSATION_FILES_DIR` setting beside
`DOCUMENTS_DIR` (`config/settings.py:179-180`). A small `agents/store.py`
(or equivalent) owns the layout the way `tools/rag/store.py` owns the
document store; nothing else touches the paths directly.

Deleting a conversation removes rows (CASCADE) and disk files (the
`delete_document` precedent: filesystem cleanup in the service delete path,
never a signal). The existing conversation-delete disclosure copy gains
"and its attached files".

**Migration numbering:** check `agents/migrations/` on the branch base
against any in-flight vision/agents branches before numbering (the vision
0006 lesson).

### 2. Upload path (`agents/chat`)

- `#turn-form` becomes `enctype="multipart/form-data"` with
  `<input type="file" name="attachments" multiple>` — a plain form field,
  so the no-JS POST path works unchanged. The XHR submit handler already
  sends `new FormData(form)`; files ride along with zero JS changes to the
  transport.
- `turn_create` validates (per-file size cap, count cap, extension against
  the RAG-ingestible set single-sourced in `tools/rag/readers.py` —
  imported, never retyped), stores each file via the store helper, creates
  `ConversationFile` rows, and stamps `file:<id>` refs into the USER turn's
  `artifacts`.
- Rejections are honest 400s through the existing `_form_errors.html`
  fragment / `#turn-errors` slot; a rejected upload never creates rows or
  files (validate before store; clean up on partial failure).
- Caps are settings with sane defaults (e.g. 10 files/turn; size cap
  generous enough for video), declared once in Python.

### 3. Understanding — prompt side (`agents/runtime`)

At turn-job execution, for each `file:` ref on the user turn being
answered:

- **Capability gate first (end-state rule), media-type-generic:** if the
  resolved `chat.converse` model's capability says it accepts THIS
  attachment's media type natively, the bytes go into the user
  `ChatMessage` as native content (LlamaIndex message blocks; the Ollama
  adapter's multimodal path). Today that means images on a multimodal
  model; an audio- or video-native chat model later slots into the same
  gate with zero design changes — extraction below is the universal
  fallback, never the definition. The capability answer comes from the models column (catalog/
  resolved-model metadata), never a hardcoded model-name list in agents/.
- **Fallback / non-images:** the RAG extraction stack runs — `tools/rag/
  readers` for PDF/office prose, `tools/rag/extract.extract_image_text`
  (vision role) for images, the whisper transcription seam for audio/video
  — writing an `extract.json` sidecar beside the stored file, validated by
  `source_sha256` against the row's `file_hash` (the exact
  `tools/rag/media.py::_load_finished_sidecar_if_matching` contract), so a
  file is extracted once per conversation ever; later turns re-read the
  sidecar.
- The extracted text is injected into the prompt as a clearly-delimited
  block on the user message ("Attached file `name` (type): …"), capped at a
  settings-declared excerpt length, with a truncation notice naming the cap
  when applied.
- Progress: the turn job's label reads "Reading <name>…" during extraction
  (the poller's existing "Working — <label>…" surface), then hands over to
  the normal turn labels.

Import-law check: `agents/runtime` importing `tools/rag` extraction seams
follows the established direction (agents already calls into tools via the
tool contract; direct reuse of the extraction functions needs the same
column-boundary review the plan must confirm against
`foundation/ops/tests/` import gates — if the gate forbids it, the seam is
a thin extraction facade registered the way tools are).

### 4. Tool handoff (`agents/contracts`, `agents/runtime`, `tools/rag`)

- `file:<id>` joins the artifact grammar in `agents/contracts/artifacts`
  (parse, title, URL name), so refs flow through `Turn.artifacts`,
  rendering, and tool params like `output:`/`document:` do today.
- **Rewrite at the vision boundary:** when the loop passes a `file:` ref in
  a vision tool's file param, the runtime stages a copy via
  `tools.vision.services.stage_upload` and substitutes the returned
  `input:<id>`. Vision's `resolve_inputs`, specs, and prose are untouched
  — zero vision-column changes, import law intact (agents may call vision's
  service seam; vision never learns agents' vocabulary).
- "Add this to the library" is DEFERRED (plan-review finding 1,
  2026-09-03): `rag.ingest` is `mutates=True` and deliberately
  ungrantable until Identity & Auth's mutating-tools ruling
  (ADR 0010:266-276; `agents/defaults.py:329-331` records it by name),
  so an attachment param today would be dead surface. The design when
  the policy lands: an optional `file` param on `RAG_INGEST` ("exactly
  one of document_id/file") resolved through a conversation-checked
  `agents.runtime.attachments.ingest_source_path` accessor by dotted
  path; bytes are copied into the document store (per-store ownership,
  no shared bytes). Owner decision required first.

### 5. Rendering + serving (`agents/chat`)

- USER cards show attachments through the existing `_artifact_images.html`
  / `_artifact_files.html` includes (the `files` card key exists; P3 noted
  it unrendered — this wires it).
- New endpoint `chat-file` serves `ConversationFile` bytes (download/
  inline by media type), gated by the conversation's visibility rules
  (same access answer as the thread page), registered in
  `identity/routes.py` with the correct class.
- The endpoint joins the never-500 route matrix.

### 6. Testing + docs (standing rule: both ship with the change)

- Model/lifecycle: create, CASCADE + disk cleanup on conversation delete,
  ownership stamping.
- Upload: happy path (refs on the user turn, rows, files), each rejection
  (size, count, type), partial-failure cleanup, no-JS POST.
- Prompt: capability-gated image path vs extraction fallback (fake
  capability answer both ways), sidecar cache hit (extraction runs once for
  a repeated hash), excerpt cap + truncation notice.
- Handoff: `file:` → `input:` rewrite at the vision boundary (vision sees
  only its own vocabulary). The ingest-from-attachment test goes with the
  cut path (section 4) — nothing to pin until the mutating-tools ruling.
- Rendering: full-render pins for attachment cards; `chat-file` in the
  never-500 matrix + visibility tests.
- Docs: `agents/README.md`, `agents/chat/README.md`, ADR 0015 amendment
  (turn contract gains attachments), EXTENDING/ARCHITECTURE touch-ups as
  needed.

## Non-goals

- Auto-ingesting attachments into the library — adding to the library
  stays an explicit act, deferred with section 4's policy gate.
- Attachment editing/versioning; re-upload is the answer.
- Cross-conversation extraction dedup (a hash-keyed sidecar index across
  stores) — a later optimization; today's sidecar lives beside its file.
- Drag-and-drop / paste-to-attach UI polish (progressive enhancement,
  later).
- MCP-edge exposure of attachments (Identity & Auth → MCP phase).

## Named future seam (recorded so v1 is not mistaken for the end state)

The excerpt cap is a v1 honesty measure, not the design: the long-term
answer for a heavyweight attachment (a 500-page PDF) is RETRIEVAL over the
attachment — conversation-scoped chunks, retrieved per question — not a
bigger excerpt. V1's path for such files is the capped excerpt alone — the
explicit `rag.ingest` act is deferred with the mutating-tools policy
(section 4), so a heavyweight attachment is understood only through its
excerpt until either that ruling or conversation-scoped retrieval lands. When conversation-scoped retrieval is built, it
replaces the excerpt injection at exactly one seam (prompt-side handling in
`agents/runtime`); nothing in this design blocks it.

## Open questions for the plan (not blockers)

1. Exact capability signal for "chat model accepts images" — catalog
   `capability` field vs a per-model flag; the plan confirms against
   `models/contracts/catalog.py` and picks the seam the models column
   prefers.
2. Turn-job footprint declaration when extraction runs whisper/vision
   models inline (the U7 over-declaration territory) — coordinate with the
   peer's agents/runtime precedent before changing declarations.
3. Whether `agents/store.py` or `foundation` hosts the store helper —
   wherever the import gates allow with least ceremony.
