# Repo-Wide Code-Hygiene Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-09-09
**Branch:** `worktree-hygiene-sweep`, worktree `.claude/worktrees/hygiene-sweep`, off `main` @ `08e28c6`
**Status:** plan, not executed

**Goal:** Land the 49 in-scope findings of the nine-zone hygiene audit — one security clamp, three
user-visible copy/layout bugs, five query/round-trip inefficiencies, one duplicated upload
pipeline, ~110 lines of verified dead code, the shared-CSS promotion the shell was built for, and
the test-suite splits — without changing a single documented behaviour.

**Architecture:** Thirty-nine tasks, ordered by risk: the small independent correctness fixes and
verified deletions first (Tasks 1–9), then the RAG query/resource fixes (10–13), the engine-probe
caches (14–15), the small in-column de-duplication (16–21), the RAG precheck extraction (22), the
RAG upload-pipeline unification (23–28), the shared-CSS promotion plus its permanent gate (29–33),
and the mechanical test-suite splits last (34–39). Every task is independently testable, ends
green, and carries its own tests and docs. No task depends on a task after it.

**Tech Stack:** Django 5.1 (server-rendered, zero-JavaScript shell — the one inline `<style>`/
`<script>` in `foundation/templates/_shell.html`), Postgres + pgvector, LlamaIndex `PGVectorStore`,
pytest + pytest-django, ruff.

**Spec:** `<scratchpad>/CONSOLIDATED-AUDIT.md`
— the consolidated audit, finding IDs `C-01`..`C-61` and work packages WP1..WP10. The per-zone
reports (`audit-agents-chat.md`, `audit-agents-core.md`, `audit-frontend.md`,
`audit-identity-foundation-config.md`, `audit-models.md`, `audit-tests.md`, `audit-tooling.md`,
`audit-tools-rag.md`, `audit-tools-vision.md`) and `prior-audits-digest.md` sit beside it and carry
the longer evidence for any finding whose one-line note is not enough. Read the audit alongside
this plan; the plan argues from it and never re-litigates it.

## Global Constraints

Every task's requirements implicitly include this section.

1. **Worktree only.** All work happens in `<WORKTREE_ROOT>`
   on branch `worktree-hygiene-sweep`. Use `git -C <WORKTREE_ROOT> …`
   for every git command. **Never touch `<REPO_ROOT>` itself** — that
   checkout is the live production stack's bind mount.
2. **Tests run natively against a private database.** Export
   `DATABASE_URL='<TEST_DATABASE_URL>'` — the orchestrator names the branch's own preview Postgres
   and a database name nobody else is using — before every `pytest` invocation. **Never
   `localhost:5432`** (the primary stack's app database) and **never a bare `test_farabunker`**
   (two sessions racing one create/drop lifecycle). `docs/DEV.md` §"Rung 1" is the rule.
3. **Both feature-flag states, both collection orders.** A task is green only when all four runs
   are green:
   ```bash
   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
   ```
   Per-task steps name the focused run; the four full runs are the task's own exit gate.
4. **Tests and docs ship in the same commit** (ADR 0008, standing owner rule). Every code change
   carries unit tests covering the changed logic and its error paths, plus the docs it affects —
   docstrings on new code, the module README, and `docs/DEV.md`/`docs/ARCHITECTURE.md` where
   behaviour or developer workflow changes. "Write tests and update docs" is never a follow-up.
5. **Regression tests for bugs.** `C-01`, `C-02`, `C-05`, `C-06`, `C-07`, `C-08` each get a test
   that fails on the current code and passes after the fix. Write it first; run it; watch it fail.
6. **Query-count assertions for PERF findings.** Use the repo's existing
   `django_assert_num_queries` pattern — 21 test files already do this. The canonical shape to copy
   is `agents/chat/tests/test_sidebar.py:1293-1310`
   (`test_the_sidebar_is_flat_in_the_number_of_workstreams`): parametrize over a small and a large
   row count, name the expected count in one module-level constant, and assert the same count for
   both, so a future extra query is *visible* rather than merely equal.
7. **The import law holds.** Rule 1 — pure leaves (`models/contracts/`, `agents/contracts/`,
   `identity/contracts/`, `foundation/format.py`, `foundation/files.py`) are universally
   importable. Rule 2 — Django apps are column-private, with exactly four named exceptions
   (`models.registry.bindings`; `models.queue.visibility`; `foundation.ops` → `models.queue.models`
   for the RUNNING-jobs refusal; `agents.entitlements`). Rule 3 — cross-column *work* goes through
   a seam, never an import. **Every new shared helper in this plan states its column**, and no task
   introduces a new cross-column import. `foundation/ops/tests/test_import_law.py` and
   `test_column_boundaries.py` are the gates and must stay green.
8. **No new static CSS/JS pipeline.** The standing ruling (prior-audits digest, Table 2) is: *"No
   static JS/CSS pipeline (`collectstatic`, `static/chat.css`) — the single inline
   `<style>`/`<script>` shell stays."* WP4 is **additive to the existing inline shell** and is
   exactly what that ruling endorses. No task adds a static asset, a build step, or a second
   `<style>` mechanism.
9. **No AI model names in documentation.** The repo is going public. README, ADR, ROADMAP and
   `docs/` prose describe capabilities generically ("the default image model", "a distilled
   few-step family") and never name a model family, checkpoint, or GGUF filename. Code identifiers
   (template module names, operation keys) are code, not docs, and are out of scope for this rule.
10. **Files held for the poller PR — do not touch, in any task:**
    - `agents/chat/templates/chat/conversation.html`
    - `agents/chat/templates/chat/_turn_card.html`
    - `agents/chat/tests/test_thread.py`
    - `agents/chat/README.md`
    A peer session (branch `chat-poller-cleanup`, worktree `.claude/worktrees/vision-generation`)
    holds all four. If a task appears to need one of them, stop and report instead of editing.
11. **Findings gated on PR #84 (`chat-attachments`) — not in this plan, do not implement:**
    - **C-37** (`.pulse-dot`: the CSS exists on `#84` at `conversation.html:127-133`; port it
      there, do not delete the JS here) — also a held file under constraint 10.
    - **C-38** (`.delete-disclosure` rules at `agents/chat/templates/chat/base.html:1531-1533`
      are dead on `main` but **live on `#84`** at `conversation.html:224`; the false test comment
      at `agents/chat/tests/test_thread.py:1165-1169` is a held file).
    Note that `agents/chat/templates/chat/base.html` is *not* itself on `#84`'s changed-file list
    and Tasks 30/32 edit other regions of it — but **the `.delete-disclosure` block at
    `base.html:1531-1533` stays exactly as it is** until the `#84` decision lands.
12. **Findings excluded by owner decision or "no action" — not in this plan:** C-13 and C-53 (no
    action), C-16 (poller doctrine, owner call), C-36, C-48, C-49, C-55 (owner decisions), C-11 and
    C-12 (trivial N+1s in files no task in this plan touches — see "Not planned" at the foot).
13. **Commit style.** Conventional, scoped, matching `git log --oneline -30`: `fix(rag): …`,
    `refactor(chat): …`, `docs(chat): …`, `test(ops): …`, `perf(rag): …` is not in use — use
    `fix(...)` for a measured inefficiency. Every commit message ends with:
    ```
    Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_<id>
    ```
14. **Nothing is committed to `main` and no branch is merged by this plan.** Each task commits to
    `worktree-hygiene-sweep`. Merge readiness is a separate gate (whole-feature UAT + whole-branch
    review) and is not this plan's business.
15. **No hand-written database edits.** Schema and bookkeeping change through migrations and
    tested management commands only. Task 8 is the one migration in this plan.
16. **Every `path:line` in this plan is measured against `main` @ `08e28c6`.** Earlier tasks insert
    and delete lines in files later tasks cite, so **before editing any file an earlier task has
    already changed, re-derive the location with `git grep -n`** rather than trusting the number
    written here. The plan's Self-review §2 lists the five files where this bites hardest; the rule
    applies everywhere. A line number is a pointer to a piece of code, and the code is the authority.
17. **Verbatim means verbatim, typographic quotes included.** Several operator-facing strings in
    this codebase use typographic quotation marks (`“ ”`), not ASCII `"` — for example
    `models/registry/views.py:1459` and `:1561`. Copy such strings from the file, never retype them:
    the existing tests assert on substrings and would not catch a silent change to operator copy.

---

## Task list at a glance

| # | Findings | One line |
|---|---|---|
| 1 | C-01 | Vision stops storing and serving a client-declared `image/svg+xml` |
| 2 | C-02 | The chat start box's blurb names the agent it describes |
| 3 | C-04 | `console.html`/`model_sets.html` inherit `_settings.html`'s flash rules |
| 4 | C-51 | The library's Workstream column joins the width-compaction list |
| 5 | C-40, C-41, C-44, C-45, C-47, C-54 | The one-line hygiene batch |
| 6 | C-34, C-35 | Two zero-caller functions go |
| 7 | C-39 | The two `_locator_*` back-compat aliases go |
| 8 | C-42 | `Category.description` and its column go |
| 9 | C-43, C-50, C-60 | Dead markup, a dead class, a permanently-skipped test |
| 10 | C-05 | `document_labels_bulk` goes flat in the document count |
| 11 | C-09 | `restamp_document_chunks` takes the store shape as an argument |
| 12 | C-06 | Every `PGVectorStore` this column opens gets disposed |
| 13 | C-03 | `permits()` gains `readable_documents()`'s conversation-scope axis |
| 14 | C-07a | The registry console caches engine health for 30s |
| 15 | C-07b | The vision tool caches its engine preflight for 30s |
| 16 | C-26 | Six attachment-registry setters share one validation body |
| 17 | C-27 | Two engine-registration handlers share one support-boundary check |
| 18 | C-28 | `agents/labels.py`: one diff-and-audit writer, one group reader, one row reader |
| 19 | C-29, C-30 | One taint tag-writer; one `visible_workstreams(...).filter(pk=…)` reader |
| 20 | C-31 (jobs half) | `tools/rag/jobs.py`: one stranded-row failer, one preview truncation |
| 21 | C-33 | The compose engine-env block becomes a YAML anchor, per file |
| 22 | C-15 | The endpoint-dedup + health-check prologue is extracted once |
| 23 | C-14 | One shared stage-and-enqueue helper for both upload paths |
| 24 | C-17, C-32, C-46 | The extension twin, the gate prologues, the hardened sidecar read |
| 25 | C-08 | The textless-page scan happens once per upload, not twice |
| 26 | C-18 | `store.py` owns the `extract.json` and `work/` sub-paths |
| 27 | C-25 | `_upload_sha256` gives way to `foundation.files.sha256_file` |
| 28 | C-31 (media half) | `media.py`/`ingest.py`/`transcode.py` small same-file repeats |
| 29 | C-20 | `--error-bg`/`--error-text`/`--danger-text`/`--mono` become shell tokens |
| 30 | C-21, C-52 | `.messages`/`.msg` move to `_shell.html`; the rag copies go |
| 31 | C-19 | One `foundation/templates/_messages.html`, included at 15 sites |
| 32 | C-22, C-23, C-24 | `.banner`, `.muted`, `.empty`, `.delete-disclosure > summary` move up |
| 33 | — | `test_css_ownership.py` walks the whole repo, not just `agents/chat/` |
| 34 | C-59 | One registry-reset fixture factory in `models/contracts/testing.py` |
| 35 | C-61 | One `make_pdf_bytes` in `tools/rag/tests/_helpers.py` |
| 36 | C-58 | `models/contracts/tests/` exists and owns its own unit tests |
| 37 | C-57 | The seven `*SettingsUpdate` classes lose their shared shape |
| 38 | C-56a | `models/registry/tests/test_views.py` splits on its 31 dividers |
| 39 | C-56b | `tools/rag/tests/test_views.py` splits on its class boundaries |

Total diff estimate: ~1,000–1,300 lines removed, ~350 added, plus the test splits (~10,000 lines
moved between files, ~550 lines net removed).

---

### Task 1: Vision stops trusting the client's `Content-Type` (C-01)

**Files:**
- Modify: `tools/vision/store.py` (new `media_type_for_upload`, beside `_basename` at `:66`)
- Modify: `tools/vision/services.py:732`, `tools/vision/services.py:877`
- Modify: `tools/vision/views.py:1387` (inside `_serve_stored_file`, defined at `:1367`)
- Modify: `tools/vision/README.md` §"Storage layout" (heading at `:770`)
- Test: `tools/vision/tests/test_store.py`, `tools/vision/tests/test_views_gallery.py`

**Column:** everything stays inside `tools/vision`. **Do not import `tools.rag.ingest._media_type_for`**
— `tools.rag` is column-private to `tools.vision` under the import law's rule 2. Copy the *shape*,
not the module.

**Interfaces:**
- Produces: `tools.vision.store.media_type_for_upload(uploaded) -> str` — the MIME type to record
  for an uploaded file, derived from its filename extension; `""` for an unknown extension.
- Produces: `tools.vision.views._SERVABLE_IMAGE_TYPES: frozenset[str]` — the closed set of MIME
  types `_serve_stored_file` will name in a `Content-Type` header.

**Why two halves.** Deriving the type at store time stops *new* rows carrying a client-chosen
header. Clamping at serve time is what protects the rows already in the database — every row
written before today took `uploaded.content_type` verbatim. Neither half alone closes C-01.

**Current code, verbatim.** `views.py:1387`:

```python
    safe_media_type = media_type if media_type and media_type.startswith("image/") else "application/octet-stream"
```

`services.py:732` and `services.py:877`, identically:

```python
        media_type=getattr(uploaded, "content_type", "") or "",
```

`_serve_stored_file` has three callers: `views.py:1407` (`output_file`), `views.py:1445`
(`input_file`), and `tools/vision/maintenance.py:275` (`engine_file`).

- [ ] **Step 1: Write the failing serve-time regression test**

Add to `tools/vision/tests/test_views_gallery.py`, beside the existing file-serving tests:

```python
@pytest.mark.parametrize("stored_type", ["image/svg+xml", "image/svg+xml; charset=utf-8"])
def test_an_svg_media_type_is_never_named_in_a_content_type_header(stored_type, tmp_path):
    """C-01. `image/*` is not an allowlist: `image/svg+xml` is an image
    type a browser renders as a DOCUMENT -- scripts and all -- in the
    origin that fetched it. A row carrying it (however it got there:
    every row written before this fix stored the uploading client's own
    `Content-Type` verbatim) must be served as a download, not as markup."""
    stored = tmp_path / "payload.svg"
    stored.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>')
    response = views._serve_stored_file(
        RequestFactory().get("/"), str(stored), stored_type, "gone")
    assert response.headers["Content-Type"] == "application/octet-stream"


def test_an_ordinary_png_is_still_served_as_a_png(tmp_path):
    """The other direction: the clamp must not turn every image into a
    download. A gate that refuses everything is not a gate."""
    stored = tmp_path / "ok.png"
    stored.write_bytes(b"\x89PNG\r\n\x1a\n")
    response = views._serve_stored_file(
        RequestFactory().get("/"), str(stored), "image/png", "gone")
    assert response.headers["Content-Type"] == "image/png"
```

- [ ] **Step 2: Run it and watch the first fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_views_gallery.py -k content_type_header`
Expected: the SVG test FAILS (both parameters come back as `image/svg+xml`, because
`startswith("image/")` is true for them); the PNG test passes.

- [ ] **Step 3: Replace the prefix clamp with a closed allowlist**

In `tools/vision/views.py`, at module level beside the other module constants:

```python
# THE CLOSED SET `_serve_stored_file` WILL NAME IN A `Content-Type`
# HEADER. `media_type.startswith("image/")` was not an allowlist:
# `image/svg+xml` is an `image/*` type a browser renders as a DOCUMENT,
# script elements and all, in the origin that fetched it -- stored XSS on
# any row whose media type came from the uploading client, which before
# `store.media_type_for_upload` was every row. Every raster type this
# platform can produce or accept is listed; anything else (SVG included,
# and any future type nobody has thought about yet) falls through to
# `application/octet-stream`, which downloads instead of rendering.
_SERVABLE_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/webp", "image/gif",
    "image/bmp", "image/tiff", "image/avif",
})
```

Replace `views.py:1387` with:

```python
    base_type = (media_type or "").split(";", 1)[0].strip().lower()
    safe_media_type = base_type if base_type in _SERVABLE_IMAGE_TYPES else "application/octet-stream"
```

Rewrite the comment block immediately above it (`views.py:1382-1386`) so it says *allowlist*, names
SVG as the reason, and stops describing a prefix test.

- [ ] **Step 4: Run the serving suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_views_gallery.py tools/vision/tests/test_views_engine_files.py`
Expected: PASS.

- [ ] **Step 5: Write the failing store-time test**

Add to `tools/vision/tests/test_store.py`:

```python
@pytest.mark.parametrize(("filename", "expected"), [
    ("a.png", "image/png"), ("a.PNG", "image/png"),
    ("a.jpg", "image/jpeg"), ("a.jpeg", "image/jpeg"),
    ("a.webp", "image/webp"), ("a.gif", "image/gif"),
    ("a.svg", ""), ("a", ""), ("a.exe", ""),
])
def test_media_type_comes_from_the_extension_not_the_client(filename, expected):
    """C-01. `SimpleUploadedFile` lets its caller declare any
    `content_type` it likes -- exactly what a browser does, and exactly
    what this platform used to record and re-serve. The stored media type
    must be derived from the name whose parsing we control."""
    uploaded = SimpleUploadedFile(filename, b"x", content_type="image/svg+xml")
    assert store.media_type_for_upload(uploaded) == expected
```

- [ ] **Step 6: Run it and watch it fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_store.py -k media_type_comes_from`
Expected: FAIL — `AttributeError: module 'tools.vision.store' has no attribute 'media_type_for_upload'`.

- [ ] **Step 7: Add the helper to `tools/vision/store.py`**

Beside `_basename` (`store.py:66`):

```python
# EXTENSION -> MIME, SERVER-SIDE. `tools.rag.ingest._media_type_for` takes
# the same approach for the same reason and is deliberately NOT imported:
# `tools.rag` is column-private to `tools.vision` under the import law's
# rule 2, and a shared table would be a cross-column import for nine lines
# of data. The two tables also answer different questions -- rag's covers
# every ingestable medium, this one covers the raster images a generation
# can take as an input or produce as an output.
_MEDIA_TYPE_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".avif": "image/avif",
}


def media_type_for_upload(uploaded) -> str:
    """The MIME type to record for `uploaded`, derived from its filename
    extension -- never from `uploaded.content_type`, which is whatever the
    posting client chose to send and is what gets re-served to the next
    viewer.

    `""` for any extension not in `_MEDIA_TYPE_BY_EXT`, `.svg` included
    (this platform neither generates SVG nor accepts it as a generation
    input). An empty media type makes `views._serve_stored_file` fall
    through to `application/octet-stream`, which downloads rather than
    renders -- the safe default, reached by omission rather than by a
    special case.
    """
    name = _basename(getattr(uploaded, "name", "") or "")
    return _MEDIA_TYPE_BY_EXT.get(Path(name).suffix.lower(), "")
```

(`Path` is already imported in `store.py`; confirm before adding an import.)

- [ ] **Step 8: Use it at both store sites**

`tools/vision/services.py:732` and `tools/vision/services.py:877` — replace each

```python
        media_type=getattr(uploaded, "content_type", "") or "",
```

with

```python
        media_type=store.media_type_for_upload(uploaded),
```

- [ ] **Step 9: Run the store and services suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_store.py tools/vision/tests/test_services.py`
Expected: PASS. If a services test asserts a stored `media_type` that came from a
`SimpleUploadedFile(content_type=...)` argument, repoint the assertion to the extension-derived
value — that is the behaviour change this task exists to make, and such a test is asserting the bug.

- [ ] **Step 10: Update the README**

In `tools/vision/README.md` §"Storage layout" (heading at `:770`), add one short paragraph: the
stored `media_type` is derived from the file's own extension, never from the posting client's
`Content-Type` header; and `_serve_stored_file` names a closed allowlist of raster types in the
response, so anything else downloads instead of rendering. **No model, family or checkpoint names**
(Global Constraint 9).

**Deliberately unchanged:** `tools/vision/forms.py:77-81` (the `forms.FileField` +
`ClearableFileInput` for `kind == "file"`) keeps accepting whatever is posted. Adding an upload-time
extension refusal there changes what the product accepts, which is a product decision, not this
finding; C-01 is about what is *stored and re-served*, and the two halves above close it without
narrowing the accept set. Say so in the README paragraph so the next reader does not re-open it.

- [ ] **Step 11: Full gate** — the four runs from Global Constraint 3. Expected: all green.

- [ ] **Step 12: Commit**

```
git add tools/vision/store.py tools/vision/services.py tools/vision/views.py \
        tools/vision/README.md tools/vision/tests/test_store.py \
        tools/vision/tests/test_views_gallery.py
git commit -m "fix(vision): the served media type is derived and allowlisted, never the client's"
```

Full message body:

```
C-01. `media_type.startswith("image/")` admitted `image/svg+xml`, which a
browser renders as a document in the fetching origin -- stored XSS on any
row whose type came from the uploading client, which was every row. Two
halves: `store.media_type_for_upload` derives the type from the filename
extension at write time, and `_serve_stored_file` names a closed raster
allowlist at read time, so rows already in the database are covered too.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 2: The start box's blurb names the agent it describes (C-02)

**Files:**
- Modify: `agents/chat/templates/chat/index.html:91`
- Test: the module that already renders `chat/index.html` — find it with
  `git grep -ln "chat-index" agents/chat/tests/`. Do **not** use `agents/chat/tests/test_thread.py`
  (Global Constraint 10).

**The bug, verbatim.** `chat/index.html:88-91`:

```django
{% if agents %}
{% url 'chat-start' as composer_action_url %}
{% include "chat/_composer.html" with composer_mode="start" ... composer_agents=agents ... %}
<p class="muted">{{ agents.0.description }}</p>
{% endif %}
```

The picker at `chat/_composer.html:104-114` renders `<select name="agent">` over the same
`composer_agents` list. The view (`agents/chat/views/conversations.py`, `_index_context`) sets
`agents = list(visible_agents(principal))` — a real multi-row list. The page carries no JavaScript
(the shell's zero-JS doctrine, not an oversight), so the blurb cannot follow the selection. It is
correct only while exactly one agent is installed.

**The fix, and why this one and not the other.** Making the blurb *track* the picker needs a
`<script>`; Global Constraint 8 forbids adding one. So the honest server-rendered fix is to make the
sentence true instead of making it move: the blurb belongs to `agents.0`, which is the option the
`<select>` renders selected and the agent a Start press will actually use — so say whose blurb it is.

- [ ] **Step 1: Write the failing regression test**

```python
def test_the_start_box_blurb_names_the_agent_it_describes(client):
    """C-02. `{{ agents.0.description }}` was correct only while exactly
    one agent was installed: the picker beneath it lists every visible
    agent, and this page has no JavaScript to follow the selection. The
    blurb now names its agent, so a reader who changes the picker can see
    the sentence is about a different one instead of silently reading the
    wrong description."""
    first = make_agent(name="Researcher", description="Finds things.")
    make_agent(name="Drafter", description="Writes things.")
    body = client.get(reverse("chat-index")).content.decode()
    assert "Finds things." in body
    assert first.name in body.split("Finds things.")[0][-120:]
    assert "Writes things." not in body
```

Use the module's own existing factories — `agents/chat/tests/_helpers.py` already has `make_agent`,
`make_user`, `sign_in`; do not invent new ones. Order the two agents so `Researcher` is `agents.0`
(match whatever ordering `visible_agents` applies; read it before writing the test).

- [ ] **Step 2: Run it and watch it fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/chat/tests -k start_box_blurb`
Expected: FAIL — the rendered blurb carries no agent name.

- [ ] **Step 3: Make the blurb name its agent**

`agents/chat/templates/chat/index.html:91` becomes:

```django
{% comment %}
C-02, WHOSE BLURB THIS IS, SAID OUT LOUD. `agents.0` is the option the
`<select name="agent">` below (`chat/_composer.html`) renders selected, so
this paragraph describes what pressing Start does right now. Because this
page carries no JavaScript -- the shell's own doctrine, not an oversight
-- it cannot follow the picker afterwards, and naming the agent is what
makes that honest: a reader who changes the picker sees that the sentence
below it is about a different agent, instead of reading a description
that silently belongs to someone else. Correct at one agent and at ten.
{% endcomment %}
<p class="muted">{{ agents.0.name }} — {{ agents.0.description }}</p>
```

- [ ] **Step 4: Run the test** — Expected: PASS.

- [ ] **Step 5: Full gate** — the four runs from Global Constraint 3.

- [ ] **Step 6: Commit**

```
git add agents/chat/templates/chat/index.html agents/chat/tests
git commit -m "fix(chat): the start box's blurb says which agent it describes"
```

Body:

```
C-02. `{{ agents.0.description }}` sat under a picker listing every
visible agent, on a page with no JavaScript to follow the selection --
correct only while exactly one agent was installed. The blurb now names
its agent, so the sentence is true whichever option is showing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 3: The two registry pages inherit `_settings.html`'s flash rules (C-04)

**Files:**
- Modify: `models/registry/templates/inference/console.html:39`, delete `:124-128`
- Modify: `models/registry/templates/inference/model_sets.html:36`, delete `:60-61`
- Test: `foundation/ops/tests/test_css_ownership.py` — the existing
  `test_no_settings_page_retypes_a_rule_settings_html_already_owns` is the gate that proves it

**The finding, precisely.** Both pages extend `inference/base.html`, which extends
`_settings.html`. Both open `{% block extra_style %}` **without** `{{ block.super }}`, so they
discard `_settings.html:92-101`'s `.messages`/`.msg` and then restate part of it locally.
`_settings.html:70-76`'s own comment records the convention — *"Every one of the ten now writes
`{{ block.super }}` first"* — and these two are not among the ten.

The audit's `VS✎` correction stands and must not be "fixed": **`model_sets.html:76` renders
`<div class="msg …">`, not a bulleted `<ul class="messages">`.** There is no live rendering bug here,
only the opt-out and the restatement. (The `<div>`-vs-`<li>` divergence is C-19's business, Task 31.)

**Why the existing gate cannot do the red-green here.**
`test_no_settings_page_retypes_a_rule_settings_html_already_owns` walks
`_templates_extending_settings_html` (`foundation/ops/tests/test_css_ownership.py:340-350`), which
matches `_EXTENDS_SETTINGS_RE = re.compile(r'{%\s*extends\s+"_settings\.html"\s*%}')` (`:336`) — a
**direct** extends only. Both leaf pages read `{% extends "inference/base.html" %}`; only
`inference/base.html:1` extends `_settings.html`. So the gate has never seen these two pages and
adding `{{ block.super }}` will not make it see them. This task therefore writes its own source-text
pin, and Task 33 is what makes the *transitive* case a permanent gate.

- [ ] **Step 1: Write the failing pin**

In `foundation/ops/tests/test_css_ownership.py`, beside the existing checks:

```python
@pytest.mark.parametrize("template", [
    "models/registry/templates/inference/console.html",
    "models/registry/templates/inference/model_sets.html",
])
def test_the_registry_pages_inherit_the_settings_flash_rules(template):
    """C-04. Both pages extend `inference/base.html`, which extends
    `_settings.html` -- and both opened `extra_style` WITHOUT
    `{{ block.super }}`, discarding `_settings.html:92-101`'s
    `.messages`/`.msg` and then restating part of it locally.
    `_settings.html:70-76` records the convention ("Every one of the ten
    now writes `{{ block.super }}` first"); these two were not among the
    ten.

    A SOURCE-TEXT PIN, not the sibling gate above: that gate matches a
    DIRECT `{% extends "_settings.html" %}` only, and these two pages
    reach `_settings.html` through `inference/base.html`, so it has never
    seen them. Task 33 generalises the gate to the transitive case; this
    pin is what makes the fix red-green today."""
    text = (REPO_ROOT / template).read_text()
    style = _style_block(text)
    assert "{% block extra_style %}{{ block.super }}" in text, (
        f"{template} overrides extra_style without inheriting it")
    settings_selectors = {selector for selector, _ in _rules(_style_block(_SETTINGS_HTML.read_text()))}
    # SELECTOR-LEVEL, NOT RULE-LEVEL, and that is the whole point of
    # writing a new pin rather than reusing the sibling gate's `own &
    # settings_rules` intersection. `_rules` compares whitespace-
    # normalised (selector, BODY) pairs, and `model_sets.html:60-61`'s
    # `.msg` body is not byte-equal to `_settings.html:93-101`'s: it
    # spells `.6rem` where the parent spells `0.6rem`, orders `border`
    # before `border-radius`, and omits `color: var(--text)` entirely. A
    # rule-level check therefore sees no overlap at all on that page and
    # goes green while the restatement is still there -- exactly the
    # failure this task exists to fix, reproduced in its own gate. What
    # is wrong here is not that the bodies match; it is that the page
    # redeclares a selector its parent already owns, having inherited it.
    redeclared = sorted(
        selector for selector, _ in _rules(style) if selector in settings_selectors)
    assert not redeclared, (
        f"{template} redeclares a selector _settings.html already owns: {redeclared}")
```

**One deliberate consequence:** `console.html` keeps a genuinely different `.msg` (`--ok-bg`/
`--ok-text`) and `model_sets.html` keeps a genuinely different `.msg.error`, so a bare
selector-level check would flag both. Restrict the assertion to the selectors that are pure
restatements — `{".messages", ".msg"}` — and say so in the pin's docstring, listing the two local
variants as intentional. A gate that forces a page to give up a rule it genuinely differs on is a
gate that will be deleted.

- [ ] **Step 2: Run it and watch both parameters fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_css_ownership.py -k registry_pages_inherit`
Expected: FAIL on the `block.super` assertion for both pages.

- [ ] **Step 3: Add `{{ block.super }}` to both pages**

`console.html:39`: `{% block extra_style %}` → `{% block extra_style %}{{ block.super }}`
`model_sets.html:36`: the same edit.

- [ ] **Step 4: Re-run the pin and watch the second assertion take over**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_css_ownership.py -k registry_pages_inherit`
Expected: still FAIL, now on the *redeclared-selector* assertion — `console.html` with
`['.messages']` and `model_sets.html` with `['.msg']`.

**Note what did and did not change.** `_style_block` strips `{{ … }}` before parsing
(`test_css_ownership.py:193-200`) and reads only the leaf's own literal text, so the set of rules
each page declares is byte-identical before and after Step 3. Adding `{{ block.super }}` did not make
the restatements *visible to the parser*; it made them **wrong**, because the page now inherits the
rules it is restating. The two assertions simply fire in source order, and the first one passing is
what lets the second speak.

- [ ] **Step 5: Delete the two restatements the parent now supplies**

`console.html:124-128`, delete entirely:

```css
  .messages {
    list-style: none;
    margin: 0 0 1rem;
    padding: 0;
  }
```

**Keep** `console.html:129-144` (`.msg`, `.msg.error`, `.msg.warning`): its `.msg` is a genuine local
variant (`background: var(--ok-bg); color: var(--ok-text);`, not `_settings.html`'s
`var(--panel)`/`var(--border)` recipe), and after `{{ block.super }}` it still wins on source order.

`model_sets.html:60-61`, delete the `.msg` rule only:

```css
  .msg { padding: .6rem .9rem; border: 1px solid var(--border); border-radius: 6px;
         margin-bottom: .4rem; font-size: .9rem; background: var(--panel); }
```

**Keep** the `.msg.error` line that follows it — `border-color: var(--danger); color: var(--danger);`
is a genuine local variant, and `_settings.html:78-86`'s own comment records that `.msg.error`
deliberately did *not* move up because the ten pages disagree on its spelling.

This deletion is pixel-neutral: the only declaration `_settings.html`'s `.msg` adds is
`color: var(--text)`, and `_settings.html:87-89` already records that `_shell.html`'s `body` sets
the same colour, so the five pages whose copy omitted it are unmoved by inheriting it.

- [ ] **Step 6: Run the pin green, then prove it is not vacuous**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_css_ownership.py`
Expected: PASS. Then temporarily re-add the deleted `.messages` block to `console.html`, re-run —
expected FAIL naming that file and `['.messages']` — and remove it again.

- [ ] **Step 7: Run the registry suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests`
Expected: PASS. Three classes in `test_views.py` assert on rendered console markup
(`TestConsoleViewSections`, `TestMachineTableLayout`, `TestRegisteredTableLayout`); none reads CSS,
so none should move.

- [ ] **Step 8: Full gate, then commit**

```
git add models/registry/templates/inference/console.html models/registry/templates/inference/model_sets.html \
        foundation/ops/tests/test_css_ownership.py
git commit -m "refactor(registry): the two model pages inherit the settings flash rules"
```

Body:

```
C-04. `console.html` and `model_sets.html` opened `extra_style` without
`{{ block.super }}`, discarding `_settings.html`'s `.messages`/`.msg` and
then restating part of it locally -- the two pages the AUDIT-2 B10
convention missed. Both now inherit first; each keeps only the rule it
genuinely differs on. No pixels move: the one declaration the parent adds
is a colour `_shell.html`'s own `body` already sets.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 4: The library's Workstream column joins the compaction list (C-51)

**Files:**
- Modify: `tools/rag/templates/rag/documents.html:416-420`
- Test: `tools/rag/tests/test_views.py` (the `TestDocumentsView` class, `:2066`) — or, after Task 39,
  `tools/rag/tests/test_views_documents.py`

**The finding, verbatim.** `documents.html:416-420`:

```css
  td.doc-title { width: 100%; min-width: 22rem; }
  .doc-labels,
  .doc-category,
  .doc-type,
  .doc-actions { width: 1%; }
```

The Workstream column was added later — `<th>Workstream</th>` at `:860`, `<td class="workstream">`
at `:939` — and never joined the list. It is therefore the one column still negotiating for space
against `td.doc-title`'s `width: 100%` claim, which is exactly the fault the rule was written to
fix. Separately, `:922` emits `<span class="doc-tabular-note">` and no rule anywhere defines it, so
it renders at body size beside the muted metadata it is meant to sit with.

- [ ] **Step 1: Write the two failing pins**

Source-text pins, the same "pure file text, no template engine" method
`foundation/ops/tests/test_css_ownership.py` uses — a rendered-HTML assertion cannot see a CSS rule.
Add to `TestDocumentsView` (or as module-level functions beside it):

```python
def test_every_narrow_library_column_is_in_the_width_compaction_list():
    """C-51. `td.doc-title` claims `width: 100%`, so every OTHER column
    must claim `width: 1%` or it negotiates against it. The Workstream
    column was added after this rule was written and never joined the
    list, which made it the only column still competing for the title's
    space -- the exact fault the rule exists to prevent. Pinned on the
    template's own text: every class a narrow `<td>` carries must appear
    in the compaction selector."""
    text = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/documents.html").read_text()
    compaction = re.search(r"((?:\s*\.[\w-]+,)+\s*\.[\w-]+)\s*\{\s*width:\s*1%", text)
    assert compaction, "the width-compaction rule itself is gone -- this pin is broken"
    listed = set(re.findall(r"\.([\w-]+)", compaction.group(1)))
    # TWO COLUMNS ARE EXEMPT AND BOTH DECLARE THEIR OWN WIDTH, which is
    # what makes them exempt rather than forgotten: `doc-title` claims
    # `width: 100%` (the whole point of the rule), and `col-select` claims
    # `width: 1.5rem` at `documents.html:457-461` -- a checkbox column
    # sized to its checkbox, not to its content. Anything else that
    # declares no width of its own must be in the compaction list.
    declares_own_width = {"doc-title", "col-select"}
    used = set(re.findall(r'<td class="([\w-]+)"', text)) - declares_own_width
    assert used <= listed, f"narrow columns missing from the compaction list: {sorted(used - listed)}"


def test_the_tabular_note_span_has_a_rule():
    """C-51, second half. `<span class="doc-tabular-note">` is emitted at
    the row level and styled nowhere."""
    text = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/documents.html").read_text()
    assert 'class="doc-tabular-note"' in text, "the span itself is gone -- this pin is broken"
    assert re.search(r"\.doc-tabular-note\s*\{", text)
```

- [ ] **Step 2: Run and watch both fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py -k "compaction_list or tabular_note_span"`
Expected: FAIL — `['workstream']` missing from the list; no `.doc-tabular-note` rule. (If the failure
names `['col-select', 'workstream']`, the `declares_own_width` exemption above was dropped —
`col-select` carries `width: 1.5rem` of its own at `documents.html:457-461` and must **not** be added
to the compaction list.)

- [ ] **Step 3: Add the column and the missing rule**

`documents.html:416-420` becomes:

```css
  {% comment %}
  EVERY COLUMN EXCEPT THE TITLE claims `width: 1%`, which in a table
  layout means "as narrow as your content allows" -- that is what lets
  `td.doc-title`'s `width: 100%` actually take the slack instead of
  negotiating with five other claimants. The Workstream column (C-51) was
  added after this rule was written and was the one left out, so it was
  the only column still competing for the title's space.
  {% endcomment %}
  td.doc-title { width: 100%; min-width: 22rem; }
  .doc-labels,
  .doc-category,
  .doc-type,
  .workstream,
  .doc-actions { width: 1%; }
  {% comment %}
  The tabular note rides beside the row's other metadata and had no rule
  at all, so it rendered at body size next to muted text (C-51).
  {% endcomment %}
  .doc-tabular-note { color: var(--muted); font-size: 0.85rem; }
```

- [ ] **Step 4: Run the two pins** — Expected: PASS.

- [ ] **Step 5: Full gate, then commit**

```
git add tools/rag/templates/rag/documents.html tools/rag/tests/test_views.py
git commit -m "fix(rag): the library's Workstream column stops competing with the title"
```

Body:

```
C-51. The width-compaction selector list predates the Workstream column,
so that column was the only one still negotiating for space against
`td.doc-title`'s `width: 100%` -- the exact fault the rule exists to fix.
`.doc-tabular-note`, emitted at the row level and styled nowhere, gets the
muted-metadata rule its neighbours already have.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 5: The one-line hygiene batch (C-40, C-41, C-44, C-45, C-47, C-54)

Six independent one-to-six-line items, batched because each is too small to be worth its own review
gate and none touches another's file in a way that could conflict. If any one of them turns out to
be non-trivial, split it out rather than growing this task.

**Files:**
- Modify: `foundation/ops/restore.py:42` (C-40)
- Modify: `models/queue/views.py:48`, `tools/rag/views.py:36`, `tools/rag/views.py:74` (C-41)
- Modify: `tools/vision/views.py:1521-1526` (C-44)
- Modify: `models/queue/views.py:159`, `tools/rag/services.py:752`, `tools/rag/views.py:1414` (C-45)
- Modify: `tools/__init__.py:1-13` (C-47)
- Modify: `tools/rag/jobs.py:359-391` (C-54)
- Test: `foundation/ops/tests/test_import_law.py` (new pin for C-47),
  `tools/rag/tests/test_jobs.py` (new pin for C-54)

- [ ] **Step 1: C-40 — delete the constant nobody reads**

`foundation/ops/restore.py:41-42` currently:

```python
STOP_ALL_COMMAND = "docker compose stop web worker watcher"
START_ALL_COMMAND = "docker compose start web worker watcher"
```

Delete line 42. `STOP_ALL_COMMAND` is used at `restore.py:180`; `START_ALL_COMMAND` has zero
references repo-wide (`git grep -n START_ALL_COMMAND` → the definition only). If the surrounding
comment explains the pair, reword it to describe the one that remains.

- [ ] **Step 2: C-41 — delete five unused imports**

`models/queue/views.py:48` — delete the whole line; both names are unused in the file:
```python
from models.contracts.jobkinds import get_job_kind, resolve_dotted_path
```

`tools/rag/views.py:36` — `NoReverseMatch` is unused, `reverse` is used:
```python
from django.urls import NoReverseMatch, reverse
```
becomes
```python
from django.urls import reverse
```

`tools/rag/views.py:74` — `_search_result_for` is used at `views.py:2131`; the other two are not:
```python
from tools.rag.retrieval import _search_result_for, locator_for, locator_text_for
```
becomes
```python
from tools.rag.retrieval import _search_result_for
```

None of the three lines carries a `# noqa` re-export marker; confirm that before deleting.

- [ ] **Step 3: C-44 — stop shadowing the `messages` module**

`tools/vision/views.py:17` is `from django.contrib import messages`. `_create_page_response`
(def at `:1492`) rebinds the name at `:1521`:

```python
    for key, messages in form.errors.items():
        target = key if key in page_form.fields else None
        existing = page_form.errors.get(target or "__all__", [])
        for message in messages:
```

Rename the loop variable — the shadow is scoped to this function and causes no live bug today
(`messages.error(...)` is never called inside it), which is exactly why it is a trap for the next
editor:

```python
    for key, field_messages in form.errors.items():
        target = key if key in page_form.fields else None
        existing = page_form.errors.get(target or "__all__", [])
        for message in field_messages:
```

Existing coverage: `tools/vision/tests/test_views_generate.py`
(`test_invalid_params_are_a_400_that_re_renders_the_form`,
`test_non_xhr_re_render_does_not_lose_an_uploaded_file`). No new test — a pure rename with no
behaviour to assert.

- [ ] **Step 4: C-45 — two ruff SIM fixes and one documented refusal**

`models/queue/views.py:159` (**SIM108**):
```python
    if has_total:
        text = f"{done_fmt} of {total_fmt}"
    else:
        text = done_fmt
```
becomes
```python
    text = f"{done_fmt} of {total_fmt}" if has_total else done_fmt
```

`tools/rag/services.py:752` (**SIM105**):
```python
    try:
        target_dir.rmdir()
    except OSError:
        pass
```
becomes
```python
    with contextlib.suppress(OSError):
        target_dir.rmdir()
```
Add `import contextlib` to the module's stdlib import block, in alphabetical position.

`tools/rag/views.py:1414` (**SIM115**) — **do not "fix" this one.** The reported pattern is:
```python
    response = FileResponse(
        open(source_path, "rb"),
        as_attachment=download,
        filename=filename,
        content_type=content_type,
    )
```
`FileResponse` streams from the handle *after* the view returns, so wrapping it in
`with open(...) as f:` would close the file before Django reads a byte of it — the rule is a false
positive here, not a finding. Record that in the code rather than leaving the next reader to
rediscover it:
```python
    response = FileResponse(
        open(source_path, "rb"),  # noqa: SIM115 -- FileResponse owns and closes this handle; it must outlive this frame
        as_attachment=download,
        filename=filename,
        content_type=content_type,
    )
```
Note for the executor: **the repo has no ruff configuration file** (no `pyproject.toml`,
`ruff.toml`, `setup.cfg`, and no CI step) — ruff is run ad hoc with an explicit `--select`. The
`# noqa` above is therefore documentation for humans first and a linter directive second. Do not add
a ruff config as part of this task; that is a tooling decision nobody has made.

- [ ] **Step 5: C-47 — the `tools/` docstring names the whole allowlist**

`tools/__init__.py:9-11` currently claims the *only* sanctioned cross-column import a `tools/*` app
may make is `models.registry.bindings`. A reader trusting that literally would misdiagnose eight
legal, gate-approved imports as bugs. Rewrite the docstring to name all nine, each with a live call
site, derived from `foundation/ops/tests/test_import_law.py`'s own allowlists:

```
- `models.registry.bindings` (rule 2's named exception; NOT `.models`/`.views`)
- `models.queue.visibility`  -- IA-1 rows-vs-content answers (`tools/rag/views.py:55`)
- `identity.contracts`       -- `tools/rag/jobs.py:76`
- `identity.access`          -- `tools/rag/views.py:48`
- `identity.request`         -- `tools/rag/views.py:50`
- `identity.audit`           -- `tools/rag/jobs.py:75`
- `agents.contracts`         -- `tools/rag/access.py:18`
- `agents.entitlements`      -- IA-2 tool-access door (`tools/rag/views.py:784`, lazy)
- `agents.workstreams`       -- the workstream seam (`tools/rag/access.py:629`, lazy)
```

Then write the pin that stops it going stale again — this is the real deliverable of the item. In
`foundation/ops/tests/test_import_law.py`, beside the existing gates:

```python
def test_the_tools_package_docstring_names_every_allowlist_this_gate_enforces():
    """C-47. `tools/__init__.py`'s docstring is what a reader consults
    before deciding whether an import in this column is legal. It named
    ONE of the nine sanctioned names, so eight legal imports read as
    violations. This pin is the reason it cannot drift again: the
    docstring must name every module the allowlists in THIS FILE permit a
    `tools/*` module to import."""
    docstring = (REPO_ROOT / "tools" / "__init__.py").read_text()
    permitted = {"models.registry.bindings", "models.queue.visibility"}
    permitted |= set(IDENTITY_PERMITTED)
    permitted |= set(AGENTS_PERMITTED)
    missing = sorted(name for name in permitted if name not in docstring)
    assert not missing, (
        "tools/__init__.py's docstring does not name these sanctioned "
        f"cross-column imports: {missing}"
    )
```

`IDENTITY_PERMITTED` and `AGENTS_PERMITTED` already exist in that module (`:606` and `:835`);
read them and use the real names rather than retyping their contents.

- [ ] **Step 6: C-54 — correct the stale ComfyUI comment**

`tools/rag/jobs.py:359-391` (inside `plan_ingest`) claims *"ComfyUI's adapter has NO
`loaded_footprint` reporting at all"* and *"it implements neither `loaded_footprint` nor `unload`"*.
Both methods exist and are fully implemented: `models/contracts/engines/comfyui.py:731`
(`loaded_footprint`) and `:827` (`unload`). Rewrite the passage so it says what is true now — the
scheduler's rule 2(b) fallback still applies to any adapter that reports no footprint, and this
planner still declares the honest thing about the job rather than copying `vision.generate`'s
`exclusive=True` — without asserting anything about which adapter implements what. Do **not** name a
model, family or checkpoint (Global Constraint 9).

Then pin it, source-text, so the claim cannot come back:

```python
def test_the_ingest_planner_does_not_claim_an_engine_lacks_methods_it_has():
    """C-54. `plan_ingest`'s comment asserted that an image-generation
    adapter implements neither `loaded_footprint` nor `unload`. Both have
    existed for some time. A comment that describes a limitation the code
    fixed is worse than no comment: it is read as current."""
    text = (Path(settings.BASE_DIR) / "tools/rag/jobs.py").read_text()
    assert "implements neither" not in text
    assert "NO `loaded_footprint`" not in text
```

- [ ] **Step 7: Run the touched suites**

Run:
```
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  foundation/ops/tests models/queue/tests tools/rag/tests/test_jobs.py \
  tools/rag/tests/test_views.py tools/rag/tests/test_services.py tools/vision/tests/test_views_generate.py
```
Expected: PASS.

- [ ] **Step 8: Full gate, then commit**

```
git add foundation/ops/restore.py foundation/ops/tests/test_import_law.py models/queue/views.py \
        tools/__init__.py tools/rag/jobs.py tools/rag/services.py tools/rag/views.py \
        tools/rag/tests/test_jobs.py tools/vision/views.py
git commit -m "cleanup: the one-line hygiene batch — dead constant, five imports, a shadow, two nits, two stale comments"
```

Body:

```
C-40 `START_ALL_COMMAND` had no reader. C-41 five imports had no user.
C-44 a loop variable shadowed `django.contrib.messages` inside a function
that will one day want to call it. C-45 two ruff SIM nits fixed and the
third (`FileResponse`'s handle must outlive the frame) refused in writing.
C-47 `tools/__init__.py` named one of nine sanctioned cross-column
imports, so eight legal ones read as violations -- now named, and pinned
against the gate's own allowlists. C-54 a comment claimed an adapter
implements neither `loaded_footprint` nor `unload`; it implements both.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 6: Two zero-caller functions go (C-34, C-35)

**Files:**
- Delete: `agents/workstreams.py:407-459` (`may_post_to_conversation`)
- Modify: `tools/rag/tests/_helpers.py:118-124` (the docstring that names it)
- Delete: `tools/rag/index.py:249-251` (`get_storage_context`)
- Modify: `agents/README.md` §"The seam: `agents/workstreams.py`" (heading at `:564`) if it
  enumerates the seam's functions — check with `git grep -n may_post_to_conversation agents/README.md`
- Test: `agents/tests/test_workstream_seam.py`, `tools/rag/tests/test_index.py`

**Every reference, verified with `git grep -n` in the worktree:**

| Name | References |
|---|---|
| `may_post_to_conversation` | `agents/workstreams.py:407` (the definition) and `tools/rag/tests/_helpers.py:122` (a docstring naming it as the validator for a test module deleted in the same round-13 commit). **No call site anywhere.** Its own docstring at `agents/workstreams.py:442` still claims "ROUND 11's ONE CALLER: `tools.rag.views.document_upload`" — that caller no longer invokes it. Absent from PR #84 as well, so no merge hazard. |
| `get_storage_context` | `tools/rag/index.py:249` (the definition). **Nothing else, anywhere.** Also orphaned on `#84`. |

- [ ] **Step 1: Re-verify both are still zero-caller**

Run:
```
git grep -n "may_post_to_conversation"
git grep -n "get_storage_context"
```
Expected: exactly the references in the table above. If a caller has appeared, stop and report.

- [ ] **Step 2: Delete `may_post_to_conversation` and repoint the one docstring**

Delete `agents/workstreams.py:407-459` in full (function, docstring, and the blank line separating
it from its neighbour).

`tools/rag/tests/_helpers.py:118-124` currently reads:

```python
def make_conversation(**overrides):
    """A minimal `Conversation` row, through `apps.get_model` -- the
    SAME reason `_workstream`/`make_agent` above give, for round 11's
    own `test_upload_attachment.py` (the chat attach form's
    `conversation` field, validated through `agents.workstreams.
    may_post_to_conversation`). ...
```

Rewrite the parenthetical so it no longer names a deleted function or a deleted test module — say
what the helper is for now, not what round 11 used it for.

- [ ] **Step 3: Delete `get_storage_context`**

`tools/rag/index.py:249-251` in full:

```python
def get_storage_context() -> StorageContext:
    """StorageContext wired to the pgvector store, for use during ingestion."""
    return StorageContext.from_defaults(vector_store=get_vector_store())
```

Then check whether `StorageContext` is still imported for another use in that module; if it is now
unused, delete the import too (and say so in the commit body).

- [ ] **Step 4: Write the pins that keep them gone**

In `agents/tests/test_workstream_seam.py`:

```python
def test_the_seam_exposes_no_conversation_post_gate():
    """C-34. `may_post_to_conversation` outlived its one caller by two
    rounds and was still being described in a test helper's docstring as
    a live validator. The wall's posting rules are answered by
    `workstream_scope`/`workstream_visible`; a second, unreachable answer
    to the same question is how two answers drift apart."""
    assert not hasattr(workstreams, "may_post_to_conversation")
```

In `tools/rag/tests/test_index.py`:

```python
def test_the_index_module_exposes_no_unused_storage_context_builder():
    """C-35. `get_storage_context` had no caller in this repo or on any
    open branch. `get_vector_store()` is the one door to the store."""
    assert not hasattr(rag_index, "get_storage_context")
```

- [ ] **Step 5: Run the touched suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/tests tools/rag/tests/test_index.py`
Expected: PASS.

- [ ] **Step 6: Update the README if it enumerates the seam**

`agents/README.md` §"The seam: `agents/workstreams.py`" (`:564`) lists what the seam offers. If
`may_post_to_conversation` appears there, remove the row and say in one clause why (no caller).

- [ ] **Step 7: Full gate, then commit**

```
git add agents/workstreams.py agents/tests/test_workstream_seam.py agents/README.md \
        tools/rag/index.py tools/rag/tests/_helpers.py tools/rag/tests/test_index.py
git commit -m "cleanup(agents,rag): two functions nothing has called since round 13"
```

Body:

```
C-34 `agents.workstreams.may_post_to_conversation` outlived its one
caller; its own docstring still named that caller, and a rag test helper
still named the function. C-35 `tools.rag.index.get_storage_context` has
never had a caller on `main` or on any open branch. Both deleted, both
pinned, and the two stale docstrings repointed.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 7: The two `_locator_*` back-compat aliases go (C-39)

**Files:**
- Delete: `tools/rag/retrieval.py:238-239`
- Modify: `tools/rag/tests/test_retrieval.py` — eight call sites
- Modify: `models/queue/views.py:95`, `tools/rag/services.py:418` (prose mentions)
- Modify: `docs/adr/0014-media-ingestion.md:615-616,920-921` (prose mentions)

**The audit's correction matters here:** these are *not* zero-caller. They are ORPHAN-TESTONLY —
`tools/rag/tests/test_retrieval.py` calls them about eight times, so deleting the aliases means
repointing those assertions **first**.

**The aliases, verbatim** (`retrieval.py:238-239`):

```python
_locator_for = locator_for
_locator_text_for = locator_text_for
```

**Every reference to repoint, verified in the worktree:**

| Path:line | What it is | Action |
|---|---|---|
| `tools/rag/retrieval.py:238` | alias definition | delete |
| `tools/rag/retrieval.py:239` | alias definition | delete |
| `tools/rag/retrieval.py:167,171,219,220,231` | the canonical `locator_for`/`locator_text_for` definitions and their own docstrings | leave; re-read the docstrings and drop any sentence that exists only to explain the alias |
| `tools/rag/tests/test_retrieval.py:313,316,324,327,328,331,345,347` | eight real calls | repoint to `locator_for` / `locator_text_for` |
| `models/queue/views.py:95` | docstring prose naming `_locator_text_for` | repoint the name |
| `tools/rag/services.py:418` | docstring prose naming `_locator_for` | repoint the name |
| `docs/adr/0014-media-ingestion.md:615-616,920-921` | ADR prose naming the underscored spellings | repoint the names |

- [ ] **Step 1: Re-verify the reference list**

Run: `git grep -n "_locator_for\|_locator_text_for"`
Expected: exactly the rows above. Any additional hit is a new caller — add it to the list before
proceeding.

- [ ] **Step 2: Repoint the eight test calls first**

In `tools/rag/tests/test_retrieval.py`, replace every `retrieval._locator_for(` with
`retrieval.locator_for(` and every `retrieval._locator_text_for(` with
`retrieval.locator_text_for(`. Example, at `:313`:

```python
    assert retrieval._locator_text_for({"start_seconds": 760.0}, "12:40") == " at 12:40"
```
becomes
```python
    assert retrieval.locator_text_for({"start_seconds": 760.0}, "12:40") == " at 12:40"
```

- [ ] **Step 3: Run the retrieval tests with the aliases still present**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_retrieval.py`
Expected: PASS. This is the safety step — the repointed tests must be green *before* the aliases go,
so a failure here is a repointing mistake and not a deletion consequence.

- [ ] **Step 4: Delete the aliases and add the pin**

Delete `retrieval.py:238-239`. Add to `tools/rag/tests/test_retrieval.py`:

```python
def test_the_module_exposes_no_underscored_locator_aliases():
    """C-39. `_locator_for`/`_locator_text_for` were back-compat aliases
    whose only remaining callers were this test module's own assertions --
    a back-compat shim kept alive by the tests written to exercise it."""
    assert not hasattr(retrieval, "_locator_for")
    assert not hasattr(retrieval, "_locator_text_for")
```

- [ ] **Step 5: Repoint the four prose mentions**

`models/queue/views.py:95`, `tools/rag/services.py:418`,
`docs/adr/0014-media-ingestion.md:615-616` and `:920-921` — replace the underscored spelling with
the public one in each sentence. The ADR is prose about behaviour, not a frozen record of a
decision, so correcting a function name in it is in scope; do not change anything else about it.

- [ ] **Step 6: Run the touched suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_retrieval.py models/queue/tests foundation/ops/tests/test_docs_sync.py`
Expected: PASS.

- [ ] **Step 7: Full gate, then commit**

```
git add tools/rag/retrieval.py tools/rag/tests/test_retrieval.py models/queue/views.py \
        tools/rag/services.py docs/adr/0014-media-ingestion.md
git commit -m "cleanup(rag): the two underscored locator aliases go, with their eight callers"
```

Body:

```
C-39. `_locator_for`/`_locator_text_for` were back-compat aliases for
`locator_for`/`locator_text_for` whose only remaining callers were the
eight assertions in `test_retrieval.py` -- a shim kept alive by the tests
written against it. Callers repointed first, then the aliases deleted, and
four prose mentions across two modules and one ADR repointed with them.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 8: `Category.description` and its column go (C-42)

**Files:**
- Modify: `tools/rag/models.py:37` (delete the field)
- Create: `tools/rag/migrations/0021_remove_category_description.py`
- Modify: `tools/rag/tests/test_categories.py:20,25,35,40` (the only readers/writers)
- Test: `tools/rag/tests/test_categories.py`, `tools/rag/tests/test_models.py`

**The finding.** `tools/rag/models.py:37` is `description = models.TextField(blank=True)`. The field
is set and read nowhere in production: `tools/rag` has no `forms.py` and no `admin.py`, no template
renders it, and the seed migration `0004_seed_default_categories.py` uses `get_or_create(name=name)`
only. The four touches in `test_categories.py:20,25,35,40` are the tests exercising the field for
its own sake. The column was added by `0003_category_document_category.py:19`; the latest migration
in the tree is `0020_documentattachment_turn_id.py`.

**Global Constraint 15 applies:** the column goes through a migration, never a hand edit.

- [ ] **Step 1: Re-verify nothing production reads it**

Run:
```
git grep -n "\.description" -- tools/rag | grep -v tests
git grep -rn "description" -- tools/rag/templates
```
Expected: no hit that resolves to `Category.description`. If one exists, stop — this becomes an
owner decision, not a deletion.

- [ ] **Step 2: Repoint the four test touches**

Read `tools/rag/tests/test_categories.py:15-45`. Each of `:20,25,35,40` sets or asserts
`description`; delete those clauses. Where a test's whole point was the field (e.g. "a category
carries a description"), delete the test — it is testing a field nothing uses. Where the field is
incidental to a test about `name` uniqueness or seeding, just drop the kwarg.

- [ ] **Step 3: Run the category tests green before touching the model**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_categories.py`
Expected: PASS with the field still on the model.

- [ ] **Step 4: Delete the field**

`tools/rag/models.py:37`: delete `description = models.TextField(blank=True)`. If the class
docstring or a nearby comment describes it, delete that sentence too.

- [ ] **Step 5: Generate the migration**

Run:
```
DATABASE_URL='<TEST_DATABASE_URL>' .venv/bin/python manage.py makemigrations rag --name remove_category_description
```
Expected: `tools/rag/migrations/0021_remove_category_description.py` containing exactly one
`migrations.RemoveField(model_name="category", name="description")`. Read the generated file; if it
contains anything else, an unrelated model change has been picked up — revert and investigate.

Add a module docstring to the generated migration saying why the column went (never set, never read,
no form and no admin in this app), in the house style the other migrations in this directory use.

- [ ] **Step 6: Add the pin**

In `tools/rag/tests/test_models.py`:

```python
def test_category_carries_no_description_column():
    """C-42. `Category.description` was declared, migrated, and then
    never set or read -- no form, no admin, no template, and a seed
    migration that only ever passed `name`. A column nothing writes is a
    column nothing can be trusted to have."""
    assert not any(f.name == "description" for f in Category._meta.get_fields())
```

- [ ] **Step 7: Run the migration and the suites**

Run:
```
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests
```
Expected: PASS (pytest-django applies the new migration when it builds the test database).

- [ ] **Step 8: Full gate, then commit**

```
git add tools/rag/models.py tools/rag/migrations/0021_remove_category_description.py \
        tools/rag/tests/test_categories.py tools/rag/tests/test_models.py
git commit -m "cleanup(rag): Category.description, which nothing ever set or read"
```

Body:

```
C-42. The column was declared in 0003 and never reached production: this
app has no forms.py and no admin.py, no template renders it, and the seed
migration passes `name` only. Its four touches were in the tests written
for the field itself. Removed through a migration, per the standing rule
that schema changes never happen by hand.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 9: Dead markup, a dead class, a permanently-skipped test (C-43, C-50, C-60)

**Files:**
- Modify: `tools/rag/templates/rag/documents.html:744-758` and `:268` (C-43)
- Modify: `agents/chat/templates/chat/_form_errors.html:14` (C-50)
- Delete: `tools/rag/tests/test_categories.py:176-212` (C-60)
- Test: `tools/rag/tests/test_views.py` (the `TestDocumentsView` class)

**C-43 — the library-page placement chooser never renders.** `documents.html:744-758`:

```django
{% comment %}
THE THREE-STATE CHOOSER, extracted to `rag/_placement_choice.html`
(walk-fix batch F10) so `rag/panels/documents.html`'s in-stream
upload form renders the identical markup rather than a second,
drift-prone copy. ...
{% endcomment %}
{% include "rag/_placement_choice.html" %}
{% if placement_state == "choose" %}
<p class="hint">Placement decides where it lives. Labels decide who may read it.</p>
{% endif %}
{% if workstream %}<input type="hidden" name="workstream" value="{{ workstream.pk }}">{% endif %}
```

`grep -n placement_state tools/rag/views.py` → **zero hits**; `DocumentsView.get_context_data`
(`views.py:228-441`) never sets it, so the include renders its "no state" branch and the hint
paragraph never appears at all.

**The fragment itself stays** — `rag/_placement_choice.html` has two other live consumers
(`tools/rag/templates/rag/panels/documents.html:95`, reached from
`agents/chat/templates/chat/base.html:1359-1390` and `chat/workstream.html:75`). Only
`documents.html`'s own dead include and hint are the deletion target. Deleting them also makes the
`documents.html` half of the standing owner-call **O5** (`.placement` promotion) moot: there is no
longer anything on that page for the promoted CSS to reach.

**C-50 — a class with no rule.** `agents/chat/templates/chat/_form_errors.html:14` carries
`<div class="banner warn form-errors">`. `git grep -n "\.form-errors"` finds no CSS rule anywhere in
the repo. (`tools/vision/templates/vision/_form_errors.html` uses the same token, and
`vision/create.html` targets `#form-errors` as an *element id* for its own script — neither is a
rule that styles chat's copy. Leave both alone.)

**C-60 — a permanently-skipped test.** `tools/rag/tests/test_categories.py:176-212` (the audit's
`177-211` clips the class line at 176 and the last assertion at 212). Its own skip reason says the
collision it tests for is un-creatable once migration 0007's case-insensitive unique constraint
exists, and names the real coverage: `choose_canonical`'s unit tests plus live-DB migrate
verification. It cannot be un-skipped without undoing 0007.

- [ ] **Step 1: Write the failing pin for C-43**

Add beside the Task 4 pins:

```python
def test_the_library_page_renders_no_placement_chooser():
    """C-43. The chooser's include and its hint sat on the library page
    reading a context key (`placement_state`) `DocumentsView` never sets
    -- markup that could not render under any request. The fragment
    itself is live elsewhere (the in-stream upload panel); this pin is
    about THIS page, and about the context key, not the fragment."""
    views_text = (Path(settings.BASE_DIR) / "tools/rag/views.py").read_text()
    assert "placement_state" not in views_text, (
        "DocumentsView now sets placement_state -- the chooser is live again "
        "and this deletion must be reconsidered, not the pin loosened")
    page = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/documents.html").read_text()
    assert "placement_state" not in page
    assert '_placement_choice.html' not in page


def test_the_placement_fragment_still_has_its_live_consumer():
    """The other half: deleting a dead include must not be mistaken for
    deleting the fragment. The in-stream upload panel still renders it."""
    panel = (Path(settings.BASE_DIR) / "tools/rag/templates/rag/panels/documents.html").read_text()
    assert 'include "rag/_placement_choice.html"' in panel
```

- [ ] **Step 2: Run and watch the first fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py -k placement`
Expected: the first FAILS (the page still names `placement_state`), the second PASSES.

- [ ] **Step 3: Delete the dead markup**

Delete `documents.html:744-758` entirely — the `{% comment %}` block, the `{% include %}`, the
`{% if placement_state == "choose" %}` paragraph, **and** the `{% if workstream %}` hidden input,
which exists only to accompany the chooser. Read the surrounding form first and confirm the hidden
input has no other consumer in `DocumentsView`'s POST handler; if it does, keep that one line and
say so in the commit body.

Then delete the now-unreachable `.placement .hint` rule at `documents.html:268`.

- [ ] **Step 4: Run the two pins** — Expected: both PASS.

- [ ] **Step 5: C-50 — drop the class with no rule**

`agents/chat/templates/chat/_form_errors.html:14`:
```django
<div class="banner warn form-errors">
```
becomes
```django
<div class="banner warn">
```
`banner` and `warn` are both real, shared rules (`chat/base.html:1476-1481`, and after Task 32
`_shell.html`); `form-errors` styles nothing and hooks nothing. Confirm with
`git grep -n "form-errors"` that no test, script or selector reads it in the chat hierarchy before
deleting. **Do not** touch `tools/vision/templates/vision/_form_errors.html` or
`vision/create.html` — vision's `#form-errors` is a live element id.

Add to whichever chat test module already renders a form-error path (find it with
`git grep -ln "_form_errors" agents/chat/tests/`; **not** `test_thread.py`, Global Constraint 10):

```python
def test_the_chat_form_error_banner_carries_no_class_nothing_styles():
    """C-50. `form-errors` had no CSS rule anywhere and was read by no
    test and no script -- a token that looks like a hook and is not one."""
    text = (Path(settings.BASE_DIR) / "agents/chat/templates/chat/_form_errors.html").read_text()
    assert "form-errors" not in text
```

- [ ] **Step 6: C-60 — delete the permanently-skipped test**

Delete `tools/rag/tests/test_categories.py:176-212` — the `TestMergeDuplicateCategoriesFunction`
class, its `@pytest.mark.skip` decorator, and the whole test body. If `import_module` or
`global_apps` become unused in that file afterwards, delete those imports too.

In the module docstring (or beside the tests that remain), add one sentence recording what happened
and where the coverage lives, so the deletion is not read as a coverage loss:

```python
# C-60: the merge-function test that used to sit here was permanently
# skipped -- migration 0007's case-insensitive unique constraint makes the
# collision it constructed un-creatable, so it could never be un-skipped
# without undoing 0007. Its own skip reason named the real coverage, which
# is where it still lives: `choose_canonical`'s unit tests, plus the
# live-database migrate verification.
```

- [ ] **Step 7: Run the touched suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests agents/chat/tests`
Expected: PASS, with one fewer skipped test in the summary line.

- [ ] **Step 8: Full gate, then commit**

```
git add tools/rag/templates/rag/documents.html tools/rag/tests/test_views.py \
        tools/rag/tests/test_categories.py agents/chat/templates/chat/_form_errors.html agents/chat/tests
git commit -m "cleanup(rag,chat): a chooser that never rendered, a class with no rule, a test that could never run"
```

Body:

```
C-43 the library page's placement chooser read a context key
`DocumentsView` never sets, so its include and hint could not render under
any request; the fragment stays, live in the in-stream upload panel.
C-50 `form-errors` had no rule and no reader. C-60 a permanently-skipped
test documented an already-applied migration and named its own real
coverage. Deleting the chooser also settles the documents.html half of the
standing `.placement` promotion question: there is nothing left to reach.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 10: `document_labels_bulk` goes flat in the document count (C-05)

**Files:**
- Modify: `tools/rag/views.py:690-716` (`document_labels_bulk`'s target loop)
- Modify: `tools/rag/services.py:116-120` (`documents_targeted_for_labelling` — add the prefetch)
- Modify: `tools/rag/labels.py:48-52` (`document_label_ids` — read the prefetch when it is there)
- Test: `tools/rag/tests/test_document_label_page.py`

**Column:** all four files are `tools/rag`. No new helper, no boundary question.

**The finding.** `views.py:699-704`:

```python
    for document in targets:
        if not may_label_document(principal, document):
            skipped += 1
            continue
        current = set(document_label_ids(document))
        updated = (current | wanted) if action == "apply" else (current - wanted)
```

Three to four queries per document, over an unbounded target set:

1. `may_label_document(principal, document)` (`access.py:678-716`) with `is_admin_`/`owned` left at
   `None` recomputes both — two identity queries per row. **Its own docstring already says a caller
   amortizing per-row cost should pre-compute them, and names `DocumentsListView` as the caller that
   does.**
2. `document_label_ids(document)` (`labels.py:48-52`) is
   `document.entitlement_labels.values_list("entitlement_id", flat=True)` — a fresh query per row
   *even under a prefetch*, because `.values_list()` on a related manager does not consult the
   prefetch cache.
3. `documents_targeted_for_labelling` (`services.py:116-120`) applies no `prefetch_related` at all.

**The pattern to copy — `views.py:332-333,344,356`**, inside `DocumentsListView`, which already does
exactly this:

```python
        is_admin_flag = is_admin(principal)
        owned_ids = owned_entitlement_ids(principal)
        ...
                "may_label": may_label_document(principal, document,
                                                is_admin_=is_admin_flag, owned=owned_ids),
        ...
                "may_administer": may_administer_document(
                    principal, document, is_admin_=is_admin_flag, owned=owned_ids),
```

and at `:302-303` prefetches `entitlement_labels__entitlement`.

**Deliberately not touched:** `set_document_labels(principal, document, updated, ...)` at
`views.py:712-714` stays a per-document write. Its own comment at `:706-711` says why (per-document
audit rows), and batching it is a different decision nobody has made.

- [ ] **Step 1: Write the failing query-count test**

The shape to copy is `tools/rag/tests/test_pin_page.py:266-280` — the same expected count at N=1 and
N=25, so a future extra query is *visible* rather than merely equal. Add to
`tools/rag/tests/test_document_label_page.py`:

```python
# The absolute pin's number, named once so both parametrized runs assert
# the SAME count and a reviewer can see what it is. It moves only when the
# bulk endpoint genuinely gains a query -- the event this pin exists for.
_BULK_LABEL_BASELINE_QUERIES = 12  # measure and replace before committing


class TestBulkLabellingIsFlatInTheDocumentCount:
    """C-05. `document_labels_bulk` paid 3-4 queries per document over an
    unbounded target set: `may_label_document` recomputed `is_admin_` and
    `owned` per row (its own docstring asks a bulk caller to hoist them),
    and `document_label_ids` issued a fresh `values_list` per row even
    under a prefetch. The list view three hundred lines above it already
    does both. Same expected count at 1 document and at 25."""

    @pytest.mark.parametrize("documents", [1, 25])
    def test_the_bulk_endpoint_costs_the_same_at_one_document_and_at_twenty_five(
        self, client, documents, django_assert_num_queries,
    ):
        entitlement = make_entitlement()
        targets = [make_document() for _ in range(documents)]
        with posture("enterprise"):
            with django_assert_num_queries(_BULK_LABEL_BASELINE_QUERIES):
                response = client.post(reverse("rag-document-labels-bulk"), {
                    "action": "apply",
                    "entitlement": [str(entitlement.pk)],
                    "documents": [str(d.pk) for d in targets],
                })
        assert response.status_code == 302
```

Measure the real N=1 count first (run the test with a deliberately wrong constant and read the
assertion message), put that number in `_BULK_LABEL_BASELINE_QUERIES`, and confirm the N=25 run is
the one that fails.

- [ ] **Step 2: Run it and watch the N=25 case fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_document_label_page.py -k flat_in_the_document_count`
Expected: N=1 PASSES, N=25 FAILS with a count roughly `baseline + 3*24` — the per-document cost made
visible.

- [ ] **Step 3: Prefetch the labels on the target queryset**

`tools/rag/services.py:116-120`, both return paths gain the prefetch:

```python
    # C-05: the bulk-label loop reads every target's labels. Prefetching
    # them here is what lets `labels.document_label_ids` answer from the
    # cache instead of issuing one `values_list` per row -- the same
    # prefetch `DocumentsListView` applies for the same reason.
    if category:
        return Document.objects.filter(
            category__name=category,
        ).exclude(scope=Document.Scope.CONVERSATION).prefetch_related("entitlement_labels")
    return Document.objects.filter(
        pk__in=list(ids or []),
    ).exclude(scope=Document.Scope.CONVERSATION).prefetch_related("entitlement_labels")
```

(Read the real current text at `:116-120` before editing — the `.exclude(...)` clause continues onto
`:119-120` and must be preserved exactly.)

- [ ] **Step 4: Make `document_label_ids` read the prefetch**

`tools/rag/labels.py:48-52` currently:

```python
def document_label_ids(document) -> frozenset[int]:
    """The entitlement ids labelling `document` -- FROM THE TABLE, never
    from the cache. The cache is a copy; this is the fact."""
    return frozenset(
        document.entitlement_labels.values_list("entitlement_id", flat=True))
```

becomes:

```python
def document_label_ids(document) -> frozenset[int]:
    """The entitlement ids labelling `document` -- FROM THE TABLE, never
    from the chunk-metadata cache. The cache is a copy; these rows are the
    fact, and that distinction is what this function is for.

    `.all()`, not `.values_list()` (C-05): both read the same table, but
    `.all()` consults a `prefetch_related("entitlement_labels")` cache when
    the caller set one up and falls back to its own query when nobody did,
    while `.values_list()` always issues a fresh query and silently makes a
    caller's prefetch useless. The bulk-label loop
    (`views.document_labels_bulk`) prefetches; single-row callers do not,
    and pay exactly what they paid before.
    """
    return frozenset(label.entitlement_id for label in document.entitlement_labels.all())
```

- [ ] **Step 5: Hoist `is_admin_`/`owned` in the bulk loop**

`tools/rag/views.py`, immediately before the `for document in targets:` loop at `:699`:

```python
    # C-05: hoisted out of the loop, exactly as `DocumentsListView` does
    # (`views.py` above, `is_admin_flag`/`owned_ids`). `may_label_document`'s
    # own docstring asks a caller amortizing per-row cost to pass these;
    # leaving them at `None` re-answered "is this principal an admin" and
    # "which entitlements do they own" once per target document.
    is_admin_flag = is_admin(principal)
    owned_ids = owned_entitlement_ids(principal)
    for document in targets:
        if not may_label_document(principal, document,
                                  is_admin_=is_admin_flag, owned=owned_ids):
            skipped += 1
            continue
        current = set(document_label_ids(document))
        updated = (current | wanted) if action == "apply" else (current - wanted)
```

`is_admin` and `owned_entitlement_ids` are already imported in `views.py` for
`DocumentsListView`'s use; confirm rather than re-importing.

- [ ] **Step 6: Re-measure and set the baseline**

Run the parametrized test again, read the actual count, and set `_BULK_LABEL_BASELINE_QUERIES` to
the number both runs now produce.
Expected: both parameters PASS at the same count.

- [ ] **Step 7: Run the rag suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests`
Expected: PASS. `document_label_ids` is called from several places; the `.all()` change must not move
any behaviour — the set it returns is identical.

- [ ] **Step 8: Update the docs**

`tools/rag/README.md` §"Entitlement labels (IA-2)" (heading at `:237`): one sentence saying the bulk
endpoint is flat in the number of targets, and that `document_label_ids` answers from a caller's
prefetch when one exists.

- [ ] **Step 9: Full gate, then commit**

```
git add tools/rag/views.py tools/rag/services.py tools/rag/labels.py tools/rag/README.md \
        tools/rag/tests/test_document_label_page.py
git commit -m "fix(rag): bulk labelling is flat in the number of documents"
```

Body:

```
C-05. `document_labels_bulk` paid three to four queries per target over an
unbounded set: `may_label_document` recomputed `is_admin_`/`owned` per row
-- which its own docstring asks a bulk caller to hoist, and which the list
view three hundred lines above already hoists -- and `document_label_ids`
issued a `values_list` per row that no prefetch could satisfy. Hoisted,
prefetched, and `.all()`-based, with a pin asserting the same query count
at one document and at twenty-five.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 11: `restamp_document_chunks` takes the store shape as an argument (C-09)

**Files:**
- Modify: `tools/rag/labels.py:88-140` (signature + the `live_store_shape()` call at `:138`)
- Modify: `tools/rag/labels.py:275-276` (the loop inside `unlabel_all_for_entitlement`, def at `:252`)
- Modify: `tools/rag/management/commands/relabel_chunks.py:39-43`
- Test: `tools/rag/tests/test_labels.py`, `tools/rag/tests/test_command_relabel_chunks.py`

**Column:** all `tools/rag`.

**The finding.** `restamp_document_chunks` calls `rag_index.live_store_shape()` at `labels.py:138`
on every invocation. That is one `pg_attribute` lookup per call (`index.py:61-100`), and the function
is called in two bulk loops:

```python
    for doc_id in doc_ids:                                  # labels.py:275-276, in unlabel_all_for_entitlement
        restamp_document_chunks(doc_id, raising=True)
```
```python
            for pk in rows.values_list("pk", flat=True):    # relabel_chunks.py:41-43
                restamp_document_chunks(pk, raising=True)
                count += 1
```

so an entitlement-delete cascade or a whole-library relabel pays it once per document.

**Why a parameter and not a module-level memo.** `index.py:162-173` already records that a
module-level memo was considered and **rejected** for `get_vector_store()`, because the web process
must be able to see a table shape a worker process changed. That reasoning is about *cross-process*
staleness and does not reach a single loop inside one call — but rather than argue the distinction in
a comment, pass the shape in. A caller that has a loop resolves it once; a caller that does not gets
exactly today's behaviour. No cache, no invalidation, nothing to go stale.

- [ ] **Step 1: Write the failing query-count test**

In `tools/rag/tests/test_labels.py`:

```python
_RESTAMP_SHAPE_QUERIES = 1  # the one pg_attribute lookup live_store_shape costs


@pytest.mark.parametrize("documents", [1, 25])
def test_a_relabel_cascade_resolves_the_store_shape_once(documents, django_assert_num_queries):
    """C-09. `restamp_document_chunks` re-derived `live_store_shape()` on
    every call -- a `pg_attribute` lookup per document inside two bulk
    loops (an entitlement-delete cascade, and the whole-library relabel
    command). The shape of one database cannot change between two
    iterations of one loop, so the loop resolves it once and hands it in."""
    entitlement = make_entitlement()
    for _ in range(documents):
        DocumentEntitlement.objects.create(
            document=make_document(), entitlement=entitlement)
    with patch("tools.rag.index.live_store_shape", wraps=rag_index.live_store_shape) as shape:
        labels.unlabel_all_for_entitlement(entitlement.id, commit=True)
    assert shape.call_count == 1
```

The cascade entry point is **`unlabel_all_for_entitlement(entitlement_id: int, *, commit: bool)`**
(`tools/rag/labels.py:252`), whose loop is at `:275-276`. It is registered from
`tools/rag/apps.py::ready()` as a dotted-path string and is what `identity` calls when an
entitlement is deleted — so the test has to build the labels the cascade will remove, not just the
documents. `commit=False` returns a count without touching anything and never reaches the loop.

- [ ] **Step 2: Run it and watch it fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_labels.py -k resolves_the_store_shape_once`
Expected: FAIL at N=25 with `call_count == 25`.

- [ ] **Step 3: Make the shape a keyword argument with today's behaviour as its default**

`tools/rag/labels.py`, `restamp_document_chunks`'s signature and `:138`:

```python
def restamp_document_chunks(doc_id, *, raising: bool = False, shape=None):
    """...  (keep the existing docstring, and add:)

    `shape` (C-09): the `index.live_store_shape()` answer, resolved by the
    caller. `None` -- the default, and what every single-document caller
    passes -- means "resolve it yourself", which is exactly what this
    function always did. A caller with a LOOP (the entitlement-delete
    cascade below, `manage.py relabel_chunks`) resolves it once and hands
    it in: one database's column layout cannot change between two
    iterations of one loop, and paying a `pg_attribute` lookup per
    document to re-ask is the only thing that changes here.

    NOT A MODULE-LEVEL MEMO, deliberately. `index.get_vector_store`'s own
    comment records why a memo was rejected there: the web process must be
    able to see a table a worker process dropped. A parameter has no such
    hazard -- there is nothing to go stale, because there is nothing kept.
    """
    ...
    shape = rag_index.live_store_shape() if shape is None else shape
```

- [ ] **Step 4: Resolve once in each of the two loops**

`tools/rag/labels.py:275-276` (inside `unlabel_all_for_entitlement`):

```python
    shape = rag_index.live_store_shape()   # C-09: once for the whole cascade
    for doc_id in doc_ids:
        restamp_document_chunks(doc_id, raising=True, shape=shape)
```

`tools/rag/management/commands/relabel_chunks.py:39-43`:

```python
            shape = rag_index.live_store_shape()   # C-09: once for the whole run
            for pk in rows.values_list("pk", flat=True):
                restamp_document_chunks(pk, raising=True, shape=shape)
                count += 1
```

Add the `rag_index` import to the command module if it is not already there.

- [ ] **Step 5: Run the two suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_labels.py tools/rag/tests/test_command_relabel_chunks.py`
Expected: PASS, both parameters.

- [ ] **Step 6: Full gate, then commit**

```
git add tools/rag/labels.py tools/rag/management/commands/relabel_chunks.py \
        tools/rag/tests/test_labels.py tools/rag/tests/test_command_relabel_chunks.py
git commit -m "fix(rag): a relabel cascade resolves the store shape once, not once per document"
```

Body:

```
C-09. `restamp_document_chunks` re-derived `live_store_shape()` on every
call, so an entitlement-delete cascade or a whole-library relabel paid a
`pg_attribute` lookup per document. The shape is now a keyword argument
defaulting to today's behaviour; the two loops resolve it once. A
parameter rather than a module-level memo, because `get_vector_store`'s
own comment already records why a memo is wrong for this value.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 12: Every `PGVectorStore` this column opens gets disposed (C-06)

**Files:**
- Modify: `tools/rag/index.py` (new `disposing_vector_store` context manager beside
  `get_vector_store` at `:120-200`)
- Modify: `tools/rag/retrieval.py:732`, `:743`
- Test: `tools/rag/tests/test_index.py`, `tools/rag/tests/test_retrieval.py`

**Column:** all `tools/rag`.

**The finding.** `get_vector_store()` builds a fresh `PGVectorStore` — and with it a fresh
SQLAlchemy engine and connection pool — on every call, and nothing ever disposes it.
`retrieval.py:732` and `:743` call it in both branches of `retrieve_nodes`, which is reached from
`views.py:2102` (`/rag/search`, per request) and `tools/rag/tools.py:222` (per `rag.ask`/`rag.search`
tool call). The package's own `close()` is `async` and therefore unreachable from this synchronous
code path:

```python
    async def close(self) -> None:            # llama_index/vector_stores/postgres/base.py:399-406
        if not self._is_initialized:
            return
        if self._engine:
            self._engine.dispose()
        if self._async_engine:
            await self._async_engine.dispose()
```

**Correction to the audit:** `git grep dispose -- tools/rag` is **not** empty. There is exactly one
hit — `tools/rag/tests/test_index_hybrid_integration.py:130`, `store._engine.dispose()`, in a
`finally:` block whose comment (`:122-130`) describes precisely this bug ("left open, it lingers past
this test and blocks pytest-django's end-of-session `DROP DATABASE`"). That is not a contradiction;
it is the ready-made template. The synchronous half of `close()` — `self._engine.dispose()`, no
`await` — is the fix, and this repo already uses it.

**The design constraint that stays.** `index.py:162-173` records that the per-call *construction* is
deliberate: the web process must see a table a worker process dropped, so the store may not be
memoized. This task changes **only the disposal**. Do not add a cache.

- [ ] **Step 1: Write the failing test**

In `tools/rag/tests/test_index.py`:

```python
def test_a_borrowed_vector_store_is_disposed_when_the_caller_is_done():
    """C-06. `PGVectorStore.from_params` opens its own SQLAlchemy engine
    and pool. The package's `close()` is `async` and unreachable from this
    synchronous path, so nothing ever disposed one -- per `/rag/search`
    request and per `rag.ask`/`rag.search` tool call.
    `test_index_hybrid_integration.py:130` already reaches
    `store._engine.dispose()` by hand, in a `finally:`, for exactly this
    reason; this makes that the production idiom instead of a test's
    workaround."""
    with patch.object(rag_index, "get_vector_store") as build:
        store = build.return_value
        with rag_index.disposing_vector_store() as borrowed:
            assert borrowed is store
    store._engine.dispose.assert_called_once_with()


def test_the_store_is_disposed_even_when_the_caller_raises():
    """The half that matters under load: a retrieval that blows up must
    not leak the pool it opened."""
    with patch.object(rag_index, "get_vector_store") as build:
        store = build.return_value
        with pytest.raises(RuntimeError):
            with rag_index.disposing_vector_store():
                raise RuntimeError("boom")
    store._engine.dispose.assert_called_once_with()
```

- [ ] **Step 2: Run and watch both fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_index.py -k disposing`
Expected: FAIL — `module 'tools.rag.index' has no attribute 'disposing_vector_store'`.

- [ ] **Step 3: Add the context manager**

In `tools/rag/index.py`, immediately after `get_vector_store` (which ends at `:200`):

```python
@contextmanager
def disposing_vector_store(vector_store=None):
    """Yield a `PGVectorStore` and dispose its SQLAlchemy engine when the
    caller is done with it -- including when the caller raises (C-06).

    `PGVectorStore.from_params` opens its own engine and connection pool.
    The package's own `close()` is a coroutine (`llama_index/
    vector_stores/postgres/base.py`), so this synchronous column could
    never call it, and nothing disposed a store at all: one leaked pool
    per `/rag/search` request and per `rag.ask`/`rag.search` tool call.
    The synchronous half of what `close()` does is `self._engine.
    dispose()`, which is exactly what `tools/rag/tests/
    test_index_hybrid_integration.py` already reaches for by hand, in a
    `finally:`, to stop a leaked pool blocking `DROP DATABASE` at the end
    of a test session. This makes that the production idiom.

    `vector_store` lets a caller that was handed a store it does not own
    pass it through: this manager builds one only when given `None`, and
    disposes only what it built. Borrowing and disposing someone else's
    pool would be worse than the leak.

    NOT A CACHE. The per-call construction is deliberate and stays --
    `get_vector_store`'s own comment records why (the web process must be
    able to see a table a worker process dropped). Only the disposal was
    missing.
    """
    owned = vector_store is None
    store = get_vector_store() if owned else vector_store
    try:
        yield store
    finally:
        if owned:
            engine = getattr(store, "_engine", None)
            if engine is not None:
                engine.dispose()
```

Add `from contextlib import contextmanager` to the module's stdlib imports if it is not there.

- [ ] **Step 4: Use it at the two retrieval call sites**

`tools/rag/retrieval.py:732` and `:743` each currently read `vector_store = get_vector_store()`
inside one of `retrieve_nodes`'s two branches. Read the whole of `retrieve_nodes` first, then wrap
each branch's remaining work in the manager, e.g.:

```python
        with index_module.disposing_vector_store() as vector_store:
            ...   # the branch's existing body, unchanged
            return nodes, hybrid, index
```

Both branches must return *from inside* the `with`, so the pool is disposed after the query rather
than before it.

**Do not** touch the two internal `vector_store if vector_store is not None else get_vector_store()`
fallbacks — inside `delete_chunks_for_document` (`index.py:274` on `main`) and `get_index`
(`index.py:308` on `main`). The store they build is handed straight back to a caller that owns its
lifetime, and disposing it there would close a pool the caller is still holding. Say so in the
commit body.

**Two, not three, and re-derive the numbers.** An earlier draft of this task also named
`index.py:251` as a third fallback. It is not: `:251` is the body of `get_storage_context`, which
**Task 6 deletes**. Both remaining fallbacks shift up by about three lines once Task 6 lands — find
them with `git grep -n "else get_vector_store()" -- tools/rag/index.py` rather than by line number
(Global Constraint 16).

- [ ] **Step 5: Run the retrieval suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_index.py tools/rag/tests/test_retrieval.py tools/rag/tests/test_retrieval_visibility.py tools/rag/tests/test_tools.py`
Expected: PASS. Any test that mocks `get_vector_store` with a plain `MagicMock` keeps working —
`getattr(store, "_engine", None)` on a `MagicMock` returns a child mock whose `.dispose()` is a no-op.
A test using a `SimpleNamespace` stub without `_engine` is covered by the `None` guard.

- [ ] **Step 6: Update the docs**

`tools/rag/README.md` §"Design in brief": one sentence in the retrieval paragraph — the store is
built per call by design and disposed when the call ends, and the async `close()` is why the
disposal is spelled `_engine.dispose()`.

- [ ] **Step 7: Full gate, then commit**

```
git add tools/rag/index.py tools/rag/retrieval.py tools/rag/README.md \
        tools/rag/tests/test_index.py tools/rag/tests/test_retrieval.py
git commit -m "fix(rag): the retrieval path disposes the vector store's engine"
```

Body:

```
C-06. `PGVectorStore.from_params` opens its own SQLAlchemy engine and
pool; the package's `close()` is a coroutine this synchronous column can
never call, so nothing disposed one -- a leaked pool per `/rag/search`
request and per `rag.ask`/`rag.search` tool call. A `disposing_vector_store`
context manager makes the `_engine.dispose()` idiom the integration test
already reaches for by hand into the production one, and disposes only
what it built. The per-call CONSTRUCTION stays deliberate; the three
internal fallbacks in `index.py` are untouched, because the store they
build belongs to their caller.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 13: `permits()` gains the conversation-scope axis (C-03)

**Files:**
- Modify: `tools/rag/access.py:99-115` (the `DocumentVisibility` field block), `:126-160`
  (`permits`), and **`:163-195` (`document_visibility`, the factory — both of its branches)**
- Test: `tools/rag/tests/test_access_documents.py`

**Column:** `tools/rag`.

**The finding.** `DocumentVisibility.permits()` (`access.py:126-160`) answers the same question as
`readable_documents()` (`:197-287`) one row at a time — and is missing one of its axes.
`readable_documents` builds `not_chat_scoped = ~Q(scope=Document.Scope.CONVERSATION)` at `:250`,
applies it at `:274` and `:283`, and re-admits chat-scoped rows only for the uploader or a principal
with `sees_all_content` when `workstream_id is None` (`:275-276`, `:284-286`). `permits()` has **no
scope branch at all**: for a chat-scoped document (never labelled, `workstream_id` always `None`) it
falls through containment, finds no labels, and returns `self.unlabelled_allowed` — never asking
whose document it is.

**Latent, not live.** The one caller — `views.py:357`, inside `DocumentsListView` — is fed by
`listable_documents` (`access.py:654-674`), which already excludes every conversation-scoped document
belonging to someone else *before* any row reaches `permits()`. So today the wrong answer is
unreachable. It would be reachable the moment anyone calls `permits()` on an unfiltered row, which
is what a one-row visibility predicate exists to be called on.

- [ ] **Step 1: Write the failing test**

In `tools/rag/tests/test_access_documents.py`:

```python
class TestPermitsAnswersTheSameQuestionAsReadableDocuments:
    """C-03. `permits()` is `readable_documents()`'s one-row twin, and it
    was missing the conversation-scope axis: a chat-scoped document
    belonging to somebody else came back permitted, because it carries no
    labels and `unlabelled_allowed` was the last word. Masked today by
    `listable_documents`'s pre-filter, which is exactly the kind of luck a
    predicate should not depend on."""

    def test_a_chat_scoped_document_belonging_to_someone_else_is_not_permitted(self):
        mine, theirs = make_user(), make_user()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(theirs)))
        with posture("enterprise"):
            visibility = document_visibility(user_principal(mine))
            assert visibility.permits(document) is False

    def test_my_own_chat_scoped_document_is_permitted(self):
        """The other direction -- the axis must admit the uploader, or it
        is a refusal rather than a rule."""
        mine = make_user()
        document = make_document(scope=Document.Scope.CONVERSATION,
                                 **owner_fields(user_principal(mine)))
        with posture("enterprise"):
            assert document_visibility(user_principal(mine)).permits(document) is True

    @pytest.mark.parametrize("scope", [Document.Scope.LIBRARY])
    def test_an_ordinary_library_document_is_unaffected(self, scope):
        """And the axis must not change the answer for the rows that were
        already right."""
        mine, theirs = make_user(), make_user()
        document = make_document(scope=scope, **owner_fields(user_principal(theirs)))
        with posture("enterprise"):
            visibility = document_visibility(user_principal(mine))
            assert visibility.permits(document) == visibility.unlabelled_allowed
```

`document_visibility(principal, *, settings_row=None, stream=None, conversation_id=None)` is the
real factory — `access.py:163-195`, **not** `:100-125`, which is the dataclass's field block. Read
`models.py` for the real `Scope` member names before writing this.

- [ ] **Step 2: Run and watch the first fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_access_documents.py -k PermitsAnswersTheSame`
Expected: the first test FAILS (returns `True`); the second and third pass.

- [ ] **Step 3: Add the axis**

**First, the two fields the dataclass does not have.** `DocumentVisibility` (`access.py:100-115`)
carries exactly `unrestricted`, `entitlement_ids`, `unlabelled_allowed`, `stream`,
`conversation_id`. There is no `owner_kind`/`owner_key`, and there is no `sees_all_content` field —
`unrestricted` **is** that flag (the class docstring at `:82` says so: *"`unrestricted` is
`sees_all_content`"*). So add two optional fields beside `conversation_id`, defaulting to `None` for
the same reason that field does (every existing call site keeps working and keeps meaning what it
meant):

```python
    # C-03: the principal's own identity, for the conversation-scope
    # axis in `permits()`. `None`/`None` is every caller before this
    # round, and means "this visibility cannot answer an ownership
    # question" -- which `permits()` treats as "not the uploader",
    # the safe direction.
    owner_kind: str | None = None
    owner_key: str | None = None
```

and thread them through **both** branches of `document_visibility` (`access.py:187` and `:189-194`)
from the `principal` it already holds.

Then, in `permits()`, between the containment check (`:152-154`) and the `unrestricted`
short-circuit (`:155`):

```python
        # C-03: THE CONVERSATION-SCOPE AXIS, the one `readable_documents`
        # has and this predicate did not. A chat-scoped document is never
        # labelled and never contained, so without this branch it fell
        # through to `unlabelled_allowed` and came back permitted for
        # anybody -- the exact answer `readable_documents` refuses at
        # `not_chat_scoped`. Re-admitted for the uploader and for a
        # principal who sees all content, and only outside a workstream
        # scope, matching that function's `:275-276`/`:284-286` clauses
        # clause for clause.
        if document.scope == Document.Scope.CONVERSATION:
            if workstream_id is not None:
                return False
            return self.unrestricted or (
                self.owner_kind is not None
                and document.owner_kind == self.owner_kind
                and document.owner_key == self.owner_key
            )
```

- [ ] **Step 4: Run the three tests** — Expected: all PASS.

- [ ] **Step 5: Prove the live caller is unmoved**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_access_documents.py tools/rag/tests/test_views.py tools/rag/tests/test_chat_scoped_documents.py tools/rag/tests/test_retrieval_visibility.py`
Expected: PASS with no assertion changes. `listable_documents` already excluded these rows, so the
library page must render identically — if a view test moves, the pre-filter was not doing what
`access.py:654-674` claims and that is a bigger finding than this one.

- [ ] **Step 6: Document the pairing**

Add a sentence to `permits()`'s docstring naming `readable_documents` as the queryset half it must
agree with, and one to `readable_documents`'s naming `permits` as the row half — so the next person
to add an axis to either is told, in both places, that there are two.

- [ ] **Step 7: Full gate, then commit**

```
git add tools/rag/access.py tools/rag/tests/test_access_documents.py
git commit -m "fix(rag): permits() answers the conversation-scope question readable_documents() answers"
```

Body:

```
C-03. `DocumentVisibility.permits()` is `readable_documents()`'s one-row
twin and was missing its conversation-scope axis, so a chat-scoped
document belonging to someone else came back permitted -- no labels, so
`unlabelled_allowed` had the last word. Latent rather than live:
`listable_documents` pre-filters the one caller. Latent is not fixed, and
a one-row predicate exists to be called on a row nobody filtered. Both
docstrings now name the other.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 14: The registry console caches engine health for 30s (C-07, half A)

**Files:**
- Create: `models/registry/probe_cache.py` (a new module in the `models` column)
- Modify: `models/registry/views.py:296-310` (`_check_health`) and `:1113` (the `discover(...)` call)
- Modify: `models/registry/README.md` §"Finding your model server" (heading at `:181`)
- Test: `models/registry/tests/test_probe_cache.py` (new),
  `models/registry/tests/test_views.py` (`TestConsoleViewQueryCount`, `:3541`)

**Column:** `models/registry` — intra-column, and the vision half (Task 15) gets its **own**
independent cache in `tools/vision`. **No shared helper**, deliberately: the two columns may not
import each other's app modules under rule 2, the TTLs answer different questions, and one shared
module would be a cross-column import for fifteen lines.

**The finding.** `_build_context(endpoint)` (`views.py:1071-…`) calls `_check_health(endpoint)` at
`:1099` and `discover(...)` at `:1113`. `_build_context` is reached from `ConsoleView.get_context_data`
(`:1260`) and the scan-POST re-render (`:2387`) — and `_redirect_console()` (`:281-293`) is returned
from **40 mutating POST handlers**, every one of which 302s straight back into a fresh
`_build_context`. So a console GET re-probes every registered engine over HTTP, and so does every
POST.

`_check_health`, verbatim (`views.py:296-310`):

```python
def _check_health(endpoint: str) -> bool:
    """True if ANY registered engine reports `endpoint` reachable..."""
    for engine in ENGINES.values():
        try:
            if engine.is_healthy(endpoint):
                return True
        except Exception:  # noqa: BLE001 -- unreachable/broken engine...
            continue
    return False
```

**The pattern to copy, verbatim** (`models/registry/availability.py:75-125`):

```python
CACHE_TTL_SECONDS = 30.0

# `(value, expires_at)`, or None for "nothing cached".
_CACHE: tuple[frozenset[str], float] | None = None
...
_GENERATION = 0

def bound_role_keys() -> frozenset[str]:
    ...
    global _CACHE
    from django.db import connection
    if connection.in_atomic_block:
        return _read_bound_role_keys()
    cached = _CACHE
    if cached is not None and cached[1] > monotonic():
        return cached[0]
    # Snapshot the generation BEFORE the read, compare after...
    generation = _GENERATION
    value = _read_bound_role_keys()
    if generation == _GENERATION:
        _CACHE = (value, monotonic() + CACHE_TTL_SECONDS)
    return value
```

Two differences to carry across deliberately: this cache is **keyed** (by endpoint, and by the
discovery call's argument tuple), and the `connection.in_atomic_block` guard does **not** apply — an
HTTP probe is not a database read and has no transaction-visibility hazard. Keep the
generation-snapshot idiom: it is what stops a probe that started before an invalidation from writing
a stale answer after it.

- [ ] **Step 1: Write the failing tests**

Create `models/registry/tests/test_probe_cache.py`:

```python
"""C-07 half A. The console re-probed every registered engine over HTTP on
every GET and on every POST -> redirect round trip -- 40 mutating handlers
return `_redirect_console()`, and each 302 lands back in a fresh
`_build_context`. `models/registry/availability.py:92-125` is this repo's
own 30-second process-local TTL pattern; this is that pattern applied to
the probe instead of to a database read."""


def test_a_second_probe_inside_the_ttl_does_not_reach_the_engine():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    assert probe_cache.health(ENDPOINT, probe) is True
    assert probe_cache.health(ENDPOINT, probe) is True
    assert probe.call_count == 1


def test_a_probe_after_the_ttl_expires_reaches_the_engine_again():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    with patch.object(probe_cache, "monotonic", side_effect=[0.0, 0.0, 100.0, 100.0]):
        probe_cache.health(ENDPOINT, probe)
        probe_cache.health(ENDPOINT, probe)
    assert probe.call_count == 2


def test_two_endpoints_do_not_share_an_answer():
    probe_cache.invalidate()
    probe = MagicMock(side_effect=[True, False])
    assert probe_cache.health("http://a:1", probe) is True
    assert probe_cache.health("http://b:2", probe) is False


def test_invalidate_clears_every_entry():
    probe_cache.invalidate()
    probe = MagicMock(return_value=True)
    probe_cache.health(ENDPOINT, probe)
    probe_cache.invalidate()
    probe_cache.health(ENDPOINT, probe)
    assert probe.call_count == 2


def test_a_probe_that_started_before_an_invalidation_does_not_write_its_answer():
    """The generation-snapshot half, copied from `availability.py:121-124`.
    An operator who edits a connection while a probe is in flight must not
    have the pre-edit answer cached over their edit."""
    probe_cache.invalidate()

    def _probe_then_invalidate(_endpoint):
        probe_cache.invalidate()
        return True

    probe_cache.health(ENDPOINT, _probe_then_invalidate)
    second = MagicMock(return_value=False)
    assert probe_cache.health(ENDPOINT, second) is False
    assert second.call_count == 1
```

And in `models/registry/tests/test_views.py`, extend `TestConsoleViewQueryCount` (`:3541`) — which
today asserts DB query counts with `django_assert_num_queries(9)` and mocks `ENGINES` wholesale but
never asserts a probe count:

```python
    def test_a_second_console_get_inside_the_ttl_does_not_re_probe(self, client):
        """C-07 half A, at the surface. Two GETs in one process, inside
        the 30s window, must cost ONE health probe -- not two."""
        probe_cache.invalidate()
        engine = _mock_engine(healthy=True)
        with patch.dict(views_module.ENGINES, {"stub": engine}, clear=True):
            client.get(reverse("inference-console"))
            client.get(reverse("inference-console"))
        assert engine.is_healthy.call_count == 1

    def test_a_mutating_post_invalidates_the_probe_cache(self, client):
        """The other direction. An operator who registers a connection and
        lands back on the console must see a fresh probe, not a 30s-old
        one -- which is what makes the TTL safe to have at all."""
        probe_cache.invalidate()
        engine = _mock_engine(healthy=True)
        with patch.dict(views_module.ENGINES, {"stub": engine}, clear=True):
            client.get(reverse("inference-console"))
            client.post(reverse("inference-connection-add"), {...})  # any handler returning _redirect_console
            assert engine.is_healthy.call_count >= 2
```

- [ ] **Step 2: Run and watch them fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests/test_probe_cache.py models/registry/tests/test_views.py -k "probe or ProbeCache"`
Expected: FAIL — the module does not exist; the view tests report `call_count == 2`.

- [ ] **Step 3: Write `models/registry/probe_cache.py`**

```python
"""A 30-second, process-local TTL cache for engine reachability probes.

WHY THIS EXISTS (C-07). `_build_context` probes every registered engine
over HTTP -- `_check_health` per endpoint, plus `discovery.discover` --
and it runs on every console GET *and* on every POST, because
`_redirect_console()` is what forty mutating handlers return and
each 302 lands back in `ConsoleView.get_context_data`. An operator who
edits three roles pays six round trips to a model server for one piece of
work.

THE PATTERN IS NOT NEW HERE: `models/registry/availability.py:92-125`
already runs a 30-second process-local TTL over the role-binding read, with
a generation counter so a read that started before an invalidation cannot
write its stale answer afterwards. This is that mechanism, keyed, over an
HTTP probe instead of a database read.

WHAT IS DELIBERATELY DIFFERENT from `availability.py`:

* KEYED. `availability` caches one value; this caches one per probe key
  (an endpoint, or a discovery argument tuple), because two model servers
  are two independent facts.
* NO `connection.in_atomic_block` GUARD. That guard exists in
  `availability` because a cached database read taken inside a transaction
  can outlive a rollback. An HTTP probe is not a database read; there is
  no transaction whose rollback could make this answer wrong.
* PROCESS-LOCAL, NOT SHARED. Same as `availability`: a worker and a web
  process hold their own, which is correct -- reachability is a property
  of the asking process's own network path.

NOT SHARED WITH `tools/vision`'s equivalent (C-07's other half), and that
is the import law, not an oversight: `models.registry` is column-private
to `tools/*` under rule 2, and vision's cache answers a different question
(one bound generation model's preflight) with its own invalidation events.
Two fifteen-line caches beat one cross-column import.
"""
from __future__ import annotations

from time import monotonic
from typing import Callable

CACHE_TTL_SECONDS = 30.0

# {key: (value, expires_at)}. Process-local; never shared, never persisted.
_CACHE: dict[str, tuple[bool, float]] = {}

# Bumped by `invalidate()`. A probe snapshots this before it runs and
# refuses to write its answer if it changed while the probe was in flight
# -- `availability.py:121-124`'s own reasoning, for the same reason.
_GENERATION = 0


def invalidate() -> None:
    """Drop every cached answer. Called from the registry's own
    post_save/post_delete signals, so an operator who edits a connection
    sees a fresh probe on the redirect rather than a 30-second-old one."""
    global _GENERATION
    _GENERATION += 1
    _CACHE.clear()


def health(key: str, probe: Callable[[str], bool]) -> bool:
    """`probe(key)`'s answer, at most `CACHE_TTL_SECONDS` old.

    `probe` is passed in rather than imported so this module knows nothing
    about `ENGINES`, which keeps it testable without a Django app registry
    and keeps the probe's own exception handling where it already lives.
    """
    global _GENERATION
    cached = _CACHE.get(key)
    if cached is not None and cached[1] > monotonic():
        return cached[0]
    generation = _GENERATION
    value = probe(key)
    if generation == _GENERATION:
        _CACHE[key] = (value, monotonic() + CACHE_TTL_SECONDS)
    return value
```

- [ ] **Step 4: Route `_check_health` through it**

`models/registry/views.py:296-310` becomes:

```python
def _check_health(endpoint: str) -> bool:
    """True if ANY registered engine reports `endpoint` reachable.

    Cached for `probe_cache.CACHE_TTL_SECONDS` (C-07): this runs on every
    console GET and on every POST -> `_redirect_console()` -> GET round
    trip, so an operator doing three edits used to pay six HTTP round
    trips to the same model server. The cache is dropped whenever a
    registry row changes, so an edit is never read through a stale answer.
    """
    return probe_cache.health(endpoint, _probe_health)


def _probe_health(endpoint: str) -> bool:
    """The uncached probe -- every registered engine, first `True` wins,
    an engine that raises counts as unreachable rather than as a 500."""
    for engine in ENGINES.values():
        try:
            if engine.is_healthy(endpoint):
                return True
        except Exception:  # noqa: BLE001 -- unreachable/broken engine, not a page failure
            continue
    return False
```

- [ ] **Step 5: Wire the invalidation — and note that mirroring is not enough**

`models/registry/apps.py::ready()` connects `availability.invalidate_after_commit` (that name, not
`invalidate`) on **three** receivers, not four:

```python
post_save.connect(invalidate_after_commit, sender="inference.RoleBinding", ...)     # apps.py:97
post_delete.connect(invalidate_after_commit, sender="inference.RoleBinding", ...)   # apps.py:101
post_delete.connect(invalidate_after_commit, sender="inference.ModelConnection",
                    dispatch_uid="inference.availability.modelconnection.deleted")  # apps.py:105
```

**There is no `post_save` for `ModelConnection`** — availability does not need one, because creating
a connection binds no role. A probe cache *does* need one: Step 1's
`test_a_mutating_post_invalidates_the_probe_cache` POSTs to `inference-connection-add`, which
**creates** a `ModelConnection`, and mirroring `apps.py` exactly would leave that uninvalidated and
the test red. Connect all four:

```python
from models.registry.probe_cache import invalidate_after_commit as invalidate_probe_cache

for signal, sender, uid in (
    (post_save,   "inference.RoleBinding",    "inference.probe_cache.rolebinding.saved"),
    (post_delete, "inference.RoleBinding",    "inference.probe_cache.rolebinding.deleted"),
    (post_save,   "inference.ModelConnection", "inference.probe_cache.modelconnection.saved"),
    (post_delete, "inference.ModelConnection", "inference.probe_cache.modelconnection.deleted"),
):
    signal.connect(invalidate_probe_cache, sender=sender, dispatch_uid=uid)
```

Name the module's own entry point `invalidate_after_commit` to match `availability.py`'s, and have
it defer to `transaction.on_commit` the same way — an operator whose edit rolls back must not have
had the cache dropped for a change that never happened. `invalidate()` stays as the direct,
test-facing form.

- [ ] **Step 6: Cache the discovery call too**

`views.py:1113` is `discovered = discover(_engine_endpoints(connections, endpoint), connections)`.
Wrap it the same way, keyed on a stable string built from the endpoints argument:

```python
    # C-07: the same 30s window as `_check_health`, keyed on the endpoint
    # set this call is about. `discover` walks every engine's discovery
    # endpoint over HTTP and is the more expensive of the two probes.
    endpoints = _engine_endpoints(connections, endpoint)
    discovered = probe_cache.cached(
        "discover:" + "|".join(sorted(endpoints)),
        lambda _key: discover(endpoints, connections),
    )
```

This needs a second, value-typed entry point beside `health`. Add it to `probe_cache.py` with the
identical body and a `Any` value type — `health` then becomes a thin `bool`-typed wrapper over it, so
there is one mechanism and not two:

```python
_VALUE_CACHE: dict[str, tuple[object, float]] = {}


def cached(key: str, probe: Callable[[str], object]) -> object:
    """`probe(key)`'s answer, at most `CACHE_TTL_SECONDS` old. `health` is
    this with a `bool` return type; both share `_GENERATION` so one
    `invalidate()` clears everything."""
```

Collapse `_CACHE`/`_VALUE_CACHE` into one dict when you write it — the split above is exposition,
not a design.

- [ ] **Step 7: Run the tests** — all of Step 1's, plus `models/registry/tests`.

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests models/queue/tests foundation/setup/tests`
Expected: PASS. `/setup/`'s reachability page also probes engines; check whether it reaches
`_check_health` and, if so, whether a 30-second-old answer is acceptable there. If it is not (that
page's whole purpose is "is it answering *right now*"), route it around the cache explicitly by
calling `_probe_health` and say so in a comment.

- [ ] **Step 8: Update the README**

`models/registry/README.md` §"Finding your model server" (`:181`): one paragraph — health and
discovery answers are cached process-locally for 30 seconds, dropped whenever a registry row
changes, and `/setup/` is (or is not — record what Step 7 found) exempt.

- [ ] **Step 9: Full gate, then commit**

```
git add models/registry/probe_cache.py models/registry/views.py models/registry/apps.py \
        models/registry/README.md models/registry/tests/test_probe_cache.py models/registry/tests/test_views.py
git commit -m "fix(registry): engine health and discovery are probed at most once per 30s"
```

Body:

```
C-07, half A. `_build_context` probes every registered engine over HTTP on
every console GET and on every POST, because `_redirect_console()` is what
forty mutating handlers return and each 302 lands back in a fresh
context build -- three role edits cost six round trips to one model
server. `probe_cache` is `availability.py`'s own 30-second process-local
TTL, keyed, with the same generation snapshot so a probe in flight during
an edit cannot write its stale answer afterwards, and dropped on every
registry row change.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 15: The vision tool caches its engine preflight for 30s (C-07, half B)

**Files:**
- Create: `tools/vision/probe_cache.py` (a new module in the `tools/vision` column)
- Modify: `tools/vision/services.py:132-153` (`_health_check`)
- Modify: `tools/vision/tests/test_services.py` (add the autouse reset fixture — see Step 1)
- Modify: `tools/vision/README.md` §"The service layer" (heading at `:375`)
- Test: `tools/vision/tests/test_probe_cache.py` (new), `tools/vision/tests/test_tools.py`

**Column:** `tools/vision`. **Its own cache, not the registry's** — `models.registry` is
column-private to `tools/*` under rule 2, so importing Task 14's module here is not an option and is
not a workaround worth wanting. Write the fifteen lines again.

**The finding.** Every chat turn that offers `vision.generate` narrows its spec:
`agents/runtime/loop.py:609-610` resolves and calls `tools.vision.tools.narrowed_generate_spec`,
which at `tools/vision/tools.py:348-353` calls `services.preflight(resolved=None)`:

```python
    from tools.vision import services
    check = services.preflight(resolved=None)
    if not check.ready:
        return spec
    resolved = check.resolved
```

`preflight` (`services.py:156-183`) calls `_health_check` (`:132-153`), whose body is:

```python
    try:
        healthy = get_engine(resolved.engine).is_healthy(resolved.endpoint)
    except Exception:  # noqa: BLE001 -- an engine that blows up is unreachable, not a 500
        logger.exception("Health check failed for %s at %r", resolved.engine, resolved.endpoint)
        healthy = False
```

One HTTP round trip to the generation server per chat turn, on the turn's critical path.

**The test hazard this task must handle.** `tools/vision/tests/test_services.py::TestPreflight`
(`:89-138`) runs several independent tests against the *same literal endpoint*
(`"http://stub:9999"`, via the `_bind()`/`_registered()` helpers at `:61-136`) with *different*
healthy states per test — `healthy=False` at `:98`, `healthy=True` at `:111`, an exception at `:106`
— and there is no cache-reset fixture in that file. A process-local cache keyed on
`(engine, endpoint)` would leak one test's answer into the next. **The autouse reset fixture is part
of this task, not a follow-up.** Tests that patch `services.preflight` wholesale (most of
`test_tools.py`, `agents/runtime/tests/test_loop.py`) bypass the cache entirely and are unaffected.

- [ ] **Step 1: Add the reset fixture first, and prove it changes nothing yet**

At the top of `tools/vision/tests/test_services.py`:

```python
@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """C-07 half B. `_health_check` caches reachability for 30 seconds,
    process-locally, keyed on (engine, endpoint). Every test in this
    module uses the SAME literal endpoint with a DIFFERENT healthy state,
    so without this the second test in a run reads the first one's answer.
    Mirrors `models/registry/tests/test_availability.py:57-65`'s own
    `invalidate()`-around-the-yield fixture."""
    probe_cache.invalidate()
    yield
    probe_cache.invalidate()
```

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_services.py`
Expected: FAIL on the import (the module does not exist yet) — which is the point: the fixture lands
before the thing it protects against.

- [ ] **Step 2: Write the failing cache tests**

Create `tools/vision/tests/test_probe_cache.py` with the same five tests as Task 14's Step 1,
repointed at `tools.vision.probe_cache` and keyed on `(engine, endpoint)` pairs rather than bare
endpoints. Then add the surface test to `tools/vision/tests/test_tools.py`, beside
`TestNarrowedGenerateSpec` (`:895-948`):

```python
def test_two_narrowing_calls_in_one_window_cost_one_health_probe():
    """C-07 half B. `narrowed_generate_spec` runs on every chat turn that
    offers `vision.generate`, and its preflight paid one HTTP round trip
    to the generation server each time -- on the turn's critical path.
    Two turns inside the 30s window must cost one probe."""
    probe_cache.invalidate()
    engine = MagicMock()
    engine.is_healthy.return_value = True
    with patch("tools.vision.services.get_engine", return_value=engine):
        tools.narrowed_generate_spec(spec)
        tools.narrowed_generate_spec(spec)
    assert engine.is_healthy.call_count == 1
```

Note that `test_tools.py:299-322`'s existing `test_preflight_runs_exactly_once` is about
`run_generate`'s own single preflight call, not this one — it does not conflict, and must stay green.

- [ ] **Step 3: Run and watch them fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_probe_cache.py tools/vision/tests/test_tools.py -k "probe or one_health_probe"`
Expected: FAIL — no module; `call_count == 2`.

- [ ] **Step 4: Write `tools/vision/probe_cache.py`**

The same shape as Task 14's module — `CACHE_TTL_SECONDS = 30.0`, a `{key: (value, expires_at)}` dict,
a `_GENERATION` counter, `invalidate()`, and one `cached(key, probe)` entry point. Its docstring must
say the three things this one differs on:

- the key is `f"{engine}|{endpoint}"`, not a bare endpoint, because a vision endpoint's reachability
  is asked about per bound engine;
- it is **not** the registry's module and cannot be, under rule 2 — say so, and say that two
  fifteen-line caches beat one cross-column import, so the next reader does not "fix" it;
- what invalidates it (Step 5).

- [ ] **Step 5: Route `_health_check` through it, and wire invalidation**

`tools/vision/services.py:141-145` becomes a call to `probe_cache.cached(...)` around the existing
try/except, with the try/except staying inside the probe function so an engine that raises is still
recorded as unreachable rather than propagating.

Invalidation: the answer must be dropped when the operator changes what is bound. `models.registry`
signals are not reachable from `tools/vision` — so invalidate on the events this column *can* see:
whenever `preflight` is called with an explicit `resolved` (an operator picked a model for one
generation), and at the start of `run_generate`. If neither is a real invalidation point, say so and
rely on the 30-second TTL alone, in a comment — a 30-second-stale "the generation server is up"
answer on a chat turn is a materially smaller problem than a 30-second-stale one on the console,
because the generation itself will fail honestly a moment later either way.

- [ ] **Step 6: Run the vision suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests agents/runtime/tests/test_loop.py`
Expected: PASS. Watch `test_services.py::TestPreflight` specifically — it is the module the Step 1
fixture exists for.

Then run it under the other flag state, which is where a vision cache is most likely to surprise:
Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision' .venv/bin/pytest -q tools/vision/tests`

- [ ] **Step 7: Update the README**

`tools/vision/README.md` §"The service layer — the seam a chatbot tool will call" (`:375`): one
paragraph on the 30-second preflight cache, what drops it, and why it is a separate cache from the
registry's. **No model or family names** (Global Constraint 9).

- [ ] **Step 8: Full gate, then commit**

```
git add tools/vision/probe_cache.py tools/vision/services.py tools/vision/README.md \
        tools/vision/tests/test_probe_cache.py tools/vision/tests/test_services.py tools/vision/tests/test_tools.py
git commit -m "fix(vision): the per-turn preflight probes the generation server at most once per 30s"
```

Body:

```
C-07, half B. `narrowed_generate_spec` runs on every chat turn that offers
`vision.generate`, and its preflight paid one HTTP round trip to the
generation server each time, on the turn's critical path. A 30-second
process-local TTL, keyed on (engine, endpoint) -- this column's own, not
the registry's, because `models.registry` is column-private to `tools/*`
and two fifteen-line caches beat one cross-column import. The autouse
reset fixture ships with it: every test in `test_services.py` uses the
same literal endpoint with a different healthy state.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 16: Six attachment-registry setters share one validation body (C-26)

**Files:**
- Modify: `agents/contracts/attachments.py:133-174,183-205,214-231,240-253,313-341,350-375`
- Test: `agents/contracts/tests/test_attachments.py`

**Column:** `agents/contracts` — a pure leaf under rule 1. The helper is private to this module and
crosses nothing.

**The finding.** Six registration functions each carry the same four-line validation body, verbatim
apart from the function's own name inside the error string:

```python
    if "." not in dotted_path:
        raise ValueError(
            f"register_attachment_provider needs a dotted path, got {dotted_path!r}"
        )
    global _PROVIDER
    _PROVIDER = dotted_path
```

The six are `register_attachment_provider` (`:133-174`), `_cleanup` (`:183-205`), `_uploader`
(`:214-231`), `_detacher` (`:240-253`), `_text_provider` (`:313-341`),
`_orphan_cleanup` (`:350-375`). What varies is only the function's own name in the message and the
module global assigned. Each function's 20-30 line docstring is unique narrative and **stays where it
is** — the docstrings are the reason six public functions exist at all.

**Scope line, restated so it is not mistaken for a reversal.** This is orthogonal to the standing
owner-call (prior-audits digest, S8: "six registry slots for one attachment-backend capability →
collapse to one slot"). This task factors the duplicated four-line *body* and keeps all six public
functions, their names, their signatures, and their docstrings. It neither advances nor pre-empts the
slot-count decision. The six accessor functions (`attachment_provider()` and its siblings) are **not**
touched: each returns a distinct module global and there is no duplicated logic in them.

- [ ] **Step 1: Write the test that pins every message**

Add to `agents/contracts/tests/test_attachments.py`:

```python
_SETTERS = [
    ("register_attachment_provider", "attachment_provider"),
    ("register_attachment_cleanup", "attachment_cleanup"),
    ("register_attachment_uploader", "attachment_uploader"),
    ("register_attachment_detacher", "attachment_detacher"),
    ("register_attachment_text_provider", "attachment_text_provider"),
    ("register_attachment_orphan_cleanup", "attachment_orphan_cleanup"),
]


@pytest.mark.parametrize(("setter", "accessor"), _SETTERS)
def test_every_setter_refuses_a_path_with_no_dot_and_names_itself(setter, accessor, isolated_attachment_registry):
    """C-26. All six carry the same four-line validation body, and the
    only thing that varies is the function's own name in the message.
    Factoring the body must not quietly make all six say
    `_register` instead -- an operator reading a traceback needs the name
    of the thing they called."""
    with pytest.raises(ValueError) as excinfo:
        getattr(attachments, setter)("notdotted")
    assert setter in str(excinfo.value)
    assert "notdotted" in str(excinfo.value)


@pytest.mark.parametrize(("setter", "accessor"), _SETTERS)
def test_every_setter_still_writes_only_its_own_slot(setter, accessor, isolated_attachment_registry):
    """The six slots stay six. This task factors a body, not a registry."""
    getattr(attachments, setter)("a.b")
    assert getattr(attachments, accessor)() == "a.b"
    others = [a for _, a in _SETTERS if a != accessor]
    assert all(getattr(attachments, a)() is None for a in others)
```

Use the module's own `isolated_attachment_registry` fixture — `test_attachments.py` already has one
(`test_isolated_attachment_registry_actually_isolates_all_six_slots` names it).

- [ ] **Step 2: Run and watch them pass**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/contracts/tests/test_attachments.py`
Expected: PASS. These are characterization tests, not red-green — the refactor's whole contract is
"nothing observable changes", so the tests must be green *before* and after. Their value is that they
go red the moment the factoring loses a per-function message.

- [ ] **Step 3: Add the private helper**

At module level in `agents/contracts/attachments.py`, above the first setter:

```python
def _validated_dotted_path(dotted_path: str, *, setter: str) -> str:
    """`dotted_path` if it looks like one, else `ValueError` naming
    `setter` (C-26).

    The four-line body every `register_attachment_*` function below used
    to carry verbatim. `setter` is the CALLER'S OWN NAME, passed
    explicitly rather than derived from the stack: an operator reading the
    traceback of a bad registration needs the name of the function they
    called, and every one of the six messages said its own name before
    this was factored. Passing it keeps that true and keeps this helper
    free of frame inspection.
    """
    if "." not in dotted_path:
        raise ValueError(f"{setter} needs a dotted path, got {dotted_path!r}")
    return dotted_path
```

- [ ] **Step 4: Rewrite each of the six bodies**

Each function keeps its `def` line and its entire docstring, and its body becomes two lines. For
`register_attachment_provider`:

```python
def register_attachment_provider(dotted_path: str) -> None:
    """<the existing docstring, unchanged>"""
    global _PROVIDER
    _PROVIDER = _validated_dotted_path(dotted_path, setter="register_attachment_provider")
```

Repeat for the other five with their own global and their own name:
`_CLEANUP` / `register_attachment_cleanup`, `_UPLOADER` / `register_attachment_uploader`,
`_DETACHER` / `register_attachment_detacher`, `_TEXT_PROVIDER` / `register_attachment_text_provider`,
`_ORPHAN_CLEANUP` / `register_attachment_orphan_cleanup`.

- [ ] **Step 5: Run the three suites that touch this registry**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/contracts/tests/test_attachments.py agents/tests/test_attachment_seam.py agents/chat/tests/test_turn_attachments.py`
Expected: PASS, no assertion changes.

- [ ] **Step 6: Full gate, then commit**

```
git add agents/contracts/attachments.py agents/contracts/tests/test_attachments.py
git commit -m "refactor(agents): the six attachment setters share one validation body"
```

Body:

```
C-26. Six `register_attachment_*` functions carried the same four-line
validation verbatim, differing only in the name inside the error message.
Factored into `_validated_dotted_path(dotted_path, setter=...)`, with the
caller's own name passed explicitly so every traceback still names the
function the operator actually called. All six public functions, their
signatures and their docstrings are unchanged -- this factors a body, not
the slot count, which remains an open owner-call.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 17: Two engine-registration handlers share one support-boundary check (C-27)

**Files:**
- Modify: `models/registry/views.py:1454-1461` (`connection_add`) and `:1868-1873`
  (`machine_model_add` — `:1863-1867` is the parity comment above it), plus the two name-collision
  messages at `:1561` and `:1968` (one line each)
- Test: `models/registry/tests/test_views.py`

**Copy the two message strings out of the file, do not retype them** (Global Constraint 17): both use
typographic quotation marks (`“ ”`), and the existing tests
(`test_views.py:2033`, `:3308`, `:5611`) assert on substrings that would not catch a silent swap to
ASCII `"`.

**Column:** `models/registry`, intra-column.

**The finding.** Both creation paths check the posted engine against the registered adapters with the
same code and the same operator-facing message. `connection_add` (`:1454-1461`):

```python
    engine_changed = instance is None or engine != instance.engine
    if engine_changed and not any(candidate.name == engine for candidate in ENGINES.values()):
        messages.error(
            request,
            f"Unknown model server “{engine}” — the supported model-server "
            "APIs are listed on the console.",
        )
        return _redirect_console(request)
```

`machine_model_add` (`:1868-1873`), whose parity comment at `:1863-1867` already admits the copy — *"Validation parity
with `connection_add` … Same de-anchored `ENGINES.values()` read and the same operator-facing message
as `connection_add`"*:

```python
    if not any(candidate.name == engine for candidate in ENGINES.values()):
        messages.error(
            request,
            f"Unknown model server “{engine}” — the supported model-server "
            "APIs are listed on the console.",
        )
```

The `engine_changed` guard is `connection_add`'s alone (it also serves edits, where an already-stored
unregistered engine must survive an unrelated edit — `test_unrelated_edit_preserves_an_unregistered_stored_engine`
pins that). It stays at the call site.

**Also in scope, same shape:** the name-collision message is retyped at `:1561` and `:1968`
as `f"A connection named “{name}” already exists."`. Promote it to a module-level format constant
beside the other message constants so the two cannot drift.

- [ ] **Step 1: Write the parity pin**

```python
def test_both_creation_paths_refuse_an_unregistered_engine_with_the_same_words(client):
    """C-27. `machine_model_add`'s own comment says it keeps "validation
    parity with `connection_add`" -- by copying it. Two copies of an
    operator-facing refusal are two things to keep in step by hand. This
    pin is what makes the parity a fact rather than an intention."""
    from_connection = _refusal_body(client, reverse("inference-connection-add"), engine="nope")
    from_machine = _refusal_body(client, reverse("inference-machine-model-add"), engine="nope")
    assert "Unknown model server" in from_connection
    assert from_connection == from_machine
```

Write `_refusal_body` as a small local helper that posts an otherwise-valid payload with the bad
engine, follows the redirect, and returns the flash text. The two existing tests
(`test_unknown_engine_is_rejected_cleanly_on_create` at `:2019` and
`test_unregistered_engine_is_rejected_cleanly` at `:3296`) show what a valid payload for each handler
looks like — read both before writing this.

- [ ] **Step 2: Run it and watch it pass** — characterization, as in Task 16. It must be green before
the refactor and after.

- [ ] **Step 3: Extract the check**

At module level in `models/registry/views.py`, beside the other private helpers:

```python
_UNKNOWN_ENGINE_MESSAGE = (
    "Unknown model server “{engine}” — the supported model-server "
    "APIs are listed on the console."
)
_NAME_TAKEN_MESSAGE = "A connection named “{name}” already exists."


def _refuse_unregistered_engine(request, engine: str) -> bool:
    """True (and a flash queued) when `engine` is not a registered
    adapter (C-27).

    Both creation paths -- `connection_add` and `machine_model_add` --
    have to enforce the same support boundary against the same
    de-anchored `ENGINES.values()` read, with the same words, and
    `machine_model_add`'s own comment used to say so while copying it.
    The CALLER still decides WHEN to ask: `connection_add` asks only when
    the engine actually changed, so an unrelated edit to a row carrying
    an engine that was deregistered since does not become unsaveable.
    """
    if any(candidate.name == engine for candidate in ENGINES.values()):
        return False
    messages.error(request, _UNKNOWN_ENGINE_MESSAGE.format(engine=engine))
    return True
```

- [ ] **Step 4: Use it at both sites**

`connection_add:1454-1461`:

```python
    engine_changed = instance is None or engine != instance.engine
    if engine_changed and _refuse_unregistered_engine(request, engine):
        return _redirect_console(request)
```

`machine_model_add:1868-1873`: read the surrounding lines for what it returns after the flash (it may
fall through rather than redirect) and preserve that exactly:

```python
    if _refuse_unregistered_engine(request, engine):
        return _redirect_console(request)
```

Then repoint `:1561` and `:1968` to `messages.error(request, _NAME_TAKEN_MESSAGE.format(name=name))`.

- [ ] **Step 5: Run the registry suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests`
Expected: PASS, no assertion changes. `test_unrelated_edit_preserves_an_unregistered_stored_engine`
is the one that proves the `engine_changed` guard stayed at the call site.

- [ ] **Step 6: Full gate, then commit**

```
git add models/registry/views.py models/registry/tests/test_views.py
git commit -m "refactor(registry): one support-boundary check for both creation paths"
```

Body:

```
C-27. `machine_model_add` enforced the same registered-adapter boundary as
`connection_add` with the same words, and its own comment said "validation
parity" while copying the code that provided it. One
`_refuse_unregistered_engine` helper now owns the check and the message;
the caller still decides when to ask, so `connection_add`'s
engine-changed guard -- which is what lets an unrelated edit to a row with
a deregistered engine still save -- stays at its call site. The
name-collision message becomes a constant for the same reason.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 18: `agents/labels.py` — one writer, one group reader, one row reader (C-28)

**Files:**
- Modify: `agents/labels.py` (222 lines; the whole file is in scope)
- Test: `agents/tests/test_tool_labels.py`, `agents/tests/test_agent_flow_labels.py`

**Column:** `agents`, intra-column.

**This is WP9's risk carrier. Read the whole file before touching a line.**

**The three shapes:**

| Shape | Functions | Lines |
|---|---|---|
| diff-and-audit writers | `set_tool_labels`, `set_agent_labels`, `set_flow_labels` | `41-83`, `140-164`, `167-185` |
| group-by readers | `tool_entitlement_ids`, `agent_entitlement_ids`, `flow_entitlement_ids` | `20-31`, `86-102`, `105-114` |
| single-row readers | `labels_for`, `agent_label_ids`, `flow_label_ids` | `34-39`, `117-128`, `131-137` |

**Three single-row readers, not the audit's two.** `labels_for(tool_key)` (`agents/labels.py:34-39`)
is the same `frozenset(... .values_list("entitlement_id", flat=True))` shape on the tools side; the
audit's row for this finding lists only the agent and flow pair. Include it — a consolidation that
leaves the third copy behind is the shape this whole sweep exists to remove.

**What is load-bearing and must survive byte-for-byte:**

1. **The diff, not a full-set overwrite.** Each writer computes `wanted - current` and
   `current - wanted` inside `transaction.atomic()` and audits only the difference. The writers' own
   docstrings state why: *"an audit trail that recorded 'labelled' for a label already there is a
   trail whose interesting lines are invisible."* `/chat/access/` reads that trail.
2. **The audit row shape.** All three call
   `audit.record(actor, ACTION, target_type=<"tool"|"agent"|"flow">, target_key=<tool_key str | str(pk)>,
   target_label=<tool_key | slug>, entitlement_id=entitlement_id)`. The action constant, the
   `target_type` string, and the *spelling* of `target_key`/`target_label` differ per shape and are
   what an operator reads. A generic writer must take these as parameters, never derive them.
3. **The `atomic()` scope.** The diff and both audit directions are inside one transaction.

- [ ] **Step 1: Write the characterization tests**

Before extracting anything, pin the current audit output for all three writers. Extend
`agents/tests/test_tool_labels.py` and `agents/tests/test_agent_flow_labels.py` — the existing tests
(`test_setting_labels_writes_the_difference_and_audits_both_directions`,
`test_setting_the_same_labels_twice_writes_no_second_event`) cover the tool path; add the same two
for the agent and flow paths if they are not already there, asserting the **exact** `target_type`,
`target_key`, `target_label` and action of every row written:

```python
@pytest.mark.parametrize(("setter", "target_type"), [
    ("set_tool_labels", "tool"),
    ("set_agent_labels", "agent"),
    ("set_flow_labels", "flow"),
])
def test_each_writer_audits_only_the_difference_with_its_own_row_shape(setter, target_type):
    """C-28. The three writers share a shape and MUST NOT share their
    audit vocabulary: an operator reading `/chat/access/` sees
    `target_type`, `target_key` and `target_label` spelled per kind. This
    is the pin that makes the extraction safe, so write it first and read
    the real spellings out of the current code rather than guessing."""
```

- [ ] **Step 2: Run them and watch them pass** — characterization; green before and after.

- [ ] **Step 3: Extract the group-by reader**

The three `*_entitlement_ids` functions each run one `values_list` and fold it into a
`{key: frozenset(ids)}` dict. Extract:

```python
def _entitlement_ids_by_key(queryset, key_field: str) -> dict:
    """{`key_field` value: frozenset of entitlement ids} in ONE query.

    The body `tool_entitlement_ids`, `agent_entitlement_ids` and
    `flow_entitlement_ids` each carried (C-28). Each of the three keeps
    its own public function, its own docstring and its own queryset --
    what they share is the fold, not the question.
    """
    grouped: dict = {}
    for key, entitlement_id in queryset.values_list(key_field, "entitlement_id"):
        grouped.setdefault(key, set()).add(entitlement_id)
    return {key: frozenset(ids) for key, ids in grouped.items()}
```

Read the three current bodies first — if any of them orders, filters or coerces differently, that
difference is either a bug (report it) or deliberate (keep that one out of the extraction).

- [ ] **Step 4: Extract the single-row reader**

`labels_for` (`:34-39`), `agent_label_ids` (`:117-128`) and `flow_label_ids` (`:131-137`) all return
`frozenset(<queryset>.values_list("entitlement_id", flat=True))`. One private helper, all three public
functions kept with their docstrings.

- [ ] **Step 5: Extract the writer, with the audit vocabulary as parameters**

```python
def _set_labels(*, actor, current, wanted, add, remove, audit_row):
    """Apply the DIFFERENCE between `current` and `wanted`, auditing each
    direction, inside ONE transaction (C-28).

    THE DIFF IS THE POINT, not an optimisation: `/chat/access/` reads this
    audit trail, and a trail that recorded "labelled" for a label already
    present is a trail whose interesting lines are invisible. Every one of
    the three writers said so in its own docstring; they now say it once.

    `add`/`remove` are the callers' own row writers (the join model
    differs per kind). `audit_row(entitlement_id, action)` is the
    caller's own audit vocabulary -- `target_type`, `target_key` and
    `target_label` are spelled differently per kind and are what an
    operator reads, so they are supplied, never derived here.
    """
    added = wanted - current
    removed = current - wanted
    with transaction.atomic():
        for entitlement_id in sorted(added):
            add(entitlement_id)
            audit_row(entitlement_id, LABELLED)
        for entitlement_id in sorted(removed):
            remove(entitlement_id)
            audit_row(entitlement_id, UNLABELLED)
```

Read the real action constants out of the current code — `LABELLED`/`UNLABELLED` above are
placeholders, and the three writers may not share them.

Each of `set_tool_labels`, `set_agent_labels`, `set_flow_labels` keeps its `def` line, its full
docstring, and becomes a body that computes `current`/`wanted`, builds its own `add`/`remove`/
`audit_row` closures, and calls `_set_labels`.

- [ ] **Step 6: Run the three suites, twice**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/tests agents/chat/tests/test_visibility.py`
Then in reverse collection order (Global Constraint 3's third run), because registry-shaped modules
are where ordering leaks show.
Expected: PASS, no assertion changes.

- [ ] **Step 7: Full gate, then commit**

```
git add agents/labels.py agents/tests/test_tool_labels.py agents/tests/test_agent_flow_labels.py
git commit -m "refactor(agents): one diff-and-audit writer, one group reader, one row reader"
```

Body:

```
C-28. `agents/labels.py` carried three copies of the diff-and-audit write,
three of the group-by fold, and three of the single-row read. Each shape is
now written once; all eight public functions keep their names, signatures
and docstrings. The audit vocabulary is passed in, never derived: the
action constant, `target_type`, `target_key` and `target_label` are spelled
per kind and are what an operator reads on `/chat/access/`. The diff --
which is what keeps the trail's interesting lines visible -- and the
single `atomic()` around both directions are unchanged.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 19: One taint tag-writer, one workstream row reader (C-29, C-30)

**Files:**
- Modify: `agents/runtime/taint.py:115-142`, `:145-168`
- Modify: `agents/workstreams.py:104`, `:159`, `:183`, `:195`
- Test: `agents/tests/test_taint.py`, `agents/tests/test_workstream_seam.py`

**Column:** `agents`, intra-column, both halves.

**C-29 — the two tag-writers.** `_add_conversation_tags` (`:115-142`) and `_add_workstream_tags`
(`:145-168`) are structurally identical: read existing → compute `fresh` → `bulk_create(ignore_conflicts=True)`
→ one `audit.record` per fresh id. They differ in the target model (`ConversationTaint` vs
`WorkstreamTaint`), the target object (`conversation` vs `stream`), and the audit vocabulary
(`CONVERSATION_TAINTED` with `target_key=conversation.pk` vs `WORKSTREAM_TAINTED` with
`target_key=stream.pk, conversation=str(conversation.pk)`).

**The constraint that must survive.** Both run inside the caller's transaction —
`agents/runtime/loop.py:496` opens `with transaction.atomic():` and creates the tool `Turn` row, then
calls `stamp_turn_taint(...)` at `:510` inside it. `loop.py:491-495`'s own comment says this was
added deliberately: *"the tag rows and the turn that caused them must land together or not at all."*
**The extracted helper must not open a transaction of its own and must not be called from outside
one.** Say so in its docstring.

**C-30 — four copies of one read.** `agents/workstreams.py`:

```python
104:    row = visible_workstreams(principal).filter(pk=workstream_id).first()    # workstream_scope (def :94)
159:    row = visible_workstreams(principal).filter(pk=workstream_id).first()    # pin_target       (def :136)
183:    return visible_workstreams(principal).filter(pk=workstream_id).exists()  # workstream_visible (def :165)
195:    row = visible_workstreams(principal).filter(pk=workstream_id).first()    # set_upload_placement_default (def :186)
```

`pin_target`'s own docstring records that this file has already deduplicated this query once (a
retired `workstream_name` helper folded into it) — so the precedent for consolidating it is the
file's own.

- [ ] **Step 1: Write the characterization tests**

For C-29, extend `agents/tests/test_taint.py`. The existing tests
(`test_a_turn_that_returned_a_labelled_document_leaves_one_tag_at_each_level`,
`test_a_turn_with_no_document_artifact_costs_nothing`,
`test_a_turn_that_retrieves_and_then_FAILS_still_taints`) cover behaviour; add one that pins the
audit vocabulary of both levels explicitly, reading the real action constants and field names out of
the current code:

```python
def test_the_two_taint_levels_keep_their_own_audit_vocabulary():
    """C-29. The two writers share a shape and must not share their audit
    rows: the workstream row carries a `conversation` field the
    conversation row does not, and the two actions are read separately."""
```

And one that pins the transaction constraint:

```python
def test_stamping_taint_outside_a_transaction_is_not_how_it_is_called():
    """C-29's real constraint, made visible. `loop.py` opens ONE atomic
    block around the tool `Turn` row and this stamp, because the tags and
    the turn that caused them must land together or not at all. This test
    documents that the helper does not open its own -- if it ever does,
    two nested transactions with different failure modes appear where one
    was intended."""
    source = (Path(settings.BASE_DIR) / "agents/runtime/taint.py").read_text()
    assert "transaction.atomic" not in source
```

For C-30, `agents/tests/test_workstream_seam.py` already covers all four callers
(`test_the_scope_carries_the_stream_half_and_no_pins`,
`test_a_stream_this_principal_may_not_be_in_is_indistinguishable_from_one_that_is_gone`,
`test_setting_the_upload_default_through_the_seam_writes_and_audits`). Confirm `workstream_visible`
has coverage too; add a test for it if it does not.

- [ ] **Step 2: Run them and watch them pass** — characterization, both halves.

- [ ] **Step 3: C-29 — extract the tag-writer**

```python
def _add_tags(*, rows_model, existing_ids, wanted_ids, build_row, audit_row):
    """`bulk_create` the tags in `wanted_ids - existing_ids`, ignoring
    conflicts, and audit each one that was genuinely new (C-29).

    **MUST BE CALLED INSIDE THE CALLER'S `transaction.atomic()`** and
    deliberately opens none of its own: `agents/runtime/loop.py` wraps the
    tool `Turn` row and this stamp in ONE block, because the tag rows and
    the turn that caused them must land together or not at all. A nested
    block here would be a second failure boundary inside that one.

    `audit_row(tag_id)` is the caller's own vocabulary: the two levels
    write different actions and the workstream row carries a
    `conversation` field the conversation row does not.
    """
```

Both `_add_conversation_tags` and `_add_workstream_tags` keep their names, signatures and docstrings
and become short bodies over it.

- [ ] **Step 4: C-30 — extract the row reader**

```python
def _visible_row(principal, workstream_id):
    """The one `Workstream` row `principal` may see with this id, or
    `None` (C-30).

    `visible_workstreams(principal).filter(pk=workstream_id)` was written
    out at four call sites in this file, three of them `.first()` and one
    `.exists()`. `pin_target`'s own docstring records that this query has
    already been folded once before, when `workstream_name` retired into
    it; this is the rest of that fold.

    `None` and "not visible" are the SAME answer on purpose -- the seam's
    404 house rule -- and every caller already treats them that way.
    """
    return visible_workstreams(principal).filter(pk=workstream_id).first()
```

`:104`, `:159` and `:195` become `row = _visible_row(principal, workstream_id)`.
`:183` becomes `return _visible_row(principal, workstream_id) is not None`.

Note the one behaviour difference to state in the commit body: `workstream_visible` moves from
`.exists()` (one `SELECT 1`) to `.first()` (one row fetched and discarded). That is a strictly
smaller change than it sounds — same one query, a few bytes more over the wire — and it buys one
reader instead of two. If a query-count or query-shape test pins `.exists()` specifically, keep
`.exists()` and give `_visible_row` an `exists_only` keyword rather than moving the assertion.

- [ ] **Step 5: Run the suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/tests agents/runtime/tests agents/chat/tests/test_workstream_page.py agents/contracts/tests/test_workstreams.py tools/rag/tests/test_workstream_pins.py`
Expected: PASS, no assertion changes.

- [ ] **Step 6: Full gate, then commit**

```
git add agents/runtime/taint.py agents/workstreams.py agents/tests/test_taint.py agents/tests/test_workstream_seam.py
git commit -m "refactor(agents): one taint tag-writer, one visible-workstream row reader"
```

Body:

```
C-29 the two taint levels wrote their tags with the same fifteen lines and
differ only in model and audit vocabulary; the shared body takes both as
parameters and opens no transaction of its own -- `loop.py` wraps the tool
turn and this stamp in one block on purpose, and a nested one would be a
second failure boundary inside it. C-30 `visible_workstreams(principal)
.filter(pk=...)` appeared at four call sites in one file; `pin_target`'s
own docstring records that this query was already folded once.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 20: `tools/rag/jobs.py` — one stranded-row failer, one preview truncation (C-31, jobs half)

**Files:**
- Modify: `tools/rag/jobs.py:519-594` (`on_ingest_terminal`), `:830-873` (`on_consolidate_terminal`),
  `:291-298`, `:512-516`, `:824-827` (the three `Truncator(...).chars(120)` calls)
- Test: `tools/rag/tests/test_jobs.py`, `tools/rag/tests/test_consolidate.py`

**Column:** `tools/rag`, intra-column. `tools/rag/jobs.py` is 873 lines.

**Half one — the stranded-row repair.** `on_ingest_terminal` (`:519-594`) and
`on_consolidate_terminal` (`:830-873`) share one shape, and `on_consolidate_terminal`'s own docstring
already says so: *"THE SAME SHAPE AND THE SAME REASON AS `on_ingest_terminal`."*

```
build a state-specific `status_detail`
  -> one conditional  .filter(status__in=(PENDING, PROCESSING))
                      .update(status=FAILED, status_detail=..., updated_at=timezone.now())
  -> branch on `updated`: debug (already terminal) vs warning (row gone)
```

They differ in how the row is selected — `pk=document_id` versus
`notes_conversation_id=conversation_id` — and in the copy of the detail string.

**Half two — the preview truncation.** `Truncator(...).chars(120)` at `:298`
(`payload.get("question", "")`), `:516` (`payload.get("title", "")`) and `:827`
(`f"Notes — {payload.get('title', '')}"`). Each docstring cross-references the others
(*"truncated the same way `summarize_ask` truncates"*).

- [ ] **Step 1: Write the characterization tests**

The existing coverage is good and must stay green: `tools/rag/tests/test_jobs.py`'s
`TestOnIngestTerminal` (`test_cancelled_state_writes_the_cancelled_copy`,
`test_race_a_still_alive_stale_worker_writing_ready_is_not_clobbered`,
`test_already_terminal_document_logs_at_debug_not_warning`) and
`tools/rag/tests/test_consolidate.py:492`
(`test_on_consolidate_terminal_repairs_a_stranded_pending_note_and_leaves_a_ready_one_alone`). Add
one pin per half:

```python
def test_the_two_terminal_handlers_repair_a_stranded_row_the_same_way():
    """C-31. Both leave a PENDING/PROCESSING row FAILED with a
    state-specific detail, and both log at debug when the row was already
    terminal and at warning when it is gone. The conditional filter is the
    race guard: a still-alive stale worker that wrote READY must not be
    clobbered."""


def test_every_job_preview_truncates_at_the_same_width():
    """C-31, second half. Three call sites truncate a preview to 120
    characters and each docstring points at the other two. One constant,
    one function."""
    assert jobs.PREVIEW_CHARS == 120
    assert jobs._preview("x" * 500).endswith("…")
    assert len(jobs._preview("x" * 500)) <= jobs.PREVIEW_CHARS + 1
```

- [ ] **Step 2: Run them; the truncation pin fails, the handler pin passes**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_jobs.py tools/rag/tests/test_consolidate.py -k "stranded_row_the_same_way or same_width"`
Expected: the truncation pin FAILS (`jobs` has no `PREVIEW_CHARS`/`_preview`); the handler pin passes.

- [ ] **Step 3: Extract the truncation**

```python
# One width for every job preview (C-31). Three call sites truncated to
# 120 characters and each of their docstrings pointed at the other two;
# `Truncator` is Django's, and `.chars` counts characters rather than
# bytes so a multibyte title is not cut mid-glyph.
PREVIEW_CHARS = 120


def _preview(text: str) -> str:
    """`text` truncated to `PREVIEW_CHARS`, for a queue row's own
    one-line summary."""
    return Truncator(text or "").chars(PREVIEW_CHARS)
```

Repoint `:298`, `:516` and `:827`. `:827`'s argument keeps its `f"Notes — …"` prefix at the call
site — the prefix is that job kind's own vocabulary, not the truncation's business.

- [ ] **Step 4: Extract the stranded-row repair**

```python
def _fail_stranded_rows(rows, *, detail: str) -> int:
    """Leave any still-unfinished row in `rows` FAILED with `detail`, and
    return how many moved (C-31).

    THE CONDITIONAL FILTER IS THE RACE GUARD, not a nicety: a stale worker
    that is still alive may have written READY between the terminal
    callback firing and this update running, and clobbering that is worse
    than leaving a stranded row. Only PENDING/PROCESSING is repaired.

    THE LOGGING DELIBERATELY STAYS AT EACH CALL SITE. `on_ingest_terminal`
    spends a second `.exists()` query to tell "its own handler writeback
    got there first" (debug) apart from "the row is gone" (warning);
    `on_consolidate_terminal` has one debug line (`:870-873`) and no such query.
    Sharing that branch would make one path pay a query it does not need
    or make the other lose a distinction an operator reads. What is
    genuinely identical is the UPDATE, and that is all this owns.
    """
    return rows.filter(
        status__in=(Document.Status.PENDING, Document.Status.PROCESSING),
    ).update(status=Document.Status.FAILED, status_detail=detail, updated_at=timezone.now())
```

**The logging stays at each call site, and that is the decision, not a fallback.** The two branches
genuinely differ and the difference is operator-facing: `on_ingest_terminal` (`tools/rag/jobs.py:583-594`)
spends a second `Document.objects.filter(pk=document_id).exists()` query to tell *"already left
PENDING/PROCESSING — its own handler writeback got there first"* (debug) apart from *"no longer
exists"* (warning), while `on_consolidate_terminal` (`:870-873`) has one debug line (`:870-873`) and no such
query. Folding those into one helper would either make the consolidate path pay a query it does not
need or make the ingest path lose a distinction an operator reads. So `_fail_stranded_rows` owns
only the conditional `UPDATE` — the race guard, which is the part that is genuinely identical — and
returns the count each caller branches on. Say so in the commit body; the helper's docstring above
already does.

The `what` parameter is therefore **not** needed. Drop it from the signature.

`on_ingest_terminal` calls it with `Document.objects.filter(pk=document_id)`;
`on_consolidate_terminal` with `Document.objects.filter(notes_conversation_id=conversation_id)`.
Each keeps its own detail-string construction.

- [ ] **Step 5: Run the suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_jobs.py tools/rag/tests/test_consolidate.py tools/rag/tests/test_ingest.py`
Expected: PASS, no assertion changes.

- [ ] **Step 6: Full gate, then commit**

```
git add tools/rag/jobs.py tools/rag/tests/test_jobs.py tools/rag/tests/test_consolidate.py
git commit -m "refactor(rag): one stranded-row repair, one preview width"
```

Body:

```
C-31, the jobs.py half. `on_consolidate_terminal`'s own docstring already
said it was "the same shape and the same reason as `on_ingest_terminal`";
now it is the same code, with the row selection and the detail copy at
each call site and the conditional PENDING/PROCESSING filter -- the guard
against clobbering a still-alive stale worker's READY -- written once.
Three `Truncator(...).chars(120)` call sites, each pointing at the other
two in prose, become one `PREVIEW_CHARS` and one `_preview`.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 21: The compose engine-env block becomes a YAML anchor, per file (C-33)

**Files:**
- Modify: `compose.yaml:69-72,108-111,129-137`
- Modify: `compose.preview.yaml:83-86,118-121,142-150`
- Modify: `docs/DEV.md` (the compose section) if it quotes the env block
- Test: `scripts/tests/test_preview.py` — read it first; it writes its own stub
  `compose.preview.yaml` and never inspects the real file's `environment:` block, so nothing pins
  this. A new pin is this task's own deliverable.

**The finding.** The same four-line engine-env block (`DATABASE_URL`, `OLLAMA_BASE_URL`,
`COMFYUI_BASE_URL`, `WHISPER_BASE_URL`) is written out six times — three services (`app`, `watcher`,
`worker`) in each of two files. Neither file uses a YAML anchor anywhere today (`grep -n '&\|<<:'`
finds only `&&` inside `sh -c` command strings).

**Collapse per file, not across them.** The two files stay separate by design (ADR 0011, branch
preview stacks), and a YAML anchor cannot cross a file boundary without an `include`/merge mechanism
this stack does not use. That file boundary is the whole reason for two anchors rather than one —
**not** a difference in values: the four engine-env keys are currently byte-identical between
`compose.yaml:69-72` and `compose.preview.yaml:83-86`. Six copies become two.

- [ ] **Step 1: Capture the rendered config before touching anything**

```bash
docker compose -f compose.yaml config > /tmp/before-primary.yaml
docker compose -f compose.preview.yaml config > /tmp/before-preview.yaml
```

This is the whole safety net for this task: a YAML anchor is a *serialization* change and must
produce byte-identical rendered output. If `docker compose` is not running, do this task later —
do not proceed on inspection alone.

- [ ] **Step 2: Add the anchor to `compose.yaml`**

At the top of the file, above `services:`, in the `x-`-prefixed extension-field form compose
ignores:

```yaml
# C-33: the four engine addresses every service needs, written once.
# `app`, `watcher` and `worker` each carried this block verbatim. An
# anchor, not a shared file: `compose.preview.yaml` keeps its own copy
# with its own values by design (ADR 0011), and an anchor cannot cross
# files.
x-engine-env: &engine-env
  DATABASE_URL: ...        # copy the four lines verbatim from compose.yaml:69-72
  OLLAMA_BASE_URL: ...
  COMFYUI_BASE_URL: ...
  WHISPER_BASE_URL: ...
```

Then each of the three services' `environment:` becomes:

```yaml
    environment:
      <<: *engine-env
      # any keys THIS service adds that the other two do not — read
      # :129-137 carefully, it is a nine-line block, not a four-line one
```

Read `compose.yaml:129-137` in particular: it is longer than the other two, which means the shared
part is the four engine lines and the rest is service-specific and stays inline.

**Mind the mapping form.** If the current blocks use the `- KEY=value` list form rather than the
`KEY: value` mapping form, a `<<:` merge will not work — YAML merge keys apply to mappings only.
Convert the three blocks in that file to mapping form as part of the change, and let Step 4 prove the
rendered result is unchanged.

- [ ] **Step 3: Do the same in `compose.preview.yaml`**

Same shape, its own anchor, its own values, at `:83-86`, `:118-121`, `:142-150`.

- [ ] **Step 4: Prove the rendered config is byte-identical**

```bash
docker compose -f compose.yaml config > /tmp/after-primary.yaml
docker compose -f compose.preview.yaml config > /tmp/after-preview.yaml
diff /tmp/before-primary.yaml /tmp/after-primary.yaml
diff /tmp/before-preview.yaml /tmp/after-preview.yaml
```
Expected: both diffs empty. **A non-empty diff means the anchor changed behaviour — revert and stop.**

- [ ] **Step 5: Add the pin**

In `scripts/tests/test_preview.py` (or `foundation/ops/tests/`, wherever a repo-shape assertion is
more at home — read both and pick):

```python
@pytest.mark.parametrize("compose_file", ["compose.yaml", "compose.preview.yaml"])
def test_the_engine_env_block_is_written_once_per_compose_file(compose_file):
    """C-33. Four engine addresses were written out three times per file,
    six times in all. An anchor per file collapses each set to one; the
    two files stay separate by design (ADR 0011) and an anchor cannot
    cross them."""
    text = (Path(settings.BASE_DIR) / compose_file).read_text()
    assert "&engine-env" in text
    assert text.count("<<: *engine-env") == 3
    assert text.count("COMFYUI_BASE_URL") == 1
```

- [ ] **Step 6: Bring a preview stack up and down**

Run: `scripts/preview up hygiene-sweep` then `scripts/preview down hygiene-sweep`
Expected: the stack starts and the web container answers. A rendered-config diff proves the YAML;
this proves compose actually accepts it.

- [ ] **Step 7: Full gate, then commit**

```
git add compose.yaml compose.preview.yaml scripts/tests/test_preview.py docs/DEV.md
git commit -m "cleanup(deploy): the engine-env block is an anchor, once per compose file"
```

Body:

```
C-33. The four engine addresses were written out for `app`, `watcher` and
`worker` in each of two compose files -- six copies of one fact. One
anchor per file; the two files stay separate by design (ADR 0011) and an
anchor cannot cross them. Proven by diffing `docker compose config` before
and after: the rendered configuration is byte-identical.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 22: The endpoint-dedup + health-check prologue is extracted once (C-15)

**Files:**
- Modify: `tools/rag/messages.py` (the new helper's home — see "Column" below)
- Modify: `tools/rag/views.py:1631-1649` (`AskView._precheck_models`) and `:1943-1950`
  (`_precheck_embed_role`)
- Modify: `tools/rag/jobs.py:208-222` (`_precheck`)
- Test: `tools/rag/tests/test_views.py`, `tools/rag/tests/test_jobs.py`, `tools/rag/tests/test_messages.py`

**Column:** `tools/rag`, all three call sites. Put the helper in `tools/rag/messages.py`, which
already owns `UNBOUND`/`UNREACHABLE` (`:75-76`) and `model_unavailable_message` (`:142-180`) and is
already imported by both `views.py` and `jobs.py` — so it is the module both callers may import
without creating a new edge.

**Extract ONLY the endpoint-dedup + health-check half.** The audit is explicit and the code agrees:

| Region | Status |
|---|---|
| `views.py:1616-1629` — the resolve loop with `answer_override` | **DO NOT TOUCH.** Diverges deliberately. |
| `views.py:1631-1649` — dedup + health check | **EXTRACT.** |
| `views.py:1651-1653` — the `Response` return | **DO NOT TOUCH.** Web-caller-specific. |
| `views.py:1934-1941` — single-role resolve | **DO NOT TOUCH.** Own message shape. |
| `views.py:1943-1950` — the probe try/except | **EXTRACT** (degenerate one-role case of the same helper). |
| `views.py:1952-1958` — single-role return | **DO NOT TOUCH.** |
| `jobs.py:195-206` — the resolve loop with `_resolve_answer(payload)` | **DO NOT TOUCH.** `jobs.py:178-189`'s docstring documents a deliberate message-precision divergence from the web path for a deleted or renamed connection pk. That divergence must survive. |
| `jobs.py:208-222` — dedup + health check | **EXTRACT.** |
| `jobs.py:224` — the 3-tuple return | **DO NOT TOUCH.** |

`AskView._precheck_models`'s own docstring (`:1590-1614`) says *"`tools.rag.jobs._precheck` is this
method's deliberately-duplicated sibling … keep the two in sync by hand if this cause matrix ever
changes."* After this task, one half of that sentence is no longer true — update it.

**The extractable region, verbatim** (`views.py:1631-1649`; `jobs.py:208-222` is the same shape down
to the comment wording):

```python
        # Dedup by normalized endpoint (matches models/registry/views.py's
        # norm_endpoint) so two roles pointing at the same engine cost one
        # health check, not two.
        endpoint_health: dict[str, bool] = {}
        for role, model in resolved.items():
            endpoint_key = model.endpoint.rstrip("/")
            if endpoint_key not in endpoint_health:
                try:
                    endpoint_health[endpoint_key] = get_engine(model.engine).is_healthy(
                        model.endpoint
                    )
                except Exception:  # noqa: BLE001 -- log detail, then degrade to the friendly 503
                    logger.exception(
                        "Unexpected error health-checking %s endpoint %r (role %r)",
                        model.engine, model.endpoint, role,
                    )
                    endpoint_health[endpoint_key] = False
            if not endpoint_health[endpoint_key]:
                causes[role] = UNREACHABLE
```

- [ ] **Step 1: Write the characterization tests**

Three surfaces, three assertions that the *messages* are unchanged. Read what each currently
produces and pin it:

```python
def test_one_unreachable_endpoint_shared_by_two_roles_is_probed_once():
    """C-15. The dedup is the point of the block being extracted: two
    roles pointing at one model server cost ONE health check. Pinned at
    all three surfaces, because all three had their own copy."""


@pytest.mark.parametrize("surface", ["ask_view", "search_view", "ask_job"])
def test_each_surface_keeps_its_own_unreachable_copy(surface):
    """The half that must NOT change. The web path and the job path say
    different things about a deleted connection pk on purpose
    (`jobs.py`'s own docstring records the divergence), and the
    single-role search path has its own message shape. Extracting the
    probe must leave every rendered sentence exactly as it is."""
```

- [ ] **Step 2: Run them and watch them pass** — characterization; green before and after.

- [ ] **Step 3: Write the helper in `tools/rag/messages.py`**

```python
def unreachable_endpoints(resolved: dict, *, log_prefix: str) -> set:
    """The roles in `resolved` whose endpoint does not answer (C-15).

    ONE PROBE PER NORMALIZED ENDPOINT, not one per role: two roles bound
    to the same model server are one question. The normalization is
    `endpoint.rstrip("/")`, matching `models/registry/views.py`'s own
    `norm_endpoint`.

    AN ENGINE THAT RAISES IS UNREACHABLE, not a 500 -- the exception is
    logged with `log_prefix` (the caller's own name for itself, because
    an operator reading "rag.ask re-check" and one reading "AskView" are
    looking at different surfaces) and the endpoint is recorded as down.

    RETURNS ROLES, NOT MESSAGES. Every caller renders its own copy: the
    ask page returns a `Response`, the search page returns a single-role
    message, and the job path returns a 3-tuple and deliberately says
    something different about a deleted connection pk (`tools/rag/
    jobs.py`'s own docstring records that divergence, which this
    extraction does not touch).
    """
    endpoint_health: dict[str, bool] = {}
    unreachable = set()
    for role, model in resolved.items():
        endpoint_key = model.endpoint.rstrip("/")
        if endpoint_key not in endpoint_health:
            try:
                endpoint_health[endpoint_key] = get_engine(model.engine).is_healthy(model.endpoint)
            except Exception:  # noqa: BLE001 -- log detail, then degrade to the caller's friendly copy
                logger.exception(
                    "%s: unexpected error health-checking %s endpoint %r (role %r)",
                    log_prefix, model.engine, model.endpoint, role,
                )
                endpoint_health[endpoint_key] = False
        if not endpoint_health[endpoint_key]:
            unreachable.add(role)
    return unreachable
```

Check `messages.py`'s current imports — it may not import `get_engine` or hold a `logger` yet; add
both, and confirm neither creates a cycle with `views.py`/`jobs.py` (it must not import either).

- [ ] **Step 4: Use it at all three sites**

`views.py:1631-1649` becomes:

```python
        for role in unreachable_endpoints(resolved, log_prefix="AskView"):
            causes[role] = UNREACHABLE
```

`jobs.py:208-222` becomes:

```python
    for role in unreachable_endpoints(resolved, log_prefix="rag.ask re-check"):
        causes[role] = UNREACHABLE
```

`views.py:1943-1950` — the one-role case — becomes:

```python
    healthy = not unreachable_endpoints(
        {RAG_EMBED_ROLE: embed_resolved}, log_prefix="SearchView")
```

and `:1952-1958` is left exactly as it is.

- [ ] **Step 5: Update the two docstrings that describe the duplication**

`AskView._precheck_models`'s docstring (`:1590-1614`) and `jobs._precheck`'s (`:178-189`) both
describe a hand-maintained twin. Rewrite the relevant sentences: the *health-check* half is now one
function; the *resolve* half and the message precision remain deliberately separate, and here is why.
Do not delete the divergence note — it is the part that must survive.

- [ ] **Step 6: Run the three suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py tools/rag/tests/test_jobs.py tools/rag/tests/test_messages.py tools/rag/tests/test_tools.py`
Expected: PASS, no assertion changes. Any change to a rendered message is a regression, not an
improvement — Step 1's tests exist to catch exactly that.

- [ ] **Step 7: Update the README**

`tools/rag/README.md` §"Design in brief": the ask/search precheck now shares one probe helper across
its three surfaces, while each keeps its own copy — one sentence, naming why the copy stayed split.

- [ ] **Step 8: Full gate, then commit**

```
git add tools/rag/messages.py tools/rag/views.py tools/rag/jobs.py tools/rag/README.md tools/rag/tests
git commit -m "refactor(rag): one endpoint-dedup health check for all three precheck surfaces"
```

Body:

```
C-15, the health-check half only. The dedup-by-normalized-endpoint probe
was written out three times -- the ask page, the search page and the job
re-check -- and `AskView._precheck_models`'s own docstring asked a future
reader to keep them in sync by hand. One `messages.unreachable_endpoints`
now owns it and returns ROLES, so every caller still renders its own copy.
The resolve halves stay split (`answer_override` versus `_resolve_answer`)
and so does the web-versus-job message precision for a deleted connection
pk, which `jobs.py`'s own docstring documents as deliberate.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 23: One shared stage-and-enqueue helper for both upload paths (C-14)

**Files:**
- Modify: `tools/rag/ingest.py` (the new helper's home)
- Modify: `tools/rag/views.py:995-1152` (`document_upload`'s per-file loop)
- Modify: `tools/rag/services.py:660-761` (`stage_turn_attachments`'s per-file loop)
- Modify: `tools/rag/README.md` — the "Browser upload" subsection (`:147-172`) and the staging
  paragraph (`:412`)
- Test: `tools/rag/tests/test_views.py` (`TestDocumentUpload`, `:2678`),
  `tools/rag/tests/test_services.py`, `tools/rag/tests/test_ingest.py`

**Column:** `tools/rag`. The helper lives in `tools/rag/ingest.py` — both callers already import
`ingest`, so this creates no new edge. **Do it first in WP5**: Tasks 24, 25, 26 and 27 all land in or
around what this task creates.

**The finding.** Two ~90-line loops, read in full and diffed line by line. The exception ladder, the
hash-before-enqueue ordering, the unlink-on-cap-rejection, and the three-way queued/unchanged/failed
split are **identical**. Every difference is bookkeeping:

| Region | `views.document_upload` | `services.stage_turn_attachments` |
|---|---|---|
| oversize | `messages.error(…)`, not counted anywhere | `failed.append(f"{name} (larger than …)")` |
| unchanged-hash | `_upload_sha256(dest)` | `sha256_file(dest)` (C-25 closes this) |
| `enqueue_ingest` args | `category or None`, workstream from `placement` | `None`, `effective_workstream_id` |
| `FileNotFoundError` | logs, `queued += 1` | logs, `queued += 1`, **`_attach(raced_document, …)`** |
| cap/duration refusals | `messages.error(str(exc))`, unlink, **not** counted failed | `failed.append(f"{name} ({exc})")`, unlink |
| success | `queued += 1`; tabular → flash; labels → `set_document_labels` | `queued += 1`; tabular → list; **`_attach`** + `staged_document_ids` |
| `dest_unchanged` | append, unlink | append, unlink, **`_attach(existing, …)`** |
| else | `failed.append(name)` | `failed.append(name)`, **`_attach`** + `staged_document_ids` |
| after the loop | flash summaries, `redirect("rag-documents")` | best-effort `rmdir`, `AttachmentUploadResult(...)` |

`services.py:575-579` already admits the copy in prose:

> MIRRORS `tools.rag.views.document_upload`'s OWN pre-round-13 chat-door loop almost verbatim …
> reusing `tools.rag.ingest.enqueue_ingest`/`stage_document` exactly as it always did … rather than
> reimplementing staging here.

**The seam.** Extract the **per-file** work — write to disk, hash-compare, enqueue, and the exception
ladder — and return a value that says which of the four outcomes happened and which `Document` (if
any) it concerns. Each caller keeps 100% of its own bookkeeping by switching on that value. Nothing
about flashes, labels, `_attach`, `staged_document_ids` or the return type moves.

**Interfaces:**
- Produces: `tools.rag.ingest.StageOutcome` — a frozen dataclass with fields
  `name: str`, `kind: Literal["queued", "unchanged", "rejected", "oversize", "refused", "failed"]`,
  `document: Document | None`, `reason: str`, `is_tabular: bool`, `raced: bool`.
- Produces:
  ```python
  def stage_and_enqueue_one(
      upload, target_dir: Path, *, category: str | None, actor,
      workstream_id: int | None, document_scope, max_upload_bytes: int,
      upload_exts: frozenset[str], human_cap: str, log_label: str,
  ) -> StageOutcome
  ```

Six `kind` values, not four, because the two callers **report the same event differently** and a
three-way split would force one of them to change behaviour: `"oversize"` and `"refused"` (the
duration/page-cap exceptions) are counted as `failed` by the chat path and merely flashed by the
browser path. Handing back the distinction lets each keep what it does today.

- [ ] **Step 1: Write the characterization tests**

This task's contract is "nothing observable changes at either surface". Before extracting, pin the
observable behaviour of both, covering every branch in the table above:

```python
class TestBothUploadPathsReportTheSameEventsTheirOwnWay:
    """C-14. Two ninety-line loops with an identical exception ladder,
    identical hash-before-enqueue ordering and an identical three-way
    outcome split, differing only in bookkeeping. Extracting the shared
    body must not move a single reported outcome, so every branch is
    pinned at BOTH surfaces first."""

    # Browser path (tools/rag/tests/test_views.py::TestDocumentUpload):
    #   unsupported extension -> "Skipped unsupported file(s)"
    #   oversize              -> the cap flash, NOT counted in any summary
    #   duration/page-cap     -> exc message flashed, NOT counted failed
    #   unchanged re-upload   -> "already in the library and unchanged"
    #   watcher race          -> counted queued
    #   labels applied on success
    #   tabular -> "stored as a table"
    #
    # Chat path (tools/rag/tests/test_services.py):
    #   unsupported extension -> result.rejected
    #   oversize              -> result.failed, with the cap in the string
    #   duration/page-cap     -> result.failed, with the exc in the string
    #   unchanged re-upload   -> result.unchanged AND the existing doc attached
    #   watcher race          -> counted queued AND attached
    #   staged_document_ids populated on success and on the else branch
    #   tabular -> result.tabular
```

Most of these already exist in `TestDocumentUpload` (`test_views.py:2678-3173`) and in
`test_services.py`. Read both, list which branches are already covered, and add only the missing
ones. Do not duplicate an existing assertion.

- [ ] **Step 2: Run them and watch them pass**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py -k DocumentUpload tools/rag/tests/test_services.py`
Expected: PASS. Green before and after is the whole contract.

- [ ] **Step 3: Add `StageOutcome` and `stage_and_enqueue_one` to `tools/rag/ingest.py`**

```python
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
    kind: str          # "queued" | "unchanged" | "rejected" | "oversize" | "refused" | "failed"
    document: object | None = None
    reason: str = ""   # the exception's own message, for "oversize" and "refused"
    is_tabular: bool = False
    raced: bool = False  # a watcher won the race; counted queued by both callers


def stage_and_enqueue_one(
    upload, target_dir, *, category, actor, workstream_id, document_scope,
    max_upload_bytes, upload_exts, human_cap, log_label,
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

    HASH BEFORE ENQUEUE, and that ordering is load-bearing: `enqueue_ingest`
    moves the file, so the unchanged-comparison has to read it first. The
    `FileNotFoundError` branch below exists because the watcher can win
    that race, and a file the watcher already staged is queued, not lost.
    """
```

The body is `views.py:995-1152`'s ladder with every `messages.*`, `set_document_labels`,
`_attach`, and counter line removed, returning a `StageOutcome` at each point where those lines used
to sit. Write it by copying the browser loop and deleting, not by writing fresh — the ladder's
`except` ordering is load-bearing and retyping it invites a reordering.

- [ ] **Step 4: Rewrite `views.document_upload`'s loop**

```python
    queued, unchanged, rejected, failed = 0, [], [], []
    for upload in files:
        outcome = ingest.stage_and_enqueue_one(
            upload, target_dir,
            category=category or None, actor=principal,
            workstream_id=(scope.workstream_id if placement == "contained" else None),
            document_scope=document_scope, max_upload_bytes=max_upload_bytes,
            upload_exts=upload_exts, human_cap=human_cap, log_label="document_upload",
        )
        if outcome.kind == "rejected":
            rejected.append(outcome.name)
        elif outcome.kind == "oversize":
            messages.error(
                request,
                f"{outcome.name} is larger than the {human_cap} limit — raise it in RAG settings, "
                "or add it to the inbox folder directly.",
            )
        elif outcome.kind == "refused":
            # The exception's own message already names the fix, so this
            # path flashes it and counts the file nowhere -- unchanged
            # from before the extraction, and deliberately NOT what the
            # chat path does with the same outcome.
            messages.error(request, outcome.reason)
        elif outcome.kind == "queued":
            queued += 1
            if outcome.is_tabular:
                messages.info(request, f"{outcome.name} stored as a table; not searchable yet.")
            if wanted_labels and outcome.document is not None:
                set_document_labels(principal, outcome.document, wanted_labels)
        elif outcome.kind == "unchanged":
            unchanged.append(outcome.name)
        else:
            failed.append(outcome.name)
```

Everything after the loop (`:1132-1152`'s four summary blocks and the redirect) is untouched.

- [ ] **Step 5: Rewrite `services.stage_turn_attachments`'s loop**

```python
    for upload in files:
        outcome = ingest.stage_and_enqueue_one(
            upload, target_dir,
            category=None, actor=actor, workstream_id=effective_workstream_id,
            document_scope=document_scope, max_upload_bytes=max_upload_bytes,
            upload_exts=upload_exts, human_cap=human_cap,
            log_label="stage_turn_attachments",
        )
        if outcome.kind == "rejected":
            rejected.append(outcome.name)
        elif outcome.kind == "oversize":
            failed.append(f"{outcome.name} (larger than the {human_cap} limit)")
        elif outcome.kind == "refused":
            failed.append(f"{outcome.name} ({outcome.reason})")
        elif outcome.kind == "queued":
            queued += 1
            if outcome.is_tabular:
                tabular.append(outcome.name)
            if outcome.document is not None:
                _attach(outcome.document, conversation_id, turn_id)
                if not outcome.raced:
                    staged_document_ids.append(outcome.document.id)
        elif outcome.kind == "unchanged":
            unchanged.append(outcome.name)
            if outcome.document is not None:
                _attach(outcome.document, conversation_id, turn_id)
        else:
            failed.append(outcome.name)
            if outcome.document is not None:
                _attach(outcome.document, conversation_id, turn_id)
                staged_document_ids.append(outcome.document.id)
```

Read `services.py:700-761` carefully against this: the race branch currently attaches but does
**not** append to `staged_document_ids`, which is why `outcome.raced` exists. If the real code does
append there, drop the `raced` guard and drop the field.

Everything after the loop (`rmdir`, `AttachmentUploadResult`) is untouched.

- [ ] **Step 6: Run both suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests`
Then under `FARABUNKER_FEATURES='vision'` — the extension set differs between the two flag states and
this loop is where that difference lands.
Expected: PASS, no assertion changes at either surface.

- [ ] **Step 7: Update the README**

`tools/rag/README.md`: the "Browser upload" subsection (`:147-172`) and the staging paragraph
(`:412`) both describe where the per-file work happens. Repoint them at
`ingest.stage_and_enqueue_one`, and say in one clause what each door still owns for itself
(flashes and labels; attachment rows and the result tuple).

- [ ] **Step 8: Full gate, then commit**

```
git add tools/rag/ingest.py tools/rag/views.py tools/rag/services.py tools/rag/README.md tools/rag/tests
git commit -m "refactor(rag): one per-file stage-and-enqueue body for both upload doors"
```

Body:

```
C-14. The library's browser upload and the chat composer's attach each
carried a ninety-line loop with an identical exception ladder, identical
hash-before-enqueue ordering and an identical three-way outcome split --
`services.py`'s own comment said it "mirrors document_upload almost
verbatim". `ingest.stage_and_enqueue_one` now owns the per-file work and
returns a `StageOutcome`; every flash, label, attachment row, counter and
return type stays at its own call site. Six outcome kinds rather than
three, because the two doors collapse oversize and cap-refusal
DIFFERENTLY and neither should have to change to share a body.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 24: The extension twin, the gate prologues, the hardened sidecar read (C-17, C-32, C-46)

**Files:**
- Delete: `tools/rag/views.py:109-124` (`supported_upload_exts`) — C-17
- Modify: `tools/rag/views.py:992` (its one caller), `tools/rag/ingest.py:164` (the docstring
  sentence in `supported_exts` that names the twin), and `tools/rag/README.md:203-205` — C-17
- Modify: `tools/rag/views.py:444-451,491-494,590-592,1182-1185,1192,1364-1367,1461-1464` — C-32
- Modify: `tools/rag/ingest.py:816-818` — C-46
- Test: `tools/rag/tests/test_views.py`, `tools/rag/tests/test_ingest.py`,
  `tools/rag/tests/test_sidecar.py`

**Column:** `tools/rag` throughout.

**C-17 — the byte-identical twin.** `ingest.supported_exts()` (`:151-170`) and
`views.supported_upload_exts()` (`:109-124`) have character-for-character identical bodies:

```python
    exts = PROSE_EXTS | TABULAR_EXTS
    if "media" in settings.FARABUNKER_FEATURES:
        exts = exts | AV_EXTS | IMAGE_EXTS
    return frozenset(exts)
```

and each docstring names the other as its twin. `supported_upload_exts` has exactly one caller
(`views.py:992`, inside `document_upload`) and **no test calls it directly**;
`ingest.supported_exts()` has four production callers plus three dedicated flag-flip tests
(`test_ingest.py:2099,2109,2122`). Delete the twin, not the original.

**C-32 — the gate prologues.** Two pairs, verified byte-identical including their comment blocks:

```python
    principal = principal_for_request(request)               # :491-494 (document_delete)
    document = get_object_or_404(Document, pk=doc_id)        # and :1182-1185 (document_reingest)
    if not may_administer_document(principal, document):
        return HttpResponseForbidden(_DELETE_FORBIDDEN_MESSAGE)   # _REINGEST_… at the other site
```

```python
    principal = principal_for_request(request)               # :1364-1367 (document_file)
    document = readable_document(principal, doc_id)          # and :1461-1464 (document_transcript)
    if document is None:
        raise Http404(f"No document {doc_id}.")
```

Plus: a third copy of the same forbidden sentence inlined at `:590-592` (`document_labels_update`,
"Labelling …") beside the two named constants at `:444-451`; and `document_reingest` calling
`principal_for_request(request)` twice — bound at `:1182`, re-derived at `:1192` as
`actor=principal_for_request(request)`.

**C-46 — the raw sidecar read.** `ingest.py:816-818`:

```python
    sidecar_path = path.parent / "extract.json"
    if sidecar_path.exists():
        sidecar = json.loads(sidecar_path.read_text())
```

versus the hardened `sidecar.read_sidecar(path: Path) -> dict | None` (`sidecar.py:42-77`), which
catches `OSError`/`ValueError` and returns `None` for anything that is not a well-formed JSON object.
`_source_documents` runs inside `run_ingest_or_fail`'s broad `except Exception` (`ingest.py:1191-1196`),
so today a corrupt sidecar becomes `doc.status_detail = str(exc)` — a raw Python exception message
where the operator needs a sentence.

- [ ] **Step 1: C-17 — delete the twin**

Delete `tools/rag/views.py:109-124` entirely. `views.py:992` becomes:

```python
    upload_exts = ingest.supported_exts()
```

Read `views.py:105-125` for what else that region imports (`PROSE_EXTS`, `TABULAR_EXTS`, `AV_EXTS`,
`IMAGE_EXTS` may now be unused in `views.py`); delete any import that becomes dead.

**And fix the docstring that will otherwise name a dead function.** `tools/rag/ingest.py:164` ends
`supported_exts`'s docstring by listing its call sites, including *"`tools.rag.views.
supported_upload_exts`'s own upload-form twin"*. After the deletion that sentence names something
that does not exist — precisely the defect class C-47 and C-54 exist to remove, reintroduced by the
task removing them. Rewrite it to name `views.document_upload` as the caller instead.

Repoint `tools/rag/README.md:203-205`, which names the deleted function explicitly:

> `tools/rag/views.py::supported_upload_exts()` — `readers.py`'s `PROSE_EXTS | TABULAR_EXTS`, plus
> `MEDIA_EXTS` while the `"media"` feature flag is on.

becomes the same sentence naming `tools/rag/ingest.py::supported_exts()`.

Add the pin:

```python
def test_the_upload_door_asks_ingest_which_extensions_it_accepts():
    """C-17. `views.supported_upload_exts` was a byte-identical twin of
    `ingest.supported_exts`, and each docstring named the other. One
    function decides what this module can ingest; the upload form asks it."""
    assert not hasattr(rag_views, "supported_upload_exts")
```

- [ ] **Step 2: Run the flag-flip tests under both states**

Run:
```
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py -k Upload tools/rag/tests/test_ingest.py
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q tools/rag/tests/test_views.py -k Upload
```
Expected: PASS under both. This is the one item in this plan whose whole behaviour is a feature flag,
so both states are the real gate.

- [ ] **Step 3: C-32 — one administer gate, one readable gate**

Add beside the two message constants at `views.py:444-451`:

```python
_LABEL_FORBIDDEN_MESSAGE = (
    "Labelling a document is an administrator action on this box, or an "
    "action for an owner of one of the document's entitlements."
)


def _administered_document(request, doc_id, forbidden_message):
    """`(principal, document)` for a handler that administers a document,
    or `(principal, HttpResponseForbidden)` when this principal may not.

    C-32: `document_delete` and `document_reingest` carried this prologue
    verbatim, comment block and all, differing only in which of the three
    forbidden sentences they hand back. The MESSAGE stays a parameter --
    "Deleting", "Re-ingesting" and "Labelling" are what the operator
    reads, and a shared verb would make all three vaguer.

    404 BEFORE 403, unchanged: a document nobody may administer and a
    document that does not exist are the same answer to a stranger, and
    `get_object_or_404` runs first so it stays that way.
    """


def _readable_document_or_404(request, doc_id):
    """`(principal, document)` for a handler that reads a document, or
    raises `Http404`.

    C-32: `document_file` and `document_transcript` carried this prologue
    verbatim including its comment. "Not readable" and "not there" are
    deliberately the same answer -- the module's own 404 house rule.
    """
```

Write both with the exact bodies currently at the four sites, then replace each site with a call.
Preserve the comment blocks at `:1358-1363` and `:1455-1460` by moving them into
`_readable_document_or_404`'s docstring rather than deleting them.

Then, in `document_labels_update`, replace the inlined sentence at `:590-592` with
`HttpResponseForbidden(_LABEL_FORBIDDEN_MESSAGE)`.

And fix the double resolution: `views.py:1192`'s
`actor=principal_for_request(request)` becomes `actor=principal`, which `:1182` already bound.

- [ ] **Step 4: Pin the three refusal sentences**

```python
@pytest.mark.parametrize(("url_name", "verb"), [
    ("rag-document-delete", "Deleting"),
    ("rag-document-reingest", "Re-ingesting"),
    ("rag-document-labels", "Labelling"),
])
def test_each_administer_refusal_names_its_own_verb(client, url_name, verb):
    """C-32. Three handlers share one sentence with three verbs. Sharing
    the prologue must not make them share the verb: the sentence is what
    an operator reads to know what they were refused."""
```

- [ ] **Step 5: C-46 — read the sidecar through the hardened reader**

`ingest.py:816-818` becomes:

```python
    sidecar_path = path.parent / "extract.json"
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
```

Add `from tools.rag.sidecar import read_sidecar` to `ingest.py`'s imports. Check whether `json` is
still used elsewhere in `ingest.py`; if not, delete that import too.

Note the coverage gap the audit found and this step closes: **no existing test asserts today's
`status_detail` for a corrupt sidecar**, so nothing pins the raw-exception wording and nothing has to
be repointed. Add the test that pins the new one:

```python
def test_a_corrupt_extraction_sidecar_fails_the_document_with_a_sentence():
    """C-46. `_source_documents` read the sidecar with a raw
    `json.loads`, so a truncated or non-object file surfaced on the
    library page as a `JSONDecodeError` repr. `sidecar.read_sidecar` is
    the hardened reader this module already ships; the failure it enables
    is a sentence naming the fix."""
    ...
    assert "unreadable or corrupt" in document.status_detail
    assert "JSONDecodeError" not in document.status_detail
```

- [ ] **Step 6: Run the suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests`
Expected: PASS.

- [ ] **Step 7: Full gate, then commit**

```
git add tools/rag/views.py tools/rag/ingest.py tools/rag/README.md tools/rag/tests
git commit -m "refactor(rag): one extension answer, two gate prologues, one hardened sidecar read"
```

Body:

```
C-17 `views.supported_upload_exts` was a byte-identical twin of
`ingest.supported_exts`, with one caller and no test of its own; the
upload door now asks the module that decides. C-32 two pairs of gate
prologues -- administer and readable -- were verbatim copies including
their comments; the refusal SENTENCE stays a parameter, because
"Deleting", "Re-ingesting" and "Labelling" are what the operator reads,
and `document_reingest` stops resolving its principal twice. C-46
`_source_documents` read the extraction sidecar with a raw `json.loads`,
so a corrupt file reached the library page as an exception repr; it now
goes through `sidecar.read_sidecar` and fails with a sentence.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 25: The textless-page scan happens once per upload (C-08)

**Files:**
- Modify: `tools/rag/ingest.py` — `_check_document_pages` (**def at `:312`**; the PDF branch this
  finding is about is at `:380-382`), `stage_document` (`:837-…`), `_enqueue_ingest_job`
  (`:1202-1279`), `enqueue_ingest` (`:1297-1342`), `_needs_vision_extraction` (`:549`).
  **Task 23 inserts ~120 lines into this file before this task runs — re-derive every one of these
  with `git grep -n` (Global Constraint 16).**
- Modify: `docs/adr/0014-media-ingestion.md` §9 ("The scan cost, stated plainly") if it states the
  per-upload cost
- Test: `tools/rag/tests/test_ingest.py`

**Column:** `tools/rag`.

**The finding, with the call chain traced.** `enqueue_ingest` (`:1297-1342`) calls `stage_document`
at `:1338` and `_enqueue_ingest_job` at `:1342`, synchronously, with nothing touching the file
between them.

- `stage_document` at `:908` calls `_check_document_pages(file_path, medium)`, which at `:380-382`
  runs `readers.pdf_textless_pages(path, limit=cap + 1)`.
- `_enqueue_ingest_job` at `:1253` calls `_needs_vision_extraction(stored_path, medium)`, which at
  `:549` runs `readers.pdf_textless_pages(stored_path, limit=1)`.

And `readers.py:142-153` states, in its own words, that **a PDF with no textless pages is scanned in
full at every positive `limit`, `limit=1` included** — because proving "no page lacks a text layer"
means looking at every page. So an ordinary all-text PDF is fully `extract_text()`-scanned **twice**,
in one request, with the `"media"` flag on.

**Gated on the flag.** With `"media"` off, `_check_document_pages` returns at `:387` and
`_needs_vision_extraction` never reaches its PDF branch — there is no double scan to fix. The fix
must therefore be a no-op in that state.

**Why the cap-check's result is sufficient.** `_check_document_pages` scans with `limit=cap + 1`;
`_needs_vision_extraction` asks a boolean at `limit=1`. A non-empty result at `cap + 1` proves
"at least one textless page exists", which is exactly the boolean. So the more expensive scan already
answers the cheaper question.

**The third scan stays.** `run_ingest_for` scans again in the worker process. That one is legitimate
— a different process, later, must not trust a stale snapshot — and is out of scope.

- [ ] **Step 1: Write the failing regression test**

```python
@override_settings(FARABUNKER_FEATURES="vision,media")
def test_an_all_text_pdf_is_scanned_once_during_upload():
    """C-08. `enqueue_ingest` calls `stage_document` then
    `_enqueue_ingest_job` synchronously with nothing between them, and
    each ran a full `pdf_textless_pages` pass -- `readers.py`'s own
    docstring records that an all-text PDF is scanned in FULL at any
    positive limit, `limit=1` included, because proving no page lacks a
    text layer means reading every page. Two full `extract_text()` passes
    over the same bytes, in one request."""
    with patch("tools.rag.readers.pdf_textless_pages", wraps=readers.pdf_textless_pages) as scan:
        ingest.enqueue_ingest(str(pdf_path), None, move=True, actor=principal)
    assert scan.call_count == 1


@override_settings(FARABUNKER_FEATURES="vision")
def test_with_media_off_nothing_changes():
    """The gate. With the flag off neither scan runs at all, and this fix
    must not introduce one."""
    with patch("tools.rag.readers.pdf_textless_pages") as scan:
        ingest.enqueue_ingest(str(pdf_path), None, move=True, actor=principal)
    assert scan.call_count == 0
```

Use `_make_pdf_bytes` for the fixture (after Task 35, from `tools/rag/tests/_helpers.py`).

- [ ] **Step 2: Run and watch the first fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_ingest.py -k scanned_once`
Expected: FAIL with `call_count == 2`.

- [ ] **Step 3: Thread the result through — by out-parameter, not by widening a return**

**Do not change either function's return shape.** `_check_document_pages` returns `None` or a
rejection message, and `TestCheckDocumentPages` compares that return directly at ten sites
(`tools/rag/tests/test_ingest.py:247,248,253,260,267,275,283,296,308,323`).
`stage_document` returns a 2-tuple that is unpacked at three production sites
(`tools/rag/ingest.py:1338`, `:1407`, `tools/rag/jobs.py:716`) and about thirty-eight test sites
across `test_ingest.py`, `test_jobs.py` and `test_upload_placement.py`. Widening either would turn a
one-line performance fix into a fifty-site rewrite, which is not what this finding is worth.

Pass a caller-owned dict instead. `_check_document_pages` writes into it when it runs the scan;
everyone who does not care passes nothing and is entirely unaffected:

```python
def _check_document_pages(path, medium, *, probe_out: dict | None = None):
    """... (existing docstring, plus:)

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
    against a message throughout `TestCheckDocumentPages`, and widening it would
    make a one-line performance fix into a fifty-site test rewrite. A
    caller that passes nothing sees no change at all.

    THE KEY IS ABSENT, NOT `None`, when the branch did not run (not a
    `.pdf`, not prose, or the "media" flag is off). Every reader must
    treat an absent key as "nobody has looked", never as "no textless
    pages" -- the difference between those two is the whole finding.
    """
```

Then thread it: `stage_document` gains the same optional `probe_out` keyword and passes it straight
through (its own return shape is unchanged, so its three production and ~38 test call sites are
untouched); `enqueue_ingest` (`:1338-1342`) owns the dict, passing it to `stage_document` and then
into `_enqueue_ingest_job`; and `_enqueue_ingest_job:1253` passes what it found to
`_needs_vision_extraction`, which gains a keyword:

```python
def _needs_vision_extraction(stored_path, medium, *, textless=None) -> bool:
    """... (existing docstring, plus:)

    `textless` (C-08): the scan result a caller already paid for, or
    `None` for "nobody has looked" -- which is what every caller outside
    the upload path passes, and which preserves this function's original
    behaviour exactly. `run_ingest_for`'s own later call deliberately
    passes `None`: it runs in the worker process, at a later time, and
    must not trust a snapshot taken in the web process.
    """
    if textless is not None:
        return bool(textless)
    ...  # the existing body, unchanged
```

- [ ] **Step 4: Run the ingest suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_ingest.py`
Then `FARABUNKER_FEATURES='vision'`.
Expected: PASS under both, **with no test edited**. That is the out-parameter's whole point: watch
`TestCheckDocumentPages` (`:238-386`), whose ten direct-return assertions compare
`_check_document_pages`'s return directly against `None` or a message
(`:247,248,253,260,267,275,283,296,308,323`, with five more call sites at `:350,367,375,385,386`) and which a widened return would have
broken one by one. `test_ingest.py:337`'s `mock_textless.assert_called_once_with(...)` pins the
single scan *inside* `_check_document_pages` and must stay green and unmodified. (`:234`'s
identically-shaped assertion belongs to the `_needs_vision_extraction` test *above*
`TestCheckDocumentPages`, which starts at `:238` — it is a different call site, and this change must
leave it green too.) `TestEnqueueIngest` (`:1806+`,
including `test_scanned_pdf_enqueues_with_pdf_scanned_medium` at `:1849`,
`test_mixed_pdf_enqueues_with_pdf_scanned_medium` at `:1863`, and
`test_text_layer_pdf_enqueues_with_plain_prose_medium` at `:1879`) exercises the full chain and
asserts the resulting `payload["medium"]` — the three medium outcomes must be identical after the
change.

- [ ] **Step 5: Update the ADR if it states the cost**

`docs/adr/0014-media-ingestion.md` §9 ("The scan cost, stated plainly") is quoted in
`readers.py:142-153`. If it says an upload costs two scans, correct it to one plus the worker's own.
If it says one, it was already describing the intent and no edit is needed — record which in the
commit body. **No model or family names** (Global Constraint 9).

- [ ] **Step 6: Full gate, then commit**

```
git add tools/rag/ingest.py docs/adr/0014-media-ingestion.md tools/rag/tests/test_ingest.py
git commit -m "fix(rag): an upload scans a PDF for textless pages once, not twice"
```

Body:

```
C-08. `enqueue_ingest` calls `stage_document` then `_enqueue_ingest_job`
synchronously with nothing between them, and each ran a full
`pdf_textless_pages` pass -- and `readers.py`'s own docstring records that
an all-text PDF is scanned in FULL at any positive limit, `limit=1`
included. The cap check's `limit=cap + 1` result already answers the
vision-routing question, so it is threaded forward instead of re-derived.
`None` still means "nobody has looked", which is what every other caller
passes -- `run_ingest_for`'s later scan in the worker process deliberately
included, since it must not trust a web-process snapshot.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 26: `store.py` owns the `extract.json` and `work/` sub-paths (C-18)

**Files:**
- Modify: `tools/rag/store.py` (two new helpers)
- Modify: `tools/rag/ingest.py:816`, `:1197`; `tools/rag/media.py:725`, `:734`, `:904`, `:913`;
  `tools/rag/models.py:357`; `tools/rag/views.py:1465`
- Modify: `tools/rag/README.md:25-33` and `:677-678` (the two prose mentions of the sidecar filename)
- Test: `tools/rag/tests/test_store.py`

**Column:** `tools/rag`.

**The finding.** Two sub-paths under a document's store directory are re-spelled as literals at eight
production sites:

| Literal | Sites |
|---|---|
| `"extract.json"` | `ingest.py:816`, `media.py:725`, `media.py:904`, `models.py:357`, `views.py:1465` |
| `/ "work"` | `ingest.py:1197`, `media.py:734`, `media.py:913` |

`tools/rag/store.py` (82 lines) already owns the directory itself and exposes exactly four functions:
`document_dir(doc_id)`, `store_file`, `move_file`, `remove_document_files`. It has no sidecar or work
helper.

In both `media.py` pairs, `doc_dir = store.document_dir(doc.id)` is bound two lines above
(`:723-724`, `:902-903`), so the substitution is exact. At `ingest.py:816` the base is `path.parent`,
where `path` is the stored path — i.e. `store.document_dir(doc.id) / <basename>` — so `path.parent`
*is* `document_dir(doc.id)`, and `doc` is already that function's first argument.

**Test sites are out of scope.** `git grep -c` finds 46 `"extract.json"` and 9 `/ "work"` references
across six test modules. Repointing them all would triple this task's diff for no behaviour change.
Leave them; a test naming the on-disk filename it is asserting about is documenting the layout, which
is a legitimate thing for a test to do.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_sidecar_path_is_under_the_documents_own_directory():
    """C-18. `"extract.json"` was written out at five production sites
    and `/ "work"` at three, all of them under a directory `store.py`
    already owns. The store decides its own layout."""
    assert store.sidecar_path(7) == store.document_dir(7) / "extract.json"


def test_the_work_dir_is_under_the_documents_own_directory():
    assert store.work_dir(7) == store.document_dir(7) / "work"
```

- [ ] **Step 2: Run and watch both fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_store.py -k "sidecar_path or work_dir"`
Expected: FAIL — no such attributes.

- [ ] **Step 3: Add the two helpers**

In `tools/rag/store.py`, beside `document_dir`:

```python
def sidecar_path(doc_id: int) -> Path:
    """The extraction sidecar for `doc_id` (C-18).

    The filename was written out at five production sites across four
    modules, each independently spelling a layout decision this module
    owns. `tools.rag.sidecar` reads and writes the file's CONTENT; this
    says where it is.
    """
    return document_dir(doc_id) / "extract.json"


def work_dir(doc_id: int) -> Path:
    """The scratch directory a media driver uses while extracting
    `doc_id`, and deletes when it is done (C-18).

    Three sites spelled `/ "work"` by hand. Same reasoning as
    `sidecar_path`: the store owns its own layout.
    """
    return document_dir(doc_id) / "work"
```

- [ ] **Step 4: Repoint the eight production sites**

| Site | Becomes |
|---|---|
| `ingest.py:816` | `sidecar_path = store.sidecar_path(doc.id)` — **Task 24 has already rewritten this region for C-46**, so the target is the `sidecar_path = …` assignment that now sits above a `read_sidecar(sidecar_path)` call, not a `json.loads`. Find it with `git grep -n 'extract.json' -- tools/rag/ingest.py`. |
| `ingest.py:1197` | `work_dir = store.work_dir(doc.id)` |
| `media.py:725` | `sidecar_path = store.sidecar_path(doc.id)` |
| `media.py:734` | `work_dir = store.work_dir(doc.id)` |
| `media.py:904` | `sidecar_path = store.sidecar_path(doc.id)` |
| `media.py:913` | `work_dir = store.work_dir(doc.id)` |
| `models.py:357` | `return store.sidecar_path(self.id).is_file()` |
| `views.py:1465` | `sidecar_path = store.sidecar_path(document.id)` |

At `ingest.py:816` confirm `doc` is in scope in `_source_documents` (it is the first argument). At
`media.py`, the `doc_dir` local bound two lines above each site may become unused once both its
consumers are repointed — delete it if so.

Add the pin that keeps them from coming back:

```python
@pytest.mark.parametrize("module", ["ingest", "media", "models", "views"])
def test_no_production_module_spells_the_store_layout_by_hand(module):
    """C-18. Eight production sites re-spelled a path the store owns.
    Test modules are exempt: a test naming the file it asserts about is
    documenting the layout, which is a legitimate thing for a test to do."""
    text = (Path(settings.BASE_DIR) / f"tools/rag/{module}.py").read_text()
    assert '"extract.json"' not in text
    assert '/ "work"' not in text
```

- [ ] **Step 5: Run the rag suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests`
Expected: PASS. The 46 test-side literals still resolve to the same paths, so nothing there moves.

- [ ] **Step 6: Update the README**

`tools/rag/README.md:25-33` (the ingest pipeline diagram) and `:677-678` (the Status section) both
name `extract.json`. Keep the filename in the prose — it is what an operator sees on disk — and add
one clause naming `store.sidecar_path`/`store.work_dir` as where the layout is decided.

- [ ] **Step 7: Full gate, then commit**

```
git add tools/rag/store.py tools/rag/ingest.py tools/rag/media.py tools/rag/models.py \
        tools/rag/views.py tools/rag/README.md tools/rag/tests/test_store.py
git commit -m "refactor(rag): the store owns the sidecar and work-dir sub-paths"
```

Body:

```
C-18. `"extract.json"` was spelled out at five production sites and
`/ "work"` at three, across four modules, each independently restating a
layout `store.py` already owns the root of. Two helpers, eight call sites
repointed, and a pin. The forty-six test-side references stay: a test
naming the file it asserts about is documenting the layout, not
duplicating a decision.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 27: `_upload_sha256` gives way to `foundation.files.sha256_file` (C-25)

**Files:**
- Delete: `tools/rag/views.py:758-769`
- Modify: `tools/rag/views.py` (import) and the call site — which, after Task 23, is inside
  `ingest.stage_and_enqueue_one` rather than at `views.py:1038`
- Modify: `foundation/files.py:19,24` (two comments naming the deleted function)
- Modify: `tools/rag/tests/test_views.py:45,2943,2948,2969,2971`
- Test: `tools/rag/tests/test_views.py`

**Column:** `foundation.files` is a pure leaf under rule 1 — universally importable. No boundary
question.

**The finding.** `views.py:758-769`:

```python
def _upload_sha256(path: Path) -> str:
    """SHA-256 of `path`'s current bytes, streamed in 1 MiB chunks -- W1
    review MAJOR 1's "unchanged re-upload" check. Mirrors `tools.rag.
    ingest._sha256` exactly (same algorithm, same chunk size) so a hash
    computed here is directly comparable to `Document.file_hash`, without
    `document_upload` reaching into that module's own private helper from
    outside its file."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
```

`foundation/files.py:30-45`'s `sha256_file` is the same algorithm at the same 1 MiB block size, and
`tools/rag/ingest.py:407` is already the sanctioned one-line alias to it (`_sha256 = sha256_file`).
The docstring's stated reason — "without reaching into that module's private helper" — is answered by
importing the *original* rather than either module's copy.

**Every reference to repoint, verified in the worktree:**

| Path:line | Verbatim | Action |
|---|---|---|
| `tools/rag/views.py:758-769` | the definition | delete |
| `tools/rag/views.py:1038` | `dest_unchanged = existing is not None and existing.file_hash == _upload_sha256(dest)` | after Task 23 this line lives in `ingest.stage_and_enqueue_one`; repoint it there |
| `tools/rag/tests/test_views.py:45` | `from tools.rag.views import RANGE_UNSATISFIABLE, _parse_range, _upload_sha256` | drop the third name |
| `tools/rag/tests/test_views.py:2943` | docstring prose | reword |
| `tools/rag/tests/test_views.py:2948` | docstring prose | reword |
| `tools/rag/tests/test_views.py:2969` | `            return _upload_sha256(path)` | repoint |
| `tools/rag/tests/test_views.py:2971` | `        with patch("tools.rag.views._upload_sha256", side_effect=_race_then_hash):` | repoint the patch target |
| `foundation/files.py:19`, `:24` | comments naming `tools/rag/views.py::_upload_sha256` | reword |

**The one subtlety.** `test_watcher_race_during_the_unchanged_hash_read_is_counted_as_queued`
(`test_views.py:2939-2979`) patches the function *by dotted path* and, inside its `side_effect`,
calls the module-level name imported at line 45 — which is bound at import time and therefore still
refers to the real function even while the module attribute is patched. Both halves must be
repointed together, and after Task 23 the patch target moves module as well as name.

- [ ] **Step 1: Confirm Task 23 has landed**

The call site this task repoints is inside `ingest.stage_and_enqueue_one`. If Task 23 has not run,
do it first — repointing `views.py:1038` and then moving it again in Task 23 is two edits to one line.

- [ ] **Step 2: Repoint the test module first**

`tools/rag/tests/test_views.py:45`:
```python
from tools.rag.views import RANGE_UNSATISFIABLE, _parse_range, _upload_sha256
```
becomes
```python
from foundation.files import sha256_file
from tools.rag.views import RANGE_UNSATISFIABLE, _parse_range
```

`:2969` becomes `            return sha256_file(path)`.
`:2971` becomes `        with patch("tools.rag.ingest.sha256_file", side_effect=_race_then_hash):`
— the module the hash is now called *from*, since patching `foundation.files.sha256_file` would also
affect `ingest._sha256` and the backup/restore paths.

Reword the two docstring lines at `:2943` and `:2948` to name `sha256_file` and the module it is
patched in.

- [ ] **Step 3: Run that one test and watch it fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py -k watcher_race_during_the_unchanged_hash_read`
Expected: FAIL — `ingest` does not import `sha256_file` under that name yet.

- [ ] **Step 4: Make `ingest` use the shared original**

`tools/rag/ingest.py` already has `_sha256 = sha256_file` at `:407` and imports `sha256_file` above
it. In `stage_and_enqueue_one`, the unchanged-comparison calls `sha256_file(dest)` directly (not
`_sha256`), so the patch target in Step 2 resolves.

Delete `tools/rag/views.py:758-769`. Check whether `hashlib` is still used in `views.py`; delete the
import if not.

- [ ] **Step 5: Run the test green**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py`
Expected: PASS.

- [ ] **Step 6: Fix the two stale comments in `foundation/files.py`**

`:19` and `:24` name `tools/rag/views.py::_upload_sha256` as a caller that must not diverge on block
size. That caller is gone. Reword to name `tools.rag.ingest._sha256` (the alias that remains) and the
upload path, and keep the block-size warning, which is the point of the comments.

- [ ] **Step 7: Add the pin**

```python
def test_the_rag_views_module_has_no_private_hash_helper():
    """C-25. `_upload_sha256` re-implemented `foundation.files.sha256_file`
    -- same algorithm, same 1 MiB block -- and justified itself by not
    wanting to reach into `ingest`'s private helper. Importing the
    ORIGINAL answers that without a third copy; `ingest._sha256` has been
    a one-line alias to it all along."""
    assert not hasattr(rag_views, "_upload_sha256")
```

- [ ] **Step 8: Full gate, then commit**

```
git add tools/rag/views.py tools/rag/ingest.py foundation/files.py tools/rag/tests/test_views.py
git commit -m "cleanup(rag): the upload hash goes through foundation.files.sha256_file"
```

Body:

```
C-25. `_upload_sha256` re-implemented `foundation.files.sha256_file` --
same algorithm, same 1 MiB block -- and its docstring justified the copy
by not wanting to reach into `ingest`'s private helper, which importing
the shared original answers without a third copy. `ingest._sha256` has
been a one-line alias to that original all along. Five test references
repointed, including the watcher-race test's patch target, and the two
comments in `foundation/files.py` that named the deleted function.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 28: `media.py`, `ingest.py` and `transcode.py` small same-file repeats (C-31, media half)

**Files:**
- Modify: `tools/rag/media.py:723-735`, `:794-806`, `:902-914`, `:989-1005`
- Modify: `tools/rag/ingest.py:1193-1196`, `:1276-1278`
- Modify: `tools/rag/transcode.py:133-145`, `:186-197`, `:234-243`
- Test: `tools/rag/tests/test_media.py`, `tools/rag/tests/test_ingest.py`,
  `tools/rag/tests/test_transcode.py`

**Column:** `tools/rag`, three same-file extractions, no cross-module movement.

**Three independent items. Any one of them can be dropped without touching the other two.**

**(a) The media-driver header and footer.** `transcribe_to_sidecar` (`:723-806`) and
`extract_to_sidecar` (`:902-1005`) open identically:

```python
    stored_path = Path(doc.source_path)
    doc_dir = store.document_dir(doc.id)
    sidecar_path = doc_dir / "extract.json"     # store.sidecar_path(doc.id) after Task 26
```

and close identically in shape — `produced_at` → build payload → `_write_sidecar_atomic` →
`_stamp_extraction` → `shutil.rmtree(work_dir, ignore_errors=True)` → `return sidecar` — differing
only in which payload builder is called (`_sidecar_payload` vs `_extraction_sidecar_payload`, the
latter taking an extra `rasterized_pages` kwarg) and the `method=` string
(`"transcription"` vs `"extraction"`).

Extract:

```python
def _driver_paths(doc):
    """`(stored_path, sidecar_path, work_dir)` for a media driver (C-31).
    Both drivers opened with these same three lines."""


def _finish_driver(doc, sidecar, *, method, resolved, produced_at, sidecar_path, work_dir):
    """Write the sidecar atomically, stamp the extraction, drop the work
    directory, and hand the sidecar back (C-31).

    The PAYLOAD is built by the caller -- `_sidecar_payload` and
    `_extraction_sidecar_payload` take different arguments and the second
    carries a `rasterized_pages` count the first has no notion of. What
    is shared is the five lines AFTER the payload exists.
    """
```

**(b) "Mark FAILED" in `ingest.py`.** `:1193-1196` and `:1276-1278` write the identical three lines
with an identical `update_fields` list:

```python
    doc.status = Document.Status.FAILED
    doc.status_detail = <detail>
    doc.save(update_fields=["status", "status_detail", "updated_at"])
```

Extract `_fail_document(doc, detail)` in `ingest.py`. Note the difference from Task 20's
`_fail_stranded_rows`: that one is a conditional queryset `.update()` guarding against a live worker;
this one is an unconditional single-row `.save()` on a row the caller already holds. **They are not
the same helper and must not be merged** — say so in both docstrings.

**(c) The subprocess wrapper in `transcode.py`.** Three functions — `probe_duration` (`:133-145`),
`extract_audio` (`:186-197`), `slice_audio` (`:234-243`) — each run:

```python
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=<TIMEOUT>)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"<tool> timed out after {<TIMEOUT>}s <doing something to> {<path>}") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"<tool> failed <doing something to> {<path>} (exit {result.returncode}): "
            f"{result.stderr.strip()}"
        )
```

Extract:

```python
def _run_tool(argv, *, timeout: float, tool: str, doing: str) -> subprocess.CompletedProcess:
    """Run `argv`, or raise `RuntimeError` naming what failed (C-31).

    `tool` and `doing` are the caller's own words -- "ffprobe" /
    "probing the duration of /x.mp4", "ffmpeg" / "slicing /y.wav". An
    operator reading a failed ingest needs to know which tool was doing
    what to which file; a generic "subprocess failed" would be a
    regression wearing a de-duplication's clothes.
    """
```

- [ ] **Step 1: Pin the failure messages before extracting**

The only thing an operator sees from any of these is the message. In
`tools/rag/tests/test_transcode.py`:

```python
@pytest.mark.parametrize(("call", "tool", "doing"), [
    ("probe_duration", "ffprobe", "probing the duration of"),
    ("extract_audio", "ffmpeg", "extracting audio from"),
    ("slice_audio", "ffmpeg", "slicing"),
])
def test_each_tool_failure_names_the_tool_the_action_and_the_file(call, tool, doing):
    """C-31. Three functions share a subprocess wrapper and MUST NOT
    share its words: "ffprobe failed probing the duration of /x.mp4" and
    "ffmpeg failed slicing /y.wav" send an operator to different places."""


@pytest.mark.parametrize(("call", "tool"), [...])
def test_each_timeout_names_its_own_timeout_constant(call, tool):
    """`PROBE_TIMEOUT_SECONDS` and `EXTRACT_TIMEOUT_SECONDS` are
    different numbers and the message says which one elapsed."""
```

Do the same for (a) and (b): pin what an operator sees on a failed extraction and on a failed
transcription, and pin that a stamped sidecar carries `method="transcription"` versus
`method="extraction"`.

- [ ] **Step 2: Run them and watch them pass** — characterization; green before and after.

- [ ] **Step 3: Extract (c), the smallest and most mechanical, first**

Rewrite the three `transcode.py` sites over `_run_tool`. Each keeps its own `argv` construction, its
own timeout constant, and its own `tool`/`doing` words.

- [ ] **Step 4: Run `test_transcode.py`** — Expected: PASS, no assertion changes.

- [ ] **Step 5: Extract (b)**

`_fail_document(doc, detail)` in `ingest.py`, used at both sites.

- [ ] **Step 6: Run `test_ingest.py`** — Expected: PASS.

- [ ] **Step 7: Extract (a)**

`_driver_paths` and `_finish_driver` in `media.py`, used by both drivers. This is the largest of the
three and the one most likely to reveal an asymmetry the audit did not see — read both functions end
to end before starting, and if the footers turn out to diverge in more than the payload builder,
extract only `_driver_paths` and say so in the commit body.

- [ ] **Step 8: Run `test_media.py` and the whole rag suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests`
Expected: PASS.

- [ ] **Step 9: Full gate, then commit**

```
git add tools/rag/media.py tools/rag/ingest.py tools/rag/transcode.py tools/rag/tests
git commit -m "refactor(rag): the media drivers' shared header and footer, one failure writer, one tool runner"
```

Body:

```
C-31, the media half. Three same-file repeats, each written once: the two
media drivers' identical three-line opening and five-line closing; the
unconditional "mark this row FAILED" in `ingest.py`; and the
`subprocess.run` + timeout + returncode ladder in `transcode.py`'s three
tool calls. Every operator-facing message stays a parameter -- "ffprobe
failed probing the duration of" and "ffmpeg failed slicing" send someone
to different places, and a generic wrapper message would be a regression
wearing a de-duplication's clothes. `ingest._fail_document` is deliberately
NOT the same helper as `jobs._fail_stranded_rows`: one holds a row, the
other guards a race.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

> **WP4 preamble — read before Tasks 29–33.**
>
> These five tasks are **additive to the existing inline shell**. Nothing here adds a static asset,
> a build step or a second `<style>` mechanism (Global Constraint 8). The standing ruling — *"No
> static JS/CSS pipeline (`collectstatic`, `static/chat.css`) — the single inline
> `<style>`/`<script>` shell stays"* — is not being revisited; promoting a rule up the template
> inheritance chain is exactly what that ruling endorses.
>
> **The placement rule**, quoted from `foundation/ops/tests/test_css_ownership.py`'s own docstring:
> *"a selector's home is the deepest template that is an ancestor of every template that uses it."*
> Every promotion below resolves to `foundation/templates/_shell.html`, and the verified extends
> graph is why: `chat/base.html`, `jobs/base.html`, `rag/base.html`, `vision/base.html` and
> `identity/login.html` descend from **bare `_shell.html`** and structurally cannot reach
> `_settings.html`. `_shell.html` is the only common ancestor.
>
> **Held files** (Global Constraint 10): `agents/chat/templates/chat/conversation.html` and
> `_turn_card.html` are **not** touched by any WP4 task — verified against every site list below.
> `agents/chat/templates/chat/base.html` and `_messages.html` **are** in scope and are not held.
>
> **Gated on #84** (Global Constraint 11): `agents/chat/templates/chat/base.html:1531-1533`
> (`.delete-disclosure`) stays exactly as it is. Task 32 edits `base.html:1476-1482` and nothing else
> in that file beyond Task 30's `:1556`.
>
> **Explicitly out of scope:** the digest rows the consolidated audit lists beside WP4 —
> RESIDUALS #3, B3 (the small-caps recipe) and B4 (the ellipsis idiom). Neither the consolidated
> audit nor `audit-frontend.md` carries a site list, a snippet or a line citation for B3 or B4; they
> exist only as labels inherited from a prior digest. **A task cannot be written from a label.** They
> are named in "Not planned", at the foot of this document, with what would be needed to plan them.

### Task 29: `--error-bg`, `--error-text`, `--danger-text` and `--mono` become shell tokens (C-20)

**Files:**
- Modify: `foundation/templates/_shell.html` — the `:root` block (`:89-…`) and the
  `@media (prefers-color-scheme: dark)` block (`:150-162`)
- Modify: `models/queue/templates/jobs/queue.html:41-47`
- Modify: `tools/rag/templates/rag/ask.html:18-24`
- Modify: `tools/rag/templates/rag/documents.html:33-45`, `:220-223`
- Modify: `tools/rag/templates/rag/search.html:14-21`
- Modify: `tools/rag/templates/rag/settings.html:34-40`
- Modify: `models/registry/templates/inference/console.html:88,92,98,138-140,162`
- Test: `foundation/ops/tests/test_css_ownership.py` (a new token pin)

**The precedent to repeat, verbatim** — `_shell.html:97-108`, which already did this once for
`--danger`:

```
    --accent: #2b5fd9;
    --accent-text: #ffffff;
    {% comment %}
    Status colours, owned here with every other token so a page never
    ships a hex the dark scheme has no override for. `--danger` was five
    copies of #b3261e across two vision templates and the setup page,
    none of them dark-aware. A Django-level comment tag, not a CSS /* */
    one: the latter is plain text inside this inline <style> block and
    would render into the page, making the hex it's explaining appear a
    second time. This form is stripped at template-render time, so the
    note documents the source without leaking into rendered CSS.
    {% endcomment %}
    --danger: #b3261e;
```

with the dark override at `:157` (`--danger: #e05c53;`).

**What is duplicated, verified site by site.** All five `--error-bg`/`--error-text` pairs declare the
identical light values (`#fdeaea` / `#922020`) and the identical dark values (`#3a1f1f` / `#ffb4b4`):
`jobs/queue.html:41-47`, `rag/ask.html:18-24`, `rag/documents.html:34-45`, `rag/search.html:14-21`,
`rag/settings.html:34-40`. `--danger-text` is declared twice (`console.html:88,98`;
`documents.html:33,43`), `--mono` three times identically (`console.html:92`, `documents.html:39`,
`search.html:16`), and `console.html` never tokenised it at all — `:138-140` and `:162` carry the raw
`#fdeaea` / `#922020`.

**The stale comment.** `documents.html:220-223` defends the copy:

> `.banner`, mirroring `agents/chat/templates/chat/base.html`'s own rule of the same name … not
> shared across the two template hierarchies, exactly as `--danger`/`--danger-text` are not, because
> this page's `<style>` block is its own.

`--danger` **is** shared today (`_shell.html:108`, `:157`). The premise is stale; rewrite the comment
rather than deleting it — the `.banner` half of what it says is Task 32's business.

- [ ] **Step 1: Write the failing token pin**

In `foundation/ops/tests/test_css_ownership.py`, beside the existing gates:

```python
_SHARED_TOKENS = ("--error-bg", "--error-text", "--danger-text", "--mono")


@pytest.mark.parametrize("token", _SHARED_TOKENS)
def test_a_shared_token_is_declared_only_in_the_shell(token):
    """C-20. Four tokens were retyped per page -- `--error-bg`/
    `--error-text` in five templates with identical light AND dark values,
    `--mono` in three, `--danger-text` in two -- and `console.html`
    hardcoded the error hexes rather than tokenising them at all. This is
    exactly what `_shell.html` already did once for `--danger`; a token
    declared per page is a token whose dark override a page can forget."""
    declarations = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        if "/.claude/" in str(path):
            continue
        for line in path.read_text().splitlines():
            if line.strip().startswith(token + ":"):
                declarations.append(str(path.relative_to(REPO_ROOT)))
    assert set(declarations) == {"foundation/templates/_shell.html"}, (
        f"{token} is declared outside the shell: {sorted(set(declarations))}")


@pytest.mark.parametrize("hex_literal", ["#fdeaea", "#922020"])
def test_the_error_hexes_appear_only_where_the_token_is_declared(hex_literal):
    """The other half of C-20: `console.html` never tokenised these, so
    its error rows had no dark-scheme override at all."""
```

Note the shell declares each token **twice** — once light, once dark — so the assertion is on the
set of *files*, not the count of lines.

- [ ] **Step 2: Run and watch it fail**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_css_ownership.py -k "shared_token or error_hexes"`
Expected: FAIL, listing the five/three/two files per token and both `console.html` hex sites.

- [ ] **Step 3: Declare the four tokens in the shell**

In `_shell.html`'s `:root` block, immediately after `--danger: #b3261e;` (`:108`):

```
    {% comment %}
    THE ERROR PAIR AND THE MONO STACK, owned here for the same reason
    `--danger` above is (C-20). `--error-bg`/`--error-text` were declared
    in FIVE leaf templates with byte-identical light and dark values;
    `--mono` in three; `--danger-text` in two; and `inference/console.
    html` never tokenised the error pair at all, so its error rows shipped
    a light hex with no dark override -- the exact failure the `--danger`
    note above describes, one token later.
    {% endcomment %}
    --error-bg: #fdeaea;
    --error-text: #922020;
    --danger-text: #ffffff;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
```

and in the dark block (`:150-162`), beside `--danger: #e05c53;`:

```
      --error-bg: #3a1f1f;
      --error-text: #ffb4b4;
      --danger-text: #1a0505;
```

(`--mono` has no dark variant at any of its three sites.)

- [ ] **Step 4: Delete the per-page declarations**

Delete only the four token lines at each site, leaving every other token in the same `:root` block
alone — several pages declare page-specific tokens (`--shelf-hover`, `--shelf-active-bg`,
`--shelf-count-bg`, `--page-max-width`) in the same block, and those stay:

| File | Delete |
|---|---|
| `models/queue/templates/jobs/queue.html` | `--error-bg`/`--error-text` in both light and dark blocks (`:41-47`) |
| `tools/rag/templates/rag/ask.html` | same (`:18-24`) |
| `tools/rag/templates/rag/documents.html` | `--danger-text` (`:33`, `:43`), `--error-bg`/`--error-text` (`:34-35`, `:44-45`), `--mono` (`:39`) |
| `tools/rag/templates/rag/search.html` | `--error-bg`/`--error-text` (`:14-15`, `:20-21`), `--mono` (`:16`) |
| `tools/rag/templates/rag/settings.html` | `--error-bg`/`--error-text` in both blocks (`:34-40`) |
| `models/registry/templates/inference/console.html` | `--danger-text` (`:88`, `:98`), `--mono` (`:92`) |

If deleting a page's last token leaves an empty `:root { }` or an empty
`@media (prefers-color-scheme: dark)` block, delete the empty block too.

- [ ] **Step 5: Tokenise `console.html`'s two hardcoded rules**

`:138-140`:
```css
  .msg.error {
    background: #fdeaea;
    color: #922020;
  }
```
becomes
```css
  .msg.error {
    background: var(--error-bg);
    color: var(--error-text);
  }
```

`:162`:
```css
  .badge.unhealthy { background: #fdeaea; color: #922020; }
```
becomes
```css
  .badge.unhealthy { background: var(--error-bg); color: var(--error-text); }
```

**This is a deliberate pixel change in dark mode** — those two rules had no dark override and now
get one. That is the fix, not a side effect. Say so in the commit body.

- [ ] **Step 6: Correct the stale comment**

`documents.html:220-223` — rewrite the clause *"exactly as `--danger`/`--danger-text` are not"*. Both
are shared now. Keep the `.banner` sentence for Task 32 to deal with; correct only the token premise.

- [ ] **Step 7: Run the pins and the template suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests models/registry/tests models/queue/tests tools/rag/tests/test_views.py`
Expected: PASS.

- [ ] **Step 8: Look at the pages**

Load `/rag/documents/`, `/rag/search/`, `/rag/ask/`, `/rag/settings/`, `/queue/` and
`/inference/console/` in the branch preview, in **both** colour schemes, with a flash message
showing. This is a CSS change: a green suite proves the tokens moved, not that the pages look right.

- [ ] **Step 9: Full gate, then commit**

```
git add foundation/templates/_shell.html models/queue/templates/jobs/queue.html \
        models/registry/templates/inference/console.html tools/rag/templates/rag/*.html \
        foundation/ops/tests/test_css_ownership.py
git commit -m "refactor(css): the error pair, --danger-text and --mono become shell tokens"
```

Body:

```
C-20. `--error-bg`/`--error-text` were declared in five leaf templates
with byte-identical light and dark values, `--mono` in three,
`--danger-text` in two, and `inference/console.html` never tokenised the
error pair at all -- its error rows and unhealthy badges shipped a light
hex with no dark override. Exactly what `_shell.html` already did once for
`--danger`, and its own comment there says why. `console.html` gains a
dark scheme for those two rules, which is the fix rather than a side
effect. The comment in `documents.html` defending the copy "exactly as
`--danger` is not shared" is corrected: it has been shared for some time.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 30: `.messages` and `.msg` move to `_shell.html` (C-21, C-52)

**Files:**
- Modify: `foundation/templates/_shell.html` (the `<style>` block, before `{% block extra_style %}`
  at `:563`)
- Modify: `foundation/templates/_settings.html:92-101` (delete what the shell now owns)
- Modify: `agents/chat/templates/chat/base.html:1556-1558`
- Modify: `models/queue/templates/jobs/queue.html:62-65`
- Modify: `tools/rag/templates/rag/documents.html:201-217`
- Modify: `tools/rag/templates/rag/search.html:56-67`
- Modify: `tools/rag/templates/rag/settings.html:47-50`
- Modify: `tools/vision/templates/vision/gallery.html:11`
- Modify: `models/registry/templates/inference/console.html:124-141`
- Test: `foundation/ops/tests/test_css_ownership.py`

**Why the shell and not `_settings.html`.** `_settings.html:92` is where a prior pass put
`.messages`, and it was the right answer for the ten settings-area pages it could see. But
`chat/base.html`, `jobs/base.html`, `rag/base.html` and `vision/base.html` all extend **bare
`_shell.html`** — none of them can reach `_settings.html` through inheritance, which is why each
carries its own copy. The deepest common ancestor of all fourteen consumers is `_shell.html`.

**What moves and what does not.**

| Rule | Move to `_shell.html`? | Why |
|---|---|---|
| `.messages { list-style: none; margin: 0 0 1rem; padding: 0; }` | **Yes** | Byte-identical at six sites (`_settings.html:92`, `chat/base.html:1556`, `jobs/queue.html:62-65`, `rag/documents.html:201-204`, `vision/gallery.html:11`, `console.html:124-128`) |
| `.msg { padding: 0.6rem 0.9rem; border-radius: 6px; margin-bottom: 0.4rem; font-size: 0.9rem; background: var(--panel); border: 1px solid var(--border); color: var(--text); }` | **Yes** | `_settings.html:93-101`'s spelling, which `chat/base.html:1557-1558` matches modulo `color` and `model_sets.html` matched before Task 3 deleted it |
| `.msg.error` | **No** | `_settings.html:78-86` records the ruling explicitly: eight of ten pages carry it and they *disagree* — `rag/settings.html` tints the row, the identity pages recolour border and text, and two define none. Promoting one spelling would restyle the others. **Do not revisit this.** |
| `.msg` variants that genuinely differ | **No** | `console.html:128-135` (`--ok-bg`/`--ok-text`), `rag/documents.html:206-212` (`--shelf-active-bg`), `rag/search.html:56-62` (`margin-top`, own padding, the `var(--shelf-active-bg, var(--panel))` fallback) each stay as a local override after `{{ block.super }}` |

**C-52 folds in here.** The three rag pages' `.msg`/`.msg.error` blocks (`documents.html:206-217`,
`search.html:56-67`, `settings.html:47-50`) are the same shape as the rest: the shared `.msg` recipe
goes, each page's genuine variant stays. `search.html:60`'s defensive
`background: var(--shelf-active-bg, var(--panel))` — a fallback for a custom property only
`documents.html` defines — stays exactly as written; it is a page-specific rule, and the fallback is
what makes it correct on a page that does not define the variable.

- [ ] **Step 1: Write the failing pin**

```python
@pytest.mark.parametrize("selector", [".messages", ".msg"])
def test_the_shared_flash_recipe_lives_in_the_shell(selector):
    """C-21/C-52. `.messages`'s list-reset was restated at six sites and
    `.msg`'s recipe at several more. `_settings.html` held the canonical
    copy, but `chat/base.html`, `jobs/base.html`, `rag/base.html` and
    `vision/base.html` extend BARE `_shell.html` and cannot reach it --
    which is exactly why each grew its own. The deepest common ancestor
    is the shell.

    Compares whole rules, selector AND body, the way this module's own
    `test_no_settings_page_retypes_a_rule_settings_html_already_owns`
    does: two pages using one class name for unrelated rules is not
    duplication, and a page's genuine variant (`console.html`'s
    `--ok-bg` `.msg`, `search.html`'s own padding) is not a copy."""
    shell_rules = _rules(_style_block((REPO_ROOT / "foundation/templates/_shell.html").read_text()))
    shared = {body for sel, body in shell_rules if sel == selector}
    assert shared, f"{selector} is not owned by the shell"
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        if path.name == "_shell.html" or "/.claude/" in str(path):
            continue
        for sel, body in _rules(_style_block(path.read_text())):
            if sel == selector and body in shared:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"{selector}'s shared rule is retyped in: {offenders}"
```

`_rules` and `_style_block` already exist in that module; reuse them, do not write new parsers.
`_style_block` reads only an `extra_style`/`chat_style` block, so check whether it needs widening to
see `vision/base.html`'s nested `vision_style` block — if it does, that widening is Task 33's, and
this pin should be written to skip what it cannot parse and Task 33 should extend it.

- [ ] **Step 2: Run and watch it fail** — listing the six/several sites.

- [ ] **Step 3: Add both rules to `_shell.html`**

In the `<style>` block, immediately before `{% block extra_style %}` at `:563`:

```
  {% comment %}
  THE FLASH RECIPE, owned at the deepest common ancestor (C-21/C-52).
  `.messages`'s list-reset was written out at six sites and `.msg`'s
  recipe at several more. `_settings.html` held the canonical copy from
  an earlier pass, and that was right for the ten settings-area pages it
  could see -- but `chat/base.html`, `jobs/base.html`, `rag/base.html`
  and `vision/base.html` extend THIS file directly and cannot reach
  `_settings.html` through inheritance, which is precisely why each of
  them grew its own copy. The deepest ancestor of all fourteen consumers
  is here.

  `.msg.error` DID NOT COME WITH THEM, and that is a ruling, not an
  omission: eight of the settings-area pages carry it and they do not
  agree -- one tints the whole row, others recolour border and text, and
  two define none at all. Promoting one spelling would restyle the rest.
  `_settings.html`'s own comment states this; it is repeated here because
  this is now the file a reader will look in first.
  {% endcomment %}
  .messages { list-style: none; margin: 0 0 1rem; padding: 0; }
  .msg {
    padding: 0.6rem 0.9rem;
    border-radius: 6px;
    margin-bottom: 0.4rem;
    font-size: 0.9rem;
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--text);
  }
```

- [ ] **Step 4: Delete the eight copies**

| File | Delete | Keep |
|---|---|---|
| `_settings.html:92-101` | `.messages` and `.msg` | everything else in that block, and its `.msg.error` comment at `:78-86` — reword it to point at the shell |
| `chat/base.html:1556-1558` | `.messages` and `.msg` | — |
| `jobs/queue.html:62-65` | `.messages` | — |
| `rag/documents.html:201-204,206-212` | `.messages` and the shared part of `.msg` | `.msg`'s `background: var(--shelf-active-bg)` override if it is genuinely different from the shell's — read it and decide; `.msg.error` at `:213-217` |
| `rag/search.html:56-62` | nothing, if its `.msg` genuinely differs (own `margin-top`, own padding, the `var()` fallback) | the whole rule, as a local override |
| `rag/settings.html:47-50` | nothing — it only overrides `.msg.error` | the whole rule |
| `vision/gallery.html:11` | `.messages` | — |
| `console.html:124-128` | `.messages` (already deleted by Task 3 — confirm) | `.msg`/`.msg.error`/`.msg.warning` at `:129-144` |

Every page that overrides `extra_style` and now depends on the shell's rules must open its block with
`{{ block.super }}`. Check each one: `rag/settings.html:31` already does; `console.html` and
`model_sets.html` gained it in Task 3; `chat/base.html`, `jobs/queue.html`, `rag/documents.html`,
`rag/search.html` and `vision/gallery.html` need checking individually.

**`vision/gallery.html` is the awkward one:** its `.messages` sits inside `{% block vision_style %}`,
a block *nested inside* `vision/base.html`'s own `extra_style` — not a `block.super` override of the
shell's block at all. Deleting the rule is still correct (the shell's rule reaches it through the
cascade, since `vision/base.html` does write `{{ block.super }}`) but verify that `vision/base.html`
actually does before deleting, and if it does not, add it there.

- [ ] **Step 5: Run the pin and the suites**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests tools/rag/tests tools/vision/tests models/queue/tests models/registry/tests agents/chat/tests`
Expected: PASS.

- [ ] **Step 6: Look at every flash**

Trigger a flash on `/chat/`, `/chat/settings/`, `/rag/documents/`, `/rag/search/`, `/rag/settings/`,
`/vision/gallery/`, `/queue/`, `/inference/console/`, `/inference/model-sets/`, `/settings/` and
`/identity/users/`, in both colour schemes. Fourteen consumers is fourteen chances to have moved a
pixel.

- [ ] **Step 7: Full gate, then commit**

```
git add foundation/templates/_shell.html foundation/templates/_settings.html \
        agents/chat/templates/chat/base.html models/queue/templates/jobs/queue.html \
        models/registry/templates/inference/console.html tools/rag/templates/rag/*.html \
        tools/vision/templates/vision/gallery.html foundation/ops/tests/test_css_ownership.py
git commit -m "refactor(css): the flash recipe moves to the deepest common ancestor"
```

Body:

```
C-21/C-52. `.messages`'s list-reset was written out at six sites and
`.msg`'s recipe at several more. `_settings.html` held the canonical copy
and that was right for the ten pages it could see -- but chat, jobs, rag
and vision all extend bare `_shell.html` and cannot reach it, which is
exactly why each grew its own. The shell is the deepest ancestor of all
fourteen. `.msg.error` deliberately did NOT move: eight settings-area
pages carry it and disagree, and promoting one spelling would restyle the
others -- `_settings.html`'s own comment already ruled on that, and the
ruling now sits in the file a reader looks in first. Every genuine local
variant stays, after `{{ block.super }}`, where it still wins.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 31: One `foundation/templates/_messages.html`, included at fifteen sites (C-19)

**Files:**
- Create: `foundation/templates/_messages.html`
- Modify: fifteen templates (the table below)
- Test: `foundation/ops/tests/test_css_ownership.py` (a new markup pin)

**The finding.** `git grep -n "for message in messages" -- '*.html'` finds the same flash loop in
fifteen live templates, and it has already drifted three ways: **five omit the
`{% if message.tags %}` guard** (so a tagless message renders `class="msg "` with a trailing space),
and **one renders `<div>` inside no `<ul>` and with no `{% if messages %}` wrapper at all**.

**The canonical markup** — `agents/chat/templates/chat/_messages.html:10-16`:

```django
{% if messages %}
<ul class="messages">
  {% for message in messages %}
  <li class="msg{% if message.tags %} {{ message.tags }}{% endif %}">{{ message }}</li>
  {% endfor %}
</ul>
{% endif %}
```

**Reachability.** All fifteen descend from `_shell.html` (some through `_settings.html`, some through
an app `base.html`), and `{% include %}` resolves against the template dirs rather than the
inheritance chain — so a partial at `foundation/templates/_messages.html`, a sibling of `_shell.html`,
is reachable from every one of them regardless of which intermediate base they extend.

**The fifteen sites:**

| # | File:line | Extends | Guard? | Shape |
|---|---|---|---|---|
| 1 | `agents/chat/templates/chat/agent_entitlements.html:54` | `_settings.html` | **no** | one-line `<li>` |
| 2 | `agents/chat/templates/chat/settings.html:62` | `_settings.html` | yes | multi-line |
| 3 | `agents/chat/templates/chat/tool_entitlements.html:52` | `_settings.html` | **no** | one-line `<li>` |
| 4 | `identity/templates/identity/entitlement.html:94` | `_settings.html` | **no** | one-line `<li>` |
| 5 | `identity/templates/identity/entitlements.html:69` | `_settings.html` | **no** | one-line `<li>` |
| 6 | `identity/templates/identity/groups.html:85` | `_settings.html` | **no** | one-line `<li>` |
| 7 | `identity/templates/identity/settings.html:96` | `_settings.html` | yes | multi-line |
| 8 | `identity/templates/identity/users.html:192` | `_settings.html` | yes | multi-line |
| 9 | `models/queue/templates/jobs/queue.html:222` | `jobs/base.html` → shell | yes | multi-line |
| 10 | `models/registry/templates/inference/console.html:929` | `inference/base.html` → `_settings.html` | yes | multi-line |
| 11 | `models/registry/templates/inference/model_sets.html:76` | `inference/base.html` → `_settings.html` | **no** | **`<div>`, no `<ul>`, no `{% if messages %}`** |
| 12 | `tools/rag/templates/rag/documents.html:658` | `rag/base.html` → shell | yes | multi-line |
| 13 | `tools/rag/templates/rag/settings.html:103` | `_settings.html` | yes | multi-line |
| 14 | `tools/vision/templates/vision/engine_files.html:56` | `_settings.html` | yes | multi-line |
| 15 | `tools/vision/templates/vision/gallery.html:47` | `vision/base.html` → shell | yes | multi-line |

**Site 11 is a real rendering change**, not just a de-duplication: `model_sets.html` currently renders
loose `<div class="msg …">` elements with no list wrapper and no empty-messages guard. After this task
it renders a `<ul class="messages">` like every other page. That is the point — and it is why Task 3
was careful to note that the audit's *original* claim of a live bulleted-list bug on that page was
wrong. There is no bug today; there is an inconsistency, and this is where it is settled.

**`agents/chat/templates/chat/_messages.html`** becomes a one-line passthrough or is deleted and its
six consumers repointed. Read its own comment first (it explains which chat pages include it and
where its CSS lives) and carry the useful half into the new partial's comment.

- [ ] **Step 1: Write the failing markup pin**

```python
def test_no_template_writes_its_own_flash_loop():
    """C-19. The same flash markup appeared in fifteen templates and had
    already drifted three ways: five omitted the `{% if message.tags %}`
    guard, so a tagless message rendered `class="msg "`, and one rendered
    loose `<div>`s with no list wrapper and no empty-messages guard at
    all. One partial, included everywhere."""
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        if path.name == "_messages.html" or "/.claude/" in str(path):
            continue
        if "for message in messages" in path.read_text():
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"templates writing their own flash loop: {offenders}"
```

Beware `foundation/templates/_settings.html:73`, which contains that phrase inside a
`{% comment %}` block as prose — strip comments with the module's own `_COMMENT_RE` before matching,
or the pin fails on a file that has no loop.

- [ ] **Step 2: Run and watch it fail** — listing fifteen files.

- [ ] **Step 3: Create the partial**

`foundation/templates/_messages.html`:

```django
{% comment %}
THE FLASH, rendered identically everywhere (C-19). Fifteen templates
across chat, identity, registry, queue, rag and vision each wrote this
loop out, and it had already drifted: five omitted the `{% templatetag
openblock %} if message.tags {% templatetag closeblock %}` guard -- which
renders `class="msg "` with a trailing space for a tagless message -- and
`inference/model_sets.html` rendered loose `<div>` elements with no list
wrapper and no empty-messages guard at all.

A SIBLING OF `_shell.html`, NOT A BLOCK IN IT. Every consumer descends
from the shell, but through four different intermediate bases
(`_settings.html`, `jobs/base.html`, `rag/base.html`, `vision/base.html`),
and each page puts its flash in a different place in its own content --
above a form here, below a heading there. A block would fix the position;
an include leaves the position to the page and shares only the markup.
`{% templatetag openblock %} include {% templatetag closeblock %}`
resolves against the template dirs rather than the inheritance chain, so
this file is reachable from all fifteen regardless of what they extend.

THE `.messages`/`.msg` RULES IT DEPENDS ON live in `_shell.html`'s own
`<style>` block, at the deepest ancestor of every consumer -- see the
comment there.
{% endcomment %}
{% if messages %}
<ul class="messages">
  {% for message in messages %}
  <li class="msg{% if message.tags %} {{ message.tags }}{% endif %}">{{ message }}</li>
  {% endfor %}
</ul>
{% endif %}
```

- [ ] **Step 4: Repoint all fifteen, one at a time**

Each site's whole `{% if messages %}…{% endif %}` block (or, for site 11, its bare `{% for %}` loop)
becomes:

```django
{% include "_messages.html" %}
```

preserving the surrounding indentation and position exactly. Work through the table in order and run
the owning app's tests after each app's group — this is fifteen small edits and one wrong deletion is
easier to find at three sites than at fifteen.

- [ ] **Step 5: Retire `chat/_messages.html`**

Its **six** consumers include it by name: `chat/index.html:80`, `chat/workstream.html:99`,
`chat/all.html:148`, `chat/workstream_new.html:54`, `chat/workstream_settings.html:65`, and
`chat/conversation.html:253` — **the last of which is a held file**. **Do not edit
`conversation.html`.** So:
leave `agents/chat/templates/chat/_messages.html` in place and make its body a single
`{% include "_messages.html" %}`, keeping the useful half of its existing comment above it. Its three
consumers are then unchanged, the held file is untouched, and there is still exactly one copy of the
markup.

- [ ] **Step 6: Run everything**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q`
Expected: PASS. Several test modules assert on rendered flash text; a changed wrapper element could
move one. If a test asserts `<div class="msg` for `model_sets.html`, repoint it to `<li class="msg` —
that assertion is pinning the inconsistency.

- [ ] **Step 7: Look at all fifteen**

Trigger a flash on each of the fifteen pages, in both colour schemes, including a **tagless** message
(which is what the missing guard affected) and an **empty** message list on `model_sets.html` (which
is what the missing wrapper affected).

- [ ] **Step 8: Full gate, then commit**

```
git add foundation/templates/_messages.html agents/chat/templates/chat/*.html \
        identity/templates/identity/*.html models/queue/templates/jobs/queue.html \
        models/registry/templates/inference/*.html tools/rag/templates/rag/*.html \
        tools/vision/templates/vision/*.html foundation/ops/tests/test_css_ownership.py
git commit -m "refactor(css): one flash partial, included at fifteen sites"
```

Body:

```
C-19. The same flash loop appeared in fifteen templates across six apps
and had already drifted three ways: five omitted the `message.tags` guard
and rendered `class="msg "` for a tagless message, and
`inference/model_sets.html` rendered loose `<div>` elements with no list
wrapper and no empty-messages guard at all -- that page now renders the
same `<ul class="messages">` as everywhere else, which is a real markup
change and the point of the task. An include rather than a block, because
each page puts its flash somewhere different in its own content and only
the markup is shared. `chat/_messages.html` stays as a one-line
passthrough so its six consumers -- one of which is held for another PR
-- are untouched.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 32: `.banner`, `.muted`, `.empty` and `.delete-disclosure > summary` move up (C-22, C-23, C-24)

**Files:**
- Modify: `foundation/templates/_shell.html` (the `<style>` block)
- Modify: `agents/chat/templates/chat/base.html:1476-1482` — **and nothing else in that file**
- Modify: `tools/vision/templates/vision/base.html:27-32`, `:37`, `:114-116`
- Modify: `tools/vision/templates/vision/engine_files.html:37-39`
- Modify: `tools/rag/templates/rag/documents.html:220-234`, `:590-594`
- Modify: `tools/rag/templates/rag/search.html:68-72`
- Modify: `models/registry/templates/inference/console.html:882-886`
- Modify: `agents/chat/templates/chat/agent_entitlements.html:38`,
  `chat/tool_entitlements.html:45`, `foundation/setup/templates/setup/index.html:23`,
  `identity/templates/identity/entitlements.html:62`, `identity/templates/identity/login.html:64`,
  `models/registry/templates/inference/model_sets.html:71`
- Test: `foundation/ops/tests/test_css_ownership.py`

**Gated on #84 — read Global Constraint 11 before editing `chat/base.html`.**
`agents/chat/templates/chat/base.html:1531-1533` (`.delete-disclosure`) is **dead on `main` and live
on `#84`** and stays exactly as it is. This task edits `:1476-1482` only. Do not "tidy while you are
in there".

**C-22 — `.banner`.** Byte-identical (modulo line wrapping) at three sites:
`chat/base.html:1476-1481`, `vision/base.html:27-32`, `rag/documents.html:225-234`:

```css
  .banner {
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    padding: 0.75rem 1rem; margin-bottom: 1rem;
  }
  .banner a { color: var(--accent); }
```

`.banner.warn { border-color: var(--accent); }` exists in the chat and vision copies and **not** in
`documents.html` — so it is genuinely page-specific and does **not** move. Promote `.banner` and
`.banner a`; leave `.banner.warn` where it is, twice.

**C-23 — `.muted` (eight sites) and `.empty` (three).** `.muted` has drifted: seven sites at
`0.85rem`/`.85rem`, one at `0.9rem` (`setup/index.html:23`), and three carry an extra `margin`.
Promote the seven-site consensus:

```css
  .muted { color: var(--muted); font-size: 0.85rem; }
```

and leave each page's extra declaration as a local override:

| Site | Keeps |
|---|---|
| `chat/agent_entitlements.html:38` | `margin: 0 0 .3rem;` |
| `chat/tool_entitlements.html:45` | `margin: 0 0 .3rem;` |
| `identity/login.html:64` | `margin-top: 1.25rem;` |
| `setup/index.html:23` | its whole rule, unchanged — `0.9rem` is a **drift, not a consensus**, and silently resizing that page's muted text is a pixel change nobody asked for. Leave it, and add a one-line comment saying it is deliberately not the shared size. |
| `chat/base.html:1482`, `identity/entitlements.html:62`, `model_sets.html:71`, `vision/base.html:37` | nothing — delete the rule outright |

`.empty` is byte-identical at `console.html:882-886` and `rag/documents.html:590-594`:

```css
  .empty {
    color: var(--muted);
    font-size: 0.9rem;
    padding: 1rem 0.25rem 1.25rem;
  }
```

`search.html:68-72` is a genuine variant (`margin-top: 1.5rem`, no `padding`). Promote the identical
pair; leave `search.html`'s as a local override.

**C-24 — `.delete-disclosure > summary`. Three sites, not the audit's two, and the third is
`#84`-gated.** `vision/base.html:114-116` and `vision/engine_files.html:37-39` carry the identical
pair:

```css
  .delete-disclosure > summary { cursor: pointer; color: var(--accent); font-weight: 500; list-style: none; }
  .delete-disclosure > summary::-webkit-details-marker { display: none; }
```

`engine_files.html` extends `_settings.html`, **not** `vision/base.html`, so the two sit on
structurally disjoint branches under one `_shell.html` root — the same forced-duplication shape as
C-21, and it cannot be fixed anywhere lower. The `.delete-disclosure { margin-top: … }` line
genuinely differs (`0.5rem` vs `0`) and stays local at both sites.

**The third copy is `agents/chat/templates/chat/base.html:1531-1533`** — the same two rules,
whitespace-normalising to the identical `(selector, body)` tuples, inside the block **Global
Constraint 11 forbids touching** because it is dead on `main` and live on PR #84. So promoting the
pair to `_shell.html` leaves a copy behind that the Step 1 pin would flag, and Task 33's generalized
gate would flag too.

**Ruling: promote, and record the exemption rather than defer.** The two vision templates get the
benefit now, the chat copy is left exactly as it is, and both the pin and Task 33's gate carry a
named, dated exemption — which is a smaller lie than leaving three copies in the tree and a much
smaller one than editing a block another PR owns.

**A NEW set, not `_KNOWN_FALSE_POSITIVES`.** That set is keyed `(fragment filename, bare class
name)` and is consulted only by the fragment/leaf-page gate
(`foundation/ops/tests/test_css_ownership.py:260`: `if (fragment.name, cls) not in
_KNOWN_FALSE_POSITIVES`); its own comment (`:109-120`) says it exists for compound-selector false
positives in *that* scan. Seeding it with template paths and full selectors would put entries in a
container nothing that needs them reads, and would falsify its documented contract. Add a second,
separately-keyed set beside it:

```python
# EXEMPTIONS FOR THE DUPLICATE-RULE CHECKS, keyed (repo-relative
# template path, selector) -- a DIFFERENT shape from
# `_KNOWN_FALSE_POSITIVES` above, which is keyed (fragment filename,
# bare class name) and is read only by the fragment/leaf-page gate.
# Two shapes because they answer two questions; one set would make
# both lookups lie.
_KNOWN_DUPLICATE_EXEMPTIONS: set[tuple[str, str]] = {
    # C-24, and the ONE entry this set carries. `.delete-disclosure >
    # summary` and its `::-webkit-details-marker` sibling moved up to
    # `_shell.html`; `agents/chat/templates/chat/base.html:1531-1533`
    # still spells them out, and that block may not be touched here --
    # it is DEAD on `main` and LIVE on PR #84 (`conversation.html:224`),
    # so the #84 decision owns it. DELETE THIS EXEMPTION the moment #84
    # lands or is abandoned: at that point the chat copy is either
    # redundant (delete it) or the whole `.delete-disclosure` question
    # reopens on that branch's terms.
    ("agents/chat/templates/chat/base.html", ".delete-disclosure > summary"),
    ("agents/chat/templates/chat/base.html",
     ".delete-disclosure > summary::-webkit-details-marker"),
}
```

Task 33 must repeat this exemption in its own docstring when it generalises the gate, or its Step 5
("a gate that is red at the end of this task has not passed") is unsatisfiable.

- [ ] **Step 1: Write the failing pins**

One parametrized pin over the four selectors, reusing Task 30's whole-rule comparison:

```python
@pytest.mark.parametrize("selector", [
    ".banner", ".banner a", ".muted", ".empty",
    ".delete-disclosure > summary", ".delete-disclosure > summary::-webkit-details-marker",
])
def test_a_shared_primitive_is_not_retyped_below_the_shell(selector):
    """C-22/C-23/C-24. Four primitives written out at three, eight, two
    and two sites. Whole-rule comparison, so a page's genuine variant
    (`documents.html` has no `.banner.warn`; `search.html`'s `.empty`
    has a margin and no padding; `setup/index.html`'s `.muted` is a
    different size) is not counted as a copy -- the same distinction
    `test_no_settings_page_retypes_a_rule_settings_html_already_owns`
    already draws.

    `_KNOWN_DUPLICATE_EXEMPTIONS` carries the one #84-gated exemption -- see
    C-24 above and that set's own comment."""
```

The pin must consult `_KNOWN_DUPLICATE_EXEMPTIONS` before
asserting, or the `chat/base.html` copy fails it.

- [ ] **Step 2: Run and watch it fail** — listing the sites per selector.

- [ ] **Step 3: Add the shared rules to `_shell.html`**

```
  {% comment %}
  SHARED PRIMITIVES (C-22/C-23/C-24). Each of these was written out below
  the shell at two or more sites on structurally DISJOINT branches:
  `chat/base.html`, `vision/base.html` and `rag/base.html` all extend this
  file directly, and `vision/engine_files.html` extends `_settings.html`
  rather than `vision/base.html` -- so no lower template is an ancestor of
  every consumer and the duplication was forced rather than lazy.

  WHAT DELIBERATELY DID NOT COME UP: `.banner.warn` (absent from
  `rag/documents.html`, so it is genuinely two pages' rule and not
  three'), `.delete-disclosure`'s own `margin-top` (0.5rem in one place,
  0 in the other), `.empty`'s variant on the search page, and
  `setup/index.html`'s `.muted` at 0.9rem -- that last one is a DRIFT
  from the seven-site 0.85rem consensus, and resizing that page's muted
  text to match would be a pixel change nobody asked for. Each stays
  local.
  {% endcomment %}
  .banner {
    border: 1px solid var(--border); border-radius: 8px; background: var(--panel);
    padding: 0.75rem 1rem; margin-bottom: 1rem;
  }
  .banner a { color: var(--accent); }
  .muted { color: var(--muted); font-size: 0.85rem; }
  .empty { color: var(--muted); font-size: 0.9rem; padding: 1rem 0.25rem 1.25rem; }
  .delete-disclosure > summary { cursor: pointer; color: var(--accent); font-weight: 500; list-style: none; }
  .delete-disclosure > summary::-webkit-details-marker { display: none; }
```

- [ ] **Step 4: Delete the copies, keeping every genuine variant**

Work the table in the finding above, one file at a time. After each file, confirm the page still
opens its `extra_style` with `{{ block.super }}` — a page that overrides the block outright and now
depends on a shell rule will render unstyled, which is the exact failure
`test_css_ownership.py`'s docstring was written about.

- [ ] **Step 5: Run the pins and the full suite**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Confirm `#84`'s block is untouched**

Run: `git diff main -- agents/chat/templates/chat/base.html`
Expected: the diff touches `:1476-1482` (and Task 30's `:1556-1558`) and **nothing at `:1531-1533`**.

- [ ] **Step 7: Look at the pages**

`.banner` appears on the chat thread and workstream pages, the vision pages and the library;
`.muted` on eight; `.empty` on three; `.delete-disclosure` on two vision pages. Load each, both
colour schemes, and confirm the disclosure triangle is still hidden on both vision pages — the
`::-webkit-details-marker` rule is the one whose removal would be least visible in a test and most
visible on screen.

- [ ] **Step 8: Full gate, then commit**

```
git add foundation/templates/_shell.html agents/chat/templates/chat/base.html \
        agents/chat/templates/chat/agent_entitlements.html agents/chat/templates/chat/tool_entitlements.html \
        foundation/setup/templates/setup/index.html identity/templates/identity/*.html \
        models/registry/templates/inference/*.html tools/rag/templates/rag/*.html \
        tools/vision/templates/vision/*.html foundation/ops/tests/test_css_ownership.py
git commit -m "refactor(css): four shared primitives move to the deepest common ancestor"
```

Body:

```
C-22 `.banner` was byte-identical in three files on three different
branches. C-23 `.muted` in eight, already drifted (seven at 0.85rem, one at
0.9rem), and `.empty` in three, two of them identical. C-24
`.delete-disclosure > summary` in two vision templates that extend
DIFFERENT parents, so no lower template could ever have owned it. Each
shared rule moves to the shell; every genuine variant -- `.banner.warn`,
the two `margin-top`s, the search page's `.empty`, and the setup page's
0.9rem `.muted` -- stays exactly where it is, because promoting a drift is
a pixel change wearing a de-duplication's clothes.
`chat/base.html:1531-1533` is untouched: that rule is dead here and live
on the open attachments branch.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 33: `test_css_ownership.py` walks the whole repo, not just `agents/chat/` (WP4's gate)

**Files:**
- Modify: `foundation/ops/tests/test_css_ownership.py` (393 lines)
- Modify: `docs/ARCHITECTURE.md` or `docs/DEV.md` — wherever the CSS placement rule is stated for
  contributors; add the generalized gate's name
- Test: the module is the test

**Why this task exists.** The gate's own docstring states the rule correctly — *"a selector's home is
the deepest template that is an ancestor of every template that uses it"* — and then applies it to
one directory. `_CHAT_TEMPLATES = REPO_ROOT / "agents" / "chat" / "templates" / "chat"` (`:107`) is
hardcoded, `_INCLUDE_RE` matches only `include "chat/_…"`, and every glob walks that one path. **That
is structurally why C-19 through C-24 survived two prior consolidation passes**: none of them is in
`agents/chat/`. Fixing the twenty-eight sites without fixing the gate buys one pass, not a rule.

**What the generalized gate has to walk**, from the verified survey:

- **Nine template directories**, not one: `agents/chat/templates/chat`,
  `foundation/landing/templates/landing`, `foundation/setup/templates/setup`,
  `foundation/templates`, `identity/templates/identity`, `models/queue/templates/jobs`,
  `models/registry/templates/inference`, `tools/rag/templates/rag`,
  `tools/rag/templates/rag/panels`, `tools/vision/templates/vision`.
- **An extends graph three tiers deep**, not two: `_shell.html` → `_settings.html` →
  `inference/base.html` → `console.html` is a real chain, and `_shell.html` → `rag/base.html` →
  `documents.html` is another. Today's gate assumes one base and its leaves.
- **Thirty `{% include %}` sites across eleven files outside `chat/`**, whose fragment/page
  relationship the gate does not check at all.
- **Nested style blocks:** `vision/gallery.html` puts its rules in `{% block vision_style %}`, a block
  *inside* `vision/base.html`'s `extra_style`. `_BLOCK_RE` (`extra_style|chat_style`) does not match
  it. The block-name set must be derived, not hardcoded to two names.

**Keep everything the current gate got right**, and say so in the new docstring:

- comment-stripping and `{{ … }}`-stripping before parsing (a `{% comment %}` in this tree routinely
  contains `.selector`-shaped prose, and `{{ block.super }}` reads as a `.super` selector);
- whole-rule comparison (selector **and** body) for the retyping check — two pages using one class
  name for unrelated rules is not duplication;
- the S27 scoping — a fragment whose only consumer is the page defining the class has nowhere to
  render unstyled and is not a finding;
- `_KNOWN_FALSE_POSITIVES` as a real, empty set — keyed `(fragment filename, bare class name)`,
  read only by the fragment/leaf-page gate, and left exactly as it is, with somewhere for the next
  irreducible compound-selector case to land. **Separately**, `_KNOWN_DUPLICATE_EXEMPTIONS` — keyed
  `(repo-relative template path, selector)` and seeded by Task 32 with the `#84`-gated
  `.delete-disclosure > summary` pair in `agents/chat/templates/chat/base.html:1531-1533`. Carry
  that set across verbatim, comment and all, keep the two shapes separate (they answer two different
  questions and one set would make both lookups lie), and repeat in this module's docstring that the
  gate exempts that one file only because Global Constraint 11 forbids editing it, and that the
  exemption dies with the `#84` decision. Without carrying it, Step 5 below is unsatisfiable;
- `test_the_gate_is_not_vacuous`.

- [ ] **Step 1: Build the extends graph**

Replace the hardcoded `_CHAT_TEMPLATES` with a derived one:

```python
_EXTENDS_RE = re.compile(r'{%\s*extends\s+"([^"]+)"\s*%}')


def _template_files():
    """Every tracked `.html` in the repo, by its template-loader name
    (`chat/base.html`, `_shell.html`, `rag/panels/documents.html`) --
    which is what an `{% extends %}`/`{% include %}` string actually
    names, and is NOT the same as the filesystem path."""


def _extends_graph():
    """{template name: parent template name or None}. Three tiers deep in
    this tree (`_shell.html` -> `_settings.html` -> `inference/base.html`
    -> `console.html`), which is why the old one-base-and-its-leaves
    shape could not see outside `agents/chat/`."""


def _ancestors(name, graph):
    """`name`'s chain up to the root, nearest first."""
```

Pin the graph itself, so a broken parser fails loudly rather than silently finding nothing:

```python
def test_the_extends_graph_is_the_shape_this_repo_actually_has():
    """Anti-vacuous, and the reason the generalized gate can be trusted.
    Derived, not hardcoded -- but pinned at three known chains so a
    regex that stops matching cannot quietly turn the gate into a
    tautology."""
    graph = _extends_graph()
    assert graph["_settings.html"] == "_shell.html"
    assert graph["inference/console.html"] == "inference/base.html"
    assert graph["inference/base.html"] == "_settings.html"
    assert graph["rag/documents.html"] == "rag/base.html"
    assert graph["chat/base.html"] == "_shell.html"
    assert graph["_shell.html"] is None
    assert len(graph) >= 40
```

- [ ] **Step 2: Generalize the retyping check to the whole graph**

`test_no_settings_page_retypes_a_rule_settings_html_already_owns` becomes
`test_no_template_retypes_a_rule_one_of_its_ancestors_already_owns`: for every template, for every
ancestor in its chain, assert no whole rule appears in both. Keep the whole-rule (selector **and**
body) comparison and the reason for it, quoted from the current comment: *"an early draft matched on
class name alone and flagged `identity/entitlement.html`'s `.warn { …; font-size: .88rem; }` against
`_settings.html`'s `.warn { …; font-size: .85rem; }` — the SAME name, a DIFFERENT rule."*

- [ ] **Step 3: Generalize the fragment check to every include**

`_INCLUDE_RE` becomes `{%\s*include\s+"([^"]+)"` — any template, not just `chat/_…`. The fragment
check then asks, for every included template: is any class it uses defined **only** inside one
consumer page's own style block, and not in a template that is an ancestor of *every* consumer?

Keep the S27 scoping exactly: a fragment whose consumer set is `{the page that defines the class}`
is exempt; a fragment with **no** known consumer is **not** exempt (the conservative default the
current gate already chose, and its reasoning about `_composer.html`'s non-leaf fragments applies to
`vision/_job_card.html` and `rag/panels/documents.html` in the same way).

- [ ] **Step 4: Derive the style-block names**

`_BLOCK_RE` hardcodes `extra_style|chat_style`. Derive the set instead: any block a template declares
whose name ends in `_style`, plus `extra_style`. That catches `vision_style` (which the current gate
cannot see at all) and anything a future column adds. Pin the derived set:

```python
def test_every_style_block_name_in_the_tree_is_parsed():
    assert _style_block_names() >= {"extra_style", "chat_style", "vision_style"}
```

- [ ] **Step 5: Run it and read what it finds**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_css_ownership.py`

**Expected: red, with a list.** The generalized gate is being written *after* Tasks 29–32, so most of
what it would have caught is already fixed — but a wider net over nine directories will find sites no
auditor listed. For each one it reports:

- if it is a genuine placement defect that Tasks 29–32 should have caught, **fix it** and note it in
  the commit body;
- if it is a compound-selector false positive of the kind the docstring's "kind 2" describes, add it
  to `_KNOWN_FALSE_POSITIVES` **with a comment naming why**, which is what that set exists for;
- if it is a whole category the gate should not police (an app-private base with one leaf, say),
  narrow the gate deliberately and write the reasoning into the docstring — never loosen it silently.

**A gate that is red at the end of this task has not passed.** Either the tree is fixed or the gate's
scope is narrowed on the record.

- [ ] **Step 6: Show it red in both directions**

The current gate's own comment describes this ritual and it is not optional. Temporarily:

- add a class to a **non-chat** fragment (`vision/_job_card.html`, say) that is defined only in
  `vision/gallery.html`'s own block → expect FAIL naming that pair;
- retype one of `_shell.html`'s newly-promoted rules verbatim in `rag/search.html` → expect FAIL
  naming that file and selector.

Revert both. Record both outputs in the commit body — that is the evidence the generalization did not
blunt the gate.

- [ ] **Step 7: Confirm the chat-scoped assertions still hold**

The three pre-consolidation failures the old gate pinned (`_tool_card.html` ×10 selectors,
`_turn_card.html` ×5, `_attach_files.html` ×3) must still be catchable. Re-run the old gate's
regression by hand: temporarily move one of `chat/base.html`'s shared rules into
`chat/workstream.html`'s own block and confirm the generalized gate fails on it.

- [ ] **Step 8: Update the contributor docs**

Wherever the three-tier CSS placement rule is written for contributors (`docs/DEV.md`, or
`docs/ARCHITECTURE.md`), say that the rule is now enforced repo-wide by
`foundation/ops/tests/test_css_ownership.py` and name what it walks. One paragraph.

- [ ] **Step 9: Full gate, then commit**

```
git add foundation/ops/tests/test_css_ownership.py docs/DEV.md
git commit -m "test(ops): the CSS-ownership gate walks every template, not just agents/chat"
```

Body:

```
The gate stated the rule correctly -- a selector's home is the deepest
template that is an ancestor of every template using it -- and then
applied it to one hardcoded directory. That is structurally why C-19
through C-24 survived two prior consolidation passes: none of them is in
`agents/chat/`. The extends graph is now derived rather than assumed
(three tiers deep in this tree, not two), every `{% include %}` is
followed rather than only `chat/_…`, and the style-block names are derived
so `vision_style` is parsed at all. Everything the old gate got right is
kept and re-argued in the docstring: comment- and variable-stripping,
whole-rule comparison, the single-consumer scoping, the empty
known-false-positive set, and the anti-vacuous pin. Shown red in both
directions before landing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

> **WP8 preamble — read before Tasks 34–39.**
>
> These six tasks move roughly ten thousand lines between files and delete about five hundred and
> fifty. They are last because they are the largest and the most mechanical, and because Tasks 4, 9,
> 24 and 27 all edit `tools/rag/tests/test_views.py` and Tasks 14 and 17 edit
> `models/registry/tests/test_views.py` — splitting those files first would make every one of those
> tasks land in a file that no longer exists.
>
> **The in-repo precedent is `tools/vision/tests/`:** one `views.py` module's tests already split by
> feature area into `test_views_create.py` (2014), `test_views_engine_files.py` (399),
> `test_views_gallery.py` (621), `test_views_generate.py` (1383), `test_views_operations.py` (212),
> `test_views_queue.py` (260). Note that the precedent itself has one file over 1500 lines, so
> **1500 is a target, not a gate** — do not contort a split to reach it.
>
> **Out of scope, named so they read as scoped-out rather than forgotten:**
> `tools/rag/tests/test_jobs.py` (1512), `tools/rag/tests/test_media.py` (1532),
> `models/queue/tests/test_worker.py` (1849) and `agents/chat/tests/test_sidebar.py` (1699) are all
> over the same threshold. None is named in the audit's C-56, and `test_sidebar.py` sits beside held
> files. A follow-up, not this plan.

### Task 34: One registry-reset fixture factory (C-59)

**Files:**
- Create: `models/contracts/testing.py`
- Modify: `models/queue/tests/test_backend.py:87`, `models/queue/tests/test_worker.py:129`
- Modify: `models/registry/tests/test_jobkinds.py:26`, `test_queue_seam.py:22`, `test_roles.py:26`
- Modify: `models/queue/tests/_helpers.py`, `models/registry/tests/_helpers.py` (re-export)
- Test: `models/contracts/tests/test_testing.py` — **created by Task 36**; if Task 36 has not run,
  put the helper's own test in `models/registry/tests/test_jobkinds.py` and move it in Task 36

**Correction to the audit, and it changes the design.** There are **two** distinct fixture bodies,
not five copies of one. Four target `jobkinds._JOB_KINDS`:

```python
@pytest.fixture(autouse=True)
def reset_registry():
    original = dict(jobkinds._JOB_KINDS)
    jobkinds._JOB_KINDS.clear()
    yield
    jobkinds._JOB_KINDS.clear()
    jobkinds._JOB_KINDS.update(original)
```

and one (`models/registry/tests/test_roles.py:26`) targets `roles._ROLES` with the identical shape.
So the shared thing is a **snapshot/restore factory over a registry dict**, not a literal fixture.

**Column, and why not `identity/testing.py`.** The audit names `identity/testing.py` as the sanctioned
shared home, and for `make_user`/`posture`/`grant` it is. But that module's own docstring says
identity imports no other column, and both registries here live in `models.contracts` — so putting a
`models.contracts`-scoped helper there would invert the dependency. **The home is a new
`models/contracts/testing.py`**, mirroring `identity/testing.py`'s pattern exactly: a plain
importable module, never imported by production code, from which each package's own `_helpers.py`
re-exports what it uses. `models.contracts` is a pure leaf under rule 1, so every column may import
it; the two consumers here are both inside `models/`.

- [ ] **Step 1: Confirm the five bodies**

Run: `git grep -n -A6 "def reset_registry" -- models/`
Expected: four `jobkinds._JOB_KINDS` bodies and one `roles._ROLES` body, byte-identical within each
group. If a sixth has appeared or one has drifted, list the difference before proceeding — a drifted
copy may be load-bearing.

- [ ] **Step 2: Write `models/contracts/testing.py`**

```python
"""Shared test scaffolding for the `models` column's registries.

**Production code must never import this module.** It mirrors
`identity/testing.py`'s pattern -- a plain importable module, not a
`conftest.py` (this repo forbids those anywhere) -- and each package's own
`tests/_helpers.py` re-exports what it uses from here.

WHY HERE AND NOT `identity/testing.py` (C-59). The audit named that module
as the sanctioned shared home, and for `make_user`/`posture`/`grant` it
is. But its own docstring records that identity imports no other column,
and both registries this file resets live in `models.contracts` -- putting
a `models.contracts`-scoped helper there would invert the dependency for
the sake of reusing a filename. `models.contracts` is a pure leaf under
the import law's rule 1, so this module is importable from anywhere that
needs it.

WHY A FACTORY AND NOT A FIXTURE. Five test modules carried a
snapshot-clear-yield-restore fixture: four over `jobkinds._JOB_KINDS` and
one over `roles._ROLES`. The bodies are identical in shape and differ in
which dict they hold, so what is shared is the mechanism, not the fixture.
"""
from __future__ import annotations

import pytest


def registry_reset_fixture(module, attribute: str):
    """An autouse pytest fixture that empties `module.<attribute>` for the
    duration of a test and restores exactly what was there before.

    Use it as::

        reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")

    at module level in a test file -- the name it is bound to is the
    fixture's name, the way `pytest.fixture` always works.

    RESTORE, NOT JUST CLEAR: these registries are populated at app-ready
    time, so a test that emptied one and did not put it back would leave
    every later test in the run looking at an empty registry -- which is
    the class of order-dependent failure `docs/DEV.md`'s
    reversed-collection-order run exists to catch.
    """
    @pytest.fixture(autouse=True)
    def _reset():
        registry = getattr(module, attribute)
        original = dict(registry)
        registry.clear()
        yield
        registry.clear()
        registry.update(original)
    return _reset
```

- [ ] **Step 3: Write the helper's own tests**

```python
def test_the_fixture_empties_the_registry_for_the_test():
def test_the_fixture_restores_exactly_what_was_there():
def test_a_test_that_registers_something_does_not_leak_it():
def test_it_works_over_either_registry_dict():
```

The third is the one that matters: register a job kind inside a test using the fixture, then assert
in a *second* test that it is gone.

- [ ] **Step 4: Repoint the five modules**

Each file's seven-line fixture becomes one line:

```python
reset_registry = registry_reset_fixture(jobkinds, "_JOB_KINDS")
```

and in `test_roles.py`:

```python
reset_registry = registry_reset_fixture(roles, "_ROLES")
```

Re-export `registry_reset_fixture` from `models/queue/tests/_helpers.py` and
`models/registry/tests/_helpers.py` with the same `# noqa: F401 -- re-exported` marker
`tools/rag/tests/_helpers.py:50` uses, so the five modules import from their own package's helper
rather than reaching across.

- [ ] **Step 5: Run in both collection orders**

Run:
```
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
```
Expected: PASS both. Registry fixtures are exactly where an ordering leak shows, so the reversed run
is the real gate here, not a formality.

- [ ] **Step 6: Full gate, then commit**

```
git add models/contracts/testing.py models/queue/tests models/registry/tests
git commit -m "test(models): one registry-reset fixture factory for both registries"
```

Body:

```
C-59. Five test modules carried a snapshot-clear-yield-restore fixture --
four over `jobkinds._JOB_KINDS`, one over `roles._ROLES` -- so the shared
thing is the mechanism, not a literal fixture. A factory in a new
`models/contracts/testing.py`, mirroring `identity/testing.py`'s pattern.
NOT in `identity/testing.py` itself: that module's own docstring records
that identity imports no other column, and both registries live in
`models.contracts`, so putting it there would invert the dependency to
reuse a filename.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 35: One `make_pdf_bytes` in `tools/rag/tests/_helpers.py` (C-61)

**Files:**
- Modify: `tools/rag/tests/_helpers.py` (add the helper; add `import io`)
- Modify: `tools/rag/tests/test_ingest.py:48-107`, `test_jobs.py:40-95`, `test_readers.py:32-88`,
  `test_services.py:29-77`, `test_transcode.py:35-86`
- Test: the five modules are the test

**The finding.** `_make_pdf_bytes` appears in five rag test modules with **byte-identical bodies**;
only the docstrings differ, and `test_readers.py`'s is the one the other four point back at.
`test_transcode.py`'s docstring is explicitly self-aware — *"standalone copy … not a cross-module
import"* — which is the sanctioned-duplication reasoning applied to two modules **in the same
package**, where the package's own `_helpers.py` is the documented answer.

| File | Lines | Call sites |
|---|---|---|
| `test_ingest.py` | `48-107` (60, incl. a 13-line docstring) | 14 |
| `test_jobs.py` | `40-95` (56) | 2 |
| `test_readers.py` | `32-88` (57) — the canonical docstring | 8 |
| `test_services.py` | `29-77` (49) | 6 |
| `test_transcode.py` | `35-86` (52) | 7 |

`tools/rag/tests/_helpers.py` (266 lines) already holds `make_document`, `make_agent`,
`make_conversation`, `model_available`, `make_job_ctx`, `make_tool_ctx`, `post_ask`,
`fake_whisper_get`, `fake_whisper_post` and the `isolated_tool_registry` fixture — every exported
builder is named `make_*`.

- [ ] **Step 1: Confirm byte-identity**

Run: `git grep -n -A40 "def _make_pdf_bytes" -- tools/rag/tests` and diff the five bodies (ignoring
docstrings). Expected: identical. If one has drifted, the drift is either a bug in four files or a
deliberate variant in one — resolve that before merging them.

- [ ] **Step 2: Move it in, under the module's own naming convention**

Add to `tools/rag/tests/_helpers.py` as `make_pdf_bytes` (public, `make_*`, matching every other
exported builder), with the canonical docstring from `test_readers.py:32-88` plus one paragraph:

```python
def make_pdf_bytes(...):
    """<the canonical docstring from test_readers.py>

    ONE COPY (C-61). This function was written out identically in five
    modules of this package, and `test_transcode.py`'s own copy called
    itself "a standalone copy, not a cross-module import" -- which is the
    sanctioned-duplication reasoning for crossing an APP boundary, applied
    to two files in the same package, where this module is the documented
    answer. Renamed from `_make_pdf_bytes` to match every other builder
    exported from here.
    """
```

Add `import io` to `_helpers.py`'s stdlib imports.

- [ ] **Step 3: Delete the five copies and repoint the 37 call sites**

In each of the five modules: delete the local definition, add `make_pdf_bytes` to the existing
`from tools.rag.tests._helpers import (...)` list, and rename every call. Do them one file at a
time, running that file's tests after each.

- [ ] **Step 4: Run the rag suite** — Expected: PASS.

- [ ] **Step 5: Add the pin**

```python
def test_no_rag_test_module_builds_its_own_pdf_bytes():
    """C-61. Five modules in one package carried a byte-identical PDF
    builder. This package has a `_helpers.py`, and that is where a
    package's shared scaffolding goes."""
    for name in ("test_ingest", "test_jobs", "test_readers", "test_services", "test_transcode"):
        text = (Path(settings.BASE_DIR) / f"tools/rag/tests/{name}.py").read_text()
        assert "def _make_pdf_bytes" not in text
        assert "def make_pdf_bytes" not in text
```

- [ ] **Step 6: Full gate, then commit**

```
git add tools/rag/tests
git commit -m "test(rag): one PDF-bytes builder for the whole package"
```

Body:

```
C-61. `_make_pdf_bytes` was written out identically in five modules of one
package, ~270 lines in all, and `test_transcode.py`'s copy described
itself as "a standalone copy, not a cross-module import" -- the
sanctioned-duplication reasoning for crossing an app boundary, applied to
two files in the same package. `tools/rag/tests/_helpers.py` is where a
package's shared scaffolding lives; renamed to `make_pdf_bytes` to match
every other builder there, and 37 call sites repointed.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 36: `models/contracts/tests/` exists and owns its own unit tests (C-58)

**Files:**
- Create: `models/contracts/tests/__init__.py`
- Move: `tools/vision/tests/test_engine_base.py` (122) and `tools/rag/tests/test_engine_base.py`
  (108) → `models/contracts/tests/test_engines_base.py`
- Move: `tools/vision/tests/test_operations.py` (641) → `models/contracts/tests/test_operations.py`
- Move: Task 34's helper test → `models/contracts/tests/test_testing.py`
- Test: the moved modules are the tests

**The finding, verified.** `find models/contracts -iname 'test*'` is empty, and three test modules
living in consumer columns import **exclusively** from `models.contracts.*`:

| Module | Lines | Imports from | Imports from `tools.*` |
|---|---|---|---|
| `tools/vision/tests/test_engine_base.py` | 122 | `models.contracts.engines.base` (9 names), `models.contracts.engines.ollama` | **none** |
| `tools/rag/tests/test_engine_base.py` | 108 | `models.contracts.engines.base` (4 names), `models.contracts.engines.ollama` | **none** |
| `tools/vision/tests/test_operations.py` | 641 | `models.contracts.operations` (16 names) + stdlib | **none** |

So all three move verbatim: no import surgery beyond the file's own path, and no test body has to be
split by consumer.

**The two `test_engine_base.py` files are not duplicates** — `diff` confirms they test complementary
halves of one contract surface. The vision one covers `Asset`, `JobStatus`, `SetupGuide`,
`ImageGenerator` and the optional-member degradation; the rag one covers `TranscriptSegment`,
`TranscriptResult`, `Transcriber` and `build_transcriber`. Together they are 230 lines, so **merge
them into one `test_engines_base.py`** rather than carrying two files with the same name in one
directory.

`pytest.ini` needs no change: `testpaths` already includes `models`, and `models/contracts/` already
has an `__init__.py`.

- [ ] **Step 1: Create the package**

`models/contracts/tests/__init__.py`, empty, matching every other `tests/` package in the tree.

- [ ] **Step 2: Move `test_operations.py` first**

```
git mv tools/vision/tests/test_operations.py models/contracts/tests/test_operations.py
```

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/contracts/tests`
Expected: PASS with no edit at all — it imports only `models.contracts.operations` and stdlib. If it
fails, something imports from `tools.vision` that the survey missed; find it before continuing.

Add a module docstring paragraph saying why it moved:

```python
"""... (existing docstring)

MOVED FROM `tools/vision/tests/` (C-58). This module tests
`models.contracts.operations` and imports nothing from `tools.vision` at
all -- it lived in a consumer column only because that column was the
first consumer. A contract's own tests belong beside the contract, where
the next consumer finds them.
"""
```

- [ ] **Step 3: Merge the two `test_engine_base.py` files**

```
git mv tools/vision/tests/test_engine_base.py models/contracts/tests/test_engines_base.py
```

then append `tools/rag/tests/test_engine_base.py`'s four classes (`TestTranscriptSegment`,
`TestTranscriptResult`, `TestTranscriberProtocol`, `TestBuildTranscriberIsOptional`) and union the two
import lists, and delete the rag file:

```
git rm tools/rag/tests/test_engine_base.py
```

Write the merged module docstring explicitly: two halves of one contract surface — image generation
and setup on one side, transcription on the other — that were split across two consumer columns
because each column tested the half it used.

- [ ] **Step 4: Move Task 34's helper test**

If Task 34 parked `registry_reset_fixture`'s tests in `models/registry/tests/test_jobkinds.py`, move
them to `models/contracts/tests/test_testing.py` now.

- [ ] **Step 5: Run everything, both orders**

Run:
```
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
```
Expected: PASS, with the **same total test count** as before the move. A changed count means a module
stopped being collected — check `pytest.ini`'s `python_files` against the new filenames.

- [ ] **Step 6: Add the pin**

In `foundation/ops/tests/` (a repo-shape assertion, which is that package's business):

```python
def test_a_contract_module_keeps_its_tests_beside_itself():
    """C-58. `models/contracts/` had no tests directory at all, and three
    modules testing nothing but `models.contracts.*` lived in `tools/vision`
    and `tools/rag` -- in a consumer column, because that column happened
    to be the first consumer. The next consumer would not have found them."""
    assert (REPO_ROOT / "models/contracts/tests/__init__.py").is_file()
    for column in ("tools/vision", "tools/rag"):
        for path in (REPO_ROOT / column / "tests").glob("test_*.py"):
            text = path.read_text()
            imports_contracts = "from models.contracts" in text
            imports_own_column = column.replace("/", ".") in text
            assert not (imports_contracts and not imports_own_column), (
                f"{path} tests only `models.contracts` and belongs beside it")
```

- [ ] **Step 7: Update the READMEs**

`models/contracts/README.md`: name the new tests directory and what it covers.
`tools/vision/README.md` §"Running the tests" (`:1328`): if it enumerates the test modules, remove
the two that moved.

- [ ] **Step 8: Full gate, then commit**

```
git add models/contracts/tests models/contracts/README.md tools/vision/README.md \
        foundation/ops/tests tools/vision/tests tools/rag/tests
git commit -m "test(contracts): the contract's own unit tests move beside the contract"
```

Body:

```
C-58. `models/contracts/` had no tests directory, and three modules that
import nothing but `models.contracts.*` lived in `tools/vision` and
`tools/rag` -- in a consumer column, because that column happened to be
the first consumer, which is not a reason the next consumer would find
them. All three move verbatim; no import surgery was needed, which is the
evidence they never belonged where they were. The two `test_engine_base.py`
files are not duplicates -- they cover complementary halves of one contract
surface, image generation and transcription -- so they merge into one
module rather than colliding on a filename.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 37: The seven `*SettingsUpdate` classes lose their shared shape (C-57)

**Files:**
- Modify: `tools/rag/tests/test_views.py:3647-4698`
- Test: the module is the test

**Do this before Task 39.** C-57's ~1,050 lines all sit in the file Task 39 splits; shrinking them
first means Task 39 moves ~500 lines instead of ~1,050 and its last proposed file lands well under
the target.

**The seven, with their spans:**

| Class | Line | Length |
|---|---|---|
| `TestHistorySettingsUpdate` | 3647 | 46 |
| `TestUploadCapSettingsUpdate` | 3693 | 118 |
| `TestMediaDurationSettingsUpdate` | 3811 | 126 |
| `TestDocumentPagesSettingsUpdate` | 3937 | 68 |
| `TestRetrievalTopKSettingsUpdate` | 4005 | 348 |
| `TestRetrievalScoreFloorSettingsUpdate` | 4353 | 153 |
| `TestHybridSearchSettingsUpdate` | 4506 | 193 |

**What is genuinely shared**, in all seven: *a valid value updates the setting and redirects to
`rag-settings`*; *the settings row is created if it does not exist*; and *a bad value is a clean
redirect that leaves the stored value alone and says something specific*.

**What is genuinely not**, and this is the part that must survive intact: `retrieval_top_k` and
`hybrid_search` patch `tools.rag.views.resolve` to exercise a context-window fit check the other five
have no notion of; `max_upload_gb` and `max_media_minutes` convert through a unit base (1024³, 60)
and carry **regression pins for specific numbered review items** —
`test_non_finite_gb_is_rejected_cleanly_never_500s` (T4 review MAJOR: `float("inf")` made
`round(gb * 1024**3)` an uncaught `OverflowError`), `test_astronomically_large_gb_is_rejected_cleanly_never_500s`
(T10 MINOR 5: `1e308` is finite but its product is not),
`test_positive_gb_that_rounds_to_zero_bytes_is_rejected_cleanly` (T10 re-review MINOR 4: `1e-15` GB
rounds to a zero-byte cap); and `retrieval_score_floor` allows `0.0` as a deliberate OFF value where
every other field refuses zero.

**Every one of those pins stays, verbatim, with its docstring.** They each name a live 500 someone
found. A de-duplication that loses a regression pin is not a de-duplication.

- [ ] **Step 1: Add the shared table-driven class**

Above the seven, a class covering the three shared behaviours for all seven fields, with a
conversion function per field so the unit-carrying ones participate rather than being excluded:

```python
# (url name, POST field, RagSettings attribute, a valid input, what it
# stores, a bad input, the words the refusal must contain). ONE ROW PER
# FIELD (C-57): the three behaviours below are the ones all seven
# `*SettingsUpdate` classes tested identically -- a valid value updates
# and redirects, the settings row is created if absent, and a bad value
# is a clean redirect that changes nothing and says something specific.
_SETTINGS_FIELDS = [
    ("rag-history-settings", "history_limit", "history_limit", "5", 5, "abc", "whole number"),
    ("rag-document-pages-settings", "max_document_pages", "max_document_pages", "250", 250, "abc", "whole number"),
    ("rag-upload-cap-settings", "max_upload_gb", "max_upload_bytes", "5", round(5 * 1024**3), "abc", "must be a number of GB"),
    ("rag-media-duration-settings", "max_media_minutes", "max_media_seconds", "90", round(90 * 60), "abc", "must be a number of minutes"),
    ("rag-retrieval-top-k-settings", "retrieval_top_k", "retrieval_top_k", "8", 8, "abc", "must be a number"),
    ("rag-retrieval-score-floor-settings", "retrieval_score_floor", "retrieval_score_floor", "0.6", 0.6, "abc", "must be a number"),
]
# `hybrid_search` is deliberately absent: it is a checkbox, not a bounded
# number, it does not go through `_ragsettings_field_update` at all, and
# its own class says so.


@pytest.mark.django_db
class TestEverySettingsFieldSharesThreeBehaviours:
    """C-57. Seven `*SettingsUpdate` classes, ~1,050 lines, each opening
    with the same three tests. Those three are here, once, over a table;
    everything each field does that the others do not stays in that
    field's own class below -- including every regression pin that names
    a numbered review item, because a de-duplication that loses one of
    those has removed a guard against a 500 somebody actually hit."""

    @pytest.mark.parametrize(
        ("url_name", "field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_a_valid_value_updates_the_setting_and_redirects(
            self, client, url_name, field, attr, valid, stored, bad, words):
        response = client.post(reverse(url_name), {field: valid}, **_patched(url_name))
        assert response.status_code == 302
        assert response.url == reverse("rag-settings")
        assert getattr(RagSettings.get_solo(), attr) == stored

    @pytest.mark.parametrize(
        ("url_name", "field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_the_settings_row_is_created_if_it_does_not_exist_yet(
            self, client, url_name, field, attr, valid, stored, bad, words):
        assert RagSettings.objects.count() == 0
        client.post(reverse(url_name), {field: valid}, **_patched(url_name))
        assert RagSettings.objects.count() == 1

    @pytest.mark.parametrize(
        ("url_name", "field", "attr", "valid", "stored", "bad", "words"), _SETTINGS_FIELDS)
    def test_a_bad_value_changes_nothing_and_says_what_is_wrong(
            self, client, url_name, field, attr, valid, stored, bad, words):
        RagSettings.objects.create(pk=1, **{attr: stored})
        response = client.post(reverse(url_name), {field: bad}, follow=True, **_patched(url_name))
        assert response.status_code == 200
        assert getattr(RagSettings.get_solo(), attr) == stored
        assert words in response.content.decode()
```

`_patched(url_name)` is a small helper returning the `resolve`-patch context the two fit-checked
fields need and nothing for the other four — write it as a context manager rather than kwargs if that
reads better; the point is that the table's two special rows do not force the other four to carry a
patch they do not need.

- [ ] **Step 2: Run it and watch it pass**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py -k SharesThreeBehaviours`
Expected: PASS, 18 parametrized cases. If a row fails, the table's expectation is wrong for that
field — fix the table, not the production code.

- [ ] **Step 3: Delete the three now-duplicated tests from each of the seven classes**

From each class, remove only the tests the table now covers — the "valid value updates", "creates the
row", and the generic bad-value case. Keep:

- `TestUploadCapSettingsUpdate`: `test_fractional_gb_is_accepted`,
  `test_gb_round_trip_is_exact_to_one_decimal`, `test_non_positive_is_a_clean_error…`,
  `test_non_finite_gb_is_rejected_cleanly_never_500s`,
  `test_astronomically_large_gb_is_rejected_cleanly_never_500s`,
  `test_positive_gb_that_rounds_to_zero_bytes_is_rejected_cleanly` — **all six, verbatim, docstrings
  included.**
- `TestMediaDurationSettingsUpdate`: its own unit-conversion and overflow equivalents.
- `TestHistorySettingsUpdate`: the parametrized `["", "0", "-5", "abc", "1.5"]` sweep and the two
  message-specific tests (`positive`, `whole number`).
- `TestDocumentPagesSettingsUpdate`: whatever it has beyond the three.
- `TestRetrievalTopKSettingsUpdate`: the whole 1..50 bound sweep and the entire context-window fit
  check — this is the largest class and most of it is genuinely its own.
- `TestRetrievalScoreFloorSettingsUpdate`: the `[0.0, 1.0]` bounds and the `0.0`-is-OFF case.
- `TestHybridSearchSettingsUpdate`: **untouched.** It is not in the table.

Each class keeps its docstring; add one clause to each saying the three shared behaviours are covered
by the table above.

- [ ] **Step 4: Run the module and count**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_views.py`
Expected: PASS. Compare the collected-test count to before: it should be **higher** (18 parametrized
cases replacing ~21 hand-written ones is roughly a wash, and nothing was dropped). If it is lower by
more than three, a test was deleted that the table does not cover — find it.

Then measure: `wc -l tools/rag/tests/test_views.py`. Expected: roughly 400–500 lines shorter.

- [ ] **Step 5: Full gate, then commit**

```
git add tools/rag/tests/test_views.py
git commit -m "test(rag): the seven settings-field classes share their three common behaviours"
```

Body:

```
C-57. Seven `*SettingsUpdate` classes, ~1,050 lines, each opened with the
same three tests: a valid value updates and redirects, the row is created
if absent, a bad value changes nothing and says something specific. Those
three are now one table-driven class over six fields. Everything each
field does that the others do not stays in its own class -- the unit
conversions, the bounds, the context-window fit check, the `0.0`-is-OFF
case, and every regression pin naming a numbered review item, verbatim,
because those name 500s somebody actually hit. `hybrid_search` is a
checkbox that does not go through the shared field updater at all and is
untouched.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 38: `models/registry/tests/test_views.py` splits on its 31 dividers (C-56a)

**Files:**
- Delete: `models/registry/tests/test_views.py` (7,606 lines, 41 classes)
- Create: six `models/registry/tests/test_views_*.py` files
- Modify: `models/registry/tests/_helpers.py` (absorb the nine module-level helpers)
- Test: the moved modules are the tests

**Mechanical, not a rewrite. Not one assertion changes.**

**The nine module-level helpers move to `_helpers.py`** — every one is used from at least two of the
six proposed files, so leaving any of them in a split file would force a cross-file test import the
package's `_helpers.py` convention exists to avoid:

| Helper | Line | Used by |
|---|---|---|
| `_only_rag_roles()` | 79 | `:1370`, `:2505`, `:2533`, `:4975` — three proposed files |
| `client` fixture | 100 | everywhere |
| `_clear_seeded_rows(db)` autouse fixture | 105 | everywhere |
| `_mock_engine(healthy)` | 111 | ~95 call sites, nearly every class |
| `_raising_engine(exc)` | 131 | `:266` only — move it anyway, for one home |
| `_installed_row(model_id, **overrides)` | 2366 | ~50 sites across five classes |
| `_catalog_only_row(model_id, **overrides)` | 2392 | two classes |
| `_getting_models_html(body)` | 2475 | one class — move it anyway |
| `_extra_role_registry` fixture | 3529 | four classes |
| `_create_form_engine_options(body)` | 6018 | one class |

`_helpers.py` (251 lines) already exports `ENDPOINT`, `bind`, `clear_seeded_rows`,
`make_chat_connection`, `make_embed_connection`, `make_job_ctx`, `make_tool_ctx`,
`isolated_tool_registry`, `fake_ollama_get`, `fake_ollama_show`, and re-exports ten names from
`identity.testing`.

**The split, cut only at class boundaries, contiguous in the original order:**

| New file | Classes | Approx. lines |
|---|---|---|
| `test_views_console_and_roles.py` | `TestConsoleViewNav`, `…ColdStart`, `…Warm`, `TestRoleAssignConnectionPick`, `TestRoleAssignFamilyBindNote`, `TestRoleUnbind`, `TestRoleUnbindWithAnEnvOverride` | 1,400 |
| `test_views_reencode_and_sections.py` | `TestRoleReencode`, `TestStampFirstMaterialization`, `TestConnectionAdd`, `TestConsoleViewSections`, `TestGettingModelsChecklist`, `TestGettingModelsServerFacts`, `TestConsoleViewNoPerRowRegisterForm` | 1,430 |
| `test_views_machine_add_and_dropdowns.py` | `TestConsoleViewNotFoundOnMachine`, `TestMachineModelAdd`, `TestConsoleViewQueryCount`, `TestConsoleViewEndpointOverride`, `TestServerScanNeverRunsOnGet`, `TestServerScan`, `TestEndpointOverridePostRoundTrip`, `TestRoleDropdownOptions` | 1,425 |
| `test_views_connection_edit.py` | `TestMachineRowPipelineState`, `TestManualFormPrefill`, `TestOrphanConnectionLabel`, `TestConnectionDetailsDisclosure`, `TestConnectionEdit`, `TestManualCreateFormCollapsed` | 1,465 |
| `test_views_engine_and_remove.py` | `TestCapabilityCheckboxGroups`, `TestCreateFormContextWindowLabelScoping`, `TestSupportedEnginesHelper`, `TestEngineDropdownsAndSupportedAPIs`, `TestEngineProfileAutofill`, `TestModelServerTerminology`, `TestConnectionRemove`, `TestConnectionRemoveConsequenceCopy`, `TestDefaultEndpointFallback`, `TestMachineTableLayout` | 1,100 |
| `test_views_tables_and_picker.py` | `TestRegisteredTableLayout`, `TestModelConnectionPickerOrder`, `TestEngineEndpointsMap`, `TestImageGenerationNeedPhrase`, `TestTranscriptionNeedPhrase` | 780 |

**Depends on Tasks 14 and 17**, both of which edit this file. Run them first.

- [ ] **Step 1: Record the baseline**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests --collect-only | tail -3`
Write the collected count down. It is the only thing that proves this task moved code and nothing else.

- [ ] **Step 2: Move the nine helpers to `_helpers.py` first, in place**

Cut each from `test_views.py` into `models/registry/tests/_helpers.py`, add
`from models.registry.tests._helpers import (...)` to `test_views.py`, and run the suite. The file is
still one file; only the helpers moved. Expected: PASS, same count.

Give each moved helper a one-line docstring if it lacks one — they are public to the package now.

- [ ] **Step 3: Create the six files**

For each, in order: `git mv` is not usable (one file becomes six), so create the new file with the
module docstring, the shared import block, and the classes cut from the original — then delete those
classes from the original. Run `models/registry/tests` after **each** file. Six small red-green cycles
beat one large one.

Each new file's module docstring says what it covers and names its siblings, so a reader landing in
one knows the other five exist:

```python
"""<what this file covers>.

One of six files `models/registry/tests/test_views.py` split into (C-56):
`test_views_console_and_roles.py`, `test_views_reencode_and_sections.py`,
`test_views_machine_add_and_dropdowns.py`, `test_views_connection_edit.py`,
`test_views_engine_and_remove.py`, `test_views_tables_and_picker.py`. The
original was 7,606 lines and 41 classes; `tools/vision/tests/` is the
in-repo precedent for splitting one `views.py` module's tests by feature
area. Cut at class boundaries, on the file's own `# ---` dividers, with
no assertion changed.
"""
```

The shared import block is the original's lines 1–75 minus its module docstring — every one of the
six needs `Client`, `reverse`, `MagicMock`/`patch`, `models.registry.models` and
`models.registry.discovery`, and most need `models.contracts.roles`. After each file is created, run
`ruff` (or read the file) and delete the imports that file genuinely does not use.

- [ ] **Step 4: Delete the original**

When the last class has moved, `models/registry/tests/test_views.py` should be empty except for its
docstring and imports. `git rm` it.

- [ ] **Step 5: Confirm the count is identical**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests --collect-only | tail -3`
Expected: **exactly** the number from Step 1. Any difference means a class was dropped or duplicated.

Then run all four Global Constraint 3 runs — a split is precisely where a test that only passed
because of collection order shows itself.

- [ ] **Step 6: Full gate, then commit**

```
git add models/registry/tests
git commit -m "test(registry): the 7,606-line view suite splits into six by feature area"
```

Body:

```
C-56a. One test module carried 41 classes and 7,606 lines, against this
repo's own convention -- `tools/vision/tests/` already splits one views
module's tests into six files by feature area. Cut at class boundaries on
the file's own 31 dividers, contiguous and in the original order; the nine
module-level helpers move to the package's `_helpers.py`, because every
one of them is used from more than one of the six. Not one assertion
changed, and the collected test count is identical before and after.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

### Task 39: `tools/rag/tests/test_views.py` splits on its class boundaries (C-56b)

**Files:**
- Delete: `tools/rag/tests/test_views.py` (4,915 lines before Task 37; ~4,450 after)
- Create: four `tools/rag/tests/test_views_*.py` files
- Modify: `tools/rag/tests/_helpers.py` (absorb `bind_rag_roles`)
- Test: the moved modules are the tests

**The audit's correction is the method here.** Only the registry file has usable dividers; this one
has **exactly one** `# ---`, at `:4834`. So the split follows **class boundaries**, not dividers.

**Module-level helpers:** `client` (a fixture at `:49`) and `bind_rag_roles()` (`:53-72`, called at
`:121`, `:1397`, `:2102`, `:3292` — three of the four proposed files). Move `bind_rag_roles` to
`tools/rag/tests/_helpers.py`, which does not have it today. `client` is a one-liner; move it too,
for one home rather than four.

**The split, at class boundaries, contiguous:**

| New file | Classes | Approx. lines |
|---|---|---|
| `test_views_ask.py` | `TestAskPageView`, `TestAskView`, `TestAskEnqueue`, `TestAskViewConnectionOverride`, `TestAskJobStatus`, `TestSearchView` | 1,335 |
| `test_views_documents.py` | `TestDocumentFileView`, `TestChatScopedDocumentFileAccess`, `TestChatScopedDocumentsInTheLibrary`, `TestParseRange`, `TestDocumentFileRangeSupport`, `TestDocumentTranscriptView`, `TestDocumentsView`, `TestDocumentDelete`, `TestDocumentReingest` | 1,270 |
| `test_views_upload_and_settings.py` | `TestDocumentUpload`, `TestDocumentUploadCorruptPdf`, `TestCategoryManagement`, `TestHistoryView`, `TestLibrarySettingsView`, `TestTheHistoryPageIsPureHistory`, `TestHistorySettingsUpdate`, `TestUploadCapSettingsUpdate`, `TestMediaDurationSettingsUpdate`, `TestDocumentPagesSettingsUpdate` | 1,100 after Task 37 |
| `test_views_retrieval_settings_and_gating.py` | `TestRetrievalTopKSettingsUpdate`, `TestRetrievalScoreFloorSettingsUpdate`, `TestHybridSearchSettingsUpdate`, `TestEverySettingsFieldSharesThreeBehaviours` (Task 37's), `TestTheLibraryMutationsAreAdministration`, `TestTheUploadDoorObeysItsToolLabel`, `TestTheLibraryRenderGatesTheUploadForm`, `TestTheLibrarySettingsFormsAreAdminOnly`, `TestTheLibraryHidesActionsAMemberCannotTake` | 850 after Task 37 |

**Depends on Tasks 4, 9, 24, 27 and 37**, all of which edit this file. Run them all first — Task 37
especially, which removes ~450 lines from the third and fourth files' territory.

**Where this plan's own new tests land** after the split:
Task 4's two compaction pins and Task 9's two placement pins → `test_views_documents.py`.
Task 24's refusal-verb pin → `test_views_documents.py`. Task 27's `_upload_sha256` pin and the
watcher-race test → `test_views_upload_and_settings.py`.

- [ ] **Step 1: Record the baseline collected count**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests --collect-only | tail -3`

- [ ] **Step 2: Move `bind_rag_roles` and `client` to `_helpers.py`, in place**

Cut both from `test_views.py` into `tools/rag/tests/_helpers.py`, import them back, run the suite.
Expected: PASS, same count. `bind_rag_roles` gets a docstring on the way if it lacks one — it is
package-public now.

- [ ] **Step 3: Create the four files, one at a time**

Same method as Task 38: create with docstring + shared imports + the cut classes, delete those
classes from the original, run `tools/rag/tests`, repeat. The shared import block is the original's
lines 1–47 minus its docstring, trimmed per file afterwards.

Note the import at `:45` — after Task 27 it no longer names `_upload_sha256`, so carry whatever it
says at the time, not what it said when this plan was written.

Each docstring names its three siblings and says the split followed class boundaries because this
file, unlike the registry's, has exactly one divider.

- [ ] **Step 4: Delete the original** — `git rm tools/rag/tests/test_views.py`.

- [ ] **Step 5: Confirm the count and run all four gate runs**

Run: `DATABASE_URL='<TEST_DATABASE_URL>' FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests --collect-only | tail -3`
Expected: exactly Step 1's number.

Then all four Global Constraint 3 runs. The rag suite is the one that differs most between the two
feature-flag states, so the `FARABUNKER_FEATURES='vision'` run is not a formality here.

- [ ] **Step 6: Add the shape pin**

In `foundation/ops/tests/`:

```python
def test_no_test_module_grows_past_the_split_threshold():
    """C-56. Two `test_views.py` modules had reached 7,606 and 4,915
    lines. `tools/vision/tests/` is the in-repo precedent for splitting
    one views module's tests by feature area, and its largest file is
    2,014 lines -- so 2,100 is the threshold, chosen as "past the
    precedent" rather than as an aspiration nobody meets.

    KNOWN OVER, and deliberately not split by this plan: they are not in
    C-56, and one of them sits beside files held for another PR. A
    follow-up, named here so the number is a decision rather than an
    accident."""
    known_over = {
        "tools/rag/tests/test_jobs.py",
        "tools/rag/tests/test_media.py",
        "models/queue/tests/test_worker.py",
        "agents/chat/tests/test_sidebar.py",
    }
    offenders = []
    for path in REPO_ROOT.rglob("tests/test_*.py"):
        relative = str(path.relative_to(REPO_ROOT))
        if relative in known_over:
            continue
        if len(path.read_text().splitlines()) > 2100:
            offenders.append(relative)
    assert not offenders, f"test modules past the split threshold: {offenders}"
```

- [ ] **Step 7: Update the docs**

`docs/DEV.md` §7 ("Running the tests"): one sentence naming the convention — a column's view tests
split by feature area, and the gate that holds it.

- [ ] **Step 8: Full gate, then commit**

```
git add tools/rag/tests foundation/ops/tests docs/DEV.md
git commit -m "test(rag): the 4,915-line view suite splits into four by feature area"
```

Body:

```
C-56b. Cut at class boundaries rather than dividers: unlike the registry's
module, this one has exactly one `# ---`. Four files by feature area --
ask and search, documents, upload and settings, retrieval settings and
gating -- following `tools/vision/tests/`'s precedent. `bind_rag_roles`
and the `client` fixture move to the package's `_helpers.py`, since three
of the four files need the first. Not one assertion changed and the
collected count is identical. A threshold pin lands with it, naming the
four modules already over it that this plan deliberately does not touch.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
```

---

## Not planned, and why

Each of these was considered and left out deliberately. They are listed so a reader can see the line
between "not done" and "forgotten".

| Finding | Why not |
|---|---|
| **C-11** — N+1 on `Turn.invocation` in `agents/management/commands/agent_turn.py:308-312` | A one-line `.select_related("invocation")`, but no other task in this plan touches that file, and the brief admits C-11 only as a one-liner *inside* a task already editing it. The loop is bounded at `MAX_STEPS_DEFAULT`=8 in an operator CLI. Trivially addable to a future task that opens the file. |
| **C-12** — `_share_subject` re-resolves the principal its caller holds (`agents/chat/views/workstreams.py:715`, `:855`) | Same reason. No task here opens `agents/chat/views/workstreams.py` — Task 19's C-30 edits `agents/workstreams.py`, a different file with a similar name. |
| **C-13** — `Document.has_transcript` stats the filesystem per row | The audit's own ruling: no action today, bounded by `DOCUMENTS_PAGE_SIZE=25`. A watch item for any future full-table iteration. |
| **C-16** — the poll-until-terminal JS in three pages | Owner call, and an unresolved doctrine question in the prior digest. The frontend auditor's new proposal (an inline `pollUntilTerminal` in `_shell.html`'s existing `<script>`, which sidesteps the "no static JS pipeline" objection) is recorded in the audit for the owner to re-decide. Two of the three pages are held files. |
| **C-36** — the single-document label endpoint has no live UI caller | Owner decision. Deleting it also touches `identity/routes.py:151`, `identity/tests/test_route_matrix.py:199-200,711,759`, `identity/tests/test_routes.py:155,190,193`, all of `tools/rag/tests/test_document_label_page.py`, and `tools/rag/README.md:281`. Not a hygiene sweep's call. |
| **C-37** — `.pulse-dot` has JS and no CSS on `main` | Gated on PR #84: the rule **exists** on that branch at `conversation.html:127-133`. Port it there; do not delete the JS here. Both halves are also held files. |
| **C-38** — `.delete-disclosure` is dead in `chat/` | Gated on PR #84: **live** on that branch at `conversation.html:224`. The false test comment at `agents/chat/tests/test_thread.py:1165-1169` is a held file. |
| **C-48** — 15 functions over the complexity thresholds | Owner call, not a work item — the audit says so explicitly, and vision's auditor recommends *not* splitting `generate`/`submit_job` (ordered pipelines with load-bearing `except` ordering). Three of the fifteen are already digest rows. |
| **C-49** — `workstream_dormant.html` marks the whole page body `.dormant` | Owner decision: the mechanism is verified, the *intent* is not knowable from the code. |
| **C-53** — no job-progress reporting during chunk/embed | The audit files it as suspected, with no action. A feature request wearing a finding's clothes. |
| **C-55** — DRF is a whole framework for two JSON views | Owner decision about an offline-first product's dependency footprint. |
| **Digest RESIDUALS #3, B3 (small-caps recipe), B4 (ellipsis idiom)** | Named beside WP4 in the consolidated audit, but **neither that document nor `audit-frontend.md` carries a site list, a snippet, or a single line citation for B3 or B4** — they survive only as labels inherited from a prior digest this session does not have. A task cannot be written from a label. To plan them, someone needs to produce the source digest or re-run the grep that found them. |
| **`tools/rag/tests/test_jobs.py` (1512), `test_media.py` (1532), `models/queue/tests/test_worker.py` (1849), `agents/chat/tests/test_sidebar.py` (1699)** | All past Task 39's split threshold, none named in C-56, and `test_sidebar.py` sits beside held files. Task 39's pin names all four explicitly so the exemption is a recorded decision rather than an oversight. |

---

## Self-review

**1. Finding coverage.** Every finding in the brief's scope maps to a task:

WP1 — C-01 (1), C-02 (2), C-04 (3), C-51 (4).
WP2 — C-34 (6), C-35 (6), C-39 (7), C-40 (5), C-41 (5), C-42 (8), C-43 (9), C-50 (9), C-60 (9).
WP3 — C-07 (14, 15 — both halves).
WP4 — C-19 (31), C-20 (29), C-21 (30), C-22 (32), C-23 (32), C-24 (32), C-52 (30), gate (33).
WP5 — C-08 (25), C-14 (23), C-17 (24), C-18 (26), C-25 (27), C-31 media half (28), C-32 (24), C-46 (24).
WP6 — C-03 (13), C-05 (10), C-06 (12), C-09 (11).
WP7 — C-15 (22, health-check half only).
WP8 — C-56 (38, 39), C-57 (37), C-58 (36), C-59 (34), C-61 (35).
WP9 — C-26 (16), C-27 (17), C-28 (18), C-29 (19), C-30 (19), C-31 jobs half (20), C-33 (21),
C-44 (5), C-45 (5), C-47 (5), C-54 (5).

**49 findings, 39 tasks, no gaps** (C-07 and C-31 each split across two tasks; every other ID appears
exactly once). Nothing outside the brief's scope is planned; every exclusion is in "Not planned"
above.

**2. Ordering dependencies**, stated once so no task is run out of turn:

- Task 27 (C-25) repoints a call site **Task 23 moves**. Run 23 first.
- Tasks 23–28 are WP5's internal order; the brief's "C-14 first" is honoured (Task 23).
- Task 37 (C-57) shrinks the file **Task 39** splits. Run 37 first.
- Tasks 38 and 39 must run **after** every task that edits their file: 14 and 17 for the registry
  module; 4, 9, 24, 27 and 37 for the rag module.
- Task 33 (the CSS gate) must run **after** Tasks 29–32, so it is written against a tree that is
  already correct and its red output is new information rather than a restatement of the backlog.
- Task 36 (`models/contracts/tests/`) may absorb Task 34's helper test; if 34 runs first, park that
  test and move it in 36.
- Task 25 (C-08) uses `make_pdf_bytes`; if Task 35 has not run, use the module-local
  `_make_pdf_bytes` and let Task 35 repoint it.
- Task 24 (C-46) rewrites `tools/rag/ingest.py:816-818`; **Task 26 (C-18) then repoints the
  `sidecar_path = …` line inside that same rewritten region.** Run 24 first and re-derive.
- Task 32 (C-24) adds `_KNOWN_DUPLICATE_EXEMPTIONS` and seeds it with the `#84`-gated `chat/base.html` entry;
  **Task 33 must carry that entry across** or its gate cannot go green.

**Files an earlier task moves lines in, before a later task cites them** (Global Constraint 16 —
these are the five where it bites hardest):

| File | Moved by | Cited afterwards by |
|---|---|---|
| `tools/rag/ingest.py` | Task 23 inserts ~120 lines (`StageOutcome`, `stage_and_enqueue_one`) | Tasks 24 (`:816-818`, `:164`), 25 (`:312`, `:549`, `:837`, `:1202`, `:1297`), 26 (`:816`, `:1197`), 28 (`:1193-1196`, `:1276-1278`) |
| `tools/rag/views.py` | Task 23 collapses `:995-1152` from ~158 lines to ~30; Task 24 deletes `:109-124` | Tasks 26 (`:1465`), 27 (`:758-769`) |
| `models/registry/templates/inference/console.html` | Task 3 deletes `:124-128` | Tasks 29 (`:138-140`, `:162`), **30 (`:124-144` — literally the region Task 3 edits)**, 31 (`:929`), 32 (`:882-886`) |
| `models/registry/templates/inference/model_sets.html` | Task 3 deletes `:60-61` | Tasks 31 (`:76`), 32 (`:71`) |
| `tools/rag/templates/rag/documents.html` | Task 4 inserts ~12 comment lines at `:416-420`; Task 29 edits `:33-45` and `:220-223`; Task 30 deletes `:201-217` | Tasks 30 (`:201-217`), 32 (`:220-234`, `:590-594`) — WP4's most-edited file, and every citation in it moves at least once |

Every other task is independent of every other task.

**3. Name consistency across tasks.** The identifiers one task produces and another consumes:

| Produced by | Name | Consumed by |
|---|---|---|
| Task 1 | `tools.vision.store.media_type_for_upload`, `views._SERVABLE_IMAGE_TYPES` | — |
| Task 12 | `tools.rag.index.disposing_vector_store` | — |
| Task 14 | `models.registry.probe_cache.{CACHE_TTL_SECONDS, invalidate, health, cached}` | — |
| Task 15 | `tools.vision.probe_cache.{CACHE_TTL_SECONDS, invalidate, cached}` | — |
| Task 22 | `tools.rag.messages.unreachable_endpoints` | — |
| Task 23 | `tools.rag.ingest.{StageOutcome, stage_and_enqueue_one}` | Task 27 (the hash call site), Task 24 (the extension argument) |
| Task 26 | `tools.rag.store.{sidecar_path, work_dir}` | Task 28 (the media drivers' header) |
| Task 34 | `models.contracts.testing.registry_reset_fixture` | Task 36 (moves its test) |
| Task 35 | `tools.rag.tests._helpers.make_pdf_bytes` | Task 25 (the PDF fixture) |
| Task 37 | `_SETTINGS_FIELDS`, `TestEverySettingsFieldSharesThreeBehaviours` | Task 39 (places the class) |

`ingest._fail_document` (Task 28) and `jobs._fail_stranded_rows` (Task 20) are deliberately different
functions with similar names; both docstrings say so.

**4. Placeholder scan.** Three places in this plan say "read the current code before writing this",
and each is deliberate rather than a gap: Task 13's `DocumentVisibility` constructor fields, Task 18's
audit action constants, and Task 20's debug/warning branch conditions. In all three the *shape* of
the change is fully specified and only an identifier has to be read out of the file — and in all three
guessing the identifier would be worse than naming the file to read it from. Every other code step
carries the actual code.

Two numbers are deliberately left to be measured rather than asserted:
`_BULK_LABEL_BASELINE_QUERIES` (Task 10) and the collected-test counts (Tasks 38, 39). Each carries
the exact procedure for obtaining it. A query count guessed in a plan is a plan telling an executor
to make the test match the guess.

---

## Plan review

**Round 1 — AMEND.** One adversarial reviewer, read-only, resolved all 403 `path:line` citations
against the worktree and hand-checked the load-bearing ones. Value/YAGNI came back **clean** (no step
outside the named finding set; all eleven exclusions accounted for). The findings, all verified
against the real code before amending and all now applied:

*Blocking mechanics*
- **Task 32/33** — `.delete-disclosure > summary` has **three** copies, not two: the third is
  `agents/chat/templates/chat/base.html:1531-1533`, inside the block Global Constraint 11 forbids
  touching. Both the pin and the generalized gate would have been unsatisfiable. Resolved by
  promoting for the two vision templates and seeding a new, separately-keyed `_KNOWN_DUPLICATE_EXEMPTIONS` with a dated,
  `#84`-scoped exemption that Task 33 carries across.
- **Task 3** — the existing settings gate matches a **direct** `{% extends "_settings.html" %}` only,
  and both registry pages reach it through `inference/base.html`, so the planned red-green could
  never have gone red. Replaced with a source-text pin; Task 33 makes the transitive case permanent.
- **Task 4** — `col-select` also fails the compaction pin; it carries `width: 1.5rem` of its own at
  `documents.html:457-461`. Pin now exempts columns that declare their own width.
- **Task 19** — `assert "transaction.atomic" not in source` fails on `taint.py:5`, which names it in
  prose. Now a line-anchored regex.
- **Task 25** — widening `_check_document_pages`'s and `stage_document`'s returns would have broken
  ~14 and ~41 assertion sites respectively. Rewritten as an out-parameter; both return shapes stay.
- **Task 14** — `apps.py` connects `invalidate_after_commit` on **three** receivers, with no
  `post_save` for `ModelConnection`; mirroring it would have left the task's own invalidation test
  red. All four receivers now named explicitly.
- **Task 12** — `index.py:251` is `get_storage_context`, which Task 6 deletes, not a fallback. Two
  fallbacks named, with an instruction to re-derive after Task 6.

*Ordering* — files where an earlier task moves lines a later task cites; added as Global
Constraint 16 and a table in Self-review §2, plus the Task 24→26 and Task 32→33 dependencies.

*Citations* — corrected: `access.py`'s factory (`:163-195`, and `DocumentVisibility` has neither
`sees_all_content` nor `owner_kind`/`owner_key` — `unrestricted` **is** the flag, and the two
identity fields must be added); `machine_model_add`'s check (`:1868-1873`);
`test_unregistered_engine_is_rejected_cleanly` (`:3296`); `_check_document_pages`'s def (`:312`);
`may_post_to_conversation`'s stale docstring (`:442`); `chat/_messages.html`'s consumers (**six**,
not three); `.muted`'s split (**seven** at .85rem + one at .9rem = eight);
`_redirect_console`'s callers (**40**); `IDENTITY_PERMITTED`/`AGENTS_PERMITTED` (`:606`, `:835`);
`store.py` 82 lines; `jobs.py` 873 (the round-1 note saying 874 was itself wrong). **Two operator-facing strings use typographic quotes** (`“ ”`)
and were being retyped as ASCII — corrected in place and raised to Global Constraint 17.

*Counting* — 48 → **49** findings. The coverage table itself was already correct.

*Simplicity* — Task 11's entry point is `unlabel_all_for_entitlement(entitlement_id, *, commit)`
(`labels.py:252`), not the placeholder name; Task 14's `cached()`/`health()` split is now specified
once rather than written then rewritten; Task 18 gains the **third** single-row reader
(`labels_for`, `agents/labels.py:34-39`); Task 24 also fixes `ingest.py:164`'s docstring, which would
otherwise name the function it deletes; Task 20's logging decision is made in the plan (it stays at
each call site, and `_fail_stranded_rows` loses its now-unneeded `what` parameter).

**Round 2 — not yet run.** The amendments above are the author's response to round 1. A scoped
re-check of the amended sections is the next step before execution begins.

**Round 2 — AMEND, seven defects, all applied.** Scoped re-check of the amended sections only.
Encoding verified clean (valid UTF-8, no mojibake; em-dashes and typographic quotes intact), and
fourteen of the round-1 amendments re-verified correct against the code. The seven that were not:

1. **Task 3's `model_sets.html` half still could not go red.** `_rules` compares whitespace-
   normalised *(selector, body)* pairs, and `model_sets.html:60-61`'s `.msg` body differs from
   `_settings.html`'s in decimal spelling, declaration order and one extra declaration — so a
   rule-level intersection is empty there and the pin would have gone green with the restatement
   still in place. The pin is now **selector-level**, scoped to `{".messages", ".msg"}` so the two
   genuine local variants survive. Step 4's stated mechanism was also wrong (`_style_block` reads
   only the leaf's own literal text, so `{{ block.super }}` changes nothing the parser sees — it
   makes the restatement *wrong*, not *visible*); corrected.
2. **`console.html` off-by-one.** `.messages` is `:124-128` (the closing brace is on 128), and the
   `.msg` block to keep is `:129-144`. Deleting `:124-127` would have left a stray `}`.
3. **Task 12's commit body** still said "three internal fallbacks"; it is two.
4. **`jobs.py` is 873 lines, not 874** — round 1's correction was itself wrong, and
   `on_consolidate_terminal` ends at `:873`, not `:874`.
5. **Task 25's site list** included three `def` lines and omitted five real call sites. Replaced
   with the ten genuine direct-return assertions; the out-parameter design itself was confirmed
   correct.
6. **`_KNOWN_FALSE_POSITIVES` was the wrong container** for the C-24 exemption: it is keyed
   *(fragment filename, bare class name)* and read only by the fragment/leaf-page gate
   (`test_css_ownership.py:260`). Seeding it with template paths and selectors would have put
   entries where nothing reads them and falsified its documented contract. C-24's exemption now
   goes in a new, separately-keyed `_KNOWN_DUPLICATE_EXEMPTIONS`, and Task 33 carries that.
7. **Two ordering-table gaps**: Task 30 also cites `console.html:124-144` (the region Task 3
   edits), and `tools/rag/templates/rag/documents.html` — WP4's most-edited file, touched by Tasks
   4, 29, 30 and 32 in that order — was missing entirely. Both added; the table is five files.

**Round 3 — not run.** Rounds 1 and 2 between them checked every citation in the plan and the
amendments to it; the seven defects above were mechanical and their fixes are verifiable by reading
the amended text against the files named. Execution should still begin with Task 1 and treat Global
Constraint 16 (re-derive every line number before editing) as binding, which is the standing
protection against exactly the class of error both rounds found.
