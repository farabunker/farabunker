# RAG Phase-1 Hardening: case-robust categories, browser upload, category management

**Status:** Approved design (2026-08-19) · **Module:** `modules/rag` · **Phase:** 1 (walking-skeleton hardening)

## Overview

Phase 1 proved the RAG slice end to end, but three rough edges remain before it's
trustworthy for a non-developer to operate:

1. **Category casing collides.** `Category.objects.get_or_create(name=…)` is exact-match, so
   dropping a `medical/` folder forks a *second* "medical" shelf next to the seeded "Medical".
   Confirmed live (`'medical'` docs=1 vs `'Medical'` docs=0; same for `reference`). This
   defeats the organizing promise categories exist to serve — findability under stress.
2. **No browser upload.** Ingestion is CLI / watch-folder only; a non-developer can't get a
   document in.
3. **No category management in the UI.** A bad-casing or misspelled category can only be fixed
   via Django admin — no rename/delete escape hatch.

This design fixes all three, plus the plumbing they require.

## Goals

- Category matching is **case- and whitespace-insensitive** everywhere: row lookup, uniqueness,
  and retrieval scoping. One shelf per name, regardless of how it's typed.
- A document can be **uploaded from the browser** and ingested **out of the request cycle**.
- Categories can be **renamed and deleted** from the UI.
- Every change ships with **unit tests + updated docs** (ADR 0008).

## Non-goals (explicitly deferred)

- Authentication on the upload / file endpoints (waits on the Identity service, Phase 2).
- A real background job queue, upload progress polling, OCR for scanned PDFs, full-content
  search, inbox storage cleanup. (Documented as follow-ups.)
- Standalone "create empty category" UI (YAGNI — creation happens via upload/ingest).

---

## Design

### A. Case-robust categories

Two layers, both fixed so casing can never fork again.

**A1 — Category rows.**
- Add `Category.Meta.constraints = [UniqueConstraint(Lower("name"), name="uniq_category_name_ci")]`
  and drop the field-level `unique=True` (subsumed by the CI constraint).
- Add a helper `get_or_create_category(name) -> Category` (in `modules/rag/categories.py`, a new
  small service module, or on a `Category` manager). It strips surrounding whitespace, collapses
  internal runs of whitespace, looks up via `name__iexact`, and creates the row with the given
  (stripped) display casing only if no case-insensitive match exists. Ingest calls this instead
  of `get_or_create`.

**A2 — Retrieval matching uses a normalized key.**
- Ingest writes chunk metadata `category` as the **lowercased** key: `doc.category.name.lower()`
  when categorized, else `"uncategorized"`. (Replaces writing the display name.)
- `answer_question` lowercases the incoming `category` filter before building the
  `MetadataFilter`. `"Uncategorized"` from the UI maps to `"uncategorized"`.
- Consequence: category-scoped retrieval is immune to display-casing and casing-only renames.
  Current chunks are already tagged `"medical"` / `"reference"` (lowercase), so **no
  re-embedding is required**. There are no uncategorized prose chunks today, so the
  `"Uncategorized"` → `"uncategorized"` shift has no existing data to migrate.

**A3 — Merge migration.**
- Data migration groups existing categories by `lower(name)`. For each group with >1 member,
  choose the canonical row: **prefer a member whose name is in the seeded `DEFAULT_CATEGORIES`**
  (the "pretty" casing); otherwise the earliest-created. Reassign the other members' documents
  to the canonical row (`Document.category`), then delete the now-empty duplicates.
- For the live data this merges `medical → Medical` and `reference → Reference & Manuals`.
- Reversible half: no-op (a merge can't be safely un-merged); document that.

### B. Browser upload → async via watch-inbox

**B1 — Inbox + watcher service.**
- New setting `INGEST_INBOX_DIR = DATA_DIR / "inbox"` (host-mounted, gitignored via `data/`).
- New `watcher` service in `compose.yaml`: same image/env as `web`, command
  `python manage.py ingest_watch /app/data/inbox`, `depends_on: db (healthy)`, shares the
  `./:/app` and `./data` mounts. Recorded as an **ADR 0006 update** (deployment/process model).

**B2 — Upload endpoint.**
- `POST /rag/documents/upload/`, name `rag-document-upload`. Multipart form, CSRF-protected,
  `@require_POST`. Progressive-enhancement: a plain `<form enctype="multipart/form-data">` that
  works with JS disabled.
- Fields: one or more files (`<input type="file" name="files" multiple>`) and a `category`
  choice — an existing category name, `"Uncategorized"`, or a new name typed into a
  `new_category` text field.
- Handler validates the extension against `readers.PROSE_EXTS | TABULAR_EXTS` (reject others with
  a message), resolves the target subfolder (`inbox/<category>/`, or `inbox/` for Uncategorized),
  writes each file there, and redirects to the library with a "Uploaded — processing, refresh in
  a moment" flash message. The watcher does the actual ingest.
- Filename handling: write to `inbox/<category>/<original_name>`; overwrite on collision
  (re-upload = update, consistent with ingest's `original_path` dedup). No uu/timestamp mangling.

**B3 — UI.**
- An "Upload documents" form on the library page (`documents.html`), styled with the shared
  tokens. Category `<select>` (existing categories + Uncategorized) plus a "New category" text
  input.

### C. Category management

- `POST /rag/categories/<int:cat_id>/rename/`, name `rag-category-rename`. Validates the new
  name case-insensitively; **rejects** a rename that would collide with a different existing
  shelf (no implicit merge). Updates the display name. Casing-only renames are safe; renaming to
  a different word is documented as needing re-ingest to re-scope old chunks.
- `POST /rag/categories/<int:cat_id>/delete/`, name `rag-category-delete`. Deletes the row; the
  model's existing `on_delete=SET_NULL` reassigns its documents to Uncategorized.
- UI: small **rename / delete** controls per shelf in the sidebar, shown for real categories
  only (not "All" / "Uncategorized"). Plain form POSTs, JS-optional.

---

## Data flow (upload)

```
Browser --upload--> web: POST /rag/documents/upload/
  web: validate ext, write file to data/inbox/<category>/<name>, redirect (flash: processing)
  watcher (ingest_watch): detects new file
    -> category_from_subfolder(inbox/<category>/<name>, inbox) = <category>
    -> ingest_path(file, category=<category>)
       -> get_or_create_category(<category>)  [case-insensitive]
       -> copy into managed store, chunk+embed (prose) / rows (tabular)
       -> chunk metadata category = <category>.lower()
  Library lists the doc within ~1-2s.
```

## Testing plan (ADR 0008)

- **Categories:** `get_or_create_category` normalization (whitespace, case reuse, new-create);
  the CI `UniqueConstraint` rejects a case-variant insert; the merge migration reassigns docs and
  deletes dupes (choosing seeded canonical).
- **Retrieval:** `answer_question` lowercases the category filter (`"Medical"` → filter value
  `"medical"`); `"Uncategorized"` → `"uncategorized"`.
- **Ingest:** chunk metadata `category` is written lowercased.
- **Upload:** valid file lands in `inbox/<category>/` and redirects; unsupported extension is
  rejected; "new category" routes to the right subfolder; Uncategorized routes to `inbox/`.
  (Ingest itself is exercised by existing ingest tests + the watcher; the upload test asserts the
  file placement + response, not a live embed.)
- **Category management:** rename success; rename collision rejected; delete falls docs back to
  Uncategorized (SET_NULL).

## Docs to update

- `modules/rag/README.md` — categories are case-insensitive; upload flow; category management.
- `docs/DEV.md` — the `watcher` service; how to upload; inbox location.
- **ADR 0006** — add the `watcher` service to the deployment model.
- **ADR 0009** — chunk `category` metadata is a normalized (lowercased) key; case-insensitive
  category identity.

## Risks / mitigations

- **Merge migration on unexpected data** (e.g. two seeded-name variants, or a group with docs in
  several members): the reassign-then-delete logic handles N members generically; canonical
  choice is deterministic (seeded-name first, else earliest). Covered by a test with a
  hand-built dup group.
- **Watcher not running** → uploads silently never ingest. Mitigation: the watcher is a
  `restart: unless-stopped` compose service with `depends_on: db healthy`; DEV.md documents
  checking it. (A future health indicator is a follow-up.)
- **Inbox storage growth** (upload copy + managed-store copy): accepted for Phase 1; cleanup is a
  documented follow-up.
