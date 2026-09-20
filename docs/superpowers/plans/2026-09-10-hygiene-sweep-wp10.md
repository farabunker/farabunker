# Repo-Wide Code-Hygiene Sweep — WP10 Addendum (Tasks 40–45)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-09-10
**Branch:** `worktree-hygiene-sweep`, worktree `.claude/worktrees/hygiene-sweep`
**Base:** the head of the parent plan's Task 39 (`be7be9b` + Task 38/39). **Not** `main @ 08e28c6`.
**Status:** plan, not executed

**Goal:** Land the six WP10 findings the owner released on 2026-09-10 — the dormant 403 page's
whole-body red, the orphan single-document label endpoint, the Django REST Framework dependency
kept for two JSON views, the three-way poller duplication, and the two functions with genuine
phase boundaries — with no change to a single documented behaviour.

**Architecture:** Six tasks, ordered smallest-blast-radius first. Task 40 is one template line and
one regression test. Task 41 is a verified deletion with an eleven-site grep list. Task 42 is a
characterize-then-rewrite dependency removal. Tasks 43–45 are the three refactors, each of which
writes characterization tests *before* it moves a line of production code. Every task is
independently testable, ends green, and carries its own tests and docs.

**Tech Stack:** Django 5.1 (server-rendered; every inline `<style>`/`<script>` lives in a
template, there is no static pipeline), Postgres + pgvector, pytest + pytest-django, ruff.

**Parent plan:** `docs/superpowers/plans/2026-09-09-hygiene-sweep.md`. **This addendum inherits
that plan's entire "Global Constraints" section (lines 33–126) unchanged** — worktree only; tests
against `<TEST_DATABASE_URL>`, never `localhost:5432`, never a bare `test_farabunker`; tests and
docs in the same commit; regression test first, watched fail; `django_assert_num_queries` for PERF
findings; the import law and its three gate tests; no new static CSS/JS pipeline; no AI model names
in docs; the four files held for PR #88; the `#84`-gated `.delete-disclosure` block; conventional
scoped commit messages with the two trailers; nothing merged to `main`; no hand-written database
edits; every `path:line` re-derived with `git grep -n` before editing; verbatim means verbatim,
typographic quotes included.

**Spec:** `<scratchpad>/CONSOLIDATED-AUDIT.md`
rows `C-49`, `C-36`, `C-55`, `C-16`, `C-48`, plus the per-zone evidence in `audit-agents-chat.md`
(F5), `audit-tools-rag.md` (V9), `audit-frontend.md` (F5), `audit-models.md` (F3/F4) and
`audit-tooling.md` (T5) beside it. The binding rulings are the `## WP10 rulings` section at the
foot of `.superpowers/sdd/2026-09-09-hygiene-sweep/progress.md`.

---

## Addendum-specific constraints

These are **additional** to the parent plan's Global Constraints, not a replacement.

A1. **The peer splits.** `models/registry/tests/test_views.py` **has already been split** — parent
    plan Task 38 landed as commit `04d13c1` ("the 7,606-line view suite splits into six by feature
    area") and that file **no longer exists**. Task 45's Files list names the six successors it
    actually needs. `tools/rag/tests/test_views.py` **has now been split too** — parent plan Task 39
    landed while this addendum was being written, replacing it with `test_views_ask.py`,
    `test_views_documents.py`, `test_views_retrieval_settings_and_gating.py` and
    `test_views_upload_and_settings.py`.

    **Both peer splits are therefore done.** This plan still cites nothing in either by line
    number — only by `class Test…` or module-level `def test_…` — and every task that touches
    them keeps its locating `git grep` step. Run those greps rather than trusting the file names
    below: Task 39 landed *after* they were measured, and a follow-up commit could still move a
    class between the four new files.

    Where the `tools/rag` classes this plan depends on live after Task 39 — verified:

    | class / function | file |
    |---|---|
    | `TestAskView`, `TestAskEnqueue`, `TestAskViewConnectionOverride`, `TestAskJobStatus` | `tools/rag/tests/test_views_ask.py` |
    | `test_each_administer_refusal_names_its_own_verb` (the C-32 verb table) | `tools/rag/tests/test_views_documents.py` |

    Where `TestConnectionAdd` and friends live after `04d13c1` — verified, not assumed:

    | class | file |
    |---|---|
    | `TestConnectionAdd`, `TestStampFirstMaterialization` | `models/registry/tests/test_views_reencode_and_sections.py` |
    | `TestConnectionEdit`, `TestManualFormPrefill` | `models/registry/tests/test_views_connection_edit.py` |
    | `TestCapabilityCheckboxGroups`, `TestEngineProfileAutofill` | `models/registry/tests/test_views_engine_and_remove.py` |

A2. **The held-file list is load-bearing for Task 43.** `agents/chat/templates/chat/conversation.html`
    and `agents/chat/tests/test_thread.py` are held for PR #88. `test_thread.py` asserts
    `body.count("<script") == 3` and `== 4` on the conversation page. **Any change that adds a
    `<script>` tag to every page in the box breaks a held file and is therefore forbidden.** Task 43
    is designed around this; read its "Why an include, not a shell-level script" note before
    touching it.

A3. **Base commit.** These tasks assume Tasks 1–39 of the parent plan are landed. Task 42 and
    Task 43 both touch files Tasks 22, 29–33 already changed. Re-derive every location with
    `git grep -n` before editing — the line numbers in this document were measured on the worktree
    at `be7be9b` and will move.

A4. **`git` prefix.** Every git command: `git -C <WORKTREE_ROOT> …`.

A5. **Commit trailers.** Every commit message in this plan ends with:
    ```
    Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_<id>
    ```
    The bodies shown below are the message *above* those two lines.

---

## Task list at a glance

| # | Finding | One line |
|---|---------|----------|
| 40 | C-49 | The dormant 403 page marks the status word, not its whole body |
| 41 | C-36 | The single-document label endpoint and its eleven references go |
| 42 | C-55 | Django REST Framework leaves the box; two JSON views keep their contract |
| 43 | C-16 | One shared `pollUntilTerminal`; `ask.html` and `create.html` call it |
| 44 | C-48a | `Worker._evict_to_match_plan` becomes four named phases |
| 45 | C-48b | `connection_add` becomes a validation step and a write step |

---

### Task 40: The dormant 403 page marks the status word, not its whole body (C-49)

**Files:**
- Modify: `agents/chat/templates/chat/workstream_dormant.html` (the `<article class="dormant">`
  wrapper — currently the first line inside `{% block content %}`)
- Test: `agents/tests/test_workstream_sharing.py` (beside
  `test_the_403_page_reveals_nothing_about_the_streams_contents`)

**Not touched:** `agents/chat/templates/chat/base.html` (the `.dormant` rule is correct and stays
verbatim), `agents/chat/README.md` (**held for PR #88** — and no change is needed: the README's
§"The dormant marker reads as a warning consistently" already documents `.dormant` as a small
inline marker, and this task makes the code match that documentation rather than changing it).

**Column:** everything stays inside `agents` / `agents.chat`. No new import.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: nothing later tasks rely on.

**The finding.** `.dormant` is defined once, in `chat/base.html`:

```css
.dormant { flex: 0 0 auto; color: var(--danger); }
```

It has three consumers. Two are the short inline marker the rule was written for —
`chat/_sidebar.html`'s `<span class="dormant">Dormant</span>` and
`chat/workstream_settings.html`'s `<strong class="dormant">Dormant</strong>`. The third is
`workstream_dormant.html`'s top-level `<article class="dormant">`, which wraps an `<h1>`, a
`.lede`, two prose paragraphs and a link. `color` is an inherited property and nothing below
overrides it, so the *entire* 403 page's body text renders in `var(--danger)`. The rule's other
half (`flex: 0 0 auto`) is inert there — the article is not a flex child of anything — which is
the extra sign that this is incidental class reuse rather than a considered design.

**Ruling (binding).** *"`workstream_dormant.html` must not colour its whole body with `.dormant`;
scope the marker to the status word only, matching the sidebar's use."*

**Re-derive before editing.** `git grep -n 'class="dormant"' -- agents/chat/templates` and
`git grep -n '\.dormant' -- agents/chat/templates/chat/base.html`. Task 32 of the parent plan moved
CSS around in `base.html`; the `.dormant` rule was not in its scope, but confirm the rule is still
there and unchanged before asserting anything about it.

- [ ] **Step 1: Write the failing regression test**

Add to `agents/tests/test_workstream_sharing.py`, immediately after
`test_the_403_page_reveals_nothing_about_the_streams_contents`. Every helper it needs
(`make_user`, `make_entitlement`, `posture`, `sign_in`, `user_principal`, `_workstream`,
`owner_fields`, `Share`, `WorkstreamTaint`, `reverse`) is already imported at the top of that
module — add no imports.

```python
def test_the_dormant_page_marks_the_status_word_not_its_whole_body(client):
    """C-49. `.dormant` is `chat/base.html`'s SMALL INLINE MARKER --
    `color: var(--danger)` on one word beside a name (`_sidebar.html`,
    `workstream_settings.html`), and `agents/chat/README.md` documents it
    as exactly that. Applied to this page's top-level `<article>` it
    inherited into the `<h1>`, the lede and the three paragraphs below it, so the
    whole 403 page read as an error rather than the stream's status
    reading as one. The class stays; the element it hangs on changes."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert response.status_code == 403
    body = response.content.decode()
    # The page body is NOT the marker.
    assert '<article class="dormant">' not in body
    # The marker is still on the page, on one word, the same shape
    # `_sidebar.html` uses for the same fact about the same stream.
    assert '<span class="dormant">Dormant</span>' in body
    # And the fence still holds: the name is shown, nothing else is.
    assert "Q3" in body
    assert "Traceback" not in body
```

- [ ] **Step 2: Run it and watch it fail**

```bash
export DATABASE_URL='<TEST_DATABASE_URL>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  agents/tests/test_workstream_sharing.py::test_the_dormant_page_marks_the_status_word_not_its_whole_body
```

Expected: FAIL on the first assertion — `'<article class="dormant">' not in body` is False.

- [ ] **Step 3: Change the two lines in the template**

In `agents/chat/templates/chat/workstream_dormant.html`, inside `{% block content %}`:

Current:
```html
<article class="dormant">
  <h1>{{ workstream.name }}</h1>
  <p class="lede">This workstream is not readable right now.</p>
```

New:
```html
<article>
  <h1>{{ workstream.name }}</h1>
  <p class="lede"><span class="dormant">Dormant</span> — this workstream is not readable right now.</p>
```

The em dash is U+2014, matching `workstream_settings.html`'s own
`— <strong class="dormant">Dormant</strong>: that account no longer holds …`. Copy it from that
file rather than retyping it (Global Constraint 17).

- [ ] **Step 4: Add the template's own note**

Inside the same file's existing `{% comment %}` block, immediately before the closing
`{% endcomment %}`, append this paragraph — the file already explains every other decision it
makes, and a future reader will otherwise ask the same "is this a bug" question the auditor did:

```
THE `.dormant` MARKER IS ONE WORD, NOT THIS PAGE. `chat/base.html`'s
rule is `color: var(--danger)` and `color` INHERITS -- on the
top-level `<article>` (where this page used to carry it) it turned the
stream's own name, the lede and the three paragraphs below it red, which is not
what a marker written for a `<span>` beside a name in `chat/
_sidebar.html` means. The class is the sidebar's; so is the shape it
hangs on.
```

- [ ] **Step 5: Run the test and watch it pass**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  agents/tests/test_workstream_sharing.py::test_the_dormant_page_marks_the_status_word_not_its_whole_body
```

Expected: PASS.

- [ ] **Step 6: Run the task's exit gate**

Per the orchestrator's standing per-task-gate ruling — the touched column's whole test dirs plus
the three repo-wide gates:

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/tests agents/chat/tests foundation/ops/tests
```

Expected: all pass. `foundation/ops/tests/test_css_ownership.py` is included because the marker's
rule lives in `chat/base.html` and that gate now walks the whole repo; nothing in this task changes
a CSS rule, so it must stay green untouched.

- [ ] **Step 7: Commit**

```bash
git -C <WORKTREE_ROOT> add \
  agents/chat/templates/chat/workstream_dormant.html agents/tests/test_workstream_sharing.py
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
fix(chat): the dormant 403 page marks the status word, not its whole body

C-49. `.dormant` is `chat/base.html`'s small inline marker -- one word
beside a name, `color: var(--danger)`, documented as exactly that. On
`workstream_dormant.html`'s top-level `<article>` the inherited colour
turned the stream's own name, the lede and the three paragraphs below it red. The
marker moves to the lede's status word, the shape `chat/_sidebar.html`
already uses for the same fact about the same stream; the CSS rule is
untouched.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 41: The single-document label endpoint and every reference go (C-36)

**Files:**
- Modify: `tools/rag/views.py` — delete `document_labels_update` and `_LABEL_FORBIDDEN_MESSAGE`
- Modify: `tools/rag/urls.py` — delete the import, the `path(...)` and its two-sentence comment
- Modify: `identity/routes.py` — delete the `"rag-document-labels": "R"` entry and rewrite the
  bulk entry's comment, which refers to it
- Modify: `identity/tests/test_routes.py` — delete `test_the_document_label_route_is_R`, edit the
  block-level pin, rewrite `test_the_bulk_label_route_is_R`'s docstring
- Modify: `identity/tests/test_route_matrix.py` — delete the `_DRIVERS` entry, remove the name
  from `_ROW_ADDRESSED_R` and `_OWNER_WIDENED`, rewrite the three comment blocks that name it
- Modify: `tools/rag/tests/test_document_label_page.py` — delete `TestTheLabelRoute` and the four
  other single-route call sites (by class name, see the grep list)
- Modify: `tools/rag/tests/test_views_documents.py` — drop the `("rag-document-labels", "Labelling")` row
  from the module-level parametrized `test_each_administer_refusal_names_its_own_verb` and edit the
  `# --- C-32: the gate prologues ---` comment block above it (**cite by name, never by line — this
  file is being split**)
- Modify: `tools/rag/README.md` — delete the `rag-document-labels — the per-document route…`
  paragraph
- Modify: `tools/rag/ingest.py`, `tools/rag/access.py`, `identity/access.py` — three prose
  references in docstrings that name the deleted view
- Test: `tools/rag/tests/test_document_label_page.py` (the new absence pin)

**Not touched:** `docs/adr/0016-identity-and-entitlements.md` and everything under
`docs/superpowers/`. ADRs and landed plans are dated records of decisions that were true when
written; this plan does not rewrite history. The live-docs rule covers `README.md`, `docs/DEV.md`
and `docs/ARCHITECTURE.md`, and only `tools/rag/README.md` mentions the route.

**Column:** `tools/rag`, `identity` **and `models.registry`** each edit their own files — the last
one is two docstring lines only (grep rows 15/16), but it is a third column and it overlaps
Task 45's file. No new import in any direction, and no code moves between columns.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the URL name `rag-document-labels` no longer exists. Any later task that reverses it
  will raise `NoReverseMatch`.

**The finding.** `git grep -n 'rag-document-labels'` (excluding `-bulk`, `.git`, and
`docs/superpowers/`) hits eleven live locations and **no template**. Every `{% url %}` under
`tools/rag/templates/` reverses `rag-document-labels-bulk` only, and a test already pins that —
`TestTheCategoryBulkControl` asserts
`body.count(f'action="{reverse("rag-document-labels-bulk")}"') == 1` on the rendered library page,
so the page has exactly one label-writing form and it is the bulk one. The per-row
`<select multiple>` + Save that used to POST to the single-document route was replaced by the
"With selected…" bulk card.

**Ruling (binding).** *"Delete `document_labels_update` / the `rag-document-labels` route and every
reference — the bulk endpoint is the only UI caller. Cost if wrong: an external caller of the
single-doc endpoint breaks; none known."*

**The grep list — every live reference, and what happens to it.**

Run this first and reconcile it against the table; the counts must match or the task stops and
reports:

```bash
git -C <WORKTREE_ROOT> grep -n 'rag-document-labels' \
  -- ':!docs/superpowers' ':!*.pyc' | grep -v 'rag-document-labels-bulk'
git -C <WORKTREE_ROOT> grep -n 'document_labels_update'
git -C <WORKTREE_ROOT> grep -n '_LABEL_FORBIDDEN_MESSAGE'
```

| # | Location | Disposition |
|---|----------|-------------|
| 1 | `tools/rag/views.py` `def document_labels_update` (+ its `@require_POST`) | delete the function |
| 2 | `tools/rag/views.py` `_LABEL_FORBIDDEN_MESSAGE = (…)` | delete — its only reader is #1 |
| 3 | `tools/rag/views.py` `workstream_pin`'s docstring, "`rag-document-labels` is the nearest precedent…" | rewrite: name `rag-document-labels-bulk` instead |
| 4 | `tools/rag/views.py` `document_labels_bulk`'s docstring, "A SECOND URL, not a wider `rag-document-labels`…" | rewrite: past tense, see Step 5 |
| 5 | `tools/rag/urls.py` — the `document_labels_update` name in the `from tools.rag.views import (…)` list | delete the name |
| 6 | `tools/rag/urls.py` — the `path("documents/<int:doc_id>/labels/", …)` + the `# A SECOND URL, never a wider …` comment | delete both |
| 7 | `identity/routes.py` — `"rag-document-labels": "R",` and the two-line comment above `"rag-document-labels-bulk"` that names it | delete the entry; rewrite the comment |
| 8 | `identity/tests/test_routes.py` — `TestCoverage`-adjacent block pin (`ROUTE_RULES.get("rag-document-labels", "R") == "R"`), `test_the_document_label_route_is_R`, `test_the_bulk_label_route_is_R`'s docstring | see Step 6 |
| 9 | `identity/tests/test_route_matrix.py` — `_DRIVERS["rag-document-labels"]`, `_ROW_ADDRESSED_R`, `_OWNER_WIDENED`, and the `_LIBRARY_MUTATIONS` comment's long "`rag-document-labels` IS NOT HERE" paragraph | see Step 7 |
| 10 | `tools/rag/tests/test_document_label_page.py` — `TestTheLabelRoute` (two of its three methods; the third MOVES) plus the single-route post in `TestTheCaseTheSplitExistsFor` and the rejected-control docstring in `TestTheLabelsColumnReadsAndTheCardWrites` | see Step 8 |
| 11 | `tools/rag/tests/test_views_documents.py` — the `("rag-document-labels", "Labelling")` parametrize row in `test_each_administer_refusal_names_its_own_verb`, and the `# --- C-32 …` comment above it | see Step 9 |
| 12 | `tools/rag/README.md` — the "`rag-document-labels` — the per-document route with SET semantics…" paragraph | delete the paragraph |
| 13 | `identity/access.py:365` — *"`tools.rag.views.document_labels_update`, `agents.chat.views.tools`"*, a list of routes sharing one predicate | mechanical rename to `document_labels_bulk` |
| 14 | `identity/services.py:376` — `may_administer_entitlement`'s *"ONE PREDICATE, THREE COLUMNS … `tools.rag.views.document_labels_update` and `agents.chat.views.tools.tool_entitlements`"* | mechanical rename — the sentence is about the predicate, which is unchanged |
| 15 | `models/registry/views.py:2457` — *"the same shape `tools/rag/views.py::document_labels_update` checks a submission against `labelling_entitlements(principal)`"* | mechanical rename — `document_labels_bulk` checks against the same function |
| 16 | `models/registry/views.py:2563` — *"The same shape `tools/rag/views.py::document_labels_update` already uses for the identical reason."* | mechanical rename |
| 17 | `tools/rag/access.py:774` and `tools/rag/ingest.py:700` — two docstrings about the **403 shape**, not the predicate | **rewrite, never rename** — see Step 10 |
| 18 | `tools/rag/views.py:683` and `:690` — `document_labels_bulk`'s own docstring says *"the same `may_label_document` **the single-document route** uses"* and *"exactly as **the single-document route** calls it"* | rewrite in Step 5 — **these name no symbol**, so neither grep finds them |

Rows 1–18 are **eighteen-plus edits across thirteen files** (row 13's group is several edits on its
own). **Nothing else in the repo may match after the task**, except `docs/adr/` and
`docs/superpowers/`.

**Three greps, not one, because they have different answers.** The URL-name grep misses every
reference that names the *function*, and both name-greps miss row 18, which describes the route in
prose without naming anything:

```bash
git -C … grep -n 'rag-document-labels' -- ':!docs/superpowers' | grep -v 'rag-document-labels-bulk'
git -C … grep -n 'document_labels_update'
git -C … grep -n 'single-document route' -- tools/rag/
```

**Two consequences the first draft of this task got wrong, and an implementer must not repeat.**

1. **This task touches three columns, not two.** Rows 15 and 16 are in `models/registry/views.py`.
   That is a docstring-only edit in a file this addendum's **Task 45 also rewrites**, so if the two
   run out of order or in parallel the second must re-derive its locations with `git grep -n`
   before editing. Declared here rather than discovered later.
2. **Rows 14–17 name no URL name**, which is why the `rag-document-labels` grep does not find them.
   Run the `document_labels_update` grep as a *separate* reconciliation — the two greps have
   different answers, and the function-name one is the larger.

- [ ] **Step 1: Write the failing absence pin**

Add at the **end** of `tools/rag/tests/test_document_label_page.py`, as a module-level function
(not in a class — it needs no fixtures):

```python
def test_the_single_document_label_route_is_gone():
    """C-36. The per-document SET-semantics route had no live caller: no
    template ever reversed it, and the library's one writing surface is
    the bulk card (`rag-document-labels-bulk`, ADD/REMOVE semantics). Two
    routes that answer the same predicate with different destructiveness
    is exactly the ambiguity `document_labels_bulk`'s own docstring says
    a bulk control must never carry -- and the one that survived is the
    one the page actually posts to."""
    from django.urls import NoReverseMatch, reverse

    from identity.routes import ROUTE_RULES
    from tools.rag import views as rag_views

    with pytest.raises(NoReverseMatch):
        reverse("rag-document-labels", args=[1])
    assert "rag-document-labels" not in ROUTE_RULES
    assert not hasattr(rag_views, "document_labels_update")
    assert not hasattr(rag_views, "_LABEL_FORBIDDEN_MESSAGE")
    # The survivor, asserted in the same breath so this pin can never be
    # satisfied by deleting the whole feature.
    assert reverse("rag-document-labels-bulk") == "/rag/documents/labels/"
    assert ROUTE_RULES["rag-document-labels-bulk"] == "R"
```

- [ ] **Step 2: Run it and watch it fail**

```bash
export DATABASE_URL='<TEST_DATABASE_URL>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  tools/rag/tests/test_document_label_page.py::test_the_single_document_label_route_is_gone
```

Expected: FAIL — `reverse("rag-document-labels", args=[1])` succeeds, so `pytest.raises` raises
`Failed: DID NOT RAISE`.

- [ ] **Step 3: Delete the view and its message**

In `tools/rag/views.py`, delete the whole `@require_POST def document_labels_update(request,
doc_id: int): …` block (from its decorator through its final
`return redirect("rag-documents")`), and delete the `_LABEL_FORBIDDEN_MESSAGE = (…)` assignment.
Leave `_DELETE_FORBIDDEN_MESSAGE`, `_REINGEST_FORBIDDEN_MESSAGE` and `_UPLOAD_FORBIDDEN_MESSAGE`
exactly as they are.

**Do not remove any import.** `listable_documents`, `may_label_document`, `document_label_ids`,
`set_document_labels` and `labelling_entitlements` all keep other callers in the same module
(`DocumentsView`, `document_labels_bulk`, `document_upload`). Prove it before you touch the import
block:

```bash
git -C <WORKTREE_ROOT> grep -n \
  'listable_documents\|may_label_document\|document_label_ids\|set_document_labels\|labelling_entitlements' \
  -- tools/rag/views.py
```

- [ ] **Step 4: Delete the route**

In `tools/rag/urls.py`: remove `document_labels_update,` from the import list, and remove

```python
    path("documents/<int:doc_id>/labels/", document_labels_update,
         name="rag-document-labels"),
    # A SECOND URL, never a wider `rag-document-labels`: the route above
    # sets a document's labels to exactly what was submitted, and this
    # one adds or removes across many at once (decision 29) -- one URL
    # must never be ambiguous about whether a POST is destructive.
```

leaving `path("documents/labels/", document_labels_bulk, name="rag-document-labels-bulk"),` in
place with this comment above it instead:

```python
    # THE ONE LABEL WRITER. It ADDS or REMOVES across many documents
    # (never "set to exactly this") -- decision 29's ambiguity rule, now
    # trivially satisfied because there is only one route left to be
    # ambiguous about. The per-document SET route that used to sit above
    # this one was deleted (C-36): no template ever reversed it.
```

- [ ] **Step 5: Rewrite the two surviving docstring references in `views.py`**

`workstream_pin`'s docstring currently reads *"`rag-document-labels` is the nearest precedent for a
`tools/rag` route whose predicate is not about reading the document's bytes"*. Replace the route
name with `rag-document-labels-bulk` — the surviving route has the identical predicate, so the
sentence stays true.

`document_labels_bulk`'s docstring currently opens its third paragraph with *"A SECOND URL, not a
wider `rag-document-labels`: that route sets a document's labels to EXACTLY what was submitted;
this one ADDS OR REMOVES across many."* Replace that paragraph with:

```
ADD OR REMOVE, NEVER "SET TO EXACTLY THIS". This was once one of two
label routes, and the per-document one carried SET semantics -- a
hidden field deciding whether a POST is destructive was the thing
decision 29 refused, so they were two URLs. The SET route had no
caller and is gone (C-36); this endpoint's semantics are unchanged,
and they are now the only ones there are. Somebody selecting forty
documents to add one label must not silently strip the labels those
documents already carry.
```

**Two more sentences in the same docstring (grep row 18), further down**, which describe the
deleted route without naming it, so neither name-grep finds them. Re-derive with
`git grep -n 'single-document route' -- tools/rag/`:

- *"`may_label_document` below is the ONLY gate, checked PER DOCUMENT … with the same
  `may_label_document` **the single-document route** uses"*
- *"exactly as **the single-document route** calls it"*

Read each surrounding sentence and repoint the comparison at a caller that still exists
(`DocumentsListView` renders the Labels column from the same predicate) — or, where that would make
the sentence say something untrue, drop the comparison clause rather than inventing a referent.

Then delete the now-duplicated *"ADD OR REMOVE, never 'set to exactly this'."* paragraph that
follows it, so the docstring says it once.

- [ ] **Step 6: Update `identity/routes.py` and `identity/tests/test_routes.py`**

In `identity/routes.py`, delete `"rag-document-labels": "R",               # -- admin, or an
entitlement owner` and replace the comment above the bulk entry:

```python
    # ONE ACTION, NOT N (spec section 22.34): admin, or an owner of every
    # entitlement being added or removed, applied per document -- no row
    # in its own URL, so it stays on the base R mapping rather than
    # `_ROW_ADDRESSED_R`/`_LIBRARY_MUTATIONS` in the route matrix.
    "rag-document-labels-bulk": "R",
```

In `identity/tests/test_routes.py`:

1. In the block-level pin, delete the line
   `assert ROUTE_RULES.get("rag-document-labels", "R") == "R"      # Task 12 adds it` and, in the
   same test's docstring, change *"the two routes arrive in later tasks"* to *"the route arrives in
   a later task"* and drop *"Task 12"* from the *"Each is also asserted in the task that adds it"*
   sentence.
2. Delete `test_the_document_label_route_is_R` entirely.
3. Replace `test_the_bulk_label_route_is_R`'s docstring (which currently says "The SAME predicate
   as `rag-document-labels`") with:

```python
    def test_the_bulk_label_route_is_R(self):
        """THE ONE label route (C-36 deleted the per-document SET twin).
        An operational ROW action applied per document: `is_admin`, or an
        owner of every entitlement being added or removed. This is the
        route the administer/read split exists for -- an administrator
        labels a document they are not cleared to read. It carries no row
        in its own URL, so it needs no `_ROW_ADDRESSED_R`/
        `_LIBRARY_MUTATIONS` entry of its own in the route matrix."""
        from identity.routes import ROUTE_RULES
        assert ROUTE_RULES["rag-document-labels-bulk"] == "R"
```

**The two exhaustiveness tests in this file are what make this a strengthening, not a weakening.**
`TestCoverage.test_every_route_this_platform_owns_is_classified` derives the route list from the
resolver and fails if a route has no rule; `test_every_rule_names_a_route_that_exists` fails if a
rule names no route. Deleting one route and one rule keeps both green, and either half left behind
would fail immediately. Do not modify either test.

- [ ] **Step 7: Update `identity/tests/test_route_matrix.py` — three edits, none of them a deletion of coverage**

1. Delete the `_DRIVERS` entry:
   ```python
       "rag-document-labels": lambda w: (
           "post", reverse("rag-document-labels", args=[w.document.pk]), {"entitlements": []}),
   ```
   `TestTheTableIsComplete.test_every_route_has_a_driver` asserts `set(ROUTE_RULES) -
   set(_DRIVERS) == []`; with the rule gone in Step 6 the driver must go too or the map carries a
   driver for a route that no longer exists.
2. Remove `"rag-document-labels"` from `_ROW_ADDRESSED_R` (leaving `"jobs-queue-cancel"`,
   `"rag-ask-status"`, `"vision-queue-status"`, `"identity-entitlement-edit"`) and from
   `_OWNER_WIDENED` (leaving the other six). **`rag-document-labels-bulk` stays in
   `_OWNER_WIDENED`** — it is already there and its own cell is unchanged.
3. Rewrite the two comment paragraphs that argue about the route's placement. Above
   `_LIBRARY_MUTATIONS`, the long *"`rag-document-labels` IS NOT HERE, unlike IA-1…"* paragraph
   ends at `-- confirmed directly against the route's real behaviour, not asserted around it.`
   Replace that whole paragraph with:

```python
# `rag-document-labels` USED TO BE ARGUED ABOUT HERE and is gone (C-36):
# the per-document SET route had no live caller, and its bulk twin
# (`rag-document-labels-bulk`) takes no row in its own URL, so it has no
# 403-vs-404 question to answer at all. What survives from that argument
# is the rule the two names below still obey: these two resolve their row
# through the UNSCOPED manager after checking standing, so a label on
# THIS PARTICULAR document changes nothing about their answer.
```

   And in the `_ROW_ADDRESSED_R` comment, delete the sentence *"`rag-document-labels` joins here in
   T18 (see `_LIBRARY_MUTATIONS`'s comment just above for why it moved rather than staying grouped
   with its two siblings)."* In the `_OWNER_WIDENED` comment, replace *"`rag-document-labels`,
   `-delete` and `-reingest` are the three capabilities spec section 7.4 gives an entitlement owner
   over a document"* with *"`rag-document-labels-bulk`, `rag-document-delete` and
   `-reingest` are the three capabilities spec section 7.4 gives an entitlement owner over a
   document"*, and delete the following sentence beginning *"`rag-document-labels-bulk` is here
   too, as the bulk form of a route that already is"* down to *"…rather than leaving a reader to
   wonder why the bulk form was left out."*, replacing it with:

```python
# `rag-document-labels-bulk`'s own driver posts an empty `documents` list,
# so its cell never actually exercises the widening (the base R mapping
# already admits everyone alike for an empty selection); the entry
# documents the intent, and the real per-document widening is asserted
# directly in `tools/rag/tests/test_document_label_page.py`.
```

- [ ] **Step 8: Update `tools/rag/tests/test_document_label_page.py`**

`TestTheLabelRoute` has **three** methods, and only two of them are safe to delete. Checked one by
one against `TestBulkLabelling`:

| `TestTheLabelRoute` method | counterpart | disposition |
|---|---|---|
| `test_an_owner_may_only_add_or_remove_entitlements_they_own` | `TestBulkLabelling::test_an_owner_may_bulk_label_only_within_their_entitlement` | delete |
| `test_a_non_numeric_entitlement_id_is_refused_not_crashed` | same name in `TestBulkLabelling` | delete |
| **`test_removing_the_last_label_makes_the_document_follow_the_library_posture`** | **none** | **MOVE, do not delete** |

**The third has no bulk counterpart and must not be lost.** It is a "Done-when 2" acceptance test:
it posts an **empty** `entitlements` list, then asserts the document becomes readable by a plain
member on an open library and 404s under `LIBRARY_LOCKED`, *without a re-encode and within one
request*. `document_labels_bulk` cannot express it the same way — that view refuses an empty
`entitlements` list outright (*"Choose at least one entitlement from the list."*), and
`TestBulkLabelling::test_remove_takes_the_label_off_every_selected_document` asserts nothing about
posture, readability, or the re-encode.

**Move it into `TestBulkLabelling`** and express "remove the last label" the way the bulk route
does — naming the label being removed, with `action="remove"`, rather than submitting an empty set:

```python
            response = client.post(reverse("rag-document-labels-bulk"), {
                "documents": [str(document.id)],
                "entitlements": [str(finance.pk)],
                "action": "remove",
            })
```

Keep **every** downstream assertion — the posture flip, the member read, the `LIBRARY_LOCKED` 404,
and the no-re-encode check — verbatim, and keep the method's name and docstring, adding one line:
*"Moved from `TestTheLabelRoute` at C-36; the empty-set spelling went with the SET route, the
outcome it asserts did not."*

Before deleting the other two, run this and paste the output into the task report so the reviewer
can check the claim:

```bash
git -C <WORKTREE_ROOT> grep -n 'def test_' \
  -- tools/rag/tests/test_document_label_page.py
```

Then, in the two remaining places:

- `TestTheCaseTheSplitExistsFor` posts to the single route to set up "the case the whole split
  exists for" (label with an entitlement the administrator does not hold, then assert
  `rag-document-file` still 404s for them). **This coverage must not be lost** — rewrite the POST
  to the bulk route, which takes the same predicate:

  ```python
              response = client.post(reverse("rag-document-labels-bulk"), {
                  "documents": [str(document.id)],
                  "entitlements": [str(finance.pk)],
                  "action": "apply",
              })
  ```

  Re-derive the exact surrounding names with `git grep -n` first — the fixture names in that class
  (`document`, `finance`, `client`) are what the existing body uses, and the surrounding
  assertions about the chunk re-stamp and the 404 stay untouched.
- `TestTheLabelsColumnReadsAndTheCardWrites`'s "rejected control, pinned as ABSENT" docstring names
  `rag-document-labels` as the thing the page does not render a form at. Rewrite that sentence to
  say the route no longer exists at all — a stronger statement than the one it replaces:

  ```
  The rejected control, pinned as ABSENT. There is no per-document
  label form on this page and no per-document label ROUTE to point one
  at (C-36 deleted it): the one writing surface is the "With selected…"
  card, which posts to `rag-document-labels-bulk`.
  ```

- [ ] **Step 9: Update the C-32 verb table — by name, never by line**

This file is being split by a peer. Locate the work with:

```bash
git -C <WORKTREE_ROOT> grep -n \
  'test_each_administer_refusal_names_its_own_verb\|C-32: the gate prologues' -- tools/rag/tests/
```

If the split has already moved it, edit it wherever it now lives.

Change the parametrize list from three rows to two:

```python
@pytest.mark.django_db
@pytest.mark.parametrize(("url_name", "verb"), [
    ("rag-document-delete", "Deleting"),
    ("rag-document-reingest", "Re-ingesting"),
])
def test_each_administer_refusal_names_its_own_verb(client, url_name, verb):
```

and in the `# --- C-32: the gate prologues ---` comment block above it, delete the clause
*"; `document_labels_update` inlined a third copy of the same forbidden sentence"* and change
*"Three handlers share one sentence with three verbs"* in the docstring to *"Two handlers share one
sentence with two verbs"*, then replace the trailing sentence *"the sentence is what an operator
reads to know what they were refused"* — keep it, it is still true — and append:

```
(A third handler, `document_labels_update`, used to be here with the
verb "Labelling"; C-36 deleted the route. Its refusal predicate lives
on in `document_labels_bulk`, which reports refusals as a SKIPPED
COUNT rather than a 403 sentence, and is tested in
`tools/rag/tests/test_document_label_page.py::TestBulkLabelling`.)
```

**The same verb trio is stated in two more places that no grep in this task finds**, because
neither names a symbol. Both must come down to two verbs:

- the tail of that same C-32 comment block: *"…because "Deleting", "Re-ingesting" and "Labelling"
  are what the operator reads"* → *"…because "Deleting" and "Re-ingesting" are what the operator
  reads"*.
- `tools/rag/views.py::_administered_document`'s docstring (around `:441-450`): *"which of the
  three forbidden sentences they hand back … "Deleting", "Re-ingesting" and "Labelling" … a shared
  verb would make all three vaguer"* → the same sentence with **two**, and "all three" → "both".

Find them with `git grep -n 'Re-ingesting' -- tools/rag/` and read each in full before editing.

- [ ] **Step 10: Update the three prose references and `tools/rag/README.md`**

In `tools/rag/README.md`, delete the whole paragraph beginning *"`rag-document-labels` — the
per-document route with SET semantics ("this document's labels are now exactly these") — is still
mounted, classified and tested, but no page renders a form at it…"* and replace it with one
sentence, placed in the same position:

```markdown
There is no per-document label route. There was one — SET semantics, the
only way to express an exact set — and nothing ever posted to it, so it
is gone: the owner's UI ruling is that the page offers chips and one bulk
writer, and a second route with the opposite destructiveness was a
standing invitation to post to the wrong one.
```

Then the prose references, which fall into **two groups that must not be treated alike**.

**Group A — mechanical rename** (`document_labels_update` → `document_labels_bulk`). These
sentences are about the *predicate*, which is unchanged, so the rename leaves them true:
`identity/access.py:365`, `identity/services.py:376`, `models/registry/views.py:2457` and `:2563`
(grep rows 13–16). Read each before editing to confirm it is still predicate-talk.

**Group B — rewrite, NEVER rename** (grep row 17). Two docstrings are about the **403 shape**, and
that 403 lives only in the view being deleted. `document_labels_bulk` has no such branch — it
excludes chat-scoped rows through `services.documents_targeted_for_labelling`'s query and reports
refusals as a skipped count, as Step 9's own note says. Renaming these would write two false
sentences:

- `tools/rag/ingest.py:700` currently reads *"`tools.rag.views.document_labels_update` refuses one
  by name (403: "This document is scoped to one chat and has no corpus to label", round 12's own
  exclusion)"*. Replace with:

  ```
  the per-document label route that used to refuse one BY NAME was
  deleted (C-36); `services.documents_targeted_for_labelling` excludes a
  chat-scoped row from the bulk writer's target query instead, so the
  exclusion is closed by QUERY now rather than by a 403 sentence -- same
  outcome, no route that can be pointed at one of these rows at all.
  ```

- `tools/rag/access.py:774` currently reads *"a chat-scoped document is deliberately still NEVER
  labellable (round 12's own exclusion, `tools.rag.views.document_labels_update`'s
  explicit-by-name 403 …)"*. Replace the parenthetical with:

  ```
  (round 12's own exclusion -- enforced by `services.
  documents_targeted_for_labelling`'s target query since C-36 deleted the
  per-document route whose explicit 403 used to carry it)
  ```

Re-derive both locations with `git grep -n 'document_labels_update'` first. If either sentence has
been reworded since this plan was measured, keep its meaning and change only the mechanism it
names.

- [ ] **Step 11: Run the pin, then the exit gate**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  tools/rag/tests/test_document_label_page.py::test_the_single_document_label_route_is_gone
```
Expected: PASS.

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests identity/tests foundation/ops/tests
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q tools/rag/tests identity/tests foundation/ops/tests
```

Both flag states here, not just one: `identity/tests/test_routes.py` skips `/vision/`'s names when
the flag is off, and this task edits that file's neighbourhood.

Expected: all pass. **No automated gate covers `tools/rag/README.md`** — `foundation/ops/tests/
test_docs_sync.py` is 38 lines about backup/restore/recovery command sequences and cannot see it.
The README change is verified by reading it, and the task report must say so rather than implying
a test caught it.

- [ ] **Step 12: Commit**

```bash
git -C <WORKTREE_ROOT> add -A
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
fix(rag): the single-document label route goes; the bulk card is the one writer

C-36. `document_labels_update` / `rag-document-labels` had no live
caller: every `{% url %}` under tools/rag/templates reverses
`rag-document-labels-bulk`, and the per-row select-and-Save the SET route
was built for was replaced by the "With selected…" card. Two routes with
the same predicate and opposite destructiveness is the ambiguity
decision 29 refused; the one that survives is the one the page posts to.

Deleted with it: `_LABEL_FORBIDDEN_MESSAGE` (single reader), the
`identity/routes.py` classification, the route-matrix driver and its two
set memberships, and `TestTheLabelRoute`. The route matrix's two
exhaustiveness tests are what keep this honest in both directions and are
untouched; `TestTheCaseTheSplitExistsFor` keeps its coverage by posting
to the bulk route with the same predicate, and a new pin asserts the
name no longer reverses, no longer classifies, and no longer exists on
the views module.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 42: Django REST Framework leaves the box (C-55)

**Files:**
- Test (new): `tools/rag/tests/test_ask_api_contract.py` — the characterization pins, written
  first, against the **current DRF** implementation
- Modify: `tools/rag/views.py` — `AskView`, `AskJobStatusView`, `_model_unavailable_response`,
  `_queue_unavailable_response`, the four `rest_framework` imports, the module docstring
- Modify: `tools/rag/urls.py` — `AskView.as_view()` / `AskJobStatusView.as_view()` become plain
  function references
- Modify: `config/settings.py` — drop `"rest_framework"` from `INSTALLED_APPS` and the whole
  `REST_FRAMEWORK = {…}` block with its `# --- REST framework ---` banner
- Modify: `requirements.txt` — drop `djangorestframework>=3.15`
- Modify: `foundation/ops/tests/test_app_labels.py` — `_project_app_configs`'s
  `cfg.name != "rest_framework"` exclusion and its docstring
- Modify: `identity/tests/test_route_matrix.py` — `TestTheAnonymousPostColumnIsNotVacuous`'s class
  comment and `test_an_anonymous_post_WITH_a_csrf_cookie_reaches_the_gate`'s docstring, both of
  which state as fact that `rag-ask` is `csrf_exempt`
- Modify: `tools/rag/messages.py`, `tools/rag/jobs.py` — two docstrings that call `views.py`
  "DRF-heavy"
- Test: `tools/rag/tests/test_views_ask.py` — the Ask classes (**by name**: `TestAskView`,
  `TestAskEnqueue`, `TestAskViewConnectionOverride`, `TestAskJobStatus`) must pass unchanged;
  amend only what Step 7 names

**Column:** `tools/rag` owns both views. `config/` and `requirements.txt` are project-level.
No new import crosses a column.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `tools.rag.views.ask(request) -> JsonResponse` — POST `/rag/ask/`, replacing `AskView`.
  - `tools.rag.views.ask_job_status(request, job_id: int) -> JsonResponse` — GET
    `/rag/ask/jobs/<job_id>/`, replacing `AskJobStatusView`.
  - `tools.rag.views._json_body(request) -> dict | None` — the request-body reader. A JSON content
    type is parsed with `json.loads`; anything else is read off `request.POST`. Returns **`None`**
    — never `{}` — when a JSON content type carries a body that is unparseable or is not a JSON
    *object*, and the caller turns that `None` into a 400 carrying `_JSON_PARSE_ERROR`. The
    distinction is load-bearing: `{}` would fall through to the *"'question' is required"* sentence
    instead, which is a different operator message and would break
    `test_an_unparseable_json_body_is_a_400`.
  - `tools.rag.views._json(payload: dict, *, status: int = 200) -> JsonResponse` — the one response
    builder, `encoder=DjangoJSONEncoder`.
  - `rest_framework` is absent from `INSTALLED_APPS` and from `requirements.txt`.

**Ruling (binding).** *"Drop Django REST Framework — rewrite the two JSON views in
`tools/rag/views.py` on Django's `JsonResponse`/JSON parsing with identical request/response
contracts pinned by the existing tests; remove `rest_framework` from `INSTALLED_APPS`, settings,
requirements. Cost if wrong: a client depending on DRF-specific error shapes; pin the shapes
first."*

**What DRF is actually doing here, measured.** `config/settings.py` sets
`DEFAULT_RENDERER_CLASSES: ("rest_framework.renderers.JSONRenderer",)` and nothing else — no
authentication classes, no permission classes, no throttles, no pagination, no serializers, no
browsable API. `git grep -l rest_framework -- '*.py'` outside tests hits `config/settings.py`,
`tools/rag/views.py` and two docstrings. Both views hand `Response` a plain `dict` and a status
constant. That is the entire dependency: **a JSON encoder, a request-body parser, and a method
dispatcher.**

**The seven behavioural differences the rewrite must handle, and the ruling on each.** These are
what the characterization tests exist to pin. An implementer who skips Steps 1–3 and goes straight
to the rewrite will not find them. Five are preserved or deliberately changed; the last two
(D6, D7) are DRF affordances with **no caller on this box**, recorded rather than reproduced.

| # | DRF today | Plain Django | Ruling |
|---|-----------|--------------|--------|
| D1 | `request.data` parses **JSON, form-urlencoded and multipart** alike (default parser list) | `request.POST` handles the latter two; JSON needs `json.loads(request.body)` | **Preserve both.** `_json_body` reads JSON when the content type says JSON, else falls back to `request.POST`. `identity/tests/test_route_matrix.py`'s `rag-ask` driver posts through Django's test client with a plain dict — which defaults to **multipart**, not urlencoded — and must keep reaching the enqueue path. `request.POST` covers both, so the fix is the same; do not write "form-encoded" in a comment and mean multipart. |
| D2 | `APIView.as_view()` is `csrf_exempt`; DRF enforces CSRF only inside `SessionAuthentication`, which this project never configures — so **`/rag/ask/` accepts a POST with no CSRF token at all** | `CsrfViewMiddleware` enforces for every caller, anonymous included | **Adopt CSRF enforcement.** `ask.html` already sends `X-CSRFToken` (`getCsrfToken()` reads the form's `csrfmiddlewaretoken`), so the only UI on the box is unaffected. This closes a real gap rather than preserving one, and it makes `rag-ask` eligible for `TestTheAnonymousPostColumnIsNotVacuous`'s parametrize list — Step 7 adds it there, which is the strengthening half of this change. |
| D3 | Unparseable JSON → DRF's own `{"detail": "JSON parse error - …"}` with 400 | ours to choose | **Replace with the platform's shape**, `{"error": "…"}` with 400. `ask.html`'s only reader is `result.data.error \|\| "Request failed."`, so `detail` already renders as the generic fallback today; `error` renders the real sentence. Pin the current `detail` shape in Step 1 and *change the pin* in Step 6 with a comment naming the decision — a recorded change, never a silent one. |
| D4 | Datetimes render through DRF's encoder: `.isoformat()`, `+00:00` → `Z`, **microseconds kept at 6 digits** | `DjangoJSONEncoder`: same, but **microseconds truncated to 3 digits** | **Accept the truncation.** `submitted_at` and `started_at` are the only datetimes in either body, and **no template reads this endpoint's JSON**. (`git grep -n 'submitted_at\|started_at' -- '*.html'` is *not* empty — it returns `models/queue/templates/jobs/queue.html`'s server-rendered `{{ row.started_at }}` and a prose comment in `vision/_job_card.html`. Neither goes anywhere near `/rag/ask/jobs/`.) The characterization test pins ISO-8601-with-`Z` and the presence of the key, not a microsecond count, and the commit body names the difference. |
| D5 | A wrong HTTP method on an `APIView` → 405 with a JSON body | `require_POST` → 405 with an **HTML** body | **Keep 405, make the body JSON.** `_json({"error": …}, status=405)` from an explicit in-body method check, so a poller that gets the verb wrong still parses the answer. Pin the current status in Step 1. |
| D6 | `OPTIONS` on an `APIView` → **200** with a DRF metadata body (name, description, renders, parses) | the in-body check → **405** | **Record, do not reproduce.** Nothing on this box sends `OPTIONS` to either endpoint — no template, no test, no client — and a self-describing metadata body is a DRF affordance, not a contract this platform ever promised. Note it in the commit body; do not build a metadata responder to preserve it. |
| D7 | Content negotiation: `Accept: text/html` → **406 Not Acceptable** (the JSON renderer cannot satisfy it) | `JsonResponse` ignores `Accept` → **200** JSON | **Record, do not reproduce.** Both callers (`ask.html`'s `fetch`, and the tests) send no `Accept` or accept anything. Answering JSON to a browser that asked for HTML is more useful than a 406, and reproducing negotiation would mean reimplementing the piece of DRF this task is removing. |

- [ ] **Step 1: Write the characterization pins against the CURRENT code**

Create `tools/rag/tests/test_ask_api_contract.py`. This file is written **before** any production
change and must pass **green on the DRF implementation, unmodified**, at Step 3. It is the contract
the rewrite has to reproduce.

**The setup already exists — do not invent a second way to make Ask work.**
`tools/rag/tests/_helpers.py` (verified) provides both pieces:

- `post_ask(client, payload)` — *"POST a question payload to `/rag/ask/` as JSON, shared by every
  `TestAskView*` class in test_views.py"*; it does
  `client.post(reverse("rag-ask"), data=json.dumps(payload), content_type="application/json")`.
- `model_available()` — the generator body behind the autouse `_model_available` fixture that
  `TestAskView`, `TestAskEnqueue` and `TestAskViewConnectionOverride` all use. It patches
  `tools.rag.views.resolve` and `tools.rag.messages.get_engine` so the pre-check transits without
  touching the network.

So this file opens with an autouse fixture of its own:

```python
@pytest.fixture(autouse=True)
def _model_available():
    yield from model_available()
```

and uses `post_ask` for the JSON half. The **form-encoded** test must post directly with
`client.post(reverse("rag-ask"), {...})` — that is the whole point of it, and `post_ask` cannot
express it. `enqueue`/`get_job` are patched as `tools.rag.views.enqueue` /
`tools.rag.views.get_job` (the seam names bound in that module), exactly as `TestAskEnqueue` does;
`TestAskJobStatus` instead creates `InferenceJob` rows directly, which is the sanctioned
test-only crossing named in its own docstring. Re-derive all of it with:

```bash
git -C <WORKTREE_ROOT> grep -n 'def post_ask\|def model_available' -A 10 -- tools/rag/tests/_helpers.py
git -C <WORKTREE_ROOT> grep -n 'class TestAskEnqueue\|class TestAskJobStatus' -A 30 -- tools/rag/tests/
```

The pins:

```python
"""The wire contract of the two Ask JSON endpoints, pinned independently
of the framework that serves them (C-55).

WRITTEN AGAINST DRF, KEPT ACROSS THE REWRITE. Every assertion here was
green before `rest_framework` was removed and is green after: status
codes, the exact JSON body keys, the `Content-Type` header, how a body
is parsed (JSON *and* form-encoded), and what a wrong method answers.
`tools/rag/tests/test_views_ask.py`'s Ask classes cover the *behaviour*;
this file covers the *transport*, which is the half a framework swap can
break silently.

THE TWO DELIBERATE CHANGES are marked `CHANGED AT C-55` inline, with the
reason: DRF's `{"detail": ...}` parse-error body becomes this platform's
`{"error": ...}`, and `/rag/ask/` stops being `csrf_exempt`.
"""
```

Then, as a class `TestTheAskWireContract` with `pytestmark = pytest.mark.django_db`:

```python
    def test_a_json_post_is_accepted_and_answers_202_with_the_five_keys(self, client):
        """The success shape, verbatim: five keys, 202, application/json."""
        # ... TestAskEnqueue's own setup: bind rag.answer + rag.embed to a
        # healthy engine double, then:
        response = client.post(
            reverse("rag-ask"),
            data=json.dumps({"question": "what does the library say?"}),
            content_type="application/json",
        )
        assert response.status_code == 202
        assert response["Content-Type"].startswith("application/json")
        body = json.loads(response.content)
        assert set(body) == {"job_id", "state", "position", "priority", "status_url"}
        assert body["state"] == "queued"
        assert body["status_url"] == reverse("rag-ask-status", args=[body["job_id"]])

    def test_a_FORM_ENCODED_post_is_accepted_too(self, client):
        """D1. DRF's default parser list takes form-encoded bodies as
        well as JSON, and `identity/tests/test_route_matrix.py`'s
        `rag-ask` driver posts exactly that. A rewrite that only reads
        `request.body` as JSON would turn that matrix cell from 202 into
        400 -- which the matrix's `_ADMITTED` set would silently accept."""
        response = client.post(reverse("rag-ask"), {"question": "what does the library say?"})
        assert response.status_code == 202
        assert json.loads(response.content)["state"] == "queued"

    @pytest.mark.parametrize(("payload", "message"), [
        ({}, "'question' is required and must be a non-empty string."),
        ({"question": "   "}, "'question' is required and must be a non-empty string."),
        ({"question": 7}, "'question' is required and must be a non-empty string."),
        ({"question": "q", "priority": "soon"}, "Priority must be a whole number."),
        ({"question": "q", "priority": "0"}, "Priority must be a positive number."),
        ({"question": "q", "priority": "-3"}, "Priority must be a positive number."),
    ])
    def test_every_400_carries_one_error_key_and_that_exact_sentence(self, client, payload, message):
        """The refusal shape: 400, `{"error": <sentence>}`, nothing else.
        The sentences are operator copy and are asserted verbatim."""
        response = client.post(reverse("rag-ask"), data=json.dumps(payload),
                               content_type="application/json")
        assert response.status_code == 400
        assert json.loads(response.content) == {"error": message}

    def test_an_unparseable_json_body_is_a_400(self, client):
        """CHANGED AT C-55, deliberately. DRF answered
        `{"detail": "JSON parse error - ..."}`; the rewrite answers this
        platform's own `{"error": ...}`. `ask.html` reads
        `result.data.error || "Request failed."` and therefore rendered
        the generic fallback for DRF's body -- the new shape renders the
        real sentence. Status is 400 either way."""
        response = client.post(reverse("rag-ask"), data="{not json",
                               content_type="application/json")
        assert response.status_code == 400
        assert "error" in json.loads(response.content)

    def test_a_GET_to_the_ask_endpoint_is_405(self, client):
        """D5. The status is the contract; the rewrite additionally makes
        the BODY json instead of DRF's own."""
        assert client.get(reverse("rag-ask")).status_code == 405

    def test_an_unknown_job_id_is_404_with_error_and_history_url(self, client):
        response = client.get(reverse("rag-ask-status", args=[999999]))
        assert response.status_code == 404
        assert response["Content-Type"].startswith("application/json")
        assert set(json.loads(response.content)) == {"error", "history_url"}

    def test_a_queued_job_reports_four_keys_and_an_iso_8601_Z_timestamp(self, client):
        """D4. `submitted_at` is a datetime, and the encoder is what
        renders it. Pinned as ISO-8601 ending in `Z` -- NOT to a
        microsecond count: DRF keeps six digits, `DjangoJSONEncoder`
        truncates to three, and no template on this box reads the field
        (no template reads this endpoint's JSON: the only `*.html` hits
        for these names are `jobs/queue.html`'s server-rendered
        `row.started_at` and a comment in `vision/_job_card.html`)."""
        # ... enqueue a job, then GET its status_url
        body = json.loads(response.content)
        assert set(body) == {"state", "position", "priority", "submitted_at"}
        assert body["state"] == "queued"
        assert body["submitted_at"].endswith("Z")
        datetime.fromisoformat(body["submitted_at"].replace("Z", "+00:00"))  # parses
```

The implementer writes the setup bodies by copying `TestAskEnqueue`'s and `TestAskJobStatus`'s own
fixtures. Every assertion above is mandatory; the setup is not prescribed because the fixture names
in `test_views.py` are moving under the peer's split.

- [ ] **Step 2: Add the CSRF pin, still against DRF**

In the same file, a second class:

```python
class TestTheAskEndpointAndCsrf:
    def test_an_anonymous_post_with_no_csrf_token_is_refused(self, client):
        """CHANGED AT C-55, deliberately, and the reason the change is
        worth making. `APIView.as_view()` wraps dispatch in
        `csrf_exempt`; DRF then enforces CSRF only inside
        `SessionAuthentication`, and this project configures NO
        authentication classes -- so before this task `/rag/ask/` took a
        cross-site POST with no token at all. A plain Django view is
        covered by `CsrfViewMiddleware` like every other POST on the box.

        `ask.html` is unaffected: it sends `X-CSRFToken` from the form's
        own `csrfmiddlewaretoken` (`getCsrfToken()`), which is why this
        is a gap being closed and not a client being broken.

        WRITTEN RED ON PURPOSE. Before the rewrite this asserts 202 (the
        `pytest.xfail` below records that); after it, 403."""
        strict = Client(enforce_csrf_checks=True)
        with posture(POSTURE_PERSONAL):
            response = strict.post(reverse("rag-ask"),
                                   data=json.dumps({"question": "q"}),
                                   content_type="application/json")
        assert response.status_code == 403
```

At Step 3 this one test fails and every other test in the file passes. **That is the expected
result and the whole point of the step** — it is the measured proof of D2. Mark it
`@pytest.mark.xfail(reason="C-55 Step 6 removes the DRF csrf_exempt wrapper", strict=True)` for
Steps 3–5 and delete the marker in Step 6, so the suite is green at every commit boundary and the
`strict=True` catches the day it starts passing.

- [ ] **Step 3: Run the pins against the unmodified DRF code**

```bash
export DATABASE_URL='<TEST_DATABASE_URL>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_ask_api_contract.py -v
```

Expected: every test passes; `test_an_anonymous_post_with_no_csrf_token_is_refused` reports XFAIL;
`test_an_unparseable_json_body_is_a_400` **may** fail on the `"error" in …` assertion if DRF's
body uses `detail` — if it does, temporarily assert `"detail" in …` and note it, then flip it in
Step 6. Record the actual observed body in the task report either way.

**If any other test in this file fails here, stop.** The pin is wrong about the current contract
and the rewrite would be built on a wrong description of it.

- [ ] **Step 4: Commit the pins alone**

```bash
git -C <WORKTREE_ROOT> add tools/rag/tests/test_ask_api_contract.py
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
test(rag): pin the Ask endpoints' wire contract before the framework moves

C-55, first half. Status codes, exact body keys, `Content-Type`, both
accepted request encodings (JSON and form-encoded -- the route matrix's
own driver posts the latter), the 405, and the ISO-8601-with-Z timestamp
shape, all asserted against the current DRF implementation so the
rewrite has something to be measured against rather than described to.

The one xfail records the difference the rewrite is FOR: `APIView` is
`csrf_exempt` and this project configures no DRF authentication classes,
so `/rag/ask/` currently accepts a POST with no CSRF token.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 5: Rewrite the two views**

In `tools/rag/views.py`, delete the four `rest_framework` imports and add to the Django import
block:

```python
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
```

**Two imports and no more.** `json` is **not** currently imported in this module (verified —
`git grep -n '^import json' -- tools/rag/views.py` returns nothing), so add it to the stdlib group
beside `import logging`/`import math`. Do **not** add
`from django.views.decorators.http import require_GET`: this task uses an in-body method check, not
a decorator (see below), so `require_GET` would land unused and trip `ruff F401`. `require_POST` is
already imported at `tools/rag/views.py:37` and stays — the other views in this module use it.

Add the two helpers immediately above `_model_unavailable_response`:

```python
_JSON_PARSE_ERROR = "The request body could not be read as JSON."


def _json(payload: dict, *, status: int = 200) -> JsonResponse:
    """The one JSON responder for this module's two API endpoints.

    `DjangoJSONEncoder`, not the bare `json` default: `JobStatus.
    created_at`/`started_at` are `datetime`s, and this encoder renders
    them ISO-8601 with a `Z` suffix -- the shape `rest_framework.
    renderers.JSONRenderer` produced before C-55 removed it. The one
    measurable difference is microsecond precision (three digits here,
    six there); no template on this box reads either field, and
    `tools/rag/tests/test_ask_api_contract.py` pins the shape rather
    than the digit count.

    `safe=False` is deliberately NOT passed: every body this module
    returns is a dict, and the day one is not, the guard should fire."""
    return JsonResponse(payload, status=status, encoder=DjangoJSONEncoder)


def _json_body(request) -> dict | None:
    """The request body as a mapping, or `None` when it cannot be read.

    TWO ENCODINGS, because DRF's default parser list accepted both and
    real callers use both: a JSON content type is parsed with
    `json.loads`; anything else (form-urlencoded, multipart) is read off
    `request.POST`, which is how `identity/tests/test_route_matrix.py`'s
    own `rag-ask` driver posts. A JSON content type whose body is not a
    JSON OBJECT -- unparseable, or a bare list/string/number -- answers
    `None`, which the caller turns into a 400. Before C-55 that case was
    DRF's own `{"detail": "JSON parse error - ..."}`; it is this
    platform's `{"error": ...}` now, which is the shape `ask.html`
    actually reads."""
    if request.content_type and request.content_type.split(";")[0].strip() == "application/json":
        try:
            parsed = json.loads(request.body or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return request.POST.dict()
```

Then convert `_model_unavailable_response` and `_queue_unavailable_response`: change both return
annotations from `Response` to `JsonResponse` and both bodies from
`Response({...}, status=status.HTTP_503_SERVICE_UNAVAILABLE)` to `_json({...}, status=503)`. Leave
their docstrings' *content* alone except for replacing the phrase "a thin `Response` wrapper" with
"a thin `JsonResponse` wrapper".

`AskView` becomes a module-level function. **Every docstring paragraph, every inline comment and
every message string moves verbatim** — this is a transport change, not a rewrite of the view's
reasoning. The only edits inside the body are mechanical:

- `class AskView(APIView):` + `def post(self, request: Request) -> Response:` →
  ```python
  def ask(request) -> JsonResponse:
      if request.method != "POST":
          return _json({"error": "Method not allowed."}, status=405)
  ```
  **No `@require_POST` decorator** — see the method-check note below; one mechanism, not two.
  The class docstring becomes the function docstring, with the following paragraph appended to it:
  ```
  A PLAIN DJANGO VIEW SINCE C-55. It was a DRF `APIView` for a JSON
  encoder, a body parser and a method dispatcher, which is three
  stdlib/Django lines; `config/settings.py` configured no
  serializers, no authentication classes and no permission classes,
  so nothing else of the framework was ever in play. One consequence
  is deliberate and is not a regression: `APIView.as_view()` is
  `csrf_exempt`, so this endpoint used to accept a POST with no CSRF
  token at all -- `CsrfViewMiddleware` now covers it like every other
  POST on the box, and `ask.html` has always sent the token.
  ```
- `self._precheck_models(...)` → a module-level `_precheck_models(answer_override=None)`; move the
  method out of the class unchanged, docstring included, dropping the `self` parameter.
- `AskView._unregistered_connection_response` → a module-level `_unregistered_connection_response()`
  returning `_json({...}, status=503)`.
- `request.data.get(...)` → `body.get(...)`, with `body = _json_body(request)` and, immediately
  after it:
  ```python
  if body is None:
      return _json({"error": _JSON_PARSE_ERROR}, status=400)
  ```
- every `Response(X, status=status.HTTP_NNN_…)` → `_json(X, status=NNN)`:
  `HTTP_400_BAD_REQUEST` → 400, `HTTP_403_FORBIDDEN` → 403,
  `HTTP_503_SERVICE_UNAVAILABLE` → 503, `HTTP_202_ACCEPTED` → 202.
- `_precheck_models`' return annotation `tuple[Response | None, dict[str, ResolvedModel]]` →
  `tuple[JsonResponse | None, dict[str, ResolvedModel]]`.

`AskJobStatusView` becomes:

```python
def ask_job_status(request, job_id: int) -> JsonResponse:
    if request.method != "GET":
        return _json({"error": "Method not allowed."}, status=405)
```

with the class docstring as the function docstring and `Response(body)` → `_json(body)`,
`Response({...}, status=status.HTTP_404_NOT_FOUND)` → `_json({...}, status=404)`. Nothing else in
the body changes — **except** its docstring's opening phrase, *"A thin `Response` wrapper around
`models.contracts.queue.get_job`"*, which becomes *"A thin `JsonResponse` wrapper …"* (Step 7b
covers the by-name half of the same docstring).

**The method check is in the body, decided here, not left to the implementer.**
`require_POST`/`require_GET` answer 405 with Django's **HTML** error page, and a poll target that
answers HTML to a wrong verb is a poller that cannot report why. So each view opens with a two-line
guard — `ask` as shown above, and `ask_job_status` with the same shape for `"GET"` — and **neither
carries a decorator**. There is no `_json_405` helper: two lines twice does not earn one, and a
helper here would be the second mechanism this note exists to avoid.

The result is pinned by Step 1 either way: `client.get(reverse("rag-ask")).status_code == 405`,
with a JSON body carrying an `error` key.

Finally, `tools/rag/urls.py`: `from tools.rag.views import (AskJobStatusView, AskPageView, AskView, …)`
becomes `… ask, ask_job_status …` (keep the list alphabetised — `ask`, `ask_job_status` sort before
`category_delete`), and

```python
    path("ask/", AskView.as_view(), name="rag-ask"),
    path("ask/jobs/<int:job_id>/", AskJobStatusView.as_view(), name="rag-ask-status"),
```
becomes
```python
    path("ask/", ask, name="rag-ask"),
    path("ask/jobs/<int:job_id>/", ask_job_status, name="rag-ask-status"),
```

Route names are unchanged, so `identity/routes.py` and the route matrix need no new entries.

- [ ] **Step 6: Flip the two recorded pins**

In `tools/rag/tests/test_ask_api_contract.py`, remove the `@pytest.mark.xfail` from
`test_an_anonymous_post_with_no_csrf_token_is_refused`, and — if Step 3 found DRF answering
`detail` — change `test_an_unparseable_json_body_is_a_400` to assert
`json.loads(response.content) == {"error": _JSON_PARSE_ERROR}` (importing the constant from
`tools.rag.views`), keeping the `CHANGED AT C-55` docstring that explains why.

- [ ] **Step 7: Remove the dependency and update the three places that assert its presence**

1. `config/settings.py`: delete `"rest_framework",` from `INSTALLED_APPS` and the whole trailing
   block
   ```python
   # --- REST framework ------------------------------------------------------------

   REST_FRAMEWORK = {
       "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
   }
   ```
2. `requirements.txt`: delete the `djangorestframework>=3.15` line.
3. `foundation/ops/tests/test_app_labels.py`: `_project_app_configs`'s comprehension drops
   `and cfg.name != "rest_framework"`, and its docstring loses the words *"and the third-party
   `rest_framework`"*:
   ```python
   def _project_app_configs():
       """Every installed AppConfig this repository authored -- Django's
       own contrib apps excluded by name, since their labels are not ours
       to pin. There is no third-party app to exclude any more: C-55
       removed `rest_framework`, the only one this box ever installed."""
       return [cfg for cfg in apps.get_app_configs() if not cfg.name.startswith("django.")]
   ```
4. `identity/tests/test_route_matrix.py`: rewrite `TestTheAnonymousPostColumnIsNotVacuous`'s class
   comment. The current text says `rag-ask` is a DRF `APIView`, is `csrf_exempt`, and would make a
   CSRF assertion vacuous. That is now false in every clause. Replace the whole
   *"`rag-ask` IS NOT USED ANYWHERE IN THIS CLASS…"* paragraph with:

   ```python
   # `rag-ask` JOINS THIS CLASS AT C-55. It used to be excluded on the
   # grounds that a DRF `APIView` is `csrf_exempt` and DRF enforces CSRF
   # only inside `SessionAuthentication`, which this project never
   # configured -- a CSRF assertion aimed at it would have passed whether
   # or not a token was ever sent, which is the vacuity this class
   # exists to prevent. It is a plain Django view now, so
   # `CsrfViewMiddleware` covers it and the assertion has teeth.
   ```

   **The class's *first* paragraph also goes stale and must be amended in the same edit.** It
   currently reads *"One POST per class that has one, and every one of them a PLAIN Django view:
   `chat-start` (A), `chat-turn` (O), `rag-document-delete` (R), `rag-category-delete` (S)."*
   `identity/routes.py` classifies `rag-ask` as **`"A"`** — the same class as `chat-start` — so
   adding it makes the list two-for-one-class and contradicts "One POST per class". Rewrite as:

   ```python
   # One POST per class, every one of them a plain Django view:
   # `chat-start` (A), `chat-turn` (O), `rag-document-delete` (R),
   # `rag-category-delete` (S) -- plus `rag-ask`, a SECOND A, added at
   # C-55. It is here not for class coverage (chat-start already covers
   # A) but because it is the one route this class used to argue ITSELF
   # out of, on grounds that stopped being true when it stopped being a
   # DRF APIView.
   ```

   and add `"rag-ask"` to `test_an_anonymous_post_with_no_csrf_cookie_is_403`'s parametrize list.
   In `test_an_anonymous_post_WITH_a_csrf_cookie_reaches_the_gate`, replace the paragraph beginning
   *"`chat-start`, a PLAIN view, not `rag-ask`: `rag-ask`'s DRF `APIView` is `csrf_exempt`…"* with:

   ```
   `chat-start`, because its driver posts a form the gate can answer
   plainly. `rag-ask` is a plain view too since C-55 and is now in the
   no-token half of this pair, just above.
   ```

   **This is the "updated, not weakened" half of the task**: the endpoint gains a CSRF assertion it
   was previously argued out of.
5. `tools/rag/messages.py`'s module docstring: *"that module is DRF-heavy (imports
   `rest_framework`, `django.views.generic`, …)"* → *"that module is the module's HTTP surface
   (imports `django.views.generic`, `django.http`, …)"*. `tools/rag/jobs.py`'s docstring:
   *"`views.py` (DRF-heavy, this module's HTTP-surface sibling …)"* → *"`views.py` (this module's
   HTTP-surface sibling …)"*. Re-derive both with
   `git grep -n 'DRF' -- tools/rag/`.
6. `tools/rag/views.py`'s module docstring: *"plus two DRF API endpoints -- AskView (…) and
   AskJobStatusView (…)"* → *"plus two JSON endpoints -- `ask` (…) and `ask_job_status` (…)"*, and
   the two names inside it updated to match.

- [ ] **Step 7b: The by-name sweep — `AskView`/`AskJobStatusView` are named in 33 files**

**The rename is the expensive half of this task and the first draft of this plan ignored it.**
The two class names are referenced by name far beyond the three files Step 5 edits. Measure first:

```bash
git -C <WORKTREE_ROOT> grep -ln 'AskView\|AskJobStatusView' \
  -- '*.py' '*.md' '*.html' ':!docs/superpowers'
```

At the time of writing that is **33 files** — production modules across four columns
(`agents/chat/views/turns.py`, `workstreams.py`; `models/contracts/queue.py`;
`models/queue/backend.py`; `models/registry/bindings.py`, `views.py`; `tools/rag/access.py`,
`jobs.py`, `messages.py`, `retrieval.py`, `services.py`; `tools/vision/services.py`, `views.py`),
tests (`foundation/ops/tests/test_import_law.py`, several `tools/rag/tests/*`, three
`agents/chat/tests/*`), and live docs (`docs/DEV.md`, `foundation/README.md`,
`tools/rag/README.md`, `tools/vision/README.md`).

**The decision rule, so the implementer improvises nothing:**

- **Rename** every reference that names the *callable* or the *surface* — they all still mean the
  same endpoint, and the URL names (`rag-ask`, `rag-ask-status`) are unchanged. Prefer
  `tools.rag.views.ask` / `ask_job_status` in the same spelling the surrounding sentence already
  uses (dotted path, or bare name).
- **Do not touch** `docs/adr/0010`, `0012`, `0013`, `0014` — ADRs are dated records (same rule
  Task 41 applies), nor anything under `docs/superpowers/`.
- **`agents/chat/README.md` is HELD for PR #88 and carries one reference.** It may **not** be
  edited by this task. Leave it, and record it as a follow-up — it is listed in this plan's
  "Not planned" table beside the C-16 chat repoint, and both are released by the same merge.

**Two sites inside `tools/rag/views.py` that Step 5 does not otherwise reach**, and must be fixed
here or they contradict the code beside them:

- `_queue_unavailable_response`'s docstring — *"shared by `AskView.post`'s enqueue-time
  `QueueUnavailable` catch and `AskJobStatusView.get`'s own"* → *"shared by `ask`'s enqueue-time
  `QueueUnavailable` catch and `ask_job_status`'s own"*.
- `AskJobStatusView`'s own docstring (which becomes `ask_job_status`'s) opens *"A thin `Response`
  wrapper around `models.contracts.queue.get_job`"* → *"A thin `JsonResponse` wrapper …"*. Step 5
  authorises that phrase change only for `_model_unavailable_response`; it applies here too.

**If this sweep turns out larger than the task's appetite, the honest fallback is to keep the
existing callable names** (`AskView = ask` is not acceptable — just name the functions
`AskView`/`AskJobStatusView` and note that they are functions now). Say which you chose in the
task report; do not half-rename.

- [ ] **Step 8: Add the required absence pin**

Append to `tools/rag/tests/test_ask_api_contract.py`:

```python
def test_the_box_does_not_install_django_rest_framework():
    """C-55. A whole web framework was installed for a JSON encoder, a
    body parser and a method dispatcher, on a product whose whole point
    is an offline-first dependency footprint. Two pins, because either
    one alone can be satisfied while the other rots: the app is not
    installed, and the package is not required.

    NOT pinned: that `import rest_framework` fails. The package may
    legitimately still sit in a developer's long-lived virtualenv after
    this commit, and a pin that depends on somebody having rebuilt their
    venv is a pin that fails for the wrong reason."""
    from pathlib import Path

    from django.conf import settings

    assert "rest_framework" not in settings.INSTALLED_APPS
    assert not hasattr(settings, "REST_FRAMEWORK")
    requirements = Path(settings.BASE_DIR, "requirements.txt").read_text()
    assert "djangorestframework" not in requirements
    assert "rest_framework" not in requirements
```

Re-derive the settings attribute that names the repo root
(`git grep -n 'BASE_DIR' -- config/settings.py`) before writing that path; if it is a `Path`
already, drop the `Path(...)` wrapper.

- [ ] **Step 9: Run the focused tests, then the exit gate**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests/test_ask_api_contract.py -v
```
Expected: every test passes, no xfail.

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/rag/tests identity/tests foundation/ops/tests models/queue/tests
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q tools/rag/tests identity/tests foundation/ops/tests models/queue/tests
```

`models/queue/tests` is in the run because `AskJobStatusView` reads `models.contracts.queue` and
`models.queue.visibility`; `identity/tests` because of the route-matrix edits;
`foundation/ops/tests` for `test_app_labels.py` and the import law.

**Then the full suite, both flag states** — this task changes `INSTALLED_APPS`, which every test in
the repo runs against:

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
```

Expected: the same pass count as the task's base commit, plus this task's new tests.

- [ ] **Step 10: Commit**

```bash
git -C <WORKTREE_ROOT> add -A
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
refactor(rag): the two Ask endpoints drop Django REST Framework

C-55. `config/settings.py` configured DRF with exactly one setting -- a
JSON renderer -- and no serializers, authentication classes or
permission classes; `AskView`/`AskJobStatusView` used it for a JSON
encoder, a request-body parser and a method dispatcher. Both are plain
functions now (`ask`, `ask_job_status`) on `JsonResponse` +
`DjangoJSONEncoder` + `json.loads`, with every docstring, comment and
operator sentence carried over verbatim; `rest_framework` leaves
INSTALLED_APPS, settings and requirements.txt.

Contract pinned first, in tools/rag/tests/test_ask_api_contract.py:
statuses, exact body keys, Content-Type, both accepted request encodings
(the route matrix's own driver posts form-encoded), 405 on a wrong verb,
and the ISO-8601-with-Z timestamp shape.

Five recorded differences, none silent:
* `/rag/ask/` is no longer `csrf_exempt`. `APIView.as_view()` wrapped it,
  and DRF enforces CSRF only inside `SessionAuthentication`, which this
  project never configured -- so the endpoint accepted a POST with no
  token. It joins the route matrix's anonymous-POST CSRF pin, which had
  argued it out on exactly those grounds. `ask.html` has always sent the
  token.
* An unparseable JSON body answers `{"error": ...}` instead of DRF's
  `{"detail": "JSON parse error - ..."}`. `ask.html` reads `.error`, so
  it rendered the generic fallback for the old shape.
* `submitted_at`/`started_at` keep three microsecond digits instead of
  six (`DjangoJSONEncoder` truncates where DRF's does not). No template
  reads the Ask status body; the two `*.html` hits for those names are
  `jobs/queue.html`'s server-rendered `row.started_at` and a comment in
  `vision/_job_card.html`, neither of which touches this endpoint.
* `OPTIONS` answers 405 instead of DRF's 200 metadata body, and
  `Accept: text/html` answers 200 JSON instead of DRF's 406. Both are
  framework affordances with no caller on this box; reproducing either
  would mean reimplementing the part of DRF being removed.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 43: One shared `pollUntilTerminal`; two pages call it (C-16)

**Files:**
- Create: `foundation/templates/_poller.html` — one inline `<script>`, the shared helper, nothing
  else
- Modify: `tools/rag/templates/rag/ask.html` — `{% block scripts %}`: include the helper, delete
  `pollJobStatus`'s loop, keep every callback
- Modify: `tools/vision/templates/vision/create.html` — `{% block scripts %}`: include the helper,
  delete `poll`/`watch`'s loop, keep `markLive` and the fragment swap
- Modify: `tools/vision/tests/test_views_create.py` — the one `body.count("<script>") == 2`
  assertion becomes 3, with a comment
- Test (new): `foundation/ops/tests/test_shared_poller.py` — the source-text gate
- Modify: `foundation/README.md` — the shared-template inventory gains `_poller.html`
  (**not** `docs/ARCHITECTURE.md`, which has no script-posture sentence to amend — see Step 7)

**Explicitly NOT touched (Global Constraint 10 / addendum A2):**
`agents/chat/templates/chat/conversation.html`, `agents/chat/templates/chat/_turn_card.html`,
`agents/chat/tests/test_thread.py`, `agents/chat/README.md`. The chat repoint is a **documented
follow-up**, written out at the end of this task.

**Column:** `foundation` owns the shared template — the deepest common ancestor of `tools/rag`,
`tools/vision` and `agents/chat`, exactly as `foundation/templates/_messages.html` (parent plan
Task 31) and `foundation/templates/_shell.html` already are. `tools/rag` and `tools/vision` each
`{% include %}` it; neither imports anything from the other, and no Python import is added
anywhere. The import-law gates are untouched by construction.

**Interfaces:**
- Produces: `window.pollUntilTerminal(url, opts)`, defined in `foundation/templates/_poller.html`.
  ```
  pollUntilTerminal(url, {
    startDelayMs,         // number, default 0 -- delay before the FIRST poll
    retryDelayMs,         // number, default POLL_DEFAULTS.intervalMs -- delay after a
                          //   transport failure (Images backs off further than Ask)
    onResponse,           // (response) => outcome | Promise<outcome>   REQUIRED
    onGiveUp,             // (reason) => void, reason is "duration" | "transport"
  })
  ```
  **Four options, not seven.** The interval, the transport-retry ceiling and the give-up ceiling
  are read from `POLL_DEFAULTS` and have no per-call override — neither caller wants one, and that
  object is already the tuning seam. Only the two scheduling quirks that genuinely differ between
  Ask and Images are per-call.
  `outcome` is either a falsy value / `{done: true}` (stop), or
  `{retry: true, url?: string, delayMs?: number}` (poll again, optionally at a new URL and a
  one-off delay). `onResponse` receives the raw `Response`; anything it throws, and any transport
  failure, counts toward `maxTransportRetries`.
- Produces: `window.POLL_DEFAULTS` — `{intervalMs, maxTransportRetries, maxDurationMs}`, the three
  numbers, as one small object a caller may read or a template may overwrite before the first
  call. This is the seam the chat follow-up uses to thread `agents/chat/service.py`'s
  `POLL_INTERVAL_MS` / `MAX_TRANSPORT_RETRIES` / `MAX_POLL_DURATION_MS` through
  `thread_context`'s existing `poll_interval_ms` / `max_transport_retries` /
  `max_poll_duration_ms` template variables.

**Why an include, not a script in `_shell.html` — read this before writing code.**

The C-16 ruling says *"one shared inline `pollUntilTerminal` helper in `_shell.html`'s single
`<script>`"*. **`foundation/templates/_shell.html` has no `<script>` tag.** It has one `<style>`
block and, at the very bottom, an empty `{% block scripts %}{% endblock %}`. Verify before
proceeding:

```bash
git -C <WORKTREE_ROOT> grep -n '<script' -- foundation/templates/_shell.html
```

That leaves two ways to put a script in the shell, and both are worse than an include:

- **Outside the block** (always rendered, on every page in the box). This adds one `<script>` tag
  to every rendered page. `agents/chat/tests/test_thread.py` asserts
  `body.count("<script") == 3` and `== 4` on the conversation page. **That file is held for PR
  #88** and may not be edited. This option is therefore forbidden by Global Constraint 10.
- **Inside `{% block scripts %}`** (rendered on every page that does not override the block).
  `conversation.html` overrides it without `{{ block.super }}`, so the held file survives — but
  every chat page that does *not* override it (the workstream page, the all-conversations page,
  the settings pages) gains a tag, breaking `agents/chat/tests/test_workstream_page.py`'s four
  `body.count("<script")` assertions and `test_all_conversations.py`'s three. That is seven test
  edits and a poller shipped to five pages that never poll, to satisfy a wording whose premise is
  factually wrong.

The include satisfies the ruling's actual content — *one* shared inline helper, no static asset, no
build step, owned by `foundation` — with a blast radius of exactly the two pages that opt in. It
also has two direct in-repo precedents: `agents/chat/templates/chat/_menu_exclusive.html` is a
template whose entire body is one inline `<script>`, included by the page that needs it; and
`foundation/templates/_messages.html` (parent plan Task 31) is a `foundation`-owned partial
included at fifteen sites. **This is recorded as an AMEND in the Plan review section and must be
confirmed by the orchestrator before the task is dispatched.**

**The three pollers, measured.** Re-derive every location before editing:

```bash
git -C <WORKTREE_ROOT> grep -n 'setTimeout\|function tick\|function poll' \
  -- tools/rag/templates/rag/ask.html tools/vision/templates/vision/create.html
```

| | `rag/ask.html` (`pollJobStatus`/`tick`) | `vision/create.html` (`poll`/`watch`) | `chat/conversation.html` (held) |
|---|---|---|---|
| reads | `response.json()` + `response.status` | `response.text()` (an HTML fragment) | JSON + a partial |
| terminal test | `data.state` in a five-way branch | the fresh card lacking `data-job-poll` | `data.state` |
| interval | `POLL_INTERVAL_MS = 2000` | literal `2000` | `{{ poll_interval_ms }}` |
| transport-failure retry | same 2000, ceiling `MAX_TRANSPORT_RETRIES = 3` | literal `5000`, **no ceiling** | 2000, ceiling 3 |
| non-ok response retry | handled inline per status | literal `2000` | inline |
| give-up ceiling | `MAX_POLL_DURATION_MS = 10 * 60 * 1000` | **none at all** | `{{ max_poll_duration_ms }}` |
| first poll | immediate | after 2000 ms | immediate |
| URL | fixed | changes per swapped card | fixed |

**Ruling on `create.html`'s missing ceilings (binding, made here per the brief):** **adopt the
shared ceilings.** `create.html` gets `maxTransportRetries = 3` and `maxDurationMs = 600000`,
matching the other two, and the commit body says so explicitly. A generation card that has been
polling a dead server for ten minutes in a background tab is not a card anybody is still watching,
and an unbounded retry loop was never a decision — it was the one poller nobody had got round to
bounding. `create.html`'s 5000 ms post-failure retry is preserved via `retryDelayMs`, and its 2000
ms first-poll delay via `startDelayMs`.

- [ ] **Step 1: Write the source-text gate first**

Create `foundation/ops/tests/test_shared_poller.py`. It is a pure text scan over templates — no
database, no client, no rendering.

**Copy the right precedent, and do not try to reuse the wrong helper.**
`foundation/ops/tests/test_css_ownership.py` has **no** `_read` / `_all_templates`. Its walker is
`_template_files() -> MappingProxyType[str, Path]`, `@lru_cache`d and keyed by **template-loader
name** (`_shell.html`, `chat/conversation.html`, `rag/ask.html`) — a different key space from the
repo-relative paths this gate wants, so importing it would `KeyError` on every constant below.

The precedent to copy is that module's `test_no_template_writes_its_own_flash_loop` (the C-19 gate
parent-plan Task 31 added). It **inlines** its walk, and its three moving parts are all
load-bearing — read its docstring before writing a line:

```python
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        if path.name == "_messages.html":
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if ".claude" in parts or ".venv" in parts:
            continue
        text = _COMMENT_RE.sub("", path.read_text())
        ...
```

1. **Exclusion by `path.relative_to(REPO_ROOT).parts`, never by a `"/.claude/" in str(path)`
   substring.** This worktree *lives under* `.claude/worktrees/hygiene-sweep`, so a substring check
   matches every file the scan can see and the test passes vacuously, having excluded everything
   before checking anything. That gate's docstring says so explicitly.
2. **`.venv` excluded the same way**, because vendored Django templates are not this repo's.
3. **`_COMMENT_RE.sub("", …)` before every check**, because templates in this repo routinely
   discuss in prose the very thing the pin forbids.

Point 3 is not optional here — it is what makes the gate's own first assertion true. The
`{% comment %}` block Step 3 puts at the top of `_poller.html` contains the literal text
`<script>` three times while explaining why the helper is an include; the **raw** source count is
therefore 4, not 1. Strip comments first and it is 1. (The *rendered* count is unaffected either
way — Django never emits `{% comment %}` — so Step 6's `body.count("<script>") == 3` on the
create page is correct as written.)

**Two decisions this plan makes so the implementer does not have to.**

1. **Inline the walk; do not lift a shared walker into `foundation/ops/tests/_helpers.py`.** The
   sibling gate inlines, and one `rglob` loop of six lines is not worth a shared abstraction with
   two callers in different key spaces. If a *third* such gate ever appears, that is the moment to
   extract one.
2. **Keys are repo-relative path strings** (`"foundation/templates/_poller.html"`,
   `"agents/chat/templates/chat/conversation.html"`), matching the sibling gate's
   `str(path.relative_to(REPO_ROOT))` offenders — **not** `_template_files()`'s loader names
   (`"_poller.html"`, `"chat/conversation.html"`). Both spellings are defensible; mixing them is
   not, and every constant in this file must use the first.

Import `_COMMENT_RE` and `REPO_ROOT` from `foundation.ops.tests.test_css_ownership` if that module
exports them at module level (re-derive with
`git grep -n '^_COMMENT_RE\|^REPO_ROOT' -- foundation/ops/tests/test_css_ownership.py`); otherwise
define the same two locally with a comment naming the sibling they mirror.

```python
"""C-16: one poll loop in this repository, not three.

`chat/conversation.html`, `rag/ask.html` and `vision/create.html` each
grew their own `fetch` + `setTimeout` retry loop, and the duplication was
self-acknowledged in-repo -- `conversation.html`'s own comment says its
timing constants are "copied from tools/rag/templates/rag/ask.html ...,
which is the poller this one mirrors", i.e. kept in sync BY HAND.

`foundation/templates/_poller.html` is the one loop now. This gate stops
a fourth from appearing and stops the two repointed pages from quietly
growing their own again.

`chat/conversation.html` IS EXEMPT AND SAID SO HERE: it is held for PR
#88 and is repointed in a follow-up once that merges (the exemption
below carries the date and the reason, matching this repo's own
exemption convention).
"""
```

with these tests:

```python
_POLLER = "foundation/templates/_poller.html"

# HELD FOR PR #88 -- repointed in the C-16 follow-up once that branch
# merges. Not a permanent exemption: the follow-up deletes this entry.
_EXEMPT = {"agents/chat/templates/chat/conversation.html"}


def _text(rel: str) -> str:
    """The template at `rel` (repo-relative), `{% comment %}` regions
    stripped -- the same convention every pin in `test_css_ownership.py`
    uses, and load-bearing here: `_poller.html`'s own comment block
    explains the include-vs-shell decision and says the word `<script>`
    three times while doing it."""
    return _COMMENT_RE.sub("", (REPO_ROOT / rel).read_text())


def test_the_shared_helper_exists_and_defines_the_function_once():
    text = _text(_POLLER)
    assert text.count("<script>") == 1
    assert "window.pollUntilTerminal" in text
    assert "window.POLL_DEFAULTS" in text


@pytest.mark.parametrize("path", ["tools/rag/templates/rag/ask.html",
                                  "tools/vision/templates/vision/create.html"])
def test_a_repointed_page_defines_no_loop_of_its_own(path):
    """The teeth. A page that calls the helper must not ALSO keep a
    hand-rolled retry loop -- which is exactly how the three copies grew
    in the first place. `setInterval` is banned outright (no page in this
    repo has ever used it, and it is the other spelling of this bug);
    `setTimeout` is allowed only inside the helper, so its absence here
    is what proves the loop actually moved."""
    text = _text(path)
    assert '{% include "_poller.html" %}' in text
    assert "pollUntilTerminal(" in text
    assert "setInterval" not in text
    assert "setTimeout" not in text


def test_no_template_outside_the_helper_rolls_its_own_poll_loop():
    """The repo-wide half: every template, not just the two repointed
    ones. Deferred scheduling is the helper's job and nobody else's.

    MEASURED, not guessed: before this task `setInterval` appeared in
    ZERO templates repo-wide, and `setTimeout` in exactly three -- the
    three pollers C-16 is about. So the condition can be the strict one
    (either spelling, anywhere) rather than a compound heuristic about
    `fetch(` being nearby, and a page that grows a timer for some
    unrelated reason will fail this test and have to argue its case here
    by name. That is the intended strictness, not an accident."""
    offenders = []
    for path in sorted(REPO_ROOT.rglob("*.html")):
        parts = path.relative_to(REPO_ROOT).parts
        if ".claude" in parts or ".venv" in parts:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if rel == _POLLER or rel in _EXEMPT:
            continue
        text = _COMMENT_RE.sub("", path.read_text())
        if "setInterval" in text or "setTimeout" in text:
            offenders.append(rel)
    assert offenders == [], offenders
```

**Re-take the measurement before trusting the strict form** — an earlier task in this addendum, or
a peer, may have added a timer since:

```bash
git -C <WORKTREE_ROOT> grep -ln 'setInterval' -- '*.html'   # expect: nothing
git -C <WORKTREE_ROOT> grep -ln 'setTimeout'  -- '*.html'   # expect: exactly the three pollers
```

If a fourth template now uses `setTimeout` for something that is genuinely not a poll loop, do
**not** silently loosen the condition to the `and "fetch(" in text` compound — add that template to
a second, separately-named exemption set with a one-line reason, so the loosening is visible in the
diff rather than buried in a boolean. Record what you found in the task report either way.

- [ ] **Step 2: Run the gate and watch it fail**

```bash
export DATABASE_URL='<TEST_DATABASE_URL>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_shared_poller.py -v
```

Expected: `test_the_shared_helper_exists_and_defines_the_function_once` fails (no such file); both
per-page tests fail on the missing `{% include %}`; the repo-wide test fails naming `ask.html`,
`create.html` and `conversation.html` — the last of which then goes into `_EXEMPT`. Record the
offender list in the task report; it is the measurement that justifies the exemption.

- [ ] **Step 3: Write the shared helper**

Create `foundation/templates/_poller.html`:

```html
{% comment %}
THE ONE POLL LOOP (C-16). Three pages grew their own `fetch` +
`setTimeout` retry loop with three sets of hand-synchronised timing
constants -- `chat/conversation.html`'s own comment admitted it was
copied from `rag/ask.html` "by hand". This is that loop, once.

AN INCLUDE, NOT A STATIC ASSET, and not a script in `_shell.html`. The
standing doctrine is "no static JS/CSS pipeline -- the single inline
`<style>`/`<script>` shell stays", which this obeys: an inline
`<script>` in a template, included by the pages that need it.
`chat/_menu_exclusive.html` is the same shape. A script placed in
`_shell.html` itself would render on every page in the box (or on every
page that does not override `{% templatetag openblock %} block scripts
{% templatetag closeblock %}`), which is a poller shipped to pages that
never poll and a `<script>` count changed on pages that count them.

WHAT THIS OWNS: WHEN to poll -- the interval, the transport-retry
ceiling, the give-up ceiling, and the scheduling. WHAT IT NEVER OWNS:
what a response MEANS. `onResponse` gets the raw `Response`; whether the
job is finished, whether to swap a fragment or render JSON, and what to
show the operator are the page's, because the three pages genuinely
differ there (JSON+state on Ask, an HTML fragment swap on Images).

TUNING: `window.POLL_DEFAULTS` holds the three numbers. A caller
overrides any of them per call; a template may overwrite the object
before the first call, which is how `agents/chat/service.py`'s
`POLL_INTERVAL_MS`/`MAX_TRANSPORT_RETRIES`/`MAX_POLL_DURATION_MS` reach
the chat page's copy through `thread_context`'s existing template
variables when that page is repointed (held for PR #88).
{% endcomment %}
<script>
(function () {
  window.POLL_DEFAULTS = {
    intervalMs: 2000,
    maxTransportRetries: 3,
    maxDurationMs: 10 * 60 * 1000
  };

  // Poll `url` until `onResponse` says to stop, a run of transport
  // failures exceeds the retry ceiling, or the whole poll outlives the
  // give-up ceiling -- so a stuck poll can never run forever in a
  // background tab.
  //
  // `onResponse(response)` returns (or resolves to):
  //   a falsy value, or {done: true}  -> stop
  //   {retry: true, url, delayMs}     -> poll again; `url` and
  //                                      `delayMs` are both optional
  // Anything it throws counts as a transport failure, which is the same
  // shape the hand-written loops had: a JSON parse error and a dropped
  // connection were both caught by one `.catch`.
  window.pollUntilTerminal = function (url, opts) {
    var o = opts || {};
    // The three TUNING numbers are read from POLL_DEFAULTS only -- there
    // is deliberately no per-call override for them. That object IS the
    // tuning seam (it is what the held chat page will assign to when it
    // is repointed), and a second way to set the same number, with no
    // caller, would be a knob nothing reads. The per-call options are
    // the two SCHEDULING quirks that genuinely differ between the two
    // pages, plus the two callbacks.
    var d = window.POLL_DEFAULTS;
    var intervalMs = d.intervalMs;
    var maxRetries = d.maxTransportRetries;
    var maxDurationMs = d.maxDurationMs;
    var retryDelayMs = o.retryDelayMs === undefined ? intervalMs : o.retryDelayMs;
    var startDelayMs = o.startDelayMs === undefined ? 0 : o.startDelayMs;
    var onResponse = o.onResponse;
    var onGiveUp = o.onGiveUp || function () {};

    var nextUrl = url;
    var failures = 0;
    var startedAt = Date.now();

    function schedule(delayMs) { setTimeout(tick, delayMs); }

    function tick() {
      if (Date.now() - startedAt > maxDurationMs) { onGiveUp("duration"); return; }
      fetch(nextUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (response) { return onResponse(response); })
        .then(function (outcome) {
          // Reset AFTER onResponse resolves, not before it runs: a
          // server answering a 500 HTML page rejects inside
          // `response.json()`, and that must still count as a transport
          // failure. Resetting earlier would make the counter
          // unreachable and turn a broken server into a ten-minute
          // silent poll. This is ask.html's own placement.
          failures = 0;
          if (!outcome || !outcome.retry) { return; }
          if (outcome.url) { nextUrl = outcome.url; }
          schedule(outcome.delayMs === undefined ? intervalMs : outcome.delayMs);
        })
        .catch(function () {
          failures += 1;
          if (failures > maxRetries) { onGiveUp("transport"); return; }
          schedule(retryDelayMs);
        });
    }

    schedule(startDelayMs);
  };
})();
</script>
```

**The `failures = 0` placement is load-bearing — do not move it "up" for tidiness.** Read
`ask.html`'s current loop before touching it: the reset is in the **second** `.then`, *after*
`response.json()` has already resolved —

```javascript
      fetch(statusUrl, {...})
        .then(function (response) {
          return response.json().then(function (data) {          // parse happens HERE
            return { status: response.status, data: data };
          });
        })
        .then(function (result) {
          transportFailures = 0;                                  // reset happens HERE
```

so today a server answering a 500 HTML page rejects during the parse, never reaches the reset, and
the poller gives up after four attempts with *"Lost contact with the server — reload this page."*
If the helper reset the counter before `onResponse` parsed, `failures` could never exceed
`maxRetries`: the page would retry for the full ten minutes and then report the **wrong** sentence
(*"Still queued after 10 minutes…"*). That is an observable regression in a task whose whole claim
is that behaviour does not change. The helper above therefore resets in the outcome handler, which
is byte-for-byte `ask.html`'s semantics — and strictly better for `create.html`, which has no
ceiling at all today.

- [ ] **Step 4: Repoint `rag/ask.html`**

Inside `{% block scripts %}`, add the include as the **first** line of the block (before the
page's own `<script>`), so the helper is defined before anything calls it:

```html
{% block scripts %}
{% include "_poller.html" %}
<script>
```

Delete the three `var POLL_INTERVAL_MS`/`MAX_TRANSPORT_RETRIES`/`MAX_POLL_DURATION_MS` declarations
and their comment block — the numbers live in `POLL_DEFAULTS` now — and replace the whole
`pollJobStatus` function with:

```javascript
  function pollJobStatus(statusUrl, submitBtn, statusEl) {
    function finish() {
      submitBtn.disabled = false;
    }

    pollUntilTerminal(statusUrl, {
      onResponse: function (response) {
        return response.json().then(function (data) {
          if (response.status === 404) {
            statusEl.textContent = "";
            showError(data.error || "That question is no longer in the queue.");
            finish();
            return null;
          }
          if (response.status === 503) {
            statusEl.textContent = "";
            showError(data.error || "The queue isn't ready yet.", data.setup_url);
            finish();
            return null;
          }

          if (data.state === "queued") {
            statusEl.textContent = data.position === 1
              ? "Waiting…"
              : (typeof data.position === "number"
                  ? "Position " + data.position + " of queue — higher-priority questions can move ahead."
                  : "Queued…");
            return { retry: true };
          }
          if (data.state === "running") {
            statusEl.textContent = "Answering…";
            return { retry: true };
          }
          if (data.state === "succeeded") {
            statusEl.textContent = "";
            renderResults(data);
            finish();
            return null;
          }
          if (data.state === "failed") {
            statusEl.textContent = "";
            showError(data.error || "The question failed.", data.setup_url);
            finish();
            return null;
          }
          if (data.state === "cancelled") {
            statusEl.textContent = "";
            showError("This question was cancelled.");
            finish();
            return null;
          }
          // Unrecognized state: keep polling rather than silently
          // stopping -- a forward-compatible state added later should
          // not strand the page mid-question.
          return { retry: true };
        });
      },
      onGiveUp: function (reason) {
        statusEl.textContent = "";
        if (reason === "duration") {
          showError(
            "Still queued after 10 minutes — this page stopped checking. " +
            "Your question is still in the queue; check Ask history when it finishes."
          );
        } else {
          showError("Lost contact with the server — reload this page. Your question is still in the queue.");
        }
        finish();
      }
    });
  }
```

Every operator sentence above is copied verbatim from the current file, including the typographic
ellipses (`…`) and em dashes (`—`). **Copy them from the file; do not retype them** (Global
Constraint 17).

The page's `fetch("{% url 'rag-ask' %}", {method: "POST", …})` submit handler is **not** a poll and
is not touched.

- [ ] **Step 5: Repoint `vision/create.html`**

Same include placement — the first line inside `{% block scripts %}`. Replace `poll` and `watch`
with:

```javascript
  function watch(card) {
    pollUntilTerminal(card.getAttribute('data-job-poll'), {
      // The first poll waits, exactly as before: a card inserted a
      // moment ago has not reached the engine yet.
      startDelayMs: 2000,
      // A transport failure backs off further than an ordinary tick
      // does -- this page's own long-standing choice, preserved.
      retryDelayMs: 5000,
      onResponse: function (response) {
        if (response.status === 404) {
          card.removeAttribute('data-job-poll');
          card.innerHTML = '<p class="muted">This generation was removed.</p>';
          return null;
        }
        if (!response.ok) { return { retry: true }; }
        return response.text().then(function (html) {
          var holder = document.createElement('div');
          holder.innerHTML = html.trim();
          var fresh = holder.firstElementChild;
          card.replaceWith(fresh);
          markLive(fresh);
          card = fresh;
          var next = fresh.getAttribute('data-job-poll');
          return next ? { retry: true, url: next } : null;
        });
      },
      onGiveUp: function () {
        // The card keeps whatever the last swap left on it; the
        // fragment's own "Refresh to update." note is the honest
        // fallback, the same one a JS-off visitor reads.
        card.removeAttribute('data-job-poll');
      }
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('[data-job-poll]'), watch);
  Array.prototype.forEach.call(document.querySelectorAll('.job-card'), markLive);
```

`markLive` is unchanged. The picker's progressive-enhancement block below it is unchanged.

Update the block comment at the top of that `<script>` to say where the loop went:

```javascript
// Poll every non-terminal card and swap in the fresh fragment; a card
// without data-job-poll is finished, so polling stops by itself. The
// loop itself is `_poller.html`'s `pollUntilTerminal` (C-16) -- this
// page owns only what a response MEANS. No external assets, and the
// page works fully without this script.
```

**The two ceilings this page did not have.** It now inherits `POLL_DEFAULTS`'
`maxTransportRetries: 3` and `maxDurationMs: 600000`. That is a deliberate behaviour change, ruled
above, and the commit body must say so.

- [ ] **Step 6: Update the one `<script>` count assertion**

```bash
git -C <WORKTREE_ROOT> grep -n 'count("<script' -- tools/vision/tests/
```

In `tools/vision/tests/test_views_create.py`, the assertion `assert body.count("<script>") == 2`
(in the test whose docstring says *"The page's other script (polling/XHR submit) is still present,
unchanged, and it is a SEPARATE `<script>` block"*) becomes:

```python
        # The page's other script (the job-card poller) is still present
        # and is a SEPARATE `<script>` block. THREE since C-16: the third
        # is `foundation/templates/_poller.html`, the shared
        # `pollUntilTerminal` loop this page now includes instead of
        # rolling its own.
        assert body.count("<script>") == 3
```

The two `script_slice.count("<script>") == 1` assertions in the same file are scoped to the
generate `<form>` block and are unaffected — leave them.

Also run, and reconcile against the expectation that **nothing else changes**:

```bash
git -C <WORKTREE_ROOT> grep -n 'count("<script' -- '*.py'
```

`agents/chat/tests/test_thread.py` (3, 4), `test_workstream_page.py` (1, 4, 2, 1) and
`test_all_conversations.py` (1, 2, 1) must all still pass **untouched**. If any of them fails, the
helper was placed somewhere it renders unconditionally — revert to the include and re-read the
"Why an include" note.

- [ ] **Step 7: Document the new partial in `foundation/README.md`**

**Not `docs/ARCHITECTURE.md`** — that file is 315 lines and never mentions scripts, JavaScript,
static assets or a build pipeline. Its only match for "script" is the substring inside
*"de**script**ion"*. There is no script-posture sentence there to amend, and inventing a section
would be this task writing architecture prose it was not asked to write. Verify before deciding
otherwise:

```bash
git -C <WORKTREE_ROOT> grep -in 'javascript\|inline script\|no static' -- docs/ARCHITECTURE.md
```

The right home is **`foundation/README.md`**, which already carries the shared-template inventory
(`_shell.html` at :24, `_settings.html` at :35–40, and the shell's own owned controls at :49) and
is the column that will own `_poller.html`. Add an entry in that inventory's own style, beside the
others:

```markdown
- **`foundation/templates/_poller.html`** — the one poll loop
  (`pollUntilTerminal(url, opts)`), included by the pages that watch a
  queued job: `rag/ask.html` and `vision/create.html`. An **include, not
  part of the shell**: a script in `_shell.html` would render on every
  page in the box, and most pages never poll. The helper owns *when* to
  poll — interval, transport-retry ceiling, give-up ceiling, scheduling —
  and never what a response means, which is why Ask can keep a JSON state
  machine while Images swaps an HTML fragment. Tuning lives in one
  `window.POLL_DEFAULTS` object so a page can override it.
  `chat/conversation.html` still has its own copy, held for PR #88.
```

Re-derive the surrounding line numbers with `git grep -n '_shell.html' -- foundation/README.md`
and match the file's existing bullet voice rather than pasting this verbatim if it reads
differently.

While in that file, note that `foundation/README.md` is also one of the files Task 42's Step 7b
sweep touches — if Task 42 has already run, re-read before editing.

- [ ] **Step 8: Run the gate, the two pages' tests, and the exit gate**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_shared_poller.py -v
```
Expected: all pass.

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests tools/rag/tests agents/chat/tests foundation/ops/tests
FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q tools/vision/tests tools/rag/tests agents/chat/tests foundation/ops/tests
```

`agents/chat/tests` is in the run **specifically** to prove the held files' `<script>` counts did
not move, per the orchestrator's standing "after any shell-level template edit, run
`tools/vision/tests` and `agents/chat` tests" ruling.

- [ ] **Step 9: Manual check (the poller is JavaScript; no unit test executes it)**

The repo has no JS test runner and this plan does not add one. Before reporting the task done,
exercise both pages against the branch preview and paste the observations into the task report:

1. `/rag/` — ask a question, watch the status text move through Queued/Position → Answering… →
   the rendered answer. Then stop the queue worker mid-question and confirm the page eventually
   reports the ten-minute give-up sentence rather than spinning silently.
2. `/vision/create/` — submit a generation, watch the card swap through its states to the final
   image. Confirm the "Refresh to update." note still becomes "Waiting for the engine…" on the
   inserted card and on each swapped-in card.

If the preview stack is not up, say so plainly in the report rather than claiming the pages work.

- [ ] **Step 10: Commit**

```bash
git -C <WORKTREE_ROOT> add -A
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
refactor(frontend): one poll loop, included by the two pages that poll

C-16. `rag/ask.html` and `vision/create.html` each carried their own
fetch + setTimeout retry loop, and the third copy (chat) says in its own
comment that its timing constants were "copied from
tools/rag/templates/rag/ask.html ..., which is the poller this one
mirrors" -- kept in sync by hand. `foundation/templates/_poller.html` is
that loop once: it owns WHEN to poll (interval, transport-retry ceiling,
give-up ceiling, scheduling) and never what a response means, so Ask
keeps its JSON state machine and Images keeps its fragment swap.

AN INCLUDE, NOT A SHELL SCRIPT. `_shell.html` has no `<script>` tag; a
script added there would render on every page in the box (or on every
page not overriding `block scripts`), which changes `<script>` counts on
pages that assert them -- including a file held for PR #88. An inline
`<script>` in a `foundation`-owned partial, included by the two pages
that poll, is the same shape as `chat/_menu_exclusive.html` and
`foundation/templates/_messages.html`, and keeps the standing "no static
JS/CSS pipeline" ruling exactly.

ONE DELIBERATE BEHAVIOUR CHANGE: `vision/create.html` had no give-up
ceiling and no transport-retry ceiling at all -- a card polling a dead
server retried forever in a background tab. It adopts the shared
ceilings (3 consecutive transport failures, 10 minutes overall). Its own
5s post-failure backoff and 2s first-poll delay are preserved through
`retryDelayMs`/`startDelayMs`.

chat/conversation.html is HELD for PR #88 and keeps its fork for one
more PR; the follow-up that repoints it is written out in the plan and
recorded as the one dated exemption in the new gate.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

#### Follow-up (NOT part of this task): repoint `chat/conversation.html` after PR #88 merges

Recorded here so it is not lost. Do **not** do any of this in Task 43.

**Trigger:** PR #88 (`chat-poller-cleanup`, worktree `.claude/worktrees/vision-generation`) merges
to `main` and the four held files are released.

**Files:** `agents/chat/templates/chat/conversation.html`, `agents/chat/tests/test_thread.py`,
`agents/chat/README.md`, `foundation/ops/tests/test_shared_poller.py` (delete the `_EXEMPT` entry).

**Steps:**
1. Re-read `conversation.html`'s poller as #88 left it — that branch is already changing it, so the
   loop this plan measured may not be the loop that lands.
2. Add `{% include "_poller.html" %}` as the first line of its `{% block scripts %}`, and set the
   page's tuning from the template variables `thread_context` already provides, before the first
   call:
   ```javascript
   window.POLL_DEFAULTS.intervalMs = {{ poll_interval_ms }};
   window.POLL_DEFAULTS.maxTransportRetries = {{ max_transport_retries }};
   window.POLL_DEFAULTS.maxDurationMs = {{ max_poll_duration_ms }};
   ```
   This is why `POLL_DEFAULTS` is a mutable object rather than three frozen literals.
3. Replace `poll()`/`tick()` with a `pollUntilTerminal` call whose `onResponse` keeps every branch
   of the existing state machine and whose `onGiveUp` keeps both existing give-up sentences.
4. `agents/chat/tests/test_thread.py` asserts `var POLL_INTERVAL_MS = {POLL_INTERVAL_MS};` and its
   two siblings, and `body.count("<script")` at 3 and 4. Update the three constant assertions to
   the `window.POLL_DEFAULTS.intervalMs = …` spelling and both counts to 4 and 5 (the include adds
   one tag). **Updated, not deleted** — the pin that the Python constants reach the page is the
   whole reason they live in `agents/chat/service.py`.
5. Delete `conversation.html` from `_EXEMPT` in `foundation/ops/tests/test_shared_poller.py` and
   watch `test_no_template_outside_the_helper_rolls_its_own_poll_loop` stay green.
6. `agents/chat/README.md`: update whatever it says about the page's own poll loop.
7. Commit: `refactor(chat): the thread poller joins the shared loop`.

---

### Task 44: `Worker._evict_to_match_plan` becomes four named phases (C-48a)

**Files:**
- Test: `models/queue/tests/test_worker.py` — four new characterization tests in the existing
  `TestEviction` class (line ~1501; re-derive), written and committed **first**
- Modify: `models/queue/worker.py` — `Worker._evict_to_match_plan` (line ~801; re-derive) and four
  new private methods beside it
- Modify: `models/queue/README.md` — the eviction paragraph, if it names the function's internals
  (`git grep -n 'evict' -- models/queue/README.md` decides)

**Column:** everything stays inside `models.queue`. No new import.

**Interfaces:**
- Produces, all private methods on `Worker`, all called only from `_evict_to_match_plan`:
  - `_eviction_targets(self, claimed: list[dict]) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]], set[tuple[str, str]]] | None`
    — `(needed_keys, endpoints, exclusive_endpoints)`, or `None` when no job is RUNNING.
  - `_residency_snapshot(self, endpoints, claimed, budget_bytes) -> tuple[dict[tuple[str, str], list], bool]`
    — `(installed_by_endpoint, over_budget)`.
  - `_evict_exclusive_endpoints(self, exclusive_endpoints, installed_by_endpoint, needed_keys) -> None`
  - `_evict_for_budget(self, installed_by_endpoint, exclusive_endpoints, needed_keys) -> None`

**The finding.** `ruff C901` scores `_evict_to_match_plan` at complexity 33 with 32 branches and
81 statements — the highest in the tree. It is four phases the function's own comments already
name: gather needed keys and endpoints from RUNNING jobs; probe actual residency and compute the
budget verdict; a Pass-1 uncapped eviction over exclusive endpoints; a Pass-2 capped eviction over
the rest.

**Ruling (binding).** *"Split only functions with clear phase boundaries: `models/queue/worker.py`
`_evict_to_match_plan` (4 phases, complexity 33) … Cost if wrong: a behaviour change hidden in a
split; characterization tests first."*

**The load-bearing ordering a split must not disturb** — the docstring says all of this and the
split must preserve every word of it:
- Pass 1 is **uncapped** and calls `self._maybe_heartbeat()` after every unload attempt, because
  `tick()` registers this batch's tokens in `self._active_tokens` *before* calling this function.
- Pass 2 is capped at `MAX_UNLOADS_PER_TICK`, counted across endpoints (`unloads_this_tick` is a
  single counter over the outer *and* inner loop, with a `break` in both).
- `resident_sizes[key]` being `None` contributes 0 bytes — a deliberate under-count, argued for at
  length in an inline comment.
- `admitted_marginal` is MAX-folded per key, never summed per job.
- `installed_by_endpoint` is populated **only** for endpoints whose `list_installed` succeeded, and
  Pass 2 iterates that dict — so an endpoint that failed to list is skipped by both passes.

- [ ] **Step 1: Name the characterization tests that already exist**

`models/queue/tests/test_worker.py`'s `TestEviction` class already pins the behaviour. Re-derive
the list and paste it into the task report:

```bash
git -C <WORKTREE_ROOT> grep -n 'class TestEviction' -A 400 \
  -- models/queue/tests/test_worker.py | grep 'def test_'
```

The ten expected to be there:

| test | phase it pins |
|---|---|
| `test_unneeded_resident_model_is_unloaded_when_over_budget` | 1→2→4 end to end |
| `test_no_eviction_when_within_budget` | 2's `over_budget` verdict |
| `test_no_budget_set_skips_eviction_entirely` | the `budget_bytes is None` early return |
| `test_engine_without_unload_does_not_crash` | 3/4's `getattr(engine, "unload")` degradation |
| `test_exclusive_admitted_job_evicts_everything_else_at_its_endpoint` | 3 |
| `test_exclusive_eviction_is_uncapped_even_past_max_unloads_per_tick` | 3's uncapped rule |
| `test_unload_attempts_capped_per_tick` | 4's `MAX_UNLOADS_PER_TICK` |
| `test_unload_returning_false_is_logged_and_loop_proceeds` | 3/4's refusal handling |
| `test_trailing_slash_endpoint_normalized_consistently` | 1/2's `norm_endpoint` key discipline |
| `test_heartbeat_advances_during_uncapped_exclusive_eviction_pass` | 3's `_maybe_heartbeat` call site |

**If any of the ten is missing or renamed, stop and report** — the peer's registry-suite split
(commit `04d13c1`) did not touch `models/queue/tests/test_worker.py` at all, so a difference here
means the plan's measurement is stale.

- [ ] **Step 2: Write the four characterization tests the existing ten do not cover**

Add to `TestEviction`, copying its existing idiom. **Read from the file, not assumed:** the
fixture is `register_engine`, which takes a built engine object, registers it into
`models.contracts.engines.ENGINES` and pops it again at teardown; the class's tests build
`FakeEngine("fakeengine", installed=[InstalledModel(model_id=..., loaded=True, loaded_size=...)])`,
call `_set_budget(...)` and `_job(state=RUNNING, model_refs=[_ref(...)], ...)`, then construct
`Worker(worker_id="w2")`, call `worker._evict_to_match_plan(claimed)` inside a
`try/finally: worker._executor.shutdown(wait=False)`, and assert on `engine.unload_calls`.

**Two of the four new tests need engine stubs that do not exist yet.** `FakeEngineNoOptionalMethods`
is *not* the one to reuse — it drops `loaded_footprint`/`unload` but **still defines
`list_installed`**, so it cannot exercise the probe-side degradations. Add two small stubs beside
it, in the same file and the same shape:

```python
class FakeEngineNoListInstalled:
    """`FakeEngine` minus `list_installed` -- an adapter that cannot be
    asked what is resident. `_residency_snapshot` must warn once per
    engine+method and contribute no endpoint, so neither eviction pass
    can touch it."""

    well_known_ports: tuple[int, ...] = ()

    def __init__(self, name: str):
        self.name = name
        self.unload_calls: list[tuple[str, str]] = []

    def is_healthy(self, endpoint, timeout=None):
        return True

    def unload(self, endpoint, model_id):
        self.unload_calls.append((endpoint, model_id))
        return True

    def build_llm(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError

    def build_embedder(self, model_id, endpoint, **cfg):  # pragma: no cover - unused here
        raise NotImplementedError


class FakeEngineListInstalledRaises(FakeEngineNoListInstalled):
    """`list_installed` blows up. Eviction must never block a launch, so
    the endpoint is skipped, the failure is logged, and every OTHER
    endpoint is still probed and still evicted from."""

    def list_installed(self, endpoint):
        raise RuntimeError("engine exploded")
```

`unload_calls` is present on both so a test can assert **no** unload happened. Re-derive
`FakeEngine`'s exact attribute list before copying it —
`git grep -n 'class FakeEngine' -A 45 -- models/queue/tests/test_worker.py`.

The four tests:

```python
    def test_no_running_job_at_all_is_an_early_return(self, register_engine):
        """PHASE 1's second early return. `claim_and_admit` persists this
        tick's admissions as `running` before this is called, so an empty
        `running_refs` means there is genuinely nothing to plan against
        -- the engine must not be probed at all. Pinned before the split
        because the return moves into a helper that answers `None`."""
        # a budget IS set, and an engine IS registered whose list_installed
        # records its calls; no InferenceJob is in RUNNING state.
        # assert the recorded list_installed calls == []

    def test_an_engine_without_list_installed_is_warned_once_and_skipped(self, register_engine):
        """PHASE 2's OTHER degradation. `test_engine_without_unload_does_
        not_crash` covers the `unload` half; this covers the probe half --
        an engine lacking `list_installed` is logged once per
        engine+method (`_warn_missing_method_once`) and contributes no
        endpoint to `installed_by_endpoint`, so neither pass can touch
        it. Two calls, one warning."""

    def test_a_list_installed_that_raises_skips_that_endpoint_and_never_propagates(self, register_engine):
        """PHASE 2's `except Exception` -- "eviction must never block a
        launch". The endpoint is absent from `installed_by_endpoint`,
        every OTHER endpoint is still probed and still evicted from, and
        `_evict_to_match_plan` returns normally."""

    def test_a_resident_model_with_no_reported_size_counts_as_zero_bytes(self, register_engine):
        """PHASE 2's documented UNDER-count, argued for in a long inline
        comment: `list_installed` reporting a loaded model with no
        `loaded_size` contributes 0 to `actual_resident_bytes`, so the
        budget verdict can come out UNDER where a real accounting would
        be over. Deliberate, not a bug -- the scheduler's rule 2b already
        makes an unknown-footprint RUNNING job run alone. Pinned here so
        the split cannot quietly 'fix' it."""
```

The implementer writes the bodies from `TestEviction`'s existing idiom: build the engine double
with `register_engine`, create `InferenceJob` rows in the states the test needs, call
`worker._evict_to_match_plan(claimed)` directly, and assert on the double's recorded calls. Every
docstring above is mandatory; the bodies are not prescribed because the fixture's exact shape is
the file's to define.

- [ ] **Step 3: Run the four new tests against the UNSPLIT function**

```bash
export DATABASE_URL='<TEST_DATABASE_URL>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  models/queue/tests/test_worker.py::TestEviction -v
```

Expected: **all fourteen pass.** These are characterization tests, not regression tests — they
describe what the code already does. A failure here means the plan's reading of the function is
wrong; stop and report rather than changing production code to match the test.

- [ ] **Step 4: Commit the characterization tests alone**

```bash
git -C <WORKTREE_ROOT> add models/queue/tests/test_worker.py
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
test(queue): characterize the four phases of _evict_to_match_plan

C-48a, first half. Ten tests already pin the eviction passes; these four
pin the phase boundaries the split is about to draw and the degradations
nothing was asserting: no RUNNING job at all, an engine with no
`list_installed`, a `list_installed` that raises, and a resident model
with no reported size (the documented deliberate under-count).

Green against the unsplit function -- they describe what it does, so the
split has something to be measured against.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 5: Extract phase 1**

Add above `_evict_to_match_plan`:

```python
    def _eviction_targets(
        self, claimed: list[dict]
    ) -> tuple[set[tuple[str, str, str]], set[tuple[str, str]], set[tuple[str, str]]] | None:
        """PHASE 1 of `_evict_to_match_plan` (which see for the whole
        argument): what the machine is supposed to be holding.

        `(needed_keys, endpoints, exclusive_endpoints)`, or `None` when
        no job is RUNNING -- the caller returns immediately on `None`,
        which is what stops the engine being probed for nothing.

        `needed_keys`/`endpoints` come from RUNNING jobs' `model_refs`.
        By the time this runs, `claim_and_admit` has already persisted
        THIS tick's admissions as `running`, so one query covers
        "running union admitted" without merging two collections.
        `exclusive_endpoints` comes from `claimed` instead -- an
        admitted-and-exclusive job's own endpoints, derived from THIS
        tick's batch and never recomputed later."""
        running_refs = list(
            InferenceJob.objects.filter(state=RUNNING).values_list("model_refs", flat=True)
        )
        if not running_refs:
            return None

        needed_keys: set[tuple[str, str, str]] = set()
        endpoints: set[tuple[str, str]] = set()
        for model_refs in running_refs:
            for ref in model_refs:
                key = (ref["engine"], norm_endpoint(ref["endpoint"]), norm_tag(ref["model_id"]))
                needed_keys.add(key)
                endpoints.add((key[0], key[1]))

        exclusive_endpoints: set[tuple[str, str]] = {
            (ref["engine"], norm_endpoint(ref["endpoint"]))
            for descriptor in claimed
            if descriptor.get("exclusive")
            for ref in descriptor["model_refs"]
        }
        return needed_keys, endpoints, exclusive_endpoints
```

- [ ] **Step 6: Extract phase 2**

```python
    def _residency_snapshot(
        self,
        endpoints: set[tuple[str, str]],
        claimed: list[dict],
        budget_bytes: int,
    ) -> tuple[dict[tuple[str, str], list], bool]:
        """PHASE 2 of `_evict_to_match_plan` (which see): what the
        machine is ACTUALLY holding, and whether that plus what is about
        to load exceeds the budget.

        `(installed_by_endpoint, over_budget)`. An endpoint is in the
        dict ONLY if its engine resolved, offered `list_installed`, and
        that call returned -- so an engine that lacks the method (warned
        once, per engine+method) or whose call raised (logged; eviction
        must never block a launch) is absent from the dict and is
        therefore untouched by both eviction passes.

        `over_budget` is `actual_resident_bytes + admitted_marginal >
        budget_bytes`. Both halves keep their exact prior arithmetic --
        see the two inline comments below, which are the reasoning for
        the deliberate under-count and for the MAX fold, and are the
        parts of this function most likely to be 'tidied' into a bug."""
```

with the body being, verbatim, the current code from `installed_by_endpoint: dict[...] = {}` down
to and including `over_budget = actual_resident_bytes + admitted_marginal > budget_bytes`, both
long inline comments included word for word, ending `return installed_by_endpoint, over_budget`.

**`resident_sizes` becomes local to this method.** Confirm with
`git grep -n 'resident_sizes' -- models/queue/worker.py` that no other line reads it — at the time
of writing, only phase 2 does (`if key in resident_sizes` inside the `admitted_new_keys` loop,
which is inside phase 2).

- [ ] **Step 7: Extract phases 3 and 4**

```python
    def _evict_exclusive_endpoints(
        self,
        exclusive_endpoints: set[tuple[str, str]],
        installed_by_endpoint: dict[tuple[str, str], list],
        needed_keys: set[tuple[str, str, str]],
    ) -> None:
        """PASS 1 of `_evict_to_match_plan` (which see for the full
        argument): every non-needed resident model at an endpoint an
        admitted-EXCLUSIVE job owns is unloaded, full stop.

        UNCAPPED, deliberately -- NOT subject to `MAX_UNLOADS_PER_TICK`.
        The safety mechanism is not the cap: `tick()` registers this
        batch's tokens in `self._active_tokens` BEFORE calling the
        caller at all, so `self._maybe_heartbeat()` -- called after every
        unload attempt in the loop below, not once around it --
        genuinely refreshes the exclusive job's row DURING this pass.
        Removing that call, or hoisting it out of the loop, reintroduces
        the stale-row window this pass was fixed to close."""

    def _evict_for_budget(
        self,
        installed_by_endpoint: dict[tuple[str, str], list],
        exclusive_endpoints: set[tuple[str, str]],
        needed_keys: set[tuple[str, str, str]],
    ) -> None:
        """PASS 2 of `_evict_to_match_plan` (which see): non-exclusive,
        budget-driven eviction, CAPPED at `MAX_UNLOADS_PER_TICK` unload
        calls per call.

        Called only when phase 2 said `over_budget` -- the caller makes
        that decision, so this method's own loop no longer re-checks a
        flag that cannot change inside it.

        `unloads_this_tick` is ONE counter across the endpoint loop and
        the model loop, with a `break` in each: the cap bounds total
        unload calls, not calls per endpoint. This runs on the heartbeat
        thread, and an unbounded run of slow `unload()` calls would eat
        the margin `STALE_AFTER_SECONDS` assumes. Whatever this cap
        leaves undone is picked up on a later tick that itself admits
        something -- not necessarily the next one."""
```

Both bodies are the current Pass-1 and Pass-2 loops verbatim, including every `logger.warning`
call, the `self._warn_missing_method_once(engine_name, "unload")` calls, and Pass 1's
`self._maybe_heartbeat()` with its comment.

**One deliberate change in Pass 2, and only one:** the current loop contains
`if not over_budget: continue` inside the endpoint loop. The caller now guards the whole call, so
that line is deleted and `over_budget` is not a parameter. This is behaviour-identical —
`over_budget` is computed once before either pass and never mutated — and it is the reason
`_evict_for_budget` has three parameters rather than four. Say so in the commit body.

- [ ] **Step 8: Rewrite `_evict_to_match_plan` as the four calls**

The docstring stays **exactly as it is** — every paragraph, including the long argument for the
uncapped pass. Append one paragraph to its end:

```
FOUR PHASES, FOUR METHODS (C-48a). This function is the ORDER; each
phase's own reasoning lives on its own method's docstring. Nothing
about the sequence is optional: phase 1 answering `None` is what stops
phase 2 probing an engine for nothing, phase 2's `over_budget` is what
gates pass 2 and nothing else, and pass 1 must run before pass 2 so an
exclusive endpoint is emptied uncapped rather than nibbled at under
the cap.
```

The body:

```python
        budget_bytes = JobSettings.get_solo().memory_budget_bytes
        if budget_bytes is None:
            return

        targets = self._eviction_targets(claimed)
        if targets is None:
            return
        needed_keys, endpoints, exclusive_endpoints = targets

        installed_by_endpoint, over_budget = self._residency_snapshot(
            endpoints, claimed, budget_bytes)

        self._evict_exclusive_endpoints(exclusive_endpoints, installed_by_endpoint, needed_keys)

        if over_budget:
            self._evict_for_budget(installed_by_endpoint, exclusive_endpoints, needed_keys)
```

- [ ] **Step 9: Run the fourteen tests, then the exit gate**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/queue/tests/test_worker.py::TestEviction -v
```
Expected: all fourteen pass, **unchanged** — no test body may be edited in this step. If one fails,
the split changed behaviour; fix the split, never the test.

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/queue/tests models/registry/tests foundation/ops/tests
.venv/bin/ruff check models/queue/worker.py
```

`ruff` is in the gate because the finding was a `ruff C901` score; the report must name the new
score for `_evict_to_match_plan` and confirm no new violation appeared on the four helpers.

- [ ] **Step 10: Update `models/queue/README.md` if it describes the internals**

```bash
git -C <WORKTREE_ROOT> grep -n -i 'evict' -- models/queue/README.md
```

If the README names the phases or the function's internal structure, update it to name the four
methods. If it only describes the *behaviour* (admission plans, eviction matches the plan), it is
still accurate and needs no edit — say which, in the task report.

- [ ] **Step 11: Commit**

```bash
git -C <WORKTREE_ROOT> add -A
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
refactor(queue): _evict_to_match_plan becomes four named phases

C-48a. Complexity 33, 32 branches, 81 statements -- the highest in the
tree, and four phases the function's own comments already named. They
are four methods now: `_eviction_targets` (what the machine should hold,
or None when nothing is RUNNING), `_residency_snapshot` (what it
actually holds, and the budget verdict), `_evict_exclusive_endpoints`
(pass 1, uncapped) and `_evict_for_budget` (pass 2, capped).
`_evict_to_match_plan` is the ORDER, and its docstring keeps every word
of the argument for that order.

No behaviour change. Fourteen tests in TestEviction pass unchanged --
the ten that were there plus four written first, pinning the phase
boundaries and the degradations nothing asserted (no RUNNING job, an
engine with no `list_installed`, a `list_installed` that raises, a
resident model with no reported size).

The one structural difference: pass 2's per-endpoint `if not
over_budget: continue` is gone because the caller guards the whole call.
`over_budget` is computed once before either pass and never mutated, so
this is the same condition asked once instead of once per endpoint.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

### Task 45: `connection_add` becomes a validation step and a write step (C-48b)

**Files:**
- Test: `models/registry/tests/test_views_reencode_and_sections.py` — characterization tests added
  to the existing `TestConnectionAdd` class (which lives there since commit `04d13c1`; the old
  `models/registry/tests/test_views.py` no longer exists), written and committed **first**
- Test (read, and re-run, but not edited): `models/registry/tests/test_views_connection_edit.py`
  (`TestConnectionEdit`, `TestManualFormPrefill`) and
  `models/registry/tests/test_views_engine_and_remove.py` (`TestCapabilityCheckboxGroups`,
  `TestEngineProfileAutofill`) — the update-mode and enrichment coverage the split must not disturb
- Modify: `models/registry/views.py` — `connection_add` (line ~1321; re-derive) plus one dataclass
  and two new private functions beside it
- Modify: `models/registry/README.md` — if it describes `connection_add`'s structure

**Column:** everything stays inside `models.registry`. **One new import, `from dataclasses import
dataclass` (stdlib) — and no more.** In particular `HttpResponse` is NOT imported by this module
(only `HttpResponseBadRequest`), which is why Step 8's seam asks `isinstance(form, _ConnectionForm)`
and why the union return annotation is safe: `from __future__ import annotations` is present at
line 124, so it is a string.

**Interfaces:**
- Produces:
  - `models.registry.views._ConnectionForm` — a frozen dataclass carrying the validated fields:
    `instance: ModelConnection | None`, `name: str`, `engine: str`, `endpoint: str`,
    `model_id: str`, `capabilities: list[str]`, `embed_dim: int | None`,
    `context_window: int | None`, `descriptor: str`, `rank: int | None`,
    `footprint_override_bytes: int | None`, `family_declared: bool`, `family: str`,
    `variant: str`, `text_encoder: str`, `vae: str`, `confirm: bool`.
  - `models.registry.views._validated_connection_form(request) -> _ConnectionForm | HttpResponse`
    — the parse-enrich-validate step. Returns the console redirect (with the operator message
    already queued on `messages`) for every refusal.
  - `models.registry.views._write_connection(request, form: _ConnectionForm) -> HttpResponse`
    — the create-or-update step, including the embeddings confirm gate and the
    first-materialization stamp.

**The finding.** `ruff` scores `connection_add` at complexity 32, 32 branches, 113 statements and
18 return points across ~370 lines. The auditor filed it Risk **H** and explicitly did *not*
recommend an immediate split, because every validation block carries its own load-bearing rationale
comment and the function is exercised by tests that reason about exact field-by-field behaviour.
The owner overrode that: split it, characterization tests first.

**Ruling (binding).** *"Split only functions with clear phase boundaries: … `models/registry/views.py`
`connection_add` (validation vs side effects) … Cost if wrong: a behaviour change hidden in a
split; characterization tests first."*

**The one rule that governs every line of this task.** *Verbatim means verbatim, typographic quotes
included* (Global Constraint 17). This function's reachable operator copy uses U+201C/U+201D curly
quotes in **five** places — the fifth is easy to miss because it lives in the helper the
unregistered-engine refusal calls, not in `connection_add` itself:

```
_UNKNOWN_ENGINE_MESSAGE = ("Unknown model server “{engine}” — the supported model-server "
                           "APIs are listed on the console.")   # via _refuse_unregistered_engine
_NAME_TAKEN_MESSAGE = "A connection named “{name}” already exists."
messages.info(request, f"Registered connection “{name}”.")
messages.info(request, f"Updated connection “{name}”.")
f"Editing “{instance.name}”: {_severity_copy(dim_changed)} "
```

Note `_UNKNOWN_ENGINE_MESSAGE` also carries an em dash (U+2014). A sixth site sits **inside** the
block being moved — the confirm gate's `"Check “confirm rebind” and submit again to proceed."`

**Which of these actually move, and which merely must not be disturbed:**

- `_UNKNOWN_ENGINE_MESSAGE` and `_NAME_TAKEN_MESSAGE` are **module-level constants** and do **not**
  move at all — `_NAME_TAKEN_MESSAGE` has a second consumer in `machine_model_add`, so editing it
  would change two endpoints' copy at once. Only the `.format(...)` call sites move.
- The three `messages.info`/`messages.warning` f-strings and the confirm-gate sentence **do** move,
  into `_write_connection`.

Everything that moves goes **by cut and paste**, never by retyping. The existing tests assert on
substrings that stop before the quotes and would not catch a silent swap to ASCII.

**The measured baseline, taken on the worktree at `04d13c1`:**

```bash
git -C <WORKTREE_ROOT> grep -c '“' -- models/registry/views.py   # 30
git -C <WORKTREE_ROOT> grep -c '”' -- models/registry/views.py   # 30
```

Both counts are **30** before this task and must be **30** after it. Re-take them at Step 5 (in
case an earlier task in this addendum touched the file) and again at Step 10, and record all four
numbers in the task report.

**The phase boundary, measured.** Re-derive with `git grep -n 'def connection_add' -A 400 --
models/registry/views.py`. Reading top to bottom, the function is:

1. **Parse** — sixteen `request.POST.get`/`getlist` reads into locals, plus the `connection_id`
   lookup that resolves `instance` (and its one refusal: *"The connection being edited no longer
   exists."*).
2. **Enrich** — the engine fallback chain (`engine_raw` → `instance.engine` → `"ollama"`) and the
   catalog fill of a blank `capability`/`embed_dim`.
3. **Validate** — nine refusals, each `messages.error(...)` + `return _redirect_console(request)`:
   required fields; the unregistered-engine boundary (gated on `engine_changed`); capability;
   `embed_dim` (two); `context_window` (two); `descriptor` length; `rank` (two);
   `footprint_gb` (three, including the `math.isfinite` guard); name uniqueness.
4. **Write** — the `create` branch (six lines and a message), or the update branch: the
   fingerprint comparison, the embeddings confirm gate (an eleventh refusal, but one that depends on
   `instance.role_bindings`), the pre-edit fingerprint capture, eleven attribute assignments, the
   `family_declared` guard, `save()`, `_stamp_first_materialization`, and the message.

The cut is between 3 and 4. **The confirm gate stays in the write step** — it is a precondition of
*this particular side effect*, it needs the pre-edit `instance` state, and it is meaningless in
create mode. Say so in `_write_connection`'s docstring.

- [ ] **Step 1: Inventory the existing coverage**

```bash
git -C <WORKTREE_ROOT> grep -n 'def test_' \
  -- models/registry/tests/test_views_reencode_and_sections.py
git -C <WORKTREE_ROOT> grep -n 'def test_' \
  -- models/registry/tests/test_views_connection_edit.py
```

**The refusal count, settled so nobody has to re-derive it:** **nine** in the validation phase
(required fields; unregistered engine; capability; `embed_dim`; `context_window`; `descriptor`
length; `rank`; `footprint_gb`; name uniqueness), **one** in the parse phase (a `connection_id`
naming no row), and **one** in the write phase (the embeddings confirm gate) — eleven in total,
of which the split moves nine to `_validated_connection_form`, keeps one ahead of them, and leaves
one with the write.

Paste both lists into the task report and map each of the nine validation refusals to the test that
already pins it. **Any refusal with no test is a characterization gap this task must fill before
splitting.** The classes to check for coverage, all by name: `TestConnectionAdd`,
`TestConnectionEdit`, `TestCapabilityCheckboxGroups`, `TestEngineProfileAutofill`,
`TestManualFormPrefill`, `TestStampFirstMaterialization`.

**The class's own idiom, read from the file — use it, do not invent a second one.**
`TestConnectionAdd` is `@pytest.mark.django_db`, carries

```python
    def _post(self, client, **data):
        return client.post(reverse("inference-connection-add"), data=data)
```

and asserts a refusal in exactly this shape (from
`test_non_positive_context_window_is_rejected_cleanly`):

```python
        assert response.status_code == 302
        assert not ModelConnection.objects.filter(name="bad conn").exists()
        followed = client.get(response.url)
        assert "Context window must be a positive number of tokens." in followed.content.decode()
```

`ENDPOINT` (`"http://localhost:11434"`) is imported from `models.registry.tests._helpers`, as is
the `client` fixture. The new tests below use `self._post`, `ENDPOINT`, and the
`client.get(response.url)` follow — no new helper.

- [ ] **Step 2: Write the characterization test that covers every refusal in one table**

Add to `TestConnectionAdd` — one parametrized test naming all nine refusals and their exact
operator sentences. Its value is not that each case is new (several are covered); it is that the
**whole refusal surface is asserted in one place**, so the split cannot drop one silently.

```python
    @pytest.mark.parametrize(("post", "fragment"), [
        ({"name": "", "endpoint": "http://x", "model_id": "m"},
         "Name, endpoint, and model are required to register a connection."),
        ({"name": "n", "endpoint": "", "model_id": "m"},
         "Name, endpoint, and model are required to register a connection."),
        ({"name": "n", "endpoint": "http://x", "model_id": ""},
         "Name, endpoint, and model are required to register a connection."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "engine": "nosuchengine"},
         "engine"),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": []},
         "Capability must be one of:"),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "embed_dim": "wide"},
         "Embedding dimension must be a whole number."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "embed_dim": "0"},
         "Embedding dimension must be a positive number."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "context_window": "big"},
         "Context window must be a whole number of tokens."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "context_window": "-1"},
         "Context window must be a positive number of tokens."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "descriptor": "x" * 81},
         "Descriptor must be 80 characters or fewer."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "rank": "first"},
         "Rank must be a positive number."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "rank": "0"},
         "Rank must be a positive number."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "footprint_gb": "heavy"},
         "Memory footprint override must be a number of GB."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "footprint_gb": "inf"},
         "Memory footprint override must be a number of GB."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "footprint_gb": "nan"},
         "Memory footprint override must be a number of GB."),
        ({"name": "n", "endpoint": "http://x", "model_id": "m", "capability": "chat",
          "footprint_gb": "0"},
         "Memory footprint override must be greater than zero."),
    ])
    def test_every_form_refusal_in_one_table(self, client, post, fragment):
        """C-48b, written BEFORE the split. Sixteen refusal cases across
        nine fields, each with the operator sentence it produces and each
        asserting that NOTHING was written -- the whole refusal surface in
        one place, so a validation/write split cannot silently drop one.

        `inf`/`nan` are here on purpose: `float()` parses both without
        raising, `round(inf * 1024**3)` is an uncaught OverflowError, and
        `nan <= 0` is False -- the T4 review MAJOR this field's own
        `math.isfinite` guard exists for."""
        before = ModelConnection.objects.count()
        response = self._post(client, **post)
        assert response.status_code == 302
        assert ModelConnection.objects.count() == before
        followed = client.get(response.url)
        assert fragment in followed.content.decode()
```

Two notes on the table above. Its `endpoint` values are written as `"http://x"` for brevity —
**use the imported `ENDPOINT` constant instead**, matching every other test in the class. And the
`"engine"` row's fragment is the only loose one: the unregistered-engine sentence comes from
`_refuse_unregistered_engine` via `_UNKNOWN_ENGINE_MESSAGE`, which reads
`Unknown model server “{engine}” — the supported model-server APIs are listed on the console.`
Assert the real sentence, **copied from the file** (it carries typographic quotes and an em dash),
not the placeholder `"engine"`.

Add three more, covering what the table cannot:

```python
    def test_a_connection_id_that_names_no_row_is_refused_before_any_validation(self, client):
        """The FIRST refusal, and the only one in the parse phase: a
        tampered or stale `connection_id` answers "The connection being
        edited no longer exists." without validating a single field --
        so it must stay ahead of every other check after the split."""

    def test_a_name_collision_is_case_insensitive_and_excludes_the_row_being_edited(self, client):
        """The LAST refusal in the validation phase, and the only one
        that reads the database. Two halves: a different row with the
        same name in any casing is refused with the typographic-quoted
        `_NAME_TAKEN_MESSAGE`; re-saving (or re-casing) a connection's
        OWN name is not a collision."""

    def test_the_operator_sentences_use_typographic_quotes(self, client):
        """Global Constraint 17, as an assertion. Registered/Updated/
        name-taken/confirm-gate copy all use U+201C/U+201D. The other
        tests assert on substrings that stop before the quote, so
        nothing else would catch a silent swap to ASCII during a
        refactor of this function."""
        # register one, assert '“' and '”' both appear in the flash;
        # update it, same; collide a name, same.
```

- [ ] **Step 3: Run them against the UNSPLIT function**

```bash
export DATABASE_URL='<TEST_DATABASE_URL>'
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q \
  models/registry/tests/test_views_reencode_and_sections.py::TestConnectionAdd -v
```

Expected: all pass. Characterization, not regression — a failure means the plan's reading of a
refusal is wrong. Stop and report; do not change `views.py` to match.

**Coordinate before committing:** the registry suite's split has landed (`04d13c1`), so there is no
peer holding these files — but confirm rather than assume with
`git -C <WORKTREE_ROOT> status --short -- models/registry/tests/`.
If anything there is dirty, report to the orchestrator and wait rather than racing.

- [ ] **Step 4: Commit the characterization tests alone**

```bash
git -C <WORKTREE_ROOT> add models/registry/tests/test_views_reencode_and_sections.py
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
test(registry): connection_add's whole refusal surface, in one table

C-48b, first half. Nine fields, sixteen refusal cases, each with the
operator sentence it produces and each asserting nothing was written --
plus the parse-phase refusal (a connection_id naming no row), the
case-insensitive name collision that excludes the row being edited, and
a pin that the Registered/Updated/name-taken copy really does use
typographic quotes.

Green against the unsplit 370-line function, so the split has something
to be measured against.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

- [ ] **Step 5: Add the dataclass**

Immediately above `_refuse_unregistered_engine` in `models/registry/views.py` (re-derive the
location). **`dataclasses` is not currently imported in this module** — verified with
`git grep -n '^from dataclasses\|^import dataclasses' -- models/registry/views.py`, which returns
nothing — so add `from dataclasses import dataclass` to the stdlib group of the import block
(beside `import math`, which is there at line ~128), keeping the block's existing ordering:

```python
@dataclass(frozen=True)
class _ConnectionForm:
    """`connection_add`'s POST, parsed, catalog-enriched and validated --
    everything the write step needs and nothing about HTTP.

    `instance` is the row being edited, or `None` in create mode; every
    other field is already in the type the model column wants, so
    `_write_connection` assigns rather than converts. `confirm` and
    `family_declared` are the two bits that are about the SUBMISSION
    rather than about the connection: whether the operator ticked
    "confirm rebind", and whether the family fieldset was posted at all
    (a submission that never mentions it leaves a stored config alone;
    one that posts it blank clears it -- final-review B1)."""

    instance: ModelConnection | None
    name: str
    engine: str
    endpoint: str
    model_id: str
    capabilities: list[str]
    embed_dim: int | None
    context_window: int | None
    descriptor: str
    rank: int | None
    footprint_override_bytes: int | None
    family_declared: bool
    family: str
    variant: str
    text_encoder: str
    vae: str
    confirm: bool
```

- [ ] **Step 6: Extract the validation step**

```python
def _validated_connection_form(request) -> _ConnectionForm | HttpResponse:
    """`connection_add`'s parse-enrich-validate half (that view's own
    docstring carries the whole argument for every rule below).

    Returns a `_ConnectionForm` when every field is good, or the console
    redirect -- with the operator's message already queued -- for any of
    the nine refusals. WRITES NOTHING. The one database read here is the
    name-uniqueness check, which is a validation whose answer happens to
    live in a table.

    NOT here: the embeddings-rebind confirm gate. That refusal is a
    precondition of a specific SIDE EFFECT -- it depends on the row's
    current role bindings and its pre-edit fingerprint, and it does not
    exist in create mode at all -- so it lives with the write it guards,
    in `_write_connection`."""
```

Body: everything from `name = request.POST.get("name", "").strip()` down to and including the
`name_taken` block, **verbatim, every comment included**, ending with:

```python
    return _ConnectionForm(
        instance=instance,
        name=name,
        engine=engine,
        endpoint=endpoint,
        model_id=model_id,
        capabilities=capability_list,
        embed_dim=embed_dim,
        context_window=context_window,
        descriptor=descriptor,
        rank=rank,
        footprint_override_bytes=footprint_override_bytes,
        family_declared=family_declared,
        family=family,
        variant=variant,
        text_encoder=text_encoder,
        vae=vae,
        confirm=confirm,
    )
```

- [ ] **Step 7: Extract the write step**

```python
def _write_connection(request, form: _ConnectionForm) -> HttpResponse:
    """`connection_add`'s write half: create the row, or update it and
    deal with what that invalidates.

    THE CONFIRM GATE LIVES HERE, not in validation, and it is the reason
    this function can still refuse. A fingerprint-altering edit
    (engine/model/dim/endpoint, compared through `norm_tag`/
    `norm_endpoint`) to a connection bound to an embeddings role
    invalidates that role's materialized data exactly like rebinding the
    role would -- so it goes through the same gate, with the same
    severity copy, and the same first-materialization stamp. That is a
    fact about the row being written, not about the form.

    Every operator sentence here uses typographic quotation marks
    (U+201C/U+201D). They are copy, not syntax; the tests assert on
    substrings that stop before them."""
```

Body: the current `if instance is None: ModelConnection.objects.create(...)` branch and the whole
update branch, verbatim, with `instance` read from `form.instance` and every other local read from
the corresponding `form.<field>`. Concretely, `name` → `form.name`, `engine` → `form.engine`,
`capability_list` → `form.capabilities`, `confirm` → `form.confirm`,
`family_declared` → `form.family_declared`, and `_family_config(family, variant, text_encoder,
vae)` → `_family_config(form.family, form.variant, form.text_encoder, form.vae)`. Add
`instance = form.instance` as the first line so the update branch's many `instance.` lines read
unchanged.

- [ ] **Step 8: Rewrite `connection_add`**

Its ~80-line docstring stays **exactly as it is** — it documents the endpoint, and the endpoint is
unchanged. Append one paragraph:

```
TWO HALVES SINCE C-48b: `_validated_connection_form` parses,
catalog-enriches and validates (nine refusals, no write), and
`_write_connection` creates or updates (and owns the embeddings
confirm gate, which is a precondition of the write rather than of the
form). This function is the seam. Every rule either half enforces is
documented above; neither half re-argues it.
```

Body:

```python
    form = _validated_connection_form(request)
    if not isinstance(form, _ConnectionForm):
        return form
    return _write_connection(request, form)
```

**Asked of `_ConnectionForm`, not of `HttpResponse`, deliberately.** `models/registry/views.py`
imports only `HttpResponseBadRequest` from `django.http` — verified with
`git grep -n 'from django.http' -- models/registry/views.py` — so an `isinstance(form,
HttpResponse)` check would need a new import for no gain. `_redirect_console` returns whatever
`redirect()` builds (an `HttpResponseRedirect`), and testing the *success* type rather than
enumerating the failure types is both import-free and correct for any future refusal shape.

Annotate the union on the helper's return type (`_ConnectionForm | HttpResponse`) anyway, for the
reader — that is a string annotation under this module's `from __future__ import annotations` and
costs no import. Confirm that future-import is present with
`git grep -n 'from __future__' -- models/registry/views.py`; if it is **not**, write the annotation
as a quoted string rather than adding an import.

- [ ] **Step 9: Run the whole registry view suite, then the exit gate**

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests -v
```

The whole registry tests directory, not one file: the classes that matter are spread across the
six `test_views_*.py` files the `04d13c1` split produced. Expected: every test passes **with no
test body edited in this step**. The classes that matter most, by name and file —
`TestConnectionAdd` and `TestStampFirstMaterialization` (`test_views_reencode_and_sections.py`),
`TestConnectionEdit` and `TestManualFormPrefill` (`test_views_connection_edit.py`),
`TestCapabilityCheckboxGroups` and `TestEngineProfileAutofill` (`test_views_engine_and_remove.py`),
and `TestRoleAssignConnectionPick` (`test_views_console_and_roles.py`).

```bash
FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/registry/tests models/queue/tests foundation/ops/tests
.venv/bin/ruff check models/registry/views.py
```

Report the new `ruff` complexity score for `connection_add` and confirm neither new function
crosses the threshold on its own. If `_validated_connection_form` is *still* over the C901
threshold, that is an acceptable outcome — it is nine independent field validations and the ruling
asked for a validation/write cut, not for a complexity number. Say so plainly rather than inventing
a second split.

- [ ] **Step 10: Prove the quotes survived**

```bash
git -C <WORKTREE_ROOT> grep -c '“' -- models/registry/views.py
git -C <WORKTREE_ROOT> grep -c '”' -- models/registry/views.py
```

Both counts must equal the counts taken before Step 5. Paste before-and-after into the task report.

- [ ] **Step 11: Commit**

```bash
git -C <WORKTREE_ROOT> add -A
git -C <WORKTREE_ROOT> commit -m "$(cat <<'EOF'
refactor(registry): connection_add splits into validation and write

C-48b. 370 lines, complexity 32, 113 statements, 18 returns, doing "parse
and validate the form" and "perform the write and its side effects" as
one function. `_validated_connection_form` now owns the first --
sixteen POST reads, the engine fallback chain, the catalog enrichment
and nine refusals, writing nothing -- and returns a frozen
`_ConnectionForm`. `_write_connection` owns the second.

The embeddings-rebind confirm gate stays with the WRITE, deliberately:
it depends on the row's current role bindings and its pre-edit
fingerprint and does not exist in create mode, so it is a precondition
of that side effect rather than of the form.

No behaviour change, and every rationale comment moved verbatim -- these
validations each carry their own reasoning (the engine_changed
exemption, the family_declared presence-vs-blank distinction, the
isfinite guard on footprint_gb) and that reasoning is the point. Every
operator sentence, typographic quotes included, is unchanged: the
before/after count of U+201C/U+201D in this file is identical, and a new
test asserts the quotes directly because the other tests' substrings
stop before them.

Sixteen refusal cases in one table, plus three more, were written and
committed against the unsplit function first.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_<id>
EOF
)"
```

---

## Not planned

| Item | Why not, and what would change that |
|---|---|
| **PR #84 (`chat-attachments`) — rebase or close** | An **owner decision**, explicitly not taken in the WP10 rulings ("PR #84 rebase-or-close is NOT decided here — owner asked explicitly"). Two findings hang on it and stay parked with it: **C-37** (`.pulse-dot` — the CSS exists on `#84`; the JS and the README claim are both in files held for PR #88) and **C-38** (the `.delete-disclosure` rules in `agents/chat/templates/chat/base.html` are dead on `main` but live on `#84`, and the false test comment is in the held `test_thread.py`). Parent-plan Global Constraint 11 stands: **that `base.html` block stays exactly as it is** until the decision lands. *Trigger to plan: the owner rules on #84.* |
| **`agents/chat/README.md`'s two stale references** | The file is **held for PR #88**. It names `AskJobStatusView` once (which Task 42 renames) and describes the chat page's own poll loop (which the C-16 follow-up replaces). Task 42 is forbidden to repair the first, so it deliberately leaves a stale name behind rather than touching a held file — recorded here so it is not mistaken for an oversight. Both are fixed by the same follow-up, since the same merge releases the file. *Trigger: #88 merges.* |
| **Repointing `agents/chat/templates/chat/conversation.html` at the shared poller** | Held for **PR #88** along with `_turn_card.html`, `test_thread.py` and `agents/chat/README.md` (parent-plan Global Constraint 10). Task 43 builds the helper and the `POLL_DEFAULTS` seam that the chat page's existing `poll_interval_ms`/`max_transport_retries`/`max_poll_duration_ms` template variables plug into, and records the page as the one dated exemption in `foundation/ops/tests/test_shared_poller.py`. The full follow-up — files, steps, the two `<script>`-count updates, and the exemption deletion — is written out at the foot of Task 43. *Trigger to execute: #88 merges to `main`.* |
| **A test-suite runtime profile** | The orchestrator runs `pytest --durations` as part of the final four-way flag×order matrix at the end of the parent plan. Doing it inside a task would measure a moving tree and hold a shared database slot for a number that is only meaningful once. *Trigger: the final matrix run; the numbers land in the SDD ledger, not in a task.* |
| **The remaining thirteen over-threshold functions (C-48)** | The ruling names exactly two: `_evict_to_match_plan` and `connection_add`. `generate`/`submit_job` are ordered pipelines with load-bearing `except` ordering and the vision auditor recommended explicitly against splitting them; `stage_turn_attachments`, `start_turn` and `chat/service.py` are already digest rows S13/S14/S20 with their own rulings; the rest were not selected. *Trigger: a new owner ruling naming a specific function.* |
| **C-13, C-53, C-11, C-12** | Excluded by the parent plan's Global Constraint 12 — "no action" (C-13, C-53) and two trivial N+1s in files no task touches (C-11, C-12). Unchanged by WP10. |
| **Repo housekeeping (merged remote branches, the two orphan `agent-*` worktrees)** | Ruled on, but it is git-administration on the owner's box, not a code change with a test. It belongs to the orchestrator's own session, not to a task in a plan whose every unit ends in a commit and a test run. |

---

## Plan review

**Round 1 — author's own pass, applied.** Written against the `plan-hygiene-review` rubric — scope
creep, duplication, over-engineering, mechanical error — with every `path:line` and symbol name
checked against the real tree. Findings AMEND 1–6 below.

**Round 1b — measurement pass against the tree at `04d13c1`, applied.** Re-derived every claim the
plan makes about existing test helpers and imports, after parent-plan Task 38 landed. Findings
AMEND 7–12 below. The material one is AMEND 7: the file Task 45 was written against no longer
exists.

**Round 2 — adversarial reviewer subagents (`plan-hygiene-review` step 1): VERDICT AMEND, ALL
FINDINGS APPLIED.** Two independent read-only reviewers were dispatched on the most capable model
against this plan, the WP10 rulings, the audit and the real code, each given the same eight
load-bearing claims to verify. Both returned **AMEND**; between them, seven blocking and seventeen
minor findings. They agreed on the substance and overlapped on four of the blocking ones, which is
itself evidence the findings are real rather than reviewer taste.

**What they confirmed** (so the plan's own claims can be trusted): the `_shell.html`-has-no-script
finding and the whole include argument (a); every `<script>`-count assertion and the claim that
only one changes (b); `_LABEL_FORBIDDEN_MESSAGE`'s orphan status and the "no import may be removed"
rule (c); the DRF settings, the route-matrix driver and the CSRF claim (d); **Task 44 end to end**
— phase boundaries, `resident_sizes` locality, the `over_budget` hoist, all ten test names — which
one reviewer called the strongest task in the addendum (e); Task 45's phase cut and the complete
17-field `_ConnectionForm` (f); Task 40 in full (g); and the no-line-numbers rule (h).

**What they broke, and what changed as a result** — findings AMEND 14–24 below. The three that
would have produced genuinely broken work: Task 43's gate could never have gone green (its own
helper's comment falsifies its `<script>` count, and it called two functions that do not exist);
Task 43's helper carried a real regression (the transport-failure counter reset in the wrong
place); and Task 41's grep list was missing five live references, so its own reconciliation step
would have halted the task on step one.

**Round 3 was not run.** The `plan-hygiene-review` loop allows up to three rounds; every finding
from round 2 was applied rather than argued with, and no finding required a judgement call that a
further adversarial pass would settle. The one decision still standing above the reviewers'
authority is AMEND 1 — the include-vs-shell-script departure from the C-16 ruling's literal
wording — which **the orchestrator must confirm before Task 43 is dispatched**; both reviewers
independently verified its premise and called the argument sound, but neither can approve a
departure from a binding ruling.

### Round 2 findings (adversarial reviewers) — all applied

### Round 1 findings

**AMEND 1 (Task 43, blocking, applied) — the ruling's premise is wrong and the naive reading breaks
a held file.** The C-16 ruling says the helper goes in *"`_shell.html`'s single `<script>`"*.
`foundation/templates/_shell.html` **has no `<script>` tag** — verified with
`git grep -n '<script' -- foundation/templates/_shell.html`, which returns nothing; the file has one
`<style>` block and an empty `{% block scripts %}{% endblock %}` at the bottom. Worse, the naive
fix — adding a `<script>` to the shell outside that block — adds one tag to **every rendered page**,
and `agents/chat/tests/test_thread.py` asserts `body.count("<script") == 3` and `== 4` on the
conversation page. That file is **held for PR #88**, so the naive fix is forbidden by Global
Constraint 10. Placing it *inside* `{% block scripts %}` spares the held file (conversation.html
overrides the block without `{{ block.super }}`) but breaks seven other `<script>`-count assertions
across `test_workstream_page.py` and `test_all_conversations.py` and ships a poller to five pages
that never poll. **Applied:** Task 43 creates `foundation/templates/_poller.html`, a
`foundation`-owned partial whose whole body is one inline `<script>`, included by the two pages that
opt in — the same shape as `agents/chat/templates/chat/_menu_exclusive.html` and as
`foundation/templates/_messages.html` (parent-plan Task 31). Blast radius: one `<script>` count
assertion, in `tools/vision/tests/test_views_create.py`. This satisfies the ruling's substance (one
shared inline helper, no static asset, no build step) and is recorded in Task 43's own "Why an
include" note. **The orchestrator must confirm this AMEND before Task 43 is dispatched.**

**AMEND 2 (Task 42, applied) — the ruling's "identical contracts" needs four exceptions, not zero.**
"Rewrite on `JsonResponse`/`json.loads` with identical request/response contracts" is not
achievable as stated: DRF's `request.data` accepts form-encoded bodies (and the route matrix's own
`rag-ask` driver posts exactly that); `APIView.as_view()` is `csrf_exempt` and this project
configures no DRF authentication classes, so `/rag/ask/` currently accepts a POST with **no CSRF
token**; DRF's parse-error body is `{"detail": …}` where the platform's is `{"error": …}`; and
DRF's datetime encoder keeps six microsecond digits where `DjangoJSONEncoder` keeps three.
**Applied:** the four are tabulated in Task 42 with a ruling on each, pinned by characterization
tests before the rewrite, and named in the commit body. The CSRF one is a gap being closed, not a
client being broken — and Task 42 adds `rag-ask` to the route matrix's anonymous-POST CSRF pin,
which had previously argued the endpoint out on exactly those grounds.

**AMEND 3 (Task 41, applied) — "the route-matrix tests must be updated, not weakened" needed a
mechanism, not an instruction.** The first draft listed the deletions without saying what keeps the
result honest. **Applied:** Task 41 now names the two exhaustiveness tests that do the work —
`identity/tests/test_routes.py::TestCoverage.test_every_route_this_platform_owns_is_classified`
(resolver → rules) and `test_every_rule_names_a_route_that_exists` (rules → resolver), plus
`test_route_matrix.py::TestTheTableIsComplete.test_every_route_has_a_driver` — forbids editing any
of the three, adds a positive absence pin (`NoReverseMatch`, not in `ROUTE_RULES`, not on the views
module), and preserves `TestTheCaseTheSplitExistsFor`'s coverage by repointing its POST at the bulk
route rather than deleting the case.

**AMEND 4 (Task 41, applied) — an orphan the grep list had missed.** `_LABEL_FORBIDDEN_MESSAGE` has
exactly one reader, the view being deleted; `document_labels_bulk` reports refusals as a skipped
count, not a 403 sentence. **Applied:** it is row 2 of the grep table and is asserted absent by the
new pin. The converse was also checked and is now stated explicitly in Step 3:
`listable_documents`, `may_label_document`, `document_label_ids`, `set_document_labels` and
`labelling_entitlements` all keep other callers in the same module, so **no import may be removed**
— a plausible and wrong cleanup a fresh implementer would otherwise make.

**AMEND 5 (Task 44, applied) — the split had five phases, and the ruling says four.** The first
draft made the budget arithmetic its own helper. **Applied:** the arithmetic folds into
`_residency_snapshot`, which returns `(installed_by_endpoint, over_budget)` — the audit's own
description of phase 2 ("probe each endpoint's actual residency *and compute* …"). The one
structural difference this creates (pass 2's per-endpoint `if not over_budget: continue` becomes a
single guard at the call site) is called out in the task and in the commit body, with the argument
for why it is behaviour-identical.

**AMEND 6 (Task 40, applied) — "scope the marker to the status word" has no status word to scope
to.** `workstream_dormant.html` never renders the word "Dormant"; it has a lede reading *"This
workstream is not readable right now."* Deleting the class from the `<article>` alone would satisfy
the letter of the ruling and lose the marker entirely. **Applied:** Step 3 introduces
`<span class="dormant">Dormant</span>` in the lede, matching `_sidebar.html`'s and
`workstream_settings.html`'s shape, and the test asserts both halves — the article does *not* carry
the class, and the span does. Also confirmed: `agents/chat/README.md` (held for PR #88) needs **no**
edit, because its §"The dormant marker reads as a warning consistently" already describes
`.dormant` as an inline marker — this task makes the code match the documentation.

### Round 1b findings — measured against the tree at `04d13c1`

**AMEND 7 (Task 45, BLOCKING, applied) — the file the task was written against no longer exists.**
Parent-plan Task 38 landed as commit `04d13c1`, *"test(registry): the 7,606-line view suite splits
into six by feature area"*, and `models/registry/tests/test_views.py` is **gone** —
`ls models/registry/tests/` shows six `test_views_*.py` files in its place. The plan's addendum
constraint A1 described that split as "in flight" and Task 45's Files list named the dead path.
**Applied:** A1 now carries a verified class→file table and Task 45's Files list names
`test_views_reencode_and_sections.py` (which holds `TestConnectionAdd` and
`TestStampFirstMaterialization`), with `test_views_connection_edit.py` and
`test_views_engine_and_remove.py` listed as read-and-re-run. `tools/rag/tests/test_views.py` was
still standing when this finding was written; **Task 39 split it too, during hand-over**, so A1
now carries that mapping as well and Tasks 41/42 name the successors directly.

**AMEND 8 (Task 45, applied) — the test code contained two placeholders, which the writing-plans
skill forbids outright.** The refusal-table test called `_post_connection(client, post)` and
`_flash_text(response, client)`, neither of which exists. **Applied:** replaced with the class's
real idiom, read from `test_views_reencode_and_sections.py`: `self._post(client, **data)`, then
`assert response.status_code == 302`, `assert not ModelConnection.objects.filter(...).exists()`,
`followed = client.get(response.url)`, `assert <sentence> in followed.content.decode()`. The
`ENDPOINT` constant (`"http://localhost:11434"`) comes from `models.registry.tests._helpers`, and
the table's placeholder `"http://x"` endpoints are called out to be replaced with it.

**AMEND 9 (Task 45, applied) — a fifth typographic-quote site, and a measured baseline.** The plan
named four; `_UNKNOWN_ENGINE_MESSAGE` is a fifth (`"Unknown model server “{engine}” — the supported
model-server APIs are listed on the console."`, which also carries an em dash) and is reachable
from `connection_add` through `_refuse_unregistered_engine`. It is also the sentence the refusal
table's loose `"engine"` fragment stands in for. **Applied:** all five are listed, and the
before/after check now names the measured baseline — `grep -c '“'` and `grep -c '”'` on
`models/registry/views.py` are both **30** — instead of asking the implementer to invent one.

**AMEND 10 (Task 45, applied) — the seam needed an import the module does not have.** Step 8 used
`isinstance(form, HttpResponse)`, but `models/registry/views.py` imports only
`HttpResponseBadRequest` from `django.http`. **Applied:** the check is
`if not isinstance(form, _ConnectionForm)` — import-free, and correct for any future refusal shape
rather than only for the ones that exist today. The union return annotation is safe because
`from __future__ import annotations` is present (verified, line 124). Separately, `dataclasses` is
**not** imported in that module either, so Step 5 now says to add `from dataclasses import
dataclass` rather than "if it is not already there".

**AMEND 11 (Task 44, applied) — two of the four new tests had no stub that could express them.**
The plan said to copy the existing fixture usage, but `FakeEngineNoOptionalMethods` — the obvious
candidate — drops `loaded_footprint`/`unload` and **still defines `list_installed`**, so it cannot
exercise either probe-side degradation. **Applied:** Task 44 now specifies two new stubs in full,
`FakeEngineNoListInstalled` and `FakeEngineListInstalledRaises` (the latter subclassing the
former), each carrying `unload_calls` so a test can assert that *no* unload happened, plus the
class's real call idiom (`FakeEngine(...)`, `InstalledModel(...)`, `_set_budget`, `_job`, `_ref`,
`Worker(worker_id=...)` inside `try/finally: worker._executor.shutdown(wait=False)`,
`engine.unload_calls`).

**AMEND 12 (Task 42, applied) — the plan told the implementer to reinvent setup that already
exists.** `tools/rag/tests/_helpers.py` already provides `post_ask(client, payload)` (*"POST a
question payload to `/rag/ask/` as JSON, shared by every `TestAskView*` class"*) and
`model_available()` (the autouse fixture body that patches `tools.rag.views.resolve` and
`tools.rag.messages.get_engine`). **Applied:** Task 42 now names both, shows the three-line autouse
fixture that reuses `model_available`, and flags that the form-encoded characterization test is the
one case `post_ask` cannot express — it must post directly, which is precisely the point of that
test.

**AMEND 13 (Task 43, applied) — the gate could be strict, and the plan hedged.** Measured:
`setInterval` appears in **zero** templates repo-wide, and `setTimeout` in **exactly three** — the
three pollers C-16 is about. The plan's repo-wide test used a compound heuristic
(`"setTimeout" in text and "fetch(" in text`). **Applied:** the condition is now the strict
`"setInterval" in text or "setTimeout" in text`, the docstring records the measurement that
justifies it, and the instruction on finding a fourth timer is to add a **separately-named,
reasoned exemption set** rather than to loosen the boolean — so any future loosening is visible in
the diff instead of buried in a condition.

**AMEND 14 (Task 43, BLOCKING, applied) — the gate called two functions that do not exist.**
`foundation/ops/tests/test_css_ownership.py` has no `_read` and no `_all_templates`; its walker is
`_template_files() -> MappingProxyType[str, Path]`, `@lru_cache`d and keyed by **template-loader
name** (`rag/ask.html`), which its own docstring says is *"NOT the same as the filesystem path"*.
Every constant in the drafted gate was in the wrong key space, so `path != _POLLER` and
`path not in _EXEMPT` would never match and the repo-wide test would be permanently red, listing
`_poller.html` and `conversation.html` as offenders. **Applied:** Step 1 now names the real
precedent — `test_no_template_writes_its_own_flash_loop` — reproduces its inline `rglob` walk,
and states its three load-bearing parts, including that `.claude`/`.venv` must be excluded via
`path.relative_to(REPO_ROOT).parts` and **never** a `"/.claude/" in str(path)` substring, because
this worktree *lives under* `.claude/` and a substring check would exclude everything and pass
vacuously. The plan also now settles the two decisions it had left open: inline the walk (do not
lift a shared helper into `_helpers.py`), and use repo-relative path strings throughout.

**AMEND 15 (Task 43, BLOCKING, applied) — the helper's own comment falsified the helper's own
gate.** `test_the_shared_helper_exists_and_defines_the_function_once` asserts
`text.count("<script>") == 1`, but the `{% comment %}` block Step 3 writes into `_poller.html`
contains the literal `<script>` three times while explaining the include decision. Raw count: 4.
**Applied:** a `_text()` reader strips `{% comment %}` with `_COMMENT_RE` before every check — the
same convention every pin in the sibling module uses, and for the same reason (templates here
routinely discuss in prose the thing the pin forbids). This also makes the `setTimeout` scan
survivable for a page whose comment merely mentions the word. The *rendered* count on the create
page is unaffected, so Step 6's `== 3` stands.

**AMEND 16 (Task 43, BLOCKING, applied) — the helper carried a real regression, and the plan
forbade fixing it.** The drafted note claimed `failures = 0` in the first `.then` matched
`ask.html`'s placement. It does not: `ask.html` resets in the **second** `.then`, after
`response.json()` has resolved. So today a server answering a 500 HTML page rejects during the
parse, never reaches the reset, and the poller correctly gives up after four attempts with *"Lost
contact with the server."* Under the drafted helper the counter would be zeroed before parsing,
`failures` could never exceed `maxRetries`, and the page would poll a broken server for ten
minutes and then report the **wrong** sentence. **Applied:** the reset moved into the outcome
handler with a comment explaining why it cannot move back, and the "do not fix this" note was
replaced with the actual `ask.html` excerpt and the failure mode.

**AMEND 17 (Task 41, BLOCKING, applied) — the grep list was missing five live references, three of
them in a column the task did not declare.** `git grep -n 'document_labels_update'` also hits
`identity/services.py:376`, `models/registry/views.py:2457` and `:2563`; and
`document_labels_bulk`'s own docstring describes the deleted route twice in prose (*"the
single-document route"*, `tools/rag/views.py:683` and `:690`) where **no name-grep finds it**.
The task's own Step 1 says *"the counts must match or the task stops"*, so an implementer would
have halted immediately. **Applied:** grep rows 13–18 added, the count corrected from "thirteen
edits across eleven files" to "eighteen-plus across thirteen", **three** greps specified instead of
one, `models.registry` added to the Column note, and the resulting file overlap with Task 45
declared as an order dependency — which also corrects this section's own earlier claim that no two
tasks touch the same file.

**AMEND 18 (Task 41, BLOCKING, applied) — one deleted test had no counterpart.** The plan asserted
that every `TestTheLabelRoute` method has an ADD/REMOVE twin in `TestBulkLabelling`. Two do;
**`test_removing_the_last_label_makes_the_document_follow_the_library_posture` does not.** It is a
"Done-when 2" acceptance test — post an empty label set, then assert the document follows the
library posture, is readable by a plain member on an open library, 404s under `LIBRARY_LOCKED`, and
needed no re-encode. `document_labels_bulk` cannot express it as written (it refuses an empty
`entitlements` list outright), and no bulk test asserts posture or readability at all. **Applied:**
Step 8 now carries a three-row disposition table and moves that test into `TestBulkLabelling`,
re-spelling "remove the last label" as `action="remove"` naming the label, with every downstream
assertion kept verbatim. Only the other two methods are deleted.

**AMEND 19 (Task 41, BLOCKING, applied) — the mechanical rename would have written two false
sentences.** `tools/rag/access.py:774` and `tools/rag/ingest.py:700` describe the deleted view's
**explicit-by-name 403**, not its predicate. `document_labels_bulk` has no such branch — it
excludes chat-scoped rows by *query* (`services.documents_targeted_for_labelling`) and reports
refusals as a skipped count, as the plan's own Step 9 says. **Applied:** Step 10 now splits the
prose references into Group A (four mechanical renames) and Group B (two **rewrites**), with the
replacement text given verbatim for both.

**AMEND 20 (Task 42, BLOCKING, applied) — the rename left ~55 references stale across 33 files,
one of them in a held file.** `AskView`/`AskJobStatusView` are named across four production
columns, the import-law test, several test modules, and five live READMEs — including
**`agents/chat/README.md`, which is held for PR #88** and cannot be repaired by this task. The
rename was also the plan's own addition; the C-55 ruling asks for a rewrite, not a rename.
**Applied:** a new **Step 7b** carries the measured file list, a decision rule (rename callable and
surface references; leave ADRs; leave the held file), the two `tools/rag/views.py` docstrings Step 5
does not otherwise reach, and an explicit fallback of keeping the existing names if the sweep
exceeds the task's appetite. The held README is recorded in the "Not planned" table, released by
the same merge as the C-16 chat repoint.

**AMEND 21 (Task 43, BLOCKING, applied) — the docs target did not exist.** Step 7 told the
implementer to amend `docs/ARCHITECTURE.md`'s script-posture sentence. That file is 315 lines and
never mentions scripts, JavaScript, static assets or a build pipeline — its only "script" match is
the substring inside *"description"*. The step's own grep returns nothing, leaving the implementer
to invent a section while being told to *"match the surrounding prose voice."* **Applied:**
retargeted to **`foundation/README.md`**, which already carries the shared-template inventory
(`_shell.html`, `_settings.html`) and is the column that will own `_poller.html`; the replacement
bullet is written in that file's existing voice, and `docs/ARCHITECTURE.md` is off the Files list.

**AMEND 22 (Task 42, applied) — a self-contradicting step and a dead import.** Step 5 first added
`require_GET`/`require_POST` and showed `@require_POST def ask`, then sixty lines later said *"do
not apply `require_POST`/`require_GET` — one mechanism, not two"* and left the choice to the
implementer. `require_GET` would have been unused (`ruff F401`). **Applied:** the plan commits to
the in-body method check, both function signatures show it, the `require_GET` import is gone with
an explanation, and the `_json_405` helper is deleted — two lines twice does not earn a helper, and
one here would be the second mechanism the note exists to avoid.

**AMEND 23 (Task 42, applied) — three factual corrections in the differences table.** (i) The table
said "four differences" above five rows and the commit body said "three"; there are now **seven**,
of which two — `OPTIONS` answering DRF's 200 metadata body, and `Accept: text/html` answering 406
by content negotiation — were missing entirely, and are recorded as *"record, do not reproduce"*
with the reason (no caller on this box; reproducing negotiation means reimplementing the piece of
DRF being removed). (ii) `_json_body`'s contract was stated as returning `{}` in Interfaces and
`None` in the code; `None` is correct and load-bearing, because `{}` would fall through to the
*"'question' is required"* sentence instead of the parse-error one. (iii) The D4 evidence grep was
claimed to be "empty" — twice, once inside a **committed test docstring**. It is not: it returns
`jobs/queue.html`'s server-rendered `row.started_at` and a comment in `vision/_job_card.html`. The
conclusion holds (neither reads this endpoint), but the claim is now stated accurately in both
places.

**AMEND 24 (minor, applied across four tasks).** Task 45's refusal count was stated as eight, nine
and "a ninth" in three places — now settled as nine in validation, one in parse, one in write, with
the enumeration written out. Task 45's Column line claimed "no new import beyond `dataclasses`"
while the drafted seam needed `HttpResponse`, which the module does not import — resolved by
AMEND 10's `isinstance(form, _ConnectionForm)`, and the Column line now says so. Task 45's
typographic-quote list wrongly implied `_NAME_TAKEN_MESSAGE` moves; it is a module-level constant
with a second consumer in `machine_model_add` and must **not** be touched — only its call site
moves — and a sixth site (the confirm gate's `"Check “confirm rebind”…"`) is now named. Task 41's
Step 11 credited `foundation/ops/tests/test_docs_sync.py` with catching a stale README; that file
is 38 lines about backup/restore command sequences and cannot see it, so the plan now says plainly
that no gate covers it. Task 41 cited `TestTheLabelsColumnReadsAndTheCardWrites` for an assertion
that lives in `TestTheCategoryBulkControl`, and attributed a test assertion to a template. Task 41's
Step 9 now also names the two places the "Deleting / Re-ingesting / Labelling" verb trio survives
without naming a symbol. Task 42's Step 7.4 now amends the anonymous-POST class's **first**
paragraph too, since `rag-ask` is class `A` like `chat-start` and its arrival contradicts "One POST
per class". Task 43's helper dropped three per-call options (`intervalMs`,
`maxTransportRetries`, `maxDurationMs`) that no caller passes and the chat follow-up will not
either — `POLL_DEFAULTS` is the tuning seam, and a second way to set the same number with no reader
is a knob nothing reads. Task 40's template note and test docstring said "all four paragraphs",
double-counting the lede; both now say "the lede and the three paragraphs below it".

**Checked and found clean, no amendment:**
- Every `path:line` in this document was measured on the worktree at `be7be9b`, and every task
  carries the "re-derive with `git grep -n`" instruction the parent plan's Global Constraint 16
  requires.
- No line number is cited in either peer-split file (addendum constraint A1). **Both splits have
  now landed** — registry at `04d13c1`, `tools/rag` during hand-over — and every reference in this
  plan is by `class Test…` or module-level `def test_…`, with a locating `git grep` in each task
  that touches them. See AMEND 7 and A1's two mapping tables.
- No task introduces a cross-column Python import. Task 43's only new artefact is a template in
  `foundation/templates/`, included by two others — a template include is not an import and the
  import-law gates do not see it.
- No task adds a static asset, a build step, or a second `<style>` mechanism (Global Constraint 8).
- No task names an AI model family, checkpoint or GGUF filename in prose (Global Constraint 9).
- Task 42 is the only task that touches `INSTALLED_APPS`, and it is the only one whose exit gate is
  a **full** suite in both flag states — correctly, since every test in the repo runs against that
  setting.
- No duplication between tasks: 41 deletes a route, 42 rewrites two unrelated views in the same
  file, and they touch disjoint regions of `tools/rag/views.py` and `tools/rag/urls.py`. If they
  are executed out of order or in parallel, the second one to run must re-derive its locations —
  which its own steps already require.
