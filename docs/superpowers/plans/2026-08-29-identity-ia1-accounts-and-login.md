# Identity IA-1 — Accounts, Login and Postures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Date:** 2026-08-29
**Branch base:** `main` at `0347777` ("feat(queue): on_terminal — job kinds hear about cancellation and permanent failure")
**Phase:** IA-1, the first of the two halves the spec's §18 splits Identity & Auth into
**Status:** plan, not executed

## Goal

Give a box that has never had a user a fifth top-level column, `identity/`, and with it: real
accounts, a login page, three postures held in a database row, superusers with a last-admin
guard, ownership stamped as a *user* rather than as "the box", a command that adopts an open
box's existing rows into that first user's name, deactivation, an append-only audit table, and
a route × principal matrix that asserts what every URL on the box answers to every kind of
caller. The default posture is `open` — today's behaviour, byte for byte, with no permission
query on any request — so this ships a complete second posture without taking the first one
away.

## Architecture

Sixteen tasks, bottom-up. Tasks 1–3 build the pure `identity/contracts/` leaf, the Django app
with its three tables, and the one migration that repoints `django_admin_log` at the new user
model. Tasks 4–6 add the access questions, the audit writer, the one request→principal point
(deleting `ACCOUNTS_REQUIRED` with it), and the guarded writes with their three refusals.
Tasks 7–9 build the gate — a route table, a middleware, and the three identity pages
(login/password, users, posture). Tasks 10–11 turn the acting rule on: a turn runs as the
**user**, not as the agent, and `agents/runtime/bindings.py::principal_for` is deleted. Tasks
12–14 stamp ownership on the two tables that lack it, fill in every visibility function, and
ship the two ownership commands. Task 15 is the route × principal matrix and the
zero-permission-query pin. Task 16 is the docs and the full gate ladder.

## Tech Stack

Django 5.1+ (server-rendered, zero JS), Postgres + pgvector, pytest + pytest-django. Django's
own `AbstractUser`, `ModelBackend`, `LoginView`/`LogoutView`/`PasswordChangeView`,
`login_required` semantics and system-check framework — this phase adds only what Django lacks.

## Spec

`docs/superpowers/specs/2026-08-29-identity-and-auth-design.md` — binding authority. This plan
implements **§18.1 (IA-1) only**. Read §3, §4, §5, §6.1/6.2/6.5/6.9, §7.1/7.2/7.5/7.6, §10,
§11, §12, §13, §14, §15, §16 and §17 alongside it. Everything entitlement-shaped
(`Entitlement`, `EntitlementGrant`, `DocumentEntitlement`, `ToolEntitlement`, `Share`, document
labels, the retrieval `visibility` argument, the chunk-metadata cache, groups and the sharing
UI) is **IA-2 and is out of scope** — but IA-1's schema and seams must not need rework for it.

## Global Constraints

Every task's requirements implicitly include this section.

1. **No AI model, product, or vendor names or versions appear anywhere** — code, comments,
   tests, docstrings, docs, or commit messages. The repository is going public and ADR 0010's
   third amendment already forbids the platform from naming a model for the operator. Describe
   models generically ("the assigned chat model", "the embedding role").
   **ENGINE KEYS ARE NOT MODEL NAMES.** `ModelConnection.engine` takes a registered engine
   adapter's `.name` — the identifiers this codebase already ships and that
   `config/settings.py::INFERENCE_DEFAULT_ENDPOINTS` is already keyed by — and a test fixture,
   a driver body or a builder default has to supply a real one or the row will not validate.
   Using those existing keys is allowed and is not what this rule is about: the rule forbids
   naming a MODEL the operator did not choose, not naming the local server software the
   operator installed. When a fixture needs an engine value, take it from the registry rather
   than inventing a string.
2. **Every task ships unit tests and updated docs in the same commit** (ADR 0008). A task with
   code and no test is not done; a task that changes behaviour a document describes and does
   not update that document is not done.
3. **Never-500.** Every response on every surface, for every principal, in every posture, is an
   honest status with no `"Traceback"` in the body. A row-addressed URL a principal may not see
   answers **404**, never 403 (spec §11.1); an admin surface answers **403**.
4. **Zero-JS, server-rendered pages** extending `foundation/templates/_shell.html`, matching
   every other page on the box. No new client-side framework, no new fetch dependency.
5. **Test databases.** Implementers run against `farabunker_impl` on the **preview** Postgres,
   port **5433**. Never `test_farabunker`; never port 5432.
   ```bash
   export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
   ```
6. **The four-way gate**, run in the final task and after any task a reviewer asks for it on:
   ```bash
   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
   .venv/bin/pytest -q                                                   # configured order
   .venv/bin/pytest -q scripts agents foundation identity models tools   # reversed
   ```
   Per-task runs may be narrower (`.venv/bin/pytest -q identity`), but the four-way gate is the
   merge gate.
7. **An open box never runs a permission query.** In `open` posture, no request executes a query
   against `identity_user` or any grant/label/share table. Every access function tests
   `accounts_on()` first and returns its open branch before touching another table. The one
   primary-key read of `IdentitySettings` is not a permission query (spec §3.4).
8. **The suite must pass in all three postures.** Default posture for the suite is `open` (the
   model default), so every existing test is unaffected. A test that depends on posture pins it
   with `identity/tests/_helpers.py::posture(...)` — the same rule flag-dependent tests already
   follow for `FARABUNKER_FEATURES`. The full-suite sweep is:
   ```bash
   FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
   ```
   `FARABUNKER_TEST_POSTURE` is read by **test helpers only**, never by production code — a
   production reader would reintroduce the second truth §3.2 rejects.
9. **`Django>=5.1,<6.0`** in `requirements.txt`, raised in Task 2. The `CheckConstraint(
   condition=...)` keyword IA-2 needs requires 5.1; the installed version already satisfies it,
   so this is a floor being raised to match what the code needs, not an upgrade.
10. **`AUTH_USER_MODEL = "identity.User"`** and `"identity"` in `INSTALLED_APPS` land in the
    **same commit** as `identity/migrations/0001_initial.py`, or Django refuses to start
    (spec §4.1). The `django_admin_log` repoint is a `RunPython` that reads the FK's
    hash-suffixed constraint name out of the Postgres catalogue and issues the DDL itself,
    behind an emptiness precondition (Task 3). The spec calls this a "catalogue-driven
    RunSQL"; it is catalogue-driven, but it cannot be `RunSQL`, because the constraint name is
    not knowable until the migration runs. Never a hard-coded name.
11. **Exactly four migrations in IA-1**, one per app: `identity/0001_initial.py`,
    `identity/0002_repoint_admin_log_fk.py`, `tools/vision/migrations/0006_generationjob_owner.py`,
    `tools/rag/migrations/0014_askrecord_owner.py`. `manage.py makemigrations --check --dry-run`
    exits 0 at the end of the plan.
12. **No `conftest.py` anywhere.** Helpers are duplicated per package in `_helpers.py`; autouse
    fixtures are defined per test module and delegate to `_helpers`. Any test overriding
    `FARABUNKER_FEATURES` with a literal `frozenset` while doing HTTP or `reverse()` keeps
    `"vision"` in the set, or `/vision/`'s routes vanish and `reverse()` raises.
13. **`docs/DEV.md`'s restart rule bites.** `identity/middleware.py`, `identity/access.py`, and
    every runtime change in Tasks 10–11 are job-kind-adjacent code the worker holds in memory:
    `docker compose restart watcher worker` before every smoke test.
14. **Commit at the end of every task**, with a message naming the task, on the worktree branch
    — never on `main`.
15. **Implementers never leave `...` or TODO.** Every step in this plan is written to be
    executable as it stands. If a step turns out to be unimplementable as written — a symbol
    that does not exist, a field the model does not have, an assertion that cannot hold —
    **stop and report BLOCKED with the reason**. Do not substitute a placeholder, a skipped
    test, a weakened assertion, or a guess: an unattended run that quietly degrades a step
    produces a green suite that proves less than it claims, which is worse than a red one.

## File Structure

New column, created across Tasks 1–9:

```
identity/
  contracts/            pure, Django-free -- a rule-1 leaf (Task 1)
    principals.py       Principal, PRINCIPAL_KINDS, OPEN_PRINCIPAL, SERVICE_PRINCIPAL,
                        ANONYMOUS, payload_fields, principal_from_payload
    postures.py         POSTURE_*, POSTURES, LIBRARY_*, SESSION_IDLE_MINUTES_DEFAULT
    actions.py          AUDIT_ACTIONS -- the closed action vocabulary
    ownership.py        OwnedRows, register_owned_rows, all_owned_rows
  apps.py               AppConfig(label="identity") + the three system checks (Tasks 2, 6)
  models.py             User, IdentitySettings, AuditEvent (Task 2)
  access.py             posture, accounts_on, is_admin, sees_all_content, owner_fields (Task 4)
  audit.py              record(...), recent(...), for_target(...) -- the ONE writer (Task 4)
  request.py            principal_for_request -- moved from agents/chat (Task 5)
  services.py           create_user, set_password, set_superuser, deactivate_user,
                        reactivate_user, set_posture (Task 6)
  checks.py             identity.E001 / E002 / W001 (Task 6)
  routes.py             ROUTE_RULES -- every URL name, classified (Task 7)
  gate.py               require_principal / require_admin decorators (Task 7)
  middleware.py         IdentityGateMiddleware (Task 7)
  context_processors.py identity_posture / identity_is_admin for the shell (Task 9)
  admin.py              the break-glass user admin, routed through services (Task 9)
  forms.py  views.py  urls.py  templates/identity/                  (Tasks 8, 9)
  management/commands/  identity_posture.py (T6), adopt_open_rows.py, reassign_owner.py (T14)
  migrations/           0001_initial.py (T2), 0002_repoint_admin_log_fk.py (T3)
  tests/                _helpers.py + one module per task
  README.md             (Task 16)
```

Touched elsewhere: `config/settings.py`, `config/urls.py`, `pytest.ini`, `requirements.txt`,
`agents/contracts/tools.py`, `agents/chat/` (six call sites, one deletion),
`agents/runtime/{bindings,jobs,loop,delegate,preflight}.py`, `agents/visibility.py`,
`agents/apps.py`, `agents/chat/service.py`, `agents/management/commands/agent_turn.py`,
`tools/rag/{apps,views,jobs,services,ingest,models}.py`,
`tools/rag/management/commands/{ingest.py,ingest_watch.py}`,
`tools/vision/{apps,views,services,models}.py`, `tools/vision/visibility.py` (new),
`models/queue/{views.py, visibility.py (new)}`, `models/registry/views.py`,
`foundation/templates/_shell.html`, `foundation/ops/tests/{test_import_law,test_column_boundaries}.py`.

## Decisions the author made

Recorded here, at the top, because an implementer who hits one of these and reaches for the
spec will find the spec saying something slightly different — and needs to know this plan
already thought about it.

1. **`ToolInvocation.agent_slug` lands in IA-2, not here.** Spec §18.1's prose lists it under
   IA-1; spec §17's migration table puts it in IA-2's migration 7, and §18.1's own done-when 6
   says `migrate --plan` shows **exactly IA-1's four migrations**. The table and the testable
   gate win over the prose. IA-1 ships `ToolContext.agent_slug` (a frozen-dataclass field, no
   migration); the column that records it arrives with its writer in IA-2. Task 10 says so in
   the code comment so the omission reads as deliberate.
2. **The turn payload is built in `agents/chat/service.py::start_turn`, not in
   `agents/chat/views/turns.py::turn_create`.** Spec §5.3 item 3 names the view; the view calls
   `start_turn(conversation, text, connection=...)` (`agents/chat/service.py:98`) and the
   payload dict is built there, inside the `transaction.atomic()` block at line 149. `start_turn`
   therefore grows an `actor` keyword; the view supplies `principal_for_request(request)` and
   `manage.py agent_turn` supplies `SERVICE_PRINCIPAL`.
3. **The queue page filters `QueueRow` objects, not a queryset.** Spec §7.6 writes
   `visible_jobs` as `InferenceJob.objects.filter(payload__actor_kind=...)`. That is right for
   `jobs-queue-cancel`, which has a job id — but `QueueView` renders
   `models/queue/backend.py::queue_snapshot()`, which returns frozen `QueueRow` dataclasses
   already fetched in one query, and re-querying per row would undo that. Task 13 therefore
   ships **one predicate** (`_is_actor`) with three callers: `visible_jobs(principal)` (the
   queryset, for cancel), `visible_rows(principal, rows)` (the list filter, for the page), and
   `may_read_job_content(principal, payload)`.
4. **`models/queue/visibility.py` becomes a named rule-2 seam.** `tools/rag/views.py` and
   `tools/vision/views.py` must ask "may this caller see queue job N" for `rag-ask-status` and
   `vision-queue-status`, and `models/contracts/queue.py::get_job` returns a `JobStatus`
   (`models/queue/backend.py:77-107`) that deliberately carries no `payload`. Rather than widen
   `JobStatus` — which would put another principal's prompt text on the seam — this module joins
   `models.registry.bindings` as a sanctioned cross-column import under rule 2, recorded in
   `models/README.md` and `foundation/README.md` (Task 16) and pinned as a closed set in Task 13.
5. **`identity/access.py` ships five functions in IA-1, not eight.** `held_entitlement_ids`,
   `owned_entitlement_ids` and `may_see_unlabelled` all query tables IA-2 creates. Writing them
   now as stubs would be three functions whose only test is "returns empty", and a stub in an
   access module is exactly the kind of thing a later reader mistakes for a decision. IA-1 ships
   `posture`, `accounts_on`, `is_admin`, `sees_all_content`, `owner_fields`.
6. **`identity.User` gets a `pk`-shape guard before it queries.** `Principal.key` for a user is
   the primary key **as a string** (spec §5.1). A principal whose key is not a decimal string
   would reach `User.objects.filter(pk=key)` and raise `ValueError` — a 500 on a never-500
   surface, reachable from a hand-written payload or an older row. `is_admin` checks
   `key.isdigit()` first and answers `False` otherwise.
7. **The audit catalogue is thirty-one names, and the plan ships thirty-one.** Spec §13.2's
   table lists 3 + 7 + 5 + 10 + 6 = 31 across its five families, and every one has a write site
   in IA-1 or IA-2. Task 1's test asserts that number so the tuple cannot be trimmed by
   accident.
8. **`library_posture` is stored, rendered and audited in IA-1, and consulted by nothing.**
   Spec §18.1 says so explicitly. The reset-to-`open`-on-move-to-`personal` behaviour in
   `set_posture` (spec §14) still ships in Task 6, because it is a write-path rule and writing
   it later means writing a data migration for boxes that already moved.
9. **A shell path owns the rows it creates, as the service principal.** Spec §5.3 item 8 says
   every shell path *acts as* `SERVICE_PRINCIPAL`; today
   `agents/management/commands/agent_turn.py:185` creates its conversation as `OPEN_PRINCIPAL`
   while — after Task 10 — its payload carries the service principal, which would make one
   command write two different answers to "who did this". §5.3 wins: shell paths stamp
   `SERVICE_PRINCIPAL` on the **row** as well as in the payload. Two consequences, both stated
   rather than discovered: `manage.py adopt_open_rows` claims `("open", "box")` and `("", "")`
   rows and **not** `("service", "local")` ones — a row the shell made is not a row the open
   box made, and sweeping it into a person's name would attribute an automated action to
   somebody; and a service-owned row is therefore visible to `is_admin` and to nobody else,
   which is the same fail-closed answer `models/queue/visibility.py` already gives a payload
   with no actor. `manage.py install_defaults` keeps `OPEN_PRINCIPAL`: a shipped default is the
   platform's own offer, marked `resident=True`, and every visibility function returns residents
   to everybody regardless of owner — so its owner column is not load-bearing and changing it
   would only move rows out of adoption's reach for no gain.
10. **`_tool_roles(agent, actor)` drops the spec's third argument.** Spec §5.3 item 4 writes
    `_tool_roles(agent, actor, access)`. `access` is a `ToolAccess`, which IA-2 introduces along
    with the entitlement tables it reads; in IA-1 every access is unrestricted, so the parameter
    would have exactly one possible value and no test could distinguish passing it from not.
    IA-1 ships the two-argument form and IA-2 adds the third — a one-line signature change at
    one call site, against a stub parameter nobody could review.

---
### Task 1: `identity/contracts/` — the pure leaf, and `Principal`'s new home

`Principal` is no longer an agents-column detail: after this phase it is the base type of the
whole platform, so it moves to a rule-1 pure leaf every column may import (spec §4.2, §5.1).
Nothing authenticates anybody in this task. What it does is create the vocabulary — the kinds,
the two constants, the "nobody" sentinel, the payload round-trip, the posture names, the audit
action catalogue, and the owned-rows registry — with **no Django anywhere in it**, pinned by a
subprocess probe with no settings module configured.

`identity/` is a plain Python package in this task, not yet a Django app; `pytest.ini`'s
`testpaths` gains `identity` **in this same commit**, because a missing `testpaths` entry is
silently ignored by pytest and stops collecting a whole tree while the suite still exits 0.

**Files:**
- Create: `identity/__init__.py`, `identity/contracts/__init__.py`,
  `identity/contracts/principals.py`, `identity/contracts/postures.py`,
  `identity/contracts/actions.py`, `identity/contracts/ownership.py`
- Create: `identity/tests/__init__.py`, `identity/tests/test_principals.py`,
  `identity/tests/test_postures.py`, `identity/tests/test_actions.py`,
  `identity/tests/test_ownership.py`, `identity/tests/test_purity.py`
  *(`identity/tests/_helpers.py` is created in Task 4, when the first test that needs a
  database row is written. Task 1's four test modules are all Django-free and import nothing
  from it.)*
- Modify: `agents/contracts/tools.py` — delete `PRINCIPAL_KINDS`, `Principal`,
  `OPEN_PRINCIPAL`; import `Principal` from `identity.contracts.principals`
- Modify: `agents/chat/principal.py:25`, `agents/chat/tests/test_principal.py:13`,
  `agents/chat/tests/test_visibility.py:24`, `agents/chat/tests/test_index.py:13`,
  `agents/management/commands/install_defaults.py:28`,
  `agents/management/commands/agent_turn.py:34`, `agents/runtime/loop.py:44`,
  `agents/runtime/bindings.py`, `agents/runtime/tests/test_jobs.py:183` — repoint the import
- Modify: `pytest.ini` (`testpaths`)
- Modify: `foundation/ops/tests/test_import_law.py` — `_PRINCIPAL_CONSTRUCTORS`, and a new
  sweep over `identity/`
- Modify: `agents/contracts/README.md`, `docs/DEV.md` §7

**Interfaces:**
- Consumes: nothing — this is the bottom of the stack.
- Produces:
  ```python
  # identity/contracts/principals.py
  PRINCIPAL_KINDS: tuple[str, ...]        # ("open", "user", "service", "resident_agent", "user_agent")
  @dataclass(frozen=True)
  class Principal:
      kind: str
      key: str
  OPEN_PRINCIPAL: Principal               # Principal("open", "box")
  SERVICE_PRINCIPAL: Principal            # Principal("service", "local")
  ANONYMOUS: _Anonymous                   # .kind == "anonymous", .key == ""
  def payload_fields(principal) -> dict:  # {"actor_kind": ..., "actor_key": ...}
  def principal_from_payload(payload: dict) -> Principal

  # identity/contracts/postures.py
  POSTURE_OPEN = "open"; POSTURE_PERSONAL = "personal"; POSTURE_ENTERPRISE = "enterprise"
  POSTURES: tuple[str, ...]; POSTURE_CHOICES: tuple[tuple[str, str], ...]
  LIBRARY_OPEN = "open"; LIBRARY_LOCKED = "locked"
  LIBRARY_CHOICES: tuple[tuple[str, str], ...]
  SESSION_IDLE_MINUTES_DEFAULT = 720
  def accounts_required(posture: str) -> bool     # pure: posture != POSTURE_OPEN

  # identity/contracts/actions.py
  AUDIT_ACTIONS: tuple[str, ...]          # the 31 names, all five families

  # identity/contracts/ownership.py
  @dataclass(frozen=True)
  class OwnedRows:
      key: str; label: str; model: str
  def register_owned_rows(spec: OwnedRows) -> None
  def all_owned_rows() -> list[OwnedRows]
  ```

**Steps:**

- [ ] **Step 1: Write the failing tests.** `identity/tests/test_principals.py`:
  ```python
  """The principal vocabulary -- kinds, constants, the anonymous sentinel,
  and the payload round-trip.

  NO Django, NO database: every assertion here is about plain values, so
  this module carries no `pytest.mark.django_db` and no settings override.
  """
  from __future__ import annotations

  import pytest

  from identity.contracts.principals import (
      ANONYMOUS, OPEN_PRINCIPAL, PRINCIPAL_KINDS, Principal, SERVICE_PRINCIPAL,
      payload_fields, principal_from_payload,
  )


  class TestKinds:
      def test_the_vocabulary_is_closed_and_carries_the_two_historical_kinds(self):
          """`resident_agent`/`user_agent` stay in the tuple after the
          acting rule stops minting them: `ToolInvocation` rows written
          before IA-1 carry them, and a closed vocabulary that cannot
          describe its own stored data is a vocabulary that lies."""
          assert PRINCIPAL_KINDS == (
              "open", "user", "service", "resident_agent", "user_agent",
          )

      def test_an_unknown_kind_is_a_construction_error(self):
          with pytest.raises(ValueError) as exc:
              Principal("nobody", "x")
          assert "nobody" in str(exc.value)

      def test_a_blank_key_is_a_construction_error(self):
          with pytest.raises(ValueError):
              Principal("user", "")

      def test_a_principal_is_frozen_and_hashable(self):
          """Hashable so a later grant cache can key on it; un-repointable
          so nothing downstream can change who a half-finished turn is
          acting as."""
          principal = Principal("user", "7")
          assert {principal: 1}[Principal("user", "7")] == 1
          with pytest.raises(Exception):
              principal.key = "8"


  class TestTheTwoConstants:
      def test_the_open_principal_is_the_box(self):
          assert OPEN_PRINCIPAL == Principal("open", "box")

      def test_there_is_exactly_one_service_principal(self):
          """One kind for every machine caller (the watcher, `ingest`,
          `ask`, `agent_turn`), not one per caller: WHICH of them acted is
          recorded in the audit row's `detail`, not in a key that would
          become a grants-table join value."""
          assert SERVICE_PRINCIPAL == Principal("service", "local")


  class TestAnonymous:
      def test_it_is_not_a_principal_and_anonymous_is_not_a_kind(self):
          """A SEPARATE TYPE, deliberately. Making it a fifth kind would
          mean the first person to write `Q(owner_kind="anonymous")` gave
          "nobody" a set of rows to own."""
          assert not isinstance(ANONYMOUS, Principal)
          assert "anonymous" not in PRINCIPAL_KINDS
          with pytest.raises(ValueError):
              Principal("anonymous", "x")

      def test_it_reads_like_a_principal_for_the_two_attributes_that_matter(self):
          """So every access function reads it with the same two attribute
          lookups and needs no isinstance branch."""
          assert ANONYMOUS.kind == "anonymous"
          assert ANONYMOUS.key == ""


  class TestPayloadRoundTrip:
      def test_the_fields_are_the_two_keys_every_job_payload_carries(self):
          assert payload_fields(Principal("user", "42")) == {
              "actor_kind": "user", "actor_key": "42",
          }

      def test_it_round_trips(self):
          for principal in (OPEN_PRINCIPAL, SERVICE_PRINCIPAL, Principal("user", "9")):
              assert principal_from_payload(payload_fields(principal)) == principal

      def test_a_payload_with_no_actor_reads_back_as_the_open_principal(self):
          """Every job enqueued before this phase. The open principal is
          the honest answer for a row written by a box that had no users --
          and `models/queue/visibility.py` still refuses it to a member,
          because a member is not the open principal."""
          assert principal_from_payload({}) == OPEN_PRINCIPAL

      def test_a_payload_with_a_junk_kind_reads_back_as_the_open_principal(self):
          """Never a raise: this is called from a job handler on the
          worker, where a raise is a failed job, and from the queue page,
          where a raise is a 500."""
          assert principal_from_payload(
              {"actor_kind": "wizard", "actor_key": "x"}) == OPEN_PRINCIPAL

      def test_a_non_dict_payload_reads_back_as_the_open_principal(self):
          assert principal_from_payload(None) == OPEN_PRINCIPAL
          assert principal_from_payload([1, 2]) == OPEN_PRINCIPAL
  ```

- [ ] **Step 2: Run it to make sure it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_principals.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'identity'`

- [ ] **Step 3: Write `identity/contracts/principals.py`.**
  ```python
  """WHO is acting -- the platform's base identity type.

  MOVED here verbatim from `agents/contracts/tools.py`, where it was an
  agents-column detail. After Identity & Auth it is the type every column
  reads, so it lives in a rule-1 pure leaf that any column may import in
  any direction (spec section 4.2). NO DJANGO, NO DATABASE, NO I/O --
  pinned by `identity/tests/test_purity.py`, a subprocess with no settings
  module configured at all.

  This module and `identity/request.py` are the ONLY two files in the
  codebase that may CONSTRUCT a `Principal`, enforced by an AST guard in
  `foundation/ops/tests/test_import_law.py`. A principal's kind is the
  field a grants table joins on, so a third place minting one is a third
  opinion about who is acting.
  """
  from __future__ import annotations

  from dataclasses import dataclass

  # The kinds of caller a grant can attach to.
  #   "open"    -- an UNAUTHENTICATED box: one `Principal("open", "box")`.
  #   "user"    -- key = the user's PRIMARY KEY as a string. Never the
  #                username: a username is renameable, and a rename must
  #                not orphan every row a person owns. Display resolves
  #                pk -> username at render time.
  #   "service" -- a named machine caller. ONE constant, not one per
  #                caller: the watcher, `manage.py ingest`, `manage.py ask`
  #                and `manage.py agent_turn` all act as it, and WHICH of
  #                them acted is recorded in `AuditEvent.detail
  #                ["source_command"]` rather than in a key that would
  #                quietly turn "the shell" into four grantable subjects.
  #                `api_client` is folded into this kind: nothing has ever
  #                constructed one, so there is no stored data to preserve,
  #                and two names meaning "a machine holding a credential"
  #                would make a future grants join test two values where
  #                one fact exists.
  #
  # HISTORICAL, below. Nothing constructs these after IA-1 -- the acting
  # rule (spec section 5.3) makes a turn run as the USER -- but
  # `agents.models.ToolInvocation` rows written before it carry them, and
  # a closed vocabulary that cannot describe its own stored data is a
  # vocabulary that lies.
  #   "resident_agent" -- a code-declared agent.
  #   "user_agent"     -- an Agent row somebody wrote.
  PRINCIPAL_KINDS = ("open", "user", "service", "resident_agent", "user_agent")


  @dataclass(frozen=True)
  class Principal:
      """WHO is calling. Two strings, deliberately: the same shape serves
      an in-process agent turn, a signed-in browser request, and an
      external tool call, so nothing downstream needs a second field or a
      second code path.

      Frozen: hashable (so a later grant cache can key on it) and
      un-repointable (so nothing downstream can quietly change who a
      half-finished turn is acting as).
      """

      kind: str
      key: str

      def __post_init__(self) -> None:
          if self.kind not in PRINCIPAL_KINDS:
              raise ValueError(
                  f"Unknown principal kind {self.kind!r}; must be one of "
                  f"{list(PRINCIPAL_KINDS)}"
              )
          if not self.key:
              raise ValueError(f"Principal({self.kind!r}) needs a non-blank key")


  # The principal an UNAUTHENTICATED box acts as. ONE instance, shared, so
  # there is never a second spelling of who this box is.
  OPEN_PRINCIPAL = Principal("open", "box")

  # The principal EVERY shell path acts as (spec section 5.3, item 8).
  SERVICE_PRINCIPAL = Principal("service", "local")


  @dataclass(frozen=True)
  class _Anonymous:
      """Nobody. A SEPARATE TYPE from `Principal`, not a fifth kind.

      It carries `.kind`/`.key` so the access functions read it with the
      same two attribute lookups they use for a real principal and need no
      isinstance branch -- but `"anonymous"` is deliberately NOT in
      PRINCIPAL_KINDS, so `Principal("anonymous", ...)` raises, and the
      type is structurally incapable of reaching a grants join or an owner
      column: it is never passed to `owner_fields` (the gate refuses the
      request first), and every join in this platform is on a user id,
      never on a kind string. Making it a kind would mean the first person
      to write `Q(owner_kind="anonymous")` gave "nobody" rows to own.
      """

      kind: str = "anonymous"
      key: str = ""


  ANONYMOUS = _Anonymous()

  # The two keys every job payload carries so the queue page, the runtime
  # and the audit trail all agree on who asked for the work.
  ACTOR_KIND_KEY = "actor_kind"
  ACTOR_KEY_KEY = "actor_key"


  def payload_fields(principal) -> dict:
      """The two keys to merge into a job payload for `principal`.

      A dict rather than two arguments, for the same reason
      `identity.access.owner_fields` is one: a call site cannot pass one
      and forget the other.
      """
      return {ACTOR_KIND_KEY: principal.kind, ACTOR_KEY_KEY: principal.key}


  def principal_from_payload(payload) -> Principal:
      """The principal a job payload was enqueued by.

      NEVER RAISES, and that is the whole design of this function. It is
      called from a job handler on the worker -- where a raise is a failed
      job -- and from the queue page -- where a raise is a 500 on a
      never-500 surface. A payload with no actor keys is every job
      enqueued before this phase, and the honest answer for a row written
      by a box that had no users is the open principal. A payload with a
      kind outside the vocabulary, or one that is not a dict at all, is a
      hand-edited or corrupt row and gets the same answer: `open` is the
      LEAST privileged reading here, because `models.queue.visibility`
      matches a member on their own kind AND key, and no member is
      `("open", "box")`.
      """
      if not isinstance(payload, dict):
          return OPEN_PRINCIPAL
      kind = payload.get(ACTOR_KIND_KEY)
      key = payload.get(ACTOR_KEY_KEY)
      if not isinstance(kind, str) or not isinstance(key, str):
          return OPEN_PRINCIPAL
      try:
          return Principal(kind, key)
      except ValueError:
          return OPEN_PRINCIPAL
  ```

- [ ] **Step 4: Run the test to verify it passes.**
  Run: `.venv/bin/pytest -q identity/tests/test_principals.py`
  Expected: PASS

- [ ] **Step 5: Write `identity/tests/test_postures.py`, then `postures.py`.** The test:
  ```python
  """The posture vocabulary -- pure names and one pure predicate.

  The DECISION about which posture this box is in lives in a database row
  (`identity.models.IdentitySettings`, Task 2) and is read by
  `identity.access.posture()`. This module holds only the names, so a
  column that needs to say "personal" in a template or a form does not
  have to import a Django model to do it.
  """
  from identity.contracts.postures import (
      LIBRARY_CHOICES, LIBRARY_LOCKED, LIBRARY_OPEN, POSTURE_CHOICES, POSTURE_ENTERPRISE,
      POSTURE_OPEN, POSTURE_PERSONAL, POSTURES, SESSION_IDLE_MINUTES_DEFAULT,
      accounts_required,
  )


  class TestPostures:
      def test_there_are_exactly_three_in_order_of_strictness(self):
          assert POSTURES == (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE)
          assert (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE) == (
              "open", "personal", "enterprise")

      def test_the_choices_cover_every_posture(self):
          assert [value for value, _label in POSTURE_CHOICES] == list(POSTURES)

      def test_open_is_the_only_posture_without_accounts(self):
          assert accounts_required(POSTURE_OPEN) is False
          assert accounts_required(POSTURE_PERSONAL) is True
          assert accounts_required(POSTURE_ENTERPRISE) is True


  class TestLibraryPosture:
      def test_two_values_and_open_is_the_default_name(self):
          assert (LIBRARY_OPEN, LIBRARY_LOCKED) == ("open", "locked")
          assert [value for value, _label in LIBRARY_CHOICES] == [LIBRARY_OPEN, LIBRARY_LOCKED]


  class TestSessionIdle:
      def test_the_default_is_twelve_hours_in_minutes(self):
          """Long enough that a person working through a document library
          is not logged out mid-task; short enough that a browser left
          open on a shared desk is not a standing session."""
          assert SESSION_IDLE_MINUTES_DEFAULT == 720
  ```
  Then the module:
  ```python
  """The posture names, as pure data.

  Three postures, and they are a DATABASE ROW's value, never an
  environment variable (spec section 3.2): `compose.yaml` starts three
  processes from one image with independently supplied environments, so a
  security posture carried in the environment could be set on the web
  process and unset on the worker -- and the worker is where turns
  actually run tools. The database is the one thing all three processes
  provably share.

  This module holds only the NAMES and one pure predicate, so a form, a
  template tag or a management command can name a posture without
  importing a Django model.
  """
  from __future__ import annotations

  POSTURE_OPEN = "open"
  POSTURE_PERSONAL = "personal"
  POSTURE_ENTERPRISE = "enterprise"

  # In order of strictness, which is also the order the posture page
  # renders them in.
  POSTURES = (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE)

  POSTURE_CHOICES = (
      (POSTURE_OPEN, "Open — no accounts, no login"),
      (POSTURE_PERSONAL, "Personal — a few local accounts, every one an administrator"),
      (POSTURE_ENTERPRISE, "Organisation — accounts, groups and entitlements"),
  )

  # Whether documents carrying NO label are readable by everyone signed in
  # (`open`, the default) or by administrators only (`locked`). Stored and
  # rendered in IA-1; consulted by nothing until IA-2 gives labels a body.
  LIBRARY_OPEN = "open"
  LIBRARY_LOCKED = "locked"
  LIBRARY_CHOICES = (
      (LIBRARY_OPEN, "Open — an unlabelled document is readable by everyone signed in"),
      (LIBRARY_LOCKED, "Locked — an unlabelled document is readable by administrators only"),
  )

  # Twelve hours, as a ROLLING idle window (spec section 14). Zero means
  # "expire when the browser closes", which is why one column covers both
  # behaviours and no `SESSION_EXPIRE_AT_BROWSER_CLOSE` setting is added.
  SESSION_IDLE_MINUTES_DEFAULT = 720


  def accounts_required(posture: str) -> bool:
      """Whether `posture` is one in which a request must be authenticated.

      Pure, and separate from `identity.access.accounts_on()`, which reads
      the row. This one lets a caller that ALREADY has the posture string
      -- a form validating a submitted value, a management command
      printing what a switch would do -- answer the question without a
      second database read.
      """
      return posture != POSTURE_OPEN
  ```

- [ ] **Step 6: Write `identity/contracts/actions.py`** — the closed catalogue, all
  thirty-one names, including the sixteen only IA-2 writes.
  ```python
  """The closed vocabulary of things that get audited.

  DECLARED IN FULL NOW, including the sixteen actions only IA-2 ever
  writes (spec section 18.1: "actions IA-2 writes are declared now, so the
  vocabulary is not amended twice"). A tuple, not a str: a typo'd action
  name is a construction error in `AuditEvent.save()` rather than a
  category that silently splits an audit report in two.

  Page views, document reads, search queries and tool calls are
  DELIBERATELY absent (spec section 13.3). The first three would produce a
  log nobody reads, in which the interesting lines are invisible; tool
  calls already have a better record in `agents.models.ToolInvocation`.
  The two tables exist separately on purpose -- one answers "who changed
  what about this box", the other "what did the machinery do" -- and
  merging them would make retention policy impossible, because the first
  must be kept and the second must be prunable.
  """
  from __future__ import annotations

  # -- session ------------------------------------------------------------
  LOGIN = "identity.login"
  LOGIN_FAILED = "identity.login_failed"
  LOGOUT = "identity.logout"

  # -- account ------------------------------------------------------------
  USER_CREATED = "identity.user_created"
  USER_DEACTIVATED = "identity.user_deactivated"
  USER_REACTIVATED = "identity.user_reactivated"
  PASSWORD_CHANGED = "identity.password_changed"
  PASSWORD_RESET = "identity.password_reset"
  SUPERUSER_GRANTED = "identity.superuser_granted"
  SUPERUSER_REVOKED = "identity.superuser_revoked"

  # -- posture and ownership ---------------------------------------------
  POSTURE_CHANGED = "identity.posture_changed"
  LIBRARY_POSTURE_CHANGED = "identity.library_posture_changed"
  ADMIN_CONTENT_ACCESS_CHANGED = "identity.admin_content_access_changed"
  ADOPTED = "identity.adopted"
  OWNER_REASSIGNED = "identity.owner_reassigned"

  # -- entitlements and groups (IA-2 writes these) ------------------------
  ENTITLEMENT_CREATED = "entitlement.created"
  ENTITLEMENT_RENAMED = "entitlement.renamed"
  ENTITLEMENT_DELETED = "entitlement.deleted"
  GRANT_ADDED = "grant.added"
  GRANT_ROLE_CHANGED = "grant.role_changed"
  GRANT_REVOKED = "grant.revoked"
  GROUP_CREATED = "group.created"
  GROUP_DELETED = "group.deleted"
  GROUP_MEMBER_ADDED = "group.member_added"
  GROUP_MEMBER_REMOVED = "group.member_removed"

  # -- labels and shares (IA-2 writes these) ------------------------------
  DOCUMENT_LABELLED = "library.document_labelled"
  DOCUMENT_UNLABELLED = "library.document_unlabelled"
  TOOL_LABELLED = "tool.labelled"
  TOOL_UNLABELLED = "tool.unlabelled"
  SHARE_ADDED = "share.added"
  SHARE_REVOKED = "share.revoked"

  AUDIT_ACTIONS = (
      LOGIN, LOGIN_FAILED, LOGOUT,
      USER_CREATED, USER_DEACTIVATED, USER_REACTIVATED, PASSWORD_CHANGED,
      PASSWORD_RESET, SUPERUSER_GRANTED, SUPERUSER_REVOKED,
      POSTURE_CHANGED, LIBRARY_POSTURE_CHANGED, ADMIN_CONTENT_ACCESS_CHANGED,
      ADOPTED, OWNER_REASSIGNED,
      ENTITLEMENT_CREATED, ENTITLEMENT_RENAMED, ENTITLEMENT_DELETED,
      GRANT_ADDED, GRANT_ROLE_CHANGED, GRANT_REVOKED,
      GROUP_CREATED, GROUP_DELETED, GROUP_MEMBER_ADDED, GROUP_MEMBER_REMOVED,
      DOCUMENT_LABELLED, DOCUMENT_UNLABELLED, TOOL_LABELLED, TOOL_UNLABELLED,
      SHARE_ADDED, SHARE_REVOKED,
  )

  # Where an audited action was performed. `web` is a page, `cli` a
  # management command, `admin` Django's break-glass surface.
  SOURCE_WEB = "web"
  SOURCE_CLI = "cli"
  SOURCE_ADMIN = "admin"
  SOURCE_CHOICES = (
      (SOURCE_WEB, "Web page"),
      (SOURCE_CLI, "Command line"),
      (SOURCE_ADMIN, "Django admin"),
  )
  ```
  Its test, in `identity/tests/test_actions.py`:
  ```python
  from identity.contracts.actions import AUDIT_ACTIONS, SOURCE_CHOICES


  class TestTheCatalogue:
      def test_every_name_is_unique(self):
          """A duplicate would make one write silently indistinguishable
          from another in every report built on `action`."""
          assert len(set(AUDIT_ACTIONS)) == len(AUDIT_ACTIONS)

      def test_every_name_carries_its_family_prefix(self):
          """Every name is `<prefix>.<verb>`, and the prefix is what an
          audit page groups on -- so a name without one has no group to
          fall in. SEVEN prefixes across the design's five FAMILIES: the
          entitlement/group family spans three prefixes and the
          label/share family two, because a family is a heading on a page
          and a prefix is a string on a row."""
          prefixes = {"identity.", "entitlement.", "grant.", "group.", "library.",
                      "tool.", "share."}
          for action in AUDIT_ACTIONS:
              assert any(action.startswith(p) for p in prefixes), action

      def test_the_full_vocabulary_is_declared_now_not_in_two_passes(self):
          """IA-2's sixteen entitlement/group/label/share actions are here
          already, so the closed tuple is amended once rather than twice."""
          assert "entitlement.created" in AUDIT_ACTIONS
          assert "share.revoked" in AUDIT_ACTIONS
          assert len(AUDIT_ACTIONS) == 31

      def test_no_name_is_longer_than_the_column(self):
          """`AuditEvent.action` is CharField(max_length=64)."""
          assert max(len(a) for a in AUDIT_ACTIONS) <= 64

      def test_the_three_sources(self):
          assert [value for value, _ in SOURCE_CHOICES] == ["web", "cli", "admin"]
  ```

- [ ] **Step 7: Write `identity/tests/test_ownership.py`, then `ownership.py`.** The test:
  ```python
  """The owned-rows registry -- five tables in three columns, named as
  strings so `identity/` never imports `agents` or `tools`.

  This module uses a MODULE-LEVEL autouse fixture that snapshots and
  restores the registry, matching how every other registry in this
  codebase is isolated in tests. No `conftest.py`.
  """
  import pytest

  from identity.contracts import ownership
  from identity.contracts.ownership import OwnedRows, all_owned_rows, register_owned_rows


  @pytest.fixture(autouse=True)
  def _isolated_registry():
      saved = dict(ownership._OWNED)
      ownership._OWNED.clear()
      yield
      ownership._OWNED.clear()
      ownership._OWNED.update(saved)


  class TestRegistry:
      def test_a_spec_is_plain_data_naming_its_model_as_a_string(self):
          """`app_label.ModelName`, resolved at command time by
          `django.apps.apps.get_model` -- never imported, exactly as a
          JobKind's handler is a dotted-path string. This is what lets
          `identity/` walk five tables in three columns it may not
          import."""
          spec = OwnedRows(key="agents.conversation", label="Conversations",
                           model="agents.Conversation")
          register_owned_rows(spec)
          assert all_owned_rows() == [spec]

      def test_registration_is_idempotent_and_last_wins(self):
          """Same shape as `register_role`/`register_job_kind`/
          `register_tool`: re-importing a module that registers at import
          time is safe."""
          register_owned_rows(OwnedRows("k", "First", "agents.Agent"))
          register_owned_rows(OwnedRows("k", "Second", "agents.Agent"))
          assert [s.label for s in all_owned_rows()] == ["Second"]

      def test_it_returns_registration_order(self):
          """The order the adoption command prints its per-table counts in.
          Stable, so an operator comparing two runs compares two identical
          lists."""
          register_owned_rows(OwnedRows("a", "A", "agents.Agent"))
          register_owned_rows(OwnedRows("b", "B", "agents.Flow"))
          assert [s.key for s in all_owned_rows()] == ["a", "b"]

      def test_a_blank_key_or_model_is_a_construction_error(self):
          with pytest.raises(ValueError):
              OwnedRows("", "A", "agents.Agent")
          with pytest.raises(ValueError):
              OwnedRows("a", "A", "")

      def test_a_model_string_without_a_dot_is_a_construction_error(self):
          """`apps.get_model` needs `app_label.ModelName`; catching the
          shape HERE means a bad registration fails at app-ready time,
          naming itself, rather than inside a transaction that is halfway
          through rewriting owners."""
          with pytest.raises(ValueError) as exc:
              OwnedRows("a", "A", "Agent")
          assert "app_label.ModelName" in str(exc.value)
  ```
  Then the module:
  ```python
  """Which tables carry owner columns -- a registry, not a list.

  Five tables in three columns carry `owner_kind`/`owner_key`, and two
  operations (adoption, section 10.4 of the spec; reassignment, section
  10.5) must walk all of them. `identity/` may not import `agents` or
  `tools` (import-law rule 4), so the command cannot name the models. They
  register themselves from each column's `AppConfig.ready()`, exactly as
  job kinds, roles and tools already do -- and for the same reason: a
  sixth owned table later is a REGISTRATION, not an edit to a command that
  would otherwise silently skip it.

  Pure: `model` is an `app_label.ModelName` STRING, resolved at command
  time by `django.apps.apps.get_model`, never imported.
  """
  from __future__ import annotations

  from dataclasses import dataclass


  @dataclass(frozen=True)
  class OwnedRows:
      """One table carrying owner columns.

      `key`   -- stable identifier, e.g. "agents.conversation". It is what
                 `manage.py reassign_owner --kind` takes, so it is
                 operator-facing and must not change casually.
      `label` -- what the adoption command prints, e.g. "Conversations".
      `model` -- "app_label.ModelName", for `apps.get_model`.
      """

      key: str
      label: str
      model: str

      def __post_init__(self) -> None:
          if not self.key:
              raise ValueError("OwnedRows needs a non-blank key")
          if not self.label:
              raise ValueError(f"OwnedRows({self.key!r}) needs a non-blank label")
          if not self.model:
              raise ValueError(f"OwnedRows({self.key!r}) needs a non-blank model")
          if "." not in self.model:
              raise ValueError(
                  f"OwnedRows({self.key!r}).model must be 'app_label.ModelName', "
                  f"got {self.model!r}"
              )


  _OWNED: dict[str, OwnedRows] = {}


  def register_owned_rows(spec: OwnedRows) -> None:
      """Register `spec` under its `.key`, replacing any existing entry.

      Idempotent, like every sibling registry in this codebase, so
      re-importing a module that registers at import time is safe.
      """
      _OWNED[spec.key] = spec


  def all_owned_rows() -> list[OwnedRows]:
      """Every registered table, in registration order."""
      return list(_OWNED.values())
  ```

- [ ] **Step 8: Move `Principal` out of `agents/contracts/tools.py`.** Delete
  `PRINCIPAL_KINDS`, the `Principal` dataclass and `OPEN_PRINCIPAL` (lines 66–126) and replace
  them with:
  ```python
  # MOVED (Identity & Auth, IA-1). `Principal` is no longer an agents-
  # column detail -- it is the base identity type of the whole platform --
  # so it lives in `identity/contracts/principals.py`, a rule-1 pure leaf
  # every column may import. Imported here, never re-exported: every call
  # site names its real home, so there is one import path to grep for and
  # the AST guard in `foundation/ops/tests/test_import_law.py` has one
  # place to look.
  from identity.contracts.principals import Principal  # noqa: F401 -- ToolContext's type
  ```
  `granted_tools(principal: Principal, tool_keys)` keeps its signature and its body unchanged.
  Repoint every import listed in **Files** above from `agents.contracts.tools` to
  `identity.contracts.principals`.

- [ ] **Step 9: Extend the purity probe.** `identity/tests/test_purity.py` is
  `agents/contracts/tests/test_purity.py` with a different `_PROBE`; write it out in full
  rather than importing across packages (the per-package helper-duplication rule):
  ```python
  """`identity/contracts/` imports no Django. Pinned, not hoped for.

  Same shape and same reasoning as `agents/contracts/tests/test_purity.py`:
  a subprocess with no DJANGO_SETTINGS_MODULE set at all, so an accidental
  `from django.conf import settings` fails here with an
  ImproperlyConfigured (or an unexpected `django` in `sys.modules`) rather
  than silently making a rule-1 pure leaf depend on a configured Django.

  The live tripwire is not hypothetical: `identity/contracts/ownership.py`
  names its models as `app_label.ModelName` strings PRECISELY so it never
  has to `from django.apps import apps` at module scope. Doing so would
  turn this test red immediately.
  """
  from __future__ import annotations

  import os
  import subprocess
  import sys
  import textwrap
  from pathlib import Path

  from django.conf import settings

  REPO_ROOT = Path(settings.BASE_DIR)

  _PROBE = textwrap.dedent("""
      import sys
      import identity.contracts.principals
      import identity.contracts.postures
      import identity.contracts.actions
      import identity.contracts.ownership
      leaked = sorted(m for m in sys.modules if m == "django" or m.startswith("django."))
      print("|".join(leaked))
  """)


  def _run_probe(script: str) -> subprocess.CompletedProcess:
      env = dict(os.environ)
      env.pop("DJANGO_SETTINGS_MODULE", None)
      return subprocess.run(
          [sys.executable, "-c", script],
          cwd=REPO_ROOT, capture_output=True, text=True, env=env,
      )


  def test_the_whole_package_imports_with_no_django_configured():
      result = _run_probe(_PROBE)
      assert result.returncode == 0, result.stderr


  def test_importing_it_pulls_in_no_django_module_at_all():
      result = _run_probe(_PROBE)
      leaked = [m for m in result.stdout.strip().split("|") if m]
      assert leaked == [], leaked


  def test_the_probe_would_actually_notice_a_django_import():
      """Anti-vacuous pin: a probe that cannot fail proves nothing."""
      result = _run_probe(_PROBE.replace(
          "import identity.contracts.principals",
          "import identity.contracts.principals\nimport django.utils.timezone",
      ))
      leaked = [m for m in result.stdout.strip().split("|") if m]
      assert "django" in leaked or "django.utils.timezone" in leaked
  ```
  `agents/contracts/tests/test_purity.py` still passes unchanged: `agents/contracts/tools.py`
  now imports `identity.contracts.principals`, which is itself Django-free, so the probe's
  `import agents.contracts.tools` pulls in no Django either. **Add one assertion to that
  existing module** proving the new edge is really covered:
  ```python
  def test_the_moved_principal_type_keeps_this_package_pure():
      """`agents/contracts/tools.py` now imports `identity.contracts.
      principals`. That import is inside this probe's reach, so a future
      Django import added THERE fails HERE as well as in identity's own
      probe -- which is the property that makes the move safe."""
      result = _run_probe(_PROBE.replace(
          "import agents.contracts.tools",
          "import agents.contracts.tools\nimport identity.contracts.principals",
      ))
      assert result.returncode == 0, result.stderr
      assert [m for m in result.stdout.strip().split("|") if m] == []
  ```

- [ ] **Step 10: Shrink `_PRINCIPAL_CONSTRUCTORS` and add the identity sweeps.** In
  `foundation/ops/tests/test_import_law.py`:
  ```python
  # IA-1 moved the type out of `agents/` entirely, so the sweep below
  # walks every column rather than `agents/` alone -- a sweep still scoped
  # to `agents/` would pass by looking at a directory that no longer
  # contains the thing it polices.
  #
  # THE SET SHRINKS IN TWO STEPS, and each entry lands with the file it
  # names, because `test_the_principal_constructors_list_is_not_silently_
  # empty` asserts every listed path exists on disk:
  #   Task 1  -- `identity/contracts/principals.py` replaces
  #              `agents/contracts/tools.py`; `agents/chat/principal.py`
  #              and `agents/runtime/bindings.py` stay.
  #   Task 5  -- `identity/request.py` replaces `agents/chat/principal.py`.
  #   Task 11 -- `agents/runtime/bindings.py` goes, because the acting
  #              rule deletes `principal_for` outright. Two files remain.
  _PRINCIPAL_CONSTRUCTORS = frozenset({
      "identity/contracts/principals.py",  # the type and the three constants
      "agents/chat/principal.py",          # who is on this REQUEST -- moves in Task 5
      "agents/runtime/bindings.py",        # who is this AGENT -- deleted in Task 11
  })

  _PRINCIPAL_SCANNED_COLUMNS = ("agents", "identity", "tools", "models", "foundation")


  def _principal_scanned_files() -> list[str]:
      """Every tracked, non-test `.py` file in every column the principal
      sweep covers. Extracted from `_principal_construction_sites()` so
      the anti-vacuous pin below can assert on the FILE LIST rather than
      on the hit dict, which is empty when the gate is green."""
      out = subprocess.run(
          ["git", "ls-files", "--"] + list(_PRINCIPAL_SCANNED_COLUMNS),
          cwd=REPO_ROOT, capture_output=True, text=True, check=True,
      )
      return [line for line in out.stdout.splitlines()
              if line.endswith(".py") and not _is_test_file(line)]
  ```
  Rewrite `_principal_construction_sites()` to walk `_principal_scanned_files()` instead of
  `git ls-files -- agents`, **rename** `test_a_principal_is_only_constructed_in_the_three_files_
  that_may` to `..._in_the_files_that_may` (the count is now a moving target across three
  tasks, and a name carrying it would be wrong twice), and add:
  ```python
  def test_the_principal_sweep_reaches_every_column():
      """Anti-vacuous pin. The type moved OUT of `agents/`, so a sweep
      still scoped to `agents/` would pass by looking at a directory that
      no longer contains the thing it polices."""
      files = _principal_scanned_files()
      assert len(files) > 100, len(files)
      for column in _PRINCIPAL_SCANNED_COLUMNS:
          assert any(f.startswith(column + "/") for f in files), column
      assert "identity/contracts/principals.py" in files
  ```
  And the new rule-4 sweep (identity imports nothing from another column):
  ```python
  # --- IA-1: import-law rule 4, `identity/` imports no other column ------

  _OTHER_COLUMNS = ("agents", "tools", "models")


  def _identity_files() -> list[str]:
      out = subprocess.run(["git", "ls-files", "--", "identity"], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True)
      return [line for line in out.stdout.splitlines()
              if line.endswith(".py") and not _is_test_file(line)]


  def _cross_column_imports(source: str) -> list[str]:
      """Every import of another column in `source`, at ANY scope -- a lazy
      in-body `from agents.models import Agent` would still be a
      cross-column dependency, just a later one, so this walks every
      import node rather than only `tree.body` (mirroring
      `test_no_agents_module_imports_a_tools_package`)."""
      tree = ast.parse(source)
      hits: list[str] = []
      for node in ast.walk(tree):
          names = (
              [a.name for a in node.names] if isinstance(node, ast.Import)
              else [node.module] if isinstance(node, ast.ImportFrom) and node.module
              else []
          )
          for name in names:
              for column in _OTHER_COLUMNS:
                  if name == column or name.startswith(column + "."):
                      hits.append(name)
      return hits


  def test_no_identity_module_imports_another_column():
      """Import-law rule 4 (spec section 4.2). `identity/` may import
      `foundation`, Django, and its own `contracts` -- and nothing else.

      This is what makes the column a BASE rather than a hub, and it has
      two load-bearing consequences: identity cannot answer "which
      documents" (it answers "which entitlement ids", and `tools/rag`
      turns that into a queryset), and the owned-rows registry names its
      models as strings resolved by `apps.get_model` rather than importing
      them.
      """
      offenders: dict[str, list[str]] = {}
      for relative in _identity_files():
          hits = _cross_column_imports((REPO_ROOT / relative).read_text(encoding="utf-8"))
          if hits:
              offenders[relative] = hits
      assert offenders == {}, offenders


  def test_the_identity_sweep_is_reading_real_files():
      """Anti-vacuous pin: a broken `git ls-files` call would make the test
      above pass by looking at nothing."""
      files = _identity_files()
      assert len(files) >= 4, files
      assert "identity/contracts/principals.py" in files


  def test_the_identity_rule_four_gate_would_catch_a_lazy_import():
      """Anti-vacuous pin: an in-BODY import must be caught too, or the
      guard enforces a style rule instead of a dependency rule."""
      lazy = "def f():\n    from agents.models import Agent\n    return Agent\n"
      assert _cross_column_imports(lazy) == ["agents.models"]
      assert _cross_column_imports("import tools.rag.views\n") == ["tools.rag.views"]
      assert _cross_column_imports("from foundation.format import bytes_to_gb\n") == []
  ```

- [ ] **Step 11: Add `identity` to `pytest.ini`'s `testpaths`, in this commit.**
  ```ini
  testpaths = tools models foundation agents identity scripts
  ```
  **Verify the edit took**, because a stale or missing `testpaths` entry is silently ignored
  by pytest and stops collecting a whole tree while the suite still exits 0 — the collected
  count is the only usable gate on this edit:
  Run: `.venv/bin/pytest -q --collect-only identity | tail -3`
  Expected: a non-zero count of collected tests under `identity/`.

- [ ] **Step 12: Update the docs this task changed.** `agents/contracts/README.md` — the
  `Principal`/`PRINCIPAL_KINDS` bullet now points at `identity.contracts.principals` and says
  the type moved and why. `docs/DEV.md` §7 — the reversed-order command gains `identity`.

- [ ] **Step 13: Run the suite and commit.**
  Run: `.venv/bin/pytest -q identity agents foundation`
  Expected: PASS. Then stage `identity/`, `agents/contracts/tools.py`, the eight repointed
  import sites, `pytest.ini`, `foundation/ops/tests/test_import_law.py`,
  `agents/contracts/README.md`, `docs/DEV.md` and commit as
  `feat(identity): T1 — the identity contracts leaf, and Principal's new home`.

---

### Task 2: the `identity` app — `User`, `IdentitySettings`, `AuditEvent`, and the `AUTH_USER_MODEL` swap

The three tables IA-1 owns, and the setting that cannot be changed once rows reference it.
`AUTH_USER_MODEL`, `INSTALLED_APPS` and `identity/migrations/0001_initial.py` land **in one
commit** or Django refuses to start.

`AuditEvent` deliberately carries **no foreign key to `User`** — two strings and a label,
exactly like `ToolInvocation`'s principal columns. An audit row a cascade could delete is not
an audit row, and it also means this migration needs no `swappable_dependency` and can create
the audit table alongside the user model in one file.

**Files:**
- Create: `identity/apps.py`, `identity/models.py`, `identity/migrations/__init__.py`,
  `identity/migrations/0001_initial.py`
- Create: `identity/tests/test_models.py`
- Modify: `config/settings.py` — `INSTALLED_APPS`, `AUTH_USER_MODEL`
- Modify: `requirements.txt` — `Django>=5.1,<6.0`
- Modify: `foundation/ops/tests/test_app_labels.py` — the new app's label
- Modify: `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`

**Interfaces:**
- Consumes: `identity.contracts.postures` (Task 1), `identity.contracts.actions` (Task 1).
- Produces:
  ```python
  # identity/models.py
  class User(AbstractUser): ...                    # table identity_user, NO extra fields
  class IdentitySettings(models.Model):
      posture: str; library_posture: str; admin_sees_content: bool
      session_idle_minutes: int; updated_at: datetime
      @classmethod
      def get_solo(cls) -> "IdentitySettings": ...
  class AuditEvent(models.Model):
      actor_kind: str; actor_key: str; actor_label: str; action: str
      target_type: str; target_key: str; target_label: str
      source: str; at: datetime; detail: dict
  ```

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_models.py`:
  ```python
  """The three tables IA-1 owns.

  MODULE-LEVEL `pytest.mark.django_db`: every assertion here touches the
  database. No `FARABUNKER_FEATURES` override and no `reverse()`, so the
  vision-flag rule does not apply.
  """
  from __future__ import annotations

  import pytest
  from django.contrib.auth import get_user_model

  from identity.contracts.postures import (
      LIBRARY_OPEN, POSTURE_OPEN, SESSION_IDLE_MINUTES_DEFAULT,
  )
  from identity.models import AuditEvent, IdentitySettings

  pytestmark = pytest.mark.django_db


  class TestUser:
      def test_the_project_user_model_is_ours(self):
          """`AUTH_USER_MODEL` cannot be changed once rows reference it,
          which is why this model exists NOW with no extra fields at all:
          the cost of adding a field later to a model we own is one
          migration, while the cost of swapping the model later is
          database surgery on a box holding a document library."""
          assert get_user_model()._meta.label == "identity.User"
          assert get_user_model()._meta.db_table == "identity_user"

      def test_it_adds_no_fields_of_its_own(self):
          """Deliberate. A field here would be a field IA-2 has to reason
          about; the empty subclass is the whole point.

          `"id"` is in the difference because `AbstractUser` is ABSTRACT:
          Django adds the implicit `AutoField` primary key when a
          CONCRETE model is built, so the parent's `get_fields()` has no
          pk to compare against. `"logentry"` is the reverse accessor
          `django.contrib.admin.LogEntry` creates by pointing at
          `AUTH_USER_MODEL`. Neither is a field this model declares, and
          asserting `<=` rather than `==` keeps the test honest if Django
          adds a third such artefact."""
          from django.contrib.auth.models import AbstractUser
          inherited = {f.name for f in AbstractUser._meta.get_fields()}
          ours = {f.name for f in get_user_model()._meta.get_fields()}
          assert ours - inherited <= {"id", "logentry"}

      def test_a_created_user_is_active_and_not_a_superuser_by_default(self):
          user = get_user_model().objects.create_user(username="ann", password="x")
          assert user.is_active is True
          assert user.is_superuser is False


  class TestIdentitySettings:
      def test_get_solo_creates_the_row_on_first_read(self):
          """Same shape `tools.rag.models.RagSettings.get_solo` uses --
          `get_or_create(pk=1)`, never raising `DoesNotExist`. No seed
          migration, so a backup restored from before this phase behaves
          identically to a fresh install."""
          assert not IdentitySettings.objects.exists()
          row = IdentitySettings.get_solo()
          assert row.pk == 1
          assert IdentitySettings.objects.count() == 1
          assert IdentitySettings.get_solo().pk == 1
          assert IdentitySettings.objects.count() == 1

      def test_the_defaults_are_the_decision(self):
          """`open` posture is today's behaviour; `admin_sees_content`
          defaults FALSE because administering is not reading."""
          row = IdentitySettings.get_solo()
          assert row.posture == POSTURE_OPEN
          assert row.library_posture == LIBRARY_OPEN
          assert row.admin_sees_content is False
          assert row.session_idle_minutes == SESSION_IDLE_MINUTES_DEFAULT


  class TestAuditEvent:
      def test_it_carries_no_foreign_key_to_a_user(self):
          """An audit row a cascade could delete is not an audit row. Two
          strings and a denormalised label, exactly like
          `ToolInvocation`'s principal columns -- which is also why
          `0001_initial` needs no `swappable_dependency`."""
          relations = [f.name for f in AuditEvent._meta.get_fields() if f.is_relation]
          assert relations == []

      def test_a_row_that_already_has_a_primary_key_may_not_be_saved_again(self):
          """Append-only, ENFORCED rather than described. `save()` is not
          the whole guard -- a queryset `.update()`/`.delete()` never calls
          it -- which is why the AST guard in `test_column_boundaries.py`
          forbids `AuditEvent.objects` outside `identity/audit.py`
          entirely."""
          row = AuditEvent.objects.create(actor_kind="open", actor_key="box",
                                          action="identity.login")
          row.action = "identity.logout"
          with pytest.raises(ValueError) as exc:
              row.save()
          assert "append-only" in str(exc.value).lower()

      def test_an_action_outside_the_catalogue_is_refused(self):
          """A typo'd action name is a construction error here, not a
          category that silently splits an audit report in two."""
          with pytest.raises(ValueError) as exc:
              AuditEvent.objects.create(actor_kind="open", actor_key="box",
                                        action="identity.did_a_thing")
          assert "identity.did_a_thing" in str(exc.value)

      def test_it_reads_newest_first(self):
          for action in ("identity.login", "identity.logout"):
              AuditEvent.objects.create(actor_kind="open", actor_key="box", action=action)
          assert [r.action for r in AuditEvent.objects.all()] == [
              "identity.logout", "identity.login"]
  ```

- [ ] **Step 2: Run it to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_models.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'identity.models'`

- [ ] **Step 3: Write `identity/apps.py`.**
  ```python
  """The `identity/` column's Django app.

  `label = "identity"` is set EXPLICITLY, as all eight existing app
  configs do -- the property the 2026-08-25 regroup depends on, and the
  one that makes `apps.get_model("identity", "User")` resolve from a data
  migration, a management command and a test alike.

  `ready()` registers this column's system checks (Task 6). It touches NO
  database -- Django forbids that here -- and imports no view, no service
  and no model at module scope, matching every other AppConfig in this
  tree.
  """
  from __future__ import annotations

  from django.apps import AppConfig


  class IdentityConfig(AppConfig):
      default_auto_field = "django.db.models.BigAutoField"
      name = "identity"
      label = "identity"
  ```

- [ ] **Step 4: Write `identity/models.py`.**
  ```python
  """The three tables IA-1 owns: who exists, what posture this box is in,
  and what has been done to it.

  Entitlements, grants, labels and shares are IA-2 and are deliberately
  absent -- IA-1 must be a complete, shippable posture on its own (a
  household box with three accounts and private conversations), not half
  of a feature.
  """
  from __future__ import annotations

  from django.contrib.auth.models import AbstractUser
  from django.db import models

  from identity.contracts.actions import AUDIT_ACTIONS, SOURCE_CHOICES, SOURCE_WEB
  from identity.contracts.postures import (
      LIBRARY_CHOICES, LIBRARY_OPEN, POSTURE_CHOICES, POSTURE_OPEN,
      SESSION_IDLE_MINUTES_DEFAULT,
  )


  class User(AbstractUser):
      """The platform's user model.

      NO EXTRA FIELDS, deliberately. It exists NOW because
      `AUTH_USER_MODEL` cannot be changed once rows reference it, and the
      cost of adding a field later to a model we own is one migration --
      while the cost of swapping the model later is database surgery
      nobody wants to perform on a box holding a document library.

      Django's own `identity_user_groups` and `identity_user_user_
      permissions` join tables come with `AbstractUser`'s inherited M2M
      fields. The permissions one is NEVER written to: Django's
      `Permission` model is a named non-goal (owner decision 16), because
      it is model-level and would be a second grant mechanism beside the
      entitlements IA-2 adds.
      """


  class IdentitySettings(models.Model):
      """The posture singleton -- the ONE answer to "what posture is this
      box in".

      A DATABASE ROW, not a setting (spec section 3.2). `compose.yaml`
      starts `web`, `worker` and `watcher` as three processes with
      independently supplied environments, so an environment variable
      governing whether permissions are enforced could be set on one and
      unset on another -- and the worker is where turns actually run
      tools. The database is the one thing all three provably share. It
      also makes the posture VALIDATABLE against database facts: switching
      away from `open` is refused unless an active superuser exists
      (`identity.services.set_posture`), and an environment variable
      cannot be refused.

      Read once per request through `get_solo()` -- one primary-key read
      on a connection Django already holds open (`CONN_MAX_AGE=600`). NO
      CACHE LAYER: a cache would be a second truth with a staleness
      window, and the staleness window of a security posture is exactly
      the interval in which the box is wrong.
      """

      posture = models.CharField(max_length=16, choices=POSTURE_CHOICES,
                                 default=POSTURE_OPEN)
      # Whether documents carrying NO label are readable by everyone
      # signed in. STORED AND EDITABLE IN IA-1, CONSULTED BY NOTHING until
      # IA-2 gives labels a body -- the column lands now so the posture
      # page is written once, and `set_posture`'s reset-to-open rule (see
      # `identity.services`) needs no data migration later.
      library_posture = models.CharField(max_length=16, choices=LIBRARY_CHOICES,
                                         default=LIBRARY_OPEN)
      # Whether an ADMINISTRATOR may read other people's CONTENT -- their
      # conversations, Ask history, generated images, agent and flow
      # bodies, document bytes, and the payload/answer text of jobs they
      # did not start.
      #
      # DEFAULT FALSE, AND THAT DEFAULT IS THE DECISION. Administering is
      # not reading: an admin already sees every ROW they need to run the
      # box -- a job's kind, owner, state and progress; a document's
      # title, category and status -- without this. Turning it on is a
      # deliberate act on the posture page, audited exactly like the
      # posture, and read per request so it takes effect without a
      # restart.
      admin_sees_content = models.BooleanField(default=False)
      # A ROLLING idle timeout, applied per request by
      # `IdentityGateMiddleware` via `request.session.set_expiry(...)`.
      # Zero means "expire when the browser closes", which is why one
      # column covers both behaviours.
      session_idle_minutes = models.PositiveIntegerField(
          default=SESSION_IDLE_MINUTES_DEFAULT)
      updated_at = models.DateTimeField(auto_now=True)

      @classmethod
      def get_solo(cls) -> "IdentitySettings":
          """The one row (`pk=1`), creating it with defaults on first use.

          Never raises `DoesNotExist` -- the same pattern
          `tools.rag.models.RagSettings.get_solo` established, and the
          reason there is no seed migration: a backup restored from before
          this phase behaves identically to a fresh install.
          """
          obj, _ = cls.objects.get_or_create(pk=1)
          return obj

      def __str__(self) -> str:  # pragma: no cover - trivial
          return f"IdentitySettings(posture={self.posture})"


  class AuditEvent(models.Model):
      """One row per thing somebody did TO this box. Append-only.

      NO FOREIGN KEY TO `User`, deliberately: two strings and a label,
      exactly like `ToolInvocation`'s principal columns. An audit row a
      cascade could delete is not an audit row -- and it also means
      `identity/0001_initial.py` needs no `swappable_dependency` and can
      create this table alongside the user model in one migration.

      APPEND-ONLY IS A CODE-LEVEL PROPERTY, NOT A DATABASE ONE, and this
      docstring says so rather than letting `save()` imply otherwise. A
      queryset `.update()` or `.delete()` never calls `save()`, so the
      real guard is the AST sweep in `foundation/ops/tests/
      test_column_boundaries.py`: no module outside `identity/audit.py`
      may touch `AuditEvent.objects` at all. Making the table append-only
      in Postgres would need a rule or a trigger -- a second enforcement
      mechanism outside the application -- and is out of scope.
      """

      actor_kind = models.CharField(max_length=32)
      actor_key = models.CharField(max_length=255)
      # The actor's DISPLAY NAME at the time of the event. Denormalised on
      # purpose: an audit line must still read correctly after the account
      # is renamed or deactivated, and a join that resolves a deleted pk
      # to "unknown" is an audit trail that forgets.
      actor_label = models.CharField(max_length=255, blank=True, default="")
      action = models.CharField(max_length=64, db_index=True)
      target_type = models.CharField(max_length=64, blank=True, default="")
      target_key = models.CharField(max_length=255, blank=True, default="")
      target_label = models.CharField(max_length=255, blank=True, default="")
      source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_WEB)
      at = models.DateTimeField(auto_now_add=True, db_index=True)
      detail = models.JSONField(default=dict, blank=True)

      class Meta:
          ordering = ["-at"]
          indexes = [
              models.Index(fields=["actor_kind", "actor_key"], name="identity_audit_actor"),
              models.Index(fields=["target_type", "target_key"], name="identity_audit_target"),
          ]

      def save(self, *args, **kwargs):
          """Append-only, and a closed action vocabulary.

          `ValueError`, not a `ValidationError`: this is a programming
          error at the call site, not a form the operator can correct.
          """
          if self.pk is not None:
              raise ValueError(
                  "AuditEvent rows are append-only; this row already exists "
                  f"(pk={self.pk}, action={self.action!r})."
              )
          if self.action not in AUDIT_ACTIONS:
              raise ValueError(
                  f"Unknown audit action {self.action!r}. Add it to "
                  f"identity/contracts/actions.py::AUDIT_ACTIONS first."
              )
          return super().save(*args, **kwargs)

      def __str__(self) -> str:  # pragma: no cover - trivial
          return f"AuditEvent({self.at:%Y-%m-%d %H:%M} · {self.action})"
  ```

- [ ] **Step 5: Wire the app and the user model into `config/settings.py`.** Add `"identity"`
  to `INSTALLED_APPS` (position is irrelevant for app loading; put it after `"foundation.ops"`
  so the list stays grouped by column), and immediately below the `SECRET_KEY`/`DEBUG` block
  replace `ACCOUNTS_REQUIRED` — **no, leave `ACCOUNTS_REQUIRED` alone in this task**; it is
  deleted in Task 5, when its one reader moves. Add:
  ```python
  # The platform's user model. SET IN THE SAME COMMIT AS
  # `identity/migrations/0001_initial.py`, or Django refuses to start.
  # It cannot be changed once rows reference it (spec section 6.1), which
  # is why `identity.User` exists now, empty, rather than later, full.
  AUTH_USER_MODEL = "identity.User"
  ```

- [ ] **Step 6: Raise the Django floor** in `requirements.txt`:
  ```
  Django>=5.1,<6.0
  ```
  with the reason on the line above:
  ```
  # 5.1 is the floor, not an upgrade: `CheckConstraint(condition=...)` --
  # the keyword the entitlement and share constraints need -- exists from
  # 5.1, and 5.0's `check=` spelling is deprecated in 5.1 and removed in
  # 6.0. Writing to the old spelling would mean shipping a deprecation
  # the next upgrade has to unpick. The installed version already
  # satisfies this.
  ```

- [ ] **Step 7: Generate the migration and read it before trusting it.**
  Run: `.venv/bin/python manage.py makemigrations identity --name initial`
  Then open `identity/migrations/0001_initial.py` and confirm, by eye:
  - it creates `User`, `IdentitySettings` and `AuditEvent` and nothing else;
  - it has **no** `swappable_dependency` (`AuditEvent` has no user FK, and `User` is the
    swappable model itself);
  - its `dependencies` name `("auth", "__first__")` and `("contenttypes", "__first__")` only —
    the dependencies `AbstractUser`'s inherited M2M fields require;
  - the two `AuditEvent` indexes carry the names written in `Meta`.
  Add a module docstring to the generated file recording what it is and that IA-1 ships exactly
  four migrations.

- [ ] **Step 8: Run the tests.**
  Run: `.venv/bin/pytest -q identity/tests/test_models.py`
  Expected: PASS

- [ ] **Step 9: Pin the app label.** `foundation/ops/tests/test_app_labels.py` asserts every
  app config sets an explicit label; add `identity` to whatever list it walks (read the module
  first — it is short) and run it:
  Run: `.venv/bin/pytest -q foundation/ops/tests/test_app_labels.py`
  Expected: PASS

- [ ] **Step 10: Docs.** `docs/ARCHITECTURE.md` — **five** columns, not four; an Identity row
  in the core-services table; one sentence noting that the three *security* postures are not
  the deployment postures the same document already names (a naming collision worth one
  sentence). `docs/OPERATIONS.md` — a new subsection stating that **a database dump now
  contains password hashes, session keys and the audit log, and must be treated as a credential
  store**: encrypted at rest, never mailed, never committed.

- [ ] **Step 11: Full-column run and commit.**
  Run: `.venv/bin/pytest -q identity foundation`
  Expected: PASS. Commit as
  `feat(identity): T2 — the identity app, its three tables, and the user-model swap`.

---

### Task 3: `identity/migrations/0002_repoint_admin_log_fk.py`

Every already-deployed box applied `auth` and `admin` migrations against `auth.User`, so
`django_admin_log.user_id` carries a foreign key to `auth_user`. Django cannot fix that itself:
the autodetector may not write migrations into a third-party app. This is the migration that
does, and it is written defensively because it runs on a box holding a document library.

`auth_user`, `auth_user_groups` and `auth_user_user_permissions` are **left in place** as empty
orphan tables. Dropping them would put `django.contrib.auth`'s migration state and the database
out of agreement over a table Django still believes it manages.

**Files:**
- Create: `identity/migrations/0002_repoint_admin_log_fk.py`
- Create: `identity/tests/test_migration_admin_log.py`
- Modify: `docs/OPERATIONS.md`

**Interfaces:**
- Consumes: `identity/migrations/0001_initial.py` (Task 2).
- Produces: nothing importable. Its contract is a database state.

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_migration_admin_log.py`:
  ```python
  """`0002_repoint_admin_log_fk` -- the precondition and the repoint.

  The precondition is exercised BOTH WAYS (a row present raises with the
  operator-readable message; no rows repoints), because a migration that
  assumes it is safe without checking is a migration that eventually is
  not.

  These tests call the migration's own module-level functions directly
  against the live test database rather than driving `migrate`: the test
  database is already fully migrated by the time a test runs, so
  re-running the migration through the executor would be a no-op. The
  functions are the unit; the four-way `migrate --plan` gate in Task 16 is
  what proves they are wired into the graph.
  """
  from __future__ import annotations

  import pytest
  from django.db import connection

  from identity.migrations import _0002_helpers as helpers  # see Step 3

  pytestmark = pytest.mark.django_db


  class TestThePrecondition:
      def test_it_passes_on_an_empty_box(self):
          """Neither table can hold a row, because no login has ever
          existed on this platform. Checked anyway."""
          helpers.refuse_if_rows_exist(connection)

      def test_it_raises_with_an_operator_readable_instruction(self, monkeypatch):
          monkeypatch.setattr(helpers, "_row_count", lambda conn, table: 3)
          with pytest.raises(RuntimeError) as exc:
              helpers.refuse_if_rows_exist(connection)
          message = str(exc.value)
          assert "auth_user" in message or "django_admin_log" in message
          assert "3" in message
          # It must tell the operator what to DO, not merely that it
          # stopped -- a migration that refuses without an instruction is
          # a migration an operator forces past.
          assert "backup" in message.lower()


  class TestTheConstraintLookup:
      def test_it_finds_the_constraint_by_catalogue_not_by_name(self):
          """The FK constraint name is hash-suffixed by Django and is not
          a literal anybody may hard-code. This looks it up in
          `information_schema`, which is the only correct way to name it."""
          found = helpers.admin_log_user_fk_name(connection)
          assert found is None or found.startswith("django_admin_log_")

      def test_a_fresh_install_is_a_no_op(self, monkeypatch):
          """On a fresh database `admin.0001_initial`'s own
          `swappable_dependency` already built the FK against
          `identity_user`, so there is nothing to repoint and the
          migration must do nothing rather than fail."""
          monkeypatch.setattr(helpers, "_table_exists",
                              lambda conn, table: table != "auth_user")
          assert helpers.repoint_is_needed(connection) is False
  ```

- [ ] **Step 2: Run it to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_migration_admin_log.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'identity.migrations._0002_helpers'`

- [ ] **Step 3: Write `identity/migrations/_0002_helpers.py`.** The logic lives in an
  importable sibling module, not inline in the migration, so the tests above can reach it
  without driving the executor — and so the migration file stays short enough to read in one
  screen.
  ```python
  """The bodies `0002_repoint_admin_log_fk` runs.

  A SIBLING MODULE, not inline in the migration, for one reason: a
  migration's `RunPython` callables are not importable by a test without
  driving the migration executor, and this migration's precondition is
  exactly the kind of code that must be exercised BOTH ways before it runs
  against a box holding a document library.

  Underscore-prefixed so Django's migration loader never mistakes it for a
  migration.
  """
  from __future__ import annotations

  _ADMIN_LOG = "django_admin_log"
  _OLD_USER = "auth_user"
  _NEW_USER = "identity_user"


  def _table_exists(connection, table: str) -> bool:
      with connection.cursor() as cursor:
          cursor.execute("SELECT to_regclass(%s) IS NOT NULL", [f"public.{table}"])
          return bool(cursor.fetchone()[0])


  def _row_count(connection, table: str) -> int:
      if not _table_exists(connection, table):
          return 0
      with connection.cursor() as cursor:
          cursor.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608 -- fixed literals
          return int(cursor.fetchone()[0])


  def refuse_if_rows_exist(connection) -> None:
      """Refuse to repoint if either table holds a row.

      NEITHER CAN, on any box this platform has ever shipped: no login has
      ever existed, so `auth_user` is empty and nothing has ever written a
      `django_admin_log` entry. The check exists because a migration that
      assumes it is safe without checking is a migration that eventually
      is not -- and because the failure mode if it IS wrong is silent
      referential nonsense in an audit-adjacent table.
      """
      for table in (_OLD_USER, _ADMIN_LOG):
          count = _row_count(connection, table)
          if count:
              raise RuntimeError(
                  f"Cannot move this box to the new user model: {table!r} holds "
                  f"{count} row(s), and this migration only knows how to repoint "
                  f"an EMPTY admin log. Take a backup, then decide deliberately "
                  f"what those rows should become -- do not force this migration."
              )


  def repoint_is_needed(connection) -> bool:
      """Whether there is an old-user-model FK to move at all.

      False on a fresh install, where `admin.0001_initial`'s own
      `swappable_dependency(settings.AUTH_USER_MODEL)` already built
      `django_admin_log.user_id` against `identity_user`.
      """
      return _table_exists(connection, _OLD_USER) and _table_exists(connection, _ADMIN_LOG)


  def admin_log_user_fk_name(connection) -> str | None:
      """The name of `django_admin_log.user_id`'s foreign-key constraint,
      read from the catalogue.

      NEVER A LITERAL. Django hash-suffixes constraint names, so the name
      differs between boxes and nobody may hard-code it.
      """
      if not _table_exists(connection, _ADMIN_LOG):
          return None
      with connection.cursor() as cursor:
          cursor.execute(
              """
              SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_attribute att
                  ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
               WHERE rel.relname = %s
                 AND con.contype = 'f'
                 AND att.attname = 'user_id'
               LIMIT 1
              """,
              [_ADMIN_LOG],
          )
          row = cursor.fetchone()
      return row[0] if row else None


  def repoint(apps, schema_editor):
      """Drop the old FK (found by catalogue) and add one at
      `identity_user`. A no-op on a fresh install."""
      connection = schema_editor.connection
      refuse_if_rows_exist(connection)
      if not repoint_is_needed(connection):
          return
      name = admin_log_user_fk_name(connection)
      with connection.cursor() as cursor:
          if name:
              cursor.execute(f'ALTER TABLE "{_ADMIN_LOG}" DROP CONSTRAINT "{name}"')
          cursor.execute(
              f'ALTER TABLE "{_ADMIN_LOG}" '
              f'ADD CONSTRAINT "django_admin_log_user_id_identity_user_fk" '
              f'FOREIGN KEY (user_id) REFERENCES "{_NEW_USER}" (id) '
              f'DEFERRABLE INITIALLY DEFERRED'
          )


  def noop(apps, schema_editor):
      """The reverse.

      A DELIBERATE NO-OP, and the docstring says so plainly rather than
      leaving a reader to wonder: the reversal of a user-model swap is
      restoring the backup. This is the same position
      `tools/rag/migrations/0013_retire_chat_tables` took for a drop.
      """
  ```

- [ ] **Step 4: Write the migration itself.**
  ```python
  """Repoint `django_admin_log.user_id` at the new user model.

  Django's autodetector may not write migrations into a third-party app,
  so `django.contrib.admin`'s own FK to `auth_user` has to be moved by
  hand. The bodies live in `_0002_helpers.py` so they can be unit-tested
  without driving the migration executor.

  IRREVERSIBLE BY DESIGN: `reverse_code` is a documented no-op, because
  the reversal of a user-model swap is restoring the backup.
  """
  from django.db import migrations

  from identity.migrations import _0002_helpers as helpers


  class Migration(migrations.Migration):

      dependencies = [
          ("identity", "0001_initial"),
          ("admin", "0001_initial"),
      ]

      operations = [
          migrations.RunPython(helpers.repoint, helpers.noop),
      ]
  ```

- [ ] **Step 5: Run the tests.**
  Run: `.venv/bin/pytest -q identity/tests/test_migration_admin_log.py`
  Expected: PASS

- [ ] **Step 6: Prove the graph is what the spec says.**
  Run: `.venv/bin/python manage.py migrate --plan | grep -E 'identity|vision|rag' | tail -20`
  Expected: `identity.0001_initial` and `identity.0002_repoint_admin_log_fk`, in that order,
  and nothing else from `identity`.
  Run: `.venv/bin/python manage.py makemigrations --check --dry-run`
  Expected: exit 0.

- [ ] **Step 7: Docs.** `docs/OPERATIONS.md` — record `auth_user`, `auth_user_groups` and
  `auth_user_user_permissions` as **expected orphan tables**, left in place deliberately so
  `django.contrib.auth`'s migration state and the database do not disagree, so a future
  operator reading a dump does not think something is broken.

- [ ] **Step 8: Commit** as
  `fix(identity): T3 — repoint the admin log at the new user model, with a precondition`.

---
### Task 4: `identity/access.py` and `identity/audit.py` — the questions, the writer, and two guards

Two small modules that everything downstream reads, and the two structural guards that keep
them the only readers. `access.py` is the **named seam** every other column imports for
"posture, admin-ness, `owner_fields`"; `audit.py` is the only module in the codebase that may
touch `AuditEvent.objects` at all.

The distinction between `is_admin` and `sees_all_content` is the whole point of this task and
it must not be collapsed. `is_admin` answers *may this principal reach an admin surface*;
`sees_all_content` answers *may this principal read somebody else's words, pixels or bytes*.
They were one function in the spec's first draft, which is exactly how "conversations are
private" quietly became "private from the people who are not administrators".

**Files:**
- Create: `identity/access.py`, `identity/audit.py`
- Create: `identity/tests/test_access.py`, `identity/tests/test_audit.py`
- Modify: `identity/tests/_helpers.py` — `posture(...)`, `make_user`, `make_admin`, `sign_in`
- Modify: `foundation/ops/tests/test_import_law.py` — identity's private modules are
  off-limits to every other column
- Modify: `foundation/ops/tests/test_column_boundaries.py` — the `AuditEvent.objects` gate

**Interfaces:**
- Consumes: `identity.models` (Task 2), `identity.contracts.*` (Task 1).
- Produces:
  ```python
  # identity/access.py -- a NAMED SEAM: every column may import this module.
  def posture() -> str
  def accounts_on() -> bool
  def is_admin(principal) -> bool
  def sees_all_content(principal) -> bool
  def owner_fields(principal) -> dict          # {"owner_kind": ..., "owner_key": ...}

  # identity/audit.py -- a NAMED SEAM: every column may import this module.
  def record(actor, action, *, target_type="", target_key="", target_label="",
             source=SOURCE_WEB, **detail) -> AuditEvent
  def recent(limit: int = 100) -> list[AuditEvent]
  def for_target(target_type: str, target_key: str, limit: int = 100) -> list[AuditEvent]
  ```

**Steps:**

- [ ] **Step 1: Write `identity/tests/_helpers.py`** — this package's shared scaffolding, a
  plain importable module and **not** a `conftest.py`:
  ```python
  """Shared test helpers for `identity/tests`.

  Plain importable module -- **not** a `conftest.py` (the repo forbids
  them anywhere). Each test module imports what it needs explicitly;
  autouse fixtures stay *defined* per test module but delegate their
  bodies to the functions below.

  `posture(...)` is this package's answer to what
  `FARABUNKER_FEATURES` overriding already is for feature flags: a test
  that depends on a posture PINS IT, and always wins over the
  `FARABUNKER_TEST_POSTURE` sweep variable.
  """
  from __future__ import annotations

  import contextlib
  import itertools
  import os

  from django.contrib.auth import get_user_model

  from identity.contracts.postures import LIBRARY_OPEN, POSTURE_OPEN
  from identity.models import IdentitySettings

  _names = itertools.count()

  # The sweep variable, read HERE and nowhere else in the tree. Production
  # code must never read it: that would reintroduce the second truth the
  # database row exists to avoid (spec section 3.2).
  SWEEP_POSTURE_ENV = "FARABUNKER_TEST_POSTURE"


  @contextlib.contextmanager
  def posture(name: str, *, admin_sees_content: bool | None = None,
              library_posture: str | None = None):
      """Run the block with the box in `name` posture, then restore.

      Writes the singleton row directly rather than going through
      `identity.services.set_posture`, deliberately: `set_posture` REFUSES
      a switch away from `open` with no active superuser, with `DEBUG` on,
      or with a default `SECRET_KEY` (spec section 14) -- and a test that
      merely needs the box to BE in a posture should not have to satisfy
      three production refusals to get there. The tests that exercise
      those refusals call `set_posture` on purpose.
      """
      row = IdentitySettings.get_solo()
      before = (row.posture, row.library_posture, row.admin_sees_content)
      row.posture = name
      if admin_sees_content is not None:
          row.admin_sees_content = admin_sees_content
      if library_posture is not None:
          row.library_posture = library_posture
      row.save()
      try:
          yield row
      finally:
          row.posture, row.library_posture, row.admin_sees_content = before
          row.save()


  def seed_sweep_posture() -> None:
      """Seed the settings row from `FARABUNKER_TEST_POSTURE` when it is
      set. Called from each package's autouse fixture; a test that pins
      its own posture always wins, the same precedence the feature flags
      already use."""
      name = os.environ.get(SWEEP_POSTURE_ENV, "").strip()
      if not name:
          return
      row = IdentitySettings.get_solo()
      if row.posture != name:
          row.posture = name
          row.save()


  def make_user(**overrides):
      """An ordinary active account."""
      fields = {"username": f"member-{next(_names)}", "password": "not-a-real-password"}
      fields.update(overrides)
      password = fields.pop("password")
      user = get_user_model()(**fields)
      user.set_password(password)
      user.save()
      return user


  def make_admin(**overrides):
      """An active superuser -- the principal every `S` route answers to."""
      overrides.setdefault("username", f"admin-{next(_names)}")
      overrides["is_superuser"] = True
      overrides["is_staff"] = True
      return make_user(**overrides)


  def user_principal(user):
      """The principal `principal_for_request` mints for `user`: the
      PRIMARY KEY as a string, never the username."""
      from identity.contracts.principals import Principal
      return Principal("user", str(user.pk))


  def sign_in(client, user, password: str = "not-a-real-password") -> None:
      """Sign `user` in on `client` through the real login machinery, so
      the session cookie a test carries is the one a browser would."""
      assert client.login(username=user.username, password=password)


  def reset_settings() -> None:
      """Put the singleton back to shipped defaults. Used by autouse
      fixtures in modules that write it directly."""
      row = IdentitySettings.get_solo()
      row.posture = POSTURE_OPEN
      row.library_posture = LIBRARY_OPEN
      row.admin_sees_content = False
      row.save()
  ```

- [ ] **Step 2: Write the failing test** `identity/tests/test_access.py`:
  ```python
  """The five access questions -- and the difference between two of them.

  ADMINISTERING IS NOT READING. `is_admin` answers "may this principal
  reach an admin surface"; `sees_all_content` answers "may this principal
  read somebody else's words, pixels or bytes". They were one function in
  the design's first draft, which is exactly how "conversations are
  private" quietly became "private from the people who are not
  administrators". Half the assertions below exist to keep them apart.
  """
  from __future__ import annotations

  import pytest

  from identity.access import accounts_on, is_admin, owner_fields, posture, sees_all_content
  from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
  from identity.contracts.principals import (
      ANONYMOUS, OPEN_PRINCIPAL, Principal, SERVICE_PRINCIPAL,
  )
  from identity.tests._helpers import make_admin, make_user, seed_sweep_posture, user_principal

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _sweep():
      seed_sweep_posture()


  class TestPosture:
      def test_the_default_is_open_and_that_is_todays_behaviour(self):
          assert posture() == POSTURE_OPEN
          assert accounts_on() is False

      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_every_other_posture_requires_accounts(self, name):
          from identity.tests._helpers import posture as pin
          with pin(name):
              assert accounts_on() is True


  class TestIsAdmin:
      def test_the_open_principal_is_an_admin(self):
          """On a box with no accounts there is nobody for anything to be
          hidden from, and answering False would make every admin surface
          unreachable in the posture that is the default."""
          assert is_admin(OPEN_PRINCIPAL) is True

      def test_an_active_superuser_is_an_admin(self):
          from identity.tests._helpers import posture as pin
          admin = make_admin()
          with pin(POSTURE_PERSONAL):
              assert is_admin(user_principal(admin)) is True

      def test_an_ordinary_member_is_not(self):
          from identity.tests._helpers import posture as pin
          with pin(POSTURE_PERSONAL):
              assert is_admin(user_principal(make_user())) is False

      def test_a_deactivated_superuser_is_not(self):
          """`is_active` is half the predicate. A demoted-by-deactivation
          admin must stop reaching the console on their next request, not
          on their next login."""
          from identity.tests._helpers import posture as pin
          admin = make_admin(is_active=False)
          with pin(POSTURE_PERSONAL):
              assert is_admin(user_principal(admin)) is False

      def test_a_service_principal_is_never_an_admin(self):
          """A machine caller that could reach the model console or the
          posture page would make the shell a privilege-escalation path
          with no login behind it."""
          from identity.tests._helpers import posture as pin
          with pin(POSTURE_ENTERPRISE):
              assert is_admin(SERVICE_PRINCIPAL) is False

      def test_anonymous_is_never_an_admin(self):
          from identity.tests._helpers import posture as pin
          with pin(POSTURE_ENTERPRISE):
              assert is_admin(ANONYMOUS) is False

      def test_a_user_principal_whose_key_is_not_a_primary_key_answers_false(self):
          """NEVER a 500. `Principal.key` is the pk as a string; a key
          that is not a decimal string would reach
          `User.objects.filter(pk=key)` and raise `ValueError` inside a
          request. A hand-written payload or a row from an older schema
          can supply one, so the shape is checked before the query."""
          from identity.tests._helpers import posture as pin
          with pin(POSTURE_PERSONAL):
              assert is_admin(Principal("user", "not-a-number")) is False

      def test_a_user_principal_naming_no_row_answers_false(self):
          from identity.tests._helpers import posture as pin
          with pin(POSTURE_PERSONAL):
              assert is_admin(Principal("user", "99999999")) is False


  class TestSeesAllContent:
      def test_open_sees_everything(self):
          assert sees_all_content(OPEN_PRINCIPAL) is True

      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_an_admin_with_the_setting_off_does_not(self, name):
          """THE DEFAULT, and the decision. An administrator runs the box
          without reading anybody's words."""
          from identity.tests._helpers import posture as pin
          admin = make_admin()
          with pin(name, admin_sees_content=False):
              assert is_admin(user_principal(admin)) is True
              assert sees_all_content(user_principal(admin)) is False

      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_turning_the_setting_on_flips_it(self, name):
          from identity.tests._helpers import posture as pin
          admin = make_admin()
          with pin(name, admin_sees_content=True):
              assert sees_all_content(user_principal(admin)) is True

      def test_a_member_never_sees_all_content_however_the_setting_is_set(self):
          from identity.tests._helpers import posture as pin
          member = make_user()
          for value in (False, True):
              with pin(POSTURE_ENTERPRISE, admin_sees_content=value):
                  assert sees_all_content(user_principal(member)) is False

      def test_the_two_non_open_postures_answer_identically(self):
          """THERE IS NO POSTURE BRANCH. `personal` and `enterprise`
          differ only in which pages exist; a predicate that read the
          posture would mean the same administrator saw different content
          on two boxes that had made the same choice."""
          from identity.tests._helpers import posture as pin
          admin, member = make_admin(), make_user()
          for setting in (False, True):
              answers = []
              for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
                  with pin(name, admin_sees_content=setting):
                      answers.append((sees_all_content(user_principal(admin)),
                                      sees_all_content(user_principal(member))))
              assert answers[0] == answers[1]

      def test_anonymous_and_service_see_nothing(self):
          from identity.tests._helpers import posture as pin
          with pin(POSTURE_ENTERPRISE, admin_sees_content=True):
              assert sees_all_content(ANONYMOUS) is False
              assert sees_all_content(SERVICE_PRINCIPAL) is False


  class TestOwnerFields:
      def test_it_is_the_two_columns_to_stamp(self):
          assert owner_fields(OPEN_PRINCIPAL) == {"owner_kind": "open", "owner_key": "box"}

      def test_a_user_is_stamped_by_primary_key_never_by_username(self):
          """A username is renameable; a rename must not orphan every row
          a person owns."""
          user = make_user(username="ann")
          assert owner_fields(user_principal(user)) == {
              "owner_kind": "user", "owner_key": str(user.pk)}


  class TestTheOpenBranchIsFirst:
      def test_open_posture_asks_the_user_table_nothing(self, django_assert_num_queries):
          """Spec section 3.4, made structural rather than promised: every
          function tests `accounts_on()` first and returns before it
          touches a second table. The one primary-key read of
          `IdentitySettings` is not a permission query -- it is the query
          that answers WHICH POSTURE, and the box must ask it before it
          can skip anything else."""
          from identity.models import IdentitySettings
          IdentitySettings.get_solo()          # warm the row so its creation is not counted
          with django_assert_num_queries(1):
              assert is_admin(OPEN_PRINCIPAL) is True
          with django_assert_num_queries(1):
              assert sees_all_content(OPEN_PRINCIPAL) is True
  ```

- [ ] **Step 3: Run it to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_access.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'identity.access'`

- [ ] **Step 4: Write `identity/access.py`.**
  ```python
  """The access questions identity can answer alone.

  A NAMED SEAM (import-law rule 2, as amended by spec section 4.2): every
  column may import this module. `identity.models`, `identity.views`,
  `identity.services`, `identity.forms` and `identity.middleware` are
  off-limits to every other column with no exception -- the same shape
  `models.registry.models`/`.views` already have, and enforced by the same
  gate.

  IDENTITY CANNOT ANSWER "WHICH DOCUMENTS". It may not import `tools/` or
  `agents/` (rule 4), so it answers questions about PRINCIPALS and lets
  each column turn those answers into a queryset of its own rows. That is
  what makes this column a base rather than a hub.
  """
  from __future__ import annotations

  from identity.contracts.postures import POSTURE_OPEN
  from identity.models import IdentitySettings, User


  def posture() -> str:
      """This box's posture, read from the one row that answers it.

      One primary-key read per request, on a connection Django already
      holds open (`CONN_MAX_AGE=600`). No cache: a cache would be a second
      truth with a staleness window, and the staleness window of a
      security posture is exactly the interval in which the box is wrong.
      """
      return IdentitySettings.get_solo().posture


  def accounts_on() -> bool:
      """Whether this box requires a signed-in caller.

      EVERY function below and every visibility function in every column
      tests this FIRST and returns its open branch before touching another
      table. That is how "an open box never runs a permission query" is
      made structural rather than promised.
      """
      return posture() != POSTURE_OPEN


  def _user_row(principal):
      """The active `User` row `principal` names, or None.

      The `isdigit()` check is not defensive noise: `Principal.key` for a
      user is the primary key AS A STRING, and a key that is not a decimal
      string -- from a hand-written payload, or a row written against an
      older schema -- would reach `filter(pk=...)` and raise `ValueError`
      inside a request. A never-500 surface cannot afford that, and
      "answer no" is the correct reading of an unparseable identity.
      """
      if getattr(principal, "kind", None) != "user":
          return None
      key = principal.key
      if not isinstance(key, str) or not key.isdigit():
          return None
      return User.objects.filter(pk=int(key), is_active=True).first()


  def is_admin(principal) -> bool:
      """May this principal reach an ADMIN SURFACE -- the model console,
      the queue settings, the users page, the posture page, `/admin/`.

      True for `OPEN_PRINCIPAL` and for an ACTIVE SUPERUSER. False for
      `ANONYMOUS` and for every service principal.

      `OPEN_PRINCIPAL` is an admin because on a box with no accounts there
      is nobody for anything to be hidden from, and answering False would
      make every admin surface unreachable in the posture that is the
      default.

      A SERVICE principal is never an admin, whatever it was issued: a
      machine caller that could reach the model console or the posture
      page would make the shell a privilege-escalation path with no login
      behind it.

      THIS IS NOT `sees_all_content`. Administering is what this answers;
      reading is what that one answers. An administrator cancels any job,
      labels any document and deletes any document with THIS predicate
      alone.
      """
      if not accounts_on():
          return True
      user = _user_row(principal)
      return bool(user and user.is_superuser)


  def sees_all_content(principal) -> bool:
      """Whether this principal may read OTHER PEOPLE'S CONTENT -- their
      conversations, Ask history, generated images, agent and flow bodies,
      document bytes, and the payload/answer text of jobs they did not
      start.

      OPEN -> True. There is nobody for anything to be hidden from.
      OTHERWISE -> `is_admin(principal) and admin_sees_content`.

      NO POSTURE BRANCH, deliberately. `personal` and `enterprise` answer
      this identically; they differ only in which pages exist. A predicate
      that read the posture would mean the same administrator saw
      different content on two boxes that had made the same choice -- and
      the choice is the SETTING, not the posture.
      """
      if not accounts_on():
          return True
      if not is_admin(principal):
          return False
      return IdentitySettings.get_solo().admin_sees_content


  def owner_fields(principal) -> dict:
      """The two columns to stamp on a row this principal is creating.

      MOVED here from `agents/visibility.py` so all five owned tables in
      three columns share ONE definition rather than three that agree by
      convention. A dict rather than two arguments, so a call site cannot
      pass one and forget the other.

      No database read at all: ownership is stamped from the principal, and
      the principal is already resolved.
      """
      return {"owner_kind": principal.kind, "owner_key": principal.key}
  ```

- [ ] **Step 5: Run the access tests.**
  Run: `.venv/bin/pytest -q identity/tests/test_access.py`
  Expected: PASS

- [ ] **Step 6: Write the failing test** `identity/tests/test_audit.py`:
  ```python
  """`identity/audit.py` -- the one writer, and the only module in the
  codebase that may touch `AuditEvent.objects` at all.
  """
  from __future__ import annotations

  import pytest

  from identity import audit
  from identity.contracts import actions
  from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL
  from identity.models import AuditEvent
  from identity.tests._helpers import make_admin, user_principal

  pytestmark = pytest.mark.django_db


  class TestRecord:
      def test_it_writes_one_row_with_the_actor_flattened(self):
          row = audit.record(OPEN_PRINCIPAL, actions.POSTURE_CHANGED,
                             target_type="posture", target_key="1", to="personal")
          assert (row.actor_kind, row.actor_key) == ("open", "box")
          assert row.action == actions.POSTURE_CHANGED
          assert row.detail == {"to": "personal"}

      def test_it_resolves_the_actor_label_at_write_time_and_never_later(self):
          """Denormalised on purpose: an audit line must still read
          correctly after the account is renamed or deactivated, and a
          join that resolves a deleted pk to "unknown" is an audit trail
          that forgets."""
          admin = make_admin(username="ann")
          row = audit.record(user_principal(admin), actions.LOGIN)
          admin.username = "ann-renamed"
          admin.save()
          assert AuditEvent.objects.get(pk=row.pk).actor_label == "ann"

      def test_the_open_principal_gets_a_readable_label_too(self):
          row = audit.record(OPEN_PRINCIPAL, actions.ADOPTED)
          assert row.actor_label

      def test_an_anonymous_actor_is_recordable(self):
          """`identity.login_failed` is written before anybody is signed
          in, so the writer must accept the sentinel."""
          row = audit.record(ANONYMOUS, actions.LOGIN_FAILED, target_label="ann")
          assert row.actor_kind == "anonymous"
          assert row.target_label == "ann"

      def test_a_login_failure_records_the_username_and_nothing_else(self):
          """It exists so a brute-force attempt is visible. It records the
          SUBMITTED username and never the password, never a hash of it,
          and never the request body."""
          row = audit.record(ANONYMOUS, actions.LOGIN_FAILED, target_label="ann")
          serialised = f"{row.detail}{row.target_label}{row.target_key}"
          assert "password" not in serialised.lower()

      def test_an_unknown_action_is_refused_by_the_model(self):
          with pytest.raises(ValueError):
              audit.record(OPEN_PRINCIPAL, "identity.invented_this")


  class TestReaders:
      def test_recent_is_newest_first_and_bounded(self):
          for _ in range(5):
              audit.record(OPEN_PRINCIPAL, actions.LOGIN)
          assert len(audit.recent(limit=3)) == 3

      def test_for_target_filters_to_one_row(self):
          audit.record(OPEN_PRINCIPAL, actions.USER_CREATED,
                       target_type="user", target_key="1")
          audit.record(OPEN_PRINCIPAL, actions.USER_CREATED,
                       target_type="user", target_key="2")
          found = audit.for_target("user", "2")
          assert [r.target_key for r in found] == ["2"]

      def test_the_readers_live_here_so_the_guard_needs_no_exception(self):
          """A guard with an exception is a guard somebody widens. The
          audit page reads through these, so no page ever names
          `AuditEvent.objects`."""
          assert callable(audit.recent) and callable(audit.for_target)
  ```

- [ ] **Step 7: Write `identity/audit.py`.**
  ```python
  """The one writer, and the one reader, of the audit trail.

  A NAMED SEAM: every column may import this module. It is also the ONLY
  module in the codebase that may perform ANY `AuditEvent.objects`
  attribute access -- not `.create`, not `.filter`, not `.update`, not
  `.delete` -- pinned by an AST guard in
  `foundation/ops/tests/test_column_boundaries.py`.

  THE GUARD FORBIDS THE WHOLE MANAGER, NOT JUST `.create`, because
  append-only is a code-level property here: `.update()` and `.delete()`
  never call `save()`, so a guard that only looked for `create` would let
  through the two operations that actually destroy an audit trail. The
  readers below exist for the same reason -- an audit page that had to
  name `AuditEvent.objects` would need an exception, and a guard with an
  exception is a guard somebody widens.
  """
  from __future__ import annotations

  from identity.contracts.actions import SOURCE_WEB
  from identity.models import AuditEvent, User

  _OPEN_LABEL = "this box (no accounts)"
  _SERVICE_LABEL = "a local command"
  _ANONYMOUS_LABEL = "not signed in"


  def _label_for(actor) -> str:
      """The actor's display name AT THE TIME OF THE EVENT.

      Resolved here, written once, and never resolved again. A user's
      label is their username; the three non-user kinds get a readable
      constant, because "open/box" on an audit page is a shape, not a
      sentence.
      """
      kind = getattr(actor, "kind", "")
      if kind == "user":
          key = actor.key
          if isinstance(key, str) and key.isdigit():
              row = User.objects.filter(pk=int(key)).values_list("username", flat=True).first()
              if row:
                  return row
          return f"user {actor.key}"
      if kind == "open":
          return _OPEN_LABEL
      if kind == "service":
          return _SERVICE_LABEL
      if kind == "anonymous":
          return _ANONYMOUS_LABEL
      return kind


  def record(actor, action: str, *, target_type: str = "", target_key: str = "",
             target_label: str = "", source: str = SOURCE_WEB, **detail) -> AuditEvent:
      """Write one audit row for `actor` doing `action`.

      `**detail` becomes the row's JSON `detail`, so a caller writes
      `record(actor, POSTURE_CHANGED, to="personal")` rather than
      assembling a dict. `action` is validated against the closed
      catalogue by `AuditEvent.save()` -- a typo raises here, at the call
      site, rather than silently splitting a report in two.

      Never swallows. An audit write that failed quietly would leave the
      operator believing the trail is complete, which is worse than the
      failure it hid. Callers that genuinely must not fail on it (there
      are none in IA-1) would wrap this themselves.
      """
      return AuditEvent.objects.create(
          actor_kind=getattr(actor, "kind", ""),
          actor_key=getattr(actor, "key", ""),
          actor_label=_label_for(actor)[:255],
          action=action,
          target_type=target_type[:64],
          target_key=str(target_key)[:255],
          target_label=str(target_label)[:255],
          source=source,
          detail=detail,
      )


  def recent(limit: int = 100) -> list[AuditEvent]:
      """The newest `limit` rows, newest first (`Meta.ordering`)."""
      return list(AuditEvent.objects.all()[:limit])


  def for_target(target_type: str, target_key: str, limit: int = 100) -> list[AuditEvent]:
      """Everything recorded about one target, newest first."""
      return list(
          AuditEvent.objects.filter(
              target_type=target_type, target_key=str(target_key))[:limit]
      )
  ```

- [ ] **Step 8: Run the audit tests.**
  Run: `.venv/bin/pytest -q identity/tests/test_audit.py`
  Expected: PASS

- [ ] **Step 9: Add the `AuditEvent.objects` guard** to
  `foundation/ops/tests/test_column_boundaries.py`, in the same AST shape as the existing chat
  `.objects` gate:
  ```python
  # --- IA-1: `AuditEvent.objects` is `identity/audit.py`'s alone ---------

  _AUDIT_WRITER = frozenset({"identity/audit.py"})


  def _audit_manager_access(source: str) -> int:
      """How many `AuditEvent.objects` attribute accesses `source` makes --
      `ast.Attribute(value=ast.Name(id="AuditEvent"), attr="objects")`, the
      SAME shape the chat `.objects` gate uses, so it catches the access in
      a function body as readily as at module scope and does not trip over
      the word "objects" in a docstring."""
      try:
          tree = ast.parse(source)
      except SyntaxError:
          return 0
      return sum(
          1 for node in ast.walk(tree)
          if isinstance(node, ast.Attribute) and node.attr == "objects"
          and isinstance(node.value, ast.Name) and node.value.id == "AuditEvent"
      )


  def test_no_module_outside_audit_touches_auditevent_objects():
      """Append-only, enforced at the level it is actually a property of.

      `AuditEvent.save()` refuses a re-save, but a queryset `.update()` or
      `.delete()` never calls `save()`. So the manager itself is off
      limits everywhere but the one writer -- `.update()` and `.delete()`
      included, because those are the two operations that actually destroy
      an audit trail. Reads go through `identity.audit.recent`/
      `for_target`, so the audit page needs no exception either.
      """
      out = subprocess.run(
          ["git", "ls-files", "--", "agents", "tools", "models", "foundation", "identity",
           "config", "scripts"],
          cwd=REPO_ROOT, capture_output=True, text=True, check=True,
      )
      offenders: dict[str, int] = {}
      for relative in out.stdout.splitlines():
          if not relative.endswith(".py") or _is_test_file(relative):
              continue
          if relative in _AUDIT_WRITER:
              continue
          count = _audit_manager_access((REPO_ROOT / relative).read_text(encoding="utf-8"))
          if count:
              offenders[relative] = count
      assert offenders == {}, offenders


  def test_the_audit_writer_exclusion_is_a_closed_set_of_one():
      """Anti-vacuous pin on the exclusion itself."""
      assert _AUDIT_WRITER == frozenset({"identity/audit.py"})
      for relative in _AUDIT_WRITER:
          assert (REPO_ROOT / relative).is_file(), relative


  def test_the_audit_gate_catches_update_and_delete_not_only_create():
      """Anti-vacuous pin, and the case a `.create`-only guard would have
      waved through -- which is also the case that actually destroys an
      audit trail."""
      assert _audit_manager_access("AuditEvent.objects.create(action='x')\n") == 1
      assert _audit_manager_access("AuditEvent.objects.update(action='x')\n") == 1
      assert _audit_manager_access("AuditEvent.objects.all().delete()\n") == 1
      assert _audit_manager_access("def f():\n    return AuditEvent.objects.filter()\n") == 1
      assert _audit_manager_access('"""AuditEvent.objects is off limits."""\n') == 0
  ```

- [ ] **Step 10: Add the private-modules gate** to `foundation/ops/tests/test_import_law.py`.
  It is **its own sweep**, deliberately not folded into `FORBIDDEN_MODULES`: that constant is
  swept over `tools`/`foundation`/`agents` only, and folding identity's names into it would
  drag `models/` into the scan and start flagging `models/queue` importing its own `models.py`.
  ```python
  # --- IA-1: identity's private modules are off-limits to every column ---

  # The four modules every column MAY import (spec section 4.2's named
  # seams) are `identity.contracts.*`, `identity.access`, `identity.request`
  # and `identity.audit`. Everything else in the column is private, exactly
  # as `models.registry.models`/`.views` are.
  IDENTITY_FORBIDDEN_MODULES = (
      "identity.models", "identity.views", "identity.services",
      "identity.forms", "identity.middleware",
  )

  # `identity/` itself is excluded, for the same intra-column reason
  # `models/` is excluded from the sweep above: `identity/views.py`
  # importing `identity.services` is not a violation of anything.
  _IDENTITY_SCANNED_COLUMNS = ("tools", "foundation", "agents", "models")


  def _identity_forbidden_imports(source: str) -> list[str]:
      """Same AST shape as `_forbidden_imports` above, including the
      `from identity import models` form where the imported NAME, not the
      module path, is the forbidden leaf."""
      try:
          tree = ast.parse(source)
      except SyntaxError:
          return []
      hits: list[str] = []
      for node in ast.walk(tree):
          if isinstance(node, ast.Import):
              for alias in node.names:
                  for forbidden in IDENTITY_FORBIDDEN_MODULES:
                      if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                          hits.append(forbidden)
          elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
              for alias in node.names:
                  full = f"{node.module}.{alias.name}"
                  for forbidden in IDENTITY_FORBIDDEN_MODULES:
                      if full == forbidden or full.startswith(forbidden + "."):
                          hits.append(forbidden)
      return hits


  def test_no_column_imports_identitys_private_modules():
      """Import-law rule 2, as amended (spec section 4.2). A column asks
      identity a QUESTION (`identity.access`, `identity.request`,
      `identity.audit`) or names a TYPE (`identity.contracts.*`); it never
      reaches the storage, the views, the forms, the services or the
      middleware.

      Cross-column foreign keys use STRINGS, never imports:
      `settings.AUTH_USER_MODEL`, `"identity.Entitlement"`, `"auth.Group"`.
      That is exactly what `AUTH_USER_MODEL` exists for, and it means no
      column ever imports `identity.models`.
      """
      out = subprocess.run(
          ["git", "ls-files", "--"] + list(_IDENTITY_SCANNED_COLUMNS),
          cwd=REPO_ROOT, capture_output=True, text=True, check=True,
      )
      offenders: dict[str, list[str]] = {}
      for relative in out.stdout.splitlines():
          if not relative.endswith(".py") or _is_test_file(relative):
              continue
          hits = _identity_forbidden_imports(
              (REPO_ROOT / relative).read_text(encoding="utf-8"))
          if hits:
              offenders[relative] = hits
      assert offenders == {}, offenders


  def test_the_identity_private_module_gate_would_catch_a_violation():
      """Anti-vacuous pin: both import forms, and the four permitted seams
      surviving alongside them unflagged."""
      assert _identity_forbidden_imports(
          "from identity.models import User\n") == ["identity.models"]
      assert _identity_forbidden_imports(
          "from identity import services\n") == ["identity.services"]
      assert _identity_forbidden_imports(
          "import identity.middleware\n") == ["identity.middleware"]
      permitted = (
          "from identity.access import is_admin\n"
          "from identity.audit import record\n"
          "from identity.request import principal_for_request\n"
          "from identity.contracts.principals import Principal\n"
      )
      assert _identity_forbidden_imports(permitted) == []


  def test_the_identity_private_module_sweep_reaches_all_four_columns():
      """Anti-vacuous pin: a sweep that quietly stopped seeing a column
      would still pass every assertion in it."""
      out = subprocess.run(
          ["git", "ls-files", "--"] + list(_IDENTITY_SCANNED_COLUMNS),
          cwd=REPO_ROOT, capture_output=True, text=True, check=True,
      )
      swept = [p for p in out.stdout.splitlines()
               if p.endswith(".py") and not _is_test_file(p)]
      for column in _IDENTITY_SCANNED_COLUMNS:
          assert any(p.startswith(column + "/") for p in swept), column
      assert "tools/rag/views.py" in swept
  ```

- [ ] **Step 11: Run the guards and the column.**
  Run: `.venv/bin/pytest -q identity foundation/ops/tests`
  Expected: PASS

- [ ] **Step 12: Docs.** `foundation/README.md` and `models/README.md` — the amended rule-2
  seam list now names `identity.contracts.*`, `identity.access`, `identity.request` and
  `identity.audit` as the four modules every column may import, and names the five that are
  private.

- [ ] **Step 13: Commit** as
  `feat(identity): T4 — the access questions, the one audit writer, and two structural guards`.

---

### Task 5: `identity/request.py` — the one request→principal point, and the end of `ACCOUNTS_REQUIRED`

`principal_for_request` moves out of `agents/chat` and gets a real body. `ACCOUNTS_REQUIRED`
disappears from the tree entirely: its `True` branch has never worked (it raises), so deleting
it removes a documented non-feature, not a behaviour — and leaving it would mean two answers to
"what posture is this box in" in a box that runs three processes with independently supplied
environments.

**Nothing enforces anything yet.** The gate that turns an `ANONYMOUS` answer into a `302` is
Task 7. This task is the seam and its tests.

**Files:**
- Create: `identity/request.py`, `identity/tests/test_request.py`
- Delete: `agents/chat/principal.py`, `agents/chat/tests/test_principal.py`
- Modify: `config/settings.py` — delete `ACCOUNTS_REQUIRED` and its comment block (lines 29–36)
- Modify: `agents/chat/views/thread.py:26`, `agents/chat/views/conversations.py:14`,
  `agents/chat/views/turns.py:28`, `agents/chat/views/defaults.py:21` — repoint the import
- Modify: `foundation/ops/tests/test_import_law.py` — `_PRINCIPAL_CONSTRUCTORS` is now real
- Create: `foundation/ops/tests/test_no_accounts_required.py` — the grep gate
- Modify: `agents/chat/README.md`, `docs/DEV.md` §"Accounts are off",
  `docs/adr/0015-agent-layer-and-tool-contract.md` (the amendment at its foot)

**Interfaces:**
- Consumes: `identity.access.accounts_on` (Task 4), `identity.contracts.principals` (Task 1).
- Produces:
  ```python
  # identity/request.py -- a NAMED SEAM: every column may import this module.
  def principal_for_request(request) -> Principal | _Anonymous
  ```

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_request.py`:
  ```python
  """`principal_for_request` -- the ONE request-to-principal point.

  Uses `rf` (RequestFactory) rather than the test `Client` wherever it can,
  so it triggers no URL resolution and no middleware at all: this function
  is being tested, not the gate that consumes it (Task 7).
  """
  from __future__ import annotations

  import pytest

  from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_PERSONAL
  from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL, Principal
  from identity.request import principal_for_request
  from identity.tests._helpers import make_user, posture, seed_sweep_posture

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _sweep():
      seed_sweep_posture()


  class TestOpenPosture:
      def test_it_returns_the_shared_open_principal(self, rf):
          """The SAME object, not an equal one built here -- so this
          module and every command hand out one answer to "who is this
          box"."""
          assert principal_for_request(rf.get("/chat/")) is OPEN_PRINCIPAL

      def test_it_does_not_look_at_the_request_user_at_all(self, rf, django_assert_num_queries):
          """An open box never runs a permission query. The one read is
          the settings row."""
          from identity.models import IdentitySettings
          IdentitySettings.get_solo()
          request = rf.get("/chat/")
          with django_assert_num_queries(1):
              assert principal_for_request(request) is OPEN_PRINCIPAL


  class TestAccountsOn:
      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_a_signed_in_user_becomes_a_user_principal_keyed_by_pk(self, rf, name):
          user = make_user()
          request = rf.get("/chat/")
          request.user = user
          with posture(name):
              assert principal_for_request(request) == Principal("user", str(user.pk))

      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_a_request_with_no_session_is_ANONYMOUS_never_the_open_principal(self, rf, name):
          """A posture leak wearing an unauthenticated request's clothes
          is exactly what this must not do."""
          from django.contrib.auth.models import AnonymousUser
          request = rf.get("/chat/")
          request.user = AnonymousUser()
          with posture(name):
              answer = principal_for_request(request)
          assert answer is ANONYMOUS
          assert answer is not OPEN_PRINCIPAL

      def test_a_request_with_no_user_attribute_at_all_is_ANONYMOUS(self, rf):
          """A request that never reached `AuthenticationMiddleware` --
          a management command's synthetic request, a test double -- must
          not be read as the box."""
          request = rf.get("/chat/")
          with posture(POSTURE_PERSONAL):
              assert principal_for_request(request) is ANONYMOUS


  class TestTheSettingIsGone:
      def test_accounts_required_is_not_a_setting_any_more(self):
          """Its True branch never worked -- it raised. Deleting it
          removes a documented non-feature, not a behaviour, and leaves
          exactly one answer to "what posture is this box in"."""
          from django.conf import settings
          assert not hasattr(settings, "ACCOUNTS_REQUIRED")
  ```

- [ ] **Step 2: Run it to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_request.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'identity.request'`

- [ ] **Step 3: Write `identity/request.py`.**
  ```python
  """Who is acting on this request.

  ONE function, and it is the only place in the codebase that turns an
  HTTP request into a principal. Every view reaches it; none of them
  builds a principal itself. MOVED here from `agents/chat/principal.py`,
  which is deleted -- the question "who is in front of the browser" is not
  an agents-column question, and it stopped being one the moment there
  were accounts.

  This module and `identity/contracts/principals.py` are the only two
  files that may CONSTRUCT a `Principal`, pinned by the AST guard in
  `foundation/ops/tests/test_import_law.py`.
  """
  from __future__ import annotations

  from identity.access import accounts_on
  from identity.contracts.principals import ANONYMOUS, OPEN_PRINCIPAL, Principal


  def principal_for_request(request):
      """The principal acting on `request`.

      OPEN posture: one principal for the whole machine, and no query
      against any identity table -- `accounts_on()` reads the settings
      singleton and returns before anything else is touched.

      PERSONAL/ENTERPRISE: the signed-in user, or `ANONYMOUS` for a
      request with no session -- NEVER the open principal, which would be
      a posture leak wearing an unauthenticated request's clothes.

      `ANONYMOUS` is not a `Principal` and "anonymous" is not a principal
      kind (see `identity/contracts/principals.py`). Handling it is the
      GATE's job, not the view's: `IdentityGateMiddleware` refuses an
      anonymous request before a view ever sees it, so this value reaches
      an access function only through a direct call in a test.
      """
      if not accounts_on():
          return OPEN_PRINCIPAL
      user = getattr(request, "user", None)
      if user is None or not user.is_authenticated:
          return ANONYMOUS
      return Principal("user", str(user.pk))
  ```

- [ ] **Step 4: Delete `agents/chat/principal.py` and `agents/chat/tests/test_principal.py`,
  and repoint the four view modules.** Each becomes:
  ```python
  from identity.request import principal_for_request
  ```
  No call site's *body* changes: every one already passes whatever it is handed straight into a
  visibility function.

- [ ] **Step 5: Delete `ACCOUNTS_REQUIRED`** from `config/settings.py` — the constant and the
  nine-line comment block above it (**lines 28–37**, the comment opening at 28 and the constant
  itself at 37). Nothing replaces it: `identity.access.posture()` is the answer now.

- [ ] **Step 6: Add the grep gate** `foundation/ops/tests/test_no_accounts_required.py`:
  ```python
  """`ACCOUNTS_REQUIRED` appears nowhere in the tree.

  A grep gate, not a style rule. The setting's `True` branch never worked
  -- `agents/chat/principal.py::principal_for_request` raised on it -- and
  the design deleted it rather than flipping it, because two truths about
  whether permissions are enforced would be two answers in a box that runs
  three processes with independently supplied environments.

  Historical mentions in `docs/adr/` are EXEMPT: an ADR records what was
  decided at the time, and rewriting one to hide a retired name would be
  falsifying the record. The amendment at ADR 0015's foot is what says the
  setting is gone.
  """
  from __future__ import annotations

  import subprocess
  from pathlib import Path

  from django.conf import settings

  REPO_ROOT = Path(settings.BASE_DIR)

  NEEDLE = "ACCOUNTS_REQUIRED"
  # ADRs and executed plans are historical records, not live descriptions.
  _EXEMPT_PREFIXES = ("docs/adr/", "docs/superpowers/plans/", "docs/superpowers/specs/")


  def _tracked_files() -> list[str]:
      out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                           capture_output=True, text=True, check=True)
      return out.stdout.splitlines()


  def test_the_setting_is_gone_from_every_live_file():
      offenders = []
      for relative in _tracked_files():
          if relative.startswith(_EXEMPT_PREFIXES):
              continue
          path = REPO_ROOT / relative
          try:
              text = path.read_text(encoding="utf-8")
          except (UnicodeDecodeError, OSError, IsADirectoryError):
              continue
          if NEEDLE in text:
              offenders.append(relative)
      assert offenders == [], offenders


  def test_the_gate_is_reading_real_files():
      """Anti-vacuous pin: a broken `git ls-files` call would make the
      test above pass by looking at nothing."""
      files = _tracked_files()
      assert len(files) > 100, len(files)
      assert "config/settings.py" in files


  def test_the_exemption_is_narrow_and_real():
      """The ADRs really do still mention it -- which is why the
      exemption exists and why it must not widen to all of `docs/`."""
      adr = (REPO_ROOT / "docs/adr/0015-agent-layer-and-tool-contract.md")
      assert NEEDLE in adr.read_text(encoding="utf-8")
      assert not "docs/DEV.md".startswith(_EXEMPT_PREFIXES)
  ```

- [ ] **Step 7: Move the request entry in the AST guard.** In
  `foundation/ops/tests/test_import_law.py`, `agents/chat/principal.py` is deleted in this
  task, so its entry in `_PRINCIPAL_CONSTRUCTORS` is **replaced** — not added beside — by the
  file that took over its question. `test_the_principal_constructors_list_is_not_silently_empty`
  asserts every listed path exists on disk, so leaving the old entry would fail loudly, which
  is the point of that pin:
  ```python
  _PRINCIPAL_CONSTRUCTORS = frozenset({
      "identity/contracts/principals.py",  # the type and the three constants
      "identity/request.py",               # who is on this REQUEST
      # DELETED IN TASK 11. `agents/runtime/bindings.py::principal_for`
      # answers "who is this AGENT acting as", which the acting rule
      # (spec section 5.3) makes the wrong question: a turn acts as the
      # USER. Listed here only until Task 11 removes both the function
      # and this line.
      "agents/runtime/bindings.py",
  })
  ```

- [ ] **Step 8: Run everything this touched.**
  Run: `.venv/bin/pytest -q identity agents foundation`
  Expected: PASS

- [ ] **Step 9: Docs.**
  - `agents/chat/README.md` — items 1 and 2 of its "One branch point" section are rewritten:
    the request→principal point now lives at `identity/request.py`, and the branch is the
    posture row, not a setting.
  - `docs/DEV.md` — the "Accounts are off" section is rewritten as **"Accounts, and the three
    postures"**: the box ships `open`; `manage.py identity_posture personal` (Task 6) switches
    it; a forgotten password is reset by an administrator with Django's own
    `manage.py changepassword <username>`, because there is no email server on the box.
  - `docs/adr/0015-agent-layer-and-tool-contract.md` — append an amendment at its foot
    recording that §10's `ACCOUNTS_REQUIRED` branch point is corrected: the setting is
    **deleted**, not flipped, and the posture lives in `IdentitySettings.posture`.

- [ ] **Step 10: Commit** as
  `feat(identity): T5 — one request-to-principal point, and ACCOUNTS_REQUIRED deleted`.

---

### Task 6: `identity/services.py` and `identity/checks.py` — the guarded writes, the three refusals, sessions and cookies

Every write that can lock an operator out of their own box goes through one module, and every
one of them is audited. Three system checks fire at boot; `set_posture` runs the same three
conditions at **run time**, because the image starts with `migrate && <server>` and a check
that runs only at startup would let an operator flip the posture on a running box with `DEBUG`
on and believe accounts were enforced.

**Files:**
- Create: `identity/services.py`, `identity/checks.py`,
  `identity/management/__init__.py`, `identity/management/commands/__init__.py`,
  `identity/management/commands/identity_posture.py`
- Create: `identity/tests/test_services.py`, `identity/tests/test_checks.py`,
  `identity/tests/test_command_identity_posture.py`
- Modify: `identity/apps.py` — register the checks in `ready()`
- Modify: `config/settings.py` — `SESSION_SAVE_EVERY_REQUEST`, `SESSION_COOKIE_HTTPONLY`,
  `SESSION_COOKIE_SAMESITE`, `SECURE_COOKIES`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`,
  and the module-level `DEV_SECRET_KEY` constant the check compares against
- Modify: `docs/OPERATIONS.md`, `docs/DEV.md`

**Interfaces:**
- Consumes: `identity.access` (Task 4), `identity.audit` (Task 4), `identity.models` (Task 2).
- Produces:
  ```python
  # identity/services.py -- PRIVATE to the column. Nothing outside identity imports it.
  class ServiceRefused(ValueError): ...
  def create_user(actor, *, username, password, is_superuser=False, source=SOURCE_WEB) -> User
  def set_password(actor, user, raw_password, *, source=SOURCE_WEB) -> None
  def set_superuser(actor, user, value: bool, *, source=SOURCE_WEB) -> None
  def deactivate_user(actor, user, *, source=SOURCE_WEB) -> dict   # per-table owned-row counts
  def reactivate_user(actor, user, *, source=SOURCE_WEB) -> None
  def set_posture(actor, *, posture=None, library_posture=None,
                  admin_sees_content=None, session_idle_minutes=None,
                  source=SOURCE_WEB) -> IdentitySettings

  # identity/checks.py
  def check_debug_is_off(app_configs, **kwargs) -> list          # identity.E001
  def check_secret_key_is_not_the_default(app_configs, **kwargs) -> list   # identity.E002
  def check_secure_cookies(app_configs, **kwargs) -> list        # identity.W001
  ```

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_services.py`. This is the
  longest test module in the plan; every refusal gets its own case, and the three
  `set_posture` conditions are exercised **independently** so a test cannot pass because a
  second condition happened to fire.
  ```python
  """The guarded writes.

  THE THREE `set_posture` REFUSALS ARE EXERCISED INDEPENDENTLY. A test
  that switched with no admin AND `DEBUG` on would pass whichever
  condition fired first and prove nothing about the other -- so each case
  below satisfies the two conditions it is not testing.
  """
  from __future__ import annotations

  import pytest
  from django.contrib.auth import get_user_model

  from identity import services
  from identity.contracts import actions
  from identity.contracts.postures import (
      LIBRARY_LOCKED, LIBRARY_OPEN, POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
  )
  from identity.contracts.principals import OPEN_PRINCIPAL
  from identity.models import AuditEvent, IdentitySettings
  from identity.tests._helpers import make_admin, make_user, posture, user_principal

  pytestmark = pytest.mark.django_db

  REAL_KEY = "a-real-key-for-this-test-only"


  @pytest.fixture(autouse=True)
  def _a_real_key_and_no_debug(settings):
      """Two of the three `set_posture` conditions satisfied by default,
      so a test that does not care about them is not accidentally
      testing them."""
      settings.DEBUG = False
      settings.SECRET_KEY = REAL_KEY


  class TestCreateUser:
      def test_it_writes_the_row_and_one_audit_event(self):
          user = services.create_user(OPEN_PRINCIPAL, username="ann", password="s3cret-value")
          assert user.check_password("s3cret-value")
          assert AuditEvent.objects.filter(action=actions.USER_CREATED,
                                           target_key=str(user.pk)).count() == 1

      def test_it_never_records_the_password_anywhere_in_the_audit_row(self):
          user = services.create_user(OPEN_PRINCIPAL, username="ann", password="s3cret-value")
          row = AuditEvent.objects.get(action=actions.USER_CREATED, target_key=str(user.pk))
          assert "s3cret-value" not in f"{row.detail}{row.target_label}{row.actor_label}"

      def test_a_duplicate_username_is_a_refusal_not_an_integrity_error(self):
          services.create_user(OPEN_PRINCIPAL, username="ann", password="x1234567")
          with pytest.raises(services.ServiceRefused) as exc:
              services.create_user(OPEN_PRINCIPAL, username="ann", password="x1234567")
          assert "ann" in str(exc.value)


  class TestSetSuperuser:
      def test_promoting_and_demoting_each_write_their_own_action(self):
          admin, other = make_admin(), make_admin()
          services.set_superuser(user_principal(admin), other, False)
          assert AuditEvent.objects.filter(action=actions.SUPERUSER_REVOKED).exists()
          services.set_superuser(user_principal(admin), other, True)
          assert AuditEvent.objects.filter(action=actions.SUPERUSER_GRANTED).exists()

      def test_the_last_active_superuser_cannot_be_demoted(self):
          """One role, one column, and a box that can always be
          administered. The message names why."""
          admin = make_admin()
          with pytest.raises(services.ServiceRefused) as exc:
              services.set_superuser(user_principal(admin), admin, False)
          assert "last" in str(exc.value).lower()
          admin.refresh_from_db()
          assert admin.is_superuser is True

      def test_an_inactive_superuser_does_not_count_towards_the_guard(self):
          """"At least one ACTIVE superuser" is the invariant. A
          deactivated admin cannot log in, so it cannot be the one that
          keeps the box administrable."""
          live, dormant = make_admin(), make_admin(is_active=False)
          assert dormant.is_superuser and not dormant.is_active
          with pytest.raises(services.ServiceRefused):
              services.set_superuser(user_principal(live), live, False)

      def test_a_no_op_write_is_not_audited(self):
          """Setting a value to what it already is is not an event. An
          audit trail full of non-changes is a trail nobody reads."""
          admin, other = make_admin(), make_admin()
          before = AuditEvent.objects.count()
          services.set_superuser(user_principal(admin), other, True)
          assert AuditEvent.objects.count() == before


  class TestDeactivate:
      def test_it_deactivates_and_audits_and_reports_owned_row_counts(self):
          """Owned rows are LEFT IN PLACE, and the counts come back so the
          operator knows to run `reassign_owner`. A deleted user with
          owned rows is an orphan nobody can reason about."""
          admin, member = make_admin(), make_user()
          counts = services.deactivate_user(user_principal(admin), member)
          member.refresh_from_db()
          assert member.is_active is False
          assert isinstance(counts, dict)
          assert AuditEvent.objects.filter(action=actions.USER_DEACTIVATED).exists()

      def test_the_last_active_superuser_cannot_be_deactivated(self):
          admin = make_admin()
          with pytest.raises(services.ServiceRefused):
              services.deactivate_user(user_principal(admin), admin)

      def test_reactivation_is_the_same_action_in_reverse(self):
          admin, member = make_admin(), make_user(is_active=False)
          services.reactivate_user(user_principal(admin), member)
          member.refresh_from_db()
          assert member.is_active is True
          assert AuditEvent.objects.filter(action=actions.USER_REACTIVATED).exists()

      def test_a_deactivated_user_stops_being_authenticated_on_the_next_request(self, client):
          """SESSIONS DIE FOR FREE, and that is why no session-sweeping
          code is written: Django's `ModelBackend.get_user` calls
          `user_can_authenticate`, which is False for an inactive user, so
          `request.user` becomes anonymous on the very next request that
          presents the old cookie. A sweep on top would be a second,
          weaker copy of a mechanism Django maintains."""
          admin = make_admin()
          member = make_user(username="ann", password="not-a-real-password")
          assert client.login(username="ann", password="not-a-real-password")
          services.deactivate_user(user_principal(admin), member)
          from django.contrib.auth import get_user
          request = type("R", (), {"session": client.session})()
          assert get_user(request).is_authenticated is False


  class TestSetPosture:
      def test_switching_to_personal_writes_one_column_and_one_audit_row(self):
          make_admin()
          row = services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
          assert row.posture == POSTURE_PERSONAL
          assert AuditEvent.objects.filter(action=actions.POSTURE_CHANGED).count() == 1

      def test_switching_rewrites_no_owner_column_and_no_user_row(self):
          """"A switch, not a migration": one column and one audit row,
          and nothing else moves."""
          admin = make_admin()
          before = (admin.username, admin.is_superuser, admin.is_active)
          services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_ENTERPRISE)
          admin.refresh_from_db()
          assert (admin.username, admin.is_superuser, admin.is_active) == before

      def test_it_refuses_with_no_active_superuser(self, settings):
          """Condition 1 ALONE: `DEBUG` off and a real key are supplied by
          the module fixture, so only the missing admin can be the
          reason."""
          make_user()                      # a member, not an admin
          with pytest.raises(services.ServiceRefused) as exc:
              services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
          assert "superuser" in str(exc.value).lower()
          assert IdentitySettings.get_solo().posture == POSTURE_OPEN

      def test_it_refuses_with_debug_on(self, settings):
          """Condition 2 ALONE: an active admin exists and the key is
          real. `DEBUG=True` renders tracebacks with settings and
          environment to any visitor, which is a disclosure a posture with
          accounts must not permit."""
          make_admin()
          settings.DEBUG = True
          with pytest.raises(services.ServiceRefused) as exc:
              services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
          assert "debug" in str(exc.value).lower()

      def test_it_refuses_with_the_shipped_default_secret_key(self, settings):
          """Condition 3 ALONE. That key signs every session cookie; a box
          whose signing key is published in a public repository has
          accounts in name only, because anybody who can read the
          repository can mint a session for any account on it."""
          from config.settings import DEV_SECRET_KEY
          make_admin()
          settings.SECRET_KEY = DEV_SECRET_KEY
          with pytest.raises(services.ServiceRefused) as exc:
              services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
          assert "key" in str(exc.value).lower()

      @pytest.mark.parametrize("broken", ["no_admin", "debug", "key"])
      def test_switching_TO_open_is_refused_by_none_of_them(self, settings, broken):
          """Reducing a posture is always allowed. An operator whose box
          is misconfigured must always be able to make it LESS strict."""
          from config.settings import DEV_SECRET_KEY
          admin = make_admin()
          with posture(POSTURE_PERSONAL):
              if broken == "no_admin":
                  admin.is_active = False
                  admin.save()
              elif broken == "debug":
                  settings.DEBUG = True
              else:
                  settings.SECRET_KEY = DEV_SECRET_KEY
              services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_OPEN)
              assert IdentitySettings.get_solo().posture == POSTURE_OPEN

      def test_moving_to_personal_resets_a_locked_library_and_audits_the_reset(self):
          """`personal` renders no library-posture control, so a box
          arriving there from a locked `enterprise` would otherwise hold a
          lock with no page to lift it. THE ONE PLACE A POSTURE BRANCH
          LIVES, and it lives in a WRITE path on purpose -- the read paths
          in `identity/access.py` stay posture-free."""
          make_admin()
          with posture(POSTURE_ENTERPRISE, library_posture=LIBRARY_LOCKED):
              services.set_posture(OPEN_PRINCIPAL, posture=POSTURE_PERSONAL)
              row = IdentitySettings.get_solo()
              assert row.library_posture == LIBRARY_OPEN
          assert AuditEvent.objects.filter(
              action=actions.LIBRARY_POSTURE_CHANGED).exists()

      def test_the_content_toggle_is_audited_with_its_new_value(self):
          """It is the one setting that changes what an administrator can
          READ, so "when did this box start letting admins read
          conversations" must be answerable from the log rather than from
          memory."""
          make_admin()
          services.set_posture(OPEN_PRINCIPAL, admin_sees_content=True)
          row = AuditEvent.objects.get(action=actions.ADMIN_CONTENT_ACCESS_CHANGED)
          assert row.detail == {"to": True}

      def test_turning_the_content_toggle_on_is_refused_by_nothing(self, settings):
          """A widening the operator is entitled to make; the audit row is
          the record that they made it."""
          settings.DEBUG = True
          services.set_posture(OPEN_PRINCIPAL, admin_sees_content=True)
          assert IdentitySettings.get_solo().admin_sees_content is True

  @pytest.mark.django_db(transaction=True)
  class TestTheLastAdminGuardUnderConcurrency:
      """ITS OWN CLASS, with its own database mark.

      `transaction=True` gives each test a real, committing database
      rather than the module-level mark's wrapping transaction -- which
      is required here and nowhere else in this module: two threads
      cannot see each other's uncommitted rows, so under the ordinary
      mark the second thread would see NO superusers, take a different
      branch, and the test would pass for the wrong reason. It is also
      much slower (it truncates tables between tests), which is why it is
      quarantined here rather than applied to the whole module.
      """

      def test_the_last_admin_guard_serialises_two_concurrent_demotions(self):
          """"At least one row satisfying a predicate" is not expressible
          as a CheckConstraint, so two simultaneous demotions could each
          see two admins and each proceed. The guard runs inside
          `transaction.atomic()` with `select_for_update()`, which
          serialises them and makes the second see one admin and refuse.

          Exercised with two real connections rather than mocked: a guard
          that is only correct single-threaded is a guard that fails on
          the one day it matters."""
          import threading
          from django.db import connections
          first, second = make_admin(), make_admin()
          actor = user_principal(first)
          errors: list[Exception] = []

          def demote(target_pk):
              try:
                  target = get_user_model().objects.get(pk=target_pk)
                  services.set_superuser(actor, target, False)
              except services.ServiceRefused as exc:
                  errors.append(exc)
              finally:
                  connections.close_all()

          threads = [threading.Thread(target=demote, args=(pk,))
                     for pk in (first.pk, second.pk)]
          for thread in threads:
              thread.start()
          for thread in threads:
              thread.join()
          assert get_user_model().objects.filter(
              is_active=True, is_superuser=True).count() >= 1
          assert len(errors) == 1
  ```
  Note on `TestTheLastAdminGuardUnderConcurrency`: the class-level
  `@pytest.mark.django_db(transaction=True)` **overrides** the module-level
  `pytestmark = pytest.mark.django_db`, and it must — see the class's own docstring. Its
  `_a_real_key_and_no_debug` fixture still applies, because an autouse fixture is inherited by
  every class in the module.

- [ ] **Step 2: Run it to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_services.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'identity.services'`

- [ ] **Step 3: Add the settings this task needs**, in `config/settings.py`, immediately below
  `SECRET_KEY`:
  ```python
  # The shipped development key, named as a CONSTANT so
  # `identity/checks.py` and `identity.services.set_posture` can compare
  # against it without either of them carrying a second copy of the
  # literal -- two copies of "the insecure default" is exactly how one of
  # them comes to be wrong.
  DEV_SECRET_KEY = "dev-insecure-change-me"
  SECRET_KEY = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
  ```
  and, in a new **Sessions and cookies** block after `MIDDLEWARE`:
  ```python
  # --- Sessions and cookies (Identity & Auth) ----------------------------
  #
  # A ROLLING idle window, applied per request by
  # `identity.middleware.IdentityGateMiddleware` from
  # `IdentitySettings.session_idle_minutes`. This setting is what makes
  # the window actually roll: without it Django writes the session only
  # when it changes, and the expiry the middleware set would never move.
  SESSION_SAVE_EVERY_REQUEST = True

  # Stated explicitly rather than left to Django's defaults, because a
  # default that is right is still a default somebody can change without
  # noticing.
  SESSION_COOKIE_HTTPONLY = True
  SESSION_COOKIE_SAMESITE = "Lax"

  # Whether this box is behind TLS is a DEPLOYMENT fact, exactly as a
  # server location is -- the same exception the engine base-URL settings
  # above carry under ADR 0010's no-baked-defaults rule. Forcing Secure
  # cookies on would break login on a plain-HTTP LAN box, which is a
  # supported posture (docs/ARCHITECTURE.md's `isolated-lan`), so this
  # defaults OFF and `identity.W001` warns when accounts are on without
  # it.
  SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "0") == "1"
  SESSION_COOKIE_SECURE = SECURE_COOKIES
  CSRF_COOKIE_SECURE = SECURE_COOKIES
  ```

- [ ] **Step 4: Write `identity/services.py`.**
  ```python
  """The writes that can lock an operator out of their own box.

  PRIVATE TO THIS COLUMN. Nothing outside `identity/` imports this module
  -- pinned by `test_no_column_imports_identitys_private_modules`. Its
  callers are the identity pages, `identity/admin.py`, and this column's
  management commands, and that is deliberate: every one of these writes
  has a guard, and a second door to any of them is a guard that does not
  exist.
  """
  from __future__ import annotations

  from django.apps import apps
  from django.conf import settings
  from django.db import IntegrityError, transaction

  from identity import audit
  from identity.contracts import actions
  from identity.contracts.actions import SOURCE_WEB
  from identity.contracts.ownership import all_owned_rows
  from identity.contracts.postures import (
      LIBRARY_OPEN, POSTURE_OPEN, POSTURE_PERSONAL, POSTURES,
  )
  from identity.models import IdentitySettings, User


  class ServiceRefused(ValueError):
      """A write this platform will not perform, with an operator-readable
      reason.

      A distinct type, not a bare `ValueError`, so a view can render the
      message and a command can print it without either of them catching
      a genuine programming error by accident.
      """


  def _active_superusers(*, lock: bool = False):
      qs = User.objects.filter(is_active=True, is_superuser=True)
      return qs.select_for_update() if lock else qs


  def _refuse_if_last_admin(user, *, because: str) -> None:
      """Refuse to leave the box with zero active superusers.

      MUST BE CALLED INSIDE `transaction.atomic()` WITH THE LOCK TAKEN.
      "At least one row satisfying a predicate" is not expressible as a
      `CheckConstraint`, so two simultaneous demotions could each see two
      admins and each proceed. `select_for_update()` serialises them and
      makes the second see one admin and refuse.
      """
      remaining = [row.pk for row in _active_superusers(lock=True)]
      if remaining == [user.pk]:
          raise ServiceRefused(
              f"{user.username!r} is the last active administrator on this box, so "
              f"it cannot be {because}. Promote another account first, or nobody "
              f"will be able to administer this box."
          )


  def create_user(actor, *, username: str, password: str, is_superuser: bool = False,
                  source: str = SOURCE_WEB) -> User:
      """Create an account. The audit row records the username and the
      superuser flag, and NEVER the password or any derivative of it."""
      try:
          with transaction.atomic():
              user = User(username=username, is_superuser=is_superuser,
                          is_staff=is_superuser)
              user.set_password(password)
              user.save()
      except IntegrityError as exc:
          raise ServiceRefused(f"An account called {username!r} already exists.") from exc
      audit.record(actor, actions.USER_CREATED, target_type="user",
                   target_key=user.pk, target_label=username, source=source,
                   is_superuser=is_superuser)
      return user


  def set_password(actor, user, raw_password: str, *, source: str = SOURCE_WEB) -> None:
      """Reset somebody else's password.

      `identity.password_reset`, not `identity.password_changed`: the
      second is what a person does to their own, and the distinction is
      the one an operator reading the log actually wants.

      Django invalidates the target's existing sessions on a password
      change through `AbstractBaseUser.get_session_auth_hash`, so nothing
      here sweeps sessions either.
      """
      user.set_password(raw_password)
      user.save(update_fields=["password"])
      audit.record(actor, actions.PASSWORD_RESET, target_type="user",
                   target_key=user.pk, target_label=user.username, source=source)


  def set_superuser(actor, user, value: bool, *, source: str = SOURCE_WEB) -> None:
      """Promote or demote. Refuses to demote the last active superuser."""
      if user.is_superuser == value:
          # Not an event. An audit trail full of non-changes is a trail
          # nobody reads.
          return
      with transaction.atomic():
          if not value:
              _refuse_if_last_admin(user, because="demoted")
          user.is_superuser = value
          user.is_staff = value
          user.save(update_fields=["is_superuser", "is_staff"])
      audit.record(
          actor,
          actions.SUPERUSER_GRANTED if value else actions.SUPERUSER_REVOKED,
          target_type="user", target_key=user.pk, target_label=user.username, source=source,
      )


  def owned_row_counts(principal_key: str) -> dict:
      """Per-table counts of the rows a user owns, walking the registry.

      `apps.get_model` per spec, never an import: `identity/` may not
      import `agents` or `tools`. A registered model whose app is not
      installed on this box (`vision` with its feature off) is SKIPPED
      rather than raising -- the registration is flag-gated at
      `AppConfig.ready()`, so this is belt-and-braces for a stale entry.
      """
      counts: dict[str, int] = {}
      for spec in all_owned_rows():
          try:
              model = apps.get_model(spec.model)
          except LookupError:
              continue
          counts[spec.label] = model.objects.filter(
              owner_kind="user", owner_key=principal_key).count()
      return counts


  def deactivate_user(actor, user, *, source: str = SOURCE_WEB) -> dict:
      """Deactivate an account. USERS ARE NEVER DELETED.

      Four effects, and the third is the one that needs stating:
      1. refuses if `user` is the last active superuser;
      2. sets `is_active = False`;
      3. OWNED ROWS ARE LEFT IN PLACE, and the per-table counts come back
         so the operator knows to run `manage.py reassign_owner`. A
         deleted user with owned rows is an orphan nobody can reason
         about;
      4. writes one audit event.

      SESSIONS DIE FOR FREE, and that is why no session-sweeping code
      exists here: `ModelBackend.get_user` calls `user_can_authenticate`,
      which is False for an inactive user, so `request.user` becomes
      anonymous on the very next request that presents the old cookie.
      Writing a `Session`-table sweep on top of that would be a second,
      weaker copy of a mechanism Django maintains.

      (IA-2 adds one line here: delete the user's `EntitlementGrant`
      rows. The grants table does not exist yet.)
      """
      with transaction.atomic():
          _refuse_if_last_admin(user, because="deactivated")
          user.is_active = False
          user.save(update_fields=["is_active"])
      counts = owned_row_counts(str(user.pk))
      audit.record(actor, actions.USER_DEACTIVATED, target_type="user",
                   target_key=user.pk, target_label=user.username, source=source,
                   owned_rows=counts)
      return counts


  def reactivate_user(actor, user, *, source: str = SOURCE_WEB) -> None:
      """The same action in reverse, minus any grants -- a dropped grant
      is a decision somebody made, and restoring it silently would undo
      that decision without anybody choosing to."""
      if user.is_active:
          return
      user.is_active = True
      user.save(update_fields=["is_active"])
      audit.record(actor, actions.USER_REACTIVATED, target_type="user",
                   target_key=user.pk, target_label=user.username, source=source)


  def _refuse_a_switch_away_from_open() -> None:
      """The three conditions, in one place, with one operator-readable
      message each.

      A CHECK THAT RUNS ONLY AT STARTUP IS NOT ENOUGH, which is why this
      exists alongside `identity/checks.py`. The image starts with
      `migrate && <server>`, so `manage.py check` runs once per boot;
      flipping the posture on a RUNNING box with `DEBUG=1` or a default
      `SECRET_KEY` would change nothing until the next restart -- which is
      precisely the window in which the operator believes accounts are on.

      `manage.py identity_posture` reaches this same function, so the
      break-glass path is not a way around any of the three.
      """
      if not _active_superusers().exists():
          raise ServiceRefused(
              "This box has no active superuser, so switching away from the open "
              "posture would lock everybody out. Run `manage.py createsuperuser` "
              "first."
          )
      if settings.DEBUG:
          raise ServiceRefused(
              "DEBUG is on. A box with accounts must not render tracebacks -- with "
              "its settings and environment in them -- to any visitor. Set DEBUG=0 "
              "and restart before switching posture."
          )
      if settings.SECRET_KEY == settings.DEV_SECRET_KEY:
          raise ServiceRefused(
              "This box is still using the shipped development signing key. That "
              "key signs every session cookie, and it is published in a public "
              "repository -- anybody who can read it could mint a session for any "
              "account here. Set a real SECRET_KEY and restart before switching "
              "posture."
          )


  def set_posture(actor, *, posture: str | None = None, library_posture: str | None = None,
                  admin_sees_content: bool | None = None,
                  session_idle_minutes: int | None = None,
                  source: str = SOURCE_WEB) -> IdentitySettings:
      """Write the posture row, with one audit event per field changed.

      A SWITCH, NOT A MIGRATION: this writes columns on one row and
      nothing else. It does not create, delete or rewrite any user; it
      does not touch `owner_kind`/`owner_key` on any row (that is
      adoption, and it is a separate, named, audited command).

      Switching TO `open` is refused by none of the three conditions --
      reducing a posture is always allowed, and an operator whose box is
      misconfigured must always be able to make it less strict.
      """
      row = IdentitySettings.get_solo()
      events: list[tuple[str, dict]] = []

      if posture is not None and posture != row.posture:
          if posture not in POSTURES:
              raise ServiceRefused(f"{posture!r} is not a posture on this platform.")
          if posture != POSTURE_OPEN:
              _refuse_a_switch_away_from_open()
          events.append((actions.POSTURE_CHANGED, {"to": posture}))
          row.posture = posture
          if posture == POSTURE_PERSONAL and row.library_posture != LIBRARY_OPEN:
              # THE ONE PLACE A POSTURE BRANCH LIVES, and it lives in a
              # write path on purpose. `personal` renders no
              # library-posture control, so a box arriving there from a
              # locked organisation posture would otherwise hold a lock
              # with no page to lift it. The read paths in
              # `identity/access.py` stay posture-free.
              row.library_posture = LIBRARY_OPEN
              events.append((actions.LIBRARY_POSTURE_CHANGED,
                             {"to": LIBRARY_OPEN, "reason": "posture reduced"}))

      if library_posture is not None and library_posture != row.library_posture:
          row.library_posture = library_posture
          events.append((actions.LIBRARY_POSTURE_CHANGED, {"to": library_posture}))

      if admin_sees_content is not None and admin_sees_content != row.admin_sees_content:
          row.admin_sees_content = admin_sees_content
          events.append((actions.ADMIN_CONTENT_ACCESS_CHANGED, {"to": admin_sees_content}))

      if session_idle_minutes is not None:
          row.session_idle_minutes = max(0, int(session_idle_minutes))

      row.save()
      for action, detail in events:
          audit.record(actor, action, target_type="posture", target_key=row.pk,
                       source=source, **detail)
      return row
  ```

- [ ] **Step 5: Write `identity/checks.py` and register it.**
  ```python
  """Three system checks, run once per boot by `manage.py check`.

  ALL THREE SWALLOW `DatabaseError` AND RETURN NOTHING, and every
  docstring below says so rather than leaving a reader to mistake it for a
  hole: a box mid-migration, or one running `manage.py migrate` against an
  empty database, has no settings row and is not in violation of anything.

  A check that runs only at startup is not enough on its own -- flipping
  the posture on a RUNNING box would change nothing until the next restart
  -- so `identity.services.set_posture` enforces the same three conditions
  at run time. These two are halves of one rule, not two rules.
  """
  from __future__ import annotations

  from django.conf import settings
  from django.core.checks import Error, Warning as CheckWarning
  from django.db import DatabaseError


  def _accounts_on() -> bool | None:
      """Whether accounts are on, or None if the box cannot be asked."""
      try:
          from identity.access import accounts_on
          return accounts_on()
      except DatabaseError:
          return None


  def check_debug_is_off(app_configs, **kwargs):
      """`identity.E001` -- DEBUG is on while the posture is not open.

      `DEBUG=True` renders tracebacks with settings and environment to any
      visitor, which is a disclosure a posture with accounts must not
      permit. Silent when the settings row cannot be read.
      """
      if _accounts_on() is not True or not settings.DEBUG:
          return []
      return [Error(
          "DEBUG is on while this box requires accounts.",
          hint="Set DEBUG=0. A box with accounts must not render tracebacks -- "
               "with its settings and environment in them -- to any visitor.",
          id="identity.E001",
      )]


  def check_secret_key_is_not_the_default(app_configs, **kwargs):
      """`identity.E002` -- a default SECRET_KEY while the posture is not
      open.

      That key signs every session cookie. A box whose signing key is
      published in a public repository has accounts in name only: anybody
      who can read the repository can mint a session for any account on
      it. This check is the reason accounts and a default key cannot
      coexist. Silent when the settings row cannot be read.
      """
      if _accounts_on() is not True or settings.SECRET_KEY != settings.DEV_SECRET_KEY:
          return []
      return [Error(
          "This box requires accounts but is still signing sessions with the "
          "shipped development key.",
          hint="Set a real SECRET_KEY in the environment and restart.",
          id="identity.E002",
      )]


  def check_secure_cookies(app_configs, **kwargs):
      """`identity.W001` -- accounts are on and SECURE_COOKIES is off.

      A WARNING, not an error, because HTTP on a local network is a
      supported posture and forcing Secure cookies on would break login
      there. Silent when the settings row cannot be read.
      """
      if _accounts_on() is not True or settings.SECURE_COOKIES:
          return []
      return [CheckWarning(
          "Accounts are on but session and CSRF cookies are not marked Secure.",
          hint="Set SECURE_COOKIES=1 if this box is served over TLS. Leave it off "
               "for a plain-HTTP local-network box, which is supported.",
          id="identity.W001",
      )]
  ```
  Register them in `identity/apps.py::ready()`:
  ```python
      def ready(self) -> None:
          """Register this column's three system checks.

          Local import so app import stays light (no DB, no HTTP at
          startup), matching every other AppConfig in this tree. Nothing
          here touches the database: the checks themselves read the
          settings row, and they do it when `manage.py check` runs, not
          now.
          """
          from django.core.checks import register

          from identity import checks

          register(checks.check_debug_is_off)
          register(checks.check_secret_key_is_not_the_default)
          register(checks.check_secure_cookies)
  ```

- [ ] **Step 6: Write `identity/tests/test_checks.py`.**
  ```python
  """The three system checks, and their one shared silence.

  Each check is exercised in isolation: the two conditions a case is not
  testing are satisfied by the fixture, so a passing assertion cannot be a
  second condition firing.
  """
  from __future__ import annotations

  import pytest
  from django.db import DatabaseError

  from identity import checks
  from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN
  from identity.tests._helpers import posture

  pytestmark = pytest.mark.django_db

  REAL_KEY = "a-real-key-for-this-test-only"


  @pytest.fixture(autouse=True)
  def _clean(settings):
      settings.DEBUG = False
      settings.SECRET_KEY = REAL_KEY
      settings.SECURE_COOKIES = True


  def _ids(messages):
      return [m.id for m in messages]


  class TestE001:
      def test_it_fires_for_debug_plus_accounts(self, settings):
          settings.DEBUG = True
          with posture(POSTURE_ENTERPRISE):
              assert _ids(checks.check_debug_is_off(None)) == ["identity.E001"]

      def test_it_is_silent_in_the_open_posture(self, settings):
          """`DEBUG=1` on an open box is the ordinary development
          configuration and is not a violation of anything."""
          settings.DEBUG = True
          with posture(POSTURE_OPEN):
              assert checks.check_debug_is_off(None) == []


  class TestE002:
      def test_it_fires_for_the_shipped_key_plus_accounts(self, settings):
          from config.settings import DEV_SECRET_KEY
          settings.SECRET_KEY = DEV_SECRET_KEY
          with posture(POSTURE_ENTERPRISE):
              assert _ids(checks.check_secret_key_is_not_the_default(None)) == [
                  "identity.E002"]

      def test_a_real_key_passes(self):
          with posture(POSTURE_ENTERPRISE):
              assert checks.check_secret_key_is_not_the_default(None) == []


  class TestW001:
      def test_it_warns_not_errors(self, settings):
          settings.SECURE_COOKIES = False
          with posture(POSTURE_ENTERPRISE):
              messages = checks.check_secure_cookies(None)
          assert _ids(messages) == ["identity.W001"]
          assert messages[0].level < 40      # WARNING, not ERROR


  class TestTheSharedSilence:
      @pytest.mark.parametrize("check", [
          checks.check_debug_is_off,
          checks.check_secret_key_is_not_the_default,
          checks.check_secure_cookies,
      ])
      def test_every_check_is_silent_when_the_row_cannot_be_read(self, monkeypatch, check):
          """A box mid-migration has no settings row and is not in
          violation. The swallow is documented in each check's own
          docstring so it is not mistaken for a hole."""
          def boom():
              raise DatabaseError("relation \"identity_identitysettings\" does not exist")
          monkeypatch.setattr("identity.access.accounts_on", boom)
          assert check(None) == []
  ```

- [ ] **Step 7: Write `manage.py identity_posture`** — the break-glass path, reaching the same
  function so it is not a way around the three refusals.
  ```python
  """Read or change this box's posture from a shell.

  THE BREAK-GLASS PATH for the case where the posture page is unreachable
  -- and it reaches `identity.services.set_posture`, exactly as the page
  does, so it is NOT a way around any of that function's three refusals.
  """
  from __future__ import annotations

  from django.core.management.base import BaseCommand, CommandError

  from identity import services
  from identity.contracts.actions import SOURCE_CLI
  from identity.contracts.postures import POSTURES
  from identity.contracts.principals import SERVICE_PRINCIPAL
  from identity.models import IdentitySettings


  class Command(BaseCommand):
      help = "Show this box's posture, or switch it."

      def add_arguments(self, parser):
          parser.add_argument("posture", nargs="?", choices=list(POSTURES),
                              help="The posture to switch to. Omit to print the current one.")
          parser.add_argument("--admin-sees-content", choices=["on", "off"],
                              help="Whether administrators may read other people's content.")

      def handle(self, *args, **options):
          row = IdentitySettings.get_solo()
          target = options["posture"]
          content = options["admin_sees_content"]
          if target is None and content is None:
              self.stdout.write(f"posture: {row.posture}")
              self.stdout.write(f"library posture: {row.library_posture}")
              self.stdout.write(f"administrators read content: "
                                f"{'yes' if row.admin_sees_content else 'no'}")
              return
          try:
              row = services.set_posture(
                  SERVICE_PRINCIPAL,
                  posture=target,
                  admin_sees_content=None if content is None else content == "on",
                  source=SOURCE_CLI,
              )
          except services.ServiceRefused as exc:
              raise CommandError(str(exc)) from exc
          self.stdout.write(self.style.SUCCESS(f"posture: {row.posture}"))
          if target is not None and target != "open":
              # The ordering guidance the adoption command also prints.
              self.stdout.write(
                  "If this box has rows created before accounts existed, run "
                  "`manage.py adopt_open_rows --user <username>` so they belong to "
                  "somebody."
              )
  ```
  Its test, `identity/tests/test_command_identity_posture.py`, asserts: printing without
  arguments does not write; switching writes one audit row; **each of the three refusals is
  reported as a `CommandError` naming the reason** (the break-glass command must not be a way
  around any of them); and switching to `open` succeeds under all three.

- [ ] **Step 8: Run the tests.**
  Run: `.venv/bin/pytest -q identity`
  Expected: PASS
  Run: `DEBUG=1 .venv/bin/python manage.py check`
  Expected: exit 0 (the box is in `open` posture, so E001 is silent — the check firing is
  proven by the unit tests above, and end to end in Task 16's ladder).

- [ ] **Step 9: Docs.** `docs/OPERATIONS.md` — the deactivation-kills-sessions mechanism (so an
  operator does not wonder whether a sweep was forgotten), and the three refusals with their
  exact messages. `docs/DEV.md` — `SECURE_COOKIES` in the environment table, and
  `manage.py identity_posture` in the accounts section written in Task 5.

- [ ] **Step 10: Commit** as
  `feat(identity): T6 — guarded writes, the last-admin guard, three checks, and the posture command`.

---
### Task 7: `identity/routes.py`, `identity/gate.py`, `identity/middleware.py` — the route table and the gate

Every URL name on the box gets a coarse tier, and a middleware enforces it. The tier answers
*may this principal be here at all*; it never answers *may they see this row* — that stays in
the view, through the visibility functions (Task 13).

**A name absent from the table is treated as the strictest tier and logged.** Forgetting to
classify a new route fails closed and loudly, rather than shipping it open.

**Files:**
- Create: `identity/routes.py`, `identity/gate.py`, `identity/middleware.py`
- Create: `identity/tests/test_routes.py`, `identity/tests/test_middleware.py`
- Modify: `config/settings.py` — `MIDDLEWARE`, `LOGIN_URL`, `LOGIN_REDIRECT_URL`,
  `LOGOUT_REDIRECT_URL`
- Modify: `identity/README.md` is written in Task 16; note the classification rule here in a
  module docstring instead

**Interfaces:**
- Consumes: `identity.access` (Task 4), `identity.request` (Task 5).
- Produces:
  ```python
  # identity/routes.py
  PUBLIC = "public"; AUTHENTICATED = "authenticated"; ADMIN = "admin"
  TIERS: tuple[str, str, str]
  ROUTE_RULES: dict[str, str]      # url_name -> tier
  def tier_for(url_name: str, app_names: list[str]) -> str

  # identity/gate.py
  def require_principal(view): ...     # the belt; the middleware is the mechanism
  def require_admin(view): ...

  # identity/middleware.py
  class IdentityGateMiddleware: ...
  ```

**Steps:**

- [ ] **Step 1: Write `identity/routes.py`.** The six classes of the spec's §11.1 map onto
  three coarse tiers; the finer distinction (O vs L vs R) is enforced in the view, because
  those three need the row itself.
  ```python
  """Every URL name on this box, and the coarse tier it answers to.

  THREE TIERS, NOT SIX. The design's six route classes -- P public, A
  authenticated, O owned content, L library content, R operational rows, S
  superuser -- collapse to three HERE because a middleware runs before a
  view has resolved anything: it can answer "may this principal be here at
  all", and it cannot answer "may they see THIS row". So P is PUBLIC; A,
  O, L and R are all AUTHENTICATED at this layer; S is ADMIN. O, L and R
  keep their real rule in the view, through the visibility functions --
  and their route class is documented per name below so the route matrix
  (`identity/tests/test_route_matrix.py`) can assert the finer answer.

  A NAME ABSENT FROM THIS TABLE IS TREATED AS ADMIN -- the strictest tier
  -- and logged. Forgetting to classify a new route fails closed and
  loudly rather than shipping it open, and `test_route_matrix.py` fails
  immediately, naming the route, which is what keeps this table a live
  artefact rather than a document that rots.
  """
  from __future__ import annotations

  PUBLIC = "public"
  AUTHENTICATED = "authenticated"
  ADMIN = "admin"
  TIERS = (PUBLIC, AUTHENTICATED, ADMIN)

  # The finer class each name carries in the design, recorded beside the
  # tier so a reader of this file sees BOTH -- the tier the middleware
  # enforces, and the rule the view still owes.
  #
  #   A -- signed in, no row rule
  #   O -- owned CONTENT: 404 unless owned/shared, or `sees_all_content`
  #   L -- library CONTENT: 404 unless readable
  #   R -- operational ROWS: `is_admin` sees every row; content withheld
  #   S -- superuser
  ROUTE_RULES: dict[str, str] = {
      # --- /chat/ (agents/chat/urls.py) ----------------------------------
      "chat-index": AUTHENTICATED,                     # A
      "chat-start": AUTHENTICATED,                     # A
      "chat-default-install": AUTHENTICATED,           # A
      "chat-conversation": AUTHENTICATED,              # O
      "chat-turn": AUTHENTICATED,                      # O
      "chat-conversation-delete": AUTHENTICATED,       # O
      "chat-turn-status": AUTHENTICATED,               # O

      # --- /rag/ (tools/rag/urls.py) -------------------------------------
      "rag-ask-page": AUTHENTICATED,                   # A
      "rag-ask": AUTHENTICATED,                        # A
      "rag-ask-status": AUTHENTICATED,                 # R
      "rag-search": AUTHENTICATED,                     # A
      "rag-documents": AUTHENTICATED,                  # R
      "rag-document-upload": AUTHENTICATED,            # A
      "rag-document-file": AUTHENTICATED,              # L
      "rag-document-transcript": AUTHENTICATED,        # L
      "rag-document-delete": AUTHENTICATED,            # R
      "rag-document-reingest": AUTHENTICATED,          # R
      "rag-category-rename": ADMIN,                    # S -- taxonomy is library-wide
      "rag-category-delete": ADMIN,                    # S
      "rag-history": AUTHENTICATED,                    # A (rows are content: own only)
      "rag-history-settings": ADMIN,                   # S -- operator policy
      "rag-upload-cap-settings": ADMIN,                # S
      "rag-media-duration-settings": ADMIN,            # S
      "rag-document-pages-settings": ADMIN,            # S
      "rag-retrieval-top-k-settings": ADMIN,           # S
      "rag-retrieval-score-floor-settings": ADMIN,     # S
      "rag-hybrid-search-settings": ADMIN,             # S -- it rebuilds the chunk table

      # --- /vision/ (tools/vision/urls.py; mounted only with the flag) ---
      "vision-create": AUTHENTICATED,                  # A
      "vision-create-operation": AUTHENTICATED,        # A -- the rendering alias
      "vision-gallery": AUTHENTICATED,                 # A (lists own; content)
      "vision-operations": AUTHENTICATED,              # A -- schema, reveals no rows
      "vision-generate": AUTHENTICATED,                # A
      "vision-job-status": AUTHENTICATED,              # O
      "vision-job-delete": AUTHENTICATED,              # O
      "vision-output-file": AUTHENTICATED,             # O -- through output.job
      "vision-input-file": AUTHENTICATED,              # O -- through input.job
      "vision-queue-status": AUTHENTICATED,            # R

      # --- /inference/ (models/registry/urls.py) -------------------------
      # The console READ is S as well as the mutations: it displays
      # endpoints, model identifiers and connection configuration -- an
      # inventory of the box, which is operator information. This closes
      # ADR 0010's standing unauthenticated-mutation gap in full rather
      # than half.
      "inference-console": ADMIN,
      "inference-connection-add": ADMIN,
      "inference-connection-remove": ADMIN,
      "inference-machine-add": ADMIN,
      "inference-role-assign": ADMIN,
      "inference-role-reencode": ADMIN,
      "inference-server-scan": ADMIN,

      # --- /queue/ (models/queue/urls.py) --------------------------------
      "jobs-queue": AUTHENTICATED,                     # R
      "jobs-queue-settings": ADMIN,                    # S -- operator policy
      "jobs-queue-cancel": AUTHENTICATED,              # R

      # --- /setup/ (foundation/setup/urls.py) ----------------------------
      # PUBLIC, deliberately: it explains how to install engines, names no
      # row, no model choice and no document, and it is the page a person
      # needs BEFORE they can log in to a box whose engines are not up.
      "setup-index": PUBLIC,

      # --- /identity/ (new in IA-1) --------------------------------------
      "identity-login": PUBLIC,                        # P
      "identity-logout": AUTHENTICATED,                # A
      "identity-password-change": AUTHENTICATED,       # A
      "identity-password-change-done": AUTHENTICATED,  # A
      "identity-users": ADMIN,                         # S
      "identity-user-create": ADMIN,                   # S
      "identity-user-edit": ADMIN,                     # S
      "identity-settings": ADMIN,                      # S
  }


  def tier_for(url_name: str, app_names) -> str:
      """The tier `url_name` answers to.

      `/admin/` is classified WHOLESALE by its namespace rather than name
      by name: Django registers its own tree, this platform does not own
      those names, and a per-name table would rot the first time Django
      added one. It is also how `/admin/` becomes SUPERUSER-only rather
      than Django's default staff-only, with no `AdminSite` subclass.

      An unknown name is ADMIN. Fail closed, and the caller logs it.
      """
      if app_names and "admin" in app_names:
          return ADMIN
      return ROUTE_RULES.get(url_name, ADMIN)
  ```

- [ ] **Step 2: Write `identity/tests/test_routes.py`** — the table is checked against the
  live resolver here, in a small form; the full cross-product matrix is Task 15.
  ```python
  """`ROUTE_RULES` covers every route this platform owns.

  The route list is DERIVED from the resolver, never typed: a route added
  to any `urls.py` without a classification fails here immediately,
  naming itself.
  """
  from __future__ import annotations

  import pytest
  from django.conf import settings
  from django.urls import get_resolver

  from identity.routes import ADMIN, AUTHENTICATED, PUBLIC, ROUTE_RULES, TIERS, tier_for


  def _named_routes() -> dict[str, list[str]]:
      """Every URL name in the project -> its `app_names`."""
      out: dict[str, list[str]] = {}

      def walk(resolver, namespaces):
          for pattern in resolver.url_patterns:
              sub = getattr(pattern, "url_patterns", None)
              if sub is not None:
                  walk(pattern, namespaces + ([pattern.app_name] if pattern.app_name else []))
              elif pattern.name:
                  out[pattern.name] = namespaces

      walk(get_resolver(), [])
      return out


  class TestCoverage:
      def test_every_route_this_platform_owns_is_classified(self):
          """The direction with the teeth. `/admin/`'s tree is excluded --
          it is classified wholesale by namespace (`tier_for`) -- and
          everything else must be in the table."""
          missing = sorted(
              name for name, app_names in _named_routes().items()
              if "admin" not in app_names and name not in ROUTE_RULES
          )
          assert missing == [], missing

      def test_every_rule_names_a_route_that_exists(self):
          """The direction that catches a rule left behind by a deleted
          route. `/vision/`'s names are skipped when the flag is off --
          `config/urls.py` mounts that tree conditionally.

          The `identity-` skip is TEMPORARY and Task 8 removes it: this
          table classifies all eight `/identity/` names now, in one piece,
          but the mount arrives with the views. Removing the skip is what
          proves the mount happened."""
          derived = set(_named_routes())
          vision_off = "vision" not in settings.FARABUNKER_FEATURES
          stale = sorted(
              name for name in ROUTE_RULES
              if name not in derived
              and not (vision_off and name.startswith("vision-"))
              and not name.startswith("identity-")      # removed in Task 8
          )
          assert stale == [], stale

      def test_the_admin_exclusion_is_not_swallowing_everything(self):
          """Anti-vacuous pin: at least one admin-namespaced name is
          derived, the non-admin set is non-empty, and it contains a known
          name from each mount."""
          routes = _named_routes()
          assert any("admin" in app_names for app_names in routes.values())
          ours = {n for n, a in routes.items() if "admin" not in a}
          assert len(ours) > 40
          for known in ("chat-index", "rag-documents", "inference-console",
                        "jobs-queue", "setup-index", "identity-login"):
              assert known in ours, known


  class TestTierFor:
      def test_every_value_in_the_table_is_a_real_tier(self):
          assert set(ROUTE_RULES.values()) <= set(TIERS)

      def test_an_unclassified_name_fails_closed(self):
          """Forgetting to classify a new route must ship it CLOSED."""
          assert tier_for("a-route-nobody-classified", []) == ADMIN

      def test_the_admin_tree_is_classified_by_namespace(self):
          assert tier_for("index", ["admin"]) == ADMIN

      def test_exactly_two_routes_are_public_and_each_earns_it(self):
          """`setup-index` is the page a person needs BEFORE they can log
          in to a box whose engines are not up; `identity-login` is the
          page they log in ON. Nothing else on this platform is reachable
          without a session, and a third entry here is a hole."""
          assert sorted(n for n, tier in ROUTE_RULES.items() if tier == PUBLIC) == [
              "identity-login", "setup-index"]
          assert tier_for("setup-index", []) == PUBLIC
          assert tier_for("identity-login", []) == PUBLIC

      def test_the_console_read_is_admin_not_merely_its_mutations(self):
          """Closes ADR 0010's standing gap in full rather than half: the
          console displays an inventory of the box, which is operator
          information."""
          assert ROUTE_RULES["inference-console"] == ADMIN

      def test_the_queue_page_is_authenticated_and_its_settings_are_admin(self):
          """A queue ROW is operational and a member sees their own; the
          memory budget and retention policy are the operator's."""
          assert ROUTE_RULES["jobs-queue"] == AUTHENTICATED
          assert ROUTE_RULES["jobs-queue-settings"] == ADMIN
  ```

- [ ] **Step 3: Write `identity/middleware.py`.**
  ```python
  """The gate: may this principal be here at all.

  INSTALLED AFTER `django.contrib.auth.middleware.AuthenticationMiddleware`
  because it needs `request.user`, and therefore AFTER
  `django.middleware.csrf.CsrfViewMiddleware` too. That ordering is NOT
  changed: moving the gate ahead of CSRF would mean an unauthenticated
  cross-site POST was evaluated for AUTHORISATION before it was evaluated
  for FORGERY, which is the wrong order to fail in. The consequence, stated
  rather than discovered: an anonymous POST with no CSRF cookie gets 403
  from CSRF, and one with a valid CSRF cookie but no session reaches this
  middleware and gets 302/401. Both are correct refusals and neither is a
  500.

  `process_view`, not `__call__`, so `request.resolver_match` is already
  populated and the URL name is known without re-resolving it.
  """
  from __future__ import annotations

  import logging

  from django.contrib.auth.views import redirect_to_login
  from django.http import JsonResponse
  from django.urls import reverse

  from identity.access import accounts_on, is_admin
  from identity.models import IdentitySettings
  from identity.request import principal_for_request
  from identity.routes import ADMIN, AUTHENTICATED, ROUTE_RULES, tier_for

  logger = logging.getLogger(__name__)


  def _is_xhr(request) -> bool:
      """The existing polling clients on this box already send this header
      (`tools/rag/templates/rag/ask.html`, the chat poller), so a poll
      gets a JSON 401 it can render rather than a login page it cannot."""
      return request.headers.get("X-Requested-With") == "XMLHttpRequest"


  def _refuse_anonymous(request):
      if _is_xhr(request):
          return JsonResponse(
              {"error": "sign-in required", "login_url": reverse("identity-login")},
              status=401,
          )
      return redirect_to_login(request.get_full_path(), reverse("identity-login"))


  class IdentityGateMiddleware:
      """Coarse-tier enforcement, plus the rolling session window."""

      def __init__(self, get_response):
          self.get_response = get_response

      def __call__(self, request):
          return self.get_response(request)

      def process_view(self, request, view_func, view_args, view_kwargs):
          """Return a response to refuse the request, or None to let it
          through.

          IN OPEN POSTURE THIS RETURNS IMMEDIATELY, before it looks at the
          route, the user, or anything else. That is the first half of
          "an open box never runs a permission query"; `accounts_on()`'s
          own single primary-key read is the query that answers WHICH
          POSTURE, and the box must ask it before it can skip anything
          else.
          """
          if not accounts_on():
              return None

          match = request.resolver_match
          url_name = match.url_name if match else None
          app_names = list(match.app_names) if match else []
          tier = tier_for(url_name or "", app_names)
          if url_name and url_name not in ROUTE_RULES and "admin" not in app_names:
              logger.warning(
                  "identity: route %r is not classified in identity/routes.py; "
                  "treating it as superuser-only", url_name,
              )

          principal = principal_for_request(request)
          if tier in (AUTHENTICATED, ADMIN) and principal.kind == "anonymous":
              return _refuse_anonymous(request)
          if tier == ADMIN and not is_admin(principal):
              # 403 on an admin surface is fine and is used: the EXISTENCE
              # of a model console is not a secret. Row-addressed URLs
              # answer 404 instead, and that rule lives in the views.
              return self._forbidden(request)

          self._roll_the_session(request)
          return None

      def _forbidden(self, request):
          from django.http import HttpResponseForbidden
          if _is_xhr(request):
              return JsonResponse({"error": "administrators only"}, status=403)
          return HttpResponseForbidden(
              "This page is for administrators of this box."
          )

      def _roll_the_session(self, request):
          """A ROLLING idle window: every authenticated request pushes the
          expiry out again. `SESSION_SAVE_EVERY_REQUEST = True` is what
          makes it actually roll. Zero minutes means "expire when the
          browser closes", which is why one column covers both behaviours
          and no `SESSION_EXPIRE_AT_BROWSER_CLOSE` setting exists.
          """
          session = getattr(request, "session", None)
          if session is None or not getattr(request.user, "is_authenticated", False):
              return
          minutes = IdentitySettings.get_solo().session_idle_minutes
          session.set_expiry(minutes * 60 if minutes else 0)
  ```

- [ ] **Step 4: Write `identity/gate.py`** — the belt to the middleware's braces.
  ```python
  """Explicit per-view checks, for the handful of callers that want one.

  THE MIDDLEWARE IS THE MECHANISM; THESE ARE THE BELT. A view reached
  through the URL conf is already gated by `IdentityGateMiddleware`, and
  nothing in IA-1 needs these. They exist for the callers that do not
  arrive through the middleware -- a management command's future HTTP
  counterpart, the MCP edge -- and so a reviewer reading a view can see
  the rule at the view rather than only in a table.
  """
  from __future__ import annotations

  from functools import wraps

  from django.http import HttpResponseForbidden

  from identity.access import is_admin
  from identity.middleware import _refuse_anonymous
  from identity.request import principal_for_request


  def require_principal(view):
      """Refuse an anonymous caller: 302 for a browser, 401 for a poll."""
      @wraps(view)
      def wrapper(request, *args, **kwargs):
          if principal_for_request(request).kind == "anonymous":
              return _refuse_anonymous(request)
          return view(request, *args, **kwargs)
      return wrapper


  def require_admin(view):
      """Refuse anybody who is not an administrator of this box. 403, not
      404: the existence of an admin surface is not a secret."""
      @wraps(view)
      def wrapper(request, *args, **kwargs):
          principal = principal_for_request(request)
          if principal.kind == "anonymous":
              return _refuse_anonymous(request)
          if not is_admin(principal):
              return HttpResponseForbidden("This page is for administrators of this box.")
          return view(request, *args, **kwargs)
      return wrapper
  ```

- [ ] **Step 5: Install the middleware and the login settings** in `config/settings.py`:
  ```python
  MIDDLEWARE = [
      "django.middleware.security.SecurityMiddleware",
      "django.contrib.sessions.middleware.SessionMiddleware",
      "django.middleware.common.CommonMiddleware",
      "django.middleware.csrf.CsrfViewMiddleware",
      "django.contrib.auth.middleware.AuthenticationMiddleware",
      # AFTER AuthenticationMiddleware, because it needs `request.user` --
      # and therefore after CSRF, deliberately (see the module docstring
      # in identity/middleware.py: authorisation must not be evaluated
      # before forgery).
      "identity.middleware.IdentityGateMiddleware",
      "django.contrib.messages.middleware.MessageMiddleware",
      "django.middleware.clickjacking.XFrameOptionsMiddleware",
  ]

  LOGIN_URL = "identity-login"
  LOGIN_REDIRECT_URL = "chat-index"
  LOGOUT_REDIRECT_URL = "identity-login"
  ```

- [ ] **Step 6: Write `identity/tests/test_middleware.py`.** It drives the real client against
  routes that already exist (`setup-index`, `chat-index`, `inference-console`), so it can be
  written **before** Task 8 adds the login page — `reverse("identity-login")` is the one thing
  it cannot resolve yet, so this module is written now and its two redirect assertions are
  added in Task 8. Assertions in this task:
  ```python
  """The gate. Coarse tiers only -- the row rules live in the views.

  KEEPS "vision" IN ANY FARABUNKER_FEATURES OVERRIDE it makes, because it
  calls `reverse()`; without the flag, `/vision/`'s tree is not mounted
  and `reverse` raises.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL
  from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture, sign_in

  pytestmark = pytest.mark.django_db

  XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


  @pytest.fixture(autouse=True)
  def _sweep():
      seed_sweep_posture()


  class TestOpenPosture:
      def test_every_route_answers_exactly_as_it_does_today(self, client):
          """The household box's experience is byte-identical: no login,
          no redirect, no 403, on a page and on an admin surface alike."""
          with posture(POSTURE_OPEN):
              assert client.get(reverse("chat-index")).status_code == 200
              assert client.get(reverse("inference-console")).status_code == 200
              assert client.get(reverse("setup-index")).status_code == 200

      def test_the_gate_returns_before_it_reads_the_route_table(self, client, monkeypatch):
          """Structural, not incidental: `accounts_on()` is tested first
          and the middleware returns, so nothing about the route table or
          the user is consulted in the posture that is the default."""
          import identity.middleware as mw
          monkeypatch.setattr(mw, "tier_for",
                              lambda *a, **k: pytest.fail("route table consulted"))
          with posture(POSTURE_OPEN):
              assert client.get(reverse("chat-index")).status_code == 200


  class TestAnonymous:
      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_an_html_get_is_redirected_with_a_next_parameter(self, client, name):
          with posture(name):
              response = client.get(reverse("chat-index"))
          assert response.status_code == 302
          assert "next=" in response["Location"]

      @pytest.mark.parametrize("name", [POSTURE_PERSONAL, POSTURE_ENTERPRISE])
      def test_a_poll_gets_a_json_401_it_can_render(self, client, name):
          """The existing pollers already send this header, so they get a
          body with a message and a login URL rather than a login page
          they would inject into a card."""
          with posture(name):
              response = client.get(reverse("jobs-queue"), **XHR)
          assert response.status_code == 401
          assert response.json()["login_url"]

      def test_the_setup_page_stays_public_in_every_posture(self, client):
          """It is the page a person needs BEFORE they can log in to a box
          whose engines are not up."""
          for name in (POSTURE_OPEN, POSTURE_PERSONAL, POSTURE_ENTERPRISE):
              with posture(name):
                  assert client.get(reverse("setup-index")).status_code == 200


  class TestTiers:
      def test_a_member_reaches_an_authenticated_route_and_not_an_admin_one(self, client):
          member = make_user()
          with posture(POSTURE_PERSONAL):
              sign_in(client, member)
              assert client.get(reverse("chat-index")).status_code == 200
              assert client.get(reverse("inference-console")).status_code == 403

      def test_403_on_an_admin_surface_not_404(self, client):
          """The EXISTENCE of a model console is not a secret. 404 is
          reserved for row-addressed URLs, where a 403 would confirm the
          row exists."""
          with posture(POSTURE_PERSONAL):
              sign_in(client, make_user())
              assert client.get(reverse("jobs-queue-settings")).status_code in (403, 405)

      def test_an_admin_reaches_every_admin_surface_with_the_content_setting_off(self, client):
          """Administering is not reading: the console, the queue settings
          and the posture page are all reachable with the toggle off."""
          admin = make_admin()
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              sign_in(client, admin)
              assert client.get(reverse("inference-console")).status_code == 200

      def test_an_unclassified_route_is_refused_to_a_member(self, client, monkeypatch):
          """Fail closed. A route added without a classification must not
          ship open."""
          import identity.routes as routes
          monkeypatch.delitem(routes.ROUTE_RULES, "chat-index")
          with posture(POSTURE_PERSONAL):
              sign_in(client, make_user())
              assert client.get(reverse("chat-index")).status_code == 403

      def test_the_django_admin_is_superuser_only_not_staff_only(self, client):
          """Classified wholesale by namespace, with no `AdminSite`
          subclass. A staff-but-not-superuser account is refused."""
          staff = make_user(is_staff=True)
          with posture(POSTURE_PERSONAL):
              sign_in(client, staff)
              assert client.get("/admin/").status_code == 403


  class TestTheRollingWindow:
      def test_an_authenticated_request_pushes_the_expiry_out(self, client):
          from identity.models import IdentitySettings
          member = make_user()
          with posture(POSTURE_PERSONAL) as row:
              row.session_idle_minutes = 30
              row.save()
              sign_in(client, member)
              client.get(reverse("chat-index"))
              assert client.session.get_expiry_age() <= 30 * 60
              assert client.session.get_expiry_age() > 29 * 60

      def test_zero_minutes_means_expire_when_the_browser_closes(self, client):
          member = make_user()
          with posture(POSTURE_PERSONAL) as row:
              row.session_idle_minutes = 0
              row.save()
              sign_in(client, member)
              client.get(reverse("chat-index"))
              assert client.session.get_expire_at_browser_close() is True
  ```

- [ ] **Step 7: Run it, and mark the three assertions Task 8 completes.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity`
  Expected: PASS, except three assertions that need routes Task 8 mounts. `ROUTE_RULES`
  classifies the eight `/identity/` names in this task — the table is written once, in full,
  rather than half now and half later — but `/identity/` is not mounted until Task 8, so:
  - `test_every_rule_names_a_route_that_exists` (Step 2) skips names starting with
    `identity-` in the same breath it skips `vision-` names when the flag is off. **Task 8's
    Step 6 removes that skip**, which is what proves the mount actually happened;
  - the two `test_middleware.py` assertions that call `reverse("identity-login")` are commented
    out with a `# Task 8` marker and uncommented in Task 8's Step 6.
  Nothing else is deferred: the middleware's tier enforcement is fully testable now against
  routes that already exist.

- [ ] **Step 8: Run the whole suite in the open posture.** This is the task that could silently
  change every page on the box, so the regression gate runs here rather than at the end.
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q`
  Expected: PASS, unchanged from before the task — the middleware returns before doing anything
  in `open` posture, which is the default.

- [ ] **Step 9: Commit** as
  `feat(identity): T7 — the route table, the gate middleware, and the rolling session window`.

---

### Task 8: login, logout and change password — Django's views, this platform's shell

Django ships all three. They are subclassed only to write the audit event and to render this
platform's shell template. **No password-reset command is written** — Django ships
`manage.py changepassword`, and there is no email server on this box, so the login page says so
in one line.

**Files:**
- Create: `identity/views.py`, `identity/urls.py`,
  `identity/templates/identity/login.html`, `identity/templates/identity/password_change.html`,
  `identity/templates/identity/password_change_done.html`
- Create: `identity/tests/test_login.py`
- Modify: `config/urls.py` — mount `/identity/`
- Modify: `identity/tests/test_middleware.py` — uncomment the two Task-8 assertions

**Interfaces:**
- Consumes: `identity.audit` (Task 4), `identity.request` (Task 5), `identity.routes` (Task 7).
- Produces: the URL names `identity-login`, `identity-logout`, `identity-password-change`,
  `identity-password-change-done`.

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_login.py`:
  ```python
  """Signing in, signing out, and changing your own password.

  Django's own views do the work. What is tested here is what this
  platform adds: the audit rows, the `?next=` round trip, the refusal for
  a deactivated account, and the one line of copy that tells a person
  there is no password-reset email on this box.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from identity.contracts import actions
  from identity.contracts.postures import POSTURE_PERSONAL
  from identity.models import AuditEvent
  from identity.tests._helpers import make_user, posture, seed_sweep_posture, sign_in

  pytestmark = pytest.mark.django_db

  PASSWORD = "not-a-real-password"


  @pytest.fixture(autouse=True)
  def _sweep():
      seed_sweep_posture()


  class TestLogin:
      def test_the_page_is_reachable_without_a_session(self, client):
          with posture(POSTURE_PERSONAL):
              assert client.get(reverse("identity-login")).status_code == 200

      def test_a_correct_password_signs_in_and_audits(self, client):
          user = make_user(username="ann", password=PASSWORD)
          with posture(POSTURE_PERSONAL):
              response = client.post(reverse("identity-login"),
                                     {"username": "ann", "password": PASSWORD})
          assert response.status_code == 302
          assert AuditEvent.objects.filter(action=actions.LOGIN,
                                           target_key=str(user.pk)).count() == 1

      def test_a_wrong_password_audits_the_attempt_with_the_username_only(self, client):
          """It exists so a brute-force attempt is visible. It records the
          SUBMITTED username and never the password, never a hash of it,
          and never the request body."""
          make_user(username="ann", password=PASSWORD)
          with posture(POSTURE_PERSONAL):
              response = client.post(reverse("identity-login"),
                                     {"username": "ann", "password": "wrong-value-here"})
          assert response.status_code == 200          # re-rendered with an error
          row = AuditEvent.objects.get(action=actions.LOGIN_FAILED)
          assert row.target_label == "ann"
          assert "wrong-value-here" not in f"{row.detail}{row.target_label}"

      def test_a_deactivated_account_cannot_sign_in(self, client):
          """Django's `ModelBackend` refuses an inactive user, so no code
          here does -- this asserts the mechanism is really in force."""
          make_user(username="ann", password=PASSWORD, is_active=False)
          with posture(POSTURE_PERSONAL):
              response = client.post(reverse("identity-login"),
                                     {"username": "ann", "password": PASSWORD})
          assert response.status_code == 200
          assert AuditEvent.objects.filter(action=actions.LOGIN).count() == 0

      def test_next_survives_the_round_trip(self, client):
          """The gate redirects with `?next=`; signing in must land the
          person where they were going, not on a default page."""
          make_user(username="ann", password=PASSWORD)
          target = reverse("rag-documents")
          with posture(POSTURE_PERSONAL):
              response = client.post(
                  f"{reverse('identity-login')}?next={target}",
                  {"username": "ann", "password": PASSWORD},
              )
          assert response["Location"] == target

      def test_the_page_says_there_is_no_reset_email_on_this_box(self, client):
          """One line of honest copy instead of a reset flow that cannot
          work: there is no email server on this box, so a forgotten
          password is reset by an administrator."""
          with posture(POSTURE_PERSONAL):
              body = client.get(reverse("identity-login")).content.decode()
          assert "administrator" in body.lower()


  class TestLogout:
      def test_it_is_post_only_and_audits(self, client):
          """Django 5's `LogoutView` is POST-only, which is correct: a GET
          logout is a URL anybody can put in an image tag."""
          user = make_user()
          with posture(POSTURE_PERSONAL):
              sign_in(client, user)
              assert client.get(reverse("identity-logout")).status_code == 405
              assert client.post(reverse("identity-logout")).status_code == 302
          assert AuditEvent.objects.filter(action=actions.LOGOUT).count() == 1


  class TestPasswordChange:
      def test_a_person_changes_their_own_and_it_audits(self, client):
          user = make_user(username="ann", password=PASSWORD)
          with posture(POSTURE_PERSONAL):
              sign_in(client, user)
              response = client.post(reverse("identity-password-change"), {
                  "old_password": PASSWORD,
                  "new_password1": "a-different-value-9",
                  "new_password2": "a-different-value-9",
              })
          assert response.status_code == 302
          user.refresh_from_db()
          assert user.check_password("a-different-value-9")
          assert AuditEvent.objects.filter(action=actions.PASSWORD_CHANGED).count() == 1

      def test_the_page_needs_a_session(self, client):
          with posture(POSTURE_PERSONAL):
              assert client.get(reverse("identity-password-change")).status_code == 302
  ```

- [ ] **Step 2: Run it to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_login.py`
  Expected: FAIL — `NoReverseMatch: 'identity-login' is not a valid view function or pattern name`

- [ ] **Step 3: Write `identity/views.py`** (the three auth views; the two admin pages join it
  in Task 9).
  ```python
  """The identity pages.

  Server-rendered, zero JS, extending `foundation/templates/_shell.html`,
  matching every other page on the box.

  DJANGO'S OWN VIEWS DO THE WORK. `LoginView`, `LogoutView` and
  `PasswordChangeView` are subclassed for exactly two reasons: to write
  the audit event, and to render this platform's templates. Password
  hashing, validation, session invalidation on password change and the
  `is_active` refusal are all Django's, maintained by somebody else --
  this phase adds only what Django lacks.
  """
  from __future__ import annotations

  from django.contrib.auth import views as auth_views
  from django.urls import reverse_lazy

  from identity import audit
  from identity.contracts import actions
  from identity.contracts.principals import ANONYMOUS, Principal
  from identity.request import principal_for_request


  class LoginView(auth_views.LoginView):
      """Sign in, and record both outcomes.

      `redirect_authenticated_user` is deliberately LEFT OFF (Django's
      default): turning it on makes the login page a redirect oracle that
      leaks whether a session is valid, and this page has nothing to gain
      from it.
      """

      template_name = "identity/login.html"

      def form_valid(self, form):
          response = super().form_valid(form)
          user = form.get_user()
          audit.record(Principal("user", str(user.pk)), actions.LOGIN,
                       target_type="user", target_key=user.pk, target_label=user.username)
          return response

      def form_invalid(self, form):
          # The SUBMITTED username, and nothing else. Never the password,
          # never a hash of it, never the request body. This row exists so
          # a brute-force attempt is visible in the log; login lockout is
          # deferred and this is its hook.
          submitted = (form.data.get("username") or "")[:255]
          audit.record(ANONYMOUS, actions.LOGIN_FAILED, target_label=submitted)
          return super().form_invalid(form)


  class LogoutView(auth_views.LogoutView):
      """POST-only in the installed Django, which is correct: a GET logout
      is a URL anybody can put in an image tag."""

      def post(self, request, *args, **kwargs):
          principal = principal_for_request(request)
          username = getattr(request.user, "username", "")
          response = super().post(request, *args, **kwargs)
          if principal.kind == "user":
              audit.record(principal, actions.LOGOUT, target_type="user",
                           target_key=principal.key, target_label=username)
          return response


  class PasswordChangeView(auth_views.PasswordChangeView):
      """A person's own password.

      Django invalidates every other session for this account on success
      (`AbstractBaseUser.get_session_auth_hash`) and keeps this one
      through `update_session_auth_hash`. Nothing here duplicates that.
      """

      template_name = "identity/password_change.html"
      success_url = reverse_lazy("identity-password-change-done")

      def form_valid(self, form):
          response = super().form_valid(form)
          user = self.request.user
          audit.record(Principal("user", str(user.pk)), actions.PASSWORD_CHANGED,
                       target_type="user", target_key=user.pk, target_label=user.username)
          return response


  class PasswordChangeDoneView(auth_views.PasswordChangeDoneView):
      template_name = "identity/password_change_done.html"
  ```

- [ ] **Step 4: Write `identity/urls.py` and mount it.**
  ```python
  """URL routes for the identity pages, mounted at /identity/ (config/urls.py).

  UNGATED BY ANY FEATURE FLAG, exactly like `/chat/` and `/rag/`: accounts
  are not optional machinery, and a third `FARABUNKER_FEATURES` token
  would leave this whole surface untested in one of the two supported
  suite states.
  """
  from django.urls import path

  from identity.views import (
      LoginView, LogoutView, PasswordChangeDoneView, PasswordChangeView,
  )

  urlpatterns = [
      path("login/", LoginView.as_view(), name="identity-login"),
      path("logout/", LogoutView.as_view(), name="identity-logout"),
      path("password/", PasswordChangeView.as_view(), name="identity-password-change"),
      path("password/done/", PasswordChangeDoneView.as_view(),
           name="identity-password-change-done"),
  ]
  ```
  In `config/urls.py`, add `path("identity/", include("identity.urls"))` beside the other five
  mounts, with a one-line comment saying it is ungated for the same reason `/chat/` is.

- [ ] **Step 5: Write the three templates.** Each extends `foundation/templates/_shell.html`,
  uses the shell's existing form styling, and carries `{% csrf_token %}`. `login.html`'s body
  is a username field, a password field, a submit button, the `?next=` hidden field Django's
  form supplies, and one paragraph:
  ```html
  <p class="muted">
    There is no password-reset email on this box. If you have forgotten your
    password, ask an administrator to reset it for you.
  </p>
  ```
  `login.html` also overrides `{% block nav %}{% endblock %}` — the shell's nav links to pages
  a signed-out visitor cannot reach, and rendering a row of links that all redirect back here
  is worse than rendering none.

- [ ] **Step 6: Close the three deferrals Task 7 left.**
  - Remove the `identity-` skip from `identity/tests/test_routes.py::
    test_every_rule_names_a_route_that_exists`. It must now hold for all eight `/identity/`
    names — that removal is what proves the mount happened, rather than a passing test that
    was skipping the thing it claimed to check.
  - Uncomment the two `# Task 8` assertions in `identity/tests/test_middleware.py` and confirm
    the `?next=` redirect target really is the login URL.

- [ ] **Step 7: Run.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity`
  Expected: PASS

- [ ] **Step 8: Commit** as
  `feat(identity): T8 — login, logout and change password on Django's own views`.

---

### Task 9: the users page, the posture page, the nav link, and `identity/admin.py`

Two pages and one break-glass surface. The posture page carries the **administrator-content
toggle**, present in `personal` as well as `enterprise` because the administer/read split has
no posture branch. The users page hides the superuser toggle in `personal` and says in one
sentence that every account there is an administrator.

`identity/admin.py` re-registers the user model — Django's `AdminSite.register` silently
ignores a swapped-out model, so `/admin/` would otherwise show Groups only — but **not** with
the stock `UserAdmin`, which would open two holes at once.

**Files:**
- Create: `identity/forms.py`, `identity/admin.py`, `identity/context_processors.py`,
  `identity/templates/identity/users.html`, `identity/templates/identity/settings.html`
- Modify: `identity/views.py`, `identity/urls.py`
- Modify: `config/settings.py` — register the context processor
- Modify: `foundation/templates/_shell.html` — the nav link
- Create: `identity/tests/test_users_page.py`, `identity/tests/test_settings_page.py`,
  `identity/tests/test_admin.py`

**Interfaces:**
- Consumes: `identity.services` (Task 6), `identity.access` (Task 4).
- Produces: `identity-users`, `identity-user-create`, `identity-user-edit`,
  `identity-settings`; the context keys `identity_posture` and `identity_is_admin`.

**Steps:**

- [ ] **Step 1: Write the failing tests.** `identity/tests/test_users_page.py`:
  ```python
  """The users page: list, create, rename, deactivate/reactivate, toggle
  superuser, reset password.

  EVERY MUTATION GOES THROUGH `identity.services`, so every guard and
  every audit row applies here exactly as it does on the command line.
  The assertions below check the page's own behaviour; the guards
  themselves are tested in `test_services.py`.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from identity.contracts import actions
  from identity.contracts.postures import POSTURE_ENTERPRISE, POSTURE_PERSONAL
  from identity.models import AuditEvent
  from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture, sign_in

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _sweep():
      seed_sweep_posture()


  class TestAccess:
      def test_a_member_gets_403_and_an_admin_gets_200(self, client):
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_user())
              assert client.get(reverse("identity-users")).status_code == 403
          client.logout()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_admin())
              assert client.get(reverse("identity-users")).status_code == 200


  class TestPersonalPosture:
      def test_it_hides_the_superuser_toggle_and_says_why(self, client):
          """In `personal` every account IS an administrator -- the
          owner's decision stated exactly -- so the page does not offer a
          choice it does not have, and says so in one sentence."""
          with posture(POSTURE_PERSONAL):
              sign_in(client, make_admin())
              body = client.get(reverse("identity-users")).content.decode()
          assert "administrator" in body.lower()
          assert 'name="is_superuser"' not in body

      def test_creating_an_account_makes_it_a_superuser(self, client):
          with posture(POSTURE_PERSONAL):
              sign_in(client, make_admin())
              client.post(reverse("identity-user-create"),
                          {"username": "ann", "password": "a-real-enough-value"})
          from django.contrib.auth import get_user_model
          assert get_user_model().objects.get(username="ann").is_superuser is True

      def test_flipping_to_enterprise_demotes_nobody(self, client):
          """It starts OFFERING the choice; the first thing an
          administrator then does is demote the accounts that should be
          members."""
          with posture(POSTURE_PERSONAL):
              sign_in(client, make_admin())
              client.post(reverse("identity-user-create"),
                          {"username": "ann", "password": "a-real-enough-value"})
          from django.contrib.auth import get_user_model
          with posture(POSTURE_ENTERPRISE):
              assert get_user_model().objects.get(username="ann").is_superuser is True


  class TestEnterprisePosture:
      def test_creating_a_member_does_not_make_them_an_admin(self, client):
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_admin())
              client.post(reverse("identity-user-create"),
                          {"username": "ann", "password": "a-real-enough-value"})
          from django.contrib.auth import get_user_model
          assert get_user_model().objects.get(username="ann").is_superuser is False


  class TestMutations:
      def test_deactivation_reports_the_owned_row_counts_on_the_page(self, client):
          """The operator is told what they now need to reassign, at the
          moment the decision is made -- not in a document they would have
          to remember to read."""
          admin, member = make_admin(), make_user()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              response = client.post(reverse("identity-user-edit", args=[member.pk]),
                                     {"action": "deactivate"}, follow=True)
          assert response.status_code == 200
          assert "reassign_owner" in response.content.decode()

      def test_demoting_the_last_admin_is_refused_with_the_pages_own_message(self, client):
          admin = make_admin()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              response = client.post(reverse("identity-user-edit", args=[admin.pk]),
                                     {"action": "demote"}, follow=True)
          assert "last active administrator" in response.content.decode()
          admin.refresh_from_db()
          assert admin.is_superuser is True

      def test_resetting_a_password_audits_a_reset_not_a_change(self, client):
          admin, member = make_admin(), make_user()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              client.post(reverse("identity-user-edit", args=[member.pk]),
                          {"action": "set_password", "password": "another-real-value"})
          member.refresh_from_db()
          assert member.check_password("another-real-value")
          assert AuditEvent.objects.filter(action=actions.PASSWORD_RESET).exists()
          assert not AuditEvent.objects.filter(action=actions.PASSWORD_CHANGED).exists()

      def test_an_unknown_user_id_is_404_not_500(self, client):
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_admin())
              assert client.post(reverse("identity-user-edit", args=[99999999]),
                                 {"action": "demote"}).status_code == 404

      def test_an_unknown_action_is_a_400_not_a_silent_no_op(self, client):
          """A form that quietly did nothing would read as success."""
          member = make_user()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_admin())
              assert client.post(reverse("identity-user-edit", args=[member.pk]),
                                 {"action": "explode"}).status_code == 400
  ```
  `identity/tests/test_settings_page.py`:
  ```python
  """The posture page -- posture, library posture, session window, and the
  administrator-content toggle.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from identity.contracts import actions
  from identity.contracts.postures import (
      POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
  )
  from identity.models import AuditEvent, IdentitySettings
  from identity.tests._helpers import make_admin, make_user, posture, seed_sweep_posture, sign_in

  pytestmark = pytest.mark.django_db

  REAL_KEY = "a-real-key-for-this-test-only"


  @pytest.fixture(autouse=True)
  def _clean(settings):
      seed_sweep_posture()
      settings.DEBUG = False
      settings.SECRET_KEY = REAL_KEY


  class TestAccess:
      def test_it_is_reachable_in_the_open_posture_by_anybody(self, client):
          """Somebody has to be able to turn accounts ON, and in the open
          posture the box's only principal is an administrator."""
          with posture(POSTURE_OPEN):
              assert client.get(reverse("identity-settings")).status_code == 200

      def test_a_member_is_refused(self, client):
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_user())
              assert client.get(reverse("identity-settings")).status_code == 403


  class TestTheContentToggle:
      def test_it_is_present_in_personal_as_well_as_enterprise(self, client):
          """The administer/read split has NO posture branch, so the
          control that governs it appears in both."""
          for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
              with posture(name):
                  sign_in(client, make_admin())
                  body = client.get(reverse("identity-settings")).content.decode()
              assert "admin_sees_content" in body
              client.logout()

      def test_it_defaults_off_and_the_page_says_what_off_means(self, client):
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_admin())
              body = client.get(reverse("identity-settings")).content.decode()
          assert IdentitySettings.get_solo().admin_sees_content is False
          assert "conversation" in body.lower()

      def test_turning_it_on_takes_effect_on_the_next_request_with_one_audit_row(self, client):
          """Read per request through `get_solo()`, so no restart and no
          re-login."""
          admin = make_admin()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              client.post(reverse("identity-settings"),
                          {"posture": POSTURE_ENTERPRISE, "admin_sees_content": "on",
                           "library_posture": "open", "session_idle_minutes": "720"})
              assert IdentitySettings.get_solo().admin_sees_content is True
          assert AuditEvent.objects.filter(
              action=actions.ADMIN_CONTENT_ACCESS_CHANGED).count() == 1


  class TestTheThreeRefusals:
      def test_switching_with_no_admin_re_renders_with_the_reason(self, client):
          with posture(POSTURE_OPEN):
              response = client.post(reverse("identity-settings"),
                                     {"posture": POSTURE_PERSONAL,
                                      "library_posture": "open",
                                      "session_idle_minutes": "720"},
                                     follow=True)
          assert "superuser" in response.content.decode().lower()
          assert IdentitySettings.get_solo().posture == POSTURE_OPEN

      def test_switching_with_debug_on_re_renders_with_the_reason(self, client, settings):
          make_admin()
          settings.DEBUG = True
          with posture(POSTURE_OPEN):
              response = client.post(reverse("identity-settings"),
                                     {"posture": POSTURE_PERSONAL,
                                      "library_posture": "open",
                                      "session_idle_minutes": "720"},
                                     follow=True)
          assert "debug" in response.content.decode().lower()

      def test_switching_with_the_default_key_re_renders_with_the_reason(self, client, settings):
          from config.settings import DEV_SECRET_KEY
          make_admin()
          settings.SECRET_KEY = DEV_SECRET_KEY
          with posture(POSTURE_OPEN):
              response = client.post(reverse("identity-settings"),
                                     {"posture": POSTURE_PERSONAL,
                                      "library_posture": "open",
                                      "session_idle_minutes": "720"},
                                     follow=True)
          assert "key" in response.content.decode().lower()

      def test_a_refusal_is_never_a_500(self, client):
          with posture(POSTURE_OPEN):
              response = client.post(reverse("identity-settings"),
                                     {"posture": POSTURE_PERSONAL,
                                      "library_posture": "open",
                                      "session_idle_minutes": "720"})
          assert response.status_code in (200, 302)
          assert "Traceback" not in response.content.decode(errors="replace")
  ```
  `identity/tests/test_admin.py` — the three pins the spec names:
  ```python
  """`/admin/` after the user-model swap.

  Django's `AdminSite.register` silently ignores a swapped-out model, so
  `django.contrib.auth.admin`'s registration became inert and `/admin/`
  would show Groups only. This re-registers the user model -- but NOT with
  the stock `UserAdmin`, which would open two holes at once: its
  `save_model` calls `obj.save()` directly, bypassing the last-admin
  guard, and its fieldsets expose the `Permission` catalogue, which is a
  named non-goal.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse

  from identity.contracts import actions
  from identity.contracts.postures import POSTURE_ENTERPRISE
  from identity.models import AuditEvent
  from identity.tests._helpers import make_admin, make_user, posture, sign_in

  pytestmark = pytest.mark.django_db


  def _admin_form(user, **overrides) -> dict:
      """A COMPLETE change-form body.

      `UserAdmin`'s fieldsets include `date_joined` and `last_login`, and
      Django renders each as an `AdminSplitDateTime` -- two inputs,
      `<name>_0` (date) and `<name>_1` (time). `date_joined` is REQUIRED,
      so a body that omits it re-renders the form with a validation error
      and `save_model` is never called -- which would make every
      assertion below pass by never reaching the code they are about.

      Checkboxes are omitted rather than sent as "off": Django reads an
      absent checkbox as False, and sending the string "off" would be
      read as TRUE.
      """
      fields = {
          "username": user.username,
          "first_name": user.first_name,
          "last_name": user.last_name,
          "email": user.email,
          "date_joined_0": user.date_joined.strftime("%Y-%m-%d"),
          "date_joined_1": user.date_joined.strftime("%H:%M:%S"),
      }
      if user.last_login:
          fields["last_login_0"] = user.last_login.strftime("%Y-%m-%d")
          fields["last_login_1"] = user.last_login.strftime("%H:%M:%S")
      fields.update(overrides)
      return fields


  class TestBreakGlassGoesThroughTheSameGuards:
      def test_demoting_the_last_superuser_through_the_form_is_refused(self, client):
          """The same message the page gives. An admin form that could
          reach a protected column without its guard is a guard with a
          second door."""
          admin = make_admin()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              response = client.post(
                  reverse("admin:identity_user_change", args=[admin.pk]),
                  _admin_form(admin, is_active="on"), follow=True,
              )
          admin.refresh_from_db()
          assert admin.is_superuser is True
          assert "last active administrator" in response.content.decode()

      def test_deactivating_through_the_form_writes_the_audit_row(self, client):
          admin, member = make_admin(), make_user()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              client.post(reverse("admin:identity_user_change", args=[member.pk]),
                          _admin_form(member), follow=True)
          member.refresh_from_db()
          assert member.is_active is False
          assert AuditEvent.objects.filter(action=actions.USER_DEACTIVATED).exists()


  class TestPermissionsAreUnreachable:
      def test_user_permissions_is_in_neither_the_fieldsets_nor_the_form(self, client):
          """Django's per-user permission catalogue is a second grant
          mechanism beside the entitlements IA-2 adds -- a named
          non-goal."""
          from identity.admin import IdentityUserAdmin
          admin = make_admin()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, admin)
              body = client.get(
                  reverse("admin:identity_user_change", args=[admin.pk])).content.decode()
          assert "user_permissions" not in body
          flattened = str(IdentityUserAdmin.fieldsets)
          assert "user_permissions" not in flattened

      def test_the_group_admin_is_left_exactly_as_django_ships_it(self):
          """This platform uses groups for MEMBERSHIP only and never reads
          `Group.permissions`, so there is nothing there to hide and
          nothing gained by subclassing it."""
          from django.contrib import admin as django_admin
          from django.contrib.auth.models import Group
          assert type(django_admin.site._registry[Group]).__name__ == "GroupAdmin"
  ```

- [ ] **Step 2: Run to verify they fail.**
  Run: `.venv/bin/pytest -q identity/tests/test_users_page.py identity/tests/test_settings_page.py identity/tests/test_admin.py`
  Expected: FAIL — `NoReverseMatch` for `identity-users`.

- [ ] **Step 3: Write `identity/forms.py`** — two plain `forms.Form`s (not `ModelForm`s: every
  write goes through `identity.services`, and a `ModelForm` would offer a `save()` that does
  not).
  ```python
  """The two identity forms.

  PLAIN `Form`, NOT `ModelForm`, deliberately: every write on this column
  goes through `identity.services`, where the last-admin guard, the three
  posture refusals and the audit rows live. A `ModelForm` would carry a
  `save()` that reaches the model directly -- a second door to exactly the
  writes that have guards.
  """
  from __future__ import annotations

  from django import forms
  from django.contrib.auth.password_validation import validate_password

  from identity.contracts.postures import LIBRARY_CHOICES, POSTURE_CHOICES


  class UserCreateForm(forms.Form):
      username = forms.CharField(max_length=150)
      password = forms.CharField(widget=forms.PasswordInput)
      # Rendered only in the organisation posture: in `personal` every
      # account is an administrator, so the page offers no choice.
      is_superuser = forms.BooleanField(required=False)

      def clean_password(self):
          """Django's own validators, not ours -- length, commonness,
          similarity to the username, all-numeric."""
          value = self.cleaned_data["password"]
          validate_password(value)
          return value


  class PostureForm(forms.Form):
      posture = forms.ChoiceField(choices=POSTURE_CHOICES)
      library_posture = forms.ChoiceField(choices=LIBRARY_CHOICES)
      admin_sees_content = forms.BooleanField(required=False)
      session_idle_minutes = forms.IntegerField(min_value=0, max_value=60 * 24 * 30)
  ```

- [ ] **Step 4: Add the two page views to `identity/views.py`** and their routes to
  `identity/urls.py`. Both are decorated `@require_admin` (Task 7) as a belt beside the
  middleware's braces. `users` renders `identity.access.posture()` into the template so it can
  hide the superuser control and print the `personal` sentence; `user_edit` dispatches on a
  single `action` field (`deactivate`, `reactivate`, `promote`, `demote`, `set_password`,
  `rename`), calls the matching `identity.services` function, catches `ServiceRefused` and
  re-renders with `messages.error(...)`, and answers **400** for an unknown action and **404**
  for an unknown user id. `settings_page` binds `PostureForm`, calls `set_posture`, and on
  `ServiceRefused` re-renders the page with the refusal message — never a 500, never a silent
  no-op.

- [ ] **Step 5: Write `identity/admin.py`.**
  ```python
  """The break-glass user surface -- routed through the same guards the
  pages use.

  THE ONLY `admin.py` IN THE REPOSITORY. `/admin/` stays what ADR 0015
  already calls it: a break-glass tool, not a product surface. Nothing new
  is registered here beyond the user model, and `AuditEvent` is
  deliberately NOT registered -- an append-only table with a delete button
  is not append-only.
  """
  from __future__ import annotations

  from django.contrib import admin
  from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

  from identity import services
  from identity.contracts.actions import SOURCE_ADMIN
  from identity.models import User
  from identity.request import principal_for_request


  @admin.register(User)
  class IdentityUserAdmin(DjangoUserAdmin):
      """Break-glass, routed through the same guards the pages use.

      `save_model` DELEGATES: a change to `is_superuser` goes through
      `identity.services.set_superuser` and a change to `is_active`
      through `deactivate_user`/`reactivate_user`, so the last-admin
      guard, its `select_for_update`, and the audit write all apply here
      exactly as they do on the users page. Everything else falls through
      to `super().save_model`. An admin form that could reach a protected
      column without its guard is a guard with a second door.

      `user_permissions` is dropped from `fieldsets` and from
      `filter_horizontal`, and `readonly_fields` pins it, so Django's
      per-user permission catalogue is not reachable from this surface --
      a named non-goal, because it is model-level and would be a second
      grant mechanism beside entitlements. `auth.Group`'s own admin is
      left exactly as Django ships it: this platform uses groups for
      MEMBERSHIP only and never reads `Group.permissions`, so there is
      nothing there to hide and nothing gained by subclassing it.
      """

      # ONE MECHANISM, not two. `fieldsets` and `add_fieldsets` are
      # declared in full here and neither names `user_permissions`, so
      # the field is not on the form at all -- there is nothing for a
      # `readonly_fields` entry to protect, and carrying one as well
      # would leave a reader unsure which of the two was load-bearing.
      # `test_admin.py` asserts the absence against BOTH the rendered
      # page and `get_fieldsets()`, so a Django upgrade that
      # reintroduced it fails there rather than being silently absorbed
      # by a second guard.
      fieldsets = (
          (None, {"fields": ("username", "password")}),
          ("Personal info", {"fields": ("first_name", "last_name", "email")}),
          ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
          ("Important dates", {"fields": ("last_login", "date_joined")}),
      )
      add_fieldsets = (
          (None, {"classes": ("wide",),
                  "fields": ("username", "password1", "password2")}),
      )
      filter_horizontal = ("groups",)

      def save_model(self, request, obj, form, change):
          if not change:
              return super().save_model(request, obj, form, change)
          actor = principal_for_request(request)
          before = User.objects.get(pk=obj.pk)
          # The two guarded columns first, through their services, so a
          # refusal happens BEFORE anything else on the form is written.
          if before.is_superuser != obj.is_superuser:
              services.set_superuser(actor, before, obj.is_superuser, source=SOURCE_ADMIN)
              obj.is_superuser = before.is_superuser
              obj.is_staff = before.is_staff
          if before.is_active != obj.is_active:
              if obj.is_active:
                  services.reactivate_user(actor, before, source=SOURCE_ADMIN)
              else:
                  services.deactivate_user(actor, before, source=SOURCE_ADMIN)
              obj.is_active = before.is_active
          super().save_model(request, obj, form, change)
  ```
  `services.ServiceRefused` propagating out of `save_model` would be a 500 on `/admin/`;
  `IdentityUserAdmin` therefore overrides `changeform_view` to catch it and re-render with
  `self.message_user(request, str(exc), level=messages.ERROR)`.

- [ ] **Step 6: Write `identity/context_processors.py` and the nav link.**
  ```python
  """Two context keys the shared shell reads.

  Registered in `TEMPLATES[0]["OPTIONS"]["context_processors"]` beside
  `tools.vision.context_processors.features`, for exactly the same reason
  that one is: the shell (`foundation/templates/_shell.html`) has to
  decide whether to render a nav link on EVERY page, including pages whose
  own view knows nothing about identity, and a context processor is the
  one mechanism that reaches all of them.

  In the open posture this answers `("open", True)` without a second
  query: `posture()` reads the settings row, and `is_admin` returns from
  its open branch before touching anything.
  """
  from __future__ import annotations

  from django.db import DatabaseError

  from identity.access import is_admin, posture
  from identity.request import principal_for_request


  def identity(request) -> dict:
      """`identity_posture` and `identity_is_admin`, so no page has to ask
      twice.

      Swallows `DatabaseError` and answers "open, not an admin" for a box
      mid-migration: a template that raised while rendering the nav would
      turn every page on an unmigrated box into a 500, which is precisely
      the state an operator is trying to fix.
      """
      try:
          return {
              "identity_posture": posture(),
              "identity_is_admin": is_admin(principal_for_request(request)),
          }
      except DatabaseError:
          return {"identity_posture": "open", "identity_is_admin": False}
  ```
  In `_shell.html`'s nav block, after the Setup link:
  ```html
  {# Only when accounts are on AND the viewer administers this box, so a
     household box's shell is byte-identical to today's. #}
  {% if identity_posture != "open" and identity_is_admin %}<a href="{% url 'identity-users' %}" class="{% block nav_current_identity %}{% endblock %}">Accounts</a>{% endif %}
  {% if identity_posture != "open" %}<a href="{% url 'identity-password-change' %}">Your password</a>{% endif %}
  ```

- [ ] **Step 7: Run the three test modules, then the whole column.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity`
  Expected: PASS

- [ ] **Step 8: Prove the open box's shell has not moved.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/chat tools foundation`
  Expected: PASS — no template assertion anywhere changes, because the nav additions are both
  behind `identity_posture != "open"`.

- [ ] **Step 9: Commit** as
  `feat(identity): T9 — the users page, the posture page with the content toggle, and break-glass admin`.

---
### Task 10: the acting rule, part 1 — `ToolContext.agent_slug` and the actor in every payload

> A chat turn or queue job acts as the **user**. An agent's tool list is intersected with the
> user's tool entitlements. Delegates inherit the root user. An agent is never a way around
> labels. The watcher and CLI ingest act as one named service principal.

Today the runtime acts as the *agent*. Reversing that needs two facts to travel separately —
*who this is being done for* and *whose tool declaration is in force* — and the second of them
has nowhere to live, because both were crammed into one field. This task splits them and puts
the actor into every job payload. **Task 11 is where the runtime starts reading them.**

**Files:**
- Modify: `agents/contracts/tools.py` — `ToolContext.agent_slug`
- Modify: `agents/chat/service.py::start_turn` — an `actor` keyword; the payload
- Modify: `agents/chat/views/turns.py`, `agents/chat/views/conversations.py` — pass the actor
- Modify: `agents/management/commands/agent_turn.py` — `SERVICE_PRINCIPAL` into the payload
- Modify: `tools/rag/views.py::AskView.post` (line ~1097) — the `rag.ask` payload
- Modify: `tools/rag/ingest.py::enqueue_ingest` (line ~1181) — the `rag.ingest` payload
- Modify: `tools/rag/management/commands/ingest.py`, `::ingest_watch.py` — the service actor
- Modify: `tools/vision/views.py::generate` (line ~789) — the `vision.generate` payload
- Modify: `models/registry/views.py::role_reencode` — the `rag.reencode` payload
- Modify: `agents/contracts/tests/test_tools.py`, `agents/chat/tests/test_turn_create.py`,
  `tools/rag/tests/test_views.py`, `tools/rag/tests/test_ingest.py`,
  `tools/vision/tests/test_views_generate.py`, `models/registry/tests/test_views.py` — the
  payload assertions. **These are the modules that exist**; there is no `test_turns.py`,
  `test_views_ask.py` or `test_views_reencode.py` in this tree.
- Modify: `docs/adr/0013-inference-execution-queue.md` — the amendment

**Interfaces:**
- Consumes: `identity.contracts.principals.payload_fields` (Task 1),
  `identity.request.principal_for_request` (Task 5).
- Produces:
  ```python
  # agents/contracts/tools.py -- the seven existing fields, unchanged, plus one.
  @dataclass(frozen=True)
  class ToolContext:
      conversation_id: str
      principal: Principal
      depth: int
      budget: StepBudget
      job: JobContext
      tool_key: str = ""
      supplied_keys: frozenset[str] = frozenset()
      agent_slug: str = ""      # NEW: whose tool DECLARATION is in force

  # agents/chat/service.py
  def start_turn(conversation, text: str, *, connection: str = "", actor) -> TurnStart
  ```
  Every payload for `agent.turn`, `rag.ask`, `rag.ingest`, `vision.generate` and `rag.reencode`
  now carries `actor_kind` and `actor_key`.

**Steps:**

- [ ] **Step 1: Write the failing test** in `agents/contracts/tests/test_tools.py`:
  ```python
  class TestToolContextCarriesBothFacts:
      def test_principal_is_who_and_agent_slug_is_whose_declaration(self):
          """They were ONE field, which is why the acting rule could not
          be expressed before: `principal` now always means WHO THIS IS
          BEING DONE FOR, and `agent_slug` means WHOSE TOOL DECLARATION IS
          IN FORCE. A delegate carries the caller's principal and its own
          slug -- that pair is the whole of "an agent is never a way
          around labels"."""
          ctx = ToolContext(conversation_id="", principal=Principal("user", "7"),
                            depth=0, budget=_budget(), job=None, agent_slug="librarian")
          assert ctx.principal == Principal("user", "7")
          assert ctx.agent_slug == "librarian"

      def test_it_defaults_blank_so_a_direct_runner_call_still_works(self):
          """Every test that builds a bare `ToolContext` and skips
          `invoke_tool` -- and the future MCP edge, which has no agent at
          all."""
          ctx = ToolContext(conversation_id="", principal=OPEN_PRINCIPAL, depth=0,
                            budget=_budget(), job=None)
          assert ctx.agent_slug == ""
  ```
  and, in `agents/chat/tests/test_turn_create.py`:
  ```python
  class TestThePayloadCarriesTheActor:
      def test_a_turn_started_on_the_page_records_the_signed_in_user(self, client, monkeypatch):
          """THE ACTOR TRAVELS IN THE PAYLOAD. The queue has one door
          (`models.contracts.queue.enqueue`) and ADR 0013 freezes its
          signature, so "who asked" belongs in the payload -- which is
          already the record of what was asked for."""
          captured = {}
          monkeypatch.setattr("agents.chat.service.enqueue",
                              lambda kind, payload, **kw: captured.update(payload) or 1)
          user = make_user()
          with posture(POSTURE_PERSONAL):
              sign_in(client, user)
              client.post(reverse("chat-start"), {"agent": agent.slug, "text": "hi"})
          assert captured["actor_kind"] == "user"
          assert captured["actor_key"] == str(user.pk)

      def test_an_open_box_records_the_open_principal(self, client, monkeypatch):
          """Not a blank. Every job on an open box has an honest actor,
          which is what makes `models/queue/visibility.py` able to reason
          about it later -- a payload with no actor at all is the
          pre-IA-1 shape, and it is admin-only."""
          captured = {}
          monkeypatch.setattr("agents.chat.service.enqueue",
                              lambda kind, payload, **kw: captured.update(payload) or 1)
          agent = make_agent(slug=_unique_slug("open-actor"))
          bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("chat-role"))
          with posture(POSTURE_OPEN):
              client.post(reverse("chat-start"), {"agent": agent.slug, "text": "hi"})
          assert (captured["actor_kind"], captured["actor_key"]) == ("open", "box")

      def test_a_cli_turn_records_the_one_service_principal(self, monkeypatch):
          """`manage.py agent_turn` builds its own payload
          (`agents/management/commands/agent_turn.py:82`) rather than
          going through `start_turn`, so it needs its own assertion --
          the two dicts are two places the same two keys can be
          forgotten."""
          captured = {}
          monkeypatch.setattr("agents.management.commands.agent_turn.enqueue",
                              lambda kind, payload, **kw: captured.update(payload) or 1)
          agent = make_agent(slug=_unique_slug("cli-actor"))
          bind_chat_role(CHAT_CONVERSE_ROLE, name=_unique_slug("chat-role"))
          call_command("agent_turn", "--agent", agent.slug, "--text", "hi")
          assert (captured["actor_kind"], captured["actor_key"]) == ("service", "local")
  ```
  The same two-assertion pair — signed-in user, then service principal — is written for each of
  the other four kinds in the module that already covers that surface:
  `tools/rag/tests/test_views.py` (`rag.ask`, patching `tools.rag.views.enqueue`),
  `tools/rag/tests/test_ingest.py` (`rag.ingest`, patching `tools.rag.ingest.enqueue`, with the
  service half driven through `call_command("ingest", ...)`),
  `tools/vision/tests/test_views_generate.py` (`vision.generate`, patching
  `tools.vision.views.enqueue`), and `models/registry/tests/test_views.py` (`rag.reencode`,
  patching `models.registry.views.enqueue` — admin-only, so its actor is always a signed-in
  superuser or the open principal, and there is no service half).

- [ ] **Step 2: Run to verify they fail.**
  Run: `.venv/bin/pytest -q agents/contracts/tests/test_tools.py agents/chat/tests/test_turn_create.py`
  Expected: FAIL — `TypeError: ToolContext.__init__() got an unexpected keyword argument 'agent_slug'`

- [ ] **Step 3: Add the field** to `ToolContext` (`agents/contracts/tools.py`, after
  `supplied_keys`), with the comment the docstring needs:
  ```python
      # WHOSE TOOL DECLARATION IS IN FORCE, as distinct from `principal`,
      # which is WHO THIS IS BEING DONE FOR. Both facts used to be crammed
      # into `principal`, which is exactly why the acting rule -- a turn
      # runs as the USER, and a delegate inherits the root user -- could
      # not be expressed: there was one field and two questions.
      #
      # Blank for a runner called directly and for a caller with no agent
      # at all (the future MCP edge).
      #
      # `agents.models.ToolInvocation` gains a matching column IN IA-2,
      # with `invoke_tool` as its writer. It is NOT added here: IA-1 ships
      # exactly four migrations (spec section 17), and a column with no
      # writer would be a schema change nobody could point at a behaviour
      # for.
      agent_slug: str = ""
  ```

- [ ] **Step 4: Thread the actor through `start_turn`.** In `agents/chat/service.py`:
  ```python
  def start_turn(conversation, text: str, *, connection: str = "", actor) -> TurnStart:
      """Queue one turn for `conversation`, or refuse without writing.

      `actor` is REQUIRED and keyword-only, deliberately: this function
      builds the payload the runtime later acts as, and a default would be
      a fail-open default -- the caller that forgot it would silently
      enqueue a turn attributed to nobody, and every visibility rule
      downstream would then be reasoning about a blank.
      """
  ```
  and inside the `atomic()` block:
  ```python
              payload = {
                  "conversation": str(conversation.id),
                  "turn": placeholder.pk,
                  "agent": agent.slug,
                  "text": message,
                  "connection": str(connection) if connection else None,
                  "mode": "chat",
                  # WHO ASKED. The runtime reads these back with
                  # `identity.contracts.principals.principal_from_payload`
                  # and runs the turn as that principal -- not as the
                  # agent, which is the design the acting rule reverses.
                  **payload_fields(actor),
              }
  ```
  Its two callers pass one: `agents/chat/views/turns.py::turn_create` and
  `agents/chat/views/conversations.py::conversation_start` each already hold
  `principal_for_request(request)`; `agents/management/commands/agent_turn.py` passes
  `SERVICE_PRINCIPAL` and adds the same two keys to its own payload dict at line 82.

- [ ] **Step 5: Stamp the other four payloads.** Each is a two-line change: import
  `payload_fields` and `principal_for_request` (or `SERVICE_PRINCIPAL` for a command), then
  `**payload_fields(actor)` into the dict.
  | Site | Actor |
  |---|---|
  | `tools/rag/views.py::AskView.post` | `principal_for_request(request)` |
  | `tools/rag/ingest.py::enqueue_ingest` | a new required `actor` keyword, supplied by its callers |
  | `tools/rag/management/commands/ingest.py` and `::ingest_watch.py` | `SERVICE_PRINCIPAL` |
  | `tools/vision/views.py::generate` | `principal_for_request(request)` |
  | `models/registry/views.py::role_reencode` | `principal_for_request(request)` |

  `enqueue_ingest`'s new keyword gets the same comment `start_turn`'s does — required, not
  defaulted, because a default of "the box" would silently attribute an uploaded document's
  ingest to nobody.

  **The re-encode kind is `rag.reencode`** (`models/registry/apps.py:37`), enqueued by
  `models/registry/views.py::role_reencode`. Stamp it like the other four: "every enqueuing
  surface stamps the actor" is a rule with no exceptions to remember, and the console is
  superuser-only anyway, so the row is admin-visible either way.

- [ ] **Step 5b: A shell path owns the row it creates, as the service principal.**
  `agents/management/commands/agent_turn.py:185` currently calls
  `create_conversation(OPEN_PRINCIPAL, agent)`. After Step 4 its payload carries
  `SERVICE_PRINCIPAL`, so leaving the create alone would make one command write two different
  answers to "who did this" — the row saying the box, the job saying the shell. Spec §5.3
  item 8 settles it: a shell-created row is stamped `("service", "local")` through
  `owner_fields`, exactly as the payload is.
  ```python
  # agents/management/commands/agent_turn.py:34 -- the import is REPLACED,
  # not joined: nothing in this command acts as the open principal any
  # more, and leaving the old name imported would invite the next editor
  # to reach for it.
  from identity.contracts.principals import SERVICE_PRINCIPAL

  # agents/management/commands/agent_turn.py:185, inside `_conversation`
          # THE SAME PRINCIPAL THIS COMMAND'S PAYLOAD CARRIES. A shell
          # path acts as the service principal (spec section 5.3 item 8),
          # and it must own what it creates as that principal too --
          # otherwise this one command writes two different answers to
          # "who did this", and every visibility function downstream has
          # to pick one.
          #
          # The consequence, stated rather than discovered: a
          # service-owned row is visible to `is_admin` and to NOBODY
          # ELSE. That is the one place the administer/read split does
          # not apply, because nobody's privacy is at stake -- there is
          # no person behind a shell invocation for the row to be
          # private from, and an operator who runs a command needs to
          # see what it produced.
          conversation = create_conversation(SERVICE_PRINCIPAL, agent)
  ```
  `manage.py install_defaults` **keeps `OPEN_PRINCIPAL`** and this step does not touch it: a
  shipped default is marked `resident=True`, and every visibility function returns residents to
  everybody regardless of owner, so its owner column is not load-bearing — while changing it
  would move those rows out of `adopt_open_rows`' reach for no gain. Its test:
  ```python
  # agents/tests/test_agent_turn_command.py
  def test_a_shell_turn_owns_its_conversation_as_the_service_principal(self):
      """One command, ONE answer to "who did this": the row and the
      payload carry the same principal."""
      call_command("agent_turn", "--agent", agent.slug, "--text", "hi")
      conversation = Conversation.objects.get()
      assert (conversation.owner_kind, conversation.owner_key) == ("service", "local")

  def test_install_defaults_still_owns_its_rows_as_the_box(self):
      """Deliberate, and NOT an oversight: a resident row is visible to
      everybody through `Q(resident=True)` whatever its owner, and
      leaving it open-owned keeps it inside `adopt_open_rows`' reach."""
      call_command("install_defaults")
      assert set(Agent.objects.values_list("owner_kind", flat=True)) == {"open"}
  ```

- [ ] **Step 6: Update the payload assertions** in the existing tests for each of the five
  kinds. Most assert `captured == {...}` on a whole dict; each gains the two keys. Where a test
  asserts a **summarizer's** output, nothing changes — no summarizer reads the actor.

- [ ] **Step 7: Run every column this touched.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents tools models`
  Expected: PASS

- [ ] **Step 8: Docs.** `docs/adr/0013-inference-execution-queue.md` — an amendment at its
  foot: job payloads now carry the acting principal (`actor_kind`/`actor_key`), the queue page
  filters on them, and **every enqueuing surface carries a new obligation to stamp them**.
  Recorded on the queue's own side, as ADR 0013's precedent requires. `agents/contracts/
  README.md` and `agents/runtime/README.md` — `ToolContext.agent_slug` and what it is for.

- [ ] **Step 9: Commit** as
  `feat(agents): T10 — the acting principal travels in every job payload`.

---

### Task 11: the acting rule, part 2 — the runtime acts as the user, and `principal_for` dies

`agents/runtime/bindings.py::principal_for(agent)` answers "who is this AGENT acting as", which
the acting rule makes the wrong question. Four call sites stop asking it, it is deleted, and the
AST guard's constructor set drops to two files.

**Files:**
- Modify: `agents/runtime/bindings.py` — delete `principal_for` (lines 50–68)
- Modify: `agents/runtime/jobs.py::_tool_roles` and `::plan_turn`
- Modify: `agents/runtime/loop.py::_run_turn` (line 164) and `::available_tools` (line 378)
- Modify: `agents/runtime/delegate.py::run_agent_tool` (line 89)
- Modify: `agents/runtime/preflight.py::preflight_turn` (line 151)
- Modify: `agents/visibility.py::visible_flows` — the reversed docstring ruling
- Modify: `foundation/ops/tests/test_import_law.py` — `_PRINCIPAL_CONSTRUCTORS` drops to two
- Modify: `agents/runtime/tests/`, `agents/chat/tests/test_visibility.py`
- Modify: `agents/README.md`, `agents/runtime/README.md`,
  `docs/adr/0015-agent-layer-and-tool-contract.md`

**Interfaces:**
- Consumes: `ToolContext.agent_slug` and the payload actor keys (Task 10).
- Produces:
  ```python
  # agents/runtime/jobs.py
  def _tool_roles(agent, actor) -> set[str]      # was _tool_roles(agent)
  ```
  `agents/runtime/bindings.py::principal_for` no longer exists.

**Steps:**

- [ ] **Step 1: Write the failing tests** in `agents/runtime/tests/test_acting_rule.py` (new):
  ```python
  """The acting rule, end to end.

  A turn runs as the USER. A delegate carries the SAME principal and a
  DIFFERENT `agent_slug`. There is no hop at which the acting principal
  widens -- which is the whole of "an agent is never a way around
  labels".

  `runner_capture` below is a MODULE-LEVEL runner, referenced by its
  dotted path exactly as every other test runner in this package is
  (`agents.runtime.tests._helpers.runner_ok`): a `ToolSpec.runner` is a
  string resolved at call time, so a closure defined inside a test would
  never be reachable. It appends the live `ToolContext` to `CAPTURED`,
  which is the only way to see what the loop actually built.
  """
  from __future__ import annotations

  import pytest

  from agents.contracts.tools import ToolResult, ToolSpec, register_tool
  from agents.models import Turn
  from agents.runtime.loop import run_turn
  from agents.runtime.tests._helpers import (  # noqa: F401 -- the import IS the registration
      FakeToolLLM, bind_chat_role, isolated_tool_registry, make_agent, make_conversation,
      make_job_ctx, make_turn, patch_llm,
  )
  from agents.resident import agent_tool_specs
  from identity.contracts.principals import OPEN_PRINCIPAL, Principal, SERVICE_PRINCIPAL
  from models.contracts.roles import CHAT_CONVERSE_ROLE

  pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_tool_registry")]

  CAPTURED: list = []


  def runner_capture(args: dict, ctx) -> ToolResult:
      """Records the live `ToolContext` and answers. The ONLY way to see
      what the loop built -- `invoke_tool` constructs it and hands it
      straight to the runner, so there is no other seam to read it at."""
      CAPTURED.append(ctx)
      return ToolResult(text="captured")


  @pytest.fixture(autouse=True)
  def _clear_captured():
      CAPTURED.clear()
      yield
      CAPTURED.clear()


  def _setup(actor_fields: dict, *, tool_keys=("stub.capture",), slug="general"):
      """A conversation, a queued assistant turn, and the payload the
      queue would carry -- with `actor_fields` merged in, so each test
      below differs by exactly the two keys it is about."""
      register_tool(ToolSpec(key="stub.capture", label="C", description="d",
                             runner="agents.runtime.tests.test_acting_rule.runner_capture"))
      bind_chat_role(CHAT_CONVERSE_ROLE)
      agent = make_agent(slug=slug, tool_keys=list(tool_keys), max_steps=8)
      conv = make_conversation(agent=agent)
      make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="a question")
      assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                            state=Turn.State.QUEUED)
      payload = {
          "conversation": str(conv.id), "turn": assistant.pk, "agent": agent.slug,
          "text": "a question", "connection": None, "mode": "chat",
          **actor_fields,
      }
      return agent, conv, payload


  class TestARootTurn:
      def test_it_runs_as_the_actor_from_the_payload_not_as_the_agent(self):
          """THE REVERSAL, stated as an assertion. Before this, a turn ran
          as `Principal("resident_agent"|"user_agent", agent.slug)` --
          `agents/runtime/bindings.py::principal_for`, now deleted."""
          agent, _conv, payload = _setup({"actor_kind": "user", "actor_key": "7"})
          script = [("tool", "stub.capture", {}), ("final", "done")]
          with patch_llm(FakeToolLLM(script)):
              run_turn(payload, [], make_job_ctx())
          assert CAPTURED[0].principal == Principal("user", "7")
          assert CAPTURED[0].agent_slug == agent.slug

      def test_a_cli_turn_runs_as_the_one_service_principal(self):
          _agent, _conv, payload = _setup({"actor_kind": "service", "actor_key": "local"})
          with patch_llm(FakeToolLLM([("tool", "stub.capture", {}), ("final", "done")])):
              run_turn(payload, [], make_job_ctx())
          assert CAPTURED[0].principal == SERVICE_PRINCIPAL

      def test_a_payload_with_no_actor_runs_as_the_open_principal(self):
          """Every turn enqueued before this phase. Never a crash, and
          never a blank principal that no filter can reason about --
          `principal_from_payload` answers `OPEN_PRINCIPAL` and says
          why."""
          _agent, _conv, payload = _setup({})
          with patch_llm(FakeToolLLM([("tool", "stub.capture", {}), ("final", "done")])):
              run_turn(payload, [], make_job_ctx())
          assert CAPTURED[0].principal == OPEN_PRINCIPAL

      def test_a_payload_with_a_junk_actor_kind_does_not_fail_the_job(self):
          """A hand-edited or corrupt row must not turn into a failed
          turn: the worker would retry it forever."""
          _agent, _conv, payload = _setup({"actor_kind": "wizard", "actor_key": "x"})
          with patch_llm(FakeToolLLM([("tool", "stub.capture", {}), ("final", "done")])):
              result = run_turn(payload, [], make_job_ctx())
          assert result["text"] == "done"
          assert CAPTURED[0].principal == OPEN_PRINCIPAL


  class TestADelegate:
      def test_it_inherits_the_root_actor_and_carries_its_own_slug(self):
          """NO HOP WIDENS THE ACTING PRINCIPAL. The delegate's own slug
          travels in `agent_slug`, so the trail still records WHICH agent
          made the call -- the fact an operator most wants when reading
          it -- while the principal stays the person who asked."""
          for spec in agent_tool_specs():
              register_tool(spec)
          register_tool(ToolSpec(key="stub.capture", label="C", description="d",
                                 runner="agents.runtime.tests.test_acting_rule.runner_capture"))
          bind_chat_role(CHAT_CONVERSE_ROLE)
          make_agent(slug="library", tool_keys=["stub.capture"],
                     system_prompt="You are the library.")
          general = make_agent(slug="general", tool_keys=["agent.library"], max_steps=8)
          conv = make_conversation(agent=general)
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask the library")
          assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                                state=Turn.State.QUEUED)
          payload = {
              "conversation": str(conv.id), "turn": assistant.pk, "agent": general.slug,
              "text": "ask the library", "connection": None, "mode": "chat",
              "actor_kind": "user", "actor_key": "7",
          }
          script = [
              ("tool", "agent.library", {"task": "what does the library say?"}),  # root
              ("tool", "stub.capture", {}),                                       # delegate
              ("final", "the library found it"),                                  # delegate
              ("final", "here is your answer"),                                   # root
          ]
          with patch_llm(FakeToolLLM(script)):
              run_turn(payload, [], make_job_ctx())

          # ONE capture, from inside the DELEGATE's own loop.
          assert len(CAPTURED) == 1
          assert CAPTURED[0].principal == Principal("user", "7")
          assert CAPTURED[0].agent_slug == "library"
          assert CAPTURED[0].depth == 1

      def test_the_delegates_tool_list_is_computed_from_the_root_actor(self, monkeypatch):
          """`available_tools` is called with the ROOT actor, not with a
          principal minted for the sub-agent. Asserted at the CALL, not
          only at its result, because in IA-1 every tool access is
          unrestricted and the two would produce the same list -- so the
          argument is the only thing that can be wrong yet."""
          import agents.runtime.delegate as delegate_module

          seen: list = []
          real = delegate_module.available_tools

          def spy(principal, agent):
              seen.append((principal, agent.slug))
              return real(principal, agent)

          monkeypatch.setattr(delegate_module, "available_tools", spy)

          for spec in agent_tool_specs():
              register_tool(spec)
          bind_chat_role(CHAT_CONVERSE_ROLE)
          make_agent(slug="library", tool_keys=[])
          general = make_agent(slug="general", tool_keys=["agent.library"], max_steps=8)
          conv = make_conversation(agent=general)
          make_turn(conversation=conv, index=0, role=Turn.Role.USER, text="ask")
          assistant = make_turn(conversation=conv, index=1, role=Turn.Role.ASSISTANT,
                                state=Turn.State.QUEUED)
          payload = {"conversation": str(conv.id), "turn": assistant.pk,
                     "agent": general.slug, "text": "ask", "connection": None,
                     "mode": "chat", "actor_kind": "user", "actor_key": "7"}
          script = [("tool", "agent.library", {"task": "go"}), ("final", "sub done"),
                    ("final", "root done")]
          with patch_llm(FakeToolLLM(script)):
              run_turn(payload, [], make_job_ctx())

          assert seen == [(Principal("user", "7"), "library")]


  class TestTheRunTimeRederivation:
      def test_the_run_time_availability_call_uses_the_payload_actor(self, monkeypatch):
          """`plan_turn` computes roles at ENQUEUE time and `_run_turn`
          re-derives from the actor at RUN time, so somebody whose access
          narrowed between the two gets the narrower answer -- free,
          because that read has to happen at run time anyway.

          In IA-1 every tool access is unrestricted, so this asserts the
          SHAPE -- the run-time call really is made with the payload's
          actor -- rather than a narrowing. IA-2 gives it teeth by making
          the two answers differ."""
          import agents.runtime.loop as loop_module

          seen: list = []
          real = loop_module.granted_tools
          monkeypatch.setattr(
              loop_module, "granted_tools",
              lambda principal, keys: seen.append(principal) or real(principal, keys),
          )
          _agent, _conv, payload = _setup({"actor_kind": "user", "actor_key": "7"})
          with patch_llm(FakeToolLLM([("final", "done")])):
              run_turn(payload, [], make_job_ctx())
          assert seen and all(p == Principal("user", "7") for p in seen)

      def test_the_planner_walks_the_agent_tree_as_the_actor(self, monkeypatch):
          """`_tool_roles(agent, actor)`: the enqueue-time role walk uses
          the same principal the run will, so the queue's admission
          snapshot cannot be computed against a wider tool set than the
          turn is actually offered."""
          import agents.runtime.jobs as jobs_module

          seen: list = []
          real = jobs_module.granted_tools
          monkeypatch.setattr(
              jobs_module, "granted_tools",
              lambda principal, keys: seen.append(principal) or real(principal, keys),
          )
          _agent, _conv, payload = _setup({"actor_kind": "user", "actor_key": "7"})
          jobs_module.plan_turn(payload)
          assert seen and all(p == Principal("user", "7") for p in seen)


  class TestPrincipalForIsGone:
      def test_the_bindings_module_no_longer_answers_who_an_agent_acts_as(self):
          """It has no callers left, and a function that answers the
          WRONG QUESTION is worse than no function: somebody would call
          it again."""
          import agents.runtime.bindings as bindings
          assert not hasattr(bindings, "principal_for")
  ```

- [ ] **Step 2: Run to verify they fail.**
  Run: `.venv/bin/pytest -q agents/runtime/tests/test_acting_rule.py`
  Expected: FAIL

- [ ] **Step 3: Rewire the four call sites.**
  - `agents/runtime/loop.py::_run_turn` (line 164): `principal = principal_from_payload(payload)`
    replaces `principal = principal_for(agent)`; the `ToolContext` it builds gains
    `agent_slug=agent.slug`.
  - `agents/runtime/loop.py::available_tools(principal, agent)` is unchanged in signature —
    it already takes the principal it is handed, and now that principal is the actor.
  - `agents/runtime/jobs.py::_tool_roles(agent)` becomes `_tool_roles(agent, actor)`; every
    `principal_for(current)` inside its walk becomes `actor`; `plan_turn` builds
    `actor = principal_from_payload(payload)` and passes it.
  - `agents/runtime/delegate.py::run_agent_tool` (line 89): delete
    `principal = principal_for(agent)`; build the child context with
    `principal=ctx.principal` and `agent_slug=agent.slug`; pass `ctx.principal` to
    `available_tools`.
  - `agents/runtime/preflight.py` (line 151): `preflight_turn` gains an `actor` keyword
    (its callers — `agents/chat/service.py::start_turn` and
    `agents/management/commands/agent_turn.py` — both already hold one) and calls
    `granted_tools(actor, agent.tool_keys)`.

- [ ] **Step 4: Delete `principal_for`** from `agents/runtime/bindings.py`, and delete the
  paragraph in that module's docstring and in `agents/runtime/README.md` that describes it.

- [ ] **Step 5: Reverse the recorded ruling** in `agents/visibility.py::visible_flows`'s
  docstring. The 2026-08-28 ruling ("a delegate sees the flows IT may run, not the flows its
  caller may") is reversed by the acting rule; write the reversal out rather than deleting the
  old text silently:
  ```python
      """Every ENABLED flow this principal may run, ASKED WITH THE ACTING
      PRINCIPAL -- which after the acting rule (Identity & Auth, spec
      section 5.3) is the USER, not the agent.

      THIS REVERSES THE 2026-08-28 RULING recorded here before. That
      ruling said a delegate sees the flows IT may run rather than the
      flows its caller may; the acting rule says an agent is a tool a
      person wields, not a second person, so there is no hop at which the
      acting principal widens. Its three runtime callers
      (`narrowed_flow_spec`, `flow_row_roles`, `run_flow`) therefore
      narrow a delegate to the flows the ROOT USER may run -- which is the
      whole point: flipping the posture changes what an agent can run, not
      merely what a page lists. Recorded in ADR 0016 and in an amendment
      to ADR 0015 section 10.
      """
  ```

- [ ] **Step 6: Shrink the constructor set to two.** In
  `foundation/ops/tests/test_import_law.py`, remove `agents/runtime/bindings.py` and rewrite
  the comment above `_PRINCIPAL_CONSTRUCTORS` to say the acting rule deleted it and why a file
  reappearing here is a new hand-rolled copy, not a restoration.

- [ ] **Step 7: Run.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents foundation`
  Expected: PASS

- [ ] **Step 8: Docs.** `agents/README.md`, `agents/runtime/README.md`,
  `agents/chat/README.md` — the acting rule, `agent_slug`, the moved `Principal`, and the
  deletion of `principal_for`. `docs/adr/0015-agent-layer-and-tool-contract.md` — extend the
  amendment written in Task 5: §10's "`/chat/` runs turns as the chosen agent's own principal"
  is **reversed**, and `visible_flows`' 2026-08-28 delegate ruling with it.

- [ ] **Step 9: Commit** as
  `feat(agents): T11 — a turn runs as the user, and principal_for is deleted`.

---

### Task 12: owner columns on `GenerationJob` and `AskRecord`, and the owned-rows registry

Five tables in three columns carry `owner_kind`/`owner_key`; three of them already do. This task
adds the other two, stamps them at their create surfaces, and registers all five so the two
ownership commands can walk them without `identity/` importing a thing.

`Document` gets **no** owner column: a document's access is decided by its labels and the
library posture and by nothing else, and giving it an owner would create a second, invisible
rule ("the uploader can always see it") that no page displays and no admin can revoke.
`InferenceJob` gets none either — the actor travels in its payload (Task 10).

**Files:**
- Modify: `tools/vision/models.py::GenerationJob`, `tools/rag/models.py::AskRecord`
- Create: `tools/vision/migrations/0006_generationjob_owner.py`,
  `tools/rag/migrations/0014_askrecord_owner.py`
- Modify: `tools/vision/services.py::submit_job`, `tools/rag/services.py::record_ask`,
  and their callers (`tools/vision/views.py::generate`, `tools/rag/jobs.py::run_ask`)
- Modify: `agents/apps.py`, `tools/rag/apps.py`, `tools/vision/apps.py` — the registrations
- Modify: `agents/visibility.py::owner_fields` — a re-export
- Create: `identity/tests/test_owned_rows_registry.py`
- Modify: `tools/vision/tests/`, `tools/rag/tests/`

**Interfaces:**
- Consumes: `identity.access.owner_fields` (Task 4), `identity.contracts.ownership` (Task 1).
- Produces:
  ```python
  # tools/vision/services.py
  def submit_job(operation_key, raw_params, files=None, resolved=None, *, actor) -> GenerationJob

  # tools/rag/services.py
  def record_ask(*, question, category, connection_name, model_id, answer, citations,
                 actor) -> None
  ```
  and five `OwnedRows` registrations: `agents.agent`, `agents.flow`, `agents.conversation`,
  `rag.askrecord`, `vision.generationjob`.

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_owned_rows_registry.py`:
  ```python
  """All five owned tables register themselves, and every one resolves.

  Lives in `identity/tests/` rather than in each column's, deliberately:
  the property being tested is that the REGISTRY is complete, and that is
  a fact about the whole tree rather than about any one column.
  """
  from __future__ import annotations

  import pytest
  from django.apps import apps
  from django.conf import settings

  from identity.contracts.ownership import all_owned_rows

  pytestmark = pytest.mark.django_db


  def test_every_owned_table_is_registered():
      """Five tables in three columns. A sixth later is a REGISTRATION,
      not an edit to a command that would otherwise silently skip it."""
      keys = {spec.key for spec in all_owned_rows()}
      expected = {"agents.agent", "agents.flow", "agents.conversation", "rag.askrecord"}
      if "vision" in settings.FARABUNKER_FEATURES:
          expected.add("vision.generationjob")
      assert keys == expected


  def test_every_registered_model_resolves_through_apps_get_model():
      """Resolved at command time, never imported -- which is what lets
      `identity/` walk tables in columns it may not import."""
      for spec in all_owned_rows():
          model = apps.get_model(spec.model)
          assert {"owner_kind", "owner_key"} <= {f.name for f in model._meta.get_fields()}


  def test_the_vision_registration_is_behind_its_feature_flag():
      """It registers inside `tools/vision/apps.py`'s existing early exit,
      so a box with the feature off does not carry a registration naming a
      model whose app registered nothing else either."""
      keys = {spec.key for spec in all_owned_rows()}
      assert ("vision.generationjob" in keys) == ("vision" in settings.FARABUNKER_FEATURES)
  ```
  and the two stamping tests, one per column. In
  `tools/vision/tests/test_services.py` (module already carries
  `pytestmark = pytest.mark.django_db` and its own `patch_engine`/`preflight` doubles — reuse
  them; nothing here reaches an engine):
  ```python
  class TestOwnership:
      def test_a_generation_is_stamped_with_the_actor(self, monkeypatch):
          """Ownership is STAMPED, never inferred. A row written with
          blank owner columns is a row no filter can reason about, and
          backfilling one is a migration nobody has the information to
          write."""
          _patch_ready_engine(monkeypatch)
          job = services.submit_job(
              "txt2img", {"prompt": "a test", "width": 512, "height": 512},
              actor=Principal("user", "7"),
          )
          assert (job.owner_kind, job.owner_key) == ("user", "7")

      def test_the_actor_is_required_not_defaulted(self):
          """A default would be a fail-open default: the caller that
          forgot it would silently write a row attributed to nobody, and
          every visibility rule downstream would then be reasoning about
          a blank."""
          with pytest.raises(TypeError):
              services.submit_job("txt2img", {"prompt": "a test"})

      def test_an_open_box_stamps_the_open_principal_not_a_blank(self, monkeypatch):
          """`("open", "box")` is what `adopt_open_rows` claims. `("", "")`
          is only ever the shape of a row written BEFORE this migration."""
          _patch_ready_engine(monkeypatch)
          job = services.submit_job(
              "txt2img", {"prompt": "a test", "width": 512, "height": 512},
              actor=OPEN_PRINCIPAL,
          )
          assert (job.owner_kind, job.owner_key) == ("open", "box")
  ```
  and in `tools/rag/tests/test_services.py`:
  ```python
  class TestAskRecordOwnership:
      def test_an_ask_record_is_stamped_with_the_actor(self):
          services.record_ask(
              question="q", category=None, connection_name="a name",
              model_id="an id", answer="a", citations=[],
              actor=Principal("user", "7"),
          )
          row = AskRecord.objects.get()
          assert (row.owner_kind, row.owner_key) == ("user", "7")

      def test_it_is_stamped_from_the_jobs_payload_actor_not_from_a_request(self):
          """`record_ask` runs on the WORKER, inside
          `tools/rag/jobs.py::run_ask` -- there is no request there, so
          the actor comes from the payload the page wrote at enqueue
          time. This drives the handler, not the service, so the wiring
          between the two is what is asserted."""
          payload = {"question": "q", "category": None,
                     "actor_kind": "user", "actor_key": "7"}
          _run_ask_with_doubles(payload)          # module's existing gateway doubles
          assert AskRecord.objects.get().owner_key == "7"

      def test_a_pre_phase_payload_stamps_the_open_principal(self):
          """A `rag.ask` job enqueued before IA-1 and still in the queue
          when the worker picks it up. Never a crash, never a blank."""
          _run_ask_with_doubles({"question": "q", "category": None})
          row = AskRecord.objects.get()
          assert (row.owner_kind, row.owner_key) == ("open", "box")
  ```

- [ ] **Step 2: Run to verify they fail.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity/tests/test_owned_rows_registry.py`
  Expected: FAIL — `assert set() == {...}`

- [ ] **Step 3: Add the two column pairs.** In `tools/vision/models.py::GenerationJob` and
  `tools/rag/models.py::AskRecord`, with the same declarations `agents/models.py` already uses:
  ```python
      # WHO this row belongs to, stamped at create from
      # `identity.access.owner_fields(principal)`. Same two columns, same
      # widths and same blank default as `agents/models.py`'s -- one
      # definition of ownership across five tables in three columns, not
      # three that agree by convention.
      #
      # Existing rows get `""`, which means "written before accounts
      # existed" and is exactly what `manage.py adopt_open_rows` claims.
      owner_kind = models.CharField(max_length=32, blank=True, default="")
      owner_key = models.CharField(max_length=200, blank=True, default="")
  ```
  with `models.Index(fields=["owner_kind", "owner_key"], name="vision_job_owner")` and
  `... name="rag_askrecord_owner"` in each `Meta.indexes`.

- [ ] **Step 4: Generate and read the two migrations.**
  Run: `.venv/bin/python manage.py makemigrations vision --name generationjob_owner`
  Run: `.venv/bin/python manage.py makemigrations rag --name askrecord_owner`
  Confirm each is exactly two `AddField`s and one `AddIndex`, and that the file numbers are
  `tools/vision/migrations/0006_...` and `tools/rag/migrations/0014_...` — the next numbers in
  each app, matching the spec's migration table.

- [ ] **Step 5: Stamp at the two create surfaces.** `submit_job` and `record_ask` each gain a
  **required keyword-only** `actor` and `**owner_fields(actor)` in their `create(...)` call, for
  the same reason `start_turn`'s is required: a default would silently write a row no filter can
  reason about. Their callers already hold a principal —
  `tools/vision/views.py::generate` has `principal_for_request(request)`, and
  `tools/rag/jobs.py::run_ask` has `principal_from_payload(payload)` (the payload actor Task 10
  put there).

- [ ] **Step 6: Register all five.** In each column's `AppConfig.ready()` — still touching no
  database and importing no implementation module:
  ```python
  # agents/apps.py::ready(), at the end
          # The three owned tables in this column, so `manage.py
          # adopt_open_rows` and `manage.py reassign_owner` can walk them
          # without `identity/` importing `agents` (import-law rule 4).
          # Strings, resolved by `apps.get_model` at command time, exactly
          # as a JobKind's handler is a dotted path.
          from identity.contracts.ownership import OwnedRows, register_owned_rows

          register_owned_rows(OwnedRows("agents.agent", "Agents", "agents.Agent"))
          register_owned_rows(OwnedRows("agents.flow", "Flows", "agents.Flow"))
          register_owned_rows(
              OwnedRows("agents.conversation", "Conversations", "agents.Conversation"))
  ```
  ```python
  # tools/rag/apps.py::ready(), at the end
          from identity.contracts.ownership import OwnedRows, register_owned_rows

          register_owned_rows(OwnedRows("rag.askrecord", "Ask history", "rag.AskRecord"))
  ```
  ```python
  # tools/vision/apps.py::ready(), INSIDE the existing feature-flag early exit
          from identity.contracts.ownership import OwnedRows, register_owned_rows

          register_owned_rows(
              OwnedRows("vision.generationjob", "Generated images", "vision.GenerationJob"))
  ```

- [ ] **Step 7: Make `agents/visibility.py::owner_fields` a re-export**, so its existing callers
  and the `.objects` guard are untouched while there is exactly one definition:
  ```python
  # MOVED to `identity.access.owner_fields` (Identity & Auth, IA-1): five
  # owned tables in three columns now share one definition rather than
  # three that agree by convention. Re-exported here so this module's
  # existing callers -- and `test_column_boundaries.py`'s exclusion, which
  # names this file -- are untouched.
  from identity.access import owner_fields  # noqa: F401 -- re-exported on purpose
  ```

- [ ] **Step 8: Run.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q tools agents identity`
  Run: `FARABUNKER_FEATURES='vision' .venv/bin/pytest -q tools agents identity`
  Expected: PASS in both. The second matters: the vision registration is behind the flag.

- [ ] **Step 9: Commit** as
  `feat(identity): T12 — owner columns on the two tables that lacked them, and the owned-rows registry`.

---

### Task 13: the visibility bodies — conversations, agents, flows, vision jobs, queue rows, ask records

Every visibility function gets its real body. **Every one tests the open branch first**, so an
open box executes zero share queries and zero grant queries — the spec's §3.4 claim, made
structural rather than promised. Entitlements do not exist yet, so `sees_all_content` and
ownership are the whole rule in IA-1; IA-2 adds the `Share` clause to the same four bodies.

**Files:**
- Modify: `agents/visibility.py` — the four bodies plus `visible_turn` and `delete_conversation`
- Modify: `agents/chat/views/thread.py`, `::turns.py`, `::conversations.py` — route through them
- Create: `tools/vision/visibility.py`, `models/queue/visibility.py`, `tools/rag/access.py`
- Create: `models/queue/tests/__init__.py` is present already; `models/queue/tests/_helpers.py`
  does **not** exist and is created here (see Step 1a)
- Modify: `tools/vision/views.py` (gallery, recent list, job_status, job_delete, output_file,
  input_file, queue_job_status), `models/queue/views.py` (QueueView, `_present_row`,
  `queue_job_cancel`), `tools/rag/views.py` (HistoryView, AskJobStatusView)
- Modify: `foundation/ops/tests/test_column_boundaries.py`, `foundation/ops/tests/test_import_law.py`
- Modify: `models/README.md`, `foundation/README.md`

**Interfaces:**
- Consumes: `identity.access.{is_admin,sees_all_content}` (Task 4),
  `identity.request.principal_for_request` (Task 5), the payload actor keys (Task 10),
  the owner columns (Task 12).
- Produces:
  ```python
  # agents/visibility.py
  def visible_conversations(principal); def visible_agents(principal)
  def installed_agent_slugs(principal); def visible_flows(principal)
  def visible_turn(principal, turn_id)                  # a Turn or None
  def delete_conversation(principal, conversation) -> bool

  # tools/vision/visibility.py
  def visible_jobs(principal); def may_read_job(principal, job) -> bool

  # models/queue/visibility.py -- a NAMED rule-2 seam (see Decisions, item 4)
  def visible_jobs(principal)                            # an InferenceJob queryset
  def visible_rows(principal, rows)                      # filters QueueRow objects
  def may_see_job_id(principal, job_id: int) -> bool
  def may_read_job_content(principal, payload) -> bool

  # tools/rag/access.py
  def visible_ask_records(principal)
  ```

**Steps:**

- [ ] **Step 1: Write the failing tests.** Four modules — one per column, plus the queue's.
  `agents/chat/tests/test_visibility.py` is **extended**, not replaced: its existing open-mode
  assertions are the regression pin that says nothing about today's box moved, so they stay
  exactly as they are and these classes are added beneath them. Each module needs `posture`,
  `make_user`, `make_admin` and `user_principal` in its own package's `_helpers.py` — copied,
  never imported across a column, per the helper-duplication rule.
  ```python
  # agents/chat/tests/test_visibility.py -- appended
  class TestOwnershipNarrowsWhenAccountsAreOn:
      def test_a_member_sees_their_own_conversations_and_not_another_persons(self):
          ann, bob = make_user(), make_user()
          mine = create_conversation(user_principal(ann), make_agent(slug="a1"))
          theirs = create_conversation(user_principal(bob), make_agent(slug="a2"))
          with posture(POSTURE_PERSONAL):
              ids = {c.id for c in visible_conversations(user_principal(ann))}
          assert mine.id in ids and theirs.id not in ids

      def test_an_admin_with_the_content_setting_off_is_answered_like_a_member(self):
          """THE DEFAULT. An administrator has no job that requires
          reading somebody's conversation."""
          admin, bob = make_admin(), make_user()
          theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert theirs.id not in {c.id for c in
                                       visible_conversations(user_principal(admin))}

      def test_turning_the_setting_on_shows_them_everything(self):
          """The toggle's whole effect, in one pair of assertions: the
          same admin, the same row, two answers."""
          admin, bob = make_admin(), make_user()
          theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL, admin_sees_content=True):
              assert theirs.id in {c.id for c in
                                   visible_conversations(user_principal(admin))}

      def test_the_two_non_open_postures_answer_identically(self):
          """No posture branch lives in any visibility function; a test
          that reintroduced one fails here."""
          admin, bob = make_admin(), make_user()
          create_conversation(user_principal(bob), make_agent(slug="a1"))
          counts = []
          for name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
              with posture(name, admin_sees_content=False):
                  counts.append(visible_conversations(user_principal(admin)).count())
          assert counts[0] == counts[1]

      def test_a_resident_agent_is_visible_to_everybody(self):
          """The shipped defaults are the platform's own offer, not
          somebody's private work -- so they are visible without being
          owned, and without a share."""
          make_agent(slug="shipped", resident=True)
          make_agent(slug="somebody-elses", resident=False,
                     owner_kind="user", owner_key="99999999")
          with posture(POSTURE_PERSONAL):
              slugs = {a.slug for a in visible_agents(user_principal(make_user()))}
          assert "shipped" in slugs
          assert "somebody-elses" not in slugs

      def test_a_disabled_resident_agent_is_still_not_runnable(self):
          """`enabled=False` is applied on top of the visibility rule,
          not instead of it -- the same property this function had before
          accounts existed."""
          make_agent(slug="shipped-off", resident=True, enabled=False)
          with posture(POSTURE_PERSONAL):
              slugs = {a.slug for a in visible_agents(user_principal(make_user()))}
          assert "shipped-off" not in slugs

      def test_a_users_own_disabled_agent_is_still_in_installed_slugs(self):
          """The offers list is computed from this, and a disabled
          INSTALLED default must stay off the offers list -- otherwise
          the operator gets an "Add" button for a row that already exists
          and cannot be re-added, with no visible way back to re-enabling
          the one they have. That is the same reason this function is
          separate from `visible_agents`; accounts do not change it."""
          ann = make_user()
          make_agent(slug="mine-off", enabled=False,
                     **owner_fields(user_principal(ann)))
          with posture(POSTURE_PERSONAL):
              assert "mine-off" in set(installed_agent_slugs(user_principal(ann)))

      def test_flows_follow_the_same_rule_as_agents(self):
          ann = make_user()
          make_flow(slug="mine", **owner_fields(user_principal(ann)))
          make_flow(slug="shipped", resident=True)
          make_flow(slug="theirs", owner_kind="user", owner_key="99999999")
          with posture(POSTURE_PERSONAL):
              slugs = {f.slug for f in visible_flows(user_principal(ann))}
          assert slugs == {"mine", "shipped"}


  class TestServiceOwnedRows:
      def test_an_admin_sees_what_a_shell_path_made_with_the_setting_off(self):
          """THE ONE PLACE THE ADMINISTER/READ SPLIT DOES NOT APPLY
          (spec section 5.3 item 8). Nobody's privacy is at stake in a row
          a command produced -- there is no person behind a shell
          invocation for it to be private from -- and an operator who
          runs `manage.py agent_turn` needs to see what it made."""
          shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              ids = {c.id for c in visible_conversations(user_principal(make_admin()))}
          assert shell_made.id in ids

      def test_a_member_does_not(self):
          shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL):
              ids = {c.id for c in visible_conversations(user_principal(make_user()))}
          assert shell_made.id not in ids

      def test_an_admin_may_delete_a_conversation_a_shell_path_made(self):
          """The read side and the delete side agree. Without this,
          `_service_rows` would show an administrator a row on the list
          that `delete_conversation` then 404s on -- a surface that
          displays what it will not let you act on.

          Content-setting OFF, deliberately: this is `is_admin`, because
          pruning what a command left behind is administration."""
          shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert delete_conversation(user_principal(make_admin()), shell_made) is True
          assert not Conversation.objects.filter(pk=shell_made.pk).exists()

      def test_a_member_may_not(self):
          shell_made = create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL):
              assert delete_conversation(user_principal(make_user()), shell_made) is False
          assert Conversation.objects.filter(pk=shell_made.pk).exists()

      def test_an_empty_service_clause_widens_nothing(self):
          """THE TRAP THIS PINS: an empty `Q()` reads like "match
          everything", and if Django combined it that way a member would
          see the whole box. It does not -- `Q._combine` returns a copy
          of the non-empty operand -- and this asserts it against the
          compiled SQL rather than trusting it.

          SCOPED TO THE PREDICATE, and that scoping is load-bearing:
          `owner_kind` is a CONCRETE COLUMN on `Conversation` and on
          `Agent`, and `visible_conversations` calls
          `select_related("agent")`, so the name appears three or more
          times in the SELECT list of every query this function builds --
          filtered or not. Counting it across the whole string would
          count columns, not clauses, and the assertion would be about
          nothing."""
          member = user_principal(make_user())
          create_conversation(SERVICE_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL):
              where = str(visible_conversations(member).query).partition(" WHERE ")[2]
              assert where, "expected a filtered query for a member"
              assert where.count("owner_kind") == 1     # the member's own clause, alone
              assert visible_conversations(member).count() == 0


  class TestTheOpenBranchIsFirst:
      def test_an_open_box_builds_no_ownership_filter_at_all(self):
          """STRUCTURAL, not incidental: `sees_all_content(principal)`
          returns True from its open branch before any filter is built,
          so the queryset is the same unfiltered one it was before this
          phase. Asserted on the compiled SQL rather than on the row
          count, because a filter that happened to match everything would
          pass a count assertion while breaking the claim.

          THE ASSERTION IS "NO PREDICATE AT ALL", not "the word
          `owner_kind` is absent": that word is a concrete column on both
          `Conversation` and the `select_related` `Agent`, so it is in
          the SELECT list of every query here whether or not anything is
          filtered. `visible_conversations` applies no other filter --
          `visible_agents` and `visible_flows` DO (`enabled=True`), which
          is why this pin is written against the conversation function
          and only that one."""
          create_conversation(OPEN_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_OPEN):
              sql = str(visible_conversations(OPEN_PRINCIPAL).query)
          assert " WHERE " not in sql, sql

      def test_an_open_box_asks_the_user_table_nothing(self, django_assert_num_queries):
          """The one read is the settings singleton -- the query that
          answers WHICH POSTURE, which the box must ask before it can
          skip anything else."""
          from identity.models import IdentitySettings
          IdentitySettings.get_solo()
          create_conversation(OPEN_PRINCIPAL, make_agent(slug="a1"))
          with posture(POSTURE_OPEN):
              with django_assert_num_queries(2):     # the settings read + the list
                  list(visible_conversations(OPEN_PRINCIPAL))


  class TestVisibleTurn:
      def test_it_resolves_through_the_conversation_never_by_bare_pk(self):
          """`chat-turn-status` takes a SEQUENTIAL INTEGER, which is the
          enumeration exposure the agents spec's gap 4 recorded. This is
          where it closes."""
          ann, bob = make_user(), make_user()
          theirs = make_turn(
              conversation=create_conversation(user_principal(bob), make_agent(slug="a1")))
          with posture(POSTURE_PERSONAL):
              assert visible_turn(user_principal(ann), theirs.pk) is None

      def test_the_owner_gets_their_own_turn_back(self):
          ann = make_user()
          mine = make_turn(
              conversation=create_conversation(user_principal(ann), make_agent(slug="a1")))
          with posture(POSTURE_PERSONAL):
              assert visible_turn(user_principal(ann), mine.pk).pk == mine.pk

      def test_an_unknown_turn_id_is_None_not_an_exception(self):
          """The view answers 404 to an unknown id and to an invisible
          one alike, and the caller cannot tell them apart. That is the
          point."""
          with posture(POSTURE_PERSONAL):
              assert visible_turn(user_principal(make_user()), 99999999) is None


  class TestDeleteConversation:
      def test_the_owner_may_delete_and_a_stranger_may_not(self):
          ann, bob = make_user(), make_user()
          theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL):
              assert delete_conversation(user_principal(ann), theirs) is False
              assert Conversation.objects.filter(pk=theirs.pk).exists()
              assert delete_conversation(user_principal(bob), theirs) is True
              assert not Conversation.objects.filter(pk=theirs.pk).exists()

      def test_an_admin_with_the_content_setting_off_may_not_delete_somebody_elses(self):
          """Deleting somebody's conversation is not on the operator's
          administer list -- it is content, and it stays with the content
          predicate."""
          admin, bob = make_admin(), make_user()
          theirs = create_conversation(user_principal(bob), make_agent(slug="a1"))
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert delete_conversation(user_principal(admin), theirs) is False
  ```
  **Step 1a first: `models/queue/tests/_helpers.py` does not exist and this task creates it.**
  Every other test package in the tree has one; this package has never needed one because its
  tests seed `InferenceJob` inline. It gets the four identity helpers, copied — never imported
  across a column, per the helper-duplication rule:
  ```python
  """Shared test helpers for `models/queue/tests`.

  NEW IN IA-1. Plain importable module -- **not** a `conftest.py` (the
  repo forbids them anywhere). The four functions below are COPIES of
  `identity/tests/_helpers.py`'s, not imports of them: one column's test
  scaffolding must not become load-bearing for another's, which is the
  same rule that already duplicates `isolated_tool_registry` and the
  agent row builders per package.
  """
  from __future__ import annotations

  import contextlib
  import itertools
  import os

  from django.contrib.auth import get_user_model

  from identity.contracts.principals import Principal
  from identity.models import IdentitySettings

  _names = itertools.count()
  SWEEP_POSTURE_ENV = "FARABUNKER_TEST_POSTURE"


  @contextlib.contextmanager
  def posture(name: str, *, admin_sees_content: bool | None = None):
      row = IdentitySettings.get_solo()
      before = (row.posture, row.admin_sees_content)
      row.posture = name
      if admin_sees_content is not None:
          row.admin_sees_content = admin_sees_content
      row.save()
      try:
          yield row
      finally:
          row.posture, row.admin_sees_content = before
          row.save()


  def seed_sweep_posture() -> None:
      name = os.environ.get(SWEEP_POSTURE_ENV, "").strip()
      if not name:
          return
      row = IdentitySettings.get_solo()
      if row.posture != name:
          row.posture = name
          row.save()


  def make_user(**overrides):
      fields = {"username": f"member-{next(_names)}", "password": "not-a-real-password"}
      fields.update(overrides)
      password = fields.pop("password")
      user = get_user_model()(**fields)
      user.set_password(password)
      user.save()
      return user


  def make_admin(**overrides):
      overrides.setdefault("username", f"admin-{next(_names)}")
      overrides["is_superuser"] = True
      overrides["is_staff"] = True
      return make_user(**overrides)


  def user_principal(user) -> Principal:
      return Principal("user", str(user.pk))


  def sign_in(client, user, password: str = "not-a-real-password") -> None:
      assert client.login(username=user.username, password=password)
  ```
  Then the tests:
  ```python
  # models/queue/tests/test_visibility.py -- new
  """The queue's rows, and the difference between a row and its contents.

  `_row(...)` builds a `models.queue.backend.QueueRow` directly rather
  than enqueuing: `visible_rows` filters the frozen dataclasses
  `queue_snapshot()` already fetched, so the dataclass IS the unit.
  `visible_jobs`/`may_see_job_id` need real rows and get them from
  `_job(...)`.
  """
  from __future__ import annotations

  import pytest
  from django.urls import reverse
  from django.utils import timezone

  from identity.contracts.postures import (
      POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
  )
  from identity.contracts.principals import OPEN_PRINCIPAL
  from models.queue.backend import QueueRow
  # MODULE-LEVEL CONSTANTS, not a `TextChoices` inner class: this app
  # declares `QUEUED`/`RUNNING`/`SUCCEEDED`/`FAILED`/`CANCELLED` and
  # `STATE_CHOICES` at module scope (`models/queue/models.py:15-33`).
  # There is no `InferenceJob.State`.
  from models.queue.models import CANCELLED, QUEUED, InferenceJob
  from models.queue.tests._helpers import (
      make_admin, make_user, posture, seed_sweep_posture, sign_in, user_principal,
  )
  from models.queue.visibility import (
      may_read_job_content, may_see_job_id, visible_jobs, visible_rows,
  )

  pytestmark = pytest.mark.django_db


  @pytest.fixture(autouse=True)
  def _sweep():
      seed_sweep_posture()


  def _row(**overrides) -> QueueRow:
      fields = dict(
          id=1, kind="rag.ask", payload={}, state=QUEUED, priority=100,
          exclusive=False, model_refs=[], footprint_bytes=None,
          created_at=timezone.now(), started_at=None, finished_at=None, error="",
          progress=None,
      )
      fields.update(overrides)
      return QueueRow(**fields)


  def _job(**overrides) -> InferenceJob:
      # `priority` is `PositiveIntegerField()` with NO default
      # (`models/queue/models.py:89`) -- it is resolved at enqueue time by
      # the priority chain, so a row built directly must supply one or
      # the insert raises IntegrityError.
      fields = dict(kind="rag.ask", payload={}, state=QUEUED, priority=100)
      fields.update(overrides)
      return InferenceJob.objects.create(**fields)


  class TestRowsVersusContent:
      def test_an_admin_sees_every_row_with_the_content_setting_off(self):
          """A queue row is OPERATIONAL: its kind, owner, state, progress,
          priority and timings are what an administrator needs to run the
          box, and none of them is somebody's words."""
          admin = make_admin()
          rows = [_row(payload={"actor_kind": "user", "actor_key": "7"}),
                  _row(id=2, payload={})]
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert len(visible_rows(user_principal(admin), rows)) == 2

      def test_and_the_payload_of_a_job_they_did_not_start_is_withheld(self):
          """A prompt is somebody's words and a retrieval answer is
          somebody's answer."""
          admin = make_admin()
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert may_read_job_content(
                  user_principal(admin),
                  {"actor_kind": "user", "actor_key": "7"}) is False

      def test_and_their_own_job_is_not_withheld_from_them(self):
          """Rows-versus-content is about OTHER PEOPLE's content. An
          administrator reading back their own prompt is not a
          disclosure."""
          admin = make_admin()
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert may_read_job_content(
                  user_principal(admin),
                  {"actor_kind": "user", "actor_key": str(admin.pk)}) is True

      def test_turning_the_setting_on_opens_the_payload_and_nothing_else(self):
          admin = make_admin()
          rows = [_row(payload={"actor_kind": "user", "actor_key": "7"})]
          with posture(POSTURE_PERSONAL, admin_sees_content=True):
              assert may_read_job_content(
                  user_principal(admin), rows[0].payload) is True
              assert len(visible_rows(user_principal(admin), rows)) == 1

      def test_a_member_sees_only_jobs_they_caused(self):
          ann, bob = make_user(), make_user()
          rows = [_row(payload={"actor_kind": "user", "actor_key": str(ann.pk)}),
                  _row(id=2, payload={"actor_kind": "user", "actor_key": str(bob.pk)})]
          with posture(POSTURE_PERSONAL):
              assert [r.id for r in visible_rows(user_principal(ann), rows)] == [1]

      def test_a_job_with_no_actor_keys_is_visible_to_admins_and_nobody_else(self):
          """FAIL CLOSED FOR MEMBERS, OPEN FOR ADMINISTRATORS. Every job
          enqueued before this phase is in this state, and a member must
          see nothing they did not cause."""
          member, admin = make_user(), make_admin()
          rows = [_row(payload={})]
          with posture(POSTURE_PERSONAL):
              assert visible_rows(user_principal(member), rows) == []
              assert len(visible_rows(user_principal(admin), rows)) == 1

      def test_a_non_dict_payload_is_not_a_500(self):
          """`InferenceJob.payload` is a JSONField: a bare string or a
          list survives it as easily as a dict, and the queue page is a
          never-500 surface."""
          member = make_user()
          with posture(POSTURE_PERSONAL):
              assert visible_rows(user_principal(member), [_row(payload=[1, 2])]) == []
              assert visible_rows(user_principal(member), [_row(payload="x")]) == []
              assert may_read_job_content(user_principal(member), None) is False

      def test_a_member_is_not_the_open_principal(self):
          """The one way a fail-closed rule could quietly fail open: a
          pre-phase row reads back as `("open", "box")`, and no member
          ever matches that pair."""
          member = make_user()
          rows = [_row(payload={"actor_kind": "open", "actor_key": "box"})]
          with posture(POSTURE_PERSONAL):
              assert visible_rows(user_principal(member), rows) == []


  class TestTheQuerysetAndTheIdLookup:
      def test_the_queryset_and_the_row_filter_agree(self):
          """One predicate, two callers. A page that showed a row the
          cancel route then refused -- or the reverse -- is the bug this
          assertion exists to prevent."""
          ann = make_user()
          mine = _job(payload={"actor_kind": "user", "actor_key": str(ann.pk)})
          theirs = _job(payload={"actor_kind": "user", "actor_key": "99999999"})
          with posture(POSTURE_PERSONAL):
              principal = user_principal(ann)
              assert list(visible_jobs(principal).values_list("pk", flat=True)) == [mine.pk]
              assert may_see_job_id(principal, mine.pk) is True
              assert may_see_job_id(principal, theirs.pk) is False

      def test_an_admin_may_see_any_id_including_one_that_does_not_exist(self):
          """`is_admin` short-circuits before the query, which is what
          keeps the two poll routes from paying a lookup they do not
          need. An unknown id is then the VIEW's 404, not this
          function's."""
          admin = make_admin()
          with posture(POSTURE_PERSONAL):
              assert may_see_job_id(user_principal(admin), 99999999) is True

      def test_an_open_box_returns_everything_unfiltered(self):
          _job(payload={})
          with posture(POSTURE_OPEN):
              sql = str(visible_jobs(OPEN_PRINCIPAL).query)
          assert "actor_key" not in sql


  class TestTheCancelRoute:
      def test_cancel_is_administer_so_an_admin_may_cancel_anything(self, client):
          """A stuck queue is an operational fact and clearing it is the
          operator's job. There is no posture, and no setting, in which
          the operator cannot clear their own queue."""
          admin = make_admin()
          job = _job(payload={"actor_kind": "user", "actor_key": "99999999"})
          with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
              sign_in(client, admin)
              response = client.post(reverse("jobs-queue-cancel", args=[job.pk]))
          assert response.status_code == 302
          job.refresh_from_db()
          assert job.state == CANCELLED

      def test_a_member_cancelling_someone_elses_job_gets_404_not_403(self, client):
          """403 on a row-addressed URL confirms the row exists."""
          member = make_user()
          job = _job(payload={"actor_kind": "user", "actor_key": "99999999"})
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, member)
              response = client.post(reverse("jobs-queue-cancel", args=[job.pk]))
          assert response.status_code == 404
          job.refresh_from_db()
          assert job.state == QUEUED

      def test_a_member_may_cancel_their_own(self, client):
          member = make_user()
          job = _job(payload={"actor_kind": "user", "actor_key": str(member.pk)})
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, member)
              assert client.post(
                  reverse("jobs-queue-cancel", args=[job.pk])).status_code == 302


  class TestTheQueuePage:
      def test_it_renders_a_withheld_marker_not_a_blank(self, client):
          """A blank summary would read as a job that carried nothing.
          The row must say that its content is hidden."""
          admin = make_admin()
          _job(kind="rag.ask",
               payload={"question": "a private question",
                        "actor_kind": "user", "actor_key": "99999999"})
          with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
              sign_in(client, admin)
              body = client.get(reverse("jobs-queue")).content.decode()
          assert "a private question" not in body
          assert "Content hidden" in body

      def test_and_the_row_itself_is_still_there(self, client):
          """The whole of rows-versus-content, on one page: the operator
          can see that something is queued and act on it, without reading
          it. The row's kind, state and cancel control are all present in
          the same response that withheld the question."""
          admin = make_admin()
          job = _job(kind="rag.ask",
                     payload={"question": "a private question",
                              "actor_kind": "user", "actor_key": "99999999"})
          with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
              sign_in(client, admin)
              body = client.get(reverse("jobs-queue")).content.decode()
          assert "rag.ask" in body or "Ask" in body
          assert reverse("jobs-queue-cancel", args=[job.pk]) in body
  ```
  ```python
  # tools/vision/tests/test_visibility.py -- new
  class TestGeneratedImagesAreContent:
      def test_a_member_sees_only_their_own_generations(self):
          ann, bob = make_user(), make_user()
          mine = _generation(**owner_fields(user_principal(ann)))
          theirs = _generation(**owner_fields(user_principal(bob)))
          with posture(POSTURE_PERSONAL):
              ids = {j.pk for j in visible_jobs(user_principal(ann))}
          assert mine.pk in ids and theirs.pk not in ids

      def test_an_admin_with_the_setting_off_sees_none_of_them(self):
          """A generated image is CONTENT. An administrator has no job
          that requires looking at somebody's pictures."""
          admin, bob = make_admin(), make_user()
          theirs = _generation(**owner_fields(user_principal(bob)))
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert may_read_job(user_principal(admin), theirs) is False
          with posture(POSTURE_PERSONAL, admin_sees_content=True):
              assert may_read_job(user_principal(admin), theirs) is True

      def test_the_four_row_addressed_routes_answer_404_not_403(self, client):
          """`vision-job-status`, `vision-job-delete`, `vision-output-file`
          and `vision-input-file`. 403 on a row-addressed URL confirms the
          row exists."""
          ann, bob = make_user(), make_user()
          theirs = _generation(**owner_fields(user_principal(bob)))
          output = _output(job=theirs)
          with posture(POSTURE_PERSONAL):
              sign_in(client, ann)
              assert client.get(
                  reverse("vision-job-status", args=[theirs.pk])).status_code == 404
              assert client.post(
                  reverse("vision-job-delete", args=[theirs.pk])).status_code == 404
              assert client.get(
                  reverse("vision-output-file", args=[output.pk])).status_code == 404

      def test_the_gallery_and_the_recent_list_both_narrow(self):
          """Two listings, one rule. The create page's Recent list reads
          the same manager the gallery does, and a filter applied to one
          and not the other is exactly the drift a single visibility
          module exists to prevent."""
          ann, bob = make_user(), make_user()
          _generation(**owner_fields(user_principal(bob)))
          with posture(POSTURE_PERSONAL):
              sign_in(client, ann)
              gallery = client.get(reverse("vision-gallery")).content.decode()
              create = client.get(reverse("vision-create")).content.decode()
          assert "no images" in gallery.lower()
          assert str(bob.pk) not in create
  ```
  ```python
  # tools/rag/tests/test_access.py -- new
  class TestAskHistoryIsContent:
      def test_a_member_sees_only_their_own_records(self):
          ann, bob = make_user(), make_user()
          mine = _ask_record(**owner_fields(user_principal(ann)))
          theirs = _ask_record(**owner_fields(user_principal(bob)))
          with posture(POSTURE_PERSONAL):
              pks = {r.pk for r in visible_ask_records(user_principal(ann))}
          assert mine.pk in pks and theirs.pk not in pks

      def test_an_admin_with_the_setting_off_sees_only_their_own(self):
          """An Ask record holds a QUESTION and an ANSWER, so it is
          content, not an operational row."""
          admin, bob = make_admin(), make_user()
          theirs = _ask_record(**owner_fields(user_principal(bob)))
          with posture(POSTURE_PERSONAL, admin_sees_content=False):
              assert theirs.pk not in {r.pk for r in
                                       visible_ask_records(user_principal(admin))}

      def test_the_history_pages_count_comes_off_the_same_function_as_its_rows(self, client):
          """A count that disagreed with the rows beneath it would leak
          exactly the fact the filter exists to hide -- and the count is
          the easiest one to forget, because it renders no record."""
          ann, bob = make_user(), make_user()
          _ask_record(**owner_fields(user_principal(bob)))
          _ask_record(**owner_fields(user_principal(ann)))
          with posture(POSTURE_PERSONAL):
              sign_in(client, ann)
              response = client.get(reverse("rag-history"))
          assert response.context["record_count"] == 1
          assert len(response.context["records"]) == 1

      def test_a_pre_phase_record_with_blank_owner_columns_is_nobodys(self):
          """`("", "")` is what a record written before this phase
          carries. It belongs to nobody until `adopt_open_rows` claims
          it, and until then a member must not see it."""
          _ask_record(owner_kind="", owner_key="")
          with posture(POSTURE_PERSONAL):
              assert visible_ask_records(user_principal(make_user())).count() == 0
  ```
- [ ] **Step 2: Run to verify they fail.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q agents/chat/tests/test_visibility.py models/queue/tests/test_visibility.py tools/vision/tests/test_visibility.py tools/rag/tests/test_access.py`
  Expected: FAIL — `ModuleNotFoundError: No module named 'models.queue.visibility'` for the
  three new modules, and `AssertionError` on the extended `agents/chat` classes, whose
  functions exist but still return everything.

- [ ] **Step 3: Fill in `agents/visibility.py`.** Each body is four lines. Note the two
  imports the module does not have yet — `Q` and `Turn`:
  ```python
  from django.db.models import Q

  from agents.models import Agent, Conversation, Flow, Turn   # Turn is new here
  from identity.access import is_admin, sees_all_content


  def _owned(principal):
      """The ownership clause. Its own function so the four bodies below
      cannot drift, and so IA-2's `Share` clause has one place to join."""
      return Q(owner_kind=principal.kind, owner_key=principal.key)


  def _service_rows(principal):
      """Rows a SHELL PATH created, for a principal who may see them.

      `Q(owner_kind="service")` for an administrator; an EMPTY `Q()` for
      everybody else. Spec section 7.2.

      `is_admin`, NOT `sees_all_content`, and this is the ONE place the
      administer/read split does not apply (spec section 5.3 item 8):
      nobody's privacy is at stake in a row a command produced -- there
      is no person behind a shell invocation for it to be private from --
      and an operator who runs a command needs to see what it made,
      whatever the content toggle says.

      AN EMPTY `Q()` IS SAFE IN AN `OR`, and that is worth stating
      because the naive reading is the dangerous one: an empty `Q` does
      NOT mean "match everything" here. Django's `Q._combine` returns a
      copy of the non-empty operand when the other is empty, so
      `_owned(p) | _service_rows(p)` is exactly `_owned(p)` for a
      non-admin. `test_an_empty_service_clause_widens_nothing` pins that
      against the compiled SQL rather than trusting it.
      """
      return Q(owner_kind="service") if is_admin(principal) else Q()


  def visible_conversations(principal):
      """Every conversation this principal may read.

      THE OPEN BRANCH IS FIRST, in this and in every function below:
      `sees_all_content` tests `accounts_on()` before it reads a second
      table, so an open box executes zero ownership queries and the
      queryset is the same `.all()` it was before this phase.

      IA-2 adds `| Q(pk__in=_shared_keys(...))` here and in the three
      functions below. Nothing else about these bodies changes.
      """
      qs = Conversation.objects.select_related("agent")
      if sees_all_content(principal):
          return qs.all()
      return qs.filter(_owned(principal) | _service_rows(principal))


  def visible_agents(principal):
      """Every ENABLED agent this principal may run.

      `resident=True` rows are the shipped defaults and are visible to
      everybody -- they are the platform's own offer, not somebody's
      private work.
      """
      qs = Agent.objects.filter(enabled=True)
      if sees_all_content(principal):
          return qs
      return qs.filter(_owned(principal) | Q(resident=True) | _service_rows(principal))


  def installed_agent_slugs(principal):
      """Every slug this principal has installed, ENABLED or not -- the
      set the "Add the default X" offers are computed against. Same
      visibility rule, minus the `enabled` filter, for the reason this
      function's original docstring gives."""
      qs = Agent.objects.all()
      if not sees_all_content(principal):
          qs = qs.filter(_owned(principal) | Q(resident=True) | _service_rows(principal))
      return qs.values_list("slug", flat=True).distinct()


  def visible_flows(principal):
      # docstring rewritten in Task 11
      qs = Flow.objects.filter(enabled=True)
      if sees_all_content(principal):
          return qs
      return qs.filter(_owned(principal) | Q(resident=True) | _service_rows(principal))


  def visible_turn(principal, turn_id):
      """The `Turn` `turn_id`, or None.

      RESOLVED THROUGH THE CONVERSATION, never by bare pk.
      `chat-turn-status` takes a sequential integer, which is the
      enumeration exposure the agents spec's gap 4 recorded, and this is
      where it closes. `None` for an unknown id as well as an invisible
      one -- the view answers 404 to both, and the caller cannot tell them
      apart, which is the point.
      """
      return Turn.objects.filter(
          pk=turn_id, conversation__in=visible_conversations(principal)
      ).select_related("conversation").first()


  def _may_delete(principal, conversation) -> bool:
      """Whether `principal` may delete `conversation`.

      THE READ SIDE AND THE DELETE SIDE MUST AGREE ABOUT SERVICE-OWNED
      ROWS. `_service_rows` shows an administrator every conversation a
      shell path made; without the middle branch here, that administrator
      would see the row on the list, click delete, and get a 404 --
      a surface that shows a row it will not let you act on, which is
      the worst of the three possible answers.

      RULING: an administrator MAY delete a service-owned conversation.
      It is the same reasoning that makes them visible in the first place
      -- nobody's privacy is at stake in a row a command produced, and
      pruning what an automated path left behind is exactly the operator
      work `is_admin` exists for. `is_admin`, not `sees_all_content`, for
      the same reason: this is administration, not reading.
      """
      if sees_all_content(principal):
          return True
      if conversation.owner_kind == "service":
          return is_admin(principal)
      return (conversation.owner_kind == principal.kind
              and conversation.owner_key == principal.key)


  def delete_conversation(principal, conversation) -> bool:
      """Delete `conversation` if `principal` may. True if it went.

      THE DELETE LIVES HERE for the same reason the create does: a view
      that could reach the manager could reach it without the ownership
      check, and a guard with an exception is a guard somebody widens.
      IA-2 deletes the conversation's `Share` rows in this same
      transaction.
      """
      if not _may_delete(principal, conversation):
          return False
      conversation.delete()
      return True
  ```
  `agents/chat/views/turns.py::turn_status` switches from a bare `get_object_or_404(Turn, ...)`
  to `visible_turn(principal, turn_id)` + `Http404`; `conversation_delete` switches to
  `delete_conversation(...)` + 404 when it answers False.

- [ ] **Step 4: Write `tools/vision/visibility.py`.**
  ```python
  """What one principal may see of the generated images, in one place.

  The same shape `agents/visibility.py` has, and for the same reason: a
  future MCP edge and a future management command need the same answer,
  and neither is a view.

  A generated image is CONTENT, so the predicate is `sees_all_content`,
  not `is_admin`: an administrator with the content setting off sees
  none of somebody's pictures, and deleting one is not on the operator's
  administer list either, so `job_delete` uses the same predicate.

  `is_admin` IS READ HERE, in exactly one place and for exactly one
  reason: a row owned by `("service", "local")` -- one a shell path
  produced. Nobody's privacy is at stake in it, there is no person
  behind a command invocation for it to be private from, and an operator
  needs to see what a command made whatever the content setting says.
  That is the same single carve-out
  `agents/visibility.py::_service_rows` states in full; it is duplicated
  here rather than imported because `tools/` may not import `agents/`.
  """
  from __future__ import annotations

  from django.db.models import Q

  from identity.access import is_admin, sees_all_content
  from tools.vision.models import GenerationJob


  def visible_jobs(principal):
      qs = GenerationJob.objects.all()
      if sees_all_content(principal):
          return qs
      # `Q(owner_kind="service")` for an admin, an empty `Q()` otherwise
      # -- the same rule `agents/visibility.py::_service_rows` states in
      # full, duplicated here rather than imported because `tools/` may
      # not import `agents/`.
      service = Q(owner_kind="service") if is_admin(principal) else Q()
      return qs.filter(Q(owner_kind=principal.kind, owner_key=principal.key) | service)


  def may_read_job(principal, job) -> bool:
      """Whether one already-loaded job may be read. `job_status`,
      `job_delete`, `output_file` and `input_file` resolve through this and
      answer 404 when it is False -- 404 rather than 403, because a 403 on
      a row-addressed URL confirms the row exists."""
      if sees_all_content(principal):
          return True
      if job.owner_kind == "service":
          return is_admin(principal)
      return job.owner_kind == principal.kind and job.owner_key == principal.key
  ```
  The six vision view sites route through it. `output_file`/`input_file` resolve their owner
  through `output.job` / `input.job` — one owner per generation, because a per-output owner
  would be a second answer to one question.

- [ ] **Step 5: Write `models/queue/visibility.py`.**
  ```python
  """The queue's rows and its contents, and the difference between them.

  A NAMED SEAM under import-law rule 2, alongside
  `models.registry.bindings`: `tools/rag/views.py` and
  `tools/vision/views.py` must ask "may this caller see queue job N" for
  their two poll routes, and `models/contracts/queue.py::get_job` returns
  a `JobStatus` that deliberately carries no `payload`. Widening
  `JobStatus` instead would put another principal's prompt text on the
  seam, which is the opposite of what this module is for. Recorded in
  `models/README.md` and `foundation/README.md`, and pinned as a closed
  set in `foundation/ops/tests/test_import_law.py`.

  ROWS ARE `is_admin`; CONTENTS ARE `sees_all_content`. A queue row's
  kind, owner, state, progress, priority and timings are what an
  administrator needs to run the box -- to see that something is stuck,
  and to cancel it -- and none of them is somebody's words. Its payload
  and its answer text are.
  """
  from __future__ import annotations

  from identity.access import is_admin, sees_all_content
  from identity.contracts.principals import ACTOR_KEY_KEY, ACTOR_KIND_KEY
  from models.queue.models import InferenceJob


  def _is_actor(principal, payload) -> bool:
      """Whether `principal` is the actor recorded in `payload`.

      ONE PREDICATE, THREE CALLERS -- the queryset filter, the row-list
      filter and the content check -- so the page and the cancel route
      cannot come to disagree about who owns a job.

      A non-dict payload answers False rather than raising:
      `InferenceJob.payload` is a `JSONField`, so a bare string or a list
      survives it as easily as a dict, and the queue page is a never-500
      surface.
      """
      if not isinstance(payload, dict):
          return False
      return (payload.get(ACTOR_KIND_KEY) == principal.kind
              and payload.get(ACTOR_KEY_KEY) == principal.key)


  def visible_jobs(principal):
      """The queue rows this principal may see, as a QUERYSET -- for
      `jobs-queue-cancel` and the two poll routes, which have an id.

      FAIL CLOSED FOR MEMBERS, OPEN FOR ADMINISTRATORS. A job whose
      payload carries neither key -- every job enqueued before this phase
      -- is visible to `is_admin` and to nobody else. A member sees
      nothing they did not cause.

      A JSON filter, not an index: the queue table is retention-pruned and
      page-limited already, so the scan is bounded. If a future box makes
      it hot, the fix is an expression index on `(payload->>'actor_key')`
      -- named here so it is a follow-up rather than a redesign.
      """
      qs = InferenceJob.objects.all()
      if is_admin(principal):
          return qs
      return qs.filter(**{f"payload__{ACTOR_KIND_KEY}": principal.kind,
                          f"payload__{ACTOR_KEY_KEY}": principal.key})


  def visible_rows(principal, rows):
      """The subset of already-fetched `QueueRow`s this principal may see.

      The queue PAGE renders `models.queue.backend.queue_snapshot()`,
      which fetches every row in ONE query and partitions it in Python. A
      queryset filter here would undo that; this filters the rows the page
      already holds, using the same predicate the queryset uses.
      """
      if is_admin(principal):
          return list(rows)
      return [row for row in rows if _is_actor(principal, row.payload)]


  def may_see_job_id(principal, job_id: int) -> bool:
      """Whether `job_id` is a row this principal may see, by id.

      For the two POLL routes (`rag-ask-status`, `vision-queue-status`),
      which reach the queue through `models.contracts.queue.get_job` and
      get a `JobStatus` back -- a shape that carries no payload, so the
      actor keys are not on it and the row has to be asked for
      separately.
      """
      if is_admin(principal):
          return True
      return visible_jobs(principal).filter(pk=job_id).exists()


  def may_read_job_content(principal, payload) -> bool:
      """Whether the PAYLOAD and the ANSWER/RESULT text of a row may be
      rendered to this principal: they own it, or `sees_all_content`.

      The second half of the rows-versus-content split. `_present_row`
      calls it once per row and, when it is False, renders the row with
      its operational fields and an explicit "content hidden" marker in
      place of the payload -- NOT A BLANK, which would read as a job that
      carried nothing.
      """
      return sees_all_content(principal) or _is_actor(principal, payload)
  ```
  `models/queue/views.py`: `QueueView.get_context_data` builds
  `principal = principal_for_request(self.request)`, filters each of the three snapshot lists
  through `visible_rows(principal, ...)`, and calls `_present_row(row, principal)`;
  `_present_row` gains the parameter and, when `may_read_job_content` is False, replaces
  `summary` with a constant `"Content hidden."` and sets `content_hidden: True` for the
  template. `queue_job_cancel` checks `may_see_job_id(principal, job_id)` first and raises
  `Http404` otherwise — **before** it calls `cancel_job`, so a member cannot learn a job exists
  by the shape of the message they get back.

- [ ] **Step 6: Write `tools/rag/access.py`** — one function in IA-1; IA-2 fills the rest of
  this module in.
  ```python
  """What one principal may see of the library, in one place.

  IA-1 SHIPS ONE FUNCTION. `readable_documents`, `listable_documents`,
  `document_visibility` and the `DocumentVisibility` value all arrive in
  IA-2, when documents have labels for them to reason about -- writing
  them now would be four functions whose only behaviour is "return
  everything", which is a stub in an access module, and a stub in an
  access module is exactly the thing a later reader mistakes for a
  decision.
  """
  from __future__ import annotations

  from django.db.models import Q

  from identity.access import is_admin, sees_all_content
  from tools.rag.models import AskRecord


  def visible_ask_records(principal):
      """`/rag/history/`: own records, or all for a principal that
      `sees_all_content`.

      An Ask record holds a QUESTION and an ANSWER, so it is content, not
      an operational row -- which is why an administrator with the content
      setting off sees only their own.
      """
      qs = AskRecord.objects.all()
      if sees_all_content(principal):
          return qs
      # An admin also sees what a shell path produced -- `manage.py ask`
      # writes no `AskRecord` today, but `rag.ask` enqueued from a
      # command would, and a row nobody can see is a row nobody can
      # prune.
      service = Q(owner_kind="service") if is_admin(principal) else Q()
      return qs.filter(Q(owner_kind=principal.kind, owner_key=principal.key) | service)
  ```
  `tools/rag/views.py::HistoryView` reads `visible_ask_records(principal_for_request(request))`
  for both `records` and `record_count`, so the count and the rows beneath it can never
  disagree. `AskJobStatusView.get` calls `may_see_job_id(principal, job_id)` and answers its
  existing 404 body when False.

  **`JobStatus.summary` is content too, and so is `result`.** `models/queue/backend.py:104-107`
  fills `summary` from the job kind's own `summarize_job`, which for `rag.ask` is the question
  itself and for `vision.generate` is the prompt. So when `may_read_job_content` is False, both
  poll routes blank **both** fields, not just the obvious one — `rag-ask-status` drops
  `job.result` and replaces `summary` with the same `"Content hidden."` constant the queue page
  uses, and `vision-queue-status`'s `_queue_card_context` does the same for the card it
  renders. A route that hid the answer and printed the question underneath it would be a
  disclosure wearing a redaction's clothes. The assertion goes in the same test:
  ```python
  def test_a_withheld_poll_hides_the_question_as_well_as_the_answer(self, client):
      """`JobStatus.summary` is `summarize_job`'s output, which for
      `rag.ask` IS the question. Hiding the answer and leaving the
      question is not hiding anything."""
      admin = make_admin()
      job = _job(kind="rag.ask",
                 payload={"question": "a private question",
                          "actor_kind": "user", "actor_key": "99999999"})
      with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
          sign_in(client, admin)
          body = client.get(reverse("rag-ask-status", args=[job.pk])).json()
      assert "a private question" not in str(body)
      assert body["content_hidden"] is True
      assert "result" not in body and "answer" not in body
  ```

- [ ] **Step 6b: SECURITY — gate the two library mutations.** `rag-document-delete` and
  `rag-document-reingest` are class **R** in `identity/routes.py`, which the middleware
  enforces only as `AUTHENTICATED`; their real rule lives in the view, and in IA-1 nothing
  writes it. **As the plan stood, any signed-in member could delete or re-ingest any document
  on the box.** Document labels are IA-2, so the entitlement-owner half of the spec's rule
  cannot be written yet — but the `is_admin` half can, and it is the half that closes the hole:
  ```python
  # tools/rag/views.py::document_delete and ::document_reingest, first lines
      # ADMINISTRATION, not reading (spec section 11.3): removing or
      # re-ingesting a document is an operator action, and an
      # administrator may do it to a document they cannot read. 403, not
      # 404: this is a refusal on a class of ACTION, not a claim that the
      # row does not exist -- the member can already see the row's title
      # on the library page, so hiding its existence here would be a
      # secrecy the surrounding page does not keep.
      #
      # IA-2 widens this to "`is_admin`, OR an owner of one of the
      # document's entitlements". The entitlement tables do not exist
      # yet; the admin half does, and shipping it now is what keeps IA-1
      # from being a posture in which accounts make the box LESS safe
      # than the open one it replaced.
      principal = principal_for_request(request)
      if not is_admin(principal):
          return HttpResponseForbidden(
              "Deleting a document is an administrator action on this box."
          )
  ```
  One test each, in `tools/rag/tests/test_views.py`:
  ```python
  class TestTheLibraryMutationsAreAdministration:
      @pytest.mark.parametrize("name", ["rag-document-delete", "rag-document-reingest"])
      def test_a_member_is_refused(self, client, name):
          """THE HOLE THIS CLOSES: without it, any signed-in account
          could delete every document on the box."""
          document = make_document()
          with posture(POSTURE_ENTERPRISE):
              sign_in(client, make_user())
              response = client.post(reverse(name, args=[document.pk]))
          assert response.status_code == 403
          assert Document.objects.filter(pk=document.pk).exists()

      @pytest.mark.parametrize("name", ["rag-document-delete", "rag-document-reingest"])
      def test_an_admin_with_the_content_setting_off_is_allowed(self, client, name):
          """Administering is not reading: an administrator prunes a
          library they are not cleared to read. That is the whole reason
          this is `is_admin` and not `sees_all_content`."""
          document = make_document()
          with posture(POSTURE_ENTERPRISE, admin_sees_content=False):
              sign_in(client, make_admin())
              assert client.post(
                  reverse(name, args=[document.pk])).status_code in (302, 200)

      def test_an_open_box_is_unchanged(self, client):
          """`is_admin(OPEN_PRINCIPAL)` is True, so the household box
          behaves exactly as it does today."""
          document = make_document()
          with posture(POSTURE_OPEN):
              assert client.post(reverse("rag-document-delete",
                                         args=[document.pk])).status_code in (302, 200)
  ```

- [ ] **Step 7: Extend the two structural guards.**
  - `test_column_boundaries.py`: nothing changes in the chat `.objects` gate's shape (IA-2 adds
    `Share` to `_VISIBILITY_MODELS`); add a **new** narrow gate that no module under
    `tools/vision` outside `tools/vision/visibility.py` names `GenerationJob.objects`, in the
    same AST shape, with the same anti-vacuous pins. *(`tools/vision/services.py` legitimately
    creates rows, so it joins the exclusion set — which is a closed set of two, named, with the
    reason written out: a create is not a visibility question, and `submit_job` is the one
    create surface.)*
  - `test_import_law.py`: pin `models.queue.visibility` as the **only** submodule of
    `models.queue` that another column may import, mirroring
    `test_agents_reaches_models_registry_through_bindings_and_nothing_else`:
    ```python
    def test_other_columns_reach_the_queues_visibility_and_nothing_else_of_models_queue():
        """Rule 2's second sanctioned seam. The existing gate forbids
        `models.queue.models` outright; this one says that for `tools/`
        and `agents/`, `visibility` is the only OTHER submodule of
        `models.queue` they may touch at all -- so a later view cannot
        reach `models.queue.backend` or `models.queue.scheduler` for a
        visibility answer that already has one home."""
    ```

- [ ] **Step 8: Run everything.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q`
  Run: `FARABUNKER_FEATURES='vision' .venv/bin/pytest -q`
  Expected: PASS in both — this is the task most likely to move a page in the open posture, and
  the whole suite is the only honest gate on that.

- [ ] **Step 9: Docs.** `models/README.md` and `foundation/README.md` — `models.queue.
  visibility` named as a rule-2 seam beside `models.registry.bindings`. `tools/vision/README.md`
  — the new visibility module. `agents/README.md` — the four bodies now filter.

- [ ] **Step 10: Commit** as
  `feat(identity): T13 — every visibility function gets its body, and rows part from contents`.

---
### Task 14: `adopt_open_rows` and `reassign_owner`

The two ownership commands. Adoption is what makes an open box's existing rows belong to the
first administrator; reassignment is the documented follow-up to deactivating somebody who owned
rows. Both walk the registry, both run in one transaction, both write **one** audit event.

**Files:**
- Create: `identity/management/commands/adopt_open_rows.py`,
  `identity/management/commands/reassign_owner.py`
- Create: `identity/tests/test_command_adopt_open_rows.py`,
  `identity/tests/test_command_reassign_owner.py`
- Modify: `docs/OPERATIONS.md`

**Interfaces:**
- Consumes: `identity.contracts.ownership.all_owned_rows` (Task 1, registered Task 12),
  `identity.audit` (Task 4).
- Produces:
  ```
  manage.py adopt_open_rows --user <username> [--dry-run]
  manage.py reassign_owner --from <username|open> --to <username> [--kind <owned-rows key>]
  ```

**Steps:**

- [ ] **Step 1: Write the failing test** `identity/tests/test_command_adopt_open_rows.py`:
  ```python
  """`manage.py adopt_open_rows` -- an open box's first admin claims the
  rows the open principal owns.
  """
  from __future__ import annotations

  import pytest
  from django.core.management import CommandError, call_command

  from identity.contracts import actions
  from identity.contracts.principals import OPEN_PRINCIPAL
  from identity.models import AuditEvent
  from identity.tests._helpers import make_admin, make_user

  pytestmark = pytest.mark.django_db


  class TestRefusals:
      def test_an_unknown_username_is_refused(self):
          with pytest.raises(CommandError) as exc:
              call_command("adopt_open_rows", "--user", "nobody")
          assert "nobody" in str(exc.value)

      def test_a_member_is_refused(self):
          """Adoption gives one account every row on the box. That is an
          administrator's decision, and the refusal names why."""
          make_user(username="ann")
          with pytest.raises(CommandError) as exc:
              call_command("adopt_open_rows", "--user", "ann")
          assert "superuser" in str(exc.value).lower()

      def test_an_inactive_superuser_is_refused(self):
          make_admin(username="ann", is_active=False)
          with pytest.raises(CommandError):
              call_command("adopt_open_rows", "--user", "ann")


  class TestClaiming:
      def test_it_claims_open_owned_and_blank_owned_rows(self, capsys):
          """BOTH. `("open", "box")` is what the agents column stamped;
          `("", "")` is what `vision` and `rag` rows written before IA-1
          carry, because their columns did not exist. A command that
          claimed only one would leave half the box unowned with no
          message saying so."""
          admin = make_admin(username="ann")
          open_owned = _conversation(owner_kind="open", owner_key="box")
          blank_owned = _ask_record(owner_kind="", owner_key="")
          call_command("adopt_open_rows", "--user", "ann")
          open_owned.refresh_from_db()
          blank_owned.refresh_from_db()
          assert (open_owned.owner_kind, open_owned.owner_key) == ("user", str(admin.pk))
          assert (blank_owned.owner_kind, blank_owned.owner_key) == ("user", str(admin.pk))

      def test_it_never_claims_a_row_a_shell_path_made(self):
          """Spec section 10.4. Attributing `manage.py agent_turn`'s
          conversation to the first administrator would put a person's
          name on an automated action, and nothing downstream could tell
          it had happened."""
          make_admin(username="ann")
          shell_made = _conversation(owner_kind="service", owner_key="local")
          call_command("adopt_open_rows", "--user", "ann")
          shell_made.refresh_from_db()
          assert (shell_made.owner_kind, shell_made.owner_key) == ("service", "local")

      def test_it_leaves_a_row_already_owned_by_somebody_alone(self):
          bob = make_user()
          make_admin(username="ann")
          theirs = _conversation(owner_kind="user", owner_key=str(bob.pk))
          call_command("adopt_open_rows", "--user", "ann")
          theirs.refresh_from_db()
          assert theirs.owner_key == str(bob.pk)

      def test_it_is_idempotent(self):
          make_admin(username="ann")
          _conversation(owner_kind="open", owner_key="box")
          call_command("adopt_open_rows", "--user", "ann")
          before = AuditEvent.objects.count()
          call_command("adopt_open_rows", "--user", "ann")
          row = AuditEvent.objects.filter(action=actions.ADOPTED).first()
          assert AuditEvent.objects.count() == before + 1
          assert sum(row.detail["counts"].values()) == 0

      def test_it_writes_exactly_one_audit_event_with_the_counts(self):
          """One row, not one per claimed row: the interesting fact is the
          EVENT and the counts, not five thousand line items."""
          make_admin(username="ann")
          for _ in range(3):
              _conversation(owner_kind="open", owner_key="box")
          call_command("adopt_open_rows", "--user", "ann")
          rows = AuditEvent.objects.filter(action=actions.ADOPTED)
          assert rows.count() == 1
          assert rows.first().detail["counts"]["Conversations"] == 3


  class TestDryRun:
      def test_it_prints_the_counts_and_writes_nothing(self, capsys):
          make_admin(username="ann")
          row = _conversation(owner_kind="open", owner_key="box")
          call_command("adopt_open_rows", "--user", "ann", "--dry-run")
          row.refresh_from_db()
          assert row.owner_kind == "open"
          assert AuditEvent.objects.filter(action=actions.ADOPTED).count() == 0
          assert "Conversations" in capsys.readouterr().out


  class TestOrdering:
      def test_it_prints_the_ordering_guidance(self, capsys):
          """Create the superuser, adopt, THEN switch the posture.
          Switching first leaves the new admin looking at their own empty
          box until they adopt -- which is confusing, not dangerous, and
          the command says so rather than letting somebody discover it."""
          make_admin(username="ann")
          call_command("adopt_open_rows", "--user", "ann")
          assert "posture" in capsys.readouterr().out.lower()

      def test_there_is_no_undo_flag(self):
          """Reversibility is the POSTURE SWITCH, not an undo: switching
          back to open makes every visibility function return everything,
          so the reassigned owner stops mattering. Rewriting owners back
          to the open principal would be a second, lossier operation that
          also erased any ownership recorded after adoption."""
          from identity.management.commands import adopt_open_rows
          source = adopt_open_rows.__file__
          assert "--undo" not in open(source).read()
  ```

- [ ] **Step 2: Run to verify it fails.**
  Run: `.venv/bin/pytest -q identity/tests/test_command_adopt_open_rows.py`
  Expected: FAIL — `CommandError: Unknown command: 'adopt_open_rows'`

- [ ] **Step 3: Write `identity/management/commands/adopt_open_rows.py`.**
  ```python
  """An open box's first administrator claims the rows nobody owns.

  A box that ran open for a year has conversations, agents, flows, ask
  records and generated images owned by `("open", "box")` or by nothing at
  all. Switching the posture does not move them -- that is `set_posture`'s
  whole "a switch, not a migration" property -- so this command exists to
  move them, once, deliberately, and with a record.

  REVERSIBILITY IS THE POSTURE SWITCH. There is no `--undo`: switching
  back to `open` makes every visibility function return everything, so the
  reassigned owner stops mattering, and rewriting owners back to the open
  principal would be a second, lossier operation that also erased any
  ownership recorded after adoption.
  """
  from __future__ import annotations

  from django.apps import apps
  from django.core.management.base import BaseCommand, CommandError
  from django.db import transaction
  from django.db.models import Q

  from identity import audit
  from identity.contracts import actions
  from identity.contracts.actions import SOURCE_CLI
  from identity.contracts.ownership import all_owned_rows
  from identity.contracts.principals import SERVICE_PRINCIPAL
  from identity.models import User

  # The two states a row can be in that mean "nobody in particular owns
  # this": what `agents/` stamped on an open box, and what `vision`/`rag`
  # rows written before IA-1 carry, because those columns had no owner
  # columns at all.
  #
  # `("service", "local")` IS DELIBERATELY NOT HERE (spec section 10.4).
  # A row a shell path made is not a row the open box made: sweeping it
  # into a person's name would attribute an automated action to somebody
  # who did not perform it, and the audit trail would then be wrong in a
  # way nothing could detect. Service-owned rows stay service-owned and
  # remain visible to `is_admin`, which is where an operator looks for
  # them anyway.
  _UNOWNED = Q(owner_kind="open", owner_key="box") | Q(owner_kind="", owner_key="")


  class Command(BaseCommand):
      help = "Claim every unowned row for one administrator."

      def add_arguments(self, parser):
          parser.add_argument("--user", required=True,
                              help="The superuser who will own the claimed rows.")
          parser.add_argument("--dry-run", action="store_true",
                              help="Print the per-table counts and write nothing.")

      def handle(self, *args, **options):
          user = self._resolve(options["user"])
          key = str(user.pk)
          dry_run = options["dry_run"]

          counts: dict[str, int] = {}
          with transaction.atomic():
              for spec in all_owned_rows():
                  try:
                      model = apps.get_model(spec.model)
                  except LookupError:
                      continue
                  qs = model.objects.filter(_UNOWNED)
                  if dry_run:
                      counts[spec.label] = qs.count()
                      continue
                  counts[spec.label] = qs.update(owner_kind="user", owner_key=key)

              if dry_run:
                  transaction.set_rollback(True)

          for label, count in counts.items():
              self.stdout.write(f"{label}: {count}")

          if dry_run:
              self.stdout.write(self.style.WARNING("Dry run — nothing was written."))
              return

          # ONE row, not one per claimed row: the interesting fact is the
          # event and the counts, not five thousand line items.
          audit.record(SERVICE_PRINCIPAL, actions.ADOPTED, target_type="user",
                       target_key=user.pk, target_label=user.username, source=SOURCE_CLI,
                       counts=counts, user_id=user.pk)
          self.stdout.write(self.style.SUCCESS(
              f"Claimed {sum(counts.values())} row(s) for {user.username!r}."
          ))
          # The ordering the operator wants, printed where they will read
          # it. Switching the posture FIRST leaves the new admin looking
          # at their own empty box until they adopt -- confusing, not
          # dangerous, and worth one line.
          self.stdout.write(
              "Next: switch the posture with `manage.py identity_posture personal` "
              "(or `enterprise`). Doing that BEFORE adopting is what leaves a new "
              "administrator looking at an empty box."
          )

      def _resolve(self, username: str) -> User:
          user = User.objects.filter(username=username).first()
          if user is None:
              raise CommandError(f"There is no account called {username!r} on this box.")
          if not user.is_active:
              raise CommandError(f"{username!r} is deactivated, so it cannot own rows.")
          if not user.is_superuser:
              raise CommandError(
                  f"{username!r} is not a superuser. Adoption gives one account every "
                  f"row on this box, which is an administrator's decision."
              )
          return user
  ```

- [ ] **Step 4: Write `reassign_owner`** — the same registry, the same transaction, one audit
  event (`identity.owner_reassigned`).
  ```
  manage.py reassign_owner --from <username|open|service> --to <username> [--kind <key>]
  ```
  `--from` accepts a username, the literal `open`, **or** the literal `service` (spec §10.5).
  The third exists because `adopt_open_rows` deliberately skips service-owned rows: an operator
  who genuinely does want a shell-made conversation to belong to a person needs a way to say so
  **explicitly, one instruction at a time**, rather than having it swept up by a bulk command
  that was asked to do something else. `--to` is a username; `--kind` narrows to one registered
  `OwnedRows.key` and raises a `CommandError` naming the valid keys when it does not match.
  ```python
  _FROM_SENTINELS = {
      "open": ("open", "box"),
      # NOT reachable from `adopt_open_rows` -- only from an operator
      # naming it here, deliberately, once (spec section 10.5).
      "service": ("service", "local"),
  }
  ```
  Its tests assert: the narrowing works and touches no other table; an unknown `--kind` names
  the valid keys; `--from service` moves exactly the shell-made rows and nothing else; and the
  audit row records `from`, `to`, `kind` and the counts.

- [ ] **Step 5: Run.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity`
  Expected: PASS

- [ ] **Step 6: Docs.** `docs/OPERATIONS.md` — a new **Turning on accounts** subsection with
  the exact sequence, verbatim, in the order it must be run:
  ```bash
  manage.py createsuperuser
  manage.py adopt_open_rows --user <username> --dry-run
  manage.py adopt_open_rows --user <username>
  manage.py identity_posture personal        # or: enterprise
  ```
  plus the deactivation follow-up (`manage.py reassign_owner --from <leaver> --to <somebody>`).

- [ ] **Step 7: Commit** as
  `feat(identity): T14 — adopt_open_rows and reassign_owner`.

---

### Task 15: the route × principal matrix, and the zero-permission-query pin

The gate that makes §11's table a live artefact rather than a document that rots. **The route
list is derived, not typed**: a route added to any `urls.py` without a classification fails
here immediately, naming itself.

**Files:**
- Create: `identity/tests/test_route_matrix.py`, `identity/tests/test_zero_queries.py`
- Modify: `identity/tests/_helpers.py` — the driver helpers the matrix needs

**Interfaces:**
- Consumes: everything above.
- Produces: no importable interface. It is the phase's proof.

**Steps:**

- [ ] **Step 1: Write `identity/tests/test_route_matrix.py`.** Modelled directly on
  `agents/chat/tests/test_never_500.py`, which already derives its route list from
  `urlpatterns` and fails with a `KeyError` naming any route that has no driver.
  ```python
  """Every route × every principal × every posture × both settings of the
  administrator-content toggle.

  THE ROUTE LIST IS DERIVED, NOT TYPED, exactly as
  `agents/chat/tests/test_never_500.py` derives its own: a route added to
  any `urls.py` without a `ROUTE_RULES` entry fails here immediately,
  naming itself, and a route with no driver raises a `KeyError` naming
  itself. That `KeyError` -- not a silently shorter loop -- is what
  "swept automatically" means.

  KEEPS "vision" IN FARABUNKER_FEATURES: it calls `reverse()` throughout,
  and `config/urls.py` mounts `/vision/` only with that flag.
  """
  from __future__ import annotations

  import itertools

  import itertools
  from collections.abc import Callable
  from dataclasses import dataclass

  import pytest
  from django.conf import settings
  from django.test import Client
  from django.urls import reverse

  from agents.defaults import DEFAULT_AGENTS
  from identity.access import owner_fields
  from identity.contracts.postures import (
      POSTURE_ENTERPRISE, POSTURE_OPEN, POSTURE_PERSONAL,
  )
  from identity.routes import ADMIN, AUTHENTICATED, PUBLIC, ROUTE_RULES, tier_for
  from identity.tests._helpers import (
      make_admin, make_user, posture, sign_in, user_principal,
  )
  # ROW BUILDERS, one per column, all defined in `identity/tests/_helpers.py`
  # -- NOT imported from each column's own test package. The
  # helper-duplication rule polices exactly that: one column's test
  # scaffolding must not become load-bearing for another's, and this
  # module addresses all six mounts. Each builder is four lines and does
  # nothing but `Model.objects.create(...)` through `apps.get_model`, so
  # `identity/tests/_helpers.py` still imports no other column.
  from identity.tests._helpers import (
      make_agent, make_category, make_connection, make_conversation, make_document,
      make_generation, make_job_input, make_output, make_queue_job, make_turn,
  )
  from models.contracts.roles import CHAT_CONVERSE_ROLE, RAG_EMBED_ROLE

  pytestmark = pytest.mark.django_db

  # Fresh usernames, category names and connection names per call, so two
  # matrix cells in one transaction never collide on a CI-unique
  # constraint.
  _counter = itertools.count()

  # The chat sweep's set, plus the two statuses the gate introduces.
  _NEVER_500_STATUSES = frozenset({200, 202, 302, 400, 401, 403, 404, 405, 409, 503})

  # The finer class per route, from `identity/routes.py`'s own comments --
  # duplicated here as DATA because the matrix asserts on it, and a
  # comment is not assertable. `test_the_classes_agree_with_the_tiers`
  # below pins the two against each other so they cannot drift.
  _CLASSES: dict[str, str] = {
      "chat-index": "A", "chat-start": "A", "chat-default-install": "A",
      "chat-conversation": "O", "chat-turn": "O", "chat-conversation-delete": "O",
      "chat-turn-status": "O",
      "rag-ask-page": "A", "rag-ask": "A", "rag-ask-status": "R", "rag-search": "A",
      "rag-documents": "R", "rag-document-upload": "A", "rag-document-file": "L",
      "rag-document-transcript": "L", "rag-document-delete": "R",
      "rag-document-reingest": "R", "rag-category-rename": "S",
      "rag-category-delete": "S", "rag-history": "A", "rag-history-settings": "S",
      "rag-upload-cap-settings": "S", "rag-media-duration-settings": "S",
      "rag-document-pages-settings": "S", "rag-retrieval-top-k-settings": "S",
      "rag-retrieval-score-floor-settings": "S", "rag-hybrid-search-settings": "S",
      "vision-create": "A", "vision-create-operation": "A", "vision-gallery": "A",
      "vision-operations": "A", "vision-generate": "A", "vision-job-status": "O",
      "vision-job-delete": "O", "vision-output-file": "O", "vision-input-file": "O",
      "vision-queue-status": "R",
      "inference-console": "S", "inference-connection-add": "S",
      "inference-connection-remove": "S", "inference-machine-add": "S",
      "inference-role-assign": "S", "inference-role-reencode": "S",
      "inference-server-scan": "S",
      "jobs-queue": "R", "jobs-queue-settings": "S", "jobs-queue-cancel": "R",
      "setup-index": "P",
      "identity-login": "P", "identity-logout": "A", "identity-password-change": "A",
      "identity-password-change-done": "A", "identity-users": "S",
      "identity-user-create": "S", "identity-user-edit": "S", "identity-settings": "S",
  }

  # One driver per route: it seeds whatever rows the route needs, and
  # returns `(method, url, data)`. A route with no driver raises KeyError.
  # ONE DRIVER PER ROUTE. Each is `(world) -> (method, url, data)`, where
  # `world` is the seeded fixture below. A name with no driver raises
  # `KeyError` naming itself -- the same "swept automatically" mechanism
  # `agents/chat/tests/test_never_500.py` already uses, and the reason
  # `test_every_route_has_a_driver` below can be a one-line assertion.
  #
  # EVERY ROW-ADDRESSED ROUTE POINTS AT A ROW OWNED BY `world.other` --
  # a third account that appears in no test as a caller. That is what
  # gives the "an admin with the setting off gets the member's answer"
  # column something to be about: a route pointed at the CALLER's own row
  # would answer 200 for everybody and prove nothing.
  #
  # Bodies are the real field names each view reads, not invented ones:
  # `agents/chat/views/{conversations,turns,defaults}.py`,
  # `tools/rag/views.py` (`_numeric_setting`'s `raw_key` per endpoint),
  # `models/queue/views.py:340-402`, `models/registry/views.py:1312,
  # 2105-2107,2273`. A body that named a field the view does not read
  # would exercise the view's "you sent nothing" branch and the matrix
  # would still pass -- which is a matrix testing the wrong thing.
  _DRIVERS: dict[str, Callable[["World"], tuple[str, str, dict]]] = {
      # --- /chat/ ------------------------------------------------------
      "chat-index": lambda w: ("get", reverse("chat-index"), {}),
      "chat-start": lambda w: ("post", reverse("chat-start"),
                               {"agent": w.agent.slug, "text": "hello"}),
      "chat-default-install": lambda w: ("post", reverse("chat-default-install"),
                                         {"kind": "agent", "slug": w.default_slug}),
      "chat-conversation": lambda w: (
          "get", reverse("chat-conversation", args=[w.conversation.id]), {}),
      "chat-turn": lambda w: (
          "post", reverse("chat-turn", args=[w.conversation.id]), {"text": "hello"}),
      "chat-conversation-delete": lambda w: (
          "post", reverse("chat-conversation-delete", args=[w.conversation.id]), {}),
      "chat-turn-status": lambda w: (
          "get", reverse("chat-turn-status", args=[w.turn.pk]), {}),

      # --- /rag/ -------------------------------------------------------
      "rag-ask-page": lambda w: ("get", reverse("rag-ask-page"), {}),
      "rag-ask": lambda w: ("post", reverse("rag-ask"),
                            {"question": "what does the library say?"}),
      "rag-ask-status": lambda w: (
          "get", reverse("rag-ask-status", args=[w.queue_job.pk]), {}),
      "rag-search": lambda w: ("get", reverse("rag-search") + "?q=anything", {}),
      "rag-documents": lambda w: ("get", reverse("rag-documents"), {}),
      # No file part: the view's own "you selected nothing" branch is a
      # 302 with a message, which is an honest non-500 and keeps this
      # driver from depending on a fixture file on disk.
      "rag-document-upload": lambda w: ("post", reverse("rag-document-upload"),
                                        {"category": ""}),
      "rag-document-file": lambda w: (
          "get", reverse("rag-document-file", args=[w.document.pk]), {}),
      "rag-document-transcript": lambda w: (
          "get", reverse("rag-document-transcript", args=[w.document.pk]), {}),
      "rag-document-delete": lambda w: (
          "post", reverse("rag-document-delete", args=[w.document.pk]), {}),
      "rag-document-reingest": lambda w: (
          "post", reverse("rag-document-reingest", args=[w.document.pk]), {}),
      "rag-category-rename": lambda w: (
          "post", reverse("rag-category-rename", args=[w.category.pk]),
          {"name": "a renamed category"}),
      "rag-category-delete": lambda w: (
          "post", reverse("rag-category-delete", args=[w.category.pk]), {}),
      "rag-history": lambda w: ("get", reverse("rag-history"), {}),
      "rag-history-settings": lambda w: ("post", reverse("rag-history-settings"),
                                         {"history_limit": "50"}),
      "rag-upload-cap-settings": lambda w: ("post", reverse("rag-upload-cap-settings"),
                                            {"max_upload_gb": "2"}),
      "rag-media-duration-settings": lambda w: (
          "post", reverse("rag-media-duration-settings"), {"max_media_minutes": "30"}),
      "rag-document-pages-settings": lambda w: (
          "post", reverse("rag-document-pages-settings"), {"max_document_pages": "100"}),
      "rag-retrieval-top-k-settings": lambda w: (
          "post", reverse("rag-retrieval-top-k-settings"), {"retrieval_top_k": "5"}),
      "rag-retrieval-score-floor-settings": lambda w: (
          "post", reverse("rag-retrieval-score-floor-settings"),
          {"retrieval_score_floor": "0.2"}),
      "rag-hybrid-search-settings": lambda w: (
          "post", reverse("rag-hybrid-search-settings"), {"hybrid_search": "on"}),

      # --- /vision/ (only reachable with the flag on; see the skip) -----
      "vision-create": lambda w: ("get", reverse("vision-create"), {}),
      "vision-create-operation": lambda w: (
          "get", reverse("vision-create-operation", args=["txt2img"]), {}),
      "vision-gallery": lambda w: ("get", reverse("vision-gallery"), {}),
      "vision-operations": lambda w: ("get", reverse("vision-operations"), {}),
      "vision-generate": lambda w: ("post", reverse("vision-generate"),
                                    {"operation": "txt2img", "prompt": "a picture"}),
      "vision-job-status": lambda w: (
          "get", reverse("vision-job-status", args=[w.generation.pk]), {}),
      "vision-job-delete": lambda w: (
          "post", reverse("vision-job-delete", args=[w.generation.pk]), {}),
      "vision-output-file": lambda w: (
          "get", reverse("vision-output-file", args=[w.output.pk]), {}),
      "vision-input-file": lambda w: (
          "get", reverse("vision-input-file", args=[w.job_input.pk]), {}),
      "vision-queue-status": lambda w: (
          "get", reverse("vision-queue-status", args=[w.queue_job.pk]), {}),

      # --- /inference/ -------------------------------------------------
      "inference-console": lambda w: ("get", reverse("inference-console"), {}),
      "inference-connection-add": lambda w: (
          "post", reverse("inference-connection-add"),
          {"name": "a connection", "engine": "ollama",
           "endpoint": "http://localhost:1", "model_id": "an-identifier"}),
      "inference-connection-remove": lambda w: (
          "post", reverse("inference-connection-remove"),
          {"connection_id": str(w.connection.pk), "confirm": "yes"}),
      "inference-machine-add": lambda w: (
          "post", reverse("inference-machine-add"),
          {"engine": "ollama", "endpoint": "http://localhost:1",
           "model_id": "an-identifier"}),
      "inference-role-assign": lambda w: (
          "post", reverse("inference-role-assign"),
          {"role_key": CHAT_CONVERSE_ROLE, "choice": f"conn:{w.connection.pk}",
           "confirm": "yes"}),
      "inference-role-reencode": lambda w: (
          "post", reverse("inference-role-reencode"), {"role_key": RAG_EMBED_ROLE}),
      # The scan forms post BARE -- their parameters arrive on the query
      # string (`models/registry/views.py`'s own docstring says so).
      "inference-server-scan": lambda w: ("post", reverse("inference-server-scan"), {}),

      # --- /queue/ -----------------------------------------------------
      "jobs-queue": lambda w: ("get", reverse("jobs-queue"), {}),
      "jobs-queue-settings": lambda w: (
          "post", reverse("jobs-queue-settings"),
          {"form": "budget", "budget_gb": "8", "max_concurrent_jobs": "1"}),
      "jobs-queue-cancel": lambda w: (
          "post", reverse("jobs-queue-cancel", args=[w.queue_job.pk]), {}),

      # --- /setup/ -----------------------------------------------------
      "setup-index": lambda w: ("get", reverse("setup-index"), {}),

      # --- /identity/ --------------------------------------------------
      "identity-login": lambda w: ("get", reverse("identity-login"), {}),
      "identity-logout": lambda w: ("post", reverse("identity-logout"), {}),
      "identity-password-change": lambda w: (
          "get", reverse("identity-password-change"), {}),
      "identity-password-change-done": lambda w: (
          "get", reverse("identity-password-change-done"), {}),
      "identity-users": lambda w: ("get", reverse("identity-users"), {}),
      "identity-user-create": lambda w: (
          "post", reverse("identity-user-create"),
          {"username": f"created-{next(_counter)}", "password": "a-real-enough-value"}),
      # `demote` on a NON-admin is a no-op the service returns early from,
      # so this driver never trips the last-admin guard and never changes
      # the world between matrix cells.
      "identity-user-edit": lambda w: (
          "post", reverse("identity-user-edit", args=[w.other.pk]), {"action": "demote"}),
      "identity-settings": lambda w: ("get", reverse("identity-settings"), {}),
  }


  @dataclass
  class World:
      """Every row the drivers address, all owned by `other`.

      Rebuilt per matrix cell (the fixture is function-scoped): several
      drivers MUTATE -- `chat-conversation-delete`, `rag-category-delete`,
      `jobs-queue-cancel` -- and a shared world would make the answer to
      cell N depend on which cells ran before it, which is exactly the
      collection-order leakage `docs/DEV.md`'s reversed-order run exists
      to catch.
      """

      other: object
      agent: object
      default_slug: str
      conversation: object
      turn: object
      document: object
      category: object
      queue_job: object
      connection: object
      generation: object = None
      output: object = None
      job_input: object = None


  def _build_world() -> World:
      """Seed one world. `other` is a plain member who owns everything and
      is never the caller.

      A PLAIN FUNCTION with a thin fixture over it, not a fixture alone:
      the two cross-cell classes below (`TestThePosturesAgree`,
      `TestTheContentToggleMovesExactlyTwoClasses`) each need a FRESH
      world inside a loop, and a fixture can only be requested once per
      test."""
      other = make_user(username=f"other-{next(_counter)}")
      principal = user_principal(other)
      agent = make_agent(slug=f"agent-{next(_counter)}", **owner_fields(principal))
      conversation = make_conversation(agent=agent, **owner_fields(principal))
      turn = make_turn(conversation=conversation)
      document = make_document()                # documents carry no owner column
      category = make_category(name=f"category-{next(_counter)}")
      queue_job = make_queue_job(
          payload={"question": "a private question",
                   "actor_kind": "user", "actor_key": str(other.pk)})
      connection = make_connection(name=f"connection-{next(_counter)}")
      world = World(
          other=other, agent=agent, default_slug=DEFAULT_AGENTS[0].slug,
          conversation=conversation, turn=turn, document=document, category=category,
          queue_job=queue_job, connection=connection,
      )
      if "vision" in settings.FARABUNKER_FEATURES:
          world.generation = make_generation(**owner_fields(principal))
          world.output = make_output(job=world.generation)
          world.job_input = make_job_input(job=world.generation)
      return world


  @pytest.fixture
  def world():
      """One world per matrix cell."""
      return _build_world()


  def _assert_never_500(response):
      assert response.status_code in _NEVER_500_STATUSES, (
          response.status_code, response.content[:2000])
      assert "Traceback" not in response.content.decode(errors="replace")
      return response


  class TestTheTableIsComplete:
      def test_every_route_has_a_driver(self):
          missing = sorted(set(ROUTE_RULES) - set(_DRIVERS))
          assert missing == [], missing

      def test_the_classes_agree_with_the_tiers(self):
          """P -> PUBLIC; A/O/L/R -> AUTHENTICATED; S -> ADMIN. The
          middleware enforces the tier and the view owes the class, and
          this is the assertion that keeps the two tables from drifting."""
          for name, klass in _CLASSES.items():
              expected = {"P": PUBLIC, "S": ADMIN}.get(klass, AUTHENTICATED)
              assert ROUTE_RULES[name] == expected, (name, klass)

      def test_every_classified_route_is_classified_in_both_tables(self):
          assert set(_CLASSES) == set(ROUTE_RULES)


  # WHAT EACH CLASS MUST ANSWER, as a table rather than as a chain of
  # ifs -- the design's section 11.1, transcribed. Keys are
  # `(class, who, content_on)`; values are the acceptable statuses for
  # that cell. Several cells accept a SET, and every one of those sets is
  # narrow and deliberate:
  #
  #   * a GET route cannot answer 405 and a POST route cannot answer 200,
  #     so `_expected` intersects the cell with the method's own
  #     vocabulary (`_METHOD_STATUSES`) before asserting;
  #   * a POST that succeeds redirects (302), and one that refuses a
  #     malformed body answers 400 -- both are "you were allowed in", and
  #     the matrix is asserting ADMISSION, not the view's own grammar;
  #   * 503 is "allowed in, but the queue or the engine is not up", which
  #     is an honest answer on this box and never an authorisation fact.
  #
  # What the table is strict about is the REFUSALS, because those are the
  # assertions with security meaning: 302/401 for anonymous, 403 for a
  # member on S, and 404 -- never 403 -- for a row a principal may not
  # see.
  _ADMITTED = frozenset({200, 202, 302, 400, 409, 503})
  _REFUSED_ANON = frozenset({302, 401})
  _REFUSED_ADMIN_SURFACE = frozenset({403})
  _REFUSED_ROW = frozenset({404})

  # THE ONE IA-1-ONLY NARROWING, named rather than folded into the table
  # so IA-2 can find and widen it.
  #
  # `rag-document-delete` and `rag-document-reingest` are class R, and in
  # IA-1 their view rule is `is_admin` ALONE (Task 13 Step 6b) -- document
  # labels are IA-2, so the entitlement-owner half of the spec's rule
  # cannot be written yet. A member therefore gets 403, not the R
  # column's ordinary admission. 403 and not 404 because the member can
  # already see the row's title on the library page: hiding its existence
  # here would be a secrecy the surrounding page does not keep.
  _LIBRARY_MUTATIONS = frozenset({"rag-document-delete", "rag-document-reingest"})

  _METHOD_STATUSES = {"get": frozenset({200, 302, 400, 401, 403, 404, 503}),
                      "post": frozenset({200, 202, 302, 400, 401, 403, 404, 409, 503})}

  _EXPECTED: dict[tuple[str, str, bool], frozenset] = {}
  for _klass in ("P", "A", "O", "L", "R", "S"):
      for _content in (False, True):
          # Anonymous: P is open to everybody; everything else redirects
          # or answers 401 to a poll.
          _EXPECTED[(_klass, "anonymous", _content)] = (
              _ADMITTED if _klass == "P" else _REFUSED_ANON)
          _EXPECTED[(_klass, "member", _content)] = {
              "P": _ADMITTED,
              "A": _ADMITTED,
              # L IS ADMITTED IN IA-1, and that is not a gap -- it is
              # spec section 18.1: entitlements do not exist here, every
              # `DocumentVisibility` is `unlabelled_allowed` for anyone
              # signed in, and `readable_documents` is IA-2. So
              # `rag-document-file` and `-transcript` answer 200 to any
              # signed-in caller. IA-2 turns this cell into
              # `_REFUSED_ROW` for a member holding none of the
              # document's labels; asserting 404 HERE would assert
              # behaviour this phase does not ship, and the matrix would
              # be red against a correct implementation.
              "L": _ADMITTED,
              # R is `is_admin` AT THE QUEUE SEAM: a member is admitted
              # to the listing and 404'd on another's row. Every
              # id-addressed R route in this module points at a job owned
              # by `world.other`, so both answers are reachable within
              # the class and the union is the honest cell. Which one a
              # given route gives depends on whether it is addressed by
              # id, and Task 13's own tests pin each side individually.
              "R": _ADMITTED | _REFUSED_ROW,
              "O": _REFUSED_ROW,
              "S": _REFUSED_ADMIN_SURFACE,
          }[_klass]
          # An administrator: admitted on P, A, L, R and S in BOTH
          # settings -- administering needs rows, and rows are never
          # gated by the content toggle. On O they are answered like a
          # member while the toggle is off, and admitted once it is on.
          # That one line is the whole of the owner's decision; L joins
          # it in IA-2, when a document can carry a label to be refused
          # by.
          _EXPECTED[(_klass, "admin", _content)] = {
              "P": _ADMITTED, "A": _ADMITTED, "L": _ADMITTED,
              "R": _ADMITTED, "S": _ADMITTED,
              "O": _ADMITTED if _content else _REFUSED_ROW,
          }[_klass]


  def _expected_for(name, who, content_on):
      """The cell, with the one IA-1-only narrowing applied."""
      if name in _LIBRARY_MUTATIONS and who == "member":
          return _REFUSED_ADMIN_SURFACE
      return _EXPECTED[(_CLASSES[name], who, content_on)]


  def _sign_in_as(client, who):
      """Returns the caller, or None for anonymous. `member` and `admin`
      are fresh accounts per cell, so nothing one cell does to an account
      can reach the next."""
      if who == "anonymous":
          return None
      user = make_admin() if who == "admin" else make_user()
      sign_in(client, user)
      return user


  # PINNED TO ONE POSTURE, deliberately. Sweeping both would double
  # ~1,000 cells -- each of which seeds a fresh world -- to prove a fact
  # `TestThePosturesAgree` below already asserts directly and far more
  # cheaply: that the two non-open postures answer IDENTICALLY for every
  # route, principal and setting. Proving it a second time by exhaustion,
  # six times per merge gate, buys nothing -- and the posture sweep
  # (`FARABUNKER_TEST_POSTURE=enterprise`) runs this whole module against
  # the other posture anyway.
  _MATRIX_POSTURE = POSTURE_PERSONAL


  @pytest.mark.parametrize("content_on", [False, True])
  @pytest.mark.parametrize("who", ["anonymous", "member", "admin"])
  @pytest.mark.parametrize("name", sorted(ROUTE_RULES))
  class TestTheMatrix:
      def test_the_answer_is_the_one_the_class_requires(
          self, client, world, name, who, content_on,
      ):
          """The whole of the design's section 11.1, as an assertion.

          With the content setting OFF -- the default -- an administrator
          is answered LIKE A MEMBER on every **O** route and LIKE AN
          ADMINISTRATOR on every R and S route: they run the box without
          reading anybody's words. Turning it on collapses the two
          columns, and changes nothing on R or S.

          L is admitted in BOTH settings in IA-1 (spec section 18.1 --
          no entitlements, no labels, so a document is readable by
          everyone signed in); it joins O in IA-2. Run in ONE posture,
          `_MATRIX_POSTURE`, for the reason stated above it.
          """
          if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
              pytest.skip("the vision tree is not mounted without its flag")
          with posture(_MATRIX_POSTURE, admin_sees_content=content_on):
              _sign_in_as(client, who)
              method, url, data = _DRIVERS[name](world)
              response = getattr(client, method)(url, data)
          allowed = _expected_for(name, who, content_on) & _METHOD_STATUSES[method]
          assert response.status_code in allowed, (
              name, who, content_on, response.status_code, response.content[:500],
          )

      def test_it_is_never_a_500_and_never_a_traceback(
          self, client, world, name, who, content_on,
      ):
          """The never-500 obligation, extended to every route,
          principal and content setting in `_MATRIX_POSTURE`. The OTHER
          non-open posture is covered by `TestThePosturesAgree` (which
          compares status codes across both) and by the
          `FARABUNKER_TEST_POSTURE` sweep, which runs this whole module
          against it.

          A separate test from the one above so a class whose expected
          set is later argued about still cannot regress into a 500 while
          that argument is being had."""
          if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
              pytest.skip("the vision tree is not mounted without its flag")
          with posture(_MATRIX_POSTURE, admin_sees_content=content_on):
              _sign_in_as(client, who)
              method, url, data = _DRIVERS[name](world)
              response = getattr(client, method)(url, data)
          _assert_never_500(response)

      def test_a_refusal_never_leaks_the_row_it_refused(
          self, client, world, name, who, content_on,
      ):
          """404, NOT 403, on the row-addressed classes.

          A 403 on a row-addressed URL confirms the row exists, which is
          exactly the enumeration `chat-turn-status`'s sequential integer
          already exposes. A 403 on an ADMIN SURFACE is fine and is used,
          because the existence of a model console is not a secret.

          SCOPED TO O AND L, matching the skip below. **R is excluded on
          purpose**: `rag-document-delete` and `rag-document-reingest`
          are class R and answer a member **403** in IA-1 (Task 13 Step
          6b), because a member can already see the document's title on
          the library page -- hiding its existence at the mutation would
          be a secrecy the surrounding page does not keep. Their sibling
          R routes 404 through the queue seam, and Task 13's own tests
          pin that side."""
          if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
              pytest.skip("the vision tree is not mounted without its flag")
          if _CLASSES[name] not in ("O", "L"):
              pytest.skip("403 is a legitimate answer outside the row-addressed classes")
          with posture(_MATRIX_POSTURE, admin_sees_content=content_on):
              _sign_in_as(client, who)
              method, url, data = _DRIVERS[name](world)
              response = getattr(client, method)(url, data)
          assert response.status_code != 403, (name, who, content_on)


  class TestThePosturesAgree:
      @pytest.mark.parametrize("content_on", [False, True])
      @pytest.mark.parametrize("who", ["member", "admin"])
      def test_the_two_non_open_postures_give_identical_answers(
          self, client, django_user_model, who, content_on,
      ):
          """THE ASSERTION THAT FAILS IF A POSTURE BRANCH IS EVER
          REINTRODUCED into a visibility function. `personal` and
          `enterprise` differ only in WHICH PAGES EXIST; for the same
          principal and the same setting they answer identically
          everywhere.

          The world and the caller are rebuilt per posture, so the two
          runs differ by the posture column and nothing else -- a shared
          world would let the first run's `jobs-queue-cancel` change the
          second run's answer."""
          for name in sorted(ROUTE_RULES):
              if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
                  continue
              answers = []
              for posture_name in (POSTURE_PERSONAL, POSTURE_ENTERPRISE):
                  fresh = Client()
                  with posture(posture_name, admin_sees_content=content_on):
                      _sign_in_as(fresh, who)
                      method, url, data = _DRIVERS[name](_build_world())
                      answers.append(getattr(fresh, method)(url, data).status_code)
              assert answers[0] == answers[1], (name, who, content_on, answers)


  class TestTheContentToggleMovesExactlyOneClassInIA1:
      @pytest.mark.parametrize("name", sorted(ROUTE_RULES))
      def test_turning_it_on_changes_O_and_nothing_else(self, name):
          """The toggle's blast radius, asserted route by route. An
          administrator's answer must change on **O** and be IDENTICAL
          everywhere else -- because the setting governs READING, and
          administering is not reading.

          L IS IN THE "NOTHING ELSE" HALF IN IA-1, and that is spec
          section 18.1 rather than an oversight: with no entitlements and
          no labels, a document is readable by everyone signed in in both
          settings, so the toggle has nothing to move. IA-2 adds L to the
          flip branch in the same change that gives `readable_documents`
          a body, and renames this class then -- this docstring is the
          note that says so."""
          if name.startswith("vision-") and "vision" not in settings.FARABUNKER_FEATURES:
              pytest.skip("the vision tree is not mounted without its flag")
          answers = []
          for content_on in (False, True):
              fresh = Client()
              with posture(POSTURE_ENTERPRISE, admin_sees_content=content_on):
                  _sign_in_as(fresh, "admin")
                  method, url, data = _DRIVERS[name](_build_world())
                  answers.append(getattr(fresh, method)(url, data).status_code)
          if _CLASSES[name] == "O":
              assert answers[0] == 404 and answers[1] != 404, (name, answers)
          else:
              assert answers[0] == answers[1], (name, answers)


  class TestTheAnonymousPostColumnIsNotVacuous:
      # One POST per class that has one, and every one of them a PLAIN
      # Django view: `chat-start` (A), `chat-turn` (O),
      # `rag-document-delete` (R), `rag-category-delete` (S).
      #
      # `rag-ask` is DELIBERATELY NOT HERE. It is a DRF `APIView`, and
      # `APIView.as_view()` wraps its dispatch in `csrf_exempt` -- DRF
      # enforces CSRF inside `SessionAuthentication` instead, and this
      # project configures no authentication classes. A CSRF-403 pin
      # aimed at it would assert nothing and pass forever, which is
      # exactly the vacuity this class exists to prevent. It keeps its
      # place in the 302/401 test below, where the GATE is what answers.
      @pytest.mark.parametrize("name", ["chat-start", "chat-turn", "rag-document-delete",
                                        "rag-category-delete"])
      def test_an_anonymous_post_with_no_csrf_cookie_is_403(self, world, name):
          """Django's test client disables CSRF enforcement by default,
          which would make the anonymous-POST column of the matrix
          vacuous. This runs one POST per class through a client built
          with `enforce_csrf_checks=True`.

          The 403 comes from CSRF, not from the gate, and that is CORRECT
          AND DELIBERATE: `CsrfViewMiddleware` runs before
          `AuthenticationMiddleware`, and therefore before
          `IdentityGateMiddleware`, so an unauthenticated cross-site POST
          is evaluated for FORGERY before it is evaluated for
          AUTHORISATION. Moving the gate ahead of CSRF would be the wrong
          order to fail in."""
          strict = Client(enforce_csrf_checks=True)
          with posture(POSTURE_PERSONAL):
              method, url, data = _DRIVERS[name](world)
              assert method == "post", name
              response = strict.post(url, data)
          assert response.status_code == 403, (name, response.status_code)
          _assert_never_500(response)

      def test_an_anonymous_post_WITH_a_csrf_cookie_reaches_the_gate(self, world):
          """The other half of the same rule, and the half that proves
          the gate is doing anything at all: a POST that satisfies CSRF
          but carries no session gets 302/401 from
          `IdentityGateMiddleware`, not 403 from CSRF."""
          strict = Client(enforce_csrf_checks=True)
          with posture(POSTURE_PERSONAL):
              strict.get(reverse("identity-login"))     # sets the CSRF cookie
              token = strict.cookies["csrftoken"].value
              method, url, data = _DRIVERS["rag-ask"](world)
              response = strict.post(url, {**data, "csrfmiddlewaretoken": token})
          assert response.status_code in (302, 401), response.status_code
  ```

- [ ] **Step 2: Add the ten row builders to `identity/tests/_helpers.py`.** `_build_world`
  needs one per addressable row, and each is four lines resolving its model through
  `django.apps.apps.get_model` so this helper module still imports no other column — the same
  mechanism `identity/contracts/ownership.py` uses, and for the same reason:
  ```python
  def _create(label: str, **fields):
      """`app_label.ModelName` -> a row. Resolved, never imported: this
      module is test scaffolding for a column that may not import
      `agents`, `tools` or `models`, and a test helper that broke that
      rule would make the rule untestable."""
      from django.apps import apps
      return apps.get_model(label).objects.create(**fields)


  def make_agent(**overrides):
      fields = dict(slug=f"agent-{next(_names)}", name="An agent", description="",
                    system_prompt="You are a test agent.", tool_keys=[],
                    resident=False, enabled=True)
      fields.update(overrides)
      return _create("agents.Agent", **fields)


  def make_conversation(**overrides):
      fields = dict(agent=overrides.pop("agent", None) or make_agent())
      fields.update(overrides)
      return _create("agents.Conversation", **fields)


  def make_turn(**overrides):
      conversation = overrides.pop("conversation", None) or make_conversation()
      fields = dict(conversation=conversation, index=0, role="user", text="a question")
      fields.update(overrides)
      return _create("agents.Turn", **fields)


  def make_document(**overrides):
      # `doc_type` is `CharField(choices=DocType.choices)` with NO default
      # (`tools/rag/models.py:74`) -- omit it and the insert raises. It is
      # the SHAPE of the queryable content, and "prose" is the ordinary
      # one.
      fields = dict(title=f"document-{next(_names)}", source_path="/dev/null",
                    file_hash=f"{next(_names):064d}", doc_type="prose",
                    status="ready")
      fields.update(overrides)
      return _create("rag.Document", **fields)


  def make_category(**overrides):
      fields = dict(name=f"category-{next(_names)}")
      fields.update(overrides)
      return _create("rag.Category", **fields)


  def make_ask_record(**overrides):
      fields = dict(question="a question", category="", connection_name="a name",
                    model_id="an identifier", answer="an answer", citations=[])
      fields.update(overrides)
      return _create("rag.AskRecord", **fields)


  def make_queue_job(**overrides):
      # `priority` is `PositiveIntegerField()` with NO default
      # (`models/queue/models.py:89`): it is resolved at enqueue time by
      # the priority chain, so a row built directly must supply one or
      # the insert raises IntegrityError. `state` takes the module-level
      # `QUEUED` constant's value -- this app has no `InferenceJob.State`
      # inner class.
      fields = dict(kind="rag.ask", payload={}, state="queued", priority=100)
      fields.update(overrides)
      return _create("jobs.InferenceJob", **fields)


  def make_connection(**overrides):
      fields = dict(name=f"connection-{next(_names)}", engine="ollama",
                    endpoint="http://localhost:1", model_id="an-identifier",
                    capabilities=["chat"])
      fields.update(overrides)
      return _create("inference.ModelConnection", **fields)


  def make_generation(**overrides):
      fields = dict(operation="txt2img", params={"prompt": "a picture"},
                    engine="comfyui", model_id="an-identifier",
                    endpoint="http://localhost:1", status="succeeded")
      fields.update(overrides)
      return _create("vision.GenerationJob", **fields)


  def make_output(**overrides):
      fields = dict(job=overrides.pop("job", None) or make_generation(),
                    index=0, path="/dev/null", media_type="image/png")
      fields.update(overrides)
      return _create("vision.GeneratedOutput", **fields)


  def make_job_input(**overrides):
      # `param_key`, not `key` (`tools/vision/models.py:397`).
      fields = dict(job=overrides.pop("job", None) or make_generation(),
                    param_key="image", path="/dev/null", media_type="image/png")
      fields.update(overrides)
      return _create("vision.JobInput", **fields)
  ```
  **Before writing these, read each model's real field list** — `agents/models.py`,
  `tools/rag/models.py`, `models/queue/models.py`, `models/registry/models.py`,
  `tools/vision/models.py` — and fix any required field this list still has wrong. Three were
  wrong in the first draft and are corrected above, which is the calibration for how carefully
  to read: `Document.doc_type` has no default, `InferenceJob.priority` has no default (it is
  resolved by the enqueue-time priority chain, so a directly built row must supply one), and
  `JobInput`'s column is `param_key`, not `key`. A builder that omits a `NOT NULL` column fails
  loudly at the first matrix cell naming the column, and one that names a field that does not
  exist fails with `TypeError` — neither is silent. The app **labels** above (`agents`, `rag`,
  `jobs`, `inference`, `vision`) are load-bearing and are the ones each `AppConfig` really
  declares.

- [ ] **Step 2b: Record where the FOURTH principal is covered.** Spec §18.1 done-when 3 says
  the matrix runs "four principals". This module sweeps **three** — anonymous, member, admin —
  and the fourth, the **open principal**, is covered by two dedicated modules instead, because
  it is the one principal for which the interesting assertion is not a status code:
  `identity/tests/test_zero_queries.py` (Step 4) drives a representative GET of every mount as
  the open principal and asserts **no identity table is queried at all**, which is the claim
  that actually matters for it, and `identity/tests/test_middleware.py::TestOpenPosture` (Task
  7) asserts every route answers exactly as it does today. Adding `open` as a fourth `who`
  column here would parametrise ~340 further cells to re-assert `_ADMITTED` everywhere, which
  is what "the box has one principal and it is an administrator" already guarantees by
  construction. Write this reasoning as a comment at the head of
  `test_route_matrix.py`, so a reader checking the done-when against the module finds the
  answer in the module.

  *(The spec's fourth principal is the entitlement owner, not the open principal; in IA-1 there
  are no entitlements to own, so that column is IA-2's — noted here so the two readings of
  "four" do not get conflated. Either way, three columns is the honest count for this phase and
  the two modules above carry the rest.)*

- [ ] **Step 3: Run it and expect it to find things.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity/tests/test_route_matrix.py`
  Expected: FAIL at first, on real gaps in Tasks 7–13 — a view that answers 403 where the class
  says 404, or one that leaks a row to an admin with the setting off. **Fix the view, never the
  matrix.** The matrix is the specification here.

- [ ] **Step 4: Write `identity/tests/test_zero_queries.py`.**
  ```python
  """An open box never runs a permission query.

  A TESTABLE CLAIM, not a promise. In `open` posture no request executes a
  query against `identity_user`. (IA-2 extends the forbidden set with the
  four entitlement/share tables, which do not exist yet.)

  The one primary-key read of `identity_identitysettings` is NOT a
  permission query -- it is the query that answers WHICH POSTURE, and the
  box must ask it before it can skip anything else.
  """
  from __future__ import annotations

  import pytest
  from django.db import connection
  from django.test.utils import CaptureQueriesContext
  from django.urls import reverse

  from identity.contracts.postures import POSTURE_OPEN
  from identity.tests._helpers import posture

  pytestmark = pytest.mark.django_db

  # One representative GET per mount.
  _MOUNTS = ["chat-index", "rag-ask-page", "rag-documents", "jobs-queue",
             "inference-console", "setup-index", "vision-gallery"]

  _FORBIDDEN_TABLES = ("identity_user", "identity_user_groups")


  @pytest.mark.parametrize("name", _MOUNTS)
  def test_no_identity_table_is_queried_on_an_open_box(client, name):
      with posture(POSTURE_OPEN):
          with CaptureQueriesContext(connection) as captured:
              client.get(reverse(name))
      offenders = [q["sql"] for q in captured.captured_queries
                   if any(table in q["sql"] for table in _FORBIDDEN_TABLES)]
      assert offenders == [], offenders


  @pytest.mark.parametrize("name", _MOUNTS)
  def test_the_settings_row_is_read_and_that_is_the_allowed_one(client, name):
      """Anti-vacuous pin: a page that queried NOTHING would pass the test
      above by doing nothing, so this asserts the posture really was
      asked."""
      with posture(POSTURE_OPEN):
          with CaptureQueriesContext(connection) as captured:
              client.get(reverse(name))
      assert any("identity_identitysettings" in q["sql"] for q in captured.captured_queries)
  ```

- [ ] **Step 5: Run both.**
  Run: `FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q identity`
  Expected: PASS

- [ ] **Step 6: Commit** as
  `test(identity): T15 — the route × principal matrix and the zero-permission-query pin`.

---

### Task 16: docs, the guards' final sweep, and the gate ladder

The record, and the proof. No behaviour changes here.

**Files:**
- Create: `identity/README.md`
- Modify: `docs/ARCHITECTURE.md`, `docs/DEV.md`, `docs/OPERATIONS.md`, `README.md`,
  `docs/adr/0013-inference-execution-queue.md`,
  `docs/adr/0015-agent-layer-and-tool-contract.md`,
  `agents/README.md`, `agents/contracts/README.md`, `agents/runtime/README.md`,
  `agents/chat/README.md`, `models/README.md`, `foundation/README.md`,
  `tools/vision/README.md`

**Steps:**

- [ ] **Step 1: Write `identity/README.md`** — the column, its import law, the postures, and
  the access questions. Sections, in the house shape the other column READMEs use:
  1. **What this column is** — the base of the platform: it imports `foundation`, Django and
     its own `contracts`, and **nothing** from `agents/`, `tools/` or `models/`. Its two
     load-bearing consequences: identity cannot answer "which documents" (it answers "which
     principal"), and the owned-rows registry names its models as strings.
  2. **The four seams** every column may import — `identity.contracts.*`, `identity.access`,
     `identity.request`, `identity.audit` — and the five private modules that nothing outside
     may.
  3. **The three postures**, what exists in each, and what the operator sees. The `personal`
     rule that every account is a superuser, and that flipping to `enterprise` demotes nobody.
  4. **Administering is not reading** — the `is_admin` / `sees_all_content` split, the
     `admin_sees_content` default of False, and the rows-versus-content table.
  5. **Turning accounts on** — the four-command sequence, verbatim, matching
     `docs/OPERATIONS.md`.
  5b. **Rows a command made** — a shell path owns what it creates as `("service", "local")`,
     those rows are visible to administrators and to nobody else, `adopt_open_rows` never
     claims them, and `reassign_owner --from service` is the one deliberate way to move them.
     Four sentences; it is the rule an operator is most likely to meet without expecting it.
  6. **What IA-2 adds** — named, so a reader knows what is missing on purpose: entitlements,
     grants, groups, document and tool labels, `Share` and the sharing UI, the retrieval
     visibility argument, and the chunk-metadata cache.

- [ ] **Step 2: Sweep the platform docs.**
  - `docs/ARCHITECTURE.md` — five columns, the Identity row in the core-services table, the
    security-postures-vs-deployment-postures sentence (started in Task 2; finish it here).
  - `docs/DEV.md` §7/§8 — `testpaths` gains `identity`; the reversed-order command gains it;
    the **posture sweep** is documented with its status: `FARABUNKER_TEST_POSTURE=personal|
    enterprise`, **required before merging identity work and before any release**, and *not* a
    per-change gate — the same status the reversed collection order already has. The restart
    rule is extended to `identity/`.
  - `docs/OPERATIONS.md` — the backup-is-a-credential-store paragraph (Task 2), the orphan
    `auth_user` tables (Task 3), the deactivation-kills-sessions mechanism (Task 6), and the
    adoption ordering (Task 14). Check what
    `foundation/ops/tests/test_docs_sync.py` already asserts verbatim before editing any
    sequence it polices.
  - `README.md` — one paragraph: the box runs open by default, with no accounts and no login,
    and can be switched to accounts by an operator.
  - `docs/ROADMAP.md` — the Identity & Auth bullet marked done **in its first half**, naming
    what IA-2 still owes. (The full rewrite is IA-2's; do not claim the whole phase here.)

- [ ] **Step 3: Finish the two ADR amendments.** They were started in Tasks 5, 10 and 11;
  re-read them as one and make sure they read as a single coherent amendment each, not three
  appended notes. ADR 0015's amendment must also mark **G13 item 1 as *partially* closed**:
  real principals, a request→principal point in its own column and the `/inference/` mutation
  surface all land in IA-1, but grants in their own table, the two builder UIs and the
  grantable-mutating-tool gate do **not**, and they stay open in G12 rather than being quietly
  counted as done. **ADR 0016 is written after IA-2, not here.**

- [ ] **Step 4: Run every structural guard together**, and read each anti-vacuous pin's output
  rather than trusting the exit code:
  Run: `.venv/bin/pytest -q foundation/ops/tests -v`
  Expected: PASS, with these named tests present and passing —
  `test_a_principal_is_only_constructed_in_the_files_that_may` (and its
  `_PRINCIPAL_CONSTRUCTORS` is down to two entries),
  `test_the_principal_sweep_reaches_every_column`,
  `test_no_identity_module_imports_another_column`,
  `test_no_column_imports_identitys_private_modules`,
  `test_no_module_outside_audit_touches_auditevent_objects`,
  `test_the_audit_gate_catches_update_and_delete_not_only_create`,
  `test_no_chat_module_queries_the_three_owned_models_directly`,
  `test_other_columns_reach_the_queues_visibility_and_nothing_else_of_models_queue`,
  and the `ACCOUNTS_REQUIRED` grep gate.

- [ ] **Step 4b: Walk spec §18.1's nine done-whens and name where each is met**, in the PR
  body, so a reviewer checking the phase off does not have to reconstruct it. Two need a
  sentence rather than a pointer:
  - **Done-when 3 ("the route matrix passes for all 48 existing routes plus IA-1's seven,
    across four principals").** The matrix covers **56** route names — 48 existing plus IA-1's
    **eight** (`identity-password-change-done` is a route the spec's table omits; Django's
    `PasswordChangeView` needs a success URL and an unclassified name fails closed). It sweeps
    **three** principals; the open principal is covered by `test_zero_queries.py` and
    `test_middleware.py::TestOpenPosture`, and the entitlement owner is IA-2's, since IA-1 has
    no entitlements to own. Say both, rather than letting a count mismatch read as a gap.
  - **Done-when 5 ("the eight structural guards in §4.3 are green").** IA-1 ships **seven** of
    them: the two that grow (`_PRINCIPAL_CONSTRUCTORS`, the chat `.objects` gate) and five new
    ones. The eighth — `test_rag_views_reads_documents_through_the_access_module` — guards
    `tools/rag/access.py::readable_documents`/`listable_documents`, which are IA-2. Name it as
    deferred with its reason.

- [ ] **Step 5: The four-way suite gate.**
  ```bash
  export DATABASE_URL='postgres://farabunker:farabunker@localhost:5433/farabunker_impl'
  FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_FEATURES='vision'       .venv/bin/pytest -q
  .venv/bin/pytest -q
  .venv/bin/pytest -q scripts agents foundation identity models tools
  ```
  Expected: four green runs. Record the collected-test count from the first: it must be
  materially higher than before the plan, which is the only usable gate on the `testpaths` edit.

- [ ] **Step 6: The posture sweep.**
  ```bash
  FARABUNKER_TEST_POSTURE=personal   FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  FARABUNKER_TEST_POSTURE=enterprise FARABUNKER_FEATURES='vision,media' .venv/bin/pytest -q
  ```
  Expected: green. A failure here is a test that assumed the open posture without pinning it —
  fix the test by pinning, never by weakening the assertion.

- [ ] **Step 7: The migration and check gates.**
  ```bash
  .venv/bin/python manage.py makemigrations --check --dry-run     # exit 0
  .venv/bin/python manage.py migrate --plan                        # exactly four new migrations
  .venv/bin/python manage.py check                                 # clean in the open posture
  ```
  And, against a **restored production backup** on the preview stack: `migrate --plan` shows
  exactly `identity.0001_initial`, `identity.0002_repoint_admin_log_fk`,
  `vision.0006_generationjob_owner`, `rag.0014_askrecord_owner` and no others; then
  `adopt_open_rows --dry-run` reports the real row counts and `--user` claims them, idempotently,
  with one audit row.

- [ ] **Step 8: The preview stack proof list, at `:8001`.** Restart the workers first
  (`docker compose -f compose.preview.yaml restart watcher worker`), then drive **in a browser**:
  1. `open` posture: every page is exactly what it is today — no login, no nav change, no 403.
  2. `manage.py createsuperuser`, then `adopt_open_rows --user <name>`, then
     `identity_posture personal`. Log in. The Accounts and Your password nav links appear.
  3. A second account is created on the users page. Its conversation is invisible to the first
     account's session — in the list, and by direct URL, and by polling `chat-turn-status` with
     the other account's turn id (404 in all three).
  4. With `admin_sees_content` **off**: the superuser sees **no** other account's conversation,
     gallery item or Ask history — **and** sees every queue row, cancels a job, reaches the
     model console and the queue settings, and sees every document row.
  5. Turn `admin_sees_content` **on** on the posture page. Reload — no restart, no re-login —
     and item 4's first half flips while its second half does not change at all.
  6. Switch to `enterprise`. Every answer in items 3–5 is identical.
  7. `DEBUG=1` with a non-open posture: `manage.py check` reports `identity.E001`. A default
     `SECRET_KEY` with a non-open posture reports `identity.E002`. The posture page refuses a
     switch away from `open` under each of the three conditions, with the message naming which.
  8. Deactivate the second account. Its still-open browser tab is signed out on its very next
     request.
  9. Switch back to `open`. Everything is visible to everybody again, and the nav is
     byte-identical to item 1.

- [ ] **Step 9: The live deploy note.** Record in the PR body, and in `docs/OPERATIONS.md`:
  - **This deploy runs migrations**, including a `RunPython` with an emptiness precondition on
    `auth_user`/`django_admin_log`. Take a backup first; the precondition refuses rather than
    guesses, and its reversal is restoring that backup.
  - **The live box stays in `open` posture on merge.** Nothing changes for its users until an
    operator deliberately runs the four-command sequence.
  - **First-admin adoption is a separate, deliberate step:**
    `manage.py createsuperuser`, then `manage.py adopt_open_rows --user <name> --dry-run`, read
    the counts, then `--user <name>`, then `manage.py identity_posture personal`.
  - **`SECRET_KEY` and `DEBUG` must be real before the posture is switched** — `set_posture`
    refuses otherwise, at run time as well as at boot.
  - **Restart `watcher` and `worker`** after deploy: `identity/middleware.py`,
    `identity/access.py` and the Task 10–11 runtime changes are code the worker holds in memory.

- [ ] **Step 10: Commit** as
  `docs(identity): T16 — the identity README, the doc sweep, and IA-1's gate ladder`.

---

## Self-Review

Run after the plan is written, before it is executed.

**1. Spec coverage.** Walk §18.1's content list and point at a task for each:
the `identity/` column and the import-law amendment (T1, T4); `Principal` moved (T1);
`identity.User` + the `AUTH_USER_MODEL` swap + the admin-log repoint (T2, T3);
`IdentitySettings`, the three postures, `admin_sees_content` (T2, T6, T9);
`ACCOUNTS_REQUIRED` deleted (T5); `principal_for_request` moved and rewritten (T5);
`access.py` (T4), `audit.py` (T4), `routes.py`/`middleware.py`/`gate.py` (T7),
`services.py`/`checks.py` (T6), `admin.py` (T9); login/logout/change-password (T8);
the users page and the posture page (T9); the superuser model and the last-admin guard (T6);
the acting rule end to end incl. `ToolContext.agent_slug` and the payload actor keys on all
five job kinds and the deletion of `principal_for` (T10, T11); owner columns on `GenerationJob`
and `AskRecord` (T12); the owned-rows registry (T1, T12); `adopt_open_rows` and
`reassign_owner` (T14); the AuditEvent skeleton and the full action catalogue (T1, T2, T4);
the visibility bodies for conversations, agents, flows, vision jobs, queue jobs and ask records
(T13); session expiry, cookies and the three checks (T6, T7); the route matrix (T15); docs
(T16).
**Deliberate departures, each recorded in "Decisions the author made" above:**
`ToolInvocation.agent_slug` → IA-2 (§17's migration table and §18.1 done-when 6 outrank §18.1's
prose); `held_entitlement_ids` / `owned_entitlement_ids` / `may_see_unlabelled` → IA-2 (their
tables do not exist in IA-1).

**2. Placeholder scan.** No "TBD", no "similar to Task N", no "add appropriate error
handling", and **no bare `...` anywhere**. The three sites that carried one in the first draft
are written out in full: T15's `_DRIVERS` is a complete table of all 56 routes with real
methods and real POST field names read off each view; T11's and T13's test bodies are real
assertions matching their docstrings. Verified mechanically:
```bash
grep -n '^\s*\.\.\.\s*$' docs/superpowers/plans/2026-08-29-identity-ia1-accounts-and-login.md
```
returns nothing. Global Constraint 15 makes the rule binding on the implementer too: a step
that cannot be executed as written is reported **BLOCKED**, never degraded into a placeholder.

**3. Type consistency.** Checked across tasks: `principal_for_request` (T5) → used by T7, T9,
T10, T12, T13. `payload_fields` / `principal_from_payload` (T1) → T10, T11, T13.
`owner_fields` (T4) → T12's two create surfaces and T13's `_owned`. `all_owned_rows` (T1) →
T6's `owned_row_counts` and T14's two commands. `ServiceRefused` (T6) → T9's two pages and
`identity/admin.py`, and T6's own command. `ROUTE_RULES` / `tier_for` (T7) → T7's middleware,
T7's own test, and T15's matrix. `sees_all_content` / `is_admin` (T4) → T7, T13's five modules.
`visible_jobs` exists twice, in two columns, with different return types — `tools/vision/
visibility.py::visible_jobs` returns a `GenerationJob` queryset and `models/queue/
visibility.py::visible_jobs` an `InferenceJob` one. That is deliberate (the spec names both) and
no module imports both; T13's docstrings say so.

## Plan review

Adversarial hygiene review (read-only reviewer against the spec and the real tree), 2026-08-29.

- **Round 1 — AMEND** (4 Major / 12 Minor / 11 Nit, all applied). Major: the matrix's L row asserted IA-2 label behaviour (IA-1 admits any signed-in caller per spec §18.1); the R member cell omitted the 404 the queue seam returns; `rag-document-delete`/`-reingest` had no `is_admin` gate (any member could delete a document — Task 13 Step 6b added); queue helpers referenced a non-existent `InferenceJob.State` and omitted the required `priority`. Orchestrator ruling on finding 14: shell paths stamp `("service","local")` on rows as well as payloads; service-owned rows are visible to `is_admin` only; `adopt_open_rows` never claims them; `reassign_owner --from <username|open|service>` (spec §5.3/§7.2/§10.4/§10.5 aligned in 01dc85a). Route tables verified 56/56/56; migration numbers verified against the tree.
- **Round 2 (scoped) — AMEND** (1 blocking Minor, 3 Nit, all applied). The two compiled-SQL pins counted `owner_kind` in the SELECT list (a concrete column on Conversation and on the select_related Agent) — scoped to the WHERE clause. Ruling on residual 3: an administrator may delete a service-owned conversation (read and delete sides agree; `_may_delete`). Reviewer independently verified `Q(a=1) | Q()` on the installed Django and the ×3 `_service_rows` duplication against the column-boundary guards.
- **Round 3 — orchestrator verification**: placeholder grep clean; no model names (engine keys allowed per Global Constraint 1). Plan executable.
