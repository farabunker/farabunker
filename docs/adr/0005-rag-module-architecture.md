# ADR 0005 — RAG module architecture

**Status:** Accepted
**Date:** 2026-08-17

## Context

The first farabunker module is an **offline RAG knowledge base**: drop documents in, and the
system becomes a queryable, cited source that works with no internet. Two driving use cases,
which are the *same* module under different [posture profiles](../ARCHITECTURE.md#2-security-posture-the-offline-spectrum):

- **Resilience knowledge base** — engineering/medical/how-to references, accessible as long
  as the machine powers on (`airgap`).
- **Sensitive business data** — internal knowledge that must never touch the WWW
  (`airgap` or `isolated-lan`).

A reference n8n "Complex RAG" workflow was provided as a *pattern* (not to be used — no n8n,
no cloud). Its good ideas, kept: **hybrid retrieval** (semantic search for prose + SQL for
tabular data), an **agent/router** that picks the right tool, **chat memory**, and automated
**ingestion**. Everything cloud in it is replaced with a local equivalent.

## Decision

Build the RAG module in **Python with LlamaIndex**, on the [stack in ADR 0004](0004-application-stack.md),
talking only to core capabilities per the [module contract](../ARCHITECTURE.md#5-the-module-contract)
(`inference:chat`, `inference:embeddings`, `vector:read/write`, `storage:documents`; `network: none`).

### Cloud → local mapping (from the reference pattern)

| Reference (cloud) | farabunker (local) |
|---|---|
| Hosted vendor chat model | Ollama LLM via Inference Gateway (OpenAI-compatible) |
| Hosted vendor embeddings | Local embeddings (e.g. `nomic-embed-text`) via the Gateway |
| Supabase (hosted pgvector) | Local Postgres + pgvector |
| Google Drive trigger/download | Local **watch folder** ingest |
| n8n orchestration | Native Python (LlamaIndex + Django) |

### Ingestion pipeline

```
watch folder ─▶ detect type ─┬─ prose (PDF/txt/md/docx) ─▶ parse ─▶ chunk (nodes)
                             │                                   ─▶ embed ─▶ pgvector
                             └─ tabular (CSV/XLSX) ─▶ rows ─▶ Postgres table + schema
                                                    (+ document metadata for both branches)
```

- **Prose** → LlamaIndex readers → node/chunk splitting → embeddings → **pgvector**.
- **Tabular** → rows written to a **structured Postgres table** with a recorded schema — *not*
  embedded — so numeric/aggregate questions stay accurate.
- Both record a row in a **document metadata** table; re-ingesting a changed file deletes its
  old rows/chunks first (idempotent re-index).
- Ingestion runs **out-of-band** (management command / worker), never inside a request.

### Retrieval / query

A **RouterQueryEngine** (LlamaIndex) — an agent over the LLM — routes each question to the
right tool:

```
question ─▶ Router ─┬─ VectorQueryEngine        (prose: semantic search over pgvector)
                    ├─ NLSQLTableQueryEngine     (tabular: natural-language → SQL over rows)
                    └─ full-document fetch        (when the whole doc is needed)
                          │
                    synthesize with LLM (Inference Gateway) ─▶ answer + citations
```

- Answers **cite their sources** (document + chunk / row) — essential for a reference tool.
- **Chat memory** persists in Postgres for multi-turn sessions.
- All LLM + embedding calls go through the **Inference Gateway**, so the engine/model is
  swappable by config.

### Data model (in Postgres)

- `documents` — one row per ingested file (id, title, path, type, hash, ingested_at).
- `doc_chunks` — chunk text + `vector` embedding (pgvector) + FK to `documents`.
- `doc_rows` — tabular rows for CSV/XLSX sources + recorded schema, queryable by SQL.
- `chat_sessions` / `chat_messages` — conversation memory.

## Scope

**In scope (Phase 1 walking skeleton):** watch-folder ingest for PDF/text/markdown/CSV/XLSX;
pgvector semantic search; text-to-SQL over tabular rows; router/agent; cited answers; a minimal
Django query UI/API; config-driven model selection.

**Deferred (later phases):** reranking, multi-hop/iterative retrieval, OCR for scanned PDFs,
per-document access control (maps to Identity & Auth), incremental/streaming re-index at scale,
voice, and non-document sources.

## Consequences

- The module is engine- and model-agnostic (Inference Gateway contract), so it improves
  automatically as local models get better — the core plug-and-play promise.
- One Postgres database backs vectors, tabular rows, metadata, and memory — a simple, packable
  appliance.
- Keeping tabular data as SQL rows (not embeddings) is the key accuracy decision inherited from
  the reference pattern.
- LlamaIndex gives the router, vector engine, and text-to-SQL engine largely out of the box,
  minimizing bespoke retrieval code.

## Amendment (2026-08-24) — Amended by ADR 0014: the OCR/voice/non-document deferral is lifted

[ADR 0014](0014-media-ingestion.md) (media ingestion) closes three items from
this ADR's **Deferred (later phases)** list above: *OCR for scanned PDFs*,
*voice*, and *non-document sources*. The Scope section is left as written —
it was an accurate record of what Phase 1 deliberately did not build — and
this note records what has since changed rather than rewriting it.

What now ships, on top of this ADR's prose/tabular pipeline:

- **Video and audio** are transcribed by a speech-to-text engine running
  natively on the host, reached the same way this ADR's "Inference Gateway
  contract" already reaches every other engine, and the transcript is then
  chunked and embedded exactly like any other prose text.
- **Images, and scanned PDFs auto-detected as having no text layer**, are
  read by a vision model — one image, one call — and the extracted text is
  likewise chunked and embedded like prose.
- **Citations gained a locator**: a media chunk cites a timestamp
  (`"file.mp4 at 12:40"`), and a page-keyed chunk finally surfaces the page
  key this ADR's ingestion pipeline was already recording
  (`"report.pdf, p. 3"`).
- **Ingestion moved onto the execution queue** ([ADR 0013](0013-inference-execution-queue.md)).
  This ADR's "Ingestion runs **out-of-band** (management command / worker),
  never inside a request" rule is unchanged in intent and stronger in
  practice: the watch folder and the browser upload are now *enqueuers*, and
  the parse/chunk/embed work is a `rag.ingest` job with priority, progress,
  and resume-across-restart.

Unchanged by ADR 0014, and worth restating because it is the deferral that
did **not** lift: **tabular data is still stored as SQL rows and is still
not searchable.** This ADR's RouterQueryEngine / text-to-SQL design remains
Phase 2 work; today a question is answered by semantic search over prose
chunks alone. Saying so plainly in the library and Ask copy is a named
follow-up in ADR 0014 §18 (item W2).

Also unchanged: retrieval performs an **exact** scan over the chunk table —
no approximate-nearest-neighbour index exists — which ADR 0014 §18 records
as a fact rather than an assumption, so adding one is a measured decision.
