# Workstreams v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-09-03
**Branch base:** `worktree-model-management-framework` at `2fbd0cb` ("Merge remote-tracking branch 'origin/main'"), which is `main` at `168b904` ("Merge pull request #82: per-model narrowed tool spec, honest failures, pre-row refusal") merged into the branch
**Phase:** Workstreams, the phase after Identity & Auth (`docs/ROADMAP.md`, Phase 1.6)
**Status:** plan, not executed

## Goal

Give the platform a **Workstream**: a named, scoped work area where conversations are born,
documents are contained or pinned, instructions apply, entitlements narrow, and what the work has
touched is remembered — so that "everything about this matter, in one place, and nothing else
leaks in or out of it" is a row rather than a habit.

## Architecture

Nineteen tasks, bottom-up, in two halves that match the spec's own phasing (§19).

**Tasks 1–15 are WS-1 — the stream, the wall, containment.** Task 1 lays the pure contracts leaf
and writes the three import-law guards *before* the modules they police. Tasks 2–4 add the
`agents` column's two tables (`Workstream`, `WorkstreamScopeEntitlement`), `Conversation.workstream`,
the writers in `agents/visibility.py`, and the cross-column seam module `agents/workstreams.py`.
Tasks 5–8 add the `tools/rag` column's containment half: `Document.workstream`/`origin`,
`WorkstreamPin`, the four ORM readers, `tools/rag/workstreams.py` with its registered panel, and
the chunk-metadata cache's second key. Tasks 9–10 spend the wall at its three seams — the one
retrieval filter point, the turn planner's entitlement intersection, and the model picker.
Tasks 11–12 are upload placement and the instructions block. Tasks 13–15 are the zero-JS surfaces:
the five `/chat/w/` routes and the stream page, the sidebar (plain and scoped), and the library
page's pin mode.

**Tasks 16–19 are WS-2 — taint, sharing, consolidation.** Task 16 adds the two taint tables, the
`ArtifactLabels` registry and the stamp on the tool turn. Task 17 is sharing: both gates, dormancy,
the one scoped exception to the 404 house rule, and rulings B, C and E. Task 18 is consolidation:
the distillation constant, the `rag.consolidate` job kind, the note document and its repair path.
Task 19 is the full route matrix, the posture sweeps, the documentation and the ladder to fresh
pixels.

The suite is green at every task boundary, and every task ends with an independently testable
deliverable.

## Tech Stack

Django 5.1+ (server-rendered, zero JavaScript), Postgres + pgvector, LlamaIndex's `PGVectorStore`
and its `MetadataFilters`, pytest + pytest-django. `UniqueConstraint(Lower(...), ...)`, partial
`UniqueConstraint`s, `jsonb_set`/`- 'key'` for the one chunk-metadata `UPDATE`, and the platform's
four existing dotted-path registries (`models.contracts.jobkinds`, `identity.contracts.cascades`,
`identity.contracts.ownership`, `agents.contracts.tools`).

## Spec

`docs/superpowers/specs/2026-09-03-workstreams-design.md` — binding authority, 3,312 lines,
**as amended twice on 2026-09-03**. Read it in full alongside this plan. §23 carries seven
lettered orchestrator rulings (**A–G**) and twenty-seven numbered author decisions; every one of
them is settled. This plan implements them and never re-litigates them.

**In scope:** §19.1 (WS-1) and §19.2 (WS-2), both halves, in one plan file per the plan brief.
The spec lands them as two PRs; this plan's task list is ordered so that Task 15 is a mergeable
WS-1 boundary and Task 19 is a mergeable WS-2 boundary.

**Out of scope, named so it reads as scoped-out rather than forgotten:**

- **ADR 0017.** Written last, after both halves merge (§19.3). It is not a task in this plan;
  Task 19 writes the two ADR *amendments* (0015 and 0016) that this phase's behaviour requires,
  and names 0017 as the follow-up.
- **Everything in §22.** Full-transcript RAG, a stream digest, automatic consolidation triggers,
  move-to-stream, untainting, recipient-initiated consolidation, re-share, stream templates,
  per-stream model bindings, admin-provisioned organisation streams, labelled non-document
  artifacts, conversation compaction, operator-editable prompt constants. Each has a named hook;
  none is built here.
- **Everything in §21.** Nested workstreams, a document in two streams, moving a conversation, a
  stream as a permission, prompt-level enforcement, per-stream agent defaults, automatic
  consolidation, stream digests, multi-tenancy, a feature flag, untainting, a separate notes
  surface.
- **Closing §24 concern 4's watcher race.** The spec implements it as written (a stream upload
  still lands in `INGEST_INBOX_DIR`); the clean fix touches the watcher's directory contract and
  is its own change.
- **A `MODEL_OUTSIDE_STREAM_SCOPE` reason code** (§24 concern 1). The refusal copy is unchanged.

---

## Global Constraints

Every task's requirements implicitly include this section.

1. **No AI model, product, or vendor names or versions appear anywhere** — code, comments, tests,
   docstrings, docs, or commit messages. The repository is going public and ADR 0010's third
   amendment forbids the platform from naming a model for the operator. Describe models
   generically ("the assigned chat model", "the embedding role", "the bound model's context
   window"). Engine keys are not model names: `ModelConnection.engine` takes a registered engine
   adapter's `.name`, and a fixture that needs one takes it from the registry.

2. **Zero JavaScript.** The conversation page's one sanctioned inline `<script>` (the message
   poller and its Enter-to-send handler, `agents/chat/templates/chat/conversation.html`) is
   untouched and nothing new depends on it. Every control this phase adds is a link, a
   `<details>` element, or a plain POST form with `{% csrf_token %}` — the idiom
   `chat/_sidebar.html` already documents.

3. **The import law, unchanged and grown.** `agents/` may not import `tools/` at all, module
   scope or in a function body (`foundation/ops/tests/test_import_law.py::
   test_no_agents_module_imports_a_tools_package`). `identity/` imports no other column.
   Cross-column reach is dotted-path strings and registries only. This phase adds exactly two
   crossings (spec §4.2): `agents/workstreams.py` as a **named seam** `tools/` and `models/` may
   import (the third such name, beside `agents.contracts.*` and `agents.entitlements`), and the
   `WorkstreamPanel` registry for the direction that is forbidden outright. Three new guards
   (Task 1) police both.

4. **Every task ships unit tests and updated docs in the same commit** (ADR 0008). TDD step order:
   the failing test comes first, is run and seen to fail, and only then is the implementation
   written. A task with code and no test is not done; a task that changes behaviour a document
   describes and does not update that document is not done.

5. **Never-500.** Every response on every surface, for every principal, in every posture, is an
   honest status with no `"Traceback"` in the body. A row-addressed URL a principal may not see
   answers **404**, never 403 — with **exactly one** fenced exception, `chat-workstream` for a
   holder of a live `Share` row whose grants no longer cover the stream's tags (§12.3). Every
   refusal is a designed status with honest copy.

6. **Enterprise-posture query pins on every new list surface.** The sidebar N+1 lesson
   (`agents/chat/sidebar.py`'s own docstring: "+2 queries per conversation on `/chat/` and on
   every thread page (47 → 95 at 25 rows)") is binding. Every list this phase adds or changes
   carries a **builder-level absolute pin** and a **view-level 1-row-equals-25-row equality pin**,
   both non-vacuous — the assertion must fail if the row count starts to matter.

7. **Tests that depend on feature-flag or posture state pin their own state.** `identity.testing.
   posture(...)` is the context manager; `settings.FARABUNKER_FEATURES` is overridden explicitly.
   No test inherits a posture from another test's leftovers.

8. **Inert-but-consistent on an open box** (ruling A, §13). The wall, taint and sharing are all
   present in code and unreachable in practice when `identity.access.accounts_on()` is False.
   `_wall_for` returns `frozenset()` **without reading `WorkstreamScopeEntitlement` at all**; the
   Scope, Tags and Sharing sections of the stream page are absent, not disabled; and a stream
   page and a stream turn on an open box run **zero** queries against `EntitlementGrant`,
   `WorkstreamScopeEntitlement` and `WorkstreamTaint`. Wall rows written under `enterprise`
   survive a switch to `open` **dormant, not deleted**, and bind again when the posture returns.

9. **Migrations are numbered from the verified tree and there is no data migration anywhere.**
   `agents/` ends at `0005_conversation_archived_at`; `tools/rag/migrations/` ends at
   `0015_documententitlement`; `identity/` ends at `0003_entitlement_and_grant` and **this phase
   adds no identity migration**. The four migrations this plan creates are, in creation order:
   `agents/0006_workstream.py`, `tools/rag/migrations/0016_document_workstream_and_pins.py`,
   `agents/0007_workstream_taint.py`, `tools/rag/migrations/0017_document_notes.py`. Every new
   column is nullable or carries a default that describes the existing rows correctly.
   `makemigrations --check --dry-run` exits 0 after every task that touches a model.

10. **The suite is green in both orders, on this branch's own private databases.** `farabunker_impl`
    and `farabunker_rev` on port 5433, per the parallel-session isolation rule in `docs/DEV.md`.
    Never `test_farabunker`.

11. **Owner columns, never a `User` FK, on every owned row this phase adds.** `owner_kind`/
    `owner_key`, stamped through `identity.access.owner_fields(principal)`, because the open
    posture's principal is not a `User` row and `owned_rows_q` is the one predicate that reads
    those two columns in every posture.

12. **Cross-column foreign keys are declared as strings**, never as imports:
    `"identity.Entitlement"`, `"agents.Workstream"`, `settings.AUTH_USER_MODEL`.

---

## The acceptance gate

Spec §19.1's eleven WS-1 criteria and §19.2's thirteen WS-2 criteria are the acceptance gate,
verbatim. They are not restated here in full — read them in the spec — but every one of them is
claimed by a named task below, and Task 19 walks the whole list. The two that most often get
built wrongly, restated because an implementer reading one task in isolation would miss them:

- **§19.1 done-when 2b and ruling G.** A contained document **opens from inside its stream and
  404s from outside it**. `rag-document-file` serves it to a stream member; the same URL answers
  404 to a signed-in account outside the stream and to an administrator with `admin_sees_content`
  **off**; and it answers **200** to an administrator with the setting **on**. Containment fences
  the corpus, not the bytes.
- **§19.2 done-when 3c and rulings C + F.** The stream's **owner** reads, consolidates and deletes
  a conversation a recipient started in their stream. The **recipient** sees no Consolidate button
  on any thread — theirs included — no staleness hints at all, and gets a **404** posting to
  `chat-workstream-consolidate` directly.

---

## File Structure

**New files (16):**

| Path | Responsibility |
|---|---|
| `agents/contracts/workstreams.py` | Rule-1 pure leaf: `WorkstreamScope`, `WorkstreamPanel`, the panel registry. No Django. |
| `agents/workstreams.py` | The cross-column seam. `workstream_scope`, `set_upload_placement_default`, `transcript_for`, `record_consolidation`, `taint_ids_for_conversation`, `staleness_for`, `stream_access`, the entitlement cascade handler, panel resolution. Imports `agents.models`, `agents.visibility`, `identity.access`; imports nothing of `tools/`. |
| `agents/runtime/taint.py` | `stamp_turn_taint` — the one taint writer. |
| `agents/chat/views/workstreams.py` | The five (WS-1) then seven (WS-2) `/chat/w/` views. |
| `agents/chat/templates/chat/workstream.html` | The stream page. |
| `agents/chat/templates/chat/workstreams.html` | The stream list. |
| `agents/chat/templates/chat/_workstream_panel.html` | One registered panel's include. |
| `agents/chat/templates/chat/workstream_dormant.html` | The §12.3 403 page. |
| `agents/tests/test_workstreams.py` | Model, visibility and writer tests. |
| `agents/tests/test_taint.py` | The stamp, the union, the audit rows, the generalisation. |
| `agents/chat/tests/test_workstream_page.py` | The stream page and the seven routes. |
| `tools/rag/workstreams.py` | `scope_with_pins`, `pin_document`, `unpin_document`, `stream_documents`, `panel`, `set_document_workstream`. The only module that writes `WorkstreamPin`. |
| `tools/rag/distil.py` | `DISTILLATION_PROMPT`, `CONSOLIDATION_MAX_TURNS`, `distil_conversation`. |
| `tools/rag/tests/test_workstream_corpus.py` | The generated composition-law matrix (§17.2). |
| `tools/rag/tests/test_consolidate.py` | The note document, the overwrite, the failure window. |
| `docs/adr/0017-workstreams.md` | **Not this plan.** Named here only so its absence reads as deferred. |

**Modified files (35):** `agents/models.py`, `agents/visibility.py`, `agents/entitlements.py`,
`agents/apps.py`, `agents/chat/sidebar.py`, `agents/chat/pickers.py`, `agents/chat/urls.py`,
`agents/chat/views/__init__.py`, `agents/chat/views/conversations.py`, `agents/chat/views/thread.py`,
`agents/chat/service.py`, `agents/management/commands/agent_turn.py`,
`agents/chat/templates/chat/_sidebar.html`, `agents/runtime/loop.py`, `agents/runtime/delegate.py`, `agents/runtime/jobs.py`,
`agents/runtime/preflight.py`, `agents/runtime/prompt.py`, `agents/contracts/tools.py`,
`agents/contracts/artifacts.py`, `agents/tests/_helpers.py`, `identity/access.py`,
`identity/routes.py`, `identity/contracts/actions.py`, `models/registry/access.py`,
`tools/rag/models.py`, `tools/rag/access.py`, `tools/rag/retrieval.py`, `tools/rag/labels.py`,
`tools/rag/ingest.py`, `tools/rag/views.py`, `tools/rag/urls.py`, `tools/rag/apps.py`,
`tools/rag/jobs.py`, `tools/rag/tests/_helpers.py`, plus `config/settings.py`,
`foundation/ops/tests/test_import_law.py`, `foundation/ops/tests/test_column_boundaries.py` and
the nine documents §20 names.

---

## Decisions the author made

Twenty-two, on top of the spec's twenty-seven and its seven rulings. Every one is a place where
this plan had to choose something the spec left to the plan, or where the spec's own words did
not match the tree it was written against. **Spec-vs-tree reconciliations are marked `[R]`** and
are collected again, with their evidence, in the section after this one.

1. **One plan file, two mergeable halves.** The spec lands as two PRs (§19). The plan brief names
   one plan file covering the whole scope. Tasks 1–15 are WS-1 and Task 15 ends at a mergeable,
   green, deployable boundary; Tasks 16–19 are WS-2. An executor may stop at 15, merge, and
   resume at 16 against a fresh base. Nothing in Tasks 1–15 references a symbol Tasks 16–19
   introduce.

2. `[R]` **The tool turn's `transaction.atomic()` does not exist and this plan creates it.**
   Spec §7.2 shows `stamp_turn_taint` called "in the same `transaction.atomic()` as the tool
   turn's own `Turn.objects.create(...)`". There is no such block: `agents/runtime/loop.py:418`
   creates the tool turn bare, and `loop.py` does not import `transaction` at all. Task 16 adds
   `from django.db import transaction` and the `with transaction.atomic():` wrapper around the
   create-plus-stamp pair. The wrapper is **new code, not a found seam**, and it is the smallest
   thing that makes §7.2's atomicity claim true.

3. `[R]` **`ToolContext.stream` is set in `agents/runtime/loop.py`, not in `invoke_tool`.**
   Spec §12.4 says "`invoke_tool` sets it from the turn's conversation alongside `tool_key` and
   `supplied_keys`, in the same `replace()` call". `invoke_tool` (`agents/runtime/invoke.py:148`)
   has no conversation — `ToolContext.conversation_id` is a bare string and the row is not in
   scope there. The `ToolContext` is *constructed* at `agents/runtime/loop.py:371`, where
   `conversation` is in scope, alongside `agent_slug` and `tool_access`, which travel by exactly
   the same mechanism and for exactly the same reason. `stream=` joins them there. A delegate
   inherits it because `delegate.py`'s nested `run_loop` call reuses the same value, which is how
   `principal` and `tool_access` already travel.

4. `[R]` **The tools→agents allow-list sweep and its anti-vacuous pin are two tests, not one.**
   Spec §4.3 says the new sweep is modelled on
   `test_agents_reaches_models_registry_through_bindings_and_nothing_else` "including its
   anti-vacuous pin: the test asserts each allowed name really is imported somewhere". That test
   carries no such assertion; the pin lives in a separate, adjacent test —
   `test_the_agents_import_sweeps_actually_reach_the_chat_app`
   (`foundation/ops/tests/test_import_law.py:552`). Task 1 writes both shapes: the sweep, and a
   separately named pin that asserts each of the three allowed names really is imported by some
   tracked production file under `tools/` or `models/`.

5. **The three-name allow-list is complete from its first line, and the pin grows once.**
   `agents.contracts.*` and `agents.entitlements` are imported today
   (`tools/vision/views.py:832`, `tools/rag/views.py:626`, and `tools/vision/views.py:824` calls
   `agents.entitlements` "a NAMED CROSS-COLUMN SEAM" in those words). `agents.workstreams` does
   not exist at Task 1, so Task 1's anti-vacuous pin asserts the two names that exist and Task 7 —
   which creates the first `tools/`-side importer of the third — extends the pin to all three.
   Writing the sweep with a two-name list would fail on the first WS-1 commit; writing the *pin*
   with a three-name list would fail on Task 1.

6. `[R]` **`tools/rag/models.py` does not import `Q`.** Spec §5.5's `Meta.constraints` block uses
   a bare `Q(...)`. That module imports only `models` and `Lower` from Django. Task 5 spells the
   partial-unique condition `models.Q(notes_conversation_id__isnull=False)` rather than adding an
   import for one expression.

7. **`may_manage_workstream` is the one predicate for all seven `chat-workstream-edit` actions,
   and it lives in `agents/visibility.py`.** Spec §14 names `_WORKSTREAM_ACTIONS` and cites
   `may_manage_conversation`'s one-predicate-for-five ruling; it does not name the predicate.
   `may_manage_workstream(principal, workstream, *, settings_row=None)` is that name, in
   `agents/visibility.py` beside `may_manage_conversation`, with the identical `settings_row`
   threading and the identical shape.

8. **`create_workstream(principal, name, *, description="")` returns `None` on a duplicate name,
   not an exception.** The case-insensitive-per-owner unique constraint is a real collision an
   operator can cause by typing a name twice, on a never-500 surface. `None` plus a named
   message on the page is the shape `share_conversation` already uses for its refusals.

9. **The wall is threaded, not re-read, and `_wall_for` lives in `agents/entitlements.py`.**
   Spec §6.3 names `_wall_for(conversation)` without placing it. It goes beside `tool_access_for`
   in `agents/entitlements.py`: that module already owns "the one place a turn's access axes are
   built from a conversation", it already imports `identity.access`, and putting the ruling-A
   posture branch there keeps it out of every `visible_*` body, which §13 requires. Its four
   consumers each receive the *value*, never call it a second time (§13: "one indexed read").

10. **`WORKSTREAM_SIDEBAR_LIMIT = 10` lives in `agents/chat/sidebar.py`** beside `SIDEBAR_LIMIT`,
    for the reason that constant's own comment gives.

11. **The stream page's document panels render through one include, and a panel whose provider
    raises degrades to a named empty section.** Spec §4.2 requires registration in the same commit
    as the handler because `import_string` never swallows. That protects against a *missing*
    module; it does not protect against a provider that raises at render time on a box with a
    broken store. The panel include catches, logs at `exception`, and renders "This section could
    not be loaded." — the same never-500 posture `chat_picker_options` already takes for the model
    picker, and for the same reason: chrome on a page whose real subject is the stream.

12. **The 403 dormant page is its own template and its own view branch**, not a status code on the
    stream page. It renders no stream content at all — not the name, not the conversation count,
    not the document panels — because the fence in §12.3 is "it reveals nothing about the stream's
    contents". It renders the stream's **name** only, which the recipient already has in their
    sidebar.

13. **Taint's audit rows are written inside the same transaction as the tag rows.** §7.3 orders
    the three steps and §16.2 names the detail keys, but does not say whether the audit write is
    inside. It is: `identity.audit.record` never swallows, and an audit row that survived a
    rolled-back tag row would name a tainting that did not happen.

14. **`entitlement_ids_for` is registered under the `"document"` kind and returns the empty set
    for an empty input** without touching the database. The overwhelmingly common taint call has
    at least one `document:` reference or it never gets here (§7.2's early return), but the
    resolver is also the generalisation test's target and a fake one must be able to answer
    cheaply.

15. **The consolidation in-flight marker is a live queue row, read through
    `models.queue.visibility.live_job_for` — a function this plan adds, because none existed.**
    §10.2 says `on_terminal` clears "a `queued` marker the page reads"; §5.4 adds only
    `consolidated_through_index` and `consolidated_at`, and §18's migration table adds no third
    column, so the marker has to be the queue row itself. **An earlier draft of this decision said
    that row was reachable through "`models.contracts.queue`'s existing helpers". It is not:** that
    module's whole public surface is `enqueue`, `get_job`, five state literals, `TERMINAL_STATES`
    and `QueueUnavailable`, and its own docstring forbids the import a reader would need — *"This
    module stays a pure leaf: it does NOT import `models.queue.models`"*. `get_job` needs an id the
    asker does not have.

    The reader therefore goes in **`models/queue/visibility.py`**, which
    `foundation/ops/tests/test_import_law.py` already names as the one submodule of `models.queue`
    that `tools/` and `agents/` may import — *"so a later view cannot reach `models.queue.backend`
    or `models.queue.scheduler` for a visibility answer that already has one home"* — and which
    already holds `InferenceJob` and already filters on `payload__<key>`. One function, one module,
    the contracts leaf untouched. Task 18 Step 3 writes it.

    `on_consolidate_terminal` is then scoped to the row it can actually strand, exactly as
    `on_ingest_terminal` is: on a **re**-consolidation, `stage_document` has already destroyed the
    previous note's chunks and stored file and set it `PENDING`, and one conditional `UPDATE`
    filtered on `status__in=(PENDING, PROCESSING)` is what stops that row being stuck forever.

16. **`transcript_for`'s cap is enforced in the seam, not by its caller.** `limit` is keyword-only
    and required; `tools/rag/jobs.py` passes `CONSOLIDATION_MAX_TURNS`. The seam refuses a
    non-positive or absent limit rather than defaulting, so the §10.4 honesty sentence
    ("distilled the most recent N turns") always has a number behind it.

17. **The note's first line is written by the job, not by the model.** §10.4 step 3 says "with a
    first line naming the source conversation and the date" and the cap sentence "says so in one
    sentence". Both are `run_consolidate`'s own prose, prepended to the model's output, so a
    model that ignored an instruction cannot make the note lie about its own provenance.

18. **The upload form's three states are computed in the view and passed as one context value**,
    `placement_state` ∈ `{"none", "choose", "default"}`, rather than as three booleans a template
    could combine into a fourth state that does not exist.

19. `[R]` **`chat_picker_options` reaches `model_access_for` through `models.registry.bindings`,
    which re-exports it** (`models/registry/bindings.py:85`). A keyword-only `wall=` parameter on
    `models.registry.access.model_access_for` is therefore transparent through that re-export and
    `models/registry/bindings.py` needs no edit. Spec §6.3's table cites `agents/chat/pickers.py:42`,
    which is the `access=model_access_for(principal)` line; that is where the keyword goes.

20. `[R]` **The spec's line citations for `duplicate_conversation` and `may_post_to` are one and
    two lines long respectively.** §5.4 cites `agents/visibility.py:341-403`; the function is
    341–401. §12.2 and §24 concern 6 cite `:404-418`; `may_post_to` is 404–416. Nothing about the
    mechanics changes; recorded so an implementer reading a line range does not think they are in
    the wrong file.

21. **Docs land in Task 19, except each column's README, which lands with its own task.** §20
    lists ten documents. `agents/README.md` and `tools/rag/README.md` describe mechanisms this
    plan builds incrementally, so each is edited by the task that builds the mechanism it
    describes; the seven cross-cutting documents (ROADMAP, the two ADR amendments, `identity/`,
    `models/`, OPERATIONS, EXTENDING) land once, in Task 19, when there is a whole to describe.

22. **The sidebar's Workstreams section shows names, not conversation counts; the stream page
    shows the count.** Spec §15.1's sketch renders a count beside each stream row. Computing one
    per listed row is exactly the N+1 that Global Constraint 6 forbids and that
    `agents/chat/sidebar.py`'s own docstring records as a measured regression (+2 queries per
    conversation, 47 → 95 at 25 rows), and the sidebar is a nav, not a report. The section
    therefore lists names — capped at `WORKSTREAM_SIDEBAR_LIMIT` with the honest "…N more" line —
    and the count appears on `chat-workstream`, where it costs one query for one row. Task 14.

---

## Spec-vs-tree reconciliations

Seven, each verified against `2fbd0cb` on 2026-09-03. Every one is an author decision above; this
section is the evidence, so a reviewer can check the claim without re-reading the tree.

| # | The spec says | The tree says | Where it is handled |
|---|---|---|---|
| R1 | §7.2: the stamp goes "in the same `transaction.atomic()` as the tool turn's own `Turn.objects.create(...)` at `agents/runtime/loop.py:407-419`" | `tool_turn = Turn.objects.create(` is at `loop.py:418` and is **not** inside any transaction; `loop.py` imports no `transaction` at all | Decision 2. Task 16 adds the import and the block. |
| R2 | §12.4: "`invoke_tool` sets it … in the same `replace()` call" | `invoke_tool` (`agents/runtime/invoke.py:148`) has only `conversation_id: str`; the `ToolContext` is built at `agents/runtime/loop.py:371` where `conversation` is in scope | Decision 3. Task 9 sets `stream=` in `loop.py`. |
| R3 | §4.3: the new sweep is modelled on the bindings sweep "including its anti-vacuous pin" | That test (`test_import_law.py:566`) has no pin; the pin is a separate test at `:552` | Decision 4. Task 1 writes two tests. |
| R4 | §5.5: `constraints = [ … condition=Q(notes_conversation_id__isnull=False) … ]` | `tools/rag/models.py` imports `models` and `Lower` only — no `Q` | Decision 6. Task 5 uses `models.Q`. |
| R5 | §6.3: `agents/chat/pickers.py:42` narrows `model_access_for` | Line 42 is `access=model_access_for(principal)`, and the name is imported from `models.registry.bindings` (a re-export at `bindings.py:85`), not from `models.registry.access` | Decision 19. Task 10 changes the call site only. |
| R6 | §5.4 cites `agents/visibility.py:341-403`; §12.2 cites `:404-418` | `duplicate_conversation` is 341–401; `may_post_to` is 404–416 | Decision 20. Cosmetic. |
| R7 | §18: migration 2 carries "`Document`'s first `Meta` ever" | Confirmed: `tools/rag/models.py:50–127` declares `class Document` with **no** `Meta` | No change — the spec is right, and Task 5 depends on it. |

---

# PART ONE — WS-1: the stream, the wall, containment

---

### Task 1: the pure contracts leaf, and the three import-law guards written first

**Files:**
- Create: `agents/contracts/workstreams.py`
- Modify: `agents/contracts/tests/test_purity.py:27-34` (the `_PROBE` string)
- Modify: `foundation/ops/tests/test_import_law.py` (append three tests at the end)
- Test: `agents/contracts/tests/test_workstreams.py`

**Interfaces:**
- Consumes: nothing. This is the first task and it imports no platform module.
- Produces: `agents.contracts.workstreams.WorkstreamScope(workstream_id: int, wall: frozenset[int],
  default_upload_placement: str, may_upload: bool, pinned_file_ids: frozenset[int] = frozenset())`
  — a frozen dataclass, no Django. `WorkstreamPanel(key: str, label: str, provider: str)`,
  `register_workstream_panel(spec: WorkstreamPanel) -> None`,
  `all_workstream_panels() -> list[WorkstreamPanel]`. Every later task that names a scope value
  names this class.

- [ ] **Step 1: Write the failing contracts test**

Create `agents/contracts/tests/test_workstreams.py`:

```python
"""The workstream contracts leaf — a frozen value and a registry, no Django."""
from __future__ import annotations

import pytest

from agents.contracts.workstreams import (
    WorkstreamPanel, WorkstreamScope, all_workstream_panels, register_workstream_panel,
)


def test_a_scope_carries_the_stream_half_and_an_empty_pin_set_by_default():
    """`pinned_file_ids` is THE ONE FIELD `agents/` does not fill (spec
    §6.2, author decision 6): the value crosses the seam with the pins
    empty and `tools/rag/workstreams.py::scope_with_pins` fills them."""
    scope = WorkstreamScope(
        workstream_id=7, wall=frozenset({3}), default_upload_placement="", may_upload=True)
    assert scope.workstream_id == 7
    assert scope.wall == frozenset({3})
    assert scope.default_upload_placement == ""
    assert scope.may_upload is True
    assert scope.pinned_file_ids == frozenset()


def test_a_scope_is_frozen():
    scope = WorkstreamScope(workstream_id=1, wall=frozenset(),
                            default_upload_placement="", may_upload=False)
    with pytest.raises(Exception):
        scope.wall = frozenset({9})


def test_a_panel_registers_and_lists_in_registration_order(isolated_panel_registry):
    register_workstream_panel(WorkstreamPanel("a.one", "One", "pkg.mod.one", "t/one.html"))
    register_workstream_panel(WorkstreamPanel("b.two", "Two", "pkg.mod.two", "t/two.html"))
    assert [p.key for p in all_workstream_panels()] == ["a.one", "b.two"]


def test_registering_the_same_key_twice_replaces_rather_than_duplicates(isolated_panel_registry):
    """Idempotent, like every sibling registry in this codebase — an
    `AppConfig.ready()` can run twice in one process."""
    register_workstream_panel(WorkstreamPanel("a.one", "One", "pkg.mod.one", "t/one.html"))
    register_workstream_panel(WorkstreamPanel("a.one", "One again", "pkg.mod.one", "t/one.html"))
    assert [(p.key, p.label) for p in all_workstream_panels()] == [("a.one", "One again")]


@pytest.mark.parametrize("key,label,provider,template,fragment", [
    ("", "One", "pkg.mod.one", "t/one.html", "non-blank key"),
    ("a.one", "", "pkg.mod.one", "t/one.html", "non-blank label"),
    ("a.one", "One", "notdotted", "t/one.html", "dotted path"),
    ("a.one", "One", "pkg.mod.one", "not-a-template", "template path"),
])
def test_a_malformed_panel_is_refused_at_construction(key, label, provider, template,
                                                      fragment):
    """Construction error, not a render-time `ImportError` on somebody's
    stream page — the same `__post_init__` shape `EntitlementCascade`
    already carries."""
    with pytest.raises(ValueError) as exc:
        WorkstreamPanel(key, label, provider, template)
    assert fragment in str(exc.value)
```

Add the fixture to `agents/contracts/tests/_helpers.py`:

```python
@pytest.fixture
def isolated_panel_registry():
    """Save, clear and restore the workstream-panel registry, so a test
    that registers a panel does not leak it into the next one. The same
    shape `isolated_tool_registry` in this file already has, and for the
    same reason."""
    from agents.contracts import workstreams as ws

    saved = dict(ws._PANELS)
    ws._PANELS.clear()
    try:
        yield
    finally:
        ws._PANELS.clear()
        ws._PANELS.update(saved)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest agents/contracts/tests/test_workstreams.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.contracts.workstreams'`

- [ ] **Step 3: Write the contracts leaf**

Create `agents/contracts/workstreams.py`:

```python
"""The workstream vocabulary every column may import.

A RULE-1 PURE LEAF: no Django, no models, no settings. It carries two
things and nothing else — the frozen VALUE a turn's stream scope travels
as, and the REGISTRY through which a column that owns rows the stream
page must display announces itself.

WHY A REGISTRY AND NOT AN IMPORT. `agents/` may not import `tools/` at
all (import-law rule 3, swept by `foundation/ops/tests/test_import_law.
py::test_no_agents_module_imports_a_tools_package`), and the stream page
is `agents/chat` code that must display documents. So the page renders
whatever is REGISTERED, in registration order, through one include, and
a second panel later — generated images in a stream, say — is a
REGISTRATION, not an edit to a view that would otherwise silently skip
it. That sentence is copied from `identity/contracts/cascades.py`'s own
docstring because it is the same argument.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkstreamScope:
    """One turn's (or one page's) stream scope, as plain data.

    BUILT ONCE per turn or per request and THREADED DOWN; never
    re-derived inside a runner, which is the drift the single filter
    point exists to prevent (`tools.rag.access.DocumentVisibility`'s own
    docstring makes the same promise for the same reason).
    """

    workstream_id: int
    # Entitlement ids; empty = no narrowing. ALWAYS EMPTY when
    # `identity.access.accounts_on()` is False -- the wall is inert on an
    # open box and its rows are never read there (ruling A, spec §13).
    wall: frozenset[int]
    # "" = ask every time (owner decision 5). One of
    # `agents.models.Workstream.UploadPlacement`'s values otherwise.
    default_upload_placement: str
    # False for a share recipient (spec §12.4), so a page that renders
    # the upload form and the pin control asks ONE question rather than
    # re-deriving ownership on a surface that cannot see the row.
    may_upload: bool
    # THE FIFTH FIELD IS FILLED BY THE OTHER COLUMN. `agents/workstreams.
    # py` returns this value with `pinned_file_ids=frozenset()`; only
    # `tools/rag/workstreams.py::scope_with_pins` fills it, because the
    # pin table lives in `tools/rag` (author decision 6, spec §6.2).
    pinned_file_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class WorkstreamPanel:
    """One column's answer to "the stream page needs to show my rows".

    `key`      -- stable identifier, e.g. "rag.documents".
    `label`    -- the page's section heading, e.g. "Documents".
    `provider` -- "package.module.function", with the signature
                  `(principal, workstream_id) -> dict`, resolved at
                  RENDER time by `agents/workstreams.py` and never
                  imported here.
    `template` -- the template path that renders this panel's own
                  `data`, `{% include %}`d by the one wrapper.

    `template` IS WHAT MAKES THE GENERALISATION CLAIM TRUE (M12). Without
    it the wrapper has to branch on `panel.key`, a second registered
    panel renders as a heading and nothing else, and adding one DOES
    require an edit to the very view this registry exists to keep out of
    it -- the opposite of what the paragraph below promises. The template
    lives in the REGISTERING column's own template directory, so the
    markup for another column's rows is written by that column, which is
    the same reason its `provider` is.

    REGISTERED IN THE SAME COMMIT AS THE HANDLER, deliberately, for the
    reason `tools/rag/apps.py` already records about its cascade: the
    resolver uses `import_string` and never swallows, so a registration
    that landed before its module would make every stream page raise
    `ImportError`.
    """

    key: str
    label: str
    provider: str
    template: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("WorkstreamPanel needs a non-blank key")
        if not self.label:
            raise ValueError(f"WorkstreamPanel({self.key!r}) needs a non-blank label")
        if "." not in self.provider:
            raise ValueError(
                f"WorkstreamPanel({self.key!r}).provider must be a dotted path, "
                f"got {self.provider!r}"
            )
        if not self.template.endswith(".html"):
            raise ValueError(
                f"WorkstreamPanel({self.key!r}).template must be a template path, "
                f"got {self.template!r}"
            )


_PANELS: dict[str, WorkstreamPanel] = {}


def register_workstream_panel(spec: WorkstreamPanel) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _PANELS[spec.key] = spec


def all_workstream_panels() -> list[WorkstreamPanel]:
    """Every registered panel, in registration order."""
    return list(_PANELS.values())
```

- [ ] **Step 4: Run the contracts test**

Run: `pytest agents/contracts/tests/test_workstreams.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Add the module to the purity probe**

Edit `agents/contracts/tests/test_purity.py`, the `_PROBE` string:

```python
_PROBE = textwrap.dedent("""
    import sys
    import agents.contracts.artifacts
    import agents.contracts.tools
    import agents.contracts.toolschema
    import agents.contracts.workstreams
    leaked = sorted(m for m in sys.modules if m == "django" or m.startswith("django."))
    print("|".join(leaked))
""")
```

Run: `pytest agents/contracts/tests/test_purity.py -v`
Expected: PASS — the new module imports no Django, so the probe's four tests stay green.

- [ ] **Step 6: Write the three failing import-law guards**

Append to `foundation/ops/tests/test_import_law.py`:

```python
# --- Workstreams: the tools/models -> agents allow-list ------------------

# The CLOSED set of `agents.`-prefixed names another column may import.
# Three names, and each is here because it is already a NAMED SEAM:
#
#   `agents.contracts`     -- the rule-1 pure leaves, imported by
#                             `tools/rag/tools.py` and `tools/vision/`
#                             since P2.
#   `agents.entitlements`  -- `tools/vision/views.py:824` calls it "a
#                             NAMED CROSS-COLUMN SEAM" in those words,
#                             and both `tools/vision/views.py` and
#                             `tools/rag/views.py` import
#                             `tool_access_for` from it today. A
#                             two-name list would fail on the FIRST
#                             workstreams commit, before
#                             `agents/workstreams.py` exists at all.
#   `agents.workstreams`   -- the seam this phase adds (spec §4.2).
#
# Everything else under `agents.` is closed to `tools/` and `models/` by
# DEFAULT, with no second list to remember to update -- the same
# allowlist-not-blocklist shape `IDENTITY_PERMITTED` above already uses.
AGENTS_PERMITTED = ("agents.contracts", "agents.entitlements", "agents.workstreams")

# `agents/` itself is excluded for the same intra-column reason `models/`
# is excluded from the registry sweep: `agents/chat/views/thread.py`
# importing `agents.visibility` is not a violation of anything.
_AGENTS_SCANNED_COLUMNS = ("tools", "models")


def _agents_permitted(dotted: str) -> bool:
    """Whether `dotted` (an `agents...` import target) is one of the
    sanctioned seams, or lies beneath one (`agents.contracts.workstreams`
    under `agents.contracts`)."""
    return any(dotted == seam or dotted.startswith(seam + ".") for seam in AGENTS_PERMITTED)


def _agents_forbidden_imports(source: str) -> list[str]:
    """Every `agents...` import target NOT covered by `AGENTS_PERMITTED`,
    named by the actual dotted path seen -- including the `from agents
    import visibility` form where the imported NAME, not the module path,
    is the part that matters."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "agents" or alias.name.startswith("agents."):
                    if not _agents_permitted(alias.name):
                        hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "agents":
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    if not _agents_permitted(full):
                        hits.append(full)
            elif node.module.startswith("agents."):
                if not _agents_permitted(node.module):
                    hits.append(node.module)
    return hits


def _scanned(*columns: str) -> list[str]:
    """Every tracked, non-test `.py` file under `columns`."""
    out = subprocess.run(["git", "ls-files", "--", *columns], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines()
            if p.endswith(".py") and not _is_test_file(p)]


def test_tools_reaches_agents_through_contracts_entitlements_and_workstreams_and_nothing_else():
    """The counterpart of `test_no_agents_module_imports_a_tools_package`,
    running the other way. `agents/` may import NOTHING of `tools/`; the
    reverse direction is permitted but CLOSED -- exactly three names.

    Not just at module scope: a lazy in-body `from agents.visibility
    import visible_conversations` inside a `tools/rag` view would still
    be a cross-column dependency on a module that is not a seam, so this
    walks EVERY import node rather than only `tree.body`.
    """
    offenders = {}
    for relative in _scanned(*_AGENTS_SCANNED_COLUMNS):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        hits = _agents_forbidden_imports(source)
        if hits:
            offenders[relative] = hits
    assert offenders == {}, offenders


def test_the_tools_to_agents_allowlist_is_not_vacuous():
    """Anti-vacuous pin (author decision 4). A sweep that stopped
    matching anything would pass every assertion in the test above, so
    this asserts the sweep really reaches both columns AND that each
    allowed name really is imported by some production file -- which is
    what makes the allow-list a description of the real seams rather
    than an aspiration.

    `agents.workstreams` joins this list in the task that creates its
    first `tools/`-side importer (`tools/rag/workstreams.py`); until then
    the pin covers the two names the tree already has, because a pin
    asserting an import that does not exist yet is a pin that fails for
    the wrong reason.
    """
    swept = _scanned(*_AGENTS_SCANNED_COLUMNS)
    assert any(p.startswith("tools/") for p in swept)
    assert any(p.startswith("models/") for p in swept)
    sources = "\n".join((REPO_ROOT / p).read_text(encoding="utf-8") for p in swept)
    assert "agents.contracts" in sources
    assert "agents.entitlements" in sources
    # And the gate would really catch a violation, rather than only
    # tolerating the imports that happen to exist.
    planted = "def f():\n    from agents.visibility import visible_conversations\n"
    assert _agents_forbidden_imports(planted) == ["agents.visibility"]


def test_agents_workstreams_imports_no_tools_package():
    """The narrow, NAMED twin of `test_no_agents_module_imports_a_tools_
    package`, pinning that the one `agents` module other columns may
    import does not itself reach back across.

    Redundant with the broad sweep by construction, and kept because
    this module is the one whose accidental widening would be least
    visible in review: it is the file an implementer edits when a
    workstream needs to know something about a document, and the correct
    answer there is always a registry, never an import.
    """
    path = REPO_ROOT / "agents/workstreams.py"
    if not path.is_file():
        pytest.skip("agents/workstreams.py does not exist yet (added in a later task)")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module] if isinstance(node, ast.ImportFrom) and node.module
            else []
        )
        hits += [n for n in names if n == "tools" or n.startswith("tools.")]
    assert hits == [], hits
```

Add `import pytest` to the module's imports if it is not already there (the `skip` above needs it).

- [ ] **Step 7: Run the guards and watch the third skip**

Run: `pytest foundation/ops/tests/test_import_law.py -k "agents_through_contracts or allowlist_is_not_vacuous or workstreams_imports_no_tools" -v`
Expected: 2 PASS, 1 SKIP (`agents/workstreams.py does not exist yet`). The two that pass are what
makes the first workstreams commit that would violate the law fail; the skip disappears in Task 4.

- [ ] **Step 8: Run the whole import-law and column-boundary suite**

Run: `pytest foundation/ops/tests/test_import_law.py foundation/ops/tests/test_column_boundaries.py -q`
Expected: all pass. Nothing existing changed.

- [ ] **Step 9: Commit**

```bash
git add agents/contracts/workstreams.py agents/contracts/tests/test_workstreams.py \
        agents/contracts/tests/_helpers.py agents/contracts/tests/test_purity.py \
        foundation/ops/tests/test_import_law.py
git commit -m "feat(agents): the workstream contracts leaf and the three import-law guards"
```

---

### Task 2: `Workstream`, `WorkstreamScopeEntitlement`, `Conversation.workstream`, and migration `agents/0006`

**Files:**
- Modify: `agents/models.py` (new models after `Conversation`; `Conversation` gains one column and one index; `Share.Target` gains a value; `TARGET_KEY_PARSERS` gains an entry)
- Create: `agents/migrations/0006_workstream.py`
- Modify: `foundation/ops/tests/test_column_boundaries.py:527` (`_VISIBILITY_MODELS`)
- Modify: `agents/tests/_helpers.py` (the `_workstream` fixture, spec §17.9)
- Test: `agents/tests/test_workstreams.py`

**Interfaces:**
- Consumes: nothing from Task 1 — the models are Django-side and the contracts leaf is pure.
- Produces: `agents.models.Workstream` with `UploadPlacement.UNIVERSAL == "universal"` /
  `UploadPlacement.CONTAINED == "contained"`, fields `name`, `description`, `instructions`,
  `default_upload_placement`, `owner_kind`, `owner_key`, `archived_at`, `created_at`,
  `updated_at`; reverse accessors `scope_entitlements`, `conversations`, `pins`, `documents`.
  `agents.models.WorkstreamScopeEntitlement`. `Conversation.workstream` (nullable, `PROTECT`).
  `Share.Target.WORKSTREAM == "workstream"`. The `_workstream(...)` test helper.

- [ ] **Step 1: Write the failing model test**

Create `agents/tests/test_workstreams.py`:

```python
"""`Workstream` and its wall table — the rows, their constraints, and the
one relationship that must refuse rather than cascade."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from agents.models import Conversation, Share, Workstream, WorkstreamScopeEntitlement
from agents.tests._helpers import _workstream          # noqa: F401 -- used from Task 3 onward
from identity.access import owner_fields
from identity.testing import make_entitlement, make_user, user_principal

pytestmark = pytest.mark.django_db


def test_a_stream_is_owned_by_owner_columns_not_a_user_fk():
    """Owner COLUMNS, matching every other owned row in this codebase —
    the open posture's single principal is not a `User` row, and
    `owned_rows_q` is the one predicate that reads these two columns in
    every posture."""
    user = make_user()
    stream = Workstream.objects.create(name="Q3 planning",
                                       **owner_fields(user_principal(user)))
    assert stream.owner_kind == "user"
    assert stream.owner_key == str(user.pk)
    assert not hasattr(stream, "owner_id")


def test_two_people_may_each_have_a_stream_called_taxes():
    """CASE-INSENSITIVELY UNIQUE PER OWNER, not globally: a global unique
    would make the second person's stream unnameable for a reason no page
    could explain."""
    one, two = make_user(), make_user()
    Workstream.objects.create(name="Taxes", **owner_fields(user_principal(one)))
    Workstream.objects.create(name="Taxes", **owner_fields(user_principal(two)))
    assert Workstream.objects.filter(name="Taxes").count() == 2


def test_one_person_may_not_have_two_streams_called_taxes_in_any_casing():
    user = make_user()
    Workstream.objects.create(name="Taxes", **owner_fields(user_principal(user)))
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Workstream.objects.create(name="taxes", **owner_fields(user_principal(user)))


def test_the_upload_default_is_blank_not_null_when_it_is_unset():
    """`"" = ask every time` (owner decision 5). Not `null=True`: a
    nullable choice column would give one column two spellings of
    empty."""
    stream = Workstream.objects.create(name="One")
    assert stream.default_upload_placement == ""
    stream.default_upload_placement = Workstream.UploadPlacement.CONTAINED
    stream.save(update_fields=["default_upload_placement", "updated_at"])
    stream.refresh_from_db()
    assert stream.default_upload_placement == "contained"


def test_archived_at_is_a_timestamp_not_a_boolean():
    """Mirroring `Conversation.archived_at`: "when" is strictly more
    information than "whether" and costs the same column."""
    stream = Workstream.objects.create(name="One")
    assert stream.archived_at is None


def test_a_wall_row_is_unique_per_stream_and_entitlement():
    stream = Workstream.objects.create(name="One")
    ent = make_entitlement()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)


def test_the_walls_reverse_accessor_is_scope_entitlements_not_entitlement_labels():
    """AUTHOR DECISION 4 (spec §5.2), asserted rather than assumed. The
    four existing label tables all name theirs `entitlement_labels`
    precisely so `agents.visibility.label_permitted_q` can be one
    function for two models — and a WALL is not a label. Sharing the
    accessor would let a future `label_permitted_q` call compile against
    a stream and answer the wrong question SILENTLY."""
    stream = Workstream.objects.create(name="One")
    assert hasattr(stream, "scope_entitlements")
    assert not hasattr(stream, "entitlement_labels")


def test_a_conversation_is_loose_by_default_and_may_be_born_in_a_stream(agent):
    stream = Workstream.objects.create(name="One")
    loose = Conversation.objects.create(agent=agent)
    inside = Conversation.objects.create(agent=agent, workstream=stream)
    assert loose.workstream_id is None
    assert inside.workstream_id == stream.pk


def test_deleting_a_stream_that_still_holds_a_conversation_is_refused(agent):
    """PROTECT, not CASCADE. `CASCADE` would make one button delete
    somebody's conversations, which is the most destructive gesture on
    this surface hiding behind the least alarming control (author
    decision 15)."""
    stream = Workstream.objects.create(name="One")
    Conversation.objects.create(agent=agent, workstream=stream)
    with pytest.raises(ProtectedError):
        stream.delete()


def test_workstream_is_a_share_target_whose_key_parses_as_an_integer():
    stream = Workstream.objects.create(name="One")
    user = make_user()
    row = Share.objects.create(target_type=Share.Target.WORKSTREAM,
                               target_key=str(stream.pk), user=user,
                               level=Share.Level.USE)
    assert row.pk is not None


def test_a_workstream_share_with_an_unparseable_key_is_refused_at_save():
    """`Share.save()` already refuses a key its parser rejects; the new
    target inherits that rather than needing its own guard."""
    user = make_user()
    with pytest.raises(ValueError):
        Share.objects.create(target_type=Share.Target.WORKSTREAM,
                             target_key="not-an-int", user=user)
```

Add to `agents/tests/_helpers.py`, beside the existing re-exports. **A plain function, not a
`@pytest.fixture`** — every caller invokes it directly with arguments, which a fixture cannot do.
Its `_` prefix breaks this module's own `make_agent`/`make_flow`/`make_conversation` convention, and
it is kept anyway because spec §17.9 names it `_workstream(...)` in so many words; the spec wins,
and this note is here so the inconsistency reads as deliberate. Task 2's own tests build their rows
with `Workstream.objects.create(...)` because they are testing the model's constraints directly; the
helper's first real caller is Task 3.

```python
def _workstream(principal=None, **overrides):
    """A `Workstream` row for a test that needs one to exist.

    The `agents`-side half of spec §17.9's "no new fixtures beyond a
    `_workstream(...)` helper and its `tools/rag` twin". Written
    directly, not through `agents.visibility.create_workstream`, for the
    same reason `identity.testing.make_entitlement` writes its row
    directly: a test that merely needs a stream to EXIST should not have
    to satisfy the writer's refusals to get one.
    """
    from agents.models import Workstream
    from identity.access import owner_fields
    from identity.contracts.principals import OPEN_PRINCIPAL

    fields = {"name": f"stream-{next(_names)}"}
    fields.update(owner_fields(principal if principal is not None else OPEN_PRINCIPAL))
    fields.update(overrides)
    return Workstream.objects.create(**fields)
```

If `_names` does not already exist in that module, add `import itertools` and
`_names = (f"{n}" for n in itertools.count(1))` beside `CALLS`.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest agents/tests/test_workstreams.py -v`
Expected: FAIL — `ImportError: cannot import name 'Workstream' from 'agents.models'`

- [ ] **Step 3: Add the two models to `agents/models.py`**

Insert after `class Conversation` (which ends at the `__str__` before `class ToolInvocation`):

```python
class Workstream(models.Model):
    """A named scoped work area.

    OWNER COLUMNS, NOT A USER FK, matching every other owned row in this
    codebase (`Agent`, `Flow`, `Conversation`, `GenerationJob`,
    `AskRecord`): the open posture's single principal is not a `User`
    row, and `owned_rows_q` is the one predicate that already knows how
    to read these two columns in every posture.

    NO SLUG. A stream is addressed by integer pk in a URL and by name on
    a page. `Agent` and `Flow` carry slugs because a slug is how a
    declaration in `agents/defaults.py` and a tool key (`agent.<slug>`)
    name a row IN CODE; nothing names a stream in code, and an immutable
    slug on a user-renamed thing would be a second identity nobody edits.

    NO `enabled`. `Agent`/`Flow` carry one because a disabled agent must
    refuse a turn while staying visible to its operator; a stream has no
    such state -- `archived_at` covers "put it away".
    """

    class UploadPlacement(models.TextChoices):
        UNIVERSAL = "universal", "The universal library"
        CONTAINED = "contained", "This workstream only"

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    # Appended to the agent's system prompt for every turn in this
    # stream, as a clearly-labelled block (spec §11). TextField, not a
    # capped CharField: it is a prompt, and a cap is a truncation nobody
    # asked for.
    instructions = models.TextField(blank=True, default="")
    # "" = ASK EVERY TIME (owner decision 5). Not `null=True`: a blank
    # CharField with choices is the shape `owner_kind`/`media_type`
    # already use for "not set", and a nullable choice column would give
    # one column two spellings of empty.
    default_upload_placement = models.CharField(
        max_length=16, choices=UploadPlacement.choices, blank=True, default="")
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    archived_at = models.DateTimeField(null=True, blank=True, default=None)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            # CASE-INSENSITIVELY UNIQUE PER OWNER, not globally: two people
            # on one box may each have a stream called "Taxes", and a
            # global unique would make the second person's stream
            # unnameable for a reason no page could explain. `Lower(...)`
            # matches `uniq_agent_slug_ci` and `uniq_category_name_ci`.
            models.UniqueConstraint(Lower("name"), "owner_kind", "owner_key",
                                    name="uniq_workstream_name_ci_per_owner"),
        ]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_ws_owner"),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class WorkstreamScopeEntitlement(models.Model):
    """One entitlement in a stream's WALL (owner decision 3a).

    NO ROWS = NO NARROWING, which is what makes an unwalled stream cost
    exactly what a loose conversation costs, and what makes every
    existing behaviour the default. Present rows narrow, and they narrow
    by INTERSECTION with the reader's own grants -- never by union
    (spec §6.1).

    The same table shape as `DocumentEntitlement`, `ToolEntitlement`,
    `AgentEntitlement`, `FlowEntitlement` and `ModelSetEntitlement`
    before it, deliberately: a sixth spelling of "this row carries
    entitlement ids" would be a sixth thing to keep in agreement.

    THE REVERSE ACCESSOR IS `scope_entitlements`, NOT `entitlement_labels`
    (author decision 4). The four existing label tables all name theirs
    `entitlement_labels` precisely so `agents.visibility.
    label_permitted_q` can be one function for two models -- and a wall
    is not a label. A label says "holders of this may reach this row"; a
    wall says "narrow this row's reach to this". Reusing the accessor
    would let a future `label_permitted_q` call compile against a stream
    and answer the wrong question silently.
    """

    workstream = models.ForeignKey(Workstream, on_delete=models.CASCADE,
                                   related_name="scope_entitlements")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="workstream_scopes")
    # PROVENANCE, written and not read by anything this phase ships --
    # deliberately, and exactly as `DocumentEntitlement.labelled_by` and
    # `Share.shared_by` already are. "Who narrowed this stream, and when"
    # is the question an operator asks of a wall they did not set, and a
    # column that is cheap to write now cannot be back-filled later.
    set_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="+")
    set_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workstream", "entitlement"],
                                               name="uniq_workstream_scope")]
        indexes = [models.Index(fields=["entitlement"], name="agents_wsscope_ent")]
```

`Lower` and `settings` are already imported by this module
(`from django.db.models.functions import Lower`, `from django.conf import settings`).

- [ ] **Step 4: Give `Conversation` its column and its index**

In `class Conversation`, after `archived_at`:

```python
    # NEW. Null = loose (today's behaviour, in every respect). Set = born
    # in that stream, FOR LIFE: no route writes this column after
    # creation, and `agents/visibility.py` exposes no setter (owner
    # decision 2). PROTECT, not CASCADE -- deleting a stream that still
    # holds conversations is refused and named (spec §8.1).
    workstream = models.ForeignKey("agents.Workstream", null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="conversations")
```

and in its `Meta.indexes`:

```python
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"], name="agents_conv_owner"),
            # The scoped sidebar's exact ordering (spec §15.2), so a
            # stream's conversation list is one index scan rather than a
            # filter over the owner index.
            models.Index(fields=["workstream", "-updated_at"], name="agents_conv_ws"),
        ]
```

- [ ] **Step 5: Give `Share` its fifth target**

In `class Share`'s `Target`:

```python
    class Target(models.TextChoices):
        CONVERSATION = "conversation", "Conversation"
        AGENT = "agent", "Agent"
        FLOW = "flow", "Flow"
        VISION_OUTPUT = "vision_output", "Generated image"
        WORKSTREAM = "workstream", "Workstream"     # NEW
```

and in `TARGET_KEY_PARSERS`:

```python
TARGET_KEY_PARSERS = {
    Share.Target.CONVERSATION: _uuid_or_none,
    Share.Target.AGENT: _int_or_none,
    Share.Target.FLOW: _int_or_none,
    Share.Target.VISION_OUTPUT: _int_or_none,
    Share.Target.WORKSTREAM: _int_or_none,          # NEW
}
```

Nothing else about `Share` changes. `target_key` is already `CharField(max_length=64)` holding a
pk as text, `Share.save()` already refuses a key its parser rejects, and `agents/shares.py::
shared_keys` already drops an unparseable row with a warning.

- [ ] **Step 6: Generate the migration and check its operations**

Run: `python manage.py makemigrations agents --name workstream`
Expected: `agents/migrations/0006_workstream.py` created.

Open it and confirm, editing only the `dependencies` list if `makemigrations` did not infer all
three:

```python
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("identity", "0003_entitlement_and_grant"),
        ("agents", "0005_conversation_archived_at"),
    ]
```

The operations must be exactly: `CreateModel(Workstream)`, `CreateModel(WorkstreamScopeEntitlement)`,
`AddField(conversation.workstream)`, `AddIndex(agents_conv_ws)`, and one `AlterField` on
`share.target_type` — a **choices-only** change, which Django emits and Postgres executes without
touching the column. No data migration: `workstream` null means "loose", which every existing
conversation is.

Run: `python manage.py makemigrations --check --dry-run`
Expected: exit 0.

- [ ] **Step 7: Add `Workstream` to the chat-column boundary gate**

Edit `foundation/ops/tests/test_column_boundaries.py:527`:

```python
_VISIBILITY_MODELS = ("Conversation", "Agent", "Flow", "Share", "Workstream")
```

No module under `agents/chat` queries streams directly either — every read goes through
`agents/visibility.py`, which is already in `_VISIBILITY_MODULE_EXCLUSION`.

- [ ] **Step 8: Run the tests**

Run: `pytest agents/tests/test_workstreams.py foundation/ops/tests/test_column_boundaries.py -v`
Expected: PASS (11 new + the existing boundary suite)

Run: `pytest agents/ -q`
Expected: PASS — nothing existing changed behaviour.

- [ ] **Step 9: Update the column README**

In `agents/README.md`, add a **Workstreams** section naming the two tables, the immutable
`Conversation.workstream` column, and `Share`'s fifth target. Say that the wall's reverse accessor
is deliberately not `entitlement_labels`, and why.

- [ ] **Step 10: Commit**

```bash
git add agents/models.py agents/migrations/0006_workstream.py agents/tests/test_workstreams.py \
        agents/tests/_helpers.py agents/README.md foundation/ops/tests/test_column_boundaries.py
git commit -m "feat(agents): Workstream, its wall table, and Conversation.workstream"
```

---

### Task 3: the writers — `visible_workstreams`, `create_workstream`, and the five owner-only mutations

**Files:**
- Modify: `agents/visibility.py` (append after `create_conversation`)
- Modify: `identity/contracts/actions.py` (nine new constants, and `AUDIT_ACTIONS`)
- Test: `agents/tests/test_workstreams.py` (append)

**Interfaces:**
- Consumes: `agents.models.Workstream`, `WorkstreamScopeEntitlement`, `Share.Target.WORKSTREAM`
  (Task 2).
- Produces, all in `agents/visibility.py`:
  `visible_workstreams(principal) -> QuerySet[Workstream]`;
  `may_manage_workstream(principal, workstream, *, settings_row=None) -> bool`;
  `create_workstream(principal, name, *, description="") -> Workstream | None`;
  `rename_workstream(principal, workstream, name) -> bool`;
  `set_workstream_description(principal, workstream, description) -> bool`;
  `set_workstream_instructions(principal, workstream, instructions) -> bool`;
  `set_workstream_archived(principal, workstream, *, archived: bool) -> bool`;
  `set_workstream_upload_default(principal, workstream, placement) -> bool`;
  `set_workstream_scope(principal, workstream, entitlement_ids) -> tuple[frozenset[int], frozenset[int]] | None`
  (added, removed);
  `delete_workstream(principal, workstream) -> str | None` (a refusal sentence, or `None` on
  success).
  In `identity/contracts/actions.py`: `WORKSTREAM_CREATED`, `WORKSTREAM_RENAMED`,
  `WORKSTREAM_DELETED`, `WORKSTREAM_ARCHIVED`, `WORKSTREAM_UNARCHIVED`,
  `WORKSTREAM_INSTRUCTIONS_SET`, `WORKSTREAM_SCOPE_ADDED`, `WORKSTREAM_SCOPE_REMOVED`,
  `WORKSTREAM_UPLOAD_DEFAULT_SET`.

- [ ] **Step 1: Write the failing visibility test**

Append to `agents/tests/test_workstreams.py`:

```python
from agents.visibility import (
    create_workstream, delete_workstream, may_manage_workstream, rename_workstream,
    set_workstream_archived, set_workstream_instructions, set_workstream_scope,
    set_workstream_upload_default, visible_workstreams,
)
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import grant, make_admin, posture, sign_in


def test_an_open_box_sees_every_stream_and_runs_no_permission_query():
    """THE OPEN BRANCH IS FIRST, as in every function in this module —
    and the claim is asserted, not stated: ZERO queries against
    `EntitlementGrant`, named by table, so the pin cannot pass by
    counting a total that happened not to move."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _workstream()
    _workstream()
    with posture("open"):
        with CaptureQueriesContext(connection) as captured:
            rows = list(visible_workstreams(OPEN_PRINCIPAL))
    assert len(rows) == 2
    assert not [q for q in captured.captured_queries
                if "identity_entitlementgrant" in q["sql"]]


def test_a_member_sees_their_own_streams_and_the_ones_shared_to_them():
    owner, other = make_user(), make_user()
    mine = _workstream(user_principal(owner), name="Mine")
    theirs = _workstream(user_principal(other), name="Theirs")
    shared = _workstream(user_principal(other), name="Shared")
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(shared.pk),
                         user=owner, level=Share.Level.USE)
    with posture("enterprise"):
        keys = set(visible_workstreams(user_principal(owner)).values_list("pk", flat=True))
    assert keys == {mine.pk, shared.pk}
    assert theirs.pk not in keys


def test_a_dormant_share_still_lists():
    """THE DORMANCY CHECK IS NOT IN `visible_workstreams` (spec §6.4) —
    a dormant share still LISTS, and hiding it would leave a recipient
    with a stream that vanished and no sentence explaining why. In WS-1
    there are no tags, so this asserts the SHAPE: the share clause is
    ownership-and-share only, with no tag join."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert visible_workstreams(user_principal(reader)).filter(pk=stream.pk).exists()


def test_creating_a_stream_stamps_the_actor_and_audits_it():
    user = make_user()
    with posture("enterprise"):
        stream = create_workstream(user_principal(user), "Q3 planning")
    assert stream.owner_key == str(user.pk)
    rows = audit.for_target("workstream", stream.pk)
    assert [r.action for r in rows] == [actions.WORKSTREAM_CREATED]


def test_creating_a_stream_with_a_name_the_actor_already_used_answers_none():
    """AUTHOR DECISION 8: a real collision an operator can cause by
    typing a name twice, on a never-500 surface. `None` plus a named
    message on the page, the shape `share_conversation` already uses."""
    user = make_user()
    with posture("enterprise"):
        assert create_workstream(user_principal(user), "Taxes") is not None
        assert create_workstream(user_principal(user), "taxes") is None


def test_a_recipient_may_not_manage_a_stream_shared_to_them():
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert may_manage_workstream(user_principal(owner), stream) is True
        assert may_manage_workstream(user_principal(reader), stream) is False
        assert rename_workstream(user_principal(reader), stream, "Renamed") is False
        assert set_workstream_instructions(user_principal(reader), stream, "hi") is False
        assert set_workstream_archived(user_principal(reader), stream, archived=True) is False


def test_the_wall_accepts_only_entitlements_the_setter_holds():
    """Spec §6.1: not because holding it would grant anything (it would
    not; the outer intersection sees to that) but because a wall naming
    an entitlement its owner does not hold is a wall that narrows to the
    empty set and reads as a bug."""
    user = make_user()
    held, unheld = make_entitlement(), make_entitlement()
    grant(held, user=user)
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        result = set_workstream_scope(user_principal(user), stream, [held.pk, unheld.pk])
    assert result is None
    assert stream.scope_entitlements.count() == 0


def test_setting_the_wall_writes_the_difference_and_audits_both_directions():
    user = make_user()
    one, two = make_entitlement(), make_entitlement()
    grant(one, user=user)
    grant(two, user=user)
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        added, removed = set_workstream_scope(user_principal(user), stream, [one.pk])
        assert (added, removed) == (frozenset({one.pk}), frozenset())
        added, removed = set_workstream_scope(user_principal(user), stream, [two.pk])
        assert (added, removed) == (frozenset({two.pk}), frozenset({one.pk}))
    kinds = [r.action for r in audit.for_target("workstream", stream.pk)]
    assert kinds.count(actions.WORKSTREAM_SCOPE_ADDED) == 2
    assert kinds.count(actions.WORKSTREAM_SCOPE_REMOVED) == 1


def test_deleting_a_stream_that_holds_rows_is_refused_by_name(agent):
    """`delete_workstream` COUNTS FIRST and refuses by name — the same
    count-then-name shape `delete_entitlement` already uses. "Delete them
    first", not "delete or re-home them first", because re-homing is not
    an action this product has."""
    user = make_user()
    stream = _workstream(user_principal(user))
    Conversation.objects.create(agent=agent, workstream=stream)
    with posture("enterprise"):
        message = delete_workstream(user_principal(user), stream)
    assert message is not None
    assert "1 conversation" in message
    assert "0 documents" in message
    assert Workstream.objects.filter(pk=stream.pk).exists()


def test_an_emptied_stream_deletes_and_takes_its_shares_with_it():
    user, reader = make_user(), make_user()
    stream = _workstream(user_principal(user))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert delete_workstream(user_principal(user), stream) is None
    assert not Workstream.objects.filter(pk=stream.pk).exists()
    assert not Share.objects.filter(target_type=Share.Target.WORKSTREAM,
                                    target_key=str(stream.pk)).exists()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest agents/tests/test_workstreams.py -v -k "visible or create_workstream or manage or wall or delete"`
Expected: FAIL — `ImportError: cannot import name 'visible_workstreams' from 'agents.visibility'`

- [ ] **Step 3: Add the nine audit actions**

In `identity/contracts/actions.py`, after the `ENGINE_FILE_DELETED` block:

```python
# Workstreams (2026-09-03, WS-1): a named scoped work area, its wall, and
# its upload default. Naming follows the existing convention exactly --
# dotted `namespace.verb_phrase`, past tense, constant name the
# SCREAMING_SNAKE of the value's tail.
#
# `SHARE_ADDED`/`SHARE_REVOKED` are REUSED, with `target_type=
# "workstream"`: they already carry the subject in `detail` and the
# target type in its own column, and a second pair of constants would
# split one question -- "who was this shared with" -- across two
# vocabularies.
WORKSTREAM_CREATED = "workstream.created"
WORKSTREAM_RENAMED = "workstream.renamed"
WORKSTREAM_DELETED = "workstream.deleted"
WORKSTREAM_ARCHIVED = "workstream.archived"
WORKSTREAM_UNARCHIVED = "workstream.unarchived"
WORKSTREAM_INSTRUCTIONS_SET = "workstream.instructions_set"
WORKSTREAM_SCOPE_ADDED = "workstream.scope_added"
WORKSTREAM_SCOPE_REMOVED = "workstream.scope_removed"
WORKSTREAM_UPLOAD_DEFAULT_SET = "workstream.upload_default_set"
```

and extend `AUDIT_ACTIONS` with the same nine names, on their own line after
`ENGINE_FILE_DELETED,`.

- [ ] **Step 4: Write the visibility functions**

Append to `agents/visibility.py` (and extend its `from agents.models import ...` line to name
`Workstream` and `WorkstreamScopeEntitlement`, and add
`from identity.access import held_entitlement_ids` — already imported — plus
`from identity import audit` and `from identity.contracts import actions`):

```python
def visible_workstreams(principal):
    """Every workstream this principal may read.

    THE OPEN BRANCH IS FIRST, as in every function in this module.
    `owned_rows_q` is own-rows-plus-service-rows-for-an-admin; a `Share`
    extends it to somebody the owner named. The DORMANCY check is NOT
    here -- a dormant share still LISTS (spec §12.2), and hiding it
    would leave a recipient with a stream that vanished and no sentence
    explaining why.
    """
    qs = Workstream.objects.all()
    if sees_all_content(principal):
        return qs
    return qs.filter(
        owned_rows_q(principal)
        | Q(pk__in=shared_keys(Share.Target.WORKSTREAM, principal))
    ).distinct()


def may_manage_workstream(principal, workstream, *, settings_row=None) -> bool:
    """Whether `principal` may rename, describe, instruct, scope,
    archive, set the upload default on, share, consolidate in, or delete
    `workstream`.

    ONE PREDICATE FOR ALL OF THEM (author decision 7), and that is the
    ruling rather than an accident of refactoring -- the same ruling
    `may_manage_conversation` already made for its own five actions and
    `identity-entitlement-edit` for its `_ENTITLEMENT_ACTIONS` tuple.
    Seven predicates that happened to agree today would be seven chances
    for one of them to drift, and the drift would show as a control that
    404s.

    A STREAM SHARED TO SOMEBODY IS NOT MANAGEABLE BY THEM (owner
    decision 7): a recipient reads and converses, and re-sharing,
    editing the wall, changing pins, uploading, consolidating and
    deleting stay owner-only in v1.

    `settings_row` is threaded straight into `sees_all_content`, for the
    reason `may_manage_conversation`'s own docstring gives at length: the
    sidebar asks a per-row predicate and a fresh singleton read per row
    also defeats `identity.access._user_row`'s memoisation.
    """
    if sees_all_content(principal, settings_row=settings_row):
        return True
    return may_read_owned_row(principal, workstream)


def create_workstream(principal, name: str, *, description: str = ""):
    """A new workstream owned by `principal`, or `None` when the name
    collides with one they already have.

    THE CREATE LIVES HERE, not in the view, for the reason
    `create_conversation`'s own docstring gives: a view that could create
    a row could create one without `owner_fields`, and that row would be
    invisible to every filter identity added.

    `None` RATHER THAN AN EXCEPTION on a collision (author decision 8).
    `uniq_workstream_name_ci_per_owner` is a real thing an operator
    causes by typing a name twice, on a never-500 surface, so the caller
    renders a named message rather than catching `IntegrityError`.
    """
    name = (name or "").strip()
    if not name:
        return None
    fields = owner_fields(principal)
    if Workstream.objects.filter(name__iexact=name, **fields).exists():
        return None
    row = Workstream.objects.create(name=name, description=description, **fields)
    audit.record(principal, actions.WORKSTREAM_CREATED, target_type="workstream",
                 target_key=row.pk, target_label=row.name)
    return row


def rename_workstream(principal, workstream, name: str) -> bool:
    """Give `workstream` a new `name` if `principal` may. True if it was
    written. A collision with another of this owner's streams is False,
    for `create_workstream`'s reason."""
    name = (name or "").strip()
    if not name or not may_manage_workstream(principal, workstream):
        return False
    clash = Workstream.objects.filter(
        name__iexact=name, owner_kind=workstream.owner_kind, owner_key=workstream.owner_key,
    ).exclude(pk=workstream.pk).exists()
    if clash:
        return False
    was = workstream.name
    workstream.name = name
    workstream.save(update_fields=["name", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_RENAMED, target_type="workstream",
                 target_key=workstream.pk, target_label=name, was=was)
    return True


def set_workstream_description(principal, workstream, description: str) -> bool:
    """Not audited: a description is a label on the operator's own row,
    carrying no access consequence, and §16.3's rule is that the trail
    records what changed about ACCESS or about what the box holds."""
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.description = description or ""
    workstream.save(update_fields=["description", "updated_at"])
    return True


def set_workstream_instructions(principal, workstream, instructions: str) -> bool:
    """AUDITED, unlike the description, because instructions reach the
    model: they are the operator's standing words for every turn in this
    stream (spec §11), and "what was this box told to do, and when" is
    exactly the question the trail exists to answer."""
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.instructions = instructions or ""
    workstream.save(update_fields=["instructions", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_INSTRUCTIONS_SET, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 cleared=not workstream.instructions.strip())
    return True


def set_workstream_archived(principal, workstream, *, archived: bool) -> bool:
    """ONE FUNCTION FOR BOTH DIRECTIONS, keyed on a flag rather than two
    near-identical bodies -- `set_conversation_archived`'s own ruling,
    applied to the second archivable row. `timezone.now()` on the way in,
    `None` on the way out."""
    if not may_manage_workstream(principal, workstream):
        return False
    workstream.archived_at = timezone.now() if archived else None
    workstream.save(update_fields=["archived_at", "updated_at"])
    audit.record(principal,
                 actions.WORKSTREAM_ARCHIVED if archived else actions.WORKSTREAM_UNARCHIVED,
                 target_type="workstream", target_key=workstream.pk,
                 target_label=workstream.name)
    return True


def set_workstream_upload_default(principal, workstream, placement: str) -> bool:
    """Set (or clear, with `""`) the stream's upload default.

    `""` is "ask every time" (owner decision 5), and it is a legal value
    here rather than a separate clear function, for the same reason
    `set_conversation_archived` takes a flag.
    """
    if not may_manage_workstream(principal, workstream):
        return False
    if placement not in ("", *Workstream.UploadPlacement.values):
        return False
    workstream.default_upload_placement = placement
    workstream.save(update_fields=["default_upload_placement", "updated_at"])
    audit.record(principal, actions.WORKSTREAM_UPLOAD_DEFAULT_SET, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 placement=placement or "ask")
    return True


def set_workstream_scope(principal, workstream, entitlement_ids):
    """Make the stream's WALL exactly `entitlement_ids`. Returns
    `(added, removed)` as two frozensets, or `None` if `principal` may
    not, or if any submitted id is outside their own grants.

    A WALL MAY ONLY NAME ENTITLEMENTS THE SETTER HOLDS (spec §6.1) --
    not because holding one would grant anything (it would not; §6.1's
    outer intersection sees to that) but because a wall naming an
    entitlement its owner does not hold is a wall that narrows to the
    empty set and reads as a bug. `held_entitlement_ids`, NOT
    `owned_entitlement_ids`: the editor renders the held set (§15.3
    item 3), and the render half and the gate half must be the same set.

    WRITES THE DIFFERENCE, not the whole set, so the audit trail records
    what changed rather than what was resubmitted -- exactly as
    `tools.rag.labels.set_document_labels` does for its own table.
    """
    if not may_manage_workstream(principal, workstream):
        return None
    wanted = {int(i) for i in entitlement_ids}
    held = held_entitlement_ids(principal)
    if wanted - held:
        return None
    with transaction.atomic():
        current = set(workstream.scope_entitlements.values_list("entitlement_id", flat=True))
        for entitlement_id in sorted(wanted - current):
            WorkstreamScopeEntitlement.objects.create(
                workstream=workstream, entitlement_id=entitlement_id,
                set_by_id=int(principal.key) if principal.kind == "user" else None)
            audit.record(principal, actions.WORKSTREAM_SCOPE_ADDED, target_type="workstream",
                         target_key=workstream.pk, target_label=workstream.name,
                         entitlement=entitlement_id)
        for entitlement_id in sorted(current - wanted):
            workstream.scope_entitlements.filter(entitlement_id=entitlement_id).delete()
            audit.record(principal, actions.WORKSTREAM_SCOPE_REMOVED, target_type="workstream",
                         target_key=workstream.pk, target_label=workstream.name,
                         entitlement=entitlement_id)
    return frozenset(wanted - current), frozenset(current - wanted)


def delete_workstream(principal, workstream) -> str | None:
    """Delete `workstream`, or return a SENTENCE naming why not.

    COUNTS FIRST AND REFUSES BY NAME, which is the same count-then-name
    shape `identity.services.delete_entitlement` already uses for its
    cascade. Both containment foreign keys are `PROTECT` (author decision
    15): a stream delete that silently took seven documents with it would
    be the one destructive gesture on this surface, hidden behind the
    least alarming button.

    "DELETE THEM FIRST", NOT "delete or re-home them first", because
    re-homing is not an action this product has: owner decision 2 forbids
    moving a conversation, §21.2 forbids a document in two streams, and
    §14 offers no re-home route. A refusal that names an action the
    person cannot take is worse than one that names a chore.

    And every row the count names is a row the person reading it can act
    on -- ruling C makes the stream's owner able to read and manage every
    conversation in their stream, including ones a recipient started.

    The pins go with the stream (`CASCADE`) because a pin is pure
    association and its loss destroys nothing.

    `None` MEANS SUCCESS AND ONLY SUCCESS. A permission refusal returns a
    SENTENCE too, rather than the `None` an earlier draft used for both:
    one return value with two opposite meanings is exactly the ambiguity
    this column's own "a predicate stated in the view alone is a predicate
    the second caller does not get" argues against, and a CLI-facing
    caller has no 404 to fall back on. The view still runs its own
    `may_manage_workstream` pre-check and answers 404 before ever
    reaching here, so this sentence is what a shell caller sees.
    """
    if not may_manage_workstream(principal, workstream):
        return "You may not delete this workstream."
    conversations = workstream.conversations.count()
    documents = workstream.documents.count()
    if conversations or documents:
        return (f"This workstream holds {conversations} conversation"
                f"{'' if conversations == 1 else 's'} and {documents} document"
                f"{'' if documents == 1 else 's'}. Delete them first.")
    name, pk = workstream.name, workstream.pk
    with transaction.atomic():
        # The stream's shares go with it, in the SAME transaction --
        # `delete_conversation`'s own ruling, and for the same reason: no
        # `post_delete` receiver, because this repository uses no Django
        # signals anywhere.
        Share.objects.filter(target_type=Share.Target.WORKSTREAM,
                             target_key=str(pk)).delete()
        workstream.delete()
    audit.record(principal, actions.WORKSTREAM_DELETED, target_type="workstream",
                 target_key=pk, target_label=name)
    return None
```

`workstream.documents` is the reverse accessor `tools/rag`'s `Document.workstream` creates in
Task 5. **Before Task 5 lands, `delete_workstream` would raise `AttributeError` on that line** —
so this step's implementation writes it as:

```python
    documents = _contained_document_count(workstream)
```

with, at module scope:

```python
def _contained_document_count(workstream) -> int:
    """How many documents this stream contains.

    THROUGH `getattr`, DELIBERATELY, and this is the one place in this
    module that reaches for a relation another column declares. The
    reverse accessor is created by `tools.rag.models.Document.workstream`
    (a string FK to `"agents.Workstream"`), so it exists on a box with
    the rag app installed and is absent on one without -- and `agents/`
    may not import `tools/` to ask. `0` when it is absent is the honest
    answer: no rag app, no contained documents.
    """
    related = getattr(workstream, "documents", None)
    return related.count() if related is not None else 0
```

- [ ] **Step 5: Run the tests**

Run: `pytest agents/tests/test_workstreams.py -v`
Expected: PASS (21 tests)

Run: `pytest agents/ identity/ -q`
Expected: PASS. `identity/tests/test_audit.py`'s catalogue test picks up the nine new actions
because they are in `AUDIT_ACTIONS`.

- [ ] **Step 6: Commit**

```bash
git add agents/visibility.py agents/tests/test_workstreams.py identity/contracts/actions.py
git commit -m "feat(agents): visible_workstreams, the stream writers, and nine audit actions"
```

---

### Task 4: `agents/workstreams.py` — the cross-column seam, and the wall's entitlement cascade

**Files:**
- Create: `agents/workstreams.py`
- Modify: `agents/apps.py` (one `register_entitlement_cascade` call in `ready()`)
- Test: `agents/tests/test_workstream_seam.py`

**Interfaces:**
- Consumes: `agents.contracts.workstreams.WorkstreamScope` (Task 1); `agents.models.Workstream`,
  `WorkstreamScopeEntitlement` (Task 2); `agents.visibility.visible_workstreams`,
  `may_manage_workstream` (Task 3).
- Produces:
  `agents.workstreams.workstream_scope(principal, workstream_id) -> WorkstreamScope | None`;
  `agents.workstreams.set_upload_placement_default(principal, workstream_id, placement) -> bool`;
  `agents.workstreams.workstream_entitlement_cascade(entitlement_id, *, commit) -> int`;
  `agents.workstreams.panels_for(principal, workstream_id) -> list[dict]`.
  Every `tools/`-side caller in Tasks 5–18 names this module and nothing else under `agents/`
  except `agents.contracts.*` and `agents.entitlements`.

- [ ] **Step 1: Write the failing seam test**

Create `agents/tests/test_workstream_seam.py`:

```python
"""`agents/workstreams.py` — the one module `tools/` and `models/` may
import besides `agents.contracts.*` and `agents.entitlements`."""
from __future__ import annotations

import pytest

from agents.contracts.workstreams import (
    WorkstreamPanel, WorkstreamScope, register_workstream_panel,
)
from agents.models import Share, WorkstreamScopeEntitlement
from agents.tests._helpers import _workstream
from agents.workstreams import (
    panels_for, set_upload_placement_default, workstream_entitlement_cascade, workstream_scope,
)
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import grant, make_entitlement, make_user, posture, user_principal

pytestmark = pytest.mark.django_db


def test_the_scope_carries_the_stream_half_and_no_pins():
    """`pinned_file_ids` is filled by the OTHER column (author decision
    6) — this side always hands over an empty set."""
    user = make_user()
    stream = _workstream(user_principal(user), default_upload_placement="contained")
    with posture("enterprise"):
        scope = workstream_scope(user_principal(user), stream.pk)
    assert isinstance(scope, WorkstreamScope)
    assert scope.workstream_id == stream.pk
    assert scope.default_upload_placement == "contained"
    assert scope.may_upload is True
    assert scope.pinned_file_ids == frozenset()


def test_a_stream_this_principal_may_not_be_in_is_indistinguishable_from_one_that_is_gone():
    """The caller cannot tell the two apart, and answers 404 to both."""
    owner, stranger = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    with posture("enterprise"):
        assert workstream_scope(user_principal(stranger), stream.pk) is None
        assert workstream_scope(user_principal(owner), 999999) is None


def test_a_recipient_gets_a_scope_but_may_not_upload():
    """`may_upload` is False for a share recipient (spec §12.4), so the
    page asks ONE question rather than re-deriving ownership on a
    surface that cannot see the row."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        scope = workstream_scope(user_principal(reader), stream.pk)
    assert scope is not None
    assert scope.may_upload is False


def test_the_wall_is_read_on_an_accounts_box():
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        scope = workstream_scope(user_principal(user), stream.pk)
    assert scope.wall == frozenset({ent.pk})


def test_the_wall_is_empty_on_an_open_box_and_the_table_is_never_read():
    """RULING A (spec §23.A), both halves, and the load-bearing half is
    the SECOND assertion: `workstream_scope` does not read
    `WorkstreamScopeEntitlement` AT ALL on an open box. Global Constraint
    8 says so; without a query assertion the test would pass against an
    implementation that read the table and then discarded the result,
    which is precisely the shape ruling A rejects."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with posture("open"):
        with CaptureQueriesContext(connection) as captured:
            scope = workstream_scope(OPEN_PRINCIPAL, stream.pk)
    assert scope.wall == frozenset()
    assert not [q for q in captured.captured_queries
                if "agents_workstreamscopeentitlement" in q["sql"]]
    # The rows are still there, waiting for the posture to come back.
    assert WorkstreamScopeEntitlement.objects.filter(workstream=stream).count() == 1


def test_switching_the_posture_back_makes_the_same_rows_bind():
    """DORMANT, NOT DELETED, and the direction runs both ways — "a
    switch, not a migration"."""
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    with posture("open"):
        assert workstream_scope(OPEN_PRINCIPAL, stream.pk).wall == frozenset()
    with posture("personal"):
        assert workstream_scope(user_principal(user), stream.pk).wall == frozenset({ent.pk})


def test_setting_the_upload_default_through_the_seam_writes_and_audits():
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        assert set_upload_placement_default(user_principal(user), stream.pk, "contained") is True
    stream.refresh_from_db()
    assert stream.default_upload_placement == "contained"


def test_a_non_owner_may_not_set_the_upload_default_through_the_seam():
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert set_upload_placement_default(user_principal(reader), stream.pk, "contained") is False


def test_the_entitlement_cascade_counts_then_removes_the_wall_rows():
    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    assert workstream_entitlement_cascade(ent.pk, commit=False) == 1
    assert WorkstreamScopeEntitlement.objects.count() == 1
    assert workstream_entitlement_cascade(ent.pk, commit=True) == 1
    assert WorkstreamScopeEntitlement.objects.count() == 0


def test_a_registered_panel_is_resolved_at_render_time(isolated_panel_registry):
    """The provider is a DOTTED PATH resolved with `import_string` at
    render time, never imported by `agents/`."""
    register_workstream_panel(WorkstreamPanel(
        "test.rows", "Rows", "agents.tests.test_workstream_seam._fake_provider",
        "chat/panels/test_rows.html"))
    stream = _workstream()
    with posture("open"):
        panels = panels_for(OPEN_PRINCIPAL, stream.pk)
    assert panels == [{"key": "test.rows", "label": "Rows",
                       "template": "chat/panels/test_rows.html", "ok": True,
                       "data": {"rows": [1, 2]}}]


def test_a_panel_whose_provider_raises_degrades_to_a_named_empty_section(
        isolated_panel_registry, caplog):
    """AUTHOR DECISION 11. Registration in the same commit as the handler
    protects against a MISSING module; it does not protect against a
    provider that raises at render time on a box with a broken store.
    Chrome on a page whose real subject is the stream — the same
    never-500 posture `chat_picker_options` already takes."""
    register_workstream_panel(WorkstreamPanel(
        "test.bad", "Bad", "agents.tests.test_workstream_seam._raising_provider",
        "chat/panels/test_bad.html"))
    stream = _workstream()
    with posture("open"):
        panels = panels_for(OPEN_PRINCIPAL, stream.pk)
    assert panels == [{"key": "test.bad", "label": "Bad",
                       "template": "chat/panels/test_bad.html", "ok": False, "data": {}}]
    assert "test.bad" in caplog.text


def _fake_provider(principal, workstream_id):
    return {"rows": [1, 2]}


def _raising_provider(principal, workstream_id):
    raise RuntimeError("the store is down")
```

Import the `isolated_panel_registry` fixture into this module:
`from agents.contracts.tests._helpers import isolated_panel_registry  # noqa: F401`.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest agents/tests/test_workstream_seam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.workstreams'`

- [ ] **Step 3: Write the seam module**

Create `agents/workstreams.py`:

```python
"""The cross-column seam for workstreams.

MAY BE IMPORTED BY `tools/` AND `models/`. It is the THIRD `agents`
name outside `agents/contracts/` that may be, beside `agents.
entitlements` (already a named seam -- `tools/vision/views.py:824` says
so in those words, and `tools/rag/views.py` imports it too). The closed
three-name set is pinned by `foundation/ops/tests/test_import_law.py::
test_tools_reaches_agents_through_contracts_entitlements_and_workstreams
_and_nothing_else`.

IT IMPORTS NOTHING OF `tools/`. That is the whole point, and it has its
own narrow guard (`::test_agents_workstreams_imports_no_tools_package`)
on top of the broad sweep, because this is the module whose accidental
widening would be least visible in review. When the stream page needs to
show another column's rows, the answer is a `WorkstreamPanel`
registration, never an import.

IT IMPORTS `agents.visibility`, NEVER THE REVERSE. The direction is
one-way, so the two agents-side stream modules cannot cycle when spec
§12 puts `stream_access` here and `share_workstream` /
`workstream_taint_ids` there.
"""
from __future__ import annotations

import logging

from django.utils.module_loading import import_string

from agents.contracts.workstreams import WorkstreamScope, all_workstream_panels
from agents.models import Workstream, WorkstreamScopeEntitlement
from agents.visibility import (
    may_manage_workstream, set_workstream_upload_default, visible_workstreams,
)
from identity.access import accounts_on

logger = logging.getLogger(__name__)


def wall_ids(workstream_id: int) -> frozenset[int]:
    """The stream's wall, or the empty set on an open box.

    RULING A (spec §23.A): when `accounts_on()` is False this returns
    `frozenset()` WITHOUT READING `WorkstreamScopeEntitlement` at all.
    That is what preserves "an open box never runs a permission query"
    and what stops a wall written under `enterprise` from bricking the
    stream after a posture switch -- `ToolEntitlement`,
    `DocumentEntitlement` and the model-set rows all survive a switch to
    `open` with no posture branch in their readers, so "on an open box
    nothing is labelled" is false and a non-empty `required` against an
    empty `held` would drop every labelled tool and refuse every labelled
    model set with no page able to clear it.

    The rows SURVIVE, dormant, and bind again the moment the posture
    returns.
    """
    if not accounts_on():
        return frozenset()
    return frozenset(
        WorkstreamScopeEntitlement.objects.filter(workstream_id=workstream_id)
        .values_list("entitlement_id", flat=True))


# PUBLIC, not `_wall_ids`: `agents/entitlements.py::_wall_for` is a second
# module's worth of callers, and a name with callers outside its own file
# is not private. The leading underscore an earlier draft carried would
# have made every one of those calls read as a boundary violation.


def workstream_scope(principal, workstream_id) -> WorkstreamScope | None:
    """The pure scope value for `workstream_id`, or None when this
    principal may not be in that stream (or it does not exist -- the
    caller cannot tell the two apart, and answers 404 to both).

    `pinned_file_ids` is left EMPTY here and filled by
    `tools/rag/workstreams.py::scope_with_pins`, which is the only
    function that fills it, because the pin table lives in `tools/rag`
    (author decision 6, spec §6.2).
    """
    row = visible_workstreams(principal).filter(pk=workstream_id).first()
    if row is None:
        return None
    return WorkstreamScope(
        workstream_id=row.pk,
        wall=wall_ids(row.pk),
        default_upload_placement=row.default_upload_placement,
        may_upload=may_manage_workstream(principal, row),
    )


def set_upload_placement_default(principal, workstream_id, placement: str) -> bool:
    """Write `Workstream.default_upload_placement` from another column.

    A SEAM CALL, because the column is on an `agents` row and its caller
    (`tools.rag.views.document_upload`, spec §9.1) is `tools/rag` code.
    `""` clears it back to "ask every time". The predicate and the audit
    row live with the writer in `agents/visibility.py`; this is the door,
    not a second copy of the rule.
    """
    row = visible_workstreams(principal).filter(pk=workstream_id).first()
    if row is None:
        return False
    return set_workstream_upload_default(principal, row, placement)


def workstream_entitlement_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's THIRD answer to "an entitlement is being deleted" --
    the stream's wall rows.

    Registered from `agents/apps.py::ready()` as a DOTTED-PATH STRING, so
    `identity/` runs it without importing `agents/` (import-law rule 4).
    `commit=False` COUNTS, so the delete confirmation names the number
    first; `commit=True` removes and returns the same count.

    WS-2 extends this handler to the two taint tables, widens the
    registration's LABEL to match, and gives it the
    `WORKSTREAM_UNTAINTED`/`CONVERSATION_UNTAINTED` audit rows -- see
    spec §8.4. In WS-1 the wall is the only thing there is to remove, and
    the label says so.
    """
    rows = WorkstreamScopeEntitlement.objects.filter(entitlement_id=entitlement_id)
    count = rows.count()
    if commit:
        rows.delete()
    return count


def panels_for(principal, workstream_id) -> list[dict]:
    """Every registered panel's data for this stream, in registration
    order.

    NEVER RAISES (author decision 11). `import_string` failing is what
    the same-commit registration rule already prevents; a provider that
    RAISES at render time -- a store that is down, a migration half
    applied -- is a different failure, and the stream page must survive
    it. A failed panel renders as its own heading with one honest
    sentence, which is the posture `agents.chat.pickers.
    chat_picker_options` already takes for the model picker.
    """
    out = []
    for spec in all_workstream_panels():
        try:
            data = import_string(spec.provider)(principal, workstream_id)
            out.append({"key": spec.key, "label": spec.label,
                        "template": spec.template, "ok": True, "data": data})
        except Exception:  # noqa: BLE001 -- one broken panel, not a broken page
            logger.exception("workstreams: panel %r could not be rendered", spec.key)
            out.append({"key": spec.key, "label": spec.label,
                        "template": spec.template, "ok": False, "data": {}})
    return out
```

- [ ] **Step 4: Register the cascade**

In `agents/apps.py::AgentsConfig.ready()`, after the `agents.runnable_labels` registration:

```python
        # This column's THIRD answer to "an entitlement is being
        # deleted": a stream's wall rows, and (in WS-2) its taint tags.
        # Same dotted-path mechanism, same reason.
        register_entitlement_cascade(EntitlementCascade(
            key="agents.workstream_entitlements",
            # "Workstream scopes" ONLY, for now: this label is what
            # `identity.services.delete_entitlement`'s confirmation prints
            # to an operator, and in WS-1 there are no taint tables for it
            # to be describing. Task 16 widens it to "Workstream scopes
            # and taint tags" in the commit that gives the handler those
            # rows to remove.
            label="Workstream scopes",
            handler="agents.workstreams.workstream_entitlement_cascade",
        ))
```

- [ ] **Step 5: Run the tests**

Run: `pytest agents/tests/test_workstream_seam.py agents/tests/test_apps.py -v`
Expected: PASS. `test_apps.py`'s registration assertions see the third cascade.

Run: `pytest foundation/ops/tests/test_import_law.py -k workstreams_imports_no_tools -v`
Expected: PASS — no longer skipped. `agents/workstreams.py` exists and imports nothing of `tools/`.

Run: `pytest identity/tests/ -q`
Expected: PASS — `delete_entitlement` now runs three cascades and its confirmation names three
labels.

- [ ] **Step 6: Update the column README**

In `agents/README.md`'s Workstreams section, add: the seam module, exactly what may import it, its
whole public surface, the one-way arrow to `agents/visibility.py`, and the panel registry with
the same-commit registration rule.

- [ ] **Step 7: Commit**

```bash
git add agents/workstreams.py agents/apps.py agents/tests/test_workstream_seam.py agents/README.md
git commit -m "feat(agents): the workstream cross-column seam and the wall's entitlement cascade"
```

---

### Task 5: `Document.workstream`, `Document.origin`, `WorkstreamPin`, and migration `rag/0016`

**Files:**
- Modify: `tools/rag/models.py` (`Document` gains two columns and its **first `Meta`**; a new model at the end)
- Create: `tools/rag/migrations/0016_document_workstream_and_pins.py`
- Modify: `tools/rag/tests/_helpers.py` (the `_workstream` twin)
- Test: `tools/rag/tests/test_workstream_models.py`

**Interfaces:**
- Consumes: `agents.Workstream` as the string `"agents.Workstream"` — **never an import** (Task 2).
- Produces: `tools.rag.models.Document.workstream` (nullable, `PROTECT`, `related_name="documents"`),
  `Document.Origin.UPLOAD == "upload"` / `Document.Origin.NOTES == "notes"`, `Document.origin`
  (default `UPLOAD`); `tools.rag.models.WorkstreamPin` with `workstream`, `document`, `pinned_by`,
  `pinned_at`, `related_name="pins"` / `"workstream_pins"`; `_workstream(...)` in
  `tools/rag/tests/_helpers.py`.

- [ ] **Step 1: Write the failing model test**

Create `tools/rag/tests/test_workstream_models.py`:

```python
"""Containment and pinning, at the table."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from tools.rag.models import Document, WorkstreamPin
from tools.rag.tests._helpers import _workstream, make_document

pytestmark = pytest.mark.django_db


def test_every_existing_document_is_universal_and_an_upload():
    """No data migration anywhere, and that is the load-bearing property
    of every column added here: `workstream` null means "the universal
    library", which every existing document is; `origin` defaults to
    `upload`, which every existing document is."""
    doc = make_document()
    assert doc.workstream_id is None
    assert doc.origin == Document.Origin.UPLOAD


def test_a_contained_document_names_its_stream_by_string_fk():
    stream = _workstream()
    doc = make_document(workstream=stream)
    assert doc.workstream_id == stream.pk
    assert list(stream.documents.all()) == [doc]


def test_deleting_a_stream_that_still_contains_a_document_is_refused():
    """PROTECT on both containment FKs (author decision 15)."""
    stream = _workstream()
    make_document(workstream=stream)
    with pytest.raises(ProtectedError):
        stream.delete()


def test_a_pin_is_unique_per_stream_and_document():
    stream = _workstream()
    doc = make_document()
    WorkstreamPin.objects.create(workstream=stream, document=doc)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            WorkstreamPin.objects.create(workstream=stream, document=doc)


def test_a_pin_dies_with_its_stream_and_with_its_document():
    """CASCADE on both halves, because a pin is pure association and its
    loss destroys nothing."""
    stream, other = _workstream(), _workstream()
    doc, doc2 = make_document(), make_document()
    WorkstreamPin.objects.create(workstream=stream, document=doc)
    WorkstreamPin.objects.create(workstream=other, document=doc2)
    stream.delete()
    assert WorkstreamPin.objects.count() == 1
    doc2.delete()
    assert WorkstreamPin.objects.count() == 0


def test_a_universal_document_may_be_pinned_into_two_streams():
    """One row, one home, and a pin is an ASSOCIATION, not a copy —
    §21.2's own answer to "it belongs to both"."""
    one, two = _workstream(), _workstream()
    doc = make_document()
    WorkstreamPin.objects.create(workstream=one, document=doc)
    WorkstreamPin.objects.create(workstream=two, document=doc)
    assert doc.workstream_pins.count() == 2


def test_origin_is_its_own_column_not_a_method_value_inside_extraction():
    """AUTHOR DECISION 12. `extraction` snapshots WHAT PRODUCED THE TEXT;
    `origin` records WHAT PUT THE ROW IN THE LIBRARY. A note's
    `extraction` is still written, with `method="distillation"`, which
    `extraction_summary` already degrades to "Processed"."""
    doc = make_document(origin=Document.Origin.NOTES,
                        extraction={"method": "distillation", "produced_at": "2026-09-03"})
    assert doc.origin == "notes"
    assert doc.extraction["method"] == "distillation"
    assert "Processed" in doc.extraction_summary
```

Add to `tools/rag/tests/_helpers.py`:

```python
def _workstream(**overrides):
    """A `Workstream` row for a `tools/rag` test that needs one.

    THE `tools/rag` TWIN of `agents/tests/_helpers.py::_workstream` (spec
    §17.9). Duplicated per column, never imported across, exactly as
    `isolated_tool_registry` is and for the same reason -- consolidating
    ACROSS apps would make one app's test scaffolding load-bearing for
    another's. Reached through `apps.get_model`, so this module still
    imports nothing of `agents/` at module scope.
    """
    from django.apps import apps

    model = apps.get_model("agents", "Workstream")
    fields = {"name": f"stream-{next(_names)}"}
    fields.update(overrides)
    return model.objects.create(**fields)
```

(If `_names` does not exist in this module, add it beside the existing counters.)

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tools/rag/tests/test_workstream_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'WorkstreamPin' from 'tools.rag.models'`

- [ ] **Step 3: Give `Document` its two columns and its first `Meta`**

In `tools/rag/models.py`, inside `class Document`, after `extraction`:

```python
    # NEW. CONTAINMENT (owner decision 4). Null = the universal library,
    # which is every existing row and needs no back-fill. Set = this
    # document exists ONLY in that stream's corpus: absent from
    # `readable_documents`' default, from the library page's member
    # listing, from Ask and Search, and from every other stream,
    # REGARDLESS of entitlements (spec §8.1). A STRING reference, so
    # `tools/rag` never imports `agents.models` -- the same mechanism
    # `DocumentEntitlement.entitlement` uses for `identity.Entitlement`.
    workstream = models.ForeignKey("agents.Workstream", null=True, blank=True,
                                   on_delete=models.PROTECT,
                                   related_name="documents")

    class Origin(models.TextChoices):
        UPLOAD = "upload", "Uploaded"
        NOTES = "notes", "Consolidated notes"

    # NEW. What PUT this row here -- distinct from `extraction` (what
    # produced its TEXT) and `media_type` (what the source file was).
    # Every existing row is an upload, which is why the default is
    # `UPLOAD` and no data migration is needed (author decision 12).
    origin = models.CharField(max_length=16, choices=Origin.choices,
                              default=Origin.UPLOAD)

    class Meta:
        # THIS MODEL'S FIRST `Meta` EVER. It has had none since ADR 0005,
        # so this migration carries `AlterModelOptions` plus `AddIndex`
        # rather than only `AddField` -- named in spec §18 because a
        # reader of the migration would otherwise wonder where the
        # options operation came from. No `ordering`: nothing about the
        # existing library listing changes, and adding a default order
        # here would silently re-sort four surfaces.
        indexes = [
            models.Index(fields=["workstream"], name="rag_document_ws"),
        ]
```

Place the `Meta` immediately before `def __str__`. **Do not add `ordering`** — the library page
and the four other readers order explicitly today, and a default would change all of them.

- [ ] **Step 4: Add `WorkstreamPin` at the end of `tools/rag/models.py`**

```python
class WorkstreamPin(models.Model):
    """A UNIVERSAL document associated into a stream's working set (owner
    decision 4). An association, never a copy and never a move: the
    document stays universal, stays in the library, and stays readable
    everywhere its labels already allowed.

    IN `tools/rag`, NOT IN `agents`, because the `document` half is a real
    ForeignKey with real referential integrity and the `workstream` half
    is a string reference -- and the column that owns the FK owns the
    join table. The reverse (`agents.WorkstreamPin` with a by-value
    `document_id`) would leave a dangling pin behind every document
    delete, which `delete_document` would then have to clean from a
    column it may not import.

    A CONTAINED DOCUMENT MAY NEVER BE PINNED -- not into another stream,
    and not into its own. That rule is NOT a `CheckConstraint`: the
    condition lives on the joined `Document` row and Postgres does not
    accept a check constraint that references another table. It is
    enforced in exactly one place, `tools/rag/workstreams.py::
    pin_document`, which refuses by name, and pinned by a test that tries
    it (author decision 13). Recorded here rather than left as an
    absence, so a reader reaching for the constraint later finds the
    reason instead of a failing migration.
    """

    workstream = models.ForeignKey("agents.Workstream", on_delete=models.CASCADE,
                                   related_name="pins")
    document = models.ForeignKey(Document, on_delete=models.CASCADE,
                                 related_name="workstream_pins")
    # Provenance, on the same terms as `WorkstreamScopeEntitlement.
    # set_by`: written by `pin_document`, read by nothing this phase
    # ships, and mirroring the sibling tables rather than inventing a
    # shape.
    pinned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    pinned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pinned_at"]
        constraints = [models.UniqueConstraint(fields=["workstream", "document"],
                                               name="uniq_workstream_pin")]
        indexes = [models.Index(fields=["document"], name="rag_wspin_document")]
```

- [ ] **Step 5: Generate the migration and pin its dependency**

Run: `python manage.py makemigrations rag --name document_workstream_and_pins`
Expected: `tools/rag/migrations/0016_document_workstream_and_pins.py`.

Confirm and, if `makemigrations` did not infer them, write the dependencies:

```python
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("agents", "0006_workstream"),
        ("rag", "0015_documententitlement"),
    ]
```

**This is the repository's first `tools/` → `agents/` migration dependency.** IA-2 introduced
cross-app dependencies (`rag` → `identity`, `agents` → `identity`, `inference` → `identity`), all
of them pointing at the base column; this one points sideways, in the direction the import law
already permits. The app labels are `agents` and `rag` (from `AgentsConfig` and `RagConfig`), not
the package paths.

Operations must be exactly: `AlterModelOptions(document)`, `AddField(document.workstream)`,
`AddField(document.origin)`, `AddIndex(rag_document_ws)`, `CreateModel(WorkstreamPin)`. No data
migration.

Run: `python manage.py makemigrations --check --dry-run`
Expected: exit 0.

- [ ] **Step 6: Exercise `PROTECT` in both directions against a real database**

Add to `tools/rag/tests/test_workstream_models.py`:

```python
def test_an_emptied_stream_deletes():
    """The other half of the `PROTECT` gate spec §18 requires exercised:
    a stream with a conversation or a document refuses; an emptied one
    deletes."""
    stream = _workstream()
    doc = make_document(workstream=stream)
    doc.delete()
    stream.delete()          # no ProtectedError
```

- [ ] **Step 7: Run the tests**

Run: `pytest tools/rag/tests/test_workstream_models.py -v`
Expected: PASS (9 tests)

Run: `pytest tools/rag/ -q`
Expected: PASS — no existing behaviour changed. In particular `test_models.py`'s
`extraction_summary` cases still pass, because `origin` is a new column nothing reads yet.

- [ ] **Step 8: Update the column README**

In `tools/rag/README.md`, add a **Workstreams** section: containment versus pinning, one row one
home, `origin` and why it is not a `method` value, and the note that a contained document may
never be pinned and where that is enforced.

- [ ] **Step 9: Commit**

```bash
git add tools/rag/models.py tools/rag/migrations/0016_document_workstream_and_pins.py \
        tools/rag/tests/test_workstream_models.py tools/rag/tests/_helpers.py tools/rag/README.md
git commit -m "feat(rag): document containment, origin, and the workstream pin table"
```

---

### Task 6: containment at the four ORM readers

**Files:**
- Modify: `tools/rag/access.py` (`DocumentVisibility` gains a field; `permits`, `readable_documents`, `document_visibility` gain a parameter)
- Modify: `tools/rag/views.py` (`document_file` and `document_transcript` resolve the stream first)
- Test: `tools/rag/tests/test_access_documents.py` (append), `tools/rag/tests/test_containment_routes.py` (new)

**Interfaces:**
- Consumes: `agents.contracts.workstreams.WorkstreamScope` (Task 1);
  `agents.workstreams.workstream_scope` (Task 4); `Document.workstream` (Task 5).
- Produces: `DocumentVisibility(unrestricted, entitlement_ids, unlabelled_allowed,
  stream: WorkstreamScope | None = None)`; `readable_documents(principal, *, workstream_id=None)`;
  `DocumentVisibility.permits(document, *, workstream_id=None)`;
  `document_visibility(principal, *, settings_row=None, stream=None)`.

- [ ] **Step 1: Write the failing access test**

Append to `tools/rag/tests/test_access_documents.py`:

```python
def test_readable_documents_excludes_contained_rows_by_default():
    """`None` — every existing caller, unedited — means the universal
    library exactly as today, which is the SAFE default: a caller that
    forgets the parameter sees no contained document rather than all of
    them."""
    stream = _workstream()
    universal = make_document()
    contained = make_document(workstream=stream)
    with posture("open"):
        keys = set(readable_documents(OPEN_PRINCIPAL).values_list("pk", flat=True))
    assert universal.pk in keys
    assert contained.pk not in keys


def test_readable_documents_admits_this_streams_contained_rows_when_asked():
    """CONTAINMENT IS STREAM-AWARE, NOT ABSOLUTE (spec §8.1, M1). An
    earlier draft filtered contained documents out unconditionally, which
    would have made every contained document — notes included — 404 at
    `rag-document-file` for its own stream's members."""
    stream, other = _workstream(), _workstream()
    here = make_document(workstream=stream)
    elsewhere = make_document(workstream=other)
    with posture("open"):
        keys = set(readable_documents(OPEN_PRINCIPAL, workstream_id=stream.pk)
                   .values_list("pk", flat=True))
    assert here.pk in keys
    assert elsewhere.pk not in keys


def test_containment_is_a_corpus_rule_not_an_entitlement_rule():
    """The clause applies in the OPEN posture and to an administrator
    with `admin_sees_content` on, exactly as it applies to everybody
    else — which is why it sits outside the `unrestricted` branch."""
    admin = make_admin()
    stream = _workstream()
    contained = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=True):
        keys = set(readable_documents(user_principal(admin)).values_list("pk", flat=True))
    assert contained.pk not in keys


def test_permits_and_readable_documents_agree_about_containment():
    """`permits` is the IN-MEMORY MIRROR of `readable_documents`' filter
    and its docstring says its whole purpose is that `DocumentsListView`
    must not "show a link `readable_documents`' own route would 404 on".
    Changing one and not the other reintroduces precisely the failure it
    was written to prevent (spec §8.1, §17.2 cell 6)."""
    stream, other = _workstream(), _workstream()
    here = make_document(workstream=stream)
    elsewhere = make_document(workstream=other)
    universal = make_document()
    with posture("open"):
        v = document_visibility(OPEN_PRINCIPAL)
        for doc in (here, elsewhere, universal):
            in_orm = readable_documents(OPEN_PRINCIPAL).filter(pk=doc.pk).exists()
            assert v.permits(doc) is in_orm, doc.pk
            in_stream_orm = readable_documents(
                OPEN_PRINCIPAL, workstream_id=stream.pk).filter(pk=doc.pk).exists()
            assert v.permits(doc, workstream_id=stream.pk) is in_stream_orm, doc.pk


def test_listable_documents_still_lists_every_row_for_an_administrator():
    """An administrator lists every document ROW including contained
    ones, marked with their stream — pruning and re-ingesting the library
    is administration, and a document an administrator cannot see the
    existence of is a document nobody can clean up. Listing a row is not
    opening it."""
    admin = make_admin()
    stream = _workstream()
    contained = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=False):
        keys = set(listable_documents(user_principal(admin)).values_list("pk", flat=True))
    assert contained.pk in keys
```

Create `tools/rag/tests/test_containment_routes.py`:

```python
"""Ruling G: containment fences the CORPUS, not the bytes."""
from __future__ import annotations

import pytest
from django.urls import reverse

from tools.rag.tests._helpers import _workstream, make_document
from identity.access import owner_fields
from identity.testing import make_admin, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def test_a_contained_document_opens_from_inside_its_stream(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    doc = make_document(workstream=stream)
    with posture("enterprise"):
        sign_in(client, user)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 200


def test_the_same_url_404s_for_a_signed_in_account_outside_the_stream(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 404
    assert "Traceback" not in response.content.decode()


def test_an_administrator_with_the_content_setting_off_gets_404(client):
    """A LISTED ROW IS NOT A KEY TO THE BYTES — the rule
    `rag-document-file` already carries for every other document."""
    owner, admin = make_user(), make_admin()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=False):
        sign_in(client, admin)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 404


def test_an_administrator_with_the_content_setting_on_gets_200(client):
    """RULING G (spec §23.G). `visible_workstreams`' first branch admits
    `sees_all_content` to every stream, so `workstream_scope` returns a
    scope rather than `None` and `readable_documents` takes its
    `unrestricted` branch. Containment fences the CORPUS, not the bytes —
    and the corpus half still holds for this same administrator, which
    `test_workstream_corpus.py` cell 4 asserts."""
    owner, admin = make_user(), make_admin()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise", admin_sees_content=True):
        sign_in(client, admin)
        response = client.get(reverse("rag-document-file", args=[doc.pk]))
    assert response.status_code == 200


def test_the_transcript_route_answers_the_same_pair(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document(workstream=stream)
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("rag-document-transcript", args=[doc.pk]))
    assert response.status_code == 404
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tools/rag/tests/test_access_documents.py tools/rag/tests/test_containment_routes.py -v`
Expected: FAIL — `readable_documents() got an unexpected keyword argument 'workstream_id'`

- [ ] **Step 3: Give `tools/rag/access.py` its containment leg**

```python
@dataclass(frozen=True)
class DocumentVisibility:
    """... (existing docstring, plus:)

    `stream` is NEW. `None` = a LOOSE turn or a non-stream surface:
    contained documents are excluded and nothing else changes. A
    `WorkstreamScope` = a stream turn: its wall narrows the universal leg
    and its contained/pinned legs are OR-ed in (spec §6.1).

    DEFAULTING TO `None` IS WHAT KEEPS EVERY EXISTING CALL SITE CORRECT,
    exactly as `UNRESTRICTED_TOOL_ACCESS` did in IA-2: `SearchView`,
    `AskView`, the two runners and every test that builds a visibility by
    hand keep working and keep meaning "the universal library", and the
    compiler never tells you about the ones you forgot -- but the
    behaviour they get is the SAFE one, because `None` EXCLUDES contained
    documents rather than including them.
    """

    unrestricted: bool
    entitlement_ids: frozenset[int]
    unlabelled_allowed: bool
    stream: "WorkstreamScope | None" = None
```

with `from agents.contracts.workstreams import WorkstreamScope` at module scope (a rule-1 pure
leaf, in the permitted direction).

`permits` gains the same parameter and the same leg, ahead of the entitlement question:

```python
    def permits(self, document, *, workstream_id=None) -> bool:
        """... (existing docstring, plus:)

        `workstream_id` IS THE CONTAINMENT HALF, and it is not optional
        for correctness: this method is the in-memory mirror of
        `readable_documents`' own filter, and a mirror that answers a
        different question about containment is the exact failure it was
        written to prevent. `None` means the universal library, the same
        safe default `readable_documents` takes.

        CONTAINMENT IS CHECKED FIRST, and outside the `unrestricted`
        branch, because it is a CORPUS rule and not an entitlement rule
        (spec §13): it binds on an open box and it binds for an
        administrator with the content setting on.
        """
        if document.workstream_id is not None \
                and document.workstream_id != workstream_id:
            return False
        if self.unrestricted:
            return True
        labels = list(document.entitlement_labels.all())
        if not labels:
            return self.unlabelled_allowed
        return any(label.entitlement_id in self.entitlement_ids for label in labels)
```

`readable_documents`:

```python
def readable_documents(principal, *, workstream_id=None):
    """Documents whose CONTENT this principal may reach -- the bytes, the
    transcript, and the chunks retrieval may return.

    OR-MATCH on the labels; the library posture decides the unlabelled
    ones. `.distinct()` because a document carrying two labels the
    principal holds would otherwise be returned twice by the join.

    `workstream_id` IS CONTAINMENT (spec §8.1). `None` -- every existing
    caller, unedited -- means the universal library exactly as today,
    which is the SAFE default: a caller that forgets the parameter sees
    no contained document rather than all of them. A value admits THAT
    stream's contained rows and no other stream's.

    THE CONTAINMENT CLAUSE IS OUTSIDE THE `unrestricted` BRANCH, so it
    binds in the open posture and for an administrator with
    `admin_sees_content` on -- it is a corpus rule, not a permission
    rule. What it does NOT do is fence the BYTES from an administrator
    who is admitted to the stream: `rag-document-file` resolves the
    stream first (ruling G), and `visible_workstreams` admits
    `sees_all_content` to every stream, so that administrator arrives
    here with the right `workstream_id` and gets a 200.
    """
    contained = Q(workstream__isnull=True) | Q(workstream_id=workstream_id)
    v = document_visibility(principal)
    if v.unrestricted:
        return Document.objects.filter(contained)
    labelled = Q(entitlement_labels__entitlement_id__in=v.entitlement_ids)
    if v.unlabelled_allowed:
        return Document.objects.filter(
            contained & (labelled | Q(entitlement_labels__isnull=True))).distinct()
    return Document.objects.filter(contained & labelled).distinct()
```

`document_visibility` gains the pass-through:

```python
def document_visibility(principal, *, settings_row=None, stream=None) -> DocumentVisibility:
    """... (existing docstring, plus:)

    `stream` is a `WorkstreamScope` for a turn inside a workstream, and
    `None` everywhere else. It is carried, never derived: the value is
    built once per turn (`agents/workstreams.py`, filled by
    `tools/rag/workstreams.py::scope_with_pins`) and threaded down, which
    is the drift the single filter point exists to prevent.
    """
    if sees_all_content(principal, settings_row=settings_row):
        return DocumentVisibility(True, frozenset(), True, stream)
    return DocumentVisibility(
        unrestricted=False,
        entitlement_ids=held_entitlement_ids(principal, settings_row=settings_row),
        unlabelled_allowed=may_see_unlabelled(principal, settings_row=settings_row),
        stream=stream,
    )
```

`listable_documents` gains **nothing**: its `is_admin` branch is `Document.objects.all()`
unconditionally, and its other branch inherits `readable_documents`' default.

- [ ] **Step 4: Make the two content routes stream-aware**

In `tools/rag/views.py`, replace the single `get_object_or_404` in **both** `document_file` and
`document_transcript` with the two-resolution shape:

```python
    # CLASS L: content, not a row. 404 outside `readable_documents` --
    # INCLUDING for an administrator with the content setting off: the row
    # is theirs to manage, the bytes are not theirs to read.
    #
    # TWO RESOLUTIONS, BOTH 404-SHAPED (spec §8.1). A CONTAINED document
    # is reachable only from inside its stream, so the stream is resolved
    # FIRST -- `agents.workstreams.workstream_scope` answers `None` for a
    # stream this principal may not be in, and the caller cannot tell
    # that apart from "no such stream". Then the ordinary label gate,
    # asked with the stream so the row is admitted rather than filtered
    # out by containment's safe default.
    #
    # RULING G: an administrator with `admin_sees_content` on is admitted
    # to every stream by `visible_workstreams`' first branch, so this
    # pair produces a 200 for them -- containment fences the CORPUS, not
    # the bytes, and §17.2 cell 4 is where the corpus half is asserted.
    from agents.workstreams import workstream_scope

    principal = principal_for_request(request)
    row = Document.objects.filter(pk=doc_id).values_list("workstream_id", flat=True).first()
    if row is not None and workstream_scope(principal, row) is None:
        raise Http404(f"No document {doc_id}.")
    document = get_object_or_404(
        readable_documents(principal, workstream_id=row), pk=doc_id)
```

**`Document.objects` appears here, in `tools/rag/views.py`, and the column-boundary gate forbids
it.** So the one-column lookup goes through a named helper in `tools/rag/access.py` instead:

```python
def containing_workstream_id(doc_id) -> int | None:
    """The stream a document is contained in, or `None` for a universal
    one -- and `None` for a document that does not exist, which the
    caller's own 404 then covers.

    HERE, NOT IN THE VIEW, because `foundation/ops/tests/
    test_column_boundaries.py::test_rag_views_reads_documents_through_
    the_access_module` forbids `tools/rag/views.py` from touching
    `Document.objects` at all, for the reason that test's own docstring
    gives: counts are a reading surface too. This is a one-column
    `values_list`, not a visibility question -- but it is a `Document`
    question, and the gate is flat.
    """
    return Document.objects.filter(pk=doc_id).values_list(
        "workstream_id", flat=True).first()
```

and the view calls `containing_workstream_id(doc_id)`.

- [ ] **Step 5: Run the tests**

Run: `pytest tools/rag/tests/test_access_documents.py tools/rag/tests/test_containment_routes.py -v`
Expected: PASS

Run: `pytest tools/rag/ foundation/ops/tests/test_column_boundaries.py -q`
Expected: PASS — every existing `readable_documents(principal)` call site keeps its meaning,
because the new parameter defaults to `None`.

- [ ] **Step 6: Commit**

```bash
git add tools/rag/access.py tools/rag/views.py tools/rag/tests/test_access_documents.py \
        tools/rag/tests/test_containment_routes.py
git commit -m "feat(rag): stream-aware containment at the four ORM readers and the two content routes"
```

---

### Task 7: `tools/rag/workstreams.py` — pins, the stream's corpus, and the registered panel

**Files:**
- Create: `tools/rag/workstreams.py`
- Modify: `tools/rag/apps.py` (one `register_workstream_panel` call in `ready()`)
- Modify: `foundation/ops/tests/test_column_boundaries.py` (a `WorkstreamPin.objects` gate)
- Modify: `foundation/ops/tests/test_import_law.py` (extend the anti-vacuous pin to three names)
- Modify: `identity/contracts/actions.py` (two new constants)
- Test: `tools/rag/tests/test_workstream_pins.py`

**Interfaces:**
- Consumes: `agents.contracts.workstreams.WorkstreamScope` (Task 1);
  `agents.workstreams.workstream_scope` (Task 4); `tools.rag.models.WorkstreamPin`,
  `Document.origin` (Task 5).
- Produces, all in `tools/rag/workstreams.py`:
  `MAX_PINS_PER_STREAM = 200`;
  `scope_with_pins(scope: WorkstreamScope) -> WorkstreamScope`;
  `pin_document(principal, scope, document) -> str | None`;
  `unpin_document(principal, scope, pin_id) -> str | None`;
  `stream_documents(principal, scope) -> QuerySet[Document]`;
  `panel(principal, workstream_id) -> dict` with keys `contained`, `pinned`, `pinnable`, `capped`;
  `set_document_workstream(actor, document, workstream_id)` — written in Task 8, declared here as
  the module's own responsibility so no later task has to move it.
  In `identity/contracts/actions.py`: `DOCUMENT_PINNED = "workstream.document_pinned"`,
  `DOCUMENT_UNPINNED = "workstream.document_unpinned"`.

- [ ] **Step 1: Write the failing pin test**

Create `tools/rag/tests/test_workstream_pins.py`:

```python
"""Pinning — an association, capped, and refused by name rather than
404-shaped, because this is a button on a page listing rows the caller
can already see."""
from __future__ import annotations

import pytest

from agents.workstreams import workstream_scope
from tools.rag.models import WorkstreamPin
from tools.rag.tests._helpers import _workstream, make_document
from tools.rag.workstreams import (
    MAX_PINS_PER_STREAM, pin_document, scope_with_pins, stream_documents, unpin_document,
)
from identity.access import owner_fields
from identity.testing import grant, make_entitlement, make_user, posture, user_principal

pytestmark = pytest.mark.django_db


def test_pinning_a_universal_document_succeeds_and_fills_the_scopes_pin_set():
    stream = _workstream()
    doc = make_document()
    with posture("open"):
        scope = _scope_open(stream)
        assert pin_document(_open(), scope, doc) is None
        filled = scope_with_pins(scope)
    assert filled.pinned_file_ids == frozenset({doc.pk})
    # And the stream half is untouched — same frozen value, one field on.
    assert filled.workstream_id == scope.workstream_id
    assert filled.wall == scope.wall
    assert filled.default_upload_placement == scope.default_upload_placement
    assert filled.may_upload == scope.may_upload


def test_a_contained_document_may_not_be_pinned_anywhere_including_its_own_stream():
    """AUTHOR DECISION 13, asserted rather than left to a constraint that
    Postgres will not accept."""
    stream, other = _workstream(), _workstream()
    contained = make_document(workstream=stream)
    with posture("open"):
        message = pin_document(_open(), _scope_open(other), contained)
        assert message is not None and "already lives in" in message
        message = pin_document(_open(), _scope_open(stream), contained)
        assert message is not None
    assert WorkstreamPin.objects.count() == 0


def test_the_pin_cap_is_refused_with_an_honest_message_naming_it():
    """A pin set becomes an `ANY` array in every query the stream runs,
    so it is unbounded work per turn. A cap, not a paginator (author
    decision 7)."""
    stream = _workstream()
    with posture("open"):
        scope = _scope_open(stream)
        for _ in range(MAX_PINS_PER_STREAM):
            assert pin_document(_open(), scope, make_document()) is None
        message = pin_document(_open(), scope, make_document())
    assert message is not None
    assert str(MAX_PINS_PER_STREAM) in message


def test_pinning_a_document_the_caller_may_not_read_is_refused_by_name():
    """The third refusal is a 404 at the ROUTE and is repeated here
    because this function is also the CLI-facing one, and a predicate
    stated in the view alone is a predicate the second caller does not
    get."""
    user = make_user()
    secret = make_entitlement()
    doc = make_document()
    doc.entitlement_labels.create(entitlement=secret)
    # OWNER COLUMNS, or `visible_workstreams` excludes the row for an
    # ordinary user under `enterprise` (`owned_rows_q` compares
    # `owner_kind`/`owner_key` against the principal, and the helper
    # leaves both blank), `workstream_scope` answers `None`, and
    # `pin_document` dereferences `scope.workstream_id` on it.
    stream = _workstream(name="s", **owner_fields(user_principal(user)))
    with posture("enterprise"):
        scope = workstream_scope(user_principal(user), stream.pk)
        message = pin_document(user_principal(user), scope, doc)
    assert message is not None
    assert "may not" in message.lower() or "cannot" in message.lower()


def test_unpinning_is_the_same_module_keyed_on_the_pin_id():
    stream = _workstream()
    doc = make_document()
    with posture("open"):
        scope = _scope_open(stream)
        pin_document(_open(), scope, doc)
        pin = WorkstreamPin.objects.get()
        assert unpin_document(_open(), scope, pin.pk) is None
    assert WorkstreamPin.objects.count() == 0


def test_unpinning_a_pin_belonging_to_another_stream_is_refused():
    """The same IDOR the conversation share revoke already guards
    against: an owner of stream A must not unpin from stream B by
    guessing a sequential id."""
    a, b = _workstream(), _workstream()
    doc = make_document()
    with posture("open"):
        pin_document(_open(), _scope_open(a), doc)
        pin = WorkstreamPin.objects.get()
        assert unpin_document(_open(), _scope_open(b), pin.pk) is not None
    assert WorkstreamPin.objects.count() == 1


def test_stream_documents_is_the_orm_mirror_of_the_corpus_formula():
    """Contained here, plus pinned, plus universal — narrowed by the
    reader's own grants and by the wall. Exercised in full by
    `test_workstream_corpus.py`; this pins the shape."""
    stream, other = _workstream(), _workstream()
    here = make_document(workstream=stream)
    elsewhere = make_document(workstream=other)
    universal = make_document()
    pinned = make_document()
    with posture("open"):
        scope = _scope_open(stream)
        pin_document(_open(), scope, pinned)
        rows = set(stream_documents(_open(), scope_with_pins(scope))
                   .values_list("pk", flat=True))
    assert here.pk in rows
    assert universal.pk in rows
    assert pinned.pk in rows
    assert elsewhere.pk not in rows


@pytest.mark.parametrize("pin_count", [1, 25])
def test_the_panel_is_flat_in_the_number_of_pins(pin_count, django_assert_num_queries):
    """GLOBAL CONSTRAINT 6, on the list this task adds. Asserted as an
    EQUALITY between 1 pin and 25 rather than a fixed number, so the
    absolute count can move with the platform while the per-row cost
    stays zero -- which is the property the constraint actually needs."""
    from django.test.utils import CaptureQueriesContext
    from django.db import connection

    from tools.rag.workstreams import panel

    stream = _workstream()
    with posture("open"):
        scope = _scope_open(stream)
        for _ in range(pin_count):
            pin_document(_open(), scope, make_document())
        with CaptureQueriesContext(connection) as captured:
            panel(_open(), stream.pk)
    _PANEL_QUERY_COUNTS[pin_count] = len(captured.captured_queries)
    if len(_PANEL_QUERY_COUNTS) == 2:
        assert _PANEL_QUERY_COUNTS[1] == _PANEL_QUERY_COUNTS[25], _PANEL_QUERY_COUNTS


# Shared across the two parametrized runs above, so the assertion is a
# COMPARISON rather than a hardcoded number.
_PANEL_QUERY_COUNTS: dict[int, int] = {}


def _open():
    from identity.contracts.principals import OPEN_PRINCIPAL
    return OPEN_PRINCIPAL


def _scope_open(stream):
    return workstream_scope(_open(), stream.pk)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tools/rag/tests/test_workstream_pins.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.rag.workstreams'`

- [ ] **Step 3: Add the two audit actions**

In `identity/contracts/actions.py`, beside the nine from Task 3:

```python
DOCUMENT_PINNED = "workstream.document_pinned"
DOCUMENT_UNPINNED = "workstream.document_unpinned"
```

and extend `AUDIT_ACTIONS` with both. `target_type` is `"workstream"` for each — the pin is a
change to the stream's working set, and `for_target("workstream", pk)` is the question an
operator asks.

- [ ] **Step 4: Write the module**

Create `tools/rag/workstreams.py`:

```python
"""What a workstream contains, from this column's side.

THE ONLY MODULE THAT WRITES `WorkstreamPin`, and (with
`tools/rag/access.py`) one of only two that read it -- the same shape and
the same reason the `Document.objects` gate exists: two readers of "what
is in this stream" is how two surfaces come to disagree. Pinned by
`foundation/ops/tests/test_column_boundaries.py`.

IT REACHES `agents/` THROUGH `agents.workstreams` AND `agents.contracts`
AND NOTHING ELSE -- the permitted direction, closed to three names by
`foundation/ops/tests/test_import_law.py::test_tools_reaches_agents_
through_contracts_entitlements_and_workstreams_and_nothing_else`.
"""
from __future__ import annotations

from dataclasses import replace

from agents.contracts.workstreams import WorkstreamScope
from identity import audit
from identity.contracts import actions
from tools.rag.models import Document, WorkstreamPin

# A pin set becomes an `ANY` array of ids in EVERY query this stream
# runs, so an uncapped one is unbounded work per turn. A CAP, NOT A
# PAGINATOR -- the reason `SIDEBAR_LIMIT = 30` is one: this is a
# single-operator box and a working set is not an archive (author
# decision 7).
MAX_PINS_PER_STREAM = 200


def scope_with_pins(scope: WorkstreamScope) -> WorkstreamScope:
    """The same frozen value with `pinned_file_ids` filled in.

    THE ONLY FUNCTION THAT FILLS THEM (author decision 6). The stream
    half -- id, wall, upload default, may-upload -- crosses the seam from
    `agents/workstreams.py` with the pins empty, because the pin table is
    a `tools/rag` table and the column that owns it is the column that
    reads it.

    `dataclasses.replace`, not a fresh construction, so a field added to
    `WorkstreamScope` later travels through here without an edit.
    """
    return replace(scope, pinned_file_ids=frozenset(
        WorkstreamPin.objects.filter(workstream_id=scope.workstream_id)
        .values_list("document_id", flat=True)))


def pin_document(principal, scope: WorkstreamScope, document) -> str | None:
    """Pin `document` into the stream. Returns None on success, or a
    sentence naming the refusal.

    THREE REFUSALS, all NAMED rather than 404-shaped, because this is a
    button on a page listing rows the caller can already see:

      - the document is CONTAINED (owner decision 4: one home, never
        two). Not into another stream, and not into its own -- it is
        already in its stream's corpus by containment, and putting it in
        a second stream is the duplication that decision forbids.
      - the cap (`MAX_PINS_PER_STREAM`) is reached.
      - the caller may not read the document (`readable_documents`).

    The third is ALSO a 404 at the route (`rag-workstream-pin` resolves
    the document through `readable_documents`) and is repeated here
    because this function is the CLI-facing one too, and a predicate
    stated in the view alone is a predicate the second caller does not
    get.

    A PIN DOES NOT FOLLOW A DOCUMENT'S LABELS. Pinning is recorded once;
    if the document is later labelled with an entitlement the pinner does
    not hold, the pin row survives and the document simply stops being
    readable by them -- spec §6.1's outer intersection does that, with no
    pin bookkeeping at all. A pin is never a stored permission and
    therefore never goes stale in a way that matters.
    """
    from tools.rag.access import readable_documents

    if document.workstream_id is not None:
        return (f"“{document.title}” already lives in a workstream. A document has one "
                f"home; pin a universal document instead.")
    if WorkstreamPin.objects.filter(workstream_id=scope.workstream_id).count() \
            >= MAX_PINS_PER_STREAM:
        return (f"This workstream already has {MAX_PINS_PER_STREAM} pinned documents, "
                f"which is the limit. Unpin one first.")
    if not readable_documents(principal).filter(pk=document.pk).exists():
        return "You may not read that document, so it cannot be pinned here."
    WorkstreamPin.objects.get_or_create(
        workstream_id=scope.workstream_id, document=document,
        defaults={"pinned_by_id": int(principal.key) if principal.kind == "user" else None})
    audit.record(principal, actions.DOCUMENT_PINNED, target_type="workstream",
                 target_key=scope.workstream_id, document=document.pk,
                 document_title=document.title)
    return None


def unpin_document(principal, scope: WorkstreamScope, pin_id) -> str | None:
    """Remove one pin FROM THIS STREAM. None if it went, a sentence
    otherwise.

    THE PIN IS CHECKED AGAINST THIS STREAM, or an owner of stream A could
    unpin from stream B by guessing a sequential id -- the same IDOR
    `agents.visibility.revoke_share` guards against, and `isdecimal()`
    for the same reason its docstring gives: `isdigit()` admits
    characters like "²" that `int()` itself rejects.
    """
    if not str(pin_id).isdecimal():
        return "That pin does not belong to this workstream."
    row = WorkstreamPin.objects.filter(
        pk=int(pin_id), workstream_id=scope.workstream_id).select_related("document").first()
    if row is None:
        return "That pin does not belong to this workstream."
    title, document_pk = row.document.title, row.document_id
    row.delete()
    audit.record(principal, actions.DOCUMENT_UNPINNED, target_type="workstream",
                 target_key=scope.workstream_id, document=document_pk, document_title=title)
    return None


def stream_documents(principal, scope: WorkstreamScope):
    """The ORM mirror of spec §6.1's corpus formula, for the panel and
    the pin picker:

        (  universal ∩ wall   (only when the wall is non-empty)
         ∪ pinned
         ∪ contained-here  )  ∩ readable_by(principal)

    THE READER'S OWN GRANTS ARE THE OUTERMOST INTERSECTION AND NOTHING
    ESCAPES THEM. A pin does not grant, containment does not grant, and a
    stream never grants (author decision 2). The wall sits INSIDE the
    parentheses because it narrows what the universal library
    contributes; the pin and the contained set sit beside it because they
    are deliberate acts that admit specific documents, and a wall -- a
    convenience the owner set for themselves -- does not un-admit what
    somebody deliberately put in.

    A NON-EMPTY WALL EXCLUDES UNLABELLED UNIVERSAL DOCUMENTS (author
    decision 5): a wall says "this stream is about material under these
    entitlements", and a document under no entitlement is under none of
    them. The stream page states that consequence in one line beside the
    wall editor.
    """
    from django.db.models import Q

    from tools.rag.access import readable_documents

    universal = Q(workstream__isnull=True)
    if scope.wall:
        universal &= Q(entitlement_labels__entitlement_id__in=scope.wall)
    corpus = universal | Q(workstream_id=scope.workstream_id)
    if scope.pinned_file_ids:
        corpus |= Q(pk__in=scope.pinned_file_ids)
    return readable_documents(
        principal, workstream_id=scope.workstream_id).filter(corpus).distinct()


def panel(principal, workstream_id) -> dict:
    """`rag.documents` — this column's stream-page section (spec §15.3
    item 6).

    Registered as a DOTTED PATH from `tools/rag/apps.py`, resolved at
    render time by `agents/workstreams.py::panels_for`, so `agents/`
    never imports this module. Three groups, exactly as §15.3 names them:
    **Contained** (with notes marked by `origin`), **Pinned** (each with
    its pin id, for Unpin), and the **Pin a document** control's capped
    `<select>` of readable universal documents.
    """
    from agents.workstreams import workstream_scope
    from tools.rag.access import readable_documents

    scope = workstream_scope(principal, workstream_id)
    if scope is None:
        return {"contained": [], "pinned": [], "pinnable": [], "capped": False}
    pins = list(WorkstreamPin.objects.filter(workstream_id=workstream_id)
                .select_related("document").order_by("-pinned_at"))
    readable_here = readable_documents(principal, workstream_id=workstream_id)
    contained = list(readable_here.filter(workstream_id=workstream_id)
                     .order_by("origin", "title"))
    pinned_ids = {p.document_id for p in pins}
    # ONE QUERY FOR THE WHOLE PIN SET, not one per pin. A
    # `.filter(...).exists()` inside the comprehension would be up to
    # `MAX_PINS_PER_STREAM` queries on a page Global Constraint 6 covers
    # -- the same shape `agents/chat/sidebar.py`'s own docstring records
    # as a measured regression (+2 per row, 47 -> 95 at 25 rows).
    readable_pin_ids = set(readable_here.filter(pk__in=pinned_ids)
                           .values_list("pk", flat=True))
    visible_pins = [{"pin": p, "document": p.document} for p in pins
                    if p.document_id in readable_pin_ids]
    pinnable = list(readable_documents(principal)
                    .filter(workstream__isnull=True)
                    .exclude(pk__in=pinned_ids)
                    .order_by("title")[:MAX_PINS_PER_STREAM])
    return {
        "contained": contained,
        "pinned": visible_pins,
        "pinnable": pinnable,
        "capped": len(pins) >= MAX_PINS_PER_STREAM,
    }
```

`readable_documents(principal, workstream_id=...)` is **Task 6's** signature, which is why the two
tasks run in that order: this module cannot compile against the unmodified
`readable_documents(principal)` at `tools/rag/access.py:109`, so the containment parameter lands
first and this commit is green on its own.

- [ ] **Step 5: Register the panel**

In `tools/rag/apps.py::RagConfig.ready()`, after the entitlement cascade registration:

```python
        # The stream page's Documents section (spec §4.2, Direction B).
        # `agents/` may not import `tools/` AT ALL, so the page renders
        # whatever is REGISTERED, in registration order, through one
        # include -- and a second panel later (generated images in a
        # stream, say) is a REGISTRATION, not an edit to a view that
        # would otherwise silently skip it.
        #
        # REGISTERED IN THE SAME COMMIT AS THE HANDLER, for the same
        # reason the cascade above is: the resolver uses `import_string`
        # and never swallows, so a registration that landed before its
        # module would make every stream page raise `ImportError`.
        from agents.contracts.workstreams import (
            WorkstreamPanel, register_workstream_panel,
        )

        register_workstream_panel(WorkstreamPanel(
            "rag.documents", "Documents", "tools.rag.workstreams.panel",
            "rag/panels/documents.html"))
```

- [ ] **Step 6: Run the pin tests**

Run: `pytest tools/rag/tests/test_workstream_pins.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Add the `WorkstreamPin.objects` column-boundary gate**

Append to `foundation/ops/tests/test_column_boundaries.py`:

```python
_PIN_READERS = ("tools/rag/workstreams.py", "tools/rag/access.py")


def _pin_objects_count(source: str) -> int:
    """`WorkstreamPin.objects` in `source` — the same AST shape
    `_document_objects_count` uses, so it catches an in-function reach as
    readily as a module-scope one and does not trip over the word
    "objects" in a docstring."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "objects"
        and isinstance(node.value, ast.Name) and node.value.id == "WorkstreamPin"
    )


def test_the_pin_table_has_exactly_two_sanctioned_readers():
    """The same shape, and for the same reason, the `Document.objects`
    gate exists: two readers of "what is in this stream" is how two
    surfaces come to disagree. `tools/rag/workstreams.py` owns the
    writes; `tools/rag/access.py` may read for the visibility value it
    builds. Every other module asks one of them a question.
    """
    offenders = {}
    for relative in _scanned("tools", "agents", "models", "foundation"):
        if relative in _PIN_READERS:
            continue
        count = _pin_objects_count((REPO_ROOT / relative).read_text(encoding="utf-8"))
        if count:
            offenders[relative] = count
    assert offenders == {}, offenders


def test_the_pin_gate_would_actually_catch_a_violation():
    assert _pin_objects_count("WorkstreamPin.objects.count()\n") == 1
    assert _pin_objects_count("stream_documents(p, s).count()\n") == 0
```

`_scanned` is the helper Task 1 added to `test_import_law.py`; import it, or repeat the two-line
`git ls-files` body locally — this module already has its own `REPO_ROOT`/`_is_test_file` imports
from `foundation/ops/tests/_helpers.py`, so repeating it is the smaller change and matches how the
two modules already relate.

- [ ] **Step 8: Extend the anti-vacuous import pin to all three names**

In `foundation/ops/tests/test_import_law.py::test_the_tools_to_agents_allowlist_is_not_vacuous`,
add the third assertion and delete the paragraph of its docstring that explained the omission:

```python
    assert "agents.contracts" in sources
    assert "agents.entitlements" in sources
    assert "agents.workstreams" in sources
```

- [ ] **Step 9: Run the guards and the suite**

Run: `pytest foundation/ops/tests/ -q`
Expected: PASS — three allowed names, all three really imported, and the pin table has exactly
two readers.

Run: `pytest tools/rag/ agents/ -q`
Expected: PASS.

- [ ] **Step 10: Update the column README**

In `tools/rag/README.md`'s Workstreams section: the pin cap and why it is a cap; the one writer of
the pin table; the panel registration and the same-commit rule.

- [ ] **Step 11: Commit**

```bash
git add tools/rag/workstreams.py tools/rag/apps.py tools/rag/tests/test_workstream_pins.py \
        identity/contracts/actions.py foundation/ops/tests/test_column_boundaries.py \
        foundation/ops/tests/test_import_law.py tools/rag/README.md
git commit -m "feat(rag): pins, the stream corpus query, and the registered documents panel"
```

---

### Task 8: the chunk-metadata cache gains one key

**Files:**
- Modify: `tools/rag/labels.py` (`restamp_document_chunks` — four branches, one statement, a disjunctive guard)
- Modify: `tools/rag/workstreams.py` (`set_document_workstream`)
- Test: `tools/rag/tests/test_labels.py` (append)

**Interfaces:**
- Consumes: `Document.workstream` (Task 5).
- Produces: `restamp_document_chunks(doc_id, *, raising=False)` — unchanged signature, now writing
  two independent optional keys; `tools.rag.workstreams.set_document_workstream(actor, document,
  workstream_id)` — the third caller.

- [ ] **Step 1: Write the failing cache test**

Append to `tools/rag/tests/test_labels.py`:

```python
def test_a_contained_documents_chunks_carry_the_workstream_key_as_a_decimal_string(
        chunk_table):
    """THE CONTAINMENT VALUE IS THE STREAM PK AS A DECIMAL STRING,
    matching the `entitlements` convention `retrieval.py` documents for
    the same reason: `ANY` and `EQ` render as JSON *string* comparisons
    over `metadata_`, so an integer stamped into the metadata would never
    match `str(workstream_id)` in the filter."""
    stream = _workstream()
    doc = make_document(workstream=stream)
    _seed_chunks(doc.id)
    restamp_document_chunks(doc.id, raising=True)
    assert _metadata(doc.id)[0]["workstream"] == str(stream.pk)


def test_a_universal_documents_chunks_have_the_key_REMOVED_not_set_to_empty(
        chunk_table):
    """§17.2 CELL 7, the one that catches an empty-string value.
    `IS_EMPTY` renders `metadata_->>'workstream' IS NULL`, which `""`
    does not satisfy — so a writer that stamped an empty value instead of
    removing the key would pass every other cell in the matrix and fail
    only here, and every re-stamped universal document would become
    invisible to every loose turn, to Ask and to Search: silently,
    totally, and only on rows that had been re-stamped."""
    stream = _workstream()
    doc = make_document(workstream=stream)
    _seed_chunks(doc.id)
    restamp_document_chunks(doc.id, raising=True)
    doc.workstream = None
    doc.save(update_fields=["workstream", "updated_at"])
    restamp_document_chunks(doc.id, raising=True)
    assert "workstream" not in _metadata(doc.id)[0]


@pytest.mark.parametrize("labelled,contained", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_two_optional_keys_need_four_branches(chunk_table, labelled, contained):
    """Each key is present-or-absent INDEPENDENTLY, so there are four
    states and not two: an unlabelled universal document, a labelled
    universal one, an unlabelled contained one, a labelled contained
    one."""
    stream = _workstream() if contained else None
    doc = make_document(workstream=stream)
    if labelled:
        doc.entitlement_labels.create(entitlement=make_entitlement())
    _seed_chunks(doc.id)
    restamp_document_chunks(doc.id, raising=True)
    metadata = _metadata(doc.id)[0]
    assert ("entitlements" in metadata) is labelled
    assert ("workstream" in metadata) is contained


def test_the_no_op_guard_still_fires_for_an_unlabelled_universal_document(
        chunk_table, monkeypatch):
    """§17.2 CELL 8. `tools/rag/labels.py` records why the guard exists
    ("review finding 3"): without it, every ingest of every never-labelled
    document rewrites every chunk row — a dead tuple and a WAL record per
    chunk, on the box's most common case. A SINGLE-KEY guard carried
    forward unchanged would silently reintroduce that regression the
    moment a second optional key appeared, which is exactly what the
    DISJUNCTION prevents."""
    doc = make_document()
    _seed_chunks(doc.id)
    before = _chunk_updated_marker(doc.id)
    with _rows_touched_by_last_restamp(monkeypatch)() as touched:
        restamp_document_chunks(doc.id, raising=True)
    assert _chunk_updated_marker(doc.id) == before
    assert touched == [0]


def test_setting_a_documents_containment_restamps_and_raises_on_failure(chunk_table):
    """`raising=True` for the third caller, for the reason the flag
    already encodes: a containment change that appears saved and is not
    enforced is worse than one that refuses to save."""
    from tools.rag.workstreams import set_document_workstream

    stream = _workstream()
    doc = make_document()
    _seed_chunks(doc.id)
    set_document_workstream(OPEN_PRINCIPAL, doc, stream.pk)
    doc.refresh_from_db()
    assert doc.workstream_id == stream.pk
    assert _metadata(doc.id)[0]["workstream"] == str(stream.pk)
```

**Three of those names are the module's real ones and two do not exist.** `test_labels.py` defines
`chunk_table` — a `@pytest.fixture(params=["json", "jsonb"])` at line 41, **parametrized over both
live column shapes deliberately** (T10 review finding 5), so every test taking it runs twice and the
`{cast}` branch of §8.3's statement is exercised on both; `_seed_chunks(doc_id, count=2)` at line 65,
plural and taking an **id**; and `_metadata(doc_id)` at line 74, which returns a **list** of
per-chunk dicts (hence `_metadata(doc.id)[0]` above — `_metadata(doc.id)["workstream"]` would raise
`TypeError`). `_chunk_updated_marker` and `_rows_touched_by_last_restamp` exist nowhere in
`tools/rag/tests/`, so this task writes them:

```python
def _chunk_updated_marker(doc_id):
    """The `metadata_` of every chunk of `doc_id`, as a comparable value.

    The chunk table this module creates has no `updated_at`, so "did the
    UPDATE touch these rows" is answered by the CONTENT, which is what
    the no-op guard is about anyway: a re-stamp that changed nothing must
    leave every chunk byte-identical.
    """
    return _metadata(doc_id)


def _rows_touched_by_last_restamp(monkeypatch):
    """A context manager yielding a one-element list that receives the
    `rowcount` of the next `restamp_document_chunks` statement.

    `tools.rag.labels._execute_stamp` is factored out of that function
    precisely so a test can reach the one cursor -- its own docstring
    says so ("Factored out so a test can make the store fail without
    also making the table-existence check fail"). This wraps it rather
    than replacing it, so the real UPDATE still runs and the assertion is
    about what it actually did.
    """
    import contextlib

    from tools.rag import labels as labels_module

    @contextlib.contextmanager
    def _capture():
        seen = []
        real = labels_module._execute_stamp

        def _wrapped(sql, params):
            from django.db import connection

            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                seen.append(cursor.rowcount)

        monkeypatch.setattr(labels_module, "_execute_stamp", _wrapped)
        try:
            yield seen
        finally:
            monkeypatch.setattr(labels_module, "_execute_stamp", real)

    return _capture
```

The no-op test therefore takes `monkeypatch` and reads the count from the wrapper:

```python
def test_the_no_op_guard_still_fires_for_an_unlabelled_universal_document(
        chunk_table, monkeypatch):
    """§17.2 CELL 8. ... (docstring unchanged) ..."""
    doc = make_document()
    _seed_chunks(doc.id)
    before = _chunk_updated_marker(doc.id)
    with _rows_touched_by_last_restamp(monkeypatch)() as touched:
        restamp_document_chunks(doc.id, raising=True)
    assert _chunk_updated_marker(doc.id) == before
    assert touched == [0]
```

This module already imports `pytest`, `json`, `connection`, `rag_index` and
`tools.rag.tests._helpers`' builders; Task 8's new tests additionally need
`from identity.contracts.principals import OPEN_PRINCIPAL` (used by the containment-change test) and
`from tools.rag.tests._helpers import _workstream`, neither of which `test_labels.py` imports today.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tools/rag/tests/test_labels.py -k workstream -v`
Expected: FAIL — the `workstream` key is never written.

- [ ] **Step 3: Rewrite `restamp_document_chunks`**

```python
def restamp_document_chunks(doc_id, *, raising: bool = False) -> None:
    """Rewrite `doc_id`'s chunks' `entitlements` AND `workstream` keys
    from the tables. ONE SQL STATEMENT.

    `raising=False` (the INGEST-TIME caller) logs and returns: a failed
    re-stamp must not fail an ingest, and the label page can repair it.
    `raising=True` (the LABEL-CHANGE caller, and the CONTAINMENT-CHANGE
    caller `tools.rag.workstreams.set_document_workstream`) raises, so the
    write rolls back with it -- a label or a containment that appears to
    be saved and is not enforced is worse than one that refuses to save.

    NOT RENAMED, despite gaining a second key: it is still the ONE WRITER
    of chunk metadata, and its three callers are still three doors onto
    one statement. Two writers would produce two shapes of one fact.

    IT TAKES AN ID, NOT A ROW, so it has nowhere to read
    `Document.workstream_id` from -- so it reads it, itself, in one more
    small query beside the labels. A `workstream_id=` parameter was the
    alternative and is worse: three callers would each have to supply it
    correctly, and the one that got it wrong would stamp a containment
    the table disagrees with. THE TABLE IS THE FACT AND THE METADATA IS A
    CACHE, and a cache writer that reads the fact itself cannot cache the
    wrong thing.

    TWO OPTIONAL KEYS NEED FOUR BRANCHES, NOT TWO. Each key is
    present-or-absent independently, and `IS_EMPTY` matches only an
    ABSENT key, because `metadata_->>'k'` yields SQL `NULL` for a missing
    key and `''` for an empty string. Writing `""` for "no stream" would
    make every re-stamped universal document invisible to every loose
    turn, to Ask and to Search: silently, totally, and only on rows that
    had been re-stamped. So this SETS A KEY OR REMOVES IT, never writes
    an empty value.

    A NO-OP when the chunk table does not exist (nothing prose has ever
    been ingested) -- the same defensive posture
    `tools.rag.index.delete_chunks_for_document` documents.
    """
    shape = rag_index.live_store_shape()
    if shape is None:
        return
    # The live `metadata_` column is `json`, not `jsonb`, on any box that
    # has never enabled hybrid search. BOTH filter operators cast
    # explicitly and work on either type; only this UPDATE's ASSIGNMENT
    # needs the branch.
    cast = "" if shape["jsonb"] else "::json"
    table = rag_index.LIVE_TABLE_NAME   # a module constant, never caller input
    ids = sorted(str(i) for i in _label_ids_for(doc_id))
    ws_id = Document.objects.filter(pk=doc_id).values_list(
        "workstream_id", flat=True).first()

    sets: list[tuple[str, str]] = []
    removes: list[str] = []
    if ids:
        sets.append(("entitlements", json.dumps(ids)))
    else:
        removes.append("entitlements")
    if ws_id:
        sets.append(("workstream", json.dumps(str(ws_id))))
    else:
        removes.append("workstream")

    # ONE STATEMENT: nested `jsonb_set(..., create_missing=true)` for each
    # key in `sets`, chained `- 'key'` for each in `removes`.
    expression = "metadata_::jsonb"
    params: list = []
    for key, value in sets:
        expression = f"jsonb_set({expression}, '{{{key}}}', %s::jsonb, true)"
        params.append(value)
    for key in removes:
        expression = f"({expression} - '{key}')"

    # THE GUARD IS A DISJUNCTION, and it is not decoration. The
    # single-key version of it (`AND metadata_->>'entitlements' IS NOT
    # NULL`) exists because without it this UPDATE rewrote every chunk
    # row on every ingest of every never-labelled document -- a dead
    # tuple and a WAL record per chunk, on the box's most common case
    # (review finding 3). Carried forward unchanged, it would silently
    # reintroduce that regression the moment a second optional key
    # appeared, and it would ALSO skip a real containment write. The
    # disjunction says "at least one key would actually change".
    sql = (f"UPDATE {table} SET metadata_ = ({expression}){cast} "
           f"WHERE metadata_->>'file_id' = %s "
           f"AND (metadata_->>'entitlements' IS NOT NULL "
           f"     OR metadata_->>'workstream' IS NOT NULL "
           f"     OR %s)")
    params += [str(doc_id), bool(sets)]

    try:
        _execute_stamp(sql, params)
    except Exception:
        if raising:
            raise
        logger.warning("rag: could not re-stamp chunk metadata for Document %s", doc_id,
                       exc_info=True)
```

Add `Document` to this module's `from tools.rag.models import ...` line.

- [ ] **Step 4: Write the third caller**

Append to `tools/rag/workstreams.py`:

```python
def set_document_workstream(actor, document, workstream_id) -> None:
    """Contain `document` in a stream (or release it, with `None`), and
    re-stamp its chunks, IN ONE TRANSACTION.

    `raising=True`, the third caller of `restamp_document_chunks` beside
    `_ingest_prose` (ingest time, `raising=False`) and
    `set_document_labels` (a label change, `raising=True`). A containment
    change that appears saved and is not enforced is worse than one that
    refuses to save, which is the rule the `raising` flag already
    encodes.

    Audited as `DOCUMENT_CONTAINED`, with the `library.` namespace
    because `DOCUMENT_LABELLED` already takes it and the subject is the
    same row.
    """
    from django.db import transaction

    from tools.rag.labels import restamp_document_chunks

    with transaction.atomic():
        document.workstream_id = workstream_id
        document.save(update_fields=["workstream", "updated_at"])
        restamp_document_chunks(document.id, raising=True)
    audit.record(actor, actions.DOCUMENT_CONTAINED, target_type="document",
                 target_key=document.id, target_label=document.title,
                 workstream=workstream_id)
```

Add `DOCUMENT_CONTAINED = "library.document_contained"` to `identity/contracts/actions.py` and to
`AUDIT_ACTIONS`.

- [ ] **Step 5: Run the tests**

Run: `pytest tools/rag/tests/test_labels.py -v`
Expected: PASS — the four-branch parametrisation, the removed-not-empty case, the disjunctive
guard and the third caller, plus every existing `entitlements` case unchanged.

Run: `pytest tools/rag/ -q`
Expected: PASS. `manage.py relabel_chunks` still works: it calls the same one writer.

- [ ] **Step 6: Update the column README**

In `tools/rag/README.md`: the `workstream` chunk-metadata key, its one writer, its three callers,
the decimal-string spelling and why, and the no-back-fill argument (an absent key is exactly what
`IS_EMPTY` matches and exactly what every chunk already looks like).

- [ ] **Step 7: Commit**

```bash
git add tools/rag/labels.py tools/rag/workstreams.py tools/rag/tests/test_labels.py \
        identity/contracts/actions.py tools/rag/README.md
git commit -m "feat(rag): the chunk-metadata cache gains a workstream key, with a disjunctive guard"
```

---

### Task 9: seam one — the wall and containment at the one retrieval filter point

**Files:**
- Modify: `tools/rag/retrieval.py` (`_visibility_filters` gains one clause)
- Modify: `agents/contracts/tools.py` (`ToolContext` gains one field)
- Modify: `agents/runtime/loop.py:371` (the `ToolContext` construction)
- Modify: `tools/rag/tools.py` (the two retrieval runners)
- Modify: `tools/rag/tests/test_retrieval_visibility.py` (the `None`-return cases)
- Test: `tools/rag/tests/test_workstream_corpus.py` (new — the generated matrix)

**Interfaces:**
- Consumes: `WorkstreamScope` (Task 1); `DocumentVisibility.stream`,
  `readable_documents(workstream_id=)` (Task 6); `scope_with_pins`, `stream_documents` (Task 7); the `workstream`
  chunk key (Task 8).
- Produces: `ToolContext.stream: WorkstreamScope | None = None`; a `_visibility_filters` that
  **never returns `None`**.

- [ ] **Step 1: Write the failing filter tests**

Create `tools/rag/tests/test_workstream_corpus.py`:

```python
"""The composition law, asserted as a generated matrix — spec §17.2.

Two expressions of one rule must exist, because the ORM path and the
chunk-metadata path answer the same question for different consumers
(pages versus retrieval). TWO EXPRESSIONS OF ONE RULE THAT ARE NEVER
COMPARED ARE TWO RULES, so every cell asserts both and asserts they
agree.

The chunk half is asserted as a FILTER OBJECT, not through a live store,
for the reason `_visibility_filters`' own docstring gives: a filter only
testable end to end is a filter tested rarely.
"""
from __future__ import annotations

import itertools

import pytest
from llama_index.core.vector_stores.types import FilterCondition, FilterOperator

from agents.contracts.workstreams import WorkstreamScope
from tools.rag.access import document_visibility, readable_documents
from tools.rag.retrieval import _visibility_filters
from tools.rag.tests._helpers import _workstream, make_document
from tools.rag.workstreams import stream_documents
from identity.testing import (
    grant, make_admin, make_entitlement, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db

PLACEMENTS = ("universal", "contained-here", "contained-elsewhere", "pinned")
LABELS = ("none", "e1", "e2")
GRANTS = ("none", "e1", "both")
WALLS = ("empty", "e1", "e2")


def _flat(filters):
    """Every `MetadataFilter` in a possibly-nested `MetadataFilters`,
    flattened, so a cell can assert on keys and operators without
    caring how deep the store's recursion goes."""
    out = []
    for f in filters.filters:
        out.extend(_flat(f) if hasattr(f, "filters") else [f])
    return out


@pytest.mark.parametrize("placement,labels,grants,wall",
                         itertools.product(PLACEMENTS, LABELS, GRANTS, WALLS))
def test_the_orm_and_the_chunk_filter_agree_on_every_cell(placement, labels, grants, wall):
    """The whole cross product. Each cell builds the world, asks the ORM
    expression (`stream_documents` / `readable_documents`) and the
    chunk-metadata expression (`_visibility_filters`), and asserts they
    describe the same corpus."""
    e1, e2 = make_entitlement(name="E1"), make_entitlement(name="E2")
    reader = make_user()
    if grants in ("e1", "both"):
        grant(e1, user=reader)
    if grants == "both":
        grant(e2, user=reader)
    stream, other = _workstream(), _workstream()
    doc = make_document(workstream={
        "universal": None, "pinned": None,
        "contained-here": stream, "contained-elsewhere": other,
    }[placement])
    if labels != "none":
        doc.entitlement_labels.create(entitlement=e1 if labels == "e1" else e2)
    pins = frozenset({doc.pk}) if placement == "pinned" else frozenset()
    if placement == "pinned":
        from tools.rag.models import WorkstreamPin
        WorkstreamPin.objects.create(workstream=stream, document=doc)
    wall_ids = {"empty": frozenset(),
                "e1": frozenset({e1.pk}),
                "e2": frozenset({e2.pk})}[wall]
    scope = WorkstreamScope(workstream_id=stream.pk, wall=wall_ids,
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=pins)

    with posture("enterprise"):
        principal = user_principal(reader)
        in_orm = stream_documents(principal, scope).filter(pk=doc.pk).exists()
        filters = _visibility_filters(
            None, document_visibility(principal, stream=scope))
        in_chunks = _chunk_filter_admits(filters, doc, stream.pk, pins)
    assert in_orm == in_chunks, (placement, labels, grants, wall, in_orm, in_chunks)
    _OUTCOMES.append(in_orm)


# Every cell's answer, accumulated across the whole product, so the two
# pins below can prove the matrix is not vacuous.
_OUTCOMES: list[bool] = []


def test_the_matrix_is_not_vacuous():
    """`in_orm == in_chunks` PASSES TRIVIALLY WHEN BOTH ARE FALSE, and 27
    of the 108 cells are `contained-elsewhere`, which is `False == False`
    by construction — as is every label/grant mismatch. A matrix that
    only ever compared two `False`s would assert nothing about the
    composition law at all, and would keep passing if
    `stream_documents` and `_visibility_filters` both started returning
    nothing.

    So: the product must produce BOTH answers, and enough of each that a
    single accidental `return False` in either expression is caught. Run
    after the parametrized cells (pytest executes in declaration order
    within a module), reading what they recorded.

    THE MODULE-LEVEL `_OUTCOMES` MAKES THIS ORDER-DEPENDENT, and that is
    accepted because of which way it fails. Under `-k`, `-x`, or a
    random-order plugin this pin can see a partial product or none at
    all -- but an empty or short list trips the first assertion, so it
    FAILS LOUDLY ("the matrix did not run") rather than passing
    vacuously, which is the failure direction that matters for a pin
    whose whole job is to catch vacuity. A full run is the only
    configuration in which it passes.
    """
    assert _OUTCOMES, "the matrix did not run"
    assert any(_OUTCOMES), "no cell admitted a document — both expressions may be empty"
    assert not all(_OUTCOMES), "every cell admitted — neither expression is filtering"
    # The 27 `contained-elsewhere` cells are the ones that MUST be False,
    # and the unwalled/held/universal cells are the ones that MUST be
    # True; between them they bound the matrix from both sides.
    assert sum(_OUTCOMES) >= 9, sum(_OUTCOMES)
    assert _OUTCOMES.count(False) >= 27, _OUTCOMES.count(False)


def _chunk_filter_admits(filters, doc, workstream_id, pins) -> bool:
    """Evaluate the built filter object against the metadata this
    document's chunks would carry — the same four-branch shape
    `restamp_document_chunks` writes (an ABSENT key, never an empty
    value)."""
    metadata = {"file_id": str(doc.pk)}
    label_ids = sorted(str(i) for i in doc.entitlement_labels.values_list(
        "entitlement_id", flat=True))
    if label_ids:
        metadata["entitlements"] = label_ids
    if doc.workstream_id:
        metadata["workstream"] = str(doc.workstream_id)
    return _evaluate(filters, metadata)


def _evaluate(node, metadata) -> bool:
    """A tiny interpreter for the three operators this phase uses —
    `EQ`, `ANY`, `IS_EMPTY` — plus `AND`/`OR` grouping. It exists so a
    cell can assert what the store WOULD do without a store: the
    operators' SQL semantics are documented in `retrieval.py` and
    `labels.py`, and `IS_EMPTY` in particular means "the key is ABSENT",
    which is the whole point of §17.2 cell 7."""
    if hasattr(node, "filters"):
        results = [_evaluate(f, metadata) for f in node.filters]
        return all(results) if node.condition == FilterCondition.AND else any(results)
    value = metadata.get(node.key)
    if node.operator == FilterOperator.IS_EMPTY:
        return value is None
    if node.operator == FilterOperator.EQ:
        return value == node.value
    if node.operator == FilterOperator.ANY:
        return bool(set(value or []) & set(node.value))
    raise AssertionError(f"unhandled operator {node.operator}")
```

Then the eight named cells, appended to the same module, each asserting the property directly
rather than through the product:

```python
def test_cell_1_a_contained_document_is_invisible_everywhere_else():
    """Invisible in the library, in Ask, in Search AND IN ANOTHER STREAM,
    for a reader who holds every one of its labels."""
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream, other = _workstream(), _workstream()
    doc = make_document(workstream=stream)
    doc.entitlement_labels.create(entitlement=ent)
    other_scope = WorkstreamScope(workstream_id=other.pk, wall=frozenset(),
                                  default_upload_placement="", may_upload=True)
    with posture("enterprise"):
        p = user_principal(reader)
        assert not readable_documents(p).filter(pk=doc.pk).exists()
        assert not stream_documents(p, other_scope).filter(pk=doc.pk).exists()


def test_cell_2_a_pinned_document_is_visible_in_both_and_follows_its_label():
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream = _workstream()
    doc = make_document()
    doc.entitlement_labels.create(entitlement=ent)
    from tools.rag.models import WorkstreamPin
    WorkstreamPin.objects.create(workstream=stream, document=doc)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=frozenset({doc.pk}))
    with posture("enterprise"):
        p = user_principal(reader)
        assert readable_documents(p).filter(pk=doc.pk).exists()
        assert stream_documents(p, scope).filter(pk=doc.pk).exists()
    other = make_user()
    with posture("enterprise"):
        q = user_principal(other)
        assert not readable_documents(q).filter(pk=doc.pk).exists()
        assert not stream_documents(q, scope).filter(pk=doc.pk).exists()


def test_cell_3_a_non_empty_wall_hides_unlabelled_universal_but_not_contained_or_pinned():
    """AUTHOR DECISION 5, and the one place a wall visibly removes
    something a person could otherwise see. The stream page says so in
    one line beside the wall editor."""
    reader = make_user()
    ent = make_entitlement()
    grant(ent, user=reader)
    stream = _workstream()
    unlabelled_universal = make_document()
    contained = make_document(workstream=stream)
    pinned = make_document()
    from tools.rag.models import WorkstreamPin
    WorkstreamPin.objects.create(workstream=stream, document=pinned)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset({ent.pk}),
                            default_upload_placement="", may_upload=True,
                            pinned_file_ids=frozenset({pinned.pk}))
    with posture("enterprise"):
        keys = set(stream_documents(user_principal(reader), scope)
                   .values_list("pk", flat=True))
    assert unlabelled_universal.pk not in keys
    assert contained.pk in keys
    assert pinned.pk in keys


def test_cell_4_sees_all_content_does_not_defeat_containment_in_retrieval():
    """RULING G's other half. An administrator with the content setting
    on still does not RETRIEVE another stream's contained document into
    THIS stream's turn — which is the property containment actually owns,
    and is a different question from whether they may open the bytes
    (they may; `test_containment_routes.py` asserts the 200)."""
    admin = make_admin()
    stream, other = _workstream(), _workstream()
    elsewhere = make_document(workstream=other)
    scope = WorkstreamScope(workstream_id=stream.pk, wall=frozenset(),
                            default_upload_placement="", may_upload=True)
    with posture("enterprise", admin_sees_content=True):
        p = user_principal(admin)
        assert not stream_documents(p, scope).filter(pk=elsewhere.pk).exists()
        filters = _visibility_filters(None, document_visibility(p, stream=scope))
        assert not _chunk_filter_admits(filters, elsewhere, stream.pk, frozenset())


def test_cell_6_permits_and_readable_documents_agree_about_containment():
    """Asserted directly rather than through the page — the property
    `permits` exists for, and the one a one-sided change breaks
    invisibly. (The full version lives in `test_access_documents.py`;
    this is the matrix's own copy, so a reader of §17.2 finds all eight
    cells in one file.)"""
    stream = _workstream()
    here = make_document(workstream=stream)
    universal = make_document()
    with posture("open"):
        from identity.contracts.principals import OPEN_PRINCIPAL
        v = document_visibility(OPEN_PRINCIPAL)
        for doc in (here, universal):
            assert v.permits(doc, workstream_id=stream.pk) is readable_documents(
                OPEN_PRINCIPAL, workstream_id=stream.pk).filter(pk=doc.pk).exists()


def test_cell_5_and_7_and_8_live_in_their_own_modules():
    """Cell 5 (a contained document opens from inside its stream and 404s
    from outside) is `test_containment_routes.py`; cells 7 and 8 (the
    re-labelled universal document, and the no-op guard) are
    `test_labels.py`, because both are properties of the one chunk-cache
    WRITER rather than of the filter. Named here so a reader working
    through §17.2 finds all eight."""
```

And the two `_visibility_filters` shape assertions:

```python
def test_a_loose_turn_excludes_contained_documents_with_one_is_empty_clause():
    with posture("open"):
        from identity.contracts.principals import OPEN_PRINCIPAL
        filters = _visibility_filters(None, document_visibility(OPEN_PRINCIPAL))
    keys = [(f.key, f.operator) for f in _flat(filters)]
    assert ("workstream", FilterOperator.IS_EMPTY) in keys


def test_the_filter_is_never_none_any_more():
    """§6.2's documented path change, asserted so it reads as intended
    rather than as a broken test somebody repaired: the `workstream`
    clause is appended in EVERY branch, so `clauses` is never empty and
    `_visibility_filters` never returns `None`. Harmless at the store (an
    AND of one group is the group), and it retires the "no category,
    unrestricted principal" fast path."""
    with posture("open"):
        from identity.contracts.principals import OPEN_PRINCIPAL
        assert _visibility_filters(None, document_visibility(OPEN_PRINCIPAL)) is not None
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tools/rag/tests/test_workstream_corpus.py -x -q`
Expected: FAIL — `document_visibility() got an unexpected keyword argument 'stream'` is already
fixed by Task 6, so the first real failure is the missing `workstream` clause: the matrix
disagrees and `test_the_filter_is_never_none_any_more` fails.

- [ ] **Step 3: Add the clause to `_visibility_filters`**

In `tools/rag/retrieval.py`, immediately before `if not clauses:`:

```python
    # CONTAINMENT AND THE WALL, at the one filter point (spec §6.2).
    #
    # OUTSIDE THE `if not visibility.unrestricted` BRANCH ABOVE:
    # containment is a CORPUS rule, not an entitlement rule, so it
    # applies in the open posture and to an administrator with
    # `admin_sees_content` on, exactly as it applies to everybody else.
    #
    # `workstream` is stamped on a contained document's chunks by the one
    # cache writer (`tools/rag/labels.py`, spec §8.3). ABSENT on every
    # chunk in every existing store, which is what `IS_EMPTY` matches --
    # so no back-fill.
    #
    # THE VALUE IS THE STREAM PK AS A DECIMAL STRING, matching the
    # `entitlements` convention documented above for the same reason:
    # `ANY` and `EQ` render as JSON *string* comparisons over
    # `metadata_`, so an integer stamped into the metadata would never
    # match `str(workstream_id)` here.
    if visibility.stream is None:
        clauses.append(MetadataFilter(key="workstream", value="",
                                      operator=FilterOperator.IS_EMPTY))
    else:
        legs = [
            # contained: this stream's own documents
            MetadataFilter(key="workstream", value=str(visibility.stream.workstream_id),
                           operator=FilterOperator.EQ),
        ]
        if visibility.stream.pinned_file_ids:
            legs.append(MetadataFilter(
                key="file_id",
                value=sorted(str(i) for i in visibility.stream.pinned_file_ids),
                operator=FilterOperator.ANY))
        universal = [MetadataFilter(key="workstream", value="",
                                    operator=FilterOperator.IS_EMPTY)]
        if visibility.stream.wall:
            # A NON-EMPTY WALL EXCLUDES UNLABELLED UNIVERSAL DOCUMENTS
            # (author decision 5): a document under no entitlement is
            # under none of the wall's. AND-ed into the universal leg
            # only -- the wall narrows what the library contributes and
            # does not un-admit what somebody deliberately contained or
            # pinned.
            universal.append(MetadataFilter(
                key="entitlements",
                value=sorted(str(i) for i in visibility.stream.wall),
                operator=FilterOperator.ANY))
        legs.append(MetadataFilters(filters=universal, condition=FilterCondition.AND))
        clauses.append(MetadataFilters(filters=legs, condition=FilterCondition.OR))
```

Extend the function's docstring with the paragraph the change earns:

```
    THIS FUNCTION NO LONGER RETURNS `None`. The `workstream` clause above
    is appended in EVERY branch, so `clauses` is never empty and every
    retrieval now carries a filter. Harmless at the store (an AND of one
    group is the group), and it retires the "no category, unrestricted
    principal" fast path deliberately rather than by accident. The
    `if not clauses: return None` line is kept as a structural
    impossibility rather than deleted, so a future branch that forgot to
    append still degrades to "no filter" the way the store expects
    rather than raising -- and `test_the_filter_is_never_none_any_more`
    pins that today's code cannot reach it.
```

**`sees_nothing` is unchanged and still returns early.** A principal who may see nothing sees
nothing in a stream too; the early return at `retrieve_nodes` is untouched.

- [ ] **Step 4: Rewrite the existing `None` assertions**

In `tools/rag/tests/test_retrieval_visibility.py`, the `test_visibility_filters` cases that assert
`_visibility_filters(...) is None` for "no category, unrestricted principal" now assert the
one-clause filter instead:

```python
def test_an_unrestricted_principal_with_no_category_still_carries_the_containment_clause():
    """WAS `assert filters is None`. §6.2's `workstream` clause is
    appended in every branch, so the fast path is retired: an
    unrestricted principal outside a stream still gets one filter, and
    that filter is what keeps contained documents out of Ask and
    Search. Rewritten deliberately, not repaired."""
    v = DocumentVisibility(unrestricted=True, entitlement_ids=frozenset(),
                           unlabelled_allowed=True)
    filters = _visibility_filters(None, v)
    assert filters is not None
    assert [(f.key, f.operator) for f in filters.filters] == [
        ("workstream", FilterOperator.IS_EMPTY)]
```

- [ ] **Step 5: Give `ToolContext` its field and thread it**

In `agents/contracts/tools.py`, in `ToolContext`, after `supplied_keys`:

```python
    # THE TURN'S STREAM SCOPE, or `None` for a loose turn and for every
    # caller with no conversation at all (an external MCP `tools/call`).
    # Defaulting to `None` is what keeps every existing runner and every
    # test that builds a bare context working -- and the behaviour they
    # get is the SAFE one, because `None` excludes contained documents
    # rather than including them.
    #
    # A DELEGATE INHERITS IT, the same way it inherits `principal` and
    # `tool_access` and for the same reason: a delegate is not a way
    # around a wall any more than it is a way around a label. The
    # instructions PROSE does not travel (author decision 21) and the
    # enforcement does, which spec §24 concern 2 records as a named
    # asymmetry.
    stream: "WorkstreamScope | None" = None
```

with `WorkstreamScope` added to this module's `TYPE_CHECKING` block —
`from agents.contracts.workstreams import WorkstreamScope` — so the rule-1 purity of the leaf is
unaffected either way (both files are pure).

In `agents/runtime/loop.py`, at the `ToolContext(...)` construction (line 371), add the field.
**This is reconciliation R2 / author decision 3: the spec says `invoke_tool` sets it, but
`invoke_tool` has only a `conversation_id` string and no row. The `ToolContext` is *built* here,
where `conversation` is in scope, alongside `agent_slug` and `tool_access`, which travel by
exactly the same mechanism.**

```python
        tool_ctx = ToolContext(
            conversation_id=str(conversation.id),
            principal=principal,
            depth=depth,
            budget=budget,
            job=job_ctx,
            agent_slug=agent.slug,
            # THE TURN'S STREAM SCOPE (spec §12.4), built ONCE per turn
            # by the caller and threaded here, never re-derived per tool
            # call. `None` for a loose conversation. A delegate's nested
            # `run_loop` receives the same value, so the wall travels
            # down every hop exactly as `principal` and `tool_access`
            # already do.
            stream=stream,
            ...
        )
```

`run_loop` gains a keyword-only `stream: WorkstreamScope | None = None` parameter, and
`_run_turn` computes it once, beside `access`:

```python
    from agents.workstreams import workstream_scope

    stream = (workstream_scope(principal, conversation.workstream_id)
              if conversation.workstream_id else None)
```

and passes `stream=stream` into `run_loop`. `agents/runtime/delegate.py`'s nested `run_loop` call
passes `stream=ctx.stream`.

- [ ] **Step 6: Let the two retrieval runners spend it**

In `tools/rag/tools.py`, in both `run_search` and `run_ask`, replace
`visibility=document_visibility(ctx.principal)` with:

```python
        visibility=document_visibility(
            ctx.principal,
            # THE PINS ARE FILLED HERE, on this side of the seam, because
            # the pin table is a `tools/rag` table and
            # `agents/workstreams.py` hands the stream half over with
            # `pinned_file_ids=frozenset()` (author decision 6).
            stream=scope_with_pins(ctx.stream) if ctx.stream is not None else None),
```

with `from tools.rag.workstreams import scope_with_pins` in the same lazy import block the runner
already uses for `document_visibility`.

- [ ] **Step 7: Run everything**

Run: `pytest tools/rag/tests/test_workstream_corpus.py -q`
Expected: PASS — 108 matrix cells, the non-vacuity pin, and the named ones.

Run: `pytest tools/rag/ agents/ -q`
Expected: PASS, with the rewritten `test_visibility_filters` cases.

- [ ] **Step 8: Commit**

```bash
git add tools/rag/retrieval.py tools/rag/tools.py agents/contracts/tools.py \
        agents/runtime/loop.py agents/runtime/delegate.py \
        tools/rag/tests/test_workstream_corpus.py tools/rag/tests/test_retrieval_visibility.py
git commit -m "feat(rag): containment and the wall at the one retrieval filter point"
```

---

### Task 10: seams two and three — the planner's intersection, the model half, and the picker

**Files:**
- Modify: `agents/entitlements.py` (`tool_access_for` gains `wall=`; `_wall_for` is added)
- Modify: `models/registry/access.py:92` (`model_access_for` gains `wall=`)
- Modify: `agents/runtime/jobs.py:93,103`, `agents/runtime/loop.py:172,221,232`,
  `agents/runtime/preflight.py:132,196,246`, `agents/chat/pickers.py:42`
- Modify (the three `preflight_turn` callers): `agents/chat/service.py:150`,
  `agents/chat/views/thread.py:75`, `agents/management/commands/agent_turn.py:141`
- Test: `agents/tests/test_wall_seams.py` (new)

**Interfaces:**
- Consumes: `agents.workstreams.wall_ids` indirectly, through `_wall_for` (Task 4);
  `Conversation.workstream` (Task 2).
- Produces: `tool_access_for(principal, *, settings_row=None, wall: frozenset[int] = frozenset())`;
  `model_access_for(principal, *, settings_row=None, wall: frozenset[int] = frozenset())`;
  `agents.entitlements._wall_for(conversation) -> frozenset[int]`;
  `chat_picker_options(principal, selected="", *, wall: frozenset[int] = frozenset())`.

- [ ] **Step 1: Write the failing seam tests**

Create `agents/tests/test_wall_seams.py`:

```python
"""The wall at seams two and three — the turn planner's entitlement
intersection and the model half, at all four call sites."""
from __future__ import annotations

import pytest

from agents.chat.pickers import chat_picker_options
from agents.entitlements import _wall_for, tool_access_for
from agents.models import Conversation, WorkstreamScopeEntitlement
from agents.tests._helpers import _workstream
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import grant, make_admin, make_entitlement, make_user, posture, user_principal
from models.registry.access import model_access_for

pytestmark = pytest.mark.django_db


def test_a_walled_turn_narrows_the_held_set_by_intersection_never_by_union():
    """`held & wall`, so a wall can only ever REMOVE entitlements from
    what the acting user already holds. It is never a grant."""
    user = make_user()
    e1, e2 = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    grant(e2, user=user)
    with posture("enterprise"):
        access = tool_access_for(user_principal(user), wall=frozenset({e1.pk}))
    assert access.held == frozenset({e1.pk})
    assert access.unrestricted is False


def test_a_wall_naming_an_entitlement_the_user_lacks_narrows_to_empty_not_to_it():
    user = make_user()
    e1, unheld = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    with posture("enterprise"):
        access = tool_access_for(user_principal(user), wall=frozenset({unheld.pk}))
    assert access.held == frozenset()


def test_and_not_wall_is_the_whole_of_the_change_to_the_unrestricted_branch():
    """AUTHOR DECISION 8, named after the line. Without `and not wall`,
    an administrator with `admin_sees_content` on would get
    `UNRESTRICTED_TOOL_ACCESS` and the wall would silently do NOTHING
    for tools while doing something for documents. A wall is not a
    permission check; it is a SCOPE THE OWNER CHOSE, and choosing it
    means choosing it for yourself as well.

    The one place a plausible refactor would silently undo the wall."""
    admin = make_admin()
    ent = make_entitlement()
    with posture("enterprise", admin_sees_content=True):
        unwalled = tool_access_for(user_principal(admin))
        walled = tool_access_for(user_principal(admin), wall=frozenset({ent.pk}))
    assert unwalled.unrestricted is True
    assert walled.unrestricted is False
    assert walled.held == frozenset()


def test_the_model_half_narrows_identically():
    user = make_user()
    e1, e2 = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    grant(e2, user=user)
    with posture("enterprise"):
        access = model_access_for(user_principal(user), wall=frozenset({e1.pk}))
    assert access.held == frozenset({e1.pk})
    assert access.unrestricted is False


def test_wall_for_reads_the_table_on_an_accounts_box(agent):
    user = make_user()
    ent = make_entitlement()
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    with posture("enterprise"):
        assert _wall_for(conversation) == frozenset({ent.pk})


def test_wall_for_returns_empty_on_an_open_box_without_reading_the_table(
        agent, django_assert_num_queries):
    """RULING A, THE ANTI-BRICKING HALF. A stream carrying wall rows
    written under `enterprise`, run on an open box, gets
    `UNRESTRICTED_TOOL_ACCESS`, starts its turns and refuses nothing —
    and reads ZERO permission rows to decide it."""
    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    with posture("open"):
        with django_assert_num_queries(1):     # the posture singleton, and nothing else
            wall = _wall_for(conversation)
        assert wall == frozenset()
        assert tool_access_for(OPEN_PRINCIPAL, wall=wall).unrestricted is True


def test_a_loose_conversation_has_no_wall_and_costs_no_query(agent, django_assert_num_queries):
    conversation = Conversation.objects.create(agent=agent)
    with posture("enterprise"):
        with django_assert_num_queries(0):
            assert _wall_for(conversation) == frozenset()


def test_all_four_call_sites_narrow(monkeypatch, agent):
    """§17.3: one parametrised test over `plan_turn`, `_run_turn`,
    `preflight_turn` and `chat_picker_options`, because §6.3's whole
    correction was that an earlier draft named two of them.

    Asserted by RECORDING the `wall=` each site passes, rather than by
    four separate end-to-end refusals: the property under test is that
    every site threads the same value, and four refusals would pass even
    if one site computed its own."""
    seen = []

    def _record(principal, *, settings_row=None, wall=frozenset()):
        seen.append(frozenset(wall))
        return _real_tool_access(principal, settings_row=settings_row, wall=wall)

    from agents import entitlements as ent_module
    from agents.runtime import jobs, loop as loop_module, preflight

    _real_tool_access = ent_module.tool_access_for
    for module in (ent_module, jobs, loop_module, preflight):
        monkeypatch.setattr(module, "tool_access_for", _record, raising=False)

    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = Conversation.objects.get(pk=conversation.pk).turns.create(
        index=0, role="assistant", text="", state="queued", depth=0)
    payload = {"turn": turn.pk, **payload_fields(user_principal(user))}

    with posture("enterprise"):
        jobs.plan_turn(payload)
        preflight.preflight_turn(agent, "", actor=user_principal(user),
                                 conversation=conversation)
        # `_run_turn(payload, models, ctx)` -- it resolves the turn, the
        # conversation and the agent itself from `payload["turn"]`
        # (`agents/runtime/loop.py:171-174`), so nothing is handed to it.
        with patch.object(loop_module.gateway, "get_llm_for",
                          return_value=_llm_answering("hi")):
            loop_module._run_turn(payload, [], make_job_ctx())

    # THREE runtime sites, all narrowed with the SAME non-empty value.
    assert len(seen) == 3
    assert set(seen) == {frozenset({ent.pk})}

    # The fourth site is the RENDER half, and it narrows the model axis
    # rather than the tool axis -- so it is asserted on its own value.
    model_seen = []
    _real_model_access = pickers.model_access_for
    monkeypatch.setattr(
        pickers, "model_access_for",
        lambda p, *, settings_row=None, wall=frozenset(): (
            model_seen.append(frozenset(wall))
            or _real_model_access(p, settings_row=settings_row, wall=wall)))
    with posture("enterprise"):
        chat_picker_options(user_principal(user), wall=_wall_for(conversation))
    assert model_seen == [frozenset({ent.pk})]


def test_the_picker_omits_exactly_what_the_walled_turn_would_refuse():
    """THE RENDER-VS-GATE PAIR, asserted AS A PAIR, on the surface the
    wall is most visible on. `chat_picker_options`' own docstring says a
    connection the principal may not use "is never in the offered
    options"; unnarrowed it would offer models the walled turn then
    refuses."""
    user = make_user()
    e1, e2 = make_entitlement(), make_entitlement()
    grant(e1, user=user)
    grant(e2, user=user)
    # THE WORLD IS BUILT HERE. Without it `picker_options` returns an
    # empty list, the loop never runs, and the test passes with zero
    # assertions on the one property this task exists to prove.
    #
    # `models/registry/tests/_helpers.py` has NO model-set builder --
    # the real one is `_set_with` at `models/registry/tests/
    # test_model_sets.py:91`, a module-private function in a test module
    # -- so this is a standalone copy, the convention
    # `tools/rag/tests/test_jobs.py:41-45` documents.
    inside = _connection_in_set_attached_to(e1, name="inside the wall")
    outside = _connection_in_set_attached_to(e2, name="outside the wall")

    with posture("enterprise"):
        offered = chat_picker_options(user_principal(user), wall=frozenset({e1.pk})) or []
        access = model_access_for(user_principal(user), wall=frozenset({e1.pk}))

    # BOTH DIRECTIONS, so the test fails if the wall stops narrowing as
    # readily as if it starts over-narrowing.
    assert {o["value"] for o in offered} == {str(inside.pk)}
    assert access.allows(inside.pk) is True
    assert access.allows(outside.pk) is False


def _connection_in_set_attached_to(entitlement, *, name):
    """One chat-capable `ModelConnection`, in a `ModelSet` attached to
    `entitlement`, bound to the chat role."""
    from models.contracts.roles import CHAT_CONVERSE_ROLE
    from models.registry.models import (
        ModelConnection, ModelSet, ModelSetEntitlement, ModelSetMember, RoleBinding,
    )

    connection = ModelConnection.objects.create(
        name=name, engine="test-inference", endpoint="http://fake-inference:1",
        model_id="a-chat-model", capabilities=["chat"])
    model_set = ModelSet.objects.create(name=f"set for {name}")
    ModelSetMember.objects.create(model_set=model_set, connection=connection)
    ModelSetEntitlement.objects.create(model_set=model_set, entitlement=entitlement)
    RoleBinding.objects.get_or_create(role_key=CHAT_CONVERSE_ROLE,
                                      defaults={"connection": connection})
    return connection
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest agents/tests/test_wall_seams.py -v`
Expected: FAIL — `tool_access_for() got an unexpected keyword argument 'wall'`

- [ ] **Step 3: Narrow `tool_access_for`, and add `_wall_for`**

Rewrite `agents/entitlements.py`'s function and append the helper:

```python
def tool_access_for(principal, *, settings_row=None,
                    wall: frozenset[int] = frozenset()) -> ToolAccess:
    """... (existing docstring, plus:)

    `wall` is the WORKSTREAM SCOPE of the turn this access is being built
    for -- empty for a loose turn and for every non-stream caller, so
    every existing call site keeps its exact meaning. It narrows the
    HELD half by INTERSECTION and never by union: a wall can only remove
    entitlements from what the acting user already holds, and is never a
    grant (spec §6.1).

    `and not wall` IS THE WHOLE OF THE CHANGE TO THE UNRESTRICTED BRANCH,
    and it is the one subtle line here (author decision 8). Without it,
    an administrator with `admin_sees_content` on would get
    `UNRESTRICTED_TOOL_ACCESS` and the wall would silently do nothing for
    tools while doing something for documents. A wall is not a permission
    check on somebody else; it is a scope somebody CHOSE, and choosing it
    means choosing it for yourself as well. The alternative -- a wall
    that binds documents but not tools for privileged readers -- would
    make the wall's meaning depend on who is looking, which is exactly
    what a scope must not do.

    BOUNDED BY RULING A: on an open box `_wall_for` returns the empty set
    without reading the wall table, so this takes the `sees_all_content
    and not wall` branch and runs zero permission queries, exactly as it
    did before this phase.
    """
    row = settings_row if settings_row is not None else _identity_settings_row()
    if sees_all_content(principal, settings_row=row) and not wall:
        return UNRESTRICTED_TOOL_ACCESS
    held = held_entitlement_ids(principal, settings_row=row)
    return ToolAccess(
        required=tool_entitlement_ids(),
        held=(held & wall) if wall else held,
        unrestricted=False,
    )


def _wall_for(conversation) -> frozenset[int]:
    """This conversation's stream wall, or the empty set.

    HERE, BESIDE `tool_access_for` (author decision 9), because this
    module already owns "the one place a turn's access axes are built"
    and because the ruling-A posture branch must live in a reader of one
    table, in the runtime -- NOT in a `visible_*` function, whose bodies
    stay posture-free exactly as IA-2 left them (spec §13).

    ONE INDEXED READ PER STREAM TURN, over `uniq_workstream_scope`, and
    the value is THREADED from here through all four call sites of §6.3
    rather than re-read at each. Zero queries for a loose turn, and zero
    on an open box, where `agents.workstreams.wall_ids` short-circuits
    on `accounts_on()` before touching the table (ruling A).
    """
    if conversation.workstream_id is None:
        return frozenset()
    from agents.workstreams import wall_ids

    return wall_ids(conversation.workstream_id)
```

- [ ] **Step 4: Narrow `model_access_for`**

In `models/registry/access.py`:

```python
def model_access_for(principal, *, settings_row=None,
                     wall: frozenset[int] = frozenset()) -> ModelAccess:
    """... (existing docstring, plus:)

    `wall` is the workstream scope of the turn this access is being built
    for -- a PLAIN FROZENSET, empty by default, lawful under every
    existing import sweep and adding no import in either direction. It
    narrows the held half identically to `agents.entitlements.
    tool_access_for`'s, for the identical reason: a wall is a scope
    somebody chose, and a scope whose meaning depended on who was looking
    would not be one. This is the ONE change the workstreams phase makes
    in this column, and `models/README.md` records why a reader of this
    column finds a workstream concept in it at all (spec §6.3, §20).
    """
    if sees_all_content(principal, settings_row=settings_row) and not wall:
        return UNRESTRICTED_MODEL_ACCESS
    held = held_entitlement_ids(principal, settings_row=settings_row)
    return ModelAccess(
        required=connection_entitlement_ids(),
        held=(held & wall) if wall else held,
        unrestricted=False,
    )
```

`models/registry/bindings.py` needs **no edit**: it re-exports the function object at `bindings.py:85`,
so a keyword-only parameter is transparent through the re-export (author decision 19).

- [ ] **Step 5: Thread the value at all four call sites**

`agents/runtime/jobs.py::plan_turn` — compute once, use twice, and widen the `select_related`:

```python
    turn = Turn.objects.select_related(
        "conversation__agent", "conversation__workstream").get(pk=payload["turn"])
    agent = turn.conversation.agent
    ...
    # ONE READ, TWO AXES (spec §13's "the wall value is threaded from
    # here through all four call sites rather than re-read at each").
    wall = _wall_for(turn.conversation)
    model_access = model_access_for(actor, wall=wall)
    ...
    access = tool_access_for(actor, wall=wall)
```

`agents/runtime/loop.py::_run_turn` — **and its own `select_related` widens too**, at
`loop.py:172`. This is a second, independent fetch from `plan_turn`'s: they are two different
job-kind hooks (`planner="agents.runtime.jobs.plan_turn"`,
`handler="agents.runtime.loop.run_turn"`, `agents/apps.py:59-60`) running in different processes,
and `_run_turn`'s own line reads `select_related("conversation__agent")` with no `workstream`. Both
`_wall_for(conversation)` here and `build_messages`' `conversation.workstream` (Task 12) read off
this row, so without the widening every stream turn pays an extra query twice:

```python
    turn = Turn.objects.select_related(
        "conversation__agent", "conversation__workstream").get(pk=payload["turn"])
```

then, beside the `settings` local it already builds:

```python
    wall = _wall_for(conversation)
    access = tool_access_for(principal, settings_row=settings, wall=wall)
    ...
    model_access = model_access_for(principal, settings_row=settings, wall=wall)
```

`agents/runtime/preflight.py::preflight_turn` — **both** calls, and this is the site §17.3 asserts
against, because without it "refused at `preflight_turn`, before a turn row is written" is
impossible and the refusal lands mid-run:

```python
    wall = _wall_for(conversation)
    access = model_access_for(actor, settings_row=settings, wall=wall)
    ...
    access = tool_access_for(actor, settings_row=settings, wall=wall)
```

**`preflight_turn` does NOT take the conversation, and it has three production callers, not one.**
Its real signature is `def preflight_turn(agent, connection, *, actor) -> Preflight:`
(`agents/runtime/preflight.py:132`), and its own docstring's "Both callers of this function" is
itself already one short. So it gains a keyword-only `conversation=None`:

```python
def preflight_turn(agent, connection, *, actor, conversation=None) -> Preflight:
    """... (existing docstring, plus:)

    `conversation` is the row this turn will be posted to, or `None` for a
    caller that has none. IT DECIDES THE WALL: a turn in a workstream is
    narrowed at this seam as well as at the planner and the loop, because
    without it spec §17.3's *"refused at `preflight_turn`, before a turn
    row is written"* is impossible -- the turn row gets written and the
    refusal lands mid-run, which `agents/runtime/jobs.py` records as the
    wrong order to fail in.

    `None` MEANS NO WALL, not "look it up": this function is the render
    half for two of its three callers, and a default that queried would
    make a page render pay for a stream it may not even be on.
    """
    ...
    wall = _wall_for(conversation) if conversation is not None else frozenset()
```

and **all three callers pass the row they already hold**:

| Caller | Change |
|---|---|
| `agents/chat/service.py:150` | `preflight_turn(agent, connection, actor=actor, conversation=conversation)` — `start_turn`'s own first parameter |
| `agents/chat/views/thread.py:75` | `preflight_turn(conversation.agent, selected, actor=principal, conversation=conversation)` — the render half, which must agree with the turn it previews |
| `agents/management/commands/agent_turn.py:141` | `preflight_turn(agent, connection, actor=SERVICE_PRINCIPAL, conversation=conversation)` — from the row the command already resolves |

**The shell path is not cosmetic.** Without its edit, a stream turn started by `manage.py
agent_turn` would preflight with `wall=frozenset()` and silently bypass seams two and three — a
walled stream whose refusals depend on which door the turn came through.

`agents/chat/pickers.py::chat_picker_options` — the render half:

```python
def chat_picker_options(principal, selected: str = "", *,
                        wall: frozenset[int] = frozenset()) -> list[dict] | None:
    """... (existing docstring, plus:)

    `wall` is the stream scope of the conversation whose page this picker
    is on, and empty everywhere else. THE RENDER HALF OF THE WALL: this
    function's own contract is that a connection the principal may not
    use "is never in the offered options", and unnarrowed it would offer
    models the walled turn then refuses -- breaking the render-vs-gate
    pair on the surface the wall is most visible on.
    """
    try:
        return picker_options("chat", CHAT_CONVERSE_ROLE, selected,
                              access=model_access_for(principal, wall=wall))
    except Exception:  # noqa: BLE001 -- log detail, then degrade to no picker
        logger.exception("chat: could not build the model picker's options")
        return None
```

Its two callers (the conversation page and the index) pass `wall=_wall_for(conversation)` where
they have a conversation and nothing where they do not.

**The refusal copy is unchanged.** A tool walled off from a stream is dropped exactly as a tool
the principal lacks the entitlement for is dropped — silently, from `granted_tools`, logged at
`DEBUG` — and a walled-off model is refused with the existing sentence. Spec §24 concern 1 records
the one place that sentence is now slightly less than true and why this phase does not change it.

- [ ] **Step 6: Add the query-count pins**

Append to `agents/tests/test_wall_seams.py`:

```python
def test_a_stream_turn_on_a_personal_box_reads_the_wall_exactly_once(
        agent, django_assert_num_queries):
    """§13's honest number, pinned as an EQUALITY rather than an
    inequality: the wall is read once per stream turn, over
    `uniq_workstream_scope`, WHATEVER the number of seams that consume
    the value — which is what "threaded, not re-read" means."""
    user = make_user()
    ent = make_entitlement()
    grant(ent, user=user)
    stream = _workstream(user_principal(user))
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = conversation.turns.create(index=0, role="assistant", text="",
                                     state="queued", depth=0)

    with posture("personal"):
        with CaptureQueriesContext(connection) as captured:
            jobs.plan_turn({"turn": turn.pk, **payload_fields(user_principal(user))})

    assert _hits(captured, "agents_workstreamscopeentitlement") == 1


def test_a_stream_turn_on_an_open_box_reads_no_permission_table_at_all(agent):
    """§19.1 done-when 9, the query half: ZERO queries against
    `EntitlementGrant`, `WorkstreamScopeEntitlement` and (in WS-2)
    `WorkstreamTaint`. Ruling A is what makes this true — `_wall_for`
    short-circuits on `accounts_on()` before touching the table, so
    deciding `not wall` never costs a read."""
    ent = make_entitlement()
    stream = _workstream()
    WorkstreamScopeEntitlement.objects.create(workstream=stream, entitlement=ent)
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = conversation.turns.create(index=0, role="assistant", text="",
                                     state="queued", depth=0)

    with posture("open"):
        with CaptureQueriesContext(connection) as captured:
            jobs.plan_turn({"turn": turn.pk})

    for table in ("identity_entitlementgrant", "agents_workstreamscopeentitlement"):
        assert _hits(captured, table) == 0, table


def _hits(captured, table: str) -> int:
    """How many captured statements name `table`.

    ASSERTED ON THE TABLE NAME IN THE SQL, not on a total count, which is
    what makes the pin non-vacuous: a total would move with every
    unrelated query the platform adds, and the property under test is
    about ONE table.
    """
    return sum(1 for q in captured.captured_queries if table in q["sql"])
```

`CaptureQueriesContext` is `django.test.utils.CaptureQueriesContext`, the same inspection
`agents/runtime/tests/` already uses for its own counts. **`agents_workstreamtaint` is deliberately
NOT in that tuple**: the table does not exist until Task 16, and author decision 1 says nothing in
Tasks 1–15 references a symbol Tasks 16–19 introduce — an assertion that is vacuous for the whole
of WS-1 is worse than one added where it bites. Task 16 Step 11 adds it.

- [ ] **Step 7: Run everything**

Run: `pytest agents/tests/test_wall_seams.py -v`
Expected: PASS

Run: `pytest agents/ models/ tools/ -q`
Expected: PASS. Every existing `tool_access_for(principal)` and `model_access_for(principal)` call
keeps its meaning: `wall` defaults to empty and the unrestricted branch is unchanged when it is.

- [ ] **Step 8: Update `models/README.md`**

One paragraph: `model_access_for`'s new `wall` parameter, that it is the one change this phase
makes in this column, and why a reader of `models/` finds a workstream concept in it at all.

- [ ] **Step 9: Commit**

```bash
git add agents/entitlements.py models/registry/access.py agents/runtime/jobs.py \
        agents/runtime/loop.py agents/runtime/preflight.py agents/chat/pickers.py \
        agents/chat/service.py agents/chat/views/thread.py \
        agents/management/commands/agent_turn.py \
        agents/tests/test_wall_seams.py models/README.md
git commit -m "feat(agents): the wall at the planner, the loop, preflight and the picker"
```

---

### Task 11: upload placement is a conscious choice

**Files:**
- Modify: `tools/rag/ingest.py` (`stage_document`, `enqueue_ingest` gain one keyword each)
- Modify: `tools/rag/views.py::document_upload` (the `placement` field, the 400, `remember_placement`)
- Modify: `tools/rag/templates/rag/documents.html` (the three-state placement control)
- Test: `tools/rag/tests/test_upload_placement.py` (new)

**Interfaces:**
- Consumes: `agents.workstreams.workstream_scope`, `set_upload_placement_default` (Task 4);
  `Document.workstream` (Task 5).
- Produces: `stage_document(path, category=None, *, move, workstream_id=None)`;
  `enqueue_ingest(path, category=None, *, move, actor, workstream_id=None)`; the
  `placement_state` context value (`"none"` / `"choose"` / `"default"`).

- [ ] **Step 1: Write the failing placement test**

Create `tools/rag/tests/test_upload_placement.py`:

```python
"""Owner decision 5: where a document lives is a decision, and a decision
that defaults silently is a decision nobody made."""
from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tools.rag.models import Document
from tools.rag.tests._helpers import _workstream
from identity.access import owner_fields
from identity.testing import make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def _upload(client, **fields):
    return client.post(reverse("rag-document-upload"), {
        "files": SimpleUploadedFile("note.md", b"hello"), **fields})


def test_an_upload_outside_a_stream_is_unchanged_and_offers_no_choice(client):
    """Everything outside a stream is unchanged, including its actor: the
    watcher, `manage.py ingest`, the `rag.ingest` tool runner and the
    library page's own form all produce universal documents, exactly as
    today. The DEFAULT PARAMETER is what makes that true without a single
    caller edit."""
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client)
    assert Document.objects.get().workstream_id is None


def test_an_in_stream_upload_with_neither_radio_picked_is_a_400_naming_the_field(client):
    """AUTHOR DECISION 17. HTML's `required` on a radio group is the
    RENDER half; the view is the GATE half, and this is the one field
    where a server-side fallback would silently undo an owner decision,
    so there is none."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = _upload(client, workstream=str(stream.pk))
    assert response.status_code == 400
    assert "placement" in response.content.decode().lower()
    assert Document.objects.count() == 0


def test_an_unrecognised_placement_is_the_same_400_not_a_fallback(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        response = _upload(client, workstream=str(stream.pk), placement="sideways")
    assert response.status_code == 400
    assert Document.objects.count() == 0


@pytest.mark.parametrize("placement,contained", [("universal", False), ("contained", True)])
def test_the_chosen_placement_is_what_the_row_gets(client, placement, contained):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client, workstream=str(stream.pk), placement=placement)
    doc = Document.objects.get()
    assert (doc.workstream_id == stream.pk) is contained


def test_remembering_the_choice_sets_the_streams_default_and_audits_it(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client, workstream=str(stream.pk), placement="contained",
                remember_placement="1")
    stream.refresh_from_db()
    assert stream.default_upload_placement == "contained"


def test_with_a_default_active_the_form_carries_no_placement_and_the_view_reads_it(client):
    """State three: no radios. One line — "Placed in this workstream only
    per this workstream's default." — with **change**, a link to the
    stream page's upload-default control."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)),
                         default_upload_placement="contained")
    with posture("enterprise"):
        sign_in(client, user)
        response = _upload(client, workstream=str(stream.pk))
    assert response.status_code in (302, 200)
    assert Document.objects.get().workstream_id == stream.pk


def test_a_stream_the_uploader_may_not_upload_to_is_a_404(client):
    """`may_upload` is False for a share recipient, and the route is
    row-addressed, so this is a 404 and not a 403."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, reader)
        response = _upload(client, workstream=str(stream.pk), placement="contained")
    assert response.status_code == 404
    assert Document.objects.count() == 0


def test_placement_and_labels_are_independent(client):
    """"Placement decides where it lives. Labels decide who may read it."
    A contained document MAY carry labels, and should, if its content
    warrants them: containment is organisation, labels are access."""
    user = make_user()
    from identity.testing import grant, make_entitlement
    ent = make_entitlement()
    grant(ent, user=user, role="owner")
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        _upload(client, workstream=str(stream.pk), placement="contained",
                entitlements=str(ent.pk))
    doc = Document.objects.get()
    assert doc.workstream_id == stream.pk
    assert doc.entitlement_labels.count() == 1


def test_a_re_stage_does_not_re_read_the_placement():
    """"A document's home is set when it is first staged and a later
    content change re-ingests it in place" (spec §9.2). Asserted at
    `stage_document`, because that is where the rule lives."""
    from tools.rag import ingest

    stream = _workstream()
    path = _write_temp("note.md", "one")
    doc, changed = ingest.stage_document(str(path), None, move=False,
                                         workstream_id=stream.pk)
    assert changed and doc.workstream_id == stream.pk
    _write_temp("note.md", "two")
    doc2, changed2 = ingest.stage_document(str(path), None, move=False)
    assert changed2 and doc2.pk == doc.pk
    assert doc2.workstream_id == stream.pk       # unchanged, not cleared
```

`_write_temp` is a small local helper writing into `tmp_path`; the existing `test_ingest.py`
already has one of the same shape — reuse its name and body rather than inventing a second.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tools/rag/tests/test_upload_placement.py -v`
Expected: FAIL — `stage_document() got an unexpected keyword argument 'workstream_id'`

- [ ] **Step 3: Thread `workstream_id` through the two ingest entry points**

`stage_document` gains the keyword and applies it **only on creation**:

```python
def stage_document(path: str, category: str | None = None, *, move: bool,
                   workstream_id=None) -> tuple[Document, bool]:
    """... (existing docstring, plus:)

    `workstream_id` IS CONTAINMENT (spec §9.2, owner decision 4).
    Keyword-only, defaulting to `None`, which is what keeps the watcher,
    `manage.py ingest`, the `rag.ingest` tool runner and the library
    page's own upload form producing universal documents with not a
    single caller edit.

    IT IS APPLIED AT CREATION AND NOT RE-READ ON A RE-STAGE. A
    document's home is set when it is first staged, and a later content
    change re-ingests it IN PLACE -- so the re-stage branch below leaves
    `workstream` exactly as it found it. Re-homing is not an action this
    product has (§21.2, §22's move-to-stream), and a re-stage that
    silently moved a document would be one.
    """
```

In the `else:` branch of the existing `if existing is not None:` / `else:` pair, add
`workstream_id=workstream_id,` to the `Document.objects.create(...)` call. **Do not** touch the
re-stage branch.

`enqueue_ingest` gains the pass-through:

```python
def enqueue_ingest(path: str, category: str | None = None, *, move: bool, actor,
                   workstream_id=None) -> int | None:
    """... (existing docstring, plus:)

    `workstream_id` is forwarded to `stage_document` -- see that
    function's docstring for what it does and what it deliberately does
    not do on a re-stage.
    """
    doc, changed = stage_document(path, category, move=move, workstream_id=workstream_id)
```

- [ ] **Step 4: Give `document_upload` the placement gate**

In `tools/rag/views.py::document_upload`, before the per-file loop:

```python
    # THE STREAM, IF THIS UPLOAD IS INSIDE ONE (spec §9.1). Resolved
    # through the seam, which answers `None` for a stream this principal
    # may not be in AND for one that does not exist -- the caller cannot
    # tell them apart, and answers 404 to both.
    from agents.workstreams import set_upload_placement_default, workstream_scope

    scope = None
    raw_stream = request.POST.get("workstream", "")
    if raw_stream:
        scope = workstream_scope(principal, int(raw_stream)) \
            if raw_stream.isdecimal() else None
        if scope is None or not scope.may_upload:
            raise Http404("No such workstream.")

    # THE THREE STATES OF THE FORM, resolved to ONE value (author
    # decision 18) rather than three booleans a template could combine
    # into a fourth state that does not exist.
    placement = None
    if scope is not None:
        if scope.default_upload_placement:
            placement = scope.default_upload_placement
        else:
            placement = request.POST.get("placement", "")
            if placement not in Workstream_PLACEMENTS:
                # NEITHER RADIO PRESELECTED, AND THE VIEW ENFORCES IT
                # (author decision 17). An absent or unrecognised
                # `placement` on an in-stream upload is a 400 NAMING THE
                # FIELD, never a fallback to either value: this is the
                # one field where a server-side fallback would silently
                # undo an owner decision.
                return HttpResponseBadRequest(
                    "Choose where this file should live — the universal library, or this "
                    "workstream only. The 'placement' field is required for an upload "
                    "inside a workstream."
                )
            if request.POST.get("remember_placement"):
                set_upload_placement_default(principal, scope.workstream_id, placement)
```

with, at module scope in `tools/rag/views.py`:

```python
# The two placement values, as plain strings. NOT imported from
# `agents.models.Workstream.UploadPlacement`: `tools/rag` may reach
# `agents` only through the three named seams, and a choices enum on a
# model is not one of them. Two literals beside the one view that reads
# them, pinned equal to the model's own values by
# `tools/rag/tests/test_upload_placement.py`.
Workstream_PLACEMENTS = ("universal", "contained")
```

Rename that constant to `_PLACEMENTS` when writing it — the mixed-case spelling above is only to
make the diff obvious in this plan. Add the pin:

```python
def test_the_views_placement_literals_equal_the_models_own_values():
    """Two literals beside the view that reads them, because `tools/rag`
    may reach `agents` only through the three named seams. Pinned equal
    here so the pair cannot drift."""
    from django.apps import apps

    from tools.rag.views import _PLACEMENTS

    model = apps.get_model("agents", "Workstream")
    assert set(_PLACEMENTS) == set(model.UploadPlacement.values)
```

And in the per-file loop, the one changed call:

```python
            ingest.enqueue_ingest(
                str(dest), category or None, move=True, actor=principal,
                workstream_id=(scope.workstream_id
                               if placement == "contained" else None))
```

- [ ] **Step 5: Render the three states**

In `rag/documents.html`'s upload form (and the stream page's own copy, Task 13), add:

```html
{% if placement_state == "choose" %}
  <fieldset class="placement">
    <legend>Where should this live?</legend>
    <label><input type="radio" name="placement" value="universal" required>
      The universal library</label>
    <label><input type="radio" name="placement" value="contained" required>
      This workstream only</label>
    <label class="remember"><input type="checkbox" name="remember_placement" value="1">
      Use this choice for all future uploads to this workstream</label>
    <p class="hint">Neither is preselected. Tick the box to set a default for this
      workstream, so you are not asked again.</p>
  </fieldset>
  <p class="hint">Placement decides where it lives. Labels decide who may read it.</p>
{% elif placement_state == "default" %}
  <p class="placement-default">
    {% if workstream.default_upload_placement == "contained" %}
      Placed in this workstream only, per this workstream’s default.
    {% else %}
      Placed in the universal library, per this workstream’s default.
    {% endif %}
    <a href="{% url 'chat-workstream' workstream.pk %}#upload-default">change</a>
  </p>
{% endif %}
{% if workstream %}<input type="hidden" name="workstream" value="{{ workstream.pk }}">{% endif %}
```

`placement_state` is `"none"` outside a stream, and the block renders nothing.

- [ ] **Step 6: Run everything**

Run: `pytest tools/rag/tests/test_upload_placement.py -v`
Expected: PASS (10 tests)

Run: `pytest tools/rag/ -q`
Expected: PASS — the watcher tests, `manage.py ingest`'s tests and the `rag.ingest` runner's tests
all still produce universal documents, because they never pass the keyword.

- [ ] **Step 7: Record the watcher race**

In `tools/rag/README.md`, beside the containment section, name spec §24 concern 4 as a known,
narrow, correctness (not security) bug: if the watcher wins the inbox race, the document is staged
**universal**, because the watcher knows nothing about streams. Name the window
(`STABLE_AFTER_SECONDS = 5`), the consequence (a document in the library rather than a document
leaked), and the shape of the fix that is deliberately not taken here.

- [ ] **Step 8: Commit**

```bash
git add tools/rag/ingest.py tools/rag/views.py tools/rag/templates/rag/documents.html \
        tools/rag/tests/test_upload_placement.py tools/rag/README.md
git commit -m "feat(rag): upload placement is a conscious choice, with no silent default"
```

---

### Task 12: instructions in the system prompt

**Files:**
- Modify: `agents/runtime/prompt.py` (`build_messages` gains one block; two module constants)
- Test: `agents/runtime/tests/test_prompt.py` (append)

**Interfaces:**
- Consumes: `Conversation.workstream` (Task 2).
- Produces: `agents.runtime.prompt._INSTRUCTIONS_HEADER` (a constant a test can assert on) and
  `_instructions_block(stream) -> str`.

- [ ] **Step 1: Write the failing prompt test**

Append to `agents/runtime/tests/test_prompt.py`:

```python
def test_a_stream_conversations_system_message_carries_the_instructions_block(agent):
    """Owner decision 9: one place, one block, VISIBLY the stream's words
    and not the agent's."""
    from agents.runtime.prompt import _INSTRUCTIONS_HEADER, build_messages

    stream = _workstream(name="Q3", instructions="Cite the source of every figure.")
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    messages = build_messages(agent, conversation)
    system = messages[0]
    assert system.role == MessageRole.SYSTEM
    assert agent.system_prompt in system.content
    assert _INSTRUCTIONS_HEADER in system.content
    assert "Cite the source of every figure." in system.content


def test_the_block_is_appended_not_sent_as_a_second_system_message(agent):
    """Some engines collapse, reorder or drop a second system message;
    ONE message with two labelled parts behaves identically on every
    engine and is what the operator sees when they read the prompt
    back."""
    from agents.runtime.prompt import build_messages

    stream = _workstream(instructions="Be terse.")
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    messages = build_messages(agent, conversation)
    assert sum(1 for m in messages if m.role == MessageRole.SYSTEM) == 1


def test_a_blank_agent_prompt_with_non_blank_instructions_still_emits_a_system_message(agent):
    """THE BEHAVIOUR CHANGE, encoded in the guard. `build_messages`'
    existing rule — "a blank system prompt emits no system message at
    all, because an empty system message is not neutral" — is preserved
    IN ITS OWN TERMS: the message is emitted when there is SOMETHING TO
    SAY, and stream instructions are something to say."""
    from agents.runtime.prompt import build_messages

    agent.system_prompt = ""
    agent.save(update_fields=["system_prompt"])
    stream = _workstream(instructions="Only answer from the workstream's documents.")
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    messages = build_messages(agent, conversation)
    assert messages[0].role == MessageRole.SYSTEM
    assert "Only answer from the workstream's documents." in messages[0].content


def test_a_blank_agent_prompt_with_blank_instructions_still_emits_nothing(agent):
    from agents.runtime.prompt import build_messages

    agent.system_prompt = ""
    agent.save(update_fields=["system_prompt"])
    stream = _workstream(instructions="   ")
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    messages = build_messages(agent, conversation)
    assert all(m.role != MessageRole.SYSTEM for m in messages)


def test_a_loose_conversation_is_byte_identical_to_before_this_phase(agent):
    from agents.runtime.prompt import build_messages

    conversation = Conversation.objects.create(agent=agent)
    messages = build_messages(agent, conversation)
    assert messages[0].content == agent.system_prompt


def test_the_block_never_describes_the_wall_the_taint_or_what_may_not_be_retrieved(agent):
    """§6.5, asserted rather than promised. A wall a model is asked to
    respect is not a wall — enforcement is three server-side seams, and
    `agents/runtime/loop.py`'s own "NO PROMPT-HACKING, EVER" docstring is
    the standing version of this rule."""
    from agents.runtime.prompt import _INSTRUCTIONS_HEADER

    lowered = _INSTRUCTIONS_HEADER.lower()
    for forbidden in ("entitlement", "scope", "may not", "cannot see", "taint", "restricted"):
        assert forbidden not in lowered
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest agents/runtime/tests/test_prompt.py -k instructions -v`
Expected: FAIL — `cannot import name '_INSTRUCTIONS_HEADER'`

- [ ] **Step 3: Add the block**

In `agents/runtime/prompt.py`, at module scope beside the other constants:

```python
# How a workstream's standing instructions reach the model. A CONSTANT,
# so a test can assert the model was told WHOSE words these are (owner
# decision 9) -- and so the label cannot drift from what the stream page
# tells the operator it says.
#
# IT NEVER DESCRIBES THE WALL, THE TAINT, OR WHAT THE MODEL MAY NOT
# RETRIEVE (spec §6.5). A wall a model is asked to respect is not a
# wall: enforcement is three server-side seams -- a filter clause, an
# entitlement intersection and a queryset -- every one of which runs in
# the web or worker process against the database, and none of which the
# model can talk its way past. This module's neighbour
# (`agents/runtime/loop.py`) carries the standing version of that rule
# in its own "NO PROMPT-HACKING, EVER" docstring.
_INSTRUCTIONS_HEADER = (
    "Workstream instructions — the operator's standing instructions for this workstream:"
)


def _instructions_block(stream) -> str:
    """The stream's instructions under their labelled header.

    A NAMED FUNCTION, not an inline f-string, because spec §24 concern 2
    records a one-line remedy that depends on it: if the owner ever wants
    a delegate to inherit the stream's instructions prose (it inherits
    the ENFORCEMENT today, through `ToolContext.stream`), the change is
    one call to this function in `agents/runtime/delegate.py`.
    """
    return f"{_INSTRUCTIONS_HEADER}\n{stream.instructions.strip()}"
```

and in `build_messages`:

```python
    messages: list[ChatMessage] = []
    system = agent.system_prompt
    stream = conversation.workstream            # NULL for a loose conversation
    if stream is not None and stream.instructions.strip():
        system = (f"{system}\n\n" if system else "") + _instructions_block(stream)
    if system:
        messages.append(ChatMessage(role=MessageRole.SYSTEM, content=system))
    messages.extend(history_messages(conversation, before_index=before_index))
    if user_text:
        messages.append(ChatMessage(role=MessageRole.USER, content=user_text))
    return messages
```

`conversation.workstream` is one join, and **`_run_turn`'s own** `select_related("conversation__agent",
"conversation__workstream")` at `agents/runtime/loop.py:172` (Task 10, Step 5) is what makes it free
on the runtime path. Not `plan_turn`'s: `build_messages` is called from `_run_turn`, and those are
two different job-kind hooks running in different processes against different fetches
(`agents/apps.py:59-60`). `plan_turn` widens its own fetch for its own `_wall_for` read.

- [ ] **Step 4: Run the tests**

Run: `pytest agents/runtime/tests/test_prompt.py -v`
Expected: PASS — including every existing case, because a loose conversation's messages are
byte-identical.

Run: `pytest agents/ -q`
Expected: PASS.

- [ ] **Step 5: Update the column README**

In `agents/README.md`'s Workstreams section: the instructions block, that it is appended to the
agent's system message rather than sent as a second one and why, that a blank agent prompt with
non-blank instructions still emits a system message, and that a delegate inherits the scope but
not the prose (with §24 concern 2's one-line remedy named).

- [ ] **Step 6: Commit**

```bash
git add agents/runtime/prompt.py agents/runtime/tests/test_prompt.py agents/README.md
git commit -m "feat(agents): a workstream's instructions reach the model as one labelled block"
```

---

### Task 13: the five `/chat/w/` routes and the stream page

**Files:**
- Create: `agents/chat/views/workstreams.py`
- Create: `agents/chat/templates/chat/workstreams.html`, `chat/workstream.html`,
  `chat/_workstream_panel.html`
- Modify: `agents/chat/views/__init__.py`, `agents/chat/urls.py`, `identity/routes.py`
- Test: `agents/chat/tests/test_workstream_page.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1–12. In particular `visible_workstreams`,
  `may_manage_workstream`, `create_workstream`, `rename_workstream`,
  `set_workstream_description`, `set_workstream_instructions`, `set_workstream_archived`,
  `set_workstream_upload_default`, `set_workstream_scope`, `delete_workstream` (Task 3);
  `agents.workstreams.panels_for`, `workstream_scope` (Task 4); `_wall_for` (Task 10).
- Produces: url names `chat-workstreams`, `chat-workstream`, `chat-workstream-edit`,
  `chat-workstream-scope` — four of the five WS-1 route names; the fifth,
  `rag-workstream-pin`, is Task 15. `chat-workstream-share` and `chat-workstream-consolidate`
  are WS-2 (Tasks 17 and 18).

- [ ] **Step 1: Write the failing route test**

Create `agents/chat/tests/test_workstream_page.py`:

```python
"""The stream list, the stream page, and the one edit route with an
`action` field."""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.models import Share, Workstream, WorkstreamScopeEntitlement
from agents.tests._helpers import _workstream
from identity.access import owner_fields
from identity.testing import (
    grant, make_admin, make_entitlement, make_user, posture, sign_in, user_principal,
)

pytestmark = pytest.mark.django_db


def test_the_list_shows_only_streams_this_principal_may_read(client):
    owner, other = make_user(), make_user()
    mine = _workstream(**owner_fields(user_principal(owner)), name="Mine")
    theirs = _workstream(**owner_fields(user_principal(other)), name="Theirs")
    with posture("enterprise"):
        sign_in(client, owner)
        body = client.get(reverse("chat-workstreams")).content.decode()
    assert "Mine" in body
    assert "Theirs" not in body


def test_posting_to_the_list_creates_a_stream_and_redirects_to_it(client):
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstreams"), {"name": "Q3 planning"})
    stream = Workstream.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("chat-workstream", args=[stream.pk])


def test_a_duplicate_name_re_renders_the_list_with_a_named_message(client):
    user = make_user()
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("chat-workstreams"), {"name": "Taxes"})
        response = client.post(reverse("chat-workstreams"), {"name": "taxes"})
    # 400, matching this task's own `test_an_unknown_action_is_a_400` and
    # Task 11's placement refusal: the submission was not accepted, and
    # the page re-renders saying why.
    assert response.status_code == 400
    assert "already have a workstream called" in response.content.decode()
    assert Workstream.objects.count() == 1


def test_the_stream_page_404s_for_a_stranger(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.get(reverse("chat-workstream", args=[stream.pk]))
    assert response.status_code == 404
    assert "Traceback" not in response.content.decode()


@pytest.mark.parametrize("action,field,value,check", [
    ("rename", "name", "Renamed", lambda s: s.name == "Renamed"),
    ("description", "description", "Why", lambda s: s.description == "Why"),
    ("instructions", "instructions", "Be terse.", lambda s: s.instructions == "Be terse."),
    ("upload_default", "placement", "contained",
     lambda s: s.default_upload_placement == "contained"),
    ("archive", "", "", lambda s: s.archived_at is not None),
])
def test_each_edit_action_writes_its_own_field(client, action, field, value, check):
    """ONE `edit` ROUTE WITH AN `action` FIELD, NOT SEVEN ROUTES (author
    decision 11 / spec §14). The seven owner-only mutations of a stream
    row share one predicate and one 404 rule; seven routes would be seven
    chances for one of them to drift, and the drift would show as a
    control that 404s."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    payload = {"action": action}
    if field:
        payload[field] = value
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("chat-workstream-edit", args=[stream.pk]), payload)
    stream.refresh_from_db()
    assert check(stream)


def test_an_unknown_action_is_a_400_and_writes_nothing(client):
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Keep")
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": "explode"})
    assert response.status_code == 400
    stream.refresh_from_db()
    assert stream.name == "Keep"


def test_a_recipient_gets_404_on_every_edit_action(client):
    """Every "does not reach" row is a 404 and NOT a 403, because they
    are all row-addressed mutations of a row the recipient can see —
    which is precisely the case `may_manage_conversation`'s docstring
    already rules on for shared conversations."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        for action in ("rename", "description", "instructions", "upload_default",
                       "archive", "unarchive", "delete"):
            response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                                   {"action": action, "name": "x"})
            assert response.status_code == 404, action
        response = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                               {"entitlements": []})
        assert response.status_code == 404


def test_delete_refuses_by_name_while_rows_remain_and_succeeds_when_emptied(client, agent):
    from agents.models import Conversation

    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": "delete"}, follow=True)
        assert "Delete them first" in response.content.decode()
        conversation.delete()
        response = client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": "delete"})
    assert response.status_code == 302
    assert not Workstream.objects.filter(pk=stream.pk).exists()


def test_the_scope_editor_offers_exactly_what_the_gate_accepts(client):
    """M10 (spec §6.1), the single test that catches
    `labelling_entitlements` being used here. `labelling_entitlements`
    answers OWNED, not HELD, and the two diverge in BOTH directions: an
    entitlement you own but do not hold would render and then be refused,
    and one you hold but do not own would never render, making a wall
    §6.1 says you may set unsettable."""
    user = make_user()
    held, owned_not_held = make_entitlement(name="Held"), make_entitlement(name="Owned")
    grant(held, user=user)
    # An entitlement this user OWNS but does not HOLD is not
    # representable through `grant` (a grant is a holding), so the owned
    # -not-held half is built by granting the owner role to a group the
    # user is not in and asserting the name is absent.
    stream = _workstream(**owner_fields(user_principal(user)))
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        assert "Held" in body
        assert "Owned" not in body
        ok = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                         {"entitlements": [str(held.pk)]})
        assert ok.status_code == 302
        refused = client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                              {"entitlements": [str(owned_not_held.pk)]}, follow=True)
    assert "you hold" in refused.content.decode().lower()
    assert set(WorkstreamScopeEntitlement.objects.values_list(
        "entitlement_id", flat=True)) == {held.pk}


def test_the_scope_editor_is_absent_entirely_on_an_open_box(client):
    """RULING A, and the condition is THE POSTURE, not "the principal
    holds no entitlements". The distinction matters: the wall is inert on
    an open box, so a hidden editor strands nothing, whereas the old
    condition would have hidden the editor on a box where the wall was
    still binding."""
    stream = _workstream()
    with posture("open"):
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "Scope" not in body
    assert "entitlement" not in body.lower()


def test_the_page_carries_no_script_tag_of_its_own(client):
    """ZERO JAVASCRIPT. The conversation page's one sanctioned inline
    script is untouched and nothing here depends on it."""
    stream = _workstream()
    with posture("open"):
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "<script" not in body


def test_the_documents_panel_renders_from_the_registry(client):
    """The page renders whatever is REGISTERED, in registration order,
    through one include — so a second panel later is a REGISTRATION, not
    an edit to this view."""
    from tools.rag.tests._helpers import make_document

    stream = _workstream()
    make_document(workstream=stream, title="Contained thing")
    with posture("open"):
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "Documents" in body
    assert "Contained thing" in body
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest agents/chat/tests/test_workstream_page.py -v`
Expected: FAIL — `NoReverseMatch: Reverse for 'chat-workstreams' not found`

- [ ] **Step 3: Write the views**

Create `agents/chat/views/workstreams.py`:

```python
"""The workstream surface — a list, a page, one edit route with an
`action` field, and the wall editor.

EVERY ROW COMES THROUGH `agents/visibility.py` (ruling 4c, extended by
this phase): `foundation/ops/tests/test_column_boundaries.py` now
forbids any module under `agents/chat` from touching `Workstream.
objects` as well as the four it already covered.

ZERO JAVASCRIPT. Every control below is a link, a `<details>` element,
or a plain POST form with a CSRF token -- the idiom `chat/_sidebar.html`
already documents.
"""
from __future__ import annotations

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from agents.chat.sidebar import sidebar_context
from agents.visibility import (
    create_workstream, delete_workstream, may_manage_workstream, rename_workstream,
    set_workstream_archived, set_workstream_description, set_workstream_instructions,
    set_workstream_scope, set_workstream_upload_default, visible_conversations,
    visible_agents, visible_workstreams,
)
from agents.workstreams import panels_for
from identity.access import accounts_on, entitlement_names, held_entitlement_ids
from identity.request import principal_for_request, settings_row_for

# The seven actions `chat-workstream-edit` accepts. ONE ROUTE, ONE
# PREDICATE, ONE 404 RULE (author decision 11) -- the shape
# `identity-entitlement-edit`'s `_ENTITLEMENT_ACTIONS` tuple already has
# and `may_manage_conversation`'s one-predicate-for-five ruling applied a
# third time. Seven routes would be seven chances for one to drift, and
# the drift would show as a control that 404s.
_WORKSTREAM_ACTIONS = ("rename", "description", "instructions", "upload_default",
                       "archive", "unarchive", "delete")


def _resolve(request, pk):
    """The stream, or `Http404`. Resolved through `visible_workstreams`,
    so a stranger and a nonexistent row are indistinguishable."""
    principal = principal_for_request(request)
    row = visible_workstreams(principal).filter(pk=pk).first()
    if row is None:
        raise Http404("No such workstream.")
    return principal, row


def workstream_list(request):
    """GET `/chat/w/` lists; POST creates.

    CLASS A: the list is this principal's own streams and the ones shared
    to them, so there is no row to be addressed by and nothing to 404
    on.
    """
    principal = principal_for_request(request)
    error = ""
    if request.method == "POST":
        row = create_workstream(principal, request.POST.get("name", ""),
                                description=request.POST.get("description", ""))
        if row is not None:
            return redirect("chat-workstream", pk=row.pk)
        error = ("You already have a workstream called that, or the name was blank. "
                 "Pick another name.")
    context = {
        "workstreams": list(visible_workstreams(principal)
                            .filter(archived_at__isnull=True)),
        "archived": list(visible_workstreams(principal)
                         .filter(archived_at__isnull=False)),
        "create_error": error,
    }
    context.update(sidebar_context(principal, settings_row=settings_row_for(request)))
    return render(request, "chat/workstreams.html", context,
                  status=400 if error else 200)


def _page_context(request, principal, stream, *, error=""):
    """The stream page's whole context. ONE BUILDER, called by the GET and
    by the three POST handlers that re-render with a message rather than
    redirecting -- so a re-rendered page can never be missing a section
    the GET has.

    SECTIONS 3, 8 AND 9 ARE ABSENT ON AN OPEN BOX, and not disabled -- an
    operator running a household box never sees the words "entitlement",
    "taint" or "share" on this page. That is "machinery invisible until a
    row says otherwise", rendered.
    """
    settings_row = settings_row_for(request)
    is_owner = may_manage_workstream(principal, stream, settings_row=settings_row)
    accounts = accounts_on(settings_row=settings_row)
    context = {
        "workstream": stream,
        "is_owner": is_owner,
        "conversations": list(visible_conversations(principal)
                              .select_related("workstream")
                              .filter(workstream_id=stream.pk)
                              .order_by("-updated_at")),
        "agents": list(visible_agents(principal)),
        "panels": panels_for(principal, stream.pk),
        # SECTION 3, the wall editor. Rendered from
        # `entitlement_names(held_entitlement_ids(principal))`, NOT
        # `labelling_entitlements`, so the set offered and the set
        # `set_workstream_scope` accepts are the SAME SET (M10, spec
        # §6.1). The condition is THE POSTURE, not "holds no
        # entitlements" (ruling A): the wall is inert on an open box, so
        # a hidden editor strands nothing.
        "scope_visible": accounts and is_owner,
        "scope_options": (entitlement_names(held_entitlement_ids(principal))
                          if accounts and is_owner else ()),
        "scope_selected": set(stream.scope_entitlements.values_list(
            "entitlement_id", flat=True)),
        "placement_state": ("default" if stream.default_upload_placement
                            else "choose") if is_owner else "none",
        "page_error": error,
    }
    context.update(sidebar_context(principal, workstream=stream, settings_row=settings_row))
    return context


def workstream_page(request, pk):
    """GET `/chat/w/<pk>/`.

    CLASS O: resolved through `visible_workstreams` -> 404. WS-2 inserts
    the read-time share gate here, between the resolution and the render,
    and it is the ONLY route on this platform that then answers 403
    (spec §12.3, fenced there).
    """
    principal, stream = _resolve(request, pk)
    return render(request, "chat/workstream.html",
                  _page_context(request, principal, stream))


@require_POST
def workstream_edit(request, pk):
    """POST `/chat/w/<pk>/edit/` -- one route, seven actions.

    Owner or `sees_all_content` only; a recipient gets 404, because these
    are row-addressed mutations of a row the recipient can see.
    """
    principal, stream = _resolve(request, pk)
    if not may_manage_workstream(principal, stream):
        raise Http404("No such workstream.")
    action = request.POST.get("action", "")
    if action not in _WORKSTREAM_ACTIONS:
        return HttpResponseBadRequest(
            f"Unknown action. Expected one of: {', '.join(_WORKSTREAM_ACTIONS)}.")
    if action == "delete":
        message = delete_workstream(principal, stream)
        if message:
            return render(request, "chat/workstream.html",
                          _page_context(request, principal, stream, error=message),
                          status=400)
        return redirect("chat-workstreams")
    if action == "rename":
        if not rename_workstream(principal, stream, request.POST.get("name", "")):
            return render(request, "chat/workstream.html",
                          _page_context(request, principal, stream,
                                        error="That name is blank or already taken."),
                          status=400)
    elif action == "description":
        set_workstream_description(principal, stream, request.POST.get("description", ""))
    elif action == "instructions":
        set_workstream_instructions(principal, stream,
                                    request.POST.get("instructions", ""))
    elif action == "upload_default":
        set_workstream_upload_default(principal, stream,
                                      request.POST.get("placement", ""))
    else:
        set_workstream_archived(principal, stream, archived=(action == "archive"))
    return redirect("chat-workstream", pk=stream.pk)


@require_POST
def workstream_scope(request, pk):
    """POST `/chat/w/<pk>/scope/` -- sets the wall to exactly the
    submitted entitlement ids.

    Each must be in `held_entitlement_ids(principal)` (spec §6.1), and
    `set_workstream_scope` is the gate: it answers `None` when any is
    outside, and this view renders that as a message rather than a
    silent partial write.
    """
    principal, stream = _resolve(request, pk)
    raw = [i for i in request.POST.getlist("entitlements") if str(i).isdecimal()]
    result = set_workstream_scope(principal, stream, [int(i) for i in raw])
    if result is None:
        if not may_manage_workstream(principal, stream):
            raise Http404("No such workstream.")
        return render(request, "chat/workstream.html",
                      _page_context(request, principal, stream,
                                    error="A workstream's scope may only name "
                                          "entitlements you hold."),
                      status=400)
    return redirect("chat-workstream", pk=stream.pk)
```

`entitlement_names(ids) -> tuple[tuple[int, str], ...]` is added to `identity/access.py` **here**,
in WS-1, rather than in WS-2 as spec §12.1 places it — this is its **first** caller (§6.1 says so
in as many words: "§12.1 is already adding `entitlement_names(ids)` to `identity/access.py`; this
is its second caller"), and WS-1's wall editor needs it:

```python
def entitlement_names(ids) -> tuple[tuple[int, str], ...]:
    """`((id, name), ...)` for `ids`, in name order.

    So a page can render entitlement ids as names without importing
    `identity.models` -- the same reason `labelling_entitlements` lives
    here, and the same shape. NOT a substitute for it: that function
    answers "which may this principal LABEL with" (owned), and this one
    answers "what are these called", with the caller deciding which set
    to ask about. The workstream scope editor asks about
    `held_entitlement_ids(principal)`, deliberately (spec §6.1's M10).

    An id with no row is DROPPED rather than rendered as a blank: a name
    that is not there is not a name, and the caller's own count is what
    reports the difference.
    """
    rows = Entitlement.objects.filter(pk__in={int(i) for i in ids})
    return tuple(rows.order_by("name").values_list("pk", "name"))
```

- [ ] **Step 4: Write the templates**

`agents/chat/templates/chat/workstream.html`, extending `chat/base.html`, in spec §15.3's order —
header, instructions, scope, new chat, conversations, documents, uploads, tags, sharing. Tags and
sharing are WS-2 and are absent here; sections 3, 8 and 9 are wrapped in `{% if scope_visible %}`
and its siblings so an open box renders none of them. Every form is a plain POST with
`{% csrf_token %}` and every disclosure is a `<details>`.

```html
{% extends "chat/base.html" %}
{% block content %}
<article class="workstream">
  {% if page_error %}<p class="error" role="alert">{{ page_error }}</p>{% endif %}

  <header>
    <h1>{{ workstream.name }}</h1>
    {% if workstream.description %}<p class="description">{{ workstream.description }}</p>{% endif %}
    {% if is_owner %}
      <details><summary>Rename</summary>
        <form method="post" action="{% url 'chat-workstream-edit' workstream.pk %}">
          {% csrf_token %}<input type="hidden" name="action" value="rename">
          <input type="text" name="name" value="{{ workstream.name }}" required>
          <button type="submit">Save</button>
        </form>
      </details>
      <details><summary>Description</summary>
        <form method="post" action="{% url 'chat-workstream-edit' workstream.pk %}">
          {% csrf_token %}<input type="hidden" name="action" value="description">
          <textarea name="description" rows="2">{{ workstream.description }}</textarea>
          <button type="submit">Save</button>
        </form>
      </details>
      <form method="post" action="{% url 'chat-workstream-edit' workstream.pk %}">
        {% csrf_token %}
        <input type="hidden" name="action"
               value="{% if workstream.archived_at %}unarchive{% else %}archive{% endif %}">
        <button type="submit">{% if workstream.archived_at %}Unarchive{% else %}Archive{% endif %}</button>
      </form>
      <details class="danger"><summary>Delete</summary>
        <form method="post" action="{% url 'chat-workstream-edit' workstream.pk %}">
          {% csrf_token %}<input type="hidden" name="action" value="delete">
          <p>Deleting is refused while this workstream still holds conversations or
             documents. Delete them first.</p>
          <button type="submit">Delete this workstream</button>
        </form>
      </details>
    {% endif %}
  </header>

  {% if is_owner %}
  <section id="instructions">
    <h2>Instructions</h2>
    <form method="post" action="{% url 'chat-workstream-edit' workstream.pk %}">
      {% csrf_token %}<input type="hidden" name="action" value="instructions">
      <textarea name="instructions" rows="6">{{ workstream.instructions }}</textarea>
      <button type="submit">Save</button>
    </form>
    <p class="hint">Sent with every turn in this workstream, labelled as the
      workstream’s instructions.</p>
  </section>
  {% endif %}

  {% if scope_visible %}
  <section id="scope">
    <h2>Scope</h2>
    <form method="post" action="{% url 'chat-workstream-scope' workstream.pk %}">
      {% csrf_token %}
      {% for id, name in scope_options %}
        <label><input type="checkbox" name="entitlements" value="{{ id }}"
          {% if id in scope_selected %}checked{% endif %}> {{ name }}</label>
      {% empty %}
        <p class="hint">You hold no entitlements, so there is nothing to narrow to.</p>
      {% endfor %}
      <button type="submit">Save scope</button>
    </form>
    <p class="hint">With a scope set, this workstream retrieves only material under these
      entitlements — and not unlabelled material.</p>
  </section>
  {% endif %}

  <section id="new-chat">
    <h2>New chat</h2>
    {% if not is_owner %}
      <p class="hint">This is somebody else’s workstream — its owner can read the
        conversations you start here.</p>
    {% endif %}
    <form method="post" action="{% url 'chat-start' %}">
      {% csrf_token %}
      <input type="hidden" name="workstream" value="{{ workstream.pk }}">
      <select name="agent">{% for a in agents %}<option value="{{ a.slug }}">{{ a.name }}</option>{% endfor %}</select>
      <textarea name="text" rows="3" placeholder="Start a conversation in this workstream"></textarea>
      <button type="submit">Start</button>
    </form>
  </section>

  <section id="conversations">
    <h2>Conversations</h2>
    <ul>
      {% for c in conversations %}
        <li><a href="{% url 'chat-conversation' c.pk %}">{{ c.title|default:"Untitled" }}</a></li>
      {% empty %}<li class="hint">No conversations here yet.</li>{% endfor %}
    </ul>
  </section>

  {% for panel in panels %}{% include "chat/_workstream_panel.html" %}{% endfor %}
</article>
{% endblock %}
```

`chat/_workstream_panel.html` is now the **wrapper only** — a heading, the degrade branch author
decision 11 requires, and one include. It branches on nothing:

```html
<section class="panel" id="panel-{{ panel.key }}">
  <h2>{{ panel.label }}</h2>
  {% if panel.ok %}{% include panel.template %}
  {% else %}<p class="hint">This section could not be loaded.</p>{% endif %}
</section>
```

`tools/rag/templates/rag/panels/documents.html` holds this column's own markup — **without any
pin control, which Task 15 adds in the same commit as the route they post to** (M11: Task 13's own
`test_the_documents_panel_renders_from_the_registry` runs under `posture("open")`, where
`sees_all_content` is True and `is_owner` is therefore True, so a `{% url 'rag-workstream-pin' %}`
here would be a `NoReverseMatch` — a 500, on the task that is supposed to be green):

```html
<h3>Contained</h3>
<ul>{% for d in panel.data.contained %}
  <li><a href="{% url 'rag-document-file' d.pk %}">{{ d.title }}</a>
    {% if d.origin == "notes" %}<span class="chip">notes</span>{% endif %}
    {% if d.status == "failed" %}<span class="chip failed">FAILED</span>{% endif %}</li>
{% empty %}<li class="hint">Nothing contained here yet.</li>{% endfor %}</ul>
<h3>Pinned</h3>
<ul>{% for row in panel.data.pinned %}
  <li><a href="{% url 'rag-document-file' row.document.pk %}">{{ row.document.title }}</a></li>
{% empty %}<li class="hint">Nothing pinned here yet.</li>{% endfor %}</ul>
```

`chat/workstreams.html` is the list: a create form, the active streams with their conversation
counts, and a `<details>` holding the archived ones.

- [ ] **Step 5: Wire the routes and the matrix**

`agents/chat/urls.py`:

```python
    path("w/", workstream_list, name="chat-workstreams"),
    path("w/<int:pk>/", workstream_page, name="chat-workstream"),
    path("w/<int:pk>/edit/", workstream_edit, name="chat-workstream-edit"),
    path("w/<int:pk>/scope/", workstream_scope, name="chat-workstream-scope"),
```

`identity/routes.py::ROUTE_RULES`, beside the other `chat-` entries:

```python
    # --- /chat/w/ (new in Workstreams WS-1) ----------------------------
    # A, not O: the list is this principal's own streams plus the ones
    # shared to them, so there is no row to be addressed by.
    "chat-workstreams": "A",
    # O, and it is the ONE route on this platform that may answer 403 on
    # a row-addressed URL -- for a holder of a live `Share` row whose
    # grants no longer cover the stream's tags, and for nobody else
    # (WS-2, spec §12.3, fenced there in three ways). A stranger still
    # gets the house rule's 404.
    "chat-workstream": "O",
    "chat-workstream-edit": "O",
    "chat-workstream-scope": "O",
```

Every name is added **in the same commit as the route**, because a name absent from that table is
treated as `ADMIN` and `identity/tests/test_route_matrix.py` fails naming it — which is what keeps
the table a live artefact.

- [ ] **Step 6: Run the tests**

Run: `pytest agents/chat/tests/test_workstream_page.py identity/tests/test_route_matrix.py -v`
Expected: PASS

Run: `pytest agents/ identity/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add agents/chat/views/workstreams.py agents/chat/views/__init__.py agents/chat/urls.py \
        agents/chat/templates/chat/workstreams.html \
        agents/chat/templates/chat/workstream.html \
        agents/chat/templates/chat/_workstream_panel.html \
        identity/routes.py identity/access.py agents/chat/tests/test_workstream_page.py
git commit -m "feat(chat): the workstream list, the stream page, and the one edit route"
```

---

### Task 14: the sidebar, stream-first creation, and duplication that launders nothing

**Files:**
- Modify: `agents/chat/sidebar.py` (`sidebar_context` gains one argument, three keys and a `select_related`)
- Modify: `agents/chat/templates/chat/_sidebar.html`
- Modify: `agents/visibility.py` (`create_conversation` gains `workstream=`; `duplicate_conversation` carries it)
- Modify: `agents/chat/views/conversations.py` (`chat-start` accepts `workstream`)
- Modify: `agents/chat/views/thread.py` (the conversation page passes its stream to the sidebar)
- Test: `agents/chat/tests/test_sidebar.py` (append), `agents/tests/test_workstreams.py` (append)

**Interfaces:**
- Consumes: `visible_workstreams` (Task 3), `workstream_scope` (Task 4), `_wall_for` (Task 10).
- Produces: `sidebar_context(principal, *, current=None, archived=False, settings_row=None,
  workstream=None)` returning three new keys — `sidebar_workstreams`,
  `sidebar_workstreams_older_count`, `sidebar_workstream` (the scoped-to stream, or `None`);
  `WORKSTREAM_SIDEBAR_LIMIT = 10`;
  `create_conversation(principal, agent, *, workstream=None)`.

- [ ] **Step 1: Write the failing sidebar and creation tests**

Append to `agents/chat/tests/test_sidebar.py`, whose import block gains
`WORKSTREAM_SIDEBAR_LIMIT` and `sidebar_context` from `agents.chat.sidebar`, `_workstream` from
`agents.tests._helpers`, `owner_fields` and `settings_row` from `identity.access`, and
`Conversation` from `agents.models` — each named here because a task that appends to an existing
module owns the import lines it adds, the way Task 3 Step 4 does for `agents/visibility.py`:

```python
def test_the_sidebar_lists_workstreams_above_conversations():
    user = make_user()
    _workstream(user_principal(user), name="Q3 planning")
    with posture("enterprise"):
        context = sidebar_context(user_principal(user))
    assert [w.name for w in context["sidebar_workstreams"]] == ["Q3 planning"]


def test_the_section_renders_even_when_empty():
    """A feature that only appears once you have used it is a feature
    nobody finds."""
    user = make_user()
    with posture("enterprise"):
        context = sidebar_context(user_principal(user))
    assert context["sidebar_workstreams"] == []
    assert "sidebar_workstreams" in context


def test_the_workstream_cap_is_honest_on_screen():
    """`WORKSTREAM_SIDEBAR_LIMIT = 10`, with the same honest "…N more"
    line the conversation cap already renders."""
    user = make_user()
    for i in range(13):
        _workstream(user_principal(user), name=f"S{i}")
    with posture("enterprise"):
        context = sidebar_context(user_principal(user))
    assert len(context["sidebar_workstreams"]) == WORKSTREAM_SIDEBAR_LIMIT
    assert context["sidebar_workstreams_older_count"] == 3


def test_entering_a_stream_scopes_the_conversation_list(agent):
    """ONE ARGUMENT, not a second template, a second route and a second
    copy of the shape — the ruling `sidebar_context`'s own docstring
    already made about the archived list, applied again."""
    user = make_user()
    stream = _workstream(user_principal(user))
    inside = Conversation.objects.create(agent=agent, workstream=stream,
                                         **owner_fields(user_principal(user)))
    loose = Conversation.objects.create(agent=agent, **owner_fields(user_principal(user)))
    with posture("enterprise"):
        scoped = sidebar_context(user_principal(user), workstream=stream)
        unscoped = sidebar_context(user_principal(user))
    scoped_keys = {r["conversation"].pk for r in scoped["sidebar_rows"]}
    assert scoped_keys == {inside.pk}
    assert scoped["sidebar_workstream"].pk == stream.pk
    assert {r["conversation"].pk for r in unscoped["sidebar_rows"]} == {inside.pk, loose.pk}


@pytest.mark.parametrize("streams", [1, 25])
def test_the_sidebar_is_flat_in_the_number_of_workstreams(streams, django_assert_num_queries):
    """GLOBAL CONSTRAINT 6, on the list THIS task adds. The scoped-vs-plain
    comparison below cannot see it — both sides pay the identical +2 — so
    the Workstreams section needs its own equality pin, plus one absolute
    pin so a future third query is visible rather than merely equal.

    `WORKSTREAM_SIDEBAR_LIMIT` bounds what is LISTED; it does not bound
    what is COUNTED, and `.count()` before the slice is the honest "…N
    more" line's own query."""
    user = make_user()
    for _ in range(streams):
        _workstream(user_principal(user))
    with posture("enterprise"):
        row = settings_row()
        with django_assert_num_queries(_SIDEBAR_BASELINE_QUERIES):
            sidebar_context(user_principal(user), settings_row=row)


# The absolute pin's number, named once so the two parametrized runs
# assert the SAME count and a reviewer sees what it is. It moves only
# when the sidebar genuinely gains a query, which is the event this pin
# exists to make visible.
_SIDEBAR_BASELINE_QUERIES = 5


@pytest.mark.parametrize("rows", [1, 25])
def test_a_sidebar_of_stream_conversations_costs_the_same_as_one_of_loose_ones(
        agent, django_assert_num_queries, rows):
    """RULING C COSTS NO QUERIES PER ROW (r4, §6.4/§17.3), asserted as a
    PINNED COUNT and not an inequality, because the regression this
    guards against was found by MEASUREMENT and would be invisible to a
    correctness test. `agents/chat/sidebar.py`'s own docstring records
    the last per-row read costing 47 → 95 queries at 25 rows.

    `select_related("workstream")` is what makes it flat — not an
    optimisation, but the condition of ruling C being affordable, since
    `may_manage_conversation` gains a stream-owner branch in WS-2 that
    reads `conversation.workstream.owner_kind`/`owner_key` once per
    listed row."""
    user = make_user()
    stream = _workstream(user_principal(user))
    for _ in range(rows):
        Conversation.objects.create(agent=agent, workstream=stream,
                                    **owner_fields(user_principal(user)))
    for _ in range(rows):
        Conversation.objects.create(agent=agent, **owner_fields(user_principal(user)))
    # MEASURE BOTH AND ASSERT EQUALITY, so the absolute number can move
    # with the platform while the DIFFERENCE stays zero -- which is the
    # property ruling C actually needs, and the one a fixed count would
    # make brittle for the wrong reason.
    with posture("enterprise"):
        scoped = _count_queries(lambda: sidebar_context(
            user_principal(user), workstream=stream, settings_row=settings_row()))
        plain = _count_queries(lambda: sidebar_context(
            user_principal(user), settings_row=settings_row()))
    assert scoped == plain
```

`_count_queries` is a small local helper wrapping `django.test.utils.CaptureQueriesContext`; add it
beside the module's existing count helpers.

Append to `agents/tests/test_workstreams.py`, whose import block gains `reverse` from
`django.urls`, and `create_conversation`, `duplicate_conversation`, `rename_conversation` and
`set_conversation_archived` from `agents.visibility`:

```python
def test_a_conversation_is_born_in_a_stream_and_the_column_is_never_written_again(agent):
    """OWNER DECISION 2: stream-first creation only. There is NO move
    affordance in v1, and `Conversation.workstream` is immutable for
    life — a thread that can change containers is a thread whose taint
    history is a lie."""
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        conversation = create_conversation(user_principal(user), agent, workstream=stream)
    assert conversation.workstream_id == stream.pk


def test_the_column_is_written_at_creation_and_by_no_other_writer(agent):
    """OWNER DECISION 2, asserted as a PROPERTY rather than by grepping
    the module's source text.

    An earlier draft asserted `"workstream_id=" not in source`, which
    pinned an implementation detail: it survives Tasks 16 and 17 only
    because their new clauses happen to spell `workstream_id__in=`, and
    it would fail on a perfectly correct refactor. What actually matters
    is that the five mutating writers this module exposes leave the
    column alone, so that is what this checks."""
    user = make_user()
    one, two = _workstream(user_principal(user)), _workstream(user_principal(user))
    with posture("enterprise"):
        conversation = create_conversation(user_principal(user), agent, workstream=one)
        rename_conversation(user_principal(user), conversation, "Renamed")
        set_conversation_archived(user_principal(user), conversation, archived=True)
        set_conversation_archived(user_principal(user), conversation, archived=False)
        copy = duplicate_conversation(user_principal(user), conversation, title="Copy")
    conversation.refresh_from_db()
    assert conversation.workstream_id == one.pk
    # And the copy is stamped once at ITS creation, in the same stream --
    # nothing moved, and `two` is reachable by no writer here.
    assert copy.workstream_id == one.pk
    assert not Conversation.objects.filter(workstream=two).exists()


def test_duplicating_a_stream_conversation_keeps_it_in_the_stream(agent):
    """RULING D (spec §23.D). Unamended, one ⋯-menu click would produce a
    LOOSE thread holding labelled material with ZERO taint tags, outside
    every gate this phase builds and shareable through
    `chat-conversation-share`. §12's two gates would be one click from
    decorative."""
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        source = create_conversation(user_principal(user), agent, workstream=stream)
        copy = duplicate_conversation(user_principal(user), source, title="Copy")
    assert copy.workstream_id == stream.pk


def test_an_administrator_duplicating_somebody_elses_stream_conversation_cannot_fork_it_out(
        agent):
    """`may_manage_conversation`'s first branch admits them, so this is
    reachable — and it is the case ruling D's own finding names."""
    owner, admin = make_user(), make_admin()
    stream = _workstream(user_principal(owner))
    with posture("enterprise", admin_sees_content=True):
        source = create_conversation(user_principal(owner), agent, workstream=stream)
        copy = duplicate_conversation(user_principal(admin), source, title="Copy")
    assert copy.workstream_id == stream.pk
    assert copy.owner_key == str(admin.pk)


def test_chat_start_refuses_a_stream_this_principal_may_not_reach(client, agent):
    owner, stranger = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.post(reverse("chat-start"),
                               {"agent": agent.slug, "workstream": str(stream.pk)})
    assert response.status_code == 400
    assert Conversation.objects.count() == 0
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest agents/chat/tests/test_sidebar.py agents/tests/test_workstreams.py -k "sidebar_workstream or scoped or duplicat or born" -v`
Expected: FAIL — `sidebar_context() got an unexpected keyword argument 'workstream'`

- [ ] **Step 3: Give the sidebar its section and its scope**

In `agents/chat/sidebar.py`:

```python
from agents.visibility import (
    may_manage_conversation, visible_conversations, visible_workstreams,
)

SIDEBAR_LIMIT = 30

# How many workstreams the sidebar lists. A cap, not a paginator, for the
# reason `SIDEBAR_LIMIT` is one -- and honest on screen when it bites,
# with the same "…N more" line the conversation cap already renders.
WORKSTREAM_SIDEBAR_LIMIT = 10


def sidebar_context(principal, *, current=None, archived: bool = False,
                    settings_row=None, workstream=None) -> dict:
    """... (existing docstring, plus:)

    `workstream` SCOPES this sidebar to one stream: the Workstreams
    section collapses to that stream's own name as a heading, the
    conversation list becomes that stream's conversations, and an "All
    chats" link returns to the unscoped view. Mechanically it is ONE
    ARGUMENT -- one extra `.filter()` on the queryset this function
    already builds, over the `agents_conv_ws` index -- and there is no
    second template, no second route and no second copy of the shape,
    which is the ruling this docstring already made about the archived
    list, applied again.

    `select_related("workstream")` IS NOT AN OPTIMISATION. It is the
    condition of ruling C being affordable: `may_manage_conversation`
    gains a stream-owner branch that reads
    `conversation.workstream.owner_kind`/`owner_key`, and this function
    calls that predicate ONCE PER LISTED ROW -- the caller this
    docstring already singles out for a MEASURED regression (+2 queries
    per conversation, 47 -> 95 at 25 rows). Without the join it would be
    a second per-row read, on the two most-trafficked pages in the app.
    """
    rows = visible_conversations(principal).select_related("workstream").filter(
        archived_at__isnull=not archived)
    if workstream is not None:
        rows = rows.filter(workstream_id=workstream.pk)
    total = rows.count()
    listed = list(rows[:SIDEBAR_LIMIT])
    streams = visible_workstreams(principal).filter(archived_at__isnull=True)
    stream_total = streams.count()
    return {
        "sidebar_archived": archived,
        # THE STREAM THIS SIDEBAR IS SCOPED TO, or None. The template
        # renders the section as a heading plus an "All chats" link when
        # it is set, and as the list when it is not.
        "sidebar_workstream": workstream,
        "sidebar_workstreams": list(streams[:WORKSTREAM_SIDEBAR_LIMIT]),
        "sidebar_workstreams_older_count": max(0, stream_total - WORKSTREAM_SIDEBAR_LIMIT),
        "sidebar_rows": [
            {"conversation": row,
             "may_manage": may_manage_conversation(principal, row,
                                                   settings_row=settings_row),
             "current": current is not None and row.pk == current.pk}
            for row in listed
        ],
        "sidebar_older_count": max(0, total - SIDEBAR_LIMIT),
        "sidebar_archived_count": (
            0 if archived
            else visible_conversations(principal).filter(
                archived_at__isnull=False).count()
        ),
    }
```

The conversation count per stream row is **not** computed here: it would be one query per row, and
the section is a nav. The template renders the name alone, and the stream page carries the count.
Spec §15.1's sketch shows a count beside each row; **author decision 22** is that a per-row count
is exactly the N+1 that Global Constraint 6 forbids and that this module's own docstring records as
a measured regression, so the sidebar lists names and `chat-workstream` carries the count.

- [ ] **Step 4: Render the section**

In `chat/_sidebar.html`, **above** the conversation list:

```html
<nav class="workstreams">
  {% if sidebar_workstream %}
    <h2>{{ sidebar_workstream.name }}</h2>
    <a class="exit" href="{% url 'chat-index' %}">All chats</a>
  {% else %}
    <h2>Workstreams <a class="new" href="{% url 'chat-workstreams' %}">+ New</a></h2>
    <ul>
      {% for w in sidebar_workstreams %}
        <li><a href="{% url 'chat-workstream' w.pk %}">{{ w.name }}</a></li>
      {% empty %}
        <li class="hint">No workstreams yet — <a href="{% url 'chat-workstreams' %}">New</a></li>
      {% endfor %}
    </ul>
    {% if sidebar_workstreams_older_count %}
      <p class="more"><a href="{% url 'chat-workstreams' %}">…{{ sidebar_workstreams_older_count }} more</a></p>
    {% endif %}
  {% endif %}
</nav>
<h2>Chats</h2>
```

- [ ] **Step 5: Stamp the column once, and carry it on duplication**

In `agents/visibility.py`:

```python
def create_conversation(principal, agent, *, workstream=None):
    """A new conversation owned by `principal`, optionally BORN IN a
    workstream.

    ... (existing docstring, plus:)

    `workstream` IS STAMPED ONCE AND NEVER WRITTEN AGAIN (owner decision
    2). There is no move affordance in v1, no route writes this column
    after creation, and this module exposes no setter for it -- a thread
    that can change containers is a thread whose taint history is a lie.
    The caller has already resolved the row through `visible_workstreams`
    (and, in WS-2, through `stream_access`); this function stamps it.
    """
    return Conversation.objects.create(agent=agent, workstream=workstream,
                                       **owner_fields(principal))
```

and in `duplicate_conversation`, the one changed line and the paragraph it earns:

```python
    THE COPY STAYS IN THE STREAM (ruling D, spec §23.D), and it is
    spelled out here because this is where duplication is defined.
    Unamended, this function built the copy with no `workstream` while
    copying `text`, `data` and `artifacts` -- the quoted document text
    and the `document:<id>` references -- so one menu click would have
    produced a LOOSE thread holding labelled material outside every gate
    the workstreams phase builds, shareable through
    `chat-conversation-share`. An administrator under
    `admin_sees_content` could have done it to anybody's stream
    conversation.

    Carrying the stream identity is CONSISTENT WITH IMMUTABILITY RATHER
    THAN AN EXCEPTION TO IT: nothing moves, and the new row's
    `workstream` is stamped once at creation like every other row's. v1
    offers NO LOOSE COPY of a stream conversation -- there is no control
    for it and no parameter that would produce one.
    """
    if not may_manage_conversation(principal, conversation):
        return None
    with transaction.atomic():
        copy = Conversation.objects.create(
            agent=conversation.agent, title=title,
            workstream=conversation.workstream,      # THE COPY STAYS IN THE STREAM
            **owner_fields(principal),
        )
```

(The `ConversationTaint` copy that ruling D also requires lands in Task 16, with the table.)

- [ ] **Step 6: Let `chat-start` name a stream**

In `agents/chat/views/conversations.py::conversation_start`, after the agent resolution:

```python
    # THE STREAM, IF THIS THREAD IS BEING BORN IN ONE (spec §14). It must
    # be in `visible_workstreams` (and, in WS-2, pass `stream_access`),
    # else 400 -- a caller error, exactly like the unknown agent above:
    # the id came from a page this surface rendered, so an unreachable
    # one means the stream was deleted or unshared between the render and
    # the submit, and starting a thread somewhere else would be worse
    # than saying no.
    raw_stream = request.POST.get("workstream", "")
    stream = None
    if raw_stream:
        stream = (visible_workstreams(principal).filter(pk=int(raw_stream)).first()
                  if raw_stream.isdecimal() else None)
        if stream is None:
            return HttpResponseBadRequest(
                "That workstream is not available. It may have been deleted, or the "
                "share that reached it may have been revoked.")
    conversation = create_conversation(principal, agent, workstream=stream)
```

and the redirect keeps the person inside the stream by passing the conversation, whose page then
scopes its own sidebar.

In `agents/chat/views/thread.py`, the conversation page's `sidebar_context(...)` call gains
`workstream=conversation.workstream`, and its `chat_picker_options(...)` call gains
`wall=_wall_for(conversation)` (Task 10, Step 5's render half).

- [ ] **Step 7: Run everything**

Run: `pytest agents/chat/tests/ agents/tests/ -q`
Expected: PASS, including the two query-count equalities at 1 row and at 25.

Run: `pytest agents/ tools/ identity/ models/ foundation/ -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add agents/chat/sidebar.py agents/chat/templates/chat/_sidebar.html \
        agents/visibility.py agents/chat/views/conversations.py agents/chat/views/thread.py \
        agents/chat/tests/test_sidebar.py agents/tests/test_workstreams.py
git commit -m "feat(chat): the sidebar's workstreams section, the scoped sidebar, and stream-first creation"
```

---

### Task 15: the library page — a Workstream column, and `?pin_into=`

**Files:**
- Modify: `tools/rag/views.py` (`DocumentsView`/`DocumentsListView` context; a new `workstream_pin` view)
- Modify: `tools/rag/templates/rag/documents.html`
- Modify: `tools/rag/urls.py`, `identity/routes.py`
- Test: `tools/rag/tests/test_pin_page.py` (new)

**Interfaces:**
- Consumes: `pin_document`, `unpin_document`, `MAX_PINS_PER_STREAM` (Task 7);
  `workstream_scope` (Task 4); `containing_workstream_id`, `readable_documents` (Task 6).
- Produces: url name `rag-workstream-pin` (POST, class **O**). **This is the last WS-1 task; the
  branch is mergeable here.**

- [ ] **Step 1: Write the failing pin-route test**

Create `tools/rag/tests/test_pin_page.py`:

```python
"""`?pin_into=` on the library page, and the one pin route."""
from __future__ import annotations

import pytest
from django.urls import reverse

from tools.rag.models import WorkstreamPin
from tools.rag.tests._helpers import _workstream, make_document
from identity.access import owner_fields
from identity.testing import make_admin, make_user, posture, sign_in, user_principal

pytestmark = pytest.mark.django_db


def test_the_library_page_names_each_contained_documents_stream(client):
    """A list that shows a row without saying where it lives is a list
    that invites the wrong delete."""
    admin = make_admin()
    stream = _workstream(name="Q3 planning")
    make_document(workstream=stream, title="Contained thing")
    make_document(title="Universal thing")
    with posture("enterprise", admin_sees_content=True):
        sign_in(client, admin)
        body = client.get(reverse("rag-documents")).content.decode()
    assert "Q3 planning" in body
    assert "Universal thing" in body


def test_pin_into_renders_a_banner_and_a_pin_control_per_universal_row(client):
    """AUTHOR DECISION 25: the pin picker is `?pin_into=` on the EXISTING
    library page, not a new page. The library page already lists,
    searches, paginates and permission-filters documents; a second
    listing would be a second set of counts and a second filter to keep
    in agreement with `readable_documents`."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)), name="Q3")
    doc = make_document(title="Pin me")
    with posture("enterprise"):
        sign_in(client, user)
        body = client.get(f"{reverse('rag-documents')}?pin_into={stream.pk}").content.decode()
    assert "Q3" in body
    assert "Pin me" in body
    assert reverse("rag-workstream-pin", args=[stream.pk]) in body


def test_pin_into_a_stream_the_caller_may_not_reach_renders_no_banner(client):
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)), name="Private")
    with posture("enterprise"):
        sign_in(client, stranger)
        body = client.get(f"{reverse('rag-documents')}?pin_into={stream.pk}").content.decode()
    assert "Private" not in body


def test_the_pin_route_resolves_both_halves_404_shaped(client):
    """TWO RESOLUTIONS, BOTH 404-SHAPED: the stream through
    `agents.workstreams.workstream_scope` (which is `None` for a stream
    the caller may not mutate) and the document through
    `readable_documents`."""
    owner, stranger = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    doc = make_document()
    with posture("enterprise"):
        sign_in(client, stranger)
        response = client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                               {"action": "pin", "document": str(doc.pk)})
    assert response.status_code == 404
    assert WorkstreamPin.objects.count() == 0


def test_a_named_refusal_is_a_message_not_a_404(client):
    """The named refusals of §8.2 — contained, capped — are messages on a
    row the caller can already SEE, not 404s."""
    user = make_user()
    stream, other = (_workstream(**owner_fields(user_principal(user))),
                     _workstream(**owner_fields(user_principal(user))))
    contained = make_document(workstream=other)
    with posture("enterprise"):
        sign_in(client, user)
        response = client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                               {"action": "pin", "document": str(contained.pk)},
                               follow=True)
    assert response.status_code == 200
    assert "already lives in a workstream" in response.content.decode()


def test_one_url_two_actions(client):
    """Unpinning is the same route with an `action` field, keyed on the
    pin id, exactly as `chat-conversation-share` revokes: one URL, two
    actions, so the two predicates cannot drift."""
    user = make_user()
    stream = _workstream(**owner_fields(user_principal(user)))
    doc = make_document()
    with posture("enterprise"):
        sign_in(client, user)
        client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                    {"action": "pin", "document": str(doc.pk)})
        pin = WorkstreamPin.objects.get()
        client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                    {"action": "unpin", "pin": str(pin.pk)})
    assert WorkstreamPin.objects.count() == 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tools/rag/tests/test_pin_page.py -v`
Expected: FAIL — `NoReverseMatch: Reverse for 'rag-workstream-pin' not found`

- [ ] **Step 3: Write the route**

In `tools/rag/views.py`:

```python
@require_POST
def workstream_pin(request, ws_id):
    """POST `/rag/workstreams/<ws_id>/pin/` -- `action` = `pin` or
    `unpin`.

    CLASS O, NOT L, and the row it is addressed by is the STREAM, not the
    document. `rag-document-labels` is the nearest precedent for a
    `tools/rag` route whose predicate is not about reading the document's
    bytes; this one goes further and is about a row in ANOTHER COLUMN
    entirely, which is why the stream half resolves through the seam
    rather than through anything in `tools/rag`.

    TWO RESOLUTIONS, BOTH 404-SHAPED: the stream through
    `agents.workstreams.workstream_scope`, which answers `None` for a
    stream this caller may not be in AND for one that does not exist; and
    the document through `readable_documents`. The NAMED refusals of spec
    §8.2 -- the document is contained, the cap is reached -- are messages
    on a row the caller can already see, not 404s.
    """
    from agents.workstreams import workstream_scope
    from tools.rag.workstreams import pin_document, unpin_document

    principal = principal_for_request(request)
    scope = workstream_scope(principal, ws_id)
    if scope is None or not scope.may_upload:
        raise Http404("No such workstream.")
    action = request.POST.get("action", "")
    if action == "unpin":
        message = unpin_document(principal, scope, request.POST.get("pin", ""))
    elif action == "pin":
        raw = request.POST.get("document", "")
        document = (readable_documents(principal).filter(pk=int(raw)).first()
                    if raw.isdecimal() else None)
        if document is None:
            raise Http404("No such document.")
        message = pin_document(principal, scope, document)
    else:
        return HttpResponseBadRequest("Unknown action. Expected 'pin' or 'unpin'.")
    if message:
        messages.error(request, message)
    return redirect(request.POST.get("next") or
                    reverse("chat-workstream", args=[ws_id]))
```

`scope.may_upload` is the owner predicate: pinning is owner-only in v1 (owner decision 7), and
`WorkstreamScope.may_upload` is exactly "this principal may mutate this stream's working set" —
which is why §12.4 says the pin control does not render when it is False.

- [ ] **Step 4: Give the library page its column and its pin mode**

In `DocumentsView`'s context builder, alongside the existing per-row `permits` computation:

```python
        # `?pin_into=<pk>` (spec §14, author decision 25). A BANNER
        # naming the stream and a Pin control per universal readable row
        # -- not a second page. Resolved through the seam, so a stream
        # this caller may not reach renders nothing at all rather than a
        # banner they cannot act on.
        pin_into = self.request.GET.get("pin_into", "")
        scope = (workstream_scope(principal, int(pin_into))
                 if pin_into.isdecimal() else None)
        context["pin_into"] = scope
        context["pin_into_name"] = (
            Workstream_name_for(scope) if scope is not None else "")
```

Reading the stream's **name** from `agents` is a second seam question, so `workstream_scope`
returns only ids. Rather than widen `WorkstreamScope` with a display field — which every retrieval
turn would then carry for nothing — the banner is rendered from the panel's own page: the library
page's banner says *"Pinning into a workstream"* and links to `chat-workstream`, whose page shows
the name. **Simplify to that**, and drop `pin_into_name`:

```python
        context["pin_into"] = scope
```

with the template:

```html
{% if pin_into %}
  <p class="banner">Pinning into
    <a href="{% url 'chat-workstream' pin_into.workstream_id %}">this workstream</a>.
    Pick a universal document below.</p>
{% endif %}
...
<td class="workstream">{% if row.document.workstream_id %}
  <a href="{% url 'chat-workstream' row.document.workstream_id %}">workstream</a>{% endif %}</td>
{% if pin_into and not row.document.workstream_id %}
<td><form method="post" action="{% url 'rag-workstream-pin' pin_into.workstream_id %}">
  {% csrf_token %}<input type="hidden" name="action" value="pin">
  <input type="hidden" name="document" value="{{ row.document.pk }}">
  <input type="hidden" name="next" value="{{ request.get_full_path }}">
  <button type="submit">Pin</button></form></td>
{% endif %}
```

The category sidebar, counts, search box and bulk-label form are untouched.

- [ ] **Step 5: Add the pin controls to the panel template, in this commit**

`tools/rag/templates/rag/panels/documents.html` gains the three forms Task 13 deliberately left
out, **in the same commit as the route they post to** — a template that reverses a URL name no
`urls.py` has yet is a `NoReverseMatch`, and the panel renders for every owner including the open
box's single principal:

```html
{% if is_owner %}
  {% for row in panel.data.pinned %}
    <form method="post" action="{% url 'rag-workstream-pin' workstream.pk %}">
      {% csrf_token %}<input type="hidden" name="action" value="unpin">
      <input type="hidden" name="pin" value="{{ row.pin.pk }}">
      <button type="submit">Unpin “{{ row.document.title }}”</button>
    </form>
  {% endfor %}
  <details><summary>Pin a document</summary>
    <form method="post" action="{% url 'rag-workstream-pin' workstream.pk %}">
      {% csrf_token %}<input type="hidden" name="action" value="pin">
      <select name="document">{% for d in panel.data.pinnable %}
        <option value="{{ d.pk }}">{{ d.title }}</option>{% endfor %}</select>
      <button type="submit">Pin</button>
    </form>
    <p class="hint"><a href="{% url 'rag-documents' %}?pin_into={{ workstream.pk }}">browse
      the library</a>{% if panel.data.capped %} — this workstream is at its pin limit.{% endif %}</p>
  </details>
{% endif %}
```

Add a Task 13 regression to `agents/chat/tests/test_workstream_page.py` asserting the panel renders
with **no** unreversed URL at Task 13's boundary — the cheapest form is that
`test_the_documents_panel_renders_from_the_registry` already asserts a 200, which is exactly what a
`NoReverseMatch` would break.

- [ ] **Step 6: Wire the route and the matrix**

`tools/rag/urls.py`:

```python
    path("workstreams/<int:ws_id>/pin/", workstream_pin, name="rag-workstream-pin"),
```

`identity/routes.py`:

```python
    # O, not L, and the row it is addressed by is the STREAM, not the
    # document (spec §14). Two resolutions, both 404-shaped.
    "rag-workstream-pin": "O",
```

- [ ] **Step 7: Run everything, twice**

Run: `pytest tools/rag/tests/test_pin_page.py identity/tests/test_route_matrix.py -v`
Expected: PASS

Run: `pytest -q` (the whole suite, on `farabunker_impl`)
Expected: PASS.

Run: `FARABUNKER_TEST_POSTURE=personal pytest -q` and `FARABUNKER_TEST_POSTURE=enterprise pytest -q`
Expected: PASS in both.

Run: `python manage.py makemigrations --check --dry-run`
Expected: exit 0.

- [ ] **Step 8: Walk §19.1's eleven done-when criteria in a browser**

This is the WS-1 acceptance gate and it is not optional. Follow `docs/DEV.md`'s preview-stack
instructions for this branch, then walk criteria 1, 2, 2b, 3, 4, 5, 6, 7, 8, 9 and 10 by hand,
capturing what each showed. **No success language before fresh pixels** — the
`verify-deployed-work` skill's ladder is the standard, and criterion 11 *is* that ladder.

- [ ] **Step 9: Commit**

```bash
git add tools/rag/views.py tools/rag/urls.py tools/rag/templates/rag/documents.html \
        tools/rag/templates/rag/panels/documents.html \
        identity/routes.py tools/rag/tests/test_pin_page.py
git commit -m "feat(rag): the library page's workstream column and its pin-into mode"
```

**WS-1 ends here.** The branch is mergeable, deployable and green: streams exist, are navigable,
contain conversations and documents, narrow retrieval and tools by a wall, carry instructions into
the prompt, and refuse to be deleted while they still hold rows. Nothing about taint, sharing or
consolidation exists yet, and nothing in Tasks 1–15 refers to it.

---

# PART TWO — WS-2: taint, sharing, consolidation

---

### Task 16: the taint tables, the `ArtifactLabels` registry, and the stamp on the tool turn

**Files:**
- Modify: `agents/models.py` (two new models; `Conversation` gains two columns)
- Create: `agents/migrations/0007_workstream_taint.py`
- Modify: `agents/contracts/artifacts.py` (the `ArtifactLabels` registry)
- Create: `agents/runtime/taint.py`
- Modify: `agents/runtime/loop.py:418` (the atomic block and the stamp call)
- Modify: `agents/visibility.py::duplicate_conversation` (the taint copy — ruling D's second half)
- Modify: `agents/workstreams.py` (the cascade grows the two taint tables)
- Modify: `tools/rag/labels.py` (`entitlement_ids_for`), `tools/rag/apps.py` (the registration)
- Modify: `identity/contracts/actions.py` (four new constants)
- Test: `agents/tests/test_taint.py` (new), `agents/contracts/tests/test_artifacts.py` (append)

**Interfaces:**
- Consumes: `Conversation.workstream` (Task 2); `Workstream` (Task 2);
  `agents.contracts.artifacts.parse_artifact` (existing).
- Produces: `agents.models.ConversationTaint` (`conversation`, `entitlement`, `first_turn`, `at`;
  reverse `taint_tags`), `agents.models.WorkstreamTaint` (`workstream`, `entitlement`,
  `first_conversation`, `first_turn`, `at`; reverse `taint_tags`); `Conversation.consolidated_through_index`,
  `Conversation.consolidated_at`;
  `agents.contracts.artifacts.ArtifactLabels(kind: str, resolver: str)`,
  `register_artifact_labels(spec) -> None`, `labels_resolver_for(kind: str) -> str | None`;
  `agents.runtime.taint.stamp_turn_taint(turn, conversation, artifacts, *, actor=None) -> frozenset[int]`;
  `tools.rag.labels.entitlement_ids_for(pks: frozenset[int]) -> frozenset[int]`;
  actions `WORKSTREAM_TAINTED`, `CONVERSATION_TAINTED`, `WORKSTREAM_UNTAINTED`,
  `CONVERSATION_UNTAINTED`.

- [ ] **Step 1: Write the failing taint tests**

Create `agents/tests/test_taint.py`:

```python
"""What the work has touched — the stamp, the union upward, the audit,
and the two things that would have made both share gates decorative."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from agents.contracts.artifacts import ArtifactLabels, register_artifact_labels
from agents.contracts.tests._helpers import isolated_labels_registry  # noqa: F401
from agents.models import Conversation, ConversationTaint, Turn, WorkstreamTaint
from agents.runtime import loop as loop_module
from agents.runtime.taint import stamp_turn_taint
from agents.tests._helpers import _workstream, make_job_ctx, make_tool_ctx
from identity.contracts.principals import payload_fields
from agents.visibility import create_conversation, duplicate_conversation
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import (
    grant, make_admin, make_entitlement, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db


def test_a_turn_that_returned_a_labelled_document_leaves_one_tag_at_each_level(agent):
    """§19.2 done-when 1: one conversation tag, one stream tag and two
    audit rows naming the causing turn."""
    ent = make_entitlement(name="E")
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}"])
    added = stamp_turn_taint(turn, conversation, turn.artifacts)
    assert added == frozenset({ent.pk})
    assert ConversationTaint.objects.filter(conversation=conversation,
                                            entitlement=ent).count() == 1
    ws_tag = WorkstreamTaint.objects.get(workstream=stream, entitlement=ent)
    assert ws_tag.first_conversation == conversation.pk
    assert ws_tag.first_turn == turn.pk
    assert {r.action for r in audit.recent()} >= {
        actions.CONVERSATION_TAINTED, actions.WORKSTREAM_TAINTED}


def test_the_second_such_turn_leaves_nothing(agent):
    """One audit row per (stream, entitlement) FIRST SIGHTING, because
    the question an operator asks of this trail is "when did E get into
    this stream, and what brought it in" — and a row per turn would bury
    it under every subsequent turn that returned the same document.
    `bulk_create(..., ignore_conflicts=True)` against the unique
    constraints is what makes the second turn free."""
    ent = make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    first = _tool_turn(conversation, [f"document:{doc.pk}"])
    stamp_turn_taint(first, conversation, first.artifacts)
    before = len(audit.recent())
    second = _tool_turn(conversation, [f"document:{doc.pk}"])
    assert stamp_turn_taint(second, conversation, second.artifacts) == frozenset()
    assert ConversationTaint.objects.count() == 1
    assert len(audit.recent()) == before


def test_a_turn_with_no_document_artifact_costs_nothing(agent, django_assert_num_queries):
    """§7.2's early return, asserted with a QUERY COUNT. The
    overwhelmingly common case — a turn that called no retrieval tool —
    must cost nothing."""
    conversation = Conversation.objects.create(agent=agent, workstream=_workstream())
    turn = _tool_turn(conversation, ["output:12", "input:3"])
    with django_assert_num_queries(0):
        assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset()


def test_a_loose_conversation_is_tainted_too_and_stops_at_step_one(agent):
    """AUTHOR DECISION 9. `ConversationTaint` hangs off the conversation,
    not the stream, so the stamp has no "am I in a stream" branch and a
    loose conversation accumulates tags nothing reads in v1 — which is
    what makes conversation-level share gating a later change with NO
    BACK-FILL."""
    ent = make_entitlement()
    conversation = Conversation.objects.create(agent=agent)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}"])
    stamp_turn_taint(turn, conversation, turn.artifacts)
    assert ConversationTaint.objects.count() == 1
    assert WorkstreamTaint.objects.count() == 0


def test_a_titled_document_reference_parses_to_the_bare_id(agent):
    """`parse_artifact` is THE ONE PARSER and taint uses it, so the taint
    stamp cannot come to disagree with the chat page's own rendering
    about what a reference means. `mint_artifact` may append a
    `:<title>` suffix, which `parse_artifact` peels off."""
    ent = make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)
    turn = _tool_turn(conversation, [f"document:{doc.pk}:Attention%20Is%20All%20You%20Need"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset({ent.pk})


def test_a_malformed_reference_is_skipped_rather_than_raising(agent):
    """`parse_artifact` is the one parser and it RAISES on a bad
    reference; this runs inside a turn's own transaction, so one bad
    string must never fail a turn."""
    conversation = Conversation.objects.create(agent=agent, workstream=_workstream())
    turn = _tool_turn(conversation, ["document:not-a-number", "", "nonsense"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset()


def test_a_turn_that_retrieves_and_then_FAILS_still_taints(
        agent, monkeypatch, isolated_tool_registry):
    """M5, and the test that would have PASSED against the earlier
    `_finish` placement while asserting the wrong thing.

    `run_loop`'s caller wraps `_run_turn` in a `try/except` that flips the
    assistant turn to FAILED with one `.update()`, and `_finish` is
    reached only on the SUCCESS path — while the tool turns are created
    and COMMITTED as they run, each carrying the retrieved text and its
    `document:` references. Stamping in `_finish` would leave `E`
    material committed and rendered in the thread with NO taint row, and
    both share gates would then pass a stream that holds `E` material.
    That is the opposite of the conservative direction: "material is in
    there and no gate knows", not "a share you expected does not work"."""
    # Drive a real turn whose retrieval tool succeeds and whose assistant
    # call then raises, then assert the tags exist and the assistant turn
    # is FAILED.
    ent = make_entitlement(name="E")
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)

    # A retrieval tool that SUCCEEDS, then an assistant call that raises.
    # `run_loop`'s caller flips the assistant turn to FAILED with one
    # `.update()`; the tool turn is already committed by then.
    monkeypatch.setattr(loop_module.gateway, "get_llm_for",
                        lambda *a, **k: _llm_that_cites_then_raises(doc))
    with pytest.raises(RuntimeError):
        _drive_turn(conversation)

    assert conversation.taint_tags.filter(entitlement=ent).exists()
    assert stream.taint_tags.filter(entitlement=ent).exists()
    assert Turn.objects.filter(conversation=conversation,
                               role=Turn.Role.ASSISTANT,
                               state=Turn.State.FAILED).exists()
    assert Turn.objects.filter(conversation=conversation,
                               role=Turn.Role.TOOL,
                               state=Turn.State.DONE).exists()


def test_a_turn_that_stops_between_two_tool_turns_taints_from_the_first(
        agent, monkeypatch, isolated_tool_registry):
    """The same property by a different route."""
    first_ent, second_ent = make_entitlement(), make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    first_doc = _labelled_document(first_ent)
    _labelled_document(second_ent)

    # THERE IS NO CANCELLATION EXCEPTION IN THIS TREE. `grep -rn
    # JobCancelled` finds nothing: a QUEUED job is cancelled by
    # `models.queue.backend.cancel_job`, and a RUNNING handler is not
    # interrupted by an exception at all -- it finishes, or it raises for
    # some other reason. Spec §7.2's *"A cancellation between tool turns
    # is the same shape"* is a statement about the SHAPE, so this test
    # asserts that shape with the only mechanism that exists: the turn
    # stops between two tool turns, for any reason.
    def _stop_after_the_first_tool_turn(*args, **kwargs):
        if Turn.objects.filter(conversation=conversation,
                               role=Turn.Role.TOOL).exists():
            raise RuntimeError("the worker went away between tool turns")
        return _tool_selection_citing(first_doc)

    monkeypatch.setattr(loop_module.gateway, "get_llm_for",
                        lambda *a, **k: _llm_calling(_stop_after_the_first_tool_turn))
    with pytest.raises(RuntimeError):
        _drive_turn(conversation)

    assert stream.taint_tags.filter(entitlement=first_ent).exists()
    assert not stream.taint_tags.filter(entitlement=second_ent).exists()


def test_the_union_across_two_retrievals_in_one_turn_is_complete(agent):
    """Falls out of the loop rather than depending on an accumulator,
    which is one of the three reasons the stamp is on the tool turn."""
    one, two = make_entitlement(), make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc_one, doc_two = _labelled_document(one), _labelled_document(two)

    # ONE tool turn returning BOTH references -- the shape
    # `_document_artifacts` mints when one `rag.ask` cites two documents.
    turn = _tool_turn(conversation,
                      [f"document:{doc_one.pk}", f"document:{doc_two.pk}"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset(
        {one.pk, two.pk})

    # And across TWO tool turns in the same run, which is the case that
    # falls out of the loop rather than depending on an accumulator.
    three = make_entitlement()
    second = _tool_turn(conversation, [f"document:{_labelled_document(three).pk}"])
    stamp_turn_taint(second, conversation, second.artifacts)
    assert set(stream.taint_tags.values_list("entitlement_id", flat=True)) == {
        one.pk, two.pk, three.pk}


def test_a_delegates_documents_are_in_the_union(agent, isolated_tool_registry):
    """§7.1's NAMED RISK, pinned rather than assumed: a document returned
    by a DELEGATE contributes only if the delegate's `ToolResult.
    artifacts` propagate to the parent's tool turn. They do today
    (`agents/runtime/delegate.py` writes artifacts onto the delegate's
    own turn and the delegate's `ToolResult` carries them back), and this
    is the one path where "the union" could quietly stop being the
    union."""
    ent = make_entitlement()
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    doc = _labelled_document(ent)

    # `agents/runtime/delegate.py` writes artifacts onto the DELEGATE's
    # own turn AND carries them back on its `ToolResult`, so the PARENT's
    # tool turn -- the row the stamp rides -- holds them too. That
    # propagation is the one path where "the union" could quietly stop
    # being the union, so it is asserted rather than assumed.
    result = _run_delegate_that_cites(conversation, agent, doc)
    parent_turn = _tool_turn(conversation, list(result.artifacts))

    assert f"document:{doc.pk}" in " ".join(parent_turn.artifacts)
    assert stamp_turn_taint(parent_turn, conversation,
                            parent_turn.artifacts) == frozenset({ent.pk})


def test_duplicating_a_conversation_copies_its_tags_row_for_row(agent):
    """RULING D's second half. A copy that dropped the tags would launder
    a loose conversation exactly as it would a stream one — so the copy
    happens ALWAYS, not only for a stream conversation."""
    ent = make_entitlement()
    user = make_user()
    stream = _workstream(user_principal(user))
    with posture("enterprise"):
        source = create_conversation(user_principal(user), agent, workstream=stream)
        ConversationTaint.objects.create(conversation=source, entitlement=ent, first_turn=7)
        copy = duplicate_conversation(user_principal(user), source, title="Copy")
    tags = list(copy.taint_tags.values_list("entitlement_id", "first_turn"))
    assert tags == [(ent.pk, 7)]


def test_duplicating_a_loose_conversation_carries_its_tags_too(agent):
    ent = make_entitlement()
    user = make_user()
    with posture("enterprise"):
        source = create_conversation(user_principal(user), agent)
        ConversationTaint.objects.create(conversation=source, entitlement=ent)
        copy = duplicate_conversation(user_principal(user), source, title="Copy")
    assert copy.taint_tags.count() == 1


def test_an_administrator_duplicating_under_admin_sees_content_launders_nothing(agent):
    """`may_manage_conversation`'s first branch admits them, which is
    exactly the case ruling D's own finding names."""
    ent = make_entitlement()
    owner, admin = make_user(), make_admin()
    stream = _workstream(user_principal(owner))
    with posture("enterprise", admin_sees_content=True):
        source = create_conversation(user_principal(owner), agent, workstream=stream)
        ConversationTaint.objects.create(conversation=source, entitlement=ent)
        copy = duplicate_conversation(user_principal(admin), source, title="Copy")
    assert copy.workstream_id == stream.pk
    assert copy.taint_tags.count() == 1


def test_the_generalisation_needs_no_runtime_edit(agent, isolated_labels_registry):
    """§7.4 and §17.4's last item, and the whole test of whether the seam
    was drawn in the right place. When a generated image becomes a
    labelled thing, `tools/vision/apps.py` registers one
    `ArtifactLabels("output", ...)` and every turn that returned one
    starts tainting — with NO change to `agents/runtime/taint.py`, NO
    change to `_finish`, and NO migration."""
    ent = make_entitlement()
    register_artifact_labels(ArtifactLabels(
        "output", "agents.tests.test_taint._fake_output_resolver"))
    _FAKE_OUTPUT_LABELS[12] = ent.pk
    stream = _workstream()
    conversation = Conversation.objects.create(agent=agent, workstream=stream)
    turn = _tool_turn(conversation, ["output:12"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset({ent.pk})


_FAKE_OUTPUT_LABELS: dict[int, int] = {}


def _fake_output_resolver(pks):
    return frozenset(_FAKE_OUTPUT_LABELS[p] for p in pks if p in _FAKE_OUTPUT_LABELS)


def test_a_kind_with_no_registered_resolver_contributes_nothing_silently(agent):
    """Which is what makes owner decision 11's "generalises mechanically"
    true, and why §7.4 is a SCOPE STATEMENT rather than a code branch."""
    conversation = Conversation.objects.create(agent=agent, workstream=_workstream())
    turn = _tool_turn(conversation, ["input:5"])
    assert stamp_turn_taint(turn, conversation, turn.artifacts) == frozenset()


def _labelled_document(entitlement):
    from tools.rag.tests._helpers import make_document

    doc = make_document()
    doc.entitlement_labels.create(entitlement=entitlement)
    return doc


def _tool_turn(conversation, artifacts):
    return Turn.objects.create(
        conversation=conversation, index=Turn.next_index(conversation),
        role=Turn.Role.TOOL, text="", artifacts=list(artifacts),
        state=Turn.State.DONE, depth=0)


# --- the four end-to-end drivers, so the turn really runs -------------
#
# These four tests are the only ones in this module that drive a REAL
# turn rather than calling `stamp_turn_taint` directly, because the
# property each asserts is about WHEN the stamp happens relative to the
# turn's own commits -- which is exactly what a direct call cannot show.

def _drive_turn(conversation, principal=OPEN_PRINCIPAL):
    """Run one turn through `agents.runtime.loop._run_turn` with the
    payload `agents.runtime.jobs` would build. Re-raises whatever the
    engine raised, so a test can assert both the exception and the rows
    the turn left behind.

    `_run_turn(payload, models, ctx)` RESOLVES ITS OWN ROWS -- its first
    act is `Turn.objects.select_related(...).get(pk=payload["turn"])`
    (`agents/runtime/loop.py:171-174`) -- so this helper creates the
    placeholder assistant turn the queue would have created and hands
    over nothing but the payload.
    """
    turn = Turn.objects.create(
        conversation=conversation, index=Turn.next_index(conversation),
        role=Turn.Role.ASSISTANT, text="", state=Turn.State.QUEUED, depth=0)
    return loop_module._run_turn(
        {"turn": turn.pk, **payload_fields(principal)}, [], make_job_ctx())


def _fake_llm(selections):
    """A stand-in for the bound chat model: it returns `selections` (a
    callable, or a list consumed one call at a time) instead of calling
    an engine. The same "mock at the HTTP boundary" convention every
    other runtime test in this app uses."""
    llm = MagicMock()
    llm.chat.side_effect = selections if callable(selections) else list(selections)
    return llm


def _tool_selection_citing(document):
    """One `rag.ask` tool call whose result carries `document`'s
    reference -- the exact shape `tools.rag.tools._document_artifacts`
    mints."""
    return _selection("rag.ask", {"question": "what?"},
                      artifacts=(f"document:{document.pk}",))


def _llm_calling(selection_or_callable):
    return _fake_llm(selection_or_callable)


def _llm_that_cites_then_raises(document):
    """First call: the tool selection above. Second call: an engine
    error. The tool turn commits; the assistant turn never arrives."""
    return _fake_llm([_tool_selection_citing(document),
                      RuntimeError("the engine went away")])


def _run_delegate_that_cites(conversation, agent, document):
    """Run one `agent.<slug>` delegation whose inner turn cites
    `document`, and return the delegate's own `ToolResult` -- the value
    `agents/runtime/delegate.py::run_agent_tool` hands back to the
    parent."""
    from agents.runtime import delegate

    ctx = make_tool_ctx(conversation_id=str(conversation.pk), principal=OPEN_PRINCIPAL)
    with patch("agents.runtime.loop.gateway.get_llm_for",
               return_value=_llm_that_cites_then_answers(document)):
        return delegate.run_agent_tool({"task": "look it up"},
                                       replace(ctx, tool_key=f"agent.{agent.slug}"))


def _llm_that_cites_then_answers(document):
    return _fake_llm([_tool_selection_citing(document), _plain_answer("done")])
```

`make_job_ctx` and `make_tool_ctx` are **defined in `agents/tests/_helpers.py`** (lines 134 and
170) — the module this test module already imports `_workstream` from, so they join that line
rather than earning one of their own. `agents/runtime/tests/_helpers.py` re-exports both
(`:153-154`) and importing through it would also work; importing from the definition site is the
smaller indirection, and that file's own comment records why the copy count matters. The two engine stand-ins below are
**this module's own**, because no shared helper mints a tool selection today:

```python
def _selection(tool_key, kwargs, *, artifacts=()):
    """One `ChatResponse` carrying a single tool call, plus the artifacts
    the runner behind it will report.

    A LOCAL BUILDER, not a shared fixture: nothing in
    `agents/runtime/tests/_helpers.py` mints one today, and the repo's
    convention (`tools/rag/tests/test_jobs.py:41-45`) is a standalone
    copy per test module rather than a new shared surface for one caller.
    """
    from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole
    from llama_index.core.tools import ToolSelection

    response = ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, content=""))
    response.message.additional_kwargs["tool_calls"] = [
        ToolSelection(tool_id=tool_key, tool_name=wire_name(tool_key), tool_kwargs=kwargs)
    ]
    _ARTIFACTS_FOR[tool_key] = tuple(artifacts)
    return response


def _plain_answer(text):
    from llama_index.core.base.llms.types import ChatMessage, ChatResponse, MessageRole

    return ChatResponse(message=ChatMessage(role=MessageRole.ASSISTANT, content=text))


# What the fake runner reports back, keyed by tool key -- set by
# `_selection` and read by the registered stand-in runner the
# `isolated_tool_registry` fixture installs.
_ARTIFACTS_FOR: dict[str, tuple[str, ...]] = {}
```

with `from agents.contracts.toolschema import wire_name` at module scope, and the four end-to-end
tests taking `isolated_tool_registry` so the stand-in runner is registered and removed per test.

Those four are the only tests in this module that drive a REAL turn rather than calling
`stamp_turn_taint` directly, because the property each asserts is about **when** the stamp happens
relative to the turn's own commits — which is exactly what a direct call cannot show. They are the
four cases §17.4 names.

Add `isolated_labels_registry` to `agents/contracts/tests/_helpers.py`, the same shape as
`isolated_panel_registry`, saving and restoring `agents.contracts.artifacts._ARTIFACT_LABELS`.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest agents/tests/test_taint.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConversationTaint' from 'agents.models'`

- [ ] **Step 3: Add the two taint tables and the two conversation columns**

In `agents/models.py`, after `WorkstreamScopeEntitlement`:

```python
class ConversationTaint(models.Model):
    """One entitlement whose labelled material retrieval has actually
    returned into this conversation (owner decision 3b).

    ADDITIVE ONLY in v1: rows are created, never deleted, except by the
    conversation's own CASCADE and by the entitlement cascade (spec
    §8.4). Removal -- "this conversation no longer contains E" -- is a
    deferred item with a real design question behind it (spec §22).

    `first_turn` is THE CAUSING TURN, kept as a plain integer, not a
    ForeignKey: it is the evidence a person needs when they ask why a
    share went dormant, and a real FK would make deleting a turn delete
    the record of what that turn brought in.

    IT HANGS OFF THE CONVERSATION, NOT THE STREAM (author decision 9), so
    a loose conversation accumulates tags exactly the same way. Nothing
    reads them in v1 -- a loose conversation's share is IA-2's and this
    phase does not change it -- and the rows are what makes
    conversation-level share gating a later change with no back-fill.
    """

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name="taint_tags")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="conversation_taints")
    first_turn = models.BigIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["conversation", "entitlement"],
                                               name="uniq_conversation_taint")]
        indexes = [models.Index(fields=["entitlement"], name="agents_convtaint_ent")]


class WorkstreamTaint(models.Model):
    """The union of its conversations' tags, MATERIALISED (owner decision
    3b, author decision 26).

    Materialised, not derived, and that is THE DECISION rather than an
    optimisation. The read-time share gate (spec §12.2) runs on every
    non-owner view of a shared stream; deriving the union would mean a
    join across every conversation in the stream on every one of those
    reads and -- worse -- the read-time gate would be answering from a
    DIFFERENT QUERY than the share-time gate did, which is how two gates
    come to disagree about one fact. ONE WRITER (spec §7.3), in the same
    transaction as the conversation row it follows.

    TWO TABLES, NOT ONE WITH A NULLABLE PAIR OF PARENTS. They answer two
    questions with different readers: the conversation's tags are what a
    note document inherits and what the per-conversation display shows;
    the stream's tags are what both share gates read. One table with
    `conversation` XOR `workstream` would need the XOR check constraint,
    two partial uniques and a branch at every read, to save one migration
    operation.
    """

    workstream = models.ForeignKey(Workstream, on_delete=models.CASCADE,
                                   related_name="taint_tags")
    entitlement = models.ForeignKey("identity.Entitlement", on_delete=models.CASCADE,
                                    related_name="workstream_taints")
    first_conversation = models.UUIDField(null=True, blank=True)
    first_turn = models.BigIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workstream", "entitlement"],
                                               name="uniq_workstream_taint")]
        indexes = [models.Index(fields=["entitlement"], name="agents_wstaint_ent")]
```

and in `class Conversation`, after `workstream`:

```python
    # NEW (WS-2). The `Turn.index` this conversation was last consolidated
    # THROUGH, and when. Null = never. Two columns rather than a
    # `Consolidation` row per run: only the LATEST matters -- a
    # re-consolidation overwrites the note (owner decision 6) -- and a
    # history table with one meaningful row is a history table nobody
    # reads.
    consolidated_through_index = models.PositiveIntegerField(null=True, blank=True)
    consolidated_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Generate migration 3**

Run: `python manage.py makemigrations agents --name workstream_taint`
Expected: `agents/migrations/0007_workstream_taint.py`, with

```python
    dependencies = [
        ("identity", "0003_entitlement_and_grant"),
        ("agents", "0006_workstream"),
    ]
```

and exactly: `CreateModel(ConversationTaint)`, `CreateModel(WorkstreamTaint)`,
`AddField(conversation.consolidated_through_index)`, `AddField(conversation.consolidated_at)`.
No data migration — `consolidated_*` null means "never", which every existing conversation is.

Run: `python manage.py makemigrations --check --dry-run` → exit 0.

- [ ] **Step 5: Add the `ArtifactLabels` registry**

Append to `agents/contracts/artifacts.py` — **beside the vocabulary it keys on**, which is why it
lives here rather than in a new module:

```python
@dataclass(frozen=True)
class ArtifactLabels:
    """One column's answer to "what entitlements label the rows behind
    this artifact kind".

    `kind`     -- an entry of `ARTIFACT_KINDS`.
    `resolver` -- "package.module.function", with the signature
                  `(pks: frozenset[int]) -> frozenset[int]`, returning
                  the UNION of the entitlement ids labelling those rows.

    A DOTTED PATH, resolved at stamp time by `agents/runtime/taint.py`,
    never imported here -- the same mechanism `identity/contracts/
    cascades.py` and `models/contracts/jobkinds.py` already use, and for
    the same reason: `agents/` may not import `tools/` at all.

    A KIND WITH NO REGISTERED RESOLVER CONTRIBUTES NOTHING, SILENTLY.
    That is what makes owner decision 11's "the stamp generalises
    mechanically" true: when a generated image becomes a labelled thing,
    `tools/vision/apps.py` registers `ArtifactLabels("output", ...)` and
    every turn that returned one starts tainting, with NO change to any
    runtime module and NO migration. `agents/tests/test_taint.py` pins
    that with a fake resolver, so the claim is not a hope.
    """

    kind: str
    resolver: str

    def __post_init__(self) -> None:
        if self.kind not in ARTIFACT_KINDS:
            raise ValueError(
                f"{self.kind!r} is not an artifact kind; must be one of "
                f"{list(ARTIFACT_KINDS)}")
        if "." not in self.resolver:
            raise ValueError(
                f"ArtifactLabels({self.kind!r}).resolver must be a dotted path, "
                f"got {self.resolver!r}")


_ARTIFACT_LABELS: dict[str, ArtifactLabels] = {}


def register_artifact_labels(spec: ArtifactLabels) -> None:
    """Register `spec` under its `.kind`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _ARTIFACT_LABELS[spec.kind] = spec


def labels_resolver_for(kind: str) -> str | None:
    """The dotted path for `kind`, or `None` when nothing is registered
    -- which is the common case and is not an error."""
    spec = _ARTIFACT_LABELS.get(kind)
    return spec.resolver if spec is not None else None
```

with `from dataclasses import dataclass` added to the module's imports. It stays a rule-1 pure
leaf: a dataclass and a dict, no Django.

- [ ] **Step 6: Write the stamp**

Create `agents/runtime/taint.py`:

```python
"""What the work has touched, recorded at the moment it touches it.

ONE WRITER. `stamp_turn_taint` is the only function that writes
`ConversationTaint` or `WorkstreamTaint`, and it is called from exactly
one place: inside the `transaction.atomic()` that creates a TOOL TURN in
`agents/runtime/loop.py`.

THE TOOL TURN, NOT THE ASSISTANT TURN, and that is the correction spec
§7.2 exists to make. `run_loop`'s caller wraps `_run_turn` in a
`try/except` that, on any exception, flips the assistant turn to FAILED
with one `.update()` -- and `_finish` is reached only on the SUCCESS
path. The tool turns, meanwhile, are created and COMMITTED as they run,
each carrying the retrieved text in `text`/`data` and its `document:`
references in `artifacts`. So "retrieve a document labelled E, then the
assistant call raises" would leave the E material committed and rendered
in the thread with NO taint row for it, and both share gates would then
pass a stream that holds E material. That is the opposite of the
conservative direction this mechanism claims: it is "material is in
there and no gate knows", not "a share you expected does not work".

`_finish` THEREFORE GROWS NO TRANSACTION AT ALL. It stays the single
`save()` it is today.
"""
from __future__ import annotations

import logging

from django.utils.module_loading import import_string

from agents.contracts.artifacts import labels_resolver_for, parse_artifact
from agents.models import ConversationTaint, WorkstreamTaint
from identity import audit
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL

logger = logging.getLogger(__name__)


def _references_by_kind(artifacts) -> dict[str, set[int]]:
    """`{kind: {pk, ...}}` for every parseable reference in `artifacts`.

    `parse_artifact` is THE ONE PARSER and taint uses it, so the taint
    stamp cannot come to disagree with the chat page's own rendering
    about what a reference means -- including `mint_artifact`'s optional
    `:<title>` suffix, which that parser peels off. A malformed
    reference is SKIPPED rather than raising: this runs inside a turn's
    own transaction, and one bad string must never fail a turn.
    """
    out: dict[str, set[int]] = {}
    for reference in artifacts or ():
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            continue
        out.setdefault(kind, set()).add(pk)
    return out


def stamp_turn_taint(turn, conversation, artifacts, *, actor=None) -> frozenset[int]:
    """Record the entitlement labels of the rows `turn` returned, on the
    conversation and on its stream. Returns what was ADDED.

    Returns the empty set WITHOUT TOUCHING THE DATABASE when `artifacts`
    holds no reference of a kind that has a registered labels resolver --
    which is the overwhelmingly common case (a turn that called no
    retrieval tool) and must cost nothing. `agents/tests/test_taint.py`
    pins that with a query count.

    `actor` is the ACTING PRINCIPAL, read back from the job payload by
    the caller -- the acting rule, unchanged. The taint was caused by a
    person's turn, not by the box, AND THAT PERSON IS NOT ALWAYS THE
    STREAM'S OWNER: a share recipient's turn taints the owner's stream,
    which is ruling B (spec §23.B) and is the point of the mechanism
    rather than a flaw in it. One audit row per newly added tag, carrying
    its acting principal, is therefore the one place "who brought E into
    this stream" is answerable.
    """
    references = _references_by_kind(artifacts)
    if not references:
        return frozenset()

    ids: set[int] = set()
    for kind, pks in references.items():
        path = labels_resolver_for(kind)
        if path is None:
            # A KIND WITH NO RESOLVER CONTRIBUTES NOTHING, SILENTLY --
            # `output:` and `input:` today, because `tools/vision` has no
            # label table and a tool's own output text is not a labelled
            # row at all. Registering a resolver for a kind that has no
            # labels would be a query that always returns the empty set.
            continue
        try:
            ids |= import_string(path)(frozenset(pks))
        except Exception:  # noqa: BLE001 -- a broken resolver, not a broken turn
            logger.exception("taint: resolver %r for kind %r failed", path, kind)
    if not ids:
        return frozenset()

    actor = actor if actor is not None else OPEN_PRINCIPAL
    added_conversation = _add_conversation_tags(conversation, ids, turn, actor)
    added_stream = frozenset()
    if conversation.workstream_id:
        added_stream = _add_workstream_tags(conversation, ids, turn, actor)
    return added_conversation | added_stream


def _add_conversation_tags(conversation, ids, turn, actor) -> frozenset[int]:
    """Step 1 of spec §7.3.

    THE READ COMES FIRST, AND IT IS NOT AN OVERSIGHT.
    `bulk_create(ignore_conflicts=True)` cannot report which rows landed,
    and `fresh` is what drives the audit rows — one per (conversation,
    entitlement) FIRST SIGHTING, which is the whole point of §16.2. So
    the read is what makes the trail exactly one row per first sighting,
    and `ignore_conflicts` is the RACE GUARD against two turns stamping
    the same tag at once, not an optimisation that removes the read."""
    existing = set(conversation.taint_tags.filter(entitlement_id__in=ids)
                   .values_list("entitlement_id", flat=True))
    fresh = sorted(set(ids) - existing)
    if not fresh:
        return frozenset()
    ConversationTaint.objects.bulk_create(
        [ConversationTaint(conversation=conversation, entitlement_id=i, first_turn=turn.pk)
         for i in fresh],
        ignore_conflicts=True)
    for entitlement_id in fresh:
        # AUDITED INSIDE THE SAME TRANSACTION as the tag rows (author
        # decision 13): `identity.audit.record` never swallows, and an
        # audit row that survived a rolled-back tag row would name a
        # tainting that did not happen.
        audit.record(actor, actions.CONVERSATION_TAINTED, target_type="conversation",
                     target_key=conversation.pk, target_label=conversation.title,
                     entitlement=entitlement_id, turn=turn.pk)
    return frozenset(fresh)


def _add_workstream_tags(conversation, ids, turn, actor) -> frozenset[int]:
    """Step 2: the union upward, IN THE SAME TRANSACTION as the
    conversation rows it follows -- only when the conversation is in a
    stream. A loose conversation stops at step 1.

    Same read-then-`ignore_conflicts` shape as step 1, and for the same
    reason: the read drives the audit rows, the flag guards the race."""
    stream = conversation.workstream
    existing = set(stream.taint_tags.filter(entitlement_id__in=ids)
                   .values_list("entitlement_id", flat=True))
    fresh = sorted(set(ids) - existing)
    if not fresh:
        return frozenset()
    WorkstreamTaint.objects.bulk_create(
        [WorkstreamTaint(workstream=stream, entitlement_id=i,
                         first_conversation=conversation.pk, first_turn=turn.pk)
         for i in fresh],
        ignore_conflicts=True)
    for entitlement_id in fresh:
        audit.record(actor, actions.WORKSTREAM_TAINTED, target_type="workstream",
                     target_key=stream.pk, target_label=stream.name,
                     entitlement=entitlement_id, conversation=str(conversation.pk),
                     turn=turn.pk)
    return frozenset(fresh)
```

- [ ] **Step 7: Call it from the tool turn's own transaction**

**This is reconciliation R1 / author decision 2. There is no `transaction.atomic()` around the
tool turn today — `agents/runtime/loop.py:418` creates it bare, and the module imports no
`transaction` at all.** Add the import and the block:

```python
from django.db import transaction
```

and, at the create site:

```python
        # THE STAMP RIDES A WRITE THAT ALREADY HAPPENS (spec §7.2,
        # author decision 3). `Turn.artifacts` on the TOOL TURN is the
        # taint source of truth: it is already deduped on the bare
        # `document:<id>`, already guarded against a non-decimal id by
        # `_document_artifacts`, and already written here.
        #
        # THE `atomic()` BLOCK IS NEW. This create was bare before the
        # workstreams phase; the tag rows and the turn that caused them
        # must land together or not at all, or a crash between them
        # leaves either a turn whose material no gate knows about or a
        # tag naming a turn that does not exist.
        with transaction.atomic():
            tool_turn = Turn.objects.create(
                conversation=conversation,
                index=Turn.next_index(conversation),
                role=Turn.Role.TOOL,
                text=tool_text,
                tool_call=call_record,
                data=(outcome.result.data if outcome.result is not None else None),
                artifacts=list(outcome.result.artifacts) if outcome.result is not None else [],
                depth=depth,
                state=Turn.State.DONE,
                invocation_id=outcome.invocation_id,
                queue_job_id=job_ctx.job_id,
            )
            stamp_turn_taint(tool_turn, conversation, tool_turn.artifacts,
                             actor=principal)
```

with `from agents.runtime.taint import stamp_turn_taint` at module scope.

- [ ] **Step 8: Register the document resolver**

In `tools/rag/labels.py`, beside `document_label_ids` — which it generalises from one document to
a set:

```python
def entitlement_ids_for(pks) -> frozenset[int]:
    """The UNION of the entitlement ids labelling `pks` -- ONE QUERY.

    Registered from `tools/rag/apps.py` as `ArtifactLabels("document",
    ...)`, a DOTTED-PATH STRING, so `agents/runtime/taint.py` resolves it
    at stamp time without `agents/` importing `tools/` (import-law rule
    3).

    `document_label_ids` generalised from one document to a set: that
    function answers "which labels does THIS row carry" for a caller
    holding the row, and this one answers "which labels do ANY of these
    rows carry" for a caller holding only ids. Both read the TABLE, never
    the chunk-metadata cache, for the reason this module's own docstring
    gives: the cache is a copy, the table is the fact.

    The empty set for an empty input, without a query (author decision
    14).
    """
    pks = {int(p) for p in pks}
    if not pks:
        return frozenset()
    return frozenset(
        DocumentEntitlement.objects.filter(document_id__in=pks)
        .values_list("entitlement_id", flat=True))
```

and in `tools/rag/apps.py::RagConfig.ready()`:

```python
        # Taint (spec §7.2): which entitlements label the documents a
        # turn actually returned. A DOTTED-PATH STRING, so
        # `agents/runtime/taint.py` never imports this column.
        from agents.contracts.artifacts import ArtifactLabels, register_artifact_labels

        register_artifact_labels(
            ArtifactLabels("document", "tools.rag.labels.entitlement_ids_for"))
```

- [ ] **Step 9: Carry the tags on duplication**

In `agents/visibility.py::duplicate_conversation`, inside the existing `transaction.atomic()`,
after the `Turn.objects.bulk_create`:

```python
        # ALWAYS, not only for a stream conversation (ruling D): a loose
        # thread's tags are recorded too (author decision 9), and a copy
        # that dropped them would launder a loose conversation exactly as
        # it would a stream one.
        ConversationTaint.objects.bulk_create([
            ConversationTaint(conversation=copy, entitlement_id=t.entitlement_id,
                              first_turn=t.first_turn)
            for t in conversation.taint_tags.all()
        ])
```

with `ConversationTaint` added to this module's `from agents.models import ...` line.

- [ ] **Step 10: Grow the cascade to the taint tables**

In `agents/apps.py`, widen the cascade's label now that the rows exist (m10):

```python
            label="Workstream scopes and taint tags",
```

and in `agents/workstreams.py::workstream_entitlement_cascade`:

```python
def workstream_entitlement_cascade(entitlement_id: int, *, commit: bool) -> int:
    """This column's THIRD answer to "an entitlement is being deleted" --
    the stream's wall rows AND its taint tags, at both levels.

    DELETING AN ENTITLEMENT THEREFORE UN-TAINTS, and it says so in the
    trail (author decision 14). That is the one path by which a tag is
    removed in v1, and it is not an exception to "additive only" so much
    as the MEANING of deleting the entitlement: the label no longer
    exists, so no document carries it, so no share can be refused for
    lacking it. `tools.rag.labels.unlabel_all_for_entitlement` already
    makes the documents unlabelled in the same transaction, and the two
    cascades run under one `transaction.atomic()` in
    `identity.services.delete_entitlement`, so a stream cannot end up
    tagged with an entitlement its documents have lost.

    THE UNTAINT AUDIT ROWS ARE WRITTEN IN `commit=True` MODE (spec
    §16.1). Without them the `WORKSTREAM_TAINTED` rows would survive
    their own tags and `for_target("workstream", pk)` would show what
    came in and not that it left -- a trail that lies by omission about
    the only removal the product performs.

    NO CASCADE FOR `WorkstreamPin`: a pin dies with its stream or its
    document, both by `CASCADE`, and an entitlement delete removes
    neither.
    """
    from agents.models import ConversationTaint, WorkstreamTaint

    walls = WorkstreamScopeEntitlement.objects.filter(entitlement_id=entitlement_id)
    conversation_tags = ConversationTaint.objects.filter(entitlement_id=entitlement_id)
    stream_tags = WorkstreamTaint.objects.filter(entitlement_id=entitlement_id)
    total = walls.count() + conversation_tags.count() + stream_tags.count()
    if not commit:
        return total
    actor = SERVICE_PRINCIPAL
    for row in conversation_tags.select_related("conversation"):
        audit.record(actor, actions.CONVERSATION_UNTAINTED, target_type="conversation",
                     target_key=row.conversation_id,
                     target_label=row.conversation.title, entitlement=entitlement_id,
                     cause="entitlement_deleted")
    for row in stream_tags.select_related("workstream"):
        audit.record(actor, actions.WORKSTREAM_UNTAINTED, target_type="workstream",
                     target_key=row.workstream_id, target_label=row.workstream.name,
                     entitlement=entitlement_id, cause="entitlement_deleted")
    walls.delete()
    conversation_tags.delete()
    stream_tags.delete()
    return total
```

with `from identity import audit`, `from identity.contracts import actions` and
`from identity.contracts.principals import SERVICE_PRINCIPAL` added to this module's imports.

Add the four constants to `identity/contracts/actions.py` and to `AUDIT_ACTIONS`:

```python
WORKSTREAM_TAINTED = "workstream.tainted"
CONVERSATION_TAINTED = "conversation.tainted"
WORKSTREAM_UNTAINTED = "workstream.untainted"
CONVERSATION_UNTAINTED = "conversation.untainted"
```

That completes the **seventeen** actions spec §16.1 names, across fifteen rows: nine from Task 3,
two from Task 7, one (`DOCUMENT_CONTAINED`) from Task 8, four here, and `WORKSTREAM_CONSOLIDATED`
in Task 18.

- [ ] **Step 11: Run everything**

Add the third table to Task 10's open-box pin, now that it exists (m8):

```python
    for table in ("identity_entitlementgrant", "agents_workstreamscopeentitlement",
                  "agents_workstreamtaint"):
        assert _hits(captured, table) == 0, table
```

Run: `pytest agents/tests/test_taint.py agents/tests/test_wall_seams.py agents/contracts/tests/ -v`
Expected: PASS

Run: `pytest agents/ tools/ identity/ -q`
Expected: PASS, including the entitlement-delete confirmation naming three cascades and their
counts.

- [ ] **Step 12: Update the column READMEs**

`agents/README.md`: the two taint tables, the stamp's place in the turn (the tool turn's own
transaction, and why not `_finish`), the `ArtifactLabels` extension point, and that duplication
copies the tags. `tools/rag/README.md`: `entitlement_ids_for` and its registration.

- [ ] **Step 13: Commit**

```bash
git add agents/models.py agents/migrations/0007_workstream_taint.py \
        agents/contracts/artifacts.py agents/runtime/taint.py agents/runtime/loop.py \
        agents/visibility.py agents/workstreams.py tools/rag/labels.py tools/rag/apps.py \
        identity/contracts/actions.py agents/tests/test_taint.py \
        agents/contracts/tests/_helpers.py agents/README.md tools/rag/README.md
git commit -m "feat(agents): taint — the tool turn's stamp, the union upward, and the untaint cascade"
```

---

### Task 17: sharing a workstream, and the two gates

**Files:**
- Modify: `identity/access.py` (`entitlement_ids_for_subject`)
- Modify: `agents/visibility.py` (`workstream_taint_ids`, `NAME_CAP`, `name_for_viewer`,
  `share_workstream`, `revoke_workstream_share`, `share_list_for`; `visible_conversations`,
  `may_post_to` and `may_manage_conversation` each gain clauses)
- Modify: `agents/workstreams.py` (`StreamAccess`, `stream_access`)
- Modify: `agents/chat/views/workstreams.py` (the gate-two branch, the share view)
- Create: `agents/chat/templates/chat/workstream_dormant.html`
- Modify: `agents/chat/urls.py`, `identity/routes.py`
- Test: `agents/tests/test_workstream_sharing.py` (new), `agents/chat/tests/test_workstream_page.py` (append)

**Interfaces:**
- Consumes: `WorkstreamTaint` (Task 16); `Share.Target.WORKSTREAM` (Task 2);
  `entitlement_names` (Task 13).
- Produces: `identity.access.entitlement_ids_for_subject(*, user=None, group=None) -> frozenset[int]`;
  `agents.visibility.workstream_taint_ids(workstream) -> frozenset[int]`;
  `agents.visibility.NAME_CAP = 5`;
  `agents.visibility.name_for_viewer(missing_ids, viewer_principal, *, disclose_all=False) -> tuple[tuple[tuple[int, str], ...], int]`;
  `agents.visibility.ShareRefused(missing_ids, missing_names, unnamed_count, message)` — a `NamedTuple` whose `__str__` is `message`;
  `agents.visibility.share_workstream(principal, workstream, *, user=None, group=None, level)`;
  `agents.workstreams.StreamAccess(ok, missing, missing_names, unnamed_count, is_owner)` and
  `stream_access(principal, workstream, *, settings_row=None)`; url name `chat-workstream-share`.

- [ ] **Step 1: Write the failing sharing tests**

Create `agents/tests/test_workstream_sharing.py`. The whole of spec §17.5, in order:

```python
"""Two gates, one dormancy rule, and the platform's one scoped exception
to the 404 house rule."""
from __future__ import annotations

import pytest
from django.urls import reverse

from agents.models import Conversation, ConversationTaint, Share, WorkstreamTaint
from agents.tests._helpers import _workstream
from agents.visibility import (
    NAME_CAP, delete_workstream, may_manage_conversation, may_post_to, name_for_viewer,
    share_workstream, visible_conversations, workstream_taint_ids,
)
from agents.workstreams import stream_access
from identity.access import entitlement_ids_for_subject, owner_fields
from identity.testing import (
    grant, make_entitlement, make_group, make_user, posture, sign_in, user_principal,
)

pytestmark = pytest.mark.django_db


def test_gate_one_refuses_and_names_what_the_SHARER_holds_and_counts_the_rest():
    """RULING B (spec §23.B). The sharer does NOT necessarily hold every
    tag — a previous recipient's turn may have brought one in — so the
    message names what THIS VIEWER holds and counts the rest."""
    sharer, recipient = make_user(), make_user()
    finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
    grant(finance, user=sharer)
    stream = _workstream(user_principal(sharer))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=finance)
    WorkstreamTaint.objects.create(workstream=stream, entitlement=legal)
    with posture("enterprise"):
        result = share_workstream(user_principal(sharer), stream,
                                  user=recipient, level=Share.Level.USE)
    assert result.missing_ids == frozenset({finance.pk, legal.pk})
    assert [n for _, n in result.missing_names] == ["Finance"]
    assert result.unnamed_count == 1


def test_gate_one_names_both_when_the_sharer_holds_both():
    sharer, recipient = make_user(), make_user()
    finance, legal = make_entitlement(name="Finance"), make_entitlement(name="Legal")
    grant(finance, user=sharer)
    grant(legal, user=sharer)
    stream = _workstream(user_principal(sharer))
    for ent in (finance, legal):
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        result = share_workstream(user_principal(sharer), stream,
                                  user=recipient, level=Share.Level.USE)
    assert sorted(n for _, n in result.missing_names) == ["Finance", "Legal"]
    assert result.unnamed_count == 0


def test_a_share_succeeds_once_the_recipient_holds_every_tag():
    sharer, recipient = make_user(), make_user()
    ent = make_entitlement()
    grant(ent, user=sharer)
    grant(ent, user=recipient)
    stream = _workstream(user_principal(sharer))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        row = share_workstream(user_principal(sharer), stream,
                               user=recipient, level=Share.Level.USE)
    assert isinstance(row, Share)
    assert row.level == Share.Level.USE


def test_a_view_level_share_is_refused_by_name():
    """AUTHOR DECISION 16. A stream share that could not converse would
    be a reading list, and owner decision 7 says a recipient reads AND
    converses — so storing a `view` level no reader honours would be a
    column value with two meanings."""
    sharer, recipient = make_user(), make_user()
    stream = _workstream(user_principal(sharer))
    with posture("enterprise"):
        result = share_workstream(user_principal(sharer), stream,
                                  user=recipient, level=Share.Level.VIEW)
    assert result is not None and not isinstance(result, Share)
    assert "converse" in str(result).lower() or "use" in str(result).lower()
    assert Share.objects.count() == 0


def test_a_group_recipient_is_checked_against_the_GROUPS_own_grants():
    """AUTHOR DECISION 23. A group is the subject of a grant in this
    codebase (`grant_user_xor_group`), so "does this group hold E" is a
    ROW, not a computation over membership — and a member who personally
    lacks E is caught by the READ-TIME gate, which checks each actual
    reader."""
    sharer = make_user()
    member = make_user()
    group = make_group()
    group.user_set.add(member)
    ent = make_entitlement()
    grant(ent, user=sharer)
    grant(ent, group=group)
    stream = _workstream(user_principal(sharer))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        assert entitlement_ids_for_subject(group=group) == frozenset({ent.pk})
        row = share_workstream(user_principal(sharer), stream,
                               group=group, level=Share.Level.USE)
        assert isinstance(row, Share)
        # And the member, who personally lacks E, is refused at READ time.
        access = stream_access(user_principal(member), stream)
    assert access.ok is False
    assert access.missing == frozenset({ent.pk})


def test_gate_two_names_EVERY_missing_entitlement_capped_at_five():
    """RULING E (spec §23.E), and the reason it exists: applying ruling
    B's DEFAULT here would name NOTHING, ALWAYS — at gate two the viewer
    IS the recipient and `missing = taint_ids - held(recipient)` by
    construction, so `missing_ids & held(viewer)` is empty by definition
    and the page would read "and N entitlements you don't hold", the
    exact inverse of owner decision 8's own words."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    ents = [make_entitlement(name=f"E{i}") for i in range(7)]
    for ent in ents:
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        named, rest = name_for_viewer(
            frozenset(e.pk for e in ents), user_principal(reader), disclose_all=True)
    assert len(named) == NAME_CAP
    assert rest == 7 - NAME_CAP


def test_the_owners_dormant_marker_takes_the_DEFAULT_mode():
    """The two default-mode sites of §12.1's table, pinned APART rather
    than assumed distinct: gate one's refusal and the owner's dormant
    marker each name what THEIR OWN viewer holds and count the rest."""
    owner = make_user()
    held, unheld = make_entitlement(name="Held"), make_entitlement(name="Unheld")
    grant(held, user=owner)
    with posture("enterprise"):
        named, rest = name_for_viewer(frozenset({held.pk, unheld.pk}),
                                      user_principal(owner))
    assert [n for _, n in named] == ["Held"]
    assert rest == 1


def test_dormancy_is_computed_never_stored():
    """A tag added AFTER a share was made makes it dormant with NO WRITE
    anywhere (spec §5.8). A stored flag is stale the moment a grant
    moves, and grants moving is the entire reason the read-time gate
    exists."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        assert stream_access(user_principal(reader), stream).ok is True
        WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
        access = stream_access(user_principal(reader), stream)
    assert access.ok is False
    assert Share.objects.get().pk is not None      # no write of any kind


def test_the_owner_is_never_locked_out_of_their_own_stream_by_its_tags():
    """Under ruling B a recipient's turn taints the owner's stream, so
    the owner may hold NO GRANT for a tag on their own stream — and a
    stream whose owner could be shut out of it by a recipient's
    retrieval would be a space nobody could administer."""
    owner = make_user()
    ent = make_entitlement()
    stream = _workstream(user_principal(owner))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    with posture("enterprise"):
        access = stream_access(user_principal(owner), stream)
    assert access.ok is True and access.is_owner is True


def test_gate_two_answers_403_for_a_share_holder_and_404_with_the_row_deleted(client):
    """ASSERTED AS A PAIR, because the exception is only safe if the
    negative case holds. There is NO INPUT A STRANGER CAN SUPPLY that
    reaches the 403 branch: without the row, `visible_workstreams`
    excludes the stream and the view raises `Http404` before
    `stream_access` is called."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    share = Share.objects.create(target_type=Share.Target.WORKSTREAM,
                                 target_key=str(stream.pk), user=reader,
                                 level=Share.Level.USE)
    url = reverse("chat-workstream", args=[stream.pk])
    with posture("enterprise"):
        sign_in(client, reader)
        response = client.get(url)
        assert response.status_code == 403
        body = response.content.decode()
        assert "Finance" in body
        assert "Traceback" not in body
        share.delete()
        assert client.get(url).status_code == 404


def test_the_403_page_reveals_nothing_about_the_streams_contents(client, agent):
    """The fence: it reveals nothing about the stream's conversations,
    its documents or its other recipients (author decision 12)."""
    owner, reader, other = make_user(), make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Conversation.objects.create(agent=agent, workstream=stream, title="Secret thread")
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=other, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
    assert "Q3" in body                      # the name, which they already have
    assert "Secret thread" not in body
    assert other.username not in body


def test_a_dormant_share_still_LISTS_in_the_recipients_sidebar(client):
    """Hiding it would make a stream the recipient has been reading
    vanish with no sentence, which is strictly worse than the honest
    refusal — and the recipient already knows it exists."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement()
    stream = _workstream(**owner_fields(user_principal(owner)), name="Q3 planning")
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        sign_in(client, reader)
        body = client.get(reverse("chat-index")).content.decode()
    assert "Q3 planning" in body


def test_ruling_C_the_owner_reads_and_manages_a_recipients_thread(agent):
    """Without the owner clause, `create_conversation`'s
    `**owner_fields(principal)` stamp would make a recipient's new thread
    INVISIBLE to the stream's owner on every surface: the stream page
    would omit it, the owner-only consolidate could never reach it, and
    the `PROTECT` delete would refuse with a count of rows the owner can
    neither open nor delete."""
    owner, reader = make_user(), make_user()
    stream = _workstream(user_principal(owner))
    Share.objects.create(target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
                         user=reader, level=Share.Level.USE)
    with posture("enterprise"):
        theirs = Conversation.objects.create(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(reader)))
        assert visible_conversations(user_principal(owner)).filter(pk=theirs.pk).exists()
        assert may_manage_conversation(user_principal(owner), theirs) is True
        assert may_post_to(user_principal(owner), theirs) is True


def test_ruling_C_a_recipient_does_not_read_another_recipients_thread(agent):
    owner, one, two = make_user(), make_user(), make_user()
    stream = _workstream(user_principal(owner))
    for u in (one, two):
        Share.objects.create(target_type=Share.Target.WORKSTREAM,
                             target_key=str(stream.pk), user=u, level=Share.Level.USE)
    with posture("enterprise"):
        theirs = Conversation.objects.create(agent=agent, workstream=stream,
                                             **owner_fields(user_principal(one)))
        # A stream share reaches every conversation IN the stream, so
        # `two` READS it — that is §12.4's own rule. What `two` may not
        # do is MANAGE it.
        assert visible_conversations(user_principal(two)).filter(pk=theirs.pk).exists()
        assert may_manage_conversation(user_principal(two), theirs) is False


def test_every_row_of_the_does_not_reach_table_is_a_404_for_a_recipient(client, agent):
    """Spec §12.4's table, one negative test each — re-sharing, editing
    the wall, pinning, uploading, editing instructions/name/upload
    default, consolidating, deleting the stream, and managing a
    conversation they did not start."""
    owner, reader, other = make_user(), make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    owners_thread = Conversation.objects.create(
        agent=agent, workstream=stream, **owner_fields(user_principal(owner)))
    with posture("enterprise"):
        sign_in(client, reader)
        # re-sharing
        assert client.post(reverse("chat-workstream-share", args=[stream.pk]),
                           {"user": str(other.pk)}).status_code == 404
        # editing the wall
        assert client.post(reverse("chat-workstream-scope", args=[stream.pk]),
                           {"entitlements": []}).status_code == 404
        # pinning and unpinning, and uploading into the stream -- both
        # gated on `scope.may_upload`, which is False for a recipient
        assert client.post(reverse("rag-workstream-pin", args=[stream.pk]),
                           {"action": "pin", "document": "1"}).status_code == 404
        # editing instructions, the name and the upload default;
        # archiving; deleting the stream
        for action in ("rename", "description", "instructions", "upload_default",
                       "archive", "unarchive", "delete"):
            assert client.post(reverse("chat-workstream-edit", args=[stream.pk]),
                               {"action": action, "name": "x"}).status_code == 404, action
        # CONSOLIDATION'S ROWS ARE ASSERTED IN TASK 18, not here: the
        # route does not exist yet, and `reverse` would raise
        # `NoReverseMatch` at this task's own green boundary. See
        # `tools/rag/tests/test_consolidate.py::
        # test_a_recipient_gets_404_consolidating_anything_including_their_own_thread`,
        # which asserts ruling F for the owner's threads AND the
        # recipient's own, beside the route that implements it.
        #
        # deleting or renaming a conversation they did not start
        assert client.post(
            reverse("chat-conversation-delete", args=[owners_thread.pk])
        ).status_code == 404

    # And nothing was written by any of them.
    stream.refresh_from_db()
    assert stream.archived_at is None
    assert stream.scope_entitlements.count() == 0
    assert Share.objects.filter(target_type=Share.Target.WORKSTREAM).count() == 1


def test_delete_workstreams_count_matches_what_the_owner_can_actually_open(agent):
    """Ruling C's consequence (spec §8.1): `PROTECT`'s count is then
    always a count of rows the person reading the refusal can act on."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    # One thread the OWNER started, one the RECIPIENT started.
    Conversation.objects.create(agent=agent, workstream=stream,
                                **owner_fields(user_principal(owner)))
    Conversation.objects.create(agent=agent, workstream=stream,
                                **owner_fields(user_principal(reader)))

    with posture("enterprise"):
        message = delete_workstream(user_principal(owner), stream)
        openable = visible_conversations(user_principal(owner)).filter(
            workstream_id=stream.pk).count()

    assert "2 conversations" in message
    # Ruling C's consequence: the count and what the owner can actually
    # open are the same number, so the refusal is actionable rather than
    # a wall of rows the owner can neither open nor delete.
    assert openable == 2
```


- [ ] **Step 2: Run them and watch them fail**

Run: `pytest agents/tests/test_workstream_sharing.py -v`
Expected: FAIL — `cannot import name 'share_workstream' from 'agents.visibility'`

- [ ] **Step 3: Add the identity function**

In `identity/access.py`, beside `_grant_ids` — **the same one join, keyed differently**
(author decision 22):

```python
def entitlement_ids_for_subject(*, user=None, group=None) -> frozenset[int]:
    """Every entitlement id a SUBJECT holds -- a `User` row or a `Group`
    row, not a `Principal`.

    `held_entitlement_ids` takes a `Principal` and answers for the
    CALLER; the workstream share gate's subject is the RECIPIENT, who is
    a row the sharer picked off a `<select>` (spec §12.1). Same join
    `_grant_ids` already runs, keyed differently, on a seam every column
    may already import -- a new module would be a fifth sanctioned
    identity import for one function.

    A GROUP IS CHECKED AGAINST ITS OWN GRANTS, not its members'
    (author decision 23): a group is the subject of a grant in this
    codebase (`grant_user_xor_group`), so "does this group hold E" is a
    row rather than a computation over membership -- and a member who
    personally lacks E is caught by the read-time gate, which checks each
    actual reader.

    EMPTY when accounts are off, exactly as `held_entitlement_ids` is:
    there is no second account to share to on an open box, and both
    gates are present in code and unreachable in practice there.
    """
    if not accounts_on():
        return frozenset()
    if bool(user) == bool(group):
        return frozenset()
    rows = (EntitlementGrant.objects.filter(user=user) if user
            else EntitlementGrant.objects.filter(group=group))
    return frozenset(rows.values_list("entitlement_id", flat=True))
```

- [ ] **Step 4: Add gate one, the naming rule, and the share writers**

Append to `agents/visibility.py`:

```python
# The cap on how many entitlement names one message may disclose (spec
# §12.3, fence 3) -- a named constant for the reason
# `MAX_PINS_PER_STREAM`, `CONSOLIDATION_MAX_TURNS`,
# `WORKSTREAM_SIDEBAR_LIMIT` and `SIDEBAR_LIMIT` are: a bound a test
# asserts is a bound the code has to name.
NAME_CAP = 5


def workstream_taint_ids(workstream) -> frozenset[int]:
    """The stream's tag set.

    IT LIVES HERE, beside `share_workstream`, NOT in
    `agents/workstreams.py` -- and the direction has to be stated because
    the two agents-side stream modules meet in this task.
    `agents/workstreams.py` imports `agents/visibility.py`, NEVER THE
    REVERSE (spec §4.2). Putting this reader in `workstreams.py` would
    make `share_workstream` import it and close the cycle; `stream_access`
    reads it FROM here and the arrow stays one-way.
    """
    return frozenset(workstream.taint_tags.values_list("entitlement_id", flat=True))


def name_for_viewer(missing_ids, viewer_principal, *, disclose_all: bool = False):
    """Render `missing_ids` for whoever is READING this message.
    Returns `(named, unnamed_count)`.

    DEFAULT (`disclose_all=False`) -- NAMED: the ids this viewer holds;
    they can act on those and they already know the name. COUNTED:
    everything else, because an entitlement's NAME is not disclosed to
    somebody who neither owns nor holds it anywhere else on this platform
    (spec §12.3), and a refusal message is not the place to start.

    `disclose_all=True` -- every id NAMED, capped at `NAME_CAP` with the
    remainder counted. EXACTLY ONE CALLER: the dormant-share 403 page of
    spec §12.3, where owner decision 8 asks for the names in so many
    words and where the reader holds a live `Share` row on this stream.

    RULING E is why the switch exists. Applying the default at gate two
    would name NOTHING, ALWAYS: the viewer there IS the recipient, and
    `missing = taint_ids - held(recipient)` by construction, so
    `missing_ids & held(viewer)` is empty by definition and the page
    would read "and N entitlements you don't hold" -- the precise
    opposite of owner decision 8's own words.
    """
    if disclose_all:
        named = entitlement_names(missing_ids)
        return named[:NAME_CAP], max(0, len(named) - NAME_CAP)
    held = held_entitlement_ids(viewer_principal)
    named = entitlement_names(frozenset(missing_ids) & held)
    return named, len(frozenset(missing_ids) - held)


class ShareRefused(NamedTuple):
    """Gate one's refusal, as data the caller renders.

    UNLIKE `share_conversation`, WHICH RETURNS `None` FOR EVERY REFUSAL.
    That function's silence is right for its three refusals, which are
    all "you may not" or "that is not a valid request". This one has a
    fourth refusal that is neither: the recipient is missing
    entitlements, and naming the ones the SHARER THEMSELVES HOLDS is
    actionable rather than a leak.
    """

    missing_ids: frozenset
    missing_names: tuple
    unnamed_count: int
    message: str

    def __str__(self) -> str:
        return self.message


def share_workstream(principal, workstream, *, user=None, group=None, level):
    """Share `workstream`. A `Share` row on success, a `ShareRefused`
    naming the missing entitlements, or `None` when `principal` may not
    share at all (which the view answers 404 to).

    `level` MUST BE `use`, or the share is refused by name (author
    decision 16): owner decision 7 says a recipient reads AND converses,
    and storing a `view` level no reader honours would be a column value
    with two meanings.

    GATE ONE is two set operations. The refusal NAMES ONLY WHAT ITS
    VIEWER HOLDS and counts the rest -- ruling B, because the sharer does
    not necessarily hold every tag: a previous recipient's turn may have
    brought one in.
    """
    if not may_manage_workstream(principal, workstream):
        return None
    if bool(user) == bool(group):
        return None
    if level != Share.Level.USE:
        return ShareRefused(
            frozenset(), (), 0,
            "A workstream share lets somebody read and converse, so it is always at the "
            "'use' level. A view-only workstream share is not something this platform has.")
    missing = workstream_taint_ids(workstream) - entitlement_ids_for_subject(
        user=user, group=group)
    if missing:
        named, rest = name_for_viewer(missing, principal)
        return ShareRefused(missing, named, rest,
                            _refusal_sentence(named, rest))
    row, _ = Share.objects.update_or_create(
        target_type=Share.Target.WORKSTREAM, target_key=str(workstream.pk),
        user=user, group=group,
        defaults={"level": Share.Level.USE,
                  "shared_by_id": int(principal.key) if principal.kind == "user" else None},
    )
    audit.record(principal, actions.SHARE_ADDED, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 subject="user" if user else "group",
                 subject_key=(user or group).pk, level=Share.Level.USE)
    return row


def _refusal_sentence(named, unnamed_count) -> str:
    """Gate one's exact copy, in the two shapes spec §12.1 gives.

    The last clause is REAL ADVICE: a single conversation inside the
    stream may carry fewer tags than the stream does, and
    `chat-conversation-share` already exists.
    """
    names = [n for _, n in named]
    if names and not unnamed_count:
        joined = " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1
                              else names)
        return (f"Not shared. This workstream contains material from {joined}, and that "
                f"account holds "
                f"{'neither' if len(names) == 2 else 'none of them'}. Grant them, or "
                f"share a conversation instead.")
    if names:
        joined = ", ".join(names)
        return (f"Not shared. This workstream contains material from {joined}, and "
                f"{unnamed_count} more entitlement"
                f"{'' if unnamed_count == 1 else 's'} you don't hold, and that account "
                f"holds none of them. Grant what you can, or share a conversation instead.")
    return (f"Not shared. This workstream contains material from {unnamed_count} "
            f"entitlement{'' if unnamed_count == 1 else 's'} you don't hold, and that "
            f"account holds none of them. Ask an administrator, or share a conversation "
            f"instead.")


def revoke_workstream_share(principal, workstream, share_id):
    """Remove one `Share` row FROM THIS WORKSTREAM. A `RevokedShare`, or
    `None`. The same two refusals and the same `isdecimal()` guard
    `revoke_share` carries, and for the same reasons -- an IDOR that
    reads as a legitimate action in the audit log is the one this shape
    prevents."""
    if not may_manage_workstream(principal, workstream):
        return None
    if not str(share_id).isdecimal():
        return None
    row = Share.objects.filter(pk=int(share_id),
                               target_type=Share.Target.WORKSTREAM,
                               target_key=str(workstream.pk)).first()
    if row is None:
        return None
    revoked = RevokedShare(subject="user" if row.user_id else "group",
                           subject_key=row.user_id or row.group_id, level=row.level)
    row.delete()
    audit.record(principal, actions.SHARE_REVOKED, target_type="workstream",
                 target_key=workstream.pk, target_label=workstream.name,
                 subject=revoked.subject, subject_key=revoked.subject_key,
                 level=revoked.level)
    return revoked


def share_list_for(workstream, viewer_principal):
    """The owner's share list, each row marked live or DORMANT with its
    reason.

    ONE GRANTS QUERY, keyed by subject, rather than N -- the same
    batching `sidebar_context` does for its per-row predicate. Dormancy
    is COMPUTED, never stored (spec §5.8): a stored flag is stale the
    moment a grant moves, and grants moving is the entire reason the
    read-time gate exists.

    THE MARKER TAKES THE DEFAULT NAMING MODE (§12.1's table): it names
    only what the OWNER holds and counts the rest, because under ruling B
    the owner may hold no grant for a tag on their own stream.
    """
    tags = workstream_taint_ids(workstream)
    out = []
    for row in shares_for(Share.Target.WORKSTREAM, str(workstream.pk)):
        subject_ids = entitlement_ids_for_subject(user=row.user, group=row.group)
        missing = tags - subject_ids
        named, rest = name_for_viewer(missing, viewer_principal) if missing else ((), 0)
        out.append({"share": row, "dormant": bool(missing),
                    "named": named, "unnamed_count": rest})
    return out
```

with `shares_for` added to this module's `from agents.shares import ...` line, and
`entitlement_ids_for_subject`, `entitlement_names` added to its `from identity.access import ...`.

- [ ] **Step 5: Add gate two and the two visibility clauses**

In `agents/workstreams.py`:

```python
@dataclass(frozen=True)
class StreamAccess:
    """Why this principal may or may not read this stream, RIGHT NOW."""

    ok: bool
    missing: frozenset          # empty when ok
    missing_names: tuple        # for the reader's own error page
    unnamed_count: int
    # WHY, for the page and for the count. `via_share` was a sixth field
    # in an earlier draft and is dropped: nothing read it. `is_owner` IS
    # read -- the stream page renders the recipient's "somebody else's
    # workstream" line off it -- so it stays.
    is_owner: bool


def stream_access(principal, workstream, *, settings_row=None) -> StreamAccess:
    """GATE TWO. Every non-owner read of a shared stream re-checks: tags
    grow and grants are revoked, so a share that passed gate one is not
    therefore passing now.

    - Owner, or `sees_all_content` -> `ok=True`, NO TAG CHECK. THE OWNER
      IS NEVER LOCKED OUT OF THEIR OWN STREAM BY ITS TAGS -- the tags
      were caused by turns IN THIS STREAM, which is the owner's space,
      and a stream whose owner could be shut out of it by a recipient's
      retrieval would be a space nobody could administer. Note what this
      deliberately no longer says: not "they caused them". Under ruling B
      a recipient's turn taints the owner's stream, so the owner may hold
      no grant for a tag on their own stream (spec §24 concern 6).
    - A share row exists -> `missing = taint_ids - held(principal)`;
      `ok = not missing`.
    - No share row -> the caller never reached this function;
      `visible_workstreams` already excluded the row and the view already
      answered 404.

    ONE EXTRA QUERY per stream page view for a recipient, against an
    indexed unique constraint, on a page that is already doing several.
    """
    from agents.visibility import name_for_viewer, workstream_taint_ids

    if may_manage_workstream(principal, workstream, settings_row=settings_row):
        return StreamAccess(True, frozenset(), (), 0, True)
    missing = workstream_taint_ids(workstream) - held_entitlement_ids(
        principal, settings_row=settings_row)
    if not missing:
        return StreamAccess(True, frozenset(), (), 0, False)
    # `disclose_all=True` -- THE ONE CALLER OF THAT MODE (ruling E). At
    # gate two the viewer IS the recipient and the missing set is
    # disjoint from what they hold by definition, so the default mode
    # would name nothing and owner decision 8's own sentence would be
    # unwritable.
    named, rest = name_for_viewer(missing, principal, disclose_all=True)
    return StreamAccess(False, missing, named, rest, False)
```

with `held_entitlement_ids` added to this module's identity import.

In `agents/visibility.py::visible_conversations`, **two** new clauses, and this is the one place
the container→contents rule is written:

```python
    return qs.filter(
        owned_rows_q(principal)
        | Q(pk__in=shared_keys(Share.Target.CONVERSATION, principal))
        # A SHARE ON THE CONTAINER IMPLIES A SHARE ON THE CONTENTS
        # (spec §12.4): a stream share reaches every conversation in the
        # stream.
        | Q(workstream_id__in=shared_keys(Share.Target.WORKSTREAM, principal))
        # AN OWNER IS NOT OPAQUE TO THEIR OWN SPACE (ruling C): the
        # stream's owner reads every conversation in it, including ones a
        # recipient started there. Without this,
        # `create_conversation`'s `**owner_fields(principal)` stamp would
        # make a recipient's new thread invisible to the stream's owner
        # on every surface, while §7.3 still unioned its taint upward and
        # refused the owner's future shares for it.
        | Q(workstream__in=Workstream.objects.filter(owned_rows_q(principal)))
    ).distinct()
```

`may_post_to` gains the same two clauses (a stream share is `use` by construction), and
`may_manage_conversation` gains the stream-owner branch:

```python
    if may_read_owned_row(principal, conversation):
        return True
    # RULING C: consolidating and deleting a conversation in a stream is
    # available to its creator AND to the stream's owner, and to nobody
    # else. `PROTECT`'s count is then always a count of rows the person
    # reading the refusal can act on (spec §8.1).
    #
    # `conversation.workstream` IS ALREADY LOADED on the two callers that
    # ask this per row -- `agents/chat/sidebar.py` and the stream page --
    # because both `select_related("workstream")`. That is not an
    # optimisation; it is the condition of this branch being affordable,
    # and `test_sidebar.py` pins it with a query count at 1 row and at
    # 25.
    stream = conversation.workstream
    return stream is not None and may_read_owned_row(principal, stream)
```

- [ ] **Step 6: Add the gate-two branch and the share view**

In `agents/chat/views/workstreams.py::workstream_page`, between `_resolve` and the render:

```python
    access = stream_access(principal, stream, settings_row=settings_row_for(request))
    if not access.ok:
        # THE ONE ROUTE ON THIS PLATFORM THAT ANSWERS 403 ON A
        # ROW-ADDRESSED URL, and spec §12.3 fences it in three ways:
        # only a holder of a real, live `Share` row reaches this branch
        # (a stranger was already excluded by `visible_workstreams` and
        # got the house rule's 404); every missing entitlement is named
        # and nothing else is; and the list caps at `NAME_CAP`.
        #
        # It renders NO STREAM CONTENT -- not the conversations, not the
        # documents, not the other recipients. Only the name, which the
        # recipient already has in their sidebar.
        context = {"workstream": stream, "named": access.missing_names,
                   "unnamed_count": access.unnamed_count}
        # THE SIDEBAR IS RENDERED, and author decision 12's fence is
        # unaffected: this page must not reveal anything about THIS
        # stream's contents, and the sidebar reveals nothing about it at
        # all -- it is the recipient's own list, and
        # `test_a_dormant_share_still_LISTS_in_the_recipients_sidebar`
        # asserts the stream is in it. Without this the one page that
        # explains why they are locked out is the one page whose
        # navigation is empty.
        context.update(sidebar_context(principal, settings_row=settings_row_for(request)))
        return render(request, "chat/workstream_dormant.html", context, status=403)
```

`chat/workstream_dormant.html`:

```html
{% extends "chat/base.html" %}
{% block content %}
<article class="dormant">
  <h1>This workstream is not readable right now.</h1>
  <p>It contains material from entitlements you don’t hold:
    {% for id, name in named %}<strong>{{ name }}</strong>{% if not forloop.last %}, {% endif %}{% endfor %}{% if unnamed_count %}, and {{ unnamed_count }} more{% endif %}.</p>
  <p>Ask an administrator for those entitlements, or ask the workstream’s owner.</p>
  <p><a href="{% url 'chat-index' %}">All chats</a></p>
</article>
{% endblock %}
```

And the share view, `chat-workstream-share` — share and revoke on one URL keyed on a `share_id` in
the body, exactly as `chat-conversation-share` does, with gate one's refusal rendered as a
**message, not a 404**:

```python
@require_POST
def workstream_share(request, pk):
    """POST `/chat/w/<pk>/share/`. Owner or `sees_all_content`; a
    recipient may not re-share and gets 404."""
    principal, stream = _resolve(request, pk)
    if not may_manage_workstream(principal, stream):
        raise Http404("No such workstream.")
    if request.POST.get("action") == "revoke":
        revoke_workstream_share(principal, stream, request.POST.get("share", ""))
        return redirect("chat-workstream", pk=stream.pk)
    subject_user, subject_group = _share_subject(request)
    if subject_user is None and subject_group is None:
        return render(request, "chat/workstream.html",
                      _page_context(request, principal, stream,
                                    error="Pick an account or a group to share with."),
                      status=400)
    result = share_workstream(principal, stream, user=subject_user,
                              group=subject_group, level=Share.Level.USE)
    if isinstance(result, ShareRefused):
        # GATE ONE'S REFUSAL RENDERS AS A MESSAGE, NOT A 404 (spec §14):
        # the sharer may share this stream, and what failed is the
        # recipient's grants -- which is actionable, and which the
        # message names under ruling B's default mode.
        return render(request, "chat/workstream.html",
                      _page_context(request, principal, stream, error=str(result)),
                      status=400)
    return redirect("chat-workstream", pk=stream.pk)


def _share_subject(request):
    """`(user, group)` from the POST body -- exactly one, or `(None, None)`.

    THE SAME SHAPE `chat-conversation-share` ALREADY PARSES, and the same
    two guards: `isdecimal()` before `int()`, because the value arrives
    from a POST body and a non-numeric one would otherwise reach the
    manager and raise on a never-500 surface (`agents.visibility.
    revoke_share`'s own docstring records why `isdigit()` is not enough
    -- it admits characters like "²" that `int()` itself rejects); and
    the subject is resolved through `identity.access.share_subjects`,
    which already excludes the caller, so a hand-posted pk cannot name an
    account this page would never have offered.

    Neither field, or both, answers `(None, None)`: the form makes both
    cases unrepresentable, and `share_workstream`'s own
    `bool(user) == bool(group)` guard refuses them again.
    """
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    from identity.access import share_subjects

    offered = share_subjects(principal_for_request(request))
    raw_user = request.POST.get("user", "")
    raw_group = request.POST.get("group", "")
    if bool(raw_user) == bool(raw_group):
        return None, None
    if raw_user:
        if not raw_user.isdecimal():
            return None, None
        allowed = {pk for pk, _name in offered.get("users", ())}
        return (get_user_model().objects.filter(pk=int(raw_user)).first()
                if int(raw_user) in allowed else None), None
    if not raw_group.isdecimal():
        return None, None
    allowed = {pk for pk, _name in offered.get("groups", ())}
    return None, (Group.objects.filter(pk=int(raw_group)).first()
                  if int(raw_group) in allowed else None)
```

`share_subjects(principal)` is `identity/access.py`'s existing reader — it returns
`{"users": ((pk, name), ...), "groups": (...)}` and already excludes the caller, so the render half
and the gate half offer and accept the same set.

Section 9 of the stream page (`chat/_share_panel.html`'s shape, reused) renders
`share_list_for(stream, principal)` with each row marked live or dormant, and sections 8 (Tags)
and 9 (Sharing) are wrapped in `{% if accounts_on %}` so an open box renders neither.

- [ ] **Step 7: Wire the route and the matrix**

`agents/chat/urls.py`: `path("w/<int:pk>/share/", workstream_share, name="chat-workstream-share")`.
`identity/routes.py`: `"chat-workstream-share": "O",`.

- [ ] **Step 8: Add the entitlement-name disclosure sweep**

Append to `identity/tests/test_route_matrix.py` (or beside it):

```python
def test_no_route_other_than_the_dormant_share_page_names_an_entitlement_to_a_non_holder(
        client):
    """§12.3's property, pinned so this page STAYS the only one.

    Today, the name of an entitlement you neither own nor hold is
    disclosed to you by NO route: `identity-entitlements` is class S, the
    accounts page rendering `effective_entitlements` is class S, and the
    only entitlement names a non-admin sees anywhere come from
    `labelling_entitlements`, which filters to `owned_entitlement_ids`
    for a non-admin. The dormant-share 403 is the FIRST, and owner
    decision 8 asks for it deliberately, trading a name for
    actionability (spec §24 concern 7).
    """
    # Walk every non-S url_name with a signed-in non-admin who neither
    # owns nor holds `secret`, and assert `secret.name` appears in no
    # response body except `chat-workstream`'s 403.
    owner, reader = make_user(), make_user()
    secret = make_entitlement(name="ZzUnholdableSecret")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=secret)
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)

    named_by = []
    with posture("enterprise"):
        sign_in(client, reader)
        for url_name, klass in ROUTE_RULES.items():
            if klass == "S":
                continue          # the middleware refuses a non-admin first
            try:
                url = _reverse_for_sweep(url_name, workstream=stream)
            except NoReverseMatch:
                continue
            body = client.get(url).content.decode(errors="ignore")
            if secret.name in body:
                named_by.append(url_name)

    # EXACTLY ONE, and it is the fenced 403 (spec §12.3, §24 concern 7).
    assert named_by == ["chat-workstream"]
```

The sweep needs three things this module does not otherwise have — the `ROUTE_RULES` table it walks,
`NoReverseMatch` to skip a name it cannot fill, and `_reverse_for_sweep` itself:

```python
from django.urls import NoReverseMatch, reverse

from identity.routes import ROUTE_RULES


def _reverse_for_sweep(url_name, *, workstream):
    """A URL for `url_name` with placeholder arguments, or
    `NoReverseMatch` for a name whose signature this cannot fill.

    THE STREAM'S OWN PK for every integer argument, so the one route that
    is allowed to name an entitlement is actually REACHED by the sweep --
    a placeholder `1` would 404 on `chat-workstream` and the test would
    pass by never rendering the page it exists to check. A fresh `uuid4()`
    for every UUID argument, which no row has, so a conversation-addressed
    route answers 404 rather than leaking a different row's content into
    the sweep.

    Tried widest-first: no arguments, then one, then two. `NoReverseMatch`
    propagates for anything none of the three fits, and the caller skips
    that name -- a route this helper cannot address is a route this
    property cannot be checked on, which is honest and is why the skip is
    the caller's decision rather than a silent `return None`.
    """
    import uuid

    for args in ((), (workstream.pk,), (workstream.pk, workstream.pk)):
        try:
            return reverse(url_name, args=args)
        except NoReverseMatch:
            continue
    return reverse(url_name, args=(uuid.uuid4(),))
```

and the module-level imports the body uses: `Conversation` is not needed, but `ROUTE_RULES`,
`NoReverseMatch`, `WorkstreamTaint`, `Share`, `_workstream`, `owner_fields`, `make_user`,
`make_entitlement`, `posture`, `sign_in` and `user_principal` all are — every one of them is
already in this module's header from the tests above it except the first two, which this step adds.

- [ ] **Step 9: Run everything**

Run: `pytest agents/tests/test_workstream_sharing.py agents/chat/tests/ identity/tests/ -v`
Expected: PASS

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 10: Update `identity/README.md`**

The two new `identity.access` functions, the seventeen new audit actions, the note that a
workstream is **not** an identity concept even though it carries entitlement sets, and the one
route that now names an entitlement to a non-admin.

- [ ] **Step 11: Commit**

```bash
git add identity/access.py agents/visibility.py agents/workstreams.py \
        agents/chat/views/workstreams.py agents/chat/urls.py identity/routes.py \
        agents/chat/templates/chat/workstream_dormant.html \
        agents/chat/templates/chat/workstream.html \
        agents/tests/test_workstream_sharing.py identity/tests/test_route_matrix.py \
        identity/README.md
git commit -m "feat(agents): both workstream share gates, dormancy, and the fenced 403"
```

---

### Task 18: consolidation into stream notes

**Files:**
- Create: `tools/rag/distil.py`
- Modify: `tools/rag/jobs.py` (four job functions), `tools/rag/apps.py` (the job kind)
- Modify: `tools/rag/models.py` (`Document.notes_conversation_id`, the partial unique)
- Create: `tools/rag/migrations/0017_document_notes.py`
- Modify: `config/settings.py` (`NOTES_DIR`)
- Modify: `agents/workstreams.py` (`transcript_for`, `record_consolidation`,
  `taint_ids_for_conversation`, `staleness_for`)
- Modify: `agents/chat/views/workstreams.py`, `agents/chat/urls.py`, `identity/routes.py`
- Modify: `identity/contracts/actions.py` (`WORKSTREAM_CONSOLIDATED`)
- Test: `tools/rag/tests/test_consolidate.py`, `tools/rag/tests/test_distil.py` (new)

**Interfaces:**
- Consumes: `ConversationTaint`, `Conversation.consolidated_*` (Task 16); `stream_access`,
  `may_manage_workstream` (Tasks 4, 17); `set_document_workstream` (Task 8);
  `set_document_labels`, `stage_document`, `run_ingest_or_fail` (existing).
- Produces: `tools.rag.distil.DISTILLATION_PROMPT`, `CONSOLIDATION_MAX_TURNS = 400`,
  `distil_conversation(turns, *, llm=None) -> str`; the `rag.consolidate` job kind with
  `plan_consolidate`, `run_consolidate`, `summarize_consolidate`, `on_consolidate_terminal`;
  `agents.workstreams.transcript_for(principal, conversation_id, *, limit)`,
  `record_consolidation(conversation_id, *, through_index, at)`,
  `taint_ids_for_conversation(conversation_id) -> frozenset[int]`,
  `staleness_for(conversations) -> dict`; url name `chat-workstream-consolidate`;
  `settings.NOTES_DIR`.

- [ ] **Step 1: Write the failing distillation and consolidation tests**

Create `tools/rag/tests/test_distil.py`:

```python
"""The distillation constant and its one call.

No `pytestmark`: nothing here touches the database. Every test imports
locally, so the module stays importable with no Django settings — the
same shape `tools/rag/tests/test_extract.py` has for `EXTRACTION_PROMPT`,
which is the precedent this constant follows.
"""
from __future__ import annotations


def test_the_distillation_prompt_is_the_documented_constant():
    """Asserted the way `tools/rag/tests/test_extract.py:45` asserts
    `EXTRACTION_PROMPT` — the one precedent in the tree, and this is the
    second instance of it."""
    from tools.rag.distil import DISTILLATION_PROMPT

    assert DISTILLATION_PROMPT.startswith("Distil the conversation below")
    assert "self-contained note" in DISTILLATION_PROMPT
    assert "\n" not in DISTILLATION_PROMPT     # implicit concatenation, no indentation


def test_the_prompt_describes_the_OUTPUT_and_addresses_no_particular_engine():
    """PLATFORM BEHAVIOR, not a model default: the wording is what makes
    "a retrievable note, not a chat recap" true of every engine the chat
    role could ever be bound to, regardless of that model's own
    summarising manners.

    Asserted STRUCTURALLY -- every sentence is an instruction about the
    OUTPUT, and the prompt contains no capitalised token that is not
    sentence-initial -- rather than against a list of names to avoid.
    Enumerating vendors in a test would put exactly the strings Global
    Constraint 1 forbids into a committed file, which is the rule this
    test exists to keep.
    """
    from tools.rag.distil import DISTILLATION_PROMPT

    lowered = DISTILLATION_PROMPT.lower()
    # It never addresses an engine, names a role, or mentions the thing
    # doing the work -- it describes what must come out.
    for forbidden in ("you are", "as an", "assistant", "model", "engine"):
        assert forbidden not in lowered, forbidden
    # And no proper noun: every capital is sentence-initial.
    sentences = [t.strip() for t in DISTILLATION_PROMPT.split(". ") if t.strip()]
    for sentence in sentences:
        assert sentence[1:] == sentence[1:].lower() or "-" in sentence, sentence


def test_it_sends_the_constant_and_the_transcript_in_one_message():
    """The body is EXERCISED, not only its constant asserted. Every
    consolidation test patches this function out, so without this case an
    empty body would not surface until production (M20)."""
    from unittest.mock import MagicMock

    from tools.rag.distil import DISTILLATION_PROMPT, distil_conversation

    llm = MagicMock()
    llm.chat.return_value.message.content = "  The note.  "
    out = distil_conversation(
        ({"role": "user", "text": "What did we decide?"},
         {"role": "assistant", "text": "To ship in Q3."}), llm=llm)

    assert out == "The note."
    (messages,), _kwargs = llm.chat.call_args
    assert len(messages) == 1
    content = messages[0].content
    assert content.startswith(DISTILLATION_PROMPT)
    assert "user: What did we decide?" in content
    assert "assistant: To ship in Q3." in content


def test_it_refuses_to_resolve_its_own_model():
    """A function that could quietly resolve one would be a second
    binding path beside the planner's."""
    import pytest

    from tools.rag.distil import distil_conversation

    with pytest.raises(ValueError):
        distil_conversation(({"role": "user", "text": "hi"},))


def test_the_cap_is_named_and_is_not_the_prompt_budget():
    """NOT `HISTORY_TURNS = 20` — that is a live turn's prompt budget,
    and distilling only the last twenty turns would silently make "the
    conversation" mean something else."""
    from agents.limits import HISTORY_TURNS
    from tools.rag.distil import CONSOLIDATION_MAX_TURNS

    assert CONSOLIDATION_MAX_TURNS == 400
    assert CONSOLIDATION_MAX_TURNS != HISTORY_TURNS
```

Create `tools/rag/tests/test_consolidate.py`. **The module header first** — without it twelve
DB-touching tests error on database access, and four use `client` as a bare global:

```python
"""Consolidation — the note document, the overwrite, and the window
between destroying the old note and writing the new one.

Every test here patches `tools.rag.jobs.distil_conversation`, NOT
`tools.rag.distil.distil_conversation`: `jobs.py` imports the name at
module scope beside `answer_question`, so the patch has to land on the
binding the handler actually reads -- the same target
`test_jobs.py:430` already patches for `answer_question`, and the same
trap.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from agents.contracts.workstreams import WorkstreamScope
from agents.workstreams import record_consolidation, staleness_for
from identity import audit
from identity.access import held_entitlement_ids, owned_entitlement_ids, owner_fields
from identity.contracts import actions
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.testing import (
    make_entitlement, make_user, posture, sign_in, user_principal,
)
from tools.rag import ingest, jobs, store
from tools.rag.access import readable_documents
from tools.rag.distil import CONSOLIDATION_MAX_TURNS
from tools.rag.models import Document
from tools.rag.tests._helpers import _workstream, make_document, make_job_ctx
from tools.rag.workstreams import stream_documents

pytestmark = pytest.mark.django_db
```

`ConversationTaint`, `Turn`, `Conversation`, `Share` and `Agent` are reached through
`django.apps.apps.get_model` inside the helpers below rather than imported, so this module keeps
importing nothing of `agents.models` — the rule `tools/rag/tests/_helpers.py::_workstream` already
follows for the same reason.

`cancel_job` is `models.queue.backend.cancel_job`, imported **inside** the one test that uses it:
production code in this column may not reach `models.queue.backend` (import-law rule 2), and while
test files are exempt from that sweep, a module-scope import would put the forbidden name in the
module's own header where a reader would reasonably read it as sanctioned.

Then spec §17.6's eight items:

```python
def test_the_note_is_one_contained_document_labelled_with_the_conversations_tags():
    """§19.2 done-when 6: one contained note document titled
    `Notes — <title>`, `origin=notes`, labelled with the conversation's
    tags, retrievable by that stream's next turn and by no other."""
    ent = make_entitlement()
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=3)
    ConversationTaint.objects.create(conversation=conversation, entitlement=ent)

    with patch("tools.rag.jobs.distil_conversation", return_value="The note."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    note = Document.objects.get(notes_conversation_id=conversation.pk)
    assert note.title == f"Notes — {conversation.title}"
    assert note.origin == Document.Origin.NOTES
    assert note.workstream_id == stream.pk
    assert note.extraction["method"] == "distillation"
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {ent.pk}
    assert Document.objects.filter(notes_conversation_id=conversation.pk).count() == 1
    # Contained, so it is in this stream's corpus and nowhere else.
    other = _workstream()
    other_scope = WorkstreamScope(workstream_id=other.pk, wall=frozenset(),
                                  default_upload_placement="", may_upload=True)
    assert not stream_documents(OPEN_PRINCIPAL, other_scope).filter(pk=note.pk).exists()


def test_re_consolidating_overwrites_the_same_row_and_re_ingests():
    """SAME PK, SAME STORE DIRECTORY — and an old turn's `document:<id>`
    reference still resolves. `stage_document` dedups on
    `original_path`: the second consolidation writes the same path, finds
    the existing row, sees a changed `file_hash`, calls
    `_delete_existing_data`, and re-ingests IN PLACE. Owner decision 6's
    "overwritten and re-ingested", delivered by the ingest path exactly
    as it already works, with no new code for the overwrite case."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=2)

    with patch("tools.rag.jobs.distil_conversation", return_value="First."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())
    note = Document.objects.get(notes_conversation_id=conversation.pk)
    first_pk, first_dir = note.pk, store.document_dir(note.id)

    with patch("tools.rag.jobs.distil_conversation", return_value="Second, longer."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    note.refresh_from_db()
    # SAME ROW, SAME ID, SAME STORE DIRECTORY -- `stage_document` dedups
    # on `original_path`, sees a changed `file_hash`, calls
    # `_delete_existing_data` and re-ingests IN PLACE.
    assert Document.objects.filter(notes_conversation_id=conversation.pk).count() == 1
    assert note.pk == first_pk
    assert store.document_dir(note.id) == first_dir
    assert note.status == Document.Status.READY
    assert "Second" in Path(note.source_path).read_text()
    # And every old `document:<id>` reference still resolves.
    assert readable_documents(OPEN_PRINCIPAL,
                              workstream_id=stream.pk).filter(pk=first_pk).exists()


def test_a_re_consolidation_whose_re_ingest_FAILS_is_visible_and_repairable(client):
    """M13 (spec §10.4, §24 concern 5), all four properties. Step 3
    destroys the previous note before step 6 recreates it, and that
    window is real: `stage_document`, on a changed hash, runs
    `_delete_existing_data(existing)` inside its own transaction, then
    sets PENDING. If step 6 then fails, the note row survives with NO
    chunks and NO stored file.

    Bounded by three things and fixed by a fourth: the model call
    precedes the destroy (so a distillation failure destroys nothing);
    the ROW is not lost, only its content; the stream page renders the
    FAILED chip rather than a silent absence; and the stream page offers
    RE-CONSOLIDATE on it — the whole repair path, in one click, from the
    page it happened on. Without that button the documented repair is
    `document_reingest` from the library page, which a non-admin stream
    owner cannot reach for a contained document at all."""
    stream = _workstream(**owner_fields(user_principal(owner := make_user())))
    conversation = _conversation_with_turns(stream, count=2,
                                            principal=user_principal(owner))

    with patch("tools.rag.jobs.distil_conversation", return_value="First."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())
    note_pk = Document.objects.get(notes_conversation_id=conversation.pk).pk

    # Force step 6 to raise, AFTER step 3 has already destroyed the
    # previous note's chunks and stored file.
    with patch("tools.rag.jobs.distil_conversation", return_value="Second."), \
         patch("tools.rag.ingest.run_ingest_or_fail",
               side_effect=RuntimeError("the embed model is down")):
        with pytest.raises(RuntimeError):
            jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    note = Document.objects.get(pk=note_pk)
    # (a) THE ROW IS NOT LOST, only its content.
    assert note.status == Document.Status.FAILED
    assert note.status_detail
    assert note.notes_conversation_id == conversation.pk

    with posture("enterprise"):
        sign_in(client, owner)
        body = client.get(reverse("chat-workstream", args=[stream.pk])).content.decode()
        # (b) THE STREAM PAGE SAYS SO, rather than a silent absence.
        assert "FAILED" in body
        # (c) AND OFFERS RE-CONSOLIDATE ON IT -- the whole repair path,
        # from the page it happened on. Without it the documented repair
        # is `document_reingest` from the library page, which a non-admin
        # stream owner cannot reach for a contained document at all.
        assert "Re-consolidate" in body
        # (d) AND PRESSING IT RESTORES A READABLE NOTE.
        with patch("tools.rag.jobs.distil_conversation", return_value="Third."):
            client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                        {"conversation": str(conversation.pk)})
            _run_one_worker_tick()

    note.refresh_from_db()
    assert note.status == Document.Status.READY


def test_consolidating_A_leaves_Bs_note_byte_identical():
    """IT NEVER CASCADES. One conversation, one note."""
    stream = _workstream()
    a = _conversation_with_turns(stream, count=2, title="A")
    b = _conversation_with_turns(stream, count=2, title="B")

    with patch("tools.rag.jobs.distil_conversation", return_value="B's note."):
        jobs.run_consolidate(_payload(b), _refs(), make_job_ctx())
    b_note = Document.objects.get(notes_conversation_id=b.pk)
    before = (b_note.file_hash, Path(b_note.source_path).read_bytes(),
              b_note.updated_at)

    with patch("tools.rag.jobs.distil_conversation", return_value="A's note."):
        jobs.run_consolidate(_payload(a), _refs(), make_job_ctx())

    b_note.refresh_from_db()
    # IT NEVER CASCADES. One conversation, one note: consolidating A does
    # not touch B's note, does not regenerate a stream digest, and does
    # not re-ingest anything but its own row.
    assert (b_note.file_hash, Path(b_note.source_path).read_bytes(),
            b_note.updated_at) == before


def test_the_database_refuses_a_second_note_for_one_conversation():
    """`uniq_notes_per_conversation`, asserted AT THE DATABASE — enforced
    by the constraint and not only by the job that writes it: a
    re-consolidation racing itself would otherwise produce two notes and
    the stream page would show both."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=1)
    make_document(workstream=stream, origin=Document.Origin.NOTES,
                  notes_conversation_id=conversation.pk)

    # ENFORCED BY THE DATABASE and not only by the job that writes it: a
    # re-consolidation racing itself would otherwise produce two notes
    # and the stream page would show both.
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_document(workstream=stream, origin=Document.Origin.NOTES,
                          notes_conversation_id=conversation.pk)

    # And a NULL is not constrained: every universal document has one.
    make_document()
    make_document()


def test_a_cancelled_while_queued_job_clears_the_in_flight_marker(client):
    """Asserted the way `on_ingest_terminal`'s test does."""
    stream = _workstream(**owner_fields(user_principal(owner := make_user())))
    conversation = _conversation_with_turns(stream, count=1,
                                            principal=user_principal(owner))

    with posture("enterprise"):
        sign_in(client, owner)
        client.post(reverse("chat-workstream-consolidate", args=[stream.pk]),
                    {"conversation": str(conversation.pk)})
        job_id = _latest_job_id("rag.consolidate", conversation)
        # A second submission while one is in flight is refused with 409,
        # the shape `start_turn`'s own in-flight refusal already uses.
        assert client.post(
            reverse("chat-workstream-consolidate", args=[stream.pk]),
            {"conversation": str(conversation.pk)}).status_code == 409

        cancel_job(job_id)
        # `on_terminal` runs AFTER the terminal write commits -- one
        # conditional `UPDATE` filtered on the marker still being
        # present, never a read-then-save, exactly as
        # `on_ingest_terminal` is.
        _run_on_commit_callbacks()

        # The button is offered again.
        assert client.post(
            reverse("chat-workstream-consolidate", args=[stream.pk]),
            {"conversation": str(conversation.pk)}).status_code in (302, 200)


def test_a_conversation_longer_than_the_cap_distils_its_most_recent_N_and_says_so():
    """An honest partial beats a failure and beats a silent truncation.
    THE FIRST LINE IS THE JOB'S OWN PROSE (author decision 17), not the
    model's, so a model that ignored an instruction cannot make the note
    lie about its own provenance."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream,
                                            count=CONSOLIDATION_MAX_TURNS + 30)
    seen = {}

    def _capture(turns, *, llm=None):
        seen["count"] = len(turns)
        seen["first"] = turns[0]["text"]
        return "The note."

    with patch("tools.rag.jobs.distil_conversation", side_effect=_capture):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    # The MOST RECENT N, not the first N.
    assert seen["count"] == CONSOLIDATION_MAX_TURNS
    assert "turn-30" in seen["first"]

    note = Document.objects.get(notes_conversation_id=conversation.pk)
    text = Path(note.source_path).read_text()
    # AN HONEST PARTIAL beats a failure and beats a silent truncation --
    # and the sentence is the JOB's own prose, not the model's (author
    # decision 17), so a model that ignored an instruction cannot make
    # the note lie about its own provenance.
    assert str(CONSOLIDATION_MAX_TURNS) in text
    assert "most recent" in text
    assert str(conversation.pk) in text


def test_the_note_inherits_the_taint_and_step_5_is_a_widening_only():
    """Owner decision 6: no laundering. A re-consolidation recomputes the
    labels from the conversation's CURRENT tags, which are additive, so
    the note's labels only ever GROW — which is the property the whole
    step exists for: a note can never become MORE readable than the
    conversation it came from."""
    one, two = make_entitlement(), make_entitlement()
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=2)
    ConversationTaint.objects.create(conversation=conversation, entitlement=one)

    with patch("tools.rag.jobs.distil_conversation", return_value="First."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())
    note = Document.objects.get(notes_conversation_id=conversation.pk)
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {one.pk}

    # The conversation acquires a second tag, then is re-consolidated.
    ConversationTaint.objects.create(conversation=conversation, entitlement=two)
    with patch("tools.rag.jobs.distil_conversation", return_value="Second."):
        jobs.run_consolidate(_payload(conversation), _refs(), make_job_ctx())

    # A WIDENING ONLY. The tags are additive, so the note's labels only
    # ever GROW -- which is the property the whole step exists for: a
    # note can never become MORE readable than the conversation it came
    # from.
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {
        one.pk, two.pk}


def test_run_consolidate_is_exempt_from_the_labelling_authority_rule():
    """AUTHOR DECISION 27, and THE EXEMPTION IS THE POINT.
    `set_document_labels`' docstring says "THE CALLER CHECKS THE
    PREDICATE"; `run_consolidate` checks neither `may_label_document` nor
    the owner-of-every-entitlement rule, because it applies
    `taint_ids_for_conversation(cid)`, which under ruling B can contain
    entitlements the actor neither owns nor holds. The authority rule
    governs a person CHOOSING labels; nobody is choosing here — the
    labels are copied from what the material already carried, and
    refusing to copy one because the actor does not own it is precisely
    the laundering owner decision 6 forbids."""
    # The acting principal neither OWNS nor HOLDS the tag -- which under
    # ruling B is a real state: a share recipient's turn can put material
    # into the owner's stream that the owner holds no grant for.
    owner = make_user()
    foreign = make_entitlement(name="Foreign")
    stream = _workstream(**owner_fields(user_principal(owner)))
    conversation = _conversation_with_turns(stream, count=2,
                                            principal=user_principal(owner))
    ConversationTaint.objects.create(conversation=conversation, entitlement=foreign)

    with posture("enterprise"):
        assert foreign.pk not in held_entitlement_ids(user_principal(owner))
        assert foreign.pk not in owned_entitlement_ids(user_principal(owner))
        with patch("tools.rag.jobs.distil_conversation", return_value="The note."):
            jobs.run_consolidate(_payload(conversation, actor=user_principal(owner)),
                                 _refs(), make_job_ctx())

    note = Document.objects.get(notes_conversation_id=conversation.pk)
    # THE EXEMPTION IS THE POINT: refusing to copy the label because the
    # actor does not own it is precisely the laundering owner decision 6
    # forbids.
    assert set(note.entitlement_labels.values_list("entitlement_id", flat=True)) == {
        foreign.pk}
    # And the `DOCUMENT_LABELLED` row is written in the actor's name.
    labelled = [r for r in audit.for_target("document", note.pk)
                if r.action == actions.DOCUMENT_LABELLED]
    assert labelled and labelled[0].actor_key == str(owner.pk)
    # The exemption is NAMED in the handler's own docstring, so a reader
    # of `set_document_labels`' callers can see why one does not check.
    assert "THE CALLER CHECKS THE PREDICATE" in jobs.run_consolidate.__doc__ \
        or "exempt" in jobs.run_consolidate.__doc__.lower()


def test_a_recipient_gets_404_consolidating_anything_including_their_own_thread(client):
    """RULING F, THE NEGATIVE (spec §23.F), asserted beside ruling C's
    positive because the two are one sentence apart and an implementer
    reading only one of them would build the other wrongly.

    Consolidation writes a stream-contained `Document`, labelled from the
    stream's taint set and ingested on the owner's box — a STREAM
    MUTATION, and it belongs beside re-sharing, wall edits and pin
    changes."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    owners_thread = _conversation_with_turns(stream, count=1,
                                             principal=user_principal(owner))
    own_thread = _conversation_with_turns(stream, count=1,
                                          principal=user_principal(reader))

    with posture("enterprise"):
        sign_in(client, reader)
        for thread in (owners_thread, own_thread):
            response = client.post(
                reverse("chat-workstream-consolidate", args=[stream.pk]),
                {"conversation": str(thread.pk)})
            assert response.status_code == 404
            assert "Traceback" not in response.content.decode()

    # RULING F: consolidation writes a stream-contained `Document`,
    # labelled from the stream's taint set and ingested on the owner's
    # box -- a STREAM MUTATION, beside re-sharing, wall edits and pin
    # changes. Ruling C makes the OWNER able to consolidate a
    # recipient's thread; it does not make the recipient able to
    # consolidate anything.
    assert Document.objects.filter(origin=Document.Origin.NOTES).count() == 0


def test_the_consolidate_button_and_the_staleness_hints_do_not_render_for_a_recipient(client):
    """RULING F's render half. A staleness hint is a prompt to press
    Consolidate; rendering it to a recipient who would get a 404 would be
    the render-vs-gate pair broken on the most visible surface there
    is — and `staleness_for` is simply not CALLED for a non-owner, one
    predicate checked once for the page rather than once per row."""
    owner, reader = make_user(), make_user()
    stream = _workstream(**owner_fields(user_principal(owner)))
    Share.objects.create(target_type=Share.Target.WORKSTREAM,
                         target_key=str(stream.pk), user=reader,
                         level=Share.Level.USE)
    _conversation_with_turns(stream, count=2, principal=user_principal(owner))

    url = reverse("chat-workstream", args=[stream.pk])
    with posture("enterprise"):
        sign_in(client, owner)
        owners_view = client.get(url).content.decode()
        client.logout()
        sign_in(client, reader)
        recipients_view = client.get(url).content.decode()

    assert "Consolidate" in owners_view
    assert "Not consolidated" in owners_view
    # THE RENDER-VS-GATE PAIR: a control that 404s is worse than one that
    # is absent, and a staleness hint is a prompt to press a button the
    # recipient may not press.
    assert "Consolidate" not in recipients_view
    assert "Not consolidated" not in recipients_view
    assert "since last consolidated" not in recipients_view


def test_the_staleness_hint_reads_three_ways_and_appends_the_tag_note():
    """§19.2 done-when 8, plus §10.5's last paragraph: the hint appends
    "; 1 tag added since" when `ConversationTaint.at > consolidated_at`
    for any row, so the one case where re-consolidating changes ACCESS
    rather than content is visible on the page that offers the button.

    INDEX DIFFERENCE, NOT TURN COUNT (author decision 20): `_finish`
    leaves deliberate gaps in `Turn.index`, so the difference can exceed
    the number of turns actually added. It is a HINT, and an
    over-estimate of "how much has happened here" is the right direction
    for a hint to err in."""
    stream = _workstream()
    conversation = _conversation_with_turns(stream, count=2)

    assert staleness_for([conversation])[conversation.pk] == "Not consolidated"

    latest = Turn.objects.filter(conversation=conversation).order_by("-index").first()
    record_consolidation(conversation.pk, through_index=latest.index,
                         at=timezone.now())
    conversation.refresh_from_db()
    assert staleness_for([conversation])[conversation.pk] == "Up to date"

    # INDEX DIFFERENCE, NOT TURN COUNT (author decision 20): `_finish`
    # leaves deliberate gaps in `Turn.index`, so this can exceed the
    # number of turns actually added. It is a HINT, and an over-estimate
    # of "how much has happened here" is the right direction to err in.
    Turn.objects.create(conversation=conversation, index=latest.index + 3,
                        role=Turn.Role.USER, text="more", state=Turn.State.DONE)
    assert staleness_for([conversation])[conversation.pk] == "3 turns since last consolidated"

    # And the one case where re-consolidating changes ACCESS rather than
    # content: a tag acquired since the last consolidation leaves the
    # note UNDER-LABELLED relative to its stream.
    ConversationTaint.objects.create(conversation=conversation,
                                     entitlement=make_entitlement())
    assert staleness_for([conversation])[conversation.pk].endswith(
        "; a tag has been added since")
```

The module's own helpers, so every body above resolves:

```python
def _conversation_with_turns(stream, *, count, title="A thread", principal=None):
    """A stream conversation with `count` completed root-depth turns,
    numbered so a cap test can tell which N survived."""
    from django.apps import apps

    from identity.access import owner_fields
    from identity.contracts.principals import OPEN_PRINCIPAL

    Conversation = apps.get_model("agents", "Conversation")
    Turn = apps.get_model("agents", "Turn")
    conversation = Conversation.objects.create(
        agent=make_agent(), workstream=stream, title=title,
        **owner_fields(principal or OPEN_PRINCIPAL))
    Turn.objects.bulk_create([
        Turn(conversation=conversation, index=i, depth=0,
             role="user" if i % 2 == 0 else "assistant",
             text=f"turn-{i}", state="done")
        for i in range(count)
    ])
    return conversation


def _payload(conversation, *, actor=None):
    """The job payload `chat-workstream-consolidate` enqueues.

    The acting principal travels IN THE PAYLOAD, through
    `identity.contracts.principals.payload_fields`, because the worker is
    a separate process that shares no memory with the page that asked for
    the job -- the same way `agent.turn`'s payload already carries its
    actor.
    """
    from identity.contracts.principals import OPEN_PRINCIPAL, payload_fields

    return {"conversation": str(conversation.pk),
            "workstream": conversation.workstream_id,
            **payload_fields(actor or OPEN_PRINCIPAL)}


def _refs():
    """The two `ModelRef`s `plan_consolidate` returns -- the chat role
    for the distillation call and the embed role for the re-ingest."""
    from models.contracts.jobkinds import ModelRef
    from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

    return [_ref_for(CHAT_CONVERSE_ROLE), _ref_for(RAG_EMBED_ROLE)]


def _latest_job_id(kind: str, conversation) -> int:
    """The live queue job of `kind` for `conversation`, through the one
    sanctioned seam.

    `models.queue.visibility` is the ONLY submodule of `models.queue`
    another column may import (import-law rule 2), and Step 3 puts
    `live_job_for` there for exactly this question. `models.contracts.
    queue` cannot answer it: that leaf deliberately does not import
    `models.queue.models`, and its `get_job` needs an id the caller does
    not have.
    """
    from models.queue.visibility import live_job_for

    job_id = live_job_for(kind, conversation=str(conversation.pk))
    assert job_id is not None, f"no live {kind} job for {conversation.pk}"
    return job_id


def _run_on_commit_callbacks():
    """Fire the `transaction.on_commit` callbacks pytest-django's
    `django_db` block otherwise defers -- `on_terminal` is scheduled with
    `on_commit` at all three of its call sites, deliberately, so it never
    runs inside `claim_and_admit`'s advisory-lock window."""
    from django.db import connection

    for _sids, func, _kw in connection.run_on_commit:
        func()
    connection.run_on_commit = []


def _run_one_worker_tick():
    """One worker tick, the shape `tools/rag/tests/test_jobs.py::
    TestEndToEndViaWorker` really drives one (lines 460-466).

    `Worker` has `__init__` and `tick`; THERE IS NO `run_once`. And the
    tick only CLAIMS and LAUNCHES -- the handler runs on the pool, so a
    test that does not wait on the futures asserts against a job that has
    not run yet.
    """
    import concurrent.futures

    from models.queue.worker import Worker

    worker = Worker(worker_id="test-worker")
    try:
        worker.tick()
        concurrent.futures.wait(list(worker._futures.values()), timeout=5)
    finally:
        worker._executor.shutdown(wait=True)
```

**The two tests that drive a worker must also be `transaction=True`, and must register a fake
engine.** `TestEndToEndViaWorker`'s own docstring records why: the job runs on a pool thread with
its own DB session, which cannot see rows a `django_db` test has not committed. So those two live in
their own class:

```python
@pytest.mark.django_db(transaction=True)
class TestConsolidateViaWorker:
    """The two consolidation tests that go through the real queue.

    `transaction=True`, for the reason `tools/rag/tests/test_jobs.py::
    TestEndToEndViaWorker` records: the handler runs on a pool thread
    whose own DB session cannot see this test's uncommitted rows.

    `_FakeInferenceEngine` is registered so the worker's pre-run
    health check touches no network -- the same "mock at the adapter
    boundary, not deeper" convention that class already follows.
    """

    @pytest.fixture(autouse=True)
    def _register_fake_engine(self):
        from models.contracts.engines import ENGINES, register

        register(_FakeInferenceEngine())
        yield
        ENGINES.pop("test-inference", None)
```

with a `_FakeInferenceEngine` copied into this module — a standalone copy per test module, the
convention `test_jobs.py:379-397` documents — and both role bindings (`chat.converse` and
`rag.embed`) created against it, exactly as that class creates its two.

**Neither `make_agent` nor `_ref_for` exists** — `grep -rn "_ref_for\|make_agent" tools/rag/`
returns nothing, and `tools/rag/tests/_helpers.py` defines only `make_document`,
`model_available`, `make_job_ctx`, `make_tool_ctx`, `isolated_tool_registry`, `post_ask`,
`fake_whisper_get` and `fake_whisper_post`. Both are written in this module:

```python
def make_agent(**overrides):
    """A minimal `Agent` row, through `apps.get_model`, so this module
    imports nothing of `agents.models` -- the same rule
    `tools/rag/tests/_helpers.py::_workstream` follows and for the same
    reason."""
    from django.apps import apps

    model = apps.get_model("agents", "Agent")
    fields = {"slug": f"agent-{next(_names)}", "name": "Test agent",
              "system_prompt": "Be useful.", "tool_keys": []}
    fields.update(overrides)
    return model.objects.create(**fields)


def _ref_for(role: str):
    """One `ModelRef` for `role`, resolved the way `plan_consolidate`
    resolves it -- the `ANSWER_RESOLVED`/`EMBED_RESOLVED` convention
    `tools/rag/tests/test_jobs.py:96-98` already uses, keyed by role
    rather than hardcoded per test."""
    from models.contracts.bindings import resolve

    return jobs._ref(role, resolve(role))
```

with `import itertools` and `_names = itertools.count()` at module scope.

`_latest_job_id` reads through **`models.queue.visibility.live_job_for`** (Step 3), which is the
sanctioned seam — not through a `latest_job_id` in `models/contracts/queue.py`, which does not
exist and could not be added there without inverting that leaf's own no-`models.queue.models` rule.

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tools/rag/tests/test_distil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.rag.distil'`

- [ ] **Step 3: Give the queue seam an in-flight reader**

**M16 / author decision 15, corrected.** The decision as first written said the marker is "a live
queue row, read through `models.contracts.queue`'s **existing helpers**". That module's entire
public surface is `enqueue`, `get_job`, five state literals, `TERMINAL_STATES` and
`QueueUnavailable`, and its own docstring forbids the fix a reader would need: *"This module stays
a pure leaf: it does NOT import `models.queue.models`… (`models.queue` imports `models.contracts`,
never the other way)."* There is no way to ask "is there a live `rag.consolidate` job for
conversation X" without already holding a job id, and §5.4/§18 add no column to store one.

**The reader goes in `models/queue/visibility.py`, which is already the sanctioned seam for exactly
this.** `foundation/ops/tests/test_import_law.py::
test_other_columns_reach_the_queues_visibility_and_nothing_else_of_models_queue` makes `visibility`
the **one** submodule of `models.queue` that `tools/` and `agents/` may import — *"so a later view
cannot reach `models.queue.backend` or `models.queue.scheduler` for a visibility answer that already
has one home"* — and that module already imports `InferenceJob` and already filters on
`payload__<key>`. Adding the reader there costs one function in one module, keeps the contracts
leaf pure, and adds no new dispatch indirection.

Append to `models/queue/visibility.py`:

```python
def live_job_for(kind: str, **payload_match) -> int | None:
    """The id of a QUEUED-or-RUNNING job of `kind` whose payload matches
    every `payload_match` pair, or `None`.

    THE IN-FLIGHT QUESTION, asked from another column. `models.contracts.
    queue` cannot answer it: that module is a pure leaf that deliberately
    does not import `models.queue.models`, and `get_job` needs an id the
    asker does not have. This module already holds `InferenceJob` and
    already filters on `payload__<key>`, and import-law rule 2 already
    names it the one submodule of `models.queue` another column may
    reach -- so the answer has one home rather than a new seam.

    NOT A VISIBILITY ANSWER, and the difference is worth stating: every
    other function here narrows rows to a PRINCIPAL. This one takes no
    principal and answers a question about the QUEUE, for a caller that
    has already run its own permission check on the row the job is about
    (`tools.rag.views.workstream_consolidate` resolves the conversation
    through `visible_conversations` and the stream through
    `may_manage_workstream` before it asks). It lives here because this
    module is the sanctioned door into `models.queue`, not because it is
    about who may see what.

    ADVISORY, NOT A LOCK. A second submission can race between this read
    and its own enqueue; `uniq_notes_per_conversation` is the database's
    own answer to that, and this reader is what turns the common case
    into an honest 409 instead of a duplicate job.
    """
    rows = InferenceJob.objects.filter(kind=kind, state__in=(QUEUED, RUNNING))
    for key, value in payload_match.items():
        rows = rows.filter(**{f"payload__{key}": value})
    return rows.order_by("-id").values_list("id", flat=True).first()
```

with `QUEUED, RUNNING` added to that module's `from models.queue.models import ...` line.

Add the pin to `models/queue/tests/test_visibility.py`:

```python
def test_live_job_for_finds_a_queued_job_and_ignores_terminal_ones():
    from models.queue.models import CANCELLED, QUEUED, InferenceJob
    from models.queue.visibility import live_job_for

    InferenceJob.objects.create(kind="rag.consolidate", priority=200, state=CANCELLED,
                                payload={"conversation": "abc"})
    assert live_job_for("rag.consolidate", conversation="abc") is None

    live = InferenceJob.objects.create(kind="rag.consolidate", priority=200, state=QUEUED,
                                       payload={"conversation": "abc"})
    assert live_job_for("rag.consolidate", conversation="abc") == live.pk
    # Keyed on the payload, so another conversation's job is not this one.
    assert live_job_for("rag.consolidate", conversation="def") is None
    # And keyed on the kind, so an unrelated job of another kind is not.
    assert live_job_for("rag.ingest", conversation="abc") is None
```

Run: `pytest models/queue/tests/test_visibility.py foundation/ops/tests/test_import_law.py -v`
Expected: PASS — the import-law sweep is unchanged, because `tools/rag/views.py` reaches
`models.queue.visibility` and nothing else of `models.queue`.

- [ ] **Step 4: Add `NOTES_DIR`**

In `config/settings.py`, beside `DOCUMENTS_DIR` and `INGEST_INBOX_DIR`:

```python
# Consolidated workstream notes (spec §10.4). UNDER `DATA_DIR`,
# DELIBERATELY NOT UNDER `INGEST_INBOX_DIR`: the watcher polls the inbox,
# and a note written there would be staged TWICE -- once by the
# consolidation job and once by the watcher, as a UNIVERSAL document with
# a service principal for an actor. `stage_document` takes any path and
# `move=False` leaves the file in place, so a directory the watcher never
# looks at costs one constant and closes the whole question.
NOTES_DIR = DATA_DIR / "notes"
```

Name it in `docs/OPERATIONS.md` (Task 19) with the sentence "the watcher must never be pointed at
it".

- [ ] **Step 5: Write `tools/rag/distil.py`**

```python
"""One transcript in, one model call, one string out."""
from __future__ import annotations

from llama_index.core.llms import ChatMessage, MessageRole

# The distillation instruction sent with every consolidation -- PLATFORM
# BEHAVIOR, not a model default: this exact wording is what makes "a
# retrievable note, not a chat recap" true of every engine the chat role
# could ever be bound to, regardless of that model's own summarising
# manners. A module constant, not buried inline in `distil_conversation`
# below, so an operator auditing or overriding platform behavior has
# exactly one place to look -- and one place to change, a deliberate,
# NAMED deferral: making this operator-editable (a `RagSettings` field,
# say) is real future work this constant's existence flags, not a
# decision this task makes.
#
# DESIGNED FOR TWO CONSUMERS. Consolidation (spec §10) is the first.
# Conversation compaction (spec §22) is the second: it will reuse this
# same constant and this same one-call function to replace a live prompt
# history with its summary when a conversation approaches its bound
# model's context limit. The wording therefore describes the OUTPUT -- a
# self-contained, factual distillation that stands without the transcript
# -- and says nothing about where that output is going.
DISTILLATION_PROMPT = (
    "Distil the conversation below into a self-contained note that will be read on its own, "
    "without the conversation. Keep every decision, fact, figure, name and open question, and "
    "the reasoning that led to each. Drop pleasantries, restatements and turn-taking. Write it "
    "as prose under short headings, in the conversation's own terms. Do not add anything that "
    "was not said, and do not summarise away specifics."
)

# How many replayable root-depth turns one consolidation reads.
#
# NOT `agents.limits.HISTORY_TURNS = 20` -- that is a LIVE TURN'S PROMPT
# BUDGET, and distilling only the last twenty turns would silently make
# spec §10.4's "the conversation" false without saying so. Not uncapped
# either: the transcript goes into one model call, and an unbounded one
# would fail against the bound model's context window with no named
# reason. When the cap bites, the job distils the MOST RECENT 400 turns
# and the note's first line says so in one sentence -- an honest partial
# beats a failure and beats a silent truncation.
CONSOLIDATION_MAX_TURNS = 400


def distil_conversation(turns: tuple[dict, ...], *, llm=None) -> str:
    """`turns` -> one note, as a string.

    `turns` are the pure dicts `agents.workstreams.transcript_for`
    returns -- `({"role": str, "text": str}, ...)`, oldest first -- so
    this function never touches an `agents` row and this module imports
    nothing of that column.

    `llm` is the already-resolved chat model the planner admitted; the
    caller resolves it, because the job's planner is what declared the
    role to the queue. `None` raises rather than resolving one here: this
    module is ONE CALL, and a function that could quietly resolve its own
    model would be a second binding path beside the planner's.

    ONE MESSAGE, NOT A SYSTEM/USER PAIR. The prompt and the transcript go
    into one user message, the shape `tools/rag/extract.py` already uses
    with `EXTRACTION_PROMPT`: a second system message behaves differently
    on different engines, and this call must produce the same note on
    every engine the chat role could be bound to.

    THE ROLES ARE LABELLED IN THE TRANSCRIPT ITSELF, in plain prose, so
    the distillation sees who said what without depending on an engine
    honouring a multi-turn history it was not given.
    """
    if llm is None:
        raise ValueError(
            "distil_conversation needs the resolved chat model its job's planner "
            "admitted; it does not resolve one of its own.")
    body = "\n\n".join(f"{turn['role']}: {turn['text']}"
                        for turn in turns if (turn.get("text") or "").strip())
    message = ChatMessage(role=MessageRole.USER,
                          content=f"{DISTILLATION_PROMPT}\n\n---\n\n{body}")
    return str(llm.chat([message]).message.content or "").strip()
```

`ChatMessage`/`MessageRole` are imported at the top of the module (above) — the same two names
`agents/runtime/prompt.py` already builds its messages from, and this module's only import besides
`__future__`.

- [ ] **Step 6: Add the note column and migration 4**

In `tools/rag/models.py`, in `class Document` after `origin`:

```python
    # NEW. The conversation a `notes` document was distilled from -- the
    # key that makes re-consolidation an OVERWRITE rather than a second
    # note (spec §10.4). A UUID BY VALUE, never a FK: `tools/rag` may not
    # import `agents.models`, and this is the same by-value reference
    # `Turn.queue_job_id` already is in the other direction.
    notes_conversation_id = models.UUIDField(null=True, blank=True)
```

and in the `Meta` Task 5 created:

```python
        constraints = [
            # ONE NOTE PER CONVERSATION (owner decision 6), enforced by
            # the DATABASE and not only by the job that writes it: a
            # re-consolidation racing itself would otherwise produce two
            # notes and the stream page would show both.
            models.UniqueConstraint(fields=["notes_conversation_id"],
                                    condition=models.Q(notes_conversation_id__isnull=False),
                                    name="uniq_notes_per_conversation"),
        ]
```

`models.Q`, not a bare `Q` — this module imports neither `Q` nor anything else from
`django.db.models` directly (reconciliation R4).

Run: `python manage.py makemigrations rag --name document_notes`
Expected: `tools/rag/migrations/0017_document_notes.py`, depending on
`("rag", "0016_document_workstream_and_pins")`, with `AddField(document.notes_conversation_id)`
and `AddConstraint(uniq_notes_per_conversation)` onto the `Meta` migration 2 created.

Run: `python manage.py makemigrations --check --dry-run` → exit 0.

- [ ] **Step 7: Add the four agents-side seam functions**

Append to `agents/workstreams.py`:

```python
def transcript_for(principal, conversation_id, *, limit: int):
    """The most recent `limit` replayable root-depth turns of a stream
    conversation, as pure dicts -- `({"role": str, "text": str}, ...)`,
    oldest first -- for the consolidation job, or `None` when the
    principal may not read it.

    NOT `agents.runtime.prompt.history_messages`, AND `limit` IS WHY.
    That helper caps at `agents.limits.HISTORY_TURNS = 20`, which is a
    PROMPT budget for a live turn; consolidating only the last twenty
    turns would make spec §10.4's "the conversation" false without saying
    so. This is its own query, with its own cap, NAMED BY ITS OWN CALLER
    -- `limit` is required and keyword-only, and a non-positive one is
    refused rather than defaulted (author decision 16), so the honesty
    sentence the job writes always has a number behind it.

    PURE DICTS, not rows: the caller is `tools/rag` code running in the
    worker process, and handing it `Turn` instances would make a
    `tools/rag` module hold `agents` ORM objects.
    """
    if limit <= 0:
        raise ValueError("transcript_for needs a positive limit")
    from agents.models import Turn
    from agents.visibility import visible_conversations

    conversation = visible_conversations(principal).filter(pk=conversation_id).first()
    if conversation is None:
        return None
    rows = (Turn.objects.filter(conversation=conversation, state=Turn.State.DONE, depth=0)
            .order_by("-index")[:limit])
    return tuple({"role": t.role, "text": t.text} for t in reversed(list(rows)))


def record_consolidation(conversation_id, *, through_index: int, at) -> None:
    """Stamp the conversation's consolidation bookkeeping (spec §10.5).

    ONE conditional `UPDATE`, never a read-then-save: the job runs in the
    worker process and the page may have written the row since.
    """
    from agents.models import Conversation

    Conversation.objects.filter(pk=conversation_id).update(
        consolidated_through_index=through_index, consolidated_at=at)


def taint_ids_for_conversation(conversation_id) -> frozenset[int]:
    """The conversation's taint tags, for the note document to inherit
    (spec §10.4 step 5). Owner decision 6: no laundering."""
    from agents.models import ConversationTaint

    return frozenset(
        ConversationTaint.objects.filter(conversation_id=conversation_id)
        .values_list("entitlement_id", flat=True))


def staleness_for(conversations) -> dict:
    """`{conversation pk: sentence}` -- the per-conversation staleness
    hint (spec §10.5).

    OWNER-ONLY, and the CALLER checks that (ruling F): a staleness hint
    is a prompt to press Consolidate, and rendering it to a recipient who
    would get a 404 would be the render-vs-gate pair broken on the
    surface it is most visible on. One predicate, checked ONCE for the
    page rather than once per row.

    INDEX DIFFERENCE, NOT TURN COUNT (author decision 20). `_finish`
    leaves deliberate gaps in `Turn.index`, so
    `latest - consolidated_through` can exceed the number of turns
    actually added. It is a HINT -- the owner's own word -- and an
    over-estimate of "how much has happened here" is the right direction
    for a hint to err in. The alternative is a `COUNT(*)` per
    conversation per page render.

    ONE AGGREGATE QUERY for the latest index per conversation, and one
    more for the tag note, over rows the page already loaded.
    """
    from django.db.models import Max

    from agents.models import ConversationTaint, Turn

    keys = [c.pk for c in conversations]
    latest = dict(Turn.objects.filter(conversation_id__in=keys)
                  .values_list("conversation_id").annotate(Max("index")))
    tagged_since = {
        row["conversation_id"]
        for row in ConversationTaint.objects.filter(conversation_id__in=keys)
        .values("conversation_id", "at")
        for c in conversations
        if c.pk == row["conversation_id"] and c.consolidated_at
        and row["at"] > c.consolidated_at
    }
    out = {}
    for c in conversations:
        if c.consolidated_through_index is None:
            out[c.pk] = "Not consolidated"
            continue
        gap = (latest.get(c.pk) or 0) - c.consolidated_through_index
        sentence = "Up to date" if gap <= 0 else (
            f"{gap} turn{'' if gap == 1 else 's'} since last consolidated")
        if c.pk in tagged_since:
            # THE ONE CASE WHERE RE-CONSOLIDATING CHANGES *ACCESS* RATHER
            # THAN CONTENT (spec §10.5). A note's labels are recomputed
            # only on re-consolidation, so a conversation that acquired a
            # tag since its last one has a note that is UNDER-LABELLED
            # relative to its stream. That is safe -- the newer material
            # is not in the note -- but nothing on a turn-counting hint
            # would tell an owner it had happened.
            sentence += "; a tag has been added since"
        out[c.pk] = sentence
    return out
```

- [ ] **Step 8: Write the four job functions and register the kind**

Append to `tools/rag/jobs.py`. **`distil_conversation` is imported at MODULE scope**, beside
`answer_question` (`tools/rag/jobs.py:85`) and for the same reason — which is also what fixes the
patch target for every consolidation test: `"tools.rag.jobs.distil_conversation"`, exactly as
`test_jobs.py:430` already patches `"tools.rag.jobs.answer_question"` (m7).

```python
from tools.rag.distil import CONSOLIDATION_MAX_TURNS, DISTILLATION_PROMPT, distil_conversation
```

```python
def plan_consolidate(payload: dict) -> tuple[list[ModelRef], bool]:
    """The models a `rag.consolidate` job needs, at ENQUEUE time.

    TWO ROLES, so the job that distils is the job that ingests and the
    operator watches ONE queue row rather than two: `CHAT_CONVERSE_ROLE`
    for the distillation call and `RAG_EMBED_ROLE` for the re-ingest of
    the note. `plan_ingest` already returns more than one ref, so this is
    the established shape rather than a new one.

    `chat.converse`, NOT A NEW ROLE (author decision 24). Owner decision 6
    asks for a documented prompt constant, not a binding an operator must
    configure before the feature works: a box that can hold a
    conversation can distil one. A dedicated summariser role is a
    `RoleSpec` registration and a console binding away if a deployment
    ever wants one (spec §22).

    EXCLUSIVE, as every model-consuming kind is: the distillation call
    and the embed pass both want the machine.
    """
    return (
        [_ref(CHAT_CONVERSE_ROLE, resolve(CHAT_CONVERSE_ROLE)),
         _ref(RAG_EMBED_ROLE, resolve(RAG_EMBED_ROLE))],
        True,
    )


def run_consolidate(payload: dict, models: list[ModelRef], ctx: JobContext) -> dict:
    """Distil one stream conversation into its contained note document.

    SIX STEPS, IN SPEC §10.4'S ORDER, and the order is the design: the
    model call precedes the destroy, so a distillation failure destroys
    nothing (spec §24 concern 5).

    EXEMPT FROM THE LABELLING-AUTHORITY RULE, AND THE EXEMPTION IS THE
    POINT (author decision 27). `tools.rag.labels.set_document_labels`'
    own docstring says *"THE CALLER CHECKS THE PREDICATE"* --
    `may_label_document` plus the view's "owner of every entitlement
    being added or removed" rule. THIS CALLER CHECKS NEITHER. It applies
    `taint_ids_for_conversation(cid)`, which under ruling B can contain
    entitlements the actor neither owns nor holds, and the
    `DOCUMENT_LABELLED` rows are written in the actor's name. The
    authority rule governs a person CHOOSING labels for a document;
    nobody is choosing here -- the labels are copied from what the
    material already carried, and refusing to copy one because the actor
    does not own it is precisely the laundering owner decision 6 forbids.
    Named here so a reader working through `set_document_labels`' callers
    can see why one of them does not check.

    THE NOTE'S FIRST LINE IS THIS FUNCTION'S OWN PROSE, not the model's
    (author decision 17), so a model that ignored an instruction cannot
    make the note lie about its own provenance or about the cap.
    """
    from agents.workstreams import (
        record_consolidation, taint_ids_for_conversation, transcript_for,
    )
    from identity.contracts.principals import principal_from_payload
    from models.contracts.gateway import get_llm_for
    from tools.rag.labels import set_document_labels

    actor = principal_from_payload(payload)
    conversation_id = payload["conversation"]

    # 1. THE TRANSCRIPT, through the agents seam. `None` fails the job
    #    with a named error rather than distilling something the actor
    #    may not read.
    turns = transcript_for(actor, conversation_id, limit=CONSOLIDATION_MAX_TURNS)
    if turns is None:
        raise RuntimeError(
            f"Conversation {conversation_id} is not readable by the principal this "
            f"job runs as, so there is nothing to consolidate.")
    if not turns:
        raise RuntimeError(
            f"Conversation {conversation_id} has no completed turns to distil.")

    # 2. ONE MODEL CALL. Before step 3, deliberately.
    chat_ref = next(m for m in models if m.role == CHAT_CONVERSE_ROLE)
    note_body = distil_conversation(turns, llm=get_llm_for(chat_ref.resolved))

    # 3. A DETERMINISTIC PATH under `NOTES_DIR`, never under
    #    `INGEST_INBOX_DIR` -- the watcher polls the inbox, and a note
    #    written there would be staged twice, once by this job and once
    #    by the watcher as a UNIVERSAL document with a service principal
    #    for an actor (author decision 19).
    #
    #    Deterministic because `stage_document` dedups on
    #    `original_path`: the second consolidation writes the same path,
    #    finds the existing row, sees a changed `file_hash`, calls
    #    `_delete_existing_data`, and re-ingests IN PLACE -- same row,
    #    same id, same store directory, and every old `document:<id>`
    #    reference still resolves.
    settings.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.NOTES_DIR / f"{conversation_id}.md"
    path.write_text(_note_text(payload, turns, note_body), encoding="utf-8")

    # `move=False` leaves the file where this job wrote it; the managed
    # store gets its own copy, exactly as every other ingest does.
    doc, _changed = ingest.stage_document(str(path), None, move=False,
                                          workstream_id=payload["workstream"])

    # 4. THE NOTE'S OWN FIELDS, in one transaction. AFTER staging, because
    #    `stage_document`'s re-stage branch overwrites `title` with the
    #    file's name -- so a title set before it would be lost on every
    #    re-consolidation.
    with transaction.atomic():
        doc.title = _note_title(payload["title"])
        doc.origin = Document.Origin.NOTES
        doc.notes_conversation_id = conversation_id
        doc.extraction = {
            "method": "distillation",
            "engine": chat_ref.engine,
            "model_id": chat_ref.model_id,
            "connection_name": chat_ref.connection_name,
            "produced_at": timezone.now().isoformat(),
        }
        doc.save(update_fields=["title", "origin", "notes_conversation_id",
                                "extraction", "updated_at"])

    # 5. INHERIT THE TAINT. Owner decision 6: no laundering. A WIDENING
    #    ONLY -- the tags are additive, so the note's labels only ever
    #    grow, which is the property this step exists for: a note can
    #    never become MORE readable than the conversation it came from.
    set_document_labels(actor, doc, taint_ids_for_conversation(conversation_id))

    # 6. RE-INGEST IN PROCESS, using the embed model the planner admitted.
    #    THE DESTROY-THEN-RECREATE WINDOW IS HERE (spec §24 concern 5):
    #    step 3 already ran `_delete_existing_data` on a re-stage, so a
    #    failure now leaves the row at FAILED with no chunks and no
    #    stored file. `run_ingest_or_fail` writes that status and
    #    re-raises; the stream page renders the chip and offers
    #    Re-consolidate, which is the whole repair path.
    ingest.run_ingest_or_fail(doc, doc.file_hash, ctx)

    record_consolidation(conversation_id,
                         through_index=payload.get("through_index"),
                         at=timezone.now())
    audit.record(actor, actions.WORKSTREAM_CONSOLIDATED, target_type="workstream",
                 target_key=payload["workstream"], target_label=payload.get("stream_name", ""),
                 conversation=str(conversation_id), document=doc.id,
                 turns=len(turns), capped=len(turns) >= CONSOLIDATION_MAX_TURNS)
    return {"document_id": doc.id, "turns": len(turns)}


# `title` is a 512-character column here, and `agents.chat.service.
# truncate_title` is `agents` code this column may not import -- so the
# note title is truncated locally at the column's own width, with the
# same ellipsis convention.
_TITLE_CAP = 512


def _note_title(conversation_title: str) -> str:
    """`"Notes — <title>"`, with an EM DASH, regenerated on every
    consolidation so a renamed conversation gets a renamed note."""
    full = f"Notes — {conversation_title or 'Untitled'}"
    return full if len(full) <= _TITLE_CAP else full[:_TITLE_CAP - 1] + "…"


def _note_text(payload: dict, turns: tuple[dict, ...], body: str) -> str:
    """The note file: this function's own provenance line, then the
    model's distillation.

    THE FIRST LINE IS THE JOB'S PROSE, NOT THE MODEL'S (author decision
    17). Spec §10.4 asks for "a first line naming the source conversation
    and the date", and the cap's honesty sentence -- "an honest partial
    beats a failure and beats a silent truncation" -- has to be true even
    when the model ignored every instruction it was given.
    """
    header = (f"Notes from conversation {payload['conversation']} "
              f"({timezone.now():%Y-%m-%d}).")
    if len(turns) >= CONSOLIDATION_MAX_TURNS:
        header += (f" This note covers the most recent {CONSOLIDATION_MAX_TURNS} turns of a "
                   f"longer conversation.")
    return f"{header}\n\n{body}\n"


def summarize_consolidate(payload: dict) -> str:
    """The queue listing's one-line row summary, truncated the way
    `summarize_ask` and `summarize_ingest` truncate theirs."""
    return Truncator(f"Notes — {payload.get('title', '')}").chars(120)


def on_consolidate_terminal(payload: dict, state: str) -> None:  # noqa: ARG001 - see docstring
    """`rag.consolidate`'s `on_terminal` hook -- the stranded-note fix.

    THE SAME SHAPE AND THE SAME REASON AS `on_ingest_terminal`, narrowed
    to the one row this kind can strand. A job cancelled while still
    queued, permanently failed by a second orphan sweep, or whose handler
    never started leaves NOTHING behind on a FIRST consolidation -- the
    note row does not exist until step 3 runs -- but on a
    RE-consolidation it can leave the PREVIOUS note at `PENDING`, because
    `stage_document` already destroyed its chunks and its stored file and
    set that status inside its own transaction before this job died.
    Without this hook that row is stuck at PENDING forever, and
    `tools.rag.views.document_reingest` refuses to re-queue a
    PENDING/PROCESSING row ("already being processed") -- so the note
    would be unrepairable from either the stream page or the library.

    ONE CONDITIONAL `UPDATE`, never a read-then-save, for exactly the
    reason `on_ingest_terminal`'s own docstring gives at length: this
    runs AFTER the job row's terminal write commits, so a still-alive
    "stale" worker can finish and write READY between a read and a write,
    which a plain `save()` would silently clobber back to FAILED. The
    `status__in` guard makes that impossible.

    `state` is unused: a cancelled job and a twice-orphaned one strand
    the row identically, and the operator-facing words come from
    `status_detail`, not from which of the two happened.
    """
    conversation_id = (payload or {}).get("conversation")
    if not conversation_id:
        logger.warning("rag.consolidate: terminal hook with no conversation in its payload")
        return
    updated = Document.objects.filter(
        notes_conversation_id=conversation_id,
        status__in=(Document.Status.PENDING, Document.Status.PROCESSING),
    ).update(
        status=Document.Status.FAILED,
        status_detail="The consolidation job ended before it could rebuild this note. "
                      "Re-consolidate from the workstream page.",
        updated_at=timezone.now(),
    )
    if not updated:
        logger.debug("rag.consolidate: nothing to clear for conversation %s "
                     "(no note yet, or it already reached a terminal status)",
                     conversation_id)
```

`CHAT_CONVERSE_ROLE` joins this module's `from models.contracts.roles import ...` line; `audit`,
`actions`, `settings` and `transaction` join its imports (`from identity import audit`,
`from identity.contracts import actions`, `from django.conf import settings`,
`from django.db import transaction`).

Add `WORKSTREAM_CONSOLIDATED = "workstream.consolidated"` to `identity/contracts/actions.py` and to
`AUDIT_ACTIONS` — the seventeenth.

In `tools/rag/apps.py::RagConfig.ready()`:

```python
        # `rag.consolidate` (spec §10.2). REGISTERED BY `tools/rag`, NOT
        # BY `agents` (author decision 18), and that is the placement
        # decision this job kind turns on: the handler needs the
        # transcript (an `agents` row), a model call, and a document
        # write plus a re-ingest. Only this column can reach both ends --
        # through `agents.workstreams`, the permitted direction -- while
        # `agents` may not reach documents at all.
        #
        # `on_terminal` for the same reason `rag.ingest`'s exists: the
        # action writes a durable row before the handler runs, and a job
        # cancelled while queued, or permanently orphaned, must clear it.
        register_job_kind(
            JobKind(
                key="rag.consolidate",
                label="Consolidate into stream notes",
                planner="tools.rag.jobs.plan_consolidate",
                handler="tools.rag.jobs.run_consolidate",
                summarizer="tools.rag.jobs.summarize_consolidate",
                default_priority=200,
                on_terminal="tools.rag.jobs.on_consolidate_terminal",
            )
        )
```

`plan_consolidate` requests **two** roles — the chat role for the distillation call and
`RAG_EMBED_ROLE` for the re-ingest — so the job that distils is the job that ingests and the
operator watches one queue row rather than two. `plan_ingest` already returns more than one ref,
so this is the established shape rather than a new one. **No new role is registered**
(author decision 24): a box that can hold a conversation can distil one.

- [ ] **Step 9: Add the route and the page controls**

The view lives in `agents/chat/views/workstreams.py` beside the other six, because it is a
`/chat/w/` route and its predicate is `may_manage_workstream` — but it must **enqueue** a
`tools/rag` job kind, which `agents/` may not import. It does so through the queue seam, which
takes the kind as a **string**: `models.contracts.queue.enqueue("rag.consolidate", payload)`
validates the kind against the registry and dispatches, with no `tools.` name anywhere in
`agents/`.

```python
@require_POST
def workstream_consolidate(request, pk):
    """POST `/chat/w/<pk>/consolidate/` -- distil one of this stream's
    conversations into its note.

    OWNER-ONLY, INCLUDING FOR A RECIPIENT'S OWN THREAD (ruling F, spec
    §23.F). Consolidation writes a stream-contained `Document`, labelled
    from the stream's taint set and ingested on the owner's box -- a
    STREAM MUTATION, and it belongs beside re-sharing, wall edits and pin
    changes. Ruling C is what makes the OWNER able to consolidate a
    recipient's thread; it does not make the recipient able to
    consolidate anything.

    FOUR REFUSALS, and each has its own status because they are four
    different facts:
      - the conversation is not in this stream, or the caller may not
        read it -> 404 (the row-addressed rule; `visible_conversations`
        already carries ruling C's owner clause, so the owner reaches a
        recipient's thread here);
      - the caller may not mutate this stream -> 404, not 403, for the
        same reason every other row-addressed mutation is;
      - a consolidation for that conversation is already queued or
        running -> 409, the shape `agents.chat.service.start_turn`'s own
        in-flight refusal already uses;
      - the conversation has no completed turns to distil -> 400, named.

    THE JOB KIND IS A STRING (`"rag.consolidate"`), never an import:
    `agents/` may not import `tools/` at all, and
    `models.contracts.queue.enqueue` validates the kind against the
    registry before dispatching -- the same dotted-string crossing every
    other cross-column reach in this phase uses.
    """
    principal, stream = _resolve(request, pk)
    if not may_manage_workstream(principal, stream):
        raise Http404("No such workstream.")

    raw = request.POST.get("conversation", "")
    conversation = None
    if raw:
        conversation = (visible_conversations(principal)
                        .filter(pk=raw, workstream_id=stream.pk).first())
    if conversation is None:
        raise Http404("No such conversation in this workstream.")

    if live_job_for("rag.consolidate", conversation=str(conversation.pk)) is not None:
        return HttpResponse(
            "This conversation is already being consolidated. Wait for that job to "
            "finish, or cancel it on the queue page.", status=409,
            content_type="text/plain; charset=utf-8")

    latest = Turn.objects.filter(conversation=conversation,
                                 state=Turn.State.DONE, depth=0).order_by("-index").first()
    if latest is None:
        return HttpResponseBadRequest(
            "This conversation has no completed turns to distil yet.")

    enqueue("rag.consolidate", {
        "conversation": str(conversation.pk),
        "workstream": stream.pk,
        "stream_name": stream.name,
        "title": conversation.title,
        # THE INDEX THIS RUN CONSOLIDATES THROUGH, resolved HERE rather
        # than in the handler: the staleness hint compares against what
        # the job actually read, and a handler that re-read it would
        # record an index later than the transcript it distilled.
        "through_index": latest.index,
        **payload_fields(principal),
    })
    return redirect("chat-workstream", pk=stream.pk)
```

with, at that module's imports:

```python
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from agents.models import Turn
from identity.contracts.principals import payload_fields
from models.contracts.queue import enqueue
from models.queue.visibility import live_job_for
```

`models.queue.visibility` is the one submodule of `models.queue` another column may import, and
`models.contracts.queue` is the enqueue seam — both are already-sanctioned crossings, so
`foundation/ops/tests/test_import_law.py` stays green with no edit.

**`Turn.objects` in a module under `agents/chat` is a column-boundary violation** —
`foundation/ops/tests/test_column_boundaries.py`'s gate covers `Conversation`, `Agent`, `Flow`,
`Share` and (since Task 2) `Workstream`, but not `Turn`, so the letter of the gate permits it and
its spirit does not. Add `latest_completed_turn_index(conversation) -> int | None` to
`agents/visibility.py` beside `visible_turn` — which is already that module's `Turn` reader — and
call that instead:

```python
def latest_completed_turn_index(conversation) -> int | None:
    """The highest `Turn.index` this conversation has actually completed
    at root depth, or `None` when it has none.

    HERE, beside `visible_turn`, because this module is where `agents/chat`
    reaches `Turn` rows -- the same ruling `resident_agent_tool_keys`
    records for `Agent`: it is not a visibility question, but it is a
    `Turn` question, and the guard is flat.

    ROOT DEPTH AND `DONE` ONLY, matching `agents.runtime.prompt`'s own
    `_REPLAYABLE_STATES`/`_ROOT_DEPTH` pair, so the index this stamps is
    the index a transcript would actually have read.
    """
    return (Turn.objects.filter(conversation=conversation, state=Turn.State.DONE, depth=0)
            .order_by("-index").values_list("index", flat=True).first())
```

The stream page's Conversations section gains the per-conversation staleness hint and the
**Consolidate** button, both `{% if is_owner %}`, and the Documents panel's Contained group gains
the **Re-consolidate** button on a `FAILED` note — the whole repair path for §10.4's
destroy-then-recreate window.

`agents/chat/urls.py`: `path("w/<int:pk>/consolidate/", workstream_consolidate,
name="chat-workstream-consolidate")`.
`identity/routes.py`: `"chat-workstream-consolidate": "O",`.

The stream page's Conversations section gains the per-conversation staleness hint and the
**Consolidate** button, both `{% if is_owner %}`, and the Documents panel's Contained group gains
the **Re-consolidate** button on a `FAILED` note — the whole repair path for §10.4's
destroy-then-recreate window.

`identity/routes.py`: `"chat-workstream-consolidate": "O",`.

- [ ] **Step 10: Run everything**

Run: `pytest tools/rag/tests/test_distil.py tools/rag/tests/test_consolidate.py -v`
Expected: PASS

Run: `pytest -q`, then both posture sweeps.
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add tools/rag/distil.py tools/rag/jobs.py tools/rag/apps.py tools/rag/models.py \
        tools/rag/migrations/0017_document_notes.py config/settings.py \
        models/queue/visibility.py models/queue/tests/test_visibility.py \
        agents/workstreams.py agents/visibility.py \
        agents/chat/views/workstreams.py agents/chat/urls.py \
        identity/routes.py identity/contracts/actions.py \
        agents/chat/templates/chat/workstream.html \
        agents/chat/templates/chat/_workstream_panel.html \
        tools/rag/tests/test_distil.py tools/rag/tests/test_consolidate.py \
        tools/rag/README.md
git commit -m "feat(rag): consolidation into stream notes, with the repair path on the stream page"
```

---

### Task 19: the full matrix, the posture sweeps, the documentation, and the ladder

**Files:**
- Modify: `identity/tests/test_route_matrix.py` (the seven names, three postures, four principals,
  both `admin_sees_content` settings, the 403/404 pair, the anonymous-POST rows)
- Create/modify: the seven cross-cutting documents §20 names
- Test: `foundation/ops/tests/test_postures.py` (or the existing posture sweep), extended

- [ ] **Step 1: Complete the route matrix**

`identity/tests/test_route_matrix.py` gains the seven new names, asserted in **all three postures**
against **all four principals** and **both settings of `admin_sees_content`** — the existing sweep,
extended, plus two rows it has never had before:

```python
def test_chat_workstream_answers_403_for_a_live_share_holder_and_404_without_the_row(client):
    """The pair, asserted TOGETHER, because the exception is only safe if
    the negative case holds. This is the only 403 on a row-addressed URL
    on this platform and spec §12.3 fences it three ways."""
    owner, reader = make_user(), make_user()
    ent = make_entitlement(name="Finance")
    stream = _workstream(**owner_fields(user_principal(owner)))
    WorkstreamTaint.objects.create(workstream=stream, entitlement=ent)
    url = reverse("chat-workstream", args=[stream.pk])

    for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
        share = Share.objects.create(
            target_type=Share.Target.WORKSTREAM, target_key=str(stream.pk),
            user=reader, level=Share.Level.USE)
        with posture(name):
            sign_in(client, reader)
            assert client.get(url).status_code == 403, name
            share.delete()
            # THE PAIR: without the row, `visible_workstreams` excludes
            # the stream and the view raises `Http404` BEFORE
            # `stream_access` is called -- so no input a stranger can
            # supply reaches the 403 branch.
            assert client.get(url).status_code == 404, name


@pytest.mark.parametrize("url_name", [
    "chat-workstreams", "chat-workstream-edit", "chat-workstream-scope",
    "chat-workstream-share", "chat-workstream-consolidate", "rag-workstream-pin",
])
def test_an_anonymous_post_to_each_new_route_is_refused(client_with_csrf, url_name):
    """The anti-vacuous pin the matrix already uses, through a client
    built with `enforce_csrf_checks=True`."""
    args = [] if url_name == "chat-workstreams" else [1]
    with posture("enterprise"):
        response = client_with_csrf.post(reverse(url_name, args=args), {})
    # Redirected to sign-in, or refused outright -- never a write, and
    # never a 500.
    assert response.status_code in (302, 403, 404)
    assert "Traceback" not in response.content.decode(errors="ignore")
```

- [ ] **Step 2: Assert §13's posture table rather than describing it**

For each mechanism in §13's table, one test that it is **live in every posture**, or one that it is
**inert-and-consistent in `open`**. The wall, taint and sharing are the three inert ones; streams,
containment, pinning, upload placement, consolidation and notes are live in all three.

- [ ] **Step 3: Write the seven cross-cutting documents**

| Document | Change |
|---|---|
| `docs/ROADMAP.md` | a Workstreams entry under Phase 1.6, marked shipped in two halves, with §22's deferred list named as such rather than left implicit |
| `docs/adr/0016-identity-and-entitlements.md` | an amendment: the 404 house rule now has **one scoped exception** and where it is fenced (§12.3); `Share` has a fifth target |
| `docs/adr/0015-agent-layer-and-tool-contract.md` | an amendment: `ToolContext.stream`, and that a delegate inherits it |
| `identity/README.md` | the two new `identity.access` functions, the **seventeen** new audit actions, the note that a workstream is **not** an identity concept even though it carries entitlement sets, and the one route that now names an entitlement to a non-admin |
| `models/README.md` | `model_access_for`'s new `wall` parameter — the one change this phase makes in that column, and why a reader finds a workstream concept in it at all |
| `docs/OPERATIONS.md` | backups now contain contained documents that no non-admin library page lists; the new `DATA_DIR/notes/` directory and **why the watcher must never be pointed at it**; and the fact that deleting an entitlement un-taints |
| `docs/EXTENDING.md` | how a column registers a workstream panel, and how a labelled artifact kind joins the taint stamp |

`agents/README.md` and `tools/rag/README.md` are already current: each was edited by the task that
built the mechanism it describes (author decision 21). Re-read both here and fix anything the
later tasks made stale.

- [ ] **Step 4: Run the whole gate**

```bash
pytest -q                                          # farabunker_impl
FARABUNKER_TEST_POSTURE=personal pytest -q
FARABUNKER_TEST_POSTURE=enterprise pytest -q
python manage.py makemigrations --check --dry-run  # exit 0
python manage.py migrate --plan                    # against a restored production backup:
                                                   # exactly the four migrations, no others
```

Exercise the `PROTECT` behaviour of migrations 1 and 2's two FKs in **both** directions: a stream
with a conversation refuses, an emptied one deletes. (Tasks 2 and 5 already assert this; run it
here against the restored backup, which is a different thing from asserting it against a fresh
test database.)

- [ ] **Step 5: Walk §19.2's thirteen done-when criteria in a browser**

Criteria 1, 2, 2b, 3, 3b, 3b2, 3c, 4, 5, 6, 7, 7b, 8, 9, 10, 11 and 12, by hand, on the branch's
own preview stack. Criterion 13 is the ladder itself.

- [ ] **Step 6: The ladder to fresh pixels**

Use the `verify-deployed-work` skill. **No success language before fresh pixels on the owner's live
system.** ADR 0017 is written *after* both halves merge and is not part of this plan; name it in
the handoff as the next piece of work.

- [ ] **Step 7: Commit**

```bash
git add identity/tests/test_route_matrix.py docs/ROADMAP.md docs/OPERATIONS.md \
        docs/EXTENDING.md docs/adr/0015-agent-layer-and-tool-contract.md \
        docs/adr/0016-identity-and-entitlements.md identity/README.md models/README.md \
        agents/README.md tools/rag/README.md
git commit -m "docs(workstreams): the route matrix, the posture sweep, and the phase's documentation"
```

---

## Self-Review

Run against the spec with fresh eyes, per the writing-plans skill, and re-run after the round-1
review's twenty Majors landed.

**1. Spec coverage.** Every section has a task:
§4.2/§4.3 → Tasks 1, 4, 7. §5.1/§5.2 → Task 2. §5.3 → Task 16. §5.4 → Tasks 2, 14, 16.
§5.5/§5.6 → Task 5. §5.7 → Task 2. §6.1 → Tasks 7, 9. §6.2 → Task 9. §6.3 → Task 10.
§6.4 → Tasks 3, 14, 17. §6.5 → Task 12. §7 → Task 16. §8.1 → Task 6. §8.2 → Task 7.
§8.3 → Task 8. §8.4 → Tasks 4, 16. §9 → Task 11. §10 → Task 18. §11 → Task 12.
§12 → Task 17. §13 → Tasks 4, 10, 19. §14 → Tasks 13, 15, 17, 18. §15 → Tasks 13, 14, 15.
§16 → Tasks 3, 7, 8, 16, 18. §17 → every task's own test step, plus Task 19.
§18 → Tasks 2, 5, 16, 18. §19.1 → Task 15's browser walk. §19.2 → Task 19. §20 → Task 19.
**Gap found and closed:** `entitlement_names` is spec §12.1's (WS-2), but §6.1's wall editor is
its **first** caller and that is WS-1 — so Task 13 adds it, and Task 17 uses it rather than
creating it.

**2. Placeholder scan.** No `TBD`, no `TODO`, no "similar to Task N", no "add appropriate error
handling", and no step that describes what to do without showing how. Every test body and every
production function named by a task is written out — including, after round 1, `distil_conversation`'s
body, all four `rag.consolidate` job functions, `workstream_consolidate`, `_share_subject`,
`_reverse_for_sweep`, `live_job_for`, `make_agent`, `_ref_for`, `_chunk_updated_marker`,
`_rows_touched_by_last_restamp` and the picker test's model-set builder. The only remaining `...` in
the document are **four elisions inside production-code excerpts** — Task 10's `plan_turn`,
`_run_turn` and `preflight_turn` snippets, where they stand for existing lines the task does not
change.

**3. Type consistency.** `WorkstreamScope`'s five fields are spelled identically everywhere.
`readable_documents(principal, *, workstream_id=None)` likewise. `WorkstreamPanel` is four fields —
`key`, `label`, `provider`, `template` — in its dataclass, its `__post_init__`, both registrations,
`panels_for`'s output dict and every test. `stamp_turn_taint(turn, conversation, artifacts, *,
actor=None)`, `ShareRefused(missing_ids, missing_names, unnamed_count, message)` and
`StreamAccess(ok, missing, missing_names, unnamed_count, is_owner)` now match their Interfaces
blocks, which round 1 found they did not. `may_manage_workstream` — not `_may_manage` — throughout.
`wall_ids` is public, because `agents/entitlements.py` calls it.

**4. Dependency order and commit contents.** This pass did not exist in round 0, and it is where
three of round 1's Majors were found.

- **Every task's `git add` stages every file its own green step needs.** Task 6 and Task 7 are
  swapped from their round-0 order, because `tools/rag/workstreams.py` cannot compile against the
  unmodified `readable_documents(principal)`; Task 10 stages the three `preflight_turn` callers;
  Task 15 stages the panel template it adds the pin forms to; Task 18 stages
  `models/queue/visibility.py` and `agents/visibility.py`.
- **No task's template reverses a URL name a later task creates.** Task 13's panel template carries
  no pin control; Task 15 adds those three forms in the same commit as `rag-workstream-pin`.
- **No task's test reverses a route a later task creates.** Task 17's §12.4 sweep no longer names
  `chat-workstream-consolidate`; those rows are asserted in Task 18, beside the route.
- **No WS-1 task references a WS-2 symbol.** Task 10's open-box query pin no longer names
  `agents_workstreamtaint`; Task 16 adds it.

**5. Non-vacuity.** Every new assertion can fail. The 108-cell corpus matrix gained an explicit
pin, because `in_orm == in_chunks` passes trivially on the 27 `contained-elsewhere` cells and every
label/grant mismatch; the picker's render-vs-gate test gained the world it was describing but never
building; the two "runs no permission query" tests now open a query capture and name their table;
and `panel()`, the sidebar's Workstreams list and the stream page each carry the pair Global
Constraint 6 requires.

---

## Plan review

### Round 1 — 2026-09-04, adversarial plan-hygiene review of `ddfbf62`

**Verdict: AMEND. 20 Major · 12 Minor · 7 Nit.** No deviation from spec §23's rulings A–G or its
twenty-seven author decisions was found; every finding was mechanical, test-hygiene or
under-specification. The reviewer checked each claim against the real tree at `2fbd0cb`+`ddfbf62`
rather than against the plan's own quotations, and confirmed clean: the four migration numbers, all
seven reconciliations R1–R7, the composition law's two expressions agreeing, `_visibility_filters`'
real signature, the seventeen audit actions adding up, and every `identity.testing` helper the plan
uses.

**Disposition: all 39 findings applied.** The three that needed real authoring rather than patching:

- **M19** — `plan_consolidate`, `run_consolidate`, `summarize_consolidate`,
  `on_consolidate_terminal` and `workstream_consolidate` were prose; all five are now written out,
  with the transaction boundary, the `NOTES_DIR` filename, the labelling-authority exemption, the
  job-written provenance line and the destroy-then-recreate window all decided rather than left to
  the implementer.
- **M20** — `distil_conversation` was a docstring with no body; it has one, plus a test that
  exercises it against a mock rather than patching it away.
- **M16** — the in-flight marker had no mechanism. See the deviation below.

**One proposed edit was implemented differently, and this is the record of why.**

> **M16.** The review proposed adding `latest_job_for(kind, *, payload_match=None)` to
> `models/queue/backend.py` plus a thin passthrough in `models/contracts/queue.py`.
>
> **Implemented instead as `live_job_for(kind, **payload_match)` in `models/queue/visibility.py`.**
> The review's diagnosis is exactly right — `latest_job_id` does not exist, and a reader over
> `InferenceJob` cannot live in the contracts leaf without inverting that module's own
> no-`models.queue.models` rule — but its remedy adds two functions across two modules when a
> sanctioned seam already exists for precisely this crossing.
> `foundation/ops/tests/test_import_law.py::
> test_other_columns_reach_the_queues_visibility_and_nothing_else_of_models_queue` names
> `models.queue.visibility` the **one** submodule of `models.queue` that `tools/` and `agents/` may
> import, in those words: *"so a later view cannot reach `models.queue.backend` or
> `models.queue.scheduler` for a visibility answer that already has one home."* That module already
> imports `InferenceJob` and already filters on `payload__<key>`. One function in one module, the
> contracts leaf untouched, no new dispatch indirection, and no widening of the import law. The
> function's own docstring records that it is not, strictly, a visibility answer — it takes no
> principal — and why it lives there anyway.

**Two further corrections the review's own edits would have carried through:**

- **M13** proposed `from models.contracts.jobkinds import JobCancelled   # or wherever it is
  defined — verify`. It is defined nowhere: `grep -rn JobCancelled` over `models/` and `agents/`
  returns nothing. A queued job is cancelled by `models.queue.backend.cancel_job`, and a *running*
  handler is never interrupted by an exception. The test is rewritten to assert the shape spec §7.2
  actually claims — the turn stops between two tool turns, for any reason — with a plain
  `RuntimeError`, and the test is renamed from `..._cancelled_...` to `..._that_stops_...` so its
  name stops promising a mechanism this tree does not have.
- **M18** proposed writing `_ref_for` "following `test_jobs.py:96-98`'s
  `ANSWER_RESOLVED`/`EMBED_RESOLVED` convention". Those are module constants for two fixed roles;
  the consolidation planner resolves two *different* roles, so `_ref_for(role)` is written as a
  role-keyed helper over `jobs._ref(role, resolve(role))` rather than a third hardcoded constant.

**Nothing was rejected.** The one finding the author had raised and the reviewer partly rejected —
that the 108-cell matrix is unbounded for runtime — was correctly rejected: `4 × 3 × 3 × 3` is
bounded and deterministic. The reviewer's counter-finding, that the matrix is **vacuous** because
`in_orm == in_chunks` passes trivially on `False == False`, was upheld and is fixed by an explicit
non-vacuity pin over the accumulated cell outcomes.

**Not re-reviewed here:** a scoped re-check of these amendments goes back to the reviewer, not to
the author.

### Round 2 — 2026-09-04, scoped re-check of `c021410`

**Verdict: AMEND, 4 residuals, all mechanical.** All thirty-nine round-1 findings verified landed.
**All three of round 1's variant implementations were accepted as better than the edits proposed** —
`live_job_for` in `models/queue/visibility.py` over a backend function plus a contracts passthrough;
the `JobCancelled` test rewritten to the shape this tree actually has; and `_ref_for` keyed by role
rather than a third hardcoded resolved-model constant.

**Disposition: all four applied.**

| # | Severity | Finding | Fix |
|---|---|---|---|
| R2 | Major | Three `SyntaxError`s in `tools/rag/tests/test_consolidate.py`: the `client` parameter was appended to three test names without its opening paren (`…_repairableclient):`), a casualty of round 1's bulk signature edit | Three-character fix each — the paren restored on all three |
| R1 | Minor | `agents/tests/test_taint.py` imported `make_job_ctx`/`make_tool_ctx` from `agents/runtime/tests/_helpers.py`; both are **defined** in `agents/tests/_helpers.py` (`:134`, `:170`) and merely re-exported there (`:153-154`) | Merged onto the `agents.tests._helpers` line the module already had; the import block is sorted while it is being touched, and the prose at the helper note corrected to match |
| R3 | Minor | `tools/rag/distil.py`'s only import was `__future__`, yet `distil_conversation` builds `ChatMessage(role=MessageRole.USER, …)` | `from llama_index.core.llms import ChatMessage, MessageRole` added at module scope; the trailing prose that had asked for it separately now points at it |
| R4 | Nit | `agents/runtime/delegate.py` is edited and staged in Task 9 but absent from the Modified-files table | Added; the count is now **35** |

**One reviewer caveat, recorded rather than coded around.** The corpus matrix's non-vacuity pin
reads a module-level `_OUTCOMES` list, which makes it order-dependent: under `-k`, `-x` or a
random-order plugin it can see a partial product or none. Accepted, because of which way it fails —
an empty or short list trips its first assertion and the pin **fails loudly** (*"the matrix did not
run"*) rather than passing vacuously, which is the failure direction that matters for a pin whose
whole job is catching vacuity. Now stated in the pin's own docstring so a future reader does not
mistake it for an oversight.
