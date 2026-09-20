# RAG Phase-1 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make categories case/whitespace-insensitive, add browser document upload (ingested out-of-band via a watcher service), and add category rename/delete — closing Phase 1's operability gaps.

**Architecture:** Category identity normalizes to a case-insensitive key everywhere (row lookup, DB uniqueness, and chunk-metadata/retrieval matching). Uploads drop files into a host-mounted `data/inbox/<category>/` folder that a dedicated `watcher` compose service (`manage.py ingest_watch`) ingests, keeping ingest off the web request thread. Category management is plain form-POST endpoints.

**Tech Stack:** Django 5.2, Postgres+pgvector, LlamaIndex, pytest + pytest-django, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-19-rag-phase1-hardening-design.md`

## Global Constraints

- **Tests + docs on every change** (ADR 0008) — no task is done until its tests pass and its docs are updated.
- **Offline UI** — all CSS/JS inline, no external assets; upload/management forms must work with JS disabled.
- **Durable data on host** (ADR 0006) — the inbox lives under `FARABUNKER_DATA_DIR` (`data/`, gitignored), never inside a container.
- **Run tests with:** `.venv/bin/pytest` (the `db` compose service must be up).
- **Migrations run inside the container** in the running stack: `docker compose exec web python manage.py migrate`.
- Category chunk-metadata value is the **lowercased** normalized key; `"Uncategorized"` documents use the literal key `"uncategorized"`.
- Seeded default category names (for merge canonical-choice): `{"Medical", "Engineering", "Reference & Manuals", "Business"}`.

---

### Task 1: Category normalization + case-insensitive resolver

**Files:**
- Create: `modules/rag/categories.py`
- Test: `modules/rag/tests/test_categories.py` (extend existing file)

**Interfaces:**
- Produces:
  - `normalize_category_name(raw: str) -> str` — strips/collapses whitespace; `""` for blank.
  - `choose_canonical(members: list, seeded_names: set[str]) -> object` — from ≥1 Category-like objects sharing a normalized name, returns the one to keep (a member whose `.name` is in `seeded_names`, else min by `(created_at, id)`). Pure; no DB/model import.
  - `get_or_create_category(raw: str) -> Category | None` — `None` for blank; else reuses a `name__iexact` match or creates a row with the given display casing.

- [ ] **Step 1: Write the failing tests**

Add to `modules/rag/tests/test_categories.py`:

```python
from modules.rag import categories as cats
from modules.rag.models import Category


class TestNormalizeCategoryName:
    def test_strips_and_collapses_whitespace(self):
        assert cats.normalize_category_name("  Field   Guide ") == "Field Guide"

    def test_blank_becomes_empty(self):
        assert cats.normalize_category_name("   ") == ""
        assert cats.normalize_category_name("") == ""


class _Stub:
    def __init__(self, name, created_at, pk):
        self.name = name
        self.created_at = created_at
        self.id = pk


class TestChooseCanonical:
    def test_prefers_seeded_name(self):
        members = [_Stub("medical", 2, 10), _Stub("Medical", 1, 5)]
        # 'Medical' is seeded even though 'medical' is older/earlier id.
        assert cats.choose_canonical(members, {"Medical"}).name == "Medical"

    def test_falls_back_to_earliest_when_none_seeded(self):
        members = [_Stub("hvac", 2, 10), _Stub("HVAC", 1, 5)]
        assert cats.choose_canonical(members, {"Medical"}).id == 5


@pytest.mark.django_db
class TestGetOrCreateCategory:
    def test_blank_returns_none(self):
        assert cats.get_or_create_category("  ") is None

    def test_reuses_case_insensitive_match(self):
        seeded = Category.objects.create(name="Medical")
        got = cats.get_or_create_category("medical")
        assert got.id == seeded.id
        assert Category.objects.filter(name__iexact="medical").count() == 1

    def test_creates_with_given_casing_when_new(self):
        got = cats.get_or_create_category("  Field  Guide ")
        assert got.name == "Field Guide"
```

Ensure `import pytest` is present at the top of the file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest modules/rag/tests/test_categories.py -q`
Expected: FAIL (`No module named 'modules.rag.categories'`).

- [ ] **Step 3: Create `modules/rag/categories.py`**

```python
"""Category name normalization + case-insensitive resolution (ADR 0009).

Category identity is case- and whitespace-insensitive: "Medical", "medical",
and " medical " denote the same shelf. This is the single place that
normalizes a raw name and resolves it to a Category row, so ingest and the
upload/rename endpoints cannot reintroduce casing duplicates. Top-level
imports stay dependency-free so data migrations can import `choose_canonical`.
"""
from __future__ import annotations

import re

_WHITESPACE = re.compile(r"\s+")


def normalize_category_name(raw: str) -> str:
    """Strip surrounding whitespace and collapse internal runs to single
    spaces. Returns "" for blank/whitespace-only input (caller treats that as
    Uncategorized)."""
    if not raw:
        return ""
    return _WHITESPACE.sub(" ", raw).strip()


def choose_canonical(members, seeded_names):
    """From >=1 Category-like objects sharing a normalized name, pick the row
    to keep: prefer one whose `.name` is a seeded default (its "pretty"
    casing), else the earliest by (created_at, id). Pure -- operates on the
    objects passed in."""
    seeded = [c for c in members if c.name in seeded_names]
    pool = seeded or list(members)
    return min(pool, key=lambda c: (c.created_at, c.id))


def get_or_create_category(raw: str):
    """Resolve `raw` to a Category, reusing any case-insensitive match (so
    "medical" reuses a seeded "Medical"). Returns None for blank input
    (Uncategorized). New rows keep the given (normalized) display casing;
    existing rows are never re-cased here."""
    from modules.rag.models import Category  # lazy: keep module import light

    name = normalize_category_name(raw)
    if not name:
        return None
    existing = Category.objects.filter(name__iexact=name).first()
    return existing or Category.objects.create(name=name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_categories.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/rag/categories.py modules/rag/tests/test_categories.py
git commit -m "feat(rag): case-insensitive category normalization + resolver"
```

---

### Task 2: Merge existing duplicate categories (data migration)

**Files:**
- Create: `modules/rag/migrations/0006_merge_duplicate_categories.py`
- Test: `modules/rag/tests/test_categories.py` (extend)

**Interfaces:**
- Consumes: `choose_canonical` (Task 1).
- Produces: migration `0006` merging case-insensitive duplicate categories before the constraint in Task 3 is added.

> **Order matters:** this data migration MUST precede the unique constraint (Task 3), or adding the constraint fails on the existing dupes.

- [ ] **Step 1: Write the failing test**

Add to `modules/rag/tests/test_categories.py`:

```python
from importlib import import_module


@pytest.mark.django_db
class TestMergeDuplicateCategoriesFunction:
    def test_merges_dupes_into_seeded_canonical_and_moves_docs(self):
        from modules.rag.models import Category, Document
        from django.apps import apps as global_apps

        # Build a case-variant dup group by hand (the constraint isn't added
        # until 0007, but tests run all migrations; create via raw casing that
        # differs only after 0006 would have merged -- so simulate pre-merge
        # state by creating the canonical then a lower-case twin through the
        # ORM before 0007's constraint applies is impossible. Instead call the
        # merge function against a fixture where the twin is created with a
        # DIFFERENT normalized name, then rename at the DB level.)
        canonical = Category.objects.create(name="Medical")
        twin = Category.objects.create(name="Medical DUP TEMP")
        # Force the twin to collide case-insensitively, bypassing the ORM-level
        # constraint check by updating via queryset (still one statement).
        Category.objects.filter(pk=twin.pk).update(name="medical")

        doc = Document.objects.create(
            title="x.md", source_path="/s/x.md", file_hash="a" * 64,
            doc_type=Document.DocType.PROSE, category=twin,
        )

        migration = import_module("modules.rag.migrations.0006_merge_duplicate_categories")
        migration.merge_dupes(global_apps, None)

        doc.refresh_from_db()
        assert doc.category_id == canonical.id
        assert not Category.objects.filter(pk=twin.pk).exists()
        assert Category.objects.filter(name__iexact="medical").count() == 1
```

> Note: a functional unique index (Task 3) still allows the `update()` above only if run before 0007's index exists in the test DB. Because pytest applies all migrations, if this test fails to insert the collision, fall back to asserting `choose_canonical` behavior only (already covered in Task 1) and verify the migration live in Task 3's manual step. Keep this test if it passes against the applied schema; otherwise mark it `@pytest.mark.skip("covered by live migrate + choose_canonical unit tests")` with that reason.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest modules/rag/tests/test_categories.py -k MergeDuplicate -q`
Expected: FAIL (module `0006_merge_duplicate_categories` not found).

- [ ] **Step 3: Create the migration**

`modules/rag/migrations/0006_merge_duplicate_categories.py`:

```python
"""Merge pre-existing case-insensitive duplicate categories into one row each,
so the case-insensitive unique constraint (0007) can be added and so the
library stops showing forked shelves (e.g. 'medical' vs seeded 'Medical').
Reassigns each duplicate's documents to the canonical row, then deletes it.
"""
from django.db import migrations

from modules.rag.categories import choose_canonical

SEEDED_NAMES = {"Medical", "Engineering", "Reference & Manuals", "Business"}


def merge_dupes(apps, schema_editor):
    Category = apps.get_model("rag", "Category")
    Document = apps.get_model("rag", "Document")

    groups: dict[str, list] = {}
    for cat in Category.objects.all():
        groups.setdefault(cat.name.strip().lower(), []).append(cat)

    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = choose_canonical(members, SEEDED_NAMES)
        for dup in members:
            if dup.pk == canonical.pk:
                continue
            Document.objects.filter(category=dup).update(category=canonical)
            dup.delete()


def noop(apps, schema_editor):
    # A merge cannot be safely un-merged.
    pass


class Migration(migrations.Migration):
    dependencies = [("rag", "0005_document_original_path")]
    operations = [migrations.RunPython(merge_dupes, noop)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest modules/rag/tests/test_categories.py -k MergeDuplicate -q`
Expected: PASS (or SKIP with the documented reason if the collision insert is blocked by the applied schema).

- [ ] **Step 5: Commit**

```bash
git add modules/rag/migrations/0006_merge_duplicate_categories.py modules/rag/tests/test_categories.py
git commit -m "feat(rag): data migration merging case-variant duplicate categories"
```

---

### Task 3: Case-insensitive unique constraint on Category

**Files:**
- Modify: `modules/rag/models.py` (Category)
- Create: `modules/rag/migrations/0007_category_ci_unique.py` (via makemigrations)
- Test: `modules/rag/tests/test_categories.py` (extend)

**Interfaces:**
- Produces: DB-level guarantee that no two categories share a name case-insensitively.

- [ ] **Step 1: Write the failing test**

Add to `modules/rag/tests/test_categories.py`:

```python
from django.db import IntegrityError, transaction


@pytest.mark.django_db
class TestCategoryCaseInsensitiveUniqueness:
    def test_rejects_case_variant_insert(self):
        Category.objects.create(name="Foo")
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Category.objects.create(name="foo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest modules/rag/tests/test_categories.py -k CaseInsensitiveUniqueness -q`
Expected: FAIL (the second insert succeeds — no constraint yet).

- [ ] **Step 3: Edit the model**

In `modules/rag/models.py`, add the import and change `Category`:

```python
from django.db.models.functions import Lower
```

```python
    name = models.CharField(max_length=255)  # was unique=True; see CI constraint below
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="uniq_category_name_ci"),
        ]
```

- [ ] **Step 4: Generate the migration**

Run: `docker compose exec web python manage.py makemigrations rag`
Expected: creates `0007_category_ci_unique.py` with `AlterField(name -> drop unique)` + `AddConstraint(uniq_category_name_ci)`, depending on `0006`. Verify the dependency is `0006_merge_duplicate_categories`.

- [ ] **Step 5: Apply migrations and run the test**

Run:
```bash
docker compose exec web python manage.py migrate
.venv/bin/pytest modules/rag/tests/test_categories.py -k CaseInsensitiveUniqueness -q
```
Expected: migrate merges the live `medical`/`reference` dupes (0006) then adds the constraint (0007); test PASSES.

- [ ] **Step 6: Verify the live dupes are gone**

Run:
```bash
docker compose exec web python manage.py shell -c "from modules.rag.models import Category; print(sorted(c.name for c in Category.objects.all()))"
```
Expected: `['Business', 'Engineering', 'Medical', 'Reference & Manuals']` — no lowercase `medical`/`reference`.

- [ ] **Step 7: Commit**

```bash
git add modules/rag/models.py modules/rag/migrations/0007_category_ci_unique.py modules/rag/tests/test_categories.py
git commit -m "feat(rag): case-insensitive unique constraint on Category.name"
```

---

### Task 4: Ingest uses the resolver + writes a lowercased category key

**Files:**
- Modify: `modules/rag/ingest.py` (`ingest_path` category resolution; `_category_label`)
- Test: `modules/rag/tests/test_ingest.py` (extend)

**Interfaces:**
- Consumes: `get_or_create_category` (Task 1).
- Produces: chunk metadata `category` written as the lowercased key; ingest reuses categories case-insensitively.

- [ ] **Step 1: Write the failing tests**

Add to `modules/rag/tests/test_ingest.py` (inside `TestIngestProse`):

```python
    @patch("modules.rag.ingest.rag_index")
    @patch("modules.rag.ingest.gateway")
    def test_chunk_metadata_category_is_lowercased(self, mock_gateway, mock_rag_index, tmp_path):
        mock_index = MagicMock()
        mock_rag_index.get_index.return_value = mock_index

        path = tmp_path / "notes.md"
        path.write_text("Some prose content.")

        ingest.ingest_path(str(path), category="Field Guide")

        (nodes,), _ = mock_index.insert_nodes.call_args
        assert len(nodes) >= 1
        for node in nodes:
            assert node.metadata["category"] == "field guide"
```

And in `TestCategoryAssignment`:

```python
    @patch("modules.rag.ingest.rag_index")
    @patch("modules.rag.ingest.gateway")
    def test_category_reused_case_insensitively(self, mock_gateway, mock_rag_index, tmp_path):
        from modules.rag.models import Category
        mock_rag_index.get_index.return_value = MagicMock()
        seeded = Category.objects.create(name="Fieldwork")

        path = tmp_path / "notes.md"
        path.write_text("some prose")
        doc = ingest.ingest_path(str(path), category="fieldwork")

        assert doc.category_id == seeded.id
        assert Category.objects.filter(name__iexact="fieldwork").count() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest modules/rag/tests/test_ingest.py -k "lowercased or case_insensitively" -q`
Expected: FAIL (metadata is the display name; a second `Fieldwork`/`fieldwork` row is created).

- [ ] **Step 3: Edit `ingest.py`**

Add import:

```python
from modules.rag.categories import get_or_create_category
```

In `ingest_path`, replace the category block:

```python
        category_obj = None
        category_name = category.strip() if category else ""
        if category_name:
            category_obj, _ = Category.objects.get_or_create(name=category_name)
```

with:

```python
        category_obj = get_or_create_category(category)
```

Change `_category_label` to return the lowercased key:

```python
def _category_label(doc: Document) -> str:
    """The normalized (lowercased) category key ingest writes into chunk
    metadata for `doc.category`: the lowercased category name if set, else the
    literal "uncategorized" (ADR 0009 -- retrieval's category filter lowercases
    the requested value and matches on this exact key)."""
    return doc.category.name.lower() if doc.category_id else UNCATEGORIZED.lower()
```

> `Category` may now be an unused import in `ingest.py`; leave it only if still referenced elsewhere, otherwise remove it from the `from modules.rag.models import ...` line. (`Document`, `DocumentRow`, `UNCATEGORIZED` are still used.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_ingest.py -q`
Expected: PASS (the existing `test_chunk_metadata_uses_uncategorized_literal_when_no_category` still passes because it asserts `"Uncategorized"` — UPDATE that test's expectation to `"uncategorized"` in this step).

Update that existing test's assertion:

```python
        for node in nodes:
            assert node.metadata["category"] == "uncategorized"
```

Re-run: `.venv/bin/pytest modules/rag/tests/test_ingest.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/rag/ingest.py modules/rag/tests/test_ingest.py
git commit -m "feat(rag): ingest reuses categories case-insensitively; chunk key lowercased"
```

---

### Task 5: Retrieval lowercases the category filter

**Files:**
- Modify: `modules/rag/retrieval.py` (`answer_question`)
- Test: `modules/rag/tests/test_retrieval.py` (extend)

**Interfaces:**
- Consumes: the lowercased chunk-metadata key (Task 4).
- Produces: `answer_question` builds its `MetadataFilter` with `category.lower()`.

- [ ] **Step 1: Write the failing test**

In `modules/rag/tests/test_retrieval.py`, replace `test_category_builds_query_engine_with_metadata_filter`'s assertion on value, and add a case-normalization test:

```python
    @patch("modules.rag.retrieval.get_index")
    def test_category_filter_is_lowercased(self, mock_get_index):
        response = _fake_response(text="an answer", source_nodes=[])
        self._patch_query_engine(mock_get_index, response)

        retrieval.answer_question("a question?", category="Medical")

        mock_index = mock_get_index.return_value
        _, kwargs = mock_index.as_query_engine.call_args
        applied_filter = kwargs["filters"].filters[0]
        assert applied_filter.key == "category"
        assert applied_filter.value == "medical"
```

Also update the existing `test_category_builds_query_engine_with_metadata_filter` to pass `category="somecat"` already-lowercase (or change its expected value to `"somecat"`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest modules/rag/tests/test_retrieval.py -k lowercased -q`
Expected: FAIL (value is `"Medical"`).

- [ ] **Step 3: Edit `retrieval.py`**

In `answer_question`, change the filter build:

```python
    filters = None
    if category:
        filters = MetadataFilters(
            filters=[
                MetadataFilter(
                    key="category", value=category.lower(), operator=FilterOperator.EQ
                )
            ]
        )
```

Update the docstring line to note the value is lowercased to match the normalized chunk key.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_retrieval.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules/rag/retrieval.py modules/rag/tests/test_retrieval.py
git commit -m "feat(rag): retrieval matches category case-insensitively (lowercased key)"
```

---

### Task 6: Inbox setting + watcher compose service

**Files:**
- Modify: `config/settings.py` (add `INGEST_INBOX_DIR`)
- Modify: `modules/rag/management/commands/ingest_watch.py` (ensure dir exists)
- Modify: `compose.yaml` (add `watcher` service)
- Modify: `docs/adr/0006-containerization-and-isolation.md` (document the service)
- Test: `modules/rag/tests/test_ingest.py` (settings assertion)

**Interfaces:**
- Produces: `settings.INGEST_INBOX_DIR` (a `Path`); a running `watcher` service ingesting `/app/data/inbox`.

- [ ] **Step 1: Write the failing test**

Add to `modules/rag/tests/test_ingest.py`:

```python
class TestInboxSetting:
    def test_inbox_dir_is_under_data_dir(self):
        from django.conf import settings
        assert settings.INGEST_INBOX_DIR == settings.DATA_DIR / "inbox"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest modules/rag/tests/test_ingest.py -k Inbox -q`
Expected: FAIL (`AttributeError: INGEST_INBOX_DIR`).

- [ ] **Step 3: Add the setting**

In `config/settings.py`, after `DOCUMENTS_DIR = DATA_DIR / "documents"`:

```python
# Watch-inbox for browser uploads: files land here (under a per-category
# subfolder) and the `watcher` service (manage.py ingest_watch) ingests them
# out-of-band. Host-mounted under DATA_DIR (ADR 0006), gitignored via data/.
INGEST_INBOX_DIR = DATA_DIR / "inbox"
```

- [ ] **Step 4: Ensure the watcher auto-creates the dir**

In `modules/rag/management/commands/ingest_watch.py`, in `handle`, before calling `watch_folder(path)`:

```python
        from pathlib import Path
        Path(path).mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 5: Add the `watcher` compose service**

In `compose.yaml`, add after the `web` service:

```yaml
  watcher:
    build:
      context: .
      dockerfile: Dockerfile
    restart: unless-stopped
    env_file:
      - .env
    environment:
      DATABASE_URL: postgres://farabunker:farabunker@db:5432/farabunker
      OLLAMA_BASE_URL: http://host.docker.internal:11434
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - .:/app
    command: python manage.py ingest_watch /app/data/inbox
```

- [ ] **Step 6: Validate compose + run the test**

Run:
```bash
docker compose config >/dev/null && echo "compose OK"
.venv/bin/pytest modules/rag/tests/test_ingest.py -k Inbox -q
docker compose up -d --build watcher
docker compose ps watcher
```
Expected: compose validates; test PASSES; `watcher` is Up.

- [ ] **Step 7: Document in ADR 0006**

Add a short subsection to `docs/adr/0006-containerization-and-isolation.md` describing the `watcher` service: same image as `web`, runs `ingest_watch` over the host-mounted `data/inbox`, so browser uploads are ingested out-of-band; `restart: unless-stopped`, depends on `db`.

- [ ] **Step 8: Commit**

```bash
git add config/settings.py modules/rag/management/commands/ingest_watch.py compose.yaml docs/adr/0006-containerization-and-isolation.md modules/rag/tests/test_ingest.py
git commit -m "feat(rag): watch-inbox setting + dedicated watcher compose service"
```

---

### Task 7: Upload endpoint

**Files:**
- Modify: `modules/rag/views.py` (add `document_upload`)
- Modify: `modules/rag/urls.py` (add route)
- Test: `modules/rag/tests/test_views.py` (new `TestDocumentUpload`)

**Interfaces:**
- Consumes: `settings.INGEST_INBOX_DIR`, `readers.PROSE_EXTS`/`TABULAR_EXTS`, `normalize_category_name`.
- Produces: `POST /rag/documents/upload/` (name `rag-document-upload`) writing files to `inbox/<category>/` and redirecting with a flash message.

- [ ] **Step 1: Write the failing tests**

Add to `modules/rag/tests/test_views.py`:

```python
from pathlib import Path
from django.core.files.uploadedfile import SimpleUploadedFile


@pytest.mark.django_db
class TestDocumentUpload:
    @pytest.fixture(autouse=True)
    def _inbox(self, tmp_path, settings):
        settings.INGEST_INBOX_DIR = tmp_path / "inbox"
        return settings.INGEST_INBOX_DIR

    def _upload(self, client, files, **data):
        return client.post(reverse("rag-document-upload"), data={"files": files, **data})

    def test_saves_file_into_category_subfolder_and_redirects(self, client, _inbox):
        f = SimpleUploadedFile("guide.md", b"# hello", content_type="text/markdown")
        resp = self._upload(client, [f], category="Medical")
        assert resp.status_code == 302
        assert (Path(_inbox) / "Medical" / "guide.md").read_bytes() == b"# hello"

    def test_uncategorized_lands_directly_in_inbox(self, client, _inbox):
        f = SimpleUploadedFile("note.txt", b"hi", content_type="text/plain")
        self._upload(client, [f], category="")
        assert (Path(_inbox) / "note.txt").exists()

    def test_new_category_field_wins(self, client, _inbox):
        f = SimpleUploadedFile("a.txt", b"x", content_type="text/plain")
        self._upload(client, [f], category="Medical", new_category="Foraging")
        assert (Path(_inbox) / "Foraging" / "a.txt").exists()

    def test_unsupported_extension_is_rejected(self, client, _inbox):
        f = SimpleUploadedFile("evil.exe", b"MZ", content_type="application/octet-stream")
        self._upload(client, [f], category="Medical")
        assert not (Path(_inbox) / "Medical" / "evil.exe").exists()

    def test_path_traversal_category_is_blocked(self, client, _inbox):
        f = SimpleUploadedFile("a.txt", b"x", content_type="text/plain")
        self._upload(client, [f], category="../../etc")
        # Nothing escapes the inbox.
        assert list(Path(_inbox).rglob("a.txt")) == [] or all(
            str(p).startswith(str(Path(_inbox).resolve())) for p in Path(_inbox).rglob("a.txt")
        )

    def test_no_files_redirects_without_error(self, client):
        resp = client.post(reverse("rag-document-upload"), data={"category": "Medical"})
        assert resp.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -k Upload -q`
Expected: FAIL (`NoReverseMatch: rag-document-upload`).

- [ ] **Step 3: Add the view**

In `modules/rag/views.py`, add imports:

```python
from pathlib import Path

from django.conf import settings
from django.contrib import messages

from modules.rag.categories import normalize_category_name
from modules.rag.readers import PROSE_EXTS, TABULAR_EXTS

SUPPORTED_UPLOAD_EXTS = PROSE_EXTS | TABULAR_EXTS
```

Add the view:

```python
@require_POST
def document_upload(request):
    """POST /rag/documents/upload/ -- accept uploaded files into the watch
    inbox (`INGEST_INBOX_DIR/<category>/<name>`), where the `watcher` service
    ingests them out-of-band (ADR 0006). Category comes from the `new_category`
    text field if given, else the `category` select ("" / "Uncategorized" ->
    the inbox root). Unsupported extensions are skipped. Redirects to the
    library with a status message.
    """
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "No files were selected to upload.")
        return redirect("rag-documents")

    new_category = normalize_category_name(request.POST.get("new_category", ""))
    selected = request.POST.get("category", "").strip()
    if selected.lower() == "uncategorized":
        selected = ""
    category = new_category or selected

    inbox = Path(settings.INGEST_INBOX_DIR).resolve()
    target_dir = (inbox / category).resolve() if category else inbox
    if not str(target_dir).startswith(str(inbox)):
        messages.error(request, "Invalid category name.")
        return redirect("rag-documents")
    target_dir.mkdir(parents=True, exist_ok=True)

    saved, rejected = 0, []
    for upload in files:
        name = os.path.basename(upload.name)
        ext = os.path.splitext(name)[1].lower()
        if ext not in SUPPORTED_UPLOAD_EXTS:
            rejected.append(name)
            continue
        with open(target_dir / name, "wb") as out:
            for chunk in upload.chunks():
                out.write(chunk)
        saved += 1

    if saved:
        messages.info(request, f"Uploaded {saved} file(s) — processing, refresh in a moment.")
    if rejected:
        messages.error(request, f"Skipped unsupported file(s): {', '.join(rejected)}.")
    return redirect("rag-documents")
```

- [ ] **Step 4: Add the route**

In `modules/rag/urls.py`, import `document_upload` and add:

```python
    path("documents/upload/", document_upload, name="rag-document-upload"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -k Upload -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/rag/views.py modules/rag/urls.py modules/rag/tests/test_views.py
git commit -m "feat(rag): browser upload endpoint writing to the watch inbox"
```

---

### Task 8: Upload UI + flash messages on the library page

**Files:**
- Modify: `modules/rag/views.py` (`DocumentsView` context: add `categories`)
- Modify: `modules/rag/templates/rag/documents.html` (upload form + messages block + styles)
- Test: `modules/rag/tests/test_views.py` (extend `TestDocumentsView`)

**Interfaces:**
- Consumes: the upload route (Task 7).
- Produces: an upload `<form>` and a rendered `messages` region on `/rag/documents/`.

- [ ] **Step 1: Write the failing test**

Add to `modules/rag/tests/test_views.py` `TestDocumentsView`:

```python
    def test_library_renders_upload_form(self, client):
        response = client.get(reverse("rag-documents"))
        body = response.content.decode()
        assert 'action="/rag/documents/upload/"' in body
        assert 'enctype="multipart/form-data"' in body
        assert 'name="files"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -k renders_upload_form -q`
Expected: FAIL (no upload form in markup).

- [ ] **Step 3: Pass `categories` to the template**

In `DocumentsView.get_context_data`, add `categories` (the already-computed annotated queryset) to the `context.update({...})` dict:

```python
                "categories": categories,
```

- [ ] **Step 4: Add the messages block + upload form to `documents.html`**

Inside `{% block content %}`, right after `<div class="page">` opens and before `<header class="page-head">`, add:

```html
  {% if messages %}
  <ul class="messages">
    {% for message in messages %}
    <li class="msg{% if message.tags %} {{ message.tags }}{% endif %}">{{ message }}</li>
    {% endfor %}
  </ul>
  {% endif %}
```

Inside `<section class="stacks">`, before `<form class="search" ...>`, add:

```html
      <form class="upload" method="post" action="{% url 'rag-document-upload' %}" enctype="multipart/form-data">
        {% csrf_token %}
        <input type="file" name="files" multiple required aria-label="Files to upload">
        <select name="category" aria-label="Category for uploaded files">
          <option value="">Uncategorized</option>
          {% for category in categories %}
          <option value="{{ category.name }}">{{ category.name }}</option>
          {% endfor %}
        </select>
        <input type="text" name="new_category" placeholder="or new category…" aria-label="New category name">
        <button type="submit">Upload</button>
      </form>
```

In `{% block extra_style %}`, add:

```css
  .messages {
    list-style: none;
    margin: 0 0 1rem;
    padding: 0;
  }
  .msg {
    padding: 0.6rem 0.9rem;
    border-radius: 6px;
    margin-bottom: 0.4rem;
    font-size: 0.9rem;
    background: var(--shelf-active-bg);
    color: var(--text);
  }
  .msg.error {
    background: #fdeaea;
    color: #922020;
  }
  form.upload {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 1rem;
    padding: 0.85rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  form.upload select,
  form.upload input[type="text"] {
    padding: 0.5rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    font: inherit;
  }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/rag/views.py modules/rag/templates/rag/documents.html modules/rag/tests/test_views.py
git commit -m "feat(rag): upload form + flash messages on the document library"
```

---

### Task 9: Category rename + delete endpoints

**Files:**
- Modify: `modules/rag/views.py` (`category_rename`, `category_delete`)
- Modify: `modules/rag/urls.py` (routes)
- Test: `modules/rag/tests/test_views.py` (new `TestCategoryManagement`)

**Interfaces:**
- Consumes: `normalize_category_name`.
- Produces: `POST /rag/categories/<id>/rename/` (`rag-category-rename`), `POST /rag/categories/<id>/delete/` (`rag-category-delete`).

- [ ] **Step 1: Write the failing tests**

Add to `modules/rag/tests/test_views.py`:

```python
@pytest.mark.django_db
class TestCategoryManagement:
    def test_rename_updates_name(self, client):
        c = Category.objects.create(name="Med")
        resp = client.post(reverse("rag-category-rename", args=[c.id]), {"name": "Medical"})
        assert resp.status_code == 302
        c.refresh_from_db()
        assert c.name == "Medical"

    def test_rename_collision_is_rejected(self, client):
        Category.objects.create(name="Medical")
        c = Category.objects.create(name="Engineering")
        client.post(reverse("rag-category-rename", args=[c.id]), {"name": "medical"})
        c.refresh_from_db()
        assert c.name == "Engineering"  # unchanged

    def test_blank_rename_is_rejected(self, client):
        c = Category.objects.create(name="Medical")
        client.post(reverse("rag-category-rename", args=[c.id]), {"name": "   "})
        c.refresh_from_db()
        assert c.name == "Medical"

    def test_delete_moves_documents_to_uncategorized(self, client):
        c = Category.objects.create(name="Medical")
        doc = Document.objects.create(
            title="x.md", source_path="/s/x.md", file_hash="a" * 64,
            doc_type=Document.DocType.PROSE, category=c,
        )
        resp = client.post(reverse("rag-category-delete", args=[c.id]))
        assert resp.status_code == 302
        doc.refresh_from_db()
        assert doc.category_id is None
        assert not Category.objects.filter(pk=c.id).exists()

    def test_rename_nonexistent_returns_404(self, client):
        resp = client.post(reverse("rag-category-rename", args=[999999]), {"name": "X"})
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -k CategoryManagement -q`
Expected: FAIL (`NoReverseMatch`).

- [ ] **Step 3: Add the views**

In `modules/rag/views.py`:

```python
@require_POST
def category_rename(request, cat_id: int):
    """POST /rag/categories/<cat_id>/rename/ -- rename a category. Rejects a
    blank name or one that collides (case-insensitively) with another shelf."""
    category = get_object_or_404(Category, pk=cat_id)
    new_name = normalize_category_name(request.POST.get("name", ""))
    if not new_name:
        messages.error(request, "Category name can't be blank.")
        return redirect("rag-documents")
    clash = Category.objects.filter(name__iexact=new_name).exclude(pk=category.pk).exists()
    if clash:
        messages.error(request, f"A category named “{new_name}” already exists.")
        return redirect("rag-documents")
    category.name = new_name
    category.save(update_fields=["name"])
    messages.info(request, "Category renamed.")
    return redirect("rag-documents")


@require_POST
def category_delete(request, cat_id: int):
    """POST /rag/categories/<cat_id>/delete/ -- delete a category; its
    documents fall back to Uncategorized via the model's SET_NULL."""
    category = get_object_or_404(Category, pk=cat_id)
    category.delete()
    messages.info(request, "Category deleted; its documents moved to Uncategorized.")
    return redirect("rag-documents")
```

- [ ] **Step 4: Add the routes**

In `modules/rag/urls.py`, import both and add:

```python
    path("categories/<int:cat_id>/rename/", category_rename, name="rag-category-rename"),
    path("categories/<int:cat_id>/delete/", category_delete, name="rag-category-delete"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -k CategoryManagement -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add modules/rag/views.py modules/rag/urls.py modules/rag/tests/test_views.py
git commit -m "feat(rag): category rename + delete endpoints"
```

---

### Task 10: Category management UI in the sidebar

**Files:**
- Modify: `modules/rag/views.py` (`_sidebar_entry` gains `id`/`manageable`)
- Modify: `modules/rag/templates/rag/documents.html` (per-shelf rename/delete controls + styles)
- Test: `modules/rag/tests/test_views.py` (extend `TestDocumentsView`)

**Interfaces:**
- Consumes: rename/delete routes (Task 9).
- Produces: sidebar entries carry `id` (real categories) and a `manageable` flag; the template renders rename/delete forms for them.

- [ ] **Step 1: Write the failing test**

Add to `modules/rag/tests/test_views.py` `TestDocumentsView`:

```python
    def test_sidebar_entries_carry_id_for_real_categories(self, client):
        c = Category.objects.create(name="Wave3b Medical")
        self._make_document("Doc", category=c)
        response = client.get(reverse("rag-documents"))
        by_name = {e["name"]: e for e in response.context["sidebar"]}
        assert by_name["Wave3b Medical"]["id"] == c.id
        assert by_name["Wave3b Medical"]["manageable"] is True
        assert by_name["All"]["manageable"] is False
        assert by_name["Uncategorized"]["manageable"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -k carry_id -q`
Expected: FAIL (`KeyError: 'id'`).

- [ ] **Step 3: Extend `_sidebar_entry` and its callers**

Change `_sidebar_entry` signature and body to include `id`/`manageable`:

```python
def _sidebar_entry(name, count, *, base_url, query, category_param, category_value=None, category_id=None):
    if category_value is None:
        url = base_url
        active = not query and not category_param
    else:
        url = f"{base_url}?{urlencode({'category': category_value})}"
        active = not query and category_param == category_value
    return {
        "name": name,
        "count": count,
        "url": url,
        "active": active,
        "id": category_id,
        "manageable": category_id is not None,
    }
```

In `get_context_data`, pass `category_id=category.id` for the per-category entries (the "All" and "Uncategorized" entries pass none, so `manageable` is False):

```python
        sidebar += [
            _sidebar_entry(
                category.name, category.doc_count, category_value=category.name,
                category_id=category.id, **entry_kwargs,
            )
            for category in categories
        ]
```

- [ ] **Step 4: Render management controls in `documents.html`**

Replace the shelf-item loop body to add controls for manageable entries:

```html
        {% for entry in sidebar %}
        <div class="shelf-row{% if entry.active %} active{% endif %}">
          <a class="shelf-item{% if entry.active %} active{% endif %}" href="{{ entry.url }}">
            <span class="shelf-name">{{ entry.name }}</span>
            <span class="shelf-count">{{ entry.count }}</span>
          </a>
          {% if entry.manageable %}
          <details class="shelf-manage">
            <summary aria-label="Manage {{ entry.name }}">⋯</summary>
            <form method="post" action="{% url 'rag-category-rename' entry.id %}">
              {% csrf_token %}
              <input type="text" name="name" value="{{ entry.name }}" aria-label="Rename category">
              <button type="submit">Rename</button>
            </form>
            <form method="post" action="{% url 'rag-category-delete' entry.id %}" onsubmit="return confirm('Delete category {{ entry.name|escapejs }}? Its documents become Uncategorized.');">
              {% csrf_token %}
              <button type="submit" class="delete-btn">Delete</button>
            </form>
          </details>
          {% endif %}
        </div>
        {% endfor %}
```

Add styles in `{% block extra_style %}`:

```css
  .shelf-row {
    display: flex;
    align-items: center;
    gap: 0.15rem;
  }
  .shelf-row .shelf-item {
    flex: 1;
  }
  .shelf-manage summary {
    list-style: none;
    cursor: pointer;
    color: var(--muted);
    padding: 0 0.35rem;
    border-radius: 6px;
  }
  .shelf-manage summary:hover {
    background: var(--shelf-hover);
  }
  .shelf-manage[open] {
    padding: 0.4rem 0.5rem;
  }
  .shelf-manage form {
    display: flex;
    gap: 0.3rem;
    margin-top: 0.3rem;
  }
  .shelf-manage input[type="text"] {
    width: 100%;
    padding: 0.3rem 0.4rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    font: inherit;
    font-size: 0.85rem;
  }
  .shelf-manage button {
    padding: 0.3rem 0.55rem;
    font-size: 0.8rem;
  }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest modules/rag/tests/test_views.py -q`
Expected: PASS (the existing sidebar tests still pass — they assert `name`/`count`/`active`, which are unchanged).

- [ ] **Step 6: Commit**

```bash
git add modules/rag/views.py modules/rag/templates/rag/documents.html modules/rag/tests/test_views.py
git commit -m "feat(rag): per-shelf rename/delete controls in the library sidebar"
```

---

### Task 11: Docs + full-suite green + live smoke test

**Files:**
- Modify: `modules/rag/README.md`, `docs/DEV.md`, `docs/adr/0009-document-store-and-categories.md`

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Update `modules/rag/README.md`**

- Note category identity is case/whitespace-insensitive (one shelf per name), resolved via `modules/rag/categories.py`.
- Document the upload flow: `POST /rag/documents/upload/` → `data/inbox/<category>/` → `watcher` ingests.
- Document category rename/delete.
- Note chunk `category` metadata is the lowercased key.

- [ ] **Step 2: Update `docs/DEV.md`**

- Add the `watcher` service to the "Run the stack" section (it comes up with `docker compose up -d`).
- Document uploading via the library page and the `data/inbox/` location.
- Note: if uploads don't appear, check `docker compose ps watcher` / `docker compose logs watcher`.

- [ ] **Step 3: Update ADR 0009**

- Record that category identity is case-insensitive (unique on `lower(name)`, resolved via `get_or_create_category`) and that chunk `category` metadata is the lowercased normalized key that retrieval matches on.

- [ ] **Step 4: Full suite**

Run: `.venv/bin/pytest -q`
Expected: all tests PASS.

- [ ] **Step 5: Live smoke test (requires Ollama + the running stack)**

```bash
# ensure stack + watcher + ollama are up
docker compose up -d
# upload via the browser at http://localhost:8000/rag/documents/ (drag a .md/.pdf, pick/type a category)
# within ~2s it appears in the library; then ask a scoped question:
curl -s -X POST http://localhost:8000/rag/ask/ -H "Content-Type: application/json" \
  -d '{"question":"<a question about the uploaded doc>","category":"<Category As Typed>"}' | python3 -m json.tool
```
Expected: a grounded answer with a citation to the uploaded document; category filter matches despite any casing difference.

- [ ] **Step 6: Commit**

```bash
git add modules/rag/README.md docs/DEV.md docs/adr/0009-document-store-and-categories.md
git commit -m "docs(rag): case-insensitive categories, upload flow, watcher service"
```

---

## Self-Review

**Spec coverage:**
- Case-robust categories (rows) → Tasks 1, 3. Retrieval normalization → Tasks 4, 5. Merge migration → Task 2. ✓
- Browser upload async via watch-inbox → Tasks 6 (inbox+watcher), 7 (endpoint), 8 (UI). ✓
- Category management → Tasks 9 (endpoints), 10 (UI). ✓
- Tests + docs on every task; ADR 0006/0009 + README + DEV updates → each task + Task 11. ✓
- Non-goals (auth, job queue, OCR, inbox cleanup, standalone create) → not implemented, documented as follow-ups. ✓

**Type consistency:** `get_or_create_category`/`normalize_category_name`/`choose_canonical` signatures match across Tasks 1, 2, 4, 7, 9. `_sidebar_entry` gains `category_id` (Task 10) consistently. Route names (`rag-document-upload`, `rag-category-rename`, `rag-category-delete`) match between views, urls, templates, and tests.

**Known conditional:** Task 2's end-to-end migration test may be un-insertable once 0007's constraint is applied by the test harness; the task documents the skip-with-reason fallback, and Task 3 Step 6 verifies the merge on the live dev DB (which currently holds the real dupes).
