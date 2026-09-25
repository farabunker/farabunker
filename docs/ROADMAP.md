# farabunker — Roadmap

A phased build order. The strategy is **walking-skeleton-first**: get one thin slice
running end to end through every layer before broadening, so the platform contracts are
proven by a real module (RAG) rather than guessed at.

---

## Phase 0 — Architecture & design  *(complete)*

- [x] Vision, threat model, and offline-posture spectrum
- [x] Layered architecture and the core service catalog
- [x] Module contract and the update-airlock concept
- [x] Choose the application stack — Python/Django, Postgres+pgvector, Ollama ([ADR 0004](adr/0004-application-stack.md))
- [x] Choose the RAG framework & module design — LlamaIndex, hybrid retrieval ([ADR 0005](adr/0005-rag-module-architecture.md))
- [x] Deployment, data-persistence & isolation — container stack, host-mounted data ([ADR 0006](adr/0006-containerization-and-isolation.md))
- [x] Reference hardware — dev on M4 Max Mac; NVIDIA desktop for CUDA validation ([ADR 0007](adr/0007-reference-hardware.md))

**Phase 0 complete.** All contracts and stack decisions are recorded; implementation begins.

---

## Phase 1 — Walking skeleton (RAG end to end)

The goal is *one* vertical slice touching every core contract, on a developer machine.

- [x] **Inference Gateway** — Ollama chat + embeddings via `models/contracts/gateway.py`
- [x] **Vector Store** — LlamaIndex pgvector (`rag_chunks`) via `tools/rag/index.py`
- [x] **Storage** — document ingest + metadata (`Document`, `DocumentRow`, chat memory)
- [x] **RAG module** — ingest (prose→embed, tabular→SQL) → router retrieval → cited answers
- [x] **Minimal ACCESS UI** — `GET /rag/` question page + `POST /rag/ask/` API (offline, inline assets)
- [x] **Config-driven models** — LLM/embedding/engine chosen by env, no code changes
- [x] **Execution queue** — a Postgres-backed, priority-ordered, memory-aware admission
  queue (`models/queue/`, [ADR 0013](adr/0013-inference-execution-queue.md)) is now the
  *one* path every model execution takes: Ask (`rag.ask`), the embeddings re-encode
  guard (`rag.reencode`), image generation (`vision.generate`), and document ingestion
  (`rag.ingest`, Phase 1.57) all submit through it and are operable at `/queue/`. The
  planned `chat.converse` (Phase 1.6) registers against this same door with zero
  framework changes — a job kind is a consumer of the queue, never a parallel path
  around it.

**Exit criteria:** ask a question about your own documents and get a grounded, cited
answer, fully offline, with the model chosen by config.

**Code complete** (3 waves, all committed). Verified live against Postgres+pgvector
(migrations, extension, ORM, routing). Exit criteria met (2026-08-24): full ingest→ask
smoke test completed end to end on live system, all document mediums supported.

---

## Phase 1.5 — Model management framework

Landed alongside the walking skeleton, ahead of schedule: an operator-managed model
registry so "config-driven models" (Phase 1) becomes swappable at runtime, not just at
deploy time. See [ADR 0010](adr/0010-model-management-framework.md).

- [x] **Registry console** — `models/registry/` at `/inference/`: register engine+model
  connections, see health/discovery, all without touching env vars or restarting
- [x] **Per-role bindings** — `RoleBinding` maps each registered role (`rag.answer`,
  `rag.embed`) to a connection, resolved via the db → env fallback chain
  (`models/registry/bindings.py`) so the platform still runs off env alone with zero DB rows
- [x] **Catalog + cold-start onboarding** — curated model catalog
  (`models/contracts/catalog.py`) with copy-paste `ollama pull` guidance; the console and
  `/rag/` both degrade to a friendly "set up a model" state when nothing is bound yet
- [x] **Embedding re-encode guard** — `Materialization` fingerprinting detects when a
  role's data has drifted from its current binding, with a guided re-encode
  (`models/registry/drift.py`, `reencode_all`) that rebuilds in place or flags a full
  store rebuild depending on whether the embedding dimension changed

This front-runs part of **Phase 4**'s "guided setup: pick a posture profile, load a model
pack, go" — the model half of that flow (browse a catalog, bind roles, no restart) now
exists; posture-profile selection remains Phase 2/4 work.

---

## Phase 1.55 — Image generation

Delivered: the first non-RAG capability, validating the model-management framework's
extension seam end to end. See [ADR 0012](adr/0012-image-generation-engine-adapter.md).

- [x] **ComfyUI engine adapter** — `models/contracts/engines/comfyui.py`: health, installed
  checkpoints, asset/choice listing, submit/status/fetch, and its own engine-declared
  `SetupGuide` (macOS/Windows/Linux install steps, rendered at `/setup/`)
- [x] **`image-generation` capability** — distinct from the input-side `vision` capability
  already reserved for image-*reading* models
- [x] **`vision.generate` role** — one role, registered by `tools/vision/` only while the
  `"vision"` feature flag is enabled; the role binding *is* the checkpoint choice, resolved
  through the same db → env chain as `rag.answer`/`rag.embed`
- [x] **`/vision/` module** — prompt-to-image page, poll-driven job cards with a no-JS
  fallback (four-band cards: prompt + status chip, media beside a labelled facts
  panel, the same Use-in/Reuse/Download actions the gallery offers from one shared
  partial), a gallery, live queue-position and elapsed-time reporting, and generic
  job/output records (`GenerationJob`/`GeneratedOutput`) storing the exact engine
  payload for reproducibility
- [x] **Operation registry** — `models/contracts/operations.py`, five operations shipped
  (`txt2img`, `img2img`, `inpaint`, `upscale`, `edit`), each one `Operation` + one graph
  template per `(family, operation)` pair, no reshape
- [x] **Image-to-image and inpainting** — attach an image (inpainting also takes a
  mask); the engine adapter moves the file, farabunker keeps its own copy
- [x] **Upscaling** — attach an image and an upscale model from
  `ComfyUI/models/upscale_models/`
- [x] **LoRA controls** on every checkpoint mode, listed from `ComfyUI/models/loras/`
  and filled from what the engine reports having
- [x] **Gallery hand-off** — any result's "Use in …" links feed it straight into
  whichever modes the currently bound/picked model supports and take an image
  (img2img/edit collapsed into one "Image to image" entry, inpaint, upscale)
  with no download-and-re-upload round trip
- [x] **`edit` operation, dispatched by model family** — instruction-based editing on
  either of two multi-file model families, each its own
  ComfyUI graph template keyed by `(family, operation)`; family and companion
  text-encoder/VAE files are an operator declaration at registration
  (`ModelConnection.config`), never inferred. Live-verified: both the
  ordinary and the distilled variant. See ADR 0012's "Instruction-based
  editing across model families" and the follow-up plan's Live
  verification log for the governed queue runs recorded
- [x] **Per-generation model picker** — the create page offers every registered
  `image-generation` connection, the role binding preselected as the default; the
  operation chooser and form narrow to what the picked model's engine actually
  supports, with an honest banner + disabled form when it supports nothing. Same
  picker grammar and `payload["connection"]` seam `rag.ask` already uses
- [x] **A distilled variant within a family** (ADR 0012 D-EDIT-6..8) —
  `ModelConnection.config["variant"]` selects a second graph for weights that need a
  different starting point (no guidance node, its own step/cfg defaults), reported
  through the same `param_defaults`/`live_defaults` seam every live-option list uses.
  Live-verified: the distilled variant ran ~32 s/step, 137.9 s and
  2:13 processing on two governed runs — faster than the ordinary graph of the same
  family's ~35 minutes of 20-step sampling on the same hardware
- [x] **A model-only LoRA/speed-adapter chain on `edit`** (ADR 0012 D-EDIT-9) — both
  edit families' bundled ComfyUI workflows carry a `LoraLoaderModelOnly`; `edit` now
  declares the same `loras`/`lora_strength` params every checkpoint mode already had,
  applied through a model-only sibling of the checkpoint fragment
- [x] **Every generation reports how long it waited and how long it ran** — derived
  `queued`/`processing`/`total`/`submitted` durations (no new column) on the job card,
  the gallery, and the tool-facing result dict, plus a live elapsed clock on the
  queued placeholder
- [x] **The second model family's `edit` live verification** — registered with a
  quantized text encoder (8.10 GB) plus its multimodal projector
  (1.35 GB). Plain run (queue job 32):
  20 steps at ~43 s/step, 14:53 processing. The distilled run (queue job 33): 4 steps
  at ~21.5 s/step, 1:47 total. See live verification log.
- [x] **Second graph family `txt2img` (R1)** — Text-to-image on the second graph
  family: a template declares the params its graph cannot honour (`IGNORES`), the
  engine exposes them (`ignored_params`), and the page/tool never trust a submitted
  value for them. Family-level defaults; shared graph fragments promoted to
  `_fragments.py`. (ADR 0012 D-EDIT-12)
- [x] **One constant input form (R2)** — One constant generation form: the union of
  every registered operation's params, each field stamped enabled/unused/ignored
  with a reason and a tooltip; Model + Mode are GET selectors; blanks the operator
  was never allowed to answer are back-filled from the engine before validation;
  the catalog reports `supported`/`unsupported_reason` per operation so tools can
  say "not runnable here". Tab/merge machinery deleted. (ADR 0012 D-EDIT-13)
- [x] **A chatbot tool wrapper over the vision module** — `vision.operations` and
  `vision.generate` are registered tools (`tools/vision/apps.py:75-76`); see
  [ADR 0015](adr/0015-agent-layer-and-tool-contract.md).

Deferred (see ADR 0012's Consequences), all follow-ups rather than gaps:

- [ ] ControlNet (needs a preprocessor story this plan didn't open) and an embedding
  parameter UI (ComfyUI has no embedding loader node to hang one on yet)
- [ ] Batch runs (more than one output per submission) — R1 excluded it from the edit
  work; every operation, `edit` included, produces exactly one output per job. (The
  background worker itself already shipped: `vision.generate` runs through the same
  execution-queue worker `rag.ask` does, not the page's poll loop.)
- [ ] Retention/quota for `data/generated/`
- [ ] A second image-generation engine adapter (A1111/Forge, SwarmUI, InvokeAI, or a
  hand-rolled diffusers service)

---

## Phase 1.57 — Media ingestion (video, audio, images, scanned PDFs)

Delivered: the knowledge base stopped being text-only. Drop in a recording, a
photograph, or a scan of a paper document and it becomes searchable, quotable source
material like anything else. See [ADR 0014](adr/0014-media-ingestion.md).

- [x] **Video and audio** — transcribed by a speech-to-text engine running natively on
  the host (the same offline, operator-placed-models contract Ollama and the image
  engine already use), then chunked and embedded like any other text
- [x] **Images and scanned PDFs** — a PDF with no text layer is detected automatically
  and read page by page by a vision model; the text comes back verbatim, never filtered
- [x] **Timestamped and page-numbered citations** — an answer now says *"file.mp4 at
  12:40"* or *"report.pdf, p. 3"*, so a claim can be checked at the exact spot it came
  from
- [x] **Everything ingests through the queue** — one `rag.ingest` job kind for every
  medium; a dropped file appears in the library immediately with an honest status while
  the heavy work runs behind it
- [x] **Live progress, and resume after a restart** — the Queue page reports a
  transcription in timecode, *"5:00 of 42:10 transcribed"*, and a page-by-page
  extraction in pages, *"3 of 12 extracted"*; a job picks up where it left off across a
  deploy, rather than starting an hour of work over
  ([ADR 0013](adr/0013-inference-execution-queue.md) §8's deferral, lifted)
- [x] **Failure is always recoverable** — every way an ingest can fail leaves a document
  marked failed with the reason, and a Retry button that works
- [x] **Operator caps** — upload size, media duration, and document page count are all
  operator-editable limits enforced before expensive work starts
- [x] **A transcript page, and streaming the original file** — read a document's
  transcript or page extraction directly; the file endpoint supports HTTP Range, so
  opening a video or audio document (it opens in a new tab) lets the browser or player
  stream and scrub the original instead of downloading it whole

Deliberate limits, recorded in ADR 0014 rather than left to be discovered:

- [ ] A PDF that mixes typed and scanned pages routes as a whole, not page by page
- [ ] Re-reading a document with a *better* model requires re-adding the file (the
  cached extraction is keyed to the file, not the model) — a force flag is missing
- [ ] Cancelling only works while a job is still queued — a job a worker has already
  started runs to completion; no cooperative stop signal exists yet
- [ ] Tabular data is stored but still not searchable (unchanged from Phase 1)

---

## Next — retrieval quality and the honest-answers wave

Approved and next on deck, in this order. No dates committed.

- [ ] **W1 — per-page PDF routing.** Split a mixed typed/scanned PDF and use the vision
  model only on the pages that actually need it
- [ ] **W2 — tabular honesty.** Say plainly, in the library and on the Ask page, that
  spreadsheet data is stored but not yet searchable, instead of letting it look
  answerable
- [x] **W3 — backup and restore.** A documented, testable procedure plus the commands to
  run it, leading with "your original files plus a database dump reproduce everything" —
  shipped (2026-08-25); see [docs/OPERATIONS.md](OPERATIONS.md)
- [ ] **W4 — a relevance floor, a tunable result count, and citation dedup.** Stop weak
  matches from padding an answer, and stop the same source being cited three times
- [ ] **W5 — hybrid search.** Keyword matching alongside semantic search, so an exact
  term someone knows is in the documents is actually found
- [ ] **W6 — a search page.** Retrieval with no model in the loop at all: type a phrase,
  see the matching passages. Fast, cheap, and the visible surface of W4's score floor

Recorded keep-outs, so they don't get re-litigated: reranking; an approximate-nearest-
neighbour index before anyone has measured that search is slow; reviving automatic
text-to-SQL retries; multi-hop retrieval; PPTX/EML/URL ingestion; a tunable chunk size;
a mobile redesign; queue dashboards; and multi-turn chat, which belongs to Phase 1.6
below.

---

## Phase 1.6 — Conversational agent module

The next phase on deck is a conversational agent — not simply a chat window. A conversation
surface that also has TOOLS for the entire system: it can interact with other features,
modify settings, and use RAG as one tool among several, not the whole interaction. This
phase is partly shipped: `chat.converse` and tool use landed in P2/P3, and Identity & Auth's
two halves — IA-1 (real principals, postures, ownership) and IA-2 (grants, groups, labels,
sharing) — are both merged and live (see below); what remains is the per-role model split,
grounded-by-default conversation, the MCP edge, and Tenancy. See
[ADR 0010](adr/0010-model-management-framework.md)'s 2026-08-22 amendment for the
architectural constraints this phase must honor, and
[ADR 0015](adr/0015-agent-layer-and-tool-contract.md) for the record of what was built.

- [x] **`chat.converse` role registered** — shipped 2026-08-27 (agents plan, P2), not as a
  `modules/chat` feature module (that shape predates the P0 regroup, and this column has no
  manifest-declared module system to register one against). Registration lives in
  `agents/apps.py::AgentsConfig.ready()`, the `agents` app's own `AppConfig`, through the
  existing role registry, exactly as planned. The console, the Getting-models checklist, and
  per-role binding serve it with **zero framework changes**, the same way they already pick up
  `rag.answer`/`rag.embed` and the reserved `vision` capability — nothing in `/inference/` was
  edited to make this true; see `agents/README.md`.
- [x] **Shipped defaults are a catalogue, not a deploy step** — shipped 2026-08-28 (agents
  plan, P3 Task 5). P2's `manage.py sync_agents` re-applied its declarations to their rows on
  every deploy that ran it; that is retired. `agents/defaults.py` now declares a catalogue
  (`DEFAULT_AGENTS`, `DEFAULT_FLOWS`) of what the platform *offers*, and `manage.py
  install_defaults [--reset <slug>]` (or the future chat-page Add button) is the explicit,
  idempotent, create-if-absent way an operator adopts one. A deploy that changes the
  catalogue changes what is offered, never what is installed — an operator who already
  installed `general` keeps their `general`, including every edit they made to it.
- [ ] **Per-role model split as a first-class outcome** — a lean model can stay bound to
  `rag.answer` for grounded lookups while a larger, more conversational model backs
  `chat.converse`; the binding provider resolves each role independently, exactly as
  designed.
- [ ] **v1 — grounded conversation.** Grounding today is by tool choice: an agent granted
  `rag.ask`/`rag.search` retrieves when it decides to, and `/chat/` renders the citations
  it returns. The open item is narrowed to an agent mode that retrieves on every turn
  regardless of the model's choice. What this plan originally described — reusing the
  `ChatSession`/`ChatMessage` tables (`modules/rag/models.py`, since renamed
  `tools/rag/models.py`) as Postgres-resident chat memory via the Ask API's
  `session_id` plumbing — did not happen: both tables were write-only (nothing
  ever read a row back) and were dropped in P2 (agents plan, spec section 7.6),
  along with `answer_question`'s `session_id` parameter. Conversation memory
  instead lives in `agents.models.Turn`, which P2 already shipped — it records
  the tool call, its arguments, its data, its artifacts, and its delegation
  depth, and an agent invokes RAG as a tool through the unchanged
  `tools.rag.retrieval.answer_question` seam (ADR 0010, amended).
- [x] **v2 — tool use** — shipped 2026-08-28 (agents plan, P3). The agent invokes
  framework capabilities as tools through the `/chat/` surface: a conversation list, a
  thread (turns as cards, tool cards carrying their arguments, thumbnails via
  `vision-output-file`, and citations via `rag-document-file`), an `agent.turn` POST that
  answers 202-and-poll (or a plain redirect with JavaScript off), and delegated turns
  rendered inside a collapsed disclosure. **Flows** ship alongside it (ruling 1): a `Flow`
  is a ROW, like an `Agent`, and every flow runs through the SAME `invoke_tool` every other
  tool call does, by the ONE registered `flow.run` tool whose `flow` param's choices are
  filled per turn from the rows a principal may see — so N flows never need N registered
  specs. Deliberately not built: **flow-as-a-turn** (deviation P3-D4 — `/chat/` picks an
  agent, not a flow, and no payload key exists with no caller to use it) and a
  **flow-builder UI** (§14 gap 2 — a flow is still JSON an operator edits out of band, but
  it is a ROW now, which is what a builder would edit instead of a code declaration).

  P3 also makes two operator-facing changes to P2's story, because they change what a
  deploy does: **nothing is seeded automatically** (ruling 2 — `manage.py install_defaults`
  replaces `sync_agents`; a deploy changes what the platform offers, never what is
  installed) and **a row that came from a shipped default is the operator's to edit**
  (ruling 3 — the resident edit-lock is gone; `--reset <slug>` is the one deliberate way
  back to the shipped text).
- [ ] **The chat cluster** — on branch `chat-cluster`, in review (2026-09-22); this box ticks
  when a later documentation pass records the deploy. Three independently argued features on
  one branch. **A context meter**: the thread page says how much of the model's context the next
  turn will carry, measured against the window the engine will actually be asked to allocate
  rather than a model's architecture maximum, and says so when a conversation is already
  being shortened before it is sent. **An agent create/edit utility**: one form and one edit
  route, mounted both at `/chat/agents/` for the people who own agents and at
  `/settings/agents/` as an administrator's library, with audience as **two independent
  controls** — reach (`Agent.box_wide`, administrators only) and entitlement labels through
  the existing add/remove diff gate — rather than one exclusive choice. **Editing a past
  prompt**, as a **branch**: a new conversation holding everything before the edited message,
  the original untouched, with two provenance columns making "where did this thread come
  from" a fact you can query rather than a string you can only read. Recorded by
  [ADR 0019](adr/0019-chat-cluster.md), which also carries the phase's ten named gaps and the
  read-direction amendment it adds to [ADR 0010](adr/0010-model-management-framework.md).
- [ ] **Conversation compaction** — still future, and deliberately so. **The meter is its
  foundation, not a substitute for it:** `agents/usage.py::estimate_tokens` is the one token
  arithmetic on this platform, at the agents column root rather than inside the chat app so a
  future management command or MCP edge can ask the same question, and the truncation clause
  the meter renders is already the sentence that tells a reader their conversation is being
  shortened. Compaction consumes that function rather than growing a second, drifting
  counter. Also carried as a deferred item in [ADR 0017](adr/0017-workstreams.md)'s gaps.

Three phases follow directly from what P2's agents plumbing already built (the tool
registry, `Principal`, `granted_tools`, the one retrieval filter point) — in this order,
straight from the 2026-08-27 addendum. Identity & Auth is itself two halves: IA-1 (real
principals, postures, ownership, the acting rule) ships first, so the MCP edge and
Tenancy below have real accounts to gate behind; IA-2 (grants, groups, labels, sharing)
is what actually narrows what a principal may see, and is what those two later phases
are waiting on:

- [x] **Identity & Auth**, shipped in two halves. **IA-1**: a real `identity.
  User`, three security postures (`open`/`personal`/`enterprise`, `IdentitySettings.
  posture`, a database row rather than an environment variable), and an append-only
  `AuditEvent` trail. `identity.request.principal_for_request` is now the one place
  every request becomes a `Principal` — the shared open one in `open` posture, the
  signed-in user otherwise; `agents/visibility.py`'s four visibility functions and their
  siblings in `tools/rag`, `tools/vision` and `models/queue` now apply a real
  `owner_kind`/`owner_key` filter instead of returning everything; `/inference/`'s
  mutation endpoints and every route in the platform are classified into one of three
  tiers (public/authenticated/admin) and enforced by one gate middleware; and the
  acting rule (a turn/job runs as the signed-in USER, never as the agent it invokes)
  closes the last open question in ADR 0015's §10. `manage.py adopt_open_rows`/
  `reassign_owner` move a box's pre-phase history onto its first administrator.

  **IA-2**: per-principal **grants** in their own table (`identity.EntitlementGrant`,
  `agents.ToolEntitlement`, `tools.rag.DocumentEntitlement`) read through
  `ToolAccess`/`agents/entitlements.py::tool_access_for` and `models/registry/
  access.py::model_access_for`; **groups**; document and tool **labels**; `Share`
  and the conversation-sharing UI; the retrieval visibility argument threaded through
  `tools/rag/access.py::readable_documents`/`listable_documents` and
  `retrieve_nodes`/`answer_question`; and the chunk-metadata cache
  (`tools/rag/labels.py::restamp_document_chunks`, `manage.py relabel_chunks`) the
  label-aware retrieval filter needs. Recorded in full by
  [ADR 0016](adr/0016-identity-and-entitlements.md) — the architectural record of both
  halves, including this phase's own named gaps. See also
  [`identity/README.md`](../identity/README.md), ADR 0015's G13 and its 2026-08-30
  amendment, and ADR 0010's 2026-08-30 amendment.
- [x] **Workstreams**, shipped in two halves on top of IA-2. **WS-1**: a `Workstream` row
  per stream of work — name, instructions, an archive state, its own conversation list, its
  own default upload placement — with a **contained corpus** (a document lives in one stream
  or in the universal library, never both), **pinning** a universal document into a stream's
  working set, and an entitlement **wall** that narrows which of the caller's own tools and
  model sets bind inside that stream (inert by design on an open box, which has no accounts
  to operate one — Ruling A). **WS-2**: **sharing** a stream to another account or group,
  through the one fenced 403 this platform has on a row-addressed URL (spec §12.3 — a live
  share holder whose grants no longer cover the stream's tags is answered 403, never the
  usual 404, and the exception is fenced three ways); **taint** — the entitlement labels a
  stream's retrievals have touched, recorded at the conversation and the stream level and
  re-checked on every shared read, because a share that passed once is not a share that still
  holds; and **consolidation** — distilling a conversation into a retrievable, stream-contained
  note document, owner-only, with a repair path on the stream page when the re-ingest half of
  it fails. Recorded by [ADR 0017](adr/0017-workstreams.md) — the architectural record of both
  halves, written after both merged — and in full by the binding spec,
  [`docs/superpowers/specs/2026-09-03-workstreams-design.md`](superpowers/specs/2026-09-03-workstreams-design.md),
  with amendments to [ADR 0015](adr/0015-agent-layer-and-tool-contract.md) (`ToolContext.
  stream`, and that a delegate inherits it) and [ADR 0016](adr/0016-identity-and-entitlements.md)
  (`Share`'s fifth target, and the one scoped exception to the platform's 404 house rule).
  **Deferred, each named against the hook it would land on** — the spec's own §22 table, not
  left implicit: full-transcript RAG (retrieving over turns rather than over distilled notes),
  a stream digest synthesised from its notes, automatic consolidation triggers, move-to-stream
  with taint import, taint removal/untaint, recipient-initiated consolidation, a third share
  level (re-share and recipient mutation rights), stream templates, per-stream agent/model
  defaults, admin-provisioned organisation streams, labelling non-document artifacts into the
  taint stamp, conversation compaction, and operator-editable prompt constants — each carried
  forward as a named gap in [ADR 0017](adr/0017-workstreams.md)'s own gaps section, alongside
  the watcher/upload placement race and the two crash-resilience items the phase's browser walk
  surfaced against the queue track.
- [ ] **MCP edge** — the same in-process tool registry, exposed to EXTERNAL agents over
  HTTP on the existing web app: `tools/list` and `tools/call`, gated behind Identity &
  Auth — still to come. Its two hooks are built: `granted_tools` now takes the acting
  principal's `ToolAccess` as a third argument (`agents/entitlements.py::
  tool_access_for`), and `ToolInvocation` (with its own `agent_slug` column) is the
  audit table an external caller's calls would land in too. Not a separate server, and
  not a protocol in the middle of an internal call — an internal tool call stays a
  Python function call. `mcp_tool_dict` (P2) is already the wire adapter. The import
  direction is also in scope: this platform as an MCP *client* of other servers, with a
  per-server allowlist of which of their tools may be registered here.
- [ ] **Tenancy** — visibility scopes on documents: **shipped** (`DocumentEntitlement`,
  `tools/rag/access.py`, applied at the one retrieval filter point,
  `tools/rag/retrieval.py::retrieve_nodes`, rather than in each tool runner).
  **Categories stay taxonomy**, not scoped: a category is library-wide,
  `rag-category-rename`/`-delete` are class `S` (administrator-only to edit the
  taxonomy itself), and no entitlement narrows which principals see a given category.

**Deliberate boundary:** the Ask page (`/rag/`) stays a stateless, single-shot grounded-
lookup instrument. Conversation is a separate surface with a different trust contract —
per-turn citations accumulating over a session versus one auditable, one-shot answer — and
this phase does not fold one into the other.

---

## Phase 2 — Make it a bunker (posture & isolation)

Turn the dev slice into something that enforces the security model.

- [ ] **Posture profiles** — implement `airgap`, `isolated-lan`, `gated-sync`
- [ ] **Default-deny egress** enforced at the isolation layer
- [ ] **Module sandboxing** — capability grants actually constrain modules
- [ ] **Update airlock** — verify & ingest a signed package; rollback on failure
- [ ] **Local-only observability & audit trail**

**Exit criteria:** a module cannot reach the WAN even when it tries; updates only enter
through the verified airlock.

---

## Phase 3 — Second module (home automation)

Prove the platform is really extensible by adding a fundamentally different module.

- [ ] Device I/O capability in the core (event bus + device abstraction)
- [ ] Home-automation module: local device control + AI-driven automations
- [ ] Decide bridge-vs-native for existing ecosystems (e.g. Home Assistant)

**Exit criteria:** RAG and home automation coexist on one box, each sandboxed, sharing
the same inference and auth core — added without redesigning the core.

---

## Phase 4 — Appliance & distribution

Make it something a non-developer can stand up.

- [ ] Reference deployment (image / compose) for the bunker box
- [ ] Provisioning & hardening automation
- [ ] Guided setup: pick a posture profile, load a model pack, go
- [ ] Docs for building your own update packages

**Exit criteria:** a fresh box goes from bare metal to a working, sealed farabunker
by following a documented process.

---

## Guiding sequence rationale

- **Skeleton before breadth** — one real module de-risks the contracts before we build
  more on them.
- **Capability before hardening** — get RAG working, *then* lock it in a bunker; it's
  easier to add isolation to a working slice than to debug through a locked-down one.
- **Second module before appliance** — extensibility is a core claim; prove it with a
  dissimilar module before investing in packaging.
