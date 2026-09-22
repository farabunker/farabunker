# Deletion semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make "delete" mean delete — a deleted conversation, document, Ask record or generated image vanishes from every surface immediately, its content is destroyed on a date the person was shown, and what survives is a content-free audit event.

**Architecture:** One new identity table (`DeletionTicket`) records that an item is deleted, who deleted it, and the date its content will be destroyed. Every column's existing visibility function excludes ticketed keys from its BASE queryset, so the item disappears everywhere at once. On the cliff — or on a click — the kind's handlers run through a new retention namespace on the EXISTING `identity/contracts/cascades.py` registry, resolved by dotted path so `identity/` still imports no other column. Three retention settings live on the existing `IdentitySettings` row, in one "Retention" section of a page that already exists; `models/queue` reads one of them across the `identity.access` seam and gains no column at all.

**Tech Stack:** Django 5.2, PostgreSQL, server-rendered templates, no static pipeline, no new dependency, no Django signals.

**Spec:** `docs/superpowers/specs/2026-09-21-deletion-semantics-design.md` — owner-approved. §11 records owner rulings that override earlier text in that document; §10 names what is OUT of scope and must not be planned or built (enterprise hold BEHAVIOUR, a longer retention for tool-call records, a dry-run count mode, workstream retention, retroactive backup purge). An implementer does not get to reopen any of them.

**Stewardship (whose clearance each task's hunks need, pre-merge, per `AGENTS.md`'s cross-column rule):**

| Task | Steward |
|---|---|
| 1–7, 12–19 (`identity/`, `foundation/`, `models/queue`, `models/contracts`) | the agents/rag/queue steward |
| 8, 9 (`agents/`), 10 (`tools/rag`), 20, 21 (`tools/rag`) | the agents/rag/queue steward |
| 11, 22 (`tools/vision/visibility.py`, `services.py`, `apps.py`, `views.py`, new `tools/vision/retention.py`) | **the vision steward** |
| 23 (`identity/` page + `foundation/ops/` gate + docs) | the agents/rag/queue steward |

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Worktree.** All work happens in the worktree **directory** `.claude/worktrees/single-repo-docs`, which holds the **branch** `deletion-semantics` (off `origin/dev`). The repository root checkout is production and is never read, written, tested or deployed from.
- **Tests and documentation ship in the same commit as the change.** Not a follow-up, not a separate PR. A column's README ships in that column's own task; only `OPERATIONS.md`, the ADR and `EXTENDING.md` are deferred to the one cross-cutting docs task.
- **The four runs plus the two posture sweeps are the BRANCH gate**, in both feature-flag states and both collection orders, against **the session's private test database** (`DATABASE_URL` pointing at this branch's own preview Postgres, with a database name nobody else is using — never a bare shared `test_farabunker`, and never a database URL, port or absolute path written into this plan or into any committed file). This is identity, posture and visibility work in every column it touches (`AGENTS.md`, "The working loop"), so the branch gate always includes the posture sweeps, not just the four flag/scope runs. **Run it at the slice-one gate (after Task 15), and again before the pull request, after pinging the queue steward** — this machine runs a cap of two concurrent suites, so a whole-repo run is coordinated, not opportunistic:
  ```bash
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q scripts identity agents foundation models tools
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q scripts identity agents foundation models tools
  ```
  The two posture sweeps are the same command with `FARABUNKER_TEST_POSTURE` set to `personal` and then to `enterprise` (the variable is read only by `identity/testing.py::seed_sweep_posture`, never by production code):
  ```bash
  FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  ```
  One pytest process per session, native, foreground, never in a container, never two runs against one database name.
- **Each task's own pre-commit gate is narrower than the branch gate above**: its focused test modules (the ones its brief names) plus the structural gates its brief names — import law, column boundaries, agent standards, docs model names, and any other gate the task's own files touch — run in both feature-flag states. Do not run the whole-repo four-plus-two branch gate per task; it is the slice-one and pre-PR checkpoint, not a per-task requirement.
- **Import-law placement of every new module** (spec §9, verbatim; downward only through `identity → foundation → models → agents → tools`):

  | New or amended | Column | May import | Must not |
  |---|---|---|---|
  | `identity/contracts/retention.py` (new, pure) | identity | nothing but the standard library | Django, any project module |
  | `identity/contracts/cascades.py` (amended) | identity | unchanged — stays pure | — |
  | `identity/cascades.py` (amended) | identity | Django's `import_string` | `agents`, `tools`, `models` |
  | `identity/retention.py` (new) | identity | `identity.*`, Django | `agents`, `tools`, `models` |
  | `identity/views.py`, `urls.py`, `routes.py`, templates, `management/commands/purge_deleted.py` | identity | `identity.*` | as above |
  | `agents/contracts/artifacts.py` (one slot) | agents | stays pure | — |
  | `agents/retention.py` (new) | agents | `agents.*`, `identity.*`, `models.contracts.*` | `tools.*`, `models.queue.*` |
  | `tools/rag/retention.py`, `tools/vision/retention.py` (new) | tools | `identity.*`, `models.contracts.*`, `agents.contracts.*`, own column | `models.queue.models` (rule 2) |
  | `models/queue/retention.py` (new) | models | `models.queue.*` (including `backend.cancel_job`), `identity.contracts`, `identity.access`, `identity.audit`, `identity.request` | **anything under `agents.` or `tools.`**, and `identity.models` |
  | `models/queue/backend.py` (amended: the prune's age condition) | models | `identity.access.queue_retention_days`, `identity.contracts.retention` for the default constant | **`identity.models`**, which is outside `IDENTITY_PERMITTED`; the row read happens on identity's side of the seam |
  | `identity/access.py` (one new non-creating reader) | identity | `identity.models` (intra-column) | `agents`, `tools`, `models` |
  | `models/contracts/queue.py` (one passthrough) | models | unchanged | `models.queue.models` |
  | `foundation/ops/tests/test_deletion_coverage.py` (new) | foundation | `django.apps`, `identity.contracts.cascades`, `identity.access` | nothing new; it introspects models, it imports no column's models module |

  **`models/queue` may import ONLY `identity.contracts` / `identity.access` / `identity.request` / `identity.audit` — never `identity.models`.**
- **`identity.retention` becomes a fifth named identity seam.** `foundation/ops/tests/test_import_law.py::IDENTITY_PERMITTED` is an allowlist and today reads exactly `("identity.contracts", "identity.access", "identity.request", "identity.audit")`. `ticketed_keys` is called from `agents/visibility.py`, `tools/rag/access.py` and `tools/vision/visibility.py`, and `delete_content` from three delete surfaces, so `"identity.retention"` is added to that tuple in Task 5, with the module carrying an `identity/access.py`-shaped "A NAMED SEAM" docstring. Nothing else under `identity/` is opened: `identity.models`, `identity.services`, `identity.views`, `identity.forms`, `identity.middleware` and `identity.testing` stay closed, and `test_the_allowlist_closes_a_module_added_after_it_was_written` and `test_identity_testing_is_closed_to_production_by_the_same_mechanism` must both stay green.
- **The owner's principle (spec §3.0, §11.8), held as a test this whole plan must pass:** *a maintainer installs this and has to set up NOTHING; every option has a working default; there are as few options as the feature can honestly have.* **Zero required setup, working defaults, three settings, one "Retention" section** on a page that already exists. No fourth knob, no second mode, no scheduler, no cron requirement, no cache.
- **Handler contract.** A retention handler's signature is `(key: str) -> int`. **One mode** — no `commit` flag, no dry-run count, no per-item count on any page. Every handler is **IDEMPOTENT**: re-running it on a partially purged item completes rather than raises. The runner runs every `ORDER_ROWS` handler **before** any `ORDER_FILES` handler. **The runner never swallows** — a handler that cannot be imported, or that raises, takes the whole purge down inside `purge_ticket`'s `transaction.atomic()`. Each handler is invoked inside a **nested `transaction.atomic()` savepoint** (the `agents/attachments.py::delete_attachments_for` discipline) with the opposite catch policy, so a database-level error does not poison the connection for the audit write that follows.
- **The queue steward's four binding conditions**, carried here and in the queue-half tasks:
  1. the identity read is **boot-tolerant and non-raising**, with a documented default — the CALLER wraps it in `except (ProgrammingError, OperationalError)` and falls back to `QUEUE_RETENTION_DAYS_DEFAULT`, logging with `exc_info=True`, never re-raising, because an exception escaping `tick()` is what `models/queue/worker.py::run_forever` reads as `crashed=True` and ends in `os._exit(1)`;
  2. the read is **non-creating** (`IdentitySettings.objects.filter(pk=1).first()`, never `get_solo()`'s `get_or_create`) and is **never made inside `models/queue/claim.py::claim_and_admit`'s advisory-lock transaction**;
  3. **one read per prune**, never one per row, pinned by a query-count test;
  4. **terminal rows only** for the age prune, and **cancel-before-delete with a refusal on `"already_running"`** for a live turn's job in `forget_conversation`.
- **The vision steward's notes**, carried verbatim: the exclusion goes on `tools/vision/visibility.py::visible_jobs`; `output:<id>` / `input:<id>` references are `GeneratedOutput` / `JobInput` primary keys, each **one FK hop** from its job, so the mapping **dedupes by job**; generation ids come from `Turn.data["id"]` (`tools/vision/services.py::job_json`'s first key), which is what catches a job that **failed and minted no output at all**; `delete_job`'s best-effort engine-side sweep (`store.remove_engine_files`, which never raises) is **said once**, in `tools/vision/README.md`, and not restated at every call site.
- **The exclusion is applied to the BASE queryset, BEFORE each visibility function's `sees_all_content` early return.** `sees_all_content` is True for every principal on an open box — the posture most boxes run — so an exclusion bolted onto the restricted leg alone would leave deleted items fully visible exactly where it matters most.
- **Plain user-facing copy, declared once in Python** (`AGENTS.md` house style), in `identity/contracts/retention.py`: `"Deleted"`, `"Deletion log"`, `"Restore"`, `"Delete permanently"`, `"Purge on <date>"`, `"Keep deleted items for"`, `"Keep finished queue jobs for"`, `"Show item names in the deletion log"`. The word *purge* appears in code, in the spec and in the date line's verb — and nowhere else a person reads. No "purge queue", no "retention cliff", no "ticket".
- **Purge is synchronous, inside the request.** "Delete permanently" calls `purge_ticket`, which runs every registered handler inside one `transaction.atomic()` and returns before the redirect. No queue job, no worker hop, no `on_commit` hook, no cache.
- **Exactly ONE migration**, in `identity/`: `identity/migrations/0004_deletion_ticket_and_retention_settings.py` — the ticket table (including the three hold columns, written by nothing) plus the three retention fields on `IdentitySettings`. `identity/migrations/` ends at `0003_entitlement_and_grant.py`, verified in the tree. **`models/queue` gains no migration and no column.**
- **Four new audit action names**, and no fifth: `CONTENT_DELETED = "content.deleted"`, `CONTENT_RESTORED = "content.restored"`, `CONTENT_PURGED = "content.purged"`, `RETENTION_POLICY_CHANGED = "identity.retention_policy_changed"`. No `CONTENT_HELD` — the action tuple is closed and `AuditEvent.save()` raises on anything unlisted, so an unused name would be an action nothing can write.
- **No absolute machine paths**, and **no AI model or vendor names**, in code, tests, docs or this plan file — `foundation/ops/tests/test_docs_model_names.py` walks `docs/superpowers/**`.
- **No `conftest.py`.** Shared fixtures live in each app's `tests/_helpers.py`; generic ones in `identity/testing.py` (`posture`, `seed_sweep_posture`, `make_user`, `make_admin`, `user_principal`, `sign_in`, `reset_settings`, `grant`, `make_entitlement`, `make_group`, `make_queue_job`). `identity/tests/_helpers.py` re-exports those and adds the row builders (`make_agent`, `make_conversation`, `make_turn`, `make_document`, `make_generation`, `make_output`, `make_job_input`, `make_workstream`, `make_category`). Use them by name; do not retype them.
- **No Django signals anywhere.** `agents/visibility.py::delete_conversation`'s own docstring already records why; a `post_delete` receiver would be exactly the wrong mechanism for a delete that must be explicit, counted and audited.
- **Never-500.** Every new route answers 404 / a flashed refusal / a redirect, never a traceback. Refusals raise `identity.services.ServiceRefused`, the sentence-carrying exception this column already uses.
- **CSS ownership.** A selector's home is the deepest template that is an ancestor of every template that uses it (`foundation/ops/tests/test_css_ownership.py`). **Settings-area styles live where the settings pages' styles live:** a rule shared by several settings leaves goes in `foundation/templates/_settings.html`'s `{% block extra_style %}`; a rule used by one leaf goes in that leaf's own `{% block extra_style %}`, which must open with `{{ block.super }}`; a rule needed outside the settings area too goes in `_shell.html`'s unconditional `<style>`. The Deleted page's own rules are leaf-local and reuse `section.settings`, `.msg`, `.warn` and `.muted`, which `_settings.html` and `_shell.html` already own — it re-types none of them.
- **Query pins are non-vacuous**: compare a one-row render against a many-row render (the equality-under-scale shape `test_thread.py` and `test_sidebar.py` use), never an absolute count that drifts with unrelated work.
- **House style.** Docstrings on new code. Commit subjects are `type(scope): subject`, imperative, lower case, no trailing period. **A change that deliberately re-pins an existing test says so, by name, in its commit message.**

---

## File structure

**Slice 1 — identity core**

| File | Responsibility |
|---|---|
| `identity/contracts/retention.py` | NEW, PURE. The kind vocabulary, the two defaults, the bounds, every user-facing string, `purge_on_line`. |
| `identity/contracts/cascades.py` | `RetentionHandler`, `ORDER_ROWS`, `ORDER_FILES`, `register_retention_handler`, `retention_handlers` — beside `EntitlementCascade`, same file, same purity. |
| `identity/cascades.py` | `run_retention(kind, key)` — one public function, savepoint per handler, never swallows. |
| `identity/models.py` | `DeletionTicket`; three retention fields on `IdentitySettings`. |
| `identity/migrations/0004_deletion_ticket_and_retention_settings.py` | The one migration. |
| `identity/contracts/actions.py` | The four new action names, in `AUDIT_ACTIONS`. |
| `identity/audit.py` | `by_action(actions, limit=100)`. |
| `identity/retention.py` | NEW, a named seam. `delete_content`, `restore_content`, `purge_ticket`, `sweep`, `ticketed_keys`, `visible_tickets`, `SWEEP_LIMIT`. |
| `identity/management/commands/purge_deleted.py` | NEW. `--limit`, service principal, `source="cli"`. |
| `identity/services.py` | `set_posture` writes and bounds the three retention fields; one audit action, the field in `detail`. |
| `identity/forms.py` | Three fields on `PostureForm`. |
| `identity/views.py` | The Retention section's context; `deleted_page`, `deleted_restore`, `deleted_purge`. |
| `identity/urls.py`, `identity/routes.py` | Three new routes, three classifications (A, O, O). |
| `identity/templates/identity/deleted.html` | NEW. Two tabs, Restore, Delete permanently. |
| `identity/templates/identity/settings.html` | The Retention section. |
| `foundation/settings_area.py`, `foundation/templates/_settings.html`, `foundation/settings_help.py` | The "Your content" group, the sidebar entry, the Deleted help card, the Identity & security card's new fields, the Queue card's one sentence. |
| `foundation/ops/tests/test_deletion_coverage.py` | NEW. The repo-wide coverage gate. |

**Slice 1 — the columns**

| File | Responsibility |
|---|---|
| `agents/visibility.py` | `visible_conversations` exclusion; `delete_conversation` becomes ticketing. |
| `agents/retention.py` | NEW. `purge_conversation`, `scrub_tool_records`, the artifact/generation collection. |
| `agents/contracts/artifacts.py` | `register_artifact_purge` / `artifact_purge` — one slot. |
| `agents/apps.py` | Registers the `conversation` FILES handler. |
| `agents/chat/templates/chat/_thread_actions.html`, `_sidebar_row.html`, `agents/chat/views/conversations.py` | The delete affordance's copy and the notice. |
| `tools/rag/retention.py` | NEW. `purge_conversation_notes` (slice 1); `purge_document`, `purge_ask` (slice 2). |
| `tools/rag/access.py` | The two exclusions on `readable_documents` / `listable_documents` / `attached_documents`; `visible_ask_records` (slice 2). |
| `tools/rag/apps.py` | Registers the rag handlers. |
| `tools/vision/retention.py` | NEW. `purge_artifacts` (slice 1); `purge_job` (slice 2). |
| `tools/vision/visibility.py` | `visible_jobs` exclusion. |
| `tools/vision/apps.py` | Registers the artifact purge and (slice 2) the `vision_job` handler. |

**The queue half (behind the gate)**

| File | Responsibility |
|---|---|
| `identity/access.py` | `queue_retention_days()` — non-creating. |
| `models/queue/retention.py` | NEW. `forget_conversation` (cancel-before-delete), `forget_document`. |
| `models/queue/apps.py` | Registers both, from `ready()`. |
| `models/queue/backend.py` | `_prune_finished_jobs(limit, retention_days)`; the one tolerant identity read in `enqueue`; `forget_jobs`. |
| `models/contracts/queue.py` | `forget_jobs` passthrough. |
| `models/queue/views.py` | The Queue settings page's one-sentence pointer. |

**Slice 2**

| File | Responsibility |
|---|---|
| `tools/rag/views.py`, `tools/rag/urls.py` | `document_delete` tickets; `ask_delete` — a new route, and `HistoryView` stops being read-only. |
| `tools/rag/templates/rag/history.html` | The per-row delete affordance. |
| `tools/vision/views.py` | `job_delete` and `jobs_delete_selected` ticket instead of deleting. |

---

## Deviations from the spec, and why

Three places this plan resolves something §-level text left open. An implementer does not get to re-open them.

1. **`identity.retention` is added to `IDENTITY_PERMITTED`.** Spec §9's import table says `agents/retention.py` may import `identity.*`, but `foundation/ops/tests/test_import_law.py` enforces an ALLOWLIST of exactly four identity seams, and `ticketed_keys` has three cross-column callers by design (§3.4). Without the addition, Task 8 fails the import-law gate. One name is added, the module carries a seam docstring, and every other private identity module stays closed — see Global Constraints.
2. **`delete_content`'s `owner` argument is the ITEM ROW, not a `Principal`.** `identity/contracts/principals.py` and `identity/request.py` are the only two files that may CONSTRUCT a `Principal` (AST-pinned in `test_import_law.py`), and every caller holds a row with `owner_kind`/`owner_key` columns rather than a principal. `owner` is therefore duck-typed on those two attributes, exactly as `identity.access.may_read_owned_row(principal, row)` already is.
3. **The coverage gate ships in Slice 1 with `rag.AskRecord` and `vision.GenerationJob` in `_EXEMPT`, reasoned "gains a registered handler in Slice 2"; Task 23 moves those two lines into `_COVERED`.** Both carry the `owner_kind`/`owner_key` pair and have no registered handler until Slice 2, so condition (b) would fail the build on the day Task 13 lands. **Two lists, not three:** an earlier draft of this plan added a temporary `_DEFERRED` list and a test pinning its eventual absence, which is a second, softer exemption vocabulary plus a test whose whole subject is an attribute not existing. `_EXEMPT` already means "somebody wrote a line and a reviewer read it", and "gains a handler in Slice 2" is exactly such a line.

4. **`identity.DeletionTicket` is itself in `_EXEMPT`.** The ticket carries `owner_kind`/`owner_key` — they name the ITEM's owner — so condition (b) sees it the moment Task 2 lands. It is bookkeeping about a deletion, destroyed by the purge it records, and a deletion of the deletion record is not a thing this design has.

---

## Facts verified against the tree before execution

Each was read in this worktree at `cc0f29d`, not recalled. An implementer who finds one false has found a tree that moved, and should stop and say so rather than adapting silently.

| Fact | Where it was checked |
|---|---|
| **The migration is `0004`.** `identity/migrations/` holds `0001_initial.py`, `0002_repoint_admin_log_fk.py`, `_0002_helpers.py` and `0003_entitlement_and_grant.py` — nothing higher. | `ls identity/migrations` |
| **The app labels are `agents`, `rag`, `vision`, `jobs`, `identity`.** `_COVERED` and `_EXEMPT` spell those, never a dotted module path: `models/queue`'s label is `jobs` and `tools/vision`'s is `vision`. | `AgentsConfig.label`, `RagConfig.label`, `VisionConfig.label`, `JobsConfig.label` |
| **`SERVICE_PRINCIPAL` is `Principal("service", "local")`**, so the sweep's audit rows carry `actor_kind="service"`, `actor_key="local"`. | `identity/contracts/principals.py` |
| **`cancel_job` returns exactly four strings** — `"cancelled"`, `"unknown"`, `"already_running"`, `"already_finished"` — and schedules the kind's `on_terminal` hook only on `"cancelled"`. `forget_conversation` treats the first, second and fourth as deletable and refuses on the third. | `models/queue/backend.py::cancel_job` |
| **`parse_artifact("output:12:extra")` raises `ValueError`.** Only `document` references carry a title suffix behind a second colon; a third colon on `output`/`input` is still a refusal, and `test_artifacts.py` already pins that case. That is why `agents/retention.py` drops-and-logs rather than letting one stored string stop a purge. | `agents/contracts/artifacts.py::parse_artifact` and its `_TITLED_KINDS` |

---

# SLICE 1 — personal-posture hard purge, conversation kind, end to end

### Task 1: The retention vocabulary and the registry namespace

**Files:**
- Create: `identity/contracts/retention.py`
- Modify: `identity/contracts/cascades.py` (append after `all_entitlement_cascades`)
- Modify: `identity/tests/test_purity.py` (add the new module to `_PROBE`)
- Test: `identity/tests/test_retention_contracts.py` (new)

**Interfaces:**
- Produces: `KIND_CONVERSATION="conversation"`, `KIND_DOCUMENT="document"`, `KIND_ASK="ask"`, `KIND_VISION_JOB="vision_job"`, `RETENTION_KINDS`, `KIND_LABELS`, `RETENTION_DAYS_DEFAULT=30`, `QUEUE_RETENTION_DAYS_DEFAULT=1`, `RETENTION_DAYS_MAX=3650`, `QUEUE_RETENTION_DAYS_MIN=1`, the six copy constants, `purge_on_line(day) -> str`, **`class RetentionRefused(Exception)`**; `RetentionHandler(kind, key, label, handler, order=ORDER_ROWS)`, `ORDER_ROWS=100`, `ORDER_FILES=200`, `register_retention_handler(spec)`, `retention_handlers(kind) -> list[RetentionHandler]`.
- Consumes: nothing. Pure — standard library only.

**`RetentionRefused` lives HERE, in the pure module, and that placement is load-bearing.** A retention handler may refuse — today only the queue's "a worker holds this job" — and the Deleted page's POST view has to render that refusal as a sentence rather than a traceback. `identity.services.ServiceRefused` is the exception this column already uses for exactly that, but `models/queue` **may not import `identity.services`**: `foundation/ops/tests/test_import_law.py`'s `IDENTITY_PERMITTED` allowlist does not name it, and `test_no_column_imports_identitys_private_modules` fails the build on it. `identity.contracts` **is** on that allowlist, and this module is pure (standard library only), so every column can raise the one refusal type the view catches. That is why it is not a `ServiceRefused` and not a bare `RuntimeError`.

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_retention_contracts.py
"""The retention vocabulary and the registry namespace beside
`EntitlementCascade`. Pure: no database, no Django settings needed."""
from __future__ import annotations

import datetime

import pytest

from identity.contracts import cascades as cascades_module
from identity.contracts import retention
from identity.contracts.cascades import (
    ORDER_FILES, ORDER_ROWS, RetentionHandler, register_retention_handler,
    retention_handlers,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """THE REGISTRY IS A MODULE-LEVEL DICT with no reset path, exactly
    like `EntitlementCascade`'s own `_CASCADES` beside it -- so every
    test module that registers into it needs this, matching
    `identity/tests/test_cascades.py::_isolated_registry` and every
    other registry isolation in this codebase
    (`identity/tests/test_ownership.py`,
    `agents/tests/_helpers.py::isolated_tool_registry`).

    Without it, a handler registered here survives this module and
    reaches every later `run_retention()` in the same pytest process --
    including handlers whose dotted path names a function that does not
    exist, which would make every later purge raise `ImportError`. The
    suite runs in BOTH collection orders, so this is not something
    ordering luck can cover.
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    yield
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


class TestTheKindVocabulary:
    def test_the_four_kinds_are_closed_and_unique(self):
        assert retention.RETENTION_KINDS == (
            retention.KIND_CONVERSATION, retention.KIND_DOCUMENT,
            retention.KIND_ASK, retention.KIND_VISION_JOB)
        assert len(set(retention.RETENTION_KINDS)) == 4

    def test_every_kind_has_a_plain_label(self):
        for kind in retention.RETENTION_KINDS:
            assert retention.KIND_LABELS[kind]

    def test_workstream_is_not_a_kind(self):
        """Spec section 10.2: a stream is a container whose contents each
        have their own cliff."""
        assert "workstream" not in retention.RETENTION_KINDS


class TestTheShippedDefaults:
    def test_a_box_that_configures_nothing_keeps_deleted_items_for_thirty_days(self):
        assert retention.RETENTION_DAYS_DEFAULT == 30

    def test_and_finished_queue_jobs_for_one_day(self):
        assert retention.QUEUE_RETENTION_DAYS_DEFAULT == 1

    def test_the_bounds_are_named_here_not_typed_at_a_call_site(self):
        assert retention.RETENTION_DAYS_MAX == 3650
        assert retention.QUEUE_RETENTION_DAYS_MIN == 1


class TestTheCopyIsDeclaredOnceInPython:
    def test_the_seven_strings_read_as_plain_words(self):
        assert retention.PAGE_TITLE == "Deleted"
        assert retention.ACTION_RESTORE == "Restore"
        assert retention.ACTION_PURGE == "Delete permanently"
        assert retention.LABEL_RETENTION_DAYS == "Keep deleted items for"
        assert retention.LABEL_QUEUE_RETENTION_DAYS == "Keep finished queue jobs for"
        assert retention.LABEL_AUDIT_DETAIL == "Show item names in the deletion log"

    def test_the_date_line_is_a_promise_a_person_can_check(self):
        assert retention.purge_on_line(datetime.date(2026, 10, 21)) == "Purge on 21 October 2026"

    def test_the_copy_never_says_ticket_cliff_or_sweep(self):
        words = " ".join([
            retention.PAGE_TITLE, retention.ACTION_RESTORE, retention.ACTION_PURGE,
            retention.LABEL_RETENTION_DAYS, retention.LABEL_QUEUE_RETENTION_DAYS,
            retention.LABEL_AUDIT_DETAIL,
            retention.purge_on_line(datetime.date(2026, 10, 21)),
        ]).lower()
        for jargon in ("ticket", "cliff", "sweep", "cascade", "retention"):
            assert jargon not in words


class TestTheRefusalType:
    def test_it_is_a_plain_exception_carrying_a_sentence(self):
        """Raised by a retention handler, caught by the Deleted page's
        POST view, and defined HERE so `models/queue` can raise it --
        that column may import `identity.contracts` and may not import
        `identity.services`."""
        assert issubclass(retention.RetentionRefused, Exception)
        assert str(retention.RetentionRefused("a worker holds this job")) \
            == "a worker holds this job"

    def test_it_lives_in_the_pure_module_so_every_column_can_raise_it(self):
        assert retention.RetentionRefused.__module__ == "identity.contracts.retention"


class TestTheRegistry:
    def test_a_handler_needs_a_known_kind(self):
        with pytest.raises(ValueError, match="not a retention kind"):
            RetentionHandler(kind="workstream", key="x.y", label="X",
                             handler="pkg.mod.fn")

    def test_a_handler_needs_a_dotted_path(self):
        with pytest.raises(ValueError, match="dotted path"):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="x.y",
                             label="X", handler="notdotted")

    def test_a_handler_needs_a_key_and_a_label(self):
        with pytest.raises(ValueError, match="non-blank key"):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="",
                             label="X", handler="pkg.mod.fn")
        with pytest.raises(ValueError, match="non-blank label"):
            RetentionHandler(kind=retention.KIND_CONVERSATION, key="x.y",
                             label="", handler="pkg.mod.fn")

    def test_registration_is_idempotent_like_every_sibling_registry(self):
        spec = RetentionHandler(kind=retention.KIND_ASK, key="test.ask",
                                label="Ask", handler="pkg.mod.fn")
        register_retention_handler(spec)
        register_retention_handler(spec)
        assert [h for h in retention_handlers(retention.KIND_ASK)
                if h.key == "test.ask"] == [spec]

    def test_rows_run_before_files_whatever_order_they_registered_in(self):
        files = RetentionHandler(kind=retention.KIND_DOCUMENT, key="test.files",
                                 label="Files", handler="pkg.mod.files",
                                 order=ORDER_FILES)
        rows = RetentionHandler(kind=retention.KIND_DOCUMENT, key="test.rows",
                                label="Rows", handler="pkg.mod.rows")
        register_retention_handler(files)
        register_retention_handler(rows)
        keys = [h.key for h in retention_handlers(retention.KIND_DOCUMENT)
                if h.key.startswith("test.")]
        assert keys == ["test.rows", "test.files"]

    def test_the_default_order_is_rows(self):
        assert RetentionHandler(kind=retention.KIND_ASK, key="a.b", label="A",
                                handler="p.m.f").order == ORDER_ROWS
        assert ORDER_ROWS < ORDER_FILES

    def test_an_unregistered_kind_answers_an_empty_list_not_an_error(self):
        assert retention_handlers("not-a-kind") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_retention_contracts.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'identity.contracts.retention'`.

- [ ] **Step 3: Write `identity/contracts/retention.py`**

```python
"""The deletion vocabulary: what kinds exist, what the shipped policy
is, and every word a person reads.

PURE -- no Django, no database, no I/O -- pinned by
`identity/tests/test_purity.py`, a subprocess with no settings module
configured at all. That is what lets `models/queue` name
`QUEUE_RETENTION_DAYS_DEFAULT` across the `identity.contracts` seam
without importing anything that touches a connection.

THE COPY LIVES HERE, not in a template and not in a form, because
user-facing sentences are declared once in Python (`AGENTS.md`, house
style) and these particular sentences have four readers apiece: the
form label, the page, the help card and the test that pins them. The
word "purge" appears in this module, in the design spec and in the date
line's verb -- and nowhere else a person reads: there is no "purge
queue", no "retention cliff" and no "ticket" in anything rendered.
"""
from __future__ import annotations

import datetime

# -- the kinds ----------------------------------------------------------
# A CLOSED TUPLE, validated in `DeletionTicket.save()` exactly as
# `AuditEvent.save()` validates its action against `AUDIT_ACTIONS`, and
# for the identical reason: a typo'd kind is a construction error at the
# call site, not a category that silently splits a page in two.
#
# `workstream` is deliberately absent (spec section 10.2): a stream is a
# container whose contents each have their own cliff, and
# `agents.visibility.delete_workstream` is unchanged.
KIND_CONVERSATION = "conversation"
KIND_DOCUMENT = "document"
KIND_ASK = "ask"
KIND_VISION_JOB = "vision_job"
RETENTION_KINDS = (KIND_CONVERSATION, KIND_DOCUMENT, KIND_ASK, KIND_VISION_JOB)

# What each kind is CALLED on the Deleted page. Plain nouns: a person is
# never asked to know that a generated image is a "vision job".
KIND_LABELS = {
    KIND_CONVERSATION: "Conversation",
    KIND_DOCUMENT: "Document",
    KIND_ASK: "Ask record",
    KIND_VISION_JOB: "Generated image",
}

# -- the shipped policy -------------------------------------------------
# THE WHOLE POINT OF THESE TWO CONSTANTS: a box that is installed and
# never configured deletes content correctly, purges it thirty days
# later, and keeps its finished queue rows for a day. No field filled
# in, no command scheduled, no posture decided (spec section 3.0).
RETENTION_DAYS_DEFAULT = 30
QUEUE_RETENTION_DAYS_DEFAULT = 1

# RANGE LIMITS LIVE IN THE WRITER, never as a database constraint
# (`foundation/settings_bounds.py`) -- named here so the writer, the
# form and the help card all spell the same numbers.
#
# `retention_days` may be 0: "no grace period" must stay expressible,
# and it is the posture-independent way to say it. `queue_retention_days`
# may NOT be 0 -- blank (null) is how "no age cliff" is said there, the
# same "honestly unknown, never silently assumed" convention
# `JobSettings.memory_budget_bytes` already documents.
RETENTION_DAYS_MIN = 0
RETENTION_DAYS_MAX = 3650
QUEUE_RETENTION_DAYS_MIN = 1
QUEUE_RETENTION_DAYS_MAX = 3650

# -- the copy -----------------------------------------------------------
# ONE constant for the page's name AND its first section's heading: they
# are the same word because they name the same thing, and two constants
# holding "Deleted" would be two places for it to stop being the same.
PAGE_TITLE = "Deleted"
# The page's second section: the content-free log of items deleted,
# restored and permanently deleted -- the same "deletion log"
# `LABEL_AUDIT_DETAIL` names, so the tab heading and the setting that
# controls what it shows use one word for one thing.
TAB_LOG = "Deletion log"
ACTION_RESTORE = "Restore"
ACTION_PURGE = "Delete permanently"
LABEL_RETENTION_DAYS = "Keep deleted items for"
LABEL_QUEUE_RETENTION_DAYS = "Keep finished queue jobs for"
LABEL_AUDIT_DETAIL = "Show item names in the deletion log"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def purge_on_line(day: datetime.date) -> str:
    """"Purge on 21 October 2026" -- the promise the page prints.

    Built from `day.day` and a month NAME rather than a `strftime`
    format, because the un-padded day directive (`%-d`) is
    platform-specific and this sentence must read identically wherever
    the box runs.
    """
    return f"Purge on {day.day} {_MONTHS[day.month - 1]} {day.year}"


# -- the one refusal ----------------------------------------------------
class RetentionRefused(Exception):
    """A purge this box will not perform right now, with an
    operator-readable reason.

    RAISED BY A RETENTION HANDLER, CAUGHT BY THE DELETED PAGE'S POST
    VIEW, which renders `str(exc)` as a flashed sentence and leaves the
    ticket in place -- the never-500 shape every mutation on this box
    has. Today exactly one handler raises it:
    `models.queue.retention.forget_conversation`, when a worker still
    holds one of the conversation's jobs.

    IT LIVES IN THIS PURE MODULE RATHER THAN BESIDE
    `identity.services.ServiceRefused`, and that is not a stylistic
    call. `models/queue` may import `identity.contracts` and may NOT
    import `identity.services`:
    `foundation/ops/tests/test_import_law.py`'s `IDENTITY_PERMITTED`
    allowlist names the first and not the second, and
    `test_no_column_imports_identitys_private_modules` fails the build
    on it. A distinct type, not a bare `RuntimeError`, so the view
    catches a refusal deliberately rather than catching every
    programming error a handler might contain.
    """
```

- [ ] **Step 4: Append the retention namespace to `identity/contracts/cascades.py`**

```python
# --- the retention namespace -------------------------------------------
# A SECOND DATACLASS AND A SECOND REGISTRY DICT, IN THIS SAME MODULE --
# not a second registry module. Deleting an entitlement and purging a
# deleted item are two questions with one shape: "this thing is going
# away; what does your column have to do about it". Same purity, same
# dotted-path discipline, same `AppConfig.ready()` self-registration,
# and one file a reader has to hold in their head instead of two.
from identity.contracts.retention import RETENTION_KINDS  # noqa: E402

# Two named bands, and only two. The runner runs every ORDER_ROWS
# handler before any ORDER_FILES handler, stable within a band by
# registration order.
#
# THIS IS THE FILESYSTEM-LAST RULE, and its whole purpose is the one
# thing a database transaction cannot undo: a filesystem delete has no
# rollback (`tools/rag/services.py::delete_document`'s own docstring),
# so a row handler that raised AFTER files were removed would leave a
# resurrected row pointing at bytes that are gone. Rows first means the
# common failure -- a database error -- aborts the purge with nothing on
# disk touched.
#
# A handler that must both READ an item's rows and REMOVE its bytes
# registers in the FILES band and does its own reads before its own
# writes, internally. That keeps this rule to one field with two values
# instead of a general dependency graph nothing else needs.
ORDER_ROWS = 100
ORDER_FILES = 200


@dataclass(frozen=True)
class RetentionHandler:
    """One column's answer to "this deleted item's content is going".

    `kind`    -- which ticket kind this answers for, from
                 `identity.contracts.retention.RETENTION_KINDS`.
    `key`     -- stable identifier, e.g. "agents.conversation".
    `label`   -- the key in the audit event's content-free `removed` map,
                 e.g. "Conversation and turns".
    `handler` -- "package.module.function", with the signature
                 `(key: str) -> int`.
    `order`   -- ORDER_ROWS (default) or ORDER_FILES.

    ONE HANDLER, ONE MODE -- deliberately unlike `EntitlementCascade`
    above, and the difference is worth stating where a reader meets it.
    That registry needs `commit=False` because deleting an entitlement
    is irreversible the instant it is confirmed: the count IS the
    confirmation. A deletion has a better confirmation than any number
    -- the Deleted page itself, where the item sits named and restorable
    for as many days as the policy says. So there is no count-only mode,
    no `commit` flag, and no per-item count anywhere a person looks; the
    returned integer has exactly one consumer, the content-free
    `removed={label: count}` detail on the `content.purged` event.

    EVERY HANDLER MUST BE IDEMPOTENT. Re-running one on a
    partially-purged item must COMPLETE rather than raise. That is not
    an assumption, it is this contract's obligation, and it is the whole
    recovery story for a purge that failed part-way: a FILES-band
    handler that raised after some bytes were gone leaves the rows
    standing, the ticket standing and the item still hidden, and the
    next sweep retries.
    """

    kind: str
    key: str
    label: str
    handler: str
    order: int = ORDER_ROWS

    def __post_init__(self) -> None:
        if self.kind not in RETENTION_KINDS:
            raise ValueError(
                f"{self.kind!r} is not a retention kind; must be one of "
                f"{list(RETENTION_KINDS)}")
        if not self.key:
            raise ValueError("RetentionHandler needs a non-blank key")
        if not self.label:
            raise ValueError(f"RetentionHandler({self.key!r}) needs a non-blank label")
        if "." not in self.handler:
            raise ValueError(
                f"RetentionHandler({self.key!r}).handler must be a dotted path, "
                f"got {self.handler!r}")


_RETENTION: dict[str, RetentionHandler] = {}


def register_retention_handler(spec: RetentionHandler) -> None:
    """Register `spec` under its `.key`, replacing any existing entry.
    Idempotent, like every sibling registry in this codebase."""
    _RETENTION[spec.key] = spec


def retention_handlers(kind: str) -> list[RetentionHandler]:
    """Every handler registered for `kind`, ROWS band before FILES band,
    stable within a band by registration order.

    A kind nothing has registered for answers `[]` -- which is not an
    error: it is what a box with a feature uninstalled looks like, and
    what every kind looks like before its column's slice lands.
    """
    matching = [spec for spec in _RETENTION.values() if spec.kind == kind]
    return sorted(matching, key=lambda spec: spec.order)
```

The `from identity.contracts.retention import RETENTION_KINDS` line moves up beside the existing `from dataclasses import dataclass` when you write it — the `# noqa: E402` above is only to show where it comes from; there is no linter configured, and the import belongs at module top with the others.

- [ ] **Step 5: Add the new module to the purity probe**

In `identity/tests/test_purity.py`, add one line to `_PROBE`, after `import identity.contracts.cascades`:

```python
    import identity.contracts.retention
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest -q identity/tests/test_retention_contracts.py identity/tests/test_purity.py`
Expected: PASS. `sorted` is stable in Python, so equal `order` values keep insertion order — which is what "stable within a band by registration order" means and what `test_rows_run_before_files_whatever_order_they_registered_in` asserts.

- [ ] **Step 7: Run the full gate and commit**

```bash
git add identity/contracts/retention.py identity/contracts/cascades.py \
        identity/tests/test_retention_contracts.py identity/tests/test_purity.py
git commit -m "feat(identity): the retention vocabulary and handler registry"
```

---

### Task 2: The deletion ticket, the three settings fields, and the one migration

**Files:**
- Modify: `identity/models.py` (add `DeletionTicket`; three fields on `IdentitySettings`)
- Create: `identity/migrations/0004_deletion_ticket_and_retention_settings.py`
- Test: `identity/tests/test_deletion_ticket.py` (new)

**Interfaces:**
- Consumes: `identity.contracts.retention.RETENTION_KINDS`, `RETENTION_DAYS_DEFAULT`, `QUEUE_RETENTION_DAYS_DEFAULT` (Task 1).
- Produces: `identity.models.DeletionTicket` with `kind`, `key`, `owner_kind`, `owner_key`, `deleted_by_kind`, `deleted_by_key`, `label`, `deleted_at`, `purge_on`, `hold_by_kind`, `hold_by_key`, `hold_note`; `UniqueConstraint(fields=["kind", "key"], name="uniq_deletion_ticket")`; `IdentitySettings.retention_days`, `.queue_retention_days`, `.audit_detail`.

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_deletion_ticket.py
"""The ticket table and the three retention policy fields."""
from __future__ import annotations

import datetime

import pytest
from django.db.utils import IntegrityError

from identity.contracts import retention
from identity.models import DeletionTicket, IdentitySettings

pytestmark = pytest.mark.django_db


def _ticket(**overrides):
    fields = dict(kind=retention.KIND_CONVERSATION, key="k-1",
                  owner_kind="user", owner_key="7",
                  deleted_by_kind="user", deleted_by_key="7",
                  purge_on=datetime.date(2026, 10, 21))
    fields.update(overrides)
    return DeletionTicket.objects.create(**fields)


class TestTheKindIsAClosedVocabulary:
    def test_an_unknown_kind_raises_at_save_like_an_unknown_audit_action(self):
        with pytest.raises(ValueError, match="Unknown deletion kind"):
            _ticket(kind="workstream")

    def test_every_declared_kind_saves(self):
        for index, kind in enumerate(retention.RETENTION_KINDS):
            assert _ticket(kind=kind, key=f"k-{index}").pk


class TestOneTicketPerItem:
    def test_a_second_ticket_for_the_same_item_is_refused_by_the_constraint(self):
        _ticket()
        with pytest.raises(IntegrityError):
            _ticket()

    def test_the_same_key_under_a_different_kind_is_a_different_item(self):
        _ticket(kind=retention.KIND_CONVERSATION, key="7")
        assert _ticket(kind=retention.KIND_ASK, key="7").pk


class TestTheHoldColumnsShipEmpty:
    def test_nothing_in_this_delivery_writes_them(self):
        ticket = _ticket()
        assert ticket.hold_by_kind == ""
        assert ticket.hold_by_key == ""
        assert ticket.hold_note == ""


class TestTheShippedPolicyNeedsNoConfiguration:
    def test_a_freshly_migrated_row_carries_the_documented_defaults(self):
        row = IdentitySettings.get_solo()
        assert row.retention_days == retention.RETENTION_DAYS_DEFAULT
        assert row.queue_retention_days == retention.QUEUE_RETENTION_DAYS_DEFAULT
        assert row.audit_detail is False

    def test_zero_and_null_are_both_storable(self):
        """The "zero stays expressible" constraint, for two fields:
        `retention_days=0` is "no grace period", `queue_retention_days=
        None` is "no age cliff"."""
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.queue_retention_days = None
        row.save()
        row.refresh_from_db()
        assert row.retention_days == 0
        assert row.queue_retention_days is None


class TestOrderingAndIndexes:
    def test_tickets_come_back_newest_first(self):
        first = _ticket(key="a")
        second = _ticket(key="b")
        assert list(DeletionTicket.objects.all()) == [second, first]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_deletion_ticket.py`
Expected: FAIL — `ImportError: cannot import name 'DeletionTicket'`.

- [ ] **Step 3: Add the three fields to `IdentitySettings`**

In `identity/models.py`, import the contracts module beside the existing `identity.contracts.postures` import:

```python
from identity.contracts.retention import (
    QUEUE_RETENTION_DAYS_DEFAULT, RETENTION_DAYS_DEFAULT, RETENTION_KINDS,
)
```

and add three fields to `IdentitySettings`, after `session_idle_minutes` and before `updated_at`:

```python
    # THE WHOLE RETENTION POLICY, ON THE ROW THAT ALREADY CARRIES THE
    # POSTURE (owner ruling, 2026-09-21). One retention policy belongs in
    # one place: two settings pages would mean two places to look, two
    # writers to validate, and a real chance the content cliff is changed
    # while the queue silently keeps its own. `models.queue.models.
    # JobSettings` gains NOTHING -- the queue READS `queue_retention_days`
    # across the `identity.access` seam at prune time, it does not own it.
    #
    # EVERY ONE IS OPTIONAL AND EVERY ONE HAS A WORKING DEFAULT, so a
    # maintainer who never opens this page still gets correct behaviour.
    # Range limits live in the WRITER (`identity.services.set_posture`),
    # never as a database constraint -- `foundation/settings_bounds.py`.
    #
    # How long a deleted item stays restorable. `0` is legal and means
    # "no grace period": the sweep the same request runs picks the ticket
    # up and the content is gone before the response returns.
    retention_days = models.PositiveIntegerField(default=RETENTION_DAYS_DEFAULT)
    # How long a FINISHED queue row survives. NULL means no age cliff at
    # all (the FIFO `retention_limit` alone) -- the same "honestly
    # unknown, never silently assumed" convention `JobSettings.
    # memory_budget_bytes` documents. `0` is NOT legal here; blank is how
    # "no cliff" is said, which is why the writer refuses it.
    queue_retention_days = models.PositiveIntegerField(
        null=True, blank=True, default=QUEUE_RETENTION_DAYS_DEFAULT)
    # Whether a deletion event carries the item's own title. OFF: the
    # event says a conversation with this id was deleted by this actor at
    # this time -- written and rendered regardless. ON: `target_label`
    # carries the title. NO EVENT IS SUPPRESSED BY THIS TOGGLE in either
    # direction: an audit trail with a switch that turns rows off is not
    # an audit trail.
    audit_detail = models.BooleanField(default=False)
```

- [ ] **Step 4: Add `DeletionTicket` to `identity/models.py`**

Place it after `AuditEvent` and before `Entitlement`:

```python
class DeletionTicket(models.Model):
    """One deleted item, and the date its content will be destroyed.

    A TICKET EXISTS EXACTLY WHILE THE ITEM IS RESTORABLE. There is no
    purged-but-pending state, no second cliff and no ticket that outlives
    its content: `identity.retention.purge_ticket` destroys the row in
    the same transaction that destroys the content, so the two can never
    disagree. That single invariant is what lets restore be "delete the
    ticket" and nothing else -- the item was never modified, so there is
    nothing to put back.

    ONE TABLE RATHER THAN A `deleted_at` COLUMN ON FOUR MODELS IN THREE
    COLUMNS, for three reasons. (a) Four tables means four migrations and
    four places to forget an exclusion. (b) A cliff, an actor, a label
    and a hold are facts about the DELETION, not about the conversation.
    (c) The Deleted page is one query over one table; with per-model
    columns it is a union over four querysets in three columns that
    `identity/` may not import.

    `key` IS THE ITEM'S PRIMARY KEY AS TEXT. The four kinds have three pk
    types (UUID, UUID, int, int); one text column is the
    `agents.models.Share.target_key` precedent, and
    `identity.retention.ticketed_keys` handles the join the same way
    `agents.shares.shared_keys` does -- a materialised list, never a
    `Subquery` that would need a per-type cast.

    NO FOREIGN KEY TO `User` for either principal: a principal is two
    strings, for `AuditEvent`'s own recorded reason.

    THE THREE HOLD COLUMNS SHIP EMPTY AND ARE WRITTEN BY NOTHING. The
    control that would set them is the deferred enterprise slice (spec
    section 10.10); the sweep's due-condition already excludes a held
    ticket. They are created by this migration anyway because the
    alternative is a second migration later against a table that by then
    holds live deletion bookkeeping -- three blank columns cost nothing,
    that migration is a risk with a maintenance window attached. This is
    the one place in this feature where something is built before it is
    used, and it is said here rather than discovered in a field list.
    """

    kind = models.CharField(max_length=32)
    key = models.CharField(max_length=200)
    # The ITEM's owner, stamped at create from the item's own owner
    # columns -- same two names, same widths, same blank default as the
    # seven owned tables that already carry them.
    owner_kind = models.CharField(max_length=32, blank=True, default="")
    owner_key = models.CharField(max_length=200, blank=True, default="")
    # The ACTOR, which is not always the owner: an administrator deletes
    # somebody's row, and the cliff acts as the service principal.
    deleted_by_kind = models.CharField(max_length=32, blank=True, default="")
    deleted_by_key = models.CharField(max_length=200, blank=True, default="")
    # The item's title AT DELETE TIME, for the Deleted page only. It is
    # CONTENT and is treated as such: never copied into an audit event
    # unless `audit_detail` is on, and destroyed with the ticket.
    label = models.CharField(max_length=255, blank=True, default="")
    deleted_at = models.DateTimeField(auto_now_add=True)
    # A DATE, not a datetime: "Purge on 21 October 2026" is the promise
    # the page prints, and a date is what a person can check. Computed
    # once at create and NEVER recomputed -- a changed setting governs
    # future deletes only, because moving this date earlier would destroy
    # content sooner than the person was told.
    purge_on = models.DateField(db_index=True)
    hold_by_kind = models.CharField(max_length=32, blank=True, default="")
    hold_by_key = models.CharField(max_length=200, blank=True, default="")
    hold_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-deleted_at"]
        constraints = [
            # ONE TICKET PER ITEM, so a second delete of the same item is
            # a no-op rather than a duplicate row -- `get_or_create` is
            # the writer. It is also the index `ticketed_keys` reads.
            models.UniqueConstraint(fields=["kind", "key"],
                                    name="uniq_deletion_ticket"),
        ]
        indexes = [
            models.Index(fields=["owner_kind", "owner_key"],
                         name="identity_ticket_owner"),
        ]

    def save(self, *args, **kwargs):
        """A closed kind vocabulary, exactly as `AuditEvent.save()`
        validates its action.

        `ValueError`, not a `ValidationError`: a typo'd kind is a
        programming error at the call site, not a form an operator can
        correct -- and a kind that silently stored would split the
        Deleted page in two.
        """
        if self.kind not in RETENTION_KINDS:
            raise ValueError(
                f"Unknown deletion kind {self.kind!r}. Add it to "
                f"identity/contracts/retention.py::RETENTION_KINDS first.")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"DeletionTicket({self.kind}:{self.key} -> {self.purge_on})"
```

- [ ] **Step 5: Generate the one migration**

Run: `.venv/bin/python manage.py makemigrations identity --name deletion_ticket_and_retention_settings`
Expected: creates `identity/migrations/0004_deletion_ticket_and_retention_settings.py` with one `CreateModel`, one `AddConstraint`, one `AddIndex` and three `AddField`s. Read it and confirm it contains **nothing else** — in particular no `models/queue` migration is generated and none is needed.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest -q identity/tests/test_deletion_ticket.py`
Expected: PASS.

- [ ] **Step 7: Run the full gate and commit**

```bash
git add identity/models.py identity/migrations/0004_deletion_ticket_and_retention_settings.py \
        identity/tests/test_deletion_ticket.py
git commit -m "feat(identity): the deletion ticket and the three retention settings"
```

---

### Task 3: The four audit actions and the `by_action` reader

**Files:**
- Modify: `identity/contracts/actions.py` (four constants, four entries in `AUDIT_ACTIONS`)
- Modify: `identity/audit.py` (`by_action`)
- Test: `identity/tests/test_actions.py` (extend), `identity/tests/test_audit.py` (extend — create if the module does not exist in this tree; check first with `ls identity/tests`)

**Held tests — two hard counts, both of which this task moves. Name both in the commit message.**
- `identity/tests/test_actions.py::test_every_name_carries_its_family_prefix` holds a CLOSED SET of sixteen prefixes and asserts `len(prefixes) == 16`, then asserts every action starts with one of them. `content.` is not in that set, so the three content actions fail it. **Re-pin:** add `"content."` to the set, change the assertion to `== 17`, and extend that test's own docstring with one sentence in its established style — it already narrates every earlier amendment (`vision.`, `workstream.`, `conversation.`, then `queue.`/`connection.`/`role.` taking it from thirteen to sixteen), so this one says that deletion semantics (2026-09-21) adds `content.` and pushes it to seventeen. `identity.retention_policy_changed` reuses the existing `identity.` prefix and adds none.
- `identity/tests/test_actions.py` asserts `len(AUDIT_ACTIONS) == 68`. **Re-pin to `== 72`** — three content actions plus one settings action.

Nothing else in that module needs a re-pin: the uniqueness check (`len(set(AUDIT_ACTIONS)) == len(AUDIT_ACTIONS)`), the 64-character ceiling (`max(len(a) for a in AUDIT_ACTIONS) <= 64`) and the membership spot-checks all pass unchanged for the four new names.

**Interfaces:**
- Produces: `CONTENT_DELETED`, `CONTENT_RESTORED`, `CONTENT_PURGED`, `RETENTION_POLICY_CHANGED`; `identity.audit.by_action(actions, limit=100) -> list[AuditEvent]`.

- [ ] **Step 1: Write the failing tests**

```python
# in identity/tests/test_actions.py -- the two held counts
        prefixes = {"identity.", "entitlement.", "grant.", "group.", "library.",
                    "tool.", "share.", "modelset.", "agent.", "flow.", "vision.",
                    "workstream.", "conversation.", "queue.", "connection.", "role.",
                    "content."}
        assert len(prefixes) == 17

# and at the end of `test_the_full_vocabulary_is_declared_now_not_in_two_
# passes` in the same module -- three content actions plus one settings
# action. Add the four membership spot-checks beside the existing ones,
# and one sentence to that test's amendment narrative:
        assert "content.deleted" in AUDIT_ACTIONS
        assert "content.restored" in AUDIT_ACTIONS
        assert "content.purged" in AUDIT_ACTIONS
        assert "identity.retention_policy_changed" in AUDIT_ACTIONS
        assert len(AUDIT_ACTIONS) == 72
```

```python
# append to identity/tests/test_actions.py
class TestTheContentActions:
    """Spec section 3.12: three content actions and one settings action,
    and no fourth content action -- a `content.held` name would be an
    action nothing in this delivery can write, and `AuditEvent.save()`
    raises on an unlisted action precisely so the tuple stays honest."""

    def test_the_three_content_actions_are_declared(self):
        assert actions.CONTENT_DELETED == "content.deleted"
        assert actions.CONTENT_RESTORED == "content.restored"
        assert actions.CONTENT_PURGED == "content.purged"
        for name in (actions.CONTENT_DELETED, actions.CONTENT_RESTORED,
                     actions.CONTENT_PURGED):
            assert name in AUDIT_ACTIONS

    def test_the_settings_action_is_one_per_domain_not_one_per_field(self):
        assert actions.RETENTION_POLICY_CHANGED == "identity.retention_policy_changed"
        assert actions.RETENTION_POLICY_CHANGED in AUDIT_ACTIONS

    def test_no_hold_action_is_declared_before_the_control_that_writes_it(self):
        assert not [a for a in AUDIT_ACTIONS if a.endswith(".held")]
```

`actions` is imported at the top of that module as `from identity.contracts import actions` if it is not already — check and add.

```python
# identity/tests/test_audit_by_action.py  (new)
"""`identity.audit.by_action` -- the Purged tab's one read."""
from __future__ import annotations

import pytest

from identity import audit
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, LOGIN,
)
from identity.contracts.principals import OPEN_PRINCIPAL

pytestmark = pytest.mark.django_db


class TestByAction:
    def test_it_returns_only_the_named_actions_newest_first(self):
        audit.record(OPEN_PRINCIPAL, LOGIN)
        first = audit.record(OPEN_PRINCIPAL, CONTENT_DELETED,
                             target_type="conversation", target_key="a")
        second = audit.record(OPEN_PRINCIPAL, CONTENT_PURGED,
                              target_type="conversation", target_key="a")
        rows = audit.by_action((CONTENT_PURGED, CONTENT_DELETED, CONTENT_RESTORED))
        assert [row.pk for row in rows] == [second.pk, first.pk]

    def test_it_honours_the_limit(self):
        for index in range(3):
            audit.record(OPEN_PRINCIPAL, CONTENT_DELETED,
                         target_type="ask", target_key=str(index))
        assert len(audit.by_action((CONTENT_DELETED,), limit=2)) == 2

    def test_an_empty_action_sequence_answers_an_empty_list(self):
        audit.record(OPEN_PRINCIPAL, CONTENT_DELETED, target_type="ask", target_key="1")
        assert audit.by_action(()) == []

    def test_it_returns_a_list_not_a_queryset(self):
        assert isinstance(audit.by_action((CONTENT_DELETED,)), list)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_actions.py identity/tests/test_audit_by_action.py`
Expected: FAIL — `AttributeError: module 'identity.contracts.actions' has no attribute 'CONTENT_DELETED'`.

- [ ] **Step 3: Declare the four actions**

In `identity/contracts/actions.py`, after the `WORKSTREAM_CONSOLIDATED` block and before `AUDIT_ACTIONS`:

```python
# Deletion (2026-09-21): an item soft-deleted, restored, or purged. A
# `content.` namespace -- naming WHAT CHANGED, not which table, the
# convention this module's comments already argue for: one vocabulary
# covers a conversation, a document, an Ask record and a generated
# image, because "a person's content was deleted" is one fact whatever
# row held it. `target_type` is the ticket KIND and `target_key` the
# item's own key, so `for_target("conversation", pk)` still answers the
# question an operator asks.
#
# CONTENT-FREE BY CONSTRUCTION. `detail` carries the kind and, on a
# purge, `removed={label: count}` -- integers. The item's own TITLE
# reaches `target_label` only when `IdentitySettings.audit_detail` is
# on, and no event is ever suppressed in either position: an audit trail
# with a switch that turns rows off is not an audit trail.
#
# NO FOURTH CONTENT ACTION. A `content.held` name belongs in the same
# commit as the control that writes it (the deferred enterprise slice,
# spec section 10.10) -- this tuple is closed and `AuditEvent.save()`
# raises on anything unlisted, so an unused name would be an action
# nothing can write.
CONTENT_DELETED = "content.deleted"
CONTENT_RESTORED = "content.restored"
CONTENT_PURGED = "content.purged"

# `RETENTION_POLICY_CHANGED` (2026-09-21): any of `IdentitySettings`'
# three retention fields changed. ONE action for all three, with the
# literal column in `detail` -- `LIBRARY_SETTINGS_UPDATED`'s own "one
# action per settings DOMAIN" rule, rather than the three-way split
# `POSTURE_CHANGED`/`LIBRARY_POSTURE_CHANGED`/
# `ADMIN_CONTENT_ACCESS_CHANGED` uses, because those three are
# semantically distinct security postures and these three are one
# retention policy expressed as three knobs.
RETENTION_POLICY_CHANGED = "identity.retention_policy_changed"
```

and add them to the tuple, after `WORKSTREAM_CONSOLIDATED,`:

```python
    CONTENT_DELETED, CONTENT_RESTORED, CONTENT_PURGED,
    RETENTION_POLICY_CHANGED,
```

- [ ] **Step 4: Add `by_action` to `identity/audit.py`**

After `for_target`:

```python
def by_action(actions, limit: int = 100) -> list[AuditEvent]:
    """Every row whose action is one of `actions`, newest first.

    HERE, not in the page that renders it, for the reason this module's
    own docstring gives: a page that had to name `AuditEvent.objects`
    would need an exception to the AST guard in
    `foundation/ops/tests/test_column_boundaries.py`, and a guard with
    an exception is a guard somebody widens. `recent` and `for_target`
    exist for exactly the same reason; this is the third.

    An empty `actions` answers `[]` without querying -- `action__in=()`
    is a query that can only return nothing, and the Deleted page's
    Purged tab is a never-500 surface that should not pay for one.
    """
    actions = tuple(actions)
    if not actions:
        return []
    return list(AuditEvent.objects.filter(action__in=actions)[:limit])
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q identity/tests/test_actions.py identity/tests/test_audit_by_action.py`
Expected: PASS.

- [ ] **Step 6: Run the full gate and commit**

```bash
git add identity/contracts/actions.py identity/audit.py \
        identity/tests/test_actions.py identity/tests/test_audit_by_action.py
git commit -m "feat(identity): four deletion audit actions and the by-action reader

Re-pins identity/tests/test_actions.py::test_every_name_carries_its_family_prefix (sixteen prefixes become seventeen, with content.) and ::test_the_full_vocabulary_is_declared_now_not_in_two_passes (68 actions become 72)."
```

---

### Task 4: The retention runner

**Files:**
- Modify: `identity/cascades.py` (`run_retention`)
- Test: `identity/tests/test_retention_runner.py` (new)

**Interfaces:**
- Consumes: `identity.contracts.cascades.retention_handlers` (Task 1).
- Produces: `identity.cascades.run_retention(kind: str, key: str) -> dict[str, int]` — `{label: count}`, every ROWS handler before any FILES handler, each inside a nested `transaction.atomic()`, never swallowing. **One public function, no private twin:** `_run` exists beside it only because the entitlement registry's two public wrappers share a `commit` flag; this runner has one caller and one mode.

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_retention_runner.py
"""`identity.cascades.run_retention` -- the one runner, and the two
properties the whole feature rests on: it never swallows, and the
savepoint leaves the connection usable afterwards."""
from __future__ import annotations

import pytest
from django.db import connection, transaction

from identity.cascades import run_retention
from identity.contracts import cascades as cascades_module
from identity.contracts.cascades import (
    ORDER_FILES, RetentionHandler, register_retention_handler,
)
from identity.contracts.retention import KIND_ASK
from identity.models import DeletionTicket

pytestmark = pytest.mark.django_db

CALLED: list[str] = []


def rows_handler(key: str) -> int:
    CALLED.append(f"rows:{key}")
    return 2


def files_handler(key: str) -> int:
    CALLED.append(f"files:{key}")
    return 1


def raising_handler(key: str) -> int:
    CALLED.append(f"raise:{key}")
    raise RuntimeError("this column cannot finish")


def db_error_handler(key: str) -> int:
    """A DATABASE-level error, not a Python one -- the case the savepoint
    exists for: on Postgres this poisons the connection's current
    transaction, not merely the Python call."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM a_table_that_does_not_exist")
    return 0


@pytest.fixture(autouse=True)
def _isolated_registry():
    """THE REGISTRY IS A MODULE-LEVEL DICT with no reset path, exactly
    like `EntitlementCascade`'s own `_CASCADES` beside it -- so every
    test module that registers into it needs this, matching
    `identity/tests/test_cascades.py::_isolated_registry` and every
    other registry isolation in this codebase.

    THIS MODULE IS THE ONE THAT PROVES WHY. `t.missing` below names a
    function that does not exist; without this fixture it survives the
    module and makes EVERY later `run_retention()` in the same pytest
    process raise `ImportError` -- the Deleted page's purge view, the
    demo, and the whole owner column of the route matrix. The suite runs
    in BOTH collection orders, so ordering luck cannot cover it.
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    CALLED.clear()
    yield
    CALLED.clear()
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


class TestTheRunner:
    def test_rows_run_before_files_and_the_counts_come_back_by_label(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.files", label="Files",
            handler=f"{__name__}.files_handler", order=ORDER_FILES))
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.rows", label="Rows",
            handler=f"{__name__}.rows_handler"))

        removed = run_retention(KIND_ASK, "77")

        assert CALLED == ["rows:77", "files:77"]
        assert removed == {"Rows": 2, "Files": 1}

    def test_a_kind_with_no_handlers_removes_nothing_and_does_not_raise(self):
        assert run_retention("document", "1") == {}

    def test_it_never_swallows_a_handler_that_raises(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.raise", label="Boom",
            handler=f"{__name__}.raising_handler"))
        with pytest.raises(RuntimeError, match="cannot finish"):
            run_retention(KIND_ASK, "1")

    def test_it_never_swallows_a_handler_that_cannot_be_imported(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.missing", label="Missing",
            handler="identity.nope.not_a_function"))
        with pytest.raises(ImportError):
            run_retention(KIND_ASK, "1")

    def test_the_savepoint_leaves_the_connection_usable_after_a_db_error(self):
        """THE ONLY WAY THIS SAVEPOINT'S PURPOSE IS ACTUALLY TESTED: a
        real query after the failure. Without the nested atomic block the
        connection stays poisoned and this write raises "current
        transaction is aborted" instead of succeeding."""
        register_retention_handler(RetentionHandler(
            kind=KIND_ASK, key="t.dberror", label="Broken",
            handler=f"{__name__}.db_error_handler"))
        with transaction.atomic():
            with pytest.raises(Exception):
                run_retention(KIND_ASK, "1")
            # The connection is healthy again: this is a real write.
            assert DeletionTicket.objects.count() == 0
```

Because `_isolated_registry` clears the registry for the duration of each test, `run_retention` sees **only** the handlers a test registered — so `test_rows_run_before_files_and_the_counts_come_back_by_label` can assert `CALLED` and `removed` by equality, and it keeps doing so unchanged when Slice 2 registers real handlers for `KIND_ASK`. That is the whole reason the fixture is worth more than a prefix convention.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_retention_runner.py`
Expected: FAIL — `ImportError: cannot import name 'run_retention'`.

- [ ] **Step 3: Add the runner to `identity/cascades.py`**

Extend the module docstring with a second paragraph and add the two functions:

```python
from django.db import transaction

from identity.contracts.cascades import all_entitlement_cascades, retention_handlers


def run_retention(kind: str, key: str) -> dict[str, int]:
    """`{label: count}` -- what each column removed for this deleted
    item. Call inside `identity.retention.purge_ticket`'s transaction.

    ONE RUNNER, ONE MODE, AND NO PRIVATE TWIN. `_run` above is split
    from its two public wrappers because it serves both of them with a
    `commit` flag; this one has a single caller and a single mode, so
    the body lives here rather than in a `_run_retention` that would
    exist only to be called once.

    The departure from `EntitlementCascade`'s two-mode shape is
    deliberate: that registry needs `commit=False` because an
    entitlement delete is irreversible the instant it is confirmed, so
    the count IS the confirmation. A deletion's confirmation is the
    Deleted page, where the item sits named and restorable, so there is
    nothing here for a dry-run pass to tell anybody.

    NEVER SWALLOWS, exactly as `_run` does not: a handler that cannot be
    imported, or that raises, takes the whole purge down with it, so
    nothing is half-purged at the row level and the ticket survives to
    be retried.
    """
    counts: dict[str, int] = {}
    for spec in retention_handlers(kind):
        handler = import_string(spec.handler)
        # A NESTED `transaction.atomic()` -- a SAVEPOINT -- around each
        # handler, the `agents.attachments.delete_attachments_for`
        # discipline, WITH THE OPPOSITE CATCH POLICY, and the difference
        # is the point.
        #
        # `delete_attachments_for` CATCHES, because a broken cleanup
        # provider must not block a delete the actor already confirmed.
        # A retention handler's exception is NOT caught, because a purge
        # that reported success while leaving content behind is exactly
        # the failure this whole feature exists to prevent.
        #
        # The savepoint is still required. Without it, a DATABASE-level
        # error inside a handler poisons the Postgres connection for the
        # rest of `purge_ticket`'s outer transaction, and the audit write
        # and the ticket delete that follow would fail for a reason
        # unrelated to the real one. Rolling back to the savepoint
        # restores the connection, so the real error reaches the view,
        # which renders it as a refusal sentence and leaves the ticket in
        # place for the next sweep.
        with transaction.atomic():
            counts[spec.label] = handler(key)
    return counts
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q identity/tests/test_retention_runner.py identity/tests/test_cascades.py`
Expected: PASS — including the existing entitlement-cascade tests, which this change must not touch.

- [ ] **Step 5: Run the full gate and commit**

```bash
git add identity/cascades.py identity/tests/test_retention_runner.py
git commit -m "feat(identity): the retention runner, savepointed and never swallowing"
```

---

### Task 5: `identity/retention.py` — the service, and the fifth identity seam

**Files:**
- Create: `identity/retention.py`
- Modify: `foundation/ops/tests/test_import_law.py` (`IDENTITY_PERMITTED`, and the `permitted` block of `test_the_identity_private_module_gate_would_catch_a_violation`)
- Modify: `identity/README.md`
- Test: `identity/tests/test_retention_service.py` (new)

**Held tests:**
- `foundation/ops/tests/test_import_law.py::test_the_identity_private_module_gate_would_catch_a_violation` — its `permitted` block lists the four seams that must survive unflagged; add `"from identity.retention import ticketed_keys\n"`. **Re-pin by name in the commit message.**
- `foundation/ops/tests/test_import_law.py::test_the_allowlist_closes_a_module_added_after_it_was_written` and `::test_identity_testing_is_closed_to_production_by_the_same_mechanism` — neither changes; both must stay green, proving the allowlist opened exactly one name.

**Interfaces:**
- Consumes: `identity.cascades.run_retention` (Task 4), `identity.models.DeletionTicket`/`IdentitySettings` (Task 2), `identity.audit.record` (Task 3), `identity.access.owned_rows_q`/`sees_all_content`/`may_read_owned_row`, `identity.contracts.principals.SERVICE_PRINCIPAL`.

  **NOT `identity.access.owner_fields`.** That function reads `principal.kind`/`principal.key` and returns the two columns to STAMP on a row a principal is creating — it takes a `Principal`, and `owner` here is the item ROW. The ticket copies the row's existing `owner_kind`/`owner_key` columns straight across instead.
- Produces:
  - `SWEEP_LIMIT = 25`
  - `delete_content(actor, *, kind, key, owner, label="", source=SOURCE_WEB) -> DeletionTicket`
  - `restore_content(actor, ticket, *, source=SOURCE_WEB) -> None`
  - `purge_ticket(actor, ticket, *, source=SOURCE_WEB) -> dict[str, int]`
  - `sweep(*, limit=SWEEP_LIMIT, source=SOURCE_WEB) -> int`
  - `ticketed_keys(kind) -> list[str]`
  - `visible_tickets(principal, *, settings_row=None)`
  - `may_purge(principal, ticket) -> bool`

  `owner` is **the item ROW**, read for its `owner_kind`/`owner_key` columns — never a `Principal` (only `identity/contracts/principals.py` and `identity/request.py` may construct one).

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_retention_service.py
"""Soft delete, restore, purge, and the sweep."""
from __future__ import annotations

import datetime
import logging

import pytest
from django.utils import timezone

from identity import audit as audit_module
from identity import retention as service
from identity.contracts import cascades as cascades_module
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED,
)
from identity.contracts.cascades import RetentionHandler, register_retention_handler
from identity.contracts.retention import KIND_ASK, KIND_CONVERSATION, KIND_DOCUMENT, RetentionRefused
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_admin, make_conversation, make_user, posture, user_principal,
)

pytestmark = pytest.mark.django_db

REMOVED: list[str] = []


def ask_handler(key: str) -> int:
    REMOVED.append(key)
    return 1


def boom(key: str) -> int:
    raise RuntimeError("not finished")


def refused(key: str) -> int:
    raise RetentionRefused("a worker still holds this item")


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- the shape
    `identity/tests/test_cascades.py::_isolated_registry` established,
    for the same reason: the registry is a module-level dict with no
    reset path, `boom` below names a handler that raises, and a
    registration that escaped this module would reach every later purge
    in the same pytest process. Both collection orders are the gate, so
    ordering luck cannot cover it.
    """
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    REMOVED.clear()
    register_retention_handler(RetentionHandler(
        kind=KIND_ASK, key="t.ask", label="Ask records",
        handler=f"{__name__}.ask_handler"))
    yield
    REMOVED.clear()
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


def _owner(user):
    """An item row's owner columns, which is all `delete_content` reads."""
    return make_conversation(owner_kind="user", owner_key=str(user.pk))


class TestSoftDelete:
    def test_it_writes_one_ticket_with_the_promised_date_and_purges_nothing(self):
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(
            user_principal(user), kind=KIND_ASK, key="5", owner=item, label="A question")

        assert ticket.purge_on == timezone.localdate() + datetime.timedelta(days=30)
        assert ticket.owner_kind == "user" and ticket.owner_key == str(user.pk)
        assert ticket.deleted_by_key == str(user.pk)
        assert ticket.label == "A question"
        assert REMOVED == []
        assert AuditEvent.objects.filter(action=CONTENT_DELETED).count() == 1

    def test_zero_days_purges_before_the_call_returns(self):
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.save()
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        assert REMOVED == ["5"]
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 1

    def test_a_second_delete_of_the_same_item_is_a_no_op(self):
        user = make_user()
        item = _owner(user)
        first = service.delete_content(user_principal(user), kind=KIND_ASK,
                                       key="5", owner=item)
        second = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=item)
        assert first.pk == second.pk
        assert DeletionTicket.objects.count() == 1
        assert AuditEvent.objects.filter(action=CONTENT_DELETED).count() == 1


class TestRestore:
    def test_it_deletes_the_ticket_and_records_the_event(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        service.restore_content(user_principal(user), ticket)
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_RESTORED).count() == 1
        assert REMOVED == []

    def test_restoring_an_already_gone_ticket_writes_no_event(self):
        """A raced purge or a double-click leaves the caller holding a
        `DeletionTicket` instance whose row is already gone -- restoring
        it must not log a restore that never happened, and must not
        raise either."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).delete()

        service.restore_content(user_principal(user), ticket)

        assert AuditEvent.objects.filter(action=CONTENT_RESTORED).count() == 0


class TestPurge:
    def test_a_completed_purge_leaves_no_ticket(self):
        """The invariant the whole "a ticket means restorable" reading
        rests on (spec section 3.1), asserted directly."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        removed = service.purge_ticket(user_principal(user), ticket)
        assert removed == {"Ask records": 1}
        assert DeletionTicket.objects.count() == 0

    def test_a_purge_that_rolled_back_leaves_a_ticket_restore_still_accepts(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        ticket = service.delete_content(user_principal(user),
                                        kind=KIND_CONVERSATION,
                                        key=str(item.pk), owner=item)
        with pytest.raises(RuntimeError):
            service.purge_ticket(user_principal(user), ticket)
        ticket.refresh_from_db()
        service.restore_content(user_principal(user), ticket)
        assert DeletionTicket.objects.count() == 0

    def test_the_audit_detail_toggle_moves_the_label_and_suppresses_nothing(self):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user), label="A question")
        off = AuditEvent.objects.filter(action=CONTENT_DELETED).first()
        assert off.target_label == ""
        assert off.target_type == KIND_ASK and off.target_key == "5"

        row = IdentitySettings.get_solo()
        row.audit_detail = True
        row.save()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="6",
                               owner=_owner(user), label="Another question")
        on = AuditEvent.objects.filter(action=CONTENT_DELETED,
                                       target_key="6").first()
        assert on.target_label == "Another question"
        assert AuditEvent.objects.filter(action=CONTENT_DELETED).count() == 2

    def test_a_purge_of_an_already_gone_ticket_is_a_silent_no_op(self):
        """Two sweeps can overlap by design (prune-on-write on every
        delete, the cron command, the Deleted page's own GET) and a
        person can double-click "Delete permanently" -- the second
        `purge_ticket` on the same ticket must not run a handler twice
        or write a second event."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        first = service.purge_ticket(user_principal(user), ticket)
        assert first == {"Ask records": 1}

        second = service.purge_ticket(user_principal(user), ticket)
        assert second == {}
        assert REMOVED == ["5"]

        events = [e for e in audit_module.by_action([CONTENT_PURGED])
                  if e.target_key == "5"]
        assert len(events) == 1


class TestTicketedKeys:
    def test_it_costs_one_query_and_answers_a_list(self, django_assert_num_queries):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        with django_assert_num_queries(1):
            keys = service.ticketed_keys(KIND_ASK)
        assert keys == ["5"]
        assert isinstance(keys, list)

    def test_it_answers_only_its_own_kind(self):
        user = make_user()
        service.delete_content(user_principal(user), kind=KIND_ASK, key="5",
                               owner=_owner(user))
        assert service.ticketed_keys(KIND_CONVERSATION) == []


class TestTheSweep:
    def test_it_picks_up_a_ticket_whose_date_has_arrived(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep() == 1
        assert REMOVED == ["5"]

    def test_it_skips_a_held_ticket(self):
        """Nothing in this delivery WRITES a hold, so the ticket is
        constructed with one directly -- the clause ships now so the
        deferred enterprise slice is a control and a refusal, not a
        change to this query."""
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1),
            hold_by_kind="user", hold_by_key="1")
        assert service.sweep() == 0
        assert DeletionTicket.objects.count() == 1

    def test_it_is_bounded_by_the_limit(self):
        """All four tickets are created FIRST, while none of them is due
        (the shipped default is 30 days), and backdated together in ONE
        queryset update AFTER every create has already run. Interleaving
        a create with a backdate, one ticket at a time, would let each
        later `delete_content`'s own unconditional prune-on-write sweep
        purge the earlier, now-overdue ticket before this test ever
        calls `sweep` itself -- exactly the behaviour
        `test_deleting_anything_purges_what_has_already_fallen_due`
        below pins on purpose. This test is about the LIMIT, so its own
        fixtures must not be eaten by the thing it is not testing."""
        user = make_user()
        item = _owner(user)
        tickets = [
            service.delete_content(user_principal(user), kind=KIND_ASK,
                                   key=str(index), owner=item)
            for index in range(4)
        ]
        DeletionTicket.objects.filter(pk__in=[t.pk for t in tickets]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep(limit=2) == 2
        assert DeletionTicket.objects.count() == 2

    def test_one_failing_ticket_does_not_stop_the_batch(self):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        bad = service.delete_content(user_principal(user), kind=KIND_CONVERSATION,
                                     key=str(item.pk), owner=item)
        good = service.delete_content(user_principal(user), kind=KIND_ASK,
                                      key="5", owner=item)
        DeletionTicket.objects.filter(pk__in=[bad.pk, good.pk]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        assert service.sweep() == 1
        assert DeletionTicket.objects.filter(pk=bad.pk).exists()
        assert not DeletionTicket.objects.filter(pk=good.pk).exists()

    def test_it_always_acts_as_the_service_principal(self):
        user = make_user()
        ticket = service.delete_content(user_principal(user), kind=KIND_ASK,
                                        key="5", owner=_owner(user))
        DeletionTicket.objects.filter(pk=ticket.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))
        service.sweep()
        purged = AuditEvent.objects.filter(action=CONTENT_PURGED).first()
        assert (purged.actor_kind, purged.actor_key) == ("service", "local")

    def test_deleting_anything_purges_what_has_already_fallen_due(self):
        """Prune-on-write (spec section 3.9, "Three callers"): the
        shipped default keeps a box that is used at all clean with no
        scheduler, because `delete_content` runs a bounded `sweep()`
        unconditionally, not only when the item it just deleted is
        itself due. `a` sits on the ordinary 30-day policy, already
        overdue by the time anybody deletes `b` -- a different item,
        with nothing else in common -- and `a`'s content is gone
        (purged, not merely swept up) as a side effect of that unrelated
        call, while `b`'s own ticket, freshly written and nowhere near
        its own cliff, stands untouched."""
        user = make_user()
        item = _owner(user)
        stale = service.delete_content(user_principal(user), kind=KIND_ASK,
                                       key="a", owner=item)
        DeletionTicket.objects.filter(pk=stale.pk).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        fresh = service.delete_content(user_principal(user), kind=KIND_ASK,
                                       key="b", owner=item)

        assert not DeletionTicket.objects.filter(pk=stale.pk).exists()
        assert DeletionTicket.objects.filter(pk=fresh.pk).exists()
        assert AuditEvent.objects.filter(action=CONTENT_PURGED,
                                         target_key="a").exists()
        assert REMOVED == ["a"]

    def test_a_refusal_is_a_warning_not_an_error_and_does_not_stop_the_batch(self, caplog):
        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="t.refused", label="Refused",
            handler=f"{__name__}.refused"))
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="t.boom", label="Boom",
            handler=f"{__name__}.boom"))
        user = make_user()
        item = _owner(user)
        refused_ticket = service.delete_content(
            user_principal(user), kind=KIND_CONVERSATION, key=str(item.pk), owner=item)
        errored_ticket = service.delete_content(
            user_principal(user), kind=KIND_DOCUMENT, key="doc-1", owner=item)
        DeletionTicket.objects.filter(
            pk__in=[refused_ticket.pk, errored_ticket.pk]).update(
            purge_on=timezone.localdate() - datetime.timedelta(days=1))

        with caplog.at_level(logging.WARNING):
            assert service.sweep() == 0

        assert DeletionTicket.objects.filter(pk=refused_ticket.pk).exists()
        assert DeletionTicket.objects.filter(pk=errored_ticket.pk).exists()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warnings) == 1 and warnings[0].exc_info is None
        assert len(errors) == 1 and errors[0].exc_info is not None


class TestStanding:
    def test_an_owner_and_a_sees_all_content_principal_may_purge_and_a_stranger_may_not(self):
        with posture("personal"):
            owner, stranger, admin = make_user(), make_user(), make_admin()
            item = _owner(owner)
            ticket = service.delete_content(user_principal(owner), kind=KIND_ASK,
                                            key="5", owner=item)
            assert service.may_purge(user_principal(owner), ticket) is True
            assert service.may_purge(user_principal(stranger), ticket) is False
            assert service.may_purge(user_principal(admin), ticket) is False

            row = IdentitySettings.get_solo()
            row.admin_sees_content = True
            row.save()
            assert service.may_purge(user_principal(admin), ticket) is True

    def test_visible_tickets_narrows_for_a_member_and_not_on_an_open_box(self):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            service.delete_content(user_principal(mine), kind=KIND_ASK, key="1",
                                   owner=_owner(mine))
            service.delete_content(user_principal(theirs), kind=KIND_ASK, key="2",
                                   owner=_owner(theirs))
            assert [t.key for t in service.visible_tickets(user_principal(mine))] == ["1"]
        with posture("open"):
            assert len(service.visible_tickets(user_principal(mine))) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_retention_service.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'identity.retention'`.

- [ ] **Step 3: Write `identity/retention.py`**

```python
"""Deleting, restoring and purging content -- the orchestration.

A NAMED SEAM, the fifth: every column may import this module, and
`foundation/ops/tests/test_import_law.py`'s `IDENTITY_PERMITTED`
allowlist names it beside `identity.contracts`, `identity.access`,
`identity.request` and `identity.audit`. `identity.models`,
`identity.services`, `identity.views`, `identity.forms`,
`identity.middleware` and `identity.testing` stay closed to every other
column, exactly as before.

IT IS A SEAM BECAUSE THE ANSWER IT GIVES IS A PRINCIPAL-SHAPED ONE.
`ticketed_keys(kind)` says "these keys are deleted"; it does not say
"these conversations", and it could not -- `identity/` may not import
`agents/` or `tools/` (rule 4). Each column turns that answer into an
exclusion on a queryset of its own rows, which is the same division of
labour `identity/access.py`'s docstring records for ownership.

THE ORCHESTRATION LIVES HERE, in `identity/`, and that placement is the
whole reason a conversation's QUEUE ROWS can go in the same transaction
as its turns: `agents/` may not import `models.queue` (import-law rule
2), so no delete inside `agents/` could ever reach them. Identity sits
below every column, so it can run all of their handlers -- by dotted
path, resolved at purge time, importing none of them.

`identity/contracts/retention.py` stays PURE and holds the vocabulary;
this module is the live service, the same split `identity/cascades.py`
has from `identity/contracts/cascades.py`.
"""
from __future__ import annotations

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from identity import audit
from identity.access import may_read_owned_row, owned_rows_q, sees_all_content
from identity.cascades import run_retention
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED, SOURCE_WEB,
)
from identity.contracts.principals import SERVICE_PRINCIPAL
from identity.contracts.retention import RETENTION_KINDS, RetentionRefused
from identity.models import DeletionTicket, IdentitySettings

logger = logging.getLogger(__name__)

# HOW MANY TICKETS ONE SWEEP PASS PURGES. A module constant, never a
# literal at a call site: three callers run this sweep (a delete, a page
# GET, and the command) and a number typed three times is a number that
# will differ three ways.
SWEEP_LIMIT = 25


def ticketed_keys(kind: str) -> list[str]:
    """The item keys currently deleted under `kind`, as strings.

    MATERIALISED into a list rather than left as a `Subquery` --
    `agents.shares.shared_keys`' own recorded reasoning, applied
    unchanged: `DeletionTicket.key` is text and the four kinds' tables
    have three different primary-key types, so a subquery would need a
    per-type cast and would be a silent type mismatch waiting to happen.
    Two small queries on a single-box install beat one clever one.

    Bounded by the open tickets on the box, which the cliff and the
    sweep bound in turn.
    """
    return list(
        DeletionTicket.objects.filter(kind=kind).values_list("key", flat=True))


def visible_tickets(principal, *, settings_row=None):
    """The tickets `principal` may see: their own, or everyone's for a
    principal that `sees_all_content` -- which is every principal on an
    open box, where there is nobody for anything to be hidden from."""
    qs = DeletionTicket.objects.all()
    if sees_all_content(principal, settings_row=settings_row):
        return qs
    return qs.filter(owned_rows_q(principal, settings_row=settings_row))


def may_purge(principal, ticket) -> bool:
    """Whether `principal` may destroy this item's content now.

    The item's OWNER, or a `sees_all_content` principal -- the same
    predicate that already lets them read the content, and the same
    shape `tools.vision.visibility.may_read_job` uses. There is no
    posture branch: in this delivery the enterprise posture behaves
    exactly as personal does, and the refusal that will differ is the
    deferred enterprise slice's (spec section 10.10), not this one's.
    """
    if sees_all_content(principal):
        return True
    return may_read_owned_row(principal, ticket)


def delete_content(actor, *, kind: str, key, owner, label: str = "",
                   source: str = SOURCE_WEB) -> DeletionTicket:
    """Soft-delete one item: write its ticket, record the event, and run
    a bounded sweep.

    `owner` is THE ITEM ROW, read for its `owner_kind`/`owner_key`
    columns -- never a `Principal`. `identity/contracts/principals.py`
    and `identity/request.py` are the only two files that may CONSTRUCT
    one (AST-pinned in `foundation/ops/tests/test_import_law.py`), and
    every caller here is holding a row, not a principal. Duck-typed on
    those two attributes, exactly as `identity.access.may_read_owned_row
    (principal, row)` already is.

    THAT IS ALSO WHY `identity.access.owner_fields` IS NOT USED HERE: it
    reads `principal.kind`/`principal.key` -- it is the STAMP a row gets
    when a principal creates it. The ticket is not being created by the
    item's owner; it is recording who the item's owner already was, so
    it copies the row's existing columns across. A row with neither
    column (nothing in this codebase, but the seam is public) stamps
    blank, which is what an unowned item honestly is.

    `get_or_create` ON `(kind, key)`, backed by the unique constraint: a
    second delete of the same item returns the first ticket, writes no
    second audit event, and is therefore safe to race.

    `purge_on` IS COMPUTED ONCE, HERE, from the setting in force now,
    and is never recomputed. The page prints that date and the date is a
    promise; moving it later would make the page's own history a lie,
    and moving it earlier would destroy content sooner than the person
    was told.

    THE SWEEP AT THE END IS WHAT MAKES `retention_days = 0` SYNCHRONOUS:
    the ticket this call just wrote is due today, so the same request
    purges it and the content is gone before the response returns. It is
    also what keeps a box that is used at all clean, with no scheduler
    -- the prune-on-write pattern `tools.rag.services.record_ask` and
    `models.queue.backend.enqueue` already use.
    """
    if kind not in RETENTION_KINDS:
        raise ValueError(f"{kind!r} is not a retention kind.")
    row = IdentitySettings.get_solo()
    purge_on = timezone.localdate() + datetime.timedelta(days=row.retention_days)
    with transaction.atomic():
        ticket, created = DeletionTicket.objects.get_or_create(
            kind=kind, key=str(key),
            defaults=dict(
                label=label[:255],
                purge_on=purge_on,
                deleted_by_kind=getattr(actor, "kind", ""),
                deleted_by_key=getattr(actor, "key", ""),
                owner_kind=getattr(owner, "owner_kind", ""),
                owner_key=str(getattr(owner, "owner_key", "")),
            ),
        )
        if created:
            audit.record(actor, CONTENT_DELETED, target_type=kind,
                         target_key=str(key),
                         target_label=label if row.audit_detail else "",
                         source=source, kind=kind)
    if created:
        sweep()
    return ticket


def restore_content(actor, ticket, *, source: str = SOURCE_WEB) -> None:
    """Put the item back: delete the ticket, record the event.

    NOTHING ELSE. The item was never modified, so there is nothing to
    put back -- which is the whole return on not adding per-model
    soft-delete columns. Every ticket that exists is restorable (a
    completed purge leaves none), so this has exactly one refusal to
    make and it is not made in this delivery: a held ticket, once the
    deferred enterprise slice can set a hold.

    A TICKET ALREADY GONE (a raced sweep, a double-click) MUST NOT LOG A
    RESTORE THAT DID NOT HAPPEN: deleting by QUERYSET rather than by
    instance reports how many rows it actually removed, and an event is
    written only when that count is nonzero.
    """
    row = IdentitySettings.get_solo()
    with transaction.atomic():
        kind, key, label = ticket.kind, ticket.key, ticket.label
        removed, _ = DeletionTicket.objects.filter(pk=ticket.pk).delete()
        if not removed:
            return
        audit.record(actor, CONTENT_RESTORED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind)


def purge_ticket(actor, ticket, *, source: str = SOURCE_WEB) -> dict[str, int]:
    """Destroy this item's content, then the ticket. Returns
    `{handler label: rows removed}` -- integers, content-free.

    ONE TRANSACTION. The handlers run, the ticket is deleted and the
    event is written together, so the ticket and the content can never
    disagree about whether the item still exists. The runner never
    swallows, so a handler that raises rolls every ROW change back and
    leaves the ticket standing for the next sweep -- and the view
    renders the sentence rather than a traceback.

    FILES ALREADY REMOVED BY AN EARLIER FILES-BAND HANDLER STAY REMOVED.
    Named, not hidden: a filesystem delete has no rollback. That is why
    every handler must be idempotent and why the next sweep completes
    the purge rather than re-raising on the half it already did.

    A TICKET ALREADY GONE IS A SILENT NO-OP, NOT A SECOND EVENT: two
    sweeps can overlap by design (prune-on-write on every delete, the
    cron command, the Deleted page's own GET) and a person can
    double-click "Delete permanently", so this RE-READS the row under a
    lock inside the transaction before running a single handler, and a
    miss returns `{}` with nothing run and nothing written.
    """
    row = IdentitySettings.get_solo()
    with transaction.atomic():
        current = DeletionTicket.objects.select_for_update().filter(pk=ticket.pk).first()
        if current is None:
            return {}
        removed = run_retention(current.kind, current.key)
        kind, key, label = current.kind, current.key, current.label
        current.delete()
        audit.record(actor, CONTENT_PURGED, target_type=kind, target_key=key,
                     target_label=label if row.audit_detail else "",
                     source=source, kind=kind, removed=removed)
    return removed


def sweep(*, limit: int = SWEEP_LIMIT, source: str = SOURCE_WEB) -> int:
    """Purge up to `limit` due tickets. Returns how many were purged.

    ONE DUE-CONDITION: the promised date has arrived and nothing holds
    the ticket. There is no second clause, because there is no second
    cliff and no ticket that outlives its content. The hold half is
    always true today -- nothing in this delivery writes a hold -- and it
    is in the query so the deferred enterprise slice is a control and a
    refusal, not a change to this function.

    ALWAYS ACTS AS THE SERVICE PRINCIPAL, whoever triggered it. A sweep
    that ran under the acting principal would write "this member purged
    somebody else's conversation" into the audit trail for a cliff
    nobody clicked. The cliff is the box's own act and the event says
    so; only an explicit click carries a real actor. `source` is how the
    trail tells the three callers apart -- `manage.py purge_deleted`
    passes `SOURCE_CLI`, the delete and the page's GET leave the
    default.

    EACH TICKET IN ITS OWN TRANSACTION, so one failing ticket does not
    block the rest of the batch. The failure is logged with its kind and
    key -- structural, never content, the shape `tools/rag/jobs.py` uses
    throughout -- and the ticket stays due for the next pass.

    A `RetentionRefused` IS NOT AN ERROR AND IS CAUGHT FIRST: it is a
    handler saying "not now" for an operator-readable reason (today,
    `models.queue.retention.forget_conversation` when a worker still
    holds one of the conversation's jobs), so it is logged at
    `logger.warning` -- one line, no traceback -- and every other
    exception keeps `logger.exception`, which is the failure this batch
    actually needs to be noisy about.
    """
    due = list(
        DeletionTicket.objects
        .filter(purge_on__lte=timezone.localdate(), hold_by_kind="")
        .order_by("purge_on", "pk")[:limit]
    )
    purged = 0
    for ticket in due:
        try:
            purge_ticket(SERVICE_PRINCIPAL, ticket, source=source)
        except RetentionRefused as exc:
            logger.warning(
                "identity.retention: purge refused for %s:%s; it stays due -- %s",
                ticket.kind, ticket.key, exc)
            continue
        except Exception:  # noqa: BLE001 -- one bad ticket, not a bad batch
            logger.exception(
                "identity.retention: purge failed for %s:%s; it stays due",
                ticket.kind, ticket.key)
            continue
        purged += 1
    return purged
```

- [ ] **Step 4: Open the seam in the import-law allowlist**

In `foundation/ops/tests/test_import_law.py`:

```python
IDENTITY_PERMITTED = (
    "identity.contracts", "identity.access", "identity.request", "identity.audit",
    # Deletion semantics (2026-09-21): `identity.retention` is the fifth
    # seam, and it is one for the same reason the four above are. A
    # column asks it a PRINCIPAL-shaped question -- "which keys of my
    # kind are deleted" -- and turns the answer into an exclusion on its
    # own queryset; identity could not answer "which conversations" even
    # if it wanted to (rule 4). Three visibility functions in three
    # columns call `ticketed_keys`, and three delete surfaces call
    # `delete_content`. Nothing else under `identity/` is opened.
    "identity.retention",
)
```

and add one line to `test_the_identity_private_module_gate_would_catch_a_violation`'s `permitted` block:

```python
        "from identity.retention import ticketed_keys\n"
```

- [ ] **Step 5: Document the seam in `identity/README.md`**

Add a short section beside the existing access/audit seam sections covering: the ticket table **and the fact that a ticket exists only while the item is restorable**; the three-field retention policy on `IdentitySettings` with its plain-words labels; the orchestration (why it lives in identity — the import law makes a queue reach from `agents/` impossible); and the sentence that identity answers "which keys are deleted", never "which conversations". Leave the `queue_retention_days` seam paragraph to Task 16 and the Deleted page to Task 12 — each column's doc ships with its own code.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest -q identity/tests/test_retention_service.py foundation/ops/tests/test_import_law.py`
Expected: PASS, including `test_the_allowlist_closes_a_module_added_after_it_was_written` and `test_identity_testing_is_closed_to_production_by_the_same_mechanism` unchanged.

- [ ] **Step 7: Run the full gate and commit**

```bash
git add identity/retention.py identity/README.md \
        identity/tests/test_retention_service.py foundation/ops/tests/test_import_law.py
git commit -m "feat(identity): delete, restore, purge and the sweep

Re-pins foundation/ops/tests/test_import_law.py::test_the_identity_private_module_gate_would_catch_a_violation: identity.retention joins IDENTITY_PERMITTED as the fifth named seam."
```

---

### Task 6: `manage.py purge_deleted`

**Files:**
- Create: `identity/management/commands/purge_deleted.py`
- Test: `identity/tests/test_purge_deleted_command.py` (new)

**Interfaces:**
- Consumes: `identity.retention.sweep(*, limit, source)`, `SWEEP_LIMIT` (Task 5); `identity.contracts.actions.SOURCE_CLI`.
- Produces: `manage.py purge_deleted [--limit N]`, printing how many items were purged.

- [ ] **Step 1: Write the failing test**

```python
# identity/tests/test_purge_deleted_command.py
"""`manage.py purge_deleted` -- the operator's cron door, for a box
where prune-on-write is not enough on its own."""
from __future__ import annotations

import datetime
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from identity.contracts import cascades as cascades_module
from identity.contracts.actions import CONTENT_PURGED
from identity.contracts.cascades import RetentionHandler, register_retention_handler
from identity.contracts.retention import KIND_ASK
from identity.models import AuditEvent, DeletionTicket
from identity.tests._helpers import make_conversation, make_user, user_principal
from identity import retention as service

pytestmark = pytest.mark.django_db


def noop_handler(key: str) -> int:
    return 0


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- `identity/tests/
    test_cascades.py::_isolated_registry`'s shape, for its reason: the
    registry is a module-level dict with no reset path, and a
    registration that escaped this module would reach every later purge
    in the same pytest process. Both collection orders are the gate."""
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    register_retention_handler(RetentionHandler(
        kind=KIND_ASK, key="t.cli", label="Ask records",
        handler=f"{__name__}.noop_handler"))
    yield
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


def _overdue(count: int):
    user = make_user()
    item = make_conversation(owner_kind="user", owner_key=str(user.pk))
    tickets = [
        service.delete_content(user_principal(user), kind=KIND_ASK,
                               key=str(index), owner=item)
        for index in range(count)
    ]
    # ALL tickets are created first, then backdated together in ONE queryset
    # update: backdating one at a time would let each later `delete_content`
    # call's own unconditional prune-on-write sweep purge the earlier,
    # already-overdue ticket before this helper -- or the test that called
    # it -- ever gets to see it.
    DeletionTicket.objects.filter(pk__in=[ticket.pk for ticket in tickets]).update(
        purge_on=timezone.localdate() - datetime.timedelta(days=1))


class TestPurgeDeleted:
    def test_it_purges_due_tickets_and_says_how_many(self):
        _overdue(2)
        out = StringIO()
        call_command("purge_deleted", stdout=out)
        assert DeletionTicket.objects.count() == 0
        assert "2" in out.getvalue()

    def test_its_events_are_recorded_as_command_line(self):
        _overdue(1)
        call_command("purge_deleted", stdout=StringIO())
        event = AuditEvent.objects.filter(action=CONTENT_PURGED).first()
        assert event.source == "cli"
        assert (event.actor_kind, event.actor_key) == ("service", "local")

    def test_the_limit_bounds_one_run(self):
        _overdue(3)
        call_command("purge_deleted", "--limit", "1", stdout=StringIO())
        assert DeletionTicket.objects.count() == 2

    def test_a_box_with_nothing_due_is_not_an_error(self):
        out = StringIO()
        call_command("purge_deleted", stdout=out)
        assert "0" in out.getvalue()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest -q identity/tests/test_purge_deleted_command.py`
Expected: FAIL — `CommandError: Unknown command: 'purge_deleted'`.

- [ ] **Step 3: Write the command**

```python
# identity/management/commands/purge_deleted.py
"""Purge every deleted item whose promised date has arrived.

FOR AN OPERATOR WHO WANTS A CRON RATHER THAN RELYING ON
PRUNE-ON-WRITE. It is not required: `identity.retention.delete_content`
runs a bounded sweep at the end of every delete and the Deleted page
runs one on GET, so a box that is used at all keeps itself clean. This
exists for a box that is not -- one that was deleted from and then left
alone -- and for an operator who would rather the cliff be a scheduled
thing than an incidental one.

ACTS AS THE SERVICE PRINCIPAL, like the sweep itself, and records
`source="cli"`: the cliff is the box's own act, not any person's.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from identity.contracts.actions import SOURCE_CLI
from identity.retention import SWEEP_LIMIT, sweep


class Command(BaseCommand):
    help = "Purge deleted items whose retention period has ended."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit", type=int, default=SWEEP_LIMIT,
            help=f"How many items to purge in this run (default {SWEEP_LIMIT}).")

    def handle(self, *args, **options) -> None:
        purged = sweep(limit=options["limit"], source=SOURCE_CLI)
        noun = "item" if purged == 1 else "items"
        self.stdout.write(f"Purged {purged} {noun}.")
```

Check `identity/management/commands/` exists with an `__init__.py` in each level; if not, create both (`identity/management/__init__.py`, `identity/management/commands/__init__.py`) empty.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q identity/tests/test_purge_deleted_command.py`
Expected: PASS.

- [ ] **Step 5: Run the full gate and commit**

```bash
git add identity/management identity/tests/test_purge_deleted_command.py
git commit -m "feat(identity): manage.py purge_deleted for an operator's cron"
```

---

### Task 7: The Retention settings section — form, writer, bounds, page, help card

**Files:**
- Modify: `identity/forms.py` (three fields on `PostureForm`)
- Modify: `identity/services.py` (`set_posture` writes and bounds the three fields)
- Modify: `identity/views.py` (`settings_page`'s initial + the retention labels in context)
- Modify: `identity/templates/identity/settings.html` (the Retention section)
- Modify: `foundation/settings_help.py` (three `HelpField`s on the Identity & security card)
- Test: `identity/tests/test_retention_settings.py` (new)

**Held tests:** `identity/tests/test_settings_page.py` and `identity/tests/test_services.py` both exercise `PostureForm` / `set_posture`. Adding three form fields makes a POST that omits them **invalid** (`retention_days` is a required `IntegerField`), so every existing test that posts the posture form must gain the three keys. Find them with `grep -rn "PostureForm\|identity-settings" identity/tests` before editing, and re-pin each by name in the commit message. `queue_retention_days` is `required=False` and `audit_detail` is a `BooleanField(required=False)`, so only `retention_days` is genuinely mandatory — but add all three to every posted body so the tests describe the real form.

**Interfaces:**
- Consumes: `identity.contracts.retention` labels and bounds (Task 1); `identity.contracts.actions.RETENTION_POLICY_CHANGED` (Task 3). **Not `foundation.settings_bounds`** — see `_retention_int`'s docstring below: these two fields have a policy bound nine orders of magnitude under the column's, so the column-overflow helper can never fire and a check that can never fire is a second thing to keep in agreement with the first.
- Produces: `set_posture(..., retention_days=None, queue_retention_days=_UNSET, audit_detail=None)` — one `identity.retention_policy_changed` event per field CHANGED, with `field` and `to` in `detail`.

  `queue_retention_days` uses a module-level `_UNSET` sentinel rather than `None`, because `None` is a **legal value** for that field ("no age cliff") and must be distinguishable from "not supplied".

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_retention_settings.py
"""The Retention section: three fields, one writer, one audit action."""
from __future__ import annotations

import pytest
from django.urls import reverse

from identity import services
from identity.contracts import retention as copy
from identity.contracts.actions import RETENTION_POLICY_CHANGED
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.models import AuditEvent, IdentitySettings
from identity.tests._helpers import make_admin, posture, sign_in

pytestmark = pytest.mark.django_db


class TestTheWriter:
    def test_it_writes_all_three_fields(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=7,
                             queue_retention_days=None, audit_detail=True)
        row = IdentitySettings.get_solo()
        assert (row.retention_days, row.queue_retention_days, row.audit_detail) \
            == (7, None, True)

    def test_one_event_per_field_changed_naming_the_field(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=7, audit_detail=True)
        events = AuditEvent.objects.filter(action=RETENTION_POLICY_CHANGED)
        assert {e.detail["field"] for e in events} == {"retention_days", "audit_detail"}
        assert events.count() == 2

    def test_an_unchanged_field_writes_no_event(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=7)
        services.set_posture(OPEN_PRINCIPAL, retention_days=7)
        assert AuditEvent.objects.filter(action=RETENTION_POLICY_CHANGED).count() == 1

    @pytest.mark.parametrize("days", [-1, copy.RETENTION_DAYS_MAX + 1, 2**31])
    def test_an_out_of_range_retention_is_refused_before_save(self, days):
        with pytest.raises(services.ServiceRefused):
            services.set_posture(OPEN_PRINCIPAL, retention_days=days)
        assert IdentitySettings.get_solo().retention_days \
            == copy.RETENTION_DAYS_DEFAULT

    def test_zero_is_legal_for_the_content_cliff(self):
        services.set_posture(OPEN_PRINCIPAL, retention_days=0)
        assert IdentitySettings.get_solo().retention_days == 0

    def test_zero_is_illegal_for_the_queue_cliff_because_blank_is_how_no_cliff_is_said(self):
        with pytest.raises(services.ServiceRefused, match="Leave it blank"):
            services.set_posture(OPEN_PRINCIPAL, queue_retention_days=0)

    def test_blank_is_how_no_queue_cliff_is_expressed_and_round_trips(self):
        services.set_posture(OPEN_PRINCIPAL, queue_retention_days=None)
        assert IdentitySettings.get_solo().queue_retention_days is None
        services.set_posture(OPEN_PRINCIPAL, queue_retention_days=5)
        assert IdentitySettings.get_solo().queue_retention_days == 5


class TestThePage:
    def test_the_section_renders_the_three_labels_in_plain_words(self, client):
        with posture("personal"):
            admin = make_admin()
            sign_in(client, admin)
            body = client.get(reverse("identity-settings")).content.decode()
        assert copy.LABEL_RETENTION_DAYS in body
        assert copy.LABEL_QUEUE_RETENTION_DAYS in body
        assert copy.LABEL_AUDIT_DETAIL in body

    def test_the_retention_section_never_says_ticket_cliff_or_sweep(self, client):
        """SCOPED TO THIS PAGE'S OWN CONTENT, not the whole response: the
        settings shell, the sidebar and the assistant panel are shared
        markup this page does not own, and a substring assertion over
        them would fail for a word some other surface introduced."""
        with posture("personal"):
            sign_in(client, make_admin())
            body = client.get(reverse("identity-settings")).content.decode()
        main = body.split("<main>", 1)[1].split("</main>", 1)[0].lower()
        for jargon in ("ticket", "cliff", "sweep"):
            assert jargon not in main

    def test_a_save_round_trips_both_expressible_extremes(self, client):
        with posture("personal"):
            admin = make_admin()
            sign_in(client, admin)
            client.post(reverse("identity-settings"), {
                "posture": "personal", "library_posture": "open",
                "session_idle_minutes": "0",
                "retention_days": "0", "queue_retention_days": "",
            })
        row = IdentitySettings.get_solo()
        assert row.retention_days == 0
        assert row.queue_retention_days is None

    def test_an_out_of_range_value_flashes_and_redirects_never_500s(self, client):
        with posture("personal"):
            sign_in(client, make_admin())
            response = client.post(reverse("identity-settings"), {
                "posture": "personal", "library_posture": "open",
                "session_idle_minutes": "0", "retention_days": "99999",
            })
        assert response.status_code == 302
        assert IdentitySettings.get_solo().retention_days \
            == copy.RETENTION_DAYS_DEFAULT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_retention_settings.py`
Expected: FAIL — `set_posture() got an unexpected keyword argument 'retention_days'`.

- [ ] **Step 3: Add the three form fields**

In `identity/forms.py`, import the copy and bounds and extend `PostureForm`:

```python
from identity.contracts.retention import (
    LABEL_AUDIT_DETAIL, LABEL_QUEUE_RETENTION_DAYS, LABEL_RETENTION_DAYS,
    QUEUE_RETENTION_DAYS_MAX, QUEUE_RETENTION_DAYS_MIN, RETENTION_DAYS_MAX,
    RETENTION_DAYS_MIN,
)


class PostureForm(forms.Form):
    posture = forms.ChoiceField(choices=POSTURE_CHOICES)
    library_posture = forms.ChoiceField(choices=LIBRARY_CHOICES)
    admin_sees_content = forms.BooleanField(required=False)
    session_idle_minutes = forms.IntegerField(min_value=0, max_value=60 * 24 * 30)
    # THE RETENTION SECTION (spec section 4). The LABELS come from
    # `identity.contracts.retention`, not from a string typed here: they
    # are user-facing sentences, so they are declared once in Python and
    # read by the form, the page and the help card alike.
    #
    # The form's own `min_value`/`max_value` are a convenience, not the
    # guard: the real refusals live in `identity.services.set_posture`,
    # where every other write on this row is refused, because a
    # `ModelForm`-free column keeps its guards in one place.
    retention_days = forms.IntegerField(
        label=LABEL_RETENTION_DAYS,
        min_value=RETENTION_DAYS_MIN, max_value=RETENTION_DAYS_MAX)
    # `required=False` AND blank means NULL, which is the only way to
    # say "no age cliff" -- so this field can never be spelled `0`.
    queue_retention_days = forms.IntegerField(
        label=LABEL_QUEUE_RETENTION_DAYS, required=False,
        min_value=QUEUE_RETENTION_DAYS_MIN, max_value=QUEUE_RETENTION_DAYS_MAX)
    audit_detail = forms.BooleanField(label=LABEL_AUDIT_DETAIL, required=False)
```

- [ ] **Step 4: Teach `set_posture` the three fields**

In `identity/services.py`, add the sentinel and the bounds helper near the top:

```python
from identity.contracts.retention import (
    LABEL_QUEUE_RETENTION_DAYS, LABEL_RETENTION_DAYS, QUEUE_RETENTION_DAYS_MAX,
    QUEUE_RETENTION_DAYS_MIN, RETENTION_DAYS_MAX, RETENTION_DAYS_MIN,
)

# `None` IS A LEGAL VALUE for `queue_retention_days` -- it is how "no age
# cliff" is expressed -- so it cannot double as "the caller did not
# supply this field". One sentinel, named once.
_UNSET = object()


def _retention_int(value, *, label: str, low: int, high: int) -> int:
    """One bounded integer for the retention policy, or `ServiceRefused`.

    ONE RANGE CHECK, NOT TWO. `foundation.settings_bounds.
    exceeds_field_ceiling` exists for a field whose only bound is the
    COLUMN's -- a value large enough to overflow `PositiveIntegerField`
    reaching `.save()` as a `DataError` on a never-500 surface. These
    two fields have a real policy bound of 3650 days, which is nine
    orders of magnitude below that ceiling, so the range check below
    already refuses everything the ceiling check would have, with the
    same sentence. A second check that can never fire is a second thing
    to keep in agreement with the first.

    The bound is stated in exactly two places: here, where the refusal
    happens, and on the form field's `min_value`/`max_value`, which is
    the browser's own hint. Both read it from
    `identity/contracts/retention.py`; neither types a number.
    """
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ServiceRefused(f"{label}: {value!r} is not a number of days.") from exc
    if not low <= number <= high:
        raise ServiceRefused(f"{label}: {number} is outside the range {low}-{high}.")
    return number
```

extend the signature and docstring:

```python
def set_posture(actor, *, posture: str | None = None, library_posture: str | None = None,
                admin_sees_content: bool | None = None,
                session_idle_minutes: int | None = None,
                retention_days: int | None = None,
                queue_retention_days=_UNSET,
                audit_detail: bool | None = None,
                source: str = SOURCE_WEB) -> IdentitySettings:
```

and add this block after the `session_idle_minutes` block and before the `with transaction.atomic():`:

```python
    # THE RETENTION POLICY (spec section 4, owner ruling section 11.1):
    # three fields, ONE audit action, the literal column in `detail` --
    # `LIBRARY_SETTINGS_UPDATED`'s "one action per settings DOMAIN" rule,
    # rather than the three-way split the three security postures get.
    # Those three are semantically distinct postures; these three are one
    # retention policy expressed as three knobs.
    #
    # REFUSED BEFORE `.save()`, like every other bound on this row, and
    # the bounds live HERE rather than as database constraints
    # (`foundation/settings_bounds.py`'s own recorded rule).
    if retention_days is not None:
        days = _retention_int(retention_days, label=LABEL_RETENTION_DAYS,
                              low=RETENTION_DAYS_MIN, high=RETENTION_DAYS_MAX)
        if days != row.retention_days:
            row.retention_days = days
            events.append((actions.RETENTION_POLICY_CHANGED,
                           {"field": "retention_days", "to": days}))

    if queue_retention_days is not _UNSET:
        if queue_retention_days in (None, ""):
            # BLANK IS HOW "NO AGE CLIFF" IS SAID -- the FIFO
            # `retention_limit` alone then bounds the queue table.
            value = None
        elif str(queue_retention_days).strip() == "0":
            # REFUSED BEFORE THE RANGE CHECK, so the operator gets the
            # sentence that tells them what to do instead. `0` is inside
            # no legal range for this field -- `1` is the floor -- but
            # "outside the range 1-3650" would not explain that blank is
            # the way to say "keep them until the row limit bites".
            raise ServiceRefused(
                f"{LABEL_QUEUE_RETENTION_DAYS}: 0 is not a number of days. "
                "Leave it blank to keep finished jobs until the queue's own "
                "row limit removes them.")
        else:
            value = _retention_int(
                queue_retention_days, label=LABEL_QUEUE_RETENTION_DAYS,
                low=QUEUE_RETENTION_DAYS_MIN, high=QUEUE_RETENTION_DAYS_MAX)
        if value != row.queue_retention_days:
            row.queue_retention_days = value
            events.append((actions.RETENTION_POLICY_CHANGED,
                           {"field": "queue_retention_days", "to": value}))

    if audit_detail is not None and audit_detail != row.audit_detail:
        row.audit_detail = audit_detail
        events.append((actions.RETENTION_POLICY_CHANGED,
                       {"field": "audit_detail", "to": audit_detail}))
```

- [ ] **Step 5: Thread the three fields through the view and the page**

In `identity/views.py::settings_page`, extend the GET `initial` dict:

```python
        form = PostureForm(initial={
            "posture": row.posture,
            "library_posture": row.library_posture,
            "admin_sees_content": row.admin_sees_content,
            "session_idle_minutes": row.session_idle_minutes,
            "retention_days": row.retention_days,
            "queue_retention_days": row.queue_retention_days,
            "audit_detail": row.audit_detail,
        })
```

`set_posture(actor, **form.cleaned_data)` already forwards every cleaned key, so the POST path needs no change — but `queue_retention_days` cleans to `None` when blank, which the `_UNSET` sentinel correctly distinguishes from "absent".

In `identity/templates/identity/settings.html`, add a Retention block after the existing `admin_sees_content` field and before the submit row. It re-types no CSS — `.field`, `.checkbox-field`, `.helptext`, `.errorlist` are already declared in this leaf's own `extra_style`:

```html
    <div class="field" id="retention">
      <label for="{{ form.retention_days.id_for_label }}">{{ form.retention_days.label }}</label>
      {{ form.retention_days }}
      <p class="helptext">Days. A deleted conversation, document, question or image stays on the Deleted page for this long, and can be restored from it at any point. Zero removes it straight away. Changing this governs future deletions only — anything already deleted keeps the date it was given.</p>
      {% if form.retention_days.errors %}<ul class="errorlist">{% for error in form.retention_days.errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
    </div>
    <div class="field" id="queue-retention">
      <label for="{{ form.queue_retention_days.id_for_label }}">{{ form.queue_retention_days.label }}</label>
      {{ form.queue_retention_days }}
      <p class="helptext">Days. Finished jobs older than this are removed from the Queue page. Leave it blank to keep them until the queue's own row limit removes them.</p>
      {% if form.queue_retention_days.errors %}<ul class="errorlist">{% for error in form.queue_retention_days.errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
    </div>
    <div class="field checkbox-field" id="audit-detail">
      {{ form.audit_detail }}
      <label for="{{ form.audit_detail.id_for_label }}">{{ form.audit_detail.label }}</label>
    </div>
```

- [ ] **Step 6: Extend the Identity & security help card**

In `foundation/settings_help.py`, add three `HelpField`s to the `route_name="identity-settings"` card, with anchors `retention`, `queue-retention` and `audit-detail` — matching the three `id=`s above, because `foundation/tests/test_settings_help.py::TestTheAnchors` renders the page and asserts every cited anchor exists. Their `meaning`/`effects` say what the fields do in plain words, and say that **the removal date is fixed at delete time so a changed setting governs future deletions only**, and that **backups are a separate layer that the deletion date does not reach**. They say nothing about holds or records obligations — none are built.

- [ ] **Step 7: Re-pin the existing posture-form tests**

Run `grep -rn "identity-settings\|PostureForm" identity/tests foundation/tests` and add `"retention_days": "30"` (plus `"queue_retention_days": "1"` where the test asserts a round trip) to every POST body. Nothing else about those tests changes.

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/pytest -q identity foundation/tests/test_settings_help.py`
Expected: PASS.

- [ ] **Step 9: Run the full gate and commit**

```bash
git add identity/forms.py identity/services.py identity/views.py \
        identity/templates/identity/settings.html foundation/settings_help.py \
        identity/tests/
git commit -m "feat(identity): one Retention section for the whole policy

Re-pins identity/tests/test_settings_page.py and identity/tests/test_services.py: every posture-form POST body now carries the three retention fields."
```

---

### Task 8: `agents/` — the exclusion, and a delete that tickets

**Files:**
- Modify: `agents/visibility.py` (`visible_conversations`, `delete_conversation`)
- Modify: `agents/chat/views/conversations.py` (`conversation_delete`'s notice)
- Modify: `agents/chat/templates/chat/_thread_actions.html`, `agents/chat/templates/chat/_sidebar_row.html` (the confirm copy)
- Modify: `agents/README.md`
- Test: `agents/chat/tests/test_visibility.py` (extend), `agents/chat/tests/test_delete.py` (re-pin + extend)

**Held tests — read each before editing, and name each in the commit message:**
- `agents/chat/tests/test_delete.py::TestTheRowsSurviveADelete::test_the_conversation_and_its_turns_survive_and_a_ticket_hides_them` — pins that the rows are gone the instant the POST returns. **Re-pin:** the rows survive; the conversation is absent from `visible_conversations` and a `DeletionTicket` exists.
- `::TestTheRowsSurviveADelete::test_the_index_shows_a_deleted_notice` — the notice text changes to name the Deleted page.
- `::TestAttachmentRowsSurviveADelete::test_deleting_a_conversation_leaves_its_attachment_rows` and `::test_a_different_conversations_attachment_row_is_untouched` — the attachment teardown moves from delete time to **purge** time (Task 9). **Re-pin:** these assert the rows survive a soft delete and go on purge; move the purge half into Task 9's own test module and leave the soft-delete half here.
- `::TestConversationDeleteCascadesChatScopedDocuments::test_a_chat_scoped_documents_delete_document_is_called` and `::test_a_universal_documents_attachment_row_is_removed_but_the_document_survives` — same move, same reason.
- `::TestTheCleanupSavepoint::test_a_database_error_in_cleanup_does_not_block_the_delete` — the cleanup provider no longer runs inside `delete_conversation`. **Re-pin:** move it to Task 9's module, against `purge_conversation`.
- `::TestTheAuditSurvives::*` — unchanged and must stay green: a soft delete still touches no `ToolInvocation`.
- `::TestTheDeleteControlOnThePage::test_the_page_carries_a_details_confirm_not_a_js_dialog` — unchanged; the copy changes, the mechanism does not.
- `agents/chat/tests/test_visibility.py::TestDeleteConversation::test_the_owner_may_delete_and_a_stranger_may_not` and `::test_an_admin_with_the_content_setting_off_may_not_delete_somebody_elses`, `::TestServiceOwnedRows::test_an_admin_may_delete_a_conversation_a_shell_path_made`, `::TestSharingAConversation::test_deleting_a_conversation_deletes_its_shares` — the first three keep their True/False answers unchanged (the gate is still `may_manage_conversation`); the last one's shares now go at **purge**, so it moves to Task 9.
- `::TestTheOpenBranchIsFirst::test_an_open_box_asks_the_user_table_nothing` — asserts `django_assert_num_queries(2)` (the settings singleton read plus the list). `ticketed_keys` adds exactly one query to `visible_conversations` on every box, including an open one. **Re-pin: `2` → `3`**, and extend that test's docstring by one sentence — the third read is `identity_deletionticket`, which is still not `identity_user`, so the claim the test's NAME makes is unchanged.
- `::TestTheOpenBranchIsFirst::test_an_open_box_builds_no_ownership_filter_at_all` — stands unchanged: the exclusion is not an ownership filter, and this test inspects the queryset's `Q` structure rather than counting queries. Re-run it and confirm before assuming.

**Interfaces:**
- Consumes: `identity.retention.ticketed_keys`, `delete_content` (Task 5); `identity.contracts.retention.KIND_CONVERSATION` (Task 1).
- Produces: `agents.visibility.delete_conversation(principal, conversation) -> DeletionTicket | None` — unchanged signature, now hands back the `DeletionTicket` it wrote (`None` when `principal` may not delete), so a truthy check still reads as "did it go" for every existing caller, and `conversation_delete` can read `ticket.purge_on` to choose the honest notice (controller addition, Task 8 review — see Step 6).

- [ ] **Step 1: Write the failing tests**

```python
# at the top of agents/chat/tests/test_visibility.py, beside the
# existing imports -- the equality-under-scale pin below needs them
from django.db import connection
from django.test.utils import CaptureQueriesContext


# append to agents/chat/tests/test_visibility.py
class TestTicketedConversationsAreInvisible:
    """The exclusion goes on the BASE queryset, BEFORE the
    `sees_all_content` early return -- that branch is EVERY principal on
    an open box, which is the posture most boxes run."""

    def test_a_ticketed_conversation_is_hidden_from_sees_all_content_too(self):
        with posture("open"):
            principal = user_principal(make_user())
            conversation = make_conversation()
            assert delete_conversation(principal, conversation) is True
            assert list(visible_conversations(principal)) == []

    def test_it_is_hidden_from_its_own_owner_in_the_personal_posture(self):
        with posture("personal"):
            user = make_user()
            principal = user_principal(user)
            conversation = make_conversation(
                owner_kind="user", owner_key=str(user.pk))
            delete_conversation(principal, conversation)
            assert list(visible_conversations(principal)) == []

    def test_an_untouched_conversation_is_still_visible(self):
        with posture("open"):
            principal = user_principal(make_user())
            kept = make_conversation()
            delete_conversation(principal, make_conversation())
            assert list(visible_conversations(principal)) == [kept]

    def test_the_cost_is_flat_in_the_number_of_conversations(self):
        """EQUALITY UNDER SCALE, never an absolute count: one row against
        twenty-five, with tickets present both times. `ticketed_keys` is
        ONE query whatever the number of conversations or tickets, and a
        literal here would drift with unrelated work -- the shape
        `test_thread.py` and `test_sidebar.py` already use.
        """
        with posture("open"):
            principal = user_principal(make_user())
            delete_conversation(principal, make_conversation())
            make_conversation()
            list(visible_conversations(principal))  # warm up

            with CaptureQueriesContext(connection) as small:
                list(visible_conversations(principal))
            for _ in range(25):
                make_conversation()
            with CaptureQueriesContext(connection) as large:
                list(visible_conversations(principal))

        assert len(large.captured_queries) == len(small.captured_queries)
```

```python
# append to agents/chat/tests/test_delete.py
class TestDeleteWritesATicketAndHidesTheThread:
    """Delete is now a PROMISE, not an erasure: the rows survive until
    the date the Deleted page prints."""

    def test_the_rows_survive_and_the_thread_vanishes_from_every_surface(self, client):
        conversation = _a_conversation_with_a_turn()
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        assert Conversation.objects.filter(pk=conversation.pk).exists()
        assert Turn.objects.filter(conversation_id=conversation.pk).exists()
        assert list(visible_conversations(OPEN_PRINCIPAL)) == []

    def test_one_ticket_carries_the_title_and_the_promised_date(self, client):
        conversation = _a_conversation_with_a_turn()
        conversation.title = "A thread"
        conversation.save(update_fields=["title"])
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        ticket = DeletionTicket.objects.get()
        assert (ticket.kind, ticket.key) == ("conversation", str(conversation.pk))
        assert ticket.label == "A thread"
        assert ticket.purge_on == timezone.localdate() + datetime.timedelta(days=30)

    def test_the_notice_names_where_the_thread_went(self, client):
        conversation = _a_conversation_with_a_turn()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]), follow=True)
        body = response.content.decode()
        assert "Conversation deleted. You can restore it from Settings → Deleted." in body

    def test_a_second_delete_of_the_same_thread_is_a_404_not_a_second_ticket(self, client):
        conversation = _a_conversation_with_a_turn()
        client.post(reverse("chat-conversation-delete", args=[conversation.id]))
        second = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]))
        assert second.status_code == 404
        assert DeletionTicket.objects.count() == 1

    def test_the_notice_names_permanent_deletion_when_the_grace_period_is_zero(
        self, client
    ):
        """CONTROLLER ADDITION (Task 8 review): the notice must not
        promise a restore the box cannot keep. With `retention_days = 0`
        the ticket `delete_content` hands back has ALREADY been purged
        by the time the view runs -- its `purge_on` is today
        (`identity.retention.delete_content`'s own unconditional bounded
        sweep) -- so the notice reads "Conversation deleted
        permanently." instead of naming a restore door that is already
        closed."""
        row = IdentitySettings.get_solo()
        row.retention_days = 0
        row.save()

        conversation = _a_conversation_with_a_turn()
        response = client.post(
            reverse("chat-conversation-delete", args=[conversation.id]), follow=True)

        body = response.content.decode()
        assert "Conversation deleted permanently." in body
        assert "Conversation deleted. You can restore it from Settings → Deleted." not in body
```

`_a_conversation_with_a_turn` is that module's existing helper — reuse it by name; do not add a second one.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q agents/chat/tests/test_delete.py agents/chat/tests/test_visibility.py`
Expected: FAIL — the new classes fail, and the held tests listed above fail because the rows are still hard-deleted. That is the signal to re-pin them in Step 5.

- [ ] **Step 3: Add the exclusion to `visible_conversations`**

In `agents/visibility.py`, import the seam at module top:

```python
from identity.contracts.retention import KIND_CONVERSATION
from identity.retention import delete_content, ticketed_keys
```

and change the first two lines of `visible_conversations`' body:

```python
    # THE EXCLUSION GOES ON THE BASE QUERYSET, BEFORE the
    # `sees_all_content` early return below -- and that ordering is the
    # single most important mechanical detail here. `sees_all_content` is
    # True for EVERY principal on an open box, the posture the majority of
    # boxes run; an exclusion bolted onto the restricted leg alone would
    # leave deleted conversations fully visible in exactly the posture
    # where it matters most.
    #
    # `ticketed_keys` is ONE query returning a materialised list of key
    # STRINGS -- `agents.shares.shared_keys`' own reasoning, and the
    # reason it is a list rather than a `Subquery`: the ticket's `key` is
    # text and `Conversation.pk` is a UUID, so a subquery would need a
    # cast and would be a silent type mismatch waiting to happen.
    qs = Conversation.objects.select_related("agent").exclude(
        pk__in=ticketed_keys(KIND_CONVERSATION))
    if sees_all_content(principal, settings_row=settings_row):
        return qs.all()
```

The rest of the function is untouched — the `owned`/`Share`/workstream legs keep filtering the already-narrowed `qs`.

**Not excluded, deliberately:** `may_manage_conversation` and its siblings. A ticketed row is unreachable because `agents/chat/service.py::visible_conversation_or_404` resolves through `visible_conversations`; a second check in the predicate would be a second place for the rule to live.

- [ ] **Step 4: Make `delete_conversation` ticket**

Replace the body (keep the signature and update the docstring):

```python
def delete_conversation(principal, conversation) -> bool:
    """Delete `conversation` if `principal` may. True if it went.

    IT TICKETS; IT DOES NOT ERASE. `identity.retention.delete_content`
    writes one `DeletionTicket`, records a content-free
    `content.deleted` event, and runs a bounded sweep. The conversation,
    its turns, its shares, its attachment claims, its chat-scoped
    documents, its generated images and its queue rows all survive until
    the date the Deleted page prints -- and are then destroyed together
    by `agents.retention.purge_conversation` and its sibling handlers,
    through the registry `identity/cascades.py` runs.

    WHY THE TEARDOWN MOVED OUT OF HERE. It had to: a conversation's
    queue rows carry the person's literal message in
    `InferenceJob.payload["text"]`, and `agents/` may not import
    `models.queue` (import-law rule 2, pinned by
    `foundation/ops/tests/test_import_law.py`'s `FORBIDDEN_MODULES`).
    No delete written in this column could ever reach them. The
    orchestration therefore lives in `identity/`, below every column,
    and this function's whole job is now the gate and the ticket.

    THE GATE IS UNCHANGED -- `may_manage_conversation`, with its
    service-owned-row ruling intact. Still no `post_delete` receiver:
    this repository uses no Django signals anywhere, and a delete that
    must be explicit, counted and audited is the last place to start.
    """
    if not may_manage_conversation(principal, conversation):
        return False
    delete_content(principal, kind=KIND_CONVERSATION, key=conversation.pk,
                   owner=conversation, label=conversation.title or "")
    return True
```

The `Share` delete and the `delete_attachments_for` call move to `agents/retention.py` in Task 9 — do not leave copies here.

- [ ] **Step 5: Re-pin the held tests**

Work through the list in this task's header. Each one either (a) changes its assertion from "the row is gone" to "the row survives and the thread is invisible", or (b) moves wholesale into `agents/tests/test_retention.py`, which Task 9 creates. For (b), leave a one-line comment at the old site naming the new home, the way this codebase does elsewhere.

- [ ] **Step 6: Change the affordance copy and the notice**

`agents/chat/views/conversations.py::conversation_delete` — update its docstring (the turns no longer go with it at delete time; the audit asymmetry paragraph moves to `agents/retention.py`'s scrub) and the notice. **THE NOTICE MUST NOT PROMISE A RESTORE THE BOX CANNOT KEEP (controller addition, Task 8 review):** `delete_conversation` now hands back the `DeletionTicket` it wrote, and with `retention_days = 0` that ticket has already been purged by the time this view runs (its `purge_on` is today — `identity.retention.delete_content`'s own unconditional bounded sweep). The view reads `ticket.purge_on` to choose between the two sentences rather than printing one unconditionally:

```python
    ticket = delete_conversation(principal, conversation)
    if not ticket:
        raise Http404(f"Conversation {conversation_id} does not exist.")
    if ticket.purge_on <= timezone.localdate():
        messages.info(request, "Conversation deleted permanently.")
    else:
        messages.info(
            request, "Conversation deleted. You can restore it from Settings → Deleted.")
```

`_thread_actions.html` and `_sidebar_row.html` — replace the confirm sentence in both. The two say the same thing because they are the same affordance in two places:

```html
        <span class="muted">Delete this conversation? It moves to Settings → Deleted,
          where you can restore it until the date shown there.</span>
```

Update each fragment's surrounding `{% comment %}` to say the same, and drop the now-wrong "Its tool-call audit trail is kept" line — after a purge the tool-call records' words are blanked and only the content-free shell survives, which Task 9 documents in `agents/README.md`.

- [ ] **Step 7: Document it in `agents/README.md`**

One short section: `delete_conversation` tickets, `agents/retention.py` purges, the new artifact-purge slot, and the tool-record scrub's rule — **scrubbed inline with the conversation, always**, no setting, no second date.

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/pytest -q agents identity`
Expected: PASS.

- [ ] **Step 9: Run the full gate and commit**

```bash
git add agents/visibility.py agents/chat/views/conversations.py \
        agents/chat/templates/chat/_thread_actions.html \
        agents/chat/templates/chat/_sidebar_row.html agents/README.md agents/chat/tests/
git commit -m "feat(agents): deleting a conversation writes a ticket, not an erasure

Re-pins agents/chat/tests/test_delete.py::TestTheRowsSurviveADelete, ::TestAttachmentRowsSurviveADelete, ::TestConversationDeleteCascadesChatScopedDocuments, ::TestTheCleanupSavepoint and agents/chat/tests/test_visibility.py::TestTheOpenBranchIsFirst::test_an_open_box_asks_the_user_table_nothing (2 queries become 3, the third being the ticket read), ::TestSharingAConversation::test_deleting_a_conversation_deletes_its_shares."
```

---

### Task 9: `agents/retention.py` — the conversation purge, the scrub, the artifact slot

**Files:**
- Create: `agents/retention.py`
- Modify: `agents/contracts/artifacts.py` (`register_artifact_purge`, `artifact_purge`)
- Modify: `agents/apps.py` (register the FILES-band handler)
- Test: `agents/tests/test_retention.py` (new), `agents/contracts/tests/test_artifacts.py` (extend)

**Interfaces:**
- Consumes: `identity.contracts.cascades.RetentionHandler`/`register_retention_handler`/`ORDER_FILES` (Task 1); `agents.attachments.delete_attachments_for`; `agents.models.Conversation`/`Turn`/`ToolInvocation`/`Share`; `agents.contracts.artifacts.parse_artifact`.
- Produces:
  - `agents.contracts.artifacts.register_artifact_purge(dotted_path: str) -> None`, `artifact_purge() -> str | None` — handler signature `(refs: Sequence[str], generation_ids: Sequence[str]) -> int`
  - `agents.retention.purge_conversation(key: str) -> int`
  - `agents.retention.scrub_tool_records(invocation_ids) -> int`
  - registration: `RetentionHandler(kind="conversation", key="agents.conversation", label="Conversation and turns", handler="agents.retention.purge_conversation", order=ORDER_FILES)`

- [ ] **Step 1: Write the failing tests**

```python
# agents/tests/test_retention.py
"""`agents.retention.purge_conversation` -- the agents-side teardown,
plus the two channels that find a conversation's generated images."""
from __future__ import annotations

import uuid

import pytest

from agents.contracts import artifacts as artifacts_module
from agents.contracts.artifacts import artifact_purge, register_artifact_purge
from agents.models import Conversation, Share, ToolInvocation, Turn
from agents.retention import purge_conversation, scrub_tool_records
from agents.tests._helpers import make_agent, make_conversation, make_turn

pytestmark = pytest.mark.django_db

SEEN: list[tuple[tuple, tuple]] = []


def fake_artifact_purge(refs, generation_ids) -> int:
    SEEN.append((tuple(refs), tuple(generation_ids)))
    return len(set(refs)) + len(set(generation_ids))


@pytest.fixture(autouse=True)
def _isolated_slot():
    """SAVE, SET, RESTORE -- the single artifact-purge slot is a
    module-level global with no reset path, exactly like the cascade
    registries `identity/tests/test_cascades.py::_isolated_registry`
    protects, and for the same reason: a fake left in place here would
    be resolved by every later conversation purge in the same pytest
    process -- `identity/tests/test_deletion_demo.py`'s, the Deleted
    page's, the route matrix's owner column -- and the suite runs in
    BOTH collection orders, so ordering luck cannot cover it.

    On a box with "vision" off the saved value is `None`, which restores
    correctly too.
    """
    saved = artifacts_module._ARTIFACT_PURGE
    SEEN.clear()
    register_artifact_purge(f"{__name__}.fake_artifact_purge")
    yield
    SEEN.clear()
    artifacts_module._ARTIFACT_PURGE = saved


class TestTheRowsGo:
    def test_the_conversation_its_turns_and_its_shares_are_removed(self):
        conversation = make_conversation()
        make_turn(conversation=conversation)
        Share.objects.create(target_type=Share.Target.CONVERSATION,
                             target_key=str(conversation.pk), level="view")
        removed = purge_conversation(str(conversation.pk))
        assert not Conversation.objects.filter(pk=conversation.pk).exists()
        assert not Turn.objects.filter(conversation_id=conversation.pk).exists()
        assert not Share.objects.filter(target_key=str(conversation.pk)).exists()
        assert removed > 0

    def test_it_is_idempotent_on_a_conversation_that_is_already_gone(self):
        """The contract obligation every retention handler owes: a
        re-run after a mid-purge failure COMPLETES rather than raises."""
        conversation = make_conversation()
        purge_conversation(str(conversation.pk))
        assert purge_conversation(str(conversation.pk)) == 0

    def test_an_unparseable_key_removes_nothing_and_does_not_raise(self):
        assert purge_conversation("not-a-uuid") == 0


class TestTheToolRecordScrub:
    def test_the_words_go_and_the_shell_stays(self):
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="rag__search",
            args={"query": "a secret"}, text="the result the model saw",
            error="", outcome="ok")
        make_turn(conversation=conversation, role="tool",
                  invocation_id=invocation.pk)
        purge_conversation(str(conversation.pk))
        invocation.refresh_from_db()
        assert invocation.args == {}
        assert invocation.text == ""
        assert invocation.error == ""
        # The machine audit trail survives, principal and outcome intact.
        assert invocation.principal_key == "1"
        assert invocation.tool_key == "rag__search"
        assert invocation.outcome == "ok"

    def test_the_ids_are_collected_before_the_turns_are_deleted(self):
        """`Turn.invocation` is SET_NULL, so after the delete there is no
        path from the conversation to its invocations at all. Collecting
        first is what makes the scrub reachable, and this is the test
        that would fail if somebody reordered it."""
        conversation = make_conversation()
        invocation = ToolInvocation.objects.create(
            principal_kind="user", principal_key="1", tool_key="t",
            args={"a": 1}, text="x", outcome="ok")
        make_turn(conversation=conversation, role="tool",
                  invocation_id=invocation.pk)
        purge_conversation(str(conversation.pk))
        invocation.refresh_from_db()
        assert invocation.args == {}

    def test_scrub_of_an_empty_id_list_is_a_no_op(self):
        assert scrub_tool_records([]) == 0


class TestFindingTheGeneratedImages:
    def test_output_and_input_references_are_collected_and_deduped(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", index=0,
                  artifacts=["output:1", "output:2"])
        make_turn(conversation=conversation, role="tool", index=1,
                  artifacts=["output:1", "input:9"])
        purge_conversation(str(conversation.pk))
        refs, _ids = SEEN[0]
        assert sorted(set(refs)) == ["input:9", "output:1", "output:2"]

    def test_a_document_reference_is_not_handed_to_the_image_column(self):
        """`document:<id>` artifacts are `Document` rows the attachment
        seam already reaches."""
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool",
                  artifacts=["document:451", "output:3"])
        purge_conversation(str(conversation.pk))
        refs, _ids = SEEN[0]
        assert list(refs) == ["output:3"]

    def test_an_unparseable_reference_is_dropped_and_never_raises(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool",
                  artifacts=["output:12:extra", "", "output:4"])
        assert purge_conversation(str(conversation.pk)) >= 0
        refs, _ids = SEEN[0]
        assert list(refs) == ["output:4"]

    def test_a_generation_id_is_collected_from_a_job_that_FAILED(self):
        """THE STEWARD'S GAP, PINNED AS A TEST. A job that reached the
        engine and failed mints no `GeneratedOutput` at all, so the
        artifact channel finds nothing -- but `run_generate` reaches
        `job_json` on every terminal outcome, so the tool turn carries
        `data["id"]`. This is the only thing that catches it."""
        conversation = make_conversation()
        job_id = str(uuid.uuid4())
        make_turn(conversation=conversation, role="tool", artifacts=[],
                  data={"id": job_id, "status": "failed", "error": "out of memory"})
        purge_conversation(str(conversation.pk))
        _refs, ids = SEEN[0]
        assert list(ids) == [job_id]

    def test_a_turn_with_no_data_contributes_nothing(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="assistant", data=None)
        purge_conversation(str(conversation.pk))
        _refs, ids = SEEN[0]
        assert list(ids) == []

    def test_data_that_is_not_a_dict_or_whose_id_is_not_a_uuid_is_ignored(self):
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", index=0, data=["a", "list"])
        make_turn(conversation=conversation, role="tool", index=1,
                  data={"id": "not-a-uuid"})
        purge_conversation(str(conversation.pk))
        _refs, ids = SEEN[0]
        assert list(ids) == []

    def test_with_no_slot_registered_the_purge_still_completes(self):
        """Safe to clear here: `_isolated_slot` restores whatever the
        real `tools/vision/apps.py::ready()` registered."""
        artifacts_module._ARTIFACT_PURGE = None
        conversation = make_conversation()
        make_turn(conversation=conversation, role="tool", artifacts=["output:1"])
        assert purge_conversation(str(conversation.pk)) >= 0
```

```python
# append to agents/contracts/tests/test_artifacts.py
from agents.contracts import artifacts as artifacts_module


@pytest.fixture(autouse=True)
def _isolated_slot():
    """This module writes the single artifact-purge global, so it saves
    and restores it -- the same discipline every registry test in this
    codebase follows (`identity/tests/test_cascades.py::
    _isolated_registry`). Without it, `"pkg.other.fn"` below would be
    resolved by the next conversation purge in the same process."""
    saved = artifacts_module._ARTIFACT_PURGE
    yield
    artifacts_module._ARTIFACT_PURGE = saved


class TestTheArtifactPurgeSlot:
    def test_it_is_one_slot_not_a_per_kind_dict(self):
        register_artifact_purge("pkg.mod.fn")
        assert artifact_purge() == "pkg.mod.fn"
        register_artifact_purge("pkg.other.fn")
        assert artifact_purge() == "pkg.other.fn"

    def test_it_refuses_a_path_that_is_not_dotted(self):
        with pytest.raises(ValueError, match="dotted path"):
            register_artifact_purge("notdotted")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q agents/tests/test_retention.py agents/contracts/tests/test_artifacts.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents.retention'`.

- [ ] **Step 3: Add the one slot to `agents/contracts/artifacts.py`**

After `file_resolver_for`:

```python
# ONE SLOT, NOT A PER-KIND DICT, matching
# `agents.contracts.attachments.register_attachment_cleanup`'s own
# single-slot shape for the identical situation: the agents column
# COMPUTES values -- artifact references and generation ids -- and
# exactly one tool column knows what they mean. `tools/rag` needs no
# registration here, because a `document:<id>` artifact is a `Document`
# row the attachment seam already reaches.
_ARTIFACT_PURGE: str | None = None


def register_artifact_purge(dotted_path: str) -> None:
    """Register the function that destroys the rows and bytes behind a
    conversation's artifact references.

    Signature `(refs: Sequence[str], generation_ids: Sequence[str]) ->
    int` -- ONE MODE, like every retention handler, and for the reason
    `identity.contracts.cascades.RetentionHandler` gives: a deletion's
    confirmation is the Deleted page, not a number.

    A DOTTED PATH, resolved at purge time by the caller, never imported
    here -- `agents/` may not import `tools/` at all.
    """
    if "." not in dotted_path:
        raise ValueError(
            f"register_artifact_purge needs a dotted path, got {dotted_path!r}")
    global _ARTIFACT_PURGE
    _ARTIFACT_PURGE = dotted_path


def artifact_purge() -> str | None:
    """The registered purge path, or `None` when nothing is -- which is
    the common case on a box with the image column uninstalled, and is
    not an error."""
    return _ARTIFACT_PURGE
```

- [ ] **Step 4: Write `agents/retention.py`**

```python
"""What `agents/` destroys when a deleted conversation's date arrives.

REGISTERED, NEVER CALLED DIRECTLY. `agents/apps.py::ready()` registers
the dotted path; `identity/cascades.py::run_retention` resolves it
inside `identity.retention.purge_ticket`'s transaction, in a savepoint
of its own. `identity/` never imports this module, and this module never
imports `tools/` or `models.queue` -- both are reached by registration,
which is what makes the whole purge one transaction across four columns
that may not import each other.

THE FILES BAND, not the rows band, and deliberately: this handler must
READ the conversation's turns (their artifacts and their tool payloads)
before it deletes them, AND it reaches bytes on disk through the
registered artifact purge. A handler that must do both registers in the
FILES band and orders its own reads before its own writes internally --
which keeps the registry's ordering rule to one field with two values
instead of a general dependency graph nothing else needs.
"""
from __future__ import annotations

import logging
import uuid

from agents.contracts.artifacts import artifact_purge, parse_artifact
from agents.models import Conversation, Share, ToolInvocation, Turn

logger = logging.getLogger(__name__)

# The artifact kinds this column hands to the image column. `document`
# is deliberately absent: a `document:<id>` reference names a `Document`
# row, and the attachment seam already reaches those.
_IMAGE_KINDS = ("output", "input")


def _collect(conversation_id):
    """Everything the purge needs to read BEFORE anything is deleted.

    ONE `values_list` over the conversation's turns. Three things come
    out of it, and the order matters for the third:

    * artifact references parsing as `output:<id>` / `input:<id>` --
      each is one FK hop from its generation job, and several outputs
      share one job, so the mapping dedupes by job on the other side;
    * generation ids: `data["id"]` for any turn whose `data` is a dict
      whose `"id"` parses as a UUID. `agents/runtime/loop.py` writes
      `data=outcome.result.data` verbatim onto a tool turn, and for the
      image tool that data IS `job_json(job)`, whose first key is the
      job id -- so this channel names the job EVEN WHEN IT PRODUCED NO
      OUTPUT AT ALL, which is precisely the failed-job case the
      artifact channel cannot see;
    * `invocation_id` values, non-null. Collected HERE because
      `Turn.invocation` is `SET_NULL`: after the turns are deleted there
      is no path from the conversation to its tool records at all. That
      `SET_NULL` is deliberate (the 2026-08-27 addendum's consequence 3
      -- an audit row is not owned by the conversation table) and is not
      being changed.

    An unparseable artifact reference is DROPPED AND LOGGED, never
    raised -- `agents.shares.shared_keys`' own posture for untrusted
    stored strings: a purge must not be stopped by one bad row.

    THE LOG LINE NAMES NO CONTENT (Task 9 fix round 1, FIX M6): a
    deletion path must not write the very bytes it is destroying into a
    log a purge is supposed to make disappear, so this logs the TURN's
    own id and the conversation id -- enough for an operator to find the
    row by hand -- and the fact that its reference did not parse, never
    the raw stored string itself.
    """
    refs: list[str] = []
    generation_ids: list[str] = []
    invocation_ids: list[int] = []
    rows = Turn.objects.filter(conversation_id=conversation_id).values_list(
        "id", "artifacts", "data", "invocation_id")
    for turn_id, artifacts, data, invocation_id in rows:
        for reference in artifacts or ():
            try:
                kind, _pk = parse_artifact(reference)
            except ValueError:
                logger.warning(
                    "agents.retention: turn %s (conversation %s) carries an "
                    "artifact reference that failed to parse; ignored.",
                    turn_id, conversation_id)
                continue
            if kind in _IMAGE_KINDS:
                refs.append(reference)
        if isinstance(data, dict):
            raw = data.get("id")
            if isinstance(raw, str):
                try:
                    uuid.UUID(raw)
                except (ValueError, AttributeError, TypeError):
                    pass
                else:
                    generation_ids.append(raw)
        if invocation_id is not None:
            invocation_ids.append(invocation_id)
    return refs, generation_ids, invocation_ids


def scrub_tool_records(invocation_ids) -> int:
    """Blank the words on these `ToolInvocation` rows, keep the shell.

    `args`, `text` and `error` are content -- the tool arguments, the
    result the model saw, the failure it reported. Everything else stays:
    principal, agent slug, tool key, outcome, timings, `queue_job_id`.
    That shell IS the machine audit trail
    `identity/contracts/actions.py`'s own docstring points at when it
    explains why tool calls are absent from the `AuditEvent` catalogue
    -- "tool calls already have a better record in
    `agents.models.ToolInvocation`... the first must be kept and the
    second must be prunable". Scrubbing the words and keeping the record
    is that sentence, implemented.

    ONE CLIFF, NO SECOND DATE, NO SETTING. This runs inline, inside the
    purge's own transaction, on the date the Deleted page printed. The
    owner asked for an optional longer retention for these records and
    withdrew it (spec section 1 ruling 1, section 11.8); there is
    nothing here that defers.

    Idempotent by construction: a second run matches the same rows and
    writes the same three empty values.

    A KNOWN, ACCEPTED RESIDUE (Task 9 fix round 1, FIX M7/I4): this only
    reaches invocations a SURVIVING `Turn` points at, because `_collect`
    above finds them by walking the conversation's own turns. A tool
    call whose job died between its `ToolInvocation` row being written
    (`agents/runtime/invoke.py`) and its tool `Turn` being written
    (`agents/runtime/loop.py`) has no turn and therefore no conversation
    link at all -- its `args`/`text` are not reachable by ANY
    conversation's purge, on this or any other kind. That is a known,
    accepted gap with no reaper today, not a bug this function is
    expected to close.
    """
    ids = list(invocation_ids)
    if not ids:
        return 0
    return ToolInvocation.objects.filter(pk__in=ids).update(
        args={}, text="", error="")


def purge_conversation(key: str) -> int:
    """Destroy one conversation's agents-side content. Returns how many
    rows were removed and records scrubbed.

    IDEMPOTENT: every step is a filtered delete or update, so a re-run
    after a partial failure removes zero rows and returns zero rather
    than raising. A conversation that is already gone -- or a `key` that
    is not a UUID at all -- is not an error: the item may have been hard
    -deleted by an older path while its ticket stood.

    ORDER, and every step's reason -- BYTES LAST (Task 9 fix round 1,
    FIX I2: the registered artifact-purge slot used to run SECOND, right
    after collect, which meant a later step raising -- the attachment
    cleanup, say -- rolled the ROW deletes back while the files that
    slot had already removed from disk stayed gone; a filesystem delete
    has no rollback):
      1. collect (see `_collect`) -- nothing is deleted yet;
      2. the row deletes: this conversation's `Share` rows, then
         `agents.attachments.delete_attachments_for` (which reaches
         `tools.rag.access.delete_attachments` through the registered
         cleanup seam -- chat-scoped documents die there, universal and
         stream-contained ones lose only their claim; a failure here now
         PROPAGATES -- FIX C1 -- rather than degrading to zero), then
         `conversation.delete()`, with `Turn` going by CASCADE;
      3. the tool-record scrub, on the ids from step 1;
      4. LAST: hand the references and ids to the registered artifact
         purge, which maps them to jobs, dedupes by job, and deletes
         each job with its files and its queue row. If step 2 or 3
         raises, this step never runs at all -- every ROW this function
         owns is already gone before a single FILE on disk is touched.
    """
    try:
        conversation_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0

    refs, generation_ids, invocation_ids = _collect(conversation_id)

    removed = 0

    removed += Share.objects.filter(
        target_type=Share.Target.CONVERSATION,
        target_key=str(conversation_id)).delete()[0]

    from agents.attachments import delete_attachments_for

    removed += delete_attachments_for(conversation_id)

    deleted, _by_model = Conversation.objects.filter(pk=conversation_id).delete()
    removed += deleted

    removed += scrub_tool_records(invocation_ids)

    purge = artifact_purge()
    if purge is not None:
        from django.utils.module_loading import import_string
        removed += import_string(purge)(refs, generation_ids)

    return removed
```

- [ ] **Step 5: Register the handler in `agents/apps.py`**

Inside `ready()`, after the three `register_entitlement_cascade` calls:

```python
        # THIS COLUMN'S ANSWER TO "A DELETED CONVERSATION'S DATE HAS
        # ARRIVED", as a DOTTED-PATH STRING so `identity/` can run it
        # without importing `agents/` (import-law rule 4) -- the same
        # mechanism, and the same reason, as the entitlement cascades
        # just above. FILES band: the handler reads the turns' artifacts
        # before it deletes them and reaches bytes through the registered
        # artifact purge.
        from identity.contracts.cascades import (
            ORDER_FILES, RetentionHandler, register_retention_handler,
        )
        from identity.contracts.retention import KIND_CONVERSATION

        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION,
            key="agents.conversation",
            label="Conversation and turns",
            handler="agents.retention.purge_conversation",
            order=ORDER_FILES,
        ))
```

- [ ] **Step 6: Move the held tests that belong here**

Bring the four moved classes from `agents/chat/tests/test_delete.py` (Task 8's list) into `agents/tests/test_retention.py`, rewritten against `purge_conversation` rather than the view: the attachment rows go, a different conversation's attachment row is untouched, a chat-scoped document's `delete_document` is called, a universal document survives with its claim removed, the shares go, and **a database error inside the cleanup provider does not poison the outer transaction** (`TestTheCleanupSavepoint`; the savepoint is still `delete_attachments_for`'s own).

**Fix round 1 (Task 9 review), amended here:** `TestTheCleanupSavepoint`'s OUTCOME flipped, not its mechanism (FIX C1). `delete_attachments_for`'s only production caller is this module's own `purge_conversation`, running at purge time inside `identity.cascades.run_retention`'s never-swallows runner — so a broken cleanup provider must no longer be swallowed into a completed purge; it must propagate, taking the purge down so the ticket survives for the next sweep. The savepoint (`transaction.atomic()`, nested) stays exactly as before — it is still what keeps a DATABASE-level error from poisoning the outer transaction before the now-ordinary Python exception re-raises — only the `except` clause's outcome changed, from `return 0` to `logger.exception(...); raise`. Two tests now cover it: one driving a real database error directly through `purge_conversation` and asserting it raises, and one driving the same error through `identity.retention.purge_ticket` end to end and asserting the Conversation row, its turns, its attachment rows and the DeletionTicket itself all survive, no `content.purged` event is written, and the connection is left usable (proven with one ORM write inside the same outer `transaction.atomic()` the failure happened in). A third new class, `TestBytesGoLast` (FIX I2), asserts that when the attachment cleanup raises, the registered artifact-purge slot is never called at all — see Step 4's reordered code below.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest -q agents identity`
Expected: PASS.

- [ ] **Step 8: Run the full gate and commit**

```bash
git add agents/retention.py agents/contracts/artifacts.py agents/apps.py agents/tests/ \
        agents/contracts/tests/test_artifacts.py agents/chat/tests/test_delete.py
git commit -m "feat(agents): the conversation purge, the tool-record scrub and the artifact slot

Re-pins agents/chat/tests/test_delete.py's four teardown classes, which now assert against agents.retention.purge_conversation in agents/tests/test_retention.py."
```

---

### Task 10: `tools/rag` — the notes handler and the chat-scope exclusions

**Files:**
- Create: `tools/rag/retention.py`
- Modify: `tools/rag/access.py` (`readable_documents`, `listable_documents`, `attached_documents`)
- Modify: `tools/rag/retrieval.py` (`_visibility_filters`, `retrieve_nodes` — review round one, below)
- Modify: `tools/rag/apps.py` (register the handler)
- Modify: `tools/rag/README.md`
- Test: `tools/rag/tests/test_retention.py` (new), `tools/rag/tests/test_access.py` / `test_chat_scoped_documents.py` (extend), `tools/rag/tests/test_retrieval.py` / `test_retrieval_visibility.py` (extend — review round one, below)

**Held tests:** `tools/rag/tests/test_access_documents.py`, `test_chat_scoped_documents.py` and `agents/chat/tests/test_thread.py` carry query-count assertions that reach `readable_documents` / `listable_documents` / `attached_documents`. Before editing, run `grep -rn "django_assert_num_queries\|CaptureQueriesContext" tools/rag/tests agents/chat/tests` and re-pin each by the real delta below; name every one you touch in the commit message.

**The real per-function delta, stated rather than guessed:**

| Function | Extra queries | Why |
|---|---|---|
| `readable_documents` | **+2** with no open conversation ticket on the box; **+3** with one or more | `ticketed_keys("document")` and `ticketed_keys("conversation")` always run; the chat-scoped-document lookup, keyed off the conversation-ticket ids, is answered by the ORM without a database round trip when that id list is empty (a filter on an empty `__in` is known-empty at compile time), so it costs a query only once there is an id to filter on |
| `listable_documents` | same **+2/+3** on the `is_admin` leg; **+0** on the member leg, which reaches them through `readable_documents` | one `_deleted_document_ids()` call either way |
| `attached_documents` | **+2/+3**, never doubled | the ids are computed ONCE at the top and threaded into both its own `chat_scoped` query and its `readable_documents` call |
| `retrieve_nodes` (review round one) | same **+2/+3**, on every call that does not take the `sees_nothing` early return | one `_deleted_document_ids()` call, threaded into `_visibility_filters` — see that review round's own note below |

This is a real saving, not a corner cut: the common box has no open conversation ticket at all, and the cheaper path is exactly that common case — the count stays bounded by the number of open tickets, never by the number of documents, on both sides of the split. An implementation that forced the third query to run unconditionally, just to keep every pin at a single flat number, would be paying a real query on the box's most common state purely so a test constant never had to carry a branch — the wrong trade.

**Interfaces:**
- Consumes: `identity.retention.ticketed_keys`; `identity.contracts.retention.KIND_CONVERSATION`, `KIND_DOCUMENT`; `tools.rag.services.delete_document`; `django.conf.settings.NOTES_DIR`.
- Produces: `tools.rag.retention.purge_conversation_notes(key: str) -> int`; registration `RetentionHandler(kind="conversation", key="rag.conversation_notes", label="Staging notes", handler="tools.rag.retention.purge_conversation_notes", order=ORDER_FILES)`.
- Produces (review round one): `tools.rag.retrieval._visibility_filters(category, visibility, deleted_ids=())` gains its third parameter; `retrieve_nodes` threads `tools.rag.access._deleted_document_ids()` into it once per call.

- [ ] **Step 1: Write the failing tests**

```python
# tools/rag/tests/test_retention.py
"""What `tools/rag` destroys for a deleted conversation."""
from __future__ import annotations

import uuid

import pytest
from django.conf import settings

from tools.rag.models import Document
from tools.rag.retention import purge_conversation_notes
from tools.rag.tests._helpers import make_document

pytestmark = pytest.mark.django_db


class TestTheStagingNote:
    def test_it_unlinks_the_note_file_and_deletes_the_note_document(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        conversation_id = uuid.uuid4()
        note = tmp_path / f"{conversation_id}.md"
        note.write_text("a consolidated stream", encoding="utf-8")
        document = make_document(notes_conversation_id=conversation_id)

        removed = purge_conversation_notes(str(conversation_id))

        assert not note.exists()
        assert not Document.objects.filter(pk=document.pk).exists()
        assert removed == 2

    def test_a_missing_file_is_not_an_error(self, tmp_path, settings):
        """A purge that failed because somebody had already cleaned up
        would be a purge nobody could finish."""
        settings.NOTES_DIR = tmp_path
        assert purge_conversation_notes(str(uuid.uuid4())) == 0

    def test_it_is_idempotent(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        conversation_id = uuid.uuid4()
        (tmp_path / f"{conversation_id}.md").write_text("x", encoding="utf-8")
        make_document(notes_conversation_id=conversation_id)
        purge_conversation_notes(str(conversation_id))
        assert purge_conversation_notes(str(conversation_id)) == 0

    def test_an_unparseable_key_removes_nothing(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        assert purge_conversation_notes("not-a-uuid") == 0

    def test_another_conversations_note_is_untouched(self, tmp_path, settings):
        settings.NOTES_DIR = tmp_path
        mine, theirs = uuid.uuid4(), uuid.uuid4()
        (tmp_path / f"{mine}.md").write_text("x", encoding="utf-8")
        (tmp_path / f"{theirs}.md").write_text("y", encoding="utf-8")
        purge_conversation_notes(str(mine))
        assert (tmp_path / f"{theirs}.md").exists()
```

```python
# append to tools/rag/tests/test_chat_scoped_documents.py
class TestAChatScopedDocumentFollowsItsConversation:
    """A `Document` with `scope=conversation` has exactly ONE attachment
    row, for one conversation -- the invariant `delete_attachments`
    already depends on. So a ticketed conversation hides its chat-scoped
    documents too, on both the content read and the library list."""

    def test_it_is_hidden_from_both_document_functions(self):
        with posture("open"):
            principal = user_principal(make_user())
            conversation = make_conversation()
            document = _chat_scoped_document_attached_to(conversation)
            delete_conversation(principal, conversation)
            assert document not in readable_documents(principal)
            assert document not in listable_documents(principal)

    def test_a_universal_document_attached_to_that_conversation_survives(self):
        """An attachment is a CLAIM a conversation makes on a document,
        never the document's own existence."""
        with posture("open"):
            principal = user_principal(make_user())
            conversation = make_conversation()
            universal = _universal_document_attached_to(conversation)
            delete_conversation(principal, conversation)
            assert universal in readable_documents(principal)
            assert universal in listable_documents(principal)

    def test_attached_documents_drops_rows_for_a_ticketed_conversation(self):
        with posture("open"):
            principal = user_principal(make_user())
            conversation = make_conversation()
            _chat_scoped_document_attached_to(conversation)
            delete_conversation(principal, conversation)
            assert attached_documents(
                principal, conversation_id=conversation.id) == []

    def test_a_ticketed_document_is_hidden_from_both_functions(self):
        """Slice 2 makes the library delete write these tickets; the
        exclusion ships now so both halves land together."""
        with posture("open"):
            principal = user_principal(make_user())
            document = make_document()
            DeletionTicket.objects.create(
                kind="document", key=str(document.pk),
                purge_on=timezone.localdate())
            assert document not in readable_documents(principal)
            assert document not in listable_documents(principal)
```

Reuse that module's existing document builders rather than writing new ones; `_chat_scoped_document_attached_to` / `_universal_document_attached_to` stand for whatever they are actually called there — read the file and use the real names.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q tools/rag/tests/test_retention.py tools/rag/tests/test_chat_scoped_documents.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.rag.retention'`.

- [ ] **Step 3: Add the two exclusions to `tools/rag/access.py`**

Import the seam at module top:

```python
from identity.contracts.retention import KIND_CONVERSATION, KIND_DOCUMENT
from identity.retention import ticketed_keys
```

Add one private helper beside the other module-level helpers:

```python
def _deleted_document_ids() -> list[int]:
    """Documents a ticket hides: the ones deleted outright, plus every
    chat-scoped document whose CONVERSATION is deleted.

    A `Document` with `scope=conversation` has exactly one
    `DocumentAttachment`, for one conversation (`delete_attachments`
    states and depends on that invariant), so a chat-scoped document
    follows its conversation and nothing else does. Universal and
    stream-contained documents are UNTOUCHED: an attachment is a CLAIM a
    conversation makes on a document, never the document's own
    existence, and deleting a conversation must not hide a document
    another conversation still holds a claim on.

    TWO QUERIES WHEN NOTHING IS TICKETED, THREE WHEN A CONVERSATION IS --
    flat in the number of TICKETS rather than in the number of documents
    (the `agents.shares.shared_keys` cost model, unchanged), never
    guessed. The two `ticketed_keys()` reads always run; the third, the
    chat-scoped lookup keyed off the conversation ticket ids, is answered
    by the ORM WITHOUT a database round trip when that id list is empty
    -- filtering on an empty `__in` is known-empty at compile time, so
    there is nothing for Postgres to be asked. That is a real saving,
    not a corner being cut: the common box has no open conversation
    ticket at all, and the cheaper path is exactly that common case. A
    box with at least one open conversation ticket pays the third query,
    still bounded by the number of TICKETS rather than documents.

    COMPUTED ONCE PER CALL AND THREADED. Its three callers each call it
    exactly once and pass the result down -- `attached_documents` in
    particular would otherwise pay for it twice, once for its own
    `chat_scoped` query and once inside `readable_documents`.
    """
    ids = [int(key) for key in ticketed_keys(KIND_DOCUMENT) if key.isdecimal()]
    ids.extend(
        Document.objects.filter(
            scope=Document.Scope.CONVERSATION,
            attachments__conversation_id__in=ticketed_keys(KIND_CONVERSATION),
        ).values_list("pk", flat=True)
    )
    return ids
```

All three functions take a new keyword-only `deleted_ids=None`, compute the list once when it is `None`, and thread it onward. That is the same "an already-fetched value travels down as plain data" shape `settings_row=` already has throughout this module, and it is what keeps `attached_documents` at one computation rather than two.

`readable_documents` — the exclusion goes on the base, **before** the `v.unrestricted` branch, and both legs inherit it:

```python
def readable_documents(principal, *, workstream_id=None, settings_row=None,
                       deleted_ids=None):
    """<the existing docstring, plus one paragraph for `deleted_ids`:
    an already-computed list from `_deleted_document_ids()`, threaded in
    by a caller that has one, exactly as `settings_row=` is.>"""
    if deleted_ids is None:
        deleted_ids = _deleted_document_ids()
    contained = Q(workstream__isnull=True) | Q(workstream_id=workstream_id)
    not_chat_scoped = ~Q(scope=Document.Scope.CONVERSATION)
    # THE EXCLUSION IS ON THE BASE, BEFORE the `unrestricted` branch --
    # that branch is every principal on an open box.
    base_rows = Document.objects.exclude(pk__in=deleted_ids)
    v = document_visibility(principal, settings_row=settings_row)
    if v.unrestricted:
        # ... the existing chat-scope reasoning, unchanged ...
        return base_rows.filter(base)
    # ... the existing labelled/unlabelled legs, unchanged ...
    return base_rows.filter(base).distinct()
```

Replace both `Document.objects.filter(base)` call sites with `base_rows.filter(base)`; the `contained` / `not_chat_scoped` / `labelled` / `unlabelled_allowed` logic between them is untouched, comments included.

`listable_documents` — one call, threaded into the member leg:

```python
def listable_documents(principal, *, deleted_ids=None):
    if deleted_ids is None:
        deleted_ids = _deleted_document_ids()
    if is_admin(principal):
        rows = Document.objects.exclude(pk__in=deleted_ids)
    else:
        rows = readable_documents(principal, deleted_ids=deleted_ids)
    if not sees_all_content(principal):
        rows = rows.exclude(
            Q(scope=Document.Scope.CONVERSATION)
            & ~Q(owner_kind=principal.kind, owner_key=principal.key)
        )
    return rows
```

`attached_documents` — computed once at the top, threaded into **both** places that would otherwise compute it:

```python
    deleted_ids = _deleted_document_ids()
    chat_scoped = list(
        Document.objects.exclude(pk__in=deleted_ids)
        .filter(scope=Document.Scope.CONVERSATION,
                attachments__conversation_id=conversation_id)
        .annotate(turn_id=F("attachments__turn_id"))
    )
```

and every `readable_documents(...)` call inside that function gains `deleted_ids=deleted_ids` — there are two (the `chat_scoped_readable_ids` narrowing and the `attached` leg). Nothing else in the function changes; `stream_documents` reaches its own rows through `readable_documents`, so a stream's corpus inherits the exclusion with no further edit.

- [ ] **Step 3b (review round one): thread the same exclusion into `tools/rag/retrieval.py`**

The first pass of this task stopped at the row surfaces; a chunk of a ticketed item stayed retrievable through `tools.rag.retrieval._visibility_filters` regardless. `_visibility_filters` gains a third, keyword-defaultable parameter, `deleted_ids=()`, and adds one top-level `file_id NOT IN (...)` clause — the installed Postgres store's own `NIN` operator, next to the `EQ`/`ANY`/`IS_EMPTY` ones it already uses — only when the list is non-empty:

```python
def _visibility_filters(category, visibility: DocumentVisibility, deleted_ids=()):
    ...
    clauses.append(gated)

    if deleted_ids:
        clauses.append(MetadataFilter(
            key="file_id", value=[str(i) for i in deleted_ids],
            operator=FilterOperator.NIN))

    if not clauses:
        return None
    return MetadataFilters(filters=clauses, condition=FilterCondition.AND)
```

`retrieve_nodes` computes the list ONCE, right before it builds the filter — never inside the `sees_nothing` early return, which already answers with zero nodes and has nothing to narrow:

```python
    filters = _visibility_filters(category, visibility, _deleted_document_ids())
```

`readable_documents`' own docstring line claiming to govern "the chunks retrieval may return" is corrected to name where retrieval actually applies the exclusion — a second EXPRESSION of the same rule (`test_workstream_corpus.py`'s own "two expressions of one rule" split), sharing `_deleted_document_ids()` itself rather than merely mirroring its shape.

TESTS (review round one): `tools/rag/tests/test_retrieval_visibility.py` gains a `TestTheDeletedIdsClause` class pinning the clause's shape (empty list adds nothing; a non-empty list adds a top-level `NOT IN` of decimal strings; the clause survives alongside a category and a restricted visibility). `tools/rag/tests/test_chat_scoped_documents.py` gains `TestTheDeletionExclusionReachesRetrieval`, extending that file's own `_matches` evaluator with the `NIN` operator: a chat-scoped document's chunk is excluded once its conversation is ticketed (through `agents.visibility.delete_conversation`); a directly ticketed document's chunk is excluded (through `identity.retention.delete_content` with `KIND_DOCUMENT`, never a hand-inserted `DeletionTicket`); an untouched document's chunk still matches, checked against the SAME built filter object as the two exclusions (one `_deleted_document_ids()` call, not three); and with nothing ticketed the built filter is identical to one built with no `deleted_ids` argument at all. `tools/rag/tests/test_retrieval.py::TestRetrieveNodes` gains three tests: a ticketed document's id reaches the mocked retriever's own `filters` kwarg as a `NOT IN` clause (proving the real end-to-end thread, not merely `_visibility_filters`'s own output in isolation); and the ruled query-count pair, `django_assert_num_queries(2)` with nothing ticketed and `django_assert_num_queries(3)` with one open conversation ticket, mirroring `test_access_documents.py`'s own pins for the row surfaces.

- [ ] **Step 4: Write `tools/rag/retention.py`**

```python
"""What `tools/rag` destroys when a deleted item's date arrives.

REGISTERED, NEVER CALLED DIRECTLY -- `tools/rag/apps.py::ready()`
registers the dotted paths and `identity/cascades.py` resolves them,
the same mechanism this column already uses for its entitlement cascade
and its six attachment seams.
"""
from __future__ import annotations

import uuid

from django.conf import settings

from tools.rag import services
from tools.rag.models import Document


def purge_conversation_notes(key: str) -> int:
    """Remove a deleted conversation's staging note and its note
    document. Returns how many things went.

    TWO THINGS, and they are not the same thing. The FILE is the
    deterministic `<NOTES_DIR>/<conversation-uuid>.md` original
    `tools/rag/jobs.py` writes when a stream is consolidated; the
    DOCUMENT is the ingested copy, found by `notes_conversation_id` -- a
    UUID BY VALUE, never a foreign key, because `tools/rag` may not
    import `agents.models`. The document goes through
    `services.delete_document`, which already tears down chunks, the
    managed store directory and the row together.

    A MISSING FILE IS NOT AN ERROR (`missing_ok=True`): the ONE
    best-effort case this function forgives is "somebody already cleaned
    this up" -- a purge that failed for that reason would be a purge
    nobody could ever finish.

    ANY OTHER FAILURE TO REMOVE THE FILE IS RAISED, NOT SWALLOWED
    (review round one -- an earlier pass caught every `OSError` here and
    logged-and-continued, which deleted the row -- the only remaining
    handle on the file -- even when the file itself could not actually
    be removed). Left to propagate, `identity/cascades.py::run_retention`
    fails the whole purge: the ticket survives, and the next sweep
    retries this handler from the top rather than reporting a purge that
    never actually happened.

    Idempotent: once the file really is gone, a re-run finds no file and
    no document and returns zero.
    """
    try:
        conversation_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0

    removed = 0
    path = settings.NOTES_DIR / f"{conversation_id}.md"
    # READ BEFORE THE UNLINK: `missing_ok=True` returns nothing whether
    # or not the file was there, and the count has to distinguish "one
    # note removed" from "nothing to remove" -- the test asserts 2 for a
    # note plus a document, and 0 for a re-run. A NON-MISSING failure
    # (any other `OSError`) is NOT caught here -- it propagates, and the
    # row below is never touched.
    existed = path.exists()
    path.unlink(missing_ok=True)
    if existed:
        removed += 1

    for document in Document.objects.filter(notes_conversation_id=conversation_id):
        services.delete_document(document)
        removed += 1
    return removed
```

TEST (review round one, added to `tools/rag/tests/test_retention.py`): patch `pathlib.Path.unlink` to raise `PermissionError` for the note's own path only (every other path unlinks for real); `purge_conversation_notes` raises, the note file still exists, and the `Document` row still exists. The pre-existing "a missing file is not an error" test is unchanged and stays green.

- [ ] **Step 5: Register the handler in `tools/rag/apps.py`**

Inside `ready()`, beside the existing `register_entitlement_cascade` call:

```python
        # THIS COLUMN'S ANSWER TO "A DELETED CONVERSATION'S DATE HAS
        # ARRIVED". FILES band: it unlinks a staging note and reaches
        # `services.delete_document`, which removes a managed store
        # directory. Same dotted-path mechanism, same import-law reason.
        from identity.contracts.cascades import (
            ORDER_FILES, RetentionHandler, register_retention_handler,
        )
        from identity.contracts.retention import KIND_CONVERSATION

        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION,
            key="rag.conversation_notes",
            label="Staging notes",
            handler="tools.rag.retention.purge_conversation_notes",
            order=ORDER_FILES,
        ))
```

- [ ] **Step 6: Document it in `tools/rag/README.md`**

One short section: the handlers this column registers, and what each one does **and does not** reach — the notes handler removes the staging note and the note document; a chat-scoped document dies with its conversation through the attachment seam; a universal or stream-contained document loses only its claim.

- [ ] **Step 7: Re-pin the query counts, add the deleted-path siblings, and run the tests**

Re-pin `tools/rag/tests/test_access_documents.py`'s two held `django_assert_num_queries`
constants by the real, no-open-conversation-ticket delta: `test_open_posture_returns_
everything_with_no_permission_query` (`readable_documents`) from 2 to 4;
`test_an_ordinary_readable_attachment_is_readable_with_no_extra_query` (`attached_documents`)
from 3 to 5. Beside each, add a sibling test that first tickets one conversation (through
`identity.retention.delete_content`, never a hand-inserted `DeletionTicket` row) and pins the
count one higher — 5 and 6 — proving both the common (no open conversation ticket) and the
less-common (one open) costs are held, not merely the cheaper one.

Run: `.venv/bin/pytest -q tools/rag identity agents`
Expected: PASS, after the re-pin and the two new sibling tests above.

- [ ] **Step 8: Run the full gate and commit**

```bash
git add tools/rag/retention.py tools/rag/access.py tools/rag/apps.py \
        tools/rag/README.md tools/rag/tests/
git commit -m "feat(rag): the staging-note purge and the chat-scope exclusions

Re-pins tools/rag/tests/test_access_documents.py::TestReadableDocuments::
test_open_posture_returns_everything_with_no_permission_query and ::
TestTheCaptionNeverCrossesALineTheBytesDoNot::
test_an_ordinary_readable_attachment_is_readable_with_no_extra_query: the document path
gains two bounded ticket reads with no open conversation ticket on the box, three with one."
```

---

### Task 11: `tools/vision` — the artifact purge and the gallery exclusion

**Steward: the vision steward.** The clearance packet is the diff against `tools/vision/visibility.py`, `tools/vision/services.py`, `tools/vision/apps.py` and the new `tools/vision/retention.py`, sent **before** merge, never as a ping after.

**Files:**
- Create: `tools/vision/retention.py`
- Modify: `tools/vision/visibility.py` (`visible_jobs`; `may_read_job` — see the review round one amendment below)
- Modify: `tools/vision/services.py` (new `delete_jobs(job_ids) -> int`, beside `delete_job` — see the amendment note below)
- Modify: `tools/vision/apps.py` (register the artifact purge)
- Modify: `tools/vision/README.md`
- Test: `tools/vision/tests/test_retention.py` (new — also covers `delete_jobs`), `tools/vision/tests/test_visibility.py` (extend), `tools/vision/tests/test_views_file_cache.py` (extend — see the review round one amendment below)

**Held tests:** `tools/vision/tests/test_visibility.py::TestGeneratedImagesAreContent::test_the_gallery_and_the_recent_list_both_narrow` and `::test_a_member_sees_only_their_own_generations` — both stand, but `visible_jobs` gains one query. Check for a `django_assert_num_queries` on the gallery path in `test_views_gallery.py` and re-pin it if present; name it in the commit message if so. Also confirm `foundation/ops/tests/test_column_boundaries.py::test_no_vision_module_queries_generationjob_directly` (IA-1's closed set of two) stays green — see the amendment note below for why that gate is the reason `purge_artifacts` may not query `GenerationJob.objects` itself.

**Interfaces:**
- Consumes: `identity.retention.ticketed_keys`; `identity.contracts.retention.KIND_VISION_JOB`; `agents.contracts.artifacts.parse_artifact`, `register_artifact_purge`; `tools.vision.services.delete_jobs`; `tools.vision.models.GeneratedOutput`, `JobInput`.
- Produces: `tools.vision.retention.purge_artifacts(refs, generation_ids) -> int`; `tools.vision.services.delete_jobs(job_ids) -> int`.
- **Deferred to the queue half (Task 19):** `purge_artifacts` will also call `models.contracts.queue.forget_jobs(queue_job_ids)`. Until then it does not touch the queue at all, and this module asserts nothing about it — the unlanded state is recorded once, by `identity/tests/test_deletion_demo.py`'s two gate markers, which assert a real row's presence rather than a function's absence.

- [ ] **Step 1: Write the failing tests**

```python
# tools/vision/tests/test_retention.py
"""The registered artifact purge: from a conversation's references to
the generation jobs behind them."""
from __future__ import annotations

import uuid

import pytest

from tools.vision import services
from tools.vision.models import GeneratedOutput, GenerationJob, JobInput
from tools.vision.retention import purge_artifacts
from tools.vision.tests._helpers import seed_sweep_posture

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sweep():
    seed_sweep_posture()


# MODULE-LOCAL ROW BUILDERS, mirroring
# `tools/vision/tests/test_visibility.py`'s own `_generation`/`_output`/
# `_job_input` exactly. `tools/vision/tests/_helpers.py` defines NO
# generation builder -- it re-exports only the identity helpers
# (`grant`, `make_admin`, `make_entitlement`, `make_user`, `posture`,
# `seed_sweep_posture`, `sign_in`, `user_principal`) and this column's
# engine fakes -- and `identity/tests/_helpers.py::make_generation` is
# route-matrix scaffolding that resolves through `apps.get_model`. Two
# builders for two contexts, per that module's own recorded rule; this
# one is inside the column, so it imports the models.
def _generation(**overrides) -> GenerationJob:
    fields = dict(
        operation="txt2img",
        params={"prompt": "a lighthouse"},
        engine="stubengine",
        model_id="stub.safetensors",
        model_fingerprint="stubengine:stub.safetensors:None",
        status=GenerationJob.Status.DONE,
    )
    fields.update(overrides)
    return GenerationJob.objects.create(**fields)


def _output(**overrides) -> GeneratedOutput:
    job = overrides.pop("job", None) or _generation()
    fields = dict(job=job, index=0, path="/tmp/does-not-matter.png",
                  media_type="image/png")
    fields.update(overrides)
    return GeneratedOutput.objects.create(**fields)


class TestMappingReferencesToJobs:
    def test_an_output_reference_deletes_its_whole_job(self):
        job = _generation()
        output = _output(job=job)
        assert purge_artifacts([f"output:{output.pk}"], []) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()

    def test_two_outputs_of_one_job_are_one_delete(self):
        """Each reference is one FK hop from its job and several outputs
        share one job, so the mapping dedupes BY JOB."""
        job = _generation()
        first, second = _output(job=job, index=0), _output(job=job, index=1)
        assert purge_artifacts(
            [f"output:{first.pk}", f"output:{second.pk}"], []) == 1

    def test_an_input_reference_resolves_through_its_own_table(self):
        job = _generation()
        job_input = JobInput.objects.create(job=job, param_key="image",
                                            path="/dev/null",
                                            media_type="image/png")
        assert purge_artifacts([f"input:{job_input.pk}"], []) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()

    def test_a_bare_generation_id_is_accepted(self):
        job = _generation()
        assert purge_artifacts([], [str(job.pk)]) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()

    def test_a_reference_and_an_id_naming_one_job_are_one_delete(self):
        job = _generation()
        output = _output(job=job)
        assert purge_artifacts([f"output:{output.pk}"], [str(job.pk)]) == 1

    def test_a_uuid_that_matches_no_job_is_ignored(self):
        assert purge_artifacts([], [str(uuid.uuid4())]) == 0

    def test_a_reference_that_matches_no_row_is_ignored(self):
        assert purge_artifacts(["output:999999"], []) == 0

    def test_an_unparseable_reference_is_dropped_not_raised(self):
        assert purge_artifacts(["not-a-reference", ""], []) == 0

    def test_a_document_reference_is_skipped_silently(self):
        """`document:<id>` names a `Document` row, which the attachment
        seam already reaches -- `agents.retention._collect` never hands
        one to this column in the first place, and this pins that a
        caller who did anyway would not raise or count it."""
        assert purge_artifacts(["document:451"], []) == 0

    def test_it_is_idempotent(self):
        job = _generation()
        output = _output(job=job)
        purge_artifacts([f"output:{output.pk}"], [])
        assert purge_artifacts([f"output:{output.pk}"], []) == 0

    def test_it_goes_through_delete_job_so_the_files_and_the_sweep_run(self, monkeypatch):
        calls = []
        from tools.vision import retention as module
        monkeypatch.setattr(module.services, "delete_job",
                            lambda job: calls.append(job.pk))
        job = _generation()
        purge_artifacts([], [str(job.pk)])
        assert calls == [job.pk]


class TestEmptyInputCostsNothing:
    """CONTROLLER ADDITION: the artifact-purge slot is called on EVERY
    conversation purge on a vision box, including the common case -- a
    conversation with no images -- which hands it two empty lists. That
    must not touch a job, run no engine-side sweep, and cost no more
    than the mapping itself needs, which is zero queries for empty
    input."""

    def test_two_empty_lists_return_zero_and_touch_nothing(
        self, monkeypatch, django_assert_num_queries,
    ):
        from tools.vision import retention as module

        called = []
        monkeypatch.setattr(module.services, "delete_jobs",
                            lambda job_ids: called.append(list(job_ids)) or 0)
        with django_assert_num_queries(0):
            assert purge_artifacts([], []) == 0
        assert called == []


class TestDeleteJobs:
    """`tools.vision.services.delete_jobs` -- the one unscoped read of
    `GenerationJob.objects` `purge_artifacts` above hands its mapped job
    ids to, rather than querying the table itself (IA-1's closed set of
    two, `foundation/ops/tests/test_column_boundaries.py`). Lives beside
    `purge_artifacts`'s own tests, not `test_services.py`, which this
    module's own size keeps under the split threshold."""

    def test_it_deletes_exactly_the_named_jobs_and_leaves_another_alone(self):
        gone = _generation()
        stays = _generation()

        assert services.delete_jobs([gone.pk]) == 1

        assert not GenerationJob.objects.filter(pk=gone.pk).exists()
        assert GenerationJob.objects.filter(pk=stays.pk).exists()

    def test_an_empty_list_deletes_nothing(self):
        _generation()

        assert services.delete_jobs([]) == 0

        assert GenerationJob.objects.count() == 1

    def test_ids_of_jobs_already_gone_answer_zero_and_do_not_raise(self):
        job = _generation()
        job_id = job.pk
        job.delete()

        assert services.delete_jobs([job_id]) == 0
```

This module asserts nothing about the queue. Task 19 ADDS `test_the_generations_queue_row_goes_with_it` here when `forget_jobs` exists; until then the state is recorded by `identity/tests/test_deletion_demo.py`'s two gate markers, which assert a real row's presence rather than a function's absence.

```python
# append to tools/vision/tests/test_visibility.py
# -- needs `from django.utils import timezone` and
# -- `from identity.models import DeletionTicket` beside the existing imports
class TestTicketedJobsAreInvisible:
    def test_a_ticketed_job_is_hidden_from_sees_all_content_too(self):
        with posture("open"):
            principal = user_principal(make_user())
            job = _generation()
            DeletionTicket.objects.create(
                kind="vision_job", key=str(job.pk),
                purge_on=timezone.localdate())
            assert list(visible_jobs(principal)) == []

    def test_an_untouched_job_is_still_visible(self):
        with posture("open"):
            principal = user_principal(make_user())
            kept = _generation()
            hidden = _generation()
            DeletionTicket.objects.create(
                kind="vision_job", key=str(hidden.pk),
                purge_on=timezone.localdate())
            assert list(visible_jobs(principal)) == [kept]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision/tests/test_retention.py tools/vision/tests/test_visibility.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.vision.retention'`.

- [ ] **Step 3: Add the exclusion to `visible_jobs`**

```python
from identity.contracts.retention import KIND_VISION_JOB
from identity.retention import ticketed_keys


def visible_jobs(principal):
    # THE EXCLUSION IS ON THE BASE QUERYSET, BEFORE the
    # `sees_all_content` early return: that branch is every principal on
    # an open box, so an exclusion on the restricted leg alone would
    # leave deleted generations fully visible in the common posture.
    qs = GenerationJob.objects.exclude(pk__in=ticketed_keys(KIND_VISION_JOB))
    if sees_all_content(principal):
        return qs
    return qs.filter(owned_rows_q(principal))
```

`GenerationJob.pk` is a UUID and `ticketed_keys` returns strings; Django adapts a list of UUID-shaped strings for a `uuid` column on Postgres, and a non-UUID string in that list would raise inside the queryset. Nothing writes one — `DeletionTicket.key` is always `str(pk)` — but filter defensively in `purge_artifacts`' own parsing, not here, so this function stays the one-line exclusion the other three columns also have.

`known_job_uuids` is **not** narrowed: it is the Engine files page's box-inventory accounting seam, and a deleted-but-not-yet-purged job's engine-side files are still accounted for, not orphaned. Say so in a comment there.

**Amended (review round one):** `visible_jobs`'s own exclusion never runs for `output_file`/`input_file` — both load their `GeneratedOutput`/`JobInput` row by primary key and call `may_read_job(principal, job)` directly, so a ticketed job's image stayed fetchable forever by its direct URL. `may_read_job` gains the same exclusion, before its own `sees_all_content` branch and for the identical reason:

```python
def may_read_job(principal, job) -> bool:
    """Whether one already-loaded job may be read. `job_status`,
    `job_delete`, `output_file` and `input_file` resolve through this and
    answer 404 when it is False -- 404 rather than 403, because a 403 on
    a row-addressed URL confirms the row exists.

    A TICKETED JOB IS REFUSED HERE TOO, before the `sees_all_content`
    branch below. `output_file` and `input_file` load their
    `GeneratedOutput`/`JobInput` row by primary key and reach this
    function directly -- they never go through `visible_jobs`, so the
    base-queryset exclusion that hides a deleted generation from the
    gallery and the Recent list never runs for them. Without a matching
    check here, a deleted image would stay fetchable forever by anybody
    who already had its direct URL. Placed before `sees_all_content` for
    the same reason `visible_jobs`'s own exclusion comes first: that
    branch answers True for every principal on an open box, so a check
    on the restricted leg alone would leave a deleted image servable
    exactly where it matters most. Costs one extra ticket read per file
    fetch.
    """
    if str(job.pk) in ticketed_keys(KIND_VISION_JOB):
        return False
    if sees_all_content(principal):
        return True
    return may_read_owned_row(principal, job)
```

Grepped for every other production caller of `may_read_job` and every sibling per-job predicate in `visibility.py`: there is no `may_manage_job` or `may_delete_job` in this module, and the only two production call sites of `may_read_job` are `output_file` and `input_file` in `tools/vision/views.py`. Every other job-scoped route (`job_status`, `job_delete`, `jobs_delete_selected`, the create page's reuse-prefill, the gallery's job filter) already resolves through `visible_jobs(principal)`, whose own exclusion already refused a ticketed job before this amendment — so re-running, editing from, or sharing a ticketed job by id was already blocked; only the two file routes had the gap. No change was needed outside `may_read_job` itself.

Tests, in `tools/vision/tests/test_views_file_cache.py` (already the module covering both routes' shared cache-header behaviour, through the project's `stored_output` helper and real files on disk): a new `TestATicketedJobsFileIsNotFetchableByItsDirectURL`, parametrized over both routes, proves the owner is refused after `identity.retention.delete_content` and served again after `identity.retention.restore_content`; that another principal on an OPEN box is refused too (the case that proves the check runs before `sees_all_content`, not only on the restricted leg); and that an unticketed job's files are unaffected.

- [ ] **Step 4: Add `delete_jobs` to `tools/vision/services.py`, then write `tools/vision/retention.py` against it**

**Amended by the controller during Task 11's own review** — see the note below the two code blocks for why. Beside `delete_job`:

```python
def delete_jobs(job_ids) -> int:
    """Delete every `GenerationJob` named by `job_ids` (each through
    `delete_job`, so its files and its best-effort engine sweep go too)
    and return how many were actually deleted.

    THE ONE UNSCOPED READ OF `GenerationJob.objects` A RETENTION PURGE
    NEEDS, and it lives here rather than in `tools.vision.retention` on
    purpose: that module maps a conversation's artifact references and
    generation ids to job ids, and `tools/vision`'s own IA-1 rule
    (`foundation/ops/tests/test_column_boundaries.py`'s closed set of
    two) is that only `visibility.py` and this module may query the job
    table directly -- a THIRD site is exactly the drift that gate
    exists to catch, and `visibility.py` is the wrong home regardless,
    since it answers "who may see this", and a purge must reach a
    ticketed job, or a job owned by somebody else, that nobody may see
    at all.

    DELIBERATELY NOT VISIBILITY-SCOPED: unlike `visibility.visible_jobs`,
    this reads every matching row regardless of owner or ticket, because
    the whole point of a purge is to finish what a ticket already
    started.

    Catches nothing: a failure deleting one job's rows or files
    propagates, so the retention runner's own transaction rolls back and
    the ticket stays due for the next sweep. `delete_job`'s own
    engine-side sweep is the one documented best-effort exception, and
    it already lives inside `delete_job` -- there is no second one here.

    Idempotent: an empty or already-gone id in `job_ids` simply matches
    no row and contributes nothing.
    """
    deleted = 0
    for job in GenerationJob.objects.filter(pk__in=job_ids):
        delete_job(job)
        deleted += 1
    return deleted
```

Then `tools/vision/retention.py`:

```python
"""What `tools/vision` destroys for a deleted conversation.

REGISTERED THROUGH `agents.contracts.artifacts.register_artifact_purge`,
a single slot, because the agents column COMPUTES the references and
exactly one tool column knows what they mean -- the same shape
`agents.contracts.attachments.register_attachment_cleanup` already has.

TWO CHANNELS REACH A CONVERSATION'S GENERATED IMAGES, and one of them
has a hole the other closes:

* ARTIFACT REFERENCES. `tools.vision.tools.run_generate` returns
  `artifacts = tuple(f"output:{output['id']}" ...)`, which
  `agents/runtime/loop.py` writes onto the tool turn verbatim.
  `output:<id>` is a `GeneratedOutput` pk and `input:<id>` a `JobInput`
  pk -- each ONE FK HOP from its job, and several outputs share one job,
  so the mapping DEDUPES BY JOB.
* GENERATION IDS. The same line of `agents/runtime/loop.py` writes
  `data=outcome.result.data`, which for this tool IS `job_json(job)`,
  whose first key is `"id"`. A job that reached the engine and FAILED
  mints no output at all, so the first channel finds nothing -- and
  `run_generate` reaches `job_json` on every terminal outcome, so this
  channel names the job anyway. A failed chat-created job IS caught.

WHAT IS NOT CAUGHT, named as accepted residue (spec section 10.7): a
generation whose tool turn was never written at all -- the job row was
created and the turn died before it (a crash, a kill, a cancelled
turn). It stays visible to its own owner in the gallery, where the
`vision_job` kind deletes it.
"""
from __future__ import annotations

import logging
import uuid

from agents.contracts.artifacts import parse_artifact
from tools.vision import services
from tools.vision.models import GeneratedOutput, JobInput

logger = logging.getLogger(__name__)


def purge_artifacts(refs, generation_ids) -> int:
    """Delete every generation job these references and ids name.
    Returns the number of JOBS deleted, deduped.

    THIS MODULE NEVER QUERIES `GenerationJob.objects` ITSELF: it only
    maps references and generation ids to job ids, and hands them to
    `tools.vision.services.delete_jobs` for the one unscoped read and
    the actual deletion -- `foundation/ops/tests/
    test_column_boundaries.py`'s IA-1 gate pins `visibility.py` and
    `services.py` as the only two files in this column allowed to touch
    that manager, and a third site here is exactly the drift it exists
    to catch. Each job goes through `services.delete_job` (inside
    `delete_jobs`), so its child rows cascade, its managed directory
    goes, and its best-effort engine-side sweep runs.

    With two empty lists this returns 0 having run no query at all
    beyond what parsing an empty sequence costs (none) and without
    calling `delete_jobs` -- every conversation purge on a vision box
    reaches this function, most of them for a conversation with no
    images, and that common case must cost nothing.

    Idempotent: a reference whose row is already gone contributes
    nothing, and so does a job id that matches no row.
    """
    job_ids: set = set()

    output_pks: list[int] = []
    input_pks: list[int] = []
    for reference in refs or ():
        try:
            kind, pk = parse_artifact(reference)
        except ValueError:
            # THE RAW REFERENCE NEVER REACHES THE LOG: this path runs
            # inside a deletion, and a deletion must not write what it
            # is destroying somewhere new. The fact that one reference
            # failed to parse and was dropped is the whole of what a
            # reader needs.
            logger.warning(
                "tools.vision.retention: one artifact reference failed to parse; ignored.")
            continue
        if kind == "output":
            output_pks.append(pk)
        elif kind == "input":
            input_pks.append(pk)

    if output_pks:
        job_ids.update(GeneratedOutput.objects.filter(pk__in=output_pks)
                       .values_list("job_id", flat=True))
    if input_pks:
        job_ids.update(JobInput.objects.filter(pk__in=input_pks)
                       .values_list("job_id", flat=True))
    for raw in generation_ids or ():
        try:
            job_ids.add(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            # SAME RULE AS THE REFERENCE BRANCH ABOVE: no raw value in
            # the log, only the fact that one generation id could not
            # be parsed and was dropped.
            logger.warning(
                "tools.vision.retention: one generation id failed to parse; ignored.")

    if not job_ids:
        return 0

    return services.delete_jobs(job_ids)
```

**Amended (review round one):** the two `logger.warning` calls above originally carried the raw value that failed to parse (`%r` of the artifact reference, `%r` of the generation id) — a deletion path logging what it is destroying, into a log outside the audit trail's own content-free discipline. Both now log only the fact that one value failed to parse. Pinned by `TestAFailedParseIsLoggedWithoutWhatItFailedToParse` in `tools/vision/tests/test_retention.py`, which asserts the raw string never appears in any `caplog` record for either path. The same review also added `test_a_failed_job_with_no_output_is_reached_only_through_its_generation_id` beside the existing generation-id tests — the docstring's own claim (a job that reached the engine and FAILED mints no output, so the generation-id channel is the only way it is reached) had no test building a FAILED job until now.

**Why this deviates from the brief's first draft, and from what the vision steward's own notes (Global Constraints) said Step 4 would look like:** the first draft closed the loop itself — `for job in GenerationJob.objects.filter(pk__in=job_ids): services.delete_job(job)` — directly inside `retention.py`. The Task 11 implementer wrote that draft, ran it against the real tree (not just read it), and found `foundation/ops/tests/test_column_boundaries.py::test_no_vision_module_queries_generationjob_directly` goes red the instant the file is tracked: that gate is a **closed set of two** files (`visibility.py`, `services.py`) allowed to touch `GenerationJob.objects` anywhere in `tools/vision`, and a third site — even one this plan wrote by hand — is exactly the drift it exists to catch. The implementer stopped rather than guess a fix and reported the conflict for a ruling (correctly, per this plan's own "no re-pinning a test the brief doesn't name" discipline). The controller's ruling, applied above: neither edit the gate (it is not this task's file, and not the vision steward's to relax) nor route the read through `visibility.py` (that module answers "who may see this"; a purge must reach a ticketed job, or one owned by somebody else, that nobody may currently see — routing the unscoped read through the visibility module would be the wrong shape even if the gate allowed it). The unscoped read belongs beside the delete it feeds, in the other allowed file: `services.py` gains `delete_jobs(job_ids) -> int`, and `retention.py` never imports `GenerationJob` at all.

- [ ] **Step 5: Register the slot in `tools/vision/apps.py`**

Inside `ready()`, **after** the feature-flag early return and beside the `register_owned_rows` call:

```python
        # THIS COLUMN'S ANSWER TO "THIS CONVERSATION'S IMAGES ARE GOING".
        # A dotted-path string on the single artifact-purge slot, for the
        # same import-law reason every other registration in this method
        # has: `agents/` may not import `tools/` at all. INSIDE the
        # feature gate, so a box with "vision" off carries no
        # registration naming a model whose app registered nothing else.
        from agents.contracts.artifacts import register_artifact_purge

        register_artifact_purge("tools.vision.retention.purge_artifacts")
```

- [ ] **Step 6: Document it in `tools/vision/README.md`**

Say, **once**: this column registers the artifact purge, which maps `output:`/`input:` references and `Turn.data["id"]` generation ids to jobs, dedupes by job and calls `services.delete_jobs`, which in turn calls `delete_job` per job — and `delete_job`'s **best-effort engine-side sweep** (`store.remove_engine_files`, which never raises) is the only reach this platform has into `/engine/output` and `/engine/input`, which are tracked by no row at all; the Engine files page remains the operator's manual door. Do not restate that at any call site. Also add one or two sentences on `delete_jobs` itself, where `delete_job` is already documented in the service-layer list: it is the one place outside `visibility.py` that queries `GenerationJob.objects` directly (IA-1's closed set of two). Also record the accepted residue: a generation whose tool turn was never written is reachable only through the gallery.

**Amended (review round one):** the Deletion section also says, in plain sentences, that the two file-serving routes refuse a ticketed job the same way — reached by its direct URL, a deleted generation's stored image or stored input answers the same 404 as one this principal could never read, so a copied or bookmarked link cannot outlive the delete.

- [ ] **Step 7: Run the tests, in both flag states**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision agents identity` then `FARABUNKER_FEATURES='vision' .venv/bin/pytest -q tools/vision`
Expected: PASS.

- [ ] **Step 8: Run the full gate and commit**

```bash
git add tools/vision/retention.py tools/vision/visibility.py tools/vision/services.py \
        tools/vision/apps.py tools/vision/README.md tools/vision/tests/
git commit -m "feat(vision): the registered artifact purge and the gallery exclusion"
```

---

### Task 12: The Deleted page

**Files:**
- Modify: `identity/views.py` (`deleted_page`, `deleted_restore`, `deleted_purge`)
- Modify: `identity/urls.py`, `identity/routes.py`
- Create: `identity/templates/identity/deleted.html`
- Modify: `foundation/settings_area.py` (the "Your content" group)
- Modify: `foundation/templates/_settings.html` (the same group, the same order)
- Modify: `foundation/settings_help.py` (the Deleted card)
- Modify: `foundation/tests/test_page_names.py` (`_NAMES`)
- Modify: `identity/tests/test_route_matrix.py` (three `_DRIVERS` entries)
- Test: `identity/tests/test_deleted_page.py` (new)

**Held tests — name each in the commit message:**
- `foundation/tests/test_settings_area.py::TestTheTable::test_a_household_box_gets_setup_and_nothing_else` — asserts the exact label list `["Models", "Library", "Chat", "Job execution", "Install guides"]`. **Re-pin:** `"Deleted"` joins it, after `Install guides` (the new group sits after Setup).
- `::test_a_member_gets_only_the_ungated_entry` — asserts `["Install guides"]`. **Re-pin:** `["Install guides", "Deleted"]` — the page is `EVERYONE`, and a member's own deleted items are exactly what a member needs.
- `::test_an_administrator_on_an_accounts_box_gets_every_group` — the full list. **Re-pin:** `"Deleted"` after `Install guides`.
- `foundation/tests/test_shell.py::TestTheSettingsSidebar::test_the_sidebar_is_the_table_and_lands_where_settings_sends_you` — the drift test that walks a rendered sidebar and asserts its first link is where `/settings/` redirects. It must stay green with no edit; if it goes red, the sidebar's order and `SETTINGS_GROUPS`' order disagree and the sidebar is wrong, not the test.
- `::test_a_member_gets_only_what_a_member_can_open` and `::test_a_member_is_not_left_with_an_empty_sidebar` — re-pin to expect the Deleted entry.
- `foundation/tests/test_settings_help.py::TestTheDriftGuard::test_every_settings_entry_has_a_card_and_every_card_has_an_entry`, `::test_every_card_carries_the_same_gate_its_sidebar_entrys_label`, `::test_every_cards_title_is_its_sidebar_entrys_label` — the table is symmetric, so the card lands in the same commit as the entry or all three go red.
- `foundation/tests/test_page_names.py` — every name in `_NAMES` gets a title and an `h1` assertion; `identity-deleted` joins it with the name `"Deleted"`, and goes in `_ADMIN_ONLY` only if an anonymous visitor on an open box cannot render it (it can — the gate is `EVERYONE` and the route is class A, so an open box renders it; leave it out of `_ADMIN_ONLY` and check the test passes).
- `identity/tests/test_route_matrix.py::TestTheTableIsComplete::test_every_route_has_a_driver` — fails immediately for a classified name with no driver. Three drivers land in this commit.
- `identity/tests/test_route_matrix.py::TestTheMatrix` and `::TestThePosturesAgree` — parametrised over every `ROUTE_RULES` name; the three new names are covered automatically once their drivers exist, and class A / O / O is what makes their expected cells correct.

**Interfaces:**
- Consumes: `identity.retention.visible_tickets`, `may_purge`, `restore_content`, `purge_ticket`, `sweep` (Task 5); `identity.audit.by_action` (Task 3); `identity.contracts.retention` copy (Task 1).
- Produces: routes `identity-deleted` (`/identity/deleted/`, class **A**), `identity-deleted-restore` (`/identity/deleted/<int:pk>/restore/`, class **O**), `identity-deleted-purge` (`/identity/deleted/<int:pk>/purge/`, class **O**).

  **The URL path is `/identity/deleted/`, not `/settings/deleted/`** — `identity/urls.py` is mounted at `/identity/` and every other identity settings page (`identity-settings` at `/identity/settings/`) already lives there while appearing in the settings AREA. The spec's `/settings/deleted/` would need a second mount in `config/urls.py` for no gain; the settings area is a registration, not a URL prefix.

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_deleted_page.py
"""The Deleted page: a checkable promise, in every posture."""
from __future__ import annotations

import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from identity.contracts import cascades as cascades_module
from identity.contracts import retention as copy
from identity.contracts.actions import CONTENT_PURGED, CONTENT_RESTORED
from identity.contracts.cascades import RetentionHandler, register_retention_handler
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_conversation, make_user, posture, sign_in, user_principal,
)
from identity import retention as service

pytestmark = pytest.mark.django_db


def noop(key: str) -> int:
    return 0


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save, clear, register, restore -- `identity/tests/
    test_cascades.py::_isolated_registry`'s shape and its reason: the
    registry is a module-level dict with no reset path, and a
    registration escaping this module would reach every later purge in
    the same pytest process. Both collection orders are the gate."""
    saved = dict(cascades_module._RETENTION)
    cascades_module._RETENTION.clear()
    register_retention_handler(RetentionHandler(
        kind=copy.KIND_ASK, key="t.page", label="Ask records",
        handler=f"{__name__}.noop"))
    yield
    cascades_module._RETENTION.clear()
    cascades_module._RETENTION.update(saved)


def _ticket_for(user, *, key="1", label="A question"):
    item = make_conversation(owner_kind="user", owner_key=str(user.pk))
    return service.delete_content(user_principal(user), kind=copy.KIND_ASK,
                                  key=key, owner=item, label=label)


class TestTheDeletedTab:
    def test_it_lists_the_viewers_own_items_with_the_promised_date(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert copy.KIND_LABELS[copy.KIND_ASK] in body
        assert copy.purge_on_line(ticket.purge_on) in body
        assert copy.ACTION_RESTORE in body
        assert copy.ACTION_PURGE in body

    def test_a_member_does_not_see_somebody_elses(self, client):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            _ticket_for(theirs, key="2", label="Their question")
            sign_in(client, mine)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "Their question" not in body

    def test_an_open_box_shows_everyones_because_there_is_nobody_to_hide_from(self, client):
        with posture("open"):
            _ticket_for(make_user(), label="A question")
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A question" in body

    def test_the_page_renders_no_hold_control_in_any_posture(self, client):
        """SCOPED TO THIS PAGE'S OWN CONTENT BLOCK, not the whole
        response: the settings shell, the sidebar and the assistant
        panel are shared markup this page does not own, and a substring
        assertion over them would fail for a word some other surface
        introduced. The enterprise BEHAVIOUR is deferred (spec section
        10.10) and this page must not imply a guarantee that is not
        built."""
        for box in ("open", "personal", "enterprise"):
            with posture(box):
                user = make_user()
                sign_in(client, user)
                _ticket_for(user)
                body = client.get(reverse("identity-deleted")).content.decode()
            main = body.split("<main>", 1)[1].split("</main>", 1)[0].lower()
            assert "hold" not in main, box

    def test_a_get_purges_anything_whose_date_has_passed_before_listing(self, client):
        with posture("open"):
            user = make_user()
            ticket = _ticket_for(user)
            DeletionTicket.objects.filter(pk=ticket.pk).update(
                purge_on=timezone.localdate() - datetime.timedelta(days=1))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert DeletionTicket.objects.count() == 0
        assert "A question" not in body

    def test_an_empty_page_says_so_and_never_500s(self, client):
        with posture("open"):
            response = client.get(reverse("identity-deleted"))
        assert response.status_code == 200


class TestThePurgedTab:
    def test_it_shows_content_free_events_with_the_toggle_off(self, client):
        with posture("open"):
            user = make_user()
            ticket = _ticket_for(user, label="A secret question")
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A secret question" not in body
        assert copy.KIND_LABELS[copy.KIND_ASK] in body

    def test_with_the_toggle_on_the_labels_appear(self, client):
        with posture("open"):
            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()
            user = make_user()
            _ticket_for(user, label="A named question")
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A named question" in body


class TestTheDeletionLogHidesOtherPeoplesLabels:
    """The toggle above (`test_with_the_toggle_on_the_labels_appear`)
    proves the label appears for the item's OWN viewer; this proves it
    stops there. The log itself LISTS every `content.*` event to every
    viewer -- it is a record of what happened, not a per-viewer view of
    it -- but a viewer with no standing to read everyone's content must
    not learn another person's item's own title through it, which is
    exactly what `audit.by_action`'s unscoped read would leak with
    nothing further checking who is looking."""

    def test_a_principal_with_no_standing_over_the_item_sees_no_label(self, client):
        with posture("personal"):
            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()
            owner, viewer = make_user(), make_user()
            _ticket_for(owner, label="A private title")
            sign_in(client, viewer)
            body = client.get(reverse("identity-deleted")).content.decode()
        assert copy.KIND_LABELS[copy.KIND_ASK] in body
        assert "A private title" not in body

    def test_a_sees_all_content_principal_still_sees_it(self, client):
        """PURGED FIRST, so the label can only reach this viewer through
        the log -- once the ticket is gone, `row.ticket.label` on the
        Deleted-items list has nothing left to leak from either."""
        with posture("open"):
            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()
            ticket = _ticket_for(make_user(), label="A private title")
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            body = client.get(reverse("identity-deleted")).content.decode()
        assert "A private title" in body


class TestRestoreAndPurge:
    def test_restore_removes_the_ticket_and_records_the_event(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-restore", args=[ticket.pk]))
        assert response.status_code == 302
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_RESTORED).count() == 1

    def test_purge_destroys_the_content_in_the_request_that_handled_the_click(self, client):
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
        assert DeletionTicket.objects.count() == 0
        assert AuditEvent.objects.filter(action=CONTENT_PURGED).count() == 1

    def test_the_enterprise_posture_behaves_exactly_as_personal_does(self, client):
        """Asserted rather than left untested: the enterprise BEHAVIOUR
        is a deferred slice (spec section 10.10), and until it is built
        the item's owner may purge before the cliff here too."""
        with posture("enterprise"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]))
        assert response.status_code == 302
        assert DeletionTicket.objects.count() == 0

    @pytest.mark.parametrize("route",
                             ["identity-deleted-restore", "identity-deleted-purge"])
    def test_a_forged_id_is_404_never_500(self, client, route):
        with posture("personal"):
            sign_in(client, make_user())
            assert client.post(reverse(route, args=[999999])).status_code == 404

    @pytest.mark.parametrize("route",
                             ["identity-deleted-restore", "identity-deleted-purge"])
    def test_somebody_elses_ticket_is_404_never_403(self, client, route):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            ticket = _ticket_for(theirs, key="2")
            sign_in(client, mine)
            assert client.post(reverse(route, args=[ticket.pk])).status_code == 404

    @pytest.mark.parametrize("exc_cls", [
        "identity.services.ServiceRefused",
        "identity.contracts.retention.RetentionRefused",
    ])
    def test_a_refusal_flashes_and_redirects_for_either_refusal_type(
            self, client, monkeypatch, exc_cls):
        """BOTH types, because the view catches both: this column's own
        `ServiceRefused`, and the `RetentionRefused` a handler in a
        column that may not import `identity.services` raises."""
        from django.utils.module_loading import import_string
        from identity import views
        refusal = import_string(exc_cls)
        monkeypatch.setattr(
            views.retention, "purge_ticket",
            lambda *a, **k: (_ for _ in ()).throw(
                refusal("a worker holds this job")))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
        assert response.status_code == 200
        assert "a worker holds this job" in response.content.decode()
        assert DeletionTicket.objects.count() == 1

    def test_a_broken_purge_flashes_the_fixed_sentence_and_keeps_the_ticket(
            self, client, monkeypatch):
        """THE OTHER CATCH -- the bare `except Exception` in
        `deleted_purge`, for a failure with no operator-readable sentence
        of its own (neither refusal type above)."""
        from identity import views
        monkeypatch.setattr(
            views.retention, "purge_ticket",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a database hiccup")))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-purge", args=[ticket.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert views._PURGE_FAILED_MESSAGE in response.content.decode()
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()

    def test_a_broken_restore_flashes_the_fixed_sentence_and_keeps_the_ticket(
            self, client, monkeypatch):
        """`deleted_restore`'s own bare `except Exception` -- the
        identical never-500 shape, for the mutation with nothing of its
        own to refuse."""
        from identity import views
        monkeypatch.setattr(
            views.retention, "restore_content",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a database hiccup")))
        with posture("personal"):
            user = make_user()
            sign_in(client, user)
            ticket = _ticket_for(user)
            response = client.post(
                reverse("identity-deleted-restore", args=[ticket.pk]), follow=True)
        assert response.redirect_chain[0][1] == 302
        assert views._RESTORE_FAILED_MESSAGE in response.content.decode()
        assert DeletionTicket.objects.filter(pk=ticket.pk).exists()


class TestTheQueryCost:
    def test_the_page_costs_the_same_queries_at_one_ticket_and_at_many(self, client):
        """THE FLAT-QUERY PIN. `deleted_page` reads `IdentitySettings`
        once and threads that SAME row through `principal_for_request`,
        `visible_tickets` and every row's own `may_purge(...,
        settings_row=row)` call -- so a row-per-ticket loop must not cost
        a settings read per row.

        NON-VACUOUS BY CONSTRUCTION: dropping `settings_row=row` from
        the view's own `may_purge` call -- the obvious wrong
        implementation, and the one this page had before `may_purge`
        grew the keyword -- costs one extra settings-row read per
        ticket, so ten tickets fails this equality by nine queries, not
        by a rounding error.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture("open"):
            _ticket_for(make_user(), key="one")
            client.get(reverse("identity-deleted"))  # warm the session reads
            with CaptureQueriesContext(connection) as one_ticket:
                assert client.get(reverse("identity-deleted")).status_code == 200
            for index in range(1, 10):
                _ticket_for(make_user(), key=f"many-{index}")
            with CaptureQueriesContext(connection) as many_tickets:
                body = client.get(reverse("identity-deleted")).content.decode()

        assert len(many_tickets) == len(one_ticket), (
            len(one_ticket), len(many_tickets), [q["sql"] for q in many_tickets])
        # Really ten rendered rows, not two empty pages agreeing by
        # accident: `copy.ACTION_PURGE` is offered per row an admin may
        # act on, and this admin `sees_all_content` on an open box.
        assert body.count(copy.ACTION_PURGE) == 10

    def test_the_get_reads_identitysettings_exactly_once(self, client):
        """THE TRUTH BEHIND `deleted_page`'s OWN "one read" CLAIM.
        `IdentityGateMiddleware` already reads `IdentitySettings` once
        per request (`identity/tests/test_middleware.py::
        TestTheSingleRowRead`'s own pin); `settings_row_for(request)`
        reuses that SAME row rather than the view fetching a second copy
        of its own with `IdentitySettings.get_solo()` -- the obvious
        wrong implementation, and the one this view had before this
        fix, which names the same table a second time and would fail
        this equality at 2, not 1.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with posture("open"):
            _ticket_for(make_user())
            client.get(reverse("identity-deleted"))  # warm the session reads
            with CaptureQueriesContext(connection) as context:
                assert client.get(reverse("identity-deleted")).status_code == 200

        reads = sum(
            1 for q in context.captured_queries if "identity_identitysettings" in q["sql"])
        assert reads == 1, [q["sql"] for q in context.captured_queries]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_deleted_page.py`
Expected: FAIL — `NoReverseMatch: Reverse for 'identity-deleted' not found`.

- [ ] **Step 3: Write the three views**

In `identity/views.py`, import the service and the copy, and add after `settings_page`:

```python
from identity import retention
from identity.access import sees_all_content
from identity.contracts import retention as retention_copy
from identity.contracts.actions import (
    CONTENT_DELETED, CONTENT_PURGED, CONTENT_RESTORED,
)


# The three sentences the Deletion log reads, declared once, in Python.
_EVENT_VERBS = {
    CONTENT_DELETED: "deleted",
    CONTENT_RESTORED: "restored",
    CONTENT_PURGED: "destroyed",
}

# NEVER-500 FLASHES. A retention handler's own refusal (`ServiceRefused`,
# `RetentionRefused`) carries its own operator-readable sentence and is
# flashed verbatim; these two are the fallback for anything else a
# handler or the restore path might raise -- a bare exception says
# nothing safe to show a person, so the flash names no detail and the
# traceback goes to the log instead, keyed on ids only.
_PURGE_FAILED_MESSAGE = (
    "That could not be deleted permanently just now. It is still in "
    "Deleted, and it will be tried again."
)
_RESTORE_FAILED_MESSAGE = (
    "That could not be restored just now. It is still in Deleted "
    "— try again in a moment."
)


def deleted_page(request):
    """GET /identity/deleted/ -- "Deleted": what this viewer has deleted,
    when it will be destroyed, and the two controls that change that.

    CLASS A, gated EVERYONE. Every other settings entry is ADMIN or
    ACCOUNTS_ADMIN; this page is a person's OWN deleted items, and on a
    box with accounts a member is exactly who needs it. The one
    consequence, recorded rather than discovered: the sidebar renders on
    the single PUBLIC page in the settings area (`setup-index`), so an
    anonymous visitor sees the entry and is redirected to sign in when
    they click it -- the same thing every app-bar link on that page
    already does.

    A SWEEP RUNS FIRST, before the list is built, so this page can never
    show a row whose promised date has passed. It is one of the three
    callers (a delete, this GET, and `manage.py purge_deleted`); there
    is no scheduler and no new job kind.

    NO HOLD CONTROL IS RENDERED IN ANY POSTURE, including enterprise,
    and this page says nothing about holds or about a purge an owner
    cannot perform: that behaviour is a deferred slice (spec section
    10.10) and the page must not imply a guarantee that is not built.

    ONE `IdentitySettings` READ FOR THE WHOLE REQUEST, and it is not
    this view's own: `settings_row_for(request)` reads
    `IdentityGateMiddleware`'s own already-fetched row off the request
    rather than calling `IdentitySettings.get_solo()` a second time, and
    that one row is threaded through `principal_for_request`,
    `visible_tickets`, `sees_all_content` and every row's own
    `may_purge` call -- the same per-request-reuse norm `entitlement_edit`
    already follows -- so a row-per-ticket loop costs no per-row settings
    query, and this GET costs no settings read beyond the middleware's
    own.

    THE LOG NAMES NOBODY'S ITEM THIS VIEWER COULD NOT ALREADY READ.
    `show_labels` is `sees_all_content(principal, settings_row=row)`,
    computed once: every event still LISTS for every viewer (the log is
    a record of what happened, not a per-viewer view of it), but its
    `target_label` -- written only when `audit_detail` was on at write
    time -- reaches the template only for a principal who could already
    read everyone's content. Anybody else sees the same event with the
    label blanked, exactly as an event carries no label at all when
    `audit_detail` was off when it was written.
    """
    retention.sweep()
    row = settings_row_for(request)
    principal = principal_for_request(request, settings_row=row)
    show_labels = sees_all_content(principal, settings_row=row)
    tickets = [
        {
            "ticket": ticket,
            "kind_label": retention_copy.KIND_LABELS.get(ticket.kind, ticket.kind),
            "purge_on_line": retention_copy.purge_on_line(ticket.purge_on),
            "may_purge": retention.may_purge(principal, ticket, settings_row=row),
        }
        for ticket in retention.visible_tickets(principal, settings_row=row)
    ]
    # BUILT HERE, NOT IN THE TEMPLATE. A Django template cannot index a
    # dict by a variable, and the log needs one plain sentence per event
    # chosen from three actions -- so the choosing happens in Python,
    # where every other user-facing sentence in this repository is
    # declared, and the template renders fields.
    events = [
        {
            "kind_label": retention_copy.KIND_LABELS.get(event.target_type,
                                                         event.target_type),
            "key": event.target_key,
            "label": event.target_label if show_labels else "",
            "verb": _EVENT_VERBS[event.action],
            "actor_label": event.actor_label,
            "at": event.at,
        }
        for event in audit.by_action(
            (CONTENT_PURGED, CONTENT_DELETED, CONTENT_RESTORED))
    ]
    return render(request, "identity/deleted.html", {
        "tickets": tickets,
        "events": events,
        "copy": retention_copy,
    })


def _own_ticket_or_404(request, pk: int, *, settings_row):
    """The addressed ticket, if this principal has standing over it.

    404, NEVER 403 -- the class-O shape: a 403 on a row-addressed URL
    confirms the row exists. Serves the double-click/raced-sweep case
    too -- a ticket a sweep already purged between page-load and click
    is simply a ticket `visible_tickets` no longer lists, so this is the
    one lookup both `deleted_restore` and `deleted_purge` need.

    `settings_row` IS REQUIRED, NOT OPTIONAL, and that asymmetry with
    `deleted_page`'s own no-argument reads is deliberate: both callers
    already hold the request's one `IdentitySettings` row
    (`settings_row_for(request)`) before reaching here, and threading it
    into `principal_for_request` rather than calling that bare is the
    same "the caller that already paid for the read passes it on" rule
    `entitlement_edit`'s own comment states in full.
    """
    principal = principal_for_request(request, settings_row=settings_row)
    ticket = retention.visible_tickets(principal, settings_row=settings_row).filter(pk=pk).first()
    if ticket is None:
        raise Http404("No such deleted item.")
    return principal, ticket


@require_POST
def deleted_restore(request, pk: int):
    """POST /identity/deleted/<pk>/restore/ -- put the item back.

    NEVER-500: `restore_content` is a plain delete-and-audit and has
    nothing of its own to refuse, but a caller here still catches
    anything it might raise rather than let a database hiccup turn a
    settings-area click into a traceback. The ticket survives either
    way -- restore never removes content, so there is nothing to retry
    beyond the click itself.
    """
    row = settings_row_for(request)
    principal, ticket = _own_ticket_or_404(request, pk, settings_row=row)
    try:
        retention.restore_content(principal, ticket)
    except Exception:  # noqa: BLE001 -- never-500; the traceback goes to the log
        logger.exception(
            "identity.views: restore failed for ticket %s", ticket.pk)
        messages.error(request, _RESTORE_FAILED_MESSAGE)
    else:
        messages.info(request, "Restored.")
    return settings_redirect(request, "identity-deleted")


@require_POST
def deleted_purge(request, pk: int):
    """POST /identity/deleted/<pk>/purge/ -- destroy the content now,
    inside this request.

    SYNCHRONOUS: `purge_ticket` runs every registered handler inside one
    `transaction.atomic()` and returns before the redirect. No queue
    job, no worker hop, no `on_commit` hook, no cache -- so after the
    redirect the item is absent from every surface on this box.

    THREE OUTCOMES, NONE OF THEM A TRACEBACK. A REFUSAL -- this column's
    own `ServiceRefused`, or the `RetentionRefused` a retention handler
    in a column that may not import `identity.services` raises instead
    -- carries its own operator-readable sentence, flashed verbatim; the
    ticket stays and the item stays invisible. ANY OTHER EXCEPTION a
    handler leaves behind is logged with `logger.exception`, keyed on
    ids only -- never this item's label or content -- and answered with
    a fixed, contentless sentence; the ticket stays for the next sweep
    or the next click either way. And a clean run flashes success. A
    caller with no standing to purge (`may_purge` refuses) is a 404,
    like every other row this principal may not act on.
    """
    row = settings_row_for(request)
    principal, ticket = _own_ticket_or_404(request, pk, settings_row=row)
    if not retention.may_purge(principal, ticket, settings_row=row):
        raise Http404("No such deleted item.")
    try:
        retention.purge_ticket(principal, ticket)
    except (services.ServiceRefused, RetentionRefused) as exc:
        messages.error(request, str(exc))
    except Exception:  # noqa: BLE001 -- never-500; the traceback goes to the log
        logger.exception(
            "identity.views: purge failed for ticket %s", ticket.pk)
        messages.error(request, _PURGE_FAILED_MESSAGE)
    else:
        messages.info(request, "Deleted permanently.")
    return settings_redirect(request, "identity-deleted")
```

`RetentionRefused` is imported at module top alongside the rest: `from identity.contracts.retention import RetentionRefused`. `require_POST` comes from `django.views.decorators.http`; check whether `identity/views.py` already imports it and add it if not. `settings_row_for` (`identity.request`) is already imported for `entitlement_edit`'s own use. A `logger = logging.getLogger(__name__)` module-level logger is needed for the two `logger.exception` calls above, if `identity/views.py` does not already carry one.

`identity.retention.may_purge` gains an optional `settings_row=None` keyword, the same shape `visible_tickets` already takes, so `deleted_page`'s row-per-ticket loop threads the one request-wide row through it instead of each call re-fetching `IdentitySettings` on its own no-argument form.

- [ ] **Step 4: Mount the routes and classify them**

`identity/urls.py`:

```python
    path("deleted/", deleted_page, name="identity-deleted"),
    path("deleted/<int:pk>/restore/", deleted_restore, name="identity-deleted-restore"),
    path("deleted/<int:pk>/purge/", deleted_purge, name="identity-deleted-purge"),
```

`identity/routes.py::ROUTE_RULES`, in the IA-2 block:

```python
    # --- /identity/deleted/ (deletion semantics, 2026-09-21) -----------
    # A: the page lists the viewer's OWN tickets and addresses no row in
    # its URL -- the identical shape `chat-all` carries. Gated EVERYONE
    # in the settings area, because a member's own deleted items are
    # exactly what a member needs.
    "identity-deleted": "A",
    # O: row-addressed mutations of OWNED content, refused with 404 for a
    # principal with no standing -- the shape `chat-conversation-delete`
    # already has.
    "identity-deleted-restore": "O",
    "identity-deleted-purge": "O",
```

`identity/tests/test_route_matrix.py::_DRIVERS`, beside the other identity entries — the world needs a ticket the class-O cells can address, so add one to `_build_world` (a `DeletionTicket` owned by `w.other`, kind `conversation`) and:

**The ticket must NOT address `w.conversation` itself.** A first pass that pointed it there (the natural reading of "kind `conversation`, key `str(w.conversation.pk)`") turned 24 unrelated cells red: `agents.visibility.visible_conversations` excludes every ticketed key (`identity.retention.ticketed_keys`), so ticketing the world's own shared conversation made it disappear for every OTHER driver that addresses it (`chat-conversation`, `chat-turn`, `chat-conversation-archive`, `chat-workstream-consolidate`, the sidebar-menu actions, and the rest) — their admin-content-on cells started answering 404 where they expect admission. The ticket instead addresses a DEDICATED conversation built solely for it (`ticket_conversation = make_conversation(agent=agent, **owner_fields(principal))`, owned by `other`, never shared with any other driver), and `purge_on` is thirty days out so no incidental sweep (the `identity-deleted` GET cell's own) removes it before the row-addressed cells get to address it:

```python
    "identity-deleted": lambda w: ("get", reverse("identity-deleted"), {}),
    "identity-deleted-restore": lambda w: (
        "post", reverse("identity-deleted-restore", args=[w.ticket.pk]), {}),
    "identity-deleted-purge": lambda w: (
        "post", reverse("identity-deleted-purge", args=[w.ticket.pk]), {}),
```

- [ ] **Step 5: Write the template**

`identity/templates/identity/deleted.html`. Two tabs are two `<section>`s on one page — no script, no `<details>` state to keep, progressive enhancement by construction. It re-types nothing `_settings.html` already owns (`section.settings`, `.msg`, `.warn`, `.muted`):

```html
{% extends "_settings.html" %}
{% comment %}
"Deleted" (deletion semantics, 2026-09-21): a person's own deleted
items, with the date each will be destroyed and the two controls that
change that.

TWO SECTIONS, NOT TABS WITH STATE. The page is short, the second half is
a log, and a disclosure that had to remember which half was open would be
client state this box does not keep. Both render, always.

NO HOLD CONTROL, IN ANY POSTURE, and nothing here mentions a records
obligation: the enterprise behaviour is deferred (spec section 10.10) and
this page must not imply a guarantee that is not built.

HEADINGS, BUTTONS AND THE DATE LINE COME FROM
`identity/contracts/retention.py` through the `copy` context key --
declared once in Python, never typed twice. The page's OWN sentences
(the tagline, the confirm prompt, the two `helptext`s) are typed here,
same as every other settings leaf's own prose.

THE WORD "PURGE" NEVER APPEARS HERE. `copy.ACTION_PURGE` says "Delete
permanently" and `copy.purge_on_line` says "Purge on <date>" -- the one
place the word survives, because it names a promise a person can check
against a calendar, not a mechanism. The URL path segment and the route
names are not rendered copy and stay as they are.

The delete confirm is a native `<details>`, modelled on
`chat/_thread_actions.html` and `vision/_delete_control.html`: no
`confirm()`/`alert()`/`prompt()` anywhere on this box.
{% endcomment %}
{% block title %}{{ copy.PAGE_TITLE }} &mdash; farabunker{% endblock %}
{% block side_current_deleted %}current{% endblock %}
{% block extra_style %}{{ block.super }}
  .item { display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap;
          padding: .5rem 0; border-top: 1px solid var(--border); }
  .item:first-of-type { border-top: 0; }
  .item .grow { flex: 1 1 12rem; min-width: 0; overflow-wrap: anywhere; }
  .item form { display: inline; }
  .confirm { display: flex; align-items: baseline; gap: .6rem;
             flex-wrap: wrap; padding: .4rem 0 .2rem; }
  .event { font-size: .85rem; color: var(--muted); padding: .2rem 0; }
{% endblock %}
{% block settings_content %}
<main>
  <h1>{{ copy.PAGE_TITLE }}</h1>
  <p class="tagline">Everything you have deleted, and the date each one will be destroyed. Until that date you can put it back.</p>

  {% include "_messages.html" %}

  <section class="settings" id="deleted-items">
    <h2>{{ copy.PAGE_TITLE }}</h2>
    {% for row in tickets %}
    <div class="item">
      <span class="grow">
        <strong>{{ row.kind_label }}</strong>
        {% if row.ticket.label %}&mdash; {{ row.ticket.label }}{% endif %}
      </span>
      <span class="muted">{{ row.purge_on_line }}</span>
      <form method="post" action="{% url 'identity-deleted-restore' row.ticket.pk %}{{ assistant.open_query }}">
        {% csrf_token %}
        <button type="submit" class="secondary">{{ copy.ACTION_RESTORE }}</button>
      </form>
      {% if row.may_purge %}
      <details>
        <summary>{{ copy.ACTION_PURGE }}</summary>
        <div class="confirm">
          <span class="muted">Destroy this now, before its date? This cannot be undone.</span>
          <form method="post" action="{% url 'identity-deleted-purge' row.ticket.pk %}{{ assistant.open_query }}">
            {% csrf_token %}
            <button type="submit" class="danger">Yes, {{ copy.ACTION_PURGE|lower }}</button>
          </form>
        </div>
      </details>
      {% endif %}
    </div>
    {% empty %}
    <p class="muted">Nothing deleted.</p>
    {% endfor %}
  </section>

  <section class="settings" id="deletion-log">
    <h2>{{ copy.TAB_LOG }}</h2>
    <p class="helptext">A record of what was deleted, restored and destroyed &mdash; who, what kind, and when. Content-free by default: item names appear only when the setting to show them was on when the entry was written.</p>
    {% for event in events %}
    <p class="event">
      {{ event.kind_label }} {{ event.key }}
      {% if event.label %}&mdash; {{ event.label }}{% endif %}
      &middot; {{ event.verb }} by {{ event.actor_label }}
      &middot; {{ event.at|date:"j M Y H:i" }}
    </p>
    {% empty %}
    <p class="muted">Nothing yet.</p>
    {% endfor %}
  </section>
</main>
{% endblock %}
```

`id="deleted-items"`/`id="deletion-log"` are the anchors `foundation/settings_help.py`'s own `HelpField`s cite for this page (Step 6). `event.label` is already blanked in Python (`deleted_page`'s own `show_labels` gate) before it ever reaches this template -- the template renders whatever field it is given and asks no question of its own about who is looking.

Every value the template reads is a field on a dict the view built — no dict indexed by a variable, no logic in the markup.

`.confirm` is this leaf's own class, declared in its `extra_style` above. It is deliberately **not** `.delete-confirm`: that name is owned by the chat templates, and `foundation/ops/tests/test_css_ownership.py`'s placement rule puts a selector's home at the deepest template that is an ancestor of every template using it — which for a chat class and a settings leaf is neither of them. Check that gate's current scope before choosing, and if `.delete-confirm` turns out to live somewhere both can reach, use it and delete the local rule.

- [ ] **Step 6: Register the entry in all five places**

`foundation/settings_area.py::SETTINGS_GROUPS`, a new group **after `Setup` and before `Access`**:

```python
    ("Your content", (
        # EVERYONE, not ADMIN: every other settings entry is operator
        # policy; this one is a person's own deleted items, and on a box
        # with accounts a member is exactly who needs it. The one
        # consequence is recorded at `identity.views.deleted_page`.
        Entry("Deleted", "identity-deleted", EVERYONE),
    )),
```

`foundation/templates/_settings.html`, the same group in the same order, ungated like `Install guides` — placed between the `Setup` group `</div>` and the `{% if identity_posture != "open" and identity_is_admin %}` that opens Access:

```html
    <div class="settings-group">
      <span class="nav-group-label">Your content</span>
      <a href="{% url 'identity-deleted' %}{{ assistant.open_query }}" class="{% block side_current_deleted %}{% endblock %}">Deleted</a>
    </div>
```

`foundation/settings_help.py`, one `HelpCard` with `route_name="identity-deleted"`, `title="Deleted"`, `gate=EVERYONE`, a `purpose` saying what the page is for, and fields whose anchors exist on the rendered page. It says what Restore and Delete permanently do, **that the removal date is fixed at delete time so a changed setting governs future deletions only**, and **that backups are a separate layer the date does not reach**. It says nothing about holds. Name the setting by its own field label in quotes (`"Keep deleted items for"`) rather than the word "retention" -- that word is as banned from this card's rendered prose as "purge"/"ticket"/"cliff" are, the identical copy constraint `identity/contracts/retention.py`'s own module docstring states for the page itself.

`foundation/tests/test_page_names.py::_NAMES` — `"identity-deleted": "Deleted"`.

`identity/routes.py` — done in Step 4.

- [ ] **Step 7: Re-pin the drift tests**

Work through this task's held-test list. Every one of them is a list or a symmetry assertion, and each gains exactly one entry.

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/pytest -q identity foundation`
Expected: PASS.

- [ ] **Step 9: Run the full gate and commit**

```bash
git add identity/views.py identity/urls.py identity/routes.py \
        identity/templates/identity/deleted.html foundation/settings_area.py \
        foundation/templates/_settings.html foundation/settings_help.py \
        foundation/tests/ identity/tests/
git commit -m "feat(identity): the Deleted page, in every posture

Re-pins foundation/tests/test_settings_area.py::TestTheTable's three label lists, foundation/tests/test_shell.py::TestTheSettingsSidebar's member cases, foundation/tests/test_settings_help.py::TestTheDriftGuard, foundation/tests/test_page_names.py::_NAMES and identity/tests/test_route_matrix.py::TestTheTableIsComplete."
```

---

### Task 13: The deletion-coverage gate

**Files:**
- Create: `foundation/ops/tests/test_deletion_coverage.py`

**Interfaces:**
- Consumes: `django.apps.apps.get_models()`; `identity.contracts.cascades.retention_handlers`; `identity.access.owner_fields` (as the documented source of the owner-column pair).
- Produces: nothing importable — a gate.

**In plain words: a future feature that stores content somewhere new must wire it into deletion, or the build fails.** This design's weakest point is not any mechanism in it; it is the year after it merges, when somebody adds a table that holds what a person typed and nobody remembers that deletion is a registry you have to join. A test is the only form of that reminder that cannot be forgotten.

- [ ] **Step 1: Write the gate**

```python
# foundation/ops/tests/test_deletion_coverage.py
"""A model that holds user content must be reachable by a deletion.

THIS GATE EXISTS SO A FEATURE THAT STARTS STORING CONTENT SOMEWHERE NEW
CANNOT SHIP WITHOUT JOINING THE DELETION REGISTRY. It fails on two
conditions, and only two:

  (a) a model in `_COVERED` whose ticket kind has NO registered
      retention handler, or whose registered handler does not resolve --
      which catches a handler deleted, renamed, or dropped from an
      `AppConfig.ready()`;

  (b) a model carrying the `owner_kind`/`owner_key` PAIR that is in
      neither `_COVERED` nor `_EXEMPT`. The pair is the
      marker because `identity.access.owner_fields` is the ONE
      definition of how ownership is stamped in this codebase -- its own
      docstring records that it was moved there so every owned table in
      every column shares one definition -- and the walk is over
      `apps.get_models()`, so a new owned table is seen the day it is
      added, with no list to update first.

WHAT IT DOES NOT DO: it runs no purge, touches no database beyond model
introspection, and asserts nothing about whether a handler is CORRECT --
each column's own tests do that. It asserts that the WIRING EXISTS,
which is the failure mode that arrives silently.

AN EXEMPTION IS A SENTENCE SOMEBODY WRITES AND A REVIEWER READS. That is
the point of making it a list rather than a default.

IT LIVES IN `foundation/ops/tests/` beside `test_import_law.py` and
`test_column_boundaries.py`, because `foundation.ops` is the app that
already reaches across every column by design and this is a repo-wide
structural assertion.
"""
from __future__ import annotations

import pytest
from django.apps import apps
from django.utils.module_loading import import_string

from identity.access import owner_fields
from identity.contracts.cascades import retention_handlers
from identity.contracts.principals import OPEN_PRINCIPAL
from identity.contracts.retention import (
    KIND_ASK, KIND_CONVERSATION, KIND_DOCUMENT, KIND_VISION_JOB,
)

# Every model carrying user content, mapped to the ticket kinds whose
# registered handlers reach it. Written from the content inventory in
# the design spec, section 2.1.
_COVERED: dict[str, tuple[str, ...]] = {
    "agents.Conversation": (KIND_CONVERSATION,),
    "agents.Turn": (KIND_CONVERSATION,),
    "agents.ToolInvocation": (KIND_CONVERSATION,),
    # A chat-scoped document dies with its conversation through the
    # attachment seam; a library document has its own kind in slice 2.
    "rag.Document": (KIND_CONVERSATION,),
    "rag.DocumentRow": (KIND_CONVERSATION,),
    "rag.DocumentAttachment": (KIND_CONVERSATION,),
    "jobs.InferenceJob": (KIND_CONVERSATION,),
}

# Owned tables a deletion does not reach, one reasoned line each -- and
# an exemption is a SENTENCE SOMEBODY WRITES AND A REVIEWER READS, which
# is the point of making it a list rather than a default.
_EXEMPT: dict[str, str] = {
    "agents.Agent": "an agent definition is a setting a person authored, "
                    "deleted from its own page, not content a deletion reaches",
    "agents.Flow": "a flow definition, for the same reason as Agent above",
    "agents.Workstream": "a container, deliberately not a ticket kind -- its "
                         "contents each have their own cliff (spec section 10.2)",
    "identity.DeletionTicket": "the deletion bookkeeping itself -- its owner "
                               "columns name the ITEM's owner, and the ticket is "
                               "destroyed by the purge it records",
    "rag.AskRecord": "gains a registered handler in Slice 2",
    "vision.GenerationJob": "gains a registered handler in Slice 2",
}


def _label(model) -> str:
    return f"{model._meta.app_label}.{model.__name__}"


def _owned_models() -> list[str]:
    """Every model carrying BOTH owner columns, discovered by walking the
    app registry -- so a new owned table is seen the day it is added."""
    marker = tuple(owner_fields(OPEN_PRINCIPAL))
    found = []
    for model in apps.get_models():
        names = {field.name for field in model._meta.get_fields()
                 if hasattr(field, "attname")}
        if all(column in names for column in marker):
            found.append(_label(model))
    return sorted(found)


def test_the_owner_marker_is_the_one_definition_of_ownership():
    """Anti-vacuous: if `owner_fields` ever stopped returning those two
    keys, `_owned_models` would silently match everything or nothing."""
    assert tuple(owner_fields(OPEN_PRINCIPAL)) == ("owner_kind", "owner_key")


def test_the_walk_actually_finds_the_owned_tables():
    """Anti-vacuous the other direction: a discovery that quietly stopped
    seeing a column would pass every assertion below."""
    owned = _owned_models()
    assert "agents.Conversation" in owned
    assert "rag.Document" in owned
    assert len(owned) >= 4


@pytest.mark.parametrize("label", sorted(_COVERED))
def test_every_covered_model_has_a_registered_handler_that_resolves(label):
    for kind in _COVERED[label]:
        handlers = retention_handlers(kind)
        assert handlers, (
            f"{label} is covered by the {kind!r} kind, but no retention handler "
            f"is registered for it. Register one from the owning column's "
            f"AppConfig.ready(), or move the model to _EXEMPT with a reason.")
        for spec in handlers:
            import_string(spec.handler)


def test_every_owned_model_is_covered_or_exempt():
    """CONDITION (b): a new table holding what a person typed must join
    the deletion registry, or say in one line why it does not."""
    unaccounted = sorted(set(_owned_models()) - set(_COVERED) - set(_EXEMPT))
    assert unaccounted == [], (
        "these owned models are in neither _COVERED nor _EXEMPT -- wire each "
        "into deletion with a retention handler, or add it to _EXEMPT with "
        "one line saying why a deletion never reaches it: "
        f"{unaccounted}")


def test_the_two_lists_do_not_overlap():
    assert not set(_COVERED) & set(_EXEMPT)


def test_every_exemption_carries_a_reason():
    for label, reason in _EXEMPT.items():
        assert reason.strip(), label


def test_the_gate_would_actually_catch_a_missing_handler():
    """The failure this exists for, exercised: a kind nothing has
    registered for answers an empty list."""
    assert retention_handlers("not-a-kind") == []
```

`KIND_ASK`, `KIND_DOCUMENT` and `KIND_VISION_JOB` are unused until Task 23 fills `_COVERED` — import only `KIND_CONVERSATION` for now and add each of the other three back in Task 23 with its entry. There is no linter, but an unused import in a gate is a reader asking "what for".

**App labels, verified against the tree:** `agents` (`agents/apps.py::AgentsConfig.label`), `rag` (`tools/rag/apps.py::RagConfig.label`), `vision` (`tools/vision/apps.py::VisionConfig.label`), `jobs` (`models/queue/apps.py::JobsConfig.label`) and `identity`. `_COVERED` and `_EXEMPT` spell those, never the dotted module path.

- [ ] **Step 2: Run it**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_deletion_coverage.py` then again with `FARABUNKER_FEATURES='vision'`
Expected: PASS in both. `_EXEMPT` may name a model that is not installed in a given flag state — the two lists are compared against what `apps.get_models()` actually returns, so an absent model simply never appears in `unaccounted`.

- [ ] **Step 3: Confirm the guards it sits beside are still green**

Run: `.venv/bin/pytest -q foundation/ops/tests`
Expected: PASS — in particular `test_column_boundaries.py`'s AST audit guard (`identity/audit.py` is still the ONLY module performing any `AuditEvent.objects` access — the new `by_action` reader is inside it precisely so this stays true) and `test_import_law.py` (no new file imports `models.queue.models`).

- [ ] **Step 4: Commit**

```bash
git add foundation/ops/tests/test_deletion_coverage.py
git commit -m "test(foundation): the deletion-coverage gate"
```

---

### Task 14: The demo, as one end-to-end test

**Files:**
- Create: `identity/tests/test_deletion_demo.py`

**Interfaces:**
- Consumes: everything Tasks 1–13 built, through the real views only — this test posts to routes and reads querysets; it calls no handler directly.

**The queue half is not landed.** Two assertions in this test say so **explicitly**, so the state is pinned rather than ambiguous, and Task 19 flips exactly those two:
- `test_the_queue_row_survives_until_the_queue_half_lands` asserts the `agent.turn` row **still exists** after a permanent delete, with a docstring naming the task that will change it. Task 19 renames it to `test_the_queue_row_is_gone` and inverts the assertion.
- `test_the_generations_queue_row_survives_until_the_queue_half_lands` does the same for the `vision.generate` row.

Nothing else in this module changes when the gate is passed.

- [ ] **Step 1: Write the demo**

```python
# identity/tests/test_deletion_demo.py
"""The demo, end to end, in the personal posture.

ONE TEST CLASS, ONE STORY: a conversation with a turn, a chat-scoped
document, a generated image and an Ask record; delete it; check it is
invisible and dated; delete it permanently; check that after the
redirect NO SURFACE ON THIS BOX renders any part of it.
"""
from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.urls import reverse
from django.utils import timezone

from agents.models import Conversation, ToolInvocation, Turn
from agents.visibility import visible_conversations
from identity.contracts import retention as copy
from identity.contracts.actions import CONTENT_DELETED, CONTENT_PURGED
from identity.models import AuditEvent, DeletionTicket, IdentitySettings
from identity.tests._helpers import (
    make_conversation, make_document, make_generation, make_output,
    make_queue_job, make_turn, make_user, posture, sign_in, user_principal,
)
from tools.rag.access import readable_documents, visible_ask_records
from tools.rag.models import AskRecord, Document, DocumentAttachment

pytestmark = pytest.mark.django_db

InferenceJob = apps.get_model("jobs.InferenceJob")


@pytest.fixture
def world(tmp_path, settings):
    """A conversation carrying every kind of content this box stores.

    EVERY ROW COMES FROM AN EXISTING BUILDER -- `identity/tests/
    _helpers.py`'s, which resolve through `apps.get_model` precisely
    because this module spans every column. No new builder, no
    `conftest.py`.
    """
    settings.NOTES_DIR = tmp_path

    user = make_user()
    owner = {"owner_kind": "user", "owner_key": str(user.pk)}

    conversation = make_conversation(title="A thread", **owner)

    # The generated image, its output row, and the queue row that made it.
    generation_queue_job = make_queue_job(
        kind="vision.generate", state="succeeded", priority=200,
        payload={"operation": "txt2img", "params": {"prompt": "a lighthouse"}})
    job = make_generation(queue_job_id=generation_queue_job.pk, **owner)
    output = make_output(job=job)

    # The tool record whose words the purge scrubs, and the tool turn
    # that reaches BOTH channels: an artifact reference AND a generation
    # id in `data`.
    invocation = ToolInvocation.objects.create(
        principal_kind="user", principal_key=str(user.pk),
        tool_key="vision__generate", args={"prompt": "a lighthouse"},
        text="Generated 1 image.", error="", outcome="ok")
    make_turn(conversation=conversation, index=0, role="user",
              text="draw me a lighthouse")
    make_turn(conversation=conversation, index=1, role="tool",
              text="Generated 1 image.",
              artifacts=[f"output:{output.pk}"],
              data={"id": str(job.pk), "status": "succeeded"},
              invocation_id=invocation.pk)

    # The chat-scoped document, its attachment claim, and its chunks.
    document = make_document(scope="conversation", **owner)
    DocumentAttachment.objects.create(document=document,
                                      conversation_id=conversation.id)
    index_chunks_for(document)

    # The staging note the consolidation path writes.
    note_path = tmp_path / f"{conversation.id}.md"
    note_path.write_text("a consolidated stream", encoding="utf-8")
    Document.objects.filter(pk=document.pk).update(
        notes_conversation_id=conversation.id)

    # The queue row carrying the person's literal message.
    turn_queue_job = make_queue_job(
        kind="agent.turn", state="succeeded", priority=100,
        payload={"conversation": str(conversation.id), "turn": 1,
                 "agent": "assistant", "text": "draw me a lighthouse"})

    ask = AskRecord.objects.create(
        question="what is in the lighthouse report",
        answer="a lighthouse", citations=[], connection_name="a connection",
        model_id="an-identifier", **owner)

    return SimpleNamespace(
        user=user,
        conversation=conversation,
        invocation=invocation,
        document=document,
        job=job,
        output=output,
        ask=ask,
        note_path=note_path,
        turn_queue_job=turn_queue_job,
        generation_queue_job=generation_queue_job,
        chunk_count_for_document=lambda: chunk_count_for(document.pk),
    )
```

`index_chunks_for` / `chunk_count_for` stand for the two real functions in `tools/rag/index.py` that write and count chunks by `metadata_->>'file_id'` — read that module and use their actual names (`delete_chunks_for_document`'s own key is the one to count on). `SimpleNamespace` comes from `types`; `DocumentAttachment`, `Document` and `AskRecord` from `tools.rag.models`; `ToolInvocation` from `agents.models`.

```python
class TestTheDemo:
    def test_step_2_a_delete_hides_it_everywhere_and_prints_a_date(self, client, world):
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            principal = user_principal(world.user)
            assert list(visible_conversations(principal)) == []
            assert world.document not in readable_documents(principal)

            body = client.get(reverse("identity-deleted")).content.decode()
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            assert copy.purge_on_line(ticket.purge_on) in body
            assert ticket.purge_on == timezone.localdate() + datetime.timedelta(days=30)

            # NOTHING HAS BEEN PURGED YET: the rows are all still there.
            assert Conversation.objects.filter(pk=world.conversation.pk).exists()
            assert Turn.objects.filter(conversation_id=world.conversation.pk).exists()

    def test_step_3_delete_permanently_leaves_nothing_on_this_box(self, client, world):
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))

            principal = user_principal(world.user)
            assert list(visible_conversations(principal)) == []
            assert not Conversation.objects.filter(pk=world.conversation.pk).exists()
            assert not Turn.objects.filter(conversation_id=world.conversation.pk).exists()
            # The generated image, its files and its rows.
            assert not apps.get_model("vision.GenerationJob").objects.filter(
                pk=world.job.pk).exists()
            # The chat-scoped document, its chunks and its bytes.
            assert not apps.get_model("rag.Document").objects.filter(
                pk=world.document.pk).exists()
            assert world.chunk_count_for_document() == 0
            # The staging note file.
            assert not world.note_path.exists()
            # The tool-call words are blanked and the SHELL SURVIVES.
            invocation = ToolInvocation.objects.get(pk=world.invocation.pk)
            assert invocation.args == {} and invocation.text == ""
            assert invocation.tool_key and invocation.outcome
            # And no ticket is left for that conversation.
            assert not DeletionTicket.objects.filter(
                kind=copy.KIND_CONVERSATION).exists()

    def test_the_queue_row_survives_until_the_queue_half_lands(self, client, world):
        """QUEUE HALF NOT LANDED, and this test says so rather than
        leaving the state ambiguous. `models/queue/retention.py` does not
        exist yet, so nothing is registered for the `conversation` kind
        in the ROWS band and the `agent.turn` row keeps the person's
        literal message in `payload["text"]`.

        TASK 19 RENAMES THIS to `test_the_queue_row_is_gone` and asserts
        `.count() == 0`. Nothing else in this module changes.
        """
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            assert InferenceJob.objects.filter(
                payload__conversation=str(world.conversation.pk)).count() == 1

    def test_the_generations_queue_row_survives_until_the_queue_half_lands(
            self, client, world):
        """QUEUE HALF NOT LANDED. `models.contracts.queue.forget_jobs`
        does not exist yet, so the `vision.generate` row -- which carries
        the prompt in `payload["params"]` -- outlives its job.

        TASK 19 RENAMES THIS to `test_the_generations_queue_row_is_gone`
        and asserts `.count() == 0`.
        """
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            assert InferenceJob.objects.filter(
                pk=world.generation_queue_job.pk).exists()

    def test_step_4_the_ask_record_is_deleted_on_its_own(self, client, world):
        """SLICE 2 BUILDS THE ROUTE. Until then this asserts the record
        is still there and still listed -- the honest state -- and
        task 21 rewrites it to post the delete and assert its absence
        from `HistoryView`'s context.
        """
        with posture("personal"):
            sign_in(client, world.user)
            assert world.ask in visible_ask_records(user_principal(world.user))

    def test_step_5_the_purged_event_is_content_free_with_the_toggle_off(
            self, client, world):
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            event = AuditEvent.objects.filter(action=CONTENT_PURGED).get()
            assert event.target_type == copy.KIND_CONVERSATION
            assert event.target_key == str(world.conversation.pk)
            assert event.target_label == ""
            assert event.actor_key == str(world.user.pk)
            assert all(isinstance(count, int)
                       for count in event.detail["removed"].values())

    def test_flipping_the_toggle_labels_the_next_item_only(self, client, world):
        """Step 5's second half: no event is suppressed in either
        position, and the label follows the setting IN FORCE AT THE
        MOMENT OF THE DELETE -- not retroactively."""
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))

            row = IdentitySettings.get_solo()
            row.audit_detail = True
            row.save()

            second = make_conversation(
                title="A second thread", owner_kind="user",
                owner_key=str(world.user.pk))
            client.post(reverse("chat-conversation-delete", args=[second.id]))

        events = {e.target_key: e.target_label for e
                  in AuditEvent.objects.filter(action=CONTENT_DELETED)}
        assert events[str(world.conversation.id)] == ""
        assert events[str(second.id)] == "A second thread"
```

- [ ] **Step 2: Run it**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity/tests/test_deletion_demo.py`
Expected: PASS.

- [ ] **Step 3: Run the full gate and the two posture sweeps, then commit**

```bash
git add identity/tests/test_deletion_demo.py
git commit -m "test(identity): the deletion demo, end to end"
```

---

### Task 15: The cross-cutting documentation

**Files:**
- Modify: `docs/OPERATIONS.md` (a new section, after "What a backup contains")
- Create: `docs/adr/0019-deletion-and-retention.md`
- Modify: `docs/EXTENDING.md` (a new recipe, beside "Adding an entitlement axis")

The column READMEs already shipped with their own tasks (8, 9, 10, 11); this task is only the three repo-wide documents. `docs/adr/` runs to `0018-settings-assistant.md`, so **0019** is the next number.

- [ ] **Step 1: Add the backups section to `docs/OPERATIONS.md`**

A new section, **"Deleted content and your backups"**, placed after "What a backup contains", saying exactly this:

- A backup is **a copy of content that no delete path on this box reaches.** The retention date governs the live box. It does not, and cannot, reach into a backup set that was written before the delete.
- **There is no retroactive purge of existing backup sets**, and this platform will not offer one: rewriting a database dump in place would make every backup's integrity unverifiable, and selectively deleting from a file copy would leave a set that no longer restores to a coherent box.
- Deleted content therefore leaves your backups **as rotation ages them out**, on whatever schedule you keep. An operator with a retention obligation sets the backup rotation to match the deletion date; the two numbers are independent and this platform will not pretend otherwise.
- **Preview stacks are not a backup layer** and are not covered either. A preview stack is a full parallel copy with its own database; deleting something on the live box does not touch it. Tear one down when you are done with it.
- What a deletion leaves in a *new* backup taken after the purge: the content-free audit rows, which are in the dump like every other identity row, and nothing else.

Plus **one line** in the existing retention discussion pointing at `manage.py purge_deleted` for an operator who wants a cron rather than relying on prune-on-write. The section names no absolute path and adds no new backup behaviour — `foundation/ops/backup.py` is untouched by this feature.

- [ ] **Step 2: Write `docs/adr/0019-deletion-and-retention.md`**

Follow the shape of `0016-identity-and-entitlements.md`. The decision it records:

- **Delete means delete.** Content is destroyed; a content-free event survives.
- **One ticket table rather than per-model soft-delete columns**, with the three reasons (four migrations vs one; a cliff/actor/label/hold is a fact about the DELETION; the Deleted page is one query over one table rather than a union across three columns identity may not import).
- **The retention namespace on the existing cascade registry**, not a second registry — and why the handler is one-mode where `EntitlementCascade` is two.
- **The content/audit split**, and why the audit-detail toggle suppresses no event in either position.
- **The retention policy is centralised on `IdentitySettings`** (owner ruling): one policy, one place, one writer, one audit action; the queue reads it across the `identity.access` seam, non-creatingly and tolerantly, and gains no column.
- **Backups are their own layer.**
- **The named residue** (spec §10): `rag.ask` queue rows keyed to no Ask record; a generation whose tool turn was never written; engine-side files beyond the best-effort sweep.
- **The owner's cost/benefit principle and what it cut** — no dry-run count mode, no second cliff for tool records, enterprise BEHAVIOUR deferred while its FIELDS ship — **and the one thing it added**, the deletion-coverage gate.

No model or vendor names; no absolute paths.

- [ ] **Step 3: Add the recipe to `docs/EXTENDING.md`**

**"Registering a retention handler"**, beside "Adding an entitlement axis", covering:

- the `RetentionHandler` fields (`kind`, `key`, `label`, `handler`, `order`);
- the two order bands and **when to use each** — `ORDER_ROWS` for database rows, `ORDER_FILES` for anything that removes bytes, directly or through a function that does; and a handler that must read rows *and* remove bytes registers in FILES and orders its own reads first, internally;
- the one-mode `(key: str) -> int` signature, **and one line on why it is not two-mode like `EntitlementCascade`**;
- **the idempotence obligation**, and why it is the recovery story rather than a nicety;
- the one-line `AppConfig.ready()` registration, with the dotted path and the import-law reason;
- **the one test a new handler owes**: re-run after a failure completes rather than raises;
- **and the coverage gate**: a new model that holds user content must be added to `foundation/ops/tests/test_deletion_coverage.py`'s `_COVERED` list with a handler that reaches it, or to `_EXEMPT` with a reason — the test fails until one of the two is done.

- [ ] **Step 4: Run the docs guards**

Run: `.venv/bin/pytest -q foundation/ops/tests`
Expected: PASS — including `test_docs_model_names.py`, which walks the planning archive too.

- [ ] **Step 5: Commit**

```bash
git add docs/OPERATIONS.md docs/adr/0019-deletion-and-retention.md docs/EXTENDING.md
git commit -m "docs: deletion and retention — ADR 0019, the backups layer, the handler recipe"
```

---

# ⛔ GATE — QUEUE HALF

**Do not start Task 16 until the queue steward's queue PR is on `dev`. Merge `dev` into this branch first, in this worktree, and resolve there.**

The constraint is **textual adjacency, not migration ordering.** The centralised policy leaves `models/queue` with no migration at all, so there is no `0005`/`0006` sequence to agree on and nothing that has to land in a particular order to apply cleanly. What remains is that their PR reshapes the Queue page and its settings form, and this half edits the same files — `models/queue/backend.py`'s prune, `models/queue/views.py`'s settings help text and the Queue page's own copy. Writing ours on top of theirs is conflict avoidance, nothing more.

```bash
git fetch origin
git merge origin/dev            # in this worktree, on this branch
# resolve here, then re-run the four runs and the two posture sweeps
```

Until this gate is passed, the conversation purge is complete in every column **but the queue**, and two tests in `identity/tests/test_deletion_demo.py` assert that state explicitly — against real rows, not against a function's absence — rather than leaving it ambiguous. Task 19 flips both.

**The clearance packet for the queue steward** is the diff against `models/queue/backend.py`, `models/queue/views.py` and the new `models/queue/retention.py`, sent before merge.

---

### Task 16: `identity/access.py::queue_retention_days` — the non-creating seam

**Files:**
- Modify: `identity/access.py`
- Modify: `identity/README.md`
- Test: `identity/tests/test_access.py` (extend, or a new `identity/tests/test_queue_retention_seam.py`)

**Interfaces:**
- Consumes: `identity.contracts.retention.QUEUE_RETENTION_DAYS_DEFAULT` (Task 1); `identity.models.IdentitySettings` (intra-column).
- Produces: `identity.access.queue_retention_days() -> int | None`.

  **It is its own function, not `settings_row()`**, and the difference is the whole point: `settings_row()` calls `IdentitySettings.get_solo()`, which is `objects.get_or_create(pk=1)` — it **writes** when the row is absent. A worker is the wrong process to create the posture row.

- [ ] **Step 1: Write the failing tests**

```python
# identity/tests/test_queue_retention_seam.py
"""The one number `models/queue` reads across the identity seam."""
from __future__ import annotations

import pytest

from identity.access import queue_retention_days
from identity.contracts.retention import QUEUE_RETENTION_DAYS_DEFAULT
from identity.models import IdentitySettings

pytestmark = pytest.mark.django_db


class TestTheSeam:
    def test_it_returns_the_rows_value(self):
        row = IdentitySettings.get_solo()
        row.queue_retention_days = 7
        row.save()
        assert queue_retention_days() == 7

    def test_null_means_no_age_cliff_and_comes_back_as_none(self):
        row = IdentitySettings.get_solo()
        row.queue_retention_days = None
        row.save()
        assert queue_retention_days() is None

    def test_with_no_row_at_all_it_returns_the_documented_default(self):
        IdentitySettings.objects.all().delete()
        assert queue_retention_days() == QUEUE_RETENTION_DAYS_DEFAULT

    def test_it_creates_nothing_in_either_case(self):
        """THE FAILURE THIS PINS is `get_solo`'s `get_or_create` being
        used from a worker: a box whose posture page has never been
        opened must not have its posture row minted by a prune."""
        IdentitySettings.objects.all().delete()
        queue_retention_days()
        assert IdentitySettings.objects.count() == 0

    def test_it_costs_exactly_one_query(self, django_assert_num_queries):
        IdentitySettings.get_solo()
        with django_assert_num_queries(1):
            queue_retention_days()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q identity/tests/test_queue_retention_seam.py`
Expected: FAIL — `ImportError: cannot import name 'queue_retention_days'`.

- [ ] **Step 3: Add the reader**

In `identity/access.py`, after `settings_row`:

```python
def queue_retention_days() -> int | None:
    """How many days a FINISHED queue row survives, or `None` for no age
    cliff at all. The queue's number, owned by the one retention policy.

    A SEPARATE FUNCTION FROM `settings_row()` ABOVE, AND NON-CREATING,
    and that is the whole reason it exists. `settings_row()` calls
    `IdentitySettings.get_solo()`, which is `objects.get_or_create(pk=1)`
    -- it WRITES when the row is absent. The caller here is
    `models.queue.backend`'s prune, which runs on the worker path; a
    worker is the wrong process to create this box's posture row, and
    creating it inside a claim's advisory-lock transaction would be
    worse. `.first()` reads and returns, or answers the documented
    default.

    AN ABSENT ROW MEANS A BOX WHOSE POSTURE PAGE HAS NEVER BEEN OPENED,
    and `QUEUE_RETENTION_DAYS_DEFAULT` is the honest answer for it -- the
    same number a freshly migrated row carries.

    IT DOES NOT SWALLOW. A missing TABLE (a racing `migrate`) raises
    `ProgrammingError`/`OperationalError` from here, and the CALLER
    forgives it -- `models.queue.backend.enqueue` already wraps its own
    `JobSettings.get_solo()` in exactly those two exception types for
    exactly that reason. Catching here would hide a genuine database
    fault from every other caller this seam may acquire.
    """
    # THE ROW OBJECT, NOT `.values_list(...).first()`. That spelling
    # answers `None` both for "there is no row" and for "the row's value
    # is NULL" -- two different facts with two different answers here
    # (the documented default, versus no age cliff at all).
    # `test_null_means_no_age_cliff_and_comes_back_as_none` is what
    # catches the other spelling.
    row = IdentitySettings.objects.filter(pk=1).first()
    return QUEUE_RETENTION_DAYS_DEFAULT if row is None else row.queue_retention_days
```

Import `QUEUE_RETENTION_DAYS_DEFAULT` from `identity.contracts.retention` at module top.

- [ ] **Step 4: Document the seam in `identity/README.md`**

One paragraph in the section Task 5 added: **the new non-creating `identity/access.py::queue_retention_days` seam** — what it returns when there is no row, and **why it is not `settings_row()`**.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest -q identity models`
Expected: PASS.

- [ ] **Step 6: Run the full gate and commit**

```bash
git add identity/access.py identity/README.md identity/tests/test_queue_retention_seam.py
git commit -m "feat(identity): a non-creating seam for the queue's retention number"
```

---

### Task 17: `models/queue/retention.py` — the payload-keyed handlers

**Files:**
- Create: `models/queue/retention.py`
- Modify: `models/queue/apps.py` (`ready()` registers both handlers)
- Modify: `models/README.md`
- Test: `models/queue/tests/test_retention.py` (new)

**Interfaces:**
- Consumes: `models.queue.models.InferenceJob`, `TERMINAL_STATES`, `RUNNING`; `models.queue.backend.cancel_job` (an **intra-column** call, so no new passthrough on `models/contracts/queue.py` is needed for it); `identity.contracts.retention.RetentionRefused` (Task 1) — the refusal type, raised on `"already_running"` and caught by `identity/views.py::deleted_purge` (Task 12). `identity.services.ServiceRefused` is **not** importable here: `models/queue` may reach `identity.contracts` and not `identity.services`, which is exactly why the refusal type lives in the pure contracts module.
- Produces:
  - `models.queue.retention.forget_conversation(key: str) -> int`
  - `models.queue.retention.forget_document(key: str) -> int`
  - registrations: `RetentionHandler(kind="conversation", key="queue.conversation_jobs", label="Queue jobs", handler="models.queue.retention.forget_conversation", order=ORDER_ROWS)` and `RetentionHandler(kind="document", key="queue.document_jobs", label="Queue jobs", handler="models.queue.retention.forget_document", order=ORDER_ROWS)`.

**The payload map**, read from each payload's own producer:

| Ticket kind | Job kinds | Payload field | Cast |
|---|---|---|---|
| `conversation` | `agent.turn` (`agents/chat/service.py`), `rag.consolidate` (`agents/chat/views/workstreams.py`) | `conversation` | `str` |
| `document` | `rag.ingest` (`tools/rag/ingest.py`) | `document_id` | `int` |

**No `rag.ask` handler.** A `rag.ask` payload carries the question text and the actor, and **no reference to the `AskRecord`** the handler later writes (`tools/rag/services.py::record_ask` creates the row on success). There is no id to key on, and matching on question text would be a guess. Those rows are left to the queue's own age cliff (Task 18), and that residue is named in the ADR rather than papered over.

- [ ] **Step 1: Write the failing tests**

```python
# models/queue/tests/test_retention.py
"""The payload-keyed queue handlers, and the cancel-before-delete rule."""
from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest
from django.conf import settings as django_settings

from identity.contracts.retention import RetentionRefused
from models.queue.models import CANCELLED, QUEUED, RUNNING, SUCCEEDED, InferenceJob
from models.queue.retention import forget_conversation, forget_document
from models.queue.tests._helpers import make_queue_job

pytestmark = pytest.mark.django_db


def _job(**overrides):
    fields = dict(kind="agent.turn", state=SUCCEEDED, priority=100)
    fields.update(overrides)
    return make_queue_job(**fields)


class TestForgetConversation:
    def test_it_deletes_agent_turn_and_rag_consolidate_rows_for_that_conversation(self):
        conversation = str(uuid.uuid4())
        turn = _job(payload={"conversation": conversation, "text": "a secret"})
        consolidate = _job(kind="rag.consolidate",
                           payload={"conversation": conversation, "title": "A stream"})
        assert forget_conversation(conversation) == 2
        assert not InferenceJob.objects.filter(
            pk__in=[turn.pk, consolidate.pk]).exists()

    def test_it_touches_no_other_row(self):
        mine, theirs = str(uuid.uuid4()), str(uuid.uuid4())
        kept = _job(payload={"conversation": theirs, "text": "theirs"})
        unrelated = _job(kind="rag.ingest", payload={"document_id": 1})
        forget_conversation(mine)
        assert InferenceJob.objects.filter(
            pk__in=[kept.pk, unrelated.pk]).count() == 2

    def test_it_is_idempotent(self):
        conversation = str(uuid.uuid4())
        _job(payload={"conversation": conversation, "text": "x"})
        forget_conversation(conversation)
        assert forget_conversation(conversation) == 0

    def test_an_unparseable_key_removes_nothing(self):
        assert forget_conversation("") == 0


class TestCancelBeforeDelete:
    def test_a_queued_row_is_cancelled_through_cancel_job_before_it_is_deleted(
            self, monkeypatch):
        """ASSERTED BY THE `on_terminal` HOOK HAVING BEEN INVOKED, not
        merely by the row's absence: only the hook distinguishes a
        cancel-then-delete from a bare delete, and the hook is what
        closes a stranded placeholder assistant turn."""
        fired = []
        from models.contracts import jobkinds
        monkeypatch.setattr(
            jobkinds, "invoke_on_terminal",
            lambda kind, payload, outcome: fired.append((kind, outcome)))
        conversation = str(uuid.uuid4())
        _job(state=QUEUED, payload={"conversation": conversation, "text": "x"})

        assert forget_conversation(conversation) == 1
        assert fired == [("agent.turn", "cancelled")]
        assert InferenceJob.objects.count() == 0

    def test_a_running_row_is_never_deleted_and_the_handler_refuses(self):
        """A worker holds that job; nothing here may delete it. The
        handler raises, the purge aborts with the ticket intact, and
        every row and byte is untouched -- this handler is ORDER_ROWS and
        registered FIRST for the kind, so it runs before any other
        handler has done anything."""
        conversation = str(uuid.uuid4())
        running = _job(state=RUNNING, payload={"conversation": conversation,
                                               "text": "x"})
        with pytest.raises(RetentionRefused, match="still running"):
            forget_conversation(conversation)
        assert InferenceJob.objects.filter(pk=running.pk).exists()

    def test_the_refusal_type_comes_from_the_pure_contracts_module(self):
        """`models/queue` may import `identity.contracts` and may NOT
        import `identity.services`, so the refusal the Deleted page's
        view renders has to live in the pure module. A bare
        `RuntimeError` would make that view swallow every programming
        error a handler contains."""
        assert RetentionRefused.__module__ == "identity.contracts.retention"

    def test_once_that_job_is_terminal_a_re_run_completes(self):
        conversation = str(uuid.uuid4())
        running = _job(state=RUNNING, payload={"conversation": conversation,
                                               "text": "x"})
        with pytest.raises(RetentionRefused):
            forget_conversation(conversation)
        InferenceJob.objects.filter(pk=running.pk).update(state=SUCCEEDED)
        assert forget_conversation(conversation) == 1

    def test_an_already_terminal_row_is_deleted_with_no_cancel_attempt(self, monkeypatch):
        calls = []
        from models.queue import retention as module
        monkeypatch.setattr(module, "cancel_job",
                            lambda job_id: calls.append(job_id) or "unknown")
        conversation = str(uuid.uuid4())
        _job(state=CANCELLED, payload={"conversation": conversation, "text": "x"})
        assert forget_conversation(conversation) == 1
        assert calls == []


class TestForgetDocument:
    def test_it_deletes_rag_ingest_rows_naming_that_document_id(self):
        job = _job(kind="rag.ingest", payload={"document_id": 42})
        assert forget_document("42") == 1
        assert not InferenceJob.objects.filter(pk=job.pk).exists()

    def test_the_id_is_cast_to_an_int_because_the_payload_holds_one(self):
        _job(kind="rag.ingest", payload={"document_id": 42})
        assert forget_document("42") == 1

    def test_a_non_numeric_key_removes_nothing(self):
        assert forget_document("not-a-number") == 0


class TestThisColumnImportsNoAgents:
    def test_the_module_names_nothing_under_agents_or_tools(self):
        """ASSERTED HERE, IN THIS COLUMN'S OWN TESTS, not left to the
        repo-wide gate: the payload map is a table of what a payload
        field MEANS, owned by the column that owns the payload column."""
        source = (Path(django_settings.BASE_DIR)
                  / "models/queue/retention.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        targets = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets.append(node.module)
        offenders = [t for t in targets
                     if t.split(".")[0] in ("agents", "tools")
                     or t.startswith("identity.models")
                     or t.startswith("identity.services")]
        assert offenders == [], offenders
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q models/queue/tests/test_retention.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'models.queue.retention'`.

- [ ] **Step 3: Write `models/queue/retention.py`**

```python
"""What the queue forgets when a deleted item's date arrives.

A PAYLOAD-KEYED HANDLER, WITH NO `agents` IMPORT OF ANY KIND. This
module holds a small table of what a payload FIELD means, owned by the
column that owns the payload column -- which is the only shape available
here: `agents/` may not import `models.queue` (import-law rule 2), so a
conversation's queue rows could never be reached from the column that
owns the conversation. Identity runs this handler by dotted path, in the
same transaction as the agents-side purge, which is exactly what the
import law made impossible any other way.

| Ticket kind    | Job kinds                      | Payload field | Cast |
|----------------|--------------------------------|---------------|------|
| `conversation` | `agent.turn`, `rag.consolidate`| `conversation`| str  |
| `document`     | `rag.ingest`                   | `document_id` | int  |

Both payload shapes are read from their producers, not guessed.

NO `rag.ask` HANDLER: that payload carries the question text and the
actor and NO reference to the `AskRecord` the handler later writes, so
there is no id to key on and matching on question text would be a guess.
Those rows go to the queue's own age cliff (`backend._prune_finished_
jobs`), and that residue is named in ADR 0019 rather than papered over.

ROWS ARE DELETED, NOT SCRUBBED -- the owner's ruling. A finished job's
bookkeeping is not worth a table of half-erased rows, and the age cliff
removes finished rows anyway, so a scrub would only defer the same
delete.
"""
from __future__ import annotations

from identity.contracts.retention import RetentionRefused
from models.queue.backend import cancel_job
from models.queue.models import TERMINAL_STATES, InferenceJob

# Job kinds whose payload names a conversation, and the field it uses.
_CONVERSATION_KINDS = ("agent.turn", "rag.consolidate")
_CONVERSATION_FIELD = "conversation"
# Job kinds whose payload names a library document.
_DOCUMENT_KINDS = ("rag.ingest",)
_DOCUMENT_FIELD = "document_id"


def _forget(rows) -> int:
    """Cancel anything non-terminal, then delete. Returns rows deleted.

    A LIVE ROW IS CANCELLED BEFORE IT IS DELETED, NEVER DELETED OUT FROM
    UNDER A RUNNING TURN, and this is a correctness rule, not a
    courtesy. `agents.runtime.jobs.on_turn_terminal` -- the `on_terminal`
    hook `backend.cancel_job` schedules on a successful cancel -- is what
    closes a stranded placeholder assistant turn. A purge that simply
    deleted a queued row would skip that hook entirely, and the turn's
    own poller (`agents.chat.views.turns`, calling `get_job` each tick)
    would see the job vanish mid-poll, which reads as a database fault
    rather than as a delete.

    So, for every non-terminal row matched:

      1. call `backend.cancel_job` -- an INTRA-COLUMN call, so no new
         passthrough on `models/contracts/queue.py` is needed for it. The
         queued->cancelled transition stays that function's single
         conditional `UPDATE`, and its `on_terminal` scheduling stays
         exactly as it is;
      2. delete the row only once it is terminal. A row that came back
         "cancelled", "unknown" or "already_finished" is deletable in
         this same call;
      3. REFUSE on "already_running", with `RetentionRefused` from
         `identity.contracts.retention` -- the pure module, which this
         column may import where `identity.services` is closed to it.
         A worker holds that job; nothing here may delete it. Raising
         aborts the whole purge -- the
         runner never swallows -- with the ticket intact and every row
         and byte untouched, because this handler is `ORDER_ROWS` and is
         registered FIRST for its kind, so it runs before any other
         handler has done anything. The view renders the sentence; the
         next sweep, or the next click, completes it once the worker is
         done. The item stays invisible the whole time, so the promise
         is kept even while the purge waits.

    The `on_terminal` hook scheduled in step 1 fires on commit of
    `purge_ticket`'s outer transaction -- after the turns are already
    gone -- and `on_turn_terminal` is one conditional `UPDATE` filtered
    on the states a stranded turn can be in, so it matches zero rows and
    no-ops. That is the existing ordering preserved, not a new one.
    """
    live = [row for row in rows if row.state not in TERMINAL_STATES]
    for row in live:
        outcome = cancel_job(row.pk)
        if outcome == "already_running":
            raise RetentionRefused(
                f"Job {row.pk} for this item is still running. Nothing was "
                "removed; try again when it has finished.")
    deleted, _by_model = InferenceJob.objects.filter(
        pk__in=[row.pk for row in rows]).delete()
    return deleted


def forget_conversation(key: str) -> int:
    """Delete every queue row naming this conversation. Idempotent."""
    conversation = str(key or "")
    if not conversation:
        return 0
    rows = list(InferenceJob.objects.filter(
        kind__in=_CONVERSATION_KINDS,
        **{f"payload__{_CONVERSATION_FIELD}": conversation}))
    return _forget(rows)


def forget_document(key: str) -> int:
    """Delete every queue row naming this document. Idempotent.

    The payload holds an INTEGER (`tools/rag/ingest.py` writes
    `document_id=document.pk`), and a ticket key is text, so the cast is
    explicit here -- a JSON field lookup against a string would silently
    match nothing.
    """
    raw = str(key or "")
    if not raw.isdecimal():
        return 0
    rows = list(InferenceJob.objects.filter(
        kind__in=_DOCUMENT_KINDS,
        **{f"payload__{_DOCUMENT_FIELD}": int(raw)}))
    return _forget(rows)
```

`from django.db.models import Q` is unused — drop it.

- [ ] **Step 4: Register both handlers in `models/queue/apps.py`**

`JobsConfig.ready()` is currently a docstring and nothing else. Give it a body:

```python
    def ready(self) -> None:
        """Register this column's two retention handlers.

        No job kinds, no roles -- feature apps register those. These two
        are different: a queue row's PAYLOAD holds the person's literal
        message, and only this column may read `InferenceJob`, so only
        this column can answer "forget everything about that item". The
        paths are dotted STRINGS, resolved by `identity/cascades.py` at
        purge time, so this method still imports no implementation
        module and touches no database.

        ROWS BAND, and registered FIRST for the conversation kind: this
        handler is the one that can REFUSE (a running job), and the
        refusal is only harmless if nothing else has run yet.
        """
        from identity.contracts.cascades import (
            ORDER_ROWS, RetentionHandler, register_retention_handler,
        )
        from identity.contracts.retention import KIND_CONVERSATION, KIND_DOCUMENT

        register_retention_handler(RetentionHandler(
            kind=KIND_CONVERSATION, key="queue.conversation_jobs",
            label="Queue jobs", handler="models.queue.retention.forget_conversation",
            order=ORDER_ROWS))
        register_retention_handler(RetentionHandler(
            kind=KIND_DOCUMENT, key="queue.document_jobs",
            label="Queue jobs", handler="models.queue.retention.forget_document",
            order=ORDER_ROWS))
```

**Registration order matters and is not guaranteed by `INSTALLED_APPS` alone.** The ROWS/FILES bands guarantee this handler runs before the agents and rag FILES handlers, which is the property the refusal depends on; within the ROWS band nothing else is registered for either kind, so there is nothing for it to race. Say that in a comment rather than relying on app order.

- [ ] **Step 5: Confirm the purge view already renders this refusal**

Nothing to change: Task 12 wrote `deleted_purge` catching `(services.ServiceRefused, RetentionRefused)`, and its parametrised test already covers both types. Re-run `identity/tests/test_deleted_page.py::TestRestoreAndPurge::test_a_refusal_flashes_and_redirects_for_either_refusal_type` and add one end-to-end case there: a conversation whose `agent.turn` row is `running`, permanently deleted through the route, flashes the sentence and leaves the ticket standing.

- [ ] **Step 6: Document it in `models/README.md`**

The payload-keyed queue handler and **its cancel-before-delete rule**; `forget_jobs` on the contracts seam is Task 19's line.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest -q models identity`
Expected: PASS.

- [ ] **Step 8: Run the full gate and commit**

```bash
git add models/queue/retention.py models/queue/apps.py models/README.md \
        models/queue/tests/test_retention.py identity/tests/
git commit -m "feat(queue): payload-keyed retention handlers, cancel before delete"
```

---

### Task 18: The age condition on the prune, and its one identity read

**Files:**
- Modify: `models/queue/backend.py` (`_prune_finished_jobs`, `enqueue`)
- Modify: `models/queue/views.py` (the Queue settings page's one-sentence pointer) and/or `foundation/settings_help.py` (the Queue card)
- Modify: `models/README.md`
- Test: `models/queue/tests/test_backend.py` (extend), `models/queue/tests/test_prune_retention.py` (new)

**Held tests:** `models/queue/tests/test_backend.py` carries three tests that patch `models.queue.backend._prune_finished_jobs` by name and assert the enqueue path forgives its failures — around lines 244, 260 and 292 in that module. **The signature changes from `(limit)` to `(limit, retention_days)`**, so every patch and every direct call must be updated. Re-pin each by name in the commit message. `models/queue/tests/test_queue_vocabulary_pin.py` is untouched.

**Interfaces — the exact call site, answering the queue steward's question:**
- **`_prune_finished_jobs`'s only caller in the tree today is `models/queue/backend.py::enqueue`.** Verified by `grep -rn '_prune_finished_jobs' models foundation agents tools identity scripts`: every other hit is a docstring or a test patching it by name.
- **That caller runs OUTSIDE any transaction, at autocommit — it is NOT inside `models/queue/claim.py::claim_and_admit`'s advisory-lock transaction.** `claim_and_admit` opens its own `transaction.atomic()` and never calls the prune; and `enqueue`'s own docstring records the unstated precondition that it *must* run outside any ambient `transaction.atomic()` a caller might wrap it in, because a `ProgrammingError` from the prune query poisons the connection's current transaction on Postgres. **The identity read is therefore made at autocommit, in `enqueue`, beside the existing `JobSettings.get_solo()` read — never inside the claim's advisory-lock transaction, and never on the claim path at all.**
- New signature: `_prune_finished_jobs(limit: int, retention_days: int | None) -> None`. It stays a **pure query**: the READ is the caller's, made once.

- [ ] **Step 1: Write the failing tests**

```python
# models/queue/tests/test_prune_retention.py
"""The queue's age cliff: identity's number, the queue's prune."""
from __future__ import annotations

import datetime

import pytest
from django.db import OperationalError, ProgrammingError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from unittest.mock import patch

from identity.contracts.retention import QUEUE_RETENTION_DAYS_DEFAULT
from identity.models import IdentitySettings
from models.queue.backend import _prune_finished_jobs, enqueue
from models.queue.models import (
    QUEUED, RUNNING, SUCCEEDED, InferenceJob, JobSettings,
)
from models.queue.tests._helpers import make_queue_job

pytestmark = pytest.mark.django_db

_IDENTITY_TABLE = "identity_identitysettings"


def _identity_reads(captured) -> list[str]:
    """Statements against the identity settings table, and nothing else.

    A QUERY-COUNT TEST ON ONE TABLE, not `django_assert_num_queries`: the
    prune path runs several statements of its own and a total count would
    drift with unrelated queue work, while the property being pinned is
    precisely "how many times did we cross the identity seam".
    """
    return [q["sql"] for q in captured.captured_queries if _IDENTITY_TABLE in q["sql"]]


def _finished(days_ago: float, **overrides):
    job = make_queue_job(state=SUCCEEDED, kind="rag.ask", payload={},
                         priority=100, **overrides)
    InferenceJob.objects.filter(pk=job.pk).update(
        finished_at=timezone.now() - datetime.timedelta(days=days_ago))
    return job


class TestTheAgeCondition:
    def test_a_one_day_cliff_removes_a_row_finished_two_days_ago(self):
        old = _finished(2)
        _prune_finished_jobs(1000, 1)
        assert not InferenceJob.objects.filter(pk=old.pk).exists()

    def test_it_keeps_one_finished_an_hour_ago(self):
        recent = _finished(1 / 24)
        _prune_finished_jobs(1000, 1)
        assert InferenceJob.objects.filter(pk=recent.pk).exists()

    @pytest.mark.parametrize("state", [QUEUED, RUNNING])
    def test_it_never_touches_a_queued_or_running_row_however_old(self, state):
        job = make_queue_job(state=state, priority=100, payload={})
        InferenceJob.objects.filter(pk=job.pk).update(
            finished_at=timezone.now() - datetime.timedelta(days=400))
        _prune_finished_jobs(1000, 1)
        assert InferenceJob.objects.filter(pk=job.pk).exists()

    def test_a_null_cliff_reproduces_todays_fifo_only_behaviour(self):
        old = _finished(400)
        _prune_finished_jobs(1000, None)
        assert InferenceJob.objects.filter(pk=old.pk).exists()

    def test_a_row_with_no_finished_at_is_not_aged_out(self):
        """Defensive: a terminal row whose `finished_at` was never
        stamped has no age, and "unknown" must not read as "ancient"."""
        job = make_queue_job(state=SUCCEEDED, priority=100, payload={})
        InferenceJob.objects.filter(pk=job.pk).update(finished_at=None)
        _prune_finished_jobs(1000, 1)
        assert InferenceJob.objects.filter(pk=job.pk).exists()


class TestTheNumberComesFromIdentity:
    def test_setting_the_identity_field_changes_what_the_prune_deletes(self):
        row = IdentitySettings.get_solo()
        row.queue_retention_days = 30
        row.save()
        old = _finished(2)
        enqueue("rag.ask", {"question": "x"})
        assert InferenceJob.objects.filter(pk=old.pk).exists()

        row.queue_retention_days = 1
        row.save()
        enqueue("rag.ask", {"question": "x"})
        assert not InferenceJob.objects.filter(pk=old.pk).exists()

    def test_job_settings_has_no_retention_days_style_field(self):
        """ASSERTED AGAINST THE MODEL'S FIELDS, so a later
        re-introduction fails this test: the retention policy is
        centralised on the identity row (owner ruling)."""
        names = {f.name for f in JobSettings._meta.get_fields()}
        assert "retention_days" not in names
        assert "queue_retention_days" not in names
        assert "retention_limit" in names  # the FIFO bound is still ours


class TestTheReadIsNonCreatingTolerantAndMadeOnce:
    def test_with_no_identity_row_the_prune_uses_the_default_and_creates_nothing(self):
        IdentitySettings.objects.all().delete()
        old = _finished(2)
        enqueue("rag.ask", {"question": "x"})
        assert not InferenceJob.objects.filter(pk=old.pk).exists()
        assert IdentitySettings.objects.count() == 0, (
            "the prune materialised the posture singleton -- `get_solo`'s "
            "`get_or_create` must never be used from this path")

    def test_a_missing_identity_table_falls_back_logs_and_does_not_raise(self):
        """An exception escaping the worker's `tick()` is what
        `run_forever` reads as `crashed=True`, which ends in a process
        exit. A racing `migrate` must not be able to cause that."""
        for exc_cls in (ProgrammingError, OperationalError):
            with patch("models.queue.backend.queue_retention_days",
                       side_effect=exc_cls("relation does not exist")):
                job = enqueue("rag.ask", {"question": "x"})
            assert InferenceJob.objects.filter(pk=job).exists()

    def test_one_prune_over_twenty_five_terminal_rows_makes_exactly_one_identity_read(self):
        """THE QUERY-COUNT PIN the queue steward asked for, stated
        exactly: statements against the identity settings table, counted
        for ONE enqueue (which runs ONE prune), asserted at exactly one
        -- never one per row."""
        IdentitySettings.get_solo()
        for _ in range(25):
            _finished(0.1)
        with CaptureQueriesContext(connection) as captured:
            enqueue("rag.ask", {"question": "x"})
        assert len(_identity_reads(captured)) == 1

    def test_it_is_still_exactly_one_when_there_is_nothing_to_prune(self):
        """The read is HOISTED beside the existing `JobSettings` read, at
        the top of `enqueue`, not made lazily inside the prune -- so it is
        one per enqueue whether or not any row is removed. Pinned in both
        directions so a later short-circuit is a deliberate change, not
        a drift."""
        IdentitySettings.get_solo()
        InferenceJob.objects.all().delete()
        with CaptureQueriesContext(connection) as captured:
            enqueue("rag.ask", {"question": "x"})
        assert len(_identity_reads(captured)) == 1

    def test_the_read_never_happens_on_the_claim_path(self):
        """`models.queue.claim.claim_and_admit` opens its own
        `transaction.atomic()` with an advisory lock, and the identity
        read must never sit inside it. The prune has exactly one caller
        -- `enqueue`, at autocommit -- and this pins that no claim
        touches the identity table at all."""
        from models.queue.claim import claim_and_admit
        IdentitySettings.get_solo()
        make_queue_job(state=QUEUED, kind="rag.ask", payload={}, priority=100)
        with CaptureQueriesContext(connection) as captured:
            claim_and_admit(worker_id="test-worker", limit=1)
        assert _identity_reads(captured) == []
```

`claim_and_admit`'s real signature is whatever `models/queue/claim.py` declares — read it and call it correctly; the assertion is the point, not the argument spelling.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest -q models/queue/tests/test_prune_retention.py`
Expected: FAIL — `_prune_finished_jobs() missing 1 required positional argument: 'retention_days'`.

- [ ] **Step 3: Add the age condition**

In `models/queue/backend.py`, import the seam and the default at module top:

```python
from identity.access import queue_retention_days
from identity.contracts.retention import QUEUE_RETENTION_DAYS_DEFAULT
```

and rewrite the prune:

```python
def _prune_finished_jobs(limit: int, retention_days: int | None) -> None:
    """Delete terminal `InferenceJob` rows on two independent bounds: the
    FIFO count (`limit`) and, when `retention_days` is not None, an AGE.

    Both are scoped to `state__in=TERMINAL_STATES`, so a queued or
    running row can never be counted toward the limit or removed by the
    age condition, no matter how large `limit` is set or how long the job
    has been running.

    `retention_days` IS A PARAMETER, NOT A READ. The number is
    `IdentitySettings.queue_retention_days` -- the queue READS the one
    centralised retention policy, it does not own it (owner ruling) --
    and the read happens ONCE, in the caller, so this function stays a
    pure query with no cross-column call of its own. `None` means no age
    cliff at all: today's FIFO-only behaviour, byte for byte.

    A TERMINAL ROW WITH NO `finished_at` IS NOT AGED OUT. "Unknown" must
    not read as "ancient" -- the same convention `JobSettings.
    memory_budget_bytes` documents for a null.
    """
    cutoff = (
        InferenceJob.objects.filter(state__in=TERMINAL_STATES)
        .order_by("-pk")
        .values_list("pk", flat=True)[limit : limit + 1]
    )
    cutoff_pk = next(iter(cutoff), None)
    if cutoff_pk is not None:
        InferenceJob.objects.filter(state__in=TERMINAL_STATES, pk__lte=cutoff_pk).delete()
    if retention_days is not None:
        InferenceJob.objects.filter(
            state__in=TERMINAL_STATES,
            finished_at__isnull=False,
            finished_at__lt=timezone.now() - timedelta(days=retention_days),
        ).delete()
```

`timedelta` comes from `datetime`; check whether `backend.py` already imports it and add it if not.

- [ ] **Step 4: Make the one tolerant read in `enqueue`**

Beside the existing hoisted `JobSettings.get_solo()`:

```python
    # ONE IDENTITY READ PER ENQUEUE, hoisted here beside the
    # `JobSettings` read above and threaded into the prune below -- never
    # inside `_prune_finished_jobs` and never in a per-job loop. This is
    # the queue's only crossing of the identity seam on this path, it is
    # NON-CREATING (`identity.access.queue_retention_days` uses
    # `.first()`, never `get_solo`'s `get_or_create` -- a worker must not
    # materialise the posture singleton), and it happens at AUTOCOMMIT:
    # `enqueue` runs outside any ambient `transaction.atomic()` by this
    # function's own recorded precondition, and `claim_and_admit`'s
    # advisory-lock transaction never reaches this code at all.
    #
    # TOLERANT, AND IT DOES NOT RE-RAISE, for exactly the reason the
    # `JobSettings` read above is: a racing `migrate` can leave
    # `identity_identitysettings` absent for a few seconds, and an
    # exception escaping the worker's `tick()` is read by `run_forever`
    # as `crashed=True`, which ends in a process exit. Those two
    # exception types and not a bare `except Exception`, so a genuine bug
    # still raises loudly.
    try:
        retention_days = queue_retention_days()
    except (ProgrammingError, OperationalError):
        retention_days = QUEUE_RETENTION_DAYS_DEFAULT
        logger.warning(
            "enqueue: could not read the retention policy; falling back to the "
            "documented default of %s day(s) for this prune",
            QUEUE_RETENTION_DAYS_DEFAULT, exc_info=True,
        )
```

and thread it into the one call:

```python
            _prune_finished_jobs(job_settings.retention_limit, retention_days)
```

Place the read **with** the `JobSettings` read at the top of `enqueue`, not next to the prune, so "one read per enqueue" is structural rather than promised.

- [ ] **Step 5: Point the Queue settings page at the one place the number lives**

**The Queue settings page does not show this number at all.** Its POST writer gains nothing and its form keeps the fields it has — one retention policy, one place to edit it; a read-only echo would still be a second place to look. Add **one sentence** to the Queue settings help text saying the finished-job cliff is part of the retention policy on Identity & security, and that is all it says. Put it wherever that page's help copy is declared — `foundation/settings_help.py`'s `jobs-settings` card if that is where the Queue page's sentences live, otherwise the page's own declared-in-Python copy in `models/queue/views.py`. Read both and put it in exactly one.

- [ ] **Step 6: Re-pin the three patching tests and document it**

Update the three `models/queue/tests/test_backend.py` tests that patch `_prune_finished_jobs`, and add to `models/README.md`: the age cliff **and the fact that its number is identity's, read across the `identity.access` seam non-creatingly and tolerantly, once per prune, with `JobSettings` gaining nothing**.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest -q models identity`
Expected: PASS.

- [ ] **Step 8: Run the full gate and commit**

```bash
git add models/queue/backend.py models/queue/views.py foundation/settings_help.py \
        models/README.md models/queue/tests/
git commit -m "feat(queue): an age cliff on the prune, reading identity's one retention number

Re-pins models/queue/tests/test_backend.py's three tests that patch _prune_finished_jobs by name: the signature is now (limit, retention_days)."
```

---

### Task 19: `forget_jobs`, the vision handler's use of it, and the gate's two flips

**Files:**
- Modify: `models/contracts/queue.py` (`forget_jobs` passthrough)
- Modify: `models/queue/backend.py` (`forget_jobs`)
- Modify: `tools/vision/retention.py` (collect and forget the queue rows) — **vision steward**
- Modify: `models/README.md`
- Test: `models/contracts/tests/test_queue.py` (extend), `models/queue/tests/test_retention.py` (extend), `tools/vision/tests/test_retention.py` (flip), `identity/tests/test_deletion_demo.py` (flip two)

**Interfaces:**
- Produces: `models.contracts.queue.forget_jobs(job_ids) -> int` — dispatched through `settings.INFERENCE_QUEUE_BACKEND` exactly as `enqueue` and `get_job` are; `models.queue.backend.forget_jobs(job_ids) -> int`.
- **Why a passthrough and not payload-keying:** a `vision.generate` payload carries `operation`, `params` and `inputs` and **no generation id**, because the `GenerationJob` row is created by the handler at run time. The link exists in the other direction only — `GenerationJob.queue_job_id`. So the vision handler collects those ids from the jobs it is about to delete and hands them over the contracts seam. **This is not an import-law exception:** rule 2 forbids `models.queue.models` to `tools/`, and `models.contracts.queue` is the sanctioned door whose own docstring exists precisely so a tool column never reaches into `models.queue`.

- [ ] **Step 1: Write the failing tests**

```python
# append to models/contracts/tests/test_queue.py
# -- needs `import sys`, `import types` beside the existing imports
class TestForgetJobs:
    def test_it_dispatches_through_the_configured_backend(self, settings, monkeypatch):
        """PROVES THE DISPATCH, rather than asserting a short-circuit.
        The seam re-imports `settings.INFERENCE_QUEUE_BACKEND` on every
        call -- the contract `enqueue` and `get_job` already rely on --
        so pointing it at a stand-in module is how a test sees which
        function actually ran."""
        seen = []
        stand_in = types.ModuleType("test_queue_backend_stand_in")
        stand_in.forget_jobs = lambda job_ids: seen.append(list(job_ids)) or 7
        monkeypatch.setitem(sys.modules, stand_in.__name__, stand_in)
        settings.INFERENCE_QUEUE_BACKEND = stand_in.__name__

        assert forget_jobs([11, 12]) == 7
        assert seen == [[11, 12]]

    def test_an_empty_sequence_needs_no_backend_at_all(self, settings):
        """A never-500 caller should not pay an import to remove
        nothing. `INFERENCE_QUEUE_BACKEND` is blanked, which `_backend`
        raises on -- so a call that reached it would fail here."""
        settings.INFERENCE_QUEUE_BACKEND = ""
        assert forget_jobs([]) == 0

    def test_it_deletes_the_named_rows(self):
        job = make_queue_job(state=SUCCEEDED, priority=100, payload={})
        assert forget_jobs([job.pk]) == 1

    def test_an_id_that_does_not_exist_is_a_no_op(self):
        assert forget_jobs([999999]) == 0

    def test_it_is_idempotent(self):
        job = make_queue_job(state=SUCCEEDED, priority=100, payload={})
        forget_jobs([job.pk])
        assert forget_jobs([job.pk]) == 0
```

```python
# append to tools/vision/tests/test_retention.py -- this module said
# nothing about the queue until now
    def test_the_generations_queue_row_goes_with_it(self):
        queue_job = make_queue_job(kind="vision.generate", state=SUCCEEDED,
                                   priority=200, payload={"params": {"prompt": "x"}})
        job = _generation(queue_job_id=queue_job.pk)
        purge_artifacts([], [str(job.pk)])
        assert not InferenceJob.objects.filter(pk=queue_job.pk).exists()

    def test_a_job_with_no_queue_row_is_not_an_error(self):
        job = _generation(queue_job_id=None)
        assert purge_artifacts([], [str(job.pk)]) == 1
```

```python
# in identity/tests/test_deletion_demo.py, rename and invert the two
# "QUEUE HALF NOT LANDED" tests
    def test_the_queue_row_is_gone(self, client, world):
        """Zero `InferenceJob` rows naming that conversation, in the same
        response cycle as the click -- which is exactly what the import
        law made impossible from `agents/` and exactly why the
        orchestration lives in `identity/`."""
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            assert InferenceJob.objects.filter(
                payload__conversation=str(world.conversation.pk)).count() == 0

    def test_the_generations_queue_row_is_gone(self, client, world):
        """The `vision.generate` row carries the prompt in
        `payload["params"]`, and it is reachable only through
        `GenerationJob.queue_job_id` -- collected by the vision handler
        before it deletes the job, and handed over the contracts seam."""
        with posture("personal"):
            sign_in(client, world.user)
            client.post(reverse("chat-conversation-delete",
                                args=[world.conversation.id]))
            ticket = DeletionTicket.objects.get(kind=copy.KIND_CONVERSATION)
            client.post(reverse("identity-deleted-purge", args=[ticket.pk]))
            assert not InferenceJob.objects.filter(
                pk=world.generation_queue_job.pk).exists()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q models/contracts/tests/test_queue.py tools/vision/tests/test_retention.py identity/tests/test_deletion_demo.py`
Expected: FAIL — `ImportError: cannot import name 'forget_jobs'`.

- [ ] **Step 3: Add the backend function**

In `models/queue/backend.py`, beside `cancel_job`:

```python
@_guarded
def forget_jobs(job_ids) -> int:
    """Delete these job rows outright. Returns how many went.

    THE ONE CASE PAYLOAD-KEYING CANNOT ANSWER. A `vision.generate`
    payload carries no generation id -- the `GenerationJob` row is
    created by the handler at run time -- so the only link is
    `GenerationJob.queue_job_id`, in the other direction. The owning
    column collects those ids from the jobs it is about to delete and
    hands them here through `models.contracts.queue`.

    Idempotent, and an unknown id is a no-op: a filtered delete removes
    zero rows and returns zero.

    NO CANCEL STEP, deliberately, unlike `models.queue.retention.
    _forget`: the caller here holds generation jobs it has already
    resolved, and a `vision.generate` row has no placeholder turn for an
    `on_terminal` hook to close -- `agents.runtime.jobs.on_turn_terminal`
    is the `agent.turn` kind's hook, not this one's.
    """
    ids = [job_id for job_id in job_ids or () if job_id]
    if not ids:
        return 0
    deleted, _by_model = InferenceJob.objects.filter(pk__in=ids).delete()
    return deleted
```

- [ ] **Step 4: Add the contracts passthrough**

In `models/contracts/queue.py`, after `get_job`:

```python
def forget_jobs(job_ids) -> int:
    """Delete the named job rows through the configured backend.

    A thin passthrough, exactly like `enqueue` and `get_job` above and
    dispatched the same way (`settings.INFERENCE_QUEUE_BACKEND`,
    re-imported per call). It exists so a `tools/*` column can remove the
    queue rows it owns the other end of -- `tools.vision.retention`
    collects `GenerationJob.queue_job_id` values for the jobs it is
    deleting -- WITHOUT importing `models.queue` at all, which is the
    exact purpose this module's own docstring gives for existing.

    An empty sequence dispatches nothing: there is no backend call to
    make, and a never-500 caller should not pay an import for one.
    """
    ids = [job_id for job_id in job_ids or () if job_id]
    if not ids:
        return 0
    backend = _backend()
    return backend.forget_jobs(ids)
```

- [ ] **Step 5: Use it from the vision handler**

In `tools/vision/retention.py::purge_artifacts`, collect the queue ids **before** deleting the jobs and forget them after:

```python
from models.contracts.queue import forget_jobs

# ... the reference/id parsing above is unchanged; this replaces the
# closing block of `purge_artifacts`, from `if not job_ids:` onward:
    if not job_ids:
        return 0

    jobs = list(GenerationJob.objects.filter(pk__in=job_ids))
    # COLLECTED BEFORE THE DELETE: `queue_job_id` lives on the job row,
    # so after `delete_job` there is no path back to it. The link exists
    # in this direction only -- a `vision.generate` payload carries no
    # generation id, because the row is created by the handler at run
    # time.
    queue_ids = [job.queue_job_id for job in jobs if job.queue_job_id]
    deleted = 0
    for job in jobs:
        services.delete_job(job)
        deleted += 1
    forget_jobs(queue_ids)
    return deleted
```

`forget_jobs`' own count is deliberately **not** added to `deleted`: the returned integer is what the audit event's `removed` map reports for this column, and a queue row is bookkeeping rather than one of this column's images.

- [ ] **Step 6: Flip the two gate tests**

Both in `identity/tests/test_deletion_demo.py`, exactly as Step 1 shows. Delete the "QUEUE HALF NOT LANDED" docstrings with them.

- [ ] **Step 7: Document it and run the tests**

Add `forget_jobs` on the contracts seam to `models/README.md`. Then:

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q` and `FARABUNKER_FEATURES='vision' .venv/bin/pytest -q`, both collection orders, plus the two posture sweeps.
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add models/contracts/queue.py models/queue/backend.py tools/vision/retention.py \
        models/README.md models/contracts/tests/ tools/vision/tests/ identity/tests/
git commit -m "feat(queue): forget_jobs on the contracts seam, and the queue half completed

Re-pins identity/tests/test_deletion_demo.py's two queue-half markers: the queue rows now go with the purge."
```

---

# SLICE 2 — documents, Ask history and the gallery

### Task 20: The `document` kind

**Files:**
- Modify: `tools/rag/retention.py` (`purge_document`)
- Modify: `tools/rag/apps.py` (register it)
- Modify: `tools/rag/views.py` (`document_delete` tickets)
- Modify: `tools/rag/README.md`
- Test: `tools/rag/tests/test_retention.py` (extend), `tools/rag/tests/test_views_documents.py` (re-pin)

**Held tests:**
- `tools/rag/tests/test_services.py::TestDeleteDocument::*` — unchanged; `services.delete_document` keeps its behaviour and its files-then-row ordering, and `purge_document` is a thin wrapper over it. Its `test_order_of_operations_files_then_row_deleted` stays green.
- Every test posting `rag-document-delete` (find them with `grep -rn "rag-document-delete" tools/rag/tests`) currently asserts the row is gone the instant the POST returns. **Re-pin:** the row survives, a `DeletionTicket` exists, and the document is absent from `listable_documents`. Name them in the commit message.

**Interfaces:**
- Produces: `tools.rag.retention.purge_document(key: str) -> int`; registration `RetentionHandler(kind=KIND_DOCUMENT, key="rag.document", label="Documents", handler="tools.rag.retention.purge_document", order=ORDER_FILES)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tools/rag/tests/test_retention.py
class TestPurgeDocument:
    def test_it_leaves_zero_chunks_for_that_file_id(self):
        """THE AUDIT'S REFUTATION, RE-VERIFIED RATHER THAN ASSUMED.
        `tools.rag.index.delete_chunks_for_document` filters on
        `metadata_->>'file_id'`, which IS the `Document` pk -- so this
        test asserts a zero chunk count for that key after a purge,
        rather than trusting the earlier finding."""
        document = _an_ingested_document_with_chunks()
        assert _chunk_count_for(document.pk) > 0
        purge_document(str(document.pk))
        assert _chunk_count_for(document.pk) == 0

    def test_it_removes_the_row_its_rows_and_its_managed_store_directory(self):
        document = _an_ingested_document_with_chunks()
        store_dir = _store_dir_for(document)
        purge_document(str(document.pk))
        assert not Document.objects.filter(pk=document.pk).exists()
        assert not DocumentRow.objects.filter(document_id=document.pk).exists()
        assert not store_dir.exists()

    def test_it_is_idempotent(self):
        document = make_document()
        purge_document(str(document.pk))
        assert purge_document(str(document.pk)) == 0

    def test_a_non_numeric_key_removes_nothing(self):
        assert purge_document("not-a-number") == 0
```

```python
# append to tools/rag/tests/test_views_documents.py
class TestTheLibraryDeleteTickets:
    def test_the_row_survives_and_the_document_leaves_every_listing(self, client):
        document = make_document()
        client.post(reverse("rag-document-delete", args=[document.pk]))
        assert Document.objects.filter(pk=document.pk).exists()
        assert document not in listable_documents(OPEN_PRINCIPAL)
        assert DeletionTicket.objects.filter(
            kind="document", key=str(document.pk)).exists()

    def test_the_ticket_carries_the_documents_title(self, client):
        document = make_document(title="Q3 report")
        client.post(reverse("rag-document-delete", args=[document.pk]))
        assert DeletionTicket.objects.get().label == "Q3 report"

    def test_a_second_delete_of_the_same_document_writes_no_second_ticket(self, client):
        document = make_document()
        client.post(reverse("rag-document-delete", args=[document.pk]))
        client.post(reverse("rag-document-delete", args=[document.pk]))
        assert DeletionTicket.objects.count() == 1

    def test_the_gate_is_unchanged(self, client):
        """`may_administer_document` still decides, with its three
        admitting cases and its 403-not-404 shape for a real row a
        principal has no standing over -- ticketing changed WHEN the
        content dies, never WHO may say so."""
        with posture("personal"):
            stranger = make_user()
            document = make_document()
            sign_in(client, stranger)
            response = client.post(
                reverse("rag-document-delete", args=[document.pk]))
        assert response.status_code == 403
        assert DeletionTicket.objects.count() == 0
```

Fill the three chunk/store helpers with this module's real names: `_an_ingested_document_with_chunks`, `_chunk_count_for` and `_store_dir_for` stand for whatever `tools/rag/tests/test_index.py` and `test_store.py` already use — read them and reuse, do not write new ones.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest -q tools/rag/tests/test_retention.py tools/rag/tests/test_views_documents.py`
Expected: FAIL — `ImportError: cannot import name 'purge_document'`.

- [ ] **Step 3: Add `purge_document`**

In `tools/rag/retention.py`:

```python
def purge_document(key: str) -> int:
    """Destroy one library document: its chunks, its managed store
    directory, its row, and by CASCADE its `DocumentRow`s and
    `DocumentAttachment`s. Returns 1 if it went, 0 if it was already
    gone.

    A THIN WRAPPER over `services.delete_document`, unchanged -- the
    same function the library's own delete has always called. Nothing
    about document teardown is re-implemented here; what is new is only
    WHEN it runs.

    THE CHUNK DELETE IS VERIFIED, NOT ASSUMED:
    `tools.rag.index.delete_chunks_for_document` keys on
    `metadata_->>'file_id'`, which is the `Document` pk, and
    `tools/rag/tests/test_retention.py` asserts a zero chunk count for
    that key after this runs.

    FILES band: it removes bytes.
    """
    if not str(key or "").isdecimal():
        return 0
    document = Document.objects.filter(pk=int(key)).first()
    if document is None:
        return 0
    services.delete_document(document)
    return 1
```

Register it in `tools/rag/apps.py::ready()` beside the notes handler, with `kind=KIND_DOCUMENT`, `key="rag.document"`, `label="Documents"`, `order=ORDER_FILES`.

- [ ] **Step 4: Make `document_delete` ticket**

In `tools/rag/views.py`:

```python
from identity.contracts.retention import KIND_DOCUMENT
from identity.retention import delete_content

# ... and inside `document_delete`, the last two lines become three:
    principal, document = _administered_document(request, doc_id, _DELETE_FORBIDDEN_MESSAGE)
    if isinstance(document, HttpResponseForbidden):
        return document
    # IT TICKETS; IT DOES NOT ERASE. The chunks, the store directory and
    # the row survive until the date the Deleted page prints, and
    # `tools.rag.retention.purge_document` destroys them then. The GATE
    # is unchanged -- `may_administer_document`, with its three admitting
    # cases and its 403-not-404 shape.
    delete_content(principal, kind=KIND_DOCUMENT, key=document.pk,
                   owner=document, label=document.title or "")
    return redirect("rag-documents")
```

Update the view's docstring: it no longer "delegates to `services.delete_document`", it tickets; `purge_document` delegates.

- [ ] **Step 5: Document, re-pin and run**

Add `purge_document` to `tools/rag/README.md`'s handler section. Re-pin the `rag-document-delete` tests.

Run: `.venv/bin/pytest -q tools/rag identity foundation/ops/tests/test_deletion_coverage.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/rag/retention.py tools/rag/apps.py tools/rag/views.py \
        tools/rag/README.md tools/rag/tests/
git commit -m "feat(rag): the library delete writes a ticket

Re-pins every tools/rag/tests test that posts rag-document-delete: the row now survives until its purge date."
```

---

### Task 21: The `ask` kind — and a reading surface that stops being read-only

**Files:**
- Modify: `tools/rag/retention.py` (`purge_ask`)
- Modify: `tools/rag/apps.py` (register it)
- Modify: `tools/rag/access.py` (`visible_ask_records` exclusion)
- Modify: `tools/rag/views.py` (`ask_delete`), `tools/rag/urls.py`, `identity/routes.py`, `identity/tests/test_route_matrix.py`
- Modify: `tools/rag/templates/rag/history.html` (the per-row affordance)
- Modify: `tools/rag/README.md`
- Test: `tools/rag/tests/test_retention.py` (extend), `tools/rag/tests/test_views_upload_and_settings.py::TestHistoryView` (extend), `identity/tests/test_retention_runner.py` (re-pin)

**Held tests:**
- `tools/rag/tests/test_views_upload_and_settings.py::TestHistoryView::*` — the page currently renders **no form at all**; `rag/history.html`'s own comment says so, and `HistoryView`'s docstring ends *"This is a reading surface, and it reads."* **Re-pin:** the page now renders one form per row. Update the docstring and the template comment in the same commit — a docstring that says the opposite of the code is the failure this repo's review catches.
- `identity/tests/test_retention_runner.py` needs **no** re-pin, and that is what its `_isolated_registry` fixture bought: the runner sees only the handlers each test registered, so a real production handler arriving for `KIND_ASK` here changes nothing in that module. Re-run it and confirm.

**Interfaces:**
- Produces: `tools.rag.retention.purge_ask(key: str) -> int`; route `rag-ask-delete` at `/rag/history/<int:pk>/delete/`, class **O** (row-addressed mutation of owned content, 404 for a principal with no standing).

- [ ] **Step 1: Write the failing tests**

```python
# append to tools/rag/tests/test_retention.py
class TestPurgeAsk:
    def test_it_deletes_the_record(self):
        record = AskRecord.objects.create(question="q", answer="a", citations=[],
                                          connection_name="c", model_id="m")
        assert purge_ask(str(record.pk)) == 1
        assert not AskRecord.objects.filter(pk=record.pk).exists()

    def test_it_is_idempotent(self):
        record = AskRecord.objects.create(question="q", answer="a", citations=[],
                                          connection_name="c", model_id="m")
        purge_ask(str(record.pk))
        assert purge_ask(str(record.pk)) == 0

    def test_a_non_numeric_key_removes_nothing(self):
        assert purge_ask("not-a-number") == 0
```

```python
# append to tools/rag/tests/test_views_upload_and_settings.py
class TestTheHistoryDelete:
    def test_the_page_renders_one_delete_form_per_row(self, client):
        record = _a_record()
        body = client.get(reverse("rag-history")).content.decode()
        assert reverse("rag-ask-delete", args=[record.pk]) in body

    def test_a_delete_tickets_and_the_row_leaves_the_page(self, client):
        record = _a_record()
        client.post(reverse("rag-ask-delete", args=[record.pk]))
        assert AskRecord.objects.filter(pk=record.pk).exists()
        assert record not in visible_ask_records(OPEN_PRINCIPAL)
        assert DeletionTicket.objects.filter(kind="ask").exists()

    def test_a_ticketed_record_is_absent_from_the_pages_context(self, client):
        record = _a_record()
        client.post(reverse("rag-ask-delete", args=[record.pk]))
        response = client.get(reverse("rag-history"))
        assert list(response.context["records"]) == []

    def test_somebody_elses_record_is_404_never_403(self, client):
        with posture("personal"):
            mine, theirs = make_user(), make_user()
            record = _a_record(owner_kind="user", owner_key=str(theirs.pk))
            sign_in(client, mine)
            assert client.post(
                reverse("rag-ask-delete", args=[record.pk])).status_code == 404

    def test_a_get_is_405(self, client):
        record = _a_record()
        assert client.get(
            reverse("rag-ask-delete", args=[record.pk])).status_code == 405
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest -q tools/rag/tests/test_retention.py tools/rag/tests/test_views_upload_and_settings.py`
Expected: FAIL — `NoReverseMatch: Reverse for 'rag-ask-delete' not found`.

- [ ] **Step 3: Add `purge_ask` and the exclusion**

In `tools/rag/retention.py`:

```python
def purge_ask(key: str) -> int:
    """Destroy one Ask record: its question, its answer and its
    citations. Returns 1 if it went, 0 if it was already gone.

    ROWS band: it touches no file. `AskRecord` has no foreign key to
    anything, so there is nothing to cascade and nothing to repair.

    NO QUEUE HANDLER FOR THIS KIND. A `rag.ask` payload carries the
    question text and the actor and NO reference to the record
    `services.record_ask` later writes, so there is no id to key on;
    those rows go to the queue's own age cliff, and that residue is
    named in ADR 0019 rather than papered over.
    """
    if not str(key or "").isdecimal():
        return 0
    deleted, _by_model = AskRecord.objects.filter(pk=int(key)).delete()
    return deleted
```

Register it with `kind=KIND_ASK`, `key="rag.ask_record"`, `label="Ask records"`, default `ORDER_ROWS`.

In `tools/rag/access.py::visible_ask_records`, exclude on the base **before** the early return:

```python
    qs = AskRecord.objects.exclude(pk__in=ticketed_keys(KIND_ASK))
    if sees_all_content(principal):
        return qs
    return qs.filter(owned_rows_q(principal))
```

- [ ] **Step 4: Add the route and the affordance**

`tools/rag/views.py`:

```python
@require_POST
def ask_delete(request, pk: int):
    """POST /rag/history/<pk>/delete/ -- delete one Ask record.

    THE FIRST WRITE THIS PAGE HAS EVER HAD. `HistoryView`'s own
    docstring used to end "This is a reading surface, and it reads" --
    true until now, and the reason `AskRecord` rows were undeletable by
    any path except FIFO pruning on insert. It tickets, like every other
    delete on this box.

    OWNED CONTENT, class O: the record's own owner, or a principal that
    `sees_all_content`. 404, never 403, for anybody else -- a 403 on a
    row-addressed URL confirms the row exists.
    """
    principal = principal_for_request(request)
    record = visible_ask_records(principal).filter(pk=pk).first()
    if record is None:
        raise Http404("No such question.")
    delete_content(principal, kind=KIND_ASK, key=record.pk, owner=record,
                   label=record.question[:255])
    messages.info(request, "Deleted. You can restore it from Settings → Deleted.")
    return redirect("rag-history")
```

`tools/rag/urls.py`: `path("history/<int:pk>/delete/", ask_delete, name="rag-ask-delete")`, and add `ask_delete` to the import list.

`identity/routes.py`: `"rag-ask-delete": "O",` beside `"rag-history": "A"`, with a comment saying why O (row-addressed, owned content) rather than R.

`identity/tests/test_route_matrix.py::_DRIVERS`: `"rag-ask-delete": lambda w: ("post", reverse("rag-ask-delete", args=[w.ask_record.pk]), {})` — and add an `AskRecord` owned by `w.other` to `_build_world`.

`tools/rag/templates/rag/history.html`: one `<details>` confirm per row, the same dialog-free shape the chat and gallery deletes use. Rewrite the template's own comment — it currently says the page "writes anything, so it renders no form", which is now false.

- [ ] **Step 5: Update the docstrings, document and run**

`HistoryView`'s docstring loses "This is a reading surface, and it reads" and gains one sentence saying it now carries one write — the per-row delete — and nothing else. Add `purge_ask` to `tools/rag/README.md`.

Run: `.venv/bin/pytest -q tools/rag identity`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/rag/ identity/routes.py identity/tests/
git commit -m "feat(rag): Ask history gains a delete, and stops being read-only

Re-pins tools/rag/tests/test_views_upload_and_settings.py::TestHistoryView: the page now renders one form per row, and HistoryView's docstring no longer says it only reads."
```

---

### Task 22: The `vision_job` kind

**Steward: the vision steward.** Clearance packet: the diff against `tools/vision/services.py`, `tools/vision/views.py`, `tools/vision/retention.py` and `tools/vision/apps.py`.

**Files:**
- Modify: `tools/vision/retention.py` (`purge_job`)
- Modify: `tools/vision/apps.py` (register it)
- Modify: `tools/vision/views.py` (`job_delete`, `jobs_delete_selected`)
- Modify: `tools/vision/README.md`
- Test: `tools/vision/tests/test_retention.py` (extend), `tools/vision/tests/test_views_gallery.py` (re-pin)

**Held tests:**
- `tools/vision/tests/test_views_gallery.py::TestGalleryDelete::*` and `::TestJobsDeleteSelected::test_deletes_only_the_checked_jobs` — both assert rows and files are gone the instant the POST returns. **Re-pin:** the rows survive, tickets exist, and the jobs are absent from `visible_jobs`. The bulk delete's "silently DROPPED, never 404" behaviour for a foreign or malformed id is **unchanged** and must stay green.
- `identity/tests/test_route_matrix.py::TestVisionJobsDeleteSelectedFilters` — four cells asserted directly against the database (a member deletes their own selection; foreign ids are dropped and the row survives; an admin without the content setting drops foreign ids; an admin with it on deletes). **Re-pin:** "deletes" becomes "tickets"; "the row survives" is now true of every row, so assert on ticket existence rather than row absence. Name it in the commit message.
- `tools/vision/tests/test_services.py`'s `delete_job` tests — unchanged; `delete_job` keeps its behaviour and `purge_job` wraps it.

**Interfaces:**
- Produces: `tools.vision.retention.purge_job(key: str) -> int`; registration `RetentionHandler(kind=KIND_VISION_JOB, key="vision.job", label="Generated images", handler="tools.vision.retention.purge_job", order=ORDER_FILES)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tools/vision/tests/test_retention.py
class TestPurgeJob:
    def test_it_removes_the_row_its_children_its_directory_and_its_queue_row(self):
        queue_job = make_queue_job(kind="vision.generate", state=SUCCEEDED,
                                   priority=200, payload={"params": {"prompt": "x"}})
        job = _generation(queue_job_id=queue_job.pk)
        _output(job=job)
        assert purge_job(str(job.pk)) == 1
        assert not GenerationJob.objects.filter(pk=job.pk).exists()
        assert not GeneratedOutput.objects.filter(job_id=job.pk).exists()
        assert not InferenceJob.objects.filter(pk=queue_job.pk).exists()

    def test_it_is_idempotent(self):
        job = _generation()
        purge_job(str(job.pk))
        assert purge_job(str(job.pk)) == 0

    def test_a_non_uuid_key_removes_nothing(self):
        assert purge_job("not-a-uuid") == 0
```

```python
# append to tools/vision/tests/test_views_gallery.py
class TestTheGalleryDeleteTickets:
    def test_a_single_delete_tickets_and_the_figure_leaves_the_gallery(self, client):
        job = _generation()
        client.post(reverse("vision-job-delete", args=[job.pk]))
        assert GenerationJob.objects.filter(pk=job.pk).exists()
        assert list(visible_jobs(OPEN_PRINCIPAL)) == []
        assert DeletionTicket.objects.filter(
            kind="vision_job", key=str(job.pk)).exists()

    def test_the_bulk_delete_tickets_every_checked_job(self, client):
        first, second, kept = _generation(), _generation(), _generation()
        client.post(reverse("vision-jobs-delete-selected"),
                    {"jobs": [str(first.pk), str(second.pk)]})
        assert DeletionTicket.objects.filter(kind="vision_job").count() == 2
        assert list(visible_jobs(OPEN_PRINCIPAL)) == [kept]

    def test_a_foreign_or_malformed_id_is_still_silently_dropped(self, client):
        """UNCHANGED: a bulk action addresses many rows behind one
        submit, so there is no single "which one" a 404 could confirm."""
        response = client.post(reverse("vision-jobs-delete-selected"),
                               {"jobs": ["not-a-uuid", str(uuid.uuid4())]})
        assert response.status_code == 302
        assert DeletionTicket.objects.count() == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision`
Expected: FAIL — `ImportError: cannot import name 'purge_job'`.

- [ ] **Step 3: Add `purge_job`**

In `tools/vision/retention.py`:

```python
def purge_job(key: str) -> int:
    """Destroy one generation: the row, its `JobInput`/`GeneratedOutput`
    children by CASCADE, its managed directory, its best-effort
    engine-side sweep, and its queue row. Returns 1 or 0.

    A THIN WRAPPER over `services.delete_job` plus `forget_jobs` -- the
    same pair `purge_artifacts` above uses per job, and for the same
    reason: a `vision.generate` payload carries no generation id, so the
    queue row is reachable only through `GenerationJob.queue_job_id`,
    collected before the delete.

    FILES band: it removes bytes.
    """
    try:
        job_id = uuid.UUID(str(key))
    except (ValueError, AttributeError, TypeError):
        return 0
    job = GenerationJob.objects.filter(pk=job_id).first()
    if job is None:
        return 0
    queue_job_id = job.queue_job_id
    services.delete_job(job)
    if queue_job_id:
        forget_jobs([queue_job_id])
    return 1
```

Register it in `tools/vision/apps.py::ready()`, **inside** the feature gate, beside the artifact-purge registration.

- [ ] **Step 4: Route both gallery deletes through the retention service**

`tools/vision/views.py::job_delete`:

```python
    principal = principal_for_request(request)
    job = get_object_or_404(visible_jobs(principal), pk=job_id)
    # IT TICKETS. The row, its files and its queue row survive until the
    # date the Deleted page prints; `tools.vision.retention.purge_job`
    # destroys them then. The gate is unchanged -- `visible_jobs`, so a
    # principal who cannot read a job cannot delete it, and both routes
    # 404 alike.
    delete_content(principal, kind=KIND_VISION_JOB, key=job.pk, owner=job,
                   label=_prompt_label(job))
```

`_prompt_label(job)` is a small local helper returning the prompt from `job.params` truncated to 255 characters, or `""` — **the prompt is content**, and it lands on the ticket's `label`, which is destroyed with the ticket and never reaches an audit event unless `audit_detail` is on. Say that in its docstring.

`jobs_delete_selected`: replace `services.delete_job(job)` in the loop with the same `delete_content` call, and keep the message, the dedupe, the UUID parsing and the silent-drop behaviour exactly as they are. The count in the success message is now the number **ticketed**; keep the wording ("Deleted N generations.") — that is what happened from the person's point of view.

- [ ] **Step 5: Document, re-pin and run**

`tools/vision/README.md` gains `purge_job` beside `purge_artifacts`, and one line that the gallery's two deletes now ticket.

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools/vision identity` then `FARABUNKER_FEATURES='vision' .venv/bin/pytest -q tools/vision`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/vision/ identity/tests/
git commit -m "feat(vision): the gallery's deletes write tickets

Re-pins tools/vision/tests/test_views_gallery.py::TestGalleryDelete and ::TestJobsDeleteSelected, and identity/tests/test_route_matrix.py::TestVisionJobsDeleteSelectedFilters: a delete now tickets rather than erasing."
```

---

### Task 23: The page's per-kind rows, the coverage gate's full shape, and the last docs

**Files:**
- Modify: `identity/views.py` (`deleted_page` — per-kind links)
- Modify: `identity/templates/identity/deleted.html`
- Modify: `foundation/ops/tests/test_deletion_coverage.py` (two `_EXEMPT` lines move into `_COVERED`)
- Modify: `docs/adr/0019-deletion-and-retention.md`, `docs/EXTENDING.md`
- Modify: `identity/tests/test_deletion_demo.py` (step 4 becomes real)
- Test: `identity/tests/test_deleted_page.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# append to identity/tests/test_deleted_page.py
class TestEveryKindRendersOnOnePage:
    @pytest.mark.parametrize("kind", list(copy.RETENTION_KINDS))
    def test_each_kind_shows_its_plain_label_and_its_date(self, client, kind):
        with posture("open"):
            DeletionTicket.objects.create(
                kind=kind, key="1", purge_on=timezone.localdate(),
                label="An item")
            body = client.get(reverse("identity-deleted")).content.decode()
        assert copy.KIND_LABELS[kind] in body

    def test_four_kinds_sit_on_one_list_newest_first(self, client):
        with posture("open"):
            for index, kind in enumerate(copy.RETENTION_KINDS):
                DeletionTicket.objects.create(
                    kind=kind, key=str(index),
                    purge_on=timezone.localdate() + datetime.timedelta(days=30))
            response = client.get(reverse("identity-deleted"))
        assert len(response.context["tickets"]) == 4
```

```python
# in foundation/ops/tests/test_deletion_coverage.py
_COVERED = {
    "agents.Conversation": (KIND_CONVERSATION,),
    "agents.Turn": (KIND_CONVERSATION,),
    "agents.ToolInvocation": (KIND_CONVERSATION,),
    "rag.Document": (KIND_CONVERSATION, KIND_DOCUMENT),
    "rag.DocumentRow": (KIND_CONVERSATION, KIND_DOCUMENT),
    "rag.DocumentAttachment": (KIND_CONVERSATION, KIND_DOCUMENT),
    "rag.AskRecord": (KIND_ASK,),
    "vision.GenerationJob": (KIND_VISION_JOB,),
    "vision.JobInput": (KIND_VISION_JOB,),
    "vision.GeneratedOutput": (KIND_VISION_JOB,),
    "jobs.InferenceJob": (KIND_CONVERSATION, KIND_DOCUMENT),
}

# and the two Slice-1 lines LEAVE `_EXEMPT`, which keeps only the four
# tables a deletion genuinely never reaches:
_EXEMPT: dict[str, str] = {
    "agents.Agent": "an agent definition is a setting a person authored, "
                    "deleted from its own page, not content a deletion reaches",
    "agents.Flow": "a flow definition, for the same reason as Agent above",
    "agents.Workstream": "a container, deliberately not a ticket kind -- its "
                         "contents each have their own cliff (spec section 10.2)",
    "identity.DeletionTicket": "the deletion bookkeeping itself -- its owner "
                               "columns name the ITEM's owner, and the ticket is "
                               "destroyed by the purge it records",
}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q foundation/ops/tests/test_deletion_coverage.py identity/tests/test_deleted_page.py`
Expected: FAIL.

- [ ] **Step 3: Make the page kind-aware**

`deleted_page` already renders `KIND_LABELS[ticket.kind]`, so all four kinds work with no change — this step is to **verify that** with the parametrised test above, and to add nothing else. Specifically: **no per-kind link back to the item.** A ticketed item is invisible on every surface, so a link would resolve to a 404; the row says what it was and what it was called, which is what the promise needs.

If the parametrised test passes with no code change, say so in the commit body rather than inventing work.

- [ ] **Step 4: Fill the coverage gate**

As Step 1 shows: `rag.AskRecord` and `vision.GenerationJob` move from `_EXEMPT` to `_COVERED` with their kinds, `rag.Document` and its two children gain `KIND_DOCUMENT`, `jobs.InferenceJob` gains `KIND_DOCUMENT`, and `vision.JobInput`/`vision.GeneratedOutput` join. Re-add the `KIND_ASK` / `KIND_DOCUMENT` / `KIND_VISION_JOB` imports that Task 13 dropped.

- [ ] **Step 5: Make the demo's step 4 real**

In `identity/tests/test_deletion_demo.py`, rewrite `test_step_4_the_ask_record_is_deleted_on_its_own` to post `rag-ask-delete` and assert the record is absent from `HistoryView`'s context — the spec's step 4, now buildable.

- [ ] **Step 6: Amend the ADR and the recipe**

`docs/adr/0019-deletion-and-retention.md` gains a dated amendment paragraph (this record is *amended with dated amendments rather than silently rewritten*) recording that slice 2 landed and the coverage gate reached its full shape. `docs/EXTENDING.md`'s recipe gains nothing new — verify its `_COVERED`/`_EXEMPT` instruction still reads correctly now that two lines have moved between them.

- [ ] **Step 7: Run the whole gate**

Run all four runs in both collection orders plus the two posture sweeps.
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add identity/ foundation/ops/tests/test_deletion_coverage.py docs/
git commit -m "feat(identity): all four kinds on the Deleted page, and the coverage gate's full shape"
```

---

## Held tests, in one list

Every existing test this plan changes, with the task that changes it. A change that deliberately re-pins one says so, by name, in its commit message.

| Test | Task |
|---|---|
| `identity/tests/test_actions.py::test_every_name_carries_its_family_prefix` (16 prefixes → 17, with `content.`) | 3 |
| `identity/tests/test_actions.py::test_the_full_vocabulary_is_declared_now_not_in_two_passes` (`len(AUDIT_ACTIONS)` 68 → 72) | 3 |
| `identity/tests/test_purity.py::_PROBE` | 1 |
| `identity/tests/test_settings_page.py` (every posture-form POST body) | 7 |
| `identity/tests/test_services.py` (every `set_posture` call) | 7 |
| `foundation/ops/tests/test_import_law.py::test_the_identity_private_module_gate_would_catch_a_violation` | 5 |
| `foundation/ops/tests/test_import_law.py::test_the_allowlist_closes_a_module_added_after_it_was_written` (must stay green) | 5 |
| `foundation/ops/tests/test_import_law.py::test_identity_testing_is_closed_to_production_by_the_same_mechanism` (must stay green) | 5 |
| `foundation/ops/tests/test_column_boundaries.py` (the `AuditEvent.objects` AST guard — must stay green) | 3, 13 |
| `agents/chat/tests/test_delete.py::TestTheRowsSurviveADelete::test_the_conversation_and_its_turns_survive_and_a_ticket_hides_them` | 8 |
| `agents/chat/tests/test_delete.py::TestTheRowsSurviveADelete::test_the_index_shows_a_deleted_notice` | 8 |
| `agents/chat/tests/test_delete.py::TestAttachmentRowsSurviveADelete::test_deleting_a_conversation_leaves_its_attachment_rows` | 8 → 9 |
| `agents/chat/tests/test_delete.py::TestAttachmentRowsSurviveADelete::test_a_different_conversations_attachment_row_is_untouched` | 8 → 9 |
| `agents/chat/tests/test_delete.py::TestConversationDeleteCascadesChatScopedDocuments::test_a_chat_scoped_documents_delete_document_is_called` | 8 → 9 |
| `agents/chat/tests/test_delete.py::TestConversationDeleteCascadesChatScopedDocuments::test_a_universal_documents_attachment_row_is_removed_but_the_document_survives` | 8 → 9 |
| `agents/chat/tests/test_delete.py::TestTheCleanupSavepoint::test_a_database_error_in_cleanup_does_not_block_the_delete` | 8 → 9 |
| `agents/chat/tests/test_delete.py::TestTheAuditSurvives::*` (must stay green) | 8 |
| `agents/chat/tests/test_visibility.py::TestTheOpenBranchIsFirst::test_an_open_box_asks_the_user_table_nothing` (2 queries → 3) | 8 |
| `agents/chat/tests/test_visibility.py::TestTheOpenBranchIsFirst::test_an_open_box_builds_no_ownership_filter_at_all` (must stay green) | 8 |
| `agents/chat/tests/test_visibility.py::TestSharingAConversation::test_deleting_a_conversation_deletes_its_shares` | 8 → 9 |
| `agents/chat/tests/test_visibility.py::TestDeleteConversation::*` (answers unchanged) | 8 |
| `agents/chat/tests/test_visibility.py::TestServiceOwnedRows::test_an_admin_may_delete_a_conversation_a_shell_path_made` | 8 |
| `tools/rag/tests/test_access_documents.py` (the `django_assert_num_queries` pins) | 10 |
| `tools/rag/tests/test_chat_scoped_documents.py` (the query pins) | 10 |
| `tools/rag/tests/test_services.py::TestDeleteDocument::*` (must stay green) | 20 |
| every `tools/rag/tests` test posting `rag-document-delete` | 20 |
| `tools/rag/tests/test_views_upload_and_settings.py::TestHistoryView::*` | 21 |
| `tools/vision/tests/test_visibility.py::TestGeneratedImagesAreContent::*` | 11 |
| `tools/vision/tests/test_views_gallery.py::TestGalleryDelete::*` | 22 |
| `tools/vision/tests/test_views_gallery.py::TestJobsDeleteSelected::test_deletes_only_the_checked_jobs` | 22 |
| `identity/tests/test_route_matrix.py::TestTheTableIsComplete::test_every_route_has_a_driver` | 12, 21 |
| `identity/tests/test_route_matrix.py::TestVisionJobsDeleteSelectedFilters` | 22 |
| `foundation/tests/test_settings_area.py::TestTheTable::test_a_household_box_gets_setup_and_nothing_else` | 12 |
| `foundation/tests/test_settings_area.py::TestTheTable::test_a_member_gets_only_the_ungated_entry` | 12 |
| `foundation/tests/test_settings_area.py::TestTheTable::test_an_administrator_on_an_accounts_box_gets_every_group` | 12 |
| `foundation/tests/test_shell.py::TestTheSettingsSidebar::test_a_member_gets_only_what_a_member_can_open` | 12 |
| `foundation/tests/test_shell.py::TestTheSettingsSidebar::test_a_member_is_not_left_with_an_empty_sidebar` | 12 |
| `foundation/tests/test_shell.py::TestTheSettingsSidebar::test_the_sidebar_is_the_table_and_lands_where_settings_sends_you` (must stay green) | 12 |
| `foundation/tests/test_settings_help.py::TestTheDriftGuard::*` and `::TestTheAnchors::*` | 7, 12 |
| `foundation/tests/test_page_names.py::_NAMES` | 12 |
| `models/queue/tests/test_backend.py` (the three tests patching `_prune_finished_jobs`) | 18 |
| `identity/tests/test_deletion_demo.py` (the two queue-half markers) | 14 → 19 |

---

## Smoke Checklist

Walked by hand, in a browser, on this branch's preview stack — not a green terminal (`AGENTS.md`, merge readiness).

- [ ] Start a conversation, send a message, generate an image in it, attach a file to it, ask a question on the Ask page.
- [ ] Delete the conversation from the thread page. It leaves the sidebar, the conversations browser and the Queue page's readable rows immediately.
- [ ] Settings → Deleted: the row is there, named, with **"Purge on ‹date›"** thirty days out. No "hold", no "ticket", no "cliff" anywhere on the page.
- [ ] Restore it. It is back in the sidebar, with its turns and its attachment.
- [ ] Delete it again, then **Delete permanently**. After the redirect: absent from the chat list, absent from the Queue page, the generated image gone from the gallery, the attached chat-scoped file gone from the library, the Deleted tab empty, and one content-free line on the Deletion log.
- [ ] Set **Keep deleted items for** to `0`. Delete a second conversation: it is gone immediately, with no row on the Deleted tab and one event on the Deletion log.
- [ ] Turn **Show item names in the deletion log** on, delete a third item, and confirm the label appears on that line only.
- [ ] Delete a library document, an Ask-history row and a gallery image. Each leaves its own page and appears on Deleted.
- [ ] Start a long turn and delete its conversation while it is **running**; click Delete permanently. The page shows the refusal sentence, the item stays invisible, and a click after the job finishes completes it.
- [ ] Set **Keep finished queue jobs for** to `1`, enqueue anything, and confirm finished rows older than a day leave the Queue page.
- [ ] Do the whole walk once more with **JavaScript off**: every delete, restore and permanent delete is a form post and works unchanged.
- [ ] `manage.py purge_deleted` on the preview stack prints a count and writes `cli` events.

---

## Self-review

### 1. Spec coverage, section by section

| Spec section | Where it lands |
|---|---|
| §1 the owner's requirement; the four rulings | Rulings 1 (inline scrub) → Task 9; 2 (queue rows deleted, cancel first) → Tasks 17, 18; 3 (existing residue to the age cliff, no one-shot command) → Task 18; 4 (audit always written, detail toggle) → Tasks 3, 5, 7 |
| §1 O1/O2/O3 (the three outcomes) | Tasks 14 (demo), 12 (the page), 3+5 (the trail) |
| §2 what exists today | Evidence; nothing to build |
| §3.0 the design principle (zero setup, working defaults) | Global Constraints; asserted in Task 2's `TestTheShippedPolicyNeedsNoConfiguration` |
| §3.1 shape in one paragraph | Tasks 2, 5 |
| §3.2 the deletion ticket, incl. the hold columns and the indexes | Task 2 |
| §3.3 soft delete / restore / purge | Task 5 |
| §3.4 the exclusion, per column, before `sees_all_content` | Tasks 8 (agents), 10 (rag documents), 11 (vision), 21 (ask) |
| §3.4 chat-scoped documents follow their conversation | Task 10 |
| §3.5 the retention namespace, the bands, the failure semantics | Tasks 1, 4 |
| §3.6 the per-kind handlers | Tasks 9, 10, 17 (conversation); 17, 20 (document); 21 (ask); 22 (vision_job) |
| §3.7 conversation-born image jobs, both channels, the seam, `forget_jobs` | Tasks 9, 11, 19 |
| §3.8 the tool-record scrub | Task 9 |
| §3.9 the sweep, one due-condition, three callers | Tasks 5, 6, 12 |
| §3.10 posture policy — and enterprise behaves as personal | Task 12 (`test_the_enterprise_posture_behaves_exactly_as_personal_does`) |
| §3.11 the queue's age cliff, the three read conditions | Tasks 16, 18 |
| §3.12 audit actions and the detail toggle | Tasks 3, 5, 7 |
| §3.13 the Deleted page, the five registration places, two tabs, the copy | Tasks 12, 23 |
| §3.14 synchronous completeness | Tasks 12, 14, 19 |
| §4 data model and migrations; the three fields and their labels | Tasks 2, 7 |
| §5 error handling and edge cases (all thirteen rows) | Tasks 4, 5, 9, 10, 16, 17 — each row has a named test |
| §6 backups are their own layer | Task 15 |
| §7 tests, per column, plus the coverage gate and the demo | Tasks 1–14, 20–23 |
| §8 documentation | Column READMEs in Tasks 5, 8, 9, 10, 11, 16, 17, 18, 20, 21, 22; the three repo-wide docs in Task 15; the help cards in Tasks 7, 12, 18 |
| §9 delivery, the two slices, the gate, the import-law table | The slice headings, the ⛔ GATE, Global Constraints |
| §10 out of scope | Nothing in this plan builds any of the eleven; §10.9/§10.10/§10.11 are recorded in the ADR (Task 15) and nowhere else |
| §11 decisions | Carried into the ADR (Task 15) and into the code comments each one governs |

**Gaps found and closed during this review:** four, all recorded under "Deviations from the spec" — the `IDENTITY_PERMITTED` addition (§9's import table is unbuildable without it), `delete_content`'s `owner` argument (a `Principal` cannot be constructed by a caller), the two Slice-2 models' `_EXEMPT` lines (condition (b) would fail on the day Task 13 lands), and `identity.DeletionTicket`'s own exemption (the plan's new table carries the owner pair and would fail its own gate).

### 2. Placeholder scan

No "TBD", no "implement later", no "similar to Task N", no "add appropriate error handling". **No bare `...` body anywhere** — re-scanned after round 1, which found six and replaced every one with real code (the demo fixture, the two queue-half flips, the library-delete gate test, and two "unchanged above" markers that are now comments naming exactly which block they replace). Six places deliberately name a helper the implementer must look up in the real tree rather than inventing (`_a_conversation_with_a_turn`, `_chat_scoped_document_attached_to`, `_an_ingested_document_with_chunks`, `_chunk_count_for`, `_store_dir_for`, `_a_record`) — each says so explicitly, with the module to read, because inventing a second builder beside an existing one is the failure that instruction prevents. No step narrates a fix to code the same step just printed: round 1 found five such blocks (the `sweep` signature, the queue-cliff `0` ordering, the note-file count, the ticket lookup and the non-creating read) and each now prints the corrected code the first time.

### 3. Type and name consistency across tasks

Checked end to end: `ticketed_keys` / `visible_tickets` / `may_purge` / `delete_content` / `restore_content` / `purge_ticket` / `sweep` are spelled identically in Tasks 5, 8, 10, 11, 12, 20, 21, 22. `RetentionHandler(kind, key, label, handler, order)` matches at every one of its eight registration sites. `purge_conversation` / `purge_conversation_notes` / `purge_document` / `purge_ask` / `purge_job` / `purge_artifacts` / `forget_conversation` / `forget_document` / `forget_jobs` each appear under one name only. `KIND_CONVERSATION` / `KIND_DOCUMENT` / `KIND_ASK` / `KIND_VISION_JOB` are used, never the literals. `_prune_finished_jobs(limit, retention_days)`'s new signature matches its caller in Task 18 and its three patching tests. `queue_retention_days()` is named the same in Tasks 16, 18 and the seam docs.

**Two inconsistencies found and fixed inline.** (a) Task 17's first draft had `forget_conversation` raise `ServiceRefused`, which `models/queue` may not import; round 1 replaced the interim bare `RuntimeError` with `RetentionRefused`, declared in the pure `identity/contracts/retention.py`, which that column *may* import — Task 12's view catches both types and its refusal test is parametrised over them. (b) `delete_content` called `identity.access.owner_fields(owner)` while its own docstring said `owner` was a row; `owner_fields` reads `principal.kind`/`principal.key`, so the ticket now copies the row's `owner_kind`/`owner_key` columns directly and `owner_fields` is out of Task 5's Consumes.

**Registry isolation, added in round 1 and now consistent across every test module that registers:** `identity/tests/test_retention_contracts.py`, `test_retention_runner.py`, `test_retention_service.py`, `test_purge_deleted_command.py` and `test_deleted_page.py` each carry an `_isolated_registry` autouse fixture saving, clearing and restoring `identity.contracts.cascades._RETENTION`; `agents/tests/test_retention.py` and `agents/contracts/tests/test_artifacts.py` each carry an `_isolated_slot` fixture doing the same for `agents.contracts.artifacts._ARTIFACT_PURGE`. All seven cite `identity/tests/test_cascades.py::_isolated_registry`, which is where this codebase already records why: a module-level registry with no reset path plus a suite run in both collection orders.

---

## Plan review

- Round 1 (2026-09-21): AMEND — 4 blockers (coverage gate vs the new ticket table; `owner_fields` shape; held audit-catalogue counts; no registry isolation), 4 major, 3 moderate, 5 cuts under the owner's cost/benefit principle; author decisions 1, 2, 5 upheld, 3 and 4 overruled (no `_DEFERRED` list; `RetentionRefused` in `identity.contracts.retention`). All applied.
- Round 2 (2026-09-21): CLEAN — every amendment verified against the real source (registry isolation complete across all registering test modules; the demo test sees the real handlers; held re-pins exact); no new findings.
- **Execution amendment (Task 1 review), 2026-09-21:** two fixes ordered by the controller during Task 1's own review, applied to both the code and this plan. (1) `TAB_PURGED = "Purged"` contradicted this plan's own copy constraint that the word *purge* appears nowhere a person reads — resolved toward the setting's own wording: the constant is now `TAB_LOG = "Deletion log"`, the same phrase `LABEL_AUDIT_DETAIL` ("Show item names in the deletion log") already names, so the tab heading and the setting that controls its detail share one word. Every rendered/asserted occurrence in this plan (the constant declaration, the template's `<h2>`, the smoke checklist's two "Purged tab" lines) was updated to match; prose nicknames that are neither rendered nor asserted (docstrings, a test class name) were left as-is per the same ruling. (2) The gate bullets read as if the four runs plus two posture sweeps were a per-task requirement, which is not what any task's own brief actually asked implementers to run and is not workable at the scale of 23 tasks on a machine with a cap of two concurrent suites. Reworded, commands unchanged: the four runs plus the two posture sweeps are the **branch** gate, run at the slice-one gate (after Task 15) and again before the pull request (after pinging the queue steward); each task's own pre-commit gate is its focused test modules plus the structural gates its brief names, in both feature-flag states — matching `AGENTS.md`'s "the four runs are the gate" as a branch-level statement, not a per-task one.
- **Execution amendment (Task 5 review), 2026-09-21:** Task 5's `test_it_is_bounded_by_the_limit` interleaved four `delete_content` creates with four one-at-a-time backdates, which the unconditional prune-on-write sweep at the end of every `delete_content` call (spec section 3.9) correctly defeats — each later create's own sweep purged the previous iteration's already-overdue ticket before the test's own explicit `sweep` ran. The implementer's first pass gated that sweep on the just-created ticket's own due date to make the test pass; the controller overruled that as a fix to the wrong side: the spec is explicit that prune-on-write runs on every delete, unconditionally, so a box on the shipped 30-day default keeps itself clean with no scheduler. `identity/retention.py::delete_content` stays exactly as this plan's Step 3 prints it (`if created: sweep()`, unconditional); the test was re-shaped instead — create all four tickets first, backdate all four together in one queryset update, then assert the bounded sweep — and the behaviour the reshaped test no longer exercises (an unrelated delete purging a ticket that independently fell due) is now pinned by its own new test, `test_deleting_anything_purges_what_has_already_fallen_due`, added beside it in `TestTheSweep`. Both are reflected in Task 5's test code block above.
- **Execution amendment (Task 6 review), 2026-09-21:** Task 6: the `_overdue` helper re-shaped to create-all-then-backdate for the same reason as Task 5's limit test; command code unchanged from the brief.
- **Execution amendment (Task 5 review, round 2), 2026-09-21:** three Minor findings, all in `identity/retention.py` with tests in `identity/tests/test_retention_service.py`: already-gone purge is a no-op (`purge_ticket` re-reads the row under `select_for_update()` inside its transaction and returns `{}` with nothing run and nothing written when it is already gone); restore logs only what it removed (`restore_content` deletes by queryset and writes `content.restored` only when a row was actually removed); refusals are warnings (`sweep` catches `RetentionRefused` before the generic `except Exception` and logs it at `logger.warning` with no traceback, leaving `logger.exception` for every other failure). Reflected in Task 5's code and test blocks above.
- **Execution amendment (Task 8 review), 2026-09-21:** confirm copy no longer says purge; zero-day notice; soft-delete document pin restored; four tests renamed.
- **Execution amendment (Task 9 fix round 1), 2026-09-21:** Task 9 review: `agents.attachments.delete_attachments_for` no longer swallows a broken cleanup provider (FIX C1) — its only production caller is `agents.retention.purge_conversation` at purge time, so a failure now logs and re-raises through the runner's own never-swallows contract, re-pinning `TestTheCleanupSavepoint` to expect propagation (and full survival, driven through `identity.retention.purge_ticket`) instead of a completed purge; `purge_conversation` now hands refs/generation ids to the registered artifact-purge slot LAST, after every row delete and the tool-record scrub, so a failure upstream never deletes a file whose row then survives (FIX I2, `TestBytesGoLast`); the collect step's parse-failure log line names the turn and conversation id only, never the raw stored reference (FIX M6); and two residues are now stated as docstring sentences rather than left implicit — `WorkstreamTaint.first_conversation` keeps a purged conversation's id by value, content-free and deliberately left, and `scrub_tool_records` cannot reach a tool call whose invocation was written but whose turn never was (FIX M7/I4), mirrored into `agents/README.md`. Task 7 fix round 1: the rendered Retention help copy said "purge date"/"cliff", both banned by this plan's own copy rule (`identity/contracts/retention.py`'s docstring) — reworded to "removal date"/"that date" and "no age limit"; the queue-jobs effects sentence was also factually wrong (a finished job carries no stamped purge date) and is now "applies ... the next time the queue tidies up, which happens whenever a new job is added"; the deletion-log effects sentence now says "permanent-deletion entries" rather than "purge entries". Both fixes reflected in the code/test/doc blocks above and in `foundation/settings_help.py`/`agents/attachments.py`/`agents/retention.py`/`agents/README.md`/`tools/rag/README.md` directly.
- **Execution amendment (Task 10 review), 2026-09-21:** the "made unconditionally... THREE BOUNDED QUERIES, ALWAYS" claim this plan's own `_deleted_document_ids` code block made was checked against the real query planner and found wrong: a `filter(...__in=[])` on an empty id list is answered by the ORM without a database round trip, so the box's common state — no open conversation ticket — costs two queries, not three; a box with at least one open conversation ticket pays the third. The controller ruled this the correct behaviour, not a defect to paper over: the cheaper path is the common one, and forcing the third query to run unconditionally just to keep a test constant flat would trade a real query on the common case for a documentation convenience. The delta table, `_deleted_document_ids`'s own docstring, and the two held pins in `tools/rag/tests/test_access_documents.py` are corrected to +2/+3 throughout this task (never +3 flat, never +6); each re-pinned test gained a sibling that tickets one conversation first and asserts the one-query-more count, so both costs are held rather than only the cheaper one.
- **Execution amendment (Task 11 review), 2026-09-21:** Task 11's first draft of `purge_artifacts` — the brief's own printed Step 4, `for job in GenerationJob.objects.filter(pk__in=job_ids): services.delete_job(job)` inside `tools/vision/retention.py` — was run against the real tree and found to trip `foundation/ops/tests/test_column_boundaries.py::test_no_vision_module_queries_generationjob_directly`, the IA-1 structural gate that closes `GenerationJob.objects` access in `tools/vision` to exactly two files (`visibility.py`, `services.py`); `retention.py` is a third site, and the implementer stopped and reported the conflict rather than editing that gate or guessing a fix. The controller's ruling: the gate stands (it is not this task's file, and not the vision steward's to relax) and `visibility.py` is not the right reroute either — that module answers "who may see this", and a purge must reach a ticketed job, or one owned by somebody else, that nobody may currently see, which is not a visibility question at all. The unscoped read moved beside the delete it feeds: `tools/vision/services.py` gained `delete_jobs(job_ids) -> int` (loops `GenerationJob.objects.filter(pk__in=job_ids)`, calling `delete_job` per row, catching nothing), and `retention.py` now imports no model but `GeneratedOutput`/`JobInput` and ends `return services.delete_jobs(job_ids)`. Two further consequences, both applied and reflected in Task 11's file list and code blocks above: `purge_artifacts` returns 0 for two empty lists without running any query or calling `delete_jobs` at all (pinned by `TestEmptyInputCostsNothing`, a `django_assert_num_queries(0)` test — CONTROLLER ADDITION (a) from this task's own dispatch, which the first pass had not yet added); and `delete_jobs`'s own tests live in `test_retention.py`, not `test_services.py`, because adding them to the latter pushed it to 2,132 lines, over `test_column_boundaries.py`'s own 2,100-line test-module split threshold gate — a second structural gate the same shape of fix (moving the test, not editing the gate) resolved.
- **Execution amendment (Task 11 review round one), 2026-09-21:** three findings. (1) `output_file`/`input_file` bypass `visible_jobs` entirely (they load their row by primary key and call `may_read_job` directly), so Task 11's exclusion never reached them — a ticketed job's image stayed fetchable by its direct URL after deletion; `may_read_job` now refuses a ticketed job too, before its own `sees_all_content` branch, and the only other production callers of `may_read_job` (there is no sibling `may_manage_job`/`may_delete_job` in this module) are those same two routes, so no other predicate needed the same fix. (2) `purge_artifacts` logged the raw artifact reference and the raw generation id it failed to parse; both warnings now record only that one value failed to parse, pinned by a `caplog` test. (3) the module's own docstring promised a FAILED job with no output is reached only through its generation id; no test built one until now. All three, and the README sentence recording the file-view refusal, are reflected in Task 11's Files list and code/test blocks above.
- **Execution amendment (Task 10 review round one), 2026-09-21:** two findings. (1) `purge_conversation_notes` caught every `OSError` from the note file's own unlink and logged-and-continued to delete the `Document` row regardless — `missing_ok=True` already forgives the one case that should be forgiven, "already gone"; any OTHER `OSError` (a permissions or I/O problem) is a genuine failure, and deleting the row while the file itself stayed stuck on disk would report a purge that never happened, with the row being the only remaining handle on that file. The catch is removed: the file goes first, the row survives when it could not be removed, and `identity/cascades.py::run_retention`'s own never-swallows contract fails the whole purge and retries it on the next sweep. (2) `tools/rag/retrieval.py::_visibility_filters` never consulted the deletion exclusion at all, so a soft-deleted item's chunks stayed retrievable into a fresh answer through any surface that function still governed (today only from inside a ticketed conversation itself, which can take no new turns — but the same gap would reopen the moment a library document can be ticketed on its own). Closed uniformly rather than per-caller: `retrieve_nodes` computes `tools.rag.access._deleted_document_ids()` once per call — never once per filter leg, never once per node — and threads it into `_visibility_filters`, which expresses it as one `file_id NOT IN (...)` clause using the installed Postgres store's own `NIN` operator, added only when the list is non-empty (an empty `NOT IN (...)` is not valid SQL, and "nothing is deleted" costs nothing extra, not an inert clause). Both fixes are reflected in Task 10's Files list, code blocks and test description above, and in `tools/rag/README.md`'s deletion section.
- **Execution amendment (Task 12 review round one), 2026-09-21:** six findings, all reflected in Task 12's own code/test/help blocks above. (C1) `deleted_page`'s Deletion log leaked another person's item label to any signed-in viewer, because `audit.by_action`'s read is unscoped by design (the log lists every `content.*` event) and nothing further checked who was looking — `show_labels = sees_all_content(principal, settings_row=row)`, computed once, now gates `event.target_label` per event; the event itself still lists for every viewer, only the label is blanked for one with no standing to read everyone's content. (I2) The Deleted help card said "the retention setting" (the banned word) and omitted the backups sentence its own brief line requires — reworded to `"Keep deleted items for"` in quotes and the backups sentence added, modelled on the identical sentence on the Identity & security card. (I3) Both mutations' generic `except Exception` catches had no test of their own — `views.retention.restore_content`/`purge_ticket` patched to raise a bare `RuntimeError`, asserting the redirect, the fixed flash and the surviving ticket. (I4) `deleted_page`'s own "one read" docstring claim was false: `IdentityGateMiddleware` already reads `IdentitySettings` once per request, and the view's `IdentitySettings.get_solo()` was a second, needless read of the same table — replaced with `settings_row_for(request)` (the row the middleware already stashed), and `_own_ticket_or_404` now takes that row as a required keyword and threads it into `principal_for_request` rather than calling it bare, the same rule `entitlement_edit`'s own comment states; a new `CaptureQueriesContext` pin asserts exactly one `identity_identitysettings` statement per GET. (I5) `identity/README.md` section 9 gained a paragraph for the page itself — its three routes and their classes, the `EVERYONE` gate's reason, the prune-on-read GET, and the log's label-visibility rule — which the section had deferred to "the Deleted page's own tasks' code" until now. (M1/M2/M3/M4/M5) `_RESTORE_FAILED_MESSAGE` reworded ("nothing retries a restore" was itself untrue — the message no longer claims it); the log's own `helptext` reworded off "It never contains the deleted words" to name the real, setting-gated rule; the template's own comment corrected from "every sentence comes from the constants" (false — the page's own prose is typed in the template) to say which half is which; `may_purge`'s docstring gained one sentence naming it as today-inert and the hook for the deferred enterprise hold behaviour; the unused `make_admin` import was dropped from `identity/tests/test_deleted_page.py` (the two new label-visibility tests use `posture("open")` for their sees-all case rather than an administrator account, so the import stayed genuinely unused).
