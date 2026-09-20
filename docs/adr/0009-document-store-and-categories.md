# ADR 0009 — Managed document store & document categories

**Status:** Accepted
**Date:** 2026-08-18

## Context

Two related gaps in the Wave 1 skeleton:

1. `Document.source_path` currently points at wherever a file happened to be ingested from
   (a watch-folder path, or an arbitrary upload location). Nothing copies it anywhere durable
   or box-owned — if the original moves or is deleted, `document_file` (modules/rag/views.py)
   404s, and the appliance has no single place it can back up, inspect, or manage "its" files.
2. There is no way to group documents for browsing or for scoping retrieval (e.g. "only search
   Medical references"). The library UI (a later wave) needs a grouping concept, and retrieval
   needs a matching filter.

This ADR (WAVE 1) establishes the data model, the managed store, and the settings plumbing.
Ingest, retrieval, and the library UI (later waves) build on the contracts fixed here.

## Decision

### 1. Managed, configurable document store

- `FARABUNKER_DATA_DIR` (env var, default `<repo>/data`) is the root of all durable
  appliance-owned data; `config/settings.py` derives `DATA_DIR` and `DOCUMENTS_DIR =
  DATA_DIR / "documents"` from it. In production this MUST be a durable host-mounted volume
  (ADR 0006 "durable data lives outside the container lifecycle"), not container-local storage.
- Layout: `data/documents/<document_id>/<original_filename>`. One directory per `Document` row,
  named by primary key, so it never collides and needs no separate slug.
- `modules/rag/store.py` is the single source of truth for this layout:
  - `document_dir(doc_id) -> Path`
  - `store_file(src_path, doc_id) -> str` — **copies** (not moves) the source file into the
    document's managed directory, keeping its basename, and returns the stored absolute path.
    Copy, not move, so the original watch-folder/upload path is left untouched.
  - `remove_document_files(doc_id) -> None` — deletes the document's directory tree (safe if
    already absent).
- `data/` is gitignored (already was); `.gitignore` now has a comment clarifying that
  `data/documents/` specifically is the managed store and must never be committed.
- `modules/rag/services.py::delete_document(document)` is the one place that tears a document
  down completely: vector chunks (via the same `PGVectorStore.delete_nodes` /
  `file_id`-metadata-filter mechanism `ingest.py` uses for re-ingest cleanup, wrapped
  defensively), then `store.remove_document_files`, then the `Document` row (its `DocumentRow`s
  cascade). The library UI's delete endpoint (later wave) calls this rather than reimplementing
  teardown.

### 2. Document categories

- `Category`: `name` (unique), ~~`description` (optional)~~ (removed 2026-09-10, C-42,
  migration rag 0021), `created_at`. Single-level (no
  hierarchy) — sufficient for the current use case and simplest for operators to manage.
- **Category identity is case- and whitespace-insensitive** (Phase 1 hardening, follow-up to
  this ADR): "Medical", "medical", and " medical " denote one shelf, not three. This is
  enforced with a `unique` index on `lower(name)` (rather than the raw column) plus
  `modules/rag/categories.py::get_or_create_category`, the single place that resolves a raw
  name to a row — it reuses any case-insensitive match (`name__iexact`) instead of creating a
  duplicate, and never re-cases an existing row. Every chunk's `category` node metadata (set
  #3 below) is written as the **lowercased, normalized** category key (or the literal
  `"uncategorized"`), and retrieval's category filter lowercases the requested value the same
  way before matching, so a scoped question is insensitive to whatever casing was typed.
- `Document.category` is a nullable `ForeignKey(Category, on_delete=SET_NULL)`. **Null means
  "Uncategorized"** — a pseudo-group the UI renders, not a `Category` row. Deleting a Category
  therefore reassigns its documents to Uncategorized automatically rather than deleting them or
  blocking the delete.
- One category per document (not tags/multi-category) — keeps retrieval filtering and the
  library UI's grouping simple.
- Seeded via a data migration (`0004_seed_default_categories`, idempotent via `get_or_create`)
  with four starting categories: **Medical, Engineering, Reference & Manuals, Business**. These
  are a starting point, not a fixed enum — operators can rename, add, or delete categories
  freely from the (later-wave) UI/admin. The reverse migration removes exactly those seeded
  names, and only ones left unused (no documents assigned), so it never deletes an
  operator-populated category.
- Categories may later carry an access-scope field for sensitive business data (per the
  module README's "Sensitive business data" posture) — deliberately not built in this wave;
  the single-level, flat model doesn't foreclose adding that later.

### 3. Contracts the next waves must honor

**Ingest** (modules/rag/ingest.py, later wave):
- On ingest, copy the source file into the managed store via `store.store_file(path, doc.id)`
  and set `Document.source_path` to the *returned stored path* — not the original
  upload/watch-folder path. (`document_file` in views.py already just streams whatever
  `source_path` points at, so this requires no view change.)
- Assign `Document.category` from either an explicit value passed to the ingest call, or —
  when ingesting via a watch-folder layout — the immediate drop-subfolder name (e.g. files
  dropped in `watch/Medical/` get category "Medical"), falling back to Uncategorized
  (`category=None`) when neither applies.
- Add a `category` key to **every chunk node's metadata** at index time: the **lowercased**
  category name if set, else the literal string `"uncategorized"` (see the case-insensitivity
  note above — implemented in `modules/rag/ingest.py::_category_label`). This is what
  retrieval filters on — it must be a plain string in node metadata, not a FK reference, since
  the vector store only sees metadata, not the Django ORM.

**Retrieval** (modules/rag/retrieval.py, later wave):
- `answer_question` gains an optional `category: str | None` parameter. When set, semantic
  search filters on the `category` node-metadata field written by ingest (a
  `MetadataFilter(key="category", value=category.lower())` — lowercased to match the
  normalized chunk key above — mirroring the `file_id` filter pattern already used for
  re-ingest cleanup). `None` (the default) means "search all categories."

**Library UI** (modules/rag/views.py + templates, later wave):
- Groups documents by category for browsing, with Uncategorized (`category=None`) rendered as
  its own pseudo-group alongside real categories.
- Its delete endpoint calls `services.delete_document(document)` rather than deleting the
  `Document` row directly, so vector chunks and stored files are always cleaned up together.

## Consequences

- The appliance now has one place (`DOCUMENTS_DIR`) it can back up, inspect, or wipe, satisfying
  "box owns its data" and the ADR 0006 durable-volume principle without any code change beyond
  pointing `FARABUNKER_DATA_DIR` at the mounted volume in production.
- Copy-on-ingest means ingest now uses roughly double the disk of the original drop location
  until the operator cleans up the source — acceptable for a document-management appliance, and
  avoids ever losing the operator's original file to a move-then-fail race.
- `SET_NULL` on `Document.category` means category deletion is always safe and non-destructive;
  the tradeoff is Uncategorized isn't a real, filterable/editable row (it's the absence of one).
- Retrieval's category filter depends on ingest faithfully mirroring `Document.category` onto
  chunk metadata as a string; if the two ever drift (e.g. a category is renamed after ingest),
  existing chunks keep the old name until the document is re-ingested. Later waves may want a
  re-index-on-rename step, but that's out of scope here.
